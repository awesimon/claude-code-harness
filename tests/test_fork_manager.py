from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from agents.fork import ForkSubagentManager, get_fork_manager
from agents.types import AgentIsolationMode
from harness.agents import AgentNotFound


class Scheduler:
    def __init__(self) -> None:
        self.requests = []
        self.records = {}

    async def spawn(self, request):
        self.requests.append(request)
        record = SimpleNamespace(
            agent_id="agent-general-purpose-1",
            root_session_id="root",
            status=SimpleNamespace(value="running"),
            definition_snapshot={
                "metadata": copy.deepcopy(request.definition_metadata),
                "initial_messages": copy.deepcopy(request.initial_messages),
                "isolation": request.isolation.value if request.isolation else None,
            },
        )
        self.records[record.agent_id] = record
        return record

    def status(self, agent_id):
        return self.records.get(agent_id)


@pytest.mark.asyncio
async def test_fork_manager_delegates_to_scheduler_without_global_registry() -> None:
    scheduler = Scheduler()
    manager = ForkSubagentManager(scheduler)

    fork_id = await manager.create_fork(
        parent_session_id="parent",
        directive="Inspect the code",
        assistant_message={"role": "assistant", "content": []},
        parent_messages=[{"role": "user", "content": "Parent context"}],
        isolate_worktree=True,
    )

    assert fork_id == "agent-general-purpose-1"
    request = scheduler.requests[0]
    assert request.background
    assert request.isolation is AgentIsolationMode.WORKTREE
    assert request.initial_messages[0] == {
        "role": "user",
        "content": "Parent context",
    }
    assert "FORK DIRECTIVE: Inspect the code" in str(request.initial_messages[-1])
    assert request.definition_metadata == {
        "fork": {"parent_session_id": "parent"}
    }
    fork = manager.get_fork(fork_id)
    assert fork is not None
    assert fork["status"] == "running"
    assert fork["messages"] == request.initial_messages
    assert fork["isolate_worktree"] is True
    assert get_fork_manager(scheduler) is not get_fork_manager(scheduler)


def test_compatibility_getter_is_singleton_and_missing_forks_are_absent() -> None:
    class RaisingScheduler:
        def status(self, agent_id):
            raise AgentNotFound(agent_id)

    assert get_fork_manager() is get_fork_manager()
    manager = ForkSubagentManager(RaisingScheduler())
    assert manager.get_fork("missing") is None
    assert manager.update_fork_status("missing", "cancelled") is None
    assert manager.cleanup_fork("missing") is None
