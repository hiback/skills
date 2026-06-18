"""Patch questions.json with transcribed option-image content.

Reads project-specific patches from image_option_patches.json. Values should be
markdown strings; use fenced code blocks for code/policies/templates.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from common import atomic_write


def main() -> int:
    parser = argparse.ArgumentParser(description="Patch English options from image transcriptions.")
    parser.add_argument("--questions", type=Path, default=Path("questions.json"))
    parser.add_argument("--patches", type=Path, default=Path("image_option_patches.json"))
    args = parser.parse_args()

    data = json.loads(args.questions.read_text(encoding="utf-8"))
    patches = json.loads(args.patches.read_text(encoding="utf-8"))
    by_id = {q["id"]: q for q in data}

    patched_options = 0
    for qid_raw, options in patches.items():
        qid = int(qid_raw)
        if qid not in by_id:
            raise KeyError(f"qid {qid} not in {args.questions}")
        q = by_id[qid]
        valid = set(q["en"]["options"])
        for letter, text in options.items():
            if letter not in valid:
                raise KeyError(f"qid {qid}: option {letter} not present in English options")
            q["en"]["options"][letter] = text
            patched_options += 1

    atomic_write(args.questions, data)
    print(f"Patched {patched_options} option(s) in {args.questions}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
