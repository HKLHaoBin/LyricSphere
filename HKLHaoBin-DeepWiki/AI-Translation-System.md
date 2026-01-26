# AI Translation System

> **Relevant source files**
> * [CHANGELOG.md](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/CHANGELOG.md)
> * [LICENSE](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/LICENSE)
> * [README.md](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/README.md)
> * [backend.py](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py)

The AI Translation System provides AI-powered lyric translation capabilities with support for multiple AI providers (DeepSeek, OpenAI, OpenRouter, Together, Groq). The system handles translation requests with streaming responses, automatic timestamp alignment, issue detection, and quality validation. It includes sophisticated SSL connection handling, optional thinking model integration for improved translation quality, and compatibility mode for models with limited multi-role support.

For information about lyric format conversion, see [Format Conversion Pipeline](/HKLHaoBin/LyricSphere/2.3-format-conversion-pipeline). For details on real-time lyric delivery to players, see [Real-time Communication](/HKLHaoBin/LyricSphere/2.5-real-time-communication). For the frontend translation interface, see [Lyrics Translation Interface](/HKLHaoBin/LyricSphere/3.5-lyrics-translation-interface).

---

## System Architecture

The AI Translation System is implemented as a backend service that accepts translation requests, processes them through configured AI providers, and returns synchronized translated lyrics. The architecture separates concerns into provider selection, SSL handling, prompt construction, streaming execution, and post-processing validation.

```

```

**Sources:** [backend.py L890-L947](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L890-L947)

 Diagram 7 from system overview

---

## AI Provider Configuration

The system uses OpenAI-compatible clients for all AI providers, allowing unified handling of different services. Each provider requires an API key and base URL, with the provider type determining routing behavior.

| Provider | Base URL | Key Format | Special Features |
| --- | --- | --- | --- |
| DeepSeek | `https://api.deepseek.com/v1` | `sk-...` | Reasoning chain extraction for `deepseek-reasoner` |
| OpenAI | `https://api.openai.com/v1` | `sk-...` | Full GPT model support |
| OpenRouter | `https://openrouter.ai/api/v1` | Various | Unified access to multiple models |
| Together | `https://api.together.xyz/v1` | API key | Open-source model support |
| Groq | `https://api.groq.com/openai/v1` | `gsk_...` | Fast inference optimization |

Configuration parameters are passed in the translation request and include:

* `ai_provider` - Provider identifier (deepseek, openai, openrouter, together, groq)
* `ai_api_key` - Authentication key for the selected provider
* `ai_base_url` - API base URL (can be customized)
* `ai_model` - Model name to use for translation
* `ai_system_prompt` - Custom system instructions for translation style
* `ai_max_tokens` - Maximum tokens in response
* `ai_temperature` - Sampling temperature for creativity control
* `compatibility_mode` - Boolean flag to merge system prompt into user message
* `strip_brackets` - Boolean flag to remove bracketed content from lyrics
* `thinking_model` - Optional model for pre-analysis stage
* `thinking_api_key` - API key for thinking model (if different)
* `thinking_base_url` - Base URL for thinking model (if different)

**Sources:** [backend.py L910-L947](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L910-L947)

 README.md, Diagram 7 from system overview

---

## SSL and Connection Handling

The system implements a three-tier fallback strategy for SSL certificate handling to maximize reliability across different environments.

### Connection Strategy

```

```

### Implementation Details

The `cleanup_missing_ssl_cert_env` function removes environment variables pointing to non-existent certificate files:

* `SSL_CERT_FILE` - Removed if file doesn't exist
* `REQUESTS_CA_BUNDLE` - Removed if file doesn't exist
* `CURL_CA_BUNDLE` - Removed if file doesn't exist
* `SSL_CERT_DIR` - Removed if directory doesn't exist

The `build_openai_client` function creates an OpenAI client with resilient SSL setup:

1. **First attempt**: Use default SSL context
2. **Second attempt**: Clean invalid SSL environment variables and retry
3. **Third attempt**: Import `certifi` and use its CA bundle via custom `httpx.Client`
4. **Final fallback**: Disable SSL verification with warning log

```

```

**Sources:** [backend.py L890-L907](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L890-L907)

 [backend.py L910-L947](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L910-L947)

---

## Bracket Preprocessing

Before translation, lyrics can optionally have bracketed content removed using the `strip_brackets` configuration flag. The preprocessing uses a high-performance character translation table rather than regular expressions.

### Processing Logic

When `strip_brackets=True`:

1. All bracket characters (`[]`, `()`, `{}`, `【】`, `「」`, `『』`) are mapped to empty string
2. Content inside brackets is preserved but brackets are removed
3. Multiple consecutive spaces are collapsed to single space
4. Leading/trailing whitespace is trimmed

This preprocessing helps AI models focus on core lyric content without annotation noise.

**Sources:** CHANGELOG.md (v1.5.7), README.md

---

## Prompt Construction

The system constructs prompts based on the compatibility mode setting and optional thinking model configuration.

### Standard Mode (Multi-Role)

In standard mode, the system sends separate system and user messages:

```
System Message: {ai_system_prompt}
User Message: {lyrics_content}
```

### Compatibility Mode (Single-Role)

When `compatibility_mode=True`, the system prompt is merged into the user message to support models that only accept single-role input:

```
User Message: {ai_system_prompt}\n\n{lyrics_content}
```

### Thinking Model Integration

When a thinking model is configured, translation proceeds in two stages:

1. **Analysis Stage**: Thinking model analyzes lyrics and provides understanding context
2. **Translation Stage**: Primary model uses thinking model output to inform translation

The thinking model output (reasoning chain) is extracted and can be logged for debugging.

**Sources:** CHANGELOG.md (v1.5.2, v1.5.4), README.md

---

## Stream Processing

The translation uses streaming API calls to provide real-time progress feedback. The system parses chunks as they arrive and emits progress events to the client.

### Chunk Processing Flow

```

```

### Progress Event Types

The system emits multiple progress states during translation:

* **Connecting**: Establishing connection to AI provider
* **Analyzing**: Thinking model processing (if enabled)
* **Translating**: Primary translation in progress
* **Processing**: Post-processing and validation
* **Complete**: Translation finished with issue highlights
* **Error**: Translation failed with error details

**Sources:** CHANGELOG.md (v1.5.8), README.md, Diagram 7 from system overview

---

## Post-processing and Validation

After translation completes, the system performs several validation and synchronization steps.

### Timestamp Synchronization

The system aligns timestamps from the translated output with the original lyrics:

1. Parse timestamps from both original and translated text
2. Match line counts between original and translation
3. Preserve original timestamp values in translation
4. Handle missing timestamps by generating structure

### Issue Detection

The system detects and highlights problematic lines:

| Issue Type | Detection Method | Action |
| --- | --- | --- |
| Missing translation | Line is identical to original or empty | Highlight in editor |
| Timestamp mismatch | Line count differs between original and translation | Report error with line numbers |
| Incorrect characters | Contains unexpected characters for target language | Highlight specific lines |
| No timestamps | Original lacks timestamps | Add default structure |

### Problem Line Highlighting

When issues are detected, the system returns:

* `issues` - Array of issue descriptions
* `problemLines` - Array of line indices to highlight in the editor
* `translatedLyrics` - The translation result (possibly with issues)

The frontend uses this information to highlight problem lines with visual indicators, allowing users to quickly identify and fix issues.

**Sources:** CHANGELOG.md (v1.5.8), README.md, Diagram 7 from system overview

---

## API Health Check

The system includes a health check mechanism for AI providers that gracefully handles services not supporting the `/v1/models` endpoint.

### Liveness Check Logic

```

```

This compatibility ensures the system works with both fully OpenAI-compatible providers and those with custom implementations.

**Sources:** CHANGELOG.md (v1.5.8), README.md

---

## Translation Configuration Reference

### Core Parameters

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `ai_provider` | string | required | Provider identifier (deepseek/openai/openrouter/together/groq) |
| `ai_api_key` | string | required | API authentication key |
| `ai_base_url` | string | provider default | API base URL |
| `ai_model` | string | required | Model name for translation |
| `ai_system_prompt` | string | built-in | Custom system instructions |
| `ai_max_tokens` | integer | 2048 | Maximum response tokens |
| `ai_temperature` | float | 0.7 | Sampling temperature (0.0-2.0) |

### Advanced Parameters

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `compatibility_mode` | boolean | false | Merge system prompt into user message |
| `strip_brackets` | boolean | false | Remove bracketed content before translation |
| `thinking_model` | string | null | Model for pre-analysis stage |
| `thinking_api_key` | string | null | API key for thinking model |
| `thinking_base_url` | string | null | Base URL for thinking model |

### Input Parameters

| Parameter | Type | Description |
| --- | --- | --- |
| `lyrics` | string | Original lyrics to translate |
| `target_language` | string | Target language for translation |
| `preserve_timestamps` | boolean | Keep original timestamps in output |

**Sources:** README.md, author notes, CHANGELOG.md

---

## Code Entity Reference

### Key Functions

| Function | Location | Purpose |
| --- | --- | --- |
| `cleanup_missing_ssl_cert_env()` | [backend.py L890-L907](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L890-L907) | Remove invalid SSL certificate environment variables |
| `build_openai_client(api_key, base_url)` | [backend.py L910-L947](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L910-L947) | Create OpenAI client with SSL fallback strategy |

### SSL Environment Variables

The system handles these environment variables:

* `SSL_CERT_FILE` - Path to SSL certificate file
* `REQUESTS_CA_BUNDLE` - CA bundle for requests library
* `CURL_CA_BUNDLE` - CA bundle for curl operations
* `SSL_CERT_DIR` - Directory containing SSL certificates

### External Dependencies

* `openai` - OpenAI Python client library
* `certifi` - CA certificate bundle (fallback)
* `httpx` - HTTP client for custom SSL configuration

**Sources:** [backend.py L40](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L40-L40)

 [backend.py L890-L947](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L890-L947)