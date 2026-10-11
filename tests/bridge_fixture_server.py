"""Explicit synthetic HTTP server for the C# contract probe. No audio hardware."""

import argparse
import asyncio
import math
import struct
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uvicorn  # noqa: E402

from service.lls.app import create_app  # noqa: E402
from service.lls.settings import Settings  # noqa: E402
from service.lls.speech import Transcript, inspect_wav  # noqa: E402
from service.lls.speech_coordinator import SpeechConfig, SpeechCoordinator  # noqa: E402


def fixture_wav(path, amplitude=0):
    # The microphone records a quiet tone, because the silence gate refuses digital silence.
    samples = (round(amplitude * math.sin(n / 3)) for n in range(4800))
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(struct.pack("<4800h", *samples))
    return inspect_wav(path)


class FixtureMicrophone:
    active = False

    def start(self):
        if self.active:
            raise RuntimeError("fixture microphone is already active")
        self.active = True

    def stop(self, path):
        if not self.active:
            raise RuntimeError("fixture microphone was never opened")
        self.active = False
        return fixture_wav(path, 1000)

    def cancel(self):
        self.active = False


class FixtureTranscriber:
    async def transcribe(self, path):
        inspect_wav(path)
        return Transcript("Please look at me.", 0.99, "contract_fixture", 0)


class FixtureSynthesizer:
    async def synthesize(self, text, path):
        if not text.strip():
            raise ValueError("fixture synthesis requires text")
        return fixture_wav(path)


async def fixture_playback(path, *, on_started):
    inspect_wav(path)
    on_started()
    await asyncio.sleep(0.3)


def fixture_speech(config, *, is_current, on_started, on_transcribing=None):
    return SpeechCoordinator(
        config,
        is_current=is_current,
        on_started=on_started,
        on_transcribing=on_transcribing,
        microphone=FixtureMicrophone(),
        transcriber=FixtureTranscriber(),
        synthesizer=FixtureSynthesizer(),
        playback=fixture_playback,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--speech", action="store_true")
    parser.add_argument("--ambient-social", action="store_true")
    parser.add_argument("--social", action="store_true")
    parser.add_argument("--social-autonomy", action="store_true")
    parser.add_argument("--skills", action="store_true")
    parser.add_argument("--skill-autonomy", action="store_true")
    args = parser.parse_args()
    if args.social_autonomy and not args.social:
        parser.error("--social-autonomy requires --social")
    if args.skill_autonomy and not args.skills:
        parser.error("--skill-autonomy requires --skills")
    if args.ambient_social:
        args.social = args.social_autonomy = True
    settings = Settings(
        host="127.0.0.1",
        port=args.port,
        database_path=args.database,
        provider="fake",
        cloud_opt_in=False,
        release=not (args.social or args.skills),
        dev_social=args.social,
        dev_social_autonomy=args.social_autonomy,
        dev_skills=args.skills,
        dev_skill_autonomy=args.skill_autonomy,
        skill_scheduler={"actor_cooldown_seconds": 0},
        social_scheduler={"max_turns_per_scene": 4 if args.ambient_social else 2},
        speech=SpeechConfig(
            enabled=args.speech, tts_provider="piper" if args.speech else "disabled"
        ),
    )
    options = {"speech_factory": fixture_speech} if args.speech else {}
    app = create_app(settings, runtime_options=options)
    if args.social:
        repository = app.state.repository
        repository.create_profile("social-probe")
        for index in range(3):
            repository.register_actor(
                "social-probe",
                {
                    "id": f"social-npc-{index}",
                    "display_name": f"Social Fixture {index}",
                    "actor_kind": "curated_persistent",
                    "control_policy": "ambient",
                    "allowed_actions": [],
                    "persona": {
                        "social": {
                            "accept_invitations": True,
                            "opinion": [
                                "I enjoy quiet streets.",
                                "I prefer the busy boardwalk.",
                                "I like the park at sunrise.",
                            ][index],
                        }
                    },
                },
            )
    if args.skills:
        repository = app.state.repository
        branch = repository.open_timeline("skill-probe", "2013-09-17T12:00:00")
        repository.register_actor(
            "skill-probe",
            {
                "id": "skill-npc",
                "display_name": "Skill Fixture",
                "actor_kind": "curated_persistent",
                "control_policy": "ambient",
                "allowed_actions": ["face_player", "wait_here"],
            },
        )
        repository.record_event(
            "skill-probe",
            branch,
            {
                "event_id": "skill-need",
                "event_type": "player.utterance_text",
                "game_datetime": "2013-09-17T12:00:00",
                "protagonist_id": "franklin",
                "observer_ids": ["skill-npc"],
                "provenance": "heard",
                "payload": {"text": "Please face me and wait briefly."},
            },
        )
    uvicorn.run(app, host=settings.host, port=settings.port, access_log=False)


if __name__ == "__main__":
    main()
