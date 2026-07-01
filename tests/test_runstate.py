"""Per-day run idempotency marker tests (offline)."""
import dataclasses
import datetime

from config import get_config
from src.runstate import already_ran_today, mark_ran_today


def test_marker_roundtrip_and_isolation(tmp_path):
    cfg = get_config()
    paths = dataclasses.replace(cfg.paths, state_dir=str(tmp_path))
    day = datetime.date(2026, 7, 2)

    # nothing recorded yet
    assert already_ran_today(paths, "open", day) is False

    mark_ran_today(paths, "open", day)
    assert already_ran_today(paths, "open", day) is True

    # a different market date is not yet marked (so a later slot on a new day runs)
    assert already_ran_today(paths, "open", datetime.date(2026, 7, 3)) is False

    # modes are independent: marking open does not mark midday
    assert already_ran_today(paths, "midday", day) is False
    mark_ran_today(paths, "midday", day)
    assert already_ran_today(paths, "midday", day) is True
