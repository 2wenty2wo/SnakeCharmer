from datetime import datetime, timezone

from app.webui import format_timestamp, format_timestamp_short


class TestFormatTimestamp:
    """Tests for the format_timestamp Jinja2 filter."""

    def test_valid_iso_timestamp_with_z_suffix(self):
        result = format_timestamp("2026-04-10T07:38:11Z")
        # Result should be in local time format like "Apr 10, 2026, 05:38 PM"
        # (converted to local timezone)
        assert "Apr" in result
        assert "2026" in result
        # Time will be converted to local timezone, just check format
        assert ":38" in result  # minutes should be preserved

    def test_valid_iso_timestamp_with_offset(self):
        result = format_timestamp("2026-04-10T07:38:11+00:00")
        assert "Apr" in result
        assert "2026" in result

    def test_none_value_returns_em_dash(self):
        assert format_timestamp(None) == "—"

    def test_empty_string_returns_em_dash(self):
        assert format_timestamp("") == "—"

    def test_invalid_timestamp_returns_original(self):
        assert format_timestamp("not-a-timestamp") == "not-a-timestamp"


class TestFormatTimestampShort:
    """Tests for the format_timestamp_short Jinja2 filter."""

    def test_current_year_omits_year(self):
        now = datetime.now(timezone.utc)
        iso_ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        result = format_timestamp_short(iso_ts)
        # Should NOT include year for current year
        assert str(now.year) not in result
        month_abbrs = [
            "Jan",
            "Feb",
            "Mar",
            "Apr",
            "May",
            "Jun",
            "Jul",
            "Aug",
            "Sep",
            "Oct",
            "Nov",
            "Dec",
        ]
        assert any(month in result for month in month_abbrs)

    def test_different_year_includes_year(self):
        result = format_timestamp_short("2025-04-10T07:38:11Z")
        # Should include year for non-current year
        assert "2025" in result
        assert "Apr" in result

    def test_none_value_returns_em_dash(self):
        assert format_timestamp_short(None) == "—"

    def test_empty_string_returns_em_dash(self):
        assert format_timestamp_short("") == "—"

    def test_invalid_timestamp_returns_original(self):
        assert format_timestamp_short("invalid") == "invalid"
