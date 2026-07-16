"""Safely remove inactive local download and aggregation cache files."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Iterable


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CacheCleanupResult:
    """Counts reported after one best-effort cache cleanup."""

    deleted_files: int
    deleted_bytes: int
    preserved_active_files: int
    failed_files: int


class DownloadCacheCleaner:
    """Delete files only inside configured cache roots and outside protected paths."""

    def __init__(
        self,
        cache_roots: Iterable[str | Path],
        *,
        protected_paths: Iterable[str | Path] = (),
    ) -> None:
        self.cache_roots = tuple(Path(path).resolve() for path in cache_roots)
        self.protected_paths = frozenset(
            Path(path).resolve() for path in protected_paths
        )

    @staticmethod
    def _is_within(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
        except ValueError:
            return False
        return True

    def clear(self, *, active_source_paths: Iterable[str | Path]) -> CacheCleanupResult:
        """Remove inactive files and empty directories without leaving cache roots."""

        active = frozenset(Path(path).resolve() for path in active_source_paths)
        protected = active | self.protected_paths
        deleted_files = 0
        deleted_bytes = 0
        preserved_active_files = 0
        failed_files = 0

        for root in self.cache_roots:
            if not root.is_dir():
                continue
            files = sorted(
                (path for path in root.rglob("*") if path.is_file()),
                key=lambda path: str(path).casefold(),
            )
            for path in files:
                if path.name == ".gitkeep":
                    continue
                try:
                    resolved = path.resolve(strict=True)
                except OSError:
                    failed_files += 1
                    LOGGER.warning("无法解析缓存文件，已跳过：%s", path.name)
                    continue
                if not self._is_within(resolved, root):
                    failed_files += 1
                    LOGGER.warning("缓存文件越出配置目录，已跳过：%s", path.name)
                    continue
                if resolved in protected:
                    if resolved in active:
                        preserved_active_files += 1
                    continue
                try:
                    size = path.stat().st_size
                    path.unlink()
                except OSError as exc:
                    failed_files += 1
                    LOGGER.warning("删除缓存文件失败：file=%s, error=%s", path.name, exc)
                    continue
                deleted_files += 1
                deleted_bytes += size

            directories = sorted(
                (path for path in root.rglob("*") if path.is_dir()),
                key=lambda path: len(path.parts),
                reverse=True,
            )
            for directory in directories:
                try:
                    resolved = directory.resolve(strict=True)
                    if resolved != root and self._is_within(resolved, root):
                        directory.rmdir()
                except OSError:
                    continue

        return CacheCleanupResult(
            deleted_files=deleted_files,
            deleted_bytes=deleted_bytes,
            preserved_active_files=preserved_active_files,
            failed_files=failed_files,
        )
