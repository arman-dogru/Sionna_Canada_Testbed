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
