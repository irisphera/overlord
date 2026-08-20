# PROJECT KNOWLEDGE BASE

## OVERVIEW

Overlord is a minimal dev-container launcher + standalone VM setup. The repo has:
- `setup.sh` : standalone non-interactive installer (lazyvim, zellij, zsh, oh-my-zsh, zsh-autosuggestions/syntax-highlighting/completions)
- `Dockerfile` : builds a container by running `setup.sh`
- `scripts/overlord` : creates/reuses a per-workspace container and runs setup.sh inside
- `config/` : container bootstrap and zellij config

Clean dev environment without extra agents remains. This is a pure dev environment.

## STRUCTURE

```
overlord/
├── Dockerfile      # builds image via setup.sh
├── setup.sh        # standalone VM installer (also used in container)
├── setup-devcontainer.sh # wrapper that calls setup.sh
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
bash setup.sh           # direct VM setup (non-interactive, handles sudo NOPASSWD fix)
```

## NOTES

- setup.sh is idempotent and DEBIAN_FRONTEND=noninteractive.
- It fixes AWS VM sudo password prompt by installing /etc/sudoers.d/99-nopasswd-* with NOPASSWD:ALL and extending timestamp_timeout.
- Container launch runs setup.sh inside the container on create/start.
- .overlord/ holds persisted zsh_data and survives fresh/purge.
