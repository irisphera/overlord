# Overlord

Overlord provides a persistent, web-first OpenCode environment per workspace. Use its container launcher for an isolated toolchain, or install the same OpenCode configuration directly on the host.

## Choose container or native

Choose **container** when you want the image-provided toolchain, workspace isolation, persistent OpenCode and zsh state, or the `overlord` web, shell, and zellij commands.

Container isolation separates the workspace filesystem and toolchain; it is not a security boundary. The container receives writable `/var/run/docker.sock` access and can control the host container daemon.

Choose **native** when OpenCode must run directly on the host. `scripts/install` configures and optionally installs host packages; it does not launch web mode, create containers or `.overlord`, or install zellij.

## Container quick start

The launcher requires host Python 3 and either Podman or Docker. It selects Podman when available and otherwise uses Docker.

```bash
git clone https://github.com/irisphera/overlord.git
cd overlord
mkdir -p "$HOME/.local/bin"
ln -s "$(pwd)/scripts/overlord" "$HOME/.local/bin/overlord"
export PATH="$HOME/.local/bin:$PATH"

cd /path/to/project
export OPENCODE_SERVER_PASSWORD="replace-with-a-strong-password"
overlord
```

On the first launch in a workspace, Overlord builds the image if needed, creates and starts that workspace's container, initializes runtime configuration, and starts OpenCode web mode.

Later launches reuse the persistent container and web server when possible. Web mode publishes a random host port on `0.0.0.0` (all host interfaces); it is unauthenticated when `OPENCODE_SERVER_PASSWORD` is empty.

The launcher prints a local URL and, when it can determine one, a network URL. Do not assume the network URL is reachable from every LAN.

## Everyday container commands

Run these commands from the workspace directory. `overlord`, `overlord web`, and `overlord opencode` are the same web-mode launch path.

```bash
overlord                         # start or reuse web mode (default)
overlord web                     # explicit web-mode alias
overlord opencode                # web-mode alias
overlord shell                   # open zsh in the workspace container
overlord zellij                  # open the configured zellij session
overlord --list-configs          # list checked-in routing presets
overlord --config default        # select the default routing preset
overlord --lms-model qwen3-8b    # route all agents to this LM Studio model
overlord fresh                   # request container removal; retain image and .overlord state
overlord purge                   # remove the container and image; retain .overlord state
overlord help                    # show launcher usage
```

To recreate a workspace container, run `overlord fresh`, then run `overlord` separately. Confirm the next launch reports `Creating container...`; `fresh` currently does not surface engine stop or removal failures.

Use `overlord purge && overlord` after changing the Dockerfile, image-provided tooling, or image bootstrap files.

To change `--config` or `--lms-model` for an existing workspace, run `overlord fresh`, then relaunch with the desired option. Catalog and routing injection occurs when the container is created or restarted.

## Native host install

Run the installer from this repository. It installs the checked-in provider catalog, selected routing preset, zellij configuration, environment file, and repository-owned `setup-devcontainer` skill.

```bash
scripts/install
scripts/install --list-configs
scripts/install --config default
scripts/install --lms-model qwen3-8b
```

A full install requires Bun in `PATH`. It installs OpenCode, oh-my-openagent, and CodeGraph with Bun, installs the pinned RTK Linux release for amd64 or arm64, then creates command shims in `~/.local/bin` when their installed targets are available.

Pinned versions and RTK release checksums are maintained in `config/tool-versions.env`; changing that one file updates both the container image and native installation paths. RTK is checksum-verified, must report the exact pinned version, and is initialized with `rtk init --global --opencode` as the runtime user.

Use `--skip-package-install` to write static configuration, environment, and skill files only. This mode does not require Bun and does not install OpenCode, oh-my-openagent, CodeGraph, RTK, or the RTK OpenCode plugin.

```bash
scripts/install --skip-package-install
```

The installer writes `opencode.json`, `oh-my-openagent.jsonc`, `oh-my-opencode.jsonc`, and `overlord-env` to `${XDG_CONFIG_HOME:-$HOME/.config}/opencode/`. A full install also requires RTK initialization to create a non-empty `plugins/rtk.ts` in that directory.

It writes zellij configuration to `${XDG_CONFIG_HOME:-$HOME/.config}/zellij/config.kdl`.

It installs the `setup-devcontainer` skill to `$HOME/.agents/skills/setup-devcontainer/SKILL.md`, including in `--skip-package-install` mode.

Existing files or links at those destinations are timestamp-backed up before replacement. `overlord-env` is written with mode `600`; source it before host `opencode` if the shell does not already provide the required settings.

```bash
. "${XDG_CONFIG_HOME:-$HOME/.config}/opencode/overlord-env"
opencode
```

The installer configures zellij but does not install it. Install zellij separately if you want the native zellij escape hatch.

Checked-in `config/` is authoritative for both workflows. The native installer replaces managed copies on rerun and backs them up first. The launcher replaces container copies during create or restart injection; normal reuse only repairs missing or invalid runtime config.

## Routing and LM Studio

`config/opencode.json` is the sole checked-in OpenCode provider and model catalog. Checked-in `config/oh-my-openagent*.jsonc` files are routing presets.

The current and only preset is `default` (`oh-my-openagent.jsonc`).

The default routes agents and categories to Azure `gpt-5.6-sol` with role-specific reasoning effort. The Azure deployment ID must be `gpt-5.6-sol`, or you must change its `id` in `config/opencode.json`.

The catalog includes Azure GPT-5.6 Sol and Terra; Bedrock Claude Opus 4.8 and Haiku 4.5; Vertex Gemini 3.1 Pro, 3.5 Flash, and 3 Flash; and LM Studio `qwopus3.5-9b-coder-mtp`.

Managed container files are `/home/overlord/.config/opencode/opencode.json`, `oh-my-openagent.jsonc`, and `oh-my-opencode.jsonc`, plus `/home/overlord/.config/zellij/config.kdl`.

They are generated copies of checked-in `config/` and are replaced when the launcher injects configuration. Do not treat in-container copies as authoritative inputs.

```bash
overlord --list-configs
overlord --config default
scripts/install --list-configs
scripts/install --config default
```

`--config` selects a checked-in routing preset and cannot be combined with `--lms-model`.

`--lms-model MODEL` rewrites all routing targets to `lmstudio/MODEL` and replaces the catalog's LM Studio model name for that run or installation.

```bash
overlord --lms-model qwen3-8b
scripts/install --lms-model qwen3-8b
```

LM Studio defaults differ by workflow. Native installation writes `LMSTUDIO_BASE_URL=http://localhost:1234/v1`.

The container launcher forwards `LMSTUDIO_BASE_URL=http://host.docker.internal:1234/v1` unless the host overrides it. Both default `LMSTUDIO_API_KEY` to `lm-studio`.

## Credentials and environment

Supply credentials through the launching shell; do not put real credentials in checked-in files or image layers. The container forwards configured provider, MCP, and web-password environment variables at runtime.

```bash
export AZURE_API_KEY="replace-with-your-key"
export AZURE_RESOURCE_NAME="replace-with-your-resource"
export OPENCODE_SERVER_PASSWORD="replace-with-a-password"
overlord
```

Relevant provider settings include `AWS_REGION`, `AWS_BEARER_TOKEN_BEDROCK`, `GOOGLE_CLOUD_PROJECT`, and `GOOGLE_CLOUD_LOCATION`.

They also include `AZURE_API_KEY`, `AZURE_RESOURCE_NAME`, `LMSTUDIO_BASE_URL`, and `LMSTUDIO_API_KEY`.

`EXA_API_KEY`, `TAVILY_API_KEY`, and `CONTEXT7_API_KEY` are forwarded for configured MCP use. `OPENCODE_SERVER_PASSWORD` protects the web server and is also used for launcher health checks.

If present, host Google Application Default Credentials are made available from `$GOOGLE_APPLICATION_CREDENTIALS` or `~/.config/gcloud/application_default_credentials.json`. Credentials created inside a container are removed by `fresh` or `purge`.

To create Google ADC inside the container, use the image-provided Google Cloud CLI. Container-created ADC survives container reuse, but not `fresh` or `purge`.

```bash
overlord shell
gcloud auth application-default login --no-launch-browser
```

Rerun `overlord` after changing or removing `EXA_API_KEY` or `OPENCODE_SERVER_PASSWORD`. It reconciles those values for a reused web server.

## Persistence and lifecycle

Each workspace receives one persistent container and a git-ignored `.overlord/` directory.

OpenCode data and zsh data are direct writable bind mounts from `.overlord/`, so conversations, memory, and shell history survive both `fresh` and `purge`.

`fresh` requests removal of the workspace container but keeps its image and `.overlord/` state. `purge` removes the container and image; the next launch rebuilds them.

Packages installed into a container outside persisted mounts disappear with `fresh`.

Before `fresh`, or before `purge` removes an existing container, Overlord verifies exactly one writable bind mount for `/workspace`, OpenCode data, and zsh data, each from the expected workspace source. If `purge` proves the container is already absent, it skips mount inspection and continues image cleanup.

Missing, ambiguous, read-only, named-volume, or mismatched mounts fail closed without destructive lifecycle action.

For a refused legacy container, quiesce it first. Copy only unmounted state to a separate staging directory, verify that copy, then remove the exact incompatible container and relaunch with Overlord.

Never copy live state back onto bind sources as an automatic recovery step.

## Workspace setup and mounts

The current directory is mounted read-write at `/workspace`. When present, `~/.gitconfig` and `~/.ssh` are mounted read-only. OpenCode and zsh state mount from the workspace as follows:

| Host source | Container destination | Mode |
| --- | --- | --- |
| current workspace | `/workspace` | read-write |
| `~/.gitconfig` | `/home/overlord/.gitconfig` | read-only |
| `~/.ssh` | `/home/overlord/.ssh` | read-only |
| `/var/run/docker.sock` | `/var/run/docker.sock` | read-write |
| `.overlord/opencode-data` | `/home/overlord/.local/share/opencode` | read-write |
| `.overlord/zsh-data` | `/home/overlord/.zsh_data` | read-write |

The Docker socket mount lets container processes control the host container daemon. Treat any code run in the container as having that capability.

Testcontainers defaults are `DOCKER_HOST=unix:///var/run/docker.sock`, `TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE=/var/run/docker.sock`, and `TESTCONTAINERS_HOST_OVERRIDE=host.docker.internal`.

Set `TESTCONTAINERS_RYUK_DISABLED=true` only when daemon policy blocks Ryuk.

For repository-specific dependencies, run `/setup-devcontainer` in OpenCode. The repository-owned skill inspects explicit manifests and tool configuration, then creates an executable, idempotent `setup-devcontainer.sh` when absent or minimally updates it when present. It preserves unrelated setup behavior and reports unsupported or contradictory evidence instead of guessing install commands.

During container creation or restart, Overlord runs `/workspace/setup-devcontainer.sh` as root, then repairs `/home/overlord` ownership.

Review setup scripts before launching untrusted workspaces. Reattaching to an already-running container skips setup; use `overlord fresh` when setup must run again.

## Included tooling and zellij

The container image includes OpenCode, oh-my-openagent, CodeGraph, RTK, Node.js 22, Bun, Python 3, uv, Docker CLI with Compose, and Google Cloud CLI.

It also includes git, zsh, zellij, neovim, ripgrep, jq, ast-grep, ShellCheck, and shfmt.

Project-specific language servers and stacks are intentionally not image defaults. Use `/setup-devcontainer` to maintain `setup-devcontainer.sh`; review the result because Overlord executes it as root from `/workspace`. The skill validates Bash syntax and uses ShellCheck and shfmt when available.

The container includes the repository-owned `setup-devcontainer` skill at `/home/overlord/.agents/skills/setup-devcontainer/SKILL.md` and global skills from pinned `mattpocock/skills`. Run `/setup-matt-pocock-skills` inside OpenCode to create repo-specific skill setup; the native installer installs only the repository-owned skill.

Open zellij with `overlord zellij`. Its checked-in configuration uses zsh, maps `Ctrl+b` to tab mode, and leaves `Ctrl+t` available for application passthrough.

`Ctrl+q` quits the zellij session while the container remains available.

`Alt+n` opens a pane, `Alt+f` toggles floating panes, and `Alt+Arrow` moves focus between panes or tabs.

`config/zellij-config.kdl` is the active zellij configuration source. `config/zellij-opencode.kdl` is checked in but is not currently injected by the launcher.

## RTK integration

Both full-install workflows pin RTK through `config/tool-versions.env`. The Docker image selects the matching Linux release asset from `TARGETARCH`; the native installer selects it from `uname -m`. Both verify the authored SHA-256 checksum, require the exact `rtk VERSION` output, and initialize the OpenCode plugin as the user who runs OpenCode.

RTK integration is provided by `${XDG_CONFIG_HOME:-$HOME/.config}/opencode/plugins/rtk.ts`; it does not add a provider or modify `config/opencode.json`.

## Troubleshooting

**`python3` is missing:** install host Python 3, ensure it is in `PATH`, then rerun `overlord`.

**No container engine is found:** install Podman or Docker and ensure it is in `PATH`. Podman has precedence when both are installed.

**Permissions or a stale workspace container:** request recreation without discarding persisted state, then confirm the next launch reports `Creating container...`.

```bash
overlord fresh
overlord
```

**The image or entrypoint changed:** rebuild the image.

```bash
overlord purge && overlord
```

**A lifecycle command refuses mounts:** treat this as a data-protection stop, not a warning. Follow the concise legacy recovery procedure in [Persistence and lifecycle](#persistence-and-lifecycle).

**API calls fail:** export the appropriate placeholder-replaced credential values before launching.

For LM Studio in a container, confirm the host service is reachable at `host.docker.internal` or set `LMSTUDIO_BASE_URL` explicitly.

## Verification

Run these checks after changing launcher, configuration, image, or documentation behavior. Use a disposable home for the native configuration-only check.

```bash
overlord
overlord web
overlord opencode
overlord shell
overlord zellij
overlord --list-configs
scripts/install --list-configs

tmp_home=$(mktemp -d)
HOME="$tmp_home" XDG_CONFIG_HOME="$tmp_home/.config" XDG_CACHE_HOME="$tmp_home/.cache" \
  scripts/install --skip-package-install
cmp skills/setup-devcontainer/SKILL.md \
  "$tmp_home/.agents/skills/setup-devcontainer/SKILL.md"
test ! -e "$tmp_home/.local/bin/rtk"
test ! -e "$tmp_home/.config/opencode/plugins/rtk.ts"

# Confirm the following launch reports "Creating container...".
overlord fresh
overlord
overlord purge && overlord
python3 -m unittest discover -s scripts/tests
```

## License

MIT
