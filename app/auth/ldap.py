import base64
import ssl
from urllib.parse import urlsplit

from fastapi import HTTPException
from ldap3 import BASE, SUBTREE, Connection, Server, Tls
from ldap3.core.exceptions import LDAPException
from ldap3.utils.conv import escape_filter_chars

from app.models import Setting
from app.security.core import decrypt_blob, encrypt_blob


LDAP_KEY = 'ldap'
LDAP_SECRET_AAD = 'setting:ldap:bind_password'
LDAP_DEFAULTS = {
    'enabled': False,
    'url': 'ldap://localhost:389',
    'start_tls': False,
    'verify_tls': True,
    'bind_dn': '',
    'base_dn': '',
    'user_filter': '(&(objectClass=person)(uid={username}))',
    'username_attribute': 'uid',
    'email_attribute': 'mail',
    'first_name_attribute': 'givenName',
    'last_name_attribute': 'sn',
}


def ldap_settings(db, *, include_secret=False):
    row = db.get(Setting, LDAP_KEY)
    raw = dict(row.value) if row and isinstance(row.value, dict) else {}
    result = {**LDAP_DEFAULTS, **{k: v for k, v in raw.items() if k != 'bind_secret'}}
    secret = raw.get('bind_secret')
    result['bind_password_configured'] = bool(secret)
    if include_secret:
        result['bind_password'] = ''
        if secret:
            try:
                result['bind_password'] = decrypt_blob(
                    base64.b64decode(secret, validate=True), LDAP_SECRET_AAD
                ).decode()
            except Exception:
                raise HTTPException(500, 'LDAP bind secret cannot be decrypted') from None
    return result


def save_ldap_settings(db, data):
    current = db.get(Setting, LDAP_KEY)
    current_value = dict(current.value) if current and isinstance(current.value, dict) else {}
    values = data.model_dump(exclude={'bind_password'})
    if data.bind_password is not None:
        values['bind_secret'] = base64.b64encode(
            encrypt_blob(data.bind_password.encode(), LDAP_SECRET_AAD)
        ).decode()
    elif current_value.get('bind_secret'):
        values['bind_secret'] = current_value['bind_secret']
    if not values.get('bind_dn'):
        values.pop('bind_secret', None)
    if current is None:
        current = Setting(key=LDAP_KEY, value=values)
        db.add(current)
    else:
        current.value = values
    db.flush()
    return ldap_settings(db)


def _server(config):
    parsed = urlsplit(config['url'])
    use_ssl = parsed.scheme == 'ldaps'
    port = parsed.port or (636 if use_ssl else 389)
    tls = Tls(validate=ssl.CERT_REQUIRED if config['verify_tls'] else ssl.CERT_NONE)
    return Server(parsed.hostname, port=port, use_ssl=use_ssl, tls=tls, connect_timeout=5)


def _connection(config, user=None, password=None):
    connection = Connection(
        _server(config),
        user=user or None,
        password=password or None,
        auto_bind=False,
        receive_timeout=8,
        raise_exceptions=True,
    )
    connection.open()
    if config['start_tls']:
        if urlsplit(config['url']).scheme == 'ldaps':
            raise HTTPException(422, 'StartTLS cannot be combined with LDAPS')
        connection.start_tls()
    connection.bind()
    return connection


def test_ldap_connection(db):
    config = ldap_settings(db, include_secret=True)
    if not config['base_dn']:
        raise HTTPException(422, 'LDAP Base DN is required')
    try:
        connection = _connection(config, config['bind_dn'], config.get('bind_password'))
        ok = connection.search(
            config['base_dn'],
            '(objectClass=*)',
            search_scope=BASE,
            attributes=[],
            size_limit=1,
        )
        connection.unbind()
        if not ok:
            raise HTTPException(502, 'LDAP Base DN is not accessible')
        return {'ok': True, 'message': 'Połączenie, bind i Base DN działają prawidłowo.'}
    except HTTPException:
        raise
    except LDAPException:
        raise HTTPException(502, 'Nie udało się połączyć lub uwierzytelnić do serwera LDAP') from None


def authenticate_ldap(db, identity, password):
    config = ldap_settings(db, include_secret=True)
    if not config['enabled']:
        return None
    if not password:
        return None
    escaped = escape_filter_chars(identity)
    ldap_filter = config['user_filter'].replace('{username}', escaped)
    attributes = list(dict.fromkeys([
        config['username_attribute'],
        config['email_attribute'],
        config['first_name_attribute'],
        config['last_name_attribute'],
    ]))
    try:
        directory = _connection(config, config['bind_dn'], config.get('bind_password'))
        found = directory.search(
            config['base_dn'],
            ldap_filter,
            search_scope=SUBTREE,
            attributes=attributes,
            size_limit=2,
        )
        entries = list(directory.entries)
        directory.unbind()
        if not found or len(entries) != 1:
            return None
        entry = entries[0]
        user_dn = entry.entry_dn

        user_connection = _connection(config, user_dn, password)
        user_connection.unbind()

        def value(name):
            if not name:
                return ''
            attribute = getattr(entry, name, None)
            if attribute is None:
                return ''
            raw = attribute.value
            if isinstance(raw, list):
                raw = raw[0] if raw else ''
            return str(raw or '').strip()

        username = value(config['username_attribute']) or identity
        email = value(config['email_attribute'])
        return {
            'dn': user_dn,
            'username': username.lower(),
            'email': email.lower(),
            'first_name': value(config['first_name_attribute']),
            'last_name': value(config['last_name_attribute']),
        }
    except LDAPException:
        return None
