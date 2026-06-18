"""Relay, tool-calling, and docs MCP smoke tests for the PDF quiz pipeline."""

from __future__ import annotations

import asyncio
import json
import os
import shlex
from urllib.parse import urlparse

from common import allowed_hosts_from_env, default_uvx_command, host_is_allowed, urls


ANTHROPIC_BASE_URL = os.environ.get("ANTHROPIC_BASE_URL")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
EXPLAIN_MODEL = os.environ.get("EXPLAIN_MODEL")
REVIEW_MODEL = os.environ.get("REVIEW_MODEL")
TRANSLATION_MODEL = os.environ.get("TRANSLATION_MODEL")
SYNTHESIS_MODEL = os.environ.get("SYNTHESIS_MODEL")

ARBITRATION_BASE_URL = os.environ.get("ARBITRATION_BASE_URL")
ARBITRATION_API_KEY = os.environ.get("ARBITRATION_API_KEY", "none")
ARBITRATION_MODEL = os.environ.get("ARBITRATION_MODEL")

METADATA_BASE_URL = os.environ.get("METADATA_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
METADATA_API_KEY = os.environ.get("METADATA_API_KEY") or os.environ.get("OPENAI_API_KEY")
METADATA_MODEL = os.environ.get("METADATA_MODEL")

DOCS_HOSTS = allowed_hosts_from_env()
DOCS_HOST_LABEL = ", ".join(DOCS_HOSTS)
FAILURES = 0


def banner(title: str) -> None:
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


def check(ok: bool, note: str) -> str:
    global FAILURES
    if not ok:
        FAILURES += 1
    return f"  [{'PASS' if ok else 'FAIL'}] {note}"


def skip(note: str) -> str:
    return f"  [SKIP] {note}"


def host(raw_url: str) -> str:
    return urlparse(raw_url).hostname or "<configured>"


def anthropic_client():
    if not ANTHROPIC_BASE_URL or not ANTHROPIC_API_KEY:
        return None
    import anthropic  # type: ignore[import-not-found]

    client = anthropic.Anthropic(base_url=ANTHROPIC_BASE_URL, api_key=ANTHROPIC_API_KEY)
    print(f"Anthropic base URL host configured: {host(str(client.base_url))}")
    if str(client.base_url).rstrip("/").endswith("/v1"):
        print("  WARNING: Anthropic SDK base_url should usually not end in /v1.")
    return client


def test_anthropic_basic(client, model: str, label: str) -> None:
    banner(f"Anthropic basic chat ({label}: {model})")
    try:
        response = client.messages.create(
            model=model,
            max_tokens=20,
            messages=[{"role": "user", "content": "Reply with exactly: OK"}],
        )
        text = "".join(getattr(block, "text", "") for block in response.content if getattr(block, "type", "") == "text").strip()
        print(check("OK" in text, f"model replied: {text!r}"))
    except Exception as exc:  # noqa: BLE001
        print(check(False, f"model call failed: {type(exc).__name__}: {exc}"))
        print(f"  body: {getattr(exc, 'body', '<none>')}")


def test_anthropic_tool_roundtrip(client, model: str, label: str) -> None:
    banner(f"Anthropic tool round-trip ({label}: {model})")
    tool = {
        "name": "lookup_value",
        "description": "Return a value for a key.",
        "input_schema": {
            "type": "object",
            "properties": {"key": {"type": "string"}},
            "required": ["key"],
        },
    }
    try:
        response = client.messages.create(
            model=model,
            max_tokens=256,
            tools=[tool],
            messages=[{"role": "user", "content": "Use lookup_value with key=ping, then answer with the value."}],
        )
        tool_uses = [block for block in response.content if getattr(block, "type", "") == "tool_use"]
        print(check(bool(tool_uses), f"tool_use blocks: {[getattr(block, 'name', '?') for block in tool_uses]}"))
        if not tool_uses:
            return
        followup = client.messages.create(
            model=model,
            max_tokens=256,
            tools=[tool],
            messages=[
                {"role": "user", "content": "Use lookup_value with key=ping, then answer with the value."},
                {"role": "assistant", "content": response.content},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_uses[0].id,
                            "content": "pong",
                        }
                    ],
                },
            ],
        )
        text = "".join(getattr(block, "text", "") for block in followup.content if getattr(block, "type", "") == "text")
        print(check("pong" in text.lower(), f"tool result consumed: {text!r}"))
    except Exception as exc:  # noqa: BLE001
        print(check(False, f"tool round-trip failed: {type(exc).__name__}: {exc}"))
        print(f"  body: {getattr(exc, 'body', '<none>')}")


def test_anthropic_thinking(client, model: str) -> None:
    banner(f"Anthropic extended thinking exposure ({model})")
    try:
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            thinking={"type": "enabled", "budget_tokens": 2048},
            messages=[{"role": "user", "content": "Think step by step: which is heavier, 1 kg of feathers or 1 kg of steel?"}],
        )
        thinking_present = False
        thinking_chars = 0
        for block in response.content:
            if getattr(block, "type", "") == "thinking":
                thinking_present = True
                thinking_chars += len(getattr(block, "thinking", "") or "")
        print(f"  thinking_present={thinking_present}, thinking_chars={thinking_chars}")
    except Exception as exc:  # noqa: BLE001
        print(f"  [WARN] thinking call failed: {exc}")


def test_openai_basic_and_tool() -> None:
    banner("OpenAI-compatible arbitration relay")
    if not ARBITRATION_BASE_URL or not ARBITRATION_MODEL:
        if ARBITRATION_BASE_URL or ARBITRATION_MODEL:
            print(check(False, "ARBITRATION_BASE_URL and ARBITRATION_MODEL must be set together"))
        else:
            print(skip("ARBITRATION_BASE_URL or ARBITRATION_MODEL not set"))
        return
    try:
        import openai  # type: ignore[import-not-found]

        client = openai.OpenAI(base_url=ARBITRATION_BASE_URL, api_key=ARBITRATION_API_KEY)
        response = client.chat.completions.create(
            model=ARBITRATION_MODEL,
            max_tokens=20,
            messages=[{"role": "user", "content": "Reply with exactly: OK"}],
        )
        text = (response.choices[0].message.content or "").strip()
        print(check("OK" in text, f"basic chat replied: {text!r}"))

        tool_response = client.chat.completions.create(
            model=ARBITRATION_MODEL,
            max_tokens=256,
            messages=[{"role": "user", "content": "Call lookup_value with key ping."}],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "lookup_value",
                        "description": "Return a value for a key.",
                        "parameters": {
                            "type": "object",
                            "properties": {"key": {"type": "string"}},
                            "required": ["key"],
                        },
                    },
                }
            ],
        )
        calls = tool_response.choices[0].message.tool_calls or []
        print(check(bool(calls), f"function tool calls: {[call.function.name for call in calls]}"))
        if calls:
            followup = client.chat.completions.create(
                model=ARBITRATION_MODEL,
                max_tokens=256,
                messages=[
                    {"role": "user", "content": "Call lookup_value with key ping."},
                    {
                        "role": "assistant",
                        "content": tool_response.choices[0].message.content or "",
                        "tool_calls": [
                            {
                                "id": call.id,
                                "type": "function",
                                "function": {"name": call.function.name, "arguments": call.function.arguments},
                            }
                            for call in calls
                        ],
                    },
                    *[{"role": "tool", "tool_call_id": call.id, "content": "pong"} for call in calls],
                ],
                tools=[
                    {
                        "type": "function",
                        "function": {
                            "name": "lookup_value",
                            "description": "Return a value for a key.",
                            "parameters": {
                                "type": "object",
                                "properties": {"key": {"type": "string"}},
                                "required": ["key"],
                            },
                        },
                    }
                ],
            )
            text = (followup.choices[0].message.content or "").strip()
            print(check("pong" in text.lower(), f"function tool result consumed: {text!r}"))
    except Exception as exc:  # noqa: BLE001
        print(check(False, f"OpenAI-compatible relay failed: {exc}"))


def test_metadata_basic() -> None:
    banner("OpenAI-compatible metadata relay")
    if not METADATA_BASE_URL or not METADATA_MODEL:
        if METADATA_BASE_URL or METADATA_MODEL:
            print(check(False, "METADATA_BASE_URL/OPENAI_BASE_URL and METADATA_MODEL must be set together"))
        else:
            print(skip("METADATA_BASE_URL/OPENAI_BASE_URL or METADATA_MODEL not set"))
        return
    if not METADATA_API_KEY:
        print(check(False, "METADATA_API_KEY or OPENAI_API_KEY is required for metadata model smoke test"))
        return
    try:
        import openai  # type: ignore[import-not-found]

        client = openai.OpenAI(base_url=METADATA_BASE_URL, api_key=METADATA_API_KEY)
        response = client.chat.completions.create(
            model=METADATA_MODEL,
            max_tokens=80,
            messages=[{"role": "user", "content": "Return exactly this JSON object: {\"ok\": true}"}],
        )
        text = (response.choices[0].message.content or "").strip()
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        print(check(isinstance(parsed, dict) and parsed.get("ok") is True, f"metadata model JSON reply: {text!r}"))
    except Exception as exc:  # noqa: BLE001
        print(check(False, f"metadata relay failed: {exc}"))


banner("Anthropic-compatible relay")
ac = anthropic_client()
if ac is None:
    if EXPLAIN_MODEL or REVIEW_MODEL or TRANSLATION_MODEL or SYNTHESIS_MODEL:
        print(check(False, "ANTHROPIC_BASE_URL and ANTHROPIC_API_KEY are required for configured Anthropic models"))
    else:
        print(skip("ANTHROPIC_BASE_URL or ANTHROPIC_API_KEY not set"))
else:
    for label, model in (("explain", EXPLAIN_MODEL), ("review", REVIEW_MODEL), ("translation", TRANSLATION_MODEL), ("synthesis", SYNTHESIS_MODEL)):
        if model:
            test_anthropic_basic(ac, model, label)
    tool_models = [("explain", EXPLAIN_MODEL), ("review", REVIEW_MODEL), ("synthesis", SYNTHESIS_MODEL)]
    tested_tool_models: set[str] = set()
    for label, model in tool_models:
        if model and model not in tested_tool_models:
            tested_tool_models.add(model)
            test_anthropic_tool_roundtrip(ac, model, label)
    thinking_model = REVIEW_MODEL or EXPLAIN_MODEL or SYNTHESIS_MODEL
    if thinking_model:
        test_anthropic_thinking(ac, thinking_model)
    else:
        print(skip("no Anthropic model env vars set"))

test_metadata_basic()
test_openai_basic_and_tool()

banner("Docs MCP server")
from mcp import ClientSession, StdioServerParameters  # type: ignore[import-not-found]
from mcp.client.stdio import stdio_client  # type: ignore[import-not-found]

SERVER_PARAMS = StdioServerParameters(
    command=os.environ.get("DOCS_MCP_COMMAND", default_uvx_command()),
    args=shlex.split(
        os.environ.get(
            "DOCS_MCP_ARGS",
            "--from awslabs.aws-documentation-mcp-server@latest awslabs.aws-documentation-mcp-server",
        )
    ),
)


async def mcp_probe() -> None:
    async with stdio_client(SERVER_PARAMS) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = sorted(tool.name for tool in tools.tools)
            print(check(bool(names), f"tools: {names}"))
            required = {"search_documentation", "read_documentation", "read_sections", "recommend"}
            missing = sorted(required - set(names))
            print(check(not missing, f"required MCP tools present; missing={missing}"))
            if "search_documentation" not in names:
                return
            result = await session.call_tool("search_documentation", arguments={"search_phrase": "object storage", "limit": 2})
            text = "\n".join(getattr(content, "text", "") for content in result.content)
            search_urls = urls(text)
            allowed_search_urls = [url for url in search_urls if host_is_allowed(url, DOCS_HOSTS)]
            print(check(bool(text), "search_documentation returned results"))
            print(check(bool(allowed_search_urls), f"search returned allowlisted docs URL(s): {DOCS_HOST_LABEL}"))
            if allowed_search_urls and "read_documentation" in names:
                read_result = await session.call_tool("read_documentation", arguments={"url": allowed_search_urls[0]})
                read_text = "\n".join(getattr(content, "text", "") for content in read_result.content)
                print(check(bool(read_text.strip()), "read_documentation returned content"))


try:
    asyncio.run(mcp_probe())
except FileNotFoundError as exc:
    print(check(False, f"MCP command not found: {exc}"))
except Exception as exc:  # noqa: BLE001
    print(check(False, f"MCP probe failed: {type(exc).__name__}: {exc}"))

print("\nSmoke test complete. Next: python pipeline/explain.py --ids <sample ids> --smoke")
raise SystemExit(1 if FAILURES else 0)
