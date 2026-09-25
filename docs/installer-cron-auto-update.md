# Automatyczne aktualizacje przez cron

`install.sh` może skonfigurować systemowy cron dla **istniejącej instalacji** Cloudportal. Funkcja jest opcjonalna i domyślnie wyłączona. Nie zmienia zadań w crontabach innych aplikacji ani użytkowników.

## Włączenie

Najpierw zainstaluj lub zaktualizuj Cloudportal standardowo. Następnie uruchom aktualny `install.sh` na hoście aplikacji. W Dockerze chodzi o host Docker, nie kontener API.

Dla instalacji Docker:

```bash
sudo bash install.sh --docker --enable-auto-update --auto-update-interval 12
```

Dla instalacji natywnej systemd:

```bash
sudo bash install.sh --enable-auto-update --auto-update-interval 12
```

Pominięcie `--auto-update-interval` oznacza 12 godzin. Obsługiwane interwały to **1, 2, 3, 4, 6, 8, 12 i 24 godziny**. Ograniczenie do dzielników doby zapobiega nierównym odstępom przy przejściu przez północ, które występowałyby np. dla `*/5`.

Domyślna reguła to `0 */12 * * *`: uruchomienia o **00:00 i 12:00 czasu serwera**, nie 12 godzin od momentu konfiguracji. Dla 24 godzin używane jest `0 0 * * *`. Obowiązują standardowe zasady lokalnego crona dotyczące zmiany czasu i wyłączonego hosta; pominięte terminy nie są nadrabiane.

Włączenie lub zmiana interwału nie uruchamia aktualizacji natychmiast i nie reinstaluje aplikacji. Jest to osobny tryb instalatora; nie łącz go z `--status`, `--uninstall`, recovery ani `--gui`. Zapisany kanał `ref`, poświadczenia GitHub, port, host i liczba workerów pozostają ustawieniami istniejącego updatera. Parametry instalacji, np. `--ref`, nie zmieniają kanału w tym trybie — zmień go w konfiguracji updatera.

Uruchomienie instalatora bez argumentów udostępnia także opcje menu 11–14: włączenie dla systemd, włączenie dla Dockera, wyłączenie i status.

## Jak działa aktualizacja

Cron wywołuje lokalnego klienta, który uwierzytelnia się do istniejącej usługi `cloudportal-updater.service` i zleca `/run`. Dla systemd używany jest adres loopback `127.0.0.1:8766`, a dla Dockera socket Unix `/run/cloudportal-updater-docker/updater.sock`. Nie jest otwierany dodatkowy port sieciowy.

Updater nadal sprawdza dostępność nowszej wersji i używa swoich dotychczasowych ustawień kontroli CI, backupu oraz weryfikacji kandydata. Cron nie omija tych mechanizmów. Brak nowej wersji nie powoduje ponownej instalacji. Wyłączone wcześniej kontrole bezpieczeństwa nie są samoczynnie włączane przez konfigurację crona.

Aby nie działały dwa niezależne harmonogramy, włączenie crona ustawia `enabled=false` w wewnętrznym harmonogramie updatera przez jego API. **Usługa updatera pozostaje aktywna**. W panelu wewnętrzne automatyczne sprawdzanie może więc wyglądać na wyłączone mimo aktywnego crona; źródłem informacji o cron jest `--auto-update-status`. Nie włączaj jednocześnie wewnętrznego harmonogramu w panelu, jeśli zadaniami ma zarządzać cron.

Klient pomija termin, gdy blokada instalatora jest zajęta; updater nie przyjmuje drugiego równoległego zadania. Instalator uruchomiony przez updater nie przejmuje siłą blokady innego instalatora. Brak aktywnej instalacji nie powoduje jej odtworzenia od zera. Przy aktualizacji należy liczyć się z restartem usług aplikacji.

## Status, logi i wyłączenie

Status samego harmonogramu, bez naprawiania ani zmieniania aplikacji:

```bash
bash install.sh --auto-update-status
```

Status crona jest także widoczny w standardowym `--status` i `--docker --status`. Ten ostatni zachowuje swoje dotychczasowe zachowanie auto-naprawy; do samej diagnostyki stacka użyj `--docker --status --no-auto-repair`.

Log przyjęcia lub pominięcia zleceń:

```bash
sudo tail -n 100 /var/log/cloudportal-auto-update.log
```

Przyjęcie zlecenia **nie jest potwierdzeniem udanej instalacji**. Końcowy wynik i postęp sprawdzaj w panelu aktualizacji oraz w usłudze updatera:

```bash
sudo journalctl -u cloudportal-updater.service -n 100 --no-pager
```

Wyłączenie jest jednakowe dla obu sposobów instalacji:

```bash
sudo bash install.sh --disable-auto-update
```

Usuwane są tylko pliki harmonogramu, klienta, wrappera oraz reguły logrotate Cloudportal. Historia logów pozostaje. Wewnętrzny harmonogram updatera nie jest automatycznie ponownie włączany. Wyłączenie harmonogramu nie anuluje już rozpoczętej aktualizacji. Deinstalacja Cloudportal również usuwa zadanie cron, po potwierdzeniu operacji przez użytkownika.

## Pliki i wymagania

- `/etc/cron.d/cloudportal-auto-update` — pojedynczy wpis zadania, root:root 0644; ponowne włączenie atomowo zastępuje wpis zamiast dodawać duplikaty.
- `/usr/local/sbin/cloudportal-auto-update` i `/usr/local/lib/cloudportal-updater/cron-update.py` — klient wywołujący lokalny updater; uruchamiany jako root.
- `/var/log/cloudportal-auto-update.log` i `/etc/logrotate.d/cloudportal-auto-update` — log 0600 i rotacja tygodniowa z maksymalnie ośmioma archiwami. Token nie trafia do polecenia crona, argumentów procesu ani logu klienta; wyjście nie zawiera ANSI.

Konfiguracja wymaga gotowej instalacji, działającego systemd i updatera oraz Pythona używanego przez instalator. Na Debianie/Ubuntu instalowane są brakujące pakiety `cron` i `logrotate`, na RHEL `cronie` i `logrotate`. Współdzielona usługa cron jest włączana, ale nie jest wyłączana podczas usuwania zadania Cloudportal. Konfiguracja odmówi zapisu przez dowiązania symboliczne w swoich plikach docelowych.

Na jednym hoście zarządzany jest jeden harmonogram Cloudportal, zgodnie z jedną wspólną usługą updatera. Ponowne włączenie z `--docker` lub bez tej flagi wybiera odpowiednią istniejącą instalację.
