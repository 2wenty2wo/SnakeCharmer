import logging
from unittest.mock import Mock, patch

import pytest

from app.config import AppConfig, MedusaConfig, SyncConfig, TraktConfig, TraktSource
from app.sync import SyncResult, _log_summary, _medusa_add_options_from_source, run_sync
from app.trakt import TraktShow


@pytest.fixture
def config():
    return AppConfig(
        trakt=TraktConfig(client_id="id", sources=[TraktSource(type="trending")]),
        medusa=MedusaConfig(url="http://localhost:8081", api_key="key"),
        sync=SyncConfig(dry_run=False, interval=0),
    )


@pytest.fixture
def mock_trakt():
    with patch("app.sync.TraktClient") as mock:
        instance = mock.return_value
        instance.__enter__.return_value = instance
        yield instance


@pytest.fixture
def mock_medusa():
    with patch("app.sync.MedusaClient") as mock:
        instance = mock.return_value
        instance.__enter__.return_value = instance
        yield instance


class TestRunSync:
    def test_adds_missing_shows(self, config, mock_trakt, mock_medusa):
        mock_trakt.get_shows.side_effect = [
            [
                TraktShow(title="Show A", tvdb_id=1),
                TraktShow(title="Show B", tvdb_id=2),
                TraktShow(title="Show C", tvdb_id=3),
            ]
        ]
        mock_medusa.get_existing_tvdb_ids.return_value = {1}
        mock_medusa.add_show.return_value = True

        run_sync(config)

        assert mock_medusa.add_show.call_count == 2
        mock_medusa.add_show.assert_any_call(2, "Show B", add_options=None)
        mock_medusa.add_show.assert_any_call(3, "Show C", add_options=None)

    def test_returns_sync_result_with_counts(self, config, mock_trakt, mock_medusa):
        mock_trakt.get_shows.side_effect = [
            [
                TraktShow(title="Show A", tvdb_id=1),
                TraktShow(title="Show B", tvdb_id=2),
                TraktShow(title="Show C", tvdb_id=3),
            ]
        ]
        mock_medusa.get_existing_tvdb_ids.return_value = {1}
        mock_medusa.add_show.return_value = True

        result = run_sync(config)

        assert isinstance(result, SyncResult)
        assert result.total_fetched == 3
        assert result.unique_shows == 3
        assert result.already_in_medusa == 1
        assert result.added == 2
        assert result.failed == 0
        assert result.success is True
        assert result.duration_seconds > 0
        assert "trending" in result.per_source

    def test_skips_when_all_in_sync(self, config, mock_trakt, mock_medusa):
        mock_trakt.get_shows.side_effect = [
            [
                TraktShow(title="Show A", tvdb_id=1),
            ]
        ]
        mock_medusa.get_existing_tvdb_ids.return_value = {1}

        run_sync(config)

        mock_medusa.add_show.assert_not_called()

    def test_handles_empty_trakt_list(self, config, mock_trakt, mock_medusa):
        mock_trakt.get_shows.side_effect = [[]]

        run_sync(config)

        mock_medusa.get_existing_tvdb_ids.assert_not_called()

    def test_dry_run_does_not_add(self, config, mock_trakt, mock_medusa):
        config.sync.dry_run = True
        mock_trakt.get_shows.side_effect = [
            [
                TraktShow(title="Show A", tvdb_id=1),
            ]
        ]
        mock_medusa.get_existing_tvdb_ids.return_value = set()

        run_sync(config)

        mock_medusa.add_show.assert_not_called()

    def test_handles_trakt_failure(self, config, mock_trakt, mock_medusa):
        mock_trakt.get_shows.side_effect = Exception("API error")

        result = run_sync(config)  # should not raise

        mock_medusa.get_existing_tvdb_ids.assert_not_called()
        assert result.success is False

    def test_handles_medusa_failure(self, config, mock_trakt, mock_medusa):
        mock_trakt.get_shows.side_effect = [
            [
                TraktShow(title="Show A", tvdb_id=1),
            ]
        ]
        mock_medusa.get_existing_tvdb_ids.side_effect = Exception("Connection refused")

        run_sync(config)  # should not raise

        mock_medusa.add_show.assert_not_called()

    def test_continues_on_individual_add_failure(self, config, mock_trakt, mock_medusa):
        mock_trakt.get_shows.side_effect = [
            [
                TraktShow(title="Show A", tvdb_id=1),
                TraktShow(title="Show B", tvdb_id=2),
            ]
        ]
        mock_medusa.get_existing_tvdb_ids.return_value = set()
        mock_medusa.add_show.side_effect = [Exception("fail"), True]

        run_sync(config)  # should not raise

        assert mock_medusa.add_show.call_count == 2

    def test_deduplicates_across_lists(self, config, mock_trakt, mock_medusa):
        config.trakt.sources = [TraktSource(type="trending"), TraktSource(type="watchlist")]
        mock_trakt.get_shows.side_effect = [
            [TraktShow(title="Show A", tvdb_id=1), TraktShow(title="Show B", tvdb_id=2)],
            [TraktShow(title="Show A Duplicate", tvdb_id=1), TraktShow(title="Show C", tvdb_id=3)],
        ]
        mock_medusa.get_existing_tvdb_ids.return_value = set()
        mock_medusa.add_show.return_value = True

        run_sync(config)

        assert mock_medusa.add_show.call_count == 3

    def test_uses_first_source_options_for_duplicates(self, config, mock_trakt, mock_medusa):
        config.trakt.sources = [
            TraktSource(type="trending"),
            TraktSource(type="watchlist"),
        ]
        config.trakt.sources[0].medusa.quality = "hd"
        config.trakt.sources[1].medusa.quality = "sd"
        config.trakt.sources[1].medusa.required_words = ["internal"]

        mock_trakt.get_shows.side_effect = [
            [TraktShow(title="Show A", tvdb_id=1)],
            [TraktShow(title="Show A Duplicate", tvdb_id=1)],
        ]
        mock_medusa.get_existing_tvdb_ids.return_value = set()
        mock_medusa.add_show.return_value = True

        run_sync(config)

        mock_medusa.add_show.assert_called_once_with(
            1,
            "Show A",
            add_options={"quality": "hd"},
        )

    def test_duplicate_precedence_deterministic_with_different_medusa_options(
        self, config, mock_trakt, mock_medusa
    ):
        config.trakt.sources = [
            TraktSource(type="popular"),
            TraktSource(type="trending"),
        ]
        config.trakt.sources[0].medusa.quality = ["uhd"]
        config.trakt.sources[0].medusa.required_words = ["remux"]
        config.trakt.sources[1].medusa.quality = ["sd"]
        config.trakt.sources[1].medusa.required_words = ["internal"]

        mock_trakt.get_shows.side_effect = [
            [TraktShow(title="Show A (popular)", tvdb_id=1)],
            [TraktShow(title="Show A (trending)", tvdb_id=1)],
        ]
        mock_medusa.get_existing_tvdb_ids.return_value = set()
        mock_medusa.add_show.return_value = True

        run_sync(config)

        mock_medusa.add_show.assert_called_once_with(
            1,
            "Show A (popular)",
            add_options={"quality": ["uhd"], "required_words": ["remux"]},
        )

    def test_passes_source_specific_add_options_into_medusa_client(
        self, config, mock_trakt, mock_medusa
    ):
        config.trakt.sources = [TraktSource(type="trending"), TraktSource(type="popular")]
        config.trakt.sources[0].medusa.quality = "hd"
        config.trakt.sources[1].medusa.required_words = ["proper", "repack"]

        mock_trakt.get_shows.side_effect = [
            [TraktShow(title="Show With Quality", tvdb_id=10)],
            [TraktShow(title="Show With Words", tvdb_id=20)],
        ]
        mock_medusa.get_existing_tvdb_ids.return_value = set()
        mock_medusa.add_show.return_value = True

        run_sync(config)

        assert mock_medusa.add_show.call_count == 2
        mock_medusa.add_show.assert_any_call(
            10,
            "Show With Quality",
            add_options={"quality": "hd"},
        )
        mock_medusa.add_show.assert_any_call(
            20,
            "Show With Words",
            add_options={"required_words": ["proper", "repack"]},
        )

    def test_add_show_false_counts_as_already_exists(self, config, mock_trakt, mock_medusa):
        mock_trakt.get_shows.return_value = [TraktShow(title="Show A", tvdb_id=1)]
        mock_medusa.get_existing_tvdb_ids.return_value = set()
        mock_medusa.add_show.return_value = False

        run_sync(config)

        mock_medusa.add_show.assert_called_once_with(1, "Show A", add_options=None)

    def test_second_source_failure_aborts_sync(self, config, mock_trakt, mock_medusa):
        config.trakt.sources = [TraktSource(type="trending"), TraktSource(type="popular")]
        mock_trakt.get_shows.side_effect = [
            [TraktShow(title="Show A", tvdb_id=1)],
            Exception("API error on second source"),
        ]

        run_sync(config)

        mock_medusa.get_existing_tvdb_ids.assert_not_called()
        mock_medusa.add_show.assert_not_called()

    def test_dry_run_counts_and_skips_add(self, config, mock_trakt, mock_medusa):
        config.sync.dry_run = True
        config.trakt.sources = [TraktSource(type="trending")]
        mock_trakt.get_shows.side_effect = [
            [
                TraktShow(title="Show A", tvdb_id=1),
                TraktShow(title="Show B", tvdb_id=2),
            ]
        ]
        mock_medusa.get_existing_tvdb_ids.return_value = set()

        run_sync(config)

        mock_medusa.add_show.assert_not_called()

    def test_manual_approval_source_without_pending_queue_is_skipped(
        self, config, mock_trakt, mock_medusa
    ):
        config.trakt.sources = [TraktSource(type="trending", auto_approve=False)]
        mock_trakt.get_shows.return_value = [TraktShow(title="Needs Review", tvdb_id=77)]
        mock_medusa.get_existing_tvdb_ids.return_value = set()

        result = run_sync(config)

        mock_medusa.add_show.assert_not_called()
        assert result.added == 0
        assert result.queued == 0
        assert result.skipped == 1

    def test_manual_approval_source_uses_pending_queue_when_available(
        self, config, mock_trakt, mock_medusa
    ):
        config.trakt.sources = [TraktSource(type="trending", auto_approve=False)]
        mock_trakt.get_shows.return_value = [TraktShow(title="Needs Review", tvdb_id=77)]
        mock_medusa.get_existing_tvdb_ids.return_value = set()
        pending_queue = Mock()
        pending_queue.is_pending.return_value = False
        pending_queue.add_show.return_value = True

        result = run_sync(config, pending_queue=pending_queue)

        mock_medusa.add_show.assert_not_called()
        pending_queue.is_pending.assert_called_once_with(77)
        pending_queue.add_show.assert_called_once()
        assert result.added == 0
        assert result.queued == 1
        assert result.skipped == 0

    def test_already_pending_show_is_skipped(self, config, mock_trakt, mock_medusa):
        """A show already in the pending queue should be skipped, not re-queued."""
        config.trakt.sources = [TraktSource(type="trending", auto_approve=False)]
        mock_trakt.get_shows.return_value = [TraktShow(title="Already Queued", tvdb_id=42)]
        mock_medusa.get_existing_tvdb_ids.return_value = set()
        pending_queue = Mock()
        pending_queue.is_pending.return_value = True

        result = run_sync(config, pending_queue=pending_queue)

        pending_queue.is_pending.assert_called_once_with(42)
        pending_queue.add_show.assert_not_called()
        mock_medusa.add_show.assert_not_called()
        assert result.skipped == 1
        assert result.queued == 0

    def test_dry_run_with_manual_approval_logs_would_queue(self, config, mock_trakt, mock_medusa):
        """Dry-run mode with auto_approve=False should count as queued but not actually queue."""
        config.sync.dry_run = True
        config.trakt.sources = [TraktSource(type="trending", auto_approve=False)]
        mock_trakt.get_shows.return_value = [TraktShow(title="Dry Queue", tvdb_id=55)]
        mock_medusa.get_existing_tvdb_ids.return_value = set()
        pending_queue = Mock()
        pending_queue.is_pending.return_value = False

        result = run_sync(config, pending_queue=pending_queue)

        pending_queue.add_show.assert_not_called()
        mock_medusa.add_show.assert_not_called()
        assert result.queued == 1
        assert result.added == 0

    def test_pending_queue_add_returns_false_counts_as_skipped(
        self, config, mock_trakt, mock_medusa
    ):
        """If add_show returns False (concurrent duplicate), the show should be skipped."""
        config.trakt.sources = [TraktSource(type="trending", auto_approve=False)]
        mock_trakt.get_shows.return_value = [TraktShow(title="Race Condition", tvdb_id=99)]
        mock_medusa.get_existing_tvdb_ids.return_value = set()
        pending_queue = Mock()
        pending_queue.is_pending.return_value = False
        pending_queue.add_show.return_value = False  # concurrent add beat us

        result = run_sync(config, pending_queue=pending_queue)

        pending_queue.add_show.assert_called_once()
        mock_medusa.add_show.assert_not_called()
        assert result.skipped == 1
        assert result.queued == 0


class TestMedusaAddOptionsFromSource:
    def test_returns_none_for_missing_source(self):
        assert _medusa_add_options_from_source(None) is None

    def test_returns_none_for_source_with_no_options_set(self):
        source = TraktSource(type="trending")
        assert _medusa_add_options_from_source(source) is None

    def test_returns_quality_only(self):
        source = TraktSource(type="trending")
        source.medusa.quality = "hd"
        result = _medusa_add_options_from_source(source)
        assert result == {"quality": "hd"}
        assert "required_words" not in result

    def test_returns_required_words_only(self):
        source = TraktSource(type="trending")
        source.medusa.required_words = ["proper"]
        result = _medusa_add_options_from_source(source)
        assert result == {"required_words": ["proper"]}
        assert "quality" not in result


class TestLogSummary:
    def test_no_dry_run_prefix(self, caplog):
        result = SyncResult(
            added=2,
            skipped=1,
            failed=0,
            duration_seconds=1.5,
            per_source={"trending": 3},
            unique_shows=5,
            already_in_medusa=2,
        )
        with caplog.at_level(logging.INFO, logger="app.sync"):
            _log_summary(result, dry_run=False)
        assert "[DRY RUN]" not in caplog.text
        assert "Sync complete" in caplog.text

    def test_dry_run_prefix_present(self, caplog):
        result = SyncResult(added=3, duration_seconds=0.5, per_source={"trending": 3})
        with caplog.at_level(logging.INFO, logger="app.sync"):
            _log_summary(result, dry_run=True)
        assert "[DRY RUN] Sync complete" in caplog.text

    def test_per_source_summary_formatted(self, caplog):
        result = SyncResult(per_source={"trending": 5, "popular": 3}, duration_seconds=1.0)
        with caplog.at_level(logging.INFO, logger="app.sync"):
            _log_summary(result, dry_run=False)
        assert "trending=5" in caplog.text
        assert "popular=3" in caplog.text

    def test_missing_count_is_sum_of_added_skipped_failed(self, caplog):
        result = SyncResult(added=2, skipped=1, failed=3, duration_seconds=1.0, per_source={})
        with caplog.at_level(logging.INFO, logger="app.sync"):
            _log_summary(result, dry_run=False)
        assert "missing: 6" in caplog.text

    def test_all_metrics_present_in_log(self, caplog):
        result = SyncResult(
            added=4,
            skipped=2,
            failed=1,
            unique_shows=10,
            already_in_medusa=3,
            duration_seconds=2.25,
            per_source={"watchlist": 7},
        )
        with caplog.at_level(logging.INFO, logger="app.sync"):
            _log_summary(result, dry_run=False)
        log_text = caplog.text
        assert "unique: 10" in log_text
        assert "in library: 3" in log_text
        assert "added: 4" in log_text
        assert "skipped: 2" in log_text
        assert "failed: 1" in log_text
        assert "2.2s" in log_text

    def test_empty_per_source_produces_empty_sources_string(self, caplog):
        result = SyncResult(duration_seconds=0.1, per_source={})
        with caplog.at_level(logging.INFO, logger="app.sync"):
            _log_summary(result, dry_run=False)
        assert "sources:  |" in caplog.text


class TestDeduplicationWithManySources:
    """Stress-test deduplication with 3+ sources and overlapping shows."""

    def test_three_sources_first_wins(self, config, mock_trakt, mock_medusa):
        """With 3 sources each contributing the same show, first source's options are used."""
        config.trakt.sources = [
            TraktSource(type="trending"),
            TraktSource(type="popular"),
            TraktSource(type="watched"),
        ]
        config.trakt.sources[0].medusa.quality = "hd720p"
        config.trakt.sources[1].medusa.quality = "hd1080p"
        config.trakt.sources[2].medusa.quality = "sd"

        # Same show in all three sources
        mock_trakt.get_shows.side_effect = [
            [TraktShow(title="Show A (trending)", tvdb_id=100)],
            [TraktShow(title="Show A (popular)", tvdb_id=100)],
            [TraktShow(title="Show A (watched)", tvdb_id=100)],
        ]
        mock_medusa.get_existing_tvdb_ids.return_value = set()
        mock_medusa.add_show.return_value = True

        result = run_sync(config)

        # Only added once, with first source's options
        mock_medusa.add_show.assert_called_once_with(
            100,
            "Show A (trending)",
            add_options={"quality": "hd720p"},
        )
        assert result.added == 1
        assert result.unique_shows == 1
        assert result.total_fetched == 3

    def test_three_sources_different_shows_all_added(self, config, mock_trakt, mock_medusa):
        config.trakt.sources = [
            TraktSource(type="trending"),
            TraktSource(type="popular"),
            TraktSource(type="watched"),
        ]
        mock_trakt.get_shows.side_effect = [
            [TraktShow(title="Show A", tvdb_id=1)],
            [TraktShow(title="Show B", tvdb_id=2)],
            [TraktShow(title="Show C", tvdb_id=3)],
        ]
        mock_medusa.get_existing_tvdb_ids.return_value = set()
        mock_medusa.add_show.return_value = True

        result = run_sync(config)

        assert mock_medusa.add_show.call_count == 3
        assert result.added == 3
        assert result.unique_shows == 3

    def test_partial_overlap_across_three_sources(self, config, mock_trakt, mock_medusa):
        """Shows overlap partially: A in src 1+2, B in src 2+3, C only in src 3."""
        config.trakt.sources = [
            TraktSource(type="trending"),
            TraktSource(type="popular"),
            TraktSource(type="watched"),
        ]
        config.trakt.sources[0].medusa.quality = "hd"
        config.trakt.sources[1].medusa.quality = "sd"
        config.trakt.sources[2].medusa.required_words = ["internal"]

        mock_trakt.get_shows.side_effect = [
            [TraktShow(title="Show A", tvdb_id=10)],
            [TraktShow(title="Show A dupe", tvdb_id=10), TraktShow(title="Show B", tvdb_id=20)],
            [TraktShow(title="Show B dupe", tvdb_id=20), TraktShow(title="Show C", tvdb_id=30)],
        ]
        mock_medusa.get_existing_tvdb_ids.return_value = set()
        mock_medusa.add_show.return_value = True

        result = run_sync(config)

        assert result.unique_shows == 3
        assert result.total_fetched == 5
        assert mock_medusa.add_show.call_count == 3

        # Show A: first seen in trending → quality=hd
        mock_medusa.add_show.assert_any_call(10, "Show A", add_options={"quality": "hd"})
        # Show B: first seen in popular → quality=sd
        mock_medusa.add_show.assert_any_call(20, "Show B", add_options={"quality": "sd"})
        # Show C: first seen in watched → required_words=["internal"]
        mock_medusa.add_show.assert_any_call(
            30,
            "Show C",
            add_options={"required_words": ["internal"]},
        )

    def test_failed_source_excludes_all_shows(self, config, mock_trakt, mock_medusa):
        """If any source fails, sync aborts — no partial results from earlier sources."""
        config.trakt.sources = [
            TraktSource(type="trending"),
            TraktSource(type="popular"),
            TraktSource(type="watched"),
        ]
        mock_trakt.get_shows.side_effect = [
            [TraktShow(title="Show A", tvdb_id=1)],
            [TraktShow(title="Show B", tvdb_id=2)],
            Exception("API timeout on third source"),
        ]

        result = run_sync(config)

        assert result.success is False
        mock_medusa.get_existing_tvdb_ids.assert_not_called()
        mock_medusa.add_show.assert_not_called()

    def test_empty_sources_list_returns_success_with_no_shows(self, mock_trakt, mock_medusa):
        """Config with empty sources list should succeed with 0 shows."""
        config = AppConfig(
            trakt=TraktConfig(client_id="id", sources=[]),
            medusa=MedusaConfig(url="http://localhost:8081", api_key="key"),
            sync=SyncConfig(dry_run=False, interval=0),
        )

        result = run_sync(config)

        assert result.success is True
        assert result.unique_shows == 0
        assert result.total_fetched == 0
        mock_trakt.get_shows.assert_not_called()
        mock_medusa.get_existing_tvdb_ids.assert_not_called()
