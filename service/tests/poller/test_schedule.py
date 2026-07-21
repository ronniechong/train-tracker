import random
from datetime import datetime, timezone

from traintracker.poller.schedule import (
    BASE_INTERVAL_S,
    OVERNIGHT_INTERVAL_RANGE_S,
    base_interval,
    is_overnight,
)


def test_overnight_detected_in_winter_aest():
    # Melbourne is UTC+10 (AEST, standard time) in July.
    three_am_local = datetime(2026, 7, 20, 17, 0, tzinfo=timezone.utc)  # 03:00 AEST
    assert is_overnight(three_am_local) is True


def test_not_overnight_in_winter_aest_daytime():
    eight_am_local = datetime(2026, 7, 20, 22, 0, tzinfo=timezone.utc)  # 08:00 AEST
    assert is_overnight(eight_am_local) is False


def test_overnight_detected_in_summer_aedt():
    # Melbourne is UTC+11 (AEDT, daylight saving) in January.
    three_am_local = datetime(2026, 1, 20, 16, 0, tzinfo=timezone.utc)  # 03:00 AEDT
    assert is_overnight(three_am_local) is True


def test_not_overnight_in_summer_aedt_daytime():
    eight_am_local = datetime(2026, 1, 20, 21, 0, tzinfo=timezone.utc)  # 08:00 AEDT
    assert is_overnight(eight_am_local) is False


def test_boundary_hour_seven_is_not_overnight():
    seven_am_local = datetime(2026, 7, 20, 21, 0, tzinfo=timezone.utc)  # 07:00 AEST exactly
    assert is_overnight(seven_am_local) is False


def test_base_interval_daytime_is_near_ten_seconds():
    daytime = datetime(2026, 7, 20, 22, 0, tzinfo=timezone.utc)  # 08:00 AEST
    interval = base_interval(daytime, rng=random.Random(0))
    assert BASE_INTERVAL_S * 0.5 <= interval <= BASE_INTERVAL_S * 1.5


def test_base_interval_overnight_is_in_slowdown_range():
    overnight = datetime(2026, 7, 20, 17, 0, tzinfo=timezone.utc)  # 03:00 AEST
    interval = base_interval(overnight, rng=random.Random(0))
    lo, hi = OVERNIGHT_INTERVAL_RANGE_S
    assert lo <= interval <= hi
