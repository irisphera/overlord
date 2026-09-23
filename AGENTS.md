# PROJECT KNOWLEDGE BASE

## WORKING RULES

The owner set these rules for every Irisphera repository. If a later section sets a stricter rule for this repository,
the stricter rule wins.

- **Work on `main`.** Commit to `main` and push. If the repository has a deployment workflow, deploy the change too. A
  change that touches only documentation needs no deploy. Do not ask first, and do not leave work on a separate branch
  waiting for approval. If you work in a separate worktree, land the commit with `git push origin HEAD:main`. Never
  force-push `main`.
- **Ask before anything else in production.** Reading production data, running migrations by hand and changing services
  or infrastructure need the owner's explicit go-ahead.
- **Do not work around a refusal.** If a permission check or a tool refuses an action, hand the command to the owner.
- **Hand over commands that work from any directory.** Use `git -C <absolute path>` and absolute file paths. Never hand
  over a bare `git checkout <commit> -- <paths>`: run in the wrong checkout, it once overwrote uncommitted work. Prefer
  commands that refuse, rather than overwrite, when they run in the wrong place. After the owner runs a command, check
  the result in the intended directory. The `!` prefix works only at the Claude Code prompt. In the owner's own shell, a
  leading `!` negates the exit status, so give the command without it.
- **Keep records in git.** Ledgers, decisions and notes the team relies on belong in the repository they describe, not
  on one machine. Do not point repository files at local paths. If the repository has a `LEDGER.md`, update its state
  and its log in the same commit as the change, or right after.
- **Check old work before you clear it.** Before you delete uncommitted work, a stash or a branch, check whether it is
  already on `main` or has been replaced. If it is neither, commit it or ask the owner.
- **Do not run `git submodule update` in a linked worktree.** A linked worktree shares the submodule's git config with
  the main checkout. The update rewrites `core.worktree` there and breaks `git status` in the main checkout. Move the
  submodule with `git -C <worktree>/<submodule> checkout <commit>` instead.
- **Prefer the simplest mechanism, and measure.** Prove that a change works by measuring it, for example against a copy
  of the production schema, instead of adding guards. Guards that depend on details of the test environment have
  refused in production before.

## OVERVIEW

Overlord is a minimal dev-container launcher + standalone VM setup. The repo has:
- `setup.sh`: self-contained Debian 13 and Ubuntu 22.04/24.04/26.04 LTS installer. Root-owned tool distributions; one target account for shell/editor/agent configuration.
- `Dockerfile`: builds Debian 13 with `setup.sh --user overlord --profile container`.
- `scripts/overlord`: Python >=3.12 launcher; verified workspace lifecycle and persisted-state migration.
- `config/` : container bootstrap and zellij config
- `skills/codegraph` + `.prime/agent/skills/codegraph`: shared CodeGraph CLI guidance for coding agents; `.prime/agent/skills/codegraph` is the Prime Agent discovery copy.

For symbol lookup, call graphs, or impact analysis, read `skills/codegraph/SKILL.md`. Check for a usable CodeGraph index before querying it. Use text search when the index is missing or does not cover the task.

## STRUCTURE

```
overlord/
├── Dockerfile      # builds image via setup.sh
├── setup.sh        # standalone VM installer (also used in container)
├── setup-devcontainer.sh # thin adapter selecting shared container profile
├── config/         # entrypoint, zellij config, tool-versions
├── scripts/        # overlord launcher (python)
├── .overlord/      # per-workspace runtime state (git-ignored)
└── README.md
```

## COMMANDS

```bash
overlord                # open shell in container (default)
overlord shell          # shell
overlord zellij         # open zellij
overlord fresh          # remove container
overlord purge          # remove container + image
bash setup.sh --user NAME # Supported Debian/Ubuntu; root or existing passwordless sudo
```

## NOTES

- `setup.sh` is sourceable; `main` orchestrates system installation, then privilege-dropped user configuration. It does not alter native VM sudoers.
- Rootless Podman Machine on macOS is a primary launcher target. Match its selected connection to the locally managed VM; remote transport alone is not grounds for rejection.
- Container names include a canonical-path hash. Verify mounts before start/reuse/exec/removal; delete containers by immutable ID and serialize lifecycle mutations with the workspace lock. Purge removes workspace-owned image tags while retaining shared aliases.
- An initialization marker is written only after workspace setup and runtime configuration succeed. Failed initialization is retried.
- Agent containers bind only the launched workspace and its local `.overlord/` state by default. Legacy containers exposing other host paths are recreated before attachment. `OVERLORD_ENGINE_SOCKET` explicitly opts out of workspace-only isolation; preserve host mount/socket ownership and modes.
- `.overlord/` persists agent sessions/configuration/databases and zsh state across fresh/purge. `.overlord/claude-data` is mounted at `/home/overlord/.claude` with `CLAUDE_CONFIG_DIR` pointing at it, so Claude Code's `.claude.json` persists too. That mount is optional when verifying a container (older containers must stay removable) but required for attach: a container without it is recreated. Host models seed only missing workspace files. Because that state outlives the image, the entrypoint re-applies the image installer's `configure_prime_agent_models` policy to the mounted `models.json` on every start; seeding alone would pin an existing workspace to the model catalog of the image that created it.
- Prime Agent shared skills are a curated list in `install_prime_agent_skills` (`setup.sh`): `setup-matt-pocock-skills`, `grill-me`, `grill-with-docs`, `grilling`, `domain-modeling`, `thermos`, `thermo-nuclear-review`, `thermo-nuclear-code-quality-review`. Add skills one at a time with `npx skills add <owner/repo> --skill NAME`; do not install whole collections.
- Managed Prime models (`configure_prime_agent_models`): `opencode-go` carries exactly `deepseek-flash`, `muse-spark-1.3-contributor`, `mimo-v2.6-flash`, `mimo-v2.6-pro`, `space-bunny-free`; `google-vertex` carries `gemini-3.8-flash`. No Azure or GPT model is managed for Prime; previously managed IDs live in `retired` and are removed from existing state (user-added models stay).
- Managed Prime models share one window policy: `contextWindow = 150000 + 16384`, so Prime's `contextTokens > contextWindow - reserveTokens` rule auto-compacts every model at 150k. Every managed entry carries `maxTokens = 32000`: Prime 0.9.5 caps each request at `min(maxTokens, 32000)` and defaults a custom model without `maxTokens` to 16384, so omitting the field lowers the cap instead of removing it. `limitTokens` and `maxInputTokens` are written for forward compatibility but Prime 0.9.5 reads neither. Verify against the real registry with `prime-agent --offline model list` (`scripts/tests/test_prime_model_policy.py`).
- `setup.sh` prompts for the optional Context7 and Serper API keys on a terminal and otherwise reads `CONTEXT7_API_KEY` / `SERPER_API_KEY`; keys are stored in the agent directory (`auth.json`, `settings.json`) and never printed.
- Setup functions are re-sourced through `declare -f` during privilege phase transfers. Keep heredocs attached to plain commands: `if cmd <<'EOF' ... then` is re-rendered as an unparsable body (`test_configuration_survives_both_privilege_phase_transfers`).
- Claude Code is installed with npm (`install_claude_code`): `CLAUDE_CODE_VERSION` defaults to the `next` dist-tag, which setup resolves to a concrete version before installing into `/opt/overlord/claude-<version>`; `--safe-chain-skip-minimum-package-age` is passed only when Safe Chain is on `PATH`. Codex CLI is no longer installed; `remove_codex` deletes the distribution earlier runs published.
- Behavioral tests: `/usr/bin/python3 -m unittest discover -s scripts/tests`.
