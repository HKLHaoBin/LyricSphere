# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Flask-based web application called "LyricSphere" for managing and displaying dynamic lyrics with synchronized playback. The application supports various lyric formats including .lrc, .lys, and .ttml, and provides features for editing, converting, and displaying lyrics with real-time animation.

## Directory Structure

- `backend.py`: Main Flask application with all routes and functionality
- `templates/`: HTML templates for the web interface
- `static/`: Static files including songs, backups, and other assets
- `static/songs/`: Directory for song files, lyrics, and related media
- `static/backups/`: Directory for backup files
- `logs/`: Directory for application logs (created automatically)
- `exports/`: Directory for exported files (created automatically)

## Development Commands

### Running the Application

```bash
python backend.py
```

Or with a specific port:
```bash
python backend.py 5000
```

The application uses Waitress as the production server when the USE_WAITRESS environment variable is set to 1.

### Dependencies

The application requires the following Python packages:
- Flask
- openai
- bcrypt
- waitress (for production deployment)

Install dependencies with:
```bash
pip install flask openai bcrypt waitress
```

## Architecture

### Core Components

1. **Main Flask Application** (`backend.py`):
   - Handles all web routes and API endpoints
   - Manages file operations (upload, save, backup, restore)
   - Provides lyric parsing and conversion functionality
   - Implements security features and authentication

2. **Lyric Processing**:
   - Support for .lys (Lyricify Syllable) format with syllable-level timing
   - Support for .lrc (Lyric) format with line-level timing
   - Support for .ttml (Timed Text Markup Language) format
   - Conversion between different lyric formats

3. **Real-time Features**:
   - WebSocket server on port 11444 for AMLL integration
   - Server-Sent Events (SSE) for real-time lyric updates
   - Progress tracking and synchronization

4. **Security System**:
   - Device-based authentication with trusted device management
   - Password protection using bcrypt hashing
   - Local access restrictions for sensitive operations

### Key Routes

- `/`: Main interface for managing JSON lyric files
- `/lyrics-animate`: Entry point for lyric display with different styles
- `/lyrics`: API endpoint for retrieving parsed lyrics
- `/amll/stream`: Server-Sent Events endpoint for real-time lyric updates
- `/auth/*`: Authentication-related endpoints
- `/convert_to_ttml`: API endpoint for converting LYS/LRC files to TTML format
- `/convert_to_ttml_temp`: API endpoint for temporary TTML conversion for AMLL rule writing

### File Management

The application automatically creates necessary directories:
- `static/songs/` for media files
- `static/backups/` for file versioning
- `logs/` for application logging
- `exports/` for exported data

Backup functionality is built into most file operations, maintaining up to 7 versions of each file.

### Lyric Format Support

1. **.lys Format**: Custom syllable-level timed lyrics format
2. **.lrc Format**: Standard line-level timed lyrics format
3. **.ttml Format**: XML-based timed text format with conversion capabilities

### Format Conversion

The application provides bidirectional conversion between different lyric formats:
- **TTML to LYS/LRC**: Convert TTML files to LYS or LRC formats
- **LYS/LRC to TTML**: Convert LYS or LRC files to TTML format (Apple style)

### Real-time Integration

The application includes a WebSocket server (port 11444) for integration with AMLL (Advanced Music Live Lyrics) and supports Server-Sent Events for real-time lyric display updates.