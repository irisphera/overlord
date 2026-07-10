#!/usr/bin/env bash
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

install_apt_tools() {
  local packages=()

  if ! command -v shellcheck >/dev/null 2>&1; then
    packages+=(shellcheck)
  fi

  if ! command -v shfmt >/dev/null 2>&1; then
    packages+=(shfmt)
  fi

  if ((${#packages[@]} == 0)); then
    return
  fi

  apt-get update
  apt-get install -y --no-install-recommends "${packages[@]}"
  rm -rf /var/lib/apt/lists/*
}

install_lsp_servers() {
  local packages=()

  if ! command -v bash-language-server >/dev/null 2>&1; then
    packages+=(bash-language-server)
  fi

  if ! command -v basedpyright-langserver >/dev/null 2>&1; then
    packages+=(basedpyright)
  fi

  if ! command -v biome >/dev/null 2>&1; then
    packages+=(@biomejs/biome)
  fi

  if ((${#packages[@]} == 0)); then
    return
  fi

  npm install -g "${packages[@]}"
}

install_apt_tools
install_lsp_servers
