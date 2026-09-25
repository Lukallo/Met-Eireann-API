"""Plausibility limits for readings.

Met Éireann's observations feed isn't quality-controlled. A value outside these
limits can't be a real reading in Ireland, so the API returns ``null`` for it,
adds a flag, and leaves it out of daily summaries. The limits are well beyond
Irish records (e.g. 33.3 °C, -19.1 °C, 927 hPa) so real extremes are kept.
"""

LIMITS = {
    "temperature_c": (-30, 40),
    "humidity_pct": (0, 100),
    "pressure_msl_hpa": (900, 1070),
    "rainfall_mm": (0, 150),
    "wind_speed_kt": (0, 150),
    "wind_gust_kt": (0, 150),
    "wind_dir_deg": (0, 360),
}


def check(row):
    """Return ``(values, flags)``: the checked columns, with impossible values set to None."""
    values, flags = {}, []
    for column, (low, high) in LIMITS.items():
        value = row[column]
        if value is not None and not low <= value <= high:
            flags.append(f"{column}_out_of_range")
            value = None
        values[column] = value
    return values, flags


def valid_sql(column):
    """SQL expression for ``column`` that is NULL when the value is outside the limits."""
    low, high = LIMITS[column]
    return f"CASE WHEN {column} BETWEEN {low} AND {high} THEN {column} END"
