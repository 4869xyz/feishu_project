"""Tests for safe cleanup of inactive local download cache files."""

from __future__ import annotations

from pathlib import Path

from services.download_cache import DownloadCacheCleaner


def test_clear_deletes_only_inactive_files_inside_cache_roots(
    project_tmp_dir: Path,
) -> None:
    """Active sources, the template, and repository placeholders are preserved."""

    inbox = project_tmp_dir / "data" / "inbox"
    archive = project_tmp_dir / "data" / "archive"
    output = project_tmp_dir / "data" / "aggregation" / "output"
    for directory in (inbox, archive, output):
        directory.mkdir(parents=True)
        (directory / ".gitkeep").write_bytes(b"")

    active_source = inbox / "active.xlsx"
    active_source.write_bytes(b"active")
    inactive_source = inbox / "nested" / "inactive.xlsx"
    inactive_source.parent.mkdir()
    inactive_source.write_bytes(b"inactive")
    archived_source = archive / "old.xlsx"
    archived_source.write_bytes(b"archive")
    generated_output = output / "2026-07" / "result.xlsx"
    generated_output.parent.mkdir()
    generated_output.write_bytes(b"result")
    protected_template = output / "template.xlsx"
    protected_template.write_bytes(b"template")

    cleaner = DownloadCacheCleaner(
        (inbox, archive, output),
        protected_paths=(protected_template,),
    )
    result = cleaner.clear(active_source_paths=(active_source,))

    assert result.deleted_files == 3
    assert result.deleted_bytes == len(b"inactivearchiveresult")
    assert result.preserved_active_files == 1
    assert result.failed_files == 0
    assert active_source.is_file()
    assert protected_template.is_file()
    assert all(
        (directory / ".gitkeep").is_file()
        for directory in (inbox, archive, output)
    )
    assert not inactive_source.exists()
    assert not archived_source.exists()
    assert not generated_output.exists()
    assert not inactive_source.parent.exists()
    assert not generated_output.parent.exists()


def test_clear_ignores_missing_cache_roots(project_tmp_dir: Path) -> None:
    """A not-yet-created cache directory is harmless."""

    cleaner = DownloadCacheCleaner((project_tmp_dir / "missing",))

    result = cleaner.clear(active_source_paths=())

    assert result.deleted_files == 0
    assert result.deleted_bytes == 0
    assert result.preserved_active_files == 0
    assert result.failed_files == 0
