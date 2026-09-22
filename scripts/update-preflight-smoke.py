#!/usr/bin/env python3
"""Exercise the installed updater runtime preflight in an isolated CI host."""
import importlib.util
import os
from pathlib import Path


UPDATER = Path('/usr/local/lib/cloudportal-updater/update-service.py')


def main():
    target_sha = os.environ.get('GITHUB_SHA', '').strip()
    if len(target_sha) != 40:
        raise SystemExit('GITHUB_SHA is required')
    spec = importlib.util.spec_from_file_location('cloudportal_installed_updater_smoke', UPDATER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    backup = module.pre_update_backup()
    try:
        module.validate_candidate_runtime(target_sha, backup, module.load_settings())
    except Exception:
        state = module.load_state()
        for line in state.get('output') or []:
            print(line)
        raise
    state = module.load_state()
    assert state.get('runtime_preflight') == 'success'
    print('Updater runtime preflight: cloned DB migration and isolated candidate API passed.')


if __name__ == '__main__':
    main()
