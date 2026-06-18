"""Classify domain and AWS service/topic metadata for finalized English questions.

Domains, label taxonomy, and service normalization live in JSON config files.
The script writes one resumable artifact per qid under metadata/ and can merge
stable delivery fields back into questions.json after audit.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from common import (
    atomic_write,
    is_transient_error,
    load_json,
    now_iso,
    parse_ids_set,
    strip_json_wrappers,
    transient_sleep_seconds,
)

DEFAULT_CONFIG = {
    "certificate_name": "AWS certification exam",
    "vendor_name": "AWS",
    "domains": [],
    "instructions": "Classify the question using the finalized English question and options.",
    "label_style": (
        "Use the shortest common unambiguous service/product/topic label. "
        "Prefer common short names over long formal names. In a vendor-scoped "
        "question bank, omit the vendor prefix unless it is part of the common "
        "name or needed for disambiguation."
    ),
}

DEFAULT_TAXONOMY = {
    "service_aliases": {},
    "parent_services": {},
    "drop_labels": [],
    "strip_prefixes": [],
}


class RelayResponseError(RuntimeError):
    def __init__(self, body: Any):
        self.body = json.dumps(body, ensure_ascii=False) if not isinstance(body, str) else body
        super().__init__(self.body)


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return dict(DEFAULT_CONFIG)
    config = dict(DEFAULT_CONFIG)
    config.update(load_json(path))
    return config


def load_taxonomy(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return dict(DEFAULT_TAXONOMY)
    taxonomy = dict(DEFAULT_TAXONOMY)
    taxonomy.update(load_json(path))
    return taxonomy


def compact(label: str) -> str:
    return re.sub(r"\s+", " ", str(label or "").strip())


def key(label: str) -> str:
    return compact(label).casefold()


def mapping(raw: dict[str, Any]) -> dict[str, str]:
    return {key(k): compact(v) for k, v in raw.items() if compact(v)}


def normalize_services(services: list[Any], taxonomy: dict[str, Any]) -> list[str]:
    aliases = mapping(taxonomy.get("service_aliases", {}))
    parents = mapping(taxonomy.get("parent_services", {}))
    drop = {key(value) for value in taxonomy.get("drop_labels", [])}
    prefixes = [str(value) for value in taxonomy.get("strip_prefixes", [])]
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in services or []:
        value = compact(str(raw))
        for prefix in prefixes:
            if value.casefold().startswith(prefix.casefold()):
                value = compact(value[len(prefix) :])
                break
        value = aliases.get(key(value), value)
        value = parents.get(key(value), value)
        if not value or key(value) in drop or key(value) in seen:
            continue
        seen.add(key(value))
        normalized.append(value)
    return normalized


def select_questions(questions: list[dict[str, Any]], ids: str | None, limit: int | None) -> list[dict[str, Any]]:
    wanted = parse_ids_set(ids)
    selected = [q for q in questions if wanted is None or int(q["id"]) in wanted]
    if limit is not None:
        selected = selected[:limit]
    return selected


def compact_question(question: dict[str, Any]) -> dict[str, Any]:
    en = question.get("en") or {}
    return {
        "id": question.get("id"),
        "question": en.get("question", ""),
        "options": en.get("options", {}),
        "correct_answer": question.get("correct_answer"),
    }


def build_messages(question: dict[str, Any], config: dict[str, Any], taxonomy: dict[str, Any]) -> list[dict[str, str]]:
    domains = config.get("domains") or []
    domain_rule = (
        "Choose exactly one domain from this list: " + ", ".join(domains)
        if domains
        else "Choose a concise English domain/topic label."
    )
    aliases = sorted((taxonomy.get("service_aliases") or {}).values())
    parents = sorted((taxonomy.get("parent_services") or {}).values())
    known_labels = sorted({str(v) for v in aliases + parents if str(v).strip()})
    label_rule = (
        "Prefer these existing project labels when applicable: " + ", ".join(known_labels[:200])
        if known_labels
        else "Use concise English service/product/topic labels."
    )
    system = f"""You classify metadata for a {config.get('vendor_name')} {config.get('certificate_name')} question bank.

Rules:
- {domain_rule}
- {label_rule}
- {config.get('label_style')}
- Return only JSON.
- Use English labels.
- Base the classification on the question stem and options; comments are weak signals only.
"""
    user = {
        "instructions": config.get("instructions"),
        "schema": {
            "id": "same id as input",
            "domain": "string",
            "services": ["string"],
            "confidence": "high|medium|low",
            "evidence": ["short text snippets"],
        },
        "question": compact_question(question),
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
    ]


def request_chat(base_url: str, api_key: str, model: str, messages: list[dict[str, str]], timeout: int, max_tokens: int) -> str:
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0,
        "max_tokens": max_tokens,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
    if isinstance(data, dict) and data.get("error"):
        raise RelayResponseError(data["error"])
    return data["choices"][0]["message"]["content"]


def validate_artifact(raw: dict[str, Any], question: dict[str, Any], config: dict[str, Any], taxonomy: dict[str, Any], model: str) -> dict[str, Any]:
    qid = int(question["id"])
    domain = str(raw.get("domain") or "").strip()
    domains = config.get("domains") or []
    if not domain:
        raise ValueError("empty domain")
    if domains and domain not in domains:
        raise ValueError(f"domain not in configured list: {domain}")
    services = normalize_services(list(raw.get("services") or []), taxonomy)
    if not services:
        raise ValueError("empty services")
    evidence = raw.get("evidence") or []
    if not isinstance(evidence, list):
        evidence = [str(evidence)]
    return {
        "id": qid,
        "domain": domain,
        "services": services,
        "confidence": str(raw.get("confidence") or "medium"),
        "evidence": [str(item) for item in evidence],
        "metadata_model": model,
        "metadata_generated_at": now_iso(),
    }


def classify_one(
    question: dict[str, Any],
    config: dict[str, Any],
    taxonomy: dict[str, Any],
    *,
    base_url: str,
    api_key: str,
    model: str,
    timeout: int,
    max_tokens: int,
    transient_retries: int,
    transient_base_sleep: int,
    transient_max_sleep: int,
) -> dict[str, Any]:
    messages = build_messages(question, config, taxonomy)
    attempt = 0
    while True:
        attempt += 1
        try:
            content = request_chat(base_url, api_key, model, messages, timeout, max_tokens)
            raw = json.loads(strip_json_wrappers(content))
            return validate_artifact(raw, question, config, taxonomy, model)
        except Exception as exc:  # noqa: BLE001
            if is_transient_error(exc) and (transient_retries == 0 or attempt <= transient_retries):
                sleep_for = transient_sleep_seconds(attempt, base=transient_base_sleep, cap=transient_max_sleep)
                print(f"q{question['id']}: transient metadata error, retrying in {sleep_for}s: {exc}", flush=True)
                time.sleep(sleep_for)
                continue
            raise


def artifact_path(metadata_dir: Path, qid: int) -> Path:
    return metadata_dir / f"{qid}.json"


def classify_questions(args: argparse.Namespace, questions: list[dict[str, Any]], config: dict[str, Any], taxonomy: dict[str, Any]) -> int:
    selected = select_questions(questions, args.ids, args.limit)
    args.metadata_dir.mkdir(exist_ok=True)
    failures: list[str] = []
    for question in selected:
        qid = int(question["id"])
        path = artifact_path(args.metadata_dir, qid)
        if path.exists() and not args.force:
            continue
        try:
            artifact = classify_one(
                question,
                config,
                taxonomy,
                base_url=args.base_url,
                api_key=args.api_key,
                model=args.model,
                timeout=args.timeout,
                max_tokens=args.max_tokens,
                transient_retries=args.transient_retries,
                transient_base_sleep=args.transient_base_sleep,
                transient_max_sleep=args.transient_max_sleep,
            )
            atomic_write(path, artifact)
            print(f"q{qid}: wrote {path}", flush=True)
        except Exception as exc:  # noqa: BLE001
            message = f"q{qid}: {exc}"
            failures.append(message)
            print(message, flush=True)
            if args.stop_on_error:
                break
        if args.sleep_between:
            time.sleep(args.sleep_between)
    if failures:
        atomic_write(args.metadata_dir / "failures.json", {"failures": failures})
    return 1 if failures else 0


def normalize_artifacts(metadata_dir: Path, taxonomy: dict[str, Any], dry_run: bool = False) -> list[int]:
    changed: list[int] = []
    for path in sorted(metadata_dir.glob("*.json")):
        if path.name == "failures.json":
            continue
        artifact = load_json(path)
        before = list(artifact.get("services") or [])
        after = normalize_services(before, taxonomy)
        if before == after:
            continue
        artifact["services"] = after
        changed.append(int(artifact.get("id", path.stem)))
        if not dry_run:
            atomic_write(path, artifact)
    return changed


def merge_metadata(questions_path: Path, metadata_dir: Path, backup: bool) -> None:
    questions = load_json(questions_path)
    by_id = {int(q["id"]): q for q in questions}
    for qid, question in by_id.items():
        path = artifact_path(metadata_dir, qid)
        if not path.exists():
            raise ValueError(f"missing metadata artifact for q{qid}")
        artifact = load_json(path)
        question["domain"] = artifact["domain"]
        question["services"] = artifact["services"]
    if backup:
        shutil.copy2(questions_path, questions_path.with_name(f"{questions_path.name}.metadata-backup-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"))
    atomic_write(questions_path, questions)


def audit_metadata(questions_path: Path, metadata_dir: Path, config: dict[str, Any], taxonomy: dict[str, Any], audit_path: Path) -> dict[str, Any]:
    questions = load_json(questions_path)
    domains = set(config.get("domains") or [])
    errors: list[str] = []
    unique_services: set[str] = set()
    for question in questions:
        qid = int(question["id"])
        path = artifact_path(metadata_dir, qid)
        if not path.exists():
            errors.append(f"q{qid}: missing metadata artifact")
            continue
        artifact = load_json(path)
        if domains and artifact.get("domain") not in domains:
            errors.append(f"q{qid}: domain not configured: {artifact.get('domain')}")
        services = artifact.get("services") or []
        normalized = normalize_services(services, taxonomy)
        if services != normalized:
            errors.append(f"q{qid}: services not normalized")
        if not services:
            errors.append(f"q{qid}: empty services")
        if len(services) != len(set(services)):
            errors.append(f"q{qid}: duplicate services")
        unique_services.update(services)
    audit = {
        "question_count": len(questions),
        "artifact_count": len([p for p in metadata_dir.glob("*.json") if p.name != "failures.json"]),
        "error_count": len(errors),
        "errors": errors,
        "unique_services": sorted(unique_services),
    }
    atomic_write(audit_path, audit)
    print(json.dumps(audit, ensure_ascii=False, indent=2))
    return audit


def main() -> int:
    parser = argparse.ArgumentParser(description="Classify and merge domain/services metadata.")
    parser.add_argument("--questions", type=Path, default=Path("questions.json"))
    parser.add_argument("--metadata-dir", type=Path, default=Path("metadata"))
    parser.add_argument("--config", type=Path, default=Path("metadata_config.json"))
    parser.add_argument("--taxonomy", type=Path, default=Path("service_taxonomy.json"))
    parser.add_argument("--audit", type=Path, default=Path("metadata_audit.json"))
    parser.add_argument("--base-url", default=os.environ.get("METADATA_BASE_URL") or os.environ.get("OPENAI_BASE_URL"))
    parser.add_argument("--api-key", default=os.environ.get("METADATA_API_KEY") or os.environ.get("OPENAI_API_KEY", ""))
    parser.add_argument("--model", default=os.environ.get("METADATA_MODEL"))
    parser.add_argument("--ids")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--max-tokens", type=int, default=1200)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--transient-retries", type=int, default=0, help="0 means retry transient errors indefinitely.")
    parser.add_argument("--transient-base-sleep", type=int, default=30)
    parser.add_argument("--transient-max-sleep", type=int, default=900)
    parser.add_argument("--sleep-between", type=float, default=0)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--stop-on-error", action="store_true")
    parser.add_argument("--normalize-artifacts", action="store_true")
    parser.add_argument("--merge", action="store_true")
    parser.add_argument("--merge-only", action="store_true")
    parser.add_argument("--audit-only", action="store_true")
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()

    config = load_config(args.config)
    taxonomy = load_taxonomy(args.taxonomy)
    questions = load_json(args.questions)

    if args.normalize_artifacts:
        changed = normalize_artifacts(args.metadata_dir, taxonomy)
        print(f"normalized metadata artifacts: {changed}")
    if args.merge_only:
        merge_metadata(args.questions, args.metadata_dir, backup=not args.no_backup)
        audit = audit_metadata(args.questions, args.metadata_dir, config, taxonomy, args.audit)
        return 1 if audit["errors"] else 0
    if args.audit_only:
        audit = audit_metadata(args.questions, args.metadata_dir, config, taxonomy, args.audit)
        return 1 if audit["errors"] else 0

    if not args.base_url or not args.api_key or not args.model:
        raise SystemExit("set METADATA_BASE_URL/OPENAI_BASE_URL, METADATA_API_KEY/OPENAI_API_KEY, and METADATA_MODEL")
    status = classify_questions(args, questions, config, taxonomy)
    if args.merge:
        merge_metadata(args.questions, args.metadata_dir, backup=not args.no_backup)
    audit = audit_metadata(args.questions, args.metadata_dir, config, taxonomy, args.audit)
    return status or (1 if audit["errors"] else 0)


if __name__ == "__main__":
    raise SystemExit(main())
