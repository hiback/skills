"""Extract candidate option images for selected qids.

This is a template. Tune size filters and sorting for the current PDF, then
manually OCR/transcribe the output and patch options with patch_image_options.py.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pdfplumber  # type: ignore[import-not-found]


def parse_ids(raw: str) -> list[int]:
    return [int(x.strip()) for x in raw.split(",") if x.strip()]


def is_candidate_image(img: dict, *, min_width: float, min_height: float, max_x0: float | None) -> bool:
    if max_x0 is not None and img["x0"] > max_x0:
        return False
    return img["width"] >= min_width and img["height"] >= min_height


def find_question_page(pdf, qid: int, template: str) -> int:
    pat = re.compile(template.format(qid=re.escape(str(qid))))
    for pno, page in enumerate(pdf.pages):
        text = page.extract_text() or ""
        if pat.search(text):
            return pno
    raise RuntimeError(f"Q{qid} not found")


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract option image candidates.")
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--ids", required=True, type=parse_ids, help="Comma-separated qids.")
    parser.add_argument("--out", type=Path, default=Path("option_images"))
    parser.add_argument("--question-template", default=r"Question\s*#?\s*{qid}\b")
    parser.add_argument("--min-width", type=float, default=100)
    parser.add_argument("--min-height", type=float, default=35)
    parser.add_argument("--max-x0", type=float, default=None, help="Ignore images with x0 greater than this value.")
    parser.add_argument("--pages", type=int, default=2, help="Number of pages to inspect from question start.")
    parser.add_argument("--per-qid", type=int, default=4, help="Number of images to save per qid.")
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    with pdfplumber.open(args.pdf) as pdf:
        for qid in args.ids:
            pno = find_question_page(pdf, qid, args.question_template)
            candidates = []
            for off in range(args.pages):
                if pno + off >= len(pdf.pages):
                    continue
                page = pdf.pages[pno + off]
                for img in page.images:
                    if is_candidate_image(img, min_width=args.min_width, min_height=args.min_height, max_x0=args.max_x0):
                        candidates.append((pno + off, page, img))

            candidates.sort(key=lambda c: (c[0], -c[2]["y0"]))
            for idx, (cur_pno, page, img) in enumerate(candidates[: args.per_qid], start=1):
                h = page.height
                bbox = (img["x0"], h - img["y1"], img["x1"], h - img["y0"])
                crop = page.crop(bbox).to_image(resolution=200)
                out_path = args.out / f"q{qid}_{idx}.png"
                crop.save(out_path)
            print(f"Q{qid}: saved {min(len(candidates), args.per_qid)} candidate image(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
