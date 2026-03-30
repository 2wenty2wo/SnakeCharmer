from unittest.mock import MagicMock, patch

import pytest
import requests

from app.config import TraktConfig
from app.trakt import REQUEST_TIMEOUT, TraktClient, TraktShow


@pytest.fixture
def trakt_config():
    return TraktConfig(
        client_id="test-client-id",
        client_secret="test-secret",
        username="testuser",
        lists=["trending"],
        limit=10,
    )


@pytest.fixture
def client(trakt_config, tmp_path):
    return TraktClient(trakt_config, config_dir=str(tmp_path))


def _mock_response(json_data, status_code=200, headers=None):
    resp = MagicMock(spec=requests.Response)
    resp.json.return_value = json_data
    resp.status_code = status_code
    resp.headers = headers or {"X-Pagination-Page-Count": "1"}
    resp.raise_for_status.return_value = None
    return resp


class TestParseShow:
    def test_parses_valid_show(self, client):
        data = {
            "title": "Breaking Bad",
            "year": 2008,
            "ids": {"tvdb": 81189, "imdb": "tt0903747"},
        }
        show = client._parse_show(data)

        assert show == TraktShow(
            title="Breaking Bad", tvdb_id=81189, imdb_id="tt0903747", year=2008
        )

    def test_skips_show_without_tvdb_id(self, client):
        data = {"title": "No TVDB", "ids": {"imdb": "tt1234567"}}
        assert client._parse_show(data) is None

    def test_handles_missing_optional_fields(self, client):
        data = {"title": "Minimal", "ids": {"tvdb": 12345}}
        show = client._parse_show(data)

        assert show.title == "Minimal"
        assert show.tvdb_id == 12345
        assert show.imdb_id is None
        assert show.year is None


class TestGetShows:
    def test_fetch_trending(self, client):
        items = [
            {"show": {"title": "Show A", "ids": {"tvdb": 1}}},
            {"show": {"title": "Show B", "ids": {"tvdb": 2}}},
        ]
        with patch.object(client, "_request", return_value=_mock_response(items)):
            shows = client.get_shows("trending")

        assert len(shows) == 2
        assert shows[0].title == "Show A"
        assert shows[1].title == "Show B"

    def test_fetch_popular(self, client):
        items = [{"title": "Popular Show", "ids": {"tvdb": 99}}]
        with patch.object(client, "_request", return_value=_mock_response(items)):
            shows = client.get_shows("popular")

        assert len(shows) == 1
        assert shows[0].title == "Popular Show"

    def test_fetch_watchlist(self, client):
        items = [{"show": {"title": "My Show", "ids": {"tvdb": 42}}}]
        with (
            patch.object(client, "_ensure_auth"),
            patch.object(client, "_request", return_value=_mock_response(items)),
        ):
            shows = client.get_shows("watchlist")

        assert len(shows) == 1
        assert shows[0].tvdb_id == 42

    def test_respects_limit(self, client):
        client.config.limit = 2
        items = [{"show": {"title": f"Show {i}", "ids": {"tvdb": i}}} for i in range(5)]
        with patch.object(client, "_request", return_value=_mock_response(items)):
            shows = client.get_shows("trending")

        assert len(shows) == 2


class TestRequest:
    def test_includes_timeout(self, client):
        mock_resp = _mock_response([], status_code=200)
        with patch.object(client.session, "request", return_value=mock_resp) as mock_req:
            client._request("GET", "/test")
            _, kwargs = mock_req.call_args
            assert kwargs["timeout"] == REQUEST_TIMEOUT

    def test_handles_rate_limit(self, client):
        rate_limited = _mock_response([], status_code=429, headers={"Retry-After": "1"})
        success = _mock_response([])

        with (
            patch.object(client.session, "request", side_effect=[rate_limited, success]),
            patch("app.trakt.time.sleep"),
        ):
            resp = client._request("GET", "/test")

        assert resp.status_code == 200
