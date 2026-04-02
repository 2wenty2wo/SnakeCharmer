from unittest.mock import MagicMock, patch

from app.config import NotifyConfig
from app.notify import _build_apprise, _failure_message, _success_message, send_notification
from app.sync import SyncResult


def _success_result(**kwargs) -> SyncResult:
    defaults = dict(
        total_fetched=5,
        unique_shows=5,
        already_in_medusa=3,
        added=2,
        skipped=0,
        failed=0,
        duration_seconds=1.5,
        success=True,
    )
    defaults.update(kwargs)
    return SyncResult(**defaults)


def _failure_result(**kwargs) -> SyncResult:
    defaults = dict(
        total_fetched=0,
        unique_shows=0,
        already_in_medusa=0,
        added=0,
        skipped=0,
        failed=1,
        duration_seconds=0.4,
        success=False,
    )
    defaults.update(kwargs)
    return SyncResult(**defaults)


class TestSendNotification:
    def test_does_nothing_when_disabled(self):
        config = NotifyConfig(enabled=False, urls=["pover://user@token"])
        result = _success_result()
        with patch("app.notify._build_apprise") as mock_build:
            send_notification(config, result)
        mock_build.assert_not_called()

    def test_warns_when_urls_empty(self, caplog):
        import logging

        config = NotifyConfig(enabled=True, urls=[])
        result = _success_result()
        with (
            caplog.at_level(logging.WARNING, logger="app.notify"),
            patch("app.notify._build_apprise") as mock_build,
        ):
            send_notification(config, result)
        mock_build.assert_not_called()
        assert "notify.urls is empty" in caplog.text

    def test_sends_on_success(self):
        config = NotifyConfig(enabled=True, urls=["pover://user@token"], on_success=True)
        result = _success_result()
        mock_ap = MagicMock()
        with patch("app.notify._build_apprise", return_value=mock_ap) as mock_build:
            send_notification(config, result)
        mock_build.assert_called_once_with(["pover://user@token"])
        mock_ap.notify.assert_called_once()
        kwargs = mock_ap.notify.call_args.kwargs
        assert "Sync Complete" in kwargs["title"]
        assert "Added 2" in kwargs["body"]

    def test_success_message_uses_dry_run_wording(self):
        config = NotifyConfig(enabled=True, urls=["pover://user@token"], on_success=True)
        result = _success_result(added=4)
        mock_ap = MagicMock()
        with patch("app.notify._build_apprise", return_value=mock_ap):
            send_notification(config, result, dry_run=True)
        kwargs = mock_ap.notify.call_args.kwargs
        assert "Would add 4" in kwargs["body"]

    def test_skips_success_when_on_success_false(self):
        config = NotifyConfig(enabled=True, urls=["pover://user@token"], on_success=False)
        result = _success_result()
        with patch("app.notify._build_apprise") as mock_build:
            send_notification(config, result)
        mock_build.assert_not_called()

    def test_sends_on_failure(self):
        config = NotifyConfig(enabled=True, urls=["pover://user@token"], on_failure=True)
        result = _failure_result()
        mock_ap = MagicMock()
        with patch("app.notify._build_apprise", return_value=mock_ap):
            send_notification(config, result)
        mock_ap.notify.assert_called_once()
        kwargs = mock_ap.notify.call_args.kwargs
        assert "Sync Failed" in kwargs["title"]

    def test_skips_failure_when_on_failure_false(self):
        config = NotifyConfig(enabled=True, urls=["pover://user@token"], on_failure=False)
        result = _failure_result()
        with patch("app.notify._build_apprise") as mock_build:
            send_notification(config, result)
        mock_build.assert_not_called()

    def test_only_if_added_suppresses_when_nothing_added(self):
        config = NotifyConfig(
            enabled=True, urls=["pover://user@token"], on_success=True, only_if_added=True
        )
        result = _success_result(added=0)
        with patch("app.notify._build_apprise") as mock_build:
            send_notification(config, result)
        mock_build.assert_not_called()

    def test_only_if_added_sends_when_shows_added(self):
        config = NotifyConfig(
            enabled=True, urls=["pover://user@token"], on_success=True, only_if_added=True
        )
        result = _success_result(added=3)
        mock_ap = MagicMock()
        with patch("app.notify._build_apprise", return_value=mock_ap):
            send_notification(config, result)
        mock_ap.notify.assert_called_once()

    def test_only_if_added_does_not_suppress_failure(self):
        config = NotifyConfig(
            enabled=True,
            urls=["pover://user@token"],
            on_failure=True,
            only_if_added=True,
        )
        result = _failure_result(added=0)
        mock_ap = MagicMock()
        with patch("app.notify._build_apprise", return_value=mock_ap):
            send_notification(config, result)
        mock_ap.notify.assert_called_once()

    def test_apprise_exception_caught(self):
        config = NotifyConfig(enabled=True, urls=["bad://url"], on_success=True)
        result = _success_result()
        mock_ap = MagicMock()
        mock_ap.notify.side_effect = RuntimeError("connection refused")
        with patch("app.notify._build_apprise", return_value=mock_ap):
            send_notification(config, result)  # must not raise

    def test_multiple_urls_all_added(self):
        urls = ["pover://user@token", "ntfy://host/topic", "discord://id/token"]
        config = NotifyConfig(enabled=True, urls=urls, on_success=True)
        result = _success_result()
        mock_ap = MagicMock()
        with patch("app.notify._build_apprise", return_value=mock_ap) as mock_build:
            send_notification(config, result)
        mock_build.assert_called_once_with(urls)

    def test_build_apprise_exception_caught(self):
        """If _build_apprise itself raises, send_notification must not propagate."""
        config = NotifyConfig(enabled=True, urls=["pover://user@token"], on_success=True)
        result = _success_result()
        with patch("app.notify._build_apprise", side_effect=RuntimeError("build failed")):
            send_notification(config, result)  # must not raise

    def test_only_if_added_dry_run_sends_when_shows_added(self):
        """only_if_added with dry_run=True should still send when added > 0."""
        config = NotifyConfig(
            enabled=True, urls=["pover://user@token"], on_success=True, only_if_added=True
        )
        result = _success_result(added=2)
        mock_ap = MagicMock()
        with patch("app.notify._build_apprise", return_value=mock_ap):
            send_notification(config, result, dry_run=True)
        mock_ap.notify.assert_called_once()
        assert "Would add 2" in mock_ap.notify.call_args.kwargs["body"]

    def test_only_if_added_dry_run_suppresses_when_nothing_added(self):
        """only_if_added with dry_run=True should still suppress when added == 0."""
        config = NotifyConfig(
            enabled=True, urls=["pover://user@token"], on_success=True, only_if_added=True
        )
        result = _success_result(added=0)
        with patch("app.notify._build_apprise") as mock_build:
            send_notification(config, result, dry_run=True)
        mock_build.assert_not_called()


class TestBuildApprise:
    def test_adds_each_url(self):
        import apprise

        urls = ["pover://user@token", "ntfy://host/topic"]
        with patch.object(apprise.Apprise, "add") as mock_add:
            _build_apprise(urls)
        assert mock_add.call_count == 2
        mock_add.assert_any_call("pover://user@token")
        mock_add.assert_any_call("ntfy://host/topic")

    def test_returns_apprise_instance(self):
        import apprise

        result = _build_apprise([])
        assert isinstance(result, apprise.Apprise)


class TestSuccessMessage:
    def test_title(self):
        result = _success_result()
        title, _ = _success_message(result)
        assert title == "SnakeCharmer: Sync Complete"

    def test_body_contains_added_count(self):
        result = _success_result(added=7)
        _, body = _success_message(result)
        assert "Added 7" in body

    def test_body_contains_dry_run_wording(self):
        result = _success_result(added=3)
        _, body = _success_message(result, dry_run=True)
        assert "Would add 3" in body

    def test_body_contains_duration(self):
        result = _success_result(duration_seconds=3.456)
        _, body = _success_message(result)
        assert "3.5s" in body

    def test_body_contains_all_metrics(self):
        result = _success_result(added=2, unique_shows=5, already_in_medusa=3, skipped=1, failed=0)
        _, body = _success_message(result)
        assert "unique: 5" in body
        assert "already in library: 3" in body
        assert "skipped: 1" in body
        assert "failed: 0" in body


class TestFailureMessage:
    def test_title(self):
        result = _failure_result()
        title, _ = _failure_message(result)
        assert title == "SnakeCharmer: Sync Failed"

    def test_body_contains_failed_count(self):
        result = _failure_result(failed=3)
        _, body = _failure_message(result)
        assert "failed: 3" in body

    def test_body_contains_duration(self):
        result = _failure_result(duration_seconds=0.987)
        _, body = _failure_message(result)
        assert "1.0s" in body
