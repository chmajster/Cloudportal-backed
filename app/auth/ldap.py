import base64
import re
import ssl
from urllib.parse import urlsplit

from fastapi import HTTPException
from ldap3 import BASE, SUBTREE, Connection, Server, Tls
from ldap3.core.exceptions import LDAPException
from ldap3.utils.conv import escape_filter_chars
from sqlalchemy import or_, select

from app.models import Setting, User, now
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



def _diagnostic_step(key, label, status, message, detail=None):
    return {
        'key': key,
        'label': label,
        'status': status,
        'message': message,
        'detail': detail,
    }


def _ldap_result_detail(connection, error=None):
    result = getattr(connection, 'result', None) or {}
    parts = []
    description = str(result.get('description') or '').strip()
    message = str(result.get('message') or '').strip()
    if description and description.lower() != 'success':
        parts.append(description)
    if message:
        parts.append(message[:500])
    if not parts and error is not None:
        parts.append(type(error).__name__)
    return ' · '.join(parts) or None


def _safe_unbind(connection):
    if connection is None:
        return
    try:
        connection.unbind()
    except Exception:
        pass


def _entry_value(entry, name):
    if not name:
        return ''
    attribute = getattr(entry, name, None)
    if attribute is None:
        return ''
    raw = attribute.value
    if isinstance(raw, list):
        raw = raw[0] if raw else ''
    return str(raw or '').strip()


def _directory_diagnostics(config, identity='', password=None):
    steps = []
    profile = None
    password_tested = password is not None
    parsed = urlsplit(config['url'])
    directory = None

    try:
        directory = Connection(
            _server(config),
            user=config['bind_dn'] or None,
            password=config.get('bind_password') or None,
            auto_bind=False,
            receive_timeout=8,
            raise_exceptions=True,
        )
        directory.open()
        steps.append(_diagnostic_step(
            'socket',
            'Połączenie z serwerem',
            'ok',
            f"Połączenie TCP z {parsed.hostname}:{parsed.port or (636 if parsed.scheme == 'ldaps' else 389)} działa.",
        ))
    except LDAPException as error:
        steps.append(_diagnostic_step(
            'socket',
            'Połączenie z serwerem',
            'error',
            'Nie udało się otworzyć połączenia z serwerem LDAP.',
            _ldap_result_detail(directory, error),
        ))
        _safe_unbind(directory)
        return steps, profile, password_tested

    try:
        if config['start_tls']:
            directory.start_tls()
            steps.append(_diagnostic_step(
                'tls', 'TLS', 'ok', 'Negocjacja StartTLS zakończyła się poprawnie.'
            ))
        elif parsed.scheme == 'ldaps':
            steps.append(_diagnostic_step(
                'tls', 'TLS', 'ok', 'Połączenie używa LDAPS.'
            ))
        else:
            steps.append(_diagnostic_step(
                'tls', 'TLS', 'warning',
                'Połączenie LDAP działa bez TLS. Hasła mogą być przesyłane bez szyfrowania.'
            ))
    except LDAPException as error:
        steps.append(_diagnostic_step(
            'tls', 'TLS', 'error', 'Nie udało się zestawić TLS.',
            _ldap_result_detail(directory, error),
        ))
        _safe_unbind(directory)
        return steps, profile, password_tested

    try:
        directory.bind()
        steps.append(_diagnostic_step(
            'service_bind',
            'Bind serwisowy',
            'ok',
            'Bind konta serwisowego działa.' if config['bind_dn'] else 'Anonymous bind działa.',
            config['bind_dn'] or 'anonymous',
        ))
    except LDAPException as error:
        steps.append(_diagnostic_step(
            'service_bind',
            'Bind serwisowy',
            'error',
            'Serwer odrzucił bind konta serwisowego.' if config['bind_dn'] else 'Serwer odrzucił anonymous bind.',
            _ldap_result_detail(directory, error),
        ))
        _safe_unbind(directory)
        return steps, profile, password_tested

    try:
        found_base = directory.search(
            config['base_dn'],
            '(objectClass=*)',
            search_scope=BASE,
            attributes=[],
            size_limit=1,
        )
        if found_base and directory.entries:
            steps.append(_diagnostic_step(
                'base_dn', 'Base DN', 'ok', 'Base DN istnieje i jest dostępny.', config['base_dn']
            ))
        else:
            steps.append(_diagnostic_step(
                'base_dn',
                'Base DN',
                'error',
                'Base DN nie istnieje albo konto bind nie ma prawa go odczytać.',
                _ldap_result_detail(directory) or config['base_dn'],
            ))
            _safe_unbind(directory)
            return steps, profile, password_tested
    except LDAPException as error:
        steps.append(_diagnostic_step(
            'base_dn', 'Base DN', 'error', 'Wyszukiwanie Base DN zakończyło się błędem.',
            _ldap_result_detail(directory, error),
        ))
        _safe_unbind(directory)
        return steps, profile, password_tested

    identity = str(identity or '').strip()
    if not identity:
        _safe_unbind(directory)
        return steps, profile, password_tested

    escaped = escape_filter_chars(identity)
    ldap_filter = config['user_filter'].replace('{username}', escaped)
    attributes = list(dict.fromkeys([
        config['username_attribute'],
        config['email_attribute'],
        config['first_name_attribute'],
        config['last_name_attribute'],
    ]))

    try:
        directory.search(
            config['base_dn'],
            ldap_filter,
            search_scope=SUBTREE,
            attributes=attributes,
            size_limit=3,
        )
        entries = list(directory.entries)
    except LDAPException as error:
        steps.append(_diagnostic_step(
            'user_search',
            'Wyszukiwanie użytkownika',
            'error',
            'Filtr użytkownika spowodował błąd LDAP.',
            _ldap_result_detail(directory, error) or ldap_filter,
        ))
        _safe_unbind(directory)
        return steps, profile, password_tested

    if not entries:
        steps.append(_diagnostic_step(
            'user_search',
            'Wyszukiwanie użytkownika',
            'error',
            f'Filtr nie znalazł użytkownika "{identity}".',
            ldap_filter,
        ))
        _safe_unbind(directory)
        return steps, profile, password_tested
    if len(entries) != 1:
        steps.append(_diagnostic_step(
            'user_search',
            'Wyszukiwanie użytkownika',
            'error',
            f'Filtr zwrócił {len(entries)} wpisy. Logowanie wymaga dokładnie jednego wyniku.',
            ldap_filter,
        ))
        _safe_unbind(directory)
        return steps, profile, password_tested

    entry = entries[0]
    user_dn = entry.entry_dn
    steps.append(_diagnostic_step(
        'user_search',
        'Wyszukiwanie użytkownika',
        'ok',
        'Użytkownik został znaleziony dokładnie raz.',
        f'{user_dn} · filtr: {ldap_filter}',
    ))

    username = _entry_value(entry, config['username_attribute']) or identity
    email = _entry_value(entry, config['email_attribute'])
    first_name = _entry_value(entry, config['first_name_attribute'])
    last_name = _entry_value(entry, config['last_name_attribute'])
    profile = {
        'dn': user_dn,
        'username': username.lower(),
        'email': email.lower(),
        'first_name': first_name,
        'last_name': last_name,
    }
    missing = [
        name for name, value in (
            (config['username_attribute'], username),
            (config['email_attribute'], email),
            (config['first_name_attribute'], first_name),
            (config['last_name_attribute'], last_name),
        ) if not value
    ]
    steps.append(_diagnostic_step(
        'attributes',
        'Mapowanie atrybutów',
        'warning' if missing else 'ok',
        ('Brakuje wartości atrybutów: ' + ', '.join(missing) + '. CloudPortal może użyć wartości zastępczych dla części pól.')
        if missing else 'Wszystkie skonfigurowane atrybuty użytkownika są dostępne.',
        f"login={username or '—'}, e-mail={email or '—'}",
    ))

    _safe_unbind(directory)

    if password is None:
        steps.append(_diagnostic_step(
            'user_bind',
            'Bind użytkownika',
            'skipped',
            'Nie podano hasła użytkownika. Test hasła został pominięty.',
        ))
        return steps, profile, password_tested
    if not password:
        steps.append(_diagnostic_step(
            'user_bind',
            'Bind użytkownika',
            'error',
            'Hasło jest puste. CloudPortal nie wykonuje logowania LDAP z pustym hasłem.',
        ))
        return steps, profile, password_tested

    user_connection = None
    try:
        user_connection = _connection(config, user_dn, password)
        steps.append(_diagnostic_step(
            'user_bind', 'Bind użytkownika', 'ok', 'Hasło użytkownika zostało zaakceptowane przez LDAP.'
        ))
    except LDAPException as error:
        steps.append(_diagnostic_step(
            'user_bind',
            'Bind użytkownika',
            'error',
            'LDAP odrzucił hasło użytkownika albo nie pozwala na bind tego DN.',
            _ldap_result_detail(user_connection, error),
        ))
    finally:
        _safe_unbind(user_connection)

    return steps, profile, password_tested


def diagnose_ldap_login(db, identity='', password=None):
    config = ldap_settings(db, include_secret=True)
    identity = str(identity or '').strip().lower()
    steps = []

    if config['enabled']:
        steps.append(_diagnostic_step(
            'enabled', 'Stan LDAP', 'ok', 'Logowanie LDAP jest włączone w CloudPortal.'
        ))
    else:
        steps.append(_diagnostic_step(
            'enabled',
            'Stan LDAP',
            'error' if identity else 'warning',
            'Logowanie LDAP jest wyłączone. Test techniczny może działać, ale ekran logowania nie użyje LDAP.',
        ))

    if not config['base_dn']:
        steps.append(_diagnostic_step(
            'configuration', 'Konfiguracja', 'error', 'Base DN nie jest skonfigurowany.'
        ))
        return {
            'ok': False,
            'login_ready': False,
            'password_tested': password is not None,
            'message': 'Konfiguracja LDAP jest niekompletna.',
            'steps': steps,
            'profile': None,
        }

    existing_user = None
    ldap_identity = identity
    if identity:
        existing_user = db.scalar(select(User).where(or_(
            User.username == identity,
            User.email == identity,
        )))
        if existing_user and existing_user.auth_source == 'local':
            steps.append(_diagnostic_step(
                'login_routing',
                'Routing logowania',
                'error',
                f'CloudPortal ma lokalne konto "{existing_user.username}". Dla tej tożsamości logowanie kończy się na haśle lokalnym i LDAP nie jest próbowany.',
                'Użyj innego loginu LDAP albo zmień/usuń kolidujące lokalne konto, jeżeli jest to zamierzone.',
            ))
        elif existing_user and existing_user.auth_source == 'ldap':
            ldap_identity = existing_user.username
            unavailable = (
                existing_user.is_locked
                or bool(existing_user.locked_until and existing_user.locked_until > now())
                or not existing_user.is_active
                or existing_user.is_service_account
            )
            steps.append(_diagnostic_step(
                'login_routing',
                'Routing logowania',
                'error' if unavailable else 'ok',
                'Istniejące konto LDAP jest zablokowane lub nieaktywne.'
                if unavailable else 'Istniejące konto jest kierowane do LDAP.',
                f'CloudPortal user id={existing_user.id}, login={existing_user.username}',
            ))
        else:
            steps.append(_diagnostic_step(
                'login_routing',
                'Routing logowania',
                'ok',
                'Brak lokalnego konta o tej tożsamości. Po poprawnym bindzie CloudPortal może wykonać JIT provisioning.',
            ))

    directory_steps, profile, password_tested = _directory_diagnostics(
        config, ldap_identity, password
    )
    steps.extend(directory_steps)

    if identity and profile:
        ldap_username = profile['username'].lower()
        ldap_email = (profile['email'] or f'{ldap_username}@ldap.invalid').lower()
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,62}', ldap_username):
            steps.append(_diagnostic_step(
                'jit', 'JIT / konto CloudPortal', 'error',
                f'Atrybut loginu zwraca niedozwoloną nazwę "{ldap_username}".'
            ))
        elif len(ldap_email) > 254 or '@' not in ldap_email:
            steps.append(_diagnostic_step(
                'jit', 'JIT / konto CloudPortal', 'error',
                f'Atrybut e-mail zwraca nieprawidłową wartość "{ldap_email}".'
            ))
        elif existing_user and existing_user.auth_source == 'local':
            steps.append(_diagnostic_step(
                'jit', 'JIT / konto CloudPortal', 'error',
                'JIT nie może zastąpić istniejącego konta lokalnego.'
            ))
        elif existing_user and existing_user.auth_source == 'ldap':
            collisions = [
                db.scalar(select(User.id).where(User.username == ldap_username, User.id != existing_user.id)),
                db.scalar(select(User.id).where(User.email == ldap_email, User.id != existing_user.id)),
                db.scalar(select(User.id).where(User.external_id == profile['dn'], User.id != existing_user.id)),
            ]
            if any(collisions):
                steps.append(_diagnostic_step(
                    'jit', 'JIT / konto CloudPortal', 'error',
                    'Mapowane dane LDAP kolidują z innym kontem CloudPortal.'
                ))
            else:
                steps.append(_diagnostic_step(
                    'jit', 'JIT / konto CloudPortal', 'ok',
                    'Istniejące konto LDAP może zostać zaktualizowane po logowaniu.'
                ))
        else:
            collision = db.scalar(select(User).where(or_(
                User.username == ldap_username,
                User.email == ldap_email,
                User.external_id == profile['dn'],
            )))
            if collision and collision.auth_source != 'ldap':
                steps.append(_diagnostic_step(
                    'jit', 'JIT / konto CloudPortal', 'error',
                    f'Dane LDAP kolidują z lokalnym kontem "{collision.username}".'
                ))
            elif collision:
                steps.append(_diagnostic_step(
                    'jit', 'JIT / konto CloudPortal', 'ok',
                    f'Logowanie zostanie powiązane z istniejącym kontem LDAP "{collision.username}".'
                ))
            else:
                steps.append(_diagnostic_step(
                    'jit', 'JIT / konto CloudPortal', 'ok',
                    'Pierwsze poprawne logowanie utworzy konto LDAP w CloudPortal bez ról.'
                ))

    has_error = any(step['status'] == 'error' for step in steps)
    login_ready = bool(identity and password_tested and not has_error)
    if login_ready:
        message = 'Pełna ścieżka logowania LDAP przeszła diagnostykę.'
    elif has_error:
        message = 'Diagnostyka wykryła problem blokujący logowanie LDAP.'
    elif identity:
        message = 'Wyszukiwanie użytkownika działa. Podaj hasło, aby sprawdzić pełny bind użytkownika.'
    else:
        message = 'Połączenie, bind serwisowy i Base DN przeszły diagnostykę.'

    return {
        'ok': not has_error,
        'login_ready': login_ready,
        'password_tested': password_tested,
        'message': message,
        'steps': steps,
        'profile': profile,
    }


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
