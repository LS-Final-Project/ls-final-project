"""Export only one service's validated browser mock responses."""

import argparse
import importlib
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service", choices=("cs", "tft", "platform"), required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    examples = importlib.import_module(f"backend.api.{args.service}.examples").examples()
    body = json.dumps(examples, indent=2, ensure_ascii=False) + "\n"
    path = Path(f"frontend/src/mocks/generated/{args.service}.json")
    if args.check:
        if not path.exists() or path.read_text(encoding="utf-8") != body:
            print(f"Regenerate mock examples for {args.service}")
            return 1
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
