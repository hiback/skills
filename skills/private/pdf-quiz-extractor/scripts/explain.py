"""Generate MCP-grounded English explanations for questions.json.

Chinese translation is intentionally not part of this script. Run
translate_final.py after answer review, patching, and synthesis are complete.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shlex
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import anthropic  # type: ignore[import-not-found]
from mcp import ClientSession, StdioServerParameters  # type: ignore[import-not-found]
from mcp.client.stdio import stdio_client  # type: ignore[import-not-found]

from common import (
    StageError,
    allowed_hosts_from_env,
    atomic_write,
    default_uvx_command,
    extract_search_urls,
    format_mcp_result_for_anthropic,
    host_is_allowed,
    is_transient_error,
    mcp_to_anthropic_tools,
    normalize_url,
    now_iso,
    parse_ids,
    retry_transient_async,
    retry_transient_sync,
    validate_citation_contract,
    validate_source_reachability,
)


BASE_URL = os.environ.get("ANTHROPIC_BASE_URL")
API_KEY = os.environ.get("ANTHROPIC_API_KEY")
MODEL_STAGE1 = os.environ.get("EXPLAIN_MODEL")
if not BASE_URL or not API_KEY or not MODEL_STAGE1:
    raise SystemExit(
        "ERROR: set ANTHROPIC_BASE_URL, ANTHROPIC_API_KEY, and EXPLAIN_MODEL. "
        "For Anthropic SDK relays, ANTHROPIC_BASE_URL must not include trailing /v1."
    )

CERT_NAME = os.environ.get("CERT_NAME", "AWS certification exam")
VENDOR_NAME = os.environ.get("VENDOR_NAME", "AWS")
ALLOWED_SOURCE_HOSTS = allowed_hosts_from_env()
ALLOWED_SOURCE_HOST_LABEL = ", ".join(ALLOWED_SOURCE_HOSTS)

DOCS_SERVER_PARAMS = StdioServerParameters(
    command=os.environ.get("DOCS_MCP_COMMAND", default_uvx_command()),
    args=shlex.split(
        os.environ.get(
            "DOCS_MCP_ARGS",
            "--from awslabs.aws-documentation-mcp-server@latest awslabs.aws-documentation-mcp-server",
        )
    ),
)

QUESTIONS_FILE = Path("questions.json")
OUTPUT_DIR = Path("explanations")
FAILURES_FILE = Path("failures.jsonl")
RATE_LIMIT_SLEEP = int(os.environ.get("RATE_LIMIT_SLEEP", str(5 * 3600)))
BACKOFF_SECONDS = [10, 30, 90]
MAX_TOKENS_STAGE1 = int(os.environ.get("EXPLAIN_MAX_TOKENS", "8192"))

_client = anthropic.Anthropic(base_url=BASE_URL, api_key=API_KEY)


def is_complete(qid: int) -> bool:
    path = OUTPUT_DIR / f"{qid}.json"
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    return bool(data.get("explanation_md_en"))


def load_questions() -> list[dict]:
    return json.loads(QUESTIONS_FILE.read_text(encoding="utf-8"))


def is_rate_limit_error(exc: Exception) -> bool:
    return is_transient_error(exc)


def log_failure(*, qid: int, stage: int, attempts: int, error_type: str, exc: Exception, last_text: str = "") -> None:
    record = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "qid": qid,
        "stage": stage,
        "attempts": attempts,
        "error_type": error_type,
        "error": str(exc),
        "last_text": last_text[:500],
    }
    with FAILURES_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def sleep_with_countdown(seconds: int) -> None:
    end = time.time() + seconds
    while time.time() < end:
        remaining = int(end - time.time())
        h, m = remaining // 3600, (remaining % 3600) // 60
        print(f"[rate limit] resuming in {h}h {m}m...", flush=True)
        time.sleep(min(600, remaining))


def validate_sources(urls: list[str], *, timeout: float = 10.0) -> list[str]:
    return validate_source_reachability(urls, allowed_host=ALLOWED_SOURCE_HOSTS, timeout=timeout)


SUBMIT_EXPLANATION_TOOL = {
    "name": "submit_explanation",
    "description": "Submit the final English analysis of the exam question.",
    "input_schema": {
        "type": "object",
        "properties": {
            "explanation_md_en": {"type": "string"},
            "model_chosen_answer": {
                "type": "array",
                "items": {"type": "string", "enum": ["A", "B", "C", "D", "E", "F"]},
            },
            "needs_review": {"type": "boolean"},
            "needs_review_reasons": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": [
                        "model_disagrees",
                        "insufficient_evidence",
                        "comment_disagreement",
                        "low_vote_concentration",
                        "ambiguous_question",
                        "multi_answer_mismatch",
                    ],
                },
            },
            "needs_review_note": {"type": "string"},
            "sources": {"type": "array", "items": {"type": "string", "format": "uri"}},
        },
        "required": [
            "explanation_md_en", "model_chosen_answer", "needs_review",
            "needs_review_reasons", "needs_review_note", "sources",
        ],
    },
}

_REQUIRED_FIELDS = {
    "explanation_md_en", "model_chosen_answer", "needs_review",
    "needs_review_reasons", "needs_review_note", "sources",
}


def build_stage1_system_prompt() -> list[dict]:
    text = f"""\
You are a senior {VENDOR_NAME} expert helping students prepare for {CERT_NAME}.

For each question:
1. Analyze with vendor-specific knowledge.
2. You MUST ground your reasoning in current official documentation by calling
   the MCP tools before submit_explanation. At minimum, issue one search or read call.
3. Every citation URL must be on one of: {ALLOWED_SOURCE_HOST_LABEL}; and must have been
   returned or fetched by the MCP tools during this turn.
4. Submit your final analysis using submit_explanation, never plain text.

explanation_md_en requirements:
- Explain the scenario and what is being asked.
- State the correct answer(s) with precise reasoning.
- For each incorrect option, explain the specific reason it is wrong.
- Cite official docs with markdown links.
- sources must contain every URL used in the markdown, deduplicated.

Set needs_review = true when any of:
- model_disagrees: you believe stated correct_answer is wrong
- insufficient_evidence: docs + reasoning cannot support a confident answer
- comment_disagreement: a highly voted comment selected a different answer with valid reasoning
- low_vote_concentration: vote_distribution top answer < 60%
- ambiguous_question: question is unclear or under-specified
- multi_answer_mismatch: chosen answer count suggests the source answer cardinality is wrong

If needs_review = true, needs_review_note must contain detailed English reasoning
for downstream review. If false, use [] and an empty note.
"""
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


def build_stage1_user_prompt(q: dict) -> str:
    en = q["en"]
    options_lines = [f"{letter}. {en['options'][letter]}" for letter in sorted(en["options"])]
    comments = en.get("comments", [])
    if comments:
        comment_lines = []
        for c in sorted(comments, key=lambda x: not x.get("highly_voted", False)):
            comment_lines.append(
                f"- {c.get('user', '?')} | selected: {c.get('selected', '?')} | "
                f"highly_voted: {c.get('highly_voted', False)} | {c.get('time', '')}"
            )
            comment_lines.append(f"  {c.get('content', '')}")
        comments_block = "\n".join(comment_lines)
    else:
        comments_block = "(no comments)"

    return (
        f"Question ID: {q['id']}\n"
        f"Domain: {q.get('domain', '?')}\n"
        f"Services: {q.get('services', [])}\n"
        f"Stated correct_answer: {q['correct_answer']}\n"
        f"Stated correct_answer_count: {len(q['correct_answer'])}\n"
        f"vote_distribution: {q.get('vote_distribution', {})}\n"
        f"\n--- Question ---\n{en['question']}\n"
        f"\n--- Options ---\n" + "\n".join(options_lines) + "\n"
        f"\n--- English User Comments ---\n{comments_block}\n"
    )


def _validate_stage1_payload(payload: dict, seen_urls: set[str]) -> None:
    missing = _REQUIRED_FIELDS - set(payload)
    if missing:
        raise StageError("schema_invalid", f"missing fields: {sorted(missing)}")
    if not str(payload["explanation_md_en"]).strip():
        raise StageError("schema_invalid", "explanation_md_en empty")
    validate_citation_contract(
        markdown=str(payload["explanation_md_en"]),
        sources=list(payload.get("sources") or []),
        allowed_host=ALLOWED_SOURCE_HOSTS,
        allowed_urls=seen_urls,
        label="stage1 explanation",
    )


async def run_stage1(q: dict, session: ClientSession, mcp_tool_specs: list[dict], *, verbose: bool = False) -> dict:
    messages: list[dict] = [{"role": "user", "content": build_stage1_user_prompt(q)}]
    fetched_urls: set[str] = set()
    search_returned_urls: set[str] = set()
    max_loops = 15
    soft_limit = 12
    validation_retries = 0

    for loop_i in range(max_loops):
        response = await asyncio.to_thread(
            _client.messages.create,
            model=MODEL_STAGE1,
            max_tokens=MAX_TOKENS_STAGE1,
            system=build_stage1_system_prompt(),
            tools=mcp_tool_specs + [SUBMIT_EXPLANATION_TOOL],
            messages=messages,
        )
        if verbose:
            print(f"  [explain turn {loop_i}] stop_reason={response.stop_reason}")

        tool_results: list[dict] = []
        validation_retry_sent = False
        for block in response.content:
            if getattr(block, "type", "") != "tool_use":
                continue
            if block.name == "submit_explanation":
                payload = dict(block.input)
                seen_urls = fetched_urls | search_returned_urls
                try:
                    _validate_stage1_payload(payload, seen_urls)
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
                            "content": f"Validation failed: {exc}. Call submit_explanation again with corrected JSON. Do not invent URLs.",
                            "is_error": True,
                        }],
                    })
                    validation_retry_sent = True
                    break
                payload["fetched_urls"] = sorted(u for u in fetched_urls if host_is_allowed(u, ALLOWED_SOURCE_HOSTS))
                payload["search_returned_urls"] = sorted(u for u in search_returned_urls if host_is_allowed(u, ALLOWED_SOURCE_HOSTS))
                return payload

            tool_args = dict(block.input)
            try:
                mcp_result = await session.call_tool(block.name, arguments=tool_args)
                content_blocks, is_error = format_mcp_result_for_anthropic(mcp_result)
            except Exception as exc:  # noqa: BLE001
                content_blocks = [{"type": "text", "text": f"MCP call failed: {exc}"}]
                is_error = True
            else:
                if block.name == "search_documentation" and not is_error:
                    search_returned_urls.update(normalize_url(url) for url in extract_search_urls(content_blocks))
                if block.name in ("read_documentation", "read_sections") and not is_error:
                    url = tool_args.get("url")
                    if isinstance(url, str) and url:
                        fetched_urls.add(normalize_url(url))
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": content_blocks,
                "is_error": is_error,
            })

        if validation_retry_sent:
            continue

        if not tool_results and response.stop_reason == "end_turn":
            raise StageError("tool_not_called", "model ended turn without submit_explanation", _last_text(response.content))

        messages.append({"role": "assistant", "content": response.content})
        if tool_results:
            messages.append({"role": "user", "content": tool_results})
        if loop_i >= soft_limit:
            remaining = max_loops - loop_i - 1
            messages.append({"role": "user", "content": f"NOTE: {remaining} turns remain. Stop searching and call submit_explanation."})

    raise StageError("tool_not_called", f"exceeded {max_loops} loop iterations")


def _last_text(content_blocks) -> str:
    for block in reversed(content_blocks):
        if getattr(block, "type", "") == "text":
            return getattr(block, "text", "")[:500]
    return ""


def _retry_call(fn, *, kind: str, qid: int, stage: int) -> Any:
    return retry_transient_sync(fn, label=f"q{qid} stage{stage} {kind}")


async def _retry_call_async(coro_fn, *, kind: str, qid: int, stage: int) -> Any:
    return await retry_transient_async(coro_fn, label=f"q{qid} stage{stage} {kind}")


async def process_question(q: dict, session: ClientSession, mcp_tool_specs: list[dict], *, verbose: bool = False) -> None:
    qid = q["id"]
    out_path = OUTPUT_DIR / f"{qid}.json"
    if is_complete(qid):
        if verbose:
            print(f"Q{qid}: already complete")
        return

    print(f"Q{qid}: explaining ({MODEL_STAGE1} + docs MCP)", flush=True)
    payload: dict | None = None
    for attempt in range(2):
        try:
            payload = await _retry_call_async(
                lambda: run_stage1(q, session, mcp_tool_specs, verbose=verbose),
                kind="run_stage1", qid=qid, stage=1,
            )
            break
        except StageError as exc:
            print(f"  [explain] {exc.error_type}: {exc}")
            if attempt == 1:
                log_failure(qid=qid, stage=1, attempts=attempt + 1, error_type=exc.error_type, exc=exc, last_text=exc.last_text)
                return
    assert payload is not None

    broken_urls = validate_sources(payload["sources"])
    reasons = list(payload["needs_review_reasons"])
    if broken_urls:
        reasons.append("broken_url_in_sources")

    final_needs_review = bool(payload["needs_review"] or broken_urls)
    out = {
        "id": qid,
        "explanation_md_en": payload["explanation_md_en"],
        "model_chosen_answer": payload["model_chosen_answer"],
        "needs_review": final_needs_review,
        "needs_review_reasons": reasons,
        "needs_review_note": payload["needs_review_note"],
        "sources": payload["sources"],
        "fetched_urls": payload.get("fetched_urls", []),
        "search_returned_urls": payload.get("search_returned_urls", []),
        "broken_sources": broken_urls,
        "stage1_model": MODEL_STAGE1,
        "generated_at": now_iso(),
    }
    atomic_write(out_path, out)
    flag = " needs_review" if final_needs_review else ""
    print(f"Q{qid}: done{flag}")


def validate_metadata_gate(targets: list[dict]) -> None:
    missing: list[str] = []
    for q in targets:
        if not str(q.get("domain", "")).strip():
            missing.append(f"q{q['id']}: missing domain")
        if not q.get("services"):
            missing.append(f"q{q['id']}: missing services")
    if missing:
        raise SystemExit("Metadata gate failed before explanation generation:\n" + "\n".join(missing[:100]))


async def main_async() -> int:
    parser = argparse.ArgumentParser(description="Generate English explanations for quiz questions.")
    parser.add_argument("--ids", type=parse_ids, default=None)
    parser.add_argument("--start", type=int, default=None)
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(exist_ok=True)
    questions = load_questions()
    if args.ids:
        wanted = set(args.ids)
        targets = [q for q in questions if q["id"] in wanted]
        for q in targets:
            (OUTPUT_DIR / f"{q['id']}.json").unlink(missing_ok=True)
    else:
        targets = questions
        if args.start is not None:
            targets = [q for q in targets if q["id"] >= args.start]

    print(f"Processing {len(targets)} question(s).", flush=True)
    validate_metadata_gate(targets)
    print(f"Spawning docs MCP server: {DOCS_SERVER_PARAMS.command}", flush=True)

    async with stdio_client(DOCS_SERVER_PARAMS) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            mcp_tools = await session.list_tools()
            mcp_tool_specs = mcp_to_anthropic_tools(mcp_tools.tools)
            print(f"Docs MCP ready: {[t['name'] for t in mcp_tool_specs]}", flush=True)

            for q in targets:
                while True:
                    try:
                        await process_question(q, session, mcp_tool_specs, verbose=args.smoke)
                        break
                    except anthropic.APIStatusError as exc:
                        if is_rate_limit_error(exc):
                            print(f"\n[!] rate limit on qid {q['id']}: {exc}")
                            print(f"[!] raw error body: {getattr(exc, 'body', '<none>')}")
                            await asyncio.to_thread(sleep_with_countdown, RATE_LIMIT_SLEEP)
                            continue
                        log_failure(qid=q["id"], stage=0, attempts=1, error_type="api_error", exc=exc)
                        print(f"Q{q['id']}: api_error, skip")
                        break
                    except KeyboardInterrupt:
                        print("\n[!] interrupted by user")
                        return 130
                    except Exception as exc:  # noqa: BLE001
                        tb = traceback.format_exc()
                        log_failure(qid=q["id"], stage=0, attempts=1, error_type="unexpected", exc=exc, last_text=tb)
                        print(f"Q{q['id']}: unexpected error, skip\n{tb}")
                        break
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    sys.exit(main())
