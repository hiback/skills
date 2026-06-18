"""Validate final bilingual questions.json delivery.

This is a read-only final gate. Keep AWS exam-specific expectations in CLI flags
or taxonomy config rather than hardcoding certificate-specific constants.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from common import WORKFLOW_FIELDS, allowed_hosts_from_env, host_is_allowed


URL_RE = re.compile(r"https?://[^\s)\]}>\"']+")
FENCE_RE = re.compile(r"```[\s\S]*?```")
INLINE_CODE_RE = re.compile(r"(`+)([^`]*?)(\1)")
CJK = r"\u3400-\u4dbf\u4e00-\u9fff"

def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def fences(value: str) -> list[str]:
    return FENCE_RE.findall(value or "")


def urls(value: str) -> list[str]:
    return [url.rstrip(".,，。;；:") for url in URL_RE.findall(value or "")]


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


def zh_values(question: dict[str, Any]) -> list[str]:
    zh = question.get("zh") or {}
    values = [str(zh.get("question", "")), str(zh.get("explanation_md", ""))]
    values.extend(str(value) for value in (zh.get("options") or {}).values())
    values.extend(str(comment.get("content", "")) for comment in zh.get("comments") or [])
    return values


def validate_comment_alignment(qid: Any, en_comments: list[Any], zh_comments: list[Any]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if len(en_comments) != len(zh_comments):
        errors.append("zh comments length mismatch")
        return errors, warnings
    for index, (en_comment, zh_comment) in enumerate(zip(en_comments, zh_comments)):
        if isinstance(en_comment, dict):
            if not isinstance(zh_comment, dict):
                errors.append(f"comment {index} structure changed")
                continue
            for key in ("user", "time", "selected", "highly_voted"):
                if key in en_comment and zh_comment.get(key) != en_comment.get(key):
                    errors.append(f"comment {index} field changed: {key}")
            if "content" in en_comment and "content" not in zh_comment:
                errors.append(f"comment {index} missing content")
            en_content = str(en_comment.get("content", ""))
            zh_content = str(zh_comment.get("content", ""))
        else:
            en_content = str(en_comment)
            zh_content = str(zh_comment)
        if fences(en_content) != fences(zh_content):
            errors.append(f"comment {index} code fence mismatch")
        if set(urls(en_content)) != set(urls(zh_content)):
            warnings.append(f"q{qid}: comment {index} URL set changed")
    return errors, warnings


def validate_question(
    question: dict[str, Any],
    *,
    bilingual: bool,
    require_domain: bool,
    require_services: bool,
    require_explanation_url: bool,
    allowed_source_hosts: tuple[str, ...],
    forbidden_service_prefixes: tuple[str, ...],
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    qid = question.get("id")
    en = question.get("en") or {}
    zh = question.get("zh") or {}

    workflow = sorted(set(question) & WORKFLOW_FIELDS)
    if workflow:
        errors.append(f"workflow fields remain: {workflow}")
    if not question.get("correct_answer"):
        errors.append("empty correct_answer")
    if set(question.get("correct_answer") or []) - set((en.get("options") or {}).keys()):
        errors.append("correct_answer not in en.options")
    if "original_correct_answer" in question and question["original_correct_answer"] == question.get("correct_answer"):
        errors.append("original_correct_answer equals correct_answer")
    if require_domain and not question.get("domain"):
        errors.append("missing domain")
    services = question.get("services") or []
    if require_services and not services:
        errors.append("missing services")
    if len(services) != len(set(services)):
        errors.append("duplicate services")
    if forbidden_service_prefixes and any(str(s).startswith(forbidden_service_prefixes) for s in services):
        errors.append("forbidden service prefix remains")
    votes = question.get("vote_distribution") or {}
    for key in votes:
        if str(key).casefold() in {"other", "others"} and key != "Other":
            errors.append("vote_distribution uses non-canonical Other key")

    if not str(en.get("question", "")).strip():
        errors.append("empty en.question")
    if not isinstance(en.get("options"), dict) or not en.get("options"):
        errors.append("empty en.options")
    if "comments" not in en:
        errors.append("missing en.comments")
        en_comments: list[Any] = []
    elif not isinstance(en.get("comments"), list):
        errors.append("en.comments must be an array")
        en_comments = []
    else:
        en_comments = en.get("comments") or []
    en_explanation_urls = urls(en.get("explanation_md", ""))
    if require_explanation_url and not en_explanation_urls:
        errors.append("missing inline explanation citation URL")
    bad_en_hosts = [url for url in en_explanation_urls if not host_is_allowed(url, allowed_source_hosts)]
    if bad_en_hosts:
        errors.append(f"non-allowlisted en explanation URL host: {bad_en_hosts}")
    if bilingual:
        for block, label in ((en, "en"), (zh, "zh")):
            if not str(block.get("question", "")).strip():
                errors.append(f"empty {label}.question")
            if not str(block.get("explanation_md", "")).strip():
                errors.append(f"empty {label}.explanation_md")
            if not isinstance(block.get("options"), dict) or not block.get("options"):
                errors.append(f"empty {label}.options")
            if "comments" not in block:
                errors.append(f"missing {label}.comments")
            elif not isinstance(block.get("comments"), list):
                errors.append(f"{label}.comments must be an array")
        if set((en.get("options") or {}).keys()) != set((zh.get("options") or {}).keys()):
            errors.append("zh option key mismatch")
        raw_zh_comments = zh.get("comments")
        zh_comments: list[Any] = raw_zh_comments if isinstance(raw_zh_comments, list) else []
        comment_errors, comment_warnings = validate_comment_alignment(qid, en_comments, zh_comments)
        errors.extend(comment_errors)
        warnings.extend(comment_warnings)
        if fences(en.get("question", "")) != fences(zh.get("question", "")):
            errors.append("question code fence mismatch")
        for letter, en_option in (en.get("options") or {}).items():
            if fences(en_option) != fences((zh.get("options") or {}).get(letter, "")):
                errors.append(f"option {letter} code fence mismatch")
        if fences(en.get("explanation_md", "")) != fences(zh.get("explanation_md", "")):
            errors.append("explanation code fence mismatch")
        zh_explanation_urls = urls(zh.get("explanation_md", ""))
        if set(en_explanation_urls) != set(zh_explanation_urls):
            errors.append("explanation URL mismatch")
        elif en_explanation_urls != zh_explanation_urls:
            warnings.append(f"q{qid}: explanation URL order or duplicate count changed")
        bad_zh_hosts = [url for url in zh_explanation_urls if not host_is_allowed(url, allowed_source_hosts)]
        if bad_zh_hosts:
            errors.append(f"non-allowlisted zh explanation URL host: {bad_zh_hosts}")
        if any(residual_cjk_spaces(value) for value in zh_values(question)):
            errors.append("residual CJK internal spaces")
    return [f"q{qid}: {error}" for error in errors], warnings


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate final questions.json delivery.")
    parser.add_argument("--questions", type=Path, default=Path("questions.json"))
    parser.add_argument("--audit", type=Path, default=Path("final_validation_audit.json"))
    parser.add_argument("--expected-count", type=int)
    parser.add_argument("--contiguous-ids", action="store_true")
    parser.add_argument("--no-bilingual", action="store_true")
    parser.add_argument("--allow-missing-domain", action="store_true")
    parser.add_argument("--allow-empty-services", action="store_true")
    parser.add_argument("--allow-missing-explanation-url", action="store_true")
    parser.add_argument("--forbidden-service-prefix", action="append", default=[])
    args = parser.parse_args()

    questions = load_json(args.questions)
    allowed_source_hosts = tuple(allowed_hosts_from_env())
    errors: list[str] = []
    warnings: list[str] = []
    ids = [question.get("id") for question in questions]
    if args.expected_count is not None and len(questions) != args.expected_count:
        errors.append(f"question count is {len(questions)}, expected {args.expected_count}")
    if args.contiguous_ids and ids != list(range(1, len(questions) + 1)):
        errors.append("ids are not contiguous 1..N")
    for question in questions:
        question_errors, question_warnings = validate_question(
            question,
            bilingual=not args.no_bilingual,
            require_domain=not args.allow_missing_domain,
            require_services=not args.allow_empty_services,
            require_explanation_url=not args.allow_missing_explanation_url,
            allowed_source_hosts=allowed_source_hosts,
            forbidden_service_prefixes=tuple(args.forbidden_service_prefix),
        )
        errors.extend(question_errors)
        warnings.extend(question_warnings)
    audit = {
        "question_count": len(questions),
        "error_count": len(errors),
        "errors": errors,
        "warning_count": len(warnings),
        "warnings": warnings,
        "top_level_keys": sorted({key for question in questions for key in question}),
        "patched_questions": [q.get("id") for q in questions if "original_correct_answer" in q],
        "services_unique_count": len({service for q in questions for service in q.get("services", [])}),
        "allowed_source_hosts": list(allowed_source_hosts),
    }
    args.audit.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(audit, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
