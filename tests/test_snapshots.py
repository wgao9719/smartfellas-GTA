import asyncio
import sqlite3
import threading
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from service.lls.runtime import Runtime
from service.lls.settings import Settings
from service.lls.storage import Repository


async def test_maintenance_produces_restorable_snapshot_over_time(tmp_path, monkeypatch):
    clock = [0.0]
    ticks = 0

    async def advance_time(seconds):
        nonlocal ticks
        ticks += 1
        if ticks > 1:
            raise asyncio.CancelledError
        clock[0] += 3600

    with Repository(tmp_path / "live.sqlite3") as repository:
        repository.create_profile("snapshot-profile")
        runtime = Runtime(Settings(), repository, clock=lambda: clock[0])
        try:
            with monkeypatch.context() as patch:
                patch.setattr("service.lls.runtime.asyncio.sleep", advance_time)
                with pytest.raises(asyncio.CancelledError):
                    await runtime.maintain()
            await runtime.snapshot_task
            snapshots = list((tmp_path / "snapshots").glob("*.sqlite3"))
            assert len(snapshots) == 1
            with closing(sqlite3.connect(snapshots[0])) as restored:
                assert restored.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
                assert restored.execute("SELECT profile_id FROM profiles").fetchall() == [
                    ("snapshot-profile",)
                ]
        finally:
            await runtime.shutdown()


@pytest.fixture
async def snapshots(tmp_path):
    clock = [0.0]
    settings = Settings(snapshot_interval_seconds=10, snapshot_retention_count=2)
    with Repository(tmp_path / "live.sqlite3") as repository:
        runtime = Runtime(
            settings, repository, clock=lambda: clock[0],
            utc_now=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=clock[0]),
        )
        try:
            yield runtime, clock, tmp_path / "snapshots"
        finally:
            await runtime.shutdown()


async def snapshot_at(runtime, clock, seconds):
    clock[0] = seconds
    previous = runtime.snapshot_task
    runtime.snapshot_if_due()
    if runtime.snapshot_task is not None:
        await runtime.snapshot_task
    return runtime.snapshot_task is not previous


async def test_snapshot_cadence_and_rotation_preserve_manual_backups(snapshots):
    runtime, clock, directory = snapshots
    manual = directory.parent / "backups" / "lls-manual.sqlite3"
    await runtime.db("backup", manual)
    manual_bytes = manual.read_bytes()
    assert not await snapshot_at(runtime, clock, 0)
    assert not await snapshot_at(runtime, clock, 9.99)
    assert not directory.exists()
    paths = []
    for seconds in (10, 20, 1000):
        runtime.repo.create_profile(f"profile-{seconds}")
        assert await snapshot_at(runtime, clock, seconds)
        paths.append(runtime.snapshot_last_success_path)
        assert len(list(directory.glob("*.sqlite3"))) == min(len(paths), 2)
        assert not await snapshot_at(runtime, clock, seconds)
        assert not await snapshot_at(runtime, clock, seconds + 9.99)
    assert {str(path) for path in directory.glob("*.sqlite3")} == set(paths[-2:])
    assert manual.read_bytes() == manual_bytes
    with closing(sqlite3.connect(paths[-1])) as restored:
        assert restored.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert restored.execute("SELECT count(*) FROM profiles").fetchone()[0] == 3


async def test_snapshot_failure_preserves_last_success_and_retries_on_interval(
    snapshots, monkeypatch,
):
    runtime, clock, directory = snapshots
    await snapshot_at(runtime, clock, 10)
    original = next(directory.glob("*.sqlite3"))
    original_bytes = original.read_bytes()
    last_success = runtime.snapshot_diagnostics()["last_success_at"]
    backup = runtime.repo.backup

    def fail(destination):
        raise sqlite3.OperationalError("private database contents")

    monkeypatch.setattr(runtime.repo, "backup", fail)
    assert await snapshot_at(runtime, clock, 20)
    failed = runtime.snapshot_diagnostics()
    assert failed["last_error_type"] == "OperationalError"
    assert failed["last_failure_at"] == runtime.utc_now().isoformat()
    assert failed["last_success_at"] == last_success
    assert "private database contents" not in str(failed)
    assert original.read_bytes() == original_bytes
    assert list(directory.iterdir()) == [original]
    assert not await snapshot_at(runtime, clock, 29.99)
    monkeypatch.setattr(runtime.repo, "backup", backup)
    assert await snapshot_at(runtime, clock, 30)
    recovered = runtime.snapshot_diagnostics()
    assert recovered["last_success_at"] == runtime.utc_now().isoformat()
    assert recovered["last_failure_at"] == failed["last_failure_at"]
    assert len(list(directory.glob("*.sqlite3"))) == 2


async def test_slow_snapshot_does_not_block_loop_overlap_or_outlive_shutdown(
    snapshots, monkeypatch,
):
    runtime, clock, directory = snapshots
    entered, release = threading.Event(), threading.Event()
    backup = runtime.repo.backup
    calls = []

    def slow_backup(destination):
        calls.append(destination)
        entered.set()
        assert release.wait(5)
        backup(destination)

    monkeypatch.setattr(runtime.repo, "backup", slow_backup)
    clock[0] = 10
    runtime.snapshot_if_due()
    task = runtime.snapshot_task
    shutdown = None
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        for seconds in (20, 30, 40):
            clock[0] = seconds
            runtime.snapshot_if_due()
            assert runtime.snapshot_task is task
        # Both event-loop work and normal repository writes progress during the backup.
        await runtime.db("create_profile", "while-backing-up")
        assert runtime.snapshot_diagnostics()["running"]
        shutdown = asyncio.create_task(runtime.shutdown())
        await asyncio.sleep(0)
        assert runtime.snapshot_stopping
        assert not shutdown.done()
        assert len(calls) == 1
    finally:
        release.set()
        await task
        if shutdown is not None:
            await shutdown
    assert len(list(directory.glob("*.sqlite3"))) == 1
    clock[0] = 100
    runtime.snapshot_if_due()
    assert runtime.snapshot_task is task


async def test_rotation_failure_keeps_valid_snapshots_and_retries(snapshots, monkeypatch):
    runtime, clock, directory = snapshots
    runtime.settings.snapshot_retention_count = 1
    await snapshot_at(runtime, clock, 10)
    original = next(directory.glob("*.sqlite3"))
    original_bytes = original.read_bytes()
    unlink = Path.unlink

    def fail_old_snapshot(path, *args, **kwargs):
        if path == original:
            raise PermissionError("snapshot is in use")
        return unlink(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", fail_old_snapshot)
        assert await snapshot_at(runtime, clock, 20)
    assert runtime.snapshot_diagnostics()["last_error_type"] == "PermissionError"
    assert original.read_bytes() == original_bytes
    assert len(list(directory.glob("*.sqlite3"))) == 2
    for path in directory.glob("*.sqlite3"):
        with closing(sqlite3.connect(path)) as restored:
            assert restored.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    await runtime.db("create_profile", "after-rotation-failure")
    assert await snapshot_at(runtime, clock, 30)
    assert len(list(directory.glob("*.sqlite3"))) == 1
    assert not original.exists()


@pytest.mark.parametrize("in_memory", [False, True])
async def test_disabled_or_in_memory_database_never_snapshots(tmp_path, monkeypatch, in_memory):
    clock = [0.0]
    settings = Settings(snapshot_interval_seconds=10 if in_memory else 0)
    with Repository(":memory:" if in_memory else tmp_path / "live.sqlite3") as repository:
        runtime = Runtime(settings, repository, clock=lambda: clock[0])
        try:
            def forbidden(destination):
                pytest.fail("disabled snapshots must not call backup")

            monkeypatch.setattr(repository, "backup", forbidden)
            for seconds in (0, 10, 100000):
                clock[0] = seconds
                runtime.snapshot_if_due()
            assert runtime.snapshot_task is None
            assert runtime.snapshot_diagnostics()["enabled"] is False
            assert not (tmp_path / "snapshots").exists()
        finally:
            await runtime.shutdown()
