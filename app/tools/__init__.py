"""工具调用系统：注册表 + 分发 + 统一 LLM 入口。

替代原来的 classify_intent + parse 两次 LLM 调用 + if/elif 代码路由。
改为一次 LLM 调用，LLM 自主选择工具，注册表分发执行。

使用方式:
    from app.tools import ToolRegistry, ToolManager
    registry = ToolRegistry()
    manager = ToolManager(registry, llm_client, ...)
    result = manager.process("R1去装卸区")
"""

from .registry import ToolRegistry
from .tool_manager import ToolManager
from .handlers import (
    build_system_prompt,
    build_system_messages,
    build_tool_definitions,
    build_schedule_tool_schema,
    UNIFIED_SYSTEM_PROMPT,
)

__all__ = [
    "ToolRegistry",
    "ToolManager",
    "build_system_prompt",
    "build_system_messages",
    "build_tool_definitions",
    "build_schedule_tool_schema",
    "UNIFIED_SYSTEM_PROMPT",
]
