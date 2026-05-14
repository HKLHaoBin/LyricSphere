// 在页面开始就定义 checkLyrics 函数
function checkLyrics(lyricsPath, filename) {
    fetch('/check_lyrics', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            path: lyricsPath
        })
    })
        .then(response => response.json())
        .then(data => {
            if (data.status === 'success') {
                const tagsContainer = document.getElementById(`tags-${filename}`)
                if (data.hasDuet && tagsContainer && !tagsContainer.querySelector('.duet-tag')) {
                    const duetTag = document.createElement('span')
                    duetTag.className = 'song-tag duet-tag'
                    duetTag.textContent = t('song.tag.duet')
                    tagsContainer.appendChild(duetTag)
                }
                if (data.hasBackgroundVocals && tagsContainer && !tagsContainer.querySelector('.background-vocals-tag')) {
                    const bgTag = document.createElement('span')
                    bgTag.className = 'song-tag background-vocals-tag'
                    bgTag.textContent = t('song.tag.backgroundVocals')
                    tagsContainer.appendChild(bgTag)
                }
                const summary = songSummaryCache.get(filename)
                let summaryChanged = false
                if (summary) {
                    if (data.hasDuet && !summary.hasDuet) {
                        summary.hasDuet = true
                        summaryChanged = true
                    }
                    if (data.hasBackgroundVocals && !summary.hasBackgroundVocals) {
                        summary.hasBackgroundVocals = true
                        summaryChanged = true
                    }
                    if (summaryChanged) {
                        songSummaryCache.set(filename, summary)
                        refreshSongSummarySearchCacheByFilename(filename)
                    }
                }
                const targetItem = songItemByFilename.get(filename)
                if (targetItem) {
                    refreshSongItemSearchCache(targetItem)
                }
                if (typeof applySearch === 'function') {
                    applySearch()
                }
            }
        })
}

async function openFamyliamCloud(filename, preset) {
    // 如果是AMLL规则编写网址
    if (preset === 'amll') {
        try {
            const styleSettingsSnapshot = readCachedAmllSettings();
            const styleQuerySnapshot = buildStyleQueryFromSettings(styleSettingsSnapshot);

        const response = await fetch('/get_json_data?filename=' + encodeURIComponent(filename), { cache: 'no-store' });
            const data = await response.json();
            if (data.status !== 'success') {
                alert(t('alert.noSongInfo'));
                return;
            }

            const jsonData = data.jsonData;
            const meta = jsonData.meta || {};

            const title = encodeURIComponent(meta.title || '');
            const artists = encodeURIComponent((meta.artists || []).join(' / '));

            let musicPath = '';
            if (jsonData.song) {
                const resolvedSongUrl = normalizeSongsUrl(jsonData.song);
                if (resolvedSongUrl && resolvedSongUrl !== '!') {
                    musicPath = encodeURIComponent(resolvedSongUrl);
                }
            }

            let lyricsPath = '';
            let originalLyricsRelative = '';
            const lyricsField = meta.lyrics || '';
            const lyricCandidate = lyricsField.includes('::')
                ? (lyricsField.split('::')[1] || '')
                : lyricsField;
            if (lyricCandidate && lyricCandidate !== '!') {
                const resolvedLyricsUrl = normalizeSongsUrl(lyricCandidate);
                if (resolvedLyricsUrl && resolvedLyricsUrl !== '!') {
                    lyricsPath = encodeURIComponent(resolvedLyricsUrl);
                    originalLyricsRelative = stripSongsPrefix(resolvedLyricsUrl);
                }
            }

            const resolveMediaForAmll = (value) => {
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

            const filterPlaceholderAlbumAsset = (sourceUrl) => {
                if (!sourceUrl) {
                    return '';
                }
                const fileName = safeDecodeURIComponent((sourceUrl.split('/').pop() || '')).toLowerCase();
                if (fileName === '专辑图.jpg' || fileName === 'album_cover.jpg') {
                    return '';
                }
                return sourceUrl;
            };

            const pickFirstAvailableMedia = (candidates = []) => {
                for (const candidate of candidates) {
                    if (candidate && candidate !== '!') {
                        return candidate;
                    }
                }
                return '';
            };

            const resolveDynamicCoverFrame = async (sourceUrl, timeoutMs = 1800) => {
                if (!sourceUrl) {
                    return '';
                }

                let timeoutId = null;
                const timeoutPromise = new Promise((resolve) => {
                    timeoutId = window.setTimeout(() => resolve(''), timeoutMs);
                });

                const framePromise = (async () => {
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
                        console.warn('Failed to extract frame for AMLL cover:', error);
                    }
                    return '';
                })();

                try {
                    return await Promise.race([framePromise, timeoutPromise]);
                } finally {
                    if (timeoutId !== null) {
                        window.clearTimeout(timeoutId);
                    }
                }
            };

            const dynamicCoverSrc = resolveMediaForAmll(meta.dynamicCoverSrc);
            const dynamicCoverPoster = filterPlaceholderAlbumAsset(resolveMediaForAmll(meta.dynamicCoverPoster));
            const albumImgSrc = filterPlaceholderAlbumAsset(
                resolveMediaForAmll(meta.albumImgSrc) ||
                resolveMediaForAmll(meta.cover) ||
                resolveMediaForAmll(meta.coverUrl) ||
                resolveMediaForAmll(jsonData.cover) ||
                resolveMediaForAmll(jsonData.coverUrl)
            );
            const backgroundImage = resolveMediaForAmll(meta['Background-image']);
            const dynamicMediaType = getAmllMediaType(dynamicCoverSrc);
            const dynamicVideoSource = dynamicMediaType === 'video' ? dynamicCoverSrc : '';

            let coverSource = '';
            if (dynamicVideoSource) {
                coverSource = pickFirstAvailableMedia([
                    dynamicCoverPoster,
                    albumImgSrc,
                    backgroundImage
                ]);
                if (!coverSource) {
                    coverSource = await resolveDynamicCoverFrame(dynamicVideoSource);
                }
            } else if (dynamicCoverSrc) {
                coverSource = dynamicCoverSrc;
            } else {
                coverSource = pickFirstAvailableMedia([
                    dynamicCoverPoster,
                    albumImgSrc,
                    backgroundImage
                ]);
            }

            const coverPath = coverSource ? encodeURIComponent(coverSource) : '';
            const dynamicCoverPath = dynamicVideoSource ? encodeURIComponent(dynamicVideoSource) : '';
            const dynamicCoverPosterPath = dynamicCoverPoster ? encodeURIComponent(dynamicCoverPoster) : '';

            const openAmllPlayer = (encodedLyricPath) => {
                const styleQuery = styleQuerySnapshot;
                const params = [];
                if (musicPath) params.push(`music=${musicPath}`);
                if (encodedLyricPath) params.push(`lyric=${encodedLyricPath}`);
                if (title) params.push(`title=${title}`);
                if (artists) params.push(`artist=${artists}`);
                if (coverPath) params.push(`cover=${coverPath}`);
                if (dynamicCoverPath) {
                    params.push(`dynamicCover=${dynamicCoverPath}`);
                    params.push(`dynamicCoverSrc=${dynamicCoverPath}`);
                }
                if (dynamicCoverPosterPath) params.push(`dynamicCoverPoster=${dynamicCoverPosterPath}`);
                const baseQuery = params.join('&');
                const mergedQuery = baseQuery
                    ? `${baseQuery}${styleQuery}`
                    : (styleQuery ? styleQuery.replace(/^&/, '') : '');
                const safeAmllBase = ensureTrailingSlash(amllPlayerBaseUrl);
                const amllUrl = mergedQuery
                    ? `${safeAmllBase}?${mergedQuery}`
                    : safeAmllBase;
                window.open(amllUrl, '_blank');
            };
            const preparePlayerLyric = async (sourcePath) => {
                const resp = await fetch('/prepare_ttml_for_player', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ path: sourcePath })
                });
                const data = await resp.json();
                if (data.status === 'success' && data.ttmlPath) {
                    return normalizeSongsUrl(data.ttmlPath);
                }
                const message = data.message || '生成TTML失败';
                throw new Error(message);
            };

            if (originalLyricsRelative) {
                try {
                    const preparedUrl = await preparePlayerLyric(lyricCandidate);
                    const preparedPath = encodeURIComponent(preparedUrl);
                    openAmllPlayer(preparedPath);
                } catch (error) {
                    console.warn('歌词处理失败，已跳过歌词参数:', error);
                    openAmllPlayer('');
                }
            } else {
                openAmllPlayer('');
            }
        } catch (error) {
            console.error('获取歌曲信息失败:', error);
            alert(t('alert.getSongInfoFailed'));
        }
        return;
    }

    // 原有的处理逻辑
    const baseUrl = preset === 'am'
        ? `http://localhost:8081/app/?#${backendRootUrl}`
        : `https://famyliam.ft2.ltd/app?preset=${preset}&font-preset=misans&pulling=superweak#${backendRootUrl}`;
    window.open(baseUrl + filename);
}