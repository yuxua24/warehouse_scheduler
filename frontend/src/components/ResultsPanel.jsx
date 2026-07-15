import React, { useState } from 'react'

const STATUS_LABELS = {
  succeeded: '✅ 全部成功',
  partially_succeeded: '⚠️ 部分成功',
  infeasible: '❌ 不可行',
}

export default function ResultsPanel({
  result, paths, timeStep, maxTimeStep, setTimeStep,
  animating, setAnimating, history = [], onSelectResult = null,
  onDeleteResult = null, onClearResults = null,
}) {
  const [expandedId, setExpandedId] = useState(null)

  const handleClick = (item) => {
    if (expandedId === item.id) {
      setExpandedId(null)
    } else {
      setExpandedId(item.id)
      onSelectResult?.(item.result)
    }
  }

  if (history.length === 0) {
    return (
      <div className="results-panel">
        <h3>📊 结果历史</h3>
        <div style={{ color: 'var(--text-secondary)', fontSize: 13, padding: 20, textAlign: 'center' }}>
          从对话栏发起调度后，结果会显示在这里
        </div>
      </div>
    )
  }

  return (
    <div className="results-panel">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <h3 style={{ margin: 0 }}>📊 结果历史 ({history.length})</h3>
        {history.length > 0 && onClearResults && (
          <button className="action-btn danger" style={{ fontSize: 11, padding: '3px 8px' }}
            onClick={() => { if (confirm('确定清空全部结果？')) onClearResults() }}>
            🗑 清空
          </button>
        )}
      </div>

      {history.map((item) => {
        const isExpanded = expandedId === item.id
        return (
          <div key={item.id} className="task-card" style={{
            cursor: 'pointer', marginBottom: 6,
            borderColor: isExpanded ? 'var(--accent2)' : undefined,
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}
              onClick={() => handleClick(item)}>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 12, fontWeight: 600 }}>
                  {item.instruction.slice(0, 50)}
                </div>
                <div style={{ fontSize: 10, color: 'var(--text-secondary)', marginTop: 2 }}>
                  {new Date(item.time).toLocaleString('zh-CN')} · {STATUS_LABELS[item.result?.batch_status] || '?'}
                </div>
              </div>
              <span style={{
                transform: isExpanded ? 'rotate(180deg)' : 'rotate(0deg)',
                transition: 'transform 0.25s ease',
                fontSize: 10, color: 'var(--text-secondary)',
              }}>▼</span>
              {onDeleteResult && (
                <span style={{ marginLeft: 8, cursor: 'pointer', fontSize: 12, opacity: 0.5 }}
                  onClick={(e) => { e.stopPropagation(); if (confirm('删除这个结果？')) onDeleteResult(item.id) }}
                  title="删除">🗑️</span>
              )}
            </div>

            <div style={{
              maxHeight: isExpanded ? '600px' : '0px',
              overflow: 'hidden',
              transition: 'max-height 0.35s ease, margin-top 0.35s ease',
              marginTop: isExpanded ? 8 : 0,
            }}>
              {item.result?.tasks?.map((task) => {
                const pathData = paths?.[task.robot_id]
                const color = pathData?.color || '#888'
                return (
                  <div key={task.robot_id} style={{
                    background: 'var(--bg)', padding: '6px 8px',
                    borderRadius: 4, marginBottom: 4, fontSize: 11,
                  }}>
                    <span style={{ color, fontWeight: 600 }}>🤖 {task.robot_id}</span>
                    <span style={{ marginLeft: 8 }}>{task.success ? '✅' : '❌'}</span>
                    {task.success && (
                      <span style={{ marginLeft: 8, color: 'var(--text-secondary)' }}>
                        {task.path?.length || 0} 步 · {task.makespan}t
                      </span>
                    )}
                    {!task.success && (
                      <span style={{ marginLeft: 8, color: 'var(--accent)' }}>
                        {task.failure_reason || '未知'}
                      </span>
                    )}
                  </div>
                )
              })}

              {item.result?.metrics && (
                <div style={{ fontSize: 10, color: 'var(--text-secondary)', marginTop: 4 }}>
                  成功率 {Math.round(item.result.metrics.planning_success_rate * 100)}% · 耗时 {Math.round(item.result.metrics.total_planning_time_ms)}ms
                </div>
              )}

              {isExpanded && maxTimeStep > 0 && (
                <div className="time-slider-container" style={{ marginTop: 8, padding: 8 }}>
                  <label style={{ fontSize: 11 }}>🎬 时间步: {timeStep} / {maxTimeStep}</label>
                  <input type="range" min={0} max={maxTimeStep} value={timeStep}
                    onChange={e => setTimeStep(Number(e.target.value))}
                    style={{ width: '100%', accentColor: 'var(--accent2)' }} />
                  <div className="time-slider-controls" style={{ marginTop: 4 }}>
                    <button className="action-btn" onClick={() => setAnimating(!animating)}
                      style={{ fontSize: 11, padding: '4px 10px' }}>
                      {animating ? '⏸ 暂停' : '▶ 播放'}
                    </button>
                    <button className="action-btn" onClick={() => setTimeStep(0)}
                      style={{ fontSize: 11, padding: '4px 10px' }}>
                      ⏮ 起点
                    </button>
                  </div>
                </div>
              )}
            </div>
          </div>
        )
      })}
    </div>
  )
}
