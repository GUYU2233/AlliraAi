"""Run on the development server: server/.venv/bin/python scripts/export_openapi.py."""

import json
from pathlib import Path

from allira.app import create_app
from allira.config import Settings

root = Path(__file__).resolve().parents[1]
# Development-only route is intentionally present for Gemini's integration reference.
# No lifespan starts, no database is opened and no upstream request is sent.
app = create_app(Settings(_env_file=None, database_path=":memory:", allow_dev_auth=True))
target = root / "docs" / "openapi.json"
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"Exported {len(app.openapi()['paths'])} endpoint paths to {target}")
