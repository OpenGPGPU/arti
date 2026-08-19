import argparse
import json
import sys
from pathlib import Path

from .config import load_config
from .generator import generate_project
from .inference import infer_protocol
from .parser import ParseError, parse_verilog


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="arti", description="Automatic RTL integration framework")
    sub = parser.add_subparsers(dest="command", required=True)
    inspect = sub.add_parser("inspect", help="parse RTL and infer its bus protocol")
    inspect.add_argument("rtl")
    inspect.add_argument("--top")
    inspect.add_argument("--output")
    generate = sub.add_parser("generate", help="generate a co-simulation project")
    generate.add_argument("config")
    generate.add_argument("--output", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "inspect":
            signature = parse_verilog(args.rtl, args.top)
            result = {"signature": signature.to_dict(), "inference": infer_protocol(signature)}
            rendered = json.dumps(result, indent=2) + "\n"
            if args.output:
                Path(args.output).write_text(rendered, encoding="utf-8")
            else:
                print(rendered, end="")
            return 0
        config_path = Path(args.config)
        config = load_config(config_path)
        config.source_files = [str((config_path.parent / source).resolve()) for source in config.source_files or []]
        signature = parse_verilog(config.source_files[0], config.top_module)
        inference = infer_protocol(signature)
        if config.protocol != "auto":
            inference["protocol"] = config.protocol
        if not inference["protocol"]:
            raise ValueError("protocol inference is inconclusive; set bridge.protocol explicitly")
        generate_project(config, signature, inference, args.output)
        print(f"generated {args.output} ({inference['protocol']}, confidence={inference['confidence']:.3f})")
        return 0
    except (OSError, ValueError, ParseError) as error:
        print(f"arti: error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
