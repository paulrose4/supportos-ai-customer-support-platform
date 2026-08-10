from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

from sync_widget_assets import sync_widget_assets

ROOT = Path(__file__).resolve().parents[1]
CONNECTOR_NAME = "company-product-support-agent-static-php"
CONNECTOR_ROOT = ROOT / "site-connectors" / "static-php"
DIST_ROOT = ROOT / "dist"
ARCHIVE_PATH = DIST_ROOT / f"{CONNECTOR_NAME}.zip"


def main() -> None:
    sync_widget_assets()
    if not CONNECTOR_ROOT.is_dir():
        raise SystemExit(f"connector directory not found: {CONNECTOR_ROOT}")
    DIST_ROOT.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(ARCHIVE_PATH, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(CONNECTOR_ROOT.rglob("*")):
            if path.is_file() and path.name != "config.php":
                archive.write(path, Path(CONNECTOR_NAME) / path.relative_to(CONNECTOR_ROOT))
    digest = hashlib.sha256(ARCHIVE_PATH.read_bytes()).hexdigest()
    print(f"built {ARCHIVE_PATH}")
    print(f"sha256 {digest}")


if __name__ == "__main__":
    main()
