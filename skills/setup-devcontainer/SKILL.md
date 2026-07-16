---
name: setup-devcontainer
description: Create or safely update setup-devcontainer.sh from explicit project evidence. Use when configuring project-specific runtimes, build tools, language servers, or development tooling for an Overlord workspace.
compatibility: opencode
---

# Setup devcontainer

Create or update the active project's `setup-devcontainer.sh` so a fresh Overlord container installs the tooling this project actually requires.

## 1. Inspect before editing

Resolve the active project root from the current OpenCode session before choosing any package or command. Do not assume the authoring workspace is `/workspace`: native OpenCode uses the host project path, while containerized OpenCode normally uses `/workspace`.

```bash
PROJECT_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd -P)"
```

Inspect `$PROJECT_ROOT`:

1. Read `AGENTS.md`, `README*`, contributor documentation, and any existing setup or bootstrap scripts.
2. If `$PROJECT_ROOT/setup-devcontainer.sh` already exists, read it completely before changing it.
3. Inspect root and workspace manifests, lockfiles, version files, and toolchain declarations.
4. Inspect CI, devcontainer, editor, `.opencode`, formatter, linter, test, and language-server configuration.
5. Check whether a monorepo declares additional manifests in its workspace configuration.

Use explicit project evidence to identify the required language runtime, package manager, build tool, language server (LSP), formatter, linter, test runner, and system libraries. Examples of evidence include `pom.xml` or `mvnw` for Maven/Java, `build.gradle*` or `gradlew` for Gradle/Java, `Cargo.toml` and `rust-toolchain.toml` for Rust/Cargo, and package-manager lockfiles for JavaScript projects. Treat these as evidence to investigate, not permission to add every conventional ecosystem tool.

Do not guess from source-file extensions, directory names, dependency directories, tools already available in the current container, or ecosystem convention alone. If declarations conflict, stop and ask which declaration is authoritative. If evidence names a tool but does not establish a safe installation method, report it as unresolved rather than inventing a package name or download command.

## 2. Plan the script

State the evidence-to-tool mapping before editing. Include only tools needed to build, test, lint, format, or provide configured editor/LSP support for this repository.

The resulting script must be deterministic and idempotent:

- Start a new script with `#!/usr/bin/env bash` and `set -euo pipefail`.
- Require root explicitly and `cd /workspace`; Overlord runs the script as root from the mounted workspace.
- Set noninteractive package-manager options where supported.
- For apt packages, use `--no-install-recommends` unless project evidence requires recommended packages.
- Check whether each command or required version is already present before installing it.
- Collect system or global packages into deduplicated arrays in stable order and skip package-manager calls when each array is empty.
- Pin versions when the repository already pins them. Do not introduce an arbitrary version pin.
- Never pipe network responses into a shell or interpreter. Prohibit `curl ... | bash`, `wget ... | sh`, process substitution from remote content, and equivalent execution patterns.
- Before executing a downloaded binary or archive as root, require an evidence-backed pinned version and verify its integrity with a checksum or signature from a trusted source identified by the project.
- If the only documented installation method is an unverified remote installer, report it as unresolved and do not add it without explicit user approval after explaining the root and Docker-socket risk.
- Avoid unconditional downloads, duplicate shell-profile appends, and project dependency installation that would leave root-owned files in `/workspace`.
- Remove package-manager indexes or temporary files created by the script when appropriate.

Prefer the repository's declared installer or toolchain manager. Use wrapper scripts such as `mvnw` and `gradlew` when present instead of installing another build-tool version. Install an LSP only when project or editor configuration requests it, or when the project documentation explicitly makes editor support part of setup.

## 3. Create or update

If `$PROJECT_ROOT/setup-devcontainer.sh` does not exist, create it with the planned root guard, workspace change, and idempotent installation functions.

If `$PROJECT_ROOT/setup-devcontainer.sh` already exists, update it through the smallest semantic edits needed for the current evidence. Preserve unrelated commands, comments, shell options, functions, ordering, and behavior. These preservation rules take precedence over normalizing unaffected package arrays or structure. Do not replace the whole file merely to match a preferred template, and do not introduce managed marker blocks.

Make the completed Bash script executable:

```bash
chmod 755 "$PROJECT_ROOT/setup-devcontainer.sh"
```

Show the resulting diff and verify that every added installation has a corresponding project signal.

## 4. Verify

Syntax validation is mandatory:

```bash
bash -n "$PROJECT_ROOT/setup-devcontainer.sh"
```

When `shellcheck` is available, run:

```bash
shellcheck "$PROJECT_ROOT/setup-devcontainer.sh"
```

When `shfmt` is available, discover repository-configured formatting options and run it in diff mode. If no policy is discoverable for an existing script, do not reformat unrelated content; report any pre-existing formatting differences.

Do not claim completion if syntax validation fails. Report the commands run, checks skipped because a verifier was unavailable, and any unresolved or contradictory project evidence. Remind the user that the script executes as root only when the container is created or restarted; reusing an already-running container does not rerun it.
