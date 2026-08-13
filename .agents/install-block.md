# The canonical install block

One install story, one wording. `README.md`, `.changeset/*`, and every page under `docs/` must say **this** and nothing else. Change it here first, then propagate.

Claude Code's official `mattpocock-skills` listing points at `mattpocock/skills`, not this fork. An unqualified install therefore omits this fork's promoted additions. Install the fork through its repository marketplace, or use skills.sh with `hiback/skills`.

## Claude Code — the fork plugin

<canonical-block name="claude-code">

```bash
claude plugin marketplace add hiback/skills
claude plugin install mattpocock-skills@mattpocock
```

Or, from inside a session:

```
/plugin marketplace add hiback/skills
/plugin install mattpocock-skills@mattpocock
```

The `@mattpocock` suffix is the marketplace name declared by this repository. Keep the qualified form so Claude Code selects the fork instead of the official upstream listing.

</canonical-block>

## Codex, and other agents — skills.sh

The plugin is Claude Code only. Everywhere else, [skills.sh](https://skills.sh/) copies editable skill files into the project. Use the whole-set form on `README.md`:

<canonical-block name="skills-sh-whole-set">

```bash
npx skills@latest add hiback/skills
```

Pick the skills you want, and which coding agents to install them on. **The installer lets you choose which skills to take — make sure `setup-matt-pocock-skills` is one of them.**

</canonical-block>

…and the single-skill form wherever one skill is named on its own. Note that **`docs/` pages are not a consumer of this block**: ai-hero renders the install widget above the body, so a page that writes the commands out duplicates it. See [writing-docs.md](./writing-docs.md).

<canonical-block name="skills-sh-one-skill">

```bash
npx skills@latest add hiback/skills --skill=<name>
```

```bash
npx skills@latest update <name>
```

</canonical-block>

`skills@latest` is the pinned spelling in all three. Pages under `docs/` carry no hand-written install commands because the site renders those itself.

## The two routes are exclusive

The plugin is a managed, read-only bundle. skills.sh writes files you own and edit. Installing both leaves the user with every skill twice — always say "pick one".

## The upstream install is not the fork install

`claude plugins install mattpocock-skills` resolves through Claude Code's official marketplace to `mattpocock/skills`. It is the correct command for the upstream set, but it does not install `implement-batch` or any future promoted fork addition, so fork-facing documentation must not use it.
