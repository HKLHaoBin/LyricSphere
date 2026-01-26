# Security and Authentication

> **Relevant source files**
> * [CHANGELOG.md](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/CHANGELOG.md)
> * [CLAUDE.md](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/CLAUDE.md)
> * [LICENSE](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/LICENSE)
> * [README.md](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/README.md)
> * [backend.py](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py)

## Purpose and Scope

This document describes the security and authentication mechanisms implemented in LyricSphere's backend system. It covers device-based authentication, password protection, path validation, access control, and session management. These systems work together to protect sensitive operations while maintaining usability.

For information about API endpoint security implementation, see [API Endpoints Reference](/HKLHaoBin/LyricSphere/2.1-api-endpoints-reference). For file path resolution and resource management security, see [Path Security and Validation](/HKLHaoBin/LyricSphere/2.6.2-path-security-and-validation).

**Sources**: [backend.py L1-L1000](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L1-L1000)

---

## Security Architecture Overview

LyricSphere implements a multi-layered security architecture with six primary defense mechanisms:

```

```

**Security Layers**:

| Layer | Component | Purpose | Implementation |
| --- | --- | --- | --- |
| 1 | CORS Validation | Cross-origin access control | Origin matching, preflight handling |
| 2 | Session Management | User session tracking | FastAPI SessionMiddleware, ContextVar |
| 3 | Device Authentication | Trusted device management | bcrypt password hashing, device list |
| 4 | Access Control | Operation authorization | Local/remote detection, operation types |
| 5 | Path Security | Path traversal prevention | Filename sanitization, boundary checks |
| 6 | File Operations | Safe file handling | Automatic backups, version control |

**Sources**: [backend.py L1215-L1262](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L1215-L1262)

 [backend.py L466-L546](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L466-L546)

 [backend.py L997-L1063](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L997-L1063)

---

## Device Authentication System

### Authentication Flow

The device authentication system maintains a list of trusted devices and requires password verification for untrusted devices on first access:

```

```

### Trusted Device Management

Trusted devices are managed through frontend localStorage and backend session validation. The system does not persist trusted device lists on the backend; instead, the frontend maintains device identifiers and requests unlock when needed.

**Key Operations**:

* **Device Unlock**: Client submits password for verification
* **Trust Storage**: Frontend stores device trust status in localStorage
* **Session Validation**: Backend validates password using bcrypt on each unlock request

**Sources**: [backend.py L8](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L8-L8)

 (bcrypt import), [backend.py L861-L862](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L861-L862)

 (SessionMiddleware setup)

---

## Password Protection

### Password Hashing with bcrypt

LyricSphere uses bcrypt for password hashing, providing strong protection against brute-force attacks:

**Password Storage**:

```

```

**Verification Process**:

1. Client submits plaintext password
2. Backend retrieves stored bcrypt hash from configuration
3. bcrypt.checkpw() verifies password against hash
4. Result determines authentication success/failure

**Configuration**:

* Passwords stored as bcrypt hashes in application configuration
* No plaintext passwords are stored or logged
* Hash algorithm provides computational cost to slow brute-force attempts

**Security Properties**:

* **Salt**: Automatically generated unique salt per password
* **Cost Factor**: Configurable work factor for adaptive security
* **One-way**: Cryptographically infeasible to reverse hash to plaintext

**Sources**: [backend.py L8](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L8-L8)

 [backend.py L861-L862](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L861-L862)

---

## Access Control

### Local Access Restrictions

Sensitive operations are restricted to local connections to prevent unauthorized remote access:

```

```

### IPv4-Mapped Address Detection

The system handles IPv4-mapped IPv6 addresses (format: `::ffff:127.0.0.1`) to ensure local detection works correctly in dual-stack environments:

**Detection Logic**:

1. Check if address is IPv6 format
2. Detect `::ffff:` prefix indicating IPv4-mapped address
3. Extract embedded IPv4 address
4. Verify against loopback ranges (127.0.0.0/8, ::1)

### Operation Authorization

Operations are categorized by sensitivity level:

| Category | Operations | Access Control |
| --- | --- | --- |
| **Read** | List songs, view lyrics, download | Minimal restrictions |
| **Write** | Create song, edit lyrics, update metadata | Device authentication required |
| **Critical** | Delete song, import ZIP, export package, restore backup | Local access + authentication required |

**Sources**: [backend.py L364-L371](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L364-L371)

 (remote_addr property)

---

## Path Security and Validation

### Filename Sanitization

The `sanitize_filename` function removes dangerous characters from filenames to prevent filesystem attacks:

**Implementation**: [backend.py L997-L1004](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L997-L1004)

```

```

**Allowed Characters**:

* Word characters: `\w` (alphanumeric + underscore)
* Chinese characters: `\u4e00-\u9fa5`
* Safe punctuation: `-`, `_`, `.`, space
* Quote normalization: ASCII quotes converted to fullwidth quotes

### Path Traversal Prevention

LyricSphere implements comprehensive path validation to prevent directory traversal attacks:

```

```

### Resource Path Resolution Functions

**Core Functions**:

1. **`_normalize_relative_path(value: str) -> str`** [backend.py L1006-L1015](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L1006-L1015) * Normalizes path separators * Filters empty segments * Rejects `.` and `..` segments * Returns clean relative path
2. **`extract_resource_relative(value: str, resource: str) -> str`** [backend.py L1018-L1034](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L1018-L1034) * Extracts relative path from URL or filesystem path * Validates resource type against whitelist * Handles URL schemes and network locations * Strips resource prefix (e.g., `songs/`)
3. **`resolve_resource_path(value: str, resource: str) -> Path`** [backend.py L1037-L1047](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L1037-L1047) * Converts relative path to absolute filesystem path * Resolves symlinks and canonicalizes path * Performs boundary check using `relative_to()` * Raises `ValueError` on boundary violation

### Resource Whitelist

**Allowed Resource Types**:

| Resource Type | Base Directory | Purpose |
| --- | --- | --- |
| `static` | `BASE_PATH / 'static'` | General static files |
| `songs` | `STATIC_DIR / 'songs'` | Song files, lyrics, media |
| `backups` | `STATIC_DIR / 'backups'` | Backup versions |

**Directory Definitions**: [backend.py L988-L992](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L988-L992)

```

```

**Sources**: [backend.py L994-L1063](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L994-L1063)

---

## CORS Support

### Cross-Origin Resource Sharing Configuration

LyricSphere implements CORS to support frontend integration and cross-origin API access:

**Configuration**: [backend.py L1215-L1223](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L1215-L1223)

```

```

### Origin Matching

The `_match_cors_origin` function validates request origins against the allowed list:

**Implementation**: [backend.py L1225-L1232](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L1225-L1232)

* If `'*'` in allowed origins, accept any origin
* Otherwise, exact string match required
* Returns matched origin or None

### CORS Headers Application

CORS headers are applied to responses via the `apply_cors_headers` after_request hook:

**Preflight Handling**: [backend.py L1265-L1270](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L1265-L1270)

```

```

**Header Application**: [backend.py L1273-L1282](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L1273-L1282)

* Sets `Access-Control-Allow-Origin` based on matched origin
* Adds `Access-Control-Allow-Credentials: true`
* Specifies allowed headers via `Access-Control-Allow-Headers`
* Specifies allowed methods via `Access-Control-Allow-Methods`

**Sources**: [backend.py L1215-L1282](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L1215-L1282)

---

## Session Management

### Session Architecture

LyricSphere uses FastAPI's SessionMiddleware combined with custom proxy classes for session management:

```

```

### SessionMiddleware Setup

**Configuration**: [backend.py L861-L862](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L861-L862)

```

```

**Properties**:

* Sessions stored in signed cookies
* Secret key used for cookie signature verification
* Session data serialized as JSON

### SessionProxy Implementation

The `SessionProxy` class provides Flask-like session access:

**Implementation**: [backend.py L466-L546](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L466-L546)

**Key Methods**:

| Method | Signature | Purpose |
| --- | --- | --- |
| `_get_session()` | `() -> Dict[str, Any]` | Safely retrieves session dict or empty dict |
| `__getitem__()` | `(key: str) -> Any` | Gets session value, raises KeyError if missing |
| `__setitem__()` | `(key: str, value: Any)` | Sets session value |
| `get()` | `(key: str, default: Any) -> Any` | Gets value with default fallback |
| `pop()` | `(key: str, default: Any) -> Any` | Removes and returns value |
| `clear()` | `() -> None` | Clears all session data |
| `__contains__()` | `(key: str) -> bool` | Checks if key exists |

### RequestContext Integration

The `RequestContext` class encapsulates all request data:

**Implementation**: [backend.py L278-L427](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L278-L427)

**Key Properties**:

* `method`: HTTP method (GET, POST, etc.)
* `headers`: Request headers
* `cookies`: Cookie dictionary
* `args`: Query parameters
* `path`: URL path
* `remote_addr`: Client IP address
* `json`: Parsed JSON body (cached)
* `files`: Uploaded files (lazy-loaded)

**Context Storage**: [backend.py L51](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L51-L51)

```

```

**Sources**: [backend.py L51](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L51-L51)

 [backend.py L278-L546](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L278-L546)

 [backend.py L861-L862](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L861-L862)

---

## Complete Security Flow

### Request Processing Pipeline

The following diagram shows the complete security validation pipeline for a sensitive operation:

```

```

**Sources**: [backend.py L1235-L1282](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L1235-L1282)

 (middleware pipeline)

---

## Security Configuration

### Environment Variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `CORS_ALLOW_ORIGINS` | `*` | Comma-separated allowed origins or `*` for all |
| `CORS_ALLOW_HEADERS` | `Authorization,Content-Type` | Allowed request headers |
| `CORS_ALLOW_METHODS` | `GET,POST,PUT,DELETE,OPTIONS` | Allowed HTTP methods |

### Security Toggles

The security system can be configured for different deployment environments:

**Development Mode**:

* CORS set to `*` for easier testing
* Password authentication can be disabled (if configured)
* Verbose logging enabled

**Production Mode**:

* Specific CORS origins required
* Device authentication enforced
* Local access restrictions active
* Path validation strictly enforced

**Sources**: [backend.py L1215-L1223](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L1215-L1223)

---

## Security Best Practices

### Implemented Protections

1. **Password Security** * bcrypt hashing with automatic salt generation * No plaintext passwords stored or transmitted * Configurable work factor for adaptive security
2. **Path Security** * Whitelist-based resource access * Multiple validation stages (normalization → segment check → boundary check) * Symlink resolution and canonicalization
3. **Access Control** * Device-based authentication for write operations * Local-only restrictions for critical operations * IPv4-mapped IPv6 address detection
4. **Session Security** * Signed session cookies prevent tampering * Secret key protects session integrity * Session data never exposed in URLs
5. **CORS Protection** * Origin validation prevents unauthorized cross-origin access * Preflight request handling * Configurable allowed origins/headers/methods

### Attack Prevention

| Attack Type | Prevention Mechanism |
| --- | --- |
| Path Traversal | `.` and `..` segment rejection, boundary checks |
| Directory Enumeration | Whitelist-based resource access |
| Filename Injection | Character filtering via `sanitize_filename` |
| Session Hijacking | Signed cookies, secret key protection |
| Unauthorized Remote Access | Local address detection, IPv4-mapped handling |
| CSRF | Session-based authentication, origin validation |
| Brute Force | bcrypt computational cost, device trust system |

**Sources**: [backend.py L8](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L8-L8)

 [backend.py L994-L1063](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L994-L1063)

 [backend.py L1215-L1282](https://github.com/HKLHaoBin/LyricSphere/blob/7864cfe0/backend.py#L1215-L1282)