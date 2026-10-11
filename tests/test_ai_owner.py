import subprocess
import sys

import pytest

from service.lls.ai_owner import AIControlInUse, AIControlOwner
from service.lls.storage import Repository


def test_owner_excludes_another_controller_but_preserves_repository_readers(tmp_path):
    path = tmp_path / "owner.sqlite3"
    alias = tmp_path / "alias.sqlite3"
    with Repository(path) as first, Repository(path) as reader:
        first.create_profile("reader-visible")
        alias.symlink_to(path)
        with Repository(alias) as alias_reader:
            owner = AIControlOwner(first)
            try:
                assert reader.export_profile("reader-visible")["profile"]
                for repository in (first, reader, alias_reader):
                    with pytest.raises(AIControlInUse, match="active Runtime"):
                        AIControlOwner(repository)
            finally:
                owner.close()
            replacement = AIControlOwner(reader)
            replacement.close()
            assert (tmp_path / "owner.sqlite3-ai-owner.lock").exists()


def test_owner_lock_excludes_another_process_and_releases_without_unlink(tmp_path):
    path = tmp_path / "owner.sqlite3"
    script = """
import sys
from service.lls.ai_owner import AIControlInUse, AIControlOwner
from service.lls.storage import Repository
with Repository(sys.argv[1]) as repo:
    try:
        owner = AIControlOwner(repo)
    except AIControlInUse:
        print('denied')
    else:
        print('acquired')
        owner.close()
"""

    def child():
        result = subprocess.run(
            [sys.executable, "-c", script, str(path)], capture_output=True, text=True, timeout=10
        )
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    with Repository(path) as repo:
        owner = AIControlOwner(repo)
        try:
            assert child() == "denied"
        finally:
            owner.close()
        assert child() == "acquired"


def test_memory_owners_are_scoped_to_repository_and_cannot_cross_databases():
    with Repository(":memory:") as first, Repository(":memory:") as second:
        owner, independent = AIControlOwner(first), AIControlOwner(second)
        try:
            with pytest.raises(AIControlInUse):
                AIControlOwner(first)
            with pytest.raises(ValueError, match="another database"):
                owner.assert_repository(second)
        finally:
            owner.close()
            independent.close()
        with pytest.raises(RuntimeError, match="closed"):
            owner.assert_open()


def test_owner_finalizer_can_run_while_registry_lock_is_held():
    """Provider allocation may trigger GC inside weak-reference registration."""
    program = """
import gc
from service.lls.ai_owner import AIControlOwner
from service.lls.storage import Repository
with Repository() as repository:
    gc.disable()
    owner = AIControlOwner(repository)
    owner.cycle = owner
    del owner
    with AIControlOwner._registry_lock:
        gc.collect()
    replacement = AIControlOwner(repository)
    replacement.close()
print('finalized and reacquired')
"""
    result = subprocess.run(
        [sys.executable, "-c", program], capture_output=True, text=True, timeout=5, check=True
    )
    assert result.stdout.strip() == "finalized and reacquired"
