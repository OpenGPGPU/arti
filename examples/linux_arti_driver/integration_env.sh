#!/usr/bin/env bash
# Load integration.yaml through ARTI's parser and preserve environment
# variables as command-line overrides.

arti_load_integration_config() {
    local explicit=0
    [ -z "${INTEGRATION_CONFIG:-}" ] || explicit=1
    local config_path="${INTEGRATION_CONFIG:-$SCRIPT_DIR/integration.yaml}"
    local values

    INTEGRATION_CONFIG="$config_path"
    INTEGRATION_CONFIG_EXPLICIT="$explicit"
    [ -f "$config_path" ] || return 0

    values="$(mktemp "${TMPDIR:-/tmp}/arti-integration.XXXXXX")"
    if ! PYTHONPATH="$ARTI_DIR/src" python3 -m arti.integration "$config_path" > "$values"; then
        rm -f "$values"
        return 1
    fi
    while IFS=$'\t' read -r key value; do
        case "$key" in
            ARTI_RTL_TOP|ARTI_RTL_SOURCE|ARTI_MMIO_BASE|ARTI_DT_COMPAT|ARTI_IRQ_BASE|ARTI_DISPLAY|ARTI_DISPLAY_WIDTH|ARTI_DISPLAY_HEIGHT|ARTI_DISPLAY_FORMAT|ARTI_DISPLAY_FB_OFFSET|ARTI_DISPLAY_FB_SIZE|DRIVER_KO|DRIVER_MARKER|SKIP_GENERIC_TEST|GPU_REFERENCE)
                [ -n "${!key+x}" ] || printf -v "$key" '%s' "$value"
                ;;
        esac
    done < "$values"
    rm -f "$values"
    export INTEGRATION_CONFIG INTEGRATION_CONFIG_EXPLICIT
}
