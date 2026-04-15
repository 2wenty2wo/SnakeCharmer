"""Tests for ``app.oauth_device`` helpers."""

import pytest

from app.oauth_device import parse_oauth_device_timing


class TestParseOauthDeviceTiming:
    def test_returns_none_for_non_numeric(self):
        assert parse_oauth_device_timing("x", 600) is None
        assert parse_oauth_device_timing(5, "y") is None

    def test_clamps_interval_below_1(self):
        assert parse_oauth_device_timing(0, 600) == (1, 600)
        assert parse_oauth_device_timing(-2, 600) == (1, 600)

    def test_clamps_expires_below_600(self):
        assert parse_oauth_device_timing(5, 0) == (5, 600)
        assert parse_oauth_device_timing(5, 599) == (5, 600)

    @pytest.mark.parametrize("expires", [600, 700, 3600])
    def test_preserves_expires_at_or_above_minimum(self, expires):
        assert parse_oauth_device_timing(5, expires) == (5, expires)
