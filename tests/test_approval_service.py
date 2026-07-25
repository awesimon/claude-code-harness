from __future__ import annotations

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from state_core import ApprovedToolExecutionStatus, PermissionRequestStatus, SessionState
from state_core.sqlalchemy_store import Base, SQLAlchemyStateStore


def _store(path: Path) -> SQLAlchemyStateStore:
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return SQLAlchemyStateStore(sessionmaker(bind=engine, expire_on_commit=False))


def _create(service, *, tool_call_id="call-1", deadline=None):
    return service.create(
        agent_id="agent",
        tool_call_id=tool_call_id,
        tool_name="Bash",
        original_input={"command": "echo ok"},
        effective_input={"command": "echo ok"},
        reason="confirmation",
        permission_mode="default",
        policy_revision=3,
        deadline=deadline,
    )


def test_hook_and_approval_domain_services_are_public_harness_contracts() -> None:
    from harness import (
        Allow,
        ApprovalRequired,
        ApprovalService,
        Deny,
        HookDispatcher,
        LifecycleDispatcher,
        PermissionRuleService,
    )

    assert all(
        value is not None
        for value in (
            Allow,
            ApprovalRequired,
            ApprovalService,
            Deny,
            HookDispatcher,
            LifecycleDispatcher,
            PermissionRuleService,
        )
    )


@pytest.mark.asyncio
async def test_pending_request_survives_service_restart_and_resolves_without_waiter(
    tmp_path: Path,
) -> None:
    from harness.approvals import ApprovalService

    db = tmp_path / "approval.db"
    first = ApprovalService(_store(db), root_session_id="root")
    request = _create(first)

    resumed = ApprovalService(_store(db), root_session_id="root")
    approved = resumed.resolve(request.request_id, "approve", request.revision, actor="user")

    assert approved.status is PermissionRequestStatus.APPROVED
    assert resumed.list(status=PermissionRequestStatus.APPROVED)[0].request_id == request.request_id


@pytest.mark.asyncio
async def test_await_resolve_is_idempotent_and_conflicts_are_revision_checked(
    tmp_path: Path,
) -> None:
    from harness.approvals import ApprovalConflict, ApprovalService

    service = ApprovalService(_store(tmp_path / "approval.db"), root_session_id="root")
    request = _create(service)
    waiter = asyncio.create_task(service.await_request(request.request_id))
    await asyncio.sleep(0)
    denied = service.resolve(
        request.request_id, "deny", request.revision, actor="user", reason="no"
    )

    assert await waiter == denied
    assert (
        service.resolve(request.request_id, "deny", request.revision, actor="user", reason="no")
        == denied
    )
    with pytest.raises(ApprovalConflict):
        service.resolve(request.request_id, "approve", request.revision, actor="other")


@pytest.mark.asyncio
async def test_timeout_cancel_interrupted_superseded_and_late_resolution(tmp_path: Path) -> None:
    from harness.approvals import ApprovalConflict, ApprovalService

    service = ApprovalService(_store(tmp_path / "approval.db"), root_session_id="root")
    expired = _create(
        service, tool_call_id="expired", deadline=datetime.now(timezone.utc) - timedelta(seconds=1)
    )
    cancelled = _create(service, tool_call_id="cancelled")
    interrupted = _create(service, tool_call_id="interrupted")
    superseded = _create(service, tool_call_id="superseded")

    service.reconcile()
    service.cancel(cancelled.request_id, cancelled.revision, reason="caller cancelled")
    service.interrupt(interrupted.request_id, interrupted.revision, reason="owner lost")
    service.supersede(superseded.request_id, superseded.revision, reason="input changed")

    assert service.get(expired.request_id).status is PermissionRequestStatus.TIMED_OUT
    assert service.get(cancelled.request_id).status is PermissionRequestStatus.CANCELLED
    assert service.get(interrupted.request_id).status is PermissionRequestStatus.INTERRUPTED
    assert service.get(superseded.request_id).status is PermissionRequestStatus.SUPERSEDED
    with pytest.raises(ApprovalConflict):
        service.resolve(expired.request_id, "approve", expired.revision, actor="late")


@pytest.mark.asyncio
async def test_explicit_resume_has_one_atomic_claim_and_never_auto_replays(tmp_path: Path) -> None:
    from harness.approvals import ApprovalAlreadyClaimed, ApprovalService

    service = ApprovalService(_store(tmp_path / "approval.db"), root_session_id="root")
    request = _create(service)
    approved = service.resolve(request.request_id, "approve", request.revision, actor="user")
    dispatched = 0

    async def execute(tool_name, tool_input, tool_call_id):
        nonlocal dispatched
        dispatched += 1
        await asyncio.sleep(0.02)
        return {"ok": True}

    async def resume(owner):
        return await service.resume_approved_tool(
            approved.request_id,
            approved.revision,
            claim_owner=owner,
            executor=execute,
            current_tool_name="Bash",
            current_effective_input={"command": "echo ok"},
            current_policy_revision=3,
            current_tool_call_id="call-1",
        )

    outcomes = await asyncio.gather(resume("one"), resume("two"), return_exceptions=True)
    assert dispatched == 1
    assert sum(isinstance(item, ApprovalAlreadyClaimed) for item in outcomes) == 1
    execution = next(item for item in outcomes if not isinstance(item, Exception))
    assert execution.status is ApprovedToolExecutionStatus.SUCCEEDED

    resumed = ApprovalService(_store(tmp_path / "approval.db"), root_session_id="root")
    with pytest.raises(ApprovalAlreadyClaimed):
        await resumed.resume_approved_tool(
            approved.request_id,
            approved.revision,
            claim_owner="restart",
            executor=execute,
            current_tool_name="Bash",
            current_effective_input={"command": "echo ok"},
            current_policy_revision=3,
            current_tool_call_id="call-1",
        )
    assert dispatched == 1


@pytest.mark.asyncio
async def test_resume_binding_change_interrupts_without_dispatch(tmp_path: Path) -> None:
    from harness.approvals import ApprovalBindingChanged, ApprovalService

    service = ApprovalService(_store(tmp_path / "approval.db"), root_session_id="root")
    request = _create(service)
    approved = service.resolve(request.request_id, "approve", request.revision, actor="user")

    async def execute(*args):
        raise AssertionError("must not dispatch")

    with pytest.raises(ApprovalBindingChanged):
        await service.resume_approved_tool(
            approved.request_id,
            approved.revision,
            claim_owner="owner",
            executor=execute,
            current_tool_name="Bash",
            current_effective_input={"command": "echo ok"},
            current_policy_revision=4,
            current_tool_call_id="call-1",
        )
    execution = service.store.approved_tool_executions.get_by_request(approved.request_id)
    assert execution is not None
    assert execution.status is ApprovedToolExecutionStatus.INTERRUPTED


def test_rule_update_failure_does_not_resolve_request(tmp_path: Path) -> None:
    from harness.approvals import ApprovalService

    class FailingRules:
        def validate_updates(self, updates):
            return tuple(updates)

        def apply_updates(self, updates):
            raise OSError("settings unavailable")

    service = ApprovalService(
        _store(tmp_path / "approval.db"),
        root_session_id="root",
        rule_service=FailingRules(),
    )
    request = _create(service)

    with pytest.raises(OSError, match="settings unavailable"):
        service.resolve(
            request.request_id,
            "approve",
            request.revision,
            actor="user",
            permission_updates=(
                {
                    "type": "addRules",
                    "rules": ["Bash"],
                    "behavior": "allow",
                    "destination": "userSettings",
                },
            ),
        )

    assert service.get(request.request_id).status is PermissionRequestStatus.PENDING


def test_reconcile_interrupts_missing_bindings_and_supersedes_changed_bindings(
    tmp_path: Path,
) -> None:
    from harness.approvals import ApprovalService

    service = ApprovalService(_store(tmp_path / "approval.db"), root_session_id="root")
    missing = _create(service, tool_call_id="missing")
    changed = _create(service, tool_call_id="changed")

    def bindings(request):
        if request.request_id == missing.request_id:
            return None
        return ("Bash", {"command": "echo changed"}, 3)

    reconciled = service.reconcile(binding_resolver=bindings)

    assert service.get(missing.request_id).status is PermissionRequestStatus.INTERRUPTED
    assert service.get(changed.request_id).status is PermissionRequestStatus.SUPERSEDED
    assert len(reconciled["invalid_bindings"]) == 2


def test_resolution_rules_and_outbox_commit_or_rollback_together(tmp_path: Path) -> None:
    from harness.approvals import ApprovalService

    db = tmp_path / "approval.db"
    store = _store(db)
    service = ApprovalService(store, root_session_id="root")
    request = _create(service)
    store.approval_transactions._before_commit = lambda: (_ for _ in ()).throw(
        OSError("injected commit failure")
    )
    update = {
        "type": "addRules",
        "rules": ["Bash"],
        "behavior": "allow",
        "destination": "userSettings",
    }

    with pytest.raises(OSError, match="injected"):
        service.resolve(
            request.request_id,
            "approve",
            request.revision,
            actor="user",
            permission_updates=(update,),
        )

    assert service.get(request.request_id).status is PermissionRequestStatus.PENDING
    assert store.permission_rules.list("root") == []
    assert store.outbox.list("root") == []

    store.approval_transactions._before_commit = None
    approved = service.resolve(
        request.request_id,
        "approve",
        request.revision,
        actor="user",
        permission_updates=(update,),
    )
    restarted = _store(db)
    events = restarted.outbox.list("root", kind="permission_resolved")
    assert approved.status is PermissionRequestStatus.APPROVED
    assert len(events) == 1
    assert events[0].aggregate_id == request.request_id
    assert (
        len([rule for rule in restarted.permission_rules.list("root") if rule.revoked_at is None])
        == 1
    )


def test_session_and_cli_permission_updates_persist_with_approval_and_rollback(
    tmp_path: Path,
) -> None:
    from harness.approvals import ApprovalService
    from harness.permissions import PermissionDecision, PermissionRuleService

    db = tmp_path / "approval.db"
    store = _store(db)
    store.states.create_session(SessionState.new("root"))
    service = ApprovalService(store, root_session_id="root")
    request = _create(service)
    extra = str((tmp_path / "extra").resolve())
    updates = (
        {
            "type": "addRules",
            "rules": ["Read", "Bash"],
            "behavior": "allow",
            "destination": "session",
        },
        {
            "type": "removeRules",
            "rules": ["Bash"],
            "behavior": "allow",
            "destination": "session",
        },
        {"type": "setMode", "mode": "plan", "destination": "session"},
        {
            "type": "addDirectories",
            "directories": [extra],
            "destination": "session",
        },
        {
            "type": "removeDirectories",
            "directories": [extra],
            "destination": "session",
        },
        {
            "type": "addRules",
            "rules": ["Bash"],
            "behavior": "deny",
            "destination": "cliArg",
        },
        {"type": "setMode", "mode": "default", "destination": "cliArg"},
        {
            "type": "addDirectories",
            "directories": [extra],
            "destination": "cliArg",
        },
    )

    approved = service.resolve(
        request.request_id,
        "approve",
        request.revision,
        actor="user",
        permission_updates=updates,
    )
    restarted = _store(db)
    state = restarted.states.load_session("root")
    rules = PermissionRuleService(
        restarted.permission_rules,
        root_session_id="root",
        snapshots=state.permission_scope_snapshots,
    )

    assert approved.status is PermissionRequestStatus.APPROVED
    assert state.revision == 1
    assert rules.decision("Read", {}) is PermissionDecision.ALLOW
    assert rules.decision("Bash", {}) is PermissionDecision.DENY
    assert rules.current_mode() == "default"
    assert rules.directories() == (Path(extra),)

    failed = _create(service, tool_call_id="rollback")
    before = restarted.states.load_session("root").to_dict()
    store.approval_transactions._before_commit = lambda: (_ for _ in ()).throw(
        OSError("snapshot commit failure")
    )
    with pytest.raises(OSError, match="snapshot commit failure"):
        service.resolve(
            failed.request_id,
            "approve",
            failed.revision,
            actor="user",
            permission_updates=(
                {
                    "type": "addRules",
                    "rules": ["Write"],
                    "behavior": "allow",
                    "destination": "session",
                },
            ),
        )

    assert _store(db).states.load_session("root").to_dict() == before
    assert service.get(failed.request_id).status is PermissionRequestStatus.PENDING
    assert len(_store(db).outbox.list("root", kind="permission_resolved")) == 1


def test_durable_permission_updates_apply_in_order_within_one_resolution(
    tmp_path: Path,
) -> None:
    from harness.approvals import ApprovalService
    from harness.permissions import PermissionRuleService

    store = _store(tmp_path / "approval.db")
    service = ApprovalService(store, root_session_id="root")
    request = _create(service)
    first_dir = str((tmp_path / "first").resolve())
    second_dir = str((tmp_path / "second").resolve())
    updates = (
        {
            "type": "addRules",
            "rules": ["Bash", "Read"],
            "behavior": "allow",
            "destination": "projectSettings",
        },
        {
            "type": "removeRules",
            "rules": ["Bash"],
            "behavior": "allow",
            "destination": "projectSettings",
        },
        {
            "type": "replaceRules",
            "rules": ["Write"],
            "behavior": "allow",
            "destination": "projectSettings",
        },
        {
            "type": "addDirectories",
            "directories": [first_dir, second_dir],
            "destination": "localSettings",
        },
        {
            "type": "removeDirectories",
            "directories": [first_dir],
            "destination": "localSettings",
        },
        {"type": "setMode", "mode": "plan", "destination": "userSettings"},
        {"type": "setMode", "mode": "default", "destination": "userSettings"},
    )

    service.resolve(
        request.request_id,
        "approve",
        request.revision,
        actor="user",
        permission_updates=updates,
    )
    rules = PermissionRuleService(store.permission_rules, root_session_id="root")

    assert rules.snapshot("projectSettings") == (
        {"kind": "rule", "behavior": "allow", "rule": "Write", "directory": None, "mode": None},
    )
    assert rules.directories() == (Path(second_dir),)
    assert rules.current_mode() == "default"


def test_concurrent_resolution_has_one_cross_process_cas_winner_and_one_outbox(
    tmp_path: Path,
) -> None:
    from harness.approvals import ApprovalConflict, ApprovalService

    db = tmp_path / "approval.db"
    creator = ApprovalService(_store(db), root_session_id="root")
    request = _create(creator)

    def decide(decision: str):
        service = ApprovalService(_store(db), root_session_id="root")
        try:
            return service.resolve(request.request_id, decision, request.revision, actor=decision)
        except ApprovalConflict as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(decide, ("approve", "deny")))

    assert sum(not isinstance(item, Exception) for item in outcomes) == 1
    assert len(_store(db).outbox.list("root", kind="permission_resolved")) == 1


@pytest.mark.asyncio
async def test_waiter_registers_then_rechecks_to_close_resolve_race(tmp_path: Path) -> None:
    from harness.approvals import ApprovalService

    db = tmp_path / "approval.db"
    service = ApprovalService(_store(db), root_session_id="root")
    resolver = ApprovalService(_store(db), root_session_id="root")
    request = _create(service)
    original_get = service._requests.get
    raced = False

    def racing_get(request_id):
        nonlocal raced
        record = original_get(request_id)
        if not raced:
            raced = True
            resolver.resolve(request_id, "approve", record.revision, actor="racer")
        return record

    service._requests.get = racing_get
    started = time.monotonic()
    result = await service.await_request(request.request_id, timeout=0.5)

    assert result.status is PermissionRequestStatus.APPROVED
    assert time.monotonic() - started < 0.1


@pytest.mark.asyncio
async def test_waiter_observes_resolution_from_another_service_without_local_publish(
    tmp_path: Path,
) -> None:
    from harness.approvals import ApprovalService

    db = tmp_path / "approval.db"
    waiter_service = ApprovalService(_store(db), root_session_id="root", poll_interval=0.01)
    resolver_service = ApprovalService(_store(db), root_session_id="root")
    request = _create(waiter_service)
    waiter = asyncio.create_task(waiter_service.await_request(request.request_id))
    await asyncio.sleep(0.02)

    approved = resolver_service.resolve(
        request.request_id, "approve", request.revision, actor="other-process"
    )

    assert await asyncio.wait_for(waiter, 0.25) == approved


@pytest.mark.asyncio
async def test_waiter_timeout_and_cancellation_token_are_bounded_and_cleanup(
    tmp_path: Path,
) -> None:
    from harness.approvals import ApprovalService
    from harness.context import CancellationToken

    service = ApprovalService(
        _store(tmp_path / "approval.db"), root_session_id="root", poll_interval=0.01
    )
    timed = _create(service, tool_call_id="timed")
    cancelled = _create(service, tool_call_id="cancelled")

    result = await service.await_request(timed.request_id, timeout=0.03)
    token = CancellationToken()
    waiter = asyncio.create_task(service.await_request(cancelled.request_id, cancellation=token))
    await asyncio.sleep(0)
    token.cancel()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(waiter, 0.25)
    assert result.status is PermissionRequestStatus.TIMED_OUT
    assert service.get(cancelled.request_id).status is PermissionRequestStatus.PENDING
    assert service._waiters == {}


@pytest.mark.asyncio
async def test_resume_requires_every_binding_and_interrupts_missing_binding(tmp_path: Path) -> None:
    from harness.approvals import ApprovalBindingChanged, ApprovalService

    service = ApprovalService(_store(tmp_path / "approval.db"), root_session_id="root")
    request = _create(service)
    approved = service.resolve(request.request_id, "approve", request.revision, actor="user")

    async def execute(*args):
        raise AssertionError("must not dispatch")

    with pytest.raises(ApprovalBindingChanged):
        await service.resume_approved_tool(
            approved.request_id,
            approved.revision,
            claim_owner="owner",
            executor=execute,
            current_tool_name=None,
            current_effective_input=None,
            current_policy_revision=None,
            current_tool_call_id=None,
        )
    execution = service.store.approved_tool_executions.get_by_request(approved.request_id)
    assert execution.status is ApprovedToolExecutionStatus.INTERRUPTED
