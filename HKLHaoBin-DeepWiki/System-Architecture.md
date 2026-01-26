# System Architecture

> **Relevant source files**
> * [CLAUDE.md](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/CLAUDE.md)
> * [LICENSE](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/LICENSE)
> * [README.md](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/README.md)
> * [backend.py](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py)

## Purpose and Scope

This document provides a comprehensive overview of LyricSphere's system architecture, describing how the major components interact to deliver lyric management, format conversion, real-time synchronization, and AI translation capabilities. It covers the layered architecture design, core backend systems, frontend interfaces, real-time communication mechanisms, storage organization, and security implementations.

For detailed information about specific subsystems:

* API endpoint specifications: see [API Endpoints Reference](/HKLHaoBin/LyricSphere/2.1-api-endpoints-reference)
* Format conversion internals: see [Format Conversion Pipeline](/HKLHaoBin/LyricSphere/2.3-format-conversion-pipeline)
* AI translation implementation: see [AI Translation System](/HKLHaoBin/LyricSphere/2.4-ai-translation-system)
* Real-time communication details: see [Real-time Communication](/HKLHaoBin/LyricSphere/2.5-real-time-communication)
* Security mechanisms: see [Security and Authentication](/HKLHaoBin/LyricSphere/2.6-security-and-authentication)

---

## Layered Architecture Overview

LyricSphere implements a six-layer architecture that separates concerns and enables modular development:

```mermaid
flowchart TD

Dashboard["LyricSphere.html<br>Main Dashboard"]
AMMLPlayer["Lyrics-style.HTML-AMLL-v1.HTML<br>AMLL Player"]
QuickEditor["lyrics_quick_editor.html<br>Quick Editor"]
OtherUI["Other UI Templates"]
FastAPIApp["FlaskCompat (FastAPI)<br>RESTful API Server"]
Routes["60+ API Endpoints<br>/songs, /translate, /convert"]
Middleware["Middleware Stack<br>CORS, Request Context, Session"]
LyricParsers["Lyric Parsers<br>LRC/LYS/TTML/LQE"]
FormatConverters["Format Converters<br>Bidirectional Conversion"]
AIEngine["AI Translation Engine<br>Multi-Provider Support"]
FileManager["File Management System<br>Upload, Backup, Export"]
WebSocketServer["WebSocket Server<br>Port 11444"]
SSEEndpoint["SSE Endpoint<br>/amll/stream"]
AnimationSync["Animation Config Sync<br>/player/animation-config"]
SongsDir["static/songs/<br>JSON + Media Files"]
BackupsDir["static/backups/<br>7-Version Rotation"]
ExportsDir["exports/<br>ZIP Packages"]
LogsDir["logs/<br>upload.log"]
DeviceAuth["Device Authentication<br>Trusted Device Management"]
PasswordProtection["Password Protection<br>bcrypt Hashing"]
PathValidation["Path Validation<br>sanitize_filename, resolve_resource_path"]
CORSHandler["CORS Handler<br>Cross-Origin Support"]

Dashboard --> FastAPIApp
AMMLPlayer --> FastAPIApp
QuickEditor --> FastAPIApp
OtherUI --> FastAPIApp
Middleware --> LyricParsers
Middleware --> FormatConverters
Middleware --> AIEngine
Middleware --> FileManager
Routes --> WebSocketServer
Routes --> SSEEndpoint
Routes --> AnimationSync
LyricParsers --> SongsDir
FormatConverters --> SongsDir
FileManager --> SongsDir
FileManager --> BackupsDir
FileManager --> ExportsDir
FileManager --> LogsDir
Middleware --> DeviceAuth
Middleware --> PasswordProtection
Middleware --> PathValidation
Routes --> CORSHandler

subgraph subGraph5 ["Layer 6: Security Layer"]
    DeviceAuth
    PasswordProtection
    PathValidation
    CORSHandler
end

subgraph subGraph4 ["Layer 5: Data Storage Layer"]
    SongsDir
    BackupsDir
    ExportsDir
    LogsDir
end

subgraph subGraph3 ["Layer 4: Real-time Communication Layer"]
    WebSocketServer
    SSEEndpoint
    AnimationSync
end

subgraph subGraph2 ["Layer 3: Business Logic Layer"]
    LyricParsers
    FormatConverters
    AIEngine
    FileManager
end

subgraph subGraph1 ["Layer 2: API Layer"]
    FastAPIApp
    Routes
    Middleware
    FastAPIApp --> Routes
    Routes --> Middleware
end

subgraph subGraph0 ["Layer 1: Presentation Layer"]
    Dashboard
    AMMLPlayer
    QuickEditor
    OtherUI
end
```

**Sources:** [backend.py L760-L831](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L760-L831)

 [backend.py L854-L878](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L854-L878)

 [backend.py L950-L993](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L950-L993)

 [README.md L96-L108](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/README.md#L96-L108)

---

## Core Backend Architecture

### FastAPI/Flask Compatibility Layer

The backend uses `FlaskCompat`, a custom wrapper that provides Flask-style APIs on top of FastAPI for easier migration and familiar development patterns.

```mermaid
flowchart TD

StarletteRequest["StarletteRequest<br>Incoming HTTP"]
FlaskCompatClass["FlaskCompat(FastAPI)<br>app instance"]
RouteDecorator["@app.route()<br>Flask-style decorator"]
ContextVars["ContextVar Storage<br>_request_context"]
RequestContext["RequestContext<br>request body + form + json"]
RequestProxy["RequestProxy (request)<br>Global access point"]
SessionProxy["SessionProxy (session)<br>Session management"]
ContextMiddleware["_request_context_middleware<br>Context injection"]
BeforeRequest["@app.before_request<br>CORS preflight handler"]
AfterRequest["@app.after_request<br>CORS headers applier"]
NormalizeResponse["_normalize_response<br>Tuple/Dict/Response handling"]
CoerceResponse["_coerce_response<br>Type conversion"]
StarletteResponse["StarletteResponse<br>Final output"]

StarletteRequest --> ContextMiddleware
ContextMiddleware --> RequestContext
RequestContext --> ContextVars
ContextVars --> RequestProxy
ContextVars --> SessionProxy
BeforeRequest --> RouteDecorator
FlaskCompatClass --> NormalizeResponse
CoerceResponse --> AfterRequest
AfterRequest --> StarletteResponse

subgraph subGraph4 ["Response Processing"]
    NormalizeResponse
    CoerceResponse
    StarletteResponse
    NormalizeResponse --> CoerceResponse
end

subgraph subGraph3 ["Middleware Stack"]
    ContextMiddleware
    BeforeRequest
    AfterRequest
    ContextMiddleware --> BeforeRequest
end

subgraph subGraph2 ["Request Context Management"]
    RequestContext
    RequestProxy
    SessionProxy
end

subgraph subGraph1 ["FlaskCompat Layer"]
    FlaskCompatClass
    RouteDecorator
    ContextVars
    RouteDecorator --> FlaskCompatClass
end

subgraph subGraph0 ["Request Entry"]
    StarletteRequest
end
```

**Key Components:**

| Component | Location | Purpose |
| --- | --- | --- |
| `FlaskCompat` | [backend.py L760-L831](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L760-L831) | FastAPI subclass providing Flask-compatible API |
| `RequestContext` | [backend.py L278-L427](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L278-L427) | Encapsulates HTTP request data with caching |
| `RequestProxy` | [backend.py L429-L463](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L429-L463) | Global `request` object for Flask-style access |
| `SessionProxy` | [backend.py L466-L546](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L466-L546) | Global `session` object for session management |
| `_request_context` | [backend.py L51](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L51-L51) | ContextVar for storing current request context |
| `_request_context_middleware` | [backend.py L1235-L1262](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L1235-L1262) | Middleware that injects context into each request |

**Sources:** [backend.py L51-L52](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L51-L52)

 [backend.py L278-L546](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L278-L546)

 [backend.py L760-L831](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L760-L831)

 [backend.py L833-L835](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L833-L835)

 [backend.py L1235-L1262](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L1235-L1262)

### File Management Architecture

```mermaid
flowchart TD

UploadFile["UploadFile<br>FastAPI native"]
FileStorageAdapter["FileStorageAdapter<br>Wrapper class"]
FilesWrapper["FilesWrapper<br>Multi-file container"]
SaveUploadFile["save_upload_file<br>Async save"]
SaveWithMeta["save_upload_file_with_meta<br>Size + MD5 calculation"]
RunSyncInThread["_run_sync_in_thread<br>Thread pool executor"]
SanitizeFilename["sanitize_filename<br>Remove dangerous chars"]
ResolveResourcePath["resolve_resource_path<br>Resolve + validate"]
ExtractRelative["extract_resource_relative<br>Parse URL paths"]
NormalizePath["_normalize_relative_path<br>Segment validation"]
BuildBackupPath["build_backup_path<br>Timestamp + hash"]
BackupPrefix["backup_prefix<br>Filename prefix"]
NormalizeBasename["_normalize_backup_basename<br>Max 255 chars"]
RotateBackups["Backup Rotation<br>Max 7 versions"]
SONGS_DIR["SONGS_DIR<br>static/songs/"]
BACKUP_DIR["BACKUP_DIR<br>static/backups/"]
EXPORTS_DIR["EXPORTS_DIR<br>exports/"]
LOG_DIR["LOG_DIR<br>logs/"]

FilesWrapper --> SaveUploadFile
SaveUploadFile --> SanitizeFilename
SaveUploadFile --> BuildBackupPath
SaveUploadFile --> SONGS_DIR
RotateBackups --> BACKUP_DIR
SaveUploadFile --> EXPORTS_DIR
SaveUploadFile --> LOG_DIR

subgraph subGraph4 ["Storage Directories"]
    SONGS_DIR
    BACKUP_DIR
    EXPORTS_DIR
    LOG_DIR
end

subgraph subGraph3 ["Backup System"]
    BuildBackupPath
    BackupPrefix
    NormalizeBasename
    RotateBackups
    BuildBackupPath --> BackupPrefix
    BackupPrefix --> NormalizeBasename
    NormalizeBasename --> RotateBackups
end

subgraph subGraph2 ["Path Security"]
    SanitizeFilename
    ResolveResourcePath
    ExtractRelative
    NormalizePath
    SanitizeFilename --> ResolveResourcePath
    ResolveResourcePath --> ExtractRelative
    ExtractRelative --> NormalizePath
end

subgraph subGraph1 ["Async Operations"]
    SaveUploadFile
    SaveWithMeta
    RunSyncInThread
    SaveUploadFile --> SaveWithMeta
    SaveWithMeta --> RunSyncInThread
end

subgraph subGraph0 ["File Adapters"]
    UploadFile
    FileStorageAdapter
    FilesWrapper
    UploadFile --> FileStorageAdapter
    FileStorageAdapter --> FilesWrapper
end
```

**Key File Management Functions:**

| Function | Location | Purpose |
| --- | --- | --- |
| `FileStorageAdapter` | [backend.py L57-L120](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L57-L120) | Adapts FastAPI UploadFile to common interface |
| `save_upload_file` | [backend.py L196-L219](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L196-L219) | Async file saving with directory creation |
| `save_upload_file_with_meta` | [backend.py L222-L275](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L222-L275) | Save file + compute size and MD5 hash |
| `sanitize_filename` | [backend.py L997-L1004](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L997-L1004) | Remove unsafe characters from filenames |
| `resolve_resource_path` | [backend.py L1037-L1047](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L1037-L1047) | Validate and resolve resource paths |
| `build_backup_path` | [backend.py L1318-L1330](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L1318-L1330) | Generate timestamped backup filename |
| `_normalize_backup_basename` | [backend.py L1299-L1315](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L1299-L1315) | Ensure backup names fit 255-char limit |

**Sources:** [backend.py L57-L275](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L57-L275)

 [backend.py L838-L851](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L838-L851)

 [backend.py L950-L993](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L950-L993)

 [backend.py L994-L1063](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L994-L1063)

 [backend.py L1293-L1336](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L1293-L1336)

---

## Frontend Architecture

LyricSphere provides multiple frontend interfaces for different use cases:

```mermaid
flowchart TD

MainDashboard["LyricSphere.html<br>Song Management Dashboard"]
AMMLPlayerV1["Lyrics-style.HTML-AMLL-v1.HTML<br>Advanced AMLL Player"]
QuickEditorUI["lyrics_quick_editor.html<br>Quick Lyric Editor"]
BasicPlayer["Lyrics-style.HTML<br>Basic Player"]
AnimatePlayer["Lyrics-style.HTML-Animate.HTML<br>Animation Showcase"]
AMMLIntegration["Lyrics-style.HTML-AMLL-v1-test.HTML<br>AMLL Test Player"]
APIServer["backend.py<br>FlaskCompat Application"]
StaticFiles["StaticFiles Mount<br>/static path"]
MonacoEditor["Monaco Editor<br>Code editing"]
AMLLAPI["AMLL Integration<br>WebSocket + SSE"]
GoogleFonts["Google Fonts<br>Dynamic loading"]

MainDashboard --> APIServer
AMMLPlayerV1 --> APIServer
QuickEditorUI --> APIServer
BasicPlayer --> APIServer
AnimatePlayer --> APIServer
AMMLIntegration --> APIServer
MainDashboard --> MonacoEditor
QuickEditorUI --> MonacoEditor
AMMLPlayerV1 --> AMLLAPI
AMMLIntegration --> AMLLAPI
AMMLPlayerV1 --> GoogleFonts
AnimatePlayer --> GoogleFonts

subgraph subGraph3 ["Shared Resources"]
    MonacoEditor
    AMLLAPI
    GoogleFonts
end

subgraph subGraph2 ["Backend API"]
    APIServer
    StaticFiles
    APIServer --> StaticFiles
end

subgraph subGraph1 ["Player Variants"]
    BasicPlayer
    AnimatePlayer
    AMMLIntegration
end

subgraph subGraph0 ["Primary Interfaces"]
    MainDashboard
    AMMLPlayerV1
    QuickEditorUI
end
```

**Frontend Interface Responsibilities:**

| Interface | File | Primary Purpose |
| --- | --- | --- |
| Main Dashboard | `LyricSphere.html` | Song CRUD, search, batch import/export |
| AMLL Player V1 | `Lyrics-style.HTML-AMLL-v1.HTML` | Full-featured player with background visualizer |
| Quick Editor | `lyrics_quick_editor.html` | Fast lyric editing with document operations |
| Basic Player | `Lyrics-style.HTML` | Simple lyric display player |
| Animation Showcase | `Lyrics-style.HTML-Animate.HTML` | Animation effect demonstrations |
| AMLL Test Player | `Lyrics-style.HTML-AMLL-v1-test.HTML` | AMLL integration testing |

**Sources:** [README.md L102-L108](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/README.md#L102-L108)

 [CLAUDE.md L9-L17](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/CLAUDE.md#L9-L17)

 Diagram 3 from provided context

---

## Real-time Communication Architecture

### Dual-Channel Communication System

LyricSphere implements two parallel real-time communication channels optimized for different client types:

```mermaid
sequenceDiagram
  participant Browser
  participant /amll/stream
  participant SSE Endpoint
  participant WebSocket Server
  participant Port 11444
  participant AMLL Client
  participant backend.py
  participant Route Handlers

  Browser->>backend.py: GET /player page
  backend.py-->>Browser: HTML + initial config
  Browser->>/amll/stream: Connect SSE stream
  /amll/stream-->>Browser: Connection established
  AMLL Client->>WebSocket Server: WebSocket connect :11444
  WebSocket Server-->>AMLL Client: Connection accepted
  loop [Playback Loop]
    backend.py->>/amll/stream: Push lyric line event
    /amll/stream-->>Browser: data: {line, timestamp}
    Browser->>Browser: Render + animate
    backend.py->>WebSocket Server: Push AMLL rule message
    WebSocket Server-->>AMLL Client: JSON lyric rule
    AMLL Client->>AMLL Client: Parse + render
  end
  Browser->>backend.py: POST /player/animation-config
  backend.py-->>Browser: Sync animation params (600ms)
  note over Browser,Route Handlers: Frontend reports params,
```

**Real-time Components:**

| Component | Location | Protocol | Purpose |
| --- | --- | --- | --- |
| WebSocket Server | Runs on port 11444 | WebSocket | AMLL client integration |
| SSE Endpoint | `/amll/stream` route | Server-Sent Events | Browser real-time updates |
| Animation Sync | `/player/animation-config` | HTTP POST | Synchronize animation timing |

**Sources:** [backend.py (WebSocket implementation)](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py (WebSocket implementation))

 [README.md L133-L136](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/README.md#L133-L136)

 [CLAUDE.md L65-L71](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/CLAUDE.md#L65-L71)

 Diagram 4 from provided context

### Animation Configuration Flow

```mermaid
flowchart TD

FrontendParams["Frontend Animation Params<br>entry/move/exit durations"]
AnimConfigRoute["/player/animation-config<br>POST handler"]
DefaultSync["Default: 600ms<br>Validate + normalize"]
UseComputedFlag["useComputedDisappear<br>Toggle flag"]
ComputeDisappear["compute_disappear_times<br>Calculate line exit timing"]
TimestampAlign["Timestamp alignment<br>with next line"]
BrowserRender["Browser Renderer<br>Apply animations"]
AMMLRender["AMLL Renderer<br>Process rules"]

FrontendParams --> AnimConfigRoute
UseComputedFlag --> ComputeDisappear
TimestampAlign --> BrowserRender
TimestampAlign --> AMMLRender

subgraph subGraph3 ["Client Rendering"]
    BrowserRender
    AMMLRender
end

subgraph subGraph2 ["Backend Calculation"]
    ComputeDisappear
    TimestampAlign
    ComputeDisappear --> TimestampAlign
end

subgraph subGraph1 ["Backend Sync Endpoint"]
    AnimConfigRoute
    DefaultSync
    UseComputedFlag
    AnimConfigRoute --> DefaultSync
    DefaultSync --> UseComputedFlag
end

subgraph subGraph0 ["Frontend Report"]
    FrontendParams
end
```

**Sources:** [backend.py (animation config endpoint)](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py (animation config endpoint))

 [README.md L135-L136](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/README.md#L135-L136)

 [CLAUDE.md L147](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/CLAUDE.md#L147-L147)

---

## Storage and File Organization

### Directory Structure

```mermaid
flowchart TD

BasePath["BASE_PATH<br>Application root"]
StaticDir["static/<br>Web-accessible files"]
SongsDir["static/songs/<br>JSON + audio + images"]
BackupsDir["static/backups/<br>7-version rotation"]
ExportsDir["exports/<br>ZIP export packages"]
LogsDir["logs/<br>upload.log activity"]
TemplatesDir["templates/<br>Jinja2 HTML templates"]
ResourceMap["{'static': STATIC_DIR,<br>'songs': SONGS_DIR,<br>'backups': BACKUP_DIR}"]

BasePath --> StaticDir
BasePath --> ExportsDir
BasePath --> LogsDir
BasePath --> TemplatesDir
SongsDir --> ResourceMap
BackupsDir --> ResourceMap
StaticDir --> ResourceMap

subgraph subGraph4 ["RESOURCE_DIRECTORIES Map"]
    ResourceMap
end

subgraph Templates ["Templates"]
    TemplatesDir
end

subgraph subGraph2 ["Dynamic Data"]
    ExportsDir
    LogsDir
end

subgraph subGraph1 ["Static Resources (STATIC_DIR)"]
    StaticDir
    SongsDir
    BackupsDir
    StaticDir --> SongsDir
    StaticDir --> BackupsDir
end

subgraph subGraph0 ["BASE_PATH Root"]
    BasePath
end
```

**Path Resolution Constants:**

| Constant | Location | Value | Purpose |
| --- | --- | --- | --- |
| `BASE_PATH` | [backend.py L847](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L847-L847) | `get_base_path()` result | Application root (handles frozen/dev mode) |
| `STATIC_DIR` | [backend.py L950](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L950-L950) | `BASE_PATH / 'static'` | Web-accessible resources |
| `SONGS_DIR` | [backend.py L951](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L951-L951) | `STATIC_DIR / 'songs'` | Song JSON + media files |
| `BACKUP_DIR` | [backend.py L952](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L952-L952) | `STATIC_DIR / 'backups'` | Versioned backup files |
| `EXPORTS_DIR` | [backend.py L850](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L850-L850) | `BASE_PATH / 'exports'` | ZIP export packages |
| `LOG_DIR` | [backend.py L953](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L953-L953) | `BASE_PATH / 'logs'` | Application logs |
| `RESOURCE_DIRECTORIES` | [backend.py L988-L992](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L988-L992) | Directory mapping dict | Path validation whitelist |

**Sources:** [backend.py L838-L857](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L838-L857)

 [backend.py L950-L993](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L950-L993)

### Backup Version Management

```mermaid
flowchart TD

FileWrite["File Write Operation"]
CreateBackup["Create Backup Decision"]
OriginalName["Original Filename<br>example_song.json"]
NormalizeLength["_normalize_backup_basename<br>Max 255 chars"]
AddTimestamp["Add timestamp<br>.20240115_143052"]
HashLongNames["Hash if too long<br>SHA1[:8] suffix"]
BackupDirStore["BACKUP_DIR<br>static/backups/"]
CountVersions["Count existing versions<br>by prefix match"]
DeleteOldest["Delete oldest<br>if count > 7"]

CreateBackup --> OriginalName
HashLongNames --> BackupDirStore

subgraph subGraph2 ["Storage & Rotation"]
    BackupDirStore
    CountVersions
    DeleteOldest
    BackupDirStore --> CountVersions
    CountVersions --> DeleteOldest
end

subgraph subGraph1 ["Filename Generation"]
    OriginalName
    NormalizeLength
    AddTimestamp
    HashLongNames
    OriginalName --> NormalizeLength
    NormalizeLength --> AddTimestamp
    AddTimestamp --> HashLongNames
end

subgraph subGraph0 ["Backup Triggering"]
    FileWrite
    CreateBackup
    FileWrite --> CreateBackup
end
```

**Backup System Constants:**

| Constant | Location | Value | Purpose |
| --- | --- | --- | --- |
| `BACKUP_TIMESTAMP_FORMAT` | [backend.py L1293](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L1293-L1293) | `'%Y%m%d_%H%M%S'` | Backup timestamp format |
| `MAX_BACKUP_FILENAME_LENGTH` | [backend.py L1295](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L1295-L1295) | `255` | Filesystem filename limit |
| `BACKUP_HASH_LENGTH` | [backend.py L1296](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L1296-L1296) | `8` | SHA1 hash truncation length |
| Max backup versions | Implicit | `7` | Version rotation limit |

**Sources:** [backend.py L1293-L1336](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L1293-L1336)

 [README.md L26](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/README.md#L26-L26)

 [CLAUDE.md L113](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/CLAUDE.md#L113-L113)

---

## Security Architecture

### Multi-Layer Security System

```mermaid
flowchart TD

CORSMiddleware["CORS Handler<br>_match_cors_origin"]
PreflightHandler["Preflight Handler<br>handle_cors_preflight"]
HeaderApplier["Header Applier<br>apply_cors_headers"]
DeviceCheck["Device Status Check"]
TrustedList["Trusted Devices<br>localStorage-based"]
PasswordPrompt["Password Verification<br>bcrypt.checkpw"]
SanitizeFn["sanitize_filename<br>SAFE_FILENAME_PATTERN"]
ResolvePathFn["resolve_resource_path<br>Boundary checking"]
NormalizePathFn["_normalize_relative_path<br>Segment validation"]
WhitelistCheck["RESOURCE_DIRECTORIES<br>Whitelist validation"]
SessionMiddleware["SessionMiddleware<br>Starlette"]
SessionProxy["SessionProxy (session)<br>Context-aware access"]
SecretKey["app.secret_key<br>Session encryption"]
IPv4MappedCheck["IPv4 Mapped Detection<br>::ffff:127.0.0.1"]
LoopbackDetect["Loopback Detection<br>Local address check"]
OperationGating["Operation Gating<br>Read/Write/Critical"]
UploadLog["upload.log<br>Operation tracking"]
ErrorLog["Error Logging<br>Failed operations"]
TimedRotation["TimedRotatingFileHandler<br>Log rotation"]

HeaderApplier --> DeviceCheck
PasswordPrompt --> SanitizeFn
WhitelistCheck --> SessionMiddleware
SecretKey --> IPv4MappedCheck
OperationGating --> UploadLog
OperationGating --> ErrorLog

subgraph subGraph5 ["Layer 6: Audit Logging"]
    UploadLog
    ErrorLog
    TimedRotation
    ErrorLog --> TimedRotation
end

subgraph subGraph4 ["Layer 5: Request Validation"]
    IPv4MappedCheck
    LoopbackDetect
    OperationGating
    IPv4MappedCheck --> LoopbackDetect
    LoopbackDetect --> OperationGating
end

subgraph subGraph3 ["Layer 4: Session Management"]
    SessionMiddleware
    SessionProxy
    SecretKey
    SessionMiddleware --> SessionProxy
    SessionProxy --> SecretKey
end

subgraph subGraph2 ["Layer 3: Path Security"]
    SanitizeFn
    ResolvePathFn
    NormalizePathFn
    WhitelistCheck
    SanitizeFn --> ResolvePathFn
    ResolvePathFn --> NormalizePathFn
    NormalizePathFn --> WhitelistCheck
end

subgraph subGraph1 ["Layer 2: Device Authentication"]
    DeviceCheck
    TrustedList
    PasswordPrompt
    DeviceCheck --> TrustedList
    DeviceCheck --> PasswordPrompt
end

subgraph subGraph0 ["Layer 1: CORS Protection"]
    CORSMiddleware
    PreflightHandler
    HeaderApplier
    CORSMiddleware --> PreflightHandler
    PreflightHandler --> HeaderApplier
end
```

**Security Functions and Mechanisms:**

| Component | Location | Purpose |
| --- | --- | --- |
| `sanitize_filename` | [backend.py L997-L1004](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L997-L1004) | Remove dangerous characters, prevent injection |
| `_normalize_relative_path` | [backend.py L1006-L1015](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L1006-L1015) | Validate path segments, block `..` traversal |
| `resolve_resource_path` | [backend.py L1037-L1047](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L1037-L1047) | Resolve + validate against base directory |
| `SAFE_FILENAME_PATTERN` | [backend.py L994](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L994-L994) | Regex whitelist for safe characters |
| `RESOURCE_DIRECTORIES` | [backend.py L988-L992](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L988-L992) | Whitelist of allowed resource paths |
| `_match_cors_origin` | [backend.py L1225-L1232](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L1225-L1232) | Validate CORS origin against allowed list |
| `SessionMiddleware` | [backend.py L862](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L862-L862) | Starlette session middleware for state |
| bcrypt password hashing | Imported at [backend.py L8](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L8-L8) | Password protection with salt |

**Sources:** [backend.py L8](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L8-L8)

 [backend.py L862](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L862-L862)

 [backend.py L988-L1047](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L988-L1047)

 [backend.py L1225-L1291](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L1225-L1291)

 [README.md L156-L165](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/README.md#L156-L165)

 Diagram 6 from provided context

---

## External Integrations

### AI Provider Integration Architecture

```mermaid
flowchart TD

BuildClient["build_openai_client<br>Resilient SSL setup"]
CleanupSSL["cleanup_missing_ssl_cert_env<br>Remove invalid env vars"]
CertifiFallback["certifi.where()<br>CA bundle fallback"]
DisableSSLLastResort["httpx.Client(verify=False)<br>Last resort fallback"]
DeepSeek["DeepSeek API<br>deepseek-reasoner"]
OpenAI["OpenAI API<br>GPT models"]
OpenRouter["OpenRouter API<br>Multi-model gateway"]
Together["Together API<br>Open models"]
Groq["Groq API<br>Fast inference"]
TranslateEndpoint["/translate_lyrics<br>Route handler"]
PromptConstruction["Prompt Construction<br>System + User messages"]
ThinkingModel["Optional Thinking Stage<br>Pre-analysis model"]
StreamingResponse["Streaming Response<br>Real-time chunks"]
TimestampSync["Timestamp Synchronization"]
LineValidation["Line Count Validation"]
IssueDetection["Issue Detection<br>Untranslated/incorrect lines"]

BuildClient --> DeepSeek
BuildClient --> OpenAI
BuildClient --> OpenRouter
BuildClient --> Together
BuildClient --> Groq
DeepSeek --> TranslateEndpoint
OpenAI --> TranslateEndpoint
OpenRouter --> TranslateEndpoint
Together --> TranslateEndpoint
Groq --> TranslateEndpoint
StreamingResponse --> TimestampSync

subgraph Post-processing ["Post-processing"]
    TimestampSync
    LineValidation
    IssueDetection
    TimestampSync --> LineValidation
    LineValidation --> IssueDetection
end

subgraph subGraph2 ["Translation Pipeline"]
    TranslateEndpoint
    PromptConstruction
    ThinkingModel
    StreamingResponse
    TranslateEndpoint --> PromptConstruction
    PromptConstruction --> ThinkingModel
    ThinkingModel --> StreamingResponse
end

subgraph subGraph1 ["Supported Providers"]
    DeepSeek
    OpenAI
    OpenRouter
    Together
    Groq
end

subgraph subGraph0 ["AI Client Builder"]
    BuildClient
    CleanupSSL
    CertifiFallback
    DisableSSLLastResort
    BuildClient --> CleanupSSL
    CleanupSSL --> CertifiFallback
    CertifiFallback --> DisableSSLLastResort
end
```

**AI Integration Components:**

| Component | Location | Purpose |
| --- | --- | --- |
| `build_openai_client` | [backend.py L910-L947](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L910-L947) | Create OpenAI-compatible client with SSL fallback |
| `cleanup_missing_ssl_cert_env` | [backend.py L890-L907](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L890-L907) | Remove invalid SSL cert environment variables |
| Translation endpoint | Route: `/translate_lyrics` | AI-powered lyric translation with streaming |
| Thinking model support | In translation pipeline | Optional pre-analysis for better translation |
| Provider configuration | Environment variables + UI | User-configurable AI provider selection |

**Sources:** [backend.py L8](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L8-L8)

 [backend.py L40](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L40-L40)

 [backend.py L890-L947](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L890-L947)

 [README.md L67](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/README.md#L67-L67)

 [README.md L143-L154](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/README.md#L143-L154)

 Diagram 7 from provided context

### Font Service Integration

Fonts are dynamically loaded from multiple sources with fallback mechanisms:

```mermaid
flowchart TD

FontMetaTag["[font-family:...] tag<br>In lyric content"]
ScriptDetection["Script Detection<br>CJK vs Latin"]
FontMapping["Font Family Mapping<br>Per-script selection"]
LocalFonts["Local Files<br>/songs or /fonts"]
GoogleFonts["Google Fonts API<br>Dynamic loading"]
CDNFonts["Font CDN<br>Fallback source"]
SystemFonts["System Fonts<br>Final fallback"]
AvailabilityCheck["Font Availability Check<br>document.fonts.check"]
SpecialFontMode["Special Font Mode<br>Pure color rendering"]
SyllableAnimation["Per-syllable Animation<br>Optimized rendering"]

FontMapping --> LocalFonts
SystemFonts --> AvailabilityCheck

subgraph subGraph2 ["Rendering Optimization"]
    AvailabilityCheck
    SpecialFontMode
    SyllableAnimation
    AvailabilityCheck --> SpecialFontMode
    SpecialFontMode --> SyllableAnimation
end

subgraph subGraph1 ["Font Loading Strategy"]
    LocalFonts
    GoogleFonts
    CDNFonts
    SystemFonts
    LocalFonts --> GoogleFonts
    GoogleFonts --> CDNFonts
    CDNFonts --> SystemFonts
end

subgraph subGraph0 ["Font Source Detection"]
    FontMetaTag
    ScriptDetection
    FontMapping
    FontMetaTag --> ScriptDetection
    ScriptDetection --> FontMapping
end
```

**Font System Features:**

* Parse `[font-family:FontName]` metadata tags from lyric content
* Script detection (Chinese, Japanese, English) for appropriate font selection
* Multi-source loading: local files → Google Fonts → CDN → system fallback
* Font availability checking before rendering
* Special handling for decorative fonts (pure color mode)
* Per-syllable font application for fine-grained control

**Sources:** [backend.py L1138-L1183](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L1138-L1183)

 (extract_font_files_from_lys function), [README.md L46-L59](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/README.md#L46-L59)

 [CLAUDE.md L22](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/CLAUDE.md#L22-L22)

 Diagram 4 from provided context

### LDDC Integration

```mermaid
flowchart TD

SearchModal["Lyrics Search Modal"]
FilenameParse["Filename Parser<br>Extract title/artist"]
MatchAPI["Match API Endpoint<br>Title + Artist search"]
KeywordSearch["Keyword Search<br>Fuzzy matching"]
ResultPreview["Result Preview<br>Before application"]
ParseLyrics["Parse Retrieved Lyrics<br>Format detection"]
ApplyToEditor["Apply to Monaco Editor<br>With validation"]

FilenameParse --> MatchAPI
SearchModal --> KeywordSearch
ResultPreview --> ParseLyrics

subgraph subGraph2 ["Backend Processing"]
    ParseLyrics
    ApplyToEditor
    ParseLyrics --> ApplyToEditor
end

subgraph subGraph1 ["LDDC API"]
    MatchAPI
    KeywordSearch
    ResultPreview
    MatchAPI --> ResultPreview
    KeywordSearch --> ResultPreview
end

subgraph subGraph0 ["Frontend Integration"]
    SearchModal
    FilenameParse
    SearchModal --> FilenameParse
end
```

**Sources:** [README.md L11](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/README.md#L11-L11)

 Diagram 3 from provided context

---

## Key Data Flows

### Song Creation Flow

```mermaid
sequenceDiagram
  participant User
  participant LyricSphere.html
  participant Main Dashboard
  participant FlaskCompat
  participant Backend API
  participant Lyric Parser
  participant Format detection
  participant File System
  participant SONGS_DIR
  participant Backup System
  participant BACKUP_DIR

  User->>LyricSphere.html: Click "Create Song"
  LyricSphere.html->>User: Show creation modal
  User->>LyricSphere.html: Fill song data + upload files
  LyricSphere.html->>FlaskCompat: POST /songs/create
  FlaskCompat->>FlaskCompat: JSON + files
  FlaskCompat->>FlaskCompat: sanitize_filename(name)
  FlaskCompat->>Lyric Parser: Device auth check
  Lyric Parser-->>FlaskCompat: Parse lyrics format
  FlaskCompat->>File System: Detected format (LYS/LRC/TTML)
  File System-->>FlaskCompat: save_upload_file_with_meta
  FlaskCompat->>File System: Audio + images
  FlaskCompat->>Backup System: File paths + MD5 hashes
  FlaskCompat-->>LyricSphere.html: Write song JSON
  LyricSphere.html->>LyricSphere.html: songs/{name}.json
```

**Sources:** [backend.py L196-L275](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L196-L275)

 (file save functions), [backend.py L997-L1004](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L997-L1004)

 (sanitize_filename), [backend.py L1318-L1330](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L1318-L1330)

 (backup path generation)

### Format Conversion Flow

```mermaid
sequenceDiagram
  participant User
  participant Edit Modal
  participant Monaco Editor
  participant FlaskCompat
  participant Backend API
  participant Format Converter
  participant Source Parser
  participant LRC/LYS/TTML
  participant Target Generator
  participant TTML/LRC/LYS

  User->>Edit Modal: Click "Convert to TTML"
  Edit Modal->>FlaskCompat: POST /convert_to_ttml
  FlaskCompat->>Format Converter: {lyrics, format}
  Format Converter->>Source Parser: convert_to_ttml(lyrics)
  Source Parser-->>Format Converter: Parse source format
  Format Converter->>Target Generator: Parsed lyric lines
  Target Generator->>Target Generator: Generate TTML XML
  Target Generator->>Target Generator: Apple-style output
  Target Generator->>Target Generator: Create <tt> root
  Target Generator-->>Format Converter: Add <head> metadata
  Format Converter-->>FlaskCompat: Create <body><div><p> structure
  FlaskCompat-->>Edit Modal: Add syllables with timing
  Edit Modal->>Edit Modal: Handle duets (ttm:agent)
```

**Sources:** Format conversion implementation in [backend.py](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py)

 [CLAUDE.md L123-L144](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/CLAUDE.md#L123-L144)

 Diagram 5 from provided context

### AI Translation Flow

```mermaid
sequenceDiagram
  participant User
  participant Translation UI
  participant Settings Panel
  participant FlaskCompat
  participant /translate_lyrics
  participant build_openai_client
  participant SSL fallback
  participant AI Provider
  participant DeepSeek/OpenAI/etc
  participant Post-processing

  User->>Translation UI: Configure provider + model
  User->>Translation UI: Click "Translate"
  Translation UI->>FlaskCompat: POST /translate_lyrics
  FlaskCompat->>FlaskCompat: {lyrics, provider, model, prompt}
  FlaskCompat->>build_openai_client: Strip brackets (optional)
  build_openai_client->>build_openai_client: Cleanup whitespace
  loop [Still fails]
    build_openai_client->>build_openai_client: Create AI client
    build_openai_client->>build_openai_client: Try default SSL
    build_openai_client->>build_openai_client: cleanup_missing_ssl_cert_env
    build_openai_client-->>FlaskCompat: Try certifi.where()
    FlaskCompat->>AI Provider: Disable SSL verify (log warning)
    AI Provider-->>FlaskCompat: OpenAI-compatible client
    FlaskCompat-->>Translation UI: Stream request
    Translation UI->>Translation UI: chat.completions.create(stream=True)
  end
  FlaskCompat->>Post-processing: Chunk with delta content
  Post-processing->>Post-processing: SSE event {text, progress}
  Post-processing-->>FlaskCompat: Update translation editor
  FlaskCompat-->>Translation UI: Show progress animation
```

**Sources:** [backend.py L890-L947](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L890-L947)

 (build_openai_client), [README.md L143-L154](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/README.md#L143-L154)

 [CLAUDE.md L153-L162](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/CLAUDE.md#L153-L162)

 Diagram 7 from provided context

### Real-time Lyric Streaming Flow

```mermaid
sequenceDiagram
  participant Browser
  participant /amll/stream
  participant SSE Endpoint
  participant Backend Routes
  participant Lyric Processor
  participant Timestamp calculator
  participant WebSocket Server
  participant Port 11444
  participant AMLL Desktop Client

  Browser->>Backend Routes: Load player page
  Backend Routes-->>Browser: HTML + song JSON
  Browser->>/amll/stream: Connect EventSource
  /amll/stream-->>Browser: GET /amll/stream?songId=X
  AMLL Desktop Client->>WebSocket Server: Connection established
  WebSocket Server-->>AMLL Desktop Client: Connect WebSocket
  Browser->>Backend Routes: ws://localhost:11444
  Backend Routes->>Lyric Processor: Connection accepted
  Lyric Processor->>Lyric Processor: Report playback start
  loop [Playback Updates]
    Backend Routes->>/amll/stream: Current timestamp
    /amll/stream-->>Browser: Get lyric lines
    Browser->>Browser: with timing data
    Backend Routes->>WebSocket Server: compute_disappear_times
    WebSocket Server-->>AMLL Desktop Client: Calculate exit timing
    AMLL Desktop Client->>AMLL Desktop Client: Push lyric line
  end
```

**Sources:** WebSocket and SSE implementation in [backend.py](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py)

 [README.md L133-L136](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/README.md#L133-L136)

 [CLAUDE.md L65-L71](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/CLAUDE.md#L65-L71)

 Diagram 4 from provided context

---

## Component Interaction Matrix

| Component | Interacts With | Protocol/Mechanism | Purpose |
| --- | --- | --- | --- |
| `FlaskCompat` | All route handlers | Python function calls | Main application orchestrator |
| `RequestContext` | All route handlers | ContextVar | Request state management |
| `FileStorageAdapter` | File upload routes | Wrapper pattern | FastAPI file handling |
| `sanitize_filename` | All file operations | Function call | Security validation |
| `resolve_resource_path` | Resource serving routes | Function call | Path security |
| WebSocket Server | AMLL clients | WebSocket protocol (port 11444) | Real-time lyric updates |
| SSE Endpoint | Browser clients | Server-Sent Events | Real-time streaming |
| AI Client Builder | Translation endpoint | OpenAI SDK | AI provider abstraction |
| Backup System | File write operations | Automatic trigger | Version management |
| CORS Handler | All HTTP requests | Middleware | Cross-origin security |
| Session Middleware | Authentication routes | Starlette middleware | State persistence |

**Sources:** [backend.py L760-L831](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L760-L831)

 [backend.py L278-L546](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L278-L546)

 [backend.py L57-L275](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L57-L275)

 [backend.py L997-L1047](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L997-L1047)

 [backend.py L890-L947](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L890-L947)

 [backend.py L1235-L1291](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L1235-L1291)

---

## Deployment Architecture

LyricSphere supports flexible deployment modes:

```mermaid
flowchart TD

DevScript["python backend.py<br>Direct execution"]
DevPath["BASE_PATH = file.parent<br>Script directory"]
DevServer["FastAPI dev server<br>or Waitress (USE_WAITRESS=1)"]
FrozenExe["backend.exe<br>PyInstaller bundle"]
FrozenPath["BASE_PATH = sys.executable.parent<br>Executable directory"]
WaitressServer["Waitress WSGI Server<br>Production-grade"]
EnvVars["Environment Variables<br>PORT, PUBLIC_BASE_URL, etc"]
ConfigDict["app.config dict<br>Runtime settings"]
LocalNetwork["Local Network<br>http://IP:PORT"]
PublicURL["Public URL<br>Configured base URL"]

EnvVars --> DevServer
EnvVars --> WaitressServer
ConfigDict --> DevServer
ConfigDict --> WaitressServer
DevServer --> LocalNetwork
WaitressServer --> LocalNetwork

subgraph subGraph3 ["External Access"]
    LocalNetwork
    PublicURL
    LocalNetwork --> PublicURL
end

subgraph Configuration ["Configuration"]
    EnvVars
    ConfigDict
end

subgraph subGraph1 ["Production Mode (Frozen)"]
    FrozenExe
    FrozenPath
    WaitressServer
    FrozenExe --> FrozenPath
    FrozenPath --> WaitressServer
end

subgraph subGraph0 ["Development Mode"]
    DevScript
    DevPath
    DevServer
    DevScript --> DevPath
    DevPath --> DevServer
end
```

**Key Deployment Functions:**

| Function/Constant | Location | Purpose |
| --- | --- | --- |
| `get_base_path()` | [backend.py L838-L844](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L838-L844) | Detect frozen vs dev mode, return correct base path |
| `BASE_PATH` | [backend.py L847](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L847-L847) | Global application root directory |
| `USE_WAITRESS` env var | Runtime check | Switch to Waitress production server |
| `PORT` env var | Runtime config | Configure listening port (default 5000) |
| `PUBLIC_BASE_URL` env var | Runtime config | Override public URL for proxied deployments |

**Sources:** [backend.py L838-L857](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L838-L857)

 [README.md L69-L93](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/README.md#L69-L93)

 [CLAUDE.md L21-L45](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/CLAUDE.md#L21-L45)

---

This architecture enables LyricSphere to maintain clear separation of concerns while supporting complex operations like real-time synchronization, multi-format conversion, and AI-powered translation across multiple client types.