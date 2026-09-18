from pathlib import Path

from fastapi import APIRouter


router = APIRouter()
WEB_ROOT = Path(__file__).parents[1] / 'web'


def _assets(pattern: str) -> list[str]:
    return [
        path.relative_to(WEB_ROOT).as_posix()
        for path in sorted(WEB_ROOT.glob(pattern))
        if path.is_file()
    ]


@router.get('/manifest.json', include_in_schema=False)
def feature_manifest():
    """Return locally installed UI feature assets in deterministic order."""
    return {
        'version': 1,
        'shared': _assets('shared/*.js'),
        'scripts': _assets('features/*.js'),
        'styles': _assets('styles/features/*.css'),
    }
