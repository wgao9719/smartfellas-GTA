import asyncio
import threading
import wave
from contextlib import asynccontextmanager

import httpx
import pytest

from service.lls.__main__ import fixture_event, fixture_session
from service.lls.app import create_app
from service.lls.display import is_display_safe
from service.lls.protocol import ActionProposal
from service.lls.providers import FakeProvider
from service.lls.settings import Settings
from service.lls.speech import Transcript, inspect_wav
from service.lls.speech_coordinator import SpeechConfig, SpeechCoordinator


def wav(path):
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(bytes(3200))
    return inspect_wav(path)


class Microphone:
    def __init__(self):
        self.active = False
        self.opened = threading.Event()
        self.release = threading.Event()
        self.release.set()
        self.stop_started = threading.Event()
        self.stop_release = threading.Event()
        self.stop_release.set()
        self.fail = False
        self.cancels = 0

    def start(self):
        self.opened.set()
        self.release.wait(5)
        if self.fail:
            raise RuntimeError("mock microphone failure")
        self.active = True

    def stop(self, destination):
        self.stop_started.set()
        self.stop_release.wait(5)
        self.active = False
        return wav(destination)

    def cancel(self):
        self.active = False
        self.cancels += 1


class Transcriber:
    def __init__(self):
        self.confidence = None
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()
        self.cleaned = False

    async def transcribe(self, path):
        self.started.set()
        try:
            await self.release.wait()
            return Transcript("Please look at me.", self.confidence, "mock-stt", 1)
        finally:
            self.cleaned = True


class Synthesizer:
    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()
        self.texts = []

    async def synthesize(self, text, destination):
        self.texts.append(text)
        self.started.set()
        await self.release.wait()
        return wav(destination)


class Playback:
    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()
        self.cleaned = False

    async def __call__(self, path, *, on_started):
        on_started()
        self.started.set()
        try:
            await self.release.wait()
        finally:
            self.cleaned = True


class Provider(FakeProvider):
    def __init__(self):
        self.contexts = []
        self.actions = 0
        self.reply = "Hello there."

    async def dialogue(self, context):
        self.contexts.append(context)
        yield self.reply

    async def action(self, context, utterance, eligible):
        self.actions += 1
        return ActionProposal(action_id="face_player")


@pytest.fixture
def speech_scene(tmp_path):
    @asynccontextmanager
    async def create(*, enabled=True, tts="piper"):
        microphone, transcriber = Microphone(), Transcriber()
        synthesizer, playback, provider = Synthesizer(), Playback(), Provider()

        def factory(config, **kwargs):
            return SpeechCoordinator(
                config,
                microphone=microphone,
                transcriber=transcriber,
                synthesizer=synthesizer,
                playback=playback,
                **kwargs,
            )

        app = create_app(
            Settings(
                database_path=str(tmp_path / "speech.db"),
                provider="fake",
                speech=SpeechConfig(enabled=enabled, tts_provider=tts),
            ),
            provider=provider,
            runtime_options={"speech_factory": factory},
        )
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 1)),
                base_url="http://127.0.0.1:8765",
            ) as client:
                opened = (await client.post("/v1/bridge/sessions", json=fixture_session())).json()
                client.headers["Authorization"] = "Bearer " + opened["token"]
                runtime = app.state.runtime
                session = runtime.sessions[opened["session_id"]]
                events = [
                    fixture_event(
                        session.id,
                        1,
                        "world.snapshot",
                        {
                            "online": False,
                            "mission_active": False,
                            "cutscene_active": False,
                            "random_event_active": False,
                            "paused": False,
                            "protagonist_id": "franklin",
                        },
                    ),
                    fixture_event(
                        session.id,
                        2,
                        "actor.snapshot",
                        {
                            "runtime_handle": 42,
                            "runtime_instance_id": "ped-1",
                            "ownership": "ambient",
                            "control_policy": "ambient",
                            "exists": True,
                            "alive": True,
                            "distance": 2,
                            "mission_entity": False,
                            "busy": False,
                            "position": {"x": 0, "y": 0, "z": 0},
                            "name": "Neighbor",
                        },
                        "npc-1",
                    ),
                ]
                response = await client.post(
                    "/v1/bridge/events:batch", json={"session_id": session.id, "events": events}
                )
                assert response.status_code == 200
                yield (
                    client,
                    runtime,
                    session,
                    microphone,
                    transcriber,
                    synthesizer,
                    playback,
                    provider,
                )

    return create


async def control(client, session, kind, *, scene="voice-scene", instance="ped-1", seq=None):
    event = fixture_event(
        session.id,
        seq or session.last_seq + 1,
        kind,
        {"scene_id": scene, "runtime_instance_id": instance},
        "npc-1",
    )
    return await client.post("/v1/bridge/control", json=event)


async def say(client, session):
    event = fixture_event(
        session.id,
        session.last_seq + 1,
        "player.utterance_text",
        {"text": "Hello.", "scene_id": "typed-scene"},
        "npc-1",
    )
    return await client.post(
        "/v1/bridge/events:batch", json={"session_id": session.id, "events": [event]}
    )


def statuses(session):
    return [c["payload"]["state"] for c in session.commands.values() if c["kind"] == "status"]


def memories(runtime, session):
    return runtime.repo.retrieve_memories(
        "fake-demo",
        session.branch_id,
        "npc-1",
        "franklin",
        session.game_datetime,
        "Hello",
        session_id=session.id,
    )


async def test_speech_disabled_does_not_open_devices(speech_scene):
    async with speech_scene(enabled=False) as (client, runtime, session, microphone, *rest):
        response = await control(client, session, "voice.ptt_started")
        assert response.status_code == 200
        assert not microphone.opened.is_set()
        assert statuses(session) == ["unavailable"]


async def test_recording_status_requires_successful_microphone_open(speech_scene):
    async with speech_scene() as (client, runtime, session, microphone, *rest):
        microphone.release.clear()
        assert (await control(client, session, "voice.ptt_started")).status_code == 200
        assert await asyncio.to_thread(microphone.opened.wait, 1)
        assert "recording" not in statuses(session)
        microphone.release.set()
        await session.voice_task
        assert microphone.active
        assert statuses(session)[-1] == "recording"


async def test_microphone_failure_never_claims_recording(speech_scene):
    async with speech_scene() as (client, runtime, session, microphone, *rest):
        microphone.fail = True
        assert (await control(client, session, "voice.ptt_started")).status_code == 200
        await session.voice_task
        assert "recording" not in statuses(session)
        assert statuses(session)[-1] == "failed"


async def test_transcribing_status_waits_for_microphone_stop(speech_scene):
    async with speech_scene() as values:
        client, runtime, session, microphone, transcriber, *_ = values
        microphone.stop_release.clear()
        transcriber.release.clear()
        await control(client, session, "voice.ptt_started")
        await session.voice_task
        await control(client, session, "voice.ptt_stopped")
        assert await asyncio.to_thread(microphone.stop_started.wait, 1)
        assert microphone.active
        assert "transcribing" not in statuses(session)
        assert not transcriber.started.is_set()
        microphone.stop_release.set()
        await asyncio.wait_for(transcriber.started.wait(), 1)
        assert not microphone.active
        assert statuses(session)[-1] == "transcribing"
        transcriber.release.set()
        await session.voice_task
        await session.task


@pytest.mark.parametrize("confidence,actions", [(None, 0), (0.5, 0), (0.9, 1)])
async def test_ptt_uses_dialogue_pipeline_with_internal_confidence_gate(
    speech_scene, confidence, actions
):
    async with speech_scene() as values:
        client, runtime, session, microphone, transcriber, synthesizer, playback, provider = values
        transcriber.confidence = confidence
        assert (await control(client, session, "voice.ptt_started")).status_code == 200
        await session.voice_task
        assert (await control(client, session, "voice.ptt_stopped")).status_code == 200
        await session.voice_task
        assert session.task is not None
        await session.task
        assert provider.contexts[0]["input"] == "Please look at me."
        assert provider.actions == actions
        assert sum(c["kind"] == "action.execute" for c in session.commands.values()) == actions
        assert not microphone.active
        assert transcriber.cleaned and playback.cleaned
        assert (
            len([m for m in memories(runtime, session) if m["text"] == "I said: Hello there."]) == 1
        )


@pytest.mark.parametrize("field", ["scene", "instance"])
async def test_ptt_stop_cannot_change_locked_target(speech_scene, field):
    async with speech_scene() as (client, runtime, session, microphone, *rest):
        assert (await control(client, session, "voice.ptt_started")).status_code == 200
        await session.voice_task
        response = await control(client, session, "voice.ptt_stopped", **{field: "different"})
        assert response.status_code == 409
        assert microphone.active


async def test_cancel_joins_transcription_and_prevents_late_turn(speech_scene):
    async with speech_scene() as values:
        client, runtime, session, microphone, transcriber, synthesizer, playback, provider = values
        transcriber.release.clear()
        await control(client, session, "voice.ptt_started")
        await session.voice_task
        await control(client, session, "voice.ptt_stopped")
        await asyncio.wait_for(transcriber.started.wait(), 1)
        cancel = fixture_event(session.id, session.last_seq + 1, "scene.cancel", {})
        response = await client.post("/v1/bridge/control", json=cancel)
        assert response.status_code == 200
        assert transcriber.cleaned
        assert not microphone.active
        assert provider.contexts == []
        assert not memories(runtime, session)


async def test_audio_subtitle_appears_only_at_playback_start_and_memory_is_once(speech_scene):
    async with speech_scene() as values:
        client, runtime, session, microphone, transcriber, synthesizer, playback, provider = values
        synthesizer.release.clear()
        playback.release.clear()
        assert (await say(client, session)).status_code == 200
        await asyncio.wait_for(synthesizer.started.wait(), 1)
        assert not any(c["kind"] == "dialogue.chunk" for c in session.commands.values())
        synthesizer.release.set()
        await asyncio.wait_for(playback.started.wait(), 1)
        command = next(c for c in session.commands.values() if c["kind"] == "dialogue.chunk")
        assert command["payload"]["delivery_mode"] == "audio"
        assert not any(m["text"].startswith("I said:") for m in memories(runtime, session))
        ack = {
            "session_id": session.id,
            "runtime_generation": 1,
            "status": "completed",
            "observed": {"delivery": "subtitle", "text": command["payload"]["text"]},
        }
        assert (
            await client.post(f"/v1/bridge/commands/{command['id']}/ack", json=ack)
        ).status_code == 200
        playback.release.set()
        await session.task
        assert (
            await client.post(f"/v1/bridge/commands/{command['id']}/ack", json=ack)
        ).status_code == 200
        assert (
            len([m for m in memories(runtime, session) if m["text"] == "I said: Hello there."]) == 1
        )


async def test_speech_and_audio_subtitles_use_identical_display_text(speech_scene):
    async with speech_scene() as values:
        client, runtime, session, microphone, transcriber, synthesizer, playback, provider = values
        provider.reply = "Hi ~r~there~s~. <C>Now</C>!"
        assert (await say(client, session)).status_code == 200
        await session.task
        audio = [
            c["payload"]["text"]
            for c in session.commands.values()
            if c["kind"] == "dialogue.chunk" and c["payload"].get("delivery_mode") == "audio"
        ]
        assert audio == synthesizer.texts == ["Hi there. ", "Now!"]
        assert all(is_display_safe(text) for text in audio)


async def test_completed_audio_keeps_subtitle_command_available_until_render_ack(speech_scene):
    async with speech_scene() as values:
        client, runtime, session, microphone, transcriber, synthesizer, playback, provider = values
        assert (await say(client, session)).status_code == 200
        await session.task
        commands = (await client.get("/v1/bridge/commands?wait_seconds=0")).json()["commands"]
        assert any(
            c["kind"] == "dialogue.chunk" and c["payload"].get("delivery_mode") == "audio"
            for c in commands
        )


async def test_close_joins_playback_and_erases_retained_receipts(speech_scene):
    async with speech_scene() as values:
        client, runtime, session, microphone, transcriber, synthesizer, playback, provider = values
        playback.release.clear()
        await say(client, session)
        await asyncio.wait_for(playback.started.wait(), 1)
        response = await client.post(
            f"/v1/bridge/sessions/{session.id}/close", json={"runtime_generation": 1}
        )
        assert response.status_code == 200
        assert playback.cleaned
        assert runtime.speech.receipts == ()
        assert session.closed and session.speech_identity is None


async def test_cancel_waits_for_opening_microphone_cleanup(speech_scene):
    async with speech_scene() as (client, runtime, session, microphone, *rest):
        microphone.release.clear()
        await control(client, session, "voice.ptt_started")
        assert await asyncio.to_thread(microphone.opened.wait, 1)
        cancel = fixture_event(session.id, session.last_seq + 1, "scene.cancel", {})
        pending = asyncio.create_task(client.post("/v1/bridge/control", json=cancel))
        await asyncio.sleep(0.01)
        assert not pending.done()
        microphone.release.set()
        assert (await asyncio.wait_for(pending, 1)).status_code == 200
        assert not microphone.active
        assert microphone.cancels > 0
        assert "recording" not in statuses(session)


@pytest.mark.parametrize("late", [False, True])
async def test_interrupted_audio_records_only_rendered_subtitle_prefix(speech_scene, late):
    async with speech_scene() as values:
        client, runtime, session, microphone, transcriber, synthesizer, playback, provider = values
        playback.release.clear()
        await say(client, session)
        await asyncio.wait_for(playback.started.wait(), 1)
        command = next(c for c in session.commands.values() if c["kind"] == "dialogue.chunk")
        ack = {
            "session_id": session.id,
            "runtime_generation": 1,
            "status": "canceled",
            "observed": {"delivery": "subtitle", "text": "Hello"},
        }
        if not late:
            assert (
                await client.post(f"/v1/bridge/commands/{command['id']}/ack", json=ack)
            ).status_code == 200
        cancel = fixture_event(session.id, session.last_seq + 1, "scene.cancel", {})
        assert (await client.post("/v1/bridge/control", json=cancel)).status_code == 200
        if late:
            assert (
                await client.post(f"/v1/bridge/commands/{command['id']}/ack", json=ack)
            ).status_code == 200
        spoken = [m["text"] for m in memories(runtime, session) if m["text"].startswith("I said:")]
        assert spoken == ["I said: Hello"]
        assert playback.cleaned


@pytest.mark.parametrize("operation", ["transcription", "playback"])
async def test_admin_erasure_hook_joins_speech_before_history_reset(speech_scene, operation):
    async with speech_scene() as values:
        client, runtime, session, microphone, transcriber, synthesizer, playback, provider = values
        if operation == "transcription":
            transcriber.release.clear()
            await control(client, session, "voice.ptt_started")
            await session.voice_task
            await control(client, session, "voice.ptt_stopped")
            await asyncio.wait_for(transcriber.started.wait(), 1)
        else:
            playback.release.clear()
            await say(client, session)
            await asyncio.wait_for(playback.started.wait(), 1)
            command = next(c for c in session.commands.values() if c["kind"] == "dialogue.chunk")
            ack = {
                "session_id": session.id,
                "runtime_generation": 1,
                "status": "completed",
                "observed": {"delivery": "subtitle", "text": command["payload"]["text"]},
            }
            await client.post(f"/v1/bridge/commands/{command['id']}/ack", json=ack)
        async with session.lock:
            runtime.cancel(session)
            await runtime.stop_session_speech(session, erase=True)
            if session.task:
                await asyncio.gather(session.task, return_exceptions=True)
            await runtime.db("reset_actor", "fake-demo", "npc-1")
        assert runtime.speech.receipts == ()
        assert not memories(runtime, session)
        assert session.speech_identity is None
        assert not microphone.active
        assert transcriber.cleaned if operation == "transcription" else playback.cleaned
