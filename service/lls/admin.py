"""Local administration with credentials independent from the game bridge."""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import secrets
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import Field, ValidationError, field_validator

from .embeddings import EmbeddingConfig
from .gateway_config import GatewayConfig, validate_local_endpoint
from .protocol import DisplayName, StrictModel
from .settings import Settings


class MemoryEdit(StrictModel):
    text: str | None = Field(default=None, min_length=1, max_length=4000)
    importance: float | None = Field(default=None, ge=0, le=1)
    confidence: float | None = Field(default=None, ge=0, le=1)


class ConfirmProfile(StrictModel):
    confirm_profile_id: str = Field(min_length=1, max_length=128)


class ConfirmActor(StrictModel):
    confirm_actor_id: str = Field(min_length=1, max_length=128)


class PinActor(ConfirmActor):
    display_name: DisplayName | None = None


class ConfirmMemory(StrictModel):
    confirm_memory_id: str = Field(min_length=1, max_length=128)


class TimelineChoice(ConfirmProfile):
    branch_id: str = Field(min_length=1, max_length=128)


class BackupRequest(StrictModel):
    confirm_scope: str = Field(pattern="^all_profiles$")


class ProviderConfiguration(StrictModel):
    provider: Literal["disabled", "fake", "local_openai", "openai"]
    dialogue_model: str = Field(default="", max_length=128)
    action_model: str = Field(default="", max_length=128)
    openai_base_url: str = Field(default="https://api.openai.com/v1", max_length=512)
    local_base_url: str = Field(default="http://127.0.0.1:1234/v1", max_length=512)
    ai_gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)

    _local_endpoint = field_validator("local_base_url")(validate_local_endpoint)

    @field_validator("openai_base_url")
    @classmethod
    def remote_endpoint(cls, value):
        endpoint = urlsplit(value)
        if (
            endpoint.scheme != "https"
            or not endpoint.hostname
            or endpoint.username
            or endpoint.password
            or endpoint.query
            or endpoint.fragment
        ):
            raise ValueError("hosted endpoint requires credential-free HTTPS")
        if endpoint.port is not None and not 1 <= endpoint.port <= 65535:
            raise ValueError("invalid hosted endpoint port")
        return value


class CloudConsent(StrictModel):
    endpoint: str = Field(min_length=1, max_length=512)
    configuration_revision: str = Field(pattern="^[a-f0-9]{64}$")


class EmptyMutation(StrictModel):
    pass


def create_admin_router(settings, runtime, repository) -> APIRouter:
    router = APIRouter()
    admin_token = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    mutation_lock = asyncio.Lock()
    no_cache = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}

    def local_request(request: Request) -> str:
        try:
            is_local = request.client and ipaddress.ip_address(request.client.host).is_loopback
        except ValueError:
            is_local = False
        if not is_local or request.url.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise HTTPException(403, "administration is available on loopback only")
        expected = f"{request.url.scheme}://{request.url.netloc}"
        if request.headers.get("origin", expected) != expected:
            raise HTTPException(403, "administration requires the same origin")
        if request.headers.get("sec-fetch-site") not in {None, "none", "same-origin"}:
            raise HTTPException(403, "cross-site administration request refused")
        return expected

    def authorize(request: Request):
        expected_origin = local_request(request)
        if not secrets.compare_digest(
            request.headers.get("authorization", ""), "Bearer " + admin_token
        ):
            raise HTTPException(401, "administrator bearer token required")
        if request.method not in {"GET", "HEAD"}:
            if request.headers.get("origin") != expected_origin:
                raise HTTPException(403, "same-origin JSON mutation required")
            if request.headers.get("content-type", "").split(";")[0] != "application/json":
                raise HTTPException(415, "administration mutations require application/json")
            if not secrets.compare_digest(request.headers.get("x-csrf-token", ""), csrf_token):
                raise HTTPException(403, "administrator CSRF token required")

    async def require_profile(profile_id):
        profiles = await runtime.db("list_profiles")
        if not any(profile["profile_id"] == profile_id for profile in profiles):
            raise HTTPException(404, "profile not found")

    @asynccontextmanager
    async def mutate_profile(profile_id, *, close_sessions=False):
        # Serialize admin changes so session locks always have one acquisition order.
        async with mutation_lock, AsyncExitStack() as locks:
            await locks.enter_async_context(runtime.profile_lock(profile_id))
            sessions = sorted(
                (
                    session
                    for session in runtime.sessions.values()
                    if session.request.profile_id == profile_id
                ),
                key=lambda session: session.id,
            )
            for session in sessions:
                await locks.enter_async_context(session.lock)
            for session in sessions:
                runtime.cancel(session)
                await runtime.stop_session_work(session, erase=True)
                if session.task and not session.task.done():
                    await asyncio.gather(session.task, return_exceptions=True)
                for command in session.commands.values():
                    if command["kind"] == "dialogue.chunk":
                        command["payload"]["text"] = ""
                # Traces retain memory IDs, never deleted content or generated snippets.
                session.trace.clear()
                session.action_outcomes.clear()
                session.delivery_turns.clear()
                if close_sessions and not session.closed:
                    await runtime.close_session_locked(session)
            yield

    def result(data, status_code=200):
        return JSONResponse(data, status_code=status_code, headers=no_cache)

    @router.get("/dashboard", response_class=HTMLResponse)
    async def dashboard(request: Request):
        local_request(request)
        nonce = secrets.token_urlsafe(24)
        page = await asyncio.to_thread(
            Path(__file__).with_name("dashboard.html").read_text, encoding="utf-8"
        )
        bootstrap = json.dumps({"token": admin_token, "csrf": csrf_token})
        page = page.replace("__ADMIN_BOOTSTRAP__", bootstrap).replace("__CSP_NONCE__", nonce)
        return HTMLResponse(
            page,
            headers={
                **no_cache,
                "Referrer-Policy": "no-referrer",
                "X-Frame-Options": "DENY",
                "Content-Security-Policy": (
                    "default-src 'none'; connect-src 'self'; "
                    f"script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; "
                    "img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; "
                    "form-action 'none'"
                ),
            },
        )

    secured = APIRouter(prefix="/v1/admin", dependencies=[Depends(authorize)])

    def configuration_revision():
        configuration = runtime.provider.configuration_summary()
        configuration.pop("cloud_opt_in", None)
        canonical = json.dumps(configuration, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    def current_provider_status():
        return {
            **runtime.provider.safe_status(),
            "configuration_revision": configuration_revision(),
        }

    @secured.get("/provider")
    async def provider_status():
        return result(current_provider_status())

    @secured.put("/provider")
    async def configure_provider(body: ProviderConfiguration):
        async with mutation_lock:
            fields = runtime.settings.model_dump()
            fields.update(body.model_dump(), cloud_opt_in=False)
            # SpeechConfig is a strict dataclass, not a configuration dictionary.
            fields["speech"] = runtime.settings.speech
            try:
                configured = Settings.model_validate(fields)
            except ValidationError:
                raise HTTPException(422, "provider configuration is invalid") from None
            await runtime.configure_provider(configured)
            return result(current_provider_status())

    @secured.post("/provider/consent")
    async def grant_cloud_consent(body: CloudConsent):
        async with mutation_lock:
            configuration = runtime.provider.configuration_summary()
            embedding = configuration["embedding"]
            if configuration["provider"] != "openai" and embedding["provider"] != "openai":
                raise HTTPException(409, "select and save a hosted provider first")
            if (
                body.endpoint != configuration["openai_base_url"]
                or body.configuration_revision != configuration_revision()
            ):
                raise HTTPException(409, "the provider configuration changed; review it again")
            prices = configuration["ai_gateway"]["prices"]
            required = {
                configuration["dialogue_model"],
                configuration["action_model"] or configuration["dialogue_model"],
            } if configuration["provider"] == "openai" else set()
            if embedding["provider"] == "openai":
                required.add(embedding["model"])
            if not required.issubset(prices):
                raise HTTPException(
                    409, "configure prices for all hosted models before enabling cloud"
                )
            await runtime.set_cloud_consent(True)
            return result(current_provider_status())

    @secured.post("/provider/revoke")
    async def revoke_cloud_consent(body: EmptyMutation):
        async with mutation_lock:
            await runtime.set_cloud_consent(False)
            return result(current_provider_status())

    @secured.get("/overview")
    async def overview():
        sessions = [
            {
                "session_id": session.id,
                "profile_id": session.request.profile_id,
                "branch_id": session.branch_id,
                "protagonist_id": session.request.protagonist_id,
                "game_datetime": session.game_datetime,
                "closed": session.closed,
                "actor_count": len(session.actors),
            }
            for session in runtime.sessions.values()
        ]
        return result(
            {
                "profiles": await runtime.db("list_profiles"),
                "sessions": sessions,
                "provider": runtime.provider.name,
                "speech": "unverified",
                "game_verified": False,
                "selected_branches": getattr(runtime, "selected_branches", {}),
            }
        )

    @secured.get("/profiles/{profile_id}/actors")
    async def actors(profile_id: str):
        await require_profile(profile_id)
        ids = [
            session.id
            for session in runtime.sessions.values()
            if session.request.profile_id == profile_id and not session.closed
        ]
        found = {actor["id"]: actor for actor in await runtime.db("list_actors", profile_id)}
        for session_id in ids:
            found.update(
                {
                    actor["id"]: actor
                    for actor in await runtime.db("list_actors", profile_id, session_id=session_id)
                }
            )
        return result({"actors": list(found.values())})

    @secured.get("/sessions/{session_id}/diagnostics")
    async def session_diagnostics(session_id: str):
        session = runtime.sessions.get(session_id)
        if session is None:
            raise HTTPException(404, "session not found")
        # Administrative diagnostics deliberately expose local dialogue, but
        # never include bearer tokens, hidden receipts, or provider credentials.
        trace_fields = {
            "turn_id",
            "actor_id",
            "provider",
            "duration_ms",
            "status",
            "error_type",
            "policy",
            "memory_ids",
            "knowledge_ids",
            "relationship_event_ids",
            "action_outcome_ids",
            "context_bytes",
            "context_truncated",
            "incident_id",
            "accepted",
            "development_perception",
            "trigger_rule_id",
            "effect_type",
            "source_event_ids",
            "relationship_event_id",
        }
        command_fields = {
            "id",
            "seq",
            "kind",
            "actor_id",
            "runtime_instance_id",
            "expires_at",
            "state",
            "reason",
            "payload",
        }
        return result(
            {
                "session_id": session.id,
                "profile_id": session.request.profile_id,
                "branch_id": session.branch_id,
                "protagonist_id": session.request.protagonist_id,
                "game_datetime": session.game_datetime,
                "closed": session.closed,
                "provider": runtime.provider.name,
                "manifest": session.request.manifest.model_dump(),
                "world": {k: v for k, v in session.world.items() if not k.startswith("_")},
                "actors": [
                    {
                        "actor_id": actor_id,
                        **{k: v for k, v in actor.items() if not k.startswith("_")},
                        **dict(
                            zip(
                                ("pin_eligible", "pin_reason"),
                                runtime.pin_eligibility(session, actor_id),
                            )
                        ),
                    }
                    for actor_id, actor in session.actors.items()
                ],
                "commands": [
                    {k: v for k, v in command.items() if k in command_fields}
                    for command in session.commands.values()
                ],
                "trace": [
                    {k: v for k, v in entry.items() if k in trace_fields} for entry in session.trace
                ],
                "heartbeat": dict(session.heartbeat),
                "perception": {
                    "development_enabled": runtime.settings.dev_perception,
                    "accepted_incidents": len(session.perception_seen),
                    "native_verified": False,
                },
                "triggers": (
                    runtime.trigger_engine.diagnostics() if runtime.trigger_engine else None
                ),
                "social": (
                    runtime.social.scenes_for_session(session.id) if runtime.social else None
                ),
                "social_scheduler": (
                    runtime.social_scheduler.diagnostics(session.id)
                    if getattr(runtime, "social_scheduler", None)
                    else None
                ),
                "neighborhood": (
                    runtime.neighborhood.diagnostics(session.id)
                    if getattr(runtime, "neighborhood", None)
                    else None
                ),
                "skills": {
                    "development_enabled": runtime.settings.dev_skills,
                    "worker_active": bool(session.skill_task and not session.skill_task.done()),
                    "scheduler": (runtime.skill_scheduler.diagnostics(session.id)
                                  if runtime.skill_scheduler is not None else None),
                    "game_verified": False,
                },
                "game_verified": False,
            }
        )

    @secured.get("/profiles/{profile_id}/memories")
    async def memories(
        profile_id: str,
        actor_id: str | None = None,
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ):
        await require_profile(profile_id)
        return result(
            {
                "memories": await runtime.db(
                    "list_memories", profile_id, actor_id, limit=limit, offset=offset
                ),
                "scope": "administrative_all_branches",
                "offset": offset,
            }
        )

    @secured.patch("/profiles/{profile_id}/memories/{memory_id}")
    async def edit_memory(profile_id: str, memory_id: str, body: MemoryEdit):
        if not body.model_dump(exclude_none=True):
            raise HTTPException(422, "select a memory field to edit")
        async with mutate_profile(profile_id):
            if await runtime.db("get_memory", profile_id, memory_id) is None:
                raise HTTPException(404, "memory not found in this profile")
            updated = await runtime.db(
                "update_memory", profile_id, memory_id, **body.model_dump(exclude_none=True)
            )
            return result(updated)

    @secured.delete("/profiles/{profile_id}/memories/{memory_id}")
    async def delete_memory(profile_id: str, memory_id: str, body: ConfirmMemory):
        if body.confirm_memory_id != memory_id:
            raise HTTPException(409, "memory confirmation mismatch")
        async with mutate_profile(profile_id):
            if await runtime.db("get_memory", profile_id, memory_id) is None:
                raise HTTPException(404, "memory not found in this profile")
            return result({"deleted": await runtime.db("delete_memory", profile_id, memory_id)})

    @secured.post("/profiles/{profile_id}/actors/{actor_id}/reset")
    async def reset_actor(profile_id: str, actor_id: str, body: ConfirmActor):
        if body.confirm_actor_id != actor_id:
            raise HTTPException(409, "actor confirmation mismatch")
        async with mutate_profile(profile_id):
            if await runtime.db("get_actor", profile_id, actor_id) is None:
                raise HTTPException(404, "actor not found in this profile")
            await runtime.db("reset_actor", profile_id, actor_id)
            return result({"status": "reset", "actor_id": actor_id})

    @secured.post("/sessions/{session_id}/actors/{actor_id}/pin")
    async def pin_actor(session_id: str, actor_id: str, body: PinActor):
        if body.confirm_actor_id != actor_id:
            raise HTTPException(409, "actor confirmation mismatch")
        session = runtime.sessions.get(session_id)
        if session is None:
            raise HTTPException(404, "session not found")
        # The same mutation, profile, and session lock order as every other admin change.
        async with mutation_lock, runtime.profile_lock(session.request.profile_id), session.lock:
            pin, refusal = await runtime.pin_locked(
                session, actor_id, display_name=body.display_name
            )
            if refusal:
                raise HTTPException(409, refusal)
            return result(
                {
                    "status": "pin_requested" if pin["state"] == "pending" else pin["state"],
                    "actor_id": actor_id,
                    "display_name": pin["display_name"],
                    "actor_kind": "spawned_persistent",
                }
            )

    @secured.delete("/profiles/{profile_id}/actors/{actor_id}")
    async def forget_pinned_actor(profile_id: str, actor_id: str, body: ConfirmActor):
        if body.confirm_actor_id != actor_id:
            raise HTTPException(409, "actor confirmation mismatch")
        async with mutate_profile(profile_id):
            actor = await runtime.db("get_actor", profile_id, actor_id)
            if actor is None:
                raise HTTPException(404, "actor not found in this profile")
            if actor.get("actor_kind") != "spawned_persistent" or not actor.get(
                "pinned_from_session"
            ):
                raise HTTPException(409, "only pinned pedestrians can be forgotten")
            for session in runtime.sessions.values():
                if session.request.profile_id == profile_id:
                    runtime.forget_pinned_locked(session, actor_id)
            await runtime.db("forget_pinned_actor", profile_id, actor_id)
            return result({"status": "forgotten", "actor_id": actor_id})

    @secured.get("/profiles/{profile_id}/export")
    async def export_profile(profile_id: str):
        await require_profile(profile_id)
        return result(await runtime.db("export_profile", profile_id))

    @secured.delete("/profiles/{profile_id}")
    async def erase_profile(profile_id: str, body: ConfirmProfile):
        if body.confirm_profile_id != profile_id:
            raise HTTPException(409, "profile confirmation mismatch")
        async with mutate_profile(profile_id, close_sessions=True):
            await require_profile(profile_id)
            selected = getattr(runtime, "selected_branches", {})
            selected.pop(profile_id, None)
            return result({"deleted": await runtime.db("delete_profile", profile_id)})

    @secured.get("/profiles/{profile_id}/branches")
    async def branches(profile_id: str):
        await require_profile(profile_id)
        return result(
            {
                "branches": await runtime.db("list_branches", profile_id),
                "selected_branch_id": getattr(runtime, "selected_branches", {}).get(profile_id),
            }
        )

    @secured.post("/profiles/{profile_id}/timeline")
    async def choose_timeline(profile_id: str, body: TimelineChoice):
        if body.confirm_profile_id != profile_id:
            raise HTTPException(409, "profile confirmation mismatch")
        branches = await runtime.db("list_branches", profile_id)
        if body.branch_id not in {branch["branch_id"] for branch in branches}:
            raise HTTPException(404, "branch not found in this profile")
        async with mutate_profile(profile_id, close_sessions=True):
            runtime.selected_branches[profile_id] = body.branch_id
            return result({"profile_id": profile_id, "parent_branch_id": body.branch_id})

    @secured.post("/backups")
    async def backup(body: BackupRequest):
        if repository.path == ":memory:":
            raise HTTPException(409, "choose a persistent database before creating a backup")
        # Never accept an arbitrary destination from an HTTP request.
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = Path(repository.path).resolve().parent / "backups"
        path /= f"lls-{stamp}-{secrets.token_hex(4)}.sqlite3"
        async with mutation_lock:
            await runtime.db("backup", path)
        return result({"scope": "all_profiles", "path": str(path), "status": "created"}, 201)

    router.include_router(secured)
    return router
