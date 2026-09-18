from pathlib import Path


INSTALLER = (Path(__file__).resolve().parents[1] / "install.sh").read_text()


def test_installer_tracks_and_refreshes_managed_self_signed_certificate():
    assert 'tls_source_file="$config/tls/certificate-source"' in INSTALLER
    assert "managed-self-signed" in INSTALLER
    assert 'openssl x509 -in "$config/tls/server.crt" -noout -checkend 86400' in INSTALLER
    assert 'managed_certificate_matches_host "$config/tls/server.crt" || regenerate_tls=1' in INSTALLER
    assert '[[ -z ${previous_host:-} || "$previous_host" == "$backend_host" ]] || regenerate_tls=1' in INSTALLER
    assert 'certificate_key_matches "$config/tls/server.crt" "$config/tls/server.key" || regenerate_tls=1' in INSTALLER
    assert 'generate_managed_tls_certificate' in INSTALLER


def test_installer_does_not_silently_replace_custom_certificate():
    assert "tls_source='custom'" in INSTALLER
    assert "Configured custom TLS certificate and key do not match." in INSTALLER
    assert "Configured custom TLS certificate is expired or expires within 5 minutes." in INSTALLER
    assert "Configured custom TLS certificate does not cover host" in INSTALLER


def test_https_healthcheck_keeps_verification_enabled():
    assert "tls_health_curl_args=(-fsS --noproxy '*' --connect-timeout 5 --max-time 15)" in INSTALLER
    assert 'tls_health_curl_args+=(--cacert "$config/tls/server.crt")' in INSTALLER
    assert "--insecure" not in INSTALLER
    assert "-k " not in INSTALLER
    assert "For a private-CA certificate, install the issuing CA in the operating-system trust store" in INSTALLER
