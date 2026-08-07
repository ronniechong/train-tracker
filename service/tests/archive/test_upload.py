import json
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from huggingface_hub.errors import EntryNotFoundError

from traintracker.archive.upload import (
    UploadStats,
    archived_days,
    is_day_fully_archived,
    record_empty_day,
    remote_path,
    upload_day,
)


def test_remote_path_layout():
    assert remote_path("ghost_events", date(2026, 8, 7)) == (
        "data/ghost_events/year=2026/month=08/2026-08-07.parquet"
    )


@patch("traintracker.archive.upload.HfApi")
def test_archived_days_parses_repo_listing(mock_hf_api_cls):
    mock_api = MagicMock()
    mock_api.list_repo_files.return_value = [
        "data/ghost_events/year=2026/month=08/2026-08-07.parquet",
        "data/ghost_events/year=2026/month=08/2026-08-06.parquet",
        "data/poll_gap_events/year=2026/month=08/2026-08-07.parquet",
        "README.md",  # not one of ours -- must be ignored, not crash
        "data/ghost_events/year=2026/month=08/not-a-date.parquet",
    ]
    mock_api.hf_hub_download.side_effect = EntryNotFoundError("no manifest")
    mock_hf_api_cls.return_value = mock_api

    result = archived_days("whitemanjuu/train-tracker", "fake-token")

    assert result["ghost_events"] == {date(2026, 8, 7), date(2026, 8, 6)}
    assert result["poll_gap_events"] == {date(2026, 8, 7)}
    assert "discrepancy_events" in result and result["discrepancy_events"] == set()


@patch("traintracker.archive.upload.HfApi")
def test_archived_days_merges_empty_days_manifest(mock_hf_api_cls, tmp_path):
    mock_api = MagicMock()
    mock_api.list_repo_files.return_value = [
        "data/poll_gap_events/year=2026/month=08/2026-08-06.parquet",
    ]

    def fake_download(*, filename, **kwargs):
        if filename == "data/poll_gap_events/_empty_days.json":
            manifest = tmp_path / "manifest.json"
            manifest.write_text('["2026-08-07"]')
            return str(manifest)
        raise EntryNotFoundError("no manifest")

    mock_api.hf_hub_download.side_effect = fake_download
    mock_hf_api_cls.return_value = mock_api

    result = archived_days("whitemanjuu/train-tracker", "fake-token")

    # a genuinely-empty day recorded in the manifest counts as archived,
    # even though it never got a Parquet file.
    assert result["poll_gap_events"] == {date(2026, 8, 6), date(2026, 8, 7)}


@patch("traintracker.archive.upload.HfApi")
def test_record_empty_day_merges_into_existing_manifest(mock_hf_api_cls, tmp_path):
    mock_api = MagicMock()

    def fake_download(*, filename, **kwargs):
        manifest = tmp_path / "manifest.json"
        manifest.write_text('["2026-08-06"]')
        return str(manifest)

    mock_api.hf_hub_download.side_effect = fake_download
    mock_hf_api_cls.return_value = mock_api

    record_empty_day("whitemanjuu/train-tracker", "fake-token", "poll_gap_events", date(2026, 8, 7))

    mock_api.upload_file.assert_called_once()
    kwargs = mock_api.upload_file.call_args.kwargs
    assert kwargs["path_in_repo"] == "data/poll_gap_events/_empty_days.json"
    assert json.loads(kwargs["path_or_fileobj"]) == ["2026-08-06", "2026-08-07"]


def test_is_day_fully_archived_requires_every_table():
    archived = {
        "discrepancy_events": {date(2026, 8, 7)},
        "ghost_events": {date(2026, 8, 7)},
        "poll_gap_events": {date(2026, 8, 7)},
        "trip_completion_events": {date(2026, 8, 7)},
        # delay_observation_events missing entirely
    }
    assert is_day_fully_archived(archived, date(2026, 8, 7)) is False

    archived["delay_observation_events"] = {date(2026, 8, 7)}
    assert is_day_fully_archived(archived, date(2026, 8, 7)) is True


@patch("traintracker.archive.upload.HfApi")
def test_upload_day_uploads_every_staged_table(mock_hf_api_cls, tmp_path):
    mock_api = MagicMock()
    mock_hf_api_cls.return_value = mock_api

    staged = {"ghost_events": tmp_path / "ghost.parquet", "poll_gap_events": tmp_path / "gap.parquet"}
    for path in staged.values():
        path.write_bytes(b"fake parquet bytes")

    upload_day("whitemanjuu/train-tracker", "fake-token", staged, date(2026, 8, 7))

    assert mock_api.upload_file.call_count == 2
    called_paths = {call.kwargs["path_in_repo"] for call in mock_api.upload_file.call_args_list}
    assert called_paths == {
        "data/ghost_events/year=2026/month=08/2026-08-07.parquet",
        "data/poll_gap_events/year=2026/month=08/2026-08-07.parquet",
    }


@patch("traintracker.archive.upload.time.sleep")
@patch("traintracker.archive.upload.HfApi")
def test_upload_day_retries_then_succeeds(mock_hf_api_cls, mock_sleep, tmp_path):
    mock_api = MagicMock()
    mock_api.upload_file.side_effect = [RuntimeError("network blip"), None]
    mock_hf_api_cls.return_value = mock_api

    staged = {"ghost_events": tmp_path / "ghost.parquet"}
    staged["ghost_events"].write_bytes(b"fake parquet bytes")

    upload_day("whitemanjuu/train-tracker", "fake-token", staged, date(2026, 8, 7))

    assert mock_api.upload_file.call_count == 2
    mock_sleep.assert_called_once()


@patch("traintracker.archive.upload.time.sleep")
@patch("traintracker.archive.upload.HfApi")
def test_upload_day_raises_after_exhausting_retries(mock_hf_api_cls, mock_sleep, tmp_path):
    mock_api = MagicMock()
    mock_api.upload_file.side_effect = RuntimeError("persistent failure")
    mock_hf_api_cls.return_value = mock_api

    staged = {"ghost_events": tmp_path / "ghost.parquet"}
    staged["ghost_events"].write_bytes(b"fake parquet bytes")

    with pytest.raises(RuntimeError):
        upload_day("whitemanjuu/train-tracker", "fake-token", staged, date(2026, 8, 7))
    assert mock_api.upload_file.call_count == 3


@patch("traintracker.archive.upload.time.sleep")
@patch("traintracker.archive.upload.HfApi")
def test_upload_day_stats_count_every_failed_attempt(mock_hf_api_cls, mock_sleep, tmp_path):
    mock_api = MagicMock()
    mock_api.upload_file.side_effect = [RuntimeError("blip"), None]
    mock_hf_api_cls.return_value = mock_api

    staged = {"ghost_events": tmp_path / "ghost.parquet"}
    staged["ghost_events"].write_bytes(b"fake parquet bytes")

    stats = UploadStats()
    upload_day("whitemanjuu/train-tracker", "fake-token", staged, date(2026, 8, 7), stats=stats)

    assert stats.retry_failures == 1
