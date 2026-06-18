---
name: pdf-quiz-extractor
disable-model-invocation: true
description: Convert AWS certification PDF question banks into bilingual JSON datasets with metadata, verified explanations, citations, translation, and validation.
---

# PDF Quiz Extractor

Coordinator skill for turning an English-source AWS certification question PDF into a final bilingual study-app JSON dataset.

This file is intentionally thin. Load the stage reference that matches the work being performed.

## Use When

- The user wants to convert an AWS PDF quiz, exam, MCQ question bank, or certification practice PDF into JSON.
- The user wants English exam questions translated to Simplified Chinese for a study app.
- The user wants explanations, answer verification, official-source citations, image/code rescue, metadata, or final JSON cleanup.
- The user has structured English question data but wants metadata, explanations, verification, translation, or finalization.

## Hard Rules

- Treat the English PDF or English structured source as the only source of truth. Do not extract, align, or merge a Chinese PDF unless the user explicitly requests a separate comparison task.
- Read `references/final-json-schema.md` before designing or mutating `questions.json`.
- The standard output is bilingual and complete: top-level `domain` and non-empty `services`, English explanations, final Simplified Chinese translations, and final validation are required.
- Do not skip English explanations or final Chinese translation in the standard pipeline.
- Use one artifact per qid for long-running stages, atomic writes, and resume-by-file-existence.
- Stop and confirm before major API-spend stages: metadata classification, bulk explanations, review/arbitration, synthesis, and final Chinese translation.
- Generate Chinese only after source freeze, metadata classification, answer correction, and final English synthesis are complete.
- Ground explanations, reviews, and arbitration with AWS documentation MCP where possible. Enforce non-empty sources, AWS official documentation host allowlist, seen/inherited URL validation, and visible inline citation URLs in finalized markdown.
- Treat relay 429/5xx/529, timeout/unavailable errors, auth exhaustion, and provider misrouting signatures as transient in bulk API stages. Prefer patient resumable retries over writing mass failures.
- Use at least one different model family for arbitration for reviewed `needs_review` questions.
- Patch only high-confidence convergent answer corrections. Preserve answer provenance and annotate ambiguous or lower-confidence disputes.
- Normalize metadata services against the project-local taxonomy before merge/audit. Use minimal unambiguous service labels.
- Keep formatted content as markdown. Do not introduce HTML or separate code fields.
- Promote recurring project fixes into data-driven patch/config files or reusable validators. Do not hardcode qids or certificate-specific labels in reusable scripts.
- If a stage reveals an uncovered lesson or failure mode, append it to project-root `pdf-quiz-lessons.md` before continuing.

## Required Pipeline

1. Extraction and source freeze: English PDF -> initial JSON, option/stem image rescue, source audit.
2. Metadata: classify and normalize top-level `domain` and non-empty `services`.
3. English explanation and verification: AWS-docs-MCP-grounded explanations, conditional review, cross-family arbitration for reviewed `needs_review` qids, human-approved answer patching, and final English synthesis for patched qids.
4. Final Chinese translation: translate finalized English question/options/comments/explanations once.
5. Finalization: merge artifacts, clean CJK whitespace, apply targeted user-feedback fixes if needed, and run final validation.

Review/arbitration/synthesis are conditional but mandatory once triggered: any `needs_review` question must be reviewed; every reviewed `needs_review` question requires cross-family arbitration; patched answers require synthesis.

## Reference Router

- Schema and field semantics: `references/final-json-schema.md`
- Pipeline entrypoint and stage order: `references/execution-workflow.md`
- Extraction, image rescue, and source freeze: `references/stage-extraction.md`
- Metadata taxonomy and normalization: `references/stage-metadata.md`
- MCP citation, explanation, review, arbitration, and synthesis: `references/stage-explanation-review.md`
- Final Chinese translation, merge, cleanup, targeted feedback fixes, and validation: `references/stage-translation-finalization.md`
- Relay/proxy smoke tests and quirks: `references/relay-quirks.md`
- Script purposes and adaptation order: `scripts/README.md`

## Delivery Gates

- `questions.json` is valid JSON and has the expected qid count.
- English source questions/options/comments are frozen before API generation.
- PDF image-object audit is complete and captured in `image_object_audit.json` before source freeze.
- `domain` and non-empty taxonomy-normalized `services` are populated for every qid.
- English explanations have non-empty AWS official sources and visible inline citation URLs that pass host allowlist and seen/inherited URL checks.
- Triggered review/arbitration/synthesis stages are complete before translation.
- Patched questions preserve `original_correct_answer` and have synthesized final English explanations before translation.
- `translations/{id}.json` exists for every qid and preserves option keys, code fences, explanation citation URL set, and comment structure.
- Final bilingual JSON has populated `en` and `zh` question/options/explanations, and both language blocks contain a `comments` array; use `[]` when the source has no comments.
- Final bilingual JSON does not retain workflow-only fields such as `answer_count`, `original_answer_count`, `ambiguous_after_review`, or `ambiguous_note`.
- No unresolved `broken_sources` remain.
- Final CJK whitespace cleanup and final validation pass.
- If uncovered lessons occurred, project-root `pdf-quiz-lessons.md` contains them.
