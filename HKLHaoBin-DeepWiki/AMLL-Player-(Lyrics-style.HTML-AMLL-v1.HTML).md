# AMLL Player (Lyrics-style.HTML-AMLL-v1.HTML)

> **Relevant source files**
> * [LICENSE](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/LICENSE)
> * [README.md](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/README.md)
> * [static/assets/amll-player.js](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/static/assets/amll-player.js)
> * [templates/Lyrics-style.HTML-AMLL-v1.HTML](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/templates/Lyrics-style.HTML-AMLL-v1.HTML)
> * [templates/amll_web_player.html](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/templates/amll_web_player.html)

## Purpose and Scope

This document describes the AMLL Player (`Lyrics-style.HTML-AMLL-v1.HTML`), an advanced lyric display interface that provides syllable-level animation, dynamic font rendering, and audio visualization. This player implements a full-featured presentation layer for synchronized lyrics with real-time updates via WebSocket or Server-Sent Events (SSE).

For information about the AMLL WebSocket server backend, see [2.5.1](/HKLHaoBin/LyricSphere/2.5.1-websocket-server). For the main dashboard interface where songs are managed, see [3.1](/HKLHaoBin/LyricSphere/3.1-main-dashboard-(lyricsphere.html)). For real-time communication protocols, see [2.5](/HKLHaoBin/LyricSphere/2.5-real-time-communication).

**Sources:** [templates/Lyrics-style.HTML-AMLL-v1.HTML L1-L250](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/templates/Lyrics-style.HTML-AMLL-v1.HTML#L1-L250)

---

## Architecture Overview

The AMLL Player is implemented as a single-page HTML application with embedded JavaScript that orchestrates multiple subsystems for lyric display, animation, and audio visualization.

```mermaid
flowchart TD

MainScript["Main Script<br>(DOMContentLoaded handler)"]
PlaybackClock["Playback Clock<br>getCurrentPlaybackMs()"]
LineClock["Line Clock<br>getLinePlaybackMs()"]
LyricsContainer["Lyrics Container<br>#lyrics-container"]
AudioElement["Audio Element<br>#audio-player"]
FontSlider["Font Slider<br>#fontSlider"]
SSEStream["SSE Stream<br>/amll/stream"]
StateEndpoint["State Endpoint<br>/amll/state"]
AnimConfigEndpoint["Animation Config<br>/player/animation-config"]
LyricsData["lyricsData array"]
TranslationData["translationData array"]
DisplayedLines["displayedLines map"]
SyllableRenderer["prepareSyllableAnimations()"]
FontParser["parseFontFamily()<br>Font Meta Parser"]
FontDetector["detectScriptType()<br>Script Detector"]
FontLoader["loadFont()<br>Multi-source Loader"]
FontAvailability["checkFontAvailability()"]
AutoScale["Auto-scale Logic<br>computeAutoScaleTarget()"]
LineMetrics["measureLineMetrics()"]
AnimationSync["requestLyricAnimationResync()"]
FLIPRenderer["FLIP Rendering<br>Per-syllable animations"]
AMMLModule["AMLL Module<br>amll-player.js"]
AMMLBackground["globalBackground object"]
BackgroundContainer["Background Container<br>.background-container"]
BeatCurve["Beat Curve Data<br>parseBeatCurve()"]
ResourceResolver["resolveMediaUrl()"]
BackgroundCleanup["backgroundCleanup callback"]
AMMLStubs["AMLL Stub Elements<br>ensureAmllStubs()"]

SSEStream --> MainScript
StateEndpoint --> MainScript
AnimConfigEndpoint --> MainScript
MainScript --> LyricsData
DisplayedLines --> LyricsContainer
PlaybackClock --> SyllableRenderer
LineClock --> SyllableRenderer
SyllableRenderer --> FontParser
SyllableRenderer --> FLIPRenderer
MainScript --> AMMLModule
MainScript --> BeatCurve
MainScript --> ResourceResolver
ResourceResolver --> BackgroundContainer
MainScript --> BackgroundCleanup
MainScript --> AMMLStubs
FontSlider --> AutoScale

subgraph subGraph6 ["Resource Management"]
    ResourceResolver
    BackgroundCleanup
    AMMLStubs
end

subgraph subGraph5 ["Background Visualizer"]
    AMMLModule
    AMMLBackground
    BackgroundContainer
    BeatCurve
    AMMLModule --> AMMLBackground
    AMMLBackground --> BackgroundContainer
end

subgraph subGraph4 ["Animation Engine"]
    AutoScale
    LineMetrics
    AnimationSync
    FLIPRenderer
    FLIPRenderer --> AutoScale
    AutoScale --> LineMetrics
    LineMetrics --> AnimationSync
end

subgraph subGraph3 ["Font System"]
    FontParser
    FontDetector
    FontLoader
    FontAvailability
    FontParser --> FontDetector
    FontDetector --> FontLoader
    FontLoader --> FontAvailability
end

subgraph subGraph2 ["Lyric Processing"]
    LyricsData
    TranslationData
    DisplayedLines
    SyllableRenderer
    LyricsData --> SyllableRenderer
    TranslationData --> SyllableRenderer
    SyllableRenderer --> DisplayedLines
end

subgraph subGraph1 ["Real-time Data Sources"]
    SSEStream
    StateEndpoint
    AnimConfigEndpoint
end

subgraph subGraph0 ["AMLL Player HTML Document"]
    MainScript
    PlaybackClock
    LineClock
    LyricsContainer
    AudioElement
    FontSlider
    MainScript --> PlaybackClock
    MainScript --> LineClock
    AudioElement --> PlaybackClock
end
```

**Description:** The AMLL Player coordinates multiple subsystems: real-time data ingestion via SSE, dual-clock playback tracking (global and per-line), font detection and loading, FLIP-based animation rendering, and optional AMLL background visualization. The architecture separates concerns between data acquisition, processing, rendering, and presentation.

**Sources:** [templates/Lyrics-style.HTML-AMLL-v1.HTML L343-L495](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/templates/Lyrics-style.HTML-AMLL-v1.HTML#L343-L495)

 [templates/Lyrics-style.HTML-AMLL-v1.HTML L718-L751](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/templates/Lyrics-style.HTML-AMLL-v1.HTML#L718-L751)

 [templates/Lyrics-style.HTML-AMLL-v1.HTML L252-L258](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/templates/Lyrics-style.HTML-AMLL-v1.HTML#L252-L258)

---

## Communication Mechanisms

### Real-time Data Flow

```mermaid
sequenceDiagram
  participant AMLL Player
  participant /player/animation-config
  participant /amll/state
  participant /amll/stream
  participant Backend Core

  AMLL Player->>/player/animation-config: POST animation params
  /player/animation-config->>/player/animation-config: {entryDuration, moveDuration, exitDuration}
  /player/animation-config->>AMLL Player: Normalize to 600ms default
  AMLL Player->>/amll/state: Set useComputedDisappear
  /amll/state->>AMLL Player: Return synced config
  AMLL Player->>AMLL Player: GET initial state
  AMLL Player->>/amll/stream: requestStateOnce()
  /amll/stream->>AMLL Player: {song, lyrics, translation, progress}
  loop [Real-time Updates]
    Backend Core->>/amll/stream: Initialize lyricsData, translationData
    /amll/stream->>AMLL Player: syncPlaybackProgress()
    AMLL Player->>AMLL Player: Connect EventSource
    AMLL Player->>AMLL Player: AMLL_STREAM_URL
    Backend Core->>/amll/stream: connection: open
    /amll/stream->>AMLL Player: Push lyric line update
    AMLL Player->>AMLL Player: event: lyric data
  end
  AMLL Player->>AMLL Player: Update displayedLines
  AMLL Player->>AMLL Player: prepareSyllableAnimations()
  note over AMLL Player,/amll/stream: Lyrics refetch attempts up to
```

**Description:** The player establishes three communication channels: (1) Animation configuration sync via POST to `/player/animation-config` to align frontend-reported durations with backend calculations; (2) Initial state fetch from `/amll/state` to populate song metadata and lyrics; (3) SSE stream from `/amll/stream` for continuous lyric and progress updates. A periodic resync timer and retry mechanism ensure data consistency.

**Sources:** [templates/Lyrics-style.HTML-AMLL-v1.HTML L252-L258](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/templates/Lyrics-style.HTML-AMLL-v1.HTML#L252-L258)

 [templates/Lyrics-style.HTML-AMLL-v1.HTML L752-L783](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/templates/Lyrics-style.HTML-AMLL-v1.HTML#L752-L783)

 [templates/Lyrics-style.HTML-AMLL-v1.HTML L785-L809](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/templates/Lyrics-style.HTML-AMLL-v1.HTML#L785-L809)

---

## Playback Clock System

The player maintains dual clock systems for accurate synchronization:

| Clock | Purpose | Update Mechanism |
| --- | --- | --- |
| `playbackClock` | Global song progress | Updated by SSE events, smoothed with easing |
| `lineClock` | Per-line progress tracking | Updated for each new lyric line |

### Playback Clock Implementation

```mermaid
flowchart TD

SSEEvent["SSE Progress Event"]
SyncFunction["syncPlaybackProgress(progressMs)"]
CheckDelta["abs(delta) > 10ms?"]
CreateSmoothing["Create smoothing object<br>{start, duration, delta}"]
DirectUpdate["Direct update"]
EasingCalc["easeOutCubic(t)<br>during getCurrentPlaybackMs()"]
UpdateClock["Update playbackClock<br>{baseMs, lastUpdateAt}"]
AnimationFrame["requestAnimationFrame loop"]
GetCurrent["getCurrentPlaybackMs()"]
RenderLyrics["Render lyric animations"]

SSEEvent --> SyncFunction
SyncFunction --> CheckDelta
CheckDelta --> CreateSmoothing
CheckDelta --> DirectUpdate
CreateSmoothing --> EasingCalc
DirectUpdate --> UpdateClock
EasingCalc --> UpdateClock
UpdateClock --> AnimationFrame
AnimationFrame --> GetCurrent
GetCurrent --> RenderLyrics
```

**Description:** The `playbackClock` object tracks song position with smoothing to prevent jarring jumps. When a progress update arrives via SSE, if the delta exceeds 10ms, a smoothing animation is applied using `easeOutCubic` over a calculated duration (clamped between 180-900ms). The `getCurrentPlaybackMs()` function applies this smoothing in real-time during rendering.

**Sources:** [templates/Lyrics-style.HTML-AMLL-v1.HTML L718-L751](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/templates/Lyrics-style.HTML-AMLL-v1.HTML#L718-L751)

 [templates/Lyrics-style.HTML-AMLL-v1.HTML L752-L783](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/templates/Lyrics-style.HTML-AMLL-v1.HTML#L752-L783)

---

## Lyric Rendering System

### Data Structures

The player maintains several key data structures:

```javascript
// From template code
let lyricsData = [];        // Array of lyric line objects
let translationData = [];   // Array of translation line objects
let displayedLines = new Map(); // Map of currently displayed lines
```

Each lyric line object structure:

```yaml
{
  words: [
    {
      word: "text content",
      startTime: 1234,  // milliseconds
      endTime: 2345
    }
  ],
  startTime: 1234,
  endTime: 2345,
  translatedLyric: "translation text",
  romanLyric: "romanization text",
  isBG: false,          // background vocals flag
  isDuet: false         // duet flag
}
```

### Syllable Rendering Pipeline

```mermaid
flowchart TD

LyricUpdate["New Lyric Line Update"]
CheckVisible["Line in<br>display window?"]
Skip["Skip rendering"]
CreateElements["Create .lyric-line element"]
ParseFont["parseFontFamily()<br>Extract [font-family:...] tag"]
ProcessWords["Iterate over words array"]
GroupSyllables["groupSyllablesIntoWords()<br>Create .word-wrapper"]
ProcessSyllable["For each syllable"]
DetectScript["detectScriptType(word)<br>zh/ja/en/other"]
SelectFont["Select font from parsed meta"]
CheckAvailable["checkFontAvailability()"]
LoadFont["loadFont()<br>Local/Google/CDN"]
CreateSpan["Create .syllable"]
ApplyFont["Apply font-family CSS"]
SetupAnimation["Setup animation data<br>start/end times"]
AddToDOM["Add to lyricsContainer"]
ScheduleAutoScale["scheduleAutoScale()"]
MeasureMetrics["measureLineMetrics()"]
ComputeTarget["computeAutoScaleTarget()"]
ApplyScale["setLyricScale(targetScale)"]
RequestSync["requestLyricAnimationResync()"]
RefreshAnimations["refreshDisplayedLineAnimations()"]
PrepareAnimations["prepareSyllableAnimations()<br>Calculate gradients & transforms"]

LyricUpdate --> CheckVisible
CheckVisible --> Skip
CheckVisible --> CreateElements
CreateElements --> ParseFont
ParseFont --> ProcessWords
ProcessWords --> GroupSyllables
GroupSyllables --> ProcessSyllable
ProcessSyllable --> DetectScript
DetectScript --> SelectFont
SelectFont --> CheckAvailable
CheckAvailable --> LoadFont
CheckAvailable --> CreateSpan
LoadFont --> CreateSpan
CreateSpan --> ApplyFont
ApplyFont --> SetupAnimation
SetupAnimation --> AddToDOM
AddToDOM --> ScheduleAutoScale
ScheduleAutoScale --> MeasureMetrics
MeasureMetrics --> ComputeTarget
ComputeTarget --> ApplyScale
ApplyScale --> RequestSync
RequestSync --> RefreshAnimations
RefreshAnimations --> PrepareAnimations
```

**Description:** The syllable rendering pipeline processes each lyric line by: (1) extracting font metadata tags, (2) grouping syllables into word wrappers, (3) detecting script type per syllable, (4) loading appropriate fonts, (5) creating animated span elements, (6) auto-scaling to fit viewport, and (7) preparing FLIP animations with gradient backgrounds and transforms.

**Sources:** [templates/Lyrics-style.HTML-AMLL-v1.HTML L534-L554](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/templates/Lyrics-style.HTML-AMLL-v1.HTML#L534-L554)

 [templates/Lyrics-style.HTML-AMLL-v1.HTML L632-L705](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/templates/Lyrics-style.HTML-AMLL-v1.HTML#L632-L705)

 [templates/Lyrics-style.HTML-AMLL-v1.HTML L707-L716](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/templates/Lyrics-style.HTML-AMLL-v1.HTML#L707-L716)

---

## Font System

### Font Meta Tag Format

The player supports inline font metadata tags in lyric text:

```markdown
[font-family:FontName]                    # Global default font
[font-family:EnFont(en),JaFont(ja)]      # Per-script fonts
[font-family:Main(en),Sub(ja),Fallback]  # Multiple with fallback
[font-family:]                            # Clear font, restore default
```

### Font Loading Pipeline

```mermaid
flowchart TD

ParseTag["parseFontFamily(text)<br>Extract font directives"]
BuildMap["Build font map<br>{en: FontA, ja: FontB, ...}"]
GetSyllable["For each syllable"]
DetectScript["detectScriptType(word)"]
CJK["CJK chars?"]
SetCJK["Set 'zh' or 'ja'"]
Latin["Latin chars?"]
SetLatin["Set 'en'"]
SetOther["Set 'other'"]
LookupFont["Lookup font in map by script"]
CheckAvail["checkFontAvailability(fontName)"]
FontLoaded["Font available?"]
TryLocal["Try local path<br>/songs/, /fonts/"]
ApplyFont["Apply font-family CSS"]
LocalSuccess["Load success?"]
TryGoogle["Try Google Fonts<br>fonts.googleapis.com"]
GoogleSuccess["Load success?"]
TryCDN["Try CDN<br>fonts.cdnfonts.com"]
CDNSuccess["Load success?"]
UseFallback["Use system fallback"]
CheckSpecial["Special font?"]
PureColor["Use pure color mode<br>No gradient"]
NormalRender["Normal gradient render"]

ParseTag --> BuildMap
BuildMap --> GetSyllable
GetSyllable --> DetectScript
DetectScript --> CJK
CJK --> SetCJK
CJK --> Latin
Latin --> SetLatin
Latin --> SetOther
SetCJK --> LookupFont
SetLatin --> LookupFont
SetOther --> LookupFont
LookupFont --> CheckAvail
CheckAvail --> FontLoaded
FontLoaded --> TryLocal
FontLoaded --> ApplyFont
TryLocal --> LocalSuccess
LocalSuccess --> TryGoogle
LocalSuccess --> ApplyFont
TryGoogle --> GoogleSuccess
GoogleSuccess --> TryCDN
GoogleSuccess --> ApplyFont
TryCDN --> CDNSuccess
CDNSuccess --> ApplyFont
CDNSuccess --> UseFallback
UseFallback --> ApplyFont
ApplyFont --> CheckSpecial
CheckSpecial --> PureColor
CheckSpecial --> NormalRender
```

**Description:** Font loading implements a multi-tier fallback strategy. The player first parses `[font-family:...]` tags to build a script-to-font map. For each syllable, it detects the script type (Chinese, Japanese, English, or other) and selects the appropriate font. If the font is unavailable, it attempts loading from: (1) local paths (`/songs/`, `/fonts/`), (2) Google Fonts API, (3) CDN sources. Special fonts (detected via name patterns) use pure color rendering instead of gradients for optimal per-syllable animation.

**Sources:** [templates/Lyrics-style.HTML-AMLL-v1.HTML L990-L1017](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/templates/Lyrics-style.HTML-AMLL-v1.HTML#L990-L1017)

 (resolveMediaUrl), inline font parsing logic (in truncated section)

---

## Animation System

### Auto-scaling Algorithm

```mermaid
flowchart TD

Trigger["scheduleAutoScale() trigger"]
RAF["requestAnimationFrame"]
GetViewport["Get viewport height<br>window.innerHeight"]
SubtractPadding["availableHeight =<br>viewportHeight - SAFETY_PADDING"]
GetLines["Query all .lyric-line elements"]
ClassifyLines["Classify by type:<br>normal/small/normalTrans/smallTrans"]
UseMetrics["Use measureLineMetrics()<br>Pre-measured heights"]
CalcSum["baseSum =<br>Σ(count × metric height)"]
CheckZero["baseSum == 0?"]
ClampDefault["Clamp to userLyricScale"]
CalcBaseline["baselineTotal =<br>baseSum × userLyricScale"]
CalcRatio["ratio =<br>availableHeight / baselineTotal"]
ApplySafety["adjustedRatio =<br>ratio × AUTO_SCALE_SAFETY_RATIO"]
ClampTarget["target = clamp(<br>userScale × adjustedRatio,<br>LYRIC_SCALE_MIN,<br>userLyricScale)"]
CheckEps["abs(target - applied)<br>< AUTO_SCALE_EPS?"]
SkipUpdate["Skip update"]
SetScale["setLyricScale(target)"]
UpdateCSS["Set --lyric-scale CSS var"]
TriggerResync["requestLyricAnimationResync()"]
RefreshAnims["refreshDisplayedLineAnimations()"]

Trigger --> RAF
RAF --> GetViewport
GetViewport --> SubtractPadding
SubtractPadding --> GetLines
GetLines --> ClassifyLines
ClassifyLines --> UseMetrics
UseMetrics --> CalcSum
CalcSum --> CheckZero
CheckZero --> ClampDefault
CheckZero --> CalcBaseline
CalcBaseline --> CalcRatio
CalcRatio --> ApplySafety
ApplySafety --> ClampTarget
ClampTarget --> CheckEps
CheckEps --> SkipUpdate
CheckEps --> SetScale
SetScale --> UpdateCSS
UpdateCSS --> TriggerResync
TriggerResync --> RefreshAnims
ClampDefault --> SetScale
```

**Description:** The auto-scaling system ensures all displayed lyrics fit within the viewport without overflow. It measures the cumulative height of all visible lines (categorized by normal/small and with/without translation), calculates a target scale factor based on available vertical space with a safety ratio (0.97) and padding (48px), then applies the scale via CSS custom property `--lyric-scale`. The algorithm uses epsilon-based change detection (AUTO_SCALE_EPS = 0.01) to avoid unnecessary re-renders.

**Sources:** [templates/Lyrics-style.HTML-AMLL-v1.HTML L515-L570](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/templates/Lyrics-style.HTML-AMLL-v1.HTML#L515-L570)

 [templates/Lyrics-style.HTML-AMLL-v1.HTML L572-L630](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/templates/Lyrics-style.HTML-AMLL-v1.HTML#L572-L630)

 [templates/Lyrics-style.HTML-AMLL-v1.HTML L632-L705](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/templates/Lyrics-style.HTML-AMLL-v1.HTML#L632-L705)

### FLIP Animation Architecture

The player uses the FLIP (First, Last, Invert, Play) technique for performant animations:

| Phase | Operation | Implementation |
| --- | --- | --- |
| **First** | Record initial state | Cache syllable positions and sizes |
| **Last** | Apply final state | Update DOM with new lyrics |
| **Invert** | Calculate delta | Compute transform offset |
| **Play** | Animate transition | Apply CSS transition with easing |

Key animation properties per syllable:

* `transform: translateY()` - Vertical floating animation
* `background-position` - Gradient sweep animation
* `opacity` - Fade in/out transitions
* `filter: blur()` - Optional blur effect

**Sources:** [templates/Lyrics-style.HTML-AMLL-v1.HTML L534-L554](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/templates/Lyrics-style.HTML-AMLL-v1.HTML#L534-L554)

 (refreshDisplayedLineAnimations)

---

## Background Visualizer

### AMLL Module Integration

```mermaid
flowchart TD

InitRequest["prepareAmllBackground()"]
CheckPromise["Promise exists?"]
ReturnExisting["Return existing promise"]
StartInit["Start initialization"]
EnsureCompat["ensureMediaCapabilitiesCompat()<br>Patch MediaCapabilities.encodingInfo"]
EnsureStubs["ensureAmllStubs()<br>Create stub DOM elements"]
CheckGlobal["window.globalBackground<br>exists?"]
ImportModule["import(AMLL_MODULE_URL)<br>amll-player.js"]
WaitFor["waitFor(() => globalBackground)"]
GetBackground["Get globalBackground object"]
RestoreStubs["Restore original<br>document.getElementById"]
ExtractElement["background.getElement()"]
StyleElement["Set position, size, opacity"]
RemoveAudio["Remove AMLL internal audio element"]
ReturnBackground["Return background object"]
InitFromAlbum["initAmllBackgroundFromAlbum(coverPath)"]
ResolveURL["resolveMediaUrl(coverPath)"]
CreateContainer["Create .background-container"]
AppendElement["Append background element"]
SetAlbum["background.setAlbum(url)"]
Configure["Configure:<br>setStaticMode(false)<br>setFlowSpeed(4)<br>setRenderScale(dpr)"]
Resume["background.resume()"]
AttachPalette["attachAmllPaletteDriver(background)"]
CleanupOld["Cleanup old background"]

InitRequest --> CheckPromise
CheckPromise --> ReturnExisting
CheckPromise --> StartInit
StartInit --> EnsureCompat
EnsureCompat --> EnsureStubs
EnsureStubs --> CheckGlobal
CheckGlobal --> ImportModule
CheckGlobal --> WaitFor
ImportModule --> WaitFor
WaitFor --> GetBackground
GetBackground --> RestoreStubs
RestoreStubs --> ExtractElement
ExtractElement --> StyleElement
StyleElement --> RemoveAudio
RemoveAudio --> ReturnBackground
ReturnBackground --> InitFromAlbum
InitFromAlbum --> ResolveURL
ResolveURL --> CreateContainer
CreateContainer --> AppendElement
AppendElement --> SetAlbum
SetAlbum --> Configure
Configure --> Resume
Resume --> AttachPalette
AttachPalette --> CleanupOld
```

**Description:** The AMLL background visualizer is loaded dynamically from `amll-player.js`. The initialization process: (1) patches `MediaCapabilities.encodingInfo` for compatibility, (2) creates stub DOM elements (skeleton structure) to satisfy AMLL's expectations, (3) imports the module and extracts `globalBackground`, (4) restores original DOM methods, (5) configures the background element with album art, flow speed (4), and device pixel ratio scaling, (6) attaches palette driver for beat synchronization.

**Sources:** [templates/Lyrics-style.HTML-AMLL-v1.HTML L915-L953](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/templates/Lyrics-style.HTML-AMLL-v1.HTML#L915-L953)

 (ensureMediaCapabilitiesCompat), [templates/Lyrics-style.HTML-AMLL-v1.HTML L874-L913](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/templates/Lyrics-style.HTML-AMLL-v1.HTML#L874-L913)

 (ensureAmllStubs), [templates/Lyrics-style.HTML-AMLL-v1.HTML L1019-L1081](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/templates/Lyrics-style.HTML-AMLL-v1.HTML#L1019-L1081)

 (prepareAmllBackground), [templates/Lyrics-style.HTML-AMLL-v1.HTML L1083-L1150](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/templates/Lyrics-style.HTML-AMLL-v1.HTML#L1083-L1150)

 (initAmllBackgroundFromAlbum)

### AMLL Stub System

The player creates stub DOM elements to satisfy AMLL module dependencies:

```javascript
const AMLL_SKELETON_IDS = new Set([
  'player', 'lyricsPanel', 'albumSidePanel',
  'songTitle', 'songArtist', 'albumInfo',
  'albumCoverContainer', 'albumCoverLarge',
  'progressBar', 'progressFill', 'waveformCanvas'
]);
```

The `ensureAmllSkeleton()` function generates a hidden skeleton structure containing these elements. The `document.getElementById` method is patched to return stub elements when AMLL module queries for them, then restored after initialization completes.

**Sources:** [templates/Lyrics-style.HTML-AMLL-v1.HTML L501-L513](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/templates/Lyrics-style.HTML-AMLL-v1.HTML#L501-L513)

 (AMLL_SKELETON_IDS), [templates/Lyrics-style.HTML-AMLL-v1.HTML L833-L872](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/templates/Lyrics-style.HTML-AMLL-v1.HTML#L833-L872)

 (ensureAmllSkeleton), [templates/Lyrics-style.HTML-AMLL-v1.HTML L874-L913](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/templates/Lyrics-style.HTML-AMLL-v1.HTML#L874-L913)

 (ensureAmllStubs)

---

## Resource Management

### URL Resolution

The `resolveMediaUrl()` function normalizes resource paths with special handling for localhost addresses:

```mermaid
flowchart TD

Input["resolveMediaUrl(rawPath)"]
Validate["Valid string?"]
ReturnNull["Return null"]
Trim["Trim whitespace"]
RemovePrefix["Remove leading './'"]
CheckScheme["Scheme type?"]
ReturnDirect["Return as-is"]
ParseURL["Parse URL object"]
MakeRelative["Prepend '/' if needed"]
CheckLocalhost["Hostname matches<br>localhost pattern?"]
ReplaceHost["Replace with<br>window.location values"]
ReturnURL["Return URL string"]
SetProtocol["Set window.location.protocol"]
SetHost["Set window.location.hostname"]
SetPort["Set window.location.port"]

Input --> Validate
Validate --> ReturnNull
Validate --> Trim
Trim --> RemovePrefix
RemovePrefix --> CheckScheme
CheckScheme --> ReturnDirect
CheckScheme --> ParseURL
CheckScheme --> MakeRelative
ParseURL --> CheckLocalhost
CheckLocalhost --> ReplaceHost
CheckLocalhost --> ReturnURL
ReplaceHost --> SetProtocol
SetProtocol --> SetHost
SetHost --> SetPort
SetPort --> ReturnURL
MakeRelative --> ReturnURL
```

**Description:** The resource resolver handles multiple URL formats: data URIs and blob URLs pass through unchanged; HTTP(S) URLs are parsed and localhost addresses (127.x.x.x, localhost, ::1, 0.0.0.0) are rewritten to use the current page's protocol, hostname, and port for proper proxying; relative paths are normalized to absolute paths starting with `/`.

**Sources:** [templates/Lyrics-style.HTML-AMLL-v1.HTML L990-L1017](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/templates/Lyrics-style.HTML-AMLL-v1.HTML#L990-L1017)

### Background Cleanup System

The player maintains a `backgroundCleanup` callback that manages lifecycle:

```javascript
backgroundCleanup = () => {
  // Stop AMLL background if active
  background.pause();
  stopAmllPaletteLoop({ clearBackground: true });
  
  // Remove from DOM
  element.parentElement.removeChild(element);
  
  // Return to stub host
  amllBackgroundState.host.appendChild(element);
  
  // Remove container
  container.remove();
};
backgroundCleanup.__kind = 'amll'; // or 'media' for video/image
```

Different cleanup types handle AMLL backgrounds vs. static media (video/image) backgrounds.

**Sources:** [templates/Lyrics-style.HTML-AMLL-v1.HTML L979-L988](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/templates/Lyrics-style.HTML-AMLL-v1.HTML#L979-L988)

 (removeExistingBackground), [templates/Lyrics-style.HTML-AMLL-v1.HTML L1102-L1114](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/templates/Lyrics-style.HTML-AMLL-v1.HTML#L1102-L1114)

 (AMLL cleanup), [templates/Lyrics-style.HTML-AMLL-v1.HTML L1209-L1213](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/templates/Lyrics-style.HTML-AMLL-v1.HTML#L1209-L1213)

 (media cleanup)

---

## Beat Visualization

### Beat Curve Loading

```mermaid
flowchart TD

LoadRequest["loadBeatCurve(beatPath)"]
ResolveURL["resolveMediaUrl(beatPath)"]
Fetch["fetch(url)"]
CheckStatus["HTTP 200?"]
SetNull["amllColorState.beatCurve = null"]
GetBuffer["arrayBuffer()"]
Parse["parseBeatCurve(buffer)"]
Validate["Valid curve?"]
Store["Store in amllColorState.beatCurve"]
AttachDriver["Can attach palette driver"]

LoadRequest --> ResolveURL
ResolveURL --> Fetch
Fetch --> CheckStatus
CheckStatus --> SetNull
CheckStatus --> GetBuffer
GetBuffer --> Parse
Parse --> Validate
Validate --> SetNull
Validate --> Store
Store --> AttachDriver
```

**Description:** Beat curves provide rhythm data for background animation. The player fetches beat curve files (binary format), parses them via `parseBeatCurve()`, and stores the result in `amllColorState.beatCurve`. This data drives the AMLL palette synchronization via `attachAmllPaletteDriver()`.

**Sources:** [templates/Lyrics-style.HTML-AMLL-v1.HTML L1152-L1175](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/templates/Lyrics-style.HTML-AMLL-v1.HTML#L1152-L1175)

### Background Control UI

The player provides a context menu (right-click) to toggle beat synchronization:

| Control | Function | Implementation |
| --- | --- | --- |
| Enable/Disable Beat | Toggle `amllBeatEnabled` flag | `setAmllBeatEnabled(boolean)` |
| Resume Background | Start AMLL palette loop | `attachAmllPaletteDriver()` |
| Pause Background | Stop palette loop | `stopAmllPaletteLoop()` |

The context menu button label updates dynamically: "开启律动背景" (Enable Background Beat) or "关闭律动背景" (Disable Background Beat).

**Sources:** [templates/Lyrics-style.HTML-AMLL-v1.HTML L362-L468](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/templates/Lyrics-style.HTML-AMLL-v1.HTML#L362-L468)

 (context menu creation and handlers)

---

## Configuration and State Management

### LocalStorage Protection

The `guardAmllSettingsStorage()` function protects AMLL settings from being cleared:

```mermaid
flowchart TD

Init["guardAmllSettingsStorage()"]
AccessStorage["Access window.localStorage"]
SaveOriginal["Save original methods:<br>getItem, setItem, removeItem, clear"]
ReadMirror["Read current AMLL_SETTINGS_STORAGE_KEY"]
CreateMirror["Create mirror variable"]
PatchGet["Patch getItem:<br>If key matches, return mirror"]
PatchSet["Patch setItem:<br>If key matches, update mirror"]
PatchRemove["Patch removeItem:<br>If key matches, clear mirror"]
PatchClear["Patch clear:<br>Restore AMLL key after clear"]
ReturnRestore["Return restore function"]
OnUnload["On window.beforeunload"]
Restore["Call restore() to unpatch"]

Init --> AccessStorage
AccessStorage --> SaveOriginal
SaveOriginal --> ReadMirror
ReadMirror --> CreateMirror
CreateMirror --> PatchGet
PatchGet --> PatchSet
PatchSet --> PatchRemove
PatchRemove --> PatchClear
PatchClear --> ReturnRestore
ReturnRestore --> OnUnload
OnUnload --> Restore
```

**Description:** The storage guard intercepts all localStorage operations. When other code calls `localStorage.clear()`, the AMLL settings are preserved in the `mirror` variable and restored immediately after the clear operation. This prevents settings loss from bulk storage clearing.

**Sources:** [templates/Lyrics-style.HTML-AMLL-v1.HTML L260-L337](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/templates/Lyrics-style.HTML-AMLL-v1.HTML#L260-L337)

### Animation Configuration Sync

The player POSTs animation parameters to `/player/animation-config` on initialization. The endpoint normalizes durations to a default of 600ms and returns a configuration object:

```yaml
{
  entryDuration: 600,      // Entry animation duration (ms)
  moveDuration: 600,       // Move/transition duration (ms)
  exitDuration: 600,       // Exit animation duration (ms)
  useComputedDisappear: true  // Whether to use backend-computed disappear times
}
```

The `useComputedDisappear` flag controls whether disappear times are calculated by the backend or by client-side animation logic.

**Sources:** Referenced in README.md description of animation-config endpoint

---

## Lyrics Refetch Mechanism

```mermaid
flowchart TD

Check["ensureLyricsRefetch()"]
HasTimer["Timer exists?"]
ClearTimer["clearTimeout(timer)"]
StartTimer["setTimeout(..., INTERVAL)"]
WaitInterval["Wait LYRICS_REFETCH_INTERVAL_MS<br>(1200ms)"]
CheckData["lyricsData.length > 0?"]
Reset["resetLyricsRefetch()"]
CheckAttempts["attempts >=<br>MAX_ATTEMPTS (4)?"]
StopRetry["Stop retrying"]
IncrementAttempts["Increment attempts"]
RequestState["requestStateOnce()"]
Recurse["ensureLyricsRefetch() again"]
ClearAttempts["Reset attempts to 0"]
ClearTimer2["Clear timer"]

Check --> HasTimer
HasTimer --> ClearTimer
HasTimer --> StartTimer
ClearTimer --> StartTimer
StartTimer --> WaitInterval
WaitInterval --> CheckData
CheckData --> Reset
CheckData --> CheckAttempts
CheckAttempts --> StopRetry
CheckAttempts --> IncrementAttempts
IncrementAttempts --> RequestState
RequestState --> Recurse
Reset --> ClearAttempts
ClearAttempts --> ClearTimer2
```

**Description:** The lyrics refetch mechanism ensures lyrics are loaded even if the initial load fails. If `lyricsData` remains empty after the first state request, it retries up to `LYRICS_REFETCH_MAX_ATTEMPTS` (4) times with `LYRICS_REFETCH_INTERVAL_MS` (1200ms) delays between attempts. This handles race conditions and temporary network issues.

**Sources:** [templates/Lyrics-style.HTML-AMLL-v1.HTML L785-L809](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/templates/Lyrics-style.HTML-AMLL-v1.HTML#L785-L809)

---

## URL Parameters and Customization

The player accepts URL query parameters for customization:

| Parameter | Purpose | Example |
| --- | --- | --- |
| `background` | Override background resource | `?background=/path/to/video.mp4` |
| `cover` | Override album cover | `?cover=/path/to/image.jpg` |

These parameters are extracted via `URLSearchParams`:

```javascript
const urlParams = new URLSearchParams(window.location.search);
const queryBackground = urlParams.get('background') || null;
const queryCover = urlParams.get('cover') || null;
```

The values override song metadata when loading backgrounds and covers.

**Sources:** [templates/Lyrics-style.HTML-AMLL-v1.HTML L498-L499](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/templates/Lyrics-style.HTML-AMLL-v1.HTML#L498-L499)

---

## Font Slider for Mobile Devices

The player includes a font size slider that appears only on mobile/touch devices:

```html
<div class="font-slider-container">
    <input type="range" min="0.5" max="1.5" value="1" step="0.05" id="fontSlider">
</div>
```

CSS media query enables the slider on small screens:

```
@media screen and (max-width: 768px), screen and (orientation: portrait) {
    .font-slider-container {
        display: block;
    }
}
```

The slider controls `userLyricScale`, which serves as the upper bound for auto-scaling. Users can adjust base font size, and auto-scaling further reduces it to fit the viewport if necessary.

**Sources:** [templates/Lyrics-style.HTML-AMLL-v1.HTML L226-L240](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/templates/Lyrics-style.HTML-AMLL-v1.HTML#L226-L240)

 (CSS), [templates/Lyrics-style.HTML-AMLL-v1.HTML L247-L249](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/templates/Lyrics-style.HTML-AMLL-v1.HTML#L247-L249)

 (HTML), [templates/Lyrics-style.HTML-AMLL-v1.HTML L528-L529](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/templates/Lyrics-style.HTML-AMLL-v1.HTML#L528-L529)

 (userLyricScale)

---

## Performance Optimizations

The player implements several performance optimizations:

| Optimization | Technique | Benefit |
| --- | --- | --- |
| **FLIP Animations** | Record-transform-animate pattern | 60 FPS smooth animations |
| **Line Metric Caching** | Pre-measure template elements | Avoid layout thrashing |
| **RAF Throttling** | `autoScaleRaf` ensures single request | Prevent excessive scaling calculations |
| **Epsilon-based Updates** | Skip updates if delta < `AUTO_SCALE_EPS` | Reduce DOM mutations |
| **Smoothed Clock** | Ease progress jumps over time | Visually smooth playback |
| **Font Availability Check** | Test with canvas before loading | Skip unnecessary font fetches |

**Sources:** [templates/Lyrics-style.HTML-AMLL-v1.HTML L523-L526](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/templates/Lyrics-style.HTML-AMLL-v1.HTML#L523-L526)

 (epsilon constants), [templates/Lyrics-style.HTML-AMLL-v1.HTML L572-L630](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/templates/Lyrics-style.HTML-AMLL-v1.HTML#L572-L630)

 (measureLineMetrics caching), [templates/Lyrics-style.HTML-AMLL-v1.HTML L707-L716](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/templates/Lyrics-style.HTML-AMLL-v1.HTML#L707-L716)

 (scheduleAutoScale RAF)

---

## Summary

The AMLL Player provides a comprehensive lyric display solution with:

* **Real-time synchronization** via SSE with dual-clock playback tracking
* **Advanced font system** with per-syllable script detection and multi-source loading (local/Google Fonts/CDN)
* **FLIP-based animations** for performant per-syllable rendering with auto-scaling
* **AMLL background visualizer** with dynamic album art and beat synchronization
* **Resource management** with URL normalization and cleanup lifecycle
* **Robust error handling** with lyrics refetch mechanism and fallback strategies

The player is accessible at `/templates/Lyrics-style.HTML-AMLL-v1.HTML` and integrates with backend systems via `/amll/state`, `/amll/stream`, and `/player/animation-config` endpoints.

**Sources:** [templates/Lyrics-style.HTML-AMLL-v1.HTML L1-L250](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/templates/Lyrics-style.HTML-AMLL-v1.HTML#L1-L250)

 [README.md L1-L172](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/README.md#L1-L172)