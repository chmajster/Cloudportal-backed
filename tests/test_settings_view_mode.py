from pathlib import Path


def test_settings_supports_persistent_grid_and_list_layouts():
    script = Path('app/web/features/settings.js').read_text()
    stylesheet = Path('app/web/styles/features/settings.css').read_text()

    assert "SETTINGS_LAYOUT_STORAGE_KEY = 'cloudportal.console.settings.layout'" in script
    assert "localStorage.getItem(SETTINGS_LAYOUT_STORAGE_KEY)" in script
    assert "localStorage.setItem(SETTINGS_LAYOUT_STORAGE_KEY, selected)" in script
    assert "text: 'Układ ustawień'" in script
    assert "text: 'Kafelki'" in script
    assert "text: 'Lista'" in script
    assert "'aria-label': 'Sposób wyświetlania ustawień'" in script
    assert "saveSettingsLayout('grid')" in script
    assert "saveSettingsLayout('list')" in script
    assert "settingsLayout === 'list' ? ' settings-list' : ''" in script

    assert '.settings-grid.settings-list' in stylesheet
    assert 'grid-template-columns: minmax(0, 1fr);' in stylesheet
    assert '.settings-appearance-label' in stylesheet
