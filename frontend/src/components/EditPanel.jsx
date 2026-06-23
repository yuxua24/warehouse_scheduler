import React from 'react'

export default function EditPanel({
  editMode,
  setEditMode,
  selectedLocationId,
  setSelectedLocationId,
  selectedRobotId,
  setSelectedRobotId,
  mapData,
  runtime,
  onSaveMap,
  onSaveRuntime,
  onReload,
}) {
  const modes = [
    { id: 'view', label: '👁️ 查看模式', desc: '查看地图和路径' },
    { id: 'obstacle', label: '🧱 编辑障碍物', desc: '点击格子添加/移除障碍物' },
    { id: 'delete', label: '🗑️ 删除模式', desc: '点击清除障碍/入口/设施格' },
    { id: 'entry', label: '🚪 添加入口', desc: '为选中位置添加入口格' },
    { id: 'facility', label: '🏗️ 添加设施', desc: '为选中位置添加设施格' },
    { id: 'robot', label: '🤖 设置机器人位置', desc: '点击放置选中的机器人' },
  ]

  const locations = mapData?.locations || []
  const robots = runtime?.robots || []

  return (
    <div className="edit-tools">
      <h3>地图编辑工具</h3>

      <div className="tool-group">
        <div className="tool-label">编辑模式</div>
        {modes.map(m => (
          <button
            key={m.id}
            className={`mode-btn ${editMode === m.id ? 'active' : ''}`}
            onClick={() => setEditMode(m.id)}
            title={m.desc}
          >
            {m.label}
          </button>
        ))}
      </div>

      {(editMode === 'entry' || editMode === 'facility') && (
        <div className="tool-group">
          <div className="tool-label">选择位置</div>
          <select
            value={selectedLocationId || ''}
            onChange={e => setSelectedLocationId(e.target.value || null)}
          >
            <option value="">-- 选择位置 --</option>
            {locations.map(loc => (
              <option key={loc.location_id} value={loc.location_id}>
                {loc.name} ({loc.location_id})
              </option>
            ))}
          </select>
        </div>
      )}

      {editMode === 'robot' && (
        <div className="tool-group">
          <div className="tool-label">选择机器人</div>
          <select
            value={selectedRobotId || ''}
            onChange={e => setSelectedRobotId(e.target.value || null)}
          >
            <option value="">-- 选择机器人 --</option>
            {robots.map(r => (
              <option key={r.robot_id} value={r.robot_id}>
                {r.robot_id} (当前位置: [{r.position?.[0]}, {r.position?.[1]}])
              </option>
            ))}
          </select>
        </div>
      )}

      <div className="tool-group">
        <div className="tool-label">当前地图信息</div>
        <div style={{ fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.6 }}>
          <div>尺寸: {mapData?.map?.width}×{mapData?.map?.height}</div>
          <div>障碍物: {mapData?.static_obstacles?.length || 0} 组</div>
          <div>位置: {locations.length}</div>
          <div>通道: {mapData?.corridors?.length || 0}</div>
          <div>机器人: {robots.length}</div>
          {robots.map(r => (
            <div key={r.robot_id} style={{ marginLeft: 8 }}>
              {r.robot_id}: [{r.position?.[0]}, {r.position?.[1]}] {r.enabled ? '✓' : '✗'}
            </div>
          ))}
        </div>
      </div>

      <div className="tool-group">
        <button className="action-btn primary" onClick={onSaveMap}>💾 保存地图</button>
        <button className="action-btn primary" onClick={onSaveRuntime}>💾 保存运行时</button>
        <button className="action-btn" onClick={onReload}>🔄 重新加载</button>
      </div>
    </div>
  )
}
