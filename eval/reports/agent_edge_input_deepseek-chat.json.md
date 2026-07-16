# Agent 评测报告：工具调用

> 模型: deepseek-chat | 数据集: edge_input_test | 总用例: 15
> 生成时间: 2026-07-16 15:56:36

## 工具选择准确率

| 工具名 | 测试数 | 正确数 | 准确率 |
|--------|--------|--------|--------|
| answer_question | 7 | 0 | 0.0% |
| confirm_cargo_done | 1 | 0 | 0.0% |
| delete_cron_job | 1 | 0 | 0.0% |
| list_cron_jobs | 1 | 1 | 100.0% |
| modify_map | 1 | 0 | 0.0% |
| move_robot | 1 | 0 | 0.0% |
| schedule_robots | 3 | 0 | 0.0% |
| **总计** | **15** | **1** | **6.7%** |

### 混淆矩阵

| 实际\期望 | _text_reply | answer_question | confirm_cargo_done | delete_cron_job | list_cron_jobs | modify_map | move_robot | schedule_robots |
|-----------|---|---|---|---|---|---|---|---|
| _text_reply | 0 | 7 | 1 | 1 | 0 | 1 | 1 | 2 |
| answer_question | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 |
| confirm_cargo_done | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| delete_cron_job | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| list_cron_jobs | 0 | 0 | 0 | 0 | 1 | 0 | 0 | 0 |
| modify_map | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| move_robot | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| schedule_robots | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

## 参数质量

| 指标 | 数值 |
|------|------|
| 参数完整率 | 93.3% |
| 参数精确匹配率 | 93.3% |

## 无效输入处理

| 输入类型 | 测试数 | 正确处理 | 正确率 |
|---------|--------|---------|--------|
| ambiguous | 1 | 0 | 0.0% |
| empty_input | 2 | 2 | 100.0% |
| fuzzy_match | 2 | 1 | 50.0% |
| gibberish | 2 | 2 | 100.0% |
| incomplete_params | 3 | 0 | 0.0% |
| invalid_entity | 2 | 2 | 100.0% |
| irrelevant | 3 | 3 | 100.0% |
| **总计** | **15** | **10** | **66.7%** |

## 延迟 (LLM)

| 指标 | 数值 |
|------|------|
| 平均延迟 | 1427 ms |
| P50 延迟 | 1332 ms |
| P95 延迟 | 2652 ms |
| 最大延迟 | 2652 ms |
| 最小延迟 | 710 ms |
| 采样数 | 15 |

## Token 消耗

| 指标 | 数值 |
|------|------|
| 总输入 Tokens | 34881 |
| 总输出 Tokens | 1110 |
| 总 Tokens | 35991 |
| 平均输入 Tokens | 2325 |
| 平均输出 Tokens | 74 |
| 缓存命中 Tokens | 34560 |
| 缓存命中率 | 99.1% |

## 失败详情

- **edge-001**: ""  期望=answer_question, 实际=_text_reply
  - ⚠ 工具选择错误: 期望=answer_question, 实际=_text_reply
- **edge-002**: "你好"  期望=answer_question, 实际=_text_reply
  - ⚠ 工具选择错误: 期望=answer_question, 实际=_text_reply
- **edge-003**: "今天天气怎么样"  期望=answer_question, 实际=_text_reply
  - ⚠ 工具选择错误: 期望=answer_question, 实际=_text_reply
- **edge-004**: "你是谁"  期望=answer_question, 实际=_text_reply
  - ⚠ 工具选择错误: 期望=answer_question, 实际=_text_reply
- **edge-005**: "asdf1234!!!@@@"  期望=answer_question, 实际=_text_reply
  - ⚠ 工具选择错误: 期望=answer_question, 实际=_text_reply
- **edge-006**: "R6去装卸区"  期望=schedule_robots, 实际=_text_reply
  - ⚠ 工具选择错误: 期望=schedule_robots, 实际=_text_reply
- **edge-007**: "R1去一个不存在的位置"  期望=schedule_robots, 实际=answer_question
  - ⚠ 工具选择错误: 期望=schedule_robots, 实际=answer_question
- **edge-008**: "把R1移到"  期望=move_robot, 实际=_text_reply
  - ⚠ 工具选择错误: 期望=move_robot, 实际=_text_reply
  - ⚠ 参数 robot_id: 期望={'expect': 'R1'}, 实际=None
- **edge-009**: "关闭"  期望=modify_map, 实际=_text_reply
  - ⚠ 工具选择错误: 期望=modify_map, 实际=_text_reply
- **edge-010**: "删除"  期望=delete_cron_job, 实际=_text_reply
  - ⚠ 工具选择错误: 期望=delete_cron_job, 实际=_text_reply
- **edge-011**: "R1 R2 R3"  期望=schedule_robots, 实际=_text_reply
  - ⚠ 工具选择错误: 期望=schedule_robots, 实际=_text_reply
- **edge-012**: "   "  期望=answer_question, 实际=_text_reply
  - ⚠ 工具选择错误: 期望=answer_question, 实际=_text_reply
- **edge-013**: "！@#￥%……&"  期望=answer_question, 实际=_text_reply
  - ⚠ 工具选择错误: 期望=answer_question, 实际=_text_reply
- **edge-014**: "卸载完成"  期望=confirm_cargo_done, 实际=_text_reply
  - ⚠ 工具选择错误: 期望=confirm_cargo_done, 实际=_text_reply