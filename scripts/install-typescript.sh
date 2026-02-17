#!/bin/bash
# Install TypeScript/JavaScript Language Server for OpenCode LSP support
set -e

echo "==> Installing TypeScript Language Server..."

if command -v bun &> /dev/null; then
    PKG_MGR="bun"
    GLOBAL_FLAG="add -g"
elif command -v npm &> /dev/null; then
    PKG_MGR="npm"
    GLOBAL_FLAG="install -g"
else
    echo "Error: Neither bun nor npm found. Please install Node.js or Bun first." >&2
    exit 1
fi

echo "Using package manager: $PKG_MGR"

$PKG_MGR $GLOBAL_FLAG typescript typescript-language-server

TS_VERSION=$(tsc --version 2>/dev/null || echo "unknown")
TSSERVER_PATH=$(which typescript-language-server 2>/dev/null || echo "not found")

echo ""
echo "==> Installation complete!"
echo "    TypeScript: $TS_VERSION"
echo "    TS Server:  $TSSERVER_PATH"
echo ""
echo "OpenCode should now detect and use the TypeScript language server."
