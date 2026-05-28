(function () {
    const STAGE_KEYS = ['lyricsPrep', 'thinkingRequest', 'thinkingOutput', 'translationRequest', 'translationOutput', 'postProcessing'];

    let _abortController = null;
    let _streamRunning = false;
    let _canUseAi = true;
    let _lastBadgeMode = 'idle';

    const el = (id) => document.getElementById(id);

    function isTranslateWorkbenchPage() {
        return Boolean(el('translateWbRunBtn'));
    }

    function updateTranslateWorkbenchStatus(message, type = 'info', suspectLines = []) {
        const statusEl = el('translateWbStatusMessage');
        if (!statusEl) {
            return;
        }

        statusEl.style.display = 'none';
        statusEl.className = 'status-message';
        statusEl.classList.remove('translation-in-progress');
        statusEl.innerHTML = '';

        if (!message) {
            return;
        }

        statusEl.classList.add(`status-${type}`);
        const isProgressMessage = typeof message === 'object' && message !== null && Array.isArray(message.stages);
        const appendSuspectLines = () => {
            if (type !== 'error' || !Array.isArray(suspectLines) || suspectLines.length === 0) {
                return;
            }

            const reminder = document.createElement('div');
            reminder.textContent = t('status.suspectLines');
            reminder.style.marginTop = '6px';
            statusEl.appendChild(reminder);

            const list = document.createElement('ul');
            list.className = 'status-message__list';
            suspectLines.slice(0, 5).forEach((item) => {
                const li = document.createElement('li');
                li.textContent = t('runtime.suspectLine', { line: item.line_number, content: item.line_content });
                list.appendChild(li);
            });
            statusEl.appendChild(list);

            if (suspectLines.length > 5) {
                const extra = document.createElement('div');
                extra.textContent = t('runtime.moreSuspectLines', { count: suspectLines.length - 5 });
                extra.style.marginTop = '4px';
                statusEl.appendChild(extra);
            }
        };

        if (isProgressMessage) {
            const stages = Array.isArray(message.stages) ? message.stages : [];
            const activeStage = stages.find((stage) => stage && stage.state === 'active');
            const summaryText = typeof message.summary === 'string' && message.summary.trim().length > 0
                ? message.summary.trim()
                : (activeStage && activeStage.label ? activeStage.label : (type === 'info' ? t('translate.thinkingPhase') : t('batch.progress')));
            const useShine = type === 'info' && message.useShine !== false;

            if (useShine && summaryText) {
                statusEl.classList.add('translation-in-progress');
                const shineLink = document.createElement('a');
                shineLink.href = '#';
                shineLink.className = 'btn-shine';
                shineLink.textContent = summaryText;
                statusEl.appendChild(shineLink);
            } else if (summaryText) {
                const summaryBlock = document.createElement('div');
                summaryBlock.className = 'status-message__summary';
                summaryBlock.textContent = summaryText;
                statusEl.appendChild(summaryBlock);
            }

            if (message.details && Array.isArray(message.details)) {
                message.details.forEach((detail) => {
                    const detailBlock = document.createElement('div');
                    detailBlock.textContent = detail;
                    statusEl.appendChild(detailBlock);
                });
            }

            appendSuspectLines();
            syncHeaderStageList(stages);
        } else {
            const isTranslating = type === 'info' && typeof message === 'string'
                && (message.includes('正在翻译') || message.includes('Translating') || message.includes('translating'));

            if (isTranslating) {
                statusEl.classList.add('translation-in-progress');
                const shineLink = document.createElement('a');
                shineLink.href = '#';
                shineLink.className = 'btn-shine';
                shineLink.textContent = t('translate.thinkingPhase');
                statusEl.appendChild(shineLink);
            } else {
                const messageBlock = document.createElement('div');
                messageBlock.textContent = message;
                statusEl.appendChild(messageBlock);
            }

            appendSuspectLines();
        }

        statusEl.style.display = 'block';
    }

    function syncHeaderStageList(stages) {
        if (!Array.isArray(stages)) {
            return;
        }
        stages.forEach((stage) => {
            if (!stage || !stage.key) {
                return;
            }
            const item = document.querySelector(`#translateWbStageList [data-stage="${stage.key}"]`);
            if (!item) {
                return;
            }
            item.classList.remove(
                'status-progress__item--pending',
                'status-progress__item--active',
                'status-progress__item--success',
                'status-progress__item--error'
            );
            const state = stage.state || 'pending';
            item.classList.add(`status-progress__item--${state}`);
        });
        updateProgressFromHeaderStages();
    }

    function resetHeaderStageList() {
        STAGE_KEYS.forEach((key) => {
            const item = document.querySelector(`#translateWbStageList [data-stage="${key}"]`);
            if (!item) {
                return;
            }
            item.classList.remove(
                'status-progress__item--active',
                'status-progress__item--success',
                'status-progress__item--error'
            );
            item.classList.add('status-progress__item--pending');
        });
        setProgress(0);
    }

    function updateProgressFromHeaderStages() {
        let completed = 0;
        let hasActive = false;
        STAGE_KEYS.forEach((key) => {
            const item = document.querySelector(`#translateWbStageList [data-stage="${key}"]`);
            if (!item) {
                return;
            }
            if (item.classList.contains('status-progress__item--success')) {
                completed += 1;
            }
            if (item.classList.contains('status-progress__item--active')) {
                hasActive = true;
            }
        });
        const pct = Math.min(100, ((completed + (hasActive ? 0.45 : 0)) / STAGE_KEYS.length) * 100);
        setProgress(pct);
    }

    function setProgress(pct) {
        const bar = el('translateWbProgressBar');
        const wrap = bar && bar.parentElement;
        if (bar) {
            bar.style.width = `${Math.round(pct)}%`;
        }
        if (wrap && wrap.setAttribute) {
            wrap.setAttribute('aria-valuenow', String(Math.round(pct)));
        }
    }

    function setBadge(mode) {
        _lastBadgeMode = mode;
        const badge = el('translateWbStatusBadge');
        if (!badge) {
            return;
        }
        badge.classList.remove('idle', 'running', 'completed', 'stopped', 'failed');
        if (mode === 'running') {
            badge.classList.add('running');
            badge.textContent = t('translateWb.badgeRunning');
        } else if (mode === 'done') {
            badge.classList.add('completed');
            badge.textContent = t('translateWb.badgeDone');
        } else if (mode === 'stopped') {
            badge.classList.add('stopped');
            badge.textContent = t('translateWb.badgeStopped');
        } else if (mode === 'failed') {
            badge.classList.add('failed');
            badge.textContent = t('translateWb.badgeFailed');
        } else {
            badge.classList.add('idle');
            badge.textContent = t('translateWb.badgeIdle');
        }
    }

    function applyToolbarLock() {
        const runBtn = el('translateWbRunBtn');
        const stopBtn = el('translateWbStopBtn');
        const copyBtn = el('translateWbCopyResultBtn');
        const settingsBtn = el('translateWbAiSettingsBtn');
        const sourceInput = el('translateWbSourceInput');

        if (runBtn) {
            runBtn.disabled = _streamRunning || !_canUseAi;
        }
        if (stopBtn) {
            stopBtn.disabled = !_streamRunning;
        }
        if (copyBtn && _streamRunning) {
            copyBtn.disabled = true;
        }
        if (settingsBtn) {
            settingsBtn.disabled = _streamRunning;
        }
        if (sourceInput) {
            sourceInput.disabled = _streamRunning;
        }
    }

    function setRunningUi(running) {
        _streamRunning = running;
        applyToolbarLock();
    }

    function buildResultText(translationContent, thinkingContent, reasoningContent) {
        const mergeFn = typeof window.mergeAiTranslationDisplayText === 'function'
            ? window.mergeAiTranslationDisplayText
            : null;
        if (mergeFn) {
            return mergeFn({
                translation: translationContent,
                thinking: thinkingContent,
                reasoning: reasoningContent,
            });
        }
        const sections = [];
        if (translationContent) {
            sections.push(translationContent);
        }
        if (thinkingContent) {
            sections.push('歌曲理解:\n' + thinkingContent);
        }
        if (reasoningContent) {
            sections.push('思考过程:\n' + reasoningContent);
        }
        return sections.join('\n\n');
    }

    async function runTranslateWorkbench() {
        if (_streamRunning || !_canUseAi) {
            return;
        }

        const sourceInput = el('translateWbSourceInput');
        const resultOutput = el('translateWbResultOutput');
        const lyricsContent = sourceInput ? String(sourceInput.value || '').trim() : '';
        if (!lyricsContent) {
            updateTranslateWorkbenchStatus(t('translateWb.errEmptySource'), 'error');
            alert(t('translateWb.errEmptySource'));
            return;
        }

        _abortController = new AbortController();
        const signal = _abortController.signal;

        resetHeaderStageList();
        updateTranslateWorkbenchStatus('');
        if (resultOutput) {
            resultOutput.value = '';
        }
        const copyBtn = el('translateWbCopyResultBtn');
        if (copyBtn) {
            copyBtn.disabled = true;
        }

        setBadge('running');
        setRunningUi(true);

        const translationStages = [
            { key: 'lyricsPrep', label: t('translate.stage.lyricsPrep'), state: 'active', description: t('translate.checkingLyrics') },
            { key: 'thinkingRequest', label: t('translate.stage.thinkingRequest'), state: 'pending' },
            { key: 'thinkingOutput', label: t('translate.stage.thinkingOutput'), state: 'pending' },
            { key: 'translationRequest', label: t('translate.stage.translationRequest'), state: 'pending' },
            { key: 'translationOutput', label: t('translate.stage.translationOutput'), state: 'pending' },
            { key: 'postProcessing', label: t('translate.stage.postProcessing'), state: 'pending' }
        ];
        const stageMap = new Map(translationStages.map((stage) => [stage.key, stage]));
        const getActiveStage = () => translationStages.find((stage) => stage.state === 'active');
        let stageDirty = false;

        const markDirty = (changed) => {
            if (changed) {
                stageDirty = true;
            }
            return changed;
        };

        const renderStages = (summary, statusType = 'info', options = {}) => {
            const activeStage = getActiveStage();
            let summaryText = summary;
            if (!summaryText) {
                if (statusType === 'info' && activeStage) {
                    summaryText = `${t('translate.translating')} · ${activeStage.label}`;
                } else if (activeStage) {
                    summaryText = activeStage.label;
                } else if (statusType === 'success') {
                    summaryText = t('translate.complete');
                } else if (statusType === 'error') {
                    const errorStage = translationStages.find((stage) => stage.state === 'error');
                    summaryText = errorStage ? `${errorStage.label}${t('translate.stageError')}` : t('translate.failed');
                } else {
                    summaryText = t('translate.status');
                }
            }

            updateTranslateWorkbenchStatus({
                summary: summaryText,
                stages: translationStages.map((stage) => ({
                    key: stage.key,
                    label: stage.label,
                    state: stage.state,
                    description: stage.description
                })),
                useShine: options.useShine !== undefined ? options.useShine : (statusType === 'info' && Boolean(activeStage))
            }, statusType, options.suspectLines);

            stageDirty = false;
        };

        const activateStage = (key, description) => {
            const stage = stageMap.get(key);
            if (!stage) {
                return false;
            }
            if (stage.state === 'error') {
                if (description !== undefined && stage.description !== description) {
                    stage.description = description;
                    return true;
                }
                return false;
            }

            let changed = false;
            translationStages.forEach((item) => {
                if (item.key !== key && item.state === 'active') {
                    item.state = 'success';
                    changed = true;
                }
            });
            if (stage.state !== 'active') {
                stage.state = 'active';
                changed = true;
            }
            if (description !== undefined && stage.description !== description) {
                stage.description = description;
                changed = true;
            }
            return changed;
        };

        const completeStage = (key, description) => {
            const stage = stageMap.get(key);
            if (!stage) {
                return false;
            }
            let changed = false;
            if (stage.state !== 'error' && stage.state !== 'success') {
                stage.state = 'success';
                changed = true;
            }
            if (description !== undefined && stage.description !== description) {
                stage.description = description;
                changed = true;
            }
            return changed;
        };

        const failStage = (key, description) => {
            const stage = stageMap.get(key);
            if (!stage) {
                return false;
            }
            let changed = false;
            if (stage.state !== 'error') {
                stage.state = 'error';
                changed = true;
            }
            if (description !== undefined && stage.description !== description) {
                stage.description = description;
                changed = true;
            }
            return changed;
        };

        const updateStageDescription = (key, description) => {
            const stage = stageMap.get(key);
            if (!stage) {
                return false;
            }
            if (stage.description !== description) {
                stage.description = description;
                return true;
            }
            return false;
        };

        const getStageState = (key) => {
            const stage = stageMap.get(key);
            return stage ? stage.state : undefined;
        };

        const flushStages = (summary, statusType = 'info', options = {}) => {
            const hasOptions = options && (
                (Array.isArray(options.suspectLines) && options.suspectLines.length > 0)
                || options.useShine !== undefined
            );
            if (!stageDirty && !summary && statusType === 'info' && !hasOptions) {
                return;
            }
            renderStages(summary, statusType, options || {});
        };

        renderStages(t('batch.preparingTranslation'));

        try {
            markDirty(completeStage('lyricsPrep', t('status.readLines', { count: lyricsContent.split('\n').length })));
            flushStages();

            if (typeof ensureAiRuntimeSummaryForProgress === 'function') {
                await ensureAiRuntimeSummaryForProgress();
            }
            const translationRuntime = typeof getAiRuntimeSummaryLabel === 'function'
                ? getAiRuntimeSummaryLabel('translation')
                : {};
            const thinkingRuntime = typeof getAiRuntimeSummaryLabel === 'function'
                ? getAiRuntimeSummaryLabel('thinking')
                : {};
            const provider = translationRuntime.provider || t('aiSettings.sourceApiKeyManaged');
            const model = translationRuntime.model || t('aiSettings.sourceApiKeyManaged');
            const thinkingProvider = thinkingRuntime.provider || provider;
            const thinkingModel = thinkingRuntime.model || model;
            const thinkingEnabled = typeof isThinkingEnabledFromRuntimeSummary === 'function'
                ? isThinkingEnabledFromRuntimeSummary()
                : false;

            let thinkingRequestAcknowledged = !thinkingEnabled;
            let thinkingOutputCompleted = !thinkingEnabled;
            let translationRequestActivated = false;
            let translationRequestAcknowledged = false;
            let translationOutputActivated = false;
            let postProcessingActivated = false;

            if (thinkingEnabled) {
                markDirty(activateStage('thinkingRequest', t('batch.prepareThinking', { p: thinkingProvider, m: thinkingModel })));
            } else {
                markDirty(completeStage('thinkingRequest', t('batch.skipHasTranslation')));
                markDirty(completeStage('thinkingOutput', t('batch.skipHasTranslation')));
                markDirty(activateStage('translationRequest', t('batch.prepareTranslation', { p: provider, m: model })));
                thinkingOutputCompleted = true;
                translationRequestActivated = true;
            }
            flushStages();

            const response = await fetch('/translate_lyrics', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    content: lyricsContent,
                    song_name: 'paste-translate'
                }),
                signal
            });

            const contentType = response.headers.get('Content-Type') || '';
            if (!response.ok || contentType.includes('application/json')) {
                let errorData = {};
                try {
                    errorData = await response.json();
                } catch (parseError) {
                    console.error('[translateWb] parse error response', parseError);
                }

                const errorMessage = errorData.message || `翻译失败（HTTP ${response.status}）`;
                const activeStage = getActiveStage();
                if (activeStage) {
                    markDirty(failStage(activeStage.key, t('batch.translationErrorMsg', { msg: errorMessage })));
                } else {
                    markDirty(failStage('translationRequest', t('batch.translationErrorMsg', { msg: errorMessage })));
                }
                flushStages(errorMessage, 'error', { useShine: false, suspectLines: errorData.suspectLines });
                setBadge('failed');
                if (!errorData.suspectLines || errorData.suspectLines.length === 0) {
                    alert(errorMessage);
                }
                return;
            }

            if (!response.body || !response.body.getReader) {
                markDirty(failStage('translationRequest', t('batch.noStreamSupport')));
                flushStages(t('batch.noStreamSupport'), 'error', { useShine: false });
                setBadge('failed');
                throw new Error(t('batch.noStreamSupport'));
            }

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let translationContent = '';
            let reasoningContent = '';
            let thinkingContent = '';
            let buffer = '';
            let translationHasTimestamps = true;
            let translationReceived = false;
            let streamFailed = false;

            const updateResultOutput = () => {
                if (resultOutput) {
                    resultOutput.value = buildResultText(
                        translationContent,
                        thinkingContent,
                        reasoningContent
                    );
                }
                if (copyBtn) {
                    copyBtn.disabled = !String(resultOutput ? resultOutput.value : '').trim();
                }
            };

            const processLine = (line) => {
                if (!line) {
                    return;
                }

                if (line.startsWith('thinking:')) {
                    if (!thinkingRequestAcknowledged && getStageState('thinkingRequest') === 'active') {
                        markDirty(completeStage('thinkingRequest', t('batch.songUnderstanding')));
                        markDirty(activateStage('thinkingOutput', t('batch.generatingThinking')));
                        thinkingRequestAcknowledged = true;
                    } else if (getStageState('thinkingOutput') === 'pending') {
                        markDirty(activateStage('thinkingOutput', t('batch.generatingThinking')));
                    }

                    try {
                        const content = JSON.parse(line.slice(9));
                        if (content.summary) {
                            thinkingContent = content.summary;
                            updateResultOutput();
                            if (!thinkingOutputCompleted) {
                                markDirty(completeStage('thinkingOutput', t('batch.complete')));
                                markDirty(activateStage('translationRequest', t('batch.prepareTranslation', { p: provider, m: model })));
                                thinkingOutputCompleted = true;
                                translationRequestActivated = true;
                            }
                        } else if (content.error) {
                            thinkingContent = '思考模型调用失败：' + content.error;
                            updateResultOutput();
                            markDirty(failStage('thinkingOutput', t('batch.thinkingFailed')));
                            if (!translationRequestActivated) {
                                markDirty(activateStage('translationRequest', t('batch.prepareTranslation', { p: provider, m: model })));
                                translationRequestActivated = true;
                            }
                            thinkingOutputCompleted = true;
                        } else {
                            updateResultOutput();
                        }
                    } catch (e) {
                        console.error('[translateWb] parse thinking', e);
                    }

                    flushStages();
                    return;
                }

                if (line.startsWith('reasoning:')) {
                    if (!thinkingRequestAcknowledged && getStageState('thinkingRequest') === 'active') {
                        markDirty(completeStage('thinkingRequest', t('batch.songUnderstanding')));
                        markDirty(activateStage('thinkingOutput', t('batch.generatingThinking')));
                        thinkingRequestAcknowledged = true;
                    }
                    try {
                        const content = JSON.parse(line.slice(10));
                        if (content.reasoning) {
                            reasoningContent = content.reasoning;
                            updateResultOutput();
                            if (!thinkingOutputCompleted) {
                                markDirty(updateStageDescription('thinkingOutput', t('batch.pendingThinking')));
                            }
                        }
                    } catch (e) {
                        console.error('[translateWb] parse reasoning', e);
                    }

                    flushStages();
                    return;
                }

                if (line.startsWith('content:')) {
                    try {
                        const content = JSON.parse(line.slice(8));
                        if (content.status === 'error') {
                            const errorMessage = content.code === 'no_numbered_translations'
                                ? t('batch.noNumberedTranslations')
                                : (content.message || t('batch.translationError'));
                            if (!translationOutputActivated) {
                                markDirty(activateStage('translationOutput', t('batch.generatingTranslation')));
                                translationOutputActivated = true;
                            }
                            markDirty(failStage('translationOutput', errorMessage));
                            flushStages(errorMessage, 'error', { useShine: false });
                            streamFailed = true;
                            return;
                        }

                        if (Object.prototype.hasOwnProperty.call(content, 'hasTimestamps')) {
                            translationHasTimestamps = content.hasTimestamps;
                        }

                        if (!translationRequestActivated) {
                            markDirty(activateStage('translationRequest', t('batch.prepareTranslation', { p: provider, m: model })));
                            translationRequestActivated = true;
                        }

                        if (!translationRequestAcknowledged) {
                            markDirty(completeStage('translationRequest', t('batch.complete')));
                            translationRequestAcknowledged = true;
                        }

                        if (!translationOutputActivated) {
                            markDirty(activateStage('translationOutput', t('batch.generatingTranslation')));
                            translationOutputActivated = true;
                        }

                        if (content.translations) {
                            translationContent = content.translations.join('\n');
                            translationReceived = true;
                            updateResultOutput();
                        }
                    } catch (e) {
                        console.error('[translateWb] parse content', e);
                    }

                    flushStages();
                }
            };

            while (true) {
                const { done, value } = await reader.read();
                if (done) {
                    break;
                }

                buffer += decoder.decode(value, { stream: true });
                let newlineIndex;
                while ((newlineIndex = buffer.indexOf('\n')) !== -1) {
                    const line = buffer.slice(0, newlineIndex);
                    buffer = buffer.slice(newlineIndex + 1);
                    processLine(line);
                }
            }

            const finalChunk = decoder.decode();
            if (finalChunk) {
                buffer += finalChunk;
            }
            if (buffer) {
                buffer.split('\n').forEach((line) => processLine(line));
            }

            if (signal.aborted) {
                return;
            }

            if (streamFailed) {
                setBadge('failed');
                return;
            }

            if (typeof window.finalizeTranslationFromStream === 'function') {
                const finalized = window.finalizeTranslationFromStream({
                    translationContent,
                    thinkingContent,
                    reasoningContent,
                    translationReceived,
                });
                translationContent = finalized.translationContent;
                thinkingContent = finalized.thinkingContent;
                reasoningContent = finalized.reasoningContent;
                translationReceived = finalized.translationReceived;
                if (resultOutput) {
                    resultOutput.value = finalized.displayText;
                }
                if (copyBtn) {
                    copyBtn.disabled = !String(finalized.displayText || '').trim();
                }
            }

            markDirty(completeStage('translationRequest', t('batch.flowEnded')));
            if (!translationReceived) {
                const emptyMessage = t('batch.noNumberedTranslations');
                markDirty(failStage('translationOutput', emptyMessage));
                flushStages(emptyMessage, 'error', { useShine: false });
                setBadge('failed');
                return;
            }

            markDirty(completeStage('translationOutput', t('batch.receivedTranslation')));
            if (!postProcessingActivated) {
                markDirty(activateStage('postProcessing', t('batch.writingTranslation')));
                postProcessingActivated = true;
            }
            markDirty(completeStage('postProcessing', t('batch.translationWritten')));
            flushStages();

            const successMessage = (!translationHasTimestamps && translationReceived)
                ? t('batch.completeNoTimestamp')
                : t('batch.complete');

            flushStages(successMessage, 'success', { useShine: false });
            setBadge('done');
            setProgress(100);
        } catch (error) {
            if (error.name === 'AbortError') {
                updateTranslateWorkbenchStatus(t('translateWb.aborted'), 'error');
                setBadge('stopped');
                return;
            }
            console.error('[translateWb] translate error', error);
            const fallbackMessage = error && error.message ? error.message : t('batch.translationFailedMsg', { msg: String(error) });
            const activeStage = translationStages.find((stage) => stage.state === 'active');
            if (activeStage) {
                markDirty(failStage(activeStage.key, t('batch.translationError')));
            } else {
                markDirty(failStage('translationOutput', t('batch.translationError')));
            }
            flushStages(t('batch.translationFailedMsg', { msg: fallbackMessage }), 'error', { useShine: false });
            setBadge('failed');
            alert(t('alert.translateError'));
        } finally {
            setRunningUi(false);
            _abortController = null;
            applyToolbarLock();
        }
    }

    function stopTranslateWorkbench() {
        if (_abortController) {
            _abortController.abort();
        }
    }

    function copyTranslateResult() {
        const resultOutput = el('translateWbResultOutput');
        const text = resultOutput ? String(resultOutput.value || '') : '';
        if (!text.trim()) {
            return;
        }
        navigator.clipboard.writeText(text).then(() => {
            const btn = el('translateWbCopyResultBtn');
            if (!btn) {
                return;
            }
            const prevLabel = btn.textContent;
            btn.textContent = t('translateWb.copied');
            setTimeout(() => {
                btn.textContent = prevLabel || t('translateWb.copyResult');
            }, 1200);
        }).catch(() => alert(t('translateWb.errCopyFailed')));
    }

    async function showAISettings() {
        try {
            if (typeof refreshAiPresetCache === 'function') {
                try {
                    await refreshAiPresetCache();
                } catch (presetError) {
                    console.warn('[translateWb] refreshAiPresetCache failed, continuing with /get_ai_settings', presetError);
                }
            }
            const response = await fetch('/get_ai_settings');
            const data = await response.json();
            if (data.status !== 'success') {
                throw new Error(data.message || t('alert.getAISettingsFailed'));
            }

            const backendSettings = data.effective_settings || data.settings || {};
            if (typeof aiPresetPermissions !== 'undefined') {
                aiPresetPermissions = data.permissions || aiPresetPermissions || {};
            }
            if (typeof setAiRuntimeSummary === 'function') {
                setAiRuntimeSummary(data.runtime_summary);
            }
            const fieldVisibility = typeof resolveAiFieldVisibilityFromResponse === 'function'
                ? resolveAiFieldVisibilityFromResponse(data)
                : {};
            const effectiveSettings = typeof buildEffectiveAISettingsFromResponse === 'function'
                ? buildEffectiveAISettingsFromResponse(backendSettings, { fieldVisibility })
                : backendSettings;
            if (typeof setAiFieldVisibility === 'function') {
                setAiFieldVisibility(fieldVisibility);
            }

            const sourceMode = String(data.source_mode || 'manual').trim().toLowerCase();
            const sourcePresetId = String(data.source_preset_id || '').trim();
            const sourcePresetName = String((data.source_preset || {}).name || '').trim();
            let syncedSource = {
                mode: (sourceMode === 'preset' && sourcePresetId) ? 'preset' : 'manual',
                preset_id: sourcePresetId,
                preset_name: sourcePresetName
            };
            if (data.source_kind || data.source_label) {
                syncedSource = {
                    mode: (sourceMode === 'preset' && sourcePresetId) ? 'preset' : 'manual',
                    preset_id: sourcePresetId,
                    preset_name: sourcePresetName,
                    kind: String(data.source_kind || ''),
                    label: String(data.source_label || '')
                };
            }
            if (sourceMode === 'preset' && sourcePresetId && !(data.source_preset && data.source_preset.id)) {
                syncedSource.kind = syncedSource.kind || 'missing_preset';
            }
            if (typeof setAiSettingsSourceSaved === 'function') {
                setAiSettingsSourceSaved(syncedSource);
            }
            if (typeof setAiSettingsSourceDraft === 'function') {
                setAiSettingsSourceDraft(syncedSource);
            }

            if (typeof fillAIFormState === 'function') {
                fillAIFormState(effectiveSettings, { fieldVisibility });
            }
            if (typeof writeAIStateToLocalStorage === 'function') {
                writeAIStateToLocalStorage(effectiveSettings, aiPresetPermissions);
            }
            if (typeof applyAiPresetFieldPermissions === 'function') {
                applyAiPresetFieldPermissions(aiPresetPermissions);
            }
            if (typeof applyAiSettingsButtonPermissions === 'function') {
                applyAiSettingsButtonPermissions(Boolean(data.can_save_settings ?? data.can_use_ai), Boolean(data.can_edit_preset));
            }
            const modal = el('aiSettingsModal');
            if (modal) {
                modal.style.display = 'block';
            }
            if (typeof setAiSettingsStatus === 'function') {
                setAiSettingsStatus('idle');
            }
            if (typeof refreshReasoningControlCapabilityHint === 'function') {
                refreshReasoningControlCapabilityHint().catch(() => {});
            }
            if (typeof updateAiPresetSelect === 'function') {
                updateAiPresetSelect();
            }
            if (typeof safeUpdateBatchWorkbenchPresetSelect === 'function') {
                safeUpdateBatchWorkbenchPresetSelect();
            }
            if (typeof snapshotAiSettingsPreviewState === 'function') {
                aiSettingsInitialSnapshot = snapshotAiSettingsPreviewState();
            }
            if (typeof updateAiPresetApplyStatus === 'function') {
                updateAiPresetApplyStatus();
            }
        } catch (error) {
            console.error('[translateWb] get AI settings failed', error);
            alert(t('alert.getAISettingsFailed'));
        }
    }

    function closeAISettings(options = {}) {
        const { force = false } = options || {};
        if (!force && typeof hasPendingAiSettingsPreview === 'function' && hasPendingAiSettingsPreview()) {
            const shouldClose = confirm(t('aiSettings.discardChangesConfirm'));
            if (!shouldClose) {
                return false;
            }
        }
        if (typeof setAiSettingsSourceDraft === 'function' && typeof aiSettingsSourceSaved !== 'undefined') {
            setAiSettingsSourceDraft(aiSettingsSourceSaved);
        }
        if (typeof aiSettingsInitialSnapshot !== 'undefined' && aiSettingsInitialSnapshot?.form && typeof fillAIFormState === 'function') {
            fillAIFormState(aiSettingsInitialSnapshot.form, {
                fieldVisibility: typeof aiFieldVisibility !== 'undefined' ? aiFieldVisibility : {},
                skipProviderPreset: true
            });
        }
        if (typeof updateAiPresetSelect === 'function') {
            updateAiPresetSelect();
        }
        const modal = el('aiSettingsModal');
        if (modal) {
            modal.style.display = 'none';
        }
        return true;
    }

    window.showAISettings = showAISettings;
    window.closeAISettings = closeAISettings;

    let _translateWbI18nWarned = false;

    function isTranslateWbKeyLikeText(text) {
        if (!text || typeof text !== 'string') {
            return false;
        }
        return /^(translateWb\.|btn\.)/.test(text.trim());
    }

    function getTranslateWbNodeDisplayText(node) {
        if (!node) {
            return '';
        }
        if (node.tagName === 'INPUT' || node.tagName === 'TEXTAREA') {
            return (node.placeholder || '').trim();
        }
        return (node.textContent || '').trim();
    }

    /** Re-apply static i18n on standalone workbench; retry once if keys leaked into the DOM. */
    function ensureTranslateWorkbenchI18n() {
        if (!isTranslateWorkbenchPage()) {
            return;
        }

        if (typeof applyI18nToStaticElements === 'function') {
            applyI18nToStaticElements();
        }

        const probeNodes = [
            () => el('translateWbRunBtn'),
            () => document.querySelector('[data-i18n="translateWb.headerTitle"]'),
            () => document.querySelector('[data-i18n="translateWb.sourceLabel"]')
        ];

        let stillKeyLike = probeNodes.some((resolve) => {
            const node = resolve();
            return node && isTranslateWbKeyLikeText(getTranslateWbNodeDisplayText(node));
        });

        if (stillKeyLike && typeof applyI18nToStaticElements === 'function') {
            applyI18nToStaticElements();
            stillKeyLike = probeNodes.some((resolve) => {
                const node = resolve();
                return node && isTranslateWbKeyLikeText(getTranslateWbNodeDisplayText(node));
            });
        }

        const pageTitle = typeof t === 'function' ? t('translateWb.pageTitle') : '';
        if (pageTitle && !isTranslateWbKeyLikeText(pageTitle)) {
            document.title = pageTitle;
        }

        if (stillKeyLike && !_translateWbI18nWarned) {
            _translateWbI18nWarned = true;
            console.warn('[translateWb] i18n 未完全应用，部分界面仍显示为 key，请确认 LyricSphere-i18n.js 已正确加载。');
        }
    }

    async function initTranslateWorkbench() {
        ensureTranslateWorkbenchI18n();

        resetHeaderStageList();
        setBadge('idle');

        try {
            const response = await fetch('/get_ai_settings');
            const data = await response.json();
            _canUseAi = Boolean(data.can_use_ai);
            if (!_canUseAi) {
                updateTranslateWorkbenchStatus(t('translateWb.noAiPermission'), 'error');
            }
            if (typeof setAiRuntimeSummary === 'function' && data.runtime_summary) {
                setAiRuntimeSummary(data.runtime_summary);
            }
        } catch (error) {
            console.error('[translateWb] init permission check failed', error);
            _canUseAi = false;
            updateTranslateWorkbenchStatus(t('translateWb.noAiPermission'), 'error');
        }

        const runBtn = el('translateWbRunBtn');
        const stopBtn = el('translateWbStopBtn');
        const copyBtn = el('translateWbCopyResultBtn');
        const settingsBtn = el('translateWbAiSettingsBtn');

        if (runBtn) {
            runBtn.addEventListener('click', runTranslateWorkbench);
        }
        if (stopBtn) {
            stopBtn.addEventListener('click', stopTranslateWorkbench);
        }
        if (copyBtn) {
            copyBtn.addEventListener('click', copyTranslateResult);
        }
        if (settingsBtn) {
            settingsBtn.addEventListener('click', showAISettings);
        }

        applyToolbarLock();
    }

    if (typeof registerDynamicI18nCallback === 'function') {
        registerDynamicI18nCallback(function () {
            document.title = t('translateWb.pageTitle');
            if (!_streamRunning) {
                setBadge(_lastBadgeMode);
            }
        });
    }

    document.addEventListener('DOMContentLoaded', initTranslateWorkbench);
})();
