"""Durable, content-free AI request accounting independent of player profiles."""

import json
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from .gateway_config import DailyBudget, ModelPrice


class BudgetExceeded(ValueError):
    pass


def utc_day(now: datetime) -> str:
    if now.tzinfo is None:
        raise ValueError("budget clocks require a timezone")
    return now.astimezone(timezone.utc).date().isoformat()


class BudgetLedger:
    """Reservations count immediately and survive failures, restarts, and profile erasure."""

    def __init__(self, repository):
        self.repository = repository

    def reserve(self, *, now, budget_class, provider, model, micro_usd, limit, price=None):
        if budget_class not in {"foreground", "background"}:
            raise ValueError("unknown AI budget class")
        if not isinstance(limit, DailyBudget) or type(micro_usd) is not int or micro_usd < 0:
            raise ValueError("invalid AI budget reservation")
        if any(not isinstance(x, str) or not 1 <= len(x) <= 128 for x in (provider, model)):
            raise ValueError("invalid provider or model identifier")
        day = utc_day(now)
        request_id = uuid4().hex
        repo = self.repository
        with repo._lock, repo.connection:
            # IMMEDIATE serializes independent service processes, not just this instance.
            repo.connection.execute("BEGIN IMMEDIATE")
            count, spent = repo.connection.execute(
                "SELECT count(*),coalesce(sum(charged_micro_usd),0) FROM ai_requests "
                "WHERE day=? AND budget_class=?",
                (day, budget_class),
            ).fetchone()
            if count >= limit.max_requests or spent + micro_usd > limit.micro_usd_limit:
                raise BudgetExceeded("daily AI request or configured spending budget exhausted")
            repo.connection.execute(
                "INSERT INTO ai_requests VALUES(?,?,?,?,?,?,?,'reserved',NULL,NULL,?,?)",
                (
                    request_id,
                    day,
                    budget_class,
                    provider,
                    model,
                    micro_usd,
                    micro_usd,
                    json.dumps(price.model_dump()) if price is not None else None,
                    now.astimezone(timezone.utc).isoformat(),
                ),
            )
        return request_id

    def settle(self, request_id, usage=None):
        """Only provider-reported complete usage can replace a conservative reservation.

        Configured prices remain estimates, never a claim about actual billing.
        Missing, failed, malformed, or interrupted responses retain the whole charge.
        """
        repo = self.repository
        with repo._lock, repo.connection:
            repo.connection.execute("BEGIN IMMEDIATE")
            row = repo.connection.execute(
                "SELECT * FROM ai_requests WHERE request_id=?", (request_id,)
            ).fetchone()
            if row is None:
                raise ValueError("unknown AI budget reservation")
            if row["state"] != "reserved":
                return
            valid = (
                isinstance(usage, tuple)
                and len(usage) == 2
                and all(type(v) is int and 0 <= v <= 1000000000 for v in usage)
            )
            if not valid:
                repo.connection.execute(
                    "UPDATE ai_requests SET state='uncertain' WHERE request_id=?", (request_id,)
                )
                return
            charge = row["charged_micro_usd"]
            if row["price_json"] is not None:
                price = ModelPrice.model_validate(json.loads(row["price_json"]))
                charge = price.estimate_micro_usd(*usage)
            repo.connection.execute(
                "UPDATE ai_requests SET state='settled',charged_micro_usd=?,"
                "input_tokens=?,output_tokens=? WHERE request_id=?",
                (charge, *usage, request_id),
            )

    def summary(self, now):
        day = utc_day(now)
        result = {"day_utc": day, "currency": "USD", "cost_basis": "configured_price_estimate"}
        repo = self.repository
        with repo._lock:
            for category in ("foreground", "background"):
                row = repo.connection.execute(
                    "SELECT count(*) AS requests,coalesce(sum(charged_micro_usd),0) AS charged,"
                    "coalesce(sum(state='reserved'),0) AS pending,"
                    "coalesce(sum(state='uncertain'),0) AS uncertain,"
                    "coalesce(sum(input_tokens),0) AS input_tokens,"
                    "coalesce(sum(output_tokens),0) AS output_tokens FROM ai_requests "
                    "WHERE day=? AND budget_class=?",
                    (day, category),
                ).fetchone()
                result[category] = {
                    "requests": row["requests"],
                    "estimated_micro_usd": row["charged"],
                    "estimated_usd": str(Decimal(row["charged"]) / 1000000),
                    "pending_reservations": row["pending"],
                    "uncertain_requests": row["uncertain"],
                    "reported_input_tokens": row["input_tokens"],
                    "reported_output_tokens": row["output_tokens"],
                }
        return result

    def settings(self):
        repo = self.repository
        with repo._lock:
            return {
                row["name"]: json.loads(row["value"])
                for row in repo.connection.execute("SELECT * FROM service_settings")
            }

    def save_settings(self, values):
        if set(values) - {"ai_provider_configuration", "ai_cloud_consent"}:
            raise ValueError("unknown service AI setting")
        if "ai_cloud_consent" in values and type(values["ai_cloud_consent"]) is not bool:
            raise ValueError("cloud consent must be explicit")
        repo = self.repository
        with repo._lock, repo.connection:
            repo.connection.executemany(
                "INSERT INTO service_settings VALUES(?,?) "
                "ON CONFLICT(name) DO UPDATE SET value=excluded.value",
                [(key, json.dumps(value)) for key, value in values.items()],
            )
