import React, { useState, useCallback, useEffect, useRef } from 'react'
import MapGrid from './components/MapGrid'
import EditPanel from './components/EditPanel'
import SchedulePanel from './components/SchedulePanel'
import ResultsPanel from './components/ResultsPanel'
import { fetchMap, updateMap, updateRuntime } from './api'

const ROBOT_COLORS = ['#e74c3c', '#2ecc71', '#3498db', '#f39c12', '#9b59b6']

export default function App() {
  const [mapData, setMapData] = useState(null)
  const [runtime, setRuntime] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  // Scheduling state
  const [scheduleResult, setScheduleResult] = useState(null)
  const [scheduleLoading, setScheduleLoading] = useState(false)

  // UI state
  const [activeTab, setActiveTab] = useState('edit') // 'edit' | 'schedule' | 'results'
  const [editMode, setEditMode] = useState('view') // 'view' | 'obstacle' | 'entry' | 'facility' | 'robot'
  const [selectedLocationId, setSelectedLocationId] = useState(null)
  const [selectedRobotId, setSelectedRobotId] = useState(null)
  const [timeStep, setTimeStep] = useState(0)
  const [maxTimeStep, setMaxTimeStep] = useState(0)
  const [animating, setAnimating] = useState(false)
  const animRef = useRef(null)

  // Path overlay data (derived from scheduleResult)
  const [paths, setPaths] = useState({})

  // Load initial data
  useEffect(() => {
    loadData()
  }, [])

  async function loadData() {
    try {
      setLoading(true)
      const [map, rt] = await Promise.all([
        fetchMap(),
        fetch('/api/runtime').then(r => r.ok ? r.json() : null),
      ])
      setMapData(map)
      setRuntime(rt)
      setError(null)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  // Update paths when schedule result changes
  useEffect(() => {
    if (scheduleResult?.tasks) {
      const newPaths = {}
      let maxT = 0
      scheduleResult.tasks.forEach((task, i) => {
        if (task.path && task.path.length > 0) {
          newPaths[task.robot_id] = {
            path: task.path,
            color: ROBOT_COLORS[i % ROBOT_COLORS.length],
            success: task.success,
            start: task.path[0],
            goal: task.path[task.path.length - 1],
          }
          if (task.path[task.path.length - 1].time > maxT) {
            maxT = task.path[task.path.length - 1].time
          }
        }
      })
      setPaths(newPaths)
      setMaxTimeStep(maxT)
      setTimeStep(0)
      setActiveTab('results')
    }
  }, [scheduleResult])

  // Animation
  useEffect(() => {
    if (animating) {
      animRef.current = setInterval(() => {
        setTimeStep(prev => {
          if (prev >= maxTimeStep) {
            setAnimating(false)
            return prev
          }
          return prev + 1
        })
      }, 400)
      return () => clearInterval(animRef.current)
    } else {
      if (animRef.current) clearInterval(animRef.current)
    }
  }, [animating, maxTimeStep])

  const handleScheduleResult = useCallback((result) => {
    setScheduleResult(result)
  }, [])

  const handleMapUpdate = useCallback((updatedMap) => {
    setMapData(updatedMap)
  }, [])

  if (loading) {
    return (
      <div className="app-loading">
        <div className="spinner" />
        <p>加载地图数据...</p>
      </div>
    )
  }

  if (error && !mapData) {
    return (
      <div className="app-error">
        <h2>加载失败</h2>
        <p>{error}</p>
        <button onClick={loadData}>重试</button>
      </div>
    )
  }

  return (
    <div className="app">
      <header className="app-header">
        <h1>🤖 智能仓储机器人调度系统</h1>
        <span className="app-version">v0.1.0</span>
      </header>

      <div className="app-body">
        <div className="map-area">
          <MapGrid
            mapData={mapData}
            runtime={runtime}
            paths={paths}
            timeStep={timeStep}
            editMode={editMode}
            selectedLocationId={selectedLocationId}
            selectedRobotId={selectedRobotId}
            onCellClick={(x, y) => handleCellClick(x, y, mapData, setMapData, editMode, selectedLocationId, selectedRobotId, runtime, setRuntime)}
          />
        </div>

        <div className="side-panel">
          <div className="tab-bar">
            <button
              className={`tab ${activeTab === 'edit' ? 'active' : ''}`}
              onClick={() => setActiveTab('edit')}
            >
              🗺️ 地图编辑
            </button>
            <button
              className={`tab ${activeTab === 'schedule' ? 'active' : ''}`}
              onClick={() => setActiveTab('schedule')}
            >
              📋 调度指令
            </button>
            <button
              className={`tab ${activeTab === 'results' ? 'active' : ''}`}
              onClick={() => setActiveTab('results')}
            >
              📊 结果
            </button>
          </div>

          <div className="panel-content">
            {activeTab === 'edit' && (
              <EditPanel
                editMode={editMode}
                setEditMode={setEditMode}
                selectedLocationId={selectedLocationId}
                setSelectedLocationId={setSelectedLocationId}
                selectedRobotId={selectedRobotId}
                setSelectedRobotId={setSelectedRobotId}
                mapData={mapData}
                runtime={runtime}
                onSaveMap={async () => {
                  try {
                    await updateMap(mapData)
                    alert('地图已保存')
                  } catch (e) {
                    alert('保存失败: ' + e.message)
                  }
                }}
                onSaveRuntime={async () => {
                  try {
                    await updateRuntime(runtime)
                    alert('运行时状态已保存')
                  } catch (e) {
                    alert('保存失败: ' + e.message)
                  }
                }}
                onReload={loadData}
              />
            )}

            {activeTab === 'schedule' && (
              <SchedulePanel
                onResult={handleScheduleResult}
                loading={scheduleLoading}
                setLoading={setScheduleLoading}
              />
            )}

            {activeTab === 'results' && (
              <ResultsPanel
                result={scheduleResult}
                paths={paths}
                timeStep={timeStep}
                maxTimeStep={maxTimeStep}
                setTimeStep={setTimeStep}
                animating={animating}
                setAnimating={setAnimating}
              />
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

// Handle cell clicks based on edit mode
function handleCellClick(x, y, mapData, setMapData, editMode, selectedLocationId, selectedRobotId, runtime, setRuntime) {
  if (!mapData) return

  if (editMode === 'obstacle') {
    // Toggle obstacle cell
    const newMap = JSON.parse(JSON.stringify(mapData))
    const obsSet = new Set()
    newMap.static_obstacles.forEach(obs => obs.cells.forEach(c => obsSet.add(`${c[0]},${c[1]}`)))
    
    if (obsSet.has(`${x},${y}`)) {
      // Remove from obstacles
      newMap.static_obstacles.forEach(obs => {
        obs.cells = obs.cells.filter(c => !(c[0] === x && c[1] === y))
      })
      newMap.static_obstacles = newMap.static_obstacles.filter(obs => obs.cells.length > 0)
    } else {
      // Add as obstacle - create a new obstacle or add to existing
      let added = false
      for (const obs of newMap.static_obstacles) {
        // Check if adjacent to any cell in this obstacle
        const isAdj = obs.cells.some(c => Math.abs(c[0] - x) + Math.abs(c[1] - y) === 1)
        if (isAdj && obs.type !== 'wall') {
          obs.cells.push([x, y])
          added = true
          break
        }
      }
      if (!added) {
        newMap.static_obstacles.push({
          obstacle_id: `obstacle_${x}_${y}`,
          type: 'obstacle',
          cells: [[x, y]],
        })
      }
    }
    setMapData(newMap)
  } else if (editMode === 'entry' && selectedLocationId) {
    // Add entry cell to selected location
    const newMap = JSON.parse(JSON.stringify(mapData))
    const loc = newMap.locations.find(l => l.location_id === selectedLocationId)
    if (loc) {
      const exists = loc.entry_cells.some(c => c[0] === x && c[1] === y)
      if (!exists) {
        loc.entry_cells.push([x, y])
        setMapData(newMap)
      }
    }
  } else if (editMode === 'facility' && selectedLocationId) {
    // Add facility cell to selected location
    const newMap = JSON.parse(JSON.stringify(mapData))
    const loc = newMap.locations.find(l => l.location_id === selectedLocationId)
    if (loc) {
      const exists = loc.facility_cells.some(c => c[0] === x && c[1] === y)
      if (!exists) {
        loc.facility_cells.push([x, y])
        setMapData(newMap)
      }
    }
  } else if (editMode === 'robot' && selectedRobotId) {
    // Set robot position
    const newRuntime = JSON.parse(JSON.stringify(runtime || {}))
    const robot = (newRuntime.robots || []).find(r => r.robot_id === selectedRobotId)
    if (robot) {
      robot.position = [x, y]
      setRuntime(newRuntime)
    }
  } else if (editMode === 'delete') {
    // Remove from obstacles, entry cells, facility cells
    const newMap = JSON.parse(JSON.stringify(mapData))
    // Remove from obstacles
    newMap.static_obstacles.forEach(obs => {
      obs.cells = obs.cells.filter(c => !(c[0] === x && c[1] === y))
    })
    newMap.static_obstacles = newMap.static_obstacles.filter(obs => obs.cells.length > 0)
    // Remove from location cells
    newMap.locations.forEach(loc => {
      loc.entry_cells = loc.entry_cells.filter(c => !(c[0] === x && c[1] === y))
      loc.facility_cells = loc.facility_cells.filter(c => !(c[0] === x && c[1] === y))
    })
    setMapData(newMap)
  }
}
