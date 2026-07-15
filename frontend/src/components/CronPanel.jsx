import React, { useState, useEffect, useCallback } from 'react'
import { fetchCronJobs, createCronJob, deleteCronJob, toggleCronJob } from '../api'

export default function CronPanel({ compact = false }) {
  const [jobs, setJobs] = useState([])
  const [loading, setLoading] = useState(false)
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState({ name: '', cron_expr: '', instruction: '' })

  const loadJobs = useCallback(async () => {
    try {
      setLoading(true)
      const data = await fetchCronJobs()
      setJobs(data)
    } catch (e) {
      console.error('Failed to load cron jobs:', e)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { loadJobs() }, [loadJobs])

  const handleCreate = async () => {
    if (!form.name || !form.cron_expr || !form.instruction) return
    try {
      await createCronJob(form)
      setForm({ name: '', cron_expr: '', instruction: '' })
      setShowForm(false)
      loadJobs()
    } catch (e) {
      alert('创建失败: ' + e.message)
    }
  }

  const handleDelete = async (jobId) => {
    if (!confirm('确定删除这个定时任务？')) return
    try {
      await deleteCronJob(jobId)
      loadJobs()
    } catch (e) {
      alert('删除失败: ' + e.message)
    }
  }

  const handleToggle = async (jobId, enabled) => {
    try {
      await toggleCronJob(jobId, enabled)
      loadJobs()
    } catch (e) {
      alert('操作失败: ' + e.message)
    }
  }

  const cronTips = [
    { label: '每天 22:00', value: '0 22 * * *' },
    { label: '工作日 8:00', value: '0 8 * * 1-5' },
    { label: '每小时', value: '0 * * * *' },
    { label: '每 30 分钟', value: '*/30 * * * *' },
    { label: '周六 2:00', value: '0 2 * * 6' },
  ]

  return (
    <div className="cron-panel">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
        {!compact && <h3 style={{ margin: 0 }}>⏰ 定时任务</h3>}
        {compact && <div />}
        <button className="action-btn primary" onClick={() => setShowForm(!showForm)}>
          {showForm ? '取消' : '+ 新建'}
        </button>
      </div>

      {showForm && (
        <div className="task-card" style={{ marginBottom: 12 }}>
          <div style={{ marginBottom: 8 }}>
            <label className="tool-label">任务名称</label>
            <input
              type="text"
              className="cron-input"
              placeholder="如：每晚充电"
              value={form.name}
              onChange={e => setForm({ ...form, name: e.target.value })}
            />
          </div>
          <div style={{ marginBottom: 8 }}>
            <label className="tool-label">Cron 表达式</label>
            <input
              type="text"
              className="cron-input"
              placeholder="如：0 22 * * *"
              value={form.cron_expr}
              onChange={e => setForm({ ...form, cron_expr: e.target.value })}
            />
            <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginTop: 4 }}>
              {cronTips.map(tip => (
                <button
                  key={tip.value}
                  className="action-btn"
                  style={{ fontSize: 10, padding: '2px 6px' }}
                  onClick={() => setForm({ ...form, cron_expr: tip.value })}
                >
                  {tip.label}
                </button>
              ))}
            </div>
          </div>
          <div style={{ marginBottom: 8 }}>
            <label className="tool-label">调度指令</label>
            <input
              type="text"
              className="cron-input"
              placeholder="如：所有机器人返回充电区"
              value={form.instruction}
              onChange={e => setForm({ ...form, instruction: e.target.value })}
            />
          </div>
          <button className="schedule-btn" onClick={handleCreate}>
            创建定时任务
          </button>
        </div>
      )}

      {loading && <div className="schedule-loading"><div className="spinner" /> 加载中...</div>}

      {!loading && jobs.length === 0 && (
        <div style={{ color: 'var(--text-secondary)', fontSize: 13, textAlign: 'center', padding: 20 }}>
          暂无定时任务，点击「+ 新建」创建一个
        </div>
      )}

      {jobs.map(job => (
        <div key={job.job_id} className="task-card" style={{ opacity: job.enabled ? 1 : 0.5 }}>
          <div className="task-header">
            <span className="robot-name">
              <span className="dot" style={{ background: job.enabled ? '#2ecc71' : '#95a5a6' }} />
              {job.name}
            </span>
            <span style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
              <code>{job.cron_expr}</code>
            </span>
          </div>
          <div style={{ fontSize: 12, color: 'var(--text)', marginBottom: 4 }}>
            {job.instruction}
          </div>
          <div style={{ fontSize: 10, color: 'var(--text-secondary)', marginBottom: 6 }}>
            {job.last_run_at
              ? `上次执行: ${new Date(job.last_run_at).toLocaleString('zh-CN')} ${job.last_result === 'succeeded' ? '✅' : '❌'}`
              : '尚未执行'}
          </div>
          <div style={{ display: 'flex', gap: 6 }}>
            <button
              className="action-btn"
              onClick={() => handleToggle(job.job_id, !job.enabled)}
            >
              {job.enabled ? '⏸ 禁用' : '▶ 启用'}
            </button>
            <button className="action-btn danger" onClick={() => handleDelete(job.job_id)}>
              🗑 删除
            </button>
          </div>
        </div>
      ))}
    </div>
  )
}
