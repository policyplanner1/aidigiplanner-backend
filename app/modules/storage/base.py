from abc import ABC, abstractmethod


class StorageService(ABC):
    """Provider-agnostic boundary for creative asset persistence.

    LocalDiskStorage is the only implementation today; S3Storage (or
    another object-storage backend) can be added later and selected by
    app.modules.storage.provider.get_storage_service() based on config.
    Callers should depend on this interface, never a concrete
    implementation, so that swap needs no change at the call sites.
    """

    @abstractmethod
    async def save(self, *, key: str, data: bytes, content_type: str = "") -> str:
        """Persists `data` under `key`, returning the key (or provider id)
        callers should store to read it back later."""
        ...

    @abstractmethod
    async def read(self, key: str) -> bytes: ...

    @abstractmethod
    async def url_for(self, key: str) -> str:
        """A URL a client can use to fetch this asset. Local disk has no
        public URL of its own, so implementations may return something a
        caller must still resolve against the backend's own download route."""
        ...

    @abstractmethod
    async def delete(self, key: str) -> None: ...
