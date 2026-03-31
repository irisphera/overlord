# SCRIPTS KNOWLEDGE BASE

**Generated:** 2026-03-07 (UTC)
**Parent:** `/workspace/AGENTS.md`

## OVERVIEW

`scripts/` owns host-side lifecycle control; `overlord` is the single launcher for build/create/start/attach/remove flow and runtime env forwarding.

## STRUCTURE

```
scripts/
└── overlord   # Main CLI: command parsing, container lifecycle, config injection
```

## WHERE TO LOOK

| Task | Location | Notes |
|------|----------|-------|
| Add CLI command behavior | `overlord` command dispatch | Handles `opencode`, `shell`, `fresh`, `purge`, `help` |
| Change env forwarding | `overlord` provider/env allowlist | Provider keys + always-forwarded vars are curated here |
| Change container naming/persistence | `overlord` lifecycle helpers | One container per workspace, `.overlord` persistence wiring |
| Change config sync into container | `overlord` bootstrap steps | Copies host `config/*` to container config paths |

## CONVENTIONS

- Script is authoritative over README for current command surface.
- Lifecycle is wrapper-first (users run `overlord`, not raw `docker` commands).
- `.overlord/` state management is intentional and must remain git-ignored.
- Provider additions must include env forwarding updates in launcher logic.

## ANTI-PATTERNS

- Do not hardcode secrets or provider credentials in script defaults.
- Do not bypass wrapper lifecycle with undocumented direct docker flows.
- Do not introduce command aliases without updating root `AGENTS.md` + `README.md` together.
