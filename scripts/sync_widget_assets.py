from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHARED_ROOT = ROOT / "site-connectors" / "shared-widget"
TARGETS = {
    SHARED_ROOT / "widget.js": (
        ROOT / "app" / "api" / "assets" / "widget.js",
        ROOT / "wordpress-plugin" / "company-product-support-agent" / "public" / "js" / "widget.js",
        ROOT / "site-connectors" / "static-php" / "public" / "support-agent" / "widget.js",
    ),
    SHARED_ROOT / "widget.css": (
        ROOT / "app" / "api" / "assets" / "widget.css",
        ROOT
        / "wordpress-plugin"
        / "company-product-support-agent"
        / "public"
        / "css"
        / "widget.css",
        ROOT / "site-connectors" / "static-php" / "public" / "support-agent" / "widget.css",
    ),
    SHARED_ROOT / "widget-runtime.js": (
        ROOT / "app" / "api" / "assets" / "widget-runtime.js",
        ROOT
        / "wordpress-plugin"
        / "company-product-support-agent"
        / "public"
        / "js"
        / "widget-runtime.js",
        ROOT / "site-connectors" / "static-php" / "public" / "support-agent" / "widget-runtime.js",
    ),
}


def sync_widget_assets() -> None:
    for source, destinations in TARGETS.items():
        if not source.is_file():
            raise FileNotFoundError(f"shared widget asset not found: {source}")
        for destination in destinations:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)


if __name__ == "__main__":
    sync_widget_assets()
    print("synchronized shared widget assets")
