import io
import zipfile
from pathlib import Path

import pytest

from traintracker.gtfs.snapshot import StaticSnapshot

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "gtfs_static_sample"


def _zip_fixture_dir(directory: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for txt_file in directory.glob("*.txt"):
            zf.write(txt_file, arcname=txt_file.name)
    return buf.getvalue()


@pytest.fixture
def sample_static_zip_bytes() -> bytes:
    return _zip_fixture_dir(FIXTURES_DIR)


@pytest.fixture
def sample_snapshot(sample_static_zip_bytes) -> StaticSnapshot:
    return StaticSnapshot.from_zip_bytes(sample_static_zip_bytes)
