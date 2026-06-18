# Stage: Extraction, Image Rescue, Source Freeze

Use this stage for Phase A-D.

## Goals

- Extract only the English AWS source PDF or English structured input.
- Produce stable English-first `questions.json` records.
- Rescue missing image/code/table/policy content before any API generation.
- Freeze the English source after audit.

## Source Reconnaissance

1. Scan the English PDF in text mode and identify question delimiter, option format, answer marker, comments/votes, source language, and image density.
2. Sample 10 questions manually in a PDF viewer. Include code blocks, JSON/YAML, IAM policies, diagrams, tables, and long scenarios.
3. Decide the answer source policy up front: official answer key, source marker, community vote, comments, or a combination. Community vote is review signal, not an automatic answer override.
4. If using a relay/proxy for later API stages, run `python pipeline/smoke_test.py` once before the bulk API session or after changing API/MCP configuration.

## Extraction Contract

- Adapt `scripts/extract.py` to the English PDF's delimiter, answer marker, multi-answer syntax, option boundaries, comments/votes, and noise patterns.
- Validate answers against the actual extracted option key set, not a fixed A-E assumption.
- Do not emit `answer_count`; the current answer count is derivable from `len(correct_answer)`. Treat choose/select wording counts as audit signals only.
- Default template supports A-F. If a PDF uses letters outside A-F, extend extractor regexes and model tool schemas together.
- Do not extract `most_voted`; it duplicates vote distribution. Keep `vote_distribution` as the community signal.
- `vote_distribution` keys:
  - Answer-combination keys are uppercase strings such as `A`, `CE`, or `BDF`.
  - Accept input spellings `other` or `others` in any case, but output exactly `Other`.
  - Unknown vote labels are preserved as-is and recorded in audit for user decision.
- Never silently fix source text. Suspected lost option labels, duplicate labels, or missing options are audit items unless a user-approved patch/config enables recovery.

## Extraction Audit

Write `extraction_audit.json` with counts and sample qids for text-mode extraction issues:

- missing question text
- missing correct answer
- empty option values
- no options or too few options
- malformed, duplicate, or missing option labels
- possible lost A option candidates
- same-stem duplicate candidates
- duplicate ids
- answer letters not in actual option keys
- suspicious answer counts from choose/select wording, reported as `selection_count_mismatch`
- image-content candidates from text heuristics
- invalid vote keys
- vote keys not in option set
- suspicious vote percentage sums
- top vote answer disagreeing with extracted `correct_answer`
- multi-answer vote key length mismatches

PDF image-object candidates are captured separately in `image_object_audit.json` by `audit_pdf_images.py`. Do not treat a clean text heuristic audit as image-free until that artifact exists.

## Image Rescue

Text heuristics are not enough. Always run a PDF image-object audit before source freeze, even when phrase heuristics find zero candidates. The bundled template is `audit_pdf_images.py`, which writes `image_object_audit.json`; replace it with `pdfimages`, PyMuPDF, pdfplumber, or equivalent only if the replacement writes an auditable artifact.

For stems, flag phrases such as:

- `shown below`
- `shown above`
- `the following code`
- `the following JSON`
- `the following YAML`
- `the following template`
- `the following policy`
- `diagram`
- `figure`
- `refer to the image`

For options, flag:

- empty option values
- all-code screenshots
- suspiciously short options with shared prefixes
- corrupted code-like text
- screenshots visible in a PDF viewer but missing from text extraction

OCR or manually transcribe rescued content. Save OCR text beside extracted images for audit. Splice rescued content into English `question` or `options[*]` as markdown code fences.

## Source Freeze Gate

Before metadata or any API text generation:

- Every qid has stable `id`.
- `en.question` is non-empty.
- `en.options` has the expected source-dependent key set and no empty values.
- `correct_answer` is a subset of `en.options.keys()`.
- `correct_answer` is non-empty unless the user explicitly accepts source-answer recovery as a separate manual task.
- Image-object audit (`image_object_audit.json`) and empty-option audit are complete or documented as not applicable.
- At least 10 qids are manually spot-checked, including single-answer, multi-answer, code-heavy, image-rescued, and long scenario questions.
