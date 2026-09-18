'use strict';

async function catalogView() {
  const [templates, playbooks] = await Promise.all([
    api('/templates'),
    allowed('ansible.read') ? api('/ansible/playbooks') : Promise.resolve({ items: [] }),
  ]);
  const sections = [    node('section', { class: 'panel' },
      node('div', { class: 'panel-header' }, node('h2', { text: 'Szablony Terraform / OpenTofu' })),
      table([
        { label: 'Szablon', value: item => node('div', {}, node('strong', { text: item.name }), node('div', { class: 'mono muted', text: item.id })) },
        { label: 'Platforma', value: item => badge(CREDENTIAL_TYPE_CONFIG[item.provider]?.label || item.provider, 'info') },
        { label: 'Wersja', value: item => `v${item.version}` },
        { label: 'Import', value: item => badge(item.importable ? 'Obsługiwany' : 'Tylko tworzenie', item.importable ? 'ok' : 'info') },
      ], templates.items, item => [button('Pola', () => showTemplateFields(item))])
    ),
  ];
  if (allowed('ansible.read')) {
    sections.push(node('section', { class: 'panel' },
      node('div', { class: 'panel-header' }, node('h2', { text: 'Zatwierdzone playbooki Ansible' })),
      table([
        { label: 'Playbook', value: item => node('strong', { text: item.name }) },
        { label: 'ID', class: 'mono', value: item => item.id },
        { label: 'Transport', value: item => badge(item.transport, 'info') },
        { label: 'Zmienne', value: item => item.variables.map(name => FIELD_LABELS[name] || name.replaceAll('_', ' ')).join(', ') || '—' },
      ], playbooks.items)));
  }
  dom.content.replaceChildren(
    heading('Zatwierdzony, wersjonowany katalog IaC. API nie przyjmuje arbitralnego HCL ani dowolnych playbooków.'),
    ...sections,
  );
}

registerView({ id: 'catalog', label: 'Katalog IaC', icon: 'C', permission: 'terraform.read', order: 60 }, catalogView);
