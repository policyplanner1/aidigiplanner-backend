from functools import lru_cache

from app.core.config import get_settings
from app.modules.storage.base import StorageService
from app.modules.storage.local import LocalDiskStorage


@lru_cache
def get_storage_service() -> StorageService:
    settings = get_settings()
    if settings.creative_storage_backend != "local":
        raise NotImplementedError(
            f"storage backend {settings.creative_storage_backend!r} not yet implemented"
        )
    return LocalDiskStorage(settings.creative_storage_local_root)
