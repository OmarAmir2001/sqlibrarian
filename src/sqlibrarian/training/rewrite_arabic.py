"""Step 2.2 (6): add Egyptian Arabic and mixed Arabic/English versions.

Takes about a third of your validated pairs and asks the model to rewrite
ONLY the question, the way Egyptian students actually type. The SQL is copied
unchanged, so it is still correct. The rewrites are added as new rows; the
originals stay.

Same free-tier limits as generate_pairs.py: stop with --max-calls, run again
tomorrow, and it skips pairs already rewritten.

Run:
    uv run python -m sqlibrarian.training.rewrite_arabic
"""

import argparse
import json
import random
import time
from pathlib import Path

from sqlibrarian.helpers.config import get_settings
from sqlibrarian.helpers.enums import Lang
from sqlibrarian.helpers.log import configure_logging, get_logger
from sqlibrarian.training.groq_client import CALL_ERRORS, make_client

log = get_logger(__name__)

STYLES = {
    Lang.EG: "Egyptian Arabic, written the way students text each other (عامية). "
    "Keep course names as they are.",
    Lang.MIXED: "a mix of Egyptian Arabic and English, the way CS students really write: "
    "Arabic sentence, English course names and technical words (GPA, credit hours).",
}

PROMPT = """Rewrite each student question in {style}
Keep the exact meaning; do not add or remove details. \
Vary the wording; don't start them all the same way.

Questions:
{items}

Return JSON only: {{"items": [{{"id": "...", "question": "..."}}]}}"""

SAMPLE_SEED = 42  # the same third is chosen every run, so a second day continues the plan


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def main() -> None:
    configure_logging()
    s = get_settings()
    p = argparse.ArgumentParser()
    p.add_argument("--pairs", type=Path, default=s.paths.own_pairs)
    p.add_argument("--out", type=Path, default=s.paths.own_pairs_arabic)
    p.add_argument("--share", type=float, default=0.33)
    p.add_argument("--batch", type=int, default=20)
    p.add_argument("--max-calls", type=int, default=s.groq_max_calls_per_day)
    p.add_argument("--wait", type=float, default=s.groq_wait_seconds)
    p.add_argument("--model", default=s.pair_generator_model)
    args = p.parse_args()

    client = make_client()
    pairs = read_jsonl(args.pairs)
    chosen = random.Random(SAMPLE_SEED).sample(pairs, int(len(pairs) * args.share))
    done = {r["source_id"] for r in read_jsonl(args.out)} if args.out.exists() else set()
    todo = [pair for pair in chosen if pair["id"] not in done]
    log.info("rewrite.start", chosen=len(chosen), already_done=len(done), to_do=len(todo))

    by_id = {pair["id"]: pair for pair in pairs}
    for call in range(args.max_calls):
        batch = todo[call * args.batch : (call + 1) * args.batch]
        if not batch:
            break
        style = Lang.EG if call % 2 == 0 else Lang.MIXED  # half Egyptian, half mixed
        items = "\n".join(
            json.dumps({"id": b["id"], "question": b["question"]}, ensure_ascii=False)
            for b in batch
        )
        prompt = PROMPT.format(style=STYLES[style], items=items)
        try:
            resp = client.chat.completions.create(
                model=args.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.8,
                response_format={"type": "json_object"},
                extra_body={"reasoning_effort": "low"},
            )
            rewrites = json.loads(resp.choices[0].message.content)["items"]
        except CALL_ERRORS as e:
            log.warning("rewrite.call_failed", call=call + 1, error=type(e).__name__)
            time.sleep(args.wait * 2)
            continue

        written = 0
        with args.out.open("a", encoding="utf-8") as f:
            for r in rewrites:
                src = by_id.get(r.get("id"))
                if src is None or not r.get("question"):
                    continue  # the model invented or dropped an id: skip it
                row = {
                    **src,
                    "id": f"{src['id']}-{style}",
                    "source_id": src["id"],
                    "question": r["question"].strip(),
                    "lang": style,
                }
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
                written += 1
        log.info("rewrite.call_done", call=call + 1, style=style, rewritten=written)
        time.sleep(args.wait)


if __name__ == "__main__":
    main()
