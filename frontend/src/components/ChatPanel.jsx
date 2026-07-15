import React, { useState, useRef, useEffect, useCallback } from 'react'
import { sendChat, confirmChat } from '../api'

export default function ChatPanel({ onScheduleResult, cronJobs, onCronChange }) {
  const [messages, setMessages] = useState([
    { role: 'bot', text: '🤖 你好！我是仓储调度助手。\n\n试试说：\n• R1前往装卸区，R2前往货架B\n• 输出当前定时任务\n• 每天晚上十点所有机器人回充电区\n• 删除所有定时任务', time: new Date() }
  ])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [confirmPending, setConfirmPending] = useState(null)
  const [lightbox, setLightbox] = useState(null)
  const chatEndRef = useRef(null)

  useEffect(() => { chatEndRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [messages])

  const handleSend = useCallback(async () => {
    const text = input.trim()
    if (!text || loading) return
    setInput('')
    setLoading(true)
    setMessages(prev => [...prev, { role: 'user', text, time: new Date() }])

    try {
      const result = await sendChat(text)
      setMessages(prev => [...prev, {
        role: 'bot', text: result.reply, time: new Date(),
        image: result.image_url || null,
      }])
      if (result.schedule && onScheduleResult) onScheduleResult(result.schedule, text)
      if (result.cron_jobs && onCronChange) onCronChange(result.cron_jobs)
      setConfirmPending(result.confirm_needed || null)
    } catch (e) {
      setMessages(prev => [...prev, { role: 'bot', text: '❌ 请求失败: ' + e.message, time: new Date() }])
    } finally {
      setLoading(false)
    }
  }, [input, loading, onScheduleResult, onCronChange])

  const handleConfirm = useCallback(async (confirmed) => {
    setLoading(true)
    setConfirmPending(null)
    const replyText = confirmed ? '确认' : '取消'
    setMessages(prev => [...prev, { role: 'user', text: replyText, time: new Date() }])
    try {
      const result = await confirmChat(confirmed)
      setMessages(prev => [...prev, { role: 'bot', text: result.reply, time: new Date() }])
      if (result.cron_jobs && onCronChange) onCronChange(result.cron_jobs)
    } catch (e) {
      setMessages(prev => [...prev, { role: 'bot', text: '❌ 操作失败: ' + e.message, time: new Date() }])
    } finally {
      setLoading(false)
    }
  }, [loading, onCronChange])

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend() }
  }

  const quickActions = [
    { label: '查看定时任务', text: '输出当前定时任务' },
    { label: 'R1去装卸区', text: 'R1前往装卸区，R2前往货架B' },
    { label: '删除全部定时', text: '删除所有定时任务' },
  ]

  return (
    <div className="chat-panel" style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div className="chat-messages" style={{ flex: 1, overflow: 'auto', padding: '8px 0' }}>
        {messages.map((msg, i) => (
          <div key={i} className={`chat-msg ${msg.role}`} style={{
            marginBottom: 10, display: 'flex', flexDirection: 'column',
            alignItems: msg.role === 'user' ? 'flex-end' : 'flex-start',
          }}>
            <div style={{
              maxWidth: '85%', padding: '8px 12px',
              borderRadius: msg.role === 'user' ? '12px 12px 2px 12px' : '2px 12px 12px 12px',
              background: msg.role === 'user' ? 'var(--accent2)' : 'var(--bg-card)',
              color: msg.role === 'user' ? '#fff' : 'var(--text)',
              fontSize: 13, lineHeight: 1.5, whiteSpace: 'pre-wrap', wordBreak: 'break-word',
            }}>
              <span dangerouslySetInnerHTML={{
                __html: msg.text.replace(/\*\*(.*?)\*\*/g, '<b>$1</b>')
              }} />
              {msg.image && (
                <img src={msg.image} alt="路径图" style={{
                  marginTop: 8, maxWidth: '100%', borderRadius: 6, cursor: 'pointer',
                  border: '1px solid var(--border)',
                }} onClick={() => setLightbox(msg.image)} />
              )}
            </div>
            <span style={{ fontSize: 9, color: 'var(--text-secondary)', marginTop: 2 }}>
              {msg.time.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}
            </span>
          </div>
        ))}
        {loading && (
          <div className="chat-msg bot" style={{ display: 'flex', alignItems: 'center', gap: 6, padding: 8 }}>
            <div className="spinner" style={{ width: 14, height: 14, borderWidth: 2 }} />
            <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>思考中...</span>
          </div>
        )}
        {confirmPending && (
          <div style={{ display: 'flex', gap: 8, padding: 8, justifyContent: 'center' }}>
            <button className="action-btn primary" onClick={() => handleConfirm(true)} style={{ padding: '6px 20px' }}>
              ✅ 确认删除
            </button>
            <button className="action-btn danger" onClick={() => handleConfirm(false)} style={{ padding: '6px 20px' }}>
              ❌ 取消
            </button>
          </div>
        )}
        <div ref={chatEndRef} />
      </div>

      <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginBottom: 8 }}>
        {quickActions.map(a => (
          <button key={a.label} className="action-btn" style={{ fontSize: 10, padding: '3px 8px' }}
            onClick={() => setInput(a.text)}>{a.label}</button>
        ))}
      </div>

      <div style={{ display: 'flex', gap: 6 }}>
        <input type="text" className="cron-input" style={{ flex: 1 }}
          placeholder="输入调度指令或定时任务..." value={input}
          onChange={e => setInput(e.target.value)} onKeyDown={handleKeyDown} disabled={loading} />
        <button className="schedule-btn" style={{ width: 60, padding: 8 }} onClick={handleSend} disabled={loading}>
          {loading ? '...' : '发送'}
        </button>
      </div>
      {lightbox && (
        <div style={{
          position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
          background: 'rgba(0,0,0,0.85)', zIndex: 9999,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          cursor: 'pointer',
        }} onClick={() => setLightbox(null)}>
          <img src={lightbox} alt="放大" style={{
            maxWidth: '90vw', maxHeight: '90vh', borderRadius: 8,
            boxShadow: '0 0 40px rgba(0,0,0,0.5)',
          }} />
          <span style={{
            position: 'absolute', top: 16, right: 24,
            color: '#fff', fontSize: 28, cursor: 'pointer',
          }}>✕</span>
        </div>
      )}
    </div>
  )
}
