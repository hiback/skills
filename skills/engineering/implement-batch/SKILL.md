---
name: implement-batch
description: Execute an approved spec or explicit ticket set as one recoverable serial batch through fresh implementers, isolated ticket branches, review gates, local squash merges, and integrated review.
disable-model-invocation: true
---

# Implement Batch

Execute an approved spec's child tickets or an explicit ticket set as one
recoverable serial batch by dispatching a fresh implementer per ticket,
reviewing each ticket before integration, and running a broad integrated review
when required.

**Core principle:** Serial frontier + fresh implementer per ticket + ticket
review gate + local squash merge + integrated review when needed.

**Narration:** Between tool calls, narrate at most one short line. The ledger and
tool results carry the durable record.

**Continuous execution:** After startup conflicts are resolved, do not pause
between tickets. Stop only for an unresolved BLOCKED status, ambiguity that
prevents safe progress, branch/tracker/ledger conflicts, new side-effect
authorization, user interruption, or completion.

## Inputs

Two input modes exist:

1. **Spec mode:** The argument is a local spec file or a remote parent ticket.
   Discover its child tickets and always run integrated review after they merge.
2. **Ticket-set mode:** The arguments are explicit ticket references. Treat one
   ticket as a one-item set and do not discover children from ticket bodies.
   Run integrated review for multiple tickets; skip it for one ticket unless the
   user asks or the ticket creates broad integration risk.

If the user supplies both a spec and explicit ticket refs, ask whether the
explicit set restricts execution or whether full spec discovery should run.

Only the current spec/ticket conventions are supported. Do not recognize old
filenames, modes, state directories, commands, or aliases.

## Tracker Resolution

Use `docs/agents/issue-tracker.md` as the source of truth when it exists. If it
is missing:

- local markdown is allowed only when the input is an explicit path under
  `.scratch/`
- GitHub, GitLab, and custom trackers require confirmation or configuration
- `git remote -v` may inform a recommendation, but never authorizes execution

Built-in trackers:

- **Local Markdown:** `.scratch/<feature>/spec.md` and
  `.scratch/<feature>/issues/<NN>-<slug>.md`
- **GitHub:** `gh`, using native parent/sub-issue and blocker relationships
- **GitLab:** `glab`, plus relationship/API commands recorded in the project
  tracker configuration

For any other tracker, continue only when `docs/agents/issue-tracker.md`
provides explicit commands for reading tickets, listing children, resolving
dependencies, and reading status. Never special-case or infer an unconfigured
tracker.

Spec and ticket sources are read-only. Do not push, open PRs, comment, close,
label, or otherwise modify tracker records unless the user explicitly requests
that side effect.

## Spec Ticket Discovery

In Spec mode, discover tickets in this order:

- **Local Markdown:** Require `.scratch/<feature>/spec.md`; read only numbered
  ticket files in its sibling `issues/` directory.
- **GitHub:** Prefer native `subIssues` from the parent and native `blockedBy`
  relations from each ticket. If native child relations are empty, fall back to
  tickets with an exact `## Parent` reference to the spec ticket.
- **GitLab:** Prefer relationship commands defined by the tracker
  configuration. If unavailable, fall back to exact `## Parent` references.
- **Final fallback:** Use ticket URLs/IDs explicitly listed by the spec only
  when each reference is unambiguous and verifiable.

Do not use comments, fuzzy title search, ordinary numbers, or unrelated
directories to infer children. Stop before branch operations if discovery is
empty, duplicated, ambiguous, or contradictory.

## Dependency Graph

Before branch operations, normalize every ticket into a ledger table with:

```text
Ticket | Source | Status | Blocked by | Normalized blockers
```

Rules:

- Local ticket identity is the numeric filename prefix. Prefer numeric blocker
  references; an exact unique title may be normalized to its number.
- Remote ticket identity is the tracker ID. Prefer native blocker relations;
  use an exact body reference only when native data is absent.
- Reject duplicate identities, unknown blockers, ambiguous title references,
  dependency cycles, and conflicting native/body relationships.
- A blocker inside the selected set must complete before its dependent ticket
  enters the frontier.
- A blocker outside the selected set is satisfied only when the tracker clearly
  reports it complete/closed. Otherwise the dependent ticket remains blocked.
- Never auto-add or implement an external blocker.
- Read ticket status before execution. By default, only `ready-for-agent`
  tickets are executable; stop on claimed, blocked, unknown, or conflicting
  states.

Execute the frontier serially. When several tickets are ready, select the
lowest local number or tracker ID. After each squash merge, mark the ticket
complete in the ledger and recompute the frontier. Never dispatch multiple
implementation agents in parallel and never use worktrees.

## Startup and Recovery

1. Require a Git repository and a clean working tree/index before any checkout,
   branch, merge, or delete operation. Do not stash, discard, or auto-commit
   user changes.
2. Read
   `$(git rev-parse --git-path implement-batch)/progress.md` when it exists.
3. Resume a matching ACTIVE or BLOCKED run after verifying every recorded
   branch, base SHA, HEAD, squash commit, and completed ticket. Never
   re-dispatch a completed ticket.
4. If an ACTIVE/BLOCKED/unknown ledger belongs to different input, stop and ask.
   A COMPLETE ledger may be summarized and replaced by the new run.
5. Resolve input mode, tracker, requirements source, ticket set, dependency
   graph, and executable frontier.
6. Scan once for spec/ticket contradictions, tracker/ref mismatches, dependency
   failures, and status risks. Ask one batched question only when required.
7. Infer branch type (`feat`, `fix`, `refactor`, `chore`, `docs`) from the user
   input, spec/ticket title, and labels. Ask once if unclear; final fallback is
   `feat`.
8. Derive a short lowercase slug from the spec title, ticket-set purpose, or
   `batch-YYYY-MM-DD`.
9. Create or resume the spec/batch branch. If the current branch is not an
   obvious base and no matching ledger identifies it, ask whether it is the
   base, an existing spec/batch branch, or unrelated.
10. Write the ACTIVE ledger with input identity, tracker, normalized dependency
    table, base branch/SHA, spec/batch branch, branch type, slug, created files,
    and timestamp.

Branch names:

```text
Spec/batch branch:  <type>/<slug>
Ticket branch:      <type>/<slug>-ticket-<ticket-id>
Final-check branch: <type>/<slug>-final-check
```

If a target branch already exists but is not recorded in the matching ledger,
stop. Do not reuse, reset, or delete it without explicit direction. Do not
start batch implementation directly on `main` or `master` without explicit
consent.

## Model and Effort

Use the current model/effort for ticket implementers, fixers, reviewers, and
final-check fixers unless the user overrides a role. Use the strongest available
model/effort for integrated review unless overridden. If a subagent is blocked
by reasoning limits, retry once with a stronger available setting, record the
reason, and stop if it remains blocked.

## Per-Ticket Loop

For the lowest-ID ticket in the current frontier:

1. Start from the clean spec/batch branch and create or resume the recorded
   ticket branch.
2. Generate a brief with `scripts/ticket-brief --tracker <tracker> TICKET_REF`.
   In Spec mode, append only the spec constraints/background that bind this
   ticket. Never hand the full spec to every implementer.
3. Record the ticket branch base SHA.
4. Dispatch a fresh implementer with `ticket-implementer-prompt.md`. The
   implementer uses `/tdd`, never `/implement` or `/code-review`, writes a
   detailed report file, commits with subject-only messages, and returns a short
   status.
5. Handle the result:
   - **DONE:** verify a clean tree/index and at least one commit, then review.
   - **DONE_WITH_CONCERNS:** resolve correctness/scope concerns before review and
     record observations.
   - **NEEDS_CONTEXT:** provide focused context and re-dispatch when possible.
   - **BLOCKED:** provide context, split only with user approval, or perform the
     single stronger-model retry. Stop if unresolved.
6. Generate `scripts/review-package BASE HEAD`, using the recorded base rather
   than `HEAD~1`.
7. Dispatch one read-only reviewer with `ticket-reviewer-prompt.md`. This is the
   batch controller's review gate; do not invoke `/code-review`.
8. Resolve every `Cannot verify from diff` item. Fix real gaps; defer named
   cross-ticket risks to integrated review.
9. For Critical or Important findings, dispatch a fresh implementer-style fixer
   on the same ticket branch, append tests/results to the report, and re-review.
   Repeat until blocking findings are clear.
10. Record Minor findings and cross-ticket risks in the ledger.
11. Checkout the clean spec/batch branch, squash merge the reviewed ticket
    branch, delete only the local ticket branch, and record the source range,
    squash SHA, review result, and deletion.
12. Recompute the frontier from the ledger.

Squash commit subjects:

- remote tracker: `<type>: <ticket title> (<ticket-ref>)`
- local markdown: `<type>: <ticket title>`

Use a one-line subject only. Put details in the report/ledger, never the commit
body.

## Integrated Review

Always run integrated review in Spec mode. In Ticket-set mode, run it for
multiple tickets; for one ticket, run it only on explicit request or recorded
integration risk.

1. Stay on the clean spec/batch branch.
2. Generate a review package from the batch base SHA to `HEAD`.
3. Dispatch a read-only integrated reviewer with the full spec or ticket-set
   source, normalized ledger, ticket briefs, implementation reports, per-ticket
   review results, accumulated Minor findings/risks, and batch review package.
4. Check full requirements coverage, cross-ticket integration, regressions,
   scope creep, standards, and verification evidence.
5. If no Critical or Important findings remain, finish without a final-check
   branch.
6. Otherwise create the recorded final-check branch, dispatch one fresh
   implementer-style fixer with the complete blocking list, require `/tdd`, a
   subject-only commit, and test evidence, then re-run integrated review.
7. Repeat until blocking findings are clear. Squash merge the final-check branch
   into the spec/batch branch, delete only the local branch, and record the
   squash SHA.

## File Handoffs

Avoid pasting growing artifacts through the conversation. Store them under:

```text
$(git rev-parse --git-path implement-batch)
```

- **Ticket brief:** the ticket's requirements source for implementers/reviewers.
- **Report file:** detailed implementation, tests, TDD evidence, fixes, and
  concerns. The subagent returns only status, commits, a one-line test summary,
  concerns, and the path.
- **Review package:** fixed commit list, stat, and diff for one review range.
- **Ledger:** the normalized dependency graph and durable recovery map.

Do not paste prior-ticket summaries into later dispatches. A fresh subagent
needs its ticket, touched interfaces, binding constraints, and nothing else.

## Durable Progress and Cleanup

- Record every created brief, report, and review package in `progress.md`.
- Tickets recorded complete are DONE and must never be re-dispatched.
- On BLOCKED or interruption, retain the ledger and all recorded artifacts.
- On successful completion, delete only the recorded transient artifacts. Do
  not `rm -rf` the state directory.
- Retain a compact `progress.md` with status COMPLETE, input identity, base and
  spec/batch branch, per-ticket source range/squash/review result, final review
  result, verification summary, and completion timestamp.
- A future run may summarize and replace a COMPLETE ledger without archiving it.

## Prompt Templates and Scripts

- [ticket-implementer-prompt.md](ticket-implementer-prompt.md) — ticket
  implementer and fixer dispatch template
- [ticket-reviewer-prompt.md](ticket-reviewer-prompt.md) — ticket-scoped review
  gate
- `scripts/ticket-brief --tracker github|gitlab|local TICKET_REF [OUTFILE]` —
  write one ticket brief
- `scripts/review-package BASE HEAD [OUTFILE]` — write a fixed review package

## Completion

Finish with a concise status: spec/batch branch, ticket squash commits, deleted
local branches, final review verdict, verification summary, unresolved risks,
COMPLETE ledger path, and any explicitly requested push/PR/tracker side effects.

## Red Flags

Never:

- start batch implementation on `main`/`master` without explicit consent
- mutate spec/ticket sources or tracker state without explicit authorization
- auto-expand scope to external blockers
- use fuzzy child discovery or guess through dependency ambiguity
- reuse branches without matching ledger evidence
- use worktrees or parallel implementation subagents
- invoke `/implement` or `/code-review` inside this workflow
- skip ticket review, accept a report missing either spec compliance or ticket
  quality, or proceed with Critical/Important findings
- make every implementer read the full spec when a scoped brief is sufficient
- dispatch a reviewer without a fixed review package
- switch branches, merge, or delete branches with a dirty tree/index
- push, open PRs, delete remote branches, or force-push unless explicitly asked
