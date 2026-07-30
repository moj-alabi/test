#!/usr/bin/env bash
# ============================================================
#  Tempest 1.0 — Server Updater
#  Pulls latest code from GitHub and restarts the service.
#
#  Usage:
#    bash update.sh
#  Or one-liner from anywhere:
#    bash <(curl -fsSL https://raw.githubusercontent.com/moj-alabi/test/main/Tempest/update.sh)
# ============================================================
set -euo pipefail

SERVICE_NAME="tempest"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
log()  { echo -e "${GREEN}[+]${NC} $*"; }
info() { echo -e "${CYAN}[*]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }

# ── Find install dir ───────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Check common locations for the repo
REPO_DIR=""
if [[ -f "$SCRIPT_DIR/server.py" ]]; then
    # Running from inside the repo
    REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
elif [[ -d "/opt/tempest_repo" ]]; then
    REPO_DIR="/opt/tempest_repo"
elif [[ -d "$HOME/tempest_repo" ]]; then
    REPO_DIR="$HOME/tempest_repo"
else
    warn "Cannot find Tempest repo directory."
    warn "Run this from inside the cloned repo, or pass the path:"
    warn "  TEMPEST_DIR=/path/to/repo bash update.sh"
    exit 1
fi

# Allow override via env var
REPO_DIR="${TEMPEST_DIR:-$REPO_DIR}"
info "Repo: $REPO_DIR"

# ── Pull latest ────────────────────────────────────────────
info "Pulling latest from origin/main..."
git -C "$REPO_DIR" fetch origin
BEFORE=$(git -C "$REPO_DIR" rev-parse HEAD)
git -C "$REPO_DIR" pull --ff-only origin main
AFTER=$(git -C "$REPO_DIR" rev-parse HEAD)

if [[ "$BEFORE" == "$AFTER" ]]; then
    log "Already up to date ($(git -C "$REPO_DIR" rev-parse --short HEAD))"
else
    log "Updated: $(git -C "$REPO_DIR" rev-parse --short "$BEFORE") → $(git -C "$REPO_DIR" rev-parse --short "$AFTER")"
    info "Changes:"
    git -C "$REPO_DIR" log --oneline "${BEFORE}..${AFTER}"
fi

# ── Create systemd service if missing, then restart ───────
TEMPEST_DIR="${REPO_DIR}/Tempest"
[[ -f "$SCRIPT_DIR/server.py" ]] && TEMPEST_DIR="$SCRIPT_DIR"

SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

if command -v systemctl >/dev/null 2>&1; then
    # Create service if it doesn't exist yet
    if [[ ! -f "$SERVICE_FILE" ]]; then
        if [[ $EUID -ne 0 ]]; then
            warn "Service file not found and not root — cannot create it."
            warn "Run with sudo to auto-create the systemd service, or restart manually:"
            warn "  cd ${TEMPEST_DIR} && python3 server.py"
        else
            PYTHON_BIN=$(command -v python3)
            info "Creating systemd service at ${SERVICE_FILE}..."
            cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Tempest 1.0 C2 Server
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=${TEMPEST_DIR}
ExecStart=${PYTHON_BIN} ${TEMPEST_DIR}/server.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
            systemctl daemon-reload
            systemctl enable "$SERVICE_NAME" --quiet
            log "Service created and enabled"
        fi
    fi

    # Restart if service file exists
    if [[ -f "$SERVICE_FILE" ]]; then
        info "Restarting systemd service '${SERVICE_NAME}'..."
        systemctl restart "$SERVICE_NAME"
        sleep 2
        if systemctl is-active --quiet "$SERVICE_NAME"; then
            log "Service '${SERVICE_NAME}' is running ✓"
            log "Logs: journalctl -u ${SERVICE_NAME} -f"
        else
            warn "Service may have failed — check: journalctl -u ${SERVICE_NAME} -n 30"
        fi
    fi
else
    warn "systemctl not available — restart manually:"
    warn "  cd ${TEMPEST_DIR} && python3 server.py"
fi

echo ""
log "Update complete."
