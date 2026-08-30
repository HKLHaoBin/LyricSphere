# LyricSphere EXE 发版流程

公开仓 **不单独演进后端**。权威源是私有仓 `famyliam`；本仓只接收白名单镜像并跑 EXE CI。

## 私有仓 → 公开仓

在私有仓根目录执行（`-Dest` 必须是 LyricSphere **git toplevel**）：

```powershell
.\scripts\sync-to-lyricsphere.ps1 -Dest 'F:\path\to\LyricSphere'
```

脚本会：

1. 校验 Dest（`.git`、`backend.spec`、`build-exe-release.yml`、位于 git toplevel、非 junction/symlink）
2. 要求源侧门禁文件与 Vite 输入 **git clean**；存在 `templates/lyric-sphere-v2/.env*` 则中止；构建前剥离 `VITE_*`
3. 运行 `pytest test_subsonic_provider.py`、`node scripts/verify-safe-increment.mjs`（在 `templates/lyric-sphere-v2`）、以及 `npm ci && npm run build`（`-SkipTests` 仅跳过前两项，且 `releaseEligible=false`）
4. 在 TEMP 组装候选树 + preimage，事务式覆盖白名单并整树镜像 `dist/`（删除目标多余哈希文件）
5. 写入 `sync-manifest.json` 与 `scripts/verify_sync_manifest.py`

**白名单（业务）：** `backend.py`、`requirements-backend.txt`、`subsonic/**/*.py`、`templates/lyric-sphere-v2/dist/**`  
**合同文件：** `sync-manifest.json`、`scripts/verify_sync_manifest.py`

脚本 **不** `git commit` / `push`。请在公开仓审 diff 后自行提交。

正式发版要求 `sync-manifest.json` 中 `releaseEligible: true`。CI 在注入 `APP_VERSION` **之前**用同一 verifier 校验 worktree；`releaseEligible != true` 直接失败。

本地尚未 commit、门禁路径 dirty 时可用 `-AllowDirty` 做首次落盘预览（强制 `releaseEligible=false`，公开 CI 仍会拒绝发版）。干净工作树后再跑一次无该开关的同步即可发版。

## 公开仓 CI（`build-exe-release.yml`）

顺序概要：

1. `check-worktree`（含 `releaseEligible`、占位 `0.0.0-dev`、路径/哈希）
2. `pip install -r requirements-backend.txt` → `import subsonic`
3. 解析 tag → 注入 `APP_VERSION` → PyInstaller（`backend.spec` 中 `datas=[]`，`collect_submodules(subsonic)`）
4. 组装干净 `release/LyricSphere.exe/`（exe + templates + `static/{assets,public,icons,monaco}`）
5. **复制到 `$RUNNER_TEMP`** 后跑 `backend.exe --self-test-subsonic`（超时 60s，保存退出码；finally 删副本）。原始 release **从不执行**
6. 用干净 release 打 `LyricSphere.exe.zip`，解压后 `check-zip` 全量审计 dist exact-set 与黑名单
7. 通过后才 upload-artifact / GitHub Release

Zip 审计禁止：`songs/`、`backups/`、`.cache/`、`exports/`、密钥配置、`node_modules/`、`lyric-sphere-v2/src/`、`debug-*.log` 等。

## 本地烟测（可选）

```powershell
$env:FAMYLIAM_SKIP_INDEX_INIT = '1'
# 在 TEMP 副本目录中：
.\backend.exe --self-test-subsonic
# 期望打印 SELF_TEST_SUBSONIC_OK，退出码 0
```

不要在即将打 zip 的干净 release 目录上直接跑 self-test（避免留下 `.cache/` / `exports/`）。

## 非目标

- 不同步个人媒体、备份、密钥、`src/`、pytest
- 不在公开仓 `npm build`
- 不改 updater 资产名 / `LyricSphere.exe.zip` Release 合同
- 不自动 push 公开仓
