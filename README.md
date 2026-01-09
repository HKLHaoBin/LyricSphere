# LyricSphere 

LyricSphere 是一个基于 Flask 的 Web 应用，专注于动态歌词的管理、编辑与展示，并支持实时同步播放。项目兼容 .lrc、.lys、.ttml 等多种格式，覆盖从编辑、转换到播放的完整流程。

致谢：
- https://github.com/BingoKingo/amll-web/tree/amlw-dev
- 本项目的 AMLL-web 功能与部分模板文件使用了该仓库的 AMLL 背景能力。
- https://github.com/Steve-xmh/applemusic-like-lyrics
- 本项目使用了该仓库提供的 Apple Music 风格歌词显示能力，实现了类似 iPad 版 Apple Music 的精美歌词动画效果。
- https://github.com/my-name-is-not-available/vercel-LDDC-api-python
- 本项目使用了该仓库提供的 LDDC（Lyrics Display and Download Center）API 能力，支持歌词显示和下载功能。
- https://deepwiki.com/HKLHaoBin/LyricSphere
- 本项目的文档在 DeepWiki 上维护，欢迎访问了解更多信息。

## 主要功能

- 支持多种歌词格式：.lrc（行级时间）、.lys（音节级时间）、.ttml 和 LQE 格式
- 实时歌词显示与动画效果，支持前后端动画配置同步与歌词消失动画
- **歌词音节分组功能**：实现音节分组，优化歌词行渲染时的布局表现，支持word-break和white-space的动态控制
- **资源URL规范化处理**：实现安全的路径解析和资源URL规范化，支持动态解析不同格式的路径，增强安全性检查
- **翻译状态可视化**：翻译流程提供阶段状态、进度追踪与多态提示动画，并在检测到问题时高亮对应歌词行
- **字体元标签与逐音节字体渲染**：解析 `[font-family:...]` 元标签并按中/英/日脚本自动选择字体，支持本地文件、Google Fonts 与 CDN 多源字体加载、可用性检测，以及针对特殊字体的纯色显示与逐音节动画优化
- **ZIP文件导入导出功能**：支持批量导入/导出歌词JSON文件和资源文件，提供一键打包歌曲和相关资源的分享功能
- **资源完整性检查**：在导出时对缺失资源生成警告，确保分享内容的完整性
- 歌词编辑和格式转换功能
- 文件版本管理和自动备份（最多保留7个版本，超长文件名自动哈希截断）
- 增强的日志处理功能，支持控制台实时输出以便监控处理过程
- 设备认证和密码保护安全机制
- 改进的安全路径处理，包含路径验证与越界检查
- CORS处理支持，为跨域请求提供更好的兼容性
- 与 AMLL（Advanced Music Live Lyrics）集成的 WebSocket 服务器
- AMLL Web 播放器集成，支持自定义基础URL配置与丰富的播放控制
- Server-Sent Events (SSE) 实现实时歌词更新
- LYS/LRC 与 TTML 格式双向转换功能
- AMLL 规则编写支持
- **TTML 内容安全处理**：实现 TTML 内容净化功能，过滤非必要的 XML 元素以提高安全性
- **从 AMLL 源创建歌曲**：支持直接从 AMLL 源的歌曲名、歌手、封面和歌词生成新条目
- **AMLL 源界面集成**：前端新增 AMLL 源卡片界面，可直接使用 AMLL 源数据创建歌曲
- AI 驱动的歌词翻译功能（支持多个AI提供商）
- **翻译提示词复制功能**：支持复制 AI 翻译使用的完整提示词
- 翻译设置提供兼容模式开关，可将系统提示词合并到用户消息以适配不支持多角色的模型
- 歌词导出为 CSV 格式功能
- 支持背景人声和对唱歌词的处理
- 歌词括号预处理功能，可通过strip_brackets配置选项控制是否移除歌词中的括号内容，采用高性能字符串翻译表替代正则表达式优化处理性能，并添加多余空格清理逻辑以提升输出质量

## 使用示例

### 字体元标签（逐音节字体选择）

在歌词文本中插入 `[font-family:...]` 元标签即可控制后续歌词的字体，按脚本自动匹配中/英/日等字体：

```
[font-family:Hymmnos]               # 全局默认改为 Hymmnos
[font-family:Hymmnos(en),(ja)]      # 英文用 Hymmnos，日文回退默认
[font-family:Main(en),Sub(ja),Extra]# 英文用 Main，日文用 Sub，其余用 Extra
[font-family:]                      # 清空字体，恢复默认
```

模板会逐音节检测脚本类型，支持本地文件、Google Fonts 与 CDN 加载；特殊字体会自动使用纯色模式并优化逐音节动画。

## 技术栈

- 后端：Python、Flask
- 前端：HTML、CSS、JavaScript
- 服务器：Waitress（生产环境）
- 安全：bcrypt 哈希加密
- AI：支持多个AI提供商（DeepSeek、OpenAI、OpenRouter、Together、Groq）

## 安装与运行

### 环境要求

- Python 3.6+

### 安装依赖

```bash
pip install flask openai bcrypt waitress websockets
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
自定义的音节级定时歌词格式，提供更精细的歌词同步控制，支持背景人声标记([6][7][8])和对唱标记([2][5])。

### .lrc 格式
标准的行级定时歌词格式，广泛用于音乐播放器，支持背景人声和对唱标记。

### .ttml 格式
基于 XML 的定时文本格式，具有转换功能，支持背景人声(ttm:role="x-bg")和对唱(ttm:agent="v2")。

### LQE 格式
结合歌词和翻译的复合格式，用于特定应用场景。

## 歌词格式转换

应用支持多种歌词格式之间的双向转换：
- **TTML 转 LYS/LRC**：将 TTML 文件转换为 LYS 或 LRC 格式，保留背景人声和对唱信息
- **LYS/LRC 转 TTML**：将 LYS 或 LRC 文件转换为 Apple 风格的 TTML 格式，支持翻译合并
- **LYS/LRC 转 LQE**：将歌词和翻译合并为 LQE 格式

## 实时集成

应用包含一个 WebSocket 服务器（端口 11444），用于与 AMLL 集成，并支持 Server-Sent Events 实现实时歌词显示更新。同时支持歌词动画效果，包括智能计算的歌词消失时间。

`/player/animation-config` 接口用于在页面加载时同步前端上报的动画参数，默认把进入/移动/退出动画时长统一到 600ms，并提供 `useComputedDisappear` 开关以控制是否启用后端计算的消失时机，便于在调试与演示之间切换。

系统还实现了资源URL的规范化处理，支持安全和高效的资源处理，为不同类型的资源提供动态路径解析。

新增 AMLL Web 播放器集成，提供现代化的 Web 界面，支持自定义基础URL配置，并包含丰富的播放控制功能，如播放速度调整、音量控制、歌词延迟设置、封面样式定制等。

## AI 歌词翻译

应用支持使用 AI 进行歌词翻译：
- 集成多个AI提供商（DeepSeek、OpenAI、OpenRouter、Together、Groq）实现高质量翻译
- 支持自定义系统提示词控制翻译风格
- 流式响应实现实时翻译显示
- 自动对齐时间戳与原始歌词
- 支持API连接测试功能
- 支持AI模型的思维链内容
- 支持歌词分析思考模型，在翻译前对歌词进行深度理解以提升翻译质量
- 自动检测并提示时间戳异常，结合原始歌词定位问题行
- 翻译流程提供阶段进度与多状态提示动画，同时在界面上高亮问题行并支持无时间戳歌词的翻译
- AI 探活接口兼容返回 404 的服务，实现对不支持 `/v1/models` 端点的 API 的健康检查
- 支持歌词括号预处理功能，可通过strip_brackets配置选项控制是否移除歌词中的括号内容，采用高性能字符串翻译表替代正则表达式优化处理性能，并添加多余空格清理逻辑以提升输出质量

## 安全特性

应用包含完善的安全机制：
- 基于设备的认证系统，支持受信任设备管理
- bcrypt 加密的密码保护
- 本地访问限制，敏感操作仅允许本地执行
- 安全配置可开关，便于不同环境部署
- **资源路径验证**：安全路径解析和验证，防止路径遍历漏洞
- **URL标准化**：URL标准化处理工具，安全处理各种资源路径
- **CORS支持**：实现跨域资源共享控制，支持与前端集成

## 许可证

本项目采用 **GNU General Public License v3.0** (GPL-3.0) 进行授权。任何人都可以在遵守 GPL-3.0 的前提下使用、修改与分发本项目的源码或衍生作品。

完整许可证文本请见 `LICENSE`。
