"""Load the Linux/QEMU integration additions to a regular ARTI config."""

from dataclasses import dataclass
from pathlib import Path
import re
import sys

from .config import Config, load_config


@dataclass(frozen=True)
class Integration:
    config: Config
    config_path: Path
    irq_base: int = 180
    dt_compat: tuple[str, ...] = ("arti,rtl",)
    driver_ko: str = ""
    driver_deps: str = ""
    driver_manifest: str = ""
    driver_marker: str = "ARTI EXTERNAL DRIVER PASS"
    skip_generic_test: bool = False
    gpu_reference: bool = False


def _section(text: str, name: str) -> str:
    match = re.search(
        rf"^{re.escape(name)}:\s*\n((?:^[ \t]+.*(?:\n|$))*)", text, re.M
    )
    return match.group(1) if match else ""


def _scalar(section: str, key: str, default: str) -> str:
    match = re.search(rf"^[ \t]+{re.escape(key)}:\s*([^#\n]+)", section, re.M)
    return match.group(1).strip().strip("'\"") if match else default


def _boolean(value: str) -> bool:
    return value.lower() in ("1", "true", "yes", "on")


def _resolve_path(value: str, base: Path) -> str:
    path = Path(value)
    return str(path if path.is_absolute() else (base / path).resolve())


def _resolve_path_list(value: str, base: Path) -> str:
    """Resolve colon-separated driver dependency paths from the profile directory."""
    return ":".join(
        _resolve_path(item, base) if item else ""
        for item in value.split(":")
    )


def _compatibles(section: str) -> tuple[str, ...]:
    value = _scalar(section, "dt_compat", "[arti,rtl]")
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    quoted = tuple(re.findall(r"['\"]([^'\"]+)['\"]", value))
    if quoted:
        items = quoted
    else:
        items = tuple(item.strip() for item in value.split(";") if item.strip())
    if not items:
        raise ValueError("integration.dt_compat must contain at least one compatible")
    return items


def load_integration(path: str | Path) -> Integration:
    config_path = Path(path).resolve()
    text = config_path.read_text(encoding="utf-8")
    config = load_config(config_path)
    section = _section(text, "integration")
    driver_ko = _scalar(section, "driver_ko", "")
    if driver_ko:
        driver_ko = _resolve_path(driver_ko, config_path.parent)
    driver_deps = _resolve_path_list(
        _scalar(section, "driver_deps", ""), config_path.parent
    )
    driver_manifest = _scalar(section, "driver_manifest", "")
    if driver_manifest:
        driver_manifest = _resolve_path(driver_manifest, config_path.parent)
    return Integration(
        config=config,
        config_path=config_path,
        irq_base=int(_scalar(section, "irq_base", "180"), 0),
        dt_compat=_compatibles(section),
        driver_ko=driver_ko,
        driver_deps=driver_deps,
        driver_manifest=driver_manifest,
        driver_marker=_scalar(section, "driver_marker", "ARTI EXTERNAL DRIVER PASS"),
        skip_generic_test=_boolean(_scalar(section, "skip_generic_test", "false")),
        gpu_reference=_boolean(_scalar(section, "gpu_reference", "false")),
    )


def _shell_values(integration: Integration) -> dict[str, str]:
    config = integration.config
    source = ""
    if config.source_files:
        source_path = Path(config.source_files[0])
        if not source_path.is_absolute():
            source_path = integration.config_path.parent / source_path
        source = str(source_path.resolve())
    return {
        "ARTI_RTL_TOP": config.top_module or "",
        "ARTI_RTL_SOURCE": source,
        "ARTI_MMIO_BASE": hex(config.base_address),
        "ARTI_DT_COMPAT": ";".join(integration.dt_compat),
        "ARTI_IRQ_BASE": str(integration.irq_base),
        "ARTI_DISPLAY": "1" if config.display_enabled else "0",
        "ARTI_DISPLAY_WIDTH": str(config.display_width),
        "ARTI_DISPLAY_HEIGHT": str(config.display_height),
        "ARTI_DISPLAY_FORMAT": config.display_format,
        "ARTI_DISPLAY_FB_OFFSET": hex(config.display_framebuffer_offset),
        "ARTI_DISPLAY_FB_SIZE": hex(config.display_framebuffer_size),
        "DRIVER_KO": integration.driver_ko,
        "DRIVER_DEPS": integration.driver_deps,
        "DRIVER_MANIFEST": integration.driver_manifest,
        "DRIVER_MARKER": integration.driver_marker,
        "SKIP_GENERIC_TEST": "1" if integration.skip_generic_test else "0",
        "GPU_REFERENCE": "1" if integration.gpu_reference else "0",
    }


def print_shell_values(path: str | Path) -> None:
    for key, value in _shell_values(load_integration(path)).items():
        print(f"{key}\t{value}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} CONFIG", file=sys.stderr)
        raise SystemExit(2)
    try:
        print_shell_values(sys.argv[1])
    except (OSError, ValueError) as error:
        print(f"arti: error: {error}", file=sys.stderr)
        raise SystemExit(2)
