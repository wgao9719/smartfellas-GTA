"""Temporary folders for live checks that stop a service process.

On Windows, terminating a virtual-environment ``python.exe`` ends its launcher first. The real
interpreter exits moments later and can still hold the database open while the folder is removed.
"""

import shutil
import tempfile
import time
from contextlib import contextmanager


@contextmanager
def temporary_directory(prefix, timeout=15):
    path = tempfile.mkdtemp(prefix=prefix)
    try:
        yield path
    finally:
        deadline = time.monotonic() + timeout
        while True:
            try:
                shutil.rmtree(path)
                break
            except FileNotFoundError:
                break
            except PermissionError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.1)
