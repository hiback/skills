# Final JSON Schema

Read this before designing extraction or mutating `questions.json` for an AWS certification question bank.

The standard pipeline has two shapes: an English-first intermediate shape used through answer review/synthesis, and the final bilingual delivery shape produced after final Chinese translation and merge.

## Intermediate Shape Before Final Translation

From extraction through final English synthesis, `questions.json` is English-first and may not contain `zh`:

```jsonc
{
  "id": 123,
  "correct_answer": ["C", "E"],
  "vote_distribution": {"CE": 48, "DE": 48, "Other": 4},
  "domain": "Domain 1",
  "services": ["S3", "Lambda"],
  "en": {
    "question": "...markdown text, may contain ```code``` fences...",
    "options": {"A": "...", "B": "...", "C": "...", "D": "...", "E": "..."},
    "comments": []
  }
}
```

`domain` and non-empty `services` are required after the metadata stage and before explanation generation.

Answer corrections add a standardized provenance field. This field is part of the final delivery schema, not workflow-only audit data:

```json
{
  "original_correct_answer": ["D", "E"]
}
```

Use `original_correct_answer` whenever `correct_answer` changes from the extracted/source answer. The original answer count is derivable from `len(original_correct_answer)`, so the final schema does not keep `original_answer_count`. Omit `original_correct_answer` for questions whose answer was not corrected. Ambiguity notes belong in review/arbitration artifacts or answer patch audit files, not final `questions.json`.

## Final Bilingual Shape

After `merge_final.py`, each object has both language blocks, metadata, and final explanations:

```jsonc
{
  "id": 123,
  "correct_answer": ["C", "E"],
  "original_correct_answer": ["D", "E"],
  "vote_distribution": {"DE": 48, "CE": 48, "Other": 4},
  "domain": "Domain 1",
  "services": ["S3", "Lambda"],
  "en": {
    "question": "...",
    "options": {"A": "...", "B": "..."},
    "comments": [],
    "explanation_md": "...full markdown explanation with visible citation URLs..."
  },
  "zh": {
    "question": "...",
    "options": {"A": "...", "B": "..."},
    "comments": [],
    "explanation_md": "...translated markdown explanation preserving citation URLs..."
  }
}
```

## Top-Level Fields

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | Stable across pipeline runs. Use the source PDF question number when possible. |
| `correct_answer` | array of letters | yes | Authoritative answer. Set by extraction and updated only by high-confidence answer patches. |
| `original_correct_answer` | array of letters | only if answer changed | Preserves extracted/source answer before `patch_answers.py` overwrote it. |
| `vote_distribution` | object | optional | Community vote percentages. Keys are answer-combination strings such as `A` or `CE`, plus `Other` for other/others. Unknown labels may be preserved only with extraction audit notes. |
| `domain` | string | yes | Project/exam domain from source or metadata classifier. |
| `services` | array of strings | yes | Non-empty project-taxonomy-normalized English service/product/topic labels. Prefer minimal unambiguous labels. |

## Per-Language Fields

| Field | Type | Required In Final | Notes |
|---|---|---|---|
| `question` | string markdown | yes | Question stem. May contain fenced code for embedded snippets/figures. |
| `options` | object `{letter: string}` | yes | Usually 4-6 keys. Values are markdown and may contain fenced code. |
| `comments` | array | yes | Original community discussion. The field must exist; use `[]` when absent. Chinese comments must preserve lightweight structure and translate human-facing content. |
| `explanation_md` | string markdown | yes | Generated in English by `explain.py`/`synthesize.py`, translated once by `translate_final.py`. Must preserve visible citation URLs. |

## Workflow Fields Not Kept In Final JSON

These may exist in intermediate artifacts or legacy extracted files but are dropped by final merge/cleanup:

- `most_voted`
- `answer_count`, `original_answer_count`
- `ambiguous_after_review`, `ambiguous_note`
- `needs_review`, `needs_review_reasons`, `needs_review_note`
- `model_chosen_answer`
- `sources`, `broken_sources`, `fetched_urls`, `search_returned_urls`, `inherited_urls`
- `structure_metrics`
- metadata classifier audit fields such as `metadata_confidence`, `metadata_evidence`, `metadata_model`, and `metadata_generated_at`
- model names and timestamps such as `stage1_model`, `reviewer_model`, `arbitration_model`, `synthesis_model`, `translation_model`, `generated_at`, `translated_at`, `synthesized_at`
- provider usage/cost telemetry

## Markdown Conventions

Use markdown freely in `question`, `options[*]`, and `explanation_md`.

Always use language-tagged fences for code/JSON/YAML/etc:

````markdown
```json
{"key": "value"}
```
````

Do not use HTML `<pre>` or separate code fields. Citation links use normal markdown:

```markdown
[Doc title](https://docs.aws.amazon.com/...)
```

The final Chinese translation must preserve headings, lists, code fences, tables, and citation URLs.

## ID Stability Across Pipeline Runs

`id` must be stable. If you re-run extraction with a tweaked extractor and the same PDF, the same question must get the same id.

- Do not auto-increment based on extraction order if extraction order can change.
- Do use the question number printed in the PDF when possible.
- If the PDF has no numbers, derive id from a hash of normalized question text with a reproducible seed.

## Schema-Level Invariants

1. `correct_answer` is a non-empty subset of `en.options.keys()`.
2. `domain` is non-empty.
3. `services` is non-empty and has no duplicates.
4. If `vote_distribution` exists, `Other` is the canonical key for other/others.
5. In final bilingual delivery, `set(en.options.keys()) == set(zh.options.keys())`.
6. In final bilingual delivery, both `en.explanation_md` and `zh.explanation_md` are non-empty.
7. In final bilingual delivery, `en.explanation_md` contains at least one visible official citation URL.
8. In final bilingual delivery, citation URL sets in `en.explanation_md` and `zh.explanation_md` match exactly. URL order or duplicate-count differences are audit warnings, not hard failures.
9. In final bilingual delivery, explanation citation URL hosts are allowlisted AWS official documentation hosts.
10. Code fences are preserved across `en` and `zh` question/options/comments/explanations.
11. Both `en.comments` and `zh.comments` exist. If comments are present, comment counts match, original structural fields such as `user`, `time`, `selected`, and `highly_voted` are preserved, and each translated comment has `content`.
12. `original_correct_answer != correct_answer` if both are present.
