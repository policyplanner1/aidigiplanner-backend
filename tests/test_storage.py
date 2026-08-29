from pathlib import Path

import pytest

from app.modules.storage.local import LocalDiskStorage


async def test_local_disk_storage_save_read_roundtrip(tmp_path: Path) -> None:
    storage = LocalDiskStorage(str(tmp_path))

    key = "company1/project1/campaign1/job1/0/post.png"
    await storage.save(key=key, data=b"fake-image-bytes", content_type="image/png")

    assert await storage.read(key) == b"fake-image-bytes"
    assert (tmp_path / key).exists()


async def test_local_disk_storage_delete(tmp_path: Path) -> None:
    storage = LocalDiskStorage(str(tmp_path))
    key = "company1/project1/campaign1/job1/0/post.png"
    await storage.save(key=key, data=b"data")

    await storage.delete(key)

    assert not (tmp_path / key).exists()
    # Deleting an already-missing key is a no-op, not an error.
    await storage.delete(key)


async def test_local_disk_storage_rejects_path_escape(tmp_path: Path) -> None:
    storage = LocalDiskStorage(str(tmp_path))

    with pytest.raises(ValueError):
        await storage.save(key="../escape.png", data=b"data")


async def test_local_disk_storage_url_for_returns_key(tmp_path: Path) -> None:
    storage = LocalDiskStorage(str(tmp_path))
    assert await storage.url_for("some/key.png") == "some/key.png"
