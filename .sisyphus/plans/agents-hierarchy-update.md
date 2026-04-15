# AGENTS Hierarchy Update Plan

## Decision

- Keep exactly three AGENTS files: `/workspace/AGENTS.md`, `/workspace/config/AGENTS.md`, `/workspace/scripts/AGENTS.md`.
- Do not create AGENTS files under `.overlord/`, `.claude/`, or any new subdirectory.

## Why this hierarchy

- Root owns repo-wide guidance, top-level structure, cross-cutting source-of-truth rules, and repo-wide anti-patterns.
- `config/` owns runtime-injected config surfaces plus bootstrap wrappers (`entrypoint.sh`, `jdtls.sh`, `zellij-*.kdl`, `opencode*.json`, `oh-my-openagent*.jsonc`).
- `scripts/` owns the host-side launcher boundary (`overlord`): command surface, lifecycle, env forwarding, persistence wiring, and config injection flow.
- `.overlord/` is runtime state, not maintained source, so it should be documented but must not get its own AGENTS file.

## File-by-file rewrite goals

### `/workspace/AGENTS.md`

Keep:
- concise repo overview
- top-level structure including `Dockerfile`, `config/`, `scripts/`, `.overlord/`
- repo-wide "where to look"
- global source-of-truth and runtime-boundary rules
- cross-cutting anti-patterns
- canonical commands and manual verification notes

Trim or remove:
- deep `config/`-specific invariants that belong in `config/AGENTS.md`
- launcher-only details that belong in `scripts/AGENTS.md`
- repeated child-file wording

### `/workspace/config/AGENTS.md`

Keep:
- one-line purpose of `config/`
- role map for each config artifact family
- runtime-copy/source-of-truth rules
- bootstrap-specific invariants for `entrypoint.sh` and `jdtls.sh`
- config-only anti-patterns

Trim or remove:
- repo-wide overview already present at root
- launcher lifecycle details that belong in `scripts/AGENTS.md`
- generic restatements of top-level commands

### `/workspace/scripts/AGENTS.md`

Keep:
- one-line purpose of `scripts/`
- `overlord` responsibilities: CLI parsing, image/container lifecycle, env forwarding, config copy-in, `.overlord/` handling
- script-authoritative command surface and doc-sync rules
- launcher-specific anti-patterns

Trim or remove:
- deep explanation of config file contents that belong in `config/AGENTS.md`
- repeated repo-wide source-of-truth phrasing already covered at root unless needed for launcher-specific context

## Review criteria

- Each AGENTS file should answer a different maintainer question.
- Parent content should not be copied verbatim into children.
- Child files should not restate each other.
- Final hierarchy remains exactly three AGENTS files.
- `.overlord/` is described only as runtime state.
