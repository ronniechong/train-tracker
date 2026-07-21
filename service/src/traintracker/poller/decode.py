"""Decode a raw GTFS-Realtime protobuf payload into the plain dict shape
`state/merge.py` and the replay fixtures already expect:
`{"header": {...}, "entity": [...]}`, field names unconverted from
snake_case (`preserving_proto_field_name=True`) to match the M1 capture
format this codebase has used from the start."""

from __future__ import annotations

from google.protobuf.json_format import MessageToDict
from google.transit import gtfs_realtime_pb2


def decode_feed(payload: bytes) -> dict:
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(payload)
    return MessageToDict(feed, preserving_proto_field_name=True)


def header_timestamp(decoded: dict) -> int | None:
    raw = decoded.get("header", {}).get("timestamp")
    return int(raw) if raw is not None else None
