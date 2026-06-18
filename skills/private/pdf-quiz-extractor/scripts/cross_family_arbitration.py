"""Cross-family arbitration for disputed exam questions.

The bundled implementation uses an OpenAI-compatible SDK because many relays
expose that interface. The stage is provider-neutral: replace only the SDK/tool
adapter if the available cross-family model uses a different API.
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
from pathlib import Path
from typing import Any

import openai  # type: ignore[import-not-found]
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
    mcp_result_to_openai_content,
    mcp_to_openai_tools,
    normalize_url,
    now_iso,
    parse_ids,
    retry_transient_async,
    validate_citation_contract,
    validate_source_reachability,
)


BASE_URL = os.environ.get("ARBITRATION_BASE_URL")
API_KEY = os.environ.get("ARBITRATION_API_KEY") or "none"
MODEL = os.environ.get("ARBITRATION_MODEL")
REASONING_EFFORT = os.environ.get("ARBITRATION_EFFORT", "high")
MAX_TOKENS = int(os.environ.get("ARBITRATION_MAX_TOKENS", "8192"))
if not BASE_URL or not MODEL:
    raise SystemExit("ERROR: set ARBITRATION_BASE_URL and ARBITRATION_MODEL")

EXPLANATIONS_DIR = Path("explanations")
REVIEWS_DIR = Path("reviews")
ARBITRATIONS_DIR = Path("arbitrations")
QUESTIONS_FILE = Path("questions.json")
FAILURES_FILE = Path("arbitration_failures.jsonl")
RATE_LIMIT_SLEEP = int(os.environ.get("RATE_LIMIT_SLEEP", str(5 * 3600)))
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

client = openai.OpenAI(base_url=BASE_URL, api_key=API_KEY)


def sleep_with_countdown(seconds: int) -> None:
    end = time.time() + seconds
    while time.time() < end:
        remaining = int(end - time.time())
        h, m = remaining // 3600, (remaining % 3600) // 60
        print(f"[transient] resuming in {h}h {m}m...", flush=True)
        time.sleep(min(600, remaining))


def validate_sources(urls: list[str], *, timeout: float = 10.0) -> list[str]:
    return validate_source_reachability(urls, allowed_host=ALLOWED_SOURCE_HOSTS, timeout=timeout)


def arbitration_is_complete(qid: int) -> bool:
    path = ARBITRATIONS_DIR / f"{qid}.json"
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    return bool(data.get("final_answer"))


def load_targets(qids: list[int]) -> list[dict]:
    questions_index = {q["id"]: q for q in json.loads(QUESTIONS_FILE.read_text(encoding="utf-8"))}
    targets: list[dict] = []
    missing: list[str] = []
    for qid in qids:
        e_path = EXPLANATIONS_DIR / f"{qid}.json"
        r_path = REVIEWS_DIR / f"{qid}.json"
        if qid not in questions_index:
            missing.append(f"{qid} (questions.json)")
            continue
        if not e_path.exists():
            missing.append(f"{qid} (explanations)")
            continue
        if not r_path.exists():
            missing.append(f"{qid} (reviews)")
            continue
        targets.append({
            "question": questions_index[qid],
            "explanation": json.loads(e_path.read_text(encoding="utf-8")),
            "review": json.loads(r_path.read_text(encoding="utf-8")),
        })
    if missing:
        sys.exit(f"Missing artifacts for: {missing}")
    return targets


def is_rate_limit_error(exc: Exception) -> bool:
    return isinstance(exc, openai.RateLimitError) or is_transient_error(exc)


def log_failure(*, qid: int, attempts: int, error_type: str, exc: Exception, last_text: str = "") -> None:
    record = {
        "ts": now_iso(),
        "qid": qid,
        "attempts": attempts,
        "error_type": error_type,
        "error": str(exc),
        "last_text": last_text[:500],
    }
    with FAILURES_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


async def retry_call_async(coro_fn, *, qid: int):
    return await retry_transient_async(coro_fn, label=f"q{qid} arbitration")


SUBMIT_ARBITRATION_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_arbitration",
        "description": "Submit your final independent verdict on this exam question.",
        "parameters": {
            "type": "object",
            "properties": {
                "final_answer": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["A", "B", "C", "D", "E", "F"]},
                },
                "verdict": {
                    "type": "string",
                    "enum": ["agrees_with_stated", "agrees_with_explainer", "agrees_with_reviewer", "agrees_with_none"],
                },
                "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                "reasoning_md": {"type": "string"},
                "sources": {"type": "array", "items": {"type": "string", "format": "uri"}},
            },
            "required": ["final_answer", "verdict", "confidence", "reasoning_md", "sources"],
        },
    },
}

SYSTEM_PROMPT = f"""\
You are an independent {VENDOR_NAME} {CERT_NAME} exam arbiter.

You will see a question plus two prior same-family passes: an explainer and a
reviewer. Your value is a different model-family perspective. Be willing to
agree or disagree with both prior passes.

Use the vendor documentation MCP tools at least once before submit_arbitration.
Every URL in sources must be on one of: {ALLOWED_SOURCE_HOST_LABEL}; and must be either:
- cited by a prior pass, or
- returned/fetched by MCP tools in this arbitration turn.

Submit via submit_arbitration, never plain text.
"""


def build_user_prompt(target: dict) -> str:
    q = target["question"]
    e = target["explanation"]
    r = target["review"]
    en = q["en"]
    options_lines = [f"{letter}. {en['options'][letter]}" for letter in sorted(en["options"])]
    return (
        f"Question ID: {q['id']}\n"
        f"Stated correct_answer: {q['correct_answer']}\n"
        f"Stated correct_answer_count: {len(q['correct_answer'])}\n"
        f"vote_distribution: {q.get('vote_distribution', {})}\n"
        f"\n--- Question ---\n{en['question']}\n"
        f"\n--- Options ---\n" + "\n".join(options_lines) + "\n"
        f"\n--- Pass 1: Explainer ---\n"
        f"Chosen answer: {e['model_chosen_answer']}\n"
        f"needs_review_reasons: {e.get('needs_review_reasons', [])}\n"
        f"Reasoning:\n{e['explanation_md_en']}\n"
        f"Sources: {e.get('sources', [])}\n"
        f"\n--- Pass 2: Reviewer ---\n"
        f"Final answer: {r['final_answer']}\n"
        f"Verdict: {r['verdict']}\n"
        f"Confidence: {r['confidence']}\n"
        f"Reasoning:\n{r['reasoning_md']}\n"
        f"Sources: {r.get('sources', [])}\n"
    )


def validate_payload(payload: dict, valid_letters: set[str], allowed_urls: set[str]) -> None:
    required = {"final_answer", "verdict", "confidence", "reasoning_md", "sources"}
    missing = required - set(payload)
    if missing:
        raise StageError("schema_invalid", f"missing fields: {sorted(missing)}")
    if not str(payload["reasoning_md"]).strip():
        raise StageError("schema_invalid", "reasoning_md empty")
    bad_letters = set(payload["final_answer"]) - valid_letters
    if bad_letters:
        raise StageError("schema_invalid", f"invalid answer letters: {sorted(bad_letters)}")
    validate_citation_contract(
        markdown=str(payload["reasoning_md"]),
        sources=list(payload.get("sources") or []),
        allowed_host=ALLOWED_SOURCE_HOSTS,
        allowed_urls=allowed_urls,
        label="arbitration reasoning_md",
    )


async def run_arbitration(target: dict, session: ClientSession, mcp_tool_specs: list[dict], *, verbose: bool = False) -> dict:
    valid_letters = set(target["question"]["en"]["options"].keys())
    inherited_urls = {
        normalize_url(url)
        for url in (*target["explanation"].get("sources", []), *target["review"].get("sources", []))
        if host_is_allowed(normalize_url(url), ALLOWED_SOURCE_HOSTS)
    }
    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": build_user_prompt(target)},
    ]
    max_loops = 12
    soft_limit = 9
    fetched_urls: set[str] = set()
    search_returned_urls: set[str] = set()
    reasoning_tokens = 0
    last_msg: Any = None
    validation_retries = 0

    for loop_i in range(max_loops):
        kwargs = {
            "model": MODEL,
            "max_completion_tokens": MAX_TOKENS,
            "messages": messages,
            "tools": mcp_tool_specs + [SUBMIT_ARBITRATION_TOOL],
        }
        if REASONING_EFFORT:
            kwargs["reasoning_effort"] = REASONING_EFFORT
        response = await asyncio.to_thread(client.chat.completions.create, **kwargs)
        msg = response.choices[0].message
        last_msg = msg
        details = getattr(response.usage, "completion_tokens_details", None) if response.usage else None
        reasoning_tokens += getattr(details, "reasoning_tokens", 0) if details else 0

        if verbose:
            print(f"  [arbitration turn {loop_i}] finish_reason={response.choices[0].finish_reason}")

        if not msg.tool_calls:
            raise StageError("tool_not_called", "model returned text without tool call", last_text=(msg.content or "")[:500])

        messages.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in msg.tool_calls
            ],
        })

        for tc in msg.tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                args = {}

            if name == "submit_arbitration":
                payload = args
                allowed_urls = inherited_urls | fetched_urls | search_returned_urls
                try:
                    validate_payload(payload, valid_letters, allowed_urls)
                except StageError as exc:
                    validation_retries += 1
                    if validation_retries > 2:
                        raise
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": f"Validation failed: {exc}. Call submit_arbitration again with corrected JSON. Do not invent URLs.",
                    })
                    break
                payload["fetched_urls"] = sorted(url for url in fetched_urls if host_is_allowed(url, ALLOWED_SOURCE_HOSTS))
                payload["search_returned_urls"] = sorted(url for url in search_returned_urls if host_is_allowed(url, ALLOWED_SOURCE_HOSTS))
                payload["inherited_urls"] = sorted(inherited_urls)
                payload["reasoning_tokens"] = reasoning_tokens
                return payload

            try:
                mcp_result = await session.call_tool(name, arguments=args)
                content_blocks, is_error = format_mcp_result_for_anthropic(mcp_result)
                if name == "search_documentation" and not is_error:
                    search_returned_urls.update(normalize_url(url) for url in extract_search_urls(content_blocks))
                if name in ("read_documentation", "read_sections") and not is_error:
                    url = args.get("url")
                    if isinstance(url, str) and url:
                        fetched_urls.add(normalize_url(url))
                tool_content = mcp_result_to_openai_content(mcp_result)
            except Exception as exc:  # noqa: BLE001
                tool_content = f"[MCP call failed: {exc}]"
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": tool_content})

        if loop_i >= soft_limit:
            remaining = max_loops - loop_i - 1
            messages.append({"role": "user", "content": f"NOTE: {remaining} turns remain. Stop searching and call submit_arbitration."})

    raise StageError("tool_not_called", f"exceeded {max_loops} loop iterations", last_text=(getattr(last_msg, "content", "") or "")[:500])


async def process_arbitration(target: dict, session: ClientSession, mcp_tool_specs: list[dict], *, verbose: bool = False) -> None:
    qid = target["question"]["id"]
    out_path = ARBITRATIONS_DIR / f"{qid}.json"
    if arbitration_is_complete(qid):
        if verbose:
            print(f"Q{qid}: already arbitrated")
        return

    print(f"Q{qid}: arbitrating ({MODEL})", flush=True)
    payload: dict | None = None
    for attempt in range(2):
        try:
            payload = await retry_call_async(lambda: run_arbitration(target, session, mcp_tool_specs, verbose=verbose), qid=qid)
            break
        except StageError as exc:
            print(f"  [arbitration] {exc.error_type}: {exc}")
            if attempt == 1:
                log_failure(qid=qid, attempts=attempt + 1, error_type=exc.error_type, exc=exc, last_text=exc.last_text)
                return
    assert payload is not None

    broken_urls = validate_sources(payload["sources"])
    final_set = set(payload["final_answer"])
    stated_set = set(target["question"]["correct_answer"])
    explainer_set = set(target["explanation"]["model_chosen_answer"])
    reviewer_set = set(target["review"]["final_answer"])
    out = {
        "id": qid,
        "final_answer": payload["final_answer"],
        "verdict": payload["verdict"],
        "confidence": payload["confidence"],
        "agrees_with_stated_answer": final_set == stated_set,
        "agrees_with_explainer_answer": final_set == explainer_set,
        "agrees_with_reviewer_answer": final_set == reviewer_set,
        "reasoning_md": payload["reasoning_md"],
        "sources": payload["sources"],
        "fetched_urls": payload.get("fetched_urls", []),
        "search_returned_urls": payload.get("search_returned_urls", []),
        "inherited_urls": payload.get("inherited_urls", []),
        "broken_sources": broken_urls,
        "arbitration_model": MODEL,
        "reasoning_effort": REASONING_EFFORT,
        "reasoning_tokens": payload.get("reasoning_tokens", 0),
        "generated_at": now_iso(),
    }
    atomic_write(out_path, out)
    print(f"Q{qid}: verdict={out['verdict']} confidence={out['confidence']} final_answer={out['final_answer']}")


def auto_select_qids() -> list[int]:
    qids: list[int] = []
    if not EXPLANATIONS_DIR.exists():
        return qids
    for path in sorted(EXPLANATIONS_DIR.glob("*.json"), key=lambda p: int(p.stem) if p.stem.isdigit() else 0):
        if not path.stem.isdigit():
            continue
        try:
            explanation = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if explanation.get("needs_review"):
            qids.append(int(path.stem))
    return qids


async def main_async() -> int:
    parser = argparse.ArgumentParser(description="Cross-family arbitration of disputed questions.")
    parser.add_argument("--ids", type=parse_ids, default=None)
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()

    qids = args.ids if args.ids else auto_select_qids()
    if not qids:
        print("No questions need cross-family arbitration.")
        return 0

    ARBITRATIONS_DIR.mkdir(exist_ok=True)
    targets = load_targets(qids)
    if args.ids:
        for target in targets:
            (ARBITRATIONS_DIR / f"{target['question']['id']}.json").unlink(missing_ok=True)

    print(f"Arbitrating {len(targets)} question(s).", flush=True)
    print(f"Spawning docs MCP server: {DOCS_SERVER_PARAMS.command}", flush=True)
    async with stdio_client(DOCS_SERVER_PARAMS) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            mcp_tools = await session.list_tools()
            mcp_tool_specs = mcp_to_openai_tools(mcp_tools.tools)
            print(f"Docs MCP ready: {[t['function']['name'] for t in mcp_tool_specs]}", flush=True)

            for target in targets:
                qid = target["question"]["id"]
                while True:
                    try:
                        await process_arbitration(target, session, mcp_tool_specs, verbose=args.smoke)
                        break
                    except openai.APIStatusError as exc:
                        if is_rate_limit_error(exc):
                            print(f"\n[!] rate limit on qid {qid}: {exc}")
                            print(f"[!] body: {getattr(exc, 'body', '<none>')}")
                            await asyncio.to_thread(sleep_with_countdown, RATE_LIMIT_SLEEP)
                            continue
                        log_failure(qid=qid, attempts=1, error_type="api_error", exc=exc)
                        print(f"Q{qid}: api_error, skip")
                        break
                    except openai.APIError as exc:
                        log_failure(qid=qid, attempts=1, error_type="api_error", exc=exc)
                        print(f"Q{qid}: api_error, skip")
                        break
                    except KeyboardInterrupt:
                        print("\n[!] interrupted by user")
                        return 130
                    except Exception as exc:  # noqa: BLE001
                        tb = traceback.format_exc()
                        log_failure(qid=qid, attempts=1, error_type="unexpected", exc=exc, last_text=tb)
                        print(f"Q{qid}: unexpected error, skip\n{tb}")
                        break
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    sys.exit(main())
