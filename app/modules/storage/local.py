import asyncio
from pathlib import Path

from app.modules.storage.base import StorageService


class LocalDiskStorage(StorageService):
    def __init__(self, root_dir: str) -> None:
        self._root = Path(root_dir).resolve()

    def _path_for(self, key: str) -> Path:
        # Keys are always built server-side (tenant/product/job/concept
        # segments), never taken raw from user input, but resolve and
        # bounds-check anyway so a malformed key can't write outside root_dir.
        path = (self._root / key).resolve()
        if path != self._root and self._root not in path.parents:
            raise ValueError(f"storage key escapes root: {key!r}")
        return path

    async def save(self, *, key: str, data: bytes, content_type: str = "") -> str:
        path = self._path_for(key)

        def _write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

        await asyncio.to_thread(_write)
        return key

    async def read(self, key: str) -> bytes:
        return await asyncio.to_thread(self._path_for(key).read_bytes)

    async def url_for(self, key: str) -> str:
        return key

    async def delete(self, key: str) -> None:
        path = self._path_for(key)
        await asyncio.to_thread(lambda: path.unlink(missing_ok=True))
