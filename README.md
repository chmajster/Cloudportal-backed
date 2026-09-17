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

Jeżeli repozytorium zostanie przełączone na prywatne, anonimowe pobranie zwróci 404. Wtedy zapisz w `/root/cloudportal-github.conf` (właściciel root, tryb 600) konfigurację curl z tokenem GitHub mającym wyłącznie dostęp Contents: read do tego repozytorium:

```text
header = "Authorization: Bearer YOUR_GITHUB_READ_TOKEN"
```

Następnie wykonaj jeden one-liner; nie wymaga klonowania repozytorium:

```bash
sudo bash -o pipefail -c 'curl --config /root/cloudportal-github.conf -fsSL -H "Accept: application/vnd.github.raw+json" "https://api.github.com/repos/chmajster/Cloudportal-backed/contents/install.sh?ref=main" | bash -s -- --github-config /root/cloudportal-github.conf --non-interactive --host backend.example.com --port 8443'
```

Można też uruchomić pobrany instalator: `sudo ./install.sh --host backend.example.com --port 8443 --github-token-file /root/github-read.token`. `--ref` pozwala zainstalować wskazaną gałąź/tag/commit. Wersja instalatora i wskazany ref powinny być zgodne.

Instalator generuje certyfikat self-signed, jeżeli nie podano `--cert-file` i `--cert-key`. Zaufaj certyfikatowi `/etc/cloudportal-backed/tls/server.crt` na serwerze PHP lub użyj certyfikatu własnego/publicznego CA. Weryfikacja TLS w portalu jest domyślnie włączona. Port API na loopback `8765` pozostaje niewystawiony przez Nginx; dostęp sieciowy zapewnia port HTTPS `8443`.

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

Istniejąca baza starego portalu nie jest automatycznie importowana ani kasowana. W trybie centralnym jej konta, RBAC i credentiale są nieaktywne; starsze funkcje projektu (m.in. lokalne projekty/IPAM/konsole) nie są udostępniane przez nowy interfejs. Przed przełączeniem istniejącej instalacji przygotuj konta/role i połączenia w backendzie oraz archiwizuj stare dane zgodnie z polityką retencji. Nowy backend nie przejmuje zarządzania dawnymi VM/state automatycznie.

## API i bezpieczeństwo

Kontrakt: `/openapi.json` (OpenAPI 3.x), Swagger `/docs`. Prefix `/api/v1`. Modele odpowiedzi obejmują publiczne pola users, RBAC, tokens, credentials, providers, deployments, jobs/logs, audit i health; nie zawierają hashy ani zaszyfrowanych sekretów. OpenAPI opisuje również wymagane nagłówki idempotencji. Publiczne są logowanie, odświeżenie sesji, realizacja tokena resetu i healthcheck. Endpointy zasobów wymagają Bearer token i granularnego permission. Nie ma endpointu wykonującego shell.

- Hasła użytkowników: 12–256 znaków, Argon2id. Jedynym wyjątkiem jest tymczasowe hasło `admin` świeżej instalacji, które przed udostępnieniem operacji administracyjnych musi zostać zmienione na hasło zgodne z polityką. Domyślny lockout po 5 błędach na 15 minut, dodatkowe limity na tożsamość i IP w Redis. Niedostępny limiter blokuje ruch (503).
- Sesje: opaque access token 15 minut, rotowany refresh token 8 godzin. Ponowne użycie refresh tokena unieważnia całą rodzinę. Logout, reset i zmiana hasła odwołują odpowiednie tokeny. Konta serwisowe nie logują się hasłem.
- Tokeny API: zakres jest przecięciem zapisanych scopes i **aktualnych** permissions konta. Osoba nadająca role/tokeny nie może nadać uprawnień spoza własnego zakresu. Nie można usunąć ostatniego aktywnego administratora.
- Reset hasła: administrator wydaje token ważny 15 minut, wyświetlany raz; użytkownik podaje go formularzem POST `/password-reset` w portalu. Token nie jest umieszczany w URL. Wydanie tokena unieważnia sesje, a realizacja jest atomowa i jednorazowa. Nie ma automatycznej wysyłki e-mail.
- Credentiale: AES-256-GCM, losowy nonce, AAD z ID credentiala. Klucz 32 bajty w `/etc/cloudportal-backed/master.key`, 0600, właściciel `cloudportal`. API zwraca maskę i flagę `configured`. Pole `secrets` pominięte w PUT zachowuje wartość. Podane `secrets` zastępuje komplet sekretów. Zmiana celu wymaga nowych sekretów; cel używany przez aktywne deploymenty nie może zostać podmieniony.
- Testy credentiali: Proxmox, VMware, SSH z obowiązkowym known_hosts, WinRM z weryfikacją certyfikatu, AWS STS, Azure OAuth, OpenStack Keystone. Typ `other` przechowuje zaszyfrowane dane, ale wymaga osobnego adaptera testującego.
- Idempotency-Key UUID: obowiązkowy przy tworzeniu deploymentu, tworzeniu joba i niszczeniu VM, opcjonalny dla innych operacji tworzących. PostgreSQL przechowuje atomowy wynik i HMAC requestu; konflikt payloadu daje 409. Sekrety jednorazowe nie są odtwarzane przy powtórzeniu.
- X-Request-ID UUID łączy request, job, worker, logi i audit. Audit nie zawiera payloadów ani sekretów. Logi wykonawców są redagowane i limitowane.
- Usunięcie użytkownika usuwa dane tożsamości i dostęp, zachowując techniczny ID dla historii deploymentów/audytu.
- Credentiala używanego przez oczekujące lub wykonywane zadanie nie można edytować ani usunąć. Credential SSH/WinRM pozostaje chroniony także jako część workflow niedestroyowanego deploymentu. Zmiana hasła unieważnia również niewykorzystane tokeny resetu.

Domyślne role to Administrator, Infrastructure Administrator, Operator, Viewer, Auditor i Portal Service. Permissions definiuje `app/rbac/service.py`; nazwy ról nie są używane do autoryzacji.

## Provisioning

Pierwszy adapter infrastruktury: Proxmox. `InfrastructureProvider` i registry pozwalają dodawać następne adaptery. Credentiale VMware/AWS/Azure/OpenStack można zapisać i przetestować, ale provisioning tych platform nie jest jeszcze zaimplementowany.

Zatwierdzony szablon `proxmox-vm` klonuje **istniejący szablon QEMU cloud-init** przez bpg/proxmox 0.111.x. Wymaga storage, bridge, template_id, węzła i działającego DHCP. Template musi mieć działający qemu-guest-agent oraz cloud-init. Rozmiar dysku docelowego nie może być mniejszy niż dysk szablonu. Token PVE musi mieć uprawnienia do klonowania, VM, storage i odczytu guest agent. Sekrety providera przekazywane są w środowisku procesu.

`POST /deployments` tworzy deployment i trwały job w jednej transakcji. Dispatcher przenosi zadania z PostgreSQL do Redis/RQ; awaria Redis nie usuwa jobów. Worker atomowo przejmuje job i ponownie sprawdza uprawnienia. Odświeżenie sesji użytkownika nie unieważnia oczekującego joba; wylogowanie, odwołanie rodziny sesji lub utrata uprawnień blokują jego rozpoczęcie. Jeden deployment ma jedno aktywne zadanie; dodatkowo obowiązuje flock workspace oraz blokada stanu Terraform. Workery korzystają ze wspólnego lokalnego katalogu danych na tym samym serwerze. Instalacja wieloserwerowa workerów wymaga współdzielonego state i rozproszonego mechanizmu blokowania. Healthcheck odrębnie monitoruje Redis, heartbeat dispatchera i heartbeat workerów, również gdy kolejka jest pusta.

Przepływ z opcją Ansible: init → plan → apply → adres VM z guest agent → wait_for_connection SSH/WinRM → zatwierdzony playbook → walidacja → successful. Bez Ansible kończy się po apply. Host SSH jest sprawdzany względem known_hosts z credentiala (również wpisy SSH CA); WinRM używa HTTPS i weryfikacji certyfikatu. Szablon musi zawierać przygotowane zaufanie kluczy/certyfikatów i konto odpowiednie dla playbooka. Bootstrap Linux wymaga bezhasłowego sudo tego konta.

API nie przyjmuje HCL, ścieżek plików, dowolnych playbooków, pluginów, zmiennych `ansible_*` ani komend. Dozwolone są tylko playbooki z repozytorium. OpenTofu używa wspólnego executora, jeżeli administrator zainstaluje `tofu` w `/usr/local/bin` lub `/usr/bin`.

Anulowanie kończy grupę procesów, zachowując state. Nie jest automatycznym rollbackiem zasobów. Przerwany worker oznacza zadanie jako failed; ponowienie apply jest decyzją operatora. Nie usuwa się VM po nieudanym Ansible. Logi: `GET /jobs/{id}/logs?after=ID&limit=200`.

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

E2E na prawdziwym PVE: `scripts/e2e-proxmox.py` używa istniejącego credentiala i providera w backendzie; wymaga jawnego `--allow-create-and-destroy` i odpowiednich parametrów. Test tworzy VM, czeka na wynik workflow, pobiera logi i niszczy VM. Nie uruchamia się automatycznie na produkcji.

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
