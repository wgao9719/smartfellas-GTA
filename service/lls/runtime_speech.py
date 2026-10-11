"""Service-side speech orchestration with locked identities and joined teardown."""

import asyncio

from fastapi import HTTPException

from .perception import PerceptionLimits
from .protocol import Ack, Event
from .speech import SpeechUnavailable
from .speech_coordinator import SpeechCoordinator, SpeechIdentity


class SpeechRuntime:
    def initialize_speech(self, speech_factory=None):
        factory = speech_factory or SpeechCoordinator.local
        self.speech = factory(
            self.settings.speech,
            is_current=self.speech_identity_current,
            on_started=self.speech_playback_started,
            on_transcribing=self.speech_transcription_started,
        )

    def speech_identity(self, session, actor_id, scene_id):
        return SpeechIdentity(
            session.id,
            session.request.runtime_generation,
            scene_id,
            actor_id,
            session.epoch,
            session.actors[actor_id]["runtime_instance_id"],
        )

    def speech_identity_current(self, identity):
        session = self.sessions.get(identity.session_id)
        if session is None or session.closed or session.speech_identity != identity:
            return False
        actor = session.actors.get(identity.actor_id)
        return bool(
            identity.epoch == session.epoch
            and identity.runtime_generation == session.request.runtime_generation
            and actor
            and identity.runtime_instance_id == actor["runtime_instance_id"]
            and session.world.get("protagonist_id") == session.request.protagonist_id
            and self.conversation_policy.check(actor, session.world).allowed
        )

    def speech_status(self, session, identity, state, message):
        if self.speech_identity_current(identity):
            session.speech_state = state
            self.command(
                session,
                "status",
                identity.actor_id,
                {"state": state, "message": message, "scene_id": identity.scene_id},
            )

    def whisper_too_far(self, session, actor_id, scene_id, identity=None):
        """FR-PER-04: refuse a whisper the target cannot hear. Nothing opens or runs."""
        if session.actors[actor_id]["distance"] <= PerceptionLimits().whisper_hearing_meters:
            return False
        if identity is None:
            self.command(
                session,
                "status",
                actor_id,
                {"state": "unavailable", "message": "Too far to whisper.", "scene_id": scene_id},
            )
        else:
            self.speech_status(session, identity, "unavailable", "Too far to whisper.")
        return True

    def cancel_speech(self, session):
        identity = session.speech_identity
        session.speech_identity = None
        session.speech_state = "idle"
        session.speech_whisper = False
        session.speech_consent = False
        task = session.voice_task
        if task is not None and not task.done():
            task.cancel()
            session.speech_cleanup.append(task)
        if identity is not None:
            session.speech_cleanup.append(asyncio.create_task(self.speech.cancel(identity)))

    async def stop_session_speech(self, session, *, erase=False):
        """Caller can hold session.lock. No speech task may survive this boundary."""
        self.cancel_speech(session)
        tasks = list(session.speech_cleanup)
        if session.voice_task is not None:
            tasks.append(session.voice_task)
        for task in tasks:
            if task is asyncio.current_task():
                continue
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                if not task.done():
                    await self.thread_join_task(task)
            except Exception:
                pass
        session.speech_cleanup.clear()
        session.voice_task = None
        session.speech_playback = None
        for command in list(session.commands.values()):
            saved = command.pop("_subtitle_ack", None)
            if command.get("_audio_pending"):
                command["_audio_pending"] = False
            if not erase and saved and (saved.observed or {}).get("text"):
                await self.acknowledge_locked(session, command["id"], saved, internal_audio=True)
        if erase:
            await self.speech.forget(session_id=session.id)
            session.delivered_utterances.clear()
            session.player_speech.clear()
            for command in session.commands.values():
                command.pop("_subtitle_ack", None)

    def refresh_speech_status(self):
        for session in self.sessions.values():
            identity = session.speech_identity
            if identity is None or session.speech_state != "recording":
                continue
            receipt = next(
                (r for r in reversed(self.speech.receipts) if r.identity == identity), None
            )
            if receipt and receipt.kind == "capture" and receipt.state in {"failed", "canceled"}:
                self.speech_status(session, identity, receipt.state, "Not recording")

    @staticmethod
    async def thread_join_task(task):
        while not task.done():
            try:
                await asyncio.shield(task)
            except (asyncio.CancelledError, Exception):
                pass
        if not task.cancelled():
            task.exception()

    async def voice_control(self, session, event):
        actor = session.actors.get(event.actor_id)
        if not self.settings.speech.enabled:
            self.command(
                session,
                "status",
                event.actor_id,
                {
                    "state": "unavailable",
                    "message": "Speech is disabled. Use typed input.",
                    "scene_id": event.payload["scene_id"],
                },
            )
            return
        if (
            not self.conversation_policy.check(actor, session.world).allowed
            or actor["runtime_instance_id"] != event.payload["runtime_instance_id"]
        ):
            raise HTTPException(409, "push-to-talk target binding is unavailable")
        if event.kind == "voice.ptt_started":
            await self.interrupt_social_actor(session, event.actor_id)
            # A follow can receive a spoken stop after its preceding reply finished.
            terminal = {"completed", "rejected", "expired", "failed", "canceled"}
            pending = [c for c in session.commands.values()
                       if not c.get("_social") and c["state"] not in terminal
                       and c["kind"] in {"action.execute", "dialogue.chunk"}]
            continue_follow = (
                len(pending) == 1 and pending[0]["kind"] == "action.execute"
                and pending[0]["payload"]["action_id"] == "follow_player"
                and pending[0]["state"] == "started"
                and pending[0]["actor_id"] == event.actor_id
                and pending[0]["runtime_instance_id"] == actor["runtime_instance_id"]
                and pending[0]["payload"]["scene_id"] == event.payload["scene_id"]
                and actor.get("mod_following", False)
                and (session.task is None or session.task.done())
                and session.speech_state not in {
                    "opening_mic", "recording", "stopping_mic", "transcribing"
                }
                and session.skill_task is None and session.social_task is None
            )
            if continue_follow:
                self.cancel_speech(session)
            else:
                foreground_scenes = {c["payload"].get("scene_id") for c in pending}
                self.cancel(session, preserve_social=True)
                for scene_id in foreground_scenes:
                    if scene_id:
                        self.command(session, "scene.cancel", None, {"scene_id": scene_id})
            identity = self.speech_identity(session, event.actor_id, event.payload["scene_id"])
            session.speech_identity = identity
            # Volume is part of the capture lock. A stop cannot change it.
            session.speech_whisper = event.payload["whisper"]
            session.speech_consent = event.payload["consent"]
            if session.speech_whisper and self.whisper_too_far(
                session, event.actor_id, identity.scene_id, identity
            ):
                session.speech_state = "refused"
                return
            session.speech_state = "opening_mic"
            session.voice_task = asyncio.create_task(self.start_voice_capture(session, identity))
        else:
            identity = session.speech_identity
            scene_id = event.payload["scene_id"]
            if (
                identity is None
                or identity != self.speech_identity(session, event.actor_id, scene_id)
                or event.payload["whisper"] != session.speech_whisper
                or event.payload["consent"] != session.speech_consent
            ):
                raise HTTPException(409, "push-to-talk stop does not match its capture")
            if session.speech_state == "refused":
                # The refused start opened no microphone. Releasing the key only ends it.
                session.speech_identity, session.speech_state = None, "idle"
                session.speech_whisper = False
                return
            if session.speech_state not in {"opening_mic", "recording"}:
                raise HTTPException(409, "push-to-talk capture is not recording")
            start_task = session.voice_task
            session.speech_state = "stopping_mic"
            session.voice_task = asyncio.create_task(
                self.finish_voice_capture(session, identity, start_task, event)
            )

    async def start_voice_capture(self, session, identity):
        try:
            for task in list(session.speech_cleanup):
                await self.thread_join_task(task)
            session.speech_cleanup.clear()
            if not self.speech_identity_current(identity):
                return
            receipt = await self.speech.start_capture(identity)
            if receipt.state == "recording":
                stopping = session.speech_state == "stopping_mic"
                self.speech_status(session, identity, "recording", "Recording")
                if stopping:
                    session.speech_state = "stopping_mic"
            else:
                self.speech_status(session, identity, receipt.state, "Microphone capture failed.")
        except SpeechUnavailable:
            self.speech_status(
                session, identity, "unavailable", "Microphone capture is unavailable."
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            self.speech_status(session, identity, "failed", "Microphone capture failed.")

    async def finish_voice_capture(self, session, identity, start_task, event):
        try:
            if start_task is not None:
                await start_task
            if not self.speech_identity_current(identity):
                return
            receipt = await self.speech.finish_capture(identity)
            if not self.speech_identity_current(identity):
                return
            if receipt.state != "completed":
                self.speech_status(
                    session,
                    identity,
                    receipt.state,
                    "Didn't catch that. Try again."
                    if receipt.reason == "empty_transcript"
                    else "Transcription did not finish.",
                )
                return
            transcript = Event.model_validate(
                {
                    **event.model_dump(mode="json"),
                    "event_id": self.id_factory("transcript"),
                    "kind": "player.utterance_text",
                    "game_datetime": session.game_datetime,
                    "payload": {
                        "text": receipt.text,
                        "scene_id": identity.scene_id,
                        "input_mode": "voice",
                        "whisper": session.speech_whisper,
                        "consent": session.speech_consent,
                    },
                }
            )
            async with session.lock:
                if not self.speech_identity_current(identity):
                    return
                if await self.accept_utterance_locked(
                    session,
                    transcript,
                    voice_action_eligible=receipt.action_eligible,
                    voice_identity=identity,
                ):
                    self.speech_status(session, identity, "generating", "Thinking")
        except SpeechUnavailable:
            self.speech_status(session, identity, "unavailable", "Transcription is unavailable.")
        except asyncio.CancelledError:
            raise
        except Exception:
            self.speech_status(session, identity, "failed", "Transcription did not finish.")

    def speech_transcription_started(self, receipt):
        session = self.sessions.get(receipt.identity.session_id)
        if session is not None:
            self.speech_status(session, receipt.identity, "transcribing", "Transcribing")

    def speech_playback_started(self, receipt):
        session = self.sessions.get(receipt.identity.session_id)
        if not session or not self.speech_identity_current(receipt.identity):
            return
        pending = session.speech_playback
        if not pending or pending["identity"] != receipt.identity:
            return
        command = self.command(
            session,
            "dialogue.chunk",
            receipt.identity.actor_id,
            {
                **pending["payload"],
                "delivery_mode": "audio",
                "audio_duration_ms": receipt.audio_duration_ms,
            },
        )
        command["_audio_pending"] = True
        pending["command"] = command
        self.speech_status(session, receipt.identity, "speaking", "Speaking")

    async def deliver_dialogue_chunk(self, session, actor_id, payload):
        if (
            not payload["text"]
            or not self.settings.speech.enabled
            or self.settings.speech.tts_provider == "disabled"
        ):
            return self.command(session, "dialogue.chunk", actor_id, payload)
        identity = session.speech_identity
        if (
            identity is None
            or identity.scene_id != payload["scene_id"]
            or identity.actor_id != actor_id
        ):
            identity = self.speech_identity(session, actor_id, payload["scene_id"])
            session.speech_identity = identity
        if not self.speech_identity_current(identity):
            raise asyncio.CancelledError
        pending = {"identity": identity, "payload": payload, "command": None}
        session.speech_playback = pending
        try:
            receipt = await self.speech.speak(identity, payload["text"])
            command = pending["command"]
            if command is None:
                if not self.speech_identity_current(identity):
                    raise asyncio.CancelledError
                self.speech_status(
                    session, identity, "unavailable", "Voice playback failed. Showing subtitles."
                )
                return self.command(session, "dialogue.chunk", actor_id, payload)
            command["_audio_pending"] = False
            if receipt.state == "completed" and receipt.delivered_text == payload["text"]:
                ack = Ack(
                    session_id=session.id,
                    runtime_generation=identity.runtime_generation,
                    status="completed",
                    observed={"delivery": "speech", "text": receipt.delivered_text},
                )
            else:
                saved = command.pop("_subtitle_ack", None)
                ack = saved or Ack(
                    session_id=session.id,
                    runtime_generation=identity.runtime_generation,
                    status="canceled" if receipt.state == "canceled" else "failed",
                )
            await self.acknowledge(session, command["id"], ack, internal_audio=True)
            self.speech_status(session, identity, "idle", "Not recording")
            return command
        except SpeechUnavailable:
            self.speech_status(
                session,
                identity,
                "unavailable",
                "Voice playback is unavailable. Showing subtitles.",
            )
            return self.command(session, "dialogue.chunk", actor_id, payload)
        finally:
            if pending["command"] is not None:
                pending["command"]["_audio_pending"] = False
            session.speech_playback = None
