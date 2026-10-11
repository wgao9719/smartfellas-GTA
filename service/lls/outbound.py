"""The sole HTTP-model authorization gate: consent, reservations, and cancellation."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone

from .ai_owner import AIControlOwner
from .budgets import BudgetLedger


class OutboundDenied(ValueError):
    pass


async def joined_job(awaitable):
    """Cancellation never leaves a reservation write or cleanup running in the background."""
    job = asyncio.ensure_future(awaitable)
    try:
        return await asyncio.shield(job)
    except asyncio.CancelledError:
        while not job.done():
            try:
                await asyncio.shield(job)
            except asyncio.CancelledError:
                continue
            except Exception:
                # A joined worker failure must not replace the caller's cancellation.
                break
        if not job.cancelled():
            job.exception()
        raise


def raise_if_cancelling():
    task = asyncio.current_task()
    if task is not None and task.cancelling():
        raise asyncio.CancelledError


@dataclass
class RequestReceipt:
    usage: tuple[int, int] | None = None
    complete: bool = False
    ambiguous_usage: bool = False


class OutboundGate:
    def __init__(self, settings, repository, *, utc_now=None, owner=None):
        self.settings = settings
        self.config = settings.ai_gateway
        self.ledger = BudgetLedger(repository)
        self.utc_now = utc_now or (lambda: datetime.now(timezone.utc))
        self.owner = owner if owner is not None else AIControlOwner(repository)
        self.owns_owner = owner is None
        self.owner.assert_repository(repository)
        self.owner.gates.add(self)
        self.lock = self.owner.lock
        self.active = {}
        self.closed = False

    @property
    def cloud_consent(self):
        saved = self.ledger.settings().get("ai_cloud_consent", self.settings.cloud_opt_in)
        # Corrupt persisted consent fails closed.
        return saved is True

    @asynccontextmanager
    async def request(self, *, local, model, prompt_bytes, output_tokens, budget_class):
        task = asyncio.current_task()
        async with self.lock:
            raise_if_cancelling()
            self.owner.assert_open()
            if self.closed:
                raise OutboundDenied("AI gateway is closed")
            if not local and not self.cloud_consent:
                raise OutboundDenied("cloud consent is required")
            active = {
                task: remote for gate in self.owner.gates for task, remote in gate.active.items()
            }
            if task in active:
                raise OutboundDenied("an outbound request is already active in this task")
            if len(active) >= self.config.max_concurrent_requests:
                raise OutboundDenied("outbound AI concurrency limit reached")
            if budget_class not in {"foreground", "background", "chatter"}:
                raise OutboundDenied("unknown AI budget class")
            # UTF-8 bytes plus a protocol allowance bound common tokenizer inputs.
            # Reserve the entire configured input ceiling, not an optimistic estimate.
            if prompt_bytes + 512 > self.config.max_prompt_tokens:
                raise OutboundDenied("prompt exceeds the conservative token ceiling")
            price = None if local else self.config.prices.get(model)
            if not local and price is None:
                raise OutboundDenied("cloud model pricing is not configured")
            charge = (
                price.estimate_micro_usd(self.config.max_prompt_tokens, output_tokens)
                if price is not None
                else 0
            )
            reservation = asyncio.create_task(
                asyncio.to_thread(
                    self.ledger.reserve,
                    now=self.utc_now(),
                    budget_class=budget_class,
                    provider="local_openai" if local else "openai",
                    model=model,
                    micro_usd=charge,
                    limit=getattr(self.config, budget_class),
                    price=price,
                )
            )
            try:
                request_id = await joined_job(reservation)
            except asyncio.CancelledError:
                if not reservation.cancelled() and reservation.exception() is None:
                    try:
                        await joined_job(
                            asyncio.to_thread(self.ledger.settle, reservation.result(), None)
                        )
                    except (asyncio.CancelledError, Exception):
                        # Retain cancellation even when terminal accounting fails.
                        pass
                raise
            self.active[task] = not local
        receipt = RequestReceipt()
        success = False
        cancelled = False
        try:
            raise_if_cancelling()
            yield receipt
            success = True
        except asyncio.CancelledError:
            cancelled = True
            raise
        finally:
            try:
                await joined_job(
                    asyncio.to_thread(
                        self.ledger.settle,
                        request_id,
                        receipt.usage
                        if success and receipt.complete and not receipt.ambiguous_usage
                        else None,
                    )
                )
            except Exception:
                if not cancelled:
                    raise
            finally:
                self.active.pop(task, None)

    async def set_cloud_consent(self, enabled):
        if type(enabled) is not bool:
            raise ValueError("cloud consent must be explicit")
        async with self.lock:
            self.owner.assert_open()
            await joined_job(
                asyncio.to_thread(self.ledger.save_settings, {"ai_cloud_consent": enabled})
            )
            pending = {
                task
                for gate in self.owner.gates
                for task, remote in gate.active.items()
                if remote and not enabled and task is not asyncio.current_task()
            }
            for task in pending:
                task.cancel()
        if pending:
            await joined_job(asyncio.gather(*pending, return_exceptions=True))

    async def aclose(self):
        async with self.lock:
            self.closed = True
            pending = [task for task in self.active if task is not asyncio.current_task()]
            for task in pending:
                task.cancel()
        try:
            if pending:
                await joined_job(asyncio.gather(*pending, return_exceptions=True))
        finally:
            if self.owns_owner:
                self.owner.close()

    def usage_summary(self):
        return self.ledger.summary(self.utc_now())
