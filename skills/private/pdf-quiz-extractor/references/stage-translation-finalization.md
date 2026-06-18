# Stage: Final Translation and Finalization

Use this stage for Phase I-K.

## Goals

- Translate finalized English question/options/comments/explanations to Simplified Chinese exactly once.
- Preserve answer invariants, markdown structure, code fences, and authoritative citation URLs.
- Merge final bilingual JSON, clean CJK whitespace, apply targeted user-feedback fixes if needed, and validate delivery.

## Translation Inputs

Each qid must already have:

- `id`
- top-level `domain`
- top-level non-empty `services`
- `correct_answer`
- finalized `en.question`
- finalized `en.options`
- `en.comments` array, using `[]` when the source has no comments
- finalized English `explanation_md_en` in `explanations/{id}.json`
- for patched qids, synthesized English explanation with `synthesized_at` and `model_chosen_answer == correct_answer`
- no unresolved `needs_review`
- no `broken_sources`
- valid inline citation URLs in the English explanation

## Translation Contract

Translate only human-facing natural language. Preserve exactly:

- option keys
- `correct_answer` labels and order
- markdown headings, lists, tables, code fences, links, and inline code
- fenced code content unless the user explicitly asks to localize comments inside code
- URLs, ARNs, IAM actions, CLI commands, JSON/YAML keys, API names, service names, region names, product names, placeholders, and variables
- explanation citation URLs; the final URL set must match the English explanation URL set

AWS/product terminology may remain English where canonical. Short technical labels that are mostly service names, product names, integrations, or configuration nouns may legitimately remain mostly English while surrounding prose is translated.

## Translation Validation

Hard fail and retry the qid when:

- qid differs from source question id
- option key set changes
- translated question/options/explanation are empty
- code fence counts differ in question/options/explanation/comments
- explanation URL set differs from English explanation URL set
- comments length differs
- comment structural fields such as user/time/selected/highly_voted are removed or corrupted

Community-comment URL mismatch is a warning by default. Explanation citation URL set mismatch is a hard failure; URL order or duplicate-count differences are warnings.

## Merge and Cleanup

1. Run `strip_broken_urls.py` only as an emergency cleanup before translation. If it removes all citation URLs, regenerate or mark the qid for review instead of silently passing.
2. Run `translate_final.py`, writing `translations/{id}.json` for every qid.
3. Spot-check translated qids before merge, including multi-answer, code-heavy, image-rescued, patched/synthesized, long scenario, and multi-clause option qids.
4. Run `merge_final.py`; it must reject missing translations, missing explanations, unresolved review, broken sources, citation URL set failures, unsynthesized patched qids, missing `comments` arrays, and translation invariant failures.
5. Run `clean_cn_spaces.py` after merge. It must protect fenced code and inline code.
6. If the user reports readability, citation, or content defects during spot checks, inspect the affected qids and apply the smallest targeted project-local edit to the relevant artifact or final `questions.json`. Do not add a dedicated one-off repair script or hardcode qid-specific fixes in reusable scripts.
7. After any targeted feedback fix, rerun the necessary downstream step: rerun `merge_final.py` if an explanation or translation artifact changed; rerun `clean_cn_spaces.py` if Chinese text changed; always rerun `validate_final.py`.

## Delivery Spot Check

Spot-check random, code-heavy, translated, patched, ambiguous, and service-name-heavy qids. Confirm:

- bilingual question/options/explanations are coherent
- option punctuation/readability is acceptable
- domain/services metadata is plausible
- code fences render
- AWS official source links resolve
- explanation citation URL hosts are allowlisted official documentation hosts (`DOCS_HOSTS` or `DOCS_HOST`)
- no CJK-internal whitespace remains outside protected code spans
