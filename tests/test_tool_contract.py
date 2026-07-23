import unittest

import tools  # noqa: F401 - importing registers the primary tool catalog
import agents  # noqa: F401 - importing registers the canonical Agent tool
from tools.base import ToolExecutionError, ToolRegistry, ToolResult


class ToolResultContractTests(unittest.TestCase):
    def test_success_result_has_no_error(self):
        result = ToolResult.ok({"value": 1})

        self.assertTrue(result.success)
        self.assertIsNone(result.error)

    def test_failure_result_preserves_error(self):
        error = ToolExecutionError("failed")

        result = ToolResult.fail(error)

        self.assertFalse(result.success)
        self.assertIs(result.error, error)
        self.assertEqual(result.message, "[Error 500] failed")


class ToolRegistryContractTests(unittest.TestCase):
    def test_every_registered_tool_has_an_object_parameter_schema(self):
        specs = ToolRegistry.list_specs()

        self.assertGreater(len(specs), 0)
        missing = [
            spec.name
            for spec in specs
            if spec.parameters.get("type") != "object"
            or "properties" not in spec.parameters
        ]
        self.assertEqual(missing, [])

    def test_legacy_names_resolve_to_canonical_tools(self):
        expected = {
            "Read": "read_file",
            "Write": "write_file",
            "Edit": "edit_file",
            "Glob": "glob",
            "Grep": "grep",
            "Bash": "bash",
            "Agent": "agent",
            "EnterPlanMode": "enter_plan_mode",
            "ExitPlanMode": "exit_plan_mode",
            "AskUserQuestion": "ask_user_question",
        }

        for alias, canonical in expected.items():
            with self.subTest(alias=alias):
                self.assertIsNotNone(ToolRegistry.get(alias))
                self.assertEqual(ToolRegistry.get_spec(alias).name, canonical)
                self.assertIs(ToolRegistry.get(alias), ToolRegistry.get(canonical))

    def test_primary_catalog_has_one_canonical_agent_tool(self):
        names = [spec.name for spec in ToolRegistry.list_specs()]

        self.assertEqual(names.count("agent"), 1)


if __name__ == "__main__":
    unittest.main()
