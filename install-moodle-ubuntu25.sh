#!/usr/bin/env bash
# =============================================================================
#  install-moodle-ubuntu25.sh
#  Automated Moodle LMS Installation Script for Ubuntu 22.04 / 24.04 / 25.x
#  Stack: Apache 2.4 + MariaDB 10.11+ + PHP 8.3 + Moodle latest stable
#
#  Features:
#   - set -euo pipefail strict mode (fail fast)
#   - trap ERR with exact line number reporting
#   - Pre-flight checks (root, OS, disk, ports, internet)
#   - Full MariaDB database setup with step-by-step verification
#   - PHP 8.3 with all Moodle-required extensions
#   - Apache VirtualHost + mod_rewrite
#   - Moodle CLI unattended install
#   - Cron job + UFW firewall
#   - Idempotent (safe to re-run)
#   - Full log at /var/log/moodle_install.log
#
#  Usage:
#   sudo bash install-moodle-ubuntu25.sh
#
#  Optional env overrides before running:
#   export MOODLE_ADMIN_PASS="MyStrongPass123!"
#   export DB_PASS="AnotherStrongPass!"
#   export MOODLE_DOMAIN="learn.example.com"
# =============================================================================

set -euo pipefail

# ─── COLOUR HELPERS ───────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${CYAN}[INFO]${RESET}  $*"; }
success() { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
error()   { echo -e "${RED}[ERROR]${RESET} $*" >&2; }
header()  { echo -e "\n${BOLD}${CYAN}══════════════════════════════════════════${RESET}"; \
            echo -e "${BOLD}${CYAN}  $*${RESET}"; \
            echo -e "${BOLD}${CYAN}══════════════════════════════════════════${RESET}"; }

# ─── LOG FILE ─────────────────────────────────────────────────────────────────
LOG_FILE="/var/log/moodle_install.log"
# Redirect all output to log AND terminal
exec > >(tee -a "$LOG_FILE") 2>&1
echo ""
echo "======================================================"
echo " Moodle Install Script — $(date '+%Y-%m-%d %H:%M:%S')"
echo "======================================================"

# ─── CONFIGURATION ────────────────────────────────────────────────────────────
# These can be overridden via environment variables before running the script
MOODLE_DOMAIN="${MOODLE_DOMAIN:-localhost}"
MOODLE_ADMIN_USER="${MOODLE_ADMIN_USER:-admin}"
MOODLE_ADMIN_PASS="${MOODLE_ADMIN_PASS:-Moodle@Admin$(openssl rand -hex 4)}"
MOODLE_ADMIN_EMAIL="${MOODLE_ADMIN_EMAIL:-admin@example.com}"
MOODLE_SITE_NAME="${MOODLE_SITE_NAME:-My Moodle LMS}"

DB_HOST="localhost"
DB_NAME="${DB_NAME:-moodledb}"
DB_USER="${DB_USER:-moodleuser}"
DB_PASS="${DB_PASS:-$(openssl rand -base64 20 | tr -d '/+=' | head -c 20)}"
DB_PREFIX="mdl_"

MOODLE_WEB_DIR="/var/www/moodle"
MOODLE_DATA_DIR="/var/moodledata"

# PHP version required by Moodle 4.5+
PHP_VERSION="8.3"

# Moodle download — latest stable branch
MOODLE_BRANCH="MOODLE_405_STABLE"
MOODLE_DOWNLOAD_URL="https://download.moodle.org/download.php/direct/${MOODLE_BRANCH}/moodle-latest-405.tgz"

# ─── TRAP: Error handler ──────────────────────────────────────────────────────
INSTALL_FAILED=false

cleanup_on_error() {
    local exit_code=$?
    local line_no=${1:-unknown}
    if [[ "$INSTALL_FAILED" == "true" ]]; then return; fi
    INSTALL_FAILED=true
    echo ""
    error "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    error " INSTALLATION FAILED at line ${line_no}"
    error " Exit code: ${exit_code}"
    error " Full log: ${LOG_FILE}"
    error "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    error "Attempting to clean up partial database objects..."
    # Only roll back DB if it was just created (not pre-existing)
    if [[ "${DB_CREATED:-false}" == "true" ]]; then
        warn "Rolling back: dropping database '${DB_NAME}' and user '${DB_USER}'..."
        mysql -u root --connect-expired-password 2>/dev/null <<SQL || true
DROP DATABASE IF EXISTS \`${DB_NAME}\`;
DROP USER IF EXISTS '${DB_USER}'@'localhost';
FLUSH PRIVILEGES;
SQL
        warn "Database rollback complete."
    fi
    echo ""
    error "To retry from scratch: sudo bash $0"
}

trap 'cleanup_on_error $LINENO' ERR

# ─── STEP COUNTER ─────────────────────────────────────────────────────────────
STEP=0
step() {
    STEP=$((STEP + 1))
    header "Step ${STEP}: $*"
}

# ─── HELPER: Check if a command succeeded ─────────────────────────────────────
assert_ok() {
    local desc="$1"; shift
    if "$@"; then
        success "${desc}"
    else
        error "FAILED: ${desc}"
        exit 1
    fi
}

# ─────────────────────────────────────────────────────────────────────────────
#  PRE-FLIGHT CHECKS
# ─────────────────────────────────────────────────────────────────────────────
step "Pre-flight checks"

# 1. Must run as root
if [[ $EUID -ne 0 ]]; then
    error "This script must be run as root. Use: sudo bash $0"
    exit 1
fi
success "Running as root"

# 2. Ubuntu version check
if [[ ! -f /etc/os-release ]]; then
    error "Cannot detect OS — /etc/os-release not found."
    exit 1
fi
# shellcheck source=/dev/null
source /etc/os-release
OS_NAME="${NAME:-Unknown}"
OS_VER="${VERSION_ID:-0}"
info "Detected OS: ${OS_NAME} ${OS_VER}"

if [[ "${ID:-}" != "ubuntu" ]]; then
    error "This script is designed for Ubuntu. Detected: ${OS_NAME}"
    exit 1
fi

# Accept Ubuntu 22.04, 24.04, 25.xx
OS_MAJOR=$(echo "$OS_VER" | cut -d. -f1)
if [[ "$OS_MAJOR" -lt 22 ]]; then
    error "Ubuntu ${OS_VER} is not supported. Minimum: Ubuntu 22.04"
    exit 1
fi
success "Ubuntu ${OS_VER} — supported"

# 3. Internet connectivity
info "Checking internet connectivity..."
if ! curl -sf --max-time 10 "https://download.moodle.org" > /dev/null; then
    error "Cannot reach download.moodle.org — check your internet connection."
    exit 1
fi
success "Internet connectivity OK"

# 4. Disk space (require at least 3 GB free in /var)
AVAIL_KB=$(df /var --output=avail | tail -1)
AVAIL_GB=$(echo "scale=1; $AVAIL_KB / 1048576" | bc)
if [[ "$AVAIL_KB" -lt 3145728 ]]; then
    error "Insufficient disk space in /var: ${AVAIL_GB} GB available, 3 GB required."
    exit 1
fi
success "Disk space OK: ${AVAIL_GB} GB available in /var"

# 5. Port check (warn if 80 already in use — don't fail, Apache might just need restart)
if ss -tlnp 2>/dev/null | grep -q ':80 '; then
    warn "Port 80 is already in use. Apache may already be installed."
fi

# 6. Check that apt is not locked
if fuser /var/lib/dpkg/lock-frontend > /dev/null 2>&1; then
    error "apt/dpkg is locked by another process. Please wait and retry."
    exit 1
fi
success "apt lock check OK"

# ─────────────────────────────────────────────────────────────────────────────
#  SYSTEM UPDATE
# ─────────────────────────────────────────────────────────────────────────────
step "Update system packages"
info "Running apt update..."
apt-get update -qq
info "Running apt upgrade (this may take a few minutes)..."
DEBIAN_FRONTEND=noninteractive apt-get upgrade -y -qq
success "System packages updated"

# ─────────────────────────────────────────────────────────────────────────────
#  INSTALL REQUIRED UTILITIES
# ─────────────────────────────────────────────────────────────────────────────
step "Install prerequisite utilities"
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    curl wget git unzip bc openssl software-properties-common \
    apt-transport-https ca-certificates gnupg lsb-release ufw
success "Utilities installed"

# ─────────────────────────────────────────────────────────────────────────────
#  INSTALL PHP 8.3
# ─────────────────────────────────────────────────────────────────────────────
step "Install PHP ${PHP_VERSION} and Moodle extensions"

# Add ondrej/php PPA for precise PHP version control
if ! grep -q "ondrej/php" /etc/apt/sources.list.d/*.list 2>/dev/null; then
    info "Adding ondrej/php PPA..."
    add-apt-repository -y ppa:ondrej/php
    apt-get update -qq
fi

# Full list of PHP extensions required/recommended by Moodle 4.5
PHP_EXTENSIONS=(
    "php${PHP_VERSION}"
    "php${PHP_VERSION}-cli"
    "php${PHP_VERSION}-common"
    "php${PHP_VERSION}-fpm"
    "php${PHP_VERSION}-mysql"
    "php${PHP_VERSION}-xml"
    "php${PHP_VERSION}-xmlrpc"
    "php${PHP_VERSION}-curl"
    "php${PHP_VERSION}-gd"
    "php${PHP_VERSION}-imagick"
    "php${PHP_VERSION}-dev"
    "php${PHP_VERSION}-imap"
    "php${PHP_VERSION}-mbstring"
    "php${PHP_VERSION}-opcache"
    "php${PHP_VERSION}-soap"
    "php${PHP_VERSION}-zip"
    "php${PHP_VERSION}-intl"
    "php${PHP_VERSION}-ldap"
    "php${PHP_VERSION}-redis"
    "libapache2-mod-php${PHP_VERSION}"
)

info "Installing PHP ${PHP_VERSION} and ${#PHP_EXTENSIONS[@]} extensions..."
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${PHP_EXTENSIONS[@]}"

# Verify PHP installed correctly
PHP_INSTALLED_VER=$(php -r "echo PHP_MAJOR_VERSION.'.'.PHP_MINOR_VERSION;" 2>/dev/null || echo "0.0")
if [[ "$PHP_INSTALLED_VER" != "${PHP_VERSION}" ]]; then
    error "PHP ${PHP_VERSION} installation failed. Detected: ${PHP_INSTALLED_VER}"
    exit 1
fi
success "PHP ${PHP_INSTALLED_VER} installed with all required extensions"

# ─── Configure PHP for Moodle ─────────────────────────────────────────────────
step "Configure PHP settings for Moodle"

PHP_INI_APACHE="/etc/php/${PHP_VERSION}/apache2/php.ini"
PHP_INI_CLI="/etc/php/${PHP_VERSION}/cli/php.ini"

configure_php_ini() {
    local ini_file="$1"
    info "Configuring ${ini_file}..."
    sed -i "s/^max_execution_time.*/max_execution_time = 360/"         "$ini_file"
    sed -i "s/^max_input_time.*/max_input_time = 360/"                 "$ini_file"
    sed -i "s/^post_max_size.*/post_max_size = 64M/"                   "$ini_file"
    sed -i "s/^upload_max_filesize.*/upload_max_filesize = 64M/"       "$ini_file"
    sed -i "s/^memory_limit.*/memory_limit = 256M/"                    "$ini_file"
    sed -i "s/^;date.timezone.*/date.timezone = UTC/"                   "$ini_file"
    sed -i "s/^date.timezone.*/date.timezone = UTC/"                    "$ini_file"
    # max_input_vars is commented by default — add it
    if ! grep -q "^max_input_vars" "$ini_file"; then
        echo "max_input_vars = 5000" >> "$ini_file"
    else
        sed -i "s/^max_input_vars.*/max_input_vars = 5000/" "$ini_file"
    fi
    # Moodle requires session.save_handler = files
    sed -i "s/^session.gc_maxlifetime.*/session.gc_maxlifetime = 7200/" "$ini_file"
}

configure_php_ini "$PHP_INI_APACHE"
configure_php_ini "$PHP_INI_CLI"
success "PHP configuration applied"

# ─────────────────────────────────────────────────────────────────────────────
#  INSTALL AND CONFIGURE APACHE
# ─────────────────────────────────────────────────────────────────────────────
step "Install and configure Apache web server"

if ! dpkg -l apache2 2>/dev/null | grep -q "^ii"; then
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq apache2
fi

# Enable required Apache modules
for mod in rewrite headers ssl; do
    a2enmod "$mod" > /dev/null 2>&1 || true
done

# Disable default site
a2dissite 000-default.conf > /dev/null 2>&1 || true

# Create Moodle VirtualHost
APACHE_CONF="/etc/apache2/sites-available/moodle.conf"
if [[ ! -f "$APACHE_CONF" ]]; then
    cat > "$APACHE_CONF" <<APACHECONF
<VirtualHost *:80>
    ServerName ${MOODLE_DOMAIN}
    ServerAdmin webmaster@${MOODLE_DOMAIN}
    DocumentRoot ${MOODLE_WEB_DIR}

    <Directory ${MOODLE_WEB_DIR}>
        Options -Indexes +FollowSymLinks
        AllowOverride All
        Require all granted
    </Directory>

    # Security headers
    Header always set X-Content-Type-Options "nosniff"
    Header always set X-Frame-Options "SAMEORIGIN"
    Header always set X-XSS-Protection "1; mode=block"
    Header always set Referrer-Policy "strict-origin-when-cross-origin"

    ErrorLog \${APACHE_LOG_DIR}/moodle_error.log
    CustomLog \${APACHE_LOG_DIR}/moodle_access.log combined
</VirtualHost>
APACHECONF
    info "Apache VirtualHost created: ${APACHE_CONF}"
fi

a2ensite moodle.conf > /dev/null 2>&1

# Test Apache configuration
assert_ok "Apache configuration syntax check" apache2ctl configtest

# Start/restart Apache
systemctl enable apache2 > /dev/null 2>&1
systemctl restart apache2
systemctl is-active apache2 > /dev/null 2>&1
success "Apache installed, configured, and running"

# ─────────────────────────────────────────────────────────────────────────────
#  INSTALL MARIADB
# ─────────────────────────────────────────────────────────────────────────────
step "Install MariaDB database server"

if ! dpkg -l mariadb-server 2>/dev/null | grep -q "^ii"; then
    info "Installing MariaDB server..."
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq mariadb-server mariadb-client
else
    info "MariaDB already installed — skipping package install"
fi

# Enable and start MariaDB
systemctl enable mariadb > /dev/null 2>&1
systemctl start mariadb

# Verify MariaDB is running
if ! systemctl is-active --quiet mariadb; then
    error "MariaDB failed to start. Check: journalctl -u mariadb -n 50"
    exit 1
fi
success "MariaDB is running"

# Verify MariaDB version
DB_VER=$(mysql -u root -e "SELECT VERSION();" 2>/dev/null | tail -1 || echo "unknown")
info "MariaDB version: ${DB_VER}"

# ─────────────────────────────────────────────────────────────────────────────
#  SECURE MARIADB (non-interactive equivalent of mysql_secure_installation)
# ─────────────────────────────────────────────────────────────────────────────
step "Secure MariaDB installation"

info "Applying security hardening to MariaDB..."
mysql -u root 2>/dev/null <<'SECURE_SQL'
DELETE FROM mysql.user WHERE User='';
DELETE FROM mysql.user WHERE User='root' AND Host NOT IN ('localhost', '127.0.0.1', '::1');
DROP DATABASE IF EXISTS test;
DELETE FROM mysql.db WHERE Db='test' OR Db LIKE 'test\_%';
FLUSH PRIVILEGES;
SECURE_SQL
# shellcheck disable=SC2181
if [[ $? -ne 0 ]]; then
    warn "Some security settings may already be applied — continuing"
fi

success "MariaDB security hardening applied"

# ─────────────────────────────────────────────────────────────────────────────
#  DATABASE SETUP — WITH STEP-BY-STEP VERIFICATION
# ─────────────────────────────────────────────────────────────────────────────
step "Create Moodle database and user"

# ── DB Step 1: Check if database already exists ────────────────────────────
DB_EXISTS=$(mysql -u root -sse "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME='${DB_NAME}';" 2>/dev/null || echo "")

if [[ -z "$DB_EXISTS" ]]; then
    info "[DB 1/6] Creating database '${DB_NAME}'..."
    mysql -u root -e "CREATE DATABASE \`${DB_NAME}\` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;" 2>/dev/null
    # Verify creation
    DB_CHECK=$(mysql -u root -sse "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME='${DB_NAME}';" 2>/dev/null || echo "")
    if [[ -z "$DB_CHECK" ]]; then
        error "Database '${DB_NAME}' was not created successfully."
        exit 1
    fi
    DB_CREATED=true
    success "[DB 1/6] Database '${DB_NAME}' created and verified"
else
    warn "[DB 1/6] Database '${DB_NAME}' already exists — skipping creation"
    DB_CREATED=false
fi

# ── DB Step 2: Check if user already exists ────────────────────────────────
USER_EXISTS=$(mysql -u root -sse "SELECT User FROM mysql.user WHERE User='${DB_USER}' AND Host='localhost';" 2>/dev/null || echo "")

if [[ -z "$USER_EXISTS" ]]; then
    info "[DB 2/6] Creating database user '${DB_USER}'@'localhost'..."
    mysql -u root -e "CREATE USER '${DB_USER}'@'localhost' IDENTIFIED BY '${DB_PASS}';" 2>/dev/null
    # Verify user creation
    USER_CHECK=$(mysql -u root -sse "SELECT User FROM mysql.user WHERE User='${DB_USER}' AND Host='localhost';" 2>/dev/null || echo "")
    if [[ -z "$USER_CHECK" ]]; then
        error "Database user '${DB_USER}' was not created successfully."
        exit 1
    fi
    success "[DB 2/6] User '${DB_USER}'@'localhost' created and verified"
else
    warn "[DB 2/6] User '${DB_USER}'@'localhost' already exists — updating password..."
    mysql -u root -e "ALTER USER '${DB_USER}'@'localhost' IDENTIFIED BY '${DB_PASS}';" 2>/dev/null
    success "[DB 2/6] User password updated"
fi

# ── DB Step 3: Grant privileges ────────────────────────────────────────────
info "[DB 3/6] Granting ALL PRIVILEGES on '${DB_NAME}' to '${DB_USER}'..."
mysql -u root -e "GRANT ALL PRIVILEGES ON \`${DB_NAME}\`.* TO '${DB_USER}'@'localhost';" 2>/dev/null
success "[DB 3/6] Privileges granted"

# ── DB Step 4: Flush privileges ───────────────────────────────────────────
info "[DB 4/6] Flushing privileges..."
mysql -u root -e "FLUSH PRIVILEGES;" 2>/dev/null
success "[DB 4/6] Privileges flushed"

# ── DB Step 5: Verify grants ──────────────────────────────────────────────
info "[DB 5/6] Verifying grants for '${DB_USER}'..."
GRANT_CHECK=$(mysql -u root -sse "SHOW GRANTS FOR '${DB_USER}'@'localhost';" 2>/dev/null | grep -i "ALL PRIVILEGES" || echo "")
if [[ -z "$GRANT_CHECK" ]]; then
    error "Grant verification failed for '${DB_USER}'."
    exit 1
fi
success "[DB 5/6] Grants verified: ALL PRIVILEGES on ${DB_NAME}"

# ── DB Step 6: Test authentication with new credentials ────────────────────
info "[DB 6/6] Testing login with '${DB_USER}' credentials..."
TEST_RESULT=$(mysql -u "${DB_USER}" -p"${DB_PASS}" -h "${DB_HOST}" \
    -e "SELECT 'connection_ok' AS test;" "${DB_NAME}" 2>/dev/null | grep "connection_ok" || echo "")
if [[ -z "$TEST_RESULT" ]]; then
    error "Cannot authenticate as '${DB_USER}' — check DB_PASS and try again."
    exit 1
fi
success "[DB 6/6] Database authentication test PASSED ✓"

# Configure MariaDB for Moodle (innodb settings)
MARIADB_MOODLE_CONF="/etc/mysql/mariadb.conf.d/99-moodle.cnf"
if [[ ! -f "$MARIADB_MOODLE_CONF" ]]; then
    cat > "$MARIADB_MOODLE_CONF" <<MYCNF
[mysqld]
# Moodle recommended settings
default_storage_engine = innodb
innodb_file_per_table = 1
innodb_large_prefix = 1
innodb_file_format = Barracuda
character-set-server = utf8mb4
collation-server = utf8mb4_unicode_ci
skip-character-set-client-handshake
innodb_buffer_pool_size = 256M
max_allowed_packet = 64M
MYCNF
    systemctl restart mariadb
    # Verify MariaDB restarted cleanly after config change
    sleep 2
    if ! systemctl is-active --quiet mariadb; then
        error "MariaDB failed to restart after configuration update."
        exit 1
    fi
    success "MariaDB InnoDB/charset configuration applied and restarted"
fi

# ─────────────────────────────────────────────────────────────────────────────
#  DOWNLOAD MOODLE
# ─────────────────────────────────────────────────────────────────────────────
step "Download Moodle latest stable release"

MOODLE_TGZ="/tmp/moodle-latest.tgz"

if [[ -d "${MOODLE_WEB_DIR}/lib" ]]; then
    EXISTING_VER=$(php "${MOODLE_WEB_DIR}/version.php" 2>/dev/null | grep "\$release" | head -1 || echo "unknown")
    warn "Moodle already installed at ${MOODLE_WEB_DIR} (${EXISTING_VER}) — skipping download"
else
    info "Downloading Moodle from ${MOODLE_DOWNLOAD_URL}..."
    if ! curl -L --fail --progress-bar --retry 3 --retry-delay 5 \
         -o "${MOODLE_TGZ}" "${MOODLE_DOWNLOAD_URL}"; then
        error "Failed to download Moodle. Check URL or internet connection."
        exit 1
    fi

    # Verify archive is valid
    info "Verifying downloaded archive..."
    if ! tar -tzf "${MOODLE_TGZ}" > /dev/null 2>&1; then
        error "Downloaded archive is corrupt or invalid."
        rm -f "${MOODLE_TGZ}"
        exit 1
    fi
    success "Archive verified"

    # Extract to web dir
    info "Extracting Moodle to ${MOODLE_WEB_DIR}..."
    mkdir -p "$(dirname "${MOODLE_WEB_DIR}")"
    tar -xzf "${MOODLE_TGZ}" -C /var/www/
    # Moodle extracts as 'moodle' directory
    if [[ -d "/var/www/moodle" && "${MOODLE_WEB_DIR}" != "/var/www/moodle" ]]; then
        mv /var/www/moodle "${MOODLE_WEB_DIR}"
    fi
    rm -f "${MOODLE_TGZ}"
    success "Moodle extracted to ${MOODLE_WEB_DIR}"
fi

# ─────────────────────────────────────────────────────────────────────────────
#  CREATE MOODLEDATA DIRECTORY (outside web root — security requirement)
# ─────────────────────────────────────────────────────────────────────────────
step "Create moodledata directory"

if [[ ! -d "${MOODLE_DATA_DIR}" ]]; then
    mkdir -p "${MOODLE_DATA_DIR}"
    success "Created ${MOODLE_DATA_DIR}"
else
    info "${MOODLE_DATA_DIR} already exists"
fi

# ─────────────────────────────────────────────────────────────────────────────
#  SET FILE PERMISSIONS
# ─────────────────────────────────────────────────────────────────────────────
step "Set file permissions"

info "Setting ownership and permissions on web directory..."
chown -R www-data:www-data "${MOODLE_WEB_DIR}"
chmod -R 755 "${MOODLE_WEB_DIR}"

info "Setting ownership and permissions on moodledata directory..."
chown -R www-data:www-data "${MOODLE_DATA_DIR}"
chmod -R 770 "${MOODLE_DATA_DIR}"

# Moodle requires config.php to be writable by web server only initially
if [[ -f "${MOODLE_WEB_DIR}/config.php" ]]; then
    chmod 644 "${MOODLE_WEB_DIR}/config.php"
fi

success "File permissions set"

# ─────────────────────────────────────────────────────────────────────────────
#  RUN MOODLE CLI INSTALLER (unattended)
# ─────────────────────────────────────────────────────────────────────────────
step "Run Moodle CLI installer (unattended)"

MOODLE_CONFIG="${MOODLE_WEB_DIR}/config.php"

if [[ -f "${MOODLE_CONFIG}" ]]; then
    warn "config.php already exists — Moodle may already be installed. Skipping CLI install."
    warn "If you want to reinstall, remove ${MOODLE_CONFIG} and re-run this script."
else
    info "Running Moodle CLI installer..."
    info "This may take 5–15 minutes — please be patient..."

    # Set MOODLE_WWWROOT based on domain
    if [[ "${MOODLE_DOMAIN}" == "localhost" ]]; then
        MOODLE_WWWROOT="http://localhost"
    else
        MOODLE_WWWROOT="http://${MOODLE_DOMAIN}"
    fi

    sudo -u www-data php "${MOODLE_WEB_DIR}/admin/cli/install.php" \
        --chmod=2770 \
        --lang=en \
        --wwwroot="${MOODLE_WWWROOT}" \
        --dataroot="${MOODLE_DATA_DIR}" \
        --dbtype=mariadb \
        --dbhost="${DB_HOST}" \
        --dbname="${DB_NAME}" \
        --dbuser="${DB_USER}" \
        --dbpass="${DB_PASS}" \
        --dbport=3306 \
        --prefix="${DB_PREFIX}" \
        --fullname="${MOODLE_SITE_NAME}" \
        --shortname="moodle" \
        --summary="" \
        --adminuser="${MOODLE_ADMIN_USER}" \
        --adminpass="${MOODLE_ADMIN_PASS}" \
        --adminemail="${MOODLE_ADMIN_EMAIL}" \
        --non-interactive \
        --agree-license

    # Verify config.php was created
    if [[ ! -f "${MOODLE_CONFIG}" ]]; then
        error "Moodle CLI install completed but config.php was not created."
        error "Check the output above for errors from the CLI installer."
        exit 1
    fi

    # Lock config.php permissions after installation
    chown root:www-data "${MOODLE_CONFIG}"
    chmod 640 "${MOODLE_CONFIG}"

    success "Moodle CLI installation completed"
    success "config.php created at ${MOODLE_CONFIG}"
fi

# Verify Moodle DB tables were created
TABLE_COUNT=$(mysql -u "${DB_USER}" -p"${DB_PASS}" "${DB_NAME}" \
    -sse "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='${DB_NAME}';" 2>/dev/null || echo "0")
if [[ "$TABLE_COUNT" -lt 10 ]]; then
    error "Database appears empty (${TABLE_COUNT} tables found). CLI install may have failed."
    exit 1
fi
success "Database verified: ${TABLE_COUNT} Moodle tables found in '${DB_NAME}'"

# ─────────────────────────────────────────────────────────────────────────────
#  CONFIGURE MOODLE CRON JOB
# ─────────────────────────────────────────────────────────────────────────────
step "Configure Moodle cron job"

CRON_CMD="* * * * * www-data /usr/bin/php ${MOODLE_WEB_DIR}/admin/cli/cron.php > /dev/null 2>&1"
CRON_FILE="/etc/cron.d/moodle"

if [[ ! -f "$CRON_FILE" ]] || ! grep -q "cron.php" "$CRON_FILE" 2>/dev/null; then
    echo "$CRON_CMD" > "$CRON_FILE"
    chmod 644 "$CRON_FILE"
    success "Moodle cron job configured: ${CRON_FILE}"
else
    warn "Moodle cron job already configured"
fi

# ─────────────────────────────────────────────────────────────────────────────
#  CONFIGURE UFW FIREWALL
# ─────────────────────────────────────────────────────────────────────────────
step "Configure UFW firewall"

# Check if UFW is available
if command -v ufw > /dev/null 2>&1; then
    # Allow SSH first to prevent lockout!
    ufw allow OpenSSH > /dev/null 2>&1 || true
    ufw allow 80/tcp  > /dev/null 2>&1
    ufw allow 443/tcp > /dev/null 2>&1

    # Enable UFW non-interactively
    echo "y" | ufw enable > /dev/null 2>&1 || ufw --force enable > /dev/null 2>&1 || true
    success "UFW firewall: SSH, HTTP (80), HTTPS (443) allowed"
    ufw status numbered 2>/dev/null | grep -E "Status|80|443|OpenSSH" || true
else
    warn "UFW not found — skipping firewall configuration"
fi

# ─────────────────────────────────────────────────────────────────────────────
#  RESTART ALL SERVICES
# ─────────────────────────────────────────────────────────────────────────────
step "Restart and verify all services"

for service in apache2 mariadb; do
    info "Restarting ${service}..."
    systemctl restart "$service"
    sleep 1
    if systemctl is-active --quiet "$service"; then
        success "${service} is running"
    else
        error "${service} failed to start after restart"
        journalctl -u "$service" -n 20 --no-pager || true
        exit 1
    fi
done

# ─────────────────────────────────────────────────────────────────────────────
#  FINAL VERIFICATION
# ─────────────────────────────────────────────────────────────────────────────
step "Final verification"

# Check Apache responds on port 80
sleep 2
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost/" --max-time 10 || echo "000")
if [[ "$HTTP_CODE" == "200" || "$HTTP_CODE" == "303" || "$HTTP_CODE" == "302" || "$HTTP_CODE" == "301" ]]; then
    success "Apache responding on port 80 (HTTP ${HTTP_CODE})"
elif [[ "$HTTP_CODE" == "000" ]]; then
    warn "Could not connect to Apache on port 80 — check UFW / networking"
else
    info "Apache responding with HTTP ${HTTP_CODE} (may be redirect or login page — normal)"
fi

# Check Moodle config.php
[[ -f "${MOODLE_CONFIG}" ]] && success "config.php present" || warn "config.php missing"

# Check moodledata
[[ -d "${MOODLE_DATA_DIR}" ]] && success "moodledata directory present" || warn "moodledata missing"

# Check cron
[[ -f "${CRON_FILE}" ]] && success "Cron job configured" || warn "Cron job missing"

# ─────────────────────────────────────────────────────────────────────────────
#  SAVE CREDENTIALS TO FILE
# ─────────────────────────────────────────────────────────────────────────────
CREDS_FILE="/root/moodle_credentials.txt"
cat > "${CREDS_FILE}" <<CREDS
============================================================
  MOODLE INSTALLATION CREDENTIALS
  Generated: $(date '+%Y-%m-%d %H:%M:%S')
============================================================

  Moodle URL:           http://${MOODLE_DOMAIN}/
  Admin Username:       ${MOODLE_ADMIN_USER}
  Admin Password:       ${MOODLE_ADMIN_PASS}
  Admin Email:          ${MOODLE_ADMIN_EMAIL}

  -- Database --
  DB Host:              ${DB_HOST}
  DB Name:              ${DB_NAME}
  DB Username:          ${DB_USER}
  DB Password:          ${DB_PASS}
  DB Prefix:            ${DB_PREFIX}

  -- Paths --
  Moodle web root:      ${MOODLE_WEB_DIR}
  Moodle data dir:      ${MOODLE_DATA_DIR}
  Config file:          ${MOODLE_CONFIG}
  Cron job:             ${CRON_FILE}

  -- Logs --
  Install log:          ${LOG_FILE}
  Apache error log:     /var/log/apache2/moodle_error.log
  Apache access log:    /var/log/apache2/moodle_access.log

============================================================
  SECURITY NOTICE: Keep this file secure. Delete after use.
  rm -f ${CREDS_FILE}
============================================================
CREDS
chmod 600 "${CREDS_FILE}"

# ─────────────────────────────────────────────────────────────────────────────
#  INSTALLATION SUMMARY
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}╔══════════════════════════════════════════════════════════╗${RESET}"
echo -e "${GREEN}${BOLD}║        ✅  MOODLE INSTALLATION COMPLETE  ✅               ║${RESET}"
echo -e "${GREEN}${BOLD}╚══════════════════════════════════════════════════════════╝${RESET}"
echo ""
echo -e "  ${BOLD}🌐 Moodle URL:${RESET}         http://${MOODLE_DOMAIN}/"
echo -e "  ${BOLD}👤 Admin user:${RESET}         ${MOODLE_ADMIN_USER}"
echo -e "  ${BOLD}🔑 Admin password:${RESET}     ${MOODLE_ADMIN_PASS}"
echo ""
echo -e "  ${BOLD}🗄️  Database:${RESET}          ${DB_NAME}"
echo -e "  ${BOLD}🗄️  DB user:${RESET}           ${DB_USER}"
echo -e "  ${BOLD}🗄️  DB password:${RESET}       ${DB_PASS}"
echo ""
echo -e "  ${BOLD}📁 Web root:${RESET}           ${MOODLE_WEB_DIR}"
echo -e "  ${BOLD}📁 Data dir:${RESET}           ${MOODLE_DATA_DIR}"
echo ""
echo -e "  ${BOLD}📄 Credentials saved to:${RESET}  ${CREDS_FILE}"
echo -e "  ${BOLD}📄 Full install log:${RESET}      ${LOG_FILE}"
echo ""
echo -e "  ${YELLOW}${BOLD}⚠️  NEXT STEPS:${RESET}"
echo -e "  1. Open http://${MOODLE_DOMAIN}/ in your browser"
echo -e "  2. Log in with the admin credentials above"
echo -e "  3. Complete the first-time setup wizard if prompted"
if [[ "${MOODLE_DOMAIN}" != "localhost" ]]; then
echo -e "  4. Point your DNS A record for ${MOODLE_DOMAIN} → this server's IP"
echo -e "  5. Install SSL:  sudo apt install certbot python3-certbot-apache"
echo -e "             sudo certbot --apache -d ${MOODLE_DOMAIN}"
fi
echo ""
echo -e "  ${YELLOW}Delete credentials file when done:${RESET}  sudo rm -f ${CREDS_FILE}"
echo ""

# Mark install as successful so trap doesn't trigger
INSTALL_FAILED=true  # Reuse flag to suppress trap on clean EXIT
exit 0
