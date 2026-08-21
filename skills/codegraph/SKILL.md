---
name: codegraph
description: Local-first code intelligence for AI agents. Use when exploring a codebase, finding definitions/references, call graphs, impact analysis, or faster than grep. Provides codegraph query/explore/node/files/callers/callees/impact via CLI. Many repositories already have .codegraph index.
---

# CodeGraph

Local graph index for `prime-agent`. It is faster and more accurate than `grep` for codebase navigation. The workspace already persists `.codegraph -> .overlord/.codegraph` with a daemon that auto-syncs on file changes.

## When to use

- Find where a symbol is defined or used — `query`, `node`
- Understand callers/callees, blast radius before editing — `callers`, `callees`, `impact`, `explore`
- Navigate large repos (71 files / 1,530 nodes in this workspace) without burning tokens on `grep` + `read`
- `grep` is still fine for raw text or when `.codegraph` is missing — use both, prefer CodeGraph when index is present.

## Quick start via bash

CodeGraph is a CLI. Prime-agent has one tool `ipython` with `%%bash` / bash exec.

```bash
codegraph status                      # 71 files, 1,530 nodes, is index up to date?
codegraph files                       # project file structure from index
codegraph query "setup.sh" --json     # search symbols (add -k function/class etc)
codegraph query "prime-agent" --json -l 20
codegraph explore "prime-agent"       # relevant symbols + call paths + source (like Read but graph-aware)
codegraph node "install_prime_agent"  # one symbol's source + caller/callee trail
codegraph node --file scripts/overlord_py/state.py --symbols-only
codegraph callers "install_codegraph"
codegraph callees "install_codegraph"
codegraph impact "make_zsh_default"   # what breaks if you change it
codegraph affected                    # tests affected by changed files
```

All commands accept `-p <path>` for project path and `-j/--json` for structured output. Parse JSON in Python when needed.

## Workflow

1.  Check `codegraph status` first — if `Index is up to date` and `Files/Nodes` >0, use it.
2.  `codegraph explore <query>` before editing — gives you source + call paths in one shot, cheaper than multiple `read`s.
3.  `codegraph node <symbol>` for precise source + dependents.
4.  Fallback to `grep -r` / `read` if `No index` or `No results`.

## Index maintenance

- Index lives in `.codegraph/codegraph.db` (symlink to `.overlord/.codegraph`). It is git-ignored.
- Daemon watches files and auto-syncs (`Daemon.log`). If status says outdated, run `codegraph sync` or `codegraph index` (full rebuild).
- `setup.sh` installs `codegraph` pinned in `config/tool-versions.env` via `npm -g` and runs `codegraph status` to warm.

## MCP (future)

`codegraph mcp` exposes `codegraph_query`, `codegraph_explore`, `codegraph_node` as MCP tools. Prime-agent stdio MCP is not yet wired (only http via `mcpServers`), so use the CLI via bash for now. When stdio lands, add to `~/.prime/agent/settings.json`:
```json
{"mcpServers":{"codegraph":{"type":"stdio","command":"codegraph","args":["mcp"]}}}
```

References are relative to this skill directory.
