"""Clean spurious CJK-internal whitespace in final translated fields.

The cleaner only mutates `zh` fields. It protects fenced code blocks and inline
code, validates that code fences and explanation URLs are preserved, and writes
an audit report. Residual checks scan non-code segments independently.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CJK = r"\u3400-\u4dbf\u4e00-\u9fff"
CJK_RE = re.compile(f"[{CJK}]")
FENCE_RE = re.compile(r"```[\s\S]*?```")
INLINE_CODE_RE = re.compile(r"(`+)([^`]*?)(\1)")
URL_RE = re.compile(r"https?://[^\s)\]}>\"']+")


def now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def atomic_write(path: Path, payload: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def fences(value: str) -> list[str]:
    return FENCE_RE.findall(value or "")


def urls(value: str) -> list[str]:
    return [url.rstrip(".,，。;；:") for url in URL_RE.findall(value or "")]


def clean_plain_segment(value: str) -> str:
    value = value.replace("\u200b", "")
    value = re.sub(rf"(?<=[{CJK}])\s+(?=[{CJK}])", "", value)
    value = re.sub(rf"(?<=[{CJK}])\s+(?=[，。！？；：、）】》」』])", "", value)
    value = re.sub(rf"(?<=[（【《「『])\s+(?=[{CJK}])", "", value)
    value = re.sub(rf"(?<=[，。！？；：、])\s+(?=[{CJK}])", "", value)
    value = re.sub(rf"(?<=[{CJK}])\s+(?=[（【《「『])", "", value)
    return value


def clean_inline_code_protected(value: str) -> str:
    parts: list[str] = []
    last = 0
    for match in INLINE_CODE_RE.finditer(value or ""):
        parts.append(clean_plain_segment(value[last : match.start()]))
        parts.append(match.group(0))
        last = match.end()
    parts.append(clean_plain_segment(value[last:]))
    return "".join(parts)


def clean_text(value: str) -> str:
    parts: list[str] = []
    last = 0
    for match in FENCE_RE.finditer(value or ""):
        parts.append(clean_inline_code_protected(value[last : match.start()]))
        parts.append(match.group(0))
        last = match.end()
    parts.append(clean_inline_code_protected(value[last:]))
    return "".join(parts)


def residual_cjk_spaces(value: str) -> bool:
    last = 0
    for fence in FENCE_RE.finditer(value or ""):
        if residual_cjk_spaces_no_fence(value[last : fence.start()]):
            return True
        last = fence.end()
    return residual_cjk_spaces_no_fence(value[last:])


def residual_cjk_spaces_no_fence(value: str) -> bool:
    last = 0
    for inline in INLINE_CODE_RE.finditer(value or ""):
        if re.search(rf"(?<=[{CJK}])\s+(?=[{CJK}])", value[last : inline.start()]):
            return True
        last = inline.end()
    return bool(re.search(rf"(?<=[{CJK}])\s+(?=[{CJK}])", value[last:]))


def validate_preserved(label: str, before: str, after: str, check_urls: bool = False) -> None:
    if fences(before) != fences(after):
        raise ValueError(f"code fence changed in {label}")
    if check_urls and urls(before) != urls(after):
        raise ValueError(f"URL changed in {label}")


def clean_question(question: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    updated = dict(question)
    zh = dict(updated.get("zh") or {})
    changed_fields: list[str] = []

    def clean_field(path: str, value: str, check_urls: bool = False) -> str:
        cleaned = clean_text(value)
        validate_preserved(f"q{question.get('id')} {path}", value, cleaned, check_urls=check_urls)
        if cleaned != value:
            changed_fields.append(path)
        return cleaned

    zh["question"] = clean_field("zh.question", str(zh.get("question", "")))
    options = dict(zh.get("options") or {})
    for letter in sorted(options):
        options[letter] = clean_field(f"zh.options.{letter}", str(options[letter]))
    zh["options"] = options

    comments = []
    for index, comment in enumerate(zh.get("comments") or []):
        updated_comment = dict(comment)
        updated_comment["content"] = clean_field(
            f"zh.comments.{index}.content", str(updated_comment.get("content", ""))
        )
        comments.append(updated_comment)
    zh["comments"] = comments
    zh["explanation_md"] = clean_field(
        "zh.explanation_md", str(zh.get("explanation_md", "")), check_urls=True
    )
    updated["zh"] = zh
    return updated, changed_fields


def zh_values(question: dict[str, Any]) -> list[str]:
    zh = question.get("zh") or {}
    values = [str(zh.get("question", "")), str(zh.get("explanation_md", ""))]
    values.extend(str(value) for value in (zh.get("options") or {}).values())
    values.extend(str(comment.get("content", "")) for comment in zh.get("comments") or [])
    return values


def main() -> int:
    parser = argparse.ArgumentParser(description="Clean CJK-internal spaces in final questions.json.")
    parser.add_argument("--questions", type=Path, default=Path("questions.json"))
    parser.add_argument("--audit", type=Path, default=Path("cn_space_cleanup_audit.json"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()

    questions = json.loads(args.questions.read_text(encoding="utf-8"))
    cleaned_questions: list[dict[str, Any]] = []
    changed: dict[int, list[str]] = {}
    errors: list[str] = []
    for question in questions:
        try:
            cleaned, fields = clean_question(question)
            cleaned_questions.append(cleaned)
            if fields:
                changed[int(question["id"])] = fields
        except Exception as exc:  # noqa: BLE001 - audit all qids
            errors.append(f"q{question.get('id')}: {exc}")
            cleaned_questions.append(question)

    residual = [
        int(q["id"])
        for q in cleaned_questions
        if any(residual_cjk_spaces(value) for value in zh_values(q))
    ]
    audit = {
        "changed_qids": sorted(changed),
        "changed_fields": changed,
        "error_count": len(errors),
        "errors": errors,
        "residual_cjk_space_qids": residual,
        "dry_run": args.dry_run,
    }
    atomic_write(args.audit, audit)
    print(json.dumps(audit, ensure_ascii=False, indent=2))
    if errors or residual:
        return 1
    if not args.dry_run:
        if not args.no_backup:
            shutil.copy2(args.questions, args.questions.with_name(f"{args.questions.name}.cn-cleanup-backup-{now_stamp()}"))
        atomic_write(args.questions, cleaned_questions)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
