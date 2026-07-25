from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from harness.context import PermissionMode, RuntimeContext
from state_core import PermissionRuleScope
from state_core.sqlalchemy_store import Base, SQLAlchemyStateStore
from tools.base import Tool, ToolResult


class Destructive(Tool):
    name = "Bash"
    description = "test"

    async def execute(self, input_data):
        return ToolResult.ok({})

    def is_destructive(self):
        return True


def _store(tmp_path: Path) -> SQLAlchemyStateStore:
    engine = create_engine(f"sqlite:///{tmp_path / 'rules.db'}")
    Base.metadata.create_all(engine)
    return SQLAlchemyStateStore(sessionmaker(bind=engine, expire_on_commit=False))


def test_all_node_permission_updates_and_scope_persistence(tmp_path: Path) -> None:
    from harness.permissions import PermissionRuleService

    store = _store(tmp_path)
    service = PermissionRuleService(store.permission_rules, root_session_id="root")
    service.apply_updates(
        [
            {
                "type": "addRules",
                "rules": ["Bash(echo *)", "Read"],
                "behavior": "allow",
                "destination": "userSettings",
            },
            {
                "type": "replaceRules",
                "rules": ["Bash(rm *)"],
                "behavior": "deny",
                "destination": "projectSettings",
            },
            {
                "type": "removeRules",
                "rules": ["Read"],
                "behavior": "allow",
                "destination": "userSettings",
            },
            {"type": "setMode", "mode": "plan", "destination": "localSettings"},
            {"type": "addDirectories", "directories": [str(tmp_path)], "destination": "session"},
            {"type": "removeDirectories", "directories": [str(tmp_path)], "destination": "session"},
            {"type": "addRules", "rules": ["Glob"], "behavior": "allow", "destination": "cliArg"},
        ]
    )

    durable = store.permission_rules.list("root")
    assert any(
        item.scope is PermissionRuleScope.USER_SETTINGS and item.revoked_at is None
        for item in durable
    )
    assert any(
        item.scope is PermissionRuleScope.PROJECT_SETTINGS and item.behavior == "deny"
        for item in durable
    )
    assert any(
        item.scope is PermissionRuleScope.LOCAL_SETTINGS and item.mode == "plan" for item in durable
    )
    assert service.snapshot("session") == ()
    assert service.snapshot("cliArg")[0]["rule"] == "Glob"


@pytest.mark.asyncio
async def test_domain_outcomes_deny_wins_and_headless_hooks_run_before_fail_closed(
    tmp_path: Path,
) -> None:
    from harness.permissions import Allow, Deny, PermissionPolicy, PermissionRuleService

    store = _store(tmp_path)
    rules = PermissionRuleService(store.permission_rules, root_session_id="root")
    rules.apply_updates(
        [
            {
                "type": "addRules",
                "rules": ["Bash"],
                "behavior": "allow",
                "destination": "userSettings",
            },
            {
                "type": "addRules",
                "rules": ["Bash"],
                "behavior": "deny",
                "destination": "projectSettings",
            },
        ]
    )
    policy = PermissionPolicy(tmp_path, rule_service=rules)
    context = RuntimeContext(workspace_root=tmp_path, permission_mode=PermissionMode.DEFAULT)
    outcome = await policy.authorize_outcome(Destructive(), "Bash", {"command": "echo ok"}, context)
    assert isinstance(outcome, Deny)

    hook_calls = 0

    async def hook(**kwargs):
        nonlocal hook_calls
        hook_calls += 1
        return {"permission_decision": "allow", "input_patch": {"command": "echo safe"}}

    empty_policy = PermissionPolicy(tmp_path)
    allowed = await empty_policy.authorize_outcome(
        Destructive(), "Bash", {"command": "echo ok"}, context, permission_hook=hook
    )
    assert isinstance(allowed, Allow)
    assert allowed.effective_input == {"command": "echo safe"}
    assert hook_calls == 1

    async def undecided(**kwargs):
        nonlocal hook_calls
        hook_calls += 1
        return {}

    denied = await empty_policy.authorize_outcome(
        Destructive(), "Bash", {"command": "echo ok"}, context, permission_hook=undecided
    )
    assert isinstance(denied, Deny)
    assert "no approval" in denied.reason.lower()
    assert hook_calls == 2


@pytest.mark.asyncio
async def test_hook_modified_input_is_boundary_checked_and_rule_mode_is_effective(
    tmp_path: Path,
) -> None:
    from harness.permissions import Deny, PermissionPolicy, PermissionRuleService

    store = _store(tmp_path)
    rules = PermissionRuleService(store.permission_rules, root_session_id="root")
    rules.apply_updates([{"type": "setMode", "mode": "dontAsk", "destination": "localSettings"}])
    policy = PermissionPolicy(tmp_path, rule_service=rules)
    context = RuntimeContext(workspace_root=tmp_path)

    async def escape(**kwargs):
        return {
            "permission_decision": "allow",
            "input_patch": {"working_dir": str(tmp_path.parent)},
        }

    escaped = await PermissionPolicy(tmp_path).authorize_outcome(
        Destructive(),
        "Bash",
        {"command": "echo ok"},
        context,
        permission_hook=escape,
    )
    mode_denied = await policy.authorize_outcome(
        Destructive(), "Bash", {"command": "echo ok"}, context
    )

    assert isinstance(escaped, Deny)
    assert "outside" in escaped.reason
    assert isinstance(mode_denied, Deny)
    assert "dontAsk" in mode_denied.reason


def test_additional_directories_expand_and_remove_workspace_boundary(tmp_path: Path) -> None:
    from harness.permissions import PermissionDecision, PermissionPolicy, PermissionRuleService

    store = _store(tmp_path)
    external = tmp_path.parent / "approved-external"
    external.mkdir(exist_ok=True)
    rules = PermissionRuleService(store.permission_rules, root_session_id="root")
    rules.apply_updates(
        [
            {
                "type": "addDirectories",
                "directories": [str(external)],
                "destination": "projectSettings",
            }
        ]
    )
    policy = PermissionPolicy(tmp_path, rule_service=rules)
    context = RuntimeContext(workspace_root=tmp_path)

    allowed, _ = policy.check(
        Destructive(), "Bash", {"command": "pwd", "working_dir": str(external)}, context
    )
    rules.apply_updates(
        [
            {
                "type": "removeDirectories",
                "directories": [str(external)],
                "destination": "projectSettings",
            }
        ]
    )
    denied, reason = policy.check(
        Destructive(), "Bash", {"command": "pwd", "working_dir": str(external)}, context
    )

    assert allowed is PermissionDecision.ASK
    assert denied is PermissionDecision.DENY
    assert "outside" in reason


def test_skill_allowed_tools_are_agent_scoped_permission_overlays(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from harness.permissions import PermissionDecision, PermissionPolicy
    from state_core import SkillActivationRecord

    store = _store(tmp_path)
    store.skill_activations.create(
        SkillActivationRecord(
            activation_id="skill-child-a",
            root_session_id="root",
            agent_id="child-a",
            skill_name="shell-skill",
            skill_digest="digest",
            snapshot={"name": "shell-skill"},
            allowed_tools=("Bash",),
        )
    )
    harness = SimpleNamespace(store=store, root_session_id="root")
    policy = PermissionPolicy(tmp_path)

    def context(agent_id):
        return RuntimeContext(
            session_id="root",
            workspace_root=tmp_path,
            metadata={"session_harness": harness, "agent_id": agent_id},
        )

    child_a, _ = policy.check(Destructive(), "Bash", {"command": "echo ok"}, context("child-a"))
    child_b, _ = policy.check(Destructive(), "Bash", {"command": "echo ok"}, context("child-b"))
    root, _ = policy.check(Destructive(), "Bash", {"command": "echo ok"}, context(None))

    assert child_a is PermissionDecision.ALLOW
    assert child_b is PermissionDecision.ASK
    assert root is PermissionDecision.ASK
