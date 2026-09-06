#!/usr/bin/env bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

# setup.sh - Standalone VM + container initializer (Debian 13 trixie preferred, Ubuntu also works)
# Idempotent, non-interactive, safe to re-run: re-runs update oh-my-zsh/plugins
# and re-enforce prompt/colors. Installs: zsh, oh-my-zsh, zsh-autosuggestions,
# zsh-syntax-highlighting, zsh-completions, zellij, lazyvim (neovim + LazyVim starter),
# codegraph (local code intelligence), prime-agent (256k contextWindow override),
# DeepSeek Harness (dsh), Oh My Pi (omp), and Codex CLI (codex).
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

# --- console users + setup identity (defined early: the install steps
# below call these before anything user-scoped is installed) ---
console_login_users() {
  local seen="" u
  if [ -n "${SUDO_USER:-}" ] && [ "${SUDO_USER}" != "root" ] && id "${SUDO_USER}" >/dev/null 2>&1; then
    printf '%s\n' "${SUDO_USER}"
    seen=" ${SUDO_USER} "
  fi
  local entry name uid home shell
  while IFS=: read -r name _ uid _ _ home shell; do
    case " $seen " in *" $name "*) continue ;; esac
    case "$uid" in ''|*[!0-9]*) continue ;; esac
    [ "$uid" -ge 1000 ] || continue
    case "$shell" in *nologin*|*/false|"") continue ;; esac
    [ -d "$home" ] || continue
    seen="$seen $name "
    printf '%s\n' "$name"
  done < <(getent passwd)
  if id overlord >/dev/null 2>&1; then
    case " $seen " in *" overlord "*) ;; *) printf '%s\n' overlord ;; esac
  fi
}
omz_target_homes() {
  local seen=""
  local h u uh
  {
    printf '%s\n' "${HOME}"
    [ -n "${SUDO_USER:-}" ] && printf '/home/%s\n' "${SUDO_USER}"
    printf '/home/overlord\n/root\n'
    while IFS= read -r u; do
      uh="$(getent passwd "$u" 2>/dev/null | cut -d: -f6)"
      [ -n "$uh" ] && printf '%s\n' "$uh"
    done < <(console_login_users)
  } | while IFS= read -r h; do
    [ -n "$h" ] || continue
    [ -d "$h" ] || continue
    # Non-root runs cannot provision homes they cannot write (e.g. /root):
    # skip them instead of dying on touch/mkdir permission errors.
    if [ ! -w "$h" ]; then
      printf '%s WARN: skipping unwritable home %s (run as root to provision it)\n' "${LOG_PREFIX}" "$h" >&2
      continue
    fi
    case " $seen " in
      *" $h "*) continue ;;
    esac
    # Compare real paths to avoid duplicates (e.g. HOME=/root).
    local rh sh
    rh="$(realpath "$h" 2>/dev/null || printf '%s' "$h")"
    case " $seen " in
      *" $rh "*) continue ;;
    esac
    seen="$seen $h $rh"
    printf '%s\n' "$h"
  done
}
resolve_setup_identity() {
  TARGET_USER="$(whoami)"
  if id overlord >/dev/null 2>&1; then
    TARGET_USER="overlord"
  else
    local first
    first="$(console_login_users | head -n1 || true)"
    [ -n "$first" ] && TARGET_USER="$first"
  fi
  TARGET_HOME="$(getent passwd "$TARGET_USER" 2>/dev/null | cut -d: -f6)"
  [ -n "$TARGET_HOME" ] || TARGET_HOME="$HOME"
  NATIVE_USER_INSTALL=""
  if [ "$(id -u)" -eq 0 ] && [ "$TARGET_USER" != "root" ] && ! id overlord >/dev/null 2>&1; then
    NATIVE_USER_INSTALL=1
  fi
  info "setup identity: user=$TARGET_USER home=$TARGET_HOME${NATIVE_USER_INSTALL:+ (native mode: user-scoped tools install as $TARGET_USER)}"
}
as_target() {
  # Run a user-scoped command as the target console user when we are root on a
  # native VM; otherwise run directly.
  if [ -n "${NATIVE_USER_INSTALL:-}" ]; then
    if command -v sudo >/dev/null 2>&1; then
      sudo -H -u "$TARGET_USER" env HOME="$TARGET_HOME" "$@"
    elif command -v runuser >/dev/null 2>&1; then
      runuser -u "$TARGET_USER" -- env HOME="$TARGET_HOME" "$@"
    else
      warn "cannot drop privileges (no sudo/runuser); running as root: $*"
      "$@"
    fi
  else
    "$@"
  fi
}
own_provisioned_home_files() {
  local home="$1" owner uh p cur
  [ -d "$home" ] || return 0
  owner="$(stat -c '%U' "$home" 2>/dev/null || echo "")"
  [ -n "$owner" ] || return 0
  # The home dir itself must belong to its user (root-created homes lock out
  # everything); contents are fixed per managed path below.
  uh="$(getent passwd "$owner" 2>/dev/null | cut -d: -f6)"
  if [ "$uh" = "$home" ] && [ "$(id -u)" -eq 0 ]; then
    chown "$owner:$owner" "$home" 2>/dev/null || true
  fi
  for p in .oh-my-zsh .zshrc .zshenv .bashrc .bash_profile .profile .zprofile .zsh_history .config .local .cache .nvm .prime .npm .bun .zellij .codegraph; do
    [ -e "${home}/${p}" ] || continue
    # Repair if the top entry OR anything under it belongs to someone else
    # (root-owned leftovers from previous root runs); find stops at the first
    # hit, so correctly-owned trees cost almost nothing.
    find "${home}/${p}" -not -user "$owner" -print -quit 2>/dev/null | grep -q . || continue
    chown -R "$owner:$owner" "${home}/${p}" 2>/dev/null       || run_sudo chown -R "$owner:$owner" "${home}/${p}" 2>/dev/null       || warn "could not chown ${home}/${p} to $owner"
  done
}
own_all_provisioned_homes() {
  local target_home
  while IFS= read -r target_home; do
    own_provisioned_home_files "$target_home"
  done < <(omz_target_homes)
}
ensure_git_safe_directories() {
  command -v git >/dev/null 2>&1 || return 0
  if git config --system --get-all safe.directory 2>/dev/null | grep -qxF '*'; then
    return 0
  fi
  if [ "$(id -u)" -eq 0 ]; then
    git config --system --add safe.directory '*' 2>/dev/null && info "git trusts all directories (safe.directory '*')" || warn "could not set system git safe.directory"
  else
    run_sudo git config --system --add safe.directory '*' 2>/dev/null       && info "git trusts all directories (safe.directory '*')"       || warn "could not set system git safe.directory"
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
    python3-yaml
    python3-tomlkit
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
  # fd-find installs as fdfind on Debian/Ubuntu; link to fd
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
  if [ -n "${NATIVE_USER_INSTALL:-}" ]; then
    # Native VM: the toolchain must belong to the console user, not root.
    # Install + provision Node fully as the target user, then source it here
    # (readable) so later root-side npm steps work too.
    nvm_dir="${TARGET_HOME}/.nvm"
    info "installing Node.js ${NODE_MAJOR} as ${TARGET_USER}..."
    as_target env NVM_DIR="$nvm_dir" bash -c '
      export NVM_DIR="$NVM_DIR"
      if [ ! -s "$NVM_DIR/nvm.sh" ]; then
        curl -fsSL "https://raw.githubusercontent.com/nvm-sh/nvm/v'"$NVM_VERSION"'/install.sh" | bash -s -- --no-use
      fi
      # shellcheck disable=SC1091
      . "$NVM_DIR/nvm.sh" --no-use
      nvm install "'"$NODE_MAJOR"'" && nvm alias default "'"$NODE_MAJOR"'" >/dev/null && nvm use default --silent
    ' 2>&1 | sed 's/^/[nvm] /' || warn "Node.js ${NODE_MAJOR} install as ${TARGET_USER} failed"
  else
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
  fi
  export NVM_DIR="$nvm_dir"
  # shellcheck disable=SC1091
  . "${nvm_dir}/nvm.sh" --no-use >/dev/null 2>&1 || true
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
  # Share the filtered target-home list (writable homes only, all console
  # users) so non-root runs never touch e.g. /root.
  local target_home
  while IFS= read -r target_home; do
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
  done < <(omz_target_homes)
}

# The prime-agent installer appends its PATH setup to ~/.bashrc only. Mirror
# those lines into ~/.zshrc so prime-agent works in the zsh/zellij default shell.
sync_prime_agent_rc_for() {
  local home="$1"
  local bashrc="${home}/.bashrc"
  local zshrc="${home}/.zshrc"
  [ -f "$bashrc" ] || return 0
  touch "$zshrc"
  local line
  while IFS= read -r line; do
    case "$line" in ""|"#"*) continue ;; esac
    case "$line" in
      *prime-agent*|*prime_agent*|*NVM_DIR*|*nvm.sh*) ;;
      *) continue ;;
    esac
    # Never leak another home's absolute paths (e.g. /root/.nvm from a root
    # install) into this user's shell: those lines break with permission
    # denied and shadow the user's own toolchain.
    case "$line" in
      *"${HOME}"*) [ "$home" = "${HOME}" ] || continue ;;
    esac
    if ! grep -qxF "$line" "$zshrc"; then
      printf '%s\n' "$line" >> "$zshrc"
      info "copied to ${zshrc}: $line"
    fi
  done < "$bashrc"
  # If this user has their own nvm, make sure the shell loads it (uses $HOME
  # literally, so it stays correct per user).
  if [ -s "${home}/.nvm/nvm.sh" ]; then
    local nvm_line
    for nvm_line in 'export NVM_DIR="$HOME/.nvm"' '[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"  # loads nvm' '[ -s "$NVM_DIR/bash_completion" ] && \. "$NVM_DIR/bash_completion"  # loads nvm bash_completion'; do
      if ! grep -qxF "$nvm_line" "$zshrc"; then
        printf '%s\n' "$nvm_line" >> "$zshrc"
        info "added to ${zshrc}: $nvm_line"
      fi
    done
  fi
  fix_home_ownership "$zshrc"
}

sync_prime_agent_rc() {
  local target_home
  while IFS= read -r target_home; do
    sync_prime_agent_rc_for "$target_home"
  done < <(omz_target_homes)
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
  for command_name in node npm npx corepack prime-agent codegraph uv aws dsh omp codex; do
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

# Published /usr/local/bin symlinks often resolve through a home dir
# ($HOME/.nvm, ~/.prime/bin). If any directory on that chain blocks traversal
# (Debian keeps /root at 700), every other user gets 'permission denied' on
# node, npm, prime-agent, etc. Grant traverse-only (o+x) on blocking chain
# directories and read (o+r) on a locked binary itself. Files otherwise keep
# their own permissions.
ensure_traversable_chain() {
  local target="$1" tool="$2" dir mode last
  dir="$(dirname "$target")"
  while :; do
    case "$dir" in
      /root|/root/*|/home/*) : ;;
      *) break ;;
    esac
    if [ -d "$dir" ]; then
      mode="$(stat -c '%a' "$dir" 2>/dev/null || echo "")"
      last="${mode: -1}"
      case "$last" in
        1|3|5|7) : ;;
        *)
          if [ "$(id -u)" -eq 0 ]; then
            chmod o+x "$dir" 2>/dev/null && info "made $dir traversable (o+x) for published $tool" || warn "could not chmod $dir"
          else
            run_sudo chmod o+x "$dir" 2>/dev/null \
              && info "made $dir traversable (o+x) for published $tool" \
              || warn "could not make $dir traversable; other users may hit 'permission denied' on $tool"
          fi
          ;;
      esac
    fi
    if [ "$dir" = "/root" ]; then break; fi
    dir="$(dirname "$dir")"
  done
  if [ -f "$target" ] && [ "$(id -u)" -eq 0 ]; then
    mode="$(stat -c '%a' "$target" 2>/dev/null || echo "")"
    last="${mode: -1}"
    case "$last" in
      0|1|2|3) chmod o+r "$target" 2>/dev/null && info "made $target readable (o+r) for published $tool" || true ;;
    esac
  fi
}

ensure_cross_user_tool_access() {
  local tool dest target
  for tool in node npm npx corepack prime-agent codegraph uv aws dsh omp codex; do
    dest="/usr/local/bin/$tool"
    [ -L "$dest" ] || continue
    target="$(readlink -f "$dest" 2>/dev/null || true)"
    [ -n "$target" ] || continue
    case "$target" in
      /root/*|/home/*) ensure_traversable_chain "$target" "$tool" ;;
    esac
  done
}

verify_login_shell_tools() {
  if ! command -v zsh >/dev/null 2>&1; then
    return 0
  fi
  local command_name target_home
  while IFS= read -r target_home; do
    for command_name in node npm npx prime-agent git omp codex; do
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

# Resolve who this machine is for before anything user-scoped is installed.
# Also repair ownership damage from previous root runs and trust checkouts, so
# plain commands work without sudo.
resolve_setup_identity
ensure_git_safe_directories
own_all_provisioned_homes
install_nvm_node
install_codegraph
ensure_node_shell_rc
install_uv
install_aws_cli

# --- oh-my-zsh (unattended, non-interactive) ---
# Human login users that will actually open shells on this machine. Covers
# native VMs (admin/ubuntu/debian, even when setup runs as root without
# SUDO_USER via su/cloud-init/SSM) as well as the Overlord container user.

# Setup may run as root while interactive shells/zellij run as the 'overlord'
# user (container) or a login user like admin (native VM). Provision oh-my-zsh
# in every target home so no shell misses plugins.

# --- setup identity: one script, two layouts ---
# Container: 'overlord' exists, shells run as overlord, root owns the toolchain
# in /root (published + traversable) — current behavior, unchanged.
# Native VM: no overlord user; user-scoped toolchains must belong to the human
# console user, never root — otherwise every command needs sudo.

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

# Previous root runs leave root-owned dotfiles inside human homes (.oh-my-zsh,
# .zshrc, .nvm, ...) — the user then needs sudo for everything, or zsh refuses
# to load. Hand provisioned paths back to the home owner. Only touches paths
# whose owner is wrong, so re-runs stay fast.

# Root-owned checkouts make plain 'git status' fail (dubious ownership), which
# reads as "git needs sudo". Trust checkouts machine-wide: single-tenant dev
# machine standard (same as devcontainers).

update_git_checkout() {
  local dir="$1"
  local label="${2:-$1}"
  if [ ! -d "${dir}/.git" ]; then
    return 1
  fi
  info "updating ${label}..."
  # Best-effort only: never fail setup on update errors (dirty tree, offline, etc).
  git -C "${dir}" pull --ff-only --quiet 2>/dev/null || warn "could not update ${label} (kept existing copy)"
}

install_oh_my_zsh_for() {
  local target_home="$1"
  local omz_dir="${target_home}/.oh-my-zsh"
  if [ -f "${omz_dir}/oh-my-zsh.sh" ]; then
    update_git_checkout "${omz_dir}" "oh-my-zsh (${target_home})" || true
    fix_home_ownership "$omz_dir"
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
    update_git_checkout "${custom}/plugins/zsh-autosuggestions" "zsh-autosuggestions" || true
  fi
  # zsh-syntax-highlighting
  if [ ! -d "${custom}/plugins/zsh-syntax-highlighting" ]; then
    info "cloning zsh-syntax-highlighting..."
    git clone --depth=1 https://github.com/zsh-users/zsh-syntax-highlighting "${custom}/plugins/zsh-syntax-highlighting"
  else
    update_git_checkout "${custom}/plugins/zsh-syntax-highlighting" "zsh-syntax-highlighting" || true
  fi
  # zsh-completions
  if [ ! -d "${custom}/plugins/zsh-completions" ]; then
    info "cloning zsh-completions..."
    git clone --depth=1 https://github.com/zsh-users/zsh-completions "${custom}/plugins/zsh-completions"
  else
    update_git_checkout "${custom}/plugins/zsh-completions" "zsh-completions" || true
  fi
  # zsh-autocomplete (optional, provides real-time autocomplete)
  if [ ! -d "${custom}/plugins/zsh-autocomplete" ]; then
    info "cloning zsh-autocomplete..."
    git clone --depth=1 https://github.com/marlonrichert/zsh-autocomplete "${custom}/plugins/zsh-autocomplete" 2>&1 | sed 's/^/[zsh-autocomplete] /' || true
  else
    update_git_checkout "${custom}/plugins/zsh-autocomplete" "zsh-autocomplete" || true
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
  local bashrc="${target_home}/.bashrc"
  mkdir -p "${target_home}"
  touch "${zshrc}" "${zshenv}" "${bashrc}"

  # Same marker prefix as before so existing installs update in place.
  # Content now covers Debian + Ubuntu (both ship a global /etc/zsh/zshrc).
  upsert_overlord_shell_block "${zshenv}" "Overlord: skip Ubuntu global compinit" <<'EOS'
# --- Overlord: skip Ubuntu global compinit ---
# Debian/Ubuntu /etc/zsh/zshrc runs compinit before ~/.zshrc. That dump never
# includes zsh-autocomplete helpers, so Tab later prints
# "_autocomplete__unambiguous not found".
skip_global_compinit=1
EOS

  # Never keep zsh-autocomplete as an Oh My Zsh plugin; it must load before compinit.
  if grep -qE '^plugins=\(.*zsh-autocomplete' "${zshrc}"; then
    # Use a backup suffix so this works with both GNU and BSD sed.
    sed -i.bak -E '/^plugins=[(]/ s/[[:space:]]*zsh-autocomplete//g' "${zshrc}"
    rm -f "${zshrc}.bak"
  fi

  # Migrate old dull prompt to a colored one that always shows user@host:path.
  # bira is built into oh-my-zsh, needs no nerd fonts (agnoster/p10k break on plain VMs).
  if grep -q 'ZSH_THEME="robbyrussell"' "${zshrc}"; then
    info "migrating ${zshrc} from robbyrussell to bira (colored user@host:path)..."
    sed -i.bak 's/ZSH_THEME="robbyrussell"/ZSH_THEME="bira"/' "${zshrc}"
    rm -f "${zshrc}.bak"
  fi

  if grep -q 'Overlord: oh-my-zsh' "${zshrc}" || ! grep -q 'oh-my-zsh.sh' "${zshrc}"; then
    upsert_overlord_shell_block "${zshrc}" "Overlord: oh-my-zsh" <<'EOS'
# --- Overlord: oh-my-zsh ---
export ZSH="$HOME/.oh-my-zsh"
ZSH_THEME="bira"
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
    # Enforce required plugins on unmanaged .zshrc too (old code only warned).
    python3 - "${zshrc}" <<'PY' || true
from pathlib import Path
import re
import sys
path = Path(sys.argv[1])
text = path.read_text() if path.exists() else ""
want = ["git", "zsh-autosuggestions", "zsh-syntax-highlighting", "zsh-completions"]
m = re.search(r"^plugins=\(([^)]*)\)", text, re.M)
if m:
    have = m.group(1).split()
    changed = False
    for p in want:
        if p not in have:
            have.append(p)
            changed = True
    # drop zsh-autocomplete from plugin list (must load before compinit instead)
    if "zsh-autocomplete" in have:
        have = [p for p in have if p != "zsh-autocomplete"]
        changed = True
    if changed:
        text = text[:m.start()] + "plugins=(" + " ".join(have) + ")" + text[m.end():]
        path.write_text(text)
        print(f"updated plugins in {path}")
else:
    print(f"no plugins= line in {path}, leaving as-is")
PY
    # Enforce bira theme on unmanaged files as well.
    if grep -qE '^ZSH_THEME=' "${zshrc}"; then
      sed -i.bak -E 's/^ZSH_THEME=".*"/ZSH_THEME="bira"/; s/^ZSH_THEME='"'"'.*'"'"'/ZSH_THEME="bira"/' "${zshrc}" || true
      rm -f "${zshrc}.bak"
    fi
    insert_autocomplete_before_omz "${zshrc}"
    info "sourced zsh-autocomplete before oh-my-zsh in ${zshrc}"
  fi

  # Colored ls/grep + handy aliases. Always shows colors and folder context.
  upsert_overlord_shell_block "${zshrc}" "Overlord: colors + aliases" <<'EOS'
# --- Overlord: colors + aliases ---
export CLICOLOR=1
export TERM="${TERM:-xterm-256color}"
[ -x /usr/bin/dircolors ] && eval "$(dircolors -b 2>/dev/null)"
alias ls='ls --color=auto'
alias ll='ls -alF --color=auto'
alias la='ls -A --color=auto'
alias l='ls -CF --color=auto'
alias grep='grep --color=auto'
alias fgrep='fgrep --color=auto'
alias egrep='egrep --color=auto'
EOS
  upsert_overlord_shell_block "${bashrc}" "Overlord: colors + aliases" <<'EOS'
# --- Overlord: colors + aliases ---
export CLICOLOR=1
export TERM="${TERM:-xterm-256color}"
[ -x /usr/bin/dircolors ] && eval "$(dircolors -b 2>/dev/null)"
alias ls='ls --color=auto'
alias ll='ls -alF --color=auto'
alias la='ls -A --color=auto'
alias l='ls -CF --color=auto'
alias grep='grep --color=auto'
alias fgrep='fgrep --color=auto'
alias egrep='egrep --color=auto'
EOS

  # Colored bash prompt with user@host:folder + git branch (for VMs still on bash).
  upsert_overlord_shell_block "${bashrc}" "Overlord: bash prompt" <<'EOS'
# --- Overlord: bash prompt ---
# Always colored, always shows user@host:full-path + git branch.
__overlord_git_branch() {
  git branch --show-current 2>/dev/null | sed 's/^/ (/;s/$/)/'
}
case "$TERM" in
  xterm*|screen*|tmux*|rxvt*) color_prompt=yes ;;
esac
if [ "$color_prompt" = yes ]; then
  PS1='\[\033[01;32m\]\u@\h\[\033[00m\]:\[\033[01;34m\]\w\[\033[01;31m\]$(__overlord_git_branch)\[\033[00m\]\$ '
else
  PS1='\u@\h:\w$(__overlord_git_branch)\$ '
fi
EOS

  # Stale dumps from global compinit / old plugin order omit autocomplete helpers.
  rm -f "${target_home}/.zcompdump" "${target_home}/.zcompdump"-* "${target_home}/.cache/zsh/compdump" 2>/dev/null || true
  fix_home_ownership "${zshrc}" "${zshenv}" "${bashrc}" || true
}

# --- zellij config + autostart on SSH (non-interactive, idempotent) ---
ensure_zellij_config() {
  local target_home
  local target_homes=()
  # Shared filtered list: writable homes only, all console users.
  while IFS= read -r target_home; do
    target_homes+=("$target_home")
  done < <(omz_target_homes)
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
  # Shared filtered list: writable homes only, all console users.
  while IFS= read -r target_home; do
    target_homes+=("$target_home")
  done < <(omz_target_homes)
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
  # Shared filtered list: writable homes only, all console users.
  local _omz_home
  while IFS= read -r _omz_home; do
    target_homes+=("$_omz_home")
  done < <(omz_target_homes)
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
    # Native mode installs as the target user, who must be able to read the
    # root-downloaded installer (mktemp is 600).
    chmod 644 "$installer" 2>/dev/null || true
    if [ -n "${NATIVE_USER_INSTALL:-}" ]; then
      info "installing prime-agent as ${TARGET_USER}..."
      as_target env PRIME_AGENT_INSTALLER_PLAIN=1 PRIME_AGENT_BOOTSTRAP_KERNEL_ON_INSTALL=0 sh "$installer" "$want" 2>&1 | sed 's/^/[prime-agent] /' || true
    else
      PRIME_AGENT_INSTALLER_PLAIN=1 PRIME_AGENT_BOOTSTRAP_KERNEL_ON_INSTALL=0 sh "$installer" "$want" 2>&1 | sed 's/^/[prime-agent] /' || true
    fi
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
  # Always converge on the latest release: a plain skip-if-present check would
  # pin whatever version happened to be installed first.
  local current=""
  if omp_is_available "$local_binary"; then
    # omp may live on PATH rather than at local_binary; query the one that runs.
    local omp_binary="omp"
    if [ -x "$local_binary" ]; then
      omp_binary="$local_binary"
    fi
    current="$("$omp_binary" --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -n1 || true)"
  fi
  # OMP_VERSION pins a version when set; otherwise track npm latest.
  local want="${OMP_VERSION:-}"
  if [ -z "$want" ] && command -v npm >/dev/null 2>&1; then
    want="$(npm view @oh-my-pi/pi-coding-agent version 2>/dev/null | tr -d ' \r\n' || true)"
  fi
  if [ -n "$current" ] && [ -n "$want" ] && [ "$current" = "$want" ]; then
    info "Oh My Pi already at latest ($current)"
    return 0
  fi
  if [ -n "$current" ] && [ -z "$want" ]; then
    warn "could not determine latest Oh My Pi version; keeping installed $current"
    return 0
  fi
  if [ -n "$current" ]; then
    info "upgrading Oh My Pi ($current -> ${want:-latest})..."
  else
    info "installing Oh My Pi (omp)..."
  fi
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

# Codex CLI — OpenAI's coding agent, configured here for the same Azure
# gpt-6-astra deployment as prime-agent/omp. https://github.com/openai/codex
CODEX_NPM_PACKAGE="@openai/codex"

codex_want_version() {
  local want="${CODEX_VERSION:-}"
  if [ -f "$(dirname "$0")/config/tool-versions.env" ]; then
    want="$(grep CODEX_VERSION "$(dirname "$0")/config/tool-versions.env" | cut -d= -f2 | tr -d ' ' || echo "$want")"
  elif [ -f "/workspace/config/tool-versions.env" ]; then
    want="$(grep CODEX_VERSION /workspace/config/tool-versions.env | cut -d= -f2 | tr -d ' ' || echo "$want")"
  elif [ -f "/usr/local/share/overlord/config/tool-versions.env" ]; then
    want="$(grep CODEX_VERSION /usr/local/share/overlord/config/tool-versions.env | cut -d= -f2 | tr -d ' ' || echo "$want")"
  fi
  echo "$want"
}

install_codex() {
  if ! command -v node >/dev/null 2>&1; then
    warn "node unavailable; skipping Codex CLI install"
    return 0
  fi
  local cur=""
  if command -v codex >/dev/null 2>&1; then
    cur="$(codex --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+[^ ]*' | head -n1 || true)"
  fi
  local want
  want="$(codex_want_version)"
  if [ -n "$cur" ]; then
    if [ -z "$want" ] || echo "$cur" | grep -q "^$want"; then
      info "Codex CLI already installed (codex $cur)"
      return 0
    fi
    info "Codex CLI version mismatch (have: $cur, want: $want), reinstalling..."
  else
    info "installing Codex CLI (codex)..."
  fi
  local spec="${CODEX_NPM_PACKAGE}"
  [ -n "$want" ] && spec="@openai/codex@${want}"
  if npm install -g "$spec" 2>&1 | sed 's/^/[codex] /'; then
    if command -v codex >/dev/null 2>&1; then
      info "codex $(codex --version 2>/dev/null | head -n1) installed"
    else
      warn "codex not on PATH after install"
    fi
  else
    warn "failed to install $spec via npm"
  fi
}

# Oh My Pi: Astra/medium by default, low for lightweight work, off for exploration.
# Merge managed models and roles, preserving other settings and first backups.
configure_omp_models() {
  info "configuring Oh My Pi model policy (Astra medium / low / off)..."
  local omp_dirs=()
  omp_dirs+=("${PI_CODING_AGENT_DIR:-$HOME/.omp/agent}")
  if [ -d "/home/overlord" ] && { [ "$(id -u)" -eq 0 ] || [ -w /home/overlord/.omp/agent ] || [ -w /home/overlord ]; }; then
    omp_dirs+=("/home/overlord/.omp/agent")
  fi
  if [ -d "/root" ] && { [ "$(id -u)" -eq 0 ] || [ -w /root/.omp/agent ] || [ -w /root ]; }; then
    omp_dirs+=("/root/.omp/agent")
  fi
  local uniq_dirs=()
  local seen=""
  local d
  for d in "${omp_dirs[@]}"; do
    case " $seen " in
      *" $d "*) continue ;;
      *) uniq_dirs+=("$d"); seen="$seen $d" ;;
    esac
  done
  /usr/bin/python3 - "${uniq_dirs[@]}" <<'PYEOF_OMP'
import copy
import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path

import yaml

resource = os.environ.get("AZURE_OPENAI_RESOURCE_NAME", "").strip()
base = os.environ.get("AZURE_OPENAI_BASE_URL", "").strip().rstrip("/")
if not base and resource:
    base = f"https://{resource}.openai.azure.com/openai/v1"
provider_id = "azure-gpt6"  # Keep the existing provider ID for saved sessions.
luna = "gpt-5.6-luna"
astra = "gpt-6-astra"
roles = {
    role: f"{provider_id}/{astra}:{'low' if role in ('smol', 'tiny', 'commit') else 'medium'}"
    for role in ("default", "smol", "slow", "vision", "plan", "commit", "tiny", "task", "advisor")
}


def mapping(parent, key):
    value = parent.setdefault(key, {})
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be a mapping")
    return value


def read_config(path):
    existing = path.read_text() if path.exists() else None
    data = yaml.safe_load(existing) if existing is not None else {}
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValueError("configuration must be a mapping")
    return existing, data


def write_config(path, original, data):
    rendered = "# Managed model policy by overlord setup.sh. Other settings are preserved.\n"
    rendered += yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    # Keep comments/formatting and mtime on an already-correct config.
    if original is not None and yaml.safe_load(original) == data:
        print(f"unchanged {path}")
        return
    write_file(path, original, rendered)


def write_file(path, original, rendered):
    if original == rendered:
        print(f"unchanged {path}")
        return
    backup = path.with_suffix(path.suffix + ".bak")
    if original is not None and not backup.exists():
        shutil.copy2(path, backup)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(rendered)
        temporary.chmod(mode)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print(f"wrote {path}")


# Azure's OMP adapter encodes off as the model's lowest effort (low for Astra).
# Preserve low for lightweight work, but send literal none for exploration.
astra_reasoning_extension = '''import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";

export default function (pi: ExtensionAPI) {
  pi.on("before_provider_request", (event, ctx) => {
    if (ctx.model?.provider !== "azure-gpt6" || ctx.model.id !== "gpt-6-astra" ||
        ctx.model.api !== "azure-openai-responses" || pi.getThinkingLevel() !== "off") return;
    if (!event.payload || typeof event.payload !== "object" || Array.isArray(event.payload)) return;
    return { ...event.payload, reasoning: { effort: "none" } };
  });
}
'''


for raw in sys.argv[1:]:
    agent_dir = Path(raw)
    try:
        agent_dir.mkdir(parents=True, exist_ok=True)
        models_path, config_path = agent_dir / "models.yml", agent_dir / "config.yml"
        models_original, models = read_config(models_path)
        config_original, config = read_config(config_path)
        provider = mapping(mapping(models, "providers"), provider_id)
        provider.update(
            baseUrl=base or provider.get("baseUrl") or "https://YOUR-RESOURCE-NAME.openai.azure.com/openai/v1",
            api="azure-openai-responses", apiKey="AZURE_OPENAI_API_KEY",
        )
        entries = provider.setdefault("models", [])
        if not isinstance(entries, list) or any(not isinstance(entry, dict) for entry in entries):
            raise ValueError("models must be a list of mappings")
        for model_id, name, effort in ((luna, "GPT-5.6 Luna", "max"), (astra, "GPT-6 Astra", "medium")):
            efforts = ["low", "medium", "high", "xhigh", "max"] if model_id == astra else [effort]
            effort_map = {level: level for level in efforts}
            thinking = {
                "mode": "effort", "efforts": efforts, "defaultLevel": effort,
                "effortMap": effort_map, "requiresEffort": model_id != astra,
            }
            matching = [entry for entry in entries if entry.get("id") == model_id]
            if not matching:
                matching = [{"id": model_id}]
                entries.extend(matching)
            for entry in matching:
                entry.update(name=name, reasoning=True, thinking=copy.deepcopy(thinking))
                # Compat maps take precedence over thinking metadata on the wire.
                mapping(entry, "compat").update(
                    reasoningEffortMap=effort_map.copy(), supportsReasoningParams=True,
                )
                entry.setdefault("input", ["text", "image"])
                entry.setdefault("contextWindow", 256000)
                entry.setdefault("maxTokens", 16384)
            # Existing wildcard/model overrides must not undo the effort policy.
            if "modelOverrides" in provider:
                overrides = mapping(provider, "modelOverrides")
                mapping(overrides, model_id)["thinking"] = copy.deepcopy(thinking)
                overrides[model_id]["reasoning"] = True
                mapping(overrides[model_id], "compat").update(
                    reasoningEffortMap=effort_map.copy(), supportsReasoningParams=True,
                )
        configured_roles = mapping(config, "modelRoles")
        configured_roles.update(roles)
        config["defaultThinkingLevel"] = "medium"
        # Explicit effort suffixes override bundled agent thinking defaults.
        agent_overrides = mapping(mapping(config, "task"), "agentModelOverrides")
        for agent, effort in (("scout", "off"), ("librarian", "off"), ("sonic", "low")):
            agent_overrides[agent] = f"{provider_id}/{astra}:{effort}"
        # Parse and merge both files before writing either one.
        write_config(models_path, models_original, models)
        write_config(config_path, config_original, config)
        extension_path = agent_dir / "extensions" / "overlord-astra-reasoning.ts"
        extension_original = extension_path.read_text() if extension_path.exists() else None
        extension_path.parent.mkdir(parents=True, exist_ok=True)
        write_file(extension_path, extension_original, astra_reasoning_extension)
    except (OSError, ValueError, yaml.YAMLError) as error:
        # Parser errors may contain credentials from user configuration: do not echo them.
        print(f"skipping invalid or unwritable Oh My Pi config in {agent_dir} ({type(error).__name__})")
PYEOF_OMP
}

# Codex: Luna/max by default, explicit high-brain profile for Astra/medium.
# Merge config instead of skipping old Astra defaults; preserve unrelated settings.
# Resolve deployment mappings here because Codex sends model names verbatim.
configure_codex() {
  info "configuring Codex model policy (Luna max / Astra medium)..."
  local codex_dirs=()
  codex_dirs+=("${CODEX_HOME:-$HOME/.codex}")
  if [ -d "/home/overlord" ] && { [ "$(id -u)" -eq 0 ] || [ -w /home/overlord/.codex ] || [ -w /home/overlord ]; }; then
    codex_dirs+=("/home/overlord/.codex")
  fi
  if [ -d "/root" ] && { [ "$(id -u)" -eq 0 ] || [ -w /root/.codex ] || [ -w /root ]; }; then
    codex_dirs+=("/root/.codex")
  fi
  local uniq_dirs=()
  local seen=""
  local d
  for d in "${codex_dirs[@]}"; do
    case " $seen " in
      *" $d "*) continue ;;
      *) uniq_dirs+=("$d"); seen="$seen $d" ;;
    esac
  done
  /usr/bin/python3 - "${uniq_dirs[@]}" <<'PYEOF_CODEX'
import copy
import os
import stat
import sys
import tempfile
from collections.abc import MutableMapping
from pathlib import Path

import tomlkit

resource = os.environ.get("AZURE_OPENAI_RESOURCE_NAME", "").strip()
env_base = os.environ.get("AZURE_OPENAI_BASE_URL", "").strip().rstrip("/")
if env_base:
    # Azure's versioned Responses endpoint is /openai/responses?api-version=...
    # Codex appends /responses; Prime/OMP bases may include the /v1 suffix.
    configured_base = env_base[:-3].rstrip("/") if env_base.endswith("/v1") else env_base
elif resource:
    configured_base = f"https://{resource}.openai.azure.com/openai"
else:
    configured_base = None
api_version = os.environ.get("AZURE_OPENAI_API_VERSION", "").strip()
deployments = {"gpt-5.6-luna": "gpt-5.6-luna", "gpt-6-astra": "gpt-6-astra"}
for chunk in os.environ.get("AZURE_OPENAI_DEPLOYMENT_NAME_MAP", "").split(","):
    key, separator, value = chunk.partition("=")
    if separator and key.strip() in deployments and value.strip():
        deployments[key.strip()] = value.strip()


def table(parent, key):
    if key not in parent:
        parent[key] = tomlkit.table()
    value = parent[key]
    if not isinstance(value, MutableMapping):
        raise ValueError("managed configuration entry must be a table")
    return value


def apply_model_policy(config, model, effort):
    # Since Codex 0.153.4 --profile selects <name>.config.toml. A legacy
    # root profile selector is rejected, even though older schemas retain it.
    config.pop("profile", None)
    config["model"] = deployments[model]
    config["model_provider"] = "azure"
    config["model_reasoning_effort"] = effort
    config["plan_mode_reasoning_effort"] = effort
    # Reviews inherit the current effort. Do not leave an Astra-only model
    # override that could send Luna's max effort to Astra.
    if config.get("review_model") in {"gpt-6-astra", deployments["gpt-6-astra"]}:
        config.pop("review_model", None)


def apply_policy(config, model, effort, is_base):
    apply_model_policy(config, model, effort)
    # Keep unrelated legacy profiles, but enforce medium for any Astra entries.
    if "profiles" in config:
        for profile in table(config, "profiles").values():
            if isinstance(profile, MutableMapping) and profile.get("model") in {"gpt-6-astra", deployments["gpt-6-astra"]}:
                profile["model_reasoning_effort"] = "medium"
                profile["plan_mode_reasoning_effort"] = "medium"
    if is_base:
        azure = table(table(config, "model_providers"), "azure")
        azure["name"] = "Azure OpenAI"
        azure["base_url"] = configured_base or azure.get("base_url") or "https://YOUR-RESOURCE-NAME.openai.azure.com/openai"
        azure["env_key"] = "AZURE_OPENAI_API_KEY"
        azure["wire_api"] = "responses"
        query = table(azure, "query_params")
        query["api-version"] = api_version or query.get("api-version") or "2025-04-01-preview"


def write_config(target, existing, updated):
    if updated == existing:
        print(f"keeping unchanged {target}")
        return
    if existing is not None:
        backup = target.with_suffix(".toml.bak")
        try:
            descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(existing)
            print(f"backed up {target} to {backup}")
    mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else 0o600
    descriptor, temporary_name = tempfile.mkstemp(prefix=".config-", suffix=".toml", dir=target.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(updated)
        temporary_path.chmod(mode)
        os.replace(temporary_path, target)
    finally:
        temporary_path.unlink(missing_ok=True)
    print(f"wrote {target}")


def merge_missing(target, source):
    # An existing file-profile setting wins over a legacy inline setting.
    for key, value in source.items():
        if key not in target:
            target[key] = copy.deepcopy(value)
        elif isinstance(target[key], MutableMapping) and isinstance(value, MutableMapping):
            merge_missing(target[key], value)


policy = {
    "config.toml": ("gpt-5.6-luna", "max"),
    "default.config.toml": ("gpt-5.6-luna", "max"),
    "high-brain.config.toml": ("gpt-6-astra", "medium"),
}
for raw in sys.argv[1:]:
    home_dir = Path(raw)
    try:
        home_dir.mkdir(parents=True, exist_ok=True)
        originals, documents = {}, {}
        # Validate all files before modifying any. Malformed user config stays intact.
        for filename in policy:
            target = home_dir / filename
            original = target.read_text(encoding="utf-8") if target.exists() else None
            originals[filename] = original
            config = tomlkit.parse(original) if original is not None else tomlkit.document()
            if original is None:
                config.add(tomlkit.comment("Managed model policy from overlord setup.sh (configure_codex)."))
            documents[filename] = config
        # Codex rejects --profile NAME while a legacy [profiles.NAME] table
        # remains in any loaded layer. Move managed tables into their files.
        for config in documents.values():
            if "profiles" not in config:
                continue
            legacy = table(config, "profiles")
            for name in ("default", "high-brain"):
                if name in legacy:
                    source = table(legacy, name)
                    merge_missing(documents[f"{name}.config.toml"], source)
                    del legacy[name]
            if not legacy:
                del config["profiles"]
        rendered = {}
        for filename, (model, effort) in policy.items():
            config = documents[filename]
            apply_policy(config, model, effort, filename == "config.toml")
            rendered[filename] = tomlkit.dumps(config)
        # Save profile settings before removing their old tables from the base.
        for filename in ("default.config.toml", "high-brain.config.toml", "config.toml"):
            write_config(home_dir / filename, originals[filename], rendered[filename])
    except (OSError, UnicodeError, ValueError, TypeError, tomlkit.exceptions.TOMLKitError) as error:
        # Parser diagnostics may contain credentials: report only the error type.
        print(f"skipping invalid or unwritable Codex config in {home_dir}: {type(error).__name__}")
PYEOF_CODEX
}

install_prime_agent_skills() {
  if ! command -v npx >/dev/null 2>&1; then
    warn "npx unavailable; skipping Prime Agent skill installation"
    return 0
  fi
  info "installing shared skills for Pi, Prime Agent, and Oh My Pi..."
  local skill_source
  for skill_source in mattpocock/skills aws/agent-toolkit-for-aws cursor/plugins; do
    if npx --yes skills add "$skill_source" --global --agent pi --yes --copy --full-depth \
      2>&1 | sed "s|^|[skills:$skill_source] |"; then
      info "installed skills from $skill_source"
    else
      warn "failed to install skills from $skill_source"
    fi
  done

  # The skills CLI targets Pi, not Prime Agent or OMP. Provision each
  # harness's native directory for every console user.
  local pi_skills="$HOME/.pi/agent/skills"
  local target_homes=()
  # Shared filtered list: writable homes only, all console users.
  local _omz_home2
  while IFS= read -r _omz_home2; do
    target_homes+=("$_omz_home2")
  done < <(omz_target_homes)
  local target_home agent_skills owner
  if [ -d "$pi_skills" ]; then
    for target_home in "${target_homes[@]}"; do
      for agent_skills in "$target_home/.prime/agent/skills" "$target_home/.omp/agent/skills"; do
        mkdir -p "$agent_skills"
        cp -a "$pi_skills/." "$agent_skills/"
        owner="$(stat -c '%U' "$target_home" 2>/dev/null || true)"
        if [ -n "$owner" ]; then
          chown -R "$owner":"$owner" "$agent_skills" 2>/dev/null || true
        fi
        info "synced Pi skills to $agent_skills"
      done
    done
  else
    warn "Pi skills directory was not created: $pi_skills"
  fi

  # The AWS setup URL is an interactive workflow, not a skills CLI package.
  # Install it as a local skill so each harness can guide login/profile setup later.
  local aws_setup_url="https://raw.githubusercontent.com/aws/agent-toolkit-for-aws/refs/heads/main/setup-instructions/setup.md"
  local aws_setup_tmp
  aws_setup_tmp="$(mktemp)"
  if curl -fsSL "$aws_setup_url" -o "$aws_setup_tmp"; then
    local skill_dir
    for target_home in "${target_homes[@]}"; do
      for agent_skills in "$target_home/.pi/agent/skills" "$target_home/.prime/agent/skills" "$target_home/.omp/agent/skills"; do
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
        chown -R "$owner":"$owner" "$target_home/.pi" "$target_home/.prime" "$target_home/.omp/agent/skills" 2>/dev/null || true
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
  if [ -d /home/overlord ] && { [ "$(id -u)" -eq 0 ] || [ -w /home/overlord/.prime/agent ] || [ -w /home/overlord ]; }; then settings_paths+=("/home/overlord/.prime/agent/settings.json"); fi
  if [ -d /root ] && { [ "$(id -u)" -eq 0 ] || [ -w /root/.prime/agent/settings.json ] || [ -w /root ]; }; then settings_paths+=("/root/.prime/agent/settings.json"); fi
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

        # Remove stale picker entries and migrate legacy model IDs to the current
        # configured versions. OpenCode has no configured models in this setup.
        muse_spark_model = "muse-spark-1.3-contributor"
        gemini_model = "gemini-3.8-flash"

        def normalize_muse_spark_model(model):
            if not isinstance(model, str):
                return model
            provider, separator, model_id = model.rpartition("/")
            if model_id.startswith("muse-spark-") and model_id.endswith("-contributor"):
                normalized = muse_spark_model
                return f"{provider}/{normalized}" if separator else normalized
            if model_id.startswith("muse-spark-"):
                return None
            return model

        def normalize_gemini_model(model):
            if not isinstance(model, str):
                return model
            provider, separator, model_id = model.rpartition("/")
            prefix = "gemini-3."
            suffix = "-flash"
            if model_id.startswith(prefix) and model_id.endswith(suffix):
                minor_text = model_id[len(prefix) : -len(suffix)]
                try:
                    minor = int(minor_text)
                except ValueError:
                    return model
                if minor < 8:
                    normalized = gemini_model
                    return f"{provider}/{normalized}" if separator else normalized
            return model

        def normalize_model(model):
            return normalize_gemini_model(normalize_muse_spark_model(model))

        recent_models = settings.get("recentModels")
        if isinstance(recent_models, list):
            migrated_recent_models = []
            for model in recent_models:
                if isinstance(model, str) and model.startswith("opencode/"):
                    continue
                model = normalize_model(model)
                if model is not None:
                    migrated_recent_models.append(model)
            settings["recentModels"] = migrated_recent_models

        raw_default_model = settings.get("defaultModel")
        default_model = normalize_model(raw_default_model)
        if isinstance(raw_default_model, str):
            _, _, raw_model_id = raw_default_model.rpartition("/")
            if raw_model_id.startswith("muse-spark-"):
                # Old free/contributor aliases all map to the supported contributor model.
                default_model = muse_spark_model
            elif raw_model_id.startswith("gemini-3.") and raw_model_id.endswith("-flash"):
                _, _, default_model = default_model.rpartition("/")
        if default_model is not None:
            settings["defaultModel"] = default_model
        if settings.get("defaultProvider") == "opencode":
            if default_model == "gpt-5.6-luna":
                settings["defaultProvider"] = "opencode-go"
            elif default_model == muse_spark_model:
                settings["defaultProvider"] = "opencode-go"
            else:
                settings.pop("defaultProvider", None)
                settings.pop("defaultModel", None)

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as mkdir_error:
            print(f"skipping unwritable {path.parent}: {mkdir_error}", file=sys.stderr)
            continue
        try:
            path.write_text(json.dumps(settings, indent=2, sort_keys=True) + "\n")
        except Exception as error:
            print(f"skipping unwritable {path}: {error}", file=sys.stderr)
            continue
        try:
            path.chmod(0o644)
        except OSError:
            pass
        print(f"configured {path}")
    except Exception as error:
        print(f"could not update {path}: {error}", file=sys.stderr)
        continue
PYEOF

  # Add the Context7 routing skill to both Prime and OMP native roots.
  local agent_dirs=() target_home
  while IFS= read -r target_home; do
    agent_dirs+=("$target_home/.prime/agent" "$target_home/.omp/agent")
  done < <(omz_target_homes)
  local agent_dir
  for agent_dir in "${agent_dirs[@]}"; do
    if ! mkdir -p "$agent_dir/skills/context7" 2>/dev/null; then
      warn "skipping unwritable $agent_dir (run as root to provision it)"
      continue
    fi
    rm -rf "$agent_dir/skills/runpod-docs" 2>/dev/null || true
    if ! cat > "$agent_dir/skills/context7/SKILL.md" <<'SKILLEOF'
---
name: context7
description: Look up current library and framework documentation through Context7 MCP. Use when API details, current examples, configuration, or version-specific behavior are needed.
---

# Context7

Use the tools exposed by the `context7` MCP server to resolve a library and retrieve its current documentation. Prefer Context7 over memory when implementation depends on current APIs or version-specific behavior.
SKILLEOF
    then
      warn "skipping unwritable $agent_dir/skills/context7/SKILL.md"
      continue
    fi
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
  # Also ensure overlord and root dirs are covered when running as root (or writable)
  if [ -d "/home/overlord" ] && { [ "$(id -u)" -eq 0 ] || [ -w /home/overlord/.prime/agent ] || [ -w /home/overlord ]; }; then
    agent_dirs+=("/home/overlord/.prime/agent")
  fi
  if [ -d "/root" ] && { [ "$(id -u)" -eq 0 ] || [ -w /root/.prime/agent ] || [ -w /root ]; }; then
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
    # Patch existing file to ensure 256k defaults and Azure Grok 4.6 at 180k (200k max)
    python3 - "$existing_models_json" <<'PYEOF_PATCH'
import json, sys, os
from pathlib import Path
path = Path(sys.argv[1])
try:
    data = json.loads(path.read_text())
except Exception as e:
    print(f"could not patch {path}: {e}", file=sys.stderr)
    sys.exit(0)
changed=False
_azure_resource=os.environ.get("AZURE_OPENAI_RESOURCE_NAME","").strip()
AZURE_BASEURL=f"https://{_azure_resource}.openai.azure.com/openai/v1" if _azure_resource else "https://YOUR-RESOURCE-NAME.openai.azure.com/openai/v1"
DEFAULT_CONTEXT_WINDOW=256000
GROK_46_CONTEXT_WINDOW=180000
def context_window_for(model_id):
    return GROK_46_CONTEXT_WINDOW if model_id=="grok-4.6" else DEFAULT_CONTEXT_WINDOW
def context_label(tokens):
    return f"{tokens // 1000}k"
def token_fields(model_id):
    window=context_window_for(model_id)
    return {"contextWindow": window, "maxInputTokens": window, "limitTokens": window}
def model_entry(model_id, name, *, reasoning, extra=None):
    entry={"id": model_id, "name": f"{name} ({context_label(context_window_for(model_id))})", **token_fields(model_id), "maxTokens": 16384, "reasoning": reasoning}
    if extra:
        entry.update(extra)
    return entry
defaults=data.setdefault("defaults",{})
for k in ("contextWindow","maxInputTokens","limitTokens"):
    if defaults.get(k)!=DEFAULT_CONTEXT_WINDOW:
        defaults[k]=DEFAULT_CONTEXT_WINDOW
        changed=True
if defaults.get("reasoning") is not True:
    defaults["reasoning"]=True
    changed=True
providers=data.setdefault("providers",{})
desired_explicit={
    "azure-openai-responses": [
        model_entry("gpt-5.6-sol", "GPT-5.6 Sol", reasoning=True, extra={"baseUrl": AZURE_BASEURL}),
        model_entry("gpt-5.6-luna", "GPT-5.6 Luna", reasoning=True, extra={"thinkingLevelMap": {"max": "max"}, "baseUrl": AZURE_BASEURL}),
        model_entry("grok-4.6", "Grok 4.6", reasoning=False, extra={"baseUrl": AZURE_BASEURL}),
        model_entry("gpt-6-astra", "GPT-6 Astra", reasoning=True, extra={"baseUrl": AZURE_BASEURL}),
    ],
    "google-vertex": [
        model_entry("gemini-3.8-flash", "Gemini 3.8 Flash", reasoning=True, extra={"input": ["text", "image"]}),
    ],
    "opencode-go": [
        model_entry("gpt-5.6-luna", "GPT-5.6 Luna", reasoning=True, extra={"thinkingLevelMap": {"max": "max"}}),
        model_entry("muse-spark-1.3-contributor", "Muse Spark 1.3 Contributor", reasoning=True, extra={"thinkingLevelMap": {"max": "max"}}),
    ],
}
allowed_ids_by_provider={
    "azure-openai-responses": {"gpt-5.6-sol", "gpt-5.6-luna", "grok-4.6", "gpt-6-astra"},
    "google-vertex": {"gemini-3.8-flash"},
    "opencode": set(),
    "opencode-go": {"gpt-5.6-luna", "muse-spark-1.3-contributor"},
}
allowed_ids=set().union(*allowed_ids_by_provider.values())
for prov, explicit_models in desired_explicit.items():
    prov_cfg=providers.setdefault(prov,{})
    overrides=prov_cfg.setdefault("modelOverrides",{})
    wildcard={"contextWindow": DEFAULT_CONTEXT_WINDOW, "maxInputTokens": DEFAULT_CONTEXT_WINDOW, "limitTokens": DEFAULT_CONTEXT_WINDOW, "reasoning": True}
    if overrides.get("*")!=wildcard:
        overrides["*"]=wildcard
        changed=True
    for m in explicit_models:
        mid=m["id"]
        window=context_window_for(mid)
        current_override=overrides.get(mid) or {}
        current_thinking_map=current_override.get("thinkingLevelMap") or {}
        needs_max_thinking_map = mid in ("gpt-5.6-luna", "muse-spark-1.3-contributor") and current_thinking_map.get("max") != "max"
        if current_override.get("contextWindow")!=window or needs_max_thinking_map:
            updated_override=dict(current_override)
            updated_override["contextWindow"]=window
            if mid in ("gpt-5.6-luna", "muse-spark-1.3-contributor"):
                updated_override["thinkingLevelMap"]={**current_thinking_map, "max": "max"}
            overrides[mid]=updated_override
            changed=True
    existing_models=prov_cfg.get("models")
    if not isinstance(existing_models,list):
        existing_models=[]
    existing_models=[mm for mm in existing_models if isinstance(mm,dict)]
    existing_ids={mm.get("id") for mm in existing_models}
    for m in explicit_models:
        if m["id"] not in existing_ids:
            existing_models.append(m)
            changed=True
        else:
            window=context_window_for(m["id"])
            label=context_label(window)
            for em in existing_models:
                if em.get("id")==m["id"]:
                    for kk in ("contextWindow","maxInputTokens","limitTokens"):
                        if em.get(kk)!=window:
                            em[kk]=window
                            changed=True
                    if label not in em.get("name",""):
                        em["name"]=m["name"]
                        changed=True
                    if em.get("reasoning") is not m.get("reasoning", False):
                        em["reasoning"]=m.get("reasoning", False)
                        changed=True
                    if m.get("thinkingLevelMap"):
                        thinking_level_map=dict(em.get("thinkingLevelMap") or {})
                        thinking_level_map.update(m["thinkingLevelMap"])
                        if em.get("thinkingLevelMap") != thinking_level_map:
                            em["thinkingLevelMap"]=thinking_level_map
                            changed=True
                    if prov=="azure-openai-responses":
                        if _azure_resource:
                            _env_baseurl=f"https://{_azure_resource}.openai.azure.com/openai/v1"
                            if em.get("baseUrl")!=_env_baseurl:
                                em["baseUrl"]=_env_baseurl
                                changed=True
                        elif not em.get("baseUrl"):
                            em["baseUrl"]=m["baseUrl"]
                            changed=True
    allowed_for_provider = allowed_ids_by_provider[prov]
    prov_cfg["models"]=[mm for mm in existing_models if mm.get("id") in allowed_for_provider]
    # filter overrides
    prov_cfg["modelOverrides"]={k:v for k,v in overrides.items() if k=="*" or k in allowed_for_provider}
    if len(prov_cfg["modelOverrides"])!=len(overrides):
        changed=True
# OpenCode has no configured models in this setup. Remove its stale custom
# provider block rather than leaving GPT or Muse entries behind.
if "opencode" in providers:
    del providers["opencode"]
    changed=True
# Remove other unapproved providers.
for prov in list(providers.keys()):
    if prov not in desired_explicit:
        allowed_for_provider=allowed_ids_by_provider.get(prov, allowed_ids)
        has_allowed=any(k in allowed_for_provider for k in providers[prov].get("modelOverrides",{}).keys())
        if not has_allowed:
            del providers[prov]
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
    try:
        path.write_text(json.dumps(data, indent=2, sort_keys=True)+"\n")
        print(f"patched {path} to 256k/Grok", file=sys.stderr)
    except OSError as e:
        print(f"skipping unwritable {path}: {e}", file=sys.stderr)
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
        ("google-vertex", "gemini-3.8-flash"), ("google-vertex", "gemini-1.5-flash"), ("google-vertex", "gemini-1.5-flash-8b"), ("google-vertex", "gemini-1.5-pro"),
        ("google-vertex", "gemini-2.0-flash"), ("google-vertex", "gemini-2.0-flash-lite"), ("google-vertex", "gemini-2.5-flash"),
        ("opencode-go", "gpt-5.6-luna"), ("opencode-go", "muse-spark-1.3-contributor"),
        ("azure-openai-responses", "grok-4.6"), ("azure-openai-responses", "gpt-5.6-sol"), ("azure-openai-responses", "gpt-5.6-luna"), ("azure-openai-responses", "gpt-6-astra"),
        ("openrouter", "anthropic/claude-opus-4.5"), ("openrouter", "openrouter/auto"),
    ]
    # merge without duplicates
    seen = set(models)
    for p in fallback:
        if p not in seen:
            models.append(p)
            seen.add(p)

# Ensure critical custom models are present even when discovery succeeded (fresh install must have Grok 4.6 and GPT-5.6 Luna on the configured providers)
for prov_model in [("azure-openai-responses", "grok-4.6"), ("azure-openai-responses", "gpt-5.6-sol"), ("azure-openai-responses", "gpt-5.6-luna"), ("azure-openai-responses", "gpt-6-astra"), ("google-vertex", "gemini-3.8-flash"), ("opencode-go", "gpt-5.6-luna"), ("opencode-go", "muse-spark-1.3-contributor")]:
    if prov_model not in models:
        models.append(prov_model)

# Build providers dict with modelOverrides -> contextWindow (256k default; Azure Grok 4.6 is 180k)
DEFAULT_CONTEXT_WINDOW = 256000
GROK_46_CONTEXT_WINDOW = 180000
def context_window_for(model_id):
    return GROK_46_CONTEXT_WINDOW if model_id == "grok-4.6" else DEFAULT_CONTEXT_WINDOW
def context_label(tokens):
    return f"{tokens // 1000}k"
def token_fields(model_id):
    window = context_window_for(model_id)
    return {"contextWindow": window, "maxInputTokens": window, "limitTokens": window}
def model_entry(model_id, name, *, reasoning, extra=None):
    entry = {"id": model_id, "name": f"{name} ({context_label(context_window_for(model_id))})", **token_fields(model_id), "maxTokens": 16384, "reasoning": reasoning}
    if extra:
        entry.update(extra)
    return entry
for provider, model in models:
    if provider not in providers:
        providers[provider] = {}
    providers[provider][model] = {"contextWindow": context_window_for(model)}

# OpenCode has no custom model entries. Muse Spark is configured only on
# opencode-go, where the contributor model is advertised.

# Also ensure we have at least these provider keys even if no models discovered for them yet
for p in ["google-vertex", "opencode", "opencode-go", "openrouter", "azure-openai-responses"]:
    providers.setdefault(p, {})

# Azure custom models need an explicit baseUrl: prime-agent silently drops custom models
# whose baseUrl resolves falsy (built-in azure models have baseUrl ""). The URL is built
# from AZURE_OPENAI_RESOURCE_NAME when set (AZURE_OPENAI_BASE_URL /
# AZURE_OPENAI_RESOURCE_NAME env vars still override it at request time).
_azure_resource = os.environ.get("AZURE_OPENAI_RESOURCE_NAME", "").strip()
AZURE_BASEURL = f"https://{_azure_resource}.openai.azure.com/openai/v1" if _azure_resource else "https://YOUR-RESOURCE-NAME.openai.azure.com/openai/v1"
# Build final output with defaults 256k and explicit custom models (ensures Grok 4.6 is always present on Azure)
custom_explicit = {
    "azure-openai-responses": [
        model_entry("gpt-5.6-sol", "GPT-5.6 Sol", reasoning=True, extra={"baseUrl": AZURE_BASEURL}),
        model_entry("gpt-5.6-luna", "GPT-5.6 Luna", reasoning=True, extra={"thinkingLevelMap": {"max": "max"}, "baseUrl": AZURE_BASEURL}),
        model_entry("grok-4.6", "Grok 4.6", reasoning=False, extra={"baseUrl": AZURE_BASEURL}),
        model_entry("gpt-6-astra", "GPT-6 Astra", reasoning=True, extra={"baseUrl": AZURE_BASEURL}),
    ],
    "google-vertex": [
        model_entry("gemini-3.8-flash", "Gemini 3.8 Flash", reasoning=True, extra={"input": ["text", "image"]}),
    ],
    "opencode-go": [
        model_entry("gpt-5.6-luna", "GPT-5.6 Luna", reasoning=True, extra={"thinkingLevelMap": {"max": "max"}}),
        model_entry("muse-spark-1.3-contributor", "Muse Spark 1.3 Contributor", reasoning=True, extra={"thinkingLevelMap": {"max": "max"}}),
    ],
}

# Ensure every provider has a wildcard 256k override and the custom models are present
for prov in list(providers.keys()):
    overrides = providers[prov]
    # add wildcard
    overrides["*"] = {"contextWindow": DEFAULT_CONTEXT_WINDOW, "maxInputTokens": DEFAULT_CONTEXT_WINDOW, "limitTokens": DEFAULT_CONTEXT_WINDOW, "reasoning": True}
# Ensure providers for custom explicit exist even if not in discovered list
for prov in custom_explicit:
    providers.setdefault(prov, {})
    providers[prov]["*"] = {"contextWindow": DEFAULT_CONTEXT_WINDOW, "maxInputTokens": DEFAULT_CONTEXT_WINDOW, "limitTokens": DEFAULT_CONTEXT_WINDOW, "reasoning": True}
    for m in custom_explicit[prov]:
        window = context_window_for(m["id"])
        model_override = providers[prov].setdefault(m["id"], {"contextWindow": window, "maxInputTokens": window, "limitTokens": window, "reasoning": True})
        model_override["contextWindow"] = window
        if m["id"] in ("gpt-5.6-luna", "muse-spark-1.3-contributor"):
            model_override["thinkingLevelMap"] = {**(model_override.get("thinkingLevelMap") or {}), "max": "max"}

# Filter by provider. OpenCode has no configured models; opencode-go keeps
# only the models advertised by that endpoint.
allowed_ids_by_provider = {
    "azure-openai-responses": {"gpt-5.6-sol", "gpt-5.6-luna", "grok-4.6", "gpt-6-astra"},
    "google-vertex": {"gemini-3.8-flash"},
    "opencode": set(),
    "opencode-go": {"gpt-5.6-luna", "muse-spark-1.3-contributor"},
}
allowed_ids = set().union(*allowed_ids_by_provider.values())
for prov in list(providers.keys()):
    allowed_for_provider = allowed_ids_by_provider.get(prov, allowed_ids)
    filtered = {}
    for k, v in providers[prov].items():
        if k == "*" or k in allowed_for_provider:
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
    "defaults": {"contextWindow": DEFAULT_CONTEXT_WINDOW, "maxInputTokens": DEFAULT_CONTEXT_WINDOW, "limitTokens": DEFAULT_CONTEXT_WINDOW, "reasoning": True},
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
    if ! mkdir -p "$d" 2>/dev/null; then
      warn "skipping unwritable $d (run as root to provision it)"
      continue
    fi
    # The workspace bind mount and the persisted Prime Agent bind mount can
    # expose the same file through different path names. Compare file identity
    # so cp is not asked to copy models.json onto itself.
    if [ ! "$models_source" -ef "$d/models.json" ]; then
      if ! cp "$models_source" "$d/models.json" 2>/dev/null; then
        warn "skipping unwritable $d/models.json (run as root to provision it)"
        continue
      fi
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
  # Determine users to set shell for: current user, original sudo user, all
  # console login users (native VM admins without SUDO_USER), and overlord.
  local users=()
  users+=("$(whoami)")
  if [ -n "${SUDO_USER:-}" ] && [ "${SUDO_USER}" != "$(whoami)" ] && [ "${SUDO_USER}" != "root" ]; then
    users+=("${SUDO_USER}")
  fi
  # Also ensure overlord user gets zsh if exists
  if id overlord >/dev/null 2>&1; then
    users+=("overlord")
  fi
  local cu
  while IFS= read -r cu; do
    users+=("$cu")
  done < <(console_login_users)
  local seen_users=" "
  local u
  for u in "${users[@]}"; do
    case "$seen_users" in *" $u "*) continue ;; esac
    seen_users="$seen_users$u "
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
# Prove the target user can run the toolchain WITHOUT sudo. If a published
# binary is still locked (root-installed files at 700), open its tool root
# with o+rX (read + traverse, no write) and re-check.
ensure_target_tool_access() {
  [ -n "${NATIVE_USER_INSTALL:-}" ] || return 0
  local tool bin_path tool_root
  for tool in node npm npx prime-agent git; do
    if as_target env PATH="/usr/local/bin:/usr/bin:/bin" "$tool" --version >/dev/null 2>&1; then
      info "$tool runs as $TARGET_USER without sudo"
      continue
    fi
    warn "$tool does NOT run as $TARGET_USER; attempting permission repair..."
    bin_path="$(readlink -f "/usr/local/bin/$tool" 2>/dev/null || command -v "$tool" 2>/dev/null || true)"
    tool_root=""
    case "$bin_path" in
      /root/.nvm/*) tool_root="/root/.nvm" ;;
      /root/*) tool_root="/root" ;;
      /home/*) tool_root="$(printf '%s' "$bin_path" | cut -d/ -f1-3)" ;;
    esac
    if [ -n "$tool_root" ] && [ -d "$tool_root" ] && [ "$(id -u)" -eq 0 ]; then
      chmod -R o+rX "$tool_root" 2>/dev/null && info "opened $tool_root (o+rX) for $tool" || true
    fi
    if as_target env PATH="/usr/local/bin:/usr/bin:/bin" "$tool" --version >/dev/null 2>&1; then
      info "$tool runs as $TARGET_USER without sudo (after repair)"
    else
      warn "$tool STILL needs sudo for $TARGET_USER. Diagnose with: sudo -H -u $TARGET_USER $tool --version; ls -la $bin_path"
    fi
  done
}

install_prime_agent
install_dsh
install_oh_my_pi
install_codex
sync_prime_agent_rc
# This run created home files as root (omz, plugins, rc blocks); hand them back.
own_all_provisioned_homes
publish_tool_commands
ensure_cross_user_tool_access
ensure_target_tool_access
install_prime_agent_skills
configure_prime_agent_tools
configure_prime_agent_models
configure_omp_models
configure_codex
make_zsh_default
verify_login_shell_tools

info "setup complete. Restart shell or run 'zsh' to use new config."
info "Tools: zsh $(zsh --version 2>/dev/null), nvim $(nvim --version 2>/dev/null | head -n1), zellij $(zellij --version 2>/dev/null), fzf $(fzf --version 2>/dev/null)"
