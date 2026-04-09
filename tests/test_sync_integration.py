from unittest.mock import patch

from app.config import AppConfig, MedusaConfig, SyncConfig, TraktConfig, TraktSource
from app.pending_queue import PendingQueue
from app.sync import run_sync
from app.trakt import TraktShow


def _base_config(*, sources: list[TraktSource], config_dir: str) -> AppConfig:
    return AppConfig(
        trakt=TraktConfig(client_id="id", sources=sources),
        medusa=MedusaConfig(url="http://localhost:8081", api_key="key"),
        sync=SyncConfig(dry_run=False, interval=0),
        config_dir=config_dir,
    )


def test_run_sync_multisource_dedup_options_queue_and_counters(tmp_path):
    sources = [
        TraktSource(type="trending", auto_approve=True),
        TraktSource(
            type="user_list",
            owner="alice",
            list_slug="curated",
            auth=True,
            auto_approve=False,
        ),
        TraktSource(type="popular", auto_approve=True),
    ]
    sources[0].medusa.quality = "hd1080p"
    sources[1].medusa.quality = "sd"
    sources[2].medusa.quality = "uhd"

    config = _base_config(sources=sources, config_dir=str(tmp_path))
    pending_queue = PendingQueue(config_dir=str(tmp_path))

    with (
        patch("app.sync.TraktClient.get_shows") as mock_get_shows,
        patch("app.sync.MedusaClient.get_existing_tvdb_ids") as mock_existing_ids,
        patch("app.sync.MedusaClient.add_show") as mock_add_show,
    ):
        mock_get_shows.side_effect = [
            [
                TraktShow(title="Alpha", tvdb_id=100),
                TraktShow(title="Beta", tvdb_id=200),
            ],
            [
                TraktShow(title="Alpha (auth duplicate)", tvdb_id=100),
                TraktShow(title="Gamma", tvdb_id=300),
            ],
            [
                TraktShow(title="Alpha (popular duplicate)", tvdb_id=100),
                TraktShow(title="Delta", tvdb_id=400),
            ],
        ]
        mock_existing_ids.return_value = {400}
        mock_add_show.return_value = True

        result = run_sync(config, pending_queue=pending_queue)

    # Dedupe is by TVDB ID (6 fetched -> 4 unique, with one existing in Medusa).
    assert result.total_fetched == 6
    assert result.unique_shows == 4
    assert result.already_in_medusa == 1

    # First source in config order controls add options for duplicates.
    mock_add_show.assert_any_call(100, "Alpha", add_options={"quality": "hd1080p"})
    mock_add_show.assert_any_call(200, "Beta", add_options={"quality": "hd1080p"})
    assert mock_add_show.call_count == 2

    # auto_approve=False source routes through PendingQueue.
    queued = pending_queue.get_pending()
    assert len(queued) == 1
    assert queued[0].tvdb_id == 300
    assert queued[0].source_label == "user_list:alice/curated (auth)"
    assert queued[0].quality == "sd"

    # Per-source and summary counters stay internally consistent.
    assert result.per_source == {
        "trending": 2,
        "user_list:alice/curated (auth)": 2,
        "popular": 2,
    }
    assert result.added == 2
    assert result.queued == 1
    assert result.skipped == 0
    assert result.failed == 0
    assert result.success is True


def test_run_sync_regression_mixed_auth_and_public_source_labels_are_preserved(tmp_path):
    sources = [
        TraktSource(
            type="user_list",
            owner="bob",
            list_slug="private-watch",
            auth=True,
            auto_approve=False,
        ),
        TraktSource(type="trending", auto_approve=False),
        TraktSource(
            type="user_list",
            owner="charlie",
            list_slug="community-picks",
            auth=False,
            auto_approve=False,
        ),
    ]
    sources[0].medusa.quality = "sd"
    sources[1].medusa.quality = "hd"
    sources[2].medusa.quality = "uhd"

    config = _base_config(sources=sources, config_dir=str(tmp_path))
    pending_queue = PendingQueue(config_dir=str(tmp_path))

    with (
        patch("app.sync.TraktClient.get_shows") as mock_get_shows,
        patch("app.sync.MedusaClient.get_existing_tvdb_ids") as mock_existing_ids,
        patch("app.sync.MedusaClient.add_show") as mock_add_show,
    ):
        mock_get_shows.side_effect = [
            [TraktShow(title="Secret Show", tvdb_id=501)],
            [
                TraktShow(title="Secret Show (public duplicate)", tvdb_id=501),
                TraktShow(title="Public Queue", tvdb_id=502),
            ],
            [TraktShow(title="Public Queue (user list duplicate)", tvdb_id=502)],
        ]
        mock_existing_ids.return_value = set()

        result = run_sync(config, pending_queue=pending_queue)

    mock_add_show.assert_not_called()

    pending_by_tvdb = {show.tvdb_id: show for show in pending_queue.get_pending()}
    assert set(pending_by_tvdb) == {501, 502}

    # 501 should keep auth source label/metadata from first source.
    assert pending_by_tvdb[501].source_label == "user_list:bob/private-watch (auth)"
    assert pending_by_tvdb[501].source_type == "user_list"
    assert pending_by_tvdb[501].quality == "sd"

    # 502 should keep public source label/metadata from first source.
    assert pending_by_tvdb[502].source_label == "trending"
    assert pending_by_tvdb[502].source_type == "trending"
    assert pending_by_tvdb[502].quality == "hd"

    assert result.per_source == {
        "user_list:bob/private-watch (auth)": 1,
        "trending": 2,
        "user_list:charlie/community-picks": 1,
    }
    assert result.total_fetched == 4
    assert result.unique_shows == 2
    assert result.already_in_medusa == 0
    assert result.added == 0
    assert result.queued == 2
    assert result.skipped == 0
    assert result.failed == 0
    assert result.success is True
