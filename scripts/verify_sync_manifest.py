#!/usr/bin/env python3
"""Shared sync-manifest verifier for famyliam -> LyricSphere publish."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

SCHEMA_VERSION = 1
BACKEND_PLACEHOLDER = 'APP_VERSION = "0.0.0-dev"'
OWNED_ROLES = frozenset({'backend', 'requirements', 'subsonic', 'dist', 'contract'})

TEXT_SUFFIXES = frozenset({
    '.py', '.txt', '.md', '.json', '.js', '.mjs', '.cjs', '.css', '.html', '.htm',
    '.svg', '.map', '.yml', '.yaml', '.toml', '.ini', '.cfg', '.lock', '.npmrc',
    '.gitignore',
})
BINARY_SUFFIXES = frozenset({
    '.png', '.jpg', '.jpeg', '.gif', '.webp', '.ico', '.woff', '.woff2', '.ttf',
    '.otf', '.eot', '.mp3', '.wav', '.flac', '.ogg', '.wasm', '.exe', '.dll', '.zip',
})

ZIP_REQUIRED_PATHS = (
    'backend.exe',
    'updater.exe',
    'templates/lyric-sphere-v2/dist/index.html',
    'templates/lyric-sphere-v2/dist/update-screen.html',
    'static/assets',
)
ZIP_FORBIDDEN_PREFIXES = (
    'songs/', 'backups/', '.cache/', 'exports/', 'node_modules/',
    'templates/lyric-sphere-v2/src/', 'lyric-sphere-v2/src/',
)
ZIP_FORBIDDEN_NAMES = frozenset({
    'security_config.json', 'keys_config.json',
    'security-config.json', 'keys-config.json',
})


class ManifestError(Exception):
    pass


def normalize_rel_path(path: str) -> str:
    raw = (path or '').replace('\\', '/').strip()
    if not raw:
        raise ManifestError('empty path')
    if raw.startswith('/') or (len(raw) > 1 and raw[1] == ':'):
        raise ManifestError(f'absolute path rejected: {path!r}')
    parts: List[str] = []
    for part in raw.split('/'):
        if part in ('', '.'):
            continue
        if part == '..':
            raise ManifestError(f'parent segment rejected: {path!r}')
        parts.append(part)
    if not parts:
        raise ManifestError(f'empty normalized path: {path!r}')
    return '/'.join(parts)


def is_text_path(rel_path: str) -> bool:
    name = Path(rel_path).name
    if name in {'.npmrc', '.gitignore'}:
        return True
    suffix = Path(rel_path).suffix.lower()
    if suffix in TEXT_SUFFIXES:
        return True
    if suffix in BINARY_SUFFIXES:
        return False
    return True


def file_sha256(path: Path, *, text: Optional[bool] = None) -> str:
    use_text = is_text_path(path.as_posix()) if text is None else text
    data = path.read_bytes()
    if use_text:
        try:
            text_data = data.decode('utf-8')
        except UnicodeDecodeError:
            return hashlib.sha256(data).hexdigest()
        normalized = text_data.replace('\r\n', '\n').replace('\r', '\n')
        return hashlib.sha256(normalized.encode('utf-8')).hexdigest()
    return hashlib.sha256(data).hexdigest()


def sha256_for_rel(root: Path, rel_path: str) -> str:
    rel = normalize_rel_path(rel_path)
    full = root.joinpath(*rel.split('/'))
    if not full.is_file():
        raise ManifestError(f'missing file: {rel}')
    return file_sha256(full, text=is_text_path(rel))


def load_manifest(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except Exception as exc:
        raise ManifestError(f'cannot read manifest: {exc}') from exc
    if not isinstance(data, dict):
        raise ManifestError('manifest root must be object')
    if data.get('schemaVersion') != SCHEMA_VERSION:
        raise ManifestError(f'unsupported schemaVersion: {data.get("schemaVersion")!r}')
    return data


def sorted_unique_paths(paths: Iterable[str]) -> List[str]:
    seen: Set[str] = set()
    out: List[str] = []
    for item in paths:
        rel = normalize_rel_path(item)
        if rel in seen:
            continue
        seen.add(rel)
        out.append(rel)
    out.sort()
    return out


def validate_manifest_shape(manifest: Dict[str, Any]) -> None:
    if not isinstance(manifest.get('releaseEligible'), bool):
        raise ManifestError('releaseEligible must be bool')
    gates = manifest.get('gates')
    if not isinstance(gates, dict):
        raise ManifestError('gates must be object')
    for key in ('providerTestsPassed', 'safeIncrementPassed', 'frontendBuildPassed'):
        if not isinstance(gates.get(key), bool):
            raise ManifestError(f'gates.{key} must be bool')
    files = manifest.get('files')
    if not isinstance(files, list) or not files:
        raise ManifestError('files must be non-empty list')
    seen: Set[str] = set()
    for entry in files:
        if not isinstance(entry, dict):
            raise ManifestError('files entries must be objects')
        rel = normalize_rel_path(str(entry.get('path') or ''))
        role = entry.get('role')
        digest = entry.get('sha256')
        if role not in OWNED_ROLES:
            raise ManifestError(f'invalid role for {rel}: {role!r}')
        if not isinstance(digest, str) or len(digest) != 64:
            raise ManifestError(f'invalid sha256 for {rel}')
        if rel in seen:
            raise ManifestError(f'duplicate path: {rel}')
        seen.add(rel)
    paths = [normalize_rel_path(str(e['path'])) for e in files]
    if paths != sorted(paths):
        raise ManifestError('files must be sorted by path')


def iter_files_under(root: Path, rel_dir: str) -> List[str]:
    if rel_dir in ('', '.'):
        base = root
    else:
        rel = normalize_rel_path(rel_dir)
        base = root.joinpath(*rel.split('/'))
    if not base.exists():
        return []
    out: List[str] = []
    for path in base.rglob('*'):
        if path.is_file():
            out.append(normalize_rel_path(path.relative_to(root).as_posix()))
    return sorted_unique_paths(out)


def build_file_entries(root: Path, items: Iterable[Tuple[str, str]]) -> List[Dict[str, str]]:
    entries: List[Dict[str, str]] = []
    for rel_path, role in items:
        rel = normalize_rel_path(rel_path)
        if role not in OWNED_ROLES:
            raise ManifestError(f'invalid role: {role}')
        entries.append({
            'path': rel,
            'role': role,
            'sha256': sha256_for_rel(root, rel),
        })
    entries.sort(key=lambda e: e['path'])
    return entries


def check_worktree(
    root: Path,
    manifest: Dict[str, Any],
    *,
    require_release_eligible: bool = True,
    backend_placeholder: str = BACKEND_PLACEHOLDER,
) -> None:
    validate_manifest_shape(manifest)
    if require_release_eligible and not manifest.get('releaseEligible'):
        raise ManifestError('releaseEligible is false; refusing publish check')

    expected = {normalize_rel_path(str(e['path'])): e for e in manifest['files']}
    for rel, entry in expected.items():
        full = root.joinpath(*rel.split('/'))
        if not full.is_file():
            raise ManifestError(f'missing owned file: {rel}')
        if rel == 'backend.py':
            text = full.read_text(encoding='utf-8')
            if backend_placeholder not in text:
                raise ManifestError(
                    'backend.py APP_VERSION placeholder missing or already injected'
                )
        actual = sha256_for_rel(root, rel)
        if actual != entry['sha256']:
            raise ManifestError(f'hash mismatch: {rel}')

    dist_expected = {p for p, e in expected.items() if e['role'] == 'dist'}
    dist_actual = set(iter_files_under(root, 'templates/lyric-sphere-v2/dist'))
    extra = sorted(dist_actual - dist_expected)
    missing = sorted(dist_expected - dist_actual)
    if extra:
        raise ManifestError(f'unexpected dist files: {extra[:20]}')
    if missing:
        raise ManifestError(f'missing dist files: {missing[:20]}')


def check_zip(zip_path: Path, manifest: Dict[str, Any], *, extract_dir: Path) -> None:
    validate_manifest_shape(manifest)
    if not manifest.get('releaseEligible'):
        raise ManifestError('releaseEligible is false; refusing zip publish')

    extract_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, 'r') as zf:
        zf.extractall(extract_dir)

    root = extract_dir
    nested = extract_dir / 'LyricSphere.exe'
    if nested.is_dir():
        root = nested

    all_files: Set[str] = set()
    for path in root.rglob('*'):
        if path.is_file():
            all_files.add(normalize_rel_path(path.relative_to(root).as_posix()))

    for required in ZIP_REQUIRED_PATHS:
        target = root.joinpath(*required.split('/'))
        if required.endswith(('.html', '.exe')):
            if not target.is_file():
                raise ManifestError(f'zip missing required file: {required}')
        elif not target.exists():
            raise ManifestError(f'zip missing required path: {required}')

    for rel in sorted(all_files):
        for prefix in ZIP_FORBIDDEN_PREFIXES:
            if rel == prefix.rstrip('/') or rel.startswith(prefix):
                raise ManifestError(f'zip contains forbidden path: {rel}')
        name = Path(rel).name.lower()
        if name in ZIP_FORBIDDEN_NAMES:
            raise ManifestError(f'zip contains forbidden file: {rel}')
        if name.endswith('.key'):
            raise ManifestError(f'zip contains key file: {rel}')
        if name.startswith('debug-') and name.endswith('.log'):
            raise ManifestError(f'zip contains debug log: {rel}')

    expected = {
        normalize_rel_path(str(e['path'])): e
        for e in manifest['files']
        if e['role'] == 'dist'
    }
    dist_actual = set(iter_files_under(root, 'templates/lyric-sphere-v2/dist'))
    if dist_actual != set(expected):
        extra = sorted(dist_actual - set(expected))
        missing = sorted(set(expected) - dist_actual)
        raise ManifestError(
            f'dist exact-set mismatch; extra={extra[:20]} missing={missing[:20]}'
        )
    for rel, entry in expected.items():
        actual = sha256_for_rel(root, rel)
        if actual != entry['sha256']:
            raise ManifestError(f'zip hash mismatch: {rel}')


def build_publish_manifest(
    *,
    root: Path,
    source_commit: str,
    synced_at: str,
    gates: Dict[str, bool],
    inputs: Dict[str, str],
) -> Dict[str, Any]:
    items: List[Tuple[str, str]] = [
        ('backend.py', 'backend'),
        ('requirements-backend.txt', 'requirements'),
        ('scripts/verify_sync_manifest.py', 'contract'),
    ]
    for rel in iter_files_under(root, 'subsonic'):
        if rel.endswith('.py'):
            items.append((rel, 'subsonic'))
    for rel in iter_files_under(root, 'templates/lyric-sphere-v2/dist'):
        items.append((rel, 'dist'))

    files = build_file_entries(root, items)
    release_eligible = all(
        bool(gates.get(k))
        for k in ('providerTestsPassed', 'safeIncrementPassed', 'frontendBuildPassed')
    )
    normalized_inputs = {
        normalize_rel_path(k): str(v)
        for k, v in sorted(inputs.items(), key=lambda kv: kv[0])
    }
    return {
        'schemaVersion': SCHEMA_VERSION,
        'sourceCommit': source_commit,
        'syncedAt': synced_at,
        'releaseEligible': release_eligible,
        'gates': {
            'providerTestsPassed': bool(gates.get('providerTestsPassed')),
            'safeIncrementPassed': bool(gates.get('safeIncrementPassed')),
            'frontendBuildPassed': bool(gates.get('frontendBuildPassed')),
        },
        'inputs': normalized_inputs,
        'files': files,
    }


def _parse_bool_flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    text = str(value).strip().lower()
    if text in {'1', 'true', 'yes', 'on'}:
        return True
    if text in {'0', 'false', 'no', 'off'}:
        return False
    raise ManifestError(f'invalid bool flag: {value!r}')


def write_manifest(path: Path, manifest: Dict[str, Any]) -> None:
    validate_manifest_shape(manifest)
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=False) + '\n',
        encoding='utf-8',
        newline='\n',
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description='Verify LyricSphere sync-manifest contract')
    sub = parser.add_subparsers(dest='cmd', required=True)

    p_wt = sub.add_parser('check-worktree')
    p_wt.add_argument('--root', type=Path, required=True)
    p_wt.add_argument('--manifest', type=Path, required=True)
    p_wt.add_argument('--allow-ineligible', action='store_true')

    p_zip = sub.add_parser('check-zip')
    p_zip.add_argument('--zip', type=Path, required=True, dest='zip_path')
    p_zip.add_argument('--manifest', type=Path, required=True)
    p_zip.add_argument('--extract-dir', type=Path, required=True)

    p_hash = sub.add_parser('hash-file')
    p_hash.add_argument('--file', type=Path, required=True)

    p_build = sub.add_parser('build-manifest')
    p_build.add_argument('--root', type=Path, required=True)
    p_build.add_argument('--source-commit', required=True)
    p_build.add_argument('--synced-at', required=True)
    p_build.add_argument('--provider-tests', required=True)
    p_build.add_argument('--safe-increment', required=True)
    p_build.add_argument('--frontend-build', required=True)
    p_build.add_argument('--inputs-json', type=Path, required=True)
    p_build.add_argument('--output', type=Path, required=True)
    p_build.add_argument(
        '--force-ineligible',
        action='store_true',
        help='Force releaseEligible=false (e.g. dirty bootstrap sync)',
    )

    args = parser.parse_args(argv)
    try:
        if args.cmd == 'hash-file':
            print(file_sha256(args.file.resolve()))
            return 0

        if args.cmd == 'build-manifest':
            raw_inputs = json.loads(args.inputs_json.read_text(encoding='utf-8'))
            if isinstance(raw_inputs, dict):
                inputs_obj = raw_inputs
            elif isinstance(raw_inputs, list):
                inputs_obj = {}
                for item in raw_inputs:
                    if not isinstance(item, dict) or 'path' not in item or 'sha256' not in item:
                        raise ManifestError('inputs-json list entries need path+sha256')
                    inputs_obj[str(item['path'])] = str(item['sha256'])
            else:
                raise ManifestError('inputs-json must be object or list')
            gates = {
                'providerTestsPassed': _parse_bool_flag(args.provider_tests),
                'safeIncrementPassed': _parse_bool_flag(args.safe_increment),
                'frontendBuildPassed': _parse_bool_flag(args.frontend_build),
            }
            root = args.root.resolve()
            verify_src = Path(__file__).resolve()
            verify_dst = root / 'scripts' / 'verify_sync_manifest.py'
            verify_dst.parent.mkdir(parents=True, exist_ok=True)
            if verify_src != verify_dst:
                verify_dst.write_bytes(verify_src.read_bytes())
            manifest = build_publish_manifest(
                root=root,
                source_commit=args.source_commit,
                synced_at=args.synced_at,
                gates=gates,
                inputs={str(k): str(v) for k, v in inputs_obj.items()},
            )
            if args.force_ineligible:
                manifest['releaseEligible'] = False
            # Keep sync-manifest.json as sidecar (not self-hashed in files[]).
            write_manifest(args.output.resolve(), manifest)
            print(args.output.as_posix())
            return 0

        manifest = load_manifest(args.manifest)
        if args.cmd == 'check-worktree':
            check_worktree(
                args.root.resolve(),
                manifest,
                require_release_eligible=not args.allow_ineligible,
            )
        elif args.cmd == 'check-zip':
            check_zip(
                args.zip_path.resolve(),
                manifest,
                extract_dir=args.extract_dir.resolve(),
            )
        else:
            raise ManifestError(f'unknown command: {args.cmd}')
    except ManifestError as exc:
        print(f'MANIFEST_FAIL: {exc}', file=sys.stderr)
        return 1
    print('MANIFEST_OK')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
