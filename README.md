# Cloudportal-backed

Centralny backend dla [Cloud Portal](https://github.com/chmajster/HomeLAB-Proxmox-CloudPortal): użytkownicy, uwierzytelnianie, permissions/RBAC, tokeny, szyfrowane credentiale, Proxmox, deploymenty i zadania Terraform/Ansible. PHP komunikuje się wyłącznie przez HTTPS API; nie otrzymuje sekretów infrastruktury ani Terraform state.

## Instalacja przez jeden one-liner

Wspierane systemy: **Ubuntu 24.04 i 26.04 LTS, Debian 12 i 13 oraz Red Hat Enterprise Linux 9 i 10**, amd64/arm64, systemd. RHEL musi być zarejestrowany i mieć dostęp do repozytoriów BaseOS oraz AppStream. Minimum praktyczne: 2 vCPU, 4 GiB RAM, 10 GiB wolnego dysku. Instalator instaluje PostgreSQL, osobny Redis (Valkey na RHEL 10), Python/venv, Terraform, Ansible, Nginx/TLS, API, dispatcher i workery. Terraform i Ansible działają jako `cloudportal`, bez roota. Na RHEL instalator zachowuje SELinux w trybie enforcing, dodaje wymagane etykiety/porty i otwiera port HTTPS, jeżeli `firewalld` jest aktywny.

Dla publicznego repozytorium użyj GitHub Contents API, aby zawsze pobrać aktualną wersję z gałęzi `main` (bez nieaktualnej kopii z cache GitHub Raw):

```bash
curl -fsSL -H 'Accept: application/vnd.github.raw+json' 'https://api.github.com/repos/chmajster/Cloudportal-backed/contents/install.sh?ref=main' | sudo bash
```

Obsługę bieżącego systemu można sprawdzić bez zmian w systemie i bez uprawnień roota:

```bash
curl -fsSL -H 'Accept: application/vnd.github.raw+json' 'https://api.github.com/repos/chmajster/Cloudportal-backed/contents/install.sh?ref=main' | bash -s -- --check-platform
```

Instalacja unattended / własny host i port:

```bash
curl -fsSL -H 'Accept: application/vnd.github.raw+json' 'https://api.github.com/repos/chmajster/Cloudportal-backed/contents/install.sh?ref=main' | sudo bash -s -- --non-interactive --host backend.example.com --port 8443 --workers 3
```

Jeżeli nowy instalator wykryje aktywny `/run/cloudportal-install.lock`, domyślnie przejmuje instalację: zatrzymuje usługi aplikacji Cloudportal (API, dispatcher, workery oraz updater), kończy poprzedni proces instalatora i po zwolnieniu blokady kontynuuje instalację. Blokada jest teraz utrzymywana przez osobny proces `flock --close`, dzięki czemu nie jest dziedziczona przez `apt`, `curl`, `systemctl`, Pythona, Terraform ani inne procesy potomne. Dla zgodności ze starymi uruchomieniami instalator dodatkowo skanuje `/proc/*/fd`, więc potrafi znaleźć lock niewidoczny w `lslocks`. Zachowanie można wyłączyć przez `--no-takeover`.

Awaryjne usunięcie runtime Cloudportal z zachowaniem bazy, konfiguracji i danych:

```bash
curl -fsSL -H 'Accept: application/vnd.github.raw+json' 'https://api.github.com/repos/chmajster/Cloudportal-backed/contents/install.sh?ref=main' | sudo bash -s -- --force-uninstall
```

Pełny, destrukcyjny reset razem z bazą PostgreSQL, `/etc/cloudportal-backed`, `/var/lib/cloudportal-backed`, backupami i użytkownikiem systemowym:

```bash
curl -fsSL -H 'Accept: application/vnd.github.raw+json' 'https://api.github.com/repos/chmajster/Cloudportal-backed/contents/install.sh?ref=main' | sudo bash -s -- --force-uninstall --purge-data
```

`--purge-data` działa wyłącznie razem z `--force-uninstall`.


Instalacja z interfejsem terminalowym `dialog` jest uruchamiana jawnie przez `--gui` lub alias `-gui`. Działa również przy `curl | sudo bash`, ponieważ formularze czytają wejście bezpośrednio z `/dev/tty`. GUI pozwala ustawić host, port HTTPS, liczbę workerów, backup i retencję, a przed zmianami pokazuje podsumowanie:

```bash
curl -fsSL -H 'Accept: application/vnd.github.raw+json' 'https://api.github.com/repos/chmajster/Cloudportal-backed/contents/install.sh?ref=main' | sudo bash -s -- --gui
```

Równoważny alias:

```bash
curl -fsSL -H 'Accept: application/vnd.github.raw+json' 'https://api.github.com/repos/chmajster/Cloudportal-backed/contents/install.sh?ref=main' | sudo bash -s -- -gui
```


Opcjonalny automatyczny backup PostgreSQL można włączyć podczas instalacji. Timer systemd uruchamia backup codziennie, a retencja usuwa wyłącznie katalogi backupów starsze niż wskazany limit:

```bash
sudo ./install.sh --host backend.example.com --enable-backups --backup-retention-days 14
```

Ręczne komendy po instalacji: `cloudportal-backup` oraz destrukcyjne `cloudportal-restore --backup KATALOG --yes-replace-database`. Restore weryfikuje SHA-256 dumpa i fingerprint istniejącego master key; master key nie jest kopiowany do katalogu backupu.

Jeżeli repozytorium zostanie przełączone na prywatne, anonimowe pobranie zwróci 404. Wtedy zapisz w `/root/cloudportal-github.conf` (właściciel root, tryb 600) konfigurację curl z tokenem GitHub mającym wyłącznie dostęp Contents: read do tego repozytorium:

```text
header = "Authorization: Bearer YOUR_GITHUB_READ_TOKEN"
```

Następnie wykonaj jeden one-liner; nie wymaga klonowania repozytorium:

```bash
sudo bash -o pipefail -c 'curl --config /root/cloudportal-github.conf -fsSL -H "Accept: application/vnd.github.raw+json" "https://api.github.com/repos/chmajster/Cloudportal-backed/contents/install.sh?ref=main" | bash -s -- --github-config /root/cloudportal-github.conf --non-interactive --host backend.example.com --port 8443'
```

Można też uruchomić pobrany instalator: `sudo ./install.sh --host backend.example.com --port 8443 --github-token-file /root/github-read.token`. `--ref` pozwala zainstalować wskazaną gałąź/tag/commit. Wersja instalatora i wskazany ref powinny być zgodne.

Instalator generuje certyfikat self-signed, jeżeli nie podano `--cert-file` i `--cert-key`. Certyfikat generowany przez instalator jest oznaczany jako zarządzany i przy kolejnych uruchomieniach zostanie automatycznie odtworzony, jeżeli wygasł, nie pasuje do klucza albo nie obejmuje aktualnego `--host`. Certyfikat dostarczony przez `--cert-file`/`--cert-key` nigdy nie jest automatycznie zastępowany; błędna nazwa hosta, wygaśnięcie lub niedopasowany klucz zatrzymują instalację z jednoznacznym błędem. Dla prywatnego CA jego certyfikat musi znajdować się w systemowym trust store. Zaufaj `/etc/cloudportal-backed/tls/server.crt` na serwerze PHP przy certyfikacie self-signed albo użyj certyfikatu własnego/publicznego CA. Weryfikacja TLS w portalu pozostaje domyślnie włączona. Port API na loopback `8765` pozostaje niewystawiony przez Nginx; dostęp sieciowy zapewnia port HTTPS `8443`.

Dopiero po udanym healthchecku instalator tworzy administratora z początkowymi danymi **`admin` / `admin`** oraz losowy **Initial Administrator Token**. Token jest wyświetlany tylko raz na standardowym wyjściu. Hasło jest przechowywane wyłącznie jako hash Argon2id, a token jako SHA-256; instalator nie zapisuje ich jawnie do plików ani logów. Sesja utworzona początkowym hasłem może wejść tylko do widoku konta i zmienić hasło. Initial Administrator Token nadal umożliwia jednorazową konfigurację połączenia PHP.

Ponowne uruchomienie zachowuje bazę, konta, tokeny, klucz szyfrujący, Redis i Terraform state. Brak istniejącego master key zatrzymuje reinstalację zamiast tworzyć niezgodny klucz. Kod jest instalowany w wersjonowanych katalogach `/opt/cloudportal-backed/releases`; dane pozostają w `/var/lib/cloudportal-backed`. Kopia zapasowa musi obejmować **bazę, master key i workspaces**; klucz przechowuj oddzielnie od kopii bazy.

## Lokalny panel administracyjny

Backend udostępnia własny panel pod `https://HOST:PORT/ui/`; wejście na `/` przekierowuje do panelu. Przy pierwszym logowaniu użyj `admin` / `admin` i ustaw nowe hasło o długości co najmniej 12 znaków. Do czasu zmiany hasła pozostałe operacje sesji administratora są blokowane przez API. Panel umożliwia zarządzanie użytkownikami, rolami i permissions, tokenami API, credentialami, providerami, deploymentami, zadaniami i audytem oraz zmianę własnego hasła.

Panel korzysta z tego samego API i tego samego RBAC co pozostali klienci — nie omija autoryzacji backendu. Elementy nawigacji i akcje są ukrywane zgodnie z efektywnymi permissions, ale każdą operację ponownie weryfikuje API. Access i refresh token są przechowywane wyłącznie w `sessionStorage`, więc zamknięcie karty usuwa lokalną sesję przeglądarki. Panel oraz jego zasoby są serwowane lokalnie przez backend, bez zewnętrznych skryptów i fontów.

## Podłączenie PHP

W powiązanej wersji Cloud Portal:

1. Na świeżym backendzie otwórz `/ui/`, zaloguj się jako `admin` / `admin` i ustaw nowe hasło. Nie wystawiaj instalacji do niezaufanej sieci przed wykonaniem tego kroku.
2. W panelu backendu utwórz konto serwisowe, przypisz mu rolę `Portal Service`, a następnie utwórz dla niego token o scope `portal.connect`. Zachowaj wyświetlony jednorazowo token.
3. W nowej instalacji PHP uruchom lokalnie `php bin/backend-setup.php` jako użytkownik PHP i otwórz `/settings/infrastructure/backend`. W istniejącej instalacji stronę może otworzyć lokalny administrator.
4. Wprowadź adres HTTPS, token konta serwisowego i klucz konfiguracji, jeśli to nowa instalacja. `Testuj połączenie` sprawdza `/health` i `/auth/me`.
5. Zapisz konfigurację, zaloguj się administratorem backendu i unieważnij niepotrzebny Initial Administrator Token.

Operacje interaktywne są autoryzowane tokenem zalogowanego użytkownika; token serwisowy potwierdza wyłącznie, że żądanie pochodzi z zaufanej instancji CloudPortal i nie rozszerza permissions użytkownika. PHP przechowuje własną sesję i chronioną konfigurację połączenia. Nie tworzy kopii backendowych kont ani ról. Uprawnienia są odczytywane z `/auth/me` na każdym żądaniu, a API sprawdza je ponownie. Przeglądarka wysyła akcje do proxy `/backend-api` w PHP, które przekazuje JSON, `X-Request-ID`, `Idempotency-Key` i status API. Wyłącznie backend otwiera połączenia z infrastrukturą i wykonuje Terraform/Ansible. Brak backendu oznacza 503; nie uruchamia lokalnego wykonawcy PHP.

Istniejąca baza starego portalu nie jest automatycznie kasowana. Konta, RBAC i credentiale centralnego trybu są źródłem prawdy w backendzie. Nowy panel backendu udostępnia m.in. IPAM, Inventory/ManagedResource, Proxmox lifecycle/snapshot/backup/console, harmonogramy, webhooki oraz monitoring. Istniejące VM mogą zostać najpierw dodane do inventory jako `external`, a następnie przejęte kontrolowanym workflow `adopt`: backend pobiera live config, tworzy desired-state, wykonuje `terraform import` i **plan-only**. Automatyczny apply po adopcji jest zabroniony; operator najpierw ocenia drift.

## API i bezpieczeństwo

Kontrakt: `/openapi.json` (OpenAPI 3.x), Swagger `/docs`. Prefix `/api/v1`. Modele odpowiedzi obejmują publiczne pola users, RBAC, tokens, credentials, providers, deployments, jobs/logs, audit i health; nie zawierają hashy ani zaszyfrowanych sekretów. OpenAPI opisuje również wymagane nagłówki idempotencji. Publiczne są logowanie, odświeżenie sesji, realizacja tokena resetu i healthcheck. Endpointy zasobów wymagają Bearer token i granularnego permission. Nie ma endpointu wykonującego shell.

- Hasła użytkowników: 12–256 znaków, Argon2id. Jedynym wyjątkiem jest tymczasowe hasło `admin` świeżej instalacji, które przed udostępnieniem operacji administracyjnych musi zostać zmienione na hasło zgodne z polityką. Domyślny lockout po 5 błędach na 15 minut, dodatkowe limity na tożsamość i IP w Redis. Niedostępny limiter blokuje ruch (503).
- Sesje: opaque access token 15 minut, rotowany refresh token 8 godzin. Ponowne użycie refresh tokena unieważnia całą rodzinę. Logout, reset i zmiana hasła odwołują odpowiednie tokeny. Konta serwisowe nie logują się hasłem.
- Tokeny API: zakres jest przecięciem zapisanych scopes i **aktualnych** permissions konta. Osoba nadająca role/tokeny nie może nadać uprawnień spoza własnego zakresu. Nie można usunąć ostatniego aktywnego administratora.
- Reset hasła: administrator wydaje token ważny 15 minut, wyświetlany raz; użytkownik podaje go formularzem POST `/password-reset` w portalu. Token nie jest umieszczany w URL. Wydanie tokena unieważnia sesje, a realizacja jest atomowa i jednorazowa. Nie ma automatycznej wysyłki e-mail.
- Credentiale: AES-256-GCM, losowy nonce, AAD z ID credentiala. Klucz 32 bajty w `/etc/cloudportal-backed/master.key`, 0600, właściciel `cloudportal`. API zwraca wyłącznie maskę i flagę `configured`. Pole `secrets` pominięte w PUT zachowuje wartość. Podane `secrets` zastępuje komplet sekretów. UI ma typowane formularze dla Proxmox/VMware/SSH/WinRM/AWS/Azure/OpenStack zamiast surowego JSON. Credential może mieć `expires_at` i `rotation_due_at`; worker blokuje wykonanie z wygasłym credentialem, a monitoring generuje alerty o wygaśnięciu/rotacji.
- Testy credentiali: Proxmox, VMware, SSH z obowiązkowym known_hosts, WinRM z weryfikacją certyfikatu, AWS STS, Azure OAuth, OpenStack Keystone. Typ `other` przechowuje zaszyfrowane dane, ale wymaga osobnego adaptera testującego.
- Idempotency-Key UUID: obowiązkowy przy tworzeniu deploymentu, tworzeniu joba i niszczeniu VM, opcjonalny dla innych operacji tworzących. PostgreSQL przechowuje atomowy wynik i HMAC requestu; konflikt payloadu daje 409. Sekrety jednorazowe nie są odtwarzane przy powtórzeniu.
- X-Request-ID UUID łączy request, job, worker, logi i audit. Audit nie zawiera payloadów ani sekretów. Logi wykonawców są redagowane i limitowane.
- Usunięcie użytkownika usuwa dane tożsamości i dostęp, zachowując techniczny ID dla historii deploymentów/audytu.
- Credentiala używanego przez oczekujące lub wykonywane zadanie nie można edytować ani usunąć. Credential SSH/WinRM pozostaje chroniony także jako część workflow niedestroyowanego deploymentu. Zmiana hasła unieważnia również niewykorzystane tokeny resetu.

Domyślne role to Administrator, Infrastructure Administrator, Operator, Viewer, Auditor i Portal Service. Permissions definiuje `app/rbac/service.py`; nazwy ról nie są używane do autoryzacji.

## Provisioning

Provisioning jest katalogowy i wspólny dla wielu providerów. Zatwierdzone manifesty Terraform obejmują: `proxmox-vm`, `aws-ec2`, `azure-linux-vm`, `openstack-vm` i `vmware-vsphere-vm`. Każdy manifest ma jawny `version`, model Pydantic zmiennych oraz provider. CI wykonuje `terraform validate` dla każdego katalogu z `template.json`. Sekrety providerów nigdy nie trafiają do HCL/tfvars — executor tłumaczy zaszyfrowany credential na standardowe zmienne środowiskowe providera.

Szablon `proxmox-vm` klonuje **istniejący szablon QEMU cloud-init** przez bpg/proxmox 0.111.x. Wymaga storage, bridge, template_id i węzła; obsługuje DHCP albo statyczny IPv4 z IPAM. Template musi mieć działający qemu-guest-agent oraz cloud-init. Token PVE musi mieć wymagane uprawnienia do VM/storage/guest-agent.

`POST /deployments` tworzy deployment i trwały job w jednej transakcji. Dispatcher przenosi zadania z PostgreSQL do Redis/RQ; awaria Redis nie usuwa jobów. Worker atomowo przejmuje job i ponownie sprawdza uprawnienia. Jeden deployment ma jedno aktywne zadanie. Terraform state jest szyfrowany AES-GCM i przechowywany w PostgreSQL z SHA-256 oraz numerem wersji; przed wykonaniem jest odtwarzany na dowolnym workerze. PostgreSQL advisory lock zapewnia blokadę rozproszoną, a lokalny flock pozostaje drugą warstwą ochrony. Dzięki temu workery na oddzielnych hostach nie wymagają wspólnego filesystemu; muszą współdzielić PostgreSQL, Redis i ten sam master key. Częściowy state jest zapisywany również po błędzie executora.

Przepływ z opcją Ansible: init → plan → apply → adres VM z guest agent → wait_for_connection SSH/WinRM → zatwierdzony playbook → walidacja → successful. Bez Ansible kończy się po apply. Host SSH jest sprawdzany względem known_hosts z credentiala (również wpisy SSH CA); WinRM używa HTTPS i weryfikacji certyfikatu. Szablon musi zawierać przygotowane zaufanie kluczy/certyfikatów i konto odpowiednie dla playbooka. Bootstrap Linux wymaga bezhasłowego sudo tego konta.

API nie przyjmuje HCL, ścieżek plików, dowolnych playbooków, pluginów, zmiennych `ansible_*` ani komend. Dozwolone są tylko playbooki z repozytorium. OpenTofu używa wspólnego executora, jeżeli administrator zainstaluje `tofu` w `/usr/local/bin` lub `/usr/bin`.

Anulowanie kończy grupę procesów i zachowuje state. Joby mają jawny retry lineage (`retry_of`, `attempt`). Blueprint może mieć `recovery_policy=preserve` albo `destroy_on_failure`; automatyczny recovery jest osobnym, audytowalnym `terraform.destroy` jobem, a nie ukrytym rollbackiem. Blueprint może też wymagać `blueprints.approve`. Logi: `GET /jobs/{id}/logs?after=ID&limit=200`.

## Uruchomienie w Docker Compose

Alternatywa dla instalacji systemd. Przygotuj `.env` z losowym, URL-safe `CP_POSTGRES_PASSWORD`, pliki `tls/server.crt` i `tls/server.key`, następnie:

```bash
docker compose build
docker compose run --rm bootstrap
docker compose up -d
```

`bootstrap` jest jednorazowym interaktywnym kontenerem usuwanym po wyświetleniu tokena. API i worker działają jako UID 10001, z read-only root filesystem, wyłączonymi capabilities i no-new-privileges. PostgreSQL/Redis/API nie publikują portów hosta; jedyny port hosta to TLS Nginx 8443. Nie podłączaj niezaufanych kontenerów do sieci backend.

## Testy i obsługa

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
TEST_REDIS_URL=redis://localhost:6379/15 .venv/bin/pytest -q
```

Testy używają domyślnie tymczasowego SQLite oraz dedykowanej bazy Redis 15. **Nie podawaj produkcyjnego URL**: testy czyszczą bazę testową. CI definiuje PostgreSQL, Terraform validate, budowę kontenera oraz test instalacji i reinstalacji systemd. Wynik tych zadań zależy od dostępności runnerów GitHub Actions. `TEST_DATABASE_URL` wskazuje wyłącznie pustą testową bazę PostgreSQL. `REDIS_SERVER_BINARY=/path/to/redis-server` uruchamia lokalny Redis na 16389 na czas testów.

Live E2E są zawsze jawnie opt-in i nie uruchamiają się automatycznie na produkcji. `scripts/e2e-proxmox.py` wykonuje pełny create → workflow → destroy dla Proxmox. `scripts/e2e-proxmox-operations.py` na wskazanej testowej VM sprawdza console-ticket, backup, restore do jawnie podanego wolnego VMID oraz cleanup odtworzonej VM. `scripts/e2e-terraform-provider.py` jest wspólnym runnerem dla `aws-ec2`, `azure-linux-vm`, `openstack-vm`, `vmware-vsphere-vm` i innych zatwierdzonych template: testuje credential, tworzy deployment, czeka na job, weryfikuje `ManagedResource`, a następnie wykonuje destroy. Runnery wymagają HTTPS, token file z mode 0600 oraz jawnej flagi zezwalającej na create/destroy.

## Blueprinty i Hostname Manager

Backend jest źródłem prawdy dla Blueprintów oraz rezerwacji hostname. Blueprint przechowuje wersjonowany schemat formularza, definicję deploymentu, widoczność (`backend`, `cloudportal`, `api`), ograniczenia ról/użytkowników i workflow będący walidowanym DAG-em. Niedozwolone typy kroków, brakujące zależności, cykle i nieznane zmienne są odrzucane przed utworzeniem zadania.

Najważniejsze endpointy:

- `GET/POST /api/v1/blueprints`, `PUT/DELETE /api/v1/blueprints/{id}`;
- `POST /api/v1/blueprints/{id}/execute` — waliduje formularz, rezerwuje hostname, tworzy deployment i trwały job;
- `GET/POST /api/v1/hostname-schemes`;
- `GET /api/v1/hostnames`, `POST /api/v1/hostnames/generate` oraz operacje `assign`/`release`.

Wzorce hostname obsługują: `{location}`, `{environment}`, `{env}`, `{application}`, `{service}`, `{role}`, `{os}`, `{cluster}`, `{site}`, `{year}`, `{number}` i `{random}`. Sekwencja jest blokowana transakcyjnie, nazwa ma unikalny indeks, a historia nie jest usuwana przy zwolnieniu rezerwacji.

Żądania z CloudPortal wysyłają jednocześnie token zalogowanego użytkownika i osobny `X-Portal-Token` konta serwisowego z `portal.connect`. Backend zapisuje źródło w zadaniu i audycie. Sam nagłówek `X-Portal-Source: CloudPortal` bez poprawnego uwierzytelnienia usługi jest odrzucany.

Usługi: `cloudportal-api`, `cloudportal-dispatcher`, `cloudportal-worker@1`, `cloudportal-redis`. Sekrety konfiguracyjne: `/etc/cloudportal-backed/backend.env`. Dane: `/var/lib/cloudportal-backed`. Sprawdzenie: `systemctl status cloudportal-api cloudportal-dispatcher cloudportal-worker@1` i `journalctl -u cloudportal-api`.

### Test wspólny z PHP

Test `tests/test_portal_http_e2e.py` uruchamia prawdziwe FastAPI przez TLS oraz PHP built-in server, konfiguruje portal, loguje administratora, tworzy użytkownika/rolę/credential oraz przekazuje zadanie Ansible do backendu i je anuluje. Sprawdza zachowanie JSON, idempotencję, request ID/audit, RBAC, blokadę lokalnych executorów i niedostępność backendu. Nie używa atrap HTTP. Wymaga osobnego checkoutu PHP bez `config/backend.json` oraz PHP z rozszerzeniami aplikacji. Zadanie `api` w CI pobiera powiązany commit portalu i uruchamia ten test wraz z PostgreSQL:

```bash
PORTAL_PATH=/path/to/HomeLAB-Proxmox-CloudPortal PHP_BINARY=php TEST_REDIS_URL=redis://localhost:6379/15 .venv/bin/pytest -q tests/test_portal_http_e2e.py
```

Status weryfikacji przy przygotowaniu: lokalne testy API/RBAC/workerów/klienta PHP i integracji HTTPS przeszły. Faktyczna instalacja systemd, kontener oraz współbieżność PostgreSQL wymagają uruchomienia zadań CI (pierwszy run prywatnego repozytorium zakończył się przed przydzieleniem runnerów). Pełnego provisioning E2E na rzeczywistym Proxmoxie nie wykonano bez dostępu do infrastruktury.


## Operacyjność i retencja

Dispatcher wykonuje trwałe harmonogramy Terraform, podpisane webhooki HMAC z retry, skan alertów systemowych oraz okresowe czyszczenie danych technicznych. Retencja obejmuje JobLog, Audit, zakończone WebhookDelivery, rekordy idempotency oraz zwolnione IP/hostname; czasy są konfigurowane przez `CP_RETENTION_*`. `/metrics` wystawia format Prometheus, `/alerts` agreguje m.in. stan DB/Redis/dispatcher/workers, stuck jobs, wyczerpane webhooki oraz credential expiry/rotation. Opcjonalne trace OTLP włącza `CP_OTEL_EXPORTER_OTLP_ENDPOINT`.


## Zewnętrzny Vault / KMS

Domyślny tryb `CP_SECRET_BACKEND=local` zachowuje dotychczasowe AES-256-GCM z lokalnym `master.key`. Opcjonalnie nowe sekrety, Terraform state i webhook secrets mogą używać **envelope encryption**: losowy 256-bitowy DEK szyfruje payload AES-GCM, a sam DEK jest owijany przez AWS KMS albo HashiCorp Vault Transit. Backend nie wysyła całych credentiali do KMS/Vault.

AWS KMS (uwierzytelnienie przez standardowy AWS credential chain/instance role):

```text
CP_SECRET_BACKEND=aws-kms
CP_AWS_KMS_KEY_ID=arn:aws:kms:eu-central-1:123456789012:key/...
CP_AWS_KMS_REGION=eu-central-1
```

HashiCorp Vault Transit:

```text
CP_SECRET_BACKEND=vault-transit
CP_VAULT_ADDR=https://vault.example.com
CP_VAULT_TOKEN_FILE=/etc/cloudportal-backed/vault.token
CP_VAULT_TRANSIT_MOUNT=transit
CP_VAULT_TRANSIT_KEY=cloudportal-backed
```

Plik tokenu Vault musi należeć do użytkownika procesu i mieć mode 0600. Dla produkcji preferuj krótko żyjący token/agent zamiast stałego root tokena. Envelope zawiera tylko identyfikatory backendu i owinięty DEK; plaintext DEK istnieje wyłącznie w pamięci procesu.

Stary format lokalny pozostaje czytelny po zmianie backendu, dlatego migracja może być stopniowa. Aby jawnie przepakować istniejące Credential, TerraformState i WebhookEndpoint, najpierw skonfiguruj zewnętrzny backend, wykonaj dry-run, a następnie:

```bash
python scripts/rewrap-secrets.py
python scripts/rewrap-secrets.py --apply
```

Nie usuwaj lokalnego `master.key` po przełączeniu, dopóki wszystkie stare ciphertexty nie zostaną przepakowane i backup/restore nie zostanie przetestowany. AWS KMS/Vault musi być osiągalny przez każdy worker, który odszyfrowuje dane.


## Migracja ze starego Cloud Portal

Jawny importer `scripts/migrate-legacy-cloudportal.py` przenosi wyłącznie dane mające bezpieczny odpowiednik w backendzie: użytkowników i role, połączenia Proxmox jako szyfrowane credentiale+providery, istniejące VM jako Inventory `external` oraz podsieci jako IPAM pools. Domyślnie działa jako **dry-run** i wycofuje transakcję. Dopiero `--apply` zapisuje zmiany.

Źródłowy klucz `APP_KEY/ENCRYPTION_KEY` zapisz jako base64 w osobnym pliku mode 0600 i ustaw wraz z DSN MySQL przez zmienne środowiskowe:

```bash
export LEGACY_DATABASE_URL='mysql+pymysql://user:password@legacy-db/cloud_portal'
export LEGACY_ENCRYPTION_KEY_FILE=/root/legacy-cloudportal.key
python scripts/migrate-legacy-cloudportal.py
python scripts/migrate-legacy-cloudportal.py --apply
```

Importer odszyfrowuje stare tokeny tylko w pamięci i natychmiast szyfruje je kluczem Cloudportal-backed. Starych hashy haseł nie kopiuje: importowane konta otrzymują nieznane losowe hasło i `must_change_password`; administrator powinien wydać token resetu. Legacy administrator **nie** dostaje automatycznie roli Administrator — wymaga jawnej flagi `--grant-legacy-admin`.

Projekty, memberships, quotas, resource plans i historyczne jobs/snapshots nie mają obecnie jednoznacznego modelu docelowego. Importer podaje ich liczby w `skipped` i nie tworzy zgadywanego mapowania.


## Multi-host HA drill

`scripts/e2e-ha-failover.py` wykonuje jawny preflight co najmniej dwóch workerów. Sprawdza backend `/health`, aktywność `cloudportal-worker@1`, zgodny fingerprint master key oraz zgodny fingerprint konfiguracji PostgreSQL/Redis bez wypisywania wartości sekretów. Opcjonalne `--exercise-failover` zatrzymuje wyłącznie usługę workera na pierwszym wskazanym hoście, wymaga pozostania drugiego workera online i zawsze próbuje uruchomić usługę ponownie.

SSH działa z `BatchMode=yes`, `StrictHostKeyChecking=yes`, wskazanym plikiem known_hosts oraz stałymi komendami systemd. Konto SSH musi mieć bezhasłowe sudo wyłącznie dla wymaganych odczytów i `systemctl stop/start cloudportal-worker@1.service`.

Przykład preflight:

```bash
python scripts/e2e-ha-failover.py --url https://backend.example.com:8443 \
  --worker-host worker-a.example.com --worker-host worker-b.example.com \
  --ssh-user cloudportal-drill --ssh-key-file /root/ha-drill.key \
  --known-hosts-file /root/ha-known-hosts
```

Dodanie `--exercise-failover` wykonuje kontrolowane wyłączenie jednego workera. Ten test potwierdza redundancję workerów i wspólny control plane; awaria podczas rzeczywistego `terraform apply` nadal wymaga osobnego live acceptance z testowym deploymentem i późniejszej kontroli częściowego state.

## Disaster-recovery restore drill

`scripts/e2e-disaster-restore.py` przywraca backup do **oddzielnej, wcześniej utworzonej bazy PostgreSQL** i po restore sprawdza Alembic, liczbę kont/credentiali oraz próbę odszyfrowania pierwszego credentiala. Skrypt odmawia działania, jeśli target odpowiada produkcyjnej bazie z `backend.env`, a nazwa bazy musi zostać powtórzona przez `--confirm-isolated-database`. Dodatkowo wymagana jest destrukcyjna flaga `--allow-destructive-isolated-restore`.

Przykład:

```bash
sudo python scripts/e2e-disaster-restore.py \
  --backup /var/backups/cloudportal-backed/20260918T120000Z \
  --target-database-url 'postgresql+psycopg://cloudportal:...@dr-db/cloudportal_restore_drill' \
  --confirm-isolated-database cloudportal_restore_drill \
  --allow-destructive-isolated-restore
```

Target jest czyszczony przez `pg_restore --clean --if-exists`; nigdy nie używaj istniejącej bazy z danymi, których potrzebujesz. Przy external KMS/Vault środowisko uruchamiające drill musi mieć dostęp do tego samego KEK, aby test odszyfrowania zakończył się powodzeniem.

## Modular development and parallel branches

The backend and local web console are structured so multiple developers or coding agents can work on separate domain branches with minimal overlap.

- Backend feature descriptors live in `app/modules/feature_*.py` and are auto-discovered. Adding a domain does not require editing `app/main.py`.
- Frontend domains live in `app/web/features/*.js`; shared browser registries live in `app/web/shared/*.js`.
- Feature CSS lives in `app/web/styles/features/*.css`.
- `/ui/manifest.json` discovers installed UI feature assets, so adding a feature does not require editing `index.html`.
- `app/web/app.js` is bootstrap-only and `app/web/core.js` contains only shared UI runtime code.
- CI runs `scripts/check-module-boundaries.py` to prevent domain logic from drifting back into shared hot files.

Create a new module skeleton with:

```bash
python scripts/scaffold-module.py example --ui --backend --label "Example" --order 500
```

See `docs/architecture/modularity.md` and `AGENTS.md` before parallel feature work.

