const backendOrigin = window.location.origin;
const backendRootUrl = new URL('/', backendOrigin).toString();
const ensureTrailingSlash = (value) => {
    if (!value) return '';
    return value.endsWith('/') ? value : `${value}/`;
};
const amllPlayerConfigElement = document.getElementById('amllPlayerConfig');
let amllPlayerBaseUrl = null;
if (amllPlayerConfigElement && amllPlayerConfigElement.textContent) {
    try {
        amllPlayerBaseUrl = JSON.parse(amllPlayerConfigElement.textContent);
    } catch (error) {
        console.warn('Failed to parse AMLL player base URL from config, fallback to local path.', error);
    }
}
amllPlayerBaseUrl = ensureTrailingSlash(
    amllPlayerBaseUrl || new URL('/amll-web/', backendOrigin).toString()
);
const AMLL_SETTINGS_STORAGE_KEY = 'amll_background_settings';
const STYLE_PARAM_EXCLUDE = new Set([
    'musicUrl',
    'lyricUrl',
    'coverUrl',
    'songTitle',
    'songArtist',
    'musicUrlInput',
    'lyricUrlInput',
    'coverUrlInput',
    'songTitleInput',
    'songArtistInput'
]);
const STYLE_PARAM_ALIAS = {
    lyricDelay: 'ms',
    playbackRate: 'x',
    volume: 'vol',
    loopPlay: 'loop',
    autoPlay: 'auto',
    rangeStartTime: 't',
    rangeEndTime: 'te'
};

function readCachedAmllSettings() {
    try {
        const raw = window.localStorage.getItem(AMLL_SETTINGS_STORAGE_KEY);
        if (!raw) {
            return null;
        }
        const parsed = JSON.parse(raw);
        return (parsed && typeof parsed === 'object') ? parsed : null;
    } catch (error) {
        console.warn('Failed to parse AMLL cached settings:', error);
        return null;
    }
}

function buildStyleQueryFromSettings(settings) {
    if (!settings || typeof settings !== 'object') {
        return '';
    }
    const queryParts = [];
    for (const [key, value] of Object.entries(settings)) {
        if (STYLE_PARAM_EXCLUDE.has(key)) {
            continue;
        }
        if (value === undefined || value === null) {
            continue;
        }
        if (typeof value === 'string' && value === '') {
            continue;
        }
        const alias = STYLE_PARAM_ALIAS[key] || key;
        let serialized;
        if (typeof value === 'boolean') {
            serialized = value ? '1' : '0';
        } else {
            serialized = String(value);
        }
        queryParts.push(`${encodeURIComponent(alias)}=${encodeURIComponent(serialized)}`);
    }
    return queryParts.length ? `&${queryParts.join('&')}` : '';
}

function buildStyleQueryFromCache() {
    const settings = readCachedAmllSettings();
    return buildStyleQueryFromSettings(settings);
}

function safeDecodeURIComponent(value) {
    try {
        return decodeURIComponent(value);
    } catch (error) {
        return value;
    }
}

function createResourceConfig(segment) {
    const baseUrl = new URL(`${segment}/`, backendRootUrl).toString();
    const path = new URL(`${segment}/`, backendRootUrl).pathname;
    return { base: baseUrl, path, name: segment };
}

const RESOURCE_CONFIG = {
    songs: createResourceConfig('songs'),
    static: createResourceConfig('static'),
    backups: createResourceConfig('backups')
};

function readLyricSphereBootstrap() {
    const el = document.getElementById('lyricSphereBootstrap');
    if (!el || !el.textContent || !el.textContent.trim()) {
        return {};
    }
    try {
        const parsed = JSON.parse(el.textContent);
        return (parsed && typeof parsed === 'object') ? parsed : {};
    } catch (error) {
        console.warn('Failed to parse lyricSphereBootstrap JSON:', error);
        return {};
    }
}

const _lyricSphereBootstrap = readLyricSphereBootstrap();
const DEFAULT_AMLL_COVER = (typeof _lyricSphereBootstrap.defaultAmllCover === 'string'
    && _lyricSphereBootstrap.defaultAmllCover.trim())
    ? _lyricSphereBootstrap.defaultAmllCover.trim()
    : '/icons/icon-512x512.png';
let createMode = 'manual';
let amllSnapshot = null;

function normalizeResourceUrl(value, resourceKey) {
    if (!value || typeof value !== 'string') {
        return value;
    }
    if (value === '!') {
        return value;
    }
    const config = RESOURCE_CONFIG[resourceKey];
    if (!config) {
        return value;
    }

    try {
        const parsed = new URL(value);
        if (parsed.pathname.startsWith(config.path)) {
            const decodedPath = safeDecodeURIComponent(parsed.pathname);
            const normalizedPath = decodedPath.startsWith(config.path)
                ? decodedPath
                : config.path + decodedPath.replace(/^\//, '');
            const search = parsed.search ? safeDecodeURIComponent(parsed.search) : '';
            const hash = parsed.hash ? safeDecodeURIComponent(parsed.hash) : '';
            return `${window.location.protocol}//${window.location.host}${normalizedPath}${search}${hash}`;
        }
        return value;
    } catch (error) {
        let normalized = value.replace(/\\/g, '/').replace(/^[./]+/, '');
        if (normalized.startsWith(`${config.name}/`)) {
            normalized = normalized.substring(config.name.length + 1);
        }
        normalized = safeDecodeURIComponent(normalized);
        if (!normalized) {
            return config.base;
        }
        return config.base + normalized;
    }
}

function stripResourcePrefix(value, resourceKey) {
    if (!value || typeof value !== 'string') {
        return value || '';
    }
    if (value === '!') {
        return value;
    }
    const config = RESOURCE_CONFIG[resourceKey];
    if (!config) {
        return value;
    }

    try {
        const parsed = new URL(value);
        if (parsed.pathname.startsWith(config.path)) {
            return safeDecodeURIComponent(parsed.pathname.substring(config.path.length));
        }
    } catch (error) {
        const normalizedValue = value.replace(/\\/g, '/');
        if (normalizedValue.startsWith(config.base)) {
            return safeDecodeURIComponent(normalizedValue.substring(config.base.length));
        }
        if (normalizedValue.startsWith(config.path)) {
            return safeDecodeURIComponent(normalizedValue.substring(config.path.length));
        }
        if (normalizedValue.startsWith(`${config.name}/`)) {
            return safeDecodeURIComponent(normalizedValue.substring(config.name.length + 1));
        }
    }
    return value;
}

function normalizeSongsUrl(value) {
    return normalizeResourceUrl(value, 'songs');
}

function stripSongsPrefix(value) {
    return stripResourcePrefix(value, 'songs');
}

function normalizeStaticUrl(value) {
    return normalizeResourceUrl(value, 'static');
}

function normalizeBackupsUrl(value) {
    return normalizeResourceUrl(value, 'backups');
}