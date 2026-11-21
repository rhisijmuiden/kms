# shellcheck shell=sh
# KMS Console: show how to reach the web interface when root logs in.
# Installed as /etc/profile.d/kms-console.sh by lxc/install.sh.
if [ "$(id -u)" = 0 ] && [ -t 1 ] && [ -r /etc/kms-console/console.env ]; then
  _kms_port="$(. /etc/kms-console/console.env; echo "${KMS_CONSOLE_PORT:-8080}")"
  _kms_ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  echo
  echo "  KMS Console $(cat /opt/kms-console/VERSION 2>/dev/null)"
  echo "  Web interface:  http://${_kms_ip:-<ip-address>}:${_kms_port}"
  [ -r /root/kms-credentials.txt ] && sed 's/^/  /' /root/kms-credentials.txt
  echo
  unset _kms_port _kms_ip
fi
