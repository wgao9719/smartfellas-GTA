"""Online backups publish standalone files without blocking WAL writers."""

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

import pytest

from service.lls import storage
from service.lls.storage import Repository


def source_connection(monkeypatch, connection_type):
    connect = sqlite3.connect

    def configured_connect(database, *args, **kwargs):
        if kwargs.get("uri"):
            kwargs["factory"] = connection_type
        return connect(database, *args, **kwargs)

    monkeypatch.setattr(storage.sqlite3, "connect", configured_connect)


def assert_restored(path, actor="avery"):
    with closing(sqlite3.connect(path)) as snapshot:
        assert snapshot.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert snapshot.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert snapshot.execute(
            "SELECT actor_id FROM actors WHERE actor_id=?", (actor,)
        ).fetchone() == (actor,)
    with Repository(path) as reopened:
        assert reopened.get_actor("pilot", actor) is not None


def test_backup_restores_committed_wal_without_sidecars(tmp_path):
    with Repository(tmp_path / "live.sqlite3") as repository:
        repository.register_actor("pilot", {"actor_id": "avery"})
        assert (tmp_path / "live.sqlite3-wal").stat().st_size > 0
        destination = tmp_path / "backups" / "snapshot.sqlite3"
        repository.backup(destination)
        assert list(destination.parent.iterdir()) == [destination]
        assert_restored(destination)


@pytest.mark.parametrize("failure", ["copy", "fsync", "publish"])
def test_failed_backup_preserves_existing_snapshot_and_cleans_partial(
    tmp_path, monkeypatch, failure
):
    with Repository(tmp_path / "live.sqlite3") as repository:
        repository.register_actor("pilot", {"actor_id": "avery"})
        destination = tmp_path / "backups" / "snapshot.sqlite3"
        repository.backup(destination)
        original = destination.read_bytes()
        repository.register_actor("pilot", {"actor_id": "bea"})

        if failure == "copy":
            class FailedCopy(sqlite3.Connection):
                def backup(self, target, **kwargs):
                    target.execute("CREATE TABLE incomplete (value TEXT)")
                    target.commit()
                    raise sqlite3.OperationalError("simulated interrupted copy")

            source_connection(monkeypatch, FailedCopy)
            error = sqlite3.OperationalError
        else:
            def fail(*args):
                raise OSError("simulated publish failure")

            monkeypatch.setattr(storage.os, "fsync" if failure == "fsync" else "replace", fail)
            error = OSError

        with pytest.raises(error, match="simulated"):
            repository.backup(destination)
        assert destination.read_bytes() == original
        assert list(destination.parent.iterdir()) == [destination]
        assert_restored(destination)


def test_backup_does_not_publish_partial_file(tmp_path, monkeypatch):
    with Repository(tmp_path / "live.sqlite3") as repository:
        repository.register_actor("pilot", {"actor_id": "avery"})
        destination = tmp_path / "backups" / "snapshot.sqlite3"
        replace = storage.os.replace
        published = []

        def checked_replace(source, target):
            assert source.parent == destination.parent
            assert not destination.exists()
            with closing(sqlite3.connect(source)) as partial:
                assert partial.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
                assert partial.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
            published.append(target)
            replace(source, target)

        monkeypatch.setattr(storage.os, "replace", checked_replace)
        repository.backup(destination)
        assert published == [destination]
        assert list(destination.parent.iterdir()) == [destination]
        assert_restored(destination)


def test_repository_writes_continue_during_online_backup(tmp_path, monkeypatch):
    copying = threading.Event()
    resume = threading.Event()

    class PausedCopy(sqlite3.Connection):
        def backup(self, target, **kwargs):
            def progress(status, remaining, total):
                if not copying.is_set() and remaining:
                    copying.set()
                    assert resume.wait(5)

            super().backup(target, pages=1, progress=progress)

    with Repository(tmp_path / "live.sqlite3") as repository:
        repository.register_actor("pilot", {"actor_id": "avery"})
        source_connection(monkeypatch, PausedCopy)
        destination = tmp_path / "backups" / "snapshot.sqlite3"
        with ThreadPoolExecutor(max_workers=2) as workers:
            backup = workers.submit(repository.backup, destination)
            try:
                assert copying.wait(5)
                assert not destination.exists()
                write = workers.submit(
                    repository.register_actor, "pilot", {"actor_id": "bea"}
                )
                write.result(timeout=5)
                assert not backup.done()
            finally:
                resume.set()
            backup.result(timeout=5)
        assert repository.get_actor("pilot", "bea") is not None
        assert_restored(destination)


def test_backup_finishes_with_point_in_time_state_during_sustained_writes(tmp_path, monkeypatch):
    copy_steps = 0

    class BusyCopy(sqlite3.Connection):
        def backup(self, target, **kwargs):
            def progress(status, remaining, total):
                nonlocal copy_steps
                copy_steps += 1
                assert copy_steps <= total * 2, "concurrent writes keep restarting the copy"
                if remaining:
                    repository.register_actor(
                        "pilot", {"actor_id": "avery", "display_name": f"write-{copy_steps}"}
                    )

            super().backup(target, pages=1, progress=progress)

    with Repository(tmp_path / "live.sqlite3") as repository:
        repository.register_actor("pilot", {"actor_id": "avery", "display_name": "original"})
        source_connection(monkeypatch, BusyCopy)
        destination = tmp_path / "snapshot.sqlite3"
        repository.backup(destination)
        assert copy_steps > 1
        assert repository.get_actor("pilot", "avery")["display_name"].startswith("write-")
        # The completed backup releases its WAL reader, allowing truncation immediately.
        checkpoint = repository.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        assert tuple(checkpoint) == (0, 0, 0)
        assert_restored(destination)
        with Repository(destination) as reopened:
            assert reopened.get_actor("pilot", "avery")["display_name"] == "original"


def test_backups_are_serialized(tmp_path, monkeypatch):
    copying = threading.Event()
    second_started = threading.Event()
    second_copy = threading.Event()
    resume = threading.Event()

    class PausedCopy(sqlite3.Connection):
        def backup(self, target, **kwargs):
            if copying.is_set():
                second_copy.set()
            else:
                copying.set()
                assert resume.wait(5)
            super().backup(target, **kwargs)

    with Repository(tmp_path / "live.sqlite3") as repository:
        repository.register_actor("pilot", {"actor_id": "avery"})
        source_connection(monkeypatch, PausedCopy)
        destinations = [tmp_path / f"snapshot-{number}.sqlite3" for number in (1, 2)]

        def second_backup():
            second_started.set()
            repository.backup(destinations[1])

        with ThreadPoolExecutor(max_workers=2) as workers:
            first = workers.submit(repository.backup, destinations[0])
            try:
                assert copying.wait(5)
                second = workers.submit(second_backup)
                assert second_started.wait(5)
                assert not second_copy.wait(0.1)
            finally:
                resume.set()
            first.result(timeout=5)
            second.result(timeout=5)
        assert second_copy.is_set()
        for destination in destinations:
            assert_restored(destination)


def test_in_memory_backup_uses_live_connection(tmp_path):
    with Repository() as repository:
        repository.register_actor("pilot", {"actor_id": "avery"})
        destination = tmp_path / "snapshot.sqlite3"
        repository.backup(destination)
        assert_restored(destination)


def test_backup_rejects_live_database_as_destination(tmp_path):
    with Repository(tmp_path / "live.sqlite3") as repository:
        with pytest.raises(ValueError, match="live database"):
            repository.backup(repository.path)
