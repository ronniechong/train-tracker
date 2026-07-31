import io
import zipfile

from traintracker.gtfs.stop_times import parse_stop_times, stop_times_from_zip_bytes

SAMPLE_STOP_TIMES_TXT = (
    "trip_id,stop_sequence,stop_id,arrival_time,departure_time\n"
    "TRIP_1,1,PLAT_A1,08:00:00,08:00:00\n"
    "TRIP_1,2,PLAT_B1,08:10:00,\n"
)


def test_parse_stop_times_extracts_records():
    records = parse_stop_times(SAMPLE_STOP_TIMES_TXT)
    assert len(records) == 2
    first = records[0]
    assert first.trip_id == "TRIP_1"
    assert first.stop_sequence == 1
    assert first.stop_id == "PLAT_A1"
    assert first.arrival_time == "08:00:00"
    assert first.departure_time == "08:00:00"


def test_parse_stop_times_treats_blank_departure_as_none():
    records = parse_stop_times(SAMPLE_STOP_TIMES_TXT)
    last = records[1]
    assert last.arrival_time == "08:10:00"
    assert last.departure_time is None


def test_stop_times_from_zip_bytes_reads_stop_times_txt_member():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("stop_times.txt", SAMPLE_STOP_TIMES_TXT)
    records = stop_times_from_zip_bytes(buf.getvalue())
    assert len(records) == 2
    assert records[0].trip_id == "TRIP_1"


def test_fixture_stop_times_parse(sample_stop_times):
    trip_ids = {r.trip_id for r in sample_stop_times}
    assert trip_ids == {
        "WEEKDAY_TRIP_1",
        "WEEKDAY_TRIP_2",
        "WEEKEND_TRIP_1",
        "WEEKEND_TRIP_2",
    }
