import asyncio
from typing import Any
from uuid import UUID

from app.domain.models.widget_asset import WidgetAssetVariant


class S3WidgetAssetStorage:
    def __init__(
        self,
        *,
        bucket: str,
        prefix: str = "widget-assets",
        endpoint_url: str | None = None,
        region_name: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        client: Any | None = None,
    ) -> None:
        self._bucket = bucket
        self._prefix = prefix.strip("/")
        if client is None:
            import boto3

            credentials = {}
            if access_key_id and secret_access_key:
                credentials = {
                    "aws_access_key_id": access_key_id,
                    "aws_secret_access_key": secret_access_key,
                }
            client = boto3.client(
                "s3",
                endpoint_url=endpoint_url,
                region_name=region_name,
                **credentials,
            )
        self._client = client

    async def put_variants(
        self, *, asset_id: str, variants: tuple[WidgetAssetVariant, ...]
    ) -> None:
        normalized_id = str(UUID(asset_id))
        for variant in variants:
            await asyncio.to_thread(
                self._client.put_object,
                Bucket=self._bucket,
                Key=self._key(normalized_id, variant.size),
                Body=variant.content,
                ContentType=variant.content_type,
                CacheControl="public, max-age=31536000, immutable",
            )

    async def read_variant(self, *, asset_id: str, size: int) -> bytes | None:
        normalized_id = str(UUID(asset_id))
        return await asyncio.to_thread(self._read_object, normalized_id, size)

    def _read_object(self, asset_id: str, size: int) -> bytes | None:
        try:
            response = self._client.get_object(
                Bucket=self._bucket,
                Key=self._key(asset_id, size),
            )
        except Exception as exc:
            code = str(getattr(exc, "response", {}).get("Error", {}).get("Code", "")).casefold()
            if code in {"404", "nosuchkey", "notfound"}:
                return None
            raise
        body = response["Body"]
        try:
            return body.read()
        finally:
            body.close()

    def _key(self, asset_id: str, size: int) -> str:
        name = f"{asset_id}/{size}.webp"
        return f"{self._prefix}/{name}" if self._prefix else name
