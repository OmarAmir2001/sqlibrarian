"""Step 2.2 (6): add Egyptian Arabic and mixed Arabic/English versions.

Takes about a third of your validated pairs and asks the model to rewrite
ONLY the question, the way Egyptian students actually type. The SQL is copied
unchanged, so it is still correct. The rewrites are added as new rows; the
originals stay.

Same free-tier limits as generate_pairs.py: stop with --max-calls, run again
tomorrow, and it skips pairs already rewritten.

Run:
    uv run python -m sqlibrarian.training.rewrite_arabic --max-calls 38
"""

import argparse
import json
import os
import random
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

STYLES = {
    "eg": "Egyptian Arabic, written the way students text each other (عامية). "
    "Keep course names as they are.",
    "mixed": "a mix of Egyptian Arabic and English, the way CS students really write: "
    "Arabic sentence, English course names and technical words (GPA, credit hours).",
}

PROMPT = """Rewrite each student question in {style}
Keep the exact meaning; do not add or remove details. \
Vary the wording; don't start them all the same way.

Questions:
{items}

Return JSON only: {{"items": [{{"id": "...", "question": "..."}}]}}"""


def main() -> None:
    load_dotenv()
    p = argparse.ArgumentParser()
    p.add_argument("--pairs", type=Path, default=Path("data/training/own_pairs.jsonl"))
    p.add_argument("--out", type=Path, default=Path("data/training/own_pairs_arabic.jsonl"))
    p.add_argument("--share", type=float, default=0.33)
    p.add_argument("--batch", type=int, default=20)
    p.add_argument("--max-calls", type=int, default=38)
    p.add_argument("--wait", type=float, default=45.0)
    p.add_argument("--model", default="openai/gpt-oss-120b")
    args = p.parse_args()

    client = OpenAI(base_url="https://api.groq.com/openai/v1", api_key=os.environ["GROQ_API_KEY"])
    pairs = [
        json.loads(x) for x in args.pairs.read_text(encoding="utf-8").splitlines() if x.strip()
    ]

    # Same random third every run (fixed seed), so a second day continues the same plan.
    random.seed(42)
    chosen = random.sample(pairs, int(len(pairs) * args.share))
    done = set()
    if args.out.exists():
        done = {
            json.loads(x)["source_id"]
            for x in args.out.read_text(encoding="utf-8").splitlines()
            if x.strip()
        }
    todo = [p_ for p_ in chosen if p_["id"] not in done]
    print(f"{len(chosen)} chosen, {len(done)} already rewritten, {len(todo)} to go")

    by_id = {p_["id"]: p_ for p_ in pairs}
    for call in range(args.max_calls):
        batch = todo[call * args.batch : (call + 1) * args.batch]
        if not batch:
            break
        style = "eg" if call % 2 == 0 else "mixed"  # half Egyptian, half mixed
        items = "\n".join(
            json.dumps({"id": b["id"], "question": b["question"]}, ensure_ascii=False)
            for b in batch
        )
        try:
            resp = client.chat.completions.create(
                model=args.model,
                messages=[
                    {"role": "user", "content": PROMPT.format(style=STYLES[style], items=items)}
                ],
                temperature=0.8,
                response_format={"type": "json_object"},
                extra_body={"reasoning_effort": "low"},
            )
            rewrites = json.loads(resp.choices[0].message.content)["items"]
        except Exception as e:
            print(f"call {call}: failed ({type(e).__name__}); continuing")
            time.sleep(args.wait * 2)
            continue

        with args.out.open("a", encoding="utf-8") as f:
            for r in rewrites:
                src = by_id.get(r.get("id"))
                if src is None or not r.get("question"):
                    continue  # the model invented or dropped an id: skip it
                f.write(
                    json.dumps(
                        {
                            **src,
                            "id": f"{src['id']}-{style}",
                            "source_id": src["id"],
                            "question": r["question"].strip(),
                            "lang": style,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        print(f"call {call + 1}: {len(rewrites)} rewritten ({style})")
        time.sleep(args.wait)


if __name__ == "__main__":
    main()
