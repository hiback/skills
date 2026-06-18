"""Extract English-source AWS certification questions into questions.json.

Template defaults target an examtopics-style English PDF. Adapt the regexes and
tail/comment parsing for the current source. Do not extract or merge a Chinese
PDF in this script; Chinese is generated later by translate_final.py.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

from common import atomic_write


OPTION_RE = re.compile(r"^([A-F])\.\s*(.*)$")
OPTION_RE_NOPERIOD = re.compile(r"^([A-F])\s+(\S.*)$")
OPTION_LABEL_CANDIDATE_RE = re.compile(r"^([A-Z])[\.)]\s*(.*)$")
ANSWER_RE = re.compile(r"Correct\s+Answer\s*:\s*([A-F, ]+)", re.I)
VOTE_RE = re.compile(r"\b([A-Za-z][A-Za-z0-9_, -]*)\s*\((\d+)%\)", re.I)
CHOOSE_RE = re.compile(r"\b(?:choose|select)\s+(one|two|three|four|five|six|\d+)\b", re.I)
SUSPICIOUS_IMAGE_RE = re.compile(
    r"shown below|shown above|following code|following json|following yaml|"
    r"following template|following policy|diagram|figure|refer to the image",
    re.I,
)

COUNT_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
}


def pdftotext(pdf: Path) -> str:
    return subprocess.check_output(
        ["pdftotext", "-layout", str(pdf), "-"],
        text=True,
        stderr=subprocess.DEVNULL,
    )


def split_into_questions(lines: list[str], header_re: re.Pattern[str]) -> list[tuple[int, list[str]]]:
    blocks: list[tuple[int, list[str]]] = []
    cur_id: int | None = None
    cur_body: list[str] = []

    for line in lines:
        m = header_re.match(line)
        if m:
            if cur_id is not None:
                blocks.append((cur_id, cur_body))
            cur_id = int(m.group(1))
            cur_body = []
            continue
        if cur_id is not None:
            cur_body.append(line.rstrip())

    if cur_id is not None:
        blocks.append((cur_id, cur_body))
    return blocks


def normalize_answer(raw: str) -> list[str]:
    letters = re.findall(r"[A-F]", raw.upper())
    return list(dict.fromkeys(letters))


def normalize_vote_label(raw: str) -> str:
    compacted = re.sub(r"[\s,]+", "", raw.strip())
    if compacted.casefold() in {"other", "others"}:
        return "Other"
    if re.fullmatch(r"[A-F]+", compacted.upper()):
        return compacted.upper()
    return raw.strip()


def infer_selection_count(stem: str) -> int | None:
    m = CHOOSE_RE.search(stem)
    if not m:
        return None
    token = m.group(1).lower()
    return int(token) if token.isdigit() else COUNT_WORDS.get(token)


def split_tail(lines: list[str]) -> tuple[list[str], list[str]]:
    for i, line in enumerate(lines):
        if ANSWER_RE.search(line):
            return lines[:i], lines[i:]
    return lines, []


def parse_tail(lines: list[str]) -> tuple[list[str], dict[str, int]]:
    text = "\n".join(lines)
    correct: list[str] = []
    if m := ANSWER_RE.search(text):
        correct = normalize_answer(m.group(1))

    votes: dict[str, int] = {}
    for answer, pct in VOTE_RE.findall(text):
        votes[normalize_vote_label(answer)] = int(pct)
    return correct, votes


def parse_question_and_options(lines: list[str], *, auto_recover_lost_a: bool = False) -> tuple[str, dict[str, str], bool, dict[str, object]]:
    stem_lines: list[str] = []
    options: dict[str, list[str]] = {}
    cur_letter: str | None = None
    possible_lost_a = False
    duplicate_option_labels: list[str] = []
    malformed_option_label_candidates: list[str] = []

    for raw in lines:
        line = raw.strip()
        if not line:
            continue

        m = OPTION_RE.match(line)
        label_candidate = OPTION_LABEL_CANDIDATE_RE.match(line)
        if m is None and cur_letter is not None:
            m2 = OPTION_RE_NOPERIOD.match(line)
            if m2 and m2.group(1) == chr(ord(cur_letter) + 1):
                m = m2
        if m is None and label_candidate is not None:
            malformed_option_label_candidates.append(line[:160])

        if m:
            letter = m.group(1)
            cur_letter = letter
            if letter in options:
                duplicate_option_labels.append(letter)
                options[letter].append(line)
            else:
                options[letter] = [m.group(2).strip()]
            continue

        if cur_letter is not None:
            if line.lower() == "most voted":
                continue
            else:
                options[cur_letter].append(line)
            continue

        stem_lines.append(line)

    cleaned: dict[str, str] = {}
    for letter, parts in options.items():
        text = " ".join(p for p in parts if p).strip()
        text = re.sub(r"\s*Most Voted\s*$", "", text, flags=re.I).strip()
        cleaned[letter] = text

    if "A" not in cleaned and {"B", "C", "D"}.issubset(cleaned) and stem_lines:
        possible_lost_a = True
    if possible_lost_a and auto_recover_lost_a:
        cleaned["A"] = stem_lines[-1]
        stem_lines = stem_lines[:-1]

    metrics: dict[str, object] = {
        "duplicate_option_labels": duplicate_option_labels,
        "malformed_option_label_candidates": malformed_option_label_candidates,
    }
    return " ".join(stem_lines).strip(), cleaned, possible_lost_a, metrics


def parse_block(qid: int, lines: list[str], *, auto_recover_lost_a: bool = False) -> dict:
    question_lines, tail_lines = split_tail(lines)
    correct_answer, votes = parse_tail(tail_lines)
    question, options, possible_lost_a, metrics = parse_question_and_options(
        question_lines,
        auto_recover_lost_a=auto_recover_lost_a,
    )
    record = {
        "id": qid,
        "correct_answer": correct_answer,
        "vote_distribution": votes,
        "en": {
            "question": question,
            "options": options,
            "comments": [],
        },
    }
    if possible_lost_a:
        record["possible_lost_a_option"] = True
    if possible_lost_a and auto_recover_lost_a:
        record["auto_recovered_options"] = ["A"]
    if any(metrics.values()):
        record["structure_metrics"] = metrics
    return record


def vote_answer_key(value: str) -> bool:
    return bool(re.fullmatch(r"[A-F]+", value))


def missing_option_labels(options: set[str]) -> list[str]:
    ordered = sorted(letter for letter in options if re.fullmatch(r"[A-F]", letter))
    if not ordered:
        return []
    expected = {chr(code) for code in range(ord("A"), ord(max(ordered)) + 1)}
    return sorted(expected - options)


def normalized_stem(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip().casefold()


def build_audit(records: list[dict]) -> dict:
    seen: set[int] = set()
    stems: dict[str, list[int]] = {}
    duplicate_ids: list[int] = []
    missing_question_text: list[int] = []
    missing_correct: list[int] = []
    no_options: list[int] = []
    too_few_options: list[int] = []
    empty_option_values: list[int] = []
    duplicate_option_labels: list[dict[str, object]] = []
    malformed_option_label_candidates: list[dict[str, object]] = []
    missing_option_label_audit: list[dict[str, object]] = []
    possible_lost_a_option: list[int] = []
    answer_not_in_options: list[int] = []
    selection_count_mismatch: list[dict[str, object]] = []
    image_candidates: list[int] = []
    invalid_vote_keys: list[dict[str, object]] = []
    vote_answer_not_in_options: list[dict[str, object]] = []
    vote_sum_suspicious: list[dict[str, object]] = []
    top_vote_disagrees_with_correct: list[dict[str, object]] = []
    multi_vote_key_count_mismatch: list[dict[str, object]] = []

    for q in records:
        qid = q["id"]
        if qid in seen:
            duplicate_ids.append(qid)
        seen.add(qid)
        stem_key = normalized_stem(q["en"].get("question", ""))
        if stem_key:
            stems.setdefault(stem_key, []).append(qid)

        opts = set(q["en"]["options"])
        answers = set(q["correct_answer"])
        if not str(q["en"].get("question", "")).strip():
            missing_question_text.append(qid)
        if not answers:
            missing_correct.append(qid)
        if not opts:
            no_options.append(qid)
        elif len(opts) < 4:
            too_few_options.append(qid)
        if any(not str(value).strip() for value in q["en"]["options"].values()):
            empty_option_values.append(qid)
        structure = q.get("structure_metrics") or {}
        if structure.get("duplicate_option_labels"):
            duplicate_option_labels.append({"id": qid, "labels": structure["duplicate_option_labels"]})
        if structure.get("malformed_option_label_candidates"):
            malformed_option_label_candidates.append({"id": qid, "candidates": structure["malformed_option_label_candidates"]})
        missing_labels = missing_option_labels(opts)
        if missing_labels:
            missing_option_label_audit.append({"id": qid, "missing": missing_labels, "options": sorted(opts)})
        if q.get("possible_lost_a_option"):
            possible_lost_a_option.append(qid)
        if answers and not answers.issubset(opts):
            answer_not_in_options.append(qid)
        selection_count = infer_selection_count(q["en"]["question"])
        if selection_count is not None and answers and selection_count != len(q["correct_answer"]):
            selection_count_mismatch.append({"id": qid, "selection_count": selection_count, "source_answer_count": len(q["correct_answer"])})
        if SUSPICIOUS_IMAGE_RE.search(q["en"]["question"]):
            image_candidates.append(qid)

        votes = q.get("vote_distribution") or {}
        vote_total = sum(int(value) for value in votes.values() if isinstance(value, int))
        if votes and not 95 <= vote_total <= 105:
            vote_sum_suspicious.append({"id": qid, "sum": vote_total, "vote_distribution": votes})
        non_other_votes = {key: value for key, value in votes.items() if key != "Other"}
        for key in non_other_votes:
            if not vote_answer_key(key):
                invalid_vote_keys.append({"id": qid, "key": key})
                continue
            if not set(key).issubset(opts):
                vote_answer_not_in_options.append({"id": qid, "key": key, "options": sorted(opts)})
            if answers and len(key) != len(q["correct_answer"]):
                multi_vote_key_count_mismatch.append({"id": qid, "key": key, "source_answer_count": len(q["correct_answer"])})
        if non_other_votes:
            top_key = max(non_other_votes, key=lambda key: non_other_votes[key])
            if vote_answer_key(top_key) and set(top_key) != answers:
                top_vote_disagrees_with_correct.append({"id": qid, "top_vote": top_key, "correct_answer": q["correct_answer"]})

    same_stem_duplicate_candidates = [
        {"ids": ids, "question_preview": next(q["en"].get("question", "")[:160] for q in records if q["id"] == ids[0])}
        for ids in stems.values()
        if len(ids) > 1
    ]

    return {
        "count": len(records),
        "duplicate_ids": duplicate_ids,
        "missing_question_text": missing_question_text,
        "missing_correct": missing_correct,
        "no_options": no_options,
        "too_few_options": too_few_options,
        "empty_option_values": empty_option_values,
        "duplicate_option_labels": duplicate_option_labels,
        "malformed_option_label_candidates": malformed_option_label_candidates,
        "missing_option_labels": missing_option_label_audit,
        "possible_lost_a_option": possible_lost_a_option,
        "same_stem_duplicate_candidates": same_stem_duplicate_candidates,
        "answer_not_in_options": answer_not_in_options,
        "selection_count_mismatch": selection_count_mismatch,
        "image_candidates": image_candidates,
        "invalid_vote_keys": invalid_vote_keys,
        "vote_answer_not_in_options": vote_answer_not_in_options,
        "vote_sum_suspicious": vote_sum_suspicious,
        "top_vote_disagrees_with_correct": top_vote_disagrees_with_correct,
        "multi_vote_key_count_mismatch": multi_vote_key_count_mismatch,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract English quiz PDF into questions.json.")
    parser.add_argument("--pdf", required=True, type=Path, help="English source PDF.")
    parser.add_argument("--out", type=Path, default=Path("questions.json"))
    parser.add_argument("--audit", type=Path, default=Path("extraction_audit.json"))
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--auto-recover-lost-a", action="store_true", help="Recover missing option A from the final stem line. Use only after manual confirmation.")
    parser.add_argument(
        "--header-regex",
        default=r"^\s*Question\s*#?\s*(\d+)(?:\s+Topic\s+\d+)?\s*$",
        help="Regex with qid as capture group 1.",
    )
    args = parser.parse_args()

    header_re = re.compile(args.header_regex)
    lines = pdftotext(args.pdf).splitlines()
    blocks = split_into_questions(lines, header_re)
    if args.limit is not None:
        blocks = blocks[: args.limit]

    records = [parse_block(qid, body, auto_recover_lost_a=args.auto_recover_lost_a) for qid, body in blocks]
    audit = build_audit(records)

    atomic_write(args.out, records)
    atomic_write(args.audit, audit)

    print(f"Wrote {len(records)} questions -> {args.out}")
    print(f"Wrote audit -> {args.audit}")
    for key, value in audit.items():
        if key == "count":
            continue
        print(f"  {key}: {len(value)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
