"""Audit PDF image objects before freezing the English source.

This script does not mutate questions.json. It records page-level image object
counts and approximate question mappings so image/code/table rescue work has a
standard audit artifact even when text heuristics find no candidates.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import pdfplumber  # type: ignore[import-not-found]

from common import atomic_write


def load_question_ids(path: Path) -> set[int]:
    if not path.exists():
        return set()
    data = json.loads(path.read_text(encoding="utf-8"))
    return {int(q["id"]) for q in data}


def qids_on_page(text: str, header_re: re.Pattern[str]) -> list[int]:
    qids: list[int] = []
    for match in header_re.finditer(text or ""):
        try:
            qids.append(int(match.group(1)))
        except (IndexError, ValueError):
            continue
    return qids


def image_record(index: int, image: dict[str, Any], *, min_width: float, min_height: float, max_x0: float | None) -> dict[str, Any]:
    candidate = image.get("width", 0) >= min_width and image.get("height", 0) >= min_height
    if max_x0 is not None and image.get("x0", 0) > max_x0:
        candidate = False
    return {
        "index": index,
        "x0": image.get("x0"),
        "x1": image.get("x1"),
        "y0": image.get("y0"),
        "y1": image.get("y1"),
        "width": image.get("width"),
        "height": image.get("height"),
        "candidate": candidate,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit PDF image objects and approximate qid mappings.")
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--questions", type=Path, default=Path("questions.json"))
    parser.add_argument("--out", type=Path, default=Path("image_object_audit.json"))
    parser.add_argument("--header-regex", default=r"Question\s*#?\s*(\d+)", help="Regex with qid as capture group 1.")
    parser.add_argument("--min-width", type=float, default=100)
    parser.add_argument("--min-height", type=float, default=35)
    parser.add_argument("--max-x0", type=float, default=None, help="Ignore images with x0 greater than this value for candidate flags.")
    args = parser.parse_args()

    header_re = re.compile(args.header_regex, re.I)
    expected_qids = load_question_ids(args.questions)
    pages: list[dict[str, Any]] = []
    qid_image_counts: dict[int, int] = {}
    candidate_qids: set[int] = set()
    matched_qids: set[int] = set()
    current_qid: int | None = None
    total_images = 0

    with pdfplumber.open(args.pdf) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            page_qids = qids_on_page(text, header_re)
            if page_qids:
                matched_qids.update(page_qids)
                current_qid = page_qids[0]
            assigned_qid = current_qid
            images = [
                image_record(index, image, min_width=args.min_width, min_height=args.min_height, max_x0=args.max_x0)
                for index, image in enumerate(page.images, start=1)
            ]
            total_images += len(images)
            candidate_count = sum(1 for image in images if image["candidate"])
            if assigned_qid is not None:
                qid_image_counts[assigned_qid] = qid_image_counts.get(assigned_qid, 0) + len(images)
                if candidate_count:
                    candidate_qids.add(assigned_qid)
            pages.append({
                "page": page_number,
                "qids_on_page": page_qids,
                "assigned_qid": assigned_qid,
                "image_count": len(images),
                "candidate_count": candidate_count,
                "images": images,
            })
            if page_qids:
                current_qid = page_qids[-1]

    audit = {
        "pdf": str(args.pdf),
        "page_count": len(pages),
        "total_images": total_images,
        "candidate_qids": sorted(candidate_qids),
        "qid_image_counts": {str(qid): count for qid, count in sorted(qid_image_counts.items())},
        "questions_without_page_match": sorted(expected_qids - matched_qids),
        "filters": {"min_width": args.min_width, "min_height": args.min_height, "max_x0": args.max_x0},
        "pages": pages,
    }
    atomic_write(args.out, audit)
    print(f"Wrote image object audit -> {args.out}")
    print(f"PDF pages={len(pages)} images={total_images} candidate_qids={len(candidate_qids)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
