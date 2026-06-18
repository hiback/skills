"""Second-pass review of needs_review questions.

Uses the same AWS docs MCP server as explain.py.
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
    retry_transient_async,
    validate_citation_contract,
    validate_source_reachability,
)

# ─── config ──────────────────────────────────────────────────────────────────

REVIEWER_MODEL = os.environ.get("REVIEW_MODEL")
BASE_URL = os.environ.get("ANTHROPIC_BASE_URL")
API_KEY = os.environ.get("ANTHROPIC_API_KEY")
if not BASE_URL or not API_KEY or not REVIEWER_MODEL:
    raise SystemExit("ERROR: set ANTHROPIC_BASE_URL, ANTHROPIC_API_KEY, and REVIEW_MODEL for review.py")
_client = anthropic.Anthropic(base_url=BASE_URL, api_key=API_KEY)
THINKING_BUDGET_TOKENS = int(os.environ.get("REVIEW_THINKING_BUDGET_TOKENS", "16000"))
# Headroom for visible output: tool_use args + reasoning_md (200-500 words
# ≈ 350-1000 tokens). 4000 token margin is comfortable.
MAX_TOKENS = THINKING_BUDGET_TOKENS + 4000

EXPLANATIONS_DIR = Path("explanations")
REVIEWS_DIR = Path("reviews")
QUESTIONS_FILE = Path("questions.json")
FAILURES_FILE = Path("review_failures.jsonl")
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


async def _retry_call_async(coro_fn, *, kind: str, qid: int, stage: int):
    return await retry_transient_async(coro_fn, label=f"q{qid} stage{stage} {kind}")


# ─── data layer ──────────────────────────────────────────────────────────────


def review_is_complete(qid: int) -> bool:
    """True iff reviews/{qid}.json exists and contains final_answer."""
    path = REVIEWS_DIR / f"{qid}.json"
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    return bool(data.get("final_answer"))


def filter_targets(*, reasons: set[str] | None, ids: list[int] | None) -> list[dict]:
    """Return list of explanation dicts to review.

    - If `ids` is given: load each id's explanation; SystemExit if any missing.
      `needs_review` is ignored — `--ids` forces inclusion.
    - Else: load all explanations, keep those with needs_review==true.
      If `reasons` is non-None, further filter to those whose
      needs_review_reasons intersects `reasons`.
    """
    if ids is not None:
        targets = []
        missing: list[int] = []
        for qid in ids:
            path = EXPLANATIONS_DIR / f"{qid}.json"
            if not path.exists():
                missing.append(qid)
                continue
            targets.append(json.loads(path.read_text(encoding="utf-8")))
        if missing:
            sys.exit(
                f"Missing explanation files for IDs {missing}. "
                f"Run `python pipeline/explain.py --ids {','.join(map(str, missing))}` first."
            )
        return targets

    targets = []
    for path in sorted(EXPLANATIONS_DIR.glob("*.json"),
                       key=lambda p: int(p.stem)):
        data = json.loads(path.read_text(encoding="utf-8"))
        if not data.get("needs_review"):
            continue
        if reasons is not None and not (set(data.get("needs_review_reasons", [])) & reasons):
            continue
        targets.append(data)
    return targets


# ─── review stage ────────────────────────────────────────────────────────────


SUBMIT_REVIEW_TOOL = {
    "name": "submit_review",
    "description": "Submit your final verdict on this exam question after independent re-analysis.",
    "input_schema": {
        "type": "object",
        "properties": {
            "final_answer": {
                "type": "array",
                "items": {"type": "string", "enum": ["A", "B", "C", "D", "E", "F"]},
                "description": "Letters you conclude are correct after independent analysis.",
            },
            "verdict": {
                "type": "string",
                "enum": ["stated_correct", "explainer_correct",
                         "neither_correct", "ambiguous"],
            },
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
            "reasoning_md": {
                "type": "string",
                "description": "200-500 words English markdown explaining the verdict; cite docs.",
            },
            "sources": {
                "type": "array",
                "items": {"type": "string", "format": "uri"},
            },
        },
        "required": ["final_answer", "verdict", "confidence", "reasoning_md", "sources"],
    },
}


REVIEW_SYSTEM_PROMPT = f"""\
You are a senior {VENDOR_NAME} expert reviewing a flagged {CERT_NAME} question.
Your job: independently re-analyze a flagged exam question and issue a final verdict.

A previous explainer pass already analyzed this question and flagged it for
review. You will see:
- The original question, options, and the stated correct_answer
- The previous pass's chosen answer (may agree or disagree with the stated)
- The previous pass's full English explanation
- The previous pass's needs_review_note explaining why it flagged this question
- Community vote distribution and key user comments

Your role is NOT to rubber-stamp either answer. Approach with fresh skepticism:
- The stated answer may be wrong — community-sourced exam dumps have errors.
- The previous pass may be wrong — it had the same training data you do.
- BOTH may be wrong, and the truly correct answer is something else.
- The question itself may be too ambiguous to resolve to a single answer.

Process:
1. You MUST ground your reasoning in current official docs via the MCP tools below
   BEFORE calling submit_review:
   - `search_documentation(search_phrase=...)` to find relevant pages
   - `read_documentation(url=...)` to fetch full doc content
   - `read_sections(url=..., section_titles=[...])` for targeted reads
   - `recommend(url=...)` to discover related pages
   At minimum issue ONE search_documentation OR read_documentation call.
2. Every URL you cite MUST be on one of: {ALLOWED_SOURCE_HOST_LABEL}; and either was
   returned by search_documentation OR fetched via read_documentation/
   read_sections THIS turn. Do NOT fabricate URLs.
3. Form your own view, ignoring social pressure from either side.
4. Submit via submit_review tool — NEVER as plain text.

Verdict semantics:
- stated_correct      → questions.json answer is right
- explainer_correct   → previous pass's model_chosen_answer is right (and differs from stated)
- neither_correct     → both wrong; final_answer is something else
- ambiguous           → genuinely no single right answer; pick most defensible, confidence=low

confidence semantics:
- high   → official docs unambiguously support your verdict
- medium → docs support but room for interpretation
- low    → significant ambiguity; flag for human review

reasoning_md: 200-500 words. Be direct about specifically what the previous
pass got wrong (if anything). Cite docs with markdown links.
sources: every URL in reasoning_md, deduplicated.
"""


def build_review_system_prompt() -> list[dict]:
    """System prompt as cacheable block (>1024 tokens after framing copy)."""
    return [{
        "type": "text",
        "text": REVIEW_SYSTEM_PROMPT,
        "cache_control": {"type": "ephemeral"},
    }]


def build_review_user_prompt(question: dict, explanation: dict) -> str:
    """Render a question + previous-pass explanation into the reviewer user prompt."""
    en = question["en"]
    options = en["options"]

    options_lines = []
    for letter in sorted(options):
        options_lines.append(f"{letter}. {options[letter]}")

    comments = en.get("comments", [])
    if comments:
        sorted_comments = sorted(comments, key=lambda c: not c.get("highly_voted", False))
        comment_lines = []
        for c in sorted_comments:
            comment_lines.append(
                f"- {c['user']} | selected: {c.get('selected', '?')} | "
                f"highly_voted: {c.get('highly_voted', False)} | {c.get('time', '')}"
            )
            comment_lines.append(f"  {c['content']}")
        comments_block = "\n".join(comment_lines)
    else:
        comments_block = "(no comments)"

    sources_block = "\n".join(explanation.get("sources", [])) or "(none)"

    return (
        f"Question ID: {question['id']}\n"
        f"Domain: {question.get('domain', '?')}, Services: {question.get('services', [])}\n"
        f"Stated correct_answer: {question['correct_answer']}\n"
        f"Stated correct_answer_count: {len(question['correct_answer'])}\n"
        f"vote_distribution: {question.get('vote_distribution', {})}\n"
        f"\n--- Question ---\n{en['question']}\n"
        f"\n--- Options ---\n" + "\n".join(options_lines) + "\n"
        f"\n--- English User Comments (highly_voted first) ---\n{comments_block}\n"
        f"\n═══ Previous Explainer Pass ═══\n"
        f"(All fields below come from explanations/{question['id']}.json,\n"
        f"NOT from questions.json.)\n\n"
        f"Chosen answer: {explanation['model_chosen_answer']}\n"
        f"needs_review_reasons: {explanation['needs_review_reasons']}\n"
        f"broken_sources: {explanation.get('broken_sources', [])}\n"
        f"\nneeds_review_note (200-400 words from previous pass):\n"
        f"{explanation['needs_review_note']}\n"
        f"\nFull explanation:\n{explanation['explanation_md_en']}\n"
        f"\nSources cited:\n{sources_block}\n"
        f"\n═══ Your task ═══\n"
        f"Independently re-analyze. Do you agree with the stated answer, the previous\n"
        f"explainer pass, or with neither? Use the docs MCP tools to verify your\n"
        f"reasoning against current official docs on {ALLOWED_SOURCE_HOST_LABEL}. Then call submit_review.\n"
    )


_REVIEW_REQUIRED_FIELDS = {
    "final_answer", "verdict", "confidence", "reasoning_md", "sources",
}
_VERDICT_VALUES = {"stated_correct", "explainer_correct", "neither_correct", "ambiguous"}
_CONFIDENCE_VALUES = {"high", "medium", "low"}


def _validate_review_payload(
    payload: dict, valid_letters: set[str], seen_urls: set[str]
) -> None:
    """Raise StageError('schema_invalid', ...) if payload is malformed.

    Belt-and-suspenders to the tool schema's enum constraint, plus the same
    citation contract as Stage 1: sources non-empty, host whitelist, and
    every URL must have been seen via MCP tools this turn.
    """
    missing = _REVIEW_REQUIRED_FIELDS - set(payload)
    if missing:
        raise StageError("schema_invalid", f"missing fields: {sorted(missing)}")
    if not str(payload["reasoning_md"]).strip():
        raise StageError("schema_invalid", "reasoning_md empty")
    bad_letters = set(payload["final_answer"]) - valid_letters
    if bad_letters:
        raise StageError(
            "schema_invalid",
            f"final_answer contains letters not in question: {sorted(bad_letters)}",
        )
    if payload["verdict"] not in _VERDICT_VALUES:
        raise StageError("schema_invalid", f"invalid verdict: {payload['verdict']}")
    if payload["confidence"] not in _CONFIDENCE_VALUES:
        raise StageError("schema_invalid", f"invalid confidence: {payload['confidence']}")

    validate_citation_contract(
        markdown=str(payload["reasoning_md"]),
        sources=list(payload.get("sources") or []),
        allowed_host=ALLOWED_SOURCE_HOSTS,
        allowed_urls=seen_urls,
        label="review reasoning_md",
    )


def extract_thinking_telemetry(content_blocks) -> tuple[bool, int]:
    """Return (thinking_present, thinking_chars) for a response's content list.

    'thinking' blocks may be returned with empty content (signature-only) by
    relays that strip reasoning text. We persist both signals so the user can
    audit later whether the relay actually exposes thinking content.
    """
    thinking_blocks = [b for b in content_blocks if getattr(b, "type", "") == "thinking"]
    thinking_present = bool(thinking_blocks)
    thinking_chars = sum(len(getattr(b, "thinking", "") or "") for b in thinking_blocks)
    return thinking_present, thinking_chars


def _last_text_block(content_blocks) -> str:
    """Trailing text block (truncated) for failure logging."""
    for block in reversed(content_blocks):
        if getattr(block, "type", "") == "text":
            return getattr(block, "text", "")[:500]
    return ""


async def run_review(
    question: dict,
    explanation: dict,
    session: ClientSession,
    mcp_tool_specs: list[dict],
    *,
    verbose: bool = False,
) -> dict:
    """Run reviewer pass for one question; return submit_review tool input + telemetry.

    Routes AWS-docs MCP tool calls through the shared `session`. Tracks
    fetched_urls / search_returned_urls for citation grounding.

    Raises StageError for tool_not_called / schema_invalid.
    Raises anthropic.APIError on transport failures (caller handles retry).
    """
    user_prompt = build_review_user_prompt(question, explanation)
    messages: list[dict] = [{"role": "user", "content": user_prompt}]
    valid_letters = set(question["en"]["options"].keys())
    max_loops = 15
    soft_limit = 12

    cumulative_thinking_present = False
    cumulative_thinking_chars = 0
    fetched_urls: set[str] = set()
    search_returned_urls: set[str] = set()
    validation_retries = 0

    for loop_i in range(max_loops):
        response = await asyncio.to_thread(
            _client.messages.create,
            model=REVIEWER_MODEL,
            max_tokens=MAX_TOKENS,
            thinking={"type": "enabled", "budget_tokens": THINKING_BUDGET_TOKENS},
            system=build_review_system_prompt(),
            tools=mcp_tool_specs + [SUBMIT_REVIEW_TOOL],
            messages=messages,
        )

        present, chars = extract_thinking_telemetry(response.content)
        cumulative_thinking_present = cumulative_thinking_present or present
        cumulative_thinking_chars += chars

        if verbose:
            print(f"  [review turn {loop_i}] stop_reason={response.stop_reason} "
                  f"thinking_present={present} thinking_chars={chars}")
            for block in response.content:
                bt = getattr(block, "type", "?")
                if bt == "tool_use":
                    name = getattr(block, "name", "?")
                    args_preview = json.dumps(block.input)[:160]
                    print(f"    tool_use: {name}  args={args_preview}")

        tool_results: list[dict] = []
        validation_retry_sent = False
        for block in response.content:
            if getattr(block, "type", "") != "tool_use":
                continue
            if block.name == "submit_review":
                payload = dict(block.input)
                seen_urls = fetched_urls | search_returned_urls
                try:
                    _validate_review_payload(payload, valid_letters, seen_urls)
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
                            "content": f"Validation failed: {exc}. Call submit_review again with corrected JSON. Do not invent URLs.",
                            "is_error": True,
                        }],
                    })
                    validation_retry_sent = True
                    break
                payload["thinking_present"] = cumulative_thinking_present
                payload["thinking_chars"] = cumulative_thinking_chars
                payload["fetched_urls"] = sorted(url for url in fetched_urls if host_is_allowed(url, ALLOWED_SOURCE_HOSTS))
                payload["search_returned_urls"] = sorted(url for url in search_returned_urls if host_is_allowed(url, ALLOWED_SOURCE_HOSTS))
                return payload
            # Route to MCP
            tool_args = dict(block.input)
            try:
                mcp_result = await session.call_tool(block.name, arguments=tool_args)
                content_blocks, is_error = format_mcp_result_for_anthropic(mcp_result)
            except Exception as e:
                content_blocks = [{"type": "text", "text": f"MCP call failed: {e}"}]
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
            text = _last_text_block(response.content)
            raise StageError(
                "tool_not_called",
                "model ended turn without calling submit_review",
                last_text=text,
            )

        messages.append({"role": "assistant", "content": response.content})
        if tool_results:
            messages.append({"role": "user", "content": tool_results})
        if loop_i >= soft_limit:
            remaining = max_loops - loop_i - 1
            messages.append({
                "role": "user",
                "content": (
                    f"NOTE: you have {remaining} turn(s) remaining. Stop calling "
                    f"search/read tools and call submit_review NOW."
                ),
            })

    raise StageError(
        "tool_not_called",
        f"exceeded {max_loops} loop iterations without submit_review",
    )


# ─── orchestration ───────────────────────────────────────────────────────────


def log_review_failure(*, qid: int, attempts: int, error_type: str,
                       exc: Exception, last_text: str = "") -> None:
    """Append a JSONL record to FAILURES_FILE.

    Schema mirrors explain.py's log_failure (including stage=0 by reviewer
    convention) so both failure logs can be merged with `cat`/`jq` if needed.
    """
    record = {
        "ts": now_iso(),
        "qid": qid,
        "stage": 0,
        "attempts": attempts,
        "error_type": error_type,
        "error": str(exc),
        "last_text": last_text[:500],
    }
    with FAILURES_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _load_questions_index() -> dict[int, dict]:
    """Load questions.json once, key by id for fast per-explanation lookup."""
    return {q["id"]: q for q in json.loads(QUESTIONS_FILE.read_text(encoding="utf-8"))}


async def process_review(
    explanation: dict, question: dict,
    session: ClientSession, mcp_tool_specs: list[dict],
    *, verbose: bool = False,
) -> None:
    """Run reviewer for one question, write reviews/{id}.json, or log + skip on failure."""
    qid = explanation["id"]
    out_path = REVIEWS_DIR / f"{qid}.json"

    if review_is_complete(qid):
        if verbose:
            print(f"[{qid}] already reviewed — skip")
        return

    print(f"[{qid}] reviewing ({REVIEWER_MODEL} + thinking + docs MCP)...", flush=True)

    payload: dict | None = None
    for attempt in range(2):
        try:
            payload = await _retry_call_async(
                lambda: run_review(
                    question, explanation, session, mcp_tool_specs, verbose=verbose,
                ),
                kind="run_review", qid=qid, stage=0,
            )
            break
        except StageError as e:
            print(f"  [review] {e.error_type}: {e}")
            if attempt == 1:
                log_review_failure(qid=qid, attempts=attempt + 1,
                                    error_type=e.error_type, exc=e,
                                    last_text=e.last_text)
                return

    assert payload is not None

    broken_urls = validate_sources(payload["sources"])
    if broken_urls:
        print(f"  [{qid}] {len(broken_urls)} broken URL(s) in sources: {broken_urls}")

    final_set = set(payload["final_answer"])
    stated_set = set(question["correct_answer"])
    explainer_set = set(explanation["model_chosen_answer"])

    out = {
        "id": qid,
        "final_answer": payload["final_answer"],
        "verdict": payload["verdict"],
        "confidence": payload["confidence"],
        "agrees_with_stated_answer": final_set == stated_set,
        "agrees_with_explainer_answer": final_set == explainer_set,
        "reasoning_md": payload["reasoning_md"],
        "sources": payload["sources"],
        "fetched_urls": payload.get("fetched_urls", []),
        "search_returned_urls": payload.get("search_returned_urls", []),
        "broken_sources": broken_urls,
        "thinking_present": payload["thinking_present"],
        "thinking_chars": payload["thinking_chars"],
        "reviewer_model": REVIEWER_MODEL,
        "thinking_budget_tokens": THINKING_BUDGET_TOKENS,
        "generated_at": now_iso(),
    }

    atomic_write(out_path, out)
    print(f"[{qid}] verdict={out['verdict']} confidence={out['confidence']} "
          f"final_answer={out['final_answer']}")


# ─── CLI ─────────────────────────────────────────────────────────────────────


_VALID_REASONS = {
    "model_disagrees", "insufficient_evidence", "comment_disagreement",
    "low_vote_concentration", "ambiguous_question", "multi_answer_mismatch",
    "broken_url_in_sources",
}


def _parse_ids(s: str) -> list[int]:
    return [int(x.strip()) for x in s.split(",") if x.strip()]


def _parse_reasons(s: str) -> set[str]:
    parts = {x.strip() for x in s.split(",") if x.strip()}
    bad = parts - _VALID_REASONS
    if bad:
        raise argparse.ArgumentTypeError(
            f"unknown reason(s) {sorted(bad)}; valid: {sorted(_VALID_REASONS)}"
        )
    return parts


async def main_async() -> int:
    parser = argparse.ArgumentParser(
        description="Review questions flagged needs_review by explain.py."
    )
    parser.add_argument("--ids", type=_parse_ids, default=None,
                        help="Comma-separated question IDs (forces re-review).")
    parser.add_argument("--reasons", type=_parse_reasons, default=None,
                        help=f"Comma-separated reasons to filter; "
                             f"any of: {sorted(_VALID_REASONS)}.")
    parser.add_argument("--smoke", action="store_true",
                        help="Verbose tracing: thinking telemetry, search queries, tool calls.")
    args = parser.parse_args()

    if not QUESTIONS_FILE.exists():
        sys.exit(f"questions.json not found at {QUESTIONS_FILE.resolve()} "
                 f"(run from the project root).")

    REVIEWS_DIR.mkdir(exist_ok=True)

    targets = filter_targets(reasons=args.reasons, ids=args.ids)
    if args.ids:
        for t in targets:
            (REVIEWS_DIR / f"{t['id']}.json").unlink(missing_ok=True)

    questions_index = _load_questions_index()

    print(f"Reviewing {len(targets)} question(s).", flush=True)
    print(f"Spawning docs MCP server: {DOCS_SERVER_PARAMS.command} ...",
          flush=True)

    async with stdio_client(DOCS_SERVER_PARAMS) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            mcp_tools = await session.list_tools()
            mcp_tool_specs = mcp_to_anthropic_tools(mcp_tools.tools)
            print(f"Docs MCP ready: "
                  f"{[t['name'] for t in mcp_tool_specs]}", flush=True)

            for explanation in targets:
                qid = explanation["id"]
                question = questions_index.get(qid)
                if question is None:
                    log_review_failure(
                        qid=qid, attempts=1, error_type="question_missing",
                        exc=RuntimeError(f"qid {qid} not in questions.json"),
                    )
                    print(f"[{qid}] question_missing — skip")
                    continue

                while True:
                    try:
                        await process_review(
                            explanation, question, session, mcp_tool_specs,
                            verbose=args.smoke,
                        )
                        break
                    except anthropic.APIStatusError as e:
                        if is_rate_limit_error(e):
                            print(f"\n[!] rate limit on qid {qid}: {e}")
                            print(f"[!] raw error body: {getattr(e, 'body', '<none>')}")
                            await asyncio.to_thread(
                                sleep_with_countdown, RATE_LIMIT_SLEEP,
                            )
                            continue
                        log_review_failure(qid=qid, attempts=1, error_type="api_error", exc=e)
                        print(f"[{qid}] api_error — skip: {e}")
                        break
                    except anthropic.APIError as e:
                        log_review_failure(qid=qid, attempts=1, error_type="api_error", exc=e)
                        print(f"[{qid}] api_error — skip: {e}")
                        break
                    except KeyboardInterrupt:
                        print("\n[!] interrupted by user")
                        return 130
                    except Exception as e:
                        tb = traceback.format_exc()
                        log_review_failure(qid=qid, attempts=1, error_type="unexpected",
                                           exc=e, last_text=tb)
                        print(f"[{qid}] unexpected error — skip\n{tb}")
                        break

    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    sys.exit(main())
