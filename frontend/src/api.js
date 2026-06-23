const API_BASE = '/api'

export async function fetchMap() {
  const res = await fetch(`${API_BASE}/map`)
  if (!res.ok) throw new Error(`Failed to fetch map: ${res.status}`)
  return res.json()
}

export async function updateMap(mapData) {
  const res = await fetch(`${API_BASE}/map`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(mapData),
  })
  if (!res.ok) throw new Error(`Failed to update map: ${res.status}`)
  return res.json()
}

export async function fetchRuntime() {
  const res = await fetch(`${API_BASE}/runtime`)
  if (!res.ok) throw new Error(`Failed to fetch runtime: ${res.status}`)
  return res.json()
}

export async function updateRuntime(runtimeData) {
  const res = await fetch(`${API_BASE}/runtime`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(runtimeData),
  })
  if (!res.ok) throw new Error(`Failed to update runtime: ${res.status}`)
  return res.json()
}

export async function scheduleInstruction(instruction, maxTimestep = 200) {
  const res = await fetch(`${API_BASE}/schedule`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ instruction, max_timestep: maxTimestep }),
  })
  if (!res.ok) {
    const err = await res.json()
    throw new Error(err.errors?.[0] || `Schedule failed: ${res.status}`)
  }
  return res.json()
}

export async function scheduleStructured(tasks, constraints = [], maxTimestep = 200) {
  const res = await fetch(`${API_BASE}/schedule`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ tasks, constraints, max_timestep: maxTimestep }),
  })
  if (!res.ok) {
    const err = await res.json()
    throw new Error(err.errors?.[0] || `Schedule failed: ${res.status}`)
  }
  return res.json()
}
