from google.transit import gtfs_realtime_pb2

from traintracker.poller.decode import decode_feed, header_timestamp


def _sample_vp_bytes(timestamp: int = 1_700_000_000, trip_id: str = "T1") -> bytes:
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = timestamp
    entity = feed.entity.add()
    entity.id = "e1"
    entity.vehicle.trip.trip_id = trip_id
    entity.vehicle.position.latitude = -37.81
    entity.vehicle.position.longitude = 144.96
    return feed.SerializeToString()


def test_decode_feed_produces_expected_shape():
    decoded = decode_feed(_sample_vp_bytes())

    assert decoded["header"]["timestamp"] == "1700000000"  # int64 -> str in MessageToDict
    entity = decoded["entity"][0]
    assert entity["vehicle"]["trip"]["trip_id"] == "T1"
    assert entity["vehicle"]["position"]["latitude"] == -37.81


def test_header_timestamp_parses_to_int():
    decoded = decode_feed(_sample_vp_bytes(timestamp=1_700_000_123))
    assert header_timestamp(decoded) == 1_700_000_123


def test_header_timestamp_none_when_absent():
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    decoded = decode_feed(feed.SerializeToString())
    assert header_timestamp(decoded) is None
