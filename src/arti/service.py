"""Shared application operations used by the CLI and MCP server."""

from pathlib import Path
from typing import Any

from .config import load_config
from .generator import generate_project
from .inference import infer_protocol
from .parser import parse_verilog


def inspect_rtl(rtl: str | Path, top: str | None = None) -> dict[str, Any]:
    """Parse an RTL source file and return its signature and protocol inference."""
    signature = parse_verilog(rtl, top)
    return {"signature": signature.to_dict(), "inference": infer_protocol(signature)}


def generate_from_config(config_file: str | Path, output: str | Path) -> dict[str, Any]:
    """Generate an ARTI project and return a compact generation summary."""
    config_path = Path(config_file).resolve()
    output_path = Path(output).resolve()
    config = load_config(config_path)
    config.source_files = [
        str((config_path.parent / source).resolve())
        for source in config.source_files or []
    ]
    if not config.source_files:
        raise ValueError("rtl.source_files must contain at least one source file")

    signature = parse_verilog(config.source_files[0], config.top_module)
    inference = infer_protocol(signature)
    if config.protocol != "auto":
        inference["protocol"] = config.protocol
    if not inference["protocol"]:
        raise ValueError("protocol inference is inconclusive; set bridge.protocol explicitly")

    generated = generate_project(config, signature, inference, output_path)
    files = sorted(
        str(path.relative_to(generated))
        for path in generated.rglob("*")
        if path.is_file()
    )
    return {
        "output": str(generated.resolve()),
        "protocol": inference["protocol"],
        "confidence": inference["confidence"],
        "top_module": signature.module_name,
        "files": files,
    }
