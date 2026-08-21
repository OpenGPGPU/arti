#!/usr/bin/env bash
# Build the cloud-init ISO for the Debian dev environment.
# This script generates user-data with the embedded .ko modules and
# a systemd service that configures SLIRP static networking.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/integration_env.sh"
ARTI_DIR="${ARTI_DIR:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
arti_load_integration_config || { echo "FAIL: cannot load integration config"; exit 1; }
CI_DIR="$SCRIPT_DIR/cloud-init"
KO="${KO:-$SCRIPT_DIR/arti_rtl_test.ko}"
GPU_KO="${GPU_KO:-$SCRIPT_DIR/arti_gpu_probe.ko}"
DRM_KO="${DRM_KO:-$SCRIPT_DIR/arti_gpu_drm.ko}"
DRIVER_KO="${DRIVER_KO:-}"
GPU_REFERENCE="${GPU_REFERENCE:-0}"
LINUX_BUILD="${LINUX_BUILD:-/tmp/arti-linux-build}"
OUTPUT="${OUTPUT:-/tmp/cloud-init.iso}"

[ -f "$KO" ] || { echo "FAIL: $KO not found"; exit 1; }
[ "$GPU_REFERENCE" != "1" ] || {
    [ -f "$GPU_KO" ] || { echo "FAIL: reference GPU probe module not found at $GPU_KO"; exit 1; }
}
[ -z "$DRIVER_KO" ] || [ -f "$DRIVER_KO" ] || { echo "FAIL: external driver not found at $DRIVER_KO"; exit 1; }
command -v xorriso >/dev/null || { echo "FAIL: xorriso not found"; exit 1; }
command -v python3 >/dev/null || { echo "FAIL: python3 not found"; exit 1; }

echo "=== Building cloud-init ISO ==="
echo "  .ko      : $KO"
[ "$GPU_REFERENCE" != "1" ] || echo "  GPU .ko  : $GPU_KO"
[ "$GPU_REFERENCE" != "1" ] || [ ! -f "$DRM_KO" ] || echo "  DRM .ko  : $DRM_KO"
[ -z "$DRIVER_KO" ] || echo "  Driver   : $DRIVER_KO"
echo "  Output   : $OUTPUT"

# Generate user-data with base64-embedded .ko files + arti-net.service
export KO_PATH="$KO"
export GPU_KO_PATH=""
[ "$GPU_REFERENCE" != "1" ] || export GPU_KO_PATH="$GPU_KO"
export DRM_KO_PATH=""
[ "$GPU_REFERENCE" != "1" ] || [ ! -f "$DRM_KO" ] || export DRM_KO_PATH="$DRM_KO"
export DRIVER_KO_PATH="$DRIVER_KO"
export DRM_SUPPORT_PATHS="${DRM_SUPPORT_PATHS:-}"
if [ -n "$DRM_KO_PATH" ] && [ -z "$DRM_SUPPORT_PATHS" ]; then
    for drm_module in backlight drm drm_kms_helper drm_client_lib drm_shmem_helper; do
        drm_path="$(find "$LINUX_BUILD/drivers" -name "$drm_module.ko" -print -quit 2>/dev/null || true)"
        [ -z "$drm_path" ] || DRM_SUPPORT_PATHS="${DRM_SUPPORT_PATHS:+$DRM_SUPPORT_PATHS:}$drm_path"
    done
    export DRM_SUPPORT_PATHS
fi
export CI_DIR="$CI_DIR"
python3 << 'PYEOF'
import base64, os
from pathlib import Path

def module_file(path, guest_path):
    with open(path, "rb") as f:
        ko_b64 = base64.b64encode(f.read()).decode()
    indented_b64 = "\n".join("        " + ko_b64[i:i+70] for i in range(0, len(ko_b64), 70))
    return f"""  - path: {guest_path}
    content: !!binary |
{indented_b64}
    permissions: '0644'
"""

write_files = module_file(os.environ["KO_PATH"], "/root/arti_rtl_test.ko")
gpu_ko_path = os.environ.get("GPU_KO_PATH", "")
if gpu_ko_path:
    write_files += module_file(gpu_ko_path, "/root/arti_gpu_probe.ko")
drm_ko_path = os.environ.get("DRM_KO_PATH", "")
if drm_ko_path:
    write_files += module_file(drm_ko_path, "/root/arti_gpu_drm.ko")
driver_ko_path = os.environ.get("DRIVER_KO_PATH", "")
if driver_ko_path:
    write_files += module_file(driver_ko_path, "/root/arti_driver.ko")
for support_path in filter(None, os.environ.get("DRM_SUPPORT_PATHS", "").split(":")):
    write_files += module_file(support_path, "/root/" + Path(support_path).name)

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
{write_files.rstrip()}
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
