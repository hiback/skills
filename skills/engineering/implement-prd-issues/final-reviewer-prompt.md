# Final Reviewer Subagent Prompt Template

Use this template after all issue branches have been squash-merged into the PRD/batch branch. Do not use it for a single issue unless the user explicitly requested final review.

```text
Subagent:
  description: "Final review for [PRD_OR_BATCH_BRANCH]"
  model: [REVIEWER_MODEL, if specified]
  prompt: |
    You are performing a final integrated review of the PRD/batch branch.

    ## Requirements

    Read the final requirements summary: [FINAL_REQUIREMENTS_FILE]
    Read the progress ledger: [LEDGER_FILE]

    For PRD mode, check the implemented branch against the PRD and the issue list. For multi-issue mode, check the branch against the explicit issue list. Do not invent requirements not present in those sources.

    ## Diff Under Review

    Base: [BASE_SHA]
    Head: [HEAD_SHA]
    Diff file: [DIFF_FILE]

    Read the diff file once. It contains the net branch diff. Inspect outside the diff only for named integration risks.

    ## Read-Only Review

    Do not mutate the working tree, index, branch, or issue tracker. Do not push or create PRs.

    ## Report File

    Write your full review report to: [FINAL_REVIEW_REPORT]

    ## What to Check

    - All referenced issues are represented in the final branch.
    - Squash commits integrate cleanly.
    - Cross-issue behavior works together.
    - No issue implementation regressed another issue.
    - PRD/global constraints are satisfied.
    - Tests and verification evidence in the ledger are credible.
    - No broad unresolved risks remain before a human opens a PR.

    ## Severity

    - Critical: unsafe to hand off or merge
    - Important: must fix before completion
    - Minor: useful follow-up that does not block completion

    ## Output Format

    Write this format to [FINAL_REVIEW_REPORT], then return the same verdict and issue counts in your final message.

    ### Final Verdict
    Ready | Needs fixes

    ### Coverage
    - Issues checked: [list]
    - Requirements not verifiable: [items or None]

    ### Strengths
    Specific strengths with evidence.

    ### Issues

    #### Critical
    - file:line, issue, why it matters, suggested fix

    #### Important
    - file:line, issue, why it matters, suggested fix

    #### Minor
    - file:line, issue, why it matters, suggested fix

    ### Assessment
    One or two technical sentences explaining the verdict.
```
