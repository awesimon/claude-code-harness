import unittest
from pathlib import Path

from query_engine import QueryEngine
from services.llm_service import ChatCompletionResponse


class RecordingLLMService:
    def __init__(self):
        self.requests = []

    async def chat_completion(self, request):
        self.requests.append(request)
        return ChatCompletionResponse(
            id="response-1",
            model=request.model or "test-model",
            content="done",
            finish_reason="stop",
        )


class QueryEngineRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_tool_schemas_use_canonical_names_and_normalized_parameters(self):
        engine = QueryEngine(
            llm_service=RecordingLLMService(),
            enable_error_recovery=False,
        )

        schemas = engine._build_tools_schema()

        names = [item["function"]["name"] for item in schemas]
        self.assertIn("read_file", names)
        self.assertIn("agent", names)
        self.assertNotIn("Read", names)
        self.assertNotIn("Agent", names)
        self.assertTrue(
            all(item["function"]["parameters"].get("type") == "object" for item in schemas)
        )

    async def test_plan_mode_keeps_canonical_exploration_tools(self):
        engine = QueryEngine(
            llm_service=RecordingLLMService(),
            enable_error_recovery=False,
        )
        engine.is_in_plan_mode = lambda _conversation_id: True

        schemas = engine._build_tools_schema("conversation-1")
        names = {item["function"]["name"] for item in schemas}

        self.assertTrue(
            {"read_file", "glob", "grep", "bash", "exit_plan_mode"}.issubset(names)
        )
        self.assertNotIn("write_file", names)
        self.assertNotIn("edit_file", names)

    async def test_non_streaming_chat_forwards_request_temperature(self):
        llm = RecordingLLMService()
        engine = QueryEngine(
            llm_service=llm,
            enable_error_recovery=False,
            workspace_root=Path.cwd(),
        )
        conversation_id = engine.create_conversation("temperature-test")

        events = [
            event
            async for event in engine.chat(
                conversation_id,
                "hello",
                temperature=0.2,
            )
        ]

        self.assertEqual(llm.requests[0].temperature, 0.2)
        self.assertEqual(events[-1]["finish_reason"], "stop")


if __name__ == "__main__":
    unittest.main()
