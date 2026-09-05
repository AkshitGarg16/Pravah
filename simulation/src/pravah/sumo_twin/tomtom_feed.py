"""Turns TomTom's discrete per-weekday segment samples into a continuous
demand stream for the simulated hour, and turns that into SUMO insertion
parameters.

TomTom's data here is only ever 5 numbers per segment -- one hourly
average per weekday (Mon-Fri), not sub-hour readings. There's no true
intra-hour discreteness to interpolate between in this dataset; what's
here instead is a documented stand-in for the real problem, matching what
the user described: spread the 5 weekday samples evenly across the
simulated hour and linearly interpolate between them, so demand varies
smoothly over the run instead of being one flat number for the whole
3600s -- the same *shape* of fix (discrete anchors -> continuous stream)
the team's real GNN work is meant to do on genuinely sub-minute TomTom
updates, not a claim this reproduces real intra-hour dynamics.
"""

_WEEKDAY_ORDER = {"Monday": 0, "Tuesday": 1, "Wednesday": 2, "Thursday": 3, "Friday": 4, "Saturday": 5, "Sunday": 6}

KMH_TO_MS = 1 / 3.6


def interpolate_segment_series(segment_rows, duration=3600):
    """segment_rows: this segment_id's CSV rows (one per weekday, any order).

    Returns series(t) -> {"avg_speed_kmh", "sample_size"} for t in
    [0, duration], linearly interpolated between the weekday samples,
    spread evenly across that window in Mon->Fri order.
    """
    anchors = sorted(segment_rows, key=lambda r: _WEEKDAY_ORDER.get(r["day"], 99))
    n = len(anchors)
    speeds = [float(r["average_speed"]) for r in anchors]
    counts = [float(r["sample_size"]) for r in anchors]

    if n == 1:
        return lambda t: {"avg_speed_kmh": speeds[0], "sample_size": counts[0]}

    anchor_times = [i * duration / (n - 1) for i in range(n)]

    def series(t):
        t = min(max(t, 0.0), duration)
        i = 0
        while i < n - 2 and t > anchor_times[i + 1]:
            i += 1
        span = anchor_times[i + 1] - anchor_times[i]
        frac = (t - anchor_times[i]) / span if span > 0 else 0.0
        return {
            "avg_speed_kmh": speeds[i] + frac * (speeds[i + 1] - speeds[i]),
            "sample_size": counts[i] + frac * (counts[i + 1] - counts[i]),
        }

    return series


def insertion_rate(sample_size, weight_total, target_network_vehicles_per_hour):
    """Turns one segment's (interpolated) TomTom sample_size into a SUMO
    insertion rate for that edge, as a share of a fixed network-wide total.

    TomTom's sample_size is a *probe* count (how many TomTom-tracked
    devices reported on this segment in the window), not literally every
    vehicle -- GPS probe penetration is a fraction of real traffic. Rather
    than invent a penetration-rate multiplier, this uses sample_size as a
    *relative* weight across segments (a segment with double the samples
    gets roughly double the simulated vehicles) and scales the whole
    network to a fixed target total that's already known to run well
    (matches this session's earlier randomTrips.py-based baseline of
    ~1200 vehicles/hour network-wide).
    """
    if weight_total <= 0:
        return 0.0
    return (sample_size / weight_total) * target_network_vehicles_per_hour


def depart_speed_ms(avg_speed_kmh, minimum_ms=0.5):
    """km/h -> m/s (SUMO's unit), floored so a near-zero interpolated speed
    doesn't produce an invalid/zero departure speed."""
    return max(avg_speed_kmh * KMH_TO_MS, minimum_ms)
