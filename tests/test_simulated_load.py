"""A full-size synthetic workload, isolated from environment credentials and live data."""

import json
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.responses import JSONResponse

import service.lls.app as app_module
from service.lls.app import BoundaryMiddleware
from service.lls.settings import Settings
from tools.simulated_load import LoadHttpClient


class RateClock:
    def __init__(self):
        self.now = 0.0
        self.waits = []

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.waits.append(seconds)
        self.now += seconds


async def test_load_retry_waits_for_next_rolling_admission_after_success(monkeypatch):
    clock = RateClock()
    # Only this regression controls the middleware clock. The full subprocess
    # workload below keeps the actual middleware clock and physical waits.
    monkeypatch.setattr(app_module, "monotonic", clock)

    async def endpoint(scope, receive, send):
        status = 409 if scope["path"] == "/conflict" else 200
        await JSONResponse({"detail": "route response"}, status_code=status)(scope, receive, send)

    boundary = BoundaryMiddleware(endpoint, Settings())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=boundary, client=("127.0.0.1", 44000)),
        base_url="http://127.0.0.1:8765",
    ) as client:
        http = LoadHttpClient(client, clock=clock, sleep=clock.sleep)
        # Route-level rejections also consume middleware admissions.
        assert (await http.request("GET", "/conflict")).status_code == 409
        clock.now = 2.0
        for _ in range(599):
            assert (await http.request("GET", "/ok")).status_code == 200
        assert http.rate_retries == 0

        # One old slot expires first. A success in that slot must not reset the
        # window: the next wait must follow the remaining old admissions.
        assert (await http.request("GET", "/ok")).status_code == 200
        assert clock.waits == pytest.approx([58.05])
        assert (await http.request("GET", "/ok")).status_code == 200

    assert clock.waits == pytest.approx([58.05, 2.0])
    assert http.rate_retries == 2
    assert http.rate_wait_seconds == pytest.approx(60.05)


async def test_load_retry_stops_when_rate_gate_never_recovers():
    clock = RateClock()

    async def reject(request):
        return httpx.Response(429, json={"detail": "request rate exceeded"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(reject), base_url="http://127.0.0.1:8765"
    ) as client:
        http = LoadHttpClient(client, clock=clock, sleep=clock.sleep)
        with pytest.raises(RuntimeError, match="HTTP rate gate did not recover"):
            await http.request("GET", "/ok")

    assert http.rate_retries == 16
    assert clock.waits == pytest.approx([0.05] * 16)


def test_36_npcs_and_200_confirmed_turns_remain_bounded_and_private():
    root = Path(__file__).resolve().parents[1]
    environment = {"PATH": os.defpath, "PYTHONPATH": str(root), "PYTHONUTF8": "1"}
    if "SYSTEMROOT" in os.environ:
        environment["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
    result = subprocess.run(
        [sys.executable, str(root / "tools/simulated_load.py")],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["actors"] == {
        "tracked": 36,
        "persistent": 12,
        "ephemeral": 24,
        "participating": 35,
        "uninvolved": 1,
    }
    assert report["turns"] == report["context_isolation_checks"] == 200
    assert report["confirmed_delivery_events_after_restart"] == 200
    assert report["confirmed_completion_events_after_restart"] == 200
    assert report["persistent_output_memories_after_restart"] == 66
    assert report["ephemeral_output_memories_removed_on_close"] == 134
    assert report["uninvolved_actor_received_outputs"] == report["network_attempts"] == 0
    assert report["blocked_while_delivery_pending"] == report["duplicate_delivery_checks"] == 8
    assert report["queue_peaks"]["commands"] == report["queue_limits"]["commands"] == 256
    assert all(
        report["queue_peaks"][name] <= limit for name, limit in report["queue_limits"].items()
    )
    assert report["timing"]["virtual_elapsed_seconds"] == 1200
    assert report["timing"]["measured_wall_seconds"] > 0
    assert report["http_rate_limit_retries"] >= 1
    assert report["timing"]["http_rate_limit_wait_seconds"] > 0
    assert report["game_verified"] is report["native_verified"] is False
    assert report["timing"]["native_frame_time_measured"] is False
    assert report["timing"]["model_latency_measured"] is False
