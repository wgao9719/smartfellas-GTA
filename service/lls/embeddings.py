"""Opt-in OpenAI-compatible embeddings through the shared authorization gateway."""

import asyncio
import hashlib
import json
import math
import os
from typing import Literal, Protocol

from pydantic import Field, model_validator

from .gateway_config import validate_local_endpoint, validate_remote_endpoint
from .outbound import OutboundDenied, OutboundGate, raise_if_cancelling
from .outbound_scrub import ScrubbedModelClient
from .protocol import StrictModel
from .providers import ProviderResponseError, bounded_bytes, decode_envelope


class EmbeddingConfig(StrictModel):
    provider: Literal["disabled", "local_openai", "openai"] = "disabled"
    model: str = Field(default="", max_length=128)
    base_url: str = Field(default="", max_length=512)
    dimensions: int | None = Field(default=None, ge=1, le=4096)
    batch_size: int = Field(default=8, ge=1, le=32)
    max_candidates: int = Field(default=512, ge=8, le=4096)
    max_index_per_query: int = Field(default=32, ge=1, le=128)

    @model_validator(mode="after")
    def validate_configuration(self):
        if self.provider != "disabled" and not self.model.strip():
            raise ValueError("embeddings require an explicit model")
        if self.base_url:
            if self.provider == "disabled":
                try:
                    validate_local_endpoint(self.base_url)
                except ValueError:
                    validate_remote_endpoint(self.base_url)
            else:
                validator = (
                    validate_local_endpoint
                    if self.provider == "local_openai"
                    else validate_remote_endpoint
                )
                validator(self.base_url)
        return self


class EmbeddingProvider(Protocol):
    model_key: str

    async def embed(
        self, texts: list[str], *, budget_class: str = "foreground", authorize=None
    ) -> list[list[float]]: ...


def normalized_vector(value, dimensions=None):
    """Reject corrupt, zero, nonfinite, excessively large, and mismatched vectors."""
    if not isinstance(value, list) or not 1 <= len(value) <= 4096:
        raise ProviderResponseError("invalid embedding dimensions")
    if dimensions is not None and len(value) != dimensions:
        raise ProviderResponseError("embedding dimensions changed")
    try:
        valid = all(type(item) in {float, int} and math.isfinite(item) for item in value)
    except OverflowError:
        valid = False
    if not valid:
        raise ProviderResponseError("invalid embedding value")
    norm = math.hypot(*value)
    if not math.isfinite(norm) or norm == 0:
        raise ProviderResponseError("invalid embedding norm")
    return [item / norm for item in value]


class OpenAIEmbeddingProvider:
    """No downloads, redirects, implicit remote fallback, or independent consent state."""

    def __init__(self, settings, *, gate, transport=None):
        if not isinstance(gate, OutboundGate):
            raise ValueError("embeddings require the durable authorization gateway")
        self.settings = settings
        self.config = settings.embedding.model_copy(deep=True)
        self.gate = gate
        self.transport = transport
        self.local = self.config.provider == "local_openai"
        base = self.config.base_url or (
            settings.local_base_url if self.local else settings.openai_base_url
        )
        self.endpoint = base.rstrip("/") + "/embeddings"
        (validate_local_endpoint if self.local else validate_remote_endpoint)(self.endpoint)
        self.model_key = hashlib.sha256(
            json.dumps(
                [
                    "embedding-v1",
                    self.config.provider,
                    self.endpoint,
                    self.config.model,
                    self.config.dimensions,
                ],
                separators=(",", ":"),
            ).encode()
        ).hexdigest()

    async def embed(self, texts, *, budget_class="foreground", authorize=None):
        raise_if_cancelling()
        if self.config.provider == "disabled":
            raise OutboundDenied("embeddings are disabled")
        if (
            not isinstance(texts, list)
            or not 1 <= len(texts) <= self.config.batch_size
            or any(not isinstance(text, str) or not text.strip() for text in texts)
        ):
            raise ProviderResponseError("invalid embedding input batch")
        payload = {"model": self.config.model, "input": texts, "encoding_format": "float"}
        if self.config.dimensions is not None:
            payload["dimensions"] = self.config.dimensions
        payload_bytes = len(json.dumps(payload, ensure_ascii=False).encode())
        if payload_bytes > self.settings.context_byte_budget:
            raise ProviderResponseError("embedding input exceeded configured byte budget")
        headers = {}
        if not self.local:
            key = os.environ.get("LLM_API_KEY", "")
            if not key:
                raise OutboundDenied("embedding credentials are unavailable")
            headers["Authorization"] = "Bearer " + key
        async with (
            self.gate.request(
                local=self.local,
                model=self.config.model,
                prompt_bytes=payload_bytes,
                output_tokens=0,
                budget_class=budget_class,
            ) as receipt,
            asyncio.timeout(self.settings.model_timeout_seconds),
            ScrubbedModelClient(
                self.settings, local=self.local, transport=self.transport
            ) as client,
        ):
            if authorize is not None and not authorize():
                raise OutboundDenied("embedding source authorization changed")
            raise_if_cancelling()
            async with client.stream(
                "POST", self.endpoint, json=payload, headers=headers
            ) as response:
                response.raise_for_status()
                body = bytearray()
                async for chunk in bounded_bytes(response, self.settings.model_response_bytes):
                    body.extend(chunk)
            try:
                envelope = decode_envelope(body)
                rows = envelope["data"]
                if not isinstance(rows, list) or len(rows) != len(texts):
                    raise ValueError("wrong embedding count")
                vectors = {}
                dimensions = self.config.dimensions
                for row in rows:
                    index = row["index"]
                    if type(index) is not int or not 0 <= index < len(texts) or index in vectors:
                        raise ValueError("invalid embedding index")
                    vector = normalized_vector(row["embedding"], dimensions)
                    dimensions = len(vector)
                    vectors[index] = vector
                usage = envelope.get("usage", {})
                tokens = usage.get("prompt_tokens")
                total = usage.get("total_tokens", tokens)
                if (
                    type(tokens) is int
                    and type(total) is int
                    and 0 <= tokens <= 1000000000
                    and total == tokens
                ):
                    receipt.usage = (tokens, 0)
                receipt.complete = True
                raise_if_cancelling()
                return [vectors[index] for index in range(len(texts))]
            except (ValueError, TypeError, AttributeError, KeyError, IndexError) as exc:
                raise ProviderResponseError("invalid embedding response") from exc
