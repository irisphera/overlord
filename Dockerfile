# Overlord - Lightweight multi-arch environment for OpenCode + Oh-My-OpenCode
# Base: ubuntu:24.04 (amd64 + arm64)
# No VNC/GUI — pure terminal + dev tools

ARG SAFE_CHAIN_VERSION=1.5.3

FROM --platform=$TARGETPLATFORM oven/bun:latest AS bun-stage

ARG SAFE_CHAIN_VERSION

RUN if ! command -v curl >/dev/null 2>&1; then \
    apt-get update && apt-get install -y curl ca-certificates && rm -rf /var/lib/apt/lists/*; \
  fi \
  && curl -fsSL "https://github.com/AikidoSec/safe-chain/releases/download/${SAFE_CHAIN_VERSION}/install-safe-chain.sh" \
  | sh -s -- --ci --install-dir /usr/local/.safe-chain

ENV PATH="/usr/local/.safe-chain/shims:/usr/local/.safe-chain/bin:$PATH"

FROM ubuntu:24.04

ARG TARGETARCH
ARG SAFE_CHAIN_VERSION

ENV DEBIAN_FRONTEND=noninteractive

# Base packages
RUN apt-get update && apt-get install -y \
  git \
  curl \
  wget \
  jq \
  build-essential \
  ca-certificates \
  gnupg \
  lsb-release \
  sudo \
  gosu \
  zsh \
  unzip \
  ripgrep \
  locales \
  xdg-utils \
  neovim \
  && rm -rf /var/lib/apt/lists/*

# Safe Chain package-manager protection must be available before npm, bun, pip, uv, etc.
RUN curl -fsSL "https://github.com/AikidoSec/safe-chain/releases/download/${SAFE_CHAIN_VERSION}/install-safe-chain.sh" \
  | sh -s -- --ci --install-dir /usr/local/.safe-chain

ENV PATH="/usr/local/.safe-chain/shims:/usr/local/.safe-chain/bin:$PATH"

# Generate locale
RUN locale-gen en_US.UTF-8

# Docker CLI (for DinD via socket mounting)
RUN curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg \
  && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
  | tee /etc/apt/sources.list.d/docker.list > /dev/null \
  && apt-get update && apt-get install -y docker-ce-cli docker-compose-plugin && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg | gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg \
  && echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" \
  | tee /etc/apt/sources.list.d/google-cloud-sdk.list > /dev/null \
  && apt-get update \
  && CLOUDSDK_SKIP_PY_COMPILATION=1 apt-get install -y google-cloud-cli \
  && rm -rf /var/lib/apt/lists/*

# Node.js 22 via NodeSource
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
  && apt-get install -y nodejs && rm -rf /var/lib/apt/lists/*

# Python 3 + pip + venv
RUN apt-get update && apt-get install -y \
  python3 \
  python3-pip \
  python3-venv \
  && rm -rf /var/lib/apt/lists/*

# ast-grep ships an `sg` bin that conflicts with Ubuntu's setgroups tool.
ARG AST_GREP_VERSION=0.43.0
RUN npm install --prefix /opt/ast-grep "@ast-grep/cli@${AST_GREP_VERSION}" \
  && ln -sf /opt/ast-grep/node_modules/.bin/ast-grep /usr/local/bin/ast-grep \
  && ast-grep --version

# Shell tooling via apt (NO tigervnc-tools)
RUN apt-get update && apt-get install -y \
  shellcheck \
  shfmt \
  && rm -rf /var/lib/apt/lists/*

# zellij (architecture-aware)
ARG ZELLIJ_VERSION=0.43.1
RUN ZELLIJ_ARCH=$([ "$TARGETARCH" = "arm64" ] && echo "aarch64" || echo "x86_64") \
  && curl -fsSL "https://github.com/zellij-org/zellij/releases/download/v${ZELLIJ_VERSION}/zellij-${ZELLIJ_ARCH}-unknown-linux-musl.tar.gz" \
  | tar xz -C /usr/local/bin

# bun (multi-arch)
COPY --from=bun-stage /usr/local/bin/bun /usr/local/bin/bunx /usr/local/bin/

# Create non-root user 'overlord' (UID 33333 for volume compatibility)
RUN groupadd -g 33333 overlord \
  && useradd -m -u 33333 -g overlord -s /bin/zsh overlord \
  && echo 'overlord ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/overlord \
  && chmod 440 /etc/sudoers.d/overlord

RUN mkdir -p /usr/local/.safe-chain/certs \
  && chown -R overlord:overlord /usr/local/.safe-chain/certs

# Config directories
RUN mkdir -p /home/overlord/.config/opencode /home/overlord/.config/zellij/layouts /home/overlord/.config/gcloud /home/overlord/.bun /home/overlord/.cache/zellij /home/overlord/.local/share \
  && chown -R overlord:overlord /home/overlord/.config /home/overlord/.bun /home/overlord/.cache /home/overlord/.local

USER overlord

ENV HOME=/home/overlord
ENV USER=overlord
ENV LOGNAME=overlord
ENV XDG_CONFIG_HOME=/home/overlord/.config
ENV XDG_CACHE_HOME=/home/overlord/.cache
ENV XDG_DATA_HOME=/home/overlord/.local/share
ENV XDG_STATE_HOME=/home/overlord/.local/state

# uv - fast Python package manager (installed as overlord so it lands in ~/.local/bin)
RUN curl -LsSf https://astral.sh/uv/install.sh | sh \
  && PATH="/usr/local/.safe-chain/shims:/usr/local/.safe-chain/bin:/home/overlord/.local/bin:$PATH" uv python install

RUN sh -c "$(curl -fsSL https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh)" "" --unattended \
  && git clone https://github.com/zsh-users/zsh-autosuggestions ${ZSH_CUSTOM:-~/.oh-my-zsh/custom}/plugins/zsh-autosuggestions \
  && git clone https://github.com/zsh-users/zsh-syntax-highlighting ${ZSH_CUSTOM:-~/.oh-my-zsh/custom}/plugins/zsh-syntax-highlighting \
  && git clone https://github.com/zsh-users/zsh-completions ${ZSH_CUSTOM:-~/.oh-my-zsh/custom}/plugins/zsh-completions \
  && sed -i 's/plugins=(git)/plugins=(git zsh-autosuggestions zsh-syntax-highlighting zsh-completions docker npm)/' ~/.zshrc

ENV BUN_INSTALL=/home/overlord/.bun
ENV BUN_INSTALL_BIN=/home/overlord/.bun/bin
ENV PATH="/usr/local/.safe-chain/shims:/usr/local/.safe-chain/bin:/home/overlord/.bun/bin:/home/overlord/.local/bin:$PATH"
ENV UV_LINK_MODE=copy
ENV UV_CACHE_DIR=/home/overlord/.cache/uv
ENV LANG=en_US.UTF-8

RUN install_log="$(mktemp)" \
  && skills_source="$(printf '%s\043%s' mattpocock/skills v1.0.1)" \
  && echo "Installing default OpenCode skills (mattpocock/skills)..." \
  && if ! DISABLE_TELEMETRY=1 npx --yes skills@1.5.11 add "${skills_source}" --skill '*' --agent opencode --global --yes --copy >"${install_log}" 2>&1; then cat "${install_log}"; rm -f "${install_log}"; exit 1; fi \
  && rm -f "${install_log}" \
  && test -f /home/overlord/.agents/skills/setup-matt-pocock-skills/SKILL.md \
  && test -f /home/overlord/.agents/skills/tdd/SKILL.md

COPY --chown=overlord:overlord skills/setup-devcontainer/SKILL.md /home/overlord/.agents/skills/setup-devcontainer/SKILL.md
RUN test -s /home/overlord/.agents/skills/setup-devcontainer/SKILL.md

COPY --chown=overlord:overlord config/tool-versions.env /tmp/tool-versions.env

RUN . /tmp/tool-versions.env \
  && install_log="$(mktemp)" \
  && echo "Installing OpenCode CLI package (opencode-ai@${OPENCODE_VERSION})..." \
  && if ! bun add -g "opencode-ai@${OPENCODE_VERSION}" --safe-chain-skip-minimum-package-age >"${install_log}" 2>&1; then cat "${install_log}"; rm -f "${install_log}"; exit 1; fi \
  && rm -f "${install_log}" \
  && opencode_version="$(opencode --version 2>/dev/null || true)" \
  && printf 'OpenCode CLI version: %s\n' "${opencode_version:-unknown}"

RUN . /tmp/tool-versions.env \
  && install_log="$(mktemp)" \
  && helper_package="oh-my-openagent@${OH_MY_OPENAGENT_VERSION}" \
  && helper_cache_dir="/home/overlord/.cache/opencode/packages/${helper_package}" \
  && mkdir -p "${helper_cache_dir}" /home/overlord/.local/bin \
  && cd "${helper_cache_dir}" \
  && bun init -y > /dev/null 2>&1 \
  && echo "Prewarming OpenCode plugin package (${helper_package})..." \
  && if ! bun add "${helper_package}" --safe-chain-skip-minimum-package-age >"${install_log}" 2>&1; then cat "${install_log}"; rm -f "${install_log}"; exit 1; fi \
  && rm -f "${install_log}" \
  && ln -sf "${helper_cache_dir}/node_modules/.bin/oh-my-openagent" /home/overlord/.local/bin/oh-my-openagent \
  && helper_version="$(node -p "require('./node_modules/oh-my-openagent/package.json').version" 2>/dev/null || true)" \
  && printf 'oh-my-openagent version: %s\n' "${helper_version:-unknown}"

RUN . /tmp/tool-versions.env \
  && install_log="$(mktemp)" \
  && echo "Installing CodeGraph CLI package (@colbymchenry/codegraph@${CODEGRAPH_VERSION})..." \
  && if ! bun add -g "@colbymchenry/codegraph@${CODEGRAPH_VERSION}" --safe-chain-skip-minimum-package-age >"${install_log}" 2>&1; then cat "${install_log}"; rm -f "${install_log}"; exit 1; fi \
  && rm -f "${install_log}" \
  && ln -sf /home/overlord/.bun/bin/codegraph /home/overlord/.local/bin/codegraph \
  && codegraph_version="$(node -p "require('/home/overlord/.bun/install/global/node_modules/@colbymchenry/codegraph/package.json').version" 2>/dev/null || true)" \
  && printf 'CodeGraph CLI version: %s\n' "${codegraph_version:-unknown}"

RUN . /tmp/tool-versions.env \
  && case "${TARGETARCH}" in \
    amd64) rtk_asset="rtk-x86_64-unknown-linux-musl.tar.gz"; rtk_sha256="${RTK_AMD64_SHA256}" ;; \
    arm64) rtk_asset="rtk-aarch64-unknown-linux-gnu.tar.gz"; rtk_sha256="${RTK_ARM64_SHA256}" ;; \
    *) echo "Unsupported RTK architecture: ${TARGETARCH}" >&2; exit 1 ;; \
  esac \
  && rtk_dir="$(mktemp -d)" \
  && curl -fsSL "https://github.com/rtk-ai/rtk/releases/download/v${RTK_VERSION}/${rtk_asset}" -o "${rtk_dir}/${rtk_asset}" \
  && printf '%s  %s\n' "${rtk_sha256}" "${rtk_dir}/${rtk_asset}" | sha256sum -c - \
  && tar -xzf "${rtk_dir}/${rtk_asset}" -C "${rtk_dir}" \
  && install -m 0755 "${rtk_dir}/rtk" /home/overlord/.local/bin/rtk \
  && rm -rf "${rtk_dir}" \
  && test "$(rtk --version)" = "rtk ${RTK_VERSION}" \
  && rtk init --global --opencode \
  && test -s "${XDG_CONFIG_HOME}/opencode/plugins/rtk.ts"

# Git safe directory
RUN git config --global --add safe.directory /workspace

WORKDIR /workspace

USER root

# Entrypoint
COPY config/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod 755 /usr/local/bin/entrypoint.sh

ENTRYPOINT ["entrypoint.sh"]
