# LyricSphere

LyricSphere 是一个基于 Flask 的 Web 应用程序，用于管理和显示动态歌词，并支持实时同步播放。该应用支持多种歌词格式，包括 .lrc、.lys 和 .ttml，并提供了编辑、转换和显示歌词的功能。

## 主要功能

- 支持多种歌词格式：.lrc（行级时间）、.lys（音节级时间）和 .ttml
- 实时歌词显示与动画效果
- 歌词编辑和格式转换功能
- 文件版本管理和自动备份（最多保留7个版本）
- 设备认证和密码保护安全机制
- 与 AMLL（Advanced Music Live Lyrics）集成的 WebSocket 服务器
- Server-Sent Events (SSE) 实现实时歌词更新

## 技术栈

- 后端：Python、Flask
- 前端：HTML、CSS、JavaScript
- 服务器：Waitress（生产环境）
- 安全：bcrypt 哈希加密

## 安装与运行

### 环境要求

- Python 3.6+

### 安装依赖

```bash
pip install flask openai bcrypt waitress
```

### 运行应用

```bash
python backend.py
```

或指定端口运行：

```bash
python backend.py 5000
```

在生产环境中，应用使用 Waitress 作为服务器（当 USE_WAITRESS 环境变量设置为 1 时）。

## 目录结构

```
LyricSphere/
├── backend.py          # 主 Flask 应用
├── README.md           # 项目说明文档
├── CLAUDE.md           # Claude Code 指导文档
├── templates/          # HTML 模板
├── static/             # 静态文件
│   ├── songs/          # 歌曲文件、歌词和相关媒体
│   └── backups/        # 备份文件
├── logs/               # 应用日志（自动创建）
└── exports/            # 导出文件（自动创建）
```

## 支持的歌词格式

### .lys 格式
自定义的音节级定时歌词格式，提供更精细的歌词同步控制。

### .lrc 格式
标准的行级定时歌词格式，广泛用于音乐播放器。

### .ttml 格式
基于 XML 的定时文本格式，具有转换功能。

## 实时集成

应用包含一个 WebSocket 服务器（端口 11444），用于与 AMLL 集成，并支持 Server-Sent Events 实现实时歌词显示更新。

## 许可证

本项目采用半开放协议发布：

- 保留著作权所有权利
- 允许自由使用、修改和分发
- 使用时请注明出处

MIT License

Copyright (c) 2025 LyricSphere. All rights reserved.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
