# Overlord - Lightweight multi-arch devbox for OpenCode + Oh-My-OpenCode
# Base: ubuntu:24.04 (amd64 + arm64)
# No VNC/GUI — pure terminal + dev tools + LSPs

FROM --platform=$TARGETPLATFORM oven/bun:latest AS bun-stage

FROM ubuntu:24.04

ARG TARGETARCH

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
    zsh \
    unzip \
    ripgrep \
    locales \
    && rm -rf /var/lib/apt/lists/*

# Generate locale
RUN locale-gen en_US.UTF-8

# Docker CLI (for DinD via socket mounting)
RUN curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
    | tee /etc/apt/sources.list.d/docker.list > /dev/null \
    && apt-get update && apt-get install -y docker-ce-cli docker-compose-plugin && rm -rf /var/lib/apt/lists/*

# Node.js 22 via NodeSource
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y nodejs && rm -rf /var/lib/apt/lists/*

# Python 3 + pip + venv
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    python3-venv \
    && rm -rf /var/lib/apt/lists/*

# JDK 24 (architecture-aware)
RUN ADOPTIUM_ARCH=$([ "$TARGETARCH" = "arm64" ] && echo "aarch64" || echo "x64") \
    && curl -fsSL "https://api.adoptium.net/v3/binary/latest/24/ga/linux/${ADOPTIUM_ARCH}/jdk/hotspot/normal/eclipse" -o /tmp/jdk.tar.gz \
    && mkdir -p /opt/jdk-24 && tar xzf /tmp/jdk.tar.gz -C /opt/jdk-24 --strip-components=1 && rm /tmp/jdk.tar.gz \
    && update-alternatives --install /usr/bin/java java /opt/jdk-24/bin/java 100 \
    && update-alternatives --install /usr/bin/javac javac /opt/jdk-24/bin/javac 100

# Maven
RUN apt-get update && apt-get install -y maven && rm -rf /var/lib/apt/lists/*

# LSPs via npm
RUN npm install -g \
    intelephense \
    typescript-language-server \
    typescript \
    vscode-langservers-extracted \
    @tailwindcss/language-server \
    yaml-language-server \
    dockerfile-language-server-nodejs \
    bash-language-server

# LSPs + tools via apt (NO tigervnc-tools)
RUN apt-get update && apt-get install -y \
    clangd \
    python3-pylsp \
    shellcheck \
    shfmt \
    && rm -rf /var/lib/apt/lists/*

# uv - fast Python package manager
RUN curl -LsSf https://astral.sh/uv/install.sh | sh

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

# Config directories
RUN mkdir -p /home/overlord/.config/opencode /home/overlord/.config/zellij/layouts /home/overlord/.bun /home/overlord/.cache/zellij \
    && chown -R overlord:overlord /home/overlord/.config /home/overlord/.bun /home/overlord/.cache

USER overlord

RUN sh -c "$(curl -fsSL https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh)" "" --unattended \
    && git clone https://github.com/zsh-users/zsh-autosuggestions ${ZSH_CUSTOM:-~/.oh-my-zsh/custom}/plugins/zsh-autosuggestions \
    && git clone https://github.com/zsh-users/zsh-syntax-highlighting ${ZSH_CUSTOM:-~/.oh-my-zsh/custom}/plugins/zsh-syntax-highlighting \
    && git clone https://github.com/zsh-users/zsh-completions ${ZSH_CUSTOM:-~/.oh-my-zsh/custom}/plugins/zsh-completions \
    && sed -i 's/plugins=(git)/plugins=(git zsh-autosuggestions zsh-syntax-highlighting zsh-completions docker mvn npm)/' ~/.zshrc

ENV BUN_INSTALL=/home/overlord/.bun
ENV PATH="/home/overlord/.bun/bin:/home/overlord/.local/bin:/opt/jdk-24/bin:$PATH"
ENV JAVA_HOME=/opt/jdk-24
ENV LANG=en_US.UTF-8

RUN echo 'export JAVA_HOME=/opt/jdk-24' >> /home/overlord/.zshrc \
    && echo 'export PATH="/opt/jdk-24/bin:$PATH"' >> /home/overlord/.zshrc

# Install opencode-ai
RUN bun add -g opencode-ai@latest

# Config files
COPY --chown=overlord:overlord config/opencode.json /home/overlord/.config/opencode/opencode.json
COPY --chown=overlord:overlord config/oh-my-opencode.json /home/overlord/.config/opencode/oh-my-opencode.json
COPY --chown=overlord:overlord config/oh-my-opencode.json /home/overlord/.config/opencode/oh-my-opencode.jsonc
COPY --chown=overlord:overlord config/zellij-config.kdl /home/overlord/.config/zellij/config.kdl
RUN cd /home/overlord/.config/opencode && bun init -y > /dev/null 2>&1 && bun add oh-my-opencode@latest

# Git safe directory
RUN git config --global --add safe.directory /workspace

WORKDIR /workspace

USER root

# Entrypoint
COPY config/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod 755 /usr/local/bin/entrypoint.sh

ENTRYPOINT ["entrypoint.sh"]
