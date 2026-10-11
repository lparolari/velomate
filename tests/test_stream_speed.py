"""Tests for GPS-derived speed in ingestor/db.py (fill_missing_speed).

GPS-only streams (e.g. Ride with GPS phone recordings: position + elevation,
no per-point speed) used to integrate to a 0 m ride, so climb detection never
found anything and the speed panels stayed empty.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault("psycopg2", MagicMock())
sys.modules.setdefault("psycopg2.extras", MagicMock())

_ingestor_dir = Path(__file__).resolve().parent.parent / "ingestor"
if str(_ingestor_dir) not in sys.path:
    sys.path.insert(0, str(_ingestor_dir))

import db as ingestor_db
from climbs import detect_climbs, smooth_altitude

# ~11.1 m per 0.0001° of latitude
LAT_STEP_M = ingestor_db._haversine_m(46.0, 10.0, 46.0001, 10.0)


def _gps_only(n, dt=2, alt=lambda i: 1000.0):
    """Northbound track, one fix every `dt` s, no speed field."""
    return [{"time_offset": i * dt, "lat": 46.0 + i * 0.0001, "lng": 10.0,
             "altitude_m": alt(i), "speed_kmh": None} for i in range(n)]


def _integrated_m(streams):
    """Distance as climb detection computes it: speed × Δt, None → 0."""
    total = 0.0
    for prev, pt in zip(streams, streams[1:]):
        total += (pt["speed_kmh"] or 0) / 3.6 * (pt["time_offset"] - prev["time_offset"])
    return total


class TestFillMissingSpeed:
    def test_derives_speed_from_consecutive_fixes(self):
        streams = ingestor_db.fill_missing_speed(_gps_only(3))
        assert streams[0]["speed_kmh"] is None  # no previous fix
        assert streams[1]["speed_kmh"] == pytest.approx(LAT_STEP_M / 2 * 3.6, abs=0.01)

    def test_integrated_distance_matches_track_length(self):
        streams = ingestor_db.fill_missing_speed(_gps_only(500))
        assert _integrated_m(streams) == pytest.approx(499 * LAT_STEP_M, rel=1e-3)

    def test_recorded_speed_is_never_overwritten(self):
        streams = _gps_only(3)
        streams[1]["speed_kmh"] = 33.3
        ingestor_db.fill_missing_speed(streams)
        assert streams[1]["speed_kmh"] == 33.3
        assert streams[2]["speed_kmh"] is not None

    def test_gap_in_gps_bridges_from_last_fix(self):
        streams = _gps_only(4)
        streams[2]["lat"] = streams[2]["lng"] = None
        ingestor_db.fill_missing_speed(streams)
        assert streams[2]["speed_kmh"] is None
        # point 3 measures from point 1 over 4 s
        assert streams[3]["speed_kmh"] == pytest.approx(
            ingestor_db._haversine_m(46.0001, 10.0, 46.0003, 10.0) / 4 * 3.6, abs=0.01)

    def test_indoor_stream_without_gps_untouched(self):
        streams = [{"time_offset": i, "lat": None, "lng": None, "speed_kmh": None}
                   for i in range(5)]
        ingestor_db.fill_missing_speed(streams)
        assert all(p["speed_kmh"] is None for p in streams)

    def test_gps_glitch_jump_left_empty(self):
        streams = _gps_only(3)
        streams[2]["lat"] = 46.1  # ~11 km in 2 s
        ingestor_db.fill_missing_speed(streams)
        assert streams[2]["speed_kmh"] is None

    def test_non_increasing_time_left_empty(self):
        streams = _gps_only(3)
        streams[2]["time_offset"] = streams[1]["time_offset"]
        ingestor_db.fill_missing_speed(streams)
        assert streams[2]["speed_kmh"] is None


class TestGpsOnlyClimbDetection:
    def test_climb_found_on_gps_only_ride(self):
        # 1 km flat, then ~2.2 km at ~6 %
        def alt(i):
            return 1000.0 if i < 90 else 1000.0 + (i - 90) * LAT_STEP_M * 0.06
        streams = ingestor_db.fill_missing_speed(_gps_only(290, alt=alt))
        cum, dist = 0.0, []
        for prev, pt in zip([streams[0]] + streams, streams):
            cum += (pt["speed_kmh"] or 0) / 3.6 * (pt["time_offset"] - prev["time_offset"])
            dist.append(cum)
        climbs = detect_climbs(smooth_altitude([p["altitude_m"] for p in streams]), dist,
                               time_offsets=[p["time_offset"] for p in streams])
        # Before speed was derived, every distance was 0 and nothing was found.
        assert len(climbs) == 1
        assert climbs[0]["gain_m"] == pytest.approx(200 * LAT_STEP_M * 0.06, abs=8)  # end trimmed by smoothing
        assert climbs[0]["length_m"] >= 200 * LAT_STEP_M


class TestUpsertStreamsFillsSpeed:
    def test_inserted_rows_carry_derived_speed(self):
        conn = MagicMock()
        cur = MagicMock()
        conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        execute_values = MagicMock()
        orig = ingestor_db.psycopg2.extras.execute_values
        ingestor_db.psycopg2.extras.execute_values = execute_values
        try:
            ingestor_db.upsert_streams(conn, 7, _gps_only(3))
        finally:
            ingestor_db.psycopg2.extras.execute_values = orig
        rows = execute_values.call_args[0][2]
        assert rows[1][5] == pytest.approx(LAT_STEP_M / 2 * 3.6, abs=0.01)  # speed_kmh column
