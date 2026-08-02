# CONFIG KNOWLEDGE BASE

**Generated:** 2026-04-12 (UTC)
**Parent:** `/workspace/AGENTS.md`

## OVERVIEW

`config/` is the host-authored source of truth for runtime-injected config, container bootstrap, and terminal/editor wrapper scripts used by `scripts/overlord`.

RTK integration is a generated OpenCode plugin created by the full-install workflows; it is not part of the checked-in provider catalog.

## SOURCE OF TRUTH

- Edit files here.
- The runtime files actually consumed by OpenCode and zellij live under `/home/overlord/.config/*` inside the container and are overwritten by `scripts/overlord` during lifecycle actions.
- `entrypoint.sh` is copied into the image by the Dockerfile. `jdtls.sh` is retained only as a reference wrapper for repo-local Java setup scripts; the shared image no longer installs JDTLS.
- `tool-versions.env` pins semantic versions used by the container and native full-install workflows.

## FILE MAP

| File | Role | Runtime target / note |
|------|------|------------------------|
| `opencode.json` | Single OpenCode provider/model catalog | Copied to `/home/overlord/.config/opencode/opencode.json` |
| `oh-my-openagent.jsonc` | Default OpenCode Go DeepSeek-V4-Flash-0731 routing preset (same routing as `opencode-ds`) | Copied to `/home/overlord/.config/opencode/oh-my-openagent.jsonc` by default |
| `oh-my-openagent.azure.jsonc` | Azure GPT-5.6 Sol/Luna routing preset | Selected with `--config azure` |
| `oh-my-openagent.gemini.jsonc` | Vertex Gemini 3.6 Flash routing preset | Selected with `--config gemini` |
| `oh-my-openagent.opencode-ds.jsonc` | OpenCode Go DeepSeek-V4-Flash-0731 routing preset | Selected with `--config opencode-ds` |
| `oh-my-openagent.openrouter.jsonc` | Existing OpenRouter routing preset | Selected with `--config openrouter` |
| `entrypoint.sh` | Container bootstrap entrypoint | Root startup, permission repair, privilege drop |
| `jdtls.sh` | Java LSP wrapper reference | Not installed by the shared image; Java repos own JDTLS setup |
| `zellij-config.kdl` | Active zellij config source | Copied to `/home/overlord/.config/zellij/config.kdl` |
| `zellij-opencode.kdl` | Checked-in layout file | Present in repo, not currently injected by launcher |
| `tool-versions.env` | Shared semantic-version pins | Sourced by Docker and native install; parsed by launcher package checks. Playwright remains container-only. |

## LOCAL INVARIANTS

- `opencode.json` is the only selectable OpenCode provider catalog; routing choices live in checked-in `oh-my-openagent*.jsonc` presets.
- The routing preset set is exactly `default`, `azure`, `gemini`, `opencode-ds`, and `openrouter`; `default` is byte-identical to `opencode-ds` and routes every category and agent through the built-in `opencode-go/deepseek-v4-flash` model.
- `opencode-ds` uses the built-in `opencode-go/deepseek-v4-flash` model for DeepSeek-V4-Flash-0731. Heavy categories and agents select reasoning effort `max`: categories via the canonical `reasoning` field (lowered to variant/effort for delegated tasks), agents via the OpenCode-native `variant` key (the only per-agent selector that survives the full request path; the model declares reasoning effort variants `high` and `max`). Light routes (`quick`, `unspecified-low`, `multimodal-looker`, `explore`, `librarian`) keep provider defaults. The deprecated `reasoningEffort` field must not be used: OpenCode drops it as an unknown top-level agent key.
- Vertex uses provider key `google` with `@ai-sdk/google-vertex`; `GOOGLE_CLOUD_LOCATION` is supplied by launcher/installer environment normalization.
- RTK must integrate through `${XDG_CONFIG_HOME}/opencode/plugins/rtk.ts`, not a provider entry in `opencode.json`.
- `entrypoint.sh` must preserve root bootstrap -> UID/GID remap -> ownership repair -> `exec gosu overlord "$@"` handoff.
- `zellij-config.kdl` intentionally maps tab mode to `Ctrl+b` and leaves `Ctrl+t` available for app passthrough.

## MANUAL VERIFICATION

- Config catalog/routing edits: run `overlord --list-configs`, then use `overlord fresh && overlord --config <preset>` to verify the selected routing preset is re-injected.
- RTK installer edits: run protected diffs for `config/opencode.json` and `config/oh-my-openagent*.jsonc`; they should stay unchanged.
- `entrypoint.sh` edits: use `overlord purge && overlord` because the entrypoint is copied into the image; `fresh` alone reuses a stale image.
- Dockerfile image edits: use `overlord purge && overlord` so the image is rebuilt.

## ANTI-PATTERNS

- Do not edit `/home/overlord/.config/*` in the container and expect changes to persist.
- Do not remove schema markers or reintroduce selectable `opencode*.json` variants; keep provider catalog in `opencode.json` and routing presets in `oh-my-openagent*.jsonc`.
- Do not add RTK provider entries or generated plugin artifacts to checked-in runtime config.
- Do not remove UID/GID remap, ownership repair, or final privilege drop from `entrypoint.sh`.
- Do not assume `zellij-opencode.kdl` is active runtime config unless launcher wiring is added.
