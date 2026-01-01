import { createContext, useContext, useEffect, useMemo, useRef, useState } from 'react'
import {
  Play,
  Pause,
  SkipBack,
  SkipForward,
  Home,
  Search,
  Library,
  User,
  Settings,
  Heart,
  Repeat,
  Shuffle,
  ListMusic,
  MoreHorizontal,
  ChevronDown,
  MonitorSpeaker,
  Maximize,
  EyeOff,
  Plus,
  X,
  Share2,
  Radio,
  UploadCloud,
  RefreshCw
} from 'lucide-react'
import { getSongsSummary, uploadFile, backupClientState, anchorBackup, getAnchorBackup } from './api'
import './App.css'
import './lyrics-player.css'

const formatTime = (seconds) => {
  if (!Number.isFinite(seconds)) return '0:00'
  const mins = Math.floor(seconds / 60)
  const secs = Math.floor(seconds % 60)
  return `${mins}:${secs < 10 ? '0' : ''}${secs}`
}

const DEFAULT_AMLL_COVER = '/static/icons/icon-512x512.png'

const resolveAmllCoverUrl = (raw) => {
  if (!raw || raw === '!') return DEFAULT_AMLL_COVER
  if (raw.startsWith('data:')) return raw
  if (raw.startsWith('http://') || raw.startsWith('https://')) return raw
  if (raw.startsWith('//')) return `${window.location.protocol}${raw}`
  if (raw.startsWith('/')) return raw
  const normalized = raw.replace(/\\/g, '/')
  if (normalized.startsWith('songs/')) return `/${normalized}`
  return raw
}

const renderAmllPreview = (lines) => {
  if (!Array.isArray(lines) || lines.length === 0) return '等待 AMLL 数据...'
  const preview = lines.slice(0, 4).map((line) => line?.text || '').filter(Boolean)
  return preview.join('\n') || '等待 AMLL 数据...'
}

const hasLyrics = (song) => Boolean(song?.lyricsPath || song?.metaLyrics)

const matchesFilter = (song, filterId) => {
  if (filterId === 'all') return true
  if (!song) return false
  if (filterId === 'background') return Boolean(song.hasBackgroundVocals)
  if (filterId === 'duet') return Boolean(song.hasDuet)
  if (filterId === 'audio-only') return Boolean(song.hasAudio)
  return true
}

const matchesFilters = (song, filters) => {
  if (!Array.isArray(filters) || !filters.length) return true
  if (filters.includes('all')) return true
  return filters.every((filterId) => matchesFilter(song, filterId))
}

const buildSongTags = (song) => {
  if (!song) return []
  const tags = []
  if (song.hasDuet) tags.push('对唱')
  if (song.hasBackgroundVocals) tags.push('和声')
  if (song.hasAudio === false) tags.push('无音源')
  return tags
}

const encodePath = (path) =>
  path
    .split('/')
    .map((segment) => {
      if (!segment) return ''
      try {
        return encodeURIComponent(decodeURIComponent(segment))
      } catch (error) {
        return encodeURIComponent(segment)
      }
    })
    .join('/')

const resolveMediaUrl = (value) => {
  if (!value) return ''
  if (value.startsWith('http://127.0.0.1') || value.startsWith('http://localhost')) {
    try {
      const url = new URL(value)
      const encodedPath = encodePath(url.pathname)
      return `${window.location.origin}${encodedPath}${url.search}`
    } catch (error) {
      return value
    }
  }
  if (value.startsWith('http')) return value
  if (value.startsWith('/')) return encodePath(value)
  if (value.startsWith('./')) return encodePath(value.slice(1))
  return encodePath(`/${value}`)
}

const PLAYLIST_STORAGE_KEY = 'lyricSpherePlaylistsV1'
const STATS_STORAGE_KEY = 'lyricSphereListenStatsV1'
const UI_SETTINGS_KEY = 'lyricSphereUiSettingsV1'
const ANCHOR_SETTINGS_KEY = 'lyricSphereAnchorSettingsV1'
const BACKUP_SETTINGS_KEY = 'lyricSphereBackupSettingsV1'
const RECENT_SEARCH_STORAGE_KEY = 'lyricSphereRecentSearchesV1'
const CLIENT_ID_KEY = 'lyricSphereClientIdV1'
const AUTO_BACKUP_INTERVAL_MS = 5 * 60 * 1000
const INVALID_COVER_URL = '/__invalid__/cover.png'
const DEFAULT_PLAYLISTS = [{ id: 'like', name: '喜欢', tracks: [] }]
const RECENT_SEARCH_LIMIT = 8
const DEFAULT_RECENT_SEARCHES = ['Taylor Swift', '周杰伦', 'Lofi Study', 'Podcast']

const emptySong = {
  filename: '',
  title: '未选择歌曲',
  artists: [],
  albumImgSrc: '',
  backgroundImage: '',
  hasDuet: false,
  hasBackgroundVocals: false,
  hasAudio: false
}

const median = (values) => {
  if (!values.length) return 0
  const sorted = [...values].sort((a, b) => a - b)
  const mid = Math.floor(sorted.length / 2)
  if (sorted.length % 2 === 0) {
    return Math.round((sorted[mid - 1] + sorted[mid]) / 2)
  }
  return sorted[mid]
}

const SettingsContext = createContext({ disableCovers: false })

const getOrCreateClientId = () => {
  try {
    const stored = window.localStorage.getItem(CLIENT_ID_KEY)
    if (stored) return stored
    const uuid =
      typeof crypto !== 'undefined' && crypto.randomUUID
        ? crypto.randomUUID()
        : `${Date.now()}-${Math.random().toString(16).slice(2)}`
    window.localStorage.setItem(CLIENT_ID_KEY, uuid)
    return uuid
  } catch (error) {
    return `client-${Date.now()}`
  }
}

const normalizeSearchTerm = (term) => term.trim()

const loadRecentSearches = () => {
  try {
    const raw = window.localStorage.getItem(RECENT_SEARCH_STORAGE_KEY)
    if (!raw) return DEFAULT_RECENT_SEARCHES
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return DEFAULT_RECENT_SEARCHES
    const cleaned = parsed
      .map((item) => normalizeSearchTerm(String(item)))
      .filter(Boolean)
      .slice(0, RECENT_SEARCH_LIMIT)
    return cleaned.length ? cleaned : DEFAULT_RECENT_SEARCHES
  } catch (error) {
    return DEFAULT_RECENT_SEARCHES
  }
}

const saveRecentSearches = (items) => {
  try {
    window.localStorage.setItem(RECENT_SEARCH_STORAGE_KEY, JSON.stringify(items))
  } catch (error) {
    // ignore storage errors
  }
}

const buildSongLookup = (songs) => {
  const map = new Map()
  songs.forEach((song) => {
    if (!song?.filename) return
    const filename = song.filename
    map.set(filename, song)
    map.set(filename.toLowerCase(), song)
    const noExt = filename.replace(/\.json$/i, '')
    map.set(noExt, song)
    map.set(noExt.toLowerCase(), song)
    try {
      const decoded = decodeURIComponent(filename)
      map.set(decoded, song)
      map.set(decoded.toLowerCase(), song)
    } catch (error) {
      // ignore decode errors
    }

    const album = resolveMediaUrl(song.albumImgSrc)
    const background = resolveMediaUrl(song.backgroundImage)
    const candidates = [song.albumImgSrc, album, song.backgroundImage, background]
    candidates.forEach((value) => {
      if (!value) return
      map.set(value, song)
      map.set(value.toLowerCase(), song)
      try {
        const decodedValue = decodeURIComponent(value)
        map.set(decodedValue, song)
        map.set(decodedValue.toLowerCase(), song)
      } catch (error) {
        // ignore decode errors
      }
    })
  })
  return map
}

const resolveSongById = (id, lookup) => {
  if (!id) return null
  const candidates = new Set()
  const addCandidate = (value) => {
    if (value) candidates.add(value)
  }
  addCandidate(id)
  addCandidate(id.toLowerCase())
  addCandidate(id.replace(/^\/?songs\//i, ''))
  addCandidate(id.replace(/^\/?songs\//i, '').toLowerCase())
  addCandidate(id.replace(/\.json$/i, ''))
  addCandidate(id.replace(/\.json$/i, '').toLowerCase())
  try {
    const decoded = decodeURIComponent(id)
    addCandidate(decoded)
    addCandidate(decoded.toLowerCase())
    addCandidate(decoded.replace(/^\/?songs\//i, ''))
    addCandidate(decoded.replace(/^\/?songs\//i, '').toLowerCase())
    addCandidate(decoded.replace(/\.json$/i, ''))
    addCandidate(decoded.replace(/\.json$/i, '').toLowerCase())
  } catch (error) {
    // ignore decode errors
  }

  try {
    const url = new URL(id)
    const pathname = url.pathname || ''
    addCandidate(pathname)
    addCandidate(pathname.toLowerCase())
    addCandidate(pathname.replace(/^\/?songs\//i, ''))
    addCandidate(pathname.replace(/^\/?songs\//i, '').toLowerCase())
    try {
      const decodedPath = decodeURIComponent(pathname)
      addCandidate(decodedPath)
      addCandidate(decodedPath.toLowerCase())
      addCandidate(decodedPath.replace(/^\/?songs\//i, ''))
      addCandidate(decodedPath.replace(/^\/?songs\//i, '').toLowerCase())
    } catch (error) {
      // ignore decode errors
    }
  } catch (error) {
    // not a URL
  }

  for (const key of candidates) {
    if (lookup.has(key)) return lookup.get(key)
  }
  return null
}

const readStatsMap = () => {
  try {
    const raw = window.localStorage.getItem(STATS_STORAGE_KEY)
    if (!raw) return {}
    const parsed = JSON.parse(raw)
    if (parsed && typeof parsed === 'object') return parsed
  } catch (error) {
    // ignore storage errors
  }
  return {}
}

const mergeHistory = (primary = [], secondary = []) => {
  const merged = []
  primary.forEach((item) => {
    if (!merged.includes(item)) merged.push(item)
  })
  secondary.forEach((item) => {
    if (!merged.includes(item)) merged.push(item)
  })
  return merged.slice(0, 50)
}

const mergeListenStats = (primary = {}, secondary = {}) => {
  const merged = {}
  Object.entries(secondary).forEach(([key, value]) => {
    if (value && typeof value === 'object') {
      merged[key] = {
        completions: Array.isArray(value.completions) ? [...value.completions] : [],
        listens: Array.isArray(value.listens) ? [...value.listens] : []
      }
    }
  })
  Object.entries(primary).forEach(([key, value]) => {
    if (!value || typeof value !== 'object') return
    const entry = merged[key] || { completions: [], listens: [] }
    const completions = entry.completions.concat(value.completions || [])
    const listens = entry.listens.concat(value.listens || [])
    merged[key] = {
      completions: completions.slice(-50),
      listens: listens.slice(-50)
    }
  })
  return merged
}

const mergePlaylists = (primary = [], secondary = []) => {
  const merged = []
  const index = new Map()

  const normalize = (item) => {
    if (!item || typeof item !== 'object') return null
    if (!item.id) return null
    const tracks = Array.isArray(item.tracks) ? item.tracks : []
    return {
      id: item.id,
      name: item.name || item.title || '',
      tracks
    }
  }

  primary.forEach((item) => {
    const normalized = normalize(item)
    if (!normalized) return
    index.set(normalized.id, normalized)
    merged.push(normalized)
  })

  secondary.forEach((item) => {
    const normalized = normalize(item)
    if (!normalized) return
    const existing = index.get(normalized.id)
    if (existing) {
      const tracks = existing.tracks
      normalized.tracks.forEach((track) => {
        if (!tracks.includes(track)) tracks.push(track)
      })
      if (!existing.name && normalized.name) {
        existing.name = normalized.name
      }
      return
    }
    index.set(normalized.id, normalized)
    merged.push(normalized)
  })

  return merged
}

const writeStatsMap = (data) => {
  try {
    window.localStorage.setItem(STATS_STORAGE_KEY, JSON.stringify(data))
  } catch (error) {
    // ignore storage errors
  }
}

const Sparkline = ({ values = [], className = '' }) => {
  const width = 180
  const height = 48
  const points = useMemo(() => {
    const safe = values.length ? values : [0]
    const step = safe.length > 1 ? width / (safe.length - 1) : 0
    return safe.map((value, index) => {
      const clamped = Math.min(100, Math.max(0, value))
      const x = step * index
      const y = height - (clamped / 100) * height
      return [x, y]
    })
  }, [values, width, height])

  const path = points
    .map((point, index) => `${index === 0 ? 'M' : 'L'} ${point[0]} ${point[1]}`)
    .join(' ')

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      className={`w-full h-12 ${className}`}
      aria-hidden="true"
    >
      <defs>
        <linearGradient id="sparklineGradient" x1="0" x2="1" y1="0" y2="0">
          <stop offset="0%" stopColor="rgba(56, 189, 248, 0.9)" />
          <stop offset="100%" stopColor="rgba(16, 185, 129, 0.9)" />
        </linearGradient>
      </defs>
      <path d={path} fill="none" stroke="url(#sparklineGradient)" strokeWidth="2" />
      {points.map((point, index) => (
        <circle key={index} cx={point[0]} cy={point[1]} r="2" fill="rgba(255,255,255,0.7)" />
      ))}
    </svg>
  )
}

const SongTags = ({ song, className = '' }) => {
  const tags = buildSongTags(song)
  if (!tags.length) return null
  const tagStyles = {
    背景: 'border-sky-400/60 text-sky-200',
    对唱: 'border-violet-400/60 text-violet-200',
    和声: 'border-emerald-400/50 text-emerald-300',
    无音源: 'border-rose-400/60 text-rose-300'
  }
  return (
    <div className={`flex flex-wrap gap-1 ${className}`}>
      {tags.map((tag) => (
        <span
          key={tag}
          className={`text-[8px] px-1 rounded border ${tagStyles[tag] || 'border-white/20 text-white/60'}`}
        >
          {tag}
        </span>
      ))}
    </div>
  )
}

const gradientClasses = [
  'from-sky-500/70 via-slate-700 to-emerald-400/80',
  'from-fuchsia-500/60 via-slate-800 to-indigo-500/70',
  'from-amber-500/60 via-slate-800 to-rose-500/70',
  'from-emerald-500/60 via-slate-700 to-cyan-500/70',
  'from-indigo-500/60 via-slate-900 to-sky-500/70'
]

const pickGradientClass = (seed) => {
  if (!seed) return gradientClasses[0]
  let hash = 0
  for (let i = 0; i < seed.length; i += 1) {
    hash = (hash * 31 + seed.charCodeAt(i)) | 0
  }
  const index = Math.abs(hash) % gradientClasses.length
  return gradientClasses[index]
}

const CoverImage = ({ src, alt, className = '' }) => {
  const url = resolveMediaUrl(src)
  const seed = `${src || ''}|${alt || ''}`
  const gradientClass = useMemo(() => pickGradientClass(seed), [seed])
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    setFailed(false)
  }, [src])

  return (
    <div
      role="img"
      aria-label={alt}
      className={`relative overflow-hidden bg-gradient-to-br ${gradientClass} ${className}`}
    >
      {url && !failed && (
        <img
          src={url}
          className="absolute inset-0 h-full w-full object-cover"
          alt={alt}
          loading="lazy"
          decoding="async"
          draggable={false}
          onDragStart={(event) => event.preventDefault()}
          onError={() => setFailed(true)}
        />
      )}
    </div>
  )
}

export default function App() {
  const [activeTab, setActiveTab] = useState('home')
  const [isMobile, setIsMobile] = useState(window.innerWidth < 768)
  const [songs, setSongs] = useState([])
  const [isLoading, setIsLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [currentTrack, setCurrentTrack] = useState(emptySong)
  const [isPlaying, setIsPlaying] = useState(false)
  const [progress, setProgress] = useState(0)
  const [duration, setDuration] = useState(0)
  const [currentTime, setCurrentTime] = useState(0)
  const [isPlayerOpen, setIsPlayerOpen] = useState(false)
  const [playerView, setPlayerView] = useState('cover')
  const [playbackMode, setPlaybackMode] = useState('list')
  const [playlists, setPlaylists] = useState(DEFAULT_PLAYLISTS)
  const [playHistory, setPlayHistory] = useState([])
  const [listenStats, setListenStats] = useState({})
  const [selectedTracks, setSelectedTracks] = useState(new Set())
  const [lastSelectedIndex, setLastSelectedIndex] = useState(-1)
  const [activePlaylistId, setActivePlaylistId] = useState(null)
  const [playQueueIds, setPlayQueueIds] = useState([])
  const [activeFilters, setActiveFilters] = useState(['all'])
  const [showPlaylistCreator, setShowPlaylistCreator] = useState(false)
  const [newPlaylistName, setNewPlaylistName] = useState('')
  const [selectedSong, setSelectedSong] = useState(null)
  const [uploadStatus, setUploadStatus] = useState('')
  const [searchTerm, setSearchTerm] = useState('')
  const [recentSearches, setRecentSearches] = useState(() => loadRecentSearches())
  const [lyricsStatus, setLyricsStatus] = useState('')
  const [songInfo, setSongInfo] = useState(null)
  const [shouldAutoPlay, setShouldAutoPlay] = useState(false)
  const [lyricsFrameSrc, setLyricsFrameSrc] = useState('')
  const [lyricScale, setLyricScale] = useState(1)
  const [showFontControl, setShowFontControl] = useState(false)
  const [isLyricsImmersive, setIsLyricsImmersive] = useState(false)
  const [showQueueModal, setShowQueueModal] = useState(false)
  const [queueOpen, setQueueOpen] = useState(false)
  const [disableCovers, setDisableCovers] = useState(false)
  const [iframeSrcA, setIframeSrcA] = useState('')
  const [iframeSrcB, setIframeSrcB] = useState('')
  const [activeIframe, setActiveIframe] = useState('A')
  const [pendingIframe, setPendingIframe] = useState(null)
  const [isIframeTransitioning, setIsIframeTransitioning] = useState(false)
  const [amllPopupOpen, setAmllPopupOpen] = useState(false)
  const [amllSnapshot, setAmllSnapshot] = useState(null)
  const [amllStatus, setAmllStatus] = useState('idle')
  const [amllMode, setAmllMode] = useState('local')
  const [isStaticDragActive, setIsStaticDragActive] = useState(false)
  const [autoBackupEnabled, setAutoBackupEnabled] = useState(false)
  const [backupStatus, setBackupStatus] = useState('')
  const [lastBackupAt, setLastBackupAt] = useState('')
  const [anchorAccount, setAnchorAccount] = useState('')
  const [anchorPassword, setAnchorPassword] = useState('')
  const [anchorId, setAnchorId] = useState('')
  const [anchorStatus, setAnchorStatus] = useState('')
  const [importStatus, setImportStatus] = useState('')
  const iframeARef = useRef(null)
  const iframeBRef = useRef(null)
  const staticZipInputRef = useRef(null)
  const activeAudioRef = useRef(null)
  const iframeSwapTimerRef = useRef(null)
  const queueListRef = useRef(null)
  const nextUpRef = useRef(null)
  const queueModalListRef = useRef(null)
  const nextUpModalRef = useRef(null)
  const lastSyncRef = useRef({ time: -1, duration: -1, playing: null })
  const endedHandledRef = useRef(false)
  const lastTrackRef = useRef({ filename: '', time: 0, duration: 0, recorded: false })
  const cleanedPlaylistsRef = useRef(false)
  const amllProgressRef = useRef(-1)
  const autoBackupTimerRef = useRef(null)
  const autoBackupIntervalRef = useRef(null)
  const lastBackupPayloadRef = useRef('')

  const clientId = useMemo(() => getOrCreateClientId(), [])

  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(PLAYLIST_STORAGE_KEY)
      if (!raw) return
      const parsed = JSON.parse(raw)
      if (Array.isArray(parsed.playlists)) {
        const normalized = parsed.playlists.map((pl) => ({
          ...pl,
          tracks: Array.isArray(pl.tracks) ? pl.tracks : []
        }))
        const hasLike = normalized.some((pl) => pl.id === 'like')
        const nextPlaylists = hasLike
          ? normalized
          : [...normalized, DEFAULT_PLAYLISTS[0]]
        setPlaylists(nextPlaylists)
      }
      if (Array.isArray(parsed.history)) {
        setPlayHistory(parsed.history)
      }
    } catch (error) {
      // ignore storage errors
    }
  }, [])

  useEffect(() => {
    try {
      const payload = JSON.stringify({ playlists, history: playHistory })
      window.localStorage.setItem(PLAYLIST_STORAGE_KEY, payload)
    } catch (error) {
      // ignore storage errors
    }
  }, [playlists, playHistory])

  useEffect(() => {
    saveRecentSearches(recentSearches)
  }, [recentSearches])

  const commitRecentSearch = (term) => {
    const normalized = normalizeSearchTerm(term)
    if (!normalized) return
    setRecentSearches((prev) => {
      const lowered = normalized.toLowerCase()
      const next = [normalized, ...prev.filter((item) => item.toLowerCase() !== lowered)]
      return next.slice(0, RECENT_SEARCH_LIMIT)
    })
  }

  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(UI_SETTINGS_KEY)
      if (!raw) return
      const parsed = JSON.parse(raw)
      if (typeof parsed?.disableCovers === 'boolean') {
        setDisableCovers(parsed.disableCovers)
      }
    } catch (error) {
      // ignore storage errors
    }
  }, [])

  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(ANCHOR_SETTINGS_KEY)
      if (!raw) return
      const parsed = JSON.parse(raw)
      if (typeof parsed?.anchorAccount === 'string') {
        setAnchorAccount(parsed.anchorAccount)
      }
      if (typeof parsed?.anchorId === 'string') {
        setAnchorId(parsed.anchorId)
      }
    } catch (error) {
      // ignore storage errors
    }
  }, [])

  useEffect(() => {
    try {
      window.localStorage.setItem(
        ANCHOR_SETTINGS_KEY,
        JSON.stringify({ anchorAccount, anchorId })
      )
    } catch (error) {
      // ignore storage errors
    }
  }, [anchorAccount, anchorId])

  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(BACKUP_SETTINGS_KEY)
      if (!raw) return
      const parsed = JSON.parse(raw)
      if (typeof parsed?.autoBackupEnabled === 'boolean') {
        setAutoBackupEnabled(parsed.autoBackupEnabled)
      }
      if (typeof parsed?.lastBackupAt === 'string') {
        setLastBackupAt(parsed.lastBackupAt)
      }
    } catch (error) {
      // ignore storage errors
    }
  }, [])

  useEffect(() => {
    try {
      window.localStorage.setItem(
        BACKUP_SETTINGS_KEY,
        JSON.stringify({ autoBackupEnabled, lastBackupAt })
      )
    } catch (error) {
      // ignore storage errors
    }
  }, [autoBackupEnabled, lastBackupAt])

  useEffect(() => {
    try {
      window.localStorage.setItem(UI_SETTINGS_KEY, JSON.stringify({ disableCovers }))
    } catch (error) {
      // ignore storage errors
    }
  }, [disableCovers])

  const refreshLibrary = async () => {
    setIsLoading(true)
    setLoadError('')
    try {
      const data = await getSongsSummary()
      const list = data?.songs || []
      setSongs(list)
      if (list.length) {
        setCurrentTrack(list[0])
      } else {
        setCurrentTrack(emptySong)
      }
    } catch (error) {
      setLoadError(error.message)
    } finally {
      setIsLoading(false)
    }
  }

  useEffect(() => {
    const handleResize = () => setIsMobile(window.innerWidth < 768)
    window.addEventListener('resize', handleResize)
    return () => window.removeEventListener('resize', handleResize)
  }, [])

  useEffect(() => {
    refreshLibrary()
  }, [])

  useEffect(() => {
    const handleKeyDown = (event) => {
      const target = event.target
      const isEditable =
        target?.tagName === 'INPUT' ||
        target?.tagName === 'TEXTAREA' ||
        target?.isContentEditable
      if (isEditable) return

      if (event.code === 'Space') {
        event.preventDefault()
        togglePlay()
        return
      }

      if (event.code === 'ArrowLeft' || event.code === 'ArrowRight') {
        const audio = getIframeAudio()
        if (!audio) return
        event.preventDefault()
        const delta = event.code === 'ArrowRight' ? 3 : -3
        const nextTime = Math.max(0, Math.min(audio.duration || 0, audio.currentTime + delta))
        audio.currentTime = nextTime
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [])

  useEffect(() => {
    if (!songs.length) return
    if (!currentTrack?.filename) {
      setCurrentTrack(songs[0])
    }
  }, [songs, currentTrack])

  useEffect(() => {
    if (!songs.length) return
    const idx = songs.findIndex((song) => song.filename === currentTrack.filename)
    if (idx < 0) {
      setCurrentTrack(songs[0])
    }
  }, [songs, currentTrack])

  const getActiveIframe = () => {
    return activeIframe === 'A' ? iframeARef.current : iframeBRef.current
  }

  const getInactiveIframe = () => {
    return activeIframe === 'A' ? iframeBRef.current : iframeARef.current
  }

  const getIframeAudio = () => {
    if (activeAudioRef.current) return activeAudioRef.current
    const iframe = getActiveIframe()
    if (!iframe) return null
    try {
      const doc = iframe.contentDocument
      if (doc) {
        const audio = doc.getElementById('audio-player')
        if (audio) return audio
      }
    } catch (error) {
      // ignore
    }
    const fallbackIframe = getInactiveIframe()
    if (!fallbackIframe) return null
    try {
      const doc = fallbackIframe.contentDocument
      if (!doc) return null
      return doc.getElementById('audio-player')
    } catch (error) {
      return null
    }
  }

  const getIframeDocumentFrom = (iframe) => {
    if (!iframe) return null
    try {
      return iframe.contentDocument || iframe.contentWindow?.document || null
    } catch (error) {
      return null
    }
  }

  const getIframeDocument = () => getIframeDocumentFrom(getActiveIframe())

  const getIframeFontSlider = (iframe) => {
    const doc = iframe ? getIframeDocumentFrom(iframe) : getIframeDocument()
    if (!doc) return null
    return doc.getElementById('fontSlider')
  }

  const applyIframeFontScale = (value) => {
    const slider = getIframeFontSlider()
    if (!slider) return
    slider.value = String(value)
    slider.dispatchEvent(new Event('input', { bubbles: true }))
  }

  const onIframeReady = async (slot) => {
    const isActiveSlot = slot === activeIframe
    const isPendingSlot = slot === pendingIframe
    if (!isActiveSlot && !isPendingSlot) return

    try {
      const iframe = slot === 'A' ? iframeARef.current : iframeBRef.current
      const doc = getIframeDocumentFrom(iframe)
      const slider = getIframeFontSlider(iframe)
      if (slider) {
        const sliderValue = Number.parseFloat(slider.value)
        if (Number.isFinite(sliderValue)) {
          setLyricScale(sliderValue)
        }
      }
      if (doc) {
        const audio = doc.getElementById('audio-player')
        if (audio) {
          activeAudioRef.current = audio
        }
      }
    } catch (error) {
      // ignore font slider errors
    }

    if (isPendingSlot) {
      setIsIframeTransitioning(true)
      if (iframeSwapTimerRef.current) {
        window.clearTimeout(iframeSwapTimerRef.current)
      }
      iframeSwapTimerRef.current = window.setTimeout(() => {
        const oldIframe = getActiveIframe()
        if (oldIframe) {
          try {
            const oldAudio = oldIframe.contentDocument?.getElementById('audio-player')
            if (oldAudio && !oldAudio.paused) oldAudio.pause()
          } catch (error) {
            // ignore pause errors
          }
        }
        setActiveIframe(slot)
        setPendingIframe(null)
        setIsIframeTransitioning(false)
      }, 320)
    }

    if (shouldAutoPlay && (isPendingSlot || (isActiveSlot && !pendingIframe))) {
      const targetIframe = isPendingSlot ? getInactiveIframe() : getActiveIframe()
      const audio = targetIframe?.contentDocument?.getElementById('audio-player')
      if (audio) {
        try {
          await audio.play()
          setIsPlaying(true)
        } catch (error) {
          setIsPlaying(false)
          setLyricsStatus(`播放失败: ${error.message}`)
        } finally {
          setShouldAutoPlay(false)
        }
      }
    }
  }


  const togglePlay = async (event) => {
    event?.stopPropagation()
    const audio = getIframeAudio()
    if (!audio) return
    try {
      if (audio.paused) {
        await audio.play()
        setIsPlaying(true)
      } else {
        audio.pause()
        setIsPlaying(false)
      }
    } catch (error) {
      setIsPlaying(false)
      setLyricsStatus(`播放失败: ${error.message}`)
    }
  }

  const toggleFullscreen = async () => {
    try {
      if (document.fullscreenElement) {
        await document.exitFullscreen()
      } else {
        await document.documentElement.requestFullscreen()
      }
    } catch (error) {
      // ignore fullscreen errors
    }
  }

  useEffect(() => {
    if (!lyricsFrameSrc) {
      setIframeSrcA('')
      setIframeSrcB('')
      setPendingIframe(null)
      setIsIframeTransitioning(false)
      return
    }
    const activeSrc = activeIframe === 'A' ? iframeSrcA : iframeSrcB
    if (!activeSrc) {
      if (activeIframe === 'A') {
        setIframeSrcA(lyricsFrameSrc)
      } else {
        setIframeSrcB(lyricsFrameSrc)
      }
      return
    }
    if (lyricsFrameSrc === activeSrc) return
    const nextSlot = activeIframe === 'A' ? 'B' : 'A'
    if (nextSlot === 'A') {
      setIframeSrcA(lyricsFrameSrc)
    } else {
      setIframeSrcB(lyricsFrameSrc)
    }
    setPendingIframe(nextSlot)
  }, [lyricsFrameSrc, activeIframe, iframeSrcA, iframeSrcB])

  useEffect(() => {
    return () => {
      if (iframeSwapTimerRef.current) {
        window.clearTimeout(iframeSwapTimerRef.current)
        iframeSwapTimerRef.current = null
      }
    }
  }, [])

  const fetchAmllSnapshot = async (force = false) => {
    try {
      setAmllStatus('loading')
      const resp = await fetch(`/amll/state${force ? `?_t=${Date.now()}` : ''}`)
      if (!resp.ok) throw new Error('请求失败')
      const data = await resp.json()
      setAmllSnapshot(data)
      setAmllStatus('ready')
    } catch (error) {
      setAmllSnapshot(null)
      setAmllStatus('error')
    }
  }

  useEffect(() => {
    if (!amllPopupOpen) return
    fetchAmllSnapshot(false)
  }, [amllPopupOpen])

  const openQueueModal = () => {
    setShowQueueModal(true)
    requestAnimationFrame(() => setQueueOpen(true))
  }

  const closeQueueModal = () => {
    setQueueOpen(false)
    window.setTimeout(() => setShowQueueModal(false), 200)
  }

  const playSong = (song, queueIds = null) => {
    setCurrentTrack(song)
    setShouldAutoPlay(true)
    if (Array.isArray(queueIds) && queueIds.length) {
      setPlayQueueIds(queueIds)
    } else {
      setPlayQueueIds([])
    }
    if (song?.filename) {
      setPlayHistory((prev) => {
        const next = [song.filename, ...prev.filter((item) => item !== song.filename)]
        return next.slice(0, 50)
      })
      recordListenStart(song.filename)
    }
    setSelectedTracks(new Set([song.filename]))
    const idx = songs.findIndex((item) => item.filename === song.filename)
    if (idx >= 0) setLastSelectedIndex(idx)
  }

  const recordCompletion = (filename, percent) => {
    if (!filename) return
    const safeValue = Math.max(0, Math.min(100, Math.round(percent)))
    const statsMap = readStatsMap()
    const entry = statsMap[filename] || { completions: [], listens: [] }
    const completions = [...(entry.completions || []), safeValue].slice(-50)
    const nextEntry = { ...entry, completions }
    statsMap[filename] = nextEntry
    writeStatsMap(statsMap)
    setListenStats((prev) => ({
      ...prev,
      [filename]: nextEntry
    }))
  }

  const recordListenStart = (filename) => {
    if (!filename) return
    const statsMap = readStatsMap()
    const entry = statsMap[filename] || { completions: [], listens: [] }
    const listens = [...(entry.listens || []), Date.now()].slice(-50)
    const nextEntry = { ...entry, listens }
    statsMap[filename] = nextEntry
    writeStatsMap(statsMap)
    setListenStats((prev) => ({
      ...prev,
      [filename]: nextEntry
    }))
  }

  const loadStatsForSong = (filename) => {
    if (!filename) return
    const statsMap = readStatsMap()
    const entry = statsMap[filename] || { completions: [], listens: [] }
    setListenStats((prev) => ({
      ...prev,
      [filename]: entry
    }))
  }

  const activeQueue = useMemo(() => {
    if (playQueueIds.length) {
      const map = new Map(songs.map((song) => [song.filename, song]))
      return playQueueIds.map((id) => map.get(id)).filter(Boolean)
    }
    return songs
  }, [playQueueIds, songs])

  const songLookup = useMemo(() => buildSongLookup(songs), [songs])

  useEffect(() => {
    if (!songs.length || cleanedPlaylistsRef.current === true) return
    const nextPlaylists = playlists.map((playlist) => {
      const normalizedTracks = (playlist.tracks || []).map((id) => {
        const match = resolveSongById(id, songLookup)
        return match?.filename || id
      })
      const deduped = Array.from(new Set(normalizedTracks))
      return { ...playlist, tracks: deduped }
    })
    const changed = nextPlaylists.some((playlist, idx) => {
      const prev = playlists[idx]
      if (!prev) return true
      return JSON.stringify(prev.tracks) !== JSON.stringify(playlist.tracks)
    })
    if (changed) {
      setPlaylists(nextPlaylists)
    }
    cleanedPlaylistsRef.current = true
  }, [songs, playlists, songLookup])

  const currentIndex = useMemo(() => {
    if (!activeQueue.length) return -1
    const idx = activeQueue.findIndex((song) => song.filename === currentTrack.filename)
    return idx >= 0 ? idx : 0
  }, [activeQueue, currentTrack])

  const buildShuffleQueue = (list) => {
    const shuffled = [...list]
    for (let i = shuffled.length - 1; i > 0; i -= 1) {
      const j = Math.floor(Math.random() * (i + 1))
      const temp = shuffled[i]
      shuffled[i] = shuffled[j]
      shuffled[j] = temp
    }
    return shuffled
  }

  const queue = useMemo(() => {
    if (!activeQueue.length) return []
    const list = activeQueue.filter((_, idx) => idx !== currentIndex)
    if (playbackMode === 'shuffle') {
      return buildShuffleQueue(list)
    }
    return list
  }, [activeQueue, currentIndex, playbackMode])

  const nextUpSong = useMemo(() => {
    if (!activeQueue.length || currentIndex < 0) return null
    if (playbackMode === 'single') return activeQueue[currentIndex]
    if (playbackMode === 'shuffle') return queue[0] || null
    return activeQueue[(currentIndex + 1) % activeQueue.length]
  }, [activeQueue, currentIndex, playbackMode, queue])

  const scrollToNextUp = (listRef, itemRef) => {
    if (!listRef.current || !itemRef.current) return
    itemRef.current.scrollIntoView({ block: 'start', behavior: 'smooth' })
  }

  useEffect(() => {
    if (playerView !== 'queue' || !nextUpSong) return
    requestAnimationFrame(() => scrollToNextUp(queueListRef, nextUpRef))
  }, [playerView, nextUpSong?.filename])

  useEffect(() => {
    if (!queueOpen || !nextUpSong) return
    requestAnimationFrame(() => scrollToNextUp(queueModalListRef, nextUpModalRef))
  }, [queueOpen, nextUpSong?.filename])

  const getNextIndex = () => {
    if (!activeQueue.length) return -1
    if (playbackMode === 'single') return currentIndex
    if (playbackMode === 'shuffle') {
      if (activeQueue.length <= 1) return currentIndex
      let next = currentIndex
      while (next === currentIndex) {
        next = Math.floor(Math.random() * activeQueue.length)
      }
      return next
    }
    return (currentIndex + 1) % activeQueue.length
  }

  const getPrevIndex = () => {
    if (!activeQueue.length) return -1
    if (playbackMode === 'single') return currentIndex
    if (playbackMode === 'shuffle') {
      if (activeQueue.length <= 1) return currentIndex
      let prev = currentIndex
      while (prev === currentIndex) {
        prev = Math.floor(Math.random() * activeQueue.length)
      }
      return prev
    }
    return (currentIndex - 1 + activeQueue.length) % activeQueue.length
  }

  const handleNext = () => {
    const nextIndex = getNextIndex()
    if (nextIndex < 0) return
    playSong(activeQueue[nextIndex], playQueueIds)
  }

  const handlePrev = () => {
    const prevIndex = getPrevIndex()
    if (prevIndex < 0) return
    playSong(activeQueue[prevIndex], playQueueIds)
  }

  const handleToggleLike = () => {
    if (!currentTrack?.filename) return
    setPlaylists((prev) =>
      prev.map((pl) => {
        if (pl.id !== 'like') return pl
        const exists = pl.tracks.includes(currentTrack.filename)
        const tracks = exists
          ? pl.tracks.filter((t) => t !== currentTrack.filename)
          : [...pl.tracks, currentTrack.filename]
        return { ...pl, tracks }
      })
    )
  }

  const isLiked = useMemo(() => {
    const like = playlists.find((pl) => pl.id === 'like')
    return like ? like.tracks.includes(currentTrack.filename) : false
  }, [playlists, currentTrack])

  const isSongLiked = (filename) => {
    const like = playlists.find((pl) => pl.id === 'like')
    return like ? like.tracks.includes(filename) : false
  }

  const toggleSongLike = (filename) => {
    if (!filename) return
    setPlaylists((prev) =>
      prev.map((pl) => {
        if (pl.id !== 'like') return pl
        const exists = pl.tracks.includes(filename)
        const tracks = exists ? pl.tracks.filter((t) => t !== filename) : [...pl.tracks, filename]
        return { ...pl, tracks }
      })
    )
  }

  const handleDropToPlaylist = (playlistId, event) => {
    event.preventDefault()
    event.stopPropagation()
    try {
      const raw = event.dataTransfer.getData('application/lyric-sphere-tracks')
      const fallback = event.dataTransfer.getData('text/plain')
      if (!raw && !fallback) return
      const items = raw ? JSON.parse(raw) : [fallback]
      if (!Array.isArray(items) || items.length === 0) return
      setPlaylists((prev) =>
        prev.map((pl) => {
          if (pl.id !== playlistId) return pl
          const merged = Array.from(new Set([...pl.tracks, ...items]))
          return { ...pl, tracks: merged }
        })
      )
    } catch (error) {
      // ignore
    }
  }

  const handleDragStart = (event, song, index) => {
    const isSelected = selectedTracks.has(song.filename)
    if (!isSelected) {
      setSelectedTracks(new Set([song.filename]))
      if (Number.isInteger(index)) setLastSelectedIndex(index)
    }
    const items = isSelected ? Array.from(selectedTracks) : [song.filename]
    event.dataTransfer.setData('application/lyric-sphere-tracks', JSON.stringify(items))
    event.dataTransfer.setData('text/plain', song.filename)
    event.dataTransfer.effectAllowed = 'copy'
  }

  const handleCreatePlaylist = (event) => {
    event?.preventDefault()
    const trimmed = newPlaylistName.trim()
    if (!trimmed) return
    setPlaylists((prev) => [
      ...prev,
      {
        id: `pl-${Date.now()}`,
        name: trimmed,
        tracks: []
      }
    ])
    setNewPlaylistName('')
    setShowPlaylistCreator(false)
  }

  const handleSongClick = (song, index, event, queueIds) => {
    const isMeta = event?.metaKey || event?.ctrlKey
    const isShift = event?.shiftKey
    if (isShift && lastSelectedIndex >= 0) {
      const start = Math.min(lastSelectedIndex, index)
      const end = Math.max(lastSelectedIndex, index)
      const next = new Set()
      songs.slice(start, end + 1).forEach((item) => next.add(item.filename))
      setSelectedTracks(next)
    } else if (isMeta) {
      setSelectedTracks((prev) => {
        const next = new Set(prev)
        if (next.has(song.filename)) {
          next.delete(song.filename)
        } else {
          next.add(song.filename)
        }
        return next
      })
    } else {
      setSelectedTracks(new Set([song.filename]))
      playSong(song, queueIds)
    }
    setLastSelectedIndex(index)
    loadStatsForSong(song.filename)
    setSelectedSong(song)
  }

  const handleStaticImport = async (event) => {
    const input = event?.target
    const file = input?.files?.[0]
    if (!file) return
    if (!file.name.toLowerCase().endsWith('.zip')) {
      setUploadStatus('请选择 static.zip 文件')
      if (input) input.value = ''
      return
    }
    setUploadStatus('导入中...')
    try {
      const data = await uploadFile('/import_static', file)
      setUploadStatus(data.message || '导入成功')
      window.location.reload()
    } catch (error) {
      setUploadStatus(`导入失败: ${error.message}`)
    } finally {
      if (input) input.value = ''
    }
  }

  const handleStaticDrop = async (event) => {
    event.preventDefault()
    setIsStaticDragActive(false)
    const file = event.dataTransfer?.files?.[0]
    if (!file) return
    if (!file.name.toLowerCase().endsWith('.zip')) {
      setUploadStatus('请选择 static.zip 文件')
      return
    }
    setUploadStatus('导入中...')
    try {
      const data = await uploadFile('/import_static', file)
      setUploadStatus(data.message || '导入成功')
      window.location.reload()
    } catch (error) {
      setUploadStatus(`导入失败: ${error.message}`)
    }
  }

  const buildBackupPayload = (anchorOverride) => ({
    client_id: clientId,
    anchor_id: anchorOverride || anchorId || undefined,
    created_at: new Date().toISOString(),
    data: {
      playlists,
      history: playHistory,
      listenStats
    }
  })

  const runBackup = async (reason, anchorOverride) => {
    const payload = buildBackupPayload(anchorOverride)
    const signature = JSON.stringify(payload.data)
    if (reason === 'auto' && signature === lastBackupPayloadRef.current) {
      return
    }
    setBackupStatus(reason === 'auto' ? '自动备份中...' : '备份中...')
    try {
      const response = await backupClientState(payload)
      lastBackupPayloadRef.current = signature
      setLastBackupAt(payload.created_at)
      setBackupStatus(response.message || '备份完成')
    } catch (error) {
      setBackupStatus(`备份失败: ${error.message}`)
    }
  }

  const applyBackupData = (data) => {
    if (!data || typeof data !== 'object') return
    const nextPlaylists = mergePlaylists(data.playlists || [], playlists)
    const nextHistory = mergeHistory(data.history || [], playHistory)
    const nextStats = mergeListenStats(data.listenStats || {}, readStatsMap())
    if (nextPlaylists.length) setPlaylists(nextPlaylists)
    if (nextHistory.length) setPlayHistory(nextHistory)
    if (Object.keys(nextStats).length) {
      writeStatsMap(nextStats)
      setListenStats((prev) => ({ ...prev, ...nextStats }))
    }
  }

  const parseBackupPayload = (raw) => {
    if (!raw || typeof raw !== 'object') return null
    if (raw.payload?.data && typeof raw.payload.data === 'object') {
      return raw.payload.data
    }
    if (raw.data && typeof raw.data === 'object') {
      return raw.data
    }
    return raw
  }

  const handleBackupImport = async (event) => {
    const file = event.target.files?.[0]
    if (!file) return
    setImportStatus('导入中...')
    try {
      const text = await file.text()
      const parsed = JSON.parse(text)
      const data = parseBackupPayload(parsed)
      if (!data || typeof data !== 'object') {
        setImportStatus('导入失败：备份格式不正确')
        event.target.value = ''
        return
      }
      applyBackupData(data)
      setImportStatus('备份已合并')
    } catch (error) {
      setImportStatus(`导入失败: ${error.message}`)
    } finally {
      event.target.value = ''
    }
  }

  const handleAnchorBackup = async () => {
    setAnchorStatus('')
    if (!anchorAccount.trim() || !anchorPassword.trim()) {
      setAnchorStatus('请输入账号和密码')
      return
    }
    try {
      const response = await anchorBackup({
        account: anchorAccount.trim(),
        password: anchorPassword
      })
      const nextAnchorId = response.anchorId || ''
      setAnchorId(nextAnchorId)
      setAnchorStatus('锚定成功，正在同步...')
      try {
        const backup = await getAnchorBackup(nextAnchorId)
        applyBackupData(backup?.data)
        setAnchorStatus('已同步锚定备份')
      } catch (error) {
        // ignore missing backup
      }
      await runBackup('manual', nextAnchorId)
    } catch (error) {
      setAnchorStatus(`锚定失败: ${error.message}`)
    }
  }

  const downloadBackup = () => {
    const url = anchorId
      ? `/download_anchor_backup?anchor_id=${encodeURIComponent(anchorId)}`
      : `/download_client_backup?client_id=${encodeURIComponent(clientId)}`
    window.location.href = url
  }

  useEffect(() => {
    if (!autoBackupEnabled) {
      return
    }
    autoBackupIntervalRef.current = setInterval(() => {
      runBackup('auto')
    }, AUTO_BACKUP_INTERVAL_MS)
    return () => {
      if (autoBackupIntervalRef.current) {
        clearInterval(autoBackupIntervalRef.current)
        autoBackupIntervalRef.current = null
      }
    }
  }, [autoBackupEnabled])

  useEffect(() => {
    if (!autoBackupEnabled) {
      if (autoBackupTimerRef.current) {
        clearTimeout(autoBackupTimerRef.current)
        autoBackupTimerRef.current = null
      }
      return
    }
    if (autoBackupTimerRef.current) {
      clearTimeout(autoBackupTimerRef.current)
    }
    autoBackupTimerRef.current = setTimeout(() => {
      runBackup('auto')
    }, 2000)
    return () => {
      if (autoBackupTimerRef.current) {
        clearTimeout(autoBackupTimerRef.current)
        autoBackupTimerRef.current = null
      }
    }
  }, [autoBackupEnabled, playlists, playHistory, listenStats])

  const coverUrl = resolveMediaUrl(
    songInfo?.cover ||
      songInfo?.coverUrl ||
      songInfo?.meta?.albumImgSrc ||
      currentTrack.albumImgSrc
  )
  const backgroundUrl = resolveMediaUrl(
    songInfo?.meta?.['Background-image'] || currentTrack.backgroundImage
  )

  const requestLyricsSession = async (url) => {
    const response = await fetch(url)
    if (!response.ok) {
      throw new Error(`Lyrics session failed (${response.status})`)
    }
  }

  const buildLyricsFrameSrc = async (track) => {
    if (!track?.filename) return ''
    const params = new URLSearchParams({
      file: track.filename,
      style: 'Kok'
    })
    const cover = disableCovers ? INVALID_COVER_URL : resolveMediaUrl(track.albumImgSrc)
    const background = disableCovers ? INVALID_COVER_URL : resolveMediaUrl(track.backgroundImage)
    if (cover) params.set('cover', cover)
    if (background) params.set('background', background)

    const rawLyrics = track.metaLyrics || track.lyricsPath || ''
    let mainPath = ''
    if (rawLyrics.includes('::')) {
      const parts = rawLyrics.split('::')
      mainPath = parts[1] || ''
    } else {
      mainPath = rawLyrics
    }

    if (mainPath) {
      const lower = mainPath.toLowerCase()
      const isTtml = lower.endsWith('.ttml')
      if (isTtml) {
        let relative = ''
        try {
          if (mainPath.startsWith('http://') || mainPath.startsWith('https://')) {
            const url = new URL(mainPath)
            relative = url.pathname.startsWith('/songs/')
              ? url.pathname.slice('/songs/'.length)
              : url.pathname.replace(/^\//, '')
          } else {
            relative = mainPath.replace(/^\/?songs\//i, '')
          }
          relative = decodeURIComponent(relative)
        } catch (error) {
          relative = mainPath.split('/').pop()
          relative = decodeURIComponent(relative || '')
        }

        const conv = await fetch('/convert_ttml_by_path', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path: relative })
        }).then((res) => res.json())

        if (conv.status === 'success' && conv.lyricPath) {
          params.set('lys', conv.lyricPath)
          if (conv.transPath) {
            params.set('lrc', conv.transPath)
          }
        } else {
          throw new Error(conv.message || 'TTML 转换失败')
        }
      }
    }

    return `/lyrics-animate?${params.toString()}`
  }

  useEffect(() => {
    if (!currentTrack?.filename) return
    let active = true
    const loadSession = async () => {
      if (amllMode === 'amll') {
        setLyricsStatus('')
        setSongInfo(null)
        setLyricsFrameSrc('/lyrics-amll')
        setShouldAutoPlay(false)
        activeAudioRef.current = null
        return
      }
      setLyricsStatus('歌词加载中...')
      setSongInfo(null)
      setIsPlaying(false)
      setCurrentTime(0)
      setDuration(0)
      setProgress(0)
      try {
        const frameSrc = await buildLyricsFrameSrc(currentTrack)
        await requestLyricsSession(frameSrc)
        if (!active) return
        setLyricsFrameSrc(frameSrc)
        const songInfoRes = await fetch('/song-info')
        const songInfoJson = await songInfoRes.json()
        if (!active) return
        setSongInfo(songInfoJson)
        setLyricsStatus('')
        if (shouldAutoPlay) {
          const audio = getIframeAudio()
          if (audio) {
            try {
              await audio.play()
              setIsPlaying(true)
            } catch (error) {
              setIsPlaying(false)
              setLyricsStatus(`播放失败: ${error.message}`)
            } finally {
              setShouldAutoPlay(false)
            }
          }
        }
      } catch (error) {
        if (!active) return
        setLyricsStatus(`歌词加载失败: ${error.message}`)
      }
    }
    loadSession()
    return () => {
      active = false
    }
  }, [currentTrack, amllMode, disableCovers])

  useEffect(() => {
    const prev = lastTrackRef.current
    if (prev.filename && prev.filename !== currentTrack.filename && !prev.recorded) {
      if (prev.duration > 0 && prev.time > 0.5) {
        const ratio = prev.time / prev.duration
        recordCompletion(prev.filename, ratio * 100)
      }
    }
    lastTrackRef.current = {
      filename: currentTrack.filename,
      time: prev.time,
      duration: prev.duration,
      recorded: false
    }
  }, [currentTrack.filename])



  useEffect(() => {
    const intervalId = window.setInterval(() => {
      if (amllMode === 'amll') return
      const audio = getIframeAudio()
      if (!audio) return
      const nextDuration = Number.isFinite(audio.duration) ? audio.duration : 0
      const nextTime = Number.isFinite(audio.currentTime) ? audio.currentTime : 0
      const nextPlaying = !audio.paused
      const lastSync = lastSyncRef.current
      const timeChanged = Math.abs(nextTime - lastSync.time) >= 0.2
      const durationChanged = Math.abs(nextDuration - lastSync.duration) >= 0.2
      const playingChanged = nextPlaying !== lastSync.playing

      if (audio.ended) {
        if (!endedHandledRef.current) {
          endedHandledRef.current = true
          if (playbackMode === 'single') {
            audio.currentTime = 0
            audio.play().catch(() => {})
          } else {
            recordCompletion(currentTrack.filename, 100)
            if (currentTrack.filename) {
              lastTrackRef.current.recorded = true
            }
            handleNext()
          }
        }
      } else if (endedHandledRef.current) {
        endedHandledRef.current = false
      }

      if (timeChanged || durationChanged) {
        setDuration(nextDuration)
        setCurrentTime(nextTime)
        setProgress(nextDuration > 0 ? (nextTime / nextDuration) * 100 : 0)
        lastSync.time = nextTime
        lastSync.duration = nextDuration
        lastTrackRef.current.time = nextTime
        lastTrackRef.current.duration = nextDuration
      }

      if (playingChanged) {
        setIsPlaying(nextPlaying)
        lastSync.playing = nextPlaying
      }
    }, 1000)

    return () => {
      window.clearInterval(intervalId)
    }
  }, [playbackMode, activeQueue, currentIndex])

  useEffect(() => {
    if (amllMode !== 'amll') return
    const intervalId = window.setInterval(async () => {
      try {
        const resp = await fetch('/amll/state')
        if (!resp.ok) return
        const data = await resp.json()
        const song = data?.song || {}
        const rawDuration = song.duration_ms || song.duration || song.length || 0
        const durationSec = rawDuration > 1000 ? rawDuration / 1000 : rawDuration
        const progressMs = data?.progress_ms || 0
        const timeSec = progressMs / 1000
        setDuration(durationSec)
        setCurrentTime(timeSec)
        setProgress(durationSec > 0 ? (timeSec / durationSec) * 100 : 0)
        if (amllProgressRef.current !== progressMs) {
          setIsPlaying(true)
          amllProgressRef.current = progressMs
        } else {
          setIsPlaying(false)
        }
      } catch (error) {
        // ignore AMLL sync errors
      }
    }, 1000)

    return () => {
      window.clearInterval(intervalId)
    }
  }, [amllMode])

  const filteredSongs = useMemo(() => {
    if (!searchTerm.trim()) return songs
    const keyword = searchTerm.trim().toLowerCase()
    return songs.filter((song) => {
      const haystack = `${song.title} ${song.artists?.join(' ')}`.toLowerCase()
      return haystack.includes(keyword)
    })
  }, [songs, searchTerm])

  const selectedStats = useMemo(() => {
    if (!selectedSong?.filename) return null
    const entry = listenStats[selectedSong.filename] || { completions: [], listens: [] }
    const completions = entry.completions || []
    const listens = entry.listens || []
    const completionRate = median(completions)
    return {
      listens: listens.length || completions.length,
      completionRate,
      trend: completions.slice(-12),
      lastListened: listens[listens.length - 1] || null
    }
  }, [listenStats, selectedSong])

  const currentTrackStats = useMemo(() => {
    if (!currentTrack?.filename) return null
    const entry = listenStats[currentTrack.filename] || { completions: [], listens: [] }
    const completions = entry.completions || []
    const listens = entry.listens || []
    return {
      listens: listens.length || completions.length,
      completionRate: median(completions),
      trend: completions.slice(-6),
      lastListened: listens[listens.length - 1] || null
    }
  }, [listenStats, currentTrack])

  const toggleFilter = (filterId) => {
    setActiveFilters((prev) => {
      if (filterId === 'all') return ['all']
      const has = prev.includes(filterId)
      const withoutAll = prev.filter((item) => item !== 'all')
      const next = has ? withoutAll.filter((item) => item !== filterId) : [...withoutAll, filterId]
      return next.length ? next : ['all']
    })
  }

  const historySongs = useMemo(() => {
    if (!playHistory.length) return songs
    const map = new Map(songs.map((song) => [song.filename, song]))
    return playHistory.map((id) => map.get(id)).filter(Boolean)
  }, [playHistory, songs])

  const renderContent = () => {
    switch (activeTab) {
      case 'home':
        return (
          <HomeView
            isLoading={isLoading}
            loadError={loadError}
            songs={historySongs}
            playSong={playSong}
            isSongLiked={isSongLiked}
            toggleSongLike={toggleSongLike}
            onSongDragStart={handleDragStart}
          />
        )
      case 'search':
        return (
          <SearchView
            songs={filteredSongs}
            searchTerm={searchTerm}
            setSearchTerm={setSearchTerm}
            recentSearches={recentSearches}
            onCommitSearch={commitRecentSearch}
            playSong={playSong}
            isSongLiked={isSongLiked}
            toggleSongLike={toggleSongLike}
            onSongDragStart={handleDragStart}
          />
        )
      case 'library':
        {
          const filteredLibrarySongs = songs.filter((song) => matchesFilters(song, activeFilters))
          const filteredIds = filteredLibrarySongs.map((song) => song.filename)
          return (
            <LibraryView
              songs={filteredLibrarySongs}
              isLoading={isLoading}
              loadError={loadError}
              selectedTracks={selectedTracks}
              activeFilters={activeFilters}
              onFilterToggle={toggleFilter}
              onSongClick={(song, index, event) => handleSongClick(song, index, event, filteredIds)}
              onSongDragStart={handleDragStart}
              selectedSong={selectedSong}
              songStats={selectedStats}
            onPlayAll={() => {
              if (filteredLibrarySongs.length) {
                playSong(filteredLibrarySongs[0], filteredIds)
              }
            }}
            isSongLiked={isSongLiked}
            toggleSongLike={toggleSongLike}
          />
          )
        }
      case 'playlist': {
        const playlist = playlists.find((pl) => pl.id === activePlaylistId) || playlists[0]
        const playlistTracks = playlist?.tracks || []
        const playlistSongs = playlistTracks
          .map((id) => resolveSongById(id, songLookup))
          .filter(Boolean)
        const filteredPlaylistSongs = playlistSongs.filter((song) => matchesFilters(song, activeFilters))
        const filteredPlaylistIds = filteredPlaylistSongs.map((song) => song.filename)
        return (
          <PlaylistView
            playlist={playlist}
            songs={filteredPlaylistSongs}
            onPlay={(song) => playSong(song, filteredPlaylistIds)}
            onPlayAll={() => {
              if (filteredPlaylistSongs.length) {
                playSong(filteredPlaylistSongs[0], filteredPlaylistIds)
              }
            }}
            isSongLiked={isSongLiked}
            toggleSongLike={toggleSongLike}
            onSongDragStart={handleDragStart}
            activeFilters={activeFilters}
            onFilterToggle={toggleFilter}
          />
        )
      }
      case 'settings':
        return (
            <SettingsView
              uploadStatus={uploadStatus}
              handleStaticImport={handleStaticImport}
              handleStaticDrop={handleStaticDrop}
              isStaticDragActive={isStaticDragActive}
              setIsStaticDragActive={setIsStaticDragActive}
              autoBackupEnabled={autoBackupEnabled}
              setAutoBackupEnabled={setAutoBackupEnabled}
              backupStatus={backupStatus}
              lastBackupAt={lastBackupAt}
              onRunBackup={() => runBackup('manual')}
              onDownloadBackup={downloadBackup}
              anchorAccount={anchorAccount}
              setAnchorAccount={setAnchorAccount}
              anchorPassword={anchorPassword}
              setAnchorPassword={setAnchorPassword}
              anchorId={anchorId}
              anchorStatus={anchorStatus}
              onAnchorBackup={handleAnchorBackup}
              importStatus={importStatus}
              onImportBackup={handleBackupImport}
              staticZipInputRef={staticZipInputRef}
              onRefreshLibrary={refreshLibrary}
              disableCovers={disableCovers}
              setDisableCovers={setDisableCovers}
            />
        )
      default:
        return null
    }
  }

  const NavItem = ({ id, icon: Icon, label }) => (
    <button
      onClick={() => {
        setActiveTab(id)
        if (isPlayerOpen) setIsPlayerOpen(false)
      }}
      className={`flex flex-col md:flex-row items-center md:gap-4 p-2 md:px-6 md:py-3 rounded-xl transition-all w-full md:justify-start
        ${
          activeTab === id
            ? 'text-white bg-white/10 font-medium'
            : 'text-white/50 hover:text-white hover:bg-white/5'
        }`}
    >
      <Icon size={isMobile ? 24 : 20} strokeWidth={activeTab === id ? 2.5 : 2} />
      <span className="text-[10px] md:text-sm mt-1 md:mt-0">{label}</span>
    </button>
  )

  const isLyricsView = isPlayerOpen && playerView === 'lyrics'
  const isLyricsFocus = isLyricsView && isLyricsImmersive
  const baseIframeOpacity = isLyricsView ? 1 : 0.7
  const getIframeOpacity = (slot) => {
    if (slot === activeIframe) return isIframeTransitioning ? 0 : 1
    if (slot === pendingIframe) return isIframeTransitioning ? 1 : 0
    return 0
  }

  return (
    <SettingsContext.Provider value={{ disableCovers }}>
      <div className="app-shell bg-neutral-900 text-white h-screen w-full flex overflow-hidden select-none">
      <div
        className={`fixed inset-0 ${
          isLyricsView ? 'z-[120] pointer-events-auto' : 'z-0 pointer-events-none'
        }`}
      >
        {iframeSrcA || iframeSrcB ? (
          <div className="absolute inset-0">
            {iframeSrcA && (
              <iframe
                ref={iframeARef}
                title="LyricSphere Player A"
                src={iframeSrcA}
                className={`absolute inset-0 w-full h-full border-0 transition-opacity duration-300 ease-out ${
                  isLyricsView ? '' : 'scale-[1.03]'
                }`}
                style={{
                  opacity: getIframeOpacity('A') * baseIframeOpacity,
                  pointerEvents: isLyricsView && activeIframe === 'A' ? 'auto' : 'none'
                }}
                onLoad={() => onIframeReady('A')}
              />
            )}
            {iframeSrcB && (
              <iframe
                ref={iframeBRef}
                title="LyricSphere Player B"
                src={iframeSrcB}
                className={`absolute inset-0 w-full h-full border-0 transition-opacity duration-300 ease-out ${
                  isLyricsView ? '' : 'scale-[1.03]'
                }`}
                style={{
                  opacity: getIframeOpacity('B') * baseIframeOpacity,
                  pointerEvents: isLyricsView && activeIframe === 'B' ? 'auto' : 'none'
                }}
                onLoad={() => onIframeReady('B')}
              />
            )}
          </div>
        ) : (
          <div
            className={`w-full h-full ${backgroundUrl ? '' : 'bg-gradient-to-br from-slate-900 via-slate-800 to-slate-950'}`}
            style={
              backgroundUrl
                ? {
                    backgroundImage: `url(${backgroundUrl})`,
                    backgroundSize: 'cover',
                    backgroundPosition: 'center'
                  }
                : undefined
            }
          />
        )}
        <div
          className={`absolute inset-0 pointer-events-none ${
            isLyricsView ? 'bg-transparent backdrop-blur-0' : 'bg-black/20 backdrop-blur-xl'
          }`}
        />
      </div>

      {!isMobile && (
        <aside className="w-64 z-10 flex flex-col bg-black/20 backdrop-blur-xl border border-white/10 p-4 rounded-3xl shadow-[0_20px_40px_rgba(0,0,0,0.35)] m-4">
          <div className="mb-8 px-6 pt-4 flex items-center gap-2">
            <div className="w-8 h-8 rounded-full bg-gradient-to-tr from-sky-500 to-emerald-400 flex items-center justify-center">
              <span className="font-bold text-lg">L</span>
            </div>
            <span className="text-xl font-bold tracking-tight">LyricSphere</span>
          </div>

          <nav className="flex-1 space-y-1">
            <NavItem id="home" icon={Home} label="首页" />
            <NavItem id="search" icon={Search} label="搜索" />
            <NavItem id="library" icon={Library} label="资料库" />
          </nav>

          <div className="mt-auto space-y-3">
            <div className="px-6">
              <div className="text-[10px] font-bold uppercase tracking-[0.3em] text-white/30 mb-2">歌单</div>
              <div className="space-y-1">
                {playlists.map((playlist) => (
                  <div
                    key={playlist.id}
                    role="button"
                    tabIndex={0}
                    onDragOver={(event) => {
                      event.preventDefault()
                      event.dataTransfer.dropEffect = 'copy'
                    }}
                    onDrop={(event) => handleDropToPlaylist(playlist.id, event)}
                    onClick={() => {
                      setActivePlaylistId(playlist.id)
                      setActiveFilters(['all'])
                      setActiveTab('playlist')
                      if (isPlayerOpen) setIsPlayerOpen(false)
                    }}
                    className={`flex items-center justify-between px-3 py-2 rounded-lg transition-colors border cursor-pointer ${
                      activeTab === 'playlist' && activePlaylistId === playlist.id
                        ? 'text-white bg-white/10 border-white/10'
                        : 'text-white/60 hover:text-white hover:bg-white/5 border-transparent hover:border-white/10'
                    }`}
                  >
                    <div className="flex items-center gap-3">
                      {playlist.id === 'like' ? (
                        <Heart size={16} className="text-emerald-300" />
                      ) : (
                        <ListMusic size={16} className="text-white/50" />
                      )}
                      <span className="text-sm font-medium">{playlist.name}</span>
                    </div>
                    <span className="text-[10px] text-white/40">{playlist.tracks.length}</span>
                  </div>
                ))}
              </div>
            </div>
            <div className="px-6 py-4">
              <button
                type="button"
                onClick={() => setShowPlaylistCreator(true)}
                className="flex w-full items-center gap-3 text-white/50 hover:text-white cursor-pointer transition-colors"
              >
                <Plus size={20} className="bg-white/10 p-1 rounded-md box-content" />
                <span className="text-sm font-medium">新建歌单</span>
              </button>
            </div>
            {showPlaylistCreator && (
              <div className="px-6 pb-4">
                <form
                  onSubmit={handleCreatePlaylist}
                  className="rounded-2xl border border-white/10 bg-white/5 p-3 shadow-[0_10px_24px_rgba(0,0,0,0.35)]"
                >
                  <div className="flex items-center justify-between mb-3">
                    <span className="text-xs font-bold uppercase tracking-widest text-white/50">新建歌单</span>
                    <button
                      type="button"
                      onClick={() => {
                        setShowPlaylistCreator(false)
                        setNewPlaylistName('')
                      }}
                      className="text-white/40 hover:text-white transition-colors"
                    >
                      <X size={14} />
                    </button>
                  </div>
                  <input
                    value={newPlaylistName}
                    onChange={(event) => setNewPlaylistName(event.target.value)}
                    placeholder="输入歌单名称"
                    className="w-full rounded-xl bg-black/40 border border-white/10 px-3 py-2 text-sm focus:outline-none focus:border-emerald-400/50 placeholder:text-white/20"
                    autoFocus
                  />
                  <div className="flex items-center gap-2 mt-3">
                    <button
                      type="submit"
                      className="flex-1 rounded-xl bg-white text-black text-xs font-bold py-2 hover:bg-white/90 transition-colors"
                    >
                      创建
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setShowPlaylistCreator(false)
                        setNewPlaylistName('')
                      }}
                      className="flex-1 rounded-xl bg-white/5 text-white/70 text-xs font-medium py-2 hover:bg-white/10 transition-colors"
                    >
                      取消
                    </button>
                  </div>
                </form>
              </div>
            )}
            <NavItem id="settings" icon={Settings} label="设置" />
            <div className="p-4">
              <a
                href="http://127.0.0.1:5000/"
                className="flex items-center gap-3 p-3 rounded-lg bg-gradient-to-r from-sky-500/20 to-emerald-400/20 border border-white/5 hover:border-emerald-300/40 transition-colors"
              >
                <User size={18} className="text-sky-300" />
                <div className="flex-1 overflow-hidden">
                  <p className="text-xs font-medium truncate">返回工具模式</p>
                  <p className="text-[10px] text-white/50">打开本地控制台</p>
                </div>
              </a>
            </div>
          </div>
        </aside>
      )}

      <main className="flex-1 z-10 relative flex flex-col h-full overflow-visible m-4">
        <div className="h-4" />

        <div className="flex-1 overflow-y-auto overflow-x-hidden pb-32 scroll-smooth">
          {renderContent()}
        </div>

        {!isPlayerOpen && (
          <div
            onClick={() => setIsPlayerOpen(true)}
            className="fixed bottom-[calc(env(safe-area-inset-bottom)+76px)] md:bottom-4 left-4 right-4 md:left-[calc(16rem+3rem)] md:right-8 h-16 md:h-20 bg-black/60 md:bg-black/50 backdrop-blur-xl border border-white/10 rounded-2xl overflow-hidden flex items-center px-4 md:px-8 gap-4 cursor-pointer hover:bg-black/70 transition-colors group z-50 shadow-[0_12px_30px_rgba(0,0,0,0.35)]"
          >
            <div className="absolute top-0 left-0 right-0 h-[2px] bg-white/5">
              <div className="h-full bg-sky-400 transition-[width] duration-1000 ease-out" style={{ width: `${progress}%` }} />
            </div>

            <CoverImage
              src={coverUrl}
              className="w-10 h-10 md:w-12 md:h-12 rounded-md object-cover shadow-lg"
              alt="Album"
            />
            <div className="flex-1 min-w-0">
              <h4
                className="font-medium text-sm md:text-base title-ellipsis-1"
                title={currentTrack.title || '未选择歌曲'}
              >
                {currentTrack.title || '未选择歌曲'}
              </h4>
              <p
                className="text-xs md:text-sm text-white/50 title-ellipsis-1 flex items-center gap-1"
                title={(currentTrack.artists || []).join(', ') || '未知艺人'}
              >
                {currentTrack.hasDuet && (
                  <span className="px-1 rounded bg-white/10 text-[9px] font-bold text-sky-200">
                    Duet
                  </span>
                )}
                {(currentTrack.artists || []).join(', ') || '未知艺人'}
              </p>
            </div>

            <div className="flex items-center gap-3 md:gap-6">
              <button
                onClick={(event) => {
                  event.stopPropagation()
                  handleToggleLike()
                }}
                className={`transition-all ${
                  isLiked ? 'text-emerald-300 scale-110' : 'text-white/70 hover:text-emerald-400 hover:scale-110'
                }`}
              >
                <Heart size={20} fill={isLiked ? 'currentColor' : 'none'} />
              </button>
              {!isMobile && (
                <button className="text-white hover:text-white transition-colors" onClick={handlePrev}>
                  <SkipBack size={24} fill="currentColor" />
                </button>
              )}
              <button
                onClick={togglePlay}
                className="w-10 h-10 rounded-full bg-white text-black flex items-center justify-center hover:scale-105 transition-transform shadow-lg shadow-white/10"
              >
                {isPlaying ? (
                  <Pause size={20} fill="currentColor" />
                ) : (
                  <Play size={20} fill="currentColor" className="ml-1" />
                )}
              </button>
              <button
                className="text-white hover:text-white transition-colors"
                onClick={(event) => {
                  event.stopPropagation()
                  handleNext()
                }}
              >
                <SkipForward size={24} fill="currentColor" />
              </button>
              {!isMobile && (
                <button
                  className="text-white/50 hover:text-white transition-colors"
                  onClick={(event) => {
                    event.stopPropagation()
                    openQueueModal()
                  }}
                  title="播放队列"
                >
                  <ListMusic size={20} />
                </button>
              )}
            </div>
          </div>
        )}

        {isMobile && (
          <nav className="absolute bottom-4 left-4 right-4 h-16 pb-[env(safe-area-inset-bottom)] bg-black/80 backdrop-blur-xl border border-white/10 rounded-2xl flex justify-around items-center z-50 shadow-[0_12px_30px_rgba(0,0,0,0.35)]">
            <NavItem id="home" icon={Home} label="首页" />
            <NavItem id="search" icon={Search} label="搜索" />
            <NavItem id="library" icon={Library} label="资料库" />
            <NavItem id="settings" icon={Settings} label="设置" />
          </nav>
        )}
      </main>

      <div
        className={`fixed inset-0 z-[200] flex flex-col transition-transform duration-500 ease-[cubic-bezier(0.32,0.72,0,1)] ${
          isLyricsView ? 'bg-transparent backdrop-blur-0' : 'bg-black/35 backdrop-blur-2xl'
        } ${isPlayerOpen ? 'translate-y-0 pointer-events-auto' : 'translate-y-full pointer-events-none'}`}
      >
        <div className="relative z-20 flex items-start justify-between p-6 mt-safe-top">
          <button
            onClick={() => setIsPlayerOpen(false)}
            className={`p-2 text-white/50 hover:text-white transition-opacity ${
              isLyricsFocus ? 'opacity-0 pointer-events-none' : 'opacity-100'
            }`}
          >
            <ChevronDown size={28} />
          </button>

          <div
            className={`flex gap-1 bg-black/20 backdrop-blur-md rounded-full p-1 transition-opacity ${
              isLyricsFocus ? 'opacity-0 pointer-events-none' : 'opacity-100'
            }`}
          >
            {['cover', 'lyrics', 'queue'].map((view) => (
              <button
                key={view}
                onClick={() => setPlayerView(view)}
                className={`px-4 py-1.5 rounded-full text-xs font-medium transition-all ${
                  playerView === view ? 'bg-white/10 text-white shadow-sm' : 'text-white/40 hover:text-white/70'
                }`}
              >
                {view === 'cover' ? '歌曲' : view === 'lyrics' ? '歌词' : '队列'}
              </button>
            ))}
          </div>

          <div className="flex flex-col items-center gap-2 p-2 text-white/50">
            <button
              className="p-2 rounded-full bg-white/5 border border-white/10 hover:text-white transition-colors"
              title="更多"
            >
              <MoreHorizontal size={20} />
            </button>
            <button
              className={`p-2 rounded-full border transition-colors ${
                playerView === 'lyrics'
                  ? 'bg-white/5 border-white/10 hover:text-white'
                  : 'bg-white/5 border-white/5 text-white/20 pointer-events-none'
              }`}
              onClick={toggleFullscreen}
              title="全屏"
            >
              <Maximize size={20} />
            </button>
            <button
              className={`p-2 rounded-full border transition-colors ${
                playerView === 'lyrics'
                  ? 'bg-white/5 border-white/10 hover:text-white'
                  : 'bg-white/5 border-white/5 text-white/20 pointer-events-none'
              }`}
              onClick={() => setIsLyricsImmersive((prev) => !prev)}
              title={isLyricsImmersive ? '显示控件' : '隐藏控件'}
            >
              <EyeOff size={20} />
            </button>
            <div className="relative h-11 w-11">
              <button
                className={`absolute right-0 top-0 flex items-center rounded-full border transition-[width,background-color,border-color] duration-300 ease-out origin-right overflow-hidden ${
                  playerView === 'lyrics'
                    ? 'bg-white/5 border-white/10 hover:text-white'
                    : 'bg-white/5 border-white/5 text-white/20 pointer-events-none'
                } ${showFontControl ? 'w-52 px-4 justify-start' : 'w-11 px-0 justify-center'} h-11`}
                onClick={() => setShowFontControl((prev) => !prev)}
                title="字体大小"
              >
                <span
                  className="font-semibold leading-none shrink-0"
                  style={{ fontSize: '18px', lineHeight: '18px' }}
                >
                  Aa
                </span>
                <div
                  className={`absolute left-12 right-4 top-1/2 -translate-y-1/2 transition-[opacity,transform] duration-300 ease-out origin-right ${
                    showFontControl ? 'opacity-100 scale-x-100' : 'opacity-0 scale-x-0 pointer-events-none'
                  }`}
                  onClick={(event) => event.stopPropagation()}
                >
                  <input
                    type="range"
                    min="0.5"
                    max="1.5"
                    step="0.05"
                    value={lyricScale}
                    onChange={(event) => {
                      const nextValue = Number.parseFloat(event.target.value)
                      setLyricScale(nextValue)
                    applyIframeFontScale(nextValue)
                  }}
                  className="w-full accent-white"
                  />
                </div>
              </button>
            </div>
          </div>
        </div>


        <div className="relative z-20 flex-1 flex flex-col justify-center px-8 md:px-20 overflow-hidden">
          <div
            className={`flex flex-col items-center justify-center h-full transition-opacity duration-300 ${
              playerView === 'cover' ? 'opacity-100 flex' : 'opacity-0 hidden'
            }`}
          >
            <div className="relative group w-full max-w-sm aspect-square shadow-[0_20px_50px_rgba(0,0,0,0.5)] rounded-2xl overflow-hidden mb-12">
              <CoverImage
                src={coverUrl}
                className={`w-full h-full object-cover transition-transform duration-700 ${isPlaying ? 'scale-100' : 'scale-105'}`}
                alt="Cover"
              />
              <div className="absolute bottom-4 right-4 bg-black/50 backdrop-blur-md px-2 py-1 rounded text-[10px] font-mono border border-white/10">
                {currentTrack.hasDuet ? 'Duet' : 'Stereo'}
              </div>
            </div>

            <div className="relative w-full max-w-2xl mx-auto mb-2">
              <div className="text-center pr-12">
                <h2
                  className="text-3xl font-bold tracking-tight mb-2 title-ellipsis-2 leading-tight break-words"
                  title={currentTrack.title || '未选择歌曲'}
                >
                  {currentTrack.title || '未选择歌曲'}
                </h2>
                <p
                  className="text-lg text-white/60 title-ellipsis-1 flex items-center justify-center gap-2 break-words"
                  title={(currentTrack.artists || []).join(', ') || '未知艺人'}
                >
                  {(currentTrack.artists || []).join(', ') || '未知艺人'}
                  {currentTrack.hasDuet && (
                    <span className="text-[10px] border border-white/20 px-1.5 rounded text-white/40">Duet</span>
                  )}
                </p>
                <div className="mt-4 grid grid-cols-3 gap-3 text-xs text-left">
                  <div className="rounded-xl bg-white/5 border border-white/10 px-3 py-2">
                    <div className="text-[10px] uppercase tracking-wider text-white/40">听的次数</div>
                    <div className="text-base font-bold text-white mt-1">{currentTrackStats?.listens || 0}</div>
                  </div>
                  <div className="rounded-xl bg-white/5 border border-white/10 px-3 py-2">
                    <div className="text-[10px] uppercase tracking-wider text-white/40">完听率</div>
                    <div className="text-base font-bold text-emerald-300 mt-1">{currentTrackStats?.completionRate || 0}%</div>
                  </div>
                  <div className="rounded-xl bg-white/5 border border-white/10 px-3 py-2">
                    <div className="text-[10px] uppercase tracking-wider text-white/40">趋势</div>
                    {currentTrackStats?.trend?.length ? (
                      <Sparkline values={currentTrackStats.trend} className="mt-1" />
                    ) : (
                      <div className="text-[10px] text-white/40 mt-2">暂无记录</div>
                    )}
                  </div>
                </div>
              </div>
              <button
                onClick={handleToggleLike}
                className={`absolute right-0 top-1/2 -translate-y-1/2 p-3 transition-colors ${
                  isLiked ? 'text-emerald-300' : 'text-white/50 hover:text-emerald-400'
                }`}
              >
                <Heart size={28} fill={isLiked ? 'currentColor' : 'none'} />
              </button>
            </div>
          </div>

          <div
            className={`h-full w-full flex flex-col items-center justify-center transition-opacity duration-300 ${
              playerView === 'lyrics' ? 'opacity-100 flex' : 'opacity-0 hidden'
            }`}
          >
            {lyricsStatus && (
              <div className="text-sm text-white/60 bg-black/40 px-4 py-2 rounded-full">
                {lyricsStatus}
              </div>
            )}
          </div>

          <div
            className={`h-full w-full max-w-2xl mx-auto flex flex-col transition-opacity duration-300 ${
              playerView === 'queue' ? 'opacity-100 flex' : 'opacity-0 hidden'
            }`}
          >
            <h3 className="text-xs font-bold text-white/40 uppercase tracking-widest mb-4">Now Playing</h3>
            <div className="bg-white/5 rounded-xl p-3 flex items-center gap-4 mb-8 border border-white/10">
              <CoverImage src={coverUrl} className="w-12 h-12 rounded" alt="cover" />
              <div className="flex-1">
                <div className="text-sm font-bold text-sky-300">正在播放</div>
                <div className="text-base font-medium title-ellipsis-1" title={currentTrack.title || '未选择歌曲'}>
                  {currentTrack.title || '未选择歌曲'}
                </div>
              </div>
              <div className="text-xs px-2 py-1 bg-white/10 rounded">来自: 资料库</div>
            </div>

            <div className="flex items-center justify-between mb-4">
              <h3
                className="text-xs font-bold text-white/40 uppercase tracking-widest cursor-pointer hover:text-white/70 transition-colors"
                onClick={() => scrollToNextUp(queueListRef, nextUpRef)}
                title={nextUpSong?.title || '暂无下一首'}
              >
                Next Up{nextUpSong?.title ? ` · ${nextUpSong.title}` : ''}
              </h3>
              <button className="text-xs text-white/40 hover:text-white">Clear</button>
            </div>
          <div className="flex-1 overflow-y-auto space-y-1 pr-2" ref={queueListRef}>
              {activeQueue.map((song, idx) => {
                const isCurrent = idx === currentIndex
                const isNext = nextUpSong?.filename === song.filename
                return (
                  <div
                    key={song.filename || idx}
                    className={`group flex items-center gap-4 p-3 rounded-lg transition-colors cursor-pointer ${
                      isCurrent ? 'bg-white/10' : 'hover:bg-white/10'
                    }`}
                    onClick={() => playSong(song, playQueueIds)}
                    ref={isNext ? nextUpRef : null}
                  >
                    <span className={`w-4 text-center font-mono text-xs ${isCurrent ? 'text-emerald-300' : 'text-white/30'}`}>
                      {idx + 1}
                    </span>
                    <CoverImage src={song.albumImgSrc} className="w-10 h-10 rounded opacity-70 group-hover:opacity-100" alt={song.title} />
                    <div className="flex-1 min-w-0">
                      <div
                        className={`font-medium title-ellipsis-1 ${isCurrent ? 'text-emerald-200' : 'text-white/80 group-hover:text-white'}`}
                        title={song.title || '未命名歌曲'}
                      >
                        {song.title || '未命名歌曲'}
                      </div>
                      <div
                        className="text-xs text-white/40 title-ellipsis-1"
                        title={(song.artists || []).join(', ') || '未知艺人'}
                      >
                        {(song.artists || []).join(', ') || '未知艺人'}
                      </div>
                      <SongTags song={song} className="mt-1" />
                    </div>
                    <button
                      className={`p-2 rounded-full border transition-all ${
                        isSongLiked(song.filename)
                          ? 'border-emerald-400/60 text-emerald-300 bg-white/5 opacity-100'
                          : 'border-white/10 text-white/40 hover:text-emerald-200 hover:border-emerald-300/60 opacity-0 group-hover:opacity-100'
                      }`}
                      onClick={(event) => {
                        event.stopPropagation()
                        toggleSongLike(song.filename)
                      }}
                    >
                      <Heart size={14} fill={isSongLiked(song.filename) ? 'currentColor' : 'none'} />
                    </button>
                  </div>
                )
              })}
            </div>
          </div>
        </div>

        <div
          className={`relative z-20 px-8 md:px-20 pb-12 pt-6 bg-gradient-to-t from-black/80 to-transparent transition-opacity ${
            isLyricsFocus ? 'opacity-0 pointer-events-none' : 'opacity-100'
          }`}
        >
          <div className="flex items-center gap-4 text-xs font-mono text-white/40 mb-4 group">
            <span>{formatTime(currentTime)}</span>
            <div
              className="flex-1 h-1 bg-white/10 rounded-full cursor-pointer relative overflow-hidden group-hover:h-2 transition-all"
              onClick={(event) => {
                const rect = event.currentTarget.getBoundingClientRect()
                const ratio = rect.width ? (event.clientX - rect.left) / rect.width : 0
                const nextTime = Math.max(0, Math.min(duration, duration * ratio))
                const audio = getIframeAudio()
                if (audio) audio.currentTime = nextTime
              }}
            >
              <div className="absolute inset-y-0 left-0 bg-sky-400 rounded-full transition-[width] duration-1000 ease-out" style={{ width: `${progress}%` }} />
            </div>
            <span>{formatTime(duration)}</span>
          </div>

          <div className="flex items-center justify-between max-w-2xl mx-auto">
            <div className="flex items-center gap-3">
            <button
              className={`transition-colors ${
                playbackMode === 'shuffle' ? 'text-sky-300' : 'text-white/40 hover:text-white'
              }`}
              onClick={() =>
                setPlaybackMode((mode) => (mode === 'shuffle' ? 'list' : 'shuffle'))
              }
            >
              <Shuffle size={20} />
            </button>
              <div className="relative">
                <button
                  className="flex items-center gap-2 text-white/30 hover:text-white text-xs font-medium bg-white/5 px-3 py-1.5 rounded-full transition-colors"
                  onClick={() => setAmllPopupOpen((prev) => !prev)}
                >
                  <MonitorSpeaker size={14} /> {amllMode === 'amll' ? 'AMLL Player' : '本机'}
                </button>
                {amllPopupOpen && (
                  <div className="absolute left-0 bottom-full mb-3 w-72 rounded-2xl border border-white/10 bg-black/70 backdrop-blur-xl p-4 shadow-[0_20px_40px_rgba(0,0,0,0.35)] z-50">
                    <div className="flex items-center justify-between mb-3">
                      <div className="text-xs font-bold tracking-widest text-white/40">播放模式</div>
                      <button
                        className="text-[10px] text-white/40 hover:text-white"
                        onClick={() => fetchAmllSnapshot(true)}
                      >
                        刷新
                      </button>
                    </div>
                    <div className="flex gap-2 mb-4">
                      <button
                        className={`flex-1 px-3 py-1.5 rounded-full text-xs font-medium border transition-colors ${
                          amllMode === 'local'
                            ? 'bg-white text-black border-white'
                            : 'bg-white/5 text-white/60 border-white/10 hover:border-white/30'
                        }`}
                        onClick={() => {
                          setAmllMode('local')
                          setAmllPopupOpen(false)
                        }}
                      >
                        本机
                      </button>
                      <button
                        className={`flex-1 px-3 py-1.5 rounded-full text-xs font-medium border transition-colors ${
                          amllMode === 'amll'
                            ? 'bg-white text-black border-white'
                            : 'bg-white/5 text-white/60 border-white/10 hover:border-white/30'
                        }`}
                        onClick={() => {
                          setAmllMode('amll')
                          setAmllPopupOpen(false)
                          setPlayerView('lyrics')
                        }}
                      >
                        AMLL
                      </button>
                    </div>
                    <div className="rounded-xl border border-white/10 bg-white/5 p-3 space-y-2">
                      <div className="flex items-center gap-3">
                        <CoverImage
                          src={resolveAmllCoverUrl(
                            amllSnapshot?.song?.cover_data_url ||
                              amllSnapshot?.song?.cover_file_url ||
                              amllSnapshot?.song?.cover ||
                              amllSnapshot?.song?.albumImgSrc ||
                              amllSnapshot?.song?.coverUrl ||
                              DEFAULT_AMLL_COVER
                          )}
                          className="w-12 h-12 rounded-lg object-cover"
                          alt="AMLL cover"
                        />
                        <div className="min-w-0">
                          <div className="text-xs text-white/40">歌曲名</div>
                          <div className="text-sm font-semibold title-ellipsis-1">
                            {amllSnapshot?.song?.musicName || '未提供'}
                          </div>
                          <div className="text-[10px] text-white/50 title-ellipsis-1">
                            {(amllSnapshot?.song?.artists || []).join(' / ') || '未提供'}
                          </div>
                        </div>
                      </div>
                      <div className="text-[10px] text-white/40">
                        歌词：{amllSnapshot?.lines?.length ? `${amllSnapshot.lines.length} 行` : '暂无数据'}
                      </div>
                      <div className="text-[10px] text-white/60 whitespace-pre-line">
                        {amllStatus === 'loading' ? '正在读取 AMLL...' : renderAmllPreview(amllSnapshot?.lines)}
                      </div>
                      <button
                        className="w-full mt-2 py-2 rounded-xl text-xs font-semibold bg-white/10 hover:bg-white/15 border border-white/10 transition-colors"
                        onClick={() => window.open('/lyrics-amll', '_blank')}
                      >
                        打开 AMLL Player
                      </button>
                    </div>
                  </div>
                )}
              </div>
            </div>
            <div className="flex items-center gap-8">
              <button className="text-white hover:text-emerald-400 transition-colors" onClick={handlePrev}>
                <SkipBack size={32} fill="currentColor" />
              </button>
              <button
                onClick={togglePlay}
                className="w-16 h-16 rounded-full bg-white text-black flex items-center justify-center hover:scale-110 active:scale-95 transition-all shadow-xl shadow-white/20"
              >
                {isPlaying ? (
                  <Pause size={32} fill="currentColor" />
                ) : (
                  <Play size={32} fill="currentColor" className="ml-1" />
                )}
              </button>
              <button className="text-white hover:text-emerald-400 transition-colors" onClick={handleNext}>
                <SkipForward size={32} fill="currentColor" />
              </button>
            </div>
            <div className="flex items-center gap-4">
              <button
                className={`transition-colors ${
                  playbackMode === 'single' ? 'text-emerald-300' : 'text-white/40 hover:text-white'
                }`}
                onClick={() =>
                  setPlaybackMode((mode) => (mode === 'single' ? 'list' : 'single'))
                }
              >
                <Repeat size={20} />
              </button>
              <button
                className={`transition-colors ${
                  isLiked ? 'text-emerald-300' : 'text-white/40 hover:text-white'
                }`}
                onClick={handleToggleLike}
                title="喜欢"
              >
                <Heart size={18} fill={isLiked ? 'currentColor' : 'none'} />
              </button>
              <button className="text-white/30 hover:text-white">
                <Share2 size={18} />
              </button>
              <button
                className="text-white/30 hover:text-white"
                onClick={() => openQueueModal()}
                title="播放队列"
              >
                <ListMusic size={18} />
              </button>
            </div>
          </div>
        </div>
      </div>

      {showQueueModal && (
        <div className="fixed inset-0 z-[260]">
          <button
            type="button"
            className={`absolute inset-0 bg-black/20 transition-opacity ${queueOpen ? 'opacity-100' : 'opacity-0'}`}
            onClick={closeQueueModal}
            aria-label="关闭播放队列"
          />
          <div
            className={`absolute right-4 top-24 w-[22rem] max-w-[85vw] rounded-3xl border border-white/10 bg-black/70 backdrop-blur-xl shadow-[0_20px_40px_rgba(0,0,0,0.45)] p-5 transition-all duration-300 ease-out ${
              queueOpen ? 'translate-x-0 opacity-100' : 'translate-x-6 opacity-0'
            }`}
          >
            <div className="flex items-center justify-between mb-4">
              <div>
                <h3 className="text-base font-bold">播放队列</h3>
                <p className="text-[10px] text-white/50">当前待播放 {queue.length} 首</p>
                {nextUpSong?.title && (
                  <button
                    className="text-[10px] text-white/40 hover:text-white transition-colors mt-1"
                    onClick={() => scrollToNextUp(queueModalListRef, nextUpModalRef)}
                  >
                    下一首: {nextUpSong.title}
                  </button>
                )}
              </div>
              <button
                className="p-2 rounded-full bg-white/5 border border-white/10 text-white/60 hover:text-white transition-colors"
                onClick={closeQueueModal}
              >
                <X size={16} />
              </button>
            </div>

            <div className="max-h-[55vh] overflow-y-auto space-y-2 pr-1" ref={queueModalListRef}>
              {activeQueue.map((song, index) => {
                const isCurrent = index === currentIndex
                const isNext = nextUpSong?.filename === song.filename
                return (
                  <div
                    key={song.filename || index}
                    className={`flex items-center gap-3 rounded-2xl border border-white/5 bg-white/5 transition-colors cursor-pointer ${
                      isCurrent ? 'border-emerald-400/40 bg-emerald-400/10' : 'hover:bg-white/10'
                    }`}
                    onClick={() => {
                      playSong(song, playQueueIds)
                      closeQueueModal()
                    }}
                    ref={isNext ? nextUpModalRef : null}
                  >
                    <span className="text-[10px] text-white/40 w-5 text-center font-mono">{index + 1}</span>
                    <CoverImage src={song.albumImgSrc} className="w-9 h-9 rounded-lg object-cover" alt={song.title} />
                    <div className="flex-1 min-w-0">
                      <div className="font-medium title-ellipsis-1">{song.title || '未命名歌曲'}</div>
                      <div className="text-xs text-white/50 title-ellipsis-1">{(song.artists || []).join(', ') || '未知艺人'}</div>
                    </div>
                    <button
                      className={`p-2 rounded-full border transition-colors ${
                        isSongLiked(song.filename)
                          ? 'border-emerald-400/60 text-emerald-300 bg-white/5'
                          : 'border-white/10 text-white/40 hover:text-emerald-200 hover:border-emerald-300/60'
                      }`}
                      onClick={(event) => {
                        event.stopPropagation()
                        toggleSongLike(song.filename)
                      }}
                    >
                      <Heart size={14} fill={isSongLiked(song.filename) ? 'currentColor' : 'none'} />
                    </button>
                  </div>
                )
              })}
              {!queue.length && <div className="text-xs text-white/50">暂无播放队列。</div>}
            </div>
          </div>
        </div>
      )}
      </div>
    </SettingsContext.Provider>
  )
}

const HomeView = ({ isLoading, loadError, songs, playSong, isSongLiked, toggleSongLike, onSongDragStart }) => {
  const [visibleCount, setVisibleCount] = useState(12)
  const [lazyMode, setLazyMode] = useState(false)
  const sentinelRef = useRef(null)
  const history = Array.isArray(songs) ? songs : []
  const historyIds = history.map((song) => song.filename)
  const topHistory = history.slice(0, 3)
  const restHistory = history.slice(3)
  const visibleHistory = restHistory.slice(0, visibleCount)
  const hasMore = visibleCount < restHistory.length
  const heroCover = resolveMediaUrl(topHistory[0]?.albumImgSrc)
  const [heroHasCover, setHeroHasCover] = useState(false)

  useEffect(() => {
    setVisibleCount(12)
    setLazyMode(false)
  }, [songs])

  useEffect(() => {
    setHeroHasCover(Boolean(heroCover))
  }, [heroCover])

  useEffect(() => {
    if (!lazyMode) return
    const target = sentinelRef.current
    if (!target) return

    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            setVisibleCount((prev) => Math.min(prev + 6, restHistory.length))
          }
        })
      },
      { rootMargin: '200px 0px' }
    )

    observer.observe(target)
    return () => observer.disconnect()
  }, [lazyMode, restHistory.length])

  return (
    <div className="p-6 md:p-10 space-y-12 animate-fade-in">
      <section>
        <h2 className="text-2xl font-bold mb-6">下午好，来点灵感？</h2>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <div
            className={`col-span-2 md:col-span-2 aspect-[2/1] rounded-2xl p-6 relative overflow-hidden group cursor-pointer hover:scale-[1.01] transition-transform ${
              heroHasCover ? 'bg-black/30' : 'bg-gradient-to-br from-sky-500 to-emerald-400'
            }`}
            onClick={() => {
              const recent = topHistory[0]
              if (recent) {
                playSong(recent, historyIds)
              }
            }}
            draggable={Boolean(topHistory[0])}
            onDragStart={(event) => {
              if (topHistory[0]) onSongDragStart(event, topHistory[0], 0)
            }}
          >
            {heroHasCover && (
              <>
                <img
                  src={heroCover}
                  alt={topHistory[0]?.title || '最近播放'}
                  className="absolute inset-0 h-full w-full object-cover"
                  onError={() => setHeroHasCover(false)}
                />
                <div className="absolute inset-0 bg-black/20" />
              </>
            )}
            <div className="relative z-10 flex flex-col justify-between h-full">
              <div>
                <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-white/20 backdrop-blur-md text-xs font-bold mb-2">
                  <Radio size={12} /> 最近播放
                </div>
                <h3
                  className="text-3xl font-bold title-ellipsis-2 leading-tight"
                  title={topHistory[0]?.title || '最近播放'}
                >
                  {topHistory[0]?.title || '最近播放'}
                </h3>
                <p className="text-white/70 text-sm mt-2">
                  {(topHistory[0]?.artists || []).join(', ') || '暂无艺人信息'}
                </p>
                <SongTags song={topHistory[0]} className="mt-2" />
              </div>
              <div className="flex items-center gap-3">
                {topHistory[0] && (
                  <button
                    onClick={(event) => {
                      event.stopPropagation()
                      toggleSongLike(topHistory[0].filename)
                    }}
                    className={`p-2 rounded-full border transition-colors ${
                      isSongLiked(topHistory[0].filename)
                        ? 'border-emerald-400/60 text-emerald-300 bg-white/10'
                        : 'border-white/10 text-white/60 hover:text-emerald-200 hover:border-emerald-300/60'
                    }`}
                  >
                    <Heart size={16} fill={isSongLiked(topHistory[0].filename) ? 'currentColor' : 'none'} />
                  </button>
                )}
                <button className="w-12 h-12 rounded-full bg-white text-black flex items-center justify-center shadow-lg hover:scale-110 transition-transform">
                  <Play size={20} fill="currentColor" className="ml-1" />
                </button>
              </div>
            </div>
          </div>
        {topHistory.slice(1, 3).map((song, index) => (
          <div
            key={song.filename}
            onClick={() => playSong(song, historyIds)}
              className="aspect-square rounded-2xl bg-neutral-800 relative group overflow-hidden cursor-pointer"
            draggable
            onDragStart={(event) => onSongDragStart(event, song, index + 1)}
            >
              <button
                onClick={(event) => {
                  event.stopPropagation()
                  toggleSongLike(song.filename)
                }}
                className={`absolute top-3 right-3 z-10 p-2 rounded-full border transition-colors ${
                  isSongLiked(song.filename)
                    ? 'border-emerald-400/60 text-emerald-300 bg-black/40'
                    : 'border-white/10 text-white/60 hover:text-emerald-200 hover:border-emerald-300/60 bg-black/30'
                }`}
              >
                <Heart size={14} fill={isSongLiked(song.filename) ? 'currentColor' : 'none'} />
              </button>
              <CoverImage
                src={song.albumImgSrc}
                className="w-full h-full object-cover opacity-60 group-hover:opacity-80 transition-opacity"
                alt={song.title}
              />
              <div className="absolute bottom-0 left-0 right-0 p-4 bg-gradient-to-t from-black/80 to-transparent">
                <h4 className="font-bold title-ellipsis-1" title={song.title || '未命名歌曲'}>
                  {song.title || '未命名歌曲'}
                </h4>
                <SongTags song={song} className="mt-2" />
              </div>
              <div className="absolute inset-0 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity">
                <div className="w-10 h-10 bg-white/20 backdrop-blur rounded-full flex items-center justify-center text-white">
                  <Play size={16} fill="currentColor" />
                </div>
              </div>
            </div>
          ))}
        </div>
      </section>

      <section>
        <div className="flex items-center justify-between mb-6">
          <h3 className="text-xl font-bold">继续播放</h3>
          {hasMore && (
            <button
              className="text-xs text-white/40 hover:text-white"
              onClick={() => {
                setLazyMode(true)
                setVisibleCount((prev) => Math.min(prev + 6, restHistory.length))
              }}
            >
              查看全部
            </button>
          )}
        </div>
        {isLoading && <div className="text-white/50">正在加载歌曲列表...</div>}
        {loadError && <div className="text-rose-300">加载失败: {loadError}</div>}
        {!isLoading && !songs.length && <div className="text-white/50">暂无歌曲，请先导入。</div>}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {visibleHistory.map((song, index) => (
            <div
              key={song.filename}
              onClick={() => playSong(song, historyIds)}
              className="flex items-center gap-4 p-3 rounded-xl bg-white/5 hover:bg-white/10 transition-colors group cursor-pointer border border-white/5 hover:border-white/10"
              draggable
              onDragStart={(event) => onSongDragStart(event, song, index)}
            >
              <div className="relative w-14 h-14 rounded-lg overflow-hidden shrink-0 bg-neutral-800">
                <CoverImage
                  src={song.albumImgSrc}
                  className="w-full h-full object-cover"
                  alt={song.title}
                />
                <div className="absolute inset-0 bg-black/30 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity">
                  <Play size={16} fill="white" className="text-white" />
                </div>
              </div>
              <div className="flex-1 min-w-0">
                <h4
                  className="font-bold title-ellipsis-1 group-hover:text-sky-200 transition-colors"
                  title={song.title || '未命名歌曲'}
                >
                  {song.title || '未命名歌曲'}
                </h4>
                <p
                  className="text-xs text-white/50 title-ellipsis-1"
                  title={(song.artists || []).join(', ') || '未知艺人'}
                >
                  {(song.artists || []).join(', ') || '未知艺人'}
                </p>
                <SongTags song={song} className="mt-1" />
              </div>
              <button
                className={`p-2 rounded-full border transition-all ${
                  isSongLiked(song.filename)
                    ? 'border-emerald-400/60 text-emerald-300 bg-white/5 opacity-100'
                    : 'border-white/10 text-white/30 hover:text-emerald-200 hover:border-emerald-300/60 opacity-0 group-hover:opacity-100'
                }`}
                onClick={(event) => {
                  event.stopPropagation()
                  toggleSongLike(song.filename)
                }}
              >
                <Heart size={16} fill={isSongLiked(song.filename) ? 'currentColor' : 'none'} />
              </button>
            </div>
          ))}
        </div>
        {lazyMode && hasMore && <div ref={sentinelRef} className="h-8" />}
      </section>
    </div>
  )
}

const SearchView = ({
  songs,
  searchTerm,
  setSearchTerm,
  recentSearches,
  onCommitSearch,
  playSong,
  isSongLiked,
  toggleSongLike,
  onSongDragStart
}) => {
  const handleSubmit = (event) => {
    event.preventDefault()
    onCommitSearch(searchTerm)
  }

  const handleBlur = () => {
    onCommitSearch(searchTerm)
  }

  const handleTagClick = (tag) => {
    setSearchTerm(tag)
    onCommitSearch(tag)
  }

  return (
    <div className="p-6 md:p-10 max-w-4xl mx-auto space-y-8 animate-fade-in">
      <div className="sticky top-0 z-20 bg-neutral-900/80 backdrop-blur-2xl rounded-2xl pb-4 pt-2 px-4 border border-white/10 shadow-[0_12px_30px_rgba(0,0,0,0.25)]">
        <form className="relative" onSubmit={handleSubmit}>
          <Search className="absolute left-4 top-1/2 -translate-y-1/2 text-white/30" size={20} />
          <input
            type="text"
            placeholder="搜索歌曲、艺人、歌词..."
            className="w-full bg-neutral-800 border border-white/10 rounded-2xl py-4 pl-12 pr-4 text-lg focus:outline-none focus:bg-neutral-700 focus:border-sky-400/50 transition-all placeholder:text-white/30"
            value={searchTerm}
            onChange={(event) => setSearchTerm(event.target.value)}
            onBlur={handleBlur}
          />
        </form>
      </div>

      <div>
        <h3 className="text-sm font-bold text-white/40 mb-4 uppercase tracking-wider">最近搜索</h3>
        <div className="flex flex-wrap gap-2">
          {recentSearches.map((tag) => (
            <span
              key={tag}
              className="px-4 py-2 rounded-full bg-white/5 hover:bg-white/10 text-sm cursor-pointer transition-colors border border-white/5"
              onClick={() => handleTagClick(tag)}
            >
              {tag}
            </span>
          ))}
        </div>
      </div>

      <div className="space-y-4">
        <h3 className="text-sm font-bold text-white/40 uppercase tracking-wider">搜索结果</h3>
        {!songs.length && <div className="text-white/50">没有匹配的歌曲。</div>}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {songs.map((song, index) => (
            <div
              key={song.filename}
              className="flex items-center gap-4 p-3 rounded-xl bg-white/5 hover:bg-white/10 transition-colors cursor-pointer"
              onClick={() => playSong(song)}
              draggable
              onDragStart={(event) => onSongDragStart(event, song, index)}
            >
              <CoverImage src={song.albumImgSrc} className="w-12 h-12 rounded-lg object-cover" alt={song.title} />
              <div className="flex-1 min-w-0">
                <div className="font-bold title-ellipsis-1" title={song.title || '未命名歌曲'}>
                  {song.title || '未命名歌曲'}
                </div>
                <div className="text-xs text-white/50 title-ellipsis-1" title={(song.artists || []).join(', ') || '未知艺人'}>
                  {(song.artists || []).join(', ') || '未知艺人'}
                </div>
                <SongTags song={song} className="mt-1" />
              </div>
              <button
                className={`p-2 rounded-full border transition-colors ${
                  isSongLiked(song.filename)
                    ? 'border-emerald-400/60 text-emerald-300 bg-white/5'
                    : 'border-white/10 text-white/40 hover:text-emerald-200 hover:border-emerald-300/60'
                }`}
                onClick={(event) => {
                  event.stopPropagation()
                  toggleSongLike(song.filename)
                }}
              >
                <Heart size={16} fill={isSongLiked(song.filename) ? 'currentColor' : 'none'} />
              </button>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

const PlaylistView = ({
  playlist,
  songs,
  onPlay,
  onPlayAll,
  isSongLiked,
  toggleSongLike,
  onSongDragStart,
  activeFilters,
  onFilterToggle
}) => (
  <div className="p-6 md:p-10 space-y-8 animate-fade-in">
    <div className="flex items-end justify-between border-b border-white/10 pb-6">
      <div>
        <h1 className="text-4xl font-bold mb-2">{playlist?.name || '歌单'}</h1>
        <p className="text-white/50">{songs.length} 首歌曲</p>
      </div>
      <button
        className="px-4 py-2 rounded-lg bg-white text-black text-sm font-bold hover:bg-white/90"
        onClick={onPlayAll}
      >
        播放全部
      </button>
    </div>

    <div className="flex gap-2 overflow-x-auto pb-2 no-scrollbar">
        {[
            { id: 'all', label: '全部' },
            { id: 'background', label: '和声' },
            { id: 'duet', label: '对唱' },
            { id: 'audio-only', label: '仅有音源' }
          ].map((filter) => (
            <button
          key={filter.id}
          className={`px-4 py-1.5 rounded-full text-xs font-medium whitespace-nowrap transition-colors border ${
            activeFilters.includes(filter.id)
              ? 'bg-white text-black border-white'
              : 'bg-transparent text-white/60 border-white/20 hover:border-white/50'
          }`}
          onClick={() => onFilterToggle(filter.id)}
        >
          {filter.label}
        </button>
      ))}
    </div>

    {!songs.length && <div className="text-white/40">暂无歌曲，拖拽歌曲到左侧歌单即可添加。</div>}

    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
      {songs.map((song, index) => (
        <div
          key={song.filename}
          className="flex items-center gap-4 p-3 rounded-xl bg-white/5 hover:bg-white/10 transition-colors cursor-pointer"
          onClick={() => onPlay(song)}
          draggable
          onDragStart={(event) => onSongDragStart(event, song, index)}
        >
          <CoverImage src={song.albumImgSrc} className="w-12 h-12 rounded-lg object-cover" alt={song.title} />
          <div className="flex-1 min-w-0">
            <div className="font-bold title-ellipsis-1" title={song.title || '未命名歌曲'}>
              {song.title || '未命名歌曲'}
            </div>
            <div className="text-xs text-white/50 title-ellipsis-1" title={(song.artists || []).join(', ') || '未知艺人'}>
              {(song.artists || []).join(', ') || '未知艺人'}
            </div>
            <SongTags song={song} className="mt-1" />
          </div>
          <button
            className={`p-2 rounded-full border transition-colors ${
              isSongLiked(song.filename)
                ? 'border-emerald-400/60 text-emerald-300 bg-white/5'
                : 'border-white/10 text-white/40 hover:text-emerald-200 hover:border-emerald-300/60'
            }`}
            onClick={(event) => {
              event.stopPropagation()
              toggleSongLike(song.filename)
            }}
          >
            <Heart size={16} fill={isSongLiked(song.filename) ? 'currentColor' : 'none'} />
          </button>
        </div>
      ))}
    </div>
  </div>
)

const LibraryView = ({
  songs,
  isLoading,
  loadError,
  selectedTracks,
  onSongClick,
  onSongDragStart,
  selectedSong,
  songStats,
  onPlayAll,
  isSongLiked,
  toggleSongLike,
  activeFilters,
  onFilterToggle
}) => (
  <div className="p-6 md:p-10 space-y-8 animate-fade-in">
    <div className="flex items-end justify-between border-b border-white/10 pb-6">
      <div>
        <h1 className="text-4xl font-bold mb-2">资料库</h1>
        <p className="text-white/50">{songs.length} 首歌曲 · 本地曲库</p>
      </div>
      <div className="flex gap-2">
        <button className="px-4 py-2 rounded-lg bg-white/5 hover:bg-white/10 text-sm font-medium border border-white/5">按添加时间</button>
        <button
          className="px-4 py-2 rounded-lg bg-white text-black text-sm font-bold hover:bg-white/90"
          onClick={onPlayAll}
        >
          播放全部
        </button>
      </div>
    </div>

    {isLoading && <div className="text-white/50">正在加载歌曲列表...</div>}
    {loadError && <div className="text-rose-300">加载失败: {loadError}</div>}

    <div className="grid grid-cols-1 xl:grid-cols-[2fr_1fr] gap-8">
      <div className="space-y-6">
        <div className="flex gap-2 overflow-x-auto pb-2 no-scrollbar">
      {[
        { id: 'all', label: '全部' },
        { id: 'background', label: '和声' },
        { id: 'duet', label: '对唱' },
        { id: 'audio-only', label: '仅有音源' }
      ].map((filter) => (
        <button
              key={filter.id}
              className={`px-4 py-1.5 rounded-full text-xs font-medium whitespace-nowrap transition-colors border ${
                activeFilters.includes(filter.id)
                  ? 'bg-white text-black border-white'
                  : 'bg-transparent text-white/60 border-white/20 hover:border-white/50'
              }`}
              onClick={() => onFilterToggle(filter.id)}
            >
              {filter.label}
            </button>
          ))}
        </div>

        <div className="bg-white/5 rounded-2xl border border-white/5 overflow-hidden">
          <div className="grid grid-cols-[auto_1fr_auto] md:grid-cols-[auto_1fr_1fr_auto] gap-4 p-4 text-xs font-bold text-white/30 border-b border-white/5 uppercase tracking-wider">
            <div className="w-8 text-center">#</div>
            <div>Title</div>
            <div className="hidden md:block">Artist</div>
            <div className="text-right pr-4">Time</div>
          </div>
          {songs.map((song, i) => (
            <div
              key={song.filename}
              draggable
              onClick={(event) => onSongClick(song, i, event)}
              onDragStart={(event) => onSongDragStart(event, song, i)}
              className={`grid grid-cols-[auto_1fr_auto] md:grid-cols-[auto_1fr_1fr_auto] gap-4 p-3 items-center hover:bg-white/5 transition-colors group cursor-pointer border-b border-white/5 last:border-0 ${
                selectedTracks.has(song.filename) ? 'bg-white/10' : ''
              }`}
            >
              <div className="w-8 text-center text-white/30 font-mono text-xs group-hover:text-sky-300">
                <span className="group-hover:hidden">{i + 1}</span>
                <Play size={12} className="hidden group-hover:inline-block mx-auto" />
              </div>
              <div className="flex items-center gap-3 overflow-hidden">
                <CoverImage src={song.albumImgSrc} className="w-10 h-10 rounded object-cover" alt={song.title} />
                <div className="min-w-0">
                  <div
                    className="font-bold text-sm title-ellipsis-1 text-white/90 group-hover:text-white"
                    title={song.title || '未命名歌曲'}
                  >
                    {song.title || '未命名歌曲'}
                  </div>
                  <SongTags song={song} className="mt-1" />
                  <div
                    className="md:hidden text-xs text-white/40 title-ellipsis-1"
                    title={(song.artists || []).join(', ') || '未知艺人'}
                  >
                    {(song.artists || []).join(', ') || '未知艺人'}
                  </div>
                </div>
              </div>
              <div
                className="hidden md:block text-sm text-white/50 title-ellipsis-1 group-hover:text-white/80"
                title={(song.artists || []).join(', ') || '未知艺人'}
              >
                {(song.artists || []).join(', ') || '未知艺人'}
              </div>
              <div className="flex items-center justify-end gap-2 pr-2 text-xs text-white/30 font-mono group-hover:text-white/60">
                <button
                  className={`p-2 rounded-full border transition-colors ${
                    isSongLiked(song.filename)
                      ? 'border-emerald-400/60 text-emerald-300 bg-white/5'
                      : 'border-white/10 text-white/30 hover:text-emerald-200 hover:border-emerald-300/60'
                  }`}
                  onClick={(event) => {
                    event.stopPropagation()
                    toggleSongLike(song.filename)
                  }}
                >
                  <Heart size={14} fill={isSongLiked(song.filename) ? 'currentColor' : 'none'} />
                </button>
                <span className="min-w-[36px] text-right">3:45</span>
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="space-y-4 sticky top-6 self-start">
        <div className="bg-white/5 border border-white/10 rounded-2xl p-5 space-y-4">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-bold text-white/50 uppercase tracking-wider">歌曲详情</h3>
            <span className="text-[10px] px-2 py-1 rounded-full border border-white/10 text-white/50">只读</span>
          </div>
          {!selectedSong && <div className="text-white/40 text-sm">请选择一首歌曲查看详情。</div>}
          {selectedSong && (
            <div className="space-y-4 text-sm">
              <div>
                <div className="text-white/40 text-xs mb-1">标题</div>
                <div
                  className="text-white font-semibold title-ellipsis-2"
                  title={selectedSong.title || '未命名歌曲'}
                >
                  {selectedSong.title || '未命名歌曲'}
                </div>
              </div>
              <div>
                <div className="text-white/40 text-xs mb-1">艺人</div>
                <div
                  className="text-white/80 title-ellipsis-2"
                  title={(selectedSong.artists || []).join(', ') || '未知艺人'}
                >
                  {(selectedSong.artists || []).join(', ') || '未知艺人'}
                </div>
              </div>
              <SongTags song={selectedSong} />

              <div className="grid grid-cols-3 gap-3">
                <div className="rounded-xl bg-white/5 border border-white/10 p-3">
                  <div className="text-[10px] uppercase tracking-wider text-white/40">听的次数</div>
                  <div className="text-2xl font-bold text-white mt-1">{songStats?.listens || 0}</div>
                </div>
                <div className="rounded-xl bg-white/5 border border-white/10 p-3">
                  <div className="text-[10px] uppercase tracking-wider text-white/40">完听率</div>
                  <div className="text-2xl font-bold text-emerald-300 mt-1">{songStats?.completionRate || 0}%</div>
                </div>
                <div className="rounded-xl bg-white/5 border border-white/10 p-3">
                  <div className="text-[10px] uppercase tracking-wider text-white/40">最近一次</div>
                  <div className="text-xs text-white/70 mt-2">
                    {songStats?.lastListened
                      ? new Date(songStats.lastListened).toLocaleString('zh-CN', { hour12: false })
                      : '暂无记录'}
                  </div>
                </div>
              </div>

              <div className="rounded-xl bg-white/5 border border-white/10 p-3">
                <div className="text-[10px] uppercase tracking-wider text-white/40 mb-2">播放量趋势</div>
                {songStats?.trend?.length ? (
                  <Sparkline values={songStats.trend} />
                ) : (
                  <div className="text-xs text-white/40">暂无趋势数据</div>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  </div>
)

const SettingsView = ({
  uploadStatus,
  handleStaticImport,
  handleStaticDrop,
  isStaticDragActive,
  setIsStaticDragActive,
  autoBackupEnabled,
  setAutoBackupEnabled,
  backupStatus,
  lastBackupAt,
  onRunBackup,
  onDownloadBackup,
  anchorAccount,
  setAnchorAccount,
  anchorPassword,
  setAnchorPassword,
  anchorId,
  anchorStatus,
  onAnchorBackup,
  importStatus,
  onImportBackup,
  staticZipInputRef,
  onRefreshLibrary,
  disableCovers,
  setDisableCovers
}) => (
  <div className="p-6 md:p-10 max-w-3xl mx-auto space-y-8 animate-fade-in">
    <h1 className="text-3xl font-bold">设置</h1>

    <div className="space-y-6">
      <section className="space-y-4">
        <h3 className="text-sm font-bold text-white/40 uppercase tracking-widest">性能</h3>
        <div className="p-4 rounded-xl bg-white/5 border border-white/10 flex items-center justify-between">
          <div>
            <div className="text-sm font-semibold">禁用歌词封面/背景</div>
            <div className="text-xs text-white/50 mt-1">仅影响歌词页请求封面与背景，降低播放负载。</div>
          </div>
          <button
            className={`w-12 h-7 rounded-full border transition-colors ${
              disableCovers ? 'bg-emerald-400/70 border-emerald-300/70' : 'bg-white/10 border-white/20'
            }`}
            onClick={() => setDisableCovers((prev) => !prev)}
            aria-pressed={disableCovers}
          >
            <span
              className={`block w-5 h-5 rounded-full bg-white shadow transition-transform ${
                disableCovers ? 'translate-x-6' : 'translate-x-1'
              }`}
            />
          </button>
        </div>
      </section>
      <section className="space-y-4">
        <h3 className="text-sm font-bold text-white/40 uppercase tracking-widest">资料库 (LyricSphere API)</h3>
        <div className="p-4 rounded-xl bg-white/5 border border-white/5 space-y-2">
          <div className="flex justify-between text-sm">
            <span className="text-white/60">Endpoint</span>
            <span className="font-mono text-white/80">http://127.0.0.1:5000</span>
          </div>
          <div className="flex justify-between text-sm">
            <span className="text-white/60">Status</span>
            <span className="text-emerald-300 flex items-center gap-1">
              <div className="w-2 h-2 rounded-full bg-emerald-300 animate-pulse" /> Connected
            </span>
          </div>
          <button onClick={onRefreshLibrary} className="w-full mt-2 py-2 text-xs font-bold bg-white/5 hover:bg-white/10 rounded transition-colors flex items-center justify-center gap-2">
            <RefreshCw size={14} /> 重新扫描资料库
          </button>
        </div>
      </section>

      <section className="space-y-4">
        <h3 className="text-sm font-bold text-white/40 uppercase tracking-widest">文件上传</h3>
        <div className="p-4 rounded-xl bg-white/5 border border-white/5 space-y-3">
          <div className="text-xs text-white/50">
            仅支持 static.zip 快速导入，会覆盖静态资源。
          </div>
          <button
            type="button"
            onClick={() => {
              if (!staticZipInputRef?.current) return
              staticZipInputRef.current.value = ''
              staticZipInputRef.current.click()
            }}
            onDragOver={(event) => {
              event.preventDefault()
            }}
            onDragEnter={(event) => {
              event.preventDefault()
              setIsStaticDragActive(true)
            }}
            onDragLeave={(event) => {
              event.preventDefault()
              setIsStaticDragActive(false)
            }}
            onDrop={handleStaticDrop}
            className={`flex w-full items-center justify-center gap-3 border border-dashed rounded-xl px-4 py-4 text-sm transition-colors ${
              isStaticDragActive
                ? 'border-emerald-300/70 text-emerald-200 bg-emerald-400/10'
                : 'border-white/30 text-white/70 hover:border-white/60 hover:text-white'
            }`}
          >
            <UploadCloud size={18} /> 快速导入 static.zip
          </button>
          <input
            ref={staticZipInputRef}
            type="file"
            accept=".zip"
            className="hidden"
            onChange={handleStaticImport}
          />
          {uploadStatus && <div className="text-xs text-white/60">{uploadStatus}</div>}
        </div>
      </section>

      <section className="space-y-4">
        <h3 className="text-sm font-bold text-white/40 uppercase tracking-widest">备份</h3>
        <div className="p-4 rounded-xl bg-white/5 border border-white/5 space-y-4">
          <div className="space-y-3">
            <div className="text-sm font-semibold">账号锚定</div>
            <div className="text-xs text-white/50">
              账号 + 密码 会生成唯一锚点，用于清理缓存后找回备份。
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <input
                value={anchorAccount}
                onChange={(event) => setAnchorAccount(event.target.value)}
                placeholder="账号"
                className="w-full bg-white/5 border border-white/10 rounded-xl px-3 py-2 text-sm focus:outline-none focus:border-emerald-400/60 transition-colors"
              />
              <input
                type="password"
                value={anchorPassword}
                onChange={(event) => setAnchorPassword(event.target.value)}
                placeholder="密码"
                className="w-full bg-white/5 border border-white/10 rounded-xl px-3 py-2 text-sm focus:outline-none focus:border-emerald-400/60 transition-colors"
              />
            </div>
            <div className="flex flex-wrap items-center gap-3">
              <button
                type="button"
                onClick={onAnchorBackup}
                className="px-4 py-2 rounded-xl text-xs font-semibold bg-emerald-400/20 hover:bg-emerald-400/30 border border-emerald-400/30 text-emerald-200 transition-colors"
              >
                锚定备份
              </button>
              {anchorId && (
                <div className="text-xs text-emerald-200/80">
                  已锚定
                </div>
              )}
              {anchorStatus && <div className="text-xs text-white/60">{anchorStatus}</div>}
            </div>
          </div>
          <div className="flex items-center justify-between">
            <div>
              <div className="text-sm font-semibold">自动备份</div>
              <div className="text-xs text-white/50 mt-1">歌单与播放数据将定期保存到服务器。</div>
            </div>
            <button
              className={`w-12 h-7 rounded-full border transition-colors ${
                autoBackupEnabled ? 'bg-emerald-400/70 border-emerald-300/70' : 'bg-white/10 border-white/20'
              }`}
              onClick={() => setAutoBackupEnabled((prev) => !prev)}
              aria-pressed={autoBackupEnabled}
            >
              <span
                className={`block w-5 h-5 rounded-full bg-white shadow transition-transform ${
                  autoBackupEnabled ? 'translate-x-6' : 'translate-x-1'
                }`}
              />
            </button>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={onRunBackup}
              className="px-4 py-2 rounded-xl text-xs font-semibold bg-white/10 hover:bg-white/20 border border-white/10 transition-colors"
            >
              立即备份
            </button>
            <button
              type="button"
              onClick={onDownloadBackup}
              className="px-4 py-2 rounded-xl text-xs font-semibold bg-white/5 hover:bg-white/10 border border-white/10 transition-colors"
            >
              下载备份{anchorId ? '' : '（本机）'}
            </button>
            <label className="px-4 py-2 rounded-xl text-xs font-semibold bg-white/5 hover:bg-white/10 border border-white/10 transition-colors cursor-pointer">
              导入备份
              <input type="file" accept=".json" className="hidden" onChange={onImportBackup} />
            </label>
            <div className="text-xs text-white/50 md:ml-auto">
              最近备份：{lastBackupAt ? new Date(lastBackupAt).toLocaleString('zh-CN', { hour12: false }) : '暂无'}
            </div>
          </div>
          {importStatus && <div className="text-xs text-white/60">{importStatus}</div>}
          {backupStatus && <div className="text-xs text-white/60">{backupStatus}</div>}
        </div>
      </section>
    </div>
  </div>
)
