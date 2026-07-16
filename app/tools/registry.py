"""轻量级工具调用系统。

基于 Hermes Agent 的注册表 + 分发模式，但大幅简化：
- 注册表：~40 行 Python 字典，而非 810 行的完整系统
- 所有工具注册到中央注册表，LLM 自主选择调用哪个
- dispatch() 分发执行，结果返回字符串

使用方法:
    registry = ToolRegistry()
    registry.register("my_tool", "描述", parameters_schema, handler_func)
    tools = registry.get_openai_tools()  # 供 LLM API 使用
    result = registry.dispatch("my_tool", {"arg": "value"})
"""

import json
import traceback


class ToolRegistry:
    """轻量级工具注册表。"""

    def __init__(self):
        self._tools: dict = {}  # name -> {name, description, parameters, handler}

    def register(
        self,
        name: str,
        description: str,
        parameters: dict,
        handler: callable,
    ) -> None:
        """注册一个工具。

        Args:
            name: 工具名称（唯一标识）
            description: LLM 可读的描述
            parameters: JSON Schema 格式的参数定义
            handler: 可调用对象，接收 (args: dict) -> str
        """
        self._tools[name] = {
            "name": name,
            "description": description,
            "parameters": parameters,
            "handler": handler,
        }

    def deregister(self, name: str) -> None:
        """取消注册一个工具。"""
        self._tools.pop(name, None)

    def get_all_names(self) -> list:
        """获取所有已注册的工具名。"""
        return list(self._tools.keys())

    def get_openai_tools(self) -> list:
        """返回 OpenAI Function Calling 格式的工具定义列表。"""
        return [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["parameters"],
                },
            }
            for t in self._tools.values()
        ]

    def dispatch(self, name: str, args: dict) -> str:
        """执行工具，返回 JSON 字符串结果。

        Returns:
            成功: {"success": true, "data": ...}
            失败: {"success": false, "error": "..."}
        """
        tool = self._tools.get(name)
        if tool is None:
            return json.dumps(
                {"success": False, "error": f"Unknown tool: {name}"},
                ensure_ascii=False,
            )
        try:
            result = tool["handler"](args)
            if isinstance(result, str):
                return result
            return json.dumps(
                {"success": True, "data": result},
                ensure_ascii=False,
            )
        except Exception as e:
            traceback.print_exc()
            return json.dumps(
                {"success": False, "error": str(e)},
                ensure_ascii=False,
            )
