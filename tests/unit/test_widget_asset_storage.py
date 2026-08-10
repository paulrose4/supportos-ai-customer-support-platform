from io import BytesIO

import pytest

from app.domain.models.widget_asset import WidgetAssetVariant
from app.integrations.object_storage import S3WidgetAssetStorage


class MissingObjectError(Exception):
    response = {"Error": {"Code": "NoSuchKey"}}


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.put_requests: list[dict[str, object]] = []

    def put_object(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
        self.put_requests.append(kwargs)
        self.objects[(str(kwargs["Bucket"]), str(kwargs["Key"]))] = bytes(kwargs["Body"])

    def get_object(self, **kwargs) -> dict[str, BytesIO]:  # type: ignore[no-untyped-def]
        key = (str(kwargs["Bucket"]), str(kwargs["Key"]))
        if key not in self.objects:
            raise MissingObjectError
        return {"Body": BytesIO(self.objects[key])}


async def test_s3_widget_asset_storage_uses_private_immutable_objects() -> None:
    client = FakeS3Client()
    storage = S3WidgetAssetStorage(
        bucket="private-assets",
        prefix="production/widget-assets",
        client=client,
    )
    asset_id = "11111111-1111-1111-1111-111111111111"

    await storage.put_variants(
        asset_id=asset_id,
        variants=(WidgetAssetVariant(size=128, content=b"webp-image"),),
    )

    assert await storage.read_variant(asset_id=asset_id, size=128) == b"webp-image"
    assert await storage.read_variant(asset_id=asset_id, size=64) is None
    assert client.put_requests[0]["Key"] == (
        "production/widget-assets/11111111-1111-1111-1111-111111111111/128.webp"
    )
    assert client.put_requests[0]["ContentType"] == "image/webp"
    assert client.put_requests[0]["CacheControl"] == ("public, max-age=31536000, immutable")
    assert "ACL" not in client.put_requests[0]


async def test_s3_widget_asset_storage_rejects_non_uuid_keys() -> None:
    storage = S3WidgetAssetStorage(bucket="private-assets", client=FakeS3Client())

    with pytest.raises(ValueError):
        await storage.read_variant(asset_id="../escape", size=128)
