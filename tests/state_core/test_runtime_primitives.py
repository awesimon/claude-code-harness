from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from models import Base
from state_core import (
    ApprovedToolExecutionRecord,
    ApprovedToolExecutionStatus,
    EventType,
    ExecutionTaskRecord,
    ExecutionTaskStatus,
    HookDefinitionRecord,
    HookInvocationRecord,
    HookInvocationStatus,
    PermissionRequestRecord,
    PermissionRequestStatus,
    PermissionRuleRecord,
    PermissionRuleScope,
    RuntimeRecordRevisionConflict,
    SessionRuntime,
    SessionState,
    SkillActivationRecord,
    SQLAlchemyStateStore,
    TeamMemberRecord,
    TeamMemberStatus,
    TeamMessageRecord,
    TeamRecord,
    TeamStatus,
)
from state_core.sqlalchemy_store import RuntimeEvent, RuntimeSession, RuntimeSnapshot


@pytest.fixture
def session_factory(tmp_path: Path) -> sessionmaker[Session]:
    engine = create_engine(f"sqlite:///{tmp_path / 'runtime-primitives.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def store(session_factory: sessionmaker[Session]) -> SQLAlchemyStateStore:
    return SQLAlchemyStateStore(session_factory)


def test_permission_request_resolution_is_durable_cas_and_idempotent(
    store: SQLAlchemyStateStore, session_factory: sessionmaker[Session]
) -> None:
    deadline = datetime.now(timezone.utc) + timedelta(minutes=5)
    created = store.permission_requests.create(
        PermissionRequestRecord(
            request_id="permission-1",
            root_session_id="root-1",
            agent_id="agent-1",
            tool_call_id="call-1",
            tool_name="Bash",
            original_input={"command": "pwd"},
            effective_input={"command": "pwd"},
            input_digest="sha256:input",
            reason="requires approval",
            permission_mode="default",
            policy_revision=3,
            deadline_at=deadline,
            idempotency_key="permission:call-1",
        )
    )

    approved = store.permission_requests.transition(
        created.request_id,
        PermissionRequestStatus.APPROVED,
        created.revision,
        actor="user-1",
        decision_reason="approved",
        updated_input={"command": "pwd"},
        permission_updates=[{"operation": "addRules", "rules": ["Bash(pwd)"]}],
    )
    assert approved.revision == 1
    assert approved.resolved_at is not None
    assert approved.permission_updates[0]["operation"] == "addRules"
    assert store.permission_requests.transition(
        approved.request_id,
        PermissionRequestStatus.APPROVED,
        created.revision,
        actor="user-1",
        decision_reason="approved",
        updated_input={"command": "pwd"},
        permission_updates=[{"operation": "addRules", "rules": ["Bash(pwd)"]}],
    ) == approved

    with pytest.raises(RuntimeRecordRevisionConflict):
        store.permission_requests.transition(
            approved.request_id,
            PermissionRequestStatus.DENIED,
            created.revision,
            decision_reason="conflict",
        )

    restarted = SQLAlchemyStateStore(session_factory)
    assert restarted.permission_requests.get(created.request_id) == approved
    assert [item.request_id for item in restarted.permission_requests.list("root-1")] == [
        "permission-1"
    ]


def test_approved_execution_claim_and_permission_rule_use_unique_cas_records(
    store: SQLAlchemyStateStore,
) -> None:
    claim = store.approved_tool_executions.create(
        ApprovedToolExecutionRecord(
            execution_id="execution-1",
            request_id="permission-1",
            root_session_id="root-1",
            request_revision=1,
            policy_revision=3,
            claim_owner="runtime-1",
            tool_call_id="call-1",
            idempotency_key="resume:permission-1",
        )
    )
    running = store.approved_tool_executions.transition(
        claim.execution_id,
        ApprovedToolExecutionStatus.RUNNING,
        claim.revision,
    )
    succeeded = store.approved_tool_executions.transition(
        running.execution_id,
        ApprovedToolExecutionStatus.SUCCEEDED,
        running.revision,
        result_reference="event:42",
    )
    assert store.approved_tool_executions.get_by_request("permission-1") == succeeded
    with pytest.raises(RuntimeRecordRevisionConflict):
        store.approved_tool_executions.transition(
            succeeded.execution_id,
            ApprovedToolExecutionStatus.FAILED,
            running.revision,
        )

    rule = store.permission_rules.create(
        PermissionRuleRecord(
            rule_id="rule-1",
            root_session_id="root-1",
            kind="rule",
            behavior="allow",
            rule="Bash(pwd)",
            scope=PermissionRuleScope.PROJECT_SETTINGS,
            source="approval",
        )
    )
    revoked = store.permission_rules.revoke(rule.rule_id, rule.revision)
    assert revoked.revoked_at is not None
    with pytest.raises(RuntimeRecordRevisionConflict):
        store.permission_rules.revoke(rule.rule_id, rule.revision)


def test_hook_definition_and_invocation_records_have_independent_cas_lifecycles(
    store: SQLAlchemyStateStore,
) -> None:
    definition = store.hook_definitions.create(
        HookDefinitionRecord(
            definition_id="hook-1",
            root_session_id="root-1",
            event="PreToolUse",
            matcher="Bash",
            runner_kind="command",
            runner_config={"command": "check.sh"},
            source="projectSettings",
            order=10,
            timeout_ms=1000,
            once=True,
        )
    )
    disabled = store.hook_definitions.create_version(
        definition.definition_id, definition.revision, enabled=False
    )
    assert disabled.config_revision == 1
    assert store.hook_definitions.get_version(definition.definition_id, 0) == definition
    assert store.hook_definitions.get(definition.definition_id) == disabled

    invocation = store.hook_invocations.create(
        HookInvocationRecord(
            invocation_id="hook-run-1",
            root_session_id="root-1",
            definition_id=definition.definition_id,
            definition_revision=definition.config_revision,
            event="PreToolUse",
            event_envelope={"tool_name": "Bash"},
            correlation_id="correlation-1",
            idempotency_key="hook:call-1:hook-1",
        )
    )
    running = store.hook_invocations.transition(
        invocation.invocation_id,
        HookInvocationStatus.RUNNING,
        invocation.revision,
        lease_owner="runtime-1",
        lease_expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
    )
    blocked = store.hook_invocations.transition(
        running.invocation_id,
        HookInvocationStatus.BLOCKED,
        running.revision,
        outcome={"reason": "policy"},
    )
    assert blocked.finished_at is not None
    assert blocked.duration_ms is not None
    with pytest.raises(RuntimeRecordRevisionConflict):
        store.hook_invocations.transition(
            blocked.invocation_id,
            HookInvocationStatus.SUCCEEDED,
            running.revision,
        )


def test_execution_task_output_is_cursor_addressable_and_task_transition_is_cas(
    store: SQLAlchemyStateStore, session_factory: sessionmaker[Session]
) -> None:
    task = store.execution_tasks.create(
        ExecutionTaskRecord(
            task_id="shell-1",
            root_session_id="root-1",
            agent_id="agent-1",
            kind="shell",
            command="printf hello",
            description="print output",
            canonical_cwd="/repo",
            output_artifact_id="output-1",
            timeout_ms=1000,
            safe_environment={"LANG": "C"},
        )
    )
    running = store.execution_tasks.transition(
        task.task_id,
        ExecutionTaskStatus.RUNNING,
        task.revision,
        process_owner_token="runtime-1",
    )
    after_first = store.execution_tasks.append_output(
        running.task_id, b"hel", running.revision
    )
    after_second = store.execution_tasks.append_output(
        after_first.task_id, b"lo", after_first.revision
    )
    chunk = store.execution_tasks.read_output(after_second.task_id, cursor=2, max_bytes=2)
    assert chunk.data == b"ll"
    assert chunk.next_cursor == 4
    assert chunk.total_bytes == 5

    completed = store.execution_tasks.transition(
        after_second.task_id,
        ExecutionTaskStatus.COMPLETED,
        after_second.revision,
        exit_code=0,
    )
    assert completed.output_byte_count == 5
    assert SQLAlchemyStateStore(session_factory).execution_tasks.read_output(
        completed.task_id, cursor=0, max_bytes=10
    ).data == b"hello"


def test_team_members_mailbox_and_skill_activation_are_durable_and_revision_safe(
    store: SQLAlchemyStateStore, session_factory: sessionmaker[Session]
) -> None:
    team = store.teams.create(
        TeamRecord(
            team_id="team-1",
            root_session_id="root-1",
            name="research",
            lead_agent_id="lead-1",
            task_list_id="team-tasks-1",
        )
    )
    member = store.team_members.create(
        TeamMemberRecord(
            member_id="member-1",
            team_id=team.team_id,
            root_session_id="root-1",
            agent_id="agent-1",
            name="analyst",
            agent_type="worker",
            role="research",
        )
    )
    running = store.team_members.transition(
        member.member_id, TeamMemberStatus.RUNNING, member.revision
    )
    idle = store.team_members.transition(
        running.member_id, TeamMemberStatus.IDLE, running.revision
    )
    message = store.team_messages.append(
        TeamMessageRecord(
            message_id="message-1",
            team_id=team.team_id,
            root_session_id="root-1",
            sender_member_id=None,
            recipient_member_id=idle.member_id,
            message_type="message",
            body={"text": "continue"},
            request_correlation_id="request-1",
        )
    )
    duplicate = store.team_messages.append(message)
    assert duplicate == message
    assert message.sequence == 1
    assert store.team_messages.list_for_member(team.team_id, idle.member_id, after_sequence=0) == [
        message
    ]

    advanced = store.team_members.update_mailbox_cursor(
        idle.member_id, idle.revision, message.sequence
    )
    assert advanced.mailbox_cursor == 1
    closing = store.teams.transition(team.team_id, TeamStatus.CLOSING, team.revision)
    assert closing.status is TeamStatus.CLOSING

    activation = store.skill_activations.create(
        SkillActivationRecord(
            activation_id="activation-1",
            root_session_id="root-1",
            agent_id="agent-1",
            skill_name="research",
            skill_digest="sha256:skill",
            snapshot={"path": "/repo/.claude/skills/research/SKILL.md"},
            registered_hook_ids=["hook-1"],
            allowed_tools=["WebSearch"],
        )
    )
    assert store.skill_activations.create(activation) == activation
    restarted = SQLAlchemyStateStore(session_factory)
    assert restarted.skill_activations.get_for_skill(
        "root-1", "agent-1", "research", "sha256:skill"
    ) == activation
    assert restarted.team_members.get(member.member_id) == advanced


def test_recovery_transitions_only_abandoned_nonterminal_records(
    store: SQLAlchemyStateStore,
) -> None:
    now = datetime.now(timezone.utc)
    expired = store.permission_requests.create(
        PermissionRequestRecord(
            request_id="expired",
            root_session_id="root-1",
            agent_id="agent-1",
            tool_call_id="call-expired",
            tool_name="Bash",
            original_input={"command": "pwd"},
            effective_input={"command": "pwd"},
            input_digest="sha256:expired",
            reason="approval",
            permission_mode="default",
            policy_revision=1,
            deadline_at=now - timedelta(seconds=1),
            idempotency_key="permission:expired",
        )
    )
    live = store.permission_requests.create(
        PermissionRequestRecord(
            request_id="live",
            root_session_id="root-1",
            agent_id="agent-1",
            tool_call_id="call-live",
            tool_name="Bash",
            original_input={"command": "pwd"},
            effective_input={"command": "pwd"},
            input_digest="sha256:live",
            reason="approval",
            permission_mode="default",
            policy_revision=1,
            deadline_at=now + timedelta(minutes=1),
            idempotency_key="permission:live",
        )
    )
    assert [item.request_id for item in store.permission_requests.expire_due("root-1", now)] == [
        expired.request_id
    ]
    assert store.permission_requests.get(live.request_id).status is PermissionRequestStatus.PENDING  # type: ignore[union-attr]

    execution = store.approved_tool_executions.create(
        ApprovedToolExecutionRecord(
            execution_id="abandoned-execution",
            request_id="approved-request",
            root_session_id="root-1",
            request_revision=1,
            policy_revision=1,
            claim_owner="dead-runtime",
            tool_call_id="call-approved",
            idempotency_key="resume:approved-request",
        )
    )
    execution = store.approved_tool_executions.transition(
        execution.execution_id,
        ApprovedToolExecutionStatus.RUNNING,
        execution.revision,
    )
    invocation = store.hook_invocations.create(
        HookInvocationRecord(
            invocation_id="abandoned-hook",
            root_session_id="root-1",
            definition_id="hook-1",
            definition_revision=1,
            event="PreToolUse",
            event_envelope={},
            correlation_id="correlation-1",
            idempotency_key="hook:abandoned",
        )
    )
    invocation = store.hook_invocations.transition(
        invocation.invocation_id,
        HookInvocationStatus.RUNNING,
        invocation.revision,
        lease_owner="dead-runtime",
        lease_expires_at=now - timedelta(seconds=1),
    )
    shell = store.execution_tasks.create(
        ExecutionTaskRecord(
            task_id="abandoned-shell",
            root_session_id="root-1",
            agent_id="agent-1",
            kind="shell",
            command="sleep 10",
            description="sleep",
            canonical_cwd="/repo",
            output_artifact_id="output-abandoned",
            timeout_ms=60_000,
        )
    )
    shell = store.execution_tasks.transition(
        shell.task_id,
        ExecutionTaskStatus.RUNNING,
        shell.revision,
        process_owner_token="dead-runtime",
    )

    assert store.approved_tool_executions.interrupt_open(
        "root-1", live_owner_tokens={"live-runtime"}, now=now
    )[0].status is ApprovedToolExecutionStatus.INTERRUPTED
    assert store.hook_invocations.interrupt_open(
        "root-1", live_owner_tokens={"live-runtime"}, now=now
    )[0].status is HookInvocationStatus.INTERRUPTED
    assert store.execution_tasks.interrupt_open(
        "root-1", live_owner_tokens={"live-runtime"}, now=now
    )[0].status is ExecutionTaskStatus.INTERRUPTED


def test_public_mutations_reject_immutable_binding_fields(store: SQLAlchemyStateStore) -> None:
    request = store.permission_requests.create(
        PermissionRequestRecord(
            request_id="immutable-permission",
            root_session_id="root-1",
            agent_id="agent-1",
            tool_call_id="call-immutable",
            tool_name="Bash",
            original_input={"command": "pwd"},
            effective_input={"command": "pwd"},
            input_digest="sha256:immutable",
            reason="approval",
            permission_mode="default",
            policy_revision=1,
            idempotency_key="permission:immutable",
        )
    )
    with pytest.raises(TypeError, match="unsupported permission request fields"):
        store.permission_requests.transition(
            request.request_id,
            PermissionRequestStatus.APPROVED,
            request.revision,
            root_session_id="root-2",
        )

    task = store.execution_tasks.create(
        ExecutionTaskRecord(
            task_id="immutable-shell",
            root_session_id="root-1",
            agent_id="agent-1",
            kind="shell",
            command="pwd",
            description="pwd",
            canonical_cwd="/repo",
            output_artifact_id="immutable-output",
            timeout_ms=1000,
        )
    )
    with pytest.raises(TypeError, match="unsupported execution task fields"):
        store.execution_tasks.transition(
            task.task_id,
            ExecutionTaskStatus.RUNNING,
            task.revision,
            command="rm -rf /",
        )


def test_create_requires_revision_zero_and_legal_initial_status(store: SQLAlchemyStateStore) -> None:
    with pytest.raises(ValueError, match="revision 0"):
        store.permission_requests.create(
            PermissionRequestRecord(
                request_id="bad-revision",
                root_session_id="root-1",
                agent_id="agent-1",
                tool_call_id="call-bad-revision",
                tool_name="Bash",
                original_input={},
                effective_input={},
                input_digest="sha256:bad-revision",
                reason="approval",
                permission_mode="default",
                policy_revision=1,
                idempotency_key="permission:bad-revision",
                revision=2,
            )
        )
    with pytest.raises(ValueError, match="initial status"):
        store.execution_tasks.create(
            ExecutionTaskRecord(
                task_id="bad-status",
                root_session_id="root-1",
                agent_id="agent-1",
                kind="shell",
                command="pwd",
                description="pwd",
                canonical_cwd="/repo",
                output_artifact_id="bad-status-output",
                timeout_ms=1000,
                status=ExecutionTaskStatus.RUNNING,
                started_at=datetime.now(timezone.utc),
            )
        )


def test_idempotency_keys_deduplicate_permission_and_hook_create(
    store: SQLAlchemyStateStore,
) -> None:
    original = store.permission_requests.create(
        PermissionRequestRecord(
            request_id="permission-first",
            root_session_id="root-1",
            agent_id="agent-1",
            tool_call_id="call-idempotent",
            tool_name="Bash",
            original_input={},
            effective_input={},
            input_digest="sha256:idempotent",
            reason="approval",
            permission_mode="default",
            policy_revision=1,
            idempotency_key="permission:same-operation",
        )
    )
    duplicate = store.permission_requests.create(
        PermissionRequestRecord(
            request_id="permission-retry",
            root_session_id="root-1",
            agent_id="agent-1",
            tool_call_id="call-idempotent",
            tool_name="Bash",
            original_input={},
            effective_input={},
            input_digest="sha256:idempotent",
            reason="approval",
            permission_mode="default",
            policy_revision=1,
            idempotency_key="permission:same-operation",
        )
    )
    assert duplicate == original

    first_hook = store.hook_invocations.create(
        HookInvocationRecord(
            invocation_id="hook-first",
            root_session_id="root-1",
            definition_id="hook-1",
            definition_revision=0,
            event="PreToolUse",
            event_envelope={},
            correlation_id="correlation-idempotent",
            idempotency_key="hook:same-operation",
        )
    )
    retry_hook = store.hook_invocations.create(
        HookInvocationRecord(
            invocation_id="hook-retry",
            root_session_id="root-1",
            definition_id="hook-1",
            definition_revision=0,
            event="PreToolUse",
            event_envelope={},
            correlation_id="correlation-idempotent",
            idempotency_key="hook:same-operation",
        )
    )
    assert retry_hook == first_hook


def test_permission_terminal_replay_ignores_revision_and_resolution_metadata(
    store: SQLAlchemyStateStore,
) -> None:
    request = store.permission_requests.create(
        PermissionRequestRecord(
            request_id="terminal-replay",
            root_session_id="root-1",
            agent_id="agent-1",
            tool_call_id="call-terminal-replay",
            tool_name="Bash",
            original_input={},
            effective_input={},
            input_digest="sha256:terminal-replay",
            reason="approval",
            permission_mode="default",
            policy_revision=1,
            idempotency_key="permission:terminal-replay",
        )
    )
    approved = store.permission_requests.transition(
        request.request_id,
        PermissionRequestStatus.APPROVED,
        request.revision,
        actor="first-actor",
    )
    replayed = store.permission_requests.transition(
        request.request_id,
        PermissionRequestStatus.APPROVED,
        expected_revision=999,
        actor="first-actor",
    )
    assert replayed == approved


def test_errors_are_sanitized_environment_is_allowlisted_and_output_is_bounded(
    store: SQLAlchemyStateStore,
) -> None:
    with pytest.raises(ValueError, match="safe environment"):
        ExecutionTaskRecord(
            task_id="unsafe-env",
            root_session_id="root-1",
            agent_id="agent-1",
            kind="shell",
            command="pwd",
            description="pwd",
            canonical_cwd="/repo",
            output_artifact_id="unsafe-env-output",
            timeout_ms=1000,
            safe_environment={"AWS_SECRET_ACCESS_KEY": "secret"},
        )

    execution = store.approved_tool_executions.create(
        ApprovedToolExecutionRecord(
            execution_id="sanitized-error",
            request_id="sanitized-request",
            root_session_id="root-1",
            request_revision=1,
            policy_revision=1,
            claim_owner="runtime-1",
            tool_call_id="call-sanitized",
            idempotency_key="resume:sanitized",
        )
    )
    execution = store.approved_tool_executions.transition(
        execution.execution_id,
        ApprovedToolExecutionStatus.RUNNING,
        execution.revision,
    )
    failed = store.approved_tool_executions.transition(
        execution.execution_id,
        ApprovedToolExecutionStatus.FAILED,
        execution.revision,
        error={"Authorization": "Bearer secret-token", "safe": "kept"},
    )
    assert failed.error == {"Authorization": "[REDACTED]", "safe": "kept"}

    shell = store.execution_tasks.create(
        ExecutionTaskRecord(
            task_id="bounded-output",
            root_session_id="root-1",
            agent_id="agent-1",
            kind="shell",
            command="pwd",
            description="pwd",
            canonical_cwd="/repo",
            output_artifact_id="bounded-output-artifact",
            timeout_ms=1000,
        )
    )
    shell = store.execution_tasks.transition(
        shell.task_id,
        ExecutionTaskStatus.RUNNING,
        shell.revision,
        process_owner_token="runtime-1",
    )
    with pytest.raises(ValueError, match="per-read"):
        store.execution_tasks.read_output(shell.task_id, cursor=0, max_bytes=10_000_000)
    with pytest.raises(ValueError, match="per-task"):
        store.execution_tasks.append_output(shell.task_id, b"x" * 10_000_000, shell.revision)


def test_permission_rule_variants_and_durable_scope_validation(store: SQLAlchemyStateStore) -> None:
    directory = store.permission_rules.create(
        PermissionRuleRecord(
            rule_id="directory-rule",
            root_session_id="root-1",
            kind="directory",
            directory="/repo/data",
            scope=PermissionRuleScope.PROJECT_SETTINGS,
            source="approval",
        )
    )
    mode = store.permission_rules.create(
        PermissionRuleRecord(
            rule_id="mode-rule",
            root_session_id="root-1",
            kind="mode",
            mode="acceptEdits",
            scope=PermissionRuleScope.USER_SETTINGS,
            source="approval",
        )
    )
    assert directory.directory == "/repo/data"
    assert mode.mode == "acceptEdits"
    with pytest.raises(ValueError, match="not repository-backed"):
        store.permission_rules.create(
            PermissionRuleRecord(
                rule_id="session-rule",
                root_session_id="root-1",
                kind="mode",
                mode="plan",
                scope=PermissionRuleScope.SESSION,
                source="runtime",
            )
        )


def test_hook_async_mode_retry_linkage_and_append_only_definition_versions(
    store: SQLAlchemyStateStore,
) -> None:
    with pytest.raises(ValueError, match="async mode"):
        HookDefinitionRecord(
            definition_id="invalid-async",
            root_session_id="root-1",
            event="Stop",
            matcher=None,
            runner_kind="command",
            runner_config={"command": "check.sh"},
            source="projectSettings",
            async_mode="later",  # type: ignore[arg-type]
        )
    invocation = HookInvocationRecord(
        invocation_id="hook-retry-2",
        root_session_id="root-1",
        definition_id="hook-1",
        definition_revision=1,
        event="Stop",
        event_envelope={},
        correlation_id="correlation-retry",
        idempotency_key="hook:retry-2",
        retry_of_invocation_id="hook-retry-1",
    )
    assert invocation.retry_of_invocation_id == "hook-retry-1"


def test_recovery_preserves_live_owners_and_observer_cannot_reconcile(
    store: SQLAlchemyStateStore,
) -> None:
    now = datetime.now(timezone.utc)
    shell = store.execution_tasks.create(
        ExecutionTaskRecord(
            task_id="live-shell",
            root_session_id="root-1",
            agent_id="agent-1",
            kind="shell",
            command="sleep 10",
            description="sleep",
            canonical_cwd="/repo",
            output_artifact_id="live-shell-output",
            timeout_ms=1000,
        )
    )
    shell = store.execution_tasks.transition(
        shell.task_id,
        ExecutionTaskStatus.RUNNING,
        shell.revision,
        process_owner_token="live-runtime",
    )
    assert store.execution_tasks.interrupt_open(
        "root-1", live_owner_tokens={"live-runtime"}, now=now
    ) == []
    assert store.execution_tasks.get(shell.task_id).status is ExecutionTaskStatus.RUNNING  # type: ignore[union-attr]

    member = store.team_members.create(
        TeamMemberRecord(
            member_id="leased-member",
            team_id="team-1",
            root_session_id="root-1",
            agent_id="agent-1",
            name="analyst",
            agent_type="worker",
            role="research",
            owner_token="dead-runtime",
            lease_expires_at=now - timedelta(seconds=1),
        )
    )
    member = store.team_members.transition(
        member.member_id, TeamMemberStatus.RUNNING, member.revision
    )
    with pytest.raises(PermissionError, match="observer"):
        store.team_members.reconcile(
            "root-1", live_owner_tokens=set(), now=now, observer=True
        )
    reconciled = store.team_members.reconcile(
        "root-1", live_owner_tokens=set(), now=now, observer=False
    )
    assert [item.member_id for item in reconciled] == [member.member_id]
    assert reconciled[0].status is TeamMemberStatus.INTERRUPTED


def test_skill_activation_retry_with_new_identity_returns_existing(
    store: SQLAlchemyStateStore,
) -> None:
    first = store.skill_activations.create(
        SkillActivationRecord(
            activation_id="skill-first",
            root_session_id="root-1",
            agent_id="agent-1",
            skill_name="research",
            skill_digest="sha256:same",
            snapshot={"path": "/repo/SKILL.md"},
        )
    )
    retried = store.skill_activations.create(
        SkillActivationRecord(
            activation_id="skill-retry",
            root_session_id="root-1",
            agent_id="agent-1",
            skill_name="research",
            skill_digest="sha256:same",
            snapshot={"path": "/repo/SKILL.md"},
        )
    )
    assert retried == first


def test_durable_json_payloads_are_deeply_frozen_bounded_and_reject_secrets() -> None:
    config = {"command": "check.sh", "options": {"args": ["--safe"]}}
    definition = HookDefinitionRecord(
        definition_id="frozen-hook",
        root_session_id="root-1",
        event="PreToolUse",
        matcher="Bash",
        runner_kind="command",
        runner_config=config,
        source="projectSettings",
    )
    config["options"]["args"][0] = "--mutated"
    assert definition.runner_config["options"]["args"] == ("--safe",)
    with pytest.raises(TypeError):
        definition.runner_config["command"] = "mutated"  # type: ignore[index]
    with pytest.raises(TypeError):
        definition.runner_config["options"]["args"][0] = "mutated"  # type: ignore[index]

    for field, builder in (
        (
            "runner_config",
            lambda value: HookDefinitionRecord(
                definition_id="bounded-hook",
                root_session_id="root-1",
                event="Stop",
                matcher=None,
                runner_kind="command",
                runner_config=value,
                source="projectSettings",
            ),
        ),
        (
            "event_envelope",
            lambda value: HookInvocationRecord(
                invocation_id="bounded-invocation",
                root_session_id="root-1",
                definition_id="hook-1",
                definition_revision=0,
                event="Stop",
                event_envelope=value,
                correlation_id="bounded",
                idempotency_key="hook:bounded",
            ),
        ),
        (
            "outcome",
            lambda value: HookInvocationRecord(
                invocation_id="bounded-outcome",
                root_session_id="root-1",
                definition_id="hook-1",
                definition_revision=0,
                event="Stop",
                event_envelope={},
                outcome=value,
                correlation_id="bounded-outcome",
                idempotency_key="hook:bounded-outcome",
            ),
        ),
        (
            "team message body",
            lambda value: TeamMessageRecord(
                message_id="bounded-message",
                team_id="team-1",
                root_session_id="root-1",
                sender_member_id=None,
                recipient_member_id=None,
                message_type="message",
                body=value,
            ),
        ),
        (
            "skill snapshot",
            lambda value: SkillActivationRecord(
                activation_id="bounded-skill",
                root_session_id="root-1",
                agent_id="agent-1",
                skill_name="bounded",
                skill_digest="sha256:bounded",
                snapshot=value,
            ),
        ),
    ):
        with pytest.raises(ValueError, match="size limit"):
            builder({"content": "x" * 2_000_000})
        with pytest.raises(ValueError, match="sensitive"):
            builder({"Authorization": "Bearer secret"})


def test_idempotency_uses_immutable_operation_identity_and_terminal_effects(
    store: SQLAlchemyStateStore,
) -> None:
    request = store.permission_requests.create(
        PermissionRequestRecord(
            request_id="identity-permission",
            root_session_id="root-1",
            agent_id="agent-1",
            tool_call_id="call-identity",
            tool_name="Bash",
            original_input={},
            effective_input={},
            input_digest="sha256:identity",
            reason="approval",
            permission_mode="default",
            policy_revision=1,
            idempotency_key="permission:identity",
        )
    )
    approved = store.permission_requests.transition(
        request.request_id,
        PermissionRequestStatus.APPROVED,
        request.revision,
        actor="user-1",
        decision_reason="approved once",
        updated_input={"command": "pwd"},
        permission_updates=[{"operation": "addRules"}],
    )
    retry_create = store.permission_requests.create(
        PermissionRequestRecord(
            request_id="identity-permission-retry",
            root_session_id="root-1",
            agent_id="agent-1",
            tool_call_id="call-identity",
            tool_name="Bash",
            original_input={},
            effective_input={},
            input_digest="sha256:identity",
            reason="approval",
            permission_mode="default",
            policy_revision=1,
            idempotency_key="permission:identity",
        )
    )
    assert retry_create == approved

    with pytest.raises(RuntimeRecordRevisionConflict):
        store.permission_requests.transition(
            request.request_id,
            PermissionRequestStatus.APPROVED,
            expected_revision=999,
            actor="different-user",
        )
    with pytest.raises(RuntimeRecordRevisionConflict):
        store.permission_requests.transition(
            request.request_id,
            PermissionRequestStatus.APPROVED,
            expected_revision=999,
            updated_input={"command": "different"},
        )
    with pytest.raises(RuntimeRecordRevisionConflict):
        store.permission_requests.transition(
            request.request_id,
            PermissionRequestStatus.APPROVED,
            expected_revision=999,
            decision_reason="different reason",
        )
    with pytest.raises(RuntimeRecordRevisionConflict):
        store.permission_requests.transition(
            request.request_id,
            PermissionRequestStatus.APPROVED,
            expected_revision=999,
            permission_updates=[{"operation": "removeRules"}],
        )
    with pytest.raises(RuntimeRecordRevisionConflict):
        store.permission_requests.transition(
            request.request_id,
            PermissionRequestStatus.APPROVED,
            expected_revision=999,
        )


def test_team_message_idempotency_ignores_created_at_but_binds_stable_fields(
    store: SQLAlchemyStateStore,
) -> None:
    first = store.team_messages.append(
        TeamMessageRecord(
            message_id="stable-message",
            team_id="team-1",
            root_session_id="root-1",
            sender_member_id="sender-1",
            recipient_member_id="recipient-1",
            message_type="message",
            body={"text": "hello"},
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
    )
    retry = store.team_messages.append(
        TeamMessageRecord(
            message_id="stable-message",
            team_id="team-1",
            root_session_id="root-1",
            sender_member_id="sender-1",
            recipient_member_id="recipient-1",
            message_type="message",
            body={"text": "hello"},
            created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
    )
    assert retry == first
    with pytest.raises(RuntimeRecordRevisionConflict):
        store.team_messages.append(
            TeamMessageRecord(
                message_id="stable-message",
                team_id="team-1",
                root_session_id="root-1",
                sender_member_id="sender-1",
                recipient_member_id="recipient-1",
                message_type="message",
                body={"text": "changed"},
            )
        )


def test_recovery_interrupts_abandoned_queued_hook(store: SQLAlchemyStateStore) -> None:
    queued = store.hook_invocations.create(
        HookInvocationRecord(
            invocation_id="queued-hook",
            root_session_id="root-1",
            definition_id="hook-1",
            definition_revision=0,
            event="Stop",
            event_envelope={},
            correlation_id="queued-correlation",
            idempotency_key="hook:queued",
        )
    )
    interrupted = store.hook_invocations.interrupt_open(
        "root-1",
        live_owner_tokens=set(),
        now=datetime.now(timezone.utc),
    )
    assert [item.invocation_id for item in interrupted] == [queued.invocation_id]
    assert interrupted[0].status is HookInvocationStatus.INTERRUPTED


def test_session_and_cli_permission_scopes_round_trip_in_owning_session_snapshot(
    store: SQLAlchemyStateStore,
) -> None:
    runtime = SessionRuntime("permission-snapshot", store)
    runtime.replace_permission_scope_snapshot(
        "session",
        [{"kind": "rule", "behavior": "allow", "rule": "Bash(pwd)"}],
    )
    runtime.replace_permission_scope_snapshot(
        "cliArg",
        [{"kind": "mode", "mode": "acceptEdits"}],
    )
    runtime.checkpoint()

    wire = runtime.state.to_dict()
    assert SessionState.from_dict(wire).to_dict()["permissionScopeSnapshots"] == {
        "session": [{"kind": "rule", "behavior": "allow", "rule": "Bash(pwd)"}],
        "cliArg": [{"kind": "mode", "mode": "acceptEdits"}],
    }
    recovered = SessionRuntime.recover(runtime.session_id, store)
    assert recovered.state.permission_scope_snapshots == runtime.state.permission_scope_snapshots


def test_permission_scope_snapshots_are_frozen_bounded_sensitive_and_schema_checked() -> None:
    source = {
        "session": [{"kind": "rule", "behavior": "allow", "rule": "Bash(pwd)"}],
        "cliArg": [{"kind": "directory", "directory": "/repo"}],
    }
    wire = SessionState.new("frozen-permissions").to_dict()
    wire["permissionScopeSnapshots"] = source
    state = SessionState.from_dict(wire)
    source["session"][0]["rule"] = "Bash(rm -rf /)"
    assert state.permission_scope_snapshots["session"][0]["rule"] == "Bash(pwd)"
    with pytest.raises(TypeError):
        state.permission_scope_snapshots["session"][0]["rule"] = "changed"  # type: ignore[index]

    for invalid in (
        {"session": [{"kind": "rule", "behavior": "maybe", "rule": "Bash(pwd)"}], "cliArg": []},
        {"session": [{"kind": "directory", "directory": "/repo", "rule": "extra"}], "cliArg": []},
        {"session": [{"kind": "mode"}], "cliArg": []},
        {"session": [{"kind": "rule", "behavior": "allow", "rule": "Bash(pwd)", "Authorization": "Bearer secret"}], "cliArg": []},
    ):
        invalid_wire = SessionState.new("invalid-permissions").to_dict()
        invalid_wire["permissionScopeSnapshots"] = invalid
        with pytest.raises((TypeError, ValueError)):
            SessionState.from_dict(invalid_wire)

    oversized_wire = SessionState.new("oversized-permissions").to_dict()
    oversized_wire["permissionScopeSnapshots"] = {
        "session": [{"kind": "rule", "behavior": "allow", "rule": "x" * 2_000_000}],
        "cliArg": [],
    }
    with pytest.raises(ValueError, match="size limit"):
        SessionState.from_dict(oversized_wire)


def test_permission_updates_have_total_count_and_byte_limits() -> None:
    base = {
        "request_id": "bounded-updates",
        "root_session_id": "root-1",
        "agent_id": "agent-1",
        "tool_call_id": "call-bounded-updates",
        "tool_name": "Bash",
        "original_input": {},
        "effective_input": {},
        "input_digest": "sha256:bounded-updates",
        "reason": "approval",
        "permission_mode": "default",
        "policy_revision": 1,
        "idempotency_key": "permission:bounded-updates",
    }
    with pytest.raises(ValueError, match="count limit"):
        PermissionRequestRecord(
            **base,
            permission_updates=tuple({"operation": "addRules"} for _ in range(1_000)),
        )
    with pytest.raises(ValueError, match="total size limit"):
        PermissionRequestRecord(
            **base,
            permission_updates=(
                {"operation": "addRules", "rules": ["x" * 180_000]},
                {"operation": "addRules", "rules": ["y" * 180_000]},
            ),
        )


@pytest.mark.parametrize(
    "rules",
    [
        [{"kind": "mode"}],
        [{"kind": "rule", "behavior": "allow", "rule": "x" * 2_000_000}],
        [
            {
                "kind": "rule",
                "behavior": "allow",
                "rule": "Bash(pwd)",
                "Authorization": "Bearer secret",
            }
        ],
    ],
)
def test_permission_snapshot_validation_failure_is_atomic_and_runtime_remains_usable(
    store: SQLAlchemyStateStore,
    rules: list[dict[str, str]],
) -> None:
    runtime = SessionRuntime(f"atomic-validation-{len(rules[0])}", store)
    before = runtime.state.to_dict()

    with pytest.raises((TypeError, ValueError)):
        runtime.replace_permission_scope_snapshot("session", rules)

    assert runtime.state.to_dict() == before
    runtime.enable_todo_v1()
    assert runtime.state.task_mode.value == "todo_v1"


def test_permission_snapshot_persist_failure_rolls_back_and_can_retry(
    store: SQLAlchemyStateStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = SessionRuntime("atomic-persist", store)
    before = runtime.state.to_dict()
    original_commit = store.states.commit

    def fail_commit(*args: object, **kwargs: object) -> object:
        raise RuntimeError("persist failed")

    monkeypatch.setattr(store.states, "commit", fail_commit)
    with pytest.raises(RuntimeError, match="persist failed"):
        runtime.replace_permission_scope_snapshot(
            "session",
            [{"kind": "mode", "mode": "acceptEdits"}],
        )
    assert runtime.state.to_dict() == before

    monkeypatch.setattr(store.states, "commit", original_commit)
    runtime.replace_permission_scope_snapshot(
        "session",
        [{"kind": "mode", "mode": "acceptEdits"}],
    )
    assert runtime.state.to_dict()["permissionScopeSnapshots"]["session"] == [
        {"kind": "mode", "mode": "acceptEdits"}
    ]


def test_skill_activation_claim_has_one_concurrent_winner(
    session_factory: sessionmaker[Session],
) -> None:
    barrier = Barrier(2)

    def claim(index: int) -> tuple[SkillActivationRecord, bool]:
        repository = SQLAlchemyStateStore(session_factory).skill_activations
        record = SkillActivationRecord(
            activation_id=f"concurrent-skill-{index}",
            root_session_id="root-1",
            agent_id="agent-1",
            skill_name="research",
            skill_digest=f"sha256:concurrent-{index}",
            snapshot={"path": f"/repo/SKILL-{index}.md"},
        )
        barrier.wait()
        return repository.claim_by_name(record)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, (1, 2)))

    assert sorted(created for _, created in results) == [False, True]
    assert results[0][0] == results[1][0]
    preparing = results[0][0]
    assert preparing.status.value == "preparing"

    finalize_barrier = Barrier(2)

    def finalize(_: int) -> tuple[SkillActivationRecord, bool]:
        repository = SQLAlchemyStateStore(session_factory).skill_activations
        finalize_barrier.wait()
        return repository.finalize_active(preparing.activation_id, preparing.revision)

    with ThreadPoolExecutor(max_workers=2) as pool:
        finalized = list(pool.map(finalize, (1, 2)))

    assert sorted(created for _, created in finalized) == [False, True]
    assert finalized[0][0] == finalized[1][0]
    assert finalized[0][0].status.value == "active"
    assert len(SQLAlchemyStateStore(session_factory).skill_activations.list("root-1")) == 1


def test_legacy_skill_activations_migrate_to_one_active_record_per_name(
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy-skill-activations.db'}")
    created_at = "2026-07-24T00:00:00+00:00"
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE runtime_skill_activations ("
                "record_id VARCHAR PRIMARY KEY, root_session_id VARCHAR NOT NULL, "
                "agent_id VARCHAR NOT NULL, skill_name VARCHAR NOT NULL, "
                "skill_digest VARCHAR NOT NULL, data JSON NOT NULL, "
                "UNIQUE (root_session_id, agent_id, skill_name, skill_digest))"
            )
        )
        for activation_id, agent_id, digest, timestamp in (
            ("activation-first", "agent-1", "sha256:first", created_at),
            ("activation-later", "agent-1", "sha256:later", "2026-07-24T00:01:00+00:00"),
            ("activation-child", "agent-2", "sha256:child", created_at),
        ):
            connection.execute(
                text(
                    "INSERT INTO runtime_skill_activations "
                    "(record_id, root_session_id, agent_id, skill_name, skill_digest, data) "
                    "VALUES (:record_id, 'root-1', :agent_id, 'research', :digest, :data)"
                ),
                {
                    "record_id": activation_id,
                    "agent_id": agent_id,
                    "digest": digest,
                    "data": json.dumps(
                        {
                            "activation_id": activation_id,
                            "root_session_id": "root-1",
                            "agent_id": agent_id,
                            "skill_name": "research",
                            "skill_digest": digest,
                            "snapshot": {"path": f"/repo/{digest}/SKILL.md"},
                            "registered_hook_ids": [],
                            "allowed_tools": ["WebSearch"],
                            "created_at": timestamp,
                            "updated_at": timestamp,
                        }
                    ),
                },
            )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    store = SQLAlchemyStateStore(factory)
    root_records = store.skill_activations.list("root-1", agent_id="agent-1")
    assert len(root_records) == 1
    assert root_records[0].activation_id == "activation-first"
    assert root_records[0].status.value == "active"
    assert root_records[0].revision == 1
    assert (
        store.skill_activations.list("root-1", agent_id="agent-2")[0].status.value
        == "active"
    )

    restarted = SQLAlchemyStateStore(factory)
    assert restarted.skill_activations.list("root-1", agent_id="agent-1") == root_records
    with engine.connect() as connection:
        columns = {
            row.name
            for row in connection.execute(
                text("PRAGMA table_info(runtime_skill_activations)")
            )
        }
        assert {"status", "revision"} <= columns
        assert connection.execute(
            text(
                "SELECT version FROM runtime_schema_migrations "
                "WHERE name = 'runtime_skill_activations'"
            )
        ).scalar_one() == 1


def test_legacy_session_snapshot_and_event_state_recover_without_permission_snapshot(
    store: SQLAlchemyStateStore,
    session_factory: sessionmaker[Session],
) -> None:
    runtime = SessionRuntime("legacy-recovery", store)
    runtime.checkpoint()
    runtime.append_event(EventType.USER_MESSAGE, {"content": "after snapshot"})

    with session_factory() as db, db.begin():
        session_row = db.get(RuntimeSession, runtime.session_id)
        assert session_row is not None
        legacy_session = dict(session_row.state)
        legacy_session.pop("permissionScopeSnapshots")
        session_row.state = legacy_session

        snapshot_row = db.get(RuntimeSnapshot, (runtime.session_id, 0))
        assert snapshot_row is not None
        legacy_snapshot = dict(snapshot_row.state)
        legacy_snapshot.pop("permissionScopeSnapshots")
        snapshot_row.state = legacy_snapshot
        encoded = json.dumps(legacy_snapshot, sort_keys=True, separators=(",", ":"))
        snapshot_row.checksum = hashlib.sha256(encoded.encode()).hexdigest()

        event_row = db.scalar(
            select(RuntimeEvent).where(RuntimeEvent.session_id == runtime.session_id)
        )
        assert event_row is not None
        legacy_payload = dict(event_row.payload)
        legacy_event_state = dict(legacy_payload["state"])
        legacy_event_state.pop("permissionScopeSnapshots")
        legacy_payload["state"] = legacy_event_state
        event_row.payload = legacy_payload

    recovered = SessionRuntime.recover(runtime.session_id, store)
    assert recovered.state.health.value == "ready"
    assert recovered.state.to_dict()["permissionScopeSnapshots"] == {
        "session": [],
        "cliArg": [],
    }
    assert recovered.events()[-1].payload["content"] == "after snapshot"


def test_interrupted_team_member_restart_clears_terminal_timestamp(
    store: SQLAlchemyStateStore,
) -> None:
    now = datetime.now(timezone.utc)
    member = store.team_members.create(
        TeamMemberRecord(
            member_id="restart-member",
            team_id="team-1",
            root_session_id="root-1",
            agent_id="agent-1",
            name="analyst",
            agent_type="worker",
            role="research",
            owner_token="dead-runtime",
            lease_expires_at=now - timedelta(seconds=1),
        )
    )
    member = store.team_members.transition(
        member.member_id, TeamMemberStatus.RUNNING, member.revision
    )
    interrupted = store.team_members.reconcile(
        "root-1", live_owner_tokens=set(), now=now, observer=False
    )[0]
    assert interrupted.stopped_at is not None

    restarted = store.team_members.transition(
        interrupted.member_id,
        TeamMemberStatus.STARTING,
        interrupted.revision,
        owner_token="new-runtime",
        lease_expires_at=now + timedelta(minutes=1),
    )
    assert restarted.stopped_at is None


def test_recovery_tables_have_root_status_composite_indexes_and_query_plans(
    session_factory: sessionmaker[Session],
) -> None:
    expected = {
        "runtime_permission_requests": "ix_runtime_permission_requests_root_status",
        "runtime_approved_tool_executions": "ix_runtime_approved_executions_root_status",
        "runtime_hook_invocations": "ix_runtime_hook_invocations_root_status",
        "runtime_execution_tasks": "ix_runtime_execution_tasks_root_status",
        "runtime_teams": "ix_runtime_teams_root_status",
        "runtime_team_members": "ix_runtime_team_members_root_status",
    }
    bind = session_factory.kw["bind"]
    with bind.connect() as connection:
        for table_name, index_name in expected.items():
            indexes = {
                row.name
                for row in connection.execute(text(f"PRAGMA index_list({table_name})"))
            }
            assert index_name in indexes
            columns = [
                row.name
                for row in connection.execute(text(f"PRAGMA index_info({index_name})"))
            ]
            assert columns == ["root_session_id", "status"]
            plan = connection.execute(
                text(
                    f"EXPLAIN QUERY PLAN SELECT record_id FROM {table_name} "
                    "WHERE root_session_id = :root AND status = :status"
                ),
                {"root": "root-1", "status": "running"},
            ).all()
            assert any(index_name in str(row.detail) for row in plan)
