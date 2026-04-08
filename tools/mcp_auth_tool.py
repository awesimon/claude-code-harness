"""
MCP认证工具模块

提供MCP服务器的认证功能。
当MCP服务器需要OAuth认证时，此工具可以帮助启动认证流程。
"""

from dataclasses import dataclass
from typing import Optional, Dict, Any

from .base import Tool, ToolResult, ToolError, ToolValidationError, ToolExecutionError, register_tool
from .mcp_tool import get_mcp_manager, MCPServer


@dataclass
class McpAuthInput:
    """MCP认证输入"""
    server: str  # 服务器名称


@register_tool
class McpAuthTool(Tool[McpAuthInput, Dict[str, Any]]):
    """
    MCP认证工具

    启动MCP服务器的OAuth认证流程。
    当MCP服务器需要认证时，此工具会返回授权URL，
    用户需要在浏览器中完成授权。

    使用场景:
    - 首次连接需要认证的MCP服务器
    - 刷新过期的访问令牌
    - 切换不同的用户账号

    注意: 此工具返回授权URL，用户需要手动在浏览器中完成授权流程。
    """

    name = "mcp_authenticate"
    description = "启动MCP服务器的OAuth认证流程"
    version = "1.0"

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "parameters": {
                "type": "object",
                "properties": {
                    "server": {
                        "type": "string",
                        "description": "需要认证的MCP服务器名称"
                    }
                },
                "required": ["server"]
            },
            "returns": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["auth_url", "unsupported", "error", "success"],
                        "description": "认证状态"
                    },
                    "message": {"type": "string", "description": "状态消息"},
                    "auth_url": {"type": "string", "description": "授权URL（如适用）"},
                    "server": {"type": "string", "description": "服务器名称"}
                }
            }
        }

    async def validate(self, input_data: McpAuthInput) -> Optional[ToolError]:
        """验证输入参数"""
        if not input_data.server or not input_data.server.strip():
            return ToolValidationError("server（服务器名称）不能为空")

        manager = get_mcp_manager()
        server = manager.get_server(input_data.server)
        if not server:
            return ToolValidationError(f"MCP服务器不存在: {input_data.server}")

        return None

    async def execute(self, input_data: McpAuthInput) -> ToolResult:
        """执行认证操作"""
        try:
            manager = get_mcp_manager()
            server = manager.get_server(input_data.server)

            # 检查服务器类型
            server_type = getattr(server, 'type', 'stdio')

            # 只有SSE/HTTP类型的服务器支持OAuth
            if server_type not in ['sse', 'http']:
                return ToolResult.ok(
                    data={
                        "status": "unsupported",
                        "message": f"服务器 '{input_data.server}' 使用 {server_type} 传输，不支持从此工具进行OAuth认证。请运行 /mcp 并手动认证。",
                        "server": input_data.server
                    },
                    message=f"服务器 '{input_data.server}' 不支持程序化OAuth认证"
                )

            # 模拟OAuth流程
            # 实际实现应该：
            # 1. 启动OAuth流程
            # 2. 获取授权URL
            # 3. 等待用户完成授权
            # 4. 交换授权码获取令牌

            auth_url = f"https://example.com/oauth/authorize?client_id=mcp_{input_data.server}&response_type=code"

            return ToolResult.ok(
                data={
                    "status": "auth_url",
                    "message": f"请在浏览器中打开以下URL以授权 {input_data.server} MCP 服务器:\n\n{auth_url}\n\n完成后，服务器的工具将自动可用。",
                    "auth_url": auth_url,
                    "server": input_data.server
                },
                message=f"已启动 {input_data.server} 的OAuth认证流程",
                metadata={
                    "server": input_data.server,
                    "note": "这是一个框架实现，实际使用需要完整的OAuth实现"
                }
            )

        except Exception as e:
            return ToolResult.error(
                ToolExecutionError(f"MCP认证失败: {str(e)}")
            )
