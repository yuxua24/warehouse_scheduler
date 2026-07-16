"""多模型配置管理：定义支持的模型及其客户端配置。

支持通过 json 配置文件或代码动态配置多个模型，
用于多模型对比评测。
"""

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ModelConfig:
    """单个模型的配置。

    Args:
        name: 模型名称（用于报告显示）
        model_id: 调用时使用的 model 参数（如 deepseek-chat）
        api_key: API 密钥
        base_url: API 基础地址
        temperature: 温度参数（默认 0.1）
        max_tokens: 最大输出 token（默认 2000）
        client: 可选的已创建 client（优先使用）
    """

    name: str
    model_id: str
    api_key: str = ""
    base_url: str = ""
    temperature: float = 0.1
    max_tokens: int = 2000
    client: Any = None

    def get_client(self):
        """获取或创建 LLM client。"""
        if self.client is not None:
            return self.client

        from openai import OpenAI
        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
        )
        return self.client


def load_model_configs(path: str = None) -> List[ModelConfig]:
    """从 JSON 文件加载模型配置。

    JSON 格式:
    {
        "models": [
            {
                "name": "deepseek-chat",
                "model_id": "deepseek-chat",
                "api_key": "${DEEPSEEK_API_KEY}",
                "base_url": "https://api.deepseek.com/v1"
            }
        ]
    }
    """
    if path is None:
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "configs",
            "api_config.json",
        )

    if not os.path.exists(path):
        return []

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # 单模型配置（兼容现有 api_config.json）
        if "deepseek_api_key" in data:
            return [
                ModelConfig(
                    name="deepseek-chat",
                    model_id=data.get("model", "deepseek-chat"),
                    api_key=data["deepseek_api_key"],
                    base_url=data.get("deepseek_base_url", "https://api.deepseek.com/v1"),
                    temperature=data.get("temperature", 0.1),
                    max_tokens=data.get("max_tokens", 2000),
                )
            ]

        # 多模型配置
        if "models" in data:
            return [
                ModelConfig(
                    name=m.get("name", m["model_id"]),
                    model_id=m["model_id"],
                    api_key=m.get("api_key", ""),
                    base_url=m.get("base_url", ""),
                    temperature=m.get("temperature", 0.1),
                    max_tokens=m.get("max_tokens", 2000),
                )
                for m in data["models"]
            ]

    except (json.JSONDecodeError, OSError, KeyError) as e:
        print(f"  [ERROR] Failed to load model config: {e}")

    return []


# 默认支持的模型列表
DEFAULT_MODELS = [
    ModelConfig(
        name="deepseek-chat",
        model_id="deepseek-chat",
        base_url="https://api.deepseek.com/v1",
    ),
]
