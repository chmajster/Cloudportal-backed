import base64
import secrets
from dataclasses import dataclass, field

from nacl.secret import SecretBox
from sqlalchemy import select, text

from app.credentials.service import save_secret
from app.models import Credential, IPPool, ManagedVM, Provider, Role, User
from app.security.core import password_hasher


def decrypt_legacy_secret(encoded: str, key: bytes) -> str:
    """Decrypt Cloud Portal Crypto.php secretbox payload: base64(nonce + ciphertext)."""
    payload = base64.b64decode(encoded, validate=True)
    if len(key) != SecretBox.KEY_SIZE or len(payload) <= SecretBox.NONCE_SIZE:
        raise ValueError('Invalid legacy encryption material')
    nonce = payload[:SecretBox.NONCE_SIZE]
    ciphertext = payload[SecretBox.NONCE_SIZE:]
    return SecretBox(key).decrypt(ciphertext, nonce).decode()


def proxmox_username(token_id: str) -> str:
    value = token_id.split('!', 1)[0].strip()
    if not value or '@' not in value:
        raise ValueError('Legacy Proxmox token id does not contain user@realm')
    return value


@dataclass
class MigrationReport:
    created: dict[str, int] = field(default_factory=dict)
    existing: dict[str, int] = field(default_factory=dict)
    skipped: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def bump(self, bucket: str, name: str, count: int = 1):
        target = getattr(self, bucket)
        target[name] = target.get(name, 0) + count

    def as_dict(self):
        return {
            'created': self.created,
            'existing': self.existing,
            'skipped': self.skipped,
            'warnings': self.warnings,
        }


class LegacyCloudPortalMigrator:
    """Conservative importer from HomeLAB-Proxmox-CloudPortal MySQL schema.

    Only concepts with a direct backend equivalent are imported. Unsupported legacy
    data is counted and reported rather than being silently coerced.
    """

    def __init__(self, source_engine, target_db, source_key: bytes, actor_user_id: int,
                 *, apply: bool = False, grant_legacy_admin: bool = False):
        self.source = source_engine
        self.db = target_db
        self.key = source_key
        self.actor_user_id = actor_user_id
        self.apply = apply
        self.grant_legacy_admin = grant_legacy_admin
        self.report = MigrationReport()

    def rows(self, sql):
        with self.source.connect() as connection:
            return connection.execute(text(sql)).mappings().all()

    def scalar(self, sql):
        with self.source.connect() as connection:
            return connection.execute(text(sql)).scalar() or 0

    def run(self):
        role_map = self._roles()
        self._users(role_map)
        provider_map = self._proxmox_connections()
        self._vms(provider_map)
        self._networks()
        self._unsupported()
        self.db.flush()
        if not self.apply:
            self.db.rollback()
        return self.report

    def _roles(self):
        mapping = {}
        admin_role = self.db.scalar(select(Role).where(Role.name == 'Administrator'))
        for row in self.rows('SELECT id,name,slug FROM roles ORDER BY id'):
            if row['slug'] == 'admin' and self.grant_legacy_admin:
                if admin_role is None:
                    raise RuntimeError('Built-in Administrator role is unavailable')
                mapping[row['id']] = admin_role
                self.report.bump('existing', 'roles')
                continue
            name = ('Legacy ' + str(row['name']))[:100]
            role = self.db.scalar(select(Role).where(Role.name == name))
            if role is None:
                role = Role(name=name, permissions=[])
                self.db.add(role)
                self.db.flush()
                self.report.bump('created', 'roles')
            else:
                self.report.bump('existing', 'roles')
            mapping[row['id']] = role
        return mapping

    def _users(self, role_map):
        for row in self.rows('SELECT id,role_id,username,email,status,last_login_at FROM users ORDER BY id'):
            existing = self.db.scalar(select(User).where(User.username == row['username']))
            if existing is None:
                existing = self.db.scalar(select(User).where(User.email == row['email']))
            if existing is not None:
                self.report.bump('existing', 'users')
                continue
            role = role_map.get(row['role_id'])
            if role is None:
                self.report.bump('skipped', 'users')
                self.report.warnings.append(f"User {row['username']} skipped: legacy role not found")
                continue
            user = User(
                username=row['username'],
                email=row['email'],
                password_hash=password_hasher.hash(secrets.token_urlsafe(48)),
                is_active=row['status'] == 'active',
                is_locked=row['status'] == 'blocked',
                must_change_password=True,
                last_login_at=row['last_login_at'],
                roles=[role],
            )
            self.db.add(user)
            self.report.bump('created', 'users')

    def _proxmox_connections(self):
        mapping = {}
        rows = self.rows(
            'SELECT id,name,hostname,port,realm,api_token_id,api_token_secret_encrypted,verify_ssl,status '
            'FROM proxmox_connections ORDER BY id'
        )
        for row in rows:
            provider_name = ('Legacy ' + str(row['name']))[:100]
            provider = self.db.scalar(select(Provider).where(Provider.name == provider_name))
            if provider is not None:
                mapping[row['id']] = provider
                self.report.bump('existing', 'providers')
                continue
            try:
                secret = decrypt_legacy_secret(row['api_token_secret_encrypted'], self.key)
                username = proxmox_username(row['api_token_id'])
            except Exception:
                self.report.bump('skipped', 'providers')
                self.report.warnings.append(f"Proxmox connection {row['name']} skipped: token secret cannot be decrypted")
                continue
            credential = Credential(
                name=('Legacy ' + str(row['name']) + ' credential')[:100],
                type='proxmox',
                endpoint=f"https://{row['hostname']}:{int(row['port'])}",
                username=username,
                verify_ssl=bool(row['verify_ssl']),
                encrypted_secret=b'',
            )
            self.db.add(credential)
            self.db.flush()
            save_secret(self.db, credential, {
                'token_id': row['api_token_id'],
                'token_secret': secret,
            })
            provider = Provider(
                name=provider_name,
                type='proxmox',
                credentials_id=credential.id,
            )
            self.db.add(provider)
            self.db.flush()
            mapping[row['id']] = provider
            self.report.bump('created', 'credentials')
            self.report.bump('created', 'providers')
            if row['status'] == 'disabled':
                self.report.warnings.append(
                    f"Proxmox connection {row['name']} was disabled in legacy portal; imported provider must be reviewed manually"
                )
        return mapping

    def _vms(self, provider_map):
        rows = self.rows(
            'SELECT id,connection_id,vmid,node_name,name,status,deleted_at FROM virtual_machines ORDER BY id'
        )
        for row in rows:
            provider = provider_map.get(row['connection_id'])
            if provider is None:
                self.report.bump('skipped', 'managed_vms')
                continue
            existing = self.db.scalar(select(ManagedVM).where(
                ManagedVM.provider_id == provider.id,
                ManagedVM.vm_id == row['vmid'],
            ))
            if existing is not None:
                self.report.bump('existing', 'managed_vms')
                continue
            destroyed = row['deleted_at'] is not None or row['status'] == 'deleted'
            self.db.add(ManagedVM(
                provider_id=provider.id,
                deployment_id=None,
                node=row['node_name'],
                vm_id=row['vmid'],
                name=row['name'],
                management_mode='external',
                lifecycle_status='destroyed' if destroyed else 'active',
                created_by=self.actor_user_id,
                destroyed_at=row['deleted_at'] if destroyed else None,
            ))
            self.report.bump('created', 'managed_vms')

    def _networks(self):
        rows = self.rows(
            'SELECT id,connection_id,name,bridge,vlan_id,subnet,gateway,dns_servers,enabled FROM networks ORDER BY id'
        )
        for row in rows:
            existing = self.db.scalar(select(IPPool).where(IPPool.cidr == row['subnet']))
            if existing is not None:
                self.report.bump('existing', 'ip_pools')
                continue
            dns = [item.strip() for item in (row['dns_servers'] or '').split(',') if item.strip()]
            pool_name = ('Legacy ' + str(row['name']))[:100]
            if self.db.scalar(select(IPPool).where(IPPool.name == pool_name)):
                pool_name = (pool_name[:90] + '-' + str(row['id']))[:100]
            self.db.add(IPPool(
                name=pool_name,
                cidr=row['subnet'],
                gateway=row['gateway'],
                dns_servers=dns,
                excluded_addresses=[],
                is_active=bool(row['enabled']),
                created_by=self.actor_user_id,
            ))
            self.report.bump('created', 'ip_pools')
            if row['bridge'] or row['vlan_id']:
                self.report.warnings.append(
                    f"Network {row['name']}: bridge/VLAN metadata is not represented by IPAM and was not copied"
                )

    def _unsupported(self):
        for table, key in [
            ('projects', 'projects'),
            ('project_users', 'project_memberships'),
            ('resource_plans', 'resource_plans'),
            ('quotas', 'quotas'),
            ('snapshots', 'snapshot_history'),
            ('jobs', 'legacy_jobs'),
        ]:
            try:
                count = int(self.scalar(f'SELECT COUNT(*) FROM {table}'))
            except Exception:
                continue
            if count:
                self.report.skipped[key] = count
        if self.report.skipped:
            self.report.warnings.append(
                'Legacy projects/quotas/plans/history without a direct Cloudportal-backed model are reported but not coerced.'
            )
