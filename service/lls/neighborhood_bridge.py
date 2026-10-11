"""Authenticated, explicitly requested virtual simulation. No native embodiment."""

import asyncio
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field

from .neighborhood import (
    AdvanceBudget,
    NeighborhoodConfig,
    NeighborhoodEngine,
    NeighborhoodPack,
    NeighborhoodScope,
)
from .neighborhood_store import instant
from .protocol import Identifier, Protagonist, StrictModel

DEVELOPMENT_NEIGHBORHOOD_CAPABILITY = "development:neighborhood:v1"
MAX_PACK_BYTES = 256 * 1024


class NeighborhoodRequest(StrictModel):
    session_id: Identifier
    runtime_generation: int = Field(ge=0)
    protagonist_id: Protagonist
    game_datetime: datetime
    neighborhood_id: Identifier


class NeighborhoodAdvance(NeighborhoodRequest):
    budget: AdvanceBudget = Field(default_factory=AdvanceBudget)


class NeighborhoodBridge:
    def __init__(self, runtime):
        self.runtime = runtime
        self.engine = None
        self.scheduler = None
        self.lock = asyncio.Lock()
        if not runtime.settings.dev_neighborhood:
            return
        if runtime.settings.release:
            raise ValueError("release builds refuse development neighborhood simulation")
        path = runtime.settings.neighborhood_pack_path
        if not path:
            raise ValueError("development neighborhood simulation requires an authored pack")
        with Path(path).open("rb") as stream:
            source = stream.read(MAX_PACK_BYTES + 1)
        if len(source) > MAX_PACK_BYTES:
            raise ValueError("authored neighborhood pack exceeds 256 KiB")
        pack = NeighborhoodPack.model_validate_json(source)
        self.engine = NeighborhoodEngine(
            runtime.repo,
            pack,
            config=NeighborhoodConfig(enabled=True, release=False),
            utc_now=runtime.utc_now,
        )
        if getattr(runtime.settings, "dev_neighborhood_autonomy", False):
            from .neighborhood_scheduler import NeighborhoodScheduler

            self.scheduler = NeighborhoodScheduler(self, runtime.settings.neighborhood_scheduler)

    def diagnostics(self, session_id=None):
        return {
            "development_enabled": self.engine is not None,
            "neighborhood_id": self.engine.pack.neighborhood_id if self.engine else None,
            "config_hash": self.engine.pack_hash if self.engine else None,
            "actor_count": len(self.engine.actors) if self.engine else 0,
            "automatic_advancement": self.scheduler is not None,
            "scheduler": self.scheduler.diagnostics(session_id) if self.scheduler else None,
            "native_verified": False,
            "game_verified": False,
        }

    def scope(self, session, body, *, mutation):
        runtime = self.runtime
        runtime.ensure_current(session, body.runtime_generation)
        if (
            runtime.settings.release
            or not runtime.settings.dev_neighborhood
            or self.engine is None
            or DEVELOPMENT_NEIGHBORHOOD_CAPABILITY not in session.request.manifest.capability_flags
        ):
            raise HTTPException(403, "development neighborhood simulation is unavailable")
        if (
            body.session_id != session.id
            or body.protagonist_id != session.request.protagonist_id
            or instant(body.game_datetime) != instant(session.game_datetime)
            or body.neighborhood_id != self.engine.pack.neighborhood_id
        ):
            raise HTTPException(409, "neighborhood request does not match the current session")
        world = session.world
        if mutation and (
            not world
            or not 0 <= runtime.clock() - world.get("_sampled_at", 0) <= 5
            or world.get("protagonist_id") != session.request.protagonist_id
            or any(
                world.get(flag, True)
                for flag in (
                    "online",
                    "mission_active",
                    "cutscene_active",
                    "random_event_active",
                    "paused",
                )
            )
        ):
            raise HTTPException(409, "neighborhood changes require a fresh eligible world snapshot")
        return NeighborhoodScope(
            profile_id=session.request.profile_id,
            branch_id=session.branch_id,
            protagonist_id=session.request.protagonist_id,
            neighborhood_id=self.engine.pack.neighborhood_id,
        )

    async def call(self, session, body, operation):
        if operation not in {"initialize", "advance", "snapshot"}:
            raise ValueError("unknown neighborhood operation")
        # Erasure, session closure, and provider changes also acquire session.lock.
        # thread_job joins canceled executor work before releasing this lock.
        async with session.lock, self.lock:
            scope = self.scope(session, body, mutation=operation != "snapshot")
            kwargs = {"budget": body.budget} if operation == "advance" else {}
            result = await self.runtime.thread_job(
                getattr(self.engine, operation),
                scope,
                session.game_datetime,
                **kwargs,
            )
            if self.scheduler is not None:
                self.scheduler.observe(scope, result, operation=operation)
            return {
                **result,
                "session_id": session.id,
                "branch_id": session.branch_id,
                "protagonist_id": session.request.protagonist_id,
                "game_datetime": session.game_datetime,
                "automatic_advancement": self.scheduler is not None,
                "game_verified": False,
                "native_operations": 0,
                "model_calls": 0,
            }


def create_neighborhood_router(runtime, bearer):
    router = APIRouter(prefix="/v1/bridge/neighborhood")

    @router.post("/initialize")
    async def initialize(body: NeighborhoodRequest, token=Depends(bearer)):
        session = runtime.authenticate(token, body.session_id)
        return await runtime.neighborhood.call(session, body, "initialize")

    @router.post("/advance")
    async def advance(body: NeighborhoodAdvance, token=Depends(bearer)):
        session = runtime.authenticate(token, body.session_id)
        return await runtime.neighborhood.call(session, body, "advance")

    @router.post("/snapshot")
    async def snapshot(body: NeighborhoodRequest, token=Depends(bearer)):
        session = runtime.authenticate(token, body.session_id)
        return await runtime.neighborhood.call(session, body, "snapshot")

    return router
