# scripts/ - AWS Pipeline Templates

Copy the entire `scripts/` directory into the project as `pipeline/` and run scripts from the project root:

```bash
python pipeline/extract.py --pdf english.pdf
python pipeline/explain.py --ids 1,2,3 --smoke
```

Data files such as `questions.json`, `image_object_audit.json`, `metadata/`, `explanations/`, `reviews/`, `arbitrations/`, `translations/`, and audit files are expected in the project root. Do not copy single scripts in isolation; API-stage scripts share `pipeline/common.py`.

These templates are AWS certification question-bank templates. They default to the AWS documentation MCP server and `docs.aws.amazon.com` citation allowlist.

Minimum external dependencies depend on the stage: `pdftotext`/poppler for extraction, `pdfplumber` for image audits, `anthropic`, `openai`, `mcp`, and `uvx` for API/MCP stages. Recommended setup after copying to `pipeline/`:

```bash
python -m venv .venv
.venv/bin/python -m pip install -r pipeline/requirements.txt
```

Scripts do not require a venv, but run them with the Python interpreter that has the dependencies installed. Docs MCP startup prefers `.venv/bin/uvx` when present, then falls back to `uvx` on `PATH`.

## Required Pipeline

The standard pipeline does not skip English explanations or final Chinese translation.

1. Extract English source, audit PDF image objects, and rescue image/code content.
2. Freeze English source.
3. Classify and normalize required `domain` and non-empty `services` metadata.
4. Generate MCP-grounded English explanations for every qid.
5. Review every `needs_review` qid.
6. Run cross-family arbitration for every reviewed `needs_review` qid.
7. Humans decide high-confidence answer corrections from the artifacts, then patch approved qids and synthesize final English explanations for patched qids.
8. Translate finalized English content to Simplified Chinese.
9. Merge, clean CJK whitespace, apply targeted user-feedback fixes if needed, and validate final JSON.

## Stability Matrix

| Script | Stability | Notes |
|---|---|---|
| `common.py` | high | Shared atomic writes, transient retry, MCP adapters, citation URL contract, path helpers. Copy with every API script. |
| `extract.py` | low | Adapt delimiter, answer marker, option parsing, comments/votes, source noise. English source only. |
| `audit_pdf_images.py` | low | Writes `image_object_audit.json`; tune page-to-question mapping and image candidate filters. |
| `extract_option_images.py` | low | Tune image filters and page-to-question mapping. |
| `patch_image_options.py` | low | Reads `image_option_patches.json`; no hardcoded qids. |
| `splice_stem_images.py` | low | Reads `stem_image_patches.json`; no hardcoded qids. |
| `classify_metadata.py` | medium | Uses `metadata_config.json` and `service_taxonomy.json`; writes `metadata/{id}.json`. |
| `normalize_services.py` | medium | Normalizes metadata artifacts and merged questions with project taxonomy. |
| `smoke_test.py` | high | Hard gate for configured Anthropic/OpenAI-compatible model families, tool calling, and docs MCP. Non-zero exit means do not start bulk API work. |
| `explain.py` | high | Generates English explanations only, with MCP citation contract. |
| `review.py` | high | Reviews `needs_review` explanations with MCP citation contract. |
| `cross_family_arbitration.py` | medium | OpenAI-compatible default adapter for different-family arbitration. |
| `patch_answers.py` | low | Reads human-approved `answer_patches.json`; preserves `original_correct_answer` and writes `answer_patch_audit.json`. |
| `synthesize.py` | high | Generates final English explanations for patched qids only. |
| `strip_broken_urls.py` | high | Emergency cleanup before translation; does not silently pass citation-less explanations. |
| `translate_final.py` | medium | Final Simplified Chinese translation after all English stages are complete. |
| `merge_final.py` | high | Final bilingual merge with validation. |
| `clean_cn_spaces.py` | high | Final Chinese whitespace cleanup. |
| `validate_final.py` | high | Read-only final gate; domain/services and explanation URLs are required by default. |

## Recommended Adaptation Order

1. Copy `scripts/` to `pipeline/`.
2. Create a project-root `.venv` and install `pipeline/requirements.txt`. Scripts default to `.venv/bin/uvx` for docs MCP, falling back to `uvx`.
3. Adapt `pipeline/extract.py` to the English PDF format.
4. Run extraction and audit. Resolve empty options, lost labels, malformed labels, image candidates, answer mismatches, and vote-distribution audit issues.
5. Run `pipeline/audit_pdf_images.py --pdf english.pdf` and resolve `image_object_audit.json` candidates.
6. Rescue image/code stems and options before any API text generation.
7. Define `metadata_config.json` and `service_taxonomy.json`.
8. Run metadata classification, normalization, merge, and audit. Every qid must have `domain` and non-empty normalized `services`.
9. Configure API env vars and run `python pipeline/smoke_test.py`; it must exit 0 before the bulk API session. Rerun it after changing model/relay/MCP configuration or when diagnosing suspected API/relay failures.
10. Run `pipeline/explain.py` samples, then bulk explanations.
11. Run `pipeline/review.py` on every flagged qid.
12. Run `python pipeline/cross_family_arbitration.py` for every reviewed `needs_review` qid.
13. Create human-approved `answer_patches.json`, run `python pipeline/patch_answers.py`, then `python pipeline/synthesize.py` for patched qids.
14. Run `python pipeline/translate_final.py` after all English artifacts pass preflight.
15. Run `python pipeline/merge_final.py`, `python pipeline/clean_cn_spaces.py`, and `python pipeline/validate_final.py`.
16. If spot checks find readability/citation defects, inspect the affected qids, make the smallest targeted project-local edit to the relevant artifact or final `questions.json`, then rerun the necessary downstream merge/cleanup step and validation.

## Citation Contract

`explain.py`, `review.py`, `cross_family_arbitration.py`, and `synthesize.py` enforce:

- non-empty `sources`
- allowlisted source hosts
- sources observed through MCP or inherited from verified prior passes
- at least one visible inline URL in markdown
- every inline URL appears in `sources`

Validation errors are fed back to the model in the same qid context for limited correction retries. Transient provider/relay errors retry patiently with resumable artifacts.

## Patch Data Files

`image_option_patches.json`:

```jsonc
{
  "123": {
    "A": "```json\n...\n```",
    "B": "..."
  }
}
```

`stem_image_patches.json`:

```jsonc
[
  {
    "qid": 123,
    "marker": "shown below:",
    "fence_lang": "json",
    "ocr_file": "q123.txt"
  }
]
```

`answer_patches.json`:

```jsonc
{
  "patches": {
    "123": ["A", "C"]
  },
  "ambiguous": {
    "124": "Short note explaining why the extracted answer was retained."
  }
}
```

Humans decide which answers to patch after reviewing explanation/review/arbitration artifacts. `patch_answers.py` writes ambiguity notes to `answer_patch_audit.json`; final `questions.json` keeps only `original_correct_answer` for patched answers and does not keep `answer_count`, `original_answer_count`, or ambiguous fields.

`metadata_config.json` / `service_taxonomy.json`:

```jsonc
{
  "domains": ["Domain 1", "Domain 2"],
  "label_style": "Use the shortest common unambiguous label.",
  "service_aliases": {"short or alternate label": "Canonical Label"},
  "parent_services": {"Feature Label": "Parent Label"},
  "drop_labels": ["Non-project label"],
  "strip_prefixes": ["VendorPrefix "]
}
```

## AWS Provider Notes

- Anthropic SDK relays normally require `ANTHROPIC_BASE_URL` without trailing `/v1`.
- OpenAI-compatible relays normally require `ARBITRATION_BASE_URL` with `/v1` if that is the relay prefix.
- Official documentation hosts default to `DOCS_HOST=docs.aws.amazon.com`; use `DOCS_HOSTS=host1,host2` for multi-host allowlists.
- `smoke_test.py` chooses Anthropic or OpenAI-compatible tool-calling tests based on configured env vars; if both families are configured, both are tested.
- Keep provider-specific SDK changes inside the relevant stage runner. Keep shared contracts in `common.py`.
