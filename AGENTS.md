# Repository Guidelines

## Latest Updates (v1.5.2)
- AI 翻译设置新增“兼容模式”开关；当目标模型仅接受单角色输入时，将系统提示词折叠进用户消息。
- AI 翻译模块现支持配置多个提供商（OpenAI、OpenRouter 等），确保在 `backend.py` 中同步更新凭据与 `base_url`。
- 歌词动画模板已重写 FLIP 流程，使用 WeakMap 跟踪状态；修改前请先阅读 `templates/Lyrics-style*.HTML` 中的新注释。

## Project Structure & Module Organization
- `backend.py` runs the Flask app, AMLL bridges, AI translation helpers, and all `BASE_PATH` filesystem wiring.
- `templates/` holds Jinja2 pages; align new views with existing lyric layouts before registering routes.
- `static/songs/` and `static/backups/` store lyric assets and rotation-managed history—group files per track and avoid manual edits in backups.
- `exports/` and `logs/` are runtime directories (`upload.log`, generated exports) that may be cleared locally when debugging.
- Root docs (`README.md`, `CHANGELOG.md`, `CLAUDE.md`) should stay current with workflow changes.

## Build, Test, and Development Commands
- `python -m venv .venv && source .venv/bin/activate` sets up an isolated environment.
- `pip install flask openai bcrypt waitress websockets` pulls the runtime dependencies imported in `backend.py`.
- `python backend.py [port]` starts the server (defaults to 5000).
- `DEBUG_LOGGING=1 python backend.py` surfaces verbose logs; set `USE_WAITRESS=1` when exercising the packaged server.

## Coding Style & Naming Conventions
- Target Python 3.6+ with 4-space indentation, `snake_case` functions, `CamelCase` classes, and `UPPER_SNAKE_CASE` globals.
- Reuse the `Path` helpers for filesystem access to retain frozen-build compatibility.
- Keep comments concise and bilingual when needed; match the tone used around lyric parsing routines.
- Place CSS, JS, and lyric assets under `static/` with descriptive filenames (e.g., `artist_title.lyrics.json`).

## Testing Guidelines
- No automated suite ships today, so run manual smoke checks before pushing changes.
- Run `python backend.py` and exercise the lyric dashboard at `http://localhost:5000`.
- Verify SSE health with `curl -N http://localhost:5000/amll/stream | head` and confirm AI translation flows finish cleanly.
- Check `logs/upload.log` for warnings and make sure new lyrics produce versioned files in `static/backups/`.

## Commit & Pull Request Guidelines
- Follow the Conventional Commit pattern already in history (`feat(scope):`, `docs:`), mixing English or Chinese scopes when helpful.
- Keep commits focused, update `CHANGELOG.md` for user-facing changes, and adjust `README.md` if workflows shift.
- Pull requests should outline motivation, list validation steps, and note any new environment variables or ports.
- Include screenshots or short clips for UI updates and link related issues or TODOs to preserve traceability.

## Security & Configuration Tips
- Store API credentials in environment variables or deployment secrets, not in source files or `封装配置.json`.
- Scrub sensitive tokens from `upload.log` before committing and rotate keys used for demos.
- When packaging, confirm `BASE_PATH` works in frozen builds and backups remain inside the project tree.

# Repository Guidelines

## Project Structure & Module Organization
- `backend.py` runs the Flask app, AMLL bridges, AI translation helpers, and all `BASE_PATH` filesystem wiring.
- `templates/` holds Jinja2 pages; align new views with existing lyric layouts before registering routes.
- `static/songs/` and `static/backups/` store lyric assets and rotation-managed history—group files per track and avoid manual edits in backups.
- `exports/` and `logs/` are runtime directories (`upload.log`, generated exports) that may be cleared locally when debugging.
- Root docs (`README.md`, `CHANGELOG.md`, `CLAUDE.md`) should stay current with workflow changes.

## Build, Test, and Development Commands
- `python -m venv .venv && source .venv/bin/activate` sets up an isolated environment.
- `pip install flask openai bcrypt waitress websockets` pulls the runtime dependencies imported in `backend.py`.
- `python backend.py [port]` starts the server (defaults to 5000).
- `DEBUG_LOGGING=1 python backend.py` surfaces verbose logs; set `USE_WAITRESS=1` when exercising the packaged server.

## Coding Style & Naming Conventions
- Target Python 3.6+ with 4-space indentation, `snake_case` functions, `CamelCase` classes, and `UPPER_SNAKE_CASE` globals.
- Reuse the `Path` helpers for filesystem access to retain frozen-build compatibility.
- Keep comments concise and bilingual when needed; match the tone used around lyric parsing routines.
- Place CSS, JS, and lyric assets under `static/` with descriptive filenames (e.g., `artist_title.lyrics.json`).

## Testing Guidelines
- No automated suite ships today, so run manual smoke checks before pushing changes.
- Run `python backend.py` and exercise the lyric dashboard at `http://localhost:5000`.
- Verify SSE health with `curl -N http://localhost:5000/amll/stream | head` and confirm AI translation flows finish cleanly.
- Check `logs/upload.log` for warnings and make sure new lyrics produce versioned files in `static/backups/`.

## Commit & Pull Request Guidelines
- Follow the Conventional Commit pattern already in history (`feat(scope):`, `docs:`), mixing English or Chinese scopes when helpful.
- Keep commits focused, update `CHANGELOG.md` for user-facing changes, and adjust `README.md` if workflows shift.
- Pull requests should outline motivation, list validation steps, and note any new environment variables or ports.
- Include screenshots or short clips for UI updates and link related issues or TODOs to preserve traceability.

## Security & Configuration Tips
- Store API credentials in environment variables or deployment secrets, not in source files or `封装配置.json`.
- Scrub sensitive tokens from `upload.log` before committing and rotate keys used for demos.
- When packaging, confirm `BASE_PATH` works in frozen builds and backups remain inside the project tree.
