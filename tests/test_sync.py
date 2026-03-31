from unittest.mock import patch

import pytest

from app.config import AppConfig, MedusaConfig, SyncConfig, TraktConfig, TraktSource
from app.sync import SyncResult, _medusa_add_options_from_source, run_sync
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
        yield mock.return_value


@pytest.fixture
def mock_medusa():
    with patch("app.sync.MedusaClient") as mock:
        yield mock.return_value


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
