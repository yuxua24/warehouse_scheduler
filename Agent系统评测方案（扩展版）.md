# Agent 系统评测方案（扩展版）

> 将评测分为两个独立层面：规划器评测 + LLM/Agent 评测
> 不修改代码，仅设计方案

---

## 一、当前评测的局限

现有的场景评测仅覆盖了**规划器层面**（`run_structured` 绕过 LLM），缺少：

| 缺少的内容 | 影响 |
|-----------|------|
| LLM 工具调用是否正确 | 工具调了但参数填错 → 下游全错，但规划器层面测不出来 |
| 无效输入处理 | 用户说"你好"→ LLM 应该用 `answer_question` 而不是 `schedule_robots` |
| Token 消耗 | 不同 prompt 版本、不同模型的 token 用量对比——直接关联成本 |
| LLM 延迟 | 工具调用延迟 ~2s vs A* 规划延迟 ~5ms，优化重点完全不同 |
| 多模型对比 | DeepSeek vs GPT-4o vs Qwen——哪个性价比最高？ |

---

## 二、评测分层架构

```
Agent 系统评测
│
├── 层面 1：规划器评测（确定性内核）
│   ├── 目标：测试 A*、冲突检测、重规划、部分执行是否正确
│   ├── 方法：使用 run_structured() 绕过 LLM，纯确定性执行
│   └── 依赖：scenario_test.json（已实现）
│
├── 层面 2：LLM/Agent 评测（自然语言理解 + 工具调用）
│   ├── 子项 A：工具选择正确性
│   │   ├── 测试：LLM 面对 12 种用户意图，是否选择了正确的工具
│   │   └── 指标：工具选择准确率
│   │
│   ├── 子项 B：参数填充正确性
│   │   ├── 测试：LLM 调用工具时，参数是否完整且符合 schema
│   │   └── 指标：参数完整率、参数合法率、参数精确匹配率
│   │
│   ├── 子项 C：无效输入与边界处理
│   │   ├── 测试：乱码、空输入、不相关输入、模糊输入
│   │   └── 指标：正确拒绝率、降级处理正确率
│   │
│   ├── 子项 D：延迟与 Token 消耗
│   │   ├── 测试：不同模型、不同 prompt 长度下的性能
│   │   └── 指标：P50/P95 LLM 延迟、输入/输出 tokens、TTFT
│   │
│   └── 子项 E：端到端合成
│       ├── 测试：LLM 解析 + 规划器串联的完整流程
│       └── 指标：端到端成功率、端到端延迟
│
└── 层面 3：多模型对比（交叉评测）
    ├── 同一数据集 → 多个模型 → 对比指标
    ├── DeepSeek / GPT-4o-mini / Qwen / GLM
    └── 产出：模型选型报告（准确率 vs 成本 vs 延迟）
```

---

## 三、数据集设计（扩展）

### 3.1 现有数据集（规划器层面）

`eval/datasets/scenario_test.json` — 15 个场景，不变。

### 3.2 新增数据集：NL 工具调用评测集

**文件**: `eval/datasets/nlu_tool_test.json`

专门测试 LLM 能否正确选择工具和填充参数。

```json
[
  {
    "id": "tool-001",
    "instruction": "R1去装卸区",
    "expected_tool": "schedule_robots",
    "check_params": {
      "tasks.0.robot_id": {"expect": "R1"},
      "tasks.0.goal_location_id": {"expect_in": ["loading_dock_north", "loading_dock_west"]},
      "tasks.0.priority": {"expect_ge": 1}
    },
    "category": "tool_selection"
  },
  {
    "id": "tool-002",
    "instruction": "每天晚上十点所有机器人回充电区",
    "expected_tool": "create_cron_job",
    "check_params": {
      "cron_expr": {"expect_match": "0 22 \\* \\* \\*"},
      "instruction": {"expect_contain": "充电区"}
    },
    "category": "tool_selection"
  },
  {
    "id": "tool-003",
    "instruction": "R1在哪",
    "expected_tool": "answer_question",
    "category": "tool_selection"
  },
  {
    "id": "tool-004",
    "instruction": "显示地图",
    "expected_tool": "show_map",
    "category": "tool_selection"
  },
  {
    "id": "tool-005",
    "instruction": "关闭北侧通道",
    "expected_tool": "modify_map",
    "check_params": {
      "action": {"expect": "close"},
      "corridor_id": {"expect": "north_aisle"}
    },
    "category": "tool_selection"
  }
]
```

**字段说明**：

| 字段 | 含义 | 用于什么指标 |
|------|------|-------------|
| `expected_tool` | LLM 应该选择的工具名 | 工具选择准确率 |
| `check_params` | 参数校验规则 | 参数填充正确率 |
| `check_params.*.expect` | 精确匹配 | 参数精确匹配率 |
| `check_params.*.expect_in` | 值在列表中 | 参数合法率 |
| `check_params.*.expect_contain` | 包含子串 | 参数容错匹配 |
| `check_params.*.expect_ge` | 大于等于 | 数值范围校验 |

建议规模：**30-50 条**，覆盖 12 种工具的调用场景。

### 3.3 新增数据集：无效输入与边界评测集

**文件**: `eval/datasets/edge_input_test.json`

```json
[
  {
    "id": "edge-001",
    "instruction": "",
    "expected_behavior": "reject_or_ask",
    "category": "empty_input"
  },
  {
    "id": "edge-002",
    "instruction": "你好",
    "expected_tool": "answer_question",
    "category": "irrelevant"
  },
  {
    "id": "edge-003",
    "instruction": "asdf1234!!!@@@",
    "expected_behavior": "reject_or_ask",
    "expected_tool": "answer_question",
    "category": "gibberish"
  },
  {
    "id": "edge-004",
    "instruction": "R1去一个不存在的位置",
    "expected_tool": "schedule_robots",
    "check_failure": true,
    "category": "invalid_entity"
  },
  {
    "id": "edge-005",
    "instruction": "把R1移到",
    "expected_tool": "move_robot",
    "check_params": {
      "robot_id": {"expect": "R1"}
    },
    "expected_partial_args": true,
    "category": "incomplete_params"
  }
]
```

建议规模：**15-20 条**，覆盖空输入、乱码、无关话题、不存在的实体、参数缺失。

### 3.4 新增数据集：延迟与 Token 基准测试集

**文件**: `eval/datasets/perf_benchmark.json`

从 NLU 评测集中选取 10 条代表性指令，专门用于性能对比：

```json
[
  {
    "id": "perf-001",
    "instruction": "R1去装卸区，R2去货架B，R3去充电区",
    "category": "schedule_3_robots",
    "prompt_length_chars": 650
  },
  {
    "id": "perf-002",
    "instruction": "显示当前所有的定时任务",
    "category": "list_cron",
    "prompt_length_chars": 650
  }
]
```

**特点**：
- 固定 10 条，不用于准确率测试，只用于性能对比
- 每条标注 `prompt_length_chars` 用于归一化 token 消耗
- 多次运行取平均值（LLM 延迟有波动）

---

## 四、新增评测指标

### 4.1 工具选择指标

| 指标 | 公式 | 说明 |
|------|------|------|
| **工具选择准确率** | 正确选择工具数 / 总用例数 | LLM 是否选对了工具 |
| **工具混淆矩阵** | 12×12 矩阵 | 哪个工具最容易被混淆（如 `schedule_robots` 被误判为 `answer_question`） |

#### 混淆矩阵示例

```
实际\预测   schedule  cron_create  answer_question  ...
schedule      45          2             3
cron_create    3         47             0
answer_q       1          0            49
...
```

对角线越高越好，非对角线是典型的混淆模式。

### 4.2 参数质量指标

| 指标 | 公式 | 说明 |
|------|------|------|
| **参数完整率** | 必需参数全部填写的调用数 / 总调用数 | LLM 是否遗漏了必填参数 |
| **参数合法率** | 参数值在 enum 范围内的调用数 / 总调用数 | 如 robot_id 填了 "R6" 但只有 R1-R5 |
| **参数精确匹配率** | 参数值与预期完全一致的调用数 / 总调用数 | 最严格的指标 |
| **参数软匹配率** | 参数值近似符合的调用数 / 总调用数 | 允许别名、大小写差异 |

### 4.3 无效输入处理指标

| 指标 | 公式 | 说明 |
|------|------|------|
| **正确拒绝率** | 正确处理的无效输入数 / 总无效输入数 | LLM 应该拒绝或降级，而不是崩溃或返回乱码 |
| **降级路径触发率** | 降级到 answer_question 的次数 / 总无效输入数 | LLM 面对不理解的内容转到问答是正确行为 |

### 4.4 延迟指标

| 指标 | 单位 | 说明 |
|------|------|------|
| **LLM 总延迟** | ms | 从发送请求到收到完整响应的耗时 |
| **TTFT (Time to First Token)** | ms | 从发送到收到第一个 token 的耗时（流式场景） |
| **P50 LLM 延迟** | ms | 一半请求快于此值 |
| **P95 LLM 延迟** | ms | 95% 请求快于此值 |
| **LLM 延迟 / Token 比** | ms/token | 延迟归一化到输出 token 数 |

### 4.5 Token 消耗指标

| 指标 | 单位 | 说明 |
|------|------|------|
| **输入 Tokens** | tokens | System prompt + tools + user message 的总 token 数 |
| **输出 Tokens** | tokens | LLM 返回的 tool_calls + content 的 token 数 |
| **总 Tokens** | tokens | 输入 + 输出 |
| **缓存命中 Tokens** | tokens | 命中了 prompt cache 的部分（成本减半） |
| **每任务平均 Tokens** | tokens/task | 总 tokens / 工具调用次数 |

### 4.6 多模型对比指标

将以上所有指标按模型分组对比：

```python
{
    "deepseek-chat": {
        "tool_accuracy": 0.92,
        "param_completeness": 0.95,
        "avg_latency_ms": 1850,
        "avg_total_tokens": 850,
        "cost_per_1k_calls": "$0.85"
    },
    "gpt-4o-mini": {
        "tool_accuracy": 0.95,
        "param_completeness": 0.97,
        "avg_latency_ms": 1200,
        "avg_total_tokens": 920,
        "cost_per_1k_calls": "$0.15"
    },
    "qwen-max": {
        "tool_accuracy": 0.88,
        ...
    }
}
```

---

## 五、评测执行方法

### 5.1 规划设计器评测（已有）

```
run_scenario_eval()
  → for each case: workflow.run_structured(structured)
  → 收集指标
  → 生成报告
```

无需 LLM，纯确定性执行。

### 5.2 LLM/Agent 评测（新增）

```
run_agent_eval(dataset, model_name, llm_client)
  → for each case:
      1. 调用 ToolManager.process(instruction)（使用指定模型）
      2. 记录:
         - 选择的工具名
         - 填充的参数
         - LLM 延迟
         - Token 消耗（输入/输出）
         - 是否报错 / 降级
      3. 对比预期：
         - expected_tool == actual_tool?
         - check_params 逐项校验
         - 是否正确处理了无效输入
  → 计算指标
  → 按模型分组生成对比报告
```

### 5.3 执行流程

```python
def run_agent_eval(
    dataset_path: str,
    model_name: str,
    llm_client: Any,
    tool_manager: ToolManager,
) -> dict:
    """运行 Agent/LLM 层面评测。"""
    dataset = load_dataset(dataset_path)
    results = []

    for case in dataset:
        instruction = case["instruction"]

        # 记录 Token 消耗
        token_counter = TokenCounter(llm_client)

        t0 = time.time()
        try:
            response = tool_manager.process(instruction)
        except Exception as e:
            response = {"tool_name": "", "success": False, "error": str(e)}
        elapsed = time.time() - t0

        token_usage = token_counter.get_usage()

        comparison = compare_agent_response(
            actual=response,
            expected=case,
        )

        results.append({
            "case_id": case["id"],
            "category": case.get("category", ""),
            "actual_tool": response.get("tool_name", ""),
            "expected_tool": case.get("expected_tool", ""),
            "tool_correct": response.get("tool_name") == case.get("expected_tool"),
            "params": response.get("args", {}),
            "comparison": comparison,
            "latency_ms": round(elapsed * 1000, 2),
            "tokens": token_usage,
            "success": response.get("success", False),
        })

    # 计算汇总指标
    metrics = compute_agent_metrics(results)
    return metrics, results
```

### 5.4 多模型对比执行

```python
def run_multi_model_comparison(
    dataset_path: str,
    models: List[ModelConfig],
) -> dict:
    """在同一个数据集上运行多个模型，输出对比报告。

    models = [
        ModelConfig(name="deepseek-chat", client=deepseek_client),
        ModelConfig(name="gpt-4o-mini", client=openai_client),
        ModelConfig(name="qwen-max", client=alibaba_client),
    ]
    """
    all_results = {}

    for model_cfg in models:
        print(f"  Running {model_cfg.name}...")
        tool_manager = create_tool_manager(model_cfg.client, model_cfg.name)
        metrics, results = run_agent_eval(dataset_path, model_cfg.name, tool_manager)
        all_results[model_cfg.name] = {
            "metrics": metrics,
            "results": results,
        }

    return generate_model_comparison_report(all_results)
```

### 5.5 Token 消耗的精确测量

需要拦截 LLM API 调用来获取实际的 token 计数：

```python
class TokenCounter:
    """包装 LLM client，记录每次调用的 token 消耗。"""

    def __init__(self, llm_client):
        self.original_create = llm_client.chat.completions.create
        self.total_input_tokens = 0
        self.total_output_tokens = 0

    def __enter__(self):
        def counting_create(*args, **kwargs):
            response = self.original_create(*args, **kwargs)
            if hasattr(response, 'usage') and response.usage:
                usage = response.usage
                self.total_input_tokens += getattr(usage, 'prompt_tokens', 0)
                self.total_output_tokens += getattr(usage, 'completion_tokens', 0)
                # GPT-4o 支持 prompt cache
                self.cached_tokens = getattr(usage, 'prompt_tokens_details', {}).get('cached_tokens', 0)
            return response

        self.llm_client.chat.completions.create = counting_create
        return self

    def __exit__(self, *args):
        self.llm_client.chat.completions.create = self.original_create

    def get_usage(self):
        return {
            "input_tokens": self.total_input_tokens,
            "output_tokens": self.total_output_tokens,
            "total_tokens": self.total_input_tokens + self.total_output_tokens,
        }
```

---

## 六、新增文件结构

```
warehouse_scheduler/
├── eval/
│   ├── datasets/
│   │   ├── scenario_test.json         # [已有] 规划器场景
│   │   ├── nlu_tool_test.json         # [新增] 工具调用评测集 (30-50条)
│   │   ├── edge_input_test.json       # [新增] 无效输入评测集 (15-20条)
│   │   └── perf_benchmark.json        # [新增] 性能基准集 (10条)
│   │
│   ├── runner.py                      # [已有] 规划器评测执行器
│   ├── agent_runner.py                # [新增] Agent/LLM 评测执行器
│   │   ├── run_agent_eval()
│   │   └── compare_agent_response()
│   │
│   ├── metrics.py                     # [已有] 规划器指标
│   ├── agent_metrics.py               # [新增] Agent 指标
│   │   ├── compute_tool_accuracy()
│   │   ├── compute_param_quality()
│   │   ├── compute_latency_metrics()
│   │   └── compute_token_metrics()
│   │
│   ├── comparators.py                 # [已有] 规划器对比
│   ├── agent_comparators.py           # [新增] Agent 对比
│   │   ├── compare_tool_selection()
│   │   ├── validate_params()
│   │   └── check_edge_behavior()
│   │
│   ├── reporter.py                    # [已有] 规划器报告
│   ├── agent_reporter.py              # [新增] Agent 报告
│   │   ├── generate_agent_report()
│   │   └── generate_model_comparison()
│   │
│   ├── model_config.py                # [新增] 多模型配置
│   │   └── ModelConfig: name, client, api_key, base_url
│   │
│   └── run_all.py                     # [修改] 加入 Agent 评测入口
│       ├── run_scenario_eval()
│       ├── run_agent_eval()
│       └── run_model_comparison()
│
└── tests/
    └── eval/
        └── test_agent_metrics.py      # [新增] Agent 评测模块测试 (15-20条)
```

---

## 七、评测报告输出

### 7.1 Agent 评测报告示例

```markdown
# Agent 评测报告：工具调用

> 模型: deepseek-chat | 数据集: nlu_tool_test.json (40条)
> 生成时间: 2026-07-16

## 工具选择准确率

| 工具名 | 测试数 | 正确数 | 准确率 |
|--------|--------|--------|--------|
| schedule_robots | 10 | 9 | 90% |
| create_cron_job | 5 | 5 | 100% |
| list_cron_jobs | 3 | 3 | 100% |
| answer_question | 5 | 5 | 100% |
| show_map | 3 | 2 | 67% |
| ... | ... | ... | ... |
| **总计** | **40** | **37** | **92.5%** |

## 参数质量

| 指标 | 数值 |
|------|------|
| 参数完整率 | 95.0% |
| 参数合法率 | 97.5% |
| 参数精确匹配率 | 82.5% |
| 参数软匹配率 | 92.5% |

## 无效输入处理

| 输入类型 | 测试数 | 正确拒绝 | 正确率 |
|---------|--------|---------|--------|
| 空输入 | 3 | 3 | 100% |
| 乱码 | 3 | 2 | 67% |
| 无关话题 | 5 | 5 | 100% |
| 不存在实体 | 4 | 3 | 75% |

## 延迟 (LLM)

| 指标 | 数值 |
|------|------|
| 平均 LLM 延迟 | 1850 ms |
| P50 延迟 | 1720 ms |
| P95 延迟 | 3100 ms |
| 平均 TTFT | 420 ms |

## Token 消耗

| 指标 | 数值 |
|------|------|
| 平均输入 Tokens | 652 |
| 平均输出 Tokens | 198 |
| 平均总 Tokens | 850 |
| 缓存命中 Tokens | 645 (75.9%) |
| 每任务平均 Tokens | 850 |
```

### 7.2 多模型对比报告示例

```markdown
# 多模型对比报告

> 数据集: nlu_tool_test.json (40条) + perf_benchmark.json (10条)

## 准确率 vs 延迟 vs 成本

| 模型 | 工具准确率 | 参数精确率 | 平均延迟 | 平均 Tokens | 每千次成本 |
|------|-----------|-----------|---------|------------|-----------|
| deepseek-chat | 92.5% | 82.5% | 1850ms | 850 | ¥0.85 |
| gpt-4o-mini | 95.0% | 87.5% | 1200ms | 920 | ¥0.42 |
| qwen-max | 90.0% | 80.0% | 2100ms | 780 | ¥0.38 |
| glm-4 | 87.5% | 75.0% | 2500ms | 950 | ¥0.28 |

## 推荐

- **成本优先**: qwen-max (最低成本，可接受准确率)
- **性能优先**: gpt-4o-mini (最低延迟，最高准确率)
- **综合性价比**: deepseek-chat (当前在用，均衡)

## 混淆矩阵对比

| 模型 | 最常混淆的组合 | 混淆率 |
|------|--------------|--------|
| deepseek-chat | show_map → answer_question | 8% |
| gpt-4o-mini | schedule → cron_create (时间+任务) | 3% |
```

---

## 八、实施建议

### 阶段 1：新增数据集（1 天）

- 创建 `nlu_tool_test.json`（40 条工具调用测试）
- 创建 `edge_input_test.json`（15 条无效输入测试）
- 创建 `perf_benchmark.json`（10 条性能基准）

### 阶段 2：Agent 评测执行器（2 天）

- 实现 `agent_runner.py` — 调用 ToolManager.process()，记录延迟和 token
- 实现 `agent_metrics.py` — 工具选择、参数质量、无效输入、延迟、token 指标
- 实现 `agent_reporter.py` — 生成含混淆矩阵的 Markdown 报告

### 阶段 3：多模型对比（2 天）

- 实现 `model_config.py` — 多模型配置管理
- 实现 `run_model_comparison()` — 同一数据集多模型执行
- 实现 `generate_model_comparison_report()` — 对比报告 + 推荐

### 阶段 4：集成到 run_all（0.5 天）

- 修改 `run_all.py`，加入 `--mode planner/agent/all/compare` 参数
- `python eval/run_all.py --mode agent --model deepseek-chat`
- `python eval/run_all.py --mode compare --models deepseek-chat,gpt-4o-mini`

---

## 九、与现有架构的关系

```
当前 run_all.py:
  run_scenario_eval()          → 仅规划器层面

扩展后 run_all.py:
  run_scenario_eval()          → 层面 1：规划器
  run_agent_eval()             → 层面 2：LLM/Agent
  run_multi_model_comparison() → 层面 3：多模型对比
```

三个层面互不依赖，可以独立运行：

```bash
# 只跑规划器（免费，快）
python eval/run_all.py --mode planner

# 只跑 Agent（需要 LLM API，慢）
python eval/run_all.py --mode agent --model deepseek-chat

# 跑全部（先规划器再 Agent）
python eval/run_all.py --mode all --model deepseek-chat

# 多模型对比（最耗时）
python eval/run_all.py --mode compare \
    --models deepseek-chat,gpt-4o-mini,qwen-max \
    --dataset eval/datasets/nlu_tool_test.json
```

---

## 十、总结

| 维度 | 原有（规划器） | 新增（Agent） |
|------|--------------|--------------|
| 测试对象 | A* + 冲突检测 + 重规划 | LLM 工具调用 + 参数填充 |
| 测试方法 | `run_structured()` 绕过 LLM | `ToolManager.process()` 调用完整流程 |
| 依赖 | 无（纯确定性） | 需要 LLM API（有费用） |
| 数据集 | `scenario_test.json` (15 个) | `nlu_tool_test.json` (40 个) + `edge_input_test.json` (15 个) |
| 核心指标 | 规划成功率、路径质量、冲突解决率 | 工具选择准确率、参数质量、无效输入处理 |
| 性能指标 | A* 耗时、节点展开数 | LLM 延迟、TTFT、Token 消耗 |
| 多模型对比 | 不适用 | DeepSeek / GPT-4o-mini / Qwen / GLM 对比 |
