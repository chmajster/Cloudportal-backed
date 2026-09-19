'use strict';

(() => {
function openProxmoxTemplateWizard(item = null) {
  if (!hasCommand('blueprints.proxmoxTemplateWizard')) {
    toast('Kreator szablonu IaC nie jest dostępny.', 'error');
    return;
  }
  return runCommand('blueprints.proxmoxTemplateWizard', item, { mode: 'template', returnTo: 'catalog' });
}

async function catalogView() {
  const [templates, playbooks, blueprintResult] = await Promise.all([
    api('/templates'),
    allowed('ansible.read') ? api('/ansible/playbooks') : Promise.resolve({ items: [] }),
    allowed('blueprints.read') ? api('/blueprints?limit=200') : Promise.resolve({ items: [] }),
  ]);

  const canCreateTemplate = allowed('blueprints.create')
    && allowed('providers.read')
    && allowed('credentials.read')
    && allowed('hostnames.read')
    && allowed('hostnames.create')
    && allowed('ipam.read');

  const actions = canCreateTemplate
    ? [button('Nowy szablon Terraform / OpenTofu', () => openProxmoxTemplateWizard(), 'primary')]
    : [];

  const sections = [
    node('section', { class: 'panel' },
      node('div', { class: 'panel-header' },
        node('div', {},
          node('h2', { text: 'Szablony Terraform / OpenTofu' }),
          node('p', { class: 'muted', text: 'Bazowe, zatwierdzone moduły IaC używane przez deploymenty i kreator.' }))),
      table([
        { label: 'Szablon', value: item => node('div', {}, node('strong', { text: item.name }), node('div', { class: 'mono muted', text: item.id })) },
        { label: 'Platforma', value: item => badge(CREDENTIAL_TYPE_CONFIG[item.provider]?.label || item.provider, 'info') },
        { label: 'Wersja', value: item => `v${item.version}` },
        { label: 'Import', value: item => badge(item.importable ? 'Obsługiwany' : 'Tylko tworzenie', item.importable ? 'ok' : 'info') },
      ], templates.items, item => [button('Pola', () => showTemplateFields(item))])
    ),
  ];

  if (allowed('blueprints.read')) {
    const generated = blueprintResult.items.filter(item => item.deployment?.template === 'proxmox-vm');
    sections.push(node('section', { class: 'panel' },
      node('div', { class: 'panel-header' },
        node('div', {},
          node('h2', { text: 'Szablony utworzone w kreatorze' }),
          node('p', { class: 'muted', text: 'Gotowe presety Proxmox z zapisaną VM bazową, parametrami, silnikiem IaC i generatorem hostname.' }))),
      table([
        { label: 'Nazwa', value: item => node('div', {}, node('strong', { text: item.name }), node('div', { class: 'mono muted', text: item.slug })) },
        { label: 'Silnik', value: item => badge(item.deployment?.executor === 'opentofu' ? 'OpenTofu' : 'Terraform', 'info') },
        { label: 'Provider', value: item => '#' + (item.deployment?.provider_id ?? '—') },
        { label: 'VM bazowa', value: item => {
          const nodeName = item.deployment?.variables?.template_node || item.deployment?.variables?.node || '—';
          const vmid = item.deployment?.variables?.template_id ?? '—';
          return nodeName + ' / VMID ' + vmid;
        } },
        { label: 'Wersja', value: item => 'v' + item.version },
        { label: 'Status', value: item => badge(item.is_active ? 'Aktywny' : 'Nieaktywny', item.is_active ? 'ok' : 'danger') },
      ], generated, item => {
        const rowActions = [];
        if (allowed('blueprints.update') && canCreateTemplate) {
          rowActions.push(button('Edytuj', () => openProxmoxTemplateWizard(item)));
        }
        if (allowed('blueprints.execute')) {
          rowActions.push(button('Użyj', () => {
            if (!hasCommand('blueprints.execute')) {
              toast('Uruchamianie szablonu nie jest dostępne.', 'error');
              return;
            }
            runCommand('blueprints.execute', item);
          }, 'primary'));
        }
        return rowActions;
      })
    ));
  }

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
    heading('Katalog IaC i kreator wielokrotnie używalnych szablonów VM. Kreator nie przyjmuje arbitralnego HCL — generuje bezpieczny preset na bazie zatwierdzonego modułu Proxmox.', actions),
    ...sections,
  );
}

registerView({ id: 'catalog', label: 'Katalog IaC', icon: 'C', permission: 'terraform.read', order: 60 }, catalogView);
})();
