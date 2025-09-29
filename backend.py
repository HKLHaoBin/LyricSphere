#最终发布版本
import hashlib
import json
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
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from flask import Flask, jsonify, render_template, request, Response, session, g, abort, stream_with_context
from re import compile, Pattern, Match
from typing import Iterator, TextIO, AnyStr
from xml.dom.minicompat import NodeList
from xml.dom.minidom import Document, Element
from openai import OpenAI
import random
import threading
import socket
import asyncio
import websockets
import queue


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

# 所有路径定义使用绝对路径
SONGS_DIR = BASE_PATH / 'static' / 'songs'
BACKUP_DIR = BASE_PATH / 'static' / 'backups'
LOG_DIR = BASE_PATH / 'logs'

# 自动创建目录（首次运行时）
for path in [SONGS_DIR, BACKUP_DIR, LOG_DIR]:
    path.mkdir(parents=True, exist_ok=True)

# 配置日志
log_format = '%(asctime)s - %(levelname)s - %(message)s'
log_handler = TimedRotatingFileHandler(os.path.join(LOG_DIR, 'upload.log'),
                                       when='midnight',
                                       interval=1,
                                       backupCount=7,
                                       encoding='utf-8')
log_handler.setFormatter(logging.Formatter(log_format))
app.logger.addHandler(log_handler)

# 设置日志级别，支持通过环境变量启用调试日志
log_level = logging.DEBUG if os.environ.get('DEBUG_LOGGING', '0') == '1' else logging.INFO
app.logger.setLevel(log_level)

useu = ""

# ==== AMLL -> 前端 的实时总线（SSE） ====
# 全局状态（给前端快照用）
AMLL_STATE = {
    "song": {"musicName": "", "artists": [], "duration": 0},
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
5. 确保每行翻译都是独立的，不要将多行合并'''
}

# ===== 解析.lys格式歌词的工具函数 =====
def compute_disappear_times(lines, *, delta1=500, delta2=0, t_anim=700):
    """
    对每一行（含 syllables 数组，单位秒）计算 disappearTime（单位毫秒）。
    规则与 parse_lys 中保持一致：
    - 行末 E_i 与下一行首 N_next 的关系，DELTA1/DELTA2 调整
    - 与上一行最终 T_disappear_prev 的"礼让/衔接"
    """
    if not lines:
        return lines

    # 复制索引，计算按开始时间排序，不改变原顺序
    for idx, line in enumerate(lines):
        line['__orig_idx'] = idx

    def first_start_ms(line):
        s = line.get('syllables', [])
        if not s:
            return float('inf')
        return int(float(s[0]['startTime']) * 1000)

    def last_end_ms(line):
        s = line.get('syllables', [])
        if not s:
            return 0
        last = s[-1]
        return int(float(last['startTime']) * 1000 + float(last['duration']) * 1000)

    sorted_lines = sorted(lines, key=first_start_ms)

    for i, line in enumerate(sorted_lines):
        E_i = last_end_ms(line)
        # 候选：自身结束
        T_candidate = E_i

        # 下一行首
        N_next = float('inf')
        if i + 1 < len(sorted_lines):
            N_next = first_start_ms(sorted_lines[i + 1])
            # 贴边调整
            if N_next - delta1 <= E_i <= N_next + delta2:
                T_candidate = N_next + delta2

        # 与上一行最终时间的礼让/衔接
        if i > 0:
            T_prev_final = sorted_lines[i - 1].get('disappearTime', 0)
            if T_prev_final > T_candidate:
                # 重叠：允许贴边，拉开动画时间
                T_final = min(N_next, E_i + t_anim, T_prev_final + t_anim)
            else:
                T_final = T_candidate
        else:
            T_final = T_candidate

        line['disappearTime'] = T_final
        line['debug_times'] = {
            'E_i': E_i,
            'T_candidate': T_candidate,
            'T_prev_final': sorted_lines[i - 1].get('disappearTime', 0) if i > 0 else 0,
            'final': T_final
        }

    # 写回原顺序
    disappear_map = {l['__orig_idx']: l['disappearTime'] for l in sorted_lines}
    debug_map = {l['__orig_idx']: l['debug_times'] for l in sorted_lines}
    for line in lines:
        line['disappearTime'] = disappear_map.get(line['__orig_idx'], 0)
        line['debug_times'] = debug_map.get(line['__orig_idx'], {})
        if '__orig_idx' in line:
            del line['__orig_idx']

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
    cleanup_regex = re.compile(r'\(\d+,\d+\)')
    offset_regex = re.compile(r'\[offset:\s*(-?\d+)\s*\]')
    last_align = 'left'
    offset = 0

    # 查找并解析 offset
    offset_match = offset_regex.search(lys_content)
    if offset_match:
        offset = int(offset_match.group(1))

    for line in lys_content.splitlines():
        # 跳过元数据行
        if line.startswith('[from:') or line.startswith('[id:') or line.startswith('[offset:'):
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
        align = 'left'
        font_size = 'normal'
        
        if not marker:
            align = 'center'
        elif marker in ['2', '5']:
            align = 'right'
        elif marker in ['6', '7', '8']:
            align = last_align
            font_size = 'small'
        
        syllables = []
        full_line_text = ""
        matches = block_regex.finditer(content)
        for match in matches:
            text_part, start_ms, duration_ms = match.groups()
            cleaned_text = cleanup_regex.sub('', text_part)
            cleaned_text = re.sub(r'[()]', '', cleaned_text)
            if cleaned_text:
                syllables.append({
                    'text': cleaned_text,
                    'startTime': (int(start_ms) + offset) / 1000.0, # 应用 offset
                    'duration': int(duration_ms) / 1000.0
                })
                full_line_text += cleaned_text
        
        if syllables:
            lyrics_data.append({
                'line': full_line_text,
                'syllables': syllables,
                'style': {
                    'align': align,
                    'fontSize': font_size
                }
            })
        last_align = align
    
    # 如果没有歌词数据，直接返回
    if not lyrics_data:
        return []

    # === 统一用通用函数计算消失时机 ===
    compute_disappear_times(lyrics_data, delta1=500, delta2=0, t_anim=700)
    return lyrics_data

@app.route('/')
def index():
    json_files = []
    static_dir = BASE_PATH / 'static'
    for file in static_dir.iterdir():
        if file.suffix == '.json':
            mtime = file.stat().st_mtime
            with open(file, 'r', encoding='utf-8') as f:
                try:
                    data = json.load(f)
                    json_files.append({
                        'filename': file.name,
                        'data': data,
                        'mtime': mtime  # 添加修改时间
                    })
                except:
                    continue
    # 按修改时间排序（最新在前）
    json_files.sort(key=lambda x: x['mtime'], reverse=True)
    return render_template('Famyliam_Everywhere.html', json_files=json_files)

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

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_path = BACKUP_DIR / f"{Path(file_path).name}.{timestamp}"
    shutil.copy2(file_path, backup_path)
    return jsonify({'status': 'success'})


@app.route('/delete_json', methods=['POST'])
def delete_json():
    if not is_request_allowed():
        return abort(403)
    data = request.json
    filename = data['filename']
    json_path = BASE_PATH / 'static' / filename

    try:
        related_files = get_related_files(str(json_path))
        delete_backup_dir = BACKUP_DIR / 'permanent'
        delete_backup_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        for file_path in related_files:
            if Path(file_path).exists():
                relative_path = Path(file_path).relative_to(BASE_PATH)
                backup_path = delete_backup_dir / f"{str(relative_path).replace('/', '__')}.{timestamp}"
                backup_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(file_path, backup_path)

        for file_path in related_files:
            if Path(file_path).exists():
                Path(file_path).unlink()

        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})


@app.route('/restore_file', methods=['POST'])
def restore_file():
    if not is_request_allowed():
        return abort(403)
    file_path = request.json.get('file_path')
    try:
        # 如果是备份文件路径
        if 'backups' in file_path:
            backup_path = Path(file_path.replace('http://127.0.0.1:5000/backups/',
                                            str(BACKUP_DIR) + '/'))
            original_name = '.'.join(
                backup_path.name.split('.')[:-1])
            restore_path = BASE_PATH / 'static' / original_name
            shutil.copy2(backup_path, restore_path)
        else:
            # 获取所有关联文件备份
            related_files = get_related_files(file_path)  # 新增关联文件获取方法
            backups = []

            # 为每个关联文件创建恢复任务
            for file in related_files:
                file_backups = []
                for backup in BACKUP_DIR.iterdir():
                    if backup.name.startswith(Path(file).name):
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
    related_files = [json_path]

    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # 获取歌词相关文件
        lyrics_info = data['meta'].get('lyrics', '').split('::')
        for path in lyrics_info[1:4]:  # 歌词、翻译、音译路径
            if path and path != '!':
                local_path = path.replace('http://127.0.0.1:5000/songs/',
                                          str(SONGS_DIR) + '/')
                related_files.append(local_path)

        # 获取音频文件
        if 'song' in data:
            local_music = data['song'].replace('http://127.0.0.1:5000/songs/',
                                               str(SONGS_DIR) + '/')
            related_files.append(local_music)

        # 获取专辑图
        if 'albumImgSrc' in data['meta']:
            local_img = data['meta']['albumImgSrc'].replace(
                'http://127.0.0.1:5000/songs/', str(SONGS_DIR) + '/')
            related_files.append(local_img)

    except Exception as e:
        print(f"Error getting related files: {str(e)}")

    return list(set(related_files))  # 去重


@app.route('/update_json', methods=['POST'])
def update_json():
    if not is_request_allowed():
        return abort(403)
    data = request.json
    file_path = BASE_PATH / 'static' / data["filename"]

    try:
        # 确保备份目录存在
        if not BACKUP_DIR.exists():
            BACKUP_DIR.mkdir(parents=True, exist_ok=True)

        # 备份原文件
        backup_path = BACKUP_DIR / f"{data['filename']}.{int(time.time())}"
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
    try:
        data = request.json
        if not data or 'path' not in data or 'content' not in data:
            app.logger.error("无效的请求数据: 缺少必要的字段")
            return jsonify({'status': 'error', 'message': '无效的请求数据'})

        # 验证路径
        if not data['path'] or data['path'] == '.' or data['path'] == './':
            app.logger.error(f"无效的文件路径: {data['path']}")
            return jsonify({'status': 'error', 'message': '无效的文件路径'})

        # 验证路径格式
        if not data['path'].startswith('http://127.0.0.1:5000/songs/'):
            app.logger.error(f"无效的路径格式: {data['path']}")
            return jsonify({'status': 'error', 'message': '无效的路径格式'})

        file_path = Path(data['path'].replace('http://127.0.0.1:5000/songs/',
                                         str(SONGS_DIR) + '/'))
        content = data['content']

        # 验证文件路径
        if not file_path.is_absolute():
            app.logger.error(f"无效的文件路径: {file_path}, 必须是绝对路径")
            return jsonify({'status': 'error', 'message': '无效的文件路径'})

        # 验证文件是否在允许的目录中
        try:
            file_path.relative_to(SONGS_DIR)
        except ValueError:
            app.logger.error(f"文件路径不在允许的目录中: {file_path}")
            return jsonify({'status': 'error', 'message': '文件路径不在允许的目录中'})

        # 验证文件名
        if not file_path.name or file_path.name == '.' or file_path.name == '..':
            app.logger.error(f"无效的文件名: {file_path.name}")
            return jsonify({'status': 'error', 'message': '无效的文件名'})

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
                    backup_path = BACKUP_DIR / f"{json_path.name}.{int(time.time())}"
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
                backups = sorted([
                    f for f in BACKUP_DIR.iterdir()
                    if f.name.startswith(file_path.name) and not f.is_dir()
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
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                backup_path = BACKUP_DIR / f"{file_path.name}.{timestamp}"
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
                f.write(content)
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
        backup_path = BACKUP_DIR / f"{data['jsonFile']}.{timestamp}"
        if json_path.exists():  # 只在文件存在时进行备份
            shutil.copy2(json_path, backup_path)

        # 更新路径
        if file_type == 'music':
            json_data['song'] = f"http://127.0.0.1:5000/songs/{new_path}"
        elif file_type == 'image':
            json_data['meta'][
                'albumImgSrc'] = f"http://127.0.0.1:5000/songs/{new_path}"
        elif file_type == 'background':
            json_data['meta']['Background-image'] = f"./songs/{new_path}"
        elif file_type == 'lyrics':
            current_lyrics = json_data['meta']['lyrics'].split('::')
            if len(current_lyrics) >= 4:
                if data.get('index') == 0:  # 歌词文件
                    new_lyrics_path = f"http://127.0.0.1:5000/songs/{new_path}" if new_path else '!'
                    current_lyrics[1] = new_lyrics_path
                elif data.get('index') == 1:  # 歌词翻译
                    new_translation_path = f"http://127.0.0.1:5000/songs/{new_path}" if new_path else '!'
                    current_lyrics[2] = new_translation_path
                elif data.get('index') == 2:  # 歌词音译
                    new_transliteration_path = f"http://127.0.0.1:5000/songs/{new_path}" if new_path else '!'
                    current_lyrics[3] = new_transliteration_path
                json_data['meta']['lyrics'] = '::'.join(current_lyrics)

        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(json_data, f, ensure_ascii=False, indent=2)

        # 在更新路径后添加文件创建逻辑
        new_local_path = SONGS_DIR / new_path
        if not new_local_path.parent.exists():
            new_local_path.parent.mkdir(parents=True, exist_ok=True)

        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})


@app.route('/create_json', methods=['POST'])
def create_json():
    if not is_request_allowed():
        return abort(403)
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
    data = request.json
    old_filename = data['oldFilename']
    new_filename = data['newFilename']
    title = data['title']
    artists = data['artists']

    old_path = BASE_PATH / 'static' / old_filename
    # 清理文件名，替换全角引号
    new_filename = re.sub(r'[^\w\u4e00-\u9fa5-_.]', '', new_filename).replace('"', '＂').replace("'", '＂')
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
        backup_path = BACKUP_DIR / f"{old_filename}.{timestamp}"
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
    lyrics_path = request.json.get('path')
    real_path = Path(lyrics_path.replace('http://127.0.0.1:5000/songs/',
                                    str(SONGS_DIR) + '/'))

    try:
        with open(real_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # 检查是否包含对唱标记
        has_duet = '[2]' in content or '[5]' in content or 'ttm:agent="v2"' in content

        # 检查是否包含背景人声标记
        has_background = '[6]' in content or '[7]' in content or '[8]' in content or 'ttm:role="x-bg"' in content

        return jsonify({
            'status': 'success',
            'hasDuet': has_duet,
            'hasBackgroundVocals': has_background
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})


@app.route('/get_backups', methods=['POST'])
def get_backups():
    file_path = request.json.get('path').replace(
        'http://127.0.0.1:5000/songs/', str(SONGS_DIR) + '/')
    base_name = Path(file_path).name

    try:
        backups = []
        for f in BACKUP_DIR.iterdir():
            if f.name.startswith(base_name):
                timestamp = f.name.split('.')[-1]
                if len(timestamp) == 15 and timestamp.isdigit():
                    backups.append({
                        'path':
                        f"http://127.0.0.1:5000/backups/{f.name}",
                        'time':
                        f"{timestamp[0:4]}-{timestamp[4:6]}-{timestamp[6:8]} {timestamp[9:11]}:{timestamp[11:13]}:{timestamp[13:15]}"
                    })
        # 按时间倒序排列并取前7个
        backups = sorted(backups, key=lambda x: x['time'], reverse=True)[:7]
        return jsonify({'status': 'success', 'backups': backups})
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

        # 清理文件名，替换全角引号
        clean_name = re.sub(r'[^\w\u4e00-\u9fa5-_.]', '', file.filename).replace('"', '＂').replace("'", '＂')
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

        # 清理文件名，替换全角引号
        clean_name = re.sub(r'[^\w\u4e00-\u9fa5-_.]', '', file.filename).replace('"', '＂').replace("'", '＂')
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

        # 清理文件名，替换全角引号
        clean_name = re.sub(r'[^\w\u4e00-\u9fa5-_.]', '', file.filename).replace('"', '＂').replace("'", '＂')
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

        # 清理文件名，替换全角引号
        clean_name = re.sub(r'[^\w\u4e00-\u9fa5-_.]', '', file.filename).replace('"', '＂').replace("'", '＂')
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
        self.text: str = element.childNodes[0].nodeValue

    def __str__(self) -> str:
        return f'{self.text}({int(self.__begin)},{self.__end - self.__begin})'

    def get_begin(self) -> TTMLTime:
        return self.__begin

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

        # 遍历所有子元素
        for child in child_elements:
            if child.nodeType == 3 and child.nodeValue:  # 如果是文本节点（例如空格或换行）
                if len(self.__orig_line) > 0 and len(child.nodeValue) < 2:
                    self.__orig_line[-1].text += child.nodeValue
                else:
                    self.__orig_line.append(child.nodeValue)
            else:
                # 获取 <span> 中的属性
                role:str = child.getAttribute("ttm:role")

                # 没有role代表是一个syl
                if role == "":
                    if child.childNodes[0].nodeValue:
                        self.__orig_line.append(TTMLSyl(child))

                elif role == "x-bg":
                    # 和声行
                    self.__bg_line = TTMLLine(child, True)
                    self.__bg_line.__is_duet = self.__is_duet
                elif role == "x-translation":
                    # 翻译行
                    TTMLLine.have_ts = True
                    self.__ts_line = f'{child.childNodes[0].data}'

        # 确保__orig_line不为空且第一个元素是TTMLSyl对象
        # 否则可能会出现'str'没有get_begin属性的错误或索引错误
        if self.__orig_line and len(self.__orig_line) > 0 and isinstance(self.__orig_line[0], TTMLSyl):
            self.__begin = self.__orig_line[0].get_begin()
        else:
            # 如果__orig_line为空或第一个元素不是TTMLSyl对象，使用默认的TTMLTime
            self.__begin = TTMLTime()
            # 如果__orig_line为空，添加一个空字符串防止后续处理出错
            if not self.__orig_line:
                self.__orig_line.append('')

        if is_bg:
            if TTMLLine.__before.search(self.__orig_line[0].text):
                self.__orig_line[0].text = TTMLLine.__before.sub(self.__orig_line[0].text, '(')
                TTMLLine.have_pair += 1
            if TTMLLine.__after.search(self.__orig_line[-1].text):
                self.__orig_line[-1].text = TTMLLine.__after.sub(self.__orig_line[-1].text, ')')
                TTMLLine.have_pair += 1

    def __role(self) -> int:
        return ((int(TTMLLine.have_bg) + int(self.__is_bg)) * 3
                + int(TTMLLine.have_duet) + int(self.__is_duet))

    def __raw(self):
        # 返回元组(str, str或None)
        try:
            # 安全地生成字符串，确保__orig_line不为空
            line_text = ''.join([str(v) for v in self.__orig_line]) if self.__orig_line else ''
            return (f'[{self.__role()}]{line_text}',
                    f'[{self.__begin}]{self.__ts_line}' if self.__ts_line else None)
        except Exception as e:
            # 如果生成过程中出现错误，返回一个安全的默认值
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
        # 解析XML文件
        dom: Document = xml.dom.minidom.parse(input_path)
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
                    app.logger.error(f"处理TTML行时出错: {str(e)}，跳过此行")
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
                        if bg_line[1] and trans_file:
                            trans_file.write(bg_line[1] + '\n')
                            trans_file.flush()
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
        clean_name = re.sub(r'[^\w\u4e00-\u9fa5-_.]', '', file.filename)
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
            result = {
                'status': 'success',
                'lyricPath': f"http://127.0.0.1:5000/songs/{os.path.basename(lyric_path)}"
            }
            
            if trans_path:
                result['transPath'] = f"http://127.0.0.1:5000/songs/{os.path.basename(trans_path)}"
            
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
        ttml_path = SONGS_DIR / ttml_filename
        if not ttml_path.exists():
            return jsonify({'status': 'error', 'message': 'TTML文件不存在'})
        # 直接调用原有转换逻辑
        success, lyric_path, trans_path = ttml_to_lys(str(ttml_path), str(SONGS_DIR))
        if success:
            result = {
                'status': 'success',
                'lyricPath': f'http://127.0.0.1:5000/songs/{os.path.basename(lyric_path)}'
            }
            if trans_path:
                result['transPath'] = f'http://127.0.0.1:5000/songs/{os.path.basename(trans_path)}'
            return jsonify(result)
        else:
            return jsonify({'status': 'error', 'message': '转换失败，请检查TTML文件格式是否正确'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})

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
        
        for i, line in enumerate(lines, 1):
            if not line.strip():
                empty_lines += 1
                app.logger.debug(f"第{i}行: 空行，跳过")
                continue
                
            # 跳过元数据行
            if line.startswith('[by:') or line.startswith('[ti:') or line.startswith('[ar:'):
                metadata_lines += 1
                app.logger.debug(f"第{i}行: 元数据行 '{line[:50]}...'，跳过")
                continue
            
            # 处理所有以 [] 或 [数字] 开头的歌词行
            if re.match(r'^\[(\d*)\]', line):
                # 只匹配第一个左括号后面的开始时间
                match = re.search(r'\((\d+),', line)
                if match:
                    try:
                        timestamp = int(match.group(1))
                        time_str = convert_milliseconds_to_time(timestamp)
                        lrc_timestamp = f"[{time_str}]"
                        timestamps.append(lrc_timestamp)
                        processed_lines += 1
                        app.logger.debug(f"第{i}行: 成功提取时间戳 '{lrc_timestamp}' (原始值: {match.group(1)}ms)")
                        app.logger.debug(f"第{i}行内容: '{line[:100]}...'" if len(line) > 100 else f"第{i}行内容: '{line}'")
                    except ValueError as e:
                        app.logger.warning(f"第{i}行: 时间戳转换失败 '{match.group(1)}', 错误: {str(e)}")
                        continue
                else:
                    app.logger.debug(f"第{i}行: 符合歌词行格式但未找到时间戳，行内容: '{line[:100]}...'" if len(line) > 100 else f"第{i}行: 符合歌词行格式但未找到时间戳，行内容: '{line}'")
            else:
                app.logger.debug(f"第{i}行: 不符合歌词行格式，跳过。内容: '{line[:100]}...'" if len(line) > 100 else f"第{i}行: 不符合歌词行格式，跳过。内容: '{line}'")
        
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
        AI_TRANSLATION_SETTINGS['system_prompt'] = data.get('system_prompt', AI_TRANSLATION_SETTINGS['system_prompt'])
        return jsonify({
            'status': 'success',
            'api_key': AI_TRANSLATION_SETTINGS['api_key'],
            'system_prompt': AI_TRANSLATION_SETTINGS['system_prompt']
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})

@app.route('/translate_lyrics', methods=['POST'])
def translate_lyrics():
    try:
        # 获取请求数据
        request_data = request.get_json()
        content = request_data.get('content', '')
        api_key = request_data.get('api_key', '')
        # 优先用前端传的 system_prompt，没有则用全局默认
        system_prompt = request_data.get('system_prompt') or AI_TRANSLATION_SETTINGS['system_prompt']

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

        app.logger.info(f"提取的时间戳数量: {len(timestamps)}")
        app.logger.info(f"提取的歌词行数: {len(lyrics)}")
        app.logger.info(f"系统提示词: {system_prompt[:100]}..." if len(system_prompt) > 100 else f"系统提示词: {system_prompt}")

        # 验证提取的内容
        if not timestamps:
            app.logger.error("未提取到任何时间戳")
            return jsonify({'status': 'error', 'message': '未提取到任何时间戳，请检查歌词格式是否正确'})

        if not lyrics or all(not line.strip() for line in lyrics):
            app.logger.error("未提取到任何歌词内容")
            return jsonify({'status': 'error', 'message': '未提取到任何歌词内容，请检查歌词格式是否正确'})

        if len(timestamps) != len(lyrics):
            app.logger.error(f"时间戳数量({len(timestamps)})与歌词行数({len(lyrics)})不匹配")
            return jsonify({'status': 'error', 'message': '时间戳数量与歌词行数不匹配，请检查歌词格式是否正确'})

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

                # 记录API调用详细信息
                app.logger.info(f"准备调用DeepSeek API [ID: {request_id}]")
                app.logger.info(f"模型: deepseek-reasoner, 歌词行数: {len(lyrics)}")
                app.logger.info(f"提示词长度: {len(numbered_lyrics)} 字符")
                app.logger.info(f"系统提示词摘要: {system_prompt[:200]}..." if len(system_prompt) > 200 else f"系统提示词: {system_prompt}")
                
                # 调用AI服务
                api_start_time = time.time()
                try:
                    client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")
                    response = client.chat.completions.create(
                        model="deepseek-reasoner",
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": numbered_lyrics}
                        ],
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
                    
                    # 记录token使用情况（如果有）
                    if hasattr(chunk, 'usage') and chunk.usage:
                        total_tokens = getattr(chunk.usage, 'total_tokens', 0)
                        
                    # 检查是否有思维链内容
                    if hasattr(chunk.choices[0].delta, 'reasoning_content') and chunk.choices[0].delta.reasoning_content:
                        content = chunk.choices[0].delta.reasoning_content
                        current_reasoning += content
                        app.logger.debug(f"收到思维链内容 [ID: {request_id}]: {content}")
                        # 发送思维链内容
                        yield f"reasoning:{json.dumps({'reasoning': current_reasoning})}\n"
                    
                    # 检查是否有普通内容
                    if hasattr(chunk.choices[0].delta, 'content') and chunk.choices[0].delta.content:
                        content = chunk.choices[0].delta.content
                        full_translation += content
                        app.logger.debug(f"收到翻译内容 [ID: {request_id}]: {content}")

                        # 处理翻译内容
                        lines = full_translation.split('\n')
                        translated_lines = []
                        for line in lines:
                            if line.strip() and not line.startswith('思考'):
                                # 提取序号和翻译内容
                                match = re.match(r'^\d+\.(.*)', line)
                                if match:
                                    translated_lines.append(match.group(1).strip())

                        # 确保翻译行数与原文行数匹配
                        if len(translated_lines) == len(lyrics):
                            # 合并时间戳和翻译
                            final_lyrics = []
                            for i, (timestamp, translation) in enumerate(zip(timestamps, translated_lines)):
                                final_lyrics.append(f"{timestamp}{translation}")
                            # 发送翻译内容
                            yield f"content:{json.dumps({'translations': final_lyrics})}\n"
                            app.logger.debug(f"成功合并 {len(final_lyrics)} 行翻译歌词")
                        else:
                            app.logger.warning(f"翻译行数不匹配! 原文行数: {len(lyrics)}, 翻译行数: {len(translated_lines)}")
                            app.logger.debug(f"原文行数详情: {len(lyrics)} 行")
                            app.logger.debug(f"翻译行数详情: {len(translated_lines)} 行")
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

@app.route('/lyrics-animate')
def lyrics_animate():
    file = request.args.get('file')
    style = request.args.get('style', 'Kok')  # 默认为 'Kok'
    if not file:
        return "缺少文件参数", 400
    session['lyrics_json_file'] = file
    if style == '亮起':
        return render_template('Lyrics-style.HTML')
    else:  # 默认为 'Kok' 或其他值
        return render_template('Lyrics-style.HTML-v1.HTML')

@app.route('/lyrics')
def get_lyrics():
    """
    获取歌词和音源信息
    支持的音乐格式：.mp3, .wav, .ogg, .mp4
    """
    json_file = session.get('lyrics_json_file', '测试 - 测试.json')
    json_path = os.path.join(app.static_folder, json_file)
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            meta_data = json.load(f)
        lyrics_info = meta_data.get('meta', {}).get('lyrics', '')
        lyric_sources = [src for src in lyrics_info.split('::') if src and src != '!']
        lys_url = None
        lrc_url = None
        for src in lyric_sources:
            if src.endswith('.lys'):
                lys_url = src
            if src.endswith('.lrc'):
                lrc_url = src
        if not lys_url:
            return jsonify({'error': '.lys 文件链接未在元数据中找到'}), 404
        from urllib.parse import urlparse
        parsed_url = urlparse(lys_url)
        lyrics_path = os.path.join(app.static_folder, parsed_url.path.lstrip('/'))
        with open(lyrics_path, 'r', encoding='utf-8-sig') as f:
            lys_content = f.read()
        parsed_lyrics = parse_lys(lys_content)

        # 新增：提取 offset
        offset = 0
        offset_match = re.search(r'\[offset:\s*(-?\d+)\s*\]', lys_content)
        if offset_match:
            offset = int(offset_match.group(1))

        # 解析翻译
        translation = []
        if lrc_url:
            parsed_lrc_url = urlparse(lrc_url)
            lrc_path = os.path.join(app.static_folder, parsed_lrc_url.path.lstrip('/'))
            if os.path.exists(lrc_path):
                with open(lrc_path, 'r', encoding='utf-8') as f:
                    lrc_content = f.read()
                translation = parse_lrc(lrc_content, offset=offset)  # 传递 offset

        return jsonify({'lyrics': parsed_lyrics, 'translation': translation})
    except FileNotFoundError:
        return jsonify({'error': '歌词文件未找到'}), 404
    except json.JSONDecodeError:
        return jsonify({'error': '解析元数据JSON时出错'}), 500

@app.route('/export_lyrics_csv', methods=['POST'])
def export_lyrics_csv():
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
    try:
        data = request.get_json()
        lyrics_path = data.get('path', '')
        if not lyrics_path:
            return jsonify({'status': 'error', 'message': '缺少歌词路径'}), 400

        # 只允许读取 static/songs 下的歌词文件
        if not lyrics_path.startswith('http://127.0.0.1:5000/songs/'):
            return jsonify({'status': 'error', 'message': '路径不合法'}), 400

        real_path = Path(lyrics_path.replace('http://127.0.0.1:5000/songs/', str(SONGS_DIR) + '/'))
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
        
    return remote in ['127.0.0.1', '::1']

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

def is_request_allowed():
    """统一的请求权限检查函数"""
    # 获取安全配置
    security_config = get_security_config()
    
    # 安全保护关闭时允许所有访问
    if not security_config.get('security_enabled', True):
        return True
        
    # 本机回环地址完全放行
    remote = request.remote_addr
    if remote in ['127.0.0.1', '::1']:
        return True
        
    # 检查设备是否受信任
    device_id = request.cookies.get('FEW_DEVICE_ID')
    if device_id and is_trusted_device(device_id):
        return True
        
    return False

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
    return jsonify({
        "song": AMLL_STATE["song"],
        "progress_ms": AMLL_STATE["progress_ms"],
        "lines": AMLL_STATE["lines"]
    })

@app.route('/amll/stream')
def amll_stream_api():
    """AMLL 实时事件流 API (Server-Sent Events)"""
    @stream_with_context
    def _gen():
        # 先发一份完整快照（让新打开的前端立刻有内容）
        yield _sse("state", {
            "song": AMLL_STATE["song"],
            "progress_ms": AMLL_STATE["progress_ms"],
            "lines": AMLL_STATE["lines"]
        })
        # 然后持续推送增量
        while True:
            try:
                evt = AMLL_QUEUE.get(timeout=15)
                yield _sse(evt["type"], evt["data"])
            except queue.Empty:
                # 心跳：防止 Nginx/浏览器断流
                yield ": keep-alive\n\n"
    return Response(_gen(), mimetype="text/event-stream")

@app.route('/lyrics-amll')
def lyrics_amll_page():
    """AMLL 歌词展示页面"""
    return render_template("Lyrics-style.HTML-AMLL.HTML")

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

def _amll_publish(evt_type: str, data: dict):
    """发布事件到AMLL前端"""
    # 更新全局快照
    if evt_type == "lyrics":
        AMLL_STATE["lines"] = data.get("lines", [])
    elif evt_type == "progress":
        AMLL_STATE["progress_ms"] = int(data.get("progress_ms", 0))
    elif evt_type == "song":
        AMLL_STATE["song"] = data.get("song", {})
    AMLL_STATE["last_update"] = time.time()

    # 推送到队列
    try:
        AMLL_QUEUE.put_nowait({"type": evt_type, "data": data})
    except queue.Full:
        app.logger.warning("AMLL 队列已满，丢弃事件")

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
        for wobj in words:
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
            "isBG": bool(line.get("isBG")),
            "isDuet": bool(line.get("isDuet")),
            "translatedLyric": line.get("translatedLyric", "") or ""
        })
    return out_lines

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
                song = {
                    "musicName": info.get("musicName", ""),
                    "artists": [a.get("name", "") for a in info.get("artists", [])],
                    "duration": int(info.get("duration") or 0)
                }
                print("[WS] 歌曲元数据：", song)
                _amll_publish("song", {"song": song})
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
                    compute_disappear_times(lines_front, delta1=500, delta2=0, t_anim=700)
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