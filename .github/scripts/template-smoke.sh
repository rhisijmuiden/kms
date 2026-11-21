#!/usr/bin/env bash
# Boots the built CT template with systemd-nspawn and checks the first boot:
# services up, administrator password generated and shown, login works and asks for a new password.
set -euo pipefail
TPL="$1"
ROOT=/var/lib/machines/kmstest
rm -rf "$ROOT"; mkdir -p "$ROOT"
tar --numeric-owner -I zstd -xf "$TPL" -C "$ROOT"
echo "nameserver 1.1.1.1" > "$ROOT/etc/resolv.conf"

systemd-nspawn -q -D "$ROOT" -M kmstest -b > /tmp/kmstest-console.log 2>&1 &
NSPAWN=$!
stop() { machinectl poweroff kmstest 2>/dev/null || kill "$NSPAWN" 2>/dev/null || true; sleep 3; }
trap stop EXIT

for _ in $(seq 1 90); do
  curl -fsS -o /dev/null http://127.0.0.1:8080/healthz 2>/dev/null && break
  sleep 2
done
curl -fsS http://127.0.0.1:8080/healthz >/dev/null || { cat /tmp/kmstest-console.log; journalctl -M kmstest --no-pager -n 80 || true; exit 1; }
echo "web interface is up"

[[ ! -e "$ROOT/etc/kms-console/firstboot" ]] || { echo "first boot did not finish"; exit 1; }
[[ -s "$ROOT/etc/machine-id" ]] || { echo "no machine-id"; exit 1; }
ls "$ROOT"/etc/ssh/ssh_host_ed25519_key >/dev/null
cat "$ROOT/etc/issue.d/kms-console.issue"
PW="$(awk '/^Password:/ {print $2}' "$ROOT/root/kms-credentials.txt")"
[[ -n "$PW" ]] || { echo "no generated password"; exit 1; }
grep -q "$PW" "$ROOT/etc/issue.d/kms-console.issue"
systemctl -M kmstest is-active kms-server.service kms-console.service

python3 - "$PW" <<'PY'
import http.cookiejar, re, sys, urllib.parse, urllib.request
pw = sys.argv[1]
base = "http://127.0.0.1:8080"
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
def token(path):
    html = op.open(base + path).read().decode()
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)
def post(path, **data):
    data["csrf_token"] = token(path)
    return op.open(base + path, urllib.parse.urlencode(data).encode())
r = post("/login", username="admin", password=pw)
assert r.url.endswith("/password"), r.url
r = post("/password", password="smoke-test-password", password2="smoke-test-password")
assert r.url.rstrip("/") == base, r.url
assert b"First steps" in r.read()
print("login, forced password change and dashboard work")
PY
sleep 1
[[ ! -e "$ROOT/root/kms-credentials.txt" ]] || { echo "credentials file not removed after password change"; exit 1; }
! grep -q "$PW" "$ROOT/etc/issue.d/kms-console.issue" || { echo "password still on the console screen"; exit 1; }
echo "smoke test passed"
