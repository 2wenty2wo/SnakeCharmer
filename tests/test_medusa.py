from unittest.mock import MagicMock, call, patch

import pytest
import requests

from app.config import MedusaConfig
from app.medusa import REQUEST_TIMEOUT, MedusaClient, resolve_quality


@pytest.fixture
def medusa_config():
    return MedusaConfig(url="http://localhost:8081", api_key="test-key")


@pytest.fixture
def client(medusa_config):
    return MedusaClient(medusa_config)


def _mock_response(json_data, status_code=200):
    resp = MagicMock(spec=requests.Response)
    resp.json.return_value = json_data
    resp.status_code = status_code
    resp.raise_for_status.return_value = None
    return resp


class TestGetExistingTvdbIds:
    def test_returns_tvdb_ids(self, client):
        series = [
            {"id": {"tvdb": 123}},
            {"id": {"tvdb": 456}},
            {"id": {}},  # missing tvdb
        ]
        with patch.object(client, "_request", return_value=_mock_response(series)) as mock_req:
            ids = client.get_existing_tvdb_ids()

        assert ids == {123, 456}
        mock_req.assert_called_once_with(
            "GET", "/series", params={"limit": 1000, "page": 1}
        )

    def test_fetches_multiple_pages_when_library_exceeds_page_size(self, client):
        batch1 = [{"id": {"tvdb": i}} for i in range(1, 1001)]
        batch2 = [{"id": {"tvdb": i}} for i in range(1001, 1051)]
        with patch.object(
            client,
            "_request",
            side_effect=[_mock_response(batch1), _mock_response(batch2)],
        ) as mock_req:
            ids = client.get_existing_tvdb_ids()

        assert len(ids) == 1050
        assert mock_req.call_count == 2
        assert mock_req.call_args_list[0] == call(
            "GET", "/series", params={"limit": 1000, "page": 1}
        )
        assert mock_req.call_args_list[1] == call(
            "GET", "/series", params={"limit": 1000, "page": 2}
        )

    def test_returns_empty_set_for_empty_library(self, client):
        with patch.object(client, "_request", return_value=_mock_response([])):
            ids = client.get_existing_tvdb_ids()

        assert ids == set()

    def test_tvdb_id_zero_excluded(self, client):
        """A series with tvdb_id of 0 is falsy and should not appear in the result set."""
        series = [{"id": {"tvdb": 0}}, {"id": {"tvdb": 123}}]
        with patch.object(client, "_request", return_value=_mock_response(series)):
            ids = client.get_existing_tvdb_ids()
        assert ids == {123}
        assert 0 not in ids

    def test_skips_malformed_tvdb_ids(self, client):
        series = [
            {"id": {"tvdb": 123}},
            {"id": {"tvdb": "bad-id"}},
            {"id": {"tvdb": 456}},
        ]
        with patch.object(client, "_request", return_value=_mock_response(series)):
            ids = client.get_existing_tvdb_ids()

        assert ids == {123, 456}

    def test_invalid_json_response_propagates(self, client):
        """If the API returns non-JSON, the ValueError from .json() should propagate."""
        bad_resp = MagicMock(spec=requests.Response)
        bad_resp.json.side_effect = ValueError("No JSON object could be decoded")
        bad_resp.status_code = 200
        bad_resp.raise_for_status.return_value = None
        with (
            patch.object(client, "_request", return_value=bad_resp),
            pytest.raises(ValueError),
        ):
            client.get_existing_tvdb_ids()


class TestGetSeriesList:
    def test_normalizes_dict_year_to_start(self, client):
        series = [
            {
                "title": "Future Show",
                "id": {"tvdb": 123, "imdb": "tt123"},
                "year": {"start": 2025, "end": 2026},
                "status": "continuing",
                "network": "Net",
            }
        ]
        with patch.object(client, "_request", return_value=_mock_response(series)):
            shows = client.get_series_list()

        assert shows[0]["year"] == 2025

    def test_normalizes_dict_year_to_end_when_start_missing(self, client):
        series = [
            {
                "title": "Ended Show",
                "id": {"tvdb": 456},
                "year": {"end": 2019},
            }
        ]
        with patch.object(client, "_request", return_value=_mock_response(series)):
            shows = client.get_series_list()

        assert shows[0]["year"] == 2019

    def test_keeps_scalar_year_values(self, client):
        series = [
            {"title": "Show A", "id": {"tvdb": 1}, "year": "2024"},
            {"title": "Show B", "id": {"tvdb": 2}, "year": 2023},
        ]
        with patch.object(client, "_request", return_value=_mock_response(series)):
            shows = client.get_series_list()

        years = [show["year"] for show in shows]
        assert "2024" in years
        assert 2023 in years

    def test_skips_malformed_tvdb_ids(self, client):
        series = [
            {"title": "Good Show", "id": {"tvdb": 1}},
            {"title": "Bad Show", "id": {"tvdb": "nope"}},
            {"title": "Another Good", "id": {"tvdb": 2}},
        ]
        with patch.object(client, "_request", return_value=_mock_response(series)):
            shows = client.get_series_list()

        assert len(shows) == 2
        assert {s["tvdb_id"] for s in shows} == {1, 2}


class TestAddShow:
    def test_adds_show_successfully(self, client):
        with patch.object(client, "_request", return_value=_mock_response({})) as mock_request:
            result = client.add_show(12345, "Test Show")

        assert result is True
        _, kwargs = mock_request.call_args
        assert kwargs["json"] == {"id": {"tvdb": 12345}}

    def test_merges_allowed_add_options(self, client):
        with patch.object(client, "_request", return_value=_mock_response({})) as mock_request:
            result = client.add_show(
                12345,
                "Test Show",
                add_options={
                    "quality": "hd720p",
                    "required_words": ["proper"],
                },
            )

        assert result is True
        _, kwargs = mock_request.call_args
        assert kwargs["json"] == {
            "id": {"tvdb": 12345},
            "options": {
                "quality": {
                    "allowed": [8, 64, 256],
                    "preferred": [],
                },
                "release": {"requiredWords": ["proper"]},
            },
        }

    def test_post_payload_includes_quality_and_required_words_when_provided(self, client):
        with patch.object(client, "_request", return_value=_mock_response({})) as mock_request:
            result = client.add_show(
                777,
                "Configured Show",
                add_options={
                    "quality": ["uhd4k", "hd1080p"],
                    "required_words": ["remux", "proper"],
                },
            )

        assert result is True
        _, kwargs = mock_request.call_args
        # uhd4k=[1024,2048,4096] + hd1080p=[32,128,512] combined
        assert kwargs["json"] == {
            "id": {"tvdb": 777},
            "options": {
                "quality": {
                    "allowed": [32, 128, 512, 1024, 2048, 4096],
                    "preferred": [],
                },
                "release": {"requiredWords": ["remux", "proper"]},
            },
        }

    def test_post_payload_is_minimal_when_no_options_provided(self, client):
        with patch.object(client, "_request", return_value=_mock_response({})) as mock_request:
            result = client.add_show(888, "Bare Minimum Show", add_options=None)

        assert result is True
        _, kwargs = mock_request.call_args
        assert kwargs["json"] == {"id": {"tvdb": 888}}

    def test_returns_false_for_conflict(self, client):
        error_resp = MagicMock(spec=requests.Response)
        error_resp.status_code = 409
        http_error = requests.HTTPError(response=error_resp)

        with patch.object(client, "_request", side_effect=http_error):
            result = client.add_show(12345, "Test Show")

        assert result is False

    def test_raises_on_other_http_errors(self, client):
        error_resp = MagicMock(spec=requests.Response)
        error_resp.status_code = 500
        http_error = requests.HTTPError(response=error_resp)

        with (
            patch.object(client, "_request", side_effect=http_error),
            pytest.raises(requests.HTTPError),
        ):
            client.add_show(12345, "Test Show")

    def test_raises_when_http_error_has_no_response(self, client):
        http_error = requests.HTTPError(response=None)

        with (
            patch.object(client, "_request", side_effect=http_error),
            pytest.raises(requests.HTTPError),
        ):
            client.add_show(12345, "Test Show")

    def test_required_words_only_no_quality_in_payload(self, client):
        with patch.object(client, "_request", return_value=_mock_response({})) as mock_request:
            result = client.add_show(555, "Words Only", add_options={"required_words": ["proper"]})

        assert result is True
        _, kwargs = mock_request.call_args
        payload = kwargs["json"]
        assert "options" in payload
        assert "quality" not in payload["options"]
        assert payload["options"]["release"] == {"requiredWords": ["proper"]}

    def test_empty_add_options_dict_produces_minimal_payload(self, client):
        with patch.object(client, "_request", return_value=_mock_response({})) as mock_request:
            result = client.add_show(666, "Empty Options", add_options={})

        assert result is True
        _, kwargs = mock_request.call_args
        assert kwargs["json"] == {"id": {"tvdb": 666}}


class TestResolveQuality:
    def test_preset_hd720p(self):
        assert resolve_quality("hd720p") == [8, 64, 256]

    def test_preset_hd1080p(self):
        assert resolve_quality("hd1080p") == [32, 128, 512]

    def test_preset_uhd4k(self):
        assert resolve_quality("uhd4k") == [1024, 2048, 4096]

    def test_individual_quality(self):
        assert resolve_quality("hdtv") == [8]

    def test_list_of_names(self):
        assert resolve_quality(["hdtv", "fullhdwebdl"]) == [8, 128]

    def test_case_insensitive(self):
        assert resolve_quality("HD720p") == [8, 64, 256]

    def test_unknown_quality_raises(self):
        with pytest.raises(ValueError, match="Unknown Medusa quality 'bogus'"):
            resolve_quality("bogus")

    def test_empty_list_returns_empty(self):
        assert resolve_quality([]) == []

    def test_duplicate_names_deduplicated(self):
        assert resolve_quality(["hdtv", "hdtv"]) == [8]

    def test_unknown_in_list_raises(self):
        with pytest.raises(ValueError, match="Unknown Medusa quality 'bogus'"):
            resolve_quality(["hdtv", "bogus"])

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="Unknown Medusa quality ''"):
            resolve_quality("")

    def test_whitespace_stripped(self):
        assert resolve_quality("  hdtv  ") == [8]

    def test_whitespace_stripped_in_list(self):
        assert resolve_quality(["  hdtv  ", " hdbluray "]) == [8, 256]


class TestRequest:
    def test_includes_timeout(self, client):
        mock_resp = _mock_response([])
        with patch.object(client.session, "request", return_value=mock_resp) as mock_req:
            client._request("GET", "/series")
            _, kwargs = mock_req.call_args
            assert kwargs["timeout"] == REQUEST_TIMEOUT

    def test_raises_connection_error(self, client):
        with (
            patch.object(
                client.session, "request", side_effect=requests.ConnectionError("refused")
            ),
            pytest.raises(requests.ConnectionError),
        ):
            client._request("GET", "/series")


class TestRetry:
    def test_retries_on_connection_error_then_succeeds(self, medusa_config):
        client = MedusaClient(medusa_config, max_retries=2, retry_backoff=0.01)
        success = _mock_response([])

        with (
            patch.object(
                client.session,
                "request",
                side_effect=[requests.ConnectionError("refused"), success],
            ),
            patch("app.http_client.time.sleep"),
        ):
            resp = client._request("GET", "/series")

        assert resp.json() == []

    def test_retries_on_500_then_succeeds(self, medusa_config):
        client = MedusaClient(medusa_config, max_retries=2, retry_backoff=0.01)
        error_resp = _mock_response({}, status_code=500)
        success = _mock_response({"ok": True})

        with (
            patch.object(client.session, "request", side_effect=[error_resp, success]),
            patch("app.http_client.time.sleep"),
        ):
            resp = client._request("GET", "/series")

        assert resp.json() == {"ok": True}

    def test_does_not_retry_4xx(self, medusa_config):
        client = MedusaClient(medusa_config, max_retries=2, retry_backoff=0.01)
        bad_request = _mock_response({}, status_code=400)
        bad_request.raise_for_status.side_effect = requests.HTTPError(response=bad_request)

        with (
            patch.object(client.session, "request", return_value=bad_request),
            pytest.raises(requests.HTTPError),
        ):
            client._request("GET", "/series")

    def test_gives_up_after_max_retries(self, medusa_config):
        client = MedusaClient(medusa_config, max_retries=2, retry_backoff=0.01)

        with (
            patch.object(
                client.session,
                "request",
                side_effect=requests.ConnectionError("refused"),
            ),
            patch("app.http_client.time.sleep"),
            pytest.raises(requests.ConnectionError),
        ):
            client._request("GET", "/series")

    def test_retries_on_timeout_then_succeeds(self, medusa_config):
        client = MedusaClient(medusa_config, max_retries=1, retry_backoff=0.01)
        success = _mock_response([])

        with (
            patch.object(
                client.session,
                "request",
                side_effect=[requests.Timeout("timeout"), success],
            ),
            patch("app.http_client.time.sleep"),
        ):
            resp = client._request("GET", "/series")

        assert resp.json() == []

    def test_exhausted_5xx_retries_raises(self, medusa_config):
        """When all retry attempts return 5xx, the last exception is raised."""
        client = MedusaClient(medusa_config, max_retries=1, retry_backoff=0.01)
        error_resp = _mock_response({}, status_code=503)
        error_resp.raise_for_status.side_effect = requests.HTTPError(response=error_resp)

        with (
            patch.object(client.session, "request", return_value=error_resp),
            patch("app.http_client.time.sleep"),
            pytest.raises(requests.HTTPError),
        ):
            client._request("GET", "/series")

    def test_exhausted_connection_retries_logs_unreachable(self, medusa_config):
        """When all connection retries are exhausted, logs Medusa unreachable."""
        client = MedusaClient(medusa_config, max_retries=1, retry_backoff=0.01)

        with (
            patch.object(
                client.session,
                "request",
                side_effect=requests.ConnectionError("refused"),
            ),
            patch("app.http_client.time.sleep"),
            patch("app.medusa.log.error") as mock_log_error,
            pytest.raises(requests.ConnectionError),
        ):
            client._request("GET", "/series")

        mock_log_error.assert_called_once()
        assert "Cannot reach Medusa" in mock_log_error.call_args[0][0]

    def test_backoff_sleep_durations_on_5xx(self, medusa_config):
        """Verify that sleep durations follow retry_backoff ** (attempt + 1) formula."""
        client = MedusaClient(medusa_config, max_retries=3, retry_backoff=2.0)
        error_resp = _mock_response({}, status_code=500)
        success = _mock_response({"ok": True})

        with (
            patch.object(
                client.session,
                "request",
                side_effect=[error_resp, error_resp, error_resp, success],
            ),
            patch("app.http_client.time.sleep") as mock_sleep,
        ):
            client._request("GET", "/series")

        assert mock_sleep.call_args_list == [call(2.0), call(4.0), call(8.0)]

    def test_backoff_sleep_durations_on_connection_error(self, medusa_config):
        """Verify that connection error retries use correct backoff delays."""
        client = MedusaClient(medusa_config, max_retries=2, retry_backoff=3.0)
        success = _mock_response([])

        with (
            patch.object(
                client.session,
                "request",
                side_effect=[
                    requests.ConnectionError("refused"),
                    requests.ConnectionError("refused"),
                    success,
                ],
            ),
            patch("app.http_client.time.sleep") as mock_sleep,
        ):
            client._request("GET", "/series")

        assert mock_sleep.call_args_list == [call(3.0), call(9.0)]


class TestResolveQualityEdgeCases:
    def test_preset_combined_with_individual(self):
        """Combining a preset (hd) with an individual value (sddvd) merges bitmasks."""
        result = resolve_quality(["hd", "sddvd"])
        # hd = [8, 16, 32, 64, 128, 256, 512], sddvd = [4]
        assert 4 in result  # sddvd
        assert 8 in result  # hdtv (from hd preset)

    def test_bitmask_to_quality_list_directly(self):
        """Test _bitmask_to_quality_list decomposition independently."""
        from app.medusa import _bitmask_to_quality_list

        # 8 + 64 = 72 (hdtv + hdwebdl)
        result = _bitmask_to_quality_list(72)
        assert result == [8, 64]

    def test_bitmask_to_quality_list_zero_excluded(self):
        """Zero ('na') should not appear in decomposed list."""
        from app.medusa import _bitmask_to_quality_list

        result = _bitmask_to_quality_list(65535)
        assert 0 not in result

    def test_add_show_with_invalid_quality_raises_value_error(self, medusa_config):
        """ValueError from resolve_quality should propagate through add_show."""
        client = MedusaClient(medusa_config)
        with (
            patch.object(client, "_request", return_value=_mock_response({})),
            pytest.raises(ValueError, match="Unknown Medusa quality"),
        ):
            client.add_show(123, "Test", add_options={"quality": "nonexistent"})
