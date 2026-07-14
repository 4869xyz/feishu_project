"""Project-wide pytest fixtures that keep all temporary files in the project."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest


@pytest.fixture
def project_tmp_dir() -> Iterator[Path]:
    """Create a test directory locally without using the system temp folder."""

    base_dir = Path.cwd() / "tests_runtime"
    base_dir.mkdir(exist_ok=True)
    test_dir = base_dir / uuid4().hex
    test_dir.mkdir()
    try:
        yield test_dir
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)
