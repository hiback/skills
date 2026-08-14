# Subagent model settings

When these skills run in pi, you can choose the model and thinking level used for two subagent roles:

| Role | Controlled dispatches |
| --- | --- |
| `reviewer` | The Standards and Spec reviewers spawned by `code-review` |
| `implementer` | The ticket implementers, ticket fixers, and integrated-review fixers spawned by `implement-batch` |

All other subagent dispatches remain agent-selected.

## Configuration lookup

The skill resolves the repository root with `git rev-parse --show-toplevel`. Outside a Git repository, it uses the current working directory. Immediately before each controlled dispatch, it uses the first file that exists:

1. `<repo-root>/.scratch/subagent-models.json`
2. `~/.config/mattpocock-skills/subagent-models.json`

The repository file replaces the global file completely; the two files are not merged. If neither exists, the subagent uses the current session's model and thinking level.

## Configuration format

```json
{
  "reviewer": {
    "model": "provider/model-id",
    "thinking": "high"
  },
  "implementer": {
    "model": "provider/model-id",
    "thinking": "high"
  }
}
```

Both roles are optional. Within a role, either `model` or `thinking` may be omitted. A missing role or field inherits the corresponding current-session value.

Use the full `provider/model-id` shown by:

```bash
pi --list-models
```

Valid thinking levels are:

```text
off | minimal | low | medium | high | xhigh | max
```

## Validation

The active file must be a non-empty JSON object with no keys other than `reviewer` and `implementer`. Each present role must be a non-empty object containing only `model` and `thinking`. A model must be a non-empty, available `provider/model-id`; a thinking level must be one of the values above.

Malformed JSON, unknown fields, empty role objects, invalid values, or unavailable models stop the subagent dispatch. The skill does not fall back to another file or model after a configuration error.

## Create a global configuration

```bash
mkdir -p ~/.config/mattpocock-skills
${EDITOR:-vi} ~/.config/mattpocock-skills/subagent-models.json
```

## Create a repository configuration

From anywhere inside the repository:

```bash
repo_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
mkdir -p "$repo_root/.scratch"
${EDITOR:-vi} "$repo_root/.scratch/subagent-models.json"
```
