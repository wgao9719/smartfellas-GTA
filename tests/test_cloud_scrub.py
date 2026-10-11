import json
from copy import deepcopy
from datetime import datetime, timezone

import httpx
import pytest

from service.lls.embeddings import EmbeddingConfig, OpenAIEmbeddingProvider
from service.lls.gateway import GatewayProvider
from service.lls.gateway_config import DailyBudget, GatewayConfig, ModelPrice
from service.lls.outbound import OutboundGate
from service.lls.providers import OpenAIProvider
from service.lls.settings import Settings
from service.lls.social import SocialEngine
from service.lls.social_models import SocialActorState, SocialConfig, SocialEnvironment, SocialStart
from service.lls.storage import Repository

PROVIDER_KEY = "synthetic-provider-credential"
BOOTSTRAP_TOKEN = "synthetic-configured-bridge-credential"
BRIDGE_TOKEN = "synthetic-environment-bridge-credential"
USERNAME = "PrivateFixtureUser"
PRIVATE_KEY = (
    "-----BEGIN " "PRIVATE KEY-----\n"
    "c3ludGhldGljLXByaXZhdGUta2V5LWZpeHR1cmU=\n"
    "-----END " "PRIVATE KEY-----"
)
SENSITIVE = {
    "provider-key": PROVIDER_KEY,
    "bootstrap-token": BOOTSTRAP_TOKEN,
    "environment-bridge-token": BRIDGE_TOKEN,
    "openai-format": "sk-" "proj-" + "Ab1Cd2Ef3Gh4Ij5Kl6Mn7Op8" * 2,
    "github" "-format": "ghp" "_" + "aB7d" * 9,
    "aws-format": "AKIA" "IOSFODNN7EXAMPLE",
    "jwt": "eyJhbGciOiJIUzI1NiJ9" ".eyJzdWIiOiJmaXh0dXJlIn0.c3ludGhldGljc2lnbmF0dXJl",
    "bearer": "Bearer synthetic-bearer-credential",
    "basic": "Basic Zml4dHVyZTpzeW50aGV0aWM=",
    "private-key": PRIVATE_KEY,
    "email": "alex@example.test",
    "phone": "+1 (212) 555-0199",
    "windows-profile": r"C:\Users\PrivateFixtureUser\Documents\notes.txt",
    "windows-profile-slashes": "C:/Users/PrivateFixtureUser/Documents/notes.txt",
    "username": USERNAME,
    "ipv4": "203.0.113.27",
    "ipv6": "2001:db8:1234::5",
    "payment-card": "4111 1111 1111 1111",
}
SOURCE = {
    "skill_id": "courtesy",
    "version": 1,
    "action_class": "courtesy",
    "max_duration_seconds": 1,
    "steps": [
        {
            "op": "action",
            "proposal": {"action_id": "face_player", "arguments": {"duration_seconds": 0.5}},
        }
    ],
}


def configuration(*, local=False):
    return Settings(
        bootstrap_token=BOOTSTRAP_TOKEN,
        provider="local_openai" if local else "openai",
        dialogue_model="synthetic-model",
        cloud_opt_in=True,
        embedding=EmbeddingConfig(
            provider="local_openai" if local else "openai", model="synthetic-model"
        ),
        ai_gateway=GatewayConfig(
            background=DailyBudget(max_requests=20, max_estimated_usd=1),
            prices={
                "synthetic-model": ModelPrice(input_usd_per_million=1, output_usd_per_million=2)
            },
        ),
    )


@pytest.fixture(autouse=True)
def synthetic_credentials(monkeypatch, caplog):
    caplog.set_level("DEBUG")
    monkeypatch.setenv("LLM_API_KEY", PROVIDER_KEY)
    monkeypatch.setenv("LLS_BRIDGE_TOKEN", BRIDGE_TOKEN)
    monkeypatch.setenv("USERNAME", USERNAME)
    monkeypatch.setenv("USERPROFILE", rf"C:\Users\{USERNAME}")


@pytest.fixture
async def outbound():
    requests = []

    def handle(request):
        requests.append(request)
        payload = json.loads(request.content)
        if request.url.path.endswith("/embeddings"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"index": index, "embedding": [1, 0]}
                        for index in range(len(payload["input"]))
                    ]
                },
            )
        if payload.get("stream"):
            return httpx.Response(200, content=b"data: [DONE]\n")
        content = json.dumps(SOURCE) if payload.get("response_format") else "null"
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}]},
        )

    settings = configuration()
    with Repository(":memory:") as repository:
        gate = OutboundGate(settings, repository)
        transport = httpx.MockTransport(handle)
        provider = OpenAIProvider(settings, gate=gate, transport=transport)
        embedding = OpenAIEmbeddingProvider(settings, gate=gate, transport=transport)
        try:
            yield provider, embedding, requests
        finally:
            await gate.aclose()


def assert_scrubbed(requests, values):
    assert requests
    for request in requests:
        # The real credential is necessary in its dedicated authentication header only.
        assert request.headers["Authorization"] == "Bearer " + PROVIDER_KEY
        body = request.content.decode()
        for value in values:
            assert value not in body
            assert json.dumps(value)[1:-1] not in body
            assert json.dumps(json.dumps(value))[1:-1] not in body


async def test_cloud_dialogue_does_not_send_personal_data(outbound):
    provider, _, requests = outbound
    assert [part async for part in provider.dialogue({"input": "Call alex@example.test."})] == []
    assert len(requests) == 1
    assert "alex@example.test" not in json.dumps(json.loads(requests[0].content))


async def test_cloud_scrubs_named_player_and_assistant_history_at_transport(outbound):
    provider, _, requests = outbound
    context = {
        "actor": {"display_name": "Avery"}, "protagonist_id": "franklin",
        "input": "My email is " + SENSITIVE["email"],
        "recent_conversation": [
            {"role": "player", "text": "The player said: Contact " + SENSITIVE["phone"]},
            {"role": "npc", "text": "I said: You mentioned " + SENSITIVE["windows-profile"]},
        ],
    }
    original = deepcopy(context)
    assert [part async for part in provider.dialogue(context)] == []
    assert_scrubbed(requests, [SENSITIVE["email"], SENSITIVE["phone"], USERNAME])
    messages = json.loads(requests[0].content)["messages"]
    assert messages[2] == {"role": "user", "content": "Franklin (the player): Contact [REDACTED]"}
    assert messages[3] == {"role": "assistant", "content": "You mentioned [REDACTED]"}
    assert messages[4] == {
        "role": "user", "content": "Franklin (the player): My email is [REDACTED]",
    }
    assert context == original


@pytest.mark.parametrize("sensitive", list(SENSITIVE.values()), ids=list(SENSITIVE))
@pytest.mark.parametrize("path", ["dialogue", "action", "embedding", "skill"])
async def test_cloud_payloads_scrub_sensitive_data_at_transport(outbound, caplog, path, sensitive):
    provider, embedding, requests = outbound
    text = "Meet me at the pier. " + sensitive
    context = {"input": text, "memories": [{"text": text}], "world": {"heard": [text]}}
    original = deepcopy(context)
    if path == "dialogue":
        assert [part async for part in provider.dialogue(context)] == []
    elif path == "action":
        assert await provider.action(context, text, ["wait_here"]) is None
    elif path == "embedding":
        assert await embedding.embed([text, json.dumps(context)]) == [[1, 0], [1, 0]]
    else:
        context["skill_request"] = {"skill_id": "courtesy", "version": 1}
        original = deepcopy(context)
        assert (await provider.skill_candidate(context))["skill_id"] == "courtesy"
    assert len(requests) == 1
    fragments = [sensitive]
    if sensitive.startswith(("Bearer ", "Basic ")):
        fragments.append(sensitive.split(" ", 1)[1])
    elif sensitive == PRIVATE_KEY:
        fragments.append("c3ludGhldGljLXByaXZhdGUta2V5LWZpeHR1cmU=")
    elif USERNAME in sensitive:
        fragments.append(USERNAME)
    assert_scrubbed(requests, fragments)
    assert "Meet me at the pier." in requests[0].content.decode()
    assert all(fragment not in caplog.text for fragment in fragments)
    assert context == original


async def test_nested_json_persona_and_sensitive_keys_are_scrubbed(outbound):
    provider, _, requests = outbound
    persona = {
        "bio": SENSITIVE["windows-profile"],
        "notes": PRIVATE_KEY,
        SENSITIVE["email"]: "Meet me at the pier.",
        "payment": 4111111111111111,
    }
    context = {"actor": {"persona": json.dumps(persona)}, "input": "Hello Franklin."}
    original = deepcopy(context)
    assert [part async for part in provider.dialogue(context)] == []
    assert_scrubbed(
        requests,
        [
            USERNAME,
            "c3ludGhldGljLXByaXZhdGUta2V5LWZpeHR1cmU=",
            SENSITIVE["email"],
            "4111111111111111",
        ],
    )
    body = json.loads(requests[0].content)
    sent_context = json.loads(body["messages"][1]["content"])
    assert "Meet me at the pier." in json.loads(sent_context["actor"]["persona"]).values()
    assert context == original


async def test_action_repair_scrubs_provider_echo_before_second_request():
    requests = []
    text = "Authorization: Bearer synthetic-echo-credential; alex@example.test"

    def handle(request):
        requests.append(request)
        content = text if len(requests) == 1 else "null"
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    settings = configuration()
    with Repository(":memory:") as repository:
        gate = OutboundGate(settings, repository)
        provider = OpenAIProvider(settings, gate=gate, transport=httpx.MockTransport(handle))
        try:
            assert await provider.action({"input": "Wait here."}, "Okay.", ["wait_here"]) is None
            assert len(requests) == 2
            assert len(json.loads(requests[1].content)["messages"]) == 4
            assert_scrubbed(requests, ["synthetic-echo-credential", "alex@example.test"])
        finally:
            await gate.aclose()


@pytest.mark.parametrize("path", ["dialogue", "action", "embedding", "skill"])
async def test_ordinary_game_content_is_unchanged(outbound, path):
    provider, embedding, requests = outbound
    text = (
        "Franklin, meet Trevor at Grove Street at 12:30. Wait 5 seconds, then walk 15 meters. "
        "Basic courtesy matters."
    )
    context = {
        "input": text,
        "memories": [{"text": "I owe Franklin $200 after the race."}],
        "position": {"x": 123.45, "y": -678.9, "z": 20.0},
        "game_datetime": "2025-01-01T10:00:00+00:00",
        "actor_id": "actor_1234567890123456",
    }
    if path == "dialogue":
        assert [part async for part in provider.dialogue(context)] == []
    elif path == "action":
        assert await provider.action(context, text, ["wait_here"]) is None
        context = {"input": text, "reply": text, "eligible": ["wait_here"]}
    elif path == "embedding":
        assert await embedding.embed([text, json.dumps(context)]) == [[1, 0], [1, 0]]
    else:
        context["skill_request"] = {"skill_id": "courtesy", "version": 1}
        assert (await provider.skill_candidate(context))["skill_id"] == "courtesy"
    payload = json.loads(requests[0].content)
    if path == "embedding":
        assert payload["input"] == [text, json.dumps(context)]
    else:
        assert json.loads(payload["messages"][1]["content"]) == context


async def test_local_provider_preserves_local_context():
    settings = configuration(local=True)
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, content=b"data: [DONE]\n")

    with Repository(":memory:") as repository:
        gate = OutboundGate(settings, repository)
        provider = OpenAIProvider(settings, gate=gate, transport=httpx.MockTransport(handle))
        context = {"input": SENSITIVE["email"], "memories": [{"text": USERNAME}]}
        try:
            assert [part async for part in provider.dialogue(context)] == []
            payload = json.loads(requests[0].content)
            assert json.loads(payload["messages"][1]["content"]) == context
            assert "Authorization" not in requests[0].headers
        finally:
            await gate.aclose()


async def test_social_invitation_and_turn_scrub_persona_and_heard_text():
    now = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
    requests = []

    def handle(request):
        requests.append(request)
        payload = json.loads(request.content)
        context = json.loads(payload["messages"][1]["content"])
        text = '{"decision":"accept"}' if context["social"]["phase"] == "invitation" else "Hello."
        data = json.dumps({"choices": [{"delta": {"content": text}}]})
        return httpx.Response(200, content=("data: " + data + "\ndata: [DONE]\n").encode())

    with Repository(":memory:") as repository:
        branch = repository.open_timeline("profile", now.isoformat())
        actors = {}
        for index, actor in enumerate(("avery", "bea")):
            repository.register_actor(
                "profile",
                {
                    "id": actor,
                    "display_name": actor,
                    "persona": {"bio": SENSITIVE["windows-profile"], "contact": SENSITIVE["email"]},
                },
            )
            actors[actor] = SocialActorState(
                actor_id=actor,
                runtime_instance_id=actor + "-1",
                sampled_at=now,
                position={"x": float(index), "y": 0, "z": 0},
                area_id="block",
                interior_id="street",
                exists=True,
                alive=True,
                busy=False,
                mission_entity=False,
                in_combat=False,
                ownership="ambient",
                control_policy="ambient",
            )
        start = SocialStart(
            scene_id="scene",
            profile_id="profile",
            branch_id=branch,
            session_id="session",
            protagonist_id="franklin",
            runtime_generation=1,
            initiator_id="avery",
            participant_bindings={actor: actor + "-1" for actor in actors},
            area_id="block",
            interior_id="street",
            game_datetime=now,
            development_attestation=True,
        )
        environment = SocialEnvironment(
            profile_id="profile",
            branch_id=branch,
            session_id="session",
            protagonist_id="franklin",
            runtime_generation=1,
            sampled_at=now,
            game_datetime=now,
            actors=actors,
            player_present=True,
            foreground_dialogue=False,
            danger=False,
            online=False,
            mission_active=False,
            cutscene_active=False,
            random_event_active=False,
            paused=False,
        )
        provider = GatewayProvider(
            configuration(), repository, transport=httpx.MockTransport(handle)
        )
        engine = SocialEngine(
            repository,
            provider,
            config=SocialConfig(enabled=True),
            utc_now=lambda: now,
        )
        try:
            assert (await engine.invite(start, lambda: environment))["status"] == "active"
            engine.scenes["scene"].heard["avery"] = [
                {"event_id": "heard", "teller_id": "bea", "text": SENSITIVE["phone"]}
            ]
            assert (await engine.generate_turn("scene", "avery")).text == "Hello."
            assert len(requests) == 3
            assert_scrubbed(requests, [SENSITIVE["email"], SENSITIVE["phone"], USERNAME])
        finally:
            await engine.aclose()
            await provider.aclose()
