"""Minimal S3-compatible object storage adapter."""

from typing import Any

import boto3  # type: ignore[import-untyped]
from botocore.config import Config  # type: ignore[import-untyped]
from botocore.exceptions import (  # type: ignore[import-untyped]
    BotoCoreError,
    ClientError,
)

from backend.config import MinIOSettings


class ObjectStorageError(RuntimeError):
    """Base error raised at the object storage boundary."""


class ObjectNotFoundError(ObjectStorageError):
    """Requested object does not exist in the configured bucket."""


class ObjectStore:
    """Store binary objects in the bucket selected by ``MinIOSettings``."""

    def __init__(
        self,
        settings: MinIOSettings,
        *,
        client: Any | None = None,
    ) -> None:
        """Configure the adapter without performing network operations.

        Args:
            settings: Validated S3-compatible endpoint and bucket settings.
            client: Optional caller-supplied S3 client, primarily for tests.
        """
        self._bucket = settings.bucket
        self._client = (
            client
            if client is not None
            else boto3.client(
                "s3",
                endpoint_url=str(settings.endpoint_url).rstrip("/"),
                region_name=settings.region,
                aws_access_key_id=(
                    settings.access_key.get_secret_value()
                    if settings.access_key is not None
                    else None
                ),
                aws_secret_access_key=(
                    settings.secret_key.get_secret_value()
                    if settings.secret_key is not None
                    else None
                ),
                config=Config(
                    s3={
                        "addressing_style": (
                            "path" if settings.force_path_style else "auto"
                        )
                    }
                ),
            )
        )

    def put(self, key: str, content: bytes) -> None:
        """Store ``content`` under ``key``, replacing an existing object.

        Raises:
            ObjectStorageError: If the S3 operation fails.
        """
        try:
            self._client.put_object(Bucket=self._bucket, Key=key, Body=content)
        except (BotoCoreError, ClientError) as error:
            raise ObjectStorageError(f"failed to put object {key!r}") from error

    def uri(self, key: str) -> str:
        """Return the stable S3 URI stored in application metadata."""
        return f"s3://{self._bucket}/{key}"

    def get(self, key: str) -> bytes:
        """Return object bytes stored under ``key``.

        Raises:
            ObjectNotFoundError: If ``key`` is absent.
            ObjectStorageError: If another S3 operation fails.
        """
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
            body = response["Body"]
            try:
                return body.read()
            finally:
                body.close()
        except ClientError as error:
            code = str(error.response.get("Error", {}).get("Code", ""))
            if code in {"NoSuchKey", "NotFound", "404"}:
                raise ObjectNotFoundError(f"object {key!r} does not exist") from error
            raise ObjectStorageError(f"failed to get object {key!r}") from error
        except BotoCoreError as error:
            raise ObjectStorageError(f"failed to get object {key!r}") from error

    def delete(self, key: str) -> None:
        """Delete ``key``; deleting an absent object is successful.

        Raises:
            ObjectStorageError: If the S3 operation fails.
        """
        try:
            self._client.delete_object(Bucket=self._bucket, Key=key)
        except (BotoCoreError, ClientError) as error:
            raise ObjectStorageError(f"failed to delete object {key!r}") from error
