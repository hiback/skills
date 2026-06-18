# Stage: Metadata

Use this stage for Phase E.

## Goals

- Classify every finalized English AWS question into top-level `domain` and non-empty `services`.
- Normalize services against a project-local taxonomy before explanations.
- Merge only stable delivery fields back into `questions.json`.

## Contract

- Metadata is required in the standard pipeline.
- Every final question must have `domain` and non-empty `services`.
- Use source-provided AWS exam domain labels when the English source has them; otherwise infer the domain from frozen English question/options.
- Classify using `en.question` and `en.options` as primary evidence. Treat comments as weak signals only.
- Keep domains, AWS service aliases, parent-service folding, drop labels, and AWS-prefix stripping in JSON config files, not certificate-specific script branches.
- For `services`, prefer the shortest common unambiguous AWS service/product/topic label a study-app user would recognize. Omit repeated `AWS` prefixes unless needed for disambiguation or established product naming.

## Recommended Files

- `metadata_config.json`: AWS exam domains, certificate name, instructions, label style.
- `service_taxonomy.json`: AWS service aliases, parent-service folding, drop labels, AWS prefixes to strip.
- `metadata/{id}.json`: one classifier artifact per qid.
- `metadata_audit.json`: merge/audit report.

## Run Order

1. Confirm domain taxonomy and service label taxonomy.
2. Stop and confirm before bulk AI classification.
3. Run `classify_metadata.py`, writing one artifact per qid under `metadata/`.
4. Run `normalize_services.py` or equivalent taxonomy normalizer on artifacts and merged questions.
5. Merge top-level `domain` and `services` into `questions.json`.
6. Audit missing, empty, duplicate, translated, alias-like, feature-like, or low-confidence metadata before explanation generation.

## Merge Gate

- Every qid has a metadata artifact.
- Every artifact has non-empty `domain` and non-empty normalized `services`.
- No duplicate services remain after normalization.
- No configured forbidden prefixes remain.
- No raw model aliases, feature labels, or non-project labels remain unless explicitly allowed by taxonomy.
