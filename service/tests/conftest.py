import io
import zipfile
from pathlib import Path

import pytest

from traintracker.gtfs.routes import Route, routes_from_zip_bytes
from traintracker.gtfs.snapshot import StaticSnapshot
from traintracker.gtfs.stop_times import StopTimeRecord, stop_times_from_zip_bytes
from traintracker.gtfs.stops import Stop, stops_from_zip_bytes

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


@pytest.fixture
def sample_stops(sample_static_zip_bytes) -> dict[str, Stop]:
    return stops_from_zip_bytes(sample_static_zip_bytes)


@pytest.fixture
def sample_stop_times(sample_static_zip_bytes) -> list[StopTimeRecord]:
    return stop_times_from_zip_bytes(sample_static_zip_bytes)


@pytest.fixture
def sample_routes(sample_static_zip_bytes) -> dict[str, Route]:
    return routes_from_zip_bytes(sample_static_zip_bytes)
