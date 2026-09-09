---
name: codegraph
description: Navigate code with the CodeGraph CLI. Use for symbol definitions and references, caller/callee tracing, and change impact analysis when a local index is available.
---

# CodeGraph

CodeGraph provides a local code index. Use its CLI through the shell or command tool available in your agent harness.

## Workflow

1. Check whether `.codegraph` exists in the target project. If it is missing, use text search and file reads.
2. Run `codegraph status` from the project root. Use the index when it is current and contains files and nodes. If it is stale, sync it before relying on results, or use text search.
3. Use `codegraph explore <query>` for related symbols and call paths, then `codegraph node <symbol>` for precise source and dependents.
4. Use `callers`, `callees`, or `impact` to trace dependencies before editing. Fall back to text search and file reads for missing results or raw text.

## CLI examples

Run these commands from the target project root after checking the index:

```bash
codegraph status
codegraph files
codegraph query "setup.sh" --json
codegraph query "install_codegraph" --json -l 20
codegraph explore "install_codegraph"
codegraph node "install_codegraph"
codegraph node --file scripts/overlord_py/state.py --symbols-only
codegraph callers "install_codegraph"
codegraph callees "install_codegraph"
codegraph impact "make_zsh_default"
codegraph affected
```

Use `codegraph <command> --help` for command-specific options, including project paths and structured output.

## Index maintenance

- A local index normally lives at `.codegraph/codegraph.db`. Check the actual path and status rather than assuming an index or persistence symlink exists.
- If the project uses a CodeGraph daemon, check its status before relying on automatic updates. Use `codegraph sync` to update an existing index or `codegraph index` to build one when indexing is part of the task.
- This repository pins the installed CodeGraph version in `config/tool-versions.env`; `setup.sh` installs the CLI. Installation alone does not mean the project has an index.
