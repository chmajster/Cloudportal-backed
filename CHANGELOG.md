# CHANGELOG — pamięć regresji i decyzji projektowych Cloudportal-backed

> Ten plik jest **pamięcią techniczną dla agentów i programistów**, a nie klasycznym changelogiem wersji.
> Powstał na podstawie całej historii Pull Requestów dostępnej w repozytorium (PR #1–#265; numer #14 nie występuje).
> Jego celem jest zapobieganie ponownemu wprowadzaniu błędów, które były już diagnozowane i naprawiane.
>
> Ostatnia pełna analiza historii PR: **2026-09-27**.

## Jak agent ma używać tego pliku

1. **Przed zmianą kodu przeczytaj ten plik oraz `AGENTS.md`.**
2. Wyszukaj sekcję odpowiadającą domenie, której dotyczy zadanie: Blueprint/workflow, RBAC, Terraform, Proxmox, inventory, updater, installer, Docker/Kubernetes, AWX/Ansible, UI itd.
3. Sprawdź powiązane PR-y i nie przywracaj rozwiązania, które wcześniej powodowało regresję.
4. Rozróżniaj wpisy **MERGED** od **OPEN/CLOSED-UNMERGED**. Otwarty PR jest ostrzeżeniem i opisem problemu, ale nie jest jeszcze kontraktem `main`.
5. Jeżeli nowa zmiana naprawia kolejny istotny błąd lub zmienia ważny kontrakt, **zaktualizuj ten plik w tym samym PR**.
6. Nie kopiuj historycznych compatibility fallbacków tylko dlatego, że występują w starym PR. Aktualny kontrakt ma pierwszeństwo.
7. Przy refaktorze usuń wszystkie referencje do usuwanych symboli/ścieżek. PR #263 jest przykładem regresji po pozostawieniu martwego odwołania.
8. Każdą zmianę w shared contract sprawdzaj end-to-end: **UI → API → service/domain → worker/job → provider/executor → DB/state → ponowny odczyt → UI**.

---

# 1. Nienaruszalne reguły wynikające z historii regresji

## 1.1. Blueprint i workflow

- Workflow Blueprintu jest **jawny**, a nie domyślny. Po PR #255 Terraform/OpenTofu wymaga jawnego `terraform_apply`, Direct Proxmox jawnego `clone_vm`, a skonfigurowane Ansible/AWX wymagają odpowiednich jawnych kroków.
- Nie przywracaj pseudo-kroków udających wykonanie runtime, jeżeli dana czynność jest tylko częścią kompilacji/deklaracji Terraform. Historycznie dotyczyło to m.in. hostname, IPAM, clone/cloud-init/tagów.
- Direct Proxmox i Terraform są dwiema różnymi ścieżkami wykonania. Nie wolno mieszać ich semantyki, checkpointów ani kroków.
- Retry/resume **nie może powtarzać zakończonej mutacji providera**. Po apply/clone zapisuj trwały checkpoint i wznawiaj od pierwszego niewykonanego kroku.
- Jeżeli wynik mutacji providera jest niepewny po utracie workera/cancel/timeout, stan ma przejść do `reconciliation_required`, a nie ślepego retry.
- Zewnętrzne side-effecty (Ansible, AWX, clone, snapshot, restore, delete) muszą być idempotentne albo posiadać trwały marker/checkpoint.
- Approval workflow i approval Policy Engine to różne mechanizmy. Nie łącz ich w jeden stan.
- Approval Terraform ma być związane z konkretnym planem i jego SHA; zmiana planu unieważnia wcześniejsze zatwierdzenie.
- DAG musi walidować brakujące zależności, self-reference, cykle, rollback targets i zmianę ID kroków.
- Zmiana ID/usunięcie kroku w edytorze musi aktualizować/usuwać wszystkie referencje `depends_on` i rollback.
- Job/deployment ma przechowywać snapshot konfiguracji potrzebnej do wykonania; późniejsza edycja Blueprintu, playbooka lub katalogu nie może zmieniać już zakolejkowanego wykonania.
- Usunięcie/wyłączenie Blueprintu nie może powodować zniknięcia definicji potrzebnej aktywnemu jobowi. Operacje kolidujące z aktywnym provisioningiem powinny być blokowane lub kolejkowane po nim.
- Po refaktorze workflow przeszukaj repo pod kątem starych symboli. PR #263: usunięto `BLUEPRINT_PRECOMPILED_STEPS`, ale worker nadal go używał, co dawało `NameError` opakowany jako ogólny błąd `clone_vm`.

## 1.2. RBAC, tenant, project, scope i Policy Engine

- Autoryzacja backendu jest źródłem prawdy. UI może ukrywać akcję, ale **nie może być jedyną kontrolą**.
- Ten sam model uprawnień musi obowiązywać w kreatorze UI, endpointach create/update/delete/execute, worker reauthorization i operacjach retry/recovery.
- Efektywne role obejmują role globalne oraz aktywne przypisania tenant/project. Nie wolno sprawdzać tylko globalnych ról.
- Cofnięcie membershipu tenant/project ma natychmiast odbierać wynikające z niego uprawnienia.
- Payload API nie może wstrzykiwać principal IDs niewidocznych w wybranym scope. Backend musi ponownie walidować role/użytkowników wybrane w UI.
- Blueprint ACL jest **dodatkowym ograniczeniem**, a nie mechanizmem rozszerzającym resource-scope RBAC.
- `can_manage` i podobne decyzje powinny być obliczane na backendzie i konsumowane przez UI.
- Wbudowany Administrator musi być synchronizowany z nowymi permissionami również na istniejących instalacjach. Regresje tego typu blokowały m.in. LDAP/settings.
- Policy Engine/ABAC nie zastępuje RBAC. Kolejność to: podstawowy scope/RBAC → ACL zasobu → Policy Engine → runtime revalidation.
- Przy rolach zarządzających Blueprintem sprawdzaj kardynalność modelu. Otwarty PR #264 dokumentuje błąd wynikający z przypadkowego one-to-one zamiast many-to-many.

## 1.3. Terraform/OpenTofu, state i mutacje providera

- Terraform state jest trwały, szyfrowany i odtwarzalny na innym workerze. Nie zakładaj wspólnego filesystemu.
- Każde wykonanie ma osobny workspace/process group; provider cache może być współdzielony, ale state/workdir nie.
- VMID Proxmox musi być rezerwowany atomowo i provider-scoped przed plan/apply, z uwzględnieniem PVE, inventory oraz innych aktywnych rezerwacji.
- Po niepewnym przerwaniu apply nie wolno tworzyć nowej VM „od zera” bez reconciliation.
- Dla Terraform-managed VM bezpośrednie mutacje providera muszą być ograniczone, aby nie tworzyć driftu.
- Destroy Proxmox ma wykonać preflight rzeczywistego stanu i w razie potrzeby hard-stop. Nie polegaj na Guest Agent dla krytycznego usunięcia.
- Legacy zasoby pozostające w Terraform state po zmianie mechanizmu provisioningowego trzeba jawnie usuwać/migrować. PR #216: stary snippet resource pozostał w state po przejściu na natywny NoCloud.
- Cache/init optymalizuj bez naruszania izolacji wykonania.

## 1.4. Proxmox i źródło prawdy

- Przy operacjach asynchronicznych Proxmox UPID jest źródłem statusu operacji providera.
- UI nie może wymyślać procentu. `null` oznacza „brak pomiaru”, a nie 0%. Ta sama zasada dotyczy CPU/RAM/progress.
- Dla Terraform clone postęp powinien korelować VMID z właściwym `qmclone` taskiem; jeżeli PVE nie podaje procentu, pokaż progress indeterminate.
- Snapshot ma mieć capability preflight w provider/service, nie tylko w UI. Błąd „snapshot feature is not available” powinien być wykryty przed wysłaniem taska.
- Operacje power/delete/clone/restore muszą zachowywać właściwą semantykę dla VM external vs Terraform-managed.
- Restore może używać wyłącznie backupu wcześniej odkrytego/zweryfikowanego przez backend.
- Token bootstrap Proxmox nie może utrwalać hasła użytego do utworzenia tokenu.
- Duplikat nazwy tokenu ma być weryfikowany, nie zgadywany na podstawie ogólnego HTTP 400.

## 1.5. Inventory, missing VM i reconciliation

- Inventory nie jest jedynym źródłem prawdy o istnieniu VM; przy refresh należy porównać z providerem.
- Brak VM w Proxmox powinien prowadzić do jawnego stanu `missing`, nie do ogólnego błędu.
- Purge stale inventory musi usunąć lub oznaczyć wszystkie dane, z których późniejszy repair mógłby odtworzyć wpis.
- PR #212/#215/#220: usunięta brakująca VM wracała, bo Terraform state/deployment nadal pozwalał mechanizmowi repair ją rekonstruować. Potrzebny jest trwały semantyczny marker/tombstone/stan wykluczający rekonstrukcję.
- Refresh i repair to różne operacje. Nie zamieniaj ich nazw ani zachowania w UI/testach.
- Po udanym apply synchronizacja ManagedResource/ManagedVM oraz deployment status musi być atomowa lub spójna transakcyjnie.
- UI po równoległym fetchu deployment/job/inventory musi rozwiązywać race condition i nie pozostawiać stale `W toku`.

## 1.6. QEMU Guest Agent, Cloud-init i dostęp do gościa

- Nie buduj cyklu „potrzebuję DHCP/SSH, żeby skonfigurować Cloud-init, który dopiero ma dać DHCP/SSH”. PR #149/#155/#156 usunęły ten deadlock.
- Preferuj natywny first-boot Cloud-init/NoCloud przez Proxmox API, bez zależności od SSH do noda PVE.
- `wait_for_agent` jest niezależnym krokiem i nie może być przypadkowo uzależniony od obecności Ansible.
- Ping QEMU Guest Agent w Proxmox używa właściwej metody API. PR #198 naprawiał błędne użycie GET.
- Poprawny HTTP 2xx z `/agent/ping` może mieć `data: null` i nadal oznacza gotowego agenta.
- HTTP 500 z ping podczas startu może oznaczać „jeszcze niegotowy”; 401/403/404/transport/TLS muszą pozostać rozróżnialne diagnostycznie.
- Retry transportu providera i polling gotowości agenta to różne mechanizmy.
- Credential użytkownika VM nie może wypuszczać prywatnego klucza/hasła do tfvars, Terraform state ani logów.
- Tryb istniejącego konta z template nie może niejawnie zmieniać hasła, authorized_keys ani sudo.
- Instalacja QGA i oczekiwanie na QGA to dwa niezależne przełączniki.

## 1.7. AWX i Ansible

- AWX credential/secrets pozostają w magazynie credentials; nie trafiają do cloud-init.
- Rejestracja AWX musi ponownie autoryzować credential w runtime scope.
- Runtime APMID/ENV musi być odzwierciedlone w grupach/zmiennych AWX, nie tylko wartości zapisane w Blueprint definition.
- Mapowanie CloudPortal Tenant/Project do AWX Organization/Project ma być centralne, nie arbitralne per Blueprint.
- Onboarding AWX powinien być idempotentny; cleanup przy destroy jest best-effort i nie może blokować usunięcia VM.
- Ręczna akceptacja onboardingu nie może fałszować wcześniejszych kroków workflow.
- Ansible runs są uporządkowaną listą. Retry wznawia od pierwszego niezakończonego runbooka.
- Zakolejkowany custom playbook ma snapshot wersji; późniejsza edycja katalogu nie zmienia joba.
- Custom playbooki muszą blokować controller-side execution/exfiltration: lookup/query, local_action, delegate_to, script, fetch, synchronize, niebezpieczne include/import oraz lokalne src.
- Wyłączenie playbooka musi być respektowane także przez Day-2.

## 1.8. Joby, dispatcher, cancel, retry i bulk

- `queued` w DB i „przekazany do RQ” to różne stany. Używaj `dispatched_at`/równoważnego markera.
- Force-dispatch nie może duplikować joba już w RQ ani omijać approval/provider backoff/cancel.
- Cancel aktywnego joba ma być widoczny jako `cancelling`; jeżeli worker zniknął, dispatcher musi po grace period domknąć orphan.
- Cooperative cancel musi zabijać całą grupę procesu Terraform/Ansible.
- Retry nie może resetować checkpointów, które chronią przed ponowną mutacją.
- Bulk actions muszą izolować błędy per resource. Kontrolowany błąd jednej VM nie może wywracać całego batcha.
- Idempotency key powinien być stabilny per VM/per batch attempt.
- Po częściowym sukcesie UI usuwa z zaznaczenia tylko zasoby rzeczywiście przyjęte/wykonane.
- Fallback po błędzie bulk nie może oznaczać elementów jako obsłużone, jeżeli request zwrócił 500.

## 1.9. Quota, reservation i recovery

- Reservation quota ma trwały lifecycle i musi być reconciled po crash/timeout.
- Nowy destroy/recovery nie może być blokowany wiecznie przez osieroconą reservation starego joba.
- Forced destroy może przejąć/supersede stale reservation tylko w kontrolowany, audytowalny sposób.
- Recovery po utracie workera musi uwzględniać persisted state, inventory i quota; nie wolno opierać decyzji wyłącznie na statusie joba.

## 1.10. Updater

- Stan zapisany na dysku nie może być jedynym źródłem prawdy o aktywności. Po restarcie `running` bez żywego procesu jest stale state, nie aktywna aktualizacja.
- Polling/check_remote nie może nadpisywać aktywnego `running` stanem `update_available`.
- Updater ma osobny trwały status/timeline, a UI musi synchronizować stan po zakończeniu i nie pozostawiać „Aktualizacja trwa”.
- Kandydat aktualizacji ma być walidowany **przed** mutacją produkcji: build/import, migracje, healthcheck oraz — dla DB zmian — migracja na klonie aktualnej bazy.
- Docker runtime preflight nie może być pomijany.
- Docker updater używa Unix socket, nie niepotrzebnego hostowego TCP portu.
- GitHub 429/5xx/network error wymaga retry/backoff.
- Legacy/non-Git marker wersji nie może być wysyłany jako commit SHA do GitHub compare.
- Po update commit marker ma być prawdziwym Git SHA.

## 1.11. Installer: systemd, Docker, Kubernetes

- Instalator ma być idempotentny, zachowywać dane i sekrety przy reinstall/update.
- Lock instalatora nie może „wyciekać” ani blokować kolejnego uruchomienia bez żywego właściciela.
- Przejęcie konkurencyjnej instalacji musi być jawne i bezpieczne.
- Operacje Docker wykonywane w automatyzacji nie mogą wymagać TTY; używaj `docker compose run -T` tam, gdzie to wymagane.
- Startup Docker jest dependency-aware: PostgreSQL/Redis health przed API/worker/dispatcher.
- Healthcheck aplikacji nie może tworzyć cyklicznej zależności od workerów/dispatchera.
- Backup/restore wykonywany jako użytkownik usługi musi mieć dostęp do katalogu docelowego; nie twórz root-only katalogu 0700, do którego `pg_dump` jako cloudportal nie może pisać.
- TLS cert musi być regenerowany/walidowany po zmianie hosta i przy expiry.
- Recovery administratora nie może przekazywać hasła w argv ani logach.
- Deinstalacja domyślnie zachowuje bazę/dane; purge jest osobną, jawną operacją.
- Kubernetes musi mieć trwałe PVC dla danych i spójny bootstrap/migration/rollout order.
- Każdy nowy tryb instalacji musi mieć status, uninstall/purge semantics i osobne testy platformowe.
- Docker candidate build używa tych samych nazw obrazów Compose co aktywny release. Rollback po nieudanym kandydacie musi odbudować obrazy z katalogu poprzedniego release przed `compose up`; samo uruchomienie starego `docker-compose.yml` może wystartować na obrazie kandydata.
- Przy błędzie usługi `migrate` instalator ma pokazać jej log. Nie wolno raportować „poprzedni release przywrócony”, jeżeli rollback sam zakończył się błędem.

## 1.12. Migracje i baza

- **Nigdy nie modyfikuj już scalonej migracji.**
- Każda zmiana schematu dostaje nową rewizję Alembic.
- Migracje muszą być testowane zarówno na pustej bazie, jak i upgrade z realnego poprzedniego head.
- Przy lightweight `sa.table()` określ typy kolumn JSON/JSONB; brak typu może przekazać surowy dict do psycopg i zakończyć migrację błędem.
- Po merge równoległych gałęzi sprawdź, czy Alembic ma jeden poprawny head albo jawny merge revision.
- Regresja 2026-09-27: `0c4e71a9d2f8` i `ab91c4e7d260` równolegle wskazywały na `f9d6c2a81e44`, przez co Docker `migrate` kończył `alembic upgrade head` kodem 255. Naprawa wymaga nowej merge revision, nigdy edycji już scalonych migracji, oraz testu repozytorium wymuszającego dokładnie jeden head.
- Nie zakładaj historycznego stałego head w testach migracji.

## 1.13. noVNC i WebSocket

- Browser nie powinien otrzymywać PVE API tokenu, infrastrukturalnego URL ani ticketu PVE.
- Konsola działa przez krótkotrwałą capability/session po backendzie.
- noVNC assets powinny być serwowane lokalnie przez CloudPortal; nie uzależniaj klienta od wersji/patchy assetów na konkretnym PVE.
- ES modules wymagają poprawnego MIME przy `X-Content-Type-Options: nosniff`.
- Nginx proxy WebSocket musi przekazywać `Upgrade`, `Connection` i używać HTTP/1.1.
- Static noVNC asset fetch i autoryzacja sesji WebSocket to różne ścieżki; nie doklejaj API tokenu do każdego assetu bez potrzeby.

## 1.14. UI, routing i stan

- Widoki długotrwałe/formularze powinny mieć stabilne URL-e, aby można było wrócić, odświeżyć i linkować.
- Po delete/nawigacyjnej mutacji nie pozostawiaj UI na trasie zasobu, który już nie istnieje.
- Nie używaj `Number(null)`/podobnej konwersji do progress — daje fałszywe 0.
- Równoległe fetch-e mogą zwrócić dane z różnych momentów. Po zakończonym jobie nie pozwól, aby starszy snapshot inventory/deployment nadpisał stan finalny.
- Zachowuj scroll logów podczas pollingu.
- Desktop i mobile mogą wymagać innych zachowań menu/layoutu.
- Każda większa funkcja tabel/kart musi mieścić się w granicach modułów; po rozbudowie wydziel domenowy moduł zamiast przekraczać limit pliku.
- Nie dubluj identyfikatorów/nazw pól HTML; historycznie powodowało to kolizję nazwy deploymentu/hostname.
- Pola zależne od runtime wyboru (APMID/ENV) nie mogą być jednocześnie wymagane/ustawiane statycznie w sposób sprzeczny.
- UI ma rozróżniać stan VM (`running/stopped`) od stanu workflow (`running/queued/failed`).

## 1.15. Sekrety i bezpieczeństwo

- Nigdy nie loguj haseł, tokenów, private keys, command lines zawierających sekrety ani secret env.
- Password używany do bootstrapu tokenu/provider session ma być jednorazowy i nieutrwalany, jeżeli nie jest docelowym credentialem.
- Terraform/provider credentials przekazuj przez chronione env/credential resolution, nie przez publiczny payload.
- Browser dostaje tylko minimalne capability potrzebne do danej operacji.
- Backup/restore musi weryfikować fingerprint master key i checksum dumpa.
- Webhook destination ma allowlist/HTTPS i podpis HMAC.
- Custom automation content wymaga walidacji pod kątem wykonania na controllerze.

## 1.16. CI i modularność

Przed merge obowiązkowo:
- `python scripts/check-module-boundaries.py`
- `find app/web -type f -name '*.js' -print0 | xargs -0 -n1 node --check`
- `pytest -q --tb=short`
- odpowiednie testy installera/Docker/Kubernetes/Terraform/container/migrations dla zmienianej domeny.

Dodatkowo:
- Nie uznawaj czerwonego `main` za normalny stan. Jeżeli baseline jest czerwony, zidentyfikuj dokładnie istniejące failure i nie dokładaj nowych.
- Testy kontraktowe muszą być aktualizowane razem ze świadomą zmianą kontraktu, nie dopiero po merge.
- Nie przekraczaj limitów modułów UI; wydzielaj funkcje do domenowych plików.
- Shared hot spots zmieniaj tylko przy rzeczywiście cross-cutting kontrakcie.
- Po refaktorze wykonaj repo-wide search dla usuniętych nazw/symboli.
- Testy mockowane nie zastępują live E2E dla Proxmox/AWX/providerów; w PR jawnie odnotuj, czego nie zweryfikowano na realnej infrastrukturze.

---

# 2. Najczęściej powtarzające się klasy regresji

| Klasa | Co historycznie psuło się | Reguła zapobiegawcza | Przykładowe PR |
|---|---|---|---|
| Workflow contract drift | UI/API/worker rozumiały inny zestaw kroków | jedna kanoniczna reprezentacja + test end-to-end | #94–#108, #185, #194, #254–#255, #263 |
| Retry po side-effect | ponowne clone/apply/Ansible/AWX | trwałe checkpointy i reconciliation | #131–#132, #194, #254 |
| Scoped RBAC drift | global roles działały, project/tenant nie | ten sam resolver w API/UI/worker | #118–#122, #147, #178, #200, #258 |
| Stale inventory | missing VM wracała po purge | tombstone/terminal lifecycle + repair guard | #188, #212, #215, #220 |
| Fałszywy progress | null stawał się 0%, etap był zbyt ogólny | null = unknown; provider task jako źródło prawdy | #160, #222, #231, #249, #265 |
| QGA false negative | zła metoda API / data:null / błędna klasyfikacja 500 | provider-specific ping semantics + retry | #186, #193, #198 |
| Installer race/lock | stale lock, TTY, readiness | idempotent preflight, live owner, no-TTY | #25, #27, #30, #90, #154, #171 |
| Updater stale state | running mimo braku procesu lub odwrotnie | żywy proces/lock jako źródło prawdy | #57, #72, #76 |
| Migration breakage | dict do JSON bez bind type, multi-head | typed tables + upgrade CI | #123, #245 |
| noVNC proxy | MIME, dynamic import, WebSocket 404 | lokalne assets + poprawny WS proxy | #110, #129, #158, #161 |
| Bulk partial failure | jedna VM wywracała batch / błędny retry UI | per-resource result + stable idempotency | #133–#138, #217, #238, #244 |
| Terraform destroy | QGA/shutdown blokował usunięcie | provider hard-stop przed destroy | #218, #224 |
| Module boundary | feature rozrastał wspólny/duży plik | wydzielanie domenowych modułów | #20, #26, #167, #183, #241 |
| Credential lifecycle | sekret trafiał do niewłaściwej warstwy | referencja ID + runtime resolution | #91, #125, #128, #149, #151, #168 |
| Async UI race | zakończony job nadal „W toku” | korelacja timestamp/state + final refresh | #83, #85, #192, #210, #222 |

---

# 3. Pełny indeks PR i lekcje historyczne

Poniższy indeks obejmuje wszystkie PR-y zwrócone przez GitHub w chwili analizy. Jest indeksem do szybkiego wyszukania kontekstu. Dla szczegółów zawsze otwórz wskazany PR.

Legenda:
- **MERGED** — zmiana weszła do historii `main`.
- **OPEN** — aktywny PR; traktuj jako nierozwiązany/pending, dopóki nie zostanie scalony.
- **CLOSED-UNMERGED** — zamknięty bez merge; nie traktuj jego implementacji jako obowiązującego kontraktu.

- **#1 [MERGED] feat: central backend for Cloud Portal with users, RBAC and provisioning** — Powiązany frontend/proxy PHP: Cloudportal-backed jest centralnym backendem, który przechowuje konta, role, permissions, hashowane tokeny, zaszyfrowane credentiale i audit. PHP obsługuje UI/sesję/CSRF i przekazuje operacje do HTTPS API z tokenem użytkownika. Wyłącznie backend łączy się z infrastrukturą oraz wykonuje Ter
- **#2 [MERGED] Expand Cloudportal-backed into full infrastructure portal** — Ten PR jest głównym roboczym checkpointem. Przy kolejnym podejściu najpierw odczytaj tę sekcję i aktualny CI. Nie wykonuj ponownego audytu funkcji oznaczonych jako zakończone. - [x] Backend CI run 154 - [x] head 8afa27d5fa8beb42ebbfded9c263a75c115563f8 - [x] API / pytest / integracja PHP
- **#3 [CLOSED-UNMERGED] feat: rozbuduj edytor credentiali** — Rozbudowa widoku Credentiale i formularza ze zrzutu.
- **#4 [MERGED] feat: generuj token Proxmox z loginu i hasła** — dodaje jednorazowy bootstrap credentiala Proxmox: endpoint + login + hasło + nazwa tokenu backend loguje się do PVE, tworzy API token i zapisuje tylko token id + token secret hasło nie jest utrwalane w bazie ani zwracane do UI UI ma nową metodę „Login + hasło → wygeneruj token” dodane testy endpointu i potwierdzenie, ż
- **#5 [MERGED] feat: otwieraj prawdziwą konsolę noVNC dla VM** — konsola noVNC nie łączy przeglądarki bezpośrednio z Proxmox chroniony endpoint vms.console tworzy krótkotrwałą sesję capability w Redis PVE ticket i port pozostają wyłącznie po stronie backendu backend proxy udostępnia moduły/assets noVNC z PVE przez krótkotrwały session ID backend zestawia i tuneluje WebSocket do vncw
- **#6 [MERGED] feat: uzupełnij akcje power i usuwania VM w UI** — dodaje w panelu brakujące akcje Proxmox: reset, suspend, resume zachowuje start, shutdown, reboot, stop akcje destrukcyjne reset i stop są oznaczone jako niebezpieczne usuwanie VM ma teraz opcje purge i destroy unreferenced disks, które backend obsługiwał już wcześniej dodany test statyczny panelu
- **#7 [MERGED] feat: dodaj restore backupu Proxmox do panelu** — lista backupów VM jest prezentowana jako tabela zamiast surowego JSON operator z backups.restore może wybrać konkretny istniejący volid formularz restore przyjmuje nowy VMID, opcjonalny storage docelowy i flagę unique wywołuje istniejący bezpieczny endpoint restore, który ponownie sprawdza obecność wskazanego backupu w
- **#8 [MERGED] feat: dodaj natywne discovery AWS Azure OpenStack i VMware** — rejestruje natywne adaptery read-only dla AWS, Azure, OpenStack i VMware każdy adapter obsługuje test credentiala przez wspólny interfejs providera discovery zwraca znormalizowane dane i przechodzi przez istniejącą allowlistę pól API AWS: instances, AMI, subnets, EBS volumes, availability zones, VPC Azure: VM, images, 
- **#9 [MERGED] feat: dodaj migrację ze starego Cloud Portal** — importer legacy MySQL → Cloudportal-backed domyślnie dry-run; zapis wymaga jawnego --apply migruje użytkowników i role bez kopiowania niekompatybilnych hashy haseł migracje kont wymagają resetu hasła; legacy admin nie dostaje automatycznie pełnych praw odszyfrowuje stare Crypto.php SecretBox i ponownie szyfruje tokeny 
- **#10 [MERGED] feat: dodaj envelope encryption przez AWS KMS i Vault Transit** — zachowuje istniejący local AES-GCM jako domyślny i kompatybilny format opcjonalny aws-kms: per-record DEK, KMS Encrypt/Decrypt + EncryptionContext z AAD opcjonalny vault-transit: per-record DEK owijany przez Transit Engine plaintext credential/TF state nie jest wysyłany do KMS/Vault, tylko losowy DEK nowy envelope ma w
- **#11 [MERGED] ci: dodaj ręczny workflow live acceptance dla infrastruktury** — osobny workflow dispatch, nigdy nie uruchamia się na push/PR GitHub Environment live-infrastructure może wymagać ręcznej akceptacji scenariusze: Proxmox create → workflow → destroy Proxmox console-ticket → backup → restore → cleanup Terraform provider create → ManagedResource → destroy dla AWS/Azure/OpenStack/VMware to
- **#12 [MERGED] test: dodaj multi-host HA failover drill** — preflight wymaga co najmniej 2 hostów workerów sprawdza /health i co najmniej dwa live workery przez SSH sprawdza aktywną usługę workera porównuje SHA-256 master key bez ujawniania klucza porównuje hash konfiguracji DB/Redis/master-key bez wypisywania DSN/hasła opcjonalny --exercise-failover zatrzymuje worker na jednym
- **#13 [MERGED] test: dodaj izolowany disaster-recovery restore drill** — osobny skrypt odtwarzający backup do wskazanej izolowanej bazy PostgreSQL ponownie sprawdza SHA-256 dumpa i fingerprint master key wymaga dokładnego powtórzenia nazwy target DB oraz jawnej destrukcyjnej flagi porównuje target z produkcyjnym CP DATABASE URL i odmawia restore na produkcję po restore sprawdza wersję Alemb
- **#15 [MERGED] feat: dodaj opcjonalny instalator GUI przez dialog** — dodaje tryb --gui oraz równoważny alias -gui domyślna instalacja CLI pozostaje bez zmian --non-interactive pozostaje trybem unattended i nie może być łączony z GUI GUI działa również z curl sudo bash, bo dialog czyta z /dev/tty jeśli dialog nie jest zainstalowany, instalator doinstalowuje go dla wspieranego systemu for
- **#16 [MERGED] fix: napraw TLS healthcheck instalatora po zmianie hosta** — Reinstalacja zachowywała istniejący /etc/cloudportal-backed/tls/server.crt nawet po zmianie --host albo wygaśnięciu certyfikatu. Lokalny API był zdrowy, ale HTTPS healthcheck przez Nginx kończył się błędem weryfikacji i bootstrap token nie był generowany.
- **#17 [MERGED] Fix Proxmox connectivity and complete user-friendly portal UI** — Follow-up to merged PR 2. Zmiany Proxmox: - komunikaty błędów w modalu są trwałe i czytelne; toast błędu ma wysoki kontrast i dłuższy czas wyświetlania, - Proxmox akceptuje jawne http:// i
- **#18 [MERGED] feat: guided Proxmox Blueprint Designer** — Rozbudowa Blueprintów Proxmox tak, aby Blueprint był kompletnym presetem VM i po zapisaniu nie wymagał ponownego wybierania obrazu, hostname, tagów ani cloud-init. - [x] guided Blueprint Designer w lokalnym panelu backendu - [x] wybór providera Proxmox - [x] dynamiczny wybór node
- **#19 [MERGED] feat(ui): global search across infrastructure** — globalne wyszukiwanie w topbarze skróty Ctrl/Cmd+K oraz / indeks respektujący RBAC wyszukiwanie po VM, deploymentach, jobach, providerach, Blueprintach, użytkownikach, credentialach i hostname nawigacja klawiaturą ArrowUp/ArrowDown/Enter bezpieczny cache indeksu na 30 sekund kliknięcie wyniku VM otwiera istniejący VM M
- **#20 [MERGED] refactor: modular architecture for parallel feature development** — Przebudowa architektury pod równoległą pracę wielu agentów/programistów na osobnych branchach. Najważniejsze zmiany: - frontend rozdzielony z monolitycznego app/web/app.js na core + izolowane feature modules, - każdy frontend feature ma własny plik app/web/features/ .js i rejestruje własne route przez registerView,
- **#21 [MERGED] feat(ui): pełna strona szczegółów VM** — dedykowana strona szczegółów VM zamiast głównego modala VM Manager breadcrumbs i pełny nagłówek zasobu zakładki: Przegląd, Hardware, Snapshoty, Backupy, Historia karty statusu CPU/RAM/dysk/uptime i trybu zarządzania paski wykorzystania zasobów snapshoty z rollback/delete backupy z wyborem storage i restore historia aud
- **#22 [MERGED] feat(ui): wizualny Blueprint DAG Designer** — rozszerza istniejący guided Blueprint Designer z PR 18 wizualna mapa DAG dla zaawansowanego workflow grupowanie kroków według poziomu zależności live aktualizacja podczas edycji wykrywanie brakujących zależności wykrywanie duplikatów ID oznaczanie niepoprawnych kroków bez czekania na submit przyciski zmiany kolejności 
- **#23 [MERGED] feat(ui): zaawansowane tabele** — sortowanie kolumn rosnąco/malejąco wyszukiwanie pełnotekstowe paginacja 10/25/50/100/wszystkie wybór widocznych kolumn tryb gęstości normalny/kompaktowy zapamiętywanie ustawień per widok/tabela w localStorage zachowanie sticky headers i horizontal scroll aria-sort i dostępne kontrolki klawiaturowe responsywne toolbar/p
- **#24 [MERGED] feat: auto-update with independent updater service** — Dodaje kompletny mechanizm auto-update wykonywany przez niezależny serwis systemd, tak aby podgląd aktualizacji działał także podczas restartu API/workerów. Zakres: - osobny cloudportal-updater.service na loopback 127.0.0.1:8766, - ekran Aktualizacje z procentem, aktualnym etapem, timeline zdarzeń, logiem live, wersją 
- **#25 [MERGED] fix(installer): take over competing installation automatically** — Zmiana zachowania instalatora przy aktywnym /run/cloudportal-install.lock. Nowe zachowanie: - domyślnie ponowne uruchomienie instalatora nie kończy się już tylko komunikatem Another installation is running., - instalator zatrzymuje usługi aplikacji Cloudportal: updater, API, dispatcher i aktywne workery,
- **#26 [MERGED] fix(ui): restore core module boundary** — Usuwa z app/web/core.js zduplikowane helpery Blueprint/Hostname, które są już własnością app/web/features/blueprints.js. Zmniejsza core z 1223 do 1145 linii i przywraca przechodzenie scripts/check-module-boundaries.py bez zmiany zachowania UI.
- **#27 [MERGED] fix(installer): non-leaking lock and force uninstall** — Naprawa przypadku, w którym instalator zwracał: Another installation is running mimo że lslocks grep cloudportal-install.lock nic nie pokazywał. Przyczyna:
- **#28 [MERGED] feat(ui): add Tools center with Auto-update** — Dodaje osobną sekcję Narzędzia do głównego panelu Cloudportal. Zakres: - nowy przycisk Narzędzia w bocznej nawigacji, - nowy ekran centrum narzędzi,
- **#29 [MERGED] Proxmox: test połączenia przed zapisem i polskie diagnostyki** — dodano przycisk Sprawdź połączenie w formularzu danych dostępowych Proxmox, test działa przed zapisem i nie zapisuje hasła/tokena, wynik pokazuje endpoint, protokół, port, użytkownika, metodę uwierzytelnienia, wersję Proxmox VE, stan TLS i czas odpowiedzi, błędy testu są prezentowane po polsku wraz z checklistą diagnos
- **#30 [MERGED] feat(installer): staged terminal UI, preflight and status mode** — Przebudowa interfejsu install.sh pod czytelną obsługę operatorską. Zmiany: - instalacja jest pokazana w etapach [1/7]–[7/7], - spójne statusy [ OK ], [INFO], [WARN], [FAIL],
- **#31 [MERGED] fix(installer): align platform smoke tests with staged UI** — Po merge PR 30 aktualizuje scripts/test-installer-platforms.sh do nowego formatu wyjścia [ OK ] / [INFO] i nowego --help. Nie zmienia działania instalatora; naprawia test CI, który nadal oczekiwał starego tekstu Supported platform: (...).
- **#32 [MERGED] merge: publish modular auto-update and Tools to main** — Publikuje na main zmiany, które wcześniej zostały scalone do refactor/modular-architecture: - Auto-update i niezależny updater service, - commit-based versioning, - favicon,
- **#33 [MERGED] ui: modernize Cloudportal admin dashboard** — - przebudowany globalny shell Cloudportal z nowym spacingiem, kartami, radiusami i zielonym motywem, - sidebar 276 px z lokalnymi ikonami SVG i nowym active state, - profil zalogowanego użytkownika na dole sidebaru oparty o state.identity, - zmodernizowany topbar i istniejący Global Search bez tworzenia drugiej wyszuki
- **#34 [MERGED] fix: detect duplicate Proxmox API token names** — - rozpoznawanie odpowiedzi Proxmox oznaczającej istniejący już token API, - zamiast ogólnego błędu HTTP 400 Cloudportal zwraca konflikt 409 z kodem proxmox token duplicate, - backend zwraca suggested token name, np. cloudportal-2, - formularz automatycznie wpisuje propozycję do pola „Nazwa nowego tokenu”, ustawia fokus
- **#35 [MERGED] fix(ui): polish navigation, responsive shell and dashboard affordances** — Przegląd i korekta niedociągnięć po modernizacji UI. naprawa uszkodzonych tokenów CSS --radius-sm / --success, dodanie poprawnych tokenów sukcesu dla jasnego i ciemnego motywu, grupowanie bocznej nawigacji: Dostęp / Infrastruktura / Operacje / System, aria-current i lepsza semantyka aktywnego widoku, zachowanie nazwy b
- **#36 [MERGED] UI: graficzny kreator wyboru szablonu Proxmox przy wdrożeniu** — usuwa konieczność ręcznego wpisywania VMID szablonu w kreatorze nowego wdrożenia Proxmox, pobiera szablony QEMU bezpośrednio z /providers/{id}/templates, pokazuje szablony jako klikalne kafelki z nazwą, VMID, węzłem i dostępnymi parametrami, dodaje wyszukiwarkę oraz filtr po węźle, po kliknięciu automatycznie ustawia t
- **#37 [MERGED] feat: add Terraform/OpenTofu template wizard for Proxmox** — Dodaje pełny kreator wielokrotnie używalnych szablonów Terraform / OpenTofu dla Proxmox.
- **#38 [MERGED] fix: verify Proxmox token duplicates after HTTP 400** — - HTTP 400 z Proxmox nie jest już automatycznie interpretowane jako duplikat, - po HTTP 400 backend pobiera rzeczywistą listę tokenów użytkownika z Proxmox, - jeśli nazwa istnieje: zwraca 409 proxxmox token duplicate z propozycją kolejnej nazwy, - jeśli nazwy nie ma: zwraca osobny błąd proxmox token create rejected z i
- **#39 [MERGED] fix: load Proxmox storage and network after template selection** — Naprawia problem w kreatorze wdrożenia Proxmox, w którym pola Storage i Sieć / bridge pozostawały w stanie „Wybierz docelowy węzeł”. Zmiany: - wybór bazowego template przekazuje jego node do kreatora, - jeśli node template istnieje na liście docelowych węzłów i użytkownik nie wybrał jeszcze innego, jest ustawiany autom
- **#40 [MERGED] feat: add LDAP authentication with JIT user provisioning** — Dodaje obsługę LDAP jako dodatkowego źródła uwierzytelniania w Cloudportal-backed.
- **#41 [MERGED] feat: simplify hostname pattern setup in Proxmox template wizard** — Upraszcza sekcję hostname w kreatorze szablonu Proxmox. Zmiany: - usuwa ręczne pole „Nazwa” szablonu z kreatora Proxmox; nazwa wyświetlana jest generowana automatycznie ze slugu, - usuwa „Nazwa wzorca” dla hostname,
- **#42 [MERGED] UI: podgląd kodu playbooków Ansible** — dodaje podgląd YAML playbooka Ansible bezpośrednio z kreatora wdrożenia, dodaje ten sam podgląd do jednorazowego uruchamiania Ansible, pokazuje główny playbook oraz powiązane pliki wait i validate, jeśli istnieją, pliki są przełączane zakładkami i oznaczone rolą, widoczne są nazwa, wersja i transport SSH/WinRM, podgląd
- **#43 [MERGED] feat(ssh): generate and install SSH keys from credential wizard** — SSH / Linux domyślnie pokazuje logowanie hasłem, nadal można wkleić istniejący klucz prywatny, dodano tryb Hasło → wygeneruj i wgraj klucz , Cloudportal pobiera publiczny klucz hosta SSH i pokazuje jego odcisk SHA256 do potwierdzenia, po potwierdzeniu generowany jest klucz Ed25519, Cloudportal loguje się jednorazowo ha
- **#44 [MERGED] Ansible: biblioteka playbooków do zarządzania zdalnymi VM** — Dodaje rozbudowaną bibliotekę zatwierdzonych playbooków Ansible uruchamianych na zdalnych VM przez istniejący mechanizm SSH/WinRM.
- **#45 [MERGED] fix(ui): move Operations directly below Dashboard** — Sekcja Operacje w bocznym menu jest teraz renderowana bezpośrednio pod Dashboardem. Kolejność grup: 1. Dashboard 2. Operacje
- **#46 [MERGED] feat: add Settings panel to Cloudportal** — Dodaje osobny widok Ustawienia do panelu Cloudportal. Zakres: - nowa pozycja „Ustawienia” w nawigacji, chroniona przez settings.read, - sekcja Wygląd z wyborem jasnego/ciemnego motywu,
- **#47 [MERGED] fix: keep Administrator synchronized with all permissions** — Na istniejących instalacjach rola Administrator mogła nie dostać nowych uprawnień dodanych w kolejnych wersjach, np.: settings.read settings.update updates.read updates.execute updates.update Powód: seed(db) był pomijany dla już zbootstrapowanej instalacji, istniejąca rola Administrator dostawała tylko permissions, któ
- **#48 [MERGED] feat: allow disabling templates and Ansible playbooks** — Dodaje trwałe włączanie/wyłączanie elementów Katalogu IaC bez usuwania ich z repozytorium.
- **#49 [MERGED] fix: use hostname generator as authoritative VM name** — Dla szablonów z przypisanym generatorem hostname użytkownik nadal mógł widzieć/podawać ręczną nazwę deploymentu lub VM. Dodatkowo formularz Nowe wdrożenie miał kolizję dwóch pól HTML o nazwie name.
- **#50 [MERGED] SSH credentials: usuń obowiązkowe known hosts** — usuwa pole known hosts z formularza credentiali SSH dla logowania hasłem i istniejącym kluczem prywatnym, backend nie wymaga już known hosts przy zapisie credentiala SSH, test połączenia SSH działa również bez known hosts, Ansible działa bez known hosts; wtedy wyłącza host key checking dla tego credentiala, istniejące 
- **#51 [MERGED] Fix backup: znajdowanie runuser podczas pre-update** — Backup uruchamiany przed aktualizacją ustawia własny ograniczony PATH bez /usr/sbin. Na systemach, gdzie runuser znajduje się w /usr/sbin/runuser, Python kończył się błędem FileNotFoundError.
- **#52 [MERGED] Fix backup: pg dump nie może zapisać database.dump** — Po poprzedniej poprawce pg dump jest poprawnie uruchamiany jako użytkownik systemowy cloudportal przez runuser, ale katalog backupu jest tworzony przez roota z prawami 0700. W efekcie proces cloudportal nie może otworzyć: /var/backups/cloudportal-backed/<timestamp /database.dump i backup kończy się Permission denied.
- **#53 [MERGED] Fix RBAC: Administrator zawsze ma dostęp do konfiguracji LDAP** — W istniejącej instalacji rola Administrator mogła pozostać bez settings.read / settings.update, np. gdy aktualizacja zakończyła się przed końcowym wywołaniem bootstrapu. Wtedy administrator nie widział lub nie mógł użyć konfiguracji LDAP.
- **#54 [MERGED] feat: add hostname generator tool for Blueprints** — - dodano kafel Generator hostname w Narzędzia, - kafel pokazuje liczbę aktywnych patternów, przykładowy pattern i następny numer, - Generator hostname korzysta z istniejącego backendowego HostnameScheme — bez duplikowania logiki, - formularz patternu ma podgląd wygenerowanej nazwy,
- **#55 [MERGED] UI: przebudowa „Nowy Blueprint” na 9-krokowy wizard** — Przebudowa tworzenia nowego Blueprintu z dużego formularza technicznego na nowoczesny wizard krok-po-kroku. Backend, API, model Blueprintu, compile blueprint() i POST /blueprints/{id}/execute pozostają bez zmian. Wizard prowadzi przez 9 kroków: 1. Podstawy - nazwa,
- **#56 [MERGED] VM classification: Environment TEST/DEV/NONPROD/PROD i APMID** — Dodano centralną klasyfikację VM opartą o Environment i APMID .
- **#57 [MERGED] Fix updater UI: zakończona aktualizacja nie zostaje jako „trwa”** — Po dojściu instalatora do 100% ekran potrafił jednocześnie pokazywać: komunikat „Instalacja lub aktualizacja zakończona pomyślnie”, status „Aktualizacja trwa”, przycisk „Aktualizacja trwa…”. Powodem był stan zapisany jako: status=running phase=complete progress=100 Marker 100% pochodzi z instalatora jeszcze przed tym, 
- **#58 [MERGED] fix(ui): repair hostname pattern edit ID handling** — Edycja patternu hostname kończyła się walidacją FastAPI: id: Input should be a valid integer, unable to parse string as an integer.
- **#59 [MERGED] UI: przebuduj panel postępu aktualizacji** — przebudowany panel „Postęp operacji” czytelniejsza hierarchia: etap, status i procent postępu osobny licznik etapów, np. 14/14 bardziej kompaktowy i nowoczesny pipeline aktualizacji lepsze połączenia między krokami oraz stany complete/active/failed animacja aktywnego paska podczas trwającej operacji poprawiona responsy
- **#60 [MERGED] feat(ui): generator wpisu hosta do Ansible inventory** — Dodaje do sekcji Narzędzia generator pojedynczego wpisu hosta dla Ansible. Zakres: - kafelek w Centrum narzędzi dla użytkowników z ansible.read, - osobny widok bez dodatkowej pozycji w sidebarze,
- **#61 [MERGED] feat: queue Proxmox provisioning while provider is offline** — Zapisać provisioning VM w lokalnej bazie Cloudportal nawet wtedy, gdy Proxmox jest chwilowo niedostępny, a następnie automatycznie wykonać go po odzyskaniu połączenia. - job i pełna konfiguracja deploymentu pozostają trwale zapisane w PostgreSQL, - przed terraform.apply dla Proxmox worker wykonuje lekki preflight dostę
- **#62 [MERGED] Fix: działający przycisk menu na desktopie** — Przycisk hamburgera był widoczny także na desktopie, ale jego handler sterował wyłącznie mobilnym stanem menu-open. Dla szerokości 760 px kliknięcie nie miało więc widocznego efektu.
- **#63 [MERGED] Produkty z gotowych Blueprintów** — Zmienia ścieżkę „Nowe wdrożenie” na katalog „Produkty”. - pokazuje tylko aktywne Blueprinty dostępne dla użytkownika; - odrzuca Blueprinty oparte na wyłączonym template Terraform/OpenTofu; - dodaje kafelki produktów z akcją „Utwórz VM”;
- **#64 [MERGED] UI: przenieś Generator hostname do Narzędzi** — - Generator hostname nie jest już osobną pozycją głównego menu bocznego. - Widok hostnames jest teraz podwidokiem Narzędzia (navigation: false, navigationParent: 'tools'). - Karta Generatora hostname pozostaje w ekranie Narzędzia i stamtąd otwiera pełny generator. - W samym generatorze dodano przycisk ← Narzędzia, aby 
- **#65 [MERGED] Rozdziel ustawienia Environment i APMID** — Rozdziela wspólne okno „Environment i APMID” na dwa niezależne obszary ustawień. - osobna karta „Environment” w Ustawieniach; - osobny modal konfiguracji Environment; - osobna karta „APMID” w Ustawieniach;
- **#66 [MERGED] Przenieś APMID do Narzędzi jako edytowalną listę** — Przenosi zarządzanie APMID z Ustawień do sekcji Narzędzia. - Environment pozostaje w Ustawieniach; - APMID dostaje osobną kartę w Narzędziach; - osobny widok APMID działa jak prosty arkusz/lista;
- **#67 [MERGED] fix(ui): redesign Monitoring overview** — przebudowano górną część widoku Monitoring, dodano czytelny nagłówek stanu platformy, Backend / Workery / Alerty / Dispatcher mają osobne nowoczesne karty z ikonami i kolorami statusu, zielony / żółty / czerwony stan zależy od realnego health checku, skrócono i uporządkowano opisy pod wartościami, pusty stan alertów po
- **#68 [MERGED] Blueprint: wybór APMID przy tworzeniu VM** — - przy uruchamianiu Blueprintu Proxmox wykrywane jest, czy APMID został zapisany w Blueprintcie - jeśli Blueprint nie ma APMID, formularz „Utwórz VM” pobiera listę APMID i wymaga wyboru - wybrany APMID trafia do backendu jako parametr wykonania - backend waliduje runtime APMID względem skonfigurowanej listy
- **#69 [MERGED] Globalne Location i Role dla hostname** — Usuwa obowiązek ręcznego podawania {location} i {role} w kreatorze Blueprintu oraz generatorze hostname. - dodaje w Narzędziach osobną konfigurację Location i Role; - wartości są zapisywane w /settings/vm-classification jako hostname defaults; - domyślne wartości to location=wro i role=server;
- **#70 [MERGED] Popraw UI postępu aktualizacji** — Przebudowuje panel postępu aktualizacji na bardziej czytelny i zwarty. - usuwa rozciągnięty timeline 14 kroków w jednym rzędzie; - etapy są pokazane jako czytelne kafelki 7×2 na desktopie, 4 kolumny na średnich ekranach, 2/1 na mobile; - każdy etap ma jawny stan: Oczekuje / W toku / Gotowe / Błąd;
- **#71 [MERGED] APMID: dodaj domyślne i nieusuwalne LEO** — - LEO jest zawsze obecne jako systemowy APMID, także gdy w bazie nie ma jeszcze ustawień klasyfikacji - backend zawsze przywraca LEO przy zapisie, więc nie można go usunąć ani zastąpić przez API - LEO jest zawsze pierwszą pozycją listy APMID - w Narzędzia → APMID pozycja LEO nie ma przycisków Edytuj/Usuń i jest oznaczo
- **#72 [MERGED] Napraw stan i współbieżność auto-update** — Naprawia przypadek widoczny w UI, gdzie updater nadal zgłaszał aktywny proces, ale panel pokazywał Aktualizacja dostępna i pozwalał ponownie uruchamiać aktualizację. - check remote() ma osobny tryb używany podczas aktywnej aktualizacji i nie nadpisuje już stanu running stanem update available; - /status zwraca operatio
- **#73 [MERGED] Blueprint: wybór patternu hostname z Generatora** — - w kroku Nazwa hosta dodano jawny selector Pattern hostname z Generatora - lista zawiera wszystkie aktywne patterny zapisane w Narzędzia → Generator hostname - wybór patternu aktualizuje hostnameSchemeId używany przez Blueprint - istniejące karty patternów nadal działają i są zsynchronizowane z selectem
- **#74 [MERGED] Fix: usuń nieużywane wartości hostname po zmianie patternu** — Po zmianie patternu hostname na taki, który nie używa wcześniejszych tokenów, wizard nadal wysyłał stare hostname values, np. env i environment. Backend poprawnie odrzucał taki Blueprint komunikatem: Blueprint hostname defaults contain tokens not used by the selected pattern: env, environment.
- **#75 [MERGED] fix(ui): replace Blueprint access checkboxes with dual-list selectors** — Przebudowano sekcję Dostęp i bezpieczeństwo w wizardzie Blueprintu. Zamiast dużych siatek checkboxów dla: - Dozwolone role - Role zarządzające Blueprintem
- **#76 [MERGED] Fix: usuń fałszywy stan „Update already in progress”** — Updater potrafił zgłaszać Update already in progress, mimo że nie było aktywnej aktualizacji. Przyczyna: stan running zapisany w state.json mógł przeżyć restart serwisu updatera. Dodatkowo nowo uruchomiony wątek aktualizacji sprawdzał ten stan z pliku i kończył się od razu, zamiast traktować żywy wątek jako źródło praw
- **#77 [MERGED] fix(ui): redesign LDAP settings panel** — Przebudowano sekcję LDAP w Ustawieniach, żeby była czytelniejsza i bardziej spójna z resztą panelu. nowy nagłówek LDAP z ikoną, statusem i akcjami, przyciski Testuj połączenie i Konfiguruj LDAP przeniesione bezpośrednio do sekcji LDAP, konfiguracja podzielona na dwie czytelne grupy: Połączenie oraz TLS i mapowanie , wa
- **#78 [MERGED] feat(ui): default Blueprint manager roles** — Dla nowego Blueprintu domyślnie wybierane są role: - Administrator - Infrastructure Administrator Dotyczy:
- **#79 [MERGED] Rozdziel Produkty i Moje zasoby** — Rozdziela obecny widok Produkty od listy wdrożonych maszyn i operacji. - Produkty pokazują wyłącznie dostępne Blueprinty/produkty do uruchomienia; - dodaje osobną zakładkę i pozycję nawigacji Moje zasoby; - Moje zasoby pokazują VM, inne zasoby zarządzane oraz wdrożenia dostępne przez bieżące uprawnienia;
- **#80 [MERGED] feat(ui): move Environment management to Tools** — Panel zarządzania środowiskami został przeniesiony z Ustawienia do Narzędzia . Dodano nową kartę Środowiska w Centrum narzędzi: - pokazuje stan TEST / DEV / NONPROD / PROD, - pokazuje liczbę aktywnych środowisk,
- **#81 [MERGED] Blueprint: wybór APMID i Environment przy tworzeniu VM** — - w kroku VM kreatora Blueprintu dodano dwa przełączniki: - Wybieraj APMID podczas tworzenia VM - Wybieraj Environment podczas tworzenia VM - wybrane w Blueprintcie APMID i Environment pozostają wartościami domyślnymi
- **#82 [MERGED] Zachowaj scroll logu aktualizacji podczas odświeżania** — Naprawia przewijanie sekcji Log techniczny podczas automatycznego pollingu statusu aktualizacji. - pierwsze otwarcie logu ustawia widok na najnowszych wpisach na dole; - jeżeli użytkownik jest przy dole, nowe wpisy utrzymują widok przy dole; - jeżeli użytkownik przewinie log wyżej, polling zachowuje jego ręczną pozycję
- **#83 [MERGED] fix(terraform): synchronize deployment status with worker workflow** — Wdrożenie VM mogło przez długi czas pozostawać w stanie W kolejce , mimo że Terraform utworzył już maszynę w Proxmox.
- **#84 [MERGED] UI: przebuduj widok Moje zasoby** — przebudowany widok Moje zasoby zgodnie z przygotowanym mockupem odświeżone zakładki Produkty / Moje zasoby z wyraźnym aktywnym stanem trzy nowe karty podsumowania: VM, Inne zasoby, Wdrożenia karty mają ikony, liczniki, opis i skrót przewijający do odpowiedniej sekcji sekcje zasobów dostały własne ikony, czytelniejszy n
- **#85 [MERGED] Napraw Terraform apply → inventory VM i live logi** — Naprawia przypadek, w którym Terraform tworzy VM, ale użytkownik nie widzi dalszego postępu w oknie logów ani nowej VM w Moje zasoby bez ręcznego odświeżania. Backend: - synchronizacja ManagedResource i ManagedVM po terraform.apply jest wykonywana atomowo w jednej transakcji; - dla Proxmox VMID i node są zapisywane raz
- **#86 [MERGED] fix(ui): prevent My Resources sections from overlapping** — Na widoku Moje zasoby kafelki podsumowania nachodziły wizualnie na sekcję Maszyny wirtualne .
- **#87 [MERGED] Napraw anulowanie jobów i odbudowę inventory VM** — Naprawia dwa powiązane problemy widoczne w Moje zasoby: deployment pozostający bez końca w W trakcie oraz nieskuteczne anulowanie. Anulowanie: - running job przechodzi od razu do jawnego stanu cancelling / Anulowanie…; - cancel requested jest widoczny w UI i ponowne kliknięcie anulowania jest idempotentne;
- **#88 [MERGED] Feature/blueprint cloudinit qemu credentials** — Nazwa domeny/modułu:
- **#89 [MERGED] perf(terraform): reuse init and provider cache** — dodano współdzielony cache providerów: Terraform: /var/lib/cloudportal-backed/terraform-plugin-cache OpenTofu: /var/lib/cloudportal-backed/tofu-plugin-cache ustawiany jest TF PLUGIN CACHE DIR, cache jest współdzielony między deploymentami, init jest chroniony globalnym lockiem, żeby kilka workerów nie pobierało tego sa
- **#90 [MERGED] fix(installer): quiet API startup retries and show diagnostics** — Podczas końcowego healthchecku instalator wypisywał serię: curl: (7) Failed to connect to 127.0.0.1 port 8765 mimo że API mogło jeszcze normalnie startować.
- **#91 [MERGED] Fix: domknij QEMU Guest Agent i credential VM w Blueprintach** — domknięcie funkcji z PR 88: automatyczna instalacja qemu-guest-agent przez Terraform/cloud-init, gdy Blueprint ma włączone oczekiwanie na QEMU Agent wybór SSH credentiala w Blueprintcie, który ustawia na VM użytkownika i publiczny klucz SSH przez cloud-init klucz prywatny pozostaje wyłącznie zaszyfrowany w Cloudportal 
- **#92 [MERGED] Fix BlueprintDeployment schema syntax** — Naprawia uszkodzony regex pola apmid, przywraca pola environment i wyboru przy wykonaniu oraz usuwa przypadkowo zduplikowany/uszkodzony fragment końca schemas.py, który powodował SyntaxError podczas startu API.
- **#93 [MERGED] fix(api): repair broken BlueprintDeployment schema** — Backend nie startuje, a instalator kończy się błędem healthchecku na porcie 8765. Traceback wskazuje: SyntaxError: unterminated string literal w app/api/schemas.py przy polu BlueprintDeployment.apmid.
- **#94 [MERGED] fix: execute Blueprint workflow DAG in provisioning worker** — Naprawia krytyczny problem, w którym Blueprint zapisywał workflow, ale worker ignorował workflow.steps i wykonywał wyłącznie ogólny terraform.apply. - worker odczytuje job.payload.blueprint.steps, - wykonuje DAG zgodnie z depends on, - wykrywa brakujące zależności, duplikaty i cykle,
- **#95 [MERGED] fix: harden Blueprint VM provisioning for QEMU agent and SSH credentials** — Naprawia punkty 2, 3 i 4 z audytu ścieżki Blueprint → VM. - wait for agent jest wykonywany przez silnik workflow niezależnie od Ansible. - Dodany test regresyjny potwierdza, że po terraform apply worker wywołuje oczekiwanie na QEMU Guest Agent także gdy context.ansible is None. - usunięty niebezpieczny fallback local p
- **#96 [MERGED] fix: complete Blueprint execution consistency and VM IP inventory** — Naprawia punkty 5–8 z audytu ścieżki Blueprint → VM. Widok Blueprintów wylicza teraz komplet wymaganych uprawnień dla konkretnego Blueprintu: - blueprints.execute - jobs.execute
- **#97 [MERGED] UI: zamień popupy aplikacji na normalne strony z historią** — wszystkie popupy korzystające z głównego modal są teraz renderowane jako normalne widoki wewnątrz obszaru aplikacji zamiast jako overlay <dialog dotyczy m.in. konfiguracji LDAP, użytkowników, credentiali, providerów, IPAM, hostname managera, Blueprintów, wdrożeń, szczegółów i konsoli VM globalne wyszukiwanie pozostaje 
- **#98 [MERGED] Fix Terraform workflow step timeouts** — Naprawia błędne timeouty podczas terraform.apply w Blueprintach. Zmiany: - kroki terraform plan i terraform apply respektują co najmniej globalny CP EXECUTION TIMEOUT, - automatycznie generowany workflow ustawia 3600 s dla kroków Terraform zamiast 600 s,
- **#99 [MERGED] Add VM user credential selection to Blueprint Designer** — Rozszerza Blueprint Designer o jawny wybór credentiala użytkownika VM. - pokazuje wyłącznie credentiale SSH obsługujące cloud-init/public key, - username jest pobierany z credentiala, - publiczny klucz jest wstrzykiwany do authorized keys przez istniejący backend/cloud-init,
- **#100 [MERGED] fix: harden Blueprint VM provisioning end-to-end** — Naprawia wszystkie pozostałe problemy wykryte w audycie ścieżki Blueprint → VM. Zakres: 1. Deklaratywne kroki po terraform apply - walidacja Blueprintu odrzuca clone vm/configure vm/cloud init/start vm/set hostname/set tags umieszczone po terraform apply,
- **#101 [MERGED] fix: make Blueprint VM workflow match real runtime execution** — Naprawia problemy, przez które workflow tworzenia VM wyglądał poprawnie w Designerze, ale zachowywał się inaczej w workerze. Najważniejsze zmiany: - workflow runtime pokazuje tylko akcje faktycznie wykonywane krok po kroku, - hostname/IPAM/clone/cloud-init/tagi pozostają compile/declarative state Terraform i nie udają 
- **#102 [MERGED] fix: close remaining VM workflow provisioning regressions** — Naprawia pozostałe problemy po PR 101 w ścieżce Blueprint/Deployment → workflow → Terraform/Proxmox → Ansible/inventory. Zmiany: - zwykły Deployment + Ansible używa teraz listy IP z wait for ip zamiast boolean z wait for vm, - statyczny/IPAM adres jest uznawany za gotowy dopiero po potwierdzeniu, że VM jest running,
- **#103 [MERGED] fix: harden provisioning lifecycle, ACL and Proxmox inventory** — PR zamyka wykryte regresje w provisioning, job lifecycle, ACL, Proxmox i inventory.
- **#104 [MERGED] feat: add global Blueprint auto-approval policy** — Dodaje globalną politykę approval dla wykonywania Blueprintów. Zachowanie: - nowe globalne ustawienie auto approve for executors, - domyślnie: true,
- **#105 [MERGED] ci: validate PR 103 head** — Techniczny PR walidacyjny dla dokładnego SHA 83010ce07e718aa0fff02504308416c09778408c z 103. Nie mergować; zostanie zamknięty po pełnym Backend CI.
- **#106 [MERGED] fix: harden VM workflow approval, provider validation and plan persistence** — Naprawia wszystkie pozostałe problemy wykryte w ścieżce tworzenia VM z workflow. Najważniejsze zmiany: - prawdziwe pause/resume na kroku approval, - wymuszona kolejność terraform plan → approval → terraform apply,
- **#107 [MERGED] refactor(ui): introduce frontend architecture foundation** — Warstwa: frontend platform / UI architecture foundation.
- **#108 [MERGED] fix: harden VM provisioning lifecycle after approval workflow changes** — Naprawia pozostałe problemy wykryte w ścieżce tworzenia VM po PR 106. Zakres: - centralna polityka approval dla każdego Blueprint terraform.apply: - execute Blueprint,
- **#109 [MERGED] UI: kolorowanie logu technicznego aktualizacji** — kolorowanie statusów logu: OK, INFO, WARN, FAIL/ERROR wyróżnienie numerów etapów, np. [6/7] kolorowanie URL-i oraz ścieżek systemowych zachowanie obecnego tekstu logu i działania przycisku „Kopiuj log” renderowanie bez innerHTML, więc log pozostaje odporny na wstrzyknięcie HTML
- **#110 [MERGED] fix: noVNC dynamic module loading** — Konsola VM kończyła się błędem: error loading dynamically imported module: .../novnc/core/rfb.js Backend przekazywał do przeglądarki Content-Type zwrócony przez Proxmox/pveproxy. Moduły ES są ładowane w trybie ścisłym i przy X-Content-Type-Options: nosniff JavaScript z typem application/octet-stream albo text/plain zos
- **#111 [MERGED] Blueprint: wybór użytkownika SSH z Credentiali** — Dodaje możliwość wybrania nazwy użytkownika SSH z zapisanych Credentiali bez blokowania ręcznego wpisania loginu. Zmiany: - pole Użytkownik SSH korzysta z podpowiedzi datalist z unikalnych użytkowników credentiali typu SSH, - ręczne wpisywanie nadal działa,
- **#112 [MERGED] feat: vRA-style graphical Blueprint Designer with YAML** — Gotowe: nowy pełnoekranowy graficzny Blueprint Designer inspirowany vRA paleta komponentów workflow z drag-and-drop canvas DAG z ręcznym rozmieszczaniem kroków ręczne tworzenie zależności między krokami oraz usuwanie połączeń auto-layout grafu inspector kroku: typ, ID, depends on, retry, timeout, rollback, conditions i
- **#113 [MERGED] feat: durable event broker and extension runtime** — Rozbudowa backendu o trwały event broker i warstwę extensibility.
- **#114 [MERGED] fix(day2): integrate action framework and harden execution safety** — Repaired the conflicts with current main and the merged tenancy/project/context domains, as requested by the user. Current head: 81b9125c5512a2069cd82dff699e65c35a7f80c8, tree 874aa40f92d819357cf2aa7245cc7e2aadc701a5. The history preserves the original Day-2 branch and main merge 5bc97d4c4499f96eaef62777041e5a12cfbdee7
- **#115 [MERGED] test: add vRA Blueprint Designer regression guards** — Follow-up po merge PR 112. Dodaje testy kontraktowe dla ostatnich poprawek review: kliknięcie krawędzi nie propaguje do viewportu przed dblclick, lista template jest ograniczona do typu wybranego providera, deployment credential jest związany z credentialem providera, lokalna walidacja wykrywa mismatch provider/templat
- **#116 [MERGED] chore: sync main into event broker enterprise branch** — Synchronizacja aktualnego main do feature/event-broker-enterprise-consumers przed właściwym PR. Bez zmian funkcjonalnych.
- **#117 [MERGED] feat: enterprise event schemas and durable consumers** — Drugi etap rozbudowy Event Broker / Extensibility do wersji enterprise.
- **#118 [MERGED] feat(tenancy): governance foundation and scoped authorization** — Global UserRole assignments remain unchanged. Tenant grants never become global permissions or authorize existing unscoped infrastructure APIs. Explicit global tenants.admin plus the operation permission is required for cross-tenant administration; role names and usernames are not authorization checks. Effective delega
- **#119 [MERGED] feat(projects): scoped project administration and validated execution context** — Tenant-scoped project permissions inherit within that tenant. Ordinary tenant membership alone grants no access to sibling projects. Project roles are bounded by assignment-time relational permission ceilings and live role permissions. Global, tenant and project assignments remain distinct, API token scopes remain a ce
- **#120 [MERGED] fix(projects): reconcile scoped context with merged project administration** — This repairs and completes the project-context integration slice, not the entire governance specification. Existing infrastructure resources are not made tenant/project-isolated by selecting a context. Resource ownership/backfill, exhaustive request/worker scoping, quotas/reservations, leases, placement and policy inte
- **#121 [MERGED] feat(governance): add hierarchical quotas and atomic reservations** — PR 121 jest oparty na aktualnym main po scaleniu 122, które przejęło wcześniejszą implementację Tenant/Project resource scope. Ten PR dostarcza kolejny zamknięty gate governance: hierarchiczne quotas / usage / atomic reservations . - limity Tenant i Project dla vm count, vcpu, memory mb, disk gib; - trwały ledger użyci
- **#122 [MERGED] feat(governance): integrate Tenant/Project resource scope** — Live account/token/membership checks, scoped SQL filtering, infrastructure assignments and saved worker execution scope. The merge retains main's Day-2 default role grants while excluding global governance permissions from Infrastructure Administrator defaults. Customized roles are not reset.
- **#123 [MERGED] fix(migrations): serialize quota backfill JSON values** — Deployment updater fails on Alembic revision d3f8c41b72a0 while backfilling confirmed deployments: The migration creates quota allocations.dimensions as JSON, but its lightweight sa.table() definition leaves dimensions untyped. SQLAlchemy therefore uses NullType and does not apply JSON bind processing before psycopg re
- **#124 [MERGED] fix(updater): block unsafe auto-updates until candidate is healthy** — Rozbudowuje auto-update o bramki bezpieczeństwa przed wdrożeniem.
- **#125 [MERGED] feat(blueprints): allow password-only credential on VM** — Pole Credential ustawiany na VM w Blueprintach nie wymaga już klucza prywatnego. - credential SSH może zawierać: - samo hasło, - sam klucz prywatny,
- **#126 [MERGED] feat(updater): validate migrations on cloned live database before update** — Druga warstwa bezpieczeństwa auto-update: kandydat musi uruchomić się na kopii aktualnej bazy danych zanim produkcyjna instalacja zostanie zmieniona.
- **#127 [MERGED] fix(blueprints): allow QEMU Guest Agent install/wait toggles** — rozdziela instalację qemu-guest-agent od kroku wait for agent dodaje osobne przełączniki w kreatorze Blueprintu i szybkim formularzu Proxmox preflight SSH nie blokuje już samego przełącznika; blokada pozostaje przy wykonaniu, jeśli SSH nadal nie działa brak storage snippets blokuje wyłącznie automatyczną instalację, al
- **#128 [MERGED] fix(credentials): close post-merge Blueprint credential review findings** — przenosi credential in use() oraz politykę ochrony referencji credentiali z routera HTTP do app/credentials/service.py; router infrastruktury korzysta z domenowej polityki zamiast implementować lifecycle credentiala lokalnie; provider-agnostic edytor Blueprintu pokazuje pole Credential ustawiany na VM tylko dla proxmox
- **#129 [MERGED] fix(console): restore VM console from My Resources** — dodaje bezpośrednią akcję Konsola na karcie VM w Moje zasoby udostępnia inventory.consoleVm przez istniejący rejestr komend UI naprawia proxy assetów noVNC: statyczny /novnc/core/rfb.js jest pobierany bez nagłówka API tokenu, a autoryzacja jest używana wyłącznie jako fallback po HTTP 401/403 lub challenge 301/302/303/3
- **#130 [MERGED] Fix VM runtime status and show IP address** — Naprawia semantykę statusu VM w widoku szczegółów i dodaje adres IP. - status Proxmox running jest prezentowany jako Uruchomiona, a nie jako workflowowe W toku; - statusy stopped/paused mają etykiety właściwe dla stanu VM; - endpoint statusu VM zwraca primary ip: najpierw z inventory Terraform, potem z QEMU Guest Agent
- **#131 [MERGED] feat: automatically resume reconciled Terraform jobs after worker loss** — - dispatcher automatycznie wznawia terraform.apply po utracie heartbeat workera, jeżeli persisted Terraform state/inventory oraz quota potwierdzają bezpieczny stan do ponowienia; - wznowienie tworzy osobny, audytowalny job Recovery z retry of i limitem prób; - recovery przywraca persisted Terraform state i wykonuje pon
- **#132 [MERGED] fix: harden automatic worker recovery after post-apply interruption** — Follow-up do 131 po końcowym review. Zamyka trzy post-merge findings dotyczące durable checkpoint, widoczności Blueprint i powtórnego Ansible.
- **#133 [MERGED] Dodaj masowe akcje dla maszyn wirtualnych** — Dodać bezpieczne masowe akcje dla maszyn wirtualnych w widoku „Moje zasoby”. - wielokrotny wybór VM checkboxami, - „Wybierz wszystkie”, licznik zaznaczeń i czyszczenie wyboru, - masowe akcje: Uruchom, Wyłącz, Restart, Wymuś stop,
- **#134 [MERGED] Dodaj wybór wielu VM i masowe akcje zasilania** — Dodaje checkboxy wyboru VM w widoku „Moje zasoby”, zaznaczanie wszystkich oraz masowe akcje: Uruchom, Wyłącz, Restart i Wymuś stop. Akcje korzystają z istniejącego endpointu vms.power i respektują uprawnienie vms.power.
- **#135 [MERGED] Ujednolicenie pozycji Moje zasoby w nawigacji** — ukrycie starego wpisu nawigacyjnego my-resources bez usuwania jego routingu, zmiana etykiety inventory z „Zasoby” na „Moje zasoby”, ujednolicenie etykiet powrotu z widoku szczegółów VM, aktualizacja testów UI.
- **#136 [MERGED] Przebudowa nawigacji pod workflow klienta** — Docelowa kolejność: 1. Produkty 2. Moje zasoby 3. Platformy 4. Zadania 5. Blueprinty 6. Katalog IaC 7. Dane dostępowe 8. Tokeny API 9. Harmonogramy 10. Webhooki 11. Użytkownicy 12. Role i RBAC 13. Narzędzia 14. Ustawienia 15. Monitoring 16. Audyt 17. Moje konto 18. Tenanci 19. Projekty Dodatkowo: sidebar jest płaski, b
- **#137 [MERGED] Dodaj odtworzenie VM od zera** — nowy workflow odtworzenia na poziomie deploymentu, kontrolowany Terraform resource replacement zamiast bezpośredniego kasowania VM w providerze, przycisk w akcjach VM, modal potwierdzający operację destrukcyjną, testy backendu, executora i UI.
- **#138 [MERGED] Napraw stan i ponawianie masowych akcji VM** — czyszczenie zaznaczeń VM przy zakończeniu sesji, zachowanie postępu zaakceptowanych partii przy błędzie późniejszego requestu, retry tylko dla VM, które nie zostały jeszcze skutecznie obsłużone, stabilne klucze idempotency dla każdej partii w ramach ponowienia, poprawa dokumentacji nagłówka Idempotency-Key dla POST /de
- **#139 [MERGED] Usuń zakładkę „Moje zasoby” z widoku Produkty** — usunięcie productResourceTabs() z app/web/features/deployments.js, usunięcie martwych stylów przełącznika z app/web/styles/features/deployments.css, zastąpienie pozycji inventory skonsolidowanym widokiem my-resources w menu klienta, aktualizacja tekstu widoku Produkty, aktualizacja testów kontraktu UI.
- **#140 [MERGED] Napraw komunikat błędu zmiany hasła** — rozdzielenie błędu braku sesji od błędu niepoprawnego obecnego hasła, polski komunikat w UI, ustawienie fokusu na polu obecnego hasła po błędzie, testy backendu i UI.
- **#141 [MERGED] Rozbuduj install.sh o bezpieczną deinstalację** — Dodać do głównego install.sh pełny, bezpieczny i czytelny tryb odinstalowania aplikacji. - jawny tryb --uninstall, - zwykła deinstalacja usuwa runtime, jednostki systemd, helpery i konfigurację Nginx, ale zachowuje bazę, /etc/cloudportal-backed i /var/lib/cloudportal-backed, - --uninstall --purge-data usuwa także lokal
- **#142 [CLOSED-UNMERGED] Dodaj tryb instalacji Docker przez --docker** — install.sh --docker, konfiguracja hosta, portu, liczby workerów, TLS i wersji/ref, obsługa prywatnego repo przez --github-token-file / --github-config, trwała konfiguracja i named volumes, --docker --status, --docker --uninstall, --docker --uninstall --purge-data, test E2E instalatora Docker w CI, dokumentacja.
- **#143 [MERGED] Dodaj tryb instalacji Docker do install.sh** — instalacja przez install.sh --docker, konfiguracja hosta, portu, workerów, ref oraz TLS, obsługa prywatnego repo przez --github-token-file / --github-config, trwałe named volumes, --docker --status, --docker --uninstall, --docker --uninstall --purge-data, preflight, locking, rollback kandydata i test E2E w CI.
- **#144 [MERGED] Dodaj interaktywne menu trybu install.sh** — Gdy install.sh jest uruchomiony bez parametrów, najpierw zapytać użytkownika co chce zrobić, zamiast od razu rozpoczynać instalację. Menu jawnie zawiera deinstalację. - bezparametrowe uruchomienie otwiera menu operacji zamiast automatycznie instalować, - menu zawiera: 1. instalację/aktualizację systemd,
- **#145 [MERGED] Fix user creation without required password** — UserCreate.password is optional; an empty string is normalized to an omitted password. When no password is supplied, the backend stores a cryptographically random unknown credential instead of a usable default. Human accounts created without a password are marked for password setup and can use the existing reset-token 
- **#146 [MERGED] Group sidebar navigation by function** — ZASOBY: Produkty, Moje zasoby, Zadania AUTOMATYZACJA: Platformy, Blueprinty, Katalog IaC, Harmonogramy, Webhooki DOSTĘP I BEZPIECZEŃSTWO: Dane dostępowe, Tokeny API, Użytkownicy, Role i RBAC ORGANIZACJA: Tenanci, Projekty OPERACJE: Monitoring, Audyt ADMINISTRACJA: Narzędzia, Ustawienia KONTO: Moje konto testy kontraktu
- **#147 [MERGED] Fix blueprint runtime ENV/APMID and add tenant/project scope** — ukrywanie/blokowanie Environment i APMID, gdy są wybierane podczas tworzenia VM, wybór tenantu i projektu w kreatorze Blueprintu tylko przy więcej niż jednej dostępnej opcji, walidacja i testy przepływu.
- **#148 [MERGED] Fix QEMU Guest Agent bootstrap provisioning** — Usunąć blokadę ssh auth missing podczas provisioningu QEMU Guest Agent i zapewnić samodzielny bootstrap nowej VM bez bezwzględnego wymagania SSH do noda Proxmox. - wykonanie Blueprintu nie kończy się już błędem 409 przy niegotowym Proxmox SSH, - automatyczny wybór trybu: - snippet gdy storage snippets i SSH do noda PVE
- **#149 [MERGED] Bootstrap guest credentials and QEMU agent without Proxmox SSH** — create an ephemeral Linux account in the new VM through cloud-init using a generated Ed25519 key keep the bootstrap private key encrypted with the existing AES-GCM/master-key mechanism use the temporary account after terraform apply to create the requested guest account from the selected SSH credential install and enab
- **#150 [MERGED] Add project and Blueprint approval policy overrides** — hierarchia: Globalne → Projekt → Blueprint, nullable override na projekcie i Blueprintcie, spójne egzekwowanie polityki w jobach i runtime approval, UI projektu i Blueprintu, migracja bazy, testy API/UI/governance, integracja aktualnego main i poprawek bootstrapu/QEMU z PR 149.
- **#151 [MERGED] Fix PR 149 post-merge bootstrap behavior** — spójnie wymuszać guest-bootstrap przy wybranym Credential VM, pominąć cloud init snippet storage w obu formularzach Blueprintu, gdy konto docelowe będzie provisionowane przez guest-bootstrap, pokazać w wizardzie, że wybrany Credential VM celowo omija snippets/SSH do noda PVE, poprawić cleanup tymczasowego konta dla doc
- **#152 [MERGED] Rozbuduj status Docker o walidację wszystkich usług** — walidacja wymaganych usług Compose: postgres, redis, migrate, api, worker, dispatcher, proxy, kontrola running dla usług długowiecznych, kontrola Docker healthcheck dla PostgreSQL i Redis, kontrola migracji jako zakończonej exited/0, kontrola liczby workerów względem CP WORKER COUNT, kontrola endpointu HTTPS /api/v1/he
- **#153 [MERGED] Dodaj auto-naprawę stacka Docker do statusu** — domyślna auto-naprawa dla --docker --status i pozycji 4 menu, read-only --docker --status --no-auto-repair, blokada instalatora podczas recovery, wymóg roota tylko dla trybu naprawczego, próba uruchomienia Docker Engine, gdy daemon nie odpowiada, odtworzenie deklarowanego stanu przez docker compose up -d --remove-orpha
- **#154 [MERGED] fix: make Docker stack startup and auto-repair dependency-aware** — wymuszenie poprawnej kolejności startu przez service healthy dla PostgreSQL i Redis, osobny liveness healthcheck API bez cyklicznej zależności od workerów/dispatchera, proxy oczekujące na zdrowe API, selektywna auto-naprawa zamiast odtwarzania całego stacka, aktywny test TCP z nowego kontenera backendu do PostgreSQL:54
- **#155 [MERGED] feat(proxmox): configure first-boot Cloud-init and QEMU agent without DHCP/SSH bootstrap deadlock** — Merged into main together with 156. Main merge commit: 285605364e78ccf492d31844a374667f360674a6. GitHub confirms this PR is merged and closed. This branch's full history, including head f5cd4e89029d7987cb507b8f9bbb63463e8bdda3, is the second parent of integration commit 14f4cdf4f6b2647673bed20fc2775029a07bd108 on 156. 
- **#156 [MERGED] feat(proxmox): integrate first-boot Cloud-init, native ISO provisioning and workflow validation** — Both open Cloud-init PRs ( 155 and 156) are merged into main with merge commit 285605364e78ccf492d31844a374667f360674a6, preserving API-only first-boot provisioning and avoiding the DHCP/guest-SSH bootstrap cycle. - Preserved both histories with integration merge 14f4cdf4f6b2647673bed20fc2775029a07bd108; its second par
- **#157 [MERGED] feat: web-first full instance backup and migration** — Implementuje pełny backup/migrację instancji Cloudportal-backed z Web UI jako podstawowym workflow.
- **#158 [MERGED] fix(console): serve noVNC client locally** — Konsola VM kończy się w przeglądarce błędem: error loading dynamically imported module: .../api/v1/console-sessions/.../novnc/core/rfb.js Dotychczas Cloudportal pobierał kod klienta noVNC z konkretnego Proxmoxa i przekazywał go do przeglądarki przez sesyjny endpoint. To uzależniało start konsoli od wersji/patchy noVNC 
- **#159 [MERGED] Blueprint Designer: configurable workflow timeouts in advanced options** — przenosi retry, timeout, rollback i conditions kroku do sekcji Opcje zaawansowane w Blueprint Designer pokazuje opisowe pole Timeout Terraform / OpenTofu [s] dla terraform plan, terraform apply i terraform destroy dla pozostałych kroków pokazuje Timeout kroku [s] ustawia domyślne timeouty: Terraform/OpenTofu 3600 s, po
- **#160 [MERGED] feat: show current workflow stage in jobs** — Dodaje kolumnę Etap w widoku Zadania i aktualizuje ją automatycznie dla aktywnych jobów. Zakres: - backend zapisuje ostatni etap wykonania w payloadzie joba ( current stage) bez migracji bazy, - API /jobs i /jobs/{id} zwraca current stage,
- **#161 [MERGED] Fix console WebSocket 404 behind Nginx** — Naprawia 404 dla wss://.../api/v1/console-sessions/{id}/websocket. Przyczyna: - Nginx przekazywał żądanie WebSocket do Uvicorna jak zwykły HTTP GET, bez nagłówków Upgrade / Connection i bez HTTP/1.1. - FastAPI ma dla tej ścieżki wyłącznie endpoint WebSocket, więc zdegradowane żądanie HTTP kończyło się 404 Not Found.
- **#162 [MERGED] feat: select accessible VMs as Ansible targets** — Dodaje wybór VM w formularzu Uruchom Ansible . Zakres: - pobiera tylko aktywne VM widoczne przez inventory.read, więc lista respektuje tenant/project/RBAC, - łączy VM z zarządzanym zasobem i znanym primary ip,
- **#163 [CLOSED-UNMERGED] feat: allow direct SSH password in Proxmox cloud-init** — Dodaje możliwość wpisania hasła SSH bezpośrednio w sekcji Cloud-init szybkiego formularza Proxmox. - nowe pole Hasło SSH (opcjonalnie) obok użytkownika SSH i klucza publicznego; - hasło nie jest zapisywane w Blueprint JSON ani terraform.tfvars.json; - po zapisie tworzony jest dedykowany, szyfrowany credential SSH i Blu
- **#164 [MERGED] feat: allow direct SSH password in Proxmox cloud-init** — Dodaje możliwość wpisania hasła SSH bezpośrednio w sekcji Cloud-init szybkiego formularza Proxmox. - nowe pole Hasło SSH (opcjonalnie) obok użytkownika SSH i klucza publicznego; - hasło nie jest zapisywane w Blueprint JSON ani terraform.tfvars.json; - po zapisie tworzony jest dedykowany, szyfrowany credential SSH i Blu
- **#165 [MERGED] Add reusable local template account to Blueprints** — Dodaje w Blueprintach osobny Credential SSH dla konta lokalnego, które już istnieje w bazowej VM/template. Zakres: - nowe pole template guest credential id w definicji Blueprintu, - osobny wybór „Istniejące konto lokalne w template” w kreatorze, szybkiej edycji i klasycznym edytorze,
- **#166 [MERGED] feat: configurable parallel job execution limit** — - dodano globalne ustawienie max parallel jobs (1–64) przechowywane w settings jako job execution, - domyślna wartość dziedziczy CP WORKER COUNT, więc zachowuje dotychczasową pojemność wykonawczą, - dispatcher rezerwuje sloty także dla jobów już przekazanych do RQ i nie dispatchuje kolejnych ponad limit, - ustawienie d
- **#167 [MERGED] fix: restore green main CI and frontend module boundaries** — main miał cztery regresje pełnego pytest oraz dwa moduły UI powyżej limitu 1400 linii. Ten PR izoluje naprawę bazowego CI od funkcji konfigurowalnej równoległości z PR 166.
- **#168 [MERGED] Add existing template account mode to Blueprint Cloud-init** — Dodaje obsługę konta lokalnego, które już istnieje w VM/template i ma być używane przez kolejne etapy Blueprintu. Zmiany: - nowy tryb guest account mode: - cloud init managed — dotychczasowe zachowanie, Cloud-init tworzy/konfiguruje konto,
- **#169 [MERGED] Allow multiple ordered Ansible runbooks in Blueprints** — Dodaje możliwość wyboru wielu runbooków Ansible w sekcji „Konfiguracja systemu po wdrożeniu”. Zakres: - zamiast pojedynczego playbooka Blueprint może przechowywać do 20 wpisów w deployment.ansible runs, - każdy runbook ma osobno:
- **#170 [MERGED] feat: add administrator-managed custom Ansible playbooks** — - dodano ansible.manage dla administratorów Ansible, - własne playbooki są przechowywane globalnie i trwale w PostgreSQL przez tabelę settings, bez zależności od lokalnego filesystemu workera, - API CRUD: dodawanie, odczyt, wersjonowanie, włączanie/wyłączanie i usuwanie własnych playbooków, - własne playbooki pojawiają
- **#171 [MERGED] Fix Docker installer master-key bootstrap without TTY** — Naprawia instalację Docker zatrzymującą się na etapie „Klucz szyfrujący” z błędem the input device is not a TTY. Zmiany: - dodaje -T do docker compose run dla bootstrap --key-only, - aktualizuje test kontraktu instalatora,
- **#172 [MERGED] test: cover custom Ansible playbook security regressions** — Dodaje testy regresyjne do funkcji własnych playbooków Ansible już znajdującej się w main. Zakres: - blokada free-form copy src=... z pliku kontrolera, - blokada aliasu ansible.legacy.copy,
- **#173 [MERGED] Add installer administrator recovery mode** — Dodaje tryb recovery administratora do install.sh bez pełnej reinstalacji. Zakres: - --recovery-admin oraz alias --recovery-password, - recovery dla systemd i Docker,
- **#174 [MERGED] feat: automatic AWX onboarding with cloud-init** — Dodaje pełną integrację AWX / Automation Controller z provisioningiem Blueprintów.
- **#175 [MERGED] Fix updater status in Docker installations** — Naprawia błąd Updater status token is unavailable w instalacjach Docker. Zakres: - hostowy cloudportal-updater.service również dla trybu Docker, - generowanie i trwałe przechowywanie updater.token oraz updater-status.token,
- **#176 [MERGED] Popraw UI formularza danych dostępowych AWX** — Zmiany: - porządkują układ sekcji połączenia AWX i TLS, - usuwają pustą połowę wiersza przy pojedynczym polu sekretu, - poprawiają responsywność formularza,
- **#177 [MERGED] Harden Docker updater runtime preflight** — Follow-up do 175 po review Codex. Naprawia dwa problemy wykryte po merge: - P1: runtime preflight dla Docker nie jest już pomijany. Kandydat jest budowany i uruchamiany w izolowanej sieci Docker z osobnym PostgreSQL i Redis, na kopii dumpa produkcyjnej bazy. Migracje i healthcheck wykonują się przed właściwą aktualizac
- **#178 [MERGED] Dodaj organizację/projekt do kreatora Blueprint i rozbuduj scoped RBAC** — Zakres zmian: - Kreator „Nowy Blueprint” zawsze pokazuje wybór Organizacji i Projektu. - Lista zakresów pochodzi z backendu i jest filtrowana przez blueprints.create. - Wybrany zakres jest przekazywany jako X-Tenant-ID + X-Project-ID; backend ponownie autoryzuje zapis.
- **#179 [MERGED] Add dedicated AWX step to Blueprint wizard** — dodaje osobny krok AWX / Automation Controller w kreatorze Nowy Blueprint dodaje przełącznik użycia AWX dla Blueprintu rozszerza discovery AWX o organizacje, projekty, inventory i Job Templates pozwala wybrać organizację AWX, projekt AWX, inventory oraz opcjonalny Job Template zapisuje organization id i project id w ko
- **#180 [MERGED] Increase API rate limit and exclude UI assets** — Zwiększa domyślny globalny limit API z 300 do 1200 żądań/min na adres klienta. - CP REQUEST LIMIT pozostaje konfigurowalny przez środowisko. - Dodany zakres walidacji 60–100000. - Statyczne zasoby /ui nie zużywają już limitu API.
- **#181 [MERGED] Otwieraj logi zadania jako bezpośrednio linkowalną stronę** — Zmiana zachowania widoku Zadania: - kliknięcie Logi nie otwiera już okna/page-surface, - logi są osobnym widokiem pod stabilnym adresem /jobs/<job-id , - adres można otworzyć bezpośrednio po wklejeniu do przeglądarki lub przekazać innej osobie z odpowiednimi uprawnieniami,
- **#182 [MERGED] Add VM filters and card/list view** — dodaje wyszukiwarkę do sekcji Maszyny wirtualne dodaje panel Filtry z kryteriami: APMID, środowisko, właściciel, projekt, tenant, status, platforma i node dodaje szybki filtr Tylko moje VM dodaje przełącznik widoku Kafelki / Lista zapamiętuje widok, wyszukiwanie i filtry w localStorage pokazuje w kartach metadane APMID
- **#183 [MERGED] Fix VM browser modularity after filters merge** — wydziela przeglądarkę VM (wyszukiwanie, filtry, Kafelki/Lista) z deployments.js do deployments-vm-browser.js wydziela helpery sekcji zasobów do deployments-resource-ui.js zachowuje funkcjonalność z 182 bez zmian kontraktu UI obniża deployments.js poniżej limitu 1400 linii wymaganego przez kontrolę modularności zachowuj
- **#184 [MERGED] Sprawdzaj adres IP co 10 sekund przez maksymalnie 3 minuty** — Zmiana dla kroku wait for ip: - odczyt adresu IP jest wykonywany co 10 sekund, - maksymalny czas oczekiwania dla DHCP wynosi 180 sekund, - istniejące Blueprinty z timeoutem większym niż 180 s są po stronie workera ograniczane do 3 minut dla wait for ip,
- **#185 [MERGED] Fix Blueprint workflow dependency editing** — blokuje zapis klasycznego Automation Designera, gdy depends on wskazuje nieistniejący krok, własny krok lub cykl pokazuje konkretny błąd z ID kroku zamiast ogólnego Workflow dependency is missing or self-referencing przy zmianie ID kroku automatycznie aktualizuje depends on oraz rollbacki wskazujące stare ID przy usuni
- **#186 [MERGED] Retry QEMU Guest Agent provider checks every 10 seconds** — wait for agent przy błędzie połączenia/API Proxmox wykonuje 3 retry odstęp między retry wynosi 10 sekund po trzecim retry wykonywana jest jeszcze ostatnia próba; jeżeli również się nie powiedzie, krok kończy się czytelnym błędem poprawne połączenie z Proxmox resetuje licznik kolejnych błędów normalne oczekiwanie na uru
- **#187 [MERGED] Add VM controls to noVNC console** — Dodaje pasek sterowania bezpośrednio nad konsolą noVNC. - Power ON - start VM - Power OFF - bezpieczne wyłączenie ACPI (shutdown) - CTRL+ALT+DEL - RFB.sendCtrlAltDel()
- **#188 [MERGED] Wykrywaj brakującą VM i proponuj usunięcie nieaktualnego wpisu** — Zmiana obsługi nieaktualnych VM Proxmox: - gdy odczyt statusu/konfiguracji/konsoli VM kończy się błędem, UI dodatkowo sprawdza /inventory/vms/{id}?refresh=true, - jeśli Proxmox potwierdza brak VM, zamiast ogólnego błędu pojawia się pytanie, czy usunąć nieaktualny wpis z aktywnych zasobów Cloudportal, - w widoku Invento
- **#189 [MERGED] Dodaj kolumnę daty utworzenia VM** — Dodaje kolumnę „Data utworzenia” w widoku listy sekcji Maszyny wirtualne. Zmiany: - data pochodzi z deployment.created at, a dla VM bez wdrożenia z ManagedVM.created at; - nowa kolumna i nagłówek w widoku „Lista”;
- **#190 [MERGED] Expose AWX onboarding in Blueprint quick edit** — Naprawia edycję Blueprintów z krokiem register awx. - dodaje konfigurację AWX do Szybkiej edycji i edytora klasycznego, - pokazuje Credential AWX, organizację, projekt, inventory, Job Template, grupowanie, retry/timeout i usuwanie hosta przy destroy, - pobiera zasoby AWX przez istniejący endpoint discovery,
- **#191 [MERGED] Dodaj wymuszone uruchomienie zadania poza limitem równoległości** — - dodano uprawnienie jobs.force dla kontrolowanego override limitu max parallel jobs; - dodano POST /api/v1/jobs/{id}/force-dispatch, który przekazuje pojedynczy job queued do RQ bez sprawdzania globalnego limitu równoległości; - force-dispatch nie omija anulowania, approval, provider backoff ani dostępności workerów i
- **#192 [MERGED] Show Blueprint provisioning immediately in My resources** — - po kliknięciu Utwórz VM z Blueprintu przechodzi od razu do Moje zasoby - VM pojawia się w sekcji Moje VM natychmiast po utworzeniu deploymentu, jeszcze przed inventory.vm.registered - tymczasowy wpis jest budowany z deploymentu Blueprintu i automatycznie zastępowany właściwym ManagedVM - kafelek pokazuje Komentarz: P
- **#193 [MERGED] Fix false QEMU Guest Agent readiness failures** — traktuje poprawny HTTP 2xx z /agent/ping jako gotowego QEMU Guest Agenta nawet gdy Proxmox zwraca data: null HTTP 500 z /agent/ping jest traktowane jako stan jeszcze niegotowy, a nie jako błąd uwierzytelnienia błędy HTTP 401/403/404/5xx z Proxmox nie są już maskowane jednym ogólnym komunikatem transport/TLS nadal daje 
- **#194 [MERGED] Harden Blueprint workflow recovery and external side effects** — Naprawia problemy wykryte podczas audytu workflow Blueprintów.
- **#195 [MERGED] Sync main into OVA appliance branch** — Synchronizacja bieżącego main przed walidacją funkcji OVA appliance.
- **#196 [MERGED] Dodaj OVA appliance jako Blueprint Proxmox** — - dodano import pliku .ova z Web UI jako nowy Blueprint typu appliance; - OVA jest streamowane poza limitem 1 MiB, walidowane jako bezpieczne archiwum tar i sprawdzane pod kątem path traversal/linków; - OVF jest analizowany w celu odczytu CPU, RAM, liczby kart sieciowych i kolejności dysków VMDK; - VMDK są walidowane p
- **#197 [MERGED] Popraw układ kart VM w widoku listy** — Zmiana porządkuje kartę VM z provisioningiem w widoku listy: - akcje pozostają w pierwszym wierszu zamiast spadać pod kartę, - stan provisioningu dostaje pełną szerokość pod głównymi danymi, - błąd i etap są czytelniejsze i lepiej wykorzystują szerokość,
- **#198 [MERGED] Napraw sprawdzanie QEMU Guest Agent przez Proxmox API** — Naprawa błędu, przez który workflow zgłaszał brak QEMU Guest Agent mimo poprawnego qm agent <vmid ping. Przyczyna: - endpoint /nodes/{node}/qemu/{vmid}/agent/ping był wywoływany metodą GET, - Proxmox traktuje agent/ping jako endpoint akcji i oczekuje POST,
- **#199 [MERGED] Napraw wybór APMID przy uruchamianiu Blueprintu** — Naprawia przypadek widoczny w UI, gdzie backend wymagał APMID (select apmid on execute), ale formularz uruchomienia Blueprintu nie pokazywał pola wyboru i kończył się błędem APMID must be selected when this Blueprint is executed. Przyczyna: - frontend uzależniał pokazanie APMID/Environment od deployment.template === 'p
- **#200 [MERGED] Fix Blueprint creation integrity, scoped RBAC and wizard validation** — Naprawia błędy wykryte w przepływie tworzenia Blueprintów: - project-scoped lista, create/edit/delete/execute i RBAC bez zależności od globalnego /auth/me - pamiętanie tenant/project po zapisie z wizarda - usunięcie wejścia do starej Szybkiej edycji na rzecz jednego kreatora
- **#201 [MERGED] Dodaj osobne URL-e dla kroków tworzenia i edycji Blueprintu** — Zmienia standardowe tworzenie i edycję Blueprintu z popupu na routowalną stronę kreatora. Nowe adresy: - tworzenie: /blueprints/new/step/1 … /blueprints/new/step/10 - edycja: /blueprints/edit/<id /<slug /step/1 … /blueprints/edit/<id /<slug /step/10
- **#202 [MERGED] Dodaj semantyczne URL-e do formularzy i widoków całego panelu** — Rozszerza routowalną nawigację wprowadzoną dla kreatora Blueprintów na pozostałe główne obszary Cloudportal. Najważniejsza zmiana architektoniczna: - wspólny rejestr registerRoutedForm() dla formularzy i widoków podrzędnych, - router rozpoznaje semantyczne ścieżki i utrzymuje aktywną właściwą sekcję sidebara,
- **#203 [MERGED] Keep VM identity always visible in list view** — Naprawia układ listy VM widoczny na screenie. Przyczyna: - karta VM zawsze zakładała 4 kolumny w nagłówku: checkbox / ikona / nazwa / status, - provisioning placeholder nie ma checkboxa bulk-selection,
- **#204 [MERGED] Sync runtime Environment and APMID with AWX** — Dodaje pełne odzwierciedlenie runtime klasyfikacji VM w AWX. Gdy Blueprint ma Wybieraj Environment podczas tworzenia VM i/lub Wybieraj APMID podczas tworzenia VM: - odpowiednia grupa AWX jest automatycznie wymuszona (env- / apmid- ), - wybrana wartość trafia do zmiennych hosta AWX,
- **#205 [MERGED] feat(installer): auto-update przez cron, domyślnie co 12 godzin** — Wspólny plik zmieniony: wyłącznie install.sh. Dodano test i dokumentację. Nie zmieniono aplikacji, schematów, migracji ani istniejących workflow CI. Tymczasowe pliki użyte do zintegrowania i sprawdzenia dużego instalatora usunięto przed otwarciem PR; końcowy diff zawiera tylko trzy pliki. PR pozostaje do przeglądu, bez
- **#206 [MERGED] Default new Blueprints to CloudPortal visibility** — Ustawia CloudPortal jako domyślnie zaznaczoną widoczność dla nowych Blueprintów. Zmiany: - stateDefaults().visibilityCloudportal = true w kreatorze, - BlueprintVisibility.cloudportal = True po stronie API, więc także Blueprint tworzony bez jawnego pola visibility ma spójny domyślny stan,
- **#207 [MERGED] Align provisioning text in VM list** — Wyrównuje tekst w wierszu provisioningu VM. Zmiany: - Provisioning, status (W toku), etykieta Etap i aktualny etap są na jednej osi, - na desktopie wewnętrzne wrappery używają display: contents, dzięki czemu wszystkie cztery elementy trafiają do jednego grida,
- **#208 [MERGED] Show VMID in job log page code and heading** — Dodaje numer VM bezpośrednio do strony logów zadania. Zmiany: - strona /jobs/<job id pokazuje Numer VM w metadanych, - nagłówek zmienia się na Logi zadania … · VMID <nr gdy numer jest znany,
- **#209 [MERGED] Set default job concurrency and workers to 10** — Ustawia domyślną równoległość wykonywania na 10: - CP WORKER COUNT domyślnie 10 w runtime i Docker Compose - max parallel jobs domyślnie 10 niezależnie od liczby workerów - świeża instalacja uruchamia 10 workerów
- **#210 [MERGED] Show VM deletion progress on My Resources cards** — Naprawia przypadek ze screena: po kliknięciu Usuń na nieudanym provisioningu karta dalej pokazywała stary błąd terraform.apply, mimo że backend wykonywał już terraform.destroy. Przyczyna: - composeProvisioningVms() śledził wyłącznie joby terraform.apply, - aktywny terraform.destroy był ignorowany,
- **#211 [MERGED] Refresh set tags web console contract** — Aktualizuje nieaktualny test UI po scaleniu walidatora workflow Blueprintów. Kod produktu pozostaje bez zmian.
- **#212 [MERGED] Mark missing Proxmox VMs and purge stale inventory** — Dodaje pełny lifecycle dla VM usuniętych poza Cloudportalem: - jeśli Proxmox odpowiada poprawnie, ale VMID nie istnieje, status VM w portalu staje się missing - jeśli VM pojawi się ponownie, status wraca do active - globalne inventory reconcile zapisuje missing/active w bazie i aktualizuje quota reconciliation
- **#213 [MERGED] Show hostname in job log details** — Dodaje pole Hostname do metadanych na stronie logów zadania. Wartość jest pobierana z nazwy deploymentu, a w razie jej braku z variables.hostname lub variables.name. Dodano też asercje regresyjne w tests/test web console.py.
- **#214 [MERGED] Isolate Terraform executions and reserve unique Proxmox VMIDs** — Usuwa klasę problemów prowadzących do VM identity is already linked to another deployment przy równoległych wdrożeniach. Najważniejsze zmiany: - każde wywołanie Terraform/OpenTofu nadal działa jako osobny OS process + osobna grupa procesów (start new session=True); logi pokazują teraz PID i process group dla init/plan/
- **#215 [MERGED] Prevent purged missing VM from reappearing** — Follow-up po 212: deployment ze statusem reconciliation required nie może tworzyć sztucznej karty provisioning po usunięciu brakującej VM z inventory. Dodaje kontrakt regresyjny.
- **#216 [MERGED] Fix legacy QEMU snippet state during native Cloud-init migration** — Naprawia błąd: Missing required argument: datastore id dla proxmox virtual environment file.qemu guest agent cloud init[0]. Przyczyna: - deployment został już przełączony na natywny NoCloud ISO (cloud init seed),
- **#217 [MERGED] Add bulk VM delete action** — Dodaje przycisk Usuń do paska masowych akcji VM w Moje zasoby. Zachowanie: - przycisk pojawia się obok Wymuś stop / Wyczyść, - działa tylko dla zaznaczonych VM, które użytkownik ma prawo usuwać,
- **#218 [MERGED] Hard-stop Proxmox VM before Terraform destroy** — Dodaje bezwarunkowy pre-destroy guard dla Proxmox: - przed każdym terraform.destroy backend sprawdza stan VM bezpośrednio przez Proxmox API - jeśli VM nie jest stopped, wysyła twarde stop - nie używa QEMU Guest Agent ani sieci gościa
- **#219 [MERGED] Fix force-start state for jobs already queued to workers** — Naprawia błąd z widoku Jobs: przycisk Wymuś start był nadal widoczny dla zadania ze statusem queued, mimo że job został już wysłany do RQ/worker queue. Kliknięcie kończyło się toastem Job is already dispatched to the worker queue. Zmiany: - JobOutput zwraca teraz dispatched at, - lista Jobs pokazuje W kolejce workera, 
- **#220 [MERGED] Prevent purged missing VMs from being rebuilt by inventory reconciliation** — Naprawia problem widoczny na screenie: po kliknięciu Usuń pozostałe dane rekord VM znikał, ale Moje zasoby natychmiast uruchamiało /inventory/reconcile, który odbudowywał wpis ze starego Terraform state. Przyczyna: - cleanup usuwał ManagedVM i ManagedResource, - powiązany deployment dostawał status reconciliation requi
- **#221 [MERGED] Add Proxmox VM clone-to-template tool** — Dodaje nowe narzędzie w Tools: VM → Template . Przepływ: - wybór providera Proxmox i źródłowej VM - wybór nowego VMID, nazwy template, node i opcjonalnego storage
- **#222 [MERGED] Fix provisioning badge stuck on running after completion** — Naprawia przypadek, w którym provisioning był już zakończony, ale karta VM nadal pokazywała W toku. Przyczyna: - widok Moje zasoby pobiera deploymenty, joby i inventory równolegle, - finalizacja joba aktualizuje w jednej transakcji job.status=successful, deployment.status=successful i czyści active job id,
- **#223 [MERGED] Add AWX inventory naming patterns to Blueprints** — Dodaje konfigurowalny pattern nazwy AWX Inventory w Blueprintach. Domyślny pattern: <Projekt -<APMID -<ENV . - <Projekt używa wybranego projektu AWX; bez wyboru korzysta z bieżącego projektu CloudPortal. - <APMID i <ENV są rozwiązywane przy wykonaniu Blueprintu.
- **#224 [MERGED] Fix Proxmox destroy stuck on missing QEMU Guest Agent** — Naprawia usuwanie VM po błędzie Terraform destroy. - ustawia stop on destroy = true dla proxmox-vm i proxmox-appliance, więc provider używa twardego Stop zamiast qmshutdown/QGA; - zachowuje istniejący backendowy hard-stop przed każdym terraform.destroy; - po błędzie usuwania UI pokazuje jawny przycisk Wymuś usunięcie i
- **#225 [MERGED] Add automatic Proxmox scope tags to provisioned VMs** — VMs provisioned through Proxmox now receive authoritative CloudPortal management metadata as tags. Changes: - add managed-by-cloudportal - add tenant-<slug and project-<slug
- **#226 [MERGED] Add VM provisioning history and Proxmox monitor** — rozszerza zakładkę Historia VM o lifecycle, deployment, joby/provisioning, Day-2 i audyt zgodnie z RBAC dodaje endpoint /inventory/vms/{id}/history z filtrowaniem bezpiecznych komunikatów jobów dodaje zakładkę Monitor w Zarządzaj VM pobiera bieżące metryki i RRD bezpośrednio z Proxmox API zakresy monitoringu: godzina, 
- **#227 [MERGED] Fix exact VM audit history matching** — Fixes VM history audit matching after 226. - matches the exact provider:node:vmid resource - matches child resources only with provider:node:vmid: - prevents VMID prefix collisions such as 889 vs 8890
- **#228 [MERGED] Allow forced Terraform destroy to supersede stale quota reservations** — Naprawia błąd The resource already has an active or uncertain quota reservation przy ponownym/wymuszonym terraform.destroy. Przyczyna: - wcześniejszy apply/destroy mógł zakończyć się błędem, timeoutem albo utratą workera, - jego quota reservation pozostawała w stanie reserved lub uncertain,
- **#229 [MERGED] Rebuild VM details UI** — Przebudowuje panel szczegółów VM, zachowując istniejące akcje i routing. Najważniejsze zmiany: - nowy hero z nazwą VM, stanem, VMID, trybem zarządzania, node, IP i deploymentem; - akcje podzielone na cztery grupy: Zasilanie, Operacje, Infrastruktura i Strefa ryzyka;
- **#230 [MERGED] Map CloudPortal tenant and project scope to AWX** — CloudPortal now owns AWX scope mapping instead of storing arbitrary AWX Organization/Project selections in each Blueprint. Mapping: - CloudPortal Tenant - AWX Organization - CloudPortal Project - AWX Project inside that Organization
- **#231 [MERGED] Show exact VM action progress in My Resources** — Zmienia ogólny status W toku na opis tego, co faktycznie dzieje się z VM. Przykłady: - W toku: włączanie VM - W toku: wyłączanie VM
- **#232 [MERGED] Add searchable source VM picker to clone-to-template tool** — Poprawia wybór źródłowej VM w Tools → VM → Template: - dodaje pole „Szukaj VM” - filtrowanie na żywo po VMID, hostname, node i statusie - format pozycji zaczyna się od numeru VM: 102 - vm01-AnsibleTower - chris - Zatrzymany
- **#233 [MERGED] Add OpenAPI docs button to API Tokens** — Dodaje przycisk Dokumentacja OpenAPI do widoku Tokeny API. Zachowanie: - przycisk jest zawsze widoczny w nagłówku widoku tokenów, - otwiera w nowej karcie wbudowaną dokumentację FastAPI/Swagger pod /docs,
- **#234 [MERGED] Add OpenAPI documentation button to API tokens** — Dodaje w widoku „Tokeny API” przycisk „Dokumentacja OpenAPI”, który otwiera FastAPI Swagger UI pod /docs w nowej karcie. Przycisk jest dostępny niezależnie od uprawnienia tokens.create; istniejąca kontrola RBAC dla tworzenia tokenów pozostaje bez zmian.
- **#235 [MERGED] Add Ansible playbook preview and management controls** — Rozbudowuje sekcję Playbooki Ansible w Katalogu IaC. - dodaje przycisk Podgląd dla każdego playbooka i pokazuje dokładne pliki YAML używane przez backend; - własne playbooki można bezpośrednio edytować , włączać/wyłączać i usuwać ; - playbook systemowy można otworzyć, uruchomić i utworzyć z niego edytowalną kopię jako 
- **#236 [MERGED] Run VM clone to template in background jobs** — Zmienia narzędzie Klon VM → Template na trwały job wykonywany przez workera. - kliknięcie tworzy proxmox.clone template i od razu przechodzi do Zadania ; - klonowanie, konwersja i weryfikacja nie zależą od otwartego widoku/przeglądarki; - etap jest zapisywany w current stage i widoczny w tabeli zadań;
- **#237 [MERGED] Redesign Proxmox task result modal** — Poprawia modal wyniku operacji Proxmox: - czytelny banner statusu zamiast pojedynczego badge - nazwy operacji przyjazne dla użytkownika, np. qmshutdown → „Bezpieczne wyłączenie VM” - osobne pola: Status, Wynik, Operacja, Rozpoczęcie, Zakończenie, Czas trwania
- **#238 [MERGED] Fix bulk VM power internal-error recovery** — Naprawia przypadek z masową akcją zasilania VM, w którym /day2-actions/bulk zwracał 500, a UI mimo niepowodzenia usuwał VM z zaznaczenia i przy kolejnym kliknięciu pokazywał „Wszystkie wybrane VM zostały już obsłużone”. Zmiany: - dla pojedynczej VM akcja zasilania używa bezpośrednio istniejącego endpointu Day-2 /resour
- **#239 [MERGED] Add managed avatars to Blueprints** — Dodaje globalny katalog awatarów Blueprintów oraz wybór awatara w kreatorze. Zakres: - nowe Narzędzia → Awatary Blueprintów z dodawaniem, edycją i usuwaniem ikon; - wejście akceptuje x-icon;base64,..., image/x-icon;base64,... i data:image/x-icon;base64,...; backend normalizuje wartość do data:image/x-icon;base64,...;
- **#240 [CLOSED-UNMERGED] Add managed avatars to Blueprints (superseded by 239)** — Zmiany zostały już włączone do main przez 239. Ten PR jest duplikatem utworzonym podczas równoległej aktualizacji gałęzi.
- **#241 [MERGED] Fix module boundary after Blueprint avatar UI** — Naprawia regresję CI po 239. Module boundary check blokował build, ponieważ: - app/web/features/blueprint-wizard.js miał 1434 linie; - app/web/features/deployments.js miał 1402 linie.
- **#242 [MERGED] Add enterprise Policy Engine with scoped ABAC enforcement** — enterprise Policy Engine obok RBAC i resource scope APMID/ENV whitelist access policies nested AND/OR/NOT conditions i rozszerzone operatory HARD / SOFT / ADVISORY, priority + specificity resolver DRAFT / DRY RUN / ENFORCED / DISABLED / ARCHIVED effects: allow/deny/approval/default/force/limit/tagging/placement/obligat
- **#243 [MERGED] Fix related CI regressions after recent VM workflow changes** — Naprawia powiązane regresje ujawnione przez Backend CI po ostatnich zmianach VM/Day-2. Zakres: - poprawia brakujący import Day2ActionRequest w teście inventory; - aktualizuje kontrakt katalogu do bieżącej wersji proxmox-vm = 7;
- **#244 [MERGED] Harden partial failures in Day-2 bulk actions** — Naprawia kolejną powiązaną klasę błędów w /day2-actions/bulk. Problem: - bulk izolował tylko Day2Failure; - błędy governance/quota/scope zwracane jako HTTPException mogły przerwać cały batch zamiast zostać przypisane do konkretnej VM;
- **#245 [MERGED] Fix remaining regressions on current main** — Naprawia 4 pozostałe awarie pełnego Backend CI na aktualnym main. Zmiany: - test merge migracji nie zakłada już historycznego head 7f3c1a9d4b20; weryfikuje pojedynczy bieżący head i poprawny upgrade z obu opublikowanych tipów; - test Cloud-init uwzględnia celowe zapisanie zarezerwowanego vm id do deployment.variables, 
- **#246 [MERGED] Fix live inventory refresh UI contract** — Aktualizuje ostatni nieaktualny test web console po rozdzieleniu repairInventory i refreshLive. Runtime używa teraz refreshLive do ?refresh=true dla /inventory/vms; test nadal oczekiwał starego repairInventory. Źródło: Backend CI run 1403 — 1 failed, 711 passed.
- **#247 [MERGED] Add VM delete button and remove deployment shortcut** — Zmienia pasek akcji na karcie VM w Moje zasoby. - usuwa przycisk Wdrożenie z karty VM; - dodaje przycisk Usuń obok Odtwórz od zera; - dla VM zarządzanej przez Terraform używa istniejącego POST /deployments/{id}/destroy;
- **#248 [MERGED] Fix Proxmox snapshot capability detection and fail-fast UI** — Tworzenie snapshotu mogło zostać wysłane do Proxmox mimo że sama VM/storage nie obsługiwała snapshotów. Operacja kończyła się dopiero jako asynchroniczny task z wynikiem snapshot feature is not available.
- **#249 [MERGED] Fix VM overview progress cards** — Naprawia karty podsumowania VM widoczne na screenie. - nie renderuje paska dla Adres IP / Uptime / Zarządzanie - nie traktuje null jako 0% - ukrywa pasek dysku, gdy brak telemetrii wykorzystania
- **#250 [MERGED] Finish Proxmox snapshot fail-fast handling** — centralny preflight snapshot capability w ProxmoxProvider.create snapshot() preflight obowiązuje również Blueprint workflow i Day-2, nie tylko bezpośredni endpoint HTTP brak podwójnego probe w endpointzie snapshotu Day-2 action catalog używa jednego wyniku capabilities zamiast odpytania Proxmox dla każdej akcji overvie
- **#251 [MERGED] Allow manual acceptance of successful AWX onboarding** — Dodaje ręczne potwierdzenie poprawnego onboardingu AWX dla provisioningów, które zatrzymały się na kroku register awx. Zmiany: - przy błędzie AWX pojawia się akcja Onboarding OK obok Ponów - akcja wymaga potwierdzenia i działa wyłącznie dla nieudanego kroku register awx
- **#252 [MERGED] Add list view to Products catalog** — dodaje przełącznik Kafelki / Lista w katalogu Produkty zapisuje preferowany widok użytkownika w localStorage widok listy korzysta z istniejącej tabeli, więc zachowuje wyszukiwanie, sortowanie, paginację i ustawienia kolumn pokazuje produkt, opis, wersję, template, provider, approval oraz akcję Utwórz VM dodaje responsy
- **#253 [MERGED] Rebuild Policy Engine scope assignment UI** — hierarchiczny wybór Global / Organizacja / Projekt wybór organizacji i projektu z zakresów dozwolonych przez RBAC checkboxy APMID i ENV zamiast ręcznego JSON pola Akcje, Typy zasobów i opcjonalny scope key podgląd efektywnego zakresu zwijany tryb zaawansowany JSON dla dodatkowych selektorów zapis do właściwego projektu
- **#254 [MERGED] Harden Blueprint workflow retry, approvals and Direct Proxmox safety** — bezpieczny Retry/Resume po checkpointach providera zachowuje workflow runtime po wykonanym apply/clone usuwa recreate po wykonanym provider mutation nie powtarza zakończonych Ansible/AWX Direct Proxmox wznawia zapisany UPID/VMID zamiast klonować od zera rozdzielenie approval approval = job-level / Policy Engine workflo
- **#255 [MERGED] Rebuild Blueprints around explicit workflows** — usunięto implicit Terraform apply i compatibility Ansible fallback Terraform/OpenTofu wymaga dokładnie jednego jawnego terraform apply Direct Proxmox wymaga dokładnie jednego clone vm i nie przyjmuje Terraform plan/apply usunięto pseudo-kroki workflow generate hostname, allocate ip, create vm Ansible w Blueprintach ma 
- **#256 [MERGED] fix(updater): recover update checks from GitHub 5xx and legacy markers** — dodaje retry/backoff dla przejściowych błędów GitHub API 429/5xx i błędów sieciowych w updaterze traktuje legacy/non-Git commit sha (np. archive SHA-256) jako nieznaną wersję lokalną zamiast wysyłać niepoprawne porównanie do GitHub odzyskuje także sytuację, gdy syntaktycznie poprawny SHA nie istnieje już w repozytorium
- **#257 [MERGED] Add enable/disable buttons for blueprints** — Dodaje bezpośrednią akcję Włącz/Wyłącz na liście Blueprintów. - używa istniejącego PUT /blueprints/{id}/enabled - respektuje blueprints.update oraz role zarządzające Blueprintem - przekazuje aktualny tenant/projekt przez nagłówki scope
- **#258 [MERGED] Fix Blueprint RBAC for tenant and project roles** — Blueprint RBAC was inconsistent across the creation wizard, API authorization, UI management actions and worker reauthorization. The persisted tenant/project scope was already enforced by app.resource scope.http.require(...), but Blueprint-specific ACL rules only considered global roles/users in several places. Scoped 
- **#259 [MERGED] feat(installer): add Kubernetes installation mode** — Nazwa domeny/modułu: installer / deployment runtime
- **#260 [OPEN] Restructure Blueprint VM metadata and Cloud-init access** — blueprints / kreator Blueprintu
- **#261 [OPEN] Return to Blueprint list after delete** — Po usunięciu Blueprintu UI przechodzi jawnie na trasę listy Blueprintów zamiast tylko lokalnie odświeżać aktualny widok.
- **#262 [OPEN] Align Blueprint advanced form fields** — - wyrównuje pola Slug i Sposób tworzenia VM w sekcji opcji zaawansowanych kreatora Blueprintu - zapobiega rozciąganiu krótszego pola przez wysokość sąsiedniego pola z tekstem pomocniczym - dodaje test CSS .advanced-options-body.form-grid { align-items: start; }
- **#263 [OPEN] fix(workflow): repair direct Proxmox clone step** — Direct Proxmox provisioning failed immediately on clone vm with: job.failed: Workflow step clone (clone vm) failed. Root cause: PR 255 removed BLUEPRINT PRECOMPILED STEPS, but run proxmox blueprint workflow() still referenced that deleted symbol before executing clone vm. Runtime raised NameError, which was then wrappe
- **#264 [OPEN] Allow Blueprint manager roles to be reused** — Creating another Blueprint could show „Brak dostępnych pozycji” under Role zarządzające Blueprintem . The cause was a one-to-one restriction between an RBAC role and a Blueprint: UI removed roles already used by another Blueprint, API returned 409 when the same role was reused, the database had a unique constraint on b
- **#265 [OPEN] Show real Proxmox clone status during Terraform provisioning** — W widoku Moje zasoby provisioning Terraform pokazywał tylko Terraform apply, mimo że provider wykonywał w tym czasie pełne klonowanie VM w Proxmox. Dodatkowo brak wartości progress percent był konwertowany przez JS z null na 0, więc UI pokazywał fałszywe 0%.

---

# 4. Procedura przy nowej regresji

Jeżeli nowy błąd przypomina którykolwiek wpis wyżej:

1. Nie naprawiaj tylko objawu w UI.
2. Zidentyfikuj źródło prawdy i pełny lifecycle danych.
3. Sprawdź równolegle UI, API, service/domain, worker, provider/executor, DB/state i testy.
4. Dodaj test odtwarzający konkretną regresję przed/razem z poprawką.
5. Sprawdź retry, cancel, worker-loss, duplicate request i partial failure.
6. Sprawdź scope/RBAC ponownie w workerze, nie tylko przy przyjęciu requestu.
7. Dla zewnętrznej mutacji zapisz checkpoint/idempotency marker przed uznaniem kroku za bezpieczny do retry.
8. Po zmianie kontraktu wykonaj repo-wide search dla starych pól, symboli i compatibility branchy.
9. Uaktualnij ten changelog, jeżeli problem wnosi nową regułę lub zmienia istniejącą.
10. Nie scalaj znanego failing/incomplete PR tylko po to, aby zamknąć zadanie.

# 5. Krytyczne obszary do ponownej kontroli przy każdej większej zmianie

- Blueprint create/edit/execute/retry/delete/enable-disable.
- Terraform apply/destroy/recovery po utracie workera.
- Direct Proxmox clone i jego checkpointy.
- Tenant/project-scoped RBAC oraz worker reauthorization.
- APMID/ENV: statyczne vs wybierane przy execute.
- Inventory refresh/missing/purge/repair.
- QEMU Guest Agent i Cloud-init first boot.
- AWX onboarding i Ansible ordered runs.
- Updater systemd/Docker oraz candidate runtime preflight.
- Installer systemd/Docker/Kubernetes i zachowanie danych.
- Alembic upgrade z poprzedniej wersji.
- noVNC HTTP assets + WebSocket.
- Bulk Day-2 z częściowymi błędami.
- UI po zakończeniu joba, przy pollingu i po refresh strony.
