#!/usr/bin/env bash
# Creates a Debian LXC container on a Proxmox VE host and installs KMS Console in it.
# Run on the Proxmox host as root:
#
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/rhisijmuiden/kms/master/lxc/proxmox-create.sh)"
#   bash proxmox-create.sh --hostname kms --ip 10.0.20.15/24 --gw 10.0.20.1 --bridge vmbr0 --vlan 20
#
# Options (all optional):
#   --ctid N            container id (default: next free id)
#   --hostname NAME     (default: kms)
#   --debian N          Debian release 13 or 12 (default: 13)
#   --storage NAME      storage for the root disk (default: local-lvm)
#   --tpl-storage NAME  storage for templates (default: local)
#   --disk GB           root disk size (default: 4)
#   --cores N           (default: 1)
#   --memory MB         (default: 512)
#   --bridge NAME       (default: vmbr0)
#   --vlan TAG          VLAN tag (default: none)
#   --ip CIDR|dhcp      (default: dhcp)
#   --gw IP             gateway when using a static IP
#   --dns IP            DNS server (default: host setting)
#   --ssh-key FILE      public key for root login
#   --branch NAME       KMS Console branch to install (default: master)
#   --repo URL          KMS Console git repository
#   --console-port N    web GUI port (default: 8080)
set -euo pipefail

CTID=""; HOSTNAME_="kms"; DEBIAN=13; STORAGE="local-lvm"; TPL_STORAGE="local"; DISK=4; CORES=1; MEMORY=512
BRIDGE="vmbr0"; VLAN=""; IP="dhcp"; GW=""; DNS=""; SSH_KEY=""; BRANCH="master"
REPO="https://github.com/rhisijmuiden/kms.git"; CONSOLE_PORT=8080

while [[ $# -gt 0 ]]; do
  case "$1" in
    --ctid) CTID="$2"; shift 2;;
    --hostname) HOSTNAME_="$2"; shift 2;;
    --debian) DEBIAN="$2"; shift 2;;
    --storage) STORAGE="$2"; shift 2;;
    --tpl-storage) TPL_STORAGE="$2"; shift 2;;
    --disk) DISK="$2"; shift 2;;
    --cores) CORES="$2"; shift 2;;
    --memory) MEMORY="$2"; shift 2;;
    --bridge) BRIDGE="$2"; shift 2;;
    --vlan) VLAN="$2"; shift 2;;
    --ip) IP="$2"; shift 2;;
    --gw) GW="$2"; shift 2;;
    --dns) DNS="$2"; shift 2;;
    --ssh-key) SSH_KEY="$2"; shift 2;;
    --branch) BRANCH="$2"; shift 2;;
    --repo) REPO="$2"; shift 2;;
    --console-port) CONSOLE_PORT="$2"; shift 2;;
    -h|--help) sed -n '2,30p' "$0"; exit 0;;
    *) echo "unknown option $1" >&2; exit 2;;
  esac
done

info() { printf '\e[36m==>\e[0m %s\n' "$*"; }
die() { printf '\e[31merror:\e[0m %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "run as root on the Proxmox host"
command -v pct >/dev/null || die "pct not found: run this on a Proxmox VE host"
[[ -n "$CTID" ]] || CTID="$(pvesh get /cluster/nextid)"
pct status "$CTID" >/dev/null 2>&1 && die "container $CTID already exists"

info "Looking for the Debian $DEBIAN template"
pveam update >/dev/null || true
TPL="$(pveam list "$TPL_STORAGE" 2>/dev/null | awk '{print $1}' | grep -E "debian-${DEBIAN}-standard_.*_amd64" | sort -V | tail -n1 || true)"
if [[ -z "$TPL" ]]; then
  NAME="$(pveam available --section system | awk '{print $2}' | grep -E "^debian-${DEBIAN}-standard_.*_amd64" | sort -V | tail -n1)"
  [[ -n "$NAME" ]] || die "no Debian $DEBIAN template available"
  info "Downloading $NAME"
  pveam download "$TPL_STORAGE" "$NAME" >/dev/null
  TPL="$TPL_STORAGE:vztmpl/$NAME"
fi
info "Template $TPL"

NET="name=eth0,bridge=$BRIDGE,ip=$IP"
[[ -n "$GW" ]] && NET+=",gw=$GW"
[[ -n "$VLAN" ]] && NET+=",tag=$VLAN"
[[ "$IP" == "dhcp" ]] || [[ "$IP" == */* ]] || die "--ip needs CIDR notation, for example 10.0.20.15/24"

ARGS=(--hostname "$HOSTNAME_" --cores "$CORES" --memory "$MEMORY" --swap 256
      --rootfs "$STORAGE:$DISK" --net0 "$NET" --unprivileged 1 --features nesting=1
      --onboot 1 --ostype debian --tags "kms" --description "KMS Console - https://github.com/rhisijmuiden/kms")
[[ -n "$DNS" ]] && ARGS+=(--nameserver "$DNS")
[[ -n "$SSH_KEY" ]] && ARGS+=(--ssh-public-keys "$SSH_KEY")

info "Creating container $CTID ($HOSTNAME_)"
pct create "$CTID" "$TPL" "${ARGS[@]}" >/dev/null
pct start "$CTID"

info "Waiting for network"
for _ in $(seq 1 60); do
  if pct exec "$CTID" -- bash -c 'getent hosts github.com >/dev/null 2>&1'; then break; fi
  sleep 2
done
pct exec "$CTID" -- bash -c 'getent hosts github.com >/dev/null 2>&1' || die "container has no working network/DNS"

info "Installing KMS Console ($BRANCH)"
RAW="${REPO%.git}"; RAW="${RAW/github.com/raw.githubusercontent.com}/$BRANCH/lxc/install.sh"
pct exec "$CTID" -- bash -c "export DEBIAN_FRONTEND=noninteractive; apt-get update -qq && apt-get install -y -qq curl ca-certificates >/dev/null"
pct exec "$CTID" -- bash -c "curl -fsSL '$RAW' -o /root/kms-install.sh && KMS_REPO='$REPO' KMS_BRANCH='$BRANCH' CONSOLE_PORT='$CONSOLE_PORT' bash /root/kms-install.sh"

CT_IP="$(pct exec "$CTID" -- hostname -I | awk '{print $1}')"
echo
echo "KMS Console is ready in container $CTID"
echo "  Web GUI:     http://$CT_IP:$CONSOLE_PORT"
echo "  KMS server:  $CT_IP:1688"
echo "Open the web GUI to create your administrator account."
