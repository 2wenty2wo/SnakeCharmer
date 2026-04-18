import queue
import threading
import time
from unittest.mock import MagicMock

import pytest

from app.sync_events import (
    EVT_FINISHED,
    EVT_LOG,
    EVT_STARTED,
    SyncEvent,
    SyncEventBroker,
    make_emitter,
)


class TestSyncEvent:
    def test_is_immutable(self):
        event = SyncEvent(id=1, type=EVT_STARTED, data={"run_id": 1})
        with pytest.raises((AttributeError, TypeError)):
            event.id = 2  # type: ignore[misc]

    def test_has_auto_timestamp(self):
        before = time.time()
        event = SyncEvent(id=1, type=EVT_STARTED, data={})
        after = time.time()
        assert before <= event.ts <= after

    def test_stores_fields(self):
        event = SyncEvent(id=7, type=EVT_LOG, data={"msg": "hi"}, ts=1.0)
        assert event.id == 7
        assert event.type == EVT_LOG
        assert event.data == {"msg": "hi"}
        assert event.ts == 1.0


class TestSyncEventBrokerRunId:
    def test_initial_run_id_is_zero(self):
        broker = SyncEventBroker()
        assert broker.current_run_id == 0

    def test_new_run_bumps_run_id(self):
        broker = SyncEventBroker()
        run_id = broker.new_run()
        assert run_id == 1
        assert broker.current_run_id == 1

    def test_new_run_increments_each_call(self):
        broker = SyncEventBroker()
        assert broker.new_run() == 1
        assert broker.new_run() == 2
        assert broker.new_run() == 3

    def test_new_run_clears_buffer(self):
        broker = SyncEventBroker()
        broker.emit(EVT_STARTED, {})
        broker.emit(EVT_FINISHED, {})
        assert len(broker.snapshot()) == 2
        broker.new_run()
        assert broker.snapshot() == []


class TestSyncEventBrokerEmit:
    def test_returns_sync_event(self):
        broker = SyncEventBroker()
        event = broker.emit(EVT_STARTED, {"key": "val"})
        assert isinstance(event, SyncEvent)
        assert event.type == EVT_STARTED
        assert event.data["key"] == "val"

    def test_auto_injects_run_id(self):
        broker = SyncEventBroker()
        broker.new_run()
        event = broker.emit(EVT_STARTED, {})
        assert event.data["run_id"] == 1

    def test_preserves_caller_run_id(self):
        broker = SyncEventBroker()
        broker.new_run()
        event = broker.emit(EVT_STARTED, {"run_id": 99})
        assert event.data["run_id"] == 99

    def test_none_data_defaults_to_empty_dict(self):
        broker = SyncEventBroker()
        event = broker.emit(EVT_STARTED, None)
        assert isinstance(event.data, dict)

    def test_ids_are_monotonically_increasing(self):
        broker = SyncEventBroker()
        e1 = broker.emit(EVT_STARTED, {})
        e2 = broker.emit(EVT_FINISHED, {})
        assert e2.id > e1.id

    def test_fans_out_to_all_subscribers(self):
        broker = SyncEventBroker()
        q1, unsub1 = broker.subscribe()
        q2, unsub2 = broker.subscribe()
        broker.emit(EVT_STARTED, {"x": 1})
        assert q1.get_nowait().type == EVT_STARTED
        assert q2.get_nowait().type == EVT_STARTED
        unsub1()
        unsub2()

    def test_drops_event_silently_when_subscriber_queue_full(self):
        broker = SyncEventBroker()
        q, unsub = broker.subscribe(maxsize=1)
        broker.emit(EVT_STARTED, {})
        # Queue is full; this second emit must not raise.
        broker.emit(EVT_FINISHED, {})
        assert q.qsize() == 1
        assert q.get_nowait().type == EVT_STARTED
        unsub()

    def test_appends_to_buffer(self):
        broker = SyncEventBroker()
        broker.emit(EVT_STARTED, {})
        broker.emit(EVT_FINISHED, {})
        assert len(broker.snapshot()) == 2


class TestSyncEventBrokerSnapshot:
    def test_returns_all_when_after_id_is_zero(self):
        broker = SyncEventBroker()
        broker.emit(EVT_STARTED, {})
        broker.emit(EVT_FINISHED, {})
        assert len(broker.snapshot(after_id=0)) == 2

    def test_default_after_id_returns_all(self):
        broker = SyncEventBroker()
        broker.emit(EVT_STARTED, {})
        assert len(broker.snapshot()) == 1

    def test_filters_events_by_id(self):
        broker = SyncEventBroker()
        e1 = broker.emit(EVT_STARTED, {})
        e2 = broker.emit(EVT_FINISHED, {})
        result = broker.snapshot(after_id=e1.id)
        assert len(result) == 1
        assert result[0].id == e2.id

    def test_empty_when_after_id_is_last(self):
        broker = SyncEventBroker()
        e1 = broker.emit(EVT_STARTED, {})
        assert broker.snapshot(after_id=e1.id) == []

    def test_empty_broker_returns_empty_list(self):
        broker = SyncEventBroker()
        assert broker.snapshot() == []


class TestSyncEventBrokerSubscribe:
    def test_replays_buffered_events(self):
        broker = SyncEventBroker()
        broker.emit(EVT_STARTED, {"x": 1})
        broker.emit(EVT_FINISHED, {"x": 2})
        q, unsub = broker.subscribe(after_id=0)
        assert q.get_nowait().type == EVT_STARTED
        assert q.get_nowait().type == EVT_FINISHED
        unsub()

    def test_replay_respects_after_id(self):
        broker = SyncEventBroker()
        e1 = broker.emit(EVT_STARTED, {})
        broker.emit(EVT_FINISHED, {})
        q, unsub = broker.subscribe(after_id=e1.id)
        assert q.get_nowait().type == EVT_FINISHED
        with pytest.raises(queue.Empty):
            q.get_nowait()
        unsub()

    def test_replay_stops_when_queue_full(self):
        broker = SyncEventBroker()
        for i in range(5):
            broker.emit(EVT_STARTED, {"i": i})
        q, unsub = broker.subscribe(maxsize=2, after_id=0)
        assert q.qsize() == 2
        unsub()

    def test_live_events_delivered_after_subscribe(self):
        broker = SyncEventBroker()
        q, unsub = broker.subscribe()
        broker.emit(EVT_STARTED, {})
        assert q.get_nowait().type == EVT_STARTED
        unsub()

    def test_subscriber_count_increases(self):
        broker = SyncEventBroker()
        assert broker.subscriber_count() == 0
        _, unsub1 = broker.subscribe()
        assert broker.subscriber_count() == 1
        _, unsub2 = broker.subscribe()
        assert broker.subscriber_count() == 2
        unsub1()
        unsub2()


class TestSyncEventBrokerUnsubscribe:
    def test_removes_from_subscriber_list(self):
        broker = SyncEventBroker()
        _, unsub = broker.subscribe()
        assert broker.subscriber_count() == 1
        unsub()
        assert broker.subscriber_count() == 0

    def test_sends_none_sentinel_to_wake_reader(self):
        broker = SyncEventBroker()
        q, unsub = broker.subscribe()
        unsub()
        assert q.get_nowait() is None

    def test_idempotent(self):
        broker = SyncEventBroker()
        _, unsub = broker.subscribe()
        unsub()
        unsub()  # must not raise
        assert broker.subscriber_count() == 0

    def test_events_after_unsubscribe_not_delivered(self):
        broker = SyncEventBroker()
        q, unsub = broker.subscribe()
        unsub()
        broker.emit(EVT_STARTED, {})
        # Only the None sentinel should be in the queue.
        assert q.get_nowait() is None
        with pytest.raises(queue.Empty):
            q.get_nowait()


class TestSyncEventBrokerBuffer:
    def test_buffer_respects_maxsize(self):
        broker = SyncEventBroker(buffer_size=3)
        for i in range(5):
            broker.emit(EVT_STARTED, {"i": i})
        events = broker.snapshot()
        assert len(events) == 3

    def test_oldest_events_evicted_first(self):
        broker = SyncEventBroker(buffer_size=2)
        broker.emit(EVT_STARTED, {"n": 1})
        broker.emit(EVT_STARTED, {"n": 2})
        broker.emit(EVT_STARTED, {"n": 3})
        events = broker.snapshot()
        ns = [e.data["n"] for e in events]
        assert ns == [2, 3]


class TestSyncEventBrokerThreadSafety:
    def test_concurrent_emits_do_not_raise(self):
        broker = SyncEventBroker()
        errors: list[Exception] = []

        def emit_many():
            try:
                for _ in range(100):
                    broker.emit(EVT_STARTED, {})
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=emit_many) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []

    def test_subscribe_and_unsubscribe_concurrent(self):
        broker = SyncEventBroker()
        errors: list[Exception] = []

        def subscribe_unsub():
            try:
                for _ in range(20):
                    _, unsub = broker.subscribe()
                    unsub()
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=subscribe_unsub) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []


class TestMakeEmitter:
    def test_none_broker_returns_noop(self):
        emitter = make_emitter(None)
        result = emitter(EVT_STARTED, {"x": 1})
        assert result is None

    def test_noop_never_raises(self):
        emitter = make_emitter(None)
        for _ in range(3):
            emitter(EVT_STARTED, {})

    def test_with_broker_emits_event(self):
        broker = SyncEventBroker()
        emitter = make_emitter(broker)
        emitter(EVT_STARTED, {"key": "val"})
        events = broker.snapshot()
        assert len(events) == 1
        assert events[0].type == EVT_STARTED
        assert events[0].data["key"] == "val"

    def test_catches_exception_from_broker(self):
        broker = MagicMock()
        broker.emit.side_effect = RuntimeError("explode")
        broker.current_run_id = 0
        emitter = make_emitter(broker)
        # Must not propagate the exception.
        emitter(EVT_STARTED, {})

    def test_emitter_passes_type_and_data(self):
        broker = SyncEventBroker()
        emitter = make_emitter(broker)
        emitter(EVT_FINISHED, {"result": "ok"})
        event = broker.snapshot()[0]
        assert event.type == EVT_FINISHED
        assert event.data["result"] == "ok"
