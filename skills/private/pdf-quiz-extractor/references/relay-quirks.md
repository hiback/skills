# Relay / Proxy Endpoint Quirks

Most users run AWS bulk stages through relay/proxy endpoints. Relays are often transparent, but small differences in base URLs, tool-calling support, and transient errors can waste hours during a long pipeline.

Run `python pipeline/smoke_test.py` once before a bulk API session. It is a hard gate for the current API/MCP configuration: any failed configured model, tool-calling, AWS docs MCP, read, or host-allowlist check returns a non-zero exit code. Rerun it after changing model/relay/MCP configuration or when diagnosing suspected API/relay failures.

## What To Test

### 1. Basic Chat

Test every configured model family:

- Anthropic-compatible: `EXPLAIN_MODEL`, `REVIEW_MODEL`, `TRANSLATION_MODEL`, `SYNTHESIS_MODEL`
- OpenAI-compatible: `METADATA_MODEL`, `ARBITRATION_MODEL`

Expected: HTTP 200 and a normal response.

### 2. Tool Calling Round Trip

The pipeline uses model tool/function calling to route docs MCP calls and final `submit_*` payloads. Test tool calling according to the configured model family:

- Anthropic-compatible relays: fake `tool_use` plus `tool_result` round trip.
- OpenAI-compatible relays: fake function call round trip.
- If both families are configured, test both.

This is required for explanation, review, arbitration, and synthesis. Server-side web search is not required by the current pipeline; docs grounding comes from the local AWS docs MCP server plus model tool calls.

### 3. Local AWS Docs MCP Server

Smoke test must verify:

- MCP command starts.
- expected tools are listed, such as `search_documentation`, `read_documentation`, `read_sections`, and `recommend` for AWS docs MCP.
- search/read calls return content.
- persisted citation URLs are host-allowlisted, normally `docs.aws.amazon.com`. Use `DOCS_HOSTS` for comma-separated AWS official host allowlists, or `DOCS_HOST` for a single host.

### 4. Thinking and Caching

Extended thinking and prompt caching are quality/cost enhancers, not grounding requirements. Test them when the relay claims support and persist telemetry where available.

## Base URL Traps

Anthropic Python SDK appends `/v1/messages` to `base_url`. If relay docs show `https://relay.example.com/v1`, passing that full URL to the Anthropic SDK often produces `/v1/v1/messages` and 404.

Recommended Anthropic setting:

```text
ANTHROPIC_BASE_URL=https://relay.example.com
```

OpenAI-compatible SDK appends `/chat/completions` to `base_url`. If the relay uses `/v1` as its API prefix, include it:

```text
ARBITRATION_BASE_URL=https://relay.example.com/v1
```

When in doubt, run `python pipeline/smoke_test.py` and compare the SDK base URL with a manual curl path.

## Transient Error Signals

Bulk API scripts should treat infrastructure/provider failures as transient, not per-qid content failures:

- HTTP 429, 500, 502, 503, 504, 529
- `rate_limit`, `rate limit`, `usage_limit`, `quota`, `too many requests`, `limit exceeded`, `insufficient_quota`
- `service unavailable`, `temporarily unavailable`, `timeout`, `timed out`, `overloaded`
- `auth_unavailable`, `no auth available`, `no_cookie_available`, `no cookie available`
- provider misrouting signatures such as `image_generation_user_error` or `gpt-image-2`
- HTTP 200 responses whose body contains an error object or relay error string

Check both `str(exc)` and any SDK-exposed body/response text.

## Retry Posture

The standard pipeline uses one artifact per qid, atomic writes, and resume-by-file-existence. Therefore the safe default for transient failures is patient retry:

- Default transient retry count is unlimited unless the script exposes an override.
- Use exponential backoff, for example base 30s with cap 900s.
- Log the first full raw transient error body so new relay signatures can be added.
- Do not continue through hundreds of untouched qids and write mass permanent failures after an infrastructure outage.
- Schema/content validation errors are not transient. Feed the validation error back to the model for limited correction retries, then record a per-qid failure.

## Relay-Stripped Thinking

Some relays accept a thinking parameter for billing but strip visible thinking content. Persist telemetry such as `thinking_present`, `thinking_chars`, and provider-specific reasoning token counts so the user can audit whether the feature actually worked.

## Cost Telemetry

Persist available token and cache fields per call:

```jsonc
{
  "input_tokens": 0,
  "output_tokens": 0,
  "cache_creation_input_tokens": 0,
  "cache_read_input_tokens": 0,
  "thinking_chars": 0,
  "reasoning_tokens": 0
}
```

After a bulk run, aggregate telemetry to confirm cost optimizations and relay behavior.
