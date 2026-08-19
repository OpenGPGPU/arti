from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class Port:
    name: str
    direction: str
    width: int = 1


@dataclass
class ModuleSignature:
    module_name: str
    ports: list[Port]
    clocks: list[str] = field(default_factory=list)
    resets: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)
