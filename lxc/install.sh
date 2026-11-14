#!/usr/bin/env bash
# KMS Console installer for Debian 12/13 (inside an LXC container, a VM or bare metal).
#
#   curl -fsSL https://raw.githubusercontent.com/rhisijmuiden/kms/master/lxc/install.sh | bash
#
# Environment overrides:
#   KMS_REPO      git repository of KMS Console   (default https://github.com/rhisijmuiden/kms.git)
#   KMS_BRANCH    branch to install                (default master)
#   PYKMS_REPO    py-kms engine repository         (default https://github.com/11notes/fork-py-kms.git)
#   PYKMS_REF     py-kms tag or branch             (default v1.0.3)
#   CONSOLE_PORT  web GUI port                     (default 8080)
#   KMS_PORT      KMS port                         (default 1688)
#
# Run again with --upgrade to refresh dependencies, services and the helper after a git update.
set -euo pipefail

KMS_REPO="${KMS_REPO:-https://github.com/rhisijmuiden/kms.git}"
KMS_BRANCH="${KMS_BRANCH:-master}"
PYKMS_REPO="${PYKMS_REPO:-https://github.com/11notes/fork-py-kms.git}"
PYKMS_REF="${PYKMS_REF:-v1.0.3}"
CONSOLE_PORT="${CONSOLE_PORT:-8080}"
KMS_PORT="${KMS_PORT:-1688}"

APP_DIR=/opt/kms-console
VENV=/opt/kms-venv
PYKMS_DIR=/opt/py-kms
DATA_DIR=/var/lib/kms
CONF_DIR=/etc/kms-console
UPGRADE=0
[[ "${1:-}" == "--upgrade" ]] && UPGRADE=1

c_ok=$'\e[32m'; c_info=$'\e[36m'; c_err=$'\e[31m'; c_off=$'\e[0m'
info() { printf '%s==>%s %s\n' "$c_info" "$c_off" "$*"; }
ok()   { printf '%s ok%s %s\n' "$c_ok" "$c_off" "$*"; }
die()  { printf '%serror:%s %s\n' "$c_err" "$c_off" "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "run as root"
[[ -r /etc/os-release ]] && . /etc/os-release
[[ "${ID:-}" == "debian" || "${ID_LIKE:-}" == *debian* ]] || die "Debian or Ubuntu is required (found ${PRETTY_NAME:-unknown})"

if [[ $UPGRADE -eq 0 ]]; then
  info "Installing system packages"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq --no-install-recommends \
    python3 python3-venv python3-pip git sudo ca-certificates curl tzdata openssh-client >/dev/null
  ok "packages"
fi

if ! id kms >/dev/null 2>&1; then
  useradd --system --home-dir "$DATA_DIR" --shell /usr/sbin/nologin kms
fi
getent group systemd-journal >/dev/null && usermod -aG systemd-journal kms

if [[ $UPGRADE -eq 0 ]]; then
  if [[ -d "$APP_DIR/.git" ]]; then
    info "Updating $APP_DIR to $KMS_BRANCH"
    git -C "$APP_DIR" fetch --quiet origin "+refs/heads/$KMS_BRANCH:refs/remotes/origin/$KMS_BRANCH"
    git -C "$APP_DIR" checkout --quiet -B "$KMS_BRANCH" "origin/$KMS_BRANCH"
  else
    info "Cloning $KMS_REPO ($KMS_BRANCH)"
    git clone --quiet --branch "$KMS_BRANCH" "$KMS_REPO" "$APP_DIR"
  fi
fi
git config --system --add safe.directory "$APP_DIR" 2>/dev/null || true
chown -R root:root "$APP_DIR"
ok "KMS Console $(cat "$APP_DIR/VERSION") ($(git -C "$APP_DIR" rev-parse --short HEAD))"

if [[ ! -d "$PYKMS_DIR/.git" ]] || [[ "$(cat "$PYKMS_DIR/PYKMS_VERSION" 2>/dev/null)" != "$PYKMS_REF" ]]; then
  info "Installing py-kms $PYKMS_REF"
  rm -rf "$PYKMS_DIR"
  git clone --quiet --depth 1 --branch "$PYKMS_REF" "$PYKMS_REPO" "$PYKMS_DIR"
  echo "$PYKMS_REF" > "$PYKMS_DIR/PYKMS_VERSION"
fi
chown -R root:root "$PYKMS_DIR"
ok "py-kms $(cat "$PYKMS_DIR/PYKMS_VERSION")"

info "Python environment"
[[ -x "$VENV/bin/python" ]] || python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -r "$APP_DIR/app/requirements.txt"
ok "venv $VENV"

install -d -o kms -g kms -m 0750 "$DATA_DIR" "$DATA_DIR/backups"
install -d -o kms -g kms -m 0750 "$CONF_DIR"

if [[ ! -f "$CONF_DIR/kms.env" ]]; then
  cat > "$CONF_DIR/kms.env" <<EOF
# Managed by KMS Console. Changes here are overwritten from the web GUI.
KMS_LISTEN=0.0.0.0
KMS_PORT=$KMS_PORT
KMS_LCID=1033
KMS_ACTIVATION_INTERVAL=120
KMS_RENEWAL_INTERVAL=10080
KMS_HWID=RANDOM
KMS_LOGLEVEL=INFO
KMS_DB=$DATA_DIR/kms.db
EOF
  chown kms:kms "$CONF_DIR/kms.env"; chmod 0640 "$CONF_DIR/kms.env"
fi
if [[ ! -f "$CONF_DIR/console.env" ]]; then
  cat > "$CONF_DIR/console.env" <<EOF
# Web GUI. Restart with: systemctl restart kms-console
KMS_CONSOLE_LISTEN=0.0.0.0
KMS_CONSOLE_PORT=$CONSOLE_PORT
# Set to 1 when the console is served over HTTPS (reverse proxy) for secure cookies
KMS_CONSOLE_HTTPS=0
EOF
  chmod 0644 "$CONF_DIR/console.env"
fi

info "Helper, sudo rule and services"
install -o root -g root -m 0755 "$APP_DIR/lxc/kms-ctl" /usr/local/sbin/kms-ctl
cat > /etc/sudoers.d/kms-console <<'EOF'
# KMS Console web GUI may control its own services and updates through this helper only.
kms ALL=(root) NOPASSWD: /usr/local/sbin/kms-ctl
EOF
chmod 0440 /etc/sudoers.d/kms-console
visudo -cf /etc/sudoers.d/kms-console >/dev/null || die "sudoers rule invalid"

install -o root -g root -m 0644 "$APP_DIR"/lxc/systemd/*.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --quiet kms-server.service kms-console.service
systemctl restart kms-server.service
systemctl restart kms-console.service
ok "services kms-server and kms-console"

sleep 2
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
systemctl is-active --quiet kms-server && ok "KMS server listening on ${IP:-<ip>}:$(. "$CONF_DIR/kms.env"; echo "$KMS_PORT")" || echo "warning: kms-server is not running, see: journalctl -u kms-server"
systemctl is-active --quiet kms-console && ok "Web GUI: http://${IP:-<ip>}:$(. "$CONF_DIR/console.env"; echo "$KMS_CONSOLE_PORT")" || echo "warning: kms-console is not running, see: journalctl -u kms-console"
[[ $UPGRADE -eq 0 ]] && echo "Open the web GUI to create the administrator account."
exit 0
