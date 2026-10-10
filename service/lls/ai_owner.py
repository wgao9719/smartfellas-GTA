"""Exclusive AI control per database, independent of ordinary Repository readers."""

import asyncio
import os
import threading
import weakref
from pathlib import Path


class AIControlInUse(RuntimeError):
    """Another live AI controller already owns this database."""


def database_identity(repository):
    return (
        ("memory", id(repository))
        if repository.path == ":memory:"
        else ("file", str(Path(repository.path).resolve()))
    )


class AIControlOwner:
    """A Runtime holds this lease until shutdown, including provider replacement."""

    _owners = weakref.WeakValueDictionary()
    # Weak-reference insertion can trigger garbage collection. A finalized
    # owner closes itself on this same thread while registration holds the lock.
    _registry_lock = threading.RLock()

    def __init__(self, repository):
        self.closed = True
        self._file = None
        self.identity = database_identity(repository)
        self.lock = asyncio.Lock()
        self.gates = weakref.WeakSet()
        with self._registry_lock:
            previous = self._owners.get(self.identity)
            if previous is not None and not previous.closed:
                raise AIControlInUse("another active Runtime owns AI control for this database")
            if self.identity[0] == "file":
                # Never unlink this sidecar: a new inode would permit two owners.
                descriptor = os.open(
                    self.identity[1] + "-ai-owner.lock", os.O_RDWR | os.O_CREAT, 0o600
                )
                stream = os.fdopen(descriptor, "r+b", buffering=0)
                try:
                    if os.name == "nt":
                        import msvcrt

                        if os.fstat(descriptor).st_size == 0:
                            stream.write(b"\0")
                        stream.seek(0)
                        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as exc:
                    stream.close()
                    raise AIControlInUse(
                        "another active Runtime owns AI control for this database"
                    ) from exc
                self._file = stream
            self.closed = False
            self._owners[self.identity] = self

    def assert_open(self):
        if self.closed:
            raise RuntimeError("AI control owner is closed")

    def assert_repository(self, repository):
        self.assert_open()
        if database_identity(repository) != self.identity:
            raise ValueError("AI control owner belongs to another database")

    def close(self):
        with self._registry_lock:
            if self.closed:
                return
            self.closed = True
            if self._owners.get(self.identity) is self:
                self._owners.pop(self.identity, None)
            if self._file is not None:
                # Closing releases the OS lock on both supported platforms.
                self._file.close()
                self._file = None

    def __del__(self):
        self.close()
