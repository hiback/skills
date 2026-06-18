"""Synthesize clean final English explanations for patched questions."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from pathlib import Path

import anthropic  # type: ignore[import-not-found]

from common import (
    StageError,
    allowed_hosts_from_env,
    atomic_write,
    host_is_allowed,
    is_transient_error,
    normalize_url,
    now_iso,
    parse_ids,
    retry_transient_sync,
    validate_citation_contract,
    validate_source_reachability,
)


SYNTHESIS_MODEL = os.environ.get("SYNTHESIS_MODEL")
BASE_URL = os.environ.get("ANTHROPIC_BASE_URL")
API_KEY = os.environ.get("ANTHROPIC_API_KEY")
if not BASE_URL or not API_KEY or not SYNTHESIS_MODEL:
    raise SystemExit("ERROR: set ANTHROPIC_BASE_URL, ANTHROPIC_API_KEY, and SYNTHESIS_MODEL for synthesize.py")
_client = anthropic.Anthropic(base_url=BASE_URL, api_key=API_KEY)
SYNTHESIS_MAX_TOKENS = int(os.environ.get("SYNTHESIS_MAX_TOKENS", "8192"))
RATE_LIMIT_SLEEP = int(os.environ.get("RATE_LIMIT_SLEEP", str(5 * 3600)))
CERT_NAME = os.environ.get("CERT_NAME", "AWS certification exam")
VENDOR_NAME = os.environ.get("VENDOR_NAME", "AWS")
ALLOWED_SOURCE_HOSTS = allowed_hosts_from_env()
ALLOWED_SOURCE_HOST_LABEL = ", ".join(ALLOWED_SOURCE_HOSTS)

EXPLANATIONS_DIR = Path("explanations")
REVIEWS_DIR = Path("reviews")
ARBITRATIONS_DIR = Path("arbitrations")
QUESTIONS_FILE = Path("questions.json")
ANSWER_PATCHES_FILE = Path("answer_patches.json")


SUBMIT_SYNTHESIS_TOOL = {
    "name": "submit_synthesis",
    "description": "Submit the final unified English explanation for this question.",
    "input_schema": {
        "type": "object",
        "properties": {
            "explanation_md_en": {"type": "string"},
            "sources": {"type": "array", "items": {"type": "string", "format": "uri"}},
        },
        "required": ["explanation_md_en", "sources"],
    },
}


SYNTHESIS_SYSTEM_PROMPT = f"""\
You are producing the final authoritative English explanation for a {CERT_NAME} question.

Treat the provided correct_answer as authoritative. Prior analysis from the
explainer, reviewer, and cross-family arbiter is research material only.

Write as if the final answer was always known. Do not mention answer correction,
original/stated answer errors, previous model passes, arbitration, or synthesis.

Your explanation must:
- summarize the scenario
- explain why the correct answer is right with precise {VENDOR_NAME} reasoning
- explain why each incorrect option is wrong
- cite official documentation with markdown links
- use only URLs that appear in the provided prior sources
- cite only URLs on one of: {ALLOWED_SOURCE_HOST_LABEL}

Submit with submit_synthesis, never plain text.
"""


def sleep_with_countdown(seconds: int) -> None:
    end = time.time() + seconds
    while time.time() < end:
        remaining = int(end - time.time())
        h, m = remaining // 3600, (remaining % 3600) // 60
        print(f"[transient] resuming in {h}h {m}m...", flush=True)
        time.sleep(min(600, remaining))


def validate_sources(urls: list[str], *, timeout: float = 10.0) -> list[str]:
    return validate_source_reachability(urls, allowed_host=ALLOWED_SOURCE_HOSTS, timeout=timeout)


def is_rate_limit_error(exc: Exception) -> bool:
    return is_transient_error(exc)


def _retry_call(fn, *, kind: str, qid: int, stage: int):
    return retry_transient_sync(fn, label=f"q{qid} stage{stage} {kind}")


def default_qids(questions: list[dict]) -> list[int]:
    if ANSWER_PATCHES_FILE.exists():
        data = json.loads(ANSWER_PATCHES_FILE.read_text(encoding="utf-8"))
        return sorted(int(qid) for qid in data.get("patches", {}))
    return sorted(q["id"] for q in questions if "original_correct_answer" in q)


def sources_from(*artifacts: dict) -> set[str]:
    urls: set[str] = set()
    for artifact in artifacts:
        urls.update(
            normalize_url(str(url))
            for url in artifact.get("sources", [])
            if host_is_allowed(normalize_url(str(url)), ALLOWED_SOURCE_HOSTS)
        )
    return urls


def validate_payload(payload: dict, allowed_urls: set[str]) -> None:
    if not str(payload.get("explanation_md_en", "")).strip():
        raise StageError("schema_invalid", "explanation_md_en empty")
    validate_citation_contract(
        markdown=str(payload["explanation_md_en"]),
        sources=list(payload.get("sources") or []),
        allowed_host=ALLOWED_SOURCE_HOSTS,
        allowed_urls=allowed_urls,
        label="synthesis explanation_md_en",
    )


def build_user_prompt(question: dict, explanation: dict, review: dict, arbitration: dict) -> str:
    en = question["en"]
    options_lines = [f"{letter}. {en['options'][letter]}" for letter in sorted(en["options"])]
    return (
        f"Question ID: {question['id']}\n"
        f"correct_answer: {question['correct_answer']}\n"
        f"correct_answer_count: {len(question['correct_answer'])}\n"
        f"\n--- Question ---\n{en['question']}\n"
        f"\n--- Options ---\n" + "\n".join(options_lines) + "\n"
        f"\n--- Prior Analysis 1 ---\n{explanation['explanation_md_en']}\n"
        f"Sources: {explanation.get('sources', [])}\n"
        f"\n--- Prior Analysis 2 ---\n{review['reasoning_md']}\n"
        f"Sources: {review.get('sources', [])}\n"
        f"\n--- Prior Analysis 3 ---\n{arbitration['reasoning_md']}\n"
        f"Sources: {arbitration.get('sources', [])}\n"
        f"\nProduce one clean final English explanation supporting correct_answer."
    )


def run_synthesis(question: dict, explanation: dict, review: dict, arbitration: dict) -> dict:
    allowed_urls = sources_from(explanation, review, arbitration)
    messages: list[dict] = [{"role": "user", "content": build_user_prompt(question, explanation, review, arbitration)}]
    validation_retries = 0
    last_text = ""
    while True:
        response = _client.messages.create(
            model=SYNTHESIS_MODEL,
            max_tokens=SYNTHESIS_MAX_TOKENS,
            system=SYNTHESIS_SYSTEM_PROMPT,
            tools=[SUBMIT_SYNTHESIS_TOOL],
            messages=messages,
        )

        for block in response.content:
            if getattr(block, "type", "") == "tool_use" and block.name == "submit_synthesis":
                payload = dict(block.input)
                try:
                    validate_payload(payload, allowed_urls)
                except StageError as exc:
                    validation_retries += 1
                    if validation_retries > 2:
                        raise
                    messages.append({"role": "assistant", "content": response.content})
                    messages.append({
                        "role": "user",
                        "content": [{
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": f"Validation failed: {exc}. Call submit_synthesis again with corrected JSON. Use only inherited URLs.",
                            "is_error": True,
                        }],
                    })
                    break
                return payload
            if getattr(block, "type", "") == "text":
                last_text = getattr(block, "text", "")[:500]
        else:
            raise StageError("tool_not_called", "model did not call submit_synthesis", last_text=last_text)


def process_synthesis(qid: int, questions_index: dict[int, dict]) -> bool:
    question = questions_index[qid]
    if "original_correct_answer" not in question:
        print(f"Q{qid}: no original_correct_answer, skip")
        return False

    paths = {
        "explanation": EXPLANATIONS_DIR / f"{qid}.json",
        "review": REVIEWS_DIR / f"{qid}.json",
        "arbitration": ARBITRATIONS_DIR / f"{qid}.json",
    }
    for label, path in paths.items():
        if not path.exists():
            print(f"Q{qid}: missing {label} artifact {path}")
            return False

    explanation = json.loads(paths["explanation"].read_text(encoding="utf-8"))
    review = json.loads(paths["review"].read_text(encoding="utf-8"))
    arbitration = json.loads(paths["arbitration"].read_text(encoding="utf-8"))

    print(f"Q{qid}: synthesizing ({SYNTHESIS_MODEL})", flush=True)
    payload: dict | None = None
    for attempt in range(2):
        try:
            payload = _retry_call(
                lambda: run_synthesis(question, explanation, review, arbitration),
                kind="run_synthesis", qid=qid, stage=0,
            )
            break
        except StageError as exc:
            print(f"  [synth] {exc.error_type}: {exc}")
            if attempt == 1:
                return False
    assert payload is not None

    broken_urls = validate_sources(payload["sources"])
    reasons = ["broken_url_in_sources"] if broken_urls else []
    explanation["explanation_md_en"] = payload["explanation_md_en"]
    explanation["model_chosen_answer"] = list(question["correct_answer"])
    explanation["sources"] = payload["sources"]
    explanation["broken_sources"] = broken_urls
    explanation["needs_review"] = bool(reasons)
    explanation["needs_review_reasons"] = reasons
    explanation["needs_review_note"] = ""
    explanation["synthesized_at"] = now_iso()
    explanation["synthesis_model"] = SYNTHESIS_MODEL
    atomic_write(paths["explanation"], explanation)

    print(f"Q{qid}: synthesized, sources={len(payload['sources'])}, broken={len(broken_urls)}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Synthesize final English explanations for patched questions.")
    parser.add_argument("--ids", type=parse_ids, default=None)
    args = parser.parse_args()

    if not QUESTIONS_FILE.exists():
        sys.exit(f"missing {QUESTIONS_FILE}")

    questions = json.loads(QUESTIONS_FILE.read_text(encoding="utf-8"))
    questions_index = {q["id"]: q for q in questions}
    qids = args.ids if args.ids else default_qids(questions)

    failed: list[int] = []
    for qid in qids:
        while True:
            try:
                if not process_synthesis(qid, questions_index):
                    failed.append(qid)
                break
            except anthropic.APIStatusError as exc:
                if is_rate_limit_error(exc):
                    print(f"\n[!] rate limit on qid {qid}: {exc}")
                    print(f"[!] body: {getattr(exc, 'body', '<none>')}")
                    sleep_with_countdown(RATE_LIMIT_SLEEP)
                    continue
                print(f"Q{qid}: api_error, skip: {exc}")
                failed.append(qid)
                break
            except KeyboardInterrupt:
                print("\n[!] interrupted by user")
                return 130
            except Exception:  # noqa: BLE001
                print(f"Q{qid}: unexpected error, skip\n{traceback.format_exc()}")
                failed.append(qid)
                break

    if failed:
        print(f"Failed: {failed}")
        return 1
    print(f"Synthesized {len(qids)} question(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
