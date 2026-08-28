import argparse
import json
import sys
from pathlib import Path

from .parser import ParseError
from .service import generate_from_config, inspect_rtl


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
            result = inspect_rtl(args.rtl, args.top)
            rendered = json.dumps(result, indent=2) + "\n"
            if args.output:
                Path(args.output).write_text(rendered, encoding="utf-8")
            else:
                print(rendered, end="")
            return 0
        result = generate_from_config(args.config, args.output)
        print(
            f"generated {result['output']} "
            f"({result['protocol']}, confidence={result['confidence']:.3f})"
        )
        return 0
    except (OSError, ValueError, ParseError) as error:
        print(f"arti: error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
