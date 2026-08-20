#!/usr/bin/env bash
# Build the cloud-init ISO for the Debian dev environment.
# This script generates user-data with the embedded .ko module and
# a systemd service that configures SLIRP static networking.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CI_DIR="$SCRIPT_DIR/cloud-init"
KO="${KO:-$SCRIPT_DIR/arti_rtl_test.ko}"
OUTPUT="${OUTPUT:-/tmp/cloud-init.iso}"

[ -f "$KO" ] || { echo "FAIL: $KO not found"; exit 1; }
command -v xorriso >/dev/null || { echo "FAIL: xorriso not found"; exit 1; }
command -v python3 >/dev/null || { echo "FAIL: python3 not found"; exit 1; }

echo "=== Building cloud-init ISO ==="
echo "  .ko      : $KO"
echo "  Output   : $OUTPUT"

# Generate user-data with base64-embedded .ko + arti-net.service
export KO_PATH="$KO"
export CI_DIR="$CI_DIR"
python3 << 'PYEOF'
import base64, os

ko_path = os.environ["KO_PATH"]
with open(ko_path, "rb") as f:
    ko_b64 = base64.b64encode(f.read()).decode()
indented_b64 = "\n".join("        " + ko_b64[i:i+70] for i in range(0, len(ko_b64), 70))

user_data = f"""#cloud-config
bootcmd:
  - sed -i 's/^root:.*/root::19000:0:99999:7:::/' /etc/shadow
chpasswd:
  expire: false
  users:
    - name: root
      password: arti
    - name: debian
      password: arti
ssh_pwauth: true
write_files:
  - path: /root/arti_rtl_test.ko
    content: !!binary |
{indented_b64}
    permissions: '0644'
  - path: /etc/systemd/system/arti-net.service
    content: |
      [Unit]
      Description=ARTI static network config (SLIRP)
      After=network.target
      Wants=network.target

      [Service]
      Type=oneshot
      RemainAfterExit=yes
      ExecStart=/bin/sh -c "ip link set eth0 up && ip addr add 10.0.2.15/24 dev eth0 2>/dev/null; ip route add default via 10.0.2.2 2>/dev/null; rm -f /etc/resolv.conf; echo nameserver 10.0.2.3 > /etc/resolv.conf"

      [Install]
      WantedBy=multi-user.target
runcmd:
  - sed -i 's/^#PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config
  - growpart /dev/vda 1 || true
  - resize2fs /dev/vda1 || true
  - systemctl enable arti-net.service
  - systemctl start arti-net.service
"""
with open(os.path.join(os.environ.get("CI_DIR", "/tmp/cloud-init"), "user-data"), "w") as f:
    f.write(user_data)
print("  user-data generated")
PYEOF

xorriso -as mkisofs -V cidata -J -r -o "$OUTPUT" \
  "$CI_DIR/meta-data" "$CI_DIR/user-data" "$CI_DIR/network-config" 2>&1 | tail -2

echo "=== cloud-init ISO built: $OUTPUT ==="
