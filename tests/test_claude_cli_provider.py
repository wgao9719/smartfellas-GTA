import json
import sys
from pathlib import Path

import pytest

from service.lls.anthropic_provider import AnthropicProvider
from service.lls.claude_cli_provider import ClaudeCliProvider, cli_environment, transcript
from service.lls.gateway import GatewayProvider
from service.lls.gateway_config import (
    HOSTED_PROVIDERS,
    KEYED_PROVIDERS,
    GatewayConfig,
    ModelPrice,
    hosted_endpoint,
)
from service.lls.outbound import OutboundDenied, OutboundGate
from service.lls.providers import SYSTEM, ProviderResponseError
from service.lls.settings import Settings
from service.lls.storage import Repository

MODEL = "claude-sonnet-5-5"
ROOT = Path(__file__).resolve().parents[1]

# Stands in for `claude -p`: records what it was given, then answers in the CLI's formats.
FAKE_CLI = r'''
import json, os, sys
argv = sys.argv[1:]
message = json.loads(sys.stdin.readline() or "null")
with open(os.environ["FAKECLI_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps({"argv": argv, "message": message,
                          "env": sorted(os.environ)}) + "\n")
mode = os.environ.get("FAKECLI_MODE", "ok")
usage = {"input_tokens": 90, "cache_read_input_tokens": 30, "output_tokens": 14}
print(json.dumps({"type": "system", "subtype": "init"}), flush=True)
if mode == "logged_out":
    print(json.dumps({"type": "result", "subtype": "success", "is_error": True,
                      "result": "Not logged in - Please run /login"}))
elif "--include-partial-messages" not in argv:
    # Structured output is delivered through a tool call, so the CLI reports tool_use.
    result = {"type": "result", "subtype": "success", "is_error": False,
              "result": '{"verdict":"yes"}', "stop_reason": "tool_use", "usage": usage}
    if "--json-schema" in argv:
        result["structured_output"] = {"verdict": "yes"}
    print(json.dumps(result))
else:
    def event(**fields):
        print(json.dumps({"type": "stream_event", "event": fields}), flush=True)
    event(type="message_start", message={"type": "message", "content": [],
                                         "usage": {"input_tokens": 120, "output_tokens": 1}})
    event(type="content_block_start", index=0, content_block={"type": "text", "text": ""})
    for part in ("Hey, it's the bread man! ", "Did you open yet?"):
        event(type="content_block_delta", index=0, delta={"type": "text_delta", "text": part})
    event(type="content_block_stop", index=0)
    event(type="message_delta", delta={"stop_reason": "end_turn"}, usage={"output_tokens": 18})
    event(type="message_stop")
    if mode != "no_result":
        print(json.dumps({"type": "result", "subtype": "success", "is_error": False,
                          "result": "Hey, it's the bread man! Did you open yet?", "usage": usage}))
'''


@pytest.fixture
async def cli_factory(tmp_path, monkeypatch):
    script = tmp_path / "fake_claude.py"
    script.write_text(FAKE_CLI, encoding="utf-8")
    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("FAKECLI_LOG", str(log))
    repositories, providers = [], []

    def create(**settings):
        repo = Repository(":memory:")
        repositories.append(repo)
        values = {"provider": "claude_cli", "dialogue_model": MODEL, "cloud_opt_in": True}
        configured = Settings(**{**values, **settings})
        configured.ai_gateway = GatewayConfig(
            prices={MODEL: ModelPrice(input_usd_per_million=0, output_usd_per_million=0)},
            foreground={"max_requests": 50, "max_estimated_usd": 0},
        )
        provider = ClaudeCliProvider(
            configured,
            gate=OutboundGate(configured, repo),
            executable=[sys.executable, str(script)],
        )
        providers.append(provider)
        return provider

    def calls():
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]

    create.calls = calls
    yield create
    for provider in providers:
        await provider.aclose()
    for repo in repositories:
        repo.close()


async def speak(provider, context=None):
    return "".join([part async for part in provider.dialogue(context or {"input": "hello"})])


CONTEXT = {
    "actor": {"display_name": "Marisol Vega"},
    "protagonist_id": "franklin",
    "recent_conversation": [
        {"memory_id": "m1", "role": "player", "text": "The player said: I'm opening a bakery."},
        {"memory_id": "m2", "role": "npc", "text": "I said: Make the conchas soft."},
    ],
    "input": "Morning, Marisol.",
}


async def test_dialogue_streams_through_a_locked_down_cli(cli_factory, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "parent-session-value")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "parent-session")
    monkeypatch.setenv("LLS_BRIDGE_TOKEN", "bridge-secret")
    provider = cli_factory()
    assert await speak(provider, CONTEXT) == "Hey, it's the bread man! Did you open yet?"
    call = cli_factory.calls()[0]
    argv = call["argv"]
    # No tools, MCP servers, hooks, thinking or saved sessions: the CLI is only a transport.
    assert argv[argv.index("--tools") + 1] == ""
    assert json.loads(argv[argv.index("--settings") + 1]) == {
        "disableAllHooks": True, "alwaysThinkingEnabled": False,
    }
    assert {"--strict-mcp-config", "--no-session-persistence", "-p"} <= set(argv)
    assert argv[argv.index("--input-format") + 1] == "stream-json"
    assert argv[argv.index("--model") + 1] == MODEL
    assert argv[argv.index("--effort") + 1] == "medium"
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    # The constant instructions are the system prompt; who speaks travels with the request.
    assert argv[argv.index("--system-prompt") + 1] == SYSTEM
    content = call["message"]["message"]["content"]
    assert content.startswith('You are "Marisol Vega"')
    assert "Franklin (the player): I'm opening a bakery." in content
    assert "You: Make the conchas soft." in content
    assert content.rstrip().endswith("Reply with your next line only.")
    assert not [key for key in call["env"] if key.upper().startswith(
        ("ANTHROPIC_", "CLAUDE_", "LLS_", "LLM_"))]
    usage = provider.gate.usage_summary()["foreground"]
    assert usage["requests"] == 1 and usage["pending_reservations"] == 0
    assert (usage["reported_input_tokens"], usage["reported_output_tokens"]) == (120, 18)
    rows = provider.gate.ledger.repository.connection.execute(
        "SELECT provider, state FROM ai_requests"
    ).fetchall()
    assert [tuple(row) for row in rows] == [("claude_cli", "settled")]


async def test_a_warm_process_waits_for_the_next_line(cli_factory):
    provider = cli_factory()
    await speak(provider, CONTEXT)
    assert len(provider.warm) == 1
    warm = next(iter(provider.warm.values()))
    assert warm.returncode is None
    await speak(provider, {**CONTEXT, "input": "See you tomorrow."})
    assert len(cli_factory.calls()) == 2
    assert warm.returncode is not None
    await provider.aclose()
    assert provider.warm == {}


async def test_grounding_uses_the_cli_schema_and_structured_output(cli_factory):
    provider = cli_factory()
    assert await provider.classify_grounding("She opened a bakery.", player_text="hi") == "yes"
    argv = cli_factory.calls()[0]["argv"]
    schema = json.loads(argv[argv.index("--json-schema") + 1])
    assert schema["properties"]["verdict"]["enum"] == ["yes", "no"]
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    assert "--include-partial-messages" not in argv
    usage = provider.gate.usage_summary()["foreground"]
    assert (usage["reported_input_tokens"], usage["reported_output_tokens"]) == (120, 14)


async def test_signed_out_cli_reports_why(cli_factory, monkeypatch):
    monkeypatch.setenv("FAKECLI_MODE", "logged_out")
    provider = cli_factory()
    with pytest.raises(ProviderResponseError, match="Not logged in"):
        await speak(provider, CONTEXT)
    with pytest.raises(ProviderResponseError, match="Not logged in"):
        await provider.classify_grounding("x", player_text="y")


async def test_stream_without_a_result_is_an_error(cli_factory, monkeypatch):
    monkeypatch.setenv("FAKECLI_MODE", "no_result")
    with pytest.raises(ProviderResponseError, match="without a result"):
        await speak(cli_factory(), CONTEXT)


async def test_credentials_are_scrubbed_before_the_cli_sees_them(cli_factory):
    await speak(cli_factory(), {"input": "my key is sk-ant-test-key-0123456789abcdef"})
    assert "sk-ant-test-key" not in json.dumps(cli_factory.calls()[0]["message"])


async def test_cloud_consent_is_required_before_anything_runs(cli_factory):
    provider = cli_factory(cloud_opt_in=False)
    with pytest.raises(OutboundDenied):
        await speak(provider, CONTEXT)
    assert cli_factory.calls() == [] and provider.warm == {}


def test_transcript_keeps_a_single_message_unchanged():
    assert transcript([{"role": "user", "content": "{}"}]) == "{}"
    with pytest.raises(ProviderResponseError):
        transcript([{"role": "assistant", "content": "hi"}])


def test_environment_drops_parent_session_and_service_secrets(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "x")
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("LLM_API_KEY", "x")
    monkeypatch.setenv("PATH", "keep")
    env = cli_environment()
    assert "PATH" in env or "Path" in env
    assert not {"ANTHROPIC_AUTH_TOKEN", "CLAUDECODE", "LLM_API_KEY"} & set(env)


def test_cli_provider_is_hosted_without_an_api_key():
    assert "claude_cli" in HOSTED_PROVIDERS and "claude_cli" not in KEYED_PROVIDERS
    settings = Settings(provider="claude_cli", dialogue_model=MODEL)
    assert hosted_endpoint(settings) == "https://api.anthropic.com/v1"
    with pytest.raises(ValueError):
        Settings(provider="claude_cli")


def test_gateway_routes_the_cli_provider(tmp_path):
    repo = Repository(":memory:")
    try:
        configured = Settings(provider="claude_cli", dialogue_model=MODEL)
        gateway = GatewayProvider(configured, repo)
        assert isinstance(gateway.primary, ClaudeCliProvider)
        assert isinstance(gateway.primary, AnthropicProvider)
    finally:
        repo.close()


def test_rig_cli_gateway_file_prices_every_model_it_offers():
    config = GatewayConfig.model_validate_json(
        (ROOT / "tools/windows-rig/claude-cli-gateway.json").read_text(encoding="utf-8")
    )
    assert {"claude-sonnet-4-5-20250929", "claude-haiku-4-5-20251001"} <= set(config.prices)
    assert config.background.max_requests == 0


def test_effort_none_from_the_environment_leaves_effort_out(monkeypatch):
    monkeypatch.setenv("LLS_PROVIDER", "claude_cli")
    monkeypatch.setenv("LLM_DIALOGUE_MODEL", "claude-sonnet-4-5")
    monkeypatch.setenv("LLS_ANTHROPIC_EFFORT", "none")
    assert Settings.from_env().anthropic_effort == ""


async def test_social_cli_uses_social_instructions_without_player_role_rewrite(cli_factory):
    from service.lls.providers import SOCIAL_SYSTEM

    provider = cli_factory()
    context = {"social": {"phase": "turn", "decision_owner": "npc-a"},
               "actor": {"display_name": "Passerby"}, "input": "Speak to the other NPC."}
    await speak(provider, context)
    call = cli_factory.calls()[0]
    assert call["argv"][call["argv"].index("--system-prompt") + 1] == SOCIAL_SYSTEM
    content = call["message"]["message"]["content"]
    assert "Franklin (the player)" not in content
    assert "npc-a" in content
