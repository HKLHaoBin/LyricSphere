const PROVIDER_PRESETS = {
    'deepseek': {
        baseUrl: 'https://api.deepseek.com',
        model: 'deepseek-reasoner'
    },
    'openai': {
        baseUrl: 'https://api.openai.com/v1',
        model: 'gpt-4o-mini'
    },
    'openrouter': {
        baseUrl: 'https://openrouter.ai/api/v1',
        model: 'openai/gpt-4o-mini'
    },
    'together': {
        baseUrl: 'https://api.together.xyz/v1',
        model: 'mistralai/Mistral-7B-Instruct-v0.3'
    },
    'groq': {
        baseUrl: 'https://api.groq.com/openai/v1',
        model: 'llama-3.1-70b-versatile'
    }
};

// 预设相关常量
const AI_PRESETS_STORAGE_KEY = 'aiTranslationPresets';
const ACTIVE_AI_PRESET_KEY = 'activeAiPresetId';
const BATCH_WORKBENCH_ACTIVE_PRESET_KEY_SAFE = (typeof globalThis !== 'undefined' && globalThis.BATCH_WORKBENCH_ACTIVE_PRESET_KEY) || 'batchWorkbenchActiveAiPresetId';
const AI_PRESETS_EXPORT_TYPE = 'famyliam-ai-presets';
const AI_PRESETS_EXPORT_VERSION = 1;
let aiPresetCache = [];
let aiPresetPermissions = {};
let aiSettingsSourceDraft = { mode: 'manual', preset_id: '', preset_name: '' };
let aiSettingsSourceSaved = { mode: 'manual', preset_id: '', preset_name: '' };
let aiSettingsInitialSnapshot = null;
let aiSettingsStatusState = { kind: 'idle', presetName: '' };
const AI_FIELD_HOSTED_PLACEHOLDER = '已由后端托管';
let aiFieldVisibility = {};
let aiRuntimeSummary = null;

function safeUpdateBatchWorkbenchPresetSelect() {
    if (typeof updateBatchWorkbenchPresetSelect === 'function') {
        updateBatchWorkbenchPresetSelect();
    }
}

function flattenAiFieldVisibilityRaw(raw) {
    if (!raw || typeof raw !== 'object') {
        return {};
    }
    if ('provider' in raw || 'thinking_provider' in raw || 'system_prompt' in raw) {
        return raw;
    }
    const flat = {};
    const translation = raw.translation;
    if (translation && typeof translation === 'object') {
        if (translation.provider !== undefined) flat.provider = translation.provider;
        if (translation.base_url !== undefined) flat.base_url = translation.base_url;
        if (translation.model !== undefined) flat.model = translation.model;
        if (translation.system_prompt !== undefined) flat.system_prompt = translation.system_prompt;
    }
    const thinking = raw.thinking;
    if (thinking && typeof thinking === 'object') {
        if (thinking.provider !== undefined) flat.thinking_provider = thinking.provider;
        if (thinking.base_url !== undefined) flat.thinking_base_url = thinking.base_url;
        if (thinking.model !== undefined) flat.thinking_model = thinking.model;
        if (thinking.system_prompt !== undefined) flat.thinking_system_prompt = thinking.system_prompt;
    }
    const batch = raw.batch;
    if (batch && typeof batch === 'object' && batch.extra_prompt !== undefined) {
        flat.batch_extra_prompt = batch.extra_prompt;
    }
    const romanization = raw.romanization;
    if (romanization && typeof romanization === 'object' && romanization.system_prompt !== undefined) {
        flat.romanization_system_prompt = romanization.system_prompt;
    }
    return flat;
}

function normalizeAiFieldVisibility(raw) {
    const incoming = flattenAiFieldVisibilityRaw(raw && typeof raw === 'object' ? raw : {});
    const normalized = {};
    Object.keys(incoming).forEach((key) => {
        normalized[key] = String(incoming[key] || 'visible').trim().toLowerCase() === 'hidden' ? 'hidden' : 'visible';
    });
    return normalized;
}

function resolveAiFieldVisibilityFromResponse(data) {
    const payload = data && typeof data === 'object' ? data : {};
    const raw = payload.field_visibility
        || payload.effective_settings?.field_visibility
        || payload.settings?.field_visibility;
    return normalizeAiFieldVisibility(raw);
}

function resolveAiFieldVisibility(settings, options = {}) {
    const fromOptions = options.fieldVisibility;
    const fromSettings = settings?._visibility || settings?.field_visibility;
    if (fromOptions && typeof fromOptions === 'object') {
        return normalizeAiFieldVisibility(fromOptions);
    }
    if (fromSettings && typeof fromSettings === 'object') {
        return normalizeAiFieldVisibility(fromSettings);
    }
    const permissions = aiPresetPermissions || {};
    return normalizeAiFieldVisibility({
        provider: permissions.ai_view_provider === false ? 'hidden' : 'visible',
        base_url: permissions.ai_view_base_url === false ? 'hidden' : 'visible',
        model: permissions.ai_view_model === false ? 'hidden' : 'visible',
        system_prompt: permissions.ai_view_prompts === false ? 'hidden' : 'visible',
        thinking_provider: permissions.ai_view_provider === false ? 'hidden' : 'visible',
        thinking_base_url: permissions.ai_view_base_url === false ? 'hidden' : 'visible',
        thinking_model: permissions.ai_view_model === false ? 'hidden' : 'visible',
        thinking_system_prompt: permissions.ai_view_prompts === false ? 'hidden' : 'visible'
    });
}

function isAiFieldHidden(fieldKey, visibility) {
    return String((visibility || aiFieldVisibility || {})[fieldKey] || 'visible').trim().toLowerCase() === 'hidden';
}

function setAiFieldVisibility(next) {
    aiFieldVisibility = normalizeAiFieldVisibility(next || {});
}

function setAiRuntimeSummary(summary) {
    aiRuntimeSummary = summary && typeof summary === 'object' ? summary : null;
}

function getAiRuntimeSummaryLabel(kind = 'translation') {
    const summary = aiRuntimeSummary || {};
    if (kind === 'thinking') {
        return {
            provider: String(summary.thinking_provider_label || summary.source_label || summary.provider_label || '').trim(),
            model: String(summary.thinking_model_label || summary.model_label || '').trim()
        };
    }
    return {
        provider: String(summary.provider_label || summary.source_label || '').trim(),
        model: String(summary.model_label || '').trim()
    };
}

function isThinkingEnabledFromRuntimeSummary() {
    if (!aiRuntimeSummary || aiRuntimeSummary.thinking_enabled === undefined) {
        return true;
    }
    return Boolean(aiRuntimeSummary.thinking_enabled);
}

async function ensureAiRuntimeSummaryForProgress() {
    if (aiRuntimeSummary) {
        return aiRuntimeSummary;
    }
    try {
        const response = await fetch('/get_ai_settings');
        const data = await response.json();
        if (data.status === 'success') {
            setAiRuntimeSummary(data.runtime_summary);
            if (data.permissions) {
                aiPresetPermissions = data.permissions;
            }
            setAiFieldVisibility(resolveAiFieldVisibilityFromResponse(data));
        }
    } catch (error) {
        console.warn('Failed to load AI runtime summary:', error);
    }
    return aiRuntimeSummary;
}

function isLocalAiAdminProbeAllowed() {
    const isLocalHost = ['localhost', '127.0.0.1', '::1'].includes(location.hostname);
    if (!isLocalHost) {
        return false;
    }
    return hasFullAiPresetVisibility();
}

function formatMissingAiPresetOptionLabel(presetId) {
    const id = String(presetId || '').trim() || t('preset.defaultName');
    return `预设已丢失：${id}`;
}

function getSavedAiPresetSelectId() {
    return aiSettingsSourceSaved && aiSettingsSourceSaved.mode === 'preset'
        ? String(aiSettingsSourceSaved.preset_id || '').trim()
        : '';
}

function getDraftAiPresetSelectId() {
    return aiSettingsSourceDraft && aiSettingsSourceDraft.mode === 'preset'
        ? String(aiSettingsSourceDraft.preset_id || '').trim()
        : '';
}

function getAiPresetSelectTargetId() {
    const savedId = getSavedAiPresetSelectId();
    const draftId = getDraftAiPresetSelectId();
    const rememberedId = String(localStorage.getItem(ACTIVE_AI_PRESET_KEY) || '').trim();
    if (hasPendingAiSettingsPreview() && draftId) {
        return draftId;
    }
    return savedId || rememberedId;
}

function appendMissingAiPresetOption(select, presetId, selectedId) {
    const id = String(presetId || '').trim();
    if (!select || !id) {
        return;
    }
    const option = document.createElement('option');
    option.value = id;
    option.textContent = formatMissingAiPresetOptionLabel(id);
    option.disabled = true;
    if (id === selectedId) {
        option.selected = true;
    }
    select.appendChild(option);
}

function normalizeAiSettingsSource(next) {
    const incoming = next && typeof next === 'object' ? next : {};
    const mode = String(incoming.mode || 'manual').trim().toLowerCase();
    const presetId = String(incoming.preset_id || '').trim();
    const presetName = String(incoming.preset_name || '').trim();
    const normalized = {
        mode: mode === 'preset' ? 'preset' : 'manual',
        preset_id: mode === 'preset' ? presetId : '',
        preset_name: mode === 'preset' ? presetName : ''
    };
    if (incoming.kind !== undefined) {
        normalized.kind = String(incoming.kind || '').trim();
    }
    if (incoming.label !== undefined) {
        normalized.label = String(incoming.label || '').trim();
    }
    return normalized;
}

function setAiSettingsSourceDraft(next) {
    aiSettingsSourceDraft = normalizeAiSettingsSource(next);
    updateAiSettingsSourceInfo();
}

function setAiSettingsSourceSaved(next) {
    aiSettingsSourceSaved = normalizeAiSettingsSource(next);
    updateAiSettingsSourceInfo();
}

function syncActiveAiPresetKeyWithSavedSource(source = null) {
    const savedSource = source && typeof source === 'object' ? source : (aiSettingsSourceSaved || {});
    const mode = String(savedSource.mode || 'manual').trim().toLowerCase();
    const presetId = mode === 'preset' ? String(savedSource.preset_id || '').trim() : '';
    const rememberedId = String(localStorage.getItem(ACTIVE_AI_PRESET_KEY) || '').trim();
    const presetExists = presetId ? loadAiPresets().some(preset => preset.id === presetId) : false;
    if (presetId && presetExists) {
        localStorage.setItem(ACTIVE_AI_PRESET_KEY, presetId);
    } else if (rememberedId && !loadAiPresets().some(preset => preset.id === rememberedId)) {
        // Cleanup only when remembered preset no longer exists.
        localStorage.removeItem(ACTIVE_AI_PRESET_KEY);
    }
    if (typeof updateQuickAiPresetSelect === 'function') {
        updateQuickAiPresetSelect();
    }
}

function setAiSettingsStatus(kind = 'idle', presetName = '') {
    aiSettingsStatusState = {
        kind: String(kind || 'idle').trim() || 'idle',
        presetName: String(presetName || '').trim()
    };
}

function formatAiSettingsSourceLabel(source) {
    const kind = String(source.kind || '').trim();
    const kindLabelMap = {
        'system_preset': t('aiSettings.sourceKindSystemPreset'),
        'personal_preset': t('aiSettings.sourceKindPersonalPreset'),
        'shared_preset': t('aiSettings.sourceKindSharedPreset'),
        'missing_preset': t('aiSettings.sourceKindMissingPreset'),
        'preset': t('aiSettings.sourceKindPreset')
    };
    const kindLabel = kindLabelMap[kind] || '';
    if (source.mode === 'preset') {
        if (kind === 'missing_preset') {
            const missingId = String(source.preset_id || '').trim() || t('preset.defaultName');
            return t('aiSettings.sourceMissingPresetFormat', { id: missingId });
        }
        const name = source.preset_name || source.preset_id || t('preset.defaultName');
        const suffix = kindLabel ? ` · ${kindLabel}` : '';
        return t('aiSettings.sourcePresetFormat', {
            name,
            suffix,
            managed: t('aiSettings.sourceApiKeyManaged')
        });
    }
    return t('aiSettings.sourceLabelStandalone');
}

function updateAiSettingsSourceInfo() {
    const el = document.getElementById('aiSettingsSourceInfo');
    if (!el) return;
    const savedLabel = formatAiSettingsSourceLabel(aiSettingsSourceSaved || {});
    el.textContent = t('aiSettings.sourceCurrent', { label: savedLabel });
}

function classifyAiPresetKind(preset) {
    const id = String(preset?.id || '').trim();
    const presetKind = String(preset?.kind || '').trim();
    if (presetKind) {
        return { kind: presetKind, label: '' };
    }
    const ownerScope = String(preset?.owner_scope || '').trim().toLowerCase();
    const acl = (preset?.acl && typeof preset.acl === 'object' && !Array.isArray(preset.acl)) ? preset.acl : {};
    if (ownerScope === 'system' || id === 'default') {
        return { kind: 'system_preset', label: '' };
    }
    if (ownerScope === 'shared' || Object.keys(acl).length > 0) {
        return { kind: 'shared_preset', label: '' };
    }
    return { kind: 'personal_preset', label: '' };
}

function setAiSettingsSourceManual() {
    const select = document.getElementById('aiPresetSelect');
    const selectedPresetId = select ? String(select.value || '').trim() : '';
    setAiSettingsSourceDraft({ mode: 'manual', preset_id: '', preset_name: '', kind: 'manual' });
    if (selectedPresetId) {
        localStorage.setItem(ACTIVE_AI_PRESET_KEY, selectedPresetId);
    }
    if (aiSettingsInitialSnapshot?.form) {
        fillAIFormState(aiSettingsInitialSnapshot.form);
    }
    setAiSettingsStatus(hasPendingAiSettingsPreview() ? 'preview-manual' : 'idle');
    updateAiPresetApplyStatus();
}

function previewSelectedAiPreset() {
    const select = document.getElementById('aiPresetSelect');
    const presetId = select ? String(select.value || '').trim() : '';
    if (!presetId) {
        alert(t('alert.selectPreset'));
        return;
    }
    const presets = loadAiPresets();
    const preset = presets.find(p => p.id === presetId);
    if (!preset) {
        alert(t('alert.presetNotExist'));
        return;
    }
    const classified = classifyAiPresetKind(preset);
    localStorage.setItem(ACTIVE_AI_PRESET_KEY, preset.id);
    setAiSettingsSourceDraft({
        mode: 'preset',
        preset_id: preset.id,
        preset_name: preset.name,
        kind: classified.kind,
        label: classified.label
    });
    // keep behavior: also fill the form so UI matches draft preview
    fillAIFormState(preset, { fieldVisibility: resolveAiFieldVisibility(preset) });
    setAiSettingsStatus(hasPendingAiSettingsPreview() ? 'preview-preset' : 'idle', preset.name);
    updateAiPresetApplyStatus();
}

function snapshotAiSettingsPreviewState() {
    return {
        form: collectAIFormState(),
        source: {
            mode: String(aiSettingsSourceDraft.mode || 'manual').trim(),
            preset_id: String(aiSettingsSourceDraft.preset_id || '').trim(),
            preset_name: String(aiSettingsSourceDraft.preset_name || '').trim()
        }
    };
}

function hasPendingAiSettingsPreview() {
    if (!aiSettingsInitialSnapshot) return false;
    try {
        return JSON.stringify(snapshotAiSettingsPreviewState()) !== JSON.stringify(aiSettingsInitialSnapshot);
    } catch (error) {
        return true;
    }
}

function markAiSettingsEdited() {
    if (!aiSettingsInitialSnapshot) return;
    if (!hasPendingAiSettingsPreview()) {
        if (String((aiSettingsStatusState || {}).kind || '') !== 'idle') {
            setAiSettingsStatus('idle');
            updateAiPresetApplyStatus();
        }
        return;
    }
    const currentKind = String((aiSettingsStatusState || {}).kind || '').trim().toLowerCase();
    if (currentKind === 'preview-preset') {
        updateAiPresetApplyStatus();
        return;
    }
    setAiSettingsStatus('preview-manual');
    updateAiPresetApplyStatus();
}

function safeParseJson(rawValue, fallbackValue) {
    if (typeof rawValue !== 'string' || !rawValue.trim()) {
        return fallbackValue;
    }
    try {
        return JSON.parse(rawValue);
    } catch (e) {
        return fallbackValue;
    }
}

function readLegacyAiPresetSnapshot() {
    const rawPresets = localStorage.getItem(AI_PRESETS_STORAGE_KEY);
    const rawActiveId = localStorage.getItem(ACTIVE_AI_PRESET_KEY) || '';
    const rawBatchActiveId = localStorage.getItem(BATCH_WORKBENCH_ACTIVE_PRESET_KEY_SAFE) || '';

    const parsed = safeParseJson(rawPresets, []);
    const presets = Array.isArray(parsed) ? parsed : [];
    const normalized = normalizeAiPresetList(presets);
    const presetIdSet = new Set(normalized.map(p => p.id));

    return {
        hasAny: normalized.length > 0,
        presets: normalized,
        activePresetId: rawActiveId && presetIdSet.has(rawActiveId) ? rawActiveId : '',
        batchActivePresetId: rawBatchActiveId && presetIdSet.has(rawBatchActiveId) ? rawBatchActiveId : ''
    };
}

function shouldMigrateLegacyPresets(legacyPresets, backendPresets) {
    if (!Array.isArray(legacyPresets) || legacyPresets.length === 0) {
        return false;
    }
    if (!Array.isArray(backendPresets) || backendPresets.length === 0) {
        return true;
    }
    const backendById = new Map();
    backendPresets.forEach(preset => {
        if (preset && preset.id) backendById.set(preset.id, preset);
    });

    const normalizeUpdatedAt = (value) => {
        if (value === null || value === undefined) return 0;
        const num = Number(value);
        if (Number.isFinite(num)) return num;
        const parsed = Date.parse(String(value));
        return Number.isFinite(parsed) ? parsed : 0;
    };

    const stablePresetFingerprint = (preset) => {
        if (!preset || typeof preset !== 'object') return '';
        const t = preset.translation || {};
        const th = preset.thinking || {};
        const b = preset.batch || {};
        // IMPORTANT: do not include api_key in fingerprint
        const payload = {
            id: String(preset.id || ''),
            name: String(preset.name || ''),
            owner_scope: String(preset.owner_scope || ''),
            acl: preset.acl && typeof preset.acl === 'object' ? preset.acl : {},
            translation: {
                provider: String(t.provider || ''),
                base_url: String(t.base_url || ''),
                model: String(t.model || ''),
                system_prompt: String(t.system_prompt || ''),
                expect_reasoning: Boolean(t.expect_reasoning),
                compat_mode: Boolean(t.compat_mode),
                strip_brackets: Boolean(t.strip_brackets),
                experimental_full_line_bracket_strip: Boolean(t.experimental_full_line_bracket_strip),
                experimental_bracket_line_as_subline: Boolean(t.experimental_bracket_line_as_subline),
            },
            thinking: {
                enabled: th.enabled !== undefined ? Boolean(th.enabled) : true,
                provider: String(th.provider || ''),
                base_url: String(th.base_url || ''),
                model: String(th.model || ''),
                system_prompt: String(th.system_prompt || ''),
            },
            batch: {
                auto_save: b.auto_save !== undefined ? Boolean(b.auto_save) : true,
                only_empty: b.only_empty !== undefined ? Boolean(b.only_empty) : true,
                always_override: Boolean(b.always_override),
                extra_prompt: String(b.extra_prompt || ''),
            },
            romanization: (() => {
                const r = preset.romanization || {};
                const am = String(r.alignment_mode || '').trim().toLowerCase();
                return {
                    system_prompt: String(r.system_prompt || ''),
                    alignment_mode: am === 'separator_tokens' ? 'separator_tokens' : 'indexed_tokens',
                    separator: String(r.separator || ';'),
                    strict_token_count: r.strict_token_count !== undefined ? Boolean(r.strict_token_count) : true,
                    require_trailing_separator: r.require_trailing_separator !== undefined ? Boolean(r.require_trailing_separator) : true
                };
            })()
        };
        try {
            return JSON.stringify(payload);
        } catch (e) {
            return '';
        }
    };

    return legacyPresets.some(p => {
        if (!p || !p.id) return false;
        const backend = backendById.get(p.id);
        if (!backend) return true; // missing id -> migrate
        const legacyTs = normalizeUpdatedAt(p.updated_at);
        const backendTs = normalizeUpdatedAt(backend.updated_at);
        if (legacyTs > backendTs) return true; // legacy newer -> migrate
        const legacyFp = stablePresetFingerprint(p);
        const backendFp = stablePresetFingerprint(backend);
        return legacyFp && backendFp && legacyFp !== backendFp; // same id but content differs -> migrate
    });
}

async function refreshAiPresetCache() {
    const legacy = readLegacyAiPresetSnapshot();
    if (legacy.hasAny) {
        aiPresetCache = legacy.presets.slice();
    }

    try {
        const res = await fetch('/ai-presets');
        const data = await res.json();
        if (data.status === 'success') {
            const presets = Array.isArray(data.presets) ? data.presets : [];
            const backendPresets = normalizeAiPresetList(presets);
            aiPresetPermissions = data.permissions || aiPresetPermissions || {};
            let migratedSnapshot = null;

            const canEditPreset = Boolean(data.can_edit_preset);
            if (!canEditPreset) {
                if (legacy.hasAny) {
                    console.warn('Detected legacy local AI presets but current device has no migration permission.');
                    return aiPresetCache.slice();
                }
                aiPresetCache = backendPresets;
                return aiPresetCache.slice();
            }

            if (legacy.hasAny && shouldMigrateLegacyPresets(legacy.presets, backendPresets)) {
                try {
                    const migrateRes = await fetch('/ai-presets/migrate-local-cache', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            presets: legacy.presets,
                            active_preset_id: legacy.activePresetId || ''
                        })
                    });
                    if (!migrateRes.ok) {
                        console.warn('Legacy AI preset migration rejected by backend:', migrateRes.status);
                        return aiPresetCache.slice();
                    }
                    const migrateData = await migrateRes.json();
                    if (!migrateData || migrateData.status !== 'success') {
                        console.warn('Legacy AI preset migration failed:', migrateData?.message || migrateData);
                        return aiPresetCache.slice();
                    }
                    // Use backend-merged result immediately to avoid rollback on subsequent refresh failure
                    const mergedPresets = Array.isArray(migrateData.presets) ? migrateData.presets : [];
                    migratedSnapshot = {
                        presets: normalizeAiPresetList(mergedPresets),
                        active_preset_id: String(migrateData.active_preset_id || '').trim()
                    };
                    aiPresetCache = migratedSnapshot.presets.slice();
                    localStorage.setItem(AI_PRESETS_STORAGE_KEY, JSON.stringify(aiPresetCache));
                } catch (e) {
                    console.warn('Failed to migrate legacy local AI presets:', e);
                    return aiPresetCache.slice();
                }
            }

            const res2 = await fetch('/ai-presets');
            const data2 = await res2.json();
            if (data2.status === 'success') {
                const presets2 = Array.isArray(data2.presets) ? data2.presets : [];
                aiPresetCache = normalizeAiPresetList(presets2);
                aiPresetPermissions = data2.permissions || aiPresetPermissions || {};
                localStorage.setItem(AI_PRESETS_STORAGE_KEY, JSON.stringify(aiPresetCache));
                return aiPresetCache.slice();
            }

            // If we have migratedSnapshot, keep it; otherwise fall back to the first backend snapshot
            aiPresetCache = migratedSnapshot ? migratedSnapshot.presets.slice() : backendPresets;
            localStorage.setItem(AI_PRESETS_STORAGE_KEY, JSON.stringify(aiPresetCache));
            return aiPresetCache.slice();
        }
    } catch (error) {
        console.warn('Failed to refresh AI presets from backend:', error);
    }

    return aiPresetCache.slice();
}

function updateAiPresetApplyStatus() {
    const statusEl = document.getElementById('aiPresetApplyStatus');
    if (!statusEl) {
        return;
    }
    const statusKind = String((aiSettingsStatusState || {}).kind || 'idle').trim().toLowerCase();
    const presetName = String((aiSettingsStatusState || {}).presetName || '').trim() || String(aiSettingsSourceDraft.preset_name || '').trim();
    statusEl.style.color = 'var(--text-secondary)';
    statusEl.textContent = '';
    if (statusKind === 'preview-preset') {
        statusEl.style.color = 'var(--warning-color, #d97706)';
        statusEl.textContent = presetName
            ? t('aiSettings.previewPreset', { name: presetName })
            : t('aiSettings.previewPresetUnnamed');
        return;
    }
    if (statusKind === 'preview-manual') {
        statusEl.style.color = 'var(--warning-color, #d97706)';
        statusEl.textContent = t('aiSettings.previewManual');
        return;
    }
    if (statusKind === 'applied-manual') {
        statusEl.style.color = 'var(--success-color, #16a34a)';
        statusEl.textContent = t('aiSettings.appliedManual');
        return;
    }
    if (statusKind === 'applied-preset') {
        statusEl.style.color = 'var(--success-color, #16a34a)';
        statusEl.textContent = t('aiSettings.appliedPreset', { name: presetName || t('preset.defaultName') });
    }
}

// 收集表单状态
function collectAIFormState() {
    return {
        translation: {
            provider: document.getElementById('aiProvider').value,
            base_url: document.getElementById('aiBaseUrl').value,
            model: document.getElementById('aiModel').value,
            api_key: document.getElementById('aiApiKey').value,
            system_prompt: document.getElementById('aiSystemPrompt').value,
            expect_reasoning: document.getElementById('aiExpectReasoning').checked,
            compat_mode: document.getElementById('aiCompatMode').checked,
            strip_brackets: document.getElementById('aiStripBrackets').checked,
            experimental_full_line_bracket_strip: document.getElementById('aiExperimentalFullLineBracketStrip').checked,
            experimental_bracket_line_as_subline: document.getElementById('aiExperimentalBracketLineAsSubline').checked
        },
        thinking: {
            enabled: document.getElementById('aiThinkingEnabled').checked,
            provider: document.getElementById('aiThinkingProvider').value,
            base_url: document.getElementById('aiThinkingBaseUrl').value,
            model: document.getElementById('aiThinkingModel').value,
            api_key: document.getElementById('aiThinkingApiKey').value,
            system_prompt: document.getElementById('aiThinkingPrompt').value
        },
        romanization: {
            system_prompt: (document.getElementById('aiRomanizationPrompt') || {}).value || '',
            alignment_mode: (() => {
                const sel = document.getElementById('aiRomanizationAlignmentMode');
                const v = sel && sel.value ? String(sel.value).trim().toLowerCase() : '';
                return v === 'separator_tokens' ? 'separator_tokens' : 'indexed_tokens';
            })(),
            separator: (document.getElementById('aiRomanizationSeparator') || {}).value || ';',
            strict_token_count: document.getElementById('aiRomanizationStrict')
                ? document.getElementById('aiRomanizationStrict').checked
                : true,
            require_trailing_separator: document.getElementById('aiRomanizationTrailing')
                ? document.getElementById('aiRomanizationTrailing').checked
                : true
        }
    };
}

function omitEmptyEndpointFields(settings) {
    const out = { ...settings };
    if (out.translation && typeof out.translation === 'object') {
        out.translation = { ...out.translation };
        if (!String(out.translation.base_url || '').trim()) delete out.translation.base_url;
        if (!String(out.translation.model || '').trim()) delete out.translation.model;
    }
    if (out.thinking && typeof out.thinking === 'object') {
        out.thinking = { ...out.thinking };
        if (!String(out.thinking.base_url || '').trim()) delete out.thinking.base_url;
        if (!String(out.thinking.model || '').trim()) delete out.thinking.model;
    }
    return out;
}

function updateRomanizationAlignmentHint() {
    const sel = document.getElementById('aiRomanizationAlignmentMode');
    const hint = document.getElementById('aiRomanizationSeparatorModeHint');
    if (!hint || !sel) return;
    if (sel.value === 'separator_tokens') {
        hint.style.display = 'block';
        if (typeof t === 'function') {
            hint.textContent = t('aiSettings.romanizationSeparatorModeHint');
        }
    } else {
        hint.style.display = 'none';
    }
}

// 填入表单状态
function fillAIFormState(settings, options = {}) {
    const visibility = resolveAiFieldVisibility(settings, options);
    setAiFieldVisibility(visibility);
    const skipProviderPreset = Boolean(options.skipProviderPreset);
    const translation = settings.translation || {};
    const thinking = settings.thinking || {};

    const setInputValue = (id, value, fieldKey, placeholderWhenHidden = AI_FIELD_HOSTED_PLACEHOLDER) => {
        const el = document.getElementById(id);
        if (!el) return;
        if (fieldKey && isAiFieldHidden(fieldKey, visibility)) {
            el.value = '';
            el.placeholder = placeholderWhenHidden;
            return;
        }
        el.placeholder = '';
        el.value = value !== undefined && value !== null ? String(value) : '';
    };

    if (settings.translation) {
        const providerEl = document.getElementById('aiProvider');
        if (providerEl) {
            if (isAiFieldHidden('provider', visibility)) {
                providerEl.value = '';
                providerEl.placeholder = AI_FIELD_HOSTED_PLACEHOLDER;
            } else {
                providerEl.placeholder = '';
                providerEl.value = translation.provider !== undefined && translation.provider !== null ? String(translation.provider) : '';
            }
        }
        setInputValue('aiBaseUrl', translation.base_url, 'base_url');
        setInputValue('aiModel', translation.model, 'model');
        document.getElementById('aiApiKey').value = '';
        setInputValue('aiSystemPrompt', translation.system_prompt, 'system_prompt', '');
        document.getElementById('aiExpectReasoning').checked = translation.expect_reasoning || false;
        document.getElementById('aiCompatMode').checked = translation.compat_mode || false;
        document.getElementById('aiStripBrackets').checked = translation.strip_brackets || false;
        document.getElementById('aiExperimentalFullLineBracketStrip').checked = translation.experimental_full_line_bracket_strip || false;
        document.getElementById('aiExperimentalBracketLineAsSubline').checked = translation.experimental_bracket_line_as_subline || false;
    }
    if (settings.thinking) {
        document.getElementById('aiThinkingEnabled').checked = thinking.enabled !== undefined ? thinking.enabled : true;
        const thinkingProviderEl = document.getElementById('aiThinkingProvider');
        if (thinkingProviderEl) {
            if (isAiFieldHidden('thinking_provider', visibility)) {
                thinkingProviderEl.value = '';
                thinkingProviderEl.placeholder = AI_FIELD_HOSTED_PLACEHOLDER;
            } else {
                thinkingProviderEl.placeholder = '';
                thinkingProviderEl.value = thinking.provider !== undefined && thinking.provider !== null ? String(thinking.provider) : '';
            }
        }
        setInputValue('aiThinkingBaseUrl', thinking.base_url, 'thinking_base_url');
        setInputValue('aiThinkingModel', thinking.model, 'thinking_model');
        document.getElementById('aiThinkingApiKey').value = '';
        setInputValue('aiThinkingPrompt', thinking.system_prompt, 'thinking_system_prompt', '');
    }
    if (settings.romanization) {
        const rp = document.getElementById('aiRomanizationPrompt');
        const ram = document.getElementById('aiRomanizationAlignmentMode');
        const rs = document.getElementById('aiRomanizationSeparator');
        const rst = document.getElementById('aiRomanizationStrict');
        const rtl = document.getElementById('aiRomanizationTrailing');
        if (rp) rp.value = settings.romanization.system_prompt || '';
        if (ram) {
            const am = String(settings.romanization.alignment_mode || '').trim().toLowerCase();
            ram.value = am === 'separator_tokens' ? 'separator_tokens' : 'indexed_tokens';
        }
        if (rs) rs.value = settings.romanization.separator || ';';
        if (rst) rst.checked = settings.romanization.strict_token_count !== false;
        if (rtl) rtl.checked = settings.romanization.require_trailing_separator !== false;
    }
    updateRomanizationAlignmentHint();
    const shouldSkipProviderPreset = skipProviderPreset
        || isAiFieldHidden('provider', visibility)
        || isAiFieldHidden('base_url', visibility)
        || isAiFieldHidden('model', visibility)
        || isAiFieldHidden('thinking_provider', visibility)
        || isAiFieldHidden('thinking_base_url', visibility)
        || isAiFieldHidden('thinking_model', visibility);
    if (!shouldSkipProviderPreset) {
        updateBaseUrlAndModel();
        updateThinkingBaseUrlAndModel();
    }
}

// 写入当前设置到 localStorage
function writeAIStateToLocalStorage(settings, permissions = {}) {
    const visibility = resolveAiFieldVisibility(settings, { fieldVisibility: settings._visibility });
    const translation = settings.translation || {};
    const thinking = settings.thinking || {};

    const shouldPersist = (permKey, visKey) => {
        if (permissions[permKey] === false) {
            return false;
        }
        return !isAiFieldHidden(visKey, visibility);
    };

    if (shouldPersist('ai_view_provider', 'provider')) {
        localStorage.setItem('aiProvider', translation.provider || '');
    } else {
        localStorage.removeItem('aiProvider');
    }
    if (shouldPersist('ai_view_provider', 'thinking_provider')) {
        localStorage.setItem('aiThinkingProvider', thinking.provider || '');
    } else {
        localStorage.removeItem('aiThinkingProvider');
    }
    if (shouldPersist('ai_view_base_url', 'base_url')) {
        localStorage.setItem('aiBaseUrl', translation.base_url || '');
    } else {
        localStorage.removeItem('aiBaseUrl');
    }
    if (shouldPersist('ai_view_base_url', 'thinking_base_url')) {
        if (thinking.base_url) {
            localStorage.setItem('aiThinkingBaseUrl', thinking.base_url);
        } else {
            localStorage.removeItem('aiThinkingBaseUrl');
        }
    } else {
        localStorage.removeItem('aiThinkingBaseUrl');
    }
    if (shouldPersist('ai_view_model', 'model')) {
        localStorage.setItem('aiModel', translation.model || '');
    } else {
        localStorage.removeItem('aiModel');
    }
    if (shouldPersist('ai_view_model', 'thinking_model')) {
        if (thinking.model) {
            localStorage.setItem('aiThinkingModel', thinking.model);
        } else {
            localStorage.removeItem('aiThinkingModel');
        }
    } else {
        localStorage.removeItem('aiThinkingModel');
    }
    if (shouldPersist('ai_view_prompts', 'system_prompt')) {
        if (translation.system_prompt) {
            localStorage.setItem('aiSystemPrompt', translation.system_prompt);
        } else {
            localStorage.removeItem('aiSystemPrompt');
        }
    } else {
        localStorage.removeItem('aiSystemPrompt');
    }
    if (shouldPersist('ai_view_prompts', 'thinking_system_prompt')) {
        if (thinking.system_prompt) {
            localStorage.setItem('aiThinkingPrompt', thinking.system_prompt);
        } else {
            localStorage.removeItem('aiThinkingPrompt');
        }
    } else {
        localStorage.removeItem('aiThinkingPrompt');
    }
    if (permissions.ai_view_prompts !== false && !isAiFieldHidden('system_prompt', visibility)) {
        const rom = settings.romanization || {};
        if (rom.system_prompt) {
            localStorage.setItem('aiRomanizationPrompt', rom.system_prompt);
        } else {
            localStorage.removeItem('aiRomanizationPrompt');
        }
        const ram = String(rom.alignment_mode || '').trim().toLowerCase();
        localStorage.setItem(
            'aiRomanizationAlignmentMode',
            ram === 'separator_tokens' ? 'separator_tokens' : 'indexed_tokens'
        );
        localStorage.setItem('aiRomanizationSeparator', rom.separator || ';');
        localStorage.setItem('aiRomanizationStrict', rom.strict_token_count !== false ? 'true' : 'false');
        localStorage.setItem('aiRomanizationTrailing', rom.require_trailing_separator !== false ? 'true' : 'false');
    } else {
        localStorage.removeItem('aiRomanizationPrompt');
        localStorage.removeItem('aiRomanizationAlignmentMode');
        localStorage.removeItem('aiRomanizationSeparator');
        localStorage.removeItem('aiRomanizationStrict');
        localStorage.removeItem('aiRomanizationTrailing');
    }
    localStorage.setItem('aiExpectReasoning', translation.expect_reasoning);
    localStorage.setItem('aiCompatMode', translation.compat_mode);
    localStorage.setItem('aiStripBrackets', translation.strip_brackets);
    localStorage.setItem('aiExperimentalFullLineBracketStrip', translation.experimental_full_line_bracket_strip);
    localStorage.setItem('aiExperimentalBracketLineAsSubline', translation.experimental_bracket_line_as_subline);

    localStorage.setItem('aiThinkingEnabled', thinking.enabled);
}

// 从 localStorage 读取当前设置
function readAIStateFromLocalStorage() {
    return {
        translation: {
            provider: localStorage.getItem('aiProvider') || '',
            base_url: localStorage.getItem('aiBaseUrl') || '',
            model: localStorage.getItem('aiModel') || '',
            api_key: '',
            system_prompt: localStorage.getItem('aiSystemPrompt') || '',
            expect_reasoning: localStorage.getItem('aiExpectReasoning') === 'true',
            compat_mode: localStorage.getItem('aiCompatMode') === 'true',
            strip_brackets: localStorage.getItem('aiStripBrackets') === 'true',
            experimental_full_line_bracket_strip: localStorage.getItem('aiExperimentalFullLineBracketStrip') === 'true',
            experimental_bracket_line_as_subline: localStorage.getItem('aiExperimentalBracketLineAsSubline') === 'true'
        },
        thinking: {
            enabled: localStorage.getItem('aiThinkingEnabled') !== 'false',
            provider: localStorage.getItem('aiThinkingProvider') || '',
            base_url: localStorage.getItem('aiThinkingBaseUrl') || '',
            model: localStorage.getItem('aiThinkingModel') || '',
            api_key: '',
            system_prompt: localStorage.getItem('aiThinkingPrompt') || ''
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
    };
}

// 加载预设列表
function loadAiPresets() {
    if (!Array.isArray(aiPresetCache)) {
        return [];
    }
    return aiPresetCache.slice();
}

// 保存预设列表
async function saveAiPresets(presets) {
    const normalized = normalizeAiPresetList(presets);
    const sanitized = normalized.map(preset => ({
        ...preset,
        translation: { ...preset.translation, api_key: '' },
        thinking: { ...preset.thinking, api_key: '' }
    }));
    const isLocalHost = ['localhost', '127.0.0.1', '::1'].includes(location.hostname);
    if (!isLocalHost && !hasFullAiPresetVisibility()) {
        throw new Error('当前设备没有整表同步权限');
    }

    const requestBody = {
        presets: normalized
    };
    const response = await fetch('/ai-presets', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(requestBody)
    });
    const data = await response.json();
    if (!response.ok || data.status !== 'success') {
        throw new Error(data.message || t('alert.settingsSaveFailed'));
    }

    aiPresetCache = sanitized.slice();
    try {
        localStorage.setItem(AI_PRESETS_STORAGE_KEY, JSON.stringify(sanitized));
    } catch (storageError) {
        console.warn('Failed to persist AI presets cache locally:', storageError);
    }
    return data;
}

async function syncAiPresetStoreToBackend(presets, activePresetId = '') {
    const requestBody = {
        presets
    };
    if (activePresetId) {
        requestBody.active_preset_id = activePresetId;
    }
    const response = await fetch('/ai-presets', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(requestBody)
    });
    const data = await response.json();
    if (!response.ok || data.status !== 'success') {
        throw new Error(data.message || t('alert.settingsSaveFailed'));
    }
    await refreshAiPresetCache();
    return data;
}

async function upsertAiPresetOnBackend(presetId, payload) {
    const response = await fetch(`/ai-presets/${encodeURIComponent(presetId)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload || {})
    });
    const data = await response.json();
    if (!response.ok || data.status !== 'success') {
        throw new Error(data.message || t('alert.settingsSaveFailed'));
    }
    await refreshAiPresetCache();
    return data;
}

async function createAiPresetOnBackend(payload) {
    const response = await fetch('/ai-presets', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload || {})
    });
    const data = await response.json();
    if (!response.ok || data.status !== 'success') {
        throw new Error(data.message || t('alert.settingsSaveFailed'));
    }
    await refreshAiPresetCache();
    return data;
}

async function deleteAiPresetOnBackend(presetId) {
    const response = await fetch(`/ai-presets/${encodeURIComponent(presetId)}`, {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' }
    });
    const data = await response.json();
    if (!response.ok || data.status !== 'success') {
        throw new Error(data.message || t('alert.settingsSaveFailed'));
    }
    await refreshAiPresetCache();
    return data;
}

function buildAiPresetPayloadFromForm(options = {}) {
    const { includeSecrets = true, includeName = false } = options;
    const permissions = aiPresetPermissions || {};
    const payload = {};
    const presetSelect = document.getElementById('aiPresetSelect');
    if (includeName) {
        const nameInput = document.getElementById('aiPresetName');
        if (nameInput && nameInput.value.trim()) {
            payload.name = nameInput.value.trim();
        }
    }
    if (permissions.ai_view_provider !== false) {
        payload.provider = document.getElementById('aiProvider').value;
        payload.thinking_provider = document.getElementById('aiThinkingProvider').value;
    }
    if (permissions.ai_view_base_url !== false) {
        payload.base_url = sanitizeBaseUrl(document.getElementById('aiBaseUrl').value);
        payload.thinking_base_url = sanitizeBaseUrl(document.getElementById('aiThinkingBaseUrl').value);
    }
    if (permissions.ai_view_model !== false) {
        payload.model = document.getElementById('aiModel').value;
        payload.thinking_model = document.getElementById('aiThinkingModel').value;
    }
    if (permissions.ai_view_prompts !== false) {
        const systemPrompt = document.getElementById('aiSystemPrompt').value;
        if (systemPrompt && systemPrompt.trim()) payload.system_prompt = systemPrompt;
        const thinkingPrompt = document.getElementById('aiThinkingPrompt').value;
        if (thinkingPrompt && thinkingPrompt.trim()) payload.thinking_system_prompt = thinkingPrompt;
        const rPrompt = document.getElementById('aiRomanizationPrompt')?.value ?? '';
        payload.romanization_system_prompt = rPrompt;
        const rAlign = document.getElementById('aiRomanizationAlignmentMode')?.value ?? 'indexed_tokens';
        payload.romanization_alignment_mode = String(rAlign).trim().toLowerCase() === 'separator_tokens'
            ? 'separator_tokens'
            : 'indexed_tokens';
        const rSep = document.getElementById('aiRomanizationSeparator')?.value ?? ';';
        payload.romanization_separator = String(rSep).slice(0, 8);
        payload.romanization_strict_token_count = document.getElementById('aiRomanizationStrict')?.checked ?? true;
        payload.romanization_require_trailing_separator = document.getElementById('aiRomanizationTrailing')?.checked ?? true;
    }
    payload.expect_reasoning = document.getElementById('aiExpectReasoning').checked;
    payload.compat_mode = document.getElementById('aiCompatMode').checked;
    payload.strip_brackets = document.getElementById('aiStripBrackets').checked;
    payload.experimental_full_line_bracket_strip = document.getElementById('aiExperimentalFullLineBracketStrip').checked;
    payload.experimental_bracket_line_as_subline = document.getElementById('aiExperimentalBracketLineAsSubline').checked;
    payload.thinking_enabled = document.getElementById('aiThinkingEnabled').checked;
    payload.batch_auto_save = true;
    payload.batch_only_empty = true;
    payload.batch_always_override = false;
    const extraPrompt = document.getElementById('batchExtraPrompt')?.value || '';
    if (permissions.ai_view_prompts !== false && extraPrompt) {
        payload.batch_extra_prompt = extraPrompt;
    }
    if (includeSecrets) {
        const apiKey = document.getElementById('aiApiKey').value;
        const thinkingApiKey = document.getElementById('aiThinkingApiKey').value;
        if (apiKey) payload.api_key = apiKey;
        if (thinkingApiKey) payload.thinking_api_key = thinkingApiKey;
    }
    if (presetSelect && presetSelect.value) {
        payload.preset_id = presetSelect.value;
    }
    return payload;
}

function applyAiPresetFieldPermissions(permissions) {
    const viewProvider = permissions?.ai_view_provider !== false;
    const viewBaseUrl = permissions?.ai_view_base_url !== false;
    const viewModel = permissions?.ai_view_model !== false;
    const viewPrompts = permissions?.ai_view_prompts !== false;
    const mainFields = [
        ['aiProvider', viewProvider],
        ['aiBaseUrl', viewBaseUrl],
        ['aiModel', viewModel],
        ['aiSystemPrompt', viewPrompts],
        ['aiRomanizationPrompt', viewPrompts],
        ['aiRomanizationAlignmentMode', viewPrompts],
        ['aiRomanizationSeparator', viewPrompts],
        ['aiRomanizationStrict', viewPrompts],
        ['aiRomanizationTrailing', viewPrompts],
        ['aiThinkingProvider', viewProvider],
        ['aiThinkingBaseUrl', viewBaseUrl],
        ['aiThinkingModel', viewModel],
        ['aiThinkingPrompt', viewPrompts],
        ['batchExtraPrompt', viewPrompts],
    ];
    mainFields.forEach(([id, enabled]) => {
        const el = document.getElementById(id);
        if (el) el.disabled = !enabled;
    });
}

function applyAiSettingsButtonPermissions(canUseAi, canEditPreset) {
    const applyManualBtn = document.getElementById('aiApplyManualSettingsBtn');
    const bindPresetBtn = document.getElementById('aiBindPresetBtn');
    const translationTestBtn = document.getElementById('aiSettingsTestTranslationBtn');
    const thinkingTestBtn = document.getElementById('aiSettingsTestThinkingBtn');
    [applyManualBtn, bindPresetBtn, translationTestBtn, thinkingTestBtn].forEach(btn => {
        if (btn) {
            btn.disabled = !canUseAi;
        }
    });

    const presetButtons = [
        'aiPresetSaveAsBtn',
        'aiPresetUpdateBtn',
        'aiPresetRenameBtn',
        'aiPresetDuplicateBtn',
        'aiPresetDeleteBtn',
    ];
    presetButtons.forEach(id => {
        const btn = document.getElementById(id);
        if (btn) {
            btn.disabled = !canEditPreset;
        }
    });
}

function hasFullAiPresetVisibility() {
    const permissions = aiPresetPermissions || {};
    return Boolean(
        permissions.ai_view_provider &&
        permissions.ai_view_base_url &&
        permissions.ai_view_model &&
        permissions.ai_view_prompts
    );
}

function generateAiPresetId() {
    return `preset_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

function normalizeAiPreset(preset, fallbackId) {
    const state = getBatchWorkbenchPresetStateFromPreset(preset || {});
    const translationSecret = typeof preset?.translation?.api_key === 'string'
        ? preset.translation.api_key
        : (typeof preset?.api_key === 'string' ? preset.api_key : '');
    const thinkingSecret = typeof preset?.thinking?.api_key === 'string'
        ? preset.thinking.api_key
        : (typeof preset?.thinking_api_key === 'string' ? preset.thinking_api_key : '');
    const id = typeof preset?.id === 'string' && preset.id.trim() ? preset.id.trim() : (fallbackId || generateAiPresetId());
    const name = typeof preset?.name === 'string' && preset.name.trim() ? preset.name.trim() : t('preset.defaultName');
    return {
        id,
        name,
        owner_scope: typeof preset?.owner_scope === 'string' && preset.owner_scope.trim() ? preset.owner_scope.trim() : (id === 'default' ? 'system' : 'personal'),
        acl: preset?.acl && typeof preset.acl === 'object' && !Array.isArray(preset.acl) ? preset.acl : {},
        translation: { ...state.translation, api_key: translationSecret },
        thinking: { ...state.thinking, api_key: thinkingSecret },
        batch: state.batch,
        romanization: { ...(state.romanization || {}) },
        updated_at: Number.isFinite(Number(preset?.updated_at))
            ? Number(preset.updated_at)
            : (Date.parse(preset?.updated_at) || Date.now())
    };
}

function normalizeAiPresetList(presets) {
    if (!Array.isArray(presets)) {
        return [];
    }
    return presets.map(preset => normalizeAiPreset(preset));
}

function getSelectedAiPresetIdForExport() {
    const select = document.getElementById('aiPresetSelect');
    const savedId = getSavedAiPresetSelectId();
    const selectedId = select ? select.value : '';
    return selectedId || savedId || '';
}

function downloadAiPresetJson(filename, payload) {
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    setTimeout(() => URL.revokeObjectURL(url), 0);
}

function exportAiPresets() {
    const scope = prompt(t('preset.exportScope'), '1');
    if (scope === null) {
        return;
    }

    const includeCurrent = scope.trim() === '1' || scope.trim().toLowerCase() === 'current';
    const includeAll = scope.trim() === '2' || scope.trim().toLowerCase() === 'all';
    if (!includeCurrent && !includeAll) {
        alert(t('alert.enter1or2'));
        return;
    }

    const presets = normalizeAiPresetList(loadAiPresets());
    let exportPresets = [];

    if (includeAll) {
        exportPresets = presets;
    } else {
        const presetId = getSelectedAiPresetIdForExport();
        if (!presetId) {
            alert(t('alert.selectPreset'));
            return;
        }
        const preset = presets.find(item => item.id === presetId);
        if (!preset) {
            alert(t('alert.presetNotExistSelected'));
            return;
        }
        exportPresets = [preset];
    }

    const sanitizedPresets = exportPresets.map(preset => ({
        ...preset,
        translation: {
            ...preset.translation,
            api_key: ''
        },
        thinking: {
            ...preset.thinking,
            api_key: ''
        }
    }));

    const payload = {
        type: AI_PRESETS_EXPORT_TYPE,
        version: AI_PRESETS_EXPORT_VERSION,
        exported_at: Date.now(),
        presets: sanitizedPresets
    };

    const fileSuffix = includeAll ? 'all' : 'current';
    downloadAiPresetJson(`ai-presets-${fileSuffix}-${Date.now()}.json`, payload);
}

function triggerAiPresetImport() {
    const input = document.getElementById('aiPresetImportInput');
    if (!input) {
        alert(t('alert.importUnavailable'));
        return;
    }

    const replaceAll = confirm(t('confirm.replaceAllPresets'));
    input.dataset.importMode = replaceAll ? 'replace' : 'merge';
    input.click();
}

function collectImportedAiPresets(rawPayload) {
    if (!rawPayload || typeof rawPayload !== 'object' || Array.isArray(rawPayload)) {
        throw new Error(t('runtime.presetFormatError', {index: 0}));
    }
    if (rawPayload.type !== AI_PRESETS_EXPORT_TYPE) {
        throw new Error(t('runtime.presetFormatError', {index: 0}));
    }
    if (rawPayload.version !== AI_PRESETS_EXPORT_VERSION) {
        throw new Error(t('runtime.presetFormatError', {index: 0}));
    }
    if (Array.isArray(rawPayload.presets)) {
        return rawPayload.presets;
    }
    if (Array.isArray(rawPayload.data)) {
        return rawPayload.data;
    }
    throw new Error(t('runtime.presetFormatError', {index: 0}));
}

function syncActiveAiPresetKeys(presets) {
    const activeId = localStorage.getItem(ACTIVE_AI_PRESET_KEY) || '';
    const batchActiveId = localStorage.getItem(BATCH_WORKBENCH_ACTIVE_PRESET_KEY_SAFE) || '';
    const presetIds = new Set(presets.map(preset => preset.id));
    const result = {
        mainRemoved: false,
        batchRemoved: false
    };

    if (activeId && !presetIds.has(activeId)) {
        syncActiveAiPresetKeyWithSavedSource();
        result.mainRemoved = true;
    }
    if (batchActiveId && !presetIds.has(batchActiveId)) {
        localStorage.removeItem(BATCH_WORKBENCH_ACTIVE_PRESET_KEY_SAFE);
        batchWorkbenchState.activePresetId = '';
        result.batchRemoved = true;
    }

    return result;
}

function makeUniqueAiPresetName(baseName, usedNames) {
    const fallbackName = t('preset.defaultName');
    const trimmed = (baseName || fallbackName).trim() || fallbackName;
    if (!usedNames.has(trimmed)) {
        return trimmed;
    }
    let index = 2;
    while (usedNames.has(`${trimmed} (${index})`)) {
        index += 1;
    }
    return `${trimmed} (${index})`;
}

async function importAiPresetsFromData(rawData, importMode) {
    const importedPresets = collectImportedAiPresets(rawData);

    const normalizedImportedPresets = importedPresets.map((preset, index) => {
        if (!preset || typeof preset !== 'object' || Array.isArray(preset)) {
            throw new Error(`第 ${index + 1} 个预设格式不正确`);
        }
        return normalizeAiPreset(preset);
    });
    const importedPresetIdSet = new Set(normalizedImportedPresets.map(preset => preset.id));
    const currentPresets = normalizeAiPresetList(loadAiPresets());
    let nextPresets = [];

    if (importMode === 'replace') {
        const usedIds = new Set();
        const usedNames = new Set();
        normalizedImportedPresets.forEach(preset => {
            let nextPreset = { ...preset };
            if (usedIds.has(nextPreset.id)) {
                nextPreset = {
                    ...nextPreset,
                    id: generateAiPresetId()
                };
            }
            if (usedNames.has(nextPreset.name)) {
                nextPreset = {
                    ...nextPreset,
                    name: makeUniqueAiPresetName(nextPreset.name, usedNames)
                };
            }
            nextPresets.push(nextPreset);
            usedIds.add(nextPreset.id);
            usedNames.add(nextPreset.name);
        });
    } else {
        nextPresets = currentPresets.slice();
        const usedIds = new Set(currentPresets.map(preset => preset.id));
        const usedNames = new Set(currentPresets.map(preset => preset.name));

        normalizedImportedPresets.forEach(preset => {
            let nextPreset = { ...preset };
            if (usedIds.has(nextPreset.id)) {
                nextPreset = {
                    ...nextPreset,
                    id: generateAiPresetId()
                };
            }
            if (usedNames.has(nextPreset.name)) {
                nextPreset = {
                    ...nextPreset,
                    name: makeUniqueAiPresetName(nextPreset.name, usedNames)
                };
            }
            nextPresets.push(nextPreset);
            usedIds.add(nextPreset.id);
            usedNames.add(nextPreset.name);
        });
    }

    nextPresets = normalizeAiPresetList(nextPresets);
    await saveAiPresets(nextPresets);
    const activeBatchPresetId = localStorage.getItem(BATCH_WORKBENCH_ACTIVE_PRESET_KEY_SAFE) || '';
    const nextPresetMap = new Map(nextPresets.map(preset => [preset.id, preset]));
    if (importMode === 'replace' && activeBatchPresetId && importedPresetIdSet.has(activeBatchPresetId)) {
        const updatedBatchPreset = nextPresetMap.get(activeBatchPresetId);
        if (updatedBatchPreset) {
            applyBatchWorkbenchPreset(updatedBatchPreset);
        }
    }
    const syncResult = syncActiveAiPresetKeys(nextPresets);
    if (syncResult.batchRemoved) {
        applyBatchWorkbenchSettingsToUI();
    }
    updateAiPresetSelect();
    updateQuickAiPresetSelect();
    safeUpdateBatchWorkbenchPresetSelect();

    return nextPresets.length;
}

async function handleAiPresetImport(event) {
    const input = event.target;
    const file = input && input.files ? input.files[0] : null;
    const importMode = input?.dataset?.importMode || 'merge';

    try {
        if (!file) {
            return;
        }

        const text = await file.text();
        const parsed = JSON.parse(text);
        const importedCount = await importAiPresetsFromData(parsed, importMode);
        alert(t('runtime.importSuccess', {count: importedCount}));
    } catch (error) {
        alert(error.message || t('runtime.importFailed'));
    } finally {
        if (input) {
            input.value = '';
            if (input.dataset) {
                delete input.dataset.importMode;
            }
        }
    }
}

// 应用预设
function applyAiPreset(preset) {
    if (!preset) {
        const select = document.getElementById('aiPresetSelect');
        const presetId = select.value;
        if (!presetId) {
            alert(t('alert.selectPreset'));
            return;
        }
        const presets = loadAiPresets();
        preset = presets.find(p => p.id === presetId);
        if (!preset) {
            alert(t('alert.presetNotExist'));
            return;
        }
    }

    // 填入表单
    fillAIFormState(preset, { fieldVisibility: resolveAiFieldVisibility(preset) });
    const classified = classifyAiPresetKind(preset);
    setAiSettingsSourceDraft({
        mode: 'preset',
        preset_id: preset.id,
        preset_name: preset.name,
        kind: classified.kind,
        label: classified.label
    });
    setAiSettingsStatus(hasPendingAiSettingsPreview() ? 'preview-preset' : 'idle', preset.name);
    updateAiPresetApplyStatus();
}

// 更新预设选择框
function updateAiPresetSelect() {
    const select = document.getElementById('aiPresetSelect');
    const presets = loadAiPresets();
    const selectedId = getAiPresetSelectTargetId();
    const savedId = getSavedAiPresetSelectId();

    select.innerHTML = '<option value="">' + t('aiSettings.selectPresetOption') + '</option>';
    presets.forEach(preset => {
        const option = document.createElement('option');
        option.value = preset.id;
        option.textContent = preset.name;
        if (selectedId && preset.id === selectedId) {
            option.selected = true;
        }
        select.appendChild(option);
    });

    if (savedId && !presets.some(preset => preset.id === savedId)) {
        appendMissingAiPresetOption(select, savedId, selectedId);
    }

    // 添加 onchange 事件监听器，实现选择后自动应用
    select.onchange = function() {
        const presetId = this.value;
        if (!presetId) {
            setAiSettingsSourceManual();
            return;
        }
        previewSelectedAiPreset();
    };
}

function getSelectedAiPresetContext(alertKey) {
    const select = document.getElementById('aiPresetSelect');
    const presetId = select ? select.value : '';
    if (!presetId) {
        alert(t(alertKey));
        return null;
    }

    const presets = loadAiPresets();
    const index = presets.findIndex(preset => preset.id === presetId);
    if (index === -1) {
        alert(t('alert.presetNotExist'));
        return null;
    }

    return { select, presetId, presets, index, preset: presets[index] };
}

// 另存为预设
async function saveAsAiPreset() {
    const name = prompt(t('preset.inputName'));
    if (!name || !name.trim()) {
        return;
    }

    const isLocalHost = ['localhost', '127.0.0.1', '::1'].includes(location.hostname);
    if (!isLocalHost && !hasFullAiPresetVisibility()) {
        alert('当前设备没有完整预设可见权限，不能另存为新预设');
        return;
    }

    const settings = omitEmptyEndpointFields(collectAIFormState());
    const preset = {
        id: 'preset_' + Date.now(),
        name: name.trim(),
        ...settings,
        updated_at: Date.now()
    };

    await createAiPresetOnBackend(preset);
    
    updateAiPresetSelect();

    alert(t('runtime.presetSaved', {name: name}));
    updateQuickAiPresetSelect(); // 更新快速选择框
    safeUpdateBatchWorkbenchPresetSelect();
}

// 更新当前预设
async function updateCurrentAiPreset() {
    const select = document.getElementById('aiPresetSelect');
    const presetId = select.value;
    if (!presetId) {
        alert(t('alert.selectPresetToUpdate'));
        return;
    }

    const settings = omitEmptyEndpointFields(collectAIFormState());
    const payload = {
        ...settings,
        id: presetId,
        updated_at: Date.now()
    };
    await upsertAiPresetOnBackend(presetId, payload);

    updateAiPresetSelect();

    const updatedPreset = loadAiPresets().find(p => p.id === presetId);
    alert(t('runtime.presetUpdated', {name: updatedPreset?.name || t('preset.defaultName')}));
    updateQuickAiPresetSelect(); // 更新快速选择框
    safeUpdateBatchWorkbenchPresetSelect();
}

async function renameCurrentAiPreset() {
    const context = getSelectedAiPresetContext('alert.selectPresetToRename');
    if (!context) {
        return;
    }

    const currentName = context.preset.name || '';
    const inputName = prompt(t('preset.renameInputName'), currentName);
    if (inputName === null) {
        return;
    }

    const trimmedName = inputName.trim();
    if (!trimmedName) {
        return;
    }

    const usedNames = new Set(context.presets.filter(preset => preset.id !== context.presetId).map(preset => preset.name));
    const nextName = makeUniqueAiPresetName(trimmedName, usedNames);

    await upsertAiPresetOnBackend(context.presetId, {
        id: context.presetId,
        name: nextName,
        updated_at: Date.now()
    });

    updateAiPresetSelect();
    updateQuickAiPresetSelect();
    safeUpdateBatchWorkbenchPresetSelect();

    alert(t('runtime.presetRenamed', {name: nextName}));
}

async function duplicateCurrentAiPreset() {
    const context = getSelectedAiPresetContext('alert.selectPresetToDuplicate');
    if (!context) {
        return;
    }

    const isLocalHost = ['localhost', '127.0.0.1', '::1'].includes(location.hostname);
    if (!isLocalHost && !hasFullAiPresetVisibility()) {
        alert('当前设备没有完整预设可见权限，不能复制预设');
        return;
    }

    const usedNames = new Set(context.presets.map(preset => preset.name));
    const duplicateName = makeUniqueAiPresetName(`${context.preset.name || t('preset.defaultName')}${t('preset.duplicateSuffix')}`, usedNames);
    const duplicatedPreset = {
        ...context.preset,
        id: generateAiPresetId(),
        name: duplicateName,
        updated_at: Date.now()
    };

    await createAiPresetOnBackend(duplicatedPreset);

    updateAiPresetSelect();
    applyAiPreset(duplicatedPreset);

    alert(t('runtime.presetDuplicated', {name: duplicateName}));
}

// 删除预设
async function deleteAiPreset() {
    const select = document.getElementById('aiPresetSelect');
    const presetId = select.value;
    if (!presetId) {
        alert(t('alert.selectPresetToDelete'));
        return;
    }

    if (!confirm(t('confirm.deletePreset'))) {
        return;
    }

    await deleteAiPresetOnBackend(presetId);
    updateAiPresetSelect();

    // 如果删除的是活动预设，清除活动预设
    syncActiveAiPresetKeyWithSavedSource();

    alert(t('alert.presetDeleted'));
    updateQuickAiPresetSelect(); // 更新快速选择框
    safeUpdateBatchWorkbenchPresetSelect();
}

// 更新快速预设选择框
function updateQuickAiPresetSelect() {
    const select = document.getElementById('quickAiPresetSelect');
    if (!select) return;

    const presets = loadAiPresets();
    const savedId = getSavedAiPresetSelectId();

    select.innerHTML = '<option value="">' + t('batch.selectPresetQuick') + '</option>';
    presets.forEach(preset => {
        const option = document.createElement('option');
        option.value = preset.id;
        option.textContent = preset.name;
        if (preset.id === savedId) {
            option.selected = true;
        }
        select.appendChild(option);
    });

    if (savedId && !presets.some(preset => preset.id === savedId)) {
        appendMissingAiPresetOption(select, savedId, savedId);
    }
}

// 快速应用预设
function quickApplyAiPreset() {
    const select = document.getElementById('quickAiPresetSelect');
    if (!select) return;

    const presetId = select.value;
    if (!presetId) {
        alert(t('alert.selectPreset'));
        return;
    }

    const presets = loadAiPresets();
    const preset = presets.find(p => p.id === presetId);
    if (!preset) {
        alert(t('alert.presetNotExist'));
        return;
    }

    applyAiPreset(preset);
    updateQuickAiPresetSelect(); // 更新选择状态
}

// 根据选择的提供商更新基础URL和模型（仅在用户主动切换时）
function applyProviderPreset(providerSelectId, baseInputId, modelInputId, forceApply = false) {
    const providerSelect = document.getElementById(providerSelectId);
    const baseUrlInput = document.getElementById(baseInputId);
    const modelInput = document.getElementById(modelInputId);

    if (!providerSelect || !baseUrlInput || !modelInput) {
        return;
    }

    const providerFieldKey = providerSelectId === 'aiThinkingProvider' || providerSelectId === 'batchWbAiThinkingProvider'
        ? 'thinking_provider'
        : 'provider';
    const baseFieldKey = providerSelectId === 'aiThinkingProvider' || providerSelectId === 'batchWbAiThinkingProvider'
        ? 'thinking_base_url'
        : 'base_url';
    const modelFieldKey = providerSelectId === 'aiThinkingProvider' || providerSelectId === 'batchWbAiThinkingProvider'
        ? 'thinking_model'
        : 'model';

    if (!forceApply) {
        return;
    }
    if (isAiFieldHidden(providerFieldKey, aiFieldVisibility)
        || isAiFieldHidden(baseFieldKey, aiFieldVisibility)
        || isAiFieldHidden(modelFieldKey, aiFieldVisibility)) {
        return;
    }

    const provider = providerSelect.value;
    const preset = PROVIDER_PRESETS[provider];

    if (provider === 'custom' || !preset) {
        baseUrlInput.readOnly = false;
        modelInput.readOnly = false;
        return;
    }

    baseUrlInput.value = preset.baseUrl;
    modelInput.value = preset.model;
    baseUrlInput.readOnly = true;
    modelInput.readOnly = true;
}

function updateBaseUrlAndModel(forceApply = false) {
    applyProviderPreset('aiProvider', 'aiBaseUrl', 'aiModel', forceApply);
    refreshReasoningControlCapabilityHint().catch(() => {});
}

function updateThinkingBaseUrlAndModel(forceApply = false) {
    applyProviderPreset('aiThinkingProvider', 'aiThinkingBaseUrl', 'aiThinkingModel', forceApply);
}

function updateBatchWorkbenchBaseUrlAndModel(forceApply = false) {
    applyProviderPreset('batchWbAiProvider', 'batchWbAiBaseUrl', 'batchWbAiModel', forceApply);
}

// 页面加载完成后添加事件监听器
document.addEventListener('DOMContentLoaded', function() {
    const providerSelect = document.getElementById('aiProvider');
    if (providerSelect) {
        providerSelect.addEventListener('change', () => {
            updateBaseUrlAndModel(true);
            markAiSettingsEdited();
        });
    }
    const baseUrlInput = document.getElementById('aiBaseUrl');
    if (baseUrlInput) {
        baseUrlInput.addEventListener('input', () => {
            refreshReasoningControlCapabilityHint().catch(() => {});
            markAiSettingsEdited();
        });
    }
    const modelInput = document.getElementById('aiModel');
    if (modelInput) {
        modelInput.addEventListener('input', () => {
            refreshReasoningControlCapabilityHint().catch(() => {});
            markAiSettingsEdited();
        });
    }
    const thinkingProviderSelect = document.getElementById('aiThinkingProvider');
    if (thinkingProviderSelect) {
        thinkingProviderSelect.addEventListener('change', () => {
            updateThinkingBaseUrlAndModel(true);
            markAiSettingsEdited();
        });
    }
    [
        'aiSystemPrompt', 'aiApiKey', 'aiExpectReasoning', 'aiCompatMode', 'aiStripBrackets',
        'aiExperimentalFullLineBracketStrip', 'aiExperimentalBracketLineAsSubline', 'aiThinkingEnabled',
        'aiRomanizationPrompt', 'aiRomanizationSeparator', 'aiRomanizationStrict', 'aiRomanizationTrailing',
        'aiThinkingBaseUrl', 'aiThinkingModel', 'aiThinkingApiKey', 'aiThinkingPrompt'
    ].forEach(id => {
        const el = document.getElementById(id);
        if (!el) return;
        const eventName = el.tagName === 'SELECT' || el.type === 'checkbox' ? 'change' : 'input';
        el.addEventListener(eventName, markAiSettingsEdited);
    });
    const romanAlignSel = document.getElementById('aiRomanizationAlignmentMode');
    if (romanAlignSel) {
        romanAlignSel.addEventListener('change', () => {
            updateRomanizationAlignmentHint();
            markAiSettingsEdited();
        });
    }
    if (typeof registerDynamicI18nCallback === 'function') {
        registerDynamicI18nCallback(updateRomanizationAlignmentHint);
    }
    refreshAiPresetCache().catch(() => {})
});

async function refreshReasoningControlCapabilityHint() {
    const hintEl = document.getElementById('aiReasoningCapabilityHint');
    const checkEl = document.getElementById('aiExpectReasoning');
    if (!hintEl || !checkEl) return;

    const provider = document.getElementById('aiProvider')?.value || '';
    const baseUrl = sanitizeBaseUrl(document.getElementById('aiBaseUrl')?.value || '');
    const model = document.getElementById('aiModel')?.value || '';

    if (!provider || !baseUrl || !model) {
        hintEl.style.display = 'none';
        checkEl.disabled = false;
        return;
    }

    const url = new URL('/ai/reasoning-control-capability', window.location.origin);
    url.searchParams.set('provider', provider);
    url.searchParams.set('base_url', baseUrl);
    url.searchParams.set('model', model);
    const res = await fetch(url.toString());
    const data = await res.json();
    if (data.status !== 'success') {
        hintEl.style.display = 'block';
        hintEl.style.color = '#b91c1c';
        hintEl.textContent = t('aiSettings.reasoningCapabilityCheckFailed');
        checkEl.disabled = false;
        return;
    }
    const cap = data.capability || {};
    const supported = Boolean(cap.supported);
    const userSelectable = cap.user_selectable !== false;
    const status = String(cap.status || 'unknown');
    if (supported) {
        hintEl.style.display = 'block';
        hintEl.style.color = 'var(--text-secondary)';
        hintEl.textContent = t('aiSettings.reasoningCapabilityConfirmed');
        checkEl.disabled = false;
        return;
    }

    hintEl.style.display = 'block';
    hintEl.style.color = '#b45309';
    if (status === 'provider_defined') {
        hintEl.textContent = t('aiSettings.reasoningCapabilityProviderDefined');
    } else {
        hintEl.textContent = t('aiSettings.reasoningCapabilityUnknown');
    }
    // Unknown/unsupported should still remain user selectable by design.
    checkEl.disabled = !userSelectable ? true : false;
}

function sanitizeBaseUrl(value) {
    if (!value) {
        return '';
    }
    return value.trim().replace(/\/+(chat|responses)\/(completions|streams?)\/?$/i, '');
}

function buildEffectiveAISettingsFromResponse(settings, options = {}) {
    const visibility = resolveAiFieldVisibility(settings, options);
    const translation = settings?.translation || {};
    const thinking = settings?.thinking || {};
    return {
        translation: {
            provider: translation.provider !== undefined ? translation.provider : '',
            base_url: translation.base_url !== undefined ? translation.base_url : '',
            model: translation.model !== undefined ? translation.model : '',
            api_key: '',
            system_prompt: translation.system_prompt !== undefined ? translation.system_prompt : '',
            expect_reasoning: translation.expect_reasoning !== undefined ? translation.expect_reasoning : false,
            compat_mode: translation.compat_mode !== undefined ? translation.compat_mode : false,
            strip_brackets: translation.strip_brackets !== undefined ? translation.strip_brackets : false,
            experimental_full_line_bracket_strip: translation.experimental_full_line_bracket_strip !== undefined ? translation.experimental_full_line_bracket_strip : false,
            experimental_bracket_line_as_subline: translation.experimental_bracket_line_as_subline !== undefined ? translation.experimental_bracket_line_as_subline : false
        },
        thinking: {
            enabled: thinking.enabled !== undefined ? thinking.enabled : true,
            provider: thinking.provider !== undefined ? thinking.provider : '',
            base_url: thinking.base_url !== undefined ? thinking.base_url : '',
            model: thinking.model !== undefined ? thinking.model : '',
            api_key: '',
            system_prompt: thinking.system_prompt !== undefined ? thinking.system_prompt : ''
        },
        romanization: (() => {
            const r = settings?.romanization || {};
            const am = String(r.alignment_mode || '').trim().toLowerCase();
            return {
                system_prompt: r.system_prompt !== undefined ? r.system_prompt : '',
                alignment_mode: am === 'separator_tokens' ? 'separator_tokens' : 'indexed_tokens',
                separator: r.separator !== undefined ? r.separator : ';',
                strict_token_count: r.strict_token_count !== undefined ? Boolean(r.strict_token_count) : true,
                require_trailing_separator: r.require_trailing_separator !== undefined ? Boolean(r.require_trailing_separator) : true
            };
        })(),
        _visibility: visibility
    };
}

// Empty base_url/model fields are omitted so preset/local values are preserved.
function buildAISavePayload() {
    const apiKey = document.getElementById('aiApiKey').value;
    const systemPrompt = document.getElementById('aiSystemPrompt').value;
    const provider = document.getElementById('aiProvider').value;
    const baseUrl = sanitizeBaseUrl(document.getElementById('aiBaseUrl').value);
    document.getElementById('aiBaseUrl').value = baseUrl;
    const model = document.getElementById('aiModel').value;
    const expectReasoning = document.getElementById('aiExpectReasoning').checked;
    const compatMode = document.getElementById('aiCompatMode').checked;
    const stripBrackets = document.getElementById('aiStripBrackets').checked;
    const experimentalFullLineBracketStrip = document.getElementById('aiExperimentalFullLineBracketStrip').checked;
    const experimentalBracketLineAsSubline = document.getElementById('aiExperimentalBracketLineAsSubline').checked;
    const thinkingEnabled = document.getElementById('aiThinkingEnabled').checked;
    const thinkingProvider = document.getElementById('aiThinkingProvider').value;
    const thinkingBaseUrl = sanitizeBaseUrl(document.getElementById('aiThinkingBaseUrl').value);
    document.getElementById('aiThinkingBaseUrl').value = thinkingBaseUrl;
    const thinkingModel = document.getElementById('aiThinkingModel').value;
    const thinkingApiKey = document.getElementById('aiThinkingApiKey').value;
    const thinkingPrompt = document.getElementById('aiThinkingPrompt').value;
    const permissions = aiPresetPermissions || {};

    const payload = {
        expect_reasoning: expectReasoning,
        compat_mode: compatMode,
        strip_brackets: stripBrackets,
        experimental_full_line_bracket_strip: experimentalFullLineBracketStrip,
        experimental_bracket_line_as_subline: experimentalBracketLineAsSubline,
        thinking_enabled: thinkingEnabled
    };
    if (aiSettingsSourceDraft && aiSettingsSourceDraft.mode === 'preset' && aiSettingsSourceDraft.preset_id) {
        payload.source_mode = 'preset';
        payload.source_preset_id = aiSettingsSourceDraft.preset_id;
    } else {
        payload.source_mode = 'manual';
        payload.source_preset_id = '';
    }
    if (apiKey) payload.api_key = apiKey;
    if (permissions.ai_view_provider !== false) {
        payload.provider = provider;
        payload.thinking_provider = thinkingProvider;
    }
    if (permissions.ai_view_base_url !== false) {
        if (baseUrl.trim()) payload.base_url = baseUrl;
        if (thinkingBaseUrl.trim()) payload.thinking_base_url = thinkingBaseUrl;
    }
    if (permissions.ai_view_model !== false) {
        if (model.trim()) payload.model = model;
        if (thinkingModel.trim()) payload.thinking_model = thinkingModel;
    }
    if (permissions.ai_view_prompts !== false) {
        payload.system_prompt = systemPrompt;
        payload.thinking_system_prompt = thinkingPrompt;
        const rPrompt = document.getElementById('aiRomanizationPrompt');
        const rAlign = document.getElementById('aiRomanizationAlignmentMode');
        const rSep = document.getElementById('aiRomanizationSeparator');
        const rStrict = document.getElementById('aiRomanizationStrict');
        const rTrail = document.getElementById('aiRomanizationTrailing');
        if (rPrompt) payload.romanization_system_prompt = rPrompt.value;
        if (rAlign) {
            const v = String(rAlign.value || '').trim().toLowerCase();
            payload.romanization_alignment_mode = v === 'separator_tokens' ? 'separator_tokens' : 'indexed_tokens';
        }
        if (rSep) payload.romanization_separator = (rSep.value || ';').slice(0, 8);
        if (rStrict) payload.romanization_strict_token_count = rStrict.checked;
        if (rTrail) payload.romanization_require_trailing_separator = rTrail.checked;
    }
    if (thinkingApiKey) payload.thinking_api_key = thinkingApiKey;
    return { payload, permissions };
}

async function saveAISettings(options = {}) {
    const { silent = false, skipClose = false, intent = '', payloadOverride = null, statusKind = '' } = options || {};
    const payloadBundle = buildAISavePayload();
    const payload = payloadOverride && typeof payloadOverride === 'object'
        ? { ...payloadBundle.payload, ...payloadOverride }
        : { ...payloadBundle.payload };
    if (intent) {
        payload.intent = intent;
    }

    try {
        const response = await fetch('/save_ai_settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await response.json();
        if (data.status !== 'success') {
            throw new Error(data.message || t('alert.settingsSaveFailed'));
        }

        await refreshAiPresetCache();
        applyAiPresetFieldPermissions(aiPresetPermissions);
        applyAiSettingsButtonPermissions(Boolean(data.can_save_settings ?? data.can_use_ai), Boolean(data.can_edit_preset ?? aiPresetPermissions?.ai_edit_preset));

        const fieldVisibility = resolveAiFieldVisibilityFromResponse(data);
        const responseSettings = buildEffectiveAISettingsFromResponse(
            data.effective_settings || data.settings || {},
            { fieldVisibility }
        );
        setAiRuntimeSummary(data.runtime_summary);
        setAiFieldVisibility(fieldVisibility);
        writeAIStateToLocalStorage(responseSettings, aiPresetPermissions || payloadBundle.permissions);
        fillAIFormState(responseSettings, { fieldVisibility });

        document.getElementById('aiApiKey').value = '';
        document.getElementById('aiThinkingApiKey').value = '';

        const savedSource = {
            mode: String(data.source_mode || payload.source_mode || 'manual'),
            preset_id: String(data.source_preset_id || payload.source_preset_id || ''),
            preset_name: String((data.source_preset || {}).name || aiSettingsSourceDraft?.preset_name || ''),
            kind: String(data.source_kind || ''),
            label: String(data.source_label || '')
        };
        setAiSettingsSourceSaved(savedSource);
        setAiSettingsSourceDraft(savedSource);

        if (statusKind) {
            setAiSettingsStatus(statusKind, String((data.source_preset || {}).name || savedSource.preset_name || '').trim());
        } else {
            setAiSettingsStatus('idle');
        }
        if (intent === 'bind_preset' || statusKind === 'applied-preset' || savedSource.mode !== 'preset') {
            syncActiveAiPresetKeyWithSavedSource(savedSource);
        }

        aiSettingsInitialSnapshot = snapshotAiSettingsPreviewState();
        updateAiPresetSelect();
        updateQuickAiPresetSelect();
        safeUpdateBatchWorkbenchPresetSelect();
        updateAiPresetApplyStatus();
        if (!silent) {
            const presetName = String((data.source_preset || {}).name || savedSource.preset_name || '').trim() || t('preset.defaultName');
            if (statusKind === 'applied-manual') {
                alert(t('alert.aiSettingsAppliedManual'));
            } else if (statusKind === 'applied-preset') {
                if (data.preset_secret_updated) {
                    alert(t('alert.aiPresetBoundWithSecretSaved', { name: presetName }));
                } else {
                    alert(t('alert.aiPresetBound', { name: presetName }));
                }
            } else {
                alert(t('alert.settingsSaved'));
            }
        }
        if (!skipClose && !silent) {
            closeAISettings({ force: true });
        }
        return data;
    } catch (error) {
        console.error('保存AI设置失败：', error);
        alert(error.message || t('alert.settingsSaveFailed'));
        throw error;
    }
}

async function applyManualAISettings() {
    await saveAISettings({
        intent: 'apply_manual_settings',
        statusKind: 'applied-manual'
    });
}

async function bindSelectedAiPreset() {
    const context = getSelectedAiPresetContext('alert.selectPreset');
    if (!context) return;
    const formState = collectAIFormState();
    const typedTranslationApiKey = String(formState.translation?.api_key || '').trim();
    const typedThinkingApiKey = String(formState.thinking?.api_key || '').trim();
    const hasTypedSecret = Boolean(typedTranslationApiKey || typedThinkingApiKey);
    const canEditPreset = Boolean(aiPresetPermissions?.ai_edit_preset);
    if (hasTypedSecret && !canEditPreset) {
        alert(t('alert.aiPresetBindSaveKeyNoPermission'));
        return;
    }
    const presetHasTranslationKey = Boolean(context.preset?.translation_api_key_present);
    if (!presetHasTranslationKey && !typedTranslationApiKey) {
        alert(t('alert.aiPresetMissingTranslationKey', { name: context.preset?.name || context.presetId }));
        return;
    }
    await saveAISettings({
        intent: 'bind_preset',
        payloadOverride: {
            source_mode: 'preset',
            source_preset_id: context.presetId,
            api_key: typedTranslationApiKey || undefined,
            thinking_api_key: typedThinkingApiKey || undefined
        },
        statusKind: 'applied-preset'
    });
}

// 测试AI连接
async function probeAIConnection(mode = 'translation', btn = null) {
    const isThinking = mode === 'thinking';
    const targetLabel = isThinking ? '思考模型' : '翻译模型';
    const formState = collectAIFormState();

    const probeBtn = btn || document.querySelector(`button[data-probe="${isThinking ? 'thinking' : 'translation'}"]`);
    const originalText = probeBtn ? probeBtn.textContent : '';
    if (probeBtn) {
        probeBtn.textContent = t('status.testing');
        probeBtn.disabled = true;
    }

    try {
        const payload = { mode };
        if (isLocalAiAdminProbeAllowed()) {
            Object.assign(payload, {
                intent: 'probe_form',
                compat_mode: formState.translation.compat_mode,
                thinking_enabled: formState.thinking.enabled,
                translation: {
                    provider: formState.translation.provider,
                    base_url: sanitizeBaseUrl(formState.translation.base_url),
                    model: formState.translation.model,
                    system_prompt: formState.translation.system_prompt,
                    expect_reasoning: formState.translation.expect_reasoning,
                    strip_brackets: formState.translation.strip_brackets,
                    experimental_full_line_bracket_strip: formState.translation.experimental_full_line_bracket_strip,
                    experimental_bracket_line_as_subline: formState.translation.experimental_bracket_line_as_subline,
                    compat_mode: formState.translation.compat_mode,
                    api_key: formState.translation.api_key
                },
                thinking: {
                    enabled: formState.thinking.enabled,
                    provider: formState.thinking.provider,
                    base_url: sanitizeBaseUrl(formState.thinking.base_url),
                    model: formState.thinking.model,
                    system_prompt: formState.thinking.system_prompt,
                    api_key: formState.thinking.api_key
                }
            });
        }

        const response = await fetch('/probe_ai', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        const data = await response.json();
        if (data.status === 'success') {
            alert(t('runtime.connectionSuccess') + '\n' + t('batch.modelInfo') + (data.model || t('runtime.modelNotReturned')) + '\n' + t('batch.baseUrlInfo') + (data.base_url || t('runtime.modelNotReturned')));
        } else {
            alert(t('runtime.connectionFailed') + data.message);
        }
    } catch (error) {
        console.error(`${targetLabel}测试连接失败：`, error);
        alert(targetLabel + ' ' + t('runtime.connectionTestError'));
    } finally {
        if (probeBtn) {
            probeBtn.textContent = originalText;
            probeBtn.disabled = false;
        }
    }
}

const AMLL_CHAR_SPLIT_ENABLED_KEY = 'amll_char_split_enabled_v1';
const AMLL_CHAR_SPLIT_THRESHOLD_KEY = 'amll_char_split_threshold_ms_v1';
const DEFAULT_AMLL_CHAR_SPLIT_THRESHOLD_MS = 2000;

function readAmllCharSplitEnabled() {
    try {
        const raw = window.localStorage.getItem(AMLL_CHAR_SPLIT_ENABLED_KEY);
        if (raw === '0' || raw === 'false') return false;
        if (raw === '1' || raw === 'true') return true;
    } catch (error) {
        // ignore storage errors
    }
    return true;
}

function readAmllCharSplitThresholdMs() {
    try {
        const raw = window.localStorage.getItem(AMLL_CHAR_SPLIT_THRESHOLD_KEY);
        const parsed = parseInt(raw, 10);
        if (Number.isFinite(parsed) && parsed >= 0) return parsed;
    } catch (error) {
        // ignore storage errors
    }
    return DEFAULT_AMLL_CHAR_SPLIT_THRESHOLD_MS;
}

function appendCharSplitQueryParams(params, enabled = readAmllCharSplitEnabled(), thresholdMs = readAmllCharSplitThresholdMs()) {
    params.set('char_split', enabled ? 'on' : 'off');
    params.set('char_split_threshold_ms', String(thresholdMs));
}

function buildLyricsAmllUrl(enabled = readAmllCharSplitEnabled(), thresholdMs = readAmllCharSplitThresholdMs()) {
    const params = new URLSearchParams();
    appendCharSplitQueryParams(params, enabled, thresholdMs);
    return `/lyrics-amll?${params.toString()}`;
}

async function openLyricsAnimate(filename, style) {
    const applyCokCharSplitParams = (params) => {
        if (String(style).trim() !== 'C_ok') return;
        appendCharSplitQueryParams(params);
    };

    const fallbackOpen = (extra = {}) => {
        const params = new URLSearchParams({ file: filename, style });
        Object.entries(extra).forEach(([key, value]) => {
            if (value) {
                params.set(key, value);
            }
        });
        applyCokCharSplitParams(params);
        params.set('for_player', '1');
        window.open('/lyrics-animate?' + params.toString(), '_blank');
    };

    let openWithParams = fallbackOpen;

    try {
        // 1) 读取歌曲 JSON，拿到歌词路径
        const res = await fetch('/get_json_data?filename=' + encodeURIComponent(filename), { cache: 'no-store' });
        const data = await res.json();
        if (data.status !== 'success') throw new Error('获取歌曲信息失败');

        const meta = data.jsonData?.meta || {};

        const resolveMediaForAnimate = (value) => {
            if (!value || value === '!') {
                return '';
            }
            try {
                const normalized = normalizeSongsUrl(value);
                if (normalized && normalized !== '!') {
                    return normalized;
                }
            } catch (error) {
                console.warn('normalizeSongsUrl 解析失败，使用原值:', value, error);
            }
            return value;
        };

        // AMLL 输入链路区分“参考图”和“真正背景”：
        // - cover: 动态封面静态化结果 / 专辑图 / poster / 背景图
        // - background: 只在存在真正 Background-image 时才传
        const dynamicCoverSrc = resolveMediaForAnimate(meta.dynamicCoverSrc);
        const dynamicCoverPoster = resolveMediaForAnimate(meta.dynamicCoverPoster);
        const albumImgSrc = resolveMediaForAnimate(meta.albumImgSrc) || 
                            resolveMediaForAnimate(meta.cover) || 
                            resolveMediaForAnimate(meta.coverUrl) ||
                            resolveMediaForAnimate(data.jsonData?.cover) ||
                            resolveMediaForAnimate(data.jsonData?.coverUrl);
        const backgroundImage = resolveMediaForAnimate(meta['Background-image']);
        const AMLL_FRAME_TIMEOUT_MS = 1800;
        const isMvodPreviewSource = (sourceUrl) => {
            const value = String(sourceUrl || '').trim().toLowerCase();
            return value.includes('mvod.itunes.apple.com');
        };
        const getPreferredFrameSeekSeconds = (sourceUrl) => (
            isMvodPreviewSource(sourceUrl) ? 10 : 0
        );
        const getAmllMediaType = (sourceUrl) => {
            if (!sourceUrl) {
                return 'none';
            }
            if (isMvodPreviewSource(sourceUrl) || isVideoFile(sourceUrl)) {
                return 'video';
            }
            if (isAnimatedImage(sourceUrl)) {
                return 'animated';
            }
            return 'image';
        };
        const pickImmediateStaticReference = (candidates = []) => {
            for (const candidate of candidates) {
                if (!candidate) {
                    continue;
                }
                if (getAmllMediaType(candidate) !== 'image') {
                    continue;
                }
                return candidate;
            }
            return '';
        };

        const extractedFrameCache = new Map();
        const resolveAmllStaticAsset = async (sourceUrl) => {
            if (!sourceUrl) {
                return '';
            }
            if (!extractedFrameCache.has(sourceUrl)) {
                extractedFrameCache.set(sourceUrl, (async () => {
                    try {
                        const frameRes = await fetch('/api/extract_frame', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                source_url: sourceUrl,
                                seek_seconds: getPreferredFrameSeekSeconds(sourceUrl)
                            })
                        });
                        const frameData = await frameRes.json();
                        if (frameData.status === 'success' && frameData.frame_url) {
                            return frameData.frame_url;
                        }
                    } catch (error) {
                        console.warn('Failed to extract frame for AMLL background:', error);
                    }
                    extractedFrameCache.delete(sourceUrl);
                    return '';
                })());
            }
            return await extractedFrameCache.get(sourceUrl);
        };
        const resolveAmllStaticAssetWithTimeout = async (sourceUrl, timeoutMs = AMLL_FRAME_TIMEOUT_MS) => {
            if (!sourceUrl) {
                return '';
            }
            let timeoutId = null;
            const timeoutPromise = new Promise((resolve) => {
                timeoutId = window.setTimeout(() => resolve(''), timeoutMs);
            });
            try {
                return await Promise.race([
                    resolveAmllStaticAsset(sourceUrl),
                    timeoutPromise
                ]);
            } finally {
                if (timeoutId !== null) {
                    window.clearTimeout(timeoutId);
                }
            }
        };

        let coverUrl = '';
        const dynamicMediaType = getAmllMediaType(dynamicCoverSrc);
        const hasExtractableDynamicSource =
            Boolean(dynamicCoverSrc) &&
            (dynamicMediaType === 'video' || dynamicMediaType === 'animated');

        if (isMvodPreviewSource(dynamicCoverSrc)) {
            coverUrl = pickImmediateStaticReference([
                dynamicCoverPoster,
                albumImgSrc,
                backgroundImage
            ]);
            if (!coverUrl && hasExtractableDynamicSource) {
                coverUrl = await resolveAmllStaticAssetWithTimeout(dynamicCoverSrc);
            }
        } else {
            coverUrl = pickImmediateStaticReference([
                dynamicCoverSrc,
                dynamicCoverPoster,
                albumImgSrc,
                backgroundImage
            ]);
            if (!coverUrl && hasExtractableDynamicSource) {
                coverUrl = await resolveAmllStaticAssetWithTimeout(dynamicCoverSrc);
            }
        }

        let backgroundUrl = '';
        if (backgroundImage) {
            backgroundUrl = backgroundImage;
        }

        openWithParams = (extra = {}) => {
            const params = new URLSearchParams({ file: filename, style });
            if (backgroundUrl) {
                params.set('background', backgroundUrl);
            }
            if (coverUrl) {
                params.set('coverUrl', coverUrl);
                params.set('cover', coverUrl);
            }
            Object.entries(extra).forEach(([key, value]) => {
                if (value) {
                    params.set(key, value);
                }
            });
            applyCokCharSplitParams(params);
            params.set('for_player', '1');
            window.open('/lyrics-animate?' + params.toString(), '_blank');
        };

        // 兼容两种写法：单路径 或 "::歌词::翻译::罗马音::"
        let rawLyricsPath = meta.lyrics || '';
        let mainPath = '';
        if (rawLyricsPath.includes('::')) {
            const parts = rawLyricsPath.split('::');
            mainPath = parts[1] || '';
        } else {
            mainPath = rawLyricsPath;
        }

        // 兜底：没有主歌词路径，直接打开动画页（保持原行为）
        if (!mainPath) {
            openWithParams();
            return;
        }

        // 2) 如果是 TTML，调用后端转换接口（不修改原 JSON，仅生成转换产物）
        const isTTML = mainPath.toLowerCase().endsWith('.ttml');
        if (isTTML) {
            // 取 /songs/ 下的相对路径传给后端：支持 http(s)://当前主机/songs/xxx 和 /songs/xxx 两种
            let relative = '';
            try {
                if (mainPath.startsWith('http://') || mainPath.startsWith('https://')) {
                    const u = new URL(mainPath);
                    relative = u.pathname.startsWith('/songs/') ? u.pathname.slice('/songs/'.length) : u.pathname.replace(/^\//, '');
                } else {
                    relative = mainPath.replace(/^\/?songs\//i, '');
                }
                relative = decodeURIComponent(relative);
            } catch (e) {
                console.error('解析URL失败:', e);
                relative = mainPath.split('/').pop();
                relative = decodeURIComponent(relative);
            }

            console.log('TTML转换 - 文件名:', filename, '相对路径:', relative);

            const conv = await fetch('/convert_ttml_by_path', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ path: relative })
            }).then(r => r.json());

            if (conv.status === 'success' && conv.lyricPath) {
                const extraParams = { lys: conv.lyricPath };
                if (conv.transPath) {
                    extraParams.lrc = conv.transPath;
                }
                openWithParams(extraParams);
                return;
            } else {
                console.error('TTML转换失败:', conv.message || '未知错误');
                alert(t('alert.ttmlConvertFailedWithMsg') + (conv.message || ''));
            }
        } else {
            if (!mainPath.toLowerCase().endsWith('.lys')) {
                alert(t('alert.lyricsNotLysDynamic'));
            }
        }

        // 4) 兜底：按老逻辑打开
        openWithParams();

    } catch (error) {
        console.error('打开动画页失败：', error);
        openWithParams();
    }
}

function writeAmllCharSplitEnabled(enabled) {
    try {
        window.localStorage.setItem(AMLL_CHAR_SPLIT_ENABLED_KEY, enabled ? '1' : '0');
    } catch (error) {
        // ignore storage errors
    }
}

function openAMLL() {
    window.open(buildLyricsAmllUrl(), '_blank');
}

function hideAmllCharSplitContextMenu() {
    const menu = document.getElementById('amllCharSplitContextMenu');
    const backdrop = document.getElementById('amllCharSplitContextBackdrop');
    if (menu) menu.hidden = true;
    if (backdrop) backdrop.hidden = true;
}

function showAmllCharSplitContextMenu(clientX, clientY) {
    const menu = document.getElementById('amllCharSplitContextMenu');
    const backdrop = document.getElementById('amllCharSplitContextBackdrop');
    const toggle = document.getElementById('amllCharSplitToggle');
    if (!menu || !backdrop || !toggle) return;
    toggle.checked = readAmllCharSplitEnabled();
    menu.style.left = `${clientX}px`;
    menu.style.top = `${clientY}px`;
    menu.hidden = false;
    backdrop.hidden = false;
}

function shouldPreserveNativeContextMenu(target) {
    if (!target || !(target instanceof Element)) return false;
    return Boolean(target.closest(
        'input, textarea, select, option, [contenteditable=""], [contenteditable="true"], [contenteditable="plaintext-only"], a[href]'
    ));
}

function initAmllCharSplitContextMenu() {
    const menu = document.getElementById('amllCharSplitContextMenu');
    const backdrop = document.getElementById('amllCharSplitContextBackdrop');
    const toggle = document.getElementById('amllCharSplitToggle');
    const menuRoot = document.body;
    if (!menu || !backdrop || !toggle || !menuRoot) return;

    menuRoot.addEventListener('contextmenu', (event) => {
        if (shouldPreserveNativeContextMenu(event.target)) return;
        if (menu.contains(event.target) || backdrop.contains(event.target)) return;
        event.preventDefault();
        showAmllCharSplitContextMenu(event.clientX, event.clientY);
    });

    backdrop.addEventListener('click', hideAmllCharSplitContextMenu);
    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') hideAmllCharSplitContextMenu();
    });

    toggle.addEventListener('change', () => {
        writeAmllCharSplitEnabled(Boolean(toggle.checked));
    });

    menu.addEventListener('click', (event) => {
        event.stopPropagation();
    });
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initAmllCharSplitContextMenu);
} else {
    initAmllCharSplitContextMenu();
}
