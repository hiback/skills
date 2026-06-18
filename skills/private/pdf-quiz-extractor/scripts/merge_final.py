"""Merge finalized English explanations and translations into questions.json.

This AWS template validates artifact shape before mutating the delivery file and
writes an audit report. Artifact locations and optional backup behavior are CLI
inputs.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

from common import WORKFLOW_FIELDS, allowed_hosts_from_env, atomic_write, fences, load_json, now_iso, now_stamp, url_set, urls, validate_explanation_ready

ALLOWED_SOURCE_HOSTS = allowed_hosts_from_env()


def explanation_text(qid: int, explanations_dir: Path, questions_index: dict[int, dict[str, Any]]) -> str:
    path = explanations_dir / f"{qid}.json"
    if not path.exists():
        raise ValueError(f"missing explanation artifact: {path}")
    artifact = load_json(path)
    explanation = validate_explanation_ready(artifact, allowed_host=ALLOWED_SOURCE_HOSTS, qid=qid)
    if artifact.get("correct_answer") is not None:
        current = questions_index.get(qid, {}).get("correct_answer")
        if artifact.get("correct_answer") != current:
            raise ValueError(f"q{qid}: explanation answer mismatch")
    question = questions_index.get(qid, {})
    if "original_correct_answer" in question:
        if not artifact.get("synthesized_at"):
            raise ValueError(f"q{qid}: patched question missing synthesized explanation")
        if list(artifact.get("model_chosen_answer") or []) != list(question.get("correct_answer") or []):
            raise ValueError(f"q{qid}: synthesized explanation answer mismatch")
    return explanation


def translation_artifact(qid: int, translations_dir: Path) -> dict[str, Any]:
    path = translations_dir / f"{qid}.json"
    if not path.exists():
        raise ValueError(f"missing translation artifact: {path}")
    artifact = load_json(path)
    if int(artifact.get("id", -1)) != qid:
        raise ValueError(f"q{qid}: translation id mismatch: {artifact.get('id')}")
    zh = artifact.get("zh")
    if not isinstance(zh, dict):
        raise ValueError(f"q{qid}: missing zh object")
    return artifact


def validate_comment_alignment(qid: int, en_comments: list[Any], zh_comments: list[Any]) -> list[str]:
    warnings: list[str] = []
    if len(en_comments) != len(zh_comments):
        raise ValueError(f"q{qid}: zh comments length mismatch")
    for index, (en_comment, zh_comment) in enumerate(zip(en_comments, zh_comments)):
        if isinstance(en_comment, dict):
            if not isinstance(zh_comment, dict):
                raise ValueError(f"q{qid}: comment {index} structure changed")
            for key in ("user", "time", "selected", "highly_voted"):
                if key in en_comment and zh_comment.get(key) != en_comment.get(key):
                    raise ValueError(f"q{qid}: comment {index} field changed: {key}")
            if "content" in en_comment and "content" not in zh_comment:
                raise ValueError(f"q{qid}: comment {index} missing content")
            en_content = str(en_comment.get("content", ""))
            zh_content = str(zh_comment.get("content", ""))
        else:
            en_content = str(en_comment)
            zh_content = str(zh_comment)
        if fences(en_content) != fences(zh_content):
            raise ValueError(f"q{qid}: comment {index} code fence mismatch")
        if url_set(en_content) != url_set(zh_content):
            warnings.append(f"q{qid}: comment {index} URL set changed")
    return warnings


def validate_translation(q: dict[str, Any], zh: dict[str, Any], en_explanation: str) -> list[str]:
    qid = int(q["id"])
    en = q.get("en") or {}
    warnings: list[str] = []
    if not str(q.get("domain", "")).strip():
        raise ValueError(f"q{qid}: missing domain")
    if not q.get("services"):
        raise ValueError(f"q{qid}: missing services")
    for field in ("question", "options", "comments", "explanation_md"):
        if field not in zh:
            raise ValueError(f"q{qid}: missing zh.{field}")
    if not str(zh.get("question", "")).strip():
        raise ValueError(f"q{qid}: empty zh.question")
    if not str(zh.get("explanation_md", "")).strip():
        raise ValueError(f"q{qid}: empty zh.explanation_md")
    if set((en.get("options") or {}).keys()) != set((zh.get("options") or {}).keys()):
        raise ValueError(f"q{qid}: zh option keys mismatch")
    en_comments = en.get("comments", [])
    zh_comments = zh.get("comments")
    if not isinstance(en_comments, list):
        raise ValueError(f"q{qid}: en.comments must be an array")
    if not isinstance(zh_comments, list):
        raise ValueError(f"q{qid}: zh.comments must be an array")
    warnings.extend(validate_comment_alignment(qid, en_comments, zh_comments))
    if fences(en.get("question", "")) != fences(zh.get("question", "")):
        raise ValueError(f"q{qid}: question code fence mismatch")
    for letter, en_option in (en.get("options") or {}).items():
        zh_option = str((zh.get("options") or {}).get(letter, ""))
        if not zh_option.strip():
            raise ValueError(f"q{qid}: empty zh option {letter}")
        if fences(en_option) != fences(zh_option):
            raise ValueError(f"q{qid}: option {letter} code fence mismatch")
    if fences(en_explanation) != fences(zh.get("explanation_md", "")):
        raise ValueError(f"q{qid}: explanation code fence mismatch")
    en_explanation_urls = urls(en_explanation)
    zh_explanation_urls = urls(zh.get("explanation_md", ""))
    if set(en_explanation_urls) != set(zh_explanation_urls):
        raise ValueError(f"q{qid}: explanation URL mismatch")
    if en_explanation_urls != zh_explanation_urls:
        warnings.append(f"q{qid}: explanation URL order or duplicate count changed")
    return warnings


def final_question(q: dict[str, Any], en_explanation: str, zh: dict[str, Any]) -> dict[str, Any]:
    updated = {k: v for k, v in q.items() if k not in WORKFLOW_FIELDS}
    en = dict(updated.get("en") or {})
    en.setdefault("comments", [])
    en["explanation_md"] = en_explanation
    updated["en"] = en
    updated["zh"] = zh
    return updated


def main() -> int:
    parser = argparse.ArgumentParser(description="Merge final bilingual delivery JSON with validation.")
    parser.add_argument("--questions", type=Path, default=Path("questions.json"))
    parser.add_argument("--explanations", type=Path, default=Path("explanations"))
    parser.add_argument("--translations", type=Path, default=Path("translations"))
    parser.add_argument("--audit", type=Path, default=Path("final_merge_audit.json"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()

    questions = load_json(args.questions)
    questions_index = {int(q["id"]): q for q in questions}
    errors: list[str] = []
    warnings: list[str] = []
    final_questions: list[dict[str, Any]] = []

    for q in questions:
        qid = int(q["id"])
        try:
            en_explanation = explanation_text(qid, args.explanations, questions_index)
            artifact = translation_artifact(qid, args.translations)
            zh = artifact["zh"]
            warnings.extend(validate_translation(q, zh, en_explanation))
            final_questions.append(final_question(q, en_explanation, zh))
        except Exception as exc:  # noqa: BLE001 - audit all qids before failing
            errors.append(f"q{qid}: {exc}")

    audit = {
        "question_count": len(questions),
        "error_count": len(errors),
        "errors": errors,
        "warning_count": len(warnings),
        "warnings": warnings,
        "dry_run": args.dry_run,
        "merged_at": now_iso(),
    }
    atomic_write(args.audit, audit)
    print(json.dumps(audit, ensure_ascii=False, indent=2))
    if errors:
        return 1
    if args.dry_run:
        return 0
    if not args.no_backup:
        shutil.copy2(args.questions, args.questions.with_name(f"{args.questions.name}.merge-backup-{now_stamp()}"))
    atomic_write(args.questions, final_questions)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
