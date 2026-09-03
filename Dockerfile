# Overlord - Minimal dev container (Debian 13 trixie-slim: smaller than Ubuntu, same tooling)
FROM debian:trixie-slim

ENV DEBIAN_FRONTEND=noninteractive

# Base deps + Docker CLI for DinD via socket (optional but kept for dev)
# NOTE: no lsb-release on trixie-slim; codename comes from /etc/os-release.
RUN apt-get update && apt-get install -y --no-install-recommends \
  git \
  curl \
  wget \
  ca-certificates \
  gnupg \
  sudo \
  gosu \
  zsh \
  unzip \
  ripgrep \
  locales \
  xdg-utils \
  jq \
  build-essential \
  && rm -rf /var/lib/apt/lists/*

RUN locale-gen en_US.UTF-8

# Docker CLI (Debian trixie repo)
RUN mkdir -p /etc/apt/keyrings \
  && curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg \
  && . /etc/os-release && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian ${VERSION_CODENAME} stable" \
    | tee /etc/apt/sources.list.d/docker.list > /dev/null \
  && apt-get update && apt-get install -y docker-ce-cli docker-compose-plugin && rm -rf /var/lib/apt/lists/*

# Node.js 22 (useful for lazyvim LSPs, optional)
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
  && apt-get install -y nodejs && rm -rf /var/lib/apt/lists/*

# Python 3
RUN apt-get update && apt-get install -y --no-install-recommends \
  python3 \
  python3-pip \
  python3-venv \
  && rm -rf /var/lib/apt/lists/*

# Create non-root user 'overlord' (UID 33333)
RUN groupadd -g 33333 overlord \
  && useradd -m -u 33333 -g overlord -s /bin/zsh overlord \
  && echo 'overlord ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/overlord \
  && chmod 440 /etc/sudoers.d/overlord

# Config dirs
RUN mkdir -p /home/overlord/.config/zellij /home/overlord/.cache/zellij /home/overlord/.local/share \
  && chown -R overlord:overlord /home/overlord

COPY config/tool-versions.env /usr/local/share/overlord/config/tool-versions.env
# Bring setup.sh (single source of truth for VM + container)
COPY setup.sh /usr/local/share/overlord/setup.sh
COPY config/zellij-config.kdl /usr/local/share/overlord/zellij-config.kdl
COPY config/entrypoint.sh /usr/local/bin/entrypoint.sh
# Bring codegraph skill for prime-agent (used by setup.sh to install)
COPY skills/codegraph/SKILL.md /usr/local/share/overlord/skills/codegraph/SKILL.md
COPY .prime/agent/skills/codegraph/SKILL.md /usr/local/share/overlord/.prime-skills/codegraph/SKILL.md
RUN chmod 755 /usr/local/bin/entrypoint.sh /usr/local/share/overlord/setup.sh && mkdir -p /usr/local/share/overlord/config && chmod 644 /usr/local/share/overlord/config/tool-versions.env 2>/dev/null || true && mkdir -p /usr/local/share/overlord/skills/codegraph /usr/local/share/overlord/.prime-skills/codegraph

# Run setup as overlord (setup.sh handles sudo internally; run as root then chown is simpler)
# We run as root so apt doesn't need sudo, then fix ownership
RUN bash /usr/local/share/overlord/setup.sh \
  && chown -R overlord:overlord /home/overlord

# Also ensure zellij config is placed for overlord (setup.sh doesn't copy kdl, we do)
RUN mkdir -p /home/overlord/.config/zellij && cp /usr/local/share/overlord/zellij-config.kdl /home/overlord/.config/zellij/config.kdl && chown -R overlord:overlord /home/overlord/.config

ENV HOME=/home/overlord
ENV USER=overlord
ENV LOGNAME=overlord
ENV LANG=en_US.UTF-8
ENV SHELL=/bin/zsh

RUN git config --global --add safe.directory /workspace

WORKDIR /workspace

ENTRYPOINT ["entrypoint.sh"]
