'use strict';

const CREDENTIAL_TYPE_CONFIG = {
  proxmox: {
    label: 'Proxmox VE',
    description: 'Połączenie z API Proxmox VE. Adres może być samym IP/hostem albo jawnym URL HTTP/HTTPS.',
    endpoint: {
      label: 'Adres Proxmox (IP / host / URL)',
      placeholder: '192.168.1.10',
      required: true,
      help: 'Możesz podać samo IP/hostname albo URL HTTP/HTTPS. Port ustaw osobno.',
    },
    port: {
      label: 'Port',
      default: 8006,
      min: 1,
      max: 65535,
      help: 'Domyślny port API Proxmox VE: 8006.',
    },
    username: { label: 'Użytkownik', placeholder: 'root', required: true },
    realm: {
      label: 'Realm',
      default: 'pam',
      placeholder: 'pam',
      required: true,
      help: 'Domyślny realm Proxmox dla kont systemowych to pam.',
    },
    tls: true,
    defaultAuth: 'token',
    authModes: {
      token: { label: 'API token (zalecane)', fields: [
        { key: 'token_id', label: 'Token ID', placeholder: 'root@pam!cloudportal', required: true },
        { key: 'token_secret', label: 'Token secret', type: 'password', required: true, autocomplete: 'new-password' },
      ]},
      generate_token: { label: 'Login + hasło → wygeneruj token', createOnly: true, fields: [
        { key: 'password', label: 'Hasło Proxmox (nie będzie zapisane)', type: 'password', required: true, autocomplete: 'current-password' },
        { key: 'token_name', label: 'Nazwa nowego tokenu', value: 'cloudportal', placeholder: 'cloudportal', required: true },
      ]},
      password: { label: 'Użytkownik i hasło', fields: [
        { key: 'password', label: 'Hasło', type: 'password', required: true, autocomplete: 'new-password' },
      ]},
    },
  },
  vmware: {
    label: 'VMware vCenter',
    description: 'Połączenie z vCenter przez vSphere REST API.',
    endpoint: { label: 'Endpoint vCenter HTTPS', placeholder: 'https://vcenter.example.com', required: true },
    username: { label: 'Użytkownik', placeholder: 'administrator@vsphere.local', required: true },
    tls: true, defaultAuth: 'password',
    authModes: { password: { label: 'Login i hasło', fields: [
      { key: 'password', label: 'Hasło', type: 'password', required: true, autocomplete: 'new-password' },
    ]}},
  },
  ssh: {
    label: 'SSH / Linux',
    description: 'Dane dostępowe do Ansible i SSH. Możesz użyć hasła, istniejącego klucza albo wygenerować nowy klucz i automatycznie wgrać go na serwer.',
    endpoint: { label: 'Endpoint SSH', placeholder: 'ssh://server.example.com:22', required: false, help: 'Dla generowania klucza i testu połączenia podaj ssh://host:port.' },
    username: { label: 'Użytkownik SSH', placeholder: 'clouduser', required: true },
    tls: false, defaultAuth: 'password',
    authModes: {
      password: { label: 'Hasło SSH', fields: [
        { key: 'password', label: 'Hasło SSH', type: 'password', required: true, autocomplete: 'current-password' },
        { key: 'known_hosts', label: 'known_hosts', tag: 'textarea', required: true, wide: true, placeholder: 'host.example.com ssh-ed25519 AAAA...' },
      ]},
      private_key: { label: 'Istniejący klucz prywatny', fields: [
        { key: 'private_key', label: 'Klucz prywatny SSH', tag: 'textarea', required: true, wide: true, placeholder: '-----BEGIN OPENSSH PRIVATE KEY-----' },
        { key: 'known_hosts', label: 'known_hosts', tag: 'textarea', required: true, wide: true, placeholder: 'host.example.com ssh-ed25519 AAAA...' },
      ]},
      generate_key: { label: 'Hasło → wygeneruj i wgraj klucz (zalecane)', createOnly: true, fields: [
        { key: 'password', label: 'Hasło SSH (tylko do jednorazowego wgrania klucza)', type: 'password', required: true, autocomplete: 'current-password', wide: true },
      ]},
    },
  },
  winrm: {
    label: 'WinRM / Windows',
    description: 'Dane dostępowe do Windows przez WinRM/NTLM.',
    endpoint: { label: 'Endpoint WinRM HTTPS', placeholder: 'https://server.example.com:5986/wsman', required: true },
    username: { label: 'Użytkownik', placeholder: 'DOMAIN\\svc-cloudportal', required: true },
    tls: true, defaultAuth: 'password',
    authModes: { password: { label: 'Login i hasło', fields: [
      { key: 'password', label: 'Hasło', type: 'password', required: true, autocomplete: 'new-password' },
    ]}},
  },
  aws: {
    label: 'Amazon Web Services',
    description: 'Klucze IAM. Test wykonuje STS GetCallerIdentity.',
    endpoint: null, username: null, tls: false, defaultAuth: 'keys',
    authModes: { keys: { label: 'Access keys', fields: [
      { key: 'access_key_id', label: 'Access Key ID', placeholder: 'AKIA...', required: true },
      { key: 'secret_access_key', label: 'Secret Access Key', type: 'password', required: true, autocomplete: 'new-password' },
      { key: 'session_token', label: 'Session Token (opcjonalnie)', tag: 'textarea', wide: true },
    ]}},
  },
  azure: {
    label: 'Microsoft Azure',
    description: 'Service Principal / App Registration używany także przez Terraform.',
    endpoint: null, username: null, tls: false, defaultAuth: 'service_principal',
    authModes: { service_principal: { label: 'Service Principal', fields: [
      { key: 'tenant_id', label: 'Tenant ID', required: true },
      { key: 'client_id', label: 'Client ID', required: true },
      { key: 'client_secret', label: 'Client Secret', type: 'password', required: true, autocomplete: 'new-password' },
      { key: 'subscription_id', label: 'Subscription ID', required: true },
    ]}},
  },
  openstack: {
    label: 'OpenStack',
    description: 'Keystone v3 password auth z projektem.',
    endpoint: { label: 'Endpoint Keystone HTTPS', placeholder: 'https://openstack.example.com:5000/v3', required: true },
    username: { label: 'Użytkownik', placeholder: 'cloudportal', required: true },
    tls: true, defaultAuth: 'password',
    authModes: { password: { label: 'Password auth', fields: [
      { key: 'password', label: 'Hasło', type: 'password', required: true, autocomplete: 'new-password' },
      { key: 'project_name', label: 'Projekt', required: true },
      { key: 'domain_name', label: 'Domena', value: 'Default', placeholder: 'Default' },
    ]}},
  },
  other: {
    label: 'Inny sekret',
    description: 'Ogólny zaszyfrowany sekret bez adaptera testującego.',
    endpoint: { label: 'Endpoint HTTPS (opcjonalnie)', placeholder: 'https://service.example.com', required: false },
    username: { label: 'Użytkownik (opcjonalnie)', placeholder: 'service-user', required: false },
    tls: true, defaultAuth: 'secret',
    authModes: { secret: { label: 'Sekret', fields: [
      { key: 'secret', label: 'Sekret', type: 'password', required: true, autocomplete: 'new-password', wide: true },
    ]}},
  },
};

function credentialTypeChoices() {
  return Object.entries(CREDENTIAL_TYPE_CONFIG).map(([value, config]) => ({ value, label: config.label }));
}

function credentialTypeLabel(type) {
  return CREDENTIAL_TYPE_CONFIG[type]?.label || type;
}
