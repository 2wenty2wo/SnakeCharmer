from unittest.mock import MagicMock, patch

import pytest
import requests

from app.config import MedusaConfig
from app.medusa import REQUEST_TIMEOUT, MedusaClient


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
        with patch.object(client, "_request", return_value=_mock_response({})):
            result = client.add_show(12345, "Test Show")

        assert result is True

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
