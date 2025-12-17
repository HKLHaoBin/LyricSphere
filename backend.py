#最终发布版本
import base64
import struct
import hashlib
import json
import copy
import bcrypt
import logging
import os
import re
import shutil
import time
import webbrowser
import sys
import xml
import uuid
import csv
import zipfile
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from io import BytesIO
from flask import Flask, jsonify, render_template, request, Response, session, g, abort, stream_with_context, has_request_context, send_from_directory, send_file
from re import compile, Pattern, Match
from typing import Iterator, TextIO, AnyStr, Optional, Union, Set, List, Dict, Tuple, Any
from xml.dom.minicompat import NodeList
from xml.dom import Node
from xml.dom.minidom import Document, Element
from flask.ctx import F
from openai import OpenAI
import random
import threading
import socket
import asyncio
import websockets
import queue
from urllib.parse import urlparse, unquote


def get_base_path():
    """ 智能获取运行基础路径 """
    if getattr(sys, 'frozen', False):
        # 打包模式：exe所在目录（dist/backend）
        return Path(sys.executable).parent.absolute()
    # 开发模式：脚本所在目录
    return Path(__file__).parent.absolute()

# 全局基础路径
BASE_PATH = get_base_path()

# 创建导出目录
EXPORTS_DIR = BASE_PATH / 'exports'
EXPORTS_DIR.mkdir(parents=True, exist_ok=True)

# Flask配置（动态路径）
app = Flask(
    __name__,
    static_folder=str(BASE_PATH / 'static'),
    template_folder=str(BASE_PATH / 'templates'),
    static_url_path=''
)
app.jinja_env.filters['tojson'] = json.dumps
app.secret_key = 'your_random_secret_key'

# 添加Jinja2全局过滤器
app.jinja_env.globals.update(tojson=json.dumps)

def cleanup_missing_ssl_cert_env() -> List[str]:
    """
    Remove SSL-related environment variables that point to missing files/dirs.
    This prevents ssl.create_default_context from crashing with FileNotFoundError.
    """
    removed: List[str] = []
    file_envs = ('SSL_CERT_FILE', 'REQUESTS_CA_BUNDLE', 'CURL_CA_BUNDLE')
    for env_key in file_envs:
        candidate = os.environ.get(env_key)
        if candidate and not os.path.isfile(candidate):
            removed.append(f"{env_key}={candidate}")
            os.environ.pop(env_key, None)

    cert_dir = os.environ.get('SSL_CERT_DIR')
    if cert_dir and not os.path.isdir(cert_dir):
        removed.append(f"SSL_CERT_DIR={cert_dir}")
        os.environ.pop('SSL_CERT_DIR', None)
    return removed


def build_openai_client(api_key: str, base_url: str) -> OpenAI:
    """Create OpenAI client with resilient SSL setup."""
    def _create_openai_client(http_client=None) -> OpenAI:
        return OpenAI(api_key=api_key, base_url=base_url, http_client=http_client)

    try:
        return _create_openai_client()
    except FileNotFoundError as exc:
        cleared = cleanup_missing_ssl_cert_env()
        if cleared:
            app.logger.warning(
                "OpenAI客户端初始化时检测到失效的SSL证书路径，已移除: %s",
                ', '.join(cleared)
            )
            try:
                return _create_openai_client()
            except FileNotFoundError as retry_exc:
                exc = retry_exc

        try:
            import certifi
            import httpx
            ca_path = certifi.where()
            app.logger.warning(
                "OpenAI客户端初始化时SSL默认证书加载失败，改用certifi CA文件: %s",
                ca_path
            )
            http_client = httpx.Client(verify=ca_path, timeout=httpx.Timeout(30.0))
            return _create_openai_client(http_client=http_client)
        except Exception as fallback_error:
            app.logger.error("使用certifi CA回退仍失败: %s", fallback_error)
            try:
                app.logger.warning("将禁用SSL验证以继续运行AI翻译（仅用于临时兼容）")
                http_client = httpx.Client(verify=False, timeout=httpx.Timeout(30.0))
                return _create_openai_client(http_client=http_client)
            except Exception as insecure_error:
                app.logger.error("禁用SSL验证的回退也失败: %s", insecure_error)
        raise exc

# 所有路径定义使用绝对路径
STATIC_DIR = BASE_PATH / 'static'
SONGS_DIR = STATIC_DIR / 'songs'
BACKUP_DIR = STATIC_DIR / 'backups'
LOG_DIR = BASE_PATH / 'logs'

# 自动创建目录（首次运行时）
for path in [SONGS_DIR, BACKUP_DIR, LOG_DIR]:
    path.mkdir(parents=True, exist_ok=True)

RESOURCE_DIRECTORIES = {
    'static': STATIC_DIR,
    'songs': SONGS_DIR,
    'backups': BACKUP_DIR,
}

SAFE_FILENAME_PATTERN = re.compile(r'[^\w\u4e00-\u9fa5\-_. ]')


def sanitize_filename(value: Optional[str]) -> str:
    """Normalize filenames while preserving spaces and safe punctuation."""
    if not value:
        return ''

    cleaned = SAFE_FILENAME_PATTERN.sub('', value)
    cleaned = cleaned.replace('"', '＂').replace("'", '＂')
    return cleaned.strip()

def _normalize_relative_path(value: str) -> str:
    cleaned = (value or '').replace('\\', '/').strip('/')
    if not cleaned:
        return ''

    segments = [segment for segment in cleaned.split('/') if segment]
    for segment in segments:
        if segment in ('.', '..'):
            raise ValueError('路径包含非法段')
    return '/'.join(segments)


def extract_resource_relative(value: str, resource: str) -> str:
    if resource not in RESOURCE_DIRECTORIES:
        raise ValueError(f'未知资源类型: {resource}')
    if value is None:
        raise ValueError('路径不能为空')

    parsed = urlparse(str(value))
    candidate = parsed.path if parsed.scheme else str(value)
    candidate = unquote(candidate.replace('\\', '/')).lstrip('/')

    prefix = f"{resource}/"
    if candidate.startswith(prefix):
        candidate = candidate[len(prefix):]
    elif parsed.scheme or parsed.netloc:
        raise ValueError(f'仅允许访问 /{resource}/ 下的文件')

    return _normalize_relative_path(candidate)


def resolve_resource_path(value: str, resource: str) -> Path:
    relative = extract_resource_relative(value, resource)
    base_dir = RESOURCE_DIRECTORIES[resource].resolve()
    target = (base_dir / relative).resolve() if relative else base_dir

    try:
        target.relative_to(base_dir)
    except ValueError as exc:
        raise ValueError('路径越界') from exc

    return target


def resource_relative_from_path(path_value: Union[str, Path], resource: str) -> str:
    if resource not in RESOURCE_DIRECTORIES:
        raise ValueError(f'未知资源类型: {resource}')

    base_dir = RESOURCE_DIRECTORIES[resource].resolve()
    path_obj = (Path(path_value) if not isinstance(path_value, Path) else path_value).resolve()

    try:
        relative = path_obj.relative_to(base_dir)
    except ValueError as exc:
        raise ValueError('路径不在受控目录内') from exc

    return _normalize_relative_path(str(relative))


def _extract_single_song_relative(value: Optional[str]) -> Optional[str]:
    """Return the relative path under songs/ referenced by the provided string."""
    if not value:
        return None

    cleaned = str(value).strip()
    if not cleaned or cleaned == '!':
        return None

    normalized = cleaned.replace('\\', '/').strip()
    parsed = urlparse(normalized)
    candidate = parsed.path if parsed.scheme else normalized
    candidate = candidate.strip()
    if not candidate:
        return None

    # Trim leading ./ or / segments repeatedly
    while candidate.startswith('./'):
        candidate = candidate[2:]
    candidate = candidate.lstrip('/')

    lower_candidate = candidate.lower()
    if lower_candidate.startswith('static/'):
        candidate = candidate[len('static/'):]
        lower_candidate = candidate.lower()

    while candidate.startswith('./'):
        candidate = candidate[2:]
        lower_candidate = candidate.lower()

    candidate = candidate.lstrip('/')
    lower_candidate = candidate.lower()
    if not lower_candidate.startswith('songs/'):
        return None

    relative = candidate[len('songs/'):].lstrip('/')
    if not relative:
        return None

    relative = relative.split('?', 1)[0].split('#', 1)[0].strip()
    if not relative:
        return None

    try:
        return _normalize_relative_path(relative)
    except ValueError:
        return None


def collect_song_resource_paths(payload: Union[dict, list, str]) -> Set[str]:
    """Walk a JSON payload and gather every referenced songs/ asset."""
    collected: Set[str] = set()

    def _walk(value: Union[dict, list, str, None]):
        if isinstance(value, dict):
            for item in value.values():
                _walk(item)
        elif isinstance(value, list):
            for item in value:
                _walk(item)
        elif isinstance(value, str):
            if '::' in value:
                for chunk in value.split('::'):
                    _walk(chunk)
                return
            relative = _extract_single_song_relative(value)
            if relative:
                collected.add(relative)

    _walk(payload)
    return collected


def get_public_base_url() -> str:
    if has_request_context():
        return request.url_root.rstrip('/')

    configured = app.config.get('PUBLIC_BASE_URL') or os.environ.get('PUBLIC_BASE_URL')
    if configured:
        return configured.rstrip('/')

    port = os.environ.get('PORT', '5000')
    return f"http://127.0.0.1:{port}".rstrip('/')


def get_amll_web_player_base_url() -> str:
    override = app.config.get('AMLL_WEB_BASE_URL') or os.environ.get('AMLL_WEB_BASE_URL')
    if override:
        return override.rstrip('/')

    base = get_public_base_url()
    return f"{base.rstrip('/')}/amll-web"


def build_public_url(resource: str, relative_path: str) -> str:
    if not relative_path:
        return ''

    normalized = _normalize_relative_path(relative_path)
    base_url = get_public_base_url()
    return f"{base_url}/{resource}/{normalized}"

_raw_cors_origins = os.environ.get('CORS_ALLOW_ORIGINS', '*')
if _raw_cors_origins.strip() == '*':
    ALLOWED_CORS_ORIGINS = ['*']
else:
    ALLOWED_CORS_ORIGINS = [origin.strip() for origin in _raw_cors_origins.split(',') if origin.strip()]

ALLOWED_CORS_HEADERS = os.environ.get('CORS_ALLOW_HEADERS', 'Authorization,Content-Type')
ALLOWED_CORS_METHODS = os.environ.get('CORS_ALLOW_METHODS', 'GET,POST,PUT,DELETE,OPTIONS')


def _match_cors_origin(origin: Optional[str]) -> Optional[str]:
    if not origin:
        return None
    if '*' in ALLOWED_CORS_ORIGINS:
        return origin
    if origin in ALLOWED_CORS_ORIGINS:
        return origin
    return None


@app.before_request
def handle_cors_preflight():
    if request.method == 'OPTIONS':
        response = app.make_response('')
        response.status_code = 204
        return response


@app.after_request
def apply_cors_headers(response):
    origin = request.headers.get('Origin')
    allow_origin = _match_cors_origin(origin)

    if allow_origin:
        response.headers['Access-Control-Allow-Origin'] = allow_origin
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        vary_header = response.headers.get('Vary', '')
        vary_items = [item.strip() for item in vary_header.split(',') if item.strip()]
        if 'Origin' not in vary_items:
            vary_items.append('Origin')
        response.headers['Vary'] = ', '.join(vary_items)

    if allow_origin or '*' in ALLOWED_CORS_ORIGINS:
        response.headers['Access-Control-Allow-Headers'] = request.headers.get('Access-Control-Request-Headers', ALLOWED_CORS_HEADERS)
        response.headers['Access-Control-Allow-Methods'] = request.headers.get('Access-Control-Request-Method', ALLOWED_CORS_METHODS)

    return response

BACKUP_TIMESTAMP_FORMAT = '%Y%m%d_%H%M%S'
BACKUP_SUFFIX_LENGTH = len(datetime.now().strftime(BACKUP_TIMESTAMP_FORMAT)) + 1  # include separator dot
MAX_BACKUP_FILENAME_LENGTH = 255
BACKUP_HASH_LENGTH = 8


def _normalize_backup_basename(original_name: str) -> str:
    """Ensure backup filenames stay within common filesystem limits."""
    if not original_name:
        return original_name
    if len(original_name) + BACKUP_SUFFIX_LENGTH <= MAX_BACKUP_FILENAME_LENGTH:
        return original_name

    suffix = ''.join(Path(original_name).suffixes)
    stem = original_name[:-len(suffix)] if suffix else original_name
    hash_part = hashlib.sha1(original_name.encode('utf-8')).hexdigest()[:BACKUP_HASH_LENGTH]
    available = MAX_BACKUP_FILENAME_LENGTH - BACKUP_SUFFIX_LENGTH - len(suffix) - BACKUP_HASH_LENGTH - 1

    if available <= 0:
        truncated = f"{hash_part}{suffix}"
        return truncated[:MAX_BACKUP_FILENAME_LENGTH - BACKUP_SUFFIX_LENGTH]

    return f"{stem[:available]}_{hash_part}{suffix}"


def build_backup_path(name_or_path: Union[str, Path],
                      timestamp: Optional[Union[str, int]] = None,
                      directory: Path = BACKUP_DIR) -> Path:
    """Create a filesystem-safe backup path for the given target file."""
    original_name = name_or_path.name if isinstance(name_or_path, Path) else str(name_or_path)
    base_name = _normalize_backup_basename(original_name)
    if isinstance(timestamp, (int, float)):
        timestamp_str = str(int(timestamp))
    elif timestamp is not None:
        timestamp_str = str(timestamp)
    else:
        timestamp_str = datetime.now().strftime(BACKUP_TIMESTAMP_FORMAT)
    return directory / f"{base_name}.{timestamp_str}"


def backup_prefix(name_or_path: Union[str, Path]) -> str:
    """Return the normalized prefix used for locating backups."""
    original_name = name_or_path.name if isinstance(name_or_path, Path) else str(name_or_path)
    return f"{_normalize_backup_basename(original_name)}."

# 配置日志
log_format = '%(asctime)s - %(levelname)s - %(message)s'
log_handler = TimedRotatingFileHandler(os.path.join(LOG_DIR, 'upload.log'),
                                       when='midnight',
                                       interval=1,
                                       backupCount=7,
                                       encoding='utf-8')
log_handler.setFormatter(logging.Formatter(log_format))
app.logger.addHandler(log_handler)

# 将日志同步输出到终端，方便实时查看处理结果
if not any(
    isinstance(handler, logging.StreamHandler) and getattr(handler, 'stream', None) is sys.stdout
    for handler in app.logger.handlers
):
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(logging.Formatter(log_format))
    app.logger.addHandler(console_handler)

# 设置日志级别，支持通过环境变量启用调试日志
log_level = logging.DEBUG if os.environ.get('DEBUG_LOGGING', '0') == '1' else logging.INFO
app.logger.setLevel(log_level)

useu = ""

# ==== AMLL -> 前端 的实时总线（SSE） ====
# 全局状态（给前端快照用）
AMLL_STATE = {
    "song": {"musicName": "", "artists": [], "duration": 0, "album": "", "cover": "", "cover_data_url": ""},
    "progress_ms": 0,
    "lines": [],
    "last_update": 0
}
# 事件队列（给前端实时推送用）
AMLL_QUEUE = queue.Queue(maxsize=1000)

# 添加全局变量存储AI翻译设置
AI_TRANSLATION_SETTINGS = {
    'api_key': '',
    'system_prompt': '''一个专业的歌词翻译助手。会按照以下规则翻译歌词：
1. 保持原文的意境和情感：令人感动的歌词，不一定需要华丽的词藻，但一定有真挚的感情。
2. 确保每行翻译都准确对应原文
3. 翻译结果必须保持序号格式，例如：1.翻译内容
4. 不要添加任何额外的解释或说明
5. 确保每行翻译都是独立的，不要将多行合并''',
    'provider': 'deepseek',
    'base_url': 'https://api.deepseek.com',
    'model': 'deepseek-reasoner',
    'expect_reasoning': True,
    'strip_brackets': False,
    'compat_mode': False,
    'thinking_enabled': True,
    'thinking_api_key': '',
    'thinking_provider': 'deepseek',
    'thinking_base_url': 'https://api.deepseek.com',
    'thinking_model': 'deepseek-reasoner',
    'thinking_system_prompt': '''你是一位资深的歌词分析师。请通读整首歌的歌词，生成对歌曲主题、情绪、叙事视角和潜在文化背景的综合理解，并指出可能影响翻译语气的关键细节。'''
}

AI_TRANSLATION_DEFAULTS = AI_TRANSLATION_SETTINGS.copy()

# ===== 动画配置（前端共用） =====
# 默认动画配置：控制歌词行进入、移动、退出及占位时长，以及歌词垂直偏移比例
ANIMATION_CONFIG_DEFAULTS = {
    'enterDuration':500,      # 歌词行进入动画时长（毫秒）
    'moveDuration': 500,       # 歌词行上下移动动画时长（毫秒）
    'exitDuration': 500,       # 歌词行退出动画时长（毫秒）
    'placeholderDuration': 50, # 歌词行占位缓冲时长（毫秒），用于 disappearTime 计算
    'lineDisplayOffset': 0.7,  # 歌词行在屏幕垂直方向的偏移比例（0.0=顶部，1.0=底部）
    'useComputedDisappear': False  # 是否使用后端计算的消失时机
}
_animation_config_state = dict(ANIMATION_CONFIG_DEFAULTS)
_animation_config_lock = threading.Lock()
_animation_config_last_update = 0.0


def load_animation_config() -> dict:
    """读取当前动画配置的副本，供后端内部使用。"""
    with _animation_config_lock:
        return dict(_animation_config_state)


def update_animation_config(payload: dict) -> dict:
    """
    根据前端 POST 的配置更新全局动画参数。
    非法值会被忽略并保留原值。
    返回最新的配置。
    """
    if not isinstance(payload, dict):
        payload = {}

    updated_fields = {}
    for key in ANIMATION_CONFIG_DEFAULTS:
        value = payload.get(key)
        if value is None:
            continue

        if key == 'lineDisplayOffset':
            try:
                parsed_value = float(value)
            except (TypeError, ValueError):
                app.logger.warning("动画配置项 %s=%r 非法，已忽略", key, value)
                continue
        elif key == 'useComputedDisappear':
            parsed_value = parse_bool(value, ANIMATION_CONFIG_DEFAULTS[key])
        else:
            try:
                parsed_value = int(value)
            except (TypeError, ValueError):
                app.logger.warning("动画配置项 %s=%r 非法，已忽略", key, value)
                continue

        updated_fields[key] = parsed_value

    with _animation_config_lock:
        if updated_fields:
            _animation_config_state.update(updated_fields)
            global _animation_config_last_update
            _animation_config_last_update = time.time()

        return dict(_animation_config_state)


def parse_bool(value, default=False):
    """Parse falsy/truthy inputs coming from JSON or form submissions."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {'1', 'true', 'yes', 'on'}
    return bool(value)

NUMERIC_TAG_REGEX = re.compile(r'\(\d+,\d+\)')
BRACKET_CHARACTERS = '()（）[]【】'
BRACKET_TRANSLATION = str.maketrans('', '', BRACKET_CHARACTERS)
BRACKET_ONLY_PATTERN = re.compile(rf'^[\s{re.escape(BRACKET_CHARACTERS)}]+$')
FONT_FAMILY_META_REGEX = re.compile(r'^\[font-family:\s*([^\]]*)\s*\]$', re.IGNORECASE)

SCRIPT_CHECKERS = {
    'ja': re.compile(r'[\u3040-\u30ff\u31f0-\u31ff\u4e00-\u9fff]'),
    'en': re.compile(r'[A-Za-z]')
}

def detect_script(text: str) -> str:
    if not text:
        return ''
    for lang, pattern in SCRIPT_CHECKERS.items():
        if pattern.search(text):
            return lang
    return ''

def choose_font_for_text(text: str, default_font: Optional[str], lang_map: Dict[str, str]) -> Optional[str]:
    script = detect_script(text)
    if script and script in lang_map:
        mapped = lang_map[script]
        return mapped or None
    return default_font or None


def parse_font_family_meta(raw: str) -> Tuple[Optional[str], Dict[str, str]]:
    """
    解析 font-family 元标签，支持多字体/语言映射。
    语法示例：
      [font-family:Hymmnos]                   -> 默认字体 Hymmnos
      [font-family:Hymmnos(en),(ja)]          -> en 用 Hymmnos，ja 用默认
      [font-family:Main(en),Sub(ja),Extra]    -> en 用 Main，ja 用 Sub，默认 Extra
      [font-family:]                          -> 清空为默认字体
    返回 (default_font, lang_map)
    """
    if raw is None:
        return None, {}

    parts = [p.strip() for p in raw.split(',')]
    default_font: Optional[str] = None
    lang_map: Dict[str, str] = {}

    for part in parts:
        if not part:
            continue
        m = re.match(r'^(?:(?P<name>[^()]+)?\s*\((?P<lang>[^)]+)\))|(?P<plain>[^()]+)$', part)
        if not m:
            continue
        if m.group('plain'):
            default_font = m.group('plain').strip() or default_font
            continue
        name = (m.group('name') or '').strip()
        lang = (m.group('lang') or '').strip().lower()
        if lang:
            lang_map[lang] = name
    return default_font or None, lang_map


def strip_bracket_blocks(content: str) -> str:
    """移除常见括号字符但保留其中文本，用于翻译前预处理。"""
    if not content:
        return ''
    cleaned = content.translate(BRACKET_TRANSLATION)
    cleaned = re.sub(r'\s{2,}', ' ', cleaned)
    return cleaned.strip()


def strip_timing_tags(content: str) -> str:
    """移除形如 (start,duration) 的时间标记。"""
    return NUMERIC_TAG_REGEX.sub('', content or '')


def is_parenthetical_background_line(content: str) -> bool:
    """
    判断一行歌词在移除时间标记后是否整体包裹在括号内。
    用于识别未显式标记的背景歌词。
    """
    if not content:
        return False
    stripped = strip_timing_tags(content).strip()
    if not stripped:
        return False
    bracket_pairs = {
        '(': ')',
        '（': '）',
    }
    opening = stripped[0]
    expected_closing = bracket_pairs.get(opening)
    if not expected_closing or not stripped.endswith(expected_closing):
        return False
    inner = stripped[1:-1].strip()
    return bool(inner)

# ===== 快速歌词顺序编辑器：LYS文档解析与状态 =====
QUICK_EDITOR_DOCS: Dict[str, Dict[str, Any]] = {}
QUICK_EDITOR_UNDO: Dict[str, List[Dict[str, Any]]] = {}
QUICK_EDITOR_REDO: Dict[str, List[Dict[str, Any]]] = {}
QUICK_EDITOR_META: Dict[str, Dict[str, Any]] = {}


def qe_new_id() -> str:
    return uuid.uuid4().hex


def qe_clone(obj: Any) -> Any:
    return copy.deepcopy(obj)


def parse_meta_lyrics(meta_value: Optional[str]) -> Tuple[str, str, str, List[str]]:
    """拆分 meta.lyrics 字段，返回 (歌词路径, 翻译路径, 音译路径, parts)。"""
    raw = str(meta_value or '')
    parts = raw.split('::')
    if len(parts) >= 4:
        while len(parts) < 5:
            parts.append('')
        lyrics = parts[1] or ''
        translation = parts[2] or ''
        roman = parts[3] or ''
        return lyrics, translation, roman, parts
    return raw, '', '', parts


def build_meta_lyrics_value(existing_parts: List[str],
                            lyrics_url: str,
                            translation_url: Optional[str] = None,
                            roman_url: Optional[str] = None) -> str:
    """
    根据已有 parts 更新并重建 meta.lyrics。
    若已有格式为 ::lyrics::translation::roman::，则沿用该结构，否则写回单一路径。
    """
    if len(existing_parts) >= 4:
        parts = list(existing_parts)
        while len(parts) < 5:
            parts.append('')
        parts[1] = lyrics_url or '!'
        if translation_url is not None:
            parts[2] = translation_url or '!'
        if roman_url is not None:
            parts[3] = roman_url or '!'
        return '::'.join(parts)
    return lyrics_url or ''


def analyze_lyrics_tags(lyrics_path: str) -> Tuple[bool, bool]:
    """
    检查歌词文件是否包含对唱或背景人声标记。
    返回 (has_duet, has_background)。
    """
    if not lyrics_path or lyrics_path == '!':
        return False, False

    real_path = resolve_resource_path(lyrics_path, 'songs')
    if not real_path.exists():
        raise FileNotFoundError(f'歌词文件未找到: {real_path}')

    with open(real_path, 'r', encoding='utf-8') as f:
        content = f.read()

    has_duet = '[2]' in content or '[5]' in content or 'ttm:agent="v2"' in content
    has_background = '[6]' in content or '[7]' in content or '[8]' in content or 'ttm:role="x-bg"' in content
    return has_duet, has_background


def has_valid_audio(song_value: str) -> bool:
    """判断音源字段是否有效（排除占位符并校验本地文件存在性）。"""
    trimmed = (song_value or '').strip()
    if not trimmed or trimmed == '!' or '音乐.mp3' in trimmed:
        return False

    try:
        parsed = urlparse(trimmed)
        if parsed.scheme in ('http', 'https'):
            return True
    except Exception:
        pass

    try:
        real_path = resolve_resource_path(trimmed, 'songs')
        return real_path.exists()
    except Exception:
        return False


def qe_parse_lys(raw_text: str) -> Dict[str, Any]:
    """
    将 .lys 文本解析为结构化文档：
    doc = { id, version, lines: [ {id, prefix, is_meta, tokens:[{id, ts, text}]} ] }
    """
    lines: List[Dict[str, Any]] = []
    for raw_line in raw_text.splitlines():
        s = raw_line.rstrip("\r\n")
        if not s:
            lines.append({"id": qe_new_id(), "prefix": "", "is_meta": False, "tokens": []})
            continue

        if re.match(r'^\[(ti|ar|al):', s, re.IGNORECASE):
            lines.append({
                "id": qe_new_id(),
                "prefix": "",
                "is_meta": True,
                "tokens": [{"id": qe_new_id(), "ts": "", "text": s}]
            })
            continue

        prefix = ""
        rest = s
        m = re.match(r'^\[(\d+)\]', s)
        if m:
            prefix = m.group(0)
            rest = s[m.end():]
        elif s.startswith("[]"):
            prefix = "[]"
            rest = s[2:]

        tokens: List[Dict[str, str]] = []
        for tok in re.finditer(r'(.*?)[(（](\d+),(\d+)[)）]', rest):
            text = tok.group(1)
            start = tok.group(2)
            dur = tok.group(3)
            tokens.append({"id": qe_new_id(), "ts": f"{start},{dur}", "text": text})

        if tokens:
            lines.append({"id": qe_new_id(), "prefix": prefix, "is_meta": False, "tokens": tokens})
        else:
            lines.append({
                "id": qe_new_id(),
                "prefix": "",
                "is_meta": True,
                "tokens": [{"id": qe_new_id(), "ts": "", "text": s}]
            })

    return {"id": qe_new_id(), "version": 0, "lines": lines}


def qe_dump_lys(doc: Dict[str, Any]) -> str:
    """结构化文档还原为 .lys 文本。meta 行原样输出；歌词行输出 prefix + text(ts) 串联。"""
    out_lines: List[str] = []
    for line in doc.get("lines", []):
        if line.get("is_meta"):
            out_lines.append("".join(tok.get("text", "") for tok in line.get("tokens", [])))
            continue

        buf = [line["prefix"]] if line.get("prefix") else []
        for tok in line.get("tokens", []):
            ts = tok.get("ts", "")
            text = tok.get("text", "")
            buf.append(f"{text}({ts})" if ts else text)
        out_lines.append("".join(buf))
    return "\n".join(out_lines)


class MoveError(Exception):
    pass


def qe_find_line(doc: Dict[str, Any], line_id: str) -> Tuple[int, Dict[str, Any]]:
    for i, ln in enumerate(doc.get("lines", [])):
        if ln.get("id") == line_id:
            return i, ln
    raise MoveError(f"line not found: {line_id}")


def qe_find_token_index(line: Dict[str, Any], token_id: str) -> int:
    for i, tok in enumerate(line.get("tokens", [])):
        if tok.get("id") == token_id:
            return i
    raise MoveError(f"token not found in line {line.get('id')}: {token_id}")


def qe_normalize_selection(doc: Dict[str, Any], selection: List[Dict[str, str]]):
    """selection -> (li, ti, token) 列表（文档顺序）"""
    collected: List[Tuple[int, int, Dict[str, str]]] = []
    for rng in selection:
        li, line = qe_find_line(doc, rng["line_id"])
        a = qe_find_token_index(line, rng["start_token_id"])
        b = qe_find_token_index(line, rng["end_token_id"])
        if a > b:
            a, b = b, a
        for ti in range(a, b + 1):
            collected.append((li, ti, line["tokens"][ti]))
    collected.sort(key=lambda t: (t[0], t[1]))
    return collected


def qe_apply_move(doc: Dict[str, Any], selection: List[Dict[str, str]], target: Dict[str, Any],
                  delete_empty_lines: bool = True) -> None:
    """将 selection 的 tokens 按 target 位置移动。"""
    if not selection:
        return
    collected = qe_normalize_selection(doc, selection)
    if not collected:
        return

    selected_ids = {tok["id"] for _, _, tok in collected}

    by_line: Dict[int, List[int]] = {}
    for li, ti, _ in collected:
        by_line.setdefault(li, []).append(ti)
    for li, idxs in sorted(by_line.items(), key=lambda kv: kv[0], reverse=True):
        line = doc["lines"][li]
        for ti in sorted(idxs, reverse=True):
            del line["tokens"][ti]

    if target.get("type") == "anchor":
        t_li, t_line = qe_find_line(doc, target["line_id"])
        anchor_idx = qe_find_token_index(t_line, target["anchor_token_id"])
        if t_line["tokens"][anchor_idx]["id"] in selected_ids:
            raise MoveError("anchor token is within the selection")
        insert_at = anchor_idx if target.get("position") == "before" else anchor_idx + 1
    elif target.get("type") == "newline":
        new_line = {"id": qe_new_id(), "prefix": "", "is_meta": False, "tokens": []}
        after_id = target.get("insert_after_line_id")
        if after_id:
            idx, _ = qe_find_line(doc, after_id)
            doc["lines"].insert(idx + 1, new_line)
            t_line = new_line
            insert_at = 0
        else:
            doc["lines"].insert(0, new_line)
            t_line = new_line
            insert_at = 0
    elif target.get("type") == "line":
        t_li, t_line = qe_find_line(doc, target["line_id"])
        pos = target.get("position", "end")
        if pos not in ("start", "end"):
            raise MoveError("invalid line position")
        insert_at = 0 if pos == "start" else len(t_line["tokens"])
    else:
        raise MoveError("invalid target type")

    moving_tokens = [tok for _, _, tok in collected]
    for offset, tok in enumerate(moving_tokens):
        t_line["tokens"].insert(insert_at + offset, tok)

    if delete_empty_lines:
        doc["lines"] = [ln for ln in doc["lines"] if (ln.get("is_meta") or len(ln.get("tokens", [])) > 0)]


def qe_error_response(message: str, status: int = 400) -> Response:
    return Response(message, status=status, mimetype='text/plain')


def qe_register_doc(doc: Dict[str, Any], meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """缓存文档及其元信息，初始化撤销/重做栈。"""
    doc_id = doc.get("id") or qe_new_id()
    doc["id"] = doc_id
    QUICK_EDITOR_DOCS[doc_id] = doc
    QUICK_EDITOR_UNDO[doc_id] = []
    QUICK_EDITOR_REDO[doc_id] = []
    if meta is not None:
        QUICK_EDITOR_META[doc_id] = meta
    return doc


def ensure_lys_file_for_editor(source_path: Path, base_name: str) -> Tuple[Path, Optional[Path], bool]:
    """
    确保提供的歌词文件最终转为标准 LYS 文件并返回其路径。
    若源为 LRC/TTML 会自动转换，若缺失则创建空文件。
    返回 (lyrics_path, translation_path_or_none, 是否发生变更)。
    """
    changed = False
    translation_path: Optional[Path] = None

    if not source_path.suffix:
        source_path = source_path.with_suffix('.lys')

    if not source_path.exists():
        if source_path.suffix.lower() == '.lys':
            source_path.parent.mkdir(parents=True, exist_ok=True)
            source_path.touch()
            changed = True
        else:
            source_path = SONGS_DIR / f"{base_name}.lys"
            source_path.parent.mkdir(parents=True, exist_ok=True)
            if not source_path.exists():
                source_path.touch()
            changed = True

    ext = source_path.suffix.lower()
    if ext == '.lys':
        lyrics_path = source_path
    elif ext == '.ttml':
        success, lyric_path, trans_path = ttml_to_lys(str(source_path), str(SONGS_DIR))
        if not success or not lyric_path:
            raise ValueError('TTML 转换失败，无法生成 LYS')
        lyrics_path = Path(lyric_path)
        translation_path = Path(trans_path) if trans_path else None
        changed = True
    elif ext == '.lrc':
        ttml_temp = SONGS_DIR / f"{base_name}.ttml"
        ttml_temp.parent.mkdir(parents=True, exist_ok=True)
        success, error_msg = lrc_to_ttml(str(source_path), str(ttml_temp))
        if not success:
            raise ValueError(f"LRC 转换失败: {error_msg or '未知错误'}")
        success, lyric_path, trans_path = ttml_to_lys(str(ttml_temp), str(SONGS_DIR))
        if not success or not lyric_path:
            raise ValueError('LRC 转换后的 TTML 转 LYS 失败')
        lyrics_path = Path(lyric_path)
        translation_path = Path(trans_path) if trans_path else None
        changed = True
        if ttml_temp.exists():
            try:
                ttml_temp.unlink()
            except Exception:
                pass
    else:
        lyrics_path = SONGS_DIR / f"{base_name}.lys"
        lyrics_path.parent.mkdir(parents=True, exist_ok=True)
        if not lyrics_path.exists():
            lyrics_path.touch()
            changed = True

    target_path = SONGS_DIR / f"{base_name}.lys"
    if lyrics_path.resolve() != target_path.resolve():
        target_path.parent.mkdir(parents=True, exist_ok=True)
        if target_path.exists():
            backup_path = build_backup_path(target_path)
            shutil.copy2(target_path, backup_path)
        try:
            shutil.move(str(lyrics_path), str(target_path))
        except Exception:
            shutil.copy2(lyrics_path, target_path)
        lyrics_path = target_path
        changed = True

    if translation_path:
        trans_target = SONGS_DIR / f"{base_name}_trans.lrc"
        if trans_target.resolve() != translation_path.resolve():
            trans_target.parent.mkdir(parents=True, exist_ok=True)
            if trans_target.exists():
                backup_path = build_backup_path(trans_target)
                shutil.copy2(trans_target, backup_path)
            try:
                shutil.move(str(translation_path), str(trans_target))
            except Exception:
                shutil.copy2(translation_path, trans_target)
            translation_path = trans_target
        else:
            translation_path = trans_target

    return lyrics_path, translation_path, changed

# ===== 解析.lys格式歌词的工具函数 =====
def compute_disappear_times(lines, *, delta1=500, delta2=0, t_anim=None):
    """
    对每一行（含 syllables 数组，单位秒）计算 disappearTime（单位毫秒）。
    规则与 parse_lys 中保持一致：
    - 行末 E_i 与下一行首 N_next 的关系，DELTA1/DELTA2 调整
    - 与上一行最终 T_disappear_prev 的"礼让/衔接"
    """
    if not lines:
        return lines

    animation_config = load_animation_config()
    use_computed = parse_bool(animation_config.get('useComputedDisappear'), True)
    if t_anim is None:
        t_anim = animation_config.get('exitDuration', ANIMATION_CONFIG_DEFAULTS['exitDuration'])

    exit_buffer = max(0, int(t_anim)) if use_computed else 0
    placeholder_buffer = max(0, int(animation_config.get('placeholderDuration', ANIMATION_CONFIG_DEFAULTS['placeholderDuration'])))

    for line in lines:
        syllables = line.get('syllables', [])
        if syllables:
            last = syllables[-1]
            base_ms = int(float(last['startTime']) * 1000 + float(last['duration']) * 1000)
        else:
            base_ms = 0

        disappear_ms = base_ms + exit_buffer if use_computed else base_ms

        line['disappearTime'] = disappear_ms
        line['debug_times'] = {
            'mode': 'raw',
            'E_i': base_ms,
            'T_candidate': base_ms,
            'T_prev_final': base_ms,
            'final': disappear_ms,
            'exit_buffer': exit_buffer,
            'placeholder_buffer': placeholder_buffer,
            'use_computed': use_computed
        }

    # 可选：调试日志
    try:
        app.logger.debug("--- AMLL Disappear Time Calc ---")
        for line in lines:
            dbg = line.get('debug_times', {})
            app.logger.debug(f"E_i:{dbg.get('E_i')} T_cand:{dbg.get('T_candidate')} "
                             f"T_prev:{dbg.get('T_prev_final')} final:{dbg.get('final')}")
        app.logger.debug("--------------------------------")
    except Exception:
        pass

    return lines


def parse_lys(lys_content):
    """
    解析.lys格式的逐音节歌词文件，并计算每行的消失时机（disappearTime，单位毫秒）。
    返回的歌词列表将保持文件中的原始顺序。
    """
    lyrics_data = []
    block_regex = re.compile(r'(.+?)\((\d+),(\d+)\)')
    offset_regex = re.compile(r'\[offset:\s*(-?\d+)\s*\]')
    last_align = 'left'
    offset = 0
    current_font_family: Optional[str] = None
    current_font_map: Dict[str, str] = {}

    # 查找并解析 offset
    offset_match = offset_regex.search(lys_content)
    if offset_match:
        offset = int(offset_match.group(1))

    for line in lys_content.splitlines():
        stripped_line = line.strip()
        font_meta_match = FONT_FAMILY_META_REGEX.match(stripped_line)
        if font_meta_match:
            detected_family = font_meta_match.group(1)
            default_font, lang_map = parse_font_family_meta(detected_family or "")
            current_font_family = default_font
            current_font_map = lang_map or {}
            continue

        # 跳过元数据行
        if stripped_line.startswith('[from:') or stripped_line.startswith('[id:') or stripped_line.startswith('[offset:'):
            continue
        
        # 修改标记解析逻辑：允许空[]，只排除非数字的标记
        content_match = re.match(r'\[(?P<marker>\d*)\](?P<content>.*)', line)
        if not content_match:
            continue
        marker = content_match.group('marker')
        # 新增：如果标记不是空且不是纯数字，则跳过
        if marker != '' and not marker.isdigit():
            continue
        content = content_match.group('content')
        is_background_marker = marker in ['6', '7', '8']
        parenthetical_background = is_parenthetical_background_line(content) if not is_background_marker else False
        is_background = is_background_marker or parenthetical_background

        align = 'left'
        font_size = 'normal'

        if not marker:
            align = 'center'
        elif marker in ['2', '5']:
            align = 'right'

        if is_background:
            align = last_align
            font_size = 'small'
        
        syllables = []
        full_line_text = ""
        detected_scripts: Set[str] = set()
        matches = block_regex.finditer(content)
        for match in matches:
            text_part, start_ms, duration_ms = match.groups()
            cleaned_text = strip_timing_tags(text_part)
            if not parenthetical_background:
                cleaned_text = re.sub(r'[()]', '', cleaned_text)
            if cleaned_text:
                detected_scripts.add(detect_script(cleaned_text))
                syllable_font = choose_font_for_text(cleaned_text, current_font_family, current_font_map)
                syllables.append({
                    'text': cleaned_text,
                    'startTime': (int(start_ms) + offset) / 1000.0, # 应用 offset
                    'duration': int(duration_ms) / 1000.0,
                    'fontFamily': syllable_font
                })
                full_line_text += cleaned_text
        
        if syllables:
            style = {
                'align': align,
                'fontSize': font_size
            }
            if current_font_family:
                style['fontFamily'] = current_font_family
            if current_font_map:
                style['fontFamilyMap'] = dict(current_font_map)
            if current_font_map and not current_font_family:
                suggested_fonts = set()
                for sc in detected_scripts:
                    mapped_font = current_font_map.get(sc)
                    if mapped_font:
                        suggested_fonts.add(mapped_font)
                if suggested_fonts:
                    style['fontFamilySuggested'] = ','.join(sorted(suggested_fonts))

            # 调试输出：展示当前行的字体选择结果，方便排查
            try:
                if style.get('fontFamily') or style.get('fontFamilyMap') or style.get('fontFamilySuggested'):
                    print("[FONT_DEBUG] line=%d scripts=%s family=%s map=%s suggested=%s text=%s" % (
                        len(lyrics_data),
                        ','.join(sorted(s for s in detected_scripts if s)) or '-',
                        style.get('fontFamily') or '-',
                        style.get('fontFamilyMap') or '-',
                        style.get('fontFamilySuggested') or '-',
                        full_line_text[:80]
                    ))
            except Exception:
                pass

            lyrics_data.append({
                'line': full_line_text,
                'syllables': syllables,
                'style': style,
                'isBackground': is_background
            })
        last_align = align
    
    # 如果没有歌词数据，直接返回
    if not lyrics_data:
        return []

    # === 统一用通用函数计算消失时机 ===
    compute_disappear_times(lyrics_data, delta1=500, delta2=0)
    return lyrics_data

@app.route('/')
def index():
    return render_template(
        'LyricSphere.html',
        amll_player_base_url=get_amll_web_player_base_url()
    )

@app.route('/favicon.ico')
def favicon():
    """Serve a fallback favicon so browsers avoid repeated 404 requests."""
    fallback_ico = STATIC_DIR / 'favicon.ico'
    if fallback_ico.exists():
        return send_from_directory(STATIC_DIR, 'favicon.ico')
    return send_from_directory(STATIC_DIR / 'assets', 'icon-128x128.png', mimetype='image/png')


@app.route('/amll-web')
@app.route('/amll-web/')
@app.route('/amll-web/index.html')
def amll_web_player():
    """Serve the AMLL web player template migrated from the standalone Vite build."""
    return render_template('amll_web_player.html')


@app.route('/amll-web/assets/<path:filename>')
def amll_web_assets(filename):
    """Provide asset files under the /amll-web namespace for compatibility with relative requests."""
    return send_from_directory(STATIC_DIR / 'assets', filename)


@app.route('/amll-web/public/<path:filename>')
def amll_web_public(filename):
    """Provide public files (service worker, media tags) under the /amll-web namespace."""
    return send_from_directory(STATIC_DIR / 'public', filename)


@app.route('/index.html')
def index_html_alias():
    """Backward compatibility for clients requesting the original index.html entry."""
    return amll_web_player()


@app.route('/quick-editor')
def quick_editor_page():
    """LYS 顺序快速编辑器入口。"""
    json_file = request.args.get('json', '')
    display_name = Path(json_file).stem if json_file else ''
    preload_data = {}
    if json_file:
        payload, error = quick_editor_load_payload(json_file)
        if payload:
            preload_data = payload
        elif error:
            preload_data = {'status': 'error', 'message': error[0], 'code': error[1]}

    return render_template(
        'lyrics_quick_editor.html',
        json_file=json_file,
        display_name=display_name,
        preload_data=preload_data
    )


# ===== 快速歌词顺序编辑器 API =====
def _qe_get_doc(doc_id: str) -> Tuple[Optional[Dict[str, Any]], Optional[Response]]:
    doc = QUICK_EDITOR_DOCS.get(doc_id)
    if not doc:
        return None, qe_error_response('document not found', 404)
    return doc, None


def _qe_prepare_mutation(doc_id: str, base_version: Optional[int]) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[Response]]:
    doc = QUICK_EDITOR_DOCS.get(doc_id)
    if not doc:
        return None, None, qe_error_response('document not found', 404)
    if doc.get('version') != base_version:
        return None, None, qe_error_response('version conflict', 409)
    before = qe_clone(doc)
    return doc, before, None


def quick_editor_load_payload(json_file: str) -> Tuple[Optional[Dict[str, Any]], Optional[Tuple[str, int]]]:
    """加载并准备快速编辑所需数据，返回 (payload, error)。error: (message, status)."""
    json_path = BASE_PATH / 'static' / json_file
    if not json_path.exists():
        return None, ('找不到对应的歌曲 JSON 文件', 404)

    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            json_data = json.load(f)
    except Exception as exc:
        return None, (f'读取 JSON 失败: {exc}', 500)

    meta = json_data.setdefault('meta', {})
    lyrics_value = meta.get('lyrics', '')
    lyrics_url, translation_url, roman_url, lyrics_parts = parse_meta_lyrics(lyrics_value)

    base_name = sanitize_filename(Path(json_file).stem or meta.get('title', ''))
    if not base_name:
        base_name = sanitize_filename(meta.get('title', '')) or Path(json_file).stem
    if not base_name:
        base_name = f"lyrics_{int(time.time())}"

    try:
        lyrics_relative = extract_resource_relative(lyrics_url, 'songs') if lyrics_url else ''
    except ValueError:
        lyrics_relative = ''

    if not lyrics_relative:
        lyrics_relative = f"{base_name}.lys"

    lyrics_path = SONGS_DIR / lyrics_relative
    translation_relative = None
    json_changed = False

    lyrics_path, trans_path, converted = ensure_lys_file_for_editor(lyrics_path, base_name)
    translation_relative = resource_relative_from_path(trans_path, 'songs') if trans_path else None
    json_changed = json_changed or converted

    try:
        lyrics_relative = resource_relative_from_path(lyrics_path, 'songs')
    except ValueError:
        lyrics_relative = _normalize_relative_path(lyrics_path.name)

    lyrics_url = build_public_url('songs', lyrics_relative)
    translation_url_to_use = translation_url
    if translation_relative:
        translation_url_to_use = build_public_url('songs', translation_relative)
    elif not translation_url_to_use:
        translation_url_to_use = '!'

    new_meta_lyrics = build_meta_lyrics_value(lyrics_parts, lyrics_url, translation_url_to_use, roman_url or None)
    if meta.get('lyrics') != new_meta_lyrics:
        meta['lyrics'] = new_meta_lyrics
        json_changed = True

    song_url = json_data.get('song', '')
    try:
        song_relative = extract_resource_relative(song_url, 'songs')
        song_url = build_public_url('songs', song_relative)
    except Exception:
        song_url = song_url or ''

    if json_changed:
        if not BACKUP_DIR.exists():
            BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        backup_path = build_backup_path(json_path, int(time.time()))
        if json_path.exists():
            shutil.copy2(json_path, backup_path)
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(json_data, f, ensure_ascii=False, indent=2)

    try:
        with open(lyrics_path, 'r', encoding='utf-8') as f:
            raw_lyrics = f.read()
    except Exception:
        raw_lyrics = ''

    doc = qe_parse_lys(raw_lyrics)
    qe_register_doc(doc, {
        'lyrics_path': lyrics_path,
        'json_path': json_path,
        'lyrics_url': lyrics_url,
        'translation_url': translation_url_to_use if translation_url_to_use and translation_url_to_use != '!' else '',
        'song_url': song_url,
        'display_name': Path(json_file).stem
    })

    payload = {
        'status': 'success',
        'doc': doc,
        'jsonFile': json_file,
        'lyricsPath': lyrics_url,
        'translationPath': translation_url_to_use if translation_url_to_use and translation_url_to_use != '!' else '',
        'songUrl': song_url,
        'title': meta.get('title', ''),
        'artists': meta.get('artists', []),
        'updatedJson': json_changed
    }
    return payload, None


@app.route('/quick-editor/api/load', methods=['POST'])
def quick_editor_load():
    if not is_request_allowed():
        return abort(403)
    locked_response = require_unlocked_device('快速编辑歌词')
    if locked_response:
        return locked_response

    data = request.get_json(silent=True) or {}
    json_file = data.get('jsonFile') or data.get('json_file')
    if not json_file:
        return jsonify({'status': 'error', 'message': '缺少 jsonFile 参数'}), 400
    payload, error = quick_editor_load_payload(json_file)
    if error:
        message, status_code = error
        return jsonify({'status': 'error', 'message': message}), status_code
    return jsonify(payload)


@app.route('/quick-editor/api/import', methods=['POST'])
def quick_editor_import():
    if not is_request_allowed():
        return abort(403)
    locked_response = require_unlocked_device('快速编辑歌词')
    if locked_response:
        return locked_response

    if 'file' not in request.files:
        return qe_error_response('missing file', 400)
    file = request.files['file']
    raw_bytes = file.read()
    try:
        raw_text = raw_bytes.decode('utf-8')
    except Exception:
        raw_text = raw_bytes.decode('utf-8', errors='ignore')

    doc = qe_parse_lys(raw_text)
    qe_register_doc(doc)
    return jsonify(doc)


@app.route('/quick-editor/api/lyrics')
def quick_editor_get():
    if not is_request_allowed():
        return abort(403)
    doc_id = request.args.get('doc_id')
    doc, err = _qe_get_doc(doc_id)
    if err:
        return err
    return jsonify(doc)


@app.route('/quick-editor/api/export')
def quick_editor_export():
    if not is_request_allowed():
        return abort(403)
    doc_id = request.args.get('doc_id')
    doc, err = _qe_get_doc(doc_id)
    if err:
        return err
    return Response(qe_dump_lys(doc), mimetype='text/plain; charset=utf-8')


@app.route('/quick-editor/api/move', methods=['POST'])
def quick_editor_move():
    if not is_request_allowed():
        return abort(403)
    payload = request.get_json(silent=True) or {}
    doc_id = payload.get('document_id')
    base_version = payload.get('base_version')
    selection = payload.get('selection') or []
    target = payload.get('target') or {}

    doc, before, err = _qe_prepare_mutation(doc_id, base_version)
    if err:
        return err

    try:
        qe_apply_move(doc, selection, target)
    except MoveError as exc:
        return qe_error_response(str(exc), 409)

    doc["version"] += 1
    QUICK_EDITOR_UNDO.setdefault(doc_id, []).append(before)
    QUICK_EDITOR_REDO.setdefault(doc_id, []).clear()
    return jsonify(doc)


@app.route('/quick-editor/api/undo', methods=['POST'])
def quick_editor_undo():
    if not is_request_allowed():
        return abort(403)
    doc_id = request.args.get('doc_id') or (request.get_json(silent=True) or {}).get('doc_id')
    stack = QUICK_EDITOR_UNDO.get(doc_id) or []
    if not stack:
        return qe_error_response('nothing to undo', 400)
    curr = QUICK_EDITOR_DOCS[doc_id]
    prev = stack.pop()
    QUICK_EDITOR_REDO.setdefault(doc_id, []).append(qe_clone(curr))
    QUICK_EDITOR_DOCS[doc_id] = prev
    return jsonify(prev)


@app.route('/quick-editor/api/redo', methods=['POST'])
def quick_editor_redo():
    if not is_request_allowed():
        return abort(403)
    doc_id = request.args.get('doc_id') or (request.get_json(silent=True) or {}).get('doc_id')
    stack = QUICK_EDITOR_REDO.get(doc_id) or []
    if not stack:
        return qe_error_response('nothing to redo', 400)
    curr = QUICK_EDITOR_DOCS[doc_id]
    nxt = stack.pop()
    QUICK_EDITOR_UNDO.setdefault(doc_id, []).append(qe_clone(curr))
    QUICK_EDITOR_DOCS[doc_id] = nxt
    return jsonify(nxt)


@app.route('/quick-editor/api/newline', methods=['POST'])
def quick_editor_newline():
    if not is_request_allowed():
        return abort(403)
    payload = request.get_json(silent=True) or {}
    doc_id = payload.get('document_id')
    base_version = payload.get('base_version')
    after_id = payload.get('insert_after_line_id')

    doc, before, err = _qe_prepare_mutation(doc_id, base_version)
    if err:
        return err

    new_line = {"id": qe_new_id(), "prefix": "", "is_meta": False, "tokens": []}
    if after_id:
        idx, _ = qe_find_line(doc, after_id)
        doc["lines"].insert(idx + 1, new_line)
    else:
        doc["lines"].insert(0, new_line)

    doc["version"] += 1
    QUICK_EDITOR_UNDO.setdefault(doc_id, []).append(before)
    QUICK_EDITOR_REDO.setdefault(doc_id, []).clear()
    return jsonify(doc)


@app.route('/quick-editor/api/set_prefix', methods=['POST'])
def quick_editor_set_prefix():
    if not is_request_allowed():
        return abort(403)
    payload = request.get_json(silent=True) or {}
    doc_id = payload.get("document_id")
    base_version = payload.get("base_version")
    line_id = payload.get("line_id")
    prefix_int = payload.get("prefix_int")

    doc, before, err = _qe_prepare_mutation(doc_id, base_version)
    if err:
        return err

    try:
        _, line = qe_find_line(doc, line_id)
    except MoveError:
        return qe_error_response('line not found', 404)
    if line.get("is_meta"):
        return qe_error_response('cannot set prefix for meta line', 400)

    if prefix_int is None or str(prefix_int) == "":
        line["prefix"] = "[]"
    else:
        try:
            n = int(prefix_int)
        except Exception:
            return qe_error_response('prefix_int must be an integer or empty', 400)
        if n < 0:
            return qe_error_response('prefix_int must be >= 0', 400)
        line["prefix"] = f"[{n}]"

    doc["version"] += 1
    QUICK_EDITOR_UNDO.setdefault(doc_id, []).append(before)
    QUICK_EDITOR_REDO.setdefault(doc_id, []).clear()
    return jsonify(doc)


@app.route('/quick-editor/api/insert_tokens', methods=['POST'])
def quick_editor_insert_tokens():
    if not is_request_allowed():
        return abort(403)
    payload = request.get_json(silent=True) or {}
    doc_id = payload.get("document_id")
    base_version = payload.get("base_version")
    line_id = payload.get("line_id")
    insert_at = payload.get("insert_at")
    tokens = payload.get("tokens") or []

    doc, before, err = _qe_prepare_mutation(doc_id, base_version)
    if err:
        return err

    try:
        _, line = qe_find_line(doc, line_id)
    except MoveError:
        return qe_error_response('line not found', 404)

    if line.get("is_meta"):
        return qe_error_response('cannot insert tokens into meta line', 400)
    if not isinstance(insert_at, int) or insert_at < 0 or insert_at > len(line.get("tokens", [])):
        return qe_error_response('invalid insert_at', 400)

    new_tokens = []
    for t in tokens:
        text = (t or {}).get("text", "")
        ts = (t or {}).get("ts", "")
        new_tokens.append({"id": qe_new_id(), "ts": ts, "text": text})

    for offset, tok in enumerate(new_tokens):
        line["tokens"].insert(insert_at + offset, tok)

    doc["version"] += 1
    QUICK_EDITOR_UNDO.setdefault(doc_id, []).append(before)
    QUICK_EDITOR_REDO.setdefault(doc_id, []).clear()
    return jsonify(doc)


@app.route('/quick-editor/api/sort_lines', methods=['POST'])
def quick_editor_sort_lines():
    if not is_request_allowed():
        return abort(403)
    payload = request.get_json(silent=True) or {}
    doc_id = payload.get("document_id")
    base_version = payload.get("base_version")

    doc, before, err = _qe_prepare_mutation(doc_id, base_version)
    if err:
        return err

    meta_lines = []
    lyric_lines = []

    for line in doc.get("lines", []):
        if line.get("is_meta"):
            meta_lines.append(line)
        else:
            lyric_lines.append(line)

    def _line_start_time(line):
        if not line.get("tokens"):
            return float('inf')
        first_token = line["tokens"][0]
        ts = first_token.get("ts", "")
        if not ts or "," not in ts:
            return float('inf')
        try:
            start_time = int(ts.split(",")[0])
            return start_time
        except (ValueError, IndexError):
            return float('inf')

    lyric_lines.sort(key=_line_start_time)
    doc["lines"] = meta_lines + lyric_lines
    doc["version"] += 1
    QUICK_EDITOR_UNDO.setdefault(doc_id, []).append(before)
    QUICK_EDITOR_REDO.setdefault(doc_id, []).clear()
    return jsonify(doc)


@app.route('/quick-editor/api/shift_line', methods=['POST'])
def quick_editor_shift_line():
    if not is_request_allowed():
        return abort(403)
    payload = request.get_json(silent=True) or {}
    doc_id = payload.get("document_id")
    base_version = payload.get("base_version")
    line_id = payload.get("line_id")
    try:
        delta_ms = int(payload.get("delta_ms") or 0)
    except Exception:
        return qe_error_response('delta_ms must be an integer', 400)

    doc, before, err = _qe_prepare_mutation(doc_id, base_version)
    if err:
        return err

    try:
        _, line = qe_find_line(doc, line_id)
    except MoveError:
        return qe_error_response('line not found', 404)

    if line.get("is_meta"):
        return qe_error_response('cannot shift meta line', 400)

    for tok in line.get("tokens", []):
        ts = (tok.get("ts") or "").strip()
        if "," not in ts:
            continue
        try:
            s_str, d_str = ts.split(",", 1)
            s, d = int(s_str), int(d_str)
        except Exception:
            continue
        new_start = s + delta_ms
        if new_start < 0:
            new_start = 0
        if new_start != s:
            tok["ts"] = f"{new_start},{d}"

    doc["version"] += 1
    QUICK_EDITOR_UNDO.setdefault(doc_id, []).append(before)
    QUICK_EDITOR_REDO.setdefault(doc_id, []).clear()
    return jsonify(doc)


@app.route('/quick-editor/api/set_last_token_duration', methods=['POST'])
def quick_editor_set_last_token_duration():
    if not is_request_allowed():
        return abort(403)
    payload = request.get_json(silent=True) or {}
    doc_id = payload.get("document_id")
    base_version = payload.get("base_version")
    line_id = payload.get("line_id")
    try:
        new_dur = int(payload.get("duration_ms"))
    except Exception:
        return qe_error_response('duration_ms must be an integer', 400)
    if new_dur < 0:
        return qe_error_response('duration_ms must be >= 0', 400)

    doc, before, err = _qe_prepare_mutation(doc_id, base_version)
    if err:
        return err

    try:
        _, line = qe_find_line(doc, line_id)
    except MoveError:
        return qe_error_response('line not found', 404)

    if line.get("is_meta"):
        return qe_error_response('cannot modify meta line', 400)
    if not line.get("tokens"):
        return qe_error_response('line has no tokens', 400)

    last_tok = None
    last_start = None
    for t in reversed(line.get("tokens", [])):
        ts = (t.get("ts") or "").strip()
        if "," not in ts:
            continue
        try:
            s_str, _ = ts.split(",", 1)
            s = int(s_str)
        except Exception:
            continue
        last_tok = t
        last_start = s
        break

    if last_tok is None or last_start is None:
        return qe_error_response('no valid timestamp token in this line', 400)

    last_tok["ts"] = f"{last_start},{new_dur}"
    doc["version"] += 1
    QUICK_EDITOR_UNDO.setdefault(doc_id, []).append(before)
    QUICK_EDITOR_REDO.setdefault(doc_id, []).clear()
    return jsonify(doc)


@app.route('/quick-editor/api/save', methods=['POST'])
def quick_editor_save():
    if not is_request_allowed():
        return abort(403)
    locked_response = require_unlocked_device('快速编辑歌词')
    if locked_response:
        return locked_response

    payload = request.get_json(silent=True) or {}
    doc_id = payload.get('doc_id') or payload.get('document_id')
    if not doc_id:
        return jsonify({'status': 'error', 'message': '缺少 doc_id'}), 400

    doc = QUICK_EDITOR_DOCS.get(doc_id)
    if not doc:
        return jsonify({'status': 'error', 'message': 'document not found'}), 404

    meta = QUICK_EDITOR_META.get(doc_id) or {}
    lyrics_path: Optional[Path] = meta.get('lyrics_path')
    if not lyrics_path:
        return jsonify({'status': 'error', 'message': '当前文档缺少保存路径，请从管理页进入快速编辑'}), 400

    lyrics_path.parent.mkdir(parents=True, exist_ok=True)
    if lyrics_path.exists():
        backup_path = build_backup_path(lyrics_path, int(time.time()))
        shutil.copy2(lyrics_path, backup_path)

    with open(lyrics_path, 'w', encoding='utf-8') as f:
        f.write(qe_dump_lys(doc))

    try:
        lyrics_relative = resource_relative_from_path(lyrics_path, 'songs')
        lyrics_url = build_public_url('songs', lyrics_relative)
    except Exception:
        lyrics_url = str(lyrics_path)

    return jsonify({'status': 'success', 'lyricsPath': lyrics_url})

# 在Flask应用中添加自定义过滤器
@app.template_filter('escape_js')
def escape_js_filter(s):
    return json.dumps(str(s))[1:-1]  # 移除外层的引号


@app.route('/backup_file', methods=['POST'])
def backup_file():
    if not is_request_allowed():
        return abort(403)
    file_path = request.json.get('file_path')
    if not BACKUP_DIR.exists():
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime(BACKUP_TIMESTAMP_FORMAT)
    backup_path = build_backup_path(Path(file_path), timestamp)
    shutil.copy2(file_path, backup_path)
    return jsonify({'status': 'success'})


@app.route('/delete_json', methods=['POST'])
def delete_json():
    if not is_request_allowed():
        return abort(403)
    locked_response = require_unlocked_device('删除歌曲')
    if locked_response:
        return locked_response
    data = request.json
    filename = data['filename']
    json_path = BASE_PATH / 'static' / filename

    try:
        # 只备份和删除JSON文件本身，不删除关联的歌词、音乐等文件
        delete_backup_dir = BACKUP_DIR / 'permanent'
        delete_backup_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        if json_path.exists():
            # 备份JSON文件
            relative_path = json_path.relative_to(BASE_PATH)
            backup_path = delete_backup_dir / f"{str(relative_path).replace('/', '__')}.{timestamp}"
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(json_path, backup_path)

            # 删除JSON文件
            json_path.unlink()

        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})


@app.route('/export_static', methods=['POST'])
def export_static_bundle():
    if not is_request_allowed():
        return abort(403)
    locked_response = require_unlocked_device('导出分享')
    if locked_response:
        return locked_response

    payload = request.get_json(silent=True) or {}
    filename = payload.get('filename')
    if not filename:
        return jsonify({'status': 'error', 'message': '缺少文件名参数'}), 400

    json_path = STATIC_DIR / filename
    if not json_path.exists():
        return jsonify({'status': 'error', 'message': 'JSON 文件不存在'}), 404

    try:
        with open(json_path, 'r', encoding='utf-8') as json_file:
            json_data = json.load(json_file)
    except Exception as exc:
        return jsonify({'status': 'error', 'message': f'读取 JSON 失败: {exc}'}), 500

    referenced_assets = collect_song_resource_paths(json_data)
    missing_assets = [asset for asset in referenced_assets if not (SONGS_DIR / asset).exists()]
    referenced_assets = {asset for asset in referenced_assets if (SONGS_DIR / asset).exists()}

    archive_buffer = BytesIO()
    try:
        with zipfile.ZipFile(archive_buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(json_path, arcname=json_path.name)
            archive.writestr('songs/', '')
            for asset in sorted(referenced_assets):
                asset_path = SONGS_DIR / asset
                if asset_path.exists():
                    archive.write(asset_path, arcname=f"songs/{asset}".replace('\\', '/'))
            if missing_assets:
                warning_content = "Missing resources during export:\n" + "\n".join(missing_assets)
                archive.writestr('warnings.txt', warning_content)
    except Exception as exc:
        return jsonify({'status': 'error', 'message': f'创建压缩包失败: {exc}'}), 500

    archive_buffer.seek(0)
    response = send_file(
        archive_buffer,
        mimetype='application/zip',
        as_attachment=True,
        download_name='static.zip'
    )
    if missing_assets:
        response.headers['X-Missing-Assets-Count'] = str(len(missing_assets))
    return response


@app.route('/import_static', methods=['POST'])
def import_static_bundle():
    if not is_request_allowed():
        return abort(403)
    locked_response = require_unlocked_device('导入歌曲')
    if locked_response:
        return locked_response

    upload = request.files.get('file')
    if not upload or not upload.filename:
        return jsonify({'status': 'error', 'message': '请上传 static.zip 文件'}), 400

    file_bytes = upload.read()
    if not file_bytes:
        return jsonify({'status': 'error', 'message': '上传文件为空'}), 400

    buffer = BytesIO(file_bytes)
    imported_jsons: List[str] = []
    imported_assets = 0

    try:
        with zipfile.ZipFile(buffer) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue

                name = info.filename.replace('\\', '/')
                if not name or name.startswith('__MACOSX'):
                    continue

                normalized = name.lstrip('/')
                while normalized.startswith('./'):
                    normalized = normalized[2:]
                if normalized.lower().startswith('static/'):
                    normalized = normalized[len('static/'):]
                normalized = normalized.lstrip('/')
                if not normalized:
                    continue

                try:
                    relative_path = _normalize_relative_path(normalized)
                except ValueError:
                    continue

                lower_relative = relative_path.lower()
                if lower_relative.endswith('.json'):
                    target_path = STATIC_DIR / relative_path
                elif lower_relative.startswith('songs/'):
                    target_path = STATIC_DIR / relative_path
                else:
                    continue

                target_path.parent.mkdir(parents=True, exist_ok=True)

                is_json_file = target_path.suffix.lower() == '.json'

                if is_json_file and target_path.exists():
                    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
                    backup_path = build_backup_path(target_path)
                    shutil.copy2(target_path, backup_path)

                with archive.open(info) as source, open(target_path, 'wb') as destination:
                    shutil.copyfileobj(source, destination)

                if is_json_file:
                    imported_jsons.append(target_path.name)
                else:
                    imported_assets += 1

    except zipfile.BadZipFile:
        return jsonify({'status': 'error', 'message': '文件不是有效的 ZIP 压缩包'}), 400

    if not imported_jsons:
        return jsonify({'status': 'error', 'message': '压缩包中未发现 JSON 文件'}), 400

    return jsonify({
        'status': 'success',
        'message': f'导入完成。JSON: {len(imported_jsons)} 个，资源文件: {imported_assets} 个',
        'jsonFiles': imported_jsons
    })


@app.route('/restore_file', methods=['POST'])
def restore_file():
    if not is_request_allowed():
        return abort(403)
    file_path = request.json.get('file_path')
    try:
        if not file_path:
            return jsonify({'status': 'error', 'message': '缺少文件路径'})

        # 如果是备份文件路径
        if '/backups/' in file_path:
            backup_path = resolve_resource_path(file_path, 'backups')
            original_name = '.'.join(backup_path.name.split('.')[:-1])
            restore_path = (STATIC_DIR / original_name).resolve()
            restore_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup_path, restore_path)
        else:
            target_path = resolve_resource_path(file_path, 'static')
            if not target_path.exists():
                return jsonify({'status': 'error', 'message': '目标文件不存在'})

            # 获取所有关联文件备份
            related_files = get_related_files(target_path)  # 新增关联文件获取方法
            backups = []

            # 为每个关联文件创建恢复任务
            for file in related_files:
                file_backups = []
                prefix = backup_prefix(Path(file))
                for backup in BACKUP_DIR.iterdir():
                    if backup.is_file() and backup.name.startswith(prefix):
                        file_backups.append(backup)

                if not file_backups:
                    continue

                file_backups.sort(reverse=True)
                latest_backup = file_backups[0]
                shutil.copy2(latest_backup, file)  # 恢复文件

                # 清理旧备份保持7个版本
                for old_backup in file_backups[7:]:
                    old_backup.unlink()

        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})


def get_related_files(json_path):
    """获取与JSON文件关联的所有文件路径"""
    json_path_obj = Path(json_path)
    related_files = [json_path_obj]

    try:
        with open(json_path_obj, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # 获取歌词相关文件
        lyrics_info = data['meta'].get('lyrics', '').split('::')
        for path in lyrics_info[1:4]:  # 歌词、翻译、音译路径
            if path and path != '!':
                try:
                    local_path = resolve_resource_path(path, 'songs')
                    related_files.append(local_path)
                except ValueError:
                    app.logger.warning(f"忽略无法解析的歌词路径: {path}")

        # 获取音频文件
        if 'song' in data:
            try:
                local_music = resolve_resource_path(data['song'], 'songs')
                related_files.append(local_music)
            except ValueError:
                app.logger.warning(f"忽略无法解析的音频路径: {data['song']}")

        # 获取专辑图
        if 'albumImgSrc' in data['meta']:
            try:
                local_img = resolve_resource_path(data['meta']['albumImgSrc'], 'songs')
                related_files.append(local_img)
            except ValueError:
                app.logger.warning(f"忽略无法解析的专辑图路径: {data['meta']['albumImgSrc']}")

    except Exception as e:
        print(f"Error getting related files: {str(e)}")

    unique_files = []
    seen = set()
    for file in related_files:
        resolved = Path(file).resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique_files.append(resolved)
    return unique_files  # 去重


@app.route('/update_json', methods=['POST'])
def update_json():
    if not is_request_allowed():
        return abort(403)
    locked_response = require_unlocked_device('更新歌曲信息')
    if locked_response:
        return locked_response
    data = request.json
    file_path = BASE_PATH / 'static' / data["filename"]

    try:
        # 确保备份目录存在
        if not BACKUP_DIR.exists():
            BACKUP_DIR.mkdir(parents=True, exist_ok=True)

        # 备份原文件
        backup_path = build_backup_path(data['filename'], int(time.time()))
        if file_path.exists():  # 只在文件存在时进行备份
            shutil.copy2(file_path, backup_path)

        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data['content'], f, ensure_ascii=False, indent=2)

        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})


@app.route('/save_lyrics', methods=['POST'])
def save_lyrics():
    if not is_request_allowed():
        return abort(403)
    locked_response = require_unlocked_device('保存歌词')
    if locked_response:
        return locked_response
    try:
        data = request.json
        if not data or 'path' not in data or 'content' not in data:
            app.logger.error("无效的请求数据: 缺少必要的字段")
            return jsonify({'status': 'error', 'message': '无效的请求数据'})

        # 验证路径
        if not data['path'] or data['path'] == '.' or data['path'] == './':
            app.logger.error(f"无效的文件路径: {data['path']}")
            return jsonify({'status': 'error', 'message': '无效的文件路径'})

        try:
            file_path = resolve_resource_path(data['path'], 'songs')
        except ValueError as exc:
            app.logger.error(f"无效的路径格式: {data['path']}，错误: {exc}")
            return jsonify({'status': 'error', 'message': '无效的路径格式'})

        content = data['content']
        content_to_write = content
        if file_path.suffix.lower() == '.ttml':
            try:
                content_to_write = sanitize_ttml_content(content)
            except Exception as exc:
                app.logger.error(f"TTML 白名单净化失败: {exc}")
                return jsonify({'status': 'error', 'message': f'TTML 解析失败，未保存: {exc}'})

        # 验证文件名
        if not file_path.name or file_path.name in ('.', '..'):
            app.logger.error(f"无效的文件名: {file_path.name}")
            return jsonify({'status': 'error', 'message': '无效的文件名'})

        if file_path.is_dir():
            app.logger.error(f"路径指向目录而非文件: {file_path}")
            return jsonify({'status': 'error', 'message': '请选择具体文件'})

        # 修改保存逻辑，添加目录创建
        file_dir = file_path.parent
        if not file_dir.exists():
            try:
                file_dir.mkdir(parents=True, exist_ok=True)
                app.logger.info(f"创建目录成功: {file_dir}")
            except Exception as e:
                app.logger.error(f"创建目录失败: {file_dir}, 错误: {str(e)}, 权限: {oct(file_dir.parent.stat().st_mode)[-3:] if file_dir.parent.exists() else 'N/A'}")
                return jsonify({'status': 'error', 'message': f'创建目录失败: {str(e)}'})

        # 如果文件不存在则创建
        if not file_path.exists():
            try:
                open(file_path, 'w', encoding='utf-8').close()
                app.logger.info(f"创建文件成功: {file_path}")
            except Exception as e:
                app.logger.error(f"创建文件失败: {file_path}, 错误: {str(e)}, 权限: {oct(file_path.parent.stat().st_mode)[-3:] if file_path.parent.exists() else 'N/A'}")
                return jsonify({'status': 'error', 'message': f'创建文件失败: {str(e)}'})

        # 扩展备份逻辑：同时备份关联的JSON文件
        if '/songs/' in data['path']:
            json_files = find_related_json(str(file_path))  # 新增查找关联JSON方法
            for json_file in json_files:
                try:
                    json_path = Path(json_file)
                    if not json_path.is_absolute():
                        app.logger.warning(f"跳过无效的JSON文件路径: {json_file}")
                        continue
                    backup_path = build_backup_path(json_path, int(time.time()))
                    shutil.copy2(json_path, backup_path)
                    app.logger.info(f"备份JSON文件成功: {json_path} -> {backup_path}")
                except Exception as e:
                    app.logger.error(f"备份JSON文件失败: {json_file}, 错误: {str(e)}, 权限: {oct(Path(json_file).parent.stat().st_mode)[-3:] if Path(json_file).parent.exists() else 'N/A'}")

        # 备份管理(保留最近7个版本)
        if file_path.exists():
            try:
                # 确保备份目录存在
                if not BACKUP_DIR.exists():
                    try:
                        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
                        app.logger.info(f"创建备份目录成功: {BACKUP_DIR}")
                    except Exception as e:
                        app.logger.error(f"创建备份目录失败: {BACKUP_DIR}, 错误: {str(e)}, 权限: {oct(BACKUP_DIR.parent.stat().st_mode)[-3:] if BACKUP_DIR.parent.exists() else 'N/A'}")
                        raise
                    
                # 获取普通备份文件（不包括permanent目录）
                prefix = backup_prefix(file_path)
                backups = sorted([
                    f for f in BACKUP_DIR.iterdir()
                    if f.is_file() and f.name.startswith(prefix)
                ], reverse=True)
                
                # 删除旧备份(保留6个历史版本+当前版本)
                for old_backup in backups[6:]:
                    try:
                        old_backup.unlink()
                        app.logger.info(f"删除旧备份成功: {old_backup}")
                    except PermissionError:
                        app.logger.error(f"删除旧备份失败(权限不足): {old_backup}, 权限: {oct(old_backup.parent.stat().st_mode)[-3:] if old_backup.parent.exists() else 'N/A'}")
                        continue
                    except Exception as e:
                        app.logger.error(f"删除旧备份失败: {old_backup}, 错误: {str(e)}, 权限: {oct(old_backup.parent.stat().st_mode)[-3:] if old_backup.parent.exists() else 'N/A'}")
                        continue
                        
                # 创建新备份
                timestamp = datetime.now().strftime(BACKUP_TIMESTAMP_FORMAT)
                backup_path = build_backup_path(file_path, timestamp)
                try:
                    shutil.copy2(file_path, backup_path)
                    app.logger.info(f"创建新备份成功: {file_path} -> {backup_path}")
                except Exception as e:
                    app.logger.error(f"创建新备份失败: {file_path} -> {backup_path}, 错误: {str(e)}, 权限: {oct(backup_path.parent.stat().st_mode)[-3:] if backup_path.parent.exists() else 'N/A'}")
                    raise
            except Exception as e:
                app.logger.error(f"备份过程中出错: {str(e)}, 文件: {file_path}, 备份目录: {BACKUP_DIR}, 权限: {oct(BACKUP_DIR.stat().st_mode)[-3:] if BACKUP_DIR.exists() else 'N/A'}")
                # 继续执行，不中断保存操作
                return jsonify({
                    'status': 'warning',
                    'message': '文件已保存，但备份过程中出现错误，可能无法创建新的备份。（重启一下电脑也许就解决了（'
                })

        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content_to_write)
            app.logger.info(f"保存文件成功: {file_path}")
            return jsonify({'status': 'success'})
        except Exception as e:
            app.logger.error(f"保存文件失败: {file_path}, 错误: {str(e)}, 权限: {oct(file_path.parent.stat().st_mode)[-3:] if file_path.parent.exists() else 'N/A'}")
            return jsonify({'status': 'error', 'message': str(e)})
    except Exception as e:
        app.logger.error(f"处理保存请求时出错: {str(e)}")
        return jsonify({'status': 'error', 'message': f'处理请求时出错: {str(e)}'})


def find_related_json(lyrics_path):
    """查找引用该歌词文件的JSON文件"""
    related_jsons = []
    static_dir = BASE_PATH / 'static'
    lyrics_path = Path(lyrics_path)
    
    # 确保歌词路径是绝对路径
    if not lyrics_path.is_absolute():
        app.logger.warning(f"歌词路径不是绝对路径: {lyrics_path}")
        return related_jsons
        
    # 获取歌词文件的相对路径（用于匹配）
    try:
        lyrics_relative = lyrics_path.relative_to(SONGS_DIR)
    except ValueError:
        app.logger.warning(f"歌词文件不在songs目录中: {lyrics_path}")
        return related_jsons

    for json_file in static_dir.iterdir():
        if json_file.suffix == '.json':
            try:
                with open(json_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    lyrics_fields = data['meta'].get('lyrics', '').split('::')
                    # 检查每个字段是否包含当前歌词文件的相对路径
                    if any(str(lyrics_relative) in field for field in lyrics_fields):
                        related_jsons.append(str(json_file))
            except Exception as e:
                app.logger.warning(f"处理JSON文件时出错 {json_file}: {str(e)}")
                continue
    return related_jsons


@app.route('/update_file_path', methods=['POST'])
def update_file_path():
    if not is_request_allowed():
        return abort(403)
    locked_response = require_unlocked_device('修改文件路径')
    if locked_response:
        return locked_response
    data = request.json
    json_path = BASE_PATH / 'static' / data['jsonFile']
    file_type = data['fileType']
    new_path = data['newPath']

    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            json_data = json.load(f)

        # 确保备份目录存在
        if not BACKUP_DIR.exists():
            BACKUP_DIR.mkdir(parents=True, exist_ok=True)

        # 备份原文件
        timestamp = int(time.time())
        backup_path = build_backup_path(data['jsonFile'], timestamp)
        if json_path.exists():  # 只在文件存在时进行备份
            shutil.copy2(json_path, backup_path)

        normalized_new_path = ''
        if new_path:
            try:
                normalized_new_path = _normalize_relative_path(new_path)
            except ValueError:
                return jsonify({'status': 'error', 'message': '文件路径包含非法字符'})

        # 更新路径
        if file_type == 'music':
            json_data['song'] = build_public_url('songs', normalized_new_path)
        elif file_type == 'image':
            json_data['meta']['albumImgSrc'] = build_public_url('songs', normalized_new_path)
        elif file_type == 'background':
            meta = json_data.setdefault('meta', {})
            if new_path:
                normalized_background_path = normalized_new_path
                if normalized_background_path.startswith('songs/'):
                    normalized_background_path = normalized_background_path[len('songs/'):]
                meta['Background-image'] = f"./songs/{normalized_background_path}" if normalized_background_path else ''
            else:
                meta['Background-image'] = ''
        elif file_type == 'lyrics':
            current_lyrics = json_data['meta']['lyrics'].split('::')
            if len(current_lyrics) >= 4:
                if data.get('index') == 0:  # 歌词文件
                    new_lyrics_path = build_public_url('songs', normalized_new_path) if new_path else '!'
                    current_lyrics[1] = new_lyrics_path
                elif data.get('index') == 1:  # 歌词翻译
                    new_translation_path = build_public_url('songs', normalized_new_path) if new_path else '!'
                    current_lyrics[2] = new_translation_path
                elif data.get('index') == 2:  # 歌词音译
                    new_transliteration_path = build_public_url('songs', normalized_new_path) if new_path else '!'
                    current_lyrics[3] = new_transliteration_path
                json_data['meta']['lyrics'] = '::'.join(current_lyrics)

        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(json_data, f, ensure_ascii=False, indent=2)

        # 在更新路径后添加文件创建逻辑
        if normalized_new_path:
            new_local_path = SONGS_DIR / normalized_new_path
            if not new_local_path.parent.exists():
                new_local_path.parent.mkdir(parents=True, exist_ok=True)

        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})


@app.route('/create_json', methods=['POST'])
def create_json():
    if not is_request_allowed():
        return abort(403)
    locked_response = require_unlocked_device('创建新歌')
    if locked_response:
        return locked_response
    data = request.json
    filename = data['filename']
    file_path = BASE_PATH / 'static' / filename

    try:
        if file_path.exists():
            return jsonify({'status': 'error', 'message': '文件已存在！'})

        file_path.parent.mkdir(parents=True, exist_ok=True)
        print(f"创建JSON文件: {file_path}")

        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data['content'], f, ensure_ascii=False, indent=2)

        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})


@app.route('/rename_json', methods=['POST'])
def rename_json():
    if not is_request_allowed():
        return abort(403)
    locked_response = require_unlocked_device('重命名歌曲')
    if locked_response:
        return locked_response
    data = request.json
    old_filename = data['oldFilename']
    new_filename = data['newFilename']
    title = data['title']
    artists = data['artists']

    old_path = BASE_PATH / 'static' / old_filename
    # 清理文件名，替换全角引号
    new_filename = sanitize_filename(new_filename)
    if not new_filename:
        return jsonify({'status': 'error', 'message': '文件名包含非法字符或为空'})
    new_path = BASE_PATH / 'static' / new_filename

    try:
        # 检查新文件名是否已存在
        if new_path.exists() and str(old_path).lower() != str(new_path).lower():
            return jsonify({'status': 'error', 'message': '文件名已存在！'})

        # 确保备份目录存在
        if not BACKUP_DIR.exists():
            BACKUP_DIR.mkdir(parents=True, exist_ok=True)

        # 读取原文件内容
        with open(old_path, 'r', encoding='utf-8') as f:
            json_data = json.load(f)

        # 备份原文件
        timestamp = int(time.time())
        backup_path = build_backup_path(old_filename, timestamp)
        shutil.copy2(old_path, backup_path)

        # 更新JSON内容
        json_data['meta']['title'] = title
        json_data['meta']['artists'] = artists

        # 写入新文件
        with open(new_path, 'w', encoding='utf-8') as f:
            json.dump(json_data, f, ensure_ascii=False, indent=2)

        # 如果新旧文件名不同，删除旧文件
        if str(old_path).lower() != str(new_path).lower():
            old_path.unlink()

        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})


@app.route('/check_lyrics', methods=['POST'])
def check_lyrics():
    if not is_request_allowed():
        return abort(403)
    locked_response = require_unlocked_device('检测歌词内容')
    if locked_response:
        return locked_response
    lyrics_path = request.json.get('path')
    try:
        has_duet, has_background = analyze_lyrics_tags(lyrics_path)
    except ValueError as exc:
        return jsonify({'status': 'error', 'message': f'无法解析歌词路径: {exc}'})
    except Exception as exc:
        return jsonify({'status': 'error', 'message': str(exc)})

    return jsonify({
        'status': 'success',
        'hasDuet': has_duet,
        'hasBackgroundVocals': has_background
    })


@app.route('/songs/summary')
def list_song_summaries():
    """返回精简的歌曲列表信息，避免前端批量读取静态资源。"""
    if not is_request_allowed():
        return abort(403)
    locked_response = require_unlocked_device('查看歌曲列表')
    if locked_response:
        return locked_response

    summaries: List[Dict[str, Any]] = []
    for file in STATIC_DIR.glob('*.json'):

        try:
            raw_data = json.loads(file.read_text(encoding='utf-8'))
        except Exception as exc:
            app.logger.warning("读取 JSON 失败，已跳过 %s: %s", file.name, exc)
            continue

        if not isinstance(raw_data, dict):
            continue

        meta = raw_data.get('meta') or {}
        if not isinstance(meta, dict):
            meta = {}

        lyrics_path, translation_path, roman_path, _ = parse_meta_lyrics(meta.get('lyrics'))

        artists_raw = meta.get('artists', [])
        if isinstance(artists_raw, list):
            artists_list = artists_raw
        elif isinstance(artists_raw, str):
            artists_list = [artists_raw]
        else:
            artists_list = []

        try:
            has_duet, has_background = analyze_lyrics_tags(lyrics_path)
        except Exception as exc:
            app.logger.warning("检测歌词标签失败，已跳过标签标记 %s: %s", lyrics_path, exc)
            has_duet = False
            has_background = False

        song_value = str(raw_data.get('song', '')).strip()
        summary = {
            'filename': file.name,
            'title': meta.get('title', ''),
            'artists': artists_list,
            'lyricsPath': lyrics_path,
            'translationPath': translation_path,
            'romanPath': roman_path,
            'metaLyrics': meta.get('lyrics', ''),
            'song': song_value,
            'albumImgSrc': meta.get('albumImgSrc', ''),
            'backgroundImage': meta.get('Background-image', ''),
            'hasDuet': has_duet,
            'hasBackgroundVocals': has_background,
            'hasAudio': has_valid_audio(song_value),
            'mtime': file.stat().st_mtime
        }
        summaries.append(summary)

    summaries.sort(key=lambda item: item.get('mtime', 0), reverse=True)
    return jsonify({'status': 'success', 'songs': summaries})


@app.route('/get_backups', methods=['POST'])
def get_backups():
    try:
        file_path = resolve_resource_path(request.json.get('path'), 'songs')
    except ValueError as exc:
        return jsonify({'status': 'error', 'message': f'无法解析文件路径: {exc}'})

    prefix = backup_prefix(file_path)

    try:
        collected = []
        prefix_len = len(prefix)
        for f in BACKUP_DIR.iterdir():
            if not f.is_file() or not f.name.startswith(prefix):
                continue

            timestamp_part = f.name[prefix_len:]
            parsed_time: Optional[datetime] = None
            try:
                parsed_time = datetime.strptime(timestamp_part, BACKUP_TIMESTAMP_FORMAT)
            except ValueError:
                if timestamp_part.isdigit():
                    try:
                        parsed_time = datetime.fromtimestamp(int(timestamp_part))
                    except (OSError, OverflowError, ValueError):
                        parsed_time = None

            if not parsed_time:
                continue

            collected.append({
                'dt': parsed_time,
                'name': f.name
            })

        collected.sort(key=lambda item: item['dt'], reverse=True)
        limited = []
        for item in collected[:7]:
            limited.append({
                'path': build_public_url('backups', item['name']),
                'time': item['dt'].strftime('%Y-%m-%d %H:%M:%S')
            })
        return jsonify({'status': 'success', 'backups': limited})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})


@app.route('/upload_music', methods=['POST'])
def upload_music():
    if not is_request_allowed():
        return abort(403)
    try:
        if 'file' not in request.files:
            return jsonify({'status': 'error', 'message': '没有选择文件'})

        file = request.files['file']
        if file.filename == '':
            return jsonify({'status': 'error', 'message': '无效的文件名'})

        # 允许所有音频视频格式
        file_ext = Path(file.filename).suffix.lower()

        # 清理文件名，保留空格与安全字符
        clean_name = sanitize_filename(file.filename)
        if not clean_name:
            return jsonify({'status': 'error', 'message': '文件名包含非法字符或为空'})
        save_path = SONGS_DIR / clean_name

        # 如果文件已存在则覆盖
        file.save(save_path)

        return jsonify({'status': 'success', 'filename': clean_name})

    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})


@app.route('/upload_image', methods=['POST'])
def upload_image():
    if not is_request_allowed():
        return abort(403)
    try:
        if 'file' not in request.files:
            return jsonify({'status': 'error', 'message': '没有选择文件'})

        file = request.files['file']
        if file.filename == '':
            return jsonify({'status': 'error', 'message': '无效的文件名'})

        # 验证文件类型
        allowed_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.webp'}
        file_ext = Path(file.filename).suffix.lower()
        if file_ext not in allowed_extensions:
            return jsonify({
                'status': 'error',
                'message': '只支持 JPG/PNG/GIF/WEBP 格式'
            })

        # 清理文件名，保留空格与安全字符
        clean_name = sanitize_filename(file.filename)
        if not clean_name:
            return jsonify({'status': 'error', 'message': '文件名包含非法字符或为空'})
        save_path = SONGS_DIR / clean_name

        # 如果文件已存在则覆盖
        file.save(save_path)

        return jsonify({'status': 'success', 'filename': clean_name})

    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})


@app.route('/upload_lyrics', methods=['POST'])
def upload_lyrics():
    if not is_request_allowed():
        return abort(403)
    client_ip = request.remote_addr
    user_agent = request.headers.get('User-Agent', 'Unknown')
    username = "Anonymous"  # 可根据实际登录系统替换

    try:
        if 'file' not in request.files:
            app.logger.error(
                f'[{client_ip}] {username} 上传失败: 未选择文件 | UA: {user_agent}')
            return jsonify({'status': 'error', 'message': '没有选择文件'})

        file = request.files['file']
        if file.filename == '':
            app.logger.error(
                f'[{client_ip}] {username} 上传失败: 空文件名 | UA: {user_agent}')
            return jsonify({'status': 'error', 'message': '无效的文件名'})

        # 验证文件类型
        allowed_extensions = {'.lrc', '.lys', '.ttml'}
        file_ext = Path(file.filename).suffix.lower()
        if file_ext not in allowed_extensions:
            app.logger.warning(
                f'[{client_ip}] {username} 尝试上传非法类型: {file_ext} | 文件名: {file.filename}'
            )
            return jsonify({'status': 'error', 'message': '只支持 LRC/LYS/ttml 格式'})

        # 清理文件名，保留空格与安全字符
        clean_name = sanitize_filename(file.filename)
        if not clean_name:
            return jsonify({'status': 'error', 'message': '文件名包含非法字符或为空'})
        save_path = SONGS_DIR / clean_name

        # 记录上传开始
        app.logger.info(
            f'[{client_ip}] {username} 开始上传歌词: {clean_name} | 大小: {len(file.read())}字节'
        )
        file.seek(0)  # 重置文件指针

        # 如果文件已存在则覆盖
        file.save(save_path)

        # 获取文件元信息
        file_size = save_path.stat().st_size
        checksum = hashlib.md5(file.read()).hexdigest()
        file.seek(0)

        app.logger.info(
            f'[{client_ip}] {username} 上传成功: {clean_name} | 大小: {file_size}字节 | MD5: {checksum}'
        )

        return jsonify({'status': 'success', 'filename': clean_name})

    except Exception as e:
        error_msg = f'[{client_ip}] {username} 上传失败: {str(e)} | 文件: {file.filename}'
        app.logger.error(error_msg, exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)})


@app.route('/upload_translation', methods=['POST'])
def upload_translation():
    if not is_request_allowed():
        return abort(403)
    client_ip = request.remote_addr
    user_agent = request.headers.get('User-Agent', 'Unknown')
    username = "Anonymous"

    try:
        if 'file' not in request.files:
            app.logger.error(
                f'[{client_ip}] {username} 翻译上传失败: 未选择文件 | UA: {user_agent}')
            return jsonify({'status': 'error', 'message': '没有选择文件'})

        file = request.files['file']
        if file.filename == '':
            app.logger.error(
                f'[{client_ip}] {username} 翻译上传失败: 空文件名 | UA: {user_agent}')
            return jsonify({'status': 'error', 'message': '无效的文件名'})

        # 验证文件类型
        allowed_extensions = {'.lrc', '.lys', '.ttml'}
        file_ext = Path(file.filename).suffix.lower()
        if file_ext not in allowed_extensions:
            app.logger.warning(
                f'[{client_ip}] {username} 尝试上传非法翻译类型: {file_ext} | 文件名: {file.filename}'
            )
            return jsonify({'status': 'error', 'message': '只支持 LRC/LYS/ttml 格式'})

        # 清理文件名，保留空格与安全字符
        clean_name = sanitize_filename(file.filename)
        if not clean_name:
            return jsonify({'status': 'error', 'message': '文件名包含非法字符或为空'})
        save_path = SONGS_DIR / clean_name

        # 记录上传开始
        app.logger.info(
            f'[{client_ip}] {username} 开始上传翻译: {clean_name} | 大小: {len(file.read())}字节'
        )
        file.seek(0)

        # 保存文件
        file.save(save_path)

        # 获取文件元信息
        file_size = save_path.stat().st_size
        checksum = hashlib.md5(file.read()).hexdigest()
        file.seek(0)

        app.logger.info(
            f'[{client_ip}] {username} 翻译上传成功: {clean_name} | 大小: {file_size}字节 | MD5: {checksum}'
        )

        return jsonify({'status': 'success', 'filename': clean_name})

    except Exception as e:
        error_msg = f'[{client_ip}] {username} 翻译上传失败: {str(e)} | 文件: {file.filename}'
        app.logger.error(error_msg, exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)})


# TTML转换相关类和函数
class TTMLTime:
    _pattern: Pattern = compile(r'\d+')

    def __init__(self, centi: str = ''):
        if centi == '':
            # 默认初始化为 00:00.000，避免属性缺失
            self._minute = 0
            self._second = 0
            self._micros = 0
            return
        # 使用 finditer 获取匹配的迭代器
        matches: Iterator[Match[str]] = TTMLTime._pattern.finditer(centi)
        # 获取下一个匹配
        iterator: Iterator[Match[str]] = iter(matches)  # 将匹配对象转换为迭代器

        self._minute:int = int(next(iterator).group())
        self._second:int = int(next(iterator).group())
        self._micros:int = int(next(iterator).group())

    def __str__(self) -> str:
        return f'{self._minute:02}:{self._second:02}.{self._micros:03}'

    def __int__(self) -> int:
        return (self._minute * 60 + self._second) * 1000 + self._micros

    def __ge__(self, other) -> bool:
        return (self._minute, self._second, self._micros) >= (other._minute, other._second, other._micros)

    def __ne__(self, other) -> bool:
        return (self._minute, self._second, self._micros) != (other._minute, other._second, other._micros)

    def __sub__(self, other) -> int:
        return abs(int(self) - int(other))

class TTMLSyl:
    def __init__(self, element: Element):
        self.__element: Element = element
        self.__begin: TTMLTime = TTMLTime(element.getAttribute("begin"))
        self.__end: TTMLTime = TTMLTime(element.getAttribute("end"))

        # ✅ 安全访问子节点文本
        node_val = None
        try:
            if element.childNodes and element.childNodes.length > 0:
                first = element.childNodes[0]
                # 3 = TEXT_NODE, 4 = CDATA_SECTION_NODE
                if getattr(first, "nodeType", None) in (3, 4):
                    node_val = first.nodeValue
        except Exception:
            node_val = None
        self.text: str = node_val or ""

    def __str__(self) -> str:
        return f'{self.text}({int(self.__begin)},{self.__end - self.__begin})'

    def get_begin(self) -> TTMLTime:
        return self.__begin

    # 👉 新增：
    def get_end(self) -> TTMLTime:
        return self.__end

class TTMLLine:
    have_ts: bool = False
    have_duet: bool = False
    have_bg: bool = False
    have_pair: int = 0

    __before: Pattern[AnyStr] = compile(r'^\({2,}')
    __after: Pattern[AnyStr] = compile(r'\){2,}$')

    def __init__(self, element: Element, is_bg: bool = False):
        self.__element: Element = element
        self.__orig_line = []  # 可以包含TTMLSyl或str类型
        self.__ts_line = None  # 可以是str或None
        self.__bg_line = None  # 可以是TTMLLine或None
        self.__is_bg: bool = is_bg

        TTMLLine.have_bg |= is_bg

        # 获取传入元素的 agent 属性
        agent = element.getAttribute("ttm:agent")
        self.__is_duet:bool = bool(agent and agent != 'v1')

        # 获取 <p> 元素的所有子节点，包括文本节点
        child_elements = element.childNodes  # iter() 会返回所有子元素和文本节点

        # 遍历所有子节点
        for child in child_elements:
            # TEXT_NODE
            if getattr(child, "nodeType", None) == 3 and getattr(child, "nodeValue", None) is not None:
                # 仅合并空白字符，避免把括号等可见字符黏到前一音节
                if len(self.__orig_line) > 0 and len(child.nodeValue) < 2 and not child.nodeValue.strip():
                    try:
                        last = self.__orig_line[-1]
                        if isinstance(last, TTMLSyl):
                            last.text = (last.text or "") + child.nodeValue
                        elif isinstance(last, str):
                            self.__orig_line[-1] = last + child.nodeValue
                    except Exception:
                        pass
                else:
                    self.__orig_line.append(child.nodeValue)
                continue

            # 只处理 ELEMENT_NODE
            if getattr(child, "nodeType", None) != 1:
                continue

            role = child.getAttribute("ttm:role") if child.hasAttribute("ttm:role") else ""

            if role == "":
                # 普通 syllable：必须有文本子节点
                if child.childNodes and child.childNodes.length > 0:
                    try:
                        # TTMLSyl 内部也做了判空
                        self.__orig_line.append(TTMLSyl(child))
                    except Exception as e:
                        app.logger.debug(f"TTMLSyl 构造跳过空节点: {e!r}")
                continue

            if role == "x-bg":
                self.__bg_line = TTMLLine(child, True)
                self.__bg_line.__is_duet = self.__is_duet
                continue

            if role == "x-translation":
                TTMLLine.have_ts = True
                try:
                    if child.childNodes and child.childNodes.length > 0:
                        first = child.childNodes[0]
                        if getattr(first, "nodeType", None) in (3, 4) and first.nodeValue:
                            self.__ts_line = f'{first.nodeValue}'
                except Exception as e:
                    app.logger.debug(f"翻译行解析失败：{e!r}")
                continue

        # ✅ 正确设置本行 begin/end
        if self.__orig_line and isinstance(self.__orig_line[0], TTMLSyl):
            self.__begin = self.__orig_line[0].get_begin()
            # 取该行最后一个 syl 的 end 更稳妥
            last_syl = next((x for x in reversed(self.__orig_line) if isinstance(x, TTMLSyl)), None)
            self.__end = last_syl.get_end() if last_syl else self.__begin
        else:
            # 纯文本 p：直接读 p 的属性
            self.__begin = TTMLTime(element.getAttribute("begin"))
            self.__end   = TTMLTime(element.getAttribute("end"))
            if not self.__orig_line:
                self.__orig_line.append('')

        if is_bg and self.__orig_line and isinstance(self.__orig_line[0], TTMLSyl):
            if TTMLLine.__before.search(self.__orig_line[0].text):
                self.__orig_line[0].text = TTMLLine.__before.sub('(', self.__orig_line[0].text)
                TTMLLine.have_pair += 1
            if TTMLLine.__after.search(self.__orig_line[-1].text):
                self.__orig_line[-1].text = TTMLLine.__after.sub(')', self.__orig_line[-1].text)
                TTMLLine.have_pair += 1

    def __role(self) -> int:
        return ((int(TTMLLine.have_bg) + int(self.__is_bg)) * 3
                + int(TTMLLine.have_duet) + int(self.__is_duet))

    def __raw(self):
        try:
            filtered_line = []
            has_syl = False
            for v in self.__orig_line:
                if isinstance(v, str):
                    if v.strip():
                        # 仅括号的文本片段保留原文，不参与时间渲染
                        filtered_line.append(v if not BRACKET_ONLY_PATTERN.match(v) else v.strip())
                else:
                    has_syl = True
                    # 括号音节不需要附带时间戳，直接保留文本
                    if BRACKET_ONLY_PATTERN.match(v.text or ''):
                        filtered_line.append((v.text or '').strip())
                    else:
                        filtered_line.append(v)

            line_text = ''.join([str(v) for v in filtered_line]) if filtered_line else ''

            # 👉 纯文本行：补上 (begin,duration)
            if not has_syl and line_text:
                duration_ms = self.__end - self.__begin
                line_text = f"{line_text}({int(self.__begin)},{duration_ms})"

            main_line = f'[{self.__role()}]{line_text}'
            translation_line = None
            if not self.__is_bg and self.__ts_line:
                translation_line = f'[{self.__begin}]{self.__ts_line}'
            return (main_line, translation_line)
        except Exception as e:
            app.logger.error(f"生成歌词行时出错: {str(e)}")
            return (f'[{self.__role()}]错误的行', None)

    def to_str(self):
        # 返回元组(元组(str, str或None), 元组(str, str或None)或None)
        return self.__raw(), (self.__bg_line.__raw() if self.__bg_line else None)

def ttml_to_lys(input_path, songs_dir):
    """主转换函数"""
    TTMLLine.have_duet = False
    TTMLLine.have_bg = False
    TTMLLine.have_ts = False
    TTMLLine.have_pair = 0

    lyric_path = ''
    trans_path = ''
    try:
        try:
            with open(input_path, 'r', encoding='utf-8', errors='ignore') as f:
                raw_ttml = f.read()
        except Exception as exc:
            app.logger.error(f"读取TTML文件失败: {input_path}. 错误: {exc}")
            return False, None, None

        try:
            sanitized_ttml = sanitize_ttml_content(raw_ttml)
        except Exception as exc:
            app.logger.error(f"TTML 白名单过滤失败: {input_path}. 错误: {exc}")
            return False, None, None

        # 解析净化后的 XML
        dom: Document = xml.dom.minidom.parseString(sanitized_ttml)
        tt: Document = dom.documentElement  # 获取根元素

        # 获取tt中的body/head元素
        body_elements = tt.getElementsByTagName('body')
        head_elements = tt.getElementsByTagName('head')
        
        if not body_elements or not head_elements:
            app.logger.error(f"TTML文件格式错误: {input_path}. 找不到必要的body或head元素")
            return False, None, None
            
        body: Element = body_elements[0]
        head: Element = head_elements[0]

        if body and head:
            # 获取body/head中的<div>/<metadata>子元素
            div_elements = body.getElementsByTagName('div')
            metadata_elements = head.getElementsByTagName('metadata')
            
            if not div_elements or not metadata_elements:
                app.logger.error(f"TTML文件格式错误: {input_path}. 找不到必要的div或metadata元素")
                return False, None, None
                
            div: Element = div_elements[0]
            metadata: Element = metadata_elements[0]

            # 获取div中的所有<p>子元素
            p_elements: NodeList[Element] = div.getElementsByTagName('p')
            if not p_elements or len(p_elements) == 0:
                app.logger.error(f"TTML文件格式错误: {input_path}. 找不到任何p元素")
                return False, None, None
                
            agent_elements: NodeList[Element] = metadata.getElementsByTagName('ttm:agent')

            # 检查是否有对唱
            for meta in agent_elements:
                if meta.getAttribute('xml:id') != 'v1':
                    TTMLLine.have_duet = True

            lines: list[TTMLLine] = []
            # 遍历每个<p>元素
            for p in p_elements:
                try:
                    lines.append(TTMLLine(p))
                except Exception as e:
                    app.logger.error(f"处理TTML行时出错: {type(e).__name__}: {e!s}，已跳过")
                    continue
            
            # 确保songs目录存在
            os.makedirs(songs_dir, exist_ok=True)

            # 修改路径
            base_name = os.path.splitext(input_path)[0]

            lyric_file: TextIO|None = None
            trans_file: TextIO|None = None

            lyric_path = os.path.join(songs_dir, f"{os.path.basename(base_name)}.lys")
            lyric_file = open(lyric_path, 'w', encoding='utf8')

            if TTMLLine.have_ts:
                trans_path = os.path.join(songs_dir, f"{os.path.basename(base_name)}_trans.lrc")
                trans_file = open(trans_path, 'w', encoding='utf8')

            count: int = 0

            try:
                for main_line, bg_line in [line.to_str() for line in lines]:
                    if main_line and main_line[0]:
                        lyric_file.write(main_line[0] + '\n')
                        lyric_file.flush()
                    if main_line and main_line[1] and trans_file:
                        trans_file.write(main_line[1] + '\n')
                        trans_file.flush()

                    if bg_line:
                        if bg_line[0]:
                            lyric_file.write(bg_line[0] + '\n')
                            lyric_file.flush()
                        # 背景歌词不生成独立的翻译行，因为它应该与主歌词共享翻译
                        count += 1
            except Exception as e:
                app.logger.error(f"写入歌词文件时出错: {str(e)}")
            finally:
                # 确保文件始终被关闭
                if lyric_file:
                    lyric_file.close()
                if trans_file:
                    trans_file.close()

        else:
            return False, None, None

    except Exception as e:
        app.logger.error(f"无法解析TTML文件: {input_path}. 错误: {str(e)}")
        return False, None, None

    return True, lyric_path, trans_path

def preprocess_brackets(content):
    """
    保留括号原样，避免把时间标记边界吞掉。
    仅做轻量的空白折叠，不再移除 ')(' / '(('。
    """
    if not content:
        return ''
    # 折叠多余空白，避免影响 regex 解析
    return re.sub(r'\s{2,}', ' ', content)


def extract_tag_value(text: str, tag: str) -> Optional[str]:
    """抽取形如 [tag:value] 的元数据值，保留原始大小写"""
    match = re.search(rf'\[{re.escape(tag)}:\s*(.*?)\s*\]', text)
    if match:
        value = match.group(1).strip()
        return value or None
    return None


def parse_syllable_info(content, marker='', offset=0):
    """解析LYS内容中的音节信息，返回音节列表；offset 为毫秒，正负皆可。"""
    content = preprocess_brackets(content)
    syllables = []

    pattern = r'(.*?)\((\d+),(\d+)\)'
    matches = re.finditer(pattern, content)
    for match in matches:
        text_part = match.group(1)
        start_ms = int(match.group(2))
        duration_ms = int(match.group(3))
        start_ms += offset  # 应用 offset
        syllables.append({
            'text': text_part,
            'start_ms': start_ms,
            'duration_ms': duration_ms
        })

    if not syllables:
        line_match = re.search(r'^(.*)\((\d+),(\d+)\)$', content)
        if line_match:
            text = line_match.group(1)
            start_ms = int(line_match.group(2))
            duration_ms = int(line_match.group(3))
            start_ms += offset  # 应用 offset
            if text or start_ms > 0 or duration_ms > 0:
                syllables.append({
                    'text': text,
                    'start_ms': start_ms,
                    'duration_ms': duration_ms
                })

    return syllables


def ms_to_ttml_time(ms):
    """将毫秒转换为 TTML 时间格式，统一输出 mm:ss.mmm（>=1h 扩展为 hh:mm:ss.mmm）"""
    total_ms = max(0, int(round(ms)))
    total_seconds, ms_part = divmod(total_ms, 1000)
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)

    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{ms_part:03d}"
    return f"{minutes:02d}:{seconds:02d}.{ms_part:03d}"


def _nearest_translation(begin_ms, trans_map, tol_ms=300):
    """
    在 translation dict 里找与 begin_ms 最接近的键（容差 ±tol_ms）。
    命中后从字典中移除该键，避免重复匹配。
    """
    if not trans_map:
        return None
    # 先试精确命中
    if begin_ms in trans_map:
        return trans_map.pop(begin_ms)
    # 找最近
    nearest_key = min(trans_map.keys(), key=lambda k: abs(k - begin_ms))
    if abs(nearest_key - begin_ms) <= tol_ms:
        return trans_map.pop(nearest_key)
    return None


def ttml_time_to_ms(time_str):
    """将TTML时间格式转换为毫秒（支持多种格式）"""
    if not time_str:
        return 0

    time_str = time_str.strip()

    # H:MM:SS.mmm 格式
    m = re.match(r"^(?:(\d+):)?(\d{1,2}):(\d{1,2}(?:\.\d{1,3})?)$", time_str)
    if m:
        h = int(m.group(1) or 0)
        mm = int(m.group(2))
        ss = float(m.group(3))
        return int((h*3600 + mm*60 + ss) * 1000)

    # MM:SS.mmm 格式
    m = re.match(r"^(\d{1,2}):(\d{1,2}(?:\.\d{1,3})?)$", time_str)
    if m:
        mm = int(m.group(1))
        ss = float(m.group(2))
        return int((mm*60 + ss) * 1000)

    # SS.mmm 格式
    try:
        sec = float(time_str)
        return int(sec * 1000)
    except ValueError:
        return 0




def text_tail_space(txt):
    """返回 (去除尾部空白后的文本, 尾随空白字符串)"""
    if txt is None:
        return "", ""
    match = re.search(r'(\s*)$', txt)
    tail = match.group(1) if match else ""
    if tail:
        return txt[:-len(tail)], tail
    return txt, ""


def find_translation_file(lyrics_path):
    """查找关联的翻译文件"""
    lyrics_path = Path(lyrics_path)
    # 尝试查找同名但带有_trans后缀的LRC文件
    trans_path = lyrics_path.parent / f"{lyrics_path.stem}_trans.lrc"
    if trans_path.exists():
        return str(trans_path)

    # 尝试查找同目录下的LRC文件（不带_trans后缀）
    lrc_files = list(lyrics_path.parent.glob("*.lrc"))
    for lrc_file in lrc_files:
        if lrc_file.name != lyrics_path.name and lrc_file.name.startswith(lyrics_path.stem):
            return str(lrc_file)

    return None


def lys_to_ttml(input_path, output_path):
    """将LYS格式转换为TTML格式（Apple风格）"""
    try:
        with open(input_path, 'r', encoding='utf-8') as f:
            lys_content = f.read()
        author_name = extract_tag_value(lys_content, 'by')

        # ---- 提取 offset（毫秒） ----
        offset = 0
        offset_match = re.search(r'\[offset:\s*(-?\d+)\s*\]', lys_content)
        if offset_match:
            try:
                offset = int(offset_match.group(1))
            except Exception:
                offset = 0

        # ---- 读取并解析翻译 LRC：转成 毫秒→文本 的字典，供"容差匹配" ----
        trans_path = find_translation_file(input_path)
        translation_dict_ms = {}
        trans_offset = 0
        translation_author = None
        if trans_path:
            try:
                with open(trans_path, 'r', encoding='utf-8') as f:
                    trans_content = f.read()

                translation_author = extract_tag_value(trans_content, 'by')

                # 提取翻译文件自身的 offset（毫秒）
                trans_offset_match = re.search(r'\[offset:\s*(-?\d+)\s*\]', trans_content)
                if trans_offset_match:
                    try:
                        trans_offset = int(trans_offset_match.group(1))
                    except Exception:
                        trans_offset = 0

                trans_lines = [line for line in trans_content.splitlines() if line.strip()]
                for line in trans_lines:
                    begin_time_str, content = parse_lrc_line(line)  # "mm:ss.mmm"
                    if begin_time_str and content is not None:
                        begin_ms = ttml_time_to_ms(begin_time_str)
                        # 给翻译时间叠加翻译文件自身的 offset
                        begin_ms += trans_offset
                        translation_dict_ms[begin_ms] = content
            except Exception as e:
                app.logger.warning(f"读取翻译文件时出错: {trans_path}. 错误: {str(e)}")

        # ---- 解析 LYS 主体 ----
        lines = [line for line in lys_content.splitlines() if line.strip()]
        parsed_lines = []

        for i, line in enumerate(lines):
            # 跳过元数据
            if line.startswith('[from:') or line.startswith('[id:') or line.startswith('[offset:'):
                continue

            m_line = re.match(r'\[([^\]]*)\](.*)', line)
            if not m_line:
                continue

            marker = m_line.group(1)       # 可能为空
            content = m_line.group(2)

            # 解析音节 + 应用 offset
            syllables = parse_syllable_info(content, marker, offset=offset)
            parenthetical_background = False
            if marker not in ['6', '7', '8']:
                parenthetical_background = is_parenthetical_background_line(content)

            if syllables:
                begin_time_ms = syllables[0]['start_ms']
                last_end_ms = syllables[-1]['start_ms'] + syllables[-1]['duration_ms']

                parsed_lines.append({
                    'marker': marker,
                    'content': content,
                    'syllables': syllables,
                    'is_duet': marker in ['2', '5'],
                    'is_background': marker in ['6', '7', '8'] or parenthetical_background,
                    'is_parenthetical_background': parenthetical_background,
                    'translation': None,
                    'begin_ms': begin_time_ms,
                    'last_end_ms': last_end_ms,
                })

        # 统一按开始时间排序，保证 key 顺序与时间线一致
        parsed_lines.sort(key=lambda x: x['begin_ms'])

        # 按时间顺序匹配翻译，避免乱序导致错行
        translation_map = dict(translation_dict_ms)
        for line_info in parsed_lines:
            begin_ms = line_info['begin_ms']
            line_info['translation'] = _nearest_translation(begin_ms, translation_map, 300)

        # ---- 统计时长范围 ----
        has_duet = any(line['is_duet'] for line in parsed_lines)
        dom, div = create_ttml_document(has_duet, author_name, translation_author)

        DEFAULT_LAST_LINE_TAIL_MS = 10000
        for idx, line_info in enumerate(parsed_lines):
            syllables = line_info['syllables']
            if not syllables:
                line_info['begin_ms'] = 0
                line_info['end_ms'] = 0
                continue
            begin_ms = line_info['begin_ms']
            last_end_ms = line_info['last_end_ms']
            if idx + 1 < len(parsed_lines):
                next_begin = parsed_lines[idx + 1]['begin_ms']
                end_ms = max(last_end_ms, next_begin)
            else:
                end_ms = max(last_end_ms, begin_ms + DEFAULT_LAST_LINE_TAIL_MS)
            line_info['end_ms'] = end_ms

        first_begin = parsed_lines[0]['begin_ms'] if parsed_lines else 0
        last_end = parsed_lines[-1]['end_ms'] if parsed_lines else 0

        body_elements = dom.getElementsByTagName('body')
        if body_elements:
            body = body_elements[0]
            body.setAttribute('dur', ms_to_ttml_time(last_end))

        if first_begin is not None:
            div.setAttribute('begin', ms_to_ttml_time(first_begin))
            div.setAttribute('end',   ms_to_ttml_time(last_end))

        # ---- 写入每一行 ----
        key_idx = 1
        prev_main_p = None

        for line_info in parsed_lines:
            syllables = line_info['syllables']
            if not syllables:
                continue

            translation_text = (line_info.get('translation') or '').strip()
            begin_ms = line_info['begin_ms']
            end_ms   = line_info['end_ms']
            begin = ms_to_ttml_time(begin_ms)
            end   = ms_to_ttml_time(end_ms)

            if not line_info['is_background']:
                p = dom.createElement('p')
                p.setAttribute('begin', begin)
                p.setAttribute('end', end)
                p.setAttribute('ttm:agent', 'v1' if not line_info['is_duet'] else 'v2')
                p.setAttribute('itunes:key', f'L{key_idx}')
                key_idx += 1

                # 逐音节 span（Apple 风格）
                for syl in syllables:
                    text = syl['text']
                    if text is None:
                        continue
                    span = dom.createElement('span')
                    span.setAttribute('begin', ms_to_ttml_time(syl['start_ms']))
                    span.setAttribute('end',   ms_to_ttml_time(syl['start_ms'] + syl['duration_ms']))
                    txt, tail = text_tail_space(text)
                    if txt:
                        span.appendChild(dom.createTextNode(txt))
                    if span.childNodes:
                        p.appendChild(span)
                    if tail:
                        p.appendChild(dom.createTextNode(tail))

                # 有翻译就加翻译 span；空白翻译不写入
                if translation_text:
                    trans_span = dom.createElement('span')
                    trans_span.setAttribute('ttm:role', 'x-translation')
                    trans_span.setAttribute('xml:lang', 'zh-CN')
                    trans_span.appendChild(dom.createTextNode(translation_text))
                    p.appendChild(trans_span)

                div.appendChild(p)
                prev_main_p = p
            else:
                # 背景行：塞到上一主行 <span ttm:role="x-bg">
                if prev_main_p is None:
                    p = dom.createElement('p')
                    p.setAttribute('begin', begin)
                    p.setAttribute('end', end)
                    p.setAttribute('itunes:key', f'L{key_idx}')
                    p.setAttribute('ttm:agent', 'v1')
                    key_idx += 1
                    div.appendChild(p)
                    prev_main_p = p

                bg_span = dom.createElement('span')
                bg_span.setAttribute('ttm:role', 'x-bg')
                bg_span.setAttribute('begin', begin)
                bg_span.setAttribute('end', end)

                for syl in syllables:
                    text = syl['text']
                    if text is None:
                        continue
                    span = dom.createElement('span')
                    span.setAttribute('begin', ms_to_ttml_time(syl['start_ms']))
                    span.setAttribute('end',   ms_to_ttml_time(syl['start_ms'] + syl['duration_ms']))
                    txt, tail = text_tail_space(text)
                    if txt:
                        span.appendChild(dom.createTextNode(txt))
                    if span.childNodes:
                        bg_span.appendChild(span)
                    if tail:
                        bg_span.appendChild(dom.createTextNode(tail))

                # 背景行的翻译（如果这一行也刚好有对应时间翻译）
                if translation_text:
                    trans_span = dom.createElement('span')
                    trans_span.setAttribute('ttm:role', 'x-translation')
                    trans_span.setAttribute('xml:lang', 'zh-CN')
                    trans_span.appendChild(dom.createTextNode(translation_text))
                    bg_span.appendChild(trans_span)

                prev_main_p.appendChild(bg_span)

        # 单行输出
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(dom.documentElement.toxml())

        return True, None

    except Exception as e:
        app.logger.error(f"无法转换LYS到TTML: {input_path}. 错误: {str(e)}")
        return False, str(e)

def create_ttml_document(has_duet=False,
                         author_name: Optional[str] = None,
                         translation_author: Optional[str] = None):
    """创建TTML文档基础结构（Apple风格）"""
    dom = xml.dom.minidom.Document()

    tt = dom.createElement('tt')
    tt.setAttribute('xmlns', 'http://www.w3.org/ns/ttml')
    tt.setAttribute('xmlns:ttm', 'http://www.w3.org/ns/ttml#metadata')
    tt.setAttribute('xmlns:amll', 'http://www.example.com/ns/amll')
    tt.setAttribute('xmlns:itunes', 'http://music.apple.com/lyric-ttml-internal')
    dom.appendChild(tt)

    head = dom.createElement('head')
    tt.appendChild(head)

    metadata = dom.createElement('metadata')
    head.appendChild(metadata)

    agent1 = dom.createElement('ttm:agent')
    agent1.setAttribute('type', 'person')
    agent1.setAttribute('xml:id', 'v1')
    metadata.appendChild(agent1)

    if has_duet:
        agent2 = dom.createElement('ttm:agent')
        agent2.setAttribute('type', 'other')
        agent2.setAttribute('xml:id', 'v2')
        metadata.appendChild(agent2)

    def _append_author_meta(value: str):
        meta = dom.createElement('amll:meta')
        meta.setAttribute('key', 'ttmlAuthorGithubLogin')
        meta.setAttribute('value', value)
        metadata.appendChild(meta)

    added_meta_values: set[str] = set()

    if author_name:
        normalized = author_name.strip()
        if normalized and normalized not in added_meta_values:
            _append_author_meta(normalized)
            added_meta_values.add(normalized)

    if translation_author:
        ta = translation_author.strip()
        combined_author = ''
        if author_name and ta.startswith(author_name):
            combined_author = ta
        elif author_name:
            combined_author = f"{author_name}，{ta}"
        else:
            combined_author = ta
        combined_author = combined_author.strip()
        if combined_author and combined_author not in added_meta_values:
            _append_author_meta(combined_author)
            added_meta_values.add(combined_author)

    body = dom.createElement('body')
    tt.appendChild(body)

    div = dom.createElement('div')
    body.appendChild(div)

    return dom, div


def sanitize_ttml_content(ttml_text: str) -> str:
    """
    Rebuild TTML with a strict whitelist to strip complex/unknown metadata.
    Only preserves Apple-style lyric timing, amll:meta entries, agents, and
    translation/romanization/background spans.
    """
    try:
        src_dom = xml.dom.minidom.parseString(ttml_text)
    except Exception as exc:
        raise ValueError(f"TTML 解析失败: {exc}") from exc

    p_elements = src_dom.getElementsByTagName('p')
    if not p_elements:
        raise ValueError("TTML 中缺少 <p> 节点，无法净化保存")

    def _node_text(node) -> str:
        parts: List[str] = []
        for child in node.childNodes:
            if child.nodeType in (Node.TEXT_NODE, Node.CDATA_SECTION_NODE):
                if child.nodeValue:
                    parts.append(child.nodeValue)
            elif child.nodeType == Node.ELEMENT_NODE:
                parts.append(_node_text(child))
        return ''.join(parts)

    def _clean_bg_text(value: str) -> str:
        cleaned = (value or '').strip()
        cleaned = re.sub(r'^[（(]', '', cleaned)
        cleaned = re.sub(r'[)）]$', '', cleaned)
        return cleaned.strip()

    def _is_translation_text(node) -> bool:
        parent = getattr(node, 'parentNode', None)
        if not parent or getattr(parent, 'tagName', None) != 'translation':
            return False
        grand_parent = getattr(parent, 'parentNode', None)
        if not grand_parent or getattr(grand_parent, 'tagName', None) != 'translations':
            return False
        root = getattr(grand_parent, 'parentNode', None)
        return bool(root and getattr(root, 'tagName', None) == 'iTunesMetadata')

    def _is_transliteration_text(node) -> bool:
        parent = getattr(node, 'parentNode', None)
        if not parent or getattr(parent, 'tagName', None) != 'transliteration':
            return False
        grand_parent = getattr(parent, 'parentNode', None)
        if not grand_parent or getattr(grand_parent, 'tagName', None) != 'transliterations':
            return False
        root = getattr(grand_parent, 'parentNode', None)
        return bool(root and getattr(root, 'tagName', None) == 'iTunesMetadata')

    translations: dict[str, dict[str, str]] = {}
    timed_translations: dict[str, dict[str, str]] = {}
    for text_el in src_dom.getElementsByTagName('text'):
        if not text_el.hasAttribute('for') or not _is_translation_text(text_el):
            continue
        key = text_el.getAttribute('for')
        main_parts: List[str] = []
        bg_parts: List[str] = []
        has_span_child = False
        for child in text_el.childNodes:
            if child.nodeType in (Node.TEXT_NODE, Node.CDATA_SECTION_NODE):
                if child.nodeValue:
                    main_parts.append(child.nodeValue)
            elif child.nodeType == Node.ELEMENT_NODE:
                if child.tagName == 'span':
                    has_span_child = True
                role = child.getAttribute('ttm:role') if child.hasAttribute('ttm:role') else ''
                if role == 'x-bg':
                    bg_parts.append(_node_text(child))
        main = ''.join(main_parts).strip()
        bg = _clean_bg_text(''.join(bg_parts))
        if main or bg:
            target = timed_translations if has_span_child else translations
            target[key] = {'main': main, 'bg': bg}
            if has_span_child:
                translations.pop(key, None)

    line_romanizations: dict[str, dict[str, str]] = {}
    for text_el in src_dom.getElementsByTagName('text'):
        if not text_el.hasAttribute('for') or not _is_transliteration_text(text_el):
            continue
        key = text_el.getAttribute('for')
        line_roman_main: List[str] = []
        line_roman_bg: List[str] = []
        for child in text_el.childNodes:
            if child.nodeType in (Node.TEXT_NODE, Node.CDATA_SECTION_NODE):
                if child.nodeValue:
                    line_roman_main.append(child.nodeValue)
            elif child.nodeType == Node.ELEMENT_NODE:
                role = child.getAttribute('ttm:role') if child.hasAttribute('ttm:role') else ''
                if role == 'x-bg':
                    line_roman_bg.append(_node_text(child))
                elif child.getAttribute('begin') and child.getAttribute('end'):
                    line_roman_main.append(_node_text(child))
        roman_main = ''.join(line_roman_main).strip()
        roman_bg = _clean_bg_text(''.join(line_roman_bg))
        if roman_main or roman_bg:
            line_romanizations[key] = {'main': roman_main, 'bg': roman_bg}

    main_agent_id = 'v1'
    for agent in src_dom.getElementsByTagName('ttm:agent'):
        if agent.getAttribute('type') == 'person':
            xml_id = agent.getAttribute('xml:id')
            if xml_id:
                main_agent_id = xml_id
                break

    has_duet = any(
        p.getAttribute('ttm:agent')
        and p.getAttribute('ttm:agent') != main_agent_id
        for p in p_elements
    )

    dom, div = create_ttml_document(has_duet)

    metadata_nodes = dom.getElementsByTagName('metadata')
    if metadata_nodes:
        metadata_el = metadata_nodes[0]
        for meta in src_dom.getElementsByTagName('amll:meta'):
            key = meta.getAttribute('key')
            value = meta.getAttribute('value')
            if key and value:
                clone = dom.createElement('amll:meta')
                clone.setAttribute('key', key)
                clone.setAttribute('value', value)
                metadata_el.appendChild(clone)

    src_body_nodes = src_dom.getElementsByTagName('body')
    if src_body_nodes:
        dur_value = src_body_nodes[0].getAttribute('dur')
        dst_body_nodes = dom.getElementsByTagName('body')
        if dur_value and dst_body_nodes:
            dst_body_nodes[0].setAttribute('dur', dur_value)
        src_div_nodes = src_body_nodes[0].getElementsByTagName('div')
        if src_div_nodes:
            begin = src_div_nodes[0].getAttribute('begin')
            end = src_div_nodes[0].getAttribute('end')
            if begin:
                div.setAttribute('begin', begin)
            if end:
                div.setAttribute('end', end)

    def normalize_agent(agent_value: str) -> str:
        if not agent_value:
            return 'v1'
        return 'v1' if agent_value == main_agent_id else 'v2'

    def copy_lyric_children(src_parent, dst_parent):
        for child in src_parent.childNodes:
            if child.nodeType in (Node.TEXT_NODE, Node.CDATA_SECTION_NODE):
                if child.nodeValue:
                    dst_parent.appendChild(dom.createTextNode(child.nodeValue))
                continue
            if child.nodeType != Node.ELEMENT_NODE:
                continue
            if child.tagName != 'span':
                continue

            role = child.getAttribute('ttm:role') if child.hasAttribute('ttm:role') else ''
            if role == 'x-bg':
                bg_span = dom.createElement('span')
                bg_span.setAttribute('ttm:role', 'x-bg')
                begin = child.getAttribute('begin')
                end = child.getAttribute('end')
                if begin:
                    bg_span.setAttribute('begin', begin)
                if end:
                    bg_span.setAttribute('end', end)
                copy_lyric_children(child, bg_span)
                if bg_span.childNodes:
                    dst_parent.appendChild(bg_span)
                continue

            if role in {'x-translation', 'x-roman'}:
                meta_span = dom.createElement('span')
                meta_span.setAttribute('ttm:role', role)
                if role == 'x-translation' and child.getAttribute('xml:lang'):
                    meta_span.setAttribute('xml:lang', child.getAttribute('xml:lang'))
                copy_lyric_children(child, meta_span)
                if meta_span.childNodes:
                    dst_parent.appendChild(meta_span)
                continue

            begin = child.getAttribute('begin')
            end = child.getAttribute('end')
            if not (begin and end):
                continue

            span = dom.createElement('span')
            span.setAttribute('begin', begin)
            span.setAttribute('end', end)
            if child.hasAttribute('amll:empty-beat'):
                span.setAttribute('amll:empty-beat', child.getAttribute('amll:empty-beat'))
            if child.getAttribute('amll:obscene') == 'true':
                span.setAttribute('amll:obscene', 'true')
            copy_lyric_children(child, span)
            if span.childNodes:
                dst_parent.appendChild(span)

    for p in p_elements:
        begin = p.getAttribute('begin')
        end = p.getAttribute('end')
        if not (begin and end):
            continue

        new_p = dom.createElement('p')
        new_p.setAttribute('begin', begin)
        new_p.setAttribute('end', end)
        itunes_key = ''
        if p.hasAttribute('itunes:key'):
            itunes_key = p.getAttribute('itunes:key')
            new_p.setAttribute('itunes:key', itunes_key)
        new_p.setAttribute('ttm:agent', normalize_agent(p.getAttribute('ttm:agent')))

        copy_lyric_children(p, new_p)

        def has_role_child(parent, role: str) -> bool:
            for child in parent.childNodes:
                if (
                    child.nodeType == Node.ELEMENT_NODE
                    and child.tagName == 'span'
                    and child.getAttribute('ttm:role') == role
                ):
                    return True
            return False

        def bg_spans(parent) -> List[Element]:
            spans: List[Element] = []
            for child in parent.childNodes:
                if (
                    child.nodeType == Node.ELEMENT_NODE
                    and child.tagName == 'span'
                    and child.getAttribute('ttm:role') == 'x-bg'
                ):
                    spans.append(child)
            return spans

        translation_data = None
        roman_data = None
        if itunes_key:
            translation_data = timed_translations.get(itunes_key) or translations.get(itunes_key)
            roman_data = line_romanizations.get(itunes_key)

        if translation_data and translation_data.get('main') and not has_role_child(new_p, 'x-translation'):
            trans_span = dom.createElement('span')
            trans_span.setAttribute('ttm:role', 'x-translation')
            trans_span.setAttribute('xml:lang', 'zh-CN')
            trans_span.appendChild(dom.createTextNode(translation_data['main']))
            new_p.appendChild(trans_span)

        if translation_data and translation_data.get('bg'):
            for span in bg_spans(new_p):
                if not has_role_child(span, 'x-translation'):
                    trans_span = dom.createElement('span')
                    trans_span.setAttribute('ttm:role', 'x-translation')
                    trans_span.setAttribute('xml:lang', 'zh-CN')
                    trans_span.appendChild(dom.createTextNode(translation_data['bg']))
                    span.appendChild(trans_span)
                    break

        if roman_data and roman_data.get('main') and not has_role_child(new_p, 'x-roman'):
            roman_span = dom.createElement('span')
            roman_span.setAttribute('ttm:role', 'x-roman')
            roman_span.appendChild(dom.createTextNode(roman_data['main']))
            new_p.appendChild(roman_span)

        if roman_data and roman_data.get('bg'):
            for span in bg_spans(new_p):
                if not has_role_child(span, 'x-roman'):
                    roman_span = dom.createElement('span')
                    roman_span.setAttribute('ttm:role', 'x-roman')
                    roman_span.appendChild(dom.createTextNode(roman_data['bg']))
                    span.appendChild(roman_span)
                    break

        if new_p.childNodes:
            div.appendChild(new_p)

    return dom.documentElement.toxml()


def parse_lrc_line(line):
    """解析LRC行，返回时间戳和内容"""
    time_match = re.match(r'\[(\d{2}):(\d{2})\.(\d{2,3})\](.*)', line)
    if time_match:
        min, sec, ms, content = time_match.groups()
        # 确保毫秒是3位数
        if len(ms) == 2:
            ms = ms + '0'
        begin_time = f"{min}:{sec}.{ms}"
        # 只去除末尾的回车，保留所有空格
        return begin_time, content.rstrip('\r')
    return None, None


def _extract_lrc_marker_and_clean(content):
    """提取LRC行的标记并清理内容"""
    # 匹配行首的标记，如 [6]content
    marker_match = re.match(r'^\[(\d+)\](.*)', content)
    if marker_match:
        marker = marker_match.group(1)
        # 保留空格
        clean_content = marker_match.group(2)
        return marker, clean_content
    # 保留空格
    return '', content


def calculate_lrc_end_time(begin_time_str, next_begin_time_str=None, default_duration_ms=5000):
    """计算LRC行的结束时间"""
    # 解析当前开始时间
    time_parts = begin_time_str.split(':')
    min = int(time_parts[0])
    sec_parts = time_parts[1].split('.')
    sec = int(sec_parts[0])
    ms = int(sec_parts[1])

    begin_ms = (min * 60 + sec) * 1000 + ms

    # 如果有下一行时间，使用下一行时间作为结束时间
    if next_begin_time_str:
        next_time_parts = next_begin_time_str.split(':')
        next_min = int(next_time_parts[0])
        next_sec_parts = next_time_parts[1].split('.')
        next_sec = int(next_sec_parts[0])
        next_ms = int(next_sec_parts[1])

        next_begin_ms = (next_min * 60 + next_sec) * 1000 + next_ms

        # 如果时间间隔合理（不超过30秒），使用实际间隔
        if 0 < next_begin_ms - begin_ms < 30000:
            end_ms = next_begin_ms
        else:
            end_ms = begin_ms + default_duration_ms
    else:
        # 没有下一行，使用默认持续时间
        end_ms = begin_ms + default_duration_ms

    # 转换回时间格式
    total_seconds = end_ms // 1000
    end_min = total_seconds // 60
    end_sec = total_seconds % 60
    end_ms_part = end_ms % 1000

    return f"{end_min:02d}:{end_sec:02d}.{end_ms_part:03d}"


def lrc_to_ttml(input_path, output_path):
    """将LRC格式转换为TTML格式（Apple风格）"""
    try:
        with open(input_path, 'r', encoding='utf-8') as f:
            lrc_content = f.read()
        author_name = extract_tag_value(lrc_content, 'by')

        # ---- 读取并解析翻译 LRC：转成 毫秒→文本 的字典，供"精确匹配" ----
        trans_path = find_translation_file(input_path)
        translation_dict_ms = {}
        translation_author = None
        if trans_path:
            try:
                with open(trans_path, 'r', encoding='utf-8') as f:
                    trans_content = f.read()
                translation_author = extract_tag_value(trans_content, 'by')
                trans_lines = [line for line in trans_content.splitlines() if line.strip()]
                for line in trans_lines:
                    begin_time_str, content = parse_lrc_line(line)  # "mm:ss.mmm"
                    if begin_time_str and content is not None:
                        begin_ms = ttml_time_to_ms(begin_time_str)
                        translation_dict_ms[begin_ms] = content
            except Exception as e:
                app.logger.warning(f"读取翻译文件时出错: {trans_path}. 错误: {str(e)}")

        # 解析LRC内容，提取有效行
        lines = [line for line in lrc_content.splitlines() if line.strip()]
        valid_lines = []

        # 收集所有行的时间范围
        begin_times_ms = []
        end_times_ms = []
        for i, line in enumerate(lines):
            begin_time_str, content = parse_lrc_line(line)
            if begin_time_str and content:
                begin_ms = ttml_time_to_ms(begin_time_str)
                begin_times_ms.append(begin_ms)

                # 计算结束时间
                next_time = None
                if i+1 < len(lines):
                    next_begin_time_str, _ = parse_lrc_line(lines[i+1])
                    if next_begin_time_str:
                        next_time = next_begin_time_str
                end_time_str = calculate_lrc_end_time(begin_time_str, next_time)
                end_ms = ttml_time_to_ms(end_time_str)
                end_times_ms.append(end_ms)

        first_begin = min(begin_times_ms) if begin_times_ms else 0
        last_end = max(end_times_ms) if end_times_ms else 0

        # 重新解析有效行，使用毫秒级精确翻译匹配
        for line in lines:
            begin_time_str, content = parse_lrc_line(line)
            if begin_time_str and content:
                # 提取标记和清理内容
                marker, clean_content = _extract_lrc_marker_and_clean(content)

                # 检查是否为背景行
                is_background = marker in ['6', '7', '8'] or is_parenthetical_background_line(clean_content)

                # 基于时间戳获取对应的翻译内容（毫秒级精确匹配）
                begin_ms = ttml_time_to_ms(begin_time_str)
                translation_content = translation_dict_ms.get(begin_ms)

                valid_lines.append({
                    'begin_ms': begin_ms,
                    'begin_time_str': begin_time_str,
                    'content': content,
                    'marker': marker,
                    'clean_content': clean_content,
                    'is_duet': '[2]' in content or '[5]' in content or marker in ['2', '5'],
                    'is_background': is_background,
                    'translation_content': translation_content
                })

        # 检查是否有对唱标记
        has_duet = any(line['is_duet'] for line in valid_lines)

        # 创建TTML文档
        dom, div = create_ttml_document(has_duet, author_name, translation_author)

        # 设置body和div的时间范围
        body_elements = dom.getElementsByTagName('body')
        if body_elements:
            body = body_elements[0]
            body.setAttribute('dur', ms_to_ttml_time(last_end))

        if first_begin is not None:
            div.setAttribute('begin', ms_to_ttml_time(first_begin))
            div.setAttribute('end', ms_to_ttml_time(last_end))

        # 转换每一行
        key_idx = 1
        prev_main_p = None

        for i, line_info in enumerate(valid_lines):
            begin_time_str = line_info['begin_time_str']
            clean_content = line_info['clean_content']
            marker = line_info['marker']
            is_duet = line_info['is_duet']
            is_background = line_info['is_background']
            translation_content = line_info['translation_content']

            if clean_content:
                # 计算结束时间
                next_time = valid_lines[i+1]['begin_time_str'] if i+1 < len(valid_lines) else None
                end_time_str = calculate_lrc_end_time(begin_time_str, next_time)
                begin_ms = line_info['begin_ms']
                end_ms = ttml_time_to_ms(end_time_str)
                begin_str_fmt = ms_to_ttml_time(begin_ms)
                end_str_fmt = ms_to_ttml_time(end_ms)

                if not is_background:
                    # 创建主行p元素（Apple风格）
                    p = dom.createElement('p')
                    p.setAttribute('begin', begin_str_fmt)
                    p.setAttribute('end', end_str_fmt)
                    p.setAttribute('ttm:agent', 'v1' if not is_duet else 'v2')
                    p.setAttribute('itunes:key', f'L{key_idx}')
                    key_idx += 1

                    # 添加文本节点（Apple风格）
                    txt, tail_space = text_tail_space(clean_content)
                    if txt:
                        text_node = dom.createTextNode(txt)
                        p.appendChild(text_node)
                    if tail_space:
                        p.appendChild(dom.createTextNode(tail_space))

                    # 如果有翻译内容，添加翻译span（Apple风格）- 精确匹配
                    if translation_content:
                        trans_span = dom.createElement('span')
                        trans_span.setAttribute('ttm:role', 'x-translation')
                        trans_span.setAttribute('xml:lang', 'zh-CN')
                        trans_text = translation_content
                        trans_span.appendChild(dom.createTextNode(trans_text))
                        p.appendChild(trans_span)

                    div.appendChild(p)
                    prev_main_p = p
                else:
                    # 背景行：作为内嵌span添加到上一主行
                    if prev_main_p is not None:
                        bg_span = dom.createElement('span')
                        bg_span.setAttribute('ttm:role', 'x-bg')
                        bg_span.setAttribute('begin', begin_str_fmt)
                        bg_span.setAttribute('end', end_str_fmt)

                        # 添加文本节点（背景）
                        txt, tail_space = text_tail_space(clean_content)
                        if txt:
                            text_node = dom.createTextNode(txt)
                            bg_span.appendChild(text_node)
                        if tail_space:
                            bg_span.appendChild(dom.createTextNode(tail_space))

                        # 如果有翻译内容，添加到背景span中（精确匹配）
                        if translation_content:
                            trans_span = dom.createElement('span')
                            trans_span.setAttribute('ttm:role', 'x-translation')
                            trans_span.setAttribute('xml:lang', 'zh-CN')
                            trans_text = translation_content
                            trans_span.appendChild(dom.createTextNode(trans_text))
                            bg_span.appendChild(trans_span)

                        prev_main_p.appendChild(bg_span)
                    else:
                        # 如果没有上一主行，创建一个主行
                        p = dom.createElement('p')
                        p.setAttribute('begin', begin_str_fmt)
                        p.setAttribute('end', end_str_fmt)
                        p.setAttribute('ttm:agent', 'v1')
                        p.setAttribute('itunes:key', f'L{key_idx}')
                        key_idx += 1

                        # 背景作为span内嵌
                        bg_span = dom.createElement('span')
                        bg_span.setAttribute('ttm:role', 'x-bg')
                        bg_span.setAttribute('begin', begin_str_fmt)
                        bg_span.setAttribute('end', end_str_fmt)

                        # 添加文本节点（背景）
                        txt, tail_space = text_tail_space(clean_content)
                        if txt:
                            text_node = dom.createTextNode(txt)
                            bg_span.appendChild(text_node)
                        if tail_space:
                            bg_span.appendChild(dom.createTextNode(tail_space))

                        # 如果有翻译内容，添加到背景span中（精确匹配）
                        if translation_content:
                            trans_span = dom.createElement('span')
                            trans_span.setAttribute('ttm:role', 'x-translation')
                            trans_span.setAttribute('xml:lang', 'zh-CN')
                            trans_text = translation_content
                            trans_span.appendChild(dom.createTextNode(trans_text))
                            bg_span.appendChild(trans_span)

                        p.appendChild(bg_span)
                        div.appendChild(p)
                        prev_main_p = p

        # 写入TTML文件（单行格式，无换行符和缩进）
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(dom.documentElement.toxml())

        return True, None
    except Exception as e:
        app.logger.error(f"无法转换LRC到TTML: {input_path}. 错误: {str(e)}")
        return False, str(e)

@app.route('/convert_ttml', methods=['POST'])
def convert_ttml():
    if not is_request_allowed():
        return abort(403)
    try:
        if 'file' not in request.files:
            return jsonify({'status': 'error', 'message': '没有选择文件'})

        file = request.files['file']
        if file.filename == '':
            return jsonify({'status': 'error', 'message': '无效的文件名'})

        # 验证文件类型
        file_ext = Path(file.filename).suffix.lower()
        if file_ext != '.ttml':
            return jsonify({'status': 'error', 'message': '只支持TTML格式'})

        # 清理文件名
        clean_name = sanitize_filename(file.filename)
        if not clean_name:
            return jsonify({'status': 'error', 'message': '文件名包含非法字符或为空'})
        temp_path = SONGS_DIR / f"temp_{clean_name}"

        # 保存临时文件
        file.save(temp_path)

        # 转换TTML文件
        try:
            success, lyric_path, trans_path = ttml_to_lys(str(temp_path), str(SONGS_DIR))
        except Exception as e:
            app.logger.error(f"TTML转换过程中发生错误: {str(e)}", exc_info=True)
            return jsonify({'status': 'error', 'message': f'TTML转换失败: {str(e)}'})

        # 删除临时文件
        if temp_path.exists():
            try:
                temp_path.unlink()
            except Exception as e:
                app.logger.warning(f"无法删除临时文件 {temp_path}: {str(e)}")

        if success:
            try:
                lyric_relative = resource_relative_from_path(lyric_path, 'songs')
            except ValueError:
                lyric_relative = _normalize_relative_path(os.path.basename(lyric_path))

            result = {
                'status': 'success',
                'lyricPath': build_public_url('songs', lyric_relative)
            }
            
            if trans_path:
                try:
                    trans_relative = resource_relative_from_path(trans_path, 'songs')
                except ValueError:
                    trans_relative = _normalize_relative_path(os.path.basename(trans_path))
                result['transPath'] = build_public_url('songs', trans_relative)
            
            return jsonify(result)
        else:
            return jsonify({'status': 'error', 'message': '转换失败，请检查TTML文件格式是否正确'})

    except Exception as e:
        app.logger.error(f"处理TTML转换请求时出错: {str(e)}", exc_info=True)
        return jsonify({'status': 'error', 'message': f'处理请求时出错: {str(e)}'})

@app.route('/convert_ttml_by_path', methods=['POST'])
def convert_ttml_by_path():
    if not is_request_allowed():
        return abort(403)
    try:
        data = request.get_json()
        ttml_filename = data.get('path')
        if not ttml_filename or not ttml_filename.lower().endswith('.ttml'):
            return jsonify({'status': 'error', 'message': '请提供TTML文件名'})
        try:
            ttml_path = resolve_resource_path(ttml_filename, 'songs')
        except ValueError as exc:
            return jsonify({'status': 'error', 'message': f'无法解析TTML路径: {exc}'})

        if not ttml_path.exists():
            return jsonify({'status': 'error', 'message': 'TTML文件不存在'})
        # 直接调用原有转换逻辑
        success, lyric_path, trans_path = ttml_to_lys(str(ttml_path), str(SONGS_DIR))
        if success:
            try:
                lyric_relative = resource_relative_from_path(lyric_path, 'songs')
            except ValueError:
                lyric_relative = _normalize_relative_path(os.path.basename(lyric_path))
            result = {
                'status': 'success',
                'lyricPath': build_public_url('songs', lyric_relative)
            }
            if trans_path:
                try:
                    trans_relative = resource_relative_from_path(trans_path, 'songs')
                except ValueError:
                    trans_relative = _normalize_relative_path(os.path.basename(trans_path))
                result['transPath'] = build_public_url('songs', trans_relative)
            return jsonify(result)
        else:
            return jsonify({'status': 'error', 'message': '转换失败，请检查TTML文件格式是否正确'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})

@app.route('/convert_to_ttml', methods=['POST'])
def convert_to_ttml():
    if not is_request_allowed():
        return abort(403)
    try:
        data = request.get_json()
        lyrics_path = data.get('path')
        if not lyrics_path:
            return jsonify({'status': 'error', 'message': '请提供歌词文件路径'})

        try:
            input_path = resolve_resource_path(lyrics_path, 'songs')
        except ValueError as exc:
            return jsonify({'status': 'error', 'message': f'无法解析歌词路径: {exc}'})

        # 获取文件扩展名
        file_ext = input_path.suffix.lower()
        if file_ext not in ['.lys', '.lrc']:
            return jsonify({'status': 'error', 'message': '只支持LYS和LRC格式'})

        if not input_path.exists():
            return jsonify({'status': 'error', 'message': '歌词文件不存在'})

        # 生成输出文件名
        output_filename = input_path.stem + '.ttml'
        output_path = SONGS_DIR / output_filename

        # 根据文件类型调用相应的转换函数
        success = False
        error_msg = None
        if file_ext == '.lys':
            success, error_msg = lys_to_ttml(str(input_path), str(output_path))
        elif file_ext == '.lrc':
            success, error_msg = lrc_to_ttml(str(input_path), str(output_path))

        if success:
            target_relative = resource_relative_from_path(output_path, 'songs')
            return jsonify({
                'status': 'success',
                'ttmlPath': build_public_url('songs', target_relative)
            })
        else:
            return jsonify({'status': 'error', 'message': f'转换失败: {error_msg}'})

    except Exception as e:
        app.logger.error(f"处理转换请求时出错: {str(e)}", exc_info=True)
        return jsonify({'status': 'error', 'message': f'处理请求时出错: {str(e)}'})


@app.route('/convert_to_ttml_temp', methods=['POST'])
def convert_to_ttml_temp():
    """将歌词临时转换为TTML格式，用于AMLL规则编写，不覆盖原文件"""
    if not is_request_allowed():
        return abort(403)
    try:
        data = request.get_json()
        lyrics_path = data.get('path')
        if not lyrics_path:
            return jsonify({'status': 'error', 'message': '请提供歌词文件路径'})

        try:
            input_path = resolve_resource_path(lyrics_path, 'songs')
        except ValueError as exc:
            return jsonify({'status': 'error', 'message': f'无法解析歌词路径: {exc}'})

        # 获取文件扩展名
        file_ext = input_path.suffix.lower()
        if file_ext not in ['.lys', '.lrc']:
            return jsonify({'status': 'error', 'message': '只支持LYS和LRC格式'})

        if not input_path.exists():
            return jsonify({'status': 'error', 'message': '歌词文件不存在'})

        # 生成带专用后缀的临时输出文件名，避免影响原文件
        output_filename = input_path.stem + '_amll_temp.ttml'
        output_path = SONGS_DIR / output_filename

        # 根据文件类型调用相应的转换函数
        success = False
        error_msg = None
        if file_ext == '.lys':
            success, error_msg = lys_to_ttml(str(input_path), str(output_path))
        elif file_ext == '.lrc':
            success, error_msg = lrc_to_ttml(str(input_path), str(output_path))

        if success:
            target_relative = resource_relative_from_path(output_path, 'songs')
            return jsonify({
                'status': 'success',
                'ttmlPath': build_public_url('songs', target_relative)
            })
        else:
            return jsonify({'status': 'error', 'message': f'转换失败: {error_msg}'})

    except Exception as e:
        app.logger.error(f"处理临时转换请求时出错: {str(e)}", exc_info=True)
        return jsonify({'status': 'error', 'message': f'处理请求时出错: {str(e)}'})


@app.route('/merge_to_lqe', methods=['POST'])
def merge_to_lqe():
    if not is_request_allowed():
        return abort(403)
    try:
        data = request.get_json()
        if not data or 'lyricsPath' not in data or 'translationPath' not in data:
            return jsonify({'status': 'error', 'message': '缺少必要的参数'})

        lyrics_path = data['lyricsPath']
        translation_path = data['translationPath']

        lyrics_filename = os.path.basename(lyrics_path)
        translation_filename = os.path.basename(translation_path)

        lyrics_full_path = SONGS_DIR / lyrics_filename
        translation_full_path = SONGS_DIR / translation_filename

        if not lyrics_full_path.exists() or not translation_full_path.exists():
            return jsonify({'status': 'error', 'message': '找不到歌词或翻译文件'})

        with open(lyrics_full_path, 'r', encoding='utf-8') as f:
            lyrics_content = f.read()
        with open(translation_full_path, 'r', encoding='utf-8') as f:
            translation_content = f.read()

        # 组装LQE内容
        lqe_content = "[lyrics: format@Lyricify Syllable]\n"
        lqe_content += lyrics_content.strip() + "\n\n"
        lqe_content += "[translation: format@LRC]\n"
        lqe_content += translation_content.strip() + "\n"

        return jsonify({
            'status': 'success',
            'content': lqe_content
        })

    except Exception as e:
        app.logger.error(f"合并LQE时出错: {str(e)}", exc_info=True)
        return jsonify({'status': 'error', 'message': f'处理请求时出错: {str(e)}'})

@app.route('/extract_timestamps', methods=['POST'])
def extract_timestamps():
    try:
        data = request.json
        lyrics_content = data.get('content', '')
        app.logger.info(f"收到歌词内容，长度: {len(lyrics_content)} 字符")
        
        # 记录内容摘要（前3行+后3行）
        lines = lyrics_content.split('\n')
        if lines:
            preview_lines = lines[:3] + ['...'] + lines[-3:] if len(lines) > 6 else lines
            app.logger.debug(f"内容预览: {preview_lines}")
        
        # 将毫秒转换为分:秒.毫秒格式
        def convert_milliseconds_to_time(milliseconds):
            total_seconds = milliseconds // 1000
            minutes = total_seconds // 60
            seconds = total_seconds % 60
            millis = milliseconds % 1000
            return f"{minutes:02d}:{seconds:02d}.{millis:03d}"
        
        # 提取时间戳并转换为LRC格式
        timestamps = []
        lines = lyrics_content.split('\n')
        app.logger.info(f"歌词总行数: {len(lines)}")

        metadata_lines = 0
        empty_lines = 0
        processed_lines = 0

        metadata_pattern = re.compile(r'^\s*\[(?:ar|ti|al|by|offset|id|from):', re.IGNORECASE)
        qrc_pattern = re.compile(r'^\s*\[(\d+)\s*,\s*(\d+)\]')
        lys_marker_pattern = re.compile(r'^\s*\[(\d*)\]')
        lrc_pattern = re.compile(r'^\s*\[(\d{1,2}):(\d{2})(?:\.(\d{1,3}))?\]')

        def append_timestamp_from_ms(raw_ms: int, line_no: int, source: str):
            nonlocal processed_lines
            time_str = convert_milliseconds_to_time(raw_ms)
            lrc_timestamp = f"[{time_str}]"
            timestamps.append(lrc_timestamp)
            processed_lines += 1
            app.logger.debug(
                f"第{line_no}行: {source} -> '{lrc_timestamp}' (原始值: {raw_ms}ms)"
            )

        for i, raw_line in enumerate(lines, 1):
            line = raw_line.strip()
            if not line:
                empty_lines += 1
                app.logger.debug(f"第{i}行: 空行，跳过")
                continue

            if metadata_pattern.match(line):
                metadata_lines += 1
                app.logger.debug(f"第{i}行: 元数据行 '{line[:50]}...'，跳过")
                continue

            # QRC: 行首为 [start,duration]
            qrc_match = qrc_pattern.match(line)
            if qrc_match:
                try:
                    start_ms = int(qrc_match.group(1))
                    append_timestamp_from_ms(start_ms, i, 'QRC起始时间')
                    app.logger.debug(
                        f"第{i}行内容: '{raw_line[:100]}...'"
                        if len(raw_line) > 100 else f"第{i}行内容: '{raw_line}'"
                    )
                    continue
                except ValueError:
                    app.logger.warning(f"第{i}行: QRC起始时间解析失败 '{qrc_match.group(1)}'")
                    continue

            # LRC: 行首为 [mm:ss.xxx]
            lrc_match = lrc_pattern.match(line)
            if lrc_match:
                try:
                    minutes = int(lrc_match.group(1))
                    seconds = int(lrc_match.group(2))
                    millis_str = lrc_match.group(3) or ''
                    millis = int((millis_str + '000')[:3]) if millis_str else 0
                    total_ms = (minutes * 60 + seconds) * 1000 + millis
                    append_timestamp_from_ms(total_ms, i, 'LRC时间戳')
                    app.logger.debug(
                        f"第{i}行内容: '{raw_line[:100]}...'"
                        if len(raw_line) > 100 else f"第{i}行内容: '{raw_line}'"
                    )
                    continue
                except ValueError as e:
                    app.logger.warning(f"第{i}行: LRC时间戳转换失败，错误: {str(e)}")
                    continue

            # LYS 逐字格式: 先检索行标记，再找 (start_ms, duration)
            if lys_marker_pattern.match(line):
                match = re.search(r'\((\d+),', line)
                if match:
                    try:
                        timestamp = int(match.group(1))
                        append_timestamp_from_ms(timestamp, i, 'LYS起始时间')
                        app.logger.debug(
                            f"第{i}行内容: '{raw_line[:100]}...'"
                            if len(raw_line) > 100 else f"第{i}行内容: '{raw_line}'"
                        )
                        continue
                    except ValueError as e:
                        app.logger.warning(f"第{i}行: 时间戳转换失败 '{match.group(1)}', 错误: {str(e)}")
                        continue

            app.logger.debug(
                f"第{i}行: 未识别的时间戳格式，跳过。内容: '{raw_line[:100]}...'"
                if len(raw_line) > 100 else f"第{i}行: 未识别的时间戳格式，跳过。内容: '{raw_line}'"
            )
        
        # 记录详细统计信息
        app.logger.info(f"处理统计 - 总行数: {len(lines)}, 空行: {empty_lines}, 元数据行: {metadata_lines}, 处理行数: {processed_lines}")
        app.logger.info(f"成功提取时间戳数量: {len(timestamps)}")
        if timestamps:
            app.logger.info(f"第一个时间戳: {timestamps[0]}")
            app.logger.info(f"最后一个时间戳: {timestamps[-1]}")
        else:
            app.logger.warning("未提取到任何时间戳，请检查歌词格式")
        
        return jsonify({
            'status': 'success',
            'timestamps': timestamps
        })
    except Exception as e:
        app.logger.error(f"提取时间戳时出错: {str(e)}", exc_info=True)
        return jsonify({'status': 'error', 'message': f'处理请求时出错: {str(e)}'})

@app.route('/extract_lyrics', methods=['POST'])
def extract_lyrics():
    try:
        data = request.json
        lyrics_content = data.get('content', '')
        app.logger.info(f"收到歌词内容用于提取歌词文本，长度: {len(lyrics_content)} 字符")
        
        # 记录内容摘要
        lines = lyrics_content.split('\n')
        app.logger.info(f"原始歌词总行数: {len(lines)}")
        if lines:
            preview_lines = lines[:3] + ['...'] + lines[-3:] if len(lines) > 6 else lines
            app.logger.debug(f"原始内容预览: {preview_lines}")
        
        # 使用正则表达式提取每行歌词（排除时间戳部分）
        extracted_lyrics = []
        empty_lines = 0
        processed_lines = 0
        filtered_lines = 0
        
        app.logger.info("开始处理歌词内容...")
        extracted_lyrics = []
        
        # 遍历每行，提取每行中的歌词并去除时间戳
        for line in lines:
            # 使用正则表达式去掉所有中括号及其内容，以及时间戳部分
            line_lyrics = re.sub(r'\[.*?\]', '', line)  # 去掉所有中括号及其内容
            line_lyrics = re.sub(r'\([0-9,]+\)', '', line_lyrics)  # 去掉时间戳部分
            line_lyrics = line_lyrics.strip()  # 去掉首尾空白字符
            if line_lyrics:  # 如果该行有歌词内容
                extracted_lyrics.append(line_lyrics)
        
        # 将每行歌词添加换行符
        cleaned_lyrics = '\n'.join(extracted_lyrics)
        
        # 记录详细统计信息
        app.logger.info(f"提取统计 - 总行数: {len(lines)}, 空行: {empty_lines}, 过滤行: {filtered_lines}, 成功提取: {processed_lines}")
        app.logger.info(f"最终提取歌词内容长度: {len(cleaned_lyrics)} 字符，行数: {len(extracted_lyrics)}")
        
        if extracted_lyrics:
            preview_extracted = extracted_lyrics[:3] + ['...'] + extracted_lyrics[-3:] if len(extracted_lyrics) > 6 else extracted_lyrics
            app.logger.debug(f"提取结果预览: {preview_extracted}")
        else:
            app.logger.warning("未提取到任何歌词内容")
        
        return jsonify({
            'status': 'success',
            'content': cleaned_lyrics
        })
    except Exception as e:
        app.logger.error(f"提取歌词时出错: {str(e)}", exc_info=True)
        return jsonify({'status': 'error', 'message': f'处理请求时出错: {str(e)}'})

@app.route('/get_ai_settings', methods=['GET'])
def get_ai_settings():
    global AI_TRANSLATION_SETTINGS
    if isinstance(AI_TRANSLATION_SETTINGS.get('system_prompt'), str) and not AI_TRANSLATION_SETTINGS['system_prompt'].strip():
        AI_TRANSLATION_SETTINGS['system_prompt'] = AI_TRANSLATION_DEFAULTS['system_prompt']
    if isinstance(AI_TRANSLATION_SETTINGS.get('thinking_system_prompt'), str) and not AI_TRANSLATION_SETTINGS['thinking_system_prompt'].strip():
        AI_TRANSLATION_SETTINGS['thinking_system_prompt'] = AI_TRANSLATION_DEFAULTS['thinking_system_prompt']
    if 'strip_brackets' not in AI_TRANSLATION_SETTINGS:
        AI_TRANSLATION_SETTINGS['strip_brackets'] = AI_TRANSLATION_DEFAULTS.get('strip_brackets', False)
    return jsonify({
        'status': 'success',
        'settings': AI_TRANSLATION_SETTINGS
    })

@app.route('/save_ai_settings', methods=['POST'])
def save_ai_settings():
    if not is_request_allowed():
        return abort(403)
    try:
        data = request.json
        global AI_TRANSLATION_SETTINGS
        AI_TRANSLATION_SETTINGS['api_key'] = data.get('api_key', '')
        system_prompt_input = data.get('system_prompt')
        if isinstance(system_prompt_input, str) and not system_prompt_input.strip():
            system_prompt_input = AI_TRANSLATION_DEFAULTS['system_prompt']
        elif system_prompt_input is None:
            system_prompt_input = AI_TRANSLATION_SETTINGS['system_prompt'] or AI_TRANSLATION_DEFAULTS['system_prompt']
        AI_TRANSLATION_SETTINGS['system_prompt'] = system_prompt_input
        AI_TRANSLATION_SETTINGS['provider'] = data.get('provider', AI_TRANSLATION_SETTINGS['provider'])
        AI_TRANSLATION_SETTINGS['base_url'] = data.get('base_url', AI_TRANSLATION_SETTINGS['base_url'])
        AI_TRANSLATION_SETTINGS['model'] = data.get('model', AI_TRANSLATION_SETTINGS['model'])
        AI_TRANSLATION_SETTINGS['expect_reasoning'] = data.get('expect_reasoning', AI_TRANSLATION_SETTINGS['expect_reasoning'])
        AI_TRANSLATION_SETTINGS['strip_brackets'] = parse_bool(
            data.get('strip_brackets'),
            AI_TRANSLATION_SETTINGS.get('strip_brackets', AI_TRANSLATION_DEFAULTS.get('strip_brackets', False))
        )
        AI_TRANSLATION_SETTINGS['compat_mode'] = parse_bool(data.get('compat_mode'), AI_TRANSLATION_SETTINGS['compat_mode'])
        AI_TRANSLATION_SETTINGS['thinking_enabled'] = parse_bool(data.get('thinking_enabled'), AI_TRANSLATION_SETTINGS['thinking_enabled'])
        AI_TRANSLATION_SETTINGS['thinking_api_key'] = data.get('thinking_api_key', AI_TRANSLATION_SETTINGS['thinking_api_key'])
        AI_TRANSLATION_SETTINGS['thinking_provider'] = data.get('thinking_provider', AI_TRANSLATION_SETTINGS['thinking_provider'])
        AI_TRANSLATION_SETTINGS['thinking_base_url'] = data.get('thinking_base_url', AI_TRANSLATION_SETTINGS['thinking_base_url'])
        AI_TRANSLATION_SETTINGS['thinking_model'] = data.get('thinking_model', AI_TRANSLATION_SETTINGS['thinking_model'])
        thinking_prompt_input = data.get('thinking_system_prompt')
        if isinstance(thinking_prompt_input, str) and not thinking_prompt_input.strip():
            thinking_prompt_input = AI_TRANSLATION_DEFAULTS['thinking_system_prompt']
        elif thinking_prompt_input is None:
            thinking_prompt_input = AI_TRANSLATION_SETTINGS['thinking_system_prompt'] or AI_TRANSLATION_DEFAULTS['thinking_system_prompt']
        AI_TRANSLATION_SETTINGS['thinking_system_prompt'] = thinking_prompt_input
        return jsonify({
            'status': 'success',
            'api_key': AI_TRANSLATION_SETTINGS['api_key'],
            'system_prompt': AI_TRANSLATION_SETTINGS['system_prompt'],
            'provider': AI_TRANSLATION_SETTINGS['provider'],
            'base_url': AI_TRANSLATION_SETTINGS['base_url'],
            'model': AI_TRANSLATION_SETTINGS['model'],
            'expect_reasoning': AI_TRANSLATION_SETTINGS['expect_reasoning'],
            'strip_brackets': AI_TRANSLATION_SETTINGS['strip_brackets'],
            'compat_mode': AI_TRANSLATION_SETTINGS['compat_mode'],
            'thinking_enabled': AI_TRANSLATION_SETTINGS['thinking_enabled'],
            'thinking_api_key': AI_TRANSLATION_SETTINGS['thinking_api_key'],
            'thinking_provider': AI_TRANSLATION_SETTINGS['thinking_provider'],
            'thinking_base_url': AI_TRANSLATION_SETTINGS['thinking_base_url'],
            'thinking_model': AI_TRANSLATION_SETTINGS['thinking_model'],
            'thinking_system_prompt': AI_TRANSLATION_SETTINGS['thinking_system_prompt']
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})

@app.route('/probe_ai', methods=['POST'])
def probe_ai():
    if not is_request_allowed():
        return abort(403)
    try:
        request_data = request.get_json(silent=True) or {}
        mode = (request_data.get('mode') or 'translation').lower()

        if mode == 'thinking':
            api_key = request_data.get('api_key') or AI_TRANSLATION_SETTINGS.get('thinking_api_key') or AI_TRANSLATION_SETTINGS.get('api_key')
            base_url_raw = request_data.get('base_url') or AI_TRANSLATION_SETTINGS.get('thinking_base_url') or AI_TRANSLATION_SETTINGS.get('base_url')
        else:
            api_key = request_data.get('api_key') or AI_TRANSLATION_SETTINGS.get('api_key')
            base_url_raw = request_data.get('base_url') or AI_TRANSLATION_SETTINGS.get('base_url')

        # 规范化 base_url，去掉用户误填的 /chat/completions 等尾巴
        def _normalize_base_url(u: str) -> str:
            if not u: return u
            u = u.strip().rstrip('/')
            import re
            return re.sub(r'/(chat|responses)/(completions|streams?)$', '', u, flags=re.I)

        base_url = _normalize_base_url(base_url_raw)
        if not api_key:
            target = '思考模型' if mode == 'thinking' else '翻译模型'
            return jsonify({'status': 'error', 'message': f'未提供{target}的API密钥'})
        if not base_url:
            target = '思考模型' if mode == 'thinking' else '翻译模型'
            return jsonify({'status': 'error', 'message': f'未提供{target}的Base URL'})

        client = build_openai_client(api_key=api_key, base_url=base_url)
        def _models_endpoint_missing(err: Exception) -> bool:
            status_code = getattr(err, 'status_code', None)
            if status_code == 404:
                return True
            resp = getattr(err, 'response', None)
            if resp is not None and getattr(resp, 'status_code', None) == 404:
                return True
            text = str(err)
            if not text:
                return False
            if '404' in text and '/v1/models' in text:
                return True
            return False

        try:
            models = client.models.list()
            names = [m.id for m in getattr(models, 'data', [])]
            return jsonify({'status': 'success', 'models': names[:200], 'base_url': base_url, 'mode': mode})
        except Exception as probe_error:
            if _models_endpoint_missing(probe_error):
                app.logger.warning(
                    "AI模型探活: 目标接口返回404，推测不支持 /v1/models 列表。继续返回成功状态。Base URL: %s, Mode: %s, 错误: %s",
                    base_url,
                    mode,
                    probe_error
                )
                return jsonify({
                    'status': 'success',
                    'models': [],
                    'base_url': base_url,
                    'mode': mode,
                    'note': 'models_endpoint_unavailable'
                })
            raise
    except Exception as e:
        app.logger.error(f"探活AI服务时出错: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': f'探活失败: {e}', 'base_url': base_url_raw, 'mode': request_data.get('mode', 'translation')})

@app.route('/translate_lyrics', methods=['POST'])
def translate_lyrics():
    try:
        # 获取请求数据
        request_data = request.get_json()
        content = request_data.get('content', '')
        api_key = request_data.get('api_key', '')
        # 优先用前端传的 system_prompt，没有则用全局默认
        system_prompt_input = request_data.get('system_prompt')
        if isinstance(system_prompt_input, str) and not system_prompt_input.strip():
            system_prompt = AI_TRANSLATION_SETTINGS['system_prompt']
        elif system_prompt_input is None:
            system_prompt = AI_TRANSLATION_SETTINGS['system_prompt']
        else:
            system_prompt = system_prompt_input

        # 获取API配置参数，优先使用请求数据中的参数，否则使用全局默认值
        provider = request_data.get('provider') or AI_TRANSLATION_SETTINGS['provider']
        base_url = request_data.get('base_url') or AI_TRANSLATION_SETTINGS['base_url']
        model = request_data.get('model') or AI_TRANSLATION_SETTINGS['model']
        expect_reasoning = request_data.get('expect_reasoning', AI_TRANSLATION_SETTINGS['expect_reasoning'])
        strip_brackets = parse_bool(
            request_data.get('strip_brackets'),
            AI_TRANSLATION_SETTINGS.get('strip_brackets', False)
        )

        compat_mode = parse_bool(request_data.get('compat_mode'), AI_TRANSLATION_SETTINGS['compat_mode'])
        thinking_enabled = parse_bool(
            request_data.get('thinking_enabled'),
            AI_TRANSLATION_SETTINGS.get('thinking_enabled', True)
        )

        thinking_api_key = request_data.get('thinking_api_key')
        if not thinking_api_key:
            thinking_api_key = AI_TRANSLATION_SETTINGS.get('thinking_api_key') or api_key
        thinking_provider = request_data.get('thinking_provider') or AI_TRANSLATION_SETTINGS.get('thinking_provider') or provider
        thinking_base_url = request_data.get('thinking_base_url') or AI_TRANSLATION_SETTINGS.get('thinking_base_url') or base_url
        thinking_model = request_data.get('thinking_model') or AI_TRANSLATION_SETTINGS.get('thinking_model') or model
        thinking_prompt_input = request_data.get('thinking_system_prompt')
        if isinstance(thinking_prompt_input, str) and not thinking_prompt_input.strip():
            thinking_system_prompt = AI_TRANSLATION_SETTINGS.get('thinking_system_prompt') or ''
        elif thinking_prompt_input is None:
            thinking_system_prompt = AI_TRANSLATION_SETTINGS.get('thinking_system_prompt') or ''
        else:
            thinking_system_prompt = thinking_prompt_input

        # 规范化 base_url，自动剔除多余路径
        def _normalize_base_url(u: str) -> str:
            if not u:
                return u
            u = u.strip().rstrip('/')
            # 去掉用户误填的 /chat/completions 或 /responses/...
            import re
            u = re.sub(r'/(chat|responses)/(completions|streams?)$', '', u, flags=re.I)
            return u

        base_url = _normalize_base_url(base_url)
        thinking_base_url = _normalize_base_url(thinking_base_url)

        if not content:
            return jsonify({'status': 'error', 'message': '未提供歌词内容'})

        if not api_key:
            return jsonify({'status': 'error', 'message': '请先设置API密钥'})

        # 获取客户端信息用于日志
        client_ip = request.remote_addr
        user_agent = request.headers.get('User-Agent', 'Unknown')
        request_id = f"{int(time.time()*1000)}_{random.randint(1000, 9999)}"
        
        app.logger.info("="*50)
        app.logger.info(f"开始处理翻译请求 [ID: {request_id}]")
        app.logger.info(f"客户端: {client_ip}, User-Agent: {user_agent}")
        app.logger.info(f"原始歌词内容长度: {len(content)} 字符")
        app.logger.info(f"API密钥: {api_key[:8]}...{api_key[-4:] if len(api_key) > 12 else '****'}")

        # 1. 使用现有的提取功能获取时间戳和歌词
        timestamps_response = extract_timestamps()
        lyrics_response = extract_lyrics()
        
        # 从响应中获取数据
        timestamps = timestamps_response.json.get('timestamps', [])
        lyrics = lyrics_response.json.get('content', '').split('\n')

        # 基于原始歌词判断是否存在时间戳模式
        candidate_lines = [
            line.strip()
            for line in content.splitlines()
            if line.strip()
            and not line.startswith('[by:')
            and not line.startswith('[ti:')
            and not line.startswith('[ar:')
        ]
        timestamp_candidates = sum(
            1
            for line in candidate_lines
            if (
                re.search(r'\(\d+,\d+\)', line)
                or re.match(r'^\s*\[\d+\s*,\s*\d+\]', line)
                or re.match(r'^\s*\[\d{1,2}:\d{2}(?:\.\d{1,3})?\]', line)
            )
        )
        has_timestamp_candidates = timestamp_candidates > 0
        has_timestamps = len(timestamps) > 0

        app.logger.info(f"提取的时间戳数量: {len(timestamps)}")
        app.logger.info(f"检测到包含时间戳标记的行数: {timestamp_candidates}")
        if strip_brackets:
            bracket_modified = 0
            processed_lyrics = []
            for line in lyrics:
                cleaned_line = strip_bracket_blocks(line)
                if cleaned_line != line:
                    bracket_modified += 1
                processed_lyrics.append(cleaned_line)
            lyrics = processed_lyrics
            app.logger.info(f"去括号预处理: 开启，修改行数: {bracket_modified}")
        else:
            app.logger.info("去括号预处理: 关闭")
        app.logger.info(f"提取的歌词行数: {len(lyrics)}")
        processed_preview = '\n'.join(f"{i+1}. {line}" for i, line in enumerate(lyrics))
        app.logger.info("预处理后的歌词内容（带行号）:\n%s", processed_preview if processed_preview else "[无可用歌词]")
        app.logger.info(f"系统提示词: {system_prompt[:100]}..." if len(system_prompt) > 100 else f"系统提示词: {system_prompt}")
        app.logger.info(f"兼容模式: {'开启' if compat_mode else '关闭'}")
        app.logger.info(f"翻译模型配置: provider={provider}, base_url={base_url}, model={model}")
        app.logger.info(f"思考模式: {'开启' if thinking_enabled else '关闭'}")
        masked_thinking_key = thinking_api_key[:8] + '...' + thinking_api_key[-4:] if thinking_api_key and len(thinking_api_key) > 12 else (thinking_api_key or '')
        app.logger.info(f"思考模型配置: provider={thinking_provider}, base_url={thinking_base_url}, model={thinking_model}, api_key={masked_thinking_key or '沿用翻译密钥'}")
        if thinking_system_prompt:
            app.logger.info(f"思考提示词: {thinking_system_prompt[:100]}..." if len(thinking_system_prompt) > 100 else f"思考提示词: {thinking_system_prompt}")

        # 验证提取的内容
        if not lyrics or all(not line.strip() for line in lyrics):
            app.logger.error("未提取到任何歌词内容")
            return jsonify({'status': 'error', 'message': '未提取到任何歌词内容，请检查歌词格式是否正确'})

        if not has_timestamps:
            if has_timestamp_candidates:
                app.logger.error("原始歌词包含时间戳标记，但未能成功解析任何时间戳")
                return jsonify({'status': 'error', 'message': '未提取到任何时间戳，请检查歌词格式是否正确'})
            app.logger.info("检测到无时间戳歌词，将跳过时间戳对齐与输出。")
        else:
            if len(timestamps) != len(lyrics):
                app.logger.error(f"时间戳数量({len(timestamps)})与歌词行数({len(lyrics)})不匹配")
                suspect_lines = []
                for idx, raw_line in enumerate(content.splitlines(), 1):
                    stripped_line = raw_line.strip()
                    if not stripped_line:
                        continue
                    if stripped_line.startswith('[by:') or stripped_line.startswith('[ti:') or stripped_line.startswith('[ar:'):
                        continue
                    if re.search(r'\(\d+,\d+\)', stripped_line) and not re.match(r'^\[(\d*)\]', stripped_line):
                        suspect_lines.append({'line_number': idx, 'line_content': stripped_line})

                if suspect_lines:
                    formatted = '; '.join(
                        f"第{item['line_number']}行: {item['line_content']}"
                        for item in suspect_lines[:3]
                    )
                    hint_message = (
                        f"检测到可能缺少中括号的行，请修正后重试。疑似问题行: {formatted}"
                    )
                    app.logger.error(hint_message)
                    return jsonify({
                        'status': 'error',
                        'message': f"时间戳数量与歌词行数不匹配。{hint_message}",
                        'suspectLines': suspect_lines
                    })

                return jsonify({
                    'status': 'error',
                    'message': '时间戳数量与歌词行数不匹配，请检查歌词格式是否正确'
                })

        line_prefixes = timestamps if has_timestamps else [''] * len(lyrics)

        # 检查歌词内容是否包含非法字符
        illegal_chars = ['content:', 'reasoning:']
        for i, line in enumerate(lyrics):
            for char in illegal_chars:
                if char in line:
                    app.logger.error(f"第{i+1}行歌词包含非法字符: {char}")
                    return jsonify({'status': 'error', 'message': f'歌词内容包含非法字符，请检查第{i+1}行'})

        # 2. 调用AI服务进行翻译
        def generate():
            try:
                # 构建提示词
                numbered_lyrics = '\n'.join(f"{i+1}.{line}" for i, line in enumerate(lyrics))
                # 使用上面优先级逻辑的 system_prompt
                app.logger.debug("发送给AI的提示词:")
                app.logger.debug(f"系统提示词: {system_prompt}")
                app.logger.debug(f"用户输入摘要:\n{numbered_lyrics[:500]}..." if len(numbered_lyrics) > 500 else f"用户输入:\n{numbered_lyrics}")

                thinking_summary = ""
                if thinking_enabled and thinking_model:
                    app.logger.info(f"开始调用思考模型 {thinking_model} [ID: {request_id}]")
                    thinking_start_time = time.time()
                    try:
                        if compat_mode:
                            thinking_parts = []
                            if thinking_system_prompt:
                                thinking_parts.append(thinking_system_prompt.strip())
                            thinking_parts.append(numbered_lyrics)
                            thinking_payload = '\n\n'.join(part for part in thinking_parts if part)
                            thinking_messages = [
                                {"role": "user", "content": thinking_payload}
                            ]
                        else:
                            thinking_messages = []
                            if thinking_system_prompt:
                                thinking_messages.append({"role": "system", "content": thinking_system_prompt})
                            thinking_messages.append({"role": "user", "content": numbered_lyrics})

                        thinking_client = build_openai_client(api_key=thinking_api_key, base_url=thinking_base_url)
                        thinking_response = thinking_client.chat.completions.create(
                            model=thinking_model,
                            messages=thinking_messages,
                            stream=True
                        )
                        app.logger.info(f"思考模型开始流式输出 [ID: {request_id}]")
                        thinking_chunks = 0
                        thinking_tokens = 0
                        current_thinking = ""
                        for chunk in thinking_response:
                            thinking_chunks += 1
                            choices = getattr(chunk, 'choices', None)
                            if not choices:
                                continue
                            delta = getattr(choices[0], 'delta', None)
                            if delta is None:
                                continue
                            if hasattr(chunk, 'usage') and chunk.usage:
                                thinking_tokens = getattr(chunk.usage, 'total_tokens', 0)
                            if hasattr(delta, 'content') and delta.content:
                                addition = delta.content
                                current_thinking += addition
                                thinking_summary = current_thinking
                                yield f"thinking:{json.dumps({'summary': thinking_summary})}\n"
                            if hasattr(delta, 'reasoning_content') and delta.reasoning_content:
                                addition = delta.reasoning_content
                                current_thinking += addition
                                thinking_summary = current_thinking
                                yield f"thinking:{json.dumps({'summary': thinking_summary})}\n"
                        if not thinking_summary:
                            thinking_summary = current_thinking
                        thinking_duration = time.time() - thinking_start_time
                        app.logger.info(f"思考模型完成 [ID: {request_id}], 耗时: {thinking_duration:.2f}秒, 输出长度: {len(thinking_summary)}, 数据块: {thinking_chunks}, 估计Tokens: {thinking_tokens}")
                        thinking_summary = (thinking_summary or '').strip()
                    except Exception as thinking_error:
                        thinking_duration = time.time() - thinking_start_time
                        app.logger.error(f"思考模型调用失败 [ID: {request_id}]: {thinking_error} (耗时 {thinking_duration:.2f}秒)", exc_info=True)
                        yield f"thinking:{json.dumps({'error': str(thinking_error)})}\n"
                        thinking_summary = ""
                else:
                    app.logger.info(f"未配置思考模型，直接进入翻译流程 [ID: {request_id}]")

                if compat_mode:
                    combined_prompt_parts = []
                    if system_prompt:
                        combined_prompt_parts.append(system_prompt.strip())
                    if thinking_summary:
                        combined_prompt_parts.append(f"歌曲理解：\n{thinking_summary}")
                    combined_prompt_parts.append(f"待翻译歌词：\n{numbered_lyrics}")
                    combined_prompt = '\n\n'.join(part for part in combined_prompt_parts if part)
                    messages = [
                        {"role": "user", "content": combined_prompt}
                    ]
                    app.logger.debug("兼容模式启用：系统提示词已合并到用户消息")
                else:
                    user_content_parts = []
                    if thinking_summary:
                        user_content_parts.append(f"歌曲理解：\n{thinking_summary}")
                    user_content_parts.append(f"待翻译歌词：\n{numbered_lyrics}")
                    user_content = '\n\n'.join(part for part in user_content_parts if part)
                    messages = [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_content}
                    ]

                # 记录API调用详细信息
                app.logger.info(f"准备调用 {provider} API [ID: {request_id}]")
                app.logger.info(f"基础URL: {base_url}, 模型: {model}, 歌词行数: {len(lyrics)}")
                app.logger.info(f"提示词长度: {len(numbered_lyrics)} 字符")
                app.logger.info(f"系统提示词摘要: {system_prompt[:200]}..." if len(system_prompt) > 200 else f"系统提示词: {system_prompt}")

                # 调用AI服务
                api_start_time = time.time()
                try:
                    client = build_openai_client(api_key=api_key, base_url=base_url)
                    response = client.chat.completions.create(
                        model=model,
                        messages=messages,
                        stream=True
                    )
                    api_call_success = True
                except Exception as api_error:
                    api_call_success = False
                    api_error_msg = str(api_error)
                    api_error_type = type(api_error).__name__
                    app.logger.error(f"API调用失败 [ID: {request_id}]: {api_error_type} - {api_error_msg}", exc_info=True)
                    raise

                # 记录API调用成功
                api_response_time = time.time() - api_start_time
                app.logger.info(f"API调用成功 [ID: {request_id}], 响应时间: {api_response_time:.2f}秒")

                # 收集翻译结果
                full_translation = ""
                reasoning_content = ""
                current_reasoning = ""
                total_tokens = 0
                received_chunks = 0

                app.logger.info("开始接收AI流式响应...")
                stream_start_time = time.time()
                for chunk in response:
                    received_chunks += 1

                    choices = getattr(chunk, 'choices', None)
                    if not choices:
                        app.logger.debug(f"收到空choices数据块 [ID: {request_id}]，跳过处理")
                        continue
                    delta = getattr(choices[0], 'delta', None)
                    if delta is None:
                        app.logger.debug(f"数据块缺少delta字段 [ID: {request_id}]，跳过处理")
                        continue
                    
                    # 记录token使用情况（如果有）
                    if hasattr(chunk, 'usage') and chunk.usage:
                        total_tokens = getattr(chunk.usage, 'total_tokens', 0)
                        
                    # 检查是否有思维链内容
                    if expect_reasoning and hasattr(delta, 'reasoning_content') and delta.reasoning_content:
                        content = delta.reasoning_content
                        current_reasoning += content
                        app.logger.debug(f"收到思维链内容 [ID: {request_id}]: {content}")
                        # 发送思维链内容
                        yield f"reasoning:{json.dumps({'reasoning': current_reasoning})}\n"
                    
                    # 检查是否有普通内容
                    if hasattr(delta, 'content') and delta.content:
                        content = delta.content
                        full_translation += content
                        app.logger.debug(f"收到翻译内容 [ID: {request_id}]: {content}")

                        # 处理翻译内容：使用字典按行号稳定对齐（后到的覆盖先到的）
                        lines = full_translation.split('\n')
                        translated_dict = {}  # 行号(0-based) -> 翻译内容
                        for line in lines:
                            if line.strip() and not line.startswith('思考'):
                                # 提取序号和翻译内容
                                match = re.match(r'^(\d+)\.(.*)', line)
                                if match:
                                    line_num = int(match.group(1))  # 1-based
                                    content = match.group(2).strip()
                                    # 转为0-based索引并存储（后到的覆盖先到的）
                                    translated_dict[line_num - 1] = content

                        # 使用字典按行号稳定对齐，即使行号乱序或缺失也能正确处理
                        if translated_dict:
                            # 按原始顺序合并翻译结果；若缺失时间戳则直接返回纯文本
                            final_lyrics = []
                            for i in range(len(lyrics)):
                                translation = translated_dict.get(i)
                                if translation is not None:
                                    prefix = line_prefixes[i] if i < len(line_prefixes) else ''
                                    final_line = f"{prefix}{translation}" if prefix else translation
                                    final_lyrics.append(final_line)

                            # 发送翻译内容（只发送有翻译的行）
                            if final_lyrics:
                                payload = {
                                    'translations': final_lyrics,
                                    'hasTimestamps': has_timestamps
                                }
                                yield f"content:{json.dumps(payload)}\n"
                                app.logger.debug(f"成功合并 {len(final_lyrics)} 行翻译歌词")
                        else:
                            app.logger.warning("未提取到有效的翻译内容")
                            app.logger.debug(f"当前完整翻译内容预览:\n{full_translation[:500]}..." if len(full_translation) > 500 else f"当前完整翻译内容:\n{full_translation}")
                
                # 记录流式响应完成
                stream_duration = time.time() - stream_start_time
                app.logger.info(f"流式响应完成 [ID: {request_id}], 耗时: {stream_duration:.2f}秒")
                app.logger.info(f"总共接收 {received_chunks} 个数据块, 估计Token使用: {total_tokens}")

            except Exception as e:
                error_time = time.time()
                error_duration = error_time - api_start_time
                app.logger.error(f"AI翻译过程中出错 [ID: {request_id}]: {str(e)}, 总耗时: {error_duration:.2f}秒", exc_info=True)
                yield f"content:翻译过程中出错: {str(e)}\n"
            else:
                # 记录翻译成功完成
                total_duration = time.time() - api_start_time
                app.logger.info(f"翻译成功完成 [ID: {request_id}], 总耗时: {total_duration:.2f}秒")
                app.logger.info(f"最终翻译字符数: {len(full_translation)}, 思维链长度: {len(current_reasoning)}")
                app.logger.info(f"API配置: {provider}, {base_url}, {model}, expect_reasoning: {expect_reasoning}, compat_mode: {compat_mode}")

        return Response(generate(), mimetype='text/event-stream')

    except Exception as e:
        error_type = type(e).__name__
        app.logger.error(f"处理翻译请求时出错 [ID: {request_id if 'request_id' in locals() else 'N/A'}]: {error_type} - {str(e)}", exc_info=True)
        
        # 提供更详细的错误信息
        error_message = f'处理请求时出错: {str(e)}'
        if 'request' in str(e).lower() or 'timeout' in str(e).lower():
            error_message += " (网络或超时问题，请检查网络连接)"
        elif 'key' in str(e).lower() or 'auth' in str(e).lower():
            error_message += " (API密钥问题，请检查密钥有效性)"
        elif 'quota' in str(e).lower() or 'limit' in str(e).lower():
            error_message += " (额度限制问题，请检查API额度)"
            
        return jsonify({'status': 'error', 'message': error_message})

@app.after_request
def add_header(resp):
    resp.headers['Access-Control-Allow-Origin'] = '*'
    return resp


@app.route('/player/animation-config', methods=['GET', 'POST'])
def player_animation_config():
    """前后端同步动画时长配置。GET 返回当前值，POST 由前端上报。"""
    if request.method == 'POST':
        payload = request.get_json(silent=True) or {}
        config = update_animation_config(payload)
        return jsonify({'status': 'ok', 'config': config})

    config = load_animation_config()
    return jsonify(config)


@app.route('/lyrics-animate')
def lyrics_animate():
    file = request.args.get('file')
    style = request.args.get('style', 'Kok')  # 默认为 'Kok'
    if not file:
        return "缺少文件参数", 400

    # ✅ 读取临时转换得到的参数，并存入 session，供 /lyrics 使用
    lys_override = request.args.get('lys')
    lrc_override = request.args.get('lrc')
    if lys_override or lrc_override:
        session['override_lys_url'] = lys_override or None
        session['override_lrc_url'] = lrc_override or None
    else:
        session.pop('override_lys_url', None)
        session.pop('override_lrc_url', None)

    cover_override = request.args.get('cover')
    background_override = request.args.get('background')
    if cover_override:
        session['override_cover_url'] = cover_override
    else:
        session.pop('override_cover_url', None)
    if background_override:
        session['override_background_url'] = background_override
    else:
        session.pop('override_background_url', None)

    session['lyrics_json_file'] = file
    if style == '亮起':
        return render_template('Lyrics-style.HTML')
    else:  # 默认为 'Kok' 或其他值
        return render_template('Lyrics-style.HTML-COK.HTML')

@app.route('/lyrics')
def get_lyrics():
    """
    获取歌词和音源信息
    支持的音乐格式：.mp3, .wav, .ogg, .mp4
    """
    json_file = session.get('lyrics_json_file', '测试 - 测试.json')

    # ✅ 优先使用临时转换得到的覆盖地址（来自 /lyrics-animate）
    lys_url = session.get('override_lys_url')
    lrc_url = session.get('override_lrc_url')

    # 如果没有覆盖地址，再走旧逻辑：从 JSON 的 meta.lyrics 里找
    if not lys_url:
        json_path = os.path.join(app.static_folder, json_file)
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                meta_data = json.load(f)
            lyrics_info = meta_data.get('meta', {}).get('lyrics', '')
            lyric_sources = [src for src in lyrics_info.split('::') if src and src != '!']
            for src in lyric_sources:
                if src.endswith('.lys'):
                    lys_url = src
                if src.endswith('.lrc'):
                    lrc_url = lrc_url or src  # JSON 里也可能有翻译
        except FileNotFoundError:
            return jsonify({'error': '元数据JSON未找到'}), 404
        except json.JSONDecodeError:
            return jsonify({'error': '解析元数据JSON时出错'}), 500

    if not lys_url:
        return jsonify({'error': '.lys 文件链接未在元数据或覆盖参数中找到'}), 404

    # 读取 LYS 内容
    from urllib.parse import urlparse
    parsed_url = urlparse(lys_url)
    lyrics_path = os.path.join(app.static_folder, parsed_url.path.lstrip('/'))
    try:
        with open(lyrics_path, 'r', encoding='utf-8-sig') as f:
            lys_content = f.read()
    except FileNotFoundError:
        return jsonify({'error': 'LYS 歌词文件未找到'}), 404

    parsed_lyrics = parse_lys(lys_content)

    # 新增：提取 offset
    offset = 0
    offset_match = re.search(r'\[offset:\s*(-?\d+)\s*\]', lys_content)
    if offset_match:
        offset = int(offset_match.group(1))

    # 解析翻译（优先使用覆盖的 lrc_url）
    translation = []
    if lrc_url:
        parsed_lrc_url = urlparse(lrc_url)
        lrc_path = os.path.join(app.static_folder, parsed_lrc_url.path.lstrip('/'))
        if os.path.exists(lrc_path):
            with open(lrc_path, 'r', encoding='utf-8') as f:
                lrc_content = f.read()
            translation = parse_lrc(lrc_content, offset=offset)  # 传递 offset

    return jsonify({'lyrics': parsed_lyrics, 'translation': translation})

@app.route('/export_lyrics_csv', methods=['POST'])
def export_lyrics_csv():
    if not is_request_allowed():
        return abort(403)
    locked_response = require_unlocked_device('导出歌词')
    if locked_response:
        return locked_response
    try:
        data = request.get_json()
        lyrics_data = data.get('lyrics', [])

        if not lyrics_data:
            return jsonify({'status': 'error', 'message': '未提供歌词数据'}), 400

        # 导出逐字CSV
        csv_path = extract_lyrics_to_csv(lyrics_data)

        if csv_path:
            return jsonify({
                'status': 'success',
                'message': f'导出完成，共导出{len([c for line in lyrics_data for w in line.get("words", []) for c in w.get("word", "")])}个字符',
                'csv_path': str(csv_path)
            })
        else:
            return jsonify({'status': 'error', 'message': '未找到有效的歌词数据'})

    except Exception as e:
        app.logger.error(f"导出CSV时出错: {str(e)}", exc_info=True)
        return jsonify({'status': 'error', 'message': f'导出失败: {str(e)}'})

@app.route('/get_json_data')
def get_json_data():
    if not is_request_allowed():
        return abort(403)
    if not is_style_preview_path(request.path):
        locked_response = require_unlocked_device('查看歌曲数据')
        if locked_response:
            return locked_response
    filename = request.args.get('filename')
    if not filename:
        return jsonify({'status': 'error', 'message': '缺少文件名参数'}), 400
    
    json_path = BASE_PATH / 'static' / filename
    if not json_path.exists():
        return jsonify({'status': 'error', 'message': 'JSON文件未找到'}), 404
    
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            json_data = json.load(f)
        return jsonify({'status': 'success', 'jsonData': json_data})
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'读取JSON文件失败: {str(e)}'}), 500

@app.route('/get_lyrics', methods=['POST'])
def get_lyrics_by_path():
    if not is_request_allowed():
        return abort(403)
    if not is_style_preview_path(request.path):
        locked_response = require_unlocked_device('查看歌词')
        if locked_response:
            return locked_response
    try:
        data = request.get_json()
        lyrics_path = data.get('path', '')
        if not lyrics_path:
            return jsonify({'status': 'error', 'message': '缺少歌词路径'}), 400

        try:
            real_path = resolve_resource_path(lyrics_path, 'songs')
        except ValueError as exc:
            return jsonify({'status': 'error', 'message': f'路径不合法: {exc}'}), 400

        if not real_path.exists():
            return jsonify({'status': 'error', 'message': '歌词文件未找到'}), 404

        with open(real_path, 'r', encoding='utf-8') as f:
            content = f.read()
        return jsonify({'status': 'success', 'content': content})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/song-info')
def song_info():
    json_file = session.get('lyrics_json_file', '测试 - 测试.json')
    json_path = os.path.join(app.static_folder, json_file)
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data_str = f.read()

        # 动态替换 host 和修复路径，以适配局域网访问
        host = request.host
        # 替换 host (127.0.0.1 or localhost)
        data_str = re.sub(r'http://(127\.0\.0\.1|localhost):\d+', f'http://{host}', data_str)
        # 修复路径 (移除 /static/)
        data_str = data_str.replace('/static/songs/', '/songs/')

        data = json.loads(data_str)
        meta = data.setdefault('meta', {}) if isinstance(data, dict) else {}

        override_background = session.get('override_background_url')
        override_cover = session.get('override_cover_url')

        def normalize_media_url(raw_value: Optional[str]) -> str:
            if not raw_value or raw_value == '!':
                return ''
            cleaned = str(raw_value).strip().replace('\\', '/').replace('/static/songs/', '/songs/').replace('static/songs/', 'songs/')
            if not cleaned:
                return ''
            parsed = urlparse(cleaned)
            if parsed.scheme:
                netloc = parsed.netloc
                if parsed.hostname in {'127.0.0.1', 'localhost'}:
                    netloc = host
                path = parsed.path.replace('/static/songs/', '/songs/')
                rebuilt = parsed._replace(netloc=netloc, path=path)
                return rebuilt.geturl()
            if cleaned.startswith('/'):
                return cleaned
            if cleaned.startswith('songs/'):
                return '/' + cleaned
            return '/songs/' + cleaned.lstrip('/')

        # 先规范化现有的封面/背景路径
        if isinstance(meta, dict):
            existing_background = normalize_media_url(meta.get('Background-image'))
            if existing_background:
                meta['Background-image'] = existing_background

            for key in ('albumImgSrc', 'cover', 'coverUrl'):
                normalized = normalize_media_url(meta.get(key))
                if normalized:
                    meta[key] = normalized

        # 应用前端传入的临时覆盖
        normalized_override_background = normalize_media_url(override_background)
        if normalized_override_background:
            meta['Background-image'] = normalized_override_background

        normalized_override_cover = normalize_media_url(override_cover)
        if normalized_override_cover:
            if not meta.get('albumImgSrc') or meta.get('albumImgSrc') == '!':
                meta['albumImgSrc'] = normalized_override_cover
            if not meta.get('cover') or meta.get('cover') == '!':
                meta['cover'] = normalized_override_cover
            if not meta.get('coverUrl') or meta.get('coverUrl') == '!':
                meta['coverUrl'] = normalized_override_cover
            data['cover'] = normalized_override_cover
            data['coverUrl'] = normalized_override_cover

        if 'cover' not in data or not data['cover'] or data['cover'] == '!':
            cover_candidates = [
                meta.get('albumImgSrc') if isinstance(meta, dict) else None,
                meta.get('cover') if isinstance(meta, dict) else None,
                meta.get('coverUrl') if isinstance(meta, dict) else None
            ]
            for candidate in cover_candidates:
                normalized_candidate = normalize_media_url(candidate)
                if normalized_candidate:
                    data['cover'] = normalized_candidate
                    data['coverUrl'] = normalized_candidate
                    break

        return jsonify(data)
    except FileNotFoundError:
        return jsonify({'error': 'Song info file not found'}), 404
    except json.JSONDecodeError:
        return jsonify({'error': 'Error decoding JSON'}), 500

def parse_lrc(lrc_content, offset=0):
    """
    解析LRC格式翻译，返回 [{'time': 'mm:ss.sss', 'content': '...'}]
    支持 offset（毫秒）
    """
    result = []
    lrc_time_re = re.compile(r'\[(\d{2}):(\d{2})\.(\d{2,3})\]')
    for line in lrc_content.splitlines():
        match = lrc_time_re.match(line)
        if match:
            min, sec, ms = match.groups()
            if len(ms) == 2:
                ms = str(int(ms) * 10).zfill(3)
            # 计算原始毫秒
            total_ms = int(min) * 60 * 1000 + int(sec) * 1000 + int(ms)
            # 加 offset
            total_ms += offset
            # 重新格式化
            minutes = total_ms // 60000
            seconds = (total_ms % 60000) // 1000
            millis = total_ms % 1000
            time_str = f"{minutes:02}:{seconds:02}.{millis:03}"
            content = lrc_time_re.sub('', line).strip()
            if content:
                result.append({'time': time_str, 'content': content})
    return result

def is_local_request():
    # 只允许真正的本地回环地址
    remote = request.remote_addr
    
    # 检查安全配置，如果禁用则允许所有访问
    security_config = get_security_config()
    if not security_config.get('security_enabled', True):
        return True
        
    return is_local_remote(remote)

PORT_STATUS_FILE = BASE_PATH / 'port_status.json'
SECURITY_CONFIG_FILE = BASE_PATH / 'security_config.json'
TRUSTED_DEVICES_FILE = BASE_PATH / 'trusted_devices.json'

# 安全配置默认值
DEFAULT_SECURITY_CONFIG = {
    'security_enabled': True,
    'password_hash': '',
    'trusted_expire_days': 30
}

# 读取安全配置
def get_security_config():
    if SECURITY_CONFIG_FILE.exists():
        try:
            with open(SECURITY_CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return DEFAULT_SECURITY_CONFIG

# 保存安全配置
def save_security_config(config):
    with open(SECURITY_CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f)

# 受信任设备数据结构和操作函数

def get_or_set_device_id():
    """获取或设置设备ID，通过HttpOnly Cookie实现"""
    device_id = request.cookies.get('FEW_DEVICE_ID')
    if not device_id:
        device_id = str(uuid.uuid4())
        response = jsonify({})
        response.set_cookie(
            'FEW_DEVICE_ID',
            device_id,
            httponly=True,
            samesite='Lax',
            max_age=365*24*3600  # 1年有效期
        )
        return device_id, response
    return device_id, None

def load_trusted_devices():
    """加载受信任设备列表"""
    if TRUSTED_DEVICES_FILE.exists():
        try:
            with open(TRUSTED_DEVICES_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_trusted_devices(devices):
    """保存受信任设备列表"""
    with open(TRUSTED_DEVICES_FILE, 'w', encoding='utf-8') as f:
        json.dump(devices, f, indent=2)

def hash_password(password):
    """使用bcrypt哈希密码"""
    
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')

def verify_password(password, hashed_password):
    """验证密码"""
    
    try:
        return bcrypt.checkpw(password.encode('utf-8'), hashed_password.encode('utf-8'))
    except:
        return False

def is_trusted_device(device_id):
    """检查设备是否受信任且未过期"""
    if not device_id:
        return False
        
    trusted_devices = load_trusted_devices()
    device_info = trusted_devices.get(device_id)
    
    if not device_info:
        return False
        
    # 检查过期时间
    security_config = get_security_config()
    expire_days = security_config.get('trusted_expire_days', 30)
    expire_seconds = expire_days * 24 * 3600
    
    last_seen = datetime.fromisoformat(device_info['last_seen'])
    if (datetime.now() - last_seen).total_seconds() > expire_seconds:
        # 自动删除过期设备
        del trusted_devices[device_id]
        save_trusted_devices(trusted_devices)
        return False
        
    # 更新最后访问时间
    device_info['last_seen'] = datetime.now().isoformat()
    trusted_devices[device_id] = device_info
    save_trusted_devices(trusted_devices)
    
    return True

def is_local_remote(remote: Optional[str] = None) -> bool:
    """检查请求是否来自本地回环地址"""
    if remote is None:
        remote = getattr(request, 'remote_addr', None)
    if not remote:
        host = getattr(request, 'host', '')
        remote = host.split(':', 1)[0] if host else ''
    normalized = (remote or '').strip().lower()
    if normalized in ('127.0.0.1', '::1', 'localhost'):
        return True
    if normalized.startswith('::ffff:127.'):
        return True
    return False


# 样式预览按钮需要在安全保护模式下也可访问的接口白名单
STYLE_PREVIEW_ALLOW_PATHS = {
    '/get_json_data',
    '/convert_to_ttml_temp',
    '/get_lyrics',
}

def is_style_preview_path(path: str) -> bool:
    """判断是否为样式预览场景下需要放行的接口"""
    return path in STYLE_PREVIEW_ALLOW_PATHS


def is_request_allowed():
    """统一的请求权限检查函数"""
    # 样式预览接口在安全模式下也需放行，便于未授权用户查看歌词样式
    if is_style_preview_path(request.path):
        return True

    # 获取安全配置
    security_config = get_security_config()
    
    # 安全保护关闭时允许所有访问
    if not security_config.get('security_enabled', True):
        return True
        
    # 本机回环地址完全放行
    remote = request.remote_addr
    if is_local_remote(remote):
        return True

    # 根据允许的 CORS 来源放行指定前端（如 AMLL-Web）
    origin = request.headers.get('Origin')
    if origin and _match_cors_origin(origin):
        return True

    referer = request.headers.get('Referer')
    if referer:
        try:
            parsed = urlparse(referer)
            referer_origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else None
            if referer_origin and _match_cors_origin(referer_origin):
                return True
        except Exception:
            pass
        
    # 检查设备是否受信任
    device_id = request.cookies.get('FEW_DEVICE_ID')
    if device_id and is_trusted_device(device_id):
        return True
        
    return False

def is_device_unlocked() -> bool:
    """检查安全防护状态，判断当前设备是否已解锁"""
    security_config = get_security_config()
    
    # 安全防护关闭时放行
    if not security_config.get('security_enabled', True):
        return True
    
    # 本机访问视为超级管理员
    if is_local_remote(request.remote_addr):
        return True
    
    device_id = request.cookies.get('FEW_DEVICE_ID')
    if device_id and is_trusted_device(device_id):
        return True
    
    return False

def require_unlocked_device(action: str):
    """统一处理需要解锁设备的敏感操作"""
    if is_device_unlocked():
        return None
    
    message = f"{action}失败：设备未解锁或安全防护已开启"
    app.logger.warning(
        "Blocked locked-device operation: %s, path=%s, ip=%s",
        action,
        request.path,
        request.remote_addr
    )
    return jsonify({'status': 'error', 'message': message}), 403

# 认证相关API端点
@app.route('/auth/login', methods=['POST'])
def auth_login():
    """设备登录认证"""
    # 获取设备ID
    device_id, cookie_response = get_or_set_device_id()
    
    # 获取密码
    data = request.json
    if not data or 'password' not in data:
        return jsonify({'status': 'error', 'message': '请输入密码'}), 400
    
    password = data['password']
    
    # 验证密码
    security_config = get_security_config()
    password_hash = security_config.get('password_hash', '')
    
    if not password_hash:
        return jsonify({'status': 'error', 'message': '系统未设置密码，请联系管理员'}), 401
    
    if not verify_password(password, password_hash):
        # 记录失败的认证尝试
        app.logger.warning(f"认证失败 - 设备ID: {device_id[:8]}..., IP: {request.remote_addr}, UA哈希: {hashlib.md5(request.headers.get('User-Agent', '').encode()).hexdigest()[:8]}")
        return jsonify({'status': 'error', 'message': '密码错误'}), 401
    
    # 添加设备到受信任列表
    trusted_devices = load_trusted_devices()
    now = datetime.now().isoformat()
    
    if device_id not in trusted_devices:
        trusted_devices[device_id] = {
            'created_at': now,
            'last_seen': now,
            'ua_hash': hashlib.md5(request.headers.get('User-Agent', '').encode()).hexdigest(),
            'ip': request.remote_addr
        }
    else:
        # 更新最后访问时间
        trusted_devices[device_id]['last_seen'] = now
    
    save_trusted_devices(trusted_devices)
    
    # 记录成功的认证
    app.logger.info(f"认证成功 - 设备ID: {device_id[:8]}..., IP: {request.remote_addr}")
    
    response = jsonify({
        'status': 'success',
        'trusted': True,
        'device_id': device_id[:8] + '...'  # 只返回部分ID用于显示
    })
    
    # 如果设置了新Cookie，需要合并响应
    if cookie_response:
        response.set_cookie(
            'FEW_DEVICE_ID',
            device_id,
            httponly=True,
            samesite='Lax',
            max_age=365*24*3600
        )
    
    return response

@app.route('/auth/logout', methods=['POST'])
def auth_logout():
    """设备登出"""
    device_id = request.cookies.get('FEW_DEVICE_ID')
    
    if device_id:
        trusted_devices = load_trusted_devices()
        if device_id in trusted_devices:
            del trusted_devices[device_id]
            save_trusted_devices(trusted_devices)
            app.logger.info(f"设备登出 - 设备ID: {device_id[:8]}..., IP: {request.remote_addr}")
    
    return jsonify({'status': 'success', 'trusted': False})

@app.route('/auth/status', methods=['GET'])
def auth_status():
    """获取设备信任状态"""
    security_config = get_security_config()
    device_id = request.cookies.get('FEW_DEVICE_ID')
    
    trusted = False
    if device_id and is_trusted_device(device_id):
        trusted = True
    
    return jsonify({
        'status': 'success',
        'trusted': trusted,
        'security_enabled': security_config.get('security_enabled', True),
        'has_password': bool(security_config.get('password_hash', ''))
    })

@app.route('/auth/set_password', methods=['POST'])
def auth_set_password():
    """设置密码（仅本机可操作）"""
    if not is_local_request():
        return abort(403)
    
    data = request.json
    if not data or 'password' not in data:
        return jsonify({'status': 'error', 'message': '请输入密码'}), 400
    
    password = data['password']
    
    # 更新安全配置
    security_config = get_security_config()
    security_config['password_hash'] = hash_password(password)
    save_security_config(security_config)
    
    # 清除所有受信任设备（安全起见，修改密码后所有设备需要重新认证）
    save_trusted_devices({})
    
    app.logger.info(f"密码已更新 - 操作IP: {request.remote_addr}")
    
    return jsonify({'status': 'success', 'message': '密码设置成功'})

@app.route('/auth/trusted', methods=['GET'])
def auth_list_trusted():
    """查看受信任设备列表（仅本机可操作）"""
    if not is_local_request():
        return abort(403)
    
    trusted_devices = load_trusted_devices()
    
    # 格式化设备信息，隐藏完整ID
    formatted_devices = []
    for device_id, info in trusted_devices.items():
        formatted_devices.append({
            'device_id': device_id[:8] + '...',
            'created_at': info.get('created_at', ''),
            'last_seen': info.get('last_seen', ''),
            'ip': info.get('ip', ''),
            'ua_hash': info.get('ua_hash', '')[:8] + '...'
        })
    
    return jsonify({
        'status': 'success',
        'devices': formatted_devices,
        'total': len(formatted_devices)
    })

@app.route('/auth/revoke', methods=['POST'])
def auth_revoke_device():
    """吊销指定设备（仅本机可操作）"""
    if not is_local_request():
        return abort(403)
    
    data = request.json
    if not data or 'device_id' not in data:
        return jsonify({'status': 'error', 'message': '请提供设备ID'}), 400
    
    device_id_prefix = data['device_id']
    
    trusted_devices = load_trusted_devices()
    revoked_count = 0
    
    # 查找匹配的设备ID
    for device_id in list(trusted_devices.keys()):
        if device_id.startswith(device_id_prefix.replace('...', '')):
            del trusted_devices[device_id]
            revoked_count += 1
    
    save_trusted_devices(trusted_devices)
    
    app.logger.info(f"吊销设备 - 操作IP: {request.remote_addr}, 吊销数量: {revoked_count}")
    
    return jsonify({
        'status': 'success',
        'message': f'已吊销 {revoked_count} 个设备',
        'revoked_count': revoked_count
    })

@app.route('/auth/revoke_all', methods=['POST'])
def auth_revoke_all():
    """吊销所有设备（仅本机可操作）"""
    if not is_local_request():
        return abort(403)
    
    trusted_devices = load_trusted_devices()
    revoked_count = len(trusted_devices)
    
    save_trusted_devices({})
    
    app.logger.info(f"吊销所有设备 - 操作IP: {request.remote_addr}, 吊销数量: {revoked_count}")
    
    return jsonify({
        'status': 'success',
        'message': f'已吊销所有 {revoked_count} 个设备',
        'revoked_count': revoked_count
    })

# 读取端口状态
def get_port_status():
    if PORT_STATUS_FILE.exists():
        try:
            with open(PORT_STATUS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {'mode': 'normal', 'port': 5000}

# 写入端口状态
def set_port_status(mode, port):
    with open(PORT_STATUS_FILE, 'w', encoding='utf-8') as f:
        json.dump({'mode': mode, 'port': port}, f)

@app.route('/get_port_status')
def api_get_port_status():
    return jsonify(get_port_status())

@app.route('/get_security_status')
def api_get_security_status():
    return jsonify(get_security_config())

@app.route('/toggle_security', methods=['POST'])
def api_toggle_security():
    if not is_local_request():
        return abort(403)
    
    try:
        security_config = get_security_config()
        # 切换安全状态
        old_status = security_config.get('security_enabled', True)
        new_status = not old_status
        security_config['security_enabled'] = new_status
        save_security_config(security_config)
        
        # 记录安全状态变更
        app.logger.info(f"安全保护状态变更 - 操作IP: {request.remote_addr}, 旧状态: {'开启' if old_status else '关闭'}, 新状态: {'开启' if new_status else '关闭'}")
        
        return jsonify({
            'status': 'success',
            'security_enabled': security_config['security_enabled']
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})

@app.route('/switch_port', methods=['POST'])
def api_switch_port():
    if not is_local_request():
        return abort(403)
    locked_response = require_unlocked_device('切换随机端口')
    if locked_response:
        return locked_response
    # 生成随机端口（1025-65535，避开常用端口）
    import random
    for _ in range(10):
        port = random.randint(1025, 65535)
        if not is_port_in_use(port):
            print(f'[端口切换] 选择随机端口: {port}')
            set_port_status('random', port)
            print(f'[端口切换] 已写入 port_status.json: mode=random, port={port}')
            # 记录端口切换审计日志
            app.logger.info(f"端口切换 - 操作IP: {request.remote_addr}, 切换到随机端口: {port}")
            # 重启到新端口
            import sys, os
            import webbrowser
            webbrowser.open(f'http://127.0.0.1:{port}')
            os.execv(sys.executable, [sys.executable, __file__, str(port)])
            return jsonify({'status': 'success', 'port': port})
    return jsonify({'status': 'fail', 'message': '无法找到可用端口'}), 500

@app.route('/restore_port', methods=['POST'])
def api_restore_port():
    if not is_local_request():
        return abort(403)
    locked_response = require_unlocked_device('恢复端口')
    if locked_response:
        return locked_response
    set_port_status('normal', 5000)
    print(f'[端口恢复] 已写入 port_status.json: mode=normal, port=5000')
    # 记录端口恢复审计日志
    app.logger.info(f"端口恢复 - 操作IP: {request.remote_addr}, 恢复到默认端口: 5000")
    import sys, os
    import webbrowser
    webbrowser.open('http://127.0.0.1:5000')
    os.execv(sys.executable, [sys.executable, __file__, '5000'])
    return jsonify({'status': 'success'})

def restart_on_port(port):
    import time
    time.sleep(1)  # 给前端响应时间
    python = sys.executable
    os.execv(python, [python, __file__, str(port)])

@app.route('/get_my_ip')
def get_my_ip():
    return jsonify({'remote_addr': request.remote_addr})

# ===== AMLL 实时流 API =====
@app.route('/amll/state')
def amll_state_api():
    """AMLL 状态快照 API"""
    host = request.host
    return jsonify({
        "song": _normalize_song_for_host(AMLL_STATE["song"], host),
        "progress_ms": AMLL_STATE["progress_ms"],
        "lines": AMLL_STATE["lines"]
    })

@app.route('/amll/stream')
def amll_stream_api():
    """AMLL 实时事件流 API (Server-Sent Events)"""

    def _normalize_song_for_client(song_val: dict) -> dict:
        return _normalize_song_for_host(song_val or {}, request.host)

    def _normalize_state_snapshot(snapshot: dict) -> dict:
        return {
            "song": _normalize_song_for_client(snapshot.get("song", {})),
            "progress_ms": snapshot.get("progress_ms", 0),
            "lines": snapshot.get("lines", [])
        }

    @stream_with_context
    def _gen():
        # 先发一份完整快照（让新打开的前端立刻有内容）
        yield _sse("state", _normalize_state_snapshot(AMLL_STATE))
        # 然后持续推送增量
        while True:
            try:
                evt = AMLL_QUEUE.get(timeout=15)
                etype = evt.get("type")
                data = evt.get("data", {})
                if etype == "state":
                    payload = _normalize_state_snapshot(data)
                elif etype == "song":
                    payload = {"song": _normalize_song_for_client(data.get("song", {}))}
                else:
                    payload = data
                yield _sse(etype, payload)
            except queue.Empty:
                # 心跳：防止 Nginx/浏览器断流
                yield ": keep-alive\n\n"
    return Response(_gen(), mimetype="text/event-stream")

@app.route('/lyrics-amll')
def lyrics_amll_page():
    """AMLL 歌词展示页面"""
    return render_template("Lyrics-style.HTML-AMLL-v1.HTML")

@app.route('/amll/create_song', methods=['POST'])
def amll_create_song():
    """基于当前 AMLL 流生成歌词/封面并创建歌曲 JSON。"""
    if not is_request_allowed():
        return abort(403)
    locked_response = require_unlocked_device('从AMLL创建歌曲')
    if locked_response:
        return locked_response

    payload = request.get_json(silent=True) or {}
    use_translation = bool(payload.get("useTranslation", True))
    snapshot_song = AMLL_STATE.get("song", {}) or {}
    lines = AMLL_STATE.get("lines", []) or []

    if not lines:
        return jsonify({'status': 'error', 'message': 'AMLL源暂无歌词数据，无法创建。'})

    title = (snapshot_song.get("musicName") or "AMLL 未命名").strip()
    artists = snapshot_song.get("artists") or []
    album = snapshot_song.get("album", "")
    duration_ms = int(snapshot_song.get("duration") or 0)

    # 生成基础文件名
    artists_part = " _ ".join([sanitize_filename(a) for a in artists if sanitize_filename(a)]) or "AMLL"
    base_stem_raw = sanitize_filename(f"{title} - {artists_part}") or "AMLL_Song"
    json_filename = _ensure_unique_filename(STATIC_DIR, f"{base_stem_raw}.json")
    base_stem = os.path.splitext(json_filename)[0]

    try:
        # 写入 LYS
        lys_filename = _ensure_unique_filename(SONGS_DIR, f"{base_stem}.lys")
        lys_path = SONGS_DIR / lys_filename
        lys_path.write_text(_amll_lines_to_lys(lines), encoding="utf-8-sig")

        # 写入翻译 LRC（可选）
        translation_filename = None
        translation_url = "!"
        if use_translation and any(str(line.get("translatedLyric") or "").strip() for line in lines):
            translation_filename = _ensure_unique_filename(SONGS_DIR, f"{base_stem}_trans.lrc")
            translation_path = SONGS_DIR / translation_filename
            translation_path.write_text(_amll_lines_to_lrc(lines), encoding="utf-8-sig")
            translation_url = build_public_url('songs', translation_filename)

        # 处理封面（优先 data:URL）
        cover_source = snapshot_song.get("cover_data_url") or snapshot_song.get("cover") or ""
        cover_url = ""
        cover_filename = None
        data_bytes, data_ext = _decode_data_url(cover_source)
        if data_bytes:
            cover_ext = data_ext or ".jpg"
            cover_filename = _ensure_unique_filename(SONGS_DIR, f"{base_stem}{cover_ext}")
            (SONGS_DIR / cover_filename).write_bytes(data_bytes)
            cover_url = build_public_url('songs', cover_filename)
        elif cover_source:
            cover_url = cover_source

        lyrics_url = build_public_url('songs', lys_filename)
        lyrics_field = f"::{lyrics_url}::{translation_url}::!::"

        placeholder_song = build_public_url('songs', "音乐.mp3")
        json_content = {
            "serial": 123456,
            "meta": {
                "title": title,
                "artists": artists,
                "albumImgSrc": cover_url or '!',
                "duration_ms": duration_ms,
                "lyrics": lyrics_field
            },
            # 默认填占位空音乐，避免播放出错
            "song": placeholder_song
        }
        if album:
            json_content["meta"]["album"] = album

        json_path = STATIC_DIR / json_filename
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(json_content, f, ensure_ascii=False, indent=2)

        return jsonify({
            'status': 'success',
            'jsonFile': json_filename,
            'lyricsFile': lys_filename,
            'translationFile': translation_filename,
            'coverFile': cover_filename,
            'coverUrl': cover_url,
            'message': '已从 AMLL 源创建新歌曲'
        })
    except Exception as exc:
        app.logger.error(f"AMLL 创建歌曲失败: {exc}", exc_info=True)
        return jsonify({'status': 'error', 'message': f'创建失败: {exc}'})

# ===== AMLL CSV 导出功能 =====
def norm_type(t):
    if not isinstance(t, str):
        return ""
    return t.replace("_", "").replace("-", "").lower()

def ms_to_ts(ms):
    ms = int(ms or 0)
    s, msec = divmod(ms, 1000)
    m, s = divmod(s, 60)
    return f"{m:02d}:{s:02d}.{msec:03d}"

def looks_cjk_or_kana(s):
    # 粗略判断：包含中日韩统一表意文字、假名、全角等，就认为需要粘连
    for ch in s:
        code = ord(ch)
        if (
            0x3040 <= code <= 0x30FF or   # ひらがな/カタカナ
            0x4E00 <= code <= 0x9FFF or   # CJK统一表意
            0x3400 <= code <= 0x4DBF or   # CJK扩展A
            0xFF01 <= code <= 0xFF60 or   # 半角/全角标点
            0x3000 <= code <= 0x303F      # CJK标点
        ):
            return True
    return False

def join_line_text(words):
    # CJK/假名行用"连写"，否则英文等用空格分词
    text = "".join(w.get("word", "") for w in words)
    return text if looks_cjk_or_kana(text) else " ".join(w.get("word", "") for w in words)

def split_word_to_chars(word_obj):
    """将一个 word 拆成逐字事件：
       - 若本身是单字，直接返回
       - 若多字，则按字符等分时间片；roman_word 尝试按空格/连字符对齐
    """
    w = str(word_obj.get("word", ""))
    rw = str(word_obj.get("romanWord", "") or "")
    s = int(word_obj.get("startTime") or 0)
    e = int(word_obj.get("endTime") or s)
    n = max(1, len(w))

    # 先准备罗马逐字列表（尽力匹配）
    if rw:
        parts = rw.replace("  ", " ").strip().split(" ")
        if len(parts) != n:
            parts = rw.split("-")
        if len(parts) != n:
            parts = list(rw) if len(rw) == n else [""] * n
        roman_candidates = parts
    else:
        roman_candidates = [""] * n

    if n == 1:
        return [{
            "char": w,
            "roman_char": roman_candidates[0] if roman_candidates else "",
            "start_ms": s,
            "end_ms": e
        }]

    # 多字：等分时间（最后一段吃余数，保证端点对齐）
    dur = max(0, e - s)
    if dur <= 0:
        # 没有有效时长，全部用同一瞬时戳
        return [{
            "char": ch,
            "roman_char": roman_candidates[i] if i < len(roman_candidates) else "",
            "start_ms": s, "end_ms": s
        } for i, ch in enumerate(w)]

    base = dur // n
    rem = dur % n
    out = []
    cur = s
    for i, ch in enumerate(w):
        seg = base + (1 if i < rem else 0)
        out.append({
            "char": ch,
            "roman_char": roman_candidates[i] if i < len(roman_candidates) else "",
            "start_ms": cur,
            "end_ms": cur + seg
        })
        cur += seg
    # 防御：最后一个端点校正为 e
    if out:
        out[-1]["end_ms"] = e
    return out

def extract_lyrics_to_csv(lyrics_data):
    """将歌词数据导出为逐字CSV文件"""
    char_rows = []
    for i, line in enumerate(lyrics_data, start=1):
        words = line.get("words", [])
        is_bg = bool(line.get("isBG", False))
        is_duet = bool(line.get("isDuet", False))
        start_ms = int(line.get("startTime") or 0)
        end_ms = int(line.get("endTime") or 0)

        for j, wobj in enumerate(words, start=1):
            # 拆成逐字
            char_events = split_word_to_chars(wobj)
            for k, ev in enumerate(char_events, start=1):
                c = ev["char"]
                rc = ev["roman_char"]
                s = ev["start_ms"]
                e = ev["end_ms"]
                char_rows.append({
                    "line_index": i,
                    "word_index": j,
                    "char_index": k,
                    "char": c,
                    "roman_char": rc,
                    "start_ms": s,
                    "end_ms": e,
                    "start_ts": ms_to_ts(s),
                    "end_ts": ms_to_ts(e),
                    "is_bg": is_bg,
                    "is_duet": is_duet
                })

    # 写CSV到导出目录
    if char_rows:
        csv_name = f"lyrics_chars_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        csv_path = EXPORTS_DIR / csv_name
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "line_index", "word_index", "char_index",
                    "char", "roman_char",
                    "start_ms", "end_ms", "start_ts", "end_ts",
                    "is_bg", "is_duet"
                ]
            )
            writer.writeheader()
            writer.writerows(char_rows)

        print(f"逐字时间轴已导出：{csv_path}（共 {len(char_rows)} 个字符事件）")
        return csv_path
    return None

# ======= 新增 AMLL 实时推送功能函数 =======
def _ms_to_sec(ms: int) -> float:
    """毫秒转秒，保留3位小数"""
    return round((ms or 0) / 1000.0, 3)

def _coerce_ms(value) -> int:
    """宽松地把各种数值/字符串转换为毫秒整数。"""
    try:
        return int(round(float(value)))
    except Exception:
        return 0

def _merge_song_state(current: dict, incoming: dict) -> dict:
    """轻量合并歌曲快照，保留已有有效字段。"""
    merged = (current or {}).copy()
    for key, value in (incoming or {}).items():
        if key == "artists":
            if isinstance(value, list) and value:
                merged[key] = value
            continue
        if value in (None, "", [], {}):
            continue
        merged[key] = value
    return merged

def _guess_ext_from_mime(mime: str) -> str:
    mime = (mime or "").lower()
    if "png" in mime:
        return ".png"
    if "webp" in mime:
        return ".webp"
    if "gif" in mime:
        return ".gif"
    return ".jpg"

def _build_data_url(raw_base64: str, mime: Optional[str] = None) -> str:
    mime_type = mime or "image/jpeg"
    return f"data:{mime_type};base64,{raw_base64}"

def _decode_data_url(data_url: str) -> tuple[Optional[bytes], Optional[str]]:
    """解码 data:URL，返回 (bytes, 扩展名)。失败返回 (None, None)。"""
    if not data_url or not isinstance(data_url, str) or not data_url.startswith("data:"):
        return None, None
    try:
        header, payload = data_url.split(",", 1)
        mime = header.split(";")[0].split(":", 1)[-1]
        return base64.b64decode(payload), _guess_ext_from_mime(mime)
    except Exception:
        return None, None

def _extract_song_payload_from_state(state_val: dict) -> dict:
    """兼容新版 AMLL state 消息里的歌曲元数据载荷。"""
    if not isinstance(state_val, dict):
        return {}
    candidates = [state_val]  # 顶层也可能直接携带字段
    for key in (
        "value", "data", "song", "music", "meta", "metadata", "info",
        "payload", "musicInfo", "songInfo", "track", "trackInfo"
    ):
        cand = state_val.get(key)
        if isinstance(cand, dict):
            candidates.append(cand)
    # 展开一层嵌套，处理 {value:{meta:{...}}} 之类的结构
    for cand in list(candidates):
        if not isinstance(cand, dict):
            continue
        for nested_key in ("song", "music", "meta", "metadata", "info", "payload"):
            nested = cand.get(nested_key)
            if isinstance(nested, dict):
                candidates.append(nested)
    important_keys = (
        "musicName", "music", "title", "name", "songName", "trackName",
        "artist", "artistName", "artists", "singer", "singers", "performer",
        "artist_name", "artist_names", "singer_name", "singer_names",
        "album", "albumName", "albumTitle", "album_name",
        "duration", "durationMs", "duration_ms", "durationMS", "durationSeconds", "durationSec", "length", "lengthMs", "length_ms", "duration_ms",
        "cover", "coverUrl", "coverURL", "albumImgSrc"
    )
    for cand in candidates:
        if any(k in cand for k in important_keys):
            return cand
    return candidates[0] if candidates else {}

def _extract_cover_payload_from_state(state_val: dict) -> dict:
    """提取 state 消息里的封面载荷，兼容 url/data 两种写法。"""
    if not isinstance(state_val, dict):
        return {}
    for candidate in (state_val.get("value"), state_val.get("data"), state_val.get("cover"), state_val.get("image"), state_val.get("payload")):
        if isinstance(candidate, dict) and candidate:
            return candidate
        if isinstance(candidate, str) and candidate.strip():
            return {"url": candidate}
        if isinstance(candidate, (bytes, bytearray, list)):
            return {"data": candidate, "mime": state_val.get("mime") or state_val.get("contentType")}
    return {}

def _extract_lines_from_state_update(state_val: dict) -> list[dict]:
    """尽量从 state 消息里抓取歌词行，支持多层嵌套字段。"""
    if not isinstance(state_val, dict):
        return []

    def dig_lines(candidate):
        if isinstance(candidate, list):
            return candidate
        if isinstance(candidate, dict):
            for key in ("lines", "data", "lyrics", "lyric"):
                nested = dig_lines(candidate.get(key))
                if nested is not None:
                    return nested
        return None

    for cand in (
        state_val.get("value"),
        state_val.get("data"),
        state_val.get("lyrics"),
        state_val.get("lyric"),
        state_val.get("lines"),
        state_val.get("payload")
    ):
        lines = dig_lines(cand)
        if lines is not None:
            return lines
    return []

def _extract_progress_ms_from_state(state_val: dict) -> Optional[int]:
    """解析 state 消息中的播放进度（毫秒）。"""
    if not isinstance(state_val, dict):
        return None

    def dig_progress(candidate):
        if candidate is None:
            return None
        if isinstance(candidate, bool):
            return None
        if isinstance(candidate, (int, float)):
            return int(candidate)
        if isinstance(candidate, str):
            try:
                return int(float(candidate))
            except Exception:
                return None
        if isinstance(candidate, dict):
            for key in ("progress_ms", "progressMs", "progress", "position", "positionMs", "position_ms", "time_ms", "timeMs", "time", "ms"):
                found = dig_progress(candidate.get(key))
                if found is not None:
                    return found
        return None

    for cand in (state_val.get("value"), state_val.get("data"), state_val.get("payload"), state_val):
        found = dig_progress(cand)
        if found is not None:
            return found
    return None

def _extract_cover_from_info(info: dict) -> str:
    """从歌曲元信息中提取封面地址。"""
    if not isinstance(info, dict):
        return ""
    for key in (
        "albumImgSrc", "cover", "coverUrl", "coverURL",
        "artworkUrl", "artworkUrl100", "artwork", "artworkURL",
        "picUrl", "image", "img", "albumArt", "albumArtUrl", "albumCover", "coverUri", "coverURI",
        "artUri", "artworkUri", "imageUrl", "imageURL", "artUri100"
    ):
        candidate = info.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return ""

def _normalize_song_info(info: dict) -> dict:
    """提取并规范化 AMLL 传来的歌曲信息。"""
    music_name = str(
        info.get("musicName")
        or info.get("music")
        or info.get("title")
        or info.get("name")
        or info.get("songName")
        or info.get("trackName")
        or info.get("song")
        or info.get("musicTitle")
        or info.get("songTitle")
        or info.get("trackTitle")
        or info.get("titleName")
        or info.get("musicTitleName")
        or info.get("music_name")
        or info.get("song_title")
        or info.get("track_title")
        or info.get("music_name")
        or info.get("song_name")
        or ""
    ).strip()
    artists_raw = (
        info.get("artists")
        or info.get("artist")
        or info.get("artistName")
        or info.get("singer")
        or info.get("singers")
        or info.get("performer")
        or info.get("artistNames")
        or info.get("artist_names")
        or info.get("singerName")
        or info.get("singerNames")
        or info.get("singer_name")
        or info.get("singer_names")
        or []
    )
    artists: list[str] = []
    if isinstance(artists_raw, list):
        for a in artists_raw:
            if isinstance(a, dict):
                name_candidate = a.get("name") or a.get("artistName") or a.get("singerName") or a.get("singer")
                if name_candidate and str(name_candidate).strip():
                    artists.append(str(name_candidate).strip())
            elif str(a).strip():
                artists.append(str(a).strip())
    else:
        if isinstance(artists_raw, dict):
            name_candidate = artists_raw.get("name") or artists_raw.get("artistName") or artists_raw.get("singerName") or artists_raw.get("singer")
            if name_candidate and str(name_candidate).strip():
                artists.append(str(name_candidate).strip())
        elif str(artists_raw).strip():
            artists.append(str(artists_raw).strip())

    album_name = str(
        info.get("album")
        or info.get("albumName")
        or info.get("albumTitle")
        or info.get("record")
        or info.get("collection")
        or info.get("disc")
        or info.get("discName")
        or info.get("discTitle")
        or info.get("album_name")
        or info.get("album_title")
        or ""
    ).strip()
    cover_url = _extract_cover_from_info(info) or info.get("url") or info.get("coverUri") or info.get("coverURI") or ""
    cover_data_url = (
        info.get("coverDataUrl")
        or info.get("cover_data_url")
        or info.get("coverDataURL")
        or info.get("coverDataURI")
        or info.get("coverData")
        or info.get("cover_data")
    ) or ""
    if not cover_data_url:
        raw_cover_data = (
            info.get("coverData")
            or info.get("coverBase64")
            or info.get("albumImgData")
            or info.get("imageData")
            or info.get("artworkData")
        )
        if isinstance(raw_cover_data, str):
            cover_data_url = _build_data_url(raw_cover_data, info.get("coverMime") or info.get("mime") or info.get("contentType"))
        elif isinstance(raw_cover_data, (bytes, bytearray)):
            cover_data_url = _build_data_url(base64.b64encode(raw_cover_data).decode("utf-8"), info.get("coverMime") or info.get("mime") or info.get("contentType"))
    try:
        duration = int(float(
            info.get("duration")
            or info.get("durationMs")
            or info.get("duration_ms")
            or info.get("durationMS")
            or info.get("durationSeconds")
            or info.get("durationSec")
            or info.get("length")
            or info.get("lengthMs")
            or info.get("length_ms")
            or info.get("duration_ms")
            or info.get("songDuration")
            or info.get("songDurationMs")
            or info.get("musicDuration")
            or 0
        ))
    except Exception:
        duration = 0
    song = {
        "musicName": music_name,
        "artists": artists,
        "duration": duration,
    }
    if album_name:
        song["album"] = album_name
    if cover_url:
        song["cover"] = cover_url
        # 帮前端兜底相同字段
        song["albumImgSrc"] = cover_url
    if cover_data_url:
        song["cover_data_url"] = cover_data_url
    return song


def _normalize_media_url_for_host(raw_value: Optional[str], host: str) -> str:
    """根据请求 host 规范化媒体 URL，修正 127/localhost 和 /static 路径。"""
    if not raw_value:
        return ""

    cleaned = str(raw_value).strip()
    if not cleaned or cleaned == "!":
        return ""

    if cleaned.startswith(("data:", "blob:")):
        return cleaned

    cleaned = cleaned.replace("\\", "/").replace("/static/songs/", "/songs/").replace("static/songs/", "songs/")
    try:
        parsed = urlparse(cleaned)
    except Exception:
        parsed = None

    if parsed and parsed.scheme:
        netloc = parsed.netloc
        if parsed.hostname in {"127.0.0.1", "localhost", "0.0.0.0"} and host:
            netloc = host
        path = (parsed.path or "").replace("/static/songs/", "/songs/")
        rebuilt = parsed._replace(netloc=netloc, path=path)
        return rebuilt.geturl()

    if cleaned.startswith("/"):
        return cleaned
    if cleaned.startswith("songs/"):
        return "/" + cleaned
    return "/songs/" + cleaned.lstrip("/")


def _normalize_song_for_host(song: dict, host: str) -> dict:
    """为前端请求方规范化歌曲信息中的封面/背景路径。"""
    if not isinstance(song, dict):
        return {}
    normalized = song.copy()
    meta_src = song.get("meta") if isinstance(song.get("meta"), dict) else {}
    meta = meta_src.copy()

    def apply(target: dict, key: str):
        if key not in target:
            return
        normed = _normalize_media_url_for_host(target.get(key), host)
        if normed:
            target[key] = normed

    apply(meta, "Background-image")

    for key in ("albumImgSrc", "cover", "coverUrl", "cover_file_url"):
        apply(meta, key)
        apply(normalized, key)

    normalized["meta"] = meta
    return normalized


def _amll_publish(evt_type: str, data: dict):
    """发布事件到AMLL前端"""
    # 更新全局快照
    if evt_type == "lyrics":
        AMLL_STATE["lines"] = data.get("lines", [])
    elif evt_type == "progress":
        AMLL_STATE["progress_ms"] = int(data.get("progress_ms", 0))
    elif evt_type == "song":
        AMLL_STATE["song"] = _merge_song_state(AMLL_STATE.get("song", {}), data.get("song", {}))
    AMLL_STATE["last_update"] = time.time()

    # 推送到队列
    try:
        AMLL_QUEUE.put_nowait({"type": evt_type, "data": data})
    except queue.Full:
        # 队列满时静默丢弃，避免日志刷屏
        pass

def _sse(event: str, data: dict) -> str:
    """SSE 格式：event:<name>\ndata:<json>\n\n"""
    return f"event:{event}\ndata:{json.dumps(data, ensure_ascii=False)}\n\n"

def _amll_lines_to_front(payload_lines: list[dict]) -> list[dict]:
    """
    把 AMLL 的 lines（每行包含 words[]）转换为前端统一的结构：
      每行 -> { syllables: [ {text,startTime,duration,roman?}, ... ] }
    """
    out_lines = []
    for line in payload_lines:
        words = line.get("words", [])
        syllables = []
        word_segments = []
        for wobj in words:
            word_text = str(wobj.get("word", "") or "")
            start_ms = _coerce_ms(
                wobj.get("start_ms")
                or wobj.get("startMs")
                or wobj.get("startTime")
            )
            end_ms = _coerce_ms(
                wobj.get("end_ms")
                or wobj.get("endMs")
                or wobj.get("endTime")
            )
            duration_ms = _coerce_ms(
                wobj.get("duration_ms")
                or wobj.get("durationMs")
                or wobj.get("duration")
            )
            if duration_ms <= 0 and end_ms:
                duration_ms = max(0, end_ms - start_ms)
            if not end_ms and duration_ms:
                end_ms = start_ms + duration_ms

            if word_text:
                word_segments.append({
                    "text": word_text,
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "duration_ms": duration_ms,
                    "roman": str(wobj.get("romanWord") or wobj.get("roman_word") or "")
                })

            # 拆成逐字
            for ev in split_word_to_chars(wobj):
                syllables.append({
                    "text": ev["char"],
                    "startTime": _ms_to_sec(ev["start_ms"]),
                    "duration": max(0.0, _ms_to_sec(ev["end_ms"] - ev["start_ms"])),
                    "roman": ev["roman_char"]
                })

        # 保持原样，但加入前端需要的额外字段
        out_lines.append({
            "syllables": syllables,
            "words": word_segments,
            "isBG": bool(line.get("isBG")),
            "isDuet": bool(line.get("isDuet")),
            "translatedLyric": line.get("translatedLyric", "") or ""
        })
    return out_lines

def _amll_lines_to_lys(lines: list[dict]) -> str:
    """将 AMLL lines 转为 .lys 格式文本。"""
    buf = ["[from: AMLL]", "[offset:0]"]
    for line in lines:
        marker = "6" if line.get("isBG") else "1"
        parts = []
        word_segments = line.get("words") or []
        if word_segments:
            for word in word_segments:
                text = str(word.get("text") or word.get("word") or "")
                if text == "":
                    continue
                start_ms = _coerce_ms(
                    word.get("start_ms")
                    or word.get("startMs")
                    or word.get("startTime")
                )
                end_ms = _coerce_ms(
                    word.get("end_ms")
                    or word.get("endMs")
                    or word.get("endTime")
                )
                duration_ms = _coerce_ms(
                    word.get("duration_ms")
                    or word.get("durationMs")
                    or word.get("duration")
                )
                if duration_ms <= 0 and end_ms:
                    duration_ms = max(0, end_ms - start_ms)
                if not end_ms and duration_ms:
                    end_ms = start_ms + duration_ms
                parts.append(f"{text}({start_ms},{duration_ms})")
        else:
            syllables = line.get("syllables") or []
            if not syllables:
                continue
            for syl in syllables:
                text = str(syl.get("text", "") or "")
                if text == "":
                    continue
                start_ms = int(round(float(syl.get("startTime", 0)) * 1000))
                duration_ms = int(round(float(syl.get("duration", 0)) * 1000))
                parts.append(f"{text}({start_ms},{duration_ms})")
        if parts:
            buf.append(f"[{marker}]{''.join(parts)}")
    return "\n".join(buf)

def _amll_lines_to_lrc(lines: list[dict]) -> str:
    """把 translatedLyric 转成简单 LRC。"""
    def _format_tag(ms: int) -> str:
        minutes, remainder = divmod(int(ms), 60000)
        seconds = remainder / 1000
        return f"[{minutes:02d}:{seconds:05.2f}]"

    rows = ["[by: AMLL]"]
    for line in lines:
        text = str(line.get("translatedLyric") or "").strip()
        if not text:
            continue
        syllables = line.get("syllables") or []
        start_ms = 0
        if syllables:
            start_ms = int(round(float(syllables[0].get("startTime", 0)) * 1000))
        rows.append(f"{_format_tag(start_ms)}{text}")
    return "\n".join(rows)

def _ensure_unique_filename(base_dir: Path, filename: str) -> str:
    """在目录下确保文件名唯一，自动追加序号。"""
    candidate = filename
    base, ext = os.path.splitext(filename)
    counter = 1
    while (base_dir / candidate).exists():
        candidate = f"{base}_{counter}{ext}"
        counter += 1
    return candidate

def _store_cover_bytes(payload: bytes, mime: Optional[str] = None) -> tuple[str, str]:
    """保存封面到 songs 目录，返回 (public_url, filename)。"""
    if not payload:
        return "", ""
    mime_type = (mime or "").lower()
    ext = _guess_ext_from_mime(mime_type)
    fname = _ensure_unique_filename(SONGS_DIR, f"amll_cover_{int(time.time())}{ext}")
    (SONGS_DIR / fname).write_bytes(payload)
    return build_public_url('songs', fname), fname

def _detect_image_mime(data: bytes) -> Optional[str]:
    if not data or len(data) < 4:
        return None
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xFF\xD8\xFF"):
        return "image/jpeg"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None

def _try_parse_amll_binary_frame(buf: bytes) -> tuple[Optional[str], Optional[bytes]]:
    """
    尝试解析 AMLL V2 二进制帧：
      magic u16 (little-endian)
      size  u32
      data  [size]
    magic=0 -> OnAudioData（忽略）
    magic=1 -> SetCoverData（返回封面原始字节）
    """
    if not buf or len(buf) < 6:
        return None, None
    try:
        magic, size = struct.unpack("<HI", buf[:6])
    except struct.error:
        return None, None
    if size > len(buf) - 6 or size < 0:
        return None, None
    payload = buf[6:6 + size]
    if magic == 1:
        return "cover", payload
    # 非 0/1 魔数：仍返回 payload 给兜底图片解析
    return None, payload

def is_port_in_use(port):
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        result = s.connect_ex(('127.0.0.1', port)) == 0
        print(f'[端口检测] 端口 {port} 是否被占用: {result}')
        return result

# === WebSocket 服务（AMLL 对接：ws://localhost:11444）===
WS_HOST = ""          # 监听所有地址（同时覆盖 IPv4 / IPv6）
WS_PORT = 11444

async def ws_handle(ws):
    peer = getattr(ws, "remote_address", None)
    print(f"[WS] 客户端连接: {peer}")
    try:
        async for raw in ws:
            # 二进制帧：可能是 TTML
            if isinstance(raw, (bytes, bytearray)):
                b = bytes(raw)
                kind, payload = _try_parse_amll_binary_frame(b)
                def _handle_cover(candidate: bytes, label: str) -> bool:
                    if not candidate:
                        return False
                    mime_guess = _detect_image_mime(candidate)
                    if not mime_guess:
                        return False
                    b64 = base64.b64encode(candidate).decode("ascii")
                    data_url = _build_data_url(b64, mime_guess)
                    file_url, _ = _store_cover_bytes(candidate, mime_guess)
                    print(f"[WS] {label}作为封面处理（{len(candidate)} bytes，mime={mime_guess}，file={file_url}）")
                    song_patch = {
                        "cover_data_url": data_url,
                        "cover": data_url,
                        "albumImgSrc": data_url,
                    }
                    if file_url:
                        song_patch["cover_file_url"] = file_url
                        song_patch["cover"] = file_url
                        song_patch["albumImgSrc"] = file_url
                    _amll_publish("song", {"song": song_patch})
                    return True

                if _handle_cover(payload, "按payload "):
                    continue
                if _handle_cover(b, "按整帧 "):
                    continue
                try:
                    magic_val, _ = struct.unpack("<HI", b[:6])
                except Exception:
                    magic_val = None
                if magic_val == 4 and payload:
                    if _handle_cover(payload, "按magic=4 payload "):
                        continue
                if kind == "cover" and payload:
                    continue  # 已处理

                try:
                    txt = b.decode("utf-8")
                except UnicodeDecodeError:
                    txt = None
                if txt and txt.lstrip().startswith("<"):
                    name = f"lyrics_ttml_{datetime.now().strftime('%Y%m%d_%H%M%S')}.ttml"
                    (EXPORTS_DIR / name).write_text(txt, encoding="utf-8")
                    print(f"[WS] 收到二进制 TTML，已保存 exports/{name}")
                else:
                    name = f"lyrics_binary_{datetime.now().strftime('%Y%m%d_%H%M%S')}.bin"
                    (EXPORTS_DIR / name).write_bytes(b)
                    print(f"[WS] 收到二进制帧（{len(b)} bytes），已保存 exports/{name}")
                continue

            # 文本帧：优先按 JSON 解析
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                app.logger.info(f"[WS] 收到原始 TTML 文本: {raw[:200]}...")
                continue

            mtype = norm_type(msg.get("type"))
            if mtype == "ping":
                await ws.send(json.dumps({"type": "pong"})); continue
            if mtype in ("initializev2","initialize","init"):
                print("[WS] 初始化"); await ws.send(json.dumps({"type": "connected"})); continue
            if mtype == "setmusicinfo":
                info = msg.get("value") or {}
                song = _normalize_song_info(info)
                print("[WS] 歌曲元数据：", song)
                _amll_publish("song", {"song": song})
                continue
            if mtype in ("setmusicalbumcoverimagedata", "setalbumcover", "setcover"):
                payload = msg.get("value") or {}
                cover_url = ""
                cover_data_url = ""
                cover_file_url = ""
                if isinstance(payload, str):
                    if payload.startswith("data:"):
                        cover_data_url = payload
                    else:
                        cover_url = payload
                elif isinstance(payload, dict):
                    cover_data_url = payload.get("dataUrl") or payload.get("dataURL") or ""
                    cover_url = payload.get("url") or payload.get("cover") or payload.get("coverUrl") or ""
                    if not cover_data_url:
                        raw_data = payload.get("imageData") or payload.get("data") or payload.get("buffer") or payload.get("blob")
                        if isinstance(raw_data, str):
                            cover_data_url = _build_data_url(raw_data, payload.get("mime") or payload.get("contentType"))
                        elif isinstance(raw_data, (bytes, bytearray)):
                            b64 = base64.b64encode(raw_data).decode("utf-8")
                            cover_data_url = _build_data_url(b64, payload.get("mime") or payload.get("contentType"))
                        elif isinstance(raw_data, list):
                            try:
                                b = bytes(raw_data)
                                b64 = base64.b64encode(b).decode("utf-8")
                                cover_data_url = _build_data_url(b64, payload.get("mime") or payload.get("contentType"))
                            except Exception:
                                pass
                        # 保存为文件，便于前端用 http 访问
                        if isinstance(raw_data, (bytes, bytearray)):
                            cover_file_url, _ = _store_cover_bytes(raw_data, payload.get("mime") or payload.get("contentType"))
                        elif isinstance(raw_data, list):
                            try:
                                b = bytes(raw_data)
                                cover_file_url, _ = _store_cover_bytes(b, payload.get("mime") or payload.get("contentType"))
                            except Exception:
                                pass
                song_patch = {}
                if cover_url:
                    song_patch["cover"] = cover_url
                    song_patch["albumImgSrc"] = cover_url
                if cover_data_url:
                    song_patch["cover_data_url"] = cover_data_url
                if cover_file_url:
                    song_patch["cover_file_url"] = cover_file_url
                    song_patch["cover"] = cover_file_url
                    song_patch["albumImgSrc"] = cover_file_url
                if song_patch:
                    _amll_publish("song", {"song": song_patch})
                continue
            if mtype == "state":
                val = msg.get("value") or {}
                if not isinstance(val, dict):
                    val = {"value": val}
                update = norm_type(val.get("update") or val.get("type"))
                content = val.get("value")
                if content is None:
                    content = val.get("data") or {}
                if update in ("setmusic", "music", "musicinfo", "song", "songinfo", "track", "trackinfo"):
                    song_payload = _extract_song_payload_from_state(val)
                    if not song_payload and isinstance(content, dict):
                        song_payload = content
                    song = _normalize_song_info(song_payload)
                    if not (song.get("musicName") or song.get("artists") or song.get("album") or song.get("cover") or song.get("cover_data_url")):
                        try:
                            preview_payload = json.dumps(song_payload, ensure_ascii=False) if song_payload else str(song_payload)
                        except Exception:
                            preview_payload = repr(song_payload)
                        try:
                            preview_content = json.dumps(content, ensure_ascii=False) if isinstance(content, (dict, list)) else str(content)
                        except Exception:
                            preview_content = repr(content)
                        try:
                            preview_full_msg = json.dumps(msg, ensure_ascii=False)
                        except Exception:
                            preview_full_msg = repr(msg)
                        print(f"[WS] setmusic 原始载荷调试 payload={preview_payload} content={preview_content} full={preview_full_msg}")
                    print("[WS] 歌曲元数据(state)：", song)
                    _amll_publish("song", {"song": song})
                    continue
                if update in ("setcover", "cover", "albumcover", "artwork"):
                    song_patch = {}
                    cover_payload = _extract_cover_payload_from_state(val)
                    if not cover_payload:
                        if isinstance(content, dict):
                            cover_payload = content
                        elif isinstance(content, str) and content.strip():
                            cover_payload = {"url": content}
                    source = cover_payload.get("source") if isinstance(cover_payload, dict) else None
                    if str(source).lower() == "uri":
                        url = cover_payload.get("url") or cover_payload.get("uri") or ""
                        if url:
                            song_patch["cover"] = url
                            song_patch["albumImgSrc"] = url
                    elif str(source).lower() == "data":
                        img = cover_payload.get("image") or {}
                        mime = img.get("mimeType") or "image/jpeg"
                        data_b64 = img.get("data")
                        if isinstance(data_b64, str):
                            song_patch["cover_data_url"] = _build_data_url(data_b64, mime)
                            song_patch["cover"] = song_patch["cover_data_url"]
                            song_patch["albumImgSrc"] = song_patch["cover_data_url"]
                            try:
                                payload = base64.b64decode(data_b64)
                                file_url, _ = _store_cover_bytes(payload, mime)
                                if file_url:
                                    song_patch["cover_file_url"] = file_url
                                    song_patch["cover"] = file_url
                                    song_patch["albumImgSrc"] = file_url
                            except Exception:
                                pass
                        elif isinstance(data_b64, list):
                            try:
                                b = bytes(data_b64)
                                b64 = base64.b64encode(b).decode("ascii")
                                song_patch["cover_data_url"] = _build_data_url(b64, mime)
                                song_patch["cover"] = song_patch["cover_data_url"]
                                song_patch["albumImgSrc"] = song_patch["cover_data_url"]
                                file_url, _ = _store_cover_bytes(b, mime)
                                if file_url:
                                    song_patch["cover_file_url"] = file_url
                                    song_patch["cover"] = file_url
                                    song_patch["albumImgSrc"] = file_url
                            except Exception:
                                pass
                    elif isinstance(cover_payload, dict):
                        url = cover_payload.get("url") or cover_payload.get("cover") or cover_payload.get("coverUrl")
                        if url:
                            song_patch["cover"] = url
                            song_patch["albumImgSrc"] = url
                        raw_data = cover_payload.get("data") or cover_payload.get("imageData") or cover_payload.get("buffer")
                        if isinstance(raw_data, (bytes, bytearray)):
                            b64 = base64.b64encode(raw_data).decode("ascii")
                            mime = cover_payload.get("mime") or cover_payload.get("contentType")
                            song_patch["cover_data_url"] = _build_data_url(b64, mime)
                            file_url, _ = _store_cover_bytes(raw_data, mime)
                            if file_url:
                                song_patch["cover_file_url"] = file_url
                                song_patch["cover"] = file_url
                                song_patch["albumImgSrc"] = file_url
                    if song_patch:
                        _amll_publish("song", {"song": song_patch})
                    continue
                if update in ("setlyric", "lyrics", "lyric"):
                    payload = _extract_lines_from_state_update(val)
                    if not payload:
                        if isinstance(content, dict) and "lines" in content:
                            payload = content.get("lines") or []
                        elif isinstance(content, list):
                            payload = content
                    print(f"[WS] 收到歌词(state) {len(payload)} 行（逐字导出）")
                    rows = []
                    for i, line in enumerate(payload, 1):
                        words = line.get("words", [])
                        line_text = join_line_text(words)
                        s_ms = int(line.get("startTime") or 0); e_ms = int(line.get("endTime") or 0)
                        print(f"{i:04d} [{ms_to_ts(s_ms)} → {ms_to_ts(e_ms)}] {line_text}")
                        is_bg = bool(line.get("isBG", False)); is_duet = bool(line.get("isDuet", False))
                        for j, wobj in enumerate(words, 1):
                            for k, ev in enumerate(split_word_to_chars(wobj), 1):
                                rows.append({
                                    "line_index": i, "word_index": j, "char_index": k,
                                    "char": ev["char"], "roman_char": ev["roman_char"],
                                    "start_ms": ev["start_ms"], "end_ms": ev["end_ms"],
                                    "start_ts": ms_to_ts(ev["start_ms"]), "end_ts": ms_to_ts(ev["end_ms"]),
                                    "is_bg": is_bg, "is_duet": is_duet
                                })
                    if rows:
                        import csv
                        name = f"lyrics_chars_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
                        with open(EXPORTS_DIR / name, "w", encoding="utf-8-sig", newline="") as f:
                            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys())); writer.writeheader(); writer.writerows(rows)
                        print(f"[WS] 逐字 CSV 已导出：exports/{name}（{len(rows)} 条）")

                    lines_front = _amll_lines_to_front(payload)
                    try:
                        compute_disappear_times(lines_front, delta1=500, delta2=0)
                    except Exception as e:
                        app.logger.warning(f"[WS] 计算消失时机失败，降级继续: {e}")
                    total_syllables = sum(len(l.get('syllables', [])) for l in lines_front)
                    print(f"[WS] 收到歌词(state) {len(payload)} 行，已转换为 {total_syllables} 个逐字单元（含 disappearTime）")
                    _amll_publish("lyrics", {"lines": lines_front})
                    continue
                if update in ("progress", "playprogress", "onplayprogress", "setprogress", "position", "time"):
                    prog = _extract_progress_ms_from_state(val)
                    if prog is not None:
                        app.logger.debug(f"[WS] 进度(state)：{prog}")
                        _amll_publish("progress", {"progress_ms": prog})
                    continue
                if update in ("resumed", "resume", "playing", "paused", "pause", "stopped", "stop"):
                    app.logger.debug(f"[WS] 播放状态(state)：{update}")
                    continue
            if mtype == "onplayprogress":
                prog = int((msg.get("value") or {}).get("progress") or 0)
                # 不打印每秒进度到控制台，避免刷屏
                app.logger.debug(f"[WS] 进度(ms)：{prog}")
                _amll_publish("progress", {"progress_ms": prog})
                continue
            if mtype in ("onresumed","onpaused"):
                app.logger.debug(f"[WS] 播放状态：{mtype}"); continue

            # 逐字展开导出
            if mtype == "setlyric":
                payload = msg.get("value", {}).get("data", [])
                print(f"[WS] 收到歌词 {len(payload)} 行（逐字导出）")
                rows = []
                for i, line in enumerate(payload, 1):
                    words = line.get("words", [])
                    line_text = join_line_text(words)
                    s_ms = int(line.get("startTime") or 0); e_ms = int(line.get("endTime") or 0)
                    print(f"{i:04d} [{ms_to_ts(s_ms)} → {ms_to_ts(e_ms)}] {line_text}")
                    is_bg = bool(line.get("isBG", False)); is_duet = bool(line.get("isDuet", False))
                    for j, wobj in enumerate(words, 1):
                        for k, ev in enumerate(split_word_to_chars(wobj), 1):
                            rows.append({
                                "line_index": i, "word_index": j, "char_index": k,
                                "char": ev["char"], "roman_char": ev["roman_char"],
                                "start_ms": ev["start_ms"], "end_ms": ev["end_ms"],
                                "start_ts": ms_to_ts(ev["start_ms"]), "end_ts": ms_to_ts(ev["end_ms"]),
                                "is_bg": is_bg, "is_duet": is_duet
                            })
                if rows:
                    import csv
                    name = f"lyrics_chars_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
                    with open(EXPORTS_DIR / name, "w", encoding="utf-8-sig", newline="") as f:
                        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys())); writer.writeheader(); writer.writerows(rows)
                    print(f"[WS] 逐字 CSV 已导出：exports/{name}（{len(rows)} 条）")

                # 转发到前端 AMLL 流（先计算消失时机）
                lines_front = _amll_lines_to_front(payload)

                try:
                    compute_disappear_times(lines_front, delta1=500, delta2=0)
                except Exception as e:
                    app.logger.warning(f"[WS] 计算消失时机失败，降级继续: {e}")

                total_syllables = sum(len(l.get('syllables', [])) for l in lines_front)
                print(f"[WS] 收到歌词 {len(payload)} 行，已转换为 {total_syllables} 个逐字单元（含 disappearTime）")
                _amll_publish("lyrics", {"lines": lines_front})
                continue

            print("[WS] 未知消息：", msg)
    except websockets.ConnectionClosed as e:
        print(f"[WS] 断开: {peer}, code={e.code}, reason={e.reason!r}")
    except Exception as e:
        print("[WS] 处理异常：", e)

# --- WebSocket 启动修复：在正在运行的事件循环中创建 server ---
async def _ws_main():
    try:
        # 在“已运行的事件循环中”创建 server
        async with websockets.serve(
            ws_handle,                  # 你已有的消息处理函数
            WS_HOST, WS_PORT,
            ping_interval=None,
            ping_timeout=None,
            max_size=64 * 1024 * 1024
        ) as server:
            # 打印真实监听的 sockets，便于自检
            sockets = getattr(server, "sockets", []) or []
            addrs = []
            for s in sockets:
                try:
                    addrs.append(s.getsockname())
                except Exception:
                    pass
            print(f"[WS] 已启动：ws://localhost:{WS_PORT}（监听={WS_HOST or 'ALL'}，sockets={addrs}）")

            # 阻塞保持运行
            await asyncio.Future()
    except OSError as e:
        print(f"[WS] 启动失败：{e} —— 多半是端口被占用或权限问题")
    except Exception as e:
        import traceback
        print("[WS] 未捕获异常：")
        traceback.print_exc()

def _run_ws_loop():
    # 在线程里创建并运行事件循环
    asyncio.run(_ws_main())

def start_ws_server_once():
    # 避免 Flask Debug reloader 启两遍
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug:
        t = threading.Thread(target=_run_ws_loop, name="WS-Server", daemon=True)
        t.start()
        return t

if __name__ == '__main__':
    import random
    def try_run(port):
        try:
            # 启动时同步 port_status.json
            if port == 5000:
                set_port_status('normal', 5000)
            else:
                set_port_status('random', port)
            print(f'[启动] 尝试端口: {port}')
            url = f"http://127.0.0.1:{port}"
            webbrowser.open(url)
            # 写入启动命令到文件
            # 检测当前是否是exe文件运行
            is_exe = getattr(sys, 'frozen', False)
            if is_exe:
                # exe模式下，使用当前可执行文件路径
                exe_path = sys.executable
                startup_cmd = f"set USE_WAITRESS=1\n"
                startup_cmd += f"\"{exe_path}\" {port}\n"
            else:
                # 开发模式下，使用python backend.py
                startup_cmd = f"set USE_WAITRESS=1\npython backend.py {port}\n"
            with open(BASE_PATH / 'last_startup.bat', 'w', encoding='utf-8') as f:
                f.write(startup_cmd)
            with open(BASE_PATH / 'last_startup.txt', 'w', encoding='utf-8') as f:
                f.write(startup_cmd)
            # 检查是否用waitress启动
            if os.environ.get('USE_WAITRESS', '0') == '1':
                from waitress import serve
                # 标准化的 Waitress 配置参数
                serve(
                    app,
                    host='0.0.0.0',
                    port=port,
                    threads=int(os.getenv('WT_THREADS', 8)),             # 线程数
                    connection_limit=int(os.getenv('WT_CONN_LIMIT', 200)),# 并发连接上限
                    channel_timeout=int(os.getenv('WT_TIMEOUT', 30)),     # 空闲通道超时（秒）
                    backlog=int(os.getenv('WT_BACKLOG', 512)),            # 半连接队列
                    ident=None                                           # 服务器标识
                )
            else:
                app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
            return True
        except OSError as e:
            print(f'[启动] 端口 {port} 启动失败: {e}')
            return False

    port = 5000
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
            # 验证端口范围 (1-65535)
            if port < 1 or port > 65535:
                print(f'[错误] 端口 {port} 无效，端口范围应为 1-65535，使用默认端口 5000')
                port = 5000
            else:
                print(f'[信息] 使用指定端口: {port}')
        except ValueError:
            print(f'[错误] 端口参数 "{sys.argv[1]}" 不是有效数字，使用默认端口 5000')
            port = 5000
        except Exception as e:
            print(f'[错误] 处理端口参数时出错: {e}，使用默认端口 5000')
            port = 5000
    else:
        print('[信息] 未指定端口，使用默认端口 5000')
    
    print(f'[主进程启动] sys.argv: {sys.argv}, 最终启动端口: {port}')

    # 先起 WS，再起 Flask
    ws_thread = start_ws_server_once()

    if not try_run(port):
        # 5000端口失败，换随机端口
        for _ in range(10):
            random_port = random.randint(1025, 65535)
            if try_run(random_port):
                break
