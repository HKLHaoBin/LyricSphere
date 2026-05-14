const _MUSIC_UPLOAD_MEDIA_EXTS = [
    '.mp4', '.webm', '.ogg', '.m4v', '.mov',
    '.mp3', '.flac', '.wav', '.m4a', '.aac', '.opus', '.oga', '.wma', '.ape', '.dff', '.dsf', '.mpc',
    '.mid', '.midi', '.aiff', '.aif', '.caf'
]

function isLikelyMusicMediaFile(file) {
    if (!file) return false
    if (file.type.startsWith('audio/') || file.type.startsWith('video/')) return true
    const lower = (file.name || '').toLowerCase()
    return _MUSIC_UPLOAD_MEDIA_EXTS.some((ext) => lower.endsWith(ext))
}

async function uploadMusicFile(file) {
    if (!file) return

    debugUploadAction('selected', 'music', file.name)

    try {
        if (!isLikelyMusicMediaFile(file)) {
            alert(t('file.uploadAudio'))
            return
        }

        const formData = new FormData()
        formData.append('file', file)

        const response = await fetch('/upload_music', {
            method: 'POST',
            body: formData
        })
        const data = await response.json()

        if (data.status === 'success') {
            document.getElementById('newMusicPath').value = data.filename
            updateMusicPath()
        } else {
            alert(t('alert.uploadFailedPrefix') + data.message)
        }
    } catch (error) {
        console.error('Error:', error)
        alert(t('alert.uploadFailed'))
    }
}

async function handleMusicUpload() {
    const fileInput = document.getElementById('musicUpload')
    const file = fileInput.files[0]

    if (!file) return

    await uploadMusicFile(file)
    fileInput.value = ''
}

const _IMAGE_UPLOAD_EXTS = ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.apng']
const _IMAGE_OR_VIDEO_UPLOAD_EXTS = [..._IMAGE_UPLOAD_EXTS, '.mp4', '.webm', '.ogg', '.m4v', '.mov']

function isAcceptableImageUploadFile(file) {
    const mime = file.type || ''
    if (mime.startsWith('image/')) return true
    const lower = (file.name || '').toLowerCase()
    return _IMAGE_UPLOAD_EXTS.some((ext) => lower.endsWith(ext))
}

function isAcceptableImageOrVideoUploadFile(file) {
    const mime = file.type || ''
    if (mime.startsWith('image/') || mime.startsWith('video/')) return true
    const lower = (file.name || '').toLowerCase()
    return _IMAGE_OR_VIDEO_UPLOAD_EXTS.some((ext) => lower.endsWith(ext))
}

async function handleImageUpload(file, type) {
    const fileInput = getImageUploadInput(type)
    if (!file) return

    if (type === 'album' || type === 'dynamicCoverPoster') {
        if (!isAcceptableImageUploadFile(file)) {
            alert(t('file.uploadImage'))
            if (fileInput) fileInput.value = ''
            return
        }
    } else if (type === 'background' || type === 'dynamicCover') {
        if (!isAcceptableImageOrVideoUploadFile(file)) {
            alert(t('file.uploadImageOrVideo'))
            if (fileInput) fileInput.value = ''
            return
        }
    }

    debugUploadAction('selected', type, file.name)

    try {
        const formData = new FormData()
        formData.append('file', file)

        const response = await fetch('/upload_image', {
            method: 'POST',
            body: formData
        })
        const result = await response.json()

        if (result.status === 'success') {
            if (type === 'album') {
                document.getElementById('newImagePath').value = result.filename
                updateImagePath()
            } else if (type === 'background') {
                document.getElementById('newBackgroundPath').value = result.filename
            } else if (type === 'dynamicCover') {
                document.getElementById('dynamicCoverPath').value = result.filename
                updateDynamicCoverPreview()
            } else if (type === 'dynamicCoverPoster') {
                document.getElementById('dynamicCoverPosterPath').value = result.filename
                updateDynamicCoverPreview()
            }
        } else {
            alert(t('alert.uploadFailedPrefix') + result.message)
        }
    } catch (error) {
        alert(t('alert.uploadErrorPrefix') + error.message)
    } finally {
        if (fileInput) {
            fileInput.value = ''
        }
    }
}

function deleteJson(filename) {
    if (confirm(t('runtime.deleteConfirm', {filename: filename}))) {
        fetch('/delete_json', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ filename: filename })
        })
            .then(response => response.json())
            .then(data => {
                if (data.status === 'success') {
                    alert(t('alert.deleteSuccess'))
                    location.reload()
                } else {
                    alert(t('alert.deleteFailedPrefix') + data.message)
                }
            })
    }
}

function handleLyricsUpload() {
    const fileInput = document.getElementById('lyricsUpload')
    const file = fileInput.files[0]
    if (!file) return

    debugUploadAction('selected', 'lyrics', file.name)

    const formData = new FormData()
    formData.append('file', file)

    fetch('/upload_lyrics', {
        method: 'POST',
        body: formData
    })
        .then(response => response.json())
        .then(data => {
            if (data.status === 'success') {
                document.getElementById('lyricsPath').value = data.filename
                updateLyricsPath(0)
                // 隐藏歌词相关按钮
                document.querySelector('.save-all-btn').style.display = 'none'
                document.querySelector('button[onclick="saveLyrics(0)"]').style.display = 'none'
                alert(t('alert.lyricsUploaded'))
            } else {
                alert(t('alert.uploadFailedPrefix') + data.message)
            }
        })
        .catch(error => {
            console.error('Error:', error)
            alert(t('alert.uploadFailed'))
        })
        .finally(() => {
            fileInput.value = ''
        })
}

function handleTranslationUpload() {
    const fileInput = document.getElementById('translationUpload')
    const file = fileInput.files[0]
    if (!file) return

    debugUploadAction('selected', 'translation', file.name)

    const formData = new FormData()
    formData.append('file', file)

    fetch('/upload_translation', {
        method: 'POST',
        body: formData
    })
        .then(response => response.json())
        .then(data => {
            if (data.status === 'success') {
                document.getElementById('translationPath').value = data.filename
                updateLyricsPath(1)
                // 隐藏翻译相关按钮
                document.querySelector('.save-all-btn').style.display = 'none'
                document.querySelector('button[onclick="saveLyrics(1)"]').style.display = 'none'
                alert(t('alert.lyricsUploaded'))
            } else {
                alert(t('alert.uploadFailedPrefix') + data.message)
            }
        })
        .catch(error => {
            console.error('Error:', error)
            alert(t('alert.uploadFailed'))
        })
        .finally(() => {
            fileInput.value = ''
        })
}

async function showBackupVersions(filename) {
    currentRestoreFile = filename
    const modal = document.getElementById('backupModal')
    const backupList = document.getElementById('backupList')
    backupList.innerHTML = '<div class="loading">' + t('backup.loading') + '</div>'

    try {
        const response = await fetch('/get_backups', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: normalizeStaticUrl(filename) })
        })
        const data = await response.json()

        backupList.innerHTML = ''
        if (data.backups && data.backups.length > 0) {
            data.backups.forEach(backup => {
                const btn = document.createElement('button')
                btn.className = 'action-button'
                btn.style.margin = '5px'
                btn.innerHTML = `
                    ${backup.time}
                    <div class="form-help-text" style="font-size:0.8em;">${backup.path.split('/').pop()}</div>
                `
                btn.onclick = () => restoreToVersion(backup.path)
                backupList.appendChild(btn)
            })
        } else {
            backupList.innerHTML = '<div>' + t('backup.noVersions') + '</div>'
        }
        modal.style.display = 'block'
    } catch (error) {
        alert(t('alert.getBackupFailedPrefix') + error)
    }
}

function closeBackupModal() {
    document.getElementById('backupModal').style.display = 'none'
}

async function quickRestore(filename) {
    if (confirm(t('runtime.restoreLatestConfirm'))) {
        try {
            const response = await fetch('/restore_file', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ file_path: normalizeStaticUrl(filename) })
            })
            const result = await response.json()
            if (result.status === 'success') {
                alert(t('alert.restoreSuccess'))
                location.reload()
            }
        } catch (error) {
            alert(t('alert.restoreFailedPrefix') + error)
        }
    }
}

async function restoreToVersion(backupPath) {
    if (confirm(t('runtime.restoreVersionConfirm') + '\n' + backupPath)) {
        try {
            const response = await fetch('/restore_file', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ file_path: backupPath })
            })
            const result = await response.json()
            if (result.status === 'success') {
                alert(t('alert.restoreSuccess'))
                location.reload()
            }
        } catch (error) {
            alert(t('alert.restoreFailedPrefix') + error)
        }
    }
}

function checkFileExtension() {
    const lyricsPath = document.getElementById('lyricsPath').value;
    const mergeLQEButton = document.getElementById('mergeLQEButton');
    if (lyricsPath && !lyricsPath.toLowerCase().endsWith('.ttml')) {
        mergeLQEButton.style.display = 'inline-block';
    } else {
        mergeLQEButton.style.display = 'none';
    }
}

function mergeToLQE() {
    const lyricsPath = document.getElementById('lyricsPath').value;
    const translationPath = document.getElementById('translationPath').value;
    
    if (!lyricsPath || !translationPath) {
        alert(t('alert.ensurePathsSet'));
        return;
    }

    fetch('/merge_to_lqe', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            lyricsPath: lyricsPath,
            translationPath: translationPath
        })
    })
    .then(response => response.json())
    .then(data => {
        if (data.status === 'success') {
            // 自动下载为.lqe文件
            const blob = new Blob([data.content], { type: 'text/plain' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            // 文件名可根据歌词文件名自动生成
            let baseName = lyricsPath.replace(/\.[^/.]+$/, '');
            a.download = baseName + '.lqe';
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
            alert(t('alert.lqeExported'));
        } else {
            alert(t('alert.mergeFailedPrefix') + data.message);
        }
    })
    .catch(error => {
        alert(t('alert.mergeFailedPrefix') + error);
    });
}

// 在更新路径时检查文件扩展名
const originalUpdateLyricsPath = updateLyricsPath;
updateLyricsPath = function(index) {
    originalUpdateLyricsPath(index);
    setTimeout(checkFileExtension, 100); // 延迟检查以确保路径已更新
};

// 页面加载时检查一次
window.addEventListener('DOMContentLoaded', function() {
    checkFileExtension();
});
// 歌词路径输入变化时检查
document.getElementById('lyricsPath').addEventListener('input', checkFileExtension);

function detectLyricsExtension(content = '') {
    const text = (content || '').trim();
    if (!text) {
        return '';
    }
    if (/<tt\b|<\/tt>/i.test(text)) {
        return '.ttml';
    }
    if (/\[\d{1,2}:\d{2}(?:\.\d{1,3})?\]/.test(text)) {
        return '.lrc';
    }
    if (/\[\d+\s*,\s*\d+\]/.test(text)) {
        return '.qrc';
    }
    if (/\[\d+\]/.test(text)) {
        return '.lys';
    }
    return '';
}

async function ensureLysContentForProcessing(actionLabel = '处理') {
    const lyricsEditor = document.getElementById('lyricsEditor');
    const lyricsPathInput = document.getElementById('lyricsPath');

    const lyricsContent = lyricsEditor ? lyricsEditor.value : '';
    const lyricsPath = lyricsPathInput ? lyricsPathInput.value : '';

    const pathExtension = (lyricsPath.match(/\.[^.]+$/) || [])[0]?.toLowerCase() || '';
    const contentExtension = detectLyricsExtension(lyricsContent);
    const shouldConvert = pathExtension === '.ttml' && contentExtension === '.ttml';

    if (!shouldConvert) {
        return { content: lyricsContent, path: lyricsPath, converted: false };
    }

    const relativePath = stripSongsPrefix(lyricsPath).trim();
    if (!relativePath) {
        throw new Error('检测到TTML歌词，但文件路径为空，无法转换');
    }

    try {
        const convertResp = await fetch('/convert_ttml_by_path', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: relativePath })
        });
        const convertData = await convertResp.json();
        if (convertData.status !== 'success' || !convertData.lyricPath) {
            throw new Error(convertData.message || 'TTML转换失败');
        }

        const lysResp = await fetch('/get_lyrics', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: convertData.lyricPath })
        });
        const lysData = await lysResp.json();
        if (lysData.status !== 'success' || !lysData.content) {
            throw new Error(lysData.message || '无法读取转换后的歌词');
        }

        console.log(`[TTML] ${actionLabel}前已临时转换为LYS`, {
            path: convertData.lyricPath
        });

        return {
            content: lysData.content,
            path: stripSongsPrefix(convertData.lyricPath),
            rawPath: convertData.lyricPath,
            converted: true
        };
    } catch (error) {
        console.error('TTML临时转换失败：', error);
        const message = error && error.message ? error.message : 'TTML临时转换失败';
        throw new Error(`检测到TTML歌词，${actionLabel}前转换失败：${message}`);
    }
}

function stripBracketCharacters(text = '') {
    if (!text) {
        return '';
    }
    return text
        .replace(/[()（）\[\]【】]/g, '')
        .replace(/\s{2,}/g, ' ')
        .trim();
}

function isFullyWrappedByOuterBrackets(text = '') {
    const stripped = String(text || '').trim();
    if (stripped.length < 2) {
        return false;
    }
    const pairMap = { '(': ')', '（': '）' };
    const opening = stripped[0];
    const closing = pairMap[opening];
    if (!closing || stripped[stripped.length - 1] !== closing) {
        return false;
    }
    let depth = 0;
    for (let i = 0; i < stripped.length; i += 1) {
        const ch = stripped[i];
        if (ch === opening) {
            depth += 1;
        } else if (ch === closing) {
            depth -= 1;
            if (depth < 0) {
                return false;
            }
            if (depth === 0 && i !== stripped.length - 1) {
                return false;
            }
        }
    }
    if (depth !== 0) {
        return false;
    }
    return stripped.slice(1, -1).trim().length > 0;
}

function stripOuterBracketsIfFullLine(text = '') {
    const stripped = String(text || '').trim();
    if (!isFullyWrappedByOuterBrackets(stripped)) {
        return stripped;
    }
    return stripped.slice(1, -1).trim();
}

function preprocessLyricsLinesForPrompt(
    rawContent = '',
    stripBracketsEnabled = false,
    experimentalFullLineBracketStrip = false,
    experimentalBracketLineAsSubline = false
) {
    const lines = (rawContent || '').split('\n');
    const structured = [];
    let mainIndex = 0;
    let activeMainIndex = 0;
    const sublineCounter = new Map();
    for (const line of lines) {
        const rawLine = String(line ?? '');
        const tagMatch = rawLine.match(/^\s*\[(\d+)\]/);
        const lineTag = tagMatch ? tagMatch[1] : '';
        const isTagSublineCandidate = lineTag === '6' || lineTag === '7' || lineTag === '8';
        let text = rawLine.replace(/\[.*?\]/g, '');
        text = text.replace(/\(\d+,\d+\)/g, '');
        text = text.trim();
        if (!text) {
            continue;
        }
        const isFullLineBracket = isFullyWrappedByOuterBrackets(text);
        if (stripBracketsEnabled) {
            text = stripBracketCharacters(text);
        }
        if (isFullLineBracket && (experimentalFullLineBracketStrip || experimentalBracketLineAsSubline)) {
            text = stripOuterBracketsIfFullLine(text);
        }
        if (text) {
            let isSubline = false;
            let subIndex = 0;
            let currentMainIndex;
            const isSublineCandidate = isFullLineBracket || isTagSublineCandidate;
            if (experimentalBracketLineAsSubline && isSublineCandidate && activeMainIndex > 0) {
                isSubline = true;
                currentMainIndex = activeMainIndex;
                subIndex = (sublineCounter.get(currentMainIndex) || 0) + 1;
                sublineCounter.set(currentMainIndex, subIndex);
            } else {
                mainIndex += 1;
                activeMainIndex = mainIndex;
                currentMainIndex = mainIndex;
            }
            const displayIndex = isSubline ? `${currentMainIndex}_${subIndex}` : String(currentMainIndex);
            structured.push({
                display_index: displayIndex,
                text,
                parent_index: isSubline ? String(currentMainIndex) : '',
                is_subline: isSubline,
                is_full_line_bracket: isFullLineBracket
            });
        }
    }
    return structured;
}

function setCurrentFileNameAsLyrics() {
    const currentFileName = document.getElementById('currentFileName').textContent;
    const lyricsPathInput = document.getElementById('lyricsPath');
    const lyricsContent = document.getElementById('lyricsEditor').value;

    const existingExtension = (lyricsPathInput.value.match(/\.[^.]+$/) || [])[0] || '';
    let extension = detectLyricsExtension(lyricsContent) || existingExtension || '.lys';

    if (!extension.startsWith('.')) {
        extension = `.${extension}`;
    }

    lyricsPathInput.value = currentFileName + extension;
    checkFileExtension();
}

function setCurrentFileNameAsTranslation() {
    const currentFileName = document.getElementById('currentFileName').textContent;
    const translationPathInput = document.getElementById('translationPath');
    const translationContent = document.getElementById('translationEditor').value;

    let extension = detectLyricsExtension(translationContent) || '.lrc';
    if (extension === '.lys') {
        // 翻译默认使用逐行格式
        extension = '.lrc';
    }
    if (!extension.startsWith('.')) {
        extension = `.${extension}`;
    }

    translationPathInput.value = currentFileName + extension;
}

function showSoftNotice(message) {
    let notice = document.getElementById('softNotice');
    if (!notice) {
        notice = document.createElement('div');
        notice.id = 'softNotice';
        notice.className = 'soft-notice';
        document.body.appendChild(notice);
    }
    notice.textContent = message;
    notice.classList.add('is-visible');
    clearTimeout(notice._hideTimer);
    notice._hideTimer = setTimeout(() => {
        notice.classList.remove('is-visible');
    }, 1200);
}

function copyFileNameText(textEl) {
    const fileName = textEl.textContent.trim();
    navigator.clipboard.writeText(fileName).then(() => {
        showSoftNotice(t('runtime.copiedFilename'));
    });
}

function copyCurrentFileName() {
    const fileName = document.getElementById('currentFileName').textContent;
    const button = document.querySelector('#lyricsModal .copy-btn');
    if (!button) {
        navigator.clipboard.writeText(fileName);
        return;
    }
    navigator.clipboard.writeText(fileName).then(() => {
        const originalText = button.textContent;
        button.textContent = '✓';
        setTimeout(() => {
            button.textContent = originalText;
        }, 1000);
    });
}

async function extractTimestamps() {
    const lyricsPath = document.getElementById('lyricsPath').value;
    const lyricsContent = document.getElementById('lyricsEditor').value;

    console.log('开始提取时间戳...');
    console.log('歌词路径:', lyricsPath);
    console.log('歌词内容长度:', lyricsContent.length);

    const allowedExtensions = ['.lys', '.qrc', '.lrc'];
    const pathExtension = (lyricsPath.match(/\.[^.]+$/) || [])[0]?.toLowerCase() || '';
    const detectedExtension = detectLyricsExtension(lyricsContent);
    let resolvedExtension = allowedExtensions.includes(pathExtension)
        ? pathExtension
        : (allowedExtensions.includes(detectedExtension) ? detectedExtension : '');
    let contentForExtraction = lyricsContent;

    const isTtml = pathExtension === '.ttml' && detectedExtension === '.ttml';
    if (isTtml) {
        try {
            const conversion = await ensureLysContentForProcessing('提取时间戳');
            contentForExtraction = conversion.content;
            resolvedExtension = '.lys';
            console.log('检测到TTML歌词，已临时转换为LYS用于时间戳提取');
        } catch (error) {
            console.error('TTML转换失败，无法提取时间戳：', error);
            alert(error.message || t('runtime.ttmlPathEmpty'));
            return;
        }
    } else if (!resolvedExtension) {
        console.error('文件格式错误：不支持的歌词格式');
        alert(t('alert.selectLysQrcLrc'));
        return;
    }

    try {
        console.log('正在发送请求到服务器...');
        const response = await fetch('/extract_timestamps', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                content: contentForExtraction
            })
        });
        
        console.log('服务器响应状态:', response.status);
        const data = await response.json();
        console.log('服务器返回数据:', data);
        
        if (data.status === 'success') {
            console.log('成功获取时间戳，数量:', data.timestamps.length);
            const translationEditor = document.getElementById('translationEditor');
            const rawTranslation = translationEditor.value || '';
            const translationLines = rawTranslation
                .split(/\r?\n/)
                .map(line => line.replace(/^\s*\d+(?:_\d+)?[\.、．:：]?\s*/, '').trim())
                .filter(line => line.length > 0);

            if (translationLines.length === data.timestamps.length && translationLines.length > 0) {
                const merged = data.timestamps.map((ts, idx) => `${ts}${translationLines[idx] ? ' ' + translationLines[idx] : ''}`);
                translationEditor.value = merged.join('\n');
                console.log('时间戳已与翻译逐行合并');
            } else {
                // 行数不一致时维持旧行为，仅输出时间戳供用户手动粘贴
                translationEditor.value = data.timestamps.join('\n');
                console.warn(`翻译行数(${translationLines.length})与时间戳(${data.timestamps.length})不一致，已仅写入时间戳`);
            }
        } else {
            console.error('提取时间戳失败:', data.message);
            alert(t('alert.extractTimestampFailedPrefix') + data.message);
        }
    } catch (error) {
        console.error('提取时间戳时出错：', error);
        console.error('错误详情:', {
            name: error.name,
            message: error.message,
            stack: error.stack
        });
        alert(t('alert.extractTimestampError'));
    }
}

async function extractLyrics() {
    const lyricsContent = document.getElementById('lyricsEditor').value;
    
    try {
        const response = await fetch('/extract_lyrics', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                content: lyricsContent
            })
        });
        
        const data = await response.json();
        
        if (data.status === 'success') {
            // 将提取的歌词复制到剪贴板
            await navigator.clipboard.writeText(data.content);
            alert(t('alert.extractLyricsSuccess'));
        } else {
            alert(t('alert.extractLyricsFailedPrefix') + data.message);
        }
    } catch (error) {
        console.error('提取歌词时出错：', error);
        alert(t('alert.extractLyricsError'));
    }
}

function updateTranslationStatus(message, type = 'info', suspectLines = []) {
    const statusEl = document.getElementById('translationStatusMessage');
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
        suspectLines.slice(0, 5).forEach(item => {
            const li = document.createElement('li');
            li.textContent = t('runtime.suspectLine', {line: item.line_number, content: item.line_content});
            list.appendChild(li);
        });
        statusEl.appendChild(list);

        if (suspectLines.length > 5) {
            const extra = document.createElement('div');
            extra.textContent = t('runtime.moreSuspectLines', {count: suspectLines.length - 5});
            extra.style.marginTop = '4px';
            statusEl.appendChild(extra);
        }
    };

    if (isProgressMessage) {
        const stages = Array.isArray(message.stages) ? message.stages : [];
        const activeStage = stages.find(stage => stage && stage.state === 'active');
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

        if (stages.length > 0) {
            const list = document.createElement('ul');
            list.className = 'status-progress';
            stages.forEach(stage => {
                if (!stage) {
                    return;
                }
                const li = document.createElement('li');
                const stateClass = stage.state ? `status-progress__item--${stage.state}` : 'status-progress__item--pending';
                li.className = `status-progress__item ${stateClass}`;

                const label = document.createElement('span');
                label.className = 'status-progress__label';
                label.textContent = stage.label || stage.key || '';
                li.appendChild(label);

                if (stage.description) {
                    const desc = document.createElement('div');
                    desc.className = 'status-progress__desc';
                    desc.textContent = stage.description;
                    li.appendChild(desc);
                }

                list.appendChild(li);
            });
            statusEl.appendChild(list);
        }

        if (message.details && Array.isArray(message.details)) {
            message.details.forEach(detail => {
                const detailBlock = document.createElement('div');
                detailBlock.textContent = detail;
                statusEl.appendChild(detailBlock);
            });
        }

        appendSuspectLines();
    } else {
        const isTranslating = type === 'info' && typeof message === 'string' && (message.includes('正在翻译') || message.includes('Translating') || message.includes('translating'));

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

function highlightSuspectLinesInEditor(suspectLines = []) {
    const editor = document.getElementById('lyricsEditor');
    if (!editor) {
        return;
    }

    if (!Array.isArray(suspectLines) || suspectLines.length === 0) {
        editor.classList.remove('error-highlight');
        return;
    }

    editor.classList.add('error-highlight');
    const targetLine = suspectLines[0]?.line_number;
    if (!targetLine || targetLine < 1) {
        return;
    }

    const lines = editor.value.split('\n');
    if (targetLine > lines.length) {
        return;
    }

    let start = 0;
    for (let i = 0; i < targetLine - 1; i++) {
        start += lines[i].length + 1;
    }
    const end = start + lines[targetLine - 1].length;

    editor.focus();
    if (typeof editor.setSelectionRange === 'function') {
        editor.setSelectionRange(start, end);
    }

    const approxLineHeight = editor.scrollHeight / Math.max(lines.length, 1);
    editor.scrollTop = Math.max(0, approxLineHeight * (targetLine - 3));
}

async function translateLyrics() {
    const translateBtn = document.querySelector('button[onclick="translateLyrics()"]');
    const originalBtnText = translateBtn.textContent;
    highlightSuspectLinesInEditor([]);
    updateTranslationStatus('');
    lastThinkingSummary = '';

    const translationStages = [
        { key: 'lyricsPrep', label: t('translate.stage.lyricsPrep'), state: 'active', description: t('translate.checkingLyrics') },
        { key: 'thinkingRequest', label: t('translate.stage.thinkingRequest'), state: 'pending' },
        { key: 'thinkingOutput', label: t('translate.stage.thinkingOutput'), state: 'pending' },
        { key: 'translationRequest', label: t('translate.stage.translationRequest'), state: 'pending' },
        { key: 'translationOutput', label: t('translate.stage.translationOutput'), state: 'pending' },
        { key: 'postProcessing', label: t('translate.stage.postProcessing'), state: 'pending' }
    ];
    const stageMap = new Map(translationStages.map(stage => [stage.key, stage]));
    const getActiveStage = () => translationStages.find(stage => stage.state === 'active');
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
                const errorStage = translationStages.find(stage => stage.state === 'error');
                summaryText = errorStage ? `${errorStage.label}${t('translate.stageError')}` : t('translate.failed');
            } else {
                summaryText = t('translate.status');
            }
        }

        updateTranslationStatus({
            summary: summaryText,
            stages: translationStages.map(stage => ({
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
        translationStages.forEach(item => {
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
        const hasOptions = options && (Array.isArray(options.suspectLines) && options.suspectLines.length > 0 || options.useShine !== undefined);
        if (!stageDirty && !summary && statusType === 'info' && !hasOptions) {
            return;
        }
        renderStages(summary, statusType, options || {});
    };

    renderStages(t('batch.preparingTranslation'));

    try {
        const lyricsPath = document.getElementById('lyricsPath').value;
        const lyricsContent = document.getElementById('lyricsEditor').value;
        if (!lyricsContent) {
            markDirty(failStage('lyricsPrep', t('batch.lyricsEmpty')));
            flushStages(t('batch.lyricsEmpty'), 'error', { useShine: false });
            alert(t('alert.enterLyricsFirst'));
            return;
        }

        const pathExtension = (lyricsPath.match(/\.[^.]+$/) || [])[0]?.toLowerCase() || '';
        const detectedExtension = detectLyricsExtension(lyricsContent);
        let processedLyricsContent = lyricsContent;

        if (pathExtension === '.ttml' && detectedExtension === '.ttml') {
            markDirty(updateStageDescription('lyricsPrep', t('batch.detectedTtmlConverting')));
            flushStages();
            const conversion = await ensureLysContentForProcessing('翻译');
            processedLyricsContent = conversion.content;
            markDirty(updateStageDescription('lyricsPrep', t('batch.complete')));
        }

        console.log('开始翻译过程...');
        console.log('歌词内容长度:', processedLyricsContent.length);
        const lyricLines = processedLyricsContent.split('\n').length;
        console.log('歌词行数:', lyricLines);

        translateBtn.textContent = t('status.translating');
        translateBtn.disabled = true;

        markDirty(completeStage('lyricsPrep', t('status.readLines', {count: lyricLines})));

        const translationEditor = document.getElementById('translationEditor');
        translationEditor.value = '';

        console.log('发送翻译请求到服务器...');
        const systemPrompt = localStorage.getItem('aiSystemPrompt');
        const provider = localStorage.getItem('aiProvider') || 'deepseek';
        const baseUrl = localStorage.getItem('aiBaseUrl') || 'https://api.deepseek.com';
        const model = localStorage.getItem('aiModel') || 'deepseek-reasoner';
        const expectReasoning = localStorage.getItem('aiExpectReasoning') === 'true';
        const compatModeStorage = localStorage.getItem('aiCompatMode');
        const compatMode = compatModeStorage ? compatModeStorage === 'true' : false;
        const thinkingEnabledStorage = localStorage.getItem('aiThinkingEnabled');
        const thinkingEnabled = thinkingEnabledStorage ? thinkingEnabledStorage === 'true' : true;
        const thinkingProviderStored = localStorage.getItem('aiThinkingProvider');
        const thinkingBaseUrlStored = localStorage.getItem('aiThinkingBaseUrl');
        const thinkingModelStored = localStorage.getItem('aiThinkingModel');
        const thinkingPrompt = localStorage.getItem('aiThinkingPrompt');
        const stripBracketsStored = localStorage.getItem('aiStripBrackets');
        const thinkingProvider = thinkingProviderStored && thinkingProviderStored.length > 0 ? thinkingProviderStored : provider;
        const thinkingBaseUrl = thinkingBaseUrlStored && thinkingBaseUrlStored.length > 0 ? thinkingBaseUrlStored : baseUrl;
        const thinkingModel = thinkingModelStored && thinkingModelStored.length > 0 ? thinkingModelStored : model;
        const stripBrackets = stripBracketsStored ? stripBracketsStored === 'true' : false;
        const experimentalFullLineBracketStripStored = localStorage.getItem('aiExperimentalFullLineBracketStrip');
        const experimentalBracketLineAsSublineStored = localStorage.getItem('aiExperimentalBracketLineAsSubline');
        const experimentalFullLineBracketStrip = experimentalFullLineBracketStripStored ? experimentalFullLineBracketStripStored === 'true' : false;
        const experimentalBracketLineAsSubline = experimentalBracketLineAsSublineStored ? experimentalBracketLineAsSublineStored === 'true' : false;

        // 获取并追加附加提示词
        const extraTranslationPrompt = getExtraTranslationPrompt();
        const extraThinkingPrompt = getExtraThinkingPrompt();
        let finalSystemPrompt = systemPrompt;
        let finalThinkingPrompt = thinkingPrompt;

        // 追加翻译附加提示词
        if (extraTranslationPrompt) {
            if (finalSystemPrompt) {
                finalSystemPrompt = finalSystemPrompt + '\n\n' + extraTranslationPrompt;
            } else {
                finalSystemPrompt = extraTranslationPrompt;
            }
        }

        // 追加思考附加提示词
        if (extraThinkingPrompt) {
            if (finalThinkingPrompt) {
                finalThinkingPrompt = finalThinkingPrompt + '\n\n' + extraThinkingPrompt;
            } else {
                finalThinkingPrompt = extraThinkingPrompt;
            }
        }

        let thinkingRequestAcknowledged = !thinkingEnabled;
        let thinkingOutputCompleted = !thinkingEnabled;
        let translationRequestActivated = false;
        let translationRequestAcknowledged = false;
        let translationOutputActivated = false;
        let postProcessingActivated = false;

        if (thinkingEnabled) {
            markDirty(activateStage('thinkingRequest', t('batch.prepareThinking', {p: thinkingProvider, m: thinkingModel})));
        } else {
            markDirty(completeStage('thinkingRequest', t('batch.skipHasTranslation')));
            markDirty(completeStage('thinkingOutput', t('batch.skipHasTranslation')));
            markDirty(activateStage('translationRequest', t('batch.prepareTranslation', {p: provider, m: model})));
            thinkingOutputCompleted = true;
            translationRequestActivated = true;
        }

        flushStages();

        const response = await fetch('/translate_lyrics', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                content: processedLyricsContent,
                translation: {
                    provider,
                    base_url: baseUrl,
                    model,
                    system_prompt: finalSystemPrompt,
                    expect_reasoning: expectReasoning,
                    compat_mode: compatMode,
                    strip_brackets: stripBrackets,
                    experimental_full_line_bracket_strip: experimentalFullLineBracketStrip,
                    experimental_bracket_line_as_subline: experimentalBracketLineAsSubline
                },
                thinking: {
                    enabled: thinkingEnabled,
                    provider: thinkingProvider,
                    base_url: thinkingBaseUrl,
                    model: thinkingModel,
                    system_prompt: finalThinkingPrompt
                },
                compat_mode: compatMode,
                thinking_enabled: thinkingEnabled,
                strip_brackets: stripBrackets,
                experimental_full_line_bracket_strip: experimentalFullLineBracketStrip,
                experimental_bracket_line_as_subline: experimentalBracketLineAsSubline
            })
        });

        console.log('开始接收流式响应...');
        const contentType = response.headers.get('Content-Type') || '';
        if (!response.ok || contentType.includes('application/json')) {
            let errorData = {};
            try {
                errorData = await response.json();
            } catch (parseError) {
                console.error('解析错误响应失败:', parseError);
            }

            const errorMessage = errorData.message || `翻译失败（HTTP ${response.status}）`;
            const activeStage = getActiveStage();
            if (activeStage) {
                markDirty(failStage(activeStage.key, t('batch.translationErrorMsg', {msg: errorMessage})));
            } else {
                markDirty(failStage('translationRequest', t('batch.translationErrorMsg', {msg: errorMessage})));
            }
            flushStages(errorMessage, 'error', { useShine: false, suspectLines: errorData.suspectLines });
            highlightSuspectLinesInEditor(errorData.suspectLines || []);
            if (!errorData.suspectLines || errorData.suspectLines.length === 0) {
                alert(errorMessage);
            }
            return;
        }

        if (!response.body || !response.body.getReader) {
            markDirty(failStage('translationRequest', t('batch.noStreamSupport')));
            flushStages(t('batch.noStreamSupport'), 'error', { useShine: false });
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

        const translationEditorUpdater = () => {
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
            translationEditor.value = sections.join('\n\n');
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
                    lastThinkingSummary = thinkingContent;
                    translationEditorUpdater();
                    if (!thinkingOutputCompleted) {
                        markDirty(completeStage('thinkingOutput', t('batch.complete')));
                        markDirty(activateStage('translationRequest', t('batch.prepareTranslation', {p: provider, m: model})));
                        thinkingOutputCompleted = true;
                            translationRequestActivated = true;
                        }
                    } else if (content.error) {
                        thinkingContent = '思考模型调用失败：' + content.error;
                        translationEditorUpdater();
                        markDirty(failStage('thinkingOutput', t('batch.thinkingFailed')));
                        if (!translationRequestActivated) {
                            markDirty(activateStage('translationRequest', t('batch.prepareTranslation', {p: provider, m: model})));
                            translationRequestActivated = true;
                        }
                        thinkingOutputCompleted = true;
                    } else {
                        translationEditorUpdater();
                    }
                } catch (e) {
                    console.error('解析思考内容时出错:', e);
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
                        translationEditorUpdater();
                        if (!thinkingOutputCompleted) {
                            markDirty(updateStageDescription('thinkingOutput', t('batch.pendingThinking')));
                        }
                        lastThinkingSummary = thinkingContent || reasoningContent || lastThinkingSummary;
                    }
                } catch (e) {
                    console.error('解析思维链内容时出错:', e);
                }

                flushStages();
                return;
            }

            if (line.startsWith('content:')) {
                try {
                    const content = JSON.parse(line.slice(8));
                    if (Object.prototype.hasOwnProperty.call(content, 'hasTimestamps')) {
                        translationHasTimestamps = content.hasTimestamps;
                    }

                    if (!translationRequestActivated) {
                        markDirty(activateStage('translationRequest', t('batch.prepareTranslation', {p: provider, m: model})));
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
                        translationEditorUpdater();
                    }
                } catch (e) {
                    console.error('解析翻译内容时出错:', e);
                }

                flushStages();
                return;
            }
        };

        while (true) {
            const { done, value } = await reader.read();
            if (done) {
                break;
            }

            const chunk = decoder.decode(value, { stream: true });
            buffer += chunk;

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
            const remainingLines = buffer.split('\n');
            for (const line of remainingLines) {
                processLine(line);
            }
        }

        markDirty(completeStage('translationRequest', t('batch.flowEnded')));
        markDirty(completeStage('translationOutput', translationReceived ? t('batch.receivedTranslation') : t('batch.translationEmpty')));
        if (!postProcessingActivated) {
            markDirty(activateStage('postProcessing', t('batch.writingTranslation')));
            postProcessingActivated = true;
        }
        markDirty(completeStage('postProcessing', translationReceived ? t('batch.translationWritten') : t('batch.translationEmpty')));
        flushStages();

        const successMessage = (!translationHasTimestamps && translationReceived)
            ? t('batch.completeNoTimestamp')
            : t('batch.complete');

        flushStages(successMessage, 'success', { useShine: false });
        highlightSuspectLinesInEditor([]);
        console.log('翻译完成');

    } catch (error) {
        console.error('翻译出错：', error);
        console.error('错误详情:', {
            name: error.name,
            message: error.message,
            stack: error.stack
        });
        const fallbackMessage = error && error.message ? error.message : '翻译过程中出现错误，请检查控制台日志。';
        const activeStage = getActiveStage();
        if (activeStage) {
            markDirty(failStage(activeStage.key, t('batch.translationError')));
        } else {
            markDirty(failStage('translationOutput', t('batch.translationError')));
        }
        flushStages(t('batch.translationFailedMsg', {msg: fallbackMessage}), 'error', { useShine: false });
        highlightSuspectLinesInEditor([]);
        alert(t('alert.translateError'));
    } finally {
        translateBtn.textContent = originalBtnText;
        translateBtn.disabled = false;
    }
}

async function copyTranslationPrompt() {
    const copyBtn = document.querySelector('button[onclick="copyTranslationPrompt()"]');
    const originalText = copyBtn ? copyBtn.textContent : '';
    if (copyBtn) {
        copyBtn.textContent = t('status.copying');
        copyBtn.disabled = true;
    }

    const resetButton = () => {
        if (copyBtn) {
            copyBtn.textContent = originalText || '📋 复制提示词';
            copyBtn.disabled = false;
        }
    };

    try {
        const lyricsEditor = document.getElementById('lyricsEditor');
        if (!lyricsEditor || !lyricsEditor.value.trim()) {
            alert(t('alert.enterLyricsFirst'));
            resetButton();
            return;
        }

        const lyricsPathInput = document.getElementById('lyricsPath');
        const lyricsPath = lyricsPathInput ? lyricsPathInput.value : '';
        const pathExtension = (lyricsPath.match(/\.[^.]+$/) || [])[0]?.toLowerCase() || '';
        const detectedExtension = detectLyricsExtension(lyricsEditor.value);

        let processedLyricsContent = lyricsEditor.value;
        if (pathExtension === '.ttml' && detectedExtension === '.ttml') {
            const conversion = await ensureLysContentForProcessing('复制提示词');
            processedLyricsContent = conversion.content;
        }

        const stripBracketsStored = localStorage.getItem('aiStripBrackets');
        const experimentalFullLineBracketStripStored = localStorage.getItem('aiExperimentalFullLineBracketStrip');
        const experimentalBracketLineAsSublineStored = localStorage.getItem('aiExperimentalBracketLineAsSubline');
        const stripBrackets = stripBracketsStored ? stripBracketsStored === 'true' : false;
        const experimentalFullLineBracketStrip = experimentalFullLineBracketStripStored ? experimentalFullLineBracketStripStored === 'true' : false;
        const experimentalBracketLineAsSubline = experimentalBracketLineAsSublineStored ? experimentalBracketLineAsSublineStored === 'true' : false;
        const processedLines = preprocessLyricsLinesForPrompt(
            processedLyricsContent,
            stripBrackets,
            experimentalFullLineBracketStrip,
            experimentalBracketLineAsSubline
        );
        if (!processedLines.length) {
            alert(t('alert.noLyricsContent'));
            resetButton();
            return;
        }

        const numberedLyrics = processedLines.map(line => `${line.display_index}.${line.text}`).join('\n');
        const hasSublines = processedLines.some(line => line.is_subline);
        const thinkingBlock = lastThinkingSummary ? `歌曲理解：\n${lastThinkingSummary}` : '';

        let systemPrompt = localStorage.getItem('aiSystemPrompt') || '';
        const compatModeStorage = localStorage.getItem('aiCompatMode');
        const compatMode = compatModeStorage ? compatModeStorage === 'true' : false;
        const thinkingEnabledStorage = localStorage.getItem('aiThinkingEnabled');
        const thinkingEnabled = thinkingEnabledStorage ? thinkingEnabledStorage === 'true' : true;
        const thinkingPromptStored = localStorage.getItem('aiThinkingPrompt') || '';
        const thinkingPrompt = thinkingPromptStored.trim().length > 0
            ? thinkingPromptStored.trim()
            : '先用你的母语对整首歌进行深入理解，归纳情节、情绪、人物和隐喻，再据此翻译歌词，保持逐行对应。';

        let finalPrompt = '';
        const promptSections = [];
        if (compatMode) {
            if (systemPrompt && systemPrompt.trim()) {
                promptSections.push(systemPrompt.trim());
            }
            if (thinkingEnabled) {
                promptSections.push(thinkingPrompt);
            }
            if (thinkingBlock) {
                promptSections.push(thinkingBlock);
            }
            if (thinkingEnabled) {
                promptSections.push('请先完成思考阶段，再进行逐行翻译。');
            }
            if (hasSublines) {
                promptSections.push('形如 N_1、N_2 的编号表示它们从属于主句 N。这些从句可能来自整句括号行，或来自原歌词中的 [6]、[7]、[8] 标签行。请保持这种主从关系输出，不要把子句改成新的主编号。');
            }
            promptSections.push(`待翻译歌词：\n${numberedLyrics}`);
            finalPrompt = promptSections.filter(Boolean).join('\n\n');
        } else {
            if (systemPrompt && systemPrompt.trim()) {
                promptSections.push(systemPrompt.trim());
            }
            if (thinkingEnabled) {
                promptSections.push(thinkingPrompt);
            }
            if (thinkingBlock) {
                promptSections.push(thinkingBlock);
            }
            if (thinkingEnabled) {
                promptSections.push('请先完成思考阶段，再进行逐行翻译。');
            }
            if (hasSublines) {
                promptSections.push('形如 N_1、N_2 的编号表示它们从属于主句 N。这些从句可能来自整句括号行，或来自原歌词中的 [6]、[7]、[8] 标签行。请保持这种主从关系输出，不要把子句改成新的主编号。');
            }
            promptSections.push(`待翻译歌词：\n${numberedLyrics}`);
            finalPrompt = promptSections.filter(Boolean).join('\n\n');
        }

        await navigator.clipboard.writeText(finalPrompt);
        if (copyBtn) {
            copyBtn.textContent = t('status.copied');
            setTimeout(resetButton, 1000);
        } else {
            alert(t('alert.promptCopied'));
        }
    } catch (error) {
        console.error('复制提示词失败：', error);
        alert(error.message || t('alert.copyFailedWithMsg') + t('runtime.connectionTestError'));
        resetButton();
    }
}

async function showAISettings() {
    try {
        await refreshAiPresetCache();
        const response = await fetch('/get_ai_settings');
        const data = await response.json();
        if (data.status !== 'success') {
            throw new Error(data.message || t('alert.getAISettingsFailed'));
        }

        const backendSettings = data.effective_settings || data.settings || {};
        aiPresetPermissions = data.permissions || aiPresetPermissions || {};
        const translation = backendSettings.translation || {};
        const thinking = backendSettings.thinking || {};
        const romanization = backendSettings.romanization || {};
        const effectiveSettings = {
            translation: {
                provider: translation.provider !== undefined ? translation.provider : 'deepseek',
                base_url: translation.base_url !== undefined ? translation.base_url : 'https://api.deepseek.com',
                model: translation.model !== undefined ? translation.model : 'deepseek-reasoner',
                api_key: '',
                system_prompt: translation.system_prompt !== undefined ? translation.system_prompt : '',
                expect_reasoning: translation.expect_reasoning !== undefined ? translation.expect_reasoning : true,
                compat_mode: translation.compat_mode !== undefined ? translation.compat_mode : false,
                strip_brackets: translation.strip_brackets !== undefined ? translation.strip_brackets : false,
                experimental_full_line_bracket_strip: translation.experimental_full_line_bracket_strip !== undefined ? translation.experimental_full_line_bracket_strip : false,
                experimental_bracket_line_as_subline: translation.experimental_bracket_line_as_subline !== undefined ? translation.experimental_bracket_line_as_subline : false
            },
            thinking: {
                enabled: thinking.enabled !== undefined ? thinking.enabled : true,
                provider: thinking.provider !== undefined ? thinking.provider : (translation.provider !== undefined ? translation.provider : 'deepseek'),
                base_url: thinking.base_url !== undefined ? thinking.base_url : (translation.base_url !== undefined ? translation.base_url : ''),
                model: thinking.model !== undefined ? thinking.model : (translation.model !== undefined ? translation.model : ''),
                api_key: '',
                system_prompt: thinking.system_prompt !== undefined ? thinking.system_prompt : ''
            },
            romanization: {
                system_prompt: romanization.system_prompt !== undefined ? romanization.system_prompt : '',
                alignment_mode: (() => {
                    const v = String(romanization.alignment_mode || '').trim().toLowerCase();
                    return v === 'separator_tokens' ? 'separator_tokens' : 'indexed_tokens';
                })(),
                separator: romanization.separator !== undefined ? romanization.separator : ';',
                strict_token_count: romanization.strict_token_count !== undefined ? Boolean(romanization.strict_token_count) : true,
                require_trailing_separator: romanization.require_trailing_separator !== undefined ? Boolean(romanization.require_trailing_separator) : true
            }
        };

        // sync current source binding from backend
        const sourceMode = String(data.source_mode || 'manual').trim().toLowerCase();
        const sourcePresetId = String(data.source_preset_id || '').trim();
        const sourcePresetName = String((data.source_preset || {}).name || '').trim();
        let syncedSource = {
            mode: (sourceMode === 'preset' && sourcePresetId) ? 'preset' : 'manual',
            preset_id: sourcePresetId,
            preset_name: sourcePresetName
        };
        // extra kind/label
        if (data.source_kind || data.source_label) {
            syncedSource = {
                mode: (sourceMode === 'preset' && sourcePresetId) ? 'preset' : 'manual',
                preset_id: sourcePresetId,
                preset_name: sourcePresetName,
                kind: String(data.source_kind || ''),
                label: String(data.source_label || '')
            };
        }
        setAiSettingsSourceSaved(syncedSource);
        setAiSettingsSourceDraft(syncedSource);
        syncActiveAiPresetKeyWithSavedSource(syncedSource);

        fillAIFormState(effectiveSettings);
        writeAIStateToLocalStorage(effectiveSettings, aiPresetPermissions);
        applyAiPresetFieldPermissions(aiPresetPermissions);
        applyAiSettingsButtonPermissions(Boolean(data.can_save_settings ?? data.can_use_ai), Boolean(data.can_edit_preset));
        document.getElementById('aiSettingsModal').style.display = 'block';
        setAiSettingsStatus('idle');
        refreshReasoningControlCapabilityHint().catch(() => {});
        updateAiPresetSelect();
        updateBatchWorkbenchPresetSelect();
        aiSettingsInitialSnapshot = snapshotAiSettingsPreviewState();
        updateAiPresetApplyStatus();
    } catch (error) {
        console.error('获取AI设置失败：', error);
        alert(t('alert.getAISettingsFailed'));
    }
}

function closeAISettings(options = {}) {
    const { force = false } = options || {};
    if (!force && hasPendingAiSettingsPreview()) {
        const shouldClose = confirm(t('aiSettings.discardChangesConfirm'));
        if (!shouldClose) {
            return false;
        }
    }
    document.getElementById('aiSettingsModal').style.display = 'none';
    return true;
}

const ROMANIZATION_STORAGE_KEY = 'lyricsphere_romanization_payload_v1';

function openRomanizationWorkbench() {
    const lyricsEditor = document.getElementById('lyricsEditor');
    const lyricsPathInput = document.getElementById('lyricsPath');
    if (!lyricsEditor) {
        alert(t('alert.enterLyricsFirst'));
        return;
    }
    const sourceContent = lyricsEditor.value || '';
    const lyricsPath = lyricsPathInput ? lyricsPathInput.value : '';
    let detectedFormat = 'lys';
    const pathExt = (lyricsPath.match(/\.[^.]+$/) || [])[0]?.toLowerCase() || '';
    if (pathExt === '.lrc') {
        detectedFormat = 'lrc';
    } else if (pathExt === '.ttml') {
        detectedFormat = 'ttml';
    } else if (pathExt === '.lys') {
        detectedFormat = 'lys';
    } else {
        const c = detectLyricsExtension(sourceContent);
        if (c === '.lrc') detectedFormat = 'lrc';
        else if (c === '.ttml') detectedFormat = 'ttml';
        else if (c === '.lys') detectedFormat = 'lys';
    }
    const fileName = (typeof currentJsonFile === 'string' && currentJsonFile.trim())
        ? currentJsonFile.trim()
        : (lyricsPath.split(/[/\\]/).pop() || '');
    let romanizationPayload = null;
    try {
        if (typeof readAIStateFromLocalStorage === 'function') {
            romanizationPayload = readAIStateFromLocalStorage().romanization || null;
        }
    } catch (_) {
        romanizationPayload = null;
    }
    try {
        sessionStorage.setItem(ROMANIZATION_STORAGE_KEY, JSON.stringify({
            sourceContent,
            lyricsPath,
            currentJsonFile: typeof currentJsonFile !== 'undefined' ? currentJsonFile : '',
            fileName,
            detectedFormat,
            ...(romanizationPayload && typeof romanizationPayload === 'object'
                ? { romanization: romanizationPayload }
                : {}),
            ts: Date.now()
        }));
    } catch (e) {
        alert((e && e.message) || String(e));
        return;
    }
    window.open('/ai-romanization-workbench', '_blank');
}