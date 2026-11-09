"""Write the API's OpenAPI schema for the web client: python -m scripts.export_openapi"""

import json
from pathlib import Path

from apps.api.main import app

OUTPUT = Path("apps/web/openapi.json")


def main() -> None:
    OUTPUT.write_bytes((json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n").encode())
    print(f"Wrote {OUTPUT}")


if __name__ == "__main__":
    main()
