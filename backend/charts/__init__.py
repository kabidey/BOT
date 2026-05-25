"""Phase 20 — chart generators (matplotlib PNG → /app/uploads/charts/<id>.png)."""
from pathlib import Path

UPLOAD_DIR = Path("/app/uploads/charts")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
