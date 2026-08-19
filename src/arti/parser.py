import re
from pathlib import Path

from .model import ModuleSignature, Port


class ParseError(ValueError):
    pass


def _strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//.*", "", text)


def _width(range_text: str | None, parameters: dict[str, int]) -> int:
    if not range_text:
        return 1
    expression = range_text.strip("[] ")
    hi, lo = (part.strip() for part in expression.split(":", 1))
    for name, value in parameters.items():
        hi = re.sub(rf"\b{re.escape(name)}\b", str(value), hi)
        lo = re.sub(rf"\b{re.escape(name)}\b", str(value), lo)
    if not re.fullmatch(r"[\d\s()+\-*/]+", hi + lo):
        return 1
    return abs(int(eval(hi, {"__builtins__": {}}, {})) - int(eval(lo, {"__builtins__": {}}, {}))) + 1


def parse_verilog(path: str | Path, top: str | None = None) -> ModuleSignature:
    text = _strip_comments(Path(path).read_text(encoding="utf-8"))
    modules = list(re.finditer(r"\bmodule\s+(\w+)\s*(?:#\s*\((.*?)\))?\s*\((.*?)\)\s*;", text, re.S))
    match = next((m for m in modules if top is None or m.group(1) == top), None)
    if not match:
        raise ParseError(f"module {top or '<any>'!r} not found in {path}")
    params = {
        name: int(value) for name, value in re.findall(
            r"parameter(?:\s+integer)?\s+(\w+)\s*=\s*(\d+)", match.group(2) or ""
        )
    }
    header = match.group(3)
    ports: list[Port] = []
    current_direction = None
    current_range = None
    for item in header.split(","):
        item = item.strip()
        declaration = re.match(r"(?:(input|output|inout)\b)?\s*(?:(?:wire|reg|logic)\b\s*)?(\[[^]]+\])?\s*(\w+)\s*$", item)
        if not declaration:
            continue
        direction, bit_range, name = declaration.groups()
        current_direction = direction or current_direction
        current_range = bit_range if direction else (bit_range or current_range)
        if current_direction:
            ports.append(Port(name, current_direction, _width(current_range, params)))
    if not ports:
        raise ParseError("only ANSI-style module port declarations are currently supported")
    clocks = [p.name for p in ports if p.direction == "input" and re.search(r"(^|_)\w*clk($|_)", p.name, re.I)]
    resets = [
        {"name": p.name, "polarity": "active_low" if re.search(r"(?:n|_n)$", p.name, re.I) else "active_high"}
        for p in ports if p.direction == "input" and re.search(r"(?:rst|reset)", p.name, re.I)
    ]
    return ModuleSignature(match.group(1), ports, clocks, resets)
