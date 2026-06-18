"""Translate finalized English questions and explanations to Chinese.

Run this after answer patching and English synthesis. It writes one artifact per
qid under translations/ and does not mutate questions.json.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import anthropic  # type: ignore[import-not-found]

from common import (
    allowed_hosts_from_env,
    atomic_write,
    fences,
    now_iso,
    parse_ids,
    retry_transient_sync,
    strip_json_wrappers,
    urls,
    validate_explanation_ready,
)


BASE_URL = os.environ.get("TRANSLATION_BASE_URL") or os.environ.get("ANTHROPIC_BASE_URL")
API_KEY = os.environ.get("TRANSLATION_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
MODEL = os.environ.get("TRANSLATION_MODEL")
if not BASE_URL or not API_KEY or not MODEL:
    raise SystemExit(
        "ERROR: set TRANSLATION_BASE_URL, TRANSLATION_API_KEY, and TRANSLATION_MODEL "
        "(or reuse ANTHROPIC_BASE_URL/ANTHROPIC_API_KEY for the first two)."
    )

QUESTIONS_FILE = Path("questions.json")
EXPLANATIONS_DIR = Path("explanations")
TRANSLATIONS_DIR = Path("translations")
FAILURES_FILE = Path("translation_failures.jsonl")
MAX_TOKENS = int(os.environ.get("TRANSLATION_MAX_TOKENS", "8192"))
ALLOWED_SOURCE_HOSTS = allowed_hosts_from_env()

client = anthropic.Anthropic(base_url=BASE_URL, api_key=API_KEY)


def is_complete(qid: int) -> bool:
    path = TRANSLATIONS_DIR / f"{qid}.json"
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    zh = data.get("zh", {})
    return bool(zh.get("question") and zh.get("options") and zh.get("explanation_md"))


def extract_json(text: str) -> dict:
    return json.loads(strip_json_wrappers(text))


def validate_comment_structure(qid: int, en_comments: list, zh_comments: list) -> list[str]:
    warnings: list[str] = []
    if len(en_comments) != len(zh_comments):
        raise ValueError("comments length changed")
    for index, (en_comment, zh_comment) in enumerate(zip(en_comments, zh_comments)):
        if not isinstance(en_comment, dict) or not isinstance(zh_comment, dict):
            continue
        for key in ("user", "time", "selected", "highly_voted"):
            if key in en_comment and zh_comment.get(key) != en_comment.get(key):
                raise ValueError(f"comment {index} field changed: {key}")
        if fences(str(en_comment.get("content", ""))) != fences(str(zh_comment.get("content", ""))):
            raise ValueError(f"comment {index} code fence count changed")
        if set(urls(str(en_comment.get("content", "")))) != set(urls(str(zh_comment.get("content", "")))):
            warnings.append(f"q{qid}: comment {index} URL set changed")
    return warnings


def validate_translation(question: dict, explanation: dict, payload: dict) -> list[str]:
    if payload.get("id") != question["id"]:
        raise ValueError(f"id mismatch: expected {question['id']}, got {payload.get('id')}")
    zh = payload.get("zh")
    if not isinstance(zh, dict):
        raise ValueError("missing zh object")

    en = question["en"]
    if set(en["options"].keys()) != set(zh.get("options", {}).keys()):
        raise ValueError("option key set changed")
    if not zh.get("question") or not zh.get("explanation_md"):
        raise ValueError("translated question or explanation is empty")
    if any(not str(value).strip() for value in zh.get("options", {}).values()):
        raise ValueError("translated option is empty")
    if "comments" not in zh:
        raise ValueError("missing zh.comments")
    if not isinstance(zh.get("comments"), list):
        raise ValueError("zh.comments must be an array")
    warnings = validate_comment_structure(question["id"], en.get("comments", []), zh.get("comments", []))

    field_pairs = [(en["question"], zh["question"]), (explanation["explanation_md_en"], zh["explanation_md"])]
    for letter, en_opt in en["options"].items():
        field_pairs.append((en_opt, zh["options"][letter]))
    for en_text, zh_text in field_pairs:
        if len(fences(en_text)) != len(fences(zh_text)):
            raise ValueError("code fence marker count changed")
        if set(urls(en_text)) != set(urls(zh_text)):
            raise ValueError("URL set changed")
    return warnings


SYSTEM_PROMPT = """\
You translate finalized AWS certification exam study content from English to Simplified Chinese.

Rules:
- Return only valid JSON matching the requested schema.
- Translate only human-facing natural language.
- Preserve option keys exactly.
- Preserve markdown structure, headings, lists, tables, code fences, inline code, links, URLs, commands, JSON/YAML keys, ARNs, IAM actions, service names, product names, placeholders, and variables.
- Short technical labels that mostly consist of service names, product names, integrations, or configuration nouns may remain mostly English; translate surrounding natural-language prose.
- Preserve explanation citation URLs exactly. Preserve community-comment URLs when possible, but do not invent or repair URLs.
- If a field is code or a machine-readable policy/document, copy it unchanged.
- Do not add preambles or commentary.
"""


def build_user_payload(question: dict, explanation: dict) -> dict[str, Any]:
    en = question["en"]
    return {
        "id": question["id"],
        "correct_answer": question["correct_answer"],
        "en": {
            "question": en["question"],
            "options": en["options"],
            "comments": en.get("comments", []),
            "explanation_md": explanation["explanation_md_en"],
        },
        "schema": {
            "id": "same number",
            "zh": {
                "question": "string",
                "options": "object with identical option keys",
                "comments": "translated comments array if present, otherwise []",
                "explanation_md": "string",
            },
        },
    }


def call_translation(question: dict, explanation: dict) -> dict:
    payload = build_user_payload(question, explanation)
    messages = [{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
    data: dict[str, Any] | None = None
    warnings: list[str] = []
    for attempt in range(3):
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=messages,
        )
        text = "".join(getattr(b, "text", "") for b in response.content if getattr(b, "type", "") == "text")
        try:
            data = extract_json(text)
            warnings = validate_translation(question, explanation, data)
            break
        except Exception as exc:  # noqa: BLE001 - validation feedback for same qid
            if attempt == 2:
                raise
            messages.append({"role": "assistant", "content": text[:4000]})
            messages.append({
                "role": "user",
                "content": f"Previous JSON failed validation: {exc}. Return corrected JSON only. Preserve all option keys, code fences, comment structure, and explanation URLs.",
            })
    assert data is not None
    data["translated_at"] = now_iso()
    data["translation_model"] = MODEL
    if warnings:
        data["translation_warnings"] = warnings
    return data


def retry_translation(question: dict, explanation: dict) -> dict:
    return retry_transient_sync(lambda: call_translation(question, explanation), label=f"q{question['id']} translation")


def log_failure(qid: int, exc: Exception) -> None:
    record = {"ts": now_iso(), "qid": qid, "error": str(exc)}
    with FAILURES_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def validate_synthesis_ready(question: dict, explanation: dict) -> None:
    if "original_correct_answer" not in question:
        return
    if not explanation.get("synthesized_at"):
        raise ValueError("patched question is missing synthesized_at in explanation artifact")
    if list(explanation.get("model_chosen_answer") or []) != list(question.get("correct_answer") or []):
        raise ValueError("patched question explanation model_chosen_answer does not match current correct_answer")


def main() -> int:
    parser = argparse.ArgumentParser(description="Translate finalized English artifacts to Chinese.")
    parser.add_argument("--ids", type=parse_ids, default=None)
    parser.add_argument("--start", type=int, default=None)
    args = parser.parse_args()

    questions = json.loads(QUESTIONS_FILE.read_text(encoding="utf-8"))
    if args.ids:
        wanted = set(args.ids)
        questions = [q for q in questions if q["id"] in wanted]
        for q in questions:
            (TRANSLATIONS_DIR / f"{q['id']}.json").unlink(missing_ok=True)
    elif args.start is not None:
        questions = [q for q in questions if q["id"] >= args.start]

    TRANSLATIONS_DIR.mkdir(exist_ok=True)
    failed: list[int] = []

    for question in questions:
        qid = question["id"]
        if is_complete(qid):
            continue
        e_path = EXPLANATIONS_DIR / f"{qid}.json"
        if not e_path.exists():
            print(f"Q{qid}: missing {e_path}")
            failed.append(qid)
            continue
        explanation = json.loads(e_path.read_text(encoding="utf-8"))
        try:
            validate_explanation_ready(explanation, allowed_host=ALLOWED_SOURCE_HOSTS, qid=qid)
            validate_synthesis_ready(question, explanation)
            translated = retry_translation(question, explanation)
            atomic_write(TRANSLATIONS_DIR / f"{qid}.json", translated)
            print(f"Q{qid}: translated")
        except Exception as exc:  # noqa: BLE001
            log_failure(qid, exc)
            failed.append(qid)
            print(f"Q{qid}: failed")

    if failed:
        print(f"Failed qids: {failed}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
