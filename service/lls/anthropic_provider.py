"""Native Claude Messages API requests behind the same consent, budget and scrub gates."""

import asyncio
import json
import os

from .action_registry import ACTION_DESCRIPTIONS
from .context import dialogue_context_bytes, dialogue_identity, dialogue_messages
from .gateway_config import validate_remote_endpoint
from .grounding import CONVERSATION_WINDOW_CHARS, last_sentence
from .outbound import OutboundGate
from .outbound_scrub import ScrubbedModelClient
from .providers import (
    ACTION_GUIDANCE,
    ACTION_REPAIR,
    GROUNDING_SYSTEM,
    SOCIAL_SYSTEM,
    SUMMARY_SYSTEM,
    SYSTEM,
    ProviderResponseError,
    bounded_bytes,
    bounded_lines,
    decode_envelope,
    offered_action_schema,
    offered_proposal,
    skill_instructions,
    skill_schema,
)

ANTHROPIC_VERSION = "2023-06-01"
COMPLETE_STOPS = frozenset({"end_turn", "stop_sequence"})
# Claude's first token arrives in about a second, not the sub-second hosted default.
GROUNDING_TIMEOUT_SECONDS = 3.0
# Thinking shares max_tokens with the answer, so a one-word verdict still needs room.
GROUNDING_OUTPUT_TOKENS = 512
GROUNDING_RESPONSE_BYTES = 65536
GROUNDING_VERDICT = {
    "type": "object",
    "additionalProperties": False,
    "required": ["verdict"],
    "properties": {"verdict": {"type": "string", "enum": ["yes", "no"]}},
}
# MemorySummary without its bounds, which parse_summary still enforces.
MEMORY_SUMMARY = {
    "type": "object",
    "additionalProperties": False,
    "required": ["text", "importance"],
    "properties": {"text": {"type": "string"}, "importance": {"type": "number"}},
}
# Structured outputs reject these keywords; local validation still enforces the bounds.
UNSUPPORTED_SCHEMA_KEYS = frozenset({
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
    "minLength", "maxLength",
})
THINKING_BLOCKS = frozenset({"thinking", "redacted_thinking"})


def api_schema(schema):
    """Copy a JSON schema without keywords the structured-output grammar refuses."""
    if isinstance(schema, list):
        return [api_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    copied = {}
    for key, value in schema.items():
        if key in UNSUPPORTED_SCHEMA_KEYS:
            continue
        if key in {"properties", "$defs"}:
            # Property names are data, never keywords to strip.
            copied[key] = {name: api_schema(item) for name, item in value.items()}
        else:
            copied[key] = api_schema(value)
    return copied


def merged_turns(messages):
    """Join adjacent same-role messages; the Messages API expects alternating turns."""
    merged = []
    for message in messages:
        if merged and merged[-1]["role"] == message["role"]:
            content = merged[-1]["content"] + "\n\n" + message["content"]
            merged[-1] = {**merged[-1], "content": content}
        else:
            merged.append(dict(message))
    return merged


def input_tokens(usage):
    """Total billed input tokens, including any cache reads and writes."""
    counts = [usage.get("input_tokens")]
    for key in ("cache_creation_input_tokens", "cache_read_input_tokens"):
        value = usage.get(key)
        counts.append(0 if value is None else value)
    if all(type(value) is int and 0 <= value <= 1000000000 for value in counts):
        return sum(counts)
    return None


def output_tokens(usage):
    value = usage.get("output_tokens")
    return value if type(value) is int and 0 <= value <= 1000000000 else None


def message_parts(envelope):
    """Return a complete response's text, stop reason and usage; thinking is never text."""
    if envelope.get("type") != "message" or not isinstance(envelope.get("content"), list):
        raise ValueError("unexpected message envelope")
    text = []
    for block in envelope["content"]:
        if block.get("type") == "text":
            if not isinstance(block.get("text"), str):
                raise TypeError("non-text content")
            text.append(block["text"])
        elif block.get("type") not in THINKING_BLOCKS:
            raise ValueError("unexpected content block")
    usage = envelope.get("usage")
    counted = None
    if isinstance(usage, dict):
        prompt, completion = input_tokens(usage), output_tokens(usage)
        counted = None if prompt is None or completion is None else (prompt, completion)
    return "".join(text), envelope.get("stop_reason"), counted


def unfenced(text):
    """Remove one surrounding Markdown code fence from a JSON-only reply."""
    stripped = text.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        body = stripped[3:-3]
        return body.split("\n", 1)[1] if "\n" in body else body
    return stripped


class MessageStream:
    """Track one Messages API event stream; only text blocks become speech."""

    def __init__(self):
        self.started = False
        self.blocks = {}
        self.input_tokens = None
        self.output_tokens = None
        self.stop_reason = None
        self.finished = False

    @property
    def usage(self):
        if self.input_tokens is None or self.output_tokens is None:
            return None
        return self.input_tokens, self.output_tokens

    def feed(self, event):
        kind = event["type"]
        if kind == "error":
            error = event.get("error")
            name = error.get("type") if isinstance(error, dict) else None
            raise ProviderResponseError(f"model stream error: {name or 'unknown'}")
        if kind == "message_start":
            if self.started:
                raise ValueError("duplicate message start")
            self.started = True
            self.observe(event["message"].get("usage"))
            return ""
        if not self.started or self.finished:
            raise ValueError("stream event outside a message")
        if kind == "content_block_start":
            block = event["content_block"]
            if block["type"] != "text" and block["type"] not in THINKING_BLOCKS:
                raise ValueError("unexpected content block")
            self.blocks[event["index"]] = block["type"]
            return block.get("text", "") if block["type"] == "text" else ""
        if kind == "content_block_delta":
            block, delta = self.blocks[event["index"]], event["delta"]
            if delta["type"] == "text_delta" and block == "text":
                if not isinstance(delta["text"], str):
                    raise TypeError("non-text delta")
                return delta["text"]
            if delta["type"] in {"thinking_delta", "signature_delta"} and block == "thinking":
                return ""
            raise ValueError("unexpected content delta")
        if kind == "message_delta":
            self.stop_reason = event["delta"].get("stop_reason")
            self.observe(event.get("usage"))
            return ""
        if kind == "message_stop":
            self.finished = True
        # Pings, block stops and event types added later carry no speech.
        return ""

    def observe(self, usage):
        # message_delta counts are cumulative, so the latest complete value wins.
        if not isinstance(usage, dict):
            return
        if "input_tokens" in usage:
            self.input_tokens = input_tokens(usage)
        if "output_tokens" in usage:
            self.output_tokens = output_tokens(usage)


class AnthropicProvider:
    """Claude through the native Messages API, never followed redirects."""

    name = "anthropic"
    local = False

    def __init__(self, settings, *, gate, transport=None):
        if not isinstance(gate, OutboundGate):
            raise ValueError("outbound providers require the durable authorization gateway")
        self.gate = gate
        self.settings = settings
        self.transport = transport
        self.endpoint = settings.anthropic_base_url.rstrip("/") + "/messages"
        validate_remote_endpoint(self.endpoint)

    def headers(self):
        key = os.environ.get("LLM_API_KEY", "")
        if not key:
            raise RuntimeError("model credentials are unavailable")
        return {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION}

    def client(self):
        return ScrubbedModelClient(self.settings, local=False, transport=self.transport)

    def context_json(self, context):
        serialized = json.dumps(context, ensure_ascii=False)
        if len(serialized.encode("utf-8")) > self.settings.context_byte_budget:
            raise ProviderResponseError("model context exceeds configured byte budget")
        return serialized

    @property
    def grounding_timeout_seconds(self):
        return min(GROUNDING_TIMEOUT_SECONDS, self.settings.model_timeout_seconds)

    @property
    def action_model(self):
        return self.settings.action_model or self.settings.dialogue_model

    def payload(self, *, model, max_tokens, system, messages, schema=None, stream=False):
        body = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": merged_turns(messages),
        }
        output = {}
        if self.settings.anthropic_effort:
            output["effort"] = self.settings.anthropic_effort
        if schema is not None:
            output["format"] = {"type": "json_schema", "schema": schema}
        if output:
            body["output_config"] = output
        if stream:
            body["stream"] = True
        return body

    def reserve(self, payload, budget_class):
        return self.gate.request(
            local=False,
            provider=self.name,
            model=payload["model"],
            prompt_bytes=len(json.dumps(payload, ensure_ascii=False).encode()),
            output_tokens=payload["max_tokens"],
            budget_class=budget_class,
        )

    async def complete(self, payload, *, budget_class, limit):
        """Send one non-streaming request and settle it against reported usage."""
        headers = self.headers()
        async with self.reserve(payload, budget_class) as receipt, self.client() as client:
            async with client.stream(
                "POST", self.endpoint, headers=headers, json=payload
            ) as response:
                response.raise_for_status()
                raw = b"".join([chunk async for chunk in bounded_bytes(response, limit)])
            try:
                text, stop_reason, usage = message_parts(decode_envelope(raw))
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                raise ProviderResponseError("invalid model response envelope") from exc
            if usage is None:
                receipt.ambiguous_usage = True
            else:
                receipt.usage = usage
            receipt.complete = True
        # Stop reasons are checked by callers after the reservation has settled.
        return text, stop_reason

    async def classify_grounding(
        self, sentence, *, player_text="", budget_class="foreground", previous_sentence="",
    ):
        payload = self.payload(
            model=self.action_model,
            max_tokens=GROUNDING_OUTPUT_TOKENS,
            system=GROUNDING_SYSTEM + ' Put that answer in the "verdict" field.',
            messages=[{"role": "user", "content": self.context_json({
                "sentence": sentence, "player_text": player_text[:CONVERSATION_WINDOW_CHARS],
                "previous_sentence": last_sentence(previous_sentence),
            })}],
            schema=GROUNDING_VERDICT,
        )
        async with asyncio.timeout(self.grounding_timeout_seconds):
            content, stop_reason = await self.complete(
                payload,
                budget_class=budget_class,
                limit=min(self.settings.model_response_bytes, GROUNDING_RESPONSE_BYTES),
            )
        if stop_reason not in COMPLETE_STOPS:
            raise ProviderResponseError("grounding classification was not completed")
        try:
            verdict = json.loads(content)["verdict"]
            if verdict not in {"yes", "no"}:
                raise ValueError("unknown verdict")
        except (ValueError, TypeError, KeyError) as exc:
            raise ProviderResponseError("invalid grounding classification response") from exc
        return verdict

    async def dialogue(self, context, *, budget_class="foreground"):
        if dialogue_context_bytes(context) > self.settings.context_byte_budget:
            raise ProviderResponseError("model context exceeds configured byte budget")
        payload = self.payload(
            model=self.settings.dialogue_model,
            max_tokens=self.gate.config.dialogue_output_tokens,
            system=SOCIAL_SYSTEM if "social" in context else dialogue_identity(context) + SYSTEM,
            messages=([{ "role": "user", "content": self.context_json(context)}]
                      if "social" in context else dialogue_messages(context)),
            stream=True,
        )
        headers = self.headers()
        stream = MessageStream()
        received = 0
        async with (
            self.reserve(payload, budget_class) as receipt,
            asyncio.timeout(self.settings.model_timeout_seconds),
        ):
            async with self.client() as client:
                async with client.stream(
                    "POST", self.endpoint, headers=headers, json=payload
                ) as response:
                    response.raise_for_status()
                    async for line in bounded_lines(response, self.settings.model_response_bytes):
                        if not line.startswith("data:"):
                            continue
                        try:
                            content = stream.feed(decode_envelope(line[5:].strip()))
                        except ProviderResponseError:
                            raise
                        except (ValueError, TypeError, AttributeError, KeyError) as exc:
                            raise ProviderResponseError("invalid model stream event") from exc
                        if stream.finished:
                            break
                        fragment = content[: self.settings.max_reply_chars - received]
                        if fragment:
                            received += len(fragment)
                            yield fragment
                        if received >= self.settings.max_reply_chars:
                            raise ProviderResponseError(
                                "model stream exceeded reply character limit"
                            )
                    else:
                        raise ProviderResponseError("model stream ended before completion marker")
            if stream.usage is None:
                receipt.ambiguous_usage = True
            else:
                receipt.usage = stream.usage
            receipt.complete = True
        if stream.stop_reason == "refusal":
            raise ProviderResponseError("model refused the request")

    async def action(self, context, utterance, eligible, *, budget_class="foreground"):
        if not eligible:
            return None
        offered = offered_action_schema(eligible)
        schema = {
            "type": "object",
            "additionalProperties": False,
            "required": ["proposal"],
            "properties": {"proposal": api_schema(offered)},
        }
        system = (
            'Select an eligible action or none. Reply with {"proposal": null} or '
            '{"proposal": P}, where P matches this JSON schema: '
            + json.dumps(offered)
            + ". "
            + ACTION_GUIDANCE
        )
        messages = [
            {
                "role": "user",
                "content": self.context_json(
                    {"input": context["input"], "reply": utterance, "eligible": eligible,
                     "actions": {action: ACTION_DESCRIPTIONS[action] for action in eligible},
                     "action_context": context.get("action_context", {})}
                ),
            },
        ]
        async with asyncio.timeout(self.settings.model_timeout_seconds):
            for attempt in range(2):
                payload = self.payload(
                    model=self.action_model,
                    max_tokens=self.gate.config.action_output_tokens,
                    system=system,
                    messages=messages,
                    schema=schema,
                )
                content, stop_reason = await self.complete(
                    payload,
                    budget_class=budget_class,
                    limit=self.settings.model_response_bytes,
                )
                if len(content) > 4000:
                    raise ProviderResponseError("invalid action response content")
                if stop_reason not in COMPLETE_STOPS:
                    # A refusal or exhausted output budget is not worth a second paid request.
                    return None
                try:
                    value = json.loads(content)
                    if not isinstance(value, dict) or set(value) != {"proposal"}:
                        raise ValueError("unexpected action envelope")
                    return offered_proposal(value["proposal"], eligible)
                except (ValueError, TypeError):
                    if attempt:
                        return None
                    if content:
                        messages.append({"role": "assistant", "content": content[:2000]})
                    messages.append({"role": "user", "content": ACTION_REPAIR})
        return None

    async def memory_summary(self, context, *, budget_class="background"):
        from .memory_consolidation import MAX_SUMMARY_RESPONSE_BYTES, parse_summary

        payload = self.payload(
            model=self.action_model,
            max_tokens=self.gate.config.memory_output_tokens,
            system=SUMMARY_SYSTEM + " text has at most 600 characters; importance is 0 to 1.",
            messages=[{"role": "user", "content": self.context_json(context)}],
            schema=MEMORY_SUMMARY,
        )
        async with asyncio.timeout(self.settings.model_timeout_seconds):
            content, stop_reason = await self.complete(
                payload, budget_class=budget_class, limit=self.settings.model_response_bytes
            )
        try:
            if stop_reason not in COMPLETE_STOPS:
                raise ProviderResponseError("memory summary was not completed")
            if len(content.encode()) > MAX_SUMMARY_RESPONSE_BYTES:
                raise ProviderResponseError("memory summary exceeds its byte limit")
            summary = parse_summary(content)
        except (ValueError, TypeError) as exc:
            raise ProviderResponseError("invalid memory summary response") from exc
        return summary.model_dump(mode="json")

    async def skill_candidate(self, context, *, budget_class="background"):
        from .skills import MAX_SOURCE_BYTES, parse_definition

        payload = self.payload(
            model=self.action_model,
            max_tokens=self.gate.config.skill_output_tokens,
            system=skill_instructions(skill_schema()) + " Reply with only the JSON object.",
            messages=[{"role": "user", "content": self.context_json(context)}],
        )
        async with asyncio.timeout(self.settings.model_timeout_seconds):
            content, stop_reason = await self.complete(
                payload, budget_class=budget_class, limit=self.settings.model_response_bytes
            )
        try:
            if stop_reason not in COMPLETE_STOPS:
                raise ProviderResponseError("skill candidate was not completed")
            source = unfenced(content)
            if len(source.encode()) > MAX_SOURCE_BYTES:
                raise ProviderResponseError("skill candidate exceeds source byte limit")
            candidate = parse_definition(source)
        except (ValueError, TypeError, KeyError, IndexError) as exc:
            raise ProviderResponseError("invalid skill candidate response") from exc
        return candidate.model_dump(mode="json")
