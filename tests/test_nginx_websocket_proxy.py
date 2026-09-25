from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCKER_NGINX = (ROOT / 'scripts' / 'nginx-container.conf').read_text(encoding='utf-8')
INSTALLER = (ROOT / 'install.sh').read_text(encoding='utf-8')


def test_docker_nginx_proxies_console_websocket_upgrade():
    assert 'location ~ ^/api/v1/console-sessions/[A-Za-z0-9_-]+/websocket$ {' in DOCKER_NGINX
    assert 'proxy_http_version 1.1;' in DOCKER_NGINX
    assert 'proxy_set_header Upgrade $http_upgrade;' in DOCKER_NGINX
    assert 'proxy_set_header Connection "upgrade";' in DOCKER_NGINX
    assert 'proxy_read_timeout 3600s;' in DOCKER_NGINX
    assert 'proxy_buffering off;' in DOCKER_NGINX


def test_systemd_installer_proxies_console_websocket_upgrade():
    assert 'location ~ ^/api/v1/console-sessions/[A-Za-z0-9_-]+/websocket$ {' in INSTALLER
    assert 'proxy_http_version 1.1;' in INSTALLER
    assert r'proxy_set_header Upgrade \$http_upgrade;' in INSTALLER
    assert 'proxy_set_header Connection "upgrade";' in INSTALLER
    assert 'proxy_read_timeout 3600s;' in INSTALLER
    assert 'proxy_buffering off;' in INSTALLER


def test_large_upload_routes_stream_without_nginx_buffering():
    route = r'location ~ ^/api/v1/(?:appliances/ova-blueprints|instance-backups/upload)$ {'
    assert route in DOCKER_NGINX
    assert 'client_max_body_size 100g;' in DOCKER_NGINX
    assert 'client_body_timeout 7200s;' in DOCKER_NGINX
    assert 'proxy_request_buffering off;' in DOCKER_NGINX
    assert 'proxy_read_timeout 7200s;' in DOCKER_NGINX

    escaped_route = r'location ~ ^/api/v1/(?:appliances/ova-blueprints|instance-backups/upload)\$ {'
    assert escaped_route in INSTALLER
    assert 'client_max_body_size 100g;' in INSTALLER
    assert 'client_body_timeout 7200s;' in INSTALLER
    assert 'proxy_request_buffering off;' in INSTALLER
    assert 'proxy_read_timeout 7200s;' in INSTALLER
