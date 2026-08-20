# SCRIPTS KNOWLEDGE BASE

**Parent:** `/workspace/AGENTS.md`

## OVERVIEW

`scripts/` owns the host launcher. `overlord` is the bind-mounted local workflow.
`scripts/overlord` is a minimal shim that resolves host `python3` and execs `scripts/overlord_py/`.
`install` is a thin wrapper that execs `setup.sh`.

## PRIMARY COMMAND

- `overlord` modes: `shell` (default), `zellij`, `fresh`, `purge`, `help`
- Engine: Podman preferred, Docker fallback
- `setup.sh` is the standalone VM/container initializer (zsh, oh-my-zsh, zellij, lazyvim)

## WHERE TO LOOK

| Task | Location | Notes |
|------|----------|-------|
| CLI | `overlord_py/cli.py` | shell/zellij/fresh/purge/help |
| Container lifecycle | `overlord_py/container_lifecycle.py` | image build, create, start, setup.sh exec |
| Runtime config | `overlord_py/runtime_config.py` | zellij config injection |
| Persisted mounts | `overlord_py/persisted_state_mounts.py` | verifies workspace + zsh_data binds |
