import re

from .model import ModuleSignature
from .rules import PROTOCOL_RULES, INTERRUPT_PATTERNS, INTERRUPT_EXCLUDE


def _canonical(name: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", name.upper())


def _find_signal(ports: list, signal: str) -> str | None:
    wanted = _canonical(signal)
    candidates = [p.name for p in ports if _canonical(p.name).endswith(wanted)]
    return min(candidates, key=len) if candidates else None


def _detect_interrupts(signature: ModuleSignature) -> list[dict]:
    """Auto-detect interrupt output ports.

    A port is an interrupt candidate if:
      - It is an output
      - Its width is 1 (or small, <= 8 bits)
      - Its canonical name matches a known interrupt pattern
      - It is not a known bus signal
    """
    exclude = {_canonical(s) for s in INTERRUPT_EXCLUDE}
    interrupts = []
    for port in signature.ports:
        if port.direction != "output":
            continue
        if port.width > 8:
            continue
        canon = _canonical(port.name)
        if canon in exclude:
            continue
        if any(canon.endswith(_canonical(pat)) for pat in INTERRUPT_PATTERNS):
            interrupts.append({"name": port.name, "width": port.width})
    return interrupts


def infer_protocol(signature: ModuleSignature) -> dict:
    candidates = []
    mappings = {}
    for protocol, rules in PROTOCOL_RULES.items():
        required = rules["required"]
        mapping = {signal: _find_signal(signature.ports, signal) for signal in required}
        hits = sum(value is not None for value in mapping.values())
        coverage = hits / len(required)
        forbidden = sum(_find_signal(signature.ports, s) is not None for s in rules.get("forbidden", []))
        score = coverage * (0.2 if forbidden else 1.0)
        candidates.append({"protocol": protocol, "score": round(score, 3), "coverage": round(coverage, 3)})
        mappings[protocol] = mapping
    candidates.sort(key=lambda item: (-item["score"], item["protocol"]))
    total = sum(item["score"] for item in candidates)
    best = candidates[0]
    confidence = best["score"] / total if total else 0.0
    protocol = best["protocol"] if best["coverage"] >= 0.8 and confidence >= 0.4 else None
    rules = PROTOCOL_RULES[best["protocol"]]
    mapping = mappings[best["protocol"]]
    for signal in rules.get("optional", []) + rules.get("clock", []) + rules.get("reset", []):
        mapping[signal] = _find_signal(signature.ports, signal)
    # Chisel emits implicit top-level `clock`/`reset` alongside protocol
    # bundle clocks. Prefer the actual design clock/reset when present; the
    # bundle aliases may be unused wires.
    names = {p.name for p in signature.ports}
    if "ACLK" in mapping and "clock" in names:
        mapping["ACLK"] = "clock"
    missing = [signal for signal in rules["required"] if not mapping[signal]]
    mapped_set = set(mapping.values())
    interrupts = _detect_interrupts(signature)
    interrupt_names = {irq["name"] for irq in interrupts}
    unknown = [p.name for p in signature.ports
               if p.name not in mapped_set and p.name not in interrupt_names]
    memory_ports = [p for p in unknown
                    if any(token in _canonical(p) for token in ("MEM", "MEMORY"))]
    return {
        "protocol": protocol,
        "confidence": round(confidence, 3),
        "candidates": candidates[:3],
        "port_mapping": {k: v for k, v in mapping.items() if v},
        "missing_required": missing,
        "unknown_ports": unknown,
        "memory_ports": memory_ports,
        "interrupts": interrupts,
    }
