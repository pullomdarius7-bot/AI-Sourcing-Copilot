#!/usr/bin/env bash
# Prepares a fresh Ubuntu 22.04 / 24.04 server: Docker Engine + Compose plugin, a deploy user, firewall, fail2ban,
# automatic security updates, swap, log rotation for containers. Run once as root:
#   sudo DEPLOY_USER=deploy SSH_PORT=22 SWAP_GB=4 ./scripts/server-bootstrap.sh
# Safe to run again.
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "Run as root (sudo)."; exit 1; }
. /etc/os-release
[ "${ID:-}" = "ubuntu" ] || { echo "This script supports Ubuntu only (found: ${ID:-unknown})."; exit 1; }

DEPLOY_USER=${DEPLOY_USER:-deploy}
SSH_PORT=${SSH_PORT:-22}
SWAP_GB=${SWAP_GB:-4}
APP_ROOT=${APP_ROOT:-/opt/copilot}
export DEBIAN_FRONTEND=noninteractive

echo "==> System packages"
apt-get update
apt-get -y upgrade
apt-get -y install ca-certificates curl gnupg git ufw fail2ban unattended-upgrades rsync cron openssl unzip logrotate

echo "==> Docker Engine and Compose plugin (Docker's apt repository)"
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
  > /etc/apt/sources.list.d/docker.list
apt-get update
apt-get -y install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

echo "==> Docker daemon: bounded container logs, containers survive daemon restarts"
install -d /etc/docker
cat > /etc/docker/daemon.json <<'JSON'
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "20m", "max-file": "5" },
  "live-restore": true
}
JSON
systemctl enable docker
systemctl restart docker

echo "==> Deploy user '$DEPLOY_USER' (member of the docker group)"
id "$DEPLOY_USER" >/dev/null 2>&1 || adduser --disabled-password --gecos "" "$DEPLOY_USER"
usermod -aG docker "$DEPLOY_USER"
DEPLOY_HOME=$(getent passwd "$DEPLOY_USER" | cut -d: -f6)
install -d -m 700 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "$DEPLOY_HOME/.ssh"
if [ ! -s "$DEPLOY_HOME/.ssh/authorized_keys" ] && [ -s /root/.ssh/authorized_keys ]; then
  install -m 600 -o "$DEPLOY_USER" -g "$DEPLOY_USER" /root/.ssh/authorized_keys "$DEPLOY_HOME/.ssh/authorized_keys"
  echo "    copied root's authorized_keys to $DEPLOY_USER"
fi

echo "==> Directories"
install -d -m 750 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "$APP_ROOT" "$APP_ROOT/backups"
install -d -m 750 -o root -g "$DEPLOY_USER" /etc/copilot
install -d -m 750 -o "$DEPLOY_USER" -g "$DEPLOY_USER" /var/log/copilot

echo "==> Swap (${SWAP_GB} GB) and kernel settings"
if [ "$SWAP_GB" != "0" ] && ! swapon --show | grep -q .; then
  fallocate -l "${SWAP_GB}G" /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi
cat > /etc/sysctl.d/60-copilot.conf <<'CONF'
vm.swappiness = 10
net.core.somaxconn = 1024
fs.inotify.max_user_watches = 262144
CONF
sysctl --system >/dev/null

echo "==> Firewall: SSH ($SSH_PORT), HTTP, HTTPS only"
ufw default deny incoming
ufw default allow outgoing
ufw allow "${SSH_PORT}/tcp"
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

echo "==> SSH: keys only"
if [ -s "$DEPLOY_HOME/.ssh/authorized_keys" ] || [ -s /root/.ssh/authorized_keys ]; then
  cat > /etc/ssh/sshd_config.d/10-copilot.conf <<CONF
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
MaxAuthTries 4
CONF
  sshd -t && { systemctl reload ssh || systemctl restart ssh; }
else
  echo "    SKIPPED: no authorized_keys found for root or $DEPLOY_USER. Add your key, then run this script again."
fi

echo "==> fail2ban for SSH"
cat > /etc/fail2ban/jail.d/sshd.local <<CONF
[sshd]
enabled = true
port = ${SSH_PORT}
maxretry = 5
bantime = 1h
findtime = 10m
CONF
systemctl enable fail2ban
systemctl restart fail2ban

echo "==> Automatic security updates and time synchronisation"
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'CONF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
CONF
systemctl enable --now unattended-upgrades
timedatectl set-ntp true || true

echo
echo "Done. Docker $(docker --version | cut -d' ' -f3 | tr -d ,), Compose $(docker compose version --short)."
echo "Next: log in as $DEPLOY_USER and put the code in $APP_ROOT/app (see docs/DEPLOYMENT.md, section 20)."
