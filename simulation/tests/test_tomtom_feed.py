import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from pravah.sumo_twin.tomtom_feed import depart_speed_ms, insertion_rate, interpolate_segment_series

ROWS = [
    {"day": "Monday", "average_speed": "20.0", "sample_size": "400"},
    {"day": "Tuesday", "average_speed": "22.0", "sample_size": "500"},
    {"day": "Wednesday", "average_speed": "24.0", "sample_size": "600"},
    {"day": "Thursday", "average_speed": "26.0", "sample_size": "700"},
    {"day": "Friday", "average_speed": "28.0", "sample_size": "800"},
]


def test_series_hits_each_weekday_anchor_exactly():
    series = interpolate_segment_series(ROWS, duration=3600)
    # 5 anchors spread evenly across 3600s -> at 0, 900, 1800, 2700, 3600
    assert series(0)["avg_speed_kmh"] == 20.0
    assert series(900)["avg_speed_kmh"] == 22.0
    assert series(1800)["avg_speed_kmh"] == 24.0
    assert series(2700)["avg_speed_kmh"] == 26.0
    assert series(3600)["avg_speed_kmh"] == 28.0


def test_series_interpolates_linearly_between_anchors():
    series = interpolate_segment_series(ROWS, duration=3600)
    mid = series(450)  # halfway between Monday (20.0) and Tuesday (22.0)
    assert mid["avg_speed_kmh"] == 21.0
    assert mid["sample_size"] == 450.0


def test_series_handles_out_of_order_input_rows():
    shuffled = [ROWS[3], ROWS[0], ROWS[4], ROWS[1], ROWS[2]]
    series = interpolate_segment_series(shuffled, duration=3600)
    assert series(0)["avg_speed_kmh"] == 20.0  # still resolves Monday as the first anchor
    assert series(3600)["avg_speed_kmh"] == 28.0


def test_series_clamps_outside_the_duration_window():
    series = interpolate_segment_series(ROWS, duration=3600)
    assert series(-100)["avg_speed_kmh"] == 20.0
    assert series(9999)["avg_speed_kmh"] == 28.0


def test_single_row_holds_constant():
    series = interpolate_segment_series([ROWS[0]], duration=3600)
    assert series(0) == series(1800) == series(3600) == {"avg_speed_kmh": 20.0, "sample_size": 400.0}


def test_insertion_rate_scales_relative_to_network_total():
    rate = insertion_rate(sample_size=500, weight_total=2000, target_network_vehicles_per_hour=1200)
    assert rate == 300.0  # 500/2000 share of 1200


def test_insertion_rate_zero_weight_total_is_safe():
    assert insertion_rate(sample_size=10, weight_total=0, target_network_vehicles_per_hour=1200) == 0.0


def test_depart_speed_converts_kmh_to_ms():
    assert abs(depart_speed_ms(36.0) - 10.0) < 1e-9  # 36 km/h = 10 m/s


def test_depart_speed_floors_near_zero_values():
    assert depart_speed_ms(0.0) == 0.5
