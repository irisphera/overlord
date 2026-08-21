#!/usr/bin/env bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

# setup.sh - Standalone VM + container initializer
# Idempotent, non-interactive. Installs: zsh, oh-my-zsh, zsh-autosuggestions,
# zsh-syntax-highlighting, zsh-completions, zellij, lazyvim (neovim + LazyVim starter),
# codegraph (local code intelligence), prime-agent (272k contextWindow override).
# Safe to run repeatedly via: bash setup.sh  or  ./setup.sh
# Also used by the overlord container (as /workspace/setup-devcontainer.sh).

ZELLIJ_VERSION="${ZELLIJ_VERSION:-0.43.1}"
CODEGRAPH_VERSION="${CODEGRAPH_VERSION:-1.5.0}"
LAZYVIM_REPO="${LAZYVIM_REPO:-https://github.com/LazyVim/starter}"
LOG_PREFIX="[setup]"

info() { printf '%s %s\n' "${LOG_PREFIX}" "$*"; }
warn() { printf '%s WARN: %s\n' "${LOG_PREFIX}" "$*" >&2; }

# --- sudo handling for AWS VMs where sudo asks password but no password is set ---
# Ensure current user has passwordless sudo. On AWS, sudo timestamp may expire
# quickly and subsequent `sudo` would prompt for a password that doesn't exist.
ensure_nopasswd_sudo() {
  local user
  user="$(whoami)"
  if [ "${user}" = "root" ]; then
    return 0
  fi
  if ! command -v sudo >/dev/null 2>&1; then
    return 0
  fi
  # Already passwordless?
  if sudo -n true 2>/dev/null; then
    return 0
  fi
  info "configuring passwordless sudo for ${user} (fixes AWS VM password prompt)..."
  # Try to use the current sudo grace period (first sudo after login often works)
  # to write a NOPASSWD drop-in. If we can't sudo at all, warn.
  if ! sudo true 2>/dev/null; then
    warn "cannot sudo without password - will try to continue; installs may prompt"
    return 0
  fi
  # Write drop-in: user NOPASSWD:ALL
  local sudoers_file="/etc/sudoers.d/99-nopasswd-${user}"
  echo "${user} ALL=(ALL) NOPASSWD:ALL" | sudo tee "${sudoers_file}" >/dev/null
  sudo chmod 440 "${sudoers_file}" 2>/dev/null || true
  # Also increase timestamp timeout so sudo doesn't ask again quickly
  local timeout_file="/etc/sudoers.d/99-timestamp-timeout"
  echo "Defaults timestamp_timeout=60" | sudo tee "${timeout_file}" >/dev/null
  sudo chmod 440 "${timeout_file}" 2>/dev/null || true
  # Validate
  if sudo visudo -c >/dev/null 2>&1; then
    info "passwordless sudo configured"
  else
    warn "sudoers validation failed, removing drop-ins"
    sudo rm -f "${sudoers_file}" "${timeout_file}" 2>/dev/null || true
  fi
  # Refresh timestamp non-interactively
  sudo -v 2>/dev/null || true
  if sudo -n true 2>/dev/null; then
    info "sudo -n true now succeeds"
  else
    warn "sudo still requires password"
  fi
}

ensure_nopasswd_sudo

# Determine sudo prefix
SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  SUDO="sudo"
  # If sudo -n fails but sudo true succeeded above we already fixed it.
  # For all apt operations use `sudo -n` when possible, but fall back to sudo.
  if ! sudo -n true 2>/dev/null; then
    warn "sudo -n true still fails; using 'sudo' (may prompt once)"
  fi
fi

# Helper to run with sudo if needed, trying -n first
run_sudo() {
  if [ -z "${SUDO}" ]; then
    "$@"
  else
    if sudo -n true 2>/dev/null; then
      sudo -n "$@"
    else
      sudo "$@"
    fi
  fi
}

APT_UPDATED=0
apt_update_once() {
  if [ "${APT_UPDATED}" -eq 0 ]; then
    info "apt-get update..."
    run_sudo apt-get update -y
    APT_UPDATED=1
  fi
}

# --- Base packages (non-interactive) ---
install_base_packages() {
  local pkgs=(
    git
    curl
    wget
    ca-certificates
    build-essential
    zsh
    neovim
    ripgrep
    fd-find
    fzf
    unzip
    locales
    jq
    xdg-utils
  )
  # Check which are missing
  local missing=()
  for p in "${pkgs[@]}"; do
    case "${p}" in
      fd-find) command -v fdfind >/dev/null 2>&1 || command -v fd >/dev/null 2>&1 || missing+=("${p}") ;;
      fzf) command -v fzf >/dev/null 2>&1 || missing+=("${p}") ;;
      neovim) command -v nvim >/dev/null 2>&1 || missing+=("${p}") ;;
      *) dpkg -s "${p}" >/dev/null 2>&1 || missing+=("${p}") ;;
    esac
  done
  # Also ensure python3 for some lazyvim extras (optional)
  if ! command -v python3 >/dev/null 2>&1; then
    missing+=(python3 python3-pip python3-venv)
  fi
  if [ "${#missing[@]}" -eq 0 ]; then
    info "base packages already installed"
    return 0
  fi
  apt_update_once
  info "installing: ${missing[*]}"
  run_sudo apt-get install -y --no-install-recommends "${missing[@]}"
  run_sudo rm -rf /var/lib/apt/lists/* 2>/dev/null || true
  # fd-find installs as fdfind on Ubuntu; link to fd
  if command -v fdfind >/dev/null 2>&1 && ! command -v fd >/dev/null 2>&1; then
    run_sudo ln -sf "$(command -v fdfind)" /usr/local/bin/fd 2>/dev/null || true
  fi
}

install_base_packages

# --- locales ---
if ! locale -a 2>/dev/null | grep -qx "en_US.utf8"; then
  info "generating locale en_US.UTF-8..."
  run_sudo locale-gen en_US.UTF-8 2>/dev/null || true
fi

# --- zellij (pinned) ---
install_zellij() {
  if command -v zellij >/dev/null 2>&1; then
    local cur
    cur="$(zellij --version 2>/dev/null | head -n1 || true)"
    if echo "${cur}" | grep -q "${ZELLIJ_VERSION}"; then
      info "zellij ${ZELLIJ_VERSION} already installed (${cur})"
      return 0
    fi
  fi
  local arch
  arch="$(uname -m)"
  case "${arch}" in
    x86_64|amd64) arch="x86_64" ;;
    aarch64|arm64) arch="aarch64" ;;
    *) warn "unsupported arch for zellij: ${arch}"; return 0 ;;
  esac
  info "installing zellij v${ZELLIJ_VERSION} (${arch})..."
  local tmp
  tmp="$(mktemp -d)"
  # shellcheck disable=SC2064
  trap "rm -rf '${tmp}'" RETURN 2>/dev/null || trap "rm -rf ${tmp}" EXIT
  curl -fsSL "https://github.com/zellij-org/zellij/releases/download/v${ZELLIJ_VERSION}/zellij-${arch}-unknown-linux-musl.tar.gz" | tar xz -C "${tmp}"
  run_sudo install -m 0755 "${tmp}/zellij" /usr/local/bin/zellij
  rm -rf "${tmp}"
  trap - RETURN 2>/dev/null || true
  zellij --version 2>/dev/null || true
}
install_zellij

# --- codegraph (pinned) ---
# --- nvm + Node.js (npm tooling and prime-agent need node; install before both) ---
NVM_VERSION="${NVM_VERSION:-0.40.3}"
NODE_MAJOR="${NODE_MAJOR:-24}"
install_nvm_node() {
  local nvm_dir="${NVM_DIR:-${HOME}/.nvm}"
  export NVM_DIR="$nvm_dir"
  if [ ! -s "${nvm_dir}/nvm.sh" ]; then
    info "installing nvm v${NVM_VERSION}..."
    curl -fsSL "https://raw.githubusercontent.com/nvm-sh/nvm/v${NVM_VERSION}/install.sh" \
      | bash -s -- --no-use 2>&1 | sed 's/^/[nvm] /' || warn "nvm install failed"
  else
    info "nvm already installed"
  fi
  # shellcheck disable=SC1091
  . "${nvm_dir}/nvm.sh" --no-use >/dev/null 2>&1 || true
  if ! type nvm >/dev/null 2>&1; then
    warn "nvm not available; skipping Node.js install (npm-dependent steps may fail)"
    return 0
  fi
  if ! command -v node >/dev/null 2>&1 || ! node --version 2>/dev/null | grep -q "^v${NODE_MAJOR}\."; then
    info "installing Node.js ${NODE_MAJOR} via nvm..."
    nvm install "${NODE_MAJOR}" 2>&1 | sed 's/^/[nvm] /' || warn "Node.js ${NODE_MAJOR} install failed"
  fi
  nvm alias default "${NODE_MAJOR}" >/dev/null 2>&1 || true
  nvm use default --silent >/dev/null 2>&1 || true
  if command -v node >/dev/null 2>&1; then
    info "node $(node --version), npm $(npm --version)"
  else
    warn "node/npm not available on PATH after nvm install"
  fi
}

# --- uv (python project manager used by workspace setups) ---
install_uv() {
  if command -v uv >/dev/null 2>&1; then
    info "uv already installed ($(uv --version 2>/dev/null | awk '{print $2}'))"
    return 0
  fi
  local uv_install_dir
  if [ "$(id -u)" -eq 0 ]; then
    uv_install_dir="/usr/local/bin"
  else
    uv_install_dir="${HOME}/.local/bin"
  fi
  info "installing uv to ${uv_install_dir}..."
  curl -LsSf https://astral.sh/uv/install.sh \
    | env UV_INSTALL_DIR="${uv_install_dir}" UV_UNMANAGED_INSTALL="1" sh -s -- -q 2>&1 | sed 's/^/[uv] /' \
    || warn "uv install failed"
  if command -v uv >/dev/null 2>&1; then
    info "uv installed at $(command -v uv)"
  else
    warn "uv not on PATH after install"
  fi
}

# Make nvm/node visible to zsh too (root-only VMs and containers run zsh via
# zellij, so .bashrc-only PATH setup from other installers is not enough).
ensure_node_shell_rc() {
  local marker="# --- Overlord: nvm/node ---"
  local snippet='export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"'
  local target_homes=()
  target_homes+=("${HOME}")
  if [ -d /home/overlord ]; then target_homes+=("/home/overlord"); fi
  if [ -d /root ]; then target_homes+=("/root"); fi
  local uniq=()
  local seen=""
  for target_home in "${target_homes[@]}"; do
    case " $seen " in
      *" $target_home "*) continue ;;
      *) uniq+=("$target_home"); seen="$seen $target_home" ;;
    esac
  done
  for target_home in "${uniq[@]}"; do
    if [ ! -d "$target_home" ]; then continue; fi
    for rc in "$target_home/.zshrc" "$target_home/.bashrc"; do
      touch "$rc"
      if grep -q "Overlord: nvm/node" "$rc" 2>/dev/null; then
        continue
      fi
      { echo ""; echo "$marker"; echo "$snippet"; } >> "$rc"
      info "added nvm/node setup to $rc"
      local owner
      owner="$(stat -c '%U' "$target_home" 2>/dev/null || echo "")"
      if [ -n "$owner" ] && [ "$owner" != "$(whoami)" ]; then
        chown "$owner":"$owner" "$rc" 2>/dev/null || run_sudo chown "$owner":"$owner" "$rc" 2>/dev/null || true
      fi
    done
  done
}

# The prime-agent installer appends its PATH setup to ~/.bashrc only. Mirror
# those lines into ~/.zshrc so prime-agent works in the zsh/zellij default shell.
sync_prime_agent_rc() {
  local bashrc="${HOME}/.bashrc"
  [ -f "$bashrc" ] || return 0
  touch "${HOME}/.zshrc"
  local line
  while IFS= read -r line; do
    case "$line" in ""|"#"*) continue ;; esac
    case "$line" in
      *prime-agent*|*prime_agent*|*NVM_DIR*|*nvm.sh*) ;;
      *) continue ;;
    esac
    if ! grep -qxF "$line" "${HOME}/.zshrc"; then
      printf '%s\n' "$line" >> "${HOME}/.zshrc"
      info "copied to ~/.zshrc: $line"
    fi
  done < "$bashrc"
}

install_codegraph() {
  local want="${CODEGRAPH_VERSION:-1.5.0}"
  if [ -f "$(dirname "$0")/config/tool-versions.env" ]; then
    want="$(grep CODEGRAPH_VERSION "$(dirname "$0")/config/tool-versions.env" | cut -d= -f2 | tr -d ' ' || echo "$want")"
  elif [ -f "/workspace/config/tool-versions.env" ]; then
    want="$(grep CODEGRAPH_VERSION /workspace/config/tool-versions.env | cut -d= -f2 | tr -d ' ' || echo "$want")"
  elif [ -f "/usr/local/share/overlord/config/tool-versions.env" ]; then
    want="$(grep CODEGRAPH_VERSION /usr/local/share/overlord/config/tool-versions.env | cut -d= -f2 | tr -d ' ' || echo "$want")"
  fi
  if command -v codegraph >/dev/null 2>&1; then
    local cur
    cur="$(codegraph --version 2>&1 | grep -oE "[0-9]+\.[0-9]+\.[0-9]+" | head -n1 || true)"
    if [ "$cur" = "$want" ]; then
      info "codegraph $want already installed ($cur)"
      return 0
    fi
    info "codegraph version mismatch (have: $cur, want: $want), reinstalling..."
  else
    info "installing codegraph v$want..."
  fi
  # Prefer npm (available with Node 22), fall back to bun
  if command -v npm >/dev/null 2>&1; then
    if run_sudo npm install -g "@colbymchenry/codegraph@$want" 2>&1 | sed 's/^/[codegraph] /'; then
      info "codegraph installed via npm"
    else
      warn "npm install failed for codegraph"
    fi
  elif command -v bun >/dev/null 2>&1; then
    if bun add -g "@colbymchenry/codegraph@$want" 2>&1 | sed 's/^/[codegraph] /'; then
      info "codegraph installed via bun"
    else
      warn "bun add failed for codegraph"
    fi
  else
    warn "npm and bun not found, skipping codegraph install (install Node.js first)"
    return 0
  fi
  # Ensure binary is in PATH for all users (npm global may be /usr/local/bin or /usr/bin)
  if ! command -v codegraph >/dev/null 2>&1; then
    # Try common npm global bin locations
    for d in /usr/local/bin /usr/bin /opt/node/bin; do
      if [ -x "$d/codegraph" ]; then
        info "codegraph found at $d/codegraph"
        break
      fi
    done
    # Check bun location for current user and overlord
    for d in "$HOME/.bun/bin" "/home/overlord/.bun/bin" "/root/.bun/bin"; do
      if [ -x "$d/codegraph" ]; then
        info "codegraph found at $d/codegraph"
        # Ensure symlink in /usr/local/bin for all users
        run_sudo ln -sf "$d/codegraph" /usr/local/bin/codegraph 2>/dev/null || true
        break
      fi
    done
  fi
  codegraph --version 2>&1 | sed 's/^/[codegraph] /' || true
  # Warm index status (non-blocking, don't fail setup)
  if command -v codegraph >/dev/null 2>&1; then
    codegraph status 2>&1 | head -n 30 | sed 's/^/[codegraph] /' || true
  fi
}

install_nvm_node
install_codegraph
ensure_node_shell_rc
install_uv

# --- oh-my-zsh (unattended, non-interactive) ---
install_oh_my_zsh() {
  local omz_dir="${HOME}/.oh-my-zsh"
  if [ -d "${omz_dir}" ]; then
    info "oh-my-zsh already installed"
    return 0
  fi
  info "installing oh-my-zsh (unattended)..."
  # Use official installer with --unattended; keep existing .zshrc if present
  RUNZSH=no CHSH=no KEEP_ZSHRC=yes \
    sh -c "$(curl -fsSL https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh)" "" --unattended --keep-zshrc 2>&1 | sed 's/^/[omz] /' || true
  if [ ! -d "${omz_dir}" ]; then
    warn "oh-my-zsh install may have failed"
  fi
}
install_oh_my_zsh

# --- zsh plugins: autosuggestions, syntax-highlighting, completions, fzf-tab optional ---
install_zsh_plugins() {
  local custom="${ZSH_CUSTOM:-${HOME}/.oh-my-zsh/custom}"
  mkdir -p "${custom}/plugins"
  # zsh-autocomplete stores recent dirs in ~/.local/share/zsh/chpwd-recent-dirs
  # but never creates the parent dir; without it every cd/completion prints
  # "chpwd_recent_filehandler: no such file or directory"
  mkdir -p "${HOME}/.local/share/zsh"
  # zsh-autosuggestions
  if [ ! -d "${custom}/plugins/zsh-autosuggestions" ]; then
    info "cloning zsh-autosuggestions..."
    git clone --depth=1 https://github.com/zsh-users/zsh-autosuggestions "${custom}/plugins/zsh-autosuggestions"
  else
    info "zsh-autosuggestions already present"
  fi
  # zsh-syntax-highlighting
  if [ ! -d "${custom}/plugins/zsh-syntax-highlighting" ]; then
    info "cloning zsh-syntax-highlighting..."
    git clone --depth=1 https://github.com/zsh-users/zsh-syntax-highlighting "${custom}/plugins/zsh-syntax-highlighting"
  else
    info "zsh-syntax-highlighting already present"
  fi
  # zsh-completions
  if [ ! -d "${custom}/plugins/zsh-completions" ]; then
    info "cloning zsh-completions..."
    git clone --depth=1 https://github.com/zsh-users/zsh-completions "${custom}/plugins/zsh-completions"
  else
    info "zsh-completions already present"
  fi
  # zsh-autocomplete (optional, provides real-time autocomplete)
  if [ ! -d "${custom}/plugins/zsh-autocomplete" ]; then
    info "cloning zsh-autocomplete..."
    git clone --depth=1 https://github.com/marlonrichert/zsh-autocomplete "${custom}/plugins/zsh-autocomplete" 2>&1 | sed 's/^/[zsh-autocomplete] /' || true
  fi

  # Ensure .zshrc loads them (idempotent)
  local zshrc="${HOME}/.zshrc"
  if [ ! -f "${zshrc}" ]; then
    # oh-my-zsh should have created one; create minimal if missing
    cat >"${zshrc}" <<'EOS'
export ZSH="$HOME/.oh-my-zsh"
ZSH_THEME="robbyrussell"
plugins=(git zsh-autosuggestions zsh-syntax-highlighting zsh-completions zsh-autocomplete)
source $ZSH/oh-my-zsh.sh
EOS
    info "created ${zshrc}"
  else
    # Update plugins line if it still is default `plugins=(git)`
    if grep -q '^plugins=(git)' "${zshrc}"; then
      info "updating plugins in ${zshrc}..."
      sed -i 's/^plugins=(git)/plugins=(git zsh-autosuggestions zsh-syntax-highlighting zsh-completions zsh-autocomplete)/' "${zshrc}"
    elif ! grep -q 'zsh-autosuggestions' "${zshrc}"; then
      warn "${zshrc} exists but doesn't contain zsh plugins; please add: plugins=(git zsh-autosuggestions zsh-syntax-highlighting zsh-completions zsh-autocomplete)"
    else
      info "zsh plugins already configured in ${zshrc}"
    fi
    # Ensure fpath for completions is set before compinit (oh-my-zsh does it, but add explicitly)
    if ! grep -q 'zsh-completions' "${zshrc}"; then
      :
    fi
  fi
}

# --- zellij config + autostart on SSH (non-interactive, idempotent) ---
ensure_zellij_config() {
  local target_home
  local target_homes=()
  # Primary HOME
  target_homes+=("$HOME")
  # Original sudo user if any
  if [ -n "${SUDO_USER:-}" ] && [ "${SUDO_USER}" != "root" ] && [ -d "/home/${SUDO_USER}" ]; then
    target_homes+=("/home/${SUDO_USER}")
  fi
  # Overlord user (container)
  if [ -d "/home/overlord" ]; then
    target_homes+=("/home/overlord")
  fi
  if [ -d "/root" ]; then
    target_homes+=("/root")
  fi
  # Workspace user home if different
  # Deduplicate
  local uniq=()
  local seen=""
  for target_home in "${target_homes[@]}"; do
    case " $seen " in
      *" $target_home "*) continue ;;
      *) uniq+=("$target_home"); seen="$seen $target_home" ;;
    esac
  done
  local src_kdl=""
  if [ -f "$(dirname "$0")/config/zellij-config.kdl" ]; then
    src_kdl="$(dirname "$0")/config/zellij-config.kdl"
  elif [ -f "/workspace/config/zellij-config.kdl" ]; then
    src_kdl="/workspace/config/zellij-config.kdl"
  elif [ -f "/usr/local/share/overlord/zellij-config.kdl" ]; then
    src_kdl="/usr/local/share/overlord/zellij-config.kdl"
  elif [ -f "/usr/local/share/overlord/config/zellij-config.kdl" ]; then
    src_kdl="/usr/local/share/overlord/config/zellij-config.kdl"
  fi
  for target_home in "${uniq[@]}"; do
    if [ ! -d "$target_home" ]; then
      continue
    fi
    if [ -n "$src_kdl" ] && [ -f "$src_kdl" ]; then
      mkdir -p "$target_home/.config/zellij"
      if [ ! -f "$target_home/.config/zellij/config.kdl" ] || ! cmp -s "$src_kdl" "$target_home/.config/zellij/config.kdl"; then
        cp "$src_kdl" "$target_home/.config/zellij/config.kdl"
        info "installed zellij config to $target_home/.config/zellij/config.kdl"
      fi
      # Ensure ownership if possible
      local owner
      owner="$(stat -c '%U' "$target_home" 2>/dev/null || echo "")"
      if [ -n "$owner" ] && [ "$owner" != "$(whoami)" ]; then
        chown -R "$owner":"$owner" "$target_home/.config/zellij" 2>/dev/null || run_sudo chown -R "$owner":"$owner" "$target_home/.config/zellij" 2>/dev/null || true
      else
        # try overlord ownership for /home/overlord
        if [ "$target_home" = "/home/overlord" ]; then
          chown -R overlord:overlord "$target_home/.config/zellij" 2>/dev/null || run_sudo chown -R overlord:overlord "$target_home/.config/zellij" 2>/dev/null || true
        fi
      fi
    fi
  done
}

ensure_zellij_autostart() {
  local target_home
  local target_homes=()
  target_homes+=("$HOME")
  if [ -n "${SUDO_USER:-}" ] && [ "${SUDO_USER}" != "root" ] && [ -d "/home/${SUDO_USER}" ]; then
    target_homes+=("/home/${SUDO_USER}")
  fi
  if [ -d "/home/overlord" ]; then
    target_homes+=("/home/overlord")
  fi
  if [ -d "/root" ]; then
    target_homes+=("/root")
  fi
  local uniq=()
  local seen=""
  for target_home in "${target_homes[@]}"; do
    case " $seen " in
      *" $target_home "*) continue ;;
      *) uniq+=("$target_home"); seen="$seen $target_home" ;;
    esac
  done
  local marker="# --- Overlord: auto-start zellij on SSH ---"
  local snippet='if [ -z "${ZELLIJ:-}" ] && [ -t 1 ] && command -v zellij >/dev/null 2>&1; then
  case $- in
    *i*)
      # Interactive shell: auto-attach (SSH has SSH_TTY/SSH_CONNECTION, but case covers all interactive)
      zellij attach --create 2>/dev/null || true
      ;;
  esac
fi'
  for target_home in "${uniq[@]}"; do
    if [ ! -d "$target_home" ]; then
      continue
    fi
    for rc in "$target_home/.zshrc" "$target_home/.bashrc"; do
      # Ensure file exists
      if [ ! -f "$rc" ]; then
        touch "$rc"
      fi
      if grep -q "Overlord: auto-start zellij" "$rc" 2>/dev/null; then
        info "zellij autostart already in $rc"
        continue
      fi
      # Append snippet
      {
        echo ""
        echo "$marker"
        echo "$snippet"
      } >> "$rc"
      info "added zellij autostart to $rc"
      # Fix ownership
      local owner
      owner="$(stat -c '%U' "$target_home" 2>/dev/null || echo "")"
      if [ -n "$owner" ] && [ "$owner" != "$(whoami)" ]; then
        chown "$owner":"$owner" "$rc" 2>/dev/null || run_sudo chown "$owner":"$owner" "$rc" 2>/dev/null || true
      fi
    done
  done
}

install_zsh_plugins
ensure_zellij_config
ensure_zellij_autostart

# --- codegraph skill for prime-agent ---
ensure_codegraph_skill() {
  local src_skill=""
  if [ -f "$(dirname "$0")/.prime/agent/skills/codegraph/SKILL.md" ]; then
    src_skill="$(dirname "$0")/.prime/agent/skills/codegraph/SKILL.md"
    src_skill="$(dirname "$src_skill")"
  elif [ -f "/workspace/.prime/agent/skills/codegraph/SKILL.md" ]; then
    src_skill="/workspace/.prime/agent/skills/codegraph"
  elif [ -f "$(dirname "$0")/skills/codegraph/SKILL.md" ]; then
    src_skill="$(dirname "$0")/skills/codegraph"
  elif [ -f "/workspace/skills/codegraph/SKILL.md" ]; then
    src_skill="/workspace/skills/codegraph"
  elif [ -f "/usr/local/share/overlord/skills/codegraph/SKILL.md" ]; then
    src_skill="/usr/local/share/overlord/skills/codegraph"
  fi
  if [ -z "$src_skill" ] || [ ! -d "$src_skill" ]; then
    return 0
  fi
  local target_homes=()
  target_homes+=("$HOME")
  if [ -n "${SUDO_USER:-}" ] && [ "${SUDO_USER}" != "root" ] && [ -d "/home/${SUDO_USER}" ]; then
    target_homes+=("/home/${SUDO_USER}")
  fi
  if [ -d "/home/overlord" ]; then
    target_homes+=("/home/overlord")
  fi
  if [ -d "/root" ]; then
    target_homes+=("/root")
  fi
  local uniq=()
  local seen=""
  local h
  for h in "${target_homes[@]}"; do
    case " $seen " in
      *" $h "*) continue ;;
      *) uniq+=("$h"); seen="$seen $h" ;;
    esac
  done
  for h in "${uniq[@]}"; do
    if [ ! -d "$h" ]; then
      continue
    fi
    local dest="$h/.prime/agent/skills/codegraph"
    mkdir -p "$dest"
    if [ ! -f "$dest/SKILL.md" ] || ! cmp -s "$src_skill/SKILL.md" "$dest/SKILL.md" 2>/dev/null; then
      cp -r "$src_skill/." "$dest/"
      info "installed codegraph skill to $dest"
    fi
    # Also copy to .agents/skills for backward compat
    local dest2="$h/.agents/skills/codegraph"
    mkdir -p "$dest2"
    if [ ! -f "$dest2/SKILL.md" ] || ! cmp -s "$src_skill/SKILL.md" "$dest2/SKILL.md" 2>/dev/null; then
      cp -r "$src_skill/." "$dest2/"
      info "installed codegraph skill to $dest2"
    fi
    # Fix ownership
    local owner
    owner="$(stat -c '%U' "$h" 2>/dev/null || echo "")"
    if [ -n "$owner" ] && [ "$owner" != "$(whoami)" ]; then
      chown -R "$owner":"$owner" "$h/.prime/agent/skills/codegraph" "$h/.agents/skills/codegraph" 2>/dev/null || run_sudo chown -R "$owner":"$owner" "$h/.prime/agent/skills/codegraph" "$h/.agents/skills/codegraph" 2>/dev/null || true
    fi
  done
  # Also ensure workspace copy exists for container persistence
  if [ -d "/workspace/.prime/agent/skills" ] && [ ! -f "/workspace/.prime/agent/skills/codegraph/SKILL.md" ]; then
    mkdir -p "/workspace/.prime/agent/skills/codegraph"
    cp -r "$src_skill/." "/workspace/.prime/agent/skills/codegraph/"
  fi
}

ensure_codegraph_skill

# --- lazyvim ---
install_lazyvim() {
  local nvim_config="${HOME}/.config/nvim"
  local nvim_data="${HOME}/.local/share/nvim"
  local nvim_state="${HOME}/.local/state/nvim"
  local nvim_cache="${HOME}/.cache/nvim"

  if ! command -v nvim >/dev/null 2>&1; then
    warn "nvim not found, skipping lazyvim"
    return 0
  fi
  # Backup existing config if it's not already lazyvim starter
  if [ -d "${nvim_config}" ] && [ ! -f "${nvim_config}/lua/config/lazy.lua" ] && [ ! -f "${nvim_config}/init.lua" ]; then
    warn "${nvim_config} exists but doesn't look like nvim config, skipping"
    return 0
  fi
  if [ -d "${nvim_config}" ] && [ -f "${nvim_config}/lua/config/lazy.lua" ]; then
    info "lazyvim already installed at ${nvim_config}"
    return 0
  fi
  if [ -d "${nvim_config}" ]; then
    local backup="${nvim_config}.backup.$(date +%Y%m%d%H%M%S)"
    info "backing up existing nvim config to ${backup}"
    mv "${nvim_config}" "${backup}"
    # Also backup share/state/cache to avoid stale
    for p in "${nvim_data}" "${nvim_state}" "${nvim_cache}"; do
      if [ -e "${p}" ]; then
        mv "${p}" "${p}.backup.$(date +%Y%m%d%H%M%S)" 2>/dev/null || true
      fi
    done
  fi
  info "cloning LazyVim starter to ${nvim_config}..."
  git clone --depth=1 "${LAZYVIM_REPO}" "${nvim_config}"
  rm -rf "${nvim_config}/.git"
  info "lazyvim cloned; first launch will install plugins (headless sync)..."
  # Optional headless bootstrap (non-interactive, best-effort, don't fail setup)
  # Use timeout to avoid hanging on lazy sync (network/Mason may stall)
  if command -v timeout >/dev/null 2>&1; then
    if timeout 30 nvim --headless "+Lazy! sync" +qa 2>/dev/null; then
      info "lazyvim plugins synced"
    else
      local rc=$?
      if [ $rc -eq 124 ]; then
        warn "lazyvim headless sync timed out after 60s (will sync on first interactive launch)"
      else
        info "lazyvim headless sync skipped (will sync on first interactive launch)"
      fi
    fi
  else
    # Fallback without timeout, but with background watchdog
    info "timeout not found, skipping headless sync (will sync on first launch)"
  fi
}
install_lazyvim

# --- make zsh default shell (non-interactive) ---

# --- prime-agent + models.json (272k contextWindow override for every model) ---
install_prime_agent() {
  if command -v prime-agent >/dev/null 2>&1; then
    local cur
    cur="$(prime-agent --version 2>&1 | grep -oE "[0-9]+\.[0-9]+\.[0-9]+" | head -n1 || true)"
    local want="${PRIME_AGENT_VERSION:-0.7.4}"
    # try to read pinned version from tool-versions.env
    if [ -f "$(dirname "$0")/config/tool-versions.env" ]; then
      want="$(grep PRIME_AGENT_VERSION "$(dirname "$0")/config/tool-versions.env" | cut -d= -f2 | tr -d ' ' || echo "$want")"
    elif [ -f "/workspace/config/tool-versions.env" ]; then
      want="$(grep PRIME_AGENT_VERSION /workspace/config/tool-versions.env | cut -d= -f2 | tr -d ' ' || echo "$want")"
    elif [ -f "/usr/local/share/overlord/config/tool-versions.env" ]; then
      want="$(grep PRIME_AGENT_VERSION /usr/local/share/overlord/config/tool-versions.env | cut -d= -f2 | tr -d ' ' || echo "$want")"
    fi
    if echo "$cur" | grep -q "$want"; then
      info "prime-agent $want already installed ($cur)"
      return 0
    fi
    info "prime-agent version mismatch (have: $cur, want: $want), reinstalling..."
  else
    info "installing prime-agent..."
  fi
  local want="${PRIME_AGENT_VERSION:-0.7.4}"
  if [ -f "$(dirname "$0")/config/tool-versions.env" ]; then
    want="$(grep PRIME_AGENT_VERSION "$(dirname "$0")/config/tool-versions.env" | cut -d= -f2 | tr -d ' ' || echo "$want")"
  elif [ -f "/workspace/config/tool-versions.env" ]; then
    want="$(grep PRIME_AGENT_VERSION /workspace/config/tool-versions.env | cut -d= -f2 | tr -d ' ' || echo "$want")"
  elif [ -f "/usr/local/share/overlord/config/tool-versions.env" ]; then
    want="$(grep PRIME_AGENT_VERSION /usr/local/share/overlord/config/tool-versions.env | cut -d= -f2 | tr -d ' ' || echo "$want")"
  fi
  local installer
  installer="$(mktemp)"
  if curl -fsSL https://app.primeintellect.ai/prime-agent/install.sh -o "$installer"; then
    PRIME_AGENT_INSTALLER_PLAIN=1 PRIME_AGENT_BOOTSTRAP_KERNEL_ON_INSTALL=0 sh "$installer" "$want" 2>&1 | sed 's/^/[prime-agent] /' || true
    rm -f "$installer"
    if command -v prime-agent >/dev/null 2>&1; then
      prime-agent --version 2>&1 | sed 's/^/[prime-agent] /' || true
    fi
  else
    warn "failed to download prime-agent installer"
    rm -f "$installer"
  fi
}

configure_prime_agent_models() {
  info "configuring Prime Agent models.json with 272k contextWindow override..."
  # Determine agent dirs to configure
  local agent_dirs=()
  agent_dirs+=("$HOME/.prime/agent")
  # Also ensure overlord and root dirs are covered when running as root
  if [ -d "/home/overlord" ]; then
    agent_dirs+=("/home/overlord/.prime/agent")
  fi
  if [ -d "/root" ]; then
    agent_dirs+=("/root/.prime/agent")
  fi
  # Also handle workspace persisted mount (for container)
  if [ -d "/workspace/.overlord/prime-agent-data" ]; then
    agent_dirs+=("/workspace/.overlord/prime-agent-data")
  elif [ -d "./.overlord/prime-agent-data" ]; then
    agent_dirs+=("./.overlord/prime-agent-data")
  fi
  # If running inside container and HOME is overlord but workspace mount exists, ensure it
  if [ -d "/home/overlord/.prime/agent" ]; then
    # ensure workspace copy for persistence
    :
  fi
  if [ -n "${PRIME_AGENT_CODING_AGENT_DIR:-}" ]; then
    agent_dirs+=("$PRIME_AGENT_CODING_AGENT_DIR")
  fi
  if [ -n "${PI_CODING_AGENT_DIR:-}" ]; then
    agent_dirs+=("$PI_CODING_AGENT_DIR")
  fi
  # Deduplicate
  local uniq_dirs=()
  local seen=""
  for d in "${agent_dirs[@]}"; do
    case " $seen " in
      *" $d "*) continue ;;
      *) uniq_dirs+=("$d"); seen="$seen $d" ;;
    esac
  done

  # Reuse an existing models.json if present (e.g. synced from the host by the
  # overlord launcher); only generate a fresh one when no candidate exists.
  local existing_models_json=""
  local candidate
  for candidate in \
    "/workspace/.overlord/prime-agent-data/models.json" \
    "./.overlord/prime-agent-data/models.json"; do
    if [ -f "$candidate" ]; then existing_models_json="$candidate"; break; fi
  done
  if [ -z "$existing_models_json" ]; then
    for d in "${uniq_dirs[@]}"; do
      if [ -f "$d/models.json" ]; then existing_models_json="$d/models.json"; break; fi
    done
  fi
  if [ -n "$existing_models_json" ]; then
    info "reusing existing models.json: $existing_models_json"
  fi
  local tmp_json
  tmp_json="$(mktemp)"
  if [ -z "$existing_models_json" ]; then
  # Generate models.json via python using prime-agent model list if available
  python3 - "$tmp_json" <<'PYEOF'
import subprocess, json, re, sys, os

tmp_path = sys.argv[1]
providers = {}

# Try to discover models via prime-agent model list
models = []
try:
    result = subprocess.run(["prime-agent", "model", "list"], capture_output=True, text=True, timeout=30)
    out = (result.stdout or "") + "\n" + (result.stderr or "")
    for line in out.splitlines():
        if not line.strip():
            continue
        if line.strip().startswith("provider"):
            continue
        # skip warnings
        if "Warning:" in line or "NO_COLOR" in line:
            continue
        m = re.match(r'^(\S+)\s+(\S+)\s+', line)
        if m:
            provider, model = m.group(1).strip(), m.group(2).strip()
            # basic sanity: provider and model should look like identifiers (alphanum, -, /, ., :)
            if re.match(r'^[a-zA-Z0-9._/:@~-]+$', provider) and re.match(r'^[a-zA-Z0-9._/:@~-]+$', model):
                models.append((provider, model))
except Exception as e:
    # fallback will handle
    pass

# If discovery failed or returned few, use fallback list from current snapshot (covers all known providers)
if len(models) < 10:
    # fallback: at least ensure these core providers are covered; also use static list to guarantee coverage
    fallback = [
        ("google-vertex", "gemini-1.5-flash"), ("google-vertex", "gemini-1.5-flash-8b"), ("google-vertex", "gemini-1.5-pro"),
        ("google-vertex", "gemini-2.0-flash"), ("google-vertex", "gemini-2.0-flash-lite"), ("google-vertex", "gemini-2.5-flash"),
        ("opencode", "claude-opus-4-5"), ("opencode", "claude-sonnet-4"), ("opencode", "gpt-5"), ("opencode", "deepseek-v4-pro"),
        ("opencode-go", "deepseek-v4-flash"), ("opencode-go", "muse-spark-1.2-contributor"),
        ("openrouter", "anthropic/claude-opus-4.5"), ("openrouter", "openrouter/auto"),
    ]
    # merge without duplicates
    seen = set(models)
    for p in fallback:
        if p not in seen:
            models.append(p)
            seen.add(p)

# Build providers dict with modelOverrides -> contextWindow 272000
for provider, model in models:
    if provider not in providers:
        providers[provider] = {}
    providers[provider][model] = {"contextWindow": 272000}

# Also ensure we have at least these provider keys even if no models discovered for them yet
for p in ["google-vertex", "opencode", "opencode-go", "openrouter"]:
    providers.setdefault(p, {})

output = {"providers": {prov: {"modelOverrides": overrides} for prov, overrides in providers.items() if overrides}}
# Also handle empty provider case: ensure each core provider has at least an empty dict if needed? Already done.

with open(tmp_path, "w") as f:
    json.dump(output, f, indent=2, sort_keys=True)
    f.write("\n")
print(f"Generated {len(models)} model overrides across {len(providers)} providers", file=sys.stderr)
PYEOF
  fi
  # Copy to each agent dir (from the reused file, or the freshly generated one)
  local models_source="${existing_models_json:-$tmp_json}"
  for d in "${uniq_dirs[@]}"; do
    mkdir -p "$d"
    if [ "$d/models.json" != "$models_source" ]; then
      cp "$models_source" "$d/models.json"
      chmod 644 "$d/models.json" 2>/dev/null || true
    fi
    info "wrote $d/models.json ($(wc -l < "$d/models.json" | tr -d ' ') lines)"
  done
  rm -f "$tmp_json"
  # Fix ownership if overlord user exists
  if [ -d "/home/overlord" ]; then
    run_sudo chown -R overlord:overlord /home/overlord/.prime 2>/dev/null || chown -R overlord:overlord /home/overlord/.prime 2>/dev/null || true
  fi
  if [ -d "/workspace/.overlord/prime-agent-data" ]; then
    chown -R overlord:overlord /workspace/.overlord/prime-agent-data 2>/dev/null || run_sudo chown -R overlord:overlord /workspace/.overlord/prime-agent-data 2>/dev/null || true
  fi
  if [ -d "./.overlord/prime-agent-data" ] && [ "./.overlord/prime-agent-data" != "/workspace/.overlord/prime-agent-data" ]; then
    run_sudo chown -R "$(whoami)":"$(whoami)" ./.overlord/prime-agent-data 2>/dev/null || true
  fi
  # Validate with prime-agent if available
  if command -v prime-agent >/dev/null 2>&1; then
    prime-agent model list 2>&1 | head -n 5 | sed 's/^/[prime-agent] /' || true
  fi
}

make_zsh_default() {
  local zsh_path
  zsh_path="$(command -v zsh 2>/dev/null || true)"
  if [ -z "${zsh_path}" ]; then
    return 0
  fi
  # Determine users to set shell for: current user and original sudo user if different
  local users=()
  users+=("$(whoami)")
  if [ -n "${SUDO_USER:-}" ] && [ "${SUDO_USER}" != "$(whoami)" ] && [ "${SUDO_USER}" != "root" ]; then
    users+=("${SUDO_USER}")
  fi
  # Also ensure overlord user gets zsh if exists
  if id overlord >/dev/null 2>&1; then
    users+=("overlord")
  fi
  local u
  for u in "${users[@]}"; do
    # Skip if already zsh
    local cur_shell
    cur_shell="$(getent passwd "$u" 2>/dev/null | cut -d: -f7 || echo "")"
    if [ "$cur_shell" = "$zsh_path" ]; then
      info "default shell for $u already $zsh_path"
      continue
    fi
    if [ "$(id -u)" -eq 0 ]; then
      # Running as root, can chsh directly
      if chsh -s "$zsh_path" "$u" 2>/dev/null; then
        info "default shell for $u set to $zsh_path"
      else
        warn "could not set default shell for $u"
      fi
    else
      if command -v chsh >/dev/null 2>&1; then
        info "setting zsh as default shell for $u..."
        if sudo -n chsh -s "$zsh_path" "$u" 2>/dev/null; then
          info "default shell for $u set to $zsh_path"
        else
          chsh -s "$zsh_path" "$u" 2>/dev/null && info "default shell for $u set" || warn "could not set default shell for $u (chsh requires password)"
        fi
      fi
    fi
  done
}
install_prime_agent
sync_prime_agent_rc
configure_prime_agent_models
make_zsh_default

info "setup complete. Restart shell or run 'zsh' to use new config."
info "Tools: zsh $(zsh --version 2>/dev/null), nvim $(nvim --version 2>/dev/null | head -n1), zellij $(zellij --version 2>/dev/null), fzf $(fzf --version 2>/dev/null)"
