import os
from abc import ABC, abstractmethod

class StorageProvider(ABC):
    @abstractmethod
    def read_file(self, file_path: str) -> str:
        """Read text content from the storage destination."""
        pass

    @abstractmethod
    def get_size(self, file_path: str) -> int:
        """Get file size in bytes."""
        pass


class LocalStorageProvider(StorageProvider):
    def __init__(self, base_dir: str = ""):
        self.base_dir = base_dir

    def _resolve_path(self, file_path: str) -> str:
        if os.path.isabs(file_path):
            return os.path.normpath(file_path)
        return os.path.normpath(os.path.join(self.base_dir, file_path))

    def read_file(self, file_path: str) -> str:
        resolved = self._resolve_path(file_path)
        if not os.path.exists(resolved):
            raise FileNotFoundError(f"File not found: {resolved}")
        with open(resolved, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()

    def get_size(self, file_path: str) -> int:
        resolved = self._resolve_path(file_path)
        if not os.path.exists(resolved):
            raise FileNotFoundError(f"File not found: {resolved}")
        return os.path.getsize(resolved)
