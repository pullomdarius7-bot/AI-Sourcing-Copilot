import pytest

from app.storage import LocalStorage, StorageError, StorageManager


class FlakyMinio:
    name = "minio"

    def __init__(self, fail: bool):
        self.fail, self.calls, self.objects = fail, 0, {}

    async def put(self, key, data, content_type):
        self.calls += 1
        if self.fail:
            raise StorageError("connection refused")
        self.objects[key] = data

    async def get(self, key):
        return self.objects[key]

    async def delete(self, key):
        self.objects.pop(key, None)


async def test_local_roundtrip_and_delete(tmp_path):
    store = LocalStorage(str(tmp_path))
    await store.put("org/abc", b"data", "text/plain")
    assert await store.get("org/abc") == b"data"
    await store.delete("org/abc")
    with pytest.raises(StorageError):
        await store.get("org/abc")
    await store.delete("org/abc")  # idempotent


async def test_local_rejects_path_traversal(tmp_path):
    store = LocalStorage(str(tmp_path))
    with pytest.raises(StorageError):
        await store.put("../escape", b"x", "text/plain")
    with pytest.raises(StorageError):
        await store.get("/etc/passwd")


async def test_prefers_minio_when_available(tmp_path):
    minio = FlakyMinio(fail=False)
    manager = StorageManager(LocalStorage(str(tmp_path)), minio, "auto")
    assert await manager.put("k", b"v", "text/plain") == "minio"
    assert await manager.get("minio", "k") == b"v"


async def test_falls_back_to_local_and_backs_off(tmp_path):
    minio = FlakyMinio(fail=True)
    manager = StorageManager(LocalStorage(str(tmp_path)), minio, "auto")
    assert await manager.put("k1", b"v1", "text/plain") == "local"
    assert await manager.put("k2", b"v2", "text/plain") == "local"
    assert minio.calls == 1  # second write skipped the unhealthy MinIO
    assert await manager.get("local", "k2") == b"v2"


async def test_retries_minio_after_backoff_window(tmp_path):
    minio = FlakyMinio(fail=True)
    manager = StorageManager(LocalStorage(str(tmp_path)), minio, "auto")
    manager.minio_retry_after = 0
    await manager.put("k1", b"v", "text/plain")
    minio.fail = False
    assert await manager.put("k2", b"v", "text/plain") == "minio"


async def test_minio_only_mode_does_not_fall_back(tmp_path):
    manager = StorageManager(LocalStorage(str(tmp_path)), FlakyMinio(fail=True), "minio")
    with pytest.raises(StorageError):
        await manager.put("k", b"v", "text/plain")


async def test_local_only_mode_ignores_minio(tmp_path):
    minio = FlakyMinio(fail=False)
    manager = StorageManager(LocalStorage(str(tmp_path)), minio, "local")
    assert await manager.put("k", b"v", "text/plain") == "local" and minio.calls == 0


async def test_reading_from_unavailable_backend_fails(tmp_path):
    manager = StorageManager(LocalStorage(str(tmp_path)), None, "auto")
    with pytest.raises(StorageError):
        await manager.get("minio", "k")
