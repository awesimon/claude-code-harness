"""Durable, API-neutral permission requests and explicit approved execution claims."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import math
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from state_core import (
    ApprovedToolExecutionRecord,
    ApprovedToolExecutionStatus,
    PermissionRequestRecord,
    PermissionRequestStatus,
    RuntimeRecordRevisionConflict,
)


class ApprovalError(RuntimeError):
    pass


class ApprovalConflict(ApprovalError):
    pass


class ApprovalAlreadyClaimed(ApprovalConflict):
    pass


class ApprovalBindingChanged(ApprovalConflict):
    pass


def canonical_input_digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class ApprovalService:
    """Own permission request CAS transitions and unique post-approval dispatch."""

    def __init__(
        self,
        store: Any,
        *,
        root_session_id: str,
        rule_service: Any = None,
        poll_interval: float = 0.05,
    ) -> None:
        if not math.isfinite(poll_interval) or poll_interval <= 0:
            raise ValueError("poll_interval must be a positive finite number")
        self.store = store
        self._requests = store.permission_requests
        self._executions = store.approved_tool_executions
        self._root_session_id = root_session_id
        self._rule_service = rule_service
        self._poll_interval = poll_interval
        self._waiters: dict[str, set[asyncio.Future[PermissionRequestRecord]]] = {}
        self._waiter_lock = threading.RLock()

    def create(
        self,
        *,
        agent_id: str,
        tool_call_id: str,
        tool_name: str,
        original_input: Mapping[str, Any],
        effective_input: Mapping[str, Any],
        reason: str,
        permission_mode: str,
        policy_revision: int,
        suggestions: tuple[str, ...] | list[str] = (),
        deadline: datetime | None = None,
        idempotency_key: str | None = None,
    ) -> PermissionRequestRecord:
        canonical_name = tool_name.strip()
        if not canonical_name:
            raise ValueError("tool_name must be canonical and non-empty")
        digest = canonical_input_digest(effective_input)
        key = idempotency_key or (
            f"approval:{self._root_session_id}:{agent_id}:{tool_call_id}:"
            f"{canonical_name}:{digest}:{policy_revision}"
        )
        return self._requests.create(
            PermissionRequestRecord(
                request_id=f"approval_{uuid.uuid4().hex}",
                root_session_id=self._root_session_id,
                agent_id=agent_id,
                tool_call_id=tool_call_id,
                tool_name=canonical_name,
                original_input=dict(original_input),
                effective_input=dict(effective_input),
                input_digest=digest,
                reason=reason,
                permission_mode=permission_mode,
                policy_revision=policy_revision,
                idempotency_key=key,
                suggestions=tuple(suggestions),
                deadline_at=deadline,
            )
        )

    def get(self, request_id: str) -> PermissionRequestRecord:
        record = self._requests.get(request_id)
        if record is None or record.root_session_id != self._root_session_id:
            raise KeyError(request_id)
        return record

    def list(
        self, *, status: PermissionRequestStatus | str | None = None
    ) -> tuple[PermissionRequestRecord, ...]:
        normalized = PermissionRequestStatus(status) if status is not None else None
        return tuple(self._requests.list(self._root_session_id, status=normalized))

    async def await_request(
        self,
        request_id: str,
        *,
        timeout: float | None = None,
        cancellation: Any = None,
    ) -> PermissionRequestRecord:
        loop = asyncio.get_running_loop()
        timeout_at = loop.time() + timeout if timeout is not None else None
        future: asyncio.Future[PermissionRequestRecord] = loop.create_future()
        with self._waiter_lock:
            current = self.get(request_id)
            if current.status is not PermissionRequestStatus.PENDING:
                return current
            self._waiters.setdefault(request_id, set()).add(future)
            current = self.get(request_id)
            if current.status is not PermissionRequestStatus.PENDING:
                future.set_result(current)
        try:
            while True:
                current = self.get(request_id)
                if current.status is not PermissionRequestStatus.PENDING:
                    return current
                now = datetime.now(timezone.utc)
                deadline_remaining = (
                    (current.deadline_at - now).total_seconds()
                    if current.deadline_at is not None
                    else None
                )
                timeout_remaining = timeout_at - loop.time() if timeout_at is not None else None
                if deadline_remaining is not None and deadline_remaining <= 0:
                    return self._timeout_request(current, "approval deadline expired")
                if timeout_remaining is not None and timeout_remaining <= 0:
                    return self._timeout_request(current, "approval wait timed out")
                delay = min(
                    (
                        self._poll_interval,
                        *(
                            value
                            for value in (deadline_remaining, timeout_remaining)
                            if value is not None
                        ),
                    ),
                )
                sleep_task = asyncio.create_task(asyncio.sleep(delay))
                cancel_task = (
                    asyncio.create_task(cancellation.wait()) if cancellation is not None else None
                )
                watched = {future, sleep_task}
                if cancel_task is not None:
                    watched.add(cancel_task)
                try:
                    done, _ = await asyncio.wait(watched, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    sleep_task.cancel()
                    if cancel_task is not None:
                        cancel_task.cancel()
                    await asyncio.gather(
                        sleep_task,
                        *(tuple([cancel_task]) if cancel_task is not None else ()),
                        return_exceptions=True,
                    )
                if cancel_task is not None and cancel_task in done:
                    raise asyncio.CancelledError("approval wait cancelled")
                if future in done:
                    return future.result()
        finally:
            with self._waiter_lock:
                waiters = self._waiters.get(request_id)
                if waiters is not None:
                    waiters.discard(future)
                    if not waiters:
                        self._waiters.pop(request_id, None)

    def _timeout_request(
        self, current: PermissionRequestRecord, reason: str
    ) -> PermissionRequestRecord:
        try:
            return self._transition(
                current,
                PermissionRequestStatus.TIMED_OUT,
                decision_reason=reason,
            )
        except ApprovalConflict:
            return self.get(current.request_id)

    def resolve(
        self,
        request_id: str,
        decision: str,
        expected_revision: int,
        *,
        actor: str,
        reason: str | None = None,
        updated_input: Mapping[str, Any] | None = None,
        permission_updates: tuple[Mapping[str, Any], ...] | list[Mapping[str, Any]] = (),
    ) -> PermissionRequestRecord:
        status = {
            "approve": PermissionRequestStatus.APPROVED,
            "approved": PermissionRequestStatus.APPROVED,
            "allow": PermissionRequestStatus.APPROVED,
            "deny": PermissionRequestStatus.DENIED,
            "denied": PermissionRequestStatus.DENIED,
        }.get(decision)
        if status is None:
            raise ValueError("decision must be approve or deny")
        current = self.get(request_id)
        now = datetime.now(timezone.utc)
        if (
            current.status is PermissionRequestStatus.PENDING
            and current.deadline_at is not None
            and current.deadline_at <= now
        ):
            try:
                self._transition(
                    current,
                    PermissionRequestStatus.TIMED_OUT,
                    decision_reason="approval deadline expired",
                )
            finally:
                raise ApprovalConflict("permission request deadline has expired")
        updates = tuple(dict(item) for item in permission_updates)
        if updates and status is not PermissionRequestStatus.APPROVED:
            raise ValueError("permission updates may only accompany approval")
        normalized = self._permission_rules().validate_updates(updates) if updates else ()
        if normalized and self._rule_service is not None:
            from .permissions import PermissionRuleService

            if not isinstance(self._rule_service, PermissionRuleService):
                self._rule_service.apply_updates(normalized)
        current = self.get(request_id)
        if current.status is not PermissionRequestStatus.PENDING:
            try:
                resolved = self._requests.transition(
                    request_id,
                    status,
                    expected_revision,
                    actor=actor,
                    decision_reason=reason,
                    updated_input=dict(updated_input) if updated_input is not None else None,
                    permission_updates=normalized,
                )
            except RuntimeRecordRevisionConflict as exc:
                raise ApprovalConflict(str(exc)) from exc
            self._publish(resolved)
            return resolved
        try:
            resolved = self.store.approval_transactions.resolve(
                request_id,
                status,
                expected_revision,
                actor=actor,
                decision_reason=reason,
                updated_input=updated_input,
                permission_updates=normalized,
            )
        except RuntimeRecordRevisionConflict as exc:
            raise ApprovalConflict(str(exc)) from exc
        self._publish(resolved)
        return resolved

    def cancel(
        self, request_id: str, expected_revision: int, *, reason: str
    ) -> PermissionRequestRecord:
        return self._terminal(
            request_id,
            expected_revision,
            PermissionRequestStatus.CANCELLED,
            interruption_reason=reason,
        )

    def interrupt(
        self, request_id: str, expected_revision: int, *, reason: str
    ) -> PermissionRequestRecord:
        return self._terminal(
            request_id,
            expected_revision,
            PermissionRequestStatus.INTERRUPTED,
            interruption_reason=reason,
        )

    def supersede(
        self, request_id: str, expected_revision: int, *, reason: str
    ) -> PermissionRequestRecord:
        return self._terminal(
            request_id,
            expected_revision,
            PermissionRequestStatus.SUPERSEDED,
            interruption_reason=reason,
        )

    def reconcile(
        self,
        *,
        now: datetime | None = None,
        live_owner_tokens: set[str] | None = None,
        observer: bool = False,
        binding_resolver: (
            Callable[[PermissionRequestRecord], tuple[str, Mapping[str, Any], int] | None] | None
        ) = None,
    ) -> dict[str, tuple[Any, ...]]:
        cutoff = now or datetime.now(timezone.utc)
        expired = tuple(self._requests.expire_due(self._root_session_id, cutoff))
        invalid_bindings: list[PermissionRequestRecord] = []
        if binding_resolver is not None:
            for request in self.list(status=PermissionRequestStatus.PENDING):
                binding = binding_resolver(request)
                try:
                    if binding is None:
                        invalid_bindings.append(
                            self.interrupt(
                                request.request_id,
                                request.revision,
                                reason="canonical tool binding is unavailable",
                            )
                        )
                        continue
                    tool_name, effective_input, policy_revision = binding
                    if (
                        tool_name != request.tool_name
                        or canonical_input_digest(effective_input) != request.input_digest
                        or policy_revision != request.policy_revision
                    ):
                        invalid_bindings.append(
                            self.supersede(
                                request.request_id,
                                request.revision,
                                reason="canonical tool binding changed",
                            )
                        )
                except ApprovalConflict:
                    continue
        interrupted = tuple(
            self._executions.interrupt_open(
                self._root_session_id,
                live_owner_tokens=live_owner_tokens or set(),
                now=cutoff,
                observer=observer,
            )
        )
        for record in expired:
            self._publish(record)
        return {
            "expired": expired,
            "invalid_bindings": tuple(invalid_bindings),
            "interrupted_executions": interrupted,
        }

    async def resume_approved_tool(
        self,
        request_id: str,
        expected_revision: int,
        *,
        claim_owner: str,
        executor: Callable[[str, Mapping[str, Any], str], Any],
        current_tool_name: str | None,
        current_effective_input: Mapping[str, Any] | None,
        current_policy_revision: int | None,
        current_tool_call_id: str | None,
    ) -> ApprovedToolExecutionRecord:
        request = self.get(request_id)
        if request.status is not PermissionRequestStatus.APPROVED:
            raise ApprovalConflict("only approved requests can be resumed")
        if request.revision != expected_revision:
            raise ApprovalConflict(
                "permission request revision changed from "
                f"{expected_revision} to {request.revision}"
            )
        execution = ApprovedToolExecutionRecord(
            execution_id=f"approved_exec_{uuid.uuid4().hex}",
            request_id=request.request_id,
            root_session_id=self._root_session_id,
            request_revision=request.revision,
            policy_revision=request.policy_revision,
            claim_owner=claim_owner,
            tool_call_id=request.tool_call_id,
            idempotency_key=f"approved-execution:{request.request_id}",
        )
        try:
            claimed = self._executions.create(execution)
        except RuntimeRecordRevisionConflict as exc:
            raise ApprovalAlreadyClaimed(
                f"approved request {request_id!r} already has an execution claim"
            ) from exc
        effective = dict(request.updated_input or request.effective_input)
        binding_changed = (
            current_tool_name is None
            or current_effective_input is None
            or current_policy_revision is None
            or current_tool_call_id is None
            or current_tool_name != request.tool_name
            or canonical_input_digest(current_effective_input) != canonical_input_digest(effective)
            or current_policy_revision != request.policy_revision
            or current_tool_call_id != request.tool_call_id
        )
        if binding_changed:
            self._executions.transition(
                claimed.execution_id,
                ApprovedToolExecutionStatus.INTERRUPTED,
                claimed.revision,
                error={"reason": "approved tool binding changed"},
            )
            raise ApprovalBindingChanged("approved tool binding changed before dispatch")
        running = self._executions.transition(
            claimed.execution_id,
            ApprovedToolExecutionStatus.RUNNING,
            claimed.revision,
        )
        try:
            result = executor(request.tool_name, effective, request.tool_call_id)
            if inspect.isawaitable(result):
                result = await result
            reference = hashlib.sha256(
                json.dumps(result, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
            ).hexdigest()
            return self._executions.transition(
                running.execution_id,
                ApprovedToolExecutionStatus.SUCCEEDED,
                running.revision,
                result_reference=reference,
            )
        except asyncio.CancelledError:
            self._executions.transition(
                running.execution_id,
                ApprovedToolExecutionStatus.CANCELLED,
                running.revision,
                error={"reason": "approved tool execution cancelled"},
            )
            raise
        except BaseException as exc:
            self._executions.transition(
                running.execution_id,
                ApprovedToolExecutionStatus.FAILED,
                running.revision,
                error={"type": type(exc).__name__, "message": str(exc)},
            )
            raise

    def _permission_rules(self) -> Any:
        if self._rule_service is None:
            from .permissions import PermissionRuleService

            self._rule_service = PermissionRuleService(
                self.store.permission_rules, root_session_id=self._root_session_id
            )
        return self._rule_service

    def _terminal(
        self,
        request_id: str,
        expected_revision: int,
        status: PermissionRequestStatus,
        **changes: Any,
    ) -> PermissionRequestRecord:
        self.get(request_id)
        try:
            result = self._requests.transition(request_id, status, expected_revision, **changes)
        except RuntimeRecordRevisionConflict as exc:
            raise ApprovalConflict(str(exc)) from exc
        self._publish(result)
        return result

    def _transition(
        self,
        current: PermissionRequestRecord,
        status: PermissionRequestStatus,
        **changes: Any,
    ) -> PermissionRequestRecord:
        try:
            result = self._requests.transition(
                current.request_id, status, current.revision, **changes
            )
        except RuntimeRecordRevisionConflict as exc:
            raise ApprovalConflict(str(exc)) from exc
        self._publish(result)
        return result

    def _publish(self, record: PermissionRequestRecord) -> None:
        with self._waiter_lock:
            for future in tuple(self._waiters.get(record.request_id, ())):
                if not future.done():
                    future.set_result(record)
