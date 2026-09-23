"""One-shot UI extraction for the Cloud-init task; removed before merge."""
from pathlib import Path
import hashlib

ui_path = Path('app/web/features/blueprint-wizard-ui.js')
raw = ui_path.read_bytes()
assert hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest() == '7493a18c3774c7ea7f1cd99626d5fc2c06533d5d', 'Wizard UI changed concurrently'
p = Path('app/web/features/blueprint-wizard.js')
s = p.read_text()
start = s.index('      function workflowVisual(steps) {')
end = s.index('      function workflowEditorRow', start)
visual = s[start:end]
s = s[:start] + s[end:]
assert s.count('workflowVisual(') == 2
s = s.replace('workflowVisual(', 'parts.ui.workflowVisual(')
ui = raw.decode()
assert ui.count('  function summaryRow(label, value) {') == 1
visual = '\n'.join(line[4:] if line.startswith('    ') else line for line in visual.splitlines()) + '\n'
ui = ui.replace('  function summaryRow(label, value) {', visual + '  function summaryRow(label, value) {')
assert ui.count('parts.ui = { errorText, summaryRow };') == 1
ui = ui.replace('parts.ui = { errorText, summaryRow };', 'parts.ui = { errorText, summaryRow, workflowVisual };')
p.write_text(s)
ui_path.write_text(ui)
