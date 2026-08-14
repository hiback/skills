## What it does

`implement-batch` works a whole set of [tickets](https://www.aihero.dev/ai-coding-dictionary/ticket) for you instead of one. You point it at an approved [spec](https://www.aihero.dev/ai-coding-dictionary/spec) or an explicit list of tickets, and it builds each one, reviews it, and squash-merges it onto a batch branch before starting the next.

It runs the same build as [implement](https://aihero.dev/skills-implement) — [tdd](https://aihero.dev/skills-tdd) at the seam, then [code-review](https://aihero.dev/skills-code-review) — rather than a second implementation process of its own. What it adds is orchestration: a fresh [subagent](https://www.aihero.dev/ai-coding-dictionary/subagent) per ticket so no ticket inherits the last one's context, a branch per ticket so a bad one can be thrown away, and a ledger it can resume from after an interruption.

## When to reach for it

You invoke this by typing `/implement-batch` — the [agent](https://www.aihero.dev/ai-coding-dictionary/agent) won't reach for it on its own.

| Where you are | What to run |
| --- | --- |
| An approved spec whose child tickets should all be built now | `/implement-batch <spec-ref>` |
| A specific set of tickets you want worked in one go | `/implement-batch <ticket-ref>...` |
| One ticket, and you want to watch it happen | [implement](https://aihero.dev/skills-implement) |
| The work isn't split into tickets yet | [to-tickets](https://aihero.dev/skills-to-tickets) first |
| Independent tickets you want running concurrently | Separate `implement` [sessions](https://www.aihero.dev/ai-coding-dictionary/session); this skill is deliberately serial |

## Prerequisites

A Git repository with a clean working tree and index. The tickets must live somewhere [setup-matt-pocock-skills](https://aihero.dev/skills-setup-matt-pocock-skills) has configured — a real tracker, or local markdown under `.scratch/<feature>/`.

Its runtime state is a single `progress.md` inside `.git`, so nothing it writes lands in your working tree.

## One controller, one frontier

The **frontier** is the set of tickets whose blockers are all complete. The skill takes the lowest-numbered ticket on the frontier, builds it, merges it, and recomputes.

You are talking to the **controller**. It never writes code itself: implementing and fixing happen in subagents, and reviewing happens in `code-review`'s two parallel subagents. What stays in the controller's own [context window](https://www.aihero.dev/ai-coding-dictionary/context-window) is small on purpose — a status line per ticket, two review reports, and the ledger. That is what makes a ten-ticket batch survivable in one session.

Serial execution is the safety property, not a missing optimisation. Every ticket starts from the latest reviewed state, so the second ticket builds on a first that has already passed review.

## Choosing the implementer model

When running in pi, ticket implementers and both kinds of fixer share the optional `implementer` model and thinking settings. The startup seam checker remains agent-selected. A repository setting can replace the global setting; without either, controlled workers inherit the current session values. See [Subagent model settings](https://github.com/hiback/skills/blob/main/docs/subagent-models.md) for the paths, format, and validation rules.

## The one stop it plans to make

Before it touches a branch, it dispatches a subagent to ask one question of each ticket: **which public interface will a test observe this behaviour at?** A ticket with no answer, or with several mutually exclusive candidates, is a ticket whose seam isn't settled — and `tdd` refuses to write a test at an unconfirmed seam.

Those doubts come back to you in a single batched round of questions, along with anything else ambiguous. After you answer, the run is meant to go to the end without pausing. This is the difference between a batch that asks you eleven times and one that asks you once.

## Common questions

**Does it run tickets in parallel?**

No, one at a time. A parallel swarm is [proposed separately upstream](https://github.com/mattpocock/skills/issues/787) and remains open there. The trade is deliberate: parallel workers can't each start from a base that has already passed review.

**`code-review` doesn't give a verdict. Who decides what blocks a merge?**

The controller does. `code-review` deliberately reports Standards and Spec separately and never merges or reranks them, so something downstream has to judge — and the skill carries the rule for that. A finding it can't call goes to you rather than through on a guess.

**It finished and left a pile of branches behind.**

On purpose. After a squash merge, that ticket's red-green history and every fixer step exist only on its ticket branch — deleting it is irreversible and the batch branch keeps none of it. The completion summary lists what the run left behind so you can decide.

**My terminal died halfway through.**

Run it again with the same input. It verifies every recorded branch, base and squash commit against the repository before resuming, and never re-dispatches a ticket the ledger records as merged. If the ledger is complete and you point it at the same input again, it tells you what already landed and asks whether you meant to re-run.

## It's working if

- It asks you one round of questions at the start, then stops asking.
- Only one implementation worker is active at a time.
- Every finished ticket is exactly one squash commit on the batch branch.
- You can see `/tdd` in each implementer's trace and `/code-review` in the controller's, rather than a bespoke review appearing inline.
- An interrupted run comes back reporting what it verified, not repeating work.
- The final report names the batch branch, each ticket's commit, the risks it carried, and the branches still lying around.

## Where it fits

This is an optional serial executor downstream of [to-tickets](https://aihero.dev/skills-to-tickets), on the same chain as [implement](https://aihero.dev/skills-implement) rather than replacing it. The default path is still one fresh `implement` session per ticket, which keeps you in the loop on every one; reach for the batch when you want the whole frontier worked in one go. [ask-matt](https://aihero.dev/skills-ask-matt) maps both paths.
