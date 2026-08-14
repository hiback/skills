---
name: implement-batch
description: "Execute an approved spec or ticket set as one recoverable serial batch."
disable-model-invocation: true
---

# Implement Batch

Execute an approved spec's child tickets, or an explicit ticket set, as one recoverable serial batch. Every ticket gets a fresh implementer, its own branch, a two-axis review, and one squash commit on the batch branch.

You are the **controller**. Implementing and reviewing happen in subagents; your own context holds the ledger and the decisions.

Once the startup questions are answered, run to completion without pausing. Stop for a blocked ticket, a review finding you cannot judge, or a branch or ledger conflict.

Between tool calls, narrate at most one short line — the ledger carries the durable record.

## Inputs

- **Spec mode** — the argument is a spec file or a parent ticket. Discover its child tickets.
- **Ticket-set mode** — the arguments are explicit ticket references. Use exactly that set.

If the user supplies both, ask whether the explicit set restricts execution or whether full spec discovery should run.

The issue tracker should have been provided to you — run `/setup-matt-pocock-skills` if `docs/agents/issue-tracker.md` is missing. That file describes how to read a ticket, list a spec's children, and resolve blockers; for local markdown, a spec's children are the numbered ticket files in its sibling `issues/` directory. Stop before touching branches if discovery comes back empty, or you cannot tell which tickets belong to the spec.

Spec and ticket sources are read-only unless the user asks for a tracker change.

For implementer and fixer dispatches only, immediately use the first existing settings file: `<repo-root>/.scratch/subagent-models.json` (resolve the Git root, falling back to the current working directory), then `~/.config/mattpocock-skills/subagent-models.json`. The active file must be a non-empty JSON object with only optional `reviewer` and `implementer` keys; each present role must be non-empty and may contain only a non-empty full `provider/model-id` as `model` and a `thinking` value of `off`, `minimal`, `low`, `medium`, `high`, `xhigh`, or `max`. Invalid JSON, unknown or empty fields, or unavailable models stop the dispatch. Apply `implementer`; a missing file, role, or field inherits the current session's corresponding value. The startup seam-check subagent remains agent-selected.

## Startup

1. Require a Git repository with a clean working tree and index.
2. Read the ledger at `$(git rev-parse --git-path implement-batch)/progress.md`:

   | Ledger | Action |
   | --- | --- |
   | ACTIVE or BLOCKED, same input | Verify every recorded branch, base SHA, and squash SHA, then resume. |
   | ACTIVE or BLOCKED, different input | Stop and ask. |
   | COMPLETE, same input | Report the recorded squash commits and ask whether to re-run or cancel. |
   | COMPLETE, different input | Replace it. |

3. Resolve the ticket set and read each ticket's status. Only tickets carrying the `ready-for-agent` label `to-tickets` applies are executable; carry any ticket in another state into the startup questions.
4. Dispatch a subagent to check the seams. For each ticket it answers one question — **which public interface will a test observe this behaviour at?** — and returns a short list of ticket, seam, and any doubt. A ticket with no answer, or several mutually exclusive candidates, is doubtful.
5. Ask one batched round of questions: the doubtful seams, any ambiguity that blocks safe progress, and the branch type if it is unclear.
6. Create or resume the batch branch and write the ACTIVE ledger, recording each ticket's settled seam.

Branch names:

```text
Batch branch:       <type>/<slug>
Ticket branch:      <type>/<slug>-ticket-<ticket-id>
Final-check branch: <type>/<slug>-final-check
```

Infer `<type>` (`feat`, `fix`, `refactor`, `chore`, `docs`) from the input, title, and labels; the fallback is `feat`. Derive `<slug>` from the spec title, the ticket set's purpose, or `batch-YYYY-MM-DD`. Get the user's explicit consent before running a batch on `main` or `master`.

If a target branch already exists and the ledger does not record it, stop and ask.

## Per-ticket loop

Take the lowest-numbered ticket — or lowest tracker ID — whose blockers are all complete. Skip a ticket while any blocker is outstanding, and stop to ask when every remaining ticket is blocked. Run one implementer at a time.

1. Check out the clean batch branch and record its SHA as this ticket's **base**.
2. Create the ticket branch from there.
3. Dispatch a fresh implementer subagent with:

   - the ticket reference, and the spec reference as background
   - the seam settled at startup, which its tests observe behaviour at
   - the ticket branch to work on
   - its job:
     1. Use `/tdd` at the ticket's seam, one red-green slice at a time.
     2. Run typechecking and single test files regularly, and the full test suite once at the end.
     3. Commit with a one-line subject and no body, leaving the work as local commits on the ticket branch.
   - a standing instruction to ask rather than guess at an unclear requirement
   - what to return, in under ten lines:
     - **Status:** `DONE` | `BLOCKED` | `NEEDS_CONTEXT`
     - each commit's short SHA and subject
     - a one-line test summary
     - the blocker, when the status is not `DONE`

4. Act on the status. `DONE`: verify a clean tree and at least one commit, then review. `NEEDS_CONTEXT`: supply the missing context and re-dispatch. `BLOCKED`: supply context, or split the ticket with the user's approval, and stop if it stays blocked.
5. Run `/code-review` with the recorded base SHA as the fixed point and the ticket as the spec source. The ticket branch was cut from that base, so its three-dot diff is exactly this ticket's work.
6. Judge the two reports:

   | Finding | Action |
   | --- | --- |
   | Spec axis — a requirement missing, partial, or misunderstood | Blocks the merge |
   | Spec axis — behaviour nobody asked for | Blocks the merge |
   | Standards axis — a documented standard breached | Blocks the merge |
   | Standards axis — a baseline smell, flagged as a judgement call | Record in the ledger, carry to integrated review |
   | Anything you cannot judge | Ask the user |

7. For every blocking finding, dispatch a fresh fixer subagent on the same ticket branch under the same contract as the implementer, then re-run `/code-review` against the same base. Repeat until nothing blocks.
8. Check out the batch branch, squash merge the ticket branch, and commit a one-line subject: `<type>: <ticket title> (<ticket-ref>)` on a remote tracker, `<type>: <ticket title>` on local markdown. Keep the ticket branch — the red-green history and the fixer's steps live only there.
9. Record the ticket's row — its squash SHA, the review verdict, and the judgement calls carried forward — then recompute which ticket comes next.

## Integrated review

Run this once every ticket has merged, whenever the batch held two or more tickets. For a single ticket, run it only when the user asks: the ticket review already covered the same range.

1. Run `/code-review` with the batch's base SHA as the fixed point and the full spec, or the ticket set, as the spec source.
2. Judge the findings by the same table above.
3. If anything blocks, create the final-check branch, dispatch one fixer subagent with the full blocking list under the implementer's contract, and re-run the review. Repeat until nothing blocks, then squash merge the final-check branch into the batch branch.

## Ledger

The ledger at `$(git rev-parse --git-path implement-batch)/progress.md` is the only file this skill writes, and it lives inside `.git` rather than the working tree:

```markdown
# implement-batch

- **Status:** ACTIVE | BLOCKED | COMPLETE
- **Input:** <spec or ticket references>
- **Batch branch:** <type>/<slug>
- **Base SHA:** <sha>

| Ticket | Seam | Branch | Base | Squash | Review | Risks carried |
| --- | --- | --- | --- | --- | --- | --- |
```

A ticket recorded with a squash SHA is done and is never re-dispatched. Keep the ledger on a blocked or interrupted run.

## Completion

Set the ledger to COMPLETE and report: the batch branch, each ticket's squash commit, the integrated review verdict, the verification summary, the risks still carried, the ledger path, and **every branch the run left behind**, so the user can decide what to clean up.

Push or open pull requests only when the user explicitly asks.
