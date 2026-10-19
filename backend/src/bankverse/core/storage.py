"""Object storage abstraction: local filesystem (dev) or Amazon S3 with SSE-KMS (cloud).

Keys are always namespaced by organisation (``orgs/<org_id>/...``) so that a key from one tenant
can never resolve inside another tenant's prefix.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Protocol

from bankverse.core.config import get_settings


class ObjectStore(Protocol):
    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None: ...
    def get(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...
    def exists(self, key: str) -> bool: ...


def org_key(org_id: uuid.UUID | str, *parts: str) -> str:
    safe = [p.replace("..", "_").strip("/") for p in parts]
    return "/".join(["orgs", str(org_id), *safe])


def assert_key_in_org(key: str, org_id: uuid.UUID | str) -> None:
    if not key.startswith(f"orgs/{org_id}/") or ".." in key:
        raise PermissionError("Storage key outside of tenant prefix")


class LocalStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if not str(p).startswith(str(self.root)):
            raise PermissionError("Path traversal attempt")
        return p

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:  # noqa: ARG002
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(p)

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def exists(self, key: str) -> bool:
        return self._path(key).exists()


class S3Store:
    def __init__(self, bucket: str, region: str, kms_key_id: str | None) -> None:
        import boto3

        self.bucket = bucket
        self.kms_key_id = kms_key_id
        self.client = boto3.client("s3", region_name=region)

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        extra = {"ServerSideEncryption": "aws:kms"}
        if self.kms_key_id:
            extra["SSEKMSKeyId"] = self.kms_key_id
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type, **extra)

    def get(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:
            return False


_store: ObjectStore | None = None


def get_store() -> ObjectStore:
    global _store
    if _store is None:
        s = get_settings()
        if s.storage_backend == "s3":
            if not s.s3_bucket_documents:
                raise RuntimeError("BANKVERSE_S3_BUCKET_DOCUMENTS is required with storage_backend=s3")
            _store = S3Store(s.s3_bucket_documents, s.aws_region, s.kms_key_id)
        else:
            _store = LocalStore(s.storage_local_dir)
    return _store
