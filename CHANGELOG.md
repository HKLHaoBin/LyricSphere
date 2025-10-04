# 更新日志 (Changelog)

## [v1.2.0] - 2025-10-03

### 新增功能 (Features)

- **歌词格式转换功能**：
  - 添加了 LYS/LRC 格式到 TTML 格式的双向转换功能
  - 支持将 TTML 文件转换为 LYS 或 LRC 格式
  - 支持将 LYS 或 LRC 文件转换为 Apple 风格的 TTML 格式

- **AMLL 规则编写支持**：
  - 新增 AMLL-web 按钮，支持直接跳转到 AMLL 规则编写页面
  - 添加临时转换功能 (`/convert_to_ttml_temp`)，用于在 AMLL 规则编写时将 LYS/LRC 文件临时转换为 TTML 格式

- **API 端点**：
  - `/convert_to_ttml`: 将 LYS/LRC 文件转换为 TTML 格式的 API 端点
  - `/convert_to_ttml_temp`: 用于 AMLL 规则编写的临时 TTML 转换 API 端点

### 改进 (Enhancements)

- 优化了 TTML 生成逻辑，更好地支持 Apple 风格的 TTML 格式
- 改进了翻译处理逻辑，确保行号对齐
- 修复了背景歌词处理问题

### 文档更新 (Documentation)

- 更新了 README.md 和 CLAUDE.md，添加了关于歌词格式转换功能的说明
- 增加了对 AMLL 规则编写支持的文档说明
- 详细描述了新添加的 API 端点和转换方向

## [v1.1.0] - 2025-09-25

### 新增功能 (Features)

- 实现了基本的歌词管理功能
- 支持 .lrc、.lys 和 .ttml 格式的歌词文件
- 实现了实时歌词显示与动画效果
- 添加了文件版本管理和自动备份功能（最多保留7个版本）
- 集成了设备认证和密码保护安全机制
- 实现了与 AMLL（Advanced Music Live Lyrics）集成的 WebSocket 服务器
- 添加了 Server-Sent Events (SSE) 实现实时歌词更新

[历史版本信息省略...]