import React, { useState } from 'react'
import { scheduleInstruction } from '../api'

const EXAMPLES = [
  'R1从左上角前往装卸区，R2前往货架B，R3前往充电区',
  '关闭北侧通道，R1前往货架A，R2前往充电区',
  'R1去装卸区，R2去货架A，R3去货架B',
  'R1去充电区，R2去打包站，R3去维护区',
]

export default function SchedulePanel({ onResult, loading, setLoading }) {
  const [instruction, setInstruction] = useState('')
  const [error, setError] = useState(null)

  async function handleSubmit() {
    if (!instruction.trim()) return
    setError(null)
    setLoading(true)
    try {
      const result = await scheduleInstruction(instruction.trim())
      onResult(result)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  function handleKeyDown(e) {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
      handleSubmit()
    }
  }

  return (
    <div className="schedule-panel">
      <h3>自然语言调度</h3>

      <textarea
        placeholder="输入调度指令，例如：R1从左上角前往装卸区，R2前往货架B，R3前往充电区"
        value={instruction}
        onChange={e => setInstruction(e.target.value)}
        onKeyDown={handleKeyDown}
      />

      <div className="hint">
        支持中文自然语言。可按 Ctrl+Enter 提交。
      </div>

      <button
        className="schedule-btn"
        onClick={handleSubmit}
        disabled={loading || !instruction.trim()}
      >
        {loading ? '规划中...' : '🚀 开始调度'}
      </button>

      {error && (
        <div className="schedule-error">
          {error}
        </div>
      )}

      {loading && (
        <div className="schedule-loading">
          <div className="spinner" style={{ width: 16, height: 16 }} />
          正在规划路径...
        </div>
      )}

      <div className="quick-examples">
        <div className="example-label">快速示例</div>
        {EXAMPLES.map((ex, i) => (
          <button
            key={i}
            className="example-btn"
            onClick={() => setInstruction(ex)}
          >
            💡 {ex}
          </button>
        ))}
      </div>
    </div>
  )
}
