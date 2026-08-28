#!/usr/bin/env bash
# Load integration.yaml through ARTI's parser and preserve environment
# variables as command-line overrides.

# ARTI uses modern Python typing syntax (>=3.10).  macOS may provide a
# system `python3` that is older than the interpreter used to install ARTI, so
# select the newest available version unless the caller explicitly overrides
# it.  Keeping this in the shared environment file makes every Linux harness
# entry point use the same interpreter.
if [ -z "${ARTI_PYTHON:-}" ]; then
    for _arti_python in python3.14 python3.13 python3.12 python3.11 python3.10 python3; do
        if command -v "$_arti_python" >/dev/null 2>&1; then
            ARTI_PYTHON="$_arti_python"
            break
        fi
    done
fi
: "${ARTI_PYTHON:=python3}"
export ARTI_PYTHON

arti_load_integration_config() {
    local explicit=0
    [ -z "${INTEGRATION_CONFIG:-}" ] || explicit=1
    local config_path="${INTEGRATION_CONFIG:-$SCRIPT_DIR/integration.yaml}"
    local values

    INTEGRATION_CONFIG="$config_path"
    INTEGRATION_CONFIG_EXPLICIT="$explicit"
    [ -f "$config_path" ] || return 0

    values="$(mktemp "${TMPDIR:-/tmp}/arti-integration.XXXXXX")"
    if ! PYTHONPATH="$ARTI_DIR/src" "$ARTI_PYTHON" -m arti.integration "$config_path" > "$values"; then
        rm -f "$values"
        return 1
    fi
    while IFS=$'\t' read -r key value; do
        case "$key" in
            ARTI_RTL_TOP|ARTI_RTL_SOURCE|ARTI_MMIO_BASE|ARTI_DT_COMPAT|ARTI_IRQ_BASE|ARTI_DISPLAY|ARTI_DISPLAY_WIDTH|ARTI_DISPLAY_HEIGHT|ARTI_DISPLAY_FORMAT|ARTI_DISPLAY_FB_OFFSET|ARTI_DISPLAY_FB_SIZE|DRIVER_KO|DRIVER_DEPS|DRIVER_MANIFEST|DRIVER_MARKER|SKIP_GENERIC_TEST|GPU_REFERENCE)
                [ -n "${!key+x}" ] || printf -v "$key" '%s' "$value"
                ;;
        esac
    done < "$values"
    rm -f "$values"
    export INTEGRATION_CONFIG INTEGRATION_CONFIG_EXPLICIT
}
