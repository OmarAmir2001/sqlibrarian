"""Export records_db (fake data only) and upload it to the private HF dataset, so the
GRPO notebook can run the same database inside Kaggle.

    uv run --extra training python db/publish_dump.py publish     # laptop
    python db/publish_dump.py fetch --out records_db.sql           # Kaggle

Uses pg_dump inside the records-db container (no local Postgres tools needed) and
uploads dumps/records_db.sql to the dataset repo. Re-run it after re-seeding.
"""

import argparse
import subprocess
from pathlib import Path
from urllib.parse import urlparse

from sqlibrarian.helpers.config import get_settings
from sqlibrarian.helpers.hub import dataset_repo
from sqlibrarian.helpers.lineage import git_commit
from sqlibrarian.helpers.log import configure_logging, get_logger

log = get_logger(__name__)

DUMP_PATH_IN_REPO = "dumps/records_db.sql"


def publish() -> None:
    s = get_settings()
    url = urlparse(s.records_dsn)
    out = s.paths.runs / "records_db.sql"
    out.parent.mkdir(parents=True, exist_ok=True)
    compose = ["docker", "compose", "--env-file", str(s.project_root / ".env"),
               "-f", str(s.project_root / "infra/docker-compose.yml")]  # fmt: skip
    dump = subprocess.run(
        [*compose, "exec", "-T", "records-db", "pg_dump", "-U", url.username,
         "-d", url.path.lstrip("/"), "--no-owner", "--no-privileges"],
        capture_output=True, check=True,
    )  # fmt: skip
    out.write_bytes(dump.stdout)
    log.info("dump.written", path=str(out), megabytes=round(len(dump.stdout) / 1e6, 2))

    from huggingface_hub import HfApi

    repo = dataset_repo()
    HfApi(token=s.hf_token).upload_file(
        path_or_fileobj=str(out), path_in_repo=DUMP_PATH_IN_REPO, repo_id=repo,
        repo_type="dataset", commit_message=f"records_db dump (fake data), code {git_commit()}",
    )  # fmt: skip
    log.info("dump.uploaded", repo=repo, path=DUMP_PATH_IN_REPO)


def fetch(out: Path) -> None:
    from huggingface_hub import hf_hub_download

    path = hf_hub_download(
        repo_id=dataset_repo(), filename=DUMP_PATH_IN_REPO, repo_type="dataset",
        token=get_settings().hf_token,
    )  # fmt: skip
    out.write_bytes(Path(path).read_bytes())
    log.info("dump.fetched", path=str(out))


def main() -> None:
    configure_logging()
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=["publish", "fetch"])
    p.add_argument("--out", type=Path, default=Path("records_db.sql"), help="fetch target")
    args = p.parse_args()
    publish() if args.command == "publish" else fetch(args.out)


if __name__ == "__main__":
    main()
