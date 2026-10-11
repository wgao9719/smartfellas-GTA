"""Development-only runtime boundary for simulated NPC social scenes."""

import asyncio
import hashlib
from datetime import datetime, timedelta

from fastapi import HTTPException

from .social_bridge import DEVELOPMENT_SOCIAL_CAPABILITY
from .social_models import SocialActorState, SocialConfig, SocialEnvironment, SocialStart

SOCIAL_EMBODIMENT_CAPABILITY = "development:social-embodiment:v1"


class SocialRuntime:
    @staticmethod
    def social_embodied(session):
        return SOCIAL_EMBODIMENT_CAPABILITY in session.request.manifest.capability_flags

    def social_player_radius(self, session):
        return 12 if self.social_embodied(session) else 20

    def initialize_social(self, social_factory=None):
        self.social_scene_owners = {}
        self.social = None
        self.social_scheduler = None
        if not self.settings.dev_social:
            return
        from .social import SocialEngine

        self.configure_social_fake(self.provider)
        self.social = (social_factory or SocialEngine)(
            self.repo,
            self.provider,
            config=SocialConfig(enabled=self.settings.dev_social),
            clock=self.clock,
            utc_now=self.utc_now,
            id_factory=self.id_factory,
        )
        if self.settings.dev_social_autonomy:
            from .social_scheduler import SocialScheduler

            self.social_scheduler = SocialScheduler(self, self.settings.social_scheduler)

    async def social_scheduler_candidates(self, session):
        world = session.world
        stamp = self.clock()
        if (
            not world
            or not 0 <= stamp - world.get("_sampled_at", -float("inf")) <= 2
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
            or (session.task and not session.task.done())
            or (session.skill_task and not session.skill_task.done())
            or self.skill_scheduler_active(session)
            or session.speech_state != "idle"
            or any(
                command["kind"] in {"dialogue.chunk", "action.execute"}
                and command["state"]
                not in {"completed", "failed", "rejected", "expired", "canceled"}
                for command in session.commands.values()
            )
        ):
            return {}
        registered = {
            actor["actor_id"]: actor
            for actor in await self.db(
                "list_actors", session.request.profile_id, session_id=session.id
            )
        }
        result = {}
        for actor_id, actor in session.actors.items():
            record = registered.get(actor_id)
            if (
                not record
                or record["actor_kind"]
                not in {
                    "curated_persistent", "spawned_persistent",
                    "anchored_service", "ambient_ephemeral",
                }
                or record.get("flags", {}).get("no_spontaneous_speech")
                or actor.get("in_vehicle") or actor.get("mod_following")
                or not self.conversation_policy.check(actor, world).allowed
                or not 0 <= stamp - actor.get("_sampled_at", -float("inf")) <= 2
                or not actor.get("area_id")
                or not actor.get("interior_id")
                or actor.get("distance", float("inf")) > self.social_player_radius(session)
            ):
                continue
            result[actor_id] = actor
        return result

    def configure_social_fake(self, provider):
        if self.settings.dev_social and provider.name == "fake" and hasattr(provider, "primary"):
            from .social import FakeSocialProvider

            provider.primary = FakeSocialProvider()

    def authorize_social(self, session, body):
        self.ensure_current(session, body.runtime_generation)
        if (
            not self.settings.dev_social
            or self.settings.release
            or DEVELOPMENT_SOCIAL_CAPABILITY not in session.request.manifest.capability_flags
        ):
            raise HTTPException(403, "development social scenes are not authorized")
        if (
            body.session_id != session.id
            or body.protagonist_id != session.request.protagonist_id
            or body.game_datetime != datetime.fromisoformat(session.game_datetime)
        ):
            raise HTTPException(409, "social session, protagonist, or game time mismatch")

    def social_environment(self, session, scene_id):
        state = session.social_scenes.get(scene_id)
        if not state or state["canceled"] or session.closed or state["epoch"] != session.epoch:
            raise HTTPException(409, "social scene is no longer current")
        if self.social_scheduler is not None and self.social_scheduler.expired(
            session.id, scene_id
        ):
            raise HTTPException(409, "automatic social scene reached its time limit")
        stamp, wall = self.clock(), self.utc_now()
        world = session.world
        world_age = stamp - world.get("_sampled_at", float("-inf"))
        if not world or not 0 <= world_age <= 2:
            raise HTTPException(409, "social scenes require a fresh world snapshot")
        if world.get("protagonist_id") != session.request.protagonist_id:
            raise HTTPException(409, "social protagonist changed")
        actors = {}
        for actor_id, instance_id in state["participants"].items():
            actor = session.actors.get(actor_id)
            if (
                not actor
                or actor["runtime_instance_id"] != instance_id
                or actor.get("in_vehicle") or actor.get("mod_following")
                or not self.conversation_policy.check(actor, world).allowed
                or not actor.get("area_id")
                or not actor.get("interior_id")
                or (state.get("area_id") and actor["area_id"] != state["area_id"])
                or (state.get("interior_id") and actor["interior_id"] != state["interior_id"])
            ):
                raise HTTPException(409, "social actor binding is unavailable")
            age = stamp - actor.get("_sampled_at", float("-inf"))
            if not 0 <= age <= 2:
                raise HTTPException(409, "social scenes require fresh actor snapshots")
            actors[actor_id] = SocialActorState(
                actor_id=actor_id,
                runtime_instance_id=instance_id,
                sampled_at=wall - timedelta(seconds=age),
                activity=actor.get("activity"),
                **{
                    name: actor[name]
                    for name in (
                        "position",
                        "area_id",
                        "interior_id",
                        "exists",
                        "alive",
                        "busy",
                        "mission_entity",
                        "in_combat",
                        "ownership",
                        "control_policy",
                        "service_integration_id",
                    )
                },
            )
        foreground = bool(
            (session.task and not session.task.done() and session.foreground_actor_id in actors)
            or (session.speech_identity and session.speech_identity.actor_id in actors
                and session.speech_state not in {"idle", "refused", "failed"})
            or any(
                c["actor_id"] in actors and c["kind"] == "dialogue.chunk"
                and not c.get("_social")
                and c["state"] not in {"completed", "failed", "canceled", "expired", "rejected"}
                for c in session.commands.values()
            )
        )
        return SocialEnvironment(
            profile_id=session.request.profile_id,
            branch_id=session.branch_id,
            session_id=session.id,
            protagonist_id=session.request.protagonist_id,
            runtime_generation=session.request.runtime_generation,
            game_datetime=datetime.fromisoformat(session.game_datetime),
            sampled_at=wall - timedelta(seconds=world_age),
            actors=actors,
            player_present=all(
                session.actors[a]["distance"] <= self.social_player_radius(session) for a in actors
            ),
            foreground_dialogue=foreground,
            weather=world.get("weather"),
            danger=any(a.in_combat for a in actors.values()),
            **{
                name: world[name]
                for name in (
                    "online",
                    "mission_active",
                    "cutscene_active",
                    "random_event_active",
                    "paused",
                )
            },
        )

    def cancel_social(self, session):
        if self.social_scheduler is not None:
            self.social_scheduler.cancel(session)
        if not session.social_scenes and session.social_task is None:
            return
        for state in session.social_scenes.values():
            state["canceled"] = True
        if session.social_task is not None and not session.social_task.done():
            session.social_task.cancel()
            session.social_cleanup.append(session.social_task)
        session.social_cleanup.append(
            asyncio.create_task(self.social.cancel_session(session.id, reason="runtime_canceled"))
        )

    async def stop_session_social(self, session, *, erase=False):
        self.cancel_social(session)
        if self.social_scheduler is not None:
            await self.social_scheduler.join(session, erase=erase)
        for task in session.social_cleanup:
            if task is not asyncio.current_task():
                await self.thread_join_task(task)
        session.social_cleanup.clear()
        session.social_task = None
        if erase:
            if self.social is not None:
                await self.social.forget_session(session.id)
            session.social_scenes.clear()

    async def stop_session_work(self, session, *, erase=False, preserve_social=False):
        if self.skill_scheduler is not None:
            await self.skill_scheduler.join(session, erase=erase)
        if self.neighborhood.scheduler is not None:
            await self.neighborhood.scheduler.join(session, erase=erase)
        await self.memory_consolidator.join(session, erase=erase)
        await self.stop_session_skills(session, erase=erase)
        await self.stop_session_speech(session, erase=erase)
        if not preserve_social:
            await self.stop_session_social(session, erase=erase)
        await self.flush_outcomes(session)

    async def stop_social_scene(self, session, scene_id, reason="interrupted"):
        state = session.social_scenes.get(scene_id)
        if not state or state["canceled"]:
            return
        state["canceled"] = True
        scheduler = self.social_scheduler
        scheduled = scheduler.states.get(session.id) if scheduler else None
        if scheduled and scheduled.scene_id == scene_id:
            await scheduler.join(session)
        if scene_id in self.social.scenes:
            await self.social.cancel_scene(scene_id, reason)
        for command in session.commands.values():
            if command.get("_social", {}).get("scene_id") == scene_id and command["state"] not in {
                "completed", "failed", "rejected", "expired", "canceled"
            }:
                command["state"] = "canceled"
        self.command(session, "scene.cancel", None, {"scene_id": scene_id})

    async def interrupt_social_actor(self, session, actor_id):
        for scene_id, state in list(session.social_scenes.items()):
            if actor_id in state["participants"]:
                await self.stop_social_scene(session, scene_id, "player_addressed_participant")

    async def stop_actor_work(self, session, actor_id):
        """A nearby ped disappearing must not interrupt unrelated conversations."""
        for scene_id, state in list(session.social_scenes.items()):
            if actor_id in state["participants"]:
                await self.stop_social_scene(session, scene_id, "actor_unavailable")
        identity = session.speech_identity
        skill = session.skill_run
        active = (
            session.foreground_actor_id == actor_id
            or (identity is not None and identity.actor_id == actor_id)
            or (skill is not None and skill["identity"].actor_id == actor_id)
            or any(
                c.get("actor_id") == actor_id and not c.get("_social")
                and c["state"] not in {"completed", "failed", "rejected", "expired", "canceled"}
                for c in session.commands.values()
            )
        )
        if active:
            self.cancel(session, preserve_social=True)
            await self.stop_session_work(session, preserve_social=True)

    async def refresh_social(self, session):
        async with session.lock:
            await self.refresh_social_locked(session)

    async def refresh_social_locked(self, session):
        for scene_id, state in session.social_scenes.items():
            if state["canceled"]:
                continue
            try:
                self.social_environment(session, scene_id)
            except HTTPException:
                await self.stop_social_scene(session, scene_id, "revalidation_failed")

    async def social_call(self, session, body, operation):
        from .social import SocialError

        async with session.lock:
            self.authorize_social(session, body)
            if self.skill_scheduler_active(session):
                raise HTTPException(409, "automatic skill practice is already active")
            if session.skill_task is not None and not session.skill_task.done():
                raise HTTPException(409, "a skill operation is already active")
            if session.social_task is not None and not session.social_task.done():
                raise HTTPException(409, "a social operation is already active")
            if operation == "invite":
                owner = self.social_scene_owners.get(body.scene_id)
                if owner is not None and owner != session.id:
                    raise HTTPException(409, "social scene belongs to another session")
                if owner is None and len(self.social_scene_owners) >= 2048:
                    raise HTTPException(429, "social scene identity limit reached")
                self.social_scene_owners[body.scene_id] = session.id
                digest = hashlib.sha256(body.model_dump_json().encode()).hexdigest()
                previous = session.social_scenes.get(body.scene_id)
                if previous:
                    if previous["digest"] != digest or previous["canceled"]:
                        raise HTTPException(409, "social scene identity was already used")
                    return self.social.get_scene(body.scene_id)
                if len(session.social_scenes) >= 32:
                    raise HTTPException(429, "social scene limit reached")
                for actor_id in body.participants:
                    actor = await self.db(
                        "get_actor", session.request.profile_id, actor_id, session_id=session.id
                    )
                    if not actor or actor["actor_kind"] not in {
                        "curated_persistent",
                        "spawned_persistent",
                        "anchored_service",
                        "ambient_ephemeral",
                    }:
                        raise HTTPException(
                            409, "social scenes require registered current-session actors"
                        )
                session.social_scenes[body.scene_id] = {
                    "epoch": session.epoch,
                    "participants": dict(body.participants),
                    "digest": digest,
                    "canceled": False,
                }
                try:
                    environment = self.social_environment(session, body.scene_id)
                    initiator = environment.actors[body.initiator_id]
                    if self.social_embodied(session):
                        from math import dist

                        positions = [tuple(a.position.model_dump().values())
                                     for a in environment.actors.values()]
                        if any(dist(a, b) > 6 for a in positions for b in positions):
                            raise HTTPException(409, "native social pair exceeds approach radius")
                    session.social_scenes[body.scene_id].update(
                        area_id=initiator.area_id, interior_id=initiator.interior_id
                    )
                    start = SocialStart(
                        scene_id=body.scene_id,
                        profile_id=session.request.profile_id,
                        branch_id=session.branch_id,
                        session_id=session.id,
                        protagonist_id=session.request.protagonist_id,
                        runtime_generation=session.request.runtime_generation,
                        initiator_id=body.initiator_id,
                        participant_bindings=dict(body.participants),
                        area_id=initiator.area_id,
                        interior_id=initiator.interior_id,
                        game_datetime=body.game_datetime,
                        development_attestation=True,
                    )
                    work = self.social.invite(
                        start, lambda: self.social_environment(session, body.scene_id)
                    )
                except BaseException:
                    session.social_scenes.pop(body.scene_id, None)
                    raise
            else:
                self.social_environment(session, body.scene_id)
                if operation in {"turn", "review"}:
                    if body.actor_id not in session.social_scenes[body.scene_id]["participants"]:
                        raise HTTPException(409, "social actor is not a participant")
                    if operation == "turn":
                        work = self.social.generate_turn(
                            body.scene_id, body.actor_id, retell_event_id=body.retell_event_id
                        )
                    else:
                        work = self.social.review_participation(body.scene_id, body.actor_id)
                else:
                    command = session.commands.get(body.command_id)
                    metadata = (command or {}).get("_social")
                    receipt = session.delivered_utterances.get(body.command_id + ":delivered")
                    if not metadata or metadata["scene_id"] != body.scene_id or receipt is None:
                        raise HTTPException(409, "social delivery requires a confirmed exact quote")
                    self.validate_social_witnesses(session, body.witnesses)
                    work = self.social.acknowledge_delivery(
                        body.scene_id, metadata["utterance_id"], receipt, body.witnesses
                    )
            task = asyncio.create_task(work)
            session.social_task = task
        try:
            result = await task
            async with session.lock:
                self.ensure_current(session, body.runtime_generation)
                self.social_environment(session, body.scene_id)
                if operation == "invite" and result["status"] == "active" and self.social_embodied(
                    session
                ):
                    self.command(session, "social.scene", None, {
                        "scene_id": body.scene_id, "participants": dict(body.participants),
                        "duration_seconds": 45,
                    })
                if operation == "turn":
                    command = self.command(
                        session,
                        "dialogue.chunk",
                        result.actor_id,
                        {
                            "text": result.text,
                            "speaker": session.actors[result.actor_id]["name"],
                            "scene_id": body.scene_id,
                            "turn_id": result.utterance_id,
                            "chunk_index": 0,
                            "final": True,
                            **(
                                {"presentation": "overhead"}
                                if self.social_embodied(session) else {}
                            ),
                        },
                    )
                    command["_social"] = {
                        "scene_id": body.scene_id,
                        "utterance_id": result.utterance_id,
                    }
                    command["_autonomous"] = True
                    # Pollers and acknowledgments also take session.lock, so the runtime's
                    # copy of this line is linked to its carried lines before any delivery.
                    try:
                        await self.thread_job(
                            self.social.store.link_turn,
                            session.request.profile_id,
                            session.id,
                            result.actor_id,
                            command["id"] + ":delivered",
                            result.carrier_event_ids,
                        )
                    except ValueError as exc:
                        command["state"] = "canceled"
                        command["payload"]["text"] = ""
                        session.social_scenes[body.scene_id]["canceled"] = True
                        await self.social.cancel_scene(body.scene_id, "turn_provenance_unavailable")
                        self.command(session, "scene.cancel", None, {"scene_id": body.scene_id})
                        raise HTTPException(409, str(exc)) from exc
                    result = {
                        "scene_id": body.scene_id,
                        "utterance_id": result.utterance_id,
                        "command_id": command["id"],
                    }
                elif result.get("status") in {"refused", "withdrawn", "completed"}:
                    session.social_scenes[body.scene_id]["canceled"] = True
                    for command in session.commands.values():
                        if command.get("_social", {}).get("scene_id") == body.scene_id and command[
                            "state"
                        ] not in {"completed", "failed", "canceled", "expired", "rejected"}:
                            command["state"] = "canceled"
                    self.command(session, "scene.cancel", None, {"scene_id": body.scene_id})
                if operation == "delivery" and self.social_scheduler is not None:
                    self.social_scheduler.delivered(session, body.scene_id, body.command_id, result)
                return {**result, "development_social": True, "native_verified": False}
        except SocialError as exc:
            if operation == "invite":
                session.social_scenes[body.scene_id]["canceled"] = True
            raise HTTPException(409, str(exc)) from exc
        except asyncio.CancelledError:
            if asyncio.current_task().cancelling():
                raise
            raise HTTPException(409, "social operation was canceled") from None
        finally:
            if session.social_task is task:
                session.social_task = None

    def validate_social_witnesses(self, session, witnesses):
        wall, stamp = self.utc_now(), self.clock()
        for witness in witnesses:
            actor = session.actors.get(witness.actor_id)
            if (
                not actor
                or actor["runtime_instance_id"] != witness.runtime_instance_id
                or not 0 <= stamp - actor.get("_sampled_at", float("-inf")) <= 2
                or not 0 <= (wall - witness.sampled_at).total_seconds() <= 2
                or actor["position"] != witness.position.model_dump()
                or actor.get("area_id") != witness.area_id
                or actor.get("interior_id") != witness.interior_id
                or actor["exists"] != witness.exists
                or actor["alive"] != witness.alive
            ):
                raise HTTPException(409, "social listener evidence does not match its binding")
