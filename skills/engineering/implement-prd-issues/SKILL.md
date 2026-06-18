---
name: implement-prd-issues
description: Implement a PRD file or explicit issue list by coordinating isolated subagents through issue branches, review gates, and squash merges.
disable-model-invocation: true
---

# Implement PRD Issues

Implement a PRD file or an explicit issue list by acting as a controller. Do not implement code directly. Coordinate fresh subagents, keep every issue isolated on its own branch, and merge only reviewed work into the PRD/batch branch.

## Inputs

Two input modes exist:

1. **PRD mode**: the argument is a PRD markdown file path. Resolve child issues from that PRD according to the configured tracker. Run a final PRD review after all issues merge.
2. **Issue-list mode**: the arguments are issue references. Treat one issue as a one-item list. Do not parse child issues from issue bodies. Run a final batch review only when there is more than one issue.

PRD mode resolves child issues as follows:

- GitHub tracker: explicit refs inside the PRD: `#123`, `owner/repo#123`, `https://github.com/owner/repo/issues/123`
- GitLab tracker: explicit refs inside the PRD: `#123`, `123`, `https://gitlab.com/group/project/-/issues/123`, or self-hosted GitLab issue URLs
- Local markdown tracker: when the PRD is `.scratch/<feature-slug>/PRD.md`, use readable markdown files matching `.scratch/<feature-slug>/issues/*.md`, sorted by filename.

Do not fuzzy-search, auto-scan other directories, or infer children from comments.

## Subagent Requirement

This skill requires subagent dispatch. If the current agent runner cannot dispatch subagents, stop before changing files. Do not downgrade to single-agent implementation.

## Model Selection

The user may specify separate models for implementation and review in natural language. If unspecified, omit model selection and let the runner use its default.

- Implementer subagents use the implementer model.
- Issue fixer subagents use the implementer model.
- Issue reviewer subagents use the reviewer model.
- Final reviewer subagents use the reviewer model.
- Final fixer subagents use the implementer model by default. If the fix requires architecture-level judgment, use the reviewer model or a stronger model and record why in the ledger.

## Files

Use git-internal storage so the workflow survives compaction without polluting the working tree:

```bash
$(git rev-parse --git-path implement-prd-issues)/progress.md
$(git rev-parse --git-path implement-prd-issues)/issue-<id>-brief.md
$(git rev-parse --git-path implement-prd-issues)/issue-<id>-report.md
$(git rev-parse --git-path implement-prd-issues)/review-<base7>..<head7>.diff
$(git rev-parse --git-path implement-prd-issues)/final-requirements.md
$(git rev-parse --git-path implement-prd-issues)/final-review-report.md
$(git rev-parse --git-path implement-prd-issues)/final-fix-report.md
$(git rev-parse --git-path implement-prd-issues)/handoff-issue-<id>.md
```

Use the scripts in `scripts/`:

- `scripts/issue-brief --tracker github|gitlab|local ISSUE_REF [OUTFILE]` writes a single issue brief file.
- `scripts/review-package BASE HEAD [OUTFILE]` writes a commit list, stat, and diff package for review.

Run these scripts from inside the target git repository. They use `git rev-parse --git-path implement-prd-issues` to write repo-local state.

Handoff files are optional. Create one only for `BLOCKED`, unresolved `NEEDS_CONTEXT`, manual takeover, pause, or session transfer.

## Startup

1. Resolve the issue tracker from `docs/agents/issue-tracker.md`. Treat this file as the source of truth, like the `to-issues` skill does. If it is missing, stop before changing files and ask the user to run `/setup-matt-pocock-skills` or provide the tracker for this run. You may inspect `git remote -v` and `.scratch/` only to recommend GitHub, GitLab, or local markdown, but do not proceed until the user confirms.
2. Require a git repository and a clean working tree. If dirty, stop and ask the user to commit, stash, or clean manually. Do not stash automatically.
3. Read `$(git rev-parse --git-path implement-prd-issues)/progress.md` if it exists. If it matches this input, resume from the first incomplete issue. If it does not match, stop and ask whether to start fresh or resume the recorded batch. If it matches but the current branch is neither the recorded PRD/batch branch nor the recorded in-progress issue branch, stop and ask the user to check out the correct branch.
4. Resolve input mode and issue list. In PRD mode with local markdown, derive the issue list from `.scratch/<feature-slug>/issues/*.md` based on the PRD path and require at least one readable markdown file. In PRD mode with other trackers, require at least one explicit child issue reference. Issue-list mode uses exactly the provided issue refs.
5. Infer branch type: `feat`, `fix`, `refactor`, `chore`, or `docs`. Use labels and titles when available. If still unclear, ask once; final fallback is `feat`.
6. Derive a slug from the PRD title, an explicit user phrase, the single issue title, or `batch-YYYY-MM-DD`. Slug must be lowercase, hyphen-separated, and short.
7. Create or resume the PRD/batch branch from the current clean branch. If current branch is not a normal base branch such as `main`, `master`, `dev`, or `develop`, ask for confirmation before branching.
8. Start the ledger with input identity, issue list, base branch, base SHA, PRD/batch branch, branch type, slug, models if specified, and timestamp. For local markdown PRD mode, record the `.scratch/<feature-slug>/issues/*.md` glob result as the issue list.

Branch type inference:

- `fix`: bug labels or titles containing fix, bug, broken, failing, regression
- `refactor`: titles containing refactor, restructure, cleanup, architecture, with no user-visible behavior change
- `docs`: docs-only work, documentation, README, guides
- `chore`: dependency, config, tooling, maintenance, build housekeeping
- `feat`: enhancement labels, add, create, support, enable, feature, or fallback

Issue tracker resolution:

- If `docs/agents/issue-tracker.md` says GitHub, use `github` mode. Fetch issue briefs with `scripts/issue-brief --tracker github ...`. Issue refs must be GitHub refs such as `#123`, `owner/repo#123`, bare issue numbers, or GitHub issue URLs.
- If it says GitLab, use `gitlab` mode. Fetch issue briefs with `scripts/issue-brief --tracker gitlab ...`. Issue refs must be GitLab issue numbers, `#123`, or GitLab issue URLs. The repo is inferred from the current clone, following the GitLab setup convention.
- If it says local markdown, use `local` mode. Fetch issue briefs with `scripts/issue-brief --tracker local ...`. Issue refs must be readable markdown paths.
- If it says Other, Linear, Jira, or another custom tracker, follow the freeform workflow in `docs/agents/issue-tracker.md`. Write the fetched issue body and comments into the same git-internal brief format manually. If the document does not say how to fetch an issue and comments, stop and ask for exact commands before proceeding. Do not guess.
- If the configured tracker and the provided refs conflict, stop and ask. Do not infer tracker from the ref shape.
- Do not close issues, update labels, or comment during normal completion even if the tracker supports it.

Branch names:

```text
PRD/batch branch: <type>/<slug>
Issue branch:     <type>/<slug>-issue-<issue-id>
```

If a target branch already exists and is not recorded in the matching ledger, stop and ask before reusing it.

## Per-Issue Loop

Before implementation, determine dependency order:

- Use explicit `Blocked by` sections or dependency references in issue bodies when present.
- If a referenced blocker is outside this batch, stop and ask whether to add it, remove the dependency, or postpone this batch.
- If no dependencies are stated, preserve the PRD order, the local markdown filename order, or the user-provided issue-list order.
- If dependencies cycle or conflict with the given order, stop and ask.

For each issue in dependency order:

1. Start from the clean PRD/batch branch. Create or resume the issue branch.
2. Generate an issue brief with `scripts/issue-brief --tracker <configured-tracker>` or the custom tracker workflow. In PRD mode, append only PRD global constraints and background that bind this issue. Do not paste the entire PRD into the issue brief.
3. Record the issue branch base SHA before dispatching implementation.
4. Dispatch an implementer subagent using `implementer-prompt.md`. The implementer must write the detailed report file and commit all changes. Uncommitted work cannot enter review.
5. Handle implementer status:
   - `DONE`: verify the worktree is clean and commits exist, then review.
   - `DONE_WITH_CONCERNS`: read concerns. Resolve correctness or scope concerns before review; record observations in the ledger.
   - `NEEDS_CONTEXT`: provide missing context and re-dispatch when possible. If you cannot, stop and write a handoff.
   - `BLOCKED`: assess whether more context, a stronger model, or a smaller task can resolve it. If not, stop the whole batch and write a handoff. Do not skip to the next issue.
6. Create a review package from the recorded base SHA to `HEAD`.
7. Dispatch an issue reviewer subagent using `issue-reviewer-prompt.md`. The reviewer checks issue scope, not whole-PRD completion.
8. If review finds Critical or Important issues, dispatch a fixer subagent on the same issue branch. The fixer commits, appends test results to the report, and you re-run review. Repeat until Critical and Important are clear.
9. Record Minor findings in the ledger. They do not block the issue merge unless they indicate hidden Critical or Important risk.
10. Squash merge the reviewed issue branch into the PRD/batch branch. Keep the local issue branch. Do not push.
11. Append completion to the ledger, including issue ref, issue branch, issue branch base/head, squash commit, tests, and review verdict.

Squash commit message:

```text
GitHub/GitLab: <type>: <issue title> (#<issue-id>)
Local markdown:<type>: <issue title>
Other tracker: <type>: <issue title>
```

Commit body:

```text
Implements #<issue-id>
Reviewed in issue branch: <issue-branch>

Tests:
- <command>
```

For local markdown or custom trackers, replace `#<issue-id>` with the issue path or configured tracker reference.

## Final Review

Run final review when input mode is PRD mode or issue-list mode has more than one issue. Do not run final review for a single issue unless the user explicitly asks.

1. Stay on the clean PRD/batch branch.
2. Write `final-requirements.md` in the git-internal directory. For PRD mode, include the PRD path and the explicit issue list. For multi-issue mode, include the issue list. Include the ledger path, branch names, and known global constraints.
3. Generate a review package from the batch base SHA to `HEAD`.
4. Dispatch a final reviewer subagent using `final-reviewer-prompt.md`, giving it `final-requirements.md`, `progress.md`, the review package, and `final-review-report.md` as its report path.
5. If Critical or Important findings exist, dispatch one final fixer subagent using `final-fixer-prompt.md` to fix all of them on the PRD/batch branch. The fixer commits and reports tests.
6. Re-run final review. Repeat until Critical and Important findings are clear.
7. Record Minor findings in the ledger and final response.

## Issue Tracker Side Effects

Default side effects are local only. Do not close issues, update labels, push branches, create remote branches, or open PRs.

Only comment on an issue when needed to preserve important context:

- `BLOCKED` or unresolved `NEEDS_CONTEXT`
- review conflicts with issue or PRD requirements
- `Cannot verify from diff` requires human confirmation
- implementation intentionally deviates from the issue or PRD
- final review leaves important unresolved risk

For custom trackers, follow `docs/agents/issue-tracker.md` for comments only when one of those cases applies.

## Completion

Finish with a concise report:

- base branch and PRD/batch branch
- issues merged and squash commits
- final review verdict, if run
- tests and checks reported by implementers/fixers
- Minor findings and unresolved risks
- note that no push or PR was created

## Red Flags

Never:

- implement directly in the controller session
- run without subagents
- start from a dirty working tree
- switch branches with dirty changes
- dispatch implementation subagents in parallel
- review uncommitted work
- skip issue review
- squash merge an issue branch with unresolved Critical or Important findings
- skip final review when PRD mode or multi-issue mode requires it
- push, open PRs, close issues, or delete branches
- continue to the next issue after an unresolved blocker
