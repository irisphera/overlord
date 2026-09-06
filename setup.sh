#!/usr/bin/env bash
# Shared, self-contained installer for Debian 13 and Ubuntu LTS containers/VMs.
# Sourcing defines functions only; execution is centralized in main below.

info() { printf '[setup] %s\n' "$*"; }
warn() { printf '[setup] WARN: %s\n' "$*" >&2; }
die() { printf '[setup] ERROR: %s\n' "$*" >&2; return 1; }

setup_help() {
  cat <<'HELP'
Usage: bash setup.sh [--user NAME] [--profile native|container] [--versions FILE]
Install the shared development environment on Debian 13 or Ubuntu 22.04/24.04/26.04 LTS.
The target defaults to SUDO_USER or the invoking non-root user. Root must select
an account explicitly. Configuration belongs only to that account; existing
sessions, authentication and databases are preserved.

Examples:
  bash setup.sh
  sudo bash setup.sh --user developer
  bash setup.sh --user overlord --profile container
  curl -fsSL https://raw.githubusercontent.com/irisphera/overlord/main/setup.sh | bash -s -- --user developer
HELP
}

require_supported_os() {
  local release_file="${1:-/etc/os-release}" ID="" VERSION_ID=""
  [ -r "$release_file" ] || { die 'cannot identify operating system'; return 1; }
  . "$release_file"
  case "$ID:$VERSION_ID" in
    debian:13|ubuntu:22.04|ubuntu:24.04|ubuntu:26.04) return 0 ;;
    *) die "unsupported system ${ID:-unknown} ${VERSION_ID:-unknown}; Debian 13 or Ubuntu 22.04/24.04/26.04 LTS is required"; return 1 ;;
  esac
}

resolve_setup_identity() {
  TARGET_USER="${REQUESTED_USER:-${SUDO_USER:-}}"
  if [ -z "$TARGET_USER" ]; then
    [ "$(id -u)" -ne 0 ] || { die 'root must select a target account with --user NAME'; return 1; }
    TARGET_USER="$(id -un)"
  fi
  local entry
  entry="$(getent passwd "$TARGET_USER")" || { die "unknown account: $TARGET_USER"; return 1; }
  IFS=: read -r TARGET_USER _ TARGET_UID TARGET_GID _ TARGET_HOME _ <<< "$entry"
  [ -d "$TARGET_HOME" ] && [ ! -L "$TARGET_HOME" ] || {
    die "target home must be an existing real directory: $TARGET_HOME"; return 1;
  }
  export TARGET_USER TARGET_HOME TARGET_UID TARGET_GID
}

load_tool_versions() {
  # Embedded defaults keep curl | bash standalone. A local manifest overrides
  # defaults; explicit environment versions override the manifest.
  local -A versions=( [ZELLIJ_VERSION]=0.43.1 [NODE_VERSION]=24.20.0 [NVIM_VERSION]=0.12.5
    [PRIME_AGENT_VERSION]=0.9.2 [CODEGRAPH_VERSION]=1.6.0 [CODEX_VERSION]=0.153.4 )
  local -A seen=()
  local line name value
  if [ -n "${VERSION_FILE:-}" ]; then
    [ -r "$VERSION_FILE" ] || { die "cannot read version manifest: $VERSION_FILE"; return 1; }
    while IFS= read -r line || [ -n "$line" ]; do
      [[ "$line" =~ ^[[:space:]]*(#|$) ]] && continue
      [[ "$line" =~ ^([A-Z_]+)=([0-9]+\.[0-9]+\.[0-9]+(-[A-Za-z0-9.]+)?)$ ]] || {
        die 'invalid version manifest assignment'; return 1;
      }
      name="${BASH_REMATCH[1]}"; value="${BASH_REMATCH[2]}"
      [[ -v versions[$name] && ! -v seen[$name] ]] || { die "unknown or duplicate version: $name"; return 1; }
      versions[$name]="$value"; seen[$name]=1
    done < "$VERSION_FILE"
  fi
  for name in "${!versions[@]}"; do
    value="${!name:-${versions[$name]}}"
    [[ "$value" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[A-Za-z0-9.]+)?$ ]] || { die "invalid $name"; return 1; }
    printf -v "$name" '%s' "$value"
    export "$name"
  done
  [[ "$NODE_VERSION" == 24.* ]] || { die 'NODE_VERSION must select Node 24'; return 1; }
}

run_sudo() {
  if [ "$(id -u)" -eq 0 ]; then "$@"; else sudo -n -- "$@"; fi
}

as_target() {
  local user_env=(env HOME="$TARGET_HOME" USER="$TARGET_USER" LOGNAME="$TARGET_USER"
    XDG_CONFIG_HOME="$TARGET_HOME/.config" XDG_CACHE_HOME="$TARGET_HOME/.cache"
    XDG_DATA_HOME="$TARGET_HOME/.local/share" XDG_STATE_HOME="$TARGET_HOME/.local/state")
  if [ "$(id -u)" -eq "$TARGET_UID" ]; then
    "${user_env[@]}" "$@"
  elif [ "$(id -u)" -eq 0 ]; then
    runuser -u "$TARGET_USER" -- "${user_env[@]}" "$@"
  else
    die 'cannot execute as target account without root privileges'
  fi
}


ensure_git_safe_directories() {
  # Migrate the old installer-owned wildcard without deleting explicit entries.
  local status=0
  git config --system --fixed-value --unset-all safe.directory '*' || status=$?
  [ "$status" -eq 0 ] || [ "$status" -eq 5 ] || return "$status"
  if [ "$SETUP_PROFILE" = container ]; then
    if ! git config --system --get-all safe.directory | grep -Fxq /workspace; then
      git config --system --add safe.directory /workspace
    fi
  fi
}

download() { curl --fail --location --silent --show-error --connect-timeout 15 "$1" -o "$2"; }

tool_version() {
  "$1" --version 2>&1 | sed -nE 's/^[^0-9]*([0-9]+\.[0-9]+\.[0-9]+(-[A-Za-z0-9.]+)?).*/\1/p' | head -n1
}

verify_version() {
  local actual
  actual="$(tool_version "$1")" || { die "cannot execute $1"; return 1; }
  [ "$actual" = "$2" ] || { die "$1 version mismatch: expected $2, got $actual"; return 1; }
}

publish_binary() {
  # Only root-owned staged installations may be published system-wide.
  local source="$1" name="$2" temporary
  [[ "$source" == /opt/overlord/* ]] || { die "unsafe tool publication: $name"; return 1; }
  temporary="$(mktemp -d /usr/local/bin/.overlord-link.XXXXXXXX)"
  ln -s "$source" "$temporary/link"
  mv -Tf "$temporary/link" "/usr/local/bin/$name"
  rmdir "$temporary"
}

install_npm_tool() (
  set -euo pipefail
  local name="$1" package="$2" version="$3" destination stage
  destination="/opt/overlord/$name-$version"
  if [ ! -x "$destination/bin/$name" ]; then
    stage="$(mktemp -d /opt/overlord/.npm.XXXXXXXX)"
    trap 'rm -rf "$stage"' EXIT
    npm install --global --prefix "$stage" --no-audit --no-fund "$package@$version"
    verify_version "$stage/bin/$name" "$version"
    chmod -R a+rX "$stage"
    [ ! -e "$destination" ] || { die "incomplete installation exists: $destination"; exit 1; }
    mv "$stage" "$destination"
  fi
  verify_version "$destination/bin/$name" "$version"
  publish_binary "$destination/bin/$name" "$name"
)

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
    ripgrep
    fd-find
    fzf
    unzip
    locales
    jq
    xdg-utils
    python3-yaml
    xz-utils
    util-linux
    passwd
    python3-tomlkit
  )
  # Check which are missing
  local missing=()
  for p in "${pkgs[@]}"; do
    case "${p}" in
      fd-find) command -v fdfind >/dev/null 2>&1 || command -v fd >/dev/null 2>&1 || missing+=("${p}") ;;
      fzf) command -v fzf >/dev/null 2>&1 || missing+=("${p}") ;;
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
  # fd-find installs as fdfind on Debian; link to fd.
  if command -v fdfind >/dev/null 2>&1 && ! command -v fd >/dev/null 2>&1; then
    run_sudo ln -sf "$(command -v fdfind)" /usr/local/bin/fd 2>/dev/null || true
  fi
}


# --- zellij (pinned) ---
install_zellij() (
  set -euo pipefail
  local destination="/opt/overlord/zellij-$ZELLIJ_VERSION" stage arch
  if [ ! -x "$destination/zellij" ]; then
    case "$(uname -m)" in
      x86_64|amd64) arch=x86_64 ;;
      aarch64|arm64) arch=aarch64 ;;
      *) die 'unsupported CPU architecture'; exit 1 ;;
    esac
    stage="$(mktemp -d /opt/overlord/.zellij.XXXXXXXX)"
    trap 'rm -rf "$stage"' EXIT
    download "https://github.com/zellij-org/zellij/releases/download/v$ZELLIJ_VERSION/zellij-$arch-unknown-linux-musl.tar.gz" "$stage/archive.tar.gz"
    tar xzf "$stage/archive.tar.gz" -C "$stage"
    rm "$stage/archive.tar.gz"
    verify_version "$stage/zellij" "$ZELLIJ_VERSION"
    chmod -R a+rX "$stage"
    [ ! -e "$destination" ] || { die "incomplete installation exists: $destination"; exit 1; }
    mv "$stage" "$destination"
  fi
  verify_version "$destination/zellij" "$ZELLIJ_VERSION"
  publish_binary "$destination/zellij" zellij
)

# Ubuntu's packaged Neovim can be too old for the current LazyVim starter.
install_neovim() (
  set -euo pipefail
  local destination="/opt/overlord/nvim-$NVIM_VERSION" stage arch archive checksum
  if [ ! -x "$destination/bin/nvim" ]; then
    case "$(uname -m)" in
      x86_64|amd64) arch=x86_64 ;;
      aarch64|arm64) arch=arm64 ;;
      *) die 'unsupported CPU architecture'; exit 1 ;;
    esac
    stage="$(mktemp -d /opt/overlord/.nvim.XXXXXXXX)"
    trap 'rm -rf "$stage"' EXIT
    archive="nvim-linux-$arch.tar.gz"
    download "https://api.github.com/repos/neovim/neovim/releases/tags/v$NVIM_VERSION" "$stage/release.json"
    checksum="$(jq -er --arg name "$archive" '.assets[] | select(.name == $name) | .digest | select(test("^sha256:[0-9a-f]{64}$")) | sub("^sha256:"; "")' "$stage/release.json")"
    download "https://github.com/neovim/neovim/releases/download/v$NVIM_VERSION/$archive" "$stage/$archive"
    printf '%s  %s\n' "$checksum" "$stage/$archive" | sha256sum --check --status
    mkdir "$stage/runtime"
    tar xzf "$stage/$archive" -C "$stage/runtime" --strip-components=1
    verify_version "$stage/runtime/bin/nvim" "$NVIM_VERSION"
    chmod -R a+rX "$stage/runtime"
    [ ! -e "$destination" ] || { die "incomplete installation exists: $destination"; exit 1; }
    mv "$stage/runtime" "$destination"
  fi
  verify_version "$destination/bin/nvim" "$NVIM_VERSION"
  publish_binary "$destination/bin/nvim" nvim
)

# --- One root-owned Node distribution for both deployment targets ---
install_node() (
  set -euo pipefail
  local destination="/opt/overlord/node-$NODE_VERSION" stage arch archive npmrc
  if [ ! -x "$destination/bin/node" ]; then
    case "$(uname -m)" in
      x86_64|amd64) arch=x64 ;;
      aarch64|arm64) arch=arm64 ;;
      *) die 'unsupported CPU architecture'; exit 1 ;;
    esac
    stage="$(mktemp -d /opt/overlord/.node.XXXXXXXX)"
    trap 'rm -rf "$stage"' EXIT
    archive="node-v$NODE_VERSION-linux-$arch.tar.xz"
    download "https://nodejs.org/dist/v$NODE_VERSION/$archive" "$stage/$archive"
    download "https://nodejs.org/dist/v$NODE_VERSION/SHASUMS256.txt" "$stage/SHASUMS256.txt"
    (cd "$stage"; grep "  $archive\$" SHASUMS256.txt | sha256sum --check --status)
    mkdir "$stage/runtime"
    tar xJf "$stage/$archive" -C "$stage/runtime" --strip-components=1
    verify_version "$stage/runtime/bin/node" "$NODE_VERSION"
    chmod -R a+rX "$stage/runtime"
    [ ! -e "$destination" ] || { die "incomplete installation exists: $destination"; exit 1; }
    mv "$stage/runtime" "$destination"
  fi
  verify_version "$destination/bin/node" "$NODE_VERSION"
  # Workspace-installed globals belong on PATH, not in the versioned Node tree.
  npmrc="$(mktemp "$destination/lib/node_modules/npm/.npmrc.XXXXXXXX")"
  printf 'prefix=/usr/local\n' > "$npmrc"
  chmod 0644 "$npmrc"
  mv -Tf "$npmrc" "$destination/lib/node_modules/npm/npmrc"
  local command
  for command in node npm npx; do publish_binary "$destination/bin/$command" "$command"; done
)

# --- AWS CLI v2 ---
install_aws_cli() (
  set -euo pipefail
  if /opt/overlord/aws-cli/v2/current/bin/aws --version >/dev/null 2>&1; then
    publish_binary /opt/overlord/aws-cli/v2/current/bin/aws aws
    return
  fi
  local stage arch
  case "$(uname -m)" in x86_64|amd64) arch=x86_64 ;; aarch64|arm64) arch=aarch64 ;; *) exit 1 ;; esac
  stage="$(mktemp -d)"
  trap 'rm -rf "$stage"' EXIT
  download "https://awscli.amazonaws.com/awscli-exe-linux-$arch.zip" "$stage/aws.zip"
  unzip -q "$stage/aws.zip" -d "$stage"
  "$stage/aws/install" --update --install-dir /opt/overlord/aws-cli --bin-dir /usr/local/bin
  /usr/local/bin/aws --version
)

# --- uv (python project manager used by workspace setups) ---
install_uv() (
  set -euo pipefail
  local stage destination="/opt/overlord/uv"
  if "$destination/uv" --version >/dev/null 2>&1; then
    publish_binary "$destination/uv" uv
    publish_binary "$destination/uvx" uvx
    return
  fi
  stage="$(mktemp -d /opt/overlord/.uv.XXXXXXXX)"
  trap 'rm -rf "$stage"' EXIT
  download https://astral.sh/uv/install.sh "$stage/install.sh"
  UV_UNMANAGED_INSTALL="$stage/bin" sh "$stage/install.sh"
  "$stage/bin/uv" --version
  chmod -R a+rX "$stage/bin"
  [ ! -e "$destination" ] || { die "incomplete installation exists: $destination"; exit 1; }
  mv "$stage/bin" "$destination"
  publish_binary "$destination/uv" uv
  publish_binary "$destination/uvx" uvx
)

# Make the shared tool distribution visible in each shell startup path. SSH bash
# reads .bash_profile/.profile, while zellij zsh reads .zprofile/.zshrc.
ensure_node_shell_rc() {
  local rc
  for rc in .zshrc .zprofile .bashrc .bash_profile .profile; do
    upsert_overlord_shell_block "$TARGET_HOME/$rc" 'Overlord: persistent tool PATH' <<'PATH_BLOCK'
# --- Overlord: persistent tool PATH ---
export PATH="/usr/local/bin:$HOME/.local/bin:$PATH"
PATH_BLOCK
  done
}


verify_login_shell_tools() {
  [ "$(id -u)" -eq "$TARGET_UID" ] || { die 'login verification requires the target UID'; return 1; }
  local command
  for command in node npm npx nvim prime-agent git omp codex; do
    env -i HOME="$TARGET_HOME" USER="$TARGET_USER" LOGNAME="$TARGET_USER" \
      TERM=xterm-256color PATH=/usr/local/bin:/usr/bin:/bin \
      zsh -lic "$command --version" >/dev/null || { die "$command does not run as $TARGET_USER"; return 1; }
  done
}

install_codegraph() { install_npm_tool codegraph @colbymchenry/codegraph "$CODEGRAPH_VERSION"; }


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

install_oh_my_zsh() {
  local omz_dir="$TARGET_HOME/.oh-my-zsh"
  if [ -f "$omz_dir/oh-my-zsh.sh" ]; then
    update_git_checkout "$omz_dir" oh-my-zsh
  else
    git clone --depth=1 https://github.com/ohmyzsh/ohmyzsh.git "$omz_dir"
  fi
}


# --- zsh plugins: autosuggestions, syntax-highlighting, completions, fzf-tab optional ---

install_zsh_plugins() {
  local target_home="$TARGET_HOME"
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
}

# Replace one Overlord-managed shell block identified by its marker prefix.
# Reads the replacement block from stdin so multiline content stays intact.
upsert_overlord_shell_block() {
  local rc="$1" marker_prefix="$2" blockfile
  blockfile="$(mktemp)"
  cat > "$blockfile"
  python_config "$rc" "$marker_prefix" "$blockfile" <<'PY'
path, prefix, block_path = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
block = block_path.read_text().replace("\r\n", "\n").strip("\n")
block_path.unlink()
original = read_text(path)
lines = (original or "").splitlines(keepends=True)
start = f"# --- {prefix}"
end = f"# --- End {prefix} ---"
legacy_prefix = start.replace("skip Debian global", "skip Ubuntu global")
terminators = {
    "Overlord: persistent tool PATH": lambda line: line.startswith("export PATH="),
    "Overlord: skip Debian global compinit": lambda line: line == "skip_global_compinit=1",
    "Overlord: oh-my-zsh": lambda line: line in ("source $ZSH/oh-my-zsh.sh", "source ${ZSH}/oh-my-zsh.sh"),
    "Overlord: colors + aliases": lambda line: line.startswith("alias egrep="),
    "Overlord: bash prompt": lambda line: line == "fi",
    "Overlord: auto-start zellij": lambda line: line == "fi",
}
out = []
replaced = False
i = 0
while i < len(lines):
    if not lines[i].startswith((start, legacy_prefix)):
        out.append(lines[i])
        i += 1
        continue
    j = i + 1
    while j < len(lines) and not lines[j].startswith("# --- "):
        j += 1
    if j < len(lines) and lines[j].strip() == end:
        stop = j + 1
    else:
        # Old blocks had no end marker. Remove only a recognized complete block,
        # never everything up to the next marker (which can include user code).
        predicate = terminators[prefix]
        stop = next((k + 1 for k in range(i + 1, j) if predicate(lines[k].strip())), None)
        if stop is None:
            raise ValueError("unrecognized legacy shell block; original preserved")
    if not replaced:
        out.append(block + "\n" + end + "\n")
        replaced = True
    i = stop
text = "".join(out)
if not replaced:
    text = text.rstrip("\n") + ("\n\n" if text else "") + block + "\n" + end + "\n"
write_file(path, original, text)
PY
}

# Source zsh-autocomplete just before the first oh-my-zsh.sh line.
insert_autocomplete_before_omz() {
  local zshrc="$1"
  python_config "$zshrc" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
original = read_text(path)
text = original or ""
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
write_file(path, original, text if text.endswith("\n") else text + "\n")
PY
}

configure_overlord_zsh_files() {
  local target_home="$1"
  local zshrc="${target_home}/.zshrc"
  local zshenv="${target_home}/.zshenv"
  local bashrc="${target_home}/.bashrc"
  mkdir -p "${target_home}"

  upsert_overlord_shell_block "${zshenv}" "Overlord: skip Debian global compinit" <<'EOS'
# --- Overlord: skip Debian global compinit ---
# Debian /etc/zsh/zshrc runs compinit before ~/.zshrc. That dump never
# includes zsh-autocomplete helpers, so Tab later prints
# "_autocomplete__unambiguous not found".
skip_global_compinit=1
EOS


  if [ ! -f "$zshrc" ] || grep -q 'Overlord: oh-my-zsh' "${zshrc}" || ! grep -q 'oh-my-zsh.sh' "${zshrc}"; then
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
    python_config "${zshrc}" <<'PY'
from pathlib import Path
import re
import sys
path = Path(sys.argv[1])
original = read_text(path)
text = original or ""
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
        write_file(path, original, text)
        print(f"updated plugins in {path}")
else:
    print(f"no plugins= line in {path}, leaving as-is")
PY
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
if [ "${color_prompt:-no}" = yes ]; then
  PS1='\[\033[01;32m\]\u@\h\[\033[00m\]:\[\033[01;34m\]\w\[\033[01;31m\]$(__overlord_git_branch)\[\033[00m\]\$ '
else
  PS1='\u@\h:\w$(__overlord_git_branch)\$ '
fi
EOS

  # Stale dumps from global compinit / old plugin order omit autocomplete helpers.
  rm -f "${target_home}/.zcompdump" "${target_home}/.zcompdump"-* "${target_home}/.cache/zsh/compdump" 2>/dev/null || true
}

# --- zellij config + autostart on SSH (non-interactive, idempotent) ---
ensure_zellij_config() {
  local source
  for source in "${SETUP_DIR:-}/config/zellij-config.kdl" /usr/local/share/overlord/zellij-config.kdl; do
    if [ -r "$source" ]; then
      python_config "$source" "$TARGET_HOME/.config/zellij/config.kdl" <<'PY_ZELLIJ'
source, target = map(Path, sys.argv[1:])
original = read_text(target)
write_file(target, original, source.read_text())
PY_ZELLIJ
      return
    fi
  done
}

ensure_zellij_autostart() {
  local rc
  for rc in "$TARGET_HOME/.zshrc" "$TARGET_HOME/.bashrc"; do
    upsert_overlord_shell_block "$rc" "Overlord: auto-start zellij" <<'EOS'
# --- Overlord: auto-start zellij on SSH ---
# exec makes detach/quit close the SSH shell instead of exposing a parent shell.
if [ -z "${ZELLIJ:-}" ] && [ -t 1 ] && command -v zellij >/dev/null 2>&1; then
  case $- in
    *i*) exec zellij attach --create ;;
  esac
fi
EOS
  done
}


# --- codegraph skill for prime-agent ---
ensure_codegraph_skill() {
  local source destination
  for source in "${SETUP_DIR:-}/skills/codegraph" /usr/local/share/overlord/skills/codegraph; do
    if [ -r "$source/SKILL.md" ]; then
      for destination in "$PRIME_AGENT_CODING_AGENT_DIR/skills/codegraph" "$PI_CODING_AGENT_DIR/skills/codegraph"; do
        mkdir -p "$destination"
        cp -R "$source/." "$destination/"
      done
      return
    fi
  done
}


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

# --- make zsh default shell (non-interactive) ---

# --- prime-agent + models.json (256k contextWindow override for every model) ---
install_prime_agent() (
  set -euo pipefail
  local destination="/opt/overlord/prime-agent-$PRIME_AGENT_VERSION" stage
  if [ ! -x "$destination/bin/prime-agent" ]; then
    stage="$(mktemp -d /opt/overlord/.prime.XXXXXXXX)"
    trap 'rm -rf "$stage"' EXIT
    download https://app.primeintellect.ai/prime-agent/install.sh "$stage/install.sh"
    mkdir "$stage/home"
    HOME="$stage/home" npm_config_prefix="$stage" PRIME_AGENT_INSTALLER_PLAIN=1 \
      PRIME_AGENT_BOOTSTRAP_KERNEL_ON_INSTALL=0 sh "$stage/install.sh" "$PRIME_AGENT_VERSION" </dev/null
    verify_version "$stage/bin/prime-agent" "$PRIME_AGENT_VERSION"
    chmod -R a+rX "$stage"
    [ ! -e "$destination" ] || { die "incomplete installation exists: $destination"; exit 1; }
    mv "$stage" "$destination"
  fi
  verify_version "$destination/bin/prime-agent" "$PRIME_AGENT_VERSION"
  publish_binary "$destination/bin/prime-agent" prime-agent
)


install_oh_my_pi() (
  set -euo pipefail
  local want="${OMP_VERSION:-}" stage destination
  if [ -z "$want" ]; then want="$(npm view @oh-my-pi/pi-coding-agent version)"; fi
  [[ "$want" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[A-Za-z0-9.]+)?$ ]] || { die 'cannot resolve OMP release'; exit 1; }
  destination="/opt/overlord/omp-$want"
  if [ ! -x "$destination/omp" ]; then
    stage="$(mktemp -d /opt/overlord/.omp.XXXXXXXX)"
    trap 'rm -rf "$stage"' EXIT
    download https://omp.sh/install "$stage/install.sh"
    PI_INSTALL_DIR="$stage/bin" sh "$stage/install.sh" --binary --ref "v$want"
    verify_version "$stage/bin/omp" "$want"
    chmod -R a+rX "$stage/bin"
    [ ! -e "$destination" ] || { die "incomplete installation exists: $destination"; exit 1; }
    mv "$stage/bin" "$destination"
  fi
  verify_version "$destination/omp" "$want"
  publish_binary "$destination/omp" omp
)

install_codex() { install_npm_tool codex @openai/codex "$CODEX_VERSION"; }

# Oh My Pi: Astra/medium by default, low for lightweight work, off for exploration.
# Merge managed models and roles, preserving other settings and first backups.
# Shared Python I/O keeps managed formats atomic and preserves permissions.
python_config() {
  { config_python_helpers; cat; } | /usr/bin/python3 - "$@"
}

config_python_helpers() {
  cat <<'PY_HELPERS'
import os
import stat
import sys
import tempfile
from pathlib import Path

def ensure_directory(path):
    path = Path(path).absolute()
    for current in (*reversed(path.parents), path):
        try:
            current.mkdir(mode=0o700)
        except FileExistsError:
            if not stat.S_ISDIR(current.lstat().st_mode):
                raise ValueError("configuration path must use real directories")

def read_text(path):
    ensure_directory(path.parent)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, encoding="utf-8") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("configuration must be a regular file")
        return stream.read()

def mapping(parent, key):
    value = parent.setdefault(key, {})
    if not isinstance(value, dict):
        raise ValueError("configuration entry must be a mapping")
    return value

def write_file(path, original, rendered):
    if original == rendered:
        return
    if read_text(path) != original:
        raise ValueError("configuration changed during setup")
    mode = stat.S_IMODE(path.lstat().st_mode) if original is not None else 0o600
    if original is not None:
        backup = path.with_suffix(path.suffix + ".bak")
        try:
            fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(original)
                stream.flush()
                os.fchmod(stream.fileno(), mode)
                os.fsync(stream.fileno())
    fd, name = tempfile.mkstemp(prefix=".overlord-config-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(rendered)
            stream.flush()
            os.fchmod(stream.fileno(), mode)
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"configured {path}")
PY_HELPERS
}

configure_omp_models() {
  info "configuring Oh My Pi model policy (Astra medium / low / off)..."
  python_config "${PI_CODING_AGENT_DIR:-$TARGET_HOME/.omp/agent}" <<'PYEOF_OMP'
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




def read_config(path):
    existing = read_text(path)
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
        ensure_directory(agent_dir)
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
        extension_original = read_text(extension_path)
        ensure_directory(extension_path.parent)
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
  python_config "${CODEX_HOME:-$TARGET_HOME/.codex}" <<'PYEOF_CODEX'
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
        ensure_directory(home_dir)
        originals, documents = {}, {}
        # Validate all files before modifying any. Malformed user config stays intact.
        for filename in policy:
            target = home_dir / filename
            original = read_text(target)
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
            write_file(home_dir / filename, originals[filename], rendered[filename])
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

  # The skills CLI targets Pi; copy assets into the selected harness directories.
  local pi_skills="$HOME/.pi/agent/skills"
  local agent_skills
  if [ -d "$pi_skills" ]; then
      for agent_skills in "$PRIME_AGENT_CODING_AGENT_DIR/skills" "$PI_CODING_AGENT_DIR/skills"; do
        mkdir -p "$agent_skills"
        cp -a "$pi_skills/." "$agent_skills/"
        info "synced Pi skills to $agent_skills"
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
      for agent_skills in "$pi_skills" "$PRIME_AGENT_CODING_AGENT_DIR/skills" "$PI_CODING_AGENT_DIR/skills"; do
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
    info "installed AWS Agent Toolkit setup skill"
  else
    warn "failed to download AWS Agent Toolkit setup instructions"
  fi
  rm -f "$aws_setup_tmp"
}

configure_prime_agent_tools() {
  info "enabling Prime Agent web search and Context7 tools..."
  local settings_paths=("${PRIME_AGENT_CODING_AGENT_DIR:-$TARGET_HOME/.prime/agent}/settings.json")
  python_config "${settings_paths[@]}" <<'PYEOF'
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
        original = read_text(path)
        settings = parse_jsonc(original) if original is not None else {}
        before = json.dumps(settings, sort_keys=True)
        settings["enableBuiltinSkills"] = True
        bundled = mapping(settings, "bundledSkills")
        bundled["websearch"] = True
        servers = mapping(settings, "mcpServers")
        servers["context7"] = {
            "type": "http",
            "url": "https://mcp.context7.com/mcp",
            "enabled": True,
        }
        if os.environ.get("SETUP_PROFILE", "native") == "container":
            servers["runpod-docs"] = {"type": "http", "url": "https://docs.runpod.io/mcp", "enabled": True}
        else:
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

        if before != json.dumps(settings, sort_keys=True):
            write_file(path, original, json.dumps(settings, indent=2, sort_keys=True) + "\n")
    except Exception as error:
        print(f"could not update {path}: {type(error).__name__}", file=sys.stderr)
        continue
PYEOF

  # Add the Context7 routing skill to both Prime and OMP native roots.
  local agent_dirs=("$PRIME_AGENT_CODING_AGENT_DIR" "$PI_CODING_AGENT_DIR")
  local agent_dir
  for agent_dir in "${agent_dirs[@]}"; do
    if ! mkdir -p "$agent_dir/skills/context7" 2>/dev/null; then
      warn "skipping unwritable $agent_dir (run as root to provision it)"
      continue
    fi
    cat > "$agent_dir/skills/context7/SKILL.md" <<'SKILLEOF'
---
name: context7
description: Look up current library and framework documentation through Context7 MCP. Use when API details, current examples, configuration, or version-specific behavior are needed.
---

# Context7

Use the tools exposed by the `context7` MCP server to resolve a library and retrieve its current documentation. Prefer Context7 over memory when implementation depends on current APIs or version-specific behavior.
SKILLEOF
  done
  if [ "$SETUP_PROFILE" = container ]; then
    mkdir -p "$PRIME_AGENT_CODING_AGENT_DIR/skills/runpod-docs"
    cat > "$PRIME_AGENT_CODING_AGENT_DIR/skills/runpod-docs/SKILL.md" <<'RUNPOD_SKILL'
---
name: runpod-docs
description: Search official Runpod documentation through the public Runpod Docs MCP.
---

# Runpod Docs

Use the Runpod Docs MCP tools for current Runpod product documentation.
RUNPOD_SKILL
  fi
  info "websearch enabled (one-time Serper login: prime-agent /login -> MCP Connections -> Serper)"
  info "Context7 MCP server configured (no login required)"
}

configure_prime_agent_models() {
  python_config "${PRIME_AGENT_CODING_AGENT_DIR:-$TARGET_HOME/.prime/agent}/models.json" <<'PYEOF_PRIME'
import copy
import json

resource = os.environ.get("AZURE_OPENAI_RESOURCE_NAME", "").strip()
base = os.environ.get("AZURE_OPENAI_BASE_URL", "").strip().rstrip("/")
if not base and resource:
    base = f"https://{resource}.openai.azure.com/openai/v1"
desired = {
    "azure-openai-responses": [
        ("gpt-5.6-sol", "GPT-5.6 Sol"), ("gpt-5.6-luna", "GPT-5.6 Luna"),
        ("grok-4.6", "Grok 4.6"), ("gpt-6-astra", "GPT-6 Astra"),
    ],
    "google-vertex": [("gemini-3.8-flash", "Gemini 3.8 Flash")],
    "opencode-go": [("gpt-5.6-luna", "GPT-5.6 Luna"), ("muse-spark-1.3-contributor", "Muse Spark 1.3 Contributor")],
}

for raw in sys.argv[1:]:
    path = Path(raw)
    try:
        original = read_text(path)
        data = json.loads(original) if original is not None else {}
        if not isinstance(data, dict):
            raise ValueError("models must be a mapping")
        before = copy.deepcopy(data)
        defaults = mapping(data, "defaults")
        defaults.update(contextWindow=256000, maxInputTokens=256000, limitTokens=256000, reasoning=True)
        providers = mapping(data, "providers")
        for provider_id, models in desired.items():
            provider = mapping(providers, provider_id)
            entries = provider.setdefault("models", [])
            if not isinstance(entries, list) or any(not isinstance(entry, dict) for entry in entries):
                raise ValueError("models must be a list of mappings")
            overrides = mapping(provider, "modelOverrides")
            for model_id, name in models:
                window = 180000 if model_id == "grok-4.6" else 256000
                fields = dict(contextWindow=window, maxInputTokens=window, limitTokens=window, reasoning=model_id != "grok-4.6")
                matching = [entry for entry in entries if entry.get("id") == model_id]
                if not matching:
                    matching = [{"id": model_id}]
                    entries.extend(matching)
                override = mapping(overrides, model_id)
                override.update(fields)
                for entry in matching:
                    entry.update(fields, name=f"{name} ({window // 1000}k)")
                    entry.setdefault("maxTokens", 16384)
                    if model_id in ("gpt-5.6-luna", "muse-spark-1.3-contributor"):
                        mapping(entry, "thinkingLevelMap")["max"] = "max"
                        mapping(override, "thinkingLevelMap")["max"] = "max"
                    if provider_id == "azure-openai-responses":
                        entry["baseUrl"] = base or entry.get("baseUrl") or "https://YOUR-RESOURCE-NAME.openai.azure.com/openai/v1"
                    if provider_id == "google-vertex":
                        entry.setdefault("input", ["text", "image"])
        if before != data:
            write_file(path, original, json.dumps(data, indent=2, sort_keys=True) + "\n")
    except (OSError, UnicodeError, ValueError, TypeError) as error:
        print(f"skipping invalid or unwritable Prime models in {path}: {type(error).__name__}", file=sys.stderr)
PYEOF_PRIME
}

make_zsh_default() {
  local shell
  shell="$(command -v zsh)"
  if [ "$(getent passwd "$TARGET_USER" | cut -d: -f7)" != "$shell" ]; then
    chsh -s "$shell" "$TARGET_USER"
  fi
}

configure_user() {
  set -euo pipefail
  [ "$(id -u)" -eq "$TARGET_UID" ] || { die 'configuration must run as the target account'; return 1; }
  umask 077
  install_oh_my_zsh
  install_zsh_plugins
  ensure_node_shell_rc
  ensure_zellij_config
  ensure_zellij_autostart
  ensure_codegraph_skill
  install_lazyvim
  install_prime_agent_skills
  configure_prime_agent_tools
  configure_prime_agent_models
  configure_omp_models
  configure_codex
  verify_login_shell_tools
}

setup_system() {
  set -euo pipefail
  export DEBIAN_FRONTEND=noninteractive
  export PATH=/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/bin:/sbin
  APT_UPDATED=0
  mkdir -p /opt/overlord /usr/local/bin /run/lock
  # Serializes native reruns too; no user state is used as a root lock path.
  exec 9>/run/lock/overlord-setup.lock
  flock 9
  install_base_packages
  ensure_git_safe_directories
  if ! locale -a | grep -ixq en_US.utf8; then
    sed -i 's/^# *\(en_US.UTF-8 UTF-8\)/\1/' /etc/locale.gen
    locale-gen en_US.UTF-8
  fi
  (
    # Version probes and upstream installers must not create root-owned runtime
    # files in the selected account's existing agent directories.
    export HOME=/root USER=root LOGNAME=root
    unset XDG_CONFIG_HOME XDG_CACHE_HOME XDG_DATA_HOME XDG_STATE_HOME
    unset PRIME_AGENT_CODING_AGENT_DIR PI_CODING_AGENT_DIR CODEX_HOME
    install_node
    install_zellij
    install_neovim
    install_codegraph
    install_uv
    install_aws_cli
    install_prime_agent
    install_oh_my_pi
    install_codex
  )
  make_zsh_default
  # Transfer function definitions, not a user-editable root script. User startup
  # and configuration code execute only after runuser has dropped privileges.
  { declare -f; printf '\nconfigure_user\n'; } | as_target bash -s
  info "setup complete for $TARGET_USER ($SETUP_PROFILE). Restart your shell."
}

main() {
  set -euo pipefail
  REQUESTED_USER=""
  SETUP_PROFILE=native
  VERSION_FILE=""
  SETUP_DIR=""
  if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
    SETUP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  fi
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --help|-h) setup_help; return 0 ;;
      --user|--profile|--versions)
        [ "$#" -ge 2 ] && [ -n "$2" ] || { die "missing value for $1"; return 2; }
        case "$1" in --user) REQUESTED_USER="$2" ;; --profile) SETUP_PROFILE="$2" ;; --versions) VERSION_FILE="$2" ;; esac
        shift 2 ;;
      *) die "unknown option: $1 (use --help)"; return 2 ;;
    esac
  done
  case "$SETUP_PROFILE" in native|container) ;; *) die 'profile must be native or container'; return 2 ;; esac
  require_supported_os
  resolve_setup_identity
  if [ -z "$VERSION_FILE" ] && [ -n "$SETUP_DIR" ] && [ -f "$SETUP_DIR/config/tool-versions.env" ]; then
    VERSION_FILE="$SETUP_DIR/config/tool-versions.env"
  fi
  load_tool_versions
  export SETUP_PROFILE SETUP_DIR
  export LAZYVIM_REPO="${LAZYVIM_REPO:-https://github.com/LazyVim/starter}"
  export PRIME_AGENT_CODING_AGENT_DIR="${PRIME_AGENT_CODING_AGENT_DIR:-$TARGET_HOME/.prime/agent}"
  export PI_CODING_AGENT_DIR="${PI_CODING_AGENT_DIR:-$TARGET_HOME/.omp/agent}"
  export CODEX_HOME="${CODEX_HOME:-$TARGET_HOME/.codex}"
  if [ "$(id -u)" -ne 0 ]; then
    sudo -n true || { die 'passwordless sudo is required; run setup as root with --user NAME'; return 1; }
    { declare -f; printf '\nsetup_system\n'; } | sudo -n --preserve-env=TARGET_USER,TARGET_UID,TARGET_GID,TARGET_HOME,SETUP_DIR,SETUP_PROFILE,ZELLIJ_VERSION,NODE_VERSION,NVIM_VERSION,PRIME_AGENT_VERSION,CODEGRAPH_VERSION,CODEX_VERSION,OMP_VERSION,LAZYVIM_REPO,PRIME_AGENT_CODING_AGENT_DIR,PI_CODING_AGENT_DIR,CODEX_HOME,AZURE_OPENAI_BASE_URL,AZURE_OPENAI_RESOURCE_NAME,AZURE_OPENAI_API_VERSION,AZURE_OPENAI_DEPLOYMENT_NAME_MAP bash -s
  else
    setup_system
  fi
}

if [ -z "${BASH_SOURCE[0]:-}" ] || [ "${BASH_SOURCE[0]}" = "$0" ]; then
  main "$@"
fi
