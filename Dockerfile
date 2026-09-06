# Overlord - Minimal dev container (Debian 13 trixie-slim: smaller than Ubuntu, same tooling)
FROM debian:trixie-slim
LABEL io.overlord.entrypoint-ready="1"
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ENV DEBIAN_FRONTEND=noninteractive

# Bootstrap the shared installer and provide the privilege-dropping runtime.
RUN apt-get update && apt-get install -y --no-install-recommends \
  curl \
  ca-certificates \
  gnupg \
  sudo \
  gosu \
  && rm -rf /var/lib/apt/lists/*


# Docker CLI (Debian trixie repo)
RUN mkdir -p /etc/apt/keyrings \
  && curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg \
  && . /etc/os-release && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian ${VERSION_CODENAME} stable" \
    | tee /etc/apt/sources.list.d/docker.list > /dev/null \
  && apt-get update && apt-get install -y docker-ce-cli docker-compose-plugin && rm -rf /var/lib/apt/lists/*


# Create non-root user 'overlord' (UID 33333)
RUN groupadd -g 33333 overlord \
  && useradd -m -u 33333 -g overlord -s /bin/bash overlord \
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
RUN chmod 755 /usr/local/bin/entrypoint.sh /usr/local/share/overlord/setup.sh \
  && chmod 644 /usr/local/share/overlord/config/tool-versions.env

# One installer owns tool versions and per-user configuration in both profiles.
RUN bash /usr/local/share/overlord/setup.sh --user overlord --profile container

# Bind mounts hide image content. Keep only authored agent defaults outside the
# mounted agent directory; never seed sessions, auth, or runtime databases.
RUN mkdir -p /usr/local/share/overlord/omp-agent-defaults \
  && cp -a /home/overlord/.omp/agent/config.yml /home/overlord/.omp/agent/models.yml \
    /home/overlord/.omp/agent/skills /usr/local/share/overlord/omp-agent-defaults/ \
  && if [ -d /home/overlord/.omp/agent/extensions ]; then \
    cp -a /home/overlord/.omp/agent/extensions /usr/local/share/overlord/omp-agent-defaults/; \
  fi \
  && chown -R root:root /usr/local/share/overlord/omp-agent-defaults \
  && chmod -R a+rX /usr/local/share/overlord/omp-agent-defaults
RUN mkdir -p /usr/local/share/overlord/prime-agent-defaults \
  && cp -a /home/overlord/.prime/agent/settings.json /home/overlord/.prime/agent/models.json \
    /home/overlord/.prime/agent/skills /usr/local/share/overlord/prime-agent-defaults/ \
  && chown -R root:root /usr/local/share/overlord/prime-agent-defaults \
  && chmod -R a+rX /usr/local/share/overlord/prime-agent-defaults


ENV HOME=/home/overlord
ENV USER=overlord
ENV LOGNAME=overlord
ENV LANG=en_US.UTF-8
ENV SHELL=/bin/zsh
ENV GIT_CONFIG_GLOBAL=/run/overlord.gitconfig


WORKDIR /workspace

ENTRYPOINT ["entrypoint.sh"]
