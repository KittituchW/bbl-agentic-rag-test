"""Re-score the answers already recorded in transcripts/*.md and diff the
result against the verdict written at the time.

Useful after editing EXPECTATIONS: it shows which past verdicts the new
checklist would change, so a grader tweak can be separated from a real model
regression without spending a single API call.

The answers are read back out of the transcripts, and run_samples' heavy
imports (main -> embedding index) are stubbed, since only EXPECTATIONS and
evaluate_answer are needed.

Usage:
    python regrade.py                      # all transcripts
    python regrade.py transcripts/x.md     # just one
"""

from __future__ import annotations

import re
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Stub the expensive imports before loading run_samples.
for _name in ("main", "retrieval", "agents", "dotenv"):
    _mod = types.ModuleType(_name)
    if _name == "dotenv":
        _mod.load_dotenv = lambda *a, **k: None
    if _name == "agents":
        _mod.set_tracing_disabled = lambda *a, **k: None
    sys.modules.setdefault(_name, _mod)

sys.path.insert(0, str(ROOT))
import run_samples as rs  # noqa: E402

SECTION = re.compile(r"^## \d+\. (.+)$", re.M)
VERDICT = re.compile(r"^\*\*Evaluation:\*\* (.+)$", re.M)


def parse(path: Path):
    """Yield (label, answer_text, recorded_verdict) for each section."""
    text = path.read_text(encoding="utf-8")
    marks = list(SECTION.finditer(text))
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        body = text[m.start():end]

        verdict = VERDICT.search(body)
        if not verdict:
            continue
        # The answer is everything between the retrieval trace and the verdict.
        block = re.search(r"```.*?```", body, re.S)
        start = block.end() if block else m.end()
        answer = body[start:verdict.start()].strip()
        yield m.group(1).strip(), answer, verdict.group(1).strip()


def main() -> int:
    args = sys.argv[1:]
    paths = [Path(a) for a in args] if args else sorted(ROOT.glob("transcripts/*.md"))

    changed = 0
    for path in paths:
        rows = []
        for label, answer, recorded in parse(path):
            now = rs.format_evaluation(rs.evaluate_answer(label, answer))
            if now != recorded:
                rows.append((label, recorded, now))
        print(f"\n=== {path.name} ===")
        if not rows:
            print("  no change")
        for label, recorded, now in rows:
            changed += 1
            flipped = recorded.split(" —")[0] != now.split(" —")[0]
            print(f"  {'[STATUS FLIP] ' if flipped else ''}{label}")
            print(f"    was: {recorded}")
            print(f"    now: {now}")

    print(f"\n{changed} line(s) changed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
