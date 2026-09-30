"""Write the pack.json JSON Schema to schemas/pack.schema.json.

Run: uv run python scripts/export_schema.py
"""

import json
from pathlib import Path

from deckwright.pack import pack_schema

OUT = Path(__file__).resolve().parents[1] / "schemas" / "pack.schema.json"

if __name__ == "__main__":
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(pack_schema(), indent=1) + "\n")
    print(f"wrote {OUT}")
