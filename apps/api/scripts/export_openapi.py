"""Write the API's OpenAPI spec to apps/web-next/openapi.json, which the web app's types are generated from.

Usage (from apps/api): python scripts/export_openapi.py [--check]
--check exits 1 if the committed file is out of date, so CI catches an API change that the web app has not
picked up. After an update, regenerate the types with `npm run gen:api` in apps/web-next.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import create_app  # noqa: E402

OUT = Path(__file__).resolve().parents[2] / "web-next" / "openapi.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    spec = json.dumps(create_app().openapi(), indent=2, sort_keys=True) + "\n"
    if args.check:
        if not OUT.exists() or OUT.read_text() != spec:
            print(f"{OUT} is out of date: run python scripts/export_openapi.py, then npm run gen:api in apps/web-next")
            return 1
        return 0
    OUT.write_text(spec)
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
