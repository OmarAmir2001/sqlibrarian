"""Step 8: turn a merged model into a small GGUF file for llama.cpp (CPU serving, CI, demo).

    python -m sqlibrarian.training.export --model runs/sft-run1/merged --run-name sft-run1

Steps: get llama.cpp (clone once), convert the merged model to a float16 GGUF, quantize
it (Q4_K_M by default, about 1 GB for 1.5B), and push the .gguf with the schema and
lineage files to a private Hub repo <prefix>-<run name>-gguf.
Runs on Kaggle after training (needs git, cmake and a C++ compiler; Kaggle has them).
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from sqlibrarian.helpers.config import get_settings
from sqlibrarian.helpers.hub import model_repo
from sqlibrarian.helpers.lineage import git_commit
from sqlibrarian.helpers.log import configure_logging, get_logger

log = get_logger(__name__)

LLAMA_CPP_URL = "https://github.com/ggml-org/llama.cpp"
BUNDLE_FILES = ("schema.txt", "sqlibrarian.json")  # written next to the weights by sft.py


def sh(*cmd: str, cwd: Path | None = None) -> None:
    log.info("export.run", cmd=" ".join(cmd)[:200])
    subprocess.run(cmd, cwd=cwd, check=True)


def ensure_llama_cpp(folder: Path) -> Path:
    """Clone llama.cpp and build only the quantize tool. Returns the quantize binary."""
    if not (folder / "convert_hf_to_gguf.py").exists():
        sh("git", "clone", "-q", "--depth", "1", LLAMA_CPP_URL, str(folder))
    # The converter needs llama.cpp's own gguf package and sentencepiece, nothing else.
    sh(sys.executable, "-m", "pip", "install", "-q", str(folder / "gguf-py"), "sentencepiece")
    binary = folder / "build" / "bin" / "llama-quantize"
    if not binary.exists():
        sh("cmake", "-B", "build", "-DLLAMA_CURL=OFF", cwd=folder)
        sh("cmake", "--build", "build", "--target", "llama-quantize", "-j", cwd=folder)
    return binary


def main() -> None:
    configure_logging()
    s = get_settings()
    p = argparse.ArgumentParser()
    p.add_argument("--model", type=Path, required=True, help="folder of the merged model")
    p.add_argument("--run-name", required=True)
    p.add_argument("--quant", default="Q4_K_M")
    p.add_argument("--llama-cpp", type=Path, default=s.paths.runs / "llama.cpp")
    p.add_argument("--no-push", action="store_true")
    args = p.parse_args()

    quantize = ensure_llama_cpp(args.llama_cpp)
    out = args.model.parent / "gguf"
    out.mkdir(parents=True, exist_ok=True)
    f16 = out / f"{args.run_name}-f16.gguf"
    final = out / f"{args.run_name}-{args.quant.lower()}.gguf"

    sh(sys.executable, str(args.llama_cpp / "convert_hf_to_gguf.py"), str(args.model),
       "--outtype", "f16", "--outfile", str(f16))  # fmt: skip
    sh(str(quantize), str(f16), str(final), args.quant)
    f16.unlink()  # only the quantized file is kept and shipped
    for name in BUNDLE_FILES:
        if (args.model / name).exists():
            shutil.copy(args.model / name, out / name)
    size_gb = round(final.stat().st_size / 1e9, 2)
    log.info("export.done", gguf=str(final), size_gb=size_gb, quant=args.quant)

    if not args.no_push:
        from huggingface_hub import HfApi

        repo = model_repo(f"{args.run_name}-gguf")
        api = HfApi(token=s.hf_token)
        api.create_repo(repo, private=True, exist_ok=True)
        api.upload_folder(
            folder_path=str(out), repo_id=repo,
            commit_message=f"{args.run_name} {args.quant} ({size_gb} GB), code {git_commit()}",
        )  # fmt: skip
        log.info("export.pushed", repo=repo)


if __name__ == "__main__":
    main()
