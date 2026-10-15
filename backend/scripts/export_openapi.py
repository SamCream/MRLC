#!/usr/bin/env python3
"""Export the actual FastAPI OpenAPI document from the current source tree."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile

HERE=Path(__file__).resolve()
BACKEND=HERE.parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0,str(BACKEND))

tmp=tempfile.TemporaryDirectory(prefix="mrlc-openapi-")
root=Path(tmp.name)
os.environ.setdefault("MRLC_ADMIN_PASSWORD","openapi-export-password")
os.environ.setdefault("MRLC_ADMIN_TOKEN","openapi-export-token-that-is-long-enough")
os.environ.setdefault("MRLC_SESSION_SECRET","openapi-export-session-secret-longer-than-32-chars")
os.environ["MRLC_DB_PATH"]=str(root/"openapi.sqlite3")
os.environ["MRLC_ARTIFACT_DIR"]=str(root/"artifacts")
os.environ["MRLC_BACKUP_DIR"]=str(root/"backups")
os.environ.pop("MRLC_STATIC_DIR",None)

from app.main import app  # noqa: E402

destination=Path(sys.argv[1]) if len(sys.argv)>1 else BACKEND/"openapi.json"
destination.write_text(
    json.dumps(app.openapi(),ensure_ascii=False,indent=2,sort_keys=True)+"\n",
    encoding="utf-8",
)
print(destination)
