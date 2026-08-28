#!/usr/bin/env bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

# setup.sh - Standalone VM + container initializer
# Idempotent, non-interactive. Installs: zsh, oh-my-zsh, zsh-autosuggestions,
# zsh-syntax-highlighting, zsh-completions, zellij, lazyvim (neovim + LazyVim starter),
# codegraph (local code intelligence), prime-agent (256k contextWindow override),
# DeepSeek Harness (dsh), and Oh My Pi (omp).
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

# --- AWS CLI v2 ---
install_aws_cli() {
  if command -v aws >/dev/null 2>&1 && aws --version >/dev/null 2>&1; then
    info "aws cli already installed ($(aws --version 2>&1 | awk '{print $2}'))"
    return 0
  fi
  info "installing aws cli v2..."
  curl -fsSL https://awscli.amazonaws.com/v2/install.sh | bash 2>&1 | sed 's/^/[aws] /' \
    || warn "aws cli installer returned an error"
  if command -v aws >/dev/null 2>&1; then
    info "aws cli installed at $(command -v aws)"
  else
    warn "aws cli not available after install"
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

# Make user and nvm tools visible in every shell startup path. SSH login bash
# reads .bash_profile/.profile, while zellij zsh reads .zprofile/.zshrc.
ensure_node_shell_rc() {
  local marker="# --- Overlord: persistent tool PATH v2 ---"
  local snippet='export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
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
    for rc in \
      "$target_home/.zshrc" \
      "$target_home/.zprofile" \
      "$target_home/.bashrc" \
      "$target_home/.bash_profile" \
      "$target_home/.profile"; do
      touch "$rc"
      if grep -q "Overlord: persistent tool PATH v2" "$rc" 2>/dev/null; then
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

# Publish user/nvm-installed commands at a stable system path. This makes
# commands work inside zellij zsh even if a login shell did not source nvm.
find_tool_command() {
  local command_name="$1"
  local found
  found="$(command -v "$command_name" 2>/dev/null || true)"
  if [ -n "$found" ] && [ -x "$found" ]; then
    printf '%s\n' "$found"
    return 0
  fi
  local candidate
  for candidate in \
    "$HOME/.local/bin/$command_name" \
    "$HOME/.prime/bin/$command_name" \
    "$HOME/.prime/agent/bin/$command_name" \
    "${NVM_DIR:-$HOME/.nvm}"/versions/node/*/bin/"$command_name"; do
    if [ -x "$candidate" ]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

publish_tool_commands() {
  local command_name source destination
  for command_name in node npm npx corepack prime-agent codegraph uv aws dsh omp; do
    source="$(find_tool_command "$command_name" || true)"
    if [ -z "$source" ]; then
      continue
    fi
    destination="/usr/local/bin/$command_name"
    if [ "$source" = "$destination" ]; then
      continue
    fi
    if [ "$(id -u)" -eq 0 ]; then
      ln -sf "$source" "$destination"
    else
      run_sudo ln -sf "$source" "$destination" 2>/dev/null || true
    fi
    if [ -x "$destination" ]; then
      info "published $command_name at $destination"
    fi
  done
  hash -r 2>/dev/null || true
}

# Interactive shells/zellij run as the 'overlord' user while this setup often
# runs as root. Published /usr/local/bin symlinks resolve through $HOME/.nvm,
# so /root must be traversable for overlord. Mode 711 allows traversal without
# listing; files inside keep their own permissions.
ensure_cross_user_tool_access() {
  [ -d /home/overlord ] || return 0
  if [ "$(stat -c '%a' /root 2>/dev/null || echo "")" = "711" ]; then
    return 0
  fi
  if [ "$(id -u)" -eq 0 ]; then
    chmod 711 /root && info "made /root traversable (711) so the overlord user can run published tools"
  else
    run_sudo chmod 711 /root 2>/dev/null || warn "could not make /root traversable; overlord may hit 'permission denied' on root-published tools"
  fi
}

verify_login_shell_tools() {
  if ! command -v zsh >/dev/null 2>&1; then
    return 0
  fi
  local command_name target_home
  while IFS= read -r target_home; do
    for command_name in node npm prime-agent omp; do
      if env -i HOME="$target_home" USER="$(stat -c '%U' "$target_home" 2>/dev/null || id -un)" TERM="${TERM:-xterm}" \
        PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
        zsh -lic "command -v $command_name" >/dev/null 2>&1; then
        info "$command_name available in a clean zsh login ($target_home)"
      else
        warn "$command_name is not available in a clean zsh login ($target_home)"
      fi
    done
  done < <(omz_target_homes)
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
  # Prefer npm (installed with Node 24 above), fall back to bun
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
install_aws_cli

# --- oh-my-zsh (unattended, non-interactive) ---
# Setup may run as root while interactive shells/zellij run as the 'overlord'
# user. Provision oh-my-zsh in every target home so no shell misses plugins.
omz_target_homes() {
  printf '%s\n' "${HOME}"
  if [ -d /home/overlord ] && [ "$(realpath /home/overlord 2>/dev/null)" != "$(realpath "${HOME}" 2>/dev/null)" ]; then
    printf '%s\n' "/home/overlord"
  fi
}

fix_home_ownership() {
  local target owner
  for target in "$@"; do
    [ -e "$target" ] || continue
    owner="$(stat -c '%U' "$target" 2>/dev/null || echo "")"
    if [ -n "$owner" ] && [ "$owner" != "$(whoami)" ]; then
      chown -R "$owner":"$owner" "$target" 2>/dev/null || run_sudo chown -R "$owner":"$owner" "$target" 2>/dev/null || true
    elif [ "$target" = "/home/overlord" ] && [ "$(id -u)" -eq 0 ]; then
      chown -R overlord:overlord "$target" 2>/dev/null || run_sudo chown -R overlord:overlord "$target" 2>/dev/null || true
    fi
  done
}

install_oh_my_zsh_for() {
  local target_home="$1"
  local omz_dir="${target_home}/.oh-my-zsh"
  if [ -f "${omz_dir}/oh-my-zsh.sh" ]; then
    info "oh-my-zsh already installed for ${target_home}"
    return 0
  fi
  if [ "$target_home" = "$HOME" ]; then
    info "installing oh-my-zsh (unattended)..."
    # Use official installer with --unattended; keep existing .zshrc if present
    RUNZSH=no CHSH=no KEEP_ZSHRC=yes \
      sh -c "$(curl -fsSL https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh)" "" --unattended --keep-zshrc 2>&1 | sed 's/^/[omz] /' || true
    if [ ! -f "${omz_dir}/oh-my-zsh.sh" ]; then
      warn "oh-my-zsh install may have failed"
      return 0
    fi
  elif [ -f "${HOME}/.oh-my-zsh/oh-my-zsh.sh" ]; then
    info "copying oh-my-zsh to ${target_home}..."
    mkdir -p "$target_home"
    cp -a "${HOME}/.oh-my-zsh/." "${omz_dir}/"
  else
    info "installing oh-my-zsh into ${target_home} (unattended)..."
    HOME="$target_home" RUNZSH=no CHSH=no KEEP_ZSHRC=yes \
      sh -c "$(curl -fsSL https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh)" "" --unattended --keep-zshrc 2>&1 | sed 's/^/[omz] /' || true
  fi
  fix_home_ownership "$omz_dir"
}

install_oh_my_zsh() {
  local target_home
  while IFS= read -r target_home; do
    install_oh_my_zsh_for "$target_home"
  done < <(omz_target_homes)
}
install_oh_my_zsh

# --- zsh plugins: autosuggestions, syntax-highlighting, completions, fzf-tab optional ---
install_zsh_plugins() {
  local target_home custom
  while IFS= read -r target_home; do
    install_zsh_plugins_for "$target_home"
  done < <(omz_target_homes)
}

install_zsh_plugins_for() {
  local target_home="$1"
  local custom="${ZSH_CUSTOM:-${target_home}/.oh-my-zsh/custom}"
  mkdir -p "${custom}/plugins"
  # zsh-autocomplete stores recent dirs in ~/.local/share/zsh/chpwd-recent-dirs
  # but never creates the parent dir; without it every cd/completion prints
  # "chpwd_recent_filehandler: no such file or directory"
  mkdir -p "${target_home}/.local/share/zsh"
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

  # Ensure .zshrc / .zshenv load plugins correctly (idempotent).
  configure_overlord_zsh_files "${target_home}"
  fix_home_ownership "${target_home}/.oh-my-zsh" "${target_home}/.local/share/zsh" "${target_home}/.zshrc" "${target_home}/.zshenv"
}

# Replace one Overlord-managed shell block identified by its marker prefix.
# Reads the replacement block from stdin so multiline content stays intact.
upsert_overlord_shell_block() {
  local rc="$1"
  local marker_prefix="$2"
  local blockfile
  touch "$rc"
  blockfile="$(mktemp)"
  cat >"$blockfile"
  python3 - "$rc" "$marker_prefix" "$blockfile" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
prefix = sys.argv[2]
block_path = Path(sys.argv[3])
block = block_path.read_text().replace("\r\n", "\n").strip("\n")
block_path.unlink(missing_ok=True)
raw = path.read_text() if path.exists() else ""
lines = raw.splitlines(keepends=True)
out: list[str] = []
i = 0
marker_start = f"# --- {prefix}"
replaced = False
while i < len(lines):
    line = lines[i]
    if line.startswith(marker_start):
        i += 1
        while i < len(lines) and not lines[i].startswith("# --- "):
            i += 1
        if i < len(lines) and lines[i].strip() == "":
            i += 1
        if block and not replaced:
            if out and out[-1].strip() != "":
                out.append("\n")
            out.append(block + "\n")
            replaced = True
        continue
    out.append(line)
    i += 1
text = "".join(out).rstrip()
if block and not replaced:
    text = (text + "\n\n" if text else "") + block
if text:
    text += "\n"
path.write_text(text)
PY
}

# Source zsh-autocomplete just before the first oh-my-zsh.sh line.
insert_autocomplete_before_omz() {
  local zshrc="$1"
  python3 - "$zshrc" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text() if path.exists() else ""
marker = "# --- Overlord: zsh-autocomplete before compinit ---"
snippet = (
    marker + "\n"
    'if [ -f "${ZSH:-$HOME/.oh-my-zsh}/custom/plugins/zsh-autocomplete/zsh-autocomplete.plugin.zsh" ]; then\n'
    '  source "${ZSH:-$HOME/.oh-my-zsh}/custom/plugins/zsh-autocomplete/zsh-autocomplete.plugin.zsh"\n'
    "fi\n"
)
if "zsh-autocomplete.plugin.zsh" in text:
    raise SystemExit(0)
needle = "source $ZSH/oh-my-zsh.sh"
alt = "source ${ZSH}/oh-my-zsh.sh"
idx = text.find(needle)
if idx < 0:
    idx = text.find(alt)
    needle = alt if idx >= 0 else ""
if idx < 0:
    text = text.rstrip() + "\n\n" + snippet
else:
    text = text[:idx] + snippet + text[idx:]
path.write_text(text if text.endswith("\n") else text + "\n")
PY
}

configure_overlord_zsh_files() {
  local target_home="$1"
  local zshrc="${target_home}/.zshrc"
  local zshenv="${target_home}/.zshenv"
  mkdir -p "${target_home}"
  touch "${zshrc}" "${zshenv}"

  upsert_overlord_shell_block "${zshenv}" "Overlord: skip Ubuntu global compinit" <<'EOS'
# --- Overlord: skip Ubuntu global compinit ---
# Ubuntu /etc/zsh/zshrc runs compinit before ~/.zshrc. That dump never includes
# zsh-autocomplete helpers, so Tab later prints "_autocomplete__unambiguous not found".
skip_global_compinit=1
EOS

  # Never keep zsh-autocomplete as an Oh My Zsh plugin; it must load before compinit.
  if grep -qE '^plugins=\(.*zsh-autocomplete' "${zshrc}"; then
    # Use a backup suffix so this works with both GNU and BSD sed.
    sed -i.bak -E '/^plugins=[(]/ s/[[:space:]]*zsh-autocomplete//g' "${zshrc}"
    rm -f "${zshrc}.bak"
  fi

  if grep -q 'Overlord: oh-my-zsh' "${zshrc}" || ! grep -q 'oh-my-zsh.sh' "${zshrc}"; then
    upsert_overlord_shell_block "${zshrc}" "Overlord: oh-my-zsh" <<'EOS'
# --- Overlord: oh-my-zsh ---
export ZSH="$HOME/.oh-my-zsh"
ZSH_THEME="robbyrussell"
plugins=(git zsh-autosuggestions zsh-syntax-highlighting zsh-completions)
# Source autocomplete before omz so Completions are on fpath for compinit.
# Loading it as an omz plugin runs after OMZ compinit and leaves helpers unloaded.
if [ -f "${ZSH:-$HOME/.oh-my-zsh}/custom/plugins/zsh-autocomplete/zsh-autocomplete.plugin.zsh" ]; then
  source "${ZSH:-$HOME/.oh-my-zsh}/custom/plugins/zsh-autocomplete/zsh-autocomplete.plugin.zsh"
fi
source $ZSH/oh-my-zsh.sh
EOS
    info "ensured oh-my-zsh bootstrap in ${zshrc}"
  else
    if grep -qE '^plugins=\(git\)[[:space:]]*$' "${zshrc}"; then
      info "updating plugins in ${zshrc}..."
      sed -i 's/^plugins=(git)/plugins=(git zsh-autosuggestions zsh-syntax-highlighting zsh-completions)/' "${zshrc}"
    elif ! grep -q 'zsh-autosuggestions' "${zshrc}"; then
      warn "${zshrc} exists but doesn't contain zsh plugins; please add: plugins=(git zsh-autosuggestions zsh-syntax-highlighting zsh-completions)"
    else
      info "zsh plugins already configured in ${zshrc}"
    fi
    insert_autocomplete_before_omz "${zshrc}"
    info "sourced zsh-autocomplete before oh-my-zsh in ${zshrc}"
  fi

  # Stale dumps from Ubuntu global compinit / old plugin order omit autocomplete helpers.
  rm -f "${target_home}/.zcompdump" "${target_home}/.zcompdump"-* "${target_home}/.cache/zsh/compdump" 2>/dev/null || true
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
  local marker_prefix="Overlord: auto-start zellij"
  for target_home in "${uniq[@]}"; do
    if [ ! -d "$target_home" ]; then
      continue
    fi
    for rc in "$target_home/.zshrc" "$target_home/.bashrc"; do
      upsert_overlord_shell_block "$rc" "$marker_prefix" <<'EOS'
# --- Overlord: auto-start zellij on SSH ---
# exec replaces this shell so detach/quit actually closes the connection
# instead of dropping you into a leftover parent shell.
if [ -z "${ZELLIJ:-}" ] && [ -t 1 ] && command -v zellij >/dev/null 2>&1; then
  case $- in
    *i*)
      exec zellij attach --create
      ;;
  esac
fi
EOS
      info "ensured zellij autostart in $rc"
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

# --- prime-agent + models.json (256k contextWindow override for every model) ---
install_prime_agent() {
  if command -v prime-agent >/dev/null 2>&1; then
    local cur
    cur="$(prime-agent --version 2>&1 | grep -oE "[0-9]+\.[0-9]+\.[0-9]+" | head -n1 || true)"
    local want="${PRIME_AGENT_VERSION:-0.8.0}"
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
  local want="${PRIME_AGENT_VERSION:-0.8.0}"
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

# DeepSeek Harness (dsh) — alternative agent harness, installed from npm.
# https://github.com/deepseek-ai/deepseek-harness
DSH_NPM_PACKAGE="@deepseek-ai/dsh"
# Native deps need postinstall scripts; npm's allow-scripts policy blocks them
# by default and dsh breaks without them (node-pty/koffi bindings).
DSH_ALLOWED_SCRIPTS="@deepseek-ai/dsh-subprocess-local,koffi,node-pty,@google/genai,protobufjs"

dsh_want_version() {
  local want="${DSH_VERSION:-}"
  if [ -f "$(dirname "$0")/config/tool-versions.env" ]; then
    want="$(grep DSH_VERSION "$(dirname "$0")/config/tool-versions.env" | cut -d= -f2 | tr -d ' ' || echo "$want")"
  elif [ -f "/workspace/config/tool-versions.env" ]; then
    want="$(grep DSH_VERSION /workspace/config/tool-versions.env | cut -d= -f2 | tr -d ' ' || echo "$want")"
  elif [ -f "/usr/local/share/overlord/config/tool-versions.env" ]; then
    want="$(grep DSH_VERSION /usr/local/share/overlord/config/tool-versions.env | cut -d= -f2 | tr -d ' ' || echo "$want")"
  fi
  echo "$want"
}

install_dsh() {
  if ! command -v node >/dev/null 2>&1; then
    warn "node unavailable; skipping DeepSeek Harness install"
    return 0
  fi
  local cur=""
  if command -v dsh >/dev/null 2>&1; then
    cur="$(dsh --version 2>/dev/null | grep -oE "[0-9]+\.[0-9]+\.[0-9]+[^ ]*" | head -n1 || true)"
  fi
  local want
  want="$(dsh_want_version)"
  if [ -n "$cur" ]; then
    if [ -z "$want" ] || echo "$cur" | grep -q "^$want"; then
      info "DeepSeek Harness already installed (dsh $cur)"
      return 0
    fi
    info "DeepSeek Harness version mismatch (have: $cur, want: $want), reinstalling..."
  else
    info "installing DeepSeek Harness (dsh)..."
  fi
  local spec="${DSH_NPM_PACKAGE}"
  [ -n "$want" ] && spec="@deepseek-ai/dsh@${want}"
  if npm install -g "$spec" --allow-scripts="$DSH_ALLOWED_SCRIPTS" 2>&1 | sed 's/^/[dsh] /'; then
    if command -v dsh >/dev/null 2>&1; then
      info "dsh $(dsh --version 2>/dev/null | head -n1) installed"
    else
      warn "dsh not on PATH after install"
    fi
  else
    warn "failed to install $spec via npm"
  fi
}

# Oh My Pi (omp) — alternative Pi-based coding-agent harness.
# https://omp.sh
omp_is_available() {
  local binary="$1"
  if [ -x "$binary" ]; then
    "$binary" --version >/dev/null 2>&1
  elif command -v omp >/dev/null 2>&1; then
    omp --version >/dev/null 2>&1
  else
    return 1
  fi
}

install_oh_my_pi() {
  local install_dir="${HOME}/.local/bin"
  # setup.sh runs as root while the container's interactive shell runs as
  # overlord. Install into a system directory in that case instead of leaving
  # omp under /root, where the interactive user cannot use it.
  if [ "$(id -u)" -eq 0 ]; then
    install_dir="/usr/local/bin"
  fi
  local local_binary="${install_dir}/omp"
  if omp_is_available "$local_binary"; then
    info "Oh My Pi already installed"
    return 0
  fi

  info "installing Oh My Pi (omp)..."
  # The official installer chooses a matching prebuilt binary when Bun is not
  # available. If Bun is present, force the binary mode so PI_INSTALL_DIR is
  # honored and omp is still available to every package user.
  local installer_ok=1
  if command -v bun >/dev/null 2>&1; then
    if PI_INSTALL_DIR="$install_dir" sh -c 'curl -fsSL https://omp.sh/install | sh -s -- --binary' 2>&1 | sed 's/^/[omp] /'; then
      installer_ok=0
    fi
  elif PI_INSTALL_DIR="$install_dir" sh -c 'curl -fsSL https://omp.sh/install | sh' 2>&1 | sed 's/^/[omp] /'; then
    installer_ok=0
  fi
  if [ "$installer_ok" -ne 0 ]; then
    warn "failed to install Oh My Pi (omp)"
    return 0
  fi

  if omp_is_available "$local_binary"; then
    info "Oh My Pi (omp) installed"
  else
    warn "Oh My Pi installer completed but omp is not available"
  fi
}

install_prime_agent_skills() {
  if ! command -v npx >/dev/null 2>&1; then
    warn "npx unavailable; skipping Prime Agent skill installation"
    return 0
  fi
  info "installing shared skills for the Pi-based Prime Agent..."
  local skill_source
  for skill_source in mattpocock/skills aws/agent-toolkit-for-aws; do
    if npx --yes skills add "$skill_source" --global --agent pi --yes --copy --full-depth \
      2>&1 | sed "s|^|[skills:$skill_source] |"; then
      info "installed skills from $skill_source"
    else
      warn "failed to install skills from $skill_source"
    fi
  done

  # The skills CLI knows Pi as ~/.pi/agent, while Prime Agent uses
  # ~/.prime/agent. Copy the installed Pi skills into every Prime Agent home.
  local pi_skills="$HOME/.pi/agent/skills"
  local target_homes=("$HOME")
  if [ -d /home/overlord ]; then target_homes+=("/home/overlord"); fi
  if [ -d /root ]; then target_homes+=("/root"); fi
  local target_home prime_skills owner
  if [ -d "$pi_skills" ]; then
    for target_home in "${target_homes[@]}"; do
      prime_skills="$target_home/.prime/agent/skills"
      mkdir -p "$prime_skills"
      cp -a "$pi_skills/." "$prime_skills/"
      owner="$(stat -c '%U' "$target_home" 2>/dev/null || true)"
      if [ -n "$owner" ]; then
        chown -R "$owner":"$owner" "$prime_skills" 2>/dev/null || true
      fi
      info "synced Pi skills to $prime_skills"
    done
  else
    warn "Pi skills directory was not created: $pi_skills"
  fi

  # The AWS setup URL is an interactive workflow, not a skills CLI package.
  # Install it as a local skill so Prime/Pi can guide login/profile setup later.
  local aws_setup_url="https://raw.githubusercontent.com/aws/agent-toolkit-for-aws/refs/heads/main/setup-instructions/setup.md"
  local aws_setup_tmp
  aws_setup_tmp="$(mktemp)"
  if curl -fsSL "$aws_setup_url" -o "$aws_setup_tmp"; then
    local agent_skills skill_dir
    for target_home in "${target_homes[@]}"; do
      for agent_skills in "$target_home/.pi/agent/skills" "$target_home/.prime/agent/skills"; do
        skill_dir="$agent_skills/aws-agent-toolkit-setup"
        mkdir -p "$skill_dir"
        {
          printf '%s\n' '---'
          printf '%s\n' 'name: aws-agent-toolkit-setup'
          printf '%s\n' 'description: Guide interactive AWS login, profile, region, Agent Toolkit, MCP, and AWS skill setup.'
          printf '%s\n' '---' ''
          printf 'Upstream instructions: %s\n\n' "$aws_setup_url"
          cat "$aws_setup_tmp"
        } > "$skill_dir/SKILL.md"
      done
      owner="$(stat -c '%U' "$target_home" 2>/dev/null || true)"
      if [ -n "$owner" ]; then
        chown -R "$owner":"$owner" "$target_home/.pi" "$target_home/.prime" 2>/dev/null || true
      fi
    done
    info "installed AWS Agent Toolkit setup skill"
  else
    warn "failed to download AWS Agent Toolkit setup instructions"
  fi
  rm -f "$aws_setup_tmp"
}

configure_prime_agent_tools() {
  info "enabling Prime Agent web search and Context7 tools..."
  local settings_paths=()
  settings_paths+=("$HOME/.prime/agent/settings.json")
  if [ -d /home/overlord ]; then settings_paths+=("/home/overlord/.prime/agent/settings.json"); fi
  if [ -d /root ]; then settings_paths+=("/root/.prime/agent/settings.json"); fi
  if [ -d /workspace/.overlord/prime-agent-data ]; then
    settings_paths+=("/workspace/.overlord/prime-agent-data/settings.json")
  elif [ -d ./.overlord/prime-agent-data ]; then
    settings_paths+=("./.overlord/prime-agent-data/settings.json")
  fi
  if [ -n "${PRIME_AGENT_CODING_AGENT_DIR:-}" ]; then
    settings_paths+=("$PRIME_AGENT_CODING_AGENT_DIR/settings.json")
  fi
  if [ -n "${PI_CODING_AGENT_DIR:-}" ]; then
    settings_paths+=("$PI_CODING_AGENT_DIR/settings.json")
  fi

  python3 - "${settings_paths[@]}" <<'PYEOF'
import json
import os
from pathlib import Path
import sys


def parse_jsonc(text: str) -> dict:
    """Parse Prime's JSON-with-comments/trailing-commas settings safely."""
    cleaned = []
    i = 0
    in_string = False
    escaped = False
    while i < len(text):
        ch = text[i]
        if in_string:
            cleaned.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            cleaned.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < len(text) and text[i + 1] == "/":
            i += 2
            while i < len(text) and text[i] not in "\r\n":
                i += 1
            continue
        if ch == "/" and i + 1 < len(text) and text[i + 1] == "*":
            i += 2
            while i + 1 < len(text) and text[i : i + 2] != "*/":
                i += 1
            i += 2
            continue
        cleaned.append(ch)
        i += 1

    text = "".join(cleaned)
    result = []
    i = 0
    in_string = False
    escaped = False
    while i < len(text):
        ch = text[i]
        if in_string:
            result.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            result.append(ch)
            i += 1
            continue
        if ch == ",":
            j = i + 1
            while j < len(text) and text[j].isspace():
                j += 1
            if j < len(text) and text[j] in "}]":
                i += 1
                continue
        result.append(ch)
        i += 1
    parsed = json.loads("".join(result))
    if not isinstance(parsed, dict):
        raise ValueError("settings root must be an object")
    return parsed


seen = set()
for raw_path in sys.argv[1:]:
    path = Path(raw_path).expanduser()
    key = str(path.resolve(strict=False))
    if key in seen:
        continue
    seen.add(key)
    try:
        settings = parse_jsonc(path.read_text()) if path.is_file() else {}
        settings["enableBuiltinSkills"] = True
        bundled = settings.setdefault("bundledSkills", {})
        bundled["websearch"] = True
        servers = settings.setdefault("mcpServers", {})
        servers["context7"] = {
            "type": "http",
            "url": "https://mcp.context7.com/mcp",
            "enabled": True,
        }
        # Remove the formerly managed Runpod Docs server on upgrade.
        servers.pop("runpod-docs", None)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(settings, indent=2, sort_keys=True) + "\n")
        path.chmod(0o644)
        print(f"configured {path}")
    except Exception as error:
        print(f"could not update {path}: {error}", file=sys.stderr)
        raise
PYEOF

  # Add routing instructions so Prime knows when to reach for the MCP tools.
  local agent_dirs=("$HOME/.prime/agent")
  if [ -d /home/overlord ]; then agent_dirs+=("/home/overlord/.prime/agent"); fi
  if [ -d /root ]; then agent_dirs+=("/root/.prime/agent"); fi
  local agent_dir
  for agent_dir in "${agent_dirs[@]}"; do
    mkdir -p "$agent_dir/skills/context7"
    rm -rf "$agent_dir/skills/runpod-docs"
    cat > "$agent_dir/skills/context7/SKILL.md" <<'SKILLEOF'
---
name: context7
description: Look up current library and framework documentation through Context7 MCP. Use when API details, current examples, configuration, or version-specific behavior are needed.
---

# Context7

Use the tools exposed by the `context7` MCP server to resolve a library and retrieve its current documentation. Prefer Context7 over memory when implementation depends on current APIs or version-specific behavior.
SKILLEOF
  done
  if [ -d /home/overlord ]; then
    chown -R overlord:overlord /home/overlord/.prime 2>/dev/null || true
  fi
  info "websearch enabled (one-time Serper login: prime-agent /login -> MCP Connections -> Serper)"
  info "Context7 MCP server configured (no login required)"
}

configure_prime_agent_models() {
  info "configuring Prime Agent models.json with 256k contextWindow override..."
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
    # Patch existing file to ensure 256k and Grok 4.6 on Azure (handles old 272k workspaces)
    python3 - "$existing_models_json" <<'PYEOF_PATCH'
import json, sys
from pathlib import Path
path = Path(sys.argv[1])
try:
    data = json.loads(path.read_text())
except Exception as e:
    print(f"could not patch {path}: {e}", file=sys.stderr)
    sys.exit(0)
changed=False
AZURE_BASEURL="https://YOUR-RESOURCE-NAME.openai.azure.com/openai/v1"
defaults=data.setdefault("defaults",{})
for k in ("contextWindow","maxInputTokens","limitTokens"):
    if defaults.get(k)!=256000:
        defaults[k]=256000
        changed=True
if defaults.get("reasoning") is not True:
    defaults["reasoning"]=True
    changed=True
providers=data.setdefault("providers",{})
desired_explicit={
    "azure-openai-responses": [
        {"id": "gpt-5.6-sol", "name": "GPT-5.6 Sol (256k)", "contextWindow": 256000, "maxInputTokens": 256000, "limitTokens": 256000, "maxTokens": 16384, "reasoning": True, "baseUrl": AZURE_BASEURL},
        {"id": "grok-4.6", "name": "Grok 4.6 (256k)", "contextWindow": 256000, "maxInputTokens": 256000, "limitTokens": 256000, "maxTokens": 16384, "reasoning": False, "baseUrl": AZURE_BASEURL},
    ],
    "google-vertex": [
        {"id": "gemini-3.7-flash", "name": "Gemini 3.7 Flash (256k)", "contextWindow": 256000, "maxInputTokens": 256000, "limitTokens": 256000, "maxTokens": 16384, "reasoning": True, "input": ["text", "image"]},
    ],
    "opencode": [
        {"id": "gpt-5.6-sol", "name": "GPT-5.6 Sol (256k)", "contextWindow": 256000, "maxInputTokens": 256000, "limitTokens": 256000, "maxTokens": 16384, "reasoning": True},
    ],
    "opencode-go": [
        {"id": "muse-spark-1.2-contributor", "name": "Muse Spark 1.2 Contributor (256k)", "contextWindow": 256000, "maxInputTokens": 256000, "limitTokens": 256000, "maxTokens": 16384, "reasoning": True},
        {"id": "muse-spark-1.2-contributor-free", "name": "Muse Spark 1.2 Contributor Free (256k)", "contextWindow": 256000, "maxInputTokens": 256000, "limitTokens": 256000, "maxTokens": 16384, "reasoning": True},
        {"id": "muse-spark-1.2-free", "name": "Muse Spark 1.2 Free (256k)", "contextWindow": 256000, "maxInputTokens": 256000, "limitTokens": 256000, "maxTokens": 16384, "reasoning": True},
    ],
}
allowed_ids={"gpt-5.6-sol","grok-4.6","gemini-3.7-flash","muse-spark-1.2-contributor","muse-spark-1.2-contributor-free","muse-spark-1.2-free"}
for prov, explicit_models in desired_explicit.items():
    prov_cfg=providers.setdefault(prov,{})
    overrides=prov_cfg.setdefault("modelOverrides",{})
    wildcard={"contextWindow": 256000, "maxInputTokens": 256000, "limitTokens": 256000, "reasoning": True}
    if overrides.get("*")!=wildcard:
        overrides["*"]=wildcard
        changed=True
    for m in explicit_models:
        mid=m["id"]
        if mid not in overrides or overrides[mid].get("contextWindow")!=256000:
            overrides[mid]={"contextWindow": 256000}
            changed=True
    existing_models=prov_cfg.get("models",[])
    existing_ids={mm.get("id") for mm in existing_models}
    for m in explicit_models:
        if m["id"] not in existing_ids:
            existing_models.append(m)
            changed=True
        else:
            for em in existing_models:
                if em.get("id")==m["id"]:
                    for kk in ("contextWindow","maxInputTokens","limitTokens"):
                        if em.get(kk)!=256000:
                            em[kk]=256000
                            changed=True
                    if "256k" not in em.get("name",""):
                        em["name"]=m["name"]
                        changed=True
    prov_cfg["models"]=[mm for mm in existing_models if mm.get("id") in allowed_ids]
    # filter overrides
    prov_cfg["modelOverrides"]={k:v for k,v in overrides.items() if k=="*" or k in allowed_ids}
    if len(prov_cfg["modelOverrides"])!=len(overrides):
        changed=True
# Remove x-preview, luna, etc. and providers without allowed
for prov in list(providers.keys()):
    if prov not in desired_explicit:
        has_allowed=any(k in allowed_ids for k in providers[prov].get("modelOverrides",{}).keys())
        if not has_allowed:
            del providers[prov]
            changed=True
    if prov=="opencode":
        for k in list(providers[prov].get("modelOverrides",{}).keys()):
            if "muse-spark" in k:
                del providers[prov]["modelOverrides"][k]
                changed=True
        for mm in list(providers[prov].get("models",[])):
            if "muse-spark" in mm.get("id",""):
                providers[prov]["models"].remove(mm)
                changed=True
# Clean empty providers
for prov in list(providers.keys()):
    if not providers[prov].get("modelOverrides") and not providers[prov].get("models"):
        del providers[prov]
        changed=True
# Ensure defaults present
if "defaults" not in data:
    data["defaults"]={"contextWindow":256000,"maxInputTokens":256000,"limitTokens":256000,"reasoning":True}
    changed=True
if changed:
    path.write_text(json.dumps(data, indent=2, sort_keys=True)+"\n")
    print(f"patched {path} to 256k/Grok", file=sys.stderr)
PYEOF_PATCH
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
        ("google-vertex", "gemini-3.7-flash"), ("google-vertex", "gemini-1.5-flash"), ("google-vertex", "gemini-1.5-flash-8b"), ("google-vertex", "gemini-1.5-pro"),
        ("google-vertex", "gemini-2.0-flash"), ("google-vertex", "gemini-2.0-flash-lite"), ("google-vertex", "gemini-2.5-flash"),
        ("opencode", "gpt-5.6-sol"), ("opencode", "claude-opus-4-5"), ("opencode", "claude-sonnet-4"), ("opencode", "gpt-5"), ("opencode", "deepseek-v4-pro"),
        ("opencode-go", "muse-spark-1.2-contributor"), ("opencode-go", "muse-spark-1.2-contributor-free"), ("opencode-go", "muse-spark-1.2-free"),
        ("azure-openai-responses", "grok-4.6"), ("azure-openai-responses", "gpt-5.6-sol"),
        ("openrouter", "anthropic/claude-opus-4.5"), ("openrouter", "openrouter/auto"),
    ]
    # merge without duplicates
    seen = set(models)
    for p in fallback:
        if p not in seen:
            models.append(p)
            seen.add(p)

# Ensure critical custom models are present even when discovery succeeded (fresh install must have Grok 4.6 on Azure)
for prov_model in [("azure-openai-responses", "grok-4.6"), ("azure-openai-responses", "gpt-5.6-sol"), ("google-vertex", "gemini-3.7-flash"), ("opencode-go", "muse-spark-1.2-contributor"), ("opencode-go", "muse-spark-1.2-contributor-free"), ("opencode-go", "muse-spark-1.2-free"), ("opencode", "gpt-5.6-sol")]:
    if prov_model not in models:
        models.append(prov_model)

# Build providers dict with modelOverrides -> contextWindow 256000
for provider, model in models:
    if provider not in providers:
        providers[provider] = {}
    providers[provider][model] = {"contextWindow": 256000}

# Route Muse Spark to opencode-go only (not opencode) - config must be applied there
if "opencode" in providers:
    for key in list(providers["opencode"].keys()):
        if "muse-spark" in key:
            del providers["opencode"][key]

# Also ensure we have at least these provider keys even if no models discovered for them yet
for p in ["google-vertex", "opencode", "opencode-go", "openrouter", "azure-openai-responses"]:
    providers.setdefault(p, {})

# Azure custom models need an explicit baseUrl: prime-agent silently drops custom models
# whose baseUrl resolves falsy (built-in azure models have baseUrl ""). The env vars
# AZURE_OPENAI_BASE_URL / AZURE_OPENAI_RESOURCE_NAME override this placeholder at request time.
AZURE_BASEURL = "https://YOUR-RESOURCE-NAME.openai.azure.com/openai/v1"
# Build final output with defaults 256k and explicit custom models (ensures Grok 4.6 is always present on Azure)
custom_explicit = {
    "azure-openai-responses": [
        {"id": "gpt-5.6-sol", "name": "GPT-5.6 Sol (256k)", "contextWindow": 256000, "maxInputTokens": 256000, "limitTokens": 256000, "maxTokens": 16384, "reasoning": True, "baseUrl": AZURE_BASEURL},
        {"id": "grok-4.6", "name": "Grok 4.6 (256k)", "contextWindow": 256000, "maxInputTokens": 256000, "limitTokens": 256000, "maxTokens": 16384, "reasoning": False, "baseUrl": AZURE_BASEURL},
    ],
    "google-vertex": [
        {"id": "gemini-3.7-flash", "name": "Gemini 3.7 Flash (256k)", "contextWindow": 256000, "maxInputTokens": 256000, "limitTokens": 256000, "maxTokens": 16384, "reasoning": True, "input": ["text", "image"]},
    ],
    "opencode": [
        {"id": "gpt-5.6-sol", "name": "GPT-5.6 Sol (256k)", "contextWindow": 256000, "maxInputTokens": 256000, "limitTokens": 256000, "maxTokens": 16384, "reasoning": True},
    ],
    "opencode-go": [
        {"id": "muse-spark-1.2-contributor", "name": "Muse Spark 1.2 Contributor (256k)", "contextWindow": 256000, "maxInputTokens": 256000, "limitTokens": 256000, "maxTokens": 16384, "reasoning": True},
        {"id": "muse-spark-1.2-contributor-free", "name": "Muse Spark 1.2 Contributor Free (256k)", "contextWindow": 256000, "maxInputTokens": 256000, "limitTokens": 256000, "maxTokens": 16384, "reasoning": True},
        {"id": "muse-spark-1.2-free", "name": "Muse Spark 1.2 Free (256k)", "contextWindow": 256000, "maxInputTokens": 256000, "limitTokens": 256000, "maxTokens": 16384, "reasoning": True},
    ],
}

# Ensure every provider has a wildcard 256k override and the custom models are present
for prov in list(providers.keys()):
    overrides = providers[prov]
    # add wildcard
    overrides["*"] = {"contextWindow": 256000, "maxInputTokens": 256000, "limitTokens": 256000, "reasoning": True}
# Ensure providers for custom explicit exist even if not in discovered list
for prov in custom_explicit:
    providers.setdefault(prov, {})
    providers[prov]["*"] = {"contextWindow": 256000, "maxInputTokens": 256000, "limitTokens": 256000, "reasoning": True}
    for m in custom_explicit[prov]:
        providers[prov].setdefault(m["id"], {"contextWindow": 256000, "maxInputTokens": 256000, "limitTokens": 256000, "reasoning": True})
        # also ensure the explicit model override points to 256k (already)

# Filter to only allowed models (user requested: keep only Muse Spark, Gemini 3.7 Flash, GPT-5.6 Sol, Grok 4.6 plus Free aliases)
# This ensures fresh installs match the committed config
allowed_ids = {"gpt-5.6-sol", "grok-4.6", "gemini-3.7-flash", "muse-spark-1.2-contributor", "muse-spark-1.2-contributor-free", "muse-spark-1.2-free"}
for prov in list(providers.keys()):
    # keep only wildcard and allowed ids
    filtered = {}
    for k, v in providers[prov].items():
        if k == "*" or k in allowed_ids:
            filtered[k] = v
    providers[prov] = filtered
# Remove providers that have no allowed models left (except wildcard is kept only if provider has allowed models)
for prov in list(providers.keys()):
    # if after filtering only wildcard remains but provider has no explicit custom models, keep wildcard if provider is in custom_explicit else drop
    has_allowed = any(k in allowed_ids for k in providers[prov].keys())
    if not has_allowed and prov not in custom_explicit:
        del providers[prov]
    # also prune wildcard if provider was kept but had no allowed - already handled

output = {
    "defaults": {"contextWindow": 256000, "maxInputTokens": 256000, "limitTokens": 256000, "reasoning": True},
    "providers": {}
}
for prov, overrides in providers.items():
    if not overrides:
        continue
    entry = {"modelOverrides": overrides}
    if prov in custom_explicit:
        # filter models to allowed - but custom_explicit already only contains allowed
        entry["models"] = custom_explicit[prov]
        # prune models list to only allowed (already)
    # For providers without explicit custom list but with overrides, keep just overrides at 256k
    output["providers"][prov] = entry

# Ensure empty providers still have wildcard if needed (already handled)

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
    # The workspace bind mount and the persisted Prime Agent bind mount can
    # expose the same file through different path names. Compare file identity
    # so cp is not asked to copy models.json onto itself.
    if [ ! "$models_source" -ef "$d/models.json" ]; then
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
install_dsh
install_oh_my_pi
sync_prime_agent_rc
publish_tool_commands
ensure_cross_user_tool_access
install_prime_agent_skills
configure_prime_agent_tools
configure_prime_agent_models
make_zsh_default
verify_login_shell_tools

info "setup complete. Restart shell or run 'zsh' to use new config."
info "Tools: zsh $(zsh --version 2>/dev/null), nvim $(nvim --version 2>/dev/null | head -n1), zellij $(zellij --version 2>/dev/null), fzf $(fzf --version 2>/dev/null)"
