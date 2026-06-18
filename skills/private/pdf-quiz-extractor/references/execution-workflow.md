# Execution Workflow

Short router for the required AWS question-bank pipeline. Load the stage document before changing files or running that stage.

## Phase Order

1. Phase A-D: extraction, image rescue, and English source freeze. Read `stage-extraction.md`.
2. Phase E: domain/services metadata. Read `stage-metadata.md`.
3. Phase F-H: English explanations, review, arbitration, answer patching, and synthesis. Read `stage-explanation-review.md`.
4. Phase I-K: final Chinese translation, merge, cleanup, targeted feedback fixes, validation, and spot check. Read `stage-translation-finalization.md`.

## Required Gates

- Stop and confirm before each major API-spend stage: metadata classification, bulk explanations, review/arbitration, synthesis, and final Chinese translation.
- Run `python pipeline/smoke_test.py` once before a bulk API session; it must exit 0. Rerun it after changing model/relay/MCP configuration or when diagnosing suspected API/relay failures.
- Do not start metadata before English source freeze.
- Do not start explanations before metadata is complete and `domain`/`services` are merged.
- Do not start final Chinese translation before all answer corrections and English synthesis are complete.
- Do not merge final JSON while any explanation has `needs_review: true`, non-empty `broken_sources`, missing inline citation URLs, or failed citation contract.
- Do not deliver before final validation and spot checks pass.

## Lesson Log

If any stage hits an unexpected case not covered by the skill or references, append it to project-root `pdf-quiz-lessons.md` before continuing. Include phase/stage, symptom, affected qids/files, root cause, fix, verification result, and whether the skill/reference should be updated later.
