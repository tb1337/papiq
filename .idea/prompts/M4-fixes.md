# Aufgabe: Befunde des Sicherheits-Reviews M4 beheben

Empfohlener Start: `claude --model opus --effort high`

Voraussetzung: Das Review in `.idea/reviews/M4-security.md` ist gelesen. Die Befunde sind dort
mit Fundstelle, Begründung und Empfehlung beschrieben; hier stehen nur die Entscheidungen und
der Arbeitsauftrag.

## Kontext
Lies zuerst vollständig:
- `CLAUDE.md`, `backend/README.md` (Abschnitte Identity, REST API, Konfiguration)
- `.idea/architektur.md` (Berechtigungen, Authentifizierung, Betrieb)
- `.idea/reviews/M4-security.md` (alle Befunde, auch „Geprüft und in Ordnung“)
- `backend/src/papiq/core/services/auth.py`, `core/domain/identity.py`,
  `adapters/inbound/rest/{auth,account,users}.py`, `composition/{settings,api}.py`

Halte Dich an die Entscheidungen unten. Fehlt eine Entscheidung oder ist etwas unklar, frag
mich, bevor Du es umsetzt. Ports und Vertragstests ändern nur nach Rücksprache.

## Branches
- Arbeite auf dem M4-Branch (`claude/charming-knuth-2ihyum`), der gegen `main` gemergt werden
  soll.
- Merge zuerst `m4-security-review` hinein: Er bringt den Bericht, diesen Prompt und die
  Belegtests `backend/tests/unit/services/test_security_review_m4.py` und
  `backend/tests/unit/adapters/rest/test_security_review_m4.py`. Die Belegtests schlagen
  absichtlich fehl und sind das Abnahmekriterium: Am Ende sind sie grün. Verschiebe sie dann
  thematisch zu den bestehenden Tests (z. B. `test_auth.py`, `test_authentication.py`,
  `test_settings.py`) und lösche die beiden Review-Dateien; Docstrings dürfen weiter auf die
  Befundnummer verweisen.

## Entscheidungen

### M4-01 – Quellen-Drossel sperrt alle Nutzer aus (hoch): beides umsetzen
1. **Konfiguration:** `PAPIQ_COOKIE_SECURE=true` (Default) ohne `PAPIQ_FORWARDED_ALLOW_IPS` ist
   ein Konfigurationsfehler, der den Start abbricht, mit klarer Meldung: Mit `Secure`-Cookies
   steht ein TLS-terminierender Proxy vor Papiq, dessen Adresse genannt werden muss. Mit
   `PAPIQ_COOKIE_SECURE=false` (Entwicklung, Direktzugriff) bleibt die Variable optional.
   README: Variable als Pflicht hinter einem Proxy dokumentieren; `*` nur, wenn der Proxy den
   Header selbst setzt und überschreibt. Bestehende Settings-Tests anpassen.
2. **Verhalten:** Die Quellen-Sperre weist nur **falsche** Anmeldungen ab. Bei gesperrter
   Quelle wird das Passwort trotzdem geprüft; ist es richtig (und ggf. der zweite Faktor),
   wird angemeldet und nichts gezählt; ist es falsch, antwortet `429` statt `401`, ohne
   erneut zu zählen. Die Konto-Drossel bleibt unverändert: Sie weist auch richtige Passwörter
   ab (Schutz gegen TOTP-Raten) und hat keine harte Sperre. Den bestehenden Test
   `test_failures_per_source_block_the_source` entsprechend ändern; der Belegtest
   `test_failures_from_one_address_do_not_refuse_other_accounts` wird grün. Docstring von
   `auth.py` und README-Abschnitt „Sign-in“ anpassen.

### M4-02 – Drossel nicht atomar (mittel)
Fehlversuche atomar in der Datenbank zählen, nicht Lesen–Rechnen–Schreiben mit Version:
`LoginFailureRepository` bekommt eine Operation, die den Zähler in einer Anweisung erhöht
(Postgres/SQLite: `INSERT … ON CONFLICT DO UPDATE … RETURNING`, In-Memory äquivalent) und die
Sperrzeit nach `ThrottleRule` setzt. Der Versuch wird **vor** der Passwortprüfung
reserviert (Zähler +1) und bei Erfolg zurückgenommen bzw. der Konto-Zähler gelöscht wie heute,
so dass gleichzeitige Versuche die Drossel nicht umgehen. `_RECORD_ATTEMPTS` und das Retry in
`_fail` entfallen. Port-Änderung: Schlag mir die Signatur vor, bevor Du sie einbaust;
Vertragstest für In-Memory, SQLite und Postgres ergänzen. Abnahme:
`test_a_concurrent_burst_does_not_bypass_the_throttle`.

### M4-03 – Admin-Token übernimmt das eigene Konto (mittel)
Admin-Endpunkte, die Anmeldedaten verändern, brauchen eine Session (`SessionPrincipal`), wie
`/auth/*`: `POST /users` (Anlegen mit Passwort), `POST /users/{id}/password`,
`DELETE /users/{id}/totp`, `DELETE /users/{id}/oidc`. `GET`, `PATCH` (Rolle, Status) und
`DELETE /users/{id}` bleiben per Token erlaubt. Zusätzlich im Service: Passwort-Reset und
TOTP-Abschalten auf das eigene Konto (`id == actor`) ablehnen (`PermissionDeniedError`); dafür
gibt es `/auth/password` und `/auth/totp/disable`. OpenAPI-Beschreibungen und README (Tabelle
der Endpunkte, Abschnitt Authentication) anpassen; `test_tokens_cannot_manage_the_sign_in`
um die Admin-Endpunkte erweitern. Abnahme: beide M4-03-Belegtests.

### M4-04 – JSON-Bodies ohne Größengrenze (mittel)
Eine ASGI-Middleware in `adapters/inbound/rest` begrenzt die Größe aller Request-Bodies außer
dem Upload (`POST /documents`, hat sein eigenes Limit): `Content-Length` prüfen und gezählte
Bytes beim Lesen; Überschreitung antwortet `413` als Problem (vorhandener `UploadTooLargeError`
oder eigener Fehler, in `problems.py` registriert). Limit als `PAPIQ_REQUEST_MAX_SIZE`
(Default 1 MiB, `ByteSize` wie `PAPIQ_UPLOAD_MAX_SIZE`), in README-Tabelle aufnehmen.
Abnahme: `test_json_bodies_are_bounded`; zusätzlich ein Test, dass ein Upload knapp unter
`PAPIQ_UPLOAD_MAX_SIZE` weiterhin durchgeht.

### M4-05 – Token-Widerruf bei Passwortwechsel: **keine Änderung**
`revoke_tokens` bleibt standardmäßig `false`, bei eigenem Wechsel und beim Admin-Reset.

### M4-06 – OIDC-Anmeldung und bestehende Session (gering)
Am OIDC-Callback die Session beenden, die der Browser mitschickt (falls vorhanden), bevor die
neue startet. Test ergänzen.

### M4-07 – Nutzername im Log (gering)
`sign-in failed` loggt nicht mehr den Account-Schlüssel. Bekanntes Konto: `user_id`;
unbekanntes: nur `account_known=false`. Die Quelle darf bleiben. Abnahme:
`test_failed_sign_ins_do_not_log_the_typed_username`.

### M4-08 – Cache-Header (gering)
Alle Antworten unter `/auth/*` tragen `Cache-Control: no-store` (Router-weit, z. B. über eine
Dependency oder in der Middleware aus M4-04). Docs, OpenAPI und Health bleiben öffentlich.

### M4-09 – Quellen-Schlüssel (gering)
`source_key` bündelt IPv6-Adressen auf /64 (IPv4 unverändert); ungültige Adressen unverändert
als Text. README-Hinweis zu `*` siehe M4-01.

### M4-10 – Freigabe an deaktivierte Nutzer (gering)
`DrawerService.share` lehnt deaktivierte Nutzer wie unbekannte ab (`NotFoundError`).

### M4-11 – Dev-Geheimnisse (gering)
Start verweigern, wenn `PAPIQ_SECRET_KEY` dem Schlüssel aus `.devcontainer/dev.env` entspricht
und `PAPIQ_COOKIE_SECURE=true` ist. README: für Produktion `PAPIQ_SECRET_KEY_FILE` und
`PAPIQ_ADMIN_PASSWORD_FILE` empfehlen.

### M4-12 – akzeptiert, nichts zu tun.

## Arbeitsweise
1. Plan-Modus zuerst: Reihenfolge, Port-Signatur für M4-02, Middleware-Entwurf für M4-04 und
   M4-08, betroffene Tests. Beginne erst nach meiner Freigabe.
2. Ein Commit je Befund, in der Reihenfolge M4-01 bis M4-04, dann M4-06 bis M4-11. Alle fünf
   Checks (`ruff check`, `ruff format --check`, `mypy`, `lint-imports`, `pytest`) vor jedem
   Commit; Integrationstests im Devcontainer.
3. Nach jedem Befund kurz melden, welcher Belegtest jetzt grün ist.
4. Zum Schluss den Bericht `.idea/reviews/M4-security.md` um eine Spalte „Status“ ergänzen
   (behoben mit Commit-Hash / Entscheidung: keine Änderung / akzeptiert) und
   `.idea/umsetzungsplan.md` unter M4 um den Hinweis auf Review und Behebung ergänzen.

## Nicht Teil dieser Aufgabe
Weitere Härtungen über den Bericht hinaus (z. B. Passwort-Sperrlisten, Schlüsselrotation,
Begrenzung der SSE-Verbindungen je Nutzer), Web-UI, M5 ff.

## Fertig, wenn
- Alle Belegtests aus dem Review sind grün und thematisch eingeordnet; kein bestehender Test
  wurde gelöscht, ohne dass ein geänderter ihn ersetzt.
- `PAPIQ_FORWARDED_ALLOW_IPS` ist mit `Secure`-Cookies Pflicht; die Quellen-Sperre weist nur
  falsche Anmeldungen ab; Fehlversuche werden atomar gezählt.
- Admin-Endpunkte für Anmeldedaten brauchen eine Session; Selbstbezug ist abgelehnt.
- Request-Bodies sind begrenzt (`413`), `/auth/*` ist `no-store`.
- README, OpenAPI-Beschreibungen und Bericht sind aktuell; alle Checks, CI-Matrix und
  Devcontainer-Job grün.
