from __future__ import annotations

import pytest

from services.llm_service import ChatCompletionRequest, LLMService, Message


def _tool_call(call_id: str, name: str, arguments: str) -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


def test_message_to_anthropic_converts_assistant_tool_calls_to_content_blocks() -> None:
    message = Message(
        role="assistant",
        content="I will inspect both files.",
        tool_calls=[
            _tool_call("call-1", "read_file", '{"path":"one.py"}'),
            _tool_call("call-2", "read_file", '{"path":"two.py"}'),
        ],
    )

    assert message.to_anthropic() == {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "I will inspect both files."},
            {
                "type": "tool_use",
                "id": "call-1",
                "name": "read_file",
                "input": {"path": "one.py"},
            },
            {
                "type": "tool_use",
                "id": "call-2",
                "name": "read_file",
                "input": {"path": "two.py"},
            },
        ],
    }


def test_message_to_anthropic_converts_tool_message_to_tool_result() -> None:
    message = Message(role="tool", content="file contents", tool_call_id="call-1")

    assert message.to_anthropic() == {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": "call-1",
                "content": "file contents",
            }
        ],
    }


def test_anthropic_request_merges_tool_results_into_one_user_turn() -> None:
    request = ChatCompletionRequest(
        messages=[
            Message(role="system", content="Use tools carefully."),
            Message(role="user", content="Inspect both files."),
            Message(
                role="assistant",
                content="",
                tool_calls=[
                    _tool_call("call-1", "read_file", '{"path":"one.py"}'),
                    _tool_call("call-2", "read_file", '{"path":"two.py"}'),
                ],
            ),
            Message(role="tool", content="one", tool_call_id="call-1"),
            Message(role="tool", content="two", tool_call_id="call-2"),
            Message(role="user", content="Compare them."),
        ],
    )

    kwargs = LLMService()._build_anthropic_create_kwargs(request)

    assert kwargs["system"] == "Use tools carefully."
    assert kwargs["messages"] == [
        {"role": "user", "content": "Inspect both files."},
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "call-1",
                    "name": "read_file",
                    "input": {"path": "one.py"},
                },
                {
                    "type": "tool_use",
                    "id": "call-2",
                    "name": "read_file",
                    "input": {"path": "two.py"},
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "call-1",
                    "content": "one",
                },
                {
                    "type": "tool_result",
                    "tool_use_id": "call-2",
                    "content": "two",
                },
                {"type": "text", "text": "Compare them."},
            ],
        },
    ]


@pytest.mark.parametrize(
    "arguments",
    [
        pytest.param("not-json", id="invalid-json"),
        pytest.param("[]", id="non-object"),
        pytest.param('{"value":NaN}', id="nan"),
        pytest.param('{"value":Infinity}', id="infinity"),
        pytest.param('{"value":-Infinity}', id="negative-infinity"),
        pytest.param('{"value":1e400}', id="overflow-to-infinity"),
    ],
)
def test_message_to_anthropic_rejects_invalid_tool_arguments(arguments: str) -> None:
    message = Message(
        role="assistant",
        content="",
        tool_calls=[_tool_call("call-1", "inspect", arguments)],
    )

    with pytest.raises(ValueError, match="tool call arguments must be a finite JSON object"):
        message.to_anthropic()
