import os
import sys
import json
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collectors.gemini.statusline import parse_reset_in_seconds, format_duration

def test_format_duration():
    assert format_duration(None) == ""
    assert format_duration(0) == "0m"
    assert format_duration(45) == "0m"
    assert format_duration(60) == "1m"
    assert format_duration(2700) == "45m"
    assert format_duration(3600) == "1h 0m"
    assert format_duration(4014) == "1h 6m"
    assert format_duration(18000) == "5h 0m"

def test_parse_reset_in_seconds():
    # Test numeric reset_in_seconds
    d1 = {"reset_in_seconds": 3600}
    assert parse_reset_in_seconds(d1) == 3600.0

    # Test reset_time as float timestamp in future
    future_ts = time.time() + 1200
    d2 = {"reset_time": future_ts}
    res = parse_reset_in_seconds(d2)
    assert res is not None and 1195 <= res <= 1205

    # Test reset_time as ISO string in future
    dt_str = "2099-01-01T12:00:00Z"
    d3 = {"reset_time": dt_str}
    res3 = parse_reset_in_seconds(d3)
    assert res3 is not None and res3 > 0

if __name__ == "__main__":
    test_format_duration()
    test_parse_reset_in_seconds()
    print("ALL STATUSLINE TESTS PASSED SUCCESSFULLY!")
