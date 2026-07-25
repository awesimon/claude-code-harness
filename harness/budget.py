"""Durable hierarchical budget limits, reservations, and usage accounting."""

from __future__ import annotations

import math
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Mapping

from state_core import RuntimeMetadataRepository, RuntimeRecordRevisionConflict

from .context import CancellationToken

_NAMESPACE = "harness.budgets"
_MAX_EVENTS = 512


class BudgetKind(str, Enum):
    INPUT_TOKENS = "input_tokens"
    OUTPUT_TOKENS = "output_tokens"
    TOTAL_TOKENS = "total_tokens"
    COST = "cost"
    MODEL_TURNS = "model_turns"
    TOOL_CALLS = "tool_calls"
    WALL_CLOCK = "wall_clock"
    COMPACTION_TOKENS = "compaction_tokens"


class BudgetExhausted(RuntimeError):
    category = "budget_exhausted"

    def __init__(
        self,
        kind: BudgetKind,
        scope: str,
        limit: float,
        requested: float,
        used: float,
        reserved: float,
    ) -> None:
        self.kind = kind
        self.scope = scope
        self.limit = limit
        self.requested = requested
        self.used = used
        self.reserved = reserved
        super().__init__(
            f"{kind.value} budget exhausted for {scope}: "
            f"{used} used + {reserved} reserved + {requested} requested > {limit}"
        )


def _amount(value: float, *, allow_zero: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("budget values must be finite numbers")
    result = float(value)
    if not math.isfinite(result) or result < 0 or (result == 0 and not allow_zero):
        raise ValueError("budget values must be positive finite numbers")
    return result


def _kind_map(value: Mapping[str, Any] | None) -> dict[str, float]:
    if not isinstance(value, Mapping):
        return {}
    return {
        kind.value: float(value.get(kind.value, 0.0))
        for kind in BudgetKind
        if value.get(kind.value) is not None
    }


@dataclass(frozen=True)
class BudgetReservation:
    reservation_id: str
    kind: BudgetKind
    amount: float
    agent_id: str | None
    _controller: "BudgetController"

    def consume(self, actual: float | None = None) -> None:
        self._controller._settle(
            self.reservation_id,
            consume=True,
            actual=self.amount if actual is None else actual,
        )

    def release(self) -> None:
        self._controller._settle(self.reservation_id, consume=False, actual=0)


class BudgetController:
    def __init__(
        self,
        metadata_repository: RuntimeMetadataRepository,
        root_session_id: str,
        *,
        cancellation: CancellationToken | None = None,
    ) -> None:
        self._metadata = metadata_repository
        self.root_session_id = root_session_id
        self.cancellation = cancellation

    def configure(
        self,
        limits: Mapping[BudgetKind | str, float],
        *,
        agent_id: str | None = None,
    ) -> None:
        normalized = {
            BudgetKind(kind).value: _amount(value)
            for kind, value in limits.items()
        }

        def mutation(snapshot: dict[str, Any]) -> dict[str, Any]:
            state = self._state(snapshot)
            if agent_id is None:
                state["limits"]["root"].update(normalized)
            else:
                agents = state["limits"]["agents"]
                current = dict(agents.get(agent_id, {}))
                current.update(normalized)
                agents[agent_id] = current
            self._event(state, "configure", agent_id, details={"limits": normalized})
            return state

        self._mutate(mutation)

    def reserve(
        self,
        kind: BudgetKind | str,
        amount: float,
        *,
        agent_id: str | None = None,
    ) -> BudgetReservation:
        dimension = BudgetKind(kind)
        requested = _amount(amount)
        reservation_id = uuid.uuid4().hex

        def mutation(snapshot: dict[str, Any]) -> dict[str, Any]:
            state = self._state(snapshot)
            self._assert_capacity(state, dimension, requested, agent_id)
            state["reservations"][reservation_id] = {
                "kind": dimension.value,
                "amount": requested,
                "agent_id": agent_id,
            }
            self._event(
                state,
                "reserve",
                agent_id,
                dimension,
                requested,
                reservation_id=reservation_id,
            )
            return state

        try:
            self._mutate(mutation)
        except BudgetExhausted as exc:
            self._cancel_scope(exc.scope, agent_id)
            raise
        return BudgetReservation(reservation_id, dimension, requested, agent_id, self)

    def reserve_tool_call(self, *, agent_id: str | None = None) -> BudgetReservation:
        return self.reserve(BudgetKind.TOOL_CALLS, 1, agent_id=agent_id)

    def consume(
        self,
        kind: BudgetKind | str,
        amount: float,
        *,
        agent_id: str | None = None,
    ) -> None:
        reservation = self.reserve(kind, amount, agent_id=agent_id)
        reservation.consume(amount)

    def record(
        self,
        kind: BudgetKind | str,
        amount: float,
        *,
        agent_id: str | None = None,
    ) -> None:
        """Record already-incurred usage, then signal any exhausted scope."""

        dimension = BudgetKind(kind)
        actual = _amount(amount, allow_zero=True)
        exhausted: list[BudgetExhausted] = []

        def mutation(snapshot: dict[str, Any]) -> dict[str, Any]:
            state = self._state(snapshot)
            root_usage = state["usage"]["root"]
            root_usage[dimension.value] = float(
                root_usage.get(dimension.value, 0.0)
            ) + actual
            if agent_id is not None:
                child_usage = state["usage"]["agents"].setdefault(agent_id, {})
                child_usage[dimension.value] = float(
                    child_usage.get(dimension.value, 0.0)
                ) + actual
            self._event(state, "record", agent_id, dimension, actual)
            root_limit = state["limits"]["root"].get(dimension.value)
            if root_limit is not None and root_usage[dimension.value] > root_limit:
                exhausted[:] = [
                    BudgetExhausted(
                        dimension,
                        "root",
                        root_limit,
                        0,
                        root_usage[dimension.value],
                        0,
                    )
                ]
            elif agent_id is not None:
                child_limit = state["limits"]["agents"].get(agent_id, {}).get(
                    dimension.value
                )
                if child_limit is not None and child_usage[dimension.value] > child_limit:
                    exhausted[:] = [
                        BudgetExhausted(
                            dimension,
                            agent_id,
                            child_limit,
                            0,
                            child_usage[dimension.value],
                            0,
                        )
                    ]
            return state

        self._mutate(mutation)
        if exhausted:
            self._cancel_scope(exhausted[0].scope, agent_id)
            raise exhausted[0]

    def record_model_usage(
        self, usage: Mapping[str, Any], *, agent_id: str | None = None
    ) -> None:
        input_tokens = usage.get("input_tokens", usage.get("prompt_tokens", 0))
        output_tokens = usage.get("output_tokens", usage.get("completion_tokens", 0))
        total_tokens = usage.get("total_tokens")
        if total_tokens is None:
            total_tokens = (
                float(input_tokens or 0) + float(output_tokens or 0)
            )
        values = (
            (BudgetKind.INPUT_TOKENS, input_tokens),
            (BudgetKind.OUTPUT_TOKENS, output_tokens),
            (BudgetKind.TOTAL_TOKENS, total_tokens),
            (BudgetKind.COST, usage.get("cost", usage.get("cost_usd", 0))),
        )
        for kind, value in values:
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
                self.record(kind, value, agent_id=agent_id)

    def usage(self, *, agent_id: str | None = None) -> dict[BudgetKind, float]:
        state = self._state(self._snapshot())
        raw = (
            state["usage"]["root"]
            if agent_id is None
            else state["usage"]["agents"].get(agent_id, {})
        )
        return {kind: float(raw.get(kind.value, 0.0)) for kind in BudgetKind}

    def summary(self) -> dict[str, Any]:
        state = self._state(self._snapshot())
        return {
            "limits": state["limits"],
            "usage": state["usage"],
            "active_reservations": len(state["reservations"]),
            "events": list(state["events"]),
        }

    def _settle(self, reservation_id: str, *, consume: bool, actual: float) -> None:
        actual_amount = _amount(actual, allow_zero=True)

        def mutation(snapshot: dict[str, Any]) -> dict[str, Any]:
            state = self._state(snapshot)
            raw = state["reservations"].pop(reservation_id, None)
            if not isinstance(raw, Mapping):
                raise ValueError("budget reservation is no longer active")
            dimension = BudgetKind(raw["kind"])
            agent_id = raw.get("agent_id")
            if consume:
                root_usage = state["usage"]["root"]
                root_usage[dimension.value] = float(
                    root_usage.get(dimension.value, 0.0)
                ) + actual_amount
                if agent_id is not None:
                    agent_usage = state["usage"]["agents"].setdefault(agent_id, {})
                    agent_usage[dimension.value] = float(
                        agent_usage.get(dimension.value, 0.0)
                    ) + actual_amount
            self._event(
                state,
                "consume" if consume else "release",
                agent_id,
                dimension,
                actual_amount,
                reservation_id=reservation_id,
            )
            return state

        self._mutate(mutation)

    def _assert_capacity(
        self,
        state: dict[str, Any],
        kind: BudgetKind,
        requested: float,
        agent_id: str | None,
    ) -> None:
        reserved_root = sum(
            float(item["amount"])
            for item in state["reservations"].values()
            if item.get("kind") == kind.value
        )
        used_root = float(state["usage"]["root"].get(kind.value, 0.0))
        root_limit = state["limits"]["root"].get(kind.value)
        if root_limit is not None and used_root + reserved_root + requested > root_limit:
            raise BudgetExhausted(
                kind, "root", root_limit, requested, used_root, reserved_root
            )
        if agent_id is None:
            return
        reserved_agent = sum(
            float(item["amount"])
            for item in state["reservations"].values()
            if item.get("kind") == kind.value and item.get("agent_id") == agent_id
        )
        used_agent = float(
            state["usage"]["agents"].get(agent_id, {}).get(kind.value, 0.0)
        )
        child_limit = state["limits"]["agents"].get(agent_id, {}).get(kind.value)
        if child_limit is not None and used_agent + reserved_agent + requested > child_limit:
            raise BudgetExhausted(
                kind, agent_id, child_limit, requested, used_agent, reserved_agent
            )

    def _cancel_scope(self, scope: str, agent_id: str | None) -> None:
        token = self.cancellation
        if token is None:
            return
        if scope == "root":
            while token.parent is not None:
                token = token.parent
            token.cancel()
        elif agent_id is not None:
            token.cancel()

    @staticmethod
    def _state(snapshot: Mapping[str, Any]) -> dict[str, Any]:
        limits = snapshot.get("limits", {})
        usage = snapshot.get("usage", {})
        return {
            "limits": {
                "root": _kind_map(limits.get("root") if isinstance(limits, Mapping) else None),
                "agents": {
                    key: _kind_map(value)
                    for key, value in (
                        limits.get("agents", {}).items()
                        if isinstance(limits, Mapping)
                        and isinstance(limits.get("agents", {}), Mapping)
                        else ()
                    )
                },
            },
            "usage": {
                "root": _kind_map(usage.get("root") if isinstance(usage, Mapping) else None),
                "agents": {
                    key: _kind_map(value)
                    for key, value in (
                        usage.get("agents", {}).items()
                        if isinstance(usage, Mapping)
                        and isinstance(usage.get("agents", {}), Mapping)
                        else ()
                    )
                },
            },
            "reservations": {
                key: dict(value)
                for key, value in snapshot.get("reservations", {}).items()
                if isinstance(key, str) and isinstance(value, Mapping)
            }
            if isinstance(snapshot.get("reservations", {}), Mapping)
            else {},
            "events": [
                dict(item)
                for item in snapshot.get("events", [])
                if isinstance(item, Mapping)
            ][-_MAX_EVENTS:],
        }

    @staticmethod
    def _event(
        state: dict[str, Any],
        action: str,
        agent_id: str | None,
        kind: BudgetKind | None = None,
        amount: float | None = None,
        *,
        reservation_id: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        state["events"].append(
            {
                "action": action,
                "agent_id": agent_id,
                "kind": kind.value if kind is not None else None,
                "amount": amount,
                "reservation_id": reservation_id,
                "details": dict(details or {}),
                "timestamp_ms": int(time.time() * 1000),
            }
        )
        state["events"] = state["events"][-_MAX_EVENTS:]

    def _snapshot(self) -> dict[str, Any]:
        record = self._metadata.get(self.root_session_id, _NAMESPACE)
        return dict(record.snapshot) if record is not None else {}

    def _mutate(
        self, mutation: Callable[[dict[str, Any]], dict[str, Any]]
    ) -> None:
        for _ in range(32):
            current = self._metadata.get(self.root_session_id, _NAMESPACE)
            snapshot = dict(current.snapshot) if current is not None else {}
            expected = current.revision if current is not None else None
            try:
                self._metadata.put(
                    self.root_session_id, _NAMESPACE, mutation(snapshot), expected
                )
                return
            except RuntimeRecordRevisionConflict:
                continue
        raise RuntimeError("budget state update conflicted repeatedly")
