import io
import zipfile

from traintracker.gtfs.stops import parse_stops, stops_from_zip_bytes

SAMPLE_STOPS_TXT = (
    "stop_id,stop_name,stop_lat,stop_lon,stop_url,location_type,parent_station,"
    "wheelchair_boarding,level_id,platform_code\n"
    '"10920","Flagstaff Station","-37.81205297","144.95562907","https://example.invalid",'
    '"","vic:rail:FGS","1","Level -3","1"\n'
)


def test_parse_stops_extracts_id_name_and_coordinates():
    stops = parse_stops(SAMPLE_STOPS_TXT)
    assert set(stops) == {"10920"}
    stop = stops["10920"]
    assert stop.name == "Flagstaff Station"
    assert stop.latitude == -37.81205297
    assert stop.longitude == 144.95562907


def test_stops_from_zip_bytes_reads_stops_txt_member():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("stops.txt", SAMPLE_STOPS_TXT)
    stops = stops_from_zip_bytes(buf.getvalue())
    assert stops["10920"].name == "Flagstaff Station"
