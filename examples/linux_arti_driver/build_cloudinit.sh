#!/usr/bin/env bash
# Build NoCloud cidata + a separate OpenGPU modules ISO for Debian.
#
# Large .ko files are NOT base64-embedded in user-data (that made cloud-init
# appear hung for minutes under QEMU). Modules live on label=OPENGPU and are
# copied to /root by a tiny oneshot on boot.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/integration_env.sh"
. "$SCRIPT_DIR/driver_preflight.sh"
ARTI_DIR="${ARTI_DIR:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
arti_load_integration_config || { echo "FAIL: cannot load integration config"; exit 1; }
CI_DIR="$SCRIPT_DIR/cloud-init"
KO="${KO:-$SCRIPT_DIR/arti_rtl_test.ko}"
GPU_KO="${GPU_KO:-$SCRIPT_DIR/arti_gpu_probe.ko}"
DRM_KO="${DRM_KO:-$SCRIPT_DIR/arti_gpu_drm.ko}"
DRIVER_KO="${DRIVER_KO:-}"
GPU_REFERENCE="${GPU_REFERENCE:-0}"
ARTI_WORK="${ARTI_WORK:-$(arti_default_work_dir)}"
LINUX_BUILD="${LINUX_BUILD:-$ARTI_WORK/arti-linux-build}"
OUTPUT="${OUTPUT:-$ARTI_WORK/cloud-init.iso}"
MODULES_ISO="${MODULES_ISO:-$ARTI_WORK/opengpu-modules.iso}"
KERNEL_RELEASE_FILE="${KERNEL_RELEASE_FILE:-$LINUX_BUILD/include/config/kernel.release}"
if [ -z "${KERNEL_RELEASE:-}" ] && [ -f "$KERNEL_RELEASE_FILE" ]; then
    KERNEL_RELEASE="$(tr -d '\n' < "$KERNEL_RELEASE_FILE")"
fi

[ -f "$KO" ] || { echo "FAIL: $KO not found"; exit 1; }
[ "$GPU_REFERENCE" != "1" ] || {
    [ -f "$GPU_KO" ] || { echo "FAIL: reference GPU probe module not found at $GPU_KO"; exit 1; }
}
[ -z "$DRIVER_KO" ] || [ -f "$DRIVER_KO" ] || { echo "FAIL: external driver not found at $DRIVER_KO"; exit 1; }
command -v xorriso >/dev/null || { echo "FAIL: xorriso not found"; exit 1; }
command -v python3 >/dev/null || { echo "FAIL: python3 not found"; exit 1; }

echo "=== Building cloud-init + OpenGPU modules ISO ==="
echo "  cidata   : $OUTPUT"
echo "  modules  : $MODULES_ISO"
[ -z "$DRIVER_KO" ] || echo "  Driver   : $DRIVER_KO"

SUPPORT_MODULES=()
append_support() {
    local path="$1" base name
    [ -n "$path" ] && [ -f "$path" ] || return 0
    base="$(basename "$path")"
    for name in "${SUPPORT_MODULES[@]+"${SUPPORT_MODULES[@]}"}"; do
        [ "$(basename "$name")" = "$base" ] && return 0
    done
    SUPPORT_MODULES+=("$path")
    echo "  support  : $path"
}

find_ko() {
    local name="$1" path=""
    path="$(arti_driver_dependency_path "$name" 2>/dev/null || true)"
    if [ -z "$path" ] || [ ! -f "$path" ]; then
        path="$(find "$LINUX_BUILD" -type f -name "$name.ko" -print -quit 2>/dev/null || true)"
    fi
    printf '%s' "$path"
}

if [ -n "$DRIVER_KO" ]; then
    arti_driver_load_manifest || true
    for mod in backlight drm drm_kms_helper gpu-sched drm_exec drm_dma_helper \
               drm_client_lib drm_shmem_helper; do
        append_support "$(find_ko "$mod")"
    done
    if [ -n "${DRIVER_DEPS:-}" ]; then
        IFS=: read -r -a _dep_paths <<< "$DRIVER_DEPS"
        for path in "${_dep_paths[@]-}"; do
            append_support "$path"
        done
    fi
fi
if [ "$GPU_REFERENCE" = "1" ]; then
    for mod in backlight drm drm_kms_helper drm_client_lib drm_shmem_helper; do
        append_support "$(find_ko "$mod")"
    done
fi

DRM_TEST=""
for cand in \
    "${DRIVER_KO:+$(dirname "$DRIVER_KO")/opengpu_drm_test}" \
    "$ARTI_WORK/opengpu-driver/opengpu_drm_test"; do
    [ -n "$cand" ] && [ -f "$cand" ] || continue
    DRM_TEST="$cand"
    break
done
[ -z "$DRM_TEST" ] || echo "  test     : $DRM_TEST"

USERSPACE_DIR=""
for cand in \
    "${OPENGPU_USERSPACE_DIR:-}" \
    "${DRIVER_KO:+$(dirname "$DRIVER_KO")}" \
    "$ARTI_WORK/opengpu-driver"; do
    [ -n "$cand" ] && [ -d "$cand" ] || continue
    if compgen -G "$cand/opengpu_pipe_*" >/dev/null 2>&1 || \
       [ -f "$cand/opengpu_triangle_example" ]; then
        USERSPACE_DIR="$cand"
        break
    fi
done
[ -z "$USERSPACE_DIR" ] || echo "  userspace: $USERSPACE_DIR"
OPENGPU_AUTO_DISPLAY="${OPENGPU_AUTO_DISPLAY:-0}"
case "$OPENGPU_AUTO_DISPLAY" in
    0) ;;
    1|console)
        [ -n "$DRIVER_KO" ] || {
            echo "FAIL: OPENGPU_AUTO_DISPLAY needs the external driver" >&2
            exit 1
        }
        ;;
    gradient)
        [ -n "$DRIVER_KO" ] && [ -n "$USERSPACE_DIR" ] && \
            [ -x "$USERSPACE_DIR/opengpu_kms_present" ] || {
            echo "FAIL: gradient display needs the external driver and opengpu_kms_present" >&2
            exit 1
        }
        ;;
    *) echo "FAIL: OPENGPU_AUTO_DISPLAY must be 0, console, 1 or gradient" >&2; exit 1 ;;
esac

# --- Stage module files into a temp dir, then make label=OPENGPU ISO ----------
STAGE="$(mktemp -d "${TMPDIR:-/tmp}/opengpu-mods.XXXXXX")"
cleanup() { rm -rf "$STAGE"; }
trap cleanup EXIT

cp "$KO" "$STAGE/arti_rtl_test.ko"
[ "$GPU_REFERENCE" != "1" ] || cp "$GPU_KO" "$STAGE/arti_gpu_probe.ko"
[ "$GPU_REFERENCE" != "1" ] || [ ! -f "$DRM_KO" ] || cp "$DRM_KO" "$STAGE/arti_gpu_drm.ko"
[ -z "$DRIVER_KO" ] || cp "$DRIVER_KO" "$STAGE/$(basename "$DRIVER_KO")"
for path in "${SUPPORT_MODULES[@]+"${SUPPORT_MODULES[@]}"}"; do
    cp "$path" "$STAGE/$(basename "$path")"
done
[ -z "$DRM_TEST" ] || cp "$DRM_TEST" "$STAGE/opengpu_drm_test"
if [ -n "$USERSPACE_DIR" ]; then
    for name in opengpu_kms_present \
                opengpu_compute_example opengpu_triangle_example \
                opengpu_compute_shader.bin \
                opengpu_fragment_tint opengpu_fragment_tint.bin \
                opengpu_pipe_clear_draw opengpu_pipe_compute \
                opengpu_pipe_blit opengpu_pipe_strided_blit \
                opengpu_pipe_resolve opengpu_pipe_texture_draw \
                opengpu_pipe_depth_pass opengpu_pipe_msaa_draw \
                opengpu_pipe_vertex_draw; do
        path="$USERSPACE_DIR/$name"
        [ -f "$path" ] || continue
        cp "$path" "$STAGE/$name"
        echo "  staged   : $name"
    done
fi

LOAD_ORDER=()
for path in "${SUPPORT_MODULES[@]+"${SUPPORT_MODULES[@]}"}"; do
    LOAD_ORDER+=("$(basename "$path")")
done
[ -z "$DRIVER_KO" ] || LOAD_ORDER+=("$(basename "$DRIVER_KO")")

{
    echo '#!/bin/sh'
    echo 'set -eu'
    echo 'cd /root'
    echo 'already() { grep -q "^$1 " /proc/modules 2>/dev/null; }'
    echo 'load() {'
    echo '  mod="$1"'
    echo '  stem="${mod%.ko}"'
    echo '  name=$(echo "$stem" | tr "-" "_")'
    echo '  alt=$(echo "$stem" | tr "_" "-")'
    echo '  if already "$name" || already "$alt" || already "$stem"; then'
    echo '    echo "skip $mod (already loaded)"'
    echo '    return 0'
    echo '  fi'
    echo '  echo "insmod $mod"'
    echo '  /sbin/insmod "./$mod"'
    echo '}'
    for mod in "${LOAD_ORDER[@]+"${LOAD_ORDER[@]}"}"; do
        echo "load $mod"
    done
    cat <<'EOS'
echo "--- dmesg (tail) ---"
dmesg | tail -30
echo "--- /dev/dri ---"
ls -l /dev/dri 2>/dev/null || echo "(no /dev/dri yet)"
run_one() {
  bin="$1"; shift
  [ -x "$bin" ] || return 0
  echo "--- $bin ---"
  "$bin" "$@"
}
if [ "${1:-}" = "test" ] && [ -x /root/opengpu_drm_test ]; then
  echo "--- opengpu_drm_test ---"
  /root/opengpu_drm_test
fi
if [ "${1:-}" = "examples" ]; then
  CARD="${2:-/dev/dri/card0}"
  SHADER=/root/opengpu_compute_shader.bin
  TINT=/root/opengpu_fragment_tint.bin
  if [ -x /root/opengpu_fragment_tint ]; then
    run_one /root/opengpu_fragment_tint "$CARD" "$TINT"
    run_one /root/opengpu_pipe_clear_draw "$CARD" "$TINT"
    run_one /root/opengpu_pipe_resolve "$CARD"
    run_one /root/opengpu_pipe_vertex_draw "$CARD"
  else
    run_one /root/opengpu_compute_example "$CARD" "$SHADER"
    run_one /root/opengpu_triangle_example "$CARD"
    run_one /root/opengpu_pipe_clear_draw "$CARD"
    run_one /root/opengpu_pipe_compute "$CARD" "$SHADER"
    run_one /root/opengpu_pipe_blit "$CARD"
    run_one /root/opengpu_pipe_strided_blit "$CARD"
    run_one /root/opengpu_pipe_resolve "$CARD"
    run_one /root/opengpu_pipe_texture_draw "$CARD"
    run_one /root/opengpu_pipe_depth_pass "$CARD"
    run_one /root/opengpu_pipe_msaa_draw "$CARD"
    run_one /root/opengpu_pipe_vertex_draw "$CARD"
  fi
  echo "OPENGPU USERSPACE EXAMPLES PASS"
fi
EOS
} > "$STAGE/load_opengpu.sh"
chmod +x "$STAGE/load_opengpu.sh" 2>/dev/null || true
chmod +x "$STAGE"/opengpu_* 2>/dev/null || true
printf '%s\n' "${LOAD_ORDER[@]+"${LOAD_ORDER[@]}"}" > "$STAGE/arti_driver_load_order.txt"

echo "  load order: $(IFS=' -> '; echo "${LOAD_ORDER[*]-}")"
rm -f "$MODULES_ISO"
xorriso -as mkisofs -V OPENGPU -J -r -o "$MODULES_ISO" "$STAGE" 2>&1 | tail -2
echo "  wrote $MODULES_ISO ($(wc -c < "$MODULES_ISO") bytes)"

# --- Tiny cidata: password/net + oneshot that copies from OPENGPU ------------
packages_yaml=""
if [ "${CLOUDINIT_PACKAGES:-0}" = "1" ] || [ "${CLOUDINIT_PACKAGES:-0}" = "true" ]; then
    packages_yaml=$'packages:\n  - kmod\n  - build-essential\n  - git\n  - vim-tiny\n  - python3\n  - ca-certificates\n  - curl\n  - pciutils\n  - strace\n  - gdb\n'
fi
autoload_commands=""
if [ "$OPENGPU_AUTO_DISPLAY" != "0" ]; then
    autoload_commands=$'  - systemctl enable opengpu-boot-display.service\n  - systemctl restart opengpu-boot-display.service\n'
else
    autoload_commands=$'  - systemctl disable opengpu-boot-display.service || true\n'
fi

if [ "$OPENGPU_AUTO_DISPLAY" = "gradient" ]; then
    display_service=$'      Type=simple\n      ExecStartPre=/root/load_opengpu.sh\n      ExecStart=/root/opengpu_kms_present /dev/dri/card0'
else
    display_service=$'      Type=oneshot\n      RemainAfterExit=yes\n      ExecStart=/root/load_opengpu.sh'
fi

cat > "$CI_DIR/user-data" <<EOF
#cloud-config
disable_root: false
ssh_pwauth: true
chpasswd:
  expire: false
  list: |
    root:arti
    debian:arti
${packages_yaml}bootcmd:
  - sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config || true
write_files:
  - path: /usr/local/sbin/opengpu-sync-modules
    permissions: '0755'
    content: |
      #!/bin/sh
      set -eu
      mkdir -p /mnt/opengpu /root
      if mountpoint -q /mnt/opengpu; then
        umount /mnt/opengpu || true
      fi
      if [ -b /dev/disk/by-label/OPENGPU ]; then
        mount -o ro /dev/disk/by-label/OPENGPU /mnt/opengpu
      elif [ -b /dev/vdc ]; then
        mount -o ro /dev/vdc /mnt/opengpu
      elif [ -b /dev/vdb ]; then
        # Fallback when cidata/root ordering differs.
        mount -o ro -t iso9660 /dev/vdb /mnt/opengpu 2>/dev/null || \
          mount -o ro /dev/vdb /mnt/opengpu
      else
        echo "opengpu-sync: OPENGPU volume not found" >&2
        exit 0
      fi
      cp -a /mnt/opengpu/. /root/
      chmod +x /root/load_opengpu.sh /root/opengpu_* 2>/dev/null || true
      umount /mnt/opengpu || true
      echo "opengpu-sync: modules refreshed in /root"
  - path: /etc/systemd/system/opengpu-sync.service
    permissions: '0644'
    content: |
      [Unit]
      Description=Sync OpenGPU modules from OPENGPU CDROM
      After=local-fs.target
      Before=getty@tty1.service serial-getty@ttyAMA0.service

      [Service]
      Type=oneshot
      ExecStart=/usr/local/sbin/opengpu-sync-modules
      RemainAfterExit=yes

      [Install]
      WantedBy=multi-user.target
  - path: /etc/systemd/system/arti-net.service
    permissions: '0644'
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
  - path: /etc/systemd/system/opengpu-boot-display.service
    permissions: '0644'
    content: |
      [Unit]
      Description=Load OpenGPU and enable its display
      Requires=opengpu-sync.service
      After=opengpu-sync.service

      [Service]
${display_service}

      [Install]
      WantedBy=multi-user.target
runcmd:
  - sed -i 's/^#PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config
  - growpart /dev/vda 1 || true
  - resize2fs /dev/vda1 || true
  # A service enabled by an earlier boot may already be running the gradient
  # presenter. Stop it before replacing the unit with this instance's mode.
  - systemctl stop opengpu-boot-display.service || true
  - systemctl daemon-reload
  - systemctl enable arti-net.service
  - systemctl start arti-net.service
  - systemctl enable opengpu-sync.service
  - systemctl restart opengpu-sync.service
${autoload_commands}
EOF

# New instance-id so existing disks pick up the tiny oneshot (cidata is small,
# so re-running cloud-init stays fast).
python3 - <<PY
from pathlib import Path
import time
meta = Path(r"$CI_DIR") / "meta-data"
meta.write_text("instance-id: arti-dev-%d\nlocal-hostname: arti-dev\n" % int(time.time()))
print("  meta-data:", meta.read_text().strip())
PY

rm -f "$OUTPUT"
xorriso -as mkisofs -V cidata -J -r -o "$OUTPUT" \
  "$CI_DIR/meta-data" "$CI_DIR/user-data" "$CI_DIR/network-config" 2>&1 | tail -2
echo "=== cloud-init ISO built: $OUTPUT ($(wc -c < "$OUTPUT") bytes) ==="
echo "=== modules ISO built:    $MODULES_ISO ($(wc -c < "$MODULES_ISO") bytes) ==="
echo "Guest: /root/load_opengpu.sh"
if [ "$OPENGPU_AUTO_DISPLAY" != "0" ]; then
    echo "       opengpu-boot-display.service enabled at boot"
fi
echo "       /root/load_opengpu.sh test"
echo "       /root/load_opengpu.sh examples"
