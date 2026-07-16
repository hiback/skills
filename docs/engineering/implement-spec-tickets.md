## What it does

`implement-spec-tickets` executes an approved [spec](https://www.aihero.dev/ai-coding-dictionary/spec) or explicit set of [tickets](https://www.aihero.dev/ai-coding-dictionary/ticket) as one recoverable batch. It gives each ticket a fresh implementer, an isolated branch, a review gate, and one squash commit before checking the integrated result against the source requirements.

One controller owns the complete batch state and advances the ticket frontier serially. It never runs implementation workers in parallel and never uses worktrees.

## When to reach for it

You invoke this by typing `/implement-spec-tickets` — the agent won't reach for it on its own.

| Where you are | What to run |
| --- | --- |
| An approved multi-ticket spec should run under one controller with recovery and integrated review | `/implement-spec-tickets` |
| One ticket or a small change should stay in the current context | [implement](https://aihero.dev/skills-implement) |
| The work has not been split into approved tickets yet | [to-tickets](https://aihero.dev/skills-to-tickets) first |
| Independent tickets should run concurrently in separate worktrees | Open separate `implement` sessions; this skill is deliberately serial |

## Prerequisites

The repository must use Git and have a clean working tree and index. A remote tracker must already be configured by [setup-matt-pocock-skills](https://aihero.dev/skills-setup-matt-pocock-skills); explicit local inputs may instead use `.scratch/<feature>/spec.md` and numbered ticket files under its sibling `issues/` directory.

## One controller, one frontier

The **frontier** is the set of tickets whose blockers are complete. Before touching branches, the controller normalizes the dependency graph and rejects duplicate identities, unknown blockers, cycles, ambiguous references, and conflicting tracker state. It then selects the lowest ready ticket, gives a fresh implementer only that ticket's binding context, reviews the resulting branch, squash-merges it, and recalculates the frontier.

Serial execution is the safety property, not a missing optimization. Every later ticket starts from the latest reviewed integration state, while the controller keeps one durable account of blockers, branches, commits, findings, and verification.

## Review and recovery

Each ticket must clear a spec-and-quality review gate before it can merge. Critical and Important findings go through a fixer and re-review loop. A multi-ticket batch then receives an integrated review for complete requirement coverage, cross-ticket behaviour, regressions, and scope creep.

Runtime state lives under Git's internal directory. After an interruption, a matching run resumes only after verifying its recorded base, branches, commits, completed tickets, and ledger. A completed run leaves a compact ledger rather than a trail of transient reports.

## Common questions

**Does it launch all ready tickets in parallel?**

No. A parallel swarm has been [proposed separately upstream](https://github.com/mattpocock/skills/issues/787), and users have reported unexpectedly expensive fan-out when orchestration launches many workers without a clear gate ([issue #826](https://github.com/mattpocock/skills/issues/826)). This skill chooses the opposite contract: one implementation worker at a time, no worktrees, with every reviewed squash present before the next ticket starts.

**Can it resume after the controller or terminal stops?**

Yes, when the durable ledger matches the same input and every recorded Git fact still verifies. It never guesses through a moved branch, mismatched commit, different active batch, or dirty working tree; those conditions stop the run for a decision.

**Does it replace `implement`?**

No. `implement` remains the default for one ticket in one fresh [context window](https://www.aihero.dev/ai-coding-dictionary/context-window). This skill is extra orchestration for an already-approved set when centralized recovery, per-ticket branches, and integrated review justify it.

**Will it push branches, open pull requests, or update tracker state?**

Not unless you explicitly request those side effects. Its normal result is a reviewed local spec or batch branch, squash commits, a verification summary, and a COMPLETE ledger.

## It's working if

- Only tickets with completed blockers enter the frontier.
- At most one implementation worker is active at a time.
- Every completed ticket appears as one reviewed squash commit on the batch branch.
- An interrupted run verifies its ledger and Git state instead of repeating completed work.
- Critical and Important findings are cleared before completion.
- The final report names the batch branch, ticket commits, verification result, remaining risks, and ledger path.

## Where it fits

This is an optional serial executor after [to-tickets](https://aihero.dev/skills-to-tickets). The default path still opens one fresh [implement](https://aihero.dev/skills-implement) session per ticket; choose this branch when one recoverable controller should own the approved frontier. [ask-matt](https://aihero.dev/skills-ask-matt) maps both paths.
