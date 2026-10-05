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
    [PRIME_AGENT_VERSION]=0.9.5 [CODEGRAPH_VERSION]=1.6.0 [CLAUDE_CODE_VERSION]=next
    [TYPESCRIPT_LANGUAGE_SERVER_VERSION]=6.0.0 [TYPESCRIPT_VERSION]=6.0.3
    [PYRIGHT_VERSION]=1.1.413 [INTELEPHENSE_VERSION]=1.18.5
    [VSCODE_LANGSERVERS_VERSION]=4.10.0 [BASH_LANGUAGE_SERVER_VERSION]=5.6.0
    [YAML_LANGUAGE_SERVER_VERSION]=1.24.0 [JDTLS_VERSION]=1.60.0 [JDTLS_JAVA_VERSION]=21.0.10 )
  # npm dist-tags are resolved to a version at install time.
  local -A dist_tag_allowed=( [CLAUDE_CODE_VERSION]=1 )
  local semver='^[0-9]+\.[0-9]+\.[0-9]+(-[A-Za-z0-9.]+)?$' dist_tag='^[a-z][a-z0-9-]*$'
  local -A seen=()
  local line name value
  if [ -n "${VERSION_FILE:-}" ]; then
    [ -r "$VERSION_FILE" ] || { die "cannot read version manifest: $VERSION_FILE"; return 1; }
    while IFS= read -r line || [ -n "$line" ]; do
      [[ "$line" =~ ^[[:space:]]*(#|$) ]] && continue
      [[ "$line" =~ ^([A-Z_]+)=([0-9]+\.[0-9]+\.[0-9]+(-[A-Za-z0-9.]+)?|[a-z][a-z0-9-]*)$ ]] || {
        die 'invalid version manifest assignment'; return 1;
      }
      name="${BASH_REMATCH[1]}"; value="${BASH_REMATCH[2]}"
      [[ -v versions[$name] && ! -v seen[$name] ]] || { die "unknown or duplicate version: $name"; return 1; }
      versions[$name]="$value"; seen[$name]=1
    done < "$VERSION_FILE"
  fi
  for name in "${!versions[@]}"; do
    value="${!name:-${versions[$name]}}"
    [[ "$value" =~ $semver ]] || { [[ -v dist_tag_allowed[$name] && "$value" =~ $dist_tag ]]; } || {
      die "invalid $name"; return 1;
    }
    printf -v "$name" '%s' "$value"
    export "$name"
  done
  [[ "$NODE_VERSION" == 24.* ]] || { die 'NODE_VERSION must select Node 24'; return 1; }
  [[ "$TYPESCRIPT_VERSION" == 6.* ]] || { die 'TYPESCRIPT_VERSION must select TypeScript 6 (TLS requires tsserver.js)'; return 1; }
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
  shift 3
  destination="/opt/overlord/$name-$version"
  if [ ! -x "$destination/bin/$name" ]; then
    stage="$(mktemp -d /opt/overlord/.npm.XXXXXXXX)"
    trap 'rm -rf "$stage"' EXIT
    npm install --global --prefix "$stage" --no-audit --no-fund "$@" "$package@$version"
    verify_version "$stage/bin/$name" "$version"
    chmod -R a+rX "$stage"
    [ ! -e "$destination" ] || { die "incomplete installation exists: $destination"; exit 1; }
    mv "$stage" "$destination"
  fi
  verify_version "$destination/bin/$name" "$version"
  publish_binary "$destination/bin/$name" "$name"
)

# Stdio servers do not universally implement --version. Check installed metadata
# and executables without starting servers or touching user configuration.
verify_npm_language_server() {
  /usr/bin/python3 - "$@" <<'PY_LSP_PACKAGE'
import json
import os
import sys
from pathlib import Path

prefix, package, version, *commands = sys.argv[1:]
root = Path(prefix) / "lib/node_modules" / package
try:
    metadata = json.loads((root / "package.json").read_text())
    if metadata["name"] != package or metadata["version"] != version:
        raise ValueError("package version mismatch")
    for command in commands:
        executable = Path(prefix) / "bin" / command
        if not executable.resolve().is_relative_to(root.resolve()) or not os.access(executable, os.X_OK):
            raise ValueError("missing package executable")
except (OSError, ValueError, KeyError):
    sys.exit(f"invalid language server installation: {package}@{version}")
PY_LSP_PACKAGE
}

install_npm_language_server() (
  set -euo pipefail
  local package="$1" version="$2"; shift 2
  local destination="/opt/overlord/$package-$version" stage command
  local packages=("$package@$version")
  if [ "$package" = typescript-language-server ]; then
    destination+="-typescript-$TYPESCRIPT_VERSION"
    packages+=("typescript@$TYPESCRIPT_VERSION")
  fi
  if [ ! -d "$destination" ]; then
    [ ! -e "$destination" ] && [ ! -L "$destination" ] || { die "incomplete installation exists: $destination"; exit 1; }
    stage="$(mktemp -d /opt/overlord/.lsp-npm.XXXXXXXX)"
    trap 'rm -rf "$stage"' EXIT
    npm install --global --prefix "$stage" --engine-strict --no-audit --no-fund "${packages[@]}"
    verify_npm_language_server "$stage" "$package" "$version" "$@"
    if [ "$package" = typescript-language-server ]; then
      verify_npm_language_server "$stage" typescript "$TYPESCRIPT_VERSION" tsserver tsc
      [ -f "$stage/lib/node_modules/typescript/lib/tsserver.js" ]
    fi
    chmod -R a+rX "$stage"
    mv "$stage" "$destination"
  fi
  verify_npm_language_server "$destination" "$package" "$version" "$@"
  if [ "$package" = typescript-language-server ]; then
    verify_npm_language_server "$destination" typescript "$TYPESCRIPT_VERSION" tsserver tsc
    [ -f "$destination/lib/node_modules/typescript/lib/tsserver.js" ]
    # Publish the language-server entry point, not a global TypeScript compiler.
  fi
  for command in "$@"; do publish_binary "$destination/bin/$command" "$command"; done
)

emit_jdtls_launcher() {
  cat <<'JDTLS_LAUNCHER'
#!/usr/bin/env bash
set -euo pipefail
distribution="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
JDTLS_HOME="${JDTLS_HOME:-$distribution/server}"
JAVA_HOME="${JAVA_HOME:-$distribution/java}"
project="$(pwd -P)"
key="$(printf '%s\0%s' "$project" "$(readlink -f "$JDTLS_HOME")" | sha256sum)"
cache="${XDG_CACHE_HOME:-$HOME/.cache}/jdtls/${key%% *}"
data="${JDTLS_DATA_DIR:-$cache/workspace}"
configuration="${JDTLS_CONFIG_DIR:-$cache/config}"
# Command-line overrides take precedence, just like the upstream launcher.
args=()
while [ "$#" -gt 0 ]; do
  case "$1" in
    -data|-configuration)
      [ "$#" -ge 2 ] || { printf 'jdtls: %s requires a directory\n' "$1" >&2; exit 2; }
      if [ "$1" = -data ]; then data="$2"; else configuration="$2"; fi
      shift 2 ;;
    *) args+=("$1"); shift ;;
  esac
done
umask 077
mkdir -p "$data" "$configuration"
case "$(uname -m)" in
  x86_64|amd64) platform=config_linux ;;
  aarch64|arm64) platform=config_linux_arm ;;
  *) printf 'jdtls: unsupported architecture\n' >&2; exit 1 ;;
esac
# Serialize first-use config copying, not the server lifetime. OSGi must not
# write into the root-owned distribution or share config between projects.
(
  flock 9
  if [ ! -f "$configuration/config.ini" ]; then
    cp -R "$JDTLS_HOME/$platform/." "$configuration/"
    chmod -R u+rwX "$configuration"
  fi
) 9>"$configuration/.overlord-init.lock"
shopt -s nullglob
launchers=("$JDTLS_HOME"/plugins/org.eclipse.equinox.launcher_*.jar)
[ "${#launchers[@]}" -eq 1 ] || { printf 'jdtls: expected one Eclipse launcher\n' >&2; exit 1; }
java_args=()
if [ -n "${LOMBOK_JAR:-}" ]; then
  java_args+=("-javaagent:$LOMBOK_JAR")
elif [ -f /opt/lombok.jar ]; then
  # Octopus provisions Lombok separately from the shared language server.
  java_args+=("-javaagent:/opt/lombok.jar")
fi
exec "$JAVA_HOME/bin/java" "${java_args[@]}" \
  -Declipse.application=org.eclipse.jdt.ls.core.id1 \
  -Dosgi.bundles.defaultStartLevel=4 \
  -Declipse.product=org.eclipse.jdt.ls.core.product \
  -Dlog.level=ALL -Xmx1G --add-modules=ALL-SYSTEM \
  --add-opens java.base/java.util=ALL-UNNAMED \
  --add-opens java.base/java.lang=ALL-UNNAMED \
  -jar "${launchers[0]}" -configuration "$configuration" -data "$data" "${args[@]}"
JDTLS_LAUNCHER
}

install_jdtls() (
  set -euo pipefail
  local destination="/opt/overlord/jdtls-$JDTLS_VERSION-java-$JDTLS_JAVA_VERSION" stage arch java_sha
  # Exact milestone/build pairs and upstream SHA256s; no moving latest URLs.
  [ "$JDTLS_VERSION" = 1.60.0 ] && [ "$JDTLS_JAVA_VERSION" = 21.0.10 ] || {
    die 'unsupported JDTLS/Java pin; add the upstream archive and checksum pair first'; exit 1;
  }
  case "$(uname -m)" in
    x86_64|amd64) arch=x64; java_sha=ea3b9bd464d6dd253e9a7accf59f7ccd2a36e4aa69640b7251e3370caef896a4 ;;
    aarch64|arm64) arch=aarch64; java_sha=357fee29fb0d5c079f6730db98b28942df13a6eed426f6c61cd4ad703ab27b9a ;;
    *) die 'unsupported CPU architecture'; exit 1 ;;
  esac
  if [ ! -d "$destination" ]; then
    [ ! -e "$destination" ] && [ ! -L "$destination" ] || { die "incomplete installation exists: $destination"; exit 1; }
    stage="$(mktemp -d /opt/overlord/.jdtls.XXXXXXXX)"
    trap 'rm -rf "$stage"' EXIT
    download 'https://download.eclipse.org/jdtls/milestones/1.60.0/jdt-language-server-1.60.0-202606262232.tar.gz' "$stage/jdtls.tar.gz"
    printf '%s  %s\n' e94c303d8198f977930803582738771fd18c52c5492878410bf222b1aa81ef1d "$stage/jdtls.tar.gz" | sha256sum --check --status
    download "https://github.com/adoptium/temurin21-binaries/releases/download/jdk-21.0.10%2B7/OpenJDK21U-jdk_${arch}_linux_hotspot_21.0.10_7.tar.gz" "$stage/java.tar.gz"
    printf '%s  %s\n' "$java_sha" "$stage/java.tar.gz" | sha256sum --check --status
    mkdir -p "$stage/runtime/server" "$stage/runtime/java"
    # Keep root ownership; upstream IDs may be unmapped in rootless builds.
    tar --no-same-owner -xzf "$stage/jdtls.tar.gz" -C "$stage/runtime/server"
    tar --no-same-owner -xzf "$stage/java.tar.gz" -C "$stage/runtime/java" --strip-components=1
    emit_jdtls_launcher > "$stage/runtime/jdtls"
    chmod -R a+rX "$stage/runtime"
    chmod 0755 "$stage/runtime/jdtls"
    "$stage/runtime/java/bin/java" -version
    [ -f "$stage/runtime/server/config_linux/config.ini" ]
    mv "$stage/runtime" "$destination"
  fi
  [ -x "$destination/jdtls" ] && [ -x "$destination/java/bin/java" ] && [ -f "$destination/server/config_linux/config.ini" ] || {
    die "incomplete installation exists: $destination"; exit 1;
  }
  # Refresh installer-owned launcher logic without re-downloading the runtime.
  local launcher
  launcher="$(mktemp "$destination/.launcher.XXXXXXXX")"
  emit_jdtls_launcher > "$launcher"
  chmod 0755 "$launcher"
  if cmp -s "$launcher" "$destination/jdtls"; then
    rm "$launcher"
  else
    mv -Tf "$launcher" "$destination/jdtls"
  fi
  publish_binary "$destination/jdtls" jdtls
)

install_marksman() (
  set -euo pipefail
  local release=2026-02-08 arch checksum stage
  local destination="/opt/overlord/marksman-$release"
  case "$(uname -m)" in
    x86_64|amd64) arch=x64; checksum=be5098e8213219269c47fc0d916a66fa31ce0602ec967475c722260aabf26087 ;;
    aarch64|arm64) arch=arm64; checksum=db8e124527f7f8048e3e6c91821b9c52ef173d92c01e47d221bf1337afd962fb ;;
    *) die 'unsupported CPU architecture'; exit 1 ;;
  esac
  if [ ! -d "$destination" ]; then
    [ ! -e "$destination" ] && [ ! -L "$destination" ] || { die "incomplete installation exists: $destination"; exit 1; }
    stage="$(mktemp -d /opt/overlord/.marksman.XXXXXXXX)"
    trap 'rm -rf "$stage"' EXIT
    download "https://github.com/artempyanykh/marksman/releases/download/$release/marksman-linux-$arch" "$stage/marksman"
    printf '%s  %s\n' "$checksum" "$stage/marksman" | sha256sum --check --status
    chmod 0755 "$stage" "$stage/marksman"
    "$stage/marksman" --version
    mv "$stage" "$destination"
  fi
  printf '%s  %s\n' "$checksum" "$destination/marksman" | sha256sum --check --status
  publish_binary "$destination/marksman" marksman
)

install_language_servers() {
  [ "$(id -u)" -eq 0 ] || { die 'language server installation requires root'; return 1; }
  mkdir -p /opt/overlord /usr/local/bin
  install_npm_language_server typescript-language-server "$TYPESCRIPT_LANGUAGE_SERVER_VERSION" typescript-language-server
  install_npm_language_server pyright "$PYRIGHT_VERSION" pyright pyright-langserver
  install_npm_language_server intelephense "$INTELEPHENSE_VERSION" intelephense
  install_npm_language_server vscode-langservers-extracted "$VSCODE_LANGSERVERS_VERSION" \
    vscode-html-language-server vscode-css-language-server vscode-json-language-server vscode-eslint-language-server vscode-markdown-language-server
  install_npm_language_server bash-language-server "$BASH_LANGUAGE_SERVER_VERSION" bash-language-server
  install_npm_language_server yaml-language-server "$YAML_LANGUAGE_SERVER_VERSION" yaml-language-server
  install_jdtls
  install_marksman
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
    ripgrep
    fd-find
    fzf
    unzip
    locales
    jq
    xdg-utils
    xz-utils
    util-linux
    passwd
    # Pulls the distro's ICU runtime required by Marksman's bundled .NET.
    libicu-dev
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
    tar --no-same-owner -xzf "$stage/archive.tar.gz" -C "$stage"
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
    tar --no-same-owner -xzf "$stage/$archive" -C "$stage/runtime" --strip-components=1
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
    tar --no-same-owner -xJf "$stage/$archive" -C "$stage/runtime" --strip-components=1
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
# Claude Code is a root-owned install; re-running setup updates it.
export DISABLE_AUTOUPDATER=1
PATH_BLOCK
  done
}


verify_login_shell_tools() {
  [ "$(id -u)" -eq "$TARGET_UID" ] || { die 'login verification requires the target UID'; return 1; }
  local command
  for command in node npm npx nvim prime-agent git claude; do
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


# --- shared CodeGraph skill for coding agents ---
ensure_codegraph_skill() {
  local source destination
  for source in "${SETUP_DIR:-}/skills/codegraph" /usr/local/share/overlord/skills/codegraph; do
    if [ -r "$source/SKILL.md" ]; then
      for destination in "$PRIME_AGENT_CODING_AGENT_DIR/skills/codegraph"; do
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

# --- Prime Agent installation ---
install_prime_agent() (
  set -euo pipefail
  local destination="/opt/overlord/prime-agent-$PRIME_AGENT_VERSION" stage
  if [ ! -x "$destination/bin/prime-agent" ]; then
    [ ! -e "$destination" ] && [ ! -L "$destination" ] || { die "incomplete installation exists: $destination"; exit 1; }
    stage="$(mktemp -d /opt/overlord/.prime.XXXXXXXX)"
    trap 'rm -rf "$stage"' EXIT
    download https://app.primeintellect.ai/prime-agent/install.sh "$stage/install.sh"
    mkdir "$stage/home"
    # Native releases ignore npm's prefix and require an empty managed root.
    # Both formats must keep their assets and relative bin links when moved.
    # Overlord publishes the command; the upstream public link is absolute.
    # Ignore archive owner IDs, which may be unmapped in rootless builds.
    # setsid also prevents older/Node installers from prompting via /dev/tty.
    HOME="$stage/home" TAR_OPTIONS=--no-same-owner npm_config_prefix="$stage/runtime" \
      PRIME_AGENT_INSTALL_DIR="$stage/runtime" PRIME_AGENT_INSTALL_LINK=0 \
      PRIME_AGENT_INSTALLER_PLAIN=1 PRIME_AGENT_INSTALLER_NONINTERACTIVE=1 \
      PRIME_AGENT_BOOTSTRAP_KERNEL_ON_INSTALL=0 setsid --wait sh "$stage/install.sh" "$PRIME_AGENT_VERSION" </dev/null
    verify_version "$stage/runtime/bin/prime-agent" "$PRIME_AGENT_VERSION"
    chmod -R a+rX "$stage/runtime"
    mv "$stage/runtime" "$destination"
  fi
  verify_version "$destination/bin/prime-agent" "$PRIME_AGENT_VERSION"
  publish_binary "$destination/bin/prime-agent" prime-agent
)


# CLAUDE_CODE_VERSION may name an npm dist-tag (default: next). Resolve it to the
# version it points at now, so each release gets its own verified distribution
# and a re-run picks up the newer release.
install_claude_code() {
  local package=@anthropic-ai/claude-code version="$CLAUDE_CODE_VERSION" npm_flags=()
  if ! [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[A-Za-z0-9.]+)?$ ]]; then
    version="$(npm view "$package@$CLAUDE_CODE_VERSION" version)" || {
      die "cannot resolve $package@$CLAUDE_CODE_VERSION"; return 1;
    }
    [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[A-Za-z0-9.]+)?$ ]] || {
      die "unexpected $package@$CLAUDE_CODE_VERSION version: $version"; return 1;
    }
  fi
  # Safe Chain blocks packages younger than its minimum age, which a fresh
  # @next release is. Plain npm would only warn about the unknown flag.
  if command -v safe-chain >/dev/null 2>&1; then
    npm_flags+=(--safe-chain-skip-minimum-package-age)
  fi
  install_npm_tool claude "$package" "$version" "${npm_flags[@]}"
}

# Codex CLI is no longer installed. Remove the distributions earlier runs
# published; the account's ~/.codex configuration and sessions stay.
remove_codex() {
  if [ -L /usr/local/bin/codex ] && [[ "$(readlink /usr/local/bin/codex)" == /opt/overlord/codex-* ]]; then
    rm -f /usr/local/bin/codex
  fi
  rm -rf /opt/overlord/codex-*
}

# Shared Python I/O keeps managed formats atomic and preserves permissions.
python_config() {
  { config_python_helpers; cat; } | /usr/bin/python3 - "$@"
}

config_python_helpers() {
  cat <<'PY_HELPERS'
import json
import os
import stat
import sys
import tempfile
from pathlib import Path

def parse_jsonc(text):
    """Parse JSON with comments and trailing commas, as Prime Agent writes it."""
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

install_skills_from_source() {
  local source="$1"
  shift
  if npx --yes skills add "$source" "$@" --global --agent pi --yes --copy --full-depth \
    2>&1 | sed "s|^|[skills:$source] |"; then
    info "installed skills from $source"
  else
    warn "failed to install skills from $source"
  fi
}

install_prime_agent_skills() {
  if ! command -v npx >/dev/null 2>&1; then
    warn "npx unavailable; skipping Prime Agent skill installation"
    return 0
  fi
  info "installing shared skills for Prime Agent..."
  # Curated set: the upstream collections are large and every installed skill
  # costs prompt surface, so install only the skills this setup uses.
  # grill-me and grill-with-docs delegate to grilling and domain-modeling.
  install_skills_from_source mattpocock/skills \
    --skill setup-matt-pocock-skills \
    --skill grill-me \
    --skill grill-with-docs \
    --skill grilling \
    --skill domain-modeling
  install_skills_from_source cursor/plugins \
    --skill thermos \
    --skill thermo-nuclear-review \
    --skill thermo-nuclear-code-quality-review

  # The skills CLI targets Pi; copy assets into the selected harness directories.
  local pi_skills="$HOME/.pi/agent/skills"
  local agent_skills
  if [ -d "$pi_skills" ]; then
      for agent_skills in "$PRIME_AGENT_CODING_AGENT_DIR/skills"; do
        mkdir -p "$agent_skills"
        cp -a "$pi_skills/." "$agent_skills/"
        info "synced Pi skills to $agent_skills"
      done
  else
    warn "Pi skills directory was not created: $pi_skills"
  fi
}

# --- optional Prime Agent integration keys ---

# Read one secret from the controlling terminal, hidden. Prints the value, or
# nothing when no terminal is attached.
read_agent_secret() {
  local prompt="$1" value="" tty_fd
  # A controlling terminal is not guaranteed even when /dev/tty exists.
  if ! exec {tty_fd}<>/dev/tty 2>/dev/null; then
    return 1
  fi
  printf '%s' "$prompt" >&"$tty_fd" || true
  IFS= read -r -s value <&"$tty_fd" || value=""
  printf '\n' >&"$tty_fd" || true
  exec {tty_fd}>&-
  printf '%s' "$value"
}

# Report which optional integration keys are already stored in the agent dir.
prime_agent_key_status() {
  python_config "$1" "$2" <<'PY'
import sys
from pathlib import Path

def load(path):
    original = read_text(path)
    if original is None or not original.strip():
        return {}
    try:
        return parse_jsonc(original)
    except Exception:
        return {}

auth = load(Path(sys.argv[1]))
settings = load(Path(sys.argv[2]))
credential = auth.get("serper")
serper = isinstance(credential, dict) and credential.get("type") == "api_key" and bool(str(credential.get("key") or "").strip())
servers = settings.get("mcpServers")
context7 = servers.get("context7") if isinstance(servers, dict) and isinstance(servers.get("context7"), dict) else {}
headers = context7.get("headers") if isinstance(context7.get("headers"), dict) else {}
context7_stored = bool(str(headers.get("CONTEXT7_API_KEY") or "").strip())
print(f"context7={'stored' if context7_stored else 'missing'}")
print(f"serper={'stored' if serper else 'missing'}")
PY
}

# Prompt for the optional Context7 and Serper keys and store them for Prime
# Agent: the Serper credential it reads for the bundled websearch skill, and the
# header it sends to the Context7 MCP server. CONTEXT7_API_KEY and SERPER_API_KEY
# win over a prompt, so headless setups (container initialization, CI) never
# block. Prompts read the controlling terminal and a blank answer keeps the
# stored key.
configure_prime_agent_api_keys() {
  local agent_dir="${PRIME_AGENT_CODING_AGENT_DIR:-$TARGET_HOME/.prime/agent}"
  local auth_path="$agent_dir/auth.json"
  local settings_path="$agent_dir/settings.json"
  local context7_key="${CONTEXT7_API_KEY:-}"
  local serper_key="${SERPER_API_KEY:-}"
  local stored_context7=missing stored_serper=missing
  local stored label

  stored="$(prime_agent_key_status "$auth_path" "$settings_path")"
  case "$stored" in *context7=stored*) stored_context7=stored ;; esac
  case "$stored" in *serper=stored*) stored_serper=stored ;; esac

  # Prompt only when setup talks to a terminal. Container initialization runs
  # without one and must not block.
  if [ -r /dev/tty ] && { [ -t 1 ] || [ -t 2 ]; }; then
    if [ -z "$context7_key" ]; then
      label='[blank to skip]'
      if [ "$stored_context7" = stored ]; then label='[Enter keeps the stored key]'; fi
      context7_key="$(read_agent_secret "Context7 API key (https://context7.com/dashboard) $label: " || true)"
    fi
    if [ -z "$serper_key" ]; then
      label='[blank to skip]'
      if [ "$stored_serper" = stored ]; then label='[Enter keeps the stored key]'; fi
      serper_key="$(read_agent_secret "Serper API key (https://serper.dev) $label: " || true)"
    fi
  fi

  context7_key="${context7_key//$'\n'/}"
  serper_key="${serper_key//$'\n'/}"
  case "$context7_key" in
    ""|ctx7sk*) ;;
    *) warn "Context7 API keys normally start with 'ctx7sk'" ;;
  esac

  if [ -z "$context7_key" ] && [ -z "$serper_key" ]; then
    local missing_keys=''
    if [ "$stored_context7" = missing ]; then missing_keys='Context7'; fi
    if [ "$stored_serper" = missing ]; then
      if [ -n "$missing_keys" ]; then
        missing_keys="$missing_keys and Serper"
      else
        missing_keys='Serper'
      fi
    fi
    if [ -n "$missing_keys" ]; then
      warn "no $missing_keys API key: export CONTEXT7_API_KEY and SERPER_API_KEY and rerun setup.sh, or run setup.sh from a terminal to be prompted"
    else
      info "Prime Agent API keys already stored"
    fi
    return 0
  fi

  local secrets_file status=0
  secrets_file="$(mktemp)"
  chmod 600 "$secrets_file"
  printf 'context7=%s\nserper=%s\n' "$context7_key" "$serper_key" > "$secrets_file"
  # Keep the heredoc on a plain command: `declare -f` re-renders an `if` that
  # routes a heredoc into an unparsable function body.
  python_config "$auth_path" "$settings_path" "$secrets_file" <<'PY' || status=1
import json
import sys
from pathlib import Path

auth_path, settings_path, secrets_path = (Path(argument) for argument in sys.argv[1:4])
values = {}
for line in secrets_path.read_text(encoding="utf-8").splitlines():
    name, separator, value = line.partition("=")
    if separator:
        values[name] = value.strip()

def load(path):
    original = read_text(path)
    if original is None or not original.strip():
        return {}, original
    return parse_jsonc(original), original

try:
    if values.get("serper"):
        auth, original = load(auth_path)
        auth["serper"] = {"type": "api_key", "key": values["serper"]}
        write_file(auth_path, original, json.dumps(auth, indent=2, sort_keys=True) + "\n")
    if values.get("context7"):
        settings, original = load(settings_path)
        server = mapping(mapping(settings, "mcpServers"), "context7")
        server.setdefault("type", "http")
        server.setdefault("url", "https://mcp.context7.com/mcp")
        server.setdefault("enabled", True)
        mapping(server, "headers")["CONTEXT7_API_KEY"] = values["context7"]
        write_file(settings_path, original, json.dumps(settings, indent=2, sort_keys=True) + "\n")
except Exception as error:
    print(f"could not store Prime Agent API keys: {type(error).__name__}", file=sys.stderr)
    raise SystemExit(1)
PY
  if [ "$status" -eq 0 ]; then
    info "stored Prime Agent API keys in $agent_dir"
  else
    warn "failed to store Prime Agent API keys in $agent_dir"
  fi
  rm -f "$secrets_file"
}

configure_prime_agent_tools() {
  info "enabling Prime Agent web search and Context7 tools..."
  local settings_paths=("${PRIME_AGENT_CODING_AGENT_DIR:-$TARGET_HOME/.prime/agent}/settings.json")
  python_config "${settings_paths[@]}" <<'PYEOF'
import json
import os
from pathlib import Path
import sys

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
        # Rebuild the entry instead of replacing it: setup stores the Context7
        # API key header here and users may add their own fields.
        context7 = servers.get("context7")
        context7 = dict(context7) if isinstance(context7, dict) else {}
        context7.update({
            "type": "http",
            "url": "https://mcp.context7.com/mcp",
            "enabled": True,
        })
        servers["context7"] = context7
        if os.environ.get("SETUP_PROFILE", "native") == "container":
            servers["runpod-docs"] = {"type": "http", "url": "https://docs.runpod.io/mcp", "enabled": True}
        else:
            servers.pop("runpod-docs", None)

        # Selections that point at a model setup no longer manages fall back to
        # DeepSeek Flash, the default opencode-go model. Any other choice stays,
        # including the other managed models and the user's own models.
        default_provider, default_model = "opencode-go", "deepseek-flash"
        # Keep in sync with `retired` in configure_prime_agent_models.
        retired_models = {
            "azure-openai-responses": ("gpt-5.6-sol", "gpt-5.6-luna", "grok-4.6", "gpt-6-astra"),
            "opencode-go": ("gpt-5.6-luna", "union-alpha", "deepseek-v4.1-flash"),
        }

        def is_retired(model, provider=None):
            if not isinstance(model, str):
                return False
            prefix, separator, model_id = model.rpartition("/")
            return model_id in retired_models.get(prefix if separator else provider, ())

        recent = settings.get("recentModels")
        if isinstance(recent, list):
            settings["recentModels"] = recent = [model for model in recent if not is_retired(model)]
        selected = settings.get("defaultModel")
        if not (isinstance(selected, str) and selected.strip()) or is_retired(selected, settings.get("defaultProvider")):
            settings["defaultModel"] = default_model
            settings["defaultProvider"] = default_provider
            fallback = f"{default_provider}/{default_model}"
            if isinstance(recent, list) and fallback not in recent:
                settings["recentModels"] = [fallback] + recent

        if before != json.dumps(settings, sort_keys=True):
            write_file(path, original, json.dumps(settings, indent=2, sort_keys=True) + "\n")
    except Exception as error:
        print(f"could not update {path}: {type(error).__name__}", file=sys.stderr)
        continue
PYEOF

  # Add the Context7 routing skill to the Prime native root.
  local agent_dirs=("$PRIME_AGENT_CODING_AGENT_DIR")
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
  info "websearch enabled (Serper key: setup prompt, SERPER_API_KEY, or prime-agent /login -> MCP Connections -> Serper)"
  info "Context7 MCP server configured (key: setup prompt or CONTEXT7_API_KEY)"
}

configure_prime_agent_models() {
  python_config "${PRIME_AGENT_CODING_AGENT_DIR:-$TARGET_HOME/.prime/agent}/models.json" <<'PYEOF_PRIME'
import copy
import json

# Prime auto-compacts when contextTokens > contextWindow - reserveTokens, and
# reserveTokens defaults to 16384 (compaction.ts). Every managed model must
# compact at 150k, so the window is that threshold plus the reserve; a model's
# true provider window is deliberately not used.
AUTOCOMPACT_TOKENS = 150000
RESERVE_TOKENS = 16384
WINDOW = AUTOCOMPACT_TOKENS + RESERVE_TOKENS
# Prime 0.9.5 sends min(maxTokens, 32000) as every request's output cap, and a
# custom model without maxTokens defaults to 16384. Output cannot be uncapped
# from models.json (maxTokens must be positive), so managed models carry
# Prime's own ceiling and no lower limit of their own.
MAX_TOKENS = 32000

desired = {
    "google-vertex": [("gemini-3.8-flash", "Gemini 3.8 Flash")],
    "opencode-go": [
        ("deepseek-flash", "DeepSeek Flash"),
        ("muse-spark-1.3-contributor", "Muse Spark 1.3 Contributor"),
        ("mimo-v2.6-flash", "MiMo V2.6 Flash"),
        ("mimo-v2.6-pro", "MiMo V2.6 Pro"),
        ("space-bunny-free", "Space Bunny Free"),
    ],
}
# Previously managed entries. Re-runs remove them from existing state; models a
# user added to the same providers are kept. Prime's own built-in catalog is
# not affected by models.json and still lists its models for configured keys.
retired = {
    "azure-openai-responses": ("gpt-5.6-sol", "gpt-5.6-luna", "grok-4.6", "gpt-6-astra"),
    "opencode-go": ("gpt-5.6-luna", "union-alpha", "deepseek-v4.1-flash"),
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
        defaults.update(contextWindow=WINDOW, maxInputTokens=WINDOW, limitTokens=WINDOW, reasoning=True)
        providers = mapping(data, "providers")
        for provider_id, model_ids in retired.items():
            provider = providers.get(provider_id)
            if not isinstance(provider, dict):
                continue
            entries = provider.get("models")
            if isinstance(entries, list):
                provider["models"] = [
                    entry for entry in entries
                    if not (isinstance(entry, dict) and entry.get("id") in model_ids)
                ]
            overrides = provider.get("modelOverrides")
            if isinstance(overrides, dict):
                for model_id in model_ids:
                    overrides.pop(model_id, None)
                # Older setups wrote a 256k "*" override. Prime ignores unknown IDs,
                # so it never applied, but it misstates the managed window.
                overrides.pop("*", None)
            if provider_id not in desired:
                # Prime rejects a provider with no models, overrides, or endpoint
                # settings, which would fail the whole file; drop what is left empty.
                for key in ("models", "modelOverrides"):
                    if provider.get(key) in ([], {}):
                        del provider[key]
                if not provider:
                    del providers[provider_id]
        for provider_id, models in desired.items():
            provider = mapping(providers, provider_id)
            entries = provider.setdefault("models", [])
            if not isinstance(entries, list) or any(not isinstance(entry, dict) for entry in entries):
                raise ValueError("models must be a list of mappings")
            overrides = mapping(provider, "modelOverrides")
            overrides.pop("*", None)
            for model_id, name in models:
                fields = dict(contextWindow=WINDOW, maxInputTokens=WINDOW, limitTokens=WINDOW,
                              maxTokens=MAX_TOKENS, reasoning=True)
                matching = [entry for entry in entries if entry.get("id") == model_id]
                if not matching:
                    matching = [{"id": model_id}]
                    entries.extend(matching)
                if model_id in ("mimo-v2.6-flash", "mimo-v2.6-pro"):
                    # The Zen gateway serves both v2.6 MiMo models only through the OpenAI
                    # Chat Completions API; /responses and /messages answer 503 (probed 2026-09-22).
                    fields["api"] = "openai-completions"
                    fields["baseUrl"] = "https://opencode.ai/zen/go/v1"
                    # The gateway rejects minimal/xhigh/max. Leave those selectors unsupported
                    # so Prime clamps a saved level instead of sending a rejected effort.
                    fields["thinkingLevelMap"] = {
                        "off": "none", "minimal": None, "low": "low",
                        "medium": "medium", "high": "high", "xhigh": None, "max": None,
                    }
                if model_id == "space-bunny-free":
                    # The gateway lists Space Bunny only under this ID, on its OpenAI
                    # Chat Completions route. It accepts minimal through max but rejects
                    # "none" (probed 2026-09-23), so off is unsupported: Prime clamps it
                    # to minimal instead of sending its default "none". xhigh and max
                    # must be mapped explicitly or Prime hides them.
                    fields["api"] = "openai-completions"
                    fields["baseUrl"] = "https://opencode.ai/zen/go/v1"
                    fields["thinkingLevelMap"] = {
                        "off": None, "minimal": "minimal", "low": "low",
                        "medium": "medium", "high": "high", "xhigh": "xhigh", "max": "max",
                    }
                if model_id == "muse-spark-1.3-contributor":
                    # The gateway accepts minimal through max and rejects "none". Prime's
                    # compaction requests no effort, and the Responses builder then sends
                    # thinkingLevelMap.off, defaulting to "none" unless off is null. This
                    # entry replaces Prime's built-in one, whose off: null is lost, so the
                    # whole map is written here; xhigh and max must be explicit or Prime hides them.
                    fields["thinkingLevelMap"] = {
                        "off": None, "minimal": "minimal", "low": "low",
                        "medium": "medium", "high": "high", "xhigh": "xhigh", "max": "max",
                    }
                override = mapping(overrides, model_id)
                override.update(copy.deepcopy(fields))
                for entry in matching:
                    # The suffix names where the model auto-compacts, not its provider window.
                    entry.update(fields, name=f"{name} ({AUTOCOMPACT_TOKENS // 1000}k)")
                    # Muse Spark's built-in entry takes images; this entry replaces it.
                    if model_id in ("mimo-v2.6-flash", "mimo-v2.6-pro", "space-bunny-free",
                                    "muse-spark-1.3-contributor"):
                        entry.setdefault("input", ["text", "image"])
                    if model_id == "deepseek-flash":
                        # Gateway accepts none/minimal/low/medium/high/xhigh/max (probed 2026-09-10).
                        for level in ("off", "minimal", "low", "medium", "high", "xhigh", "max"):
                            value = "none" if level == "off" else level
                            mapping(entry, "thinkingLevelMap")[level] = value
                            mapping(override, "thinkingLevelMap")[level] = value
                    if provider_id == "google-vertex":
                        entry.setdefault("input", ["text", "image"])
            if provider_id == "opencode-go":
                # Console Go rejects requests without x-opencode-session (HTTP 400).
                # Prime resolves the value from the environment when set, else runs
                # the command (cached per process): explicit override, per-workspace
                # ID in containers (OVERLORD_WORKSPACE), per-host ID elsewhere.
                mapping(provider, "headers").setdefault(
                    "x-opencode-session",
                    "!echo ${OPENCODE_SESSION_ID:-${OVERLORD_WORKSPACE:-$(hostname)}} | tr -cs 'A-Za-z0-9_.-' '-'",
                )
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
  configure_prime_agent_api_keys
  configure_prime_agent_models
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
    unset PRIME_AGENT_CODING_AGENT_DIR
    install_node
    install_language_servers
    install_zellij
    install_neovim
    install_codegraph
    install_uv
    install_aws_cli
    install_prime_agent
    install_claude_code
    remove_codex
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
  if [ "$(id -u)" -ne 0 ]; then
    sudo -n true || { die 'passwordless sudo is required; run setup as root with --user NAME'; return 1; }
    { declare -f; printf '\nsetup_system\n'; } | sudo -n --preserve-env=TARGET_USER,TARGET_UID,TARGET_GID,TARGET_HOME,SETUP_DIR,SETUP_PROFILE,ZELLIJ_VERSION,NODE_VERSION,NVIM_VERSION,PRIME_AGENT_VERSION,CODEGRAPH_VERSION,CLAUDE_CODE_VERSION,TYPESCRIPT_LANGUAGE_SERVER_VERSION,TYPESCRIPT_VERSION,PYRIGHT_VERSION,INTELEPHENSE_VERSION,VSCODE_LANGSERVERS_VERSION,BASH_LANGUAGE_SERVER_VERSION,YAML_LANGUAGE_SERVER_VERSION,JDTLS_VERSION,JDTLS_JAVA_VERSION,LAZYVIM_REPO,PRIME_AGENT_CODING_AGENT_DIR bash -s
  else
    setup_system
  fi
}

if [ -z "${BASH_SOURCE[0]:-}" ] || [ "${BASH_SOURCE[0]}" = "$0" ]; then
  main "$@"
fi
