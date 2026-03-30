from unittest.mock import MagicMock, patch

import pytest
import requests

from app.config import TraktConfig, TraktSource
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
    def test_normalize_source_alias(self, client):
        source = client._normalize_source("watchlist")

        assert source.type == "watchlist"
        assert source.list_slug == ""
        assert source.requires_auth is False

    def test_normalize_source_custom_list_to_user_list(self, client):
        source = client._normalize_source("my custom")

        assert source.type == "user_list"
        assert source.owner == client.config.username
        assert source.list_slug == "my custom"
        assert source.requires_auth is True

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

    def test_fetch_watched(self, client):
        items = [{"show": {"title": "Watched Show", "ids": {"tvdb": 7}}}]
        with patch.object(client, "_request", return_value=_mock_response(items)):
            shows = client.get_shows("watched")

        assert len(shows) == 1
        assert shows[0].tvdb_id == 7

    def test_fetch_public_user_list_without_oauth(self, client):
        items = [{"show": {"title": "List Show", "ids": {"tvdb": 50}}}]
        source = TraktSource(type="user_list", owner="otheruser", list_slug="public-list")
        with (
            patch.object(client, "_ensure_auth") as mock_auth,
            patch.object(client, "_request", return_value=_mock_response(items)),
        ):
            shows = client.get_shows(source)

        assert len(shows) == 1
        assert shows[0].tvdb_id == 50
        mock_auth.assert_not_called()

    def test_fetch_private_user_list_with_oauth(self, client):
        items = [{"show": {"title": "Private Show", "ids": {"tvdb": 88}}}]
        source = TraktSource(
            type="user_list",
            owner="testuser",
            list_slug="private-list",
            auth=True,
        )
        with (
            patch.object(client, "_ensure_auth") as mock_auth,
            patch.object(client, "_request", return_value=_mock_response(items)),
        ):
            shows = client.get_shows(source)

        assert len(shows) == 1
        assert shows[0].tvdb_id == 88
        mock_auth.assert_called_once()

    def test_respects_limit(self, client):
        client.config.limit = 2
        items = [{"show": {"title": f"Show {i}", "ids": {"tvdb": i}}} for i in range(5)]
        with patch.object(client, "_request", return_value=_mock_response(items)):
            shows = client.get_shows("trending")

        assert len(shows) == 2

    def test_fetch_public_stops_on_empty_page(self, client):
        with patch.object(client, "_request", return_value=_mock_response([])) as mock_request:
            shows = client._fetch_public("/shows/trending", "trending", nested_key="show")

        assert shows == []
        mock_request.assert_called_once()

    def test_fetch_public_uses_pagination_headers(self, client):
        page1 = _mock_response(
            [{"show": {"title": "Show A", "ids": {"tvdb": 1}}}],
            headers={"X-Pagination-Page-Count": "2"},
        )
        page2 = _mock_response(
            [{"show": {"title": "Show B", "ids": {"tvdb": 2}}}],
            headers={"X-Pagination-Page-Count": "2"},
        )
        with patch.object(client, "_request", side_effect=[page1, page2]) as mock_request:
            shows = client._fetch_public("/shows/trending", "trending", nested_key="show")

        assert [show.tvdb_id for show in shows] == [1, 2]
        assert mock_request.call_count == 2

    def test_fetch_user_list_paginates_until_last_page(self, client):
        page1 = _mock_response(
            [{"show": {"title": "Show A", "ids": {"tvdb": 1}}}],
            headers={"X-Pagination-Page-Count": "2"},
        )
        page2 = _mock_response(
            [{"show": {"title": "Show B", "ids": {"tvdb": 2}}}],
            headers={"X-Pagination-Page-Count": "2"},
        )
        with patch.object(client, "_request", side_effect=[page1, page2]):
            shows = client._fetch_user_list(
                "/users/test/list/items/shows", "list", nested_key="show"
            )

        assert [show.tvdb_id for show in shows] == [1, 2]

    def test_fetch_user_list_stops_on_empty_page(self, client):
        with patch.object(client, "_request", return_value=_mock_response([])) as mock_request:
            shows = client._fetch_user_list(
                "/users/test/list/items/shows", "list", nested_key="show"
            )

        assert shows == []
        mock_request.assert_called_once()

    def test_get_shows_unsupported_source_type_raises(self, client):
        with pytest.raises(ValueError, match="Unsupported Trakt source type"):
            client.get_shows(TraktSource(type="unknown"))


class TestAuth:
    def test_load_token_returns_none_when_file_missing(self, client):
        assert client._load_token() is None

    def test_load_token_invalid_json_returns_none(self, client, tmp_path):
        token_file = tmp_path / "trakt_token.json"
        token_file.write_text("{invalid")
        client.token_path = str(token_file)

        assert client._load_token() is None

    def test_load_token_returns_valid_non_expired_token(self, client, tmp_path):
        token = {"access_token": "abc", "created_at": 1000, "expires_in": 100000}
        token_file = tmp_path / "trakt_token.json"
        token_file.write_text('{"access_token":"abc","created_at":1000,"expires_in":100000}')
        client.token_path = str(token_file)

        with patch("app.trakt.time.time", return_value=1500):
            loaded = client._load_token()

        assert loaded == token

    def test_load_token_refreshes_expired_token(self, client, tmp_path):
        token_file = tmp_path / "trakt_token.json"
        token_file.write_text(
            '{"access_token":"old","refresh_token":"r1","created_at":1000,"expires_in":100}'
        )
        client.token_path = str(token_file)
        refreshed = {"access_token": "new"}

        with (
            patch("app.trakt.time.time", return_value=5000),
            patch.object(client, "_refresh_token", return_value=refreshed) as mock_refresh,
        ):
            loaded = client._load_token()

        assert loaded == refreshed
        mock_refresh.assert_called_once()

    def test_ensure_auth_uses_loaded_token(self, client):
        with (
            patch.object(client, "_load_token", return_value={"access_token": "abc"}),
            patch.object(client, "_authenticate") as mock_auth,
        ):
            client._ensure_auth()

        assert client.session.headers["Authorization"] == "Bearer abc"
        mock_auth.assert_not_called()

    def test_ensure_auth_authenticates_when_no_token(self, client):
        with (
            patch.object(client, "_load_token", return_value=None),
            patch.object(client, "_authenticate") as mock_auth,
        ):
            client._ensure_auth()

        mock_auth.assert_called_once()

    def test_save_token_writes_file(self, client, tmp_path):
        client.token_path = str(tmp_path / "trakt_token.json")
        client._save_token({"access_token": "abc"})

        assert (tmp_path / "trakt_token.json").exists()
        assert "abc" in (tmp_path / "trakt_token.json").read_text()

    def test_refresh_token_success_saves_and_returns_token(self, client):
        refreshed = {"access_token": "new-token"}
        with (
            patch.object(client.session, "post", return_value=_mock_response(refreshed)),
            patch.object(client, "_save_token") as mock_save,
        ):
            token = client._refresh_token({"refresh_token": "old-refresh"})

        assert token == refreshed
        mock_save.assert_called_once_with(refreshed)

    def test_refresh_token_request_error_returns_none(self, client):
        with patch.object(client.session, "post", side_effect=requests.RequestException("boom")):
            token = client._refresh_token({"refresh_token": "old-refresh"})

        assert token is None

    def test_authenticate_success_sets_authorization(self, client):
        device_resp = _mock_response(
            {
                "user_code": "AAAA",
                "verification_url": "https://trakt.tv/activate",
                "expires_in": 60,
                "interval": 1,
                "device_code": "device-1",
            }
        )
        poll_success = _mock_response({"access_token": "token-123"}, status_code=200)

        with (
            patch.object(client, "_request", return_value=device_resp),
            patch.object(client.session, "post", return_value=poll_success),
            patch.object(client, "_save_token") as mock_save,
            patch("app.trakt.time.sleep"),
            patch("app.trakt.time.time", side_effect=[0, 1]),
        ):
            client._authenticate()

        assert client.session.headers["Authorization"] == "Bearer token-123"
        mock_save.assert_called_once_with({"access_token": "token-123"})

    @pytest.mark.parametrize("status_code", [404, 409, 410, 418])
    def test_authenticate_terminal_poll_status_exits(self, client, status_code):
        device_resp = _mock_response(
            {
                "user_code": "AAAA",
                "verification_url": "https://trakt.tv/activate",
                "expires_in": 60,
                "interval": 1,
                "device_code": "device-1",
            }
        )
        poll_resp = _mock_response({}, status_code=status_code)

        with (
            patch.object(client, "_request", return_value=device_resp),
            patch.object(client.session, "post", return_value=poll_resp),
            patch("app.trakt.time.sleep"),
            patch("app.trakt.time.time", return_value=1),
            patch("app.trakt.log.error"),
            pytest.raises(SystemExit),
        ):
            client._authenticate()

    def test_authenticate_timeout_exits(self, client):
        device_resp = _mock_response(
            {
                "user_code": "AAAA",
                "verification_url": "https://trakt.tv/activate",
                "expires_in": 1,
                "interval": 1,
                "device_code": "device-1",
            }
        )
        pending_resp = _mock_response({}, status_code=400)

        with (
            patch.object(client, "_request", return_value=device_resp),
            patch.object(client.session, "post", return_value=pending_resp),
            patch("app.trakt.time.sleep"),
            patch("app.trakt.time.time", side_effect=[0, 0.5, 2, 2]),
            patch("app.trakt.log.error"),
            pytest.raises(SystemExit),
        ):
            client._authenticate()

    def test_authenticate_429_slow_down_retries(self, client):
        device_resp = _mock_response(
            {
                "user_code": "AAAA",
                "verification_url": "https://trakt.tv/activate",
                "expires_in": 60,
                "interval": 2,
                "device_code": "device-1",
            }
        )
        slow_down = _mock_response({}, status_code=429)
        poll_success = _mock_response({"access_token": "token-123"}, status_code=200)

        with (
            patch.object(client, "_request", return_value=device_resp),
            patch.object(client.session, "post", side_effect=[slow_down, poll_success]),
            patch.object(client, "_save_token") as mock_save,
            patch("app.trakt.time.sleep") as mock_sleep,
            patch("app.trakt.time.time", side_effect=[0, 1, 3]),
        ):
            client._authenticate()

        assert client.session.headers["Authorization"] == "Bearer token-123"
        assert mock_sleep.call_count == 3
        mock_sleep.assert_any_call(2)
        mock_save.assert_called_once_with({"access_token": "token-123"})

    def test_authenticate_request_exception_retries(self, client):
        device_resp = _mock_response(
            {
                "user_code": "AAAA",
                "verification_url": "https://trakt.tv/activate",
                "expires_in": 60,
                "interval": 1,
                "device_code": "device-1",
            }
        )
        poll_success = _mock_response({"access_token": "token-123"}, status_code=200)

        with (
            patch.object(client, "_request", return_value=device_resp),
            patch.object(
                client.session,
                "post",
                side_effect=[requests.RequestException("boom"), poll_success],
            ),
            patch.object(client, "_save_token") as mock_save,
            patch("app.trakt.time.sleep"),
            patch("app.trakt.time.time", side_effect=[0, 1, 2]),
            patch("app.trakt.log.warning") as mock_warning,
        ):
            client._authenticate()

        assert client.session.headers["Authorization"] == "Bearer token-123"
        mock_warning.assert_called_once()
        mock_save.assert_called_once_with({"access_token": "token-123"})


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
