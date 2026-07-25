"""Permission domain outcomes, Node-compatible rules, and legacy authorization adapter."""

from __future__ import annotations

import fnmatch
import inspect
import shlex
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from state_core import (
    PermissionRuleKind,
    PermissionRuleRecord,
    PermissionRuleScope,
    SkillActivationStatus,
)
from tools.base import Tool, tool_flag

from .context import PermissionMode, RuntimeContext


class PermissionDecision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


@dataclass(frozen=True)
class PermissionRequest:
    tool_name: str
    input_data: dict[str, Any]
    reason: str
    session_id: Optional[str] = None


@dataclass(frozen=True)
class Allow:
    effective_input: dict[str, Any]
    permission_updates: tuple[Mapping[str, Any], ...] = ()


@dataclass(frozen=True)
class Deny:
    reason: str
    interrupt: bool = False


@dataclass(frozen=True)
class ApprovalRequired:
    request_id: str
    deadline: Any = None


PermissionOutcome = Allow | Deny | ApprovalRequired


def _rule_string(rule: Any) -> str:
    if isinstance(rule, str) and rule.strip():
        return rule.strip()
    if isinstance(rule, Mapping):
        tool_name = rule.get("toolName", rule.get("tool_name"))
        content = rule.get("ruleContent", rule.get("rule_content"))
        if not isinstance(tool_name, str) or not tool_name:
            raise ValueError("permission rule mappings require toolName")
        return tool_name if content is None else f"{tool_name}({content})"
    raise ValueError("permission rules must be strings or rule mappings")


class PermissionRuleService:
    """Apply Node permission update operations across durable and snapshot scopes."""

    _DURABLE = frozenset(
        {
            PermissionRuleScope.USER_SETTINGS,
            PermissionRuleScope.PROJECT_SETTINGS,
            PermissionRuleScope.LOCAL_SETTINGS,
        }
    )

    def __init__(
        self,
        repository: Any,
        *,
        root_session_id: str,
        snapshots: Mapping[str, Iterable[Mapping[str, Any]]] | None = None,
        snapshot_writer: Any = None,
    ) -> None:
        self._repository = repository
        self._root_session_id = root_session_id
        snapshots = snapshots or {}
        self._snapshots: dict[PermissionRuleScope, list[dict[str, Any]]] = {
            scope: [dict(item) for item in snapshots.get(scope.value, ())]
            for scope in (PermissionRuleScope.SESSION, PermissionRuleScope.CLI_ARG)
        }
        self._snapshot_writer = snapshot_writer

    def validate_updates(self, updates: Iterable[Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
        normalized: list[dict[str, Any]] = []
        for raw in updates:
            update = dict(raw)
            operation = update.get("type", update.get("operation"))
            if operation not in {
                "addRules",
                "replaceRules",
                "removeRules",
                "setMode",
                "addDirectories",
                "removeDirectories",
            }:
                raise ValueError(f"unsupported permission update {operation!r}")
            try:
                scope = PermissionRuleScope(update.get("destination"))
            except (TypeError, ValueError) as exc:
                raise ValueError("permission update requires a valid destination") from exc
            item: dict[str, Any] = {"type": operation, "destination": scope.value}
            if operation in {"addRules", "replaceRules", "removeRules"}:
                behavior = update.get("behavior")
                if behavior not in {"allow", "deny", "ask"}:
                    raise ValueError("rule updates require allow, deny, or ask behavior")
                rules = update.get("rules")
                if not isinstance(rules, (list, tuple)):
                    raise ValueError("rule updates require a rules array")
                item.update(behavior=behavior, rules=[_rule_string(rule) for rule in rules])
            elif operation == "setMode":
                mode = update.get("mode")
                if not isinstance(mode, str) or not mode:
                    raise ValueError("setMode requires mode")
                item["mode"] = mode
            else:
                directories = update.get("directories")
                if not isinstance(directories, (list, tuple)) or not all(
                    isinstance(directory, str) and directory for directory in directories
                ):
                    raise ValueError("directory updates require a directories array")
                item["directories"] = [
                    str(Path(directory).expanduser().resolve()) for directory in directories
                ]
            normalized.append(item)
        return tuple(normalized)

    def apply_updates(
        self, updates: Iterable[Mapping[str, Any]]
    ) -> tuple[PermissionRuleRecord, ...]:
        normalized = self.validate_updates(updates)
        changed: list[PermissionRuleRecord] = []
        for update in normalized:
            scope = PermissionRuleScope(update["destination"])
            if scope in self._DURABLE:
                changed.extend(self._apply_durable(scope, update))
            else:
                self._apply_snapshot(scope, update)
        self._write_snapshots()
        return tuple(changed)

    def snapshot(self, scope: PermissionRuleScope | str) -> tuple[dict[str, Any], ...]:
        normalized = PermissionRuleScope(scope)
        if normalized in self._DURABLE:
            return tuple(self._record_dict(record) for record in self._active(normalized))
        return tuple(dict(item) for item in self._snapshots[normalized])

    def active_records(self) -> tuple[PermissionRuleRecord, ...]:
        return tuple(
            record
            for record in self._repository.list(self._root_session_id)
            if record.revoked_at is None
        )

    def decision(self, tool_name: str, input_data: Mapping[str, Any]) -> PermissionDecision | None:
        matches: set[str] = set()
        records = list(self.active_records())
        snapshot_items = [
            item
            for scope in (PermissionRuleScope.SESSION, PermissionRuleScope.CLI_ARG)
            for item in self._snapshots[scope]
        ]
        for record in records:
            if (
                record.kind is PermissionRuleKind.RULE
                and record.rule is not None
                and self._matches(record.rule, tool_name, input_data)
            ):
                matches.add(str(record.behavior))
        for item in snapshot_items:
            if item.get("kind") == "rule" and self._matches(
                str(item.get("rule")), tool_name, input_data
            ):
                matches.add(str(item.get("behavior")))
        if "deny" in matches:
            return PermissionDecision.DENY
        if "ask" in matches:
            return PermissionDecision.ASK
        if "allow" in matches:
            return PermissionDecision.ALLOW
        return None

    def current_mode(self) -> str | None:
        durable = [
            record for record in self.active_records() if record.kind is PermissionRuleKind.MODE
        ]
        snapshots = [
            item
            for scope in (PermissionRuleScope.SESSION, PermissionRuleScope.CLI_ARG)
            for item in self._snapshots[scope]
            if item.get("kind") == "mode"
        ]
        if snapshots:
            return str(snapshots[-1]["mode"])
        return durable[-1].mode if durable else None

    def directories(self) -> tuple[Path, ...]:
        durable = [
            Path(record.directory).resolve()
            for record in self.active_records()
            if record.kind is PermissionRuleKind.DIRECTORY and record.directory is not None
        ]
        snapshots = [
            Path(str(item["directory"])).resolve()
            for scope in (PermissionRuleScope.SESSION, PermissionRuleScope.CLI_ARG)
            for item in self._snapshots[scope]
            if item.get("kind") == "directory" and item.get("directory") is not None
        ]
        return tuple(dict.fromkeys((*durable, *snapshots)))

    def _apply_durable(
        self, scope: PermissionRuleScope, update: Mapping[str, Any]
    ) -> list[PermissionRuleRecord]:
        operation = update["type"]
        changed: list[PermissionRuleRecord] = []
        if operation in {"replaceRules", "removeRules"}:
            targets = set(update["rules"])
            for record in self._active(scope):
                should_revoke = (
                    record.kind is PermissionRuleKind.RULE
                    and record.behavior == update["behavior"]
                    and (operation == "replaceRules" or record.rule in targets)
                )
                if should_revoke:
                    changed.append(self._repository.revoke(record.rule_id, record.revision))
        elif operation in {"setMode", "removeDirectories"}:
            targets = set(update.get("directories", ()))
            kind = (
                PermissionRuleKind.MODE if operation == "setMode" else PermissionRuleKind.DIRECTORY
            )
            for record in self._active(scope):
                if record.kind is kind and (operation == "setMode" or record.directory in targets):
                    changed.append(self._repository.revoke(record.rule_id, record.revision))
        if operation in {"addRules", "replaceRules"}:
            for rule in update["rules"]:
                changed.append(
                    self._create(
                        scope, PermissionRuleKind.RULE, behavior=update["behavior"], rule=rule
                    )
                )
        elif operation == "setMode":
            changed.append(self._create(scope, PermissionRuleKind.MODE, mode=update["mode"]))
        elif operation == "addDirectories":
            for directory in update["directories"]:
                changed.append(
                    self._create(scope, PermissionRuleKind.DIRECTORY, directory=directory)
                )
        return changed

    def _apply_snapshot(self, scope: PermissionRuleScope, update: Mapping[str, Any]) -> None:
        items = self._snapshots[scope]
        operation = update["type"]
        if operation in {"replaceRules", "removeRules"}:
            targets = set(update["rules"])
            items[:] = [
                item
                for item in items
                if not (
                    item.get("kind") == "rule"
                    and item.get("behavior") == update["behavior"]
                    and (operation == "replaceRules" or item.get("rule") in targets)
                )
            ]
        elif operation == "setMode":
            items[:] = [item for item in items if item.get("kind") != "mode"]
        elif operation == "removeDirectories":
            targets = set(update["directories"])
            items[:] = [
                item
                for item in items
                if not (item.get("kind") == "directory" and item.get("directory") in targets)
            ]
        if operation in {"addRules", "replaceRules"}:
            items.extend(
                {"kind": "rule", "behavior": update["behavior"], "rule": rule}
                for rule in update["rules"]
            )
        elif operation == "setMode":
            items.append({"kind": "mode", "mode": update["mode"]})
        elif operation == "addDirectories":
            for directory in update["directories"]:
                if not any(
                    item.get("kind") == "directory" and item.get("directory") == directory
                    for item in items
                ):
                    items.append({"kind": "directory", "directory": directory})

    def _create(self, scope: PermissionRuleScope, kind: PermissionRuleKind, **values: Any):
        return self._repository.create(
            PermissionRuleRecord(
                rule_id=f"permission_rule_{uuid.uuid4().hex}",
                root_session_id=self._root_session_id,
                kind=kind,
                scope=scope,
                source="permission_update",
                **values,
            )
        )

    def _active(self, scope: PermissionRuleScope) -> list[PermissionRuleRecord]:
        return [
            record
            for record in self._repository.list(self._root_session_id)
            if record.scope is scope and record.revoked_at is None
        ]

    def _write_snapshots(self) -> None:
        if self._snapshot_writer is None:
            return
        for scope, items in self._snapshots.items():
            self._snapshot_writer(scope.value, tuple(dict(item) for item in items))

    @staticmethod
    def _record_dict(record: PermissionRuleRecord) -> dict[str, Any]:
        return {
            "kind": record.kind.value,
            "behavior": record.behavior,
            "rule": record.rule,
            "directory": record.directory,
            "mode": record.mode,
        }

    @staticmethod
    def _matches(rule: str, tool_name: str, input_data: Mapping[str, Any]) -> bool:
        if "(" not in rule or not rule.endswith(")"):
            return fnmatch.fnmatchcase(tool_name, rule)
        expected_tool, pattern = rule[:-1].split("(", 1)
        if not fnmatch.fnmatchcase(tool_name, expected_tool):
            return False
        content = input_data.get("command")
        if content is None:
            content = input_data.get("path", input_data.get("file_path", ""))
        return fnmatch.fnmatchcase(str(content), pattern)


class PermissionPolicy:
    PLAN_ALLOWED_TOOLS = {
        "read_file",
        "glob",
        "grep",
        "bash",
        "enter_plan_mode",
        "exit_plan_mode",
        "ask_user_question",
    }
    PLAN_BASH_WRITE_MARKERS = (
        "touch ",
        "mkdir ",
        "rm ",
        "cp ",
        "mv ",
        ">",
        "git add",
        "git commit",
        "git push",
    )

    def __init__(
        self,
        workspace_root: Optional[Path] = None,
        *,
        rule_service: PermissionRuleService | None = None,
    ) -> None:
        self.workspace_root = workspace_root.resolve() if workspace_root else None
        self.rule_service = rule_service

    def check(
        self,
        tool: Tool,
        tool_name: str,
        input_data: dict[str, Any],
        context: RuntimeContext,
    ) -> tuple[PermissionDecision, str]:
        boundary_error = self._check_workspace_boundary(
            tool_name,
            input_data,
            context.workspace_root,
            self.rule_service.directories() if self.rule_service else (),
        )
        if boundary_error:
            return PermissionDecision.DENY, boundary_error
        configured_mode = self.rule_service.current_mode() if self.rule_service else None
        effective_mode = configured_mode or context.permission_mode.value
        if effective_mode == PermissionMode.PLAN.value:
            if tool_name.casefold() not in {name.casefold() for name in self.PLAN_ALLOWED_TOOLS}:
                return PermissionDecision.DENY, "Only planning tools are allowed in plan mode"
            if tool_name.casefold() == "bash":
                command = str(input_data.get("command", "")).lower()
                if any(marker in command for marker in self.PLAN_BASH_WRITE_MARKERS):
                    return PermissionDecision.DENY, "Bash command may mutate state in plan mode"
        if effective_mode in {PermissionMode.BYPASS.value, "bypassPermissions"}:
            return PermissionDecision.ALLOW, "bypass mode"
        if self.rule_service is not None:
            rule_decision = self.rule_service.decision(tool_name, input_data)
            if rule_decision is not None:
                return rule_decision, f"matched {rule_decision.value} permission rule"
        if self._skill_allows(tool_name, context):
            return PermissionDecision.ALLOW, "allowed by active agent skill"
        if tool_flag(tool, "requires_confirmation") or tool_flag(tool, "is_destructive"):
            if effective_mode == "dontAsk":
                return PermissionDecision.DENY, "Current permission mode (dontAsk) denies prompts"
            return PermissionDecision.ASK, "tool is destructive or requires confirmation"
        return PermissionDecision.ALLOW, "read-only tool"

    @staticmethod
    def _skill_allows(tool_name: str, context: RuntimeContext) -> bool:
        harness = context.metadata.get("session_harness")
        agent_id = context.metadata.get("agent_id")
        if harness is None or not isinstance(agent_id, str) or not agent_id:
            return False
        store = getattr(harness, "store", None)
        root_session_id = getattr(harness, "root_session_id", None)
        activations = getattr(store, "skill_activations", None)
        if activations is None or not isinstance(root_session_id, str):
            return False
        canonical = tool_name.casefold()
        return any(
            activation.status is SkillActivationStatus.ACTIVE
            and any(candidate.casefold() == canonical for candidate in activation.allowed_tools)
            for activation in activations.list(root_session_id, agent_id=agent_id)
        )

    async def authorize_outcome(
        self,
        tool: Tool,
        tool_name: str,
        input_data: dict[str, Any],
        context: RuntimeContext,
        *,
        permission_hook: Any = None,
        approval_service: Any = None,
        tool_call_id: str | None = None,
        policy_revision: int = 0,
    ) -> PermissionOutcome:
        decision, reason = self.check(tool, tool_name, input_data, context)
        if decision is PermissionDecision.ALLOW:
            return Allow(dict(input_data))
        if decision is PermissionDecision.DENY:
            return Deny(reason)
        effective = dict(input_data)
        updates: tuple[Mapping[str, Any], ...] = ()
        if permission_hook is not None:
            hook_result = permission_hook(
                tool_name=tool_name,
                tool_input=dict(input_data),
                context=context,
                reason=reason,
            )
            if inspect.isawaitable(hook_result):
                hook_result = await hook_result
            if not isinstance(hook_result, Mapping):
                hook_result = {
                    "permission_decision": getattr(hook_result, "permission_decision", None),
                    "input_patch": getattr(hook_result, "input_patch", {}),
                    "permission_updates": getattr(hook_result, "permission_updates", ()),
                    "reason": getattr(hook_result, "reason", None),
                }
            hook_decision = hook_result.get("permission_decision")
            patch = hook_result.get("input_patch", hook_result.get("updated_input"))
            if isinstance(patch, Mapping):
                effective.update(patch)
                boundary_error = self._check_workspace_boundary(
                    tool_name,
                    effective,
                    context.workspace_root,
                    self.rule_service.directories() if self.rule_service else (),
                )
                if boundary_error:
                    return Deny(boundary_error)
            updates = tuple(hook_result.get("permission_updates", ()))
            if hook_decision == "deny":
                return Deny(str(hook_result.get("reason") or "permission denied by hook"))
            if hook_decision == "allow":
                if updates and self.rule_service is not None:
                    self.rule_service.apply_updates(updates)
                return Allow(effective, updates)
        if approval_service is None:
            return Deny("Approval is required but no approval adapter is configured")
        request = approval_service.create(
            agent_id=str(context.metadata.get("agent_id") or context.session_id or "root"),
            tool_call_id=tool_call_id or f"tool_{uuid.uuid4().hex}",
            tool_name=tool_name,
            original_input=input_data,
            effective_input=effective,
            reason=reason,
            permission_mode=context.permission_mode.value,
            policy_revision=policy_revision,
        )
        return ApprovalRequired(request.request_id, request.deadline_at)

    async def authorize(
        self,
        tool: Tool,
        tool_name: str,
        input_data: dict[str, Any],
        context: RuntimeContext,
    ) -> tuple[bool, str]:
        decision, reason = self.check(tool, tool_name, input_data, context)
        if decision is PermissionDecision.ALLOW:
            return True, reason
        if decision is PermissionDecision.DENY:
            return False, reason
        if context.approval_callback is None:
            return False, "Approval is required but no approval callback is configured"
        request = PermissionRequest(tool_name, input_data, reason, context.session_id)
        result = context.approval_callback(request)
        approved = await result if inspect.isawaitable(result) else result
        return bool(approved), "approved" if approved else "approval denied"

    @staticmethod
    def _check_workspace_boundary(
        tool_name: str,
        input_data: dict[str, Any],
        workspace_root: Optional[Path],
        additional_roots: Iterable[Path] = (),
    ) -> Optional[str]:
        if workspace_root is None:
            return None
        path_keys = {
            "path",
            "file_path",
            "notebook_path",
            "working_dir",
            "working_directory",
            "cwd",
            "directory",
            "root_dir",
        }
        root = workspace_root.resolve()
        allowed_roots = (root, *(Path(item).resolve() for item in additional_roots))

        def is_allowed(candidate: Path) -> bool:
            for allowed_root in allowed_roots:
                try:
                    candidate.relative_to(allowed_root)
                    return True
                except ValueError:
                    continue
            return False

        for key, value in input_data.items():
            normalized_key = key.lower()
            if not (
                normalized_key in path_keys
                or normalized_key.endswith("_path")
                or normalized_key.endswith("_dir")
            ) or not isinstance(value, str):
                continue
            candidate = Path(value).expanduser()
            if not candidate.is_absolute():
                candidate = root / candidate
            if not is_allowed(candidate.resolve()):
                return f"Path for '{key}' is outside the workspace boundary"
        if tool_name.casefold() == "bash":
            command = input_data.get("command")
            if isinstance(command, str):
                try:
                    tokens = shlex.split(command)
                except ValueError:
                    return "Bash command could not be safely parsed"
                for token in tokens:
                    candidate_text = token.lstrip("<>")
                    if not candidate_text.startswith(("/", "~/", "../", "./")):
                        continue
                    candidate = Path(candidate_text).expanduser()
                    if not candidate.is_absolute():
                        candidate = root / candidate
                    if not is_allowed(candidate.resolve()):
                        return "Bash command references a path outside the workspace boundary"
        return None
