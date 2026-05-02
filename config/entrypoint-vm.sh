#!/bin/bash
set -euo pipefail

SSH_RUNTIME_DIRS=(/run/sshd /var/run/sshd)
OVERLORD_DIRS=(
	/workspace
	/home/overlord/.ssh
	/home/overlord/.config/opencode
	/home/overlord/.config/zellij/layouts
	/home/overlord/.config/gcloud
	/home/overlord/.cache/zellij
	/home/overlord/.local/share/opencode
	/home/overlord/.zsh_data
)

for dir in "${SSH_RUNTIME_DIRS[@]}"; do
	mkdir -p "${dir}"
done

mkdir -p /etc/ssh

for dir in "${OVERLORD_DIRS[@]}"; do
	mkdir -p "${dir}"
done

install -m 600 -o root -g root /usr/local/share/overlord/sshd_config.vm /etc/ssh/sshd_config

ssh-keygen -A

chown -R overlord:overlord /home/overlord /workspace
chmod 755 /home/overlord
chmod 700 /home/overlord/.ssh

if [ ! -f /home/overlord/.ssh/authorized_keys ]; then
	touch /home/overlord/.ssh/authorized_keys
fi

chown overlord:overlord /home/overlord/.ssh /home/overlord/.ssh/authorized_keys
chmod 600 /home/overlord/.ssh/authorized_keys

if [ "$#" -eq 0 ]; then
	exec /usr/sbin/sshd -D -e
fi

exec "$@"
