from unittest.mock import MagicMock, patch

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
        with patch.object(client, "_request", return_value=_mock_response(series)):
            ids = client.get_existing_tvdb_ids()

        assert ids == {123, 456}

    def test_returns_empty_set_for_empty_library(self, client):
        with patch.object(client, "_request", return_value=_mock_response([])):
            ids = client.get_existing_tvdb_ids()

        assert ids == set()


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
            patch("app.medusa.time.sleep"),
        ):
            resp = client._request("GET", "/series")

        assert resp.json() == []

    def test_retries_on_500_then_succeeds(self, medusa_config):
        client = MedusaClient(medusa_config, max_retries=2, retry_backoff=0.01)
        error_resp = _mock_response({}, status_code=500)
        success = _mock_response({"ok": True})

        with (
            patch.object(client.session, "request", side_effect=[error_resp, success]),
            patch("app.medusa.time.sleep"),
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
            patch("app.medusa.time.sleep"),
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
            patch("app.medusa.time.sleep"),
        ):
            resp = client._request("GET", "/series")

        assert resp.json() == []
