# Overlord - Lean Dockerized OpenCode with zellij multiplexing
# Provides isolated execution environment with multi-provider support

FROM node:22-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    bash \
    zsh \
    neovim \
    curl \
    less \
    openssh-client \
    ca-certificates \
    locales \
    ncurses-base \
    docker.io \
    gosu \
    && sed -i 's/^# *\(en_US.UTF-8\)/\1/' /etc/locale.gen \
    && locale-gen \
    && rm -rf /var/lib/apt/lists/*

RUN git clone https://github.com/LazyVim/starter ~/.config/nvim
RUN rm -rf ~/.config/nvim/.git

# zellij - terminal multiplexer (static musl binary)
ARG ZELLIJ_VERSION=0.43.1
RUN ARCH=$(dpkg --print-architecture) \
    && case "${ARCH}" in \
         amd64) ZELLIJ_ARCH="x86_64" ;; \
         arm64) ZELLIJ_ARCH="aarch64" ;; \
         *) echo "Unsupported arch: ${ARCH}" >&2; exit 1 ;; \
       esac \
    && curl -fsSL "https://github.com/zellij-org/zellij/releases/download/v${ZELLIJ_VERSION}/zellij-${ZELLIJ_ARCH}-unknown-linux-musl.tar.gz" \
       | tar xz -C /usr/local/bin

# bun - JavaScript runtime and package manager
COPY --from=oven/bun:latest /usr/local/bin/bun /usr/local/bin/bunx /usr/local/bin/

ARG USER_ID=1000
ARG GROUP_ID=1000
ARG DOCKER_GID=999
RUN groupadd -g ${GROUP_ID} overlord 2>/dev/null || groupadd overlord && \
    useradd -m -u ${USER_ID} -g overlord -s /bin/zsh overlord 2>/dev/null || \
    useradd -m -g overlord -s /bin/zsh overlord && \
    groupadd -g ${DOCKER_GID} docker 2>/dev/null || true && \
    usermod -aG docker overlord

RUN mkdir -p /home/overlord/.config/opencode /home/overlord/.config/zellij/layouts /home/overlord/.bun /home/overlord/.local/state /home/overlord/.local/share/opencode /workspace && \
    chown -R overlord:overlord /home/overlord /workspace

USER overlord
WORKDIR /home/overlord

# oh-my-zsh
RUN sh -c "$(curl -fsSL https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh)" "" --unattended \
    && chmod 700 /home/overlord/.oh-my-zsh /home/overlord/.oh-my-zsh/cache /home/overlord/.oh-my-zsh/custom \
    && sed -i '1i export ZSH_DISABLE_COMPFIX=true' /home/overlord/.zshrc

ENV HOME=/home/overlord
ENV BUN_INSTALL=/home/overlord/.bun
ENV PATH="/home/overlord/.bun/bin:$PATH"
ENV TERM=xterm-256color
ENV LANG=en_US.UTF-8
ENV LC_ALL=en_US.UTF-8

RUN bun add -g opencode-ai@latest

# Install oh-my-opencode plugin into opencode's config directory
COPY --chown=overlord:overlord config/opencode.json /home/overlord/.config/opencode/opencode.json
COPY --chown=overlord:overlord config/oh-my-opencode.json /home/overlord/.config/opencode/oh-my-opencode.json
COPY --chown=overlord:overlord config/oh-my-opencode.json /home/overlord/.config/opencode/oh-my-opencode.jsonc
COPY --chown=overlord:overlord config/zellij-config.kdl /home/overlord/.config/zellij/config.kdl
RUN cd /home/overlord/.config/opencode && bun init -y > /dev/null 2>&1 && bun add oh-my-opencode@latest

RUN git config --global --add safe.directory /workspace

WORKDIR /workspace

USER root

COPY scripts/install-java.sh scripts/install-typescript.sh scripts/install-php.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/install-java.sh /usr/local/bin/install-typescript.sh /usr/local/bin/install-php.sh

COPY config/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh
ENTRYPOINT ["entrypoint.sh"]
