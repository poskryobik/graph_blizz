"""Unit coverage for the S3-compatible object storage adapter."""

from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from pydantic import SecretStr

from backend.config import MinIOSettings
from backend.storage import ObjectNotFoundError, ObjectStorageError, ObjectStore

pytestmark = pytest.mark.unit


@pytest.fixture
def client() -> MagicMock:
    """Return an isolated S3 client double."""
    return MagicMock()


@pytest.fixture
def store(client: MagicMock) -> ObjectStore:
    """Bind the adapter to the client double and configured bucket."""
    return ObjectStore(
        MinIOSettings(
            bucket="documents",
            access_key=SecretStr("access-key"),
            secret_key=SecretStr("secret-key"),
        ),
        client=client,
    )


def test_put_writes_exact_bytes_to_configured_bucket(
    store: ObjectStore, client: MagicMock
) -> None:
    store.put("workspace/source.pdf", b"document bytes")

    client.put_object.assert_called_once_with(
        Bucket="documents",
        Key="workspace/source.pdf",
        Body=b"document bytes",
    )


def test_constructor_maps_minio_settings_to_s3_client() -> None:
    settings = MinIOSettings(
        endpoint_url="http://minio:9000",
        region="eu-test-1",
        bucket="documents",
        access_key=SecretStr("access-key"),
        secret_key=SecretStr("secret-key"),
        force_path_style=True,
    )

    with patch("backend.storage.object_store.boto3.client") as create_client:
        ObjectStore(settings)

    create_client.assert_called_once()
    (service,) = create_client.call_args.args
    options = create_client.call_args.kwargs
    assert service == "s3"
    assert options["endpoint_url"] == "http://minio:9000"
    assert options["region_name"] == "eu-test-1"
    assert options["aws_access_key_id"] == "access-key"
    assert options["aws_secret_access_key"] == "secret-key"
    assert options["config"].s3["addressing_style"] == "path"


def test_get_returns_bytes_and_closes_response_body(
    store: ObjectStore, client: MagicMock
) -> None:
    body = MagicMock(wraps=BytesIO(b"persisted bytes"))
    client.get_object.return_value = {"Body": body}

    assert store.get("workspace/source.pdf") == b"persisted bytes"
    body.close.assert_called_once_with()


def test_get_maps_missing_object_to_domain_error(
    store: ObjectStore, client: MagicMock
) -> None:
    client.get_object.side_effect = _client_error("NoSuchKey", "GetObject")

    with pytest.raises(ObjectNotFoundError) as raised:
        store.get("missing.pdf")

    assert isinstance(raised.value.__cause__, ClientError)


def test_sdk_failure_is_exposed_as_storage_error(
    store: ObjectStore, client: MagicMock
) -> None:
    client.put_object.side_effect = EndpointConnectionError(
        endpoint_url="http://minio:9000"
    )

    with pytest.raises(ObjectStorageError) as raised:
        store.put("source.pdf", b"content")

    assert isinstance(raised.value.__cause__, EndpointConnectionError)


def test_delete_uses_s3_idempotent_delete_contract(
    store: ObjectStore, client: MagicMock
) -> None:
    store.delete("workspace/source.pdf")

    client.delete_object.assert_called_once_with(
        Bucket="documents", Key="workspace/source.pdf"
    )


def _client_error(code: str, operation: str) -> ClientError:
    """Build a botocore error with the response shape used by the adapter."""
    return ClientError({"Error": {"Code": code, "Message": code}}, operation)
