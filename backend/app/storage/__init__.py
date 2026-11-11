"""Object storage: MinIO with automatic fallback to local disk."""
import asyncio
import io
import logging
import os
import time
from pathlib import Path
from typing import Protocol

from app.config import get_settings

logger = logging.getLogger("app.storage")
settings = get_settings()


class StorageError(Exception):
    pass


class Storage(Protocol):
    name: str

    async def put(self, key: str, data: bytes, content_type: str) -> None: ...
    async def get(self, key: str) -> bytes: ...
    async def delete(self, key: str) -> None: ...


class LocalStorage:
    name = "local"

    def __init__(self, base_dir: str) -> None:
        self.base = Path(base_dir).resolve()

    def _path(self, key: str) -> Path:
        path = (self.base / key).resolve()
        if self.base not in path.parents:
            raise StorageError("invalid storage key")
        return path

    def _put(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.{time.monotonic_ns()}.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        try:
            await asyncio.to_thread(self._put, key, data)
        except OSError as exc:
            raise StorageError(f"local write failed: {exc}") from exc

    async def get(self, key: str) -> bytes:
        try:
            return await asyncio.to_thread(self._path(key).read_bytes)
        except OSError as exc:
            raise StorageError(f"local read failed: {exc}") from exc

    async def delete(self, key: str) -> None:
        try:
            await asyncio.to_thread(self._path(key).unlink, True)
        except OSError as exc:
            raise StorageError(f"local delete failed: {exc}") from exc


class MinioStorage:
    name = "minio"

    def __init__(self, endpoint: str, access_key: str, secret_key: str, bucket: str, secure: bool):
        import urllib3
        from minio import Minio

        self.bucket = bucket
        self._ready = False
        self._client = Minio(
            endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
            http_client=urllib3.PoolManager(
                timeout=urllib3.Timeout(connect=2, read=20),
                retries=urllib3.Retry(total=1, backoff_factor=0.2),
            ),
        )

    def _ensure_bucket(self) -> None:
        if not self._ready:
            if not self._client.bucket_exists(self.bucket):
                self._client.make_bucket(self.bucket)
            self._ready = True

    def _put(self, key: str, data: bytes, content_type: str) -> None:
        self._ensure_bucket()
        self._client.put_object(
            self.bucket, key, io.BytesIO(data), length=len(data), content_type=content_type
        )

    def _get(self, key: str) -> bytes:
        response = self._client.get_object(self.bucket, key)
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        try:
            await asyncio.to_thread(self._put, key, data, content_type)
        except Exception as exc:
            raise StorageError(f"minio write failed: {exc}") from exc

    async def get(self, key: str) -> bytes:
        try:
            return await asyncio.to_thread(self._get, key)
        except Exception as exc:
            raise StorageError(f"minio read failed: {exc}") from exc

    async def delete(self, key: str) -> None:
        try:
            await asyncio.to_thread(self._client.remove_object, self.bucket, key)
        except Exception as exc:
            raise StorageError(f"minio delete failed: {exc}") from exc


class StorageManager:
    """Writes to MinIO when available, otherwise local disk. Reads use the backend recorded per file."""

    def __init__(self, local: Storage, minio: Storage | None, mode: str = "auto") -> None:
        self.local = local
        self.minio = minio
        self.mode = mode
        self.minio_retry_after = settings.minio_retry_after_seconds
        self._minio_down_until = 0.0

    def _backend(self, name: str) -> Storage:
        if name == "minio" and self.minio is not None:
            return self.minio
        if name == "local":
            return self.local
        raise StorageError(f"storage backend '{name}' is not available")

    async def put(self, key: str, data: bytes, content_type: str) -> str:
        use_minio = self.minio is not None and self.mode in ("auto", "minio")
        if use_minio and (self.mode == "minio" or time.monotonic() >= self._minio_down_until):
            try:
                await self.minio.put(key, data, content_type)  # type: ignore[union-attr]
                return "minio"
            except StorageError as exc:
                if self.mode == "minio":
                    raise
                self._minio_down_until = time.monotonic() + self.minio_retry_after
                logger.warning("MinIO unavailable, falling back to local storage: %s", exc)
        await self.local.put(key, data, content_type)
        return "local"

    async def get(self, backend: str, key: str) -> bytes:
        return await self._backend(backend).get(key)

    async def delete(self, backend: str, key: str) -> None:
        await self._backend(backend).delete(key)


_manager: StorageManager | None = None


def build_storage_manager() -> StorageManager:
    minio = None
    if settings.storage_backend != "local" and settings.minio_endpoint:
        minio = MinioStorage(
            settings.minio_endpoint, settings.minio_access_key, settings.minio_secret_key,
            settings.minio_bucket, settings.minio_secure,
        )
    return StorageManager(LocalStorage(settings.storage_local_dir), minio, settings.storage_backend)


def get_storage() -> StorageManager:
    global _manager
    if _manager is None:
        _manager = build_storage_manager()
    return _manager


def set_storage(manager: StorageManager | None) -> None:
    global _manager
    _manager = manager
