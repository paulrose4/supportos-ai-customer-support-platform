import asyncio
from pathlib import Path

from app.domain.models.widget_asset import WidgetAssetVariant


class FileSystemWidgetAssetStorage:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    async def put_variants(
        self, *, asset_id: str, variants: tuple[WidgetAssetVariant, ...]
    ) -> None:
        directory = self._asset_directory(asset_id)
        await asyncio.to_thread(self._write_variants, directory, variants)

    async def read_variant(self, *, asset_id: str, size: int) -> bytes | None:
        path = self._asset_directory(asset_id) / f"{size}.webp"
        try:
            return await asyncio.to_thread(path.read_bytes)
        except FileNotFoundError:
            return None

    @staticmethod
    def _write_variants(directory: Path, variants: tuple[WidgetAssetVariant, ...]) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        for variant in variants:
            destination = directory / f"{variant.size}.webp"
            temporary = directory / f".{variant.size}.tmp"
            temporary.write_bytes(variant.content)
            temporary.replace(destination)

    def _asset_directory(self, asset_id: str) -> Path:
        directory = (self._root / asset_id).resolve()
        if directory.parent != self._root:
            raise ValueError("invalid widget asset identifier")
        return directory
