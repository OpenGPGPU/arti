from dataclasses import dataclass
from pathlib import Path
import re


@dataclass
class Config:
    project_name: str = "rtl_cosim"
    top_module: str | None = None
    source_files: list[str] | None = None
    protocol: str = "auto"
    base_address: int = 0xA0000000
    data_width: int = 32
    clk_freq_mhz: int = 100
    timeout_cycles: int = 1000
    mode: str = "local"
    socket_path: str = "/tmp/arti-qemu.sock"
    device_model: str = "sysbus"
    mmio_size: int = 0x1000
    sync_quantum_ns: int = 100000
    qemu_machine: str = "virt"
    icount: int = 1
    display_enabled: bool = False
    display_width: int = 1024
    display_height: int = 768
    display_format: str = "a8r8g8b8"
    display_source: str = "mmio-vram"
    display_framebuffer_offset: int = 0x100000
    display_framebuffer_size: int = 0x800000
    display_address_register: int = 0x18
    display_stride_register: int = 0x20


def load_config(path: str | Path) -> Config:
    """Read the documented YAML subset without requiring PyYAML at bootstrap."""
    text = Path(path).read_text(encoding="utf-8")
    def scalar(key: str, default=None):
        match = re.search(rf"^\s*{re.escape(key)}:\s*([^#\n]+)", text, re.M)
        return match.group(1).strip().strip("'\"") if match else default

    source_match = re.search(r"^\s*source_files:\s*\[([^]]*)\]", text, re.M)
    sources = [item.strip().strip("'\"") for item in source_match.group(1).split(",")] if source_match else []
    display_match = re.search(r"^\s*display:\s*\n((?:\s+.*\n)*)", text, re.M)
    display_text = display_match.group(1) if display_match else ""
    def display_scalar(key: str, default=None):
        match = re.search(rf"^\s*{re.escape(key)}:\s*([^#\n]+)", display_text, re.M)
        return match.group(1).strip().strip("'\"") if match else default
    return Config(
        project_name=scalar("name", "rtl_cosim"),
        top_module=scalar("top_module"), source_files=sources,
        protocol=scalar("protocol", "auto"),
        base_address=int(scalar("base_address", "0xA0000000").replace("_", ""), 0),
        data_width=int(scalar("data_width", "32")),
        clk_freq_mhz=int(scalar("clk_freq_mhz", "100")),
        timeout_cycles=int(scalar("timeout_cycles", "1000")),
        mode=scalar("mode", "local"),
        socket_path=scalar("socket_path", "/tmp/arti-qemu.sock"),
        device_model=scalar("device_model", "sysbus"),
        mmio_size=int(scalar("mmio_size", "0x1000"), 0),
        sync_quantum_ns=int(scalar("sync_quantum_ns", "100000")),
        qemu_machine=scalar("qemu_machine", "virt"),
        icount=int(scalar("icount", "1")),
        display_enabled=display_scalar("enabled", "false").lower() in ("1", "true", "yes", "on"),
        display_width=int(display_scalar("width", "1024")),
        display_height=int(display_scalar("height", "768")),
        display_format=display_scalar("format", "a8r8g8b8"),
        display_source=display_scalar("source", "mmio-vram"),
        display_framebuffer_offset=int(display_scalar("framebuffer_offset", "0x100000").replace("_", ""), 0),
        display_framebuffer_size=int(display_scalar("framebuffer_size", "0x800000").replace("_", ""), 0),
        display_address_register=int(display_scalar("address_register", "0x18").replace("_", ""), 0),
        display_stride_register=int(display_scalar("stride_register", "0x20").replace("_", ""), 0),
    )
