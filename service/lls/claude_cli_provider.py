"""Claude through the signed-in Claude Code CLI instead of an API key.

Development convenience for people who use Claude Code on the service computer: each
request runs ``claude -p`` once, with every tool, MCP server, setting file, hook and
saved session turned off, so the CLI is only a transport to the model. Prompts, parsing,
consent, budgets and scrubbing are the same as the Messages API provider. Requests still
leave this computer, so cloud consent applies exactly as for ``anthropic``.

The CLI takes about a second to start, so one process per prompt shape is started ahead
of time and waits on stdin for its single request.
"""

import asyncio
import json
import os
import shutil
import tempfile
from contextlib import suppress
from pathlib import Path

from .anthropic_provider import GROUNDING_VERDICT, AnthropicProvider, MessageStream, merged_turns
from .context import dialogue_context_bytes, dialogue_identity, dialogue_messages
from .outbound import OutboundDenied
from .outbound_scrub import OutboundScrubber
from .providers import SYSTEM, ProviderResponseError

# Variables a parent Claude Code session or this service sets; the CLI must use its own login.
SCRUBBED_PREFIXES = ("ANTHROPIC_", "CLAUDE_", "CLAUDECODE", "LLM_", "LLS_")
CLI_STDERR_BYTES = 4096
WARM_PROCESS_LIMIT = 4
CLI_GROUNDING_TIMEOUT_SECONDS = 8.0
CLI_SETTINGS = json.dumps({"disableAllHooks": True, "alwaysThinkingEnabled": False})


def cli_executable(configured=""):
    """The configured CLI, else ``claude`` on PATH, else the default per-user install."""
    candidates = [configured] if configured else [
        shutil.which("claude") or "",
        str(Path.home() / ".local" / "bin" / ("claude.exe" if os.name == "nt" else "claude")),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise RuntimeError("the Claude Code CLI was not found; set LLS_CLAUDE_CLI")


def cli_environment():
    return {
        key: value for key, value in os.environ.items()
        if not key.upper().startswith(SCRUBBED_PREFIXES)
    }


def transcript(messages):
    """One user message for the CLI: the context, then the conversation as labelled lines."""
    turns = merged_turns(messages)
    if not turns or turns[0]["role"] != "user":
        raise ProviderResponseError("model messages must start with the user")
    if len(turns) == 1:
        return turns[0]["content"]
    lines = [turns[0]["content"], "", "Conversation so far:"]
    for turn in turns[1:]:
        lines.append(turn["content"] if turn["role"] == "user" else "You: " + turn["content"])
    lines += ["", "Reply with your next line only."]
    return "\n".join(lines)


class ClaudeCliProvider(AnthropicProvider):
    name = "claude_cli"
    local = False

    def __init__(self, settings, *, gate, transport=None, executable=None):
        super().__init__(settings, gate=gate, transport=transport)
        self.configured = executable
        self.workdir = None
        self.warm = {}

    @property
    def command(self):
        # Resolved on first use, so selecting the provider never requires the CLI to exist yet.
        if self.configured is None:
            self.configured = cli_executable(self.settings.claude_cli_executable)
        return [self.configured] if isinstance(self.configured, str) else list(self.configured)

    @property
    def grounding_timeout_seconds(self):
        # Even a warm CLI adds about a second before the model starts; the API's 3 s would
        # withhold most checked sentences.
        return min(CLI_GROUNDING_TIMEOUT_SECONDS, self.settings.model_timeout_seconds)

    def headers(self):
        # The CLI authenticates with its own sign-in; no API key is read or sent.
        return {}

    def scrubbed(self, payload):
        # The API provider scrubs inside its HTTP client; the CLI gets the same treatment.
        try:
            clean = OutboundScrubber(self.settings).scrub(payload)
        except (TypeError, ValueError, RecursionError) as exc:
            raise ValueError("cloud payload could not be scrubbed") from exc
        size = len(json.dumps(clean, ensure_ascii=False).encode())
        if size + 512 > self.settings.ai_gateway.max_prompt_tokens:
            raise OutboundDenied("scrubbed prompt exceeds the conservative token ceiling")
        return clean

    def argv(self, *, model, system, schema=None, stream=False):
        # User settings stay loaded because they can hold the CLI's sign-in route; hooks and
        # always-on thinking are switched off, and the empty working folder has no project.
        argv = [
            *self.command, "-p", "--model", model,
            "--tools", "", "--strict-mcp-config", "--no-session-persistence",
            "--settings", CLI_SETTINGS,
            "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
            "--system-prompt", system,
        ]
        if self.settings.anthropic_effort:
            argv += ["--effort", self.settings.anthropic_effort]
        if schema is not None:
            argv += ["--json-schema", json.dumps(schema, separators=(",", ":"))]
        if stream:
            argv.append("--include-partial-messages")
        return argv

    async def spawn(self, argv):
        if self.workdir is None:
            # An empty directory keeps project instructions and files out of the CLI's view.
            self.workdir = tempfile.mkdtemp(prefix="lls-claude-cli-")
        return await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.workdir,
            env=cli_environment(),
            limit=max(self.settings.model_response_bytes, 65536) * 4,
        )

    async def process(self, argv, *, keep_warm):
        """A started CLI waiting on stdin; a warm one when this prompt shape has one."""
        key = tuple(argv)
        process = self.warm.pop(key, None)
        if process is not None and process.returncode is not None:
            process = None
        if process is None:
            process = await self.spawn(argv)
        if keep_warm and len(self.warm) < WARM_PROCESS_LIMIT:
            with suppress(OSError, RuntimeError):
                self.warm[key] = await self.spawn(argv)
        return process

    async def ask(self, process, prompt):
        message = {"type": "user", "message": {"role": "user", "content": prompt}}
        process.stdin.write((json.dumps(message, ensure_ascii=False) + "\n").encode())
        await process.stdin.drain()
        process.stdin.close()

    @staticmethod
    async def stop(process):
        if process.returncode is None:
            with suppress(ProcessLookupError):
                process.kill()
        with suppress(Exception):
            await process.wait()

    async def failure(self, process, fallback):
        detail = b""
        with suppress(Exception):
            detail = await asyncio.wait_for(process.stderr.read(CLI_STDERR_BYTES), 1)
        # CLI errors name the cause (for example "Not logged in"); prompts never reach stderr.
        text = detail.decode("utf-8", "replace").strip().splitlines()
        return ProviderResponseError(fallback + (": " + text[-1][:200] if text else ""))

    @staticmethod
    def result_parts(result):
        if not isinstance(result, dict) or result.get("type") != "result":
            raise ValueError("unexpected CLI result")
        if result.get("is_error"):
            raise ProviderResponseError(
                "Claude CLI request failed: " + str(result.get("result") or "unknown")[:200]
            )
        stop = result.get("stop_reason")
        if "structured_output" in result:
            # Structured output arrives through a tool call; the answer itself is complete.
            text = json.dumps(result["structured_output"], ensure_ascii=False)
            stop = "end_turn"
        else:
            text = result.get("result")
            if not isinstance(text, str):
                raise TypeError("non-text CLI result")
        usage = result.get("usage")
        counted = None
        if isinstance(usage, dict) and isinstance(usage.get("output_tokens"), int):
            prompt = sum(
                usage.get(name) or 0 for name in (
                    "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens",
                )
            )
            counted = (prompt, usage["output_tokens"])
        if stop is None and result.get("subtype") == "success":
            stop = "end_turn"
        return text, stop, counted

    async def result_line(self, process, limit, on_event=None):
        """Read stream-json lines up to the final result, passing stream events along."""
        read = 0
        while True:
            line = await process.stdout.readline()
            if not line:
                raise await self.failure(process, "Claude CLI ended without a result")
            read += len(line)
            if read > limit:
                raise ProviderResponseError("model response exceeded byte limit")
            try:
                item = json.loads(line)
            except ValueError as exc:
                raise ProviderResponseError("invalid Claude CLI output line") from exc
            if item.get("type") == "result":
                return item
            if on_event is not None and item.get("type") == "stream_event":
                on_event(item["event"])

    async def complete(self, payload, *, budget_class, limit):
        schema = payload.get("output_config", {}).get("format", {}).get("schema")
        async with self.reserve(payload, budget_class) as receipt:
            clean = self.scrubbed(payload)
            argv = self.argv(model=payload["model"], system=clean["system"], schema=schema)
            process = await self.process(argv, keep_warm=schema is GROUNDING_VERDICT)
            try:
                await self.ask(process, transcript(clean["messages"]))
                result = await self.result_line(process, limit)
            finally:
                await self.stop(process)
            try:
                text, stop_reason, usage = self.result_parts(result)
            except ProviderResponseError:
                raise
            except (ValueError, TypeError, KeyError) as exc:
                raise ProviderResponseError("invalid Claude CLI result") from exc
            if usage is None:
                receipt.ambiguous_usage = True
            else:
                receipt.usage = usage
            receipt.complete = True
        return text, stop_reason

    async def dialogue(self, context, *, budget_class="foreground"):
        if dialogue_context_bytes(context) > self.settings.context_byte_budget:
            raise ProviderResponseError("model context exceeds configured byte budget")
        # The constant instructions stay in the system prompt so a warm process can wait
        # for any character; who is speaking travels with the request.
        messages = dialogue_messages(context)
        identity = dialogue_identity(context)
        messages[0] = {**messages[0], "content": identity + "\n\n" + messages[0]["content"]}
        payload = self.payload(
            model=self.settings.dialogue_model,
            max_tokens=self.gate.config.dialogue_output_tokens,
            system=SYSTEM,
            messages=messages,
            stream=True,
        )
        stream = MessageStream()
        received = 0
        async with (
            self.reserve(payload, budget_class) as receipt,
            asyncio.timeout(self.settings.model_timeout_seconds),
        ):
            clean = self.scrubbed(payload)
            argv = self.argv(model=payload["model"], system=clean["system"], stream=True)
            process = await self.process(argv, keep_warm=True)
            try:
                await self.ask(process, transcript(clean["messages"]))
                read = 0
                result = None
                while True:
                    line = await process.stdout.readline()
                    if not line:
                        break
                    read += len(line)
                    if read > self.settings.model_response_bytes:
                        raise ProviderResponseError("model stream exceeded byte limit")
                    try:
                        item = json.loads(line)
                    except ValueError as exc:
                        raise ProviderResponseError("invalid Claude CLI stream line") from exc
                    if item.get("type") == "result":
                        result = item
                        break
                    if item.get("type") != "stream_event" or stream.finished:
                        continue
                    try:
                        content = stream.feed(item["event"])
                    except ProviderResponseError:
                        raise
                    except (ValueError, TypeError, AttributeError, KeyError) as exc:
                        raise ProviderResponseError("invalid model stream event") from exc
                    fragment = content[: self.settings.max_reply_chars - received]
                    if fragment:
                        received += len(fragment)
                        yield fragment
                    if received >= self.settings.max_reply_chars:
                        raise ProviderResponseError("model stream exceeded reply character limit")
                if result is None:
                    raise await self.failure(process, "Claude CLI stream ended without a result")
                if result.get("is_error"):
                    self.result_parts(result)
                if not stream.finished:
                    raise ProviderResponseError("model stream ended before completion marker")
            finally:
                await self.stop(process)
            if stream.usage is None:
                receipt.ambiguous_usage = True
            else:
                receipt.usage = stream.usage
            receipt.complete = True
        if stream.stop_reason == "refusal":
            raise ProviderResponseError("model refused the request")

    async def aclose(self):
        warm, self.warm = self.warm, {}
        for process in warm.values():
            await self.stop(process)
        if self.workdir is not None:
            shutil.rmtree(self.workdir, ignore_errors=True)
            self.workdir = None

