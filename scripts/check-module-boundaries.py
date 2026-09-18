#!/usr/bin/env python3
"""Fail CI when changes collapse modular boundaries back into shared hot files."""
from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'app' / 'web'
FEATURES = WEB / 'features'
SHARED = WEB / 'shared'
FEATURE_STYLES = WEB / 'styles' / 'features'
BACKEND_MODULES = ROOT / 'app' / 'modules'


def fail(message: str, errors: list[str]) -> None:
    errors.append(message)


def main() -> int:
    errors: list[str] = []
    index = (WEB / 'index.html').read_text(encoding='utf-8')
    bootstrap = (WEB / 'app.js').read_text(encoding='utf-8')
    core = (WEB / 'core.js').read_text(encoding='utf-8')
    core_css = (WEB / 'styles.css').read_text(encoding='utf-8')

    if 'src="./core.js"' not in index or 'src="./loader.js"' not in index:
        fail('index.html must load only core.js + loader.js as the modular entrypoint', errors)
    if 'src="./app.js"' in index or 'src="./features/' in index or 'src="./shared/' in index:
        fail('index.html must not hard-code runtime feature/shared scripts', errors)
    if 'href="./styles/features/' in index:
        fail('index.html must not hard-code feature styles', errors)
    if list(WEB.glob('*.py')):
        fail('Python source must not live under the public app/web static directory', errors)

    bootstrap_lines = len(bootstrap.splitlines())
    core_lines = len(core.splitlines())
    if bootstrap_lines > 120:
        fail(f'app/web/app.js is bootstrap-only and must stay <=120 lines (got {bootstrap_lines})', errors)
    if core_lines > 1200:
        fail(f'app/web/core.js must stay <=1200 lines; move domain logic to a feature (got {core_lines})', errors)
    if re.search(r'\b(?:async\s+)?function\s+\w+View\s*\(', bootstrap):
        fail('Domain view functions are not allowed in app/web/app.js', errors)

    feature_files = sorted(FEATURES.glob('*.js'))
    if not feature_files:
        fail('No frontend feature modules found', errors)
    route_ids: list[tuple[str, Path]] = []
    route_pattern = re.compile(r"registerView\(\{\s*id:\s*'([^']+)'")
    for path in feature_files:
        text = path.read_text(encoding='utf-8')
        if not text.startswith("'use strict';"):
            fail(f'{path.relative_to(ROOT)} must start with use strict', errors)
        if "(() => {" not in text or not text.rstrip().endswith("})();"):
            fail(f'{path.relative_to(ROOT)} must be isolated in an IIFE', errors)
        if 'registerView({' not in text and 'registerExtension(' not in text:
            fail(f'{path.relative_to(ROOT)} must register a view or extension', errors)
        if len(text.splitlines()) > 1400:
            fail(f'{path.relative_to(ROOT)} exceeds 1400 lines; split the feature further', errors)
        for route_id in route_pattern.findall(text):
            route_ids.append((route_id, path))

    extension_pattern = re.compile(r"registerExtension\(\s*'([^']+)'")
    extension_ids: list[tuple[str, Path]] = []
    for path in feature_files:
        for extension_id in extension_pattern.findall(path.read_text(encoding='utf-8')):
            extension_ids.append((extension_id, path))

    seen_extensions: dict[str, Path] = {}
    for extension_id, path in extension_ids:
        if extension_id in seen_extensions:
            fail(
                f'duplicate frontend extension {extension_id!r}: '
                f'{seen_extensions[extension_id].relative_to(ROOT)} and {path.relative_to(ROOT)}',
                errors,
            )
        seen_extensions[extension_id] = path

    seen: dict[str, Path] = {}
    for route_id, path in route_ids:
        if route_id in seen:
            fail(
                f'duplicate frontend route {route_id!r}: '
                f'{seen[route_id].relative_to(ROOT)} and {path.relative_to(ROOT)}',
                errors,
            )
        seen[route_id] = path

    for path in sorted(SHARED.glob('*.js')):
        text = path.read_text(encoding='utf-8')
        if 'registerView({' in text:
            fail(f'{path.relative_to(ROOT)} is shared code and must not register routes', errors)

    for selector in (
        '.credential-dynamic',
        '.permission-groups',
        '.designer-heading',
        '.workflow-preview',
        '.task-progress',
        '.novnc-screen',
    ):
        if selector in core_css:
            fail(f'feature selector {selector} leaked back into app/web/styles.css', errors)

    expected_style_files = {'identity.css', 'credentials.css', 'blueprints.css', 'inventory.css', 'search.css'}
    actual_style_files = {path.name for path in FEATURE_STYLES.glob('*.css')}
    if not expected_style_files <= actual_style_files:
        fail(f'missing feature styles: {sorted(expected_style_files - actual_style_files)}', errors)

    main_py = (ROOT / 'app' / 'main.py').read_text(encoding='utf-8')
    if 'backend_modules()' not in main_py:
        fail('app/main.py must compose routers through backend_modules()', errors)
    if re.search(r'^from app\.api import ', main_py, re.MULTILINE):
        fail('app/main.py must not import domain routers directly', errors)

    backend_features = sorted(BACKEND_MODULES.glob('feature_*.py'))
    if not backend_features:
        fail('No backend feature module descriptors found', errors)
    for path in backend_features:
        text = path.read_text(encoding='utf-8')
        if 'MODULES =' not in text:
            fail(f'{path.relative_to(ROOT)} must expose MODULES = (...)', errors)

    if errors:
        print('Module boundary check failed:', file=sys.stderr)
        for error in errors:
            print(f' - {error}', file=sys.stderr)
        return 1

    print(
        f'Module boundaries OK: {len(feature_files)} UI features, '
        f'{len(route_ids)} UI routes, {len(extension_ids)} UI extensions, '
        f'{len(backend_features)} backend descriptors.'
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
