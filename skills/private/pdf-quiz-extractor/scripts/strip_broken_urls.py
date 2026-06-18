"""Strip broken URL references from English explanation artifacts.

Run before translate_final.py so Chinese translations inherit cleaned links.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from common import atomic_write, urls


def strip_url(md: str, broken_url: str) -> str:
    pattern = r"\[([^\]]*)\]\(" + re.escape(broken_url) + r"\)"
    return re.sub(pattern, r"\1", md)


def main() -> int:
    parser = argparse.ArgumentParser(description="Strip broken links from explanations/*.json.")
    parser.add_argument("--explanations", type=Path, default=Path("explanations"))
    args = parser.parse_args()

    affected = 0
    total_urls = 0
    for path in sorted(args.explanations.glob("*.json"), key=lambda p: int(p.stem) if p.stem.isdigit() else 0):
        data = json.loads(path.read_text(encoding="utf-8"))
        broken = data.get("broken_sources", [])
        if not broken:
            continue

        en = data.get("explanation_md_en", "")
        for url in broken:
            en = strip_url(en, url)
        data["explanation_md_en"] = en
        data["sources"] = [s for s in data.get("sources", []) if s not in set(broken)]
        data["broken_sources"] = []
        reasons = [r for r in data.get("needs_review_reasons", []) if r != "broken_url_in_sources"]
        if not data["sources"] or not urls(en):
            reasons.append("missing_inline_citation_after_strip")
        data["needs_review_reasons"] = reasons
        if not reasons:
            data["needs_review"] = False
        else:
            data["needs_review"] = True

        atomic_write(path, data)
        affected += 1
        total_urls += len(broken)
        print(f"Q{data['id']}: stripped {len(broken)} URL(s)")

    print(f"Stripped {total_urls} URL(s) across {affected} question(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
