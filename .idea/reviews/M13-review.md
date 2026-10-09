# Gesamt-Review M13 – Release 0.1

Datum: 2026-10-09. Fünf unabhängige Reviews durch separate Agenten, parallel, nur lesend (das
Image-Review hat das Runtime-Image zusätzlich gebaut und mit eigenen Containern geprüft). Geprüfter
Stand: `main` nach dem Merge von Phase A (Commit `80bd199`, PR #28). Aufbau wie
`M4-security.md`: je Bereich eine Tabelle der Befunde, dann die Befunde mit Fundstelle und
Behebung, dann „Geprüft und in Ordnung“. Pfade relativ zu `backend/src/papiq/` bzw. `web/`,
wie im jeweiligen Abschnitt angegeben.

## Zusammenfassung

Kein Befund der Schwere hoch im Backend, im Image oder in der Architektur; ein hoher Befund in der
Web-UI (Verhalten, keine Sicherheitslücke). Die Zugriffskontrolle (Dokumente, Schubladen,
Regeln, Webhooks, Admin-Vollzugriff, M12-Erweiterungen), Sitzungen, CSRF, Drosseln, OIDC, MCP,
Signaturen und Geheimnisse der Webhooks, die Rechte im Container und die Trennung von Kern,
Ports und Adaptern sind in Ordnung. Behoben vor dem Merge (Phase B, mit Tests, die vorher
fehlschlagen): alle Befunde der Schwere hoch und mittel, soweit sie nach Prüfung Bestand hatten
(zwei Architektur-Befunde wurden nach Prüfung auf niedrig gesetzt, Begründung unten), dazu
Korrekturen an der Dokumentation. Alle niedrigen Befunde stehen als Vorschlag unter „Nach 0.1“
im Umsetzungsplan; was davon kommt, entscheidet Tobi (Entscheidung 9 in `prompts/M13.md`).

| Nr. | Bereich | Schwere | Befund | Status |
| --- | --- | --- | --- | --- |
| 1-01 | API | mittel | Admin-API-Token befördert ein anderes Konto zum Admin (`PATCH /users/{id}`), damit entsteht eine Admin-Sitzung aus einem Token; es kann auch alle anderen Admins deaktivieren | behoben: Rollenwechsel und Zustand von Admins nur mit Sitzung; `bootstrap_admin` legt wieder einen Admin an, wenn alle inaktiv sind |
| 1-02 | API | niedrig | OIDC-Verknüpfung ohne Passwort als Persistenz einer gekaperten Sitzung | Nach 0.1 (Entscheidung wie M4-05) |
| 1-03 | API | niedrig | `channel=migration` darf jeder Nutzer setzen (Regel-Bedingung fälschbar) | Nach 0.1 |
| 1-04 | API | niedrig | Personenbezogene API-Antworten außerhalb `/auth` ohne `Cache-Control: no-store` und `nosniff` | Nach 0.1 |
| 1-05 | API | niedrig | Webhook-Test als Portscanner im internen Netz (Status, Fehlerart, Dauer) | Nach 0.1 (Sperrliste oder knappere Rückmeldung für Nicht-Admins) |
| 1-06 | API | niedrig | Zahl offener Ereignisströme je Nutzer unbegrenzt | Nach 0.1 |
| 1-07 | API | niedrig | OIDC-Callback mit `error` lässt das Flow-Cookie stehen | Nach 0.1 (eine Zeile) |
| 1-08 | API | niedrig | Konto-Drossel sperrt richtige Anmeldungen (gezielte Aussperrung möglich); Kommentar widerspricht | akzeptiert (M4); Kommentar und README anpassen: Nach 0.1 |
| 2-01 | Egress | niedrig | Meilisearch ohne Master-Key ist über einen Webhook beschreibbar; Papiq warnt nicht | Nach 0.1 (Warnung ohne Schlüssel, toleranter Abgleich) |
| 2-02 | Egress | niedrig | OIDC-Discovery: Endpunkt-URLs nicht auf `https` geprüft | Nach 0.1 |
| 2-03 | Egress | niedrig | `PAPIQ_LLM_TIMEOUT` wirkt je Socket-Operation, nicht je Anfrage | Nach 0.1 |
| 2-04 | Egress | niedrig | Webhook-Test antwortet 500 statt Protokolleintrag bei unentschlüsselbarem Geheimnis | Nach 0.1 |
| 3.1 | Image | mittel | `init-papiq` lässt `..` in den Datenpfaden durch: `/data/../etc` wird samt `passwd` und `shadow` an PUID:PGID übergeben | behoben: `.`/`..` abgelehnt, `tmp` und `var` in der Systemordner-Liste, Prüfung in `test-image.sh` |
| 3.2 | Image | niedrig | `var`, `tmp` fehlten in der Systemordner-Liste | behoben (mit 3.1) |
| 3.3 | Image | niedrig | README: Dev-Schlüssel „wird abgelehnt“ gilt nur mit `PAPIQ_COOKIE_SECURE=true` | behoben (Doku) |
| 3.4 | Image | niedrig | Setuid-Programme aus Debian im Runtime-Image | Nach 0.1 |
| 3.5 | Image | niedrig | `VOLUME /data` erzeugt anonyme Volumes bei Postgres/S3-Stacks | Nach 0.1 |
| 3.6 | Image | niedrig | `test-image.sh`: Lücken (Geheimnisse in `docker inspect`, beschäftigtes Stoppen, `degraded`); Anzahl 42, nicht 31 | Nach 0.1 (Zahl in `umsetzungsplan.md` korrigiert) |
| 3.7 | Image | niedrig | CI-Actions per Major-Tag, nicht per SHA | Nach 0.1 |
| 3.8 | Image | niedrig | Basis-Images per Tag, nicht per Digest; `curl … \| bash` in der Dev-Stufe | Nach 0.1 |
| 3.9 | Image | niedrig | Docker-outside-of-Docker und `GH_TOKEN` im Devcontainer undokumentiert | behoben in Phase C (Devcontainer-Doku) |
| 3.10 | Image | niedrig | `/opt/papiq/venv/.lock` 0666 | Nach 0.1 |
| A-01 | Architektur | niedrig (vom Agenten: mittel) | MCP-Adapter importiert `rest.context`, `rest.problems`, `rest.schemas`; `rest.app` bindet `mcp` ein | so gewollt: MCP läuft laut `architektur.md` im API-Prozess und teilt die Service-Schicht und die JSON-Formen; kein Vertrag verletzt. Nach 0.1: Vertrag „Inbound-Adapter untereinander“ ausweisen |
| A-02 | Architektur | niedrig (vom Agenten: mittel) | Regel-Muster werden im Kern mit `re` geprüft, mit `regex` ausgeführt | ohne Sicherheitsfolge: `regex` nimmt jedes gültige `re`-Muster, Abweichungen melden sich als 422 bzw. `ValidationError` beim Lauf. Nach 0.1: Prüfung über den `PatternMatcher`-Port |
| A-03 | Architektur | niedrig | Outbound→Outbound-Importe (`docling`, `ocrmypdf` → `pdfium`, `system`) ohne Vertrag | Nach 0.1 |
| A-04 | Architektur | niedrig | `PatternMatcher` ohne In-Memory-Adapter; `MissingKeyCipher` in `composition` | bekannt (seit M8); Nach 0.1 |
| A-05 | Architektur | niedrig | Fehlende Indizes für Listenfilter und Aufräumabfragen | Nach 0.1 (mit dem Echtlauf messen) |
| A-06 | Architektur | niedrig | Kein Test, dass jede `DomainError`-Unterklasse eine HTTP-Zuordnung hat | Nach 0.1 |
| A-07 | Architektur | niedrig | `architektur.md` veraltet (Kontakt/Typ optional, Worker indexiert, Ports-Tabelle) | behoben (Doku) |
| A-08 | Architektur | niedrig | `README.md`: „(owner)“ an Protokoll, Retry, Reprocess, Löschen; Admins dürfen das auch | behoben (Doku) |
| A-09 | Architektur | niedrig | Kein Meta-Test der Vertragstest-Abdeckung | Nach 0.1 |
| 5.1 | Web-UI | hoch | Jede Navigation startete den Ereignisstrom neu, leerte die Upload-Warteschlange und setzte den Posteingangszähler zurück (Effekt an der Identität von `session.user`) | behoben: `session.load()` behält das Nutzerobjekt, Effekte folgen der Nutzer-ID |
| 5.2 | Web-UI | mittel | Nach einem vom Server geschlossenen Ereignisstrom stieg `generation` nicht; Listen blieben veraltet | behoben |
| 5.3 | Web-UI | mittel | Späte Antworten in Prüfansicht, Verarbeitungsprotokoll und Such-Trefferzahl | behoben |
| 5.4 | Web-UI | niedrig | Upload ohne Abbruch, Größenprüfung, Duplikat-Link; gelöschtes Dokument bleibt „in Verarbeitung“ | Nach 0.1 |
| 5.5 | Web-UI | niedrig | Keine Pluralformen (elf Texte) | Nach 0.1 |
| 5.6 | Web-UI | niedrig | Texte: feste Anführungszeichen, `ms`, pdf.js-Fehler englisch, ungenutzte Schlüssel | Nach 0.1 |
| 5.7 | Web-UI | niedrig | CSP: `img-src data: blob:` von keinem Code gebraucht | Phase C (Prüfung mit JPX-PDF, dann streichen) |
| 5.8 | Web-UI | niedrig | Token-Name 200 statt 100 Zeichen | Nach 0.1 |
| 5.9 | Web-UI | niedrig | Zugänglichkeit: Fokusverlust durch `{#key}`, Fehlertext ohne `role="alert"`, kein „Erneut versuchen“ in der Prüfansicht | Nach 0.1 |
| 5.10 | Web-UI | niedrig | Rechte als Komfort: Webhook-Bearbeiten ungeschützt angeboten, keine Schublade für andere, Freigabe an deaktivierte Nutzer | Nach 0.1 |
| 5.11 | Web-UI | niedrig | Posteingangs-Reiter filtern nur die geladene Seite; `all_users` nicht in der URL | Nach 0.1 |
| 5.12 | Web-UI | niedrig | PDF-Viewer öffnet nicht neu, wenn das Archiv nachkommt | Nach 0.1 |
| 5.13 | Web-UI | niedrig | `minimumReleaseAge` nicht im Repo; kein `pnpm audit` | Nach 0.1 |

**Einordnung der beiden herabgestuften Befunde.** A-01: `architektur.md` legt fest, dass der
MCP-Server im API-Prozess läuft und dieselbe Service-Schicht nutzt; die gemeinsame Nutzung der
JSON-Formen (`rest.schemas`), der Fehlerabbildung und des Kontexts ist Absicht, und `rest.app`
bindet MCP bewusst ein. Die Regel in `CLAUDE.md` betrifft Inbound gegen Outbound. A-02: Die
Validierung mit `re` im Kern (kein Fremdmodul erlaubt) ist strenger oder gleich zu `regex`; ein
in `re` gültiges Muster läuft in `regex`, ein nur in `regex` gültiges wird mit 422 abgelehnt. Ein
zur Laufzeit ungültiges Muster endet als `ValidationError` im Regel-Lauf, nicht still. Beides ist
keine Lücke, aber eine Unschärfe, die unter „Nach 0.1“ steht.

**Behebung (2026-10-09, Phase B, PR 2):** 1-01, 3.1, 3.2, 5.1, 5.2, 5.3 mit Tests; Doku 3.3, A-07,
A-08. Phase A hatte zuvor die drei Punkte aus Entscheidung 9 behoben (Drossel des Webhook-Tests,
Löschen von `previous_secret`; `img-src blob:` folgt in Phase C).

## 1 Sicherheit der API

Datum: 2026-10-09. Unabhängiges Review, nur lesend. Branch `claude/project-thread-7jl9zr`,
Stand `80bd199`. Grundlage: `CLAUDE.md`, `.idea/architektur.md` (Berechtigungen, Authentifizierung,
Schnittstellen, Migration), `.idea/reviews/M4-security.md` (frühere Befunde; behobene werden nicht
wiederholt). Gelesen: `adapters/inbound/rest/*`, `adapters/inbound/mcp/*`,
`core/services/{auth,oidc,users,documents,drawers,_access,pipeline}.py`,
`core/services/{rules,webhooks}/*`, `core/domain/{identity,permissions,webhooks,media_types}.py`,
`adapters/outbound/{oidc,webhooks,sql/identity}.py`, `composition/{settings,api,logging_setup}.py`,
die REST-, MCP- und Rechte-Tests sowie die MCP-SDK-Fehlerbehandlung in `.venv`. Alle Pfade unten
relativ zu `backend/src/papiq/`, sofern nicht anders angegeben.

Keine hohen Befunde. Die Zugriffskontrolle auf Dokumente, Schubladen, Regeln und Webhooks folgt
der Architektur; die M12-Erweiterungen sind auf Admins beschränkt; Sitzungen, CSRF, Drossel, OIDC
und MCP sind sauber. Die Lücken liegen am Rand: eine Umgehung der M4-03-Regel über ein zweites
Konto, Persistenz einer gekaperten Sitzung per OIDC-Link, fehlende Header außerhalb `/auth` und
einige Ressourcen- und Reichweitenfragen.

| Nr. | Schwere | Befund |
| --- | --- | --- |
| 1-01 | mittel | Admin-API-Token befördert ein anderes Konto zum Admin (`PATCH /users/{id}` mit `role`); damit entsteht doch eine Admin-Sitzung aus einem Token |
| 1-02 | niedrig | OIDC-Verknüpfung nur mit Sitzung, ohne Passwort: eine gekaperte Sitzung verschafft dauerhaften Zugang, den Passwortwechsel und `revoke_tokens` nicht beenden |
| 1-03 | niedrig | `channel=migration` darf jeder Nutzer setzen; Regeln mit Kanal-Bedingung sind fälschbar |
| 1-04 | niedrig | Personenbezogene API-Antworten außerhalb `/auth` ohne `Cache-Control` und `X-Content-Type-Options` |
| 1-05 | niedrig | Webhook-Test liefert jedem Nutzer Status, Fehlerart und Dauer beliebiger interner Ziele (Portscan, Cloud-Metadaten) |
| 1-06 | niedrig | Zahl offener Ereignisströme je Nutzer unbegrenzt |
| 1-07 | niedrig | OIDC-Callback mit `error`/ohne `code` lässt das Flow-Cookie stehen |
| 1-08 | niedrig (akzeptiert) | Konto-Drossel sperrt auch richtige Anmeldungen: gezielte Aussperrung eines Kontos mit ~100 Anfragen am Tag; Kommentar im Code widerspricht dem Verhalten |

### 1-01 (mittel): Admin-Token befördert ein anderes Konto zum Admin

**Problem.** M4-03 wurde so behoben, dass ein Admin-Token keine Anmeldedaten mehr verändert
(`POST /users` mit Passwort oder Rolle Admin, Passwort-Reset, TOTP aus, OIDC-Links: nur Sitzung;
`adapters/inbound/rest/users.py:74-79,125-154`). `PATCH /users/{id}` nimmt aber weiter jeden
`Authenticated`, also auch ein `read_write`-Token, und ändert `role` und `active`
(`adapters/inbound/rest/users.py:101-113`, `core/services/users.py:101-124`). Die Architektur sagt
für M12 ausdrücklich „Passwort oder Rolle Admin weiter nur mit Sitzung“ (`.idea/architektur.md`,
Abschnitt Migration), prüft das aber nur beim Anlegen.

**Angriffsweg.** Ein geleaktes Admin-Token (typisch: Migrations-CLI, Skript, CI) plus ein
beliebiges Nutzerkonto mit Passwort (eigenes oder ein mitwirkendes): `PATCH /users/<nutzer>`
`{"role": "admin"}` mit dem Token, danach normale Passwort-Anmeldung dieses Nutzers. Ergebnis ist
genau die Admin-**Sitzung**, die M4-03 verhindern sollte: Passwörter anderer zurücksetzen, TOTP
abschalten, Links entfernen, neue Tokens. Zusätzlich kann das Token alle anderen Admins
deaktivieren (`active=false`, bis auf den letzten), was die rechtmäßigen Admins aussperrt; weil
`bootstrap_admin` schon bei einem inaktiven Admin nichts tut (`core/services/users.py:224-232`,
`is_admin` statt `is_active_admin`), hilft dann auch `PAPIQ_ADMIN_*` nicht mehr.

**Vorhandene Tests.** `tests/unit/adapters/rest/test_authentication.py:324-371` prüfen nur das
eigene Konto des Token-Inhabers; `test_resources.py:223` befördert mit Sitzung.

**Behebung.** Rollenwechsel nur mit Sitzung: in `update_user` bei `body.role is not None` einen
`SessionPrincipal` verlangen (oder getrennte Endpunkte); `active` darf per Token bleiben, weil die
Migration inaktive Nutzer deaktiviert (Architektur-Tabelle). Zusätzlich erwägen: `active=false`
für Admin-Konten nur mit Sitzung. `bootstrap_admin` sollte auf `is_active_admin` prüfen, damit ein
Betreiber eine Aussperrung über die Konfiguration auflösen kann.

**Test, der vorher fehlschlägt.** `test_an_admin_token_cannot_promote_anyone`: Admin-Token,
`PATCH /users/<bob>` `{"role": "admin"}` → 403 mit „session“ im `detail`; anschließend meldet sich
Bob per Passwort an und `GET /users` liefert ihm nur `id`/`username` (keine Admin-Sicht).
Zweiter Test: `PATCH … {"active": false}` per Token bleibt 200 (Migration).

### 1-02 (niedrig): OIDC-Verknüpfung ohne Passwort als Persistenz einer gekaperten Sitzung

**Problem.** `POST /auth/oidc/link` braucht nur eine Sitzung und den CSRF-Token
(`adapters/inbound/rest/account.py:408-424`, `core/services/oidc.py:99-103`); weder Passwort noch
TOTP-Code. Ein Passwortwechsel beendet Sitzungen, lässt aber Links stehen
(`core/services/auth.py:314-331`), ebenso der Admin-Reset (`core/services/users.py:139-153`).
Wer eine Sitzung kapert (XSS in einem Drittskript, offener Browser), verknüpft sein eigenes
IdP-Konto mit dem Opfer und meldet sich danach jederzeit per OIDC an; `revoke_tokens` und ein neues
Passwort ändern daran nichts. Der Link ist in `GET /auth/me` sichtbar, aber nichts weist das Opfer
darauf hin. Verwandt mit M4-05 (Tokens überleben den Passwortwechsel; Entscheidung: keine
Änderung); hier kommt hinzu, dass der Weg eine vollständige, TOTP-freie Anmeldung ist.

**Behebung (Entscheidung des Besitzers).** `POST /auth/oidc/link` wie `POST /auth/password` das
aktuelle Passwort (bei Konten ohne Passwort: einen TOTP-Code, sonst frei) verlangen; dasselbe
wäre für `POST /auth/tokens` konsequent, wurde aber in M4-05 anders entschieden. Mindestens: beim
Passwort-Reset durch einen Admin (`reset_password`) Links mit entfernen oder `revoke_links`
anbieten, da der Reset der typische Schritt bei Kompromittierungsverdacht ist.

**Test.** `test_linking_needs_the_current_password`: Sitzung ohne `current_password` →
`POST /auth/oidc/link` 422/403; mit falschem Passwort 403 und ein gezählter Fehlversuch; mit
richtigem 200.

### 1-03 (niedrig): `channel=migration` für alle Nutzer

**Problem.** `_channel` erlaubt jedem Aufrufer `migration` (`adapters/inbound/rest/documents.py:651-657`);
`PipelineService._owner` prüft Admin-Rechte nur, wenn `metadata` mitkommt
(`core/services/pipeline.py:304-308`). Ohne Metadaten läuft die Verarbeitung normal
(`core/services/imports.py:25-39`), der Kanal steht aber als `migration` am Dokument und ist
Regel-Bedingung (`core/domain/rules.py:80,195`). Ein Nutzer kann damit globale Regeln, die ein Admin
für migrierte Dokumente geschrieben hat (Tags, Attribute, erzwungene Prüfung), auf eigene Uploads
anwenden lassen oder umgehen. Die Architektur beschreibt `migration` als Kanal „für Imports“ und
die Erweiterungen als solche, die „Nicht-Admins keine Rechte“ geben; der Kanal selbst ist dort
nicht als Admin-Vorrecht festgelegt, deshalb niedrig.

**Behebung.** `channel=migration` nur für aktive Admins (403 sonst), analog zu `owner`; in
`_UPLOAD_BODY` dokumentieren.

**Test.** `test_only_admins_choose_the_migration_channel`: Nutzer-Token, Upload mit
`channel=migration` ohne Metadaten → 403; Admin → 202 und `channel == "migration"`.

### 1-04 (niedrig): Fehlende Header auf API-Antworten außerhalb `/auth`

**Problem.** `NoStore` wirkt nur unter `/api/v1/auth` (`adapters/inbound/rest/middleware.py:55-77`,
`app.py:122`); `test_authentication.py:392-393` schreibt sogar fest, dass `/drawers` *keinen*
`Cache-Control` trägt. Dokumentlisten, Details, Suchtreffer (`q` in der URL), Protokolle, Nutzer
und Webhook-Logs sind personenbezogen und werden mit Cookie geholt; ohne `Cache-Control` bleibt
ihr Verbleib in Browser- und Proxy-Caches (hinter dem laut M4-01 verpflichtenden Reverse-Proxy)
der Konfiguration überlassen. Außerdem fehlt `X-Content-Type-Options: nosniff` auf allen
JSON-Antworten; nur Downloads (`documents.py:388-400`) und die UI (`ui.py:24-28`) setzen es.
Die UI setzt ihre CSP als Meta-Tag (`web/vite.config.ts:26-43`) und `frame-ancestors 'none'`,
`nosniff`, `Referrer-Policy` als Header (`ui.py:24-28`): in Ordnung.

**Behebung.** `NoStore` auf `/api/v1` ausweiten (Downloads setzen schon `private, no-store`;
`/health` und `openapi.json` ausnehmen) oder eine kleine Header-Middleware:
`Cache-Control: no-store`, `X-Content-Type-Options: nosniff` für alle API-Antworten. Test in
`test_authentication.py:392-393` entsprechend umkehren.

**Test.** `test_api_answers_carry_no_store_and_nosniff`: `GET /documents`, `GET /users`,
`GET /documents/search?q=x`, eine 404 → beide Header vorhanden; `GET /health` ohne `no-store`.

### 1-05 (niedrig): Webhook-Test als Portscanner

**Problem.** `validate_url` lässt jeden `http`/`https`-Host zu, bewusst auch im eigenen Netz
(`core/domain/webhooks.py:55-76`). `POST /webhooks/{id}/test` sendet sofort und gibt dem Aufrufer
`status_code`, `error` („cannot connect“, „TLS handshake failed“, „no answer within …“) und
`duration_ms` zurück (`core/services/webhooks/delivery.py:155-173`,
`adapters/outbound/webhooks/sender.py:39-68`). Jeder Nutzer mit `read_write`-Token kann so Hosts
und Ports des internen Netzes (Datenbank, Meilisearch, S3, Ollama, Cloud-Metadaten
`169.254.169.254`) mit 10 Anfragen pro Minute abtasten; über reguläre Zustellungen und das
Lieferprotokoll (`GET /webhooks/{id}/deliveries`) ohne diese Grenze. Body ist signiertes
Papiq-JSON, Methode POST, keine Redirects: ein klassischer SSRF mit Datenabfluss ist nicht
möglich, die Erkundung schon. Die Entscheidung „Ziele im eigenen Netz sind erlaubt“ ist
dokumentiert; der Befund betrifft die Granularität der Rückmeldung an Nicht-Admins.

**Behebung.** Optional eine Sperrliste in der Konfiguration (`PAPIQ_WEBHOOK_DENY_HOSTS`, Default:
link-local `169.254.0.0/16`, `fe80::/10`, Loopback) und/oder für Nicht-Admins nur
`delivered`/`gave_up` ohne `error`-Text und Dauer zurückgeben. Die Testgrenze gilt pro Prozess
(`_test_requests`, Kommentar `delivery.py:93-95`): mit mehreren API-Instanzen vervielfacht sie sich.

**Test.** `test_a_webhook_cannot_target_link_local_addresses`: `POST /webhooks` mit
`http://169.254.169.254/latest/meta-data` → 422; bestehender Webhook, `PATCH url` darauf → 422.

### 1-06 (niedrig): Ereignisströme je Nutzer unbegrenzt

**Problem.** `GET /events` legt je Verbindung einen `Listener` mit Queue (100) an
(`adapters/inbound/rest/events.py:122-133`, `streams.py:70-82`) und prüft alle 30 s die Anmeldung
per Datenbankzugriff. Es gibt keine Obergrenze je Nutzer oder insgesamt; ein angemeldeter Nutzer
(auch mit `read`-Token) hält mit tausenden Verbindungen Dateideskriptoren, Speicher und bei jedem
Ereignis `filter_readers`-Abfragen (`core/services/documents.py:209-224`, eine Abfrage pro
Zuhörer-Nutzer) im API-Prozess fest. Keine Rechteverletzung, aber eine Verfügbarkeitslücke, die
nur Angemeldeten offensteht.

**Behebung.** Grenze je Nutzer in `EventHub.listen` (z. B. 10, 429 oder 409 darüber) und
insgesamt (z. B. 1000); Zähler im Hub.

**Test.** `test_a_user_may_hold_only_so_many_event_streams`: elf Verbindungen desselben Nutzers,
die elfte antwortet 429; nach Schließen einer weiteren klappt es wieder.

### 1-07 (niedrig): Flow-Cookie bleibt nach „denied“

**Problem.** Bei `error` oder fehlendem `code`/`state` antwortet der Callback
`_oidc_failure(failed, "denied")` ohne `context`, also ohne das Flow-Cookie zu löschen
(`adapters/inbound/rest/account.py:377-378,400-405`); alle anderen Fehlpfade löschen es. Der
versiegelte Flow (State, Nonce, PKCE-Verifier) bleibt bis zu 10 Minuten im Browser. Praktisch
harmlos (AES-GCM-versiegelt, kein Code darin), aber inkonsistent und ein zweiter Versuch mit dem
alten State bleibt möglich.

**Behebung.** `context` auch hier übergeben.

**Test.** `test_a_denied_callback_clears_the_flow_cookie`: `GET /auth/oidc/callback?error=access_denied`
mit Flow-Cookie → 303 nach `/ui/login?error=denied` und `Set-Cookie` löscht `__Host-papiq_oidc`.

### 1-08 (niedrig, akzeptiert in M4): Konto-Drossel als gezielte Aussperrung

**Problem.** `ACCOUNT_THROTTLE` sperrt ab dem sechsten Fehlversuch (Fenster 1 Tag) mit
Verdopplung bis 15 Minuten, und zwar **vor** der Passwortprüfung, also auch richtige Anmeldungen
(`core/domain/identity.py:342-344,393-404`, `core/services/auth.py:495-506`). Ein nicht
angemeldeter Angreifer hält mit einem falschen Versuch alle 15 Minuten (~100 Anfragen am Tag)
jedes Konto – auch das des einzigen Admins – dauerhaft gesperrt; die Quellen-Drossel greift nicht,
da richtige Anmeldungen durchkommen sollen. Der Kommentar „No hard lock, so nobody can lock others
out on purpose“ (`identity.py:340-341`) beschreibt das Gegenteil des Verhaltens. M4 hat die
Konto-Drossel bewusst so belassen (Schutz gegen TOTP-Raten).

**Hinweis.** Wenn die Entscheidung bleibt: Kommentar und README anpassen. Alternative ohne
Verlust beim TOTP-Schutz: die Sperre nur auf den zweiten Faktor anwenden (Passwort bekannt), beim
Passwort selbst wie bei der Quelle nur Fehlversuche abweisen; oder ein Konto-Lockout, das ein
gültiges TOTP/Recovery-Code-Paar durchlässt.

## Geprüft und in Ordnung

**Sitzungen.** Cookie `__Host-papiq_session`: HttpOnly, Secure, SameSite=Lax, Path=/, keine Domain
(`adapters/inbound/rest/auth.py:128-137`); ohne `Secure` nur mit `PAPIQ_COOKIE_SECURE=false`, und
dann erzwingt `settings.py:259-265` hinter Secure-Cookies einen Proxy. CSRF: `X-CSRF-Token` =
HMAC(Session-Token) auf allen unsicheren Methoden, konstantzeitig (`auth.py:81-84`,
`core/domain/identity.py:196-203`); Bearer ignoriert Cookies (`auth.py:68-80`); Login nur als
`application/json` (`account.py:72-76`), kein CORS. Ablauf serverseitig: idle 1 Tag, max 30 Tage,
`max_age >= idle` geprüft (`auth.py:252-270`, `settings.py:115-116,295-298`); Touch höchstens
minütlich. Login beendet die vorherige Sitzung des Browsers (`account.py:113-114`), OIDC-Login
ebenso (`oidc.py:166-167`; M4-06). `DELETE /auth/sessions`, Passwortwechsel beendet alle Sitzungen
mit Erneuerung der eigenen und Konfliktprüfung (`auth.py:314-331`); Deaktivierung beendet
Sitzungen und sperrt Tokens (`users.py:116-118`, `auth.py:265-266,280-282`); Rollenwechsel wirkt
sofort, da der Nutzer je Anfrage gelesen wird. Jede registrierte Route außer genau fünf
öffentlichen antwortet 401 (`tests/unit/adapters/rest/test_authentication.py:21-72`).

**Passwort, TOTP, Wiederherstellungscodes.** Argon2id über den Port, NFKC, Policy 12–256 Zeichen,
nicht der Nutzername (`identity.py:49-65`); Dummy-Hash für unbekannte und deaktivierte Konten,
gleiche Fehlermeldung (`auth.py:161-172,489-493`). Drossel atomar: Upsert mit `ON CONFLICT` und
`RETURNING` vor der Prüfung, Rücknahme bei Erfolg (`adapters/outbound/sql/identity.py:345-378`,
`auth.py:495-514`; M4-02), Quelle je IPv4 bzw. IPv6-/64 (`identity.py:355-366`; M4-09),
Quellen-Sperre weist nur falsche Anmeldungen ab (`auth.py:168-172,225-229`; M4-01). TOTP ±1
Schritt, jeder Schritt einmal, Commit nur bei Erfolg (`identity.py:140-146`, `auth.py:207-224`);
Secret AES-GCM mit Nutzer-ID als Kontext (`auth.py:480-487`). Wiederherstellungscodes 80 Bit,
SHA-256, einmalig, Vergleich konstantzeitig (`identity.py:85-96,131-138`). TOTP aus und neue Codes
nur mit Code oder Wiederherstellungscode und gedrosselt (`auth.py:396-418`). Fehlversuche loggen
nie den eingegebenen Namen (`auth.py:535-538`; M4-07).

**OIDC.** Authorization Code mit PKCE S256, `state`, `nonce`, Verifier, Ziel und ggf. Link-Nutzer
AES-GCM-versiegelt im HttpOnly-Flow-Cookie (10 Minuten), `state` konstantzeitig
(`oidc.py:105-127,194-227`, `provider.py:207-216`). ID-Token: nur asymmetrische, angekündigte
Algorithmen, JWKS mit einmaligem Refetch, `iss` exakt, `aud`/`azp`, `exp`/`iat` ±60 s, `nonce`
(`provider.py:116-161`); Discovery-Issuer muss gleichen, Issuer `https` (`provider.py:165-177`,
`settings.py:337-338`). Verknüpfung über `(issuer, subject)`, nie E-Mail; Link nur, wenn derselbe
Nutzer am Callback angemeldet ist (`oidc.py:129-148`); Auto-Create aus, bei Namenskollision
Konflikt (`oidc.py:150-165`, `users.py:263-270`). Fehlpfade als Redirect-Codes ohne Details
(`account.py:376-392`); `safe_redirect` lässt nur Pfade dieser Site zu (`identity.py:303-315`).
Access-Log verbirgt die Callback-Query (`logging_setup.py:71-81`).

**API-Tokens.** `papiq_` + 256 Bit, nur SHA-256 gespeichert (`identity.py:71-78,237-259`), Suche
über den Hash. `read` zentral für alle Methoden außer GET/HEAD/OPTIONS abgewiesen
(`auth.py:74-75`), auch im MCP (`mcp/server.py:381-383`). Eigene Anmeldung (`/auth/*`) und die
Admin-Endpunkte für Anmeldedaten nur mit Sitzung (`account.py`, `users.py:125-154`; M4-03), kein
Selbstbezug (`users.py:251-253`); `POST /users` per Token nur Rolle Nutzer ohne Passwort
(`users.py:74-79`). Offen: Rollenwechsel (1-01).

**Rechte je Endpunkt.** Eine Regel in `core/domain/permissions.py:28-100`: Besitzer immer; andere
nur über Schublade und nur bei Grün; Admins alles, deaktivierte Admins nichts. SQL spiegelt die
Reichweite (`sql/repositories.py:242-261`), `all_users` nur Admins (`documents.py:373-377`,
`search.py:104-105`, `rules/management.py:40-41`), Service prüft jedes Ergebnis erneut
(`documents.py:169-179`). Fremde und gelbe/rote Dokumente sind 404 wie fehlende
(`_access.py:27-34`, `test_permission_matrix.py:393`). Verschieben: Besitzer in beschreibbare
Schublade oder Admin (`permissions.py:83-86`, `documents.py:334-352`); Bestätigen: Admin jede
Schublade, sonst Besitzer mit Schreibrecht (`pipeline.py:381-387`); Protokoll, Review, Retry,
Reprocess, Löschen: Besitzer oder Admin. Schubladen: verwalten nur Besitzer und Admins
(`drawers.py:96-100`), Freigabe nur an aktive Nutzer (`drawers.py:75-85`; M4-10), Standardschublade
nie geteilt (`core/domain/drawers.py:43-46`). Regeln: globale lesen alle, ändern Admins;
Nutzer-Regeln sind für Dritte 404; F2a: Schublade einer Nutzer-Regel wird gegen deren Besitzer
geprüft, auch wenn ein Admin ändert oder aktiviert (`rules/management.py:92-115,126-151,177-179`),
und beim Lauf erneut (`rules/running.py:389`). Rückwirkende Anwendung: Nutzer-Regel nur auf
Dokumente des Regelbesitzers, Schreibrecht je Dokument (`rules/retroactive.py:393-410`). Webhooks:
Besitzer oder Admin, Dritte 404 (`webhooks/management.py:149-158`); Ereignisse nur in der
Reichweite des Besitzers, auch bei Admins (`webhooks/delivery.py` Fan-out, `permissions.in_reach`).
M12: `owner` und `metadata` nur aktive Admins, `metadata` nur mit `channel=migration`, Besitzer
muss aktiv sein, Referenzen und Attribute vor dem Speichern geprüft (`pipeline.py:294-331,633-654`);
`owner_id` an `POST /drawers` nur Admins, Ziel aktiv (`drawers.py:49-64`); `sha256` in den Details
(`schemas.py:154-157,180`), nur für Leser des Dokuments. Stammdaten: lesen alle, ändern Admins
(`master_data.py:288-289`); Reindex Admins (`indexing.py:164-170`).

**Upload.** Streaming in eine Tempdatei, Limit über `Content-Length` und gezählte Bytes, Feld- und
Gesamtgrenzen, Tempdatei wird in jedem Fall entfernt (`upload.py:54-100`, `documents.py:216-217`).
Typ nur aus dem Inhalt (`core/domain/media_types.py:22-30`); Dateiname: letztes Segment nach
Normalisierung von `\`, ohne Steuerzeichen, 255 Zeichen, Unicode bleibt (`upload.py:209-214`);
Bidi-Zeichen (U+202E) bleiben erhalten, was eine Endung in der UI verschleiern kann – nur Anzeige,
da Objekte nach SHA-256 liegen. Dubletten je Besitzer (`pipeline.py:323-325`); das Original wird
je Hash einmal gespeichert, ohne dass ein Nutzer vom anderen erfährt. Downloads mit `nosniff`,
`CSP: sandbox; default-src 'none'`, `private, no-store`, Original als Attachment
(`documents.py:388-400`).

**Request-Größen und Fehler.** JSON-Bodies 1 MiB (`middleware.py:20-52`, `settings.py:101-102`),
Upload ausgenommen und eigens begrenzt (M4-04). Problem-Details: Domänenfehler mit ihrem Text,
Validierungsfehler nur Ort und Meldung, Unerwartetes als generische 500 mit Stacktrace nur im Log
(`problems.py:128-157`). Pydantic-Modelle `extra="forbid"` mit Längen (`schemas.py`).

**Header.** UI: CSP als Meta (Hash-Modus, `default-src 'self'`, `object-src 'none'`, `base-uri`,
`form-action`; `web/vite.config.ts:26-43`) plus Header `frame-ancestors 'none'`, `nosniff`,
`Referrer-Policy: same-origin` (`ui.py:24-28,115-117`); Pfadprüfung gegen Traversal und versteckte
Dateien (`ui.py:64-73`). `/auth/*` `no-store` auch für Fehler und Redirects (`middleware.py:55-77`;
M4-08). Offen: übrige API (1-04).

**SSE.** Sichtbarkeit des `document_id`-Filters vor Streamstart (`streams.py:24-33`), je Ereignis
`filter_readers` mit aktuellem Stand, Admins zu allem, `document.deleted` an gespeicherte Leser plus
aktive Admins, `readers` nicht gesendet (`events.py:101-120,142-150`); Re-Authentifizierung alle
30 s (`streams.py:77-78`, `auth.py:89-96`); Abbruch räumt den Zuhörer auf, langsame Clients werden
getrennt (`events.py:78-89,129-133`). Offen: Anzahl (1-06).

**MCP.** Nur Bearer, Cookies zählen nicht, 401 mit `WWW-Authenticate` (`mcp/app.py:32-54`);
stateless, Body-Limit über die App-Middleware. Tools rufen ausschließlich Services mit der Nutzer-ID
(`mcp/server.py:268-419`); `update_metadata` prüft `can_write` (`:381-383`); Textgrenze
`min(limit, text_max)` (`:348`). Fehler: `DomainError` als Text, alles andere wird vom SDK als
generisches „Error executing tool …“ ohne Exception-Text gemeldet
(`.venv/…/mcp/server/mcpserver/tools/base.py:140-213`). DNS-Rebinding-Schutz aus ist begründet
(Header-Token, kein Cookie).

**Ratenbegrenzung und Enumeration.** Login je Konto und Quelle (oben); Webhook-Test 10 je Nutzer
und Minute mit `Retry-After` (`delivery.py:175-181`). Nach richtigem Passwort verrät 401
`second_factor_required` (akzeptiert, M4-12); unbekannte Nutzer wie falsche Passwörter; Nutzerliste
für Angemeldete bewusst sichtbar.

**Logs.** Fehlversuche ohne Namen, nur IDs und Fehlerarten (`auth.py:535-538`,
`provider.py:92-99,129-133,160`); Webhook-Fehler ohne URL (`sender.py:8-9,67`); HTTP-Client-Logger
auf WARNING, Health-Checks und Callback-Query aus dem Access-Log (`logging_setup.py:53-81`);
Konfiguration mit maskierten Secrets und Userinfo (`settings.py:356-361,410-411`); 500er mit
Traceback nur serverseitig (`problems.py:154-157`).

## 2 Webhooks und ausgehende Verbindungen

Datum: 2026-10-09. Unabhängiges Review ohne Kontext aus der Umsetzung; nur gelesen, nichts geändert, keine Tests ausgeführt.

### Geprüfter Stand

- **Branch:** `claude/project-thread-7jl9zr` (Commit `80bd199`) gegen `main` (`b8d8587`).
- **Grundlage:** `CLAUDE.md`, `.idea/architektur.md` (Asynchronität und Ereignisse, Webhooks, Suche, Betrieb), `backend/README.md` (Webhooks, Search, Classification), `.idea/reviews/M4-security.md`.
- **Vollständig gelesen:** `core/domain/webhooks.py`, `core/services/webhooks/{delivery,management,policy}.py`, `core/ports/webhook_sender.py`, `adapters/outbound/webhooks/sender.py`, `adapters/inbound/rest/webhooks.py`, `adapters/outbound/sql/webhooks.py` (und die Tabellen), `core/services/maintenance.py` (Aufräumen), `core/domain/permissions.py` (`reach_access`, `in_reach`), `adapters/outbound/crypto/cipher.py`, `adapters/outbound/openai_compat/client.py`, `core/services/classification/prompts.py`, `adapters/outbound/meilisearch/index.py`, `core/services/search.py` (Einbettung der Anfrage), `adapters/outbound/s3/object_store.py`, `core/ports/object_store.py` (`check_key`), `adapters/outbound/oidc/provider.py`, `composition/{endpoints,logging_setup,container,settings,__main__}.py`, die Compose-Beispiele in `deploy/` und `.devcontainer/`. Standardwerte von `httpx2` 2.13.1 im installierten Paket nachgeschlagen (`follow_redirects=False`, `trust_env=True`, `verify=True`).
- Pfade unten relativ zu `backend/src/papiq/`, sofern nicht anders angegeben.

### Zusammenfassung

Keine hohen oder mittleren Befunde. Die SSRF-Entscheidung (interne Ziele erlaubt) ist sauber begrenzt: nur `http`/`https`, keine Zugangsdaten in der URL, keine Weiterleitungen, keine Proxy-Variablen, feste Header und fester Body, Antwort wird nicht gelesen, Fehlertexte ohne URL. Signatur, Geheimnisse, Zustellprotokoll, Reichweite und Abschalten sind konsequent umgesetzt und getestet. Die vier Befunde sind Randfälle: ein Betriebshinweis zu Meilisearch ohne Schlüssel (das einzige interne Ziel, auf das ein Webhook mit dem festen Body tatsächlich wirkt), eine fehlende `https`-Prüfung für die Endpunkte aus der OIDC-Discovery, ein Zeitlimit des LLM-Clients, das anders wirkt als dokumentiert, und ein 500 beim Testaufruf nach einem Schlüsselwechsel.

| Nr. | Schwere | Befund |
| --- | --- | --- |
| 2-01 | niedrig | Meilisearch ohne Master-Key: jeder Nutzer kann über einen Webhook in den Index schreiben und den Abgleich dauerhaft brechen; Papiq warnt nicht |
| 2-02 | niedrig | OIDC: `jwks_uri`, `token_endpoint`, `authorization_endpoint` aus der Discovery werden nicht auf `https` geprüft |
| 2-03 | niedrig | LLM- und Embedding-Client: `PAPIQ_LLM_TIMEOUT` gilt je Socket-Operation, nicht je Anfrage; Lease kann ablaufen, Anfrage läuft doppelt |
| 2-04 | niedrig | `POST /webhooks/{id}/test` antwortet 500 statt eines Protokolleintrags, wenn das Geheimnis nicht entschlüsselbar ist |

### Befunde

#### 2-01 (niedrig): Meilisearch ohne Master-Key ist über Webhooks beschreibbar

**Fundstellen:** `composition/settings.py:169` (`meilisearch_api_key` optional, keine Konsistenzprüfung), `adapters/outbound/meilisearch/index.py:84` (ohne Schlüssel kein `Authorization`-Header, Papiq arbeitet anstandslos), `core/domain/webhooks.py:55-76` (`validate_url`: jeder Host, jeder Port, jede Query), `adapters/outbound/meilisearch/index.py:485-494` (`_state_of`: `item["version"]` fehlt → `SearchIndexError`), `:117-131` (`states()`, vom Abgleich benutzt).

**Problem:** Die Architektur erlaubt interne Ziele bewusst, mit dem Argument, dass Body und Header fest sind und die Antwort nicht gezeigt wird (`backend/README.md`, „Targets“). Ich habe die internen Dienste des Compose-Stacks daraufhin durchgesehen, ob der feste Body `{"id":"<uuid>","type":…,"occurred_at":…,"document_id":…}` irgendwo eine Wirkung hat: Papiq-API (401/422), Garage (Signatur fehlt), Postgres (kein HTTP), Ollama (`model` fehlt → 400) sind unempfindlich. **Meilisearch ohne Master-Key** nicht:

- `POST http://meilisearch:7700/indexes/papiq-documents/documents` nimmt den Body als Dokument an (Primärschlüssel `id`, die Ereignis-UUID ist gültig). Das Fremddokument hat kein `version`; der nächste Abgleich (`states()` → `_state_of`) wirft `SearchIndexError` und bricht ab, bei jedem Lauf, bis jemand das Dokument von Hand löscht. Suchen sind nicht betroffen (keine durchsuchbaren Felder, kein Vektor, Rechtefilter), aber der Index driftet unbemerkt.
- `POST http://meilisearch:7700/tasks/cancel?statuses=enqueued,processing` bricht laufende Index-Aufgaben ab (`_wait` sieht `canceled`, der Job wiederholt sich); `POST …/dumps` und `…/snapshots` füllen die Platte.

Ein Nutzer braucht dafür nur das Recht, Webhooks anzulegen (jeder Nutzer) und einen Testaufruf. Die Beispiel-Stacks (`deploy/compose.sqlite.yml:46,88`, `deploy/compose.postgres.yml:52,147`, `.devcontainer/compose.yml:68`) setzen einen Schlüssel, deshalb niedrig. Papiq selbst verlangt ihn aber nicht und sagt nichts, wenn er fehlt, obwohl die Webhook-Dokumentation genau diese Konstellation („die eigene Infrastruktur ist erreichbar“) als unbedenklich beschreibt.

**Empfehlung:**
1. In `Settings._check_consistency`: mit `meilisearch_url` und ohne `meilisearch_api_key` den Start in Produktion (`cookie_secure=true`, wie beim Dev-Schlüssel) verweigern oder mindestens laut warnen („Meilisearch ohne Schlüssel: jeder, der Webhooks anlegen darf, kann den Index beschreiben“). Alternativ in `MeilisearchIndex.check()`: ohne Schlüssel `GET /keys` versuchen; antwortet es 200, läuft Meilisearch ohne Master-Key.
2. Den Abgleich robust machen: `_state_of` sollte ein Dokument ohne `version` nicht zum Abbruch machen, sondern es als fremd melden und löschen lassen (der Index enthält nichts, was nicht in der Datenbank steht).

**Belegtests (schlagen heute fehl):**
- `tests/unit/test_settings.py::test_meilisearch_without_a_key_is_refused_in_production`: `PAPIQ_MEILISEARCH_URL` gesetzt, kein Schlüssel, `PAPIQ_COOKIE_SECURE=true`, gültiger Proxy → `ConfigurationError` erwartet (oder: Warnung in `describe`/Startlog).
- `tests/unit/adapters/meilisearch/…::test_a_foreign_document_in_the_index_does_not_stop_the_states`: `MockTransport`, `POST …/documents/fetch` liefert ein Dokument `{"id": "<uuid>"}` ohne `version` → `states()` liefert die übrigen Zustände statt `SearchIndexError`.

#### 2-02 (niedrig): OIDC-Endpunkte aus der Discovery werden nicht auf `https` geprüft

**Fundstellen:** `adapters/outbound/oidc/provider.py:173-175` (nur `isinstance(str)` für `authorization_endpoint`, `token_endpoint`, `jwks_uri`), `:183` (JWKS wird von `jwks_uri` geholt), `:74` (Browser wird an `authorization_endpoint` geschickt), `composition/settings.py:337` (nur der Issuer muss `https` sein).

**Problem:** OpenID Connect Discovery 1.0 verlangt für diese drei Endpunkte das `https`-Schema. Papiq prüft das nicht. Nennt ein (fehlkonfigurierter oder teilweise kompromittierter) Provider `http://`-Endpunkte, holt Papiq die Signaturschlüssel im Klartext; wer auf dem Pfad sitzt, tauscht das JWKS aus und stellt ein ID-Token aus, das alle Prüfungen (`iss`, `aud`, `nonce`, Signatur gegen die „frischen“ Schlüssel, Refetch bei unbekannter `kid` in `:121-134`) besteht → Anmeldung als beliebiger verknüpfter Nutzer, mit `oidc_auto_create` als neuer Nutzer. Der `client_secret` ginge beim Token-Abruf ebenfalls im Klartext. Voraussetzung ist ein Provider, der das tut; echte Provider tun es nicht, deshalb niedrig. Die Prüfung kostet drei Zeilen und schließt den Weg.

**Empfehlung:** In `_discover` zusätzlich `urlsplit(metadata[field]).scheme == "https"` fordern (Fehler „the discovery document names a non-https <field>“); für die Tests reicht das, `tests/oidc_idp.py` nutzt bereits `https://idp.example`.

**Belegtest (schlägt heute fehl):** `tests/unit/adapters/oidc/test_provider.py::test_discovery_endpoints_must_be_https`: `SimulatedIdp` mit `jwks_uri: "http://idp.example/keys"` (je einmal für die drei Felder parametrisiert) → `IdentityProviderError` bei `authorization_url(...)`; heute läuft der Flow durch.

#### 2-03 (niedrig): Zeitlimit des LLM-/Embedding-Clients gilt je Operation, nicht je Anfrage

**Fundstellen:** `adapters/outbound/openai_compat/client.py:163` (`httpx2.AsyncClient(timeout=timeout)`: Verbindungs-, Lese-, Schreib- und Pool-Limit je Operation), `:165` (nur `TimeoutException` wird zu „no answer within …“), `composition/container.py:539` (Lease der Pipeline-Jobs `2 * llm_timeout`), `backend/README.md` („`PAPIQ_LLM_TIMEOUT` is per request, and the job lease allows twice that“). Gegenstück: `adapters/outbound/webhooks/sender.py:46` (`asyncio.timeout(limit)` um den ganzen Austausch, dazu getestet in `tests/unit/adapters/webhooks/test_sender.py:34`).

**Problem:** Das Lese-Limit von httpx ist die Wartezeit *zwischen zwei Bytes*. Ein Endpunkt, der die Antwort tröpfelt (Cloud-Gateway unter Last, ein Proxy, ein langsamer lokaler Server), bleibt unter dem Limit und hält die Anfrage beliebig lange offen. Nach `2 * llm_timeout` läuft die Lease ab, ein zweiter Worker nimmt den Job und stellt dieselbe Anfrage noch einmal: doppelte Kosten bei einem bezahlten Endpunkt, doppelte Last bei Ollama, und das Ergebnis des ersten Durchlaufs wird beim Schreiben verworfen (`ConcurrencyError`). Die Dokumentation verspricht ein Limit je Anfrage. Für die Einbettung der Suchanfrage ist das gelöst (`core/services/search.py:159`, `asyncio.timeout`), für Klassifikation und Index-Einbettung nicht. Antwortgröße ist ebenfalls unbegrenzt (`response.json()`); da der Endpunkt vom Admin konfiguriert ist, nur ein Hinweis.

**Empfehlung:** In `_post` den Aufruf in `async with asyncio.timeout(timeout)` legen und `TimeoutError` wie `httpx2.TimeoutException` behandeln; das httpx-Limit bleibt als Verbindungs-Limit.

**Belegtest (schlägt heute fehl):** `tests/unit/adapters/openai_compat/test_client.py::test_the_timeout_covers_the_whole_request`: asynchroner `MockTransport`-Handler, der `await asyncio.sleep(1.0)` wartet und dann eine gültige Antwort liefert; `language_model(transport, timeout=0.1).complete(REQUEST)` muss `LanguageModelError("no answer within 0.1 seconds")` werfen und darf nicht länger als etwa 0,5 s dauern. Heute kommt nach einer Sekunde die Antwort.

#### 2-04 (niedrig): Testaufruf bei unbrauchbarem Geheimnis antwortet 500

**Fundstellen:** `core/services/webhooks/delivery.py:165` (`send_test`: `self._decrypt(webhook, now)` außerhalb jeder Behandlung), Gegenstück `:236-247` (`_attempt` fängt `DecryptionError`/`ValueError` und protokolliert „the secret cannot be used (was the secret key changed?)“), `adapters/inbound/rest/problems.py:169ff` (`DecryptionError` ist kein Domänenfehler → `unexpected_error`, 500 mit Stacktrace im Log).

**Problem:** Nach einem Wechsel von `PAPIQ_SECRET_KEY` (oder einer beschädigten Zeile) bekommt der Nutzer beim Testaufruf – der Weg, auf dem er so etwas bemerken soll – einen generischen 500 und keinen Protokolleintrag; die Zustellung desselben Webhooks protokolliert den Grund korrekt als `gave_up`. Kein Leck, aber ein unnötiger Fehlerpfad mit Stacktrace, und der Zählerstand der Drossel ist trotzdem verbraucht.

**Empfehlung:** `_decrypt` in `send_test` wie in `_attempt` behandeln: Zeile mit `outcome=gave_up`, `error="the secret cannot be used (was the secret key changed?)"`, `log.error("webhook secret unusable", …)`. Am einfachsten die Entschlüsselung und das Senden in eine gemeinsame Methode ziehen.

**Belegtest (schlägt heute fehl):** `tests/unit/services/test_webhook_delivery.py::test_the_test_request_reports_an_unusable_secret`: Webhook anlegen, danach `webhook.encrypted_secret` in der Datenbank verfälschen (oder die World mit einem `FakeCipher` anderen Schlüssels neu bauen) → `send_test` liefert eine `WebhookDelivery` mit `outcome == GAVE_UP` und „secret“ im `error`, kein Request beim Sender; heute `DecryptionError`.

### Geprüft und in Ordnung

**1. Webhooks: SSRF-Grenzen.** `validate_url` (`core/domain/webhooks.py:55-76`): nur `http`/`https` (Schema wird von `urlsplit` kleingeschrieben), Host Pflicht, keine Zugangsdaten (`user@`-Tricks mit Backslash landen als `username` und werden abgewiesen), kein Fragment, keine Steuerzeichen oder Leerzeichen, Port geprüft, 2000 Zeichen; gilt bei Anlegen und Ändern (`Webhook.__post_init__`, `change`). Sender (`adapters/outbound/webhooks/sender.py`): `follow_redirects=False` (3xx ist die Antwort, Signatur gilt nur für das Ziel), `trust_env=False` (keine `HTTP_PROXY`/`SSL_CERT_FILE`-Einflüsse), Zertifikate verifiziert ohne Abschaltmöglichkeit, feste Header (`User-Agent`, `Content-Type`, drei Signaturheader), fester Body, Antwort nicht gelesen (`client.stream`, nur Statuscode). DNS-Rebinding ist ohne Belang, weil interne Ziele erlaubt sind. Dass Status, Fehlerart (`cannot connect` / `no answer within` / TLS) und `duration_ms` ein Abtasten des Netzes erlauben und dass ein Nutzer mit 20 Webhooks und ~6 Ereignissen je Dokument über Uploads POSTs an ein internes Ziel vervielfachen kann (bis zu 10 Versuche je Zustellung), ist dokumentiert und per Entscheidung akzeptiert; mit Ausnahme von 2-01 habe ich kein internes Ziel gefunden, auf das der feste Body wirkt.

**2. Webhooks: Zeitlimits.** `asyncio.timeout(limit)` um den ganzen Austausch plus `httpx2.Timeout(limit, connect=min(5, limit))` (`sender.py:46-52`), `PAPIQ_WEBHOOK_TIMEOUT` 1 s bis 2 min (`settings.py:137`), Lease `timeout + 60 s` (`delivery.py:76,142`), `PAPIQ_WEBHOOK_CONCURRENCY` eigene Schleifen, Pipeline nicht betroffen (`worker.py:83`).

**3. Signatur.** Standard Webhooks: `webhook-id` = Ereignis-ID, `webhook-timestamp` = Unix-Sekunden des Versuchs (frisch je Versuch, `delivery.py:275`), HMAC-SHA256 über `<id>.<ts>.<body>` mit dem Base64-dekodierten Schlüssel (`webhooks.py:107-111`), `v1,` je Geheimnis, mehrere durch Leerzeichen (`signed_headers`), Body kompakt und genau so gesendet wie signiert (`event_body`, UTF-8, `separators=(",", ":")`). Geheimnis: 32 Zufallsbytes aus `secrets` (`new_secret`). Test gegen das Beispiel der Spezifikation vorhanden (`tests/unit/domain/test_webhooks.py:96`).

**4. Geheimnisse.** AES-256-GCM, Versionsbyte, 96-Bit-Nonce aus `os.urandom`, Kontext `b"webhook:" + id` als Associated Data – ein Geheimnis lässt sich nicht einem anderen Webhook unterschieben (`cipher.py`, `management.py:32-34`). Klartext nur in `CreatedWebhook.secret` (`repr=False`) und in der Antwort auf Anlegen/Erneuern; `WebhookOut` enthält kein Geheimnis, `Webhook` hat `repr=False` auf beiden Feldern. Erneuern: altes Geheimnis signiert mit für `PAPIQ_WEBHOOK_SECRET_GRACE`, erneutes Erneuern beendet das sofort (`renew_secret`), `encrypted_secrets(now)` ignoriert ein abgelaufenes Vorgänger-Geheimnis auch vor dem Aufräumen, und der Aufräumjob löscht es (`drop_expired_secret`, `maintenance.py:142,168-176`; auch defensiv, wenn `previous_valid_until` fehlt). Schlüssel per `PAPIQ_SECRET_KEY(_FILE)`, 32 Byte erzwungen, Dev-Schlüssel in Produktion abgewiesen (`settings.py:275-292`). Entschlüsselung nur zum Signieren, nicht geloggt, nicht gespeichert (`delivery.py:283-288`).

**5. Zustellprotokoll.** Zeile je Versuch mit Zeit, Ereignis, Dokument-ID, Ergebnis, Status, Dauer, Fehler (≤ 300 Zeichen), nächster Versuch (`WebhookDelivery`); Antwortkörper weder gelesen noch gespeichert; Fehlertexte nennen nur die Art (`_describe`, Port-Vertrag „does not contain the URL“ mit Test `test_errors_do_not_name_the_url`); `DeliveryOut` spiegelt genau diese Felder. `dropped` für Weg und Verborgen mit demselben Text (`DOCUMENT_UNAVAILABLE`). Seitenweise mit `before`, auf den eigenen Webhook eingeschränkt (`sql/webhooks.py:78-86`). Aufgeräumt nach `PAPIQ_RETENTION` (`purge_deliveries`); `ON DELETE CASCADE` zu Webhook und Besitzer.

**6. Reichweite.** Fan-out mit `in_reach` = `reach_access` ohne Admin-Rechte: inaktiver Nutzer nie, Besitzer immer, andere nur `GREEN` in erreichbaren Schubladen (`permissions.py:51-70`); `document.deleted` über die im Ereignis festgehaltenen Leser; `document.filed` ohne Lane übersprungen (`delivery.py:402-419`). Vor jedem Versuch erneut: Webhook aktiv, Besitzer aktiv, Dokument noch in Reichweite (`_reason_to_drop`). Verwaltung: Besitzer oder Admin, Fremde sehen 404 (`visible_webhook`); Admins sehen URL und Protokoll anderer – dokumentierte Entscheidung. `load_actor` lehnt deaktivierte Konten ab.

**7. Abschalten und Wiederholung.** 2xx zugestellt; kein Ergebnis, 5xx, 408/425/429 wiederholt (`_is_temporary`), alles andere endgültig; Abstände `30 s · 2^(n-1)`, höchstens 1 h, höchstens `PAPIQ_WEBHOOK_MAX_ATTEMPTS` (`DeliveryRetry.next_attempt`, Shift auf 40 begrenzt). Nach `PAPIQ_WEBHOOK_DISABLE_AFTER` aufgegebenen Zustellungen in Folge `active=false`, `disabled_reason=failing`; Einschalten setzt den Zähler zurück (`gave_up`, `change`). Ergebnis, Zähler und Job in einer Transaktion, drei Runden bei Versionskonflikt (`_settle_attempt`). Dedup-Schlüssel `<webhook>:<event>` gegen doppelte Jobs; Freigabe des Jobs bei Worker-Stopp (`_release`, `asyncio.shield`).

**8. Drossel des Testaufrufs.** Nach der Rechteprüfung (ein 404 kostet nichts), je Nutzer, fest 10 je Minute (`WebhookPolicy.test_requests`/`test_window`, nicht über `PAPIQ_`-Variablen einstellbar), `TooManyAttemptsError` mit `Retry-After` (`delivery.py:174-181`, `problems.py:137-139`); prozesslokal und so dokumentiert (README, Docstring). Test gegen Fenster und Nutzertrennung vorhanden (`test_test_requests_are_limited_per_user_and_window`). Hinweis: die Meldung lautet „too many failed attempts“, gemeint sind Testaufrufe – kosmetisch.

**9. Payload.** Nur `id`, `type`, `occurred_at`, `document_id` (`event_body`); Jobs tragen dieselben Felder plus `webhook_id`; der Testaufruf `webhook.test` mit `document_id: null`. Keine Lane, keine Namen, keine Nutzer.

**10. LLM- und Embedding-Client.** API-Schlüssel nur im `Authorization`-Header, nie im Log (httpx-Logger auf WARNING, `logging_setup.py:14,55`), nie im Fehlertext (`_post` nennt Host:Port und Fehlertyp; Test `test_errors_do_not_show_the_key`); Weiterleitungen werden nicht gefolgt (httpx2-Standard, und `httpx2` streicht `Authorization` bei Origin-Wechsel); Antwort muss JSON-Objekt sein, `choices[0].message.content` als Text, Embeddings nach `index` sortiert, Anzahl und gleiche Länge geprüft, Länge zusätzlich gegen `PAPIQ_EMBEDDING_DIMENSIONS` in Meilisearch-Adapter und Suchdienst. Anfragegröße: Dokumenttext auf `PAPIQ_LLM_INPUT_BUDGET` gekürzt, Tags auf `PAPIQ_LLM_MAX_TAGS`, Suchanfrage auf 500 Zeichen (REST und MCP), Index-Text auf `PAPIQ_SEARCH_MAX_TEXT`/`MAX_CHUNKS`. Prompt-Injection: Dokumenttext zwischen Markern mit SHA-256-Präfix des Textes (kann seine eigene Schlussmarke nicht enthalten), explizite Datenregel, Antwort nur im strikten Schema, jede Angabe wird im Kern gegen den Text und die Stammdaten geprüft (`prompts.py`, README „Classification“). Hinweise ohne Befund: `_message` übernimmt bis zu 300 Zeichen der Provider-Fehlermeldung in `LanguageModelError`, die im Verarbeitungsprotokoll des Besitzers landet – Provider maskieren Schlüssel in solchen Meldungen, ein Proxy muss das nicht tun; `trust_env=True` (Proxy-Variablen wirken) ist für vom Admin konfigurierte Endpunkte vertretbar, der Unterschied zum Webhook-Sender ist bewusst.

**11. Meilisearch.** Schlüssel als Bearer-Header; `GET /version` im Health-Check beweist, dass er angenommen wird. Filter nur aus IDs (UUID-Typen in `DocumentFilter`/`Visibility`) und Lane-Namen, alle mit `json.dumps` in Anführungszeichen (`filter_expression`, `_quote`); der Suchtext geht ausschließlich in `q`. Rechtefilter ist Teil jeder Anfrage und wird im Dienst gegen die Datenbank wiederholt (`search.py:128-140`). Highlight-Marken sind Private-Use-Zeichen und werden beim Indexieren aus dem Text entfernt (`_unmarked`), ein Dokument kann keine Treffer fälschen. Indexname per Regex in den Settings. Zeitlimits je Anfrage und je Task mit Deadline (`_wait`); Ausfälle werden zu `SearchUnavailableError` (503/degraded), Fehlermeldungen auf 300 Zeichen gekürzt.

**12. S3.** Zugangsdaten nur in der aioboto3-Session; TLS-Verifikation Standard; Pfadstil konfigurierbar; `check_key`: Segmente `[A-Za-z0-9_-][A-Za-z0-9._-]*`, keine leeren Segmente, kein `..`, kein führender Schrägstrich; Bucket fest; Retries begrenzt; Download über temporäre Datei mit `replace`. Hinweis: bei `PAPIQ_LOG_LEVEL=debug` loggen `botocore.endpoint`/`botocore.auth` komplette Anfragen mit `Authorization`-Header (Access-Key-ID und Request-Signatur, nicht der Secret-Key) – nur die httpx-Logger sind gedeckelt. Ebenfalls Hinweis: `external_endpoints` warnt für LLM, Embeddings und Meilisearch, nicht für `s3_endpoint_url`, obwohl dort die Originale liegen.

**13. OIDC.** Issuer muss `https` sein, Discovery-`issuer` muss exakt gleichen; Discovery und JWKS einmal geholt, JWKS einmal erneut bei unbekannter `kid`; Zeitlimit 10 s je Anfrage, auch im Authlib-Client; TLS verifiziert; nur asymmetrische, angekündigte Algorithmen; `iss`, `sub`, `aud`/`azp`, `exp`/`iat` mit 60 s, `nonce` konstantzeitig; nichts von Codes oder Tokens im Log (Fehlertyp, Fehlercode ≤ 100 Zeichen, Claim-Namen). Siehe 2-02 für die Endpunkte.

**14. `composition/endpoints.py`.** Warnung beim Start für jeden Modell-/Suchendpunkt außerhalb des lokalen Netzes (`__main__.py:111-116`); `is_local_host` erkennt Loopback, private und Link-Local-Adressen, `localhost`, Namen ohne Punkt (Docker-Dienste) und die Suffixe `.local`, `.internal`, `.lan`, `.home.arpa`, `.localhost`. Tailscale-Adressen (100.64/10) gelten als extern – harmloser Fehlalarm.

**15. Logging.** `httpx`, `httpx2`, `httpcore` auf mindestens WARNING, auch bei `PAPIQ_LOG_LEVEL=debug` (`max(WARNING, root)`), also keine Webhook- oder Modell-URLs im Log; Sender loggt nur die Fehlerart; Zustellung loggt IDs; `settings.describe()` maskiert `SecretStr` und `user:pass@` in URLs (`_mask_userinfo`); Uvicorn-Access-Log blendet die OIDC-Callback-Query aus; `deploy/secrets/` ist in `.gitignore`.

## 3 Image und Betrieb

Stand: Branch `claude/project-thread-7jl9zr`, Commit `e593168`. Gelesen: `CLAUDE.md`, `.idea/architektur.md` (Betrieb), `deploy/README.md`, `Dockerfile`, `.dockerignore`, `deploy/` (Compose-Stacks, `create-secrets.sh`, `garage-init/`, `garage.toml`, `image/rootfs/` komplett, `test-image.sh`), `.devcontainer/` (`compose.yml`, `dev.env`, `devcontainer.json`, `devcontainer-lock.json`), `.github/workflows/ci.yml`, `.github/compose.ci.yml`, `backend/src/papiq/composition/{settings,__main__,api,database,logging_setup}.py`, `adapters/inbound/rest/health.py`.

Praktisch: Runtime-Image auf dem Mac (arm64, OrbStack) gebaut, `deploy/test-image.sh` gelaufen, eigene Container `papiq-review-*` für Prozesse, Rechte, Geheimnisse, Pfadprüfung, Health und Herunterfahren; alles danach entfernt. Details unter „Geprüft und in Ordnung“.

| Nr. | Schwere | Befund |
| --- | --- | --- |
| 3.1 | mittel | `init-papiq` lässt `..` in `PAPIQ_DB_SQLITE_PATH`/`PAPIQ_STORAGE_PATH` durch: `/data/../etc` übergibt `/etc` samt `passwd` und `shadow` per `chown -R` an PUID:PGID |
| 3.2 | niedrig | Liste der Systemordner in `init-papiq` ohne `var` (und `tmp`); `/var` als Ablage wird angenommen und rekursiv umgehängt |
| 3.3 | niedrig | README verspricht, der Dev-Schlüssel werde abgelehnt; er wird nur mit `PAPIQ_COOKIE_SECURE=true` abgelehnt, die Beispiel-Stacks laufen mit `false` |
| 3.4 | niedrig | Setuid-Programme aus Debian (`su`, `passwd`, `mount`, …) bleiben im Runtime-Image; mit 3.1 ein Weg zu root im Container |
| 3.5 | niedrig | `VOLUME /data` im Dockerfile erzeugt anonyme Volumes für jeden Container ohne eigenes Volume (Postgres/S3-Stack, `docker run` ohne `-v`) |
| 3.6 | niedrig | `test-image.sh`: Lücken (Geheimnisse in `docker inspect` und `/run/s6/container_environment`, Pfadprüfung über `/` hinaus, beschäftigtes Herunterfahren, `degraded`, nur `papiq.composition`-Prozesse auf root geprüft); Anzahl ist 42, nicht 31 |
| 3.7 | niedrig | CI: Actions nur per Major-Tag gepinnt (`@v4` … `@v7`), nicht per Commit-SHA |
| 3.8 | niedrig | Dockerfile: Basis-Images nur per Tag, nicht per Digest; Dev-Stufe führt `curl … | bash` (Claude-Installer) ungepinnt aus |
| 3.9 | niedrig | Devcontainer: Docker-outside-of-Docker (Host-Socket, root-äquivalent) und `GH_TOKEN` aus der Host-Umgebung sind nirgends dokumentiert |
| 3.10 | niedrig | `/opt/papiq/venv/.lock` ist 0666 (world-writable) im Image |

### 3.1 Pfadprüfung in `init-papiq` lässt `..` durch (mittel)

**Problem.** `safe()` prüft nur das zweite Pfadsegment (`cut -d/ -f2`) gegen eine Liste von Systemordnern. Ein Pfad mit `..` besteht die Prüfung, und `own()` ruft anschließend `chown -R` auf den aufgelösten Ordner. Praktisch geprüft: `PAPIQ_DB_SQLITE_PATH=/data/../etc/papiq.db` startet ohne Fehler, Log `init-papiq: giving /data/../etc to 1000:1000`; danach gehören `/etc` (755), `/etc/passwd` (644) und `/etc/shadow` (640) dem Nutzer 1000:1000, Papiq läuft mit der Datenbank in `/etc/papiq.db`. Der Papiq-Prozess (unprivilegiert, verarbeitet fremde PDFs mit Ghostscript, Tesseract, Docling) kann dann `/etc/passwd` schreiben; zusammen mit dem setuid-`su` (3.4) ist das root im Container. Die README (`deploy/README.md:130-131`) und der Umsetzungsplan versprechen genau diesen Schutz („refuse relative paths and system folders“). Auslöser ist zwar ein Tippfehler des Betreibers, aber genau dafür gibt es die Prüfung.

**Fundstelle.** `deploy/image/rootfs/etc/s6-overlay/scripts/init-papiq:26-37` (`safe`), `:41-47` (`own`), `:53-54`, `:63-64`.

**Vorschlag.** Pfad vor der Prüfung normalisieren und `..` ablehnen:

```sh
safe() { # safe VARIABLE FOLDER
  case "/$2/" in */../*|*/./*) fail "$1: '$2' must not contain . or .. components" ;; esac
  resolved=$(realpath -m -- "$2")   # coreutils, im Image vorhanden
  ...weiter mit "$resolved" statt "$2"...
}
```

und den aufgelösten Pfad auch an `own()` geben. Zusätzlich im Test (`test-image.sh`, Abschnitt „invalid configuration“): ein Container mit `PAPIQ_DB_SQLITE_PATH=/data/../etc/papiq.db` muss beendet werden und `must not contain` loggen; optional nach dem Lauf `stat -c %u /etc/passwd` = 0 in einem laufenden Container prüfen.

### 3.2 Systemordner-Liste ohne `var` und `tmp` (niedrig)

**Problem.** Die Liste in `safe()` enthält `bin boot dev etc lib lib64 opt proc root run sbin sys usr`, aber nicht `var` (dort `lib/dpkg`, `lib/apt`, `log`, `cache`) und nicht `tmp`. Praktisch geprüft: `PAPIQ_STORAGE_PATH=/var/objects` wird angenommen (neuer Ordner, harmlos); `PAPIQ_STORAGE_PATH=/var` oder `/var/lib` würde den ganzen Baum rekursiv umhängen. `/home`, `/srv`, `/mnt`, `/media` sind bewusst frei (Volume-Mountpunkte) und bleiben besser so.

**Fundstelle.** `deploy/image/rootfs/etc/s6-overlay/scripts/init-papiq:33`.

**Vorschlag.** `tmp` und `var` ergänzen. Test: `PAPIQ_STORAGE_PATH=/var/lib` wird mit `is a system folder` abgelehnt.

### 3.3 Dev-Schlüssel wird nur mit sicheren Cookies abgelehnt (niedrig)

**Problem.** `deploy/README.md:72`: „The key from `.devcontainer/dev.env` is public and refused.“ Tatsächlich prüft `settings.py` den Schlüssel nur, wenn `cookie_secure` wahr ist. Beide Beispiel-Stacks setzen `PAPIQ_COOKIE_SECURE: "false"` (`compose.sqlite.yml:39`, `compose.postgres.yml:36`); wer dort den Dev-Schlüssel einträgt, startet ohne Einwand. Praktisch geprüft: Container mit `PAPIQ_SECRET_KEY=<Dev-Schlüssel>` und `PAPIQ_COOKIE_SECURE=false` läuft und wird healthy; mit `PAPIQ_COOKIE_SECURE=true` + `PAPIQ_FORWARDED_ALLOW_IPS` endet er mit Exit 1 wie erwartet. Die Bedingung ist absichtlich (der Devcontainer braucht den Schlüssel), nur die Dokumentation ist zu knapp.

**Fundstelle.** `backend/src/papiq/composition/settings.py:286-290`; `deploy/README.md:72`.

**Vorschlag.** README präzisieren („refused as soon as `PAPIQ_COOKIE_SECURE` is `true`; the example stacks run over plain HTTP, so replace it before exposing Papiq“), oder ergänzend eine Warnung im Log ausgeben, wenn der Dev-Schlüssel mit `cookie_secure=false` läuft (`log.warning` in `__main__.py` nach `configuration valid`).

### 3.4 Setuid-Programme im Runtime-Image (niedrig)

**Problem.** `find / -xdev -perm -4000` im laufenden Container: `/usr/bin/{chfn,chsh,gpasswd,mount,newgrp,passwd,su,umount}` (aus Debian) und `/package/admin/s6-overlay-helpers-0.1.2.2/command/s6-overlay-suexec` (gewollt, s6). Die Papiq-Prozesse laufen unprivilegiert und brauchen keines davon. Ohne 3.1 ist das nur Härtung; mit 3.1 wird aus einem beschreibbaren `/etc/passwd` echter root im Container.

**Fundstelle.** `Dockerfile:178-214` (Stufe `runtime`, kein Entfernen).

**Vorschlag.** In der Stufe `runtime`:

```dockerfile
RUN find / -xdev -type f -perm /6000 ! -path '/package/*' -exec chmod a-s {} +
```

Test in `test-image.sh`: `docker exec … find / -xdev -type f -perm /6000 ! -path '/package/*'` liefert nichts.

### 3.5 `VOLUME /data` erzeugt anonyme Volumes (niedrig)

**Problem.** Das Image deklariert `VOLUME /data`. Jeder Container, der kein Volume auf `/data` bekommt, erhält ein anonymes Volume: im Postgres/S3-Stack (`compose.postgres.yml` hat keine `volumes:` für `papiq`), bei `docker run` ohne `-v` und bei allen Negativ-Containern in `test-image.sh`. Praktisch geprüft: drei Testcontainer ohne `-v` hatten je ein anonymes Volume (`docker inspect … .Mounts`). `docker compose down` ohne `-v` und `docker rm` ohne `-v` lassen sie liegen; `test-image.sh` räumt mit `rm -fv` korrekt auf. Datenverlust droht nicht (mit Postgres/S3 bleibt `/data` leer), es sammelt sich nur Müll.

**Fundstelle.** `Dockerfile:210`; `deploy/compose.postgres.yml:12-95`.

**Vorschlag.** Entweder `VOLUME` streichen (die Compose-Dateien deklarieren das Volume ohnehin; die README nennt `/data`) oder im Postgres-Stack `tmpfs: [/data]` bzw. in der README unter „User and volumes“ einen Satz ergänzen, dass ein Container ohne eigenes `/data`-Volume ein anonymes bekommt (`docker compose down -v`).

### 3.6 `test-image.sh`: Umfang und Lücken (niedrig)

**Stand.** 42 Einzelprüfungen (`ok:`-Zeilen), nicht 31 wie im Umsetzungsplan (`.idea/umsetzungsplan.md:254`) genannt; Laufzeit hier 1:19 min, alle bestanden. Geprüft werden Image-Env, PID 1, Rolle `all` (Health, Migration, Nutzer, `/data`-Eigentümer, Geheimnisse in Logs, `papiq`-Helfer), Web-UI (11), PDF bis Lane, `docker stop` leer, Rollen `api`/`worker`, vier ungültige Konfigurationen.

**Lücken.**

1. Geheimnisse werden nur in den Logs gesucht (`:128-131`), nicht in `docker inspect` des laufenden Containers und nicht in `/run/s6/container_environment` (die README `:62-63` nennt genau diesen Ort). Ergänzung: `docker inspect $prefix-all | grep -cF -f "$work/secret_key"` = 0 und `docker exec … grep -rlF -f … /run/s6/container_environment` leer.
2. Root-Prüfung nur für Prozesse mit `papiq.composition` im Kommando (`:122-125`). Ergänzung: jeder Prozess, dessen Kommando nicht mit `s6-`/`/package/` beginnt, läuft als `$uid:$gid` (deckt auch gestartete OCR-/Docling-Kindprozesse).
3. Pfadprüfung nur mit `/` (`:222-225`); fehlen: relativer Pfad, Systemordner, `..` (siehe 3.1), `PUID=abc`, `PAPIQ_SECRET_KEY` und `_FILE` gleichzeitig.
4. Herunterfahren nur leer und mit Docker-Standard-Timeout 10 s (`:182`). Ergänzung: PDF hochladen, sofort `docker stop -t 60`, Exit 0, nach `docker start` erreicht das Dokument eine Lane (praktisch geprüft, funktioniert: Stopp in 2 s, Lane gelb nach Neustart).
5. Health `degraded`: ein Container mit `PAPIQ_MEILISEARCH_URL=http://127.0.0.1:1` muss healthy werden und `"status":"degraded"` mit HTTP 200 liefern (praktisch geprüft, funktioniert).
6. Dateimodi/umask in `/data` (`644`/`755`, umask 022) werden nicht festgehalten.
7. „no secret in the build“ (`:102-107`) sieht nur `Config.Env`, nicht `docker history`; mit `--mount=type=secret` ist das akzeptabel, ein Kommentar würde es erklären.

**Fundstelle.** `deploy/test-image.sh` (Zeilen oben); `.idea/umsetzungsplan.md:254` (Zahl 31).

### 3.7 CI: Actions per Major-Tag (niedrig)

**Problem.** `actions/checkout@v6`, `astral-sh/setup-uv@v7`, `actions/cache/{restore,save}@v5`, `pnpm/action-setup@v6`, `actions/setup-node@v7`, `docker/setup-buildx-action@v4`, `docker/build-push-action@v7` sind bewegliche Tags. Mit `permissions: contents: read` und nur `HF_TOKEN` als Geheimnis ist der Schaden einer kompromittierten Action klein (Lesezugriff, HF-Token), aber die Devcontainer-Features sind per Digest gepinnt (`devcontainer-lock.json`) und das Dockerfile pinnt s6 per SHA-256; die CI fällt dagegen ab.

**Fundstelle.** `.github/workflows/ci.yml:53,120-121,127,136,154-155,176-180,186,193,232,245,256,291,296,351-352,357`.

**Vorschlag.** Auf Commit-SHAs pinnen (`uses: actions/checkout@<sha> # v6.x`) und Dependabot/Renovate für `github-actions` aktivieren.

### 3.8 Dockerfile: Tag-Pins und `curl | bash` in der Dev-Stufe (niedrig)

**Problem.** `python:3.13.13-slim-trixie`, `node:24.21.0-trixie-slim`, `ghcr.io/astral-sh/uv:0.11.33`, `alpine:3.24.2`, `postgres:17.11-trixie`, `dxflrs/garage:v2.4.1`, `getmeili/meilisearch:v1.54.3` sind per Tag, nicht per Digest gepinnt; Tags sind beweglich (Debian-Patches fließen so ein, was man oft will). s6-overlay ist per SHA-256 gepinnt und aktuell (v3.2.3.2 = neueste Release, am 09.10.2026 geprüft); uv 0.11.33 ist gepinnt, upstream 0.12.24. Die Dev-Stufe führt `curl -fsSL https://claude.ai/install.sh | bash` ungeprüft aus (`Dockerfile:171`), als `vscode` mit passwortlosem `sudo`; nur im Devcontainer, nicht im Runtime-Image.

**Fundstelle.** `Dockerfile:23-24,31,116,171`; `deploy/compose.*.yml`, `deploy/garage-init/Dockerfile:1`.

**Vorschlag.** Für das veröffentlichte Image (M13) Basis-Images per `@sha256:` pinnen und per Renovate nachziehen; den Installer in der Dev-Stufe per Version fixieren oder seine Prüfsumme vergleichen.

### 3.9 Devcontainer: Docker-Socket und Host-Token undokumentiert (niedrig)

**Problem.** `devcontainer.json:9` bindet `docker-outside-of-docker` ein: der Host-Docker-Socket im Container ist root-äquivalent auf dem Host; `vscode` hat passwortloses `sudo` (`Dockerfile:140`). `GH_TOKEN` kommt aus `GH_DEVCONTAINER_TOKEN` des Hosts (`devcontainer.json:13`) und steht in `docker inspect` des Dev-Containers. Beides ist für die Entwicklung sinnvoll, aber weder `README.md` (Abschnitt Devcontainer, `:48-66`) noch `.devcontainer/*` sagen es; die Dev-Zugangsdaten dagegen sind klar markiert (`dev.env:1-2`, `compose.yml:1`, `README.md:51`). Ports: `forwardPorts` leitet nur auf `localhost` des Hosts, `compose.yml` veröffentlicht nichts; `compose.ci.yml` öffnet 5432/3900/7700 nur auf dem flüchtigen Runner.

**Fundstelle.** `.devcontainer/devcontainer.json:9,13`; `README.md:48-66`.

**Vorschlag.** Zwei Sätze im Root-README: der Devcontainer sieht den Docker des Hosts (nötig für `docker build`/`test-image.sh` und die Paperless-Instanz), also nur auf vertrauenswürdigen Rechnern öffnen; `GH_DEVCONTAINER_TOKEN` ist optional und sollte ein fein-granulares Token sein.

### 3.10 World-writable `.lock` im venv (niedrig)

**Problem.** `/opt/papiq/venv/.lock` ist `0666 root` (uv legt es an). Einzige world-writable Datei im Image (`find / -xdev -type f -perm -o+w` außerhalb `/proc,/sys,/data,/run,/tmp,/dev`). Praktisch bedeutungslos, aber ein Scanner meldet es.

**Fundstelle.** `Dockerfile:181` (`COPY --from=app /opt/papiq/venv`).

**Vorschlag.** In der Stufe `app` nach `uv sync`: `rm -f /opt/papiq/venv/.lock` oder `chmod 644`.

### Geprüft und in Ordnung

**Bau und Test**

- `docker build --target runtime -t papiq:review .` (arm64, OrbStack, mit Layer-Cache): 1:53 min; Image 1,41 GB Inhalt / 4,58 GB „disk usage“ laut `docker image ls` (README: „about 3 GB“, CI misst 3,0–3,2 GB; plausibel). `Config.Env` ohne Geheimnis; `Entrypoint ["/init"]`.
- `deploy/test-image.sh papiq:review`: „All checks passed.“, 42 Prüfungen, 1:19 min. PDF bis Lane gelb (ocr ok, parse ok, classify uncertain).
- `docker compose -f compose.sqlite.yml config --quiet` und `compose.postgres.yml`: gültig; einziger veröffentlichter Port `127.0.0.1:8000`. Meilisearch-Image `v1.54.3` enthält `curl` (Healthcheck funktioniert).

**Rechte im Container** (eigener Container `papiq-review-all`, PUID=1234, PGID=4321)

- Prozessliste aus `/proc`: als root nur `s6-svscan` (PID 1), fünf `s6-supervise`, `s6-linux-init-shutdownd`, `s6-ipcserverd`; `python -m papiq.composition api` und `worker` als `1234:4321`, Zusatzgruppen `[4321]` (`s6-applyuidgid -G ""` wirkt), umask 0022. `docker top` bestätigt.
- `papiq-run id` → `uid=1234 gid=4321 groups=4321`, `HOME=/run/papiq-home` (0700, 1234:4321); `papiq check-schema` als derselbe Nutzer (test-image).
- `/data` 755, `papiq.db*` 644, alles 1234:4321. Marke `/run/papiq-worker-ready` root 644 (nur Existenz zählt).
- `PUID=0` abgelehnt (test-image), `PUID=abc` abgelehnt („must be numbers“), relativer Pfad abgelehnt („must be an absolute path“), `/` abgelehnt (test-image). Ausnahmen: 3.1, 3.2.

**Geheimnisse**

- Beispiel-Stacks: ausschließlich `_FILE`-Variablen für Papiq; `meilisearch.env` als dokumentierte Ausnahme (`README:72-74`), `create-secrets.sh` legt 0644 in 0700-Ordner, Garage 0600; `deploy/secrets/` ist in `.gitignore:35`, `.dockerignore` lässt nur `deploy/image/` in den Build-Kontext.
- `docker inspect` des Containers: 0 Treffer für Schlüssel und Admin-Passwort; `docker logs`: 0 Treffer. Log „configuration valid“ zeigt `"secret_key": "**********"` (SecretStr, `describe()`); Validierungsfehler lassen bei Secret-Feldern den Wert weg (`settings.py:452-460`); HTTP-Client-Logger auf WARNING, OIDC-Callback-Query ausgeblendet (`logging_setup.py:53-81`).
- `/run/s6/container_environment`: nur Pfade (`PAPIQ_*_FILE`), Dateien 0644 root. Gegenprobe mit `PAPIQ_SECRET_KEY` als Klartext-Variable: Wert in `docker inspect` und in `/run/s6/container_environment/PAPIQ_SECRET_KEY` sichtbar, genau wie `README:62-63` warnt.
- Secret-Dateien im Container 0644 root (Bind-Mount), lesbar für PUID; die README verlangt genau das (`:60-61`).
- Build-Secret `hf_token` nur als `--mount=type=secret` (`Dockerfile:74`), nicht im Image.

**Healthchecks**

- API: `/api/v1/health` → `{"status":"ok","checks":{"database":"ok","object_store":"ok"}}`; mit unerreichbarem Meilisearch (`papiq-review-degraded`): `"status":"degraded"`, HTTP 200, Container healthy nach 18 s; `unavailable` → 503 im Code (`health.py:36-38`). Healthcheck-Requests nicht im Access-Log (`DropHealthChecks`).
- Worker: `s6-svstat -o up,updownfor /run/service/svc-worker` → `true 38`, Skript wertet ≥ 10 s plus Marke aus; `papiq-healthcheck` Exit 0. Wartender Worker ist unhealthy (test-image). Hängender Worker nicht erkannt: dokumentiert `README:149-150`.
- `HEALTHCHECK --start-period=120s --interval=30s --timeout=10s --retries=3` (`Dockerfile:212`), Health-Timeout pro Check 5 s (`health.py:16`) passt darunter.

**Herunterfahren**

- Kette 30 s (`worker_shutdown_timeout`) < 40 s (`S6_SERVICES_GRACETIME`) < 60 s (`stop_grace_period`), `S6_KILL_GRACETIME` 1 s, Uvicorn 10 s (`api.py:24`): konsistent in `Dockerfile:189-198`, beiden Compose-Dateien (`:19-20`) und `README:157-173`.
- Leer: Stopp in 2 s, Exit 0, „worker stopped“ und „Finished server process“ geloggt (test-image). Beschäftigt: PDF hochgeladen, 2 s später `docker stop -t 60` → 2 s, Exit 0; nach `docker start` wurde das Dokument verarbeitet (Lane gelb, alle Schritte ok): Job zurückgegeben und wieder aufgenommen.
- Migration nicht unterbrechbar, eine Transaktion, dokumentiert `README:171-173`; der Worker wartet deshalb in `svc-worker` statt in einem Oneshot (`init-migrations:3-4`), SIGTERM beendet das Warten über die `trap` (`svc-worker/run:19`); wartender Worker stoppt in 1 s (test-image).

**Rollen**

- `PAPIQ_ROLE` wird in jedem Dienstskript geprüft, falscher Dienst mit `s6-svc -Od .` dauerhaft unten; `api` ohne Worker-Prozess, `worker` ohne API, Worker migriert nie und wartet mit `check-schema --wait 600` (`database.py:41-66`: Poll 2 s, Meldung je 60 s, `newer` sofort), danach alle 10 s neuer Versuch (`svc-worker/run:22-28`); ungültige Rolle abgelehnt (alles test-image). Update-Reihenfolge und Ablehnung einer neueren Datenbank dokumentiert `README:119-124,301-306`.

**Dockerfile**

- Pins: Python 3.13.13, Node 24.21.0, uv 0.11.33, pnpm 12.9.1, s6-overlay 3.2.3.2 mit SHA-256 je Archiv (`:86-99`, aktuellste Version), Alpine 3.24.2, Postgres 17.11, Garage v2.4.1, Meilisearch v1.54.3. Mehrarchitektur über `TARGETARCH` (s6) und `--platform=$BUILDPLATFORM` (Web-Build), CI baut nativ auf amd64 und arm64. `S6_BEHAVIOUR_IF_STAGE2_FAILS=2`: fehlender Init stoppt den Container (Exit 1, test-image). `UV_NO_CACHE`, apt-Listen entfernt, kein Node im Runtime-Image.

**CI**

- `permissions: contents: read` global (`ci.yml:9-10`); Caches schreiben nur Pushes auf `main` (`WRITE_CACHE`, `:23-24,134,191,255,303`), Pull Requests lesen; `HF_TOKEN` nur in den drei Modell-Downloads (`:253,304,362`), Ereignis `pull_request` (nicht `pull_request_target`); Timeouts 5/10/5/10/15/30/30 min; `cancel-in-progress` nur für PRs; Pfadfilter per `if` statt `paths`, damit übersprungene Jobs als bestanden zählen.

**Devcontainer**

- Dev-Zugangsdaten nur in `dev.env`/`compose.yml`, als solche markiert; keine `ports:` in `compose.yml`, `forwardPorts` nur nach localhost; Features per Digest gelockt (`devcontainer-lock.json`). Devcontainer `papiq-dev-*` während des Reviews nicht berührt (danach weiterhin „Up“).

**Aufräumen**

- `docker rm -fv papiq-review-*` (10 Container inkl. anonymer Volumes), `docker volume rm papiq-review-data`, `docker rmi papiq:review`: keine Reste (`docker ps -a`, `docker volume ls`, `docker images` je 0). `git status` sauber, keine Datei im Repo geändert.

## 4 Architektur

Geprüfter Stand: Branch `claude/project-thread-7jl9zr` (Commit `80bd199`, Merge von PR #28). Grundlage: `CLAUDE.md` (Architekturregeln), `.idea/architektur.md` (verbindlich), `backend/README.md`, `backend/pyproject.toml` (import-linter-Verträge). Vorgehen: nur Lesen (Quelltext, Tests, Grep über die Importe); `lint-imports` wurde nicht ausgeführt, die Verträge wurden gegen die tatsächlichen Importe von Hand geprüft.

Keine Befunde der Schwere hoch. Die hexagonale Struktur ist sauber umgesetzt: der Kern importiert ausschließlich die Standardbibliothek und `papiq.core`, Ports sind Protocols, die Verdrahtung liegt allein in `composition`, Zustand, Ereignisse und Folgejobs gehen durch eine Unit of Work. Die Befunde betreffen Lücken in den Verträgen des import-linters (Kopplungen zwischen Adaptern derselben Richtung), fehlende Indizes, eine Regex-Validierung, die den Port umgeht, sowie veraltete Stellen in `architektur.md` und `README.md`.

| Nr. | Schwere | Befund |
| --- | --- | --- |
| A-01 | mittel | MCP-Adapter hängt vom REST-Adapter ab (Zyklus `rest.app` ↔ `mcp`); kein Vertrag deckt Inbound↔Inbound ab |
| A-02 | mittel | Regel-Muster werden im Kern mit `re` geprüft, aber mit `regex` ausgeführt: Validierung umgeht den Port |
| A-03 | niedrig | Outbound→Outbound-Abhängigkeiten (`docling`→`pdfium`, `ocrmypdf`→`pdfium`, beide→`system`) sind erlaubt, aber nicht als Vertrag ausgewiesen |
| A-04 | niedrig | `PatternMatcher` ohne In-Memory-Adapter; `MissingKeyCipher` ist ein Adapter im Composition Root |
| A-05 | niedrig | Fehlende Indizes für Dokumentfilter (`lane`, `contact_id`, `document_type_id`, `document_tags.tag_id`) und Aufräumabfragen (`outbox.recorded_at`, `sessions`, `event_retries.retry_at`, `jobs.locked_until`) |
| A-06 | niedrig | Keine Prüfung, dass jede `DomainError`-Unterklasse eine HTTP-Zuordnung hat (vier Klassen fehlen, heute nicht über REST erreichbar) |
| A-07 | niedrig | `architektur.md` veraltet: Kontakt/Typ „genau einer pro Dokument", „Nur die API spricht mit Meilisearch", Ports-Tabelle unvollständig |
| A-08 | niedrig | `README.md` veraltet: Endpunkt-Tabelle nennt „(owner)" für Protokoll, Retry, Reprocess, Löschen; Admins dürfen das ebenfalls |
| A-09 | niedrig | Kein Meta-Test, dass jede Vertragssuite vom In-Memory-Adapter und von jedem echten Adapter geerbt wird |

### A-01 (mittel): MCP-Adapter hängt vom REST-Adapter ab

**Fundstellen:** `backend/src/papiq/adapters/inbound/mcp/app.py:25-26` (`ApiContext`, `problem` aus `rest.context`, `rest.problems`), `backend/src/papiq/adapters/inbound/mcp/server.py:27-29` (`ApiContext`, `AttributeJson`, `RuleReportOut`, `attribute_json` aus `rest.schemas`), `backend/src/papiq/adapters/inbound/rest/app.py:14` (`McpEndpoint` aus `mcp`). `backend/pyproject.toml:90-96` (Vertrag „Inbound and outbound adapters are independent" prüft nur `inbound` gegen `outbound`).

**Problem:** Die beiden eingehenden Adapter REST und MCP importieren einander: `rest.app` baut den MCP-Endpunkt ein, `mcp.app`/`mcp.server` nehmen Kontext, Fehlerformat und Pydantic-Antwortschemata aus dem REST-Adapter. `architektur.md` (Zeile 300) sieht MCP „im API-Prozess, nutzt dieselbe Service-Schicht" vor; die Kopplung soll also über die Services (`core`) laufen, nicht über REST-Schemata. Heute ist MCP ohne REST nicht lauffähig, und eine Änderung an `rest/schemas.py` (z. B. ein Feld in `RuleReportOut`) ändert stillschweigend das MCP-Antwortformat. Der import-linter meldet das nicht, weil kein Vertrag die Inbound-Adapter untereinander trennt. `adapters/inbound/values.py` zeigt bereits das richtige Muster (gemeinsam genutzter Code ohne Zugehörigkeit zu einem Adapter).

**Vorschlag:** `ApiContext` und die gemeinsam genutzten Antworttypen (`AttributeJson`, `attribute_json`, `RuleReportOut`) nach `adapters/inbound/common/` (oder neben `values.py`) verschieben, `problem()` für 401 im MCP durch eine eigene kleine Antwort ersetzen oder ebenfalls in `common` legen. Das Einhängen des MCP-Endpunkts in die FastAPI-App gehört in `composition/api.py` (dort liegt die Entscheidung, welche Adapter laufen), nicht in `rest/app.py`. Danach Vertrag in `pyproject.toml`:

```toml
[[tool.importlinter.contracts]]
name = "Inbound adapters are independent of each other"
type = "independence"
modules = ["papiq.adapters.inbound.rest", "papiq.adapters.inbound.mcp", "papiq.adapters.inbound.worker", "papiq.adapters.inbound.cli", "papiq.adapters.inbound.evaluation"]
```

**Test:** Der Vertrag selbst (`uv run lint-imports`). Zusätzlich ein Unit-Test in `tests/unit/adapters/mcp`, der den MCP-Endpunkt ohne `create_app` aus `rest` aufbaut (nur `ApiContext`/Services), um zu belegen, dass MCP eigenständig läuft.

### A-02 (mittel): Regex-Validierung umgeht den Port

**Fundstellen:** `backend/src/papiq/core/domain/rules.py:24` (`import re`), `:958-962` (`_pattern`: `re.compile(str(value))`), `backend/src/papiq/core/ports/patterns.py:12-15` (Port: „ValidationError if the pattern is invalid"), `backend/src/papiq/adapters/outbound/regex/matcher.py` (Ausführung mit dem Paket `regex`), `backend/src/papiq/core/services/rules/running.py:160-164` (Fehler zur Laufzeit werden als „skipped" protokolliert, die Bedingung trifft nicht zu).

**Problem:** Beim Anlegen einer Regel wird ein `matches`-Muster mit der Standardbibliothek `re` geprüft, ausgeführt wird es mit `regex`. Die Syntax ist nicht identisch: `regex` kennt z. B. `\p{L}`, possessive Quantoren (`a++`), `(?V1)`, Mengenoperationen in Zeichenklassen; `re` lehnt sie ab, obwohl der Adapter sie ausführen könnte (`422` für ein gültiges Muster). Umgekehrt könnte ein Muster, das `re` annimmt und `regex` ablehnt, gespeichert werden und zur Laufzeit still nicht zutreffen (`running.py:162`). Architektonisch: Die Domäne kennt die Fähigkeiten des Adapters nicht; die Prüfung gehört an den Port, so wie die Docstring des Ports es vorsieht. Zugleich ist das ein verdecktes „Framework im Kern": der Kern legt sich mit `re` auf eine Regex-Engine fest, während der austauschbare Adapter eine andere nutzt.

**Vorschlag:** `PatternMatcher` um `validate(pattern: str) -> None` erweitern (oder `search(pattern, "", case_sensitive=False)` nutzen) und die Prüfung aus `rules.py:_pattern` in `RuleService.create/update` (`core/services/rules/management.py`) verlegen, wo der Port verfügbar ist; `Rule`/`Condition` prüfen dann nur noch Typ und Länge. `PatternMatcherContract` um einen Test „ungültiges Muster → ValidationError bei `validate`" ergänzen.

**Test:** Unit-Test im RuleService: ein Muster mit `\p{L}` wird angenommen, läuft im `ApplyRulesStep` auf einem Dokument mit Umlauten und trifft zu; ein Muster `(` wird mit `ValidationError` abgelehnt. Vertragstest für `validate` in `tests/contracts/patterns.py`, geerbt von `RegexPatternMatcher` (und dem neuen In-Memory-Adapter, siehe A-04).

### A-03 (niedrig): Outbound→Outbound-Abhängigkeiten ohne Vertrag

**Fundstellen:** `backend/src/papiq/adapters/outbound/docling/convert.py:21` (`pdfium.library.text_layer`), `backend/src/papiq/adapters/outbound/docling/parser.py:17` (`system.run_process`), `backend/src/papiq/adapters/outbound/ocrmypdf/engine.py:41-42` (`pdfium.count_pages`, `system.run_process`, `Completed`). `backend/pyproject.toml:81-96`.

**Problem:** Die Regel „Inbound- und Outbound-Adapter importieren einander nicht" wird eingehalten; zwischen Outbound-Adaptern gibt es jedoch Abhängigkeiten, die kein Vertrag benennt. `system` (Prozessstart mit Zeitlimit) ist geteilte Infrastruktur und gewollt; `pdfium` ist ein eigener Adapter (Port `PreviewRenderer`), dessen Bibliotheksfunktionen (`text_layer`, `count_pages`) zwei andere Adapter nutzen. Wird der Vorschau-Adapter gegen einen anderen getauscht, bleibt `pypdfium2` dennoch Pflicht für OCR und Parsen. Das ist tragbar, sollte aber sichtbar sein, damit neue Kopplungen (z. B. `s3`→`sql`) nicht unbemerkt entstehen.

**Vorschlag:** Vertrag in `pyproject.toml`, der die Outbound-Adapter voneinander trennt und die drei gewollten Ausnahmen aufführt:

```toml
[[tool.importlinter.contracts]]
name = "Outbound adapters are independent (shared: system, pdfium.library)"
type = "independence"
modules = ["papiq.adapters.outbound.crypto", "papiq.adapters.outbound.docling", "papiq.adapters.outbound.filesystem", "papiq.adapters.outbound.meilisearch", "papiq.adapters.outbound.ocrmypdf", "papiq.adapters.outbound.oidc", "papiq.adapters.outbound.openai_compat", "papiq.adapters.outbound.pdfium", "papiq.adapters.outbound.regex", "papiq.adapters.outbound.s3", "papiq.adapters.outbound.sql", "papiq.adapters.outbound.webhooks"]
ignore_imports = [
    "papiq.adapters.outbound.docling.convert -> papiq.adapters.outbound.pdfium.library",
    "papiq.adapters.outbound.ocrmypdf.engine -> papiq.adapters.outbound.pdfium",
]
```

(`system` und `memory` bleiben außen vor: `system` ist Hilfscode, `memory` wird von niemandem importiert.) Alternativ `pdfium.library` nach `adapters/outbound/pdf/` als ausgewiesene gemeinsame Bibliothek verschieben.

**Test:** Der Vertrag (`uv run lint-imports`).

### A-04 (niedrig): `PatternMatcher` ohne In-Memory-Adapter; Adapter im Composition Root

**Fundstellen:** `backend/src/papiq/composition/container.py:370` (`build_memory_container` nutzt den echten `RegexPatternMatcher`), `:332`; `backend/src/papiq/adapters/outbound/memory/__init__.py` (kein Pattern-Matcher); `backend/src/papiq/composition/container.py:229-237` (`MissingKeyCipher`, eine `SecretCipher`-Implementierung in `composition`).

**Problem:** `CLAUDE.md` und `architektur.md` (Zeile 57) verlangen je Port einen In-Memory-Adapter. Für `PatternMatcher` fehlt er (bekannt); dadurch hängen alle Kern- und REST-Tests, die Regeln mit `matches` berühren, am Paket `regex` und an dessen Thread-Timeout. `MissingKeyCipher` ist ein (bewusst scheiternder) Adapter, liegt aber in `composition` statt bei den Adaptern und hat keinen Vertragstest; `composition` soll nur verdrahten.

**Vorschlag:** `FakePatternMatcher` in `adapters/outbound/memory/patterns.py` auf `re` mit `asyncio.wait_for` um einen Thread (oder ohne Timeout, mit einer Liste absichtlich „langsamer" Muster für den Vertragstest) und in `build_memory_container` einsetzen. `MissingKeyCipher` nach `adapters/outbound/crypto/cipher.py` (neben `AesGcmCipher`) verschieben.

**Test:** `TestFakePatternMatcher(PatternMatcherContract)` in `tests/unit/adapters/memory/test_contracts.py`. Für `MissingKeyCipher` ein kleiner Test, dass `encrypt` `RuntimeError` und `decrypt` `DecryptionError` wirft (heute ungetestet).

### A-05 (niedrig): Fehlende Indizes

**Fundstellen:** `backend/src/papiq/adapters/outbound/sql/tables.py:140-145` (`contact_id`, `document_type_id`, `lane` ohne Index), `:158-163` (`document_tags`: PK `(document_id, tag_id)`, kein Index auf `tag_id`), `:269` (`jobs.locked_until`), `:293` (`outbox.recorded_at`), `:320` (`event_retries.retry_at`), `:358-359` (`sessions.last_seen_at`, `expires_at`). Abfragen: `repositories.py:242-251` (`_visible_to`: `lane = 'green' AND drawer_id IN (…)`), `:426-444` (`_query`: Filter nach `contact_id`, `document_type_id`, `tag_id`, `lane`), `event_bus.py:89-96` (`purge` nach `recorded_at`), `identity.py:186-189` (`sessions.purge`), `job_queue.py:81-85` (`claim`: `status = running AND locked_until <= now`).

**Problem:** Die geprüften Kernabfragen sind abgedeckt: Job-Claim über `(status, run_at)`, Dublette und Besitzer über den Unique-Index `(owner_id, sha256)` (die führende Spalte deckt `owner_id = ?`), Protokoll über `processing_log.document_id`, Liste über `drawer_id`. Nicht abgedeckt sind die Listenfilter nach Kontakt, Typ, Tag und Lane (Posteingang: `lane IN ('yellow','red')`), die Löschprüfungen `exists(contact=…)`, `exists(document_type=…)`, `exists(tag=…)` (`repositories.py:470-488`) und die Aufräumabfragen. Postgres legt für Fremdschlüssel keine Indizes an. Bei den geplanten 100 bis einigen tausend Dokumenten ist das unkritisch, es sind aber die Abfragen, die mit der Dokumentzahl wachsen und bei jeder Listenansicht laufen.

**Vorschlag:** Alembic-Revision `v0007_indexes` mit Indizes auf `documents(lane)`, `documents(contact_id)`, `documents(document_type_id)`, `document_tags(tag_id)`, `outbox(recorded_at)`, `sessions(expires_at)`, `sessions(last_seen_at)`, `event_retries(retry_at)`; `jobs`: zusätzlich `(status, locked_until)` oder den bestehenden Index zu `(status, run_at, locked_until)` erweitern. `tables.py` entsprechend (`index=True`), damit `test_migrations_match_the_table_definitions` grün bleibt.

**Test:** Der bestehende Schema-Vergleich (`tests/sql_suite.py:307`) sichert Tabellen und Migration gegeneinander. Ergänzend ein Postgres-Integrationstest, der `EXPLAIN` für `query_visible` mit Lane-Filter ausführt und keinen `Seq Scan` auf `documents` erwartet (nur sinnvoll mit genug Zeilen; alternativ weglassen und den Schema-Vergleich als Absicherung nehmen).

### A-06 (niedrig): Vollständigkeit der Fehlerzuordnung ungeprüft

**Fundstellen:** `backend/src/papiq/adapters/inbound/rest/problems.py:188-201` (`_STATUSES`), `backend/src/papiq/core/domain/errors.py` (`UnprocessableDocumentError`, `LanguageModelError`, `EmbeddingsError`, `PatternTimeoutError` ohne Eintrag; `ConcurrencyError` über `ConflictError` → 409; `OpenFieldsError`, `DuplicateDocumentError`, `SecondFactorRequiredError`, `TooManyAttemptsError` gesondert behandelt).

**Problem:** Vier `DomainError`-Unterklassen haben keine HTTP-Zuordnung und würden als `500` enden. Heute erreichen sie die REST-Schicht nicht: `UnprocessableDocumentError` und `LanguageModelError` entstehen nur in Worker-Schritten (`pipeline.py:479`), `EmbeddingsError` wird in `search.py:161` abgefangen (Rückfall auf Wörter), `PatternTimeoutError` in `running.py:162`. Das ist richtig, aber ungesichert: ein neuer Aufrufpfad (z. B. ein synchroner Klassifizierungs-Endpunkt oder die Mustervalidierung aus A-02 über den Port) macht daraus unbemerkt einen 500er. Die Zuordnung `SearchIndexError` → 503 „Service Unavailable" ist für „Index hat die Anfrage abgelehnt" (z. B. falsche Vektorlänge) eher 502/500, fällt aber nicht ins Gewicht.

Alle `except Exception` wurden geprüft: jede Stelle protokolliert (`log.exception`/`exc_info=True`) oder delegiert an eine Methode, die es tut (`indexing.py:203` → `_failed` `:230`, `webhooks/delivery.py:199` → `_failed` `:368-371`, `maintenance.py:112,150`, `pipeline.py:481,568`, `worker.py:111`, `health.py:44`, `events.py:159`, `sql/event_bus.py:213`, `memory/event_bus.py:85`, `composition/__main__.py`). `except BaseException` kommt nur mit `raise` zur Aufräumarbeit vor.

**Vorschlag:** Entweder jede Klasse zuordnen (`UnprocessableDocumentError` → 422, `LanguageModelError`/`EmbeddingsError`/`PatternTimeoutError` → 503 oder 502) oder im Test festhalten, welche Klassen bewusst fehlen.

**Test:** `tests/unit/adapters/rest/test_schemas.py` (oder neu `test_problems.py`): alle Unterklassen von `DomainError` rekursiv sammeln (`__subclasses__`), jede muss per `isinstance`-Kette in `_STATUSES` landen oder in einer expliziten Menge `NOT_REACHABLE_OVER_REST` stehen; die Menge dokumentiert die Entscheidung.

### A-07 (niedrig): `architektur.md` veraltet gegenüber dem Code

**Fundstellen:** `.idea/architektur.md:135-136` („Dokument … ein Kontakt, ein Typ"; „Kontakt … genau einer pro Dokument"), `:126` („Nur die API spricht mit Meilisearch"), `:61-74` (Ports-Tabelle). Code: `tables.py:140-141` (`contact_id`, `document_type_id` `nullable=True`), `core/domain/documents.py` (Kontakt und Typ optional; die Klassifizierung lässt sie leer, wenn nichts passt), `adapters/inbound/worker/worker.py` und `core/services/indexing.py` (der Worker schreibt den Index), `backend/README.md:35-38` (vollständige Portliste).

**Problem:** Drei Aussagen des verbindlichen Dokuments stimmen nicht mehr mit dem Code überein, in allen Fällen ist der Code richtig und das Dokument hinkt hinterher:

1. Kontakt und Dokumenttyp sind optional („höchstens einer"). Ein Dokument ohne Kontakt ist nach der Pipeline ein normaler Zustand (Gelb mit Vorschlag oder vom Besitzer so belassen).
2. Der Index wird vom Worker geschrieben (Jobs `search.index`, Abgleich, Neuaufbau); die API liest nur. Der Satz unter „Datenhaltung" widerspricht dem Abschnitt „Suche" desselben Dokuments.
3. Die Ports-Tabelle („erster Wurf") nennt weder `PreviewRenderer`, `PatternMatcher`, `WebhookSender`, `Clock` noch die Identitäts-Ports (`PasswordHasher`, `SecretCipher`, `Totp`). Da `CLAUDE.md` die Tabelle als verbindlich erklärt, fehlt die Grundlage für die Regel „je Port ein In-Memory-Adapter" bei diesen Ports.

Ebenfalls geprüft, aber in Ordnung: Regel-Engine (Konflikte, Schleifenschutz, Misstrauen, Flanke, Rückwirkung), Lanes, Konfidenzregeln, Ereignistypen, Berechtigungen, M12-Erweiterungen (siehe „Geprüft und in Ordnung").

**Vorschlag:** Zeile 135/136 zu „höchstens ein Kontakt, höchstens ein Typ"; Zeile 126 zu „Nur API und Worker sprechen mit Meilisearch: der Worker schreibt (Jobs, Abgleich, Neuaufbau), die API sucht"; Ports-Tabelle um die sieben Ports ergänzen (eine Zeile je Port mit erstem Adapter: PDFium, `regex`, httpx2, Systemuhr, Argon2, AES-GCM, pyotp).

**Test:** Kein Code-Test; Doku-Änderung. Optional ein Test, der `core/ports/__init__.__all__` gegen eine Liste in der README prüft, ist Übertreibung.

### A-08 (niedrig): `README.md` nennt „(owner)" für Rechte, die Admins auch haben

**Fundstellen:** `backend/README.md:447` (Löschen „(owner)"), `:451-453` (Protokoll, Retry, Reprocess „(owner)"). Code: `core/domain/permissions.py:104-106` (`can_control_document`: Besitzer oder aktiver Admin), `core/services/documents.py:244,340,360`, Endpunkt-Beschreibungen in `rest/documents.py:347,406,420,433` („The owner and admins").

**Problem:** Die Endpunkt-Tabelle der README stammt aus der Zeit vor M11b („Admins haben alle Rechte und sehen alles", `architektur.md:149`). OpenAPI-Beschreibungen und Code sind aktuell; die Tabelle nicht. Für Betreiber, die die README als Referenz lesen, ist das irreführend (das M11a-Review fand genau diesen Widerspruch in der UI).

**Vorschlag:** In den vier Zeilen „(owner)" durch „(owner, admins)" ersetzen; dasselbe für `GET /inbox` prüfen (der Posteingang zeigt mit `all_users` auch Admins alles).

**Test:** Kein Code-Test. Die Rechte selbst sind durch `tests/unit/adapters/rest/test_permission_matrix.py` abgedeckt.

### A-09 (niedrig): Kein Meta-Test für die Vertragsabdeckung

**Fundstellen:** `backend/tests/contracts/*.py` (23 Suiten), `backend/tests/unit/adapters/memory/test_contracts.py`, die Adapter-Testmodule unter `tests/unit/adapters` und `tests/integration/adapters`; `backend/README.md:24-25` („Every port gets an in-memory adapter … and a contract test suite that every real adapter must pass").

**Problem:** Die Abdeckung ist heute vollständig (Prüfung von Hand, siehe unten), aber nichts erzwingt sie: ein neuer Port mit Memory-Adapter ohne Vertragssuite oder ein neuer echter Adapter, dessen Testmodul die Suite nicht erbt, fällt in CI nicht auf. Gerade die Regel aus `CLAUDE.md` ist der Kern des Testkonzepts.

**Vorschlag:** Ein Test `tests/unit/test_contract_coverage.py`: alle Klassen `*Contract` in `tests.contracts` sammeln; für jede muss es (a) eine Unterklasse in `tests.unit.adapters.memory` geben und (b) mindestens eine Unterklasse außerhalb von `memory` (unit oder integration), Ausnahmen in einer kleinen, begründeten Liste (`PatternMatcherContract` bis A-04 erledigt ist). Zusätzlich: jeder Name in `papiq.core.ports.__all__`, der ein `Protocol` ist, wird von einer Klasse in `papiq.adapters.outbound.memory` implementiert (Prüfung über `typing.runtime_checkable` oder einen festen Zuordnungs-Dict).

**Test:** Der Meta-Test selbst.

## Geprüft und in Ordnung

**1. Kern ohne Framework.** Alle Importe in `core` sind Standardbibliothek und `papiq.core` (Grep über 344 Importzeilen; kein `papiq.adapters`, `papiq.composition`, kein `pydantic`, `sqlalchemy`, `fastapi`, `structlog`, `httpx2`, `regex`). Logging ausschließlich über `logging.getLogger` (11 Module). `structlog` nur in `composition`. Der Vertrag „Core is plain Python" (`pyproject.toml:104-139`) listet die kritischen Pakete; der Layers-Vertrag verbietet `core`→`adapters`/`composition` und `adapters`→`composition`. Der Vertrag „Domain model depends on nothing else in the core" wird eingehalten (`core/domain` importiert kein `ports`/`services`).

**2. Ports und Verdrahtung.** 16 Port-Module, alle als `Protocol`; Datenklassen daneben (`OcrResult`, `StructuredRequest`, `SearchQuery` mit Pflicht-`visibility`). Nur `composition/container.py` kennt die Adapterklassen (`PERSISTENCE`, `OBJECT_STORES`, `SEARCH_INDEXES`, … als Tabellen mit `AdapterNotAvailableError`); `composition/settings.py` importiert nur `decode_key`. Inbound-Adapter (REST, MCP, Worker, Evaluation) importieren ausschließlich `core` und den eigenen Bereich (Ausnahme A-01). Kein Adapter importiert `composition` (ein Treffer ist ein Docstring). Keine relativen Importe.

**3. Vertragsabdeckung (von Hand).** Jede Suite wird vom In-Memory-Adapter geerbt (`tests/unit/adapters/memory/test_contracts.py`, `test_webhook_sender.py`) und von jedem echten Adapter: `UnitOfWork`, `JobQueue`, `EventBus`, `IdentityRepositories`, `RuleRepositories`, `WebhookRepository` auf SQLite (`tests/unit/adapters/sql/test_sqlite.py`) und Postgres (`tests/integration/adapters/sql/test_postgres.py`) samt `SqlAdapterSuite` und `MigrationSuite`; `ObjectStore`: Dateisystem (unit), S3 (integration); `Ocr`: OCRmyPDF (integration); `DocumentParser`: Docling (integration); `PreviewRenderer`: PDFium (unit); `SearchIndex`: Meilisearch (integration); `LanguageModel`/`Embeddings`: OpenAI-kompatibel (unit, mit Fake-Server) und `BagOfWordsEmbeddings`; `WebhookSender`: httpx2 (unit); `PatternMatcher`: `regex` (unit); `OidcProvider`: Authlib (unit, mit Fake-IdP); `PasswordHasher`/`SecretCipher`/`Totp`: Argon2/AES-GCM/pyotp (unit); `Clock`: System (unit). `ProcessingLog`, `Outbox` und `RuleApplicationRepository` sind Teil der UoW- bzw. Regel-Suite; `UnitOfWork.lock` hat zwei Vertragstests.

**4. Unit of Work und Nebenläufigkeit.** Jeder schreibende Anwendungsfall endet mit genau einem `commit()`: Empfang (`pipeline.py:258-291`: Dokument, Protokoll, Schubladenwahl, erster Job, Ereignisse), Schrittergebnis (`_record_once`, `pipeline.py:535-556`: Ergebnis, Protokoll, Folgejob, Job-Abschluss, Ereignisse), Bestätigen (`confirm` → `_restart`, `:370-427`), Metadatenänderung (`documents.py:305-330`), Verschieben, Löschen mit `documents.remove_files`-Job (`:357-370`), Aufräumen (`maintenance.py:142-145`). Ereignisse entstehen ausschließlich in der Outbox (`SqlOutbox.flush` als letzter Schreibzugriff vor COMMIT, `sql/unit_of_work.py:61-83`; Postgres-Advisory-Lock sichert die Commit-Reihenfolge, Test `test_an_event_written_first_but_committed_last_is_not_skipped`). Jobs: Claim als eigene Transaktion, `FOR UPDATE SKIP LOCKED` (`job_queue.py:78-96`), Lease länger als jeder Schritt (`container.py`: `max(ocr, parse, 2*llm) + 2 min`), verlorener Claim → `ConcurrencyError` → Ergebnis verworfen (`_record`, `_still_claimed`), Unterbrechung → `release` ohne Zählung. Optimistische Versionen auf allen Aggregaten; `touch` ohne Version. SQLite: `BEGIN IMMEDIATE`, zweite schreibende Unit im selben Task bricht sofort ab (Test vorhanden). Lock pro Original-Key beim Speichern/Löschen (`pipeline.py:262`, `maintenance.py:103`). Die bekannte Lücke „Original vor Dokument gespeichert" ist dokumentiert (`README.md`, „Known gap") und durch die Wiederholung unter Lock entschärft.

**5. Schema.** `tables.py` und Alembic-Kette (`v0001`–`v0006`) werden in `tests/sql_suite.py:307-321` doppelt verglichen (Alembic-Autogenerate mit `compare_type` und die DDL aus `sqlite_master`/`pg_catalog`, so dass auch Teilindex-Prädikate und AUTOINCREMENT geprüft werden), auf SQLite und Postgres. Eindeutigkeit per Datenbank (`username_key`, `name_key`, `(owner_id, sha256)`, Teilindizes für Standardschublade und aktive Dedup-Keys). Kaskaden für Identität, Webhooks, Regeln.

**6. architektur.md gegen Code.** Datenmodell (bis auf A-07) und Attribut-Datentypen (`AttributeType`: text, number, amount, date, boolean, choice, link) stimmen. Berechtigungen: `permissions.py` setzt Reichweite (`reach_access`, `in_reach`), Admin-Vollrechte (`is_active_admin`), Verschieben (Besitzer in schreibbare Schublade oder Admin), Posteingang (Gelb/Rot nur Besitzer und Admins), Freigabe erlaubt kein Verschieben, deaktivierter Admin ohne Rechte um; SQL spiegelt die Reichweite (`_visible_to`), Admin-Sicht über `query` mit `all_users`; Webhooks nutzen `in_reach` (`delivery.py:417,449`), SSE `can_read_document` (`documents.py:222`), Admins werden bei SSE gesondert zugestellt (`active_admins`). Ingest: Lane = schlechtestes Ergebnis (`Lane.from_outcomes`, `domain/pipeline.py:50-57`); Konfidenz aus Fakten (Kontaktabgleich, Typ nur aus Liste, Datum/Betrag im Text, Datumsattribut gleich Dokumentdatum → unsicher mit Vorschlag, `classification/steps.py:433-440`); Regelkonflikt → Gelb nur beim Eingang. Suche: Abschnitte 1500/8/200 000 (Settings-Defaults), Rechtefilter im Index plus Nachprüfung je Treffer (`search.py:137-138`), Abgleich alle 6 h und bei Worker-Start (`indexing.py:schedule`), Neuaufbau neben dem aktiven Index mit Tausch (`IndexBuild`), Wiederholung 30 s verdoppelnd bis 1 h, zehnmal (≈ 3 h). Regel-Engine: Konflikte statt Überschreiben, Schleifenschutz (Bedingungen auf dem Zustand vor allen Aktionen), Misstrauen (`distrust`, `Match.trusted`), Flanke (`before` in `matches`), Gelb nur in der Pipeline, Rückwirkung mit `accept_conflicts` (`rule_engine.py` Docstring und `:343-356, :616-685`). Ereignisse: genau die sechs Typen (`events.py:EVENT_TYPES`). M12: `POST /documents` mit `owner` und `metadata` (`rest/documents.py:84-86, 205-214`; Prüfung in `pipeline.receive`/`_check_imported`), `POST /drawers` mit `owner_id` (`rest/drawers.py:50`), `POST /users` per Admin-Token nur Rolle `user` ohne Passwort (`rest/users.py:74-80`), `sha256` in den Dokumentdetails (`schemas.py:154`). Betrieb: `PAPIQ_ROLE`, s6-Dienste, Konfiguration nur per `PAPIQ_`, `_FILE`-Variante mit Fehler bei Doppelsetzung (`settings.py:_SecretFileSource`).

**7. Konfiguration.** Alle 97 Felder von `Settings` sind in `backend/README.md` dokumentiert (Grep; gruppierte Zeilen wie `PAPIQ_DB_HOST, _NAME, _USER` eingerechnet); umgekehrt nennt die README keine Variable, die es nicht gibt. Unbekannte `PAPIQ_`-Variablen werden bei jedem Befehl als Warnung gemeldet (`__main__.py:108-109`, `find_unknown_variables`). `PAPIQ_HTTP_PORT` in `deploy/compose.*.yml` ist eine Compose-Variable des Hosts (Port-Mapping), erreicht den Container nicht und ist in `deploy/README.md` erklärt; der gemeinsame Präfix könnte Leser verwirren, ist aber kein Fehler. Konsistenzprüfungen (Postgres-/S3-Pflichtfelder, OIDC-Tripel, `embedding_dimensions` mit Meilisearch, Dev-Schlüssel bei sicheren Cookies, Proxy-Pflicht) brechen den Start mit Nennung der Variablen ab.

**8. Fehlerbehandlung.** `problems.py` ordnet alle über REST erreichbaren Domänenfehler zu, mit Sonderfällen (`DuplicateDocumentError` mit `existing_document_id`, `OpenFieldsError` mit `open_fields`, `SecondFactorRequiredError`, `TooManyAttemptsError` mit `Retry-After`, `AuthenticationError` mit `WWW-Authenticate`); unerwartete Fehler → 500 ohne Details, mit Log. Jedes `except Exception` protokolliert (Aufzählung in A-06).

**9. Tests.** Marker nach Verzeichnis mit `UsageError` für falsch abgelegte Tests (`tests/conftest.py`); `--strict-markers`; Hilfsmodule (`builders`, `probes`, `sql_suite`, `api`, `oidc_idp`) außerhalb der Marker-Verzeichnisse. Integrationstests überspringen bei nicht erreichbarem Dienst, CI prüft, dass nichts übersprungen wurde (README). `tests/unit/test_openapi.py` sichert `web/openapi.json` gegen den Code. REST-Tests laufen gegen den Memory-Container mit echter Authentifizierung (`tests/unit/adapters/rest/conftest.py`).

## 5 Web-UI

Geprüft: `web/` auf Branch `claude/project-thread-7jl9zr` (Stand `e593168`), dazu `backend/src/papiq/adapters/inbound/rest/ui.py` und die Endpunktbeschreibungen in `web/openapi.json`. Nur gelesen; keine Befehle der Werkzeugkette ausgeführt. Alle Pfade relativ zu `web/`, sofern nicht anders angegeben.

| Nr. | Schwere | Befund |
| --- | --- | --- |
| 5.1 | hoch | Der Sitzungs-Effekt im App-Layout hängt an der Objektidentität von `session.user`; jede Navigation (auch Hover-Preload) startet den Ereignisstrom neu, leert die Upload-Warteschlange und setzt den Posteingangszähler zurück |
| 5.2 | mittel | Nach einem vom Server geschlossenen Ereignisstrom (5xx, Proxy) wird `generation` beim Wiederaufbau nicht erhöht; Listen laden nicht nach |
| 5.3 | mittel | Späte Antworten: die Prüfansicht (`inbox/[id]`) und das Verarbeitungsprotokoll prüfen nicht, ob die Antwort noch zur angezeigten ID gehört; die Suche übernimmt `total` aus veralteten Antworten |
| 5.4 | niedrig | Upload: kein Abbruch, keine Größenprüfung vor dem Senden (413-Pfad unsicher), Duplikat (409) ohne Sprung zum vorhandenen Dokument, gelöschtes Dokument bleibt „in Verarbeitung“ |
| 5.5 | niedrig | Keine Pluralformen: elf Texte mit `{count}`/`{seconds}` sind bei 1 grammatisch falsch („1 Dokumente warten“, „1 sessions ended“) |
| 5.6 | niedrig | Texte: deutsche Anführungszeichen „…“ fest im Markup, `ms` als Rohtext, pdf.js-Fehlertexte englisch, `JSON.stringify` als Anzeige, zwei ungenutzte Schlüssel |
| 5.7 | niedrig | CSP: `img-src data: blob:` wird von keinem Code gebraucht; der Worker läuft ohne eigene CSP (nur Kopfzeile `frame-ancestors`) |
| 5.8 | niedrig | Feldgrenzen: Token-Name erlaubt 200 Zeichen, die API 100 |
| 5.9 | niedrig | Zugänglichkeit: Fokusverlust durch `{#key}` bei der Schlagwortauswahl (drei Stellen), PDF ohne Textebene, Fehlertext der Einstellungen ohne `role="alert"`, Prüfansicht ohne „Erneut versuchen“ |
| 5.10 | niedrig | Rechte als Komfort: Webhook-Bearbeiten/Löschen ungeschützt angeboten (inkonsistent zu Test/Erneuern), Admins können keine Schublade für andere anlegen (`owner_id`), kein Neuaufbau der Suche, Freigabe an deaktivierte Nutzer wird angeboten |
| 5.11 | niedrig | Posteingang: Lane-Reiter filtern nur die geladene Seite; `all_users` steht nicht in der URL (anders als in Liste und Suche) |
| 5.12 | niedrig | PDF-Viewer öffnet nicht neu, wenn die Archivdatei während der Anzeige entsteht (`{#key id}` allein) |
| 5.13 | niedrig | Abhängigkeiten: `minimumReleaseAge` nicht im Repo gesetzt (nur im Plan erwähnt); kein `pnpm audit` in der CI |

### 5.1 Layout-Effekt an der Identität von `session.user` (hoch)

**Problem.** `src/routes/(app)/+layout.ts:6-8` ruft in `load({ url })` bei jeder Navigation `session.load()` auf, weil `url.pathname` und `url.search` gelesen werden (SvelteKit führt `load` dann bei jeder Änderung des Pfads oder der Query neu aus, auch beim Preload per Hover, `data-sveltekit-preload-data="hover"` in `src/app.html:12`). `session.load()` weist in `src/lib/session.svelte.ts:30` immer ein neues Objekt zu (`this.user = data.user`). Der Effekt in `src/routes/(app)/+layout.svelte:23-49` liest `session.user` und läuft deshalb bei jeder Navigation neu: die Aufräumfunktion ruft `inbox.stop()`, `events.stop()` und `uploads.reset()` (Zeilen 42-48), danach wird alles neu aufgebaut. Folgen:

- `uploads.reset()` (`src/lib/upload.svelte.ts:82-88`) leert `#queue` und `items`. Wer fünf Dateien hochlädt (drei laufen parallel) und dann ein Dokument öffnet oder nur einen Link berührt, verliert die zwei wartenden Dateien ohne Meldung; die laufenden drei werden zwar fertig, erscheinen aber in keiner Liste mehr.
- Der Ereignisstrom wird bei jeder Navigation geschlossen und neu geöffnet; Ereignisse in der Lücke gehen verloren (kein Replay), und weil `#failed` dabei nicht gesetzt ist, steigt `generation` nicht (siehe 5.2).
- `inbox.stop()` setzt den Zähler auf 0, bis `refresh()` zurück ist (Flackern der Plakette).
- Der zweite Effekt (`+layout.svelte:14-20`) löst zusätzlich `inbox.refresh()` und `uploads.recheck()` aus; zusammen mit `GET /auth/me` sind das drei bis vier Anfragen je Navigation oder Hover.

Nebenbemerkung: Wirft `session.load()` in `+layout.ts` (API kurz nicht erreichbar, 503), zeigt SvelteKit die Fehlerseite für die ganze App, obwohl die aktuelle Seite weiter brauchbar wäre.

**Vorschlag.** Den Effekt an eine primitive Kennung binden statt an das Objekt: `const userId = $derived(session.user?.id ?? null)` und im Effekt `if (!userId) return;` (Derived mit String-Vergleich ändert sich nur bei Nutzerwechsel). Zusätzlich oder alternativ `Session.load()` so ändern, dass bei gleicher `id` das vorhandene Objekt behalten wird (nur Felder aktualisieren). `uploads.reset()` ausdrücklich nur bei Abmeldung und `replaced()` aufrufen. Für den Fehlerfall in `+layout.ts`: nur bei 401 umleiten, andere Fehler schlucken, wenn bereits ein Nutzer geladen ist.

**Test.** Vitest: `session.load()` zweimal mit derselben Nutzer-ID → `session.user` behält die Referenz (oder: ein Effekt, der `session.user` liest, läuft nicht erneut). Komponententest des Layouts mit `events.useFactory` (FakeSource wie in `events.test.ts`): nach zweitem `session.load()` existiert weiterhin genau eine Quelle und `uploads.items` ist unverändert. Playwright: fünf Dateien hochladen, sofort ein Dokument öffnen, zurück zur Liste → alle fünf Einträge im Upload-Dialog erreichen eine Lane.

### 5.2 Kein `generation++` nach geschlossenem Ereignisstrom (mittel)

**Problem.** `src/lib/events.svelte.ts:63-71`: bei `readyState === 2` (Server antwortete nicht 200, z. B. 502 vom Reverse-Proxy während eines Neustarts) ruft `onerror` `this.stop()`, das `#failed = false` setzt (`:84-89`), und dann `onClosed`. Das Layout (`(app)/+layout.svelte:27-39`) öffnet nach 5 s neu; `onopen` sieht `#failed === false` und erhöht `generation` nicht. Alle Seiten, die `events.generation` als Signal zum Nachladen nutzen (Dokumentliste, Posteingang, Detail, Protokoll, Uploads), zeigen nach einem API-Neustart veraltete Daten, bis der Nutzer selbst neu lädt. Der Test `events.test.ts:26-48` deckt nur den Pfad „onerror ohne Schließen, dann onopen“ ab.

**Vorschlag.** In `stop()` `#failed` nicht zurücksetzen, wenn der Stopp vom Server kam, oder in `start()` einen Parameter `reconnect = true` führen: jedes `onopen` nach einem früheren erfolgreichen `onopen` erhöht `generation`. Einfachste Variante: ein Feld `#everOpened`; `onopen` erhöht `generation`, wenn `#everOpened` bereits wahr war; `stop()` lässt es stehen, nur ein Nutzerwechsel (Abmelden) setzt es zurück.

**Test.** Vitest in `events.test.ts`: `start` → `onopen` → `readyState = 2`, `onerror` → `onClosed` ruft `start` erneut → neues `onopen` → `generation` ist um 1 gestiegen.

### 5.3 Späte Antworten in Prüfansicht, Protokoll und Suche (mittel)

**Problem.** Dieselbe Fehlerklasse, die im M11a-Review für die Dokumentdetailseite behoben wurde:

- `src/routes/(app)/inbox/[id]/+page.svelte:52-59`: `load()` schreibt `review`/`problem` ohne zu prüfen, ob `id` noch die gewünschte ist; `review` wird beim ID-Wechsel nicht geleert (die Dokumentseite tut beides, `documents/[id]/+page.svelte:97-117`). Wechselt man schnell zwischen zwei Posteingangsdokumenten (Zurück-Taste), kann die ältere Antwort die neuere überschreiben; der Baseline-Effekt (`:64-82`) schützt zwar die Formularwerte, nicht aber Titel, PDF-Quelle und Lane.
- `src/lib/components/ProcessingLog.svelte:35-53`: `load()` ohne `wanted === documentId`; die Komponente wird in der Detailseite nicht per `{#key}` neu erzeugt, nur die Prop ändert sich.
- `src/routes/(app)/search/+page.svelte:30-40`: `total = data.estimated_total` wird im Lader gesetzt, bevor `PagedList` die Generation prüft; eine verworfene Antwort ändert die angezeigte Trefferzahl.

**Vorschlag.** In beiden `load()`-Funktionen `const wanted = id` merken und nur bei `wanted === id` zuweisen; in der Prüfansicht `review = null` beim ID-Wechsel (wie `documents/[id]`). Für die Suche `total` aus den Items der Seite ableiten oder `PagedList.Page` um ein `meta`-Feld erweitern, das nur bei gültiger Generation übernommen wird.

**Test.** Vitest mit zwei verzögerten `fetch`-Antworten in umgekehrter Reihenfolge (Muster wie in `paging.test.ts`): nach dem ID-Wechsel bleibt das jüngere Dokument stehen; für `PagedList` ein Test, dass `total`/Meta einer verworfenen Seite nicht übernommen wird.

### 5.4 Upload: Abbruch, Grenzen, Duplikate (niedrig)

**Problem.**
- `src/lib/upload.svelte.ts:105-124`: kein `AbortController`; weder einzelne Dateien noch die Warteschlange lassen sich abbrechen (der Dialog bietet nur „Fertige entfernen“, `UploadDialog.svelte:113`). Bekannt ist bereits der fehlende Byte-Fortschritt.
- Die UI kennt `PAPIQ_UPLOAD_MAX_SIZE` nicht (API-Standard 100 MiB, `backend/.../composition/settings.py:100`) und prüft die Größe nicht. Der Server antwortet laut `rest/upload.py:3` „früh“ anhand `Content-Length`; ob der Browser eine 413-Antwort liefert oder `fetch` mit `TypeError` scheitert (dann erscheint „Netzwerkfehler“, `errors.ts:19`), hängt vom Browser und davon ab, ob der Server den Körper noch liest. Im Browser mit einer 150-MB-Datei prüfen.
- 409 bei Duplikat: `Problem.existing_document_id` wird nicht genutzt; es gibt nur den englischen Detailtext, keinen Sprung zum vorhandenen Dokument.
- `#listen` (`:44-53`) reagiert nicht auf `document.deleted`; ein währenddessen gelöschtes Dokument bleibt „wird verarbeitet“ und hält `uploads.active` wahr, womit „Fertige entfernen“ dauerhaft gesperrt ist.
- Dateitypen: der Dateiwähler filtert (`UploadDialog.svelte:81`, deckt sich mit `media_types.SUPPORTED`), Drag-and-Drop nicht; das ist akzeptabel, weil die API mit 415 und Detail antwortet.

**Vorschlag.** `AbortController` je Upload, Zustand `cancelled`, Knopf je Zeile und „Alle abbrechen“; `document.deleted` → Zustand `failed` mit Text; bei 409 `existing_document_id` als Link anzeigen (neuer Schlüssel). Größenlimit: entweder die API gibt es preis (z. B. in `GET /health` oder einem kleinen `GET /config`) und die UI prüft vorab, oder zumindest einen eigenen Text für 413/TypeError während eines Uploads („Datei zu groß oder Verbindung abgebrochen“).

**Test.** Vitest für `Uploads`: Abbruch setzt den Zustand und ruft `abort()`; `document.deleted` beendet den Eintrag; 409 mit `existing_document_id` landet im Eintrag. Playwright: Datei über dem Limit hochladen und auf eine verständliche Meldung prüfen.

### 5.5 Keine Pluralformen (niedrig)

**Problem.** Paraglide kann Varianten (`Intl.PluralRules`), genutzt wird es nicht; alle Texte sind Strings. Betroffen (`messages/en.json`, `messages/de.json`): `inbox_count` („1 Dokumente warten“, als `aria-label` der Plakette in `AppShell.svelte:57`), `inbox_open_fields` („1 offene Felder“, sichtbar in jeder Posteingangszeile), `sessions_ended` („1 sessions ended“), `webhook_failing`, `rule_apply_selected`, `rule_switch_on_apply`, `rule_preview_loaded`, `rule_preview_complete`, `error_too_many` („in 1 Sekunden“), `search_total` (bei `about 1`), `delivery_attempt` (unkritisch). `tests/messages.test.ts` prüft nur Schlüssel, Leere und Platzhalter.

**Vorschlag.** Für die betroffenen Schlüssel Paraglide-Varianten mit `plural` (one/other) anlegen oder, wenn Varianten vermieden werden sollen, je zwei Schlüssel (`_one`/`_other`) und eine kleine Hilfsfunktion `plural(count, one, other)` in `i18n.ts`.

**Test.** Vitest in `i18n.test.ts`: `m.inbox_count({ count: 1 })` enthält „Dokument wartet“, `({ count: 2 })` „Dokumente warten“ (beide Sprachen); `messages.test.ts` zusätzlich: jeder Schlüssel mit `{count}` hat eine Pluralvariante.

### 5.6 Texte: Kleinigkeiten (niedrig)

- `src/lib/components/ReviewFieldInput.svelte:55`: `„{check.evidence}“` setzt deutsche Anführungszeichen fest ins Markup, auch im Englischen; der Rohtext-Test sieht nur Buchstaben (`tests/no-raw-text.test.ts:17-19`). → Schlüssel `review_quoted` mit `{text}` je Sprache (es gibt schon `rule_quoted`).
- `src/routes/(app)/webhooks/[id]/+page.svelte:118`: `` `${delivery.duration_ms} ms` `` als Ausdruck; das Protokoll nutzt dafür `formatNumber(…, { style: 'unit', unit: 'second' })` (`ProcessingLog.svelte:95-100`). → gleich formatieren.
- `src/lib/components/PdfViewer.svelte:78-81`: Fehler aus pdf.js (`error.message`, z. B. „Invalid PDF structure“) erscheinen englisch. → auf `pdf_missing`/einen neuen Schlüssel `pdf_broken` abbilden, Detail nur in `title`.
- `src/lib/describe.ts:35`: `JSON.stringify(value)` als Anzeige für unbekannte Objekte. → `value_unset` oder ein neutraler Text.
- Ungenutzte Schlüssel: `rule_versions_title`, `rule_action_type` (in `src/` nicht referenziert). → entfernen; `messages.test.ts` um eine Prüfung auf ungenutzte Schlüssel erweitern (Quellen per `import.meta.glob` wie in `no-raw-text.test.ts`).
- `no-raw-text.test.ts` prüft keine String-Literale in Ausdrücken (`{'–'}`, Template-Strings); eine zusätzliche Regel für Literale mit Buchstaben in `{…}` würde Fälle wie das `ms` finden.

In Ordnung: alle 526 Schlüssel in beiden Sprachen, Platzhalter identisch, keine englischen Sätze in `de.json` (gleiche Werte nur bei Admin, Tags, Text, Link, Code, Global, Migration, „Version {number}“).

### 5.7 CSP: `img-src data: blob:` und Worker-CSP (niedrig)

**Problem.** `vite.config.ts:35`: `img-src 'self' data: blob:`. Im Quelltext gibt es weder `createObjectURL` noch `data:`-Bilder (grep über `src/`); der QR-Code ist ein inline-SVG per `{@html}` (`TotpCard.svelte:79`), Vorschaubilder kommen von `/api/v1/documents/{id}/preview`. pdf.js zeichnet auf Canvas, lädt Schriften über die `FontFace`-API (kein `@font-face` mit `data:`) und erzeugt Blob-URLs nur für Worker von fremden Origins (hier gebündelt, `PdfViewer.svelte:7`). Beide Quellen sind damit sehr wahrscheinlich überflüssig; die bekannte Prüfung mit einem JBIG2/JPX-PDF steht noch aus (`areas.spec.ts` meldet CSP-Verstöße als Konsolenfehler, eignet sich also dafür).

Hinweis, kein Mangel: Die Meta-CSP gilt nur für das HTML-Dokument. Der PDF-Worker (`/ui/_app/immutable/…`) bekommt vom Server nur `Content-Security-Policy: frame-ancestors 'none'` (`ui.py:24-28`), also faktisch keine Skript-CSP; deshalb war `wasm-unsafe-eval` nicht nötig. Soll das enger werden, müsste `ui.py` für Dateien unter `_app/` eine `script-src 'self' 'wasm-unsafe-eval'; connect-src 'self'` liefern; `style-src-attr 'unsafe-inline'`, `worker-src 'self'`, `connect-src 'self'`, `base-uri`, `form-action`, `object-src 'none'` sind minimal und passend.

**Vorschlag.** `data:` und `blob:` streichen, nachdem ein PDF mit JBIG2- und JPX-Bildern sowie ein PDF mit eingebetteten CJK-Schriften ohne Konsolenfehler angezeigt wurde.

**Test.** Playwright: solche PDFs im Rauchtest hochladen und öffnen; `areas.spec.ts` fängt CSP-Verstöße bereits ab. Vitest: Snapshot der CSP-Direktiven aus `vite.config.ts`, damit eine Erweiterung bewusst geschieht.

### 5.8 Feldgrenze Token-Name (niedrig)

**Problem.** `src/lib/components/TokensCard.svelte:152`: `maxlength={200}`; `TokenCreate.name` hat `maxLength: 100` (`openapi.json`). Ein längerer Name führt zu 422 mit englischem Pydantic-Text statt zur Begrenzung im Feld. Alle anderen Grenzen passen (Schublade/Stammdaten 200, Webhook 100/2000, Benutzername 150, Attribut 200, Regelname über `LIMITS`).

**Vorschlag.** `maxlength={100}`; besser: die Grenzen aus `schema.ts` ableiten lassen sich nicht (nur Typen), daher eine kleine Konstante je Formular mit Verweis auf das Schema.

**Test.** Vitest: der Token-Dialog hat `maxlength="100"` (Render mit Testing Library).

### 5.9 Zugänglichkeit (niedrig)

- `{#key filters.tags.length}` in `DocumentFilters.svelte:80-88`, `DocumentEditor.svelte:210-219`, `ReviewFieldInput.svelte:101-111` zerstört das `<select>` nach jeder Auswahl; der Tastaturfokus fällt auf `<body>`. → Statt `{#key}` den Wert nach `onchange` zurücksetzen (`value = ''` nach `await tick()`), die Komponente bleibt stehen.
- `PdfViewer.svelte:217`: Canvas ohne Textebene; Screenreader bekommen nur `aria-label` mit dem Titel. Mindestens der Hinweis „Text als Download verfügbar“ oder pdf.js-TextLayer (`style-src-attr` reicht dafür) erwägen.
- `src/routes/(app)/settings/+page.svelte:73-77`: Fehlertext ohne `role="alert"` (alle anderen Seiten haben es).
- `src/routes/(app)/inbox/[id]/+page.svelte:150-157`: bei Fehlern kein „Erneut versuchen“ (Dokumentseite: `documents/[id]/+page.svelte:213`).
- `AppShell.svelte:54-61`: `aria-label` auf einem `<span>` wird von Screenreadern uneinheitlich vorgelesen; besser `<span class="sr-only">` mit dem Text neben der Zahl.

**Test.** Vitest mit `user-event`: nach Auswahl eines Schlagworts bleibt `document.activeElement` das `<select>`.

### 5.10 Rechte als Komfort (niedrig)

Abgleich `src/lib/permissions.ts` mit den Endpunktbeschreibungen: `canEdit`/`canManage`/`canMove`/`writableDrawers`/`canManageDrawer`/`canManageWebhook`/`canChangeRule`/`canApplyRule`/`canListAllUsers` entsprechen den Texten zu `PATCH/DELETE /documents/{id}`, `move`, `retry`, `reprocess`, `log`, `review`, `confirm`, `drawers`, `webhooks`, `rules`, `all_users`. Admins sehen die Admin-Bereiche (`sections.ts:40-43`), den Umschalter „Alle Nutzer“ (Liste, Posteingang, Suche, Regeln), alle Schubladen und Webhooks, und bekommen beim Upload, Bestätigen und Verschieben jede Schublade. Kleinere Abweichungen:

- `src/routes/(app)/webhooks/+page.svelte:162-177`: Bearbeiten und Löschen werden ohne `canManageWebhook` angeboten, Test und Erneuern mit. Praktisch folgenlos (die API liefert Nicht-Admins nur eigene Webhooks), aber inkonsistent. → gleich schützen.
- `POST /drawers` erlaubt Admins `owner_id` (Schublade für einen anderen aktiven Nutzer); `drawers/+page.svelte` und `NameDialog` bieten das nicht. Gehört zu „Admins verwalten alle Schubladen“.
- `POST /search/reindex` (Admins) hat keinen Knopf; vermutlich bewusst (Betrieb), dann in `deploy/README.md` als API-Aufruf dokumentieren.
- `DrawerShares.svelte:36-40`: Kandidaten enthalten für Admins deaktivierte Nutzer (die API antwortet 404). → `user.active !== false` filtern.
- `MultiPicker`/`ActionRow` zeigen für gelöschte IDs „unbekannt“, gut; `UserManage.svelte:45-46` behandelt die eingeschränkte Antwort für Nicht-Admins korrekt.

**Test.** Vitest-Render der Webhook-Liste mit fremdem Webhook und Nicht-Admin: keine Bearbeiten/Löschen-Knöpfe. Playwright: Admin legt eine Schublade für einen zweiten Nutzer an.

### 5.11 Posteingang: Lane-Reiter und `all_users` (niedrig)

**Problem.** `src/routes/(app)/inbox/+page.svelte:35`: die Reiter „Prüfen“/„Eingreifen“ filtern nur die geladene Seite (`GET /inbox` kennt nur `limit`, `cursor`, `all_users`). Enthält die erste Seite nur gelbe Dokumente, zeigt „Eingreifen“ eine leere Liste mit „Mehr laden“ (`:97` greift erst ohne `hasMore`). `allUsers` steht nicht in der URL (`:19`), anders als in Liste und Suche (`filters.ts`); ein Neuladen vergisst die Wahl.

**Vorschlag.** Entweder `lane` als Parameter für `GET /inbox` in der API ergänzen (Reichweite bleibt), oder die Reiter als reine Anzeige kennzeichnen und beim Reiterwechsel automatisch nachladen, bis eine Seite Treffer hat (Muster aus `rules/[id]/apply/+page.svelte:90-120`). `all` wie in `parseFilters` in die URL schreiben.

**Test.** Vitest mit zwei Seiten (gelb, dann rot): Reiter „Eingreifen“ zeigt nach Auswahl das rote Dokument ohne weiteren Klick.

### 5.12 PDF-Viewer bei nachgelieferter Archivdatei (niedrig)

**Problem.** `src/routes/(app)/documents/[id]/+page.svelte:288-295` und `inbox/[id]/+page.svelte:178-183`: `{#key id}` erzeugt den Viewer nur bei ID-Wechsel; `PdfViewer.svelte:159` öffnet in `onMount` einmal. Ein Bild-Upload (JPEG/PNG/TIFF) hat bis zur OCR weder Archiv noch ein PDF-Original: der Viewer zeigt „Kein PDF“ und bleibt dabei, obwohl `document.step_completed` und `document.filed` das Dokument neu laden.

**Vorschlag.** `{#key `${id}:${document.processing.status}`}` oder der Viewer bekommt ein `reload()`/einen Effekt auf `sources`, der bei vorherigem 404 neu öffnet.

**Test.** Vitest: `PdfViewer` mit `fetch`-Mock, erst 404/404, nach Prop-Änderung 200 → `onsource` wird gerufen.

### 5.13 Abhängigkeiten (niedrig)

In Ordnung: `package.json` exakt gepinnt (keine Bereiche), `packageManager: pnpm@12.9.1`, Node 24.21.0 in CI, `pnpm install --frozen-lockfile`, `pdfjs-dist` 6.4.299 nur als `legacy/build` importiert (`PdfViewer.svelte:6-7`), Worker gebündelt, wasm/Schriften/CMaps per `vite-pdfjs.js` aus `/ui/pdfjs/`, keine CDN-URLs im Quelltext, Schriften aus `@fontsource-variable`.

Offen: `minimumReleaseAge` wird in `umsetzungsplan.md` als Schutz genannt, steht aber weder in `web/.npmrc` noch in einer `pnpm-workspace.yaml` (beides fehlt); greift nur ein pnpm-Standard, überlebt die Regel ein pnpm-Update nicht. → `pnpm-workspace.yaml` mit `minimumReleaseAge: 1440` einchecken. Die CI führt kein `pnpm audit` aus; ein Schritt `pnpm audit --prod` (oder Dependabot/Renovate) wäre günstig.

### Geprüft und in Ordnung

- **Sitzung und Geheimnisse:** Sitzung nur im HttpOnly-Cookie, `credentials: 'same-origin'` (`api/fetch.ts:62`); CSRF-Token nur im Speicher (`session.svelte.ts:16`), nur bei Änderungen und nur an die eigene Origin (`fetch.ts:53-59`), ein Wiederholversuch nur bei gleicher Nutzer-ID, sonst Neuladen (`session.svelte.ts:71-85`, Tests in `session.test.ts`, `fetch.test.ts`). `localStorage` nur für `mode-watcher-mode` (`static/theme.js`) und `PARAGLIDE_LOCALE`; kein `sessionStorage`, kein IndexedDB, kein `console.*` im Quelltext. API-Tokens, Webhook-Geheimnisse und Wiederherstellungscodes nur einmal im `SecretDialog` (Speicher, kein Storage, nicht in URLs); TOTP-Geheimnis nur während der Einrichtung (`TotpCard.svelte:21-46`); Passwortfelder werden nach Erfolg geleert.
- **`next`:** `safeNext` (`navigation.ts:17-33`) lässt nur `/ui` und `/ui/…` ohne `//`, `\`, Steuerzeichen, Punktsegmente (auch kodiert) durch; `loginHref`, `oidcLoginHref` und `login/+page.ts` nutzen es; `navigation.test.ts` deckt die Fälle ab. Keine Endlosschleife bei `next=/ui/login`.
- **Fehlerpfade:** 401 außerhalb von `login/me/logout` → Login mit `next` (`fetch.ts:70`, `+layout.ts:11-14`); 403 mit Präfix und Detail; 404 in Dokument- und Prüfseite als eigener Text; 409 als Detail (Namen, Duplikate); 422 je Feld (`errors.ts:32-42`, passt zum Format `loc[1:]` in `rest/problems.py:142-148`), in `DocumentEditor`, `PasswordCard`, Regelbaukasten (`rules/errors.ts`); 429 mit Sekunden oder HTTP-Datum (`problem.ts:38-44`); Netzfehler als `TypeError` → `error_network`; Ladefehler über `hooks.client.ts` in der Sprache des Nutzers.
- **SSE:** eine `EventSource` (`events.svelte.ts`), Start und Ende am Sitzungs-Effekt des Layouts; Abmelden räumt Strom, Zähler und Uploads auf (`AppShell.svelte:29-37` → `session.clear()` → Effekt-Cleanup); 401/abgelaufene Sitzung → `onClosed` → `session.load()` → Login; Netzfehler versucht der Browser selbst, `generation` steigt (bis auf 5.2); `uploads.recheck()` nach Wiederverbindung.
- **Upload:** drei parallel (`upload.svelte.ts:23`), Dateitypen des Wählers = `media_types.SUPPORTED`, Zustandsfolge über SSE und Nachladen der Lane, Fehlertext je Datei, Reset bei Abmeldung.
- **Race Conditions:** `PagedList` mit Generation (`paging.svelte.ts`, inkl. `refresh()` während `more()`), Dokumentdetail mit `wanted === id` und Zurücksetzen, Regelseiten (`loads`-Zähler, `wanted`), Webhook-Protokoll, `PdfViewer` mit `layoutRun`/`destroyed`.
- **XSS/HTML:** `{@html}` nur für das uqr-SVG; alles andere escaped; keine `innerHTML`, kein `target="_blank"`; `window.location.assign` nur mit der vom Server gelieferten OIDC-URL.
- **Auslieferung (`ui.py`):** Pfade werden aufgelöst und gegen `root` geprüft, versteckte Namen und Backslashes abgewiesen, `_app/` nie als Seite, `immutable` nur für gehashte Dateien, `no-cache` + ETag/304 sonst, Security-Header auch auf 304 und 404, nur GET.
- **Texte:** gleiche Schlüssel und Platzhalter in `en`/`de` (Test), kein Rohtext in Markup und `aria-label`/`title`/`placeholder`/`alt` (Test), `<html lang>` folgt der Sprache, Formate über `Intl` mit regionaler Variante.
- **Tastatur und aria:** Labels an allen Feldern, `aria-pressed` an Filtern, `aria-invalid`/`aria-describedby`/`role="alert"` im Login mit Fokus auf das Codefeld, Skip-Link, `main tabindex=-1`, Fokusring 2 px mit Abstand (`app.css:132-133`), Dialoge mit Fokusfalle (bits-ui), `aria-live` an Fortschritt und Trefferzahl, native `<select>` und Checkboxen im Regelbaukasten, Zoom per Tastatur im Viewer.
- **CSP (bis auf 5.7):** `script-src 'self'` im Hash-Modus, `style-src 'self'` + `style-src-attr 'unsafe-inline'`, `worker-src 'self'`, `connect-src 'self'`, `font-src 'self'`, `object-src 'none'`, `base-uri 'self'`, `form-action 'self'`; `frame-ancestors`, `nosniff`, `Referrer-Policy` als Header.

