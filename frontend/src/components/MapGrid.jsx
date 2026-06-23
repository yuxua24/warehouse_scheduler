import React, { useRef, useEffect, useCallback } from 'react'

const COLORS = {
  walkable: '#1e1e3a',
  obstacle: '#2c3e50',
  facility: '#5d6d7e',
  entry: '#1a7a3a',
  corridor: '#1a3a5c',
  gridLine: '#2a2a4a',
  gridLineMajor: '#3a3a5a',
  robot: '#e74c3c',
  pathTrail: 'rgba(52,152,219,0.3)',
}

const ROBOT_COLORS = ['#e74c3c', '#2ecc71', '#3498db', '#f39c12', '#9b59b6']

export default function MapGrid({
  mapData,
  runtime,
  paths,
  timeStep,
  editMode,
  selectedLocationId,
  selectedRobotId,
  onCellClick,
}) {
  const canvasRef = useRef(null)
  const containerRef = useRef(null)
  const hoveredCell = useRef(null)

  const CELL_SIZE = 28
  const GRID_PAD = 20

  const getWidth = useCallback(() => {
    if (!mapData) return 400
    return mapData.map.width * CELL_SIZE + GRID_PAD * 2
  }, [mapData])

  const getHeight = useCallback(() => {
    if (!mapData) return 400
    return mapData.map.height * CELL_SIZE + GRID_PAD * 2
  }, [mapData])

  // Build lookup maps
  const buildMaps = useCallback(() => {
    if (!mapData) return { obstacleSet: new Set(), facilitySet: new Set(), entrySet: new Set(), corridorSet: new Set(), locationMap: {}, entryToLocation: {} }

    const obstacleSet = new Set()
    mapData.static_obstacles?.forEach(obs =>
      obs.cells?.forEach(c => obstacleSet.add(`${c[0]},${c[1]}`))
    )

    const facilitySet = new Set()
    const entrySet = new Set()
    const locationMap = {}
    const entryToLocation = {}

    mapData.locations?.forEach(loc => {
      locationMap[loc.location_id] = loc
      loc.facility_cells?.forEach(c => {
        facilitySet.add(`${c[0]},${c[1]}`)
      })
      loc.entry_cells?.forEach(c => {
        entrySet.add(`${c[0]},${c[1]}`)
        entryToLocation[`${c[0]},${c[1]}`] = loc.location_id
      })
    })

    const corridorSet = new Set()
    mapData.corridors?.forEach(cor =>
      cor.cells?.forEach(c => corridorSet.add(`${c[0]},${c[1]}`))
    )

    return { obstacleSet, facilitySet, entrySet, corridorSet, locationMap, entryToLocation }
  }, [mapData])

  // Get robot positions at current time step
  const getRobotPositions = useCallback(() => {
    const positions = {}
    if (paths) {
      Object.entries(paths).forEach(([rid, data]) => {
        if (data.path && data.path.length > 0) {
          let pos = data.path[0]
          for (const p of data.path) {
            if (p.time === timeStep) { pos = p; break }
            if (p.time > timeStep) break
            pos = p
          }
          positions[rid] = { x: pos.x, y: pos.y, color: data.color }
        }
      })
    }
    // Also show runtime robot positions if no paths
    if (Object.keys(positions).length === 0 && runtime?.robots) {
      runtime.robots.forEach((r, i) => {
        positions[r.robot_id] = {
          x: r.position[0],
          y: r.position[1],
          color: ROBOT_COLORS[i % ROBOT_COLORS.length],
        }
      })
    }
    return positions
  }, [paths, timeStep, runtime])

  // Drag support for painting obstacles
  const isMouseDown = useRef(false)

  const draw = useCallback(() => {
    const canvas = canvasRef.current
    if (!canvas || !mapData) return

    const ctx = canvas.getContext('2d')
    const { width: mapW, height: mapH } = mapData.map
    const w = mapW * CELL_SIZE + GRID_PAD * 2
    const h = mapH * CELL_SIZE + GRID_PAD * 2
    canvas.width = w
    canvas.height = h

    const { obstacleSet, facilitySet, entrySet, corridorSet, locationMap } = buildMaps()
    const robotPositions = getRobotPositions()
    const hovered = hoveredCell.current

    // Background
    ctx.fillStyle = COLORS.walkable
    ctx.fillRect(0, 0, w, h)

    // Draw cells
    for (let x = 0; x < mapW; x++) {
      for (let y = 0; y < mapH; y++) {
        const cx = GRID_PAD + x * CELL_SIZE
        const cy = GRID_PAD + y * CELL_SIZE
        const key = `${x},${y}`

        let fill = COLORS.walkable

        if (obstacleSet.has(key)) {
          fill = COLORS.obstacle
        } else if (facilitySet.has(key)) {
          fill = COLORS.facility
        } else if (entrySet.has(key)) {
          fill = COLORS.entry
        } else if (corridorSet.has(key)) {
          fill = COLORS.corridor
        }

        ctx.fillStyle = fill
        ctx.fillRect(cx + 1, cy + 1, CELL_SIZE - 2, CELL_SIZE - 2)

        // Grid lines
        ctx.strokeStyle = COLORS.gridLine
        ctx.lineWidth = 0.5
        ctx.strokeRect(cx, cy, CELL_SIZE, CELL_SIZE)
      }
    }

    // Draw major grid lines (every 5)
    ctx.strokeStyle = COLORS.gridLineMajor
    ctx.lineWidth = 1
    for (let x = 0; x <= mapW; x += 5) {
      const cx = GRID_PAD + x * CELL_SIZE
      ctx.beginPath()
      ctx.moveTo(cx, GRID_PAD)
      ctx.lineTo(cx, GRID_PAD + mapH * CELL_SIZE)
      ctx.stroke()
    }
    for (let y = 0; y <= mapH; y += 5) {
      const cy = GRID_PAD + y * CELL_SIZE
      ctx.beginPath()
      ctx.moveTo(GRID_PAD, cy)
      ctx.lineTo(GRID_PAD + mapW * CELL_SIZE, cy)
      ctx.stroke()
    }

    // Draw coordinate labels
    ctx.fillStyle = COLORS.gridLineMajor
    ctx.font = '9px monospace'
    ctx.textAlign = 'center'
    for (let x = 0; x < mapW; x += 5) {
      ctx.fillText(`${x}`, GRID_PAD + x * CELL_SIZE + CELL_SIZE / 2, GRID_PAD - 4)
    }
    ctx.textAlign = 'right'
    for (let y = 0; y < mapH; y += 5) {
      ctx.fillText(`${y}`, GRID_PAD - 4, GRID_PAD + y * CELL_SIZE + CELL_SIZE / 2 + 3)
    }

    // Draw location labels on facility cells
    ctx.font = '8px sans-serif'
    ctx.textAlign = 'center'
    ctx.fillStyle = '#ddd'
    for (const loc of (mapData.locations || [])) {
      if (loc.facility_cells && loc.facility_cells.length > 0) {
        const [fx, fy] = loc.facility_cells[0]
        const cx = GRID_PAD + fx * CELL_SIZE + CELL_SIZE / 2
        const cy = GRID_PAD + fy * CELL_SIZE + CELL_SIZE / 2
        // Truncate name
        const name = loc.name.length > 3 ? loc.name.slice(0, 3) : loc.name
        ctx.fillText(name, cx, cy + 3)
      }
    }

    // Draw path trails
    if (paths) {
      Object.entries(paths).forEach(([rid, data]) => {
        if (!data.path || data.path.length === 0) return
        const color = data.color
        // Draw trail up to current timeStep
        ctx.strokeStyle = color
        ctx.lineWidth = 2
        ctx.lineCap = 'round'
        ctx.beginPath()
        let started = false
        for (const p of data.path) {
          if (p.time > timeStep) break
          const px = GRID_PAD + p.x * CELL_SIZE + CELL_SIZE / 2
          const py = GRID_PAD + p.y * CELL_SIZE + CELL_SIZE / 2
          if (!started) { ctx.moveTo(px, py); started = true }
          else ctx.lineTo(px, py)
        }
        ctx.stroke()

        // Draw start and goal
        if (data.path.length > 0 && timeStep >= 0) {
          const start = data.path[0]
          const goal = data.path[data.path.length - 1]
          // Start marker
          ctx.fillStyle = color
          ctx.strokeStyle = 'white'
          ctx.lineWidth = 1.5
          ctx.beginPath()
          ctx.arc(
            GRID_PAD + start.x * CELL_SIZE + CELL_SIZE / 2,
            GRID_PAD + start.y * CELL_SIZE + CELL_SIZE / 2,
            CELL_SIZE / 2 - 3, 0, Math.PI * 2
          )
          ctx.fill()
          ctx.stroke()
          // Goal marker
          ctx.fillStyle = color
          ctx.beginPath()
          ctx.arc(
            GRID_PAD + goal.x * CELL_SIZE + CELL_SIZE / 2,
            GRID_PAD + goal.y * CELL_SIZE + CELL_SIZE / 2,
            CELL_SIZE / 2 - 3, 0, Math.PI * 2
          )
          ctx.fill()
          ctx.stroke()
        }
      })
    }

    // Draw robots at current time step
    Object.entries(robotPositions).forEach(([rid, pos]) => {
      const cx = GRID_PAD + pos.x * CELL_SIZE + CELL_SIZE / 2
      const cy = GRID_PAD + pos.y * CELL_SIZE + CELL_SIZE / 2
      const r = CELL_SIZE / 2 - 2

      // Robot circle
      ctx.fillStyle = pos.color
      ctx.beginPath()
      ctx.arc(cx, cy, r, 0, Math.PI * 2)
      ctx.fill()
      ctx.strokeStyle = 'white'
      ctx.lineWidth = 2
      ctx.stroke()

      // Robot label
      ctx.fillStyle = 'white'
      ctx.font = 'bold 10px sans-serif'
      ctx.textAlign = 'center'
      ctx.textBaseline = 'middle'
      ctx.fillText(rid, cx, cy)
    })

    // Draw hover highlight
    if (hovered && editMode !== 'view') {
      const cx = GRID_PAD + hovered.x * CELL_SIZE
      const cy = GRID_PAD + hovered.y * CELL_SIZE
      ctx.strokeStyle = editMode === 'delete' ? '#e94560' : '#3498db'
      ctx.lineWidth = 2
      ctx.strokeRect(cx + 1, cy + 1, CELL_SIZE - 2, CELL_SIZE - 2)
    }
  }, [mapData, paths, timeStep, editMode, buildMaps, getRobotPositions])

  useEffect(() => {
    draw()
  }, [draw])

  // Handle click
  const handleClick = useCallback((e) => {
    const canvas = canvasRef.current
    if (!canvas || !mapData) return
    const rect = canvas.getBoundingClientRect()
    const scaleX = canvas.width / rect.width
    const scaleY = canvas.height / rect.height
    const mx = (e.clientX - rect.left) * scaleX
    const my = (e.clientY - rect.top) * scaleY
    const x = Math.floor((mx - GRID_PAD) / CELL_SIZE)
    const y = Math.floor((my - GRID_PAD) / CELL_SIZE)
    if (x >= 0 && x < mapData.map.width && y >= 0 && y < mapData.map.height) {
      onCellClick?.(x, y)
    }
  }, [mapData, onCellClick])

  // Handle hover
  const handleMouseMove = useCallback((e) => {
    const canvas = canvasRef.current
    if (!canvas || !mapData) return
    const rect = canvas.getBoundingClientRect()
    const scaleX = canvas.width / rect.width
    const scaleY = canvas.height / rect.height
    const mx = (e.clientX - rect.left) * scaleX
    const my = (e.clientY - rect.top) * scaleY
    const x = Math.floor((mx - GRID_PAD) / CELL_SIZE)
    const y = Math.floor((my - GRID_PAD) / CELL_SIZE)
    if (x >= 0 && x < mapData.map.width && y >= 0 && y < mapData.map.height) {
      hoveredCell.current = { x, y }
    } else {
      hoveredCell.current = null
    }
    draw()
    // Paint while dragging
    if (isMouseDown.current && hoveredCell.current && editMode !== 'view') {
      onCellClick?.(hoveredCell.current.x, hoveredCell.current.y)
    }
  }, [mapData, editMode, onCellClick, draw])

  const handleMouseDown = useCallback(() => { isMouseDown.current = true }, [])
  const handleMouseUp = useCallback(() => { isMouseDown.current = false }, [])
  const handleMouseLeave = useCallback(() => {
    isMouseDown.current = false
    hoveredCell.current = null
    draw()
  }, [draw])

  const { obstacleSet, facilitySet, entrySet, corridorSet } = buildMaps()

  // Build cell info tooltip content
  const getCellInfo = useCallback((x, y) => {
    const key = `${x},${y}`
    if (obstacleSet.has(key)) return '静态障碍物'
    if (facilitySet.has(key)) return '设施本体'
    if (entrySet.has(key)) return '入口格'
    if (corridorSet.has(key)) return '通道'
    return '可行走'
  }, [obstacleSet, facilitySet, entrySet, corridorSet])

  return (
    <div className="map-grid-container" ref={containerRef}>
      <canvas
        ref={canvasRef}
        width={getWidth()}
        height={getHeight()}
        style={{ maxWidth: '100%', maxHeight: 'calc(100vh - 120px)' }}
        onClick={handleClick}
        onMouseMove={handleMouseMove}
        onMouseDown={handleMouseDown}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseLeave}
      />
      <div className="map-legend">
        <div className="legend-item">
          <div className="legend-swatch" style={{background: COLORS.walkable}} />
          可行走
        </div>
        <div className="legend-item">
          <div className="legend-swatch" style={{background: COLORS.obstacle}} />
          障碍物
        </div>
        <div className="legend-item">
          <div className="legend-swatch" style={{background: COLORS.facility}} />
          设施
        </div>
        <div className="legend-item">
          <div className="legend-swatch" style={{background: COLORS.entry}} />
          入口
        </div>
        <div className="legend-item">
          <div className="legend-swatch" style={{background: COLORS.corridor}} />
          通道
        </div>
        <div className="legend-item">
          <div className="legend-swatch" style={{background: '#e74c3c', borderRadius: '50%'}} />
          机器人
        </div>
      </div>
    </div>
  )
}
