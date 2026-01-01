const parseJsonSafely = async (response) => {
  try {
    return await response.json()
  } catch (error) {
    return null
  }
}

export const fetchJson = async (url, options = {}) => {
  const headers = new Headers(options.headers || {})
  if (!headers.has('Content-Type') && !(options.body instanceof FormData)) {
    headers.set('Content-Type', 'application/json')
  }

  const response = await fetch(url, { ...options, headers })
  const data = await parseJsonSafely(response)

  if (!response.ok) {
    const message = data?.message || `Request failed with ${response.status}`
    throw new Error(message)
  }

  if (data?.status === 'error') {
    throw new Error(data.message || 'Request error')
  }

  return data
}

export const getSongsSummary = () => fetchJson('/songs/summary')

export const getJsonData = (filename) =>
  fetchJson(`/get_json_data?filename=${encodeURIComponent(filename)}`)

export const updateJson = (payload) =>
  fetchJson('/update_json', {
    method: 'POST',
    body: JSON.stringify(payload)
  })

export const uploadFile = (endpoint, file) => {
  const form = new FormData()
  form.append('file', file)
  return fetchJson(endpoint, {
    method: 'POST',
    body: form
  })
}

export const backupClientState = (payload) =>
  fetchJson('/backup_client_state', {
    method: 'POST',
    body: JSON.stringify(payload)
  })

export const getClientBackupDownloadUrl = (clientId) =>
  `/download_client_backup?client_id=${encodeURIComponent(clientId)}`

export const anchorBackup = (payload) =>
  fetchJson('/anchor_backup', {
    method: 'POST',
    body: JSON.stringify(payload)
  })

export const getAnchorBackup = (anchorId) =>
  fetchJson(`/get_anchor_backup?anchor_id=${encodeURIComponent(anchorId)}`)
