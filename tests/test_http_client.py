from unittest.mock import MagicMock, call, patch

import pytest
import requests

from app.http_client import REQUEST_TIMEOUT, RetryClient


def _make_session():
    return MagicMock(spec=requests.Session)


def _mock_response(status_code=200, json_data=None):
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.raise_for_status.return_value = None
    if status_code >= 400:
        resp.raise_for_status.side_effect = requests.HTTPError(response=resp)
    return resp


class TestRetryClientRequest:
    def test_successful_request(self):
        session = _make_session()
        resp = _mock_response(200, {"ok": True})
        session.request.return_value = resp
        client = RetryClient(session, "http://example.com")

        result = client._request("GET", "/test")

        assert result.json() == {"ok": True}
        session.request.assert_called_once()

    def test_sets_default_timeout(self):
        session = _make_session()
        session.request.return_value = _mock_response(200)
        client = RetryClient(session, "http://example.com")

        client._request("GET", "/test")

        _, kwargs = session.request.call_args
        assert kwargs["timeout"] == REQUEST_TIMEOUT

    def test_builds_url_from_base_url_and_path(self):
        session = _make_session()
        session.request.return_value = _mock_response(200)
        client = RetryClient(session, "http://example.com/api")

        client._request("GET", "/items")

        args, _ = session.request.call_args
        assert args == ("GET", "http://example.com/api/items")


class TestRetryOn5xx:
    def test_retries_on_500_then_succeeds(self):
        session = _make_session()
        error = _mock_response(500)
        error.raise_for_status.side_effect = None  # not called during retry
        success = _mock_response(200, {"ok": True})
        session.request.side_effect = [error, success]
        client = RetryClient(session, "http://example.com", max_retries=2)

        with patch("app.http_client.time.sleep"):
            result = client._request("GET", "/test")

        assert result.json() == {"ok": True}

    def test_exhausted_5xx_retries_raises(self):
        session = _make_session()
        error = _mock_response(503)
        session.request.return_value = error
        client = RetryClient(session, "http://example.com", max_retries=1)

        with (
            patch("app.http_client.time.sleep"),
            pytest.raises(requests.HTTPError),
        ):
            client._request("GET", "/test")

    def test_backoff_durations(self):
        session = _make_session()
        error = _mock_response(500)
        error.raise_for_status.side_effect = None
        success = _mock_response(200)
        session.request.side_effect = [error, error, success]
        client = RetryClient(session, "http://example.com", max_retries=3, retry_backoff=2.0)

        with patch("app.http_client.time.sleep") as mock_sleep:
            client._request("GET", "/test")

        assert mock_sleep.call_args_list == [call(2.0), call(4.0)]


class TestRetryOnConnectionError:
    def test_retries_then_succeeds(self):
        session = _make_session()
        success = _mock_response(200, {"ok": True})
        session.request.side_effect = [requests.ConnectionError("refused"), success]
        client = RetryClient(session, "http://example.com", max_retries=2)

        with patch("app.http_client.time.sleep"):
            result = client._request("GET", "/test")

        assert result.json() == {"ok": True}

    def test_exhausted_raises(self):
        session = _make_session()
        session.request.side_effect = requests.ConnectionError("refused")
        client = RetryClient(session, "http://example.com", max_retries=1)

        with (
            patch("app.http_client.time.sleep"),
            pytest.raises(requests.ConnectionError),
        ):
            client._request("GET", "/test")

    def test_retries_on_timeout(self):
        session = _make_session()
        success = _mock_response(200)
        session.request.side_effect = [requests.Timeout("timed out"), success]
        client = RetryClient(session, "http://example.com", max_retries=1)

        with patch("app.http_client.time.sleep"):
            result = client._request("GET", "/test")

        assert result.status_code == 200


class TestHooks:
    def test_handle_rate_limit_default_noop(self):
        session = _make_session()
        resp = _mock_response(200)
        session.request.return_value = resp
        client = RetryClient(session, "http://example.com")

        assert client._handle_rate_limit(resp, "GET", "http://example.com/test") is None

    def test_handle_rate_limit_replacement_used(self):
        session = _make_session()
        rate_limited = _mock_response(429)
        rate_limited.raise_for_status.side_effect = None
        replacement = _mock_response(200, {"ok": True})

        class CustomClient(RetryClient):
            def _handle_rate_limit(self, resp, method, url, **kwargs):
                if resp.status_code == 429:
                    return replacement
                return None

        session.request.return_value = rate_limited
        client = CustomClient(session, "http://example.com")

        result = client._request("GET", "/test")

        assert result.json() == {"ok": True}

    def test_on_connection_exhausted_called(self):
        session = _make_session()
        session.request.side_effect = requests.ConnectionError("refused")

        class CustomClient(RetryClient):
            exhausted_called = False

            def _on_connection_exhausted(self, exc):
                CustomClient.exhausted_called = True

        client = CustomClient(session, "http://example.com", max_retries=0)

        with pytest.raises(requests.ConnectionError):
            client._request("GET", "/test")

        assert CustomClient.exhausted_called

    def test_on_connection_exhausted_not_called_on_timeout(self):
        session = _make_session()
        session.request.side_effect = requests.Timeout("timed out")

        class CustomClient(RetryClient):
            exhausted_called = False

            def _on_connection_exhausted(self, exc):
                CustomClient.exhausted_called = True

        client = CustomClient(session, "http://example.com", max_retries=0)

        with pytest.raises(requests.Timeout):
            client._request("GET", "/test")

        assert not CustomClient.exhausted_called

    def test_service_name_in_log_messages(self):
        session = _make_session()
        error = _mock_response(500)
        error.raise_for_status.side_effect = None
        success = _mock_response(200)
        session.request.side_effect = [error, success]

        class CustomClient(RetryClient):
            _service_name = "TestAPI"

        client = CustomClient(session, "http://example.com", max_retries=1)

        with (
            patch("app.http_client.time.sleep"),
            patch("app.http_client.log.warning") as mock_warn,
        ):
            client._request("GET", "/test")

        assert mock_warn.call_args[0][1] == "TestAPI"


class TestNoRetry4xx:
    def test_4xx_not_retried(self):
        session = _make_session()
        bad_request = _mock_response(400)
        session.request.return_value = bad_request
        client = RetryClient(session, "http://example.com", max_retries=3)

        with pytest.raises(requests.HTTPError):
            client._request("GET", "/test")

        session.request.assert_called_once()
