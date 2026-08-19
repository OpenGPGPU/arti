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


def load_config(path: str | Path) -> Config:
    """Read the documented YAML subset without requiring PyYAML at bootstrap."""
    text = Path(path).read_text(encoding="utf-8")
    def scalar(key: str, default=None):
        match = re.search(rf"^\s*{re.escape(key)}:\s*([^#\n]+)", text, re.M)
        return match.group(1).strip().strip("'\"") if match else default

    source_match = re.search(r"^\s*source_files:\s*\[([^]]*)\]", text, re.M)
    sources = [item.strip().strip("'\"") for item in source_match.group(1).split(",")] if source_match else []
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
    )
