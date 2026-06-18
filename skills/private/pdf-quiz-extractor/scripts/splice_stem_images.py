"""Splice OCR/transcribed stem image content into English question text."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from common import atomic_write


def splice(text: str, marker: str, snippet_md: str, qid: int) -> str:
    if marker not in text:
        raise RuntimeError(f"qid {qid}: marker {marker!r} not found")
    head, tail = text.split(marker, 1)
    return f"{head}{marker.rstrip()}\n\n{snippet_md}\n\n{tail.lstrip()}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Splice OCR'd stem images into English markdown.")
    parser.add_argument("--questions", type=Path, default=Path("questions.json"))
    parser.add_argument("--patches", type=Path, default=Path("stem_image_patches.json"))
    parser.add_argument("--ocr-dir", type=Path, default=Path("stem_images"))
    args = parser.parse_args()

    data = json.loads(args.questions.read_text(encoding="utf-8"))
    patches = json.loads(args.patches.read_text(encoding="utf-8"))
    by_id = {q["id"]: q for q in data}

    patched = 0
    for item in patches:
        qid = int(item["qid"])
        marker = item["marker"]
        fence_lang = item.get("fence_lang", "text")
        ocr = (args.ocr_dir / item["ocr_file"]).read_text(encoding="utf-8").rstrip()
        snippet_md = f"```{fence_lang}\n{ocr}\n```"

        if qid not in by_id:
            raise KeyError(f"qid {qid} not in {args.questions}")
        q = by_id[qid]
        if snippet_md in q["en"]["question"]:
            print(f"Q{qid}: already spliced, skip")
            continue
        q["en"]["question"] = splice(q["en"]["question"], marker, snippet_md, qid)
        patched += 1
        print(f"Q{qid}: spliced {len(ocr)} chars")

    atomic_write(args.questions, data)
    print(f"Patched {patched} stem(s) in {args.questions}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
