"""Loopback HTTP API. Game native calls never run in this process."""

import asyncio
import ipaddress
import secrets
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from time import monotonic

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse, RedirectResponse

from . import __version__
from .actions import POLICY_VERSION
from .admin import create_admin_router
from .build_identity import source_identity
from .neighborhood_bridge import create_neighborhood_router
from .perception_bridge import PerceptionBatch
from .protocol import (
    Ack,
    Event,
    EventBatch,
    Heartbeat,
    SessionClose,
    SessionOpen,
)
from .runtime import TERMINAL, Runtime
from .runtime_skills import SkillAdopt, SkillExecute, SkillGenerate, SkillTrial
from .settings import Settings
from .social_bridge import SocialDelivery, SocialInvite, SocialReview, SocialTurn
from .storage import Repository, TimelineAmbiguityError


class BoundaryMiddleware:
    """Reject hostile origins, nonlocal sources, oversized streams, and floods."""

    def __init__(self, app, settings):
        self.app = app
        self.settings = settings
        self.rates = defaultdict(deque)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        source = scope.get("client", ("", 0))[0]
        try:
            local = ipaddress.ip_address(source).is_loopback
        except ValueError:
            local = False
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        allowed_hosts = {"localhost", "127.0.0.1", "[::1]", self.settings.host}
        host = headers.get("host", "").rsplit(":", 1)[0]
        if headers.get("host", "") in allowed_hosts:
            host = headers["host"]
        origins = {f"http://{h}:{self.settings.port}" for h in allowed_hosts}
        if not local and not (self.settings.dev_lan and source in self.settings.allow_sources):
            return await self.reject(scope, receive, send, 403, "source not allowed")
        if host not in allowed_hosts:
            return await self.reject(scope, receive, send, 403, "host not allowed")
        if "origin" in headers and headers["origin"] not in origins:
            return await self.reject(scope, receive, send, 403, "origin not allowed")
        bucket = self.rates[source]
        stamp = monotonic()
        while bucket and bucket[0] < stamp - 60:
            bucket.popleft()
        if len(bucket) >= 600:
            return await self.reject(scope, receive, send, 429, "request rate exceeded")
        bucket.append(stamp)
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > 256 * 1024:
                return await self.reject(scope, receive, send, 413, "request body exceeds 256 KiB")
            if not message.get("more_body", False):
                break
        delivered = False

        async def bounded_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, bounded_receive, send)

    async def reject(self, scope, receive, send, code, message):
        await JSONResponse({"detail": message}, status_code=code)(scope, receive, send)


def create_app(settings=None, repository=None, provider=None, *, runtime_options=None):
    settings = settings or Settings.from_env()
    build_identity = source_identity()
    owns_repository = repository is None
    repository = repository or Repository(settings.database_path)
    try:
        runtime = Runtime(settings, repository, provider, **(runtime_options or {}))
    except BaseException:
        if owns_repository:
            repository.close()
        raise

    @asynccontextmanager
    async def lifespan(app):
        maintenance = asyncio.create_task(runtime.maintain())
        try:
            yield
        finally:
            maintenance.cancel()
            await asyncio.gather(maintenance, return_exceptions=True)
            try:
                await runtime.shutdown()
            finally:
                await asyncio.to_thread(repository.close)

    app = FastAPI(title="Living Los Santos", version=__version__, lifespan=lifespan)
    app.state.runtime = runtime
    app.state.repository = repository
    app.add_middleware(BoundaryMiddleware, settings=settings)

    @app.exception_handler(TimelineAmbiguityError)
    async def ambiguous_timeline(request, exc):
        return JSONResponse(
            {"detail": "Choose the save timeline explicitly", "branch_candidates": exc.candidates},
            status_code=409,
        )

    @app.exception_handler(ValueError)
    async def invalid_state(request, exc):
        return JSONResponse({"detail": "invalid stored state or timeline input"}, status_code=409)

    def bearer(authorization: str = Header(default="")):
        if not authorization.startswith("Bearer "):
            raise HTTPException(401, "session bearer token required")
        return authorization[7:]

    app.include_router(create_neighborhood_router(runtime, bearer))

    @app.get("/health")
    async def health():
        return {
            "status": "ok",
            "version": __version__,
            "build": build_identity,
            "release_mode": settings.release,
            "schema_version": 1,
            "provider": runtime.provider.name,
            "speech": "configured" if runtime.settings.speech.enabled else "disabled",
            "game_verified": False,
        }

    @app.post("/v1/bridge/sessions")
    async def open_session(body: SessionOpen, authorization: str = Header(default="")):
        if settings.bootstrap_token and not secrets.compare_digest(
            authorization, "Bearer " + settings.bootstrap_token
        ):
            raise HTTPException(401, "bootstrap bearer token required")
        session = await runtime.open(body)
        return {
            "session_id": session.id,
            "token": session.token,
            "policy_version": POLICY_VERSION,
            "schema_version": 1,
            "capabilities": body.capabilities,
            "branch_id": session.branch_id,
        }

    @app.post("/v1/bridge/events:batch")
    async def batch(body: EventBatch, token=Depends(bearer)):
        session = runtime.authenticate(token, body.session_id)
        results = []
        for event in body.events:
            results.append(await runtime.event(session, event))
        return {"results": results, "last_seq": session.last_seq}

    @app.post("/v1/bridge/control")
    async def control(body: Event, token=Depends(bearer)):
        if body.kind not in {"scene.cancel", "voice.ptt_started", "voice.ptt_stopped"}:
            raise HTTPException(422, "control lane accepts PTT and cancel only")
        session = runtime.authenticate(token, body.session_id)
        return await runtime.event(session, body)

    @app.post("/v1/bridge/perception")
    async def perception(body: PerceptionBatch, token=Depends(bearer)):
        session = runtime.authenticate(token, body.session_id)
        return await runtime.perceive(session, body)

    @app.post("/v1/bridge/social/invite")
    async def social_invite(body: SocialInvite, token=Depends(bearer)):
        session = runtime.authenticate(token, body.session_id)
        return await runtime.social_call(session, body, "invite")

    @app.post("/v1/bridge/social/turn")
    async def social_turn(body: SocialTurn, token=Depends(bearer)):
        session = runtime.authenticate(token, body.session_id)
        return await runtime.social_call(session, body, "turn")

    @app.post("/v1/bridge/social/review")
    async def social_review(body: SocialReview, token=Depends(bearer)):
        session = runtime.authenticate(token, body.session_id)
        return await runtime.social_call(session, body, "review")

    @app.post("/v1/bridge/social/delivery")
    async def social_delivery(body: SocialDelivery, token=Depends(bearer)):
        session = runtime.authenticate(token, body.session_id)
        return await runtime.social_call(session, body, "delivery")

    @app.post("/v1/bridge/skills/generate")
    async def skill_generate(body: SkillGenerate, token=Depends(bearer)):
        session = runtime.authenticate(token, body.session_id)
        return await runtime.skill_call(session, body, "generate")

    @app.post("/v1/bridge/skills/trial")
    async def skill_trial(body: SkillTrial, token=Depends(bearer)):
        session = runtime.authenticate(token, body.session_id)
        return await runtime.skill_call(session, body, "trial")

    @app.post("/v1/bridge/skills/adopt")
    async def skill_adopt(body: SkillAdopt, token=Depends(bearer)):
        session = runtime.authenticate(token, body.session_id)
        return await runtime.skill_call(session, body, "adopt")

    @app.post("/v1/bridge/skills/execute")
    async def skill_execute(body: SkillExecute, token=Depends(bearer)):
        session = runtime.authenticate(token, body.session_id)
        return await runtime.skill_call(session, body, "execute")

    @app.get("/v1/bridge/commands")
    async def commands(
        session_id: str | None = None,
        after: int = Query(default=0, ge=0),
        wait_seconds: float = Query(default=5, ge=0, le=5),
        token=Depends(bearer),
    ):
        session = runtime.authenticate(token, session_id)
        await runtime.flush_triggers(session)

        async def available():
            async with session.lock:
                runtime.expire_commands(session)
                await runtime.flush_outcomes(session)
                output = []
                for command in session.commands.values():
                    if command["seq"] > after and command["state"] not in TERMINAL:
                        output.append(
                            {
                                k: v
                                for k, v in command.items()
                                if k not in {"state", "reason"} and not k.startswith("_")
                            }
                        )
                return output

        session.changed.clear()
        output = await available()
        if not output and wait_seconds:
            try:
                await asyncio.wait_for(session.changed.wait(), wait_seconds)
            except TimeoutError:
                pass
            output = await available()
        return {"commands": output, "cursor": max([after] + [c["seq"] for c in output])}

    @app.post("/v1/bridge/commands/{command_id}/ack")
    async def ack(command_id: str, body: Ack, token=Depends(bearer)):
        session = runtime.authenticate(token, body.session_id)
        return await runtime.acknowledge(session, command_id, body)

    @app.post("/v1/bridge/heartbeat")
    async def heartbeat(body: Heartbeat, token=Depends(bearer)):
        session = runtime.authenticate(token, body.session_id)
        runtime.ensure_current(session, body.runtime_generation)
        session.heartbeat = body.model_dump()
        return {"status": "ok"}

    @app.post("/v1/bridge/sessions/{session_id}/close")
    async def close(session_id: str, body: SessionClose, token=Depends(bearer)):
        session = runtime.authenticate(token, session_id)
        runtime.ensure_current(session, body.runtime_generation)
        await runtime.close_session(session)
        return {"status": "closed"}

    @app.get("/v1/diagnostics")
    async def diagnostics(session_id: str | None = None, token=Depends(bearer)):
        session = runtime.authenticate(token, session_id)
        scheduler = getattr(runtime, "social_scheduler", None)
        return {
            "session_id": session.id,
            "profile_id": session.request.profile_id,
            "branch_id": session.branch_id,
            "manifest": session.request.manifest.model_dump(),
            "provider": runtime.provider.name,
            "actors": session.actors,
            "commands": [
                {k: v for k, v in command.items() if not k.startswith("_")}
                for command in session.commands.values()
            ],
            "trace": list(session.trace),
            "heartbeat": session.heartbeat,
            "perception": {
                "development_enabled": settings.dev_perception,
                "native_verified": False,
                "accepted_incidents": len(session.perception_seen),
            },
            "triggers": runtime.trigger_engine.diagnostics() if runtime.trigger_engine else None,
            "social_scheduler": scheduler.diagnostics(session.id) if scheduler else None,
            "neighborhood": runtime.neighborhood.diagnostics(session.id),
            "skills": {
                "development_enabled": runtime.settings.dev_skills,
                "worker_active": bool(session.skill_task and not session.skill_task.done()),
                "scheduler": (runtime.skill_scheduler.diagnostics(session.id)
                              if runtime.skill_scheduler is not None else None),
                "game_verified": False,
            },
            "limitations": [
                "Speech hardware acceptance pending",
                "No GTA acceptance",
                "Skill bridge is development-only; native acceptance pending",
            ],
        }

    app.include_router(create_admin_router(settings, runtime, repository))

    @app.get("/")
    async def dashboard():
        return RedirectResponse("/dashboard")

    return app
