import React from 'react'

const STATUS_LABELS = {
  succeeded: '✅ 全部成功',
  partially_succeeded: '⚠️ 部分成功',
  infeasible: '❌ 不可行',
}

export default function ResultsPanel({
  result,
  paths,
  timeStep,
  maxTimeStep,
  setTimeStep,
  animating,
  setAnimating,
}) {
  if (!result) {
    return (
      <div className="results-panel">
        <h3>规划结果</h3>
        <p style={{ color: 'var(--text-secondary)', fontSize: 13 }}>
          暂无结果。请在"调度指令"面板中输入指令并提交。
        </p>
      </div>
    )
  }

  return (
    <div className="results-panel">
      <h3>规划结果</h3>

      <div className={`batch-status ${result.batch_status}`}>
        {STATUS_LABELS[result.batch_status] || result.batch_status}
      </div>

      {/* Task cards */}
      {result.tasks?.map((task, i) => {
        const pathData = paths?.[task.robot_id]
        const color = pathData?.color || '#888'
        return (
          <div key={task.robot_id} className="task-card">
            <div className="task-header">
              <span className="robot-name">
                <span className="dot" style={{ background: color }} />
                {task.robot_id}
                {task.replanned && ' 🔄'}
              </span>
              <span style={{ fontSize: 12, color: task.success ? 'var(--success)' : 'var(--accent)' }}>
                {task.success ? '✅' : '❌'}
              </span>
            </div>
            <div className="task-stats">
              {task.success ? (
                <>
                  <span>路径长度: {task.path?.length || 0}</span>
                  <span>耗时: {task.makespan}步</span>
                  <span>
                    起: [{task.start?.[0]},{task.start?.[1]}]
                    → [{task.goal?.[0]},{task.goal?.[1]}]
                  </span>
                </>
              ) : (
                <span>失败原因: {task.failure_reason || '未知'}</span>
              )}
            </div>
          </div>
        )
      })}

      {/* Time slider for animation */}
      {maxTimeStep > 0 && (
        <div className="time-slider-container">
          <label>时间步: {timeStep} / {maxTimeStep}</label>
          <input
            type="range"
            min={0}
            max={maxTimeStep}
            value={timeStep}
            onChange={e => setTimeStep(parseInt(e.target.value))}
          />
          <div className="time-slider-controls">
            <button
              className="action-btn"
              onClick={() => setTimeStep(Math.max(0, timeStep - 1))}
            >
              ⏮
            </button>
            <button
              className={`action-btn ${animating ? 'danger' : 'primary'}`}
              onClick={() => setAnimating(!animating)}
            >
              {animating ? '⏹ 停止' : '▶ 播放'}
            </button>
            <button
              className="action-btn"
              onClick={() => setTimeStep(Math.min(maxTimeStep, timeStep + 1))}
            >
              ⏭
            </button>
            <button
              className="action-btn"
              onClick={() => setTimeStep(0)}
            >
              ⏪
            </button>
            <button
              className="action-btn"
              onClick={() => setTimeStep(maxTimeStep)}
            >
              ⏩
            </button>
          </div>
        </div>
      )}

      {/* Metrics */}
      {result.metrics && (
        <>
          <h4 style={{ marginTop: 14, fontSize: 13 }}>规划指标</h4>
          <div className="metrics-grid">
            <div className="metric-item">
              <div className="metric-value">
                {result.metrics.planned_task_count}/{result.metrics.total_task_count}
              </div>
              <div className="metric-label">成功/总数</div>
            </div>
            <div className="metric-item">
              <div className="metric-value">
                {(result.metrics.planning_success_rate * 100).toFixed(0)}%
              </div>
              <div className="metric-label">成功率</div>
            </div>
            <div className="metric-item">
              <div className="metric-value">
                {result.metrics.total_planning_time_ms.toFixed(0)}ms
              </div>
              <div className="metric-label">总耗时</div>
            </div>
            <div className="metric-item">
              <div className="metric-value">
                {result.metrics.astar_call_count}
              </div>
              <div className="metric-label">A* 调用次数</div>
            </div>
            <div className="metric-item">
              <div className="metric-value">
                {result.metrics.retry_count}
              </div>
              <div className="metric-label">重规划次数</div>
            </div>
            <div className="metric-item">
              <div className="metric-value">
                {result.metrics.total_expanded_nodes}
              </div>
              <div className="metric-label">展开节点数</div>
            </div>
            <div className="metric-item">
              <div className="metric-value">
                {result.metrics.initial_conflict_count}
              </div>
              <div className="metric-label">初始冲突数</div>
            </div>
            <div className="metric-item">
              <div className="metric-value">
                {result.metrics.final_conflict_count}
              </div>
              <div className="metric-label">最终冲突数</div>
            </div>
          </div>
        </>
      )}

      {/* Warnings & Errors */}
      {result.warnings?.length > 0 && (
        <div className="diag-list">
          <div style={{ fontSize: 12, fontWeight: 600, margin: '8px 0 4px' }}>⚠️ 警告</div>
          {result.warnings.map((w, i) => (
            <div key={i} className="diag-item warning">{w}</div>
          ))}
        </div>
      )}

      {result.errors?.length > 0 && (
        <div className="diag-list">
          <div style={{ fontSize: 12, fontWeight: 600, margin: '8px 0 4px' }}>❌ 错误</div>
          {result.errors.map((e, i) => (
            <div key={i} className="diag-item error">{e}</div>
          ))}
        </div>
      )}

      {/* Replan history */}
      {result.replan_history?.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 4 }}>重规划历史</div>
          {result.replan_history.map((h, i) => (
            <div key={i} style={{
              fontSize: 11,
              color: 'var(--text-secondary)',
              padding: '4px 8px',
              background: 'var(--bg-card)',
              borderRadius: 3,
              marginBottom: 3,
            }}>
              <strong>#{h.retry_index}</strong> {h.action}: {h.explanation}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
