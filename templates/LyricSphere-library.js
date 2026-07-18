// 搜索功能
const searchBox = document.getElementById('songSearchInput')
if (searchBox) {
    const unlock = () => {
        searchBox.removeAttribute('readonly')
    }
    searchBox.addEventListener('focus', unlock, { once: true })
    searchBox.addEventListener('pointerdown', unlock, { once: true })
    setTimeout(unlock, 200)
}
const fuzzySearchToggle = document.getElementById('fuzzySearchToggle')
const jsonList = document.getElementById('jsonList')
const jsonListStatus = document.getElementById('jsonListStatus')
const batchTranslateStatus = document.getElementById('batchTranslateStatus')
const batchSelectionCount = document.getElementById('batchSelectionCount')
const songSummaryCache = new Map()
const songItemByFilename = new Map()
const songSearchPoolCache = new Map()
const songSortNameCache = new Map()
const selectedSongs = new Set()
let allSongFilenames = []
let mainListVisibleFilenames = []
let mainListRenderCursor = 0
let mainListRenderRaf = 0
let searchInputDebounceTimer = 0
const MAIN_LIST_BATCH_SIZE = 50
const SONG_SUMMARY_PAGE_SIZE = 50
let songSummaryPageSize = SONG_SUMMARY_PAGE_SIZE
let songSummaryCurrentPage = 1
let songSummaryTotal = 0
let songSummaryHasMore = false
let songSummaryIsLoadingMore = false
let libraryMode = 'browse'
let browseLibrarySnapshot = null
let searchPage = 1
let searchTotal = 0
let searchHasMore = false
let searchIsLoading = false
let searchResultGeneration = 0
const SEARCH_INPUT_DEBOUNCE_MS = 120
const RESIZE_SETTLE_MS = 180
const WRITE_LOCK_TEXT_PATTERN = /_WRITE_LOCK_|write-lock-action/; // i18n-safe: matched via data-write-lock attribute
let currentPortMode = 'fixed'
let currentWriteLockEnabled = false
let pendingFullStaticExportStart = false
let pendingFullStaticExportDownloadTaskId = ''
let activeFullStaticExportTaskId = ''
let fullStaticExportPollTimer = 0
let fullStaticExportRequestInFlight = false
let fullStaticExportLastState = null
let resizeSettleTimer = 0
let viewportResizing = false
let resizePausedCoverVideos = []

const BATCH_FILTERS = [
    { key: 'all', labelKey: 'filter.all' },
    { key: 'hasLyrics', labelKey: 'filter.hasLyrics' },
    { key: 'noTranslation', labelKey: 'filter.noTranslation' },
    { key: 'ttml', labelKey: 'filter.ttml' },
    { key: 'noAudio', labelKey: 'filter.noAudio' }
]
const BATCH_RESULT_STATUS_META = {
    queued: { textKey: 'resultStatus.queued', color: '#8f95a3' },
    reading: { textKey: 'resultStatus.reading', color: '#4f7cff' },
    converting: { textKey: 'resultStatus.converting', color: '#4f7cff' },
    sent: { textKey: 'resultStatus.sent', color: '#4f7cff' },
    received: { textKey: 'resultStatus.received', color: '#4f7cff' },
    saving: { textKey: 'resultStatus.saving', color: '#4f7cff' },
    success: { textKey: 'resultStatus.success', color: '#2ea043' },
    stopped: { textKey: 'resultStatus.stopped', color: '#f0ad4e' },
    skipped: { textKey: 'resultStatus.skipped', color: '#f0ad4e' },
    failed: { textKey: 'resultStatus.failed', color: '#d9534f' }
}
function getBatchResultStatusText(statusKey) {
    const meta = BATCH_RESULT_STATUS_META[statusKey];
    if (!meta) return statusKey;
    return meta.textKey ? t(meta.textKey) : (meta.text || statusKey);
}
const BATCH_TERMINAL_STATUS_KEYS = new Set(['success', 'failed', 'skipped', 'stopped'])
const BATCH_PROCESSING_STATUS_KEYS = new Set(['queued', 'reading', 'converting', 'sent', 'received', 'saving'])
const batchWorkbenchState = {
    visible: false,
    maximized: false,
    filter: 'all',
    results: new Map(),
    resultCardMap: new Map(),
    resultCardPlaceholder: null,
    dirtyResultIds: new Set(),
    resultStructureDirty: false,
    activeResultId: '',
    lastRenderedActiveResultId: '',
    detailDirty: false,
    running: false,
    stopRequested: false,
    lifecyclePhase: 'idle',
    finalResult: 'none',
    runState: 'idle',
    runPhase: '',
    totals: { total: 0, completed: 0 },
    latestStage: '',
    songListItems: [],
    songListCursor: 0,
    songListBatchSize: 72,
    songListScrollRaf: 0,
    resultRenderRaf: 0,
    detailRenderRaf: 0,
    statusRenderRaf: 0,
    statusRenderTimer: 0,
    statusLastFlushAt: 0,
    statusThrottleMs: 260,
    pendingStatusLines: [],
    pendingStatusIsError: false,
    statsRenderRaf: 0,
    runtimeStats: {
        success: 0,
        failed: 0,
        skipped: 0,
        processing: 0,
        completed: 0,
        total: 0
    },
    diagnostics: {
        streamChunkCount: 0,
        uiRefreshCount: 0,
        partialUpdateCount: 0,
        fullRefreshCount: 0
    },
    activePresetId: ''
}
let batchTranslateAbortController = null
let defaultBatchSystemPromptCache = ''
let defaultBatchFixedPromptCache = ''
const BATCH_WORKBENCH_ACTIVE_PRESET_KEY = 'batchWorkbenchActiveAiPresetId'

function getSummaryDisplayName(summary) {
    if (!summary) return ''
    const title = String(summary.title || '').trim()
    if (title) return title
    return String(summary.filename || '').replace(/\.json$/i, '')
}

function getSummaryArtist(summary) {
    if (!summary) return ''
    if (Array.isArray(summary.artists)) {
        return summary.artists.filter(Boolean).join(' / ')
    }
    return String(summary.artist || '').trim()
}

function isSummaryTTML(summary) {
    const p = String(summary && summary.lyricsPath ? summary.lyricsPath : '').toLowerCase()
    return p.endsWith('.ttml')
}

function hasSummaryTranslation(summary) {
    const path = String(summary && summary.translationPath ? summary.translationPath : '').trim()
    return Boolean(path && path !== '!')
}

function shouldSummaryMatchFilter(summary, filterKey) {
    if (filterKey === 'all') return true
    if (filterKey === 'hasLyrics') return hasLyricsSummary(summary)
    if (filterKey === 'noTranslation') return !hasSummaryTranslation(summary)
    if (filterKey === 'ttml') return isSummaryTTML(summary)
    if (filterKey === 'noAudio') return !deriveHasAudioFromSummary(summary)
    return true
}

    function getBatchWorkbenchControls() {
        return {
            modal: document.getElementById('batchWorkbenchModal'),
            dialog: document.getElementById('batchWorkbenchDialog'),
            search: document.getElementById('batchWorkbenchSearch'),
            fuzzy: document.getElementById('batchWorkbenchFuzzyToggle'),
            selectedCount: document.getElementById('batchWorkbenchSelectedCount'),
            filterRow: document.getElementById('batchWorkbenchFilterRow'),
            songList: document.getElementById('batchWorkbenchSongList'),
            summary: document.getElementById('batchWorkbenchSummary'),
            progressBar: document.getElementById('batchWorkbenchProgressBar'),
            runStatus: document.getElementById('batchWorkbenchRunStatus'),
            modelPreset: document.getElementById('batchWorkbenchModelPreset'),
            compatMode: document.getElementById('batchWorkbenchCompatMode'),
            stripBrackets: document.getElementById('batchWorkbenchStripBrackets'),
            thinkingEnabled: document.getElementById('batchWorkbenchThinkingEnabled'),
            expectReasoning: document.getElementById('batchWorkbenchExpectReasoning'),
            systemPrompt: document.getElementById('batchWorkbenchSystemPrompt'),
            extraPrompt: document.getElementById('batchWorkbenchExtraPrompt'),
            autoSave: document.getElementById('batchWbAutoSave'),
            onlyEmpty: document.getElementById('batchWbOnlyEmpty'),
            alwaysOverride: document.getElementById('batchWbAlwaysOverride'),
            resultList: document.getElementById('batchWorkbenchResultList'),
            detail: document.getElementById('batchWorkbenchDetail'),
            // 新增：工作台头部元素
            headerTitle: document.querySelector('.batch-wb-title'),
            headerSubtitle: document.querySelector('.batch-wb-subtitle'),
            summaryTotal: document.getElementById('batchWbSummaryTotal'),
            summaryProcessable: document.getElementById('batchWbSummaryProcessable'),
            summaryHasTranslation: document.getElementById('batchWbSummaryHasTranslation'),
            statusBadge: document.getElementById('batchWbStatusBadge'),
            headerProgressBar: document.getElementById('batchWbHeaderProgressBar'),
            // 新增：设置区元素
            aiProvider: document.getElementById('batchWbAiProvider'),
            aiBaseUrl: document.getElementById('batchWbAiBaseUrl'),
            aiModel: document.getElementById('batchWbAiModel'),
            presetSelect: document.getElementById('batchWbPresetSelect'),
            settingsSource: document.getElementById('batchWbSettingsSource'),
            settingsNote: document.getElementById('batchWbSettingsNote'),
            compatModeCheck: document.getElementById('batchWbCompatMode'),
            stripBracketsCheck: document.getElementById('batchWbStripBrackets'),
            experimentalFullLineBracketStripCheck: document.getElementById('batchWbExperimentalFullLineBracketStrip'),
            experimentalBracketLineAsSublineCheck: document.getElementById('batchWbExperimentalBracketLineAsSubline'),
            thinkingEnabledCheck: document.getElementById('batchWbThinkingEnabled'),
            expectReasoningCheck: document.getElementById('batchWbExpectReasoning'),
            onlyEmptyCheck: document.getElementById('batchWbOnlyEmpty'),
            alwaysOverrideCheck: document.getElementById('batchWbAlwaysOverride'),
            overrideWarning: document.getElementById('batchWbOverrideWarning'),
            summaryModel: document.getElementById('batchWbSummaryModel'),
            summaryCoverageMode: document.getElementById('batchWbSummaryCoverageMode'),
            summarySelectedCount: document.getElementById('batchWbSummarySelectedCount'),
            summaryProcessableCount: document.getElementById('batchWbSummaryProcessableCount'),
            summarySkippedCount: document.getElementById('batchWbSummarySkippedCount'),
            // 新增：运行态区元素
            currentStage: document.getElementById('batchWbCurrentStage'),
            progressPercent: document.getElementById('batchWbProgressPercent'),
            countSuccess: document.getElementById('batchWbCountSuccess'),
            countProcessing: document.getElementById('batchWbCountProcessing'),
            countError: document.getElementById('batchWbCountError'),
            countSkipped: document.getElementById('batchWbCountSkipped'),
            totalCount: document.getElementById('batchWbTotalCount'),
            filteredCount: document.getElementById('batchWbFilteredCount')
        }
    }

function getBatchWorkbenchSummaryList() {
    return Array.from(songSummaryCache.values())
}

function getBatchWorkbenchFilteredSummaries() {
    const controls = getBatchWorkbenchControls()
    const raw = String(controls.search && controls.search.value ? controls.search.value : '').toLowerCase()
    const isFuzzy = Boolean(controls.fuzzy && controls.fuzzy.checked)
    const keywords = isFuzzy ? parseFuzzyKeywords(raw) : parseKeywords(raw)
    const all = getBatchWorkbenchSummaryList().filter(summary => shouldSummaryMatchFilter(summary, batchWorkbenchState.filter))
    if (!keywords.length) {
        return all
    }
    return all.filter(summary => {
        const source = [
            getSummaryDisplayName(summary),
            getSummaryArtist(summary),
            summary.filename || ''
        ].join(' ').toLowerCase()
        return keywords.every(keyword => source.includes(keyword))
    })
}

    function renderBatchSelectionCount() {
        if (batchSelectionCount) {
            batchSelectionCount.textContent = `${t('batch.selectSongCount', {count: selectedSongs.size})}`
        }
        const controls = getBatchWorkbenchControls()
        if (controls.selectedCount) {
            controls.selectedCount.textContent = String(selectedSongs.size)
        }
        syncBatchWbHeaderSummary() // 新增：更新头部概览
        renderBatchWorkbenchSummary()
    }

function toggleBatchSongSelection(filename, checked) {
    if (!filename) return
    if (checked) {
        selectedSongs.add(filename)
    } else {
        selectedSongs.delete(filename)
    }
    renderBatchSelectionCount()
    refreshBatchWorkbenchSongCard(filename)
}



function updateBatchTranslateStatus(lines, isError = false) {
    if (!batchTranslateStatus) return
    if (!lines || lines.length === 0) {
        batchTranslateStatus.style.display = 'none'
        batchTranslateStatus.textContent = ''
        return
    }
    batchTranslateStatus.style.display = 'block'
    batchTranslateStatus.style.color = isError ? '#d9534f' : '#666'
    batchTranslateStatus.textContent = lines.join('\n')
}

function renderBatchWorkbenchFilterRow() {
    const controls = getBatchWorkbenchControls()
    if (!controls.filterRow) return
    controls.filterRow.innerHTML = ''
    BATCH_FILTERS.forEach(item => {
        const btn = document.createElement('button')
        btn.className = `batch-wb-chip ${batchWorkbenchState.filter === item.key ? 'active' : ''}`
        btn.textContent = t(item.labelKey || item.label)
        btn.addEventListener('click', () => {
            batchWorkbenchState.filter = item.key
            renderBatchWorkbenchFilterRow()
            renderBatchWorkbenchSongList()
        })
        controls.filterRow.appendChild(btn)
    })
}

function collectSummaryTags(summary) {
    const tags = []
    tags.push(hasSummaryTranslation(summary) ? t('song.tag.hasTranslation') : t('song.tag.noTranslation'))
    if (isSummaryTTML(summary)) tags.push('TTML')
    if (!hasLyricsSummary(summary)) tags.push(t('song.tag.instrumental'))
    if (summary.hasDuet) tags.push(t('song.tag.duet'))
    if (summary.hasBackgroundVocals) tags.push(t('song.tag.backgroundVocals'))
    return tags
}

function canSaveBatchResult(target) {
    const translations = Array.isArray(target?.payload?.translations) ? target.payload.translations : []
    return Boolean(target?.summary && translations.length > 0)
}

function escapeSelectorValue(value) {
    const text = String(value || '')
    if (window.CSS && typeof window.CSS.escape === 'function') {
        return window.CSS.escape(text)
    }
    return text.replace(/[^a-zA-Z0-9_-]/g, '\\$&')
}

function getBatchWorkbenchPresetStateFromUI() {
    const settings = getBatchWorkbenchSettingsFromUI()
    return {
        batch: {
            auto_save: settings.autoSave,
            only_empty: settings.onlyEmpty,
            always_override: settings.alwaysOverride,
            extra_prompt: settings.extraPrompt
        }
    }
}

function getBatchWorkbenchFullPresetStateFromUI() {
    const settings = getBatchWorkbenchSettingsFromUI()
    return {
        translation: {
            provider: settings.provider,
            base_url: settings.baseUrl,
            model: settings.model,
            system_prompt: settings.systemPrompt,
            expect_reasoning: settings.expectReasoning,
            compat_mode: settings.compatMode,
            strip_brackets: settings.stripBrackets,
            experimental_full_line_bracket_strip: settings.experimentalFullLineBracketStrip,
            experimental_bracket_line_as_subline: settings.experimentalBracketLineAsSubline
        },
        thinking: {
            enabled: settings.thinkingEnabled,
            provider: localStorage.getItem('aiThinkingProvider') || '',
            base_url: localStorage.getItem('aiThinkingBaseUrl') || '',
            model: localStorage.getItem('aiThinkingModel') || '',
            system_prompt: localStorage.getItem('aiThinkingPrompt') || ''
        },
        batch: {
            auto_save: settings.autoSave,
            only_empty: settings.onlyEmpty,
            always_override: settings.alwaysOverride,
            extra_prompt: settings.extraPrompt
        },
        romanization: {
            system_prompt: localStorage.getItem('aiRomanizationPrompt') || '',
            alignment_mode: (() => {
                const v = String(localStorage.getItem('aiRomanizationAlignmentMode') || '').trim().toLowerCase();
                return v === 'separator_tokens' ? 'separator_tokens' : 'indexed_tokens';
            })(),
            separator: (localStorage.getItem('aiRomanizationSeparator') || ';').slice(0, 8),
            strict_token_count: localStorage.getItem('aiRomanizationStrict') !== 'false',
            require_trailing_separator: localStorage.getItem('aiRomanizationTrailing') !== 'false'
        }
    }
}

function getBatchWorkbenchPresetStateFromPreset(preset) {
    return {
        translation: {
            provider: preset?.translation?.provider || 'deepseek',
            base_url: preset?.translation?.base_url || '',
            model: preset?.translation?.model || '',
            system_prompt: preset?.translation?.system_prompt || '',
            expect_reasoning: Boolean(preset?.translation?.expect_reasoning),
            compat_mode: Boolean(preset?.translation?.compat_mode),
            strip_brackets: Boolean(preset?.translation?.strip_brackets),
            experimental_full_line_bracket_strip: Boolean(preset?.translation?.experimental_full_line_bracket_strip),
            experimental_bracket_line_as_subline: Boolean(preset?.translation?.experimental_bracket_line_as_subline)
        },
        thinking: {
            enabled: preset?.thinking?.enabled !== undefined ? Boolean(preset.thinking.enabled) : true,
            provider: preset?.thinking?.provider || '',
            base_url: preset?.thinking?.base_url || '',
            model: preset?.thinking?.model || '',
            system_prompt: preset?.thinking?.system_prompt || ''
        },
        batch: {
            auto_save: preset?.batch?.auto_save !== undefined ? Boolean(preset.batch.auto_save) : true,
            only_empty: preset?.batch?.only_empty !== undefined ? Boolean(preset.batch.only_empty) : true,
            always_override: Boolean(preset?.batch?.always_override),
            extra_prompt: preset?.batch?.extra_prompt || ''
        },
        romanization: {
            system_prompt: preset?.romanization?.system_prompt || '',
            alignment_mode: (() => {
                const v = String(preset?.romanization?.alignment_mode || '').trim().toLowerCase();
                return v === 'separator_tokens' ? 'separator_tokens' : 'indexed_tokens';
            })(),
            separator: (preset?.romanization?.separator !== undefined && String(preset.romanization.separator).trim())
                ? String(preset.romanization.separator).slice(0, 8)
                : ';',
            strict_token_count: preset?.romanization?.strict_token_count !== undefined ? Boolean(preset.romanization.strict_token_count) : true,
            require_trailing_separator: preset?.romanization?.require_trailing_separator !== undefined
                ? Boolean(preset.romanization.require_trailing_separator)
                : true
        }
    }
}

function getBatchWorkbenchPresetVisibleState(state) {
    return {
        translation: {
            provider: state?.translation?.provider || 'deepseek',
            base_url: state?.translation?.base_url || '',
            model: state?.translation?.model || '',
            system_prompt: state?.translation?.system_prompt || '',
            expect_reasoning: Boolean(state?.translation?.expect_reasoning),
            compat_mode: Boolean(state?.translation?.compat_mode),
            strip_brackets: Boolean(state?.translation?.strip_brackets),
            experimental_full_line_bracket_strip: Boolean(state?.translation?.experimental_full_line_bracket_strip),
            experimental_bracket_line_as_subline: Boolean(state?.translation?.experimental_bracket_line_as_subline)
        },
        thinking: {
            enabled: state?.thinking?.enabled !== undefined ? Boolean(state.thinking.enabled) : true
        },
        batch: {
            auto_save: state?.batch?.auto_save !== undefined ? Boolean(state.batch.auto_save) : true,
            only_empty: state?.batch?.only_empty !== undefined ? Boolean(state.batch.only_empty) : true,
            always_override: Boolean(state?.batch?.always_override),
            extra_prompt: state?.batch?.extra_prompt || ''
        },
        romanization: {
            system_prompt: String(state?.romanization?.system_prompt || ''),
            alignment_mode: (() => {
                const v = String(state?.romanization?.alignment_mode || '').trim().toLowerCase();
                return v === 'separator_tokens' ? 'separator_tokens' : 'indexed_tokens';
            })(),
            separator: String(state?.romanization?.separator || ';').slice(0, 8),
            strict_token_count: state?.romanization?.strict_token_count !== undefined ? Boolean(state.romanization.strict_token_count) : true,
            require_trailing_separator: state?.romanization?.require_trailing_separator !== undefined
                ? Boolean(state.romanization.require_trailing_separator)
                : true
        }
    }
}

function batchWorkbenchPresetMatchesCurrent(preset) {
    return JSON.stringify(getBatchWorkbenchPresetVisibleState(getBatchWorkbenchFullPresetStateFromUI())) === JSON.stringify(getBatchWorkbenchPresetVisibleState(getBatchWorkbenchPresetStateFromPreset(preset)))
}

function getBatchWorkbenchPresetList() {
    return loadAiPresets()
}

function getBatchWorkbenchActivePreset() {
    const presetId = localStorage.getItem(BATCH_WORKBENCH_ACTIVE_PRESET_KEY) || batchWorkbenchState.activePresetId || ''
    const presets = getBatchWorkbenchPresetList()
    return presets.find(preset => preset.id === presetId) || null
}

function updateBatchWorkbenchPresetSelect() {
    const controls = getBatchWorkbenchControls()
    const select = controls.presetSelect
    if (!select) return
    const presets = getBatchWorkbenchPresetList()
    const activeId = localStorage.getItem(BATCH_WORKBENCH_ACTIVE_PRESET_KEY) || ''
    select.innerHTML = '<option value="">' + t('aiSettings.selectPresetOption') + '</option>'
    presets.forEach(preset => {
        const option = document.createElement('option')
        option.value = preset.id
        option.textContent = preset.name
        if (preset.id === activeId) option.selected = true
        select.appendChild(option)
    })
}

function applyBatchWorkbenchPreset(preset) {
    const controls = getBatchWorkbenchControls()
    if (!preset) {
        const presetId = controls.presetSelect ? controls.presetSelect.value : ''
        if (!presetId) {
            alert(t('alert.selectPreset'))
            return
        }
        preset = getBatchWorkbenchPresetList().find(item => item.id === presetId)
        if (!preset) {
            alert(t('alert.presetNotExist'))
            return
        }
    }
    const state = getBatchWorkbenchPresetStateFromPreset(preset)
    if (controls.aiProvider) controls.aiProvider.value = state.translation.provider
    if (controls.aiBaseUrl) controls.aiBaseUrl.value = state.translation.base_url
    if (controls.aiModel) controls.aiModel.value = state.translation.model
    if (controls.compatModeCheck) controls.compatModeCheck.checked = state.translation.compat_mode
    if (controls.stripBracketsCheck) controls.stripBracketsCheck.checked = state.translation.strip_brackets
    if (controls.experimentalFullLineBracketStripCheck) controls.experimentalFullLineBracketStripCheck.checked = state.translation.experimental_full_line_bracket_strip
    if (controls.experimentalBracketLineAsSublineCheck) controls.experimentalBracketLineAsSublineCheck.checked = state.translation.experimental_bracket_line_as_subline
    if (controls.thinkingEnabledCheck) controls.thinkingEnabledCheck.checked = state.thinking.enabled
    if (controls.expectReasoningCheck) controls.expectReasoningCheck.checked = state.translation.expect_reasoning
    if (controls.systemPrompt) controls.systemPrompt.value = state.translation.system_prompt
    if (controls.extraPrompt) controls.extraPrompt.value = state.batch.extra_prompt
    if (controls.autoSave) controls.autoSave.checked = state.batch.auto_save
    if (controls.onlyEmptyCheck) controls.onlyEmptyCheck.checked = state.batch.only_empty
    if (controls.alwaysOverrideCheck) controls.alwaysOverrideCheck.checked = state.batch.always_override
    localStorage.setItem(BATCH_WORKBENCH_ACTIVE_PRESET_KEY, preset.id)
    batchWorkbenchState.activePresetId = preset.id
    syncBatchWorkbenchPromptFields()
    applyBatchWorkbenchSettingsToUI()
    syncBatchWbSettingsSummary()
    updateBatchWorkbenchPresetSelect()
}

function onBatchWorkbenchPresetSelectChange() {
    const controls = getBatchWorkbenchControls()
    const presetId = controls.presetSelect ? controls.presetSelect.value : ''
    if (!presetId) {
        batchWorkbenchState.activePresetId = ''
        localStorage.removeItem(BATCH_WORKBENCH_ACTIVE_PRESET_KEY)
        applyBatchWorkbenchSettingsToUI()
        syncBatchWbSettingsSummary()
        return
    }
    applyBatchWorkbenchPreset()
}

async function saveBatchWorkbenchAsPreset() {
    const name = prompt(t('preset.inputName'))
    if (!name || !name.trim()) return
    const isLocalHost = ['localhost', '127.0.0.1', '::1'].includes(location.hostname)
    if (!isLocalHost && !hasFullAiPresetVisibility()) {
        alert('当前设备没有完整预设可见权限，不能另存为新预设')
        return
    }
    const preset = {
        id: 'preset_' + Date.now(),
        name: name.trim(),
        ...getBatchWorkbenchFullPresetStateFromUI(),
        updated_at: Date.now()
    }
    await createAiPresetOnBackend(preset)
    localStorage.setItem(BATCH_WORKBENCH_ACTIVE_PRESET_KEY, preset.id)
    batchWorkbenchState.activePresetId = preset.id
    updateBatchWorkbenchPresetSelect()
    updateAiPresetSelect()
    updateQuickAiPresetSelect()
    syncBatchWbSettingsSummary()
}

async function updateCurrentBatchWorkbenchPreset() {
    const controls = getBatchWorkbenchControls()
    const presetId = controls.presetSelect ? controls.presetSelect.value : ''
    if (!presetId) {
        alert(t('alert.selectPresetToUpdate'))
        return
    }
    await upsertAiPresetOnBackend(presetId, {
        id: presetId,
        ...getBatchWorkbenchFullPresetStateFromUI(),
        updated_at: Date.now()
    })
    localStorage.setItem(BATCH_WORKBENCH_ACTIVE_PRESET_KEY, presetId)
    batchWorkbenchState.activePresetId = presetId
    updateBatchWorkbenchPresetSelect()
    updateAiPresetSelect()
    updateQuickAiPresetSelect()
    syncBatchWbSettingsSummary()
}

async function deleteBatchWorkbenchPreset() {
    const controls = getBatchWorkbenchControls()
    const presetId = controls.presetSelect ? controls.presetSelect.value : ''
    if (!presetId) {
        alert(t('alert.selectPresetToDelete'))
        return
    }
    if (!confirm(t('confirm.deletePreset'))) return
    await deleteAiPresetOnBackend(presetId)
    if (localStorage.getItem(BATCH_WORKBENCH_ACTIVE_PRESET_KEY) === presetId) {
        localStorage.removeItem(BATCH_WORKBENCH_ACTIVE_PRESET_KEY)
        batchWorkbenchState.activePresetId = ''
    }
    syncActiveAiPresetKeyWithSavedSource()
    updateBatchWorkbenchPresetSelect()
    updateAiPresetSelect()
    updateQuickAiPresetSelect()
    syncBatchWbSettingsSummary()
}

function buildBatchWorkbenchSongCard(summary) {
    const card = document.createElement('div')
    const isSelected = selectedSongs.has(summary.filename)
    card.className = `batch-wb-song-card ${isSelected ? 'selected' : ''}`
    card.dataset.filename = summary.filename || ''
    const tags = collectSummaryTags(summary)
    const artist = getSummaryArtist(summary) || t('runtime.unknownArtist')
    const displayName = escapeHtml(getSummaryDisplayName(summary))
    card.innerHTML = `
        <div class="batch-wb-song-top">
            <input type="checkbox" ${isSelected ? 'checked' : ''}>
            <strong class="batch-wb-song-title">${displayName}</strong>
        </div>
        <div class="batch-wb-song-meta">
            <span class="batch-wb-song-meta-item batch-wb-song-meta-line artist">${t('create.artistDisplay')}${escapeHtml(artist)}</span>
            <span class="batch-wb-song-meta-item batch-wb-song-meta-line filename">${t('batch.filename')}${escapeHtml(summary.filename || '')}</span>
        </div>
        <div class="batch-wb-mini-tags">${tags.map(tag => `<span class="batch-wb-mini-tag">${escapeHtml(tag)}</span>`).join('')}</div>
    `
    const checkbox = card.querySelector('input[type="checkbox"]')
    checkbox.addEventListener('click', (event) => event.stopPropagation())
    checkbox.addEventListener('change', (event) => {
        toggleBatchSongSelection(summary.filename, Boolean(event.target.checked))
    })
    card.addEventListener('click', () => {
        const next = !selectedSongs.has(summary.filename)
        toggleBatchSongSelection(summary.filename, next)
    })
    return card
}

function appendBatchWorkbenchSongListBatch() {
    const controls = getBatchWorkbenchControls()
    if (!controls.songList) return
    const list = batchWorkbenchState.songListItems || []
    if (batchWorkbenchState.songListCursor >= list.length) return
    const fragment = document.createDocumentFragment()
    const nextCursor = Math.min(list.length, batchWorkbenchState.songListCursor + batchWorkbenchState.songListBatchSize)
    for (let i = batchWorkbenchState.songListCursor; i < nextCursor; i++) {
        fragment.appendChild(buildBatchWorkbenchSongCard(list[i]))
    }
    batchWorkbenchState.songListCursor = nextCursor
    controls.songList.appendChild(fragment)
}

function handleBatchWorkbenchSongListScroll() {
    const controls = getBatchWorkbenchControls()
    const list = controls.songList
    if (!list || batchWorkbenchState.songListScrollRaf) return
    batchWorkbenchState.songListScrollRaf = window.requestAnimationFrame(() => {
        batchWorkbenchState.songListScrollRaf = 0
        const distanceToBottom = list.scrollHeight - list.scrollTop - list.clientHeight
        if (distanceToBottom < 140) {
            appendBatchWorkbenchSongListBatch()
        }
    })
}

function refreshBatchWorkbenchSongCard(filename) {
    const controls = getBatchWorkbenchControls()
    if (!controls.songList || !filename) return
    const card = controls.songList.querySelector(`[data-filename="${escapeSelectorValue(filename)}"]`)
    if (!card) return
    const isSelected = selectedSongs.has(filename)
    card.classList.toggle('selected', isSelected)
    const checkbox = card.querySelector('input[type="checkbox"]')
    if (checkbox) checkbox.checked = isSelected
}

function renderBatchWorkbenchSongList() {
    if (!batchWorkbenchState.visible) return
    const controls = getBatchWorkbenchControls()
    if (!controls.songList) return
    const list = getBatchWorkbenchFilteredSummaries()
    batchWorkbenchState.songListItems = list
    batchWorkbenchState.songListCursor = 0
    if (batchWorkbenchState.songListScrollRaf) {
        window.cancelAnimationFrame(batchWorkbenchState.songListScrollRaf)
        batchWorkbenchState.songListScrollRaf = 0
    }
    controls.songList.innerHTML = ''
    controls.songList.scrollTop = 0
    appendBatchWorkbenchSongListBatch()
    if (list.length === 0) {
        controls.songList.innerHTML = '<div class="batch-wb-song-meta">' + t('batch.noMatchSongs') + '</div>'
    }
    if (controls.totalCount) {
        controls.totalCount.textContent = String(getBatchWorkbenchSummaryList().length)
    }
    if (controls.filteredCount) {
        controls.filteredCount.textContent = String(list.length)
    }
    syncBatchWbHeaderSummary()
}

function renderBatchWorkbenchSummary() {
    const controls = getBatchWorkbenchControls()
    if (!controls.summary) return
    const selectedSummaries = Array.from(selectedSongs).map(name => songSummaryCache.get(name)).filter(Boolean)
    const total = selectedSummaries.length
    const settings = getBatchWorkbenchSettingsFromUI()
    const translatable = selectedSummaries.filter(item => {
        if (!hasLyricsSummary(item)) return false
        if (settings.alwaysOverride) return true
        return settings.onlyEmpty ? !hasSummaryTranslation(item) : true
    }).length
    const skipped = Math.max(0, total - translatable)
    const ttml = selectedSummaries.filter(item => isSummaryTTML(item)).length
    if (controls.summarySelectedCount) controls.summarySelectedCount.textContent = String(total)
    if (controls.summaryProcessableCount) controls.summaryProcessableCount.textContent = String(translatable)
    if (controls.summarySkippedCount) controls.summarySkippedCount.textContent = String(skipped)
    if (controls.summaryModel) controls.summaryModel.textContent = settings.model || t('batch.notSet')
    if (controls.summaryCoverageMode) {
        controls.summaryCoverageMode.textContent = settings.alwaysOverride ? t('batch.alwaysOverrideShort') : (settings.onlyEmpty ? t('batch.onlyEmptyShort') : t('batch.byCurrentRules'))
    }
    if (controls.summary && controls.summary.querySelector) {
        const extra = controls.summary.querySelector('[data-batch-summary-extra]')
        if (extra) extra.textContent = `${t('batch.amongTtml')}${ttml}`
    }
}

function setBatchWorkbenchProgress(completed, total) {
    batchWorkbenchState.totals = { completed, total }
    const controls = getBatchWorkbenchControls()
    const ratio = total > 0 ? Math.max(0, Math.min(100, Math.round((completed / total) * 100))) : 0
    if (controls.progressBar) controls.progressBar.style.width = `${ratio}%`
    if (controls.headerProgressBar) controls.headerProgressBar.style.width = `${ratio}%`
    if (controls.progressPercent) controls.progressPercent.textContent = String(ratio)
}

function computeBatchRuntimeStats() {
    const entries = Array.from(batchWorkbenchState.results.values())
    let success = 0
    let failed = 0
    let skipped = 0
    let processing = 0
    entries.forEach(item => {
        if (!item) return
        if (item.statusKey === 'success') {
            success += 1
        } else if (item.statusKey === 'failed') {
            failed += 1
        } else if (item.statusKey === 'skipped' || item.statusKey === 'stopped') {
            skipped += 1
        } else if (BATCH_PROCESSING_STATUS_KEYS.has(item.statusKey)) {
            processing += 1
        }
    })
    return {
        success,
        failed,
        skipped,
        processing,
        completed: success + failed + skipped,
        total: entries.length
    }
}

function applyBatchRuntimeStatsToUi(stats = null) {
    const controls = getBatchWorkbenchControls()
    const data = stats || computeBatchRuntimeStats()
    batchWorkbenchState.runtimeStats = data
    setBatchWorkbenchProgress(data.completed, data.total)
    if (controls.countSuccess) controls.countSuccess.textContent = String(data.success)
    if (controls.countError) controls.countError.textContent = String(data.failed)
    if (controls.countSkipped) controls.countSkipped.textContent = String(data.skipped)
    if (controls.countProcessing) controls.countProcessing.textContent = String(data.processing)
}

function scheduleBatchRuntimeStatsRender() {
    if (batchWorkbenchState.statsRenderRaf) return
    batchWorkbenchState.statsRenderRaf = window.requestAnimationFrame(() => {
        batchWorkbenchState.statsRenderRaf = 0
        applyBatchRuntimeStatsToUi()
    })
}

function markBatchResultDirty(resultId, options = {}) {
    const { detail = false, structure = false } = options
    if (resultId) {
        batchWorkbenchState.dirtyResultIds.add(String(resultId))
    }
    if (detail) {
        batchWorkbenchState.detailDirty = true
    }
    if (structure) {
        batchWorkbenchState.resultStructureDirty = true
    }
    scheduleBatchRuntimeStatsRender()
    scheduleBatchWorkbenchResultRender({ detail, full: structure, resultId })
}

function updateBatchWorkbenchLifecycle(phase, finalResult = null) {
    if (phase) {
        batchWorkbenchState.lifecyclePhase = phase
    }
    if (finalResult !== null) {
        batchWorkbenchState.finalResult = finalResult
    }
    if (batchWorkbenchState.running) {
        batchWorkbenchState.runState = 'running'
        return
    }
    if (batchWorkbenchState.finalResult === 'success') {
        batchWorkbenchState.runState = 'completed'
    } else if (batchWorkbenchState.finalResult === 'partial') {
        batchWorkbenchState.runState = 'partial'
    } else if (batchWorkbenchState.finalResult === 'failed') {
        batchWorkbenchState.runState = 'failed'
    } else if (batchWorkbenchState.finalResult === 'stopped') {
        batchWorkbenchState.runState = 'stopped'
    } else {
        batchWorkbenchState.runState = 'idle'
    }
}

function setBatchWorkbenchRunStatus(text, runState = null) {
    batchWorkbenchState.latestStage = text || ''
    batchWorkbenchState.runPhase = text || ''
    if (runState) {
        batchWorkbenchState.runState = runState
    }
    updateBatchWorkbenchLifecycle(batchWorkbenchState.lifecyclePhase || 'idle')
    const controls = getBatchWorkbenchControls()
    if (controls.currentStage) {
        controls.currentStage.textContent = text || t('batch.status.waiting')
    }
    if (controls.statusBadge) {
        controls.statusBadge.classList.remove('idle', 'running', 'completed', 'stopped', 'failed')
        let badgeClass = 'idle'
        let badgeText = t('batch.status.idle')
        if (batchWorkbenchState.runState === 'running') {
            badgeClass = 'running'
            badgeText = t('batch.status.running')
        } else if (batchWorkbenchState.runState === 'completed') {
            badgeClass = 'completed'
            badgeText = t('batch.status.completed')
        } else if (batchWorkbenchState.runState === 'stopped') {
            badgeClass = 'stopped'
            badgeText = t('batch.status.stopped')
        } else if (batchWorkbenchState.runState === 'partial') {
            badgeClass = 'completed'
            badgeText = t('batch.status.partial')
        } else if (batchWorkbenchState.runState === 'failed') {
            badgeClass = 'failed'
            badgeText = t('batch.status.failed')
        }
        controls.statusBadge.classList.add(badgeClass)
        controls.statusBadge.textContent = badgeText
    }
}

function scheduleBatchWorkbenchResultRender(options = {}) {
    const { detail = false, full = false, resultId = '' } = options
    if (detail) {
        batchWorkbenchState.detailDirty = true
    }
    if (full) {
        batchWorkbenchState.resultStructureDirty = true
    }
    if (resultId) {
        batchWorkbenchState.dirtyResultIds.add(String(resultId))
    }
    if (batchWorkbenchState.resultRenderRaf) return
    batchWorkbenchState.resultRenderRaf = window.requestAnimationFrame(() => {
        batchWorkbenchState.resultRenderRaf = 0
        renderBatchWorkbenchResultList()
        batchWorkbenchState.diagnostics.uiRefreshCount += 1
    })
}

function flushBatchTranslateStatusRender() {
    batchWorkbenchState.statusRenderRaf = 0
    batchWorkbenchState.statusRenderTimer = 0
    batchWorkbenchState.statusLastFlushAt = Date.now()
    updateBatchTranslateStatus(
        batchWorkbenchState.pendingStatusLines || [],
        Boolean(batchWorkbenchState.pendingStatusIsError)
    )
}

function scheduleBatchTranslateStatusRender(lines, isError = false, options = {}) {
    const { force = false } = options
    batchWorkbenchState.pendingStatusLines = Array.isArray(lines) ? lines.slice() : []
    batchWorkbenchState.pendingStatusIsError = Boolean(isError)
    const now = Date.now()
    if (force || now - batchWorkbenchState.statusLastFlushAt >= batchWorkbenchState.statusThrottleMs) {
        if (batchWorkbenchState.statusRenderTimer) {
            window.clearTimeout(batchWorkbenchState.statusRenderTimer)
            batchWorkbenchState.statusRenderTimer = 0
        }
        if (!batchWorkbenchState.statusRenderRaf) {
            batchWorkbenchState.statusRenderRaf = window.requestAnimationFrame(flushBatchTranslateStatusRender)
        }
        return
    }
    if (batchWorkbenchState.statusRenderRaf || batchWorkbenchState.statusRenderTimer) return
    const waitMs = Math.max(16, batchWorkbenchState.statusThrottleMs - (now - batchWorkbenchState.statusLastFlushAt))
    batchWorkbenchState.statusRenderTimer = window.setTimeout(() => {
        if (!batchWorkbenchState.statusRenderRaf) {
            batchWorkbenchState.statusRenderRaf = window.requestAnimationFrame(flushBatchTranslateStatusRender)
        }
    }, waitMs)
}

function createBatchResultCard(resultId) {
    const card = document.createElement('div')
    card.className = 'batch-wb-result-card'
    card.dataset.resultId = resultId
    card.innerHTML = `
        <div><span class="batch-wb-status-dot"></span><strong class="batch-wb-result-title"></strong></div>
        <div class="batch-wb-song-meta batch-wb-result-filename"></div>
        <div class="batch-wb-song-meta batch-wb-result-status"></div>
        <div class="batch-wb-song-meta batch-wb-result-meta"></div>
        <div class="batch-wb-btn-row" style="margin-top:8px; gap: 6px;">
            <button class="batch-wb-btn secondary batch-wb-result-save-btn" style="flex: 1; font-size: 11px;"></button>
            <button class="batch-wb-btn ghost batch-wb-result-detail-btn" style="flex: 1; font-size: 11px;" data-i18n="batch.detailTitle">查看详情</button>
        </div>
    `
    const saveBtn = card.querySelector('.batch-wb-result-save-btn')
    const detailBtn = card.querySelector('.batch-wb-result-detail-btn')

    card.addEventListener('click', () => {
        const previousId = batchWorkbenchState.activeResultId
        batchWorkbenchState.activeResultId = resultId
        if (previousId && previousId !== resultId) {
            batchWorkbenchState.dirtyResultIds.add(previousId)
        }
        markBatchResultDirty(resultId, { detail: true })
    })

    if (saveBtn) {
        saveBtn.addEventListener('click', async (event) => {
            event.stopPropagation()
            await saveBatchResultById(resultId)
        })
    }
    if (detailBtn) {
        detailBtn.addEventListener('click', (event) => {
            event.stopPropagation()
            const previousId = batchWorkbenchState.activeResultId
            batchWorkbenchState.activeResultId = resultId
            if (previousId && previousId !== resultId) {
                batchWorkbenchState.dirtyResultIds.add(previousId)
            }
            markBatchResultDirty(resultId, { detail: true })
        })
    }

    card.__refs = {
        statusDot: card.querySelector('.batch-wb-status-dot'),
        title: card.querySelector('.batch-wb-result-title'),
        filename: card.querySelector('.batch-wb-result-filename'),
        status: card.querySelector('.batch-wb-result-status'),
        meta: card.querySelector('.batch-wb-result-meta'),
        saveBtn
    }
    return card
}

function updateBatchResultCard(card, result) {
    if (!card || !result) return
    const refs = card.__refs || {}
    const meta = BATCH_RESULT_STATUS_META[result.statusKey] || BATCH_RESULT_STATUS_META.queued
    const canSave = canSaveBatchResult(result)
    card.classList.toggle('selected', batchWorkbenchState.activeResultId === result.id)
    if (refs.statusDot) refs.statusDot.style.background = meta.color
    if (refs.title) refs.title.textContent = result.display || ''
    if (refs.filename) refs.filename.textContent = result.filename || ''
    if (refs.status) refs.status.textContent = result.statusText || meta.text
    if (refs.meta) refs.meta.textContent = `${t('batch.lineCount')}: ${result.lineCount || 0} | ${result.hasTimestamps ? t('batch.hasTimestamp') : t('batch.noTimestamp')} | ${result.saved ? t('batch.saved') : t('batch.notSaved')}`
    if (refs.saveBtn) {
        refs.saveBtn.disabled = !canSave
        refs.saveBtn.textContent = result.saved ? t('batch.resave') : (canSave ? t('btn.save') : t('batch.cannotSave'))
    }
}

function ensureBatchResultListStructure() {
    const controls = getBatchWorkbenchControls()
    if (!controls.resultList) return
    const entries = Array.from(batchWorkbenchState.results.values())
    const ids = new Set(entries.map(item => item.id))

    if (!entries.length) {
        batchWorkbenchState.resultCardMap.clear()
        controls.resultList.innerHTML = '<div class="batch-wb-song-meta">' + t('batch.noTaskResults') + '</div>'
        batchWorkbenchState.resultCardPlaceholder = controls.resultList.firstElementChild
        batchWorkbenchState.resultStructureDirty = false
        return
    }

    if (batchWorkbenchState.resultCardPlaceholder) {
        batchWorkbenchState.resultCardPlaceholder.remove()
        batchWorkbenchState.resultCardPlaceholder = null
    }

    const staleIds = []
    batchWorkbenchState.resultCardMap.forEach((_, id) => {
        if (!ids.has(id)) staleIds.push(id)
    })
    staleIds.forEach(id => {
        const stale = batchWorkbenchState.resultCardMap.get(id)
        if (stale && stale.parentElement) stale.parentElement.removeChild(stale)
        batchWorkbenchState.resultCardMap.delete(id)
    })

    entries.forEach(result => {
        let card = batchWorkbenchState.resultCardMap.get(result.id)
        if (!card) {
            card = createBatchResultCard(result.id)
            batchWorkbenchState.resultCardMap.set(result.id, card)
            batchWorkbenchState.dirtyResultIds.add(result.id)
        }
        if (controls.resultList.lastElementChild !== card) {
            controls.resultList.appendChild(card)
        }
    })

    batchWorkbenchState.resultStructureDirty = false
    batchWorkbenchState.diagnostics.fullRefreshCount += 1
}

function renderBatchWorkbenchResultList() {
    const controls = getBatchWorkbenchControls()
    if (!controls.resultList) return
    if (batchWorkbenchState.resultStructureDirty) {
        ensureBatchResultListStructure()
    }
    const previousActiveId = batchWorkbenchState.lastRenderedActiveResultId
    const activeId = batchWorkbenchState.activeResultId
    if (previousActiveId && previousActiveId !== activeId) {
        batchWorkbenchState.dirtyResultIds.add(previousActiveId)
    }
    if (activeId && previousActiveId !== activeId) {
        batchWorkbenchState.dirtyResultIds.add(activeId)
    }

    const dirtyIds = Array.from(batchWorkbenchState.dirtyResultIds)
    dirtyIds.forEach(resultId => {
        const result = batchWorkbenchState.results.get(resultId)
        const card = batchWorkbenchState.resultCardMap.get(resultId)
        if (!result || !card) return
        updateBatchResultCard(card, result)
        batchWorkbenchState.diagnostics.partialUpdateCount += 1
    })
    batchWorkbenchState.dirtyResultIds.clear()
    applyBatchRuntimeStatsToUi()
    if (batchWorkbenchState.detailDirty || previousActiveId !== activeId) {
        renderBatchWorkbenchDetail()
        batchWorkbenchState.detailDirty = false
    }
    batchWorkbenchState.lastRenderedActiveResultId = activeId
}

async function saveBatchResultById(resultId) {
    const target = batchWorkbenchState.results.get(resultId)
    if (!target || !target.summary || !target.payload) {
        alert(t('alert.noSaveResult'))
        return false
    }
    try {
        target.statusKey = 'saving'
        target.statusText = t('resultStatus.saving')
        markBatchResultDirty(resultId, { detail: batchWorkbenchState.activeResultId === resultId })
        const path = await saveBatchTranslationToDisk(target.summary, target.payload)
        target.saved = true
        target.savePath = path
        target.statusKey = 'success'
        target.statusText = t('batch.successCount')
        markBatchResultDirty(resultId, { detail: batchWorkbenchState.activeResultId === resultId })
        return true
    } catch (error) {
        target.saved = false
        target.error = error && error.message ? error.message : String(error)
        target.statusKey = 'failed'
        target.statusText = t('batch.savePhaseFailed')
        markBatchResultDirty(resultId, { detail: batchWorkbenchState.activeResultId === resultId })
        return false
    }
}

async function saveBatchWorkbenchResults(mode) {
    const entries = Array.from(batchWorkbenchState.results.values())
    const targets = entries.filter(result => {
        if (!canSaveBatchResult(result)) return false
        if (mode === 'all') return true
        if (mode === 'pending') return !result.saved
        if (mode === 'failed') return result.statusKey === 'failed'
        return false
    })
    if (!targets.length) {
        alert(t('alert.noSaveResult'))
        return
    }
    for (const result of targets) {
        await saveBatchResultById(result.id)
    }
}

function updateBatchWorkbenchRuntimeCounters() {
    applyBatchRuntimeStatsToUi()
}

function renderBatchWorkbenchDetail() {
    const controls = getBatchWorkbenchControls()
    if (!controls.detail) return
    const target = batchWorkbenchState.activeResultId ? batchWorkbenchState.results.get(batchWorkbenchState.activeResultId) : null
    if (!target) {
        controls.detail.style.display = 'none'
        controls.detail.innerHTML = ''
        return
    }
    controls.detail.style.display = 'block'
    const canSave = canSaveBatchResult(target)
    controls.detail.innerHTML = `
        <h4 class="batch-wb-section-title">${escapeHtml(target.display)} ${t('batch.detailTitle')}</h4>
        <div style="font-size:12px;">${t('batch.originalPreview')}</div>
        <div class="batch-wb-pre">${escapeHtml(target.originalPreview || t('batch.empty'))}</div>
        <div style="font-size:12px;">${t('batch.translationPreview')}</div>
        <div class="batch-wb-pre">${escapeHtml(target.translationPreview || t('batch.empty'))}</div>
        <div style="font-size:12px;">${t('batch.errorInfo')}</div>
        <div class="batch-wb-pre">${escapeHtml(target.error || t('batch.none'))}</div>
        <div style="font-size:12px;">${t('batch.savePath')}${escapeHtml(target.savePath || t('batch.notSavedPath'))}</div>
        <div style="font-size:12px;">${t('batch.diagnostic')} ${batchWorkbenchState.diagnostics.streamChunkCount} | ${batchWorkbenchState.diagnostics.uiRefreshCount} | ${batchWorkbenchState.diagnostics.partialUpdateCount} | ${batchWorkbenchState.diagnostics.fullRefreshCount}</div>
        <div class="batch-wb-btn-row" style="margin-top:8px;">
            <button class="action-button" onclick="copyBatchResultTranslation()">${t('batch.copyTranslation')}</button>
            ${canSave ? `<button class="action-button" onclick="resaveBatchResult()">${target.saved ? t('batch.resave') : t('btn.save')}</button>` : `<button class="action-button" disabled>${t('batch.cannotSave')}</button>`}
            <button class="action-button" onclick="openBatchResultInEditor()">${t('batch.openInEditor')}</button>
        </div>
    `
}

function getBatchWorkbenchSettingsFromUI() {
    const controls = getBatchWorkbenchControls()
    return {
        provider: controls.aiProvider ? controls.aiProvider.value : (localStorage.getItem('aiProvider') || 'deepseek'),
        baseUrl: sanitizeBaseUrl(controls.aiBaseUrl ? controls.aiBaseUrl.value : (localStorage.getItem('aiBaseUrl') || '')),
        model: controls.aiModel ? controls.aiModel.value : (localStorage.getItem('aiModel') || ''),
        expectReasoning: controls.expectReasoningCheck ? controls.expectReasoningCheck.checked : (localStorage.getItem('aiExpectReasoning') === 'true'),
        compatMode: controls.compatModeCheck ? controls.compatModeCheck.checked : (localStorage.getItem('aiCompatMode') === 'true'),
        stripBrackets: controls.stripBracketsCheck ? controls.stripBracketsCheck.checked : (localStorage.getItem('aiStripBrackets') === 'true'),
        experimentalFullLineBracketStrip: controls.experimentalFullLineBracketStripCheck ? controls.experimentalFullLineBracketStripCheck.checked : (localStorage.getItem('aiExperimentalFullLineBracketStrip') === 'true'),
        experimentalBracketLineAsSubline: controls.experimentalBracketLineAsSublineCheck ? controls.experimentalBracketLineAsSublineCheck.checked : (localStorage.getItem('aiExperimentalBracketLineAsSubline') === 'true'),
        thinkingEnabled: controls.thinkingEnabledCheck ? controls.thinkingEnabledCheck.checked : (localStorage.getItem('aiThinkingEnabled') !== 'false'),
        autoSave: controls.autoSave ? controls.autoSave.checked : true,
        onlyEmpty: controls.onlyEmptyCheck ? controls.onlyEmptyCheck.checked : true,
        alwaysOverride: controls.alwaysOverrideCheck ? controls.alwaysOverrideCheck.checked : false,
        systemPrompt: controls.systemPrompt ? controls.systemPrompt.value : (localStorage.getItem('aiSystemPrompt') || ''),
        extraPrompt: controls.extraPrompt ? controls.extraPrompt.value : (localStorage.getItem('batchExtraPrompt') || '')
    }
}

function applyBatchWorkbenchSettingsToUI() {
    const controls = getBatchWorkbenchControls()
    if (!controls.aiProvider) return
    const activePreset = getBatchWorkbenchActivePreset()
    const mainProvider = document.getElementById('aiProvider')?.value || 'deepseek'
    const mainBaseUrl = document.getElementById('aiBaseUrl')?.value || ''
    const mainModel = document.getElementById('aiModel')?.value || ''
    const providerValue = activePreset?.translation?.provider || localStorage.getItem('aiProvider') || mainProvider
    const preset = PROVIDER_PRESETS[providerValue] || {}
    const sourceState = activePreset ? getBatchWorkbenchPresetStateFromPreset(activePreset) : null
    controls.aiProvider.value = providerValue
    controls.aiBaseUrl.value = activePreset ? (sourceState.translation.base_url || preset.baseUrl || '') : (localStorage.getItem('aiBaseUrl') || mainBaseUrl || preset.baseUrl || '')
    controls.aiModel.value = activePreset ? (sourceState.translation.model || preset.model || '') : (localStorage.getItem('aiModel') || mainModel || preset.model || '')
    if (controls.expectReasoningCheck) controls.expectReasoningCheck.checked = activePreset ? sourceState.translation.expect_reasoning : (localStorage.getItem('aiExpectReasoning') !== null ? localStorage.getItem('aiExpectReasoning') === 'true' : (document.getElementById('aiExpectReasoning')?.checked ?? true))
    if (controls.compatModeCheck) controls.compatModeCheck.checked = activePreset ? sourceState.translation.compat_mode : (localStorage.getItem('aiCompatMode') !== null ? localStorage.getItem('aiCompatMode') === 'true' : (document.getElementById('aiCompatMode')?.checked ?? false))
    if (controls.stripBracketsCheck) controls.stripBracketsCheck.checked = activePreset ? sourceState.translation.strip_brackets : (localStorage.getItem('aiStripBrackets') !== null ? localStorage.getItem('aiStripBrackets') === 'true' : (document.getElementById('aiStripBrackets')?.checked ?? false))
    if (controls.experimentalFullLineBracketStripCheck) controls.experimentalFullLineBracketStripCheck.checked = activePreset ? sourceState.translation.experimental_full_line_bracket_strip : (localStorage.getItem('aiExperimentalFullLineBracketStrip') !== null ? localStorage.getItem('aiExperimentalFullLineBracketStrip') === 'true' : (document.getElementById('aiExperimentalFullLineBracketStrip')?.checked ?? false))
    if (controls.experimentalBracketLineAsSublineCheck) controls.experimentalBracketLineAsSublineCheck.checked = activePreset ? sourceState.translation.experimental_bracket_line_as_subline : (localStorage.getItem('aiExperimentalBracketLineAsSubline') !== null ? localStorage.getItem('aiExperimentalBracketLineAsSubline') === 'true' : (document.getElementById('aiExperimentalBracketLineAsSubline')?.checked ?? false))
    if (controls.thinkingEnabledCheck) controls.thinkingEnabledCheck.checked = activePreset ? sourceState.thinking.enabled : (localStorage.getItem('aiThinkingEnabled') !== null ? localStorage.getItem('aiThinkingEnabled') === 'true' : (document.getElementById('aiThinkingEnabled')?.checked ?? true))
    if (controls.autoSave) controls.autoSave.checked = activePreset ? sourceState.batch.auto_save : localStorage.getItem('batchWorkbenchAutoSave') !== 'false'
    if (controls.onlyEmptyCheck) controls.onlyEmptyCheck.checked = activePreset ? sourceState.batch.only_empty : localStorage.getItem('batchWorkbenchOnlyEmpty') !== 'false'
    if (controls.alwaysOverrideCheck) controls.alwaysOverrideCheck.checked = activePreset ? sourceState.batch.always_override : localStorage.getItem('batchWorkbenchAlwaysOverride') === 'true'
    if (controls.systemPrompt) controls.systemPrompt.value = activePreset ? sourceState.translation.system_prompt : (localStorage.getItem('aiSystemPrompt') || '')
    if (controls.extraPrompt) controls.extraPrompt.value = activePreset ? sourceState.batch.extra_prompt : (localStorage.getItem('batchExtraPrompt') || '')
    if (controls.settingsSource) controls.settingsSource.textContent = activePreset ? `${t('batch.fromPreset')}${activePreset.name}` : t('batch.globalSettings')
    if (controls.settingsNote) controls.settingsNote.textContent = activePreset ? t('batch.loadedPreset') : t('batch.notOverridden')
    updateBatchWorkbenchBaseUrlAndModel()
    applyAiPresetFieldPermissions(aiPresetPermissions)
    batchWorkbenchState.activePresetId = activePreset ? activePreset.id : ''
    if (controls.presetSelect && activePreset) {
        controls.presetSelect.value = activePreset.id
    }
    syncBatchWbSettingsSummary()
}

function updateBatchWorkbenchSettingsPreview() {
    applyBatchWorkbenchSettingsToUI()
}

function syncBatchWorkbenchPromptFields() {
    const controls = getBatchWorkbenchControls()
    if (controls.systemPrompt && !controls.systemPrompt.value) {
        controls.systemPrompt.value = localStorage.getItem('aiSystemPrompt') || ''
    }
    if (controls.extraPrompt && !controls.extraPrompt.value) {
        controls.extraPrompt.value = localStorage.getItem('batchExtraPrompt') || ''
    }
}

function persistBatchWorkbenchSettingsToStorage() {
    const settings = getBatchWorkbenchSettingsFromUI()
    localStorage.setItem('aiProvider', settings.provider)
    localStorage.setItem('aiBaseUrl', settings.baseUrl)
    localStorage.setItem('aiModel', settings.model)
    localStorage.setItem('aiExpectReasoning', String(settings.expectReasoning))
    localStorage.setItem('aiCompatMode', String(settings.compatMode))
    localStorage.setItem('aiStripBrackets', String(settings.stripBrackets))
    localStorage.setItem('aiExperimentalFullLineBracketStrip', String(settings.experimentalFullLineBracketStrip))
    localStorage.setItem('aiExperimentalBracketLineAsSubline', String(settings.experimentalBracketLineAsSubline))
    localStorage.setItem('aiThinkingEnabled', String(settings.thinkingEnabled))
    localStorage.setItem('batchWorkbenchAutoSave', String(settings.autoSave))
    localStorage.setItem('batchWorkbenchOnlyEmpty', String(settings.onlyEmpty))
    localStorage.setItem('batchWorkbenchAlwaysOverride', String(settings.alwaysOverride))
    if (settings.systemPrompt) {
        localStorage.setItem('aiSystemPrompt', settings.systemPrompt)
    } else {
        localStorage.removeItem('aiSystemPrompt')
    }
    if (settings.extraPrompt) {
        localStorage.setItem('batchExtraPrompt', settings.extraPrompt)
    } else {
        localStorage.removeItem('batchExtraPrompt')
    }

    const mainControls = {
        provider: document.getElementById('aiProvider'),
        baseUrl: document.getElementById('aiBaseUrl'),
        model: document.getElementById('aiModel'),
        expectReasoning: document.getElementById('aiExpectReasoning'),
        compatMode: document.getElementById('aiCompatMode'),
        stripBrackets: document.getElementById('aiStripBrackets'),
        experimentalFullLineBracketStrip: document.getElementById('aiExperimentalFullLineBracketStrip'),
        experimentalBracketLineAsSubline: document.getElementById('aiExperimentalBracketLineAsSubline'),
        thinkingEnabled: document.getElementById('aiThinkingEnabled'),
        systemPrompt: document.getElementById('aiSystemPrompt')
    }
    if (mainControls.provider) mainControls.provider.value = settings.provider
    if (mainControls.baseUrl) mainControls.baseUrl.value = settings.baseUrl
    if (mainControls.model) mainControls.model.value = settings.model
    if (mainControls.expectReasoning) mainControls.expectReasoning.checked = settings.expectReasoning
    if (mainControls.compatMode) mainControls.compatMode.checked = settings.compatMode
    if (mainControls.stripBrackets) mainControls.stripBrackets.checked = settings.stripBrackets
    if (mainControls.experimentalFullLineBracketStrip) mainControls.experimentalFullLineBracketStrip.checked = settings.experimentalFullLineBracketStrip
    if (mainControls.experimentalBracketLineAsSubline) mainControls.experimentalBracketLineAsSubline.checked = settings.experimentalBracketLineAsSubline
    if (mainControls.thinkingEnabled) mainControls.thinkingEnabled.checked = settings.thinkingEnabled
    if (mainControls.systemPrompt) mainControls.systemPrompt.value = settings.systemPrompt

    const hiddenBatchExtraPrompt = document.getElementById('batchExtraPrompt')
    if (hiddenBatchExtraPrompt) {
        hiddenBatchExtraPrompt.value = settings.extraPrompt
    }
}

function syncBatchWbSettingsSummary() {
    const controls = getBatchWorkbenchControls()
    if (!controls.summaryModel) return
    const settings = getBatchWorkbenchSettingsFromUI()
    const runtimeModel = typeof getAiRuntimeSummaryLabel === 'function'
        ? getAiRuntimeSummaryLabel('translation').model
        : ''
    controls.summaryModel.textContent = runtimeModel || settings.model || t('batch.notSet')
    controls.summaryCoverageMode.textContent = settings.alwaysOverride ? t('batch.alwaysOverrideShort') : (settings.onlyEmpty ? t('batch.onlyEmptyShort') : t('batch.byCurrentRules'))
    const activePreset = getBatchWorkbenchActivePreset()
    const currentPresetState = getBatchWorkbenchFullPresetStateFromUI()
    if (controls.settingsSource) {
        if (activePreset) {
            controls.settingsSource.textContent = `${t('batch.fromPreset')}${activePreset.name}`
        } else {
            const storageMatches = settings.provider === (localStorage.getItem('aiProvider') || 'deepseek')
                && settings.baseUrl === sanitizeBaseUrl(localStorage.getItem('aiBaseUrl') || '')
                && settings.model === (localStorage.getItem('aiModel') || '')
                && settings.compatMode === (localStorage.getItem('aiCompatMode') === 'true')
                && settings.stripBrackets === (localStorage.getItem('aiStripBrackets') === 'true')
                && settings.experimentalFullLineBracketStrip === (localStorage.getItem('aiExperimentalFullLineBracketStrip') === 'true')
                && settings.experimentalBracketLineAsSubline === (localStorage.getItem('aiExperimentalBracketLineAsSubline') === 'true')
                && settings.thinkingEnabled === (localStorage.getItem('aiThinkingEnabled') !== 'false')
                && settings.autoSave === (localStorage.getItem('batchWorkbenchAutoSave') !== 'false')
                && settings.onlyEmpty === (localStorage.getItem('batchWorkbenchOnlyEmpty') !== 'false')
                && settings.alwaysOverride === (localStorage.getItem('batchWorkbenchAlwaysOverride') === 'true')
                && settings.systemPrompt === (localStorage.getItem('aiSystemPrompt') || '')
                && settings.extraPrompt === (localStorage.getItem('batchExtraPrompt') || '')
            controls.settingsSource.textContent = storageMatches ? t('batch.globalSettings') : t('batch.thisOverride')
        }
    }
    if (controls.settingsNote) {
        if (activePreset) {
            controls.settingsNote.textContent = batchWorkbenchPresetMatchesCurrent(activePreset) ? t('batch.matchPreset') : t('batch.tempModified')
        } else {
            const sourceMatches = JSON.stringify(getBatchWorkbenchPresetVisibleState(currentPresetState)) === JSON.stringify(getBatchWorkbenchPresetVisibleState({
                translation: {
                    provider: localStorage.getItem('aiProvider') || 'deepseek',
                    base_url: localStorage.getItem('aiBaseUrl') || '',
                    model: localStorage.getItem('aiModel') || '',
                    system_prompt: localStorage.getItem('aiSystemPrompt') || '',
                    expect_reasoning: localStorage.getItem('aiExpectReasoning') === 'true',
                    compat_mode: localStorage.getItem('aiCompatMode') === 'true',
                    strip_brackets: localStorage.getItem('aiStripBrackets') === 'true',
                    experimental_full_line_bracket_strip: localStorage.getItem('aiExperimentalFullLineBracketStrip') === 'true',
                    experimental_bracket_line_as_subline: localStorage.getItem('aiExperimentalBracketLineAsSubline') === 'true'
                },
                thinking: {
                    enabled: localStorage.getItem('aiThinkingEnabled') !== 'false'
                },
                batch: {
                    auto_save: localStorage.getItem('batchWorkbenchAutoSave') !== 'false',
                    only_empty: localStorage.getItem('batchWorkbenchOnlyEmpty') !== 'false',
                    always_override: localStorage.getItem('batchWorkbenchAlwaysOverride') === 'true',
                    extra_prompt: localStorage.getItem('batchExtraPrompt') || ''
                }
            }))
            controls.settingsNote.textContent = sourceMatches ? t('batch.matchGlobal') : t('batch.tempModified')
        }
    }
    if (controls.overrideWarning) {
        controls.overrideWarning.style.display = settings.alwaysOverride ? 'block' : 'none'
    }
}

function syncBatchWbHeaderSummary() {
    const controls = getBatchWorkbenchControls()
    if (!controls.summaryTotal) return
    const selectedSummaries = Array.from(selectedSongs)
        .map(filename => songSummaryCache.get(filename))
        .filter(Boolean)
    const settings = getBatchWorkbenchSettingsFromUI()
    const total = selectedSummaries.length
    const processable = selectedSummaries.filter(summary => {
        if (!hasLyricsSummary(summary)) return false
        if (settings.alwaysOverride) return true
        return settings.onlyEmpty ? !hasSummaryTranslation(summary) : true
    }).length
    const hasTranslation = selectedSummaries.filter(summary => hasSummaryTranslation(summary)).length
    controls.summaryTotal.textContent = String(total)
    controls.summaryProcessable.textContent = String(processable)
    controls.summaryHasTranslation.textContent = String(hasTranslation)
}

function onBatchWbOverrideToggle(checkbox) {
    const controls = getBatchWorkbenchControls()
    if (checkbox && checkbox.checked) {
        if (controls.onlyEmptyCheck) controls.onlyEmptyCheck.checked = false
    } else if (controls.onlyEmptyCheck && controls.onlyEmptyCheck.checked) {
        if (controls.alwaysOverrideCheck) controls.alwaysOverrideCheck.checked = false
    }
    syncBatchWbSettingsSummary()
}

function captureAISettingsSnapshot() {
    const keys = [
        'aiSystemPrompt', 'aiProvider', 'aiBaseUrl', 'aiModel',
        'aiExpectReasoning', 'aiCompatMode', 'aiStripBrackets', 'aiExperimentalFullLineBracketStrip', 'aiExperimentalBracketLineAsSubline', 'aiThinkingEnabled',
        'aiThinkingProvider', 'aiThinkingBaseUrl', 'aiThinkingModel',
        'aiThinkingPrompt', 'batchWorkbenchAutoSave', 'batchWorkbenchOnlyEmpty',
        'batchWorkbenchAlwaysOverride', 'batchExtraPrompt'
    ]
    const storage = {}
    keys.forEach(key => {
        storage[key] = localStorage.getItem(key)
    })
    const ids = [
        'aiSystemPrompt', 'aiProvider', 'aiBaseUrl', 'aiModel',
        'aiExpectReasoning', 'aiCompatMode', 'aiStripBrackets', 'aiExperimentalFullLineBracketStrip', 'aiExperimentalBracketLineAsSubline', 'aiThinkingEnabled',
        'aiThinkingProvider', 'aiThinkingBaseUrl', 'aiThinkingModel',
        'aiThinkingPrompt', 'batchExtraPrompt'
    ]
    const inputs = {}
    ids.forEach(id => {
        const el = document.getElementById(id)
        if (!el) return
        inputs[id] = el.type === 'checkbox' ? el.checked : el.value
    })
    return { storage, inputs }
}

function restoreAISettingsSnapshot(snapshot) {
    if (!snapshot) return
    Object.entries(snapshot.storage || {}).forEach(([key, value]) => {
        if (value === null || value === undefined) {
            localStorage.removeItem(key)
        } else {
            localStorage.setItem(key, value)
        }
    })
    Object.entries(snapshot.inputs || {}).forEach(([id, value]) => {
        const el = document.getElementById(id)
        if (!el) return
        if (el.type === 'checkbox') {
            el.checked = Boolean(value)
        } else {
            el.value = value || ''
        }
    })
}

async function testBatchWbConnection() {
        const settings = getBatchWorkbenchSettingsFromUI()
        try {
            if (typeof ensureAiRuntimeSummaryForProgress === 'function') {
                await ensureAiRuntimeSummaryForProgress()
            }
            const buildProbePayload = (mode) => {
                const payload = { mode }
                if (typeof isLocalAiAdminProbeAllowed === 'function' && isLocalAiAdminProbeAllowed()) {
                    Object.assign(payload, {
                        intent: 'probe_form',
                        ...getBatchWorkbenchFullPresetStateFromUI(),
                        compat_mode: settings.compatMode,
                        thinking_enabled: settings.thinkingEnabled
                    })
                }
                return payload
            }

        const response = await fetch('/probe_ai', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(buildProbePayload('translation'))
        })
        const data = await response.json()
        if (!data || data.status !== 'success') {
            throw new Error(data?.message || t('batch.modelReturnError'))
        }

        let thinkingResultText = ''
        const thinkingEnabled = typeof isThinkingEnabledFromRuntimeSummary === 'function'
            ? isThinkingEnabledFromRuntimeSummary()
            : settings.thinkingEnabled
        if (thinkingEnabled) {
            const thinkingResponse = await fetch('/probe_ai', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(buildProbePayload('thinking'))
            })
            const thinkingData = await thinkingResponse.json()
            if (!thinkingData || thinkingData.status !== 'success') {
                throw new Error(thinkingData?.message || t('batch.thinkingFailed'))
            }
            const thinkingRuntime = typeof getAiRuntimeSummaryLabel === 'function'
                ? getAiRuntimeSummaryLabel('thinking')
                : { provider: settings.provider, model: settings.model }
            thinkingResultText = `${t('batch.thinkingModelInfo')}${thinkingData.model || thinkingRuntime.model || ''}${t('batch.thinkingBaseUrlInfo')}${thinkingData.base_url || ''}`
        }

        const translationRuntime = typeof getAiRuntimeSummaryLabel === 'function'
            ? getAiRuntimeSummaryLabel('translation')
            : { provider: settings.provider, model: settings.model }
        const extra = data.note ? `\n${data.note}` : ''
        alert(t('runtime.connectionSuccess') + '\n' + t('batch.modelInfo') + (data.model || translationRuntime.model || settings.model) + '\n' + t('batch.baseUrlInfo') + (data.base_url || settings.baseUrl) + extra + thinkingResultText)
    } catch (error) {
        alert(t('runtime.connectionFailed') + (error.message || error))
    }
}

async function saveBatchWbCurrentSettings() {
    const snapshot = captureAISettingsSnapshot()
    persistBatchWorkbenchSettingsToStorage()
    const controls = getBatchWorkbenchControls()
    if (controls.settingsSource) {
        controls.settingsSource.textContent = t('status.saving')
    }
    try {
        const result = typeof saveAISettings === 'function' ? await saveAISettings({ silent: true, skipClose: true }) : { status: 'error', message: '主设置保存函数不可用' }
        if (!result || result.status !== 'success') {
            throw new Error(result?.message || t('batch.savePhaseFailed'))
        }
        localStorage.removeItem(BATCH_WORKBENCH_ACTIVE_PRESET_KEY)
        batchWorkbenchState.activePresetId = ''
        updateBatchWorkbenchPresetSelect()
        updateBatchWorkbenchBaseUrlAndModel()
        if (controls.settingsSource) {
            controls.settingsSource.textContent = t('status.saved')
        }
        syncBatchWbSettingsSummary()
    } catch (error) {
        restoreAISettingsSnapshot(snapshot)
        applyBatchWorkbenchSettingsToUI()
        syncBatchWbSettingsSummary()
        if (controls.settingsSource) {
            controls.settingsSource.textContent = t('status.saveFailed')
        }
        alert(t('batch.saveFailedMsg', {msg: error && error.message ? error.message : String(error)}))
    }
}

function openBatchWorkbench() {
    const controls = getBatchWorkbenchControls()
    if (!controls.modal) return
    batchWorkbenchState.visible = true
    controls.modal.classList.add('show')
    fetchDefaultBatchPromptParts().then(defaults => {
        const fixedPromptEl = document.getElementById('batchWbFixedPrompt')
        if (fixedPromptEl) {
            fixedPromptEl.textContent = defaults.fixedPrompt || t('batch.noFixedRules')
        }
    }).catch(() => {})
    updateBatchWorkbenchPresetSelect()
    renderBatchWorkbenchFilterRow()
    renderBatchWorkbenchSongList()
    updateBatchWorkbenchSettingsPreview()
    syncBatchWorkbenchPromptFields()
    renderBatchWorkbenchSummary()
    renderBatchWorkbenchResultList()
    syncBatchWbHeaderSummary()
    syncBatchWbSettingsSummary()
    setBatchWorkbenchRunStatus(batchWorkbenchState.latestStage || t('batch.statusWaiting'))
    setBatchWorkbenchProgress(batchWorkbenchState.totals.completed || 0, batchWorkbenchState.totals.total || 0)
}

function closeBatchWorkbench() {
    if (batchWorkbenchState.running) {
        if (!confirm(t('confirm.closeRunningWorkbench'))) return
    }
    const controls = getBatchWorkbenchControls()
    if (!controls.modal) return
    controls.modal.classList.remove('show')
    batchWorkbenchState.visible = false
}

function handleBatchWorkbenchBackdrop(event) {
    if (event.target && event.target.id === 'batchWorkbenchModal') {
        closeBatchWorkbench()
    }
}

function toggleBatchWorkbenchMaximize() {
    const controls = getBatchWorkbenchControls()
    if (!controls.dialog) return
    batchWorkbenchState.maximized = !batchWorkbenchState.maximized
    controls.dialog.classList.toggle('maximized', batchWorkbenchState.maximized)
}

function batchWorkbenchSelectCurrent() {
    const summaries = getBatchWorkbenchFilteredSummaries()
    summaries.forEach(summary => selectedSongs.add(summary.filename))
    
    renderBatchSelectionCount()
    renderBatchWorkbenchSongList()
}

function batchWorkbenchSelectUntranslated() {
    const summaries = getBatchWorkbenchSummaryList().filter(summary => !hasSummaryTranslation(summary))
    summaries.forEach(summary => selectedSongs.add(summary.filename))
    
    renderBatchSelectionCount()
    renderBatchWorkbenchSongList()
}

function copyBatchResultTranslation() {
    const target = batchWorkbenchState.results.get(batchWorkbenchState.activeResultId)
    if (!target || !target.translationText) {
        alert(t('alert.noCopyTranslation'))
        return
    }
    navigator.clipboard.writeText(target.translationText).then(() => {
        alert(t('alert.copiedTranslation'))
    }).catch(() => {
        alert(t('alert.copyFailed'))
    })
}

async function resaveBatchResult() {
    const target = batchWorkbenchState.results.get(batchWorkbenchState.activeResultId)
    if (!target) {
        alert(t('alert.noResaveResult'))
        return
    }
    await saveBatchResultById(target.id)
}

async function openBatchResultInEditor() {
    const target = batchWorkbenchState.results.get(batchWorkbenchState.activeResultId)
    if (!target || !target.summary) {
        alert(t('alert.selectSongInBatch'))
        return
    }
    await editLyrics(target.summary.lyricsPath || '', target.summary.translationPath || '', 0, target.summary.filename)
    closeBatchWorkbench()
}

async function fetchDefaultBatchPromptParts() {
    if (defaultBatchSystemPromptCache && defaultBatchFixedPromptCache) {
        return {
            systemPrompt: defaultBatchSystemPromptCache,
            fixedPrompt: defaultBatchFixedPromptCache
        }
    }
    const storageValue = localStorage.getItem('defaultAiSystemPrompt')
    if (storageValue && String(storageValue).trim()) {
        defaultBatchSystemPromptCache = String(storageValue)
    }
    try {
        const response = await fetch('/get_ai_settings')
        const data = await response.json()
        const backendDefault = data && data.status === 'success'
            ? String(
                (data.defaults && data.defaults.system_prompt && String(data.defaults.system_prompt).trim())
                    ? data.defaults.system_prompt
                    : ((data.system_prompt && String(data.system_prompt).trim())
                        ? data.system_prompt
                    : (data.settings && data.settings.system_prompt ? data.settings.system_prompt : '')
                    )
            )
            : ''
        if (backendDefault.trim()) {
            defaultBatchSystemPromptCache = backendDefault
            localStorage.setItem('defaultAiSystemPrompt', backendDefault)
        }
        const backendFixedPrompt = data && data.status === 'success'
            ? String((data.defaults && data.defaults.batch_fixed_prompt) || '').trim()
            : ''
        if (backendFixedPrompt) {
            defaultBatchFixedPromptCache = backendFixedPrompt
        }
    } catch (error) {
        console.warn('获取默认批量提示词失败：', error)
    }
    return {
        systemPrompt: defaultBatchSystemPromptCache || localStorage.getItem('aiSystemPrompt') || '',
        fixedPrompt: defaultBatchFixedPromptCache || ''
    }
}

async function restoreBatchPromptDefaults() {
    const controls = getBatchWorkbenchControls()
    if (!controls.systemPrompt || !controls.extraPrompt) return
    const defaults = await fetchDefaultBatchPromptParts()
    controls.systemPrompt.value = defaults.systemPrompt || ''
    controls.extraPrompt.value = ''
    localStorage.setItem('batchExtraPrompt', '')
    const hiddenBatchExtraPrompt = document.getElementById('batchExtraPrompt')
    if (hiddenBatchExtraPrompt) hiddenBatchExtraPrompt.value = ''
    const fixedPromptEl = document.getElementById('batchWbFixedPrompt')
    if (fixedPromptEl) fixedPromptEl.textContent = defaults.fixedPrompt || t('batch.noFixedRules')
}

async function previewBatchFinalPrompt() {
    const controls = getBatchWorkbenchControls()
    const systemPrompt = String(controls.systemPrompt && controls.systemPrompt.value ? controls.systemPrompt.value : '').trim()
    const extraPrompt = String(controls.extraPrompt && controls.extraPrompt.value ? controls.extraPrompt.value : '').trim()
    const defaults = await fetchDefaultBatchPromptParts()
    const fixedPrompt = String(defaults.fixedPrompt || defaultBatchFixedPromptCache || '').trim()
    const finalPrompt = [systemPrompt, fixedPrompt, extraPrompt].filter(Boolean).join('\n\n')
    alert(finalPrompt ? t('batch.viewPromptMain') + '\n' + (systemPrompt || t('batch.promptEmpty')) + '\n\n' + t('batch.viewPromptFixed') + '\n' + (fixedPrompt || t('batch.promptEmpty')) + '\n\n' + t('batch.viewPromptExtra') + '\n' + (extraPrompt || t('batch.promptEmpty')) + '\n\n' + t('batch.viewPromptFinal') + '\n' + finalPrompt : t('batch.promptEmpty'))
}

function stopBatchTranslate() {
    if (!batchWorkbenchState.running) {
        alert(t('alert.noRunningTask'))
        return
    }
    batchWorkbenchState.stopRequested = true
    if (batchTranslateAbortController) {
        batchTranslateAbortController.abort()
    }
    setBatchWorkbenchRunStatus(t('batch.interrupting'), 'stopped')
}

function getSortComparator() {
    return (filenameA, filenameB) => {
        if (currentSort.type === 'name') {
            const textA = String(songSortNameCache.get(filenameA) || '')
            const textB = String(songSortNameCache.get(filenameB) || '')
            const sortLocale = _currentLang === 'en' ? 'en' : 'zh-Hans-CN';
            return currentSort.asc ? textA.localeCompare(textB, sortLocale)
                : textB.localeCompare(textA, sortLocale)
        }
        const summaryA = songSummaryCache.get(filenameA)
        const summaryB = songSummaryCache.get(filenameB)
        const timeA = Number(summaryA && summaryA.mtime ? summaryA.mtime : 0)
        const timeB = Number(summaryB && summaryB.mtime ? summaryB.mtime : 0)
        if (timeA !== timeB) {
            return currentSort.asc ? timeA - timeB : timeB - timeA
        }
        const textA = String(songSortNameCache.get(filenameA) || '')
        const textB = String(songSortNameCache.get(filenameB) || '')
        const _sortLocale2 = _currentLang === 'en' ? 'en' : 'zh-Hans-CN';
        return textA.localeCompare(textB, _sortLocale2)
    }
}

function parseKeywords(rawText) {
    const normalized = rawText.replace(/，/g, ',')
    const keywords = normalized.split(',').map(keyword => keyword.trim()).filter(Boolean)
    return Array.from(new Set(keywords))
}

function parseFuzzyKeywords(rawText) {
    const normalized = rawText.replace(/[,，\s\-_]+/g, '')
    const keywords = Array.from(normalized).filter(Boolean)
    return Array.from(new Set(keywords))
}

function hasMainListSearchKeywords() {
    const rawText = String(searchBox && searchBox.value ? searchBox.value : '').toLowerCase()
    const isFuzzy = fuzzySearchToggle && fuzzySearchToggle.checked
    const keywords = isFuzzy ? parseFuzzyKeywords(rawText) : parseKeywords(rawText)
    return keywords.length > 0
}

function updateSongLibraryProgressStatus() {
    if (!jsonListStatus) return
    if (libraryMode === 'search') {
        const loaded = allSongFilenames.length
        const total = typeof searchTotal === 'number' ? searchTotal : 0
        let text = t('batch.searchResultsProgress', { total, loaded })
        if (searchHasMore) {
            text += searchIsLoading ? (' ' + t('batch.searchLoadingMore')) : (' ' + t('batch.searchHasMoreHint'))
        }
        setListStatus(text, false, false)
        return
    }
    if (!songSummaryTotal) return
    const loaded = allSongFilenames.length
    const text = t('batch.loadedProgress', { current: loaded, total: songSummaryTotal })
    setListStatus(text, false, false)
}

function setListStatus(message, isError = false, loading = false) {
    if (!jsonListStatus) return
    if (!message) {
        jsonListStatus.style.display = 'none'
        jsonListStatus.innerHTML = ''
        return
    }
    jsonListStatus.style.display = 'flex'
    jsonListStatus.style.color = isError ? '#d9534f' : '#666'
    if (loading) {
        jsonListStatus.innerHTML = `<span class="spinner"></span><span>${message}</span>`
    } else {
        jsonListStatus.textContent = message
    }
}

function toggleLoader(show, text = 'Loading') {
    const wrapper = document.getElementById('wifiLoaderWrapper')
    const textEl = document.querySelector('#wifi-loader .text')
    if (!wrapper) return
    if (textEl && text) {
        textEl.setAttribute('data-text', text)
    }
    wrapper.style.display = show ? 'flex' : 'none'
}

let monacoLoaderPromise = null
let lyricsMonacoEditor = null
let translationMonacoEditor = null
let lyricsSearchMonacoEditor = null
let translationSearchMonacoEditor = null
let pendingLyricsText = ''
let pendingTranslationText = ''
let pendingSearchLyricsText = ''
let pendingSearchTranslationText = ''
let monacoTheme = 'vs'

function applyMonacoTheme() {
    const target = document.body.classList.contains('dark-mode') ? 'vs-dark' : 'vs'
    monacoTheme = target
    loadMonaco().then(monaco => {
        if (monaco && monaco.editor) {
            monaco.editor.setTheme(target)
        }
    }).catch(() => { /* ignore */ })
}

function loadMonaco() {
    if (monacoLoaderPromise) return monacoLoaderPromise
    monacoLoaderPromise = new Promise((resolve, reject) => {
        if (window.monaco) {
            resolve(window.monaco)
            return
        }
        const script = document.createElement('script')
        script.src = '/monaco/vs/loader.js'
        script.onload = () => {
            if (!window.require) {
                reject(new Error('Monaco loader 未找到'))
                return
            }
            window.require.config({ paths: { 'vs': '/monaco/vs' } })
            window.require(['vs/editor/editor.main'], () => resolve(window.monaco))
        }
        script.onerror = () => reject(new Error('加载 Monaco 失败'))
        document.body.appendChild(script)
    })
    return monacoLoaderPromise
}

function buildMonacoOptions(value) {
    return {
        value: value || '',
        language: 'plaintext',
        theme: monacoTheme,
        automaticLayout: true,
        minimap: { enabled: false },
        wordWrap: 'off',
        scrollBeyondLastColumn: 5
    }
}

async function ensureMonacoEditors() {
    const monaco = await loadMonaco()
    if (!monaco) return
    if (!lyricsMonacoEditor) {
        lyricsMonacoEditor = monaco.editor.create(
            document.getElementById('lyricsEditor'),
            buildMonacoOptions(pendingLyricsText)
        )
    }
    if (!translationMonacoEditor) {
        translationMonacoEditor = monaco.editor.create(
            document.getElementById('translationEditor'),
            buildMonacoOptions(pendingTranslationText)
        )
    }
    bindEditorValueProxy()
    if (monaco && monaco.editor) {
        monaco.editor.setTheme(monacoTheme)
    }
}

async function ensureLyricsSearchEditors() {
    const monaco = await loadMonaco()
    if (!monaco) return
    const lyricsEl = document.getElementById('lyricsSearchLyricsEditor')
    const translationEl = document.getElementById('lyricsSearchTranslationEditor')
    if (lyricsEl && !lyricsSearchMonacoEditor) {
        lyricsSearchMonacoEditor = monaco.editor.create(
            lyricsEl,
            buildMonacoOptions(pendingSearchLyricsText)
        )
    }
    if (translationEl && !translationSearchMonacoEditor) {
        translationSearchMonacoEditor = monaco.editor.create(
            translationEl,
            buildMonacoOptions(pendingSearchTranslationText)
        )
    }
    if (monaco && monaco.editor) {
        monaco.editor.setTheme(monacoTheme)
    }
    bindLyricsSearchPreviewApplyGate()
}

function bindLyricsSearchPreviewApplyGate() {
    if (lyricsSearchPreviewApplyGateBound) return
    if (!lyricsSearchMonacoEditor || !translationSearchMonacoEditor) return
    lyricsSearchPreviewApplyGateBound = true
    const scheduleRefresh = () => requestAnimationFrame(() => updateLyricsSearchActionButtons())
    lyricsSearchMonacoEditor.onDidChangeModelContent(scheduleRefresh)
    translationSearchMonacoEditor.onDidChangeModelContent(scheduleRefresh)
}

function setSearchLyricsContent(value) {
    pendingSearchLyricsText = value || ''
    if (lyricsSearchMonacoEditor) {
        lyricsSearchMonacoEditor.setValue(pendingSearchLyricsText)
    }
}

function setSearchTranslationContent(value) {
    pendingSearchTranslationText = value || ''
    if (translationSearchMonacoEditor) {
        translationSearchMonacoEditor.setValue(pendingSearchTranslationText)
    }
}

function getSearchLyricsContent() {
    if (lyricsSearchMonacoEditor) return lyricsSearchMonacoEditor.getValue()
    return pendingSearchLyricsText || ''
}

function getSearchTranslationContent() {
    if (translationSearchMonacoEditor) return translationSearchMonacoEditor.getValue()
    return pendingSearchTranslationText || ''
}

function hasLyricsSearchApplicablePreview() {
    return Boolean(getSearchLyricsContent().trim() || getSearchTranslationContent().trim())
}

function getLyricsContent() {
    if (lyricsMonacoEditor) return lyricsMonacoEditor.getValue()
    const el = document.getElementById('lyricsEditor')
    return el && 'value' in el ? el.value : (pendingLyricsText || '')
}

function setLyricsContent(value) {
    pendingLyricsText = value || ''
    if (lyricsMonacoEditor) {
        lyricsMonacoEditor.setValue(pendingLyricsText)
    } else {
        const el = document.getElementById('lyricsEditor')
        if (el && 'value' in el) {
            el.value = pendingLyricsText
        }
    }
}

function getTranslationContent() {
    if (translationMonacoEditor) return translationMonacoEditor.getValue()
    const el = document.getElementById('translationEditor')
    return el && 'value' in el ? el.value : (pendingTranslationText || '')
}

function setTranslationContent(value) {
    pendingTranslationText = value || ''
    if (translationMonacoEditor) {
        translationMonacoEditor.setValue(pendingTranslationText)
    } else {
        const el = document.getElementById('translationEditor')
        if (el && 'value' in el) {
            el.value = pendingTranslationText
        }
    }
}

function bindEditorValueProxy() {
    const lyricsEl = document.getElementById('lyricsEditor')
    if (lyricsEl && !lyricsEl.__valueProxy) {
        Object.defineProperty(lyricsEl, 'value', {
            get: () => getLyricsContent(),
            set: (v) => setLyricsContent(v)
        })
        lyricsEl.__valueProxy = true
    }
    const translationEl = document.getElementById('translationEditor')
    if (translationEl && !translationEl.__valueProxy) {
        Object.defineProperty(translationEl, 'value', {
            get: () => getTranslationContent(),
            set: (v) => setTranslationContent(v)
        })
        translationEl.__valueProxy = true
    }
}

function createTag(text, className) {
    const span = document.createElement('span')
    span.className = className
    span.textContent = text
    return span
}

function normalizeLyricsValue(value) {
    return String(value || '').trim()
}

function extractLyricsPathFromMeta(metaLyrics) {
    const raw = normalizeLyricsValue(metaLyrics)
    if (!raw) return ''
    const parts = raw.split('::')
    if (parts.length >= 4) {
        return normalizeLyricsValue(parts[1])
    }
    return raw
}

function hasLyricsSummary(summary) {
    const direct = normalizeLyricsValue(summary.lyricsPath)
    if (direct && direct !== '!') return true
    const fromMeta = extractLyricsPathFromMeta(summary.metaLyrics)
    return Boolean(fromMeta && fromMeta !== '!')
}

function getSummaryDisplayNameForList(summary) {
    return getSummaryDisplayName(summary)
}

function getSummaryTagSearchText(summary) {
    const tags = []
    if (!hasLyricsSummary(summary)) {
        tags.push(t('song.tag.instrumental'))
    }
    if (!deriveHasAudioFromSummary(summary)) {
        tags.push(t('song.tag.noAudio'))
    }
    if (summary.hasDuet) {
        tags.push(t('song.tag.duetLong'))
    }
    if (summary.hasBackgroundVocals) {
        tags.push(t('song.tag.backgroundVocals'))
    }
    return tags.join(' ')
}

function buildSongSummarySearchPool(summary) {
    const filename = String(summary && summary.filename ? summary.filename : '')
    const displayName = getSummaryDisplayNameForList(summary)
    const tagsText = getSummaryTagSearchText(summary)
    return `${displayName} ${filename} ${tagsText}`.toLowerCase()
}

function refreshSongSummarySearchCacheByFilename(filename) {
    if (!filename) return
    const summary = songSummaryCache.get(filename)
    if (!summary) return
    const sortName = getSummaryDisplayNameForList(summary).toLowerCase()
    const searchPool = buildSongSummarySearchPool(summary)
    songSortNameCache.set(filename, sortName)
    songSearchPoolCache.set(filename, searchPool)

    const cachedItem = songItemByFilename.get(filename)
    if (cachedItem) {
        cachedItem.dataset.sortName = sortName
        cachedItem.dataset.searchPool = searchPool
    }
}

function ensureSongItemForFilename(filename) {
    if (!filename) return null
    const cachedItem = songItemByFilename.get(filename)
    if (cachedItem) {
        return cachedItem
    }
    const summary = songSummaryCache.get(filename)
    if (!summary) {
        return null
    }
    const createdItem = createSongItem(summary)
    songItemByFilename.set(filename, createdItem)
    const sortName = String(songSortNameCache.get(filename) || '')
    const searchPool = String(songSearchPoolCache.get(filename) || '')
    if (sortName) {
        createdItem.dataset.sortName = sortName
    }
    if (searchPool) {
        createdItem.dataset.searchPool = searchPool
    }
    return createdItem
}

function buildSongTags(summary) {
    const container = document.createElement('div')
    container.className = 'song-tags'
    container.id = `tags-${summary.filename}`
    if (!hasLyricsSummary(summary)) {
        container.appendChild(createTag(t('song.tag.instrumental'), 'song-tag instrumental-tag'))
    }
    if (!deriveHasAudioFromSummary(summary)) {
        container.appendChild(createTag(t('song.tag.noAudio'), 'song-tag no-audio-tag'))
    }
    if (summary.hasDuet) {
        container.appendChild(createTag(t('song.tag.duet'), 'song-tag duet-tag'))
    }
    if (summary.hasBackgroundVocals) {
        container.appendChild(createTag(t('song.tag.backgroundVocals'), 'song-tag background-vocals-tag'))
    }
    return container
}

let albumCoverObserver = null
let albumCoverObserverSuspended = false
const videoCoverCache = new Map()

function refreshAlbumCoverObserverTargets() {
    if (!albumCoverObserver || albumCoverObserverSuspended) return
    const covers = document.querySelectorAll('#jsonList .album-cover[data-src], #jsonList .album-cover[data-video-src]')
    covers.forEach(cover => {
        if (!(cover instanceof HTMLElement)) return
        if (cover.getAttribute('src')) return
        albumCoverObserver.observe(cover)
    })
}

function setAlbumCoverObserverSuspended(suspended) {
    const next = Boolean(suspended)
    if (albumCoverObserverSuspended === next) return
    albumCoverObserverSuspended = next
    if (!albumCoverObserver) return
    if (next) {
        albumCoverObserver.disconnect()
        return
    }
    refreshAlbumCoverObserverTargets()
}

function stripQueryHash(value) {
    return String(value || '').split('?')[0].split('#')[0]
}

function resolveBackgroundVideoUrl(raw) {
    if (!raw || raw === '!') {
        return ''
    }
    const normalized = normalizeSongsUrl(raw)
    const checkTarget = stripQueryHash(normalized)
    if (!hasValidVideoExtension(checkTarget)) {
        return ''
    }
    return normalized
}

function resolveBackgroundImageUrl(raw) {
    if (!raw || raw === '!') {
        return ''
    }
    const normalized = normalizeSongsUrl(raw)
    const checkTarget = stripQueryHash(normalized)
    if (!hasValidImageExtension(checkTarget)) {
        return ''
    }
    return normalized
}

function captureVideoMiddleFrame(videoUrl) {
    if (videoCoverCache.has(videoUrl)) {
        return videoCoverCache.get(videoUrl)
    }

    const promise = new Promise(resolve => {
        const video = document.createElement('video')
        let settled = false

        const finalize = (dataUrl) => {
            if (settled) return
            settled = true
            try {
                video.pause()
                video.removeAttribute('src')
                video.load()
            } catch (error) {
                // Ignore cleanup errors
            }
            resolve(dataUrl || '')
        }

        const fail = () => finalize('')

        video.crossOrigin = 'anonymous'
        video.preload = 'metadata'
        video.muted = true
        video.playsInline = true
        video.addEventListener('error', fail, { once: true })

        video.addEventListener('loadedmetadata', () => {
            const duration = Number.isFinite(video.duration) ? video.duration : 0
            const targetTime = duration > 0 ? duration / 2 : 0
            try {
                video.currentTime = targetTime
            } catch (error) {
                fail()
            }
        }, { once: true })

        video.addEventListener('seeked', () => {
            const width = video.videoWidth || 0
            const height = video.videoHeight || 0
            if (!width || !height) {
                fail()
                return
            }
            const canvas = document.createElement('canvas')
            canvas.width = width
            canvas.height = height
            const ctx = canvas.getContext('2d')
            if (!ctx) {
                fail()
                return
            }
            try {
                ctx.drawImage(video, 0, 0, width, height)
                const dataUrl = canvas.toDataURL('image/jpeg', 0.92)
                finalize(dataUrl)
            } catch (error) {
                fail()
            }
        })

        video.src = videoUrl
    }).then(dataUrl => {
        if (!dataUrl) {
            videoCoverCache.delete(videoUrl)
        }
        return dataUrl
    })

    videoCoverCache.set(videoUrl, promise)
    return promise
}

function ensureAlbumCoverObserver() {
    if (albumCoverObserver || !('IntersectionObserver' in window)) {
        return
    }
    albumCoverObserver = new IntersectionObserver(entries => {
        if (albumCoverObserverSuspended) return
        entries.forEach(entry => {
            if (!entry.isIntersecting) return
            const cover = entry.target
            const dataSrc = cover.getAttribute('data-src')
            const dataVideo = cover.getAttribute('data-video-src')
            if (dataVideo && !cover.src) {
                captureVideoMiddleFrame(dataVideo).then(dataUrl => {
                    if (!dataUrl) {
                        removeSongItemCover(cover)
                        return
                    }
                    cover.src = dataUrl
                })
            } else if (dataSrc && !cover.src) {
                cover.src = dataSrc
            }
            albumCoverObserver.unobserve(cover)
        })
    }, { rootMargin: '150px 0px' })
}

function createAlbumCoverImage(src = '') {
    const img = document.createElement('img')
    img.className = 'album-cover'
    img.alt = 'album cover'
    img.loading = 'lazy'
    if (src) {
        img.src = src
    }
    return img
}

function removeSongItemCover(mediaEl) {
    if (!mediaEl) return
    const coverSlot = mediaEl.closest('.album-cover-slot')
    const container = mediaEl.closest('.file-name')
    if (coverSlot && coverSlot.parentElement) {
        coverSlot.parentElement.removeChild(coverSlot)
    }
    if (container) {
        container.classList.remove('has-cover')
        container.classList.remove('has-dynamic-video-cover')
    }
}

function replaceCoverWithVideo(coverEl, videoSrc, posterSrc = '', options = {}) {
    const playImmediately = Boolean(options && options.playImmediately)
    if (!coverEl || !videoSrc) return
    const coverSlot = coverEl.closest('.album-cover-slot')
    const container = coverEl.closest('.file-name')
    if (!coverSlot || !container) return

    container.classList.add('has-cover')
    container.classList.add('has-dynamic-video-cover')

    const videoEl = document.createElement('video')
    videoEl.className = 'album-cover album-cover-video'
    videoEl.muted = true
    videoEl.loop = true
    videoEl.autoplay = false
    videoEl.playsInline = true
    videoEl.preload = 'metadata'
    if (posterSrc) {
        videoEl.poster = posterSrc
    }
    videoEl.src = videoSrc

    const playCoverVideo = () => {
        if (viewportResizing) {
            return
        }
        videoEl.play().catch(() => {
            // Ignore autoplay/gesture restrictions
        })
    }

    const pauseCoverVideo = () => {
        videoEl.pause()
    }

    const hoverHost = container.closest('.json-item') || coverSlot
    if (hoverHost) {
        hoverHost.addEventListener('mouseenter', playCoverVideo)
        hoverHost.addEventListener('mouseleave', pauseCoverVideo)
    }

    videoEl.addEventListener('error', () => {
        container.classList.remove('has-dynamic-video-cover')
        if (posterSrc) {
            const fallbackImg = createAlbumCoverImage(posterSrc)
            fallbackImg.addEventListener('error', () => removeSongItemCover(fallbackImg), { once: true })
            coverSlot.replaceChildren(fallbackImg)
            return
        }
        removeSongItemCover(videoEl)
    })

    coverSlot.replaceChildren(videoEl)
    if (playImmediately) {
        playCoverVideo()
    }
}

function getPathFromUrl(url) {
    if (!url) return ''
    const mediaFile = typeof parseMediaAudioFileParam === 'function'
        ? parseMediaAudioFileParam(url)
        : null
    if (mediaFile) {
        return mediaFile
    }
    const path = url.split('?')[0].split('#')[0]
    return path
}

function isVideoFile(filename) {
    if (!filename) return false
    const actualPath = filename.startsWith('http') ? getPathFromUrl(filename) : filename
    const videoExtensions = ['.mp4', '.webm', '.ogg', '.m4v', '.mov']
    const lower = actualPath.toLowerCase()
    return videoExtensions.some(ext => lower.endsWith(ext))
}

function isAnimatedImage(filename) {
    if (!filename) return false
    const actualPath = filename.startsWith('http') ? getPathFromUrl(filename) : filename
    const animatedExtensions = ['.gif', '.webp', '.apng']
    const lower = actualPath.toLowerCase()
    return animatedExtensions.some(ext => lower.endsWith(ext))
}

const _CARD_DROP_IMAGE_EXTS = ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.apng']
const _CARD_DROP_VIDEO_EXTS = ['.mp4', '.webm', '.ogg', '.m4v', '.mov']
const _CARD_DROP_AUDIO_EXTS = ['.mp3', '.flac', '.wav', '.m4a', '.aac', '.opus', '.oga', '.wma', '.ape', '.dff', '.dsf', '.mpc', '.mid', '.midi', '.aiff', '.aif', '.caf']

function getDroppedFile(dataTransfer) {
    if (!dataTransfer) return null
    const files = dataTransfer.files
    if (files && files.length > 0 && files[0]) {
        return files[0]
    }
    const items = dataTransfer.items
    if (!items) return null
    for (let i = 0; i < items.length; i++) {
        const item = items[i]
        if (item && item.kind === 'file') {
            const file = item.getAsFile()
            if (file) return file
        }
    }
    return null
}

function getDroppedExternalUrlHint(dataTransfer) {
    if (!dataTransfer) return null
    const types = dataTransfer.types ? Array.from(dataTransfer.types) : []
    if (types.includes('DownloadURL')) {
        try {
            const downloadUrl = dataTransfer.getData('DownloadURL')
            if (downloadUrl) {
                const firstColon = downloadUrl.indexOf(':')
                const secondColon = firstColon >= 0 ? downloadUrl.indexOf(':', firstColon + 1) : -1
                if (secondColon >= 0) {
                    const mime = downloadUrl.slice(0, firstColon) || ''
                    const filename = downloadUrl.slice(firstColon + 1, secondColon) || ''
                    const url = downloadUrl.slice(secondColon + 1)
                    if (url && /^https?:\/\//i.test(url)) {
                        return { mime, filename, url }
                    }
                }
            }
        } catch (_) { /* ignore */ }
    }
    for (const textType of ['text/uri-list', 'text/plain']) {
        try {
            const text = dataTransfer.getData(textType)
            if (!text) continue
            const urlMatch = text.match(/https?:\/\/[^\s]+/i)
            if (urlMatch) return { url: urlMatch[0] }
        } catch (_) { /* ignore */ }
    }
    return null
}

function resolveDroppedUploadFile(dataTransfer) {
    const file = getDroppedFile(dataTransfer)
    const urlHint = getDroppedExternalUrlHint(dataTransfer)
    return { file, urlHint }
}

function classifySongCardDropFile(file) {
    if (!file || !file.name) return null
    const mime = (file.type || '').toLowerCase()
    if (mime.startsWith('audio/') || mime.startsWith('video/')) return 'music'
    if (mime.startsWith('image/')) return 'album'

    const lower = file.name.toLowerCase()
    const musicExt = [..._CARD_DROP_VIDEO_EXTS, ..._CARD_DROP_AUDIO_EXTS]
    if (musicExt.some((ext) => lower.endsWith(ext))) return 'music'
    if (_CARD_DROP_IMAGE_EXTS.some((ext) => lower.endsWith(ext))) return 'album'
    return null
}

function attachSongCardQuickFileDrop(li) {
    if (!li || !li.dataset.filename) return

    const cap = true

    li.addEventListener('dragenter', (e) => {
        e.preventDefault()
        li.classList.add('json-item--file-dragover')
    }, cap)

    li.addEventListener('dragleave', (e) => {
        if (!li.contains(e.relatedTarget)) {
            li.classList.remove('json-item--file-dragover')
        }
    }, cap)

    li.addEventListener('dragover', (e) => {
        e.preventDefault()
        e.dataTransfer.dropEffect = 'copy'
    }, cap)

    li.addEventListener('drop', (e) => {
        e.preventDefault()
        e.stopPropagation()
        li.classList.remove('json-item--file-dragover')

        if (currentWriteLockEnabled) {
            alert(t('pathDropzone.writeLocked'))
            return
        }

        const jsonFile = li.dataset.filename
        if (!jsonFile) return

        const { file, urlHint } = resolveDroppedUploadFile(e.dataTransfer)
        const urlHintIsMusic = urlHint && isLikelyMusicMediaUrlHint(urlHint)

        if (!file && urlHintIsMusic) {
            if (typeof uploadMusicFromDroppedUrl !== 'function') return
            currentMusicJsonFile = jsonFile
            uploadMusicFromDroppedUrl(urlHint)
            return
        }
        if (!file) return

        const kind = classifySongCardDropFile(file)

        if (kind === 'music') {
            if (typeof uploadMusicFile !== 'function') return
            currentMusicJsonFile = jsonFile
            uploadMusicFile(file, { fallbackUrlHint: urlHint })
            return
        }

        if (kind === 'album') {
            if (typeof handleImageUpload !== 'function') return
            if (typeof isAcceptableImageUploadFile === 'function') {
                if (!isAcceptableImageUploadFile(file)) {
                    alert(t('file.uploadImage'))
                    return
                }
            } else if (!file.type.startsWith('image/')) {
                const lower = file.name.toLowerCase()
                if (!_CARD_DROP_IMAGE_EXTS.some((ext) => lower.endsWith(ext))) {
                    alert(t('file.uploadImage'))
                    return
                }
            }
            currentImageJsonFile = jsonFile
            handleImageUpload(file, 'album')
            return
        }

        if (urlHintIsMusic) {
            if (typeof uploadMusicFromDroppedUrl !== 'function') return
            currentMusicJsonFile = jsonFile
            uploadMusicFromDroppedUrl(urlHint)
            return
        }

        alert(t('song.cardDropUnsupported'))
    }, cap)
}

function createSongItem(summary) {
    const li = document.createElement('li')
    li.className = 'json-item'
    li.dataset.mtime = summary.mtime || 0
    li.dataset.filename = summary.filename || ''

    const header = document.createElement('div')
    header.className = 'json-item-header'

    const fileNameDiv = document.createElement('div')
    fileNameDiv.className = 'file-name'
    const displayName = getSummaryDisplayName(summary)
    const normalizedDisplayName = displayName.toLowerCase()
    const fileNameText = document.createElement('span')
    fileNameText.className = 'file-name-text'
    fileNameText.textContent = displayName
    fileNameDiv.appendChild(fileNameText)

    // 优先级：dynamicCoverPoster > dynamicCoverSrc(如果是动图) > albumImgSrc > backgroundImage
    let coverUrl = ''
    let posterUrl = ''
    let dynamicVideoUrl = ''

    if (summary.dynamicCoverPoster) {
        posterUrl = resolveCoverUrl(summary.dynamicCoverPoster)
    }

    if (summary.dynamicCoverSrc) {
        if (isVideoFile(summary.dynamicCoverSrc)) {
            dynamicVideoUrl = resolveCoverUrl(summary.dynamicCoverSrc)
        } else if (isAnimatedImage(summary.dynamicCoverSrc)) {
            coverUrl = resolveCoverUrl(summary.dynamicCoverSrc)
        }
    }

    if (!coverUrl && !posterUrl && !dynamicVideoUrl) {
        coverUrl = summary.albumImgSrc ? resolveCoverUrl(summary.albumImgSrc) : ''
    }

    const backgroundImageUrl = (coverUrl || posterUrl || dynamicVideoUrl) ? '' : resolveBackgroundImageUrl(summary.backgroundImage || '')
    const backgroundVideoUrl = (coverUrl || posterUrl || dynamicVideoUrl || backgroundImageUrl)
        ? ''
        : resolveBackgroundVideoUrl(summary.backgroundImage || '')

    const finalCoverUrl = posterUrl || coverUrl || backgroundImageUrl

    if (finalCoverUrl || backgroundVideoUrl || dynamicVideoUrl) {
        fileNameDiv.classList.add('has-cover')
        const coverSlot = document.createElement('div')
        coverSlot.className = 'album-cover-slot'

        // 创建占位符 img 元素，用于懒加载
        const coverImg = createAlbumCoverImage()

        if (dynamicVideoUrl) {
            // 动态封面视频：默认仅显示静态封面，交互后再切换为视频
            coverImg.setAttribute('data-dynamic-video-src', dynamicVideoUrl)
            const posterCandidate = posterUrl || (summary.albumImgSrc ? resolveCoverUrl(summary.albumImgSrc) : '')
            if (posterUrl) {
                coverImg.setAttribute('data-poster-src', posterUrl)
            } else if (posterCandidate) {
                coverImg.setAttribute('data-poster-src', posterCandidate)
            }
            if (posterCandidate) {
                coverImg.src = posterCandidate
            } else {
                coverImg.setAttribute('data-video-src', dynamicVideoUrl)
            }
            coverImg.addEventListener('error', () => {
                coverImg.removeAttribute('src')
            })
            const activateDynamicCover = () => {
                replaceCoverWithVideo(
                    coverImg,
                    dynamicVideoUrl,
                    coverImg.getAttribute('data-poster-src') || '',
                    { playImmediately: true }
                )
            }
            coverImg.addEventListener('pointerenter', activateDynamicCover, { once: true })
            coverImg.addEventListener('click', activateDynamicCover, { once: true })
        } else if (finalCoverUrl) {
            coverImg.setAttribute('data-src', finalCoverUrl)
            coverImg.addEventListener('error', () => removeSongItemCover(coverImg), { once: true })
        } else {
            coverImg.setAttribute('data-video-src', backgroundVideoUrl)
            coverImg.addEventListener('error', () => removeSongItemCover(coverImg), { once: true })
        }

        coverSlot.appendChild(coverImg)
        fileNameDiv.appendChild(coverSlot)
        ensureAlbumCoverObserver()
        const shouldObserveCover = Boolean(coverImg.getAttribute('data-src') || coverImg.getAttribute('data-video-src'))
        if (shouldObserveCover && albumCoverObserver && !albumCoverObserverSuspended) {
            albumCoverObserver.observe(coverImg)
        } else if (finalCoverUrl) {
            coverImg.src = finalCoverUrl
        } else if (backgroundVideoUrl) {
            captureVideoMiddleFrame(backgroundVideoUrl).then(dataUrl => {
                if (!dataUrl) {
                    removeSongItemCover(coverImg)
                    return
                }
                coverImg.src = dataUrl
            })
        }
    }
    fileNameText.addEventListener('click', () => {
        const selection = window.getSelection()
        if (selection && !selection.isCollapsed) {
            return
        }
        copyFileNameText(fileNameText)
    })

    header.appendChild(fileNameDiv)
    const songTags = buildSongTags(summary)
    header.appendChild(songTags)
    li.appendChild(header)

    const actionsWrapper = document.createElement('div')
    actionsWrapper.className = 'json-item-actions'

    const styleButtons = document.createElement('div')
    styleButtons.className = 'style-buttons'

    const fsBtn = document.createElement('button')
    fsBtn.className = 'style-button fs-style'
    fsBtn.textContent = t('song.fullscreenStyle')
    fsBtn.addEventListener('click', () => openFamyliamCloud(summary.filename, 'fs'))

    const fslrBtn = document.createElement('button')
    fslrBtn.className = 'style-button fslr-style'
    fslrBtn.textContent = t('song.fullscreenDuetStyle')
    fslrBtn.addEventListener('click', () => openFamyliamCloud(summary.filename, 'fslr'))

    const amllBtn = document.createElement('button')
    amllBtn.className = 'style-button am-style'
    amllBtn.textContent = t('song.amStyle')
    amllBtn.addEventListener('click', () => openFamyliamCloud(summary.filename, 'amll'))

    const brightBtn = document.createElement('button')
    brightBtn.className = 'action-button'
    brightBtn.textContent = t('btn.brighten')
    brightBtn.addEventListener('click', () => openLyricsAnimate(summary.filename, '亮起'))

    const C_okBtn = document.createElement('button')
    C_okBtn.className = 'action-button'
    C_okBtn.textContent = 'C_ok'
    C_okBtn.addEventListener('click', () => openLyricsAnimate(summary.filename, 'C_ok'))

    //const junpBtn = document.createElement('button')
    //junpBtn.className = 'action-button'
    //junpBtn.textContent = 'JUNP'
    //junpBtn.addEventListener('click', () => openLyricsAnimate(summary.filename, 'JUNP'))

    styleButtons.appendChild(fsBtn)
    styleButtons.appendChild(fslrBtn)
    styleButtons.appendChild(amllBtn)
    styleButtons.appendChild(brightBtn)
    styleButtons.appendChild(C_okBtn)
    //styleButtons.appendChild(junpBtn)

    const fileActions = document.createElement('div')
    fileActions.className = 'file-actions'

    const editLyricsBtn = document.createElement('button')
    editLyricsBtn.className = 'action-button'
    editLyricsBtn.textContent = t('song.editLyrics')
    editLyricsBtn.setAttribute('data-write-lock', '')
    editLyricsBtn.addEventListener('click', () => {
        editLyrics(summary.lyricsPath || '', summary.translationPath || '', 0, summary.filename)
    })

    const editMusicBtn = document.createElement('button')
    editMusicBtn.className = 'action-button'
    editMusicBtn.textContent = t('song.editMusicPath')
    editMusicBtn.setAttribute('data-write-lock', '')
    editMusicBtn.addEventListener('click', () => editMusicPath(summary.song || '', summary.filename))

    const editImageBtn = document.createElement('button')
    editImageBtn.className = 'action-button'
    editImageBtn.textContent = t('song.editImagePath')
    editImageBtn.setAttribute('data-write-lock', '')
    editImageBtn.addEventListener('click', () => editImagePath(summary.albumImgSrc || '', summary.filename))

    const exportBtn = document.createElement('button')
    exportBtn.className = 'action-button'
    exportBtn.textContent = t('song.export')
    exportBtn.setAttribute('data-write-lock', '')
    exportBtn.addEventListener('click', () => exportStatic(summary.filename))

    const renameBtn = document.createElement('button')
    renameBtn.className = 'action-button'
    renameBtn.textContent = t('song.rename')
    renameBtn.setAttribute('data-write-lock', '')
    const artistsString = Array.isArray(summary.artists) ? summary.artists.join(',') : ''
    renameBtn.addEventListener('click', () => showRenameModal(summary.filename, summary.title || '', artistsString))

    const deleteBtn = document.createElement('button')
    deleteBtn.className = 'action-button action-button-danger'
    deleteBtn.textContent = t('song.delete')
    deleteBtn.setAttribute('data-write-lock', '')
    deleteBtn.addEventListener('click', () => {
        if (confirm(t('file.deleteConfirm'))) {
            deleteJson(summary.filename)
        }
    })

    fileActions.appendChild(editLyricsBtn)
    fileActions.appendChild(editMusicBtn)
    fileActions.appendChild(editImageBtn)
    fileActions.appendChild(exportBtn)
    fileActions.appendChild(renameBtn)
    fileActions.appendChild(deleteBtn)

    actionsWrapper.appendChild(styleButtons)
    actionsWrapper.appendChild(fileActions)

    const preview = document.createElement('div')
    preview.className = 'file-preview'
    preview.id = `preview-${summary.filename}`
    const previewContent = document.createElement('div')
    previewContent.className = 'preview-content'
    preview.appendChild(previewContent)

    li.appendChild(actionsWrapper)
    li.appendChild(preview)
    li.dataset.sortName = normalizedDisplayName
    refreshSongItemSearchCache(li)
    attachSongCardQuickFileDrop(li)
    return li
}

function renderSongList(summaries, options = {}) {
    const append = Boolean(options.append)
    if (!append) {
        songSummaryCache.clear()
        songItemByFilename.clear()
        songSearchPoolCache.clear()
        songSortNameCache.clear()
        allSongFilenames = []
        mainListVisibleFilenames = []
        mainListRenderCursor = 0
        resizePausedCoverVideos = []
        if (jsonList) {
            jsonList.innerHTML = ''
        }
    }

    summaries.forEach(summary => {
        const filename = String(summary && summary.filename ? summary.filename : '')
        if (!filename) return
        if (append && allSongFilenames.includes(filename)) {
            songSummaryCache.set(filename, summary)
            refreshSongSummarySearchCacheByFilename(filename)
            return
        }
        songSummaryCache.set(filename, summary)
        allSongFilenames.push(filename)
        refreshSongSummarySearchCacheByFilename(filename)
    })

    applySearch()
}

function captureBrowseLibrarySnapshot() {
    browseLibrarySnapshot = {
        allSongFilenames: [...allSongFilenames],
        songSummaryCurrentPage,
        songSummaryHasMore,
        songSummaryTotal,
        songSummaryPageSize,
    }
}

function restoreBrowseLibraryFromSnapshot() {
    if (!browseLibrarySnapshot) return
    allSongFilenames = [...browseLibrarySnapshot.allSongFilenames]
    songSummaryCurrentPage = browseLibrarySnapshot.songSummaryCurrentPage
    songSummaryHasMore = browseLibrarySnapshot.songSummaryHasMore
    songSummaryTotal = browseLibrarySnapshot.songSummaryTotal
    songSummaryPageSize = browseLibrarySnapshot.songSummaryPageSize
    browseLibrarySnapshot = null
}

function mergeSummariesIntoLibraryCache(summaries) {
    summaries.forEach(summary => {
        const filename = String(summary && summary.filename ? summary.filename : '')
        if (!filename) return
        songSummaryCache.set(filename, normalizeSongSummaryAudio(summary))
        refreshSongSummarySearchCacheByFilename(filename)
    })
}

function buildSongSearchQueryString(rawText, page, fuzzy) {
    const sortType = currentSort.type === 'name' ? 'name' : 'time'
    const sortAsc = currentSort.asc ? '1' : '0'
    const qs = new URLSearchParams({
        q: rawText,
        page: String(page),
        pageSize: String(songSummaryPageSize),
        fuzzy: fuzzy ? '1' : '0',
        sortType,
        sortAsc,
    })
    return qs.toString()
}

async function executeLibrarySearch() {
    if (!hasMainListSearchKeywords()) return
    const rawText = String(searchBox && searchBox.value ? searchBox.value : '').trim()
    const isFuzzy = Boolean(fuzzySearchToggle && fuzzySearchToggle.checked)
    const gen = ++searchResultGeneration
    searchIsLoading = true
    updateSongLibraryProgressStatus()
    try {
        const qs = buildSongSearchQueryString(rawText, 1, isFuzzy)
        const res = await fetch('/songs/search?' + qs, { cache: 'no-store' })
        const data = await res.json()
        if (gen !== searchResultGeneration) return
        if (libraryMode !== 'search' || !hasMainListSearchKeywords()) return
        if (data.status !== 'success') {
            throw new Error(data.message || t('batch.loadFailed'))
        }
        searchPage = typeof data.page === 'number' ? data.page : 1
        searchTotal = typeof data.total === 'number' ? data.total : 0
        searchHasMore = Boolean(data.hasMore)
        mergeSummariesIntoLibraryCache(data.songs || [])
        const files = (data.songs || []).map(s => String(s && s.filename ? s.filename : '')).filter(Boolean)
        allSongFilenames = files
        songItemByFilename.clear()
        if (jsonList) {
            jsonList.innerHTML = ''
        }
        mainListRenderCursor = 0
        resetAndRenderMainSongList(files)
    } catch (error) {
        if (gen === searchResultGeneration && libraryMode === 'search' && hasMainListSearchKeywords()) {
            console.error('搜索歌曲失败', error)
            setListStatus(t('batch.loadFailed') + (error.message || error), true)
        }
    } finally {
        if (gen === searchResultGeneration && libraryMode === 'search') {
            searchIsLoading = false
            updateSongLibraryProgressStatus()
        }
    }
}

async function loadMoreSongSearch() {
    if (libraryMode !== 'search' || !searchHasMore || searchIsLoading) return
    if (!hasMainListSearchKeywords()) return
    const requestGen = searchResultGeneration
    const rawText = String(searchBox && searchBox.value ? searchBox.value : '').trim()
    const isFuzzy = Boolean(fuzzySearchToggle && fuzzySearchToggle.checked)
    const next = searchPage + 1
    searchIsLoading = true
    updateSongLibraryProgressStatus()
    try {
        const qs = buildSongSearchQueryString(rawText, next, isFuzzy)
        const res = await fetch('/songs/search?' + qs, { cache: 'no-store' })
        const data = await res.json()
        if (libraryMode !== 'search' || requestGen !== searchResultGeneration || !hasMainListSearchKeywords()) return
        if (data.status !== 'success') {
            throw new Error(data.message || t('batch.loadFailed'))
        }
        searchPage = typeof data.page === 'number' ? data.page : next
        searchHasMore = Boolean(data.hasMore)
        if (typeof data.total === 'number') {
            searchTotal = data.total
        }
        const songs = data.songs || []
        mergeSummariesIntoLibraryCache(songs)
        const newFns = []
        songs.forEach(s => {
            const fn = String(s && s.filename ? s.filename : '')
            if (!fn || mainListVisibleFilenames.includes(fn)) return
            newFns.push(fn)
        })
        newFns.forEach(fn => {
            if (!allSongFilenames.includes(fn)) {
                allSongFilenames.push(fn)
            }
            mainListVisibleFilenames.push(fn)
        })
        while (mainListRenderCursor < mainListVisibleFilenames.length) {
            appendSongListBatch()
        }
        void ensureMainListViewportFilled()
    } catch (error) {
        if (libraryMode === 'search' && requestGen === searchResultGeneration && hasMainListSearchKeywords()) {
            console.error('加载更多搜索结果失败', error)
            setListStatus(t('batch.loadFailed') + (error.message || error), true)
        }
    } finally {
        if (requestGen === searchResultGeneration && libraryMode === 'search') {
            searchIsLoading = false
            updateSongLibraryProgressStatus()
        }
    }
}

async function loadSongSummaries() {
    searchResultGeneration += 1
    libraryMode = 'browse'
    browseLibrarySnapshot = null
    searchPage = 1
    searchTotal = 0
    searchHasMore = false
    searchIsLoading = false
    songSummaryCurrentPage = 1
    songSummaryHasMore = false
    songSummaryTotal = 0
    songSummaryPageSize = SONG_SUMMARY_PAGE_SIZE
    setListStatus(t('batch.loadingSongs'), false, true)
    toggleLoader(true, 'Loading')
    try {
        const qs = new URLSearchParams({ page: '1', pageSize: String(songSummaryPageSize) })
        const res = await fetch('/songs/summary?' + qs.toString(), { cache: 'no-store' })
        const data = await res.json()
        if (data.status !== 'success') {
            throw new Error(data.message || t('batch.loadFailed'))
        }
        songSummaryTotal = typeof data.total === 'number' ? data.total : 0
        songSummaryHasMore = Boolean(data.hasMore)
        songSummaryCurrentPage = typeof data.page === 'number' ? data.page : 1
        renderSongList(data.songs || [], { append: false })
        if (!songSummaryTotal) {
            setListStatus('')
        }
    } catch (error) {
        console.error('加载歌曲摘要失败', error)
        setListStatus(t('batch.loadFailed') + (error.message || error), true)
    } finally {
        toggleLoader(false)
    }
}

async function loadMoreSongSummaries() {
    if (libraryMode === 'search') return
    if (!songSummaryHasMore || songSummaryIsLoadingMore) return
    songSummaryIsLoadingMore = true
    try {
        const next = songSummaryCurrentPage + 1
        const qs = new URLSearchParams({ page: String(next), pageSize: String(songSummaryPageSize) })
        const res = await fetch('/songs/summary?' + qs.toString(), { cache: 'no-store' })
        const data = await res.json()
        if (data.status !== 'success') {
            throw new Error(data.message || t('batch.loadFailed'))
        }
        songSummaryHasMore = Boolean(data.hasMore)
        songSummaryCurrentPage = typeof data.page === 'number' ? data.page : next
        if (typeof data.total === 'number') {
            songSummaryTotal = data.total
        }
        renderSongList(data.songs || [], { append: true })
    } catch (error) {
        console.error('加载更多歌曲摘要失败', error)
        setListStatus(t('batch.loadFailed') + (error.message || error), true)
    } finally {
        songSummaryIsLoadingMore = false
    }
}

function selectVisibleSongsForBatch() {
    const visibleFilenames = Array.isArray(mainListVisibleFilenames) ? mainListVisibleFilenames : []
    visibleFilenames.forEach(filename => {
        if (filename) selectedSongs.add(filename)
    })
    
    renderBatchSelectionCount()
    renderBatchWorkbenchSongList()
}

function clearBatchSelectedSongs() {
    selectedSongs.clear()
    
    renderBatchSelectionCount()
    renderBatchWorkbenchSongList()
    updateBatchTranslateStatus([])
}

async function fetchLyricsTextByPath(pathValue) {
    if (!pathValue) return ''
    const response = await fetch('/get_lyrics', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: pathValue })
    })
    const data = await response.json()
    if (data.status !== 'success') {
        throw new Error(data.message || '读取歌词失败')
    }
    return data.content || ''
}

async function preprocessBatchLyrics(summary, rawContent) {
    const lyricsPathRaw = summary && summary.lyricsPath ? summary.lyricsPath : ''
    const lyricsPath = stripSongsPrefix(lyricsPathRaw).trim()
    const extension = (lyricsPath.match(/\.[^.]+$/) || [])[0]?.toLowerCase() || ''
    const detectedExt = detectLyricsExtension(rawContent)
    if (extension === '.ttml' && detectedExt === '.ttml') {
        const conversionRes = await fetch('/convert_ttml_by_path', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: lyricsPath })
        })
        const conversionData = await conversionRes.json()
        if (!conversionData || conversionData.status !== 'success') {
            throw new Error(conversionData?.message || 'TTML 转换失败')
        }
        if (!conversionData.lyricPath) {
            throw new Error('TTML 转换成功但未返回LYS路径')
        }
        const lyricRes = await fetch('/get_lyrics', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: conversionData.lyricPath })
        })
        const lyricData = await lyricRes.json()
        if (!lyricData || lyricData.status !== 'success') {
            throw new Error(lyricData?.message || '读取转换后的LYS失败')
        }
        return lyricData.content || ''
    }
    return rawContent
}

function deriveBatchTranslationPath(summary) {
    const existingPath = (summary.translationPath && summary.translationPath !== '!')
        ? stripSongsPrefix(summary.translationPath).trim()
        : ''
    if (existingPath) {
        return existingPath
    }
    const lyricsPath = (summary.lyricsPath && summary.lyricsPath !== '!')
        ? stripSongsPrefix(summary.lyricsPath).trim()
        : ''
    if (!lyricsPath) {
        return ''
    }
    const base = lyricsPath.replace(/\.[^/.]+$/, '')
    return `${base}_trans.lrc`
}

async function ensureBatchTranslationPath(summary, targetPath) {
    const response = await fetch('/update_file_path', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            jsonFile: summary.filename,
            fileType: 'lyrics',
            newPath: targetPath,
            index: 1,
            clear: !targetPath
        })
    })
    const data = await response.json()
    if (!data || data.status !== 'success') {
        throw new Error(data?.message || '更新翻译路径失败')
    }
}

async function saveBatchTranslationToDisk(summary, payload) {
    const translations = Array.isArray(payload.translations) ? payload.translations : []
    if (translations.length === 0) {
        throw new Error(t('batch.translationEmpty'))
    }
    const targetPath = deriveBatchTranslationPath(summary)
    if (!targetPath) {
        throw new Error('缺少可用的翻译路径')
    }
    await ensureBatchTranslationPath(summary, targetPath)
    const saveRes = await fetch('/save_lyrics', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            path: normalizeSongsUrl(targetPath),
            content: translations.join('\n'),
            jsonFile: summary.filename || undefined
        })
    })
    const saveData = await saveRes.json()
    if (!saveData || (saveData.status !== 'success' && saveData.status !== 'warning')) {
        throw new Error(saveData?.message || '保存翻译文件失败')
    }
    summary.translationPath = targetPath
    songSummaryCache.set(summary.filename, summary)
    return targetPath
}

async function startBatchTranslate() {
    let stoppedByUser = false
    let requestFailed = false
    const markOutstandingAsFailed = (reason) => {
        const statusReason = reason || t('batch.requestError')
        Array.from(batchWorkbenchState.results.values()).forEach(resultEntry => {
            if (!resultEntry) return
            if (BATCH_TERMINAL_STATUS_KEYS.has(resultEntry.statusKey)) return
            resultEntry.statusKey = 'failed'
            resultEntry.statusText = statusReason
            resultEntry.error = statusReason
            markBatchResultDirty(resultEntry.id, { detail: resultEntry.id === batchWorkbenchState.activeResultId })
        })
        scheduleBatchTranslateStatusRender([statusReason], true, { force: true })
    }
    const markOutstandingAsStopped = () => {
        Array.from(batchWorkbenchState.results.values()).forEach(resultEntry => {
            if (!resultEntry) return
            if (BATCH_TERMINAL_STATUS_KEYS.has(resultEntry.statusKey)) return
            resultEntry.statusKey = 'stopped'
            resultEntry.statusText = t('resultStatus.stopped')
            markBatchResultDirty(resultEntry.id, { detail: resultEntry.id === batchWorkbenchState.activeResultId })
        })
    }
    try {
        if (batchWorkbenchState.running) {
            alert(t('alert.batchRunning'))
            return
        }
        if (selectedSongs.size === 0) {
            alert(t('alert.selectAtLeastOne'))
            return
        }
        const controls = getBatchWorkbenchControls()
        const settings = getBatchWorkbenchSettingsFromUI()

        const provider = settings.provider || 'deepseek'
        const baseUrl = settings.baseUrl || 'https://api.deepseek.com'
        const model = settings.model || 'deepseek-reasoner'
        const expectReasoning = Boolean(settings.expectReasoning)
        const compatMode = Boolean(settings.compatMode)
        const stripBrackets = Boolean(settings.stripBrackets)
        const experimentalFullLineBracketStrip = Boolean(settings.experimentalFullLineBracketStrip)
        const experimentalBracketLineAsSubline = Boolean(settings.experimentalBracketLineAsSubline)
        const thinkingEnabled = Boolean(settings.thinkingEnabled)

        const basePrompt = settings.systemPrompt || ''
        const defaults = await fetchDefaultBatchPromptParts()
        const fixedPrompt = String(defaults.fixedPrompt || defaultBatchFixedPromptCache || '').trim()
        const batchExtraPrompt = (settings.extraPrompt || '').trim()
        const finalSystemPrompt = [basePrompt, fixedPrompt, batchExtraPrompt].filter(Boolean).join('\n\n')

        const autoSave = Boolean(settings.autoSave)
        const onlyEmpty = Boolean(settings.onlyEmpty)
        const alwaysOverride = Boolean(settings.alwaysOverride)
        if (alwaysOverride && controls.onlyEmptyCheck) {
            controls.onlyEmptyCheck.checked = false
        }

        batchWorkbenchState.running = true
        batchWorkbenchState.stopRequested = false
        batchWorkbenchState.finalResult = 'none'
        batchWorkbenchState.lifecyclePhase = 'preparing'
        batchWorkbenchState.runState = 'running'
        setAlbumCoverObserverSuspended(true)
        batchWorkbenchState.runPhase = t('batch.preprocessStatus')
        batchTranslateAbortController = new AbortController()
        batchWorkbenchState.results.clear()
        batchWorkbenchState.resultCardMap.clear()
        batchWorkbenchState.dirtyResultIds.clear()
        batchWorkbenchState.resultStructureDirty = true
        batchWorkbenchState.detailDirty = true
        batchWorkbenchState.resultCardPlaceholder = null
        batchWorkbenchState.lastRenderedActiveResultId = ''
        batchWorkbenchState.runtimeStats = {
            success: 0,
            failed: 0,
            skipped: 0,
            processing: 0,
            completed: 0,
            total: 0
        }
        batchWorkbenchState.diagnostics.streamChunkCount = 0
        batchWorkbenchState.diagnostics.uiRefreshCount = 0
        batchWorkbenchState.diagnostics.partialUpdateCount = 0
        batchWorkbenchState.diagnostics.fullRefreshCount = 0
        batchWorkbenchState.activeResultId = ''
        scheduleBatchWorkbenchResultRender({ detail: true, full: true })
        setBatchWorkbenchRunStatus(t('batch.preprocessStatus'), 'running')

        const selectedSummaries = Array.from(selectedSongs)
            .map(filename => songSummaryCache.get(filename))
            .filter(Boolean)

        const statusLog = []
        let batchTaskTotal = selectedSummaries.length
        const pushStatus = (message = '', isError = false) => {
            if (message) {
                statusLog.push(message)
                if (statusLog.length > 4) {
                    statusLog.splice(0, statusLog.length - 4)
                }
            }
            const stats = computeBatchRuntimeStats()
            const successCount = stats.success
            const errorCount = stats.failed
            const skippedCount = stats.skipped
            const processingCount = stats.processing
            const completedCount = stats.completed
            const totalCount = batchTaskTotal || stats.total
            setBatchWorkbenchProgress(completedCount, totalCount)
            const summaryLine = `${t('batch.progress')} ${completedCount}/${totalCount} | ${t('batch.successCount')} ${successCount} / ${t('batch.failedCount')} ${errorCount} / ${t('batch.skipped')} ${skippedCount} / ${t('batch.processing')} ${processingCount}`
            scheduleBatchTranslateStatusRender([summaryLine, ...statusLog.slice(-3)], isError, { force: isError })
        }

        setBatchWorkbenchProgress(0, selectedSummaries.length)

        const items = []
        const idToSummary = new Map()
        for (let i = 0; i < selectedSummaries.length; i++) {
            if (batchWorkbenchState.stopRequested) break
            const summary = selectedSummaries[i]
            const display = summary.filename.replace(/\.json$/i, '')
            const resultEntry = {
                id: String(i + 1),
                filename: summary.filename,
                display,
                summary,
                statusKey: 'queued',
                statusText: t('resultStatus.queued'),
                lineCount: 0,
                hasTimestamps: false,
                saved: false,
                savePath: '',
                error: '',
                originalPreview: '',
                translationPreview: '',
                translationText: '',
                payload: null
            }
            batchWorkbenchState.results.set(resultEntry.id, resultEntry)
            if (!batchWorkbenchState.activeResultId) {
                batchWorkbenchState.activeResultId = resultEntry.id
            }
            markBatchResultDirty(resultEntry.id, { detail: resultEntry.id === batchWorkbenchState.activeResultId, structure: true })
            try {
                if (!summary.lyricsPath || summary.lyricsPath === '!') {
                    resultEntry.statusKey = 'skipped'
                    resultEntry.statusText = t('batch.skipNoLyrics')
                    markBatchResultDirty(resultEntry.id, { detail: resultEntry.id === batchWorkbenchState.activeResultId })
                    pushStatus(`${display}: ${t('batch.skipNoLyricsPath')}`)
                    continue
                }
                resultEntry.statusKey = 'reading'
                resultEntry.statusText = t('resultStatus.reading')
                markBatchResultDirty(resultEntry.id, { detail: resultEntry.id === batchWorkbenchState.activeResultId })
                pushStatus(`${display}: ${t('batch.sentToModel')}`)
                const rawContent = await fetchLyricsTextByPath(summary.lyricsPath)
                resultEntry.originalPreview = String(rawContent || '').slice(0, 800)
                if (!rawContent || !rawContent.trim()) {
                    resultEntry.statusKey = 'skipped'
                    resultEntry.statusText = t('batch.skipNoLyrics')
                    markBatchResultDirty(resultEntry.id, { detail: resultEntry.id === batchWorkbenchState.activeResultId })
                    pushStatus(`${display}: ${t('batch.skipEmptyLyrics')}`)
                    continue
                }
                if (isSummaryTTML(summary)) {
                    resultEntry.statusKey = 'converting'
                    resultEntry.statusText = t('resultStatus.converting')
                    markBatchResultDirty(resultEntry.id, { detail: resultEntry.id === batchWorkbenchState.activeResultId })
                }
                const processedContent = await preprocessBatchLyrics(summary, rawContent)
                const itemId = String(i + 1)
                items.push({
                    id: itemId,
                    jsonFile: summary.filename,
                    song_name: getSummaryDisplayName(summary),
                    lyricsPath: summary.lyricsPath || '',
                    translationPath: summary.translationPath || '',
                    content: processedContent
                })
                idToSummary.set(itemId, summary)
                resultEntry.statusKey = 'sent'
                resultEntry.statusText = t('resultStatus.sent')
                markBatchResultDirty(resultEntry.id, { detail: resultEntry.id === batchWorkbenchState.activeResultId })
                pushStatus(`${display}: ${t('batch.addedToRequest')}`)
            } catch (error) {
                resultEntry.statusKey = 'failed'
                resultEntry.statusText = t('batch.preprocessError')
                resultEntry.error = error && error.message ? error.message : String(error)
                markBatchResultDirty(resultEntry.id, { detail: resultEntry.id === batchWorkbenchState.activeResultId })
                pushStatus(`${display}: ${t('batch.preprocessError')}: ${error.message || error}`, true)
            }
        }
        if (batchWorkbenchState.stopRequested) {
            stoppedByUser = true
        }

        if (stoppedByUser) {
            scheduleBatchTranslateStatusRender([t('batch.stoppedByUser')], true, { force: true })
            return
        }

        if (items.length === 0) {
            alert(t('alert.noTranslatableSong'))
            return
        }
        batchTaskTotal = items.length

        const idToDisplay = new Map(items.map(item => [item.id, item.jsonFile.replace(/\.json$/i, '')]))
        batchWorkbenchState.lifecyclePhase = 'waiting-model'
        setBatchWorkbenchRunStatus(t('batch.waitingModel'), 'running')
        await ensureAiRuntimeSummaryForProgress()
        const response = await fetch('/translate_lyrics', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            signal: batchTranslateAbortController.signal,
            body: JSON.stringify({
                items,
                ...getBatchWorkbenchPresetStateFromUI()
            })
        })

        const contentType = response.headers.get('Content-Type') || ''
        if (!response.ok || contentType.includes('application/json')) {
            const err = await response.json().catch(() => ({}))
            const failureMessage = `${t('batch.batchFailed')}${err.message || `HTTP ${response.status}`}`
            requestFailed = true
            markOutstandingAsFailed(`${t('batch.requestError')}${err.message || `HTTP ${response.status}`}`)
            scheduleBatchTranslateStatusRender([failureMessage], true, { force: true })
            return
        }

        const reader = response.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ''
        let streamErrorMessage = ''
        const latestPayloadById = new Map()
        const saveErrorById = new Map()

        const processLine = (line) => {
            if (!line || !line.startsWith('content:')) return
            try {
                const payload = JSON.parse(line.slice(8))
                if (payload.status === 'error' && payload.message) {
                    streamErrorMessage = payload.message
                    pushStatus(`${t('batch.systemError')}: ${payload.message}`, true)
                    return
                }
                if (!payload.id) {
                    return
                }
                const summary = idToSummary.get(payload.id)
                const display = idToDisplay.get(payload.id) || payload.id
                const resultEntry = batchWorkbenchState.results.get(payload.id)
                if (!summary) {
                    pushStatus(`${display}: ${t('batch.notFoundMapping')}`, true)
                    return
                }
                latestPayloadById.set(payload.id, payload)
                batchWorkbenchState.diagnostics.streamChunkCount += 1
                batchWorkbenchState.lifecyclePhase = 'receiving'
                const lineCount = Array.isArray(payload.translations) ? payload.translations.length : 0
                const tsMark = payload.hasTimestamps ? t('batch.hasTimestamp') : t('batch.noTimestamp')
                if (resultEntry) {
                    resultEntry.statusKey = 'received'
                    resultEntry.statusText = t('resultStatus.received')
                    resultEntry.lineCount = lineCount
                    resultEntry.hasTimestamps = Boolean(payload.hasTimestamps)
                    resultEntry.payload = payload
                    resultEntry.translationText = Array.isArray(payload.translations) ? payload.translations.join('\n') : ''
                    resultEntry.translationPreview = resultEntry.translationText.slice(0, 800)
                    markBatchResultDirty(resultEntry.id, { detail: resultEntry.id === batchWorkbenchState.activeResultId })
                }
                pushStatus(`${display}: ${t('batch.receivedTranslationDelta', {lines: lineCount, ts: tsMark})}`)
            } catch (error) {
                console.error('处理批量翻译内容失败:', error)
                const msg = error && error.message ? error.message : String(error)
                pushStatus(`${t('batch.systemError')}: ${t('batch.streamError')}: ${msg}`, true)
            }
        }

        while (true) {
            if (batchWorkbenchState.stopRequested) {
                stoppedByUser = true
                break
            }
            const { done, value } = await reader.read()
            if (done) break
            buffer += decoder.decode(value, { stream: true })
            let newlineIndex
            while ((newlineIndex = buffer.indexOf('\n')) !== -1) {
                const line = buffer.slice(0, newlineIndex)
                buffer = buffer.slice(newlineIndex + 1)
                processLine(line)
            }
        }
        if (stoppedByUser) {
            pushStatus(t('batch.userStopped'), true)
        }
        const tail = decoder.decode()
        if (tail) buffer += tail
        if (buffer) {
            const remaining = buffer.split('\n')
            for (const line of remaining) {
                processLine(line)
            }
        }

        for (const item of items) {
            if (batchWorkbenchState.stopRequested) break
            const itemId = item.id
            const display = idToDisplay.get(itemId) || itemId
            const summary = idToSummary.get(itemId)
            const finalPayload = latestPayloadById.get(itemId)
            const resultEntry = batchWorkbenchState.results.get(itemId)

            if (!summary) {
                if (resultEntry) {
                    resultEntry.statusKey = 'failed'
                    resultEntry.statusText = t('batch.mappingError')
                }
                pushStatus(`${display}: ${t('batch.notFoundMapping')}`, true)
                continue
            }
            if (!finalPayload) {
                if (resultEntry && resultEntry.statusKey !== 'skipped') {
                    resultEntry.statusKey = 'failed'
                    resultEntry.statusText = t('batch.modelReturnError')
                    markBatchResultDirty(resultEntry.id, { detail: resultEntry.id === batchWorkbenchState.activeResultId })
                }
                pushStatus(`${display}: ${t('batch.noTranslationResult')}`, true)
                continue
            }
            try {
                const hadTranslation = hasSummaryTranslation(summary)
                const shouldSkipSave = (!autoSave) || (onlyEmpty && hadTranslation && !alwaysOverride)
                if (shouldSkipSave) {
                    if (resultEntry) {
                        resultEntry.saved = false
                        resultEntry.statusKey = 'success'
                        resultEntry.statusText = autoSave ? t('batch.skipHasTranslation') : t('resultStatus.success')
                        markBatchResultDirty(resultEntry.id, { detail: resultEntry.id === batchWorkbenchState.activeResultId })
                    }
                    pushStatus(`${display}: ${t('batch.receivedTranslation')}`)
                    continue
                }
                if (resultEntry) {
                    resultEntry.statusKey = 'saving'
                    resultEntry.statusText = t('status.saving')
                    markBatchResultDirty(resultEntry.id, { detail: resultEntry.id === batchWorkbenchState.activeResultId })
                }
                batchWorkbenchState.lifecyclePhase = 'saving'
                setBatchWorkbenchRunStatus(t('batch.savingTranslation'), 'running')
                pushStatus(`${display}: ${t('batch.savingTranslation')}`)
                const savePath = await saveBatchTranslationToDisk(summary, finalPayload)
                const lineCount = Array.isArray(finalPayload.translations) ? finalPayload.translations.length : 0
                const tsMark = finalPayload.hasTimestamps ? t('batch.hasTimestamp') : t('batch.noTimestamp')
                if (resultEntry) {
                    resultEntry.saved = true
                    resultEntry.savePath = savePath
                    resultEntry.statusKey = 'success'
                    resultEntry.statusText = t('resultStatus.success')
                    markBatchResultDirty(resultEntry.id, { detail: resultEntry.id === batchWorkbenchState.activeResultId })
                }
                pushStatus(`${display}: ${t('batch.complete')} (${t('batch.saved')}, ${lineCount} ${t('batch.lineCount')}, ${tsMark})`)
            } catch (saveError) {
                const saveMsg = saveError && saveError.message ? saveError.message : String(saveError)
                saveErrorById.set(itemId, saveMsg)
                if (resultEntry) {
                    resultEntry.saved = false
                    resultEntry.error = saveMsg
                    resultEntry.statusKey = 'failed'
                    resultEntry.statusText = t('batch.savePhaseFailed')
                    markBatchResultDirty(resultEntry.id, { detail: resultEntry.id === batchWorkbenchState.activeResultId })
                }
                pushStatus(`${t('batch.systemError')}: ${t('batch.savePhaseFailed')}: ${saveMsg}`, true)
            }
            pushStatus(`${display}: ${saveErrorById.has(itemId) ? t('batch.savePhaseFailed') : t('batch.processed')}`, saveErrorById.size > 0)
        }

        items.forEach(item => {
            const display = idToDisplay.get(item.id) || item.id
            const resultEntry = batchWorkbenchState.results.get(item.id)
            const currentStatusKey = resultEntry ? resultEntry.statusKey : ''
            if (currentStatusKey !== 'success' && currentStatusKey !== 'skipped' && currentStatusKey !== 'stopped') {
                if (saveErrorById.has(item.id)) {
                    if (resultEntry) {
                        resultEntry.statusKey = 'failed'
                        resultEntry.statusText = t('batch.savePhaseFailed')
                        markBatchResultDirty(resultEntry.id, { detail: resultEntry.id === batchWorkbenchState.activeResultId })
                    }
                    pushStatus(`${display}: ${t('batch.saveFailedWithMsg', {msg: saveErrorById.get(item.id)})}`, true)
                } else if (streamErrorMessage) {
                    if (resultEntry && resultEntry.statusKey !== 'success') {
                        resultEntry.statusKey = 'failed'
                        resultEntry.statusText = t('batch.modelReturnError')
                        resultEntry.error = streamErrorMessage
                        markBatchResultDirty(resultEntry.id, { detail: resultEntry.id === batchWorkbenchState.activeResultId })
                    }
                    pushStatus(`${display}: ${t('batch.translationErrorMsg', {msg: streamErrorMessage})}`, true)
                } else if (!latestPayloadById.has(item.id) && !currentStatusKey) {
                    if (resultEntry) {
                        resultEntry.statusKey = 'failed'
                        resultEntry.statusText = t('batch.modelReturnError')
                        markBatchResultDirty(resultEntry.id, { detail: resultEntry.id === batchWorkbenchState.activeResultId })
                    }
                    pushStatus(`${display}: ${t('batch.noTranslationResult')}`, true)
                } else if (!currentStatusKey || currentStatusKey === 'saving') {
                    if (resultEntry && resultEntry.statusKey !== 'success') {
                        resultEntry.statusKey = 'failed'
                        resultEntry.statusText = t('batch.statusIncomplete')
                        markBatchResultDirty(resultEntry.id, { detail: resultEntry.id === batchWorkbenchState.activeResultId })
                    }
                    pushStatus(`${display}: ${t('batch.statusIncomplete')}`, true)
                }
            }
        })
        scheduleBatchWorkbenchResultRender({ detail: true })
        pushStatus(t('batch.flowEnded'), Boolean(streamErrorMessage) || saveErrorById.size > 0)
        } catch (error) {
            const message = error && error.message ? error.message : String(error)
            const isAbortError = error && (error.name === 'AbortError' || String(message).toLowerCase().includes('aborted'))
            if (isAbortError || batchWorkbenchState.stopRequested) {
                stoppedByUser = true
                scheduleBatchTranslateStatusRender([t('batch.stoppedByUser')], true, { force: true })
            } else {
                requestFailed = true
                markOutstandingAsFailed(`${t('batch.requestError')}${message}`)
                scheduleBatchTranslateStatusRender([`${t('batch.flowAborted')}${message}`], true, { force: true })
            }
        } finally {
            if (stoppedByUser) {
                markOutstandingAsStopped()
            }
            batchWorkbenchState.running = false
            batchWorkbenchState.lifecyclePhase = 'ended'
            batchTranslateAbortController = null
            const finalEntries = Array.from(batchWorkbenchState.results.values())
            const successCount = finalEntries.filter(item => item.statusKey === 'success').length
            const failedCount = finalEntries.filter(item => item.statusKey === 'failed').length
            const skippedCount = finalEntries.filter(item => item.statusKey === 'skipped' || item.statusKey === 'stopped').length
            const finalState = stoppedByUser
                ? 'stopped'
                : (requestFailed
                    ? 'failed'
                    : (failedCount > 0
                        ? (successCount > 0 ? 'partial' : 'failed')
                        : (successCount === finalEntries.length ? 'completed' : 'partial')))
            batchWorkbenchState.runState = finalState
            updateBatchWorkbenchLifecycle('ended', finalState === 'completed' ? 'success' : (finalState === 'partial' ? 'partial' : finalState))
            const finalMessage = stoppedByUser
                ? t('batch.taskStopped')
                : (finalState === 'failed'
                    ? t('batch.taskFailed')
                    : (finalState === 'partial'
                        ? (failedCount > 0
                            ? t('batch.taskPartialFailed')
                            : (skippedCount > 0 ? t('batch.taskPartialSkipped') : t('batch.taskCompleted')))
                        : t('batch.taskCompleted')))
            setBatchWorkbenchRunStatus(finalMessage, finalState)
            const totalEntries = batchWorkbenchState.results.size
            const finishedEntries = successCount + failedCount + skippedCount
            setBatchWorkbenchProgress(finishedEntries, totalEntries)
            scheduleBatchWorkbenchResultRender({ detail: true, full: true })
            setAlbumCoverObserverSuspended(viewportResizing)
        }
}

function refreshSongItemSearchCache(item) {
    if (!item) return
    const filename = String(item.dataset && item.dataset.filename ? item.dataset.filename : '')
    const fileNameText = item.querySelector('.file-name-text')
    const tagsEl = item.querySelector('.song-tags')
    const visibleTitle = fileNameText ? String(fileNameText.textContent || '') : filename.replace(/\.json$/i, '')
    const tagsText = tagsEl ? String(tagsEl.textContent || '') : ''
    const sortName = item.dataset.sortName
        ? String(item.dataset.sortName)
        : visibleTitle.toLowerCase()
    const searchPool = `${visibleTitle} ${filename} ${tagsText}`.toLowerCase()
    item.dataset.sortName = sortName
    item.dataset.searchPool = searchPool
    if (filename) {
        songSortNameCache.set(filename, sortName)
        songSearchPoolCache.set(filename, searchPool)
    }
}

function buildSearchPoolByFilename(filename) {
    if (!filename) return ''
    if (!songSearchPoolCache.has(filename)) {
        refreshSongSummarySearchCacheByFilename(filename)
    }
    const cachedItem = songItemByFilename.get(filename)
    if (cachedItem && !cachedItem.dataset.searchPool) {
        refreshSongItemSearchCache(cachedItem)
    }
    return String(songSearchPoolCache.get(filename) || '')
}

function applyPortModeToSongItem(item) {
    if (!item) return
    const styleButtons = item.querySelectorAll('.am-style, .fs-style, .fslr-style')
    styleButtons.forEach(btn => {
        if (currentPortMode === 'random') {
            btn.disabled = true
            btn.classList.add('disabled-style')
            btn.title = t('port.unavailable')
        } else {
            btn.disabled = false
            btn.classList.remove('disabled-style')
            btn.title = ''
            btn.textContent = btn.classList.contains('am-style')
                ? t('song.amStyle')
                : (btn.classList.contains('fs-style') ? t('song.fullscreenStyle') : t('song.fullscreenDuetStyle'))
        }
    })
}

function applyPortModeToAllSongItems() {
    songItemByFilename.forEach(item => applyPortModeToSongItem(item))
}

function applyWriteLockToSongItem(item, locked = currentWriteLockEnabled) {
    if (!item) return
    const controls = item.querySelectorAll('.file-actions .action-button[data-write-lock], .style-buttons .style-button[data-write-lock]')
    controls.forEach(btn => {
        btn.disabled = Boolean(locked)
        btn.style.pointerEvents = locked ? 'none' : ''
        btn.style.opacity = locked ? '0.5' : ''
    })
}

function applyWriteLockToAllSongItems() {
    songItemByFilename.forEach(item => applyWriteLockToSongItem(item))
}

function appendSongListBatch() {
    if (!jsonList) return
    if (mainListRenderCursor >= mainListVisibleFilenames.length) return
    const fragment = document.createDocumentFragment()
    const nextCursor = Math.min(mainListVisibleFilenames.length, mainListRenderCursor + MAIN_LIST_BATCH_SIZE)
    for (let i = mainListRenderCursor; i < nextCursor; i += 1) {
        const filename = String(mainListVisibleFilenames[i] || '')
        const item = ensureSongItemForFilename(filename)
        if (!item) continue
        applyPortModeToSongItem(item)
        applyWriteLockToSongItem(item)
        item.style.display = ''
        fragment.appendChild(item)
    }
    mainListRenderCursor = nextCursor
    jsonList.appendChild(fragment)
    refreshAlbumCoverObserverTargets()
}

async function ensureMainListViewportFilled() {
    let safety = 0
    while (safety < 24) {
        const distanceToBottom = document.documentElement.scrollHeight - (window.scrollY + window.innerHeight)
        if (distanceToBottom > 260) {
            break
        }
        if (mainListRenderCursor < mainListVisibleFilenames.length) {
            appendSongListBatch()
        } else if (libraryMode === 'search' && searchHasMore && !searchIsLoading) {
            await loadMoreSongSearch()
        } else if (songSummaryHasMore && !songSummaryIsLoadingMore) {
            await loadMoreSongSummaries()
        } else {
            break
        }
        safety += 1
    }
}

function resetAndRenderMainSongList(visibleFilenames) {
    mainListVisibleFilenames = Array.isArray(visibleFilenames) ? visibleFilenames : []
    mainListRenderCursor = 0
    if (jsonList) {
        jsonList.innerHTML = ''
    }
    appendSongListBatch()
    void ensureMainListViewportFilled()
}

function handleMainListScroll() {
    if (viewportResizing) return
    if (mainListRenderRaf) return
    mainListRenderRaf = window.requestAnimationFrame(() => {
        mainListRenderRaf = 0
        const distanceToBottom = document.documentElement.scrollHeight - (window.scrollY + window.innerHeight)
        if (distanceToBottom < 420) {
            appendSongListBatch()
            void ensureMainListViewportFilled()
        }
    })
}

function isElementInsideViewport(element, margin = 0) {
    if (!element || typeof element.getBoundingClientRect !== 'function') {
        return false
    }
    const rect = element.getBoundingClientRect()
    const viewportWidth = window.innerWidth || document.documentElement.clientWidth || 0
    const viewportHeight = window.innerHeight || document.documentElement.clientHeight || 0
    return rect.bottom >= -margin
        && rect.top <= viewportHeight + margin
        && rect.right >= -margin
        && rect.left <= viewportWidth + margin
}

function pauseActiveAlbumCoverVideosForResize() {
    const activeVideos = []
    const videos = document.querySelectorAll('#jsonList .album-cover-video')
    videos.forEach(videoEl => {
        if (!(videoEl instanceof HTMLVideoElement)) return
        if (!videoEl.isConnected) return
        if (!videoEl.paused && !videoEl.ended) {
            activeVideos.push(videoEl)
        }
        videoEl.pause()
    })
    resizePausedCoverVideos = activeVideos
}

function resumeAlbumCoverVideosAfterResize() {
    if (!Array.isArray(resizePausedCoverVideos) || resizePausedCoverVideos.length === 0) {
        resizePausedCoverVideos = []
        return
    }
    const videosToResume = resizePausedCoverVideos
    resizePausedCoverVideos = []
    videosToResume.forEach(videoEl => {
        if (!(videoEl instanceof HTMLVideoElement)) return
        if (!videoEl.isConnected) return
        if (!isElementInsideViewport(videoEl, 140)) return
        videoEl.play().catch(() => {
            // Ignore autoplay/gesture restrictions
        })
    })
}

function finishViewportResize() {
    if (resizeSettleTimer) {
        window.clearTimeout(resizeSettleTimer)
        resizeSettleTimer = 0
    }
    if (!viewportResizing) return
    viewportResizing = false
    document.body.classList.remove('is-resizing')
    if (!batchWorkbenchState.running) {
        setAlbumCoverObserverSuspended(false)
    }
    void ensureMainListViewportFilled()
    resumeAlbumCoverVideosAfterResize()
}

function beginViewportResize() {
    if (!viewportResizing) {
        viewportResizing = true
        document.body.classList.add('is-resizing')
        if (!batchWorkbenchState.running) {
            setAlbumCoverObserverSuspended(true)
        }
        pauseActiveAlbumCoverVideosForResize()
    }
    if (resizeSettleTimer) {
        window.clearTimeout(resizeSettleTimer)
    }
    resizeSettleTimer = window.setTimeout(finishViewportResize, RESIZE_SETTLE_MS)
}

function handleViewportResize() {
    beginViewportResize()
    if (typeof scheduleUpdateSearchHeaderOffset === 'function') {
        scheduleUpdateSearchHeaderOffset()
    }
}

function scheduleApplySearch() {
    if (searchInputDebounceTimer) {
        window.clearTimeout(searchInputDebounceTimer)
    }
    searchInputDebounceTimer = window.setTimeout(() => {
        searchInputDebounceTimer = 0
        applySearch()
    }, SEARCH_INPUT_DEBOUNCE_MS)
}

function applySearch() {
    if (searchInputDebounceTimer) {
        window.clearTimeout(searchInputDebounceTimer)
        searchInputDebounceTimer = 0
    }
    const rawText = String(searchBox && searchBox.value ? searchBox.value : '').toLowerCase()
    const isFuzzy = fuzzySearchToggle && fuzzySearchToggle.checked
    const keywords = isFuzzy ? parseFuzzyKeywords(rawText) : parseKeywords(rawText)

    if (keywords.length === 0) {
        searchResultGeneration += 1
        searchIsLoading = false
        if (libraryMode === 'search') {
            restoreBrowseLibraryFromSnapshot()
            libraryMode = 'browse'
        }
        const listFilenames = Array.from(allSongFilenames || [])
        if (listFilenames.length === 0) {
            if (jsonList) {
                jsonList.innerHTML = ''
            }
            mainListVisibleFilenames = []
            mainListRenderCursor = 0
            updateSongLibraryProgressStatus()
            return
        }
        listFilenames.sort(getSortComparator())
        resetAndRenderMainSongList(listFilenames)
        updateSongLibraryProgressStatus()
        return
    }

    if (libraryMode === 'browse') {
        captureBrowseLibrarySnapshot()
    }
    libraryMode = 'search'
    void executeLibrarySearch()
}

window.addEventListener('scroll', handleMainListScroll, { passive: true })
window.addEventListener('resize', handleViewportResize)

if (searchBox) {
    searchBox.addEventListener('input', scheduleApplySearch)
}
if (fuzzySearchToggle) {
    fuzzySearchToggle.addEventListener('change', () => {
        if (hasMainListSearchKeywords() && libraryMode === 'search') {
            void executeLibrarySearch()
        } else {
            applySearch()
        }
    })
}

let currentEditingFile = ''
let currentLyricsPath = ''
let currentLyricsIndex = 0
let currentJsonFile = ''
let currentMusicPath = ''
let currentMusicJsonFile = ''
let currentImagePath = ''
let currentImageJsonFile = ''
let currentRenameFile = ''
let currentSort = { type: 'time', asc: false } // 初始为时间倒序
let currentRestoreFile = ''
let lastThinkingSummary = ''

function toggleSort(type) {
    if (type === currentSort.type) {
        currentSort.asc = !currentSort.asc
    } else {
        currentSort.type = type
        currentSort.asc = (type === 'name') // 名称排序默认升序
    }
    updateSortButtons()
    if (libraryMode === 'search' && hasMainListSearchKeywords()) {
        void executeLibrarySearch()
        return
    }
    applySearch()
}

function updateSortButtons() {
    const nameBtn = document.getElementById('nameSortBtn')
    const timeBtn = document.getElementById('timeSortBtn')

    nameBtn.textContent = `${t('sort.name')} ${currentSort.type === 'name' ? (currentSort.asc ? t('sort.asc') : t('sort.desc')) : ''}`
    timeBtn.textContent = `${t('sort.time')} ${currentSort.type === 'time' ? (currentSort.asc ? t('sort.asc') : t('sort.desc')) : ''}`
}

function sortList() {
    if (libraryMode === 'search' && hasMainListSearchKeywords()) {
        void executeLibrarySearch()
        return
    }
    applySearch()
}

function initMusicAlbumPathDropzones() {
    function attach(el, kind) {
        if (!el) return

        // Capture phase so dragover/drop run before nested inputs/buttons; avoids browser opening dropped files.
        const cap = true

        el.addEventListener('dragenter', (e) => {
            e.preventDefault()
            el.classList.add('path-dropzone--dragover')
        }, cap)
        el.addEventListener('dragleave', (e) => {
            if (!el.contains(e.relatedTarget)) {
                el.classList.remove('path-dropzone--dragover')
            }
        }, cap)
        el.addEventListener('dragover', (e) => {
            e.preventDefault()
            e.dataTransfer.dropEffect = 'copy'
        }, cap)
        el.addEventListener('drop', (e) => {
            e.preventDefault()
            e.stopPropagation()
            el.classList.remove('path-dropzone--dragover')

            if (currentWriteLockEnabled) {
                alert(t('pathDropzone.writeLocked'))
                return
            }

            const { file, urlHint } = resolveDroppedUploadFile(e.dataTransfer)

            if (kind === 'music') {
                const urlHintIsMusic = urlHint && isLikelyMusicMediaUrlHint(urlHint)

                if (!file && urlHintIsMusic) {
                    if (typeof uploadMusicFromDroppedUrl !== 'function') return
                    uploadMusicFromDroppedUrl(urlHint)
                    return
                }
                if (!file) return
                if (typeof uploadMusicFile !== 'function') return

                if (isLikelyMusicMediaFile(file)) {
                    uploadMusicFile(file, { fallbackUrlHint: urlHint })
                    return
                }
                if (urlHintIsMusic) {
                    uploadMusicFromDroppedUrl(urlHint)
                    return
                }
                alert(t('file.uploadAudio'))
            } else if (kind === 'album') {
                if (!file) return
                if (typeof handleImageUpload !== 'function') {
                    return
                }
                handleImageUpload(file, 'album')
            }
        }, cap)
    }

    attach(document.getElementById('musicPathDropzone'), 'music')
    attach(document.getElementById('albumCoverDropzone'), 'album')
}

// 初始化时更新按钮状态
window.addEventListener('DOMContentLoaded', () => {
    applyI18nToStaticElements()
    updateSortButtons()
    bindEditorValueProxy()
    loadSongSummaries()
    updateBatchWorkbenchPresetSelect()
    updateQuickAiPresetSelect() // 初始化快速预设选择框
    renderBatchSelectionCount()
    const wbControls = getBatchWorkbenchControls()
    if (wbControls.search) {
        wbControls.search.addEventListener('input', renderBatchWorkbenchSongList)
    }
    if (wbControls.fuzzy) {
        wbControls.fuzzy.addEventListener('change', renderBatchWorkbenchSongList)
    }
    if (wbControls.songList) {
        wbControls.songList.addEventListener('scroll', handleBatchWorkbenchSongListScroll)
    }
    const persistAndRefresh = () => {
        syncBatchWbSettingsSummary()
        syncBatchWbHeaderSummary()
        renderBatchWorkbenchSummary()
    }
    ;['aiProvider', 'aiBaseUrl', 'aiModel', 'expectReasoningCheck', 'compatModeCheck', 'stripBracketsCheck', 'experimentalFullLineBracketStripCheck', 'experimentalBracketLineAsSublineCheck', 'thinkingEnabledCheck', 'autoSave', 'onlyEmptyCheck', 'systemPrompt', 'extraPrompt'].forEach(key => {
        const el = wbControls[key]
        if (!el) return
        const evt = el.tagName === 'TEXTAREA' || (el.tagName === 'INPUT' && el.type !== 'checkbox' && el.type !== 'radio') ? 'input' : 'change'
        el.addEventListener(evt, persistAndRefresh)
    })
    if (wbControls.alwaysOverrideCheck) {
        wbControls.alwaysOverrideCheck.addEventListener('change', () => {
            onBatchWbOverrideToggle(wbControls.alwaysOverrideCheck)
            persistAndRefresh()
        })
    }
    applyBatchWorkbenchSettingsToUI()
    syncBatchWbSettingsSummary()
    syncBatchWbHeaderSummary()
    initLyricsSearchModalEvents()
    initMusicAlbumPathDropzones()
})

function viewFile(type, path) {
    // 实现文件预览逻辑
}

function editJson(filename) {
    currentEditingFile = filename
    const modal = document.getElementById('editModal')
    const editor = document.getElementById('jsonEditor')
    // 获取JSON内容并显示在编辑器中
    modal.style.display = 'block'
}

function saveJson() {
    const editor = document.getElementById('jsonEditor')
    fetch('/update_json', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            filename: currentEditingFile,
            content: JSON.parse(editor.value)
        })
    }).then(response => response.json())
        .then(data => {
            if (data.status === 'success') {
                closeModal()
                location.reload()
            }
        })
}

function closeModal() {
    document.getElementById('editModal').style.display = 'none'
}

function restoreFile(filename) {
    fetch('/restore_file', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            file_path: normalizeStaticUrl(filename)
        })
    }).then(response => response.json())
        .then(data => {
            if (data.status === 'success') {
                location.reload()
            }
        })
}

function detectFontFamilyTag(text) {
    if (!text) return '';
    const match = text.match(/^\s*\[font-family:\s*([^\]]+?)\s*\]\s*$/mi);
    return match ? match[1].trim() : '';
}

function updateFontFamilyNotice(content) {
    const noticeEl = document.getElementById('fontFamilyNotice');
    if (!noticeEl) return;
    const detected = detectFontFamilyTag(content);
    if (detected) {
        noticeEl.textContent = t('runtime.fontTagDetected') + detected;
        noticeEl.style.display = 'block';
    } else {
        noticeEl.textContent = t('status.noFontTag');
        noticeEl.style.display = 'block';
    }
}

async function editLyrics(lyricsPath, translationPath, index, jsonFile) {
    currentJsonFile = jsonFile
    const modal = document.getElementById('lyricsModal')
    const lyricsPathInput = document.getElementById('lyricsPath')
    const translationPathInput = document.getElementById('translationPath')
    const normalizedLyricsPath = lyricsPath && lyricsPath !== '!' ? lyricsPath : ''
    const normalizedTranslationPath = translationPath && translationPath !== '!' ? translationPath : ''
    
    // 设置当前文件名
    document.getElementById('currentFileName').textContent = jsonFile.replace('.json', '')

    // 设置路径
    lyricsPathInput.value = normalizedLyricsPath ? stripSongsPrefix(normalizedLyricsPath) : ''
    translationPathInput.value = normalizedTranslationPath ? stripSongsPrefix(normalizedTranslationPath) : ''

    updateFontFamilyNotice('')
    await ensureMonacoEditors()

    // 加载歌词内容
    if (normalizedLyricsPath) {
        fetch('/get_lyrics', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: normalizedLyricsPath })
        })
            .then(response => response.json())
            .then(data => {
                if (data.status === 'success') {
                    setLyricsContent(data.content)
                    updateFontFamilyNotice(data.content)
                }
            })
    } else {
        setLyricsContent('')
    }

    // 加载翻译内容
    if (normalizedTranslationPath) {
        fetch('/get_lyrics', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: normalizedTranslationPath })
        })
            .then(response => response.json())
            .then(data => {
                if (data.status === 'success') {
                    setTranslationContent(data.content)
                }
            })
    } else {
        setTranslationContent('')
    }

    lockPageScrollForModal()
    modal.style.display = 'block'
    checkFileExtension()
}

function openQuickEdit() {
    if (!currentJsonFile) {
        alert(t('alert.selectSongToEdit'))
        return
    }
    const target = `/quick-editor?json=${encodeURIComponent(currentJsonFile)}`
    window.open(target, '_blank')
}

let latestLyricsSearchResult = null
let lyricsSearchModalEventsBound = false
let lyricsSearchPreviewApplyGateBound = false
const lyricsSearchFlow = {
    isSearching: false,
    isMatching: false,
    isFetchingLyrics: false,
    results: [],
    selectedIndex: -1
}
const LYRICS_SEARCH_REGEX_KEYS = {
    title: 'lyricsSearchTitleRegex',
    artist: 'lyricsSearchArtistRegex'
}

function initLyricsSearchModalEvents() {
    if (lyricsSearchModalEventsBound) return
    lyricsSearchModalEventsBound = true
    const parseBtn = document.getElementById('lyricsSearchBtnParse')
    if (parseBtn) {
        parseBtn.addEventListener('click', () => applySearchNameFromFile(false))
    }
    const autoBtn = document.getElementById('lyricsSearchBtnAutoMatch')
    if (autoBtn) {
        autoBtn.addEventListener('click', () => {
            const title = document.getElementById('lyricsSearchTitle').value.trim()
            const artist = document.getElementById('lyricsSearchArtist').value.trim()
            if (title && artist) {
                performLyricsMatch(title, artist)
                return
            }
            applySearchNameFromFile(true)
        })
    }
    const searchBtn = document.getElementById('lyricsSearchBtnSearchList')
    if (searchBtn) {
        searchBtn.addEventListener('click', () => performLyricsSearch())
    }
    const clearBtn = document.getElementById('lyricsSearchBtnClear')
    if (clearBtn) {
        clearBtn.addEventListener('click', () => clearLyricsSearchInputs())
    }
    const applyBtn = document.getElementById('lyricsSearchBtnApply')
    if (applyBtn) {
        applyBtn.addEventListener('click', () => applyLyricsSearchResult())
    }
    const closeBtn = document.getElementById('lyricsSearchBtnClose')
    if (closeBtn) {
        closeBtn.addEventListener('click', () => closeLyricsSearchModal())
    }
    const kwInput = document.getElementById('lyricsSearchKeyword')
    if (kwInput) {
        kwInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault()
                performLyricsSearch()
            }
        })
    }
}

function updateLyricsSearchActionButtons() {
    const busy = lyricsSearchFlow.isSearching || lyricsSearchFlow.isMatching || lyricsSearchFlow.isFetchingLyrics
    const parseBtn = document.getElementById('lyricsSearchBtnParse')
    const autoBtn = document.getElementById('lyricsSearchBtnAutoMatch')
    const searchBtn = document.getElementById('lyricsSearchBtnSearchList')
    const clearBtn = document.getElementById('lyricsSearchBtnClear')
    const applyBtn = document.getElementById('lyricsSearchBtnApply')
    ;[parseBtn, autoBtn, searchBtn, clearBtn].forEach(btn => {
        if (btn) btn.disabled = busy
    })
    if (applyBtn) {
        applyBtn.disabled = busy || !hasLyricsSearchApplicablePreview()
    }
}

function fillLyricsSearchResultsHint() {
    const resultsEl = document.getElementById('lyricsSearchResults')
    if (!resultsEl) return
    resultsEl.innerHTML = ''
    const div = document.createElement('div')
    div.className = 'lyrics-search-placeholder'
    div.textContent = t('lyricsSearch.resultsHint')
    resultsEl.appendChild(div)
}

function fillLyricsSearchFieldsFromSongSummary() {
    if (!currentJsonFile) return false
    const summary = songSummaryCache.get(currentJsonFile)
    if (!summary) return false
    const title = String(summary.title || '').trim()
    const artist = getSummaryArtist(summary)
    const titleEl = document.getElementById('lyricsSearchTitle')
    const artistEl = document.getElementById('lyricsSearchArtist')
    const kwEl = document.getElementById('lyricsSearchKeyword')
    if (titleEl) titleEl.value = title
    if (artistEl) artistEl.value = artist
    if (kwEl) {
        const parts = [title, artist].filter(Boolean)
        kwEl.value = parts.join(' ')
    }
    return Boolean(title || artist)
}

async function openLyricsSearchModal() {
    const modal = document.getElementById('lyricsSearchModal')
    if (!modal) return
    latestLyricsSearchResult = null
    lyricsSearchFlow.results = []
    lyricsSearchFlow.selectedIndex = -1
    lyricsSearchFlow.isSearching = false
    lyricsSearchFlow.isMatching = false
    lyricsSearchFlow.isFetchingLyrics = false

    fillLyricsSearchResultsHint()
    setLyricsSearchPreview('', '')

    const currentName = document.getElementById('currentFileName').textContent.trim()
    const nameEl = document.getElementById('lyricsSearchFileName')
    if (nameEl) nameEl.textContent = currentName || t('lyricsSearch.notSelectedText')

    loadLyricsSearchRegexCache()
    const filledFromSummary = fillLyricsSearchFieldsFromSongSummary()
    await ensureLyricsSearchEditors()
    requestAnimationFrame(() => {
        try {
            if (lyricsSearchMonacoEditor) lyricsSearchMonacoEditor.layout()
            if (translationSearchMonacoEditor) translationSearchMonacoEditor.layout()
        } catch (e) {}
    })

    modal.style.display = 'block'
    if (filledFromSummary) {
        setLyricsSearchStatus('info', t('lyricsSearch.prefillFromSong'))
    } else {
        setLyricsSearchStatus('info', t('lyricsSearch.hintText'))
    }
    updateLyricsSearchActionButtons()
}

function closeLyricsSearchModal() {
    const modal = document.getElementById('lyricsSearchModal')
    if (modal) {
        modal.style.display = 'none'
    }
    latestLyricsSearchResult = null
    lyricsSearchFlow.results = []
    lyricsSearchFlow.selectedIndex = -1
    lyricsSearchFlow.isSearching = false
    lyricsSearchFlow.isMatching = false
    lyricsSearchFlow.isFetchingLyrics = false
    setLyricsSearchPreview('', '')
    updateLyricsSearchActionButtons()
}

function lockPageScrollForModal() {
    if (document.documentElement.dataset.modalScrollLocked === '1') {
        return;
    }
    const scrollY = window.scrollY || window.pageYOffset || 0;
    document.documentElement.dataset.modalScrollLocked = '1';
    document.documentElement.dataset.modalScrollY = String(scrollY);

    document.documentElement.classList.add('modal-open');
    document.body.classList.add('modal-open');

    // iOS-friendly scroll lock:
    // keep the viewport fixed, preserve the current scroll position.
    document.body.style.position = 'fixed';
    document.body.style.top = `-${scrollY}px`;
    document.body.style.left = '0';
    document.body.style.right = '0';
    document.body.style.width = '100%';
    document.body.style.overflow = 'hidden';
}

function unlockPageScrollForModal() {
    if (document.documentElement.dataset.modalScrollLocked !== '1') {
        return;
    }
    const scrollY = parseInt(document.documentElement.dataset.modalScrollY || '0', 10) || 0;
    delete document.documentElement.dataset.modalScrollLocked;
    delete document.documentElement.dataset.modalScrollY;

    document.documentElement.classList.remove('modal-open');
    document.body.classList.remove('modal-open');

    document.body.style.position = '';
    document.body.style.top = '';
    document.body.style.left = '';
    document.body.style.right = '';
    document.body.style.width = '';
    document.body.style.overflow = '';

    window.scrollTo(0, scrollY);
}

function setLyricsSearchStatus(type, text) {
    const statusEl = document.getElementById('lyricsSearchStatus')
    if (!statusEl) return
    const display = text == null ? '' : String(text)
    const safeText = escapeHtml(display)
    if (type === 'loading') {
        statusEl.innerHTML = `<a href="#" class="btn-shine">${safeText}</a>`
    } else {
        statusEl.textContent = display
    }
    statusEl.style.display = 'block'
    statusEl.classList.remove('status-error', 'status-success', 'status-info')
    if (type === 'error') {
        statusEl.classList.add('status-error')
    } else if (type === 'success') {
        statusEl.classList.add('status-success')
    } else {
        statusEl.classList.add('status-info')
    }
}

function formatFetchError(error) {
    if (error == null) return ''
    if (typeof error === 'string') return error
    return error.message || String(error)
}

function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, match => {
        switch (match) {
            case '&': return '&amp;'
            case '<': return '&lt;'
            case '>': return '&gt;'
            case '"': return '&quot;'
            case "'": return '&#39;'
            default: return match
        }
    })
}

function setLyricsSearchPreview(lysText, translationText) {
    setSearchLyricsContent(lysText || '')
    setSearchTranslationContent(translationText || '')
}

function clearLyricsSearchInputs() {
    document.getElementById('lyricsSearchKeyword').value = ''
    document.getElementById('lyricsSearchTitle').value = ''
    document.getElementById('lyricsSearchArtist').value = ''
    document.getElementById('lyricsSearchTitleRegex').value = ''
    document.getElementById('lyricsSearchArtistRegex').value = ''
    persistLyricsSearchRegexCache()
    latestLyricsSearchResult = null
    lyricsSearchFlow.results = []
    lyricsSearchFlow.selectedIndex = -1
    setLyricsSearchPreview('', '')
    fillLyricsSearchResultsHint()
    setLyricsSearchStatus('success', t('lyricsSearch.cleared'))
    updateLyricsSearchActionButtons()
}

function normalizeSearchFileName(rawName) {
    if (!rawName) return ''
    return rawName.replace(/\.[^.]+$/, '')
}

function applySearchNameFromFile(autoMatch = false) {
    const fileName = normalizeSearchFileName(document.getElementById('currentFileName').textContent.trim())
    if (!fileName) {
        setLyricsSearchStatus('error', t('lyricsSearch.noFilename'))
        return
    }
    let title = document.getElementById('lyricsSearchTitle').value.trim()
    let artist = document.getElementById('lyricsSearchArtist').value.trim()
    const titleRegex = document.getElementById('lyricsSearchTitleRegex').value.trim()
    const artistRegex = document.getElementById('lyricsSearchArtistRegex').value.trim()
    if (titleRegex) {
        try {
            const match = fileName.match(new RegExp(titleRegex))
            if (match) title = (match[1] || match[0] || '').trim()
        } catch (error) {
            alert(t('alert.invalidTitleRegex'))
            return
        }
    }
    if (artistRegex) {
        try {
            const match = fileName.match(new RegExp(artistRegex))
            if (match) artist = (match[1] || match[0] || '').trim()
        } catch (error) {
            alert(t('alert.invalidArtistRegex'))
            return
        }
    }
    persistLyricsSearchRegexCache()
    if (!title && !artist && fileName.includes(' - ')) {
        const parts = fileName.split(' - ')
        title = parts[0].trim()
        let rest = parts.slice(1).join(' - ').trim()
        rest = rest.replace(/\s*_\s*/g, ' ').replace(/\s+/g, ' ').trim()
        artist = rest
    }
    document.getElementById('lyricsSearchTitle').value = title
    document.getElementById('lyricsSearchArtist').value = artist
    const kwParts = [title, artist].filter(Boolean)
    if (kwParts.length) {
        document.getElementById('lyricsSearchKeyword').value = kwParts.join(' ')
    }
    if (title && artist) {
        setLyricsSearchStatus('success', t('lyricsSearch.parsedFromFile'))
        if (autoMatch) {
            performLyricsMatch(title, artist)
        }
    } else {
        setLyricsSearchStatus('info', t('lyricsSearch.parsePartial'))
    }
}

function persistLyricsSearchRegexCache() {
    const titleRegex = document.getElementById('lyricsSearchTitleRegex').value.trim()
    const artistRegex = document.getElementById('lyricsSearchArtistRegex').value.trim()
    localStorage.setItem(LYRICS_SEARCH_REGEX_KEYS.title, titleRegex)
    localStorage.setItem(LYRICS_SEARCH_REGEX_KEYS.artist, artistRegex)
}

function loadLyricsSearchRegexCache() {
    const cachedTitle = localStorage.getItem(LYRICS_SEARCH_REGEX_KEYS.title) || ''
    const cachedArtist = localStorage.getItem(LYRICS_SEARCH_REGEX_KEYS.artist) || ''
    document.getElementById('lyricsSearchTitleRegex').value = cachedTitle
    document.getElementById('lyricsSearchArtistRegex').value = cachedArtist
}

function performLyricsMatchFromInputs() {
    const title = document.getElementById('lyricsSearchTitle').value.trim()
    const artist = document.getElementById('lyricsSearchArtist').value.trim()
    if (title && artist) {
        performLyricsMatch(title, artist)
        return
    }
    alert(t('alert.fillTitleAndArtist'))
}

function performLyricsSearch() {
    let keyword = document.getElementById('lyricsSearchKeyword').value.trim()
    if (!keyword) {
        const title = document.getElementById('lyricsSearchTitle').value.trim()
        const artist = document.getElementById('lyricsSearchArtist').value.trim()
        if (title || artist) {
            keyword = `${title} ${artist}`.trim()
            document.getElementById('lyricsSearchKeyword').value = keyword
        }
    }
    if (!keyword) {
        alert(t('alert.fillKeyword'))
        return
    }
    performLyricsKeywordSearch(keyword)
}

function performLyricsMatch(title, artist) {
    lyricsSearchFlow.isMatching = true
    updateLyricsSearchActionButtons()
    setLyricsSearchStatus('loading', t('lyricsSearch.matching'))
    const params = new URLSearchParams({ title, artist })
    fetch(`/lddc/match_lyrics?${params.toString()}`)
        .then(async response => {
            const textBody = await response.text()
            let data = null
            try {
                data = textBody ? JSON.parse(textBody) : null
            } catch (e) {
                throw new Error(textBody || `HTTP ${response.status}`)
            }
            if (!response.ok) {
                throw new Error((data && data.message) || textBody || `HTTP ${response.status}`)
            }
            return data
        })
        .then(data => {
            if (data.status !== 'success') {
                setLyricsSearchStatus('error', t('lyricsSearch.matchFailed', { msg: data.message || t('lyricsSearch.unknownError') }))
                return
            }
            latestLyricsSearchResult = data
            setLyricsSearchPreview(data.lyrics_lys || '', data.translation_lrc || '')
            setLyricsSearchStatus('success', t('lyricsSearch.matchComplete'))
        })
        .catch(error => {
            setLyricsSearchStatus('error', t('lyricsSearch.matchFailed', { msg: formatFetchError(error) }))
        })
        .finally(() => {
            lyricsSearchFlow.isMatching = false
            updateLyricsSearchActionButtons()
        })
}

function normalizeLddcSearchPayload(data) {
    if (data == null || typeof data !== 'object') {
        return { errorMessage: t('lyricsSearch.searchFormatError'), results: [] }
    }
    if (data.status === 'error') {
        return { errorMessage: data.message || t('lyricsSearch.unknownError'), results: [] }
    }
    const raw = data.results
    if (Array.isArray(raw)) {
        return { errorMessage: null, results: raw }
    }
    if (raw && typeof raw === 'object' && Array.isArray(raw.items)) {
        return { errorMessage: null, results: raw.items }
    }
    if (Array.isArray(data)) {
        return { errorMessage: null, results: data }
    }
    console.warn('[lyricsSearch] unexpected /lddc/search payload', data)
    return { errorMessage: null, results: [] }
}

function highlightLyricsSearchResultCards() {
    const container = document.getElementById('lyricsSearchResults')
    if (!container) return
    container.querySelectorAll('.lyrics-search-card').forEach((el, i) => {
        el.classList.toggle('is-selected', i === lyricsSearchFlow.selectedIndex)
    })
}

function renderLyricsSearchResults(results) {
    const container = document.getElementById('lyricsSearchResults')
    if (!container) return
    container.innerHTML = ''
    lyricsSearchFlow.results = Array.isArray(results) ? results : []
    if (!results || results.length === 0) {
        const empty = document.createElement('div')
        empty.className = 'lyrics-search-placeholder'
        empty.textContent = t('lyricsSearch.resultsEmpty')
        container.appendChild(empty)
        return
    }
    try {
        results.forEach((item, index) => {
            const card = document.createElement('button')
            card.type = 'button'
            card.className = 'lyrics-search-card'
            card.setAttribute('role', 'option')
            const titleText = item.title != null ? String(item.title) : ''
            const artistText = Array.isArray(item.artist) ? item.artist.filter(Boolean).join(', ') : (item.artist != null ? String(item.artist) : '')
            const sourceText = item.source != null ? String(item.source) : t('lyricsSearch.unknownSource')
            const titleEl = document.createElement('div')
            titleEl.className = 'lyrics-search-card-title'
            titleEl.textContent = titleText || t('lyricsSearch.unknownError')
            const metaEl = document.createElement('div')
            metaEl.className = 'lyrics-search-card-meta'
            metaEl.textContent = artistText
            const srcEl = document.createElement('div')
            srcEl.className = 'lyrics-search-card-source'
            srcEl.textContent = sourceText
            card.appendChild(titleEl)
            card.appendChild(metaEl)
            card.appendChild(srcEl)
            card.addEventListener('click', () => {
                if (!item.song_info_json) {
                    alert(t('alert.invalidSongInfo'))
                    return
                }
                lyricsSearchFlow.selectedIndex = index
                highlightLyricsSearchResultCards()
                fetchLyricsById(item.song_info_json)
            })
            container.appendChild(card)
        })
        highlightLyricsSearchResultCards()
    } catch (error) {
        container.innerHTML = ''
        const err = document.createElement('div')
        err.className = 'lyrics-search-placeholder lyrics-search-placeholder--error'
        err.textContent = t('lyricsSearch.renderFailed') + formatFetchError(error)
        container.appendChild(err)
    }
}

function performLyricsKeywordSearch(keyword) {
    lyricsSearchFlow.isSearching = true
    lyricsSearchFlow.selectedIndex = -1
    updateLyricsSearchActionButtons()
    setLyricsSearchStatus('loading', t('lyricsSearch.searching'))
    const loading = document.createElement('div')
    loading.className = 'lyrics-search-placeholder'
    loading.textContent = t('lyricsSearch.resultsLoading')
    const resultsEl = document.getElementById('lyricsSearchResults')
    if (resultsEl) {
        resultsEl.innerHTML = ''
        resultsEl.appendChild(loading)
    }
    fetch(`/lddc/search?keyword=${encodeURIComponent(keyword)}`)
        .then(async response => {
            const textBody = await response.text()
            let data = null
            try {
                data = textBody ? JSON.parse(textBody) : null
            } catch (e) {
                throw new Error(textBody || `HTTP ${response.status}`)
            }
            if (!response.ok) {
                throw new Error((data && data.message) || textBody || `HTTP ${response.status}`)
            }
            return data
        })
        .then(data => {
            window.lastLddcSearch = data
            const { errorMessage, results } = normalizeLddcSearchPayload(data)
            if (errorMessage) {
                if (resultsEl) {
                    resultsEl.innerHTML = ''
                    const errBox = document.createElement('div')
                    errBox.className = 'lyrics-search-placeholder lyrics-search-placeholder--error'
                    errBox.textContent = errorMessage
                    resultsEl.appendChild(errBox)
                }
                setLyricsSearchStatus('error', errorMessage)
                return
            }
            renderLyricsSearchResults(results)
            setLyricsSearchStatus('success', t('lyricsSearch.searchComplete', { count: results.length }))
        })
        .catch(error => {
            const msg = formatFetchError(error)
            if (resultsEl) {
                resultsEl.innerHTML = ''
                const errBox = document.createElement('div')
                errBox.className = 'lyrics-search-placeholder lyrics-search-placeholder--error'
                errBox.textContent = t('lyricsSearch.searchFailed', { msg })
                resultsEl.appendChild(errBox)
            }
            setLyricsSearchStatus('error', t('lyricsSearch.searchFailed', { msg }))
        })
        .finally(() => {
            lyricsSearchFlow.isSearching = false
            updateLyricsSearchActionButtons()
        })
}

function fetchLyricsById(songInfoJson) {
    if (!songInfoJson) {
        alert(t('alert.invalidSongInfo'))
        return
    }
    lyricsSearchFlow.isFetchingLyrics = true
    updateLyricsSearchActionButtons()
    setLyricsSearchStatus('loading', t('lyricsSearch.fetchingDetail'))
    fetch('/lddc/get_lyrics_by_id', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ song_info_json: songInfoJson })
    })
        .then(async response => {
            const textBody = await response.text()
            let data = null
            try {
                data = textBody ? JSON.parse(textBody) : null
            } catch (e) {
                throw new Error(textBody || `HTTP ${response.status}`)
            }
            if (!response.ok) {
                throw new Error((data && data.message) || textBody || `HTTP ${response.status}`)
            }
            return data
        })
        .then(data => {
            if (data.status !== 'success') {
                setLyricsSearchStatus('error', t('lyricsSearch.fetchFailed', { msg: data.message || t('lyricsSearch.unknownError') }))
                return
            }
            latestLyricsSearchResult = data
            setLyricsSearchPreview(data.lyrics_lys || '', data.translation_lrc || '')
            setLyricsSearchStatus('success', t('lyricsSearch.fetchComplete'))
        })
        .catch(error => {
            setLyricsSearchStatus('error', t('lyricsSearch.fetchFailed', { msg: formatFetchError(error) }))
        })
        .finally(() => {
            lyricsSearchFlow.isFetchingLyrics = false
            updateLyricsSearchActionButtons()
        })
}

function applyLyricsSearchResult() {
    if (!hasLyricsSearchApplicablePreview()) {
        alert(t('alert.noLyricsToApply'))
        return
    }
    const lyricsText = getSearchLyricsContent()
    const translationText = getSearchTranslationContent()
    setLyricsContent(lyricsText)
    setTranslationContent(translationText)
    updateFontFamilyNotice(lyricsText)
    setLyricsSearchStatus('success', t('lyricsSearch.applied'))
    updateLyricsSearchActionButtons()
}

async function copyTTMLForAMLL() {
    const lyricsPath = document.getElementById('lyricsPath').value.trim();
    const lyricsContent = getLyricsContent();
    const pathExtension = (lyricsPath.match(/\.[^.]+$/) || [])[0]?.toLowerCase() || '';
    const detectedExtension = detectLyricsExtension(lyricsContent);
    const isTtml = pathExtension === '.ttml' || detectedExtension === '.ttml';

    if (isTtml) {
        if (!lyricsContent.trim()) {
            alert(t('alert.emptyLyricsNoTTML'));
            return;
        }
        try {
            await navigator.clipboard.writeText(lyricsContent);
            alert(t('alert.copiedTTML'));
            window.open('https://editor.amll.dev/', '_blank');
        } catch (error) {
            alert(t('alert.copyFailedWithMsg') + (error.message || error));
        }
        return;
    }

    if (!lyricsPath) {
        alert(t('alert.fillLyricsPath'));
        return;
    }

    if (pathExtension !== '.lys' && pathExtension !== '.lrc') {
        alert(t('alert.onlyLysLrcToTtml'));
        return;
    }

    try {
        const translationPath = document.getElementById('translationPath').value;
        const response = await fetch('/convert_to_ttml', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: lyricsPath, translationPath })
        });
        const data = await response.json();
        if (data.status !== 'success' || !data.ttmlPath) {
            alert(t('alert.convertFailedPrefix') + (data.message || '无法生成TTML'));
            return;
        }

        const ttmlResponse = await fetch('/get_lyrics', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: data.ttmlPath })
        });
        const ttmlData = await ttmlResponse.json();
        if (ttmlData.status !== 'success') {
            alert(t('alert.readTTMLFailedWithMsg') + (ttmlData.message || 'Unknown error'));
            return;
        }

        await navigator.clipboard.writeText(ttmlData.content || '');
        alert(t('alert.copiedTTML'));
        window.open('https://editor.amll.dev/', '_blank');
    } catch (error) {
        alert(t('alert.convertOrCopyFailedPrefix') + (error.message || error));
    }
}

function saveLyrics(index) {
    const path = index === 0 ?
        document.getElementById('lyricsPath').value :
        document.getElementById('translationPath').value
    const content = index === 0 ?
        getLyricsContent() :
        getTranslationContent()
    const trimmedPath = (path || '').trim()

    // 验证路径
    if (!trimmedPath || trimmedPath === '.' || trimmedPath === './') {
        if (index === 0 || index === 1) {
            return updateLyricsPath(index).then(() => Promise.resolve())
        }
        return Promise.reject(new Error('无效的文件路径'));
    }

    return new Promise((resolve, reject) => {
        const fullPath = normalizeSongsUrl(trimmedPath)

        fetch('/save_lyrics', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            path: fullPath,
            content: content,
            jsonFile: currentJsonFile || undefined
        })
        })
            .then(response => response.json())
            .then(data => {
                if (data.status === 'success') {
                    resolve()
                } else if (data.status === 'warning') {
                    alert(data.message)
                    resolve()  // 仍然resolve因为文件已经保存成功
                } else {
                    reject(new Error(data.message || '保存失败'))
                }
            })
            .catch(error => reject(error))
    })
}

async function saveAllLyrics() {
    const saveBtn = document.querySelector('.save-all-btn');
    saveBtn.disabled = true;
    saveBtn.textContent = t('status.saving');

    try {
        const lyricsPath = document.getElementById('lyricsPath').value;
        const translationPath = document.getElementById('translationPath').value;
        const trimmedLyricsPath = (lyricsPath || '').trim();

        // 验证歌词路径
        if (!trimmedLyricsPath || trimmedLyricsPath === '.' || trimmedLyricsPath === './') {
            await updateLyricsPath(0);
        } else {
            await updateLyricsPath(0);
            await saveLyrics(0);
        }

        // 验证翻译路径（如果有）
        if (translationPath && (translationPath === '.' || translationPath === './')) {
            throw new Error('翻译文件路径无效');
        }

        // 只有在有翻译路径时才保存翻译
        if (translationPath) {
            await updateLyricsPath(1);
            await saveLyrics(1);
        }

        alert(t('alert.allSaveSuccess'));
    } catch (err) {
        alert(t('alert.saveErrorPrefix') + err.message);
    } finally {
        saveBtn.disabled = false;
        saveBtn.textContent = '💾 ' + t('lyrics.saveAll');
    }
}

function updateLyricsPath(index) {
    return new Promise((resolve, reject) => {
        const pathInput = index === 0 ?
            document.getElementById('lyricsPath') :
            document.getElementById('translationPath')
        const newPath = (pathInput.value || '').trim()

        fetch('/update_file_path', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                jsonFile: currentJsonFile,
                fileType: 'lyrics',
                newPath: newPath,
                index: index,
                clear: !newPath
            })
        })
            .then(response => response.json())
            .then(data => {
                if (data.status === 'success') {
                    resolve()
                } else {
                    reject(new Error('路径更新失败'))
                }
            })
            .catch(error => reject(error))
    })
}

function closeLyricsModal() {
    document.getElementById('lyricsModal').style.display = 'none'
    clearExtraPrompt()
    const extraContainer = document.getElementById('extraPromptContainer')
    const toggleBtn = document.getElementById('extraPromptToggleBtn')
    if (extraContainer) extraContainer.style.display = 'none'
    if (toggleBtn) toggleBtn.innerHTML = '➕ ' + t('lyrics.extraPrompt')
    unlockPageScrollForModal()
    location.reload()
}

function toggleExtraPrompt() {
    const container = document.getElementById('extraPromptContainer')
    const toggleBtn = document.getElementById('extraPromptToggleBtn')
    if (container.style.display === 'none') {
        container.style.display = 'block'
        toggleBtn.innerHTML = '➖ ' + t('lyrics.extraPrompt')
    } else {
        container.style.display = 'none'
        toggleBtn.innerHTML = '➕ ' + t('lyrics.extraPrompt')
    }
}

function clearExtraPrompt() {
    const transInput = document.getElementById('extraTranslationPrompt')
    const thinkInput = document.getElementById('extraThinkingPrompt')
    if (transInput) transInput.value = ''
    if (thinkInput) thinkInput.value = ''
}

function getExtraTranslationPrompt() {
    const input = document.getElementById('extraTranslationPrompt')
    return input ? input.value.trim() : ''
}

function getExtraThinkingPrompt() {
    const input = document.getElementById('extraThinkingPrompt')
    return input ? input.value.trim() : ''
}

function editMusicPath(path, jsonFile) {
    currentMusicPath = path
    currentMusicJsonFile = jsonFile
    const modal = document.getElementById('musicPathModal')
    const currentPathInput = document.getElementById('musicPath')
    const newPathInput = document.getElementById('newMusicPath')

    // 显示当前路径（去掉前缀）
    currentPathInput.value = stripSongsPrefix(path)
    newPathInput.value = currentPathInput.value

    modal.style.display = 'block'
}

function updateMusicPath() {
    const newPath = document.getElementById('newMusicPath').value.trim()

    if (!newPath) {
        alert(t('alert.enterNewPath'))
        return
    }

    fetch('/update_file_path', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            jsonFile: currentMusicJsonFile,
            fileType: 'music',
            newPath: newPath
        })
    })
        .then(response => response.json())
        .then(data => {
            if (data.status === 'success') {
                closeMusicPathModal()
                location.reload()
            } else {
                alert(t('alert.updateFailedPrefix') + data.message)
            }
        })
        .catch(error => {
            alert(t('alert.updateFailedPrefix') + error)
        })
}

function closeMusicPathModal() {
    document.getElementById('musicPathModal').style.display = 'none'
}

const VALID_IMAGE_EXTENSIONS = ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.apng']
const VALID_VIDEO_EXTENSIONS = ['.mp4', '.webm', '.ogg', '.m4v', '.mov']
const VALID_BACKGROUND_EXTENSIONS = [
    '.jpg', '.jpeg', '.png', '.gif', '.webp', '.apng',
    '.mp4', '.webm', '.ogg', '.m4v', '.mov'
]

function hasValidImageExtension(path) {
    if (!path) {
        return false
    }
    const actualPath = path.startsWith('http') ? getPathFromUrl(path) : path
    const lower = actualPath.toLowerCase()
    return VALID_IMAGE_EXTENSIONS.some(ext => lower.endsWith(ext))
}

function hasValidVideoExtension(path) {
    if (!path) {
        return false
    }
    const actualPath = path.startsWith('http') ? getPathFromUrl(path) : path
    const lower = actualPath.toLowerCase()
    return VALID_VIDEO_EXTENSIONS.some(ext => lower.endsWith(ext))
}

function hasValidBackgroundExtension(path) {
    if (!path) {
        return false
    }
    const actualPath = path.startsWith('http') ? getPathFromUrl(path) : path
    const lower = actualPath.toLowerCase()
    return VALID_BACKGROUND_EXTENSIONS.some(ext => lower.endsWith(ext))
}

function editImagePath(path, jsonFile) {
    currentImagePath = path
    currentImageJsonFile = jsonFile
    const modal = document.getElementById('imagePathModal')
    const currentPathInput = document.getElementById('imagePath')
    const newPathInput = document.getElementById('newImagePath')
    const backgroundPathInput = document.getElementById('backgroundPath')
    const newBackgroundPathInput = document.getElementById('newBackgroundPath')
    const dynamicCoverPathInput = document.getElementById('dynamicCoverPath')
    const dynamicCoverPosterPathInput = document.getElementById('dynamicCoverPosterPath')

    // 显示当前路径（去掉前缀）
    currentPathInput.value = stripSongsPrefix(path)
    newPathInput.value = currentPathInput.value

    // 获取当前背景图片路径和动态封面路径
    fetch('/get_json_data?filename=' + encodeURIComponent(jsonFile), { cache: 'no-store' })
        .then(response => response.json())
        .then(data => {
            if (data.status === 'success') {
                const meta = data.jsonData.meta || {}
                const backgroundPath = meta['Background-image'] || ''
                backgroundPathInput.value = backgroundPath
                const editableBackgroundPath = backgroundPath === '!' ? '' : stripSongsPrefix(backgroundPath)
                newBackgroundPathInput.value = editableBackgroundPath

                // 加载动态封面路径
                const dynamicCoverPath = meta['dynamicCoverSrc'] || ''
                dynamicCoverPathInput.value = dynamicCoverPath === '!' ? '' : stripSongsPrefix(dynamicCoverPath)

                // 加载海报路径
                const dynamicCoverPosterPath = meta['dynamicCoverPoster'] || ''
                dynamicCoverPosterPathInput.value = dynamicCoverPosterPath === '!' ? '' : stripSongsPrefix(dynamicCoverPosterPath)

                // 更新预览
                updateDynamicCoverPreview()
            }
        })

    modal.style.display = 'block'
}

function updateImagePath() {
    const newPath = document.getElementById('newImagePath').value.trim()

    if (!newPath) {
        alert(t('alert.enterNewPath'))
        return
    }

    // 检查文件扩展名
    if (!hasValidImageExtension(newPath)) {
        alert(t('alert.enterValidImageDetailed'))
        return
    }

    fetch('/update_file_path', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            jsonFile: currentImageJsonFile,
            fileType: 'image',
            newPath: newPath
        })
    })
        .then(response => response.json())
        .then(data => {
            if (data.status === 'success') {
                closeImagePathModal()
                location.reload()
            } else {
                alert(t('alert.updateFailedPrefix') + data.message)
            }
        })
        .catch(error => {
            alert(t('alert.updateFailedPrefix') + error)
        })
}

function updateBackgroundPath() {
    const rawPath = document.getElementById('newBackgroundPath').value.trim()
    const newPath = rawPath === '!' ? '' : stripSongsPrefix(rawPath)

    if (newPath) {
        // 检查文件扩展名
        if (!hasValidBackgroundExtension(newPath)) {
            alert(t('alert.enterValidBackgroundDetailed'))
            return
        }
    }

    fetch('/update_file_path', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            jsonFile: currentImageJsonFile,
            fileType: 'background',
            newPath: newPath
        })
    })
        .then(response => response.json())
        .then(data => {
            if (data.status === 'success') {
                closeImagePathModal()
                location.reload()
            } else {
                alert(t('alert.updateFailedPrefix') + data.message)
            }
        })
        .catch(error => {
            alert(t('alert.updateFailedPrefix') + error)
        })
}

function updateDynamicCoverPreview() {
    const dynamicPath = document.getElementById('dynamicCoverPath').value.trim()
    const posterPath = document.getElementById('dynamicCoverPosterPath').value.trim()
    const container = document.getElementById('dynamicCoverPreviewContainer')
    const imgEl = document.getElementById('dynamicCoverPreviewImg')
    const videoEl = document.getElementById('dynamicCoverPreviewVideo')
    const placeholderEl = document.getElementById('dynamicCoverPreviewPlaceholder')

    // 重置所有元素
    imgEl.style.display = 'none'
    imgEl.src = ''
    videoEl.style.display = 'none'
    videoEl.src = ''
    videoEl.poster = ''
    placeholderEl.style.display = 'none'

    if (!dynamicPath) {
        container.style.display = 'none'
        return
    }

    container.style.display = 'block'

    const fullDynamicUrl = resolveCoverUrl(dynamicPath)
    const fullPosterUrl = posterPath ? resolveCoverUrl(posterPath) : ''

    if (isVideoFile(dynamicPath)) {
        // 视频类型
        videoEl.src = fullDynamicUrl
        if (fullPosterUrl) {
            videoEl.poster = fullPosterUrl
        }
        videoEl.style.display = 'block'
        videoEl.load()
        videoEl.play().catch(() => {
            // 自动播放被阻止，显示海报或占位符
            if (!fullPosterUrl) {
                videoEl.style.display = 'none'
                placeholderEl.textContent = t('status.videoPlaceholder')
                placeholderEl.style.display = 'block'
            }
        })
    } else if (isAnimatedImage(dynamicPath)) {
        // 动图类型
        imgEl.src = fullDynamicUrl
        imgEl.style.display = 'block'
    } else {
        // 普通图片
        imgEl.src = fullDynamicUrl
        imgEl.style.display = 'block'
    }
}

function updateDynamicCoverPath() {
    const dynamicPath = document.getElementById('dynamicCoverPath').value.trim()
    const posterPath = document.getElementById('dynamicCoverPosterPath').value.trim()

    // 验证动态封面路径
    if (dynamicPath) {
        const isValidDynamic = hasValidImageExtension(dynamicPath) || hasValidVideoExtension(dynamicPath)
        if (!isValidDynamic) {
            alert(t('alert.enterValidDynamicCoverDetailed'))
            return
        }
    }

    // 验证海报路径
    if (posterPath && !hasValidImageExtension(posterPath)) {
        alert(t('alert.enterValidPosterDetailed'))
        return
    }

    const requests = []

    // 保存动态封面
    if (dynamicPath || dynamicPath === '') {
        requests.push(
            fetch('/update_file_path', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    jsonFile: currentImageJsonFile,
                    fileType: 'dynamicCover',
                    newPath: stripSongsPrefix(dynamicPath)
                })
            })
        )
    }

    // 保存海报
    if (posterPath || posterPath === '') {
        requests.push(
            fetch('/update_file_path', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    jsonFile: currentImageJsonFile,
                    fileType: 'dynamicCoverPoster',
                    newPath: stripSongsPrefix(posterPath)
                })
            })
        )
    }

    Promise.all(requests)
        .then(responses => Promise.all(responses.map(r => r.json())))
        .then(results => {
            const allSuccess = results.every(r => r.status === 'success')
            if (allSuccess) {
                closeImagePathModal()
                location.reload()
            } else {
                const errorMsg = results.find(r => r.status !== 'success')?.message || '更新失败'
                alert(t('alert.updateFailedPrefix') + errorMsg)
            }
        })
        .catch(error => {
            alert(t('alert.updateFailedPrefix') + error)
        })
}

async function saveAllImagePaths() {
    const albumPathInput = document.getElementById('newImagePath')
    const backgroundPathInput = document.getElementById('newBackgroundPath')
    const dynamicCoverPathInput = document.getElementById('dynamicCoverPath')
    const dynamicCoverPosterPathInput = document.getElementById('dynamicCoverPosterPath')

    const albumPath = albumPathInput.value.trim()
    const rawBackgroundPath = backgroundPathInput.value.trim()
    const rawDynamicCoverPath = dynamicCoverPathInput.value.trim()
    const rawDynamicCoverPosterPath = dynamicCoverPosterPathInput.value.trim()

    let normalizedAlbumPath = ''
    if (albumPath && albumPath !== '!') {
        if (!hasValidImageExtension(albumPath)) {
            alert(t('alert.enterValidAlbumArtDetailed'))
            return
        }
        normalizedAlbumPath = stripSongsPrefix(albumPath)
    }

    let normalizedBackgroundPath = ''
    if (rawBackgroundPath && rawBackgroundPath !== '!') {
        if (!hasValidBackgroundExtension(rawBackgroundPath)) {
            alert(t('alert.enterValidBackgroundDetailed'))
            return
        }
        normalizedBackgroundPath = stripSongsPrefix(rawBackgroundPath)
    }

    // 验证动态封面路径
    let normalizedDynamicCoverPath = ''
    if (rawDynamicCoverPath && rawDynamicCoverPath !== '!') {
        const isValidDynamic = hasValidImageExtension(rawDynamicCoverPath) || hasValidVideoExtension(rawDynamicCoverPath)
        if (!isValidDynamic) {
            alert(t('alert.enterValidDynamicCoverDetailed'))
            return
        }
        normalizedDynamicCoverPath = stripSongsPrefix(rawDynamicCoverPath)
    }

    // 验证海报路径
    let normalizedDynamicCoverPosterPath = ''
    if (rawDynamicCoverPosterPath && rawDynamicCoverPosterPath !== '!') {
        if (!hasValidImageExtension(rawDynamicCoverPosterPath)) {
            alert(t('alert.enterValidPosterDetailed'))
            return
        }
        normalizedDynamicCoverPosterPath = stripSongsPrefix(rawDynamicCoverPosterPath)
    }

    try {
        const requests = []

        // 保存专辑图
        requests.push(
            fetch('/update_file_path', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    jsonFile: currentImageJsonFile,
                    fileType: 'image',
                    newPath: normalizedAlbumPath
                })
            })
        )

        // 保存背景图
        requests.push(
            fetch('/update_file_path', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    jsonFile: currentImageJsonFile,
                    fileType: 'background',
                    newPath: normalizedBackgroundPath
                })
            })
        )

        // 保存动态封面（总是发送请求，空值会清空字段）
        requests.push(
            fetch('/update_file_path', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    jsonFile: currentImageJsonFile,
                    fileType: 'dynamicCover',
                    newPath: normalizedDynamicCoverPath
                })
            })
        )

        // 保存海报（总是发送请求，空值会清空字段）
        requests.push(
            fetch('/update_file_path', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    jsonFile: currentImageJsonFile,
                    fileType: 'dynamicCoverPoster',
                    newPath: normalizedDynamicCoverPosterPath
                })
            })
        )

        const responses = await Promise.all(requests)
        const results = await Promise.all(responses.map(r => r.json()))

        const allSuccess = results.every(r => r.status === 'success')
        if (!allSuccess) {
            const errorMsg = results.find(r => r.status !== 'success')?.message || '保存失败'
            throw new Error(errorMsg)
        }

        closeImagePathModal()
        location.reload()
    } catch (error) {
        alert(t('alert.saveFailedPrefix') + (error.message || error))
    }
}

function closeImagePathModal() {
    document.getElementById('imagePathModal').style.display = 'none'
}

function convertTTML() {
    const lyricsPath = document.getElementById('lyricsPath').value.trim();
    if (!lyricsPath || !lyricsPath.toLowerCase().endsWith('.ttml')) {
        alert(t('alert.fillTTMLPath'));
        return;
    }

    fetch('/convert_ttml_by_path', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: lyricsPath })
    })
    .then(response => response.json())
    .then(data => {
        const lyricsEditor = document.getElementById('lyricsEditor');
        if (data.status === 'success') {
            document.getElementById('lyricsPath').value = stripSongsPrefix(data.lyricPath);
            if (data.transPath) {
                document.getElementById('translationPath').value = stripSongsPrefix(data.transPath);
            }
            // 自动获取并应用歌词内容
            fetch('/get_lyrics', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ path: data.lyricPath })
            })
            .then(response => response.json())
            .then(lyricsData => {
                if (lyricsData.status === 'success') {
                    lyricsEditor.value = lyricsData.content;
                }
            });
            // 自动获取并应用翻译内容
            if (data.transPath) {
                fetch('/get_lyrics', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ path: data.transPath })
                })
                .then(response => response.json())
                .then(transData => {
                    if (transData.status === 'success') {
                        document.getElementById('translationEditor').value = transData.content;
                    }
                });
            }
            alert(t('alert.ttmlAutoConvertSaveAll'));
        } else {
            alert(t('alert.autoConvertFailedPrefix') + data.message);
        }
    })
    .catch(error => {
        alert(t('alert.autoConvertFailedPrefix') + error);
    });
}

function convertToTTML() {
    const lyricsPath = document.getElementById('lyricsPath').value.trim();
    if (!lyricsPath) {
        alert(t('alert.fillLyricsPathSimple'));
        return;
    }

    // 检查文件扩展名
    const fileExt = lyricsPath.toLowerCase().substring(lyricsPath.lastIndexOf('.'));
    if (fileExt !== '.lys' && fileExt !== '.lrc') {
        alert(t('alert.onlyLysLrc'));
        return;
    }

    const translationPath = document.getElementById('translationPath').value;
    fetch('/convert_to_ttml', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: lyricsPath, translationPath })
    })
    .then(response => response.json())
    .then(data => {
        if (data.status === 'success') {
            // 更新歌词路径为TTML文件
            document.getElementById('lyricsPath').value = stripSongsPrefix(data.ttmlPath);

            // 自动获取并应用转换后的TTML内容
            fetch('/get_lyrics', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ path: data.ttmlPath })
            })
            .then(response => response.json())
            .then(lyricsData => {
                if (lyricsData.status === 'success') {
                    document.getElementById('lyricsEditor').value = lyricsData.content;
                }
            });

            alert(t('alert.lysToTtmlSuccess'));
        } else {
            alert(t('alert.convertFailedPrefix') + data.message);
        }
    })
    .catch(error => {
        alert(t('alert.convertFailedPrefix') + error);
    });
}

let amllCardReady = false;

function selectCreateMode(mode) {
    if (mode === 'amll' && !amllCardReady) {
        return;
    }
    createMode = mode;
    const amllCard = document.getElementById('amllSourceCard');
    const manualSection = document.getElementById('manualCreateSection');
    if (amllCard) {
        amllCard.classList.toggle('selected', mode === 'amll' && amllCardReady);
    }
    if (manualSection) {
        manualSection.classList.toggle('selected', mode === 'manual');
    }
}

function renderAmllPreview(lines) {
    if (!Array.isArray(lines) || lines.length === 0) {
        return t('create.noLyricsCreatable');
    }
    const previewLines = [];
    lines.slice(0, 4).forEach((line, idx) => {
        const syllables = Array.isArray(line.syllables) ? line.syllables : [];
        const text = syllables.map(s => s.text || '').join('') || line.translatedLyric || '';
        previewLines.push(`${idx + 1}. ${text || '（空行）'}`);
    });
    if (lines.length > 4) {
        previewLines.push(`... 还有 ${lines.length - 4} 行`);
    }
    return previewLines.join('\n');
}

function resolveCoverUrl(raw) {
    if (!raw || raw === '!') return DEFAULT_AMLL_COVER;
    if (raw.startsWith('data:')) return raw;
    let isSongsResource = false;
    try {
        const parsed = new URL(raw);
        isSongsResource = parsed.pathname.startsWith(RESOURCE_CONFIG.songs.path);
    } catch (error) {
        const normalized = raw.replace(/\\/g, '/');
        isSongsResource = normalized.startsWith('songs/') || normalized.startsWith('./songs/') || normalized.startsWith('/songs/');
    }
    if (isSongsResource) {
        return normalizeSongsUrl(raw);
    }
    if (raw.startsWith('http://') || raw.startsWith('https://')) return raw;
    if (raw.startsWith('//')) return window.location.protocol + raw;
    if (raw.startsWith('/')) return window.location.origin + raw;
    return raw;
}

async function refreshAmllSnapshot(force = false) {
    const statusEl = document.getElementById('amllCardStatus');
    const previewEl = document.getElementById('amllLyricsPreview');
    const cardEl = document.getElementById('amllSourceCard');
    const coverEl = document.getElementById('amllCoverPreview');
    if (statusEl) statusEl.textContent = t('status.refreshing');
    amllCardReady = false;
    try {
        const resp = await fetch('/amll/state' + (force ? `?_t=${Date.now()}` : ''));
        if (!resp.ok) {
            throw new Error('请求失败');
        }
        const data = await resp.json();
        amllSnapshot = data;
        const song = data.song || {};
        const lines = data.lines || [];
        const coverUrl = resolveCoverUrl(
            song.cover_data_url ||
            song.cover_file_url ||
            song.cover ||
            song.albumImgSrc ||
            song.coverUrl ||
            DEFAULT_AMLL_COVER
        );
        if (coverEl) {
            coverEl.src = coverUrl || DEFAULT_AMLL_COVER;
        }
        const titleEl = document.getElementById('amllSongTitleDisplay');
        const artistsEl = document.getElementById('amllSongArtistsDisplay');
        const albumEl = document.getElementById('amllSongAlbumDisplay');
        const summaryEl = document.getElementById('amllLyricsSummary');
        if (titleEl) titleEl.textContent = t('create.songTitleDisplay') + (song.musicName || t('create.notProvided'));
        if (artistsEl) artistsEl.textContent = t('create.artistDisplay') + ((song.artists || []).join(' / ') || t('create.notProvided'));
        if (albumEl) albumEl.textContent = t('create.albumDisplay') + (song.album || t('create.notProvided'));
        if (summaryEl) summaryEl.textContent = t('create.lyricsDisplay') + (lines.length ? `${lines.length} ${t('batch.lineUnit')}` : t('create.noLyricsCreatable'));
        if (previewEl) previewEl.textContent = renderAmllPreview(lines);
        amllCardReady = Boolean(String(song.musicName || '').trim());
        if (statusEl) statusEl.textContent = amllCardReady
            ? (Array.isArray(lines) && lines.length > 0 ? t('create.available') : t('create.availableNoLyrics'))
            : t('create.waitingDataStatus');
        if (cardEl) cardEl.classList.toggle('disabled', !amllCardReady);
        if (!amllCardReady && createMode === 'amll') {
            selectCreateMode('manual');
        }
    } catch (error) {
        if (statusEl) statusEl.textContent = t('status.fetchFailed');
        if (previewEl) previewEl.textContent = t('status.amllFetchFailed');
        if (cardEl) cardEl.classList.add('disabled');
        amllSnapshot = null;
        amllCardReady = false;
    }
}

async function showCreateModal() {
    document.getElementById('createJsonModal').style.display = 'block'
    document.getElementById('songTitle').value = ''
    document.getElementById('songArtists').value = ''
    createMode = ''
    document.getElementById('amllSourceCard')?.classList.remove('selected')
    document.getElementById('manualCreateSection')?.classList.remove('selected')
    updateCreateFilenamePreview()
    await refreshAmllSnapshot()
    if (!createMode) {
        selectCreateMode(amllCardReady ? 'amll' : 'manual')
    } else {
        selectCreateMode(createMode)
    }
}

function closeCreateModal() {
    document.getElementById('createJsonModal').style.display = 'none'
}

const WINDOWS_RESERVED_FILENAME_STEMS = new Set([
    'CON', 'PRN', 'AUX', 'NUL',
    'COM1', 'COM2', 'COM3', 'COM4', 'COM5', 'COM6', 'COM7', 'COM8', 'COM9',
    'LPT1', 'LPT2', 'LPT3', 'LPT4', 'LPT5', 'LPT6', 'LPT7', 'LPT8', 'LPT9'
])

function sanitizeWindowsStrictFilename(value) {
    let cleaned = String(value ?? '').replace(/[<>:"/\\|?*\x00-\x1F]/g, '')
    cleaned = cleaned.trim().replace(/[ .]+$/g, '')
    if (!cleaned || cleaned === '.' || cleaned === '..') {
        return ''
    }
    const stem = cleaned.split('.', 1)[0].replace(/[ .]+$/g, '')
    if (WINDOWS_RESERVED_FILENAME_STEMS.has(stem.toUpperCase())) {
        return ''
    }
    return cleaned
}

function buildStrictSongJsonFilename(title, artists) {
    const cleanTitle = sanitizeWindowsStrictFilename(title)
    const cleanArtists = artists
        .map(artist => sanitizeWindowsStrictFilename(artist))
        .filter(artist => artist)

    if (!cleanTitle || cleanArtists.length === 0) {
        return ''
    }

    return sanitizeWindowsStrictFilename(`${cleanTitle} - ${cleanArtists.join(' _ ')}.json`)
}

function renderFilenamePreview(elementId, fileName) {
    const previewEl = document.getElementById(elementId)
    if (!previewEl) {
        return
    }
    previewEl.textContent = fileName
        ? `文件名预览：${fileName}`
        : '文件名预览：清理后为空或命中 Windows 保留名'
}

function updateCreateFilenamePreview() {
    const title = document.getElementById('songTitle')?.value.trim() || ''
    const artists = (document.getElementById('songArtists')?.value || '')
        .split(',')
        .map(artist => artist.trim())
        .filter(artist => artist)
    renderFilenamePreview('createFileNamePreview', buildStrictSongJsonFilename(title, artists))
}

function updateRenameFilenamePreview() {
    const title = document.getElementById('renameSongTitle')?.value.trim() || ''
    const artists = (document.getElementById('renameSongArtists')?.value || '')
        .split(',')
        .map(artist => artist.trim())
        .filter(artist => artist)
    renderFilenamePreview('renameFileNamePreview', buildStrictSongJsonFilename(title, artists))
}

function buildSongJsonData(title, artists, baseName, songFileName) {
    return {
        serial: 123456,
        meta: {
            title: title,
            artists: artists,
            albumImgSrc: `${RESOURCE_CONFIG.songs.base}专辑图.jpg`,
            duration_ms: 0,
            lyrics: '::!::!::!::'
        },
        song: `${RESOURCE_CONFIG.songs.base}${songFileName}`
    }
}

function readManualCreateState() {
    const title = document.getElementById('songTitle').value.trim()
    const artistsInput = document.getElementById('songArtists').value.trim()
    const artists = artistsInput
        .split(',')
        .map(artist => artist.trim())
        .filter(artist => artist)
    return {
        title,
        artistsInput,
        artists,
        hasAnyInput: Boolean(title || artistsInput),
        hasCompleteInput: Boolean(title && artists.length > 0)
    }
}

function hasUsableAmllSource() {
    return Boolean(amllCardReady && amllSnapshot && amllSnapshot.song && String(amllSnapshot.song.musicName || '').trim())
}

function createManualJson(manualState) {
    const fileName = buildStrictSongJsonFilename(manualState.title, manualState.artists)
    if (!fileName) {
        alert(t('alert.filenameEmptyAdjust'))
        return
    }

    const jsonData = buildSongJsonData(manualState.title, manualState.artists, '歌词', '音乐.mp3')

    fetch('/create_json', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            filename: fileName,
            content: jsonData
        })
    })
        .then(response => response.json())
        .then(data => {
            if (data.status === 'success') {
                closeCreateModal()
                loadSongSummaries()
            } else {
                alert(t('alert.createFailedPrefix') + data.message)
            }
        })
        .catch(error => {
            alert(t('alert.createFailedPrefix') + error)
        })
}

function createJsonFile() {
    const manualState = readManualCreateState()
    const amllReady = hasUsableAmllSource()

    if (createMode === 'amll' && amllReady) {
        createFromAmllSource()
        return
    }

    if (manualState.hasCompleteInput) {
        createManualJson(manualState)
        return
    }

    if (manualState.hasAnyInput && createMode === 'manual') {
        alert(t('alert.fillTitleAndSinger'))
        return
    }

    if (amllReady) {
        createFromAmllSource()
        return
    }

    alert(t('alert.fillTitleAndSinger'))
}

async function createFromAmllSource() {
    if (!amllCardReady || !amllSnapshot || !amllSnapshot.song || !String(amllSnapshot.song.musicName || '').trim()) {
        alert(t('alert.amllNotReady'))
        return
    }
    const btn = document.getElementById('createJsonConfirm')
    if (btn) {
        btn.disabled = true
        btn.textContent = t('status.creating')
    }
    try {
        const resp = await fetch('/amll/create_song', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ useTranslation: true })
        })
        if (!resp.ok) {
            throw new Error(`HTTP ${resp.status}`)
        }
        const data = await resp.json()
        if (data.status === 'success') {
            alert(t('create.success') + data.jsonFile)
            closeCreateModal()
            loadSongSummaries()
        } else {
            alert(t('create.failed') + data.message)
        }
    } catch (error) {
        alert(t('create.failed') + error)
    } finally {
        if (btn) {
            btn.disabled = false
            btn.textContent = t('common.create')
        }
    }
}

function getFileBaseName(fileName) {
    return fileName.replace(/\.[^.]+$/, '')
}

function parseTitleArtistFromName(rawName) {
    const baseName = getFileBaseName(rawName).trim()
    if (!baseName) {
        return { title: '', artists: [] }
    }
    const parts = baseName.split(' - ')
    if (parts.length >= 2) {
        const title = parts[0].trim()
        const artistText = parts.slice(1).join(' - ').trim()
        const artists = artistText
            .split(/ _ |,|，/g)
            .map(item => item.trim())
            .filter(item => item)
        return { title: title, artists: artists.length ? artists : [artistText] }
    }
    return { title: baseName, artists: [] }
}

async function handleCreateAudioImport(files, inputEl) {
    const fileInput = inputEl || document.getElementById('audioImportInput')
    if (!files || files.length === 0) {
        return
    }
    const statusEl = document.getElementById('createAudioStatus')
    const results = []
    console.debug('DEBUG: create audio import selected', files.length)
    for (const file of files) {
        if (!file.type.startsWith('audio/') && !file.type.startsWith('video/')) {
            results.push(`${file.name}: 跳过（不是音频/视频）`)
            continue
        }
        if (statusEl) {
            statusEl.textContent = t('create.uploading') + file.name
        }
        console.debug('DEBUG: create audio import uploading', file.name)
        const formData = new FormData()
        formData.append('file', file)
        try {
            const uploadResp = await fetch('/upload_music', {
                method: 'POST',
                body: formData
            })
            const uploadData = await uploadResp.json()
            if (uploadData.status !== 'success') {
                results.push(`${file.name}: 上传失败 - ${uploadData.message}`)
                continue
            }
            const cleanName = uploadData.filename
            const jsonName = `${getFileBaseName(cleanName)}.json`
            const parsed = parseTitleArtistFromName(file.name)
            const title = parsed.title || getFileBaseName(file.name)
            const artists = parsed.artists.length ? parsed.artists : [t('runtime.unknownArtist')]
            const jsonData = buildSongJsonData(title, artists, getFileBaseName(cleanName), cleanName)
            const createResp = await fetch('/create_json', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    filename: jsonName,
                    content: jsonData
                })
            })
            const createData = await createResp.json()
            if (createData.status === 'success') {
                results.push(`${cleanName}: 创建成功`)
            } else {
                results.push(`${cleanName}: 创建失败 - ${createData.message}`)
            }
        } catch (error) {
            results.push(`${file.name}: 创建失败 - ${error}`)
        }
    }
    if (statusEl) {
        statusEl.textContent = results.length ? results.join(' | ') : '导入完成'
    }
    if (results.length) {
        alert(results.join('\n'))
        location.reload()
    }
    if (fileInput) {
        fileInput.value = ''
    }
}

function triggerStaticImport() {
    const input = document.getElementById('staticZipInput')
    if (!input) {
        alert(t('alert.noImportControl'))
        return
    }
    input.value = ''
    input.click()
}

function debugUploadAction(action, target, detail) {
    if (typeof console !== 'undefined' && typeof console.debug === 'function') {
        console.debug(`DEBUG: ${target} ${action}`, detail)
    }
}

function triggerUploadInput(inputId, target) {
    const input = document.getElementById(inputId)
    if (!input) {
        alert(t('alert.noImportControl'))
        return
    }
    debugUploadAction('picker opened', target)
    input.value = ''
    input.click()
}

function getImageUploadInput(type) {
    const inputMap = {
        album: 'imageUpload',
        background: 'backgroundUpload',
        dynamicCover: 'dynamicCoverUpload',
        dynamicCoverPoster: 'dynamicCoverPosterUpload'
    }
    const inputId = inputMap[type]
    return inputId ? document.getElementById(inputId) : null
}

function triggerLyricsUpload() {
    triggerUploadInput('lyricsUpload', 'lyrics')
}

function triggerTranslationUpload() {
    triggerUploadInput('translationUpload', 'translation')
}

function triggerMusicUpload() {
    triggerUploadInput('musicUpload', 'music')
}

function triggerImageUpload(type) {
    const input = getImageUploadInput(type)
    if (!input) {
        alert(t('alert.noImportControl'))
        return
    }
    triggerUploadInput(input.id, type)
}

function triggerCreateAudioImport() {
    triggerUploadInput('audioImportInput', 'create audio import')
}

async function handleStaticImport(event) {
    const input = event ? event.target : null
    const file = input && input.files ? input.files[0] : null
    if (!file) {
        return
    }

    if (!file.name.toLowerCase().endsWith('.zip')) {
        alert(t('alert.selectZipFile'))
        input.value = ''
        return
    }

    const formData = new FormData()
    formData.append('file', file)

    try {
        const response = await fetch('/import_static', {
            method: 'POST',
            body: formData
        })
        const contentType = response.headers.get('content-type') || ''

        if (!contentType.includes('application/json')) {
            throw new Error('服务器返回未知格式')
        }

        const result = await response.json()
        if (response.ok && result.status === 'success') {
            alert(result.message || t('runtime.importSuccess', {count: 0}))
            location.reload()
        } else {
            throw new Error(result.message || '导入失败')
        }
    } catch (error) {
        alert(error.message || t('runtime.importFailed'))
    } finally {
        if (input) {
            input.value = ''
        }
    }
}

async function exportStatic(filename) {
    if (!filename) {
        alert(t('alert.missingFilename'))
        return
    }

    try {
        const response = await fetch('/export_static', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ filename })
        })

        const contentType = response.headers.get('content-type') || ''

        if (!response.ok) {
            if (contentType.includes('application/json')) {
                const errorPayload = await response.json()
                throw new Error(errorPayload.message || '导出失败')
            }
            throw new Error('导出失败')
        }

        if (contentType.includes('application/json')) {
            const payload = await response.json()
            if (payload.status === 'error') {
                throw new Error(payload.message || '导出失败')
            }
            throw new Error(payload.message || '服务器返回未知内容')
        }

        const blob = await response.blob()
        const url = URL.createObjectURL(blob)
        const link = document.createElement('a')
        link.href = url
        link.download = 'static.zip'
        document.body.appendChild(link)
        link.click()
        document.body.removeChild(link)
        URL.revokeObjectURL(url)

        const missingCount = parseInt(response.headers.get('x-missing-assets-count') || '0', 10)
        if (missingCount > 0) {
            alert(t('runtime.exportPartial', {count: missingCount}))
        }
    } catch (error) {
        alert(error.message || t('runtime.exportFailed'))
    }
}

function formatStaticExportBytes(bytes) {
    const value = Number(bytes) || 0
    if (value < 1024) return `${value} B`
    const units = ['KB', 'MB', 'GB', 'TB']
    let current = value / 1024
    let unitIndex = 0
    while (current >= 1024 && unitIndex < units.length - 1) {
        current /= 1024
        unitIndex += 1
    }
    return `${current.toFixed(current >= 10 ? 1 : 2)} ${units[unitIndex]}`
}

async function isStaticExportLocalAccess() {
    try {
        const authData = await fetchAuthStatus()
        return authData.status === 'success' ? Boolean(authData.is_local) : false
    } catch (error) {
        console.warn('Failed to detect local access for static export:', error)
        return false
    }
}

async function getStaticExportUnlockState() {
    const authData = await fetchAuthStatus()
    const localAccess = authData.status === 'success' ? Boolean(authData.is_local) : false
    const securityEnabled = authData.status === 'success' ? Boolean(authData.security_enabled) : true
    const isUnlocked = Boolean(localAccess || !securityEnabled || authData.is_system_admin || authData.trusted)
    return { authData, isUnlocked, localAccess }
}

function openStaticExportModal() {
    const modal = document.getElementById('staticExportModal')
    if (modal) {
        modal.style.display = 'block'
    }
}

function closeStaticExportModal() {
    const modal = document.getElementById('staticExportModal')
    if (modal) {
        modal.style.display = 'none'
    }
}

function renderStaticExportState(state) {
    const statusEl = document.getElementById('staticExportStatus')
    const progressBar = document.getElementById('staticExportProgressBar')
    const progressText = document.getElementById('staticExportProgressText')
    const filesText = document.getElementById('staticExportFilesText')
    const bytesText = document.getElementById('staticExportBytesText')
    const currentFileEl = document.getElementById('staticExportCurrentFile')
    const downloadBtn = document.getElementById('staticExportDownloadBtn')

    if (!statusEl || !progressBar || !progressText || !filesText || !bytesText || !currentFileEl || !downloadBtn) {
        return
    }

    const nextState = state || {}
    const status = nextState.status || 'pending'
    const totalFiles = Number(nextState.total_files) || 0
    const processedFiles = Number(nextState.processed_files) || 0
    const totalBytes = Number(nextState.total_bytes) || 0
    const processedBytes = Number(nextState.processed_bytes) || 0
    const percent = Number.isFinite(Number(nextState.progress_percent)) ? Number(nextState.progress_percent) : 0
    const downloadReady = nextState.download_ready !== false

    const statusTextMap = {
        pending: t('staticExport.preparing'),
        running: t('staticExport.running'),
        done: t('staticExport.done'),
        error: t('staticExport.error')
    }

    const errorMessage = nextState.error ? `：${nextState.error}` : ''
    const expiredSuffix = status === 'done' && !downloadReady ? `（${t('staticExport.expired')}）` : ''
    statusEl.textContent = status === 'error'
        ? `${statusTextMap.error}${errorMessage}`
        : `${statusTextMap[status] || statusTextMap.pending}${expiredSuffix}`
    progressBar.style.width = `${Math.max(0, Math.min(100, percent))}%`
    progressText.textContent = `${Math.max(0, Math.min(100, percent)).toFixed(2)}%`
    filesText.textContent = t('staticExport.filesSummary', { processed: processedFiles, total: totalFiles })
    bytesText.textContent = t('staticExport.bytesSummary', {
        processed: formatStaticExportBytes(processedBytes),
        total: formatStaticExportBytes(totalBytes)
    })
    currentFileEl.textContent = nextState.current_file || '—'
    downloadBtn.disabled = status !== 'done' || !downloadReady
    downloadBtn.textContent = t('staticExport.download')

    fullStaticExportLastState = nextState
    if (nextState.task_id) {
        activeFullStaticExportTaskId = nextState.task_id
    }
}

function stopStaticExportPolling() {
    if (fullStaticExportPollTimer) {
        clearTimeout(fullStaticExportPollTimer)
        fullStaticExportPollTimer = 0
    }
    fullStaticExportRequestInFlight = false
}

async function refreshStaticExportStatus(taskId) {
    const currentTaskId = taskId || activeFullStaticExportTaskId
    if (!currentTaskId || fullStaticExportRequestInFlight) {
        return
    }

    fullStaticExportRequestInFlight = true
    try {
        const response = await fetch(`/export_static_full/status?task_id=${encodeURIComponent(currentTaskId)}`)
        const contentType = response.headers.get('content-type') || ''
        if (!response.ok) {
            let message = t('staticExport.statusFetchFailed')
            if (contentType.includes('application/json')) {
                const errorPayload = await response.json()
                message = errorPayload.message || message
            }
            throw new Error(message)
        }

        if (!contentType.includes('application/json')) {
            throw new Error(t('staticExport.statusResponseInvalid'))
        }

        const payload = await response.json()
        const task = payload.task || {}
        renderStaticExportState(task)

        const taskStatus = task.status || 'pending'
        if (taskStatus === 'pending' || taskStatus === 'running') {
            fullStaticExportPollTimer = window.setTimeout(() => {
                refreshStaticExportStatus(currentTaskId).catch(error => {
                    console.error('刷新整包导出进度失败:', error)
                    renderStaticExportState({
                        ...(fullStaticExportLastState || {}),
                        status: 'error',
                        error: error.message || t('common.failed')
                    })
                    stopStaticExportPolling()
                })
            }, 1200)
        } else {
            stopStaticExportPolling()
        }
    } catch (error) {
        renderStaticExportState({
            ...(fullStaticExportLastState || {}),
            status: 'error',
            error: error.message || t('common.failed')
        })
        stopStaticExportPolling()
        throw error
    } finally {
        fullStaticExportRequestInFlight = false
    }
}

async function beginStaticExportTask() {
    stopStaticExportPolling()
    openStaticExportModal()
    const pendingState = {
        status: 'pending',
        total_files: 0,
        processed_files: 0,
        total_bytes: 0,
        processed_bytes: 0,
        current_file: '',
        progress_percent: 0,
        error: ''
    }
    renderStaticExportState(pendingState)

    try {
        const response = await fetch('/export_static_full/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        })
        const contentType = response.headers.get('content-type') || ''
        const payload = contentType.includes('application/json') ? await response.json() : null

        if (!response.ok) {
            const errorMessage = payload && payload.message ? payload.message : t('staticExport.createFailed')
            if (response.status === 403 || /未解锁/.test(errorMessage)) {
                pendingFullStaticExportStart = true
                await toggleAuthModal()
                return false
            }
            throw new Error(errorMessage)
        }

        if (!payload || payload.status !== 'success') {
            throw new Error((payload && payload.message) || t('staticExport.createFailed'))
        }

        const task = payload.task || {}
        const taskId = task.task_id || payload.task_id
        if (!taskId) {
            throw new Error(t('staticExport.missingTaskId'))
        }

        activeFullStaticExportTaskId = taskId
        renderStaticExportState({ ...task, task_id: taskId })
        await refreshStaticExportStatus(taskId)
        return true
    } catch (error) {
        const errorMessage = error && error.message ? error.message : t('staticExport.createFailed')
        renderStaticExportState({
            ...(fullStaticExportLastState || pendingState),
            status: 'error',
            error: errorMessage
        })
        stopStaticExportPolling()
        throw error
    }
}

async function triggerFullStaticExport() {
    if (activeFullStaticExportTaskId && fullStaticExportLastState) {
        if (['pending', 'running'].includes(fullStaticExportLastState.status)) {
            openStaticExportModal()
            await refreshStaticExportStatus(activeFullStaticExportTaskId).catch(() => {})
            return
        }

        if (fullStaticExportLastState.status === 'done' && fullStaticExportLastState.download_ready !== false) {
            openStaticExportModal()
            try {
                await refreshStaticExportStatus(activeFullStaticExportTaskId)
            } catch (error) {
                console.warn('刷新已完成导出任务失败:', error)
            }

            if (fullStaticExportLastState && fullStaticExportLastState.status === 'done' && fullStaticExportLastState.download_ready === false) {
                const started = await beginStaticExportTask()
                if (started === false) {
                    return
                }
            }
            return
        }
    }

    try {
        const unlockState = await getStaticExportUnlockState()
        if (!unlockState.isUnlocked) {
            pendingFullStaticExportStart = true
            await toggleAuthModal()
            const statusEl = document.getElementById('staticExportStatus')
            if (statusEl) {
                statusEl.textContent = t('staticExport.needAuth')
            }
            return
        }

        pendingFullStaticExportStart = false
        const started = await beginStaticExportTask()
        if (started === false) {
            return
        }
    } catch (error) {
        if (error && error.message) {
            alert(error.message)
        }
    }
}

async function downloadFullStaticExport(taskId) {
    const currentTaskId = taskId || activeFullStaticExportTaskId
    if (!currentTaskId) {
        alert(t('staticExport.noTask'))
        return
    }

    try {
        const unlockState = await getStaticExportUnlockState()
        if (!unlockState.isUnlocked) {
            pendingFullStaticExportDownloadTaskId = currentTaskId
            await toggleAuthModal()
            return
        }
    } catch (error) {
        alert(error && error.message ? error.message : t('staticExport.permissionCheckFailed'))
        return
    }

    if (fullStaticExportLastState && fullStaticExportLastState.status === 'done' && fullStaticExportLastState.download_ready === false) {
        await triggerFullStaticExport()
        return
    }

    if (!fullStaticExportLastState || fullStaticExportLastState.status !== 'done') {
        alert(t('staticExport.notReady'))
        return
    }

    const link = document.createElement('a')
    link.href = `/export_static_full/download?task_id=${encodeURIComponent(currentTaskId)}`
    link.download = ''
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
}

function showRenameModal(filename, title, artists) {
    currentRenameFile = filename
    const modal = document.getElementById('renameModal')
    const titleInput = document.getElementById('renameSongTitle')
    const artistsInput = document.getElementById('renameSongArtists')

    titleInput.value = title || ''
    artistsInput.value = artists || ''
    updateRenameFilenamePreview()

    modal.style.display = 'block'
}

function closeRenameModal() {
    document.getElementById('renameModal').style.display = 'none'
}

function renameJsonFile() {
    const title = document.getElementById('renameSongTitle').value.trim()
    const artistsInput = document.getElementById('renameSongArtists').value.trim()

    if (!title || !artistsInput) {
        alert(t('alert.fillTitleAndSinger'))
        return
    }

    const artists = artistsInput.split(',').map(artist => artist.trim()).filter(artist => artist)

    if (artists.length === 0) {
        alert(t('alert.enterAtLeastOneArtist'))
        return
    }

    const newFileName = buildStrictSongJsonFilename(title, artists)
    if (!newFileName) {
        alert(t('alert.filenameEmptyAdjust'))
        return
    }

    fetch('/rename_json', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            oldFilename: currentRenameFile,
            newFilename: newFileName,
            title: title,
            artists: artists
        })
    })
        .then(response => response.json())
        .then(data => {
            if (data.status === 'success') {
                closeRenameModal()
                location.reload()
            } else {
                alert(t('alert.renameFailedWithMsg') + data.message)
            }
        })
        .catch(error => {
            alert(t('file.renameFailed') + error)
        })
}
