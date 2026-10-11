"""Explicit outbound model budgets and configured pricing, with no credentials."""

import ipaddress
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from urllib.parse import urlsplit

from pydantic import Field, field_validator

from .protocol import Identifier, StrictModel


def validate_remote_endpoint(value: str) -> str:
    endpoint = urlsplit(value)
    if (
        endpoint.scheme != "https"
        or not endpoint.hostname
        or endpoint.username
        or endpoint.password
        or endpoint.query
        or endpoint.fragment
    ):
        raise ValueError("remote endpoint requires credential-free HTTPS")
    if endpoint.port is not None and not 1 <= endpoint.port <= 65535:
        raise ValueError("invalid remote endpoint port")
    return value


def validate_local_endpoint(value: str) -> str:
    endpoint = urlsplit(value)
    try:
        address = ipaddress.ip_address(endpoint.hostname or "")
    except ValueError as exc:
        raise ValueError("local endpoint requires a literal loopback IP") from exc
    if (
        not address.is_loopback
        or endpoint.scheme not in {"http", "https"}
        or endpoint.username
        or endpoint.password
        or endpoint.query
        or endpoint.fragment
    ):
        raise ValueError("local endpoint requires credential-free literal loopback HTTP")
    if endpoint.port is not None and not 1 <= endpoint.port <= 65535:
        raise ValueError("invalid local endpoint port")
    return value


class ModelPrice(StrictModel):
    # Floats are wire values only. Every monetary calculation uses Decimal(str()).
    input_usd_per_million: float = Field(ge=0, le=10000)
    output_usd_per_million: float = Field(ge=0, le=10000)

    def estimate_micro_usd(self, input_tokens: int, output_tokens: int) -> int:
        value = (
            Decimal(str(self.input_usd_per_million)) * input_tokens
            + Decimal(str(self.output_usd_per_million)) * output_tokens
        )
        return int(value.to_integral_value(rounding=ROUND_CEILING))


class DailyBudget(StrictModel):
    max_requests: int = Field(default=100, ge=0, le=100000)
    max_estimated_usd: float = Field(default=1, ge=0, le=10000)

    @property
    def micro_usd_limit(self) -> int:
        return int(
            (Decimal(str(self.max_estimated_usd)) * 1000000).to_integral_value(rounding=ROUND_FLOOR)
        )


class ChatterBudget(DailyBudget):
    max_requests_per_minute: int = Field(default=8, ge=0, le=60)


class LocalFallback(StrictModel):
    endpoint: str = Field(min_length=1, max_length=512)
    dialogue_model: Identifier
    action_model: Identifier | None = None

    _endpoint = field_validator("endpoint")(validate_local_endpoint)


class GatewayConfig(StrictModel):
    prices: dict[str, ModelPrice] = Field(default_factory=dict, max_length=64)
    foreground: DailyBudget = Field(default_factory=DailyBudget)
    background: DailyBudget = Field(
        default_factory=lambda: DailyBudget(max_requests=0, max_estimated_usd=0)
    )
    chatter: ChatterBudget = Field(
        default_factory=lambda: ChatterBudget(max_requests=0, max_estimated_usd=0)
    )
    max_prompt_tokens: int = Field(default=16384, ge=1024, le=131072)
    dialogue_output_tokens: int = Field(default=300, ge=1, le=4096)
    action_output_tokens: int = Field(default=200, ge=1, le=4096)
    skill_output_tokens: int = Field(default=1200, ge=128, le=4096)
    memory_output_tokens: int = Field(default=400, ge=64, le=2048)
    max_concurrent_requests: int = Field(default=2, ge=1, le=8)
    local_fallback: LocalFallback | None = None
    circuit_failure_limit: int = Field(default=3, ge=1, le=10)
    circuit_cooldown_seconds: float = Field(default=30, ge=1, le=600)

    @field_validator("prices")
    @classmethod
    def model_names(cls, value):
        if any(not name.strip() or len(name) > 128 for name in value):
            raise ValueError("pricing requires bounded model identifiers")
        return value
