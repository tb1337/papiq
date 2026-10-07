# Sicherheits-Review M4 – Authentifizierung, Rechte, REST-API

Datum: 2026-10-07. Unabhängiges Review ohne Kontext aus der Umsetzung.

## Geprüfter Stand

- **Branch:** `claude/charming-knuth-2ihyum` (Commit `327dc95`) gegen `main` (`cac0821`).
  Ein Branch `m4-auth` existiert im Remote nicht; dieser Branch enthält die M4-Arbeit
  (Identität, TOTP, Sessions, API-Tokens, OIDC, REST-Rechte) und wurde deshalb geprüft.
- **Grundlage:** `CLAUDE.md`, `.idea/architektur.md` (Datenmodell, Berechtigungen,
  Authentifizierung, Schnittstellen, Betrieb), `.idea/prompts/M4.md` (Auftrag, Bedrohungsmodell).
- **Vorgehen:** Vollständiges Lesen der neuen und geänderten Module in `core/domain/identity.py`,
  `core/services/{auth,oidc,users,documents,drawers,_access}.py`, `adapters/inbound/rest/*`,
  `adapters/outbound/{crypto,oidc,sql,memory}/*`, `composition/*`, Migration `v0004`, README und
  Tests. Unit-Tests ausgeführt: 863 bestanden, 3 übersprungen (ohne Docling; `torch` war über
  das Netz nicht installierbar, deshalb `uv sync --no-install-package torch`). Integrationstests
  (Postgres, Garage) konnten in dieser Umgebung nicht laufen.
- **Belegtests:** Branch `m4-security-review`, Dateien
  `backend/tests/unit/services/test_security_review_m4.py` und
  `backend/tests/unit/adapters/rest/test_security_review_m4.py`. Sie kodieren das empfohlene
  Verhalten und schlagen auf dem geprüften Stand absichtlich fehl (7 Tests). Code wurde nicht
  geändert.

## Zusammenfassung

Keine kritischen Befunde. Die Zugriffskontrolle auf Dokumente und Schubladen ist konsequent:
eine Regel (`permissions.document_access`), in SQL gespiegelt (`_visible_to`) und im Service
nochmals geprüft; fremde und gelbe/rote Dokumente sind auf allen geprüften Wegen „nicht
gefunden“. Passwort-Hashing, Token-Speicherung, Cookie-Attribute, CSRF, OIDC-Prüfungen und
die Behandlung von Geheimnissen sind sorgfältig. Die Schwachstellen liegen in der
Drosselung (Verfügbarkeit, Umgehung), in der Reichweite von Admin-API-Tokens und in fehlenden
Größengrenzen für JSON-Eingaben.

| Nr. | Schwere | Befund | Vor Merge | Status |
| --- | --- | --- | --- | --- |
| M4-01 | hoch | Quellen-Drossel sperrt im Standard-Deployment alle Nutzer aus | ja | behoben (`1cc8bb1`) |
| M4-02 | mittel | Drossel ist nicht atomar; gleichzeitige Versuche umgehen sie | ja | behoben (`dc504de`) |
| M4-03 | mittel | Admin-API-Token übernimmt das eigene Admin-Konto (Passwort, TOTP) | ja | behoben (`f714d22`) |
| M4-04 | mittel | JSON-Bodies ohne Größengrenze, auch an `POST /auth/login` | ja | behoben (`74b3b2a`) |
| M4-05 | mittel | API-Tokens überleben Passwortwechsel und Admin-Reset (Design) | nein (entschieden) | Entscheidung: keine Änderung |
| M4-06 | gering | OIDC-Anmeldung lässt eine bestehende Session serverseitig gültig | nein | behoben (`dfd38a0`) |
| M4-07 | gering | Eingegebener Nutzername wird bei Fehlversuchen geloggt | nein | behoben (`fa432ff`) |
| M4-08 | gering | Kein `Cache-Control: no-store` auf `/auth/*`; Docs/OpenAPI/Health öffentlich | nein | behoben (`c95ff0a`); Docs/OpenAPI/Health bleiben öffentlich |
| M4-09 | gering | Quellen-Drossel pro Einzeladresse (IPv6), `*` vertraut jedem `X-Forwarded-For` | nein | behoben (`025674a`) |
| M4-10 | gering | Freigabe an deaktivierte Nutzer möglich | nein | behoben (`06afc3d`) |
| M4-11 | gering | Dev-Schlüssel und Dev-Admin-Passwort im Repository; kein Schutz vor Übernahme in Produktion | nein | behoben (`ec33e1b`) |
| M4-12 | gering | Enumeration und Timing: akzeptierte Unterschiede, dokumentiert | nein | akzeptiert |

## Befunde

### M4-01 (hoch): Quellen-Drossel sperrt im Standard-Deployment alle Nutzer aus

**Fundstellen:** `core/domain/identity.py:345` (`SOURCE_THROTTLE`: 30 Fehlversuche in 15 Minuten
sperren die Quelle 15 Minuten, auch für richtige Passwörter), `core/services/auth.py:156`
(`_check_throttles` vor jeder Prüfung), `adapters/inbound/rest/auth.py:172` (`client_address`
= `request.client.host`), `composition/api.py:112` (`proxy_headers` nur mit
`PAPIQ_FORWARDED_ALLOW_IPS`), `composition/settings.py:100,110` (Default: keine Proxies,
`cookie_secure=true`).

**Problem:** Papiq spricht nur HTTP. Mit dem Default `PAPIQ_COOKIE_SECURE=true` schickt kein
Browser das Session-Cookie über HTTP, also steht in jeder funktionierenden Produktion ein
TLS-terminierender Reverse-Proxy davor. Ohne `PAPIQ_FORWARDED_ALLOW_IPS` (Default) ist
`request.client.host` dann immer die Adresse des Proxys: alle Nutzer teilen sich eine
„Quelle“. Ein nicht angemeldeter Angreifer sperrt mit 30 Anfragen pro 15 Minuten
(beliebige Nutzernamen) die Passwort-Anmeldung der ganzen Instanz dauerhaft. Dasselbe gilt
hinter NAT/CGNAT auch bei korrekter Proxy-Konfiguration für alle Nutzer hinter derselben
Adresse. Der Kommentar zur Konto-Drossel („No hard lock, so nobody can lock others out on
purpose“, `identity.py:339`) gilt für die Quellen-Drossel nicht; die README beschreibt das
Verhalten, warnt aber nicht vor der Folge.

**Belegtests:** `test_production_settings_need_the_trusted_proxies` (Settings-Prüfung),
`test_failures_from_one_address_do_not_refuse_other_accounts` (Service; widerspricht bewusst
`test_failures_per_source_block_the_source`, siehe Empfehlung).

**Empfehlung (Entscheidung des Besitzers):**
1. Mindestens: Start verweigern oder laut warnen, wenn `cookie_secure=true` und
   `forwarded_allow_ips` leer ist; README: hinter einem Proxy ist die Variable Pflicht.
2. Besser zusätzlich: die Quellen-Sperre nur auf **falsche** Anmeldungen wirken lassen,
   d. h. bei gesperrter Quelle Passwort trotzdem prüfen und nur Fehlversuche abweisen
   (Kosten bleiben durch den Argon2-Semaphor begrenzt); oder die Quellen-Drossel wie die
   Konto-Drossel als Verlangsamung statt als Sperre ausführen. Die Konto-Drossel (die auch
   richtige Passwörter abweist, wichtig gegen TOTP-Raten) bleibt unverändert.

### M4-02 (mittel): Drossel ist nicht atomar; gleichzeitige Versuche umgehen sie

**Fundstellen:** `core/services/auth.py:464` (`_check_throttles` liest), `:474` (`_fail`
schreibt erst nach der Hash-Prüfung; bei Versionskonflikt drei Versuche, dann „at worst one
failure goes uncounted“, `:70`).

**Problem:** Zwischen Lesen des Zählers und Schreiben liegt die Argon2-Prüfung (≥ 100 ms,
maximal vier parallel). N gleichzeitig eintreffende Versuche passieren alle die Prüfung mit
Zählerstand 0. Im Belegtest werden 60 gleichzeitige Versuche auf ein Konto von einer Quelle
alle geprüft, keiner abgewiesen; erwartet wären höchstens 30 (Quelle) bzw. 6 (Konto). Ein
Angreifer rät damit pro 15-Minuten-Fenster beliebig viele Passwörter in einem Schub statt 30,
und bei TOTP (Passwort bekannt) beliebig viele Codes statt ~6. Unter Postgres gehen bei
Konflikten außerdem Zählungen verloren (mehr als eine, wenn viele Versuche gleichzeitig
auf dieselbe Zeile schreiben).

**Belegtest:** `test_a_concurrent_burst_does_not_bypass_the_throttle`.

**Empfehlung:** Zählen **vor** der Prüfung und atomar: in der Datenbank
`UPDATE login_failures SET failures = failures + 1 … RETURNING` (bzw. `INSERT … ON CONFLICT`)
statt Lesen-Rechnen-Schreiben mit Version; bei Erfolg den Konto-Zähler löschen (wie heute).
Damit entfällt auch das Retry in `_fail`. Alternativ ein Reservieren des Versuchs
(`failures + 1` beim Eintritt, bei Erfolg zurück), wenn Fehlversuche weiterhin erst nach der
Prüfung „zählen“ sollen.

### M4-03 (mittel): Admin-API-Token übernimmt das eigene Admin-Konto

**Fundstellen:** `adapters/inbound/rest/users.py:108` (`POST /users/{id}/password`) und `:123`
(`DELETE /users/{id}/totp`) nehmen `CurrentUser` (Session **oder** Token);
`core/services/users.py:128,155` (`reset_password`, `disable_totp`) schließen `id == actor`
nicht aus. Gegenstück: `adapters/inbound/rest/auth.py:104` (`session_principal`: „an API
token cannot be used to take over an account“).

**Problem:** Die Regel, dass ein Token die eigene Anmeldung nicht verwalten darf, wird für
Admins über die Admin-Endpunkte umgangen: Mit einem geleakten `read_write`-Token eines
Admins setzt der Angreifer das Passwort des Admins neu, schaltet dessen TOTP ab, meldet sich
mit Session an und hat dann alles (eigene Tokens, Passwort, TOTP, OIDC-Verknüpfungen).
Der rechtmäßige Admin ist zugleich ausgesperrt. Allgemeiner: ein Admin-Token kann neue
Admins mit Passwort anlegen und fremde Passwörter zurücksetzen; es ist faktisch ein
vollwertiges Admin-Credential ohne zweiten Faktor und ohne Ablauf (optional).

**Belegtests:** `test_an_admin_token_cannot_reset_the_admins_own_password`,
`test_an_admin_token_does_not_lead_to_a_session` (die Kette Token → Reset → Session).

**Empfehlung (Entscheidung des Besitzers):** Kontoverwaltung, die Anmeldedaten verändert
(Passwort-Reset, TOTP aus, OIDC-Links entfernen, Nutzer mit Passwort anlegen), nur mit
Session (`SessionPrincipal`), wie bei `/auth/*`; mindestens aber Selbstbezug (`id == actor`)
über Token ablehnen. Falls Provisionierung per CLI gewünscht ist: eigener Token-Scope
(z. B. `admin`), den die README als Admin-Credential ausweist.

### M4-04 (mittel): JSON-Bodies ohne Größengrenze

**Fundstellen:** Nur `adapters/inbound/rest/upload.py:54` begrenzt (Multipart,
`PAPIQ_UPLOAD_MAX_SIZE`). Für alle JSON-Endpunkte liest FastAPI den Body vollständig in den
Speicher, bevor validiert wird; keine Middleware, kein Uvicorn-Limit (`composition/api.py`).

**Problem:** Ein nicht angemeldeter Client schickt an `POST /auth/login` einen beliebig großen
JSON-Body (Belegtest: 16 MiB → `422` statt `413`, der Body wurde ganz gelesen); mehrere
parallele Anfragen erschöpfen den Speicher des API-Prozesses. Angemeldet dasselbe über
`PATCH /documents/{id}` (`attributes: dict[UUID, Any]`, beliebig verschachtelt). Ein
Reverse-Proxy kann das begrenzen, aber die Anwendung sollte nicht darauf angewiesen sein.

**Belegtest:** `test_json_bodies_are_bounded`.

**Empfehlung:** ASGI-Middleware oder Wrapper um `request.stream()`: `Content-Length` und
gezählte Bytes gegen ein Limit (z. B. 1 MiB für JSON; Upload-Route ausgenommen, die hat ihr
eigenes), Antwort `413` als Problem.

### M4-05 (mittel, Designentscheidung): API-Tokens überleben Passwortwechsel und Admin-Reset

**Fundstellen:** `core/services/auth.py:272` (`revoke_tokens: bool = False`),
`core/services/users.py:128` (`reset_password`, gleicher Default); README „Identity“.

**Problem:** Wer eine Session kapert, legt ein `read_write`-Token an (Sessions dürfen das).
Wechselt der Nutzer später das Passwort oder setzt ein Admin es zurück (typisch bei
Kompromittierungsverdacht), bleibt das Token gültig, solange niemand `revoke_tokens`
setzt. Das Bedrohungsmodell in `M4.md` fragt ausdrücklich nach Tokens bei Passwortwechsel.

**Empfehlung:** Default umkehren (`revoke_tokens=true`), mindestens beim Admin-Reset; oder in
der Antwort auf Passwortwechsel die Zahl der weiterlebenden Tokens nennen, damit die UI
nachfragen kann. Kein Belegtest, da bewusste Entscheidung.

### M4-06 (gering): OIDC-Anmeldung lässt eine bestehende Session gültig

**Fundstelle:** `core/services/oidc.py:157` (`sign_in` startet eine neue Session; die
Session, die der Browser gerade trägt, wird nicht beendet), `adapters/inbound/rest/account.py`
`oidc_callback` (Cookie wird überschrieben).

**Problem:** Meldet sich ein Browser mit Session A per OIDC als anderer (oder derselbe) Nutzer
an, bleibt Session A serverseitig bis zu 24 Stunden gültig, obwohl der Nutzer sie nicht
mehr sieht und nicht beenden kann.

**Empfehlung:** Beim OIDC-Callback die mitgeschickte Session beenden (oder Anmeldung bei
bestehender Session ablehnen, wie bei der Verknüpfung).

### M4-07 (gering): Eingegebener Nutzername wird bei Fehlversuchen geloggt

**Fundstelle:** `core/services/auth.py:495` (`extra={"keys": [...]}` enthält
`account:<nutzername casefold>` und `source:<adresse>`).

**Problem:** Ein ins Nutzername-Feld getipptes Passwort (häufiger Bedienfehler) landet im
Log. Der Belegtest zeigt es.

**Empfehlung:** Nutzernamen im Log nur gehasht/gekürzt oder nur für bekannte Konten (die
`user_id`); die Quelle kann bleiben.

### M4-08 (gering): Fehlende `Cache-Control`-Header; öffentliche Dokumentation

**Fundstellen:** Antworten von `GET /auth/me` (CSRF-Token), `POST /auth/totp` (Geheimnis),
`/auth/totp/confirm`, `/auth/totp/recovery-codes` (Wiederherstellungscodes), `POST /auth/tokens`
(Token) tragen keinen `Cache-Control: no-store` (nur Downloads, `documents.py`).
`adapters/inbound/rest/app.py:88` (`openapi.json`, `/docs` öffentlich), `GET /health` öffentlich.

**Empfehlung:** `Cache-Control: no-store` auf alle `/auth/*`-Antworten (oder global für JSON).
Docs/OpenAPI/Health öffentlich ist für ein Open-Source-Projekt vertretbar; eine Option, sie
nur angemeldet auszuliefern, wäre ein Plus.

### M4-09 (gering): Quellen-Drossel pro Einzeladresse; `*` vertraut jedem

**Fundstellen:** `core/domain/identity.py:354` (`source_key` = Adresse roh),
`composition/settings.py:100` (`*` erlaubt).

**Problem:** Ein Angreifer mit einem IPv6-/64 hat praktisch unbegrenzt viele Quellen; die
Quellen-Drossel greift dann nicht, nur die Konto-Drossel. Mit `PAPIQ_FORWARDED_ALLOW_IPS=*`
ist `X-Forwarded-For` frei wählbar und die Quellen-Drossel umgehbar (und M4-01 trivial
auslösbar für beliebige Adressen). Jeder unbekannte Nutzername erzeugt zudem eine Zeile in
`login_failures` (Aufräumen erst nach dem Fenster).

**Empfehlung:** IPv6 auf /64 (ggf. /56) bündeln; README: `*` nur hinter einem Proxy, der den
Header selbst setzt.

### M4-10 (gering): Freigabe an deaktivierte Nutzer

**Fundstelle:** `core/services/drawers.py:59` (`uow.users.get(user)` ohne Aktiv-Prüfung),
während `UserService.list/get` deaktivierte Nutzer vor Nicht-Admins verbirgt.

**Problem:** Harmlos im Moment (deaktivierte Nutzer lesen nichts), aber die Freigabe wird mit
der Reaktivierung wirksam, ohne dass der Besitzer es sieht. Inkonsistent zur Nutzerliste.

**Empfehlung:** Freigabe nur an aktive Nutzer (sonst `404`, wie in der Liste).

### M4-11 (gering): Dev-Geheimnisse im Repository

**Fundstellen:** `.devcontainer/dev.env:7,10` (`PAPIQ_SECRET_KEY`, `PAPIQ_ADMIN_PASSWORD`).

**Problem:** Für die Entwicklung richtig und dokumentiert. Risiko: Übernahme der Datei in eine
Produktion; der bekannte Schlüssel entschlüsselt dann alle TOTP-Geheimnisse und
OIDC-Flow-Cookies.

**Empfehlung:** Start mit dem bekannten Dev-Schlüssel bei `cookie_secure=true` verweigern;
README: `PAPIQ_SECRET_KEY_FILE` und `PAPIQ_ADMIN_PASSWORD_FILE` für Produktion empfehlen.

### M4-12 (gering, akzeptiert): Enumeration und Timing

- `SecondFactorRequiredError` (`core/services/auth.py:170`) verrät nach **richtigem**
  Passwort, dass TOTP aktiv ist; vor der Passwortprüfung nichts. Entspricht dem Auftrag
  („Passwort und Code erneut senden“); akzeptabel.
- Unbekannter Nutzername spart eine Datenbankabfrage (`credentials.find`) gegenüber bekanntem;
  Unterschied im Millisekundenbereich gegenüber ≥ 100 ms Argon2 (Dummy-Hash,
  `auth.py:458`). Akzeptabel.
- `readable_document` macht für fremde Dokumente zwei Abfragen, für fehlende eine.
  Theoretisch messbar, praktisch nicht. Akzeptabel.
- Bei OIDC-Auto-Create verrät `409 username 'x' is taken` die Existenz eines lokalen Kontos
  gegenüber dem angemeldeten IdP-Nutzer. Akzeptabel (Nutzerliste ist ohnehin sichtbar).

## Geprüft und in Ordnung

**1. Zugriffskontrolle.** Eine Regel in `core/domain/permissions.py` (`document_access`: Besitzer
immer; andere nur über Schublade und nur bei `GREEN`; auch Schubladenbesitzer und Admins sehen
fremde gelbe/rote/laufende Dokumente nicht). SQL spiegelt sie in `_visible_to`
(`sql/repositories.py`), `query_visible` setzt Filter (`drawer_id`, `contact_id`, Tags, Lanes)
nur **innerhalb** der sichtbaren Menge, der Service prüft jedes Ergebnis erneut und loggt
Abweichungen. Cursor ist eine Dokument-ID und verrät nichts. Detail, Downloads
(`readable_document`), Protokoll (Besitzer), Verschieben (Besitzer in schreibbare Schublade;
Admin ohne Leserecht, wie Architektur), Löschen/Retry/Reprocess (Besitzer) sind konsistent.
Fremde Dokumente und Schubladen sind `404` mit identischem Body wie fehlende
(`test_hidden_and_missing_documents_look_alike`). SSE: `_visible_document` vor Streamstart,
`filter_readers` pro Ereignis mit aktuellem Stand, Re-Authentifizierung alle 30 s
(`still_authenticated`). Upload in fremde Schublade: `visible_drawer` + `can_file_into`,
nochmals in der Transaktion. Duplikaterkennung nur je Besitzer (`find_by_sha256(actor, …)`).
Rechte-Matrix über HTTP vorhanden (`test_permission_matrix.py`), ebenso der Test, dass jede
registrierte Route ohne Anmeldung `401` liefert und genau fünf Routen öffentlich sind.

**2. Authentifizierung.** Argon2id mit RFC-9106-Low-Memory-Profil, Semaphor 4, Worker-Thread;
Rehash bei Anmeldung; NFKC-Normalisierung; Policy 12–256 Zeichen, nicht der Nutzername
(NIST SP 800-63B). Unbekannte und deaktivierte Nutzer gegen Dummy-Hash geprüft, gleiche
Fehlermeldung. TOTP: ±1 Schritt, jeder Schritt nur einmal (`accept_totp_step`, nur bei Erfolg
committet), Bestätigung per Code, Wiederherstellungscodes 80 Bit als SHA-256, einmalig;
Abschalten nur mit Code/Wiederherstellungscode oder Admin; TOTP-Raten zählt als Fehlversuch.
Ersteinrichtung nur solange kein Admin existiert, nie Änderung eines bestehenden Kontos,
Name eines Nicht-Admins bricht den Start ab. Letzter aktiver Admin geschützt, mit
Versionsschreiben gegen gleichzeitige Herabstufung.

**3. Sessions und Tokens.** 256-Bit-Zufall, nur SHA-256 gespeichert, Vergleich über den Hash
(konstantzeitig in der Domäne). Cookie `__Host-papiq_session`: HttpOnly, Secure, SameSite=Lax,
Path=/, keine Domain; ohne `Secure` nur mit `PAPIQ_COOKIE_SECURE=false`. CSRF: Header
`X-CSRF-Token` = HMAC(Session-Token) auf allen nicht-sicheren Methoden mit Session; Bearer
ignoriert Cookies; kein CORS. Login nur als `application/json` (Login-CSRF per Formular
ausgeschlossen, getestet). Passwortwechsel beendet alle Sessions (eigene wird erneuert) mit
Konflikterkennung; Deaktivierung beendet Sessions und sperrt Tokens; Rollenwechsel wirkt
sofort (Nutzer wird pro Anfrage gelesen). `read`-Tokens: zentral in `authenticate` für alle
Methoden außer GET/HEAD/OPTIONS abgewiesen; keine GET-Route mit Seiteneffekt gefunden.
Verwaltung der eigenen Anmeldung nur mit Session (aber siehe M4-03). Ablauf/Idle serverseitig,
Idle-Touch höchstens minütlich.

**4. OIDC.** Authorization Code mit PKCE S256; `state`, `nonce`, Verifier, Ziel und ggf.
Link-Nutzer AES-GCM-versiegelt im HttpOnly-Cookie (10 Minuten) → Flow an den Browser
gebunden; `state` konstantzeitig verglichen. ID-Token: nur asymmetrische, vom Provider
angekündigte Algorithmen (`none`/HMAC getestet abgelehnt), Signatur gegen JWKS (einmaliger
Refetch bei unbekannter `kid`), `iss` exakt, `aud` enthält Client-ID, `azp` bei mehreren,
`exp`/`iat` mit 60 s, `nonce`. Discovery-`issuer` muss der Konfiguration gleichen; Issuer
muss `https` sein. Verknüpfung über `(issuer, subject)` mit Unique-Index, nie E-Mail;
Verknüpfen nur, wenn derselbe Nutzer am Callback noch angemeldet ist (`caller == link_user`);
`POST /auth/oidc/link` CSRF-geschützt. Auto-Create default aus. `safe_redirect` lässt nur
Pfade auf dieser Site zu (`//`, `\`, Steuerzeichen, Länge geprüft). Access-Log verbirgt die
Callback-Query.

**5. Geheimnisse.** Passwörter als `SecretStr`; Tokens/Codes nur in der Antwort der Erzeugung;
Logs mit IDs, Fehlerart und Claim-Namen statt Werten (`test_nothing_secret_is_logged`); 500er
ohne Details; OpenAPI-Beispiele mit Platzhaltern; keine Produktionsgeheimnisse in Tests. TOTP-
Geheimnis AES-256-GCM mit Nutzer-ID als Associated Data (kein Vertauschen zwischen Nutzern),
Schlüssel 32 Byte base64, `_FILE`-Variante über den generischen Mechanismus; Worker ohne
Schlüssel erhält `MissingKeyCipher`. Kein Schlüsselwechsel vorgesehen (Versionsbyte existiert) –
Hinweis, kein Befund.

**6. Eingaben.** Upload: Streaming mit Limit über `Content-Length` und gezählte Bytes,
Dateiname bereinigt (Steuerzeichen, 255), Typ per Inhalt. Downloads über Tempdatei mit
`nosniff`, `CSP: sandbox`, `no-store`; Dateiname von Starlette RFC-5987-kodiert. Pydantic-
Modelle mit `extra="forbid"`, Längen für Nutzername, Passwort, Codes, Namen, Listen, Query-
Parameter (`cursor` 64, `tag_id` ≤ 50, `next` ≤ 2000). Offen: Gesamtgröße von JSON-Bodies
(M4-04).

## Entscheidungen des Besitzers (2026-10-07)

- **M4-01:** Beides: Start bricht mit `Secure`-Cookies ohne `PAPIQ_FORWARDED_ALLOW_IPS` ab, und
  die Quellen-Sperre weist nur falsche Anmeldungen ab (richtiges Passwort kommt durch; die
  Konto-Drossel bleibt wie sie ist). Der bestehende Test zur Quellen-Sperre wird entsprechend
  geändert.
- **M4-05:** Keine Änderung; `revoke_tokens` bleibt standardmäßig `false`.
- Alle übrigen Befunde werden wie empfohlen behoben; Arbeitsauftrag in
  `.idea/prompts/M4-fixes.md`.

## Vor dem Merge beheben

1. **M4-01** – Lockout über geteilte Quelladresse: Konfigurationsprüfung/-warnung und README;
   Entscheidung, ob die Quellen-Sperre richtige Anmeldungen durchlassen soll.
2. **M4-02** – Drossel atomar zählen (DB-Increment vor der Prüfung).
3. **M4-03** – Admin-Endpunkte für Anmeldedaten nur mit Session, mindestens kein Selbstbezug per
   Token.
4. **M4-04** – Größengrenze für JSON-Bodies (`413`).

**Entscheidung des Besitzers:** M4-05 (Token-Widerruf als Default). Die übrigen Befunde
(gering) können nach dem Merge folgen.

## Behebung (2026-10-07)

Auf `claude/charming-knuth-2ihyum`, ein Commit je Befund (Status-Spalte oben). Die Belegtests
sind grün und thematisch eingeordnet (`test_auth.py`, `test_authentication.py`,
`test_resources.py`, `test_settings.py`); die beiden Review-Dateien sind entfernt. Anpassung
des Belegtests zu M4-02: Seine Warteschleife endet jetzt auch, wenn Versuche sofort
abgewiesen werden (vorher hätte sie mit der Behebung endlos gewartet); die Aussage ist
unverändert. Der Belegtest zu M4-01 (Settings) erwartet `ConfigurationError` schon beim
Erzeugen von `Settings`; Konsistenzfehler werden deshalb jetzt direkt so ausgelöst.
