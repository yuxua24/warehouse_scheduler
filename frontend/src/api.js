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

// ── Cron job API ────────────────────────────────────────────────────────

export async function fetchCronJobs() {
  const res = await fetch(`${API_BASE}/cron`)
  if (!res.ok) throw new Error(`Failed to fetch cron jobs: ${res.status}`)
  return res.json()
}

export async function createCronJob({ name, cron_expr, instruction }) {
  const res = await fetch(`${API_BASE}/cron`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, cron_expr, instruction }),
  })
  if (!res.ok) {
    const err = await res.json()
    throw new Error(err.detail || `Failed: ${res.status}`)
  }
  return res.json()
}

export async function deleteCronJob(jobId) {
  const res = await fetch(`${API_BASE}/cron/${jobId}`, { method: 'DELETE' })
  if (!res.ok) throw new Error(`Failed to delete: ${res.status}`)
  return res.json()
}

export async function toggleCronJob(jobId, enabled) {
  const res = await fetch(`${API_BASE}/cron/${jobId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  })
  if (!res.ok) throw new Error(`Failed to toggle: ${res.status}`)
  return res.json()
}

// ── Chat API ────────────────────────────────────────────────────────────

export async function sendChat(message) {
  const res = await fetch(`${API_BASE}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message }),
  })
  if (!res.ok) {
    const err = await res.json()
    throw new Error(err.detail || `Chat failed: ${res.status}`)
  }
  return res.json()
}

// ── Confirm API ─────────────────────────────────────────────────────────

export async function confirmChat(confirmed) {
  const res = await fetch(`${API_BASE}/chat/confirm`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message: confirmed ? '确认' : '取消' }),
  })
  if (!res.ok) throw new Error(`Confirm failed: ${res.status}`)
  return res.json()
}

// ── Chat History Save ────────────────────────────────────────────────────

export async function saveChatHistory(sessionId, messages) {
  await fetch(`${API_BASE}/chat/save`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message: JSON.stringify({ session: sessionId, messages }) }),
  })
}
