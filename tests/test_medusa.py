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
