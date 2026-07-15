import React from 'react'

const STATUS_LABELS = {
  succeeded: '✅ 全部成功',
  partially_succeeded: '⚠️ 部分成功',
  infeasible: '❌ 不可行',
}

export default function ResultsPanel({
  result, paths, timeStep, maxTimeStep, setTimeStep,
  animating, setAnimating, history = [], onSelectResult = null,
}) {
  if (history.length === 0 && !result) {
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
      <h3>📊 结果历史 ({history.length})</h3>

      {history.map((item, i) => (
        <div key={item.id || i} className="task-card" style={{
          cursor: 'pointer',
          opacity: result?.request_id === item.result?.request_id ? 1 : 0.6,
          borderColor: result?.request_id === item.result?.request_id ? 'var(--accent2)' : undefined,
        }} onClick={() => onSelectResult?.(item.result)}>
          <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 4 }}>
            {item.instruction.slice(0, 60)}
          </div>
          <div style={{ fontSize: 10, color: 'var(--text-secondary)' }}>
            {new Date(item.time).toLocaleString('zh-CN')} · {STATUS_LABELS[item.result?.batch_status] || '?'}
          </div>
        </div>
      ))}

      {result && (
        <>
          <h3 style={{ marginTop: 16 }}>当前选中</h3>
          <div className={`batch-status ${result.batch_status}`}>
            {STATUS_LABELS[result.batch_status] || result.batch_status}
          </div>

          {result.tasks?.map((task) => {
            const pathData = paths?.[task.robot_id]
            const color = pathData?.color || '#888'
            return (
              <div key={task.robot_id} className="task-card">
                <div className="task-header">
                  <span className="robot-name">
                    <span className="dot" style={{ background: color }} />{task.robot_id}
                  </span>
                  <span>{task.success ? '✅' : '❌'}</span>
                </div>
                <div className="task-stats">
                  {task.success ? (
                    <span>路径: {task.path?.length || 0} 步 · 耗时: {task.makespan}t</span>
                  ) : (
                    <span>失败: {task.failure_reason || '未知'}</span>
                  )}
                </div>
              </div>
            )
          })}

          {maxTimeStep > 0 && (
            <div className="time-slider-container">
              <label>时间步: {timeStep} / {maxTimeStep}</label>
              <input type="range" min={0} max={maxTimeStep} value={timeStep}
                onChange={e => setTimeStep(Number(e.target.value))} />
              <div className="time-slider-controls">
                <button className="action-btn" onClick={() => setAnimating(!animating)}>
                  {animating ? '⏸ 暂停' : '▶ 播放'}
                </button>
                <button className="action-btn" onClick={() => setTimeStep(0)}>⏮ 起点</button>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  )
}
