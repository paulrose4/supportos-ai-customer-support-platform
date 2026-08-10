from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

from sync_widget_assets import sync_widget_assets

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_NAME = "company-product-support-agent"
PLUGIN_ROOT = ROOT / "wordpress-plugin" / PLUGIN_NAME
DIST_ROOT = ROOT / "dist"
ARCHIVE_PATH = DIST_ROOT / f"{PLUGIN_NAME}.zip"


def main() -> None:
    sync_widget_assets()
    if not PLUGIN_ROOT.is_dir():
        raise SystemExit(f"plugin directory not found: {PLUGIN_ROOT}")

    DIST_ROOT.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(ARCHIVE_PATH, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(PLUGIN_ROOT.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(PLUGIN_ROOT.parent))

    digest = hashlib.sha256(ARCHIVE_PATH.read_bytes()).hexdigest()
    print(f"built {ARCHIVE_PATH}")
    print(f"sha256 {digest}")


if __name__ == "__main__":
    main()
