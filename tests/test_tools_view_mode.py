from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_tools_support_grid_and_list_views():
    script = (ROOT / "app/web/features/tools.js").read_text(encoding="utf-8")
    stylesheet = (ROOT / "app/web/styles/features/tools.css").read_text(encoding="utf-8")

    assert "const TOOLS_VIEW_KEY = 'cloudportal.console.tools.view';" in script
    assert "text: 'Kafelki'" in script
    assert "text: 'Lista'" in script
    assert "aria-label': 'Sposób wyświetlania narzędzi'" in script
    assert "localStorage.setItem(TOOLS_VIEW_KEY" in script
    assert "container.classList.toggle('tools-list', list)" in script
    assert ".tools-view-toggle" in stylesheet
    assert ".tools-view-button" in stylesheet
    assert ".tools-grid.tools-list" in stylesheet
    assert ".tools-grid.tools-list .tool-meta-grid" in stylesheet
