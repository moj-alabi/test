#!/usr/bin/env bash
#
# install_node_exporter.sh
#
# One-shot installer for Prometheus node_exporter on Ubuntu.
# Installs the binary, creates a dedicated system user, sets up a
# systemd service listening on port 9100, and starts it.
#
# Usage:
#   sudo ./install_node_exporter.sh [PROMETHEUS_SERVER_IP]
#
# If PROMETHEUS_SERVER_IP is supplied and ufw is active, a firewall
# rule is added to allow port 9100 only from that IP. If omitted,
# no firewall rule is created — you must handle access control yourself.

set -euo pipefail

NODE_EXPORTER_VERSION="1.8.2"
NODE_EXPORTER_USER="node_exporter"
LISTEN_PORT="9100"
PROM_SERVER_IP="${1:-}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "This script must be run as root (use sudo)." >&2
  exit 1
fi

ARCH="$(uname -m)"
case "${ARCH}" in
  x86_64) NE_ARCH="amd64" ;;
  aarch64) NE_ARCH="arm64" ;;
  *)
    echo "Unsupported architecture: ${ARCH}" >&2
    exit 1
    ;;
esac

echo "==> Installing dependencies (wget, tar)..."
apt-get update -y
apt-get install -y wget tar

echo "==> Downloading node_exporter v${NODE_EXPORTER_VERSION} (${NE_ARCH})..."
TMP_DIR="$(mktemp -d)"
cd "${TMP_DIR}"
wget -q "https://github.com/prometheus/node_exporter/releases/download/v${NODE_EXPORTER_VERSION}/node_exporter-${NODE_EXPORTER_VERSION}.linux-${NE_ARCH}.tar.gz"
tar xzf "node_exporter-${NODE_EXPORTER_VERSION}.linux-${NE_ARCH}.tar.gz"

echo "==> Installing binary to /usr/local/bin/node_exporter..."
mv "node_exporter-${NODE_EXPORTER_VERSION}.linux-${NE_ARCH}/node_exporter" /usr/local/bin/node_exporter
chown root:root /usr/local/bin/node_exporter
chmod 755 /usr/local/bin/node_exporter

cd /
rm -rf "${TMP_DIR}"

echo "==> Creating dedicated system user '${NODE_EXPORTER_USER}'..."
if ! id -u "${NODE_EXPORTER_USER}" >/dev/null 2>&1; then
  useradd --no-create-home --shell /usr/sbin/nologin "${NODE_EXPORTER_USER}"
else
  echo "    User already exists, skipping."
fi

echo "==> Writing systemd service..."
cat > /etc/systemd/system/node_exporter.service << EOF
[Unit]
Description=Prometheus Node Exporter
Wants=network-online.target
After=network-online.target

[Service]
User=${NODE_EXPORTER_USER}
Group=${NODE_EXPORTER_USER}
Type=simple
ExecStart=/usr/local/bin/node_exporter --web.listen-address=:${LISTEN_PORT}
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF

echo "==> Starting and enabling node_exporter..."
systemctl daemon-reload
systemctl enable --now node_exporter

sleep 1
systemctl --no-pager status node_exporter || true

echo "==> Verifying local metrics endpoint..."
if curl -s "http://localhost:${LISTEN_PORT}/metrics" | grep -q "^# HELP"; then
  echo "    OK — node_exporter is serving metrics on port ${LISTEN_PORT}."
else
  echo "    WARNING — could not confirm metrics output. Check 'systemctl status node_exporter' and journalctl." >&2
fi

if command -v ufw >/dev/null 2>&1 && ufw status | grep -q "Status: active"; then
  if [[ -n "${PROM_SERVER_IP}" ]]; then
    echo "==> ufw is active. Allowing port ${LISTEN_PORT} from ${PROM_SERVER_IP} only..."
    ufw allow from "${PROM_SERVER_IP}" to any port "${LISTEN_PORT}" proto tcp
  else
    echo "==> ufw is active but no Prometheus server IP was supplied."
    echo "    Port ${LISTEN_PORT} is NOT open through ufw — node_exporter is only reachable locally until you add a rule."
    echo "    Re-run as: sudo ./install_node_exporter.sh <PROMETHEUS_SERVER_IP>"
    echo "    Or manually: sudo ufw allow from <PROMETHEUS_SERVER_IP> to any port ${LISTEN_PORT} proto tcp"
  fi
else
  echo "==> ufw not active or not installed — no firewall rule created."
  echo "    node_exporter has no built-in authentication. Restrict access to port ${LISTEN_PORT} at the network level"
  echo "    (security group / NACL / iptables) to only your Prometheus server, since this endpoint exposes system metrics."
fi

echo ""
echo "==> Done. node_exporter is running on port ${LISTEN_PORT}."
echo "    Add this host's IP:${LISTEN_PORT} to your Prometheus server's scrape targets."
