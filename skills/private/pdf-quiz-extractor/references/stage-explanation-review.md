# Stage: English Explanation, Review, Arbitration, Synthesis

Use this stage for Phase F-H.

## Goals

- Generate AWS-official-doc-grounded English explanations for every qid.
- Verify answers through conditional review and cross-family arbitration.
- Patch only high-confidence convergent corrections.
- Synthesize clean final English explanations for patched qids before Chinese translation.

## MCP Grounding Contract

Use the AWS documentation MCP server whenever possible.

For each submitted explanation/review/arbitration/synthesis payload:

- `sources` must be non-empty.
- Every `sources` URL host must be allowlisted, normally `docs.aws.amazon.com`. Use `DOCS_HOSTS` for comma-separated AWS official host allowlists and `DOCS_HOST` for the single-host default.
- Explain/review sources must be observed via MCP search/read in the same qid run.
- Arbitration sources may additionally use inherited URLs from prior verified passes.
- Synthesis sources must be inherited from prior verified passes.
- Markdown body must contain at least one visible inline citation URL.
- Every inline URL in markdown must be present in `sources`.
- `sources` may contain additional verified URLs not cited inline, but these should be audited as extra sources.
- Persist host-filtered `sources`, `fetched_urls`, `search_returned_urls`, and `inherited_urls` where applicable.

Validation failure is not transient. Feed the exact validation error back to the model in the same qid context and retry a limited number of times.

## Transient Retry Contract

Bulk API stages treat infrastructure/provider failures as transient:

- HTTP 429, 500, 502, 503, 504, 529
- rate limit, quota, usage limit
- timeout, temporarily unavailable, overloaded
- auth exhaustion such as `auth_unavailable`, `no auth available`, `no_cookie_available`
- provider misrouting signatures such as `image_generation_user_error` or `gpt-image-2`

Default behavior is patient retry with exponential backoff and resume-by-file-existence. Schema/content validation errors get limited correction retries and then per-qid failure logging.

## Explanation Phase

1. Confirm metadata is merged into `questions.json`.
2. Install LLM/MCP dependencies and run `python pipeline/smoke_test.py` once before the bulk API session. Rerun it after model/relay/MCP configuration changes or when diagnosing suspected API failures.
3. Smoke test `explain.py` on single-answer, multi-answer, and markdown/code qids. `smoke_test.py` is a hard gate for the configured API/MCP session and must return exit code 0 before bulk API work.
4. Bulk run `explain.py` with one artifact per qid under `explanations/`.
5. Audit explanation artifacts for citation contract, non-empty sources, no broken sources, and visible inline URLs.

## Review and Arbitration

- `review.py` is conditional but mandatory for every qid whose explanation has `needs_review: true`.
- `cross_family_arbitration.py` is conditional but mandatory for every reviewed `needs_review` qid.
- If only one provider is available, use a different model or prompt and document the degraded independence.
- Same-family agreement can share blind spots; every reviewed `needs_review` qid should get at least one different-family pass when available.

## Answer Patching

Patch only when a human reviewer decides the explanation, review, and arbitration artifacts justify a high-confidence correction. Otherwise annotate ambiguity and keep the source answer.

`patch_answers.py` must:

- preserve `original_correct_answer` whenever `correct_answer` changes
- validate patched answers against actual option keys
- write ambiguity notes to `answer_patch_audit.json`, not final `questions.json`
- record that patches came from human-reviewed `answer_patches.json`, not an automatic model-only decision

## Synthesis

Run `synthesize.py` for every patched qid. Patched qids require explanation, review, and arbitration artifacts before synthesis. The final English explanation must read as if the final answer was always authoritative and must not mention correction history, previous model passes, arbitration, or disagreement.

Do not start final Chinese translation until all patched qids have synthesized English explanations (`synthesized_at` present and `model_chosen_answer == correct_answer`) and all explanation artifacts pass citation/preflight checks.
