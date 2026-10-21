"""Export or check per-service OpenAPI without running HTTP servers."""

import argparse
import importlib
import json
from pathlib import Path

from backend.shared.settings import Settings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("docs/openapi"))
    parser.add_argument("--check", action="store_true", help="Fail if checked-in schemas are stale")
    parser.add_argument(
        "--service", choices=("cs", "tft", "platform"), help="Generate only your service"
    )
    arguments = parser.parse_args()
    settings = Settings(environment="test", dev_auth_enabled=False, _env_file=None)
    mismatches = []
    for service in [arguments.service] if arguments.service else ("cs", "tft", "platform"):
        factory = importlib.import_module(f"backend.api.{service}.main").create_app
        output = json.dumps(factory(settings).openapi(), indent=2, sort_keys=True) + "\n"
        path = arguments.output_dir / f"{service}.json"
        if arguments.check:
            if not path.exists() or path.read_text(encoding="utf-8") != output:
                mismatches.append(str(path))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(output, encoding="utf-8")
    if mismatches:
        print("Regenerate OpenAPI: " + ", ".join(mismatches))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
