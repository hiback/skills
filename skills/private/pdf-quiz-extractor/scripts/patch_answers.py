"""Patch human-approved answer corrections and audit ambiguous questions.

Reads project-specific data from answer_patches.json instead of hardcoding qids.
Humans decide which answers are safe to patch after reviewing explanation,
review, and arbitration artifacts; this script only validates option letters and
preserves provenance.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from common import atomic_write


def main() -> int:
    parser = argparse.ArgumentParser(description="Patch answer corrections from answer_patches.json.")
    parser.add_argument("--questions", type=Path, default=Path("questions.json"))
    parser.add_argument("--patches", type=Path, default=Path("answer_patches.json"))
    parser.add_argument("--audit", type=Path, default=Path("answer_patch_audit.json"))
    args = parser.parse_args()

    data = json.loads(args.questions.read_text(encoding="utf-8"))
    patch_data = json.loads(args.patches.read_text(encoding="utf-8"))
    patches: dict[str, list[str]] = patch_data.get("patches", {})
    ambiguous: dict[str, str] = patch_data.get("ambiguous", {})
    by_id = {q["id"]: q for q in data}
    patched_audit: list[dict[str, object]] = []
    ambiguous_audit: list[dict[str, object]] = []

    for qid_raw, new_answer in patches.items():
        qid = int(qid_raw)
        if qid not in by_id:
            raise KeyError(f"qid {qid} not in {args.questions}")
        q = by_id[qid]
        if "original_correct_answer" in q:
            raise RuntimeError(f"qid {qid} already patched")
        old_answer = q["correct_answer"]
        if old_answer == new_answer:
            raise ValueError(f"qid {qid}: patch is a no-op")
        valid_letters = set(q["en"]["options"])
        if not set(new_answer).issubset(valid_letters):
            raise ValueError(f"qid {qid}: patch contains invalid option letters {new_answer}")

        q["original_correct_answer"] = old_answer
        q["correct_answer"] = new_answer
        patched_audit.append({
            "id": qid,
            "original_correct_answer": old_answer,
            "correct_answer": new_answer,
            "answer_count_changed": len(old_answer) != len(new_answer),
        })
        print(f"Q{qid}: {''.join(old_answer)} -> {''.join(new_answer)}")

    for qid_raw, note in ambiguous.items():
        qid = int(qid_raw)
        if qid not in by_id:
            raise KeyError(f"qid {qid} not in {args.questions}")
        ambiguous_audit.append({"id": qid, "note": note})
        print(f"Q{qid}: marked ambiguous")

    atomic_write(args.questions, data)
    atomic_write(args.audit, {
        "decision_source": "human-reviewed answer_patches.json",
        "patched": patched_audit,
        "ambiguous": ambiguous_audit,
    })
    print(f"Patched {len(patches)} and annotated {len(ambiguous)} question(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
