(function () {
    const STORAGE_KEY = 'lyricsphere_romanization_payload_v1';
    const STAGE_ORDER = ['parse_source', 'send_request', 'receive_stream', 'validating', 'assembling', 'done'];

    let _romanPayloadCache = null;
    let _abortController = null;
    let _streamRunning = false;
    let _manualAssembleBusy = false;
    let _weaveBusy = false;
    let _metaCache = null;
    let _expectedLines = 0;
    let _lastValidationMode = 'idle';
    let _lastTrustMode = 'none';
    let _lastBadgeMode = 'idle';

    let _repairReady = false;
    let _conversationHistory = [];
    let _repairBaseRaw = '';
    let _lastValidationErrors = [];
    let _lastRepairInstructionSent = '';
    let _lastRequestWasRepair = false;

    const el = (id) => document.getElementById(id);

    function readPayload() {
        try {
            const raw = sessionStorage.getItem(STORAGE_KEY);
            if (!raw) return null;
            return JSON.parse(raw);
        } catch (e) {
            return null;
        }
    }

    /**
     * Body for /romanize_lyrics, /romanize_lyrics_prompt, /romanize_lyrics_assemble (without manual_model_output).
     * @param {boolean} [forceFresh] When true, omit repair fields (default true).
     */
    function buildRomanizeRequestBody(forceFresh) {
        if (forceFresh === undefined) forceFresh = true;
        const payload = readPayload();
        if (!payload) return null;
        const targetFmt = el('romanWbTargetFormat') ? el('romanWbTargetFormat').value : 'lys';
        const sourceFormat = String(payload.detectedFormat || 'lys').toLowerCase();
        const requestBody = {
            source_content: payload.sourceContent,
            source_format: sourceFormat,
            target_format: targetFmt
        };
        if (payload.romanization && typeof payload.romanization === 'object') {
            const rom = payload.romanization;
            const am = String(rom.alignment_mode || '').trim().toLowerCase();
            const alignmentMode = am === 'separator_tokens' ? 'separator_tokens' : 'indexed_tokens';
            requestBody.romanization = {
                alignment_mode: alignmentMode,
                separator: rom.separator !== undefined ? String(rom.separator).slice(0, 8) : ';',
                strict_token_count: rom.strict_token_count !== undefined ? Boolean(rom.strict_token_count) : true,
                require_trailing_separator: rom.require_trailing_separator !== undefined
                    ? Boolean(rom.require_trailing_separator)
                    : true
            };
            const sp = rom.system_prompt !== undefined ? String(rom.system_prompt).trim() : '';
            if (sp) requestBody.romanization.system_prompt = String(rom.system_prompt);
        }
        const useRepair = !forceFresh && _repairReady && String(_repairBaseRaw || '').trim();
        if (useRepair) {
            requestBody.conversation_history = JSON.parse(JSON.stringify(_conversationHistory));
            requestBody.repair_instruction = _lastRepairInstructionSent || buildRepairInstruction();
            requestBody.previous_full_model_output = _repairBaseRaw;
        }
        return requestBody;
    }

    function resetRomanRepairState() {
        _repairReady = false;
        _conversationHistory = [];
        _repairBaseRaw = '';
        _lastValidationErrors = [];
        _lastRepairInstructionSent = '';
        _lastRequestWasRepair = false;
        updateErrorCardRepairHint();
    }

    function buildRepairInstruction() {
        const numPre = el('romanWbNumberedPre');
        const numbered = (_metaCache && _metaCache.numbered_input_preview != null)
            ? String(_metaCache.numbered_input_preview)
            : (numPre ? String(numPre.textContent || '').trim() : '');
        const errs = Array.isArray(_lastValidationErrors) && _lastValidationErrors.length
            ? _lastValidationErrors.join('\n')
            : '';
        return [
            '请只修复未通过校验的编号行；若只返回部分行，行首编号必须与输入一致；不要输出说明文字。',
            '校验错误：',
            errs || '（无）',
            '带编号的原始输入（与首轮相同）：',
            numbered || '（空）'
        ].join('\n\n');
    }

    function updateErrorCardRepairHint() {
        const hint = el('romanWbRepairHint');
        if (!hint) return;
        if (_repairReady && !_streamRunning) {
            hint.textContent = t('romanWb.repairReadyHint');
            hint.style.display = 'block';
        } else {
            hint.textContent = '';
            hint.style.display = 'none';
        }
    }

    function applyRomanRepairAfterStreamError(payload, ctx) {
        const msg = payload.message ? String(payload.message) : '';
        const errors = Array.isArray(payload.errors) ? payload.errors.slice() : [];
        const isMergeFail = msg.indexOf('修复合并失败') !== -1 || msg.indexOf('合并失败') !== -1;
        const baseBroken = errors.some((e) => String(e).indexOf('基底不完整') !== -1);

        if (!_lastRequestWasRepair) {
            if (errors.length && payload.raw_model_output != null && payload.raw_model_output !== '') {
                _repairBaseRaw = String(payload.raw_model_output);
                _lastValidationErrors = errors;
                const ub = (_metaCache && _metaCache.user_block != null)
                    ? String(_metaCache.user_block).trim()
                    : '';
                const numPre = el('romanWbNumberedPre');
                const numberedFb = (_metaCache && _metaCache.numbered_input_preview != null)
                    ? String(_metaCache.numbered_input_preview).trim()
                    : (numPre ? String(numPre.textContent || '').trim() : '');
                const firstUser = ub || (numberedFb ? `（编号输入）\n${numberedFb}` : '');
                _conversationHistory = [
                    { role: 'user', content: firstUser },
                    { role: 'assistant', content: _repairBaseRaw }
                ];
                _repairReady = true;
            }
        } else {
            _conversationHistory.push({ role: 'user', content: _lastRepairInstructionSent });
            _conversationHistory.push({ role: 'assistant', content: ctx.rawText || '' });
            if (baseBroken) {
                resetRomanRepairState();
                return;
            }
            _lastValidationErrors = errors;
            if (isMergeFail) {
                _repairReady = String(_repairBaseRaw || '').trim().length > 0;
            } else {
                const merged = payload.merged_model_output != null ? String(payload.merged_model_output).trim() : '';
                if (merged) {
                    _repairBaseRaw = merged;
                } else if (payload.raw_model_output != null && payload.raw_model_output !== '') {
                    _repairBaseRaw = String(payload.raw_model_output);
                }
                _repairReady = String(_repairBaseRaw || '').trim().length > 0;
            }
        }
        updateErrorCardRepairHint();
    }

    function renderSourceInfo() {
        const info = el('romanWbSourceInfo');
        if (!info) return;
        if (!_romanPayloadCache) {
            info.textContent = t('romanWb.metaNoSession');
            return;
        }
        const fn = _romanPayloadCache.fileName ? String(_romanPayloadCache.fileName) : '';
        const fmt = _romanPayloadCache.detectedFormat ? String(_romanPayloadCache.detectedFormat) : '';
        info.textContent = t('romanWb.metaLine', {
            fileName: fn.trim() ? fn : t('romanWb.unnamedFile'),
            format: fmt.trim() ? fmt : t('romanWb.unknownFormat'),
            path: String(_romanPayloadCache.lyricsPath || '')
        });
        const sum = el('romanWbSessionSummary');
        if (sum) {
            sum.textContent = info.textContent;
        }
    }

    function updateLysHint(sourceFormat, targetSelect) {
        const hint = el('romanWbLysHint');
        if (!targetSelect) {
            updateWeaveBackgroundButtonState();
            return;
        }
        if (!hint) {
            if (targetSelect.value !== 'lys') clearBackgroundWeaveOutputs();
            updateWeaveBackgroundButtonState();
            return;
        }
        if (sourceFormat === 'lrc') {
            targetSelect.value = 'lrc';
            const lysOpt = targetSelect.querySelector('option[value="lys"]');
            if (lysOpt) lysOpt.disabled = true;
            hint.textContent = t('romanWb.hintLrcOnly');
        } else {
            const lysOpt = targetSelect.querySelector('option[value="lys"]');
            if (lysOpt) lysOpt.disabled = false;
            hint.textContent = '';
        }
        updateWeaveBackgroundButtonState();
        if (targetSelect.value !== 'lys') clearBackgroundWeaveOutputs();
    }

    function resetStageVisuals() {
        STAGE_ORDER.forEach((key) => {
            const item = document.querySelector(`#romanWbStageList [data-stage="${key}"]`);
            if (!item) return;
            item.classList.remove(
                'status-progress__item--pending',
                'status-progress__item--active',
                'status-progress__item--success',
                'status-progress__item--error'
            );
            item.classList.add('status-progress__item--pending');
        });
        setProgress(0);
    }

    function setStageVisual(stageKey, state) {
        const item = document.querySelector(`#romanWbStageList [data-stage="${stageKey}"]`);
        if (!item) return;
        item.classList.remove(
            'status-progress__item--pending',
            'status-progress__item--active',
            'status-progress__item--success',
            'status-progress__item--error'
        );
        const map = {
            pending: 'status-progress__item--pending',
            active: 'status-progress__item--active',
            success: 'status-progress__item--success',
            error: 'status-progress__item--error'
        };
        item.classList.add(map[state] || map.pending);
    }

    function applyStageEvent(stageKey, state) {
        const idx = STAGE_ORDER.indexOf(stageKey);
        if (state === 'active' && idx >= 0) {
            for (let i = 0; i < idx; i++) {
                setStageVisual(STAGE_ORDER[i], 'success');
            }
            setStageVisual(stageKey, 'active');
        } else if (state === 'success') {
            setStageVisual(stageKey, 'success');
        } else if (state === 'error') {
            setStageVisual(stageKey, 'error');
        }
        updateProgressFromStages();
    }

    function updateProgressFromStages() {
        let completed = 0;
        let hasActive = false;
        STAGE_ORDER.forEach((key) => {
            const item = document.querySelector(`#romanWbStageList [data-stage="${key}"]`);
            if (!item) return;
            if (item.classList.contains('status-progress__item--success')) completed += 1;
            if (item.classList.contains('status-progress__item--active')) hasActive = true;
        });
        const pct = Math.min(100, ((completed + (hasActive ? 0.45 : 0)) / STAGE_ORDER.length) * 100);
        setProgress(pct);
    }

    function setProgress(pct) {
        const bar = el('romanWbProgressBar');
        const wrap = bar && bar.parentElement;
        if (bar) bar.style.width = `${Math.round(pct)}%`;
        if (wrap && wrap.setAttribute) wrap.setAttribute('aria-valuenow', String(Math.round(pct)));
    }

    function setBadge(mode) {
        _lastBadgeMode = mode;
        const badge = el('romanWbStatusBadge');
        if (!badge) return;
        badge.classList.remove('idle', 'running', 'completed', 'stopped', 'failed');
        if (mode === 'running') {
            badge.classList.add('running');
            badge.textContent = t('romanWb.badgeRunning');
        } else if (mode === 'done') {
            badge.classList.add('completed');
            badge.textContent = t('romanWb.badgeDone');
        } else if (mode === 'stopped') {
            badge.classList.add('stopped');
            badge.textContent = t('romanWb.badgeStopped');
        } else if (mode === 'failed') {
            badge.classList.add('failed');
            badge.textContent = t('romanWb.badgeFailed');
        } else {
            badge.classList.add('idle');
            badge.textContent = t('romanWb.badgeIdle');
        }
    }

    function setValidationUi(mode) {
        _lastValidationMode = mode;
        const line = el('romanWbValidationLine');
        const valEl = el('romanWbKpiValidationVal');
        if (!line) return;
        line.classList.remove('ok', 'fail', 'stopped');
        let msg = t('romanWb.validationIdle');
        let kpi = '—';
        if (mode === 'running') {
            msg = t('romanWb.validationRunning');
            kpi = t('romanWb.receiving');
        } else if (mode === 'ok') {
            msg = t('romanWb.validationOk');
            kpi = t('romanWb.validationOk');
            line.classList.add('ok');
        } else if (mode === 'fail') {
            msg = t('romanWb.validationFail');
            kpi = t('romanWb.validationFail');
            line.classList.add('fail');
        } else if (mode === 'stopped') {
            msg = t('romanWb.validationStopped');
            kpi = t('romanWb.validationStopped');
            line.classList.add('stopped');
        }
        line.textContent = msg;
        if (valEl) valEl.textContent = kpi;
    }

    function markStagesForClientFailure(errorStageKey) {
        const idx = STAGE_ORDER.indexOf(errorStageKey);
        if (idx < 0) return;
        for (let j = idx + 1; j < STAGE_ORDER.length; j++) {
            setStageVisual(STAGE_ORDER[j], 'pending');
        }
        setStageVisual(STAGE_ORDER[idx], 'error');
        updateProgressFromStages();
    }

    function markStagesAfterStop() {
        let activeIdx = -1;
        STAGE_ORDER.forEach((key, i) => {
            const item = document.querySelector(`#romanWbStageList [data-stage="${key}"]`);
            if (item && item.classList.contains('status-progress__item--active')) {
                activeIdx = i;
            }
        });
        if (activeIdx >= 0) {
            setStageVisual(STAGE_ORDER[activeIdx], 'error');
            for (let j = activeIdx + 1; j < STAGE_ORDER.length; j++) {
                setStageVisual(STAGE_ORDER[j], 'pending');
            }
            updateProgressFromStages();
            return;
        }
        let lastOk = -1;
        STAGE_ORDER.forEach((key, i) => {
            const item = document.querySelector(`#romanWbStageList [data-stage="${key}"]`);
            if (item && item.classList.contains('status-progress__item--success')) {
                lastOk = i;
            }
        });
        const errIdx = lastOk >= 0 ? lastOk + 1 : 0;
        if (errIdx < STAGE_ORDER.length) {
            setStageVisual(STAGE_ORDER[errIdx], 'error');
            for (let j = errIdx + 1; j < STAGE_ORDER.length; j++) {
                setStageVisual(STAGE_ORDER[j], 'pending');
            }
            updateProgressFromStages();
        }
    }

    /**
     * @param {'success'|'fail'|'stopped'} outcome
     * @param {{ fromSse?: boolean, failStage?: string, incompleteStream?: boolean }} [opts]
     */
    function finishRomanWorkbench(outcome, opts) {
        opts = opts || {};
        if (outcome === 'success') {
            resetRomanRepairState();
            setBadge('done');
            setValidationUi('ok');
            setTrustHint('success');
            setProgress(100);
            return;
        }
        if (outcome === 'stopped') {
            markStagesAfterStop();
            setBadge('stopped');
            setValidationUi('stopped');
            setTrustHint('none');
            updateProgressFromStages();
            return;
        }
        if (outcome === 'fail') {
            if (!opts.fromSse) {
                if (opts.incompleteStream) {
                    markStagesAfterStop();
                } else {
                    const stageKey = opts.failStage || 'send_request';
                    markStagesForClientFailure(stageKey);
                }
            }
            setBadge('failed');
            setValidationUi('fail');
            setTrustHint('fail');
            updateProgressFromStages();
        }
    }

    function setTrustHint(mode) {
        _lastTrustMode = mode;
        const hint = el('romanWbTrustHint');
        if (!hint) return;
        hint.classList.remove('success', 'error');
        if (mode === 'success') {
            hint.textContent = t('romanWb.trustHintSuccess');
            hint.classList.add('success');
        } else if (mode === 'fail') {
            hint.textContent = t('romanWb.trustHintFail');
            hint.classList.add('error');
        } else {
            hint.textContent = '';
        }
    }

    function updateKpiFromMeta(meta) {
        _metaCache = meta || _metaCache;
        const m = _metaCache;
        if (!m) return;
        _expectedLines = Number(m.lines) || 0;
        const lv = el('romanWbKpiLinesVal');
        const sv = el('romanWbKpiSourceFmtVal');
        const tv = el('romanWbKpiTargetFmtVal');
        if (lv) lv.textContent = String(m.lines != null ? m.lines : '—');
        if (sv) sv.textContent = String(m.source_effective || m.source_format || '—');
        if (tv) tv.textContent = String(m.target_format || '—');
    }

    function updateKpiStats(stats) {
        if (!stats) return;
        const c = el('romanWbKpiCharsVal');
        const ld = el('romanWbKpiLinesDetectedVal');
        if (c && stats.chars != null) c.textContent = String(stats.chars);
        if (ld && stats.lines_detected != null) ld.textContent = String(stats.lines_detected);
    }

    function resetOutputPanels() {
        const numbered = el('romanWbNumberedPre');
        const raw = el('romanWbRawPre');
        const reasoning = el('romanWbReasoningPre');
        const finalPre = el('romanWbFinalPre');
        const finalTa = el('romanWbFinalCopySource');
        if (numbered) numbered.textContent = '';
        if (raw) raw.textContent = '';
        if (reasoning) reasoning.textContent = '';
        if (finalPre) finalPre.textContent = '';
        if (finalTa) finalTa.value = '';
        const manualTa = el('romanWbManualInput');
        if (manualTa) manualTa.value = '';
        clearBackgroundWeaveOutputs();
        const errCard = el('romanWbErrorsCard');
        const errPre = el('romanWbErrors');
        if (errCard) errCard.style.display = 'none';
        if (errPre) errPre.textContent = '';
        setTrustHint('none');
        setValidationUi('idle');
        _metaCache = null;
        _expectedLines = 0;
        const lv = el('romanWbKpiLinesVal');
        const sv = el('romanWbKpiSourceFmtVal');
        const tv = el('romanWbKpiTargetFmtVal');
        const c = el('romanWbKpiCharsVal');
        const ld = el('romanWbKpiLinesDetectedVal');
        const vv = el('romanWbKpiValidationVal');
        if (lv) lv.textContent = '—';
        if (sv) sv.textContent = '—';
        if (tv) tv.textContent = '—';
        if (c) c.textContent = '0';
        if (ld) ld.textContent = '0';
        if (vv) vv.textContent = '—';
        updateWeaveBackgroundButtonState();
    }

    function clearBackgroundWeaveOutputs() {
        const bgPre = el('romanWbBackgroundPre');
        const bgTa = el('romanWbBackgroundCopySource');
        if (bgPre) bgPre.textContent = '';
        if (bgTa) bgTa.value = '';
        const copyBgBtn = el('romanWbCopyBackgroundBtn');
        if (copyBgBtn) copyBgBtn.disabled = true;
    }

    function getFinalResultText() {
        const ta = el('romanWbFinalCopySource');
        if (ta && String(ta.value || '').trim()) return String(ta.value);
        const pre = el('romanWbFinalPre');
        return pre ? String(pre.textContent || '') : '';
    }

    function updateWeaveBackgroundButtonState() {
        const btn = el('romanWbWeaveBackgroundBtn');
        if (!btn) return;
        const fmt = el('romanWbTargetFormat');
        const isLys = fmt && fmt.value === 'lys';
        const hasFinal = String(getFinalResultText() || '').trim().length > 0;
        const blocked = _streamRunning || _manualAssembleBusy || _weaveBusy;
        btn.disabled = !isLys || !hasFinal || blocked;
    }

    function showErrorCard(message, errors) {
        const errCard = el('romanWbErrorsCard');
        const errPre = el('romanWbErrors');
        if (!errCard || !errPre) return;
        let text = message || '';
        if (Array.isArray(errors) && errors.length) {
            text += (text ? '\n' : '') + errors.join('\n');
        }
        errPre.textContent = text;
        errCard.style.display = 'block';
        updateErrorCardRepairHint();
    }

    /** Clear prior LYS/LRC result and stream raw so a failed manual run cannot look successful. */
    function clearStaleResultOutputsForManualAssemble() {
        const finalPre = el('romanWbFinalPre');
        const finalTa = el('romanWbFinalCopySource');
        const rawPre = el('romanWbRawPre');
        const copyBtn = el('romanWbCopyResultBtn');
        if (finalPre) finalPre.textContent = '';
        if (finalTa) finalTa.value = '';
        if (rawPre) rawPre.textContent = '';
        if (copyBtn) copyBtn.disabled = true;
        clearBackgroundWeaveOutputs();
        setTrustHint('none');
        updateWeaveBackgroundButtonState();
    }

    /** Single place for toolbar disabled state (stream + manual assemble). */
    function applyRomanWorkbenchToolbarLock() {
        const stream = _streamRunning;
        const manual = _manualAssembleBusy;
        const busy = stream || manual;
        const runBtn = el('romanWbRunBtn');
        const stopBtn = el('romanWbStopBtn');
        const regenBtn = el('romanWbRegenBtn');
        const copyBtn = el('romanWbCopyResultBtn');
        const manualAssembleBtn = el('romanWbManualAssembleBtn');
        const copyPromptBtn = el('romanWbCopyPromptBtn');
        const clearBtn = el('romanWbClearBtn');
        const fmt = el('romanWbTargetFormat');
        if (runBtn) {
            runBtn.disabled = busy;
            if (stream) {
                runBtn.textContent = t('romanWb.runPending');
            } else {
                runBtn.textContent = _repairReady ? t('romanWb.repair') : t('romanWb.run');
            }
        }
        if (stopBtn) stopBtn.disabled = !stream;
        if (regenBtn) regenBtn.disabled = busy;
        if (fmt) fmt.disabled = busy;
        if (copyPromptBtn) copyPromptBtn.disabled = manual;
        if (clearBtn) clearBtn.disabled = busy;
        if (manualAssembleBtn) manualAssembleBtn.disabled = busy;
        if (copyBtn && stream) copyBtn.disabled = true;
        if (copyBtn && manual && !stream) copyBtn.disabled = true;
        updateWeaveBackgroundButtonState();
    }

    function setRunningUi(running) {
        _streamRunning = running;
        applyRomanWorkbenchToolbarLock();
    }

    function processSseLine(line, ctx) {
        if (!line) return;
        if (line.startsWith('meta:')) {
            try {
                const payload = JSON.parse(line.slice(5));
                ctx.meta = payload;
                updateKpiFromMeta(payload);
                const pre = el('romanWbNumberedPre');
                if (pre && payload.numbered_input_preview != null) {
                    pre.textContent = payload.numbered_input_preview;
                }
            } catch (e) {
                console.error('[romanWb] meta parse', e);
            }
            return;
        }
        if (line.startsWith('stage:')) {
            try {
                const payload = JSON.parse(line.slice(6));
                if (payload.stage && payload.state) {
                    applyStageEvent(payload.stage, payload.state);
                }
            } catch (e) {
                console.error('[romanWb] stage parse', e);
            }
            return;
        }
        if (line.startsWith('reasoning:')) {
            try {
                const payload = JSON.parse(line.slice(10));
                if (payload.reasoning != null) {
                    const reasoningPre = el('romanWbReasoningPre');
                    if (reasoningPre) reasoningPre.textContent = payload.reasoning;
                }
            } catch (e) {
                console.error('[romanWb] reasoning parse', e);
            }
            return;
        }
        if (line.startsWith('raw:')) {
            try {
                const payload = JSON.parse(line.slice(4));
                if (payload.chunk) {
                    ctx.rawText += payload.chunk;
                    const rawPre = el('romanWbRawPre');
                    if (rawPre) rawPre.textContent = ctx.rawText;
                }
            } catch (e) {
                console.error('[romanWb] raw parse', e);
            }
            return;
        }
        if (line.startsWith('stats:')) {
            try {
                const payload = JSON.parse(line.slice(6));
                updateKpiStats(payload);
            } catch (e) {
                console.error('[romanWb] stats parse', e);
            }
            return;
        }
        if (line.startsWith('result:')) {
            try {
                const payload = JSON.parse(line.slice(7));
                const text = payload.result_text || '';
                const finalPre = el('romanWbFinalPre');
                const finalTa = el('romanWbFinalCopySource');
                if (finalPre) finalPre.textContent = text;
                if (finalTa) finalTa.value = text;
                const copyBtn = el('romanWbCopyResultBtn');
                if (copyBtn) copyBtn.disabled = !text;
                updateWeaveBackgroundButtonState();
                ctx.success = true;
                const rawMerged = payload.merged_raw_model_output != null
                    ? String(payload.merged_raw_model_output)
                    : (payload.raw_model_output != null ? String(payload.raw_model_output) : '');
                if (rawMerged.trim()) {
                    const rawPre = el('romanWbRawPre');
                    if (rawPre) rawPre.textContent = rawMerged;
                }
                finishRomanWorkbench('success');
            } catch (e) {
                console.error('[romanWb] result parse', e);
            }
            return;
        }
        if (line.startsWith('error:')) {
            try {
                const payload = JSON.parse(line.slice(6));
                ctx.failed = true;
                const msg = payload.message || t('romanWb.errFailed');
                showErrorCard(msg, payload.errors);
                const showRaw = payload.merged_model_output != null && String(payload.merged_model_output).trim()
                    ? String(payload.merged_model_output)
                    : (payload.raw_model_output != null && payload.raw_model_output !== '' ? String(payload.raw_model_output) : '');
                if (showRaw) {
                    const rawPre = el('romanWbRawPre');
                    if (rawPre) rawPre.textContent = showRaw;
                }
                applyRomanRepairAfterStreamError(payload, ctx);
                finishRomanWorkbench('fail', { fromSse: true });
                applyRomanWorkbenchToolbarLock();
            } catch (e) {
                console.error('[romanWb] error parse', e);
            }
        }
    }

    function formatRomanMessagesForClipboard(messages) {
        if (!Array.isArray(messages) || !messages.length) return '';
        const titleMap = { system: 'System', user: 'User', assistant: 'Assistant' };
        return messages.map((m) => {
            const role = String(m.role || '');
            const label = titleMap[role] || role || 'unknown';
            return `[${label}]\n${String(m.content != null ? m.content : '')}`;
        }).join('\n\n');
    }

    async function copyRomanizationPrompt() {
        if (_manualAssembleBusy || _streamRunning) return;
        const btn = el('romanWbCopyPromptBtn');
        const payload = readPayload();
        const targetFmt = el('romanWbTargetFormat') ? el('romanWbTargetFormat').value : 'lys';
        if (!payload || !String(payload.sourceContent || '').trim()) {
            alert(t('romanWb.errNoPayload'));
            return;
        }
        const sourceFormat = String(payload.detectedFormat || 'lys').toLowerCase();
        if (sourceFormat === 'lrc' && targetFmt === 'lys') {
            alert(t('romanWb.errLrcToLys'));
            return;
        }
        const origLabel = btn ? btn.textContent : '';
        try {
            const requestBody = _repairReady
                ? (() => {
                    const b = buildRomanizeRequestBody(false);
                    if (b) {
                        b.repair_instruction = buildRepairInstruction();
                    }
                    return b;
                })()
                : buildRomanizeRequestBody(true);
            if (!requestBody) {
                alert(t('romanWb.errNoPayload'));
                return;
            }
            if (btn) {
                btn.textContent = t('romanWb.copyPromptPending');
                btn.disabled = true;
            }
            const resp = await fetch('/romanize_lyrics_prompt', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(requestBody)
            });
            const data = await resp.json().catch(() => ({}));
            if (data.status !== 'success') {
                let errMsg = data.message || t('romanWb.errorFromServer');
                if (resp.status === 400 && errMsg.indexOf('兼容模式') !== -1) {
                    errMsg = `${errMsg}\n${t('romanWb.errCompatNoRepair')}`;
                }
                showErrorCard(errMsg, data.errors || []);
                const ec = el('romanWbErrorsCard');
                if (ec) ec.style.display = 'block';
                if (btn) {
                    btn.disabled = false;
                    btn.textContent = origLabel || t('romanWb.copyPrompt');
                }
                return;
            }
            let clip = '';
            if (data.multiturn_prompt_text != null && String(data.multiturn_prompt_text).trim()) {
                clip = String(data.multiturn_prompt_text);
            } else if (data.messages && Array.isArray(data.messages) && data.messages.length) {
                clip = formatRomanMessagesForClipboard(data.messages);
            } else {
                clip = data.final_prompt != null ? String(data.final_prompt) : '';
            }
            await navigator.clipboard.writeText(clip);
            const pre = el('romanWbNumberedPre');
            if (pre && data.numbered_input_preview != null) {
                pre.textContent = data.numbered_input_preview;
            }
            updateKpiFromMeta({
                lines: data.lines,
                source_effective: data.source_effective,
                source_format: data.source_format,
                target_format: data.target_format
            });
            if (btn) btn.textContent = t('romanWb.promptCopied');
            setTimeout(() => {
                if (btn) {
                    btn.textContent = origLabel || t('romanWb.copyPrompt');
                    btn.disabled = false;
                }
            }, 1200);
        } catch (e) {
            console.error('[romanWb] copy prompt', e);
            alert(e.message || t('romanWb.errCopyFailed'));
            if (btn) {
                btn.textContent = origLabel || t('romanWb.copyPrompt');
                btn.disabled = false;
            }
        }
    }

    async function assembleManualRomanization() {
        if (_streamRunning || _manualAssembleBusy) return;
        const manualTa = el('romanWbManualInput');
        const manual = manualTa ? String(manualTa.value || '').trim() : '';
        if (!manual) {
            showErrorCard(t('romanWb.errNoManualInput'), []);
            const ec = el('romanWbErrorsCard');
            if (ec) ec.style.display = 'block';
            return;
        }
        const payload = readPayload();
        const targetFmt = el('romanWbTargetFormat') ? el('romanWbTargetFormat').value : 'lys';
        if (!payload || !String(payload.sourceContent || '').trim()) {
            showErrorCard(t('romanWb.errNoPayload'), []);
            const ec0 = el('romanWbErrorsCard');
            if (ec0) ec0.style.display = 'block';
            return;
        }
        const sourceFormat = String(payload.detectedFormat || 'lys').toLowerCase();
        if (sourceFormat === 'lrc' && targetFmt === 'lys') {
            showErrorCard(t('romanWb.errLrcToLys'), []);
            const ec1 = el('romanWbErrorsCard');
            if (ec1) ec1.style.display = 'block';
            return;
        }

        const errCard = el('romanWbErrorsCard');
        const errPre = el('romanWbErrors');
        if (errCard) errCard.style.display = 'none';
        if (errPre) errPre.textContent = '';

        clearStaleResultOutputsForManualAssemble();
        resetStageVisuals();
        setBadge('running');
        setValidationUi('running');
        setStageVisual('parse_source', 'active');
        _manualAssembleBusy = true;
        applyRomanWorkbenchToolbarLock();

        try {
            const requestBody = buildRomanizeRequestBody();
            if (!requestBody) {
                setStageVisual('parse_source', 'error');
                showErrorCard(t('romanWb.errNoPayload'), []);
                if (errCard) errCard.style.display = 'block';
                finishRomanWorkbench('fail', { failStage: 'parse_source' });
                return;
            }
            requestBody.manual_model_output = manual;
            const resp = await fetch('/romanize_lyrics_assemble', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(requestBody)
            });
            const data = await resp.json().catch(() => ({}));
            setStageVisual('parse_source', 'success');
            setStageVisual('send_request', 'success');
            setStageVisual('receive_stream', 'success');
            setStageVisual('validating', 'active');
            if (!resp.ok || data.status !== 'success') {
                setStageVisual('validating', 'error');
                setStageVisual('assembling', 'error');
                setStageVisual('done', 'error');
                showErrorCard(data.message || t('romanWb.errorFromServer'), data.errors || []);
                if (errCard) errCard.style.display = 'block';
                if (data.numbered_input_preview) {
                    const pre = el('romanWbNumberedPre');
                    if (pre) pre.textContent = data.numbered_input_preview;
                }
                if (data.raw_model_output) {
                    const rawPre = el('romanWbRawPre');
                    if (rawPre) rawPre.textContent = data.raw_model_output;
                }
                finishRomanWorkbench('fail', { failStage: 'validating' });
                return;
            }
            setStageVisual('validating', 'success');
            setStageVisual('assembling', 'active');
            setStageVisual('assembling', 'success');
            const text = data.result_text || '';
            const finalPre = el('romanWbFinalPre');
            const finalTa = el('romanWbFinalCopySource');
            if (finalPre) finalPre.textContent = text;
            if (finalTa) finalTa.value = text;
            const copyBtn = el('romanWbCopyResultBtn');
            if (copyBtn) copyBtn.disabled = !text;
            updateWeaveBackgroundButtonState();
            setStageVisual('done', 'success');
            finishRomanWorkbench('success');
        } catch (e) {
            console.error('[romanWb] manual assemble', e);
            setStageVisual('parse_source', 'error');
            showErrorCard(e.message || String(e), []);
            if (errCard) errCard.style.display = 'block';
            finishRomanWorkbench('fail', { failStage: 'parse_source' });
        } finally {
            _manualAssembleBusy = false;
            applyRomanWorkbenchToolbarLock();
            updateProgressFromStages();
        }
    }

    async function runRomanizationStream(forceFresh) {
        if (_streamRunning || _manualAssembleBusy) return;

        const payload = readPayload();
        const targetFmt = el('romanWbTargetFormat').value;

        if (!payload || !payload.sourceContent || !String(payload.sourceContent).trim()) {
            resetStageVisuals();
            showErrorCard(t('romanWb.errNoPayload'), []);
            const ec0 = el('romanWbErrorsCard');
            if (ec0) ec0.style.display = 'block';
            finishRomanWorkbench('fail', { failStage: 'parse_source' });
            return;
        }

        const sourceFormat = String(payload.detectedFormat || 'lys').toLowerCase();
        if (sourceFormat === 'lrc' && targetFmt === 'lys') {
            resetStageVisuals();
            showErrorCard(t('romanWb.errLrcToLys'), []);
            const ec1 = el('romanWbErrorsCard');
            if (ec1) ec1.style.display = 'block';
            finishRomanWorkbench('fail', { failStage: 'parse_source' });
            return;
        }

        _lastRequestWasRepair = Boolean(!forceFresh && _repairReady);
        if (_lastRequestWasRepair) {
            _lastRepairInstructionSent = buildRepairInstruction();
        }

        _abortController = new AbortController();
        const signal = _abortController.signal;

        resetOutputPanels();
        resetStageVisuals();
        setBadge('running');
        setRunningUi(true);
        setValidationUi('running');

        const ctx = { meta: null, rawText: '', success: false, failed: false };

        try {
            const requestBody = buildRomanizeRequestBody(forceFresh);
            if (!requestBody) {
                showErrorCard(t('romanWb.errNoPayload'), []);
                const ecB = el('romanWbErrorsCard');
                if (ecB) ecB.style.display = 'block';
                finishRomanWorkbench('fail', { failStage: 'parse_source' });
                return;
            }
            const resp = await fetch('/romanize_lyrics', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(requestBody),
                signal
            });

            const ct = (resp.headers.get('content-type') || '').toLowerCase();
            if (ct.includes('application/json')) {
                const data = await resp.json().catch(() => ({}));
                const msg0 = data && data.message ? String(data.message) : '';
                if (msg0.indexOf('兼容模式') !== -1 || msg0.indexOf('compat') !== -1) {
                    resetRomanRepairState();
                }
                if (data && data.status === 'error') {
                    showErrorCard(data.message || t('romanWb.errorFromServer'), data.errors || []);
                    const ecj = el('romanWbErrorsCard');
                    if (ecj) ecj.style.display = 'block';
                    if (data.numbered_input_preview) {
                        const pre = el('romanWbNumberedPre');
                        if (pre) pre.textContent = data.numbered_input_preview;
                    }
                    if (data.raw_model_output) {
                        const rawPre = el('romanWbRawPre');
                        if (rawPre) rawPre.textContent = data.raw_model_output;
                    }
                    finishRomanWorkbench('fail', { failStage: 'send_request' });
                } else {
                    const fallbackMsg = !resp.ok
                        ? (data && data.message ? data.message : t('romanWb.httpError', { status: resp.status }))
                        : (data && data.message ? data.message : t('romanWb.errorFromServer'));
                    showErrorCard(fallbackMsg, (data && data.errors) || []);
                    const ecu = el('romanWbErrorsCard');
                    if (ecu) ecu.style.display = 'block';
                    if (data && data.numbered_input_preview) {
                        const pre = el('romanWbNumberedPre');
                        if (pre) pre.textContent = data.numbered_input_preview;
                    }
                    if (data && data.raw_model_output) {
                        const rawPre = el('romanWbRawPre');
                        if (rawPre) rawPre.textContent = data.raw_model_output;
                    }
                    finishRomanWorkbench('fail', { failStage: 'send_request' });
                }
                return;
            }

            if (!resp.ok) {
                let msg = t('romanWb.httpError', { status: resp.status });
                try {
                    const data = await resp.json();
                    if (data.message) msg = data.message;
                    if (String(data.message || '').indexOf('兼容模式') !== -1) {
                        resetRomanRepairState();
                    }
                } catch (_) {}
                showErrorCard(msg, []);
                const ech = el('romanWbErrorsCard');
                if (ech) ech.style.display = 'block';
                finishRomanWorkbench('fail', { failStage: 'send_request' });
                return;
            }

            if (!ct.includes('event-stream') && !ct.includes('text/plain')) {
                showErrorCard(t('romanWb.notStream'), []);
                const ecs = el('romanWbErrorsCard');
                if (ecs) ecs.style.display = 'block';
                finishRomanWorkbench('fail', { failStage: 'send_request' });
                return;
            }

            if (!resp.body || !resp.body.getReader) {
                showErrorCard(t('romanWb.notStream'), []);
                const ecr = el('romanWbErrorsCard');
                if (ecr) ecr.style.display = 'block';
                finishRomanWorkbench('fail', { failStage: 'send_request' });
                return;
            }

            const reader = resp.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                buffer += decoder.decode(value, { stream: true });
                let nl;
                while ((nl = buffer.indexOf('\n')) !== -1) {
                    const row = buffer.slice(0, nl);
                    buffer = buffer.slice(nl + 1);
                    processSseLine(row, ctx);
                }
            }
            buffer += decoder.decode();
            if (buffer) {
                buffer.split('\n').forEach((row) => processSseLine(row, ctx));
            }

            if (!ctx.success && !ctx.failed && !signal.aborted) {
                showErrorCard(t('romanWb.errFailed'), []);
                const ecf = el('romanWbErrorsCard');
                if (ecf) ecf.style.display = 'block';
                finishRomanWorkbench('fail', { incompleteStream: true });
            }
        } catch (e) {
            if (e.name === 'AbortError') {
                showErrorCard(t('romanWb.aborted'), []);
                const eca = el('romanWbErrorsCard');
                if (eca) eca.style.display = 'block';
                finishRomanWorkbench('stopped');
            } else {
                showErrorCard(e.message || String(e), []);
                const eco = el('romanWbErrorsCard');
                if (eco) eco.style.display = 'block';
                finishRomanWorkbench('fail', { incompleteStream: true });
            }
        } finally {
            setRunningUi(false);
            _abortController = null;
            applyRomanWorkbenchToolbarLock();
        }
    }

    async function runRomanizationPrimary() {
        await runRomanizationStream(false);
    }

    async function runRomanizationFromScratch() {
        resetRomanRepairState();
        await runRomanizationStream(true);
    }

    function stopRomanization() {
        if (_abortController) {
            _abortController.abort();
        }
    }

    function clearViews() {
        if (_streamRunning || _manualAssembleBusy) return;
        resetRomanRepairState();
        resetOutputPanels();
        resetStageVisuals();
        setBadge('idle');
        setProgress(0);
        _lastValidationMode = 'idle';
        _lastTrustMode = 'none';
        setValidationUi('idle');
        setTrustHint('none');
        const copyBtn = el('romanWbCopyResultBtn');
        if (copyBtn) copyBtn.disabled = true;
        applyRomanWorkbenchToolbarLock();
    }

    function copyResult() {
        const finalTa = el('romanWbFinalCopySource');
        if (!finalTa || !finalTa.value) return;
        navigator.clipboard.writeText(finalTa.value).then(() => {
            const btn = el('romanWbCopyResultBtn');
            const prevLabel = btn.textContent;
            btn.textContent = t('romanWb.copied');
            setTimeout(() => { btn.textContent = prevLabel || t('romanWb.copyResult'); }, 1200);
        }).catch(() => alert(t('romanWb.errCopyFailed')));
    }

    async function weaveBackgroundFromFinal() {
        const fmt = el('romanWbTargetFormat');
        if (!fmt || fmt.value !== 'lys') {
            alert(t('romanWb.errLrcNoBackgroundWeave'));
            return;
        }
        const romanLysText = String(getFinalResultText() || '').trim();
        if (!romanLysText) {
            alert(t('romanWb.errNoFinalLys'));
            return;
        }
        if (_weaveBusy || _streamRunning || _manualAssembleBusy) return;
        const weaveBtn = el('romanWbWeaveBackgroundBtn');
        const prevWeaveLabel = weaveBtn ? weaveBtn.textContent : '';
        _weaveBusy = true;
        applyRomanWorkbenchToolbarLock();
        if (weaveBtn) weaveBtn.textContent = t('romanWb.weaveBackgroundPending');
        try {
            const resp = await fetch('/romanize_lyrics_weave_background', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ roman_lys_text: romanLysText })
            });
            const data = await resp.json().catch(() => ({}));
            if (!resp.ok || data.status !== 'success') {
                showErrorCard(data.message || t('romanWb.errorFromServer'), data.errors || []);
                const ec = el('romanWbErrorsCard');
                if (ec) ec.style.display = 'block';
                return;
            }
            const woven = data.woven_text || '';
            const bgPre = el('romanWbBackgroundPre');
            const bgTa = el('romanWbBackgroundCopySource');
            if (bgPre) bgPre.textContent = woven;
            if (bgTa) bgTa.value = woven;
            const copyBgBtn = el('romanWbCopyBackgroundBtn');
            if (copyBgBtn) copyBgBtn.disabled = !String(woven).trim();
        } catch (e) {
            console.error('[romanWb] weave background', e);
            showErrorCard(e.message || String(e), []);
            const ec = el('romanWbErrorsCard');
            if (ec) ec.style.display = 'block';
        } finally {
            _weaveBusy = false;
            if (weaveBtn) weaveBtn.textContent = prevWeaveLabel || t('romanWb.weaveBackground');
            applyRomanWorkbenchToolbarLock();
        }
    }

    function copyBackgroundResult() {
        const ta = el('romanWbBackgroundCopySource');
        if (!ta || !String(ta.value || '').trim()) return;
        navigator.clipboard.writeText(ta.value).then(() => {
            const btn = el('romanWbCopyBackgroundBtn');
            if (!btn) return;
            const prevLabel = btn.textContent;
            btn.textContent = t('romanWb.copied');
            setTimeout(() => { btn.textContent = prevLabel || t('romanWb.copyBackgroundResult'); }, 1200);
        }).catch(() => alert(t('romanWb.errCopyFailed')));
    }

    function initRomanWorkbench() {
        if (typeof applyI18nToStaticElements === 'function') {
            applyI18nToStaticElements();
        }
        document.title = t('romanWb.pageTitle');

        _romanPayloadCache = readPayload();
        renderSourceInfo();

        const targetSelect = el('romanWbTargetFormat');
        if (_romanPayloadCache && targetSelect) {
            updateLysHint(String(_romanPayloadCache.detectedFormat || '').toLowerCase(), targetSelect);
        }

        resetStageVisuals();
        setBadge('idle');
        setValidationUi('idle');

        el('romanWbRunBtn').addEventListener('click', runRomanizationPrimary);
        el('romanWbRegenBtn').addEventListener('click', runRomanizationFromScratch);
        el('romanWbStopBtn').addEventListener('click', stopRomanization);
        el('romanWbClearBtn').addEventListener('click', clearViews);
        el('romanWbCopyResultBtn').addEventListener('click', copyResult);
        const weaveBtn = el('romanWbWeaveBackgroundBtn');
        if (weaveBtn) weaveBtn.addEventListener('click', () => { weaveBackgroundFromFinal(); });
        const copyBgBtn = el('romanWbCopyBackgroundBtn');
        if (copyBgBtn) copyBgBtn.addEventListener('click', copyBackgroundResult);
        const copyPromptBtn = el('romanWbCopyPromptBtn');
        if (copyPromptBtn) copyPromptBtn.addEventListener('click', copyRomanizationPrompt);
        const manualAssembleBtn = el('romanWbManualAssembleBtn');
        if (manualAssembleBtn) manualAssembleBtn.addEventListener('click', assembleManualRomanization);
        if (targetSelect) {
            targetSelect.addEventListener('change', () => {
                const src = _romanPayloadCache
                    ? String(_romanPayloadCache.detectedFormat || '').toLowerCase()
                    : '';
                updateLysHint(src, targetSelect);
                updateWeaveBackgroundButtonState();
            });
        }
        applyRomanWorkbenchToolbarLock();
    }

    if (typeof registerDynamicI18nCallback === 'function') {
        registerDynamicI18nCallback(function () {
            document.title = t('romanWb.pageTitle');
            renderSourceInfo();
            const targetSelect = el('romanWbTargetFormat');
            if (_romanPayloadCache && targetSelect) {
                updateLysHint(String(_romanPayloadCache.detectedFormat || '').toLowerCase(), targetSelect);
            }
            if (!_streamRunning && !_manualAssembleBusy) {
                setBadge(_lastBadgeMode);
                const runBtn = el('romanWbRunBtn');
                if (runBtn) runBtn.textContent = _repairReady ? t('romanWb.repair') : t('romanWb.run');
                const copyBtn = el('romanWbCopyResultBtn');
                if (copyBtn && copyBtn.disabled) {
                    copyBtn.textContent = t('romanWb.copyResult');
                }
                const weaveBtnI18n = el('romanWbWeaveBackgroundBtn');
                if (weaveBtnI18n && !_weaveBusy) {
                    weaveBtnI18n.textContent = t('romanWb.weaveBackground');
                }
                const copyBgI18n = el('romanWbCopyBackgroundBtn');
                if (copyBgI18n) {
                    copyBgI18n.textContent = t('romanWb.copyBackgroundResult');
                }
                const copyPromptBtn = el('romanWbCopyPromptBtn');
                if (copyPromptBtn) {
                    copyPromptBtn.textContent = t('romanWb.copyPrompt');
                }
                const manualAssembleBtn = el('romanWbManualAssembleBtn');
                if (manualAssembleBtn) manualAssembleBtn.textContent = t('romanWb.manualAssemble');
                setValidationUi(_lastValidationMode);
                setTrustHint(_lastTrustMode);
                updateErrorCardRepairHint();
            }
            const stopBtn = el('romanWbStopBtn');
            if (stopBtn) stopBtn.textContent = t('romanWb.stop');
            const regenBtn = el('romanWbRegenBtn');
            if (regenBtn) regenBtn.textContent = t('romanWb.regenerate');
            const clearBtn = el('romanWbClearBtn');
            if (clearBtn) clearBtn.textContent = t('romanWb.clear');
        });
    }

    document.addEventListener('DOMContentLoaded', initRomanWorkbench);
})();
