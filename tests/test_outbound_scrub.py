import json
import traceback

import httpx
import pytest

from service.lls.gateway_config import GatewayConfig
from service.lls.outbound import OutboundDenied
from service.lls.outbound_scrub import REDACTED, OutboundScrubber, ScrubbedModelClient
from service.lls.settings import Settings


@pytest.fixture
def scrubber(monkeypatch):
    for name in ("LLM_API_KEY", "LLS_BRIDGE_TOKEN", "USERNAME", "USERPROFILE"):
        monkeypatch.delenv(name, raising=False)
    return OutboundScrubber(Settings())


@pytest.mark.parametrize(
    "sensitive",
    [
        "sk-" "abcdefghijklmnopqrstuvwxyz0123456789",
        "sk-" "proj-abcdefghijklmnopqrstuvwxyz0123456789",
        "sk-" "svcacct-abcdefghijklmnopqrstuvwxyz0123456789",
        "sk-" "ant-api03-abcdefghijklmnopqrstuvwxyz0123456789",
        "ghp" "_abcdefghijklmnopqrstuvwxyz0123456789",
        "gho" "_abcdefghijklmnopqrstuvwxyz0123456789",
        "github" "_pat_abcdefghijklmnopqrstuvwxyz0123456789",
        "AKIA" "ABCDEFGHIJKLMNOP",
        "ASIA" "ABCDEFGHIJKLMNOP",
        "AI" "zaABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
        "xo" "xb-123456789012-123456789012-abcdefghijklmnopqrstuvwxyz",
        "xa" "pp-1-abcdefghijklmnopqrstuvwxyz",
        "hf" "_abcdefghijklmnopqrstuvwxyz0123456789",
        "eyJhbGciOiJIUzI1NiJ9" ".eyJzdWIiOiJzeW50aGV0aWMifQ.c3ludGhldGljc2lnbmF0dXJl",
        "Bearer synthetic-access-value",
        "Authorization: Bearer abc",
        "Basic dGVzdHVzZXI6c2VjcmV0",
        "Basic dTpw",
        "api_key=synthetic-value",
        'api-key="synthetic value"',
        "client_secret: 'synthetic value'",
        "password=synthetic-value",
        "token: synthetic-value",
        "pilot+test@example.invalid",
        "+1 (212) 555-0199",
        "212-555-0199",
        "555-0100",
        "212.555.0199",
        "2125550199",
        "4111 1111 1111 1111",
        "5555-5555-5555-4444",
        "378282246310005",
        "192.0.2.17",
        "2001:db8::1234",
        "::1",
        "fe80::abcd%eth0",
        r"C:\Users\SyntheticPilot\Documents\notes.txt",
        "C:/Users/SyntheticPilot/Documents/notes.txt",
        r"C:\Documents and Settings\SyntheticPilot\Desktop\notes.txt",
        "username=synthetic-account",
        "windows_user: synthetic-account",
    ],
)
def test_each_sensitive_format_is_removed_independently(scrubber, sensitive):
    result = scrubber.scrub({"input": "Remember " + sensitive + ". Meet at the pier."})
    assert sensitive not in result["input"]
    assert REDACTED in result["input"]
    assert "Meet at the pier." in result["input"]


@pytest.mark.parametrize(
    ("scheme", "credential"),
    [("Bearer", "abc"), ("Basic", "dTpw"), ("Basic", "dGVzdHVzZXI6c2VjcmV0")],
)
def test_auth_redaction_removes_the_entire_credential(scrubber, scheme, credential):
    value = "Authorization: " + scheme + " " + credential + ". Meet Franklin."
    result = scrubber.scrub(value)
    assert credential not in result
    assert "Meet Franklin." in result


@pytest.mark.parametrize("kind", ["", "RSA ", "EC ", "OPENSSH ", "ENCRYPTED "])
def test_private_key_blocks_are_removed_with_their_contents(scrubber, kind):
    private_key = (
        f"-----BEGIN {kind}PRIVATE KEY-----\n"
        "c3ludGhldGljLXByaXZhdGUta2V5\n"
        f"-----END {kind}PRIVATE KEY-----"
    )
    assert scrubber.scrub("At the pier. " + private_key + " Meet Franklin.") == (
        "At the pier. " + REDACTED + " Meet Franklin."
    )


def test_incomplete_private_key_block_is_removed(scrubber):
    value = "At the pier. -----BEGIN " "PRIVATE KEY-----\nc3ludGhldGlj"
    assert scrubber.scrub(value) == "At the pier. " + REDACTED


@pytest.mark.parametrize(
    "text",
    [
        "Franklin, meet Michael and Trevor at the pier.",
        "Basic courtesy goes a long way. I am the bearer of bad news.",
        "Meet at 2026-10-11 12:30 by the pier.",
        "Wait 5 seconds, follow for 15 meters, and bring $250.",
        "Coordinates: (123.45, -456.78, 9.0). Heading: 180.0.",
        "Actor 550e8400-e29b-41d4-a716-446655440000 saw a red car.",
        "The score was 999999999, and the date was 2026-10-11.",
        "I heard this from a stranger; I do not know whether it is true.",
        "Can you face me, wait here, and follow_player when asked?",
    ],
)
def test_ordinary_game_dialogue_is_unchanged(scrubber, text):
    assert scrubber.scrub(text) == text


def test_game_context_and_json_formatting_are_unchanged_without_sensitive_data(scrubber):
    context = {
        "actor": {"display_name": "Franklin", "health": 100},
        "coordinates": {"x": -123.45, "y": 678.9, "z": 25.0},
        "input": "Meet me at the pier.",
        "memories": [{"text": "I saw a red car.", "confidence": 0.8}],
        "eligible": ["face_player", "wait_here"],
        "context_truncated": False,
        "optional": None,
    }
    serialized = json.dumps(context, indent=2)
    assert scrubber.scrub(context) == context
    assert scrubber.scrub(serialized) == serialized


def test_configured_secrets_are_removed_by_value_in_escaped_nested_json(monkeypatch):
    secrets = ['bridge-"quoted"\\value', "provider/$value+with=symbols", "env-bridge\nvalue"]
    monkeypatch.setenv("LLM_API_KEY", secrets[1])
    monkeypatch.setenv("LLS_BRIDGE_TOKEN", secrets[2])
    scrubber = OutboundScrubber(Settings(bootstrap_token=secrets[0]))
    context = {"input": " / ".join(secrets), "memories": list(secrets)}
    payload = {"messages": [{"content": json.dumps(json.dumps(context))}]}
    original = json.loads(json.dumps(payload))
    result = scrubber.scrub(payload)
    decoded = json.loads(json.loads(result["messages"][0]["content"]))
    assert decoded == {"input": " / ".join([REDACTED] * 3), "memories": [REDACTED] * 3}
    assert payload == original


@pytest.mark.parametrize("ensure_ascii", [False, True])
def test_clipped_persona_json_still_redacts_escaped_configured_secrets(monkeypatch, ensure_ascii):
    secret = 'synthetic-"quoted"\\caf\u00e9\nvalue'
    monkeypatch.setenv("LLM_API_KEY", secret)
    scrubber = OutboundScrubber(Settings())
    clipped = json.dumps({"memory": secret}, ensure_ascii=ensure_ascii)[:-1]
    with pytest.raises(ValueError):
        json.loads(clipped)
    result = scrubber.scrub({"persona": clipped})
    assert result == {"persona": '{"memory": "' + REDACTED + '"'}


def test_profile_usernames_are_removed_anywhere_in_the_same_payload(scrubber):
    context = {
        "memories": ["SyntheticPilot met Franklin."],
        "input": json.dumps({"path": r"C:\Users\SyntheticPilot\Documents\notes.txt"}),
    }
    result = scrubber.scrub(context)
    assert "SyntheticPilot" not in json.dumps(result)
    assert result["memories"] == [REDACTED + " met Franklin."]


def test_spaced_windows_profile_and_username_are_removed(scrubber):
    value = r"C:\Users\Alex Smith\Documents\note.txt; Alex Smith met Franklin."
    assert scrubber.scrub(value) == REDACTED + "; " + REDACTED + " met Franklin."


def test_current_windows_username_and_profile_basename_are_removed(monkeypatch):
    monkeypatch.setenv("USERNAME", "SyntheticAccount")
    monkeypatch.setenv("USERPROFILE", r"D:\Users\Synthetic Profile")
    scrubber = OutboundScrubber(Settings())
    value = "SyntheticAccount and synthetic profile met Franklin, Michael and Trevor."
    assert scrubber.scrub(value) == (
        REDACTED + " and " + REDACTED + " met Franklin, Michael and Trevor."
    )


def test_sensitive_dictionary_keys_values_and_numeric_cards_are_removed(scrubber):
    result = scrubber.scrub(
        {
            "pilot@example.invalid": "A red car.",
            "authorization": "synthetic-header-value",
            "api_key": "short",
            "client_secret": "synthetic-secret-value",
            "card": 4111111111111111,
            "count": 12,
            "duration_seconds": 5.5,
            "enabled": True,
        }
    )
    assert result == {
        REDACTED: "A red car.",
        "authorization": REDACTED,
        "api_key": REDACTED,
        "client_secret": REDACTED,
        "card": REDACTED,
        "count": 12,
        "duration_seconds": 5.5,
        "enabled": True,
    }


async def test_new_caller_using_raw_json_content_cannot_bypass_boundary(monkeypatch, caplog):
    monkeypatch.setenv("LLM_API_KEY", "synthetic-configured-value")
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"ok": True})

    payload = {
        "messages": [{"content": "synthetic-configured-value pilot@example.invalid"}],
        "extra": json.dumps({"memory": r"C:\Users\SyntheticPilot\notes.txt"}),
    }
    async with ScrubbedModelClient(
        Settings(), local=False, transport=httpx.MockTransport(handle)
    ) as client:
        response = await client.post(
            "https://provider.example.invalid/new-call",
            content=json.dumps(payload).encode(),
            headers={"content-type": "application/json"},
        )
    assert response.status_code == 200
    assert len(requests) == 1
    wire = requests[0].content.decode()
    for sensitive in ("synthetic-configured-value", "pilot@example.invalid", "SyntheticPilot"):
        assert sensitive not in wire
        assert sensitive not in caplog.text
    assert int(requests[0].headers["content-length"]) == len(requests[0].content)


@pytest.mark.parametrize(
    "body",
    [b"pilot@example.invalid", b'{"input": "pilot@example.invalid"', b"\xffpilot@example.invalid"],
)
async def test_unparseable_cloud_body_fails_closed_without_exposing_body(body, caplog):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200)

    async with ScrubbedModelClient(
        Settings(), local=False, transport=httpx.MockTransport(handle)
    ) as client:
        with pytest.raises(ValueError, match="cloud payload could not be scrubbed") as error:
            await client.post("https://provider.example.invalid/new-call", content=body)
    assert not requests
    assert error.value.__context__ is None
    assert "pilot@example.invalid" not in "".join(traceback.format_exception(error.value))
    assert "pilot@example.invalid" not in caplog.text


async def test_excessive_cloud_nesting_fails_closed():
    payload = "pilot@example.invalid"
    for _ in range(40):
        payload = [payload]
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200)

    async with ScrubbedModelClient(
        Settings(), local=False, transport=httpx.MockTransport(handle)
    ) as client:
        with pytest.raises(ValueError, match="cloud payload could not be scrubbed"):
            await client.post("https://provider.example.invalid/new-call", json=payload)
    assert not requests


async def test_scrubbing_cannot_expand_prompt_past_gateway_limit(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "q")
    settings = Settings(ai_gateway=GatewayConfig(max_prompt_tokens=1024))
    payload = {"input": "q" * 60}
    assert len(json.dumps(payload).encode()) + 512 < settings.ai_gateway.max_prompt_tokens
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200)

    async with ScrubbedModelClient(
        settings, local=False, transport=httpx.MockTransport(handle)
    ) as client:
        with pytest.raises(OutboundDenied, match="conservative token ceiling"):
            await client.post("https://provider.example.invalid/new-call", json=payload)
    assert not requests


@pytest.mark.parametrize("transport_failure", [False, True])
async def test_http_errors_only_retain_scrubbed_request(transport_failure, caplog):
    def handle(request):
        if transport_failure:
            raise httpx.ConnectError("synthetic transport failure", request=request)
        return httpx.Response(503)

    async with ScrubbedModelClient(
        Settings(), local=False, transport=httpx.MockTransport(handle)
    ) as client:
        with pytest.raises(httpx.HTTPError) as error:
            response = await client.post(
                "https://provider.example.invalid/new-call",
                json={"input": "pilot@example.invalid"},
            )
            response.raise_for_status()
    assert "pilot@example.invalid" not in error.value.request.content.decode()
    assert "pilot@example.invalid" not in "".join(traceback.format_exception(error.value))
    assert "pilot@example.invalid" not in caplog.text


async def test_local_client_preserves_body_bytes():
    original = b'{ "input": "pilot@example.invalid", "card": 4111111111111111 }'
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200)

    async with ScrubbedModelClient(
        Settings(), local=True, transport=httpx.MockTransport(handle)
    ) as client:
        await client.post("http://127.0.0.1:1234/new-call", content=original)
    assert len(requests) == 1
    assert requests[0].content == original
