#!/usr/bin/env bash
# Shared external-driver validation for the Linux integration entry points.

arti_driver_module_vermagic() {
    local module="$1" value=""
    if command -v modinfo >/dev/null 2>&1; then
        value="$(modinfo -F vermagic "$module" 2>/dev/null || true)"
    fi
    if [ -z "$value" ]; then
        value="$(strings "$module" 2>/dev/null | sed -n 's/^vermagic=//p' | head -1 || true)"
    fi
    printf '%s\n' "$value" | awk '{print $1}'
}

arti_driver_check_module_kernel() {
    local module="$1" actual
    [ -n "${KERNEL_RELEASE:-}" ] || {
        echo "FAIL: kernel release metadata not found at ${KERNEL_RELEASE_FILE:-unknown}"
        echo "  Set LINUX_BUILD to the configured kernel build directory"
        return 1
    }
    actual="$(arti_driver_module_vermagic "$module")"
    [ -n "$actual" ] || {
        echo "FAIL: cannot read vermagic from $module"
        return 1
    }
    [ "$actual" = "$KERNEL_RELEASE" ] || {
        echo "FAIL: vermagic mismatch for $(basename "$module"): $actual != $KERNEL_RELEASE"
        echo "  Rebuild the module with build_driver.sh against $LINUX_BUILD"
        return 1
    }
}

arti_driver_load_manifest() {
    [ -n "${DRIVER_KO:-}" ] || return 0
    DRIVER_MANIFEST="${DRIVER_MANIFEST:-${DRIVER_KO%.ko}.deps}"
    [ -f "$DRIVER_MANIFEST" ] || return 0

    local manifest_release manifest_key manifest_value manifest_path
    manifest_release="$(sed -n 's/^kernel_release=//p' "$DRIVER_MANIFEST" | head -1)"
    if [ -n "$manifest_release" ] && [ -n "${KERNEL_RELEASE:-}" ] && \
       [ "$manifest_release" != "$KERNEL_RELEASE" ]; then
        echo "FAIL: driver manifest kernel release mismatch: $manifest_release != $KERNEL_RELEASE"
        echo "  Rebuild the driver with build_driver.sh against $LINUX_BUILD"
        return 1
    fi
    while IFS='=' read -r manifest_key manifest_value; do
        [ "$manifest_key" = "dependency" ] || continue
        manifest_path="${manifest_value#*:}"
        [ -f "$manifest_path" ] || continue
        DRIVER_DEPS="${DRIVER_DEPS:+$DRIVER_DEPS:}$manifest_path"
    done < "$DRIVER_MANIFEST"
}

arti_driver_dependency_path() {
    local name="$1" candidate
    local -a candidates=()
    IFS=: read -r -a candidates <<< "${DRIVER_DEPS:-}"
    for candidate in "${candidates[@]-}"; do
        [ -n "$candidate" ] || continue
        if [ "$(basename "$candidate" .ko)" = "$name" ] && [ -f "$candidate" ]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    find "$LINUX_BUILD" -type f -name "$name.ko" -print -quit 2>/dev/null
}

arti_driver_check_dependency() {
    local name="$1" path dep_line dep
    case ",${ARTI_DRIVER_CHECKED_DEPS:-}," in
        *,"$name",*) return 0 ;;
    esac
    path="$(arti_driver_dependency_path "$name" || true)"
    [ -n "$path" ] || {
        echo "FAIL: dependency $name.ko not found; set DRIVER_DEPS or provide a .deps manifest"
        return 1
    }
    arti_driver_check_module_kernel "$path" || return 1
    dep_line="$(strings "$path" | sed -n 's/^depends=//p' | head -1 || true)"
    if [ -n "$dep_line" ]; then
        local -a deps=()
        IFS=',' read -r -a deps <<< "$dep_line"
        for dep in "${deps[@]-}"; do
            dep="${dep//[[:space:]]/}"
            [ -n "$dep" ] || continue
            arti_driver_check_dependency "$dep" || return 1
        done
    fi
    ARTI_DRIVER_CHECKED_DEPS="${ARTI_DRIVER_CHECKED_DEPS:+$ARTI_DRIVER_CHECKED_DEPS,}$name"
}

arti_driver_preflight() {
    [ -n "${DRIVER_KO:-}" ] || return 0
    [ -f "$DRIVER_KO" ] || {
        echo "FAIL: external driver not found at $DRIVER_KO"
        return 1
    }
    arti_driver_load_manifest || return 1
    arti_driver_check_module_kernel "$DRIVER_KO" || return 1

    local driver_dep_line dep
    driver_dep_line="$(strings "$DRIVER_KO" | sed -n 's/^depends=//p' | head -1 || true)"
    ARTI_DRIVER_CHECKED_DEPS=""
    if [ -n "$driver_dep_line" ]; then
        local -a driver_deps=()
        IFS=',' read -r -a driver_deps <<< "$driver_dep_line"
        for dep in "${driver_deps[@]-}"; do
            dep="${dep//[[:space:]]/}"
            [ -n "$dep" ] || continue
            arti_driver_check_dependency "$dep" || return 1
        done
    fi
}
