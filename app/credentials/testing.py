import os
from pathlib import Path
import tempfile
from urllib.parse import urlsplit, quote
import httpx
from fastapi import HTTPException
from app.awx import AwxError, test_awx_connection
from app.providers.registry import provider_for
from app.security.core import decrypt_secret


def test_connection(credential):
    if credential.type in {'proxmox', 'vmware', 'aws', 'azure', 'openstack'}:
        return provider_for(credential).test()
    secret = decrypt_secret(credential)
    try:
        if credential.type == 'awx':
            return test_awx_connection(
                credential.endpoint,
                credential.username,
                secret,
                verify_ssl=credential.verify_ssl,
            )
        if credential.type == 'ssh':
            import paramiko
            endpoint = urlsplit(credential.endpoint)
            if not endpoint.hostname:
                raise HTTPException(422, 'SSH connection test requires ssh://hostname:22 endpoint')
            with tempfile.TemporaryDirectory(prefix='cp-ssh-test-') as folder:
                key_file = None
                if secret.get('private_key'):
                    key_file = str(Path(folder) / 'key')
                    Path(key_file).write_text(secret['private_key'])
                    os.chmod(key_file, 0o600)
                with paramiko.SSHClient() as client:
                    if secret.get('known_hosts'):
                        known_hosts = Path(folder) / 'known_hosts'
                        known_hosts.write_text(secret['known_hosts'])
                        os.chmod(known_hosts, 0o600)
                        client.load_host_keys(str(known_hosts))
                        client.set_missing_host_key_policy(paramiko.RejectPolicy())
                    else:
                        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                    client.connect(endpoint.hostname, port=endpoint.port or 22, username=credential.username,
                                   password=secret.get('password'), key_filename=key_file,
                                   timeout=10, auth_timeout=10, banner_timeout=10, look_for_keys=False, allow_agent=False)
        elif credential.type == 'winrm':
            import winrm
            client = winrm.Session(credential.endpoint, auth=(credential.username, secret['password']),
                                   transport='ntlm', server_cert_validation='validate' if credential.verify_ssl else 'ignore', read_timeout_sec=20, operation_timeout_sec=15)
            result = client.run_ps('Write-Output CloudPortalConnectionTest')
            if result.status_code != 0:
                raise ValueError('WinRM authentication failed')
        elif credential.type == 'aws':
            import boto3
            from botocore.config import Config
            boto3.client('sts', region_name='us-east-1', aws_access_key_id=secret['access_key_id'],
                         aws_secret_access_key=secret['secret_access_key'], aws_session_token=secret.get('session_token'),
                         config=Config(connect_timeout=5, read_timeout=10, retries={'max_attempts': 0})).get_caller_identity()
        else:
            with httpx.Client(verify=credential.verify_ssl, timeout=15, follow_redirects=False, trust_env=False) as client:
                if credential.type == 'vmware':
                    response = client.post(credential.endpoint.rstrip('/') + '/api/session', auth=(credential.username, secret['password']))
                    response.raise_for_status()
                    ticket = response.json()
                    client.delete(credential.endpoint.rstrip('/') + '/api/session', headers={'vmware-api-session-id': ticket}).raise_for_status()
                elif credential.type == 'azure':
                    client.post('https://login.microsoftonline.com/' + quote(secret['tenant_id'], safe='') + '/oauth2/v2.0/token',
                                data={'client_id':secret['client_id'], 'client_secret':secret['client_secret'],
                                      'grant_type':'client_credentials', 'scope':'https://management.azure.com/.default'}).raise_for_status()
                elif credential.type == 'openstack':
                    endpoint = credential.endpoint.rstrip('/')
                    if not endpoint.endswith('/v3'):
                        endpoint += '/v3'
                    response = client.post(endpoint + '/auth/tokens', json={'auth': {
                        'identity': {'methods':['password'], 'password': {'user': {'name':credential.username,
                           'password':secret['password'], 'domain':{'name':secret.get('domain_name','Default')}}}},
                        'scope': {'project':{'name':secret['project_name'], 'domain':{'name':secret.get('domain_name','Default')}}}}})
                    response.raise_for_status()
                else:
                    raise HTTPException(422, 'No connection-test adapter registered for this credential type')
    except HTTPException:
        raise
    except AwxError as exc:
        raise HTTPException(502, str(exc)) from None
    except Exception:
        raise HTTPException(502, 'Credential authentication test failed; verify endpoint, authentication data and required fields') from None
    return {'ok': True, 'provider': credential.type}
