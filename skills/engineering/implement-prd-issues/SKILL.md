---
name: implement-prd-issues
description: Implement a PRD parent issue or explicit issue list by coordinating fresh subagents through issue branches, review gates, final integrated review when needed, and local squash merges.
disable-model-invocation: true
---

# Implement PRD Issues

Execute a PRD or issue list by dispatching a fresh implementer subagent per
issue, an issue review after each, and a broad integrated review when required.

**Core principle:** Fresh subagent per issue + issue review (spec + quality) +
local squash merge + integrated review when needed.

**Narration:** between tool calls, narrate at most one short line — the ledger
and the tool results carry the record.

**Continuous execution:** Do not pause to check in with your human partner
between issues. Execute all issues without stopping. The only reasons to stop
are: BLOCKED status you cannot resolve, ambiguity that genuinely prevents
progress, branch or tracker safety conflicts, or all work complete.

## Inputs

Two input modes exist:

1. **PRD mode:** the argument is a PRD file or parent issue. Resolve child
   issues from that PRD, then run integrated review after all child issues merge.
2. **Issue-list mode:** the arguments are issue references. Treat one issue as a
   one-item list. Do not parse child issues from issue bodies. Run integrated
   review only when there is more than one issue unless the user explicitly asks.

If the user provides both a PRD and issue refs, ask whether to restrict execution
to the explicit issue list or run the full PRD child discovery.

## Tracker Resolution

Use `docs/agents/issue-tracker.md` as the tracker source of truth when it
exists. If it is missing:

- local markdown is allowed when the input is clearly a local PRD or issue path
- GitHub, GitLab, and custom trackers require user confirmation or configuration
- `git remote -v` may inform a recommendation, but do not execute from inference

Supported built-in trackers:

- **GitHub:** use `gh` according to `docs/agents/issue-tracker.md`
- **GitLab:** use `glab` according to `docs/agents/issue-tracker.md`
- **Local markdown:** use files under `.scratch/`

For custom trackers, continue only when `docs/agents/issue-tracker.md` gives
explicit commands for reading and listing issues. Otherwise stop and ask.

Default side effects are local only. Do not push, open PRs, comment on, close,
label, or otherwise modify tracker issues unless explicitly requested.

## PRD Child Discovery

In PRD mode, resolve child issues as follows:

- **Local markdown:** when the PRD is `.scratch/<feature-slug>/PRD.md`, use
  readable markdown files matching `.scratch/<feature-slug>/issues/*.md`, sorted
  by filename.
- **GitHub/GitLab:** treat the PRD issue as the parent. Use the tracker command
  line to list/search issues whose body has a `## Parent` reference to the PRD.
  Comments are not part of default discovery.
- **Fallback:** explicit child refs in the PRD body may be used if parent lookup
  is unavailable or empty. Do not treat ordinary numbers as issue refs.

Do not fuzzy-search, auto-scan unrelated directories, or infer children from
comments. If discovery is empty or ambiguous, ask for an explicit issue list.

## Startup

1. Require a git repository and a clean working tree/index before branch
   operations. Do not stash or discard changes automatically.
2. Read `$(git rev-parse --git-path ipi)/progress.md` if it exists. It is the
   active recovery ledger. Resume matching in-progress or blocked runs; do not
   re-dispatch issues already marked complete. If it describes different input,
   ask whether to resume the old run, archive it and start new, or stop.
3. Resolve input mode, tracker, PRD requirements source, issue list, and issue
   dependency order.
4. Scan once for conflicts: PRD constraints vs issue acceptance criteria, issue
   contradictions, dependency cycles, tracker/ref mismatches, and label risks
   such as blocked/waiting issues. Ask one batched question if conflicts exist.
   If the scan is clean, proceed without comment.
5. Infer branch type (`feat`, `fix`, `refactor`, `chore`, `docs`) from user
   input, PRD/issue title, and labels. Ask once if unclear; final fallback is
   `feat`.
6. Derive a short lowercase slug from user input, PRD title, single issue title,
   or `batch-YYYY-MM-DD`.
7. Create or resume the PRD/batch branch. If no matching ledger exists and the
   current branch is not an obvious base branch, ask whether it is the PRD/batch
   branch or the base for a new PRD/batch branch.
8. Start the ledger with status, input identity, tracker, issue list, dependency
   order, base branch/SHA, PRD/batch branch, branch type, slug, created
   transient files, and timestamp.

Branch names:

```text
PRD/batch branch:  <type>/<slug>
Issue branch:      <type>/<slug>-issue-<issue-id>
Final check branch:<type>/<slug>-final-check
```

If a target branch already exists and is not recorded in the matching ledger,
stop and ask before reusing it.

## Model and Effort

Use the current model and effort for implementers, issue fixers, issue reviewers,
and final fixers unless the user specifies role-specific overrides. The final
integrated reviewer uses the strongest available model/effort unless the user
specifies otherwise. If a subagent is blocked by reasoning limits, retry with a
stronger available model/effort and record why.

## Per-Issue Loop

For each issue in dependency order:

1. Start from the clean PRD/batch branch. Create or resume the issue branch.
2. Generate an issue brief with `scripts/issue-brief --tracker <tracker>
   ISSUE_REF`. In PRD mode, append only PRD constraints and background that bind
   this issue. Do not hand the full PRD to every issue subagent.
3. Record the issue branch base SHA before dispatching implementation.
4. Dispatch a fresh implementer subagent using `implementer-prompt.md`. The
   implementer must use the test-driven-development skill when available, write
   the detailed report file, commit all changes, and return a short status.
5. Handle implementer status:
   - **DONE:** verify clean working tree/index and at least one commit, then
     review.
   - **DONE_WITH_CONCERNS:** read concerns. Resolve correctness or scope concerns
     before review; record observations in the ledger.
   - **NEEDS_CONTEXT:** provide missing context and re-dispatch when possible.
   - **BLOCKED:** provide more context, split the issue, or retry with stronger
     model/effort. If unresolved, stop the batch and keep the ledger.
6. Generate a review package with `scripts/review-package BASE HEAD`; use the
   recorded base SHA, never `HEAD~1`.
7. Dispatch an issue reviewer subagent using `issue-reviewer-prompt.md`.
8. Resolve reviewer `Cannot verify from diff` items yourself before marking the
   issue complete. If the item is a real gap, dispatch a fixer and re-review. If
   it is cross-issue risk, record it for integrated review.
9. If review finds Critical or Important issues, dispatch a fresh implementer-
   style fixer subagent on the same issue branch with all blocking findings.
   Re-run review after fixes. Repeat until Critical and Important are clear.
10. Record Minor findings in the ledger.
11. Checkout the PRD/batch branch, squash merge the reviewed issue branch, verify
    clean state, delete the local issue branch, and record the squash commit and
    deleted branch in the ledger. Do not delete remote branches.

Squash commit subject:

- remote trackers: `<type>: <issue title> (<issue-ref>)`
- local markdown: `<type>: <issue title>`

Use a subject-only squash commit.

## Integrated Review

Run integrated review in PRD mode and in issue-list mode with more than one
issue. Skip it for a single issue unless the user explicitly asks or the issue
created broad integration risk.

1. Stay on the clean PRD/batch branch.
2. Generate a review package from the batch base SHA to `HEAD`.
3. Dispatch a final integrated reviewer with the PRD or issue-list requirements
   source, ledger path, issue brief paths, review package, and known Minor
   findings. The review is read-only and checks coverage, integration, regressions,
   PRD/global constraints, and verification evidence.
4. If there are no Critical or Important findings, complete without creating a
   final check branch.
5. If Critical or Important findings exist, create the final check branch from
   the PRD/batch branch. Dispatch one fresh implementer-style final fixer with
   the complete blocking findings list. The fixer must use test-driven
   development, commit all changes, and report tests.
6. Re-run integrated review on the final check branch. Repeat until Critical and
   Important are clear.
7. Checkout the PRD/batch branch, squash merge the final check branch, verify
   clean state, delete the local final check branch, and record the squash commit
   and deleted branch in the ledger. Do not delete remote branches.

## File Handoffs

Everything you paste into a dispatch prompt — and everything a subagent prints
back — stays resident in your context for the rest of the session and is re-read
on every later turn. Hand artifacts over as files:

- **Issue brief:** generated before dispatching an implementer. It is the single
  source of requirements for the issue.
- **Report file:** named after the brief and stored under
  `$(git rev-parse --git-path ipi)`. The implementer writes the full report
  there and returns only status, commits, a one-line test summary, concerns, and
  the report path.
- **Reviewer inputs:** issue reviewers get the issue brief, report file, review
  package, and binding PRD/global constraints.
- **Fix dispatches:** append fix reports and test results to the same report
  file. Re-reviews read the updated file.

Do not paste accumulated prior-issue summaries into later dispatches. A fresh
subagent needs its issue, the interfaces it touches, and binding constraints.
Nothing else.

## Durable Progress

Conversation memory does not survive compaction. Track progress in the ledger,
not only in todos.

- At skill start, check `$(git rev-parse --git-path ipi)/progress.md`.
- Issues listed there as complete are DONE — do not re-dispatch them.
- When an issue's review comes back clean and its branch is squash-merged, append
  one line to the ledger: `Issue <ref>: complete (commits <base7>..<head7>,
  squash <sha7>, review clean, branch deleted)`.
- The ledger is your recovery map: the commits it names exist in git even when
  your context no longer remembers creating them.
- Record every transient IPI file the run creates. On successful completion,
  delete only the transient files recorded in the ledger. Do not `rm -rf` the
  whole IPI directory. Keep files for BLOCKED or interrupted runs.

## Prompt Templates and Scripts

- [implementer-prompt.md](implementer-prompt.md) - issue implementer and fixer
  dispatch template
- [issue-reviewer-prompt.md](issue-reviewer-prompt.md) - issue-scoped review
  template
- `scripts/issue-brief --tracker github|gitlab|local ISSUE_REF [OUTFILE]` -
  writes one issue brief
- `scripts/review-package BASE HEAD [OUTFILE]` - writes commit list, stat, and
  diff package

## Completion

Finish with a concise status: PRD/batch branch, merged issue squash commits,
deleted local branches, final review verdict if any, verification summary,
unresolved risks, and whether any push/PR/tracker side effects were requested.

## Red Flags

Never:

- Start implementation on main/master branch without explicit user consent
- Skip issue review, or accept a report missing either verdict (spec compliance
  AND issue quality are both required)
- Proceed with unfixed Critical or Important issues
- Dispatch multiple implementation subagents in parallel (conflicts)
- Make a subagent read the full PRD when an issue brief is sufficient
- Skip scene-setting context
- Ignore subagent questions
- Accept "close enough" on spec compliance
- Skip review loops
- Tell a reviewer what not to flag, or pre-rate a finding's severity in the
  dispatch prompt
- Dispatch a reviewer without a diff file
- Move to the next issue while the current issue review has open Critical or
  Important findings
- Re-dispatch an issue the progress ledger already marks complete
- Switch branches, merge, or delete branches with a dirty working tree/index
- Push, open PRs, modify tracker issues, delete remote branches, or force-push
  unless explicitly requested
