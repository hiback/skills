"""Shared helpers for the PDF quiz extraction pipeline templates."""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, TypeVar
from urllib.parse import urlparse


T = TypeVar("T")

URL_RE = re.compile(r"https?://[^\s)\]}>\"']+")
FENCE_RE = re.compile(r"```[\s\S]*?```")
INLINE_CODE_RE = re.compile(r"(`+)([^`]*?)(\1)")

WORKFLOW_FIELDS = {
    "needs_review",
    "needs_review_reasons",
    "needs_review_note",
    "model_chosen_answer",
    "sources",
    "broken_sources",
    "fetched_urls",
    "search_returned_urls",
    "inherited_urls",
    "structure_metrics",
    "metadata_confidence",
    "metadata_evidence",
    "metadata_model",
    "metadata_generated_at",
    "confidence",
    "evidence",
    "stage1_model",
    "reviewer_model",
    "arbitration_model",
    "synthesis_model",
    "translation_model",
    "generated_at",
    "translated_at",
    "synthesized_at",
    "thinking_present",
    "thinking_chars",
    "thinking_budget_tokens",
    "reasoning_effort",
    "reasoning_tokens",
    "usage",
    "cost",
    "provider_usage",
    "translation_warnings",
    "most_voted",
    "possible_lost_a_option",
    "auto_recovered_options",
    "answer_count",
    "original_answer_count",
    "ambiguous_after_review",
    "ambiguous_note",
}

TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504, 529}
TRANSIENT_KEYWORDS = (
    "rate_limit",
    "rate limit",
    "usage_limit",
    "quota",
    "too many requests",
    "limit exceeded",
    "insufficient_quota",
    "service unavailable",
    "temporarily unavailable",
    "timeout",
    "timed out",
    "connection reset",
    "remote protocol error",
    "overloaded",
    "auth_unavailable",
    "no auth available",
    "no_cookie_available",
    "no cookie available",
    "image_generation_user_error",
    "gpt-image-2",
)


class StageError(Exception):
    def __init__(self, error_type: str, message: str, last_text: str = ""):
        super().__init__(message)
        self.error_type = error_type
        self.last_text = last_text


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def atomic_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_ids(raw: str | None) -> list[int]:
    if not raw:
        return []
    return [int(x.strip()) for x in raw.split(",") if x.strip()]


def parse_ids_set(raw: str | None) -> set[int] | None:
    ids = parse_ids(raw)
    return set(ids) if ids else None


def strip_json_wrappers(content: str) -> str:
    value = content.strip()
    if value.startswith("```json"):
        value = value.removeprefix("```json").strip()
        if value.endswith("```"):
            value = value[:-3].strip()
    elif value.startswith("```"):
        value = value.removeprefix("```").strip()
        if value.endswith("```"):
            value = value[:-3].strip()
    return value


def default_uvx_command() -> str:
    cwd_uvx = Path.cwd() / ".venv" / "bin" / "uvx"
    return str(cwd_uvx) if cwd_uvx.exists() else os.environ.get("UVX", "uvx")


def _status_code(exc: BaseException) -> int | None:
    for attr in ("status_code", "status", "code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    return None


def _error_body(exc: BaseException) -> str:
    body = getattr(exc, "body", "") or ""
    response = getattr(exc, "response", None)
    if response is not None:
        body = body or getattr(response, "text", "") or ""
    if isinstance(exc, urllib.error.HTTPError):
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    return str(body)


def is_transient_error(exc: BaseException, *, extra_keywords: Iterable[str] = ()) -> bool:
    status = _status_code(exc)
    if status in TRANSIENT_STATUS_CODES:
        return True
    text = (str(exc) + "\n" + _error_body(exc)).casefold()
    return any(keyword.casefold() in text for keyword in (*TRANSIENT_KEYWORDS, *extra_keywords))


def transient_sleep_seconds(attempt: int, *, base: int = 30, cap: int = 900) -> int:
    return min(cap, base * (2 ** max(0, attempt - 1)))


def retry_transient_sync(
    fn: Callable[[], T],
    *,
    label: str,
    retries: int = 0,
    base_sleep: int = 30,
    max_sleep: int = 900,
) -> T:
    attempt = 0
    while True:
        attempt += 1
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            if not is_transient_error(exc) or (retries and attempt > retries):
                raise
            sleep_for = transient_sleep_seconds(attempt, base=base_sleep, cap=max_sleep)
            print(f"{label}: transient error attempt {attempt}, retrying in {sleep_for}s: {exc}", flush=True)
            time.sleep(sleep_for)


async def retry_transient_async(
    fn: Callable[[], Any],
    *,
    label: str,
    retries: int = 0,
    base_sleep: int = 30,
    max_sleep: int = 900,
) -> Any:
    import asyncio

    attempt = 0
    while True:
        attempt += 1
        try:
            return await fn()
        except Exception as exc:  # noqa: BLE001
            if not is_transient_error(exc) or (retries and attempt > retries):
                raise
            sleep_for = transient_sleep_seconds(attempt, base=base_sleep, cap=max_sleep)
            print(f"{label}: transient error attempt {attempt}, retrying in {sleep_for}s: {exc}", flush=True)
            await asyncio.sleep(sleep_for)


def normalize_url(url: str) -> str:
    return str(url or "").strip().rstrip(".,，。;；:")


def urls(text: str) -> list[str]:
    return [normalize_url(url) for url in URL_RE.findall(text or "")]


def url_set(text: str) -> set[str]:
    return set(urls(text))


def unique(values: Iterable[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            output.append(value)
    return output


def fences(value: str) -> list[str]:
    return FENCE_RE.findall(value or "")


def allowed_hosts_from_env(default: str = "docs.aws.amazon.com") -> list[str]:
    raw = os.environ.get("DOCS_HOSTS") or os.environ.get("DOCS_HOST") or default
    hosts = [host.strip().casefold() for host in raw.split(",") if host.strip()]
    return hosts or [default]


def _allowed_host_set(allowed_hosts: str | Iterable[str]) -> set[str]:
    if isinstance(allowed_hosts, str):
        values = allowed_hosts.split(",")
    else:
        values = allowed_hosts
    return {str(host).strip().casefold() for host in values if str(host).strip()}


def _allowed_hosts_label(allowed_hosts: str | Iterable[str]) -> str:
    return ", ".join(sorted(_allowed_host_set(allowed_hosts)))


def host_is_allowed(url: str, allowed_host: str | Iterable[str]) -> bool:
    try:
        hostname = urlparse(url).hostname
        return bool(hostname and hostname.casefold() in _allowed_host_set(allowed_host))
    except ValueError:
        return False


def validate_citation_contract(
    *,
    markdown: str,
    sources: list[str],
    allowed_host: str | Iterable[str],
    allowed_urls: set[str] | None = None,
    label: str = "explanation",
) -> dict[str, list[str]]:
    normalized_sources = unique(normalize_url(source) for source in sources if normalize_url(source))
    if not normalized_sources:
        raise StageError("schema_invalid", f"{label}: sources empty")
    bad_hosts = [source for source in normalized_sources if not host_is_allowed(source, allowed_host)]
    if bad_hosts:
        raise StageError("schema_invalid", f"{label}: sources contain non-allowlisted hosts {_allowed_hosts_label(allowed_host)}: {bad_hosts}")
    if allowed_urls is not None:
        unseen = [source for source in normalized_sources if source not in allowed_urls]
        if unseen:
            raise StageError("schema_invalid", f"{label}: source URLs were not observed or inherited: {unseen}")

    inline_urls = unique(urls(markdown))
    if not inline_urls:
        raise StageError("schema_invalid", f"{label}: markdown has no visible inline citation URL")
    bad_inline_hosts = [url for url in inline_urls if not host_is_allowed(url, allowed_host)]
    if bad_inline_hosts:
        raise StageError("schema_invalid", f"{label}: inline URLs contain non-allowlisted hosts {_allowed_hosts_label(allowed_host)}: {bad_inline_hosts}")
    source_set = set(normalized_sources)
    inline_not_in_sources = [url for url in inline_urls if url not in source_set]
    if inline_not_in_sources:
        raise StageError("schema_invalid", f"{label}: inline URLs are missing from sources: {inline_not_in_sources}")
    return {
        "inline_urls": inline_urls,
        "extra_sources": [source for source in normalized_sources if source not in set(inline_urls)],
    }


def validate_source_reachability(url_list: list[str], *, allowed_host: str | Iterable[str], timeout: float = 10.0) -> list[str]:
    failed: list[str] = []
    for url in unique(normalize_url(u) for u in url_list if normalize_url(u)):
        if not host_is_allowed(url, allowed_host):
            failed.append(url)
            continue
        headers = {"User-Agent": "pdf-quiz-extractor/1.0"}
        try:
            req = urllib.request.Request(url, method="HEAD", headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as response:
                if getattr(response, "status", 200) < 400:
                    continue
        except Exception:  # noqa: BLE001 - fall back to a tiny GET below
            pass
        try:
            get_headers = {**headers, "Range": "bytes=0-0"}
            req = urllib.request.Request(url, method="GET", headers=get_headers)
            with urllib.request.urlopen(req, timeout=timeout) as response:
                if getattr(response, "status", 200) >= 400:
                    failed.append(url)
        except Exception:  # noqa: BLE001
            failed.append(url)
    return failed


def explanation_text(artifact: dict[str, Any]) -> str:
    return str(artifact.get("explanation_md_en") or artifact.get("explanation_md") or "").strip()


def validate_explanation_ready(artifact: dict[str, Any], *, allowed_host: str | Iterable[str], qid: int | None = None) -> str:
    label = f"q{qid} explanation" if qid is not None else "explanation"
    if artifact.get("needs_review") is True:
        raise ValueError(f"{label}: still needs review")
    if artifact.get("broken_sources"):
        raise ValueError(f"{label}: has broken_sources")
    text = explanation_text(artifact)
    if not text:
        raise ValueError(f"{label}: empty English explanation")
    try:
        validate_citation_contract(
            markdown=text,
            sources=list(artifact.get("sources") or []),
            allowed_host=allowed_host,
            label=label,
        )
    except StageError as exc:
        raise ValueError(str(exc)) from exc
    return text


def mcp_to_anthropic_tools(mcp_tools: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "name": tool.name,
            "description": (tool.description or "").strip(),
            "input_schema": tool.inputSchema or {"type": "object", "properties": {}},
        }
        for tool in mcp_tools
    ]


def mcp_to_openai_tools(mcp_tools: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": (tool.description or "").strip(),
                "parameters": tool.inputSchema or {"type": "object", "properties": {}},
            },
        }
        for tool in mcp_tools
    ]


def format_mcp_result_for_anthropic(result: Any) -> tuple[list[dict[str, str]], bool]:
    blocks: list[dict[str, str]] = []
    for content in getattr(result, "content", []):
        text = getattr(content, "text", None)
        if text is None:
            resource = getattr(content, "resource", None)
            text = getattr(resource, "text", None) if resource is not None else None
        blocks.append({"type": "text", "text": text if text is not None else f"[unsupported content type: {type(content).__name__}]"})
    if not blocks:
        blocks.append({"type": "text", "text": "(empty result)"})
    return blocks, bool(getattr(result, "isError", False))


def mcp_result_to_openai_content(result: Any) -> str:
    parts: list[str] = []
    for content in getattr(result, "content", []):
        text = getattr(content, "text", None)
        if text is None:
            resource = getattr(content, "resource", None)
            text = getattr(resource, "text", None) if resource is not None else None
        parts.append(text if text is not None else f"[unsupported content: {type(content).__name__}]")
    if getattr(result, "isError", False):
        parts.insert(0, "[tool returned isError=true]")
    return "\n".join(parts) if parts else "(empty result)"


def extract_search_urls(content_blocks: list[dict[str, Any]]) -> list[str]:
    output: list[str] = []
    for block in content_blocks:
        text = block.get("text", "") if isinstance(block, dict) else ""
        if not text:
            continue
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            continue
        results = data.get("search_results") if isinstance(data, dict) else None
        if not isinstance(results, list):
            continue
        for item in results:
            if isinstance(item, dict) and isinstance(item.get("url"), str):
                output.append(item["url"])
    return output
