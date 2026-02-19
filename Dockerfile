# Overlord - Full-fat OpenCode with EVERYTHING
# Base: gitpod/workspace-full-vnc (Node, Python, Java, PHP, Docker, VNC, etc.)

FROM gitpod/workspace-full-vnc

USER root

# JDK 24 (latest GA) via Adoptium + Maven
RUN curl -fsSL "https://api.adoptium.net/v3/binary/latest/24/ga/linux/x64/jdk/hotspot/normal/eclipse" -o /tmp/jdk.tar.gz \
    && mkdir -p /opt/jdk-24 && tar xzf /tmp/jdk.tar.gz -C /opt/jdk-24 --strip-components=1 && rm /tmp/jdk.tar.gz \
    && update-alternatives --install /usr/bin/java java /opt/jdk-24/bin/java 100 \
    && update-alternatives --install /usr/bin/javac javac /opt/jdk-24/bin/javac 100 \
    && apt-get update && apt-get install -y maven && rm -rf /var/lib/apt/lists/*

# LSPs
RUN npm install -g \
    intelephense \
    typescript-language-server \
    typescript \
    vscode-langservers-extracted \
    @tailwindcss/language-server \
    yaml-language-server \
    dockerfile-language-server-nodejs \
    bash-language-server

# More LSPs + tools via apt
RUN apt-get update && apt-get install -y \
    clangd \
    python3-pylsp \
    shellcheck \
    shfmt \
    && rm -rf /var/lib/apt/lists/*

# uv - fast Python package manager
RUN curl -LsSf https://astral.sh/uv/install.sh | sh

# zellij
ARG ZELLIJ_VERSION=0.43.1
RUN curl -fsSL "https://github.com/zellij-org/zellij/releases/download/v${ZELLIJ_VERSION}/zellij-x86_64-unknown-linux-musl.tar.gz" \
    | tar xz -C /usr/local/bin

# bun
COPY --from=oven/bun:latest /usr/local/bin/bun /usr/local/bin/bunx /usr/local/bin/

RUN mkdir -p /home/gitpod/.config/opencode /home/gitpod/.config/zellij/layouts /home/gitpod/.bun \
    && chown -R gitpod:gitpod /home/gitpod/.config /home/gitpod/.bun

USER gitpod

ENV BUN_INSTALL=/home/gitpod/.bun
ENV PATH="/home/gitpod/.bun/bin:/opt/jdk-24/bin:$PATH"
ENV JAVA_HOME=/opt/jdk-24

RUN echo 'export JAVA_HOME=/opt/jdk-24' >> /home/gitpod/.bashrc \
    && echo 'export PATH="/opt/jdk-24/bin:$PATH"' >> /home/gitpod/.bashrc

RUN bun add -g opencode-ai@latest

COPY --chown=gitpod:gitpod config/opencode.json /home/gitpod/.config/opencode/opencode.json
COPY --chown=gitpod:gitpod config/oh-my-opencode.json /home/gitpod/.config/opencode/oh-my-opencode.json
COPY --chown=gitpod:gitpod config/oh-my-opencode.json /home/gitpod/.config/opencode/oh-my-opencode.jsonc
COPY --chown=gitpod:gitpod config/zellij-config.kdl /home/gitpod/.config/zellij/config.kdl
RUN cd /home/gitpod/.config/opencode && bun init -y > /dev/null 2>&1 && bun add oh-my-opencode@latest

RUN git config --global --add safe.directory /workspace

WORKDIR /workspace

USER root

COPY scripts/install-java.sh scripts/install-typescript.sh scripts/install-php.sh /usr/local/bin/
COPY scripts/start-vnc.sh /usr/local/bin/start-vnc.sh
RUN chmod +x /usr/local/bin/install-java.sh /usr/local/bin/install-typescript.sh /usr/local/bin/install-php.sh /usr/local/bin/start-vnc.sh

COPY config/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 6080

ENTRYPOINT ["entrypoint.sh"]
