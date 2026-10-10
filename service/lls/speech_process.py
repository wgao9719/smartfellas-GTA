"""Owned subprocess trees for explicitly configured local speech executables.

Windows starts the child suspended, assigns a kill-on-close Job Object, then
resumes its first thread. POSIX starts a new process session. Temporary files
avoid inherited pipe handles preventing asynchronous cleanup.
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import tempfile
import time


class ProcessFailure(RuntimeError):
    pass


class WindowsJob:
    """A Job Object owns descendants even after their original parent exits."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes

        self.ctypes = ctypes
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        api = self.api
        api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        api.CreateJobObjectW.restype = wintypes.HANDLE
        api.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        api.SetInformationJobObject.restype = wintypes.BOOL
        api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        api.AssignProcessToJobObject.restype = wintypes.BOOL
        api.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        api.TerminateJobObject.restype = wintypes.BOOL
        api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        api.OpenProcess.restype = wintypes.HANDLE
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        api.CloseHandle.restype = wintypes.BOOL

        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ("process_time", ctypes.c_int64),
                ("job_time", ctypes.c_int64),
                ("flags", wintypes.DWORD),
                ("min_working_set", ctypes.c_size_t),
                ("max_working_set", ctypes.c_size_t),
                ("active_processes", wintypes.DWORD),
                ("affinity", ctypes.c_size_t),
                ("priority", wintypes.DWORD),
                ("scheduling", wintypes.DWORD),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [
                (name, ctypes.c_uint64)
                for name in (
                    "read_ops",
                    "write_ops",
                    "other_ops",
                    "read_bytes",
                    "write_bytes",
                    "other_bytes",
                )
            ]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("basic", BasicLimits),
                ("io", IoCounters),
                ("process_memory", ctypes.c_size_t),
                ("job_memory", ctypes.c_size_t),
                ("peak_process_memory", ctypes.c_size_t),
                ("peak_job_memory", ctypes.c_size_t),
            ]

        self.handle = api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ProcessFailure("Windows speech process containment is unavailable.")
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not api.SetInformationJobObject(
            self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            self.close()
            raise ProcessFailure("Windows speech Job Object limits could not be set.")

    def attach_and_resume(self, pid):
        from ctypes import wintypes

        c, api = self.ctypes, self.api
        handle = api.OpenProcess(0x0200 | 0x0001, False, pid)
        if not handle:
            raise ProcessFailure("Windows speech child process could not be opened.")
        try:
            if not api.AssignProcessToJobObject(self.handle, handle):
                raise ProcessFailure("Windows speech child could not join its Job Object.")
        finally:
            api.CloseHandle(handle)

        class ThreadEntry(c.Structure):
            _fields_ = [
                ("size", wintypes.DWORD),
                ("usage", wintypes.DWORD),
                ("thread_id", wintypes.DWORD),
                ("owner_pid", wintypes.DWORD),
                ("priority", wintypes.LONG),
                ("delta", wintypes.LONG),
                ("flags", wintypes.DWORD),
            ]

        api.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        api.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        api.Thread32First.argtypes = [wintypes.HANDLE, c.POINTER(ThreadEntry)]
        api.Thread32First.restype = wintypes.BOOL
        api.Thread32Next.argtypes = [wintypes.HANDLE, c.POINTER(ThreadEntry)]
        api.Thread32Next.restype = wintypes.BOOL
        api.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        api.OpenThread.restype = wintypes.HANDLE
        api.ResumeThread.argtypes = [wintypes.HANDLE]
        api.ResumeThread.restype = wintypes.DWORD
        snapshot = api.CreateToolhelp32Snapshot(0x4, 0)  # TH32CS_SNAPTHREAD
        if snapshot == c.c_void_p(-1).value:
            raise ProcessFailure("Windows speech thread enumeration failed.")
        try:
            entry = ThreadEntry()
            entry.size = c.sizeof(entry)
            found = api.Thread32First(snapshot, c.byref(entry))
            while found:
                if entry.owner_pid == pid:
                    thread = api.OpenThread(0x0002, False, entry.thread_id)
                    if not thread:
                        raise ProcessFailure("Windows speech thread could not be opened.")
                    try:
                        if api.ResumeThread(thread) == 0xFFFFFFFF:
                            raise ProcessFailure("Windows speech child could not resume.")
                        return
                    finally:
                        api.CloseHandle(thread)
                found = api.Thread32Next(snapshot, c.byref(entry))
            raise ProcessFailure("Windows speech child thread was not found.")
        finally:
            api.CloseHandle(snapshot)

    def close(self):
        if self.handle:
            # KILL_ON_JOB_CLOSE remains the fallback if TerminateJobObject fails.
            self.api.TerminateJobObject(self.handle, 1)
            self.api.CloseHandle(self.handle)
            self.handle = None


def terminate_tree(process, job):
    if job is not None:
        job.close()
    else:
        # Reap a finished group leader first. macOS can return EPERM for a
        # just-exited, unreaped leader even when no live descendants remain.
        process.poll()
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except PermissionError:
            process.poll()
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    if process.poll() is None:
        # Also kill the suspended direct child if assignment failed on Windows.
        process.kill()


async def run_owned(argv, *, stdin=b"", timeout=60, env=None, max_output=2 * 1024 * 1024):
    if timeout <= 0 or len(stdin) > max_output:
        raise ProcessFailure("Invalid speech process limits.")
    process, job = None, None
    with (
        tempfile.TemporaryFile() as input_file,
        tempfile.TemporaryFile() as out_file,
        tempfile.TemporaryFile() as err_file,
    ):
        input_file.write(stdin)
        input_file.seek(0)
        try:
            if os.name == "nt":
                job = WindowsJob()
            process = subprocess.Popen(
                list(map(str, argv)),
                stdin=input_file,
                stdout=out_file,
                stderr=err_file,
                env=env,
                start_new_session=os.name != "nt",
                creationflags=(0x00000004 | 0x08000000) if os.name == "nt" else 0,
            )
            if job is not None:
                job.attach_and_resume(process.pid)
            deadline = time.monotonic() + timeout
            while True:
                if any(
                    os.fstat(file.fileno()).st_size > max_output for file in (out_file, err_file)
                ):
                    raise ProcessFailure("Speech executable exceeded its output limit.")
                code = process.poll()
                if code is not None:
                    if code:
                        raise ProcessFailure(f"Speech executable failed with exit code {code}.")
                    if any(
                        os.fstat(file.fileno()).st_size > max_output
                        for file in (out_file, err_file)
                    ):
                        raise ProcessFailure("Speech executable exceeded its output limit.")
                    out_file.seek(0)
                    return out_file.read(max_output)
                if time.monotonic() >= deadline:
                    raise ProcessFailure("Speech executable timed out.")
                await asyncio.sleep(0.02)
        except OSError as exc:
            raise ProcessFailure("Speech executable could not start.") from exc
        finally:
            if process is not None:
                terminate_tree(process, job)
                # No inherited pipe drain. Never wait indefinitely for a device tool.
                deadline = time.monotonic() + 2
                while process.poll() is None and time.monotonic() < deadline:
                    await asyncio.sleep(0.01)
                if process.poll() is None:
                    raise ProcessFailure("Speech process teardown exceeded its deadline.")
            elif job is not None:
                job.close()
