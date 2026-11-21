#!/usr/bin/env bash
# Builds the KMS Console Proxmox CT template: a Debian 13 root filesystem with KMS Console installed,
# packed as .tar.zst like the official Proxmox templates.
#
#   lxc/build-template.sh [--branch master] [--repo https://github.com/rhisijmuiden/kms.git] [--out dist]
#
# Run as root on Debian (GitHub Actions does this in a debian:trixie container on every release).
# Needs: debootstrap zstd ca-certificates. Each container made from the template creates its own SSH host keys,
# encryption key and administrator password at its first boot (kms-firstboot.service).
set -euo pipefail

REPO="https://github.com/rhisijmuiden/kms.git"
BRANCH="master"
OUT="dist"
SUITE="trixie"
MIRROR="http://deb.debian.org/debian"
ARCH="amd64"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo) REPO="$2"; shift 2 ;;
    --branch) BRANCH="$2"; shift 2 ;;
    --out) OUT="$2"; shift 2 ;;
    --mirror) MIRROR="$2"; shift 2 ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
done

die() { echo "error: $*" >&2; exit 1; }
[[ $EUID -eq 0 ]] || die "run as root"
command -v debootstrap >/dev/null || die "debootstrap is not installed"
command -v zstd >/dev/null || die "zstd is not installed"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOTFS="$(mktemp -d /tmp/kms-rootfs.XXXXXX)"
cleanup() {
  for m in dev/pts dev proc sys; do mountpoint -q "$ROOTFS/$m" && umount -l "$ROOTFS/$m"; done
  rm -rf "$ROOTFS"
}
trap cleanup EXIT

# Same base as the official Proxmox Debian templates: systemd, ifupdown (Proxmox writes
# /etc/network/interfaces), SSH, and a few tools for the console.
PKGS=systemd-sysv,ifupdown,dhcpcd-base,iproute2,iputils-ping,openssh-server,ca-certificates,curl,less,nano,procps,locales,tzdata
echo "==> debootstrap $SUITE ($ARCH)"
debootstrap --variant=minbase --arch="$ARCH" --include="$PKGS" "$SUITE" "$ROOTFS" "$MIRROR"

cat > "$ROOTFS/etc/apt/sources.list.d/debian.sources" <<EOF
Types: deb
URIs: http://deb.debian.org/debian
Suites: $SUITE $SUITE-updates
Components: main
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg

Types: deb
URIs: http://security.debian.org/debian-security
Suites: $SUITE-security
Components: main
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg
EOF
rm -f "$ROOTFS/etc/apt/sources.list"

# Nothing may start inside the chroot
printf '#!/bin/sh\nexit 101\n' > "$ROOTFS/usr/sbin/policy-rc.d"; chmod 0755 "$ROOTFS/usr/sbin/policy-rc.d"
cp /etc/resolv.conf "$ROOTFS/etc/resolv.conf"
mount -t proc proc "$ROOTFS/proc"
mount -t sysfs sys "$ROOTFS/sys"
mount --bind /dev "$ROOTFS/dev"
mount --bind /dev/pts "$ROOTFS/dev/pts"

in_root() { chroot "$ROOTFS" /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin HOME=/root LANG=C.UTF-8 DEBIAN_FRONTEND=noninteractive "$@"; }

echo "==> updates"
in_root apt-get update -qq
in_root apt-get full-upgrade -y -qq >/dev/null
sed -i 's/^# *en_US.UTF-8/en_US.UTF-8/; s/^# *nl_NL.UTF-8/nl_NL.UTF-8/' "$ROOTFS/etc/locale.gen"
in_root locale-gen >/dev/null

echo "==> KMS Console ($BRANCH)"
install -m 0755 "$HERE/install.sh" "$ROOTFS/root/kms-install.sh"
in_root env KMS_REPO="$REPO" KMS_BRANCH="$BRANCH" bash /root/kms-install.sh --template
VERSION="$(cat "$ROOTFS/opt/kms-console/VERSION")"

echo "==> clean up"
in_root apt-get clean
rm -rf "$ROOTFS"/var/lib/apt/lists/* "$ROOTFS"/var/cache/apt/*.bin "$ROOTFS"/root/kms-install.sh \
       "$ROOTFS"/root/.cache "$ROOTFS"/tmp/* "$ROOTFS"/var/tmp/* "$ROOTFS"/var/log/*.log "$ROOTFS"/var/log/apt/*
rm -f "$ROOTFS"/etc/ssh/ssh_host_* "$ROOTFS/usr/sbin/policy-rc.d" "$ROOTFS/var/lib/dbus/machine-id"
: > "$ROOTFS/etc/machine-id"          # systemd makes a new one at the first boot
: > "$ROOTFS/etc/resolv.conf"         # Proxmox writes hostname, hosts, resolv.conf and network settings
echo localhost > "$ROOTFS/etc/hostname"
for m in dev/pts dev proc sys; do umount -l "$ROOTFS/$m"; done

mkdir -p "$OUT"
NAME="debian-13-kms-console_${VERSION}_${ARCH}.tar.zst"
echo "==> $OUT/$NAME"
tar --numeric-owner --xattrs --xattrs-include='*' -C "$ROOTFS" -cf - . | zstd -q -19 -T0 -o "$OUT/$NAME"
(cd "$OUT" && sha256sum "$NAME" > "$NAME.sha256")
echo "$NAME" > "$OUT/name.txt"
ls -lh "$OUT/$NAME"
