"""Bounded independent sections, ordered output and draining on cancellation; fake model only."""

from threading import Barrier, Event, Lock

import pytest

from backend.api.tft import worker_processor as processor
from backend.engine.rfp.requirements import SectionResult
from backend.shared.ai import Generation, Usage


def generation():
    return Generation(text="{}", usage=Usage(), model_version=None, finish_reasons=[])


def test_sections_overlap_but_results_and_counts_remain_ordered(monkeypatch):
    barrier = Barrier(3, timeout=3)
    lock = Lock()
    active = peak = 0
    events = []

    def provider(prompt):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        if int(prompt) < 3:
            barrier.wait()
        with lock:
            active -= 1
        return generation()

    def extract(item, provider):
        provider(str(item))
        return SectionResult(section_no=str(item), outcome="VALID")

    monkeypatch.setattr(processor, "extract_section", extract)
    results = processor.extract_sections(list(range(8)), provider, events.append, concurrency=3)
    assert [r.section_no for r in results] == list(map(str, range(8)))
    assert peak == 3 and active == 0
    assert len([e for e in events if "counts" in e]) == 8


def test_lease_loss_stops_new_calls_and_drains_inflight(monkeypatch):
    barrier = Barrier(3, timeout=3)
    finished = []
    entered = []
    lock = Lock()

    class LeaseLost(Exception):
        pass

    def provider(prompt):
        with lock:
            entered.append(prompt)
        barrier.wait()
        with lock:
            finished.append(prompt)
        return generation()

    def emit(_):
        raise LeaseLost("fixed")

    def extract(item, provider):
        # Simulates the product engine's Exception fallback, then another coverage call.
        try:
            provider(str(item))
        except Exception:
            pass
        try:
            provider("must-not-call")
        except Exception:
            pass
        return SectionResult(section_no=str(item), outcome="TRANSPORT_FAILURE")

    monkeypatch.setattr(processor, "extract_section", extract)
    with pytest.raises(LeaseLost):
        processor.extract_sections(list(range(9)), provider, emit, concurrency=3)
    assert sorted(entered) == ["0", "1", "2"]
    assert sorted(finished) == sorted(entered)


def test_fatal_accounting_stop_drains_other_calls_without_submitting_more(monkeypatch):
    barrier = Barrier(3, timeout=3)
    released = Event()
    finished = []

    class Stop(BaseException):
        pass

    def extract(item, _):
        barrier.wait()
        if item == 0:
            released.set()
            raise Stop()
        assert released.wait(3)
        finished.append(item)
        return SectionResult(section_no=str(item), outcome="VALID")

    monkeypatch.setattr(processor, "extract_section", extract)
    with pytest.raises(Stop):
        processor.extract_sections(
            list(range(9)), lambda _: generation(), lambda _: None, concurrency=3
        )
    assert sorted(finished) == [1, 2]


@pytest.mark.parametrize("concurrency", [0, 7, True, 1.5])
def test_bad_parallelism_rejected_before_work(concurrency):
    with pytest.raises(ValueError, match="INVALID_EXTRACTION_CONCURRENCY"):
        processor.extract_sections(
            [], lambda _: generation(), lambda _: None, concurrency=concurrency
        )
