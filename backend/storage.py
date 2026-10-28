"""Pluggable blob storage.

AppSail local disk is ephemeral, so uploads/exports should go through an adapter.
`STORAGE_BACKEND=local` (default) writes under uploads/ and exports/.
`catalyst_datastore` and `stratus` are stubs that currently fall back to local
while logging that Catalyst Data Store / Stratus should be wired for production.
"""

from __future__ import annotations

import logging
from pathlib import Path

from backend.config import Config

LOGGER = logging.getLogger("hacksphere.storage")


class StorageAdapter:
    backend_id = "base"

    def save_bytes(self, key: str, data: bytes) -> str:
        raise NotImplementedError

    def read_bytes(self, key: str) -> bytes:
        raise NotImplementedError

    def exists(self, key: str) -> bool:
        raise NotImplementedError

    def local_path(self, key: str) -> Path | None:
        return None


class LocalDiskStorage(StorageAdapter):
    backend_id = "local"

    def __init__(self, root: Path | None = None):
        self.root = Path(root or Config.UPLOAD_DIR)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        safe = str(key).replace("\\", "/").lstrip("/")
        dest = (self.root / safe).resolve()
        if not str(dest).startswith(str(self.root.resolve())):
            raise ValueError("Invalid storage key")
        dest.parent.mkdir(parents=True, exist_ok=True)
        return dest

    def save_bytes(self, key: str, data: bytes) -> str:
        path = self._path(key)
        path.write_bytes(data)
        return str(path)

    def read_bytes(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def local_path(self, key: str) -> Path | None:
        return self._path(key)


class CatalystDataStoreStub(LocalDiskStorage):
    """Placeholder for Zoho Catalyst Data Store (table rows / file columns).

    Check: install `zcatalyst-sdk` and insert into CATALYST_DATASTORE_TABLE
    with columns (file_key, blob). Until then we persist locally.
    """

    backend_id = "catalyst_datastore"

    def __init__(self):
        super().__init__(Config.UPLOAD_DIR / "_datastore_stub")
        LOGGER.warning(
            "STORAGE_BACKEND=catalyst_datastore is stubbed; files are local and "
            "will not survive an AppSail recycle. Wire zcatalyst-sdk Data Store."
        )


class CatalystStratusStub(LocalDiskStorage):
    """Placeholder for Catalyst Stratus object storage.

    Check: create bucket STRATUS_BUCKET and put objects via the Stratus SDK.
    """

    backend_id = "stratus"

    def __init__(self):
        super().__init__(Config.UPLOAD_DIR / "_stratus_stub")
        LOGGER.warning(
            "STORAGE_BACKEND=stratus is stubbed; files are local and will not "
            "survive an AppSail recycle. Wire Catalyst Stratus for the bucket %s.",
            Config.STRATUS_BUCKET,
        )


def get_storage() -> StorageAdapter:
    kind = (Config.STORAGE_BACKEND or "local").strip().lower()
    if kind in {"catalyst_datastore", "datastore"}:
        return CatalystDataStoreStub()
    if kind in {"stratus", "catalyst_stratus"}:
        return CatalystStratusStub()
    return LocalDiskStorage()
