"""Operational provider selection, explicit fallback, and safe administrator status."""

import asyncio
import time
import weakref
from contextlib import aclosing

from .anthropic_provider import AnthropicProvider
from .budgets import BudgetExceeded, BudgetLedger
from .gateway_config import HOSTED_PROVIDERS, validate_local_endpoint, validate_remote_endpoint
from .outbound import OutboundDenied, OutboundGate, raise_if_cancelling
from .providers import DisabledProvider, FakeProvider, OpenAIProvider, ProviderResponseError

CONFIGURATION_FIELDS = (
    "provider",
    "cloud_opt_in",
    "dialogue_model",
    "action_model",
    "openai_base_url",
    "anthropic_base_url",
    "local_base_url",
    "ai_gateway",
    "embedding",
)
NEUTRAL_LINE = "I'm not sure what to say right now."


def validate_endpoints(settings):
    # Even inactive endpoints appear in administrator status, so validate them all.
    validate_remote_endpoint(settings.openai_base_url)
    validate_remote_endpoint(settings.anthropic_base_url)
    validate_local_endpoint(settings.local_base_url)
    return settings


def effective_provider_settings(settings, repository):
    saved = BudgetLedger(repository).settings()
    configuration = saved.get("ai_provider_configuration", {})
    if not isinstance(configuration, dict) or set(configuration) - set(CONFIGURATION_FIELDS):
        raise ValueError("invalid stored AI configuration")
    values = settings.model_dump()
    values.update(configuration)
    if "ai_cloud_consent" in saved:
        values["cloud_opt_in"] = saved["ai_cloud_consent"] is True
    return validate_endpoints(type(settings).model_validate(values))


def persist_provider_configuration(repository, settings):
    # This allowlist intentionally excludes API credentials and bootstrap tokens.
    validated = validate_endpoints(type(settings).model_validate(settings.model_dump()))
    safe = validated.model_dump(mode="json", include=set(CONFIGURATION_FIELDS))
    BudgetLedger(repository).save_settings(
        {
            "ai_provider_configuration": safe,
            "ai_cloud_consent": validated.cloud_opt_in,
        }
    )


class GatewayProvider:
    """At most one selected provider and one explicitly configured local fallback."""

    def __init__(
        self,
        settings,
        repository,
        *,
        transport=None,
        utc_now=None,
        clock=time.monotonic,
        owner=None,
    ):
        self.settings = validate_endpoints(settings)
        self.name = settings.provider
        self.gate = OutboundGate(settings, repository, utc_now=utc_now, owner=owner)
        self.clock = clock
        self.last_fallback_reason = None
        self.failures = 0
        self.circuit_until = 0
        self.turn_routes = weakref.WeakKeyDictionary()
        self.grounding_routes = weakref.WeakKeyDictionary()
        if self.name == "anthropic":
            self.primary = AnthropicProvider(settings, gate=self.gate, transport=transport)
        elif self.name in {"openai", "local_openai"}:
            self.primary = OpenAIProvider(settings, gate=self.gate, transport=transport)
        else:
            self.primary = {"disabled": DisabledProvider, "fake": FakeProvider}[self.name]()
        self.fallback = None
        configured = settings.ai_gateway.local_fallback
        if self.name in HOSTED_PROVIDERS and configured is not None:
            values = settings.model_dump()
            values.update(
                provider="local_openai",
                local_base_url=configured.endpoint,
                dialogue_model=configured.dialogue_model,
                action_model=configured.action_model or configured.dialogue_model,
            )
            fallback_settings = type(settings).model_validate(values)
            self.fallback = OpenAIProvider(fallback_settings, gate=self.gate, transport=transport)

    def configuration_summary(self):
        values = self.settings.model_dump(mode="json", include=set(CONFIGURATION_FIELDS))
        values["cloud_opt_in"] = self.gate.cloud_consent
        return values

    def usage_summary(self):
        return self.gate.usage_summary()

    def safe_status(self):
        return {
            "configuration": self.configuration_summary(),
            "cloud_consent": self.gate.cloud_consent,
            "usage": self.usage_summary(),
            "active_remote_requests": sum(self.gate.active.values()),
            "active_local_requests": sum(not remote for remote in self.gate.active.values()),
            "closed": self.gate.closed,
            "last_fallback_reason": self.last_fallback_reason,
        }

    async def set_cloud_consent(self, enabled):
        await self.gate.set_cloud_consent(enabled)
        self.settings.cloud_opt_in = enabled

    async def aclose(self):
        await self.gate.aclose()

    @property
    def classify_grounding(self):
        if self.name in {"disabled", "fake"} or not self.settings.grounding_classifier_enabled:
            return None
        try:
            task = asyncio.current_task()
        except RuntimeError:
            task = None
        # Resolve in the dialogue task before the bounded classifier runs in its own task.
        provider = (
            self.grounding_routes.get(task, self.primary) if task is not None else self.primary
        )
        return provider.classify_grounding

    def _failure(self, exc, provider):
        self.last_fallback_reason = type(exc).__name__
        if (
            provider is self.primary
            and self.name in HOSTED_PROVIDERS
            and not isinstance(exc, (OutboundDenied, BudgetExceeded))
        ):
            self.failures += 1
            if self.failures >= self.settings.ai_gateway.circuit_failure_limit:
                cooldown = self.settings.ai_gateway.circuit_cooldown_seconds
                self.circuit_until = self.clock() + cooldown

    async def dialogue(self, context, *, budget_class="foreground"):
        raise_if_cancelling()
        task = asyncio.current_task()
        self.turn_routes[task] = None
        if self.name in {"disabled", "fake"}:
            async for chunk in self.primary.dialogue(context):
                yield chunk
            raise_if_cancelling()
            self.turn_routes[task] = self.primary
            return
        candidates = [self.primary]
        if self.name in HOSTED_PROVIDERS and self.clock() < self.circuit_until:
            candidates = []
            self.last_fallback_reason = "circuit_open"
        if self.fallback is not None:
            candidates.append(self.fallback)
        for provider in candidates:
            emitted = False
            try:
                self.grounding_routes[task] = provider
                response = provider.dialogue(context, budget_class=budget_class)
                async with aclosing(response) as stream:
                    async for chunk in stream:
                        if chunk.strip():
                            emitted = True
                        yield chunk
                if not emitted:
                    raise ProviderResponseError("empty model response")
                raise_if_cancelling()
                self.turn_routes[task] = provider
                if provider is self.primary:
                    self.failures = 0
                return
            except Exception as exc:
                raise_if_cancelling()
                self._failure(exc, provider)
                if emitted:
                    # Never splice a second model's answer into a partial first answer.
                    yield " " + NEUTRAL_LINE
                    return
        yield NEUTRAL_LINE

    async def action(self, context, utterance, eligible, *, budget_class="foreground"):
        raise_if_cancelling()
        if not eligible:
            return None

        task = asyncio.current_task()
        provider = self.turn_routes.get(task, self.primary)
        if provider is None:
            return None
        if self.name in {"disabled", "fake"}:
            proposal = await provider.action(context, utterance, eligible)
            raise_if_cancelling()
            return proposal
        try:
            proposal = await provider.action(
                context, utterance, eligible, budget_class=budget_class
            )
            raise_if_cancelling()
            return proposal
        except Exception as exc:
            raise_if_cancelling()
            self._failure(exc, provider)
            # A failed action stage has no gameplay action; do not ask a second model.
            return None

    async def skill_candidate(self, context):
        """Generate code data through the background gate, with no gameplay action."""
        return await self.background("skill_candidate", context)

    async def memory_summary(self, context):
        """Paraphrase memory lines through the background gate. None means unavailable."""
        return await self.background("memory_summary", context)

    async def background(self, method, context):
        raise_if_cancelling()
        if self.name == "disabled":
            return None
        candidates = [self.primary]
        if self.name in HOSTED_PROVIDERS and self.clock() < self.circuit_until:
            candidates = []
            self.last_fallback_reason = "circuit_open"
        if self.fallback is not None:
            candidates.append(self.fallback)
        for provider in candidates:
            if asyncio.current_task().cancelling():
                raise asyncio.CancelledError
            try:
                candidate = await getattr(provider, method)(context, budget_class="background")
                if asyncio.current_task().cancelling():
                    raise asyncio.CancelledError
                if provider is self.primary:
                    self.failures = 0
                return candidate
            except Exception as exc:
                raise_if_cancelling()
                self._failure(exc, provider)
        return None
