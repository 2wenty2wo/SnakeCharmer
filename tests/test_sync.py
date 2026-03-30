from unittest.mock import patch

import pytest

from app.config import AppConfig, MedusaConfig, SyncConfig, TraktConfig, TraktSource
from app.sync import run_sync
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
        mock_medusa.add_show.assert_any_call(2, "Show B")
        mock_medusa.add_show.assert_any_call(3, "Show C")

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

        run_sync(config)  # should not raise

        mock_medusa.get_existing_tvdb_ids.assert_not_called()

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
