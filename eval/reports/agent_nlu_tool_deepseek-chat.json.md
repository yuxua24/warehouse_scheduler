# Agent 评测报告：工具调用

> 模型: deepseek-chat | 数据集: nlu_tool_test | 总用例: 25
> 生成时间: 2026-07-16 15:56:15

## 工具选择准确率

| 工具名 | 测试数 | 正确数 | 准确率 |
|--------|--------|--------|--------|
| answer_question | 2 | 2 | 100.0% |
| confirm_cargo_done | 2 | 2 | 100.0% |
| create_cron_job | 2 | 2 | 100.0% |
| delete_all_cron_jobs | 1 | 1 | 100.0% |
| delete_cron_job | 1 | 1 | 100.0% |
| list_cron_jobs | 3 | 3 | 100.0% |
| modify_map | 3 | 3 | 100.0% |
| move_robot | 2 | 2 | 100.0% |
| schedule_robots | 5 | 5 | 100.0% |
| show_map | 2 | 2 | 100.0% |
| toggle_cron_job | 2 | 2 | 100.0% |
| **总计** | **25** | **25** | **100.0%** |

### 混淆矩阵

| 实际\期望 | answer_question | confirm_cargo_done | create_cron_job | delete_all_cron_jobs | delete_cron_job | list_cron_jobs | modify_map | move_robot | schedule_robots | show_map | toggle_cron_job |
|-----------|---|---|---|---|---|---|---|---|---|---|---|
| answer_question | 2 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| confirm_cargo_done | 0 | 2 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| create_cron_job | 0 | 0 | 2 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| delete_all_cron_jobs | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| delete_cron_job | 0 | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| list_cron_jobs | 0 | 0 | 0 | 0 | 0 | 3 | 0 | 0 | 0 | 0 | 0 |
| modify_map | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 0 | 0 | 0 | 0 |
| move_robot | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 2 | 0 | 0 | 0 |
| schedule_robots | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 5 | 0 | 0 |
| show_map | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 2 | 0 |
| toggle_cron_job | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 2 |

## 参数质量

| 指标 | 数值 |
|------|------|
| 参数完整率 | 96.0% |
| 参数精确匹配率 | 96.0% |

## 无效输入处理

| 输入类型 | 测试数 | 正确处理 | 正确率 |
|---------|--------|---------|--------|
| tool_selection | 25 | 25 | 100.0% |
| **总计** | **25** | **25** | **100.0%** |

## 延迟 (LLM)

| 指标 | 数值 |
|------|------|
| 平均延迟 | 1137 ms |
| P50 延迟 | 1023 ms |
| P95 延迟 | 1491 ms |
| 最大延迟 | 2112 ms |
| 最小延迟 | 728 ms |
| 采样数 | 25 |

## Token 消耗

| 指标 | 数值 |
|------|------|
| 总输入 Tokens | 58219 |
| 总输出 Tokens | 1945 |
| 总 Tokens | 60164 |
| 平均输入 Tokens | 2329 |
| 平均输出 Tokens | 78 |
| 缓存命中 Tokens | 57600 |
| 缓存命中率 | 98.9% |
