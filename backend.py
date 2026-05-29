#最终发布版本
import base64
import struct
import hashlib
import hmac
import json
import copy
from collections import deque
import functools
import bcrypt
import logging
import math
import subprocess
import os
import re
import shutil
import time
import tempfile
import atexit
import webbrowser
import sys
import xml
import uuid
import csv
import zipfile
from datetime import datetime, timedelta
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar, copy_context
from fastapi import FastAPI, Request as StarletteRequest, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse, PlainTextResponse, StreamingResponse, FileResponse, Response as StarletteResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.datastructures import QueryParams, Headers, FormData, UploadFile
from jinja2 import Environment, FileSystemLoader, select_autoescape
import aiofiles
from re import compile, Pattern, Match
from typing import Iterator, TextIO, AnyStr, Optional, Union, Set, List, Dict, Tuple, Any, Iterable
from xml.dom.minicompat import NodeList
from xml.dom import Node
from xml.dom.minidom import Document, Element
from openai import OpenAI
import random
import threading
import socket
import asyncio
import websockets
import queue
from urllib.parse import urlparse, unquote, urlencode
import requests
from PIL import Image

APP_VERSION = "0.0.0-dev"

_request_context: ContextVar[Optional["RequestContext"]] = ContextVar("request_context", default=None)
_MISSING = object()
TEMP_TTML_FILES: Dict[str, float] = {}
TEMP_TTML_TTL_SEC = 10 * 60


class FileStorageAdapter:
    """文件存储适配器类，用于适配FastAPI的UploadFile对象

    该类提供了一个统一的接口来处理文件上传，封装了FastAPI的UploadFile对象，
    使其更易于操作和使用。
    """

    def __init__(self, upload: UploadFile):
        """初始化文件存储适配器

        Args:
            upload: FastAPI的UploadFile对象，包含上传文件的所有信息
        """
        self._upload = upload
        self.filename = upload.filename or ""
        self.stream = upload.file

    def save(self, dst: Union[str, Path]) -> None:
        """保存上传的文件到指定目标位置

        该方法会将上传的文件内容保存到指定的路径。如果目标目录不存在，
        会自动创建目录结构。保存前会将文件指针重置到文件开头。

        Args:
            dst: 目标保存路径，可以是字符串或Path对象
        """
        dst_path = Path(dst)
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        self._upload.file.seek(0)
        with open(dst_path, "wb") as out_file:
            shutil.copyfileobj(self._upload.file, out_file)

    @property
    def upload(self) -> UploadFile:
        """获取原始的UploadFile对象

        Returns:
            返回被适配的原始UploadFile对象
        """
        return self._upload

    def read(self, size: int = -1) -> bytes:
        """从文件中读取指定大小的数据

        Args:
            size: 要读取的字节数，-1表示读取到文件末尾

        Returns:
            返回读取到的字节数据
        """
        return self._upload.file.read(size)

    def seek(self, offset: int, whence: int = 0) -> int:
        """移动文件指针到指定位置

        Args:
            offset: 偏移量
            whence: 参考位置（0:文件开头, 1:当前位置, 2:文件末尾）

        Returns:
            返回新的文件指针位置
        """
        return self._upload.file.seek(offset, whence)


class FilesWrapper:
    """文件包装器类，用于管理多个上传的文件

    该类提供了一个类似字典的接口来访问和管理通过表单上传的多个文件。
    每个键可以对应多个文件值，但默认访问第一个文件。
    """

    def __init__(self, mapping: Dict[str, List[FileStorageAdapter]]):
        """初始化文件包装器

        Args:
            mapping: 文件映射字典，键为字段名，值为FileStorageAdapter列表
        """
        self._mapping = mapping

    def __contains__(self, key: str) -> bool:
        """检查是否存在指定键的文件

        Args:
            key: 字段名

        Returns:
            如果存在返回True，否则返回False
        """
        return key in self._mapping

    def __getitem__(self, key: str) -> FileStorageAdapter:
        """获取指定键的第一个文件

        Args:
            key: 字段名

        Returns:
            返回该键对应的第一个FileStorageAdapter对象

        Raises:
            KeyError: 当键不存在时抛出
        """
        return self._mapping[key][0]

    def get(self, key: str, default: Any = None) -> Any:
        """安全获取指定键的第一个文件

        Args:
            key: 字段名
            default: 默认值，当键不存在时返回

        Returns:
            返回该键对应的第一个FileStorageAdapter对象或默认值
        """
        if key in self._mapping:
            return self._mapping[key][0]
        return default

    def getlist(self, key: str) -> List[FileStorageAdapter]:
        """获取指定键的所有文件

        Args:
            key: 字段名

        Returns:
            返回该键对应的所有FileStorageAdapter对象列表
        """
        return list(self._mapping.get(key, []))

    def items(self):
        """获取所有键值对（每个键只取第一个文件）

        Yields:
            生成(键, 第一个FileStorageAdapter)的元组
        """
        return ((key, values[0]) for key, values in self._mapping.items())


async def save_upload_file(upload: Union[FileStorageAdapter, UploadFile], dst: Union[str, Path]) -> None:
    """异步保存上传的文件到指定目标位置

    该函数支持FileStorageAdapter和UploadFile两种类型的上传文件对象。
    使用异步IO进行大文件写入，避免阻塞事件循环。写入完成后会重置文件指针。

    Args:
        upload: 上传的文件对象，可以是FileStorageAdapter或UploadFile
        dst: 目标保存路径，可以是字符串或Path对象
    """
    dst_path = Path(dst)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    upload_file = upload.upload if isinstance(upload, FileStorageAdapter) else upload
    if isinstance(upload_file, UploadFile):
        await upload_file.seek(0)
        async with aiofiles.open(dst_path, "wb") as out_file:
            while True:
                chunk = await upload_file.read(1024 * 1024)
                if not chunk:
                    break
                await out_file.write(chunk)
        await upload_file.seek(0)
        return
    await _run_sync_in_thread(upload.save, dst_path)


async def save_upload_file_with_meta(
    upload: Union[FileStorageAdapter, UploadFile],
    dst: Union[str, Path]
) -> Tuple[int, str]:
    """异步保存上传的文件并计算文件大小和MD5哈希值

    该函数在保存文件的同时，会计算文件的总大小和MD5哈希值。
    这对于文件完整性校验和元数据记录非常有用。

    Args:
        upload: 上传的文件对象，可以是FileStorageAdapter或UploadFile
        dst: 目标保存路径，可以是字符串或Path对象

    Returns:
        返回一个元组 (文件大小, MD5哈希值字符串)
    """
    dst_path = Path(dst)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    upload_file = upload.upload if isinstance(upload, FileStorageAdapter) else upload
    md5_hash = hashlib.md5()
    size = 0
    if isinstance(upload_file, UploadFile):
        await upload_file.seek(0)
        async with aiofiles.open(dst_path, "wb") as out_file:
            while True:
                chunk = await upload_file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                md5_hash.update(chunk)
                await out_file.write(chunk)
        await upload_file.seek(0)
        return size, md5_hash.hexdigest()

    def _sync_copy() -> Tuple[int, str]:
        """同步复制文件并计算大小和MD5

        Returns:
            返回一个元组 (文件大小, MD5哈希值字符串)
        """
        sync_size = 0
        upload.seek(0)
        with open(dst_path, "wb") as out_file:
            while True:
                chunk = upload.read(1024 * 1024)
                if not chunk:
                    break
                out_file.write(chunk)
                md5_hash.update(chunk)
                sync_size += len(chunk)
        upload.seek(0)
        return sync_size, md5_hash.hexdigest()

    return await _run_sync_in_thread(_sync_copy)


class RequestContext:
    """请求上下文类，封装HTTP请求的所有相关信息

    该类提供了一个统一的接口来访问HTTP请求的各个部分，
    包括请求体、表单数据、JSON数据、文件上传等。
    使用缓存机制提高性能。
    """

    def __init__(self, request: StarletteRequest, body: bytes, form: Optional[FormData]):
        """初始化请求上下文

        Args:
            request: Starlette的Request对象
            body: 请求体的字节数据
            form: 解析后的表单数据（如果存在）
        """
        self._request = request
        self._body = body
        self._form = form
        self._json_cache: Any = _MISSING  # 用于缓存JSON数据
        self._files_cache: Optional[FilesWrapper] = None  # 用于缓存文件包装器

    @property
    def method(self) -> str:
        """获取HTTP请求方法

        Returns:
            返回HTTP方法（GET、POST、PUT等）
        """
        return self._request.method

    @property
    def headers(self) -> Headers:
        """获取HTTP请求头

        Returns:
            返回请求头对象
        """
        return self._request.headers

    @property
    def cookies(self) -> Dict[str, str]:
        """获取所有Cookie

        Returns:
            返回Cookie字典
        """
        return dict(self._request.cookies)

    @property
    def args(self) -> QueryParams:
        """获取查询参数

        Returns:
            返回URL查询参数对象
        """
        return self._request.query_params

    @property
    def path(self) -> str:
        """获取请求路径

        Returns:
            返回URL路径部分（不包含域名和查询参数）
        """
        return self._request.url.path

    @property
    def url_root(self) -> str:
        """获取URL根路径

        Returns:
            返回完整的URL根路径（协议+域名+/）
        """
        url = self._request.url
        return f"{url.scheme}://{url.netloc}/"

    @property
    def host(self) -> str:
        """获取主机名

        Returns:
            返回请求的主机名
        """
        return self._request.headers.get("host", "")

    @property
    def remote_addr(self) -> Optional[str]:
        """获取客户端IP地址

        Returns:
            返回客户端的IP地址，如果无法获取则返回None
        """
        return self._request.client.host if self._request.client else None

    @property
    def json(self) -> Any:
        """获取JSON数据（静默模式）

        Returns:
            返回解析后的JSON对象，解析失败返回None
        """
        return self.get_json(silent=True)

    @property
    def files(self) -> FilesWrapper:
        """获取上传的文件

        使用懒加载和缓存机制，只在首次访问时解析表单中的文件。

        Returns:
            返回FilesWrapper对象，用于访问上传的文件
        """
        if self._files_cache is None:
            mapping: Dict[str, List[FileStorageAdapter]] = {}
            if self._form:
                for key, value in self._form.multi_items():
                    if isinstance(value, UploadFile):
                        mapping.setdefault(key, []).append(FileStorageAdapter(value))
            self._files_cache = FilesWrapper(mapping)
        return self._files_cache

    def get_json(self, silent: bool = False) -> Any:
        """解析并获取JSON数据

        使用缓存机制，避免重复解析。支持静默模式处理解析错误。

        Args:
            silent: 是否在解析失败时静默返回None而不是抛出异常

        Returns:
            返回解析后的JSON对象，失败时根据silent参数决定返回None或抛出异常

        Raises:
            Exception: 当silent为False且JSON解析失败时抛出
        """
        if self._json_cache is not _MISSING:
            return self._json_cache
        if not self._body:
            self._json_cache = None
            return None
        try:
            self._json_cache = json.loads(self._body.decode("utf-8"))
        except Exception:
            if silent:
                self._json_cache = None
                return None
            raise
        return self._json_cache


class RequestProxy:
    """请求代理类，用于访问当前请求上下文

    该类提供了一个全局的访问点来获取当前请求的属性和方法。
    通过__getattr__魔法方法，将所有属性访问委托给当前的RequestContext对象。
    """

    def _require_context(self) -> RequestContext:
        """确保请求上下文存在

        获取当前的请求上下文，如果不存在则抛出异常。

        Returns:
            返回当前的RequestContext对象

        Raises:
            RuntimeError: 当请求上下文不可用时抛出
        """
        ctx = _request_context.get()
        if ctx is None:
            raise RuntimeError("Request context is not available.")
        return ctx

    def __getattr__(self, name: str) -> Any:
        """动态获取请求上下文的属性

        将所有未定义的属性访问委托给当前的RequestContext对象。

        Args:
            name: 属性名

        Returns:
            返回RequestContext对象对应属性的值
        """
        return getattr(self._require_context(), name)


class SessionProxy:
    """会话代理类，用于管理用户会话数据

    该类提供了一个类似字典的接口来访问和管理会话数据。
    会自动处理会话不存在的情况，返回空字典而不是抛出异常。
    """

    def _get_session(self) -> Dict[str, Any]:
        """获取会话字典

        安全地获取当前请求的会话字典，如果会话不存在则返回空字典。

        Returns:
            返回会话字典，如果不存在则返回空字典
        """
        ctx = _request_context.get()
        if ctx is None or not hasattr(ctx._request, "session"):
            return {}
        return ctx._request.session

    def __getitem__(self, key: str) -> Any:
        """获取会话中的值

        Args:
            key: 键名

        Returns:
            返回对应的值

        Raises:
            KeyError: 当键不存在时抛出
        """
        return self._get_session()[key]

    def __setitem__(self, key: str, value: Any) -> None:
        """设置会话中的值

        Args:
            key: 键名
            value: 要设置的值
        """
        self._get_session()[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        """安全获取会话中的值

        Args:
            key: 键名
            default: 默认值，当键不存在时返回

        Returns:
            返回对应的值或默认值
        """
        return self._get_session().get(key, default)

    def pop(self, key: str, default: Any = None) -> Any:
        """弹出并删除会话中的值

        Args:
            key: 键名
            default: 默认值，当键不存在时返回

        Returns:
            返回被弹出的值或默认值
        """
        return self._get_session().pop(key, default)

    def clear(self) -> None:
        """清空会话中的所有数据"""
        self._get_session().clear()

    def __contains__(self, key: str) -> bool:
        """检查会话中是否存在指定键

        Args:
            key: 键名

        Returns:
            如果存在返回True，否则返回False
        """
        return key in self._get_session()


def has_request_context() -> bool:
    """检查是否存在请求上下文

    Returns:
        如果存在请求上下文返回True，否则返回False
    """
    return _request_context.get() is not None


def stream_with_context(generator):
    """在请求上下文中流式传输生成器

    该函数保持生成器在当前的请求上下文中执行。

    Args:
        generator: 生成器对象

    Returns:
        返回原始的生成器对象
    """
    return generator


def abort(code: int):
    """中止请求并返回HTTP错误

    立即抛出HTTPException，中断当前请求的处理。

    Args:
        code: HTTP状态码

    Raises:
        HTTPException: 总是抛出，包含指定的状态码
    """
    raise HTTPException(status_code=code)


def jsonify(payload: Any = None, **kwargs: Any) -> JSONResponse:
    """创建JSON响应

    将Python对象转换为JSON响应。支持灵活的参数组合。

    Args:
        payload: 主要数据，可以是None
        **kwargs: 额外的数据字段

    Returns:
        返回JSONResponse对象
    """
    if payload is None:
        payload = kwargs
    elif kwargs:
        if isinstance(payload, dict):
            payload = {**payload, **kwargs}
        else:
            payload = {"data": payload, **kwargs}
    return JSONResponse(content=payload)


def _coerce_response(payload: Any) -> StarletteResponse:
    """将各种类型的响应转换为标准的Starlette响应对象

    该函数是一个响应转换器，能够将Python的各种数据类型转换为
    Starlette框架可以处理的响应对象。

    Args:
        payload: 要转换的响应数据，可以是任意类型

    Returns:
        返回转换后的StarletteResponse对象
    """
    if isinstance(payload, StarletteResponse):
        return payload
    if isinstance(payload, (dict, list)):
        return JSONResponse(content=payload)
    if isinstance(payload, (bytes, bytearray)):
        return StarletteResponse(content=payload)
    if payload is None:
        return StarletteResponse(content=b"")
    return PlainTextResponse(str(payload))


def _normalize_response(result: Any) -> StarletteResponse:
    """规范化响应结果，支持多种返回格式

    该函数处理Flask风格的返回值，支持直接返回响应对象、
    或者返回包含响应体、状态码和头的元组。

    Args:
        result: 路由函数的返回值，可以是以下格式：
            - StarletteResponse对象：直接返回
            - 元组：(body, status_code, headers)
            - 其他类型：通过_coerce_response转换

    Returns:
        返回规范化的StarletteResponse对象
    """
    if isinstance(result, StarletteResponse):
        return result
    if isinstance(result, tuple):
        body = result[0] if len(result) > 0 else None
        status_code = result[1] if len(result) > 1 else None
        headers = result[2] if len(result) > 2 else None
        response = _coerce_response(body)
        if status_code is not None:
            response.status_code = status_code
        if headers:
            response.headers.update(headers)
        return response
    return _coerce_response(result)


def send_file(
    path: Union[str, Path],
    as_attachment: bool = False,
    download_name: Optional[str] = None,
    mimetype: Optional[str] = None
) -> StarletteResponse:
    """发送文件给客户端，支持多种文件格式和下载方式

    该函数可以发送本地文件、内存中的字节数据或文件对象。
    支持以附件形式下载或直接在浏览器中显示。

    Args:
        path: 要发送的文件路径或文件对象，可以是：
            - 字符串或Path对象：本地文件路径
            - BytesIO/bytes/bytearray：内存中的字节数据
            - 任何有read()方法的对象：文件类对象
        as_attachment: 是否作为附件下载，默认为False
        download_name: 下载时的文件名，如果未提供则使用原文件名
        mimetype: 文件的MIME类型，如果未提供则自动检测

    Returns:
        返回包含文件内容的StarletteResponse对象

    Raises:
        HTTPException: 当文件不存在时抛出404错误
    """
    if isinstance(path, (BytesIO, bytearray, bytes)) or hasattr(path, "read"):
        file_obj = path
        if isinstance(file_obj, BytesIO):
            file_obj.seek(0)
        if isinstance(file_obj, (bytes, bytearray)):
            content = bytes(file_obj)
            response = StarletteResponse(content=content, media_type=mimetype)
        else:
            def _iter_file():
                while True:
                    chunk = file_obj.read(1024 * 1024)
                    if not chunk:
                        break
                    yield chunk
            response = StreamingResponse(_iter_file(), media_type=mimetype)
    else:
        target = Path(path)
        if not target.exists():
            raise HTTPException(status_code=404)
        response = FileResponse(target, media_type=mimetype)
    if as_attachment:
        filename = download_name or (Path(path).name if not hasattr(path, "read") else "download")
        response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def send_from_directory(directory: Union[str, Path], filename: str, mimetype: Optional[str] = None) -> StarletteResponse:
    """从指定目录发送文件，防止路径遍历攻击

    该函数安全地从指定目录发送文件，通过验证文件路径确保
    无法访问目录外的文件，防止路径遍历攻击。

    Args:
        directory: 要发送文件的根目录
        filename: 要发送的文件名
        mimetype: 文件的MIME类型，如果未提供则自动检测

    Returns:
        返回包含文件内容的FileResponse对象

    Raises:
        HTTPException: 当文件路径不合法或文件不存在时抛出404错误
    """
    directory_path = Path(directory).resolve()
    target = (directory_path / filename).resolve()
    try:
        target.relative_to(directory_path)
    except ValueError:
        raise HTTPException(status_code=404)
    if not target.exists():
        raise HTTPException(status_code=404)
    return FileResponse(target, media_type=mimetype)


async def _run_sync_in_thread(func, **kwargs):
    """在线程池中运行同步函数，保持上下文变量

    该函数将同步函数放到线程池中执行，避免阻塞事件循环。
    同时保持当前上下文变量，确保请求上下文等数据可用。

    Args:
        func: 要执行的同步函数
        **kwargs: 传递给函数的关键字参数

    Returns:
        返回函数执行的结果
    """
    ctx = copy_context()
    bound = functools.partial(func, **kwargs)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(THREADPOOL_EXECUTOR, ctx.run, bound)


class FlaskCompat(FastAPI):
    """Flask兼容性包装器，提供Flask风格的API

    该类继承自FastAPI，但提供了Flask风格的接口和装饰器，
    使得从Flask迁移到FastAPI更加容易。
    """

    def __init__(self, import_name: str, static_folder: str, template_folder: str, static_url_path: str):
        """初始化Flask兼容性应用

        Args:
            import_name: 应用名称，类似Flask的import_name
            static_folder: 静态文件目录路径
            template_folder: 模板文件目录路径
            static_url_path: 静态文件的URL路径前缀
        """
        super().__init__()
        self.import_name = import_name
        self.static_folder = static_folder
        self.template_folder = template_folder
        self.static_url_path = static_url_path or ""
        self.config: Dict[str, Any] = {}
        self.debug = False
        self.logger = logging.getLogger("famyliam.backend")
        self._before_request_funcs: List[Any] = []
        self._after_request_funcs: List[Any] = []
        self.jinja_env = Environment(
            loader=FileSystemLoader(self.template_folder),
            autoescape=select_autoescape(["html", "xml", "HTML"])
        )

    def route(self, path: str, methods: Optional[List[str]] = None, **kwargs):
        methods = methods or ["GET"]
        converted_path = re.sub(
            r"<(?:(\w+):)?(\w+)>",
            lambda m: f"{{{m.group(2)}:path}}" if m.group(1) == "path" else f"{{{m.group(2)}}}",
            path
        )

        def decorator(func):
            if not hasattr(func, "_fastapi_endpoint"):
                async def endpoint(request: StarletteRequest):
                    if asyncio.iscoroutinefunction(func):
                        result = await func(**request.path_params)
                    else:
                        result = await _run_sync_in_thread(func, **request.path_params)
                    return _normalize_response(result)

                func._fastapi_endpoint = endpoint
            self.add_api_route(converted_path, func._fastapi_endpoint, methods=methods, **kwargs)
            return func

        return decorator

    def before_request(self, func):
        self._before_request_funcs.append(func)
        return func

    def after_request(self, func):
        self._after_request_funcs.append(func)
        return func

    def template_filter(self, name: Optional[str] = None):
        def decorator(func):
            filter_name = name or func.__name__
            self.jinja_env.filters[filter_name] = func
            return func
        return decorator

    def make_response(self, content: Any, status: int = 200, mimetype: Optional[str] = None) -> StarletteResponse:
        return StarletteResponse(content=content, status_code=status, media_type=mimetype)


request = RequestProxy()
session = SessionProxy()
Response = StarletteResponse


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

# FastAPI compatibility config (dynamic paths)
app = FlaskCompat(
    __name__,
    static_folder=str(BASE_PATH / 'static'),
    template_folder=str(BASE_PATH / 'templates'),
    static_url_path=''
)
app.jinja_env.filters['tojson'] = json.dumps
app.secret_key = 'your_random_secret_key'
app.add_middleware(SessionMiddleware, secret_key=app.secret_key)

# 添加Jinja2全局过滤器
app.jinja_env.globals.update(tojson=json.dumps)


def url_for(endpoint: str, **values: Any) -> str:
    if endpoint == "static":
        filename = values.get("filename", "")
        if app.static_url_path:
            return f"{app.static_url_path.rstrip('/')}/{str(filename).lstrip('/')}"
        return f"/{str(filename).lstrip('/')}"
    return f"/{endpoint}"


app.jinja_env.globals.update(url_for=url_for)

_updater_started = False
UPDATER_STATUS_FILE = BASE_PATH / '.updater.status.json'
UPDATER_RUNTIME_FILE = BASE_PATH / '.updater.runtime.json'
UPDATER_GITHUB_REPO = os.getenv('UPDATER_GITHUB_REPO', 'HKLHaoBin/LyricSphere')
UPDATER_RELEASE_LATEST_API = 'https://api.github.com/repos/{repo}/releases/latest'


def launch_updater_sidecar(port: int) -> None:
    """Start updater.exe in frozen builds. Version checks use GitHub REST API (requests), not git CLI."""
    global _updater_started
    if not getattr(sys, 'frozen', False):
        app.logger.info('Source mode detected; built-in updater is disabled. Please update via git.')
        return

    backend_pid = os.getpid()
    backend_mode = 'exe' if getattr(sys, 'frozen', False) else 'python'
    backend_executable_path = str(Path(sys.executable).resolve()) if backend_mode == 'exe' else ''
    backend_script_path = '' if backend_mode == 'exe' else str(Path(__file__).resolve())
    python_executable_path = '' if backend_mode == 'exe' else str(Path(sys.executable).resolve())

    runtime_payload = {
        'timestamp': datetime.now().isoformat(timespec='seconds'),
        'app_version': APP_VERSION,
        'backend_pid': backend_pid,
        'port': port,
        'backend_mode': backend_mode,
        'backend_executable': backend_executable_path,
        'backend_script': backend_script_path,
        'python_executable': python_executable_path,
    }
    try:
        UPDATER_RUNTIME_FILE.write_text(json.dumps(runtime_payload, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception as exc:
        app.logger.warning('Failed to write updater runtime file: %s', exc)

    if _updater_started:
        return

    updater_exe = BASE_PATH / 'updater.exe'
    updater_py = BASE_PATH / 'updater.py'

    sidecar_args = [
        '--watch',
        '--work-dir', str(BASE_PATH),
        '--backend-pid', str(backend_pid),
        '--port', str(port),
        '--backend-mode', backend_mode,
    ]
    if backend_mode == 'exe':
        sidecar_args += ['--backend-executable', backend_executable_path]
    else:
        sidecar_args += [
            '--backend-script', backend_script_path,
            '--python-executable', python_executable_path,
        ]

    if updater_exe.exists():
        command = [str(updater_exe), *sidecar_args]
    elif updater_py.exists():
        command = [str(Path(sys.executable).resolve()), str(updater_py), *sidecar_args]
    else:
        app.logger.info('Updater sidecar not found, skip launch.')
        return

    updater_stdout_log = BASE_PATH / 'updater-debug.log'
    updater_stderr_log = BASE_PATH / 'updater-error.log'
    stdout_handle: Optional[TextIO] = None
    stderr_handle: Optional[TextIO] = None
    try:
        stdout_handle = open(updater_stdout_log, 'a', encoding='utf-8')
        stderr_handle = open(updater_stderr_log, 'a', encoding='utf-8')
    except Exception as exc:
        app.logger.warning('Failed to open updater log files: %s', exc)

    kwargs: Dict[str, Any] = {
        'cwd': str(BASE_PATH),
        'stdout': stdout_handle if stdout_handle else subprocess.DEVNULL,
        'stderr': stderr_handle if stderr_handle else subprocess.DEVNULL,
        'stdin': subprocess.DEVNULL,
        'close_fds': True,
    }
    if os.name == 'nt':
        kwargs['creationflags'] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs['start_new_session'] = True

    try:
        subprocess.Popen(command, **kwargs)
        _updater_started = True
        app.logger.info('Updater sidecar launched: %s', command[0])
    except Exception as exc:
        app.logger.warning('Failed to launch updater sidecar: %s', exc)
    finally:
        try:
            if stdout_handle:
                stdout_handle.close()
        except Exception:
            pass
        try:
            if stderr_handle:
                stderr_handle.close()
        except Exception:
            pass


@app.route('/api/runtime/version', methods=['GET'])
def api_runtime_version():
    updater_status = None
    if UPDATER_STATUS_FILE.exists():
        try:
            updater_status = json.loads(UPDATER_STATUS_FILE.read_text(encoding='utf-8'))
        except Exception as exc:
            updater_status = {'state': 'error', 'message': f'parse failed: {exc}'}
    return {
        'app_version': APP_VERSION,
        'backend_pid': os.getpid(),
        'updater_status': updater_status,
    }


def _parse_updater_release_summary(updater_status: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    summary: Dict[str, Any] = {
        'latest_tag': '',
        'release_title': '',
        'release_body': '',
        'published_at': '',
    }
    if not isinstance(updater_status, dict):
        return summary

    extra = updater_status.get('extra') if isinstance(updater_status.get('extra'), dict) else {}
    summary['latest_tag'] = str(
        extra.get('latest_tag')
        or updater_status.get('latest_tag')
        or updater_status.get('tag')
        or ''
    )
    summary['release_title'] = str(extra.get('release_title') or updater_status.get('release_title') or '')
    summary['release_body'] = str(extra.get('release_body') or updater_status.get('release_body') or '')
    summary['published_at'] = str(extra.get('release_published_at') or updater_status.get('published_at') or '')
    return summary


def _fetch_latest_release_notes(repo: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    api_url = UPDATER_RELEASE_LATEST_API.format(repo=repo)
    headers = {
        'Accept': 'application/vnd.github+json',
        'User-Agent': 'Famyliam-Backend',
    }
    try:
        response = requests.get(api_url, headers=headers, timeout=8)
        response.raise_for_status()
        payload = response.json()
        return {
            'latest_tag': str(payload.get('tag_name') or ''),
            'release_title': str(payload.get('name') or ''),
            'release_body': str(payload.get('body') or ''),
            'published_at': str(payload.get('published_at') or ''),
        }, None
    except Exception as exc:
        return None, str(exc)


@app.route('/api/runtime/release-notes', methods=['GET'])
def api_runtime_release_notes():
    repo = str(request.args.get('repo') or UPDATER_GITHUB_REPO)
    updater_status = None
    if UPDATER_STATUS_FILE.exists():
        try:
            updater_status = json.loads(UPDATER_STATUS_FILE.read_text(encoding='utf-8'))
        except Exception:
            updater_status = None

    cached = _parse_updater_release_summary(updater_status)
    remote, remote_error = _fetch_latest_release_notes(repo)
    release_info = remote if remote else cached

    return jsonify({
        'current_version': APP_VERSION,
        'repo': repo,
        'latest_tag': release_info.get('latest_tag', ''),
        'release_title': release_info.get('release_title', ''),
        'release_body': release_info.get('release_body', ''),
        'published_at': release_info.get('published_at', ''),
        'source': 'github' if remote else 'updater_status',
        'remote_error': remote_error if remote_error else '',
    })


def render_template(template_name: str, **context: Any) -> HTMLResponse:
    template = app.jinja_env.get_template(template_name)
    return HTMLResponse(template.render(**context))


THREADPOOL_MAX_WORKERS = int(os.getenv("APP_THREADPOOL_WORKERS", "16"))
THREADPOOL_EXECUTOR = ThreadPoolExecutor(max_workers=THREADPOOL_MAX_WORKERS)
BEAT_CURVE_TASKS: Dict[str, Dict[str, Any]] = {}
BEAT_CURVE_LOCK = threading.Lock()
STATIC_EXPORT_TASKS: Dict[str, Dict[str, Any]] = {}
STATIC_EXPORT_LOCK = threading.Lock()

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


_REASONING_SCHEMA_PROVIDER_DEFINED_MSG = (
    '当前 provider 未确认可用的显式思考开关（模型行为可能由服务商或模型自身决定）'
)
_REASONING_SCHEMA_UNKNOWN_MSG = '当前 provider/model 未确认支持显式思考控制（未在白名单中）'

# guarantee_level: strong = explicit field mapping; conditional = partial/model guards; fallback = openrouter_compat
_REASONING_SCHEMA_META: Dict[str, Dict[str, Any]] = {
    'openai_native': {
        'supported': True, 'status': 'confirmed', 'message': '', 'control_field_sent': True,
        'guarantee_level': 'strong',
    },
    'openrouter': {
        'supported': True, 'status': 'confirmed', 'message': '', 'control_field_sent': True,
        'guarantee_level': 'strong',
    },
    'volcengine': {
        'supported': True, 'status': 'confirmed', 'message': '', 'control_field_sent': True,
        'guarantee_level': 'strong',
    },
    'siliconflow': {
        'supported': True, 'status': 'confirmed', 'message': '', 'control_field_sent': True,
        'guarantee_level': 'strong',
    },
    'deepseek': {
        'supported': True, 'status': 'confirmed', 'message': '', 'control_field_sent': True,
        'guarantee_level': 'strong',
    },
    'dashscope': {
        'supported': True, 'status': 'confirmed', 'message': '', 'control_field_sent': True,
        'guarantee_level': 'strong',
    },
    'anthropic': {
        'supported': False, 'status': 'provider_defined',
        'message': _REASONING_SCHEMA_PROVIDER_DEFINED_MSG, 'control_field_sent': True,
        'guarantee_level': 'conditional',
    },
    'gemini': {
        'supported': False, 'status': 'provider_defined',
        'message': _REASONING_SCHEMA_PROVIDER_DEFINED_MSG, 'control_field_sent': True,
        'guarantee_level': 'conditional',
    },
    'groq': {
        'supported': True, 'status': 'confirmed', 'message': '', 'control_field_sent': True,
        'guarantee_level': 'strong',
    },
    'together': {
        'supported': False, 'status': 'provider_defined',
        'message': _REASONING_SCHEMA_PROVIDER_DEFINED_MSG, 'control_field_sent': True,
        'guarantee_level': 'conditional',
    },
    'cerebras': {
        'supported': True, 'status': 'confirmed', 'message': '', 'control_field_sent': True,
        'guarantee_level': 'strong',
    },
    'minimax': {
        'supported': False, 'status': 'provider_defined',
        'message': _REASONING_SCHEMA_PROVIDER_DEFINED_MSG, 'control_field_sent': True,
        'guarantee_level': 'conditional',
    },
    'zhipu': {
        'supported': False, 'status': 'provider_defined',
        'message': _REASONING_SCHEMA_PROVIDER_DEFINED_MSG, 'control_field_sent': True,
        'guarantee_level': 'conditional',
    },
    'kimi': {
        'supported': False, 'status': 'provider_defined',
        'message': _REASONING_SCHEMA_PROVIDER_DEFINED_MSG, 'control_field_sent': True,
        'guarantee_level': 'conditional',
    },
    'openrouter_compat': {
        'supported': False, 'status': 'unknown',
        'message': _REASONING_SCHEMA_UNKNOWN_MSG, 'control_field_sent': True,
        'guarantee_level': 'fallback',
    },
}


def _deepseek_uses_full_reasoning_control(model: str) -> bool:
    """True when model is known to accept reasoning_effort + thinking.type together."""
    model_norm = str(model or '').strip().lower()
    if not model_norm:
        return False
    if 'reasoner' in model_norm:
        return True
    if model_norm.startswith('deepseek-v4'):
        return True
    if model_norm in ('deepseek-r1', 'deepseek-reasoner'):
        return True
    if 'deepseek-chat' in model_norm and 'thinking' in model_norm:
        return True
    return False


def _deepseek_is_v4_family(model_norm: str) -> bool:
    return bool(model_norm) and (
        model_norm.startswith('deepseek-v4')
        or model_norm.startswith('deepseek_v4')
    )


def _gemini_uses_thinking_level(model_norm: str) -> bool:
    """Gemini 3.x uses thinkingLevel; 2.5 uses thinkingBudget (must not mix)."""
    if not model_norm:
        return False
    if 'gemini-3' in model_norm or model_norm.startswith('gemini-3'):
        return True
    if 'gemini-3.' in model_norm:
        return True
    return False


def _recognize_openai_native(provider_norm: str, base_url_norm: str, model_norm: str) -> bool:
    return 'api.openai.com' in base_url_norm


def _recognize_openrouter(provider_norm: str, base_url_norm: str, model_norm: str) -> bool:
    return 'openrouter.ai' in base_url_norm or provider_norm == 'openrouter'


def _recognize_volcengine(provider_norm: str, base_url_norm: str, model_norm: str) -> bool:
    return 'volces.com' in base_url_norm or provider_norm == 'volcengine'


def _recognize_siliconflow(provider_norm: str, base_url_norm: str, model_norm: str) -> bool:
    if 'siliconflow.cn' in base_url_norm or 'siliconflow.com' in base_url_norm:
        return True
    return provider_norm in ('siliconflow', 'siliconflow_cn')


def _recognize_deepseek(provider_norm: str, base_url_norm: str, model_norm: str) -> bool:
    return 'api.deepseek.com' in base_url_norm or provider_norm == 'deepseek'


def _recognize_dashscope(provider_norm: str, base_url_norm: str, model_norm: str) -> bool:
    dashscope_url_tokens = (
        'dashscope.aliyuncs.com',
        'dashscope-intl.aliyuncs.com',
        'dashscope-us.aliyuncs.com',
    )
    if any(token in base_url_norm for token in dashscope_url_tokens):
        return True
    if 'aliyuncs.com' in base_url_norm and 'dashscope' in base_url_norm:
        return True
    return provider_norm in ('alibaba', 'alibaba_cn', 'dashscope', 'modelscope', 'qwen')


def _recognize_anthropic(provider_norm: str, base_url_norm: str, model_norm: str) -> bool:
    if 'api.anthropic.com' in base_url_norm or 'anthropic.com/v1' in base_url_norm:
        return True
    if provider_norm in ('anthropic', 'claude', 'claudinio'):
        return True
    if 'bedrock' in base_url_norm or provider_norm in ('bedrock', 'amazon_bedrock'):
        return 'claude' in model_norm or 'anthropic' in base_url_norm
    if ('vertex' in base_url_norm or provider_norm == 'vertex') and (
        'claude' in model_norm or 'anthropic' in base_url_norm or provider_norm == 'vertex_anthropic'
    ):
        return True
    return False


def _recognize_gemini(provider_norm: str, base_url_norm: str, model_norm: str) -> bool:
    if _recognize_anthropic(provider_norm, base_url_norm, model_norm):
        return False
    gemini_url_tokens = (
        'generativelanguage.googleapis.com',
        'googleapis.com/v1beta',
        'aiplatform.googleapis.com',
    )
    if any(token in base_url_norm for token in gemini_url_tokens):
        return True
    if 'googleapis.com' in base_url_norm and ('gemini' in base_url_norm or 'generatecontent' in base_url_norm):
        return True
    if provider_norm in ('google', 'gemini', 'vertex_gemini'):
        return True
    if ('vertex' in base_url_norm or provider_norm == 'vertex') and 'gemini' in model_norm:
        return True
    if 'gemini' in model_norm and provider_norm in ('google', 'gemini', 'vertex', ''):
        return True
    return False


def _recognize_groq(provider_norm: str, base_url_norm: str, model_norm: str) -> bool:
    return 'groq.com' in base_url_norm or provider_norm == 'groq'


def _recognize_together(provider_norm: str, base_url_norm: str, model_norm: str) -> bool:
    return 'together.xyz' in base_url_norm or 'together.ai' in base_url_norm or provider_norm == 'together'


def _recognize_cerebras(provider_norm: str, base_url_norm: str, model_norm: str) -> bool:
    return 'cerebras.ai' in base_url_norm or provider_norm == 'cerebras'


def _recognize_minimax(provider_norm: str, base_url_norm: str, model_norm: str) -> bool:
    if any(token in base_url_norm for token in ('minimax.io', 'minimaxi.com', 'minimax.chat')):
        return True
    return provider_norm in ('minimax', 'minimax_cn')


def _recognize_zhipu(provider_norm: str, base_url_norm: str, model_norm: str) -> bool:
    if any(token in base_url_norm for token in ('open.bigmodel.cn', 'api.z.ai', 'bigmodel.cn')):
        return True
    return provider_norm in ('zhipu', 'z.ai', 'zai')


def _recognize_kimi(provider_norm: str, base_url_norm: str, model_norm: str) -> bool:
    if 'api.moonshot.ai' in base_url_norm or 'api.moonshot.cn' in base_url_norm:
        return True
    return provider_norm in ('kimi', 'moonshot', 'moonshot_cn')


def _build_openai_native_options(on: bool, model_norm: str) -> Dict[str, Any]:
    return {'reasoning_effort': 'medium' if on else 'none'}


def _build_openrouter_options(on: bool, model_norm: str) -> Dict[str, Any]:
    effort = 'medium' if on else 'none'
    return {'extra_body': {'reasoning': {'effort': effort}}}


def _build_volcengine_options(on: bool, model_norm: str) -> Dict[str, Any]:
    opts: Dict[str, Any] = {
        'extra_body': {'thinking': {'type': 'enabled' if on else 'disabled'}},
    }
    if on and model_norm.startswith('doubao-seed-2'):
        opts['reasoning_effort'] = 'medium'
    return opts


def _build_siliconflow_options(on: bool, model_norm: str) -> Dict[str, Any]:
    return {'extra_body': {'thinking_budget': 4096 if on else 0}}


def _build_deepseek_options(on: bool, model_norm: str) -> Dict[str, Any]:
    thinking_type = 'enabled' if on else 'disabled'
    if _deepseek_is_v4_family(model_norm):
        if on:
            return {
                'reasoning_effort': 'high',
                'extra_body': {'thinking': {'type': thinking_type}},
            }
        return {'extra_body': {'thinking': {'type': thinking_type}}}
    if _deepseek_uses_full_reasoning_control(model_norm):
        return {
            'reasoning_effort': 'high' if on else 'none',
            'extra_body': {'thinking': {'type': thinking_type}},
        }
    return {'extra_body': {'thinking': {'type': thinking_type}}}


def _build_dashscope_options(on: bool, model_norm: str) -> Dict[str, Any]:
    return {
        'extra_body': {
            'enable_thinking': on,
            'thinking_budget': 4096 if on else 0,
        },
    }


def _build_anthropic_options(on: bool, model_norm: str) -> Dict[str, Any]:
    return {'extra_body': {'thinking': {'type': 'enabled' if on else 'disabled'}}}


def _build_gemini_options(on: bool, model_norm: str) -> Dict[str, Any]:
    if _gemini_uses_thinking_level(model_norm):
        level = 'medium' if on else 'minimal'
        return {'extra_body': {'thinkingLevel': level}}
    budget = 4096 if on else 0
    return {'extra_body': {'thinkingBudget': budget}}


def _build_groq_options(on: bool, model_norm: str) -> Dict[str, Any]:
    if not on:
        if 'qwen' in model_norm:
            return {'reasoning_effort': 'none', 'reasoning_format': 'none'}
        return {'reasoning_effort': 'none'}
    opts: Dict[str, Any] = {'reasoning_effort': 'medium'}
    if 'qwen' in model_norm:
        opts['reasoning_format'] = 'default'
    elif 'gpt-oss' in model_norm or 'openai/' in model_norm:
        opts['reasoning_format'] = 'parsed'
    return opts


def _build_together_options(on: bool, model_norm: str) -> Dict[str, Any]:
    if 'gpt-oss' in model_norm or model_norm.startswith('openai/'):
        return {'reasoning_effort': 'medium' if on else 'none'}
    if on:
        return {'extra_body': {'reasoning': {'enabled': True}}}
    return {'extra_body': {'reasoning': {'enabled': False}}}


def _build_cerebras_options(on: bool, model_norm: str) -> Dict[str, Any]:
    return {'reasoning_effort': 'medium' if on else 'none'}


def _build_minimax_options(on: bool, model_norm: str) -> Dict[str, Any]:
    if on:
        return {
            'extra_body': {
                'reasoning_split': True,
                'thinking': {'type': 'enabled'},
            },
        }
    return {'extra_body': {'thinking': {'type': 'disabled'}}}


def _build_zhipu_kimi_options(on: bool, model_norm: str) -> Dict[str, Any]:
    return {'extra_body': {'thinking': {'type': 'enabled' if on else 'disabled'}}}


class _ReasoningSchemaRegistryEntry:
    __slots__ = ('schema_id', 'recognize', 'build_options')

    def __init__(
        self,
        schema_id: str,
        recognize: Any,
        build_options: Any,
    ) -> None:
        self.schema_id = schema_id
        self.recognize = recognize
        self.build_options = build_options


_REASONING_SCHEMA_REGISTRY: List[_ReasoningSchemaRegistryEntry] = [
    _ReasoningSchemaRegistryEntry('openai_native', _recognize_openai_native, _build_openai_native_options),
    _ReasoningSchemaRegistryEntry('openrouter', _recognize_openrouter, _build_openrouter_options),
    _ReasoningSchemaRegistryEntry('volcengine', _recognize_volcengine, _build_volcengine_options),
    _ReasoningSchemaRegistryEntry('siliconflow', _recognize_siliconflow, _build_siliconflow_options),
    _ReasoningSchemaRegistryEntry('deepseek', _recognize_deepseek, _build_deepseek_options),
    _ReasoningSchemaRegistryEntry('dashscope', _recognize_dashscope, _build_dashscope_options),
    _ReasoningSchemaRegistryEntry('anthropic', _recognize_anthropic, _build_anthropic_options),
    _ReasoningSchemaRegistryEntry('gemini', _recognize_gemini, _build_gemini_options),
    _ReasoningSchemaRegistryEntry('groq', _recognize_groq, _build_groq_options),
    _ReasoningSchemaRegistryEntry('together', _recognize_together, _build_together_options),
    _ReasoningSchemaRegistryEntry('cerebras', _recognize_cerebras, _build_cerebras_options),
    _ReasoningSchemaRegistryEntry('minimax', _recognize_minimax, _build_minimax_options),
    _ReasoningSchemaRegistryEntry('zhipu', _recognize_zhipu, _build_zhipu_kimi_options),
    _ReasoningSchemaRegistryEntry('kimi', _recognize_kimi, _build_zhipu_kimi_options),
]

_REASONING_SCHEMA_BUILDERS: Dict[str, Any] = {
    'openai_native': _build_openai_native_options,
    'openrouter': _build_openrouter_options,
    'openrouter_compat': _build_openrouter_options,
    'volcengine': _build_volcengine_options,
    'siliconflow': _build_siliconflow_options,
    'deepseek': _build_deepseek_options,
    'dashscope': _build_dashscope_options,
    'anthropic': _build_anthropic_options,
    'gemini': _build_gemini_options,
    'groq': _build_groq_options,
    'together': _build_together_options,
    'cerebras': _build_cerebras_options,
    'minimax': _build_minimax_options,
    'zhipu': _build_zhipu_kimi_options,
    'kimi': _build_zhipu_kimi_options,
}


def resolve_reasoning_schema(provider: str, base_url: str, model: str = '') -> str:
    """
    Resolve reasoning control schema from base_url (first), then provider, then fallback.
    Priority: registry recognition order > openrouter_compat.
    """
    provider_norm = str(provider or '').strip().lower()
    base_url_norm = str(base_url or '').strip().lower()
    model_norm = str(model or '').strip().lower()

    for entry in _REASONING_SCHEMA_REGISTRY:
        if entry.recognize(provider_norm, base_url_norm, model_norm):
            return entry.schema_id

    return 'openrouter_compat'


def build_schema_reasoning_options(schema: str, expect_reasoning: bool, model: str = '') -> Dict[str, Any]:
    """Build chat.completions.create() kwargs for the given reasoning schema."""
    on = bool(expect_reasoning)
    model_norm = str(model or '').strip().lower()
    builder = _REASONING_SCHEMA_BUILDERS.get(schema)
    if builder is not None:
        return builder(on, model_norm)
    return {}


def get_reasoning_control_capability(provider: str, base_url: str, model: str) -> Dict[str, Any]:
    """
    Returns capability information for explicit reasoning control.
    - supported: whether we can explicitly control reasoning via request fields
    - user_selectable: whether UI should allow user to choose expectation
    - control_field_sent: whether backend will attempt to send reasoning control fields
    - status: confirmed | unknown | provider_defined
    - guarantee_level: strong | conditional | fallback
    - options_for(expect_reasoning): returns kwargs to pass into chat.completions.create()
    """
    provider_norm = str(provider or '').strip().lower()
    schema = resolve_reasoning_schema(provider=provider, base_url=base_url, model=model)
    meta = _REASONING_SCHEMA_META.get(schema, _REASONING_SCHEMA_META['openrouter_compat'])
    supported = bool(meta.get('supported', False))
    status = str(meta.get('status') or 'unknown')
    message = str(meta.get('message') or '')
    control_field_sent = bool(meta.get('control_field_sent', False))
    guarantee_level = str(meta.get('guarantee_level') or 'fallback')
    if schema == 'deepseek' and not _deepseek_uses_full_reasoning_control(model):
        supported = False
        status = 'provider_defined'
        message = _REASONING_SCHEMA_PROVIDER_DEFINED_MSG
        guarantee_level = 'conditional'

    def _options_for(expect_reasoning: bool) -> Dict[str, Any]:
        return build_schema_reasoning_options(schema, expect_reasoning, model=model)

    return {
        'supported': supported,
        'user_selectable': True,
        'control_field_sent': control_field_sent,
        'status': status,
        'guarantee_level': guarantee_level,
        'provider': provider_norm or schema,
        'schema': schema,
        'message': message,
        'options_for': _options_for,
    }


def build_reasoning_request_options(provider: str, base_url: str, model: str, expect_reasoning: bool) -> Dict[str, Any]:
    """
    Backward-compatible wrapper: returns kwargs for upstream request.
    This does NOT raise; callers should enforce support when they must guarantee behavior.
    """
    cap = get_reasoning_control_capability(provider=provider, base_url=base_url, model=model)
    if not cap.get('control_field_sent', False):
        return {}
    return cap['options_for'](bool(expect_reasoning))

# 所有路径定义使用绝对路径
STATIC_DIR = BASE_PATH / 'static'
SONGS_DIR = STATIC_DIR / 'songs'
BACKUP_DIR = STATIC_DIR / 'backups'
LOG_DIR = BASE_PATH / 'logs'
AI_USAGE_LOG_DIR = LOG_DIR / 'ai-usage'

BACKUP_WRITE_LOCKS: Dict[str, threading.Lock] = {}
BACKUP_WRITE_LOCKS_GUARD = threading.Lock()

# 自动创建目录（首次运行时）
for path in [SONGS_DIR, BACKUP_DIR, LOG_DIR, AI_USAGE_LOG_DIR]:
    path.mkdir(parents=True, exist_ok=True)


def get_ai_usage_log_path(dt: Optional[datetime] = None) -> Path:
    now_dt = dt or datetime.now()
    return AI_USAGE_LOG_DIR / f"ai-usage-{now_dt.strftime('%Y-%m-%d')}.jsonl"


def append_ai_usage_log(event: Dict[str, Any]) -> None:
    try:
        payload = dict(event) if isinstance(event, dict) else {'event': str(event)}
        payload.setdefault('ts', int(time.time() * 1000))
        with open(get_ai_usage_log_path(), 'a', encoding='utf-8') as f:
            f.write(json.dumps(payload, ensure_ascii=False) + '\n')
    except Exception as exc:
        app.logger.warning("Failed to write ai usage log: %s", exc)


def _ai_usage_resolve_song_name(json_file: str) -> str:
    raw = str(json_file or '').strip()
    if not raw:
        return ''
    try:
        _, json_path = _resolve_existing_static_json_filename(raw)
        if json_path.exists():
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            meta = data.get('meta') if isinstance(data.get('meta'), dict) else {}
            title = str(meta.get('title') or '').strip()
            if title:
                return title
    except Exception:
        pass
    return re.sub(r'\.json$', '', raw, flags=re.I)


def _ai_usage_merge_stream_usage(
    usage: Any,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
) -> Tuple[int, int, int]:
    if usage is None:
        return prompt_tokens, completion_tokens, total_tokens
    p = getattr(usage, 'prompt_tokens', None)
    c = getattr(usage, 'completion_tokens', None)
    t = getattr(usage, 'total_tokens', None)
    if p is not None:
        prompt_tokens += int(p)
    if c is not None:
        completion_tokens += int(c)
    if t is not None:
        total_tokens += int(t)
    return prompt_tokens, completion_tokens, total_tokens


def _ai_usage_audit_token_payload(
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
) -> Dict[str, Any]:
    has_usage = bool(prompt_tokens or completion_tokens or total_tokens)
    if not has_usage:
        return {
            'prompt_tokens': None,
            'completion_tokens': None,
            'total_tokens': None,
            'token_total': None,
        }
    return {
        'prompt_tokens': prompt_tokens,
        'completion_tokens': completion_tokens,
        'total_tokens': total_tokens,
        'token_total': total_tokens,
    }


def _ai_usage_event_tokens(ev: Dict[str, Any]) -> Tuple[int, int, int]:
    prompt = coerce_int(ev.get('prompt_tokens'), 0) or 0
    completion = coerce_int(ev.get('completion_tokens'), 0) or 0
    total = coerce_int(ev.get('total_tokens'), 0) or 0
    if not total:
        total = coerce_int(ev.get('token_total'), 0) or 0
    return prompt, completion, total


def _ai_usage_effective_model(ev: Dict[str, Any]) -> str:
    for key in ('effective_model', 'model', 'translation_model'):
        value = str(ev.get(key) or '').strip()
        if value:
            return value
    return ''


def _ai_usage_preset_key(ev: Dict[str, Any]) -> str:
    preset_id = str(ev.get('preset_id') or '').strip()
    return preset_id if preset_id else 'manual'

AMLL_COVER_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}


def cleanup_amll_cover_images() -> int:
    """Remove generated amll_cover images from songs dir before startup."""
    removed = 0
    if not SONGS_DIR.exists():
        return removed

    for path in SONGS_DIR.iterdir():
        if not path.is_file():
            continue
        name_lower = path.name.lower()
        if "amll_cover" not in name_lower:
            continue
        if path.suffix.lower() not in AMLL_COVER_IMAGE_EXTS:
            continue
        try:
            path.unlink()
            removed += 1
        except Exception as exc:
            app.logger.warning("Failed to remove amll_cover image: %s (%s)", path, exc)
    if removed:
        app.logger.info("Removed %s amll_cover images from songs dir", removed)
    return removed


cleanup_amll_cover_images()


def cleanup_exports_dir(max_keep: int = 20) -> int:
    """清理 exports 目录，保留最新的 N 个文件

    Args:
        max_keep: 保留的最新文件数量，默认为20

    Returns:
        返回删除的文件数量
    """
    removed = 0
    if not EXPORTS_DIR.exists():
        return removed

    # 获取所有文件并按修改时间排序
    files = []
    for path in EXPORTS_DIR.iterdir():
        if path.is_file():
            try:
                stat = path.stat()
                files.append((stat.st_mtime, path))
            except Exception as exc:
                app.logger.warning("Failed to stat export file: %s (%s)", path, exc)

    # 按修改时间倒序排列（最新的在前）
    files.sort(reverse=True, key=lambda x: x[0])

    # 保留最新的 max_keep 个文件，删除其余的
    for timestamp, path in files[max_keep:]:
        try:
            path.unlink()
            removed += 1
        except Exception as exc:
            app.logger.warning("Failed to remove export file: %s (%s)", path, exc)

    if removed:
        app.logger.info("清理 exports 目录：删除了 %d 个旧文件，保留了最新的 %d 个", removed, max_keep)

    return removed


cleanup_exports_dir(max_keep=20)


def _collect_static_export_files() -> List[Path]:
    """Collect all files under static/ for the full bundle export."""
    if not STATIC_DIR.exists():
        return []

    files = [path for path in STATIC_DIR.rglob('*') if path.is_file()]
    files.sort(key=lambda path: path.relative_to(STATIC_DIR).as_posix().lower())
    return files


def _build_static_export_task_snapshot(task_id: str, task: Dict[str, Any]) -> Dict[str, Any]:
    total_files = int(task.get('total_files') or 0)
    processed_files = int(task.get('processed_files') or 0)
    total_bytes = int(task.get('total_bytes') or 0)
    processed_bytes = int(task.get('processed_bytes') or 0)
    status = task.get('status', 'pending')

    if status == 'done':
        progress_percent = 100.0
    elif total_bytes > 0:
        progress_percent = round(min(100.0, (processed_bytes * 100.0) / total_bytes), 2)
    elif total_files > 0:
        progress_percent = round(min(100.0, (processed_files * 100.0) / total_files), 2)
    else:
        progress_percent = 0.0

    download_path = task.get('download_path') or ''
    download_ready = status == 'done' and bool(download_path) and Path(download_path).exists()
    return {
        'task_id': task_id,
        'status': status,
        'archive_name': task.get('archive_name', ''),
        'total_files': total_files,
        'processed_files': processed_files,
        'total_bytes': total_bytes,
        'processed_bytes': processed_bytes,
        'current_file': task.get('current_file', ''),
        'error': task.get('error', ''),
        'created_at': task.get('created_at', ''),
        'completed_at': task.get('completed_at', ''),
        'progress_percent': progress_percent,
        'download_ready': download_ready,
    }


def _static_export_task_sort_key(item: Tuple[str, Dict[str, Any]]) -> Tuple[int, datetime, str]:
    task_id, task = item
    status = task.get('status', 'pending')
    timestamp_text = task.get('completed_at') or task.get('created_at') or ''
    try:
        timestamp = datetime.fromisoformat(timestamp_text)
    except Exception:
        timestamp = datetime.min

    if status in {'pending', 'running'}:
        priority = 2
    elif status == 'done':
        priority = 1
    else:
        priority = 0

    return priority, timestamp, task_id


def _find_reusable_static_export_task(device_id: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    if not device_id:
        return None

    with STATIC_EXPORT_LOCK:
        matching_tasks = [
            (task_id, task)
            for task_id, task in STATIC_EXPORT_TASKS.items()
            if task.get('owner_device_id') == device_id
        ]

        active_tasks = [
            item for item in matching_tasks
            if item[1].get('status') in {'pending', 'running'}
        ]
        if active_tasks:
            return max(active_tasks, key=_static_export_task_sort_key)

        done_tasks = []
        for item in matching_tasks:
            task = item[1]
            if task.get('status') != 'done':
                continue
            download_path = (task.get('download_path') or '').strip()
            if download_path and Path(download_path).exists():
                done_tasks.append(item)

        if done_tasks:
            return max(done_tasks, key=_static_export_task_sort_key)
    return None


def _run_static_export_task(task_id: str) -> None:
    archive_path = None
    try:
        with STATIC_EXPORT_LOCK:
            task = STATIC_EXPORT_TASKS.get(task_id)
        if task is None:
            return

        archive_path = Path(task['download_path'])
        static_files = _collect_static_export_files()
        total_files = len(static_files)
        total_bytes = 0
        for file_path in static_files:
            try:
                total_bytes += file_path.stat().st_size
            except Exception as exc:
                raise RuntimeError(f'统计文件大小失败: {file_path.relative_to(STATIC_DIR).as_posix()} ({exc})') from exc

        with STATIC_EXPORT_LOCK:
            task = STATIC_EXPORT_TASKS.get(task_id)
            if task is None:
                return
            task['status'] = 'running'
            task['total_files'] = total_files
            task['processed_files'] = 0
            task['total_bytes'] = total_bytes
            task['processed_bytes'] = 0
            task['current_file'] = ''
            task['error'] = ''

        with zipfile.ZipFile(archive_path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            processed_files = 0
            processed_bytes = 0
            for file_path in static_files:
                relative_name = file_path.relative_to(BASE_PATH).as_posix()
                try:
                    file_size = file_path.stat().st_size
                except Exception:
                    file_size = 0

                with STATIC_EXPORT_LOCK:
                    task = STATIC_EXPORT_TASKS.get(task_id)
                    if task is None:
                        return
                    task['current_file'] = relative_name

                archive.write(file_path, arcname=relative_name)

                processed_files += 1
                processed_bytes += file_size
                with STATIC_EXPORT_LOCK:
                    task = STATIC_EXPORT_TASKS.get(task_id)
                    if task is None:
                        return
                    task['processed_files'] = processed_files
                    task['processed_bytes'] = processed_bytes

        with STATIC_EXPORT_LOCK:
            task = STATIC_EXPORT_TASKS.get(task_id)
            if task is None:
                return
            task['status'] = 'done'
            task['completed_at'] = datetime.now().isoformat()
            task['current_file'] = ''
            task['processed_files'] = total_files
            task['processed_bytes'] = total_bytes

    except Exception as exc:
        if archive_path and archive_path.exists():
            try:
                archive_path.unlink()
            except Exception:
                pass
        with STATIC_EXPORT_LOCK:
            task = STATIC_EXPORT_TASKS.get(task_id)
            if task is None:
                return
            task['status'] = 'error'
            task['error'] = str(exc)
            task['completed_at'] = datetime.now().isoformat()

    finally:
        cleanup_exports_dir(max_keep=20)


def _start_static_export_task(device_id: str) -> Tuple[str, Dict[str, Any]]:
    cleanup_exports_dir(max_keep=20)
    task_id = uuid.uuid4().hex
    timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    archive_name = f'static-full-{timestamp}-{task_id[:8]}.zip'
    archive_path = EXPORTS_DIR / archive_name

    task = {
        'status': 'pending',
        'owner_device_id': device_id,
        'archive_name': archive_name,
        'download_path': str(archive_path),
        'total_files': 0,
        'processed_files': 0,
        'total_bytes': 0,
        'processed_bytes': 0,
        'current_file': '',
        'error': '',
        'created_at': datetime.now().isoformat(),
        'completed_at': '',
    }

    with STATIC_EXPORT_LOCK:
        STATIC_EXPORT_TASKS[task_id] = task

    THREADPOOL_EXECUTOR.submit(_run_static_export_task, task_id)
    return task_id, task

RESOURCE_DIRECTORIES = {
    'static': STATIC_DIR,
    'songs': SONGS_DIR,
    'backups': BACKUP_DIR,
}

WINDOWS_STRICT_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1F]')
WINDOWS_RESERVED_FILENAMES = {
    'CON', 'PRN', 'AUX', 'NUL',
    'COM1', 'COM2', 'COM3', 'COM4', 'COM5', 'COM6', 'COM7', 'COM8', 'COM9',
    'LPT1', 'LPT2', 'LPT3', 'LPT4', 'LPT5', 'LPT6', 'LPT7', 'LPT8', 'LPT9',
}


def sanitize_filename(value: Optional[str]) -> str:
    """Apply Windows-strict filename sanitization by deleting invalid characters."""
    if value is None:
        return ''

    cleaned = WINDOWS_STRICT_INVALID_FILENAME_CHARS.sub('', str(value))
    cleaned = cleaned.strip()
    cleaned = cleaned.rstrip(' .')
    if cleaned in {'', '.', '..'}:
        return ''

    stem = cleaned.split('.', 1)[0]
    if stem.rstrip(' .').upper() in WINDOWS_RESERVED_FILENAMES:
        return ''

    return cleaned


def _validate_windows_strict_filename(raw_filename: Optional[str],
                                      *,
                                      required_suffix: Optional[str] = None) -> str:
    """Validate that the provided filename already satisfies Windows-strict rules."""
    if raw_filename is None:
        raise ValueError('文件名不能为空')

    candidate = str(raw_filename)
    if not candidate:
        raise ValueError('文件名不能为空')

    safe_filename = sanitize_filename(candidate)
    if not safe_filename:
        raise ValueError('文件名在清理后为空，或命中了 Windows 保留名')

    if candidate != safe_filename:
        raise ValueError('文件名不符合 Windows 严格模式')

    if required_suffix and Path(safe_filename).suffix.lower() != required_suffix.lower():
        raise ValueError(f'文件名必须以 {required_suffix} 结尾')

    return safe_filename


def _resolve_new_filename_in_directory(base_dir: Path,
                                       raw_filename: Optional[str],
                                       *,
                                       required_suffix: Optional[str] = None) -> Tuple[str, Path]:
    """Sanitize a filename for creation/upload and keep the target inside the base directory."""
    safe_filename = sanitize_filename(raw_filename)
    if not safe_filename:
        raise ValueError('文件名在清理后为空，或命中了 Windows 保留名')

    if required_suffix and Path(safe_filename).suffix.lower() != required_suffix.lower():
        raise ValueError(f'文件名必须以 {required_suffix} 结尾')

    base_resolved = base_dir.resolve()
    target_path = (base_resolved / safe_filename).resolve()
    try:
        relative = target_path.relative_to(base_resolved)
    except ValueError as exc:
        raise ValueError('文件路径越界') from exc

    if len(relative.parts) != 1:
        raise ValueError('文件名不能包含子目录')

    return safe_filename, target_path


def _resolve_existing_filename_in_directory(base_dir: Path,
                                            raw_filename: Optional[str],
                                            *,
                                            required_suffix: Optional[str] = None) -> Tuple[str, Path]:
    """Resolve an existing filename only if the original input is already Windows-strict."""
    safe_filename = _validate_windows_strict_filename(raw_filename, required_suffix=required_suffix)

    base_resolved = base_dir.resolve()
    target_path = (base_resolved / safe_filename).resolve()
    try:
        relative = target_path.relative_to(base_resolved)
    except ValueError as exc:
        raise ValueError('文件路径越界') from exc

    if len(relative.parts) != 1:
        raise ValueError('文件名不能包含子目录')

    return safe_filename, target_path


def _resolve_new_static_json_filename(raw_filename: Optional[str]) -> Tuple[str, Path]:
    """Resolve a JSON filename inside static/ for create/rename targets."""
    return _resolve_new_filename_in_directory(STATIC_DIR, raw_filename, required_suffix='.json')


def _resolve_existing_static_json_filename(raw_filename: Optional[str]) -> Tuple[str, Path]:
    """Resolve a JSON filename inside static/ for lookup/update/delete operations."""
    return _resolve_existing_filename_in_directory(STATIC_DIR, raw_filename, required_suffix='.json')

def _normalize_relative_path(value: str) -> str:
    cleaned = (value or '').replace('\\', '/').strip('/')
    if not cleaned:
        return ''

    segments = [segment for segment in cleaned.split('/') if segment]
    for segment in segments:
        if segment in ('.', '..'):
            raise ValueError('路径包含非法段')
    return '/'.join(segments)


def _url_path_for_local_filesystem(url_or_path: str) -> str:
    """
    Path used to map a URL string onto local static files.

    urllib.parse treats '#' as starting a fragment, so parsed.path drops the
    rest of a filename like 'track#1.lrc'. For http(s), take everything from
    the first '/' after the authority through end of string, then strip only
    a '?query' suffix. For other strings, strip query only and keep '#'.
    """
    raw = str(url_or_path or '').strip().replace('\\', '/')
    if not raw:
        return ''
    lower = raw.lower()
    if lower.startswith('http://') or lower.startswith('https://'):
        idx = raw.find('://')
        authority_and_rest = raw[idx + 3:]
        slash = authority_and_rest.find('/')
        if slash < 0:
            path_q = '/'
        else:
            path_q = authority_and_rest[slash:]
        if '?' in path_q:
            path_q = path_q.split('?', 1)[0]
        return path_q
    return raw.split('?', 1)[0]


def extract_resource_relative(value: str, resource: str) -> str:
    if resource not in RESOURCE_DIRECTORIES:
        raise ValueError(f'未知资源类型: {resource}')
    if value is None:
        raise ValueError('路径不能为空')

    parsed = urlparse(str(value))
    if parsed.scheme in ('http', 'https'):
        candidate = _url_path_for_local_filesystem(str(value))
    elif parsed.scheme:
        candidate = parsed.path or ''
    else:
        candidate = str(value)
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
    if parsed.scheme in ('http', 'https'):
        candidate = _url_path_for_local_filesystem(normalized)
    else:
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

    relative = relative.split('?', 1)[0].strip()
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


def extract_font_files_from_lys(lys_content: str) -> Set[str]:
    """从LYS文件内容中提取所有引用的字体文件名
    
    解析 [font-family:] 标记，提取其中的字体文件名：
    - [font-family:] - 指定默认字体（无字体文件）
    - [font-family:Hymmnos-m8rx] - 使用 Hymmnos-m8rx 字体文件
    - [font-family:ar-ciela_compartment] - 使用 ar-ciela_compartment 字体文件
    - [font-family:(ja),Hymmnos-m8rx(en)] - 日语默认，英语使用 Hymmnos-m8rx
    
    Returns:
        Set[str]: 所有引用的字体文件名集合（不含扩展名）
    """
    font_files: Set[str] = set()
    
    for line in lys_content.splitlines():
        stripped_line = line.strip()
        font_meta_match = FONT_FAMILY_META_REGEX.match(stripped_line)
        if font_meta_match:
            detected_family = font_meta_match.group(1)
            if not detected_family:
                continue
            
            # 解析字体标记
            parts = [p.strip() for p in detected_family.split(',')]
            for part in parts:
                if not part:
                    continue
                # 匹配两种格式：
                # 1. 纯字体名: Hymmnos-m8rx
                # 2. 语言映射: Hymmnos-m8rx(en) 或 (ja)
                m = re.match(r'^(?:(?P<name>[^()]+)\s*\((?P<lang>[^)]+)\))|(?P<plain>[^()]+)$', part)
                if not m:
                    continue
                
                # 提取字体名（排除空字符串和纯语言标记）
                font_name = None
                if m.group('plain'):
                    font_name = m.group('plain').strip()
                elif m.group('name'):
                    font_name = m.group('name').strip()
                
                # 只有当字体名非空时才添加
                if font_name:
                    font_files.add(font_name)
    
    return font_files


def get_public_base_url() -> str:
    if has_request_context():
        return request.url_root.rstrip('/')

    configured = app.config.get('PUBLIC_BASE_URL') or os.environ.get('PUBLIC_BASE_URL')
    if configured:
        return configured.rstrip('/')

    port = os.environ.get('PORT', '5000')
    return f"http://127.0.0.1:{port}".rstrip('/')


def _ensure_trailing_slash(url: str) -> str:
    if not url:
        return '/'
    return url.rstrip('/') + '/'


def get_amll_web_player_base_url() -> str:
    override = app.config.get('AMLL_WEB_BASE_URL') or os.environ.get('AMLL_WEB_BASE_URL')
    if override:
        return _ensure_trailing_slash(override)

    base = get_public_base_url()
    return _ensure_trailing_slash(f"{base.rstrip('/')}/amll-web")


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


def _maybe_block_static_audio_gateway(starlette_request: StarletteRequest) -> Optional[StarletteResponse]:
    """Block direct /songs/* audio when enforce_media_gateway_for_audio is enabled."""
    try:
        media_cfg = get_media_config()
    except Exception:
        return None
    if not media_cfg.get('enforce_media_gateway_for_audio'):
        return None
    path = starlette_request.url.path or ''
    if not path.startswith('/songs/'):
        return None
    suffix = Path(path).suffix.lower()
    if suffix not in MEDIA_AUDIO_EXTENSIONS:
        return None
    return JSONResponse({'error': 'Direct audio access disabled'}, status_code=403)


@app.middleware("http")
async def _request_context_middleware(request_in: StarletteRequest, call_next):
    blocked = _maybe_block_static_audio_gateway(request_in)
    if blocked is not None:
        return blocked

    body = await request_in.body()
    form: Optional[FormData] = None
    content_type = request_in.headers.get("content-type", "")
    if content_type.startswith("multipart/") or "application/x-www-form-urlencoded" in content_type:
        try:
            form = await request_in.form()
        except Exception:
            form = None

    token = _request_context.set(RequestContext(request_in, body=body, form=form))
    try:
        response: Optional[StarletteResponse] = None
        for func in app._before_request_funcs:
            result = func()
            if result is not None:
                response = _normalize_response(result)
                break
        if response is None:
            response = await call_next(request_in)
        for func in app._after_request_funcs:
            updated = func(response)
            if updated is not None:
                response = updated
        return response
    finally:
        _request_context.reset(token)


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


def backup_song_subdirectory(name_or_path: Union[str, Path]) -> Path:
    """Per-song backup subdirectory under static/backups/by-song/{shard}/{basename}/."""
    original_name = name_or_path.name if isinstance(name_or_path, Path) else Path(str(name_or_path)).name
    base_name = _normalize_backup_basename(original_name)
    shard = hashlib.sha1(base_name.encode('utf-8')).hexdigest()[:2]
    return BACKUP_DIR / 'by-song' / shard / base_name


def build_backup_path(name_or_path: Union[str, Path],
                      timestamp: Optional[Union[str, int]] = None,
                      directory: Optional[Path] = None) -> Path:
    """Create a filesystem-safe backup path for the given target file."""
    original_name = name_or_path.name if isinstance(name_or_path, Path) else str(name_or_path)
    base_name = _normalize_backup_basename(original_name)
    if directory is None:
        directory = backup_song_subdirectory(name_or_path)
    if isinstance(timestamp, (int, float)):
        timestamp_str = str(int(timestamp))
    elif timestamp is not None:
        timestamp_str = str(timestamp)
    else:
        timestamp_str = datetime.now().strftime(BACKUP_TIMESTAMP_FORMAT)
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{base_name}.{timestamp_str}"


def backup_prefix(name_or_path: Union[str, Path]) -> str:
    """Return the normalized prefix used for locating backups."""
    original_name = name_or_path.name if isinstance(name_or_path, Path) else str(name_or_path)
    return f"{_normalize_backup_basename(original_name)}."


def _backup_file_recency_key(backup_file: Path, prefix: str) -> Tuple[int, float]:
    """Sort key for iter_backup_files: parsed backups by epoch, unparseable last."""
    timestamp_part = backup_file.name[len(prefix):]
    parsed_time: Optional[datetime] = None
    try:
        parsed_time = datetime.strptime(timestamp_part, BACKUP_TIMESTAMP_FORMAT)
    except ValueError:
        if timestamp_part.isdigit():
            try:
                parsed_time = datetime.fromtimestamp(int(timestamp_part))
            except (OSError, OverflowError, ValueError):
                parsed_time = None
    if parsed_time is None:
        return (0, 0.0)
    return (1, parsed_time.timestamp())


def iter_backup_files(name_or_path: Union[str, Path]) -> List[Path]:
    """List backups for a file: per-song subdir first, then legacy flat BACKUP_DIR."""
    prefix = backup_prefix(name_or_path)
    collected: List[Path] = []
    subdir = backup_song_subdirectory(name_or_path)
    if subdir.is_dir():
        for backup_file in subdir.iterdir():
            if backup_file.is_file() and backup_file.name.startswith(prefix):
                collected.append(backup_file)
    if BACKUP_DIR.is_dir():
        for backup_file in BACKUP_DIR.iterdir():
            if backup_file.is_file() and backup_file.name.startswith(prefix):
                collected.append(backup_file)
    return sorted(collected, key=lambda p: _backup_file_recency_key(p, prefix), reverse=True)


def backup_public_relative_path(backup_file: Path) -> str:
    """Relative path under static/backups for build_public_url."""
    try:
        return backup_file.relative_to(BACKUP_DIR).as_posix()
    except ValueError:
        return backup_file.name


def _sanitize_client_id(raw_id: str) -> str:
    """Normalize client id for filesystem usage."""
    if not raw_id:
        return ''
    safe = re.sub(r'[^a-zA-Z0-9_-]+', '_', str(raw_id))
    safe = safe.strip('_')[:64]
    if not safe:
        hashed = hashlib.sha256(str(raw_id).encode('utf-8')).hexdigest()
        safe = hashed[:32]
    return safe


def _resolve_import_target(target_path: Path, reserved_paths: Set[str]) -> Tuple[Path, bool]:
    """Return a non-conflicting target path for import."""
    relative_key = str(target_path.relative_to(STATIC_DIR)).lower()
    if not target_path.exists() and relative_key not in reserved_paths:
        reserved_paths.add(relative_key)
        return target_path, False

    stem = target_path.stem
    suffix = target_path.suffix
    counter = 1
    while True:
        candidate = target_path.with_name(f"{stem}_imported_{counter}{suffix}")
        relative_candidate = str(candidate.relative_to(STATIC_DIR)).lower()
        if not candidate.exists() and relative_candidate not in reserved_paths:
            reserved_paths.add(relative_candidate)
            return candidate, True
        counter += 1


def _rebuild_import_url(parsed, new_path: str) -> str:
    if parsed.scheme or parsed.netloc:
        return parsed._replace(path=new_path).geturl()
    if parsed.query or parsed.fragment:
        suffix = ''
        if parsed.query:
            suffix += f"?{parsed.query}"
        if parsed.fragment:
            suffix += f"#{parsed.fragment}"
        return f"{new_path}{suffix}"
    return new_path


def _replace_single_import_path(raw: str, old_rel: str, new_rel: str) -> str:
    if raw == old_rel:
        return new_rel

    old_norm = old_rel.replace('\\', '/')
    new_norm = new_rel.replace('\\', '/')
    old_lower = old_norm.lower()

    bare_old = None
    bare_new = None
    if old_lower.startswith('songs/'):
        bare_old = old_norm[len('songs/'):]
        if new_norm.lower().startswith('songs/'):
            bare_new = new_norm[len('songs/'):]
        else:
            bare_new = new_norm
        if raw == bare_old:
            return bare_new

    parsed = urlparse(raw)
    path = (parsed.path or '').replace('\\', '/')
    if not path:
        return raw

    path_lower = path.lower()
    if path_lower.endswith(old_lower):
        new_path = f"{path[:-len(old_norm)]}{new_norm}"
        return _rebuild_import_url(parsed, new_path)

    if bare_old and path_lower.endswith(bare_old.lower()):
        new_path = f"{path[:-len(bare_old)]}{bare_new}"
        return _rebuild_import_url(parsed, new_path)

    return raw


def _replace_import_paths(value: Any, rename_map: Dict[str, str]) -> Any:
    """Replace renamed import paths in JSON payload."""
    if isinstance(value, dict):
        return {k: _replace_import_paths(v, rename_map) for k, v in value.items()}
    if isinstance(value, list):
        return [_replace_import_paths(item, rename_map) for item in value]
    if not isinstance(value, str):
        return value

    if '::' in value:
        parts = value.split('::')
        return '::'.join(_replace_import_paths(part, rename_map) for part in parts)

    updated = value
    for old_rel, new_rel in rename_map.items():
        if old_rel == new_rel:
            continue
        updated = _replace_single_import_path(updated, old_rel, new_rel)
    return updated


def _build_anchor_id(account: str, password: str) -> str:
    """Create a stable anchor id from account + password."""
    raw = f"{account}:{password}"
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _merge_history(new_history, old_history):
    """Merge play history with newest-first order."""
    merged = []
    for item in (new_history or []):
        if item not in merged:
            merged.append(item)
    for item in (old_history or []):
        if item not in merged:
            merged.append(item)
    return merged[:50]


def _merge_listen_stats(new_stats, old_stats):
    """Merge listen stats, keeping recent history."""
    merged = {}
    for key, value in (old_stats or {}).items():
        if isinstance(value, dict):
            merged[key] = {
                'completions': list(value.get('completions', [])),
                'listens': list(value.get('listens', []))
            }
    for key, value in (new_stats or {}).items():
        if not isinstance(value, dict):
            continue
        entry = merged.get(key, {'completions': [], 'listens': []})
        completions = entry.get('completions', []) + list(value.get('completions', []))
        listens = entry.get('listens', []) + list(value.get('listens', []))
        entry['completions'] = completions[-50:]
        entry['listens'] = listens[-50:]
        merged[key] = entry
    return merged


def _merge_playlists(new_playlists, old_playlists):
    """Merge playlists by id and combine tracks."""
    merged = []
    playlist_index = {}

    def normalize_playlist(item):
        if not isinstance(item, dict):
            return None
        playlist_id = item.get('id')
        if not playlist_id:
            return None
        tracks = item.get('tracks')
        if not isinstance(tracks, list):
            tracks = []
        name = item.get('name') or item.get('title') or ''
        playlist_type = item.get('type') or ('artist' if item.get('artistName') else 'manual')
        artist_name = item.get('artistName') or (name if playlist_type == 'artist' else '')
        return {
            'id': playlist_id,
            'name': name,
            'type': playlist_type,
            'artistName': artist_name,
            'tracks': tracks
        }

    for item in (new_playlists or []):
        normalized = normalize_playlist(item)
        if not normalized:
            continue
        playlist_index[normalized['id']] = normalized
        merged.append(normalized)

    for item in (old_playlists or []):
        normalized = normalize_playlist(item)
        if not normalized:
            continue
        existing = playlist_index.get(normalized['id'])
        if existing:
            if existing.get('type') != 'artist' and normalized.get('type') != 'artist':
                existing_tracks = existing.get('tracks', [])
                for track in normalized.get('tracks', []):
                    if track not in existing_tracks:
                        existing_tracks.append(track)
            if not existing.get('type') and normalized.get('type'):
                existing['type'] = normalized['type']
            if not existing.get('artistName') and normalized.get('artistName'):
                existing['artistName'] = normalized['artistName']
            if not existing.get('name') and normalized.get('name'):
                existing['name'] = normalized['name']
        else:
            playlist_index[normalized['id']] = normalized
            merged.append(normalized)

    return merged


def _normalize_backup_payload_data(data):
    """Normalize backup payload data and split artist shortcuts."""
    if not isinstance(data, dict):
        return {}

    playlists = data.get('playlists')
    artist_shortcuts = data.get('artistShortcuts')
    if not isinstance(playlists, list):
        playlists = []
    if not isinstance(artist_shortcuts, list):
        artist_shortcuts = []

    normalized_playlists = []
    normalized_artist_shortcuts = []
    artist_ids = set()

    def normalize_artist_shortcut(item):
        if not isinstance(item, dict):
            return None
        playlist_id = item.get('id')
        if not playlist_id:
            return None
        name = item.get('name') or item.get('title') or ''
        artist_name = item.get('artistName') or name
        return {
            'id': playlist_id,
            'name': name,
            'type': 'artist',
            'artistName': artist_name,
            'tracks': []
        }

    for item in playlists:
        if not isinstance(item, dict):
            continue
        playlist_id = item.get('id')
        if not playlist_id:
            continue
        name = item.get('name') or item.get('title') or ''
        playlist_type = item.get('type')
        artist_name = item.get('artistName')
        if not playlist_type and isinstance(playlist_id, str) and playlist_id.startswith('artist-'):
            playlist_type = 'artist'
        if playlist_type == 'artist':
            shortcut = normalize_artist_shortcut({
                'id': playlist_id,
                'name': name,
                'artistName': artist_name or name
            })
            if shortcut:
                if shortcut['id'] not in artist_ids:
                    normalized_artist_shortcuts.append(shortcut)
                    artist_ids.add(shortcut['id'])
            normalized_playlists.append({
                'id': playlist_id,
                'name': name,
                'type': 'artist',
                'artistName': artist_name or name,
                'tracks': []
            })
            continue
        tracks = item.get('tracks')
        if not isinstance(tracks, list):
            tracks = []
        normalized_playlists.append({
            'id': playlist_id,
            'name': name,
            'type': playlist_type or 'manual',
            'artistName': artist_name or '',
            'tracks': tracks
        })

    for item in artist_shortcuts:
        normalized = normalize_artist_shortcut(item)
        if normalized and normalized['id'] not in artist_ids:
            normalized_artist_shortcuts.append(normalized)
            artist_ids.add(normalized['id'])

    data['playlists'] = normalized_playlists
    data['artistShortcuts'] = normalized_artist_shortcuts
    return data


def _get_backup_write_lock(backup_path: Path) -> threading.Lock:
    """Return a stable write lock for the given backup path."""
    lock_key = str(backup_path)
    with BACKUP_WRITE_LOCKS_GUARD:
        lock = BACKUP_WRITE_LOCKS.get(lock_key)
        if lock is None:
            lock = threading.Lock()
            BACKUP_WRITE_LOCKS[lock_key] = lock
        return lock


def _write_json_atomically(target_path: Path, payload: Any) -> None:
    """Write JSON data to disk via temp file replacement."""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode='w',
            encoding='utf-8',
            dir=str(target_path.parent),
            prefix=f'.{target_path.stem}.',
            suffix='.tmp',
            delete=False
        ) as temp_file:
            temp_path = Path(temp_file.name)
            json.dump(payload, temp_file, ensure_ascii=False, indent=2)
            temp_file.write('\n')
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_path, target_path)
    except Exception:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise

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
    "raw_lines": [],
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
    'experimental_full_line_bracket_strip': False,
    'experimental_bracket_line_as_subline': False,
    'compat_mode': False,
    'thinking_enabled': True,
    'thinking_api_key': '',
    'thinking_provider': 'deepseek',
    'thinking_base_url': 'https://api.deepseek.com',
    'thinking_model': 'deepseek-reasoner',
    'thinking_system_prompt': '''你是一位资深的歌词分析师。请通读整首歌的歌词，生成对歌曲主题、情绪、叙事视角和潜在文化背景的综合理解，并指出可能影响翻译语气的关键细节。'''
}

AI_TRANSLATION_DEFAULTS = AI_TRANSLATION_SETTINGS.copy()

AI_ALLOW_REQUEST_RUNTIME_OVERRIDE = os.environ.get('AI_ALLOW_REQUEST_RUNTIME_OVERRIDE', '0') == '1'

AI_TASK_PAYLOAD_ALLOWLIST = frozenset({
    'content', 'song_name', 'jsonFile', 'items', 'thinking_enabled',
    'mode', 'intent',
    'source_content', 'source_format', 'target_format',
    'repair_instruction', 'previous_full_model_output', 'conversation_history',
    'manual_model_output', 'compat_mode',
})

AI_RUNTIME_CONFIG_REQUEST_KEYS = frozenset({
    'preset_id', 'source_mode', 'source_preset_id',
    'provider', 'base_url', 'model', 'api_key', 'system_prompt',
    'expect_reasoning', 'strip_brackets', 'experimental_full_line_bracket_strip',
    'experimental_bracket_line_as_subline', 'compat_mode',
    'thinking_enabled', 'thinking_provider', 'thinking_base_url', 'thinking_model',
    'thinking_system_prompt', 'thinking_api_key',
    'auto_save', 'only_empty', 'always_override', 'extra_prompt',
    'romanization_system_prompt', 'romanization_alignment_mode', 'romanization_separator',
    'romanization_strict_token_count', 'romanization_require_trailing_separator',
    'translation', 'thinking', 'batch', 'romanization',
    'clear_translation_api_key', 'clear_thinking_api_key',
})

_ai_runtime_config_strip_warning_logged = False

ROMANIZATION_DEFAULT_PROMPT_INDEXED = (
    '你是歌词罗马音助手。用户每行格式为：行号 N. 后紧跟若干「[k]原文片段」，k 为从 1 开始的 token 编号（与输入完全一致）。\n'
    '你必须输出完全相同的结构：保留每个 [k] 编号，只把编号后的原文替换为对应罗马音（如日语 Hepburn）；不得翻译含义；不得添加说明。\n'
    '硬性规则：\n'
    '1. 行号 N 与输入一致，不得增删行、不得改顺序。\n'
    '2. 每行必须包含与输入相同的一组 [k] 编号，k 从 1 连续到该行 token 总数；不得跳号、不得重复、不得新增或删除编号。\n'
    '3. 不要输出 markdown、前言或后记；只输出编号行。\n'
    '4. 除 [k] 与罗马音文本外，不要在行内插入其它标记。'
)

ROMANIZATION_DEFAULT_PROMPT_SEPARATOR = (
    '你是歌词罗马音助手（兼容模式：分隔符分节）。用户每行以行号 N. 开头，之后为若干「分节」，分节之间由用户消息中说明的分隔符连接；'
    '每行末尾是否保留分隔符须与输入一致。\n'
    '你必须保持相同的行号、分节数量与分隔符用法：只将各分节内的原文替换为对应罗马音（如日语 Hepburn）；不得翻译含义；不得添加说明。\n'
    '硬性规则：\n'
    '1. 行号 N 与输入一致，不得增删行、不得改顺序。\n'
    '2. 每行的分节数量、分隔符位置与行尾分隔规则必须与输入一致。\n'
    '3. 不要输出 markdown、前言或后记；只输出编号行。\n'
    '4. 不要使用 [k] 编号协议；除约定分隔符与罗马音文本外，不要在行内插入其它标记。'
)

ROMANIZATION_DEFAULTS: Dict[str, Any] = {
    'system_prompt': ROMANIZATION_DEFAULT_PROMPT_INDEXED,
    'alignment_mode': 'indexed_tokens',
    'separator': ';',
    'strict_token_count': True,
    'require_trailing_separator': True,
}


def _default_romanization_system_prompt_for_mode(mode: str) -> str:
    if _normalize_romanization_alignment_mode(mode) == 'separator_tokens':
        return ROMANIZATION_DEFAULT_PROMPT_SEPARATOR
    return ROMANIZATION_DEFAULT_PROMPT_INDEXED


def _is_non_custom_romanization_system_prompt(user_sp: Any) -> bool:
    """Empty or exactly either built-in default (treat as non-custom for mode switching)."""
    s = str(user_sp or '').strip()
    if not s:
        return True
    if s == str(ROMANIZATION_DEFAULT_PROMPT_INDEXED).strip():
        return True
    if s == str(ROMANIZATION_DEFAULT_PROMPT_SEPARATOR).strip():
        return True
    return False


def _resolve_romanization_system_prompt_for_mode(user_raw: Any, mode: str) -> str:
    if _is_non_custom_romanization_system_prompt(user_raw):
        return _default_romanization_system_prompt_for_mode(mode)
    return str(user_raw or '').strip()


def _normalize_romanization_alignment_mode(raw: Any) -> str:
    s = str(raw or '').strip().lower().replace('-', '_')
    if s in ('indexed_tokens', 'indexed', 'token_indexed'):
        return 'indexed_tokens'
    if s in ('separator_tokens', 'separator', 'legacy'):
        return 'separator_tokens'
    return str(ROMANIZATION_DEFAULTS.get('alignment_mode') or 'indexed_tokens')


def coalesce_romanization_settings(raw: Any) -> Dict[str, Any]:
    r = raw if isinstance(raw, dict) else {}
    mode = _normalize_romanization_alignment_mode(r.get('alignment_mode'))
    sep = str(r.get('separator') or '').strip() or str(ROMANIZATION_DEFAULTS['separator'])
    sep = sep[:8] or str(ROMANIZATION_DEFAULTS['separator'])
    sp = _resolve_romanization_system_prompt_for_mode(r.get('system_prompt'), mode)
    return {
        'system_prompt': sp,
        'alignment_mode': mode,
        'separator': sep,
        'strict_token_count': parse_bool(r.get('strict_token_count'), bool(ROMANIZATION_DEFAULTS['strict_token_count'])),
        'require_trailing_separator': parse_bool(r.get('require_trailing_separator'), bool(ROMANIZATION_DEFAULTS['require_trailing_separator'])),
    }


AI_PRESETS_FILE = BASE_PATH / 'ai_presets.json'
AI_PRESET_STORE_VERSION = 1
DEFAULT_AI_PRESET_ID = 'default'
AI_SETTINGS_FILE = BASE_PATH / 'ai_settings.json'
AI_SETTINGS_STORE_VERSION = 1

DEFAULT_DEVICE_PERMISSIONS = {
    'ai_use': False,
    'ai_view_provider': False,
    'ai_view_base_url': False,
    'ai_view_model': False,
    'ai_view_prompts': False,
    'ai_edit_preset': False,
    'write_access': False,
}


def now_iso() -> str:
    return datetime.now().isoformat(timespec='seconds')


def coerce_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    try:
        if value is None or value == '':
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def normalize_device_permissions(raw_permissions: Any) -> Dict[str, bool]:
    permissions = dict(DEFAULT_DEVICE_PERMISSIONS)
    if isinstance(raw_permissions, list):
        for key in raw_permissions:
            if key in permissions:
                permissions[key] = True
        return permissions
    if isinstance(raw_permissions, dict):
        for key in permissions:
            permissions[key] = parse_bool(raw_permissions.get(key), permissions[key])
    return permissions


def default_device_permissions(write_access: bool = True) -> Dict[str, bool]:
    permissions = dict(DEFAULT_DEVICE_PERMISSIONS)
    permissions.update({
        'ai_use': True,
        'ai_view_provider': True,
        'ai_view_base_url': True,
        'ai_view_model': True,
        'ai_view_prompts': True,
        'ai_edit_preset': bool(write_access),
        'write_access': bool(write_access),
    })
    return permissions


CREDENTIAL_MEDIA_PLAYBACK_MODE_POLICIES = frozenset({
    'inherit',
    'stream_only',
    'oneshot_only',
    'user_select',
})


def normalize_credential_media_playback_mode_policy(value: Any) -> str:
    policy = str(value or 'inherit').strip().lower()
    if policy not in CREDENTIAL_MEDIA_PLAYBACK_MODE_POLICIES:
        return 'inherit'
    return policy


def normalize_security_credential(raw_credential: Any, fallback_id: Optional[str] = None) -> Dict[str, Any]:
    credential = raw_credential if isinstance(raw_credential, dict) else {}
    credential_id = str(credential.get('credential_id') or fallback_id or f'cred_{uuid.uuid4().hex[:12]}').strip()
    password_hash = str(credential.get('password_hash') or '').strip()
    permissions = normalize_device_permissions(credential.get('permissions'))
    max_uses = coerce_int(credential.get('max_uses'))
    used_count = coerce_int(credential.get('used_count'), 0) or 0
    created_at = str(credential.get('created_at') or now_iso())
    updated_at = str(credential.get('updated_at') or created_at)
    remark = str(credential.get('remark') or '').strip()
    expires_at = str(credential.get('expires_at') or '').strip()
    revoked = parse_bool(credential.get('revoked'), False)
    media_playback_mode_policy = normalize_credential_media_playback_mode_policy(
        credential.get('media_playback_mode_policy')
    )
    return {
        'credential_id': credential_id,
        'password_hash': password_hash,
        'remark': remark,
        'expires_at': expires_at,
        'max_uses': max_uses,
        'used_count': used_count,
        'revoked': revoked,
        'permissions': permissions,
        'media_playback_mode_policy': media_playback_mode_policy,
        'created_at': created_at,
        'updated_at': updated_at,
    }


def normalize_security_config(config: Any) -> Tuple[Dict[str, Any], bool]:
    raw = config if isinstance(config, dict) else {}
    normalized = dict(DEFAULT_SECURITY_CONFIG)
    normalized.update(raw)
    migrated = False

    credentials = normalized.get('device_credentials')
    if not isinstance(credentials, list):
        credentials = []

    system_password_hash = str(normalized.get('system_password_hash') or '').strip()
    next_credentials = []
    for item in credentials:
        credential = normalize_security_credential(item)
        credential_id = str(credential.get('credential_id') or '').strip()
        if credential_id == 'legacy-admin':
            if not system_password_hash:
                system_password_hash = credential.get('password_hash', '')
            migrated = True
            continue
        next_credentials.append(credential)

    legacy_password_hash = str(normalized.get('password_hash') or '').strip()
    if not system_password_hash and legacy_password_hash:
        system_password_hash = legacy_password_hash
        migrated = True

    normalized['device_credentials'] = next_credentials
    normalized['system_password_hash'] = system_password_hash
    normalized['password_hash'] = ''
    normalized['trusted_expire_days'] = coerce_int(normalized.get('trusted_expire_days'), DEFAULT_SECURITY_CONFIG['trusted_expire_days']) or DEFAULT_SECURITY_CONFIG['trusted_expire_days']
    normalized['security_enabled'] = parse_bool(normalized.get('security_enabled'), DEFAULT_SECURITY_CONFIG['security_enabled'])

    if migrated or normalized != raw:
        migrated = True
    return normalized, migrated


def get_system_password_hash(security_config: Dict[str, Any]) -> str:
    return str(security_config.get('system_password_hash') or security_config.get('password_hash') or '').strip()


def system_admin_permissions() -> Dict[str, bool]:
    return default_device_permissions(write_access=True)


def find_password_conflict(
    security_config: Dict[str, Any],
    password: str,
    exclude_credential_id: Optional[str] = None,
    skip_system_password: bool = False,
) -> Optional[str]:
    candidate_password = str(password or '').strip()
    if not candidate_password:
        return None

    system_password_hash = get_system_password_hash(security_config)
    if system_password_hash and not skip_system_password and verify_password(candidate_password, system_password_hash):
        return 'system'

    for credential in security_config.get('device_credentials', []):
        credential_id = str(credential.get('credential_id') or '').strip()
        if exclude_credential_id and credential_id == exclude_credential_id:
            continue
        if verify_password(candidate_password, credential.get('password_hash', '')):
            return credential_id or 'credential'

    return None


def resolve_trusted_device_auth_state(security_config: Dict[str, Any], device_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    credential_id = str(device_info.get('credential_id') or '').strip()
    auth_type = str(device_info.get('auth_type') or '').strip().lower()
    is_system_admin = parse_bool(device_info.get('system_admin'), False) or auth_type == 'system' or credential_id == 'legacy-admin'

    if is_system_admin:
        if get_system_password_hash(security_config):
            return {
                'auth_type': 'system',
                'is_system_admin': True,
                'credential': None,
                'permissions': system_admin_permissions(),
                'remark': str(device_info.get('remark') or '系统密码'),
                'credential_id': '',
            }
        return None

    credential = get_device_credential_by_id(security_config, credential_id)
    if not credential or parse_bool(credential.get('revoked'), False) or is_credential_expired(credential):
        return None

    return {
        'auth_type': 'credential',
        'is_system_admin': False,
        'credential': credential,
        'permissions': credential_permissions_snapshot(credential),
        'remark': str(device_info.get('remark') or credential.get('remark') or ''),
        'credential_id': credential_id,
    }


def get_device_credential_by_id(security_config: Dict[str, Any], credential_id: str) -> Optional[Dict[str, Any]]:
    for credential in security_config.get('device_credentials', []):
        if credential.get('credential_id') == credential_id:
            return credential
    return None


def is_credential_expired(credential: Dict[str, Any]) -> bool:
    expires_at = str(credential.get('expires_at') or '').strip()
    if not expires_at:
        return False
    try:
        return datetime.fromisoformat(expires_at) <= datetime.now()
    except Exception:
        return False


def get_credential_status(credential: Dict[str, Any]) -> str:
    if not credential:
        return 'unknown'
    if parse_bool(credential.get('revoked'), False):
        return 'revoked'
    if is_credential_expired(credential):
        return 'expired'
    max_uses = coerce_int(credential.get('max_uses'))
    used_count = coerce_int(credential.get('used_count'), 0) or 0
    if max_uses is not None and max_uses >= 0 and used_count >= max_uses:
        return 'exhausted'
    return 'usable'


def is_credential_usable(credential: Dict[str, Any]) -> bool:
    return get_credential_status(credential) == 'usable'


def credential_permissions_snapshot(credential: Optional[Dict[str, Any]]) -> Dict[str, bool]:
    if not credential:
        return dict(DEFAULT_DEVICE_PERMISSIONS)
    return normalize_device_permissions(credential.get('permissions'))


def build_ai_field_visibility(permissions: Optional[Dict[str, bool]] = None) -> Dict[str, str]:
    """Flat field_visibility keys aligned with LyricSphere-ai-presets.js."""
    permissions = permissions or dict(DEFAULT_DEVICE_PERMISSIONS)

    def vis(perm_key: str) -> str:
        return 'visible' if permissions.get(perm_key, False) else 'hidden'

    provider_vis = vis('ai_view_provider')
    base_url_vis = vis('ai_view_base_url')
    model_vis = vis('ai_view_model')
    prompt_vis = vis('ai_view_prompts')

    return {
        'provider': provider_vis,
        'base_url': base_url_vis,
        'model': model_vis,
        'system_prompt': prompt_vis,
        'thinking_provider': provider_vis,
        'thinking_base_url': base_url_vis,
        'thinking_model': model_vis,
        'thinking_system_prompt': prompt_vis,
        'batch_extra_prompt': prompt_vis,
        'romanization_system_prompt': prompt_vis,
    }


def build_ai_public_payload_from_settings(
    settings: Dict[str, Any],
    permissions: Optional[Dict[str, bool]] = None,
) -> Dict[str, Any]:
    translation = settings.get('translation', {}) if isinstance(settings.get('translation'), dict) else {}
    thinking = settings.get('thinking', {}) if isinstance(settings.get('thinking'), dict) else {}
    batch = settings.get('batch', {}) if isinstance(settings.get('batch'), dict) else {}
    romanization = coalesce_romanization_settings(settings.get('romanization'))

    if permissions is None:
        return {
            'translation': {
                'provider': translation.get('provider', AI_TRANSLATION_DEFAULTS.get('provider', 'deepseek')),
                'base_url': translation.get('base_url', AI_TRANSLATION_DEFAULTS.get('base_url', '')),
                'model': translation.get('model', AI_TRANSLATION_DEFAULTS.get('model', '')),
                'system_prompt': translation.get('system_prompt', AI_TRANSLATION_DEFAULTS.get('system_prompt', '')),
                'expect_reasoning': parse_bool(translation.get('expect_reasoning'), AI_TRANSLATION_DEFAULTS.get('expect_reasoning', True)),
                'strip_brackets': parse_bool(translation.get('strip_brackets'), AI_TRANSLATION_DEFAULTS.get('strip_brackets', False)),
                'experimental_full_line_bracket_strip': parse_bool(translation.get('experimental_full_line_bracket_strip'), AI_TRANSLATION_DEFAULTS.get('experimental_full_line_bracket_strip', False)),
                'experimental_bracket_line_as_subline': parse_bool(translation.get('experimental_bracket_line_as_subline'), AI_TRANSLATION_DEFAULTS.get('experimental_bracket_line_as_subline', False)),
                'compat_mode': parse_bool(translation.get('compat_mode'), AI_TRANSLATION_DEFAULTS.get('compat_mode', False)),
            },
            'thinking': {
                'enabled': parse_bool(thinking.get('enabled'), AI_TRANSLATION_DEFAULTS.get('thinking_enabled', True)),
                'provider': thinking.get('provider', AI_TRANSLATION_DEFAULTS.get('thinking_provider', 'deepseek')),
                'base_url': thinking.get('base_url', AI_TRANSLATION_DEFAULTS.get('thinking_base_url', '')),
                'model': thinking.get('model', AI_TRANSLATION_DEFAULTS.get('thinking_model', '')),
                'system_prompt': thinking.get('system_prompt', AI_TRANSLATION_DEFAULTS.get('thinking_system_prompt', '')),
            },
            'batch': {
                'auto_save': parse_bool(batch.get('auto_save'), True),
                'only_empty': parse_bool(batch.get('only_empty'), True),
                'always_override': parse_bool(batch.get('always_override'), False),
                'extra_prompt': batch.get('extra_prompt', ''),
            },
            'romanization': romanization,
        }

    view_provider = bool(permissions.get('ai_view_provider', False))
    view_base_url = bool(permissions.get('ai_view_base_url', False))
    view_model = bool(permissions.get('ai_view_model', False))
    view_prompts = bool(permissions.get('ai_view_prompts', False))

    def pick_text(value: Any, default_value: str = '', visible: bool = True) -> str:
        if not visible:
            return ''
        text = '' if value is None else str(value)
        return text if text.strip() else default_value

    return {
        'translation': {
            'provider': pick_text(
                translation.get('provider'),
                AI_TRANSLATION_DEFAULTS.get('provider', 'deepseek'),
                view_provider,
            ).strip(),
            'base_url': pick_text(
                translation.get('base_url'),
                AI_TRANSLATION_DEFAULTS.get('base_url', ''),
                view_base_url,
            ).strip(),
            'model': pick_text(
                translation.get('model'),
                AI_TRANSLATION_DEFAULTS.get('model', ''),
                view_model,
            ).strip(),
            'system_prompt': pick_text(
                translation.get('system_prompt'),
                AI_TRANSLATION_DEFAULTS.get('system_prompt', ''),
                view_prompts,
            ),
            'expect_reasoning': parse_bool(translation.get('expect_reasoning'), AI_TRANSLATION_DEFAULTS.get('expect_reasoning', True)),
            'strip_brackets': parse_bool(translation.get('strip_brackets'), AI_TRANSLATION_DEFAULTS.get('strip_brackets', False)),
            'experimental_full_line_bracket_strip': parse_bool(translation.get('experimental_full_line_bracket_strip'), AI_TRANSLATION_DEFAULTS.get('experimental_full_line_bracket_strip', False)),
            'experimental_bracket_line_as_subline': parse_bool(translation.get('experimental_bracket_line_as_subline'), AI_TRANSLATION_DEFAULTS.get('experimental_bracket_line_as_subline', False)),
            'compat_mode': parse_bool(translation.get('compat_mode'), AI_TRANSLATION_DEFAULTS.get('compat_mode', False)),
        },
        'thinking': {
            'enabled': parse_bool(thinking.get('enabled'), AI_TRANSLATION_DEFAULTS.get('thinking_enabled', True)),
            'provider': pick_text(
                thinking.get('provider'),
                AI_TRANSLATION_DEFAULTS.get('thinking_provider', 'deepseek'),
                view_provider,
            ).strip(),
            'base_url': pick_text(
                thinking.get('base_url'),
                AI_TRANSLATION_DEFAULTS.get('thinking_base_url', ''),
                view_base_url,
            ).strip(),
            'model': pick_text(
                thinking.get('model'),
                AI_TRANSLATION_DEFAULTS.get('thinking_model', ''),
                view_model,
            ).strip(),
            'system_prompt': pick_text(
                thinking.get('system_prompt'),
                AI_TRANSLATION_DEFAULTS.get('thinking_system_prompt', ''),
                view_prompts,
            ),
        },
        'batch': {
            'auto_save': parse_bool(batch.get('auto_save'), True),
            'only_empty': parse_bool(batch.get('only_empty'), True),
            'always_override': parse_bool(batch.get('always_override'), False),
            'extra_prompt': pick_text(batch.get('extra_prompt'), '', view_prompts),
        },
        'romanization': {
            **romanization,
            'system_prompt': pick_text(
                romanization.get('system_prompt'),
                _default_romanization_system_prompt_for_mode(romanization.get('alignment_mode')),
                view_prompts,
            ),
        },
    }


def build_ai_settings_snapshot(
    record: Optional[Dict[str, Any]] = None,
    permissions: Optional[Dict[str, bool]] = None,
) -> Dict[str, Any]:
    source = record or {}
    if isinstance(source, dict):
        if isinstance(source.get('settings'), dict):
            source = source.get('settings', {})
        elif isinstance(source.get('public_payload'), dict):
            source = source.get('public_payload', {})
    normalized = normalize_ai_settings_state(source)
    snapshot = build_ai_public_payload_from_settings(normalized, permissions=permissions)
    snapshot['translation_api_key_present'] = bool(str((normalized.get('translation') or {}).get('api_key') or '').strip())
    snapshot['thinking_api_key_present'] = bool(str((normalized.get('thinking') or {}).get('api_key') or '').strip())
    if permissions is not None:
        snapshot['field_visibility'] = build_ai_field_visibility(permissions)
    return snapshot


def sanitize_settings_for_device(
    settings: Dict[str, Any],
    permissions: Optional[Dict[str, bool]] = None,
) -> Dict[str, Any]:
    permissions = permissions or dict(DEFAULT_DEVICE_PERMISSIONS)
    return build_ai_settings_snapshot({'settings': settings}, permissions=permissions)


def build_ai_runtime_summary(
    settings_store: Dict[str, Any],
    runtime: Dict[str, Any],
    permissions: Optional[Dict[str, bool]] = None,
) -> Dict[str, Any]:
    permissions = permissions or dict(DEFAULT_DEVICE_PERMISSIONS)
    source_mode = str(settings_store.get('source_mode') or 'manual').strip().lower()
    if source_mode not in {'manual', 'preset'}:
        source_mode = 'manual'
    source_preset_id = str(settings_store.get('source_preset_id') or '').strip() if source_mode == 'preset' else ''
    source_preset = get_ai_preset_by_id(source_preset_id) if source_preset_id else None
    if source_mode == 'preset':
        source_kind, source_label = classify_ai_preset_source(source_preset, source_preset_id)
    else:
        source_kind, source_label = ('manual', '独立当前设置')

    provider_visible = bool(permissions.get('ai_view_provider', False))
    model_visible = bool(permissions.get('ai_view_model', False))
    translation = runtime.get('translation', {}) if isinstance(runtime.get('translation'), dict) else {}
    thinking = runtime.get('thinking', {}) if isinstance(runtime.get('thinking'), dict) else {}

    def mask_model(model_value: Any) -> str:
        model_text = str(model_value or '').strip()
        if not model_text:
            return ''
        if not model_visible:
            return '***'
        return model_text

    def mask_provider(provider_value: Any) -> str:
        if not provider_visible:
            return ''
        return str(provider_value or '').strip()

    translation_provider = mask_provider(translation.get('provider'))
    thinking_provider = mask_provider(thinking.get('provider'))
    translation_model_label = mask_model(translation.get('model'))
    thinking_model_label = mask_model(thinking.get('model'))
    thinking_enabled = parse_bool(
        thinking.get('enabled'),
        AI_TRANSLATION_DEFAULTS.get('thinking_enabled', True),
    )

    return {
        'source_mode': source_mode,
        'source_preset_id': source_preset_id,
        'source_kind': source_kind,
        'source_label': source_label,
        'provider_visible': provider_visible,
        'provider_label': translation_provider,
        'model_label': translation_model_label,
        'thinking_provider_label': thinking_provider,
        'thinking_model_label': thinking_model_label,
        'thinking_enabled': thinking_enabled,
        'translation_provider': translation_provider,
        'translation_model_label': translation_model_label,
        'translation_api_key_present': bool(str(translation.get('api_key') or '').strip()),
        'thinking_api_key_present': bool(str(thinking.get('api_key') or '').strip()),
        'resolved_from': str(runtime.get('resolved_from') or ''),
        'preset_id': str(runtime.get('id') or ''),
        'preset_name': str(runtime.get('name') or ''),
    }


def ai_preset_secret_presence(preset: Optional[Dict[str, Any]]) -> Dict[str, bool]:
    preset = preset if isinstance(preset, dict) else {}
    secret_payload = preset.get('secret_payload') if isinstance(preset.get('secret_payload'), dict) else {}
    translation_secret = str(
        ((secret_payload.get('translation') or {}).get('api_key'))
        or ((preset.get('translation') or {}).get('api_key'))
        or preset.get('api_key')
        or ''
    ).strip()
    thinking_secret = str(
        ((secret_payload.get('thinking') or {}).get('api_key'))
        or ((preset.get('thinking') or {}).get('api_key'))
        or preset.get('thinking_api_key')
        or ''
    ).strip()
    return {
        'translation_api_key_present': bool(translation_secret),
        'thinking_api_key_present': bool(thinking_secret),
    }


def materialize_ai_settings_from_preset(preset: Dict[str, Any]) -> Dict[str, Any]:
    normalized = normalize_ai_preset_record(preset)
    public_payload = normalized.get('public_payload', {}) if isinstance(normalized.get('public_payload'), dict) else {}
    secret_payload = normalized.get('secret_payload', {}) if isinstance(normalized.get('secret_payload'), dict) else {}
    source_state = {
        'translation': {
            **(public_payload.get('translation', {}) if isinstance(public_payload.get('translation'), dict) else {}),
            'api_key': str((secret_payload.get('translation') or {}).get('api_key') or '').strip(),
        },
        'thinking': {
            **(public_payload.get('thinking', {}) if isinstance(public_payload.get('thinking'), dict) else {}),
            'api_key': str((secret_payload.get('thinking') or {}).get('api_key') or '').strip(),
        },
        'batch': public_payload.get('batch', {}) if isinstance(public_payload.get('batch'), dict) else {},
        'romanization': public_payload.get('romanization', {}) if isinstance(public_payload.get('romanization'), dict) else {},
    }
    return normalize_ai_settings_state(source_state)


def resolve_effective_ai_settings_state(settings_store: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    store = settings_store if isinstance(settings_store, dict) else get_ai_settings_store()
    stored_settings = store.get('settings', {}) if isinstance(store.get('settings'), dict) else normalize_ai_settings_state({})
    source_mode = str(store.get('source_mode') or 'manual').strip().lower()
    if source_mode not in {'manual', 'preset'}:
        source_mode = 'manual'
    source_preset_id = str(store.get('source_preset_id') or '').strip() if source_mode == 'preset' else ''
    if source_mode == 'preset' and source_preset_id:
        preset = get_ai_preset_by_id(source_preset_id)
        if preset:
            return materialize_ai_settings_from_preset(preset)
    return normalize_ai_settings_state(stored_settings)


def normalize_ai_preset_record(raw_preset: Any, fallback_id: Optional[str] = None) -> Dict[str, Any]:
    preset = raw_preset if isinstance(raw_preset, dict) else {}
    preset_id = str(preset.get('id') or fallback_id or f'preset_{uuid.uuid4().hex[:12]}').strip()
    name = str(preset.get('name') or preset_id).strip() or preset_id
    created_at = str(preset.get('created_at') or now_iso())
    updated_at = str(preset.get('updated_at') or created_at)
    acl = preset.get('acl') if isinstance(preset.get('acl'), dict) else {}
    owner_scope_raw = str(preset.get('owner_scope') or '').strip().lower()
    if owner_scope_raw:
        owner_scope = owner_scope_raw
    else:
        owner_scope = 'system' if preset_id == DEFAULT_AI_PRESET_ID else 'personal'
    # Canonical scope normalization:
    # - default/system preset: system
    # - any preset with acl rules: shared
    # - others: personal
    if owner_scope in {'user', 'local', 'mine'}:
        owner_scope = 'personal'
    if preset_id == DEFAULT_AI_PRESET_ID or owner_scope == 'system':
        owner_scope = 'system'
    elif acl:
        owner_scope = 'shared'
    else:
        owner_scope = 'personal'
    public_payload = build_ai_public_payload_from_settings(preset)
    translation = preset.get('translation') if isinstance(preset.get('translation'), dict) else {}
    thinking = preset.get('thinking') if isinstance(preset.get('thinking'), dict) else {}
    batch = preset.get('batch') if isinstance(preset.get('batch'), dict) else {}
    raw_secret_payload = preset.get('secret_payload') if isinstance(preset.get('secret_payload'), dict) else {}
    secret_payload = {
        'translation': {
            'api_key': str(
                translation.get('api_key')
                or (raw_secret_payload.get('translation') or {}).get('api_key')
                or preset.get('api_key')
                or ''
            ).strip(),
        },
        'thinking': {
            'api_key': str(
                thinking.get('api_key')
                or (raw_secret_payload.get('thinking') or {}).get('api_key')
                or preset.get('thinking_api_key')
                or ''
            ).strip(),
        },
    }
    return {
        'id': preset_id,
        'name': name,
        'created_at': created_at,
        'updated_at': updated_at,
        'owner_scope': owner_scope,
        'acl': acl,
        'public_payload': public_payload,
        'secret_payload': secret_payload,
        'translation': {**public_payload['translation'], 'api_key': secret_payload['translation']['api_key']},
        'thinking': {**public_payload['thinking'], 'api_key': secret_payload['thinking']['api_key']},
        'batch': public_payload['batch'],
        'romanization': dict(public_payload.get('romanization', {})),
    }


def flatten_ai_preset_record(preset: Dict[str, Any], include_secrets: bool = False) -> Dict[str, Any]:
    public_payload = preset.get('public_payload', {}) if isinstance(preset.get('public_payload'), dict) else {}
    translation = dict(public_payload.get('translation', {}))
    thinking = dict(public_payload.get('thinking', {}))
    batch = dict(public_payload.get('batch', {}))
    romanization = dict(public_payload.get('romanization', {}))
    if include_secrets:
        secret_payload = preset.get('secret_payload', {}) if isinstance(preset.get('secret_payload'), dict) else {}
        translation['api_key'] = str((secret_payload.get('translation') or {}).get('api_key') or '')
        thinking['api_key'] = str((secret_payload.get('thinking') or {}).get('api_key') or '')
    return {
        'id': preset.get('id', ''),
        'name': preset.get('name', ''),
        'created_at': preset.get('created_at', ''),
        'updated_at': preset.get('updated_at', ''),
        'owner_scope': preset.get('owner_scope', 'global'),
        'acl': preset.get('acl', {}),
        'translation': translation,
        'thinking': thinking,
        'batch': batch,
        'romanization': romanization,
    }


def load_ai_preset_store() -> Dict[str, Any]:
    if AI_PRESETS_FILE.exists():
        try:
            with open(AI_PRESETS_FILE, 'r', encoding='utf-8') as f:
                store = json.load(f)
        except Exception:
            store = {}
    else:
        store = {}
    if not isinstance(store, dict):
        store = {}
    presets = store.get('presets')
    if not isinstance(presets, list):
        presets = []
    needs_legacy_owner_scope_migration = False
    for raw_preset in presets:
        if not isinstance(raw_preset, dict):
            continue
        raw_scope = str(raw_preset.get('owner_scope') or '').strip().lower()
        raw_acl = raw_preset.get('acl') if isinstance(raw_preset.get('acl'), dict) else {}
        raw_id = str(raw_preset.get('id') or '').strip()
        target_scope = 'system' if (raw_id == DEFAULT_AI_PRESET_ID or raw_scope == 'system') else ('shared' if raw_acl else 'personal')
        # Force rewrite whenever stored scope is not canonical target
        if raw_scope != target_scope:
            needs_legacy_owner_scope_migration = True
            break
    normalized_presets = [normalize_ai_preset_record(preset) for preset in presets]
    if not normalized_presets:
        normalized_presets = [normalize_ai_preset_record({
            'id': DEFAULT_AI_PRESET_ID,
            'name': '默认预设',
            'owner_scope': 'system',
            'translation': {
                'provider': AI_TRANSLATION_DEFAULTS.get('provider', 'deepseek'),
                'base_url': AI_TRANSLATION_DEFAULTS.get('base_url', 'https://api.deepseek.com'),
                'model': AI_TRANSLATION_DEFAULTS.get('model', 'deepseek-reasoner'),
                'system_prompt': AI_TRANSLATION_DEFAULTS.get('system_prompt', ''),
                'expect_reasoning': AI_TRANSLATION_DEFAULTS.get('expect_reasoning', True),
                'strip_brackets': AI_TRANSLATION_DEFAULTS.get('strip_brackets', False),
                'experimental_full_line_bracket_strip': AI_TRANSLATION_DEFAULTS.get('experimental_full_line_bracket_strip', False),
                'experimental_bracket_line_as_subline': AI_TRANSLATION_DEFAULTS.get('experimental_bracket_line_as_subline', False),
                'compat_mode': AI_TRANSLATION_DEFAULTS.get('compat_mode', False),
            },
            'thinking': {
                'enabled': AI_TRANSLATION_DEFAULTS.get('thinking_enabled', True),
                'provider': AI_TRANSLATION_DEFAULTS.get('thinking_provider', 'deepseek'),
                'base_url': AI_TRANSLATION_DEFAULTS.get('thinking_base_url', 'https://api.deepseek.com'),
                'model': AI_TRANSLATION_DEFAULTS.get('thinking_model', 'deepseek-reasoner'),
                'system_prompt': AI_TRANSLATION_DEFAULTS.get('thinking_system_prompt', ''),
            },
            'batch': {
                'auto_save': True,
                'only_empty': True,
                'always_override': False,
                'extra_prompt': '',
            },
            'romanization': dict(ROMANIZATION_DEFAULTS),
            'secret_payload': {
                'translation': {'api_key': ''},
                'thinking': {'api_key': ''},
            }
        }, DEFAULT_AI_PRESET_ID)]
    active_preset_id = str(store.get('active_preset_id') or normalized_presets[0]['id']).strip() or normalized_presets[0]['id']
    if not any(preset['id'] == active_preset_id for preset in normalized_presets):
        active_preset_id = normalized_presets[0]['id']
    normalized_store = {
        'version': AI_PRESET_STORE_VERSION,
        'active_preset_id': active_preset_id,
        'presets': normalized_presets,
    }
    if needs_legacy_owner_scope_migration:
        normalized_store['_needs_owner_scope_migration_save'] = True
    return normalized_store


def save_ai_preset_store(store: Dict[str, Any]) -> None:
    normalized_store = load_ai_preset_store()
    if isinstance(store, dict):
        presets = store.get('presets') if isinstance(store.get('presets'), list) else normalized_store['presets']
        normalized_store['presets'] = [normalize_ai_preset_record(preset) for preset in presets]
        active_preset_id = str(store.get('active_preset_id') or normalized_store.get('active_preset_id') or DEFAULT_AI_PRESET_ID).strip()
        if active_preset_id and any(preset['id'] == active_preset_id for preset in normalized_store['presets']):
            normalized_store['active_preset_id'] = active_preset_id
        elif normalized_store['presets']:
            normalized_store['active_preset_id'] = normalized_store['presets'][0]['id']
        normalized_store['version'] = AI_PRESET_STORE_VERSION
    with open(AI_PRESETS_FILE, 'w', encoding='utf-8') as f:
        json.dump(normalized_store, f, ensure_ascii=False, indent=2)


def get_ai_preset_store() -> Dict[str, Any]:
    store = load_ai_preset_store()
    needs_migration_save = parse_bool(store.pop('_needs_owner_scope_migration_save', False), False)
    if not AI_PRESETS_FILE.exists() or needs_migration_save:
        save_ai_preset_store(store)
    return store


def normalize_ai_settings_state(raw_state: Any, fallback_state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    state = raw_state if isinstance(raw_state, dict) else {}
    fallback = fallback_state if isinstance(fallback_state, dict) else {}

    state_translation = state.get('translation') if isinstance(state.get('translation'), dict) else {}
    state_thinking = state.get('thinking') if isinstance(state.get('thinking'), dict) else {}
    state_batch = state.get('batch') if isinstance(state.get('batch'), dict) else {}

    fallback_translation = fallback.get('translation') if isinstance(fallback.get('translation'), dict) else {}
    fallback_thinking = fallback.get('thinking') if isinstance(fallback.get('thinking'), dict) else {}
    fallback_batch = fallback.get('batch') if isinstance(fallback.get('batch'), dict) else {}

    def pick_text(flat_key: str, nested_section: Dict[str, Any], nested_key: str, fallback_section: Dict[str, Any], default_value: str = '') -> str:
        if flat_key in state:
            value = state.get(flat_key)
        elif nested_key in nested_section:
            value = nested_section.get(nested_key)
        elif flat_key in fallback:
            value = fallback.get(flat_key)
        elif nested_key in fallback_section:
            value = fallback_section.get(nested_key)
        else:
            value = default_value
        return '' if value is None else str(value)

    def pick_bool(flat_key: str, nested_section: Dict[str, Any], nested_key: str, fallback_section: Dict[str, Any], fallback_value: bool) -> bool:
        if flat_key in state:
            value = state.get(flat_key)
        elif nested_key in nested_section:
            value = nested_section.get(nested_key)
        elif flat_key in fallback:
            value = fallback.get(flat_key)
        elif nested_key in fallback_section:
            value = fallback_section.get(nested_key)
        else:
            value = fallback_value
        return parse_bool(value, fallback_value)

    translation = {
        'provider': pick_text('provider', state_translation, 'provider', fallback_translation, AI_TRANSLATION_DEFAULTS.get('provider', 'deepseek')).strip(),
        'base_url': pick_text('base_url', state_translation, 'base_url', fallback_translation, AI_TRANSLATION_DEFAULTS.get('base_url', '')).strip(),
        'model': pick_text('model', state_translation, 'model', fallback_translation, AI_TRANSLATION_DEFAULTS.get('model', '')).strip(),
        'system_prompt': pick_text('system_prompt', state_translation, 'system_prompt', fallback_translation, AI_TRANSLATION_DEFAULTS.get('system_prompt', '')),
        'expect_reasoning': pick_bool('expect_reasoning', state_translation, 'expect_reasoning', fallback_translation, parse_bool(fallback_translation.get('expect_reasoning'), AI_TRANSLATION_DEFAULTS.get('expect_reasoning', True))),
        'strip_brackets': pick_bool('strip_brackets', state_translation, 'strip_brackets', fallback_translation, parse_bool(fallback_translation.get('strip_brackets'), AI_TRANSLATION_DEFAULTS.get('strip_brackets', False))),
        'experimental_full_line_bracket_strip': pick_bool('experimental_full_line_bracket_strip', state_translation, 'experimental_full_line_bracket_strip', fallback_translation, parse_bool(fallback_translation.get('experimental_full_line_bracket_strip'), AI_TRANSLATION_DEFAULTS.get('experimental_full_line_bracket_strip', False))),
        'experimental_bracket_line_as_subline': pick_bool('experimental_bracket_line_as_subline', state_translation, 'experimental_bracket_line_as_subline', fallback_translation, parse_bool(fallback_translation.get('experimental_bracket_line_as_subline'), AI_TRANSLATION_DEFAULTS.get('experimental_bracket_line_as_subline', False))),
        'compat_mode': pick_bool('compat_mode', state_translation, 'compat_mode', fallback_translation, parse_bool(fallback_translation.get('compat_mode'), AI_TRANSLATION_DEFAULTS.get('compat_mode', False))),
        'api_key': pick_text('api_key', state_translation, 'api_key', fallback_translation, '').strip(),
    }

    thinking = {
        'enabled': pick_bool('thinking_enabled', state_thinking, 'enabled', fallback_thinking, parse_bool(fallback_thinking.get('enabled'), AI_TRANSLATION_DEFAULTS.get('thinking_enabled', True))),
        'provider': pick_text('thinking_provider', state_thinking, 'provider', fallback_thinking, AI_TRANSLATION_DEFAULTS.get('thinking_provider', 'deepseek')).strip(),
        'base_url': pick_text('thinking_base_url', state_thinking, 'base_url', fallback_thinking, AI_TRANSLATION_DEFAULTS.get('thinking_base_url', '')).strip(),
        'model': pick_text('thinking_model', state_thinking, 'model', fallback_thinking, AI_TRANSLATION_DEFAULTS.get('thinking_model', '')).strip(),
        'system_prompt': pick_text('thinking_system_prompt', state_thinking, 'system_prompt', fallback_thinking, AI_TRANSLATION_DEFAULTS.get('thinking_system_prompt', '')),
        'api_key': pick_text('thinking_api_key', state_thinking, 'api_key', fallback_thinking, '').strip(),
    }

    batch = {
        'auto_save': pick_bool('auto_save', state_batch, 'auto_save', fallback_batch, parse_bool(fallback_batch.get('auto_save'), True)),
        'only_empty': pick_bool('only_empty', state_batch, 'only_empty', fallback_batch, parse_bool(fallback_batch.get('only_empty'), True)),
        'always_override': pick_bool('always_override', state_batch, 'always_override', fallback_batch, parse_bool(fallback_batch.get('always_override'), False)),
        'extra_prompt': pick_text('extra_prompt', state_batch, 'extra_prompt', fallback_batch, ''),
    }

    state_roman = state.get('romanization') if isinstance(state.get('romanization'), dict) else {}
    fallback_roman = fallback.get('romanization') if isinstance(fallback.get('romanization'), dict) else {}
    roman_sep = pick_text('romanization_separator', state_roman, 'separator', fallback_roman, ROMANIZATION_DEFAULTS['separator']).strip()
    if not roman_sep:
        roman_sep = str(ROMANIZATION_DEFAULTS['separator'])
    roman_align_raw = pick_text(
        'romanization_alignment_mode', state_roman, 'alignment_mode', fallback_roman,
        str(ROMANIZATION_DEFAULTS.get('alignment_mode') or 'indexed_tokens')
    )
    roman_mode_norm = _normalize_romanization_alignment_mode(roman_align_raw)
    romanization = {
        'system_prompt': pick_text(
            'romanization_system_prompt', state_roman, 'system_prompt', fallback_roman,
            _default_romanization_system_prompt_for_mode(roman_mode_norm),
        ),
        'alignment_mode': roman_mode_norm,
        'separator': roman_sep[:8],
        'strict_token_count': pick_bool('romanization_strict_token_count', state_roman, 'strict_token_count', fallback_roman, parse_bool(fallback_roman.get('strict_token_count'), True)),
        'require_trailing_separator': pick_bool(
            'romanization_require_trailing_separator', state_roman, 'require_trailing_separator', fallback_roman,
            parse_bool(fallback_roman.get('require_trailing_separator'), True)
        ),
    }
    romanization['system_prompt'] = _resolve_romanization_system_prompt_for_mode(
        romanization.get('system_prompt'), romanization.get('alignment_mode')
    )

    return {
        'translation': translation,
        'thinking': thinking,
        'batch': batch,
        'romanization': romanization,
    }


def load_ai_settings_store() -> Dict[str, Any]:
    if AI_SETTINGS_FILE.exists():
        try:
            with open(AI_SETTINGS_FILE, 'r', encoding='utf-8') as f:
                store = json.load(f)
        except Exception:
            store = {}
    else:
        store = {}
    if not isinstance(store, dict):
        store = {}

    # Backward compatibility:
    # - legacy file may store translation/thinking/batch directly at root
    # - new file stores meta fields (source_mode/source_preset_id) + nested settings
    if isinstance(store.get('settings'), dict):
        raw_settings = store.get('settings')
    elif any(key in store for key in ('translation', 'thinking', 'batch', 'romanization', 'provider', 'base_url', 'model', 'api_key')):
        raw_settings = store
    else:
        raw_settings = {}
    normalized_settings = normalize_ai_settings_state(raw_settings, AI_TRANSLATION_SETTINGS)
    source_mode = str(store.get('source_mode') or 'manual').strip().lower()
    if source_mode not in {'manual', 'preset'}:
        source_mode = 'manual'
    source_preset_id = str(store.get('source_preset_id') or '').strip() if source_mode == 'preset' else ''
    normalized_store = {
        'version': AI_SETTINGS_STORE_VERSION,
        'updated_at': str(store.get('updated_at') or now_iso()),
        'source_mode': source_mode,
        'source_preset_id': source_preset_id,
        'settings': normalized_settings,
    }
    return normalized_store


def save_ai_settings_store(store: Dict[str, Any]) -> None:
    normalized_store = load_ai_settings_store()
    if isinstance(store, dict):
        # persist meta fields
        incoming_mode = str(store.get('source_mode') or normalized_store.get('source_mode') or 'manual').strip().lower()
        if incoming_mode not in {'manual', 'preset'}:
            incoming_mode = 'manual'
        incoming_preset_id = str(store.get('source_preset_id') or '').strip() if incoming_mode == 'preset' else ''
        normalized_store['source_mode'] = incoming_mode
        normalized_store['source_preset_id'] = incoming_preset_id

        raw_settings = store.get('settings') if isinstance(store.get('settings'), dict) else store
        normalized_store['settings'] = normalize_ai_settings_state(raw_settings, normalized_store.get('settings', {}))
        normalized_store['updated_at'] = str(store.get('updated_at') or now_iso())
        normalized_store['version'] = AI_SETTINGS_STORE_VERSION
    with open(AI_SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(normalized_store, f, ensure_ascii=False, indent=2)


def get_ai_settings_store() -> Dict[str, Any]:
    store = load_ai_settings_store()
    if not AI_SETTINGS_FILE.exists():
        save_ai_settings_store(store)
    return store


def sync_ai_translation_settings(settings_state: Dict[str, Any]) -> None:
    translation = settings_state.get('translation', {}) if isinstance(settings_state.get('translation'), dict) else {}
    thinking = settings_state.get('thinking', {}) if isinstance(settings_state.get('thinking'), dict) else {}
    global AI_TRANSLATION_SETTINGS
    AI_TRANSLATION_SETTINGS.update({
        'api_key': str(translation.get('api_key') or ''),
        'system_prompt': str(translation.get('system_prompt') or ''),
        'provider': str(translation.get('provider') or AI_TRANSLATION_DEFAULTS.get('provider', 'deepseek')),
        'base_url': str(translation.get('base_url') or AI_TRANSLATION_DEFAULTS.get('base_url', '')),
        'model': str(translation.get('model') or AI_TRANSLATION_DEFAULTS.get('model', '')),
        'expect_reasoning': parse_bool(translation.get('expect_reasoning'), AI_TRANSLATION_DEFAULTS.get('expect_reasoning', True)),
        'strip_brackets': parse_bool(translation.get('strip_brackets'), AI_TRANSLATION_DEFAULTS.get('strip_brackets', False)),
        'experimental_full_line_bracket_strip': parse_bool(translation.get('experimental_full_line_bracket_strip'), AI_TRANSLATION_DEFAULTS.get('experimental_full_line_bracket_strip', False)),
        'experimental_bracket_line_as_subline': parse_bool(translation.get('experimental_bracket_line_as_subline'), AI_TRANSLATION_DEFAULTS.get('experimental_bracket_line_as_subline', False)),
        'compat_mode': parse_bool(translation.get('compat_mode'), AI_TRANSLATION_DEFAULTS.get('compat_mode', False)),
        'thinking_enabled': parse_bool(thinking.get('enabled'), AI_TRANSLATION_DEFAULTS.get('thinking_enabled', True)),
        'thinking_api_key': str(thinking.get('api_key') or ''),
        'thinking_provider': str(thinking.get('provider') or AI_TRANSLATION_DEFAULTS.get('thinking_provider', 'deepseek')),
        'thinking_base_url': str(thinking.get('base_url') or AI_TRANSLATION_DEFAULTS.get('thinking_base_url', '')),
        'thinking_model': str(thinking.get('model') or AI_TRANSLATION_DEFAULTS.get('thinking_model', '')),
        'thinking_system_prompt': str(thinking.get('system_prompt') or ''),
    })


def get_current_ai_settings_state() -> Dict[str, Any]:
    store = get_ai_settings_store()
    settings_state = store.get('settings') if isinstance(store.get('settings'), dict) else normalize_ai_settings_state({})
    sync_ai_translation_settings(settings_state)
    return settings_state


def get_active_ai_preset() -> Optional[Dict[str, Any]]:
    store = get_ai_preset_store()
    active_id = store.get('active_preset_id')
    for preset in store.get('presets', []):
        if preset.get('id') == active_id:
            return preset
    return store.get('presets', [None])[0]


def set_active_ai_preset(preset_id: str) -> None:
    store = get_ai_preset_store()
    if any(preset.get('id') == preset_id for preset in store.get('presets', [])):
        store['active_preset_id'] = preset_id
        save_ai_preset_store(store)


def update_ai_preset_store_from_payload(
    presets_payload: List[Dict[str, Any]],
    active_preset_id: Optional[str] = None,
    mode: str = 'replace_all',
    permissions: Optional[Dict[str, bool]] = None,
) -> Dict[str, Any]:
    current_store = get_ai_preset_store()
    current_presets = [preset for preset in current_store.get('presets', []) if isinstance(preset, dict)]
    current_presets_by_id = {
        str(preset.get('id') or ''): preset
        for preset in current_presets
        if str(preset.get('id') or '')
    }
    normalized_permissions = normalize_device_permissions(permissions) if isinstance(permissions, dict) else None

    def should_update_field(permission_key: Optional[str]) -> bool:
        if not permission_key:
            return True
        if normalized_permissions is None:
            return True
        return bool(normalized_permissions.get(permission_key, False))

    def has_nested_key(container: Dict[str, Any], field_name: str) -> bool:
        return isinstance(container, dict) and field_name in container

    def preserve_existing_secret_payload(raw_preset: Dict[str, Any], existing_preset: Dict[str, Any]) -> Dict[str, Any]:
        preserved = dict(raw_preset)
        existing_secret_payload = existing_preset.get('secret_payload') if isinstance(existing_preset.get('secret_payload'), dict) else {}
        existing_translation_secret = str((existing_secret_payload.get('translation') or {}).get('api_key') or '').strip()
        existing_thinking_secret = str((existing_secret_payload.get('thinking') or {}).get('api_key') or '').strip()

        raw_translation = raw_preset.get('translation') if isinstance(raw_preset.get('translation'), dict) else {}
        raw_thinking = raw_preset.get('thinking') if isinstance(raw_preset.get('thinking'), dict) else {}

        incoming_translation_secret = ''
        if 'api_key' in raw_preset:
            incoming_translation_secret = str(raw_preset.get('api_key') or '').strip()
        elif has_nested_key(raw_translation, 'api_key'):
            incoming_translation_secret = str(raw_translation.get('api_key') or '').strip()

        incoming_thinking_secret = ''
        if 'thinking_api_key' in raw_preset:
            incoming_thinking_secret = str(raw_preset.get('thinking_api_key') or '').strip()
        elif has_nested_key(raw_thinking, 'api_key'):
            incoming_thinking_secret = str(raw_thinking.get('api_key') or '').strip()

        preserved_secret_payload = dict(preserved.get('secret_payload')) if isinstance(preserved.get('secret_payload'), dict) else {}
        if not incoming_translation_secret and existing_translation_secret:
            preserved_secret_payload.setdefault('translation', {})['api_key'] = existing_translation_secret
        if not incoming_thinking_secret and existing_thinking_secret:
            preserved_secret_payload.setdefault('thinking', {})['api_key'] = existing_thinking_secret
        if preserved_secret_payload:
            preserved['secret_payload'] = preserved_secret_payload
        return preserved

    def merge_existing_preset(existing_preset: Dict[str, Any], raw_preset: Dict[str, Any]) -> Dict[str, Any]:
        merged = normalize_ai_preset_record(flatten_ai_preset_record(existing_preset, include_secrets=True), existing_preset.get('id'))
        merged['created_at'] = existing_preset.get('created_at', merged.get('created_at', now_iso()))

        if 'name' in raw_preset:
            merged['name'] = str(raw_preset.get('name') or merged.get('name') or merged['id']).strip() or merged['id']
        if 'owner_scope' in raw_preset:
            merged['owner_scope'] = str(raw_preset.get('owner_scope') or merged.get('owner_scope') or 'global').strip() or 'global'
        if 'acl' in raw_preset and isinstance(raw_preset.get('acl'), dict):
            merged['acl'] = raw_preset.get('acl')

        public_payload = merged.get('public_payload', {}) if isinstance(merged.get('public_payload'), dict) else {}
        translation = dict(public_payload.get('translation', {}))
        thinking = dict(public_payload.get('thinking', {}))
        batch = dict(public_payload.get('batch', {}))
        romanization = dict(coalesce_romanization_settings(public_payload.get('romanization')))
        raw_translation = raw_preset.get('translation') if isinstance(raw_preset.get('translation'), dict) else {}
        raw_thinking = raw_preset.get('thinking') if isinstance(raw_preset.get('thinking'), dict) else {}
        raw_batch = raw_preset.get('batch') if isinstance(raw_preset.get('batch'), dict) else {}
        raw_roman = raw_preset.get('romanization') if isinstance(raw_preset.get('romanization'), dict) else {}

        if should_update_field('ai_view_provider'):
            if 'provider' in raw_preset or has_nested_key(raw_translation, 'provider'):
                translation['provider'] = str(raw_preset.get('provider', raw_translation.get('provider')) or '').strip()
            if 'thinking_provider' in raw_preset or has_nested_key(raw_thinking, 'provider'):
                thinking['provider'] = str(raw_preset.get('thinking_provider', raw_thinking.get('provider')) or '').strip()
        # Empty string means unchanged for base_url/model fields.
        if should_update_field('ai_view_base_url'):
            if 'base_url' in raw_preset or has_nested_key(raw_translation, 'base_url'):
                incoming_base_url = str(raw_preset.get('base_url', raw_translation.get('base_url')) or '').strip()
                if incoming_base_url:
                    translation['base_url'] = incoming_base_url
            if 'thinking_base_url' in raw_preset or has_nested_key(raw_thinking, 'base_url'):
                incoming_thinking_base_url = str(raw_preset.get('thinking_base_url', raw_thinking.get('base_url')) or '').strip()
                if incoming_thinking_base_url:
                    thinking['base_url'] = incoming_thinking_base_url
        if should_update_field('ai_view_model'):
            if 'model' in raw_preset or has_nested_key(raw_translation, 'model'):
                incoming_model = str(raw_preset.get('model', raw_translation.get('model')) or '').strip()
                if incoming_model:
                    translation['model'] = incoming_model
            if 'thinking_model' in raw_preset or has_nested_key(raw_thinking, 'model'):
                incoming_thinking_model = str(raw_preset.get('thinking_model', raw_thinking.get('model')) or '').strip()
                if incoming_thinking_model:
                    thinking['model'] = incoming_thinking_model
        if should_update_field('ai_view_prompts'):
            if 'system_prompt' in raw_preset or has_nested_key(raw_translation, 'system_prompt'):
                translation['system_prompt'] = str(raw_preset.get('system_prompt', raw_translation.get('system_prompt')) or '')
            if 'thinking_system_prompt' in raw_preset or has_nested_key(raw_thinking, 'system_prompt'):
                thinking['system_prompt'] = str(raw_preset.get('thinking_system_prompt', raw_thinking.get('system_prompt')) or '')
            if 'extra_prompt' in raw_preset or has_nested_key(raw_batch, 'extra_prompt'):
                batch['extra_prompt'] = str(raw_preset.get('extra_prompt', raw_batch.get('extra_prompt')) or '')
            if 'romanization_system_prompt' in raw_preset or has_nested_key(raw_roman, 'system_prompt'):
                romanization['system_prompt'] = str(
                    raw_preset.get('romanization_system_prompt', raw_roman.get('system_prompt')) or ''
                )
            if 'romanization_separator' in raw_preset or has_nested_key(raw_roman, 'separator'):
                sep = str(raw_preset.get('romanization_separator', raw_roman.get('separator')) or '').strip()[:8]
                romanization['separator'] = sep or str(ROMANIZATION_DEFAULTS['separator'])
            if 'romanization_strict_token_count' in raw_preset or has_nested_key(raw_roman, 'strict_token_count'):
                romanization['strict_token_count'] = parse_bool(
                    raw_preset.get('romanization_strict_token_count', raw_roman.get('strict_token_count')),
                    romanization.get('strict_token_count', True)
                )
            if 'romanization_require_trailing_separator' in raw_preset or has_nested_key(raw_roman, 'require_trailing_separator'):
                romanization['require_trailing_separator'] = parse_bool(
                    raw_preset.get('romanization_require_trailing_separator', raw_roman.get('require_trailing_separator')),
                    romanization.get('require_trailing_separator', True)
                )
            if 'romanization_alignment_mode' in raw_preset or has_nested_key(raw_roman, 'alignment_mode'):
                romanization['alignment_mode'] = _normalize_romanization_alignment_mode(
                    raw_preset.get('romanization_alignment_mode', raw_roman.get('alignment_mode'))
                )

        if 'expect_reasoning' in raw_preset or has_nested_key(raw_translation, 'expect_reasoning'):
            translation['expect_reasoning'] = parse_bool(
                raw_preset.get('expect_reasoning', raw_translation.get('expect_reasoning')),
                translation.get('expect_reasoning', True)
            )
        if 'compat_mode' in raw_preset or has_nested_key(raw_translation, 'compat_mode'):
            translation['compat_mode'] = parse_bool(
                raw_preset.get('compat_mode', raw_translation.get('compat_mode')),
                translation.get('compat_mode', False)
            )
        if 'strip_brackets' in raw_preset or has_nested_key(raw_translation, 'strip_brackets'):
            translation['strip_brackets'] = parse_bool(
                raw_preset.get('strip_brackets', raw_translation.get('strip_brackets')),
                translation.get('strip_brackets', False)
            )
        if 'experimental_full_line_bracket_strip' in raw_preset or has_nested_key(raw_translation, 'experimental_full_line_bracket_strip'):
            translation['experimental_full_line_bracket_strip'] = parse_bool(
                raw_preset.get('experimental_full_line_bracket_strip', raw_translation.get('experimental_full_line_bracket_strip')),
                translation.get('experimental_full_line_bracket_strip', False)
            )
        if 'experimental_bracket_line_as_subline' in raw_preset or has_nested_key(raw_translation, 'experimental_bracket_line_as_subline'):
            translation['experimental_bracket_line_as_subline'] = parse_bool(
                raw_preset.get('experimental_bracket_line_as_subline', raw_translation.get('experimental_bracket_line_as_subline')),
                translation.get('experimental_bracket_line_as_subline', False)
            )
        if 'thinking_enabled' in raw_preset or has_nested_key(raw_thinking, 'enabled'):
            thinking['enabled'] = parse_bool(
                raw_preset.get('thinking_enabled', raw_thinking.get('enabled')),
                thinking.get('enabled', True)
            )
        if 'auto_save' in raw_preset or has_nested_key(raw_batch, 'auto_save'):
            batch['auto_save'] = parse_bool(
                raw_preset.get('auto_save', raw_batch.get('auto_save')),
                batch.get('auto_save', True)
            )
        if 'only_empty' in raw_preset or has_nested_key(raw_batch, 'only_empty'):
            batch['only_empty'] = parse_bool(
                raw_preset.get('only_empty', raw_batch.get('only_empty')),
                batch.get('only_empty', True)
            )
        if 'always_override' in raw_preset or has_nested_key(raw_batch, 'always_override'):
            batch['always_override'] = parse_bool(
                raw_preset.get('always_override', raw_batch.get('always_override')),
                batch.get('always_override', False)
            )

        if 'api_key' in raw_preset or has_nested_key(raw_translation, 'api_key'):
            translation_secret = str(raw_preset.get('api_key', raw_translation.get('api_key')) or '').strip()
            if translation_secret:
                merged.setdefault('secret_payload', {}).setdefault('translation', {})['api_key'] = translation_secret
        if 'thinking_api_key' in raw_preset or has_nested_key(raw_thinking, 'api_key'):
            thinking_secret = str(raw_preset.get('thinking_api_key', raw_thinking.get('api_key')) or '').strip()
            if thinking_secret:
                merged.setdefault('secret_payload', {}).setdefault('thinking', {})['api_key'] = thinking_secret

        romanization = dict(coalesce_romanization_settings(romanization))
        merged['public_payload'] = build_ai_public_payload_from_settings({
            'translation': translation,
            'thinking': thinking,
            'batch': batch,
            'romanization': romanization,
        })
        merged['translation'] = {**merged['public_payload']['translation'], 'api_key': merged.get('secret_payload', {}).get('translation', {}).get('api_key', '')}
        merged['thinking'] = {**merged['public_payload']['thinking'], 'api_key': merged.get('secret_payload', {}).get('thinking', {}).get('api_key', '')}
        merged['batch'] = merged['public_payload']['batch']
        merged['romanization'] = merged['public_payload']['romanization']
        merged['updated_at'] = now_iso()
        return merged

    def parse_updated_at_to_ts(value: Any) -> float:
        if value is None:
            return 0.0
        if isinstance(value, (int, float)):
            try:
                return float(value)
            except Exception:
                return 0.0
        text = str(value).strip()
        if not text:
            return 0.0
        try:
            if text.isdigit():
                return float(text)
        except Exception:
            pass
        try:
            return float(datetime.fromisoformat(text.replace('Z', '+00:00')).timestamp() * 1000.0)
        except Exception:
            return 0.0

    def stable_public_fingerprint(preset: Dict[str, Any]) -> str:
        """Stable fingerprint for comparing preset visible configuration (exclude secrets/timestamps)."""
        try:
            flat = flatten_ai_preset_record(preset, include_secrets=False)
        except Exception:
            flat = preset if isinstance(preset, dict) else {}
        if not isinstance(flat, dict):
            flat = {}

        translation = flat.get('translation') if isinstance(flat.get('translation'), dict) else {}
        thinking = flat.get('thinking') if isinstance(flat.get('thinking'), dict) else {}
        batch = flat.get('batch') if isinstance(flat.get('batch'), dict) else {}
        roman = flat.get('romanization') if isinstance(flat.get('romanization'), dict) else {}
        payload = {
            'id': str(flat.get('id') or ''),
            'name': str(flat.get('name') or ''),
            'owner_scope': str(flat.get('owner_scope') or ''),
            'acl': flat.get('acl') if isinstance(flat.get('acl'), dict) else {},
            'translation': {
                'provider': str(translation.get('provider') or ''),
                'base_url': str(translation.get('base_url') or ''),
                'model': str(translation.get('model') or ''),
                'system_prompt': str(translation.get('system_prompt') or ''),
                'expect_reasoning': parse_bool(translation.get('expect_reasoning'), True),
                'compat_mode': parse_bool(translation.get('compat_mode'), False),
                'strip_brackets': parse_bool(translation.get('strip_brackets'), False),
                'experimental_full_line_bracket_strip': parse_bool(translation.get('experimental_full_line_bracket_strip'), False),
                'experimental_bracket_line_as_subline': parse_bool(translation.get('experimental_bracket_line_as_subline'), False),
            },
            'thinking': {
                'enabled': parse_bool(thinking.get('enabled'), True),
                'provider': str(thinking.get('provider') or ''),
                'base_url': str(thinking.get('base_url') or ''),
                'model': str(thinking.get('model') or ''),
                'system_prompt': str(thinking.get('system_prompt') or ''),
            },
            'batch': {
                'auto_save': parse_bool(batch.get('auto_save'), True),
                'only_empty': parse_bool(batch.get('only_empty'), True),
                'always_override': parse_bool(batch.get('always_override'), False),
                'extra_prompt': str(batch.get('extra_prompt') or ''),
            },
            'romanization': {
                'system_prompt': str(roman.get('system_prompt') or ''),
                'alignment_mode': _normalize_romanization_alignment_mode(roman.get('alignment_mode')),
                'separator': str(roman.get('separator') or ''),
                'strict_token_count': parse_bool(roman.get('strict_token_count'), True),
                'require_trailing_separator': parse_bool(roman.get('require_trailing_separator'), True),
            },
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)

    def is_backend_generated_default_preset(preset: Dict[str, Any]) -> bool:
        if not isinstance(preset, dict):
            return False
        if str(preset.get('id') or '').strip() != DEFAULT_AI_PRESET_ID:
            return False
        name = str(preset.get('name') or '').strip()
        if name and name not in ('默认预设', 'Default', 'default'):
            # user renamed it: treat as customized
            return False
        secret_payload = preset.get('secret_payload') if isinstance(preset.get('secret_payload'), dict) else {}
        translation_secret = str((secret_payload.get('translation') or {}).get('api_key') or '').strip()
        thinking_secret = str((secret_payload.get('thinking') or {}).get('api_key') or '').strip()
        if translation_secret or thinking_secret:
            # has secrets, likely customized
            return False

        default_template = normalize_ai_preset_record({
            'id': DEFAULT_AI_PRESET_ID,
            'name': '默认预设',
            'translation': {
                'provider': AI_TRANSLATION_DEFAULTS.get('provider', 'deepseek'),
                'base_url': AI_TRANSLATION_DEFAULTS.get('base_url', 'https://api.deepseek.com'),
                'model': AI_TRANSLATION_DEFAULTS.get('model', 'deepseek-reasoner'),
                'system_prompt': AI_TRANSLATION_DEFAULTS.get('system_prompt', ''),
                'expect_reasoning': AI_TRANSLATION_DEFAULTS.get('expect_reasoning', True),
                'strip_brackets': AI_TRANSLATION_DEFAULTS.get('strip_brackets', False),
                'experimental_full_line_bracket_strip': AI_TRANSLATION_DEFAULTS.get('experimental_full_line_bracket_strip', False),
                'experimental_bracket_line_as_subline': AI_TRANSLATION_DEFAULTS.get('experimental_bracket_line_as_subline', False),
                'compat_mode': AI_TRANSLATION_DEFAULTS.get('compat_mode', False),
            },
            'thinking': {
                'enabled': AI_TRANSLATION_DEFAULTS.get('thinking_enabled', True),
                'provider': AI_TRANSLATION_DEFAULTS.get('thinking_provider', AI_TRANSLATION_DEFAULTS.get('provider', 'deepseek')),
                'base_url': AI_TRANSLATION_DEFAULTS.get('thinking_base_url', AI_TRANSLATION_DEFAULTS.get('base_url', 'https://api.deepseek.com')),
                'model': AI_TRANSLATION_DEFAULTS.get('thinking_model', AI_TRANSLATION_DEFAULTS.get('model', 'deepseek-reasoner')),
                'system_prompt': AI_TRANSLATION_DEFAULTS.get('thinking_system_prompt', ''),
            },
            'batch': {
                'auto_save': True,
                'only_empty': True,
                'always_override': False,
                'extra_prompt': '',
            },
            'secret_payload': {
                'translation': {'api_key': ''},
                'thinking': {'api_key': ''},
            }
        }, DEFAULT_AI_PRESET_ID)

        try:
            return stable_public_fingerprint(preset) == stable_public_fingerprint(default_template)
        except Exception:
            return False

    next_presets: List[Dict[str, Any]] = []
    next_presets_by_id: Dict[str, Dict[str, Any]] = {}

    if mode == 'upsert':
        for raw_preset in presets_payload or []:
            raw = raw_preset if isinstance(raw_preset, dict) else {}
            preset_id = str(raw.get('id') or raw.get('preset_id') or '').strip()
            existing_preset = current_presets_by_id.get(preset_id) if preset_id else None
            if existing_preset:
                next_preset = merge_existing_preset(existing_preset, raw)
            else:
                next_preset = normalize_ai_preset_record(raw, preset_id or None)
                next_preset['updated_at'] = now_iso()
            next_presets_by_id[next_preset['id']] = next_preset

        for preset in current_presets:
            preset_id = str(preset.get('id') or '')
            if preset_id in next_presets_by_id:
                next_presets.append(next_presets_by_id.pop(preset_id))
            else:
                next_presets.append(preset)
        next_presets.extend(next_presets_by_id.values())
    elif mode == 'merge_legacy_local':
        for raw_preset in presets_payload or []:
            raw = raw_preset if isinstance(raw_preset, dict) else {}
            preset_id = str(raw.get('id') or raw.get('preset_id') or '').strip()
            if not preset_id:
                continue
            existing_preset = current_presets_by_id.get(preset_id)
            if existing_preset:
                incoming_ts = parse_updated_at_to_ts(raw.get('updated_at'))
                existing_ts = parse_updated_at_to_ts(existing_preset.get('updated_at'))
                existing_fp = stable_public_fingerprint(existing_preset)
                incoming_fp = stable_public_fingerprint(normalize_ai_preset_record(raw, preset_id))
                content_differs = bool(existing_fp and incoming_fp and existing_fp != incoming_fp)
                # Protect legacy customized default preset: backend-generated default should not overwrite it
                prefer_incoming = (
                    preset_id == DEFAULT_AI_PRESET_ID
                    and content_differs
                    and is_backend_generated_default_preset(existing_preset)
                )
                if prefer_incoming or incoming_ts >= existing_ts:
                    raw = preserve_existing_secret_payload(raw, existing_preset)
                    next_presets_by_id[preset_id] = merge_existing_preset(existing_preset, raw)
                else:
                    next_presets_by_id[preset_id] = existing_preset
            else:
                next_preset = normalize_ai_preset_record(raw, preset_id)
                next_preset['updated_at'] = now_iso()
                next_presets_by_id[next_preset['id']] = next_preset

        # keep ordering: existing first, then new
        for preset in current_presets:
            preset_id = str(preset.get('id') or '')
            if preset_id in next_presets_by_id:
                next_presets.append(next_presets_by_id.pop(preset_id))
            else:
                next_presets.append(preset)
        next_presets.extend(next_presets_by_id.values())
    else:
        for raw_preset in presets_payload or []:
            raw = raw_preset if isinstance(raw_preset, dict) else {}
            preset_id = str(raw.get('id') or raw.get('preset_id') or '').strip()
            existing_preset = current_presets_by_id.get(preset_id) if preset_id else None
            if existing_preset:
                raw = preserve_existing_secret_payload(raw, existing_preset)
            next_preset = normalize_ai_preset_record(raw)
            next_preset['updated_at'] = now_iso()
            next_presets.append(next_preset)

    if not next_presets:
        next_presets = current_store.get('presets', [])

    next_active_id = active_preset_id or current_store.get('active_preset_id') or (next_presets[0]['id'] if next_presets else DEFAULT_AI_PRESET_ID)
    if next_presets and not any(preset['id'] == next_active_id for preset in next_presets):
        next_active_id = next_presets[0]['id']
    next_store = {
        'version': AI_PRESET_STORE_VERSION,
        'active_preset_id': next_active_id,
        'presets': next_presets,
    }
    save_ai_preset_store(next_store)
    return next_store


def sanitize_preset_for_device(preset: Dict[str, Any], permissions: Optional[Dict[str, bool]] = None) -> Dict[str, Any]:
    permissions = permissions or dict(DEFAULT_DEVICE_PERMISSIONS)
    flat = flatten_ai_preset_record(preset, include_secrets=False)
    kind, label = classify_ai_preset_source(preset, str(preset.get('id') or ''))
    flat['kind'] = kind
    flat['label'] = label
    flat.update(ai_preset_secret_presence(preset))
    if not permissions.get('ai_view_provider', False):
        flat['translation']['provider'] = ''
        flat['thinking']['provider'] = ''
    if not permissions.get('ai_view_base_url', False):
        flat['translation']['base_url'] = ''
        flat['thinking']['base_url'] = ''
    if not permissions.get('ai_view_model', False):
        flat['translation']['model'] = ''
        flat['thinking']['model'] = ''
    if not permissions.get('ai_view_prompts', False):
        flat['translation']['system_prompt'] = ''
        flat['thinking']['system_prompt'] = ''
        flat['batch']['extra_prompt'] = ''
        if isinstance(flat.get('romanization'), dict):
            flat['romanization']['system_prompt'] = ''
    flat['field_visibility'] = build_ai_field_visibility(permissions)
    return flat


def _can_apply_request_runtime_override(request_data: Dict[str, Any]) -> bool:
    if AI_ALLOW_REQUEST_RUNTIME_OVERRIDE:
        return True
    if not is_loopback_request():
        return False
    intent = str(request_data.get('intent') or '').strip().lower()
    return intent in {'probe_form', 'preview'}


def _request_contains_runtime_config_keys(request_data: Dict[str, Any]) -> List[str]:
    if not isinstance(request_data, dict):
        return []
    stripped: List[str] = []
    for key in request_data.keys():
        if key in AI_RUNTIME_CONFIG_REQUEST_KEYS:
            stripped.append(key)
            continue
        if key.startswith('thinking_') or key.startswith('romanization_'):
            stripped.append(key)
    return stripped


def extract_ai_task_payload(request_data: Any) -> Dict[str, Any]:
    global _ai_runtime_config_strip_warning_logged
    if not isinstance(request_data, dict):
        return {}
    payload = {key: request_data[key] for key in AI_TASK_PAYLOAD_ALLOWLIST if key in request_data}
    if not _can_apply_request_runtime_override(request_data):
        stripped = _request_contains_runtime_config_keys(request_data)
        if stripped and not _ai_runtime_config_strip_warning_logged:
            app.logger.warning(
                'AI task request contained runtime config fields (ignored): %s',
                ', '.join(sorted(set(stripped))[:24]),
            )
            _ai_runtime_config_strip_warning_logged = True
    return payload


def resolve_runtime_ai_config(request_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    request_data = request_data if isinstance(request_data, dict) else {}
    settings_store = get_ai_settings_store()
    current_settings = settings_store.get('settings', {}) if isinstance(settings_store.get('settings'), dict) else normalize_ai_settings_state({})

    stored_source_mode = str(settings_store.get('source_mode') or 'manual').strip().lower()
    if stored_source_mode not in {'manual', 'preset'}:
        stored_source_mode = 'manual'
    stored_preset_id = str(settings_store.get('source_preset_id') or '').strip() if stored_source_mode == 'preset' else ''

    resolved_from = 'manual'
    effective_preset_id = ''
    preset = None

    if stored_source_mode == 'preset' and stored_preset_id:
        effective_preset_id = stored_preset_id
        preset = get_ai_preset_by_id(effective_preset_id)
        if preset:
            resolved_from = 'stored_preset'
            runtime = normalize_ai_settings_state(materialize_ai_settings_from_preset(preset))
        else:
            resolved_from = 'stored_preset_missing_fallback'
            runtime = normalize_ai_settings_state(current_settings)
    else:
        runtime = normalize_ai_settings_state(current_settings)

    if _can_apply_request_runtime_override(request_data):
        runtime = normalize_ai_settings_state(request_data, runtime)

    request_preset_id = str(request_data.get('preset_id') or '').strip()
    if effective_preset_id and not preset and resolved_from == 'stored_preset_missing_fallback':
        runtime['source_mode'] = 'manual'
        runtime['source_preset_id'] = ''
    elif preset:
        runtime['source_mode'] = 'preset'
        runtime['source_preset_id'] = str(preset.get('id') or '')
    else:
        runtime['source_mode'] = 'manual'
        runtime['source_preset_id'] = ''

    runtime['requested_preset_id'] = request_preset_id
    runtime['resolved_from'] = resolved_from
    runtime['id'] = str((preset or {}).get('id') or effective_preset_id or '')
    runtime['name'] = str((preset or {}).get('name') or effective_preset_id or '')
    return runtime


def resolve_ai_request_preset(request_data: Dict[str, Any]) -> Dict[str, Any]:
    """Backward-compatible wrapper; runtime is resolved server-side only."""
    return resolve_runtime_ai_config(request_data)


def get_current_device_auth_context() -> Dict[str, Any]:
    device_id = request.cookies.get('FEW_DEVICE_ID')
    if not device_id:
        return {
            'device_id': None,
            'authenticated': False,
            'trusted': False,
            'is_system_admin': False,
            'is_local': is_loopback_request(),
            'auth_type': '',
            'permissions': dict(DEFAULT_DEVICE_PERMISSIONS),
            'credential': None,
            'device_info': None,
            'remark': '',
            'expires_at': '',
            'max_uses': None,
            'used_count': 0,
        }

    trusted_devices = load_trusted_devices()
    device_info = trusted_devices.get(device_id)
    if not isinstance(device_info, dict):
        return {
            'device_id': device_id,
            'authenticated': False,
            'trusted': False,
            'is_system_admin': False,
            'is_local': is_loopback_request(),
            'auth_type': '',
            'permissions': dict(DEFAULT_DEVICE_PERMISSIONS),
            'credential': None,
            'device_info': None,
            'remark': '',
            'expires_at': '',
            'max_uses': None,
            'used_count': 0,
        }

    security_config = get_security_config()
    resolved = resolve_trusted_device_auth_state(security_config, device_info)
    if not resolved:
        return {
            'device_id': device_id,
            'authenticated': False,
            'trusted': False,
            'is_system_admin': False,
            'is_local': is_loopback_request(),
            'auth_type': '',
            'permissions': dict(DEFAULT_DEVICE_PERMISSIONS),
            'credential': None,
            'device_info': device_info,
            'remark': str(device_info.get('remark') or ''),
            'expires_at': str(device_info.get('expires_at') or ''),
            'max_uses': coerce_int(device_info.get('max_uses')),
            'used_count': coerce_int(device_info.get('used_count'), 0) or 0,
        }

    auth_type = str(resolved.get('auth_type') or '').strip().lower()
    is_system_admin = bool(resolved.get('is_system_admin'))
    credential = resolved.get('credential')
    permissions = resolved.get('permissions', dict(DEFAULT_DEVICE_PERMISSIONS))
    max_uses = coerce_int(device_info.get('max_uses'), coerce_int((credential or {}).get('max_uses')))
    used_count = coerce_int(device_info.get('used_count'), 0) or 0
    expires_at = str(device_info.get('expires_at') or (credential or {}).get('expires_at') or '')

    if expires_at:
        try:
            if datetime.fromisoformat(expires_at) <= datetime.now():
                return {
                    'device_id': device_id,
                    'authenticated': False,
                    'trusted': False,
                    'is_system_admin': False,
                    'is_local': is_loopback_request(),
                    'auth_type': '',
                    'permissions': dict(DEFAULT_DEVICE_PERMISSIONS),
                    'credential': None,
                    'device_info': device_info,
                    'remark': str(device_info.get('remark') or resolved.get('remark') or ''),
                    'expires_at': expires_at,
                    'max_uses': max_uses,
                    'used_count': used_count,
                }
        except Exception:
            pass

    if max_uses is not None and max_uses >= 0 and used_count >= max_uses:
        return {
            'device_id': device_id,
            'authenticated': False,
            'trusted': False,
            'is_system_admin': False,
            'is_local': is_loopback_request(),
            'auth_type': '',
            'permissions': dict(DEFAULT_DEVICE_PERMISSIONS),
            'credential': None,
            'device_info': device_info,
            'remark': str(device_info.get('remark') or resolved.get('remark') or ''),
            'expires_at': expires_at,
            'max_uses': max_uses,
            'used_count': used_count,
        }

    return {
        'device_id': device_id,
        'authenticated': True,
        'trusted': bool(is_system_admin or (credential and is_credential_usable(credential) and permissions.get('write_access', False))),
        'is_system_admin': is_system_admin,
        'is_local': is_loopback_request(),
        'auth_type': auth_type,
        'permissions': permissions,
        'credential': credential,
        'device_info': device_info,
        'remark': str(resolved.get('remark') or device_info.get('remark') or (credential or {}).get('remark') or ''),
        'expires_at': expires_at,
        'max_uses': max_uses,
        'used_count': used_count,
    }


def has_device_permission(permission: str) -> bool:
    context = get_current_device_auth_context()
    if context.get('is_system_admin'):
        return True
    return bool(context.get('permissions', {}).get(permission, False))


def can_use_ai() -> bool:
    return is_local_request() or has_device_permission('ai_use')


def can_edit_ai_presets() -> bool:
    return is_local_request() or has_device_permission('ai_edit_preset')


def can_manage_system() -> bool:
    return is_loopback_request() or bool(get_current_device_auth_context().get('is_system_admin'))


def get_ai_preset_by_id(preset_id: str) -> Optional[Dict[str, Any]]:
    if not preset_id:
        return None
    store = get_ai_preset_store()
    for preset in store.get('presets', []):
        if preset.get('id') == preset_id:
            return preset
    return None


def classify_ai_preset_source(preset: Optional[Dict[str, Any]], preset_id: str = '') -> Tuple[str, str]:
    pid = str(preset_id or (preset or {}).get('id') or '').strip()
    if not preset:
        if pid:
            return ('missing_preset', f'预设已丢失：{pid}')
        return ('manual', '独立当前设置')
    name = str(preset.get('name') or pid or '未命名预设').strip() or '未命名预设'
    owner_scope = str(preset.get('owner_scope') or '').strip().lower()
    acl = preset.get('acl') if isinstance(preset.get('acl'), dict) else {}
    if owner_scope in {'system'} or str(preset.get('id') or '') == DEFAULT_AI_PRESET_ID:
        return ('system_preset', f'系统预设：{name}')
    if owner_scope in {'shared'} or acl:
        return ('shared_preset', f'分享预设：{name}')
    return ('personal_preset', f'个人预设：{name}')


BATCH_TRANSLATION_FIXED_PROMPT = '''批量翻译固定规则：
1. 每个歌曲块必须先输出对应的 [ID:xxx]。
2. 每个 [ID:] 下只输出该歌曲的逐行翻译，编号格式必须与输入保持一致（如 N. 翻译内容，或 N_M. 翻译内容）。
3. 不要删除编号，不要合并不同歌曲，不要把多个 ID 混在一起。
4. 只输出翻译结果，不要额外解释或说明。'''


def build_batch_system_prompt(system_prompt: str) -> str:
    """Append the fixed batch-only contract to the editable prompt."""
    parts = []
    editable_prompt = system_prompt.strip() if isinstance(system_prompt, str) else ''
    fixed_prompt = BATCH_TRANSLATION_FIXED_PROMPT.strip()
    if editable_prompt:
        parts.append(editable_prompt)
    if fixed_prompt not in editable_prompt:
        parts.append(fixed_prompt)
    return '\n\n'.join(parts)


def iter_complete_lines(buffer: str) -> Tuple[List[str], str]:
    """Split a streaming buffer into complete lines and a trailing partial line."""
    lines: List[str] = []
    start = 0
    for match in re.finditer(r'\r\n|\n|\r', buffer):
        lines.append(buffer[start:match.start()])
        start = match.end()
    return lines, buffer[start:]

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
OUTER_BRACKET_PAIRS = {
    '(': ')',
    '（': '）',
}
_NUMBERED_INDEX = r'(\d+)(?:_(\d+))?'
# Keep separator set in sync with extractTimestamps() strip regex in
# templates/LyricSphere-lyrics-workbench.js ([\.、．,，:：\)）] etc.).
NUMBERED_TRANSLATION_LINE_PATTERNS = [
    re.compile(rf'^\s*{_NUMBERED_INDEX}\.\s*(.*)$'),
    re.compile(rf'^\s*{_NUMBERED_INDEX}[、,，]\s*(.*)$'),
    re.compile(rf'^\s*{_NUMBERED_INDEX}[:：]\s*(.*)$'),
    re.compile(rf'^\s*{_NUMBERED_INDEX}[\)）]\s*(.*)$'),
    re.compile(rf'^\s*\[\s*{_NUMBERED_INDEX}\s*\]\s*(.*)$'),
    re.compile(rf'^\s*【\s*{_NUMBERED_INDEX}\s*】\s*(.*)$'),
]
NUMBERED_TRANSLATION_LINE_PATTERN = NUMBERED_TRANSLATION_LINE_PATTERNS[0]
TRANSLATION_OUTPUT_FORMAT_CONTRACT = (
    '输出格式要求：\n'
    '允许在同一次回复中包含歌曲理解等自由文本，但翻译段必须逐行使用与待翻译歌词一致的编号，'
    '格式为「行号.译文」（例如 1.译文、2_1.子句译文）。\n'
    '理解段不要使用可被误识别为编号翻译的「N.」开头行；不要用 markdown 代码块包裹译文。'
)
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


def is_fully_wrapped_by_outer_brackets(text: str) -> bool:
    if not text:
        return False
    stripped = text.strip()
    if len(stripped) < 2:
        return False
    opening = stripped[0]
    closing = OUTER_BRACKET_PAIRS.get(opening)
    if not closing or stripped[-1] != closing:
        return False

    depth = 0
    for idx, ch in enumerate(stripped):
        if ch == opening:
            depth += 1
            continue
        if ch == closing:
            depth -= 1
            if depth < 0:
                return False
            if depth == 0 and idx != len(stripped) - 1:
                return False
    if depth != 0:
        return False
    return bool(stripped[1:-1].strip())


def strip_outer_brackets_if_full_line(text: str) -> str:
    stripped = (text or '').strip()
    if not is_fully_wrapped_by_outer_brackets(stripped):
        return stripped
    return stripped[1:-1].strip()


def build_translation_prompt_lines(
    entries: List[Dict[str, Any]],
    strip_brackets: bool = False,
    experimental_full_line_bracket_strip: bool = False,
    experimental_bracket_line_as_subline: bool = False
) -> List[Dict[str, Any]]:
    prompt_lines: List[Dict[str, Any]] = []
    main_index = 0
    active_main_index = 0
    subline_counters: Dict[int, int] = {}

    for entry in entries:
        source_line_no = int(entry.get('source_line_no') or 0)
        entry_index = int(entry.get('entry_index') or 0)
        raw_line = str(entry.get('raw_line') or '')
        original_text = str(entry.get('text') or '')
        normalized_text = original_text.strip()
        line_tag = str(entry.get('line_tag') or '').strip()
        is_tag_subline_candidate = line_tag in {'6', '7', '8'}
        is_full_line_bracket = is_fully_wrapped_by_outer_brackets(normalized_text)

        if strip_brackets:
            normalized_text = strip_bracket_blocks(normalized_text)

        should_strip_outer = experimental_full_line_bracket_strip or (
            experimental_bracket_line_as_subline and is_full_line_bracket
        )
        if should_strip_outer and is_full_line_bracket:
            normalized_text = strip_outer_brackets_if_full_line(normalized_text)

        if not normalized_text:
            continue

        is_subline = False
        current_main_index = 0
        sub_index = 0
        is_subline_candidate = is_full_line_bracket or is_tag_subline_candidate
        if experimental_bracket_line_as_subline and is_subline_candidate and active_main_index > 0:
            is_subline = True
            current_main_index = active_main_index
            sub_index = subline_counters.get(current_main_index, 0) + 1
            subline_counters[current_main_index] = sub_index
        else:
            main_index += 1
            active_main_index = main_index
            current_main_index = main_index

        display_index = f"{current_main_index}_{sub_index}" if is_subline else str(current_main_index)
        prompt_lines.append({
            'source_line_no': source_line_no,
            'entry_index': entry_index,
            'original_text': original_text,
            'normalized_text': normalized_text,
            'is_full_line_bracket': is_full_line_bracket,
            'line_tag': line_tag,
            'is_tag_subline_candidate': is_tag_subline_candidate,
            'is_subline_candidate': is_subline_candidate,
            'is_subline': is_subline,
            'main_index': current_main_index,
            'sub_index': sub_index,
            'display_index': display_index,
            'raw_line': raw_line,
        })
    return prompt_lines


def build_subline_prompt_notice() -> str:
    return (
        '补充说明：\n'
        '形如 N_1、N_2 的编号表示它们从属于主句 N。\n'
        '这些从句可能来自整句括号行，或来自原歌词中的 [6]、[7]、[8] 标签行。\n'
        '请保持这种主从关系输出，不要把子句改成新的主编号。\n'
        '主句与从句仍需逐行对应翻译。'
    )


def parse_numbered_translation_line(line: str) -> Optional[Tuple[str, str]]:
    if not line:
        return None
    stripped = line.strip()
    for pattern in NUMBERED_TRANSLATION_LINE_PATTERNS:
        match = pattern.match(stripped)
        if not match:
            continue
        main_index = int(match.group(1))
        if main_index <= 0:
            return None
        sub_raw = match.group(2)
        if sub_raw:
            sub_index = int(sub_raw)
            if sub_index <= 0:
                return None
            display_index = f"{main_index}_{sub_index}"
        else:
            display_index = str(main_index)
        content = match.group(3).strip()
        return display_index, content
    return None


def build_translated_dict_from_text(text: str) -> Dict[str, str]:
    translated_dict: Dict[str, str] = {}
    if not text:
        return translated_dict
    for line in text.split('\n'):
        stripped = line.strip()
        if not stripped or stripped.startswith('思考'):
            continue
        parsed_line = parse_numbered_translation_line(line)
        if not parsed_line:
            continue
        display_index, translated_text = parsed_line
        translated_dict[display_index] = translated_text
    return translated_dict


def merge_model_stream_texts(content: str, reasoning: str, thinking_summary: str = '') -> str:
    parts: List[str] = []
    for segment in (thinking_summary, content, reasoning):
        if not segment:
            continue
        stripped = segment.strip()
        if stripped and stripped not in parts:
            parts.append(stripped)
    return '\n\n'.join(parts)


def resolve_translation_source_text(content: str, reasoning: str, thinking_summary: str = '') -> str:
    for candidate in (content, reasoning, merge_model_stream_texts(content, reasoning, thinking_summary)):
        if candidate and build_translated_dict_from_text(candidate):
            return candidate
    merged = merge_model_stream_texts(content, reasoning, thinking_summary)
    if merged:
        return merged
    return content or reasoning or thinking_summary or ''


def build_fallback_timestamped_lines(prose: str, line_prefixes: List[str]) -> List[str]:
    if not prose or not line_prefixes:
        return []
    final_lyrics: List[str] = []
    prefix_idx = 0
    for raw_line in prose.split('\n'):
        stripped = raw_line.strip()
        if not stripped:
            continue
        if stripped.startswith('思考') or stripped.startswith('歌曲理解'):
            continue
        if parse_numbered_translation_line(raw_line):
            continue
        if prefix_idx >= len(line_prefixes):
            break
        prefix = line_prefixes[prefix_idx]
        final_lyrics.append(f"{prefix}{stripped}" if prefix else stripped)
        prefix_idx += 1
    return final_lyrics


def extract_batch_item_stream_section(stream_text: str, item_id: str) -> str:
    if not stream_text or not item_id:
        return ''
    id_header = re.compile(rf'^\s*\[ID:{re.escape(item_id)}\]\s*$', re.MULTILINE)
    match = id_header.search(stream_text)
    if not match:
        return ''
    start = match.end()
    next_match = re.search(r'^\s*\[ID:.+?\]\s*$', stream_text[start:], re.MULTILINE)
    end = start + next_match.start() if next_match else len(stream_text)
    return stream_text[start:end].strip()


def finalize_translation_dict_and_lyrics(
    content: str,
    reasoning: str,
    thinking_summary: str,
    prompt_lines: List[Dict[str, Any]],
    line_prefixes: List[str],
) -> Tuple[Dict[str, str], List[str]]:
    parse_text = resolve_translation_source_text(content, reasoning, thinking_summary)
    final_dict = build_translated_dict_from_text(parse_text)
    if final_dict:
        final_lyrics = merge_translated_dict_into_final_lyrics(
            final_dict, prompt_lines, line_prefixes
        )
        return final_dict, final_lyrics
    if parse_text.strip():
        fallback_lyrics = build_fallback_timestamped_lines(parse_text, line_prefixes)
        if fallback_lyrics:
            return {}, fallback_lyrics
    return {}, []


def merge_translated_dict_into_final_lyrics(
    translated_dict: Dict[str, str],
    prompt_lines: List[Dict[str, Any]],
    line_prefixes: List[str],
) -> List[str]:
    final_lyrics: List[str] = []
    for prompt_line in prompt_lines:
        translation = translated_dict.get(prompt_line['display_index'])
        if translation is None:
            continue
        entry_idx = max(0, int(prompt_line.get('entry_index', 1)) - 1)
        prefix = line_prefixes[entry_idx] if entry_idx < len(line_prefixes) else ''
        final_line = f"{prefix}{translation}" if prefix else translation
        final_lyrics.append(final_line)
    return final_lyrics


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


def _extract_lyrics_windows_from_meta(
    meta_value: Optional[str],
    json_meta: Optional[dict],
    padding_ms: int = 400,
    merge_gap_ms: int = 200,
    fallback_json_path: Optional[Path] = None
) -> List[Tuple[int, int]]:
    lyrics_url, _, _, _ = parse_meta_lyrics(meta_value)
    if not lyrics_url and json_meta and isinstance(json_meta, dict):
        lyrics_url = json_meta.get('lyrics', '') or ''
        lyrics_url, _, _, _ = parse_meta_lyrics(lyrics_url)
    if not lyrics_url:
        return []
    try:
        parsed = urlparse(str(lyrics_url))
        if parsed.scheme in ('http', 'https'):
            lyric_rel = _url_path_for_local_filesystem(str(lyrics_url)).lstrip('/')
        else:
            lyric_rel = parsed.path.lstrip('/')
        if not lyric_rel:
            return []
        lyrics_path = STATIC_DIR / lyric_rel
        if not lyrics_path.exists() and fallback_json_path:
            # Try resolving relative to JSON file directory for legacy paths.
            candidate = fallback_json_path.parent / lyric_rel
            if candidate.exists():
                lyrics_path = candidate
        if not lyrics_path.exists():
            return []
        with open(lyrics_path, 'r', encoding='utf-8-sig') as handle:
            lys_content = handle.read()
    except Exception:
        return []

    lines = parse_lys(lys_content)
    if not lines:
        return []

    windows: List[Tuple[int, int]] = []
    for line in lines:
        syllables = line.get('syllables') or []
        if not syllables:
            continue
        start_ms = min(int(float(item.get('startTime', 0)) * 1000) for item in syllables)
        end_ms = max(
            int((float(item.get('startTime', 0)) + float(item.get('duration', 0))) * 1000)
            for item in syllables
        )
        if end_ms <= start_ms:
            continue
        start_ms = max(0, start_ms - padding_ms)
        end_ms = max(start_ms + 1, end_ms + padding_ms)
        windows.append((start_ms, end_ms))

    if not windows:
        return []

    windows.sort(key=lambda item: item[0])
    merged: List[Tuple[int, int]] = []
    for start_ms, end_ms in windows:
        if not merged:
            merged.append((start_ms, end_ms))
            continue
        prev_start, prev_end = merged[-1]
        if start_ms <= prev_end + merge_gap_ms:
            merged[-1] = (prev_start, max(prev_end, end_ms))
        else:
            merged.append((start_ms, end_ms))
    return merged


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


_BACKGROUND_WEAVE_TAGS = frozenset({'[6]', '[7]', '[8]'})


def _lys_prefix_is_background_style(prefix: str) -> bool:
    return str(prefix or '').strip() in _BACKGROUND_WEAVE_TAGS


def _normalize_weave_background_prefix(raw: Any) -> str:
    s = str(raw or '').strip()
    if not s:
        return '[6]'
    m = re.fullmatch(r'\[(\d+)\]', s)
    if not m:
        return '[6]'
    n = int(m.group(1))
    if 1 <= n <= 9:
        return f'[{n}]'
    return '[6]'


def _weave_roman_lys_as_background(roman_lys_text: str, background_prefix: str = '[6]') -> Tuple[str, List[str]]:
    """
    Idempotent LYS helper aligned with roman assembly:
    - If a non-background line is already followed by a companion [6]/[7]/[8] line with matching
      timestamps, only normalize inter-token spaces on that background line.
    - Otherwise insert (or replace a mismatched background follower) using the main line's timings
      and token texts, with mandatory spaces between units on the background line.
    Meta lines and lines without tokens are left unchanged.
    """
    bg = _normalize_weave_background_prefix(background_prefix)
    try:
        doc = qe_parse_lys(roman_lys_text or '')
    except Exception as exc:
        return '', [f'解析 LYS 失败: {exc}']
    lines: List[Dict[str, Any]] = list(doc.get('lines', []) or [])
    doc['lines'] = lines
    work_indices = [
        i for i, line in enumerate(lines)
        if not line.get('is_meta')
        and not _lys_prefix_is_background_style(str(line.get('prefix') or ''))
        and (line.get('tokens') or [])
    ]
    for i in sorted(work_indices, reverse=True):
        line = lines[i]
        tokens = line.get('tokens') or []
        nxt_i = i + 1
        if nxt_i < len(lines):
            nxt = lines[nxt_i]
            if _lys_prefix_is_background_style(str(nxt.get('prefix') or '')):
                if _lyric_line_tokens_ts_match(line, nxt):
                    ntoks = nxt.get('tokens') or []
                    spaced = _roman_token_texts_with_spaces([str(t.get('text', '')) for t in ntoks])
                    for j, tok in enumerate(ntoks):
                        tok['text'] = spaced[j] if j < len(spaced) else str(tok.get('text', ''))
                    continue
                lines[nxt_i] = _build_lys_roman_background_line(
                    line, bg, [str(t.get('text', '')) for t in tokens]
                )
                continue
        lines.insert(i + 1, _build_lys_roman_background_line(
            line, bg, [str(t.get('text', '')) for t in tokens]
        ))
    return qe_dump_lys(doc), []


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
    """将选中的tokens按照目标位置移动

    该函数实现了快速编辑器中的拖拽移动功能，支持多种目标位置：
    - anchor: 移动到指定token的前后
    - newline: 移动到新行
    - line: 移动到行的开始或结束

    Args:
        doc: 文档对象
        selection: 选中的token列表
        target: 目标位置信息
        delete_empty_lines: 是否删除空行
    """
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
    """创建快速编辑器的错误响应

    Args:
        message: 错误消息
        status: HTTP状态码

    Returns:
        返回包含错误信息的纯文本响应
    """
    return PlainTextResponse(message, status_code=status)


def qe_register_doc(doc: Dict[str, Any], meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """注册文档到快速编辑器缓存并初始化撤销/重做栈

    Args:
        doc: 要注册的文档对象
        meta: 文档的元信息

    Returns:
        返回注册后的文档对象
    """
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


PROGRESSIVE_TIMESTAMP_TOKEN_RE = re.compile(r'([^(]*)\((\d+)\s*,\s*(\d+)\)')


def expand_progressive_timestamp_lines(lines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Expand inline timestamp lines, e.g.:
    "Do (34026,208)you (34234,192)plan (34426,272)"
    into progressive cumulative lines:
    "Do", "Do you", "Do you plan", ...
    """
    if not isinstance(lines, list) or not lines:
        return lines

    expanded_lines: List[Dict[str, Any]] = []

    for line in lines:
        if not isinstance(line, dict):
            expanded_lines.append(line)
            continue

        syllables = line.get('syllables') or []
        if not syllables:
            expanded_lines.append(line)
            continue

        raw_line = str(
            line.get('rawTimedLine')
            or line.get('line')
            or ''.join(str(s.get('text', '')) for s in syllables if isinstance(s, dict))
        )
        if '(' not in raw_line or ')' not in raw_line:
            expanded_lines.append(line)
            continue

        raw_body = raw_line
        marker_match = re.match(r'^\s*\[(\d*)\]\s*', raw_line)
        if marker_match:
            raw_body = raw_line[marker_match.end():]

        parts: List[Tuple[str, int, int]] = []
        for match in PROGRESSIVE_TIMESTAMP_TOKEN_RE.finditer(raw_body):
            fragment = (match.group(1) or '')
            start_ms = int(match.group(2))
            duration_ms = max(1, int(match.group(3)))
            if not fragment.strip():
                continue
            parts.append((fragment, start_ms, duration_ms))

        if len(parts) < 2:
            expanded_lines.append(line)
            continue

        base_syllable = copy.deepcopy(syllables[0]) if isinstance(syllables[0], dict) else {}
        cumulative = ''
        for fragment, start_ms, duration_ms in parts:
            cumulative = f"{cumulative}{fragment}"
            normalized = ' '.join(cumulative.split()).strip()
            full_text = normalized

            expanded_line = copy.deepcopy(line)
            expanded_line['line'] = full_text
            expanded_line['progressiveExpanded'] = True
            expanded_line['syllables'] = [{
                **base_syllable,
                'text': full_text,
                'startTime': start_ms / 1000.0,
                'duration': duration_ms / 1000.0
            }]
            # Set a deterministic disappearTime for this generated segment.
            expanded_line['disappearTime'] = start_ms + duration_ms
            expanded_lines.append(expanded_line)

    return expanded_lines


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
                'rawTimedLine': content,
                'marker': marker,
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


@app.route('/ai-romanization-workbench')
def ai_romanization_workbench():
    return render_template('lyrics_ai_romanization.html')


@app.route('/ai-translate-workbench')
def ai_translate_workbench():
    return render_template('lyrics_ai_translate.html')


@app.route('/LyricSphere.css')
def lyric_sphere_css():
    """Serve LyricSphere stylesheet from templates (paired with LyricSphere.html)."""
    return send_from_directory(BASE_PATH / 'templates', 'LyricSphere.css', mimetype='text/css')


_LYRIC_SPHERE_JS_ALLOWLIST = frozenset({
    'LyricSphere-i18n.js',
    'LyricSphere-runtime.js',
    'LyricSphere-cloud-player.js',
    'LyricSphere-library.js',
    'LyricSphere-appearance.js',
    'LyricSphere-lyrics-workbench.js',
    'LyricSphere-ai-presets.js',
    'LyricSphere-ai-romanization.js',
    'LyricSphere-ai-translate.js',
    'LyricSphere-admin-shell.js',
})


@app.route('/LyricSphere-js/<filename>')
def lyric_sphere_js_module(filename: str):
    """Serve split LyricSphere front-end scripts from templates with a fixed allowlist."""
    if filename not in _LYRIC_SPHERE_JS_ALLOWLIST:
        raise HTTPException(status_code=404)
    return send_from_directory(BASE_PATH / 'templates', filename, mimetype='application/javascript')


@app.route('/favicon.ico')
def favicon():
    """Serve a fallback favicon so browsers avoid repeated 404 requests."""
    fallback_ico = STATIC_DIR / 'favicon.ico'
    if fallback_ico.exists():
        return send_from_directory(STATIC_DIR, 'favicon.ico')
    return send_from_directory(STATIC_DIR / 'assets', 'icon-128x128.png', mimetype='image/png')


@app.route('/service-worker.js')
def service_worker():
    """Serve a legacy service worker to unregister the root scope registration."""
    return send_from_directory(STATIC_DIR / 'public', 'legacy-service-worker.js', mimetype='application/javascript')


@app.route('/amll-web/service-worker.js')
def amll_web_service_worker():
    """Serve the AMLL service worker directly from the Vite dist output."""
    dist_dir = get_amll_web_dist_dir()
    if not dist_dir.exists():
        return jsonify({
            'status': 'error',
            'message': 'AMLL web dist not found. Please run pnpm build in templates/amll-web.'
        }), 404

    primary_worker = dist_dir / 'service-worker.js'
    fallback_worker = dist_dir / 'public' / 'service-worker.js'

    if primary_worker.exists():
        return send_from_directory(dist_dir, 'service-worker.js', mimetype='application/javascript')

    if fallback_worker.exists():
        return send_from_directory(fallback_worker.parent, fallback_worker.name, mimetype='application/javascript')

    return jsonify({
        'status': 'error',
        'message': 'AMLL web dist not found. Please run pnpm build in templates/amll-web.'
    }), 404


@app.route('/amll-web')
@app.route('/amll-web/')
@app.route('/amll-web/index.html')
def amll_web_player():
    """Serve the AMLL web player entry from the Vite dist build."""
    dist_dir = get_amll_web_dist_dir()
    if not dist_dir.exists():
        return jsonify({
            'status': 'error',
            'message': 'AMLL web dist not found. Please run pnpm build in templates/amll-web.'
        }), 404

    index_file = dist_dir / 'index.html'
    if not index_file.exists():
        return jsonify({
            'status': 'error',
            'message': 'AMLL web dist not found. Please run pnpm build in templates/amll-web.'
        }), 404

    return send_from_directory(dist_dir, 'index.html')


@app.route('/amll-web/assets/<path:filename>')
def amll_web_assets(filename):
    """Serve AMLL asset files directly from the dist output with path safety checks."""
    dist_dir = get_amll_web_dist_dir()
    if not dist_dir.exists():
        return jsonify({
            'status': 'error',
            'message': 'AMLL web dist not found. Please run pnpm build in templates/amll-web.'
        }), 404

    assets_dir = (dist_dir / 'assets').resolve()
    safe_path = (assets_dir / filename).resolve()
    try:
        safe_path.relative_to(assets_dir)
    except ValueError:
        return jsonify({'status': 'error', 'message': 'Invalid path.'}), 400

    if not safe_path.exists() or not safe_path.is_file():
        return jsonify({'status': 'error', 'message': 'File not found.'}), 404

    return send_from_directory(assets_dir, filename)


@app.route('/amll-web/icons/<path:filename>')
def amll_web_icons(filename):
    """Serve AMLL icon files directly from the dist output with path safety checks."""
    dist_dir = get_amll_web_dist_dir()
    if not dist_dir.exists():
        return jsonify({
            'status': 'error',
            'message': 'AMLL web dist not found. Please run pnpm build in templates/amll-web.'
        }), 404

    icons_dir = (dist_dir / 'icons').resolve()
    safe_path = (icons_dir / filename).resolve()
    try:
        safe_path.relative_to(icons_dir)
    except ValueError:
        return jsonify({'status': 'error', 'message': 'Invalid path.'}), 400

    if not safe_path.exists() or not safe_path.is_file():
        return jsonify({'status': 'error', 'message': 'File not found.'}), 404

    return send_from_directory(icons_dir, filename)


@app.route('/amll-web/public/<path:filename>')
def amll_web_public(filename):
    """Serve AMLL public files directly from the dist output with path safety checks."""
    dist_dir = get_amll_web_dist_dir()
    if not dist_dir.exists():
        return jsonify({
            'status': 'error',
            'message': 'AMLL web dist not found. Please run pnpm build in templates/amll-web.'
        }), 404

    public_dir = (dist_dir / 'public').resolve()
    safe_path = (public_dir / filename).resolve()
    try:
        safe_path.relative_to(public_dir)
    except ValueError:
        return jsonify({'status': 'error', 'message': 'Invalid path.'}), 400

    if not safe_path.exists() or not safe_path.is_file():
        return jsonify({'status': 'error', 'message': 'File not found.'}), 404

    return send_from_directory(public_dir, filename)


@app.route('/assets/<path:filename>')
def amll_web_assets_legacy(filename):
    """Backward-compatible route to serve AMLL dist assets from the legacy /assets path."""
    dist_dir = get_amll_web_dist_dir()
    if not dist_dir.exists():
        return jsonify({
            'status': 'error',
            'message': 'AMLL web dist not found. Please run pnpm build in templates/amll-web.'
        }), 404

    assets_dir = (dist_dir / 'assets').resolve()
    safe_path = (assets_dir / filename).resolve()
    try:
        safe_path.relative_to(assets_dir)
    except ValueError:
        return jsonify({'status': 'error', 'message': 'Invalid path.'}), 400

    if not safe_path.exists() or not safe_path.is_file():
        return jsonify({'status': 'error', 'message': 'File not found.'}), 404

    return send_from_directory(assets_dir, filename)


@app.route('/icons/<path:filename>')
def amll_web_icons_legacy(filename):
    """Backward-compatible route to serve AMLL dist icons from the legacy /icons path."""
    dist_dir = get_amll_web_dist_dir()
    if not dist_dir.exists():
        return jsonify({
            'status': 'error',
            'message': 'AMLL web dist not found. Please run pnpm build in templates/amll-web.'
        }), 404

    icons_dir = (dist_dir / 'icons').resolve()
    safe_path = (icons_dir / filename).resolve()
    try:
        safe_path.relative_to(icons_dir)
    except ValueError:
        return jsonify({'status': 'error', 'message': 'Invalid path.'}), 400

    if not safe_path.exists() or not safe_path.is_file():
        return jsonify({'status': 'error', 'message': 'File not found.'}), 404

    return send_from_directory(icons_dir, filename)


@app.route('/public/<path:filename>')
def amll_web_public_legacy(filename):
    """Backward-compatible route to serve AMLL dist public files from the legacy /public path."""
    dist_dir = get_amll_web_dist_dir()
    if not dist_dir.exists():
        return jsonify({
            'status': 'error',
            'message': 'AMLL web dist not found. Please run pnpm build in templates/amll-web.'
        }), 404

    public_dir = (dist_dir / 'public').resolve()
    safe_path = (public_dir / filename).resolve()
    try:
        safe_path.relative_to(public_dir)
    except ValueError:
        return jsonify({'status': 'error', 'message': 'Invalid path.'}), 400

    if not safe_path.exists() or not safe_path.is_file():
        return jsonify({'status': 'error', 'message': 'File not found.'}), 404

    return send_from_directory(public_dir, filename)


@app.route('/index.html')
def index_html_alias():
    """Backward compatibility for clients requesting the original index.html entry."""
    return amll_web_player()


def get_amll_web_dist_dir() -> Path:
    """Return the dist directory for the AMLL web Vite build."""
    return BASE_PATH / 'templates' / 'amll-web' / 'dist'


def _get_latest_file_by_pattern(dir_path: Path, pattern: str) -> Optional[Path]:
    """Return the newest file matching pattern inside dir_path, or None."""
    if not dir_path.exists() or not dir_path.is_dir():
        return None

    matched = sorted(dir_path.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    if not matched:
        return None
    return matched[0]


def get_amll_entry_assets() -> Dict[str, str]:
    """Resolve AMLL entry JS/CSS URLs with dist-first, static fallback strategy."""
    js_url = url_for('static', filename='assets/amll-player.js')
    css_url = url_for('static', filename='assets/amll-player.css')

    dist_dir = get_amll_web_dist_dir()
    assets_dir = dist_dir / 'assets'
    if assets_dir.exists() and assets_dir.is_dir():
        base_url = get_amll_web_player_base_url()
        latest_js = _get_latest_file_by_pattern(assets_dir, 'index-*.js')
        latest_css = _get_latest_file_by_pattern(assets_dir, 'index-*.css')
        if latest_js:
            js_url = f"{base_url}assets/{latest_js.name}"
        if latest_css:
            css_url = f"{base_url}assets/{latest_css.name}"

    return {'js': js_url, 'css': css_url}


def get_amll_static_entry_assets() -> Dict[str, str]:
    """Resolve AMLL entry assets from the stable static bundle."""
    return {
        'js': url_for('static', filename='assets/amll-player.js'),
        'css': url_for('static', filename='assets/amll-player.css')
    }


def get_lyric_sphere_v2_dist_dir() -> Path:
    """Return the dist directory for the LyricSphere v2 frontend build."""
    return BASE_PATH / 'templates' / 'lyric-sphere-v2' / 'dist'


@app.route('/update-screen')
@app.route('/update-screen/')
def update_screen_frontend():
    """Serve the standalone browser resource update screen."""
    dist_dir = get_lyric_sphere_v2_dist_dir()
    if not dist_dir.exists():
        return jsonify({'status': 'error', 'message': 'LyricSphere v2 dist not found. Please run npm run build.'}), 404

    update_screen_file = dist_dir / 'update-screen.html'
    if not update_screen_file.exists():
        return jsonify({'status': 'error', 'message': 'Update screen build not found. Please run npm run build.'}), 404

    return send_from_directory(dist_dir, 'update-screen.html')


@app.route('/lyric-sphere-v2')
@app.route('/lyric-sphere-v2/')
@app.route('/lyric-sphere-v2/<path:filename>')
def lyric_sphere_v2_frontend(filename: str = 'index.html'):
    """Serve the LyricSphere v2 React build from the dist directory."""
    dist_dir = get_lyric_sphere_v2_dist_dir()
    if not dist_dir.exists():
        return jsonify({'status': 'error', 'message': 'LyricSphere v2 dist not found. Please run npm run build.'}), 404

    safe_path = (dist_dir / filename).resolve()
    if dist_dir not in safe_path.parents and safe_path != dist_dir:
        return jsonify({'status': 'error', 'message': 'Invalid path.'}), 400

    if safe_path.exists() and safe_path.is_file():
        return send_from_directory(dist_dir, filename)

    return send_from_directory(dist_dir, 'index.html')


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
    """获取快速编辑器文档

    Args:
        doc_id: 文档ID

    Returns:
        返回元组 (文档对象, 错误响应)
    """
    doc = QUICK_EDITOR_DOCS.get(doc_id)
    if not doc:
        return None, qe_error_response('document not found', 404)
    return doc, None


def _qe_prepare_mutation(doc_id: str, base_version: Optional[int]) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[Response]]:
    """准备文档变更操作，验证版本并创建备份

    Args:
        doc_id: 文档ID
        base_version: 基础版本号

    Returns:
        返回元组 (文档对象, 备份文档, 错误响应)
    """
    doc = QUICK_EDITOR_DOCS.get(doc_id)
    if not doc:
        return None, None, qe_error_response('document not found', 404)
    if doc.get('version') != base_version:
        return None, None, qe_error_response('version conflict', 409)
    before = qe_clone(doc)
    return doc, before, None


def quick_editor_load_payload(json_file: str) -> Tuple[Optional[Dict[str, Any]], Optional[Tuple[str, int]]]:
    """加载并准备快速编辑所需数据，返回 (payload, error)。error: (message, status)."""
    try:
        json_file, json_path = _resolve_existing_static_json_filename(json_file)
    except ValueError as exc:
        return None, (str(exc), 400)

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
        base_name = sanitize_filename(meta.get('title', '')) or f"lyrics_{int(time.time())}"
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
        upsert_song_search_index_for_path(json_path)

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
    return PlainTextResponse(qe_dump_lys(doc), media_type='text/plain; charset=utf-8')


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

    json_path_hint = meta.get('json_path')
    if json_path_hint and Path(json_path_hint).is_file():
        related_json_paths = [str(Path(json_path_hint).resolve())]
    else:
        related_json_paths = find_related_json(str(lyrics_path))
    for jp in related_json_paths:
        upsert_song_search_index_for_path(Path(jp))

    try:
        lyrics_relative = resource_relative_from_path(lyrics_path, 'songs')
        lyrics_url = build_public_url('songs', lyrics_relative)
    except Exception:
        lyrics_url = str(lyrics_path)

    return jsonify({'status': 'success', 'lyricsPath': lyrics_url})

# Register a custom Jinja2 filter for the compatibility layer
@app.template_filter('escape_js')
def escape_js_filter(s):
    return json.dumps(str(s))[1:-1]  # 移除外层的引号


@app.route('/backup_file', methods=['POST'])
def backup_file():
    """备份指定文件

    接收文件路径，创建该文件的时间戳备份。备份文件保存在backups目录下，
    保持与原文件相同的目录结构。

    Returns:
        JSON响应，包含备份状态
    """
    if not is_request_allowed():
        return abort(403)
    file_path = request.json.get('file_path')
    if not BACKUP_DIR.exists():
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime(BACKUP_TIMESTAMP_FORMAT)
    backup_path = build_backup_path(Path(file_path), timestamp)
    shutil.copy2(file_path, backup_path)
    return jsonify({'status': 'success'})


@app.route('/backup_client_state', methods=['POST'])
def backup_client_state():
    if not is_request_allowed():
        return abort(403)
    payload = request.get_json(silent=True) or {}
    client_id = _sanitize_client_id(payload.get('client_id') or payload.get('clientId'))
    anchor_id = _sanitize_client_id(payload.get('anchor_id') or payload.get('anchorId'))
    data = payload.get('data')
    if data is None:
        return jsonify({'status': 'error', 'message': '缺少 data'}), 400
    if not client_id and not anchor_id:
        return jsonify({'status': 'error', 'message': '缺少 client_id 或 anchor_id'}), 400

    if anchor_id:
        backup_dir = BACKUP_DIR / 'anchors'
        filename = f"{anchor_id}.json"
    else:
        backup_dir = BACKUP_DIR / 'clients'
        filename = f"{client_id}.json"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / filename
    data = _normalize_backup_payload_data(data)
    payload['data'] = data

    envelope = {
        'client_id': client_id or None,
        'anchor_id': anchor_id or None,
        'saved_at': datetime.now().isoformat(),
        'source': 'lyric-sphere-v2',
        'payload': payload
    }
    try:
        with _get_backup_write_lock(backup_path):
            _write_json_atomically(backup_path, envelope)
    except Exception as exc:
        app.logger.exception(
            "保存备份失败: anchor_id=%s client_id=%s path=%s",
            anchor_id or '',
            client_id or '',
            backup_path
        )
        return jsonify({'status': 'error', 'message': f'备份保存失败: {exc}'}), 500

    try:
        if anchor_id:
            public_path = build_public_url('backups', f"anchors/{anchor_id}.json")
        else:
            public_path = build_public_url('backups', f"clients/{client_id}.json")
    except Exception:
        public_path = str(backup_path)

    return jsonify({
        'status': 'success',
        'message': '备份已保存',
        'path': public_path,
        'anchorId': anchor_id or None
    })


@app.route('/anchor_backup', methods=['POST'])
def anchor_backup():
    if not is_request_allowed():
        return abort(403)
    payload = request.get_json(silent=True) or {}
    account = str(payload.get('account') or '').strip()
    password = str(payload.get('password') or '').strip()
    if not account or not password:
        return jsonify({'status': 'error', 'message': '缺少账号或密码'}), 400

    anchor_id = _build_anchor_id(account, password)
    return jsonify({'status': 'success', 'anchorId': anchor_id})


@app.route('/download_client_backup', methods=['GET'])
def download_client_backup():
    if not is_request_allowed():
        return abort(403)

    raw_id = request.args.get('client_id') or ''
    client_id = _sanitize_client_id(raw_id)
    if not client_id:
        return jsonify({'status': 'error', 'message': '缺少 client_id'}), 400

    backup_path = BACKUP_DIR / 'clients' / f"{client_id}.json"
    if not backup_path.exists():
        return jsonify({'status': 'error', 'message': '备份文件不存在'}), 404

    return send_file(
        backup_path,
        as_attachment=True,
        download_name=f"lyric-sphere-backup-{client_id}.json",
        mimetype='application/json'
    )


@app.route('/download_anchor_backup', methods=['GET'])
def download_anchor_backup():
    if not is_request_allowed():
        return abort(403)

    raw_id = request.args.get('anchor_id') or ''
    anchor_id = _sanitize_client_id(raw_id)
    if not anchor_id:
        return jsonify({'status': 'error', 'message': '缺少 anchor_id'}), 400

    backup_path = BACKUP_DIR / 'anchors' / f"{anchor_id}.json"
    if not backup_path.exists():
        return jsonify({'status': 'error', 'message': '备份文件不存在'}), 404

    return send_file(
        backup_path,
        as_attachment=True,
        download_name=f"lyric-sphere-backup-{anchor_id}.json",
        mimetype='application/json'
    )


@app.route('/get_anchor_backup', methods=['GET'])
def get_anchor_backup():
    if not is_request_allowed():
        return abort(403)

    raw_id = request.args.get('anchor_id') or ''
    anchor_id = _sanitize_client_id(raw_id)
    if not anchor_id:
        return jsonify({'status': 'error', 'message': '缺少 anchor_id'}), 400

    backup_path = BACKUP_DIR / 'anchors' / f"{anchor_id}.json"
    if not backup_path.exists():
        return jsonify({'status': 'error', 'message': '备份文件不存在'}), 404

    try:
        with open(backup_path, 'r', encoding='utf-8') as f:
            content = json.load(f)
    except json.JSONDecodeError as exc:
        app.logger.error(
            "锚点备份文件损坏: anchor_id=%s path=%s error=%s line=%s col=%s",
            anchor_id,
            backup_path,
            exc.msg,
            exc.lineno,
            exc.colno
        )
        return jsonify({'status': 'error', 'message': '备份文件损坏：不是合法的 JSON'}), 500
    except Exception as exc:
        app.logger.exception(
            "读取锚点备份失败: anchor_id=%s path=%s",
            anchor_id,
            backup_path
        )
        return jsonify({'status': 'error', 'message': f'备份文件读取失败: {exc}'}), 500

    payload = content.get('payload', {}) if isinstance(content, dict) else {}
    data = payload.get('data', {}) if isinstance(payload, dict) else {}
    data = _normalize_backup_payload_data(data)

    return jsonify({
        'status': 'success',
        'anchorId': anchor_id,
        'savedAt': content.get('saved_at'),
        'data': data
    })


@app.route('/delete_json', methods=['POST'])
def delete_json():
    if not is_request_allowed():
        return abort(403)
    locked_response = require_unlocked_device('删除歌曲')
    if locked_response:
        return locked_response
    data = request.get_json(silent=True) or {}

    try:
        _, json_path = _resolve_existing_static_json_filename(data.get('filename'))
    except ValueError as exc:
        return jsonify({'status': 'error', 'message': str(exc)})

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
            deleted_name = json_path.name
            try:
                remove_song_search_index_entry(deleted_name)
            except Exception as exc:
                app.logger.error(
                    'song search index: remove after delete failed for %s: %s',
                    deleted_name,
                    exc,
                    exc_info=True,
                )
                return jsonify({
                    'status': 'error',
                    'message': (
                        '歌曲 JSON 已删除，但搜索索引更新失败；请稍后重试或调用 '
                        'POST /internal/rebuild_song_search_index 修复索引。'
                        f' 详情: {exc}'
                    ),
                }), 500

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
    try:
        _, json_path = _resolve_existing_static_json_filename(payload.get('filename'))
    except ValueError as exc:
        return jsonify({'status': 'error', 'message': str(exc)}), 400

    if not json_path.exists():
        return jsonify({'status': 'error', 'message': 'JSON 文件不存在'}), 404

    try:
        with open(json_path, 'r', encoding='utf-8') as json_file:
            json_data = json.load(json_file)
    except Exception as exc:
        return jsonify({'status': 'error', 'message': f'读取 JSON 失败: {exc}'}), 500

    referenced_assets = collect_song_resource_paths(json_data)
    
    # 检查所有.lys文件，提取字体文件引用
    font_files: Set[str] = set()
    for asset in referenced_assets:
        if asset.lower().endswith('.lys'):
            lys_path = SONGS_DIR / asset
            if lys_path.exists():
                try:
                    with open(lys_path, 'r', encoding='utf-8') as lys_file:
                        lys_content = lys_file.read()
                    extracted_fonts = extract_font_files_from_lys(lys_content)
                    font_files.update(extracted_fonts)
                except Exception as exc:
                    app.logger.warning(f"读取LYS文件 {asset} 失败: {exc}")
    
    # 查找字体文件（支持常见的字体文件扩展名）
    font_extensions = {'.ttf', '.otf', '.woff', '.woff2', '.eot'}
    font_assets: Set[str] = set()
    for font_name in font_files:
        for ext in font_extensions:
            font_file = f"{font_name}{ext}"
            font_path = SONGS_DIR / font_file
            if font_path.exists():
                font_assets.add(font_file)
                break
    
    # 合并所有资源文件
    referenced_assets.update(font_assets)
    
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


@app.route('/export_static_full/start', methods=['POST'])
def export_static_full_start():
    if not is_request_allowed():
        return abort(403)
    locked_response = require_unlocked_device('打包下载全部资源')
    if locked_response:
        return locked_response

    device_id = request.cookies.get('FEW_DEVICE_ID')
    if not device_id:
        device_id = str(uuid.uuid4())

    reusable_task = _find_reusable_static_export_task(device_id)
    if reusable_task:
        task_id, task = reusable_task
        response = jsonify({
            'status': 'success',
            'reused': True,
            'task': _build_static_export_task_snapshot(task_id, task)
        })
        response.set_cookie(
            'FEW_DEVICE_ID',
            device_id,
            httponly=True,
            samesite='Lax',
            max_age=365 * 24 * 3600
        )
        return response

    task_id, task = _start_static_export_task(device_id)
    response = jsonify({
        'status': 'success',
        'reused': False,
        'task_id': task_id,
        'task': _build_static_export_task_snapshot(task_id, task)
    })
    response.set_cookie(
        'FEW_DEVICE_ID',
        device_id,
        httponly=True,
        samesite='Lax',
        max_age=365 * 24 * 3600
    )
    return response


@app.route('/export_static_full/status', methods=['GET'])
def export_static_full_status():
    if not is_request_allowed():
        return abort(403)
    locked_response = require_unlocked_device('查看整包导出进度')
    if locked_response:
        return locked_response

    task_id = (request.args.get('task_id') or '').strip()
    if not task_id:
        return jsonify({'status': 'error', 'message': '请提供任务ID'}), 400

    device_id = request.cookies.get('FEW_DEVICE_ID')
    if not device_id:
        return jsonify({'status': 'error', 'message': '设备未解锁'}), 403

    with STATIC_EXPORT_LOCK:
        task = STATIC_EXPORT_TASKS.get(task_id)
        if not task or task.get('owner_device_id') != device_id:
            return jsonify({'status': 'error', 'message': '任务不存在'}), 404
        task_snapshot = _build_static_export_task_snapshot(task_id, task)

    return jsonify({
        'status': 'success',
        'task': task_snapshot,
    })


@app.route('/export_static_full/download', methods=['GET'])
def export_static_full_download():
    if not is_request_allowed():
        return abort(403)
    locked_response = require_unlocked_device('下载整包资源')
    if locked_response:
        return locked_response

    task_id = (request.args.get('task_id') or '').strip()
    if not task_id:
        return jsonify({'status': 'error', 'message': '请提供任务ID'}), 400

    device_id = request.cookies.get('FEW_DEVICE_ID')
    if not device_id:
        return jsonify({'status': 'error', 'message': '设备未解锁'}), 403

    with STATIC_EXPORT_LOCK:
        task = STATIC_EXPORT_TASKS.get(task_id)
        if not task or task.get('owner_device_id') != device_id:
            return jsonify({'status': 'error', 'message': '任务不存在'}), 404
        task_snapshot = _build_static_export_task_snapshot(task_id, task)

    if task_snapshot['status'] != 'done':
        return jsonify({'status': 'error', 'message': '任务尚未完成'}), 409

    archive_path = Path(task.get('download_path', ''))
    if not archive_path.exists():
        return jsonify({'status': 'error', 'message': '导出文件已失效'}), 404

    return send_file(
        archive_path,
        mimetype='application/zip',
        as_attachment=True,
        download_name=task.get('archive_name', 'static-full.zip')
    )


@app.route('/import_static', methods=['POST'])
def import_static_bundle():
    if not is_request_allowed():
        return abort(403)

    upload = request.files.get('file')
    if not upload or not upload.filename:
        return jsonify({'status': 'error', 'message': '请上传 static.zip 文件'}), 400

    file_bytes = upload.read()
    if not file_bytes:
        return jsonify({'status': 'error', 'message': '上传文件为空'}), 400

    buffer = BytesIO(file_bytes)
    imported_jsons: List[str] = []
    imported_assets = 0
    renamed_files: List[str] = []

    try:
        with zipfile.ZipFile(buffer) as archive:
            entries: List[Tuple[zipfile.ZipInfo, str, Path, bool]] = []
            reserved_paths: Set[str] = set()
            rename_map: Dict[str, str] = {}

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
                resolved_path, renamed = _resolve_import_target(target_path, reserved_paths)
                resolved_relative = str(resolved_path.relative_to(STATIC_DIR)).replace('\\', '/')
                if renamed:
                    rename_map[relative_path] = resolved_relative
                    renamed_files.append(resolved_relative)

                entries.append((info, relative_path, resolved_path, is_json_file))

            for info, relative_path, target_path, is_json_file in entries:
                with archive.open(info) as source:
                    if is_json_file:
                        raw = source.read()
                        try:
                            payload = json.loads(raw.decode('utf-8'))
                            if rename_map:
                                payload = _replace_import_paths(payload, rename_map)
                            with open(target_path, 'w', encoding='utf-8') as destination:
                                json.dump(payload, destination, ensure_ascii=False, indent=2)
                        except Exception:
                            with open(target_path, 'wb') as destination:
                                destination.write(raw)
                        imported_jsons.append(target_path.name)
                    else:
                        with open(target_path, 'wb') as destination:
                            shutil.copyfileobj(source, destination)
                        imported_assets += 1

    except zipfile.BadZipFile:
        return jsonify({'status': 'error', 'message': '文件不是有效的 ZIP 压缩包'}), 400

    if not imported_jsons:
        return jsonify({'status': 'error', 'message': '压缩包中未发现 JSON 文件'}), 400

    rebuild_song_search_index_full()

    message = f'导入完成。JSON: {len(imported_jsons)} 个，资源文件: {imported_assets} 个'
    if renamed_files:
        message += f'，重名处理: {len(renamed_files)} 个'

    return jsonify({
        'status': 'success',
        'message': message,
        'jsonFiles': imported_jsons,
        'renamed': renamed_files
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
            if restore_path.suffix.lower() == '.json' and restore_path.name.lower() != 'artists.json':
                try:
                    restore_path.resolve().relative_to(STATIC_DIR.resolve())
                    upsert_song_search_index_for_path(restore_path)
                except ValueError:
                    pass
        else:
            target_path = resolve_resource_path(file_path, 'static')
            if not target_path.exists():
                return jsonify({'status': 'error', 'message': '目标文件不存在'})

            # 获取所有关联文件备份
            related_files = get_related_files(target_path)  # 新增关联文件获取方法
            backups = []

            # 为每个关联文件创建恢复任务
            for file in related_files:
                file_backups = iter_backup_files(Path(file))

                if not file_backups:
                    continue

                latest_backup = file_backups[0]
                shutil.copy2(latest_backup, file)  # 恢复文件
                try:
                    fp = Path(file).resolve()
                    if fp.suffix.lower() == '.json' and fp.name.lower() != 'artists.json':
                        fp.relative_to(STATIC_DIR.resolve())
                        upsert_song_search_index_for_path(fp)
                except ValueError:
                    pass

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
    data = request.get_json(silent=True) or {}

    try:
        filename, file_path = _resolve_existing_static_json_filename(data.get('filename'))
    except ValueError as exc:
        return jsonify({'status': 'error', 'message': str(exc)})

    try:
        # 确保备份目录存在
        if not BACKUP_DIR.exists():
            BACKUP_DIR.mkdir(parents=True, exist_ok=True)

        # 备份原文件
        backup_path = build_backup_path(file_path, int(time.time()))
        if file_path.exists():  # 只在文件存在时进行备份
            shutil.copy2(file_path, backup_path)

        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data['content'], f, ensure_ascii=False, indent=2)

        upsert_song_search_index_for_path(file_path)
        return jsonify({'status': 'success', 'filename': filename})
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

        related_json_paths: List[str] = []

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
            related_json_paths = _resolve_related_json_paths(file_path, data.get('jsonFile'))
            for json_file in related_json_paths:
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
                    
                backups = iter_backup_files(file_path)
                
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
            if not related_json_paths and '/songs/' in data['path']:
                related_json_paths = _resolve_related_json_paths(file_path, data.get('jsonFile'))
            for jp in related_json_paths:
                upsert_song_search_index_for_path(Path(jp))
            return jsonify({'status': 'success'})
        except Exception as e:
            app.logger.error(f"保存文件失败: {file_path}, 错误: {str(e)}, 权限: {oct(file_path.parent.stat().st_mode)[-3:] if file_path.parent.exists() else 'N/A'}")
            return jsonify({'status': 'error', 'message': str(e)})
    except Exception as e:
        app.logger.error(f"处理保存请求时出错: {str(e)}")
        return jsonify({'status': 'error', 'message': f'处理请求时出错: {str(e)}'})


def _normalize_lyrics_field_to_songs_relative(field_value: str) -> Optional[str]:
    """Normalize a meta.lyrics field value to a songs/ relative path for index lookup."""
    if not field_value or field_value == '!':
        return None
    parsed = urlparse(str(field_value))
    if parsed.scheme in ('http', 'https'):
        candidate = _url_path_for_local_filesystem(str(field_value))
    else:
        candidate = parsed.path if parsed.scheme else str(field_value)
    candidate = unquote(candidate.replace('\\', '/')).strip()
    if not candidate:
        return None
    while candidate.startswith('./'):
        candidate = candidate[2:]
    candidate = candidate.lstrip('/')
    lower_candidate = candidate.lower()
    if lower_candidate.startswith('static/'):
        candidate = candidate[len('static/'):].lstrip('/')
        lower_candidate = candidate.lower()
    if lower_candidate.startswith('songs/'):
        candidate = candidate[len('songs/'):].lstrip('/')
    candidate = candidate.split('?', 1)[0].strip()
    if not candidate:
        return None
    try:
        return _normalize_relative_path(candidate)
    except ValueError:
        return None


def _lyrics_resource_keys_from_summary(summary: Dict[str, Any]) -> Set[str]:
    keys: Set[str] = set()
    for field in ('lyricsPath', 'translationPath', 'romanPath'):
        rel = _normalize_lyrics_field_to_songs_relative(str(summary.get(field) or ''))
        if rel:
            keys.add(rel)
    return keys


def _resolve_related_json_paths(lyrics_path: Union[str, Path],
                                json_file_hint: Optional[str] = None) -> List[str]:
    if json_file_hint:
        try:
            _, json_path = _resolve_existing_static_json_filename(json_file_hint)
            if json_path.is_file():
                return [str(json_path.resolve())]
        except ValueError:
            pass
    return find_related_json(str(lyrics_path))


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

    lyrics_relative_key = str(lyrics_relative).replace('\\', '/')

    with _song_search_index_lock:
        if _lyrics_resource_index_initialized:
            json_names = _lyrics_resource_index.get(lyrics_relative_key, set())
            for json_name in json_names:
                json_file = static_dir / json_name
                if json_file.is_file():
                    related_jsons.append(str(json_file.resolve()))
            return related_jsons

    def _field_matches(field_value: str) -> bool:
        rel = _normalize_lyrics_field_to_songs_relative(field_value)
        return rel == lyrics_relative_key

    for json_file in static_dir.iterdir():
        if json_file.suffix != '.json' or json_file.name.lower() == 'artists.json':
            continue
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            meta = data.get('meta')
            if not isinstance(meta, dict):
                continue
            lyrics_fields = str(meta.get('lyrics', '') or '').split('::')
            if any(_field_matches(field) for field in lyrics_fields):
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
    data = request.get_json(silent=True) or {}
    file_type = data.get('fileType')
    new_path = data.get('newPath')

    try:
        _, json_path = _resolve_existing_static_json_filename(data.get('jsonFile'))
    except ValueError as exc:
        return jsonify({'status': 'error', 'message': str(exc)})

    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            json_data = json.load(f)

        # 确保备份目录存在
        if not BACKUP_DIR.exists():
            BACKUP_DIR.mkdir(parents=True, exist_ok=True)

        # 备份原文件
        timestamp = int(time.time())
        backup_path = build_backup_path(json_path, timestamp)
        if json_path.exists():  # 只在文件存在时进行备份
            shutil.copy2(json_path, backup_path)

        is_url = new_path and (new_path.startswith('http://') or new_path.startswith('https://'))
        normalized_new_path = ''
        if new_path:
            if is_url:
                normalized_new_path = new_path
            else:
                try:
                    normalized_new_path = _normalize_relative_path(new_path)
                except ValueError:
                    return jsonify({'status': 'error', 'message': '文件路径包含非法字符'})

        # 更新路径
        if file_type == 'music':
            if is_url:
                json_data['song'] = normalized_new_path
            else:
                json_data['song'] = build_public_url('songs', normalized_new_path)
        elif file_type == 'image':
            if is_url:
                json_data['meta']['albumImgSrc'] = normalized_new_path
            else:
                json_data['meta']['albumImgSrc'] = build_public_url('songs', normalized_new_path)
        elif file_type == 'background':
            meta = json_data.setdefault('meta', {})
            if new_path:
                if is_url:
                    meta['Background-image'] = normalized_new_path
                else:
                    normalized_background_path = normalized_new_path
                    if normalized_background_path.startswith('songs/'):
                        normalized_background_path = normalized_background_path[len('songs/'):]
                    meta['Background-image'] = f"./songs/{normalized_background_path}" if normalized_background_path else ''
            else:
                meta['Background-image'] = ''
        elif file_type == 'dynamicCover':
            meta = json_data.setdefault('meta', {})
            if new_path:
                if is_url:
                    meta['dynamicCoverSrc'] = normalized_new_path
                else:
                    normalized_dynamic_path = normalized_new_path
                    if normalized_dynamic_path.startswith('songs/'):
                        normalized_dynamic_path = normalized_dynamic_path[len('songs/'):]
                    meta['dynamicCoverSrc'] = f"./songs/{normalized_dynamic_path}" if normalized_dynamic_path else ''
            else:
                meta['dynamicCoverSrc'] = ''
        elif file_type == 'dynamicCoverPoster':
            meta = json_data.setdefault('meta', {})
            if new_path:
                if is_url:
                    meta['dynamicCoverPoster'] = normalized_new_path
                else:
                    normalized_poster_path = normalized_new_path
                    if normalized_poster_path.startswith('songs/'):
                        normalized_poster_path = normalized_poster_path[len('songs/'):]
                    meta['dynamicCoverPoster'] = f"./songs/{normalized_poster_path}" if normalized_poster_path else ''
            else:
                meta['dynamicCoverPoster'] = ''
        elif file_type == 'lyrics':
            current_lyrics = json_data['meta']['lyrics'].split('::')
            if len(current_lyrics) >= 4:
                if data.get('index') == 0:  # 歌词文件
                    if is_url:
                        new_lyrics_path = normalized_new_path if new_path else '!'
                    else:
                        new_lyrics_path = build_public_url('songs', normalized_new_path) if new_path else '!'
                    current_lyrics[1] = new_lyrics_path
                elif data.get('index') == 1:  # 歌词翻译
                    if is_url:
                        new_translation_path = normalized_new_path if new_path else '!'
                    else:
                        new_translation_path = build_public_url('songs', normalized_new_path) if new_path else '!'
                    current_lyrics[2] = new_translation_path
                elif data.get('index') == 2:  # 歌词音译
                    if is_url:
                        new_transliteration_path = normalized_new_path if new_path else '!'
                    else:
                        new_transliteration_path = build_public_url('songs', normalized_new_path) if new_path else '!'
                    current_lyrics[3] = new_transliteration_path
                json_data['meta']['lyrics'] = '::'.join(current_lyrics)

        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(json_data, f, ensure_ascii=False, indent=2)

        upsert_song_search_index_for_path(json_path)

        # 在更新路径后添加文件创建逻辑（仅对本地路径执行）
        if normalized_new_path and not is_url:
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
    data = request.get_json(silent=True) or {}

    try:
        filename, file_path = _resolve_new_static_json_filename(data.get('filename'))
    except ValueError as exc:
        return jsonify({'status': 'error', 'message': str(exc)})

    try:
        if file_path.exists():
            return jsonify({'status': 'error', 'message': '文件已存在！'})

        file_path.parent.mkdir(parents=True, exist_ok=True)
        print(f"创建JSON文件: {file_path}")

        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data['content'], f, ensure_ascii=False, indent=2)

        upsert_song_search_index_for_path(file_path)
        return jsonify({'status': 'success', 'filename': filename})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})


@app.route('/rename_json', methods=['POST'])
def rename_json():
    if not is_request_allowed():
        return abort(403)
    locked_response = require_unlocked_device('重命名歌曲')
    if locked_response:
        return locked_response
    data = request.get_json(silent=True) or {}
    title = (data.get('title') or '').strip()
    artists_raw = data.get('artists') or []
    artists = [str(artist).strip() for artist in artists_raw if str(artist).strip()]

    try:
        old_filename, old_path = _resolve_existing_static_json_filename(data.get('oldFilename'))
        new_filename, new_path = _resolve_new_static_json_filename(data.get('newFilename'))
    except ValueError as exc:
        return jsonify({'status': 'error', 'message': str(exc)})

    if not title or not artists:
        return jsonify({'status': 'error', 'message': '歌曲名和歌手名不能为空'})

    try:
        if not old_path.exists():
            return jsonify({'status': 'error', 'message': '原文件不存在！'})

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
        backup_path = build_backup_path(old_path, timestamp)
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

        if old_path.name.lower() != new_path.name.lower():
            remove_song_search_index_entry(old_path.name)
        upsert_song_search_index_for_path(new_path)

        return jsonify({'status': 'success', 'filename': new_filename})
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


def collect_static_json_file_paths() -> List[Path]:
    """在 static 目录内罗列 json 文件路径，多策略容错，兼容 Windows（不读取文件内容）。"""

    collected: Dict[str, Path] = {}

    def add_paths(paths: Iterable[Path], label: str) -> None:
        for p in paths:
            try:
                if not p.exists() or not p.is_file():
                    continue
                if not p.name.lower().endswith('.json'):
                    continue
                collected[p.name] = p
            except OSError as exc:
                app.logger.warning("%s 路径检查失败 %s: %s", label, p, exc)

    try:
        add_paths(STATIC_DIR.glob('*.json'), 'glob')
    except OSError as exc:
        app.logger.warning("glob static/*.json 失败，进入降级：%s", exc)

    try:
        with os.scandir(STATIC_DIR) as it:
            for entry in it:
                try:
                    if entry.is_file() and entry.name.lower().endswith('.json'):
                        add_paths([Path(entry.path)], 'scandir')
                except OSError as exc:
                    app.logger.warning("scandir 处理 %s 失败: %s", entry.name, exc)
    except OSError as exc:
        app.logger.warning("scandir static 失败：%s", exc)

    try:
        if os.name == 'nt':
            cmd = ['cmd', '/c', 'dir', '/b', '*.json']
            proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(STATIC_DIR))
        else:
            cmd = ['find', '.', '-maxdepth', '1', '-type', 'f', '-name', '*.json', '-print']
            proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(STATIC_DIR))

        if proc.stdout:
            lines = proc.stdout.splitlines()
            parsed: List[Path] = []
            for line in lines:
                name = line.strip()
                if not name:
                    continue
                if os.name != 'nt' and name.startswith('./'):
                    name = name[2:]
                parsed.append(STATIC_DIR / name)
            add_paths(parsed, 'fallback-cmd')

        if proc.returncode != 0:
            app.logger.warning("fallback 命令 %s 返回码 %s, stderr=%s", cmd[0], proc.returncode, proc.stderr.strip())
    except FileNotFoundError:
        app.logger.warning("fallback 命令不可用（%s），跳过", 'cmd/dir' if os.name == 'nt' else 'find')
    except Exception as exc:
        app.logger.warning("fallback 命令列举 static/*.json 异常：%s", exc)

    return list(collected.values())


def _build_song_summary_from_static_json(file: Path) -> Optional[Dict[str, Any]]:
    """从 static 下单个 JSON 文件构建歌曲摘要；失败返回 None。"""
    try:
        raw_data = json.loads(file.read_text(encoding='utf-8'))
    except Exception as exc:
        app.logger.warning("读取 JSON 失败，已跳过 %s: %s", file.name, exc)
        return None

    if not isinstance(raw_data, dict):
        app.logger.warning("JSON 非对象，已跳过 %s", file.name)
        return None

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

    try:
        mtime_val = file.stat().st_mtime
    except OSError:
        mtime_val = 0.0

    song_value = str(raw_data.get('song', '')).strip()
    return {
        'filename': file.name,
        'title': meta.get('title', ''),
        'artists': artists_list,
        'lyricsPath': lyrics_path,
        'translationPath': translation_path,
        'romanPath': roman_path,
        'metaLyrics': meta.get('lyrics', ''),
        'song': song_value,
        'albumImgSrc': meta.get('albumImgSrc', ''),
        'album': str(meta.get('album', '') or '').strip(),
        'backgroundImage': meta.get('Background-image', ''),
        'dynamicCoverSrc': meta.get('dynamicCoverSrc', ''),
        'dynamicCoverPoster': meta.get('dynamicCoverPoster', ''),
        'hasDuet': has_duet,
        'hasBackgroundVocals': has_background,
        'hasAudio': has_valid_audio(song_value),
        'mtime': mtime_val,
    }


_song_search_index_lock = threading.Lock()
_song_search_index: Dict[str, Dict[str, Any]] = {}
_song_search_index_revision: int = 0
_lyrics_resource_index: Dict[str, Set[str]] = {}
_lyrics_resource_index_initialized: bool = False

_INDEX_PERSIST_DEBOUNCE_SEC = 1.5
_song_search_index_persist_timer: Optional[threading.Timer] = None
_artist_playlist_index_persist_timer: Optional[threading.Timer] = None
_song_search_index_persist_pending = False
_artist_playlist_index_persist_pending = False

SONG_SEARCH_INDEX_VERSION = 1
SONG_SEARCH_INDEX_FILE = BASE_PATH / '.cache' / 'song_search_index.json'

# Artist basename index: updated only while holding _song_search_index_lock (same critical
# section as song_search_index rows) to avoid deadlock and keep the two structures consistent.
ARTIST_PLAYLIST_INDEX_VERSION = 1
ARTIST_PLAYLIST_INDEX_FILE = BASE_PATH / '.cache' / 'artist_playlist_index.json'
_artist_playlist_index_revision: int = 0
_artist_playlist_index: Dict[str, Set[str]] = {}
_artist_playlist_file_to_keys: Dict[str, Set[str]] = {}


def _song_search_json_paths() -> List[Path]:
    return [p for p in collect_static_json_file_paths() if p.name.lower() != 'artists.json']


def _compact_search_pool(pool: str) -> str:
    return re.sub(r'[\s,，\-_]+', '', pool or '')


def _search_tag_tokens_from_summary(summary: Dict[str, Any]) -> str:
    """Match LyricSphere-library tag search strings (zh + en) without importing i18n."""
    tags_zh: List[str] = []
    tags_en: List[str] = []
    lp = str(summary.get('lyricsPath') or '').strip()
    has_lyrics = bool(lp and lp != '!')
    if not has_lyrics:
        tags_zh.extend(['纯音乐', '无歌词'])
        tags_en.append('instrumental')
    if not summary.get('hasAudio'):
        tags_zh.append('无音源')
        tags_en.append('no audio')
    if summary.get('hasDuet'):
        tags_zh.extend(['对唱', '包含对唱歌词'])
        tags_en.append('duet')
    if summary.get('hasBackgroundVocals'):
        tags_zh.append('包含背景歌词')
        tags_en.append('background vocals')
    return ' '.join(tags_zh + tags_en).lower()


def _search_pool_from_summary(summary: Dict[str, Any]) -> str:
    filename = str(summary.get('filename') or '')
    stem = re.sub(r'\.json$', '', filename, flags=re.I).lower()
    title = str(summary.get('title') or '').strip().lower()
    album = str(summary.get('album') or '').strip().lower()
    artists = summary.get('artists') or []
    if isinstance(artists, list):
        artists_s = ' '.join(str(a).strip().lower() for a in artists if str(a).strip())
    else:
        artists_s = str(artists).strip().lower()
    song_val = str(summary.get('song') or '').strip().lower()
    tags = _search_tag_tokens_from_summary(summary)
    parts = [title, album, artists_s, filename.lower(), stem, song_val, tags]
    return ' '.join(p for p in parts if p)


def _load_song_search_index_from_disk() -> Optional[Tuple[Dict[str, Dict[str, Any]], int]]:
    """Return (entries map, revision) or None to trigger a full rebuild. Legacy files omit revision (treated as 0)."""
    path = SONG_SEARCH_INDEX_FILE
    if not path.is_file():
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as exc:
        app.logger.warning('song search index: load failed %s: %s', path, exc)
        return None
    if not isinstance(data, dict):
        return None
    if data.get('version') != SONG_SEARCH_INDEX_VERSION:
        return None
    raw_entries = data.get('entries')
    if not isinstance(raw_entries, dict):
        return None
    raw_rev = data.get('revision', 0)
    try:
        file_revision = int(raw_rev)
    except (TypeError, ValueError):
        file_revision = 0
    out: Dict[str, Dict[str, Any]] = {}
    for key, row in raw_entries.items():
        if not isinstance(key, str) or not isinstance(row, dict):
            continue
        if not isinstance(row.get('summary'), dict):
            continue
        if 'pool' not in row or 'pool_compact' not in row:
            continue
        try:
            row['mtime'] = float(row.get('mtime', 0.0))
        except (TypeError, ValueError):
            row['mtime'] = 0.0
        out[key] = row
    return out, file_revision


def _bump_song_search_index_revision_locked() -> None:
    """Increment content revision before persisting. Caller must hold _song_search_index_lock."""
    global _song_search_index_revision
    try:
        cur = int(_song_search_index_revision)
    except (TypeError, ValueError):
        cur = 0
    _song_search_index_revision = cur + 1


def _remove_json_from_lyrics_resource_index_locked(json_fn: str,
                                                   summary: Optional[Dict[str, Any]]) -> None:
    if not summary:
        return
    for key in _lyrics_resource_keys_from_summary(summary):
        refs = _lyrics_resource_index.get(key)
        if not refs:
            continue
        refs.discard(json_fn)
        if not refs:
            _lyrics_resource_index.pop(key, None)


def _add_json_to_lyrics_resource_index_locked(json_fn: str, summary: Dict[str, Any]) -> None:
    for key in _lyrics_resource_keys_from_summary(summary):
        _lyrics_resource_index.setdefault(key, set()).add(json_fn)


def _rebuild_lyrics_resource_index_from_song_index_locked() -> None:
    global _lyrics_resource_index_initialized
    _lyrics_resource_index.clear()
    for fn, row in _song_search_index.items():
        summ = row.get('summary') if isinstance(row, dict) else None
        if isinstance(summ, dict):
            _add_json_to_lyrics_resource_index_locked(fn, summ)
    _lyrics_resource_index_initialized = True


def _schedule_persist_song_search_index_locked() -> None:
    global _song_search_index_persist_timer, _song_search_index_persist_pending
    _song_search_index_persist_pending = True
    if _song_search_index_persist_timer is not None:
        _song_search_index_persist_timer.cancel()
    timer = threading.Timer(_INDEX_PERSIST_DEBOUNCE_SEC, _flush_pending_song_search_index_persist)
    timer.daemon = True
    _song_search_index_persist_timer = timer
    timer.start()


def _schedule_persist_artist_playlist_index_locked() -> None:
    global _artist_playlist_index_persist_timer, _artist_playlist_index_persist_pending
    _artist_playlist_index_persist_pending = True
    if _artist_playlist_index_persist_timer is not None:
        _artist_playlist_index_persist_timer.cancel()
    timer = threading.Timer(_INDEX_PERSIST_DEBOUNCE_SEC, _flush_pending_artist_playlist_index_persist)
    timer.daemon = True
    _artist_playlist_index_persist_timer = timer
    timer.start()


def _flush_pending_song_search_index_persist() -> None:
    global _song_search_index_persist_timer, _song_search_index_persist_pending
    with _song_search_index_lock:
        _song_search_index_persist_timer = None
        if not _song_search_index_persist_pending:
            return
        _persist_song_search_index()
        _song_search_index_persist_pending = False


def _flush_pending_artist_playlist_index_persist() -> None:
    global _artist_playlist_index_persist_timer, _artist_playlist_index_persist_pending
    with _song_search_index_lock:
        _artist_playlist_index_persist_timer = None
        if not _artist_playlist_index_persist_pending:
            return
        _persist_artist_playlist_index_locked()
        _artist_playlist_index_persist_pending = False


def flush_pending_index_persists() -> None:
    """Flush debounced index writes (e.g. on process exit)."""
    global _song_search_index_persist_timer, _artist_playlist_index_persist_timer
    with _song_search_index_lock:
        for timer in (_song_search_index_persist_timer, _artist_playlist_index_persist_timer):
            if timer is not None:
                timer.cancel()
        _song_search_index_persist_timer = None
        _artist_playlist_index_persist_timer = None
        if _song_search_index_persist_pending:
            _persist_song_search_index()
            _song_search_index_persist_pending = False
        if _artist_playlist_index_persist_pending:
            _persist_artist_playlist_index_locked()
            _artist_playlist_index_persist_pending = False


atexit.register(flush_pending_index_persists)


def _persist_song_search_index() -> None:
    """Serialize index to disk atomically. Caller must hold _song_search_index_lock."""
    cache_dir = SONG_SEARCH_INDEX_FILE.parent
    cache_dir.mkdir(parents=True, exist_ok=True)
    _bump_song_search_index_revision_locked()
    payload = {
        'version': SONG_SEARCH_INDEX_VERSION,
        'revision': int(_song_search_index_revision),
        'updatedAt': datetime.utcnow().isoformat() + 'Z',
        'entries': dict(_song_search_index),
    }
    fd, tmp_path = tempfile.mkstemp(prefix='song_search_index.', suffix='.tmp', dir=str(cache_dir))
    tmp_file = Path(tmp_path)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as tmp_fp:
            json.dump(payload, tmp_fp, ensure_ascii=False)
        os.replace(str(tmp_file), str(SONG_SEARCH_INDEX_FILE))
    except Exception:
        tmp_file.unlink(missing_ok=True)
        raise


def _apply_single_path_to_index_locked(path: Path, *, skip_artist_reconcile: bool = False) -> None:
    fn = path.name
    old_row = _song_search_index.get(fn)
    old_summary = old_row.get('summary') if isinstance(old_row, dict) else None
    if old_summary:
        _remove_json_from_lyrics_resource_index_locked(fn, old_summary)
    if fn.lower() == 'artists.json':
        _song_search_index.pop(fn, None)
        if not skip_artist_reconcile:
            _artist_index_reconcile_file_locked(fn, old_summary, None)
        return
    try:
        mtime = float(path.stat().st_mtime)
    except OSError as exc:
        app.logger.warning('song search index: stat failed %s: %s', path, exc)
        _song_search_index.pop(fn, None)
        if not skip_artist_reconcile:
            _artist_index_reconcile_file_locked(fn, old_summary, None)
        return
    built = _build_song_summary_from_static_json(path)
    if not built:
        _song_search_index.pop(fn, None)
        if not skip_artist_reconcile:
            _artist_index_reconcile_file_locked(fn, old_summary, None)
        return
    pool = _search_pool_from_summary(built)
    _song_search_index[fn] = {
        'mtime': mtime,
        'summary': built,
        'pool': pool,
        'pool_compact': _compact_search_pool(pool),
    }
    _add_json_to_lyrics_resource_index_locked(fn, built)
    new_row = _song_search_index.get(fn)
    new_summary = new_row.get('summary') if isinstance(new_row, dict) else None
    if not skip_artist_reconcile:
        _artist_index_reconcile_file_locked(fn, old_summary, new_summary)


def _rebuild_song_search_index_full_locked() -> None:
    global _lyrics_resource_index_initialized
    _song_search_index.clear()
    _artist_playlist_index.clear()
    _artist_playlist_file_to_keys.clear()
    _lyrics_resource_index.clear()
    for path in _song_search_json_paths():
        _apply_single_path_to_index_locked(path, skip_artist_reconcile=True)
    _rebuild_artist_playlist_from_song_index_locked()
    _lyrics_resource_index_initialized = True
    _persist_song_search_index()
    _persist_artist_playlist_index_locked()


def rebuild_song_search_index_full() -> None:
    with _song_search_index_lock:
        _rebuild_song_search_index_full_locked()


def upsert_song_search_index_for_path(path: Path) -> None:
    """Refresh one row for a static-dir song JSON and persist. Drops entry if file is gone."""
    path = path.resolve()
    if path.suffix.lower() != '.json' or path.name.lower() == 'artists.json':
        return
    try:
        path.relative_to(STATIC_DIR.resolve())
    except ValueError:
        return
    with _song_search_index_lock:
        if not path.is_file():
            old_row = _song_search_index.get(path.name)
            old_summary = old_row.get('summary') if isinstance(old_row, dict) else None
            if _song_search_index.pop(path.name, None) is not None:
                _remove_json_from_lyrics_resource_index_locked(path.name, old_summary)
                _artist_index_remove_file_locked(path.name)
                _schedule_persist_song_search_index_locked()
                _schedule_persist_artist_playlist_index_locked()
            return
        _apply_single_path_to_index_locked(path)
        _schedule_persist_song_search_index_locked()
        _schedule_persist_artist_playlist_index_locked()


def remove_song_search_index_entry(filename: str) -> None:
    """Remove index row by JSON basename (e.g. foo.json)."""
    key = Path(str(filename)).name
    with _song_search_index_lock:
        old_row = _song_search_index.get(key)
        old_summary = old_row.get('summary') if isinstance(old_row, dict) else None
        if _song_search_index.pop(key, None) is None:
            return
        _remove_json_from_lyrics_resource_index_locked(key, old_summary)
        _artist_index_remove_file_locked(key)
        _schedule_persist_song_search_index_locked()
        _schedule_persist_artist_playlist_index_locked()


def _sync_song_search_index_with_disk_locked() -> bool:
    """Prune stale keys and refresh rows whose mtime is newer than cached. Caller holds lock."""
    changed = False
    paths = _song_search_json_paths()
    valid = {p.name for p in paths}
    for fn in list(_song_search_index.keys()):
        if fn not in valid:
            old_row = _song_search_index.get(fn)
            old_summary = old_row.get('summary') if isinstance(old_row, dict) else None
            _remove_json_from_lyrics_resource_index_locked(fn, old_summary)
            _artist_index_remove_file_locked(fn)
            del _song_search_index[fn]
            changed = True
    for path in paths:
        fn = path.name
        try:
            mtime = float(path.stat().st_mtime)
        except OSError as exc:
            app.logger.warning('song search index: stat failed %s: %s', path, exc)
            continue
        cached = _song_search_index.get(fn)
        if cached and float(cached.get('mtime', 0.0)) >= mtime:
            continue
        _apply_single_path_to_index_locked(path)
        changed = True
    return changed


def _song_search_index_should_initialize() -> bool:
    if os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
        return True
    if not getattr(app, 'debug', False):
        return True
    return False


def init_song_search_index_on_startup() -> None:
    """Load persisted index or rebuild. Skips werkzeug reloader parent (mirrors start_ws_server_once)."""
    global _song_search_index_revision
    if not _song_search_index_should_initialize():
        return
    try:
        with _song_search_index_lock:
            loaded = _load_song_search_index_from_disk()
            if loaded is not None:
                entries_map, file_revision = loaded
                _song_search_index_revision = file_revision
                _song_search_index.clear()
                _song_search_index.update(entries_map)
                _rebuild_lyrics_resource_index_from_song_index_locked()
                if _sync_song_search_index_with_disk_locked():
                    _persist_song_search_index()
                    _persist_artist_playlist_index_locked()
            else:
                _rebuild_song_search_index_full_locked()
    except Exception:
        app.logger.exception('song search index: startup init failed')


def _parse_library_search_keywords(query: str) -> List[str]:
    normalized = (query or '').replace('，', ',')
    return list(dict.fromkeys([p.strip().lower() for p in normalized.split(',') if p.strip()]))


def _parse_library_fuzzy_chars(query: str) -> List[str]:
    raw = re.sub(r'[,，\s\-_]+', '', (query or '').lower())
    return list(dict.fromkeys([c for c in raw if c]))


def _library_list_display_name_for_sort(summary: Dict[str, Any]) -> str:
    """Same ordering key as LyricSphere-library getSummaryDisplayNameForList (getSummaryDisplayName): title else stem."""
    if not summary:
        return ''
    title = str(summary.get('title') or '').strip()
    if title:
        return title.lower()
    fn = str(summary.get('filename') or '')
    return re.sub(r'\.json$', '', fn, flags=re.I).lower()


# Match lyric-sphere-v2 normalizeArtistName / songHasArtist for server-side artist routes.
UNKNOWN_ARTIST_SENTINEL = '__unknown_artist__'
UNKNOWN_ARTIST_LEGACY_FALLBACK = 'Unknown artist'


def _normalize_artist_name_for_match(value: Optional[Any]) -> str:
    """Mirror lyric-sphere-v2 normalizeArtistName for artist filter and /songs/artist."""
    trimmed = str(value or '').strip()
    if not trimmed:
        return UNKNOWN_ARTIST_SENTINEL
    if trimmed == UNKNOWN_ARTIST_LEGACY_FALLBACK:
        return UNKNOWN_ARTIST_SENTINEL
    return trimmed


_COMPOSITE_ARTIST_SEP_RE = re.compile(
    r'\s*[,，、;；/|]\s*|\s*&\s*|\b(?:feat\.?|ft\.?|vs\.?)\b\s*',
    re.IGNORECASE,
)


def _expand_composite_artist_string(value: Any) -> List[str]:
    """Split composite display strings into individual artist tokens (trimmed, non-empty)."""
    text = str(value or '').strip()
    if not text:
        return []
    parts = [p.strip() for p in _COMPOSITE_ARTIST_SEP_RE.split(text)]
    return [p for p in parts if p]


def _artist_keys_from_summary_for_index(summary: Optional[Dict[str, Any]]) -> Set[str]:
    """Normalized artist keys for index buckets (one file may map to multiple keys)."""
    if not summary or not isinstance(summary, dict):
        return set()
    artists_raw = summary.get('artists')
    names: List[str]
    if isinstance(artists_raw, list):
        names = [str(a) for a in artists_raw] if artists_raw else []
    elif artists_raw is None or artists_raw == '':
        names = []
    else:
        names = [str(artists_raw)]
    if not names:
        names = [UNKNOWN_ARTIST_SENTINEL]
    expanded: List[str] = []
    for n in names:
        expanded.extend(_expand_composite_artist_string(n))
    if not expanded:
        expanded = [UNKNOWN_ARTIST_SENTINEL]
    return {_normalize_artist_name_for_match(n) for n in expanded}


def _artist_index_remove_file_locked(fn: str) -> None:
    """Caller must hold _song_search_index_lock."""
    keys = _artist_playlist_file_to_keys.pop(fn, None)
    if not keys:
        return
    for ak in keys:
        bucket = _artist_playlist_index.get(ak)
        if bucket:
            bucket.discard(fn)
            if not bucket:
                del _artist_playlist_index[ak]


def _artist_index_add_file_locked(fn: str, summary: Dict[str, Any]) -> None:
    """Caller must hold _song_search_index_lock."""
    keys = _artist_keys_from_summary_for_index(summary)
    if not keys:
        return
    _artist_playlist_file_to_keys[fn] = set(keys)
    for ak in keys:
        _artist_playlist_index.setdefault(ak, set()).add(fn)


def _artist_index_reconcile_file_locked(fn: str, old_summary: Optional[Dict[str, Any]], new_summary: Optional[Dict[str, Any]]) -> None:
    """Sync artist buckets for one JSON basename. Caller must hold _song_search_index_lock."""
    _artist_index_remove_file_locked(fn)
    if new_summary and isinstance(new_summary, dict):
        _artist_index_add_file_locked(fn, new_summary)


def _bump_artist_playlist_index_revision_locked() -> None:
    global _artist_playlist_index_revision
    try:
        cur = int(_artist_playlist_index_revision)
    except (TypeError, ValueError):
        cur = 0
    _artist_playlist_index_revision = cur + 1


def _persist_artist_playlist_index_locked() -> None:
    """Atomic persist for artist index. Caller must hold _song_search_index_lock."""
    cache_dir = ARTIST_PLAYLIST_INDEX_FILE.parent
    cache_dir.mkdir(parents=True, exist_ok=True)
    _bump_artist_playlist_index_revision_locked()
    buckets_out: Dict[str, List[str]] = {}
    for ak, fset in _artist_playlist_index.items():
        buckets_out[ak] = sorted(fset)
    file_keys_out: Dict[str, List[str]] = {
        fn: sorted(keys) for fn, keys in _artist_playlist_file_to_keys.items()
    }
    payload = {
        'version': ARTIST_PLAYLIST_INDEX_VERSION,
        'revision': int(_artist_playlist_index_revision),
        'songSearchRevision': int(_song_search_index_revision),
        'updatedAt': datetime.utcnow().isoformat() + 'Z',
        'buckets': buckets_out,
        'fileToKeys': file_keys_out,
    }
    fd, tmp_path = tempfile.mkstemp(prefix='artist_playlist_index.', suffix='.tmp', dir=str(cache_dir))
    tmp_file = Path(tmp_path)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as tmp_fp:
            json.dump(payload, tmp_fp, ensure_ascii=False)
        os.replace(str(tmp_file), str(ARTIST_PLAYLIST_INDEX_FILE))
    except Exception:
        tmp_file.unlink(missing_ok=True)
        raise


def _load_artist_playlist_index_from_disk() -> Optional[Tuple[Dict[str, Set[str]], Dict[str, Set[str]], int, int]]:
    """Return (buckets, file_to_keys, file_artist_revision, paired_song_revision) or None."""
    path = ARTIST_PLAYLIST_INDEX_FILE
    if not path.is_file():
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as exc:
        app.logger.warning('artist playlist index: load failed %s: %s', path, exc)
        return None
    if not isinstance(data, dict):
        return None
    if data.get('version') != ARTIST_PLAYLIST_INDEX_VERSION:
        return None
    raw_buckets = data.get('buckets')
    raw_ftk = data.get('fileToKeys')
    if not isinstance(raw_buckets, dict) or not isinstance(raw_ftk, dict):
        return None
    try:
        file_artist_rev = int(data.get('revision', 0))
    except (TypeError, ValueError):
        file_artist_rev = 0
    try:
        paired_song_rev = int(data.get('songSearchRevision', 0))
    except (TypeError, ValueError):
        paired_song_rev = 0
    buckets: Dict[str, Set[str]] = {}
    for ak, lst in raw_buckets.items():
        if not isinstance(ak, str) or not isinstance(lst, list):
            continue
        good: Set[str] = set()
        for fn in lst:
            if isinstance(fn, str) and fn.lower().endswith('.json') and fn.lower() != 'artists.json':
                good.add(fn)
        if good:
            buckets[ak] = good
    file_to_keys: Dict[str, Set[str]] = {}
    for fn, lst in raw_ftk.items():
        if not isinstance(fn, str) or not isinstance(lst, list):
            continue
        keys = {k for k in lst if isinstance(k, str)}
        if keys:
            file_to_keys[fn] = keys
    return buckets, file_to_keys, file_artist_rev, paired_song_rev


def _rebuild_artist_playlist_from_song_index_locked() -> None:
    """Rebuild in-memory artist buckets from _song_search_index. Caller holds _song_search_index_lock."""
    _artist_playlist_index.clear()
    _artist_playlist_file_to_keys.clear()
    for fn, row in _song_search_index.items():
        if not isinstance(row, dict):
            continue
        summ = row.get('summary')
        if isinstance(summ, dict):
            _artist_index_add_file_locked(fn, summ)


def rebuild_artist_playlist_index_full() -> None:
    with _song_search_index_lock:
        _rebuild_artist_playlist_from_song_index_locked()
        _persist_artist_playlist_index_locked()


def init_artist_playlist_index_on_startup() -> None:
    """Load .cache/artist_playlist_index.json or rebuild from song index. Call after init_song_search_index_on_startup."""
    global _artist_playlist_index_revision
    if not _song_search_index_should_initialize():
        return
    try:
        with _song_search_index_lock:
            loaded = _load_artist_playlist_index_from_disk()
            song_rev = int(_song_search_index_revision)
            if loaded is not None:
                buckets, file_to_keys, artist_rev, paired = loaded
                if paired == song_rev:
                    _artist_playlist_index_revision = artist_rev
                    _artist_playlist_index.clear()
                    _artist_playlist_file_to_keys.clear()
                    for ak, s in buckets.items():
                        _artist_playlist_index[ak] = set(s)
                    for fn, ks in file_to_keys.items():
                        _artist_playlist_file_to_keys[fn] = set(ks)
                    if _song_search_index:
                        indexed_fns = set(_artist_playlist_file_to_keys.keys())
                        if indexed_fns != set(_song_search_index.keys()):
                            _rebuild_artist_playlist_from_song_index_locked()
                            _persist_artist_playlist_index_locked()
                    return
            _rebuild_artist_playlist_from_song_index_locked()
            _persist_artist_playlist_index_locked()
    except Exception:
        app.logger.exception('artist playlist index: startup init failed')


# Eager index init at import: keeps first /api/search and artist routes consistent.
# Deferred/background init was skipped: race on first request before index is ready.
init_song_search_index_on_startup()
init_artist_playlist_index_on_startup()


def _sort_search_summaries_inplace(summaries: List[Dict[str, Any]], sort_type: str, sort_asc: bool) -> None:
    """Align browse list ordering: name uses _library_list_display_name_for_sort; time = mtime then filename."""
    st = (sort_type or 'time').strip().lower()
    if st not in ('time', 'name'):
        st = 'time'
    if st == 'name':

        def name_key(s: Dict[str, Any]) -> Tuple[str, str]:
            return (_library_list_display_name_for_sort(s), str(s.get('filename') or '').lower())

        summaries.sort(key=name_key, reverse=not sort_asc)
        return

    def time_key(s: Dict[str, Any]) -> Tuple[float, str]:
        fn = str(s.get('filename') or '')
        return (float(s.get('mtime') or 0.0), fn.lower())

    summaries.sort(key=time_key, reverse=not sort_asc)


def _run_library_search_ordered_summaries(
    query: str, fuzzy: bool, sort_type: str = 'time', sort_asc: bool = False
) -> List[Dict[str, Any]]:
    if fuzzy:
        tokens = _parse_library_fuzzy_chars(query)
        if not tokens:
            return []

        def row_match(entry: Dict[str, Any]) -> bool:
            pc = entry['pool_compact']
            return all(ch in pc for ch in tokens)
    else:
        tokens = _parse_library_search_keywords(query)
        if not tokens:
            return []

        def row_match(entry: Dict[str, Any]) -> bool:
            pool = entry['pool']
            return all(kw in pool for kw in tokens)

    with _song_search_index_lock:
        rows = list(_song_search_index.values())
    summaries: List[Dict[str, Any]] = []
    for entry in rows:
        if not row_match(entry):
            continue
        summaries.append(entry['summary'])
    _sort_search_summaries_inplace(summaries, sort_type, sort_asc)
    return summaries


def _snapshot_query_sort_params() -> Tuple[str, bool]:
    sort_type = (request.args.get('sortType') or 'time').strip().lower()
    if sort_type not in ('time', 'name'):
        sort_type = 'time'
    sort_asc_raw = (request.args.get('sortAsc') or '0').strip().lower()
    sort_asc = sort_asc_raw in ('1', 'true', 'yes', 'on')
    return sort_type, sort_asc


@app.route('/songs/snapshot')
def songs_library_snapshot():
    """Full in-memory library snapshot from the song search index (revision bumps on index writes)."""
    if not is_request_allowed():
        return abort(403)

    def _no_store(resp):
        resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        resp.headers['Pragma'] = 'no-cache'
        resp.headers['Expires'] = '0'
        return resp

    meta_raw = (request.args.get('meta') or '').strip().lower()
    meta_only = meta_raw in ('1', 'true', 'yes', 'on')
    sort_type, sort_asc = _snapshot_query_sort_params()

    with _song_search_index_lock:
        rev = int(_song_search_index_revision)
        total = len(_song_search_index)
    if meta_only:
        return _no_store(jsonify({'status': 'success', 'revision': rev, 'total': total}))

    with _song_search_index_lock:
        summaries = [
            entry['summary']
            for entry in _song_search_index.values()
            if isinstance(entry.get('summary'), dict)
        ]
    _sort_search_summaries_inplace(summaries, sort_type, sort_asc)
    payload = {
        'status': 'success',
        'revision': rev,
        'total': len(summaries),
        'sortType': sort_type,
        'sortAsc': sort_asc,
        'songs': _rewrite_client_song_summaries(summaries),
    }
    return _no_store(jsonify(payload))


@app.route('/songs/summary')
def list_song_summaries():
    """返回精简的歌曲列表信息，避免前端批量读取静态资源。支持按 mtime 分页与单曲查询。"""
    if not is_request_allowed():
        return abort(403)

    def _paging_payload(
        songs: List[Dict[str, Any]],
        page: int,
        page_size: int,
        total: int,
        revision: int,
    ) -> Dict[str, Any]:
        total_pages = math.ceil(total / page_size) if total > 0 and page_size > 0 else 0
        has_more = total_pages > 0 and page < total_pages
        next_page = page + 1 if has_more else None
        return {
            'status': 'success',
            'songs': songs,
            'page': page,
            'pageSize': page_size,
            'total': total,
            'totalPages': total_pages,
            'hasMore': has_more,
            'nextPage': next_page,
            'loaded': len(songs),
            'revision': int(revision),
        }

    raw_single = (request.args.get('filename') or '').strip()
    if raw_single:
        def _single_error(message: str, status_code: int = 404):
            resp = jsonify({
                'status': 'error',
                'message': message,
                'songs': [],
                'page': 1,
                'pageSize': 50,
                'total': 0,
                'totalPages': 0,
                'hasMore': False,
                'nextPage': None,
                'loaded': 0,
            })
            resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
            resp.headers['Pragma'] = 'no-cache'
            resp.headers['Expires'] = '0'
            return resp, status_code

        try:
            _, single_path = _resolve_existing_static_json_filename(raw_single)
        except ValueError:
            return _single_error('Invalid or disallowed song JSON filename', 400)

        if not single_path.is_file():
            return _single_error('Song JSON file not found', 404)

        if single_path.name.lower() == 'artists.json':
            return _single_error('Not a song metadata file', 400)

        with _song_search_index_lock:
            rev = int(_song_search_index_revision)
            cached = _song_search_index.get(single_path.name)
        if cached and isinstance(cached.get('summary'), dict):
            summary = cached['summary']
        else:
            summary = _build_song_summary_from_static_json(single_path)
        if not summary:
            return _single_error('Failed to read or parse song JSON', 422)

        payload = _paging_payload([_rewrite_client_song_summary(summary)], 1, 50, 1, rev)
        response = jsonify(payload)
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        return response

    page = max(1, coerce_int(request.args.get('page'), 1) or 1)
    page_size = coerce_int(request.args.get('pageSize'), 50) or 50
    page_size = max(1, min(page_size, 50))

    with _song_search_index_lock:
        rev = int(_song_search_index_revision)
        rows = list(_song_search_index.values())
    summaries_full: List[Dict[str, Any]] = []
    for entry in rows:
        s = entry.get('summary')
        if isinstance(s, dict):
            summaries_full.append(s)
    _sort_search_summaries_inplace(summaries_full, 'time', False)
    total = len(summaries_full)
    page_size_eff = page_size
    total_pages = math.ceil(total / page_size_eff) if total > 0 and page_size_eff > 0 else 0
    offset = (page - 1) * page_size_eff
    summaries = summaries_full[offset:offset + page_size_eff]

    has_more = total_pages > 0 and page < total_pages
    next_page = page + 1 if has_more else None
    payload = {
        'status': 'success',
        'revision': rev,
        'songs': _rewrite_client_song_summaries(summaries),
        'page': page,
        'pageSize': page_size_eff,
        'total': total,
        'totalPages': total_pages,
        'hasMore': has_more,
        'nextPage': next_page,
        'loaded': len(summaries),
    }
    response = jsonify(payload)
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


@app.route('/songs/summary/batch', methods=['POST'])
def batch_song_summaries():
    """Resolve up to 50 song summaries in one request; order matches input (null slots skipped)."""
    if not is_request_allowed():
        return abort(403)

    def _no_store(resp):
        resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        resp.headers['Pragma'] = 'no-cache'
        resp.headers['Expires'] = '0'
        return resp

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _no_store(jsonify({'status': 'error', 'message': 'JSON body required'})), 400
    raw_list = payload.get('filenames')
    if not isinstance(raw_list, list):
        return _no_store(jsonify({'status': 'error', 'message': 'filenames must be an array'})), 400

    truncated = len(raw_list) > 50
    capped = raw_list[:50]
    songs_out: List[Optional[Dict[str, Any]]] = []
    errors_out: List[Optional[str]] = []

    for idx, raw in enumerate(capped):
        label = f'[{idx}]'
        if not isinstance(raw, str):
            songs_out.append(None)
            errors_out.append(f'{label} filename must be a string')
            continue
        name = raw.strip()
        if not name:
            songs_out.append(None)
            errors_out.append(f'{label} empty filename')
            continue
        try:
            _, path = _resolve_existing_static_json_filename(name)
        except ValueError:
            songs_out.append(None)
            errors_out.append(f'{label} invalid or disallowed filename')
            continue
        if path.name.lower() == 'artists.json':
            songs_out.append(None)
            errors_out.append(f'{label} not a song metadata file')
            continue
        if not path.is_file():
            songs_out.append(None)
            errors_out.append(f'{label} file not found')
            continue
        built = _build_song_summary_from_static_json(path)
        if not built:
            songs_out.append(None)
            errors_out.append(f'{label} failed to read or parse JSON')
            continue
        songs_out.append(_rewrite_client_song_summary(built))
        errors_out.append(None)

    body: Dict[str, Any] = {
        'status': 'success',
        'songs': songs_out,
        'errors': errors_out,
        'requested': len(raw_list),
        'returned': len(capped),
        'truncated': truncated,
    }
    return _no_store(jsonify(body))


@app.route('/songs/artists')
def list_artists_index_summary():
    """Lightweight artist list from static index: revision + [{key, count}] sorted by name."""
    if not is_request_allowed():
        return abort(403)

    def _no_store(resp):
        resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        resp.headers['Pragma'] = 'no-cache'
        resp.headers['Expires'] = '0'
        return resp

    with _song_search_index_lock:
        rev = int(_artist_playlist_index_revision)
        items = [{'key': ak, 'count': len(v)} for ak, v in _artist_playlist_index.items()]
    items.sort(key=lambda r: (r['key'].casefold(), r['key']))
    return _no_store(jsonify({'status': 'success', 'revision': rev, 'artists': items}))


@app.route('/songs/artist')
def list_songs_by_artist():
    """Paginated songs for one artist; filenames from artist index, summaries from song search index."""
    if not is_request_allowed():
        return abort(403)

    def _no_store(resp):
        resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        resp.headers['Pragma'] = 'no-cache'
        resp.headers['Expires'] = '0'
        return resp

    raw_artist = (request.args.get('artist') or '').strip()
    if not raw_artist:
        payload = {
            'status': 'success',
            'artist': '',
            'sortType': 'time',
            'sortAsc': False,
            'page': 1,
            'pageSize': 50,
            'total': 0,
            'totalPages': 0,
            'hasMore': False,
            'nextPage': None,
            'loaded': 0,
            'songs': [],
        }
        return _no_store(jsonify(payload))

    sort_type = (request.args.get('sortType') or 'time').strip().lower()
    if sort_type not in ('time', 'name'):
        sort_type = 'time'
    sort_asc_raw = (request.args.get('sortAsc') or '0').strip().lower()
    sort_asc = sort_asc_raw in ('1', 'true', 'yes', 'on')

    page = max(1, coerce_int(request.args.get('page'), 1) or 1)
    page_size = coerce_int(request.args.get('pageSize'), 50) or 50
    page_size = max(1, min(page_size, 50))

    target_norm = _normalize_artist_name_for_match(raw_artist)
    summaries: List[Dict[str, Any]] = []
    with _song_search_index_lock:
        fns = sorted(_artist_playlist_index.get(target_norm, set()))
        for fn in fns:
            row = _song_search_index.get(fn)
            summ = row.get('summary') if isinstance(row, dict) else None
            if isinstance(summ, dict):
                summaries.append(summ)
                continue
            try:
                _, path = _resolve_existing_static_json_filename(fn)
            except ValueError:
                continue
            if path.name.lower() == 'artists.json':
                continue
            if not path.is_file():
                continue
            built = _build_song_summary_from_static_json(path)
            if built:
                summaries.append(built)
    _sort_search_summaries_inplace(summaries, sort_type, sort_asc)
    total = len(summaries)
    total_pages = math.ceil(total / page_size) if total > 0 and page_size > 0 else 0
    offset = (page - 1) * page_size
    slice_songs = summaries[offset:offset + page_size]
    has_more = total_pages > 0 and page < total_pages
    next_page = page + 1 if has_more else None
    payload = {
        'status': 'success',
        'artist': raw_artist,
        'sortType': sort_type,
        'sortAsc': sort_asc,
        'page': page,
        'pageSize': page_size,
        'total': total,
        'totalPages': total_pages,
        'hasMore': has_more,
        'nextPage': next_page,
        'loaded': len(slice_songs),
        'songs': _rewrite_client_song_summaries(slice_songs),
    }
    return _no_store(jsonify(payload))


@app.route('/songs/search')
def search_song_library():
    """Full-library song search with pagination; separate from browse list /songs/summary."""
    if not is_request_allowed():
        return abort(403)

    raw_q = (request.args.get('q') or '').strip()
    fuzzy_arg = (request.args.get('fuzzy') or '').strip().lower()
    fuzzy = fuzzy_arg in ('1', 'true', 'yes', 'on')

    sort_type = (request.args.get('sortType') or 'time').strip().lower()
    if sort_type not in ('time', 'name'):
        sort_type = 'time'
    sort_asc_raw = (request.args.get('sortAsc') or '0').strip().lower()
    sort_asc = sort_asc_raw in ('1', 'true', 'yes', 'on')

    page = max(1, coerce_int(request.args.get('page'), 1) or 1)
    page_size = coerce_int(request.args.get('pageSize'), 50) or 50
    page_size = max(1, min(page_size, 50))

    def _no_store(resp):
        resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        resp.headers['Pragma'] = 'no-cache'
        resp.headers['Expires'] = '0'
        return resp

    if not raw_q:
        payload = {
            'status': 'success',
            'query': '',
            'fuzzy': fuzzy,
            'sortType': sort_type,
            'sortAsc': sort_asc,
            'page': 1,
            'pageSize': page_size,
            'total': 0,
            'totalPages': 0,
            'hasMore': False,
            'nextPage': None,
            'loaded': 0,
            'songs': [],
        }
        return _no_store(jsonify(payload))

    ordered = _run_library_search_ordered_summaries(raw_q, fuzzy, sort_type, sort_asc)
    total = len(ordered)
    total_pages = math.ceil(total / page_size) if total > 0 and page_size > 0 else 0
    offset = (page - 1) * page_size
    slice_songs = ordered[offset:offset + page_size]
    has_more = total_pages > 0 and page < total_pages
    next_page = page + 1 if has_more else None
    payload = {
        'status': 'success',
        'query': raw_q,
        'fuzzy': fuzzy,
        'sortType': sort_type,
        'sortAsc': sort_asc,
        'page': page,
        'pageSize': page_size,
        'total': total,
        'totalPages': total_pages,
        'hasMore': has_more,
        'nextPage': next_page,
        'loaded': len(slice_songs),
        'songs': _rewrite_client_song_summaries(slice_songs),
    }
    return _no_store(jsonify(payload))


@app.route('/internal/rebuild_song_search_index', methods=['POST'])
def internal_rebuild_song_search_index():
    if not is_request_allowed():
        return abort(403)
    locked_response = require_unlocked_device('重建歌曲搜索索引')
    if locked_response:
        return locked_response
    try:
        rebuild_song_search_index_full()
        with _song_search_index_lock:
            count = len(_song_search_index)
        return jsonify({'status': 'success', 'entries': count})
    except Exception as exc:
        return jsonify({'status': 'error', 'message': str(exc)}), 500


@app.route('/internal/rebuild_artist_playlist_index', methods=['POST'])
def internal_rebuild_artist_playlist_index():
    if not is_request_allowed():
        return abort(403)
    locked_response = require_unlocked_device('重建艺术家索引')
    if locked_response:
        return locked_response
    try:
        rebuild_artist_playlist_index_full()
        with _song_search_index_lock:
            keys = len(_artist_playlist_index)
            rev = int(_artist_playlist_index_revision)
        return jsonify({'status': 'success', 'artists': keys, 'revision': rev})
    except Exception as exc:
        return jsonify({'status': 'error', 'message': str(exc)}), 500


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
        for f in iter_backup_files(file_path):
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
                'name': backup_public_relative_path(f)
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
async def upload_music():
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

        try:
            clean_name, save_path = _resolve_new_filename_in_directory(SONGS_DIR, file.filename)
        except ValueError as exc:
            return jsonify({'status': 'error', 'message': str(exc)})

        # 如果文件已存在则覆盖
        await save_upload_file(file, save_path)

        return jsonify({'status': 'success', 'filename': clean_name})

    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})


@app.route('/upload_image', methods=['POST'])
async def upload_image():
    if not is_request_allowed():
        return abort(403)
    try:
        if 'file' not in request.files:
            return jsonify({'status': 'error', 'message': '没有选择文件'})

        file = request.files['file']
        if file.filename == '':
            return jsonify({'status': 'error', 'message': '无效的文件名'})

        # 验证文件类型
        allowed_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.apng', '.mp4', '.webm', '.ogg', '.m4v', '.mov'}
        file_ext = Path(file.filename).suffix.lower()
        if file_ext not in allowed_extensions:
            return jsonify({
                'status': 'error',
                'message': '只支持 JPG/PNG/GIF/WEBP/MP4/WEBM/OGG/M4V/MOV 格式'
            })

        try:
            clean_name, save_path = _resolve_new_filename_in_directory(SONGS_DIR, file.filename)
        except ValueError as exc:
            return jsonify({'status': 'error', 'message': str(exc)})

        # 如果文件已存在则覆盖
        await save_upload_file(file, save_path)

        return jsonify({'status': 'success', 'filename': clean_name})

    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})


@app.route('/lddc/search', methods=['GET'])
def lddc_search():
    if not is_request_allowed():
        return abort(403)
    locked_response = require_unlocked_device('搜索歌词')
    if locked_response:
        return locked_response
    keyword = (request.args.get('keyword') or '').strip()
    sources = (request.args.get('sources') or '').strip()
    if not keyword:
        return jsonify({'status': 'error', 'message': '缺少搜索关键词'})
    params = {'keyword': keyword}
    if sources:
        params['sources'] = sources
    try:
        response = requests.get(f"{LDDC_API_BASE}/api/search", params=params, timeout=20)
        response.raise_for_status()
        return jsonify({'status': 'success', 'results': response.json()})
    except requests.exceptions.RequestException as exc:
        return jsonify({'status': 'error', 'message': f'搜索失败: {exc}'})


@app.route('/lddc/match_lyrics', methods=['GET'])
def lddc_match_lyrics():
    if not is_request_allowed():
        return abort(403)
    locked_response = require_unlocked_device('匹配歌词')
    if locked_response:
        return locked_response
    title = (request.args.get('title') or '').strip()
    artist = (request.args.get('artist') or '').strip()
    keyword = (request.args.get('keyword') or '').strip()
    if not (title and artist) and not keyword:
        return jsonify({'status': 'error', 'message': '需要提供歌曲名+歌手名或关键词'})
    params = {}
    if title and artist:
        params['title'] = title
        params['artist'] = artist
    if keyword:
        params['keyword'] = keyword
    try:
        response = requests.get(f"{LDDC_API_BASE}/api/match_lyrics", params=params, timeout=20)
        response.raise_for_status()
        raw_lrc = response.text or ''
        lys_text, translation_text = _split_lddc_lrc(raw_lrc)
        return jsonify({
            'status': 'success',
            'lyrics_lys': lys_text,
            'translation_lrc': translation_text,
            'raw_lrc': raw_lrc
        })
    except requests.exceptions.RequestException as exc:
        return jsonify({'status': 'error', 'message': f'匹配失败: {exc}'})


@app.route('/lddc/get_lyrics_by_id', methods=['POST'])
def lddc_get_lyrics_by_id():
    if not is_request_allowed():
        return abort(403)
    locked_response = require_unlocked_device('获取歌词')
    if locked_response:
        return locked_response
    data = request.json or {}
    song_info_json = (data.get('song_info_json') or '').strip()
    if not song_info_json:
        return jsonify({'status': 'error', 'message': '缺少 song_info_json'})
    try:
        response = requests.get(
            f"{LDDC_API_BASE}/api/get_lyrics_by_id",
            params={'song_info_json': song_info_json},
            timeout=20
        )
        response.raise_for_status()
        raw_lrc = response.text or ''
        lys_text, translation_text = _split_lddc_lrc(raw_lrc)
        return jsonify({
            'status': 'success',
            'lyrics_lys': lys_text,
            'translation_lrc': translation_text,
            'raw_lrc': raw_lrc
        })
    except requests.exceptions.RequestException as exc:
        return jsonify({'status': 'error', 'message': f'获取失败: {exc}'})


@app.route('/upload_lyrics', methods=['POST'])
async def upload_lyrics():
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

        try:
            clean_name, save_path = _resolve_new_filename_in_directory(SONGS_DIR, file.filename)
        except ValueError as exc:
            return jsonify({'status': 'error', 'message': str(exc)})

        # 保存文件并计算大小/校验
        file_size, checksum = await save_upload_file_with_meta(file, save_path)

        app.logger.info(
            f'[{client_ip}] {username} 上传成功: {clean_name} | 大小: {file_size}字节 | MD5: {checksum}'
        )

        return jsonify({'status': 'success', 'filename': clean_name})

    except Exception as e:
        error_msg = f'[{client_ip}] {username} 上传失败: {str(e)} | 文件: {file.filename}'
        app.logger.error(error_msg, exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)})


@app.route('/upload_translation', methods=['POST'])
async def upload_translation():
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

        try:
            clean_name, save_path = _resolve_new_filename_in_directory(SONGS_DIR, file.filename)
        except ValueError as exc:
            return jsonify({'status': 'error', 'message': str(exc)})

        # 保存文件并计算大小/校验
        file_size, checksum = await save_upload_file_with_meta(file, save_path)

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
    """TTML时间处理类，用于解析和操作TTML格式的时间戳

    支持格式：MM:SS.mmm（分:秒.毫秒）
    """
    _pattern: Pattern = compile(r'\d+')

    def __init__(self, centi: str = ''):
        """初始化TTML时间对象

        Args:
            centi: 时间字符串，格式为 MM:SS.mmm
        """
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
        """返回格式化的时间字符串

        Returns:
            返回 MM:SS.mmm 格式的时间字符串
        """
        return f'{self._minute:02}:{self._second:02}.{self._micros:03}'

    def __int__(self) -> int:
        """转换为毫秒数

        Returns:
            返回总的毫秒数
        """
        return (self._minute * 60 + self._second) * 1000 + self._micros

    def __ge__(self, other) -> bool:
        """大于等于比较

        Args:
            other: 另一个TTMLTime对象

        Returns:
            如果当前时间大于等于另一个时间返回True
        """
        return (self._minute, self._second, self._micros) >= (other._minute, other._second, other._micros)

    def __ne__(self, other) -> bool:
        """不等于比较

        Args:
            other: 另一个TTMLTime对象

        Returns:
            如果两个时间不相等返回True
        """
        return (self._minute, self._second, self._micros) != (other._minute, other._second, other._micros)

    def __sub__(self, other) -> int:
        """计算时间差

        Args:
            other: 另一个TTMLTime对象

        Returns:
            返回两个时间差的绝对值（毫秒）
        """
        return abs(int(self) - int(other))

class TTMLSyl:
    """TTML音节类，表示一个带时间戳的音节

    从TTML的<span>元素解析而来，包含文本内容和时间信息。
    """
    def __init__(self, element: Element):
        """初始化TTML音节对象

        Args:
            element: XML DOM元素对象，包含begin和end属性
        """
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


def ttml_text_to_lys_parts(ttml_text: str) -> Tuple[bool, List[str], List[str], str]:
    """解析 TTML 字符串，返回 LYS 行列表与可选翻译行（与写盘版语义一致）。"""
    TTMLLine.have_duet = False
    TTMLLine.have_bg = False
    TTMLLine.have_ts = False
    TTMLLine.have_pair = 0
    if not (ttml_text or '').strip():
        return False, [], [], 'TTML 内容为空'
    try:
        sanitized_ttml = sanitize_ttml_content(ttml_text)
    except Exception as exc:
        app.logger.error("TTML 白名单过滤失败（字符串）: %s", exc)
        return False, [], [], f'TTML 白名单过滤失败: {exc}'

    try:
        dom: Document = xml.dom.minidom.parseString(sanitized_ttml)
        tt: Document = dom.documentElement
        body_elements = tt.getElementsByTagName('body')
        head_elements = tt.getElementsByTagName('head')
        if not body_elements or not head_elements:
            return False, [], [], 'TTML 缺少 body 或 head'
        body: Element = body_elements[0]
        head: Element = head_elements[0]
        div_elements = body.getElementsByTagName('div')
        metadata_elements = head.getElementsByTagName('metadata')
        if not div_elements or not metadata_elements:
            return False, [], [], 'TTML 缺少 div 或 metadata'
        div: Element = div_elements[0]
        metadata: Element = metadata_elements[0]
        p_elements: NodeList[Element] = div.getElementsByTagName('p')
        if not p_elements or len(p_elements) == 0:
            return False, [], [], 'TTML 缺少歌词行'
        agent_elements: NodeList[Element] = metadata.getElementsByTagName('ttm:agent')
        for meta in agent_elements:
            if meta.getAttribute('xml:id') != 'v1':
                TTMLLine.have_duet = True
        lines: list[TTMLLine] = []
        for p in p_elements:
            try:
                lines.append(TTMLLine(p))
            except Exception as e:
                app.logger.error(f"处理TTML行时出错: {type(e).__name__}: {e!s}，已跳过")
                continue
        lys_parts: List[str] = []
        trans_parts: List[str] = []
        for main_line, bg_line in [line.to_str() for line in lines]:
            if main_line and main_line[0]:
                lys_parts.append(main_line[0])
            if main_line and main_line[1]:
                trans_parts.append(main_line[1])
            if bg_line:
                if bg_line[0]:
                    lys_parts.append(bg_line[0])
        if not lys_parts:
            return False, [], [], '未生成任何 LYS 行'
        return True, lys_parts, trans_parts, ''
    except Exception as e:
        app.logger.error("无法解析TTML字符串: %s", e, exc_info=True)
        return False, [], [], str(e)


def ttml_text_to_lys_content(ttml_text: str) -> Tuple[bool, Optional[str], str]:
    ok, lys_parts, _trans, msg = ttml_text_to_lys_parts(ttml_text)
    if not ok:
        return False, None, msg
    return True, "\n".join(lys_parts), ''


def ttml_to_lys(input_path, songs_dir):
    """主转换函数"""
    lyric_path = ''
    trans_path = ''
    try:
        try:
            with open(input_path, 'r', encoding='utf-8', errors='ignore') as f:
                raw_ttml = f.read()
        except Exception as exc:
            app.logger.error(f"读取TTML文件失败: {input_path}. 错误: {exc}")
            return False, None, None

        ok, lys_parts, trans_parts, _err = ttml_text_to_lys_parts(raw_ttml)
        if not ok or not lys_parts:
            return False, None, None

        os.makedirs(songs_dir, exist_ok=True)
        base_name = os.path.splitext(input_path)[0]
        lyric_path = os.path.join(songs_dir, f"{os.path.basename(base_name)}.lys")
        with open(lyric_path, 'w', encoding='utf8') as lyric_file:
            lyric_file.write("\n".join(lys_parts) + "\n")

        if trans_parts:
            trans_path = os.path.join(songs_dir, f"{os.path.basename(base_name)}_trans.lrc")
            with open(trans_path, 'w', encoding='utf8') as trans_file:
                trans_file.write("\n".join(trans_parts) + "\n")
        else:
            trans_path = ''

    except Exception as e:
        app.logger.error(f"无法转换TTML文件: {input_path}. 错误: {str(e)}")
        return False, None, None

    return True, lyric_path, trans_path or None

def preprocess_brackets(content):
    """预处理括号内容，保留时间标记边界

    该函数保留括号原样，避免把时间标记边界吞掉。
    仅做轻量的空白折叠，不再移除 ')(' / '(('。

    Args:
        content: 要处理的文本内容

    Returns:
        返回处理后的文本
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


CREATOR_LABEL = "创作者"


def _normalize_artists(artists: Optional[List[str]]) -> List[str]:
    if not artists:
        return []
    normalized: List[str] = []
    for artist in artists:
        if not artist:
            continue
        parts = re.split(r'\s*_\s*', str(artist))
        for part in parts:
            cleaned = part.strip()
            if cleaned:
                normalized.append(cleaned)
    return normalized


def _format_creator_text(artists: List[str]) -> Optional[str]:
    if not artists:
        return None
    joined = "、".join(artists)
    return f"{CREATOR_LABEL}: {joined}"


def _extract_offset_ms(lys_content: str) -> int:
    offset_match = re.search(r'\[offset:\s*(-?\d+)\s*\]', lys_content)
    if offset_match:
        try:
            return int(offset_match.group(1))
        except Exception:
            return 0
    return 0


def _get_last_lyric_end_ms(lys_content: str) -> int:
    last_end_ms = 0
    offset_ms = _extract_offset_ms(lys_content)
    for line in lys_content.splitlines():
        stripped_line = line.strip()
        if not stripped_line:
            continue
        if stripped_line.startswith('[from:') or stripped_line.startswith('[id:') or stripped_line.startswith('[offset:'):
            continue
        content_match = re.match(r'\[(?P<marker>[^\]]*)\](?P<content>.*)', stripped_line)
        if not content_match:
            continue
        marker = content_match.group('marker')
        if marker != '' and not marker.isdigit():
            continue
        content = content_match.group('content')
        syllables = parse_syllable_info(content, marker, offset=offset_ms)
        if not syllables:
            continue
        end_ms = syllables[-1]['start_ms'] + syllables[-1]['duration_ms']
        if end_ms > last_end_ms:
            last_end_ms = end_ms
    return last_end_ms


def _ensure_creator_line_in_lys(
    lys_content: str,
    artists: Optional[List[str]] = None,
    song_end_ms: Optional[int] = None
) -> str:
    if not artists:
        return lys_content
    if re.search(rf'{re.escape(CREATOR_LABEL)}\s*:', lys_content):
        return lys_content
    creator_text = _format_creator_text(_normalize_artists(artists))
    if not creator_text:
        return lys_content
    last_end_ms = _get_last_lyric_end_ms(lys_content)
    start_ms = max(0, last_end_ms)
    total_end_ms = max(start_ms, int(song_end_ms or 0), last_end_ms)
    duration_ms = max(0, total_end_ms - start_ms)
    creator_line = f"[]{creator_text}({start_ms},{duration_ms})"
    if lys_content and not lys_content.endswith("\n"):
        lys_content += "\n"
    return f"{lys_content}{creator_line}\n"


def _append_creator_line_to_lys_file(
    lyrics_path: Path,
    artists: Optional[List[str]] = None,
    song_end_ms: Optional[int] = None
) -> bool:
    try:
        raw_content = lyrics_path.read_text(encoding='utf-8', errors='ignore')
    except Exception:
        return False
    updated_content = _ensure_creator_line_in_lys(raw_content, artists, song_end_ms)
    if updated_content == raw_content:
        return False
    lyrics_path.write_text(updated_content, encoding='utf-8')
    return True


def _append_creator_line_to_ttml(
    ttml_text: str,
    artists: Optional[List[str]] = None,
    song_end_ms: Optional[int] = None
) -> Optional[str]:
    if not artists:
        return None
    if re.search(rf'{re.escape(CREATOR_LABEL)}\s*:', ttml_text):
        return ttml_text
    creator_text = _format_creator_text(_normalize_artists(artists))
    if not creator_text:
        return None
    try:
        dom: Document = xml.dom.minidom.parseString(ttml_text)
    except Exception:
        return None

    bodies = dom.getElementsByTagName('body')
    if not bodies:
        return None
    body = bodies[0]
    div_nodes = body.getElementsByTagName('div')
    container = div_nodes[0] if div_nodes else body

    last_end_ms = 0
    for p_node in body.getElementsByTagName('p'):
        end_attr = p_node.getAttribute('end')
        if end_attr:
            try:
                end_ms = ttml_time_to_ms(end_attr)
                if end_ms > last_end_ms:
                    last_end_ms = end_ms
            except Exception:
                continue

    start_ms = max(0, last_end_ms)
    total_end_ms = max(start_ms, int(song_end_ms or 0), last_end_ms)
    begin_str = ms_to_ttml_time(start_ms)
    end_str = ms_to_ttml_time(total_end_ms)

    creator_p = dom.createElement('p')
    creator_p.setAttribute('begin', begin_str)
    creator_p.setAttribute('end', end_str)
    creator_span = dom.createElement('span')
    creator_span.appendChild(dom.createTextNode(creator_text))
    creator_p.appendChild(creator_span)
    container.appendChild(creator_p)
    return dom.toxml()


def _inject_creator_line_into_ttml(
    ttml_text: str,
    artists: Optional[List[str]] = None,
    song_end_ms: Optional[int] = None
) -> Optional[str]:
    appended = _append_creator_line_to_ttml(ttml_text, artists, song_end_ms)
    if appended:
        return appended
    return None


def _extract_artists_from_meta(meta: Optional[Dict[str, Any]]) -> List[str]:
    if not meta or not isinstance(meta, dict):
        return []
    artists = meta.get('artists')
    if isinstance(artists, list):
        return _normalize_artists([str(item) for item in artists if item is not None])
    if isinstance(artists, str):
        return _normalize_artists([artists])
    return []


def _extract_duration_ms_from_meta(meta: Optional[Dict[str, Any]]) -> Optional[int]:
    if not meta or not isinstance(meta, dict):
        return None
    duration_ms = meta.get('duration_ms')
    if isinstance(duration_ms, (int, float)) and duration_ms > 0:
        return int(duration_ms)
    return None


def _extract_song_value_from_json(data: Optional[Dict[str, Any]]) -> Optional[str]:
    if not data or not isinstance(data, dict):
        return None
    song_value = data.get('song')
    if isinstance(song_value, str) and song_value.strip():
        return song_value
    meta = data.get('meta')
    if isinstance(meta, dict):
        meta_song = meta.get('song')
        if isinstance(meta_song, str) and meta_song.strip():
            return meta_song
    return None


def _get_audio_duration_ms(audio_path: Path) -> Optional[int]:
    if not audio_path.exists():
        return None
    try:
        import librosa
    except Exception:
        return None
    try:
        duration_sec = float(librosa.get_duration(path=str(audio_path)))
        if not math.isfinite(duration_sec) or duration_sec <= 0:
            return None
        return int(round(duration_sec * 1000))
    except Exception:
        return None


def _get_song_duration_ms_from_json(data: Optional[Dict[str, Any]]) -> Optional[int]:
    song_value = _extract_song_value_from_json(data)
    if not song_value:
        return None
    try:
        song_path = resolve_resource_path(song_value, 'songs')
    except ValueError:
        return None
    return _get_audio_duration_ms(song_path)


def _cleanup_temp_ttml_files(now_ts: Optional[float] = None) -> None:
    now_ts = now_ts or time.time()
    expired = [path for path, ts in TEMP_TTML_FILES.items() if ts <= now_ts]
    for path in expired:
        try:
            Path(path).unlink(missing_ok=True)
        except Exception:
            pass
        TEMP_TTML_FILES.pop(path, None)


def _create_player_ttml_from_text(
    ttml_text: str,
    artists: Optional[List[str]],
    duration_ms: Optional[int]
) -> Optional[str]:
    updated = _inject_creator_line_into_ttml(ttml_text, artists, duration_ms)
    if not updated:
        return None
    unique = uuid.uuid4().hex
    filename = f"lyrics_player_{unique}.ttml"
    output_path = SONGS_DIR / filename
    output_path.write_text(updated, encoding='utf-8')
    TEMP_TTML_FILES[str(output_path)] = time.time() + TEMP_TTML_TTL_SEC
    return build_public_url('songs', filename)


def _get_artists_from_related_json(lyrics_path: Path) -> Tuple[List[str], Optional[int]]:
    related_jsons = find_related_json(lyrics_path)
    if not related_jsons:
        return [], None
    for json_path in related_jsons:
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            meta = data.get('meta', {})
            artists = _extract_artists_from_meta(meta)
            duration_ms = _get_song_duration_ms_from_json(data)
            if artists or duration_ms:
                return artists, duration_ms
        except Exception:
            continue
    return [], None


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


def _translation_path_from_hint(translation_hint: Optional[str]) -> Optional[str]:
    """根据显式传入的翻译路径提示解析实际文件。"""
    if not translation_hint:
        return None

    try:
        hint_path = resolve_resource_path(translation_hint, 'songs')
    except ValueError:
        return None

    return str(hint_path) if hint_path.exists() else None


def _translation_path_from_meta_json(lyrics_path: Path) -> Optional[str]:
    """从 static 根目录的歌曲 JSON 元数据中查找显式配置的翻译文件。"""
    target_path = lyrics_path.resolve()

    for json_path in STATIC_DIR.glob('*.json'):
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                payload = json.load(f)
        except Exception:
            continue

        meta = payload.get('meta') if isinstance(payload, dict) else None
        if not isinstance(meta, dict):
            continue

        lyrics_value = meta.get('lyrics')
        if not lyrics_value:
            continue

        lyrics_url, translation_url, _, _ = parse_meta_lyrics(lyrics_value)
        if not lyrics_url or not translation_url or translation_url == '!':
            continue

        try:
            meta_lyrics_path = resolve_resource_path(lyrics_url, 'songs').resolve()
            translation_path = resolve_resource_path(translation_url, 'songs')
        except ValueError:
            continue

        if meta_lyrics_path == target_path and translation_path.exists():
            return str(translation_path)

    return None


def find_translation_file(lyrics_path, translation_hint: Optional[str] = None):
    """查找关联的翻译文件。优先使用显式路径，其次读取 JSON 元数据，最后再按文件名猜测。"""
    hinted_path = _translation_path_from_hint(translation_hint)
    if hinted_path:
        return hinted_path

    lyrics_path = Path(lyrics_path)

    meta_path = _translation_path_from_meta_json(lyrics_path)
    if meta_path:
        return meta_path

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


def lys_to_ttml(input_path, output_path, translation_hint: Optional[str] = None):
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
        trans_path = find_translation_file(input_path, translation_hint)
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


def lrc_to_ttml(input_path, output_path, translation_hint: Optional[str] = None):
    """将LRC格式转换为TTML格式（Apple风格）"""
    try:
        with open(input_path, 'r', encoding='utf-8') as f:
            lrc_content = f.read()
        author_name = extract_tag_value(lrc_content, 'by')

        # ---- 读取并解析翻译 LRC：转成 毫秒→文本 的字典，供"精确匹配" ----
        trans_path = find_translation_file(input_path, translation_hint)
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

        try:
            _, temp_path = _resolve_new_filename_in_directory(SONGS_DIR, f"temp_{file.filename}", required_suffix='.ttml')
        except ValueError as exc:
            return jsonify({'status': 'error', 'message': str(exc)})

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
        translation_path = data.get('translationPath')
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
            success, error_msg = lys_to_ttml(str(input_path), str(output_path), translation_path)
        elif file_ext == '.lrc':
            success, error_msg = lrc_to_ttml(str(input_path), str(output_path), translation_path)

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
        translation_path = data.get('translationPath')
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
            success, error_msg = lys_to_ttml(str(input_path), str(output_path), translation_path)
        elif file_ext == '.lrc':
            success, error_msg = lrc_to_ttml(str(input_path), str(output_path), translation_path)

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

def _convert_milliseconds_to_time(milliseconds: int) -> str:
    total_seconds = milliseconds // 1000
    minutes = total_seconds // 60
    seconds = total_seconds % 60
    millis = milliseconds % 1000
    return f"{minutes:02d}:{seconds:02d}.{millis:03d}"


def extract_timestamps_from_content(lyrics_content: str) -> List[str]:
    timestamps: List[str] = []
    lines = (lyrics_content or '').split('\n')
    metadata_pattern = re.compile(r'^\s*\[(?:ar|ti|al|by|offset|id|from):', re.IGNORECASE)
    qrc_pattern = re.compile(r'^\s*\[(\d+)\s*,\s*(\d+)\]')
    lys_marker_pattern = re.compile(r'^\s*\[(\d*)\]')
    lrc_pattern = re.compile(r'^\s*\[(\d{1,2}):(\d{2})(?:\.(\d{1,3}))?\]')

    for raw_line in lines:
        line = raw_line.strip()
        if not line or metadata_pattern.match(line):
            continue

        qrc_match = qrc_pattern.match(line)
        if qrc_match:
            try:
                start_ms = int(qrc_match.group(1))
                timestamps.append(f"[{_convert_milliseconds_to_time(start_ms)}]")
                continue
            except ValueError:
                continue

        lrc_match = lrc_pattern.match(line)
        if lrc_match:
            try:
                minutes = int(lrc_match.group(1))
                seconds = int(lrc_match.group(2))
                millis_str = lrc_match.group(3) or ''
                millis = int((millis_str + '000')[:3]) if millis_str else 0
                total_ms = (minutes * 60 + seconds) * 1000 + millis
                timestamps.append(f"[{_convert_milliseconds_to_time(total_ms)}]")
                continue
            except ValueError:
                continue

        if lys_marker_pattern.match(line):
            match = re.search(r'\((\d+),', line)
            if match:
                try:
                    timestamp = int(match.group(1))
                    timestamps.append(f"[{_convert_milliseconds_to_time(timestamp)}]")
                    continue
                except ValueError:
                    continue
    return timestamps


def extract_lyrics_entries_from_content(lyrics_content: str) -> List[Dict[str, Any]]:
    extracted_entries: List[Dict[str, Any]] = []
    entry_index = 0
    for source_line_no, line in enumerate((lyrics_content or '').split('\n'), 1):
        raw_line = str(line or '')
        line_tag_match = re.match(r'^\s*\[(\d+)\]', raw_line)
        line_tag = line_tag_match.group(1) if line_tag_match else ''
        line_lyrics = re.sub(r'\[.*?\]', '', raw_line)
        line_lyrics = re.sub(r'\(\d+,\d+\)', '', line_lyrics)
        line_lyrics = line_lyrics.strip()
        if line_lyrics:
            entry_index += 1
            extracted_entries.append({
                'source_line_no': source_line_no,
                'entry_index': entry_index,
                'raw_line': raw_line,
                'line_tag': line_tag,
                'text': line_lyrics,
            })
    return extracted_entries


def extract_lyrics_from_content(lyrics_content: str) -> List[str]:
    return [entry['text'] for entry in extract_lyrics_entries_from_content(lyrics_content)]


def has_timestamp_markers(content: str) -> bool:
    candidate_lines = [
        line.strip()
        for line in (content or '').splitlines()
        if line.strip()
        and not line.startswith('[by:')
        and not line.startswith('[ti:')
        and not line.startswith('[ar:')
    ]
    return any(
        re.search(r'\(\d+,\d+\)', line)
        or re.match(r'^\s*\[\d+\s*,\s*\d+\]', line)
        or re.match(r'^\s*\[\d{1,2}:\d{2}(?:\.\d{1,3})?\]', line)
        for line in candidate_lines
    )


def parse_translations_by_id(raw_text: str) -> Dict[str, Dict[str, str]]:
    id_pattern = re.compile(r'^\s*\[ID:(.+?)\]\s*$')
    grouped: Dict[str, Dict[str, str]] = {}
    current_id: Optional[str] = None
    for raw_line in (raw_text or '').splitlines():
        line = raw_line.strip()
        if not line:
            continue
        id_match = id_pattern.match(line)
        if id_match:
            current_id = id_match.group(1).strip()
            grouped.setdefault(current_id, {})
            continue
        if current_id is None:
            continue
        parsed_line = parse_numbered_translation_line(line)
        if not parsed_line:
            continue
        display_index, text = parsed_line
        grouped.setdefault(current_id, {})[display_index] = text
    return grouped


@app.route('/extract_timestamps', methods=['POST'])
def extract_timestamps():
    try:
        data = request.json
        lyrics_content = data.get('content', '')
        timestamps = extract_timestamps_from_content(lyrics_content)
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
        extracted_lyrics = extract_lyrics_from_content(lyrics_content)
        cleaned_lyrics = '\n'.join(extracted_lyrics)
        return jsonify({
            'status': 'success',
            'content': cleaned_lyrics
        })
    except Exception as e:
        app.logger.error(f"提取歌词时出错: {str(e)}", exc_info=True)
        return jsonify({'status': 'error', 'message': f'处理请求时出错: {str(e)}'})

@app.route('/get_ai_settings', methods=['GET'])
def get_ai_settings():
    settings_store = get_ai_settings_store()
    settings_state = settings_store.get('settings', {}) if isinstance(settings_store.get('settings'), dict) else normalize_ai_settings_state({})
    effective_settings_state = resolve_effective_ai_settings_state(settings_store)
    sync_ai_translation_settings(effective_settings_state)
    preset_store = get_ai_preset_store()
    permissions = default_device_permissions(write_access=True) if is_local_request() else get_current_device_auth_context().get('permissions', dict(DEFAULT_DEVICE_PERMISSIONS))
    active_preset = get_active_ai_preset()
    active_settings = sanitize_preset_for_device(active_preset or {}, permissions)

    # Source status (manual vs preset binding)
    source_mode = str(settings_store.get('source_mode') or 'manual').strip().lower()
    if source_mode not in {'manual', 'preset'}:
        source_mode = 'manual'
    source_preset_id = str(settings_store.get('source_preset_id') or '').strip() if source_mode == 'preset' else ''
    source_preset = get_ai_preset_by_id(source_preset_id) if source_preset_id else None
    source_preset_sanitized = sanitize_preset_for_device(source_preset or {}, permissions) if source_preset else None
    if source_mode == 'preset':
        source_kind, source_label = classify_ai_preset_source(source_preset, source_preset_id)
    else:
        source_kind, source_label = ('manual', '独立当前设置')

    runtime_for_summary = resolve_runtime_ai_config({})
    runtime_summary = build_ai_runtime_summary(settings_store, runtime_for_summary, permissions)
    field_visibility = build_ai_field_visibility(permissions)

    return jsonify({
        'status': 'success',
        'settings': sanitize_settings_for_device(effective_settings_state, permissions),
        'stored_settings': sanitize_settings_for_device(settings_state, permissions),
        'effective_settings': sanitize_settings_for_device(effective_settings_state, permissions),
        'field_visibility': field_visibility,
        'runtime_summary': runtime_summary,
        'preset': active_settings,
        'active_preset_id': preset_store.get('active_preset_id', ''),
        'source_mode': source_mode,
        'source_preset_id': source_preset_id,
        'source_preset': source_preset_sanitized,
        'source_kind': source_kind,
        'source_label': source_label,
        'presets': [sanitize_preset_for_device(preset, permissions) for preset in preset_store.get('presets', [])],
        'permissions': permissions,
        'can_use_ai': bool(permissions.get('ai_use', False) or is_local_request()),
        'can_edit_preset': bool(permissions.get('ai_edit_preset', False) or is_local_request()),
        'can_save_settings': bool(permissions.get('ai_use', False) or is_local_request()),
        'defaults': {
            'system_prompt': AI_TRANSLATION_DEFAULTS.get('system_prompt', ''),
            'thinking_system_prompt': AI_TRANSLATION_DEFAULTS.get('thinking_system_prompt', ''),
            'strip_brackets': AI_TRANSLATION_DEFAULTS.get('strip_brackets', False),
            'experimental_full_line_bracket_strip': AI_TRANSLATION_DEFAULTS.get('experimental_full_line_bracket_strip', False),
            'experimental_bracket_line_as_subline': AI_TRANSLATION_DEFAULTS.get('experimental_bracket_line_as_subline', False),
            'batch_fixed_prompt': BATCH_TRANSLATION_FIXED_PROMPT
        }
    })

@app.route('/save_ai_settings', methods=['POST'])
def save_ai_settings():
    if not can_use_ai():
        return jsonify({'status': 'error', 'message': '当前设备没有保存 AI 设置的权限'}), 403
    try:
        data = request.get_json(silent=True) or {}
        settings_store = get_ai_settings_store()
        current_settings = settings_store.get('settings', {}) if isinstance(settings_store.get('settings'), dict) else normalize_ai_settings_state({})
        intent = str(data.get('intent') or '').strip().lower()

        # Prevent implicit overwrite: empty string means unchanged for api_key and flat base_url/model keys
        # (base_url, model, thinking_base_url, thinking_model) from the frontend — not nested translation.model shapes.
        # Explicit clearing must be requested via dedicated flags.
        clear_translation_api_key = parse_bool(data.get('clear_translation_api_key'), False)
        clear_thinking_api_key = parse_bool(data.get('clear_thinking_api_key'), False)
        if 'api_key' in data and not clear_translation_api_key:
            if not str(data.get('api_key') or '').strip():
                data.pop('api_key', None)
        if 'thinking_api_key' in data and not clear_thinking_api_key:
            if not str(data.get('thinking_api_key') or '').strip():
                data.pop('thinking_api_key', None)
        for key in ('base_url', 'model', 'thinking_base_url', 'thinking_model'):
            if key in data and not str(data.get(key) or '').strip():
                data.pop(key, None)

        preset_secret_updated = False
        if intent == 'bind_preset':
            incoming_source_preset_id = str(data.get('source_preset_id') or data.get('preset_id') or '').strip()
            if not incoming_source_preset_id:
                return jsonify({'status': 'error', 'message': '绑定失败：请选择要绑定的预设'}), 400
            bound_preset = get_ai_preset_by_id(incoming_source_preset_id)
            if not bound_preset:
                return jsonify({'status': 'error', 'message': f'绑定失败：预设不存在或已被删除（{incoming_source_preset_id}）'}), 400
            incoming_translation_api_key = str(data.get('api_key') or '').strip()
            incoming_thinking_api_key = str(data.get('thinking_api_key') or '').strip()
            if incoming_translation_api_key or incoming_thinking_api_key:
                if can_edit_ai_presets():
                    update_payload = {
                        'id': incoming_source_preset_id,
                    }
                    if incoming_translation_api_key:
                        update_payload['api_key'] = incoming_translation_api_key
                    if incoming_thinking_api_key:
                        update_payload['thinking_api_key'] = incoming_thinking_api_key
                    update_ai_preset_store_from_payload(
                        [update_payload],
                        mode='upsert',
                        permissions=default_device_permissions(write_access=True) if is_local_request() else get_current_device_auth_context().get('permissions', dict(DEFAULT_DEVICE_PERMISSIONS))
                    )
                    preset_secret_updated = True
                    bound_preset = get_ai_preset_by_id(incoming_source_preset_id)
                else:
                    preset_name = str(bound_preset.get('name') or incoming_source_preset_id).strip() or incoming_source_preset_id
                    return jsonify({'status': 'error', 'message': f'绑定失败：当前设备没有修改 AI 预设的权限，无法保存输入的 API 密钥到预设「{preset_name}」。请清空输入框后绑定已有凭据，或使用有权限的设备更新预设'}), 403
            if not ai_preset_secret_presence(bound_preset).get('translation_api_key_present'):
                preset_name = str(bound_preset.get('name') or incoming_source_preset_id).strip() or incoming_source_preset_id
                return jsonify({'status': 'error', 'message': f'绑定失败：预设「{preset_name}」缺少翻译模型 API 凭据。请输入 API 密钥，或先更新该预设后再绑定'}), 400
            settings_store['source_mode'] = 'preset'
            settings_store['source_preset_id'] = incoming_source_preset_id
            next_settings = current_settings
        elif intent == 'apply_manual_settings':
            preview_source_mode = str(data.get('source_mode') or settings_store.get('source_mode') or 'manual').strip().lower()
            if preview_source_mode not in {'manual', 'preset'}:
                preview_source_mode = 'manual'
            preview_source_preset_id = str(data.get('source_preset_id') or '').strip() if preview_source_mode == 'preset' else ''

            base_settings = current_settings
            if preview_source_mode == 'preset' and preview_source_preset_id:
                source_preset = get_ai_preset_by_id(preview_source_preset_id)
                if not source_preset:
                    return jsonify({'status': 'error', 'message': f'应用失败：预设不存在或已被删除（{preview_source_preset_id}）'}), 400
                base_settings = materialize_ai_settings_from_preset(source_preset)

            next_settings = normalize_ai_settings_state(data, base_settings)
            if clear_translation_api_key:
                next_settings.setdefault('translation', {})['api_key'] = ''
            if clear_thinking_api_key:
                next_settings.setdefault('thinking', {})['api_key'] = ''
            settings_store['settings'] = next_settings
            settings_store['source_mode'] = 'manual'
            settings_store['source_preset_id'] = ''
        else:
            # Backward-compatible legacy save semantics.
            incoming_source_mode = str(data.get('source_mode') or settings_store.get('source_mode') or 'manual').strip().lower()
            if incoming_source_mode not in {'manual', 'preset'}:
                incoming_source_mode = 'manual'
            incoming_source_preset_id = str(data.get('source_preset_id') or '').strip() if incoming_source_mode == 'preset' else ''
            if incoming_source_mode == 'preset':
                if not incoming_source_preset_id:
                    incoming_source_mode = 'manual'
                    incoming_source_preset_id = ''
                elif not get_ai_preset_by_id(incoming_source_preset_id):
                    return jsonify({'status': 'error', 'message': f'绑定失败：预设不存在或已被删除（{incoming_source_preset_id}）'}), 400
            settings_store['source_mode'] = incoming_source_mode
            settings_store['source_preset_id'] = incoming_source_preset_id

            next_settings = normalize_ai_settings_state(data, current_settings)
            if clear_translation_api_key:
                next_settings.setdefault('translation', {})['api_key'] = ''
            if clear_thinking_api_key:
                next_settings.setdefault('thinking', {})['api_key'] = ''
            settings_store['settings'] = next_settings

        settings_store['updated_at'] = now_iso()
        save_ai_settings_store(settings_store)
        sync_ai_translation_settings(resolve_effective_ai_settings_state(settings_store))

        response_permissions = default_device_permissions(write_access=True) if is_local_request() else get_current_device_auth_context().get('permissions', dict(DEFAULT_DEVICE_PERMISSIONS))
        preset_store = get_ai_preset_store()
        stored_settings_rsp = settings_store.get('settings', {}) if isinstance(settings_store.get('settings'), dict) else normalize_ai_settings_state({})
        effective_settings_rsp = resolve_effective_ai_settings_state(settings_store)
        active_preset = get_active_ai_preset() or {}
        source_mode_rsp = str(settings_store.get('source_mode') or 'manual').strip().lower()
        source_preset_id_rsp = str(settings_store.get('source_preset_id') or '').strip() if source_mode_rsp == 'preset' else ''
        source_preset_rsp = get_ai_preset_by_id(source_preset_id_rsp) if source_preset_id_rsp else None
        source_preset_rsp_sanitized = sanitize_preset_for_device(source_preset_rsp or {}, response_permissions) if source_preset_rsp else None
        if source_mode_rsp == 'preset':
            source_kind_rsp, source_label_rsp = classify_ai_preset_source(source_preset_rsp, source_preset_id_rsp)
        else:
            source_kind_rsp, source_label_rsp = ('manual', '独立当前设置')

        runtime_for_summary_rsp = resolve_runtime_ai_config({})
        runtime_summary_rsp = build_ai_runtime_summary(settings_store, runtime_for_summary_rsp, response_permissions)
        field_visibility_rsp = build_ai_field_visibility(response_permissions)

        return jsonify({
            'status': 'success',
            'settings': sanitize_settings_for_device(effective_settings_rsp, response_permissions),
            'stored_settings': sanitize_settings_for_device(stored_settings_rsp, response_permissions),
            'effective_settings': sanitize_settings_for_device(effective_settings_rsp, response_permissions),
            'field_visibility': field_visibility_rsp,
            'runtime_summary': runtime_summary_rsp,
            'preset': sanitize_preset_for_device(active_preset, response_permissions),
            'active_preset_id': preset_store.get('active_preset_id', ''),
            'source_mode': source_mode_rsp,
            'source_preset_id': source_preset_id_rsp,
            'source_preset': source_preset_rsp_sanitized,
            'source_kind': source_kind_rsp,
            'source_label': source_label_rsp,
            'preset_secret_updated': preset_secret_updated,
            'presets': [sanitize_preset_for_device(preset, response_permissions) for preset in preset_store.get('presets', [])],
            'permissions': response_permissions,
            'can_use_ai': bool(response_permissions.get('ai_use', False) or is_local_request()),
            'can_edit_preset': bool(response_permissions.get('ai_edit_preset', False) or is_local_request()),
            'can_save_settings': bool(response_permissions.get('ai_use', False) or is_local_request()),
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})


@app.route('/ai-presets', methods=['GET', 'PUT', 'POST'])
def ai_presets_collection():
    if request.method == 'GET':
        store = get_ai_preset_store()
        permissions = default_device_permissions(write_access=True) if is_local_request() else get_current_device_auth_context().get('permissions', dict(DEFAULT_DEVICE_PERMISSIONS))
        return jsonify({
            'status': 'success',
            'active_preset_id': store.get('active_preset_id', ''),
            'presets': [sanitize_preset_for_device(preset, permissions) for preset in store.get('presets', [])],
            'permissions': permissions,
            'can_edit_preset': bool(permissions.get('ai_edit_preset', False) or is_local_request()),
        })

    if not can_edit_ai_presets():
        return jsonify({'status': 'error', 'message': '当前设备没有修改 AI 预设的权限'}), 403

    data = request.get_json(silent=True) or {}
    if request.method == 'POST':
        payload = dict(data)
        active_preset_id = str(payload.get('id') or payload.get('preset_id') or DEFAULT_AI_PRESET_ID).strip() or DEFAULT_AI_PRESET_ID
        payload['id'] = active_preset_id
        presets_payload = [payload]
    else:
        presets_payload = data.get('presets') if isinstance(data.get('presets'), list) else data if isinstance(data, list) else []
        active_preset_id = str(data.get('active_preset_id') or data.get('preset_id') or '').strip() or None

    if not isinstance(presets_payload, list):
        return jsonify({'status': 'error', 'message': '预设数据格式不正确'}), 400

    store = update_ai_preset_store_from_payload(presets_payload, active_preset_id=active_preset_id, mode='upsert' if request.method == 'POST' else 'replace_all')
    permissions = default_device_permissions(write_access=True) if is_local_request() else get_current_device_auth_context().get('permissions', dict(DEFAULT_DEVICE_PERMISSIONS))
    return jsonify({
        'status': 'success',
        'active_preset_id': store.get('active_preset_id', ''),
        'presets': [sanitize_preset_for_device(preset, permissions) for preset in store.get('presets', [])],
    })


@app.route('/ai-presets/migrate-local-cache', methods=['POST'])
def ai_presets_migrate_local_cache():
    if not can_edit_ai_presets():
        return jsonify({'status': 'error', 'message': '当前设备没有修改 AI 预设的权限'}), 403

    data = request.get_json(silent=True) or {}
    presets_payload = data.get('presets') if isinstance(data.get('presets'), list) else []
    active_preset_id = str(data.get('active_preset_id') or '').strip() or None

    response_permissions = default_device_permissions(write_access=True) if is_local_request() else get_current_device_auth_context().get('permissions', dict(DEFAULT_DEVICE_PERMISSIONS))
    store_before = get_ai_preset_store()
    count_before = len(store_before.get('presets', []) or [])
    store = update_ai_preset_store_from_payload(presets_payload, active_preset_id=active_preset_id, mode='merge_legacy_local', permissions=response_permissions)
    count_after = len(store.get('presets', []) or [])

    return jsonify({
        'status': 'success',
        'migrated': True,
        'before': count_before,
        'after': count_after,
        'active_preset_id': store.get('active_preset_id', ''),
        'presets': [sanitize_preset_for_device(preset, response_permissions) for preset in store.get('presets', [])],
        'permissions': response_permissions,
    })


@app.route('/ai/reasoning-control-capability', methods=['GET'])
def ai_reasoning_control_capability():
    if not can_use_ai():
        return jsonify({'status': 'error', 'message': '当前设备没有使用 AI 的权限'}), 403
    provider = str(request.args.get('provider') or '').strip()
    base_url = str(request.args.get('base_url') or '').strip()
    model = str(request.args.get('model') or '').strip()
    cap = get_reasoning_control_capability(provider=provider, base_url=base_url, model=model)
    return jsonify({
        'status': 'success',
        'capability': {
            'supported': bool(cap.get('supported', False)),
            'user_selectable': bool(cap.get('user_selectable', True)),
            'control_field_sent': bool(cap.get('control_field_sent', True)),
            'status': str(cap.get('status') or 'unknown'),
            'guarantee_level': str(cap.get('guarantee_level') or 'fallback'),
            'schema': str(cap.get('schema') or ''),
            'provider': str(cap.get('provider') or ''),
            'message': str(cap.get('message') or ''),
        }
    })


@app.route('/ai-presets/<preset_id>', methods=['GET', 'PUT', 'DELETE'])
def ai_preset_item(preset_id):
    if request.method == 'GET':
        preset = get_ai_preset_by_id(preset_id)
        if not preset:
            return jsonify({'status': 'error', 'message': '预设不存在'}), 404
        permissions = default_device_permissions(write_access=True) if is_local_request() else get_current_device_auth_context().get('permissions', dict(DEFAULT_DEVICE_PERMISSIONS))
        return jsonify({
            'status': 'success',
            'preset': sanitize_preset_for_device(preset, permissions),
        })

    if not can_edit_ai_presets():
        return jsonify({'status': 'error', 'message': '当前设备没有修改 AI 预设的权限'}), 403

    store = get_ai_preset_store()
    preset = get_ai_preset_by_id(preset_id)
    if request.method == 'DELETE':
        if not preset:
            return jsonify({'status': 'error', 'message': '预设不存在'}), 404
        next_presets = [item for item in store.get('presets', []) if item.get('id') != preset_id]
        next_active_id = store.get('active_preset_id')
        if next_active_id == preset_id:
            next_active_id = next_presets[0]['id'] if next_presets else DEFAULT_AI_PRESET_ID
        save_ai_preset_store({'presets': next_presets, 'active_preset_id': next_active_id})
        permissions = default_device_permissions(write_access=True) if is_local_request() else get_current_device_auth_context().get('permissions', dict(DEFAULT_DEVICE_PERMISSIONS))
        return jsonify({'status': 'success', 'presets': [sanitize_preset_for_device(preset_item, permissions) for preset_item in get_ai_preset_store().get('presets', [])], 'active_preset_id': get_ai_preset_store().get('active_preset_id', '')})

    data = request.get_json(silent=True) or {}
    payload = dict(data)
    payload['id'] = preset_id
    response_permissions = default_device_permissions(write_access=True) if is_local_request() else get_current_device_auth_context().get('permissions', dict(DEFAULT_DEVICE_PERMISSIONS))
    next_store = update_ai_preset_store_from_payload([payload], active_preset_id=preset_id, mode='upsert', permissions=response_permissions)
    merged_record = get_ai_preset_by_id(preset_id) or {}
    permissions = default_device_permissions(write_access=True) if is_local_request() else get_current_device_auth_context().get('permissions', dict(DEFAULT_DEVICE_PERMISSIONS))
    return jsonify({
        'status': 'success',
        'preset': sanitize_preset_for_device(merged_record, permissions),
        'active_preset_id': next_store.get('active_preset_id', ''),
    })


@app.route('/ai-presets/<preset_id>/resolve', methods=['POST'])
def ai_preset_resolve(preset_id):
    if not can_use_ai():
        return jsonify({'status': 'error', 'message': '当前设备没有使用 AI 的权限'}), 403
    preset = get_ai_preset_by_id(preset_id)
    if not preset:
        return jsonify({'status': 'error', 'message': '预设不存在'}), 404
    permissions = default_device_permissions(write_access=True) if is_local_request() else get_current_device_auth_context().get('permissions', dict(DEFAULT_DEVICE_PERMISSIONS))
    return jsonify({
        'status': 'success',
        'preset': sanitize_preset_for_device(preset, permissions),
        'active_preset_id': get_ai_preset_store().get('active_preset_id', ''),
    })

@app.route('/probe_ai', methods=['POST'])
def probe_ai():
    if not can_use_ai():
        return jsonify({'status': 'error', 'message': '当前设备没有使用 AI 的权限'}), 403
    try:
        request_data = request.get_json(silent=True) or {}
        task_payload = extract_ai_task_payload(request_data)
        mode = (task_payload.get('mode') or 'translation').lower()
        runtime = resolve_runtime_ai_config(request_data)
        source_mode = str(runtime.get('source_mode') or 'manual')
        source_preset_name = str(runtime.get('name') or runtime.get('id') or '').strip()
        if mode == 'thinking':
            api_key = runtime.get('thinking', {}).get('api_key') or runtime.get('translation', {}).get('api_key')
            base_url_raw = runtime.get('thinking', {}).get('base_url')
            model = runtime.get('thinking', {}).get('model')
            system_prompt = runtime.get('thinking', {}).get('system_prompt') or ''
        else:
            api_key = runtime.get('translation', {}).get('api_key')
            base_url_raw = runtime.get('translation', {}).get('base_url')
            model = runtime.get('translation', {}).get('model')
            system_prompt = runtime.get('translation', {}).get('system_prompt') or ''
        compat_mode = parse_bool(task_payload.get('compat_mode'), parse_bool(runtime.get('translation', {}).get('compat_mode'), False))

        # 规范化 base_url，去掉用户误填的 /chat/completions 等尾巴
        def _normalize_base_url(u: str) -> str:
            if not u: return u
            u = u.strip().rstrip('/')
            import re
            return re.sub(r'/(chat|responses)/(completions|streams?)$', '', u, flags=re.I)

        base_url = _normalize_base_url(base_url_raw)
        if not api_key:
            target = '思考模型' if mode == 'thinking' else '翻译模型'
            if source_mode == 'preset':
                preset_label = source_preset_name or '（未知预设）'
                return jsonify({'status': 'error', 'message': f'当前预设 {preset_label} 未配置后端 {target} API 密钥'})
            return jsonify({'status': 'error', 'message': f'当前 AI 设置未保存{target} API 密钥'})
        if not base_url:
            target = '思考模型' if mode == 'thinking' else '翻译模型'
            return jsonify({'status': 'error', 'message': f'未提供{target}的Base URL'})
        if not model:
            target = '思考模型' if mode == 'thinking' else '翻译模型'
            return jsonify({'status': 'error', 'message': f'未提供{target}的模型名'})

        client = build_openai_client(api_key=api_key, base_url=base_url)
        expect_reasoning = bool(runtime.get('translation', {}).get('expect_reasoning', AI_TRANSLATION_SETTINGS.get('expect_reasoning', True)))
        translation_provider = runtime.get('translation', {}).get('provider') or AI_TRANSLATION_SETTINGS.get('provider', '')
        reasoning_cap = get_reasoning_control_capability(provider=translation_provider, base_url=base_url, model=model) if mode != 'thinking' else {'supported': True, 'control_field_sent': False, 'message': '', 'provider': translation_provider}
        reasoning_opts = build_reasoning_request_options(provider=translation_provider, base_url=base_url, model=model, expect_reasoning=expect_reasoning) if mode != 'thinking' else {}
        if compat_mode:
            probe_prompt = f"{system_prompt}\n\nping".strip() if system_prompt else 'ping'
            probe_messages = [
                {'role': 'user', 'content': probe_prompt}
            ]
        else:
            probe_messages = []
            if system_prompt:
                probe_messages.append({'role': 'system', 'content': system_prompt})
            probe_messages.append({'role': 'user', 'content': 'ping'})
        probe_response = client.chat.completions.create(
            model=model,
            messages=probe_messages,
            stream=True,
            **reasoning_opts
        )
        first_chunk = None
        for chunk in probe_response:
            first_chunk = chunk
            break
        usage = getattr(first_chunk, 'usage', None) if first_chunk is not None else None
        return jsonify({
            'status': 'success',
            'base_url': base_url,
            'mode': mode,
            'model': model,
            'probe': 'chat.completions.stream',
            'observed_chunk': first_chunk is not None,
            'reasoning_control': {
                'supported': bool(reasoning_cap.get('supported', False)),
                'control_field_sent': bool(reasoning_cap.get('control_field_sent', False)),
                'provider': reasoning_cap.get('provider', ''),
                'message': str(reasoning_cap.get('message') or ''),
            },
            'usage': {
                'prompt_tokens': getattr(usage, 'prompt_tokens', None),
                'completion_tokens': getattr(usage, 'completion_tokens', None),
                'total_tokens': getattr(usage, 'total_tokens', None)
            }
        })
    except Exception as e:
        app.logger.error(f"探活AI服务时出错: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': f'探活失败: {e}', 'base_url': base_url_raw, 'mode': request_data.get('mode', 'translation')})

@app.route('/translate_lyrics', methods=['POST'])
def translate_lyrics():
    try:
        # 获取请求数据
        request_data = request.get_json(silent=True) or {}
        task_payload = extract_ai_task_payload(request_data)
        if not can_use_ai():
            return jsonify({'status': 'error', 'message': '当前设备没有使用 AI 的权限'}), 403
        content = task_payload.get('content', '')
        auth_context = get_current_device_auth_context()
        runtime = resolve_runtime_ai_config(request_data)
        api_key = runtime.get('translation', {}).get('api_key', '')
        if not api_key:
            source_mode = str(runtime.get('source_mode') or 'manual')
            preset_label = str(runtime.get('name') or runtime.get('id') or '').strip()
            if source_mode == 'preset':
                preset_label = preset_label or '（未知预设）'
                return jsonify({'status': 'error', 'message': f'当前预设 {preset_label} 未配置后端 API 密钥'})
            return jsonify({'status': 'error', 'message': '当前 AI 设置未保存 API 密钥'})

        system_prompt = runtime.get('translation', {}).get('system_prompt', '')
        provider = runtime.get('translation', {}).get('provider') or AI_TRANSLATION_SETTINGS['provider']
        base_url = runtime.get('translation', {}).get('base_url') or AI_TRANSLATION_SETTINGS['base_url']
        model = runtime.get('translation', {}).get('model') or AI_TRANSLATION_SETTINGS['model']
        expect_reasoning = runtime.get('translation', {}).get('expect_reasoning', AI_TRANSLATION_SETTINGS['expect_reasoning'])
        strip_brackets = parse_bool(runtime.get('translation', {}).get('strip_brackets'), AI_TRANSLATION_SETTINGS.get('strip_brackets', False))
        experimental_full_line_bracket_strip = parse_bool(runtime.get('translation', {}).get('experimental_full_line_bracket_strip'), AI_TRANSLATION_SETTINGS.get('experimental_full_line_bracket_strip', False))
        experimental_bracket_line_as_subline = parse_bool(runtime.get('translation', {}).get('experimental_bracket_line_as_subline'), AI_TRANSLATION_SETTINGS.get('experimental_bracket_line_as_subline', False))

        compat_mode = parse_bool(runtime.get('translation', {}).get('compat_mode'), AI_TRANSLATION_SETTINGS['compat_mode'])
        thinking_enabled = parse_bool(runtime.get('thinking', {}).get('enabled'), AI_TRANSLATION_SETTINGS.get('thinking_enabled', True))

        thinking_api_key = runtime.get('thinking', {}).get('api_key') or api_key
        thinking_provider = runtime.get('thinking', {}).get('provider') or provider
        thinking_base_url = runtime.get('thinking', {}).get('base_url') or base_url
        thinking_model = runtime.get('thinking', {}).get('model') or model
        thinking_system_prompt = runtime.get('thinking', {}).get('system_prompt') or ''

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

        reasoning_cap = get_reasoning_control_capability(provider=provider, base_url=base_url, model=model)

        # 获取客户端信息用于日志
        client_ip = request.remote_addr
        user_agent = request.headers.get('User-Agent', 'Unknown')
        request_id = f"{int(time.time()*1000)}_{random.randint(1000, 9999)}"
        audit_started_at = time.monotonic()

        def _summarize_content(raw_text: str) -> Dict[str, Any]:
            text = raw_text if isinstance(raw_text, str) else str(raw_text or '')
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            preview = '\n'.join(lines[:6])
            preview = preview[:320]
            sha = hashlib.sha256(text.encode('utf-8', errors='ignore')).hexdigest() if text else ''
            return {
                'content_preview': preview,
                'content_length': len(text),
                'line_count': len(lines),
                'sha256': sha,
            }

        def _build_ai_usage_audit_base() -> Dict[str, Any]:
            credential_id = str(auth_context.get('credential_id') or (auth_context.get('credential') or {}).get('credential_id') or '').strip()
            if auth_context.get('is_system_admin'):
                credential_id = 'system'
            auth_type = str(auth_context.get('auth_type') or ('system' if auth_context.get('is_system_admin') else '')).strip()
            effective_preset_id = str(runtime.get('id') or '').strip() or 'manual'
            translation_model = str(model or '')
            return {
                'request_id': request_id,
                'event': 'ai_translate_lyrics',
                'credential_id': credential_id,
                'auth_type': auth_type,
                'device_id': auth_context.get('device_id'),
                'preset_id': effective_preset_id,
                'preset_name': str(runtime.get('name') or ''),
                'source_mode': str(runtime.get('source_mode') or ''),
                'resolved_from': str(runtime.get('resolved_from') or ''),
                'provider': provider,
                'base_url': base_url,
                'translation_model': translation_model,
                'effective_model': translation_model,
                'model': translation_model,
                'thinking_enabled': bool(thinking_enabled),
                'thinking_provider': thinking_provider,
                'thinking_base_url': thinking_base_url,
                'thinking_model': thinking_model,
                'expect_reasoning': bool(expect_reasoning),
                'compat_mode': bool(compat_mode),
                'reasoning_control_supported': bool(reasoning_cap.get('supported', False)),
                'reasoning_control_message': str(reasoning_cap.get('message') or ''),
            }
        
        app.logger.info("="*50)
        app.logger.info(f"开始处理翻译请求 [ID: {request_id}]")
        app.logger.info(f"客户端: {client_ip}, User-Agent: {user_agent}")
        app.logger.info(f"原始歌词内容长度: {len(content)} 字符")
        app.logger.info(f"预设ID: {runtime.get('id', '')}, API密钥已由后端托管")
        app.logger.info(
            "预处理开关: strip_brackets=%s, experimental_full_line_bracket_strip=%s, experimental_bracket_line_as_subline=%s",
            strip_brackets,
            experimental_full_line_bracket_strip,
            experimental_bracket_line_as_subline
        )

        items = task_payload.get('items')
        if isinstance(items, list) and items:
            batch_thinking_enabled = parse_bool(task_payload.get('thinking_enabled'), False)
            valid_items: List[Dict[str, Any]] = []
            for idx, raw_item in enumerate(items, 1):
                item = raw_item if isinstance(raw_item, dict) else {}
                item_id = str(item.get('id', idx)).strip() or str(idx)
                item_content = item.get('content', '')
                if not isinstance(item_content, str):
                    item_content = str(item_content or '')
                item_entries = extract_lyrics_entries_from_content(item_content)
                item_lyrics = [entry['text'] for entry in item_entries]
                item_timestamps = extract_timestamps_from_content(item_content)
                item_has_timestamps = len(item_timestamps) > 0
                item_has_timestamp_markers = has_timestamp_markers(item_content)

                if not item_lyrics or all(not line.strip() for line in item_lyrics):
                    continue
                if item_has_timestamp_markers and not item_has_timestamps:
                    continue
                if item_has_timestamps and len(item_timestamps) != len(item_lyrics):
                    continue

                item_prompt_lines = build_translation_prompt_lines(
                    item_entries,
                    strip_brackets=strip_brackets,
                    experimental_full_line_bracket_strip=experimental_full_line_bracket_strip,
                    experimental_bracket_line_as_subline=experimental_bracket_line_as_subline
                )
                if not item_prompt_lines:
                    continue
                has_sublines = any(line['is_subline'] for line in item_prompt_lines)
                item_song_name = str(item.get('song_name') or '').strip()
                if not item_song_name:
                    item_song_name = _ai_usage_resolve_song_name(str(item.get('jsonFile') or ''))

                valid_items.append({
                    'id': item_id,
                    'jsonFile': item.get('jsonFile', ''),
                    'lyricsPath': item.get('lyricsPath', ''),
                    'translationPath': item.get('translationPath', ''),
                    'song_name': item_song_name,
                    'lyrics': item_lyrics,
                    'lyrics_entries': item_entries,
                    'prompt_lines': item_prompt_lines,
                    'timestamps': item_timestamps,
                    'hasTimestamps': item_has_timestamps,
                    'hasSublines': has_sublines
                })

            if not valid_items:
                return jsonify({'status': 'error', 'message': '批量请求中没有可翻译的歌词项'})

            audit_base = _build_ai_usage_audit_base()
            json_files = [str(item.get('jsonFile') or '') for item in valid_items if str(item.get('jsonFile') or '').strip()]
            json_files_preview = ', '.join(json_files[:12])
            items_preview_lines = []
            for item in valid_items[:20]:
                item_json = str(item.get('jsonFile') or '')
                item_preview = '\n'.join((item.get('lyrics') or [])[:3])
                item_preview = item_preview.strip().replace('\r', '')
                if item_preview:
                    items_preview_lines.append(f"{item_json or item.get('id')}: {item_preview[:160]}")

            batch_song_names = [str(item.get('song_name') or '').strip() for item in valid_items if str(item.get('song_name') or '').strip()]
            audit_base.update({
                'mode': 'batch',
                'item_count': len(valid_items),
                'json_files_preview': json_files_preview,
                'items_preview': '\n'.join(items_preview_lines),
                'content_preview': '\n'.join(items_preview_lines)[:600],
                'song_names_preview': ', '.join(batch_song_names[:8]),
                'items': [
                    {
                        'id': str(item.get('id') or ''),
                        'jsonFile': str(item.get('jsonFile') or ''),
                        'lyricsPath': str(item.get('lyricsPath') or ''),
                        'song_name': str(item.get('song_name') or ''),
                        **_summarize_content('\n'.join(item.get('lyrics', []) or []))
                    }
                    for item in valid_items[:50]
                ]
            })

            batch_full_line_count = sum(
                1
                for item in valid_items
                for line in item.get('prompt_lines', [])
                if line.get('is_full_line_bracket')
            )
            batch_subline_count = sum(
                1
                for item in valid_items
                for line in item.get('prompt_lines', [])
                if line.get('is_subline')
            )
            batch_tag_line_count = sum(
                1
                for item in valid_items
                for line in item.get('prompt_lines', [])
                if line.get('is_tag_subline_candidate')
            )
            batch_tag_subline_count = sum(
                1
                for item in valid_items
                for line in item.get('prompt_lines', [])
                if line.get('is_tag_subline_candidate') and line.get('is_subline')
            )
            app.logger.info(
                "批量预处理统计: 歌曲=%d, 整句括号行=%d, 归并从句=%d, 标签行=%d, 标签归并=%d",
                len(valid_items),
                batch_full_line_count,
                batch_subline_count,
                batch_tag_line_count,
                batch_tag_subline_count
            )

            def generate_batch():
                audit_payload = dict(audit_base)
                prompt_tokens = 0
                completion_tokens = 0
                total_tokens = 0
                try:
                    batch_blocks = []
                    has_any_sublines = False
                    for item in valid_items:
                        if item.get('hasSublines'):
                            has_any_sublines = True
                        numbered = '\n'.join(
                            f"{line['display_index']}.{line['normalized_text']}"
                            for line in item.get('prompt_lines', [])
                        )
                        batch_blocks.append(f"[ID:{item['id']}]\n{numbered}")
                    numbered_lyrics = '\n\n'.join(batch_blocks)
                    batch_system_prompt = build_batch_system_prompt(system_prompt)
                    user_prompt_parts = []
                    if has_any_sublines:
                        user_prompt_parts.append(build_subline_prompt_notice())
                    user_prompt_parts.append(f"待翻译歌词：\n{numbered_lyrics}")
                    user_prompt = '\n\n'.join(user_prompt_parts)

                    if compat_mode:
                        combined_prompt_parts = []
                        if batch_system_prompt:
                            combined_prompt_parts.append(batch_system_prompt.strip())
                        combined_prompt_parts.append(user_prompt)
                        messages = [{"role": "user", "content": '\n\n'.join(combined_prompt_parts)}]
                    else:
                        messages = [
                            {"role": "system", "content": batch_system_prompt},
                            {"role": "user", "content": user_prompt}
                        ]

                    if batch_thinking_enabled:
                        yield f"thinking:{json.dumps({'summary': '批量模式默认建议关闭思考；当前已按请求开启。'})}\n"

                    client = build_openai_client(api_key=api_key, base_url=base_url)
                    reasoning_opts = build_reasoning_request_options(provider=provider, base_url=base_url, model=model, expect_reasoning=bool(expect_reasoning))
                    response = client.chat.completions.create(
                        model=model,
                        messages=messages,
                        stream=True,
                        **reasoning_opts
                    )

                    grouped: Dict[str, Dict[str, str]] = {item['id']: {} for item in valid_items}
                    last_sent_counts = {item['id']: 0 for item in valid_items}
                    batch_fallback_lyrics: Dict[str, List[str]] = {}
                    current_id: Optional[str] = None
                    stream_buffer = ""
                    stream_content_full = ""
                    stream_reasoning = ""
                    stream_buffer_full = ""
                    last_flush_at = time.monotonic()
                    id_pattern = re.compile(r'^\s*\[ID:(.+?)\]\s*$')

                    def emit_updates(force: bool = False):
                        nonlocal last_flush_at
                        now = time.monotonic()
                        if not force and now - last_flush_at < 1.0:
                            return
                        emitted = False
                        for item in valid_items:
                            item_id = item['id']
                            if item_id in batch_fallback_lyrics:
                                final_lyrics = batch_fallback_lyrics[item_id]
                                if not final_lyrics:
                                    continue
                                if not force and last_sent_counts.get(item_id, 0) >= len(final_lyrics):
                                    continue
                                payload = {
                                    'id': item_id,
                                    'jsonFile': item.get('jsonFile', ''),
                                    'translations': final_lyrics,
                                    'hasTimestamps': item['hasTimestamps'],
                                }
                                yield f"content:{json.dumps(payload)}\n"
                                last_sent_counts[item_id] = len(final_lyrics)
                                emitted = True
                                continue
                            translated_dict = grouped.get(item_id, {})
                            if not translated_dict:
                                continue
                            line_count = len(translated_dict)
                            expected_count = len(item.get('prompt_lines', []))
                            is_complete = line_count >= expected_count
                            if line_count <= last_sent_counts[item_id]:
                                continue
                            if not force and not is_complete and now - last_flush_at < 1.0:
                                continue
                            line_prefixes = item['timestamps'] if item['hasTimestamps'] else [''] * len(item['lyrics'])
                            final_lyrics = []
                            for prompt_line in item.get('prompt_lines', []):
                                translation = translated_dict.get(prompt_line['display_index'])
                                if translation is None:
                                    continue
                                entry_idx = max(0, int(prompt_line.get('entry_index', 1)) - 1)
                                prefix = line_prefixes[entry_idx] if entry_idx < len(line_prefixes) else ''
                                final_lyrics.append(f"{prefix}{translation}" if prefix else translation)
                            if not final_lyrics:
                                continue
                            payload = {
                                'id': item_id,
                                'jsonFile': item.get('jsonFile', ''),
                                'translations': final_lyrics,
                                'hasTimestamps': item['hasTimestamps']
                            }
                            yield f"content:{json.dumps(payload)}\n"
                            last_sent_counts[item_id] = line_count
                            emitted = True
                        if emitted or force:
                            last_flush_at = now

                    for chunk in response:
                        if hasattr(chunk, 'usage') and chunk.usage:
                            prompt_tokens, completion_tokens, total_tokens = _ai_usage_merge_stream_usage(
                                chunk.usage, prompt_tokens, completion_tokens, total_tokens
                            )
                        choices = getattr(chunk, 'choices', None)
                        if not choices:
                            continue
                        delta = getattr(choices[0], 'delta', None)
                        if delta is None:
                            continue
                        if expect_reasoning and hasattr(delta, 'reasoning_content') and delta.reasoning_content:
                            reasoning = delta.reasoning_content
                            yield f"reasoning:{json.dumps({'reasoning': reasoning})}\n"
                        elif not expect_reasoning and hasattr(delta, 'reasoning_content') and delta.reasoning_content:
                            stream_reasoning += delta.reasoning_content
                            stream_buffer_full += delta.reasoning_content
                        if hasattr(delta, 'content') and delta.content:
                            stream_buffer_full += delta.content
                            stream_content_full += delta.content
                            stream_buffer += delta.content
                            lines, stream_buffer = iter_complete_lines(stream_buffer)
                            for raw_line in lines:
                                line = raw_line.strip()
                                if not line:
                                    continue
                                id_match = id_pattern.match(line)
                                if id_match:
                                    current_id = id_match.group(1).strip()
                                    grouped.setdefault(current_id, {})
                                    continue
                                if current_id is None:
                                    continue
                                parsed_line = parse_numbered_translation_line(line)
                                if not parsed_line:
                                    continue
                                display_index, translated_text = parsed_line
                                grouped.setdefault(current_id, {})[display_index] = translated_text
                            yield from emit_updates(False)

                    if stream_buffer.strip():
                        tail_lines, tail_buffer = iter_complete_lines(stream_buffer + '\n')
                        if tail_buffer:
                            tail_lines.append(tail_buffer)
                        for raw_line in tail_lines:
                            line = raw_line.strip()
                            if not line:
                                continue
                            id_match = id_pattern.match(line)
                            if id_match:
                                current_id = id_match.group(1).strip()
                                grouped.setdefault(current_id, {})
                                continue
                            if current_id is None:
                                continue
                            parsed_line = parse_numbered_translation_line(line)
                            if not parsed_line:
                                continue
                            display_index, translated_text = parsed_line
                            grouped.setdefault(current_id, {})[display_index] = translated_text

                    for item in valid_items:
                        item_id = item['id']
                        translated_dict = grouped.get(item_id, {})
                        expected_count = len(item.get('prompt_lines', []))
                        if expected_count and len(translated_dict) >= expected_count:
                            continue
                        section_text = extract_batch_item_stream_section(stream_content_full, item_id)
                        item_reasoning = extract_batch_item_stream_section(stream_reasoning, item_id)
                        if not item_reasoning and len(valid_items) == 1:
                            item_reasoning = stream_reasoning.strip()
                        line_prefixes = item['timestamps'] if item['hasTimestamps'] else [''] * len(item['lyrics'])
                        final_dict, final_lyrics = finalize_translation_dict_and_lyrics(
                            section_text,
                            item_reasoning,
                            '',
                            item.get('prompt_lines', []),
                            line_prefixes,
                        )
                        if final_dict:
                            grouped[item_id] = {**grouped.get(item_id, {}), **final_dict}
                        elif final_lyrics:
                            batch_fallback_lyrics[item_id] = final_lyrics

                    yield from emit_updates(True)

                    audit_payload.update({
                        'success': True,
                        'duration_ms': int((time.monotonic() - audit_started_at) * 1000),
                        **_ai_usage_audit_token_payload(prompt_tokens, completion_tokens, total_tokens),
                    })
                except Exception as e:
                    app.logger.error(f"批量翻译出错 [ID: {request_id}]: {str(e)}", exc_info=True)
                    yield f"content:{json.dumps({'status':'error','message':str(e)})}\n"
                    audit_payload.update({
                        'success': False,
                        'duration_ms': int((time.monotonic() - audit_started_at) * 1000),
                        'error': str(e),
                        **_ai_usage_audit_token_payload(prompt_tokens, completion_tokens, total_tokens),
                    })
                finally:
                    append_ai_usage_log(audit_payload)

            return StreamingResponse(generate_batch(), media_type='text/event-stream')

        if not content:
            return jsonify({'status': 'error', 'message': '未提供歌词内容'})

        audit_base = _build_ai_usage_audit_base()
        single_song_name = str(request_data.get('song_name') or '').strip()
        if not single_song_name:
            single_song_name = _ai_usage_resolve_song_name(str(request_data.get('jsonFile') or ''))
        audit_base.update({
            'mode': 'single',
            'item_count': 1,
            **_summarize_content(content),
            'jsonFile': str(request_data.get('jsonFile') or ''),
            'lyricsPath': str(request_data.get('lyricsPath') or ''),
            'translationPath': str(request_data.get('translationPath') or ''),
            'song_name': single_song_name,
            'song_names_preview': single_song_name,
        })

        timestamps = extract_timestamps_from_content(content)
        lyrics_entries = extract_lyrics_entries_from_content(content)
        lyrics = [entry['text'] for entry in lyrics_entries]

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
        prompt_lines = build_translation_prompt_lines(
            lyrics_entries,
            strip_brackets=strip_brackets,
            experimental_full_line_bracket_strip=experimental_full_line_bracket_strip,
            experimental_bracket_line_as_subline=experimental_bracket_line_as_subline
        )
        if not prompt_lines:
            app.logger.error("预处理后无可用歌词行")
            return jsonify({'status': 'error', 'message': '预处理后无可翻译歌词，请检查括号清理设置或歌词内容'})
        full_line_bracket_count = sum(1 for line in prompt_lines if line['is_full_line_bracket'])
        subline_count = sum(1 for line in prompt_lines if line['is_subline'])
        tag_line_count = sum(1 for line in prompt_lines if line.get('is_tag_subline_candidate'))
        tag_subline_count = sum(1 for line in prompt_lines if line.get('is_tag_subline_candidate') and line.get('is_subline'))
        app.logger.info(f"去括号预处理: {'开启' if strip_brackets else '关闭'}")
        app.logger.info(f"实验性整句外层括号清理: {'开启' if experimental_full_line_bracket_strip else '关闭'}")
        app.logger.info(f"实验性整句括号并入上一句从句: {'开启' if experimental_bracket_line_as_subline else '关闭'}")
        app.logger.info(f"识别为整句括号行数量: {full_line_bracket_count}")
        app.logger.info(f"归并为从句数量: {subline_count}")
        app.logger.info(f"识别为标签行数量: {tag_line_count}")
        app.logger.info(f"标签归并为从句数量: {tag_subline_count}")
        app.logger.info(f"提取的歌词行数: {len(lyrics)}")
        processed_preview = '\n'.join(f"{line['display_index']}. {line['normalized_text']}" for line in prompt_lines)
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
        for line in prompt_lines:
            line_text = line['normalized_text']
            source_line_no = int(line.get('source_line_no', 0) or 0)
            display_line_no = source_line_no if source_line_no > 0 else '?'
            for char in illegal_chars:
                if char in line_text:
                    app.logger.error(f"第{display_line_no}行歌词包含非法字符: {char}")
                    return jsonify({'status': 'error', 'message': f'歌词内容包含非法字符，请检查第{display_line_no}行'})

        # 2. 调用AI服务进行翻译
        def generate():
            audit_payload = dict(audit_base)
            prompt_tokens = 0
            completion_tokens = 0
            total_tokens = 0
            translation_parse_failed = False
            try:
                # 构建提示词
                numbered_lyrics = '\n'.join(
                    f"{line['display_index']}.{line['normalized_text']}"
                    for line in prompt_lines
                )
                has_sublines = any(line['is_subline'] for line in prompt_lines)
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
                            if hasattr(chunk, 'usage') and chunk.usage:
                                prompt_tokens, completion_tokens, total_tokens = _ai_usage_merge_stream_usage(
                                    chunk.usage, prompt_tokens, completion_tokens, total_tokens
                                )
                                thinking_tokens = getattr(chunk.usage, 'total_tokens', 0) or thinking_tokens
                            choices = getattr(chunk, 'choices', None)
                            if not choices:
                                continue
                            delta = getattr(choices[0], 'delta', None)
                            if delta is None:
                                continue
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
                    if has_sublines:
                        combined_prompt_parts.append(build_subline_prompt_notice())
                    combined_prompt_parts.append(f"待翻译歌词：\n{numbered_lyrics}")
                    combined_prompt_parts.append(TRANSLATION_OUTPUT_FORMAT_CONTRACT)
                    combined_prompt = '\n\n'.join(part for part in combined_prompt_parts if part)
                    messages = [
                        {"role": "user", "content": combined_prompt}
                    ]
                    app.logger.debug("兼容模式启用：系统提示词已合并到用户消息")
                else:
                    user_content_parts = []
                    if thinking_summary:
                        user_content_parts.append(f"歌曲理解：\n{thinking_summary}")
                    if has_sublines:
                        user_content_parts.append(build_subline_prompt_notice())
                    user_content_parts.append(f"待翻译歌词：\n{numbered_lyrics}")
                    user_content_parts.append(TRANSLATION_OUTPUT_FORMAT_CONTRACT)
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
                    reasoning_opts = build_reasoning_request_options(provider=provider, base_url=base_url, model=model, expect_reasoning=bool(expect_reasoning))
                    response = client.chat.completions.create(
                        model=model,
                        messages=messages,
                        stream=True,
                        **reasoning_opts
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
                stream_reasoning = ""
                current_reasoning = ""
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
                    
                    if hasattr(chunk, 'usage') and chunk.usage:
                        prompt_tokens, completion_tokens, total_tokens = _ai_usage_merge_stream_usage(
                            chunk.usage, prompt_tokens, completion_tokens, total_tokens
                        )

                    # 检查是否有思维链内容
                    if expect_reasoning and hasattr(delta, 'reasoning_content') and delta.reasoning_content:
                        content = delta.reasoning_content
                        current_reasoning += content
                        app.logger.debug(f"收到思维链内容 [ID: {request_id}]: {content}")
                        yield f"reasoning:{json.dumps({'reasoning': current_reasoning})}\n"
                    elif not expect_reasoning and hasattr(delta, 'reasoning_content') and delta.reasoning_content:
                        stream_reasoning += delta.reasoning_content

                    # 检查是否有普通内容
                    if hasattr(delta, 'content') and delta.content:
                        content = delta.content
                        full_translation += content
                        app.logger.debug(f"收到翻译内容 [ID: {request_id}]: {content}")

                        parse_text = resolve_translation_source_text(
                            full_translation,
                            stream_reasoning or current_reasoning,
                            thinking_summary,
                        )
                        translated_dict = build_translated_dict_from_text(parse_text)
                        if translated_dict:
                            final_lyrics = merge_translated_dict_into_final_lyrics(
                                translated_dict, prompt_lines, line_prefixes
                            )
                            if final_lyrics:
                                payload = {
                                    'translations': final_lyrics,
                                    'hasTimestamps': has_timestamps
                                }
                                yield f"content:{json.dumps(payload)}\n"
                                app.logger.debug(f"成功合并 {len(final_lyrics)} 行翻译歌词")

                reasoning_for_parse = current_reasoning if expect_reasoning else stream_reasoning
                final_dict, final_lyrics = finalize_translation_dict_and_lyrics(
                    full_translation,
                    reasoning_for_parse,
                    thinking_summary,
                    prompt_lines,
                    line_prefixes,
                )
                if not final_lyrics:
                    translation_parse_failed = True
                    parse_text = resolve_translation_source_text(
                        full_translation,
                        reasoning_for_parse,
                        thinking_summary,
                    )
                    preview = parse_text[:300]
                    error_payload = {
                        'status': 'error',
                        'code': 'no_numbered_translations',
                        'message': (
                            '未能从模型输出中识别任何编号翻译行。'
                            '请确保翻译段使用 N. / N、 / N: / [N] 等可解析的编号格式。'
                        ),
                        'preview': preview,
                        'expected_line_count': len(prompt_lines),
                    }
                    app.logger.error(
                        "翻译流终态无编号译文 [ID: %s], preview=%r, expected_line_count=%s",
                        request_id,
                        preview,
                        len(prompt_lines),
                    )
                    yield f"content:{json.dumps(error_payload)}\n"
                else:
                    payload = {
                        'translations': final_lyrics,
                        'hasTimestamps': has_timestamps
                    }
                    yield f"content:{json.dumps(payload)}\n"
                    app.logger.debug(f"终态合并 {len(final_lyrics)} 行翻译歌词")

                # 记录流式响应完成
                stream_duration = time.time() - stream_start_time
                app.logger.info(f"流式响应完成 [ID: {request_id}], 耗时: {stream_duration:.2f}秒")
                app.logger.info(f"总共接收 {received_chunks} 个数据块, 估计Token使用: {total_tokens}")

            except Exception as e:
                error_time = time.time()
                error_duration = error_time - api_start_time
                app.logger.error(f"AI翻译过程中出错 [ID: {request_id}]: {str(e)}, 总耗时: {error_duration:.2f}秒", exc_info=True)
                yield f"content:翻译过程中出错: {str(e)}\n"
                audit_payload.update({
                    'success': False,
                    'duration_ms': int((time.monotonic() - audit_started_at) * 1000),
                    'error': str(e),
                    **_ai_usage_audit_token_payload(prompt_tokens, completion_tokens, total_tokens),
                })
            else:
                total_duration = time.time() - api_start_time
                if translation_parse_failed:
                    app.logger.error(
                        f"翻译完成但未识别编号译文 [ID: {request_id}], 总耗时: {total_duration:.2f}秒"
                    )
                    audit_payload.update({
                        'success': False,
                        'duration_ms': int((time.monotonic() - audit_started_at) * 1000),
                        'error': 'no_numbered_translations',
                        'reasoning_length': len(current_reasoning),
                        'translation_length': len(full_translation),
                        **_ai_usage_audit_token_payload(prompt_tokens, completion_tokens, total_tokens),
                    })
                else:
                    app.logger.info(f"翻译成功完成 [ID: {request_id}], 总耗时: {total_duration:.2f}秒")
                    app.logger.info(f"最终翻译字符数: {len(full_translation)}, 思维链长度: {len(current_reasoning)}")
                    app.logger.info(f"API配置: {provider}, {base_url}, {model}, expect_reasoning: {expect_reasoning}, compat_mode: {compat_mode}")
                    audit_payload.update({
                        'success': True,
                        'duration_ms': int((time.monotonic() - audit_started_at) * 1000),
                        'reasoning_length': len(current_reasoning),
                        'translation_length': len(full_translation),
                        **_ai_usage_audit_token_payload(prompt_tokens, completion_tokens, total_tokens),
                    })
            finally:
                append_ai_usage_log(audit_payload)

        return StreamingResponse(generate(), media_type='text/event-stream')

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


_ROMAN_LINE_NUM_RE = re.compile(r'^(\d+)\.(.*)$')
_ROMAN_LRC_HEAD_RE = re.compile(r'^\s*\[(\d{1,2}):(\d{2})(?:\.(\d{1,3}))?\]\s*(.*)$')


def _split_roman_body_tokens(body: str, sep: str, require_trail: bool) -> Tuple[Optional[List[str]], Optional[str]]:
    if sep == '':
        return [body], None
    if require_trail and not body.endswith(sep):
        return None, '缺少行尾分隔符'
    parts = body.split(sep)
    if parts and parts[-1] == '':
        parts = parts[:-1]
    return parts, None


_ROMAN_INDEXED_BRACKET_RE = re.compile(r'\[(\d+)\]')


def _parse_indexed_roman_body(body: str, line_n: int) -> Tuple[Optional[Dict[int, str]], List[str]]:
    """Parse N.[1]tok[2]tok... body into {k: roman_text}. Returns errors on duplicate or malformed."""
    errs: List[str] = []
    matches = list(_ROMAN_INDEXED_BRACKET_RE.finditer(body))
    if not matches:
        return None, [f'第{line_n}行缺少 token 标记 [k]']
    first = matches[0].start()
    if first > 0 and body[:first].strip():
        return None, [f'第{line_n}行在首个 [k] 前存在多余内容']
    by_k: Dict[int, str] = {}
    for i, m in enumerate(matches):
        k = int(m.group(1))
        if k in by_k:
            return None, [f'第{line_n}行重复 token [{k}]']
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        by_k[k] = body[m.end():end].strip()
    return by_k, errs


def _parse_roman_model_output(
    raw: str, alignment_mode: str, sep: str, require_trail: bool
) -> Tuple[Dict[int, Any], List[str]]:
    by_num: Dict[int, Any] = {}
    errors: List[str] = []
    mode = _normalize_romanization_alignment_mode(alignment_mode)
    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        m = _ROMAN_LINE_NUM_RE.match(line)
        if not m:
            errors.append(f'无法解析编号行: {line[:160]}')
            continue
        n = int(m.group(1))
        body = m.group(2)
        if n in by_num:
            errors.append(f'重复行号: {n}')
            continue
        if mode == 'indexed_tokens':
            idx_map, idx_errs = _parse_indexed_roman_body(body, n)
            if idx_errs:
                errors.extend(idx_errs)
                continue
            if idx_map is None:
                continue
            by_num[n] = idx_map
        else:
            parts, err = _split_roman_body_tokens(body, sep, require_trail)
            if err:
                errors.append(f'第{n}行 {err}')
                continue
            if parts is None:
                continue
            by_num[n] = parts
    return by_num, errors


def _roman_token_texts_with_spaces(parts: Union[List[str], Tuple[str, ...]]) -> List[str]:
    """
    Strip each roman fragment so LYS serializes with readable gaps:
    - trailing space before '(' on every token except the last (e.g. 'a ' -> 'a (ts)');
    - leading space after the previous ')' starting from the second token (e.g. ' bb' -> ') bb(').
    """
    seq = [str(p).strip() for p in parts]
    n = len(seq)
    out: List[str] = []
    for j, s in enumerate(seq):
        piece = s
        if j > 0:
            piece = f' {piece}' if piece else ' '
        if j < n - 1:
            if piece.strip():
                piece = piece.rstrip() + ' '
            elif not piece:
                piece = ' '
        out.append(piece)
    return out


def _lyric_line_tokens_ts_match(a_line: Dict[str, Any], b_line: Dict[str, Any]) -> bool:
    ta = a_line.get('tokens') or []
    tb = b_line.get('tokens') or []
    if len(ta) != len(tb):
        return False
    for x, y in zip(ta, tb):
        if (x.get('ts') or '') != (y.get('ts') or ''):
            return False
    return True


def _build_lys_roman_background_line(
    base_line: Dict[str, Any], bg_prefix: str, roman_parts: List[str]
) -> Dict[str, Any]:
    """Deep-copy base_line timings, set prefix to bg_prefix, assign spaced roman to token texts."""
    dup = copy.deepcopy(base_line)
    dup['id'] = qe_new_id()
    dup['prefix'] = bg_prefix
    spaced = _roman_token_texts_with_spaces(roman_parts)
    toks = dup.get('tokens') or []
    for j, tok in enumerate(toks):
        tok['id'] = qe_new_id()
        tok['text'] = spaced[j] if j < len(spaced) else str(tok.get('text', ''))
    return dup


def _build_lys_roman_targets(
    doc: Dict[str, Any], alignment_mode: str, sep: str, require_trail: bool
) -> Tuple[List[Dict[str, Any]], List[str]]:
    targets: List[Dict[str, Any]] = []
    numbered: List[str] = []
    mode = _normalize_romanization_alignment_mode(alignment_mode)
    for li, line in enumerate(doc.get('lines', [])):
        if line.get('is_meta'):
            continue
        if _lys_prefix_is_background_style(str(line.get('prefix') or '')):
            continue
        tokens = line.get('tokens') or []
        if not tokens:
            continue
        texts = [str(t.get('text', '')) for t in tokens]
        n = len(targets) + 1
        exp = len(tokens)
        token_indexes = list(range(1, exp + 1))
        if mode == 'indexed_tokens':
            body = ''.join(f'[{j + 1}]{texts[j]}' for j in range(exp))
            targets.append({
                'n': n,
                'doc_line_index': li,
                'expected': exp,
                'alignment_mode': mode,
                'token_indexes': token_indexes,
            })
            numbered.append(f'{n}.{body}')
        else:
            body = sep.join(texts)
            if require_trail:
                body += sep
            targets.append({
                'n': n,
                'doc_line_index': li,
                'expected': exp,
                'alignment_mode': mode,
                'token_indexes': token_indexes,
            })
            numbered.append(f'{n}.{body}')
    return targets, numbered


def _build_lrc_roman_targets(
    lrc_text: str, alignment_mode: str, sep: str, require_trail: bool
) -> Tuple[List[Dict[str, Any]], List[str]]:
    targets: List[Dict[str, Any]] = []
    numbered: List[str] = []
    mode = _normalize_romanization_alignment_mode(alignment_mode)
    for raw in lrc_text.splitlines():
        s = raw.strip()
        if not s:
            continue
        m = _ROMAN_LRC_HEAD_RE.match(s)
        if not m:
            continue
        mins, secs, ms3, rest = m.groups()
        start_ms = (int(mins) * 60 + int(secs)) * 1000 + int((ms3 or '0').ljust(3, '0')[:3])
        text = (rest or '').strip()
        if not text:
            continue
        n = len(targets) + 1
        if mode == 'indexed_tokens':
            body = f'[1]{text}'
            targets.append({
                'n': n,
                'start_ms': start_ms,
                'expected': 1,
                'alignment_mode': mode,
                'token_indexes': [1],
            })
            numbered.append(f'{n}.{body}')
        else:
            body = text + sep if require_trail else text
            targets.append({
                'n': n,
                'start_ms': start_ms,
                'expected': 1,
                'alignment_mode': mode,
                'token_indexes': [1],
            })
            numbered.append(f'{n}.{body}')
    return targets, numbered


def _validate_roman_alignment(
    targets: List[Dict[str, Any]],
    ai_by_line: Dict[int, Any],
    strict: bool,
) -> List[str]:
    errors: List[str] = []
    expected_ns = {t['n'] for t in targets}
    for n in sorted(ai_by_line.keys()):
        if n not in expected_ns:
            errors.append(f'多余行号: {n}')
    for t in targets:
        n = t['n']
        mode = _normalize_romanization_alignment_mode(t.get('alignment_mode'))
        val = ai_by_line.get(n)
        if val is None:
            errors.append(f'缺少行号: {n}')
            continue
        exp = int(t.get('expected') or 0)
        if mode == 'indexed_tokens':
            if isinstance(val, dict):
                d: Dict[int, str] = val
                need = set(range(1, exp + 1))
                keys = set(d.keys())
                for k in sorted(keys - need):
                    if k < 1:
                        errors.append(f'第{n}行出现非法 token 编号 [{k}]')
                    else:
                        errors.append(f'第{n}行出现越界 token [{k}]，期望范围 1..{exp}')
                for k in sorted(need - keys):
                    errors.append(f'第{n}行缺少 token [{k}]')
                    break
                if keys != need:
                    continue
                ai_by_line[n] = [d[i] for i in range(1, exp + 1)]
            elif isinstance(val, list):
                if strict and len(val) != exp:
                    errors.append(f'行{n} token 数不一致（期望 {exp}，实际 {len(val)}）')
            else:
                errors.append(f'第{n}行解析结果类型无效')
        else:
            parts = val if isinstance(val, list) else None
            if parts is None:
                errors.append(f'第{n}行解析结果类型无效')
                continue
            if strict and len(parts) != exp:
                errors.append(f'行{n} token 数不一致（期望 {exp}，实际 {len(parts)}）')
    return errors


def _apply_roman_tokens_to_lys_doc(doc: Dict[str, Any], targets: List[Dict[str, Any]], ai_by_line: Dict[int, Any]) -> str:
    """
    Keep each source lyric line unchanged; write roman into a new [6] line directly below
    (or update an existing companion [6]/[7]/[8] line with matching timestamps).
    """
    lines: List[Dict[str, Any]] = doc.setdefault('lines', [])
    bg_prefix = '[6]'
    for t in sorted(targets, key=lambda x: int(x['doc_line_index']), reverse=True):
        li = int(t['doc_line_index'])
        if li < 0 or li >= len(lines):
            continue
        line = lines[li]
        if line.get('is_meta'):
            continue
        if _lys_prefix_is_background_style(str(line.get('prefix') or '')):
            continue
        toks = line.get('tokens') or []
        if not toks:
            continue
        parts = ai_by_line.get(t['n'])
        if not isinstance(parts, list) or len(parts) != len(toks):
            continue
        spaced = _roman_token_texts_with_spaces(parts)
        nxt_i = li + 1
        if nxt_i < len(lines):
            nxt = lines[nxt_i]
            if _lys_prefix_is_background_style(str(nxt.get('prefix') or '')):
                if _lyric_line_tokens_ts_match(line, nxt):
                    nxtoks = nxt.get('tokens') or []
                    for j, tok in enumerate(nxtoks):
                        tok['text'] = spaced[j] if j < len(spaced) else str(tok.get('text', ''))
                    continue
                lines[nxt_i] = _build_lys_roman_background_line(line, bg_prefix, parts)
                continue
        lines.insert(li + 1, _build_lys_roman_background_line(line, bg_prefix, parts))
    return qe_dump_lys(doc)


def _parse_lys_rows_for_roman_lrc(parsed: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Keep only lyric rows that _build_lys_roman_targets would number (non-[6]/[7]/[8]).
    parse_lys marker is the digits inside brackets ('6','7','8' for background tags).
    """
    return [pl for pl in parsed if str(pl.get('marker', '')) not in ('6', '7', '8')]


def _format_roman_lrc_lines(start_ms_list: List[int], texts: List[str]) -> str:
    """LRC-style timestamps with centisecond fraction, e.g. [00:32.65]."""
    rows: List[str] = []
    for start_ms, text in zip(start_ms_list, texts):
        ms = max(0, int(start_ms))
        minutes, rem_ms = divmod(ms, 60000)
        whole_sec, frac_ms = divmod(rem_ms, 1000)
        centis = (frac_ms + 5) // 10
        if centis >= 100:
            whole_sec += 1
            centis -= 100
        if whole_sec >= 60:
            minutes += whole_sec // 60
            whole_sec %= 60
        rows.append(f"[{minutes:02d}:{whole_sec:02d}.{centis:02d}]{text}")
    return '\n'.join(rows)


def _roman_lys_to_lrc(lys_text: str, targets: List[Dict[str, Any]], ai_by_line: Dict[int, Any], strict: bool) -> Tuple[Optional[str], List[str]]:
    parsed = parse_lys(lys_text)
    main_rows = _parse_lys_rows_for_roman_lrc(parsed)
    err = _validate_roman_alignment(targets, ai_by_line, strict)
    if err:
        return None, err
    if len(main_rows) != len(targets):
        return None, [
            f'时间轴对齐失败：非背景歌词行数 {len(main_rows)} 与罗马音行数 {len(targets)} 不一致'
            f'（parse_lys 总行数 {len(parsed)}，已排除 [6]/[7]/[8] 标记行）'
        ]
    starts: List[int] = []
    texts: List[str] = []
    for i, t in enumerate(targets):
        parts = ai_by_line[t['n']]
        pl = main_rows[i]
        syl = pl.get('syllables') or []
        if not syl:
            return None, [f'第{t["n"]}行无音节时间戳']
        starts.append(int(float(syl[0]['startTime']) * 1000))
        texts.append(' '.join(parts))
    return _format_roman_lrc_lines(starts, texts), []


_ROMAN_NUMBERED_LINE_HEAD = re.compile(r'(?m)^\s*\d+\.')


def _count_numbered_roman_lines(text: str) -> int:
    return len(_ROMAN_NUMBERED_LINE_HEAD.findall(text or ''))


def prepare_romanization_job(
    request_data: Dict[str, Any],
    runtime: Dict[str, Any],
) -> Tuple[Optional[Dict[str, Any]], str, List[str]]:
    """
    Parse source, build targets/numbered input, assemble OpenAI-style messages. No API call.

    Returns (job, "", []) on success. On failure returns (None, user_message, errors_list).
    """
    roman_cfg = coalesce_romanization_settings((runtime.get('romanization') or {}))
    alignment_mode = str(roman_cfg.get('alignment_mode') or ROMANIZATION_DEFAULTS['alignment_mode'])
    sep = str(roman_cfg.get('separator') or ';')
    strict = bool(roman_cfg.get('strict_token_count', True))
    require_trail = bool(roman_cfg.get('require_trailing_separator', True))
    system_prompt = str(roman_cfg.get('system_prompt') or '').strip() or _default_romanization_system_prompt_for_mode(
        alignment_mode
    )
    compat_mode = parse_bool(runtime.get('translation', {}).get('compat_mode'), AI_TRANSLATION_SETTINGS['compat_mode'])

    source_content = str(request_data.get('source_content') or '')
    source_format = str(request_data.get('source_format') or 'lys').strip().lower()
    target_format = str(request_data.get('target_format') or 'lys').strip().lower()
    if target_format not in {'lys', 'lrc'}:
        return None, 'target_format 必须是 lys 或 lrc', []
    if source_format not in {'lys', 'lrc', 'ttml'}:
        return None, 'source_format 必须是 lys、lrc 或 ttml', []
    if source_format == 'lrc' and target_format == 'lys':
        return None, 'LRC 源不能输出 LYS（无逐字时间轴可供回填）', []

    lys_working = source_content
    if source_format == 'ttml':
        ok_ttml, lys_text, err_ttml = ttml_text_to_lys_content(source_content)
        if not ok_ttml or not lys_text:
            return None, err_ttml or 'TTML 转 LYS 失败', []
        lys_working = lys_text
        source_effective = 'lys'
    else:
        source_effective = source_format

    targets: List[Dict[str, Any]] = []
    numbered: List[str] = []
    if source_effective == 'lys':
        doc = qe_parse_lys(lys_working)
        targets, numbered = _build_lys_roman_targets(doc, alignment_mode, sep, require_trail)
    else:
        targets, numbered = _build_lrc_roman_targets(source_content, alignment_mode, sep, require_trail)

    if not numbered:
        return None, '没有可罗马音化的歌词行', []

    numbered_preview = '\n'.join(numbered)
    if _normalize_romanization_alignment_mode(alignment_mode) == 'indexed_tokens':
        user_block = (
            '对齐协议：逐 token 编号。每行在「行号 N.」之后为若干「[k]片段」，k 从 1 递增，与输入完全一致。\n'
            f'输入共 {len(numbered)} 行，请保持行号 1..{len(numbered)} 一一对应。\n\n'
            f'{numbered_preview}'
        )
        contract = (
            '\n\n硬性要求：只输出编号行，不要其它说明；行号必须与输入一致；'
            '每行必须保留与输入相同的全部 [k] 编号且不得跳号、不得重复；'
            '只将每个 [k] 后的原文替换为罗马音，不要输出额外说明。'
        )
    else:
        user_block = (
            f'分节分隔符为 {sep!r}。\n'
            f'输入共 {len(numbered)} 行，请保持行号 1..{len(numbered)} 一一对应。\n\n'
            f'{numbered_preview}'
        )
        contract = (
            '\n\n硬性要求：只输出编号行，不要其它说明；行号必须与输入一致；'
            '每行分段数量必须与输入一致；只输出罗马音。'
        )
    if compat_mode:
        messages: List[Dict[str, str]] = [{'role': 'user', 'content': f"{system_prompt}{contract}\n\n{user_block}"}]
    else:
        messages = [
            {'role': 'system', 'content': system_prompt + contract},
            {'role': 'user', 'content': user_block},
        ]

    job: Dict[str, Any] = {
        'source_content': source_content,
        'lys_working': lys_working,
        'source_format': source_format,
        'source_effective': source_effective,
        'target_format': target_format,
        'targets': targets,
        'numbered': numbered,
        'numbered_preview': numbered_preview,
        'alignment_mode': alignment_mode,
        'sep': sep,
        'strict': strict,
        'require_trail': require_trail,
        'system_prompt': system_prompt,
        'contract': contract,
        'user_block': user_block,
        'compat_mode': compat_mode,
        'messages': messages,
    }
    return job, '', []


def _serialize_roman_merged_to_raw(
    ai_by_line: Dict[int, Any],
    targets: List[Dict[str, Any]],
    alignment_mode: str,
    sep: str,
    require_trail: bool,
) -> Tuple[str, List[str]]:
    """Rebuild numbered romanization text from merged parse dict. Returns (text, errors)."""
    errors: List[str] = []
    mode = _normalize_romanization_alignment_mode(alignment_mode)
    lines_out: List[str] = []
    for t in sorted(targets, key=lambda x: int(x['n'])):
        n = int(t['n'])
        val = ai_by_line.get(n)
        if val is None:
            errors.append(f'合并后缺少行号: {n}')
            continue
        exp = int(t.get('expected') or 0)
        if mode == 'indexed_tokens':
            if isinstance(val, dict):
                d: Dict[int, str] = val
                need = set(range(1, exp + 1))
                if set(d.keys()) != need:
                    errors.append(f'第{n}行 token 结构不完整，无法序列化')
                    continue
                parts = [d[i] for i in range(1, exp + 1)]
            elif isinstance(val, list):
                if len(val) != exp:
                    errors.append(f'第{n}行 token 数 {len(val)} 与期望 {exp} 不一致，无法序列化')
                    continue
                parts = [str(x) for x in val]
            else:
                errors.append(f'第{n}行合并值类型无效')
                continue
            body = ''.join(f'[{j + 1}]{parts[j]}' for j in range(exp))
        else:
            if not isinstance(val, list):
                errors.append(f'第{n}行合并值类型无效')
                continue
            if len(val) != exp:
                errors.append(f'第{n}行 token 数 {len(val)} 与期望 {exp} 不一致，无法序列化')
                continue
            body = sep.join(str(p) for p in val)
            if require_trail:
                body += sep
        lines_out.append(f'{n}.{body}')
    if errors:
        return '', errors
    return '\n'.join(lines_out), []


def merge_roman_patch_into_base(
    job: Dict[str, Any], base_raw: str, patch_raw: str
) -> Tuple[Optional[str], List[str]]:
    """
    Overlay patch lines onto a full baseline model output, then serialize to a complete raw block.
    Returns (merged_raw_stripped, errors). merged_raw is None when merge cannot proceed.
    """
    targets = job['targets']
    alignment_mode = str(job.get('alignment_mode') or ROMANIZATION_DEFAULTS['alignment_mode'])
    sep = str(job.get('sep') or ';')
    require_trail = bool(job.get('require_trail', True))
    expected_ns = {int(t['n']) for t in targets}

    base_raw_s = str(base_raw or '').strip()
    patch_raw_s = str(patch_raw or '').strip()
    if not patch_raw_s:
        return None, ['修复输出为空']

    ai_base, err_base = _parse_roman_model_output(base_raw_s, alignment_mode, sep, require_trail)
    if err_base:
        return None, list(err_base)
    if set(ai_base.keys()) != expected_ns:
        return None, ['罗马音基底不完整（缺少行号或含无法解析行），请使用「重新生成」']

    ai_patch, err_patch = _parse_roman_model_output(patch_raw_s, alignment_mode, sep, require_trail)
    if err_patch:
        return None, list(err_patch)

    for pn in ai_patch.keys():
        if int(pn) not in expected_ns:
            return None, [f'修复输出含非法行号: {pn}']

    merged = {**ai_base, **ai_patch}
    merged_raw, ser_errs = _serialize_roman_merged_to_raw(merged, targets, alignment_mode, sep, require_trail)
    if ser_errs:
        return None, ser_errs
    return merged_raw.strip(), []


def romanization_request_has_repair_fields(request_data: Dict[str, Any]) -> bool:
    if str(request_data.get('repair_instruction') or '').strip():
        return True
    if str(request_data.get('previous_full_model_output') or '').strip():
        return True
    hist = request_data.get('conversation_history')
    return isinstance(hist, list) and len(hist) > 0


def _validate_roman_repair_history_turns(hist: List[Any]) -> Tuple[bool, str]:
    """Require user/assistant alternation, start with user, end with assistant (last model output)."""
    if not isinstance(hist, list) or len(hist) < 2:
        return False, 'conversation_history 至少需包含 user 与 assistant 各一条（须含首轮 user 与首轮模型输出）'
    prev: Optional[str] = None
    for i, msg in enumerate(hist):
        if not isinstance(msg, dict):
            return False, f'conversation_history[{i}] 必须是对象'
        role = str(msg.get('role') or '').strip().lower()
        if role not in ('user', 'assistant'):
            return False, f'conversation_history[{i}].role 必须是 user 或 assistant'
        if str(msg.get('content') or '').strip() == '':
            return False, f'conversation_history[{i}].content 不能为空'
        if prev is None:
            if role != 'user':
                return False, 'conversation_history 须以 user（首轮发给模型的输入）起始'
        else:
            if role == prev:
                return False, f'conversation_history[{i}] 须与相邻消息 user/assistant 交替'
        prev = role
    if prev != 'assistant':
        return False, 'conversation_history 须以 assistant（上一轮模型输出）结尾'
    return True, ''


def _format_roman_messages_for_external_clipboard(messages: List[Dict[str, str]]) -> str:
    """Human-readable multi-turn block for external chat UIs."""
    title_map = {'system': 'System', 'user': 'User', 'assistant': 'Assistant'}
    blocks: List[str] = []
    for m in messages:
        role = str(m.get('role') or '')
        label = title_map.get(role, role or 'unknown')
        blocks.append(f'[{label}]\n{str(m.get("content") or "")}')
    return '\n\n'.join(blocks).strip()


def apply_romanization_repair_to_job(job: Dict[str, Any], request_data: Dict[str, Any]) -> Tuple[bool, str, List[str]]:
    """
    Rebuild job['messages'] for multi-turn repair: [system] + conversation_history + [repair user].
    First-turn user must appear inside conversation_history (not duplicated after system).
    compat_mode jobs cannot use repair fields.
    Sets job['_roman_repair_mode'] and job['_roman_repair_base_raw'] on success.
    """
    if not romanization_request_has_repair_fields(request_data):
        return True, '', []
    if bool(job.get('compat_mode')):
        return False, '多轮修复需要关闭兼容模式（请使用分条的 system / user 消息预设）', []

    repair_instruction = str(request_data.get('repair_instruction') or '').strip()
    prev_full = str(request_data.get('previous_full_model_output') or '').strip()
    hist = request_data.get('conversation_history')

    if not repair_instruction:
        return False, '缺少 repair_instruction', []
    if not prev_full:
        return False, '缺少 previous_full_model_output', []
    if not isinstance(hist, list):
        return False, 'conversation_history 必须是数组', []

    ok_hist, herr = _validate_roman_repair_history_turns(hist)
    if not ok_hist:
        return False, herr, []

    base_messages = list(job['messages'])
    if not base_messages:
        return False, 'job 缺少基础消息', []
    role0 = str(base_messages[0].get('role') or '').strip().lower()
    if role0 != 'system':
        return False, '修复模式要求首条消息为 system（请使用非兼容预设）', []
    system_msg: Dict[str, str] = {
        'role': 'system',
        'content': str(base_messages[0].get('content') or ''),
    }
    extra: List[Dict[str, str]] = []
    for m in hist:
        role = str(m.get('role') or '').strip().lower()
        extra.append({'role': role, 'content': str(m.get('content') or '')})
    extra.append({'role': 'user', 'content': repair_instruction})
    job['messages'] = [system_msg] + extra
    job['_roman_repair_mode'] = True
    job['_roman_repair_base_raw'] = prev_full
    return True, '', []


def assemble_romanization_from_raw(job: Dict[str, Any], raw_model_output: str) -> Tuple[str, List[str], str]:
    """Parse model output, validate alignment, build LYS/LRC. Returns (result_text, errors, raw_out_stripped)."""
    targets = job['targets']
    alignment_mode = str(job.get('alignment_mode') or ROMANIZATION_DEFAULTS['alignment_mode'])
    sep = str(job.get('sep') or ';')
    require_trail = bool(job.get('require_trail', True))
    strict = bool(job.get('strict', True))
    lys_working = str(job.get('lys_working') or '')
    source_effective = str(job.get('source_effective') or 'lys')
    target_format = str(job.get('target_format') or 'lys')

    raw_out = str(raw_model_output or '').strip()
    ai_by_line, parse_errors = _parse_roman_model_output(raw_out, alignment_mode, sep, require_trail)
    all_errors = list(parse_errors)
    all_errors.extend(_validate_roman_alignment(targets, ai_by_line, strict))

    result_text = ''
    if not all_errors:
        if target_format == 'lys' and source_effective == 'lys':
            doc2 = qe_parse_lys(lys_working)
            result_text = _apply_roman_tokens_to_lys_doc(doc2, targets, ai_by_line)
        elif target_format == 'lrc' and source_effective == 'lys':
            lrc_out, lrc_err = _roman_lys_to_lrc(lys_working, targets, ai_by_line, strict)
            if lrc_err:
                all_errors.extend(lrc_err)
            else:
                result_text = lrc_out or ''
        elif target_format == 'lrc' and source_effective == 'lrc':
            err2 = _validate_roman_alignment(targets, ai_by_line, strict)
            if err2:
                all_errors.extend(err2)
            else:
                starts = [int(t['start_ms']) for t in targets]
                texts = [' '.join(ai_by_line[t['n']]) for t in targets]
                result_text = _format_roman_lrc_lines(starts, texts)

    return result_text, all_errors, raw_out


@app.route('/romanize_lyrics', methods=['POST'])
def romanize_lyrics():
    request_data = request.get_json(silent=True) or {}
    task_payload = extract_ai_task_payload(request_data)
    if not can_use_ai():
        return jsonify({'status': 'error', 'message': '当前设备没有使用 AI 的权限'}), 403
    auth_context = get_current_device_auth_context()
    runtime = resolve_runtime_ai_config(request_data)
    api_key = str((runtime.get('translation') or {}).get('api_key') or '').strip()
    if not api_key:
        source_mode = str(runtime.get('source_mode') or 'manual')
        preset_label = str(runtime.get('name') or runtime.get('id') or '').strip()
        if source_mode == 'preset':
            preset_label = preset_label or '（未知预设）'
            return jsonify({'status': 'error', 'message': f'当前预设 {preset_label} 未配置后端 API 密钥'})
        return jsonify({'status': 'error', 'message': '当前 AI 设置未保存 API 密钥'})

    job, prep_err, prep_details = prepare_romanization_job(task_payload, runtime)
    if job is None:
        return jsonify({
            'status': 'error',
            'message': prep_err,
            'errors': prep_details,
            'numbered_input_preview': '',
        })

    ok_rep, rep_msg, rep_errs = apply_romanization_repair_to_job(job, task_payload)
    if not ok_rep:
        return jsonify({
            'status': 'error',
            'message': rep_msg,
            'errors': rep_errs,
            'numbered_input_preview': job.get('numbered_preview', ''),
        }), 400

    provider = runtime.get('translation', {}).get('provider') or AI_TRANSLATION_SETTINGS['provider']
    base_url_raw = runtime.get('translation', {}).get('base_url') or AI_TRANSLATION_SETTINGS['base_url']
    model = runtime.get('translation', {}).get('model') or AI_TRANSLATION_SETTINGS['model']
    expect_reasoning = runtime.get('translation', {}).get('expect_reasoning', AI_TRANSLATION_SETTINGS['expect_reasoning'])

    def _normalize_base_url(u: str) -> str:
        if not u:
            return u
        u = u.strip().rstrip('/')
        u = re.sub(r'/(chat|responses)/(completions|streams?)$', '', u, flags=re.I)
        return u

    base_url = _normalize_base_url(str(base_url_raw or ''))

    request_id = f"{int(time.time() * 1000)}_{random.randint(1000, 9999)}"
    audit_started_at = time.monotonic()
    credential_id = str(auth_context.get('credential_id') or (auth_context.get('credential') or {}).get('credential_id') or '').strip()
    if auth_context.get('is_system_admin'):
        credential_id = 'system'
    romanize_preset_id = str(runtime.get('id') or '').strip() or 'manual'
    romanize_effective_model = str(model or '')
    audit_payload: Dict[str, Any] = {
        'request_id': request_id,
        'event': 'ai_romanize_lyrics',
        'credential_id': credential_id,
        'auth_type': str(auth_context.get('auth_type') or ('system' if auth_context.get('is_system_admin') else '')).strip(),
        'device_id': auth_context.get('device_id'),
        'preset_id': romanize_preset_id,
        'preset_name': str(runtime.get('name') or ''),
        'source_mode': str(runtime.get('source_mode') or ''),
        'resolved_from': str(runtime.get('resolved_from') or ''),
        'provider': provider,
        'base_url': base_url,
        'effective_model': romanize_effective_model,
        'model': romanize_effective_model,
        'thinking_model': str(runtime.get('thinking', {}).get('model') or ''),
        'source_format': job['source_format'],
        'target_format': job['target_format'],
        'lines': len(job['numbered']),
    }

    def generate():
        audit = dict(audit_payload)
        reasoning_cap: Dict[str, Any] = {}
        logged = False
        current_stage = 'parse_source'
        numbered = job['numbered']
        numbered_preview = job['numbered_preview']
        prompt_tokens = 0
        completion_tokens = 0
        total_tokens = 0

        def finalize(success: bool, err_msg: str = '', **extra: Any) -> None:
            nonlocal logged
            if logged:
                return
            logged = True
            audit.update({
                'success': success,
                'duration_ms': int((time.monotonic() - audit_started_at) * 1000),
                'error': err_msg,
            })
            audit.update(extra)
            append_ai_usage_log(audit)

        try:
            reasoning_cap = get_reasoning_control_capability(provider=provider, base_url=base_url, model=model)
            meta_body = {
                'numbered_input_preview': numbered_preview,
                'user_block': str(job.get('user_block') or ''),
                'source_format': job['source_format'],
                'target_format': job['target_format'],
                'source_effective': job['source_effective'],
                'lines': len(numbered),
            }
            yield f"meta:{json.dumps(meta_body, ensure_ascii=False)}\n"
            current_stage = 'parse_source'
            yield f"stage:{json.dumps({'stage': 'parse_source', 'state': 'success'}, ensure_ascii=False)}\n"

            client = build_openai_client(api_key=api_key, base_url=base_url)
            reasoning_opts = build_reasoning_request_options(
                provider=provider, base_url=base_url, model=model, expect_reasoning=bool(expect_reasoning)
            )

            current_stage = 'send_request'
            yield f"stage:{json.dumps({'stage': 'send_request', 'state': 'active'}, ensure_ascii=False)}\n"
            try:
                stream = client.chat.completions.create(
                    model=model,
                    messages=job['messages'],
                    stream=True,
                    **reasoning_opts,
                )
            except Exception as api_err:
                yield f"stage:{json.dumps({'stage': 'send_request', 'state': 'error'}, ensure_ascii=False)}\n"
                yield f"error:{json.dumps({'message': str(api_err), 'errors': []}, ensure_ascii=False)}\n"
                finalize(
                    False,
                    str(api_err),
                    reasoning_control_supported=bool(reasoning_cap.get('supported', False)),
                    **_ai_usage_audit_token_payload(prompt_tokens, completion_tokens, total_tokens),
                )
                return

            yield f"stage:{json.dumps({'stage': 'send_request', 'state': 'success'}, ensure_ascii=False)}\n"
            current_stage = 'receive_stream'
            yield f"stage:{json.dumps({'stage': 'receive_stream', 'state': 'active'}, ensure_ascii=False)}\n"

            raw_out_accum = ""
            current_reasoning = ""
            last_stats_at = time.monotonic()
            chunk_i = 0
            for chunk in stream:
                chunk_i += 1
                usage = getattr(chunk, 'usage', None)
                if usage is not None:
                    prompt_tokens, completion_tokens, total_tokens = _ai_usage_merge_stream_usage(
                        usage, prompt_tokens, completion_tokens, total_tokens
                    )
                choices = getattr(chunk, 'choices', None)
                if not choices:
                    continue
                delta = getattr(choices[0], 'delta', None)
                if delta is None:
                    continue
                if expect_reasoning and hasattr(delta, 'reasoning_content') and delta.reasoning_content:
                    current_reasoning += delta.reasoning_content
                    yield f"reasoning:{json.dumps({'reasoning': current_reasoning}, ensure_ascii=False)}\n"
                if hasattr(delta, 'content') and delta.content:
                    raw_out_accum += delta.content
                    yield f"raw:{json.dumps({'chunk': delta.content}, ensure_ascii=False)}\n"
                    now = time.monotonic()
                    if now - last_stats_at >= 0.2 or chunk_i % 12 == 0:
                        last_stats_at = now
                        yield (
                            f"stats:{json.dumps({'chars': len(raw_out_accum), 'lines_detected': _count_numbered_roman_lines(raw_out_accum), 'expected_lines': len(numbered)}, ensure_ascii=False)}\n"
                        )

            yield f"stats:{json.dumps({'chars': len(raw_out_accum), 'lines_detected': _count_numbered_roman_lines(raw_out_accum), 'expected_lines': len(numbered)}, ensure_ascii=False)}\n"
            yield f"stage:{json.dumps({'stage': 'receive_stream', 'state': 'success'}, ensure_ascii=False)}\n"

            rc_sup = bool(reasoning_cap.get('supported', False))
            raw_out = raw_out_accum.strip()
            patch_raw = raw_out
            is_repair = bool(job.get('_roman_repair_mode'))
            merged_for_payload: Optional[str] = None

            current_stage = 'validating'
            yield f"stage:{json.dumps({'stage': 'validating', 'state': 'active'}, ensure_ascii=False)}\n"

            if is_repair:
                merged_candidate, merge_errs = merge_roman_patch_into_base(
                    job, str(job.get('_roman_repair_base_raw') or ''), patch_raw
                )
                if merge_errs:
                    yield f"stage:{json.dumps({'stage': 'validating', 'state': 'error'}, ensure_ascii=False)}\n"
                    yield f"stage:{json.dumps({'stage': 'assembling', 'state': 'error'}, ensure_ascii=False)}\n"
                    yield f"stage:{json.dumps({'stage': 'done', 'state': 'error'}, ensure_ascii=False)}\n"
                    err_merge: Dict[str, Any] = {
                        'message': '罗马音修复合并失败',
                        'errors': merge_errs,
                        'raw_model_output': patch_raw,
                        'patch_model_output': patch_raw,
                        'merged_model_output': merged_candidate or '',
                    }
                    yield f"error:{json.dumps(err_merge, ensure_ascii=False)}\n"
                    finalize(
                        False, '; '.join(merge_errs),
                        reasoning_control_supported=rc_sup,
                        **_ai_usage_audit_token_payload(prompt_tokens, completion_tokens, total_tokens),
                    )
                    return
                merged_for_payload = merged_candidate
                result_text, all_errors, raw_out = assemble_romanization_from_raw(job, merged_candidate or '')
            else:
                result_text, all_errors, raw_out = assemble_romanization_from_raw(job, raw_out)

            if all_errors:
                yield f"stage:{json.dumps({'stage': 'validating', 'state': 'error'}, ensure_ascii=False)}\n"
                yield f"stage:{json.dumps({'stage': 'assembling', 'state': 'error'}, ensure_ascii=False)}\n"
                yield f"stage:{json.dumps({'stage': 'done', 'state': 'error'}, ensure_ascii=False)}\n"
                err_val: Dict[str, Any] = {
                    'message': '罗马音结果未通过校验',
                    'errors': all_errors,
                    'raw_model_output': raw_out,
                }
                if is_repair:
                    err_val['patch_model_output'] = patch_raw
                    err_val['merged_model_output'] = merged_for_payload or raw_out
                yield f"error:{json.dumps(err_val, ensure_ascii=False)}\n"
                finalize(
                    False, '; '.join(all_errors),
                    reasoning_control_supported=rc_sup,
                    **_ai_usage_audit_token_payload(prompt_tokens, completion_tokens, total_tokens),
                )
            else:
                yield f"stage:{json.dumps({'stage': 'validating', 'state': 'success'}, ensure_ascii=False)}\n"
                current_stage = 'assembling'
                yield f"stage:{json.dumps({'stage': 'assembling', 'state': 'active'}, ensure_ascii=False)}\n"
                yield f"stage:{json.dumps({'stage': 'assembling', 'state': 'success'}, ensure_ascii=False)}\n"
                current_stage = 'done'
                result_payload: Dict[str, Any] = {'result_text': result_text, 'raw_model_output': raw_out}
                if is_repair:
                    result_payload['patch_model_output'] = patch_raw
                    result_payload['merged_raw_model_output'] = merged_for_payload or raw_out
                yield f"result:{json.dumps(result_payload, ensure_ascii=False)}\n"
                yield f"stage:{json.dumps({'stage': 'done', 'state': 'success'}, ensure_ascii=False)}\n"
                finalize(
                    True, '',
                    reasoning_control_supported=rc_sup,
                    **_ai_usage_audit_token_payload(prompt_tokens, completion_tokens, total_tokens),
                )

        except GeneratorExit:
            finalize(
                False,
                'client_aborted',
                reasoning_control_supported=bool(reasoning_cap.get('supported', False)),
                **_ai_usage_audit_token_payload(prompt_tokens, completion_tokens, total_tokens),
            )
            raise
        except Exception as e:
            app.logger.error("romanize_lyrics stream failed: %s", e, exc_info=True)
            try:
                stage_order = ['parse_source', 'send_request', 'receive_stream', 'validating', 'assembling', 'done']
                ci = stage_order.index(current_stage) if current_stage in stage_order else stage_order.index('receive_stream')
                for sj in range(ci, len(stage_order)):
                    yield f"stage:{json.dumps({'stage': stage_order[sj], 'state': 'error'}, ensure_ascii=False)}\n"
                yield f"error:{json.dumps({'message': str(e), 'errors': []}, ensure_ascii=False)}\n"
            except GeneratorExit:
                finalize(
                    False,
                    'client_aborted',
                    reasoning_control_supported=bool(reasoning_cap.get('supported', False)),
                    **_ai_usage_audit_token_payload(prompt_tokens, completion_tokens, total_tokens),
                )
                raise
            finalize(
                False,
                str(e),
                reasoning_control_supported=bool(reasoning_cap.get('supported', False)),
                **_ai_usage_audit_token_payload(prompt_tokens, completion_tokens, total_tokens),
            )

    return StreamingResponse(
        generate(),
        media_type='text/event-stream',
        headers={
            'Cache-Control': 'no-store, no-cache, must-revalidate, max-age=0',
            'Pragma': 'no-cache',
            'X-Accel-Buffering': 'no',
        },
    )


@app.route('/romanize_lyrics_prompt', methods=['POST'])
def romanize_lyrics_prompt():
    """Return full prompt for external AI (no model call; does not require API key)."""
    request_data = request.get_json(silent=True) or {}
    task_payload = extract_ai_task_payload(request_data)
    if not can_use_ai():
        return jsonify({'status': 'error', 'message': '当前设备没有使用 AI 的权限'}), 403
    runtime = resolve_runtime_ai_config(request_data)
    job, prep_err, prep_details = prepare_romanization_job(task_payload, runtime)
    if job is None:
        return jsonify({
            'status': 'error',
            'message': prep_err,
            'errors': prep_details,
            'numbered_input_preview': '',
        })
    if romanization_request_has_repair_fields(task_payload) and bool(job.get('compat_mode')):
        return jsonify({
            'status': 'error',
            'message': '多轮修复需要关闭兼容模式（请使用分条的 system / user 消息预设）',
            'errors': [],
            'numbered_input_preview': job.get('numbered_preview', ''),
        }), 400
    compat_mode = bool(job.get('compat_mode'))
    system_prompt = str(job.get('system_prompt') or '')
    contract = str(job.get('contract') or '')
    user_block = str(job.get('user_block') or '')
    repair_note = (
        '修复轮：POST /romanize_lyrics 时在 JSON 中加入 conversation_history（首轮 user + 各轮 assistant 成对扩展）、'
        'repair_instruction（单独一条 user）、previous_full_model_output（完整编号行基底，供服务端按行合并）。'
        '多轮修复需关闭 compat_mode。'
    )
    if compat_mode:
        final_prompt = f"{system_prompt}{contract}\n\n{user_block}"
        final_prompt_sections = None
        message_plan: Dict[str, Any] = {
            'first_turn_format': 'compat_single_user',
            'compat_single_user': final_prompt,
            'repair_usage_note': repair_note,
        }
    else:
        system_part = system_prompt + contract
        final_prompt_sections = {'system': system_part, 'user': user_block}
        final_prompt = f"[System Prompt]\n{system_part}\n\n[User Message]\n{user_block}"
        message_plan = {
            'first_turn_format': 'system_then_user',
            'system': system_part,
            'user_first': user_block,
            'repair_usage_note': repair_note,
        }

    repair_prompt_mode = False
    multiturn_prompt_text: Optional[str] = None
    repair_messages: Optional[List[Dict[str, str]]] = None
    if romanization_request_has_repair_fields(task_payload):
        job_prompt = copy.deepcopy(job)
        ok_pr, pr_msg, pr_errs = apply_romanization_repair_to_job(job_prompt, task_payload)
        if not ok_pr:
            return jsonify({
                'status': 'error',
                'message': pr_msg,
                'errors': pr_errs,
                'numbered_input_preview': job.get('numbered_preview', ''),
            }), 400
        repair_prompt_mode = True
        repair_messages = list(job_prompt['messages'])
        multiturn_prompt_text = _format_roman_messages_for_external_clipboard(repair_messages)

    return jsonify({
        'status': 'success',
        'numbered_input_preview': job['numbered_preview'],
        'system_prompt': system_prompt,
        'contract': contract,
        'user_block': user_block,
        'final_prompt': final_prompt,
        'final_prompt_sections': final_prompt_sections,
        'compat_mode': compat_mode,
        'message_plan': message_plan,
        'repair_prompt_mode': repair_prompt_mode,
        'messages': repair_messages,
        'multiturn_prompt_text': multiturn_prompt_text,
        'source_effective': job['source_effective'],
        'source_format': job['source_format'],
        'target_format': job['target_format'],
        'lines': len(job['numbered']),
        'alignment_mode': job['alignment_mode'],
    })


@app.route('/romanize_lyrics_assemble', methods=['POST'])
def romanize_lyrics_assemble():
    """Validate pasted model output and assemble LYS/LRC (no AI call; does not require API key)."""
    request_data = request.get_json(silent=True) or {}
    task_payload = extract_ai_task_payload(request_data)
    if not can_use_ai():
        return jsonify({'status': 'error', 'message': '当前设备没有使用 AI 的权限'}), 403
    runtime = resolve_runtime_ai_config(request_data)
    job, prep_err, prep_details = prepare_romanization_job(task_payload, runtime)
    if job is None:
        return jsonify({
            'status': 'error',
            'message': prep_err,
            'errors': prep_details,
            'numbered_input_preview': '',
        })
    manual = str(task_payload.get('manual_model_output') or '')
    result_text, all_errors, raw_out = assemble_romanization_from_raw(job, manual)
    if all_errors:
        return jsonify({
            'status': 'error',
            'message': '罗马音结果未通过校验',
            'errors': all_errors,
            'raw_model_output': raw_out,
            'numbered_input_preview': job['numbered_preview'],
        })
    return jsonify({
        'status': 'success',
        'result_text': result_text,
        'raw_model_output': raw_out,
        'errors': [],
    })


@app.route('/romanize_lyrics_weave_background', methods=['POST'])
def romanize_lyrics_weave_background():
    """Weave romanized LYS: insert a [6]-style background copy after each main lyric line (local only)."""
    request_data = request.get_json(silent=True) or {}
    if not can_use_ai():
        return jsonify({'status': 'error', 'message': '当前设备没有使用 AI 的权限'}), 403
    roman = str(request_data.get('roman_lys_text') or '')
    if not roman.strip():
        return jsonify({
            'status': 'error',
            'message': 'roman_lys_text 不能为空',
            'errors': ['缺少 roman_lys_text'],
        })
    bg_in = request_data.get('background_prefix', '[6]')
    woven, errs = _weave_roman_lys_as_background(roman, bg_in)
    if errs:
        return jsonify({'status': 'error', 'message': errs[0], 'errors': errs})
    return jsonify({'status': 'success', 'woven_text': woven, 'errors': []})


@app.after_request
def add_header(resp):
    resp.headers['Access-Control-Allow-Origin'] = '*'
    content_type = resp.headers.get('content-type', '')
    media_type = getattr(resp, 'media_type', '') or ''
    if content_type.startswith('application/json') or media_type.startswith('application/json'):
        resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        resp.headers['Pragma'] = 'no-cache'
        resp.headers['Expires'] = '0'
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
    try:
        file, _ = _resolve_existing_static_json_filename(file)
    except ValueError as exc:
        return str(exc), 400

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
        session['amll_reference_cover_url'] = cover_override
    else:
        session.pop('amll_reference_cover_url', None)
    if background_override:
        session['amll_direct_background_url'] = background_override
    else:
        session.pop('amll_direct_background_url', None)
    session.pop('override_cover_url', None)
    session.pop('override_background_url', None)

    for_player = request.args.get('for_player')
    if for_player in ('1', 'true', 'True'):
        session['for_player'] = True
    elif for_player is not None:
        session['for_player'] = False

    session['lyrics_json_file'] = file
    normalized_style = (style or '').strip().lower()
    session['lyrics_style'] = normalized_style
    amll_entry = get_amll_entry_assets()
    if style == '亮起':
        return render_template('Lyrics-style.HTML', amll_entry=amll_entry)
    if normalized_style == 'junp':
        return render_template('Lyrics-style.HTML-JUNP.HTML', amll_entry=amll_entry)
    else:  # 默认为 'Kok' 或其他值
        return render_template('Lyrics-style.HTML-COK-up.HTML', amll_entry=amll_entry)

@app.route('/lyrics')
def get_lyrics():
    """
    获取歌词和音源信息
    支持的音乐格式：.mp3, .wav, .ogg, .mp4
    """
    requested_file = (request.args.get('file') or '').strip()
    
    # 问题修复：显式指定 file 时严格验证，不回退
    if requested_file:
        json_file = requested_file
        try:
            _, json_path = _resolve_existing_static_json_filename(json_file)
        except ValueError:
            return jsonify({'error': f'Song file not found: {requested_file}'}), 404
    else:
        # 仅在未指定时才使用 session
        json_file = session.get('lyrics_json_file', '测试 - 测试.json')
        try:
            _, json_path = _resolve_existing_static_json_filename(json_file)
        except ValueError:
            json_path = STATIC_DIR / '测试 - 测试.json'
    
    meta_data = {}
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            meta_data = json.load(f)
    except FileNotFoundError:
        meta_data = {}
    except json.JSONDecodeError:
        return jsonify({'error': '解析元数据JSON时出错'}), 500
    except Exception:
        meta_data = {}

    # 修复：参数优先级清晰
    # 1. 优先使用显式 lys/lrc 参数（无论是否传了 file）
    # 2. 如果没有显式 lys/lrc：
    #    - 若显式传了 file，只使用 JSON 配置（不吃 session 覆盖，避免串页）
    #    - 若没有显式传 file（依赖 session），才可以吃 session 的 override_lys_url
    # 3. 最后，如果都没有，从 JSON 的 meta.lyrics 读配置
    
    lys_url = request.args.get('lys')
    lrc_url = request.args.get('lrc')
    
    # 只有在没有显式 lys/lrc 且没有显式 file 时，才从 session 读覆盖
    if not lys_url and not lrc_url and not requested_file:
        lys_url = session.get('override_lys_url')
        lrc_url = session.get('override_lrc_url')

    # 如果没有覆盖地址，再走旧逻辑：从 JSON 的 meta.lyrics 里找
    if not lys_url:
        try:
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
    for_player = request.args.get('for_player') in ('1', 'true', 'True')
    if not for_player:
        for_player = bool(session.get('for_player'))
    parsed_url = urlparse(lys_url)
    lyrics_path = os.path.join(app.static_folder, parsed_url.path.lstrip('/'))
    try:
        with open(lyrics_path, 'r', encoding='utf-8-sig') as f:
            lys_content = f.read()
    except FileNotFoundError:
        return jsonify({'error': 'LYS 歌词文件未找到'}), 404

    if for_player:
        meta = meta_data.get('meta', {}) if isinstance(meta_data, dict) else {}
        artists = _extract_artists_from_meta(meta)
        duration_ms = _get_song_duration_ms_from_json(meta_data)
        lys_content = _ensure_creator_line_in_lys(lys_content, artists, duration_ms)
    parsed_lyrics = parse_lys(lys_content)
    style_hint = (request.args.get('style') or session.get('lyrics_style') or '').strip().lower()
    if style_hint in ('junp', 'jump'):
        parsed_lyrics = expand_progressive_timestamp_lines(parsed_lyrics)
    compute_disappear_times(parsed_lyrics, delta1=500, delta2=0)

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
    try:
        _, json_path = _resolve_existing_static_json_filename(request.args.get('filename'))
    except ValueError as exc:
        return jsonify({'status': 'error', 'message': str(exc)}), 400

    if not json_path.exists():
        return jsonify({'status': 'error', 'message': 'JSON文件未找到'}), 404
    
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            json_data = json.load(f)
        response = jsonify({'status': 'success', 'jsonData': json_data})
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        return response
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
        data = request.get_json() or {}
        lyrics_path = data.get('path', '')
        for_player = data.get('for_player') in (True, '1', 1, 'true', 'True')
        if not for_player:
            for_player = request.args.get('for_player') in ('1', 'true', 'True')
        if not lyrics_path:
            return jsonify({'status': 'error', 'message': '缺少歌词路径'}), 400

        try:
            real_path = resolve_resource_path(lyrics_path, 'songs')
        except ValueError as exc:
            return jsonify({'status': 'error', 'message': f'路径不合法: {exc}'}), 400

        if not real_path.exists():
            return jsonify({'status': 'error', 'message': '歌词文件未找到'}), 404

        with open(real_path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
        if for_player:
            artists, duration_ms = _get_artists_from_related_json(real_path)
            if real_path.suffix.lower() == '.ttml':
                updated = _inject_creator_line_into_ttml(content, artists, duration_ms)
                if updated:
                    content = updated
            elif real_path.suffix.lower() == '.lys':
                updated = _ensure_creator_line_in_lys(content, artists, duration_ms)
                if updated != content:
                    content = updated
        return jsonify({'status': 'success', 'content': content})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/prepare_ttml_for_player', methods=['POST'])
def prepare_ttml_for_player():
    if not is_request_allowed():
        return abort(403)
    try:
        data = request.get_json() or {}
        lyrics_path = data.get('path', '')
        if not lyrics_path:
            return jsonify({'status': 'error', 'message': '缺少歌词路径'}), 400

        _cleanup_temp_ttml_files()

        try:
            real_path = resolve_resource_path(lyrics_path, 'songs')
        except ValueError as exc:
            return jsonify({'status': 'error', 'message': f'路径不合法: {exc}'}), 400

        if not real_path.exists():
            return jsonify({'status': 'error', 'message': '歌词文件未找到'}), 404

        artists, duration_ms = _get_artists_from_related_json(real_path)
        file_ext = real_path.suffix.lower()

        if file_ext == '.ttml':
            ttml_text = real_path.read_text(encoding='utf-8', errors='ignore')
        elif file_ext in ('.lys', '.lrc'):
            temp_dir = tempfile.TemporaryDirectory(dir=str(SONGS_DIR))
            temp_path = Path(temp_dir.name) / f"{real_path.stem}.ttml"
            if file_ext == '.lys':
                success, error_msg = lys_to_ttml(str(real_path), str(temp_path))
            else:
                success, error_msg = lrc_to_ttml(str(real_path), str(temp_path))
            if not success:
                temp_dir.cleanup()
                return jsonify({'status': 'error', 'message': f'转换失败: {error_msg}'}), 400
            ttml_text = temp_path.read_text(encoding='utf-8', errors='ignore')
            temp_dir.cleanup()
        else:
            return jsonify({'status': 'error', 'message': '只支持LYS/LRC/TTML格式'}), 400

        ttml_url = _create_player_ttml_from_text(ttml_text, artists, duration_ms)
        if not ttml_url:
            return jsonify({'status': 'error', 'message': '生成TTML失败'}), 500

        return jsonify({'status': 'success', 'ttmlPath': ttml_url})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


def _build_beat_curve_bins(freqs, band_count: int, min_freq: float, max_freq: float) -> List[Tuple[int, int]]:
    log_min = math.log10(min_freq)
    log_max = math.log10(max_freq)
    edges = [
        10 ** (log_min + (log_max - log_min) * (i / band_count))
        for i in range(band_count + 1)
    ]
    bins: List[Tuple[int, int]] = []
    for i in range(band_count):
        f0 = edges[i]
        f1 = edges[i + 1]
        start = int(freqs.searchsorted(f0, side="left"))
        end = int(freqs.searchsorted(f1, side="right"))
        start = max(0, min(start, len(freqs) - 1))
        end = max(start + 1, min(end, len(freqs)))
        bins.append((start, end))
    return bins


def _generate_beat_curve_file(
    audio_path: Path,
    output_path: Path,
    frame_ms: int = 1000,
    band_count: int = 16,
    min_freq: float = 40.0,
    max_freq: float = 14000.0,
    sample_rate: int = 44100,
    n_fft: int = 2048,
    lyric_windows_ms: Optional[List[Tuple[int, int]]] = None
) -> Dict[str, Any]:
    try:
        import numpy as np
        import librosa
    except ImportError as exc:
        raise RuntimeError("Missing dependencies: numpy, librosa") from exc

    audio, sr = librosa.load(str(audio_path), sr=sample_rate, mono=True)
    if audio.size == 0:
        raise RuntimeError("Empty audio data")
    hop_length = max(1, int(round(sr * frame_ms / 1000)))
    stft = librosa.stft(audio, n_fft=n_fft, hop_length=hop_length, window="hann", center=True)
    mag = np.abs(stft)
    if mag.size == 0:
        raise RuntimeError("Empty spectrogram")
    mag_max = float(np.max(mag))
    if not math.isfinite(mag_max) or mag_max <= 0:
        mag_max = 1.0
    mag = mag / mag_max
    freqs = librosa.fft_frequencies(sr=sr, n_fft=n_fft)
    band_bins = _build_beat_curve_bins(freqs, band_count, min_freq, max_freq)

    frame_count = mag.shape[1]
    if frame_count <= 0:
        raise RuntimeError("Empty spectrogram")

    emphasized = np.zeros((band_count, frame_count), dtype=np.float32)
    for i, (start, end) in enumerate(band_bins):
        band_mag = mag[start:end, :]
        if band_mag.size == 0:
            continue
        rms = np.sqrt(np.mean(band_mag ** 2, axis=0))
        peak = np.max(band_mag, axis=0)
        level = 0.7 * rms + 0.3 * peak
        band_emph = np.power(np.clip(level, 0.0, 1.0), 1.15)
        emphasized[i, :] = band_emph

    window_frames = max(3, int(round(8 * 1000 / frame_ms)))
    half_window = window_frames // 2
    bytes_per_frame = band_count
    output = bytearray(frame_count * bytes_per_frame)

    prefix = np.concatenate([np.zeros((band_count, 1), dtype=np.float32), np.cumsum(emphasized, axis=1)], axis=1)
    prefix_sq = np.concatenate([np.zeros((band_count, 1), dtype=np.float32), np.cumsum(emphasized ** 2, axis=1)], axis=1)

    def _std_for_range(band_idx: int, left: int, right: int) -> float:
        if right <= left:
            return 0.0
        sum_v = float(prefix[band_idx, right] - prefix[band_idx, left])
        sum_sq = float(prefix_sq[band_idx, right] - prefix_sq[band_idx, left])
        count = max(1, right - left)
        mean = sum_v / count
        var = max(0.0, (sum_sq / count) - mean ** 2)
        return math.sqrt(var)

    lyric_windows_ms = lyric_windows_ms or []

    for i in range(band_count):
        series = emphasized[i, :]
        stds = np.zeros(frame_count, dtype=np.float32)
        if lyric_windows_ms:
            frame_windows = []
            for start_ms, end_ms in lyric_windows_ms:
                left = max(0, int(math.floor(start_ms / frame_ms)))
                right = min(frame_count, int(math.ceil(end_ms / frame_ms)) + 1)
                if right <= left:
                    continue
                frame_windows.append((left, right))
            frame_windows.sort(key=lambda item: item[0])
            window_stds = []
            for left, right in frame_windows:
                window_stds.append(_std_for_range(i, left, right))

            window_idx = 0
            for t in range(frame_count):
                while window_idx < len(frame_windows) and t >= frame_windows[window_idx][1]:
                    window_idx += 1
                if window_idx < len(frame_windows):
                    left, right = frame_windows[window_idx]
                    if left <= t < right:
                        stds[t] = window_stds[window_idx]
                        continue
                local_left = max(0, t - half_window)
                local_right = min(frame_count, t + half_window + 1)
                stds[t] = _std_for_range(i, local_left, local_right)
        else:
            for t in range(frame_count):
                left = max(0, t - half_window)
                right = min(frame_count, t + half_window + 1)
                stds[t] = _std_for_range(i, left, right)

        target_std = float(np.percentile(stds, 70))
        target_std = max(target_std, 0.03)
        mean_energy = float(np.mean(series))
        energy_factor = max(0.7, min(0.35 / (mean_energy + 0.05), 2.0))
        for t in range(frame_count):
            local_std = float(stds[t])
            gain = target_std / (local_std + 0.02)
            gain = max(0.6, min(gain * energy_factor, 3.0))
            gain_norm = (gain - 0.6) / (3.0 - 0.6)
            gain_norm = max(0.0, min(gain_norm, 1.0))
            output[t * bytes_per_frame + i] = int(round(gain_norm * 255))

    actual_frame_ms = max(1, int(round(hop_length * 1000 / sr)))
    header = bytearray()
    header.extend(b"AMBG")
    header.extend(struct.pack("<BBHI", 1, band_count, actual_frame_ms, frame_count))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as handle:
        handle.write(header)
        handle.write(output)

    return {
        "frame_ms": actual_frame_ms,
        "frame_count": frame_count,
        "band_count": band_count
    }


def _update_song_json_with_curve(json_path: Path, curve_relative: str) -> None:
    with BEAT_CURVE_LOCK:
        with open(json_path, 'r', encoding='utf-8') as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise RuntimeError('json 格式不正确')
        meta = data.setdefault('meta', {})
        if not isinstance(meta, dict):
            raise RuntimeError('meta 格式不正确')
        meta['background_beat_curve'] = curve_relative
        with open(json_path, 'w', encoding='utf-8') as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)


def _beat_curve_task_key(json_path: Path, song_relative: str) -> str:
    return f"{json_path.resolve()}::{song_relative}"


def _submit_beat_curve_job(
    json_path: Path,
    song_relative: str,
    audio_path: Path,
    output_path: Path,
    frame_ms: int,
    band_count: int,
    min_freq: float,
    max_freq: float,
    sample_rate: int,
    n_fft: int,
    lyric_windows_ms: Optional[List[Tuple[int, int]]] = None
) -> None:
    task_key = _beat_curve_task_key(json_path, song_relative)

    def _job():
        info = _generate_beat_curve_file(
            audio_path=audio_path,
            output_path=output_path,
            frame_ms=frame_ms,
            band_count=band_count,
            min_freq=min_freq,
            max_freq=max_freq,
            sample_rate=sample_rate,
            n_fft=n_fft,
            lyric_windows_ms=lyric_windows_ms
        )
        curve_relative = f"./songs/{output_path.name}"
        _update_song_json_with_curve(json_path, curve_relative)
        return {
            "curve_relative": curve_relative,
            "info": info
        }

    def _done_callback(future):
        with BEAT_CURVE_LOCK:
            task = BEAT_CURVE_TASKS.get(task_key)
        if task is None:
            return
        try:
            result = future.result()
            with BEAT_CURVE_LOCK:
                task['status'] = 'done'
                task['result'] = result
                task['error'] = None
        except Exception as exc:
            with BEAT_CURVE_LOCK:
                task['status'] = 'error'
                task['error'] = str(exc)

    future = THREADPOOL_EXECUTOR.submit(_job)
    with BEAT_CURVE_LOCK:
        BEAT_CURVE_TASKS[task_key] = {
            "status": "pending",
            "future": future,
            "result": None,
            "error": None,
            "output_path": str(output_path)
        }
    future.add_done_callback(_done_callback)


@app.route('/song-info')
def song_info():
    if not is_request_allowed():
        return jsonify({'error': 'Forbidden'}), 403

    requested_file = (request.args.get('file') or '').strip()
    
    # 问题 1 & 2 修复：显式指定 file 时严格验证，不回退
    if requested_file:
        try:
            _, json_path = _resolve_existing_static_json_filename(requested_file)
        except ValueError:
            return jsonify({'error': f'Song file not found: {requested_file}'}), 404
    else:
        # 仅在未指定 file 时才使用 session 或默认值
        json_file = session.get('lyrics_json_file', '测试 - 测试.json')
        try:
            _, json_path = _resolve_existing_static_json_filename(json_file)
        except ValueError:
            json_path = STATIC_DIR / '测试 - 测试.json'
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

        # 问题 1 修复：仅当没有显式指定 file 时才使用 session 里的覆盖
        # 这样可以防止 A 歌曲的 session 覆盖被应用到 B 歌曲
        should_apply_session_overrides = not requested_file
        amll_direct_background = session.get('amll_direct_background_url') if should_apply_session_overrides else None
        amll_reference_cover = session.get('amll_reference_cover_url') if should_apply_session_overrides else None

        def normalize_media_url(raw_value: Optional[str]) -> str:
            if not raw_value or raw_value == '!':
                return ''
            cleaned = str(raw_value).strip().replace('\\', '/').replace('/static/songs/', '/songs/').replace('static/songs/', 'songs/')
            if cleaned.startswith('./'):
                cleaned = cleaned[2:]
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

            for key in ('albumImgSrc', 'cover', 'coverUrl', 'background_beat_curve'):
                normalized = normalize_media_url(meta.get(key))
                if normalized:
                    meta[key] = normalized

        # 应用前端传入的临时覆盖
        normalized_amll_background = normalize_media_url(amll_direct_background)
        if normalized_amll_background:
            data['amll_direct_background_url'] = normalized_amll_background

        normalized_amll_cover = normalize_media_url(amll_reference_cover)
        if normalized_amll_cover:
            data['amll_reference_cover_url'] = normalized_amll_cover
            data['cover'] = normalized_amll_cover
            data['coverUrl'] = normalized_amll_cover
            if isinstance(meta, dict):
                meta['albumImgSrc'] = normalized_amll_cover
                meta['cover'] = normalized_amll_cover
                meta['coverUrl'] = normalized_amll_cover

        if ('cover' not in data or not data['cover'] or data['cover'] == '!') and not normalized_amll_cover:
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

        palette_payload = _build_cover_palette_payload(data, meta)
        if palette_payload:
            data['cover_palette'] = palette_payload

        if isinstance(data, dict) and data.get('song'):
            original_song = str(data['song'])
            playback_blocked = _song_info_device_playback_blocked()
            if not playback_blocked:
                gateway_url = build_media_audio_url(original_song)
                if gateway_url is not None:
                    data['song'] = gateway_url
                elif _media_token_requires_device():
                    playback_blocked = True
                    app.logger.warning(
                        'media_gateway: song-info blocked static song URL (device cookie required)',
                    )
            if playback_blocked:
                data['song'] = ''
                data['audio_delivery'] = _audio_delivery_device_required()
            else:
                data['audio_delivery'] = build_audio_delivery_info()

        return jsonify(data)
    except FileNotFoundError:
        return jsonify({'error': 'Song info file not found'}), 404
    except json.JSONDecodeError:
        return jsonify({'error': 'Error decoding JSON'}), 500


@app.route('/api/extract_frame', methods=['POST'])
def extract_frame():
    """提取视频或动图的指定时间帧作为静态图片

    用于 AMLL 歌词系统的封面和背景静态化，支持：
    - 视频文件 (MP4, WebM, OGG, M4V, MOV)
    - 动图文件 (GIF, APNG, WebP)

    请求体: { "source_url": "源文件URL或路径", "seek_seconds": 0 }
    响应: { "frame_url": "提取的首帧图片URL", "status", "success" }
    
    向后兼容：仍支持 "video_url"/"videoUrl" 作为参数名
    """
    if not is_request_allowed():
        return abort(403)

    def is_mvod_preview_url(value):
        raw = str(value or '').strip().lower()
        if not raw:
            return False
        if 'mvod.itunes.apple.com' in raw:
            return True
        try:
            parsed = urlparse(raw)
            return (parsed.hostname or '').lower() == 'mvod.itunes.apple.com'
        except Exception:
            return False

    def normalize_seek_seconds(raw_value, source_value):
        default_seek = 10.0 if is_mvod_preview_url(source_value) else 0.0
        if raw_value in (None, ''):
            return default_seek
        try:
            seek_value = float(raw_value)
        except (TypeError, ValueError):
            return default_seek
        return max(0.0, seek_value)

    def is_probably_video_source(value):
        raw = str(value or '').strip().lower()
        if not raw:
            return False
        if is_mvod_preview_url(raw):
            return True
        path_part = raw.split('?', 1)[0].split('#', 1)[0]
        return path_part.endswith(('.mp4', '.webm', '.ogg', '.m4v', '.mov'))

    def build_seek_attempts(raw_value, source_value):
        requested_seek = normalize_seek_seconds(raw_value, source_value)
        if not is_probably_video_source(source_value):
            return [requested_seek]

        default_attempts = [10.0, 12.0, 15.0, 18.0] if is_mvod_preview_url(source_value) else [0.0, 1.0, 3.0, 5.0]
        seek_attempts = []
        for candidate in [requested_seek, *default_attempts]:
            try:
                normalized_candidate = max(0.0, float(candidate))
            except (TypeError, ValueError):
                continue
            if any(abs(normalized_candidate - existing) < 0.01 for existing in seek_attempts):
                continue
            seek_attempts.append(normalized_candidate)
        return seek_attempts or [requested_seek]

    def format_ffmpeg_seek_timestamp(seconds):
        total_ms = max(0, int(round(float(seconds) * 1000)))
        hours, remainder = divmod(total_ms, 3600000)
        minutes, remainder = divmod(remainder, 60000)
        whole_seconds, milliseconds = divmod(remainder, 1000)
        return f'{hours:02d}:{minutes:02d}:{whole_seconds:02d}.{milliseconds:03d}'

    def detect_temp_suffix(source_value):
        lower_source = str(source_value or '').lower()
        for suffix in ('.gif', '.apng', '.webp', '.webm', '.ogg', '.m4v', '.mov'):
            if lower_source.endswith(suffix) or f'{suffix}?' in lower_source:
                return suffix
        return '.mp4'

    def download_remote_source_to_temp(source_value):
        import requests

        response = requests.get(source_value, timeout=30)
        response.raise_for_status()
        temp_source = tempfile.NamedTemporaryFile(delete=False, suffix=detect_temp_suffix(source_value))
        temp_source.write(response.content)
        temp_source.close()
        return temp_source.name

    def analyze_reference_image(image_path: Path):
        from PIL import ImageStat

        try:
            with Image.open(image_path) as img:
                img = img.convert('RGB')
                width, height = img.size
                if width < 64 or height < 64:
                    return False, {
                        'reason': 'too_small',
                        'width': width,
                        'height': height
                    }

                sample = img.copy()
                sample.thumbnail((128, 128))
                grayscale = sample.convert('L')
                gray_stat = ImageStat.Stat(grayscale)
                mean_brightness = float(gray_stat.mean[0]) if gray_stat.mean else 0.0
                gray_stddev = float(gray_stat.stddev[0]) if gray_stat.stddev else 0.0

                quantized = sample.quantize(colors=16, method=Image.MEDIANCUT)
                color_counts = quantized.getcolors(16) or []
                total_pixels = sum(count for count, _ in color_counts)
                dominant_ratio = (max((count for count, _ in color_counts), default=0) / total_pixels) if total_pixels else 1.0
                color_count = len(color_counts)

                quality = {
                    'width': width,
                    'height': height,
                    'mean_brightness': round(mean_brightness, 2),
                    'gray_stddev': round(gray_stddev, 2),
                    'color_count': color_count,
                    'dominant_ratio': round(dominant_ratio, 4)
                }

                if mean_brightness < 10 and gray_stddev < 12:
                    quality['reason'] = 'near_black_frame'
                    return False, quality
                if mean_brightness > 245 and gray_stddev < 12:
                    quality['reason'] = 'near_white_frame'
                    return False, quality
                if gray_stddev < 8:
                    quality['reason'] = 'low_gray_stddev'
                    return False, quality
                if color_count <= 2:
                    quality['reason'] = 'too_few_colors'
                    return False, quality
                if dominant_ratio > 0.92:
                    quality['reason'] = 'dominant_color_ratio_too_high'
                    return False, quality

                quality['reason'] = 'ok'
                return True, quality
        except Exception as exc:
            return False, {'reason': f'analysis_failed: {exc}'}

    payload = request.get_json(silent=True) or {}
    source_url = payload.get('source_url') or payload.get('sourceUrl') or payload.get('video_url') or payload.get('videoUrl')
    requested_seek_seconds = payload.get('seek_seconds', payload.get('seekSeconds'))
    seek_attempts = build_seek_attempts(requested_seek_seconds, source_url)

    if not source_url:
        return jsonify({'status': 'error', 'message': '缺少 source_url 或 video_url 参数'}), 400

    try:
        # 解析源路径
        source_path = source_url
        is_temp_file = False
        downloaded_source_path = None
        
        # 检测是否为URL（包括畸形URL）
        is_url = source_url.startswith('http://') or source_url.startswith('https://') or \
                 '://' in source_url or source_url.startswith('http:/') or source_url.startswith('https:/')
        
        if not is_url:
            # 本地路径，尝试解析
            try:
                source_path = resolve_resource_path(source_url, 'static')
            except ValueError:
                # 如果解析失败，尝试直接使用
                source_path = source_url
                if not os.path.exists(source_path):
                    # 尝试在 static/songs 目录下查找
                    alt_path = os.path.join('static', 'songs', source_url.lstrip('/'))
                    if os.path.exists(alt_path):
                        source_path = alt_path

        if not is_url and not os.path.exists(source_path):
            return jsonify({'status': 'error', 'message': f'源文件不存在: {source_url}'}), 404

        def ensure_local_source_path():
            nonlocal source_path, is_temp_file, downloaded_source_path
            if not is_url:
                return source_path
            if downloaded_source_path and os.path.exists(downloaded_source_path):
                return downloaded_source_path
            downloaded_source_path = download_remote_source_to_temp(source_url)
            source_path = downloaded_source_path
            is_temp_file = True
            return downloaded_source_path

        # 生成输出图片路径
        source_name_source = urlparse(source_url).path if is_url else source_path
        source_name = Path(source_name_source).stem or 'frame'
        output_dir = Path(STATIC_DIR) / 'temp' / 'frames'
        output_dir.mkdir(parents=True, exist_ok=True)
        import subprocess
        attempt_results = []
        last_error_message = ''

        for seek_seconds in seek_attempts:
            seek_suffix = int(round(seek_seconds * 1000))
            output_path = output_dir / f'{source_name}_frame_{seek_suffix}.jpg'
            ffmpeg_success = False

            try:
                result = subprocess.run(
                    ['ffmpeg', '-y', '-ss', format_ffmpeg_seek_timestamp(seek_seconds), '-i', str(source_path), '-vframes', '1', str(output_path)],
                    capture_output=True,
                    text=True,
                    timeout=30
                )
                ffmpeg_success = (result.returncode == 0)
                if not ffmpeg_success:
                    last_error_message = result.stderr.strip() or 'ffmpeg failed'
            except FileNotFoundError:
                last_error_message = 'ffmpeg_not_found'
            except subprocess.TimeoutExpired:
                last_error_message = 'ffmpeg_timeout'

            if not ffmpeg_success and is_url:
                try:
                    local_source_path = ensure_local_source_path()
                    result = subprocess.run(
                        ['ffmpeg', '-y', '-ss', format_ffmpeg_seek_timestamp(seek_seconds), '-i', str(local_source_path), '-vframes', '1', str(output_path)],
                        capture_output=True,
                        text=True,
                        timeout=30
                    )
                    ffmpeg_success = (result.returncode == 0)
                    if not ffmpeg_success:
                        last_error_message = result.stderr.strip() or 'ffmpeg download fallback failed'
                except FileNotFoundError:
                    last_error_message = 'ffmpeg_not_found'
                except subprocess.TimeoutExpired:
                    last_error_message = 'ffmpeg_timeout'
                except Exception as e:
                    last_error_message = f'download_fallback_failed: {str(e)}'

            if not ffmpeg_success:
                try:
                    image_source_path = ensure_local_source_path() if is_url else source_path
                    img = Image.open(image_source_path)
                    if img.mode not in ['RGB', 'L']:
                        img = img.convert('RGB')
                    img.save(output_path, 'JPEG')
                    ffmpeg_success = True
                except Exception as e:
                    last_error_message = f'fallback_failed: {str(e)}'

            if not ffmpeg_success or not output_path.exists():
                attempt_results.append({
                    'seek_seconds': seek_seconds,
                    'status': 'extract_failed',
                    'message': last_error_message
                })
                continue

            is_valid_frame, quality = analyze_reference_image(output_path)
            attempt_results.append({
                'seek_seconds': seek_seconds,
                'status': 'ok' if is_valid_frame else 'invalid',
                'quality': quality
            })

            if not is_valid_frame:
                try:
                    output_path.unlink(missing_ok=True)
                except Exception:
                    pass
                continue

            frame_filename = f'{source_name}_frame_{seek_suffix}.jpg'
            frame_url = build_public_url('temp', f'frames/{frame_filename}')
            return jsonify({
                'status': 'success',
                'frame_url': frame_url,
                'frame_path': str(output_path),
                'seek_seconds': seek_seconds,
                'attempts': attempt_results,
                'quality': quality
            })

        return jsonify({
            'status': 'error',
            'message': '未找到有效参考帧',
            'attempts': attempt_results,
            'seek_attempts': seek_attempts
        }), 422

    except Exception as e:
        return jsonify({'status': 'error', 'message': f'提取帧失败: {str(e)}'}), 500
    finally:
        # 清理临时下载的源文件
        if 'is_temp_file' in locals() and is_temp_file and 'source_path' in locals():
            try:
                os.unlink(source_path)
            except Exception:
                pass


# 向后兼容：保持旧 API 名称可用
@app.route('/api/extract_video_frame', methods=['POST'])
def extract_video_frame_backward_compat():
    return extract_frame()


@app.route('/amll/generate_beat_curve', methods=['POST'])
def generate_beat_curve():
    if not is_request_allowed():
        return abort(403)
    locked_response = require_unlocked_device('生成背景节奏曲线')
    if locked_response:
        return locked_response

    payload = request.get_json(silent=True) or {}
    json_path = payload.get('json_path') or payload.get('jsonPath')
    frame_ms = int(payload.get('frame_ms', 1000))
    band_count = int(payload.get('band_count', 16))
    min_freq = float(payload.get('min_freq', 40.0))
    max_freq = float(payload.get('max_freq', 14000.0))
    sample_rate = int(payload.get('sample_rate', 44100))
    n_fft = int(payload.get('n_fft', 2048))
    force = bool(payload.get('force'))

    if frame_ms <= 0 or band_count <= 0 or min_freq <= 0 or max_freq <= 0:
        return jsonify({'status': 'error', 'message': '参数不合法'}), 400

    if json_path:
        try:
            json_real = resolve_resource_path(json_path, 'static')
        except ValueError as exc:
            return jsonify({'status': 'error', 'message': f'路径不合法: {exc}'}), 400
    else:
        json_file = session.get('lyrics_json_file', '测试 - 测试.json')
        try:
            _, json_real = _resolve_existing_static_json_filename(json_file)
        except ValueError as exc:
            return jsonify({'status': 'error', 'message': f'json 文件路径不合法: {exc}'}), 400

    if not json_real.exists():
        return jsonify({'status': 'error', 'message': 'json 文件未找到'}), 404

    try:
        with open(json_real, 'r', encoding='utf-8') as handle:
            data = json.load(handle)
    except Exception as exc:
        return jsonify({'status': 'error', 'message': f'读取 json 失败: {exc}'}), 500

    if not isinstance(data, dict):
        return jsonify({'status': 'error', 'message': 'json 格式不正确'}), 400

    meta = data.get('meta') if isinstance(data, dict) else None
    if isinstance(meta, dict):
        existing_curve = meta.get('background_beat_curve')
        if existing_curve and not force:
            curve_relative = _extract_single_song_relative(existing_curve)
            curve_path = (SONGS_DIR / curve_relative) if curve_relative else None
            if curve_path and curve_path.exists():
                curve_public = build_public_url('songs', curve_relative)
                return jsonify({
                    'status': 'success',
                    'curve': curve_public,
                    'meta_key': 'background_beat_curve',
                    'frame_ms': None,
                    'frame_count': None,
                    'band_count': None
                })

    song_value = data.get('song')
    song_relative = _extract_single_song_relative(song_value)
    if not song_relative:
        return jsonify({'status': 'error', 'message': '歌曲路径无效'}), 400

    audio_path = SONGS_DIR / song_relative
    if not audio_path.exists():
        return jsonify({'status': 'error', 'message': '歌曲文件未找到'}), 404

    base_name = sanitize_filename(Path(song_relative).stem)
    if not base_name:
        base_name = f"beat_curve_{int(time.time())}"
    output_path = SONGS_DIR / f"{base_name}.ambc"

    lyric_windows_ms = []
    if isinstance(meta, dict):
        lyric_windows_ms = _extract_lyrics_windows_from_meta(
            meta.get('lyrics'),
            meta,
            padding_ms=int(payload.get('lyric_pad_ms', 400)),
            merge_gap_ms=int(payload.get('lyric_gap_ms', 200)),
            fallback_json_path=json_real
        )

    if output_path.exists() and not force:
        try:
            _update_song_json_with_curve(json_real, f"./songs/{output_path.name}")
        except Exception as exc:
            return jsonify({'status': 'error', 'message': f'写入 json 失败: {exc}'}), 500
        return jsonify({
            'status': 'success',
            'curve': build_public_url('songs', output_path.name),
            'meta_key': 'background_beat_curve',
            'frame_ms': None,
            'frame_count': None,
            'band_count': None
        })

    task_key = _beat_curve_task_key(json_real, song_relative)
    with BEAT_CURVE_LOCK:
        task = BEAT_CURVE_TASKS.get(task_key)

    if task:
        status = task.get('status')
        if status == 'done':
            result = task.get('result') or {}
            curve_relative = result.get('curve_relative') or f"./songs/{output_path.name}"
            try:
                curve_rel = _extract_single_song_relative(curve_relative)
                curve_url = build_public_url('songs', curve_rel) if curve_rel else f"/songs/{output_path.name}"
            except Exception:
                curve_url = f"/songs/{output_path.name}"
            info = result.get('info') or {}
            return jsonify({
                'status': 'success',
                'curve': curve_url,
                'meta_key': 'background_beat_curve',
                'frame_ms': info.get('frame_ms'),
                'frame_count': info.get('frame_count'),
                'band_count': info.get('band_count')
            })
        if status == 'error':
            error_message = task.get('error') or '生成失败'
            if force:
                with BEAT_CURVE_LOCK:
                    BEAT_CURVE_TASKS.pop(task_key, None)
            else:
                return jsonify({'status': 'error', 'message': error_message}), 500
        else:
            return jsonify({'status': 'pending', 'message': '任务处理中'}), 202

    _submit_beat_curve_job(
        json_path=json_real,
        song_relative=song_relative,
        audio_path=audio_path,
        output_path=output_path,
        frame_ms=frame_ms,
        band_count=band_count,
        min_freq=min_freq,
        max_freq=max_freq,
        sample_rate=sample_rate,
        n_fft=n_fft,
        lyric_windows_ms=lyric_windows_ms
    )

    return jsonify({'status': 'pending', 'message': '任务已创建'}), 202

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


def is_loopback_request() -> bool:
    """只判断请求是否来自回环地址。"""
    return is_local_remote()

PORT_STATUS_FILE = BASE_PATH / 'port_status.json'
SECURITY_CONFIG_FILE = BASE_PATH / 'security_config.json'
MEDIA_CONFIG_FILE = BASE_PATH / 'media_config.json'
SERVER_SECRET_FILE = BASE_PATH / 'server_secret.key'
TRUSTED_DEVICES_FILE = BASE_PATH / 'trusted_devices.json'

MEDIA_AUDIO_EXTENSIONS = frozenset({
    '.mp3', '.wav', '.ogg', '.flac', '.m4a', '.aac', '.opus', '.webm', '.mp4',
})

DEFAULT_MEDIA_CONFIG = {
    'audio_delivery_mode': 'stream',
    'enforce_media_gateway_for_audio': False,
    'strict_device_binding': False,
    'media_token_ttl_seconds': 7200,
    'initial_chunk_bytes': 524288,
}

_MEDIA_AUDIO_MIMETYPES = {
    '.mp3': 'audio/mpeg',
    '.wav': 'audio/wav',
    '.ogg': 'audio/ogg',
    '.flac': 'audio/flac',
    '.m4a': 'audio/mp4',
    '.aac': 'audio/aac',
    '.opus': 'audio/opus',
    '.webm': 'audio/webm',
    '.mp4': 'audio/mp4',
}

_MEDIA_AUDIT_LOG: deque = deque(maxlen=500)
_MEDIA_RATE_LIMIT: Dict[Tuple[str, str], List[float]] = {}
_MEDIA_RATE_LIMIT_LOCK = threading.Lock()
_MEDIA_RATE_LIMIT_WINDOW_SEC = 60
_MEDIA_RATE_LIMIT_MAX_PER_WINDOW = 120

# 安全配置默认值
DEFAULT_SECURITY_CONFIG = {
    'security_enabled': True,
    'system_password_hash': '',
    'trusted_expire_days': 30,
    'device_credentials': []
}

# 读取安全配置
def get_security_config():
    migrated = False
    if SECURITY_CONFIG_FILE.exists():
        try:
            with open(SECURITY_CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
        except Exception:
            config = {}
    else:
        config = {}

    normalized_config, migrated = normalize_security_config(config)
    if migrated:
        save_security_config(normalized_config)
    return normalized_config

# 保存安全配置
def save_security_config(config):
    normalized_config, _ = normalize_security_config(config)
    with open(SECURITY_CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(normalized_config, f, ensure_ascii=False, indent=2)


def normalize_media_config(config: Any) -> Tuple[Dict[str, Any], bool]:
    raw = config if isinstance(config, dict) else {}
    normalized = dict(DEFAULT_MEDIA_CONFIG)
    normalized.update(raw)
    migrated = False

    mode = str(normalized.get('audio_delivery_mode') or 'stream').strip().lower()
    if mode not in ('oneshot', 'stream', 'both'):
        mode = DEFAULT_MEDIA_CONFIG['audio_delivery_mode']
        migrated = True
    normalized['audio_delivery_mode'] = mode

    normalized['enforce_media_gateway_for_audio'] = parse_bool(
        normalized.get('enforce_media_gateway_for_audio'),
        DEFAULT_MEDIA_CONFIG['enforce_media_gateway_for_audio'],
    )

    normalized['strict_device_binding'] = parse_bool(
        normalized.get('strict_device_binding'),
        DEFAULT_MEDIA_CONFIG['strict_device_binding'],
    )

    ttl = coerce_int(normalized.get('media_token_ttl_seconds'), DEFAULT_MEDIA_CONFIG['media_token_ttl_seconds'])
    if ttl is None or ttl < 60:
        ttl = DEFAULT_MEDIA_CONFIG['media_token_ttl_seconds']
        migrated = True
    normalized['media_token_ttl_seconds'] = ttl

    chunk = coerce_int(normalized.get('initial_chunk_bytes'), DEFAULT_MEDIA_CONFIG['initial_chunk_bytes'])
    if chunk is None or chunk < 4096:
        chunk = DEFAULT_MEDIA_CONFIG['initial_chunk_bytes']
        migrated = True
    normalized['initial_chunk_bytes'] = chunk

    if normalized != raw:
        migrated = True
    return normalized, migrated


def get_media_config() -> Dict[str, Any]:
    migrated = False
    if MEDIA_CONFIG_FILE.exists():
        try:
            with open(MEDIA_CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
        except Exception:
            config = {}
    else:
        config = {}

    normalized_config, migrated = normalize_media_config(config)
    if migrated:
        save_media_config(normalized_config)
    return normalized_config


def save_media_config(config: Dict[str, Any]) -> Dict[str, Any]:
    normalized_config, _ = normalize_media_config(config)
    with open(MEDIA_CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(normalized_config, f, ensure_ascii=False, indent=2)
    return normalized_config


def get_media_config_policy_notes() -> Dict[str, str]:
    """Human-readable policy hints for GET /media/config (additive JSON keys only)."""
    return {
        'enforce_media_gateway_for_audio': (
            'Default false: /songs/*.audio stays directly reachable until enabled; '
            'when true, clients must use the signed /media/audio gateway and cannot fetch audio from /songs/* directly.'
        ),
        'audio_delivery_mode': (
            'stream: FileResponse with native Range support; '
            'oneshot: entire file in one response; '
            'both: clients append ?mode=stream or ?mode=oneshot on /media/audio URLs (default stream when omitted); '
            'GET /song-info includes audio_delivery metadata when mode is both. '
            'Lyrics-style frontends read audio_delivery and let the user pick stream vs oneshot (?mode= query).'
        ),
        'strict_device_binding': (
            'Default false. When true and the request is not from loopback: signed URLs require FEW_DEVICE_ID '
            '(build_media_audio_url skips signing without the cookie); /media/audio returns 403 device_required '
            'without the cookie and rejects tokens not bound to the matching device. '
            'With security_enabled and enforce_media_gateway_for_audio, GET /song-info omits a playable song URL '
            'and returns audio_delivery.error=device_required when the cookie is missing.'
        ),
        'require_device_for_media_token': (
            'Alias concept: same as strict_device_binding for gateway tokens—no anonymous empty-device tokens '
            'on non-loopback clients when strict binding is enabled.'
        ),
        'initial_chunk_bytes': (
            'Reserved tuning knob; stream mode uses FileResponse and does not truncate to this size.'
        ),
        'credential_media_playback_mode_policy': (
            'Per shared-credential override for audio delivery. Priority: credential policy > global '
            'audio_delivery_mode > ?mode= on /media/audio. inherit uses global config; stream_only and '
            'oneshot_only force a single mode and ignore client ?mode=; user_select always exposes stream '
            'and oneshot (like global both). System/local admin sessions without a credential use inherit '
            '(full access to global settings).'
        ),
    }


def _get_media_signing_secret() -> str:
    if SERVER_SECRET_FILE.exists():
        try:
            secret = SERVER_SECRET_FILE.read_text(encoding='utf-8').strip()
            if secret:
                return secret
        except Exception:
            pass
    return str(app.secret_key)


def _media_token_sign_payload(relative_path: str, exp: int, device_id: str) -> str:
    return f"{relative_path}|{int(exp)}|{device_id or ''}"


def sign_media_audio_token(relative_path: str, exp: int, device_id: str = '') -> str:
    secret = _get_media_signing_secret()
    payload = _media_token_sign_payload(relative_path, exp, device_id)
    digest = hmac.new(secret.encode('utf-8'), payload.encode('utf-8'), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode('ascii').rstrip('=')


def verify_media_audio_token(relative_path: str, exp: int, token: str, device_id: str = '') -> bool:
    if not token:
        return False
    expected = sign_media_audio_token(relative_path, exp, device_id)
    return hmac.compare_digest(expected, token.strip())


def _media_device_id_for_signing() -> str:
    if has_request_context():
        return request.cookies.get('FEW_DEVICE_ID') or ''
    return ''


def _strict_device_binding_active() -> bool:
    media_cfg = get_media_config()
    return bool(media_cfg.get('strict_device_binding')) and not is_local_remote()


def _media_token_requires_device() -> bool:
    """Fail closed when issuing signed URLs without FEW_DEVICE_ID."""
    return _strict_device_binding_active()


_SONG_INFO_DEVICE_REQUIRED_MESSAGE = (
    'Audio playback requires a trusted device (FEW_DEVICE_ID cookie). '
    'Register or sign in on this device first.'
)


def _song_info_device_playback_blocked() -> bool:
    """True when song-info must not expose a playable URL (strict binding, no device cookie)."""
    if not _strict_device_binding_active():
        return False
    security_cfg = get_security_config()
    if not security_cfg.get('security_enabled', True):
        return False
    if is_local_remote():
        return False
    return not bool(_media_device_id_for_signing())


def _audio_delivery_device_required() -> Dict[str, str]:
    return {
        'error': 'device_required',
        'message': _SONG_INFO_DEVICE_REQUIRED_MESSAGE,
    }


def _default_media_audio_url_mode() -> str:
    return 'stream'


def _get_credential_media_playback_policy_from_auth() -> Tuple[str, str]:
    """Return (credential_policy, policy_source) for the current request."""
    if not has_request_context():
        return ('inherit', 'global')

    auth = get_current_device_auth_context()
    credential = auth.get('credential')
    if isinstance(credential, dict):
        policy = normalize_credential_media_playback_mode_policy(
            credential.get('media_playback_mode_policy')
        )
        if policy != 'inherit':
            return (policy, 'credential')
    return ('inherit', 'global')


def _parse_request_media_mode() -> Optional[str]:
    if not has_request_context():
        return None
    requested = (request.args.get('mode') or '').strip().lower()
    if requested in ('stream', 'oneshot'):
        return requested
    return None


def resolve_effective_media_playback_policy() -> Dict[str, Any]:
    """Resolve audio delivery policy: credential > global audio_delivery_mode > request ?mode=."""
    credential_policy, policy_source = _get_credential_media_playback_policy_from_auth()
    global_mode = str(get_media_config().get('audio_delivery_mode') or 'stream').strip().lower()
    if global_mode not in ('stream', 'oneshot', 'both'):
        global_mode = DEFAULT_MEDIA_CONFIG['audio_delivery_mode']

    default_mode = _default_media_audio_url_mode()
    requested_mode = _parse_request_media_mode()
    locked_by_policy = False
    available_modes = ['stream', 'oneshot']

    if credential_policy == 'stream_only':
        effective_policy = 'stream_only'
        client_mode = 'stream'
        available_modes = ['stream']
        locked_by_policy = True
        policy_source = 'credential'
    elif credential_policy == 'oneshot_only':
        effective_policy = 'oneshot_only'
        client_mode = 'oneshot'
        available_modes = ['oneshot']
        locked_by_policy = True
        policy_source = 'credential'
    elif credential_policy == 'user_select':
        effective_policy = 'user_select'
        client_mode = 'both'
        available_modes = ['stream', 'oneshot']
        policy_source = 'credential'
    else:
        effective_policy = global_mode
        policy_source = 'global'
        if global_mode == 'stream':
            client_mode = 'stream'
            available_modes = ['stream']
        elif global_mode == 'oneshot':
            client_mode = 'oneshot'
            available_modes = ['oneshot']
        else:
            client_mode = 'both'
            available_modes = ['stream', 'oneshot']

    resolved_url_mode: Optional[str] = None
    if locked_by_policy:
        resolved_url_mode = client_mode if client_mode in ('stream', 'oneshot') else default_mode
    elif client_mode == 'both':
        if requested_mode and requested_mode in available_modes:
            resolved_url_mode = requested_mode
        else:
            resolved_url_mode = default_mode
    elif client_mode in ('stream', 'oneshot'):
        resolved_url_mode = client_mode

    return {
        'mode': client_mode,
        'available_modes': available_modes,
        'default_mode': default_mode,
        'locked_by_policy': locked_by_policy,
        'policy_source': policy_source,
        'effective_policy': effective_policy,
        'credential_policy': credential_policy,
        'global_mode': global_mode,
        'resolved_url_mode': resolved_url_mode,
    }


def _resolve_media_audio_url_mode(mode: Optional[str] = None) -> Optional[str]:
    """Return mode query value for signed /media/audio URLs."""
    policy = resolve_effective_media_playback_policy()
    if policy.get('locked_by_policy'):
        return policy.get('resolved_url_mode')

    if policy.get('mode') == 'both':
        if mode:
            requested = str(mode).strip().lower()
            if requested in policy.get('available_modes', []):
                return requested
        return policy.get('resolved_url_mode')

    resolved = policy.get('resolved_url_mode')
    return resolved if resolved in ('stream', 'oneshot') else None


def build_audio_delivery_info() -> Dict[str, Any]:
    policy = resolve_effective_media_playback_policy()
    return {
        'mode': policy['mode'],
        'available_modes': policy['available_modes'],
        'default_mode': policy['default_mode'],
        'locked_by_policy': policy['locked_by_policy'],
        'policy_source': policy['policy_source'],
        'effective_policy': policy['effective_policy'],
    }


def build_media_audio_url(
    song_value: str,
    device_id: Optional[str] = None,
    base_url: Optional[str] = None,
    mode: Optional[str] = None,
) -> Optional[str]:
    relative = _extract_single_song_relative(song_value)
    if not relative:
        return str(song_value or '').strip()

    if device_id is None:
        device_id = _media_device_id_for_signing()
    if _media_token_requires_device() and not device_id:
        app.logger.warning(
            'media_gateway: skipped signed audio URL for %s (FEW_DEVICE_ID required)',
            relative,
        )
        return None

    media_cfg = get_media_config()
    ttl = int(media_cfg.get('media_token_ttl_seconds') or DEFAULT_MEDIA_CONFIG['media_token_ttl_seconds'])
    exp = int(time.time()) + ttl
    token = sign_media_audio_token(relative, exp, device_id)
    root = (base_url or get_public_base_url()).rstrip('/')
    query_params: Dict[str, str] = {
        'file': relative,
        'exp': str(exp),
        'token': token,
    }
    resolved_mode = _resolve_media_audio_url_mode(mode)
    if resolved_mode:
        query_params['mode'] = resolved_mode
    query = urlencode(query_params)
    return f"{root}/media/audio?{query}"


def _rewrite_client_song_summary(summary: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(summary, dict):
        return summary
    song_value = summary.get('song')
    if not song_value:
        return summary
    out = dict(summary)
    playback_blocked = _song_info_device_playback_blocked()
    if not playback_blocked:
        gateway_url = build_media_audio_url(str(song_value))
        if gateway_url is not None:
            out['song'] = gateway_url
        elif _media_token_requires_device():
            playback_blocked = True
    if playback_blocked:
        out['song'] = ''
        out['audio_delivery'] = _audio_delivery_device_required()
        return out
    out['audio_delivery'] = build_audio_delivery_info()
    return out


def _rewrite_client_song_summaries(summaries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [_rewrite_client_song_summary(item) for item in summaries]


def _guess_audio_mimetype(path: Path) -> str:
    return _MEDIA_AUDIO_MIMETYPES.get(path.suffix.lower(), 'application/octet-stream')


def _append_media_audit(entry: Dict[str, Any]) -> None:
    entry = dict(entry)
    entry.setdefault('ts', now_iso())
    _MEDIA_AUDIT_LOG.appendleft(entry)
    app.logger.info('media_gateway %s', json.dumps(entry, ensure_ascii=False))


def _check_media_rate_limit(remote_addr: str, relative_path: str) -> bool:
    key = (remote_addr or 'unknown', relative_path)
    now = time.time()
    with _MEDIA_RATE_LIMIT_LOCK:
        bucket = _MEDIA_RATE_LIMIT.get(key, [])
        bucket = [ts for ts in bucket if now - ts < _MEDIA_RATE_LIMIT_WINDOW_SEC]
        if len(bucket) >= _MEDIA_RATE_LIMIT_MAX_PER_WINDOW:
            _MEDIA_RATE_LIMIT[key] = bucket
            return False
        bucket.append(now)
        _MEDIA_RATE_LIMIT[key] = bucket
    return True


def _can_manage_media_config() -> bool:
    return can_manage_system() or is_loopback_request()


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
                devices = json.load(f)
                if not isinstance(devices, dict):
                    return {}
                normalized_devices = {}
                for device_id, device_info in devices.items():
                    info = device_info if isinstance(device_info, dict) else {}
                    auth_type = str(info.get('auth_type') or '').strip().lower()
                    system_admin = parse_bool(info.get('system_admin'), False) or auth_type == 'system' or str(info.get('credential_id') or '').strip() == 'legacy-admin'
                    normalized_devices[str(device_id)] = {
                        'created_at': str(info.get('created_at') or now_iso()),
                        'last_seen': str(info.get('last_seen') or now_iso()),
                        'ua_hash': str(info.get('ua_hash') or ''),
                        'ip': str(info.get('ip') or ''),
                        'credential_id': str(info.get('credential_id') or ''),
                        'remark': str(info.get('remark') or ''),
                        'expires_at': str(info.get('expires_at') or ''),
                        'max_uses': coerce_int(info.get('max_uses')),
                        'used_count': coerce_int(info.get('used_count'), 0) or 0,
                        'auth_type': auth_type or ('system' if system_admin else ('credential' if str(info.get('credential_id') or '').strip() else '')),
                        'system_admin': system_admin,
                        'permissions': normalize_device_permissions(info.get('permissions')),
                    }
                return normalized_devices
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

    security_config = get_security_config()
    resolved = resolve_trusted_device_auth_state(security_config, device_info)
    if not resolved:
        del trusted_devices[device_id]
        save_trusted_devices(trusted_devices)
        return False

    permissions = resolved.get('permissions', dict(DEFAULT_DEVICE_PERMISSIONS))
    is_system_admin = bool(resolved.get('is_system_admin'))

    # 检查过期时间
    expire_days = security_config.get('trusted_expire_days', 30)
    expire_seconds = expire_days * 24 * 3600
    
    try:
        last_seen = datetime.fromisoformat(str(device_info.get('last_seen') or ''))
    except Exception:
        last_seen = datetime.now()
    if (datetime.now() - last_seen).total_seconds() > expire_seconds:
        # 自动删除过期设备
        del trusted_devices[device_id]
        save_trusted_devices(trusted_devices)
        return False
        
    # 更新最后访问时间
    device_info['last_seen'] = now_iso()
    device_info['auth_type'] = 'system' if is_system_admin else 'credential'
    device_info['system_admin'] = is_system_admin
    device_info['credential_id'] = '' if is_system_admin else str(device_info.get('credential_id') or '')
    device_info['permissions'] = permissions
    trusted_devices[device_id] = device_info
    save_trusted_devices(trusted_devices)
    
    return bool(is_system_admin or device_info.get('permissions', {}).get('write_access', False))

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
    # 允许远程查看只读歌曲列表
    if request.path == '/songs/summary':
        return True
    if request.path == '/songs/snapshot':
        return True
    if request.path == '/songs/search':
        return True
    if request.path == '/song-info':
        return True
    if request.path == '/songs/summary/batch' and request.method == 'POST':
        return True
    if request.path == '/songs/artist':
        return True
    if request.path == '/songs/artists':
        return True
    # 允许远程快速导入 static.zip
    if request.path == '/import_static':
        return True
    # 允许远程备份与下载客户端备份
    if request.path in (
        '/backup_client_state',
        '/download_client_backup',
        '/anchor_backup',
        '/download_anchor_backup',
        '/get_anchor_backup'
    ):
        return True
    # 允许访问媒体音频（该接口有独立的 token 验证机制）
    if request.path == '/media/audio':
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


def find_matching_device_credential(password: str) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]], str]:
    security_config = get_security_config()
    system_password_hash = get_system_password_hash(security_config)
    if system_password_hash and verify_password(password, system_password_hash):
        return security_config, None, 'system'
    for credential in security_config.get('device_credentials', []):
        if not is_credential_usable(credential):
            continue
        if verify_password(password, credential.get('password_hash', '')):
            return security_config, credential, 'credential'
    return security_config, None, ''


def update_security_credential_usage(credential_id: str) -> None:
    security_config = get_security_config()
    updated = False
    for credential in security_config.get('device_credentials', []):
        if credential.get('credential_id') == credential_id:
            credential['used_count'] = coerce_int(credential.get('used_count'), 0) or 0
            credential['used_count'] += 1
            credential['updated_at'] = now_iso()
            updated = True
            break
    if updated:
        save_security_config(security_config)

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
    security_config, matched_credential, auth_type = find_matching_device_credential(password)
    has_any_credential = bool(get_system_password_hash(security_config)) or any(is_credential_usable(item) for item in security_config.get('device_credentials', []))
    if not matched_credential and auth_type != 'system':
        if not has_any_credential:
            return jsonify({'status': 'error', 'message': '系统未设置密码，请联系管理员'}), 401
        app.logger.warning(f"认证失败 - 设备ID: {device_id[:8]}..., IP: {request.remote_addr}, UA哈希: {hashlib.md5(request.headers.get('User-Agent', '').encode()).hexdigest()[:8]}")
        return jsonify({'status': 'error', 'message': '密码错误'}), 401

    # 添加设备到受信任列表
    trusted_devices = load_trusted_devices()
    now = datetime.now().isoformat()
    is_system_admin = auth_type == 'system'
    permissions = system_admin_permissions() if is_system_admin else credential_permissions_snapshot(matched_credential)
    next_used_count = (coerce_int((matched_credential or {}).get('used_count'), 0) or 0) + 1
    
    if device_id not in trusted_devices:
        trusted_devices[device_id] = {
            'created_at': now,
            'last_seen': now,
            'ua_hash': hashlib.md5(request.headers.get('User-Agent', '').encode()).hexdigest(),
            'ip': request.remote_addr,
            'credential_id': '' if is_system_admin else matched_credential.get('credential_id', ''),
            'remark': '系统密码' if is_system_admin else matched_credential.get('remark', ''),
            'expires_at': '' if is_system_admin else matched_credential.get('expires_at', ''),
            'max_uses': None if is_system_admin else matched_credential.get('max_uses'),
            'used_count': next_used_count,
            'permissions': permissions,
            'auth_type': 'system' if is_system_admin else 'credential',
            'system_admin': is_system_admin,
        }
    else:
        # 更新最后访问时间
        trusted_devices[device_id]['last_seen'] = now
        trusted_devices[device_id]['credential_id'] = '' if is_system_admin else matched_credential.get('credential_id', '')
        trusted_devices[device_id]['remark'] = '系统密码' if is_system_admin else matched_credential.get('remark', '')
        trusted_devices[device_id]['expires_at'] = '' if is_system_admin else matched_credential.get('expires_at', '')
        trusted_devices[device_id]['max_uses'] = None if is_system_admin else matched_credential.get('max_uses')
        trusted_devices[device_id]['used_count'] = next_used_count
        trusted_devices[device_id]['permissions'] = permissions
        trusted_devices[device_id]['auth_type'] = 'system' if is_system_admin else 'credential'
        trusted_devices[device_id]['system_admin'] = is_system_admin
    
    save_trusted_devices(trusted_devices)
    if matched_credential:
        update_security_credential_usage(matched_credential['credential_id'])
    
    # 记录成功的认证
    app.logger.info(f"认证成功 - 设备ID: {device_id[:8]}..., IP: {request.remote_addr}, 类型: {'system' if is_system_admin else 'credential'}")
    
    response = jsonify({
        'status': 'success',
        'trusted': bool(is_system_admin or permissions.get('write_access', False)),
        'authenticated': True,
        'device_id': device_id[:8] + '...',  # 只返回部分ID用于显示
        'credential_id': '' if is_system_admin else matched_credential.get('credential_id', ''),
        'remark': '系统密码' if is_system_admin else matched_credential.get('remark', ''),
        'permissions': permissions,
        'expires_at': '' if is_system_admin else matched_credential.get('expires_at', ''),
        'max_uses': None if is_system_admin else matched_credential.get('max_uses'),
        'used_count': next_used_count,
        'auth_type': 'system' if is_system_admin else 'credential',
        'is_system_admin': is_system_admin,
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
    
    context = get_current_device_auth_context()
    system_password_set = bool(get_system_password_hash(security_config))
    shared_credential_set = any(is_credential_usable(item) for item in security_config.get('device_credentials', []))
    
    return jsonify({
        'status': 'success',
        'trusted': bool(context.get('trusted')),
        'authenticated': bool(context.get('authenticated')),
        'security_enabled': security_config.get('security_enabled', True),
        'is_local': bool(context.get('is_local')),
        'auth_type': context.get('auth_type', ''),
        'is_system_admin': bool(context.get('is_system_admin')),
        'has_system_password': system_password_set,
        'has_shared_credentials': shared_credential_set,
        'has_password': bool(system_password_set or shared_credential_set),
        'credential_id': context.get('credential_id') or (context.get('credential') or {}).get('credential_id', ''),
        'remark': context.get('remark') or (context.get('credential') or {}).get('remark', ''),
        'permissions': context.get('permissions', dict(DEFAULT_DEVICE_PERMISSIONS)),
        'expires_at': context.get('expires_at') or (context.get('credential') or {}).get('expires_at', ''),
        'remaining_uses': (
            max(0, (coerce_int(context.get('max_uses')) or 0) - (coerce_int(context.get('used_count'), 0) or 0))
            if coerce_int(context.get('max_uses')) is not None else None
        )
    })

@app.route('/auth/set_password', methods=['POST'])
def auth_set_password():
    """设置系统密码（本机或系统管理员可操作）"""
    if not can_manage_system():
        return abort(403)
    
    data = request.json
    if not data or 'password' not in data:
        return jsonify({'status': 'error', 'message': '请输入密码'}), 400
    
    password = data['password']
    permissions = system_admin_permissions()

    security_config = get_security_config()
    current_system_password_hash = get_system_password_hash(security_config)
    current_password = str(data.get('current_password') or '').strip()
    current_context = get_current_device_auth_context()
    if not (is_loopback_request() or current_context.get('is_system_admin')) and current_system_password_hash:
        if not current_password:
            return jsonify({'status': 'error', 'message': '请输入当前系统密码'}), 400
        if not verify_password(current_password, current_system_password_hash):
            return jsonify({'status': 'error', 'message': '当前系统密码错误'}), 400

    password_conflict = find_password_conflict(security_config, password, skip_system_password=True)
    if password_conflict == 'system':
        return jsonify({'status': 'error', 'message': '该密码已被系统密码占用，请使用新的唯一密码'}), 400
    if password_conflict:
        return jsonify({'status': 'error', 'message': '该密码已被共享凭据占用，请使用新的唯一密码'}), 400
    
    security_config['system_password_hash'] = hash_password(password)
    security_config['password_hash'] = ''
    save_security_config(security_config)
    
    # 清除所有受信任设备（安全起见，修改密码后所有设备需要重新认证）
    save_trusted_devices({})
    
    app.logger.info(f"密码已更新 - 操作IP: {request.remote_addr}")
    
    return jsonify({'status': 'success', 'message': '系统密码保存成功', 'credential_id': 'system', 'permissions': permissions})


def serialize_security_credential(credential: Dict[str, Any]) -> Dict[str, Any]:
    return {
        'credential_id': credential.get('credential_id', ''),
        'remark': credential.get('remark', ''),
        'expires_at': credential.get('expires_at', ''),
        'max_uses': credential.get('max_uses'),
        'used_count': credential.get('used_count', 0),
        'revoked': credential.get('revoked', False),
        'usable': is_credential_usable(credential),
        'status': get_credential_status(credential),
        'permissions': normalize_device_permissions(credential.get('permissions')),
        'media_playback_mode_policy': normalize_credential_media_playback_mode_policy(
            credential.get('media_playback_mode_policy')
        ),
        'created_at': credential.get('created_at', ''),
        'updated_at': credential.get('updated_at', ''),
        'has_password': bool(credential.get('password_hash')),
    }


@app.route('/auth/credentials', methods=['GET', 'POST'])
def auth_credentials_collection():
    if not can_manage_system():
        return abort(403)

    security_config = get_security_config()
    if request.method == 'GET':
        return jsonify({
            'status': 'success',
            'credentials': [serialize_security_credential(credential) for credential in security_config.get('device_credentials', [])],
            'total': len(security_config.get('device_credentials', [])),
        })

    data = request.get_json(silent=True) or {}
    password = str(data.get('password') or '').strip()
    if not password:
        return jsonify({'status': 'error', 'message': '请输入密码'}), 400

    credential_id = str(data.get('credential_id') or f'cred_{uuid.uuid4().hex[:12]}').strip()
    permissions = normalize_device_permissions(data.get('permissions'))
    if not any(permissions.values()):
        permissions = default_device_permissions(write_access=False)
    password_conflict = find_password_conflict(security_config, password, exclude_credential_id=credential_id)
    if password_conflict == 'system':
        return jsonify({'status': 'error', 'message': '该密码已被系统密码占用，请使用新的唯一密码'}), 400
    if password_conflict:
        return jsonify({'status': 'error', 'message': '该密码已被共享凭据占用，请使用新的唯一密码'}), 400
    credential = normalize_security_credential({
        'credential_id': credential_id,
        'password_hash': hash_password(password),
        'remark': data.get('remark') or '',
        'expires_at': data.get('expires_at') or '',
        'max_uses': data.get('max_uses'),
        'used_count': 0,
        'revoked': False,
        'permissions': permissions,
        'media_playback_mode_policy': data.get('media_playback_mode_policy'),
    }, credential_id)

    existing = [item for item in security_config.get('device_credentials', []) if item.get('credential_id') != credential_id]
    existing.append(credential)
    security_config['device_credentials'] = existing
    security_config['password_hash'] = ''
    save_security_config(security_config)
    return jsonify({'status': 'success', 'credential': serialize_security_credential(credential)})


@app.route('/auth/credentials/<credential_id>', methods=['PUT', 'DELETE'])
def auth_credential_item(credential_id):
    if not can_manage_system():
        return abort(403)

    security_config = get_security_config()
    credentials = security_config.get('device_credentials', [])
    target_index = next((idx for idx, item in enumerate(credentials) if item.get('credential_id') == credential_id), -1)
    if target_index < 0:
        return jsonify({'status': 'error', 'message': '凭据不存在'}), 404

    if request.method == 'DELETE':
        credentials[target_index]['revoked'] = True
        credentials[target_index]['updated_at'] = now_iso()
        save_security_config(security_config)
        return jsonify({'status': 'success', 'credential': serialize_security_credential(credentials[target_index])})

    data = request.get_json(silent=True) or {}
    target = credentials[target_index]
    if parse_bool(target.get('revoked'), False):
        return jsonify({'status': 'error', 'message': '已吊销凭据不能编辑，请新建新的共享凭据'}), 400
    if 'remark' in data:
        target['remark'] = str(data.get('remark') or '').strip()
    if 'expires_at' in data:
        target['expires_at'] = str(data.get('expires_at') or '').strip()
    if 'max_uses' in data:
        target['max_uses'] = coerce_int(data.get('max_uses'))
    if 'revoked' in data:
        target['revoked'] = parse_bool(data.get('revoked'), target.get('revoked', False))
    if 'permissions' in data:
        target['permissions'] = normalize_device_permissions(data.get('permissions'))
    if 'media_playback_mode_policy' in data:
        target['media_playback_mode_policy'] = normalize_credential_media_playback_mode_policy(
            data.get('media_playback_mode_policy')
        )
    if data.get('password'):
        password = str(data.get('password'))
        password_conflict = find_password_conflict(security_config, password, exclude_credential_id=credential_id)
        if password_conflict == 'system':
            return jsonify({'status': 'error', 'message': '该密码已被系统密码占用，请使用新的唯一密码'}), 400
        if password_conflict:
            return jsonify({'status': 'error', 'message': '该密码已被共享凭据占用，请使用新的唯一密码'}), 400
        target['password_hash'] = hash_password(password)
    target['updated_at'] = now_iso()
    credentials[target_index] = normalize_security_credential(target, credential_id)
    security_config['device_credentials'] = credentials
    security_config['password_hash'] = ''
    save_security_config(security_config)
    return jsonify({'status': 'success', 'credential': serialize_security_credential(credentials[target_index])})


def iter_ai_usage_events(days: int = 7) -> List[Dict[str, Any]]:
    safe_days = max(1, min(int(days or 7), 30))
    cutoff = datetime.now() - timedelta(days=safe_days - 1)
    events: List[Dict[str, Any]] = []
    try:
        for path in sorted(AI_USAGE_LOG_DIR.glob('ai-usage-*.jsonl'), reverse=True):
            try:
                date_part = path.stem.replace('ai-usage-', '').strip()
                file_dt = datetime.strptime(date_part, '%Y-%m-%d')
                if file_dt.date() < cutoff.date():
                    continue
            except Exception:
                pass
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            obj = json.loads(line)
                        except Exception:
                            continue
                        if isinstance(obj, dict):
                            events.append(obj)
            except Exception:
                continue
    except Exception:
        return []
    return events


_AI_USAGE_TRACKED_EVENTS = frozenset({'ai_translate_lyrics', 'ai_romanize_lyrics'})


@app.route('/admin/ai-usage/summary', methods=['GET'])
def admin_ai_usage_summary():
    if not can_manage_system():
        return abort(403)
    days = coerce_int(request.args.get('days'), 7) or 7
    events = iter_ai_usage_events(days=days)
    grouped: Dict[str, Dict[str, Any]] = {}
    for ev in events:
        if str(ev.get('event') or '') not in _AI_USAGE_TRACKED_EVENTS:
            continue
        credential_id = str(ev.get('credential_id') or 'unknown')
        bucket = grouped.setdefault(credential_id, {
            'credential_id': credential_id,
            'total': 0,
            'success': 0,
            'failure': 0,
            'last_ts': 0,
            'preset_ids': set(),
            'prompt_tokens_total': 0,
            'completion_tokens_total': 0,
            'total_tokens_total': 0,
        })
        bucket['total'] += 1
        if ev.get('success') is True:
            bucket['success'] += 1
        elif ev.get('success') is False:
            bucket['failure'] += 1
        ts = coerce_int(ev.get('ts'), 0) or 0
        if ts > bucket['last_ts']:
            bucket['last_ts'] = ts
        bucket['preset_ids'].add(_ai_usage_preset_key(ev))
        p_tok, c_tok, t_tok = _ai_usage_event_tokens(ev)
        bucket['prompt_tokens_total'] += p_tok
        bucket['completion_tokens_total'] += c_tok
        bucket['total_tokens_total'] += t_tok
    summary = []
    for bucket in grouped.values():
        preset_ids = bucket.pop('preset_ids', set())
        bucket['preset_count'] = len(preset_ids) if isinstance(preset_ids, set) else 0
        summary.append(bucket)
    summary.sort(key=lambda x: (x.get('total', 0), x.get('last_ts', 0)), reverse=True)
    security_config = get_security_config()
    remark_by_id = {
        str(c.get('credential_id') or '').strip(): str(c.get('remark') or '').strip()
        for c in security_config.get('device_credentials', [])
        if str(c.get('credential_id') or '').strip()
    }
    for bucket in summary:
        credential_id = str(bucket.get('credential_id') or 'unknown')
        if credential_id == 'unknown':
            bucket['credential_remark'] = ''
            bucket['credential_display'] = '未知凭据（内部编号缺失）'
            bucket['credential_primary_label'] = '未知凭据（内部编号缺失）'
            bucket['credential_secondary_label'] = ''
        elif credential_id == 'system':
            bucket['credential_remark'] = ''
            bucket['credential_display'] = '系统管理员'
            bucket['credential_primary_label'] = '系统管理员'
            bucket['credential_secondary_label'] = credential_id
        else:
            remark = remark_by_id.get(credential_id, '')
            bucket['credential_remark'] = remark
            bucket['credential_display'] = f"{remark}（{credential_id}）" if remark else credential_id
            bucket['credential_primary_label'] = remark if remark else '未备注'
            bucket['credential_secondary_label'] = credential_id
    return jsonify({'status': 'success', 'days': days, 'summary': summary, 'total_events': len(events)})


@app.route('/admin/ai-usage/recent', methods=['GET'])
def admin_ai_usage_recent():
    if not can_manage_system():
        return abort(403)
    days = coerce_int(request.args.get('days'), 7) or 7
    limit = max(1, min(coerce_int(request.args.get('limit'), 200) or 200, 1000))
    credential_id = str(request.args.get('credential_id') or '').strip()
    success_raw = request.args.get('success')
    q = str(request.args.get('q') or '').strip().lower()

    want_success: Optional[bool] = None
    if success_raw is not None and str(success_raw).strip() != '':
        want_success = parse_bool(success_raw, False)

    events = iter_ai_usage_events(days=days)
    filtered: List[Dict[str, Any]] = []
    for ev in events:
        if str(ev.get('event') or '') not in _AI_USAGE_TRACKED_EVENTS:
            continue
        if credential_id == 'unknown':
            if str(ev.get('credential_id') or '').strip():
                continue
        elif credential_id and str(ev.get('credential_id') or '') != credential_id:
            continue
        if want_success is not None and ev.get('success') is not want_success:
            continue
        if q:
            hay = ' '.join([
                str(ev.get('request_id') or ''),
                str(ev.get('model') or ''),
                str(ev.get('effective_model') or ''),
                str(ev.get('translation_model') or ''),
                str(ev.get('thinking_model') or ''),
                str(ev.get('preset_id') or ''),
                str(ev.get('preset_name') or ''),
                str(ev.get('source_mode') or ''),
                str(ev.get('resolved_from') or ''),
                str(ev.get('song_name') or ''),
                str(ev.get('song_names_preview') or ''),
                str(ev.get('provider') or ''),
                str(ev.get('jsonFile') or ''),
                str(ev.get('lyricsPath') or ''),
                str(ev.get('content_preview') or ''),
                str(ev.get('json_files_preview') or ''),
                str(ev.get('source_format') or ''),
                str(ev.get('target_format') or ''),
                str(ev.get('items_preview') or ''),
                str(ev.get('prompt_tokens') or ''),
                str(ev.get('completion_tokens') or ''),
                str(ev.get('total_tokens') or ''),
                json.dumps(ev.get('items') or [], ensure_ascii=False) if isinstance(ev.get('items'), list) else '',
            ]).lower()
            if q not in hay:
                continue
        filtered.append(ev)

    filtered.sort(key=lambda x: coerce_int(x.get('ts'), 0) or 0, reverse=True)
    return jsonify({'status': 'success', 'days': days, 'events': filtered[:limit], 'total': len(filtered)})


@app.route('/admin/ai-usage/export', methods=['GET'])
def admin_ai_usage_export():
    if not can_manage_system():
        return abort(403)
    days = coerce_int(request.args.get('days'), 7) or 7
    export_format = str(request.args.get('format') or 'jsonl').strip().lower()
    events = iter_ai_usage_events(days=days)
    events.sort(key=lambda x: coerce_int(x.get('ts'), 0) or 0)

    if export_format == 'csv':
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            'ts', 'request_id', 'credential_id', 'auth_type', 'preset_id',
            'provider', 'base_url', 'model',
            'translation_model', 'thinking_model', 'effective_model',
            'prompt_tokens', 'completion_tokens', 'total_tokens',
            'song_name', 'song_names_preview', 'preset_name', 'source_mode', 'resolved_from',
            'thinking_enabled',
            'expect_reasoning', 'mode', 'item_count', 'success', 'duration_ms',
            'content_length', 'line_count', 'sha256', 'content_preview', 'error'
        ])
        for ev in events:
            writer.writerow([
                ev.get('ts'), ev.get('request_id'), ev.get('credential_id'), ev.get('auth_type'), ev.get('preset_id'),
                ev.get('provider'), ev.get('base_url'), ev.get('model'),
                ev.get('translation_model'), ev.get('thinking_model'), ev.get('effective_model'),
                ev.get('prompt_tokens'), ev.get('completion_tokens'), ev.get('total_tokens'),
                ev.get('song_name'), ev.get('song_names_preview'), ev.get('preset_name'),
                ev.get('source_mode'), ev.get('resolved_from'),
                ev.get('thinking_enabled'),
                ev.get('expect_reasoning'), ev.get('mode'), ev.get('item_count'), ev.get('success'), ev.get('duration_ms'),
                ev.get('content_length'), ev.get('line_count'), ev.get('sha256'), ev.get('content_preview'), ev.get('error')
            ])
        resp = make_response(output.getvalue())
        resp.headers['Content-Type'] = 'text/csv; charset=utf-8'
        resp.headers['Content-Disposition'] = f'attachment; filename="ai-usage-{days}d.csv"'
        return resp

    text = '\n'.join(json.dumps(ev, ensure_ascii=False) for ev in events) + ('\n' if events else '')
    resp = make_response(text)
    resp.headers['Content-Type'] = 'application/x-ndjson; charset=utf-8'
    resp.headers['Content-Disposition'] = f'attachment; filename="ai-usage-{days}d.jsonl"'
    return resp

@app.route('/auth/trusted', methods=['GET'])
def auth_list_trusted():
    """查看受信任设备列表（本机或系统管理员可操作）"""
    if not can_manage_system():
        return abort(403)
    
    trusted_devices = load_trusted_devices()
    
    # 格式化设备信息，隐藏完整ID
    formatted_devices = []
    for device_id, info in trusted_devices.items():
        credential = get_device_credential_by_id(get_security_config(), str(info.get('credential_id') or ''))
        auth_type = str(info.get('auth_type') or '').strip().lower()
        if not auth_type:
            auth_type = 'system' if parse_bool(info.get('system_admin'), False) or str(info.get('credential_id') or '').strip() == 'legacy-admin' else ('credential' if str(info.get('credential_id') or '').strip() else '')
        formatted_devices.append({
            'device_id': device_id[:8] + '...',
            'created_at': info.get('created_at', ''),
            'last_seen': info.get('last_seen', ''),
            'ip': info.get('ip', ''),
            'ua_hash': info.get('ua_hash', '')[:8] + '...',
            'credential_id': info.get('credential_id', ''),
            'remark': info.get('remark', '') or (credential or {}).get('remark', ''),
            'expires_at': info.get('expires_at', '') or (credential or {}).get('expires_at', ''),
            'used_count': info.get('used_count', 0),
            'max_uses': info.get('max_uses'),
            'auth_type': auth_type,
            'system_admin': bool(parse_bool(info.get('system_admin'), False) or auth_type == 'system'),
            'permissions': normalize_device_permissions(info.get('permissions')),
        })
    
    return jsonify({
        'status': 'success',
        'devices': formatted_devices,
        'total': len(formatted_devices)
    })

@app.route('/auth/revoke', methods=['POST'])
def auth_revoke_device():
    """吊销指定设备（本机或系统管理员可操作）"""
    if not can_manage_system():
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
    """吊销所有设备（本机或系统管理员可操作）"""
    if not can_manage_system():
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
    if not can_manage_system():
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
    if not can_manage_system():
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
            import threading
            threading.Thread(
                target=restart_on_port,
                args=(port, f'http://127.0.0.1:{port}'),
                daemon=True
            ).start()
            return jsonify({'status': 'success', 'port': port})
    return jsonify({'status': 'fail', 'message': '无法找到可用端口'}), 500

@app.route('/restore_port', methods=['POST'])
def api_restore_port():
    if not can_manage_system():
        return abort(403)
    locked_response = require_unlocked_device('恢复端口')
    if locked_response:
        return locked_response
    set_port_status('normal', 5000)
    print(f'[端口恢复] 已写入 port_status.json: mode=normal, port=5000')
    # 记录端口恢复审计日志
    app.logger.info(f"端口恢复 - 操作IP: {request.remote_addr}, 恢复到默认端口: 5000")
    import threading
    threading.Thread(
        target=restart_on_port,
        args=(5000, 'http://127.0.0.1:5000'),
        daemon=True
    ).start()
    return jsonify({'status': 'success'})

def restart_on_port(port, open_url=None):
    import time
    import subprocess
    import webbrowser
    time.sleep(1)  # Give the client time to receive the response.
    if open_url:
        try:
            webbrowser.open(open_url)
        except Exception:
            pass
    executable = sys.executable
    if getattr(sys, 'frozen', False):
        args = [executable, str(port)]
    else:
        args = [executable, __file__, str(port)]
    subprocess.Popen(args, close_fds=True)
    os._exit(0)

@app.route('/get_my_ip')
def get_my_ip():
    return jsonify({'remote_addr': request.remote_addr})

# ===== AMLL 实时流 API =====
@app.route('/amll/state')
def amll_state_api():
    """AMLL 状态快照 API"""
    host = request.host
    lines = _build_amll_lines_for_client(AMLL_STATE.get("raw_lines", []), request.args)
    return jsonify({
        "song": _normalize_song_for_host(AMLL_STATE["song"], host),
        "progress_ms": AMLL_STATE["progress_ms"],
        "lines": lines
    })

@app.route('/amll/stream')
def amll_stream_api():
    """AMLL 实时事件流 API (Server-Sent Events)"""
    split_opts = _parse_char_split_options(request.args)

    def _normalize_song_for_client(song_val: dict) -> dict:
        return _normalize_song_for_host(song_val or {}, request.host)

    def _normalize_state_snapshot(snapshot: dict) -> dict:
        raw_lines = snapshot.get("raw_lines")
        if raw_lines is None:
            raw_lines = AMLL_STATE.get("raw_lines", [])
        return {
            "song": _normalize_song_for_client(snapshot.get("song", {})),
            "progress_ms": snapshot.get("progress_ms", 0),
            "lines": _build_amll_lines_for_client(raw_lines, char_split_options=split_opts),
        }

    def _transform_event_payload(etype: str, data: dict) -> dict:
        if etype == "lyrics":
            raw_lines = data.get("raw_lines")
            if raw_lines is None:
                raw_lines = data.get("lines", [])
            return {
                "lines": _build_amll_lines_for_client(raw_lines, char_split_options=split_opts),
            }
        if etype == "state":
            return _normalize_state_snapshot(data)
        if etype == "song":
            return {"song": _normalize_song_for_client(data.get("song", {}))}
        return data

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
                payload = _transform_event_payload(etype, data)
                yield _sse(etype, payload)
            except queue.Empty:
                # 心跳：防止 Nginx/浏览器断流
                yield ": keep-alive\n\n"
    return StreamingResponse(_gen(), media_type="text/event-stream")

@app.route('/lyrics-amll')
def lyrics_amll_page():
    """AMLL 歌词展示页面"""
    amll_entry = get_amll_entry_assets()
    return render_template("Lyrics-style.HTML-AMLL-v1.HTML", amll_entry=amll_entry)

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
    lines = _build_amll_lines_for_client(
        AMLL_STATE.get("raw_lines", []) or [],
        {"char_split": "off"},
    )

    title = (snapshot_song.get("musicName") or "AMLL 未命名").strip()
    artists = [str(item).strip() for item in (snapshot_song.get("artists") or []) if str(item).strip()]
    album = (snapshot_song.get("album") or "").strip()
    duration_ms = int(snapshot_song.get("duration") or 0)

    # 生成基础文件名
    sanitized_artists = [sanitize_filename(artist) for artist in artists]
    artists_part = " _ ".join([artist for artist in sanitized_artists if artist]) or "AMLL"
    base_stem_raw = sanitize_filename(f"{title} - {artists_part}")
    if not base_stem_raw:
        base_stem_raw = sanitize_filename(title) or sanitize_filename(artists_part) or "AMLL_Song"
    json_filename = _ensure_unique_filename(STATIC_DIR, f"{base_stem_raw}.json")
    base_stem = os.path.splitext(json_filename)[0]

    try:
        lyrics_filename = None
        lyrics_url = "!"

        # 写入 LYS
        if lines:
            lyrics_filename = _ensure_unique_filename(SONGS_DIR, f"{base_stem}.lys")
            lyrics_path = SONGS_DIR / lyrics_filename
            lyrics_path.write_text(_amll_lines_to_lys(lines), encoding="utf-8-sig")
            lyrics_url = build_public_url('songs', lyrics_filename)

        # 写入翻译 LRC（可选）
        translation_filename = None
        translation_url = "!"
        if lines and use_translation and any(str(line.get("translatedLyric") or "").strip() for line in lines):
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

        lyrics_field = f"::{lyrics_url}::{translation_url}::!::" if lines else "::!::!::!::"

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

        upsert_song_search_index_for_path(json_path)

        return jsonify({
            'status': 'success',
            'jsonFile': json_filename,
            'lyricsFile': lyrics_filename,
            'translationFile': translation_filename,
            'coverFile': cover_filename,
            'coverUrl': cover_url,
            'message': '已从 AMLL 源创建新歌曲'
        })
    except Exception as exc:
        app.logger.error(f"AMLL 创建歌曲失败: {exc}", exc_info=True)
        return jsonify({'status': 'error', 'message': f'创建失败: {exc}'})

def _resolve_media_audio_delivery_mode() -> str:
    policy = resolve_effective_media_playback_policy()
    if policy.get('locked_by_policy'):
        resolved = policy.get('resolved_url_mode')
        if resolved in ('stream', 'oneshot'):
            return resolved

    if policy.get('mode') == 'both':
        requested = _parse_request_media_mode()
        if requested and requested in policy.get('available_modes', []):
            return requested
        return policy.get('default_mode', _default_media_audio_url_mode())

    resolved = policy.get('resolved_url_mode')
    if resolved in ('stream', 'oneshot'):
        return resolved
    return _default_media_audio_url_mode()


@app.route('/media/audio')
def media_audio():
    if not is_request_allowed():
        _append_media_audit({
            'event': 'media_audio_denied',
            'reason': 'request_not_allowed',
            'file': request.args.get('file'),
            'remote_addr': request.remote_addr,
        })
        return abort(403)

    relative_raw = (request.args.get('file') or '').strip()
    exp_raw = (request.args.get('exp') or '').strip()
    token = (request.args.get('token') or '').strip()

    if not relative_raw or not exp_raw or not token:
        _append_media_audit({
            'event': 'media_audio_denied',
            'reason': 'missing_params',
            'file': relative_raw,
            'remote_addr': request.remote_addr,
        })
        return jsonify({'error': 'Missing file, exp, or token'}), 400

    try:
        relative_path = _normalize_relative_path(unquote(relative_raw))
    except ValueError:
        _append_media_audit({
            'event': 'media_audio_denied',
            'reason': 'path_traversal',
            'file': relative_raw,
            'remote_addr': request.remote_addr,
        })
        return jsonify({'error': 'Invalid file path'}), 400

    try:
        exp = int(exp_raw)
    except ValueError:
        _append_media_audit({
            'event': 'media_audio_denied',
            'reason': 'bad_exp',
            'file': relative_path,
            'remote_addr': request.remote_addr,
        })
        return jsonify({'error': 'Invalid exp'}), 400

    if exp < int(time.time()):
        _append_media_audit({
            'event': 'media_audio_denied',
            'reason': 'token_expired',
            'file': relative_path,
            'remote_addr': request.remote_addr,
        })
        return jsonify({'error': 'Token expired'}), 401

    device_id = request.cookies.get('FEW_DEVICE_ID') or ''
    strict_binding = _strict_device_binding_active()

    if strict_binding and not device_id:
        _append_media_audit({
            'event': 'media_audio_denied',
            'reason': 'device_required',
            'file': relative_path,
            'remote_addr': request.remote_addr,
        })
        return jsonify({'error': 'device_required'}), 403

    if strict_binding:
        token_ok = verify_media_audio_token(relative_path, exp, token, device_id)
        if not token_ok:
            _append_media_audit({
                'event': 'media_audio_denied',
                'reason': 'device_mismatch',
                'file': relative_path,
                'remote_addr': request.remote_addr,
            })
            return jsonify({'error': 'Invalid token'}), 403
    else:
        token_ok = verify_media_audio_token(relative_path, exp, token, device_id)
        if not token_ok:
            token_ok = verify_media_audio_token(relative_path, exp, token, '')
        if not token_ok:
            _append_media_audit({
                'event': 'media_audio_denied',
                'reason': 'bad_sig',
                'file': relative_path,
                'remote_addr': request.remote_addr,
            })
            return jsonify({'error': 'Invalid token'}), 403

    if not _check_media_rate_limit(request.remote_addr or '', relative_path):
        _append_media_audit({
            'event': 'media_audio_denied',
            'reason': 'rate_limited',
            'file': relative_path,
            'remote_addr': request.remote_addr,
        })
        return jsonify({'error': 'Too many requests'}), 429

    try:
        audio_path = resolve_resource_path(f'/songs/{relative_path}', 'songs')
    except ValueError:
        _append_media_audit({
            'event': 'media_audio_denied',
            'reason': 'path_traversal',
            'file': relative_path,
            'remote_addr': request.remote_addr,
        })
        return jsonify({'error': 'Invalid file path'}), 400

    if not audio_path.is_file():
        _append_media_audit({
            'event': 'media_audio_denied',
            'reason': 'not_found',
            'file': relative_path,
            'remote_addr': request.remote_addr,
        })
        return jsonify({'error': 'File not found'}), 404

    delivery_mode = _resolve_media_audio_delivery_mode()
    mimetype = _guess_audio_mimetype(audio_path)
    cache_headers = {'Cache-Control': 'private, no-store'}
    has_range = bool(request.headers.get('Range'))

    if delivery_mode == 'oneshot':
        content = audio_path.read_bytes()
        _append_media_audit({
            'event': 'media_audio_served',
            'file': relative_path,
            'remote_addr': request.remote_addr,
            'status': 200,
            'has_range': has_range,
            'bytes': len(content),
            'mode': 'oneshot',
        })
        return StarletteResponse(
            content=content,
            status_code=200,
            media_type=mimetype,
            headers=cache_headers,
        )

    # stream: FileResponse serves the full file and honors Range natively (with or without Range header)
    response = FileResponse(audio_path, media_type=mimetype)
    response.headers.update(cache_headers)
    _append_media_audit({
        'event': 'media_audio_served',
        'file': relative_path,
        'remote_addr': request.remote_addr,
        'status': 206 if has_range else 200,
        'has_range': has_range,
        'mode': 'stream',
    })
    return response


@app.route('/media/config', methods=['GET'])
def get_media_config_route():
    if not _can_manage_media_config():
        return abort(403)
    payload = dict(get_media_config())
    payload['policy_notes'] = get_media_config_policy_notes()
    return jsonify(payload)


@app.route('/media/config', methods=['POST'])
def post_media_config_route():
    if not _can_manage_media_config():
        return abort(403)
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({'error': 'JSON body required'}), 400
    current = get_media_config()
    merged = dict(current)
    for key in DEFAULT_MEDIA_CONFIG:
        if key in payload:
            merged[key] = payload[key]
    saved = save_media_config(merged)
    return jsonify({
        'status': 'success',
        'config': {**saved, 'policy_notes': get_media_config_policy_notes()},
    })


@app.route('/media/audit/recent')
def media_audit_recent():
    if not _can_manage_media_config():
        return abort(403)
    limit = coerce_int(request.args.get('limit'), 50) or 50
    limit = max(1, min(limit, 200))
    entries = list(_MEDIA_AUDIT_LOG)[:limit]
    return jsonify({'status': 'success', 'entries': entries})


# Mount static directory at root to mirror Flask static_url_path=''
app.mount("/", StaticFiles(directory=STATIC_DIR), name="static")

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
    """将毫秒转换为秒，保留3位小数

    Args:
        ms: 毫秒数

    Returns:
        返回转换后的秒数
    """
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

def _load_cover_image_bytes(cover_url: Optional[str], cover_data_url: Optional[str]) -> Optional[bytes]:
    if cover_data_url:
        data_bytes, _ = _decode_data_url(cover_data_url)
        if data_bytes:
            return data_bytes
    if not cover_url:
        return None
    cover_url = str(cover_url).strip()
    if cover_url.startswith("data:"):
        data_bytes, _ = _decode_data_url(cover_url)
        if data_bytes:
            return data_bytes
    relative = _extract_single_song_relative(cover_url)
    if not relative:
        return None
    cover_path = SONGS_DIR / relative
    if not cover_path.exists():
        return None
    try:
        return cover_path.read_bytes()
    except Exception:
        return None

def _extract_palette_from_bytes(image_bytes: bytes, max_colors: int = 128) -> Optional[dict]:
    try:
        with Image.open(BytesIO(image_bytes)) as img:
            img = img.convert("RGB")
            img.thumbnail((128, 128))
            quantized = img.quantize(colors=max_colors, method=Image.MEDIANCUT)
            palette = quantized.getpalette() or []
            color_counts = quantized.getcolors(max_colors) or []
    except Exception:
        return None

    if not color_counts or not palette:
        return None

    extracted: list[tuple[int, str]] = []
    for count, index in color_counts:
        base = palette[index * 3:(index * 3) + 3]
        if len(base) != 3:
            continue
        r, g, b = base
        extracted.append((count, f"#{r:02x}{g:02x}{b:02x}"))

    if not extracted:
        return None

    extracted.sort(key=lambda item: item[0])
    counts = [item[0] for item in extracted]
    colors = [item[1] for item in extracted]
    return {"counts": counts, "colors": colors, "color_count": len(colors)}

def _is_video_file(filename: Optional[str]) -> bool:
    if not filename:
        return False
    
    # 处理URL情况
    if isinstance(filename, str):
        if filename.startswith('http://') or filename.startswith('https://'):
            # 移除查询参数和片段标识符
            path_part = filename.split('?')[0].split('#')[0]
            return Path(path_part.lower()).suffix in {'.mp4', '.webm', '.ogg', '.m4v', '.mov'}
    
    # 处理本地文件
    return Path(str(filename).lower()).suffix in {'.mp4', '.webm', '.ogg', '.m4v', '.mov'}

_COVER_PALETTE_CACHE_MAX = 128
_cover_palette_cache: dict = {}
_cover_palette_cache_order: list = []

def _cover_palette_cache_key(cover_url: Optional[str], cover_data_url: Optional[str]):
    if cover_data_url:
        digest = hashlib.sha256(str(cover_data_url).encode("utf-8", errors="replace")).hexdigest()
        return ("data_url", digest)
    if not cover_url:
        return None
    cover_url = str(cover_url).strip()
    if cover_url.startswith("data:"):
        digest = hashlib.sha256(cover_url.encode("utf-8", errors="replace")).hexdigest()
        return ("data_url", digest)
    relative = _extract_single_song_relative(cover_url)
    if not relative:
        return None
    cover_path = SONGS_DIR / relative
    if not cover_path.exists():
        return None
    try:
        stat = cover_path.stat()
        return ("file", str(cover_path.resolve()), stat.st_mtime_ns, stat.st_size)
    except Exception:
        return None

def _cover_palette_cache_get(key):
    return _cover_palette_cache.get(key)

def _cover_palette_cache_set(key, value: dict) -> None:
    if key in _cover_palette_cache:
        _cover_palette_cache[key] = value
        return
    while len(_cover_palette_cache_order) >= _COVER_PALETTE_CACHE_MAX:
        oldest = _cover_palette_cache_order.pop(0)
        _cover_palette_cache.pop(oldest, None)
    _cover_palette_cache_order.append(key)
    _cover_palette_cache[key] = value

def _build_cover_palette_payload(data: dict, meta: dict) -> dict:
    cover_data_url = ""
    if isinstance(data, dict):
        cover_data_url = data.get("cover_data_url") or ""
    if not cover_data_url and isinstance(meta, dict):
        cover_data_url = meta.get("cover_data_url") or ""
    cover_candidates = []
    if isinstance(meta, dict):
        # 优先使用 albumImgSrc 作为色盘提取源
        cover_candidates.append(meta.get("albumImgSrc"))
        if meta.get("dynamicCoverPoster"):
            cover_candidates.append(meta.get("dynamicCoverPoster"))
        cover_candidates.extend([meta.get("cover"), meta.get("coverUrl")])
    if isinstance(data, dict):
        cover_candidates.extend([data.get("cover"), data.get("coverUrl")])

    # 过滤掉视频文件，避免色盘提取失败
    cover_url = None
    for candidate in cover_candidates:
        if candidate and not _is_video_file(candidate):
            cover_url = candidate
            break

    cache_key = _cover_palette_cache_key(cover_url, cover_data_url)
    if cache_key is not None:
        cached = _cover_palette_cache_get(cache_key)
        if cached is not None:
            return cached

    image_bytes = _load_cover_image_bytes(cover_url, cover_data_url)
    if not image_bytes:
        result: dict = {}
    else:
        palette = _extract_palette_from_bytes(image_bytes)
        if not palette:
            result = {}
        else:
            result = {
                "colors": palette["colors"],
                "counts": palette["counts"],
                "color_count": palette["color_count"],
            }

    if cache_key is not None:
        _cover_palette_cache_set(cache_key, result)
    return result

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

    # 动态封面字段也需要 host 规范化
    for key in ("dynamicCoverSrc", "dynamicCoverPoster"):
        apply(meta, key)
        apply(normalized, key)

    normalized["meta"] = meta
    return normalized


def _amll_publish(evt_type: str, data: dict):
    """发布事件到AMLL前端"""
    # 更新全局快照
    if evt_type == "lyrics":
        if "raw_lines" in data:
            AMLL_STATE["raw_lines"] = data.get("raw_lines", [])
        elif "lines" in data:
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


def _parse_char_split_threshold_ms(request_args) -> int:
    """Parse char_split_threshold_ms; 0 is valid; default 2000 when absent/invalid."""
    th_raw = None
    if request_args is not None:
        getter = getattr(request_args, "get", None)
        if callable(getter):
            th_raw = getter("char_split_threshold_ms")
        elif isinstance(request_args, dict):
            th_raw = request_args.get("char_split_threshold_ms")
    if th_raw is None or str(th_raw).strip() == "":
        return 2000
    try:
        return max(0, int(th_raw))
    except (TypeError, ValueError):
        return 2000


def _parse_char_split_options(request_args) -> dict:
    """Parse char_split query: off (default) or on, with optional threshold_ms."""
    raw = None
    if request_args is not None:
        getter = getattr(request_args, "get", None)
        if callable(getter):
            raw = getter("char_split")
        elif isinstance(request_args, dict):
            raw = request_args.get("char_split")
    threshold_ms = _parse_char_split_threshold_ms(request_args)
    if raw is None or str(raw).strip() == "":
        return {"mode": "off", "threshold_ms": threshold_ms}
    val = str(raw).strip().lower()
    if val == "off":
        return {"mode": "off", "threshold_ms": threshold_ms}
    if val == "on":
        return {"mode": "on", "threshold_ms": threshold_ms}
    return {"mode": "off", "threshold_ms": threshold_ms}


def split_word_for_frontend(word_obj, *, mode: str, threshold_ms: int) -> list[dict]:
    """Split a word for AMLL frontend display; off=whole word, on=split when duration > threshold."""
    w = str(word_obj.get("word", "") or "")
    if not w:
        return []

    s = int(word_obj.get("startTime") or word_obj.get("start_ms") or 0)
    e = int(word_obj.get("endTime") or word_obj.get("end_ms") or s)
    rw = str(word_obj.get("romanWord") or word_obj.get("roman_word") or "")
    duration = max(0, e - s)

    def _whole_word():
        return [{
            "char": w,
            "roman_char": rw,
            "start_ms": s,
            "end_ms": e,
        }]

    if mode == "off" or len(w) == 1:
        return _whole_word()

    if duration > threshold_ms:
        return split_word_to_chars(word_obj)
    return _whole_word()


def _build_amll_lines_for_client(raw_lines, request_args=None, *, char_split_options=None) -> list:
    opts = char_split_options if char_split_options is not None else _parse_char_split_options(request_args)
    lines = _amll_lines_to_front(raw_lines or [], opts)
    try:
        compute_disappear_times(lines, delta1=500, delta2=0)
    except Exception as e:
        app.logger.warning(f"[AMLL] compute_disappear_times failed: {e}")
    return lines


def _amll_lines_to_front(payload_lines: list[dict], char_split_options: dict | None = None) -> list[dict]:
    """
    把 AMLL 的 lines（每行包含 words[]）转换为前端统一的结构：
      每行 -> { syllables: [ {text,startTime,duration,roman?}, ... ] }
    """
    if char_split_options is None:
        char_split_options = {"mode": "off", "threshold_ms": 2000}
    mode = char_split_options.get("mode", "off")
    if mode not in ("off", "on"):
        mode = "off"
    th_val = char_split_options.get("threshold_ms")
    if th_val is None:
        threshold_ms = 2000
    else:
        try:
            threshold_ms = max(0, int(th_val))
        except (TypeError, ValueError):
            threshold_ms = 2000
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

            for ev in split_word_for_frontend(wobj, mode=mode, threshold_ms=threshold_ms):
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

LDDC_API_BASE = "https://vercel-lddc-api-python-eight.vercel.app"
_LDDC_TS_RE = re.compile(r"\[(\d{1,2}):(\d{2})(?:\.(\d{1,3}))?\]")

def _parse_lrc_timestamp(value: str) -> Optional[int]:
    """解析 LRC 时间戳为毫秒。"""
    match = _LDDC_TS_RE.match(value)
    if not match:
        return None
    minutes = int(match.group(1))
    seconds = int(match.group(2))
    millis_str = (match.group(3) or "0").ljust(3, "0")[:3]
    millis = int(millis_str)
    return (minutes * 60 + seconds) * 1000 + millis

def _format_lrc_timestamp(ms: int) -> str:
    minutes, remainder = divmod(int(ms), 60000)
    seconds, millis = divmod(remainder, 1000)
    return f"{minutes:02d}:{seconds:02d}.{millis:03d}"

def _lrc_syllable_line_to_lys(raw_line: str) -> Optional[str]:
    matches = list(_LDDC_TS_RE.finditer(raw_line))
    if len(matches) < 2:
        return None
    timestamps = []
    segments = []
    for idx, match in enumerate(matches):
        ts = _parse_lrc_timestamp(match.group(0))
        if ts is None:
            continue
        timestamps.append(ts)
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(raw_line)
        segments.append(raw_line[start:end])
    parts = []
    for idx, start_ms in enumerate(timestamps):
        if idx >= len(segments):
            continue
        text = segments[idx]
        if text == "":
            continue
        next_ms = timestamps[idx + 1] if idx + 1 < len(timestamps) else None
        duration = max(0, (next_ms - start_ms) if next_ms is not None else 0)
        parts.append(f"{text}({start_ms},{duration})")
    if not parts:
        return None
    return f"[0]{''.join(parts)}"

def _split_lddc_lrc(raw_lrc: str) -> Tuple[str, str]:
    """将 LDDC 返回的 LRC 拆为 LYS 与翻译 LRC。"""
    lyrics_lines: List[str] = []
    translation_lines: List[str] = []
    last_lyric_start: Optional[int] = None

    for raw_line in raw_lrc.splitlines():
        if not raw_line.strip():
            continue
        matches = list(_LDDC_TS_RE.finditer(raw_line))
        if not matches:
            continue
        timestamps = []
        segments = []
        for idx, match in enumerate(matches):
            ts = _parse_lrc_timestamp(match.group(0))
            if ts is None:
                continue
            timestamps.append(ts)
            start = match.end()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(raw_line)
            segments.append(raw_line[start:end])
        if not timestamps:
            continue
        if len(timestamps) > 1:
            if len(timestamps) == 2 and segments and segments[0].strip() and (len(segments) < 2 or not segments[1].strip()):
                translation_lines.append(f"[{_format_lrc_timestamp(timestamps[0])}]{segments[0].strip()}")
                continue
            lys_line = _lrc_syllable_line_to_lys(raw_line)
            if lys_line:
                lyrics_lines.append(lys_line)
                last_lyric_start = timestamps[0]
            continue
        text = segments[0].strip() if segments else ""
        if not text:
            continue
        if last_lyric_start is not None and timestamps[0] == last_lyric_start:
            translation_lines.append(f"[{_format_lrc_timestamp(timestamps[0])}]{text}")
        else:
            lyrics_lines.append(f"[0]{text}({timestamps[0]},0)")
            last_lyric_start = timestamps[0]

    return "\n".join(lyrics_lines), "\n".join(translation_lines)

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
    magic=0 -> OnAudioData（音频数据）
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

# === 音频数据处理（用于律动背景） ===
_audio_data_buffer = []  # 存储最近的音频数据
_audio_buffer_max_size = 8000  # 缓冲区最大大小（约8秒的音频数据，假设采样率44100Hz）
_audio_threshold_update_timer = None  # 阈值更新定时器
_audio_thresholds = [0.1] * 16  # 默认16个频带的阈值
_audio_last_threshold_update = 0  # 上次更新阈值的时间

def _decode_audio_samples(audio_bytes: bytes):
    """
    Decode audio bytes into float samples in [-1, 1].
    Prefer f32 PCM when values look valid, otherwise fall back to i16 PCM.
    """
    if not audio_bytes:
        return [], "empty"

    import array

    f32_count = len(audio_bytes) // 4
    if f32_count > 0:
        raw_f32 = array.array('f', audio_bytes[:f32_count * 4])
        valid = True
        checked = 0
        for value in raw_f32[: min(64, len(raw_f32))]:
            if value != value or abs(value) > 2.5:
                valid = False
                break
            checked += 1
        if valid and checked:
            return list(raw_f32), "f32"

    i16_count = len(audio_bytes) // 2
    if i16_count > 0:
        raw_i16 = array.array('h', audio_bytes[:i16_count * 2])
        return [float(value) / 32768.0 for value in raw_i16], "i16"

    return [], "unknown"

def _process_audio_data(audio_bytes: bytes):
    """
    处理音频数据，用于律动背景
    音频数据格式：f32 PCM（小端序）
    """
    global _audio_data_buffer, _audio_threshold_update_timer, _audio_thresholds, _audio_last_threshold_update

    if not audio_bytes:
        return

    try:
        samples, sample_format = _decode_audio_samples(audio_bytes)
        if not samples:
            return

        if app.logger.isEnabledFor(logging.DEBUG):
            app.logger.debug(
                f"[Audio] 收到音频数据: {len(audio_bytes)} bytes, {len(samples)} samples, format={sample_format}"
            )

        _audio_data_buffer.extend(samples)

        # 限制缓冲区大小
        if len(_audio_data_buffer) > _audio_buffer_max_size:
            _audio_data_buffer = _audio_data_buffer[-_audio_buffer_max_size:]

        # 每8秒更新一次阈值
        current_time = time.time()
        if current_time - _audio_last_threshold_update >= 8.0:
            _update_audio_thresholds()
            _audio_last_threshold_update = current_time

        # 推送音频数据给前端（通过 SSE）
        _broadcast_audio_data(samples)

    except Exception as e:
        app.logger.warning(f"[Audio] 处理音频数据失败: {e}")

def _update_audio_thresholds():
    """
    每8秒更新一次音频阈值
    """
    global _audio_thresholds

    if len(_audio_data_buffer) < 1024:
        return

    try:
        import numpy as np

        # 转换为 numpy 数组
        samples = np.array(_audio_data_buffer[-len(_audio_data_buffer):])

        # 计算 FFT
        fft_result = np.fft.rfft(samples)
        fft_magnitude = np.abs(fft_result)

        # 将 FFT 结果分为 16 个频带
        num_bands = 16
        band_size = len(fft_magnitude) // num_bands
        new_thresholds = []

        for i in range(num_bands):
            start = i * band_size
            end = start + band_size
            band_data = fft_magnitude[start:end]

            if len(band_data) > 0:
                # 计算该频带的平均值作为阈值
                threshold = float(np.mean(band_data))
                # 归一化到 0-1 范围（使用对数刻度）
                normalized = np.log10(threshold + 1) / np.log10(1000 + 1)
                # 确保 normalized 不是 NaN
                if normalized != normalized:  # NaN 检查
                    normalized = 0.1
                new_thresholds.append(float(np.clip(normalized, 0.05, 0.5)))
            else:
                new_thresholds.append(0.1)

        _audio_thresholds = new_thresholds
        app.logger.info(f"[Audio] 更新阈值: {[f'{t:.3f}' for t in _audio_thresholds[:4]]}...")

    except ImportError:
        # 如果没有 numpy，使用简单的峰值检测
        samples = _audio_data_buffer[-len(_audio_data_buffer):]
        num_bands = 16
        band_size = len(samples) // num_bands
        new_thresholds = []

        for i in range(num_bands):
            start = i * band_size
            end = start + band_size
            band_data = samples[start:end]

            if len(band_data) > 0:
                # 计算绝对值的平均值
                threshold = sum(abs(s) for s in band_data) / len(band_data)
                # 归一化
                normalized = min(threshold / 0.5, 0.5)
                # 确保 normalized 不是 NaN
                if normalized != normalized:  # NaN 检查
                    normalized = 0.1
                new_thresholds.append(max(normalized, 0.05))
            else:
                new_thresholds.append(0.1)

        _audio_thresholds = new_thresholds
        app.logger.info(f"[Audio] 更新阈值（简单模式）: {[f'{t:.3f}' for t in _audio_thresholds[:4]]}...")

def _broadcast_audio_data(samples):
    """
    将音频数据推送给前端（通过 SSE）
    """
    try:
        # 计算频带能量
        band_levels = _calculate_band_levels(samples)

        # 确保所有数值都是有效的（过滤 NaN 和 Infinity）
        valid_levels = [float(x) if isinstance(x, (int, float)) and not (x != x) else 0.0 for x in band_levels]
        valid_thresholds = [float(x) if isinstance(x, (int, float)) and not (x != x) else 0.1 for x in _audio_thresholds]

        if app.logger.isEnabledFor(logging.DEBUG):
            app.logger.debug(
                f"[Audio] 推送音频数据: samples={len(samples)}, levels={[f'{l:.3f}' for l in valid_levels[:4]]}..."
            )

        # 通过 SSE 推送给前端
        _amll_publish("audio_levels", {
            "levels": valid_levels,
            "thresholds": valid_thresholds,
            "timestamp": time.time() * 1000
        })

    except Exception as e:
        app.logger.warning(f"[Audio] 推送音频数据失败: {e}")

def _calculate_band_levels(samples):
    """
    计算音频数据的频带能量
    返回 16 个频带的能量值（0-1 范围）
    """
    try:
        import numpy as np

        # 转换为 numpy 数组
        audio_array = np.array(samples)

        # 计算 FFT
        fft_result = np.fft.rfft(audio_array)
        fft_magnitude = np.abs(fft_result)

        # 分为 16 个频带
        num_bands = 16
        band_size = len(fft_magnitude) // num_bands
        band_levels = []

        for i in range(num_bands):
            start = i * band_size
            end = start + band_size
            band_data = fft_magnitude[start:end]

            if len(band_data) > 0:
                # 计算该频带的能量
                energy = float(np.mean(band_data))
                # 归一化
                normalized = np.log10(energy + 1) / np.log10(1000 + 1)
                band_levels.append(float(np.clip(normalized, 0, 1)))
            else:
                band_levels.append(0.0)

        return band_levels

    except ImportError:
        # 如果没有 numpy，使用简单的能量计算
        num_bands = 16
        band_size = len(samples) // num_bands
        band_levels = []

        for i in range(num_bands):
            start = i * band_size
            end = start + band_size
            band_data = samples[start:end]

            if len(band_data) > 0:
                # 计算绝对值的平均值
                energy = sum(abs(s) for s in band_data) / len(band_data)
                # 归一化
                normalized = min(energy / 0.5, 1.0)
                band_levels.append(float(min(normalized, 1.0)))
            else:
                band_levels.append(0.0)

        return band_levels

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
            # 二进制帧：可能是 TTML、封面或音频数据
            if isinstance(raw, (bytes, bytearray)):
                b = bytes(raw)
                print(f"[WS] 收到二进制帧: {len(b)} bytes")
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

                # 处理音频数据（magic=0）
                try:
                    magic_val, size = struct.unpack("<HI", b[:6])
                    print(f"[WS] 二进制帧 magic={magic_val}, size={size}, payload_len={len(payload) if payload else 0}")
                    if magic_val == 0:  # OnAudioData
                        # 音频数据：解析并推送给前端用于律动背景
                        print(f"[WS] 检测到音频数据帧，开始处理")
                        _process_audio_data(payload)
                        continue
                except Exception as e:
                    print(f"[WS] 解析二进制帧失败: {e}")
                    pass

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
                    # 只保存非音频数据的二进制帧
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

                    print(f"[WS] 收到歌词(state) {len(payload)} 行（raw_lines 发布）")
                    _amll_publish("lyrics", {"raw_lines": payload})
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

                print(f"[WS] 收到歌词 {len(payload)} 行（raw_lines 发布）")
                _amll_publish("lyrics", {"raw_lines": payload})
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
    # Avoid double start when a reloader is enabled
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug:
        t = threading.Thread(target=_run_ws_loop, name="WS-Server", daemon=True)
        t.start()
        return t

if __name__ == '__main__':
    """主函数入口

    处理命令行参数，启动WebSocket服务器和FastAPI应用。
    支持指定端口，如果默认端口被占用会自动切换到随机端口。
    """
    import random
    def try_run(port):
        """尝试在指定端口启动应用

        Args:
            port: 要尝试的端口号

        Returns:
            启动成功返回True，失败返回False
        """
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

            if getattr(sys, 'frozen', False):
                launch_updater_sidecar(port)
            else:
                app.logger.info('Source mode startup: skip updater sidecar launch.')

            use_waitress = os.environ.get('USE_WAITRESS', '0') == '1'
            if use_waitress:
                app.logger.info("USE_WAITRESS=1 detected; FastAPI will use uvicorn instead of waitress.")
            import uvicorn
            uvicorn.run(
                app,
                host='0.0.0.0',
                port=port,
                log_level=os.getenv('UVICORN_LOG_LEVEL', 'info'),
                reload=False,
                workers=int(os.getenv('UVICORN_WORKERS', '1')),
                use_colors=False
            )
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

    # Start WS first, then FastAPI
    ws_thread = start_ws_server_once()

    if not try_run(port):
        # 5000端口失败，换随机端口
        for _ in range(10):
            random_port = random.randint(1025, 65535)
            if try_run(random_port):
                break
