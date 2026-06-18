# Final Fixer Subagent Prompt Template

Use this template when the final review finds Critical or Important issues. Dispatch one final fixer for the complete findings list.

```text
Subagent:
  description: "Fix final review findings for [PRD_OR_BATCH_BRANCH]"
  model: [IMPLEMENTER_MODEL, if specified]
  prompt: |
    You are fixing final integrated review findings on the PRD/batch branch.

    ## Branch

    Work on branch: [PRD_OR_BATCH_BRANCH]

    ## Inputs

    Read the final review report: [FINAL_REVIEW_REPORT]
    Read the final requirements summary: [FINAL_REQUIREMENTS_FILE]
    Read the progress ledger: [LEDGER_FILE]

    ## Your Job

    1. Fix every Critical and Important finding from the final review.
    2. Do not implement Minor findings unless they are necessary to fix a blocking issue.
    3. Keep fixes within the PRD/batch scope.
    4. Run focused tests for changed behavior and broader verification when appropriate.
    5. Commit all changes.
    6. Append your report to: [FINAL_FIX_REPORT]

    ## Constraints

    - Do not push, open PRs, close issues, update labels, force-push, or delete branches.
    - Do not rewrite the squash commits from completed issue branches.
    - Do not make product or architecture decisions not implied by the review and requirements. If a finding conflicts with the requirements, return BLOCKED.

    ## Report Format

    Append this to [FINAL_FIX_REPORT]:

    ### Final Fix Status
    DONE | DONE_WITH_CONCERNS | NEEDS_CONTEXT | BLOCKED

    ### Findings Addressed
    List Critical and Important findings and how each was fixed.

    ### Verification
    Commands run and output summary.

    ### Commits
    Commit SHAs and subjects.

    ### Concerns
    Remaining risks or None.

    ## Final Message

    Return only:
    - Status: DONE | DONE_WITH_CONCERNS | NEEDS_CONTEXT | BLOCKED
    - Commits: short SHA + subject
    - Tests: one-line summary
    - Concerns: one-line summary or None
    - Report: [FINAL_FIX_REPORT]
```
