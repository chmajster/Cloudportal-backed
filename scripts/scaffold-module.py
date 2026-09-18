#!/usr/bin/env python3
"""Create conflict-resistant frontend/backend module skeletons."""
from __future__ import annotations

import argparse
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def write_new(path: Path, content: str) -> None:
    if path.exists():
        raise SystemExit(f'Refusing to overwrite existing file: {path.relative_to(ROOT)}')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')
    print('created', path.relative_to(ROOT))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('name', help='lowercase module name, e.g. billing or dns_zones')
    parser.add_argument('--ui', action='store_true')
    parser.add_argument('--backend', action='store_true')
    parser.add_argument('--label')
    parser.add_argument('--permission')
    parser.add_argument('--order', type=int, default=500)
    args = parser.parse_args()

    if not re.fullmatch(r'[a-z][a-z0-9_]*', args.name):
        raise SystemExit('name must match [a-z][a-z0-9_]*')
    if not args.ui and not args.backend:
        args.ui = args.backend = True

    label = args.label or args.name.replace('_', ' ').title()
    route_id = args.name.replace('_', '-')
    function_name = ''.join(part.title() for part in args.name.split('_')) + 'View'

    if args.ui:
        permission = f", permission: '{args.permission}'" if args.permission else ''
        js = f"""'use strict';

async function {function_name}() {{
  dom.content.replaceChildren(
    heading('{label}'),
    node('section', {{ class: 'panel' }},
      node('p', {{ class: 'muted', text: 'Moduł jest gotowy do implementacji.' }}))
  );
}}

registerView({{ id: '{route_id}', label: '{label}', icon: '•'{permission}, order: {args.order} }}, {function_name});
"""
        css = f"""/* Styles owned by the {args.name} UI feature. */\n"""
        write_new(ROOT / 'app' / 'web' / 'features' / f'{args.name}.js', js)
        write_new(ROOT / 'app' / 'web' / 'styles' / 'features' / f'{args.name}.css', css)

    if args.backend:
        router = f"""from fastapi import APIRouter


router = APIRouter(prefix='/{route_id}', tags=['{args.name}'])
"""
        module = f"""from app.api import {args.name}
from app.modules.spec import ModuleSpec


MODULES = (ModuleSpec('{args.name}', {args.name}.router, order={args.order}),)
"""
        write_new(ROOT / 'app' / 'api' / f'{args.name}.py', router)
        write_new(ROOT / 'app' / 'modules' / f'feature_{args.name}.py', module)


if __name__ == '__main__':
    main()
