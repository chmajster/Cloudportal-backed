# Cloudportal-backed — integracja scope zasobów

Status: lokalny patch, wymagający przeglądu i dalszej walidacji. Nie opublikowano
nowego PR, nie zaktualizowano istniejących PR i nie wykonano merge do `main`.
Bieżące narzędzia GitHub w tej sesji udostępniały wyłącznie odczyt.

## Summary

Kontynuacja subsystemu governance na podstawie faktycznie zmergowanej implementacji
Projects z PR #119. Bazą jest `main` z commita
`0dec12c7b4700ab187b9cfb27e1f45866d3ae391`; jego drzewo plików
`d47b99cc817407c34f423ce745abfa621425e986` odtworzono i porównano lokalnie.
Lokalna gałąź: `feat/resource-scope-integration`.

**Nie jest to ukończenie całej specyfikacji 76 sekcji.** Patch wprowadza trwałe
przypisanie istniejących zasobów do Tenant/Project oraz kontrolę tego przypisania
w wybranych istniejących ścieżkach HTTP, SQL, workerów i synchronizacji.
Nie używa alternatywnej, konfliktującej implementacji Projects z PR #120.
Żaden istniejący PR nie został zmieniony przez tę dostawę.

## Architecture

Nowy moduł `app/resource_scope/` obejmuje modele przypisań, kolumny i ograniczenia
własności, autoryzację, sesję SQLAlchemy, zależność HTTP, serwisy i weryfikację
konsoli. `app/modules/feature_resource_scope.py` rejestruje cienki router.

Istniejące endpointy nie zostały zastąpione drugim API provisioningowym.
Wykorzystano obecne uwierzytelnianie, katalog ról i permissions, ograniczenia
tokenów, blokadę governance, idempotency, jobs oraz audit/event broker.
Kod domenowy nie trafił do `app/main.py`, `app/web/core.js` ani `app/web/app.js`.

## Tenant/Project model

Kolumny `tenant_id` i `project_id` dodano do dziesięciu istniejących tabel:
`deployments`, `jobs`, `managed_vms`, `managed_resources`, `blueprints`,
`ip_pools`, `ip_allocations`, `hostname_schemes`, `hostname_reservations`,
`scheduled_operations`.

Dane zastane są przypisywane do Default/Default. Złożone klucze obce kontrolują
parę tenant/projekt oraz wybrane relacje rodzic–dziecko. Jobs dziedziczą projekt
deploymentu; zmiana nagłówków późniejszego żądania nie przenosi istniejącego joba.
Usunięcie projektu z zasobami, historią lub przypisaniami infrastruktury jest
blokowane. Retencja historii i audytowane przenoszenie zasobów pozostają otwarte.

## Scoped RBAC

Istniejące permissions zasobów mogą należeć do ról tenantowych i projektowych.
Nie stają się uprawnieniami globalnej administracji tożsamością. Dostęp uwzględnia
bieżący stan konta, tokenu, rodzica, członkostwa i rolę ograniczoną zapisanym
pułapem delegacji. Wyłączony scope jest niedostępny, a zawieszony odrzuca zmiany.

Kontekst zasobów podaje się parą nagłówków `X-Tenant-ID` / `X-Project-ID` lub parą
parametrów `tenant_id` / `project_id`. Ich sprzeczność, powtórzenie, niekompletność
lub nieprawidłowy UUID powoduje błąd. Brak obu oznacza Default/Default, nie
uprawnienie do dowolnego projektu. Kontrakt jest widoczny w OpenAPI.

**Ograniczenie wdrożenia:** wykonywanie operacji zasobowych poza Default wymaga
jawnego globalnego `governance.admin` oraz właściwego permission operacji.
Delegowane wykonywanie operacji w Default także jest blokowane, gdy istnieją
zasoby w innych projektach. Zwracany kod to
`PROJECT_EXECUTION_REQUIRES_PLATFORM_ADMIN`.
Nie usuwać tej blokady przed ukończeniem kontroli natywnych celów providerów,
obrazów, sieci i Ansible oraz ich testów. To ograniczenie, a nie ukończona
obsługa samoobsługowego provisioningu wielodostępnego.

## Quotas and reservations

Niewykonane. Nie ma ledgeru usage, atomowych rezerwacji quota/capacity, delty resize
ani reconciliation tych rezerwacji. Scenariusz limit=10, used=9 i 10 równoległych
żądań nie jest realizowany przez ten patch. Nie dodano pozornych liczników w UI.

## Placement

Niewykonane. Brak nowego silnika constraints/scoring/capacity/simulation.
Filtrowanie providerów po przypisaniach nie jest silnikiem placement.

## Leases

Niewykonane. Brak lease defaults/maxima, przedłużeń i obsługi expiry.
Zmiany istniejącego schedulera dotyczą kontroli scope przy tworzeniu joba,
nie implementacji nowego cyklu życia lease.

## Policy Engine

Niewykonane. Patch nie dodaje deklaratywnego silnika polityk, wersjonowania decyzji,
symulacji, explain, obligations ani integracji tych elementów z approvalami.
Dotychczasowe guardy pozostają; kontrola scope nie zastępuje policy admission.

## Provisioning integration

Istniejące moduły zasobowe używają zweryfikowanego scope. Sesja dodaje kryteria do
zapytań ORM przed paginacją i ogranicza zasoby po identyfikatorze, logi jobów,
Terraform state/plan oraz powiązania providerów i credentiali. Chroniony jest też
skrót przez identity map i wybrane operacje zbiorcze; surowe SQL/Core przez związaną
sesję jest odrzucane. Niezwiązana sesja pozostaje uprzywilejowanym narzędziem
wewnętrznym — nie jest to PostgreSQL RLS dla dowolnego klienta SQL.

Nowe endpointy:

```
GET /api/v1/projects/{project_id}/infrastructure-access
PUT /api/v1/projects/{project_id}/infrastructure-access
```

GET zwraca paginowane identyfikatory przypisanych providerów i credentiali.
PUT wymaga globalnych `governance.admin` i `governance.access.manage`,
`expected_version` oraz istniejących identyfikatorów. Lista credentiali musi
zawierać credential każdego providera. PUT zastępuje kompletną listę, nie dodaje
elementów do niej; limit wynosi 200 identyfikatorów każdego typu. Nie wolno
traktować pojedynczej strony GET jako kompletnej listy przy jej zastępowaniu.
Sekrety nie są kopiowane do projektów ani zwracane przez ten endpoint.

Worker używa zapisanego scope joba, ponownie sprawdza uprawnienia i przypisania
przed executorem oraz przy okresowych kontrolach. Token musi należeć do twórcy
joba. Cofnięcie dostępu nie blokuje zapisania statusu błędu i cleanupu.
Test ścieżki sukcesu używa zastępczego executora Terraform, nie realnego providera.

Idempotentne odtwarzanie odpowiedzi sprawdza dostęp do jej zasobów i referencji.
Fingerprint poza Default zawiera scope. Stary fingerprint Default zachowano,
a stare odpowiedzi deployment/job otrzymują wymagane pola scope bez zmiany
zapisanego statusu i identyfikatorów.

## Day-2 integration

Bezpośrednie synchroniczne endpointy VM/task/restore Proxmox są ograniczone do
Default i odrzucane na providerze mającym zasoby innych projektów, również dla
administratora. Nie są jeszcze pełnym, kontrolowanym przez policy/quota
subsystemem Day-2 dla wielu projektów.

Listy VM z providera są filtrowane po przypisaniu natywnych identyfikatorów.
Na współdzielonym providerze ukrywane są także VM bez ustalonego przypisania,
w tym podczas przerwy między utworzeniem a rejestracją w inventory.
Import/synchronizacja i opóźniony rezultat zadania nie mogą przejąć znanej VM
innego projektu. Rekord zadania jest potwierdzany w Redis po zatwierdzeniu DB;
nieudane rekordy reconciliation pozostają do ponowienia.

Konsola przechowuje aktora, token i scope. Ładowanie jej zasobów oraz otwarcie
WebSocket wymagają ponownej autoryzacji; aktywne połączenie kontroluje dostęp co
pięć sekund. Stare capability bez informacji o scope/aktorze wygasają.
Nie odwraca to operacji, którą provider zdążył już przyjąć.

## Security

Nowe testy obejmują obce identyfikatory deploymentów/jobów/logów, niezgodne
referencje rodzic–dziecko, skrót identity-map, filtrowanie przed limitem,
nieprawidłowe pary kontekstu, token ceilings, cofnięcie przypisań, blokadę executora,
przejmowanie inventory, idempotentny replay i nieaktualne wersje allowlist.

Globalne API audit/events/metrics pozostają globalne. Brak tu pełnej projektowej
historii zdarzeń, separacji statycznego katalogu obrazów/playbooków, kontroli
wszystkich zewnętrznych consumerów oraz pełnego przeglądu bezpieczeństwa.
Dotychczasowe dostosowania ról i pułapy istniejących tokenów nie są automatycznie
rozszerzane. Nowe uprawnienia muszą być nadane jawnie.

## Database migrations

Nowa migracja: `b752ca806e19_resource_scope.py`, parent `9a42d10e63bc` z PR #119.
Nie zmieniono zmergowanych migracji. Test migracji w SQLite sprawdza zachowanie
zastanych danych, ponowienie upgrade, przypisania Default, ograniczenia
referencyjne i downgrade. Testy relacyjne jawnie włączają FK w SQLite.

Downgrade odmawia spłaszczenia danych/przypisań należących do innych projektów.
Przy samym Default usuwa tylko nowe kolumny/ograniczenia/tabele przypisań;
nie usuwa istniejących zasobów ani tabel Tenant/Project.

W tej sesji nie uruchomiono serwera PostgreSQL. Wyniki wcześniejszych CI PR #119
nie są wynikami nowej migracji. Rzeczywiste blokady PostgreSQL, równoległe operacje
i wydajność wymagają osobnego uruchomienia przed wdrożeniem.

## UI

Nie dodano Project Switchera ani nowych ekranów governance. Istniejąca
przeglądarka działa w Default, dopóki klient nie wysyła jawnego kontekstu API.
Nie wykorzystano localStorage jako autorytatywnego źródła permissions.
Przełączanie projektu, unieważnianie wyników wcześniejszych żądań oraz przegląd
przypisań providerów/credentiali w UI pozostają niewykonane.

## Tests

Wyniki końcowego zestawu dla niezmienianego podczas testów kodu:

| Sprawdzenie | Wynik |
| --- | --- |
| Pełne pytest, 357 przypadków | **349 zaliczonych, 7 pominiętych, 1 błąd**, 7 ostrzeżeń |
| Wszystkie nowe testy resource_scope | **24 zaliczone**, bez błędów i pominięć |
| Podział nowych przypadków | 11 HTTP/API/worker, 12 ORM/DB, 1 migracja |
| Kontrola granic modułów | Zaliczone: 24 UI features, 28 routes, 8 extensions, 13 backend descriptors |
| Składnia wszystkich plików JavaScript | Zaliczone |
| Python compileall | Zaliczone |
| Kontrola whitespace patcha | Zaliczone |

Pełny zestaw zakończył się kodem błędu z powodu jednego przypadku:
`tests/test_event_broker_enterprise.py::test_consumer_detects_gap_after_retention_while_disabled`.
Po usunięciu zdarzeń SQLite ponownie nadaje wartość sekwencji 1; asercja
`newer['sequence'] > 1` kończy się `assert 1 > 1`.

Ten sam przypadek **ponownie odtworzono na czystym drzewie bazowym**
`d47b99cc817407c34f423ce745abfa621425e986`: 1 błąd z identyczną asercją.
Wcześniejszy pełny zestaw czystej bazy miał 325 zaliczonych, 7 pominiętych i ten
sam 1 błąd. Nowy patch nie usuwa ani nie pomija tego testu. **Pełny zestaw nadal
nie jest zielony**; w wykonanych testach nie wystąpiły nowe niepowodzenia.

Pominięcia: sześć testów wymagających prawdziwego PostgreSQL (tenancy, Projects,
idempotency concurrency) oraz jeden test integracji HTTP z oddzielnym portalem
PHP, wymagający `PORTAL_PATH`. Testy nowych mechanizmów wykonano na SQLite/Redis;
nie jest to potwierdzenie zachowania blokad PostgreSQL.

Archiwum dostawy zawiera pełny log i JUnit XML końcowego uruchomienia, log pełnej
bazy oraz niezależny log/JUnit odtworzenia jej błędu. Po końcowym uruchomieniu
zmieniano wyłącznie dokumentację i metadane Git, nie kod aplikacji ani testów.

Końcowe polecenia:

```sh
python scripts/check-module-boundaries.py
find app/web -type f -name '*.js' -print0 | xargs -0 -n1 node --check
python -m compileall -q app migrations tests/resource_scope
pytest -q --tb=short --junitxml=/mnt/data/resource-scope-verified.xml
git diff --cached --check
```

Testy uruchomiono Pythonem 3.13 w izolowanym środowisku `/mnt/data/cpvenv`,
z pakietami offline pobranymi z istniejącego artefaktu CI oraz lokalnym Redis.
Dla pytest użyto `REDIS_SERVER_BINARY=/mnt/data/governance-runtime/run-redis`.
Brak nowego uruchomienia GitHub Actions, testu Docker build i walidacji Terraform
CLI w tej sesji. Nie przypisujemy sobie wyników CI wcześniejszych commitów.

## Manual verification

Nie wykonywano operacji na prawdziwym Proxmox/VMware/cloud ani pełnego browser E2E.
Nie zrealizowano końcowego scenariusza Engineering/Production: quota, lease 30 dni,
placement, policy-denied resize 8→32 CPU, approved destroy i zwolnienie usage po
potwierdzeniu providera. Ukończenie migracji/scope nie spełnia tego kryterium.

## Shared files touched

Zmodyfikowane istniejące pliki (w tym testy i dokumentacja):

```text
app/access.py
app/api/automation.py
app/api/common.py
app/api/infrastructure.py
app/api/inventory.py
app/api/ipam.py
app/api/operations.py
app/api/outputs.py
app/api/proxmox_management.py
app/database.py
app/inventory_sync.py
app/jobs/worker.py
app/models.py
app/operations/service.py
app/projects/authorization.py
app/projects/permissions.py
app/projects/service.py
app/providers/task_reconcile.py
app/rbac/service.py
app/tenancy/permissions.py
docs/roadmap/governance.md
migrations/env.py
tests/projects/test_projects_migration.py
tests/projects/test_projects_unit.py
tests/tenancy/test_tenancy_migration.py
tests/tenancy/test_tenancy_unit.py
tests/test_module_architecture.py
```

Nowe pliki należą do `app/resource_scope/`, `tests/resource_scope/`, nowej migracji,
deskryptora modułu, wspólnego helpera testów migracji i dokumentacji dostawy.

Zmiany starszych testów migracji służą odczytowi historycznej struktury tabel
przez reflection zamiast bieżącego ORM z nowymi kolumnami. Zachowano asercje
integralności danych. Fixtures permissions obejmują rozszerzony katalog.
Nie usunięto asercji istniejącego błędu brokera i nie pominięto go w całym zestawie.

## Zastosowanie dostawy

Patch wymaga bazy z PR #119, nie alternatywnej migracji Projects z PR #120.
Przykład lokalnego odtworzenia zmian z dołączonego pliku:

```sh
git switch -c feat/resource-scope-integration 0dec12c7b4700ab187b9cfb27e1f45866d3ae391
git apply --check /sciezka/cloudportal-resource-scope.patch
git apply /sciezka/cloudportal-resource-scope.patch
```

Przed zastosowaniem do innego commita trzeba rozwiązać różnice względem wskazanej
bazy. Archiwum source zawiera wyłącznie pliki śledzone, bez `.git`, środowiska
uruchomieniowego, testowych baz, kluczy runtime i pakietów zależności.
Historia lokalnego commita bazowego została odtworzona z archiwum — jej SHA nie
jest SHA z GitHub, ale drzewo plików jest identyczne. Patch opiera się na zmianach
plików względem wskazanego zdalnego drzewa, nie na zastąpieniu historii `main`.

## Remaining work

1. **Dokończyć scope execution:** natywne identyfikatory obrazów/VM/network,
   dowolne cele Ansible, entitlements statycznego Catalog/playbooków, adoption
   i przenoszenie zasobów oraz wszyscy consumerzy/schedulerzy. Właściciele:
   `app/resource_scope/`, `app/jobs/`, `app/providers/`, moduły Blueprint/Catalog,
   inventory i API. Dopiero po tych kontrolach usuwać rollout gate.
2. **PostgreSQL:** rzeczywisty upgrade istniejącej bazy, konkurencyjna zmiana
   członkostwa/przypisań i start joba, spójna kolejność blokad, rygorystyczna
   walidacja referencji oraz parametry obciążenia. Nowy CI dla końcowego patcha.
3. **Quota/usage/reservations:** model wymiarów i księgowania, atomowe rezerwacje,
   delta resize, reconciliation i zwolnienie dopiero po potwierdzonym wyniku.
   Zachować obowiązkowy test dziesięciu żądań walczących o ostatnią wolną VM.
4. **Leases/placement/policies:** kompletne domeny, a potem jeden pipeline
   provisioning/Blueprint/Catalog/Day-2 z dotychczasowymi approvalami i jobs.
5. **UI i obserwowalność:** Project Switcher, ekrany przypisań, quota, lease,
   placement, policy oraz projektowe audit/events/metrics i pełne browser E2E.
6. **Odbiór całości:** końcowy scenariusz Engineering/Production na realnym
   providerze, test awarii i odmów oraz przegląd bezpieczeństwa przed uznaniem
   całej specyfikacji za ukończoną.

Architektura i kontrakty: `docs/architecture/resource-scope.md`.
Plan całości: `docs/roadmap/governance.md`.
