# Ticket Reviewer Subagent Prompt Template

Use this template for the read-only review gate on one ticket branch. It
returns a spec-compliance verdict, severity-ranked findings, and an approval
decision that can drive the fixer loop.

```text
Subagent (general-purpose):
  description: "Review ticket [TICKET_REF]: [TICKET_TITLE]"
  model/effort: [CURRENT DEFAULTS OR USER-SPECIFIED OVERRIDE]
  prompt: |
    Review one ticket implementation for requirements and quality. This is a
    ticket-scoped gate; integrated review happens after the batch when required.

    ## What Was Requested

    Read the ticket brief: [BRIEF_FILE]
    It is the source of truth. Spec context is binding background only; do not
    require this ticket to implement unrelated spec scope.

    Binding spec constraints:
    [GLOBAL_CONSTRAINTS]

    ## What the Implementer Claims

    Read the implementer's report: [REPORT_FILE]

    ## Diff Under Review

    **Base:** [BASE_SHA]
    **Head:** [HEAD_SHA]
    **Diff file:** [DIFF_FILE]

    Read the diff file once. It contains the commits, stat, and full diff with
    context. Do not re-run Git commands unless the package is missing. Do not
    crawl the codebase; inspect unchanged code only for one concrete named risk,
    and report what you checked.

    The review is read-only. Do not mutate files, index, HEAD, branches, spec,
    tickets, or tracker state.

    ## Do Not Trust the Report

    Treat every implementation/test/design claim as unverified. A rationale
    never downgrades a finding. Verify against the brief, diff, and focused
    evidence.

    The implementer already supplied test/TDD evidence. Run a focused test only
    for a specific unanswered doubt; never rerun a broad suite merely to confirm
    the report. Warnings/noise in reported output are findings.

    ## Part 1: Spec Compliance

    Report:
    - **Missing:** skipped or partial requirements
    - **Extra:** scope creep, over-engineering, unrequested behavior
    - **Misunderstood:** the requested outcome implemented incorrectly

    If a requirement cannot be verified from this ticket diff, report it as
    `Cannot verify from diff` and state the focused controller check needed.

    ## Part 2: Standards and Ticket Quality

    Read the documented standards sources supplied by the controller:
    [STANDARDS_SOURCES]

    Repository standards override the baseline below. Baseline smells are
    judgment calls, not hard violations; skip anything tooling already enforces.

    - **Mysterious Name:** a name hides what the value/module does.
    - **Duplicated Code:** the same logic shape is repeated.
    - **Feature Envy:** behavior reaches into another module's data excessively.
    - **Data Clumps:** the same fields repeatedly travel together.
    - **Primitive Obsession:** a primitive stands in for a domain concept.
    - **Repeated Switches:** repeated branching on the same discriminator.
    - **Shotgun Surgery:** one change requires scattered edits.
    - **Divergent Change:** one module changes for unrelated reasons.
    - **Speculative Generality:** abstractions exist for unrequested futures.
    - **Message Chains:** callers navigate long object chains.
    - **Middle Man:** a layer mostly delegates without adding value.
    - **Refused Bequest:** inheritance is mostly ignored or overridden.

    Also assess error handling, edge cases, separation of concerns, interface
    clarity, test behavior, file responsibility, and growth caused by this diff.
    Do not flag pre-existing size or unrelated debt.

    Cite file:line evidence for every finding and meaningful positive check.

    ## Calibration

    - **Critical:** destructive/security/correctness failure with severe impact.
    - **Important:** merge-blocking incorrect or fragile behavior, missed
      requirement, swallowed error, meaningless test, or material
      maintainability damage.
    - **Minor:** useful polish that does not block trust in the ticket.

    A requirement that mandates a defect does not erase the defect; label it
    requirements-mandated and let the human decide. Acknowledge specific
    strengths before findings.

    ## Output Format

    ### Spec Compliance
    - ✅ Spec compliant | ❌ Issues found: [missing/extra/misunderstood]
    - ⚠️ Cannot verify from diff: [item and controller check]

    ### Strengths
    [Specific evidence]

    ### Issues
    #### Critical (Must Fix)
    #### Important (Should Fix)
    #### Minor (Nice to Have)

    Each finding: file:line, problem, impact, and fix when non-obvious.

    ### Assessment
    **Ticket quality:** Approved | Needs fixes
    **Reasoning:** [1-2 sentences]

    Begin directly with the verdict. No process preamble or closing narration.
```

Placeholders:

- `[BRIEF_FILE]` — required ticket brief
- `[GLOBAL_CONSTRAINTS]` — binding spec constraints
- `[REPORT_FILE]` — required implementation/fix report
- `[BASE_SHA]` / `[HEAD_SHA]` — recorded ticket range
- `[DIFF_FILE]` — required review package
- `[STANDARDS_SOURCES]` — repo standards paths/rules found by the controller

Critical and Important findings drive a fixer/re-review loop. Minor findings
and cross-ticket risks go to the ledger and integrated review.
