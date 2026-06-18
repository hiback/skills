"""Normalize project-local service/topic labels in questions and metadata.

The taxonomy is data-driven. Put aliases, parent folding, and drop rules in
`service_taxonomy.json` rather than hardcoding one vendor or certification.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_TAXONOMY = {
    "service_aliases": {},
    "parent_services": {},
    "drop_labels": [],
    "strip_prefixes": [],
}


def now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def atomic_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_taxonomy(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return dict(DEFAULT_TAXONOMY)
    data = load_json(path)
    merged = dict(DEFAULT_TAXONOMY)
    merged.update(data)
    return merged


def compact(label: str) -> str:
    return re.sub(r"\s+", " ", str(label or "").strip())


def key(label: str) -> str:
    return compact(label).casefold()


def mapping(raw: dict[str, Any]) -> dict[str, str]:
    return {key(k): compact(v) for k, v in raw.items() if compact(v)}


def strip_configured_prefixes(label: str, prefixes: list[str]) -> str:
    current = compact(label)
    for prefix in prefixes:
        if current.casefold().startswith(str(prefix).casefold()):
            return compact(current[len(str(prefix)) :])
    return current


def normalize_label(label: str, taxonomy: dict[str, Any]) -> str | None:
    aliases = mapping(taxonomy.get("service_aliases", {}))
    parents = mapping(taxonomy.get("parent_services", {}))
    drop = {key(value) for value in taxonomy.get("drop_labels", [])}
    prefixes = [str(value) for value in taxonomy.get("strip_prefixes", [])]

    value = compact(label)
    if not value:
        return None
    value = strip_configured_prefixes(value, prefixes)
    value = aliases.get(key(value), value)
    value = parents.get(key(value), value)
    if key(value) in drop:
        return None
    return value or None


def normalize_services(services: list[Any], taxonomy: dict[str, Any]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in services or []:
        value = normalize_label(str(raw), taxonomy)
        if value is None:
            continue
        norm_key = key(value)
        if norm_key not in seen:
            seen.add(norm_key)
            normalized.append(value)
    return normalized


def service_counter(questions: list[dict[str, Any]]) -> Counter[str]:
    counter: Counter[str] = Counter()
    for question in questions:
        counter.update(question.get("services") or [])
    return counter


def normalize_questions(questions: list[dict[str, Any]], taxonomy: dict[str, Any]) -> tuple[list[dict[str, Any]], list[int]]:
    changed: list[int] = []
    output: list[dict[str, Any]] = []
    for question in questions:
        updated = dict(question)
        before = list(updated.get("services") or [])
        after = normalize_services(before, taxonomy)
        if before != after:
            changed.append(int(updated["id"]))
            updated["services"] = after
        output.append(updated)
    return output, changed


def normalize_metadata_artifacts(metadata_dir: Path, taxonomy: dict[str, Any], dry_run: bool) -> list[int]:
    changed: list[int] = []
    for path in sorted(metadata_dir.glob("*.json")):
        if path.name == "failures.json":
            continue
        data = load_json(path)
        before = list(data.get("services") or [])
        after = normalize_services(before, taxonomy)
        if before == after:
            continue
        data["services"] = after
        changed.append(int(data.get("id", path.stem)))
        if not dry_run:
            atomic_write(path, data)
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description="Normalize project-local service/topic labels.")
    parser.add_argument("--questions", type=Path, default=Path("questions.json"))
    parser.add_argument("--metadata-dir", type=Path, default=Path("metadata"))
    parser.add_argument("--taxonomy", type=Path, default=Path("service_taxonomy.json"))
    parser.add_argument("--audit", type=Path, default=Path("services_normalization_audit.json"))
    parser.add_argument("--questions-only", action="store_true")
    parser.add_argument("--metadata-only", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()

    taxonomy = load_taxonomy(args.taxonomy)
    before_questions = load_json(args.questions) if args.questions.exists() else []
    after_questions = before_questions
    changed_questions: list[int] = []
    if not args.metadata_only:
        after_questions, changed_questions = normalize_questions(before_questions, taxonomy)

    changed_metadata: list[int] = []
    if not args.questions_only and args.metadata_dir.exists():
        changed_metadata = normalize_metadata_artifacts(args.metadata_dir, taxonomy, dry_run=args.dry_run)

    audit = {
        "changed_question_qids": changed_questions,
        "changed_metadata_qids": changed_metadata,
        "unique_services_before": sorted(service_counter(before_questions)),
        "unique_services_after": sorted(service_counter(after_questions)),
        "empty_service_qids": [q.get("id") for q in after_questions if not q.get("services")],
        "dry_run": args.dry_run,
    }
    atomic_write(args.audit, audit)
    print(json.dumps(audit, ensure_ascii=False, indent=2))

    if args.dry_run or args.metadata_only:
        return 0
    if changed_questions:
        if not args.no_backup:
            shutil.copy2(args.questions, args.questions.with_name(f"{args.questions.name}.services-backup-{now_stamp()}"))
        atomic_write(args.questions, after_questions)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
