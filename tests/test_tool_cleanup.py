from pathlib import Path

import pytest

from tools.base import ToolExecutionError, ToolValidationError
from tools.lsp_tool import LSPInput, LSPTool
from tools.powershell_tool import PowerShellInput, PowerShellTool


@pytest.mark.asyncio
async def test_lsp_compatibility_tool_fails_closed(tmp_path: Path) -> None:
    source = tmp_path / "example.py"
    source.write_text("value = 1\n", encoding="utf-8")
    tool = LSPTool()

    result = await tool.execute(LSPInput("hover", str(source), 1, 1))

    assert result.success is False
    assert isinstance(result.error, ToolExecutionError)
    assert result.error.details == {"tool_name": "lsp", "reason": "unsupported"}


@pytest.mark.asyncio
async def test_lsp_validation_uses_classified_error() -> None:
    error = await LSPTool().validate(LSPInput("invalid", "missing.py", 1, 1))

    assert isinstance(error, ToolValidationError)
    assert error.details["tool_name"] == "lsp"


@pytest.mark.asyncio
async def test_powershell_non_windows_error_keeps_tool_detail() -> None:
    tool = PowerShellTool()
    tool.is_windows = False

    result = await tool.run(PowerShellInput(command="Get-Date"))

    assert result.success is False
    assert isinstance(result.error, ToolExecutionError)
    assert result.error.details["tool_name"] == "powershell"
