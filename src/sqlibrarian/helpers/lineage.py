"""What produced an artifact: the code commit and a fingerprint of the data.

Every training run tags itself with these (MLflow tags, Hub commit messages), so
"the score changed, what changed?" has an answer.
"""

import hashlib
import json
import subprocess
from collections.abc import Iterable

from sqlibrarian.helpers.config import get_settings


def git_commit() -> str:
    """Short commit of the code being run, "+dirty" if there are uncommitted changes."""
    root = get_settings().project_root
    try:
        sha = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()  # fmt: skip
        dirty = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()  # fmt: skip
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return f"{sha}+dirty" if dirty else sha


def data_fingerprint(rows: Iterable[dict]) -> str:
    """Order-independent short hash of a list of JSON-able rows."""
    lines = sorted(json.dumps(r, sort_keys=True, ensure_ascii=False) for r in rows)
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()[:12]
