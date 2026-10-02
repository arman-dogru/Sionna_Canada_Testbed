import multiprocessing
from pathlib import Path

import pytest

from ottawa_rt.jobs import FileJobQueue, SimulationJob


def test_queue_claim_is_atomic_and_resumable(tmp_path):
    queue = FileJobQueue(tmp_path / "queue")
    job = SimulationJob("j1", "r1", "scene.xml", {}, 1900.0, ["s1"], "out.npz")
    queue.enqueue(job)
    claimed = queue.claim()
    assert claimed is not None
    assert queue.claim() is None
    queue.finish(claimed[1], metrics={"elapsed_seconds": 1})
    assert queue.counts() == {"pending": 0, "processing": 0, "done": 1, "failed": 0}


def test_failed_job_can_be_retried_without_duplicate(tmp_path):
    queue = FileJobQueue(tmp_path / "queue")
    job = SimulationJob("j1", "r1", "scene.xml", {}, 1900.0, ["s1"], "out.npz")
    queue.enqueue(job)
    claimed = queue.claim()
    assert claimed is not None
    queue.finish(claimed[1], error="test failure")
    queue.enqueue(job)
    assert queue.counts()["failed"] == 1
    assert queue.counts()["pending"] == 0

    assert queue.retry_failed() == 1
    assert queue.counts()["failed"] == 0
    assert queue.counts()["pending"] == 1


def test_queue_finish_retries_transient_windows_lock(tmp_path, monkeypatch):
    queue = FileJobQueue(tmp_path / "queue")
    job = SimulationJob("j1", "r1", "scene.xml", {}, 1900.0, ["s1"], "out.npz")
    queue.enqueue(job)
    claimed = queue.claim()
    assert claimed is not None

    original_replace = type(claimed[1]).replace
    calls = 0

    def flaky_replace(path, destination):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise PermissionError("transient lock")
        return original_replace(path, destination)

    monkeypatch.setattr(type(claimed[1]), "replace", flaky_replace)
    monkeypatch.setattr("ottawa_rt.jobs.time.sleep", lambda _seconds: None)
    queue.finish(claimed[1], metrics={"elapsed_seconds": 1})
    assert calls == 2
    assert queue.counts()["done"] == 1


@pytest.mark.parametrize("operation", ["read_text", "write_text"])
def test_claim_retries_transient_record_lock(tmp_path, monkeypatch, operation):
    queue = FileJobQueue(tmp_path / "queue")
    queue.enqueue(SimulationJob("j1", "r1", "scene.xml", {}, 1900.0, ["s1"], "out.npz"))
    original = getattr(type(tmp_path), operation)
    calls = 0

    def locked_once(path, *args, **kwargs):
        nonlocal calls
        if path.parent.name == "processing":
            calls += 1
            if calls == 1:
                raise PermissionError("transient record lock")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(type(tmp_path), operation, locked_once)
    monkeypatch.setattr("ottawa_rt.jobs.time.sleep", lambda _seconds: None)
    claimed = queue.claim()
    assert claimed is not None
    assert calls == 2
    assert queue.counts() == {"pending": 0, "processing": 1, "done": 0, "failed": 0}
    assert queue.claim() is None


def _drain_jobs(root, claimed_ids):
    queue = FileJobQueue(Path(root))
    while (claimed := queue.claim()) is not None:
        job, record = claimed
        claimed_ids.put(job.job_id)
        queue.finish(record)


def test_parallel_workers_never_claim_the_same_job(tmp_path):
    queue = FileJobQueue(tmp_path / "queue")
    for index in range(60):
        queue.enqueue(
            SimulationJob(f"j{index:03}", "r1", "scene.xml", {}, 1900.0, ["s1"], "out.npz")
        )
    context = multiprocessing.get_context("spawn")
    claimed_ids = context.Queue()
    processes = [
        context.Process(target=_drain_jobs, args=(str(queue.root), claimed_ids)) for _ in range(4)
    ]
    try:
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=20)
            assert process.exitcode == 0
        ids = [claimed_ids.get(timeout=2) for _ in range(60)]
        assert len(set(ids)) == 60
        assert claimed_ids.empty()
        assert queue.counts() == {"pending": 0, "processing": 0, "done": 60, "failed": 0}
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join()
        claimed_ids.close()


def test_claim_preserves_an_existing_processing_record(tmp_path):
    queue = FileJobQueue(tmp_path / "queue")
    job = SimulationJob("j1", "r1", "scene.xml", {}, 1900.0, ["s1"], "out.npz")
    pending = queue.enqueue(job)
    claimed = queue.claim()
    assert claimed is not None
    original = claimed[1].read_bytes()
    pending.write_text(original.decode(), encoding="utf-8")
    assert queue.claim() is None
    assert claimed[1].read_bytes() == original
