"""Export or check per-service OpenAPI without running HTTP servers."""

import argparse
import json
from pathlib import Path

from backend.api.cs.main import create_app as create_cs_app
from backend.api.platform.main import create_app as create_platform_app
from backend.api.tft.main import create_app as create_tft_app
from backend.shared.settings import Settings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("docs/openapi"))
    parser.add_argument("--check", action="store_true", help="Fail if checked-in schemas are stale")
    arguments = parser.parse_args()
    settings = Settings(environment="test", dev_auth_enabled=False, _env_file=None)
    mismatches = []
    for service, factory in (
        ("cs", create_cs_app),
        ("tft", create_tft_app),
        ("platform", create_platform_app),
    ):
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
