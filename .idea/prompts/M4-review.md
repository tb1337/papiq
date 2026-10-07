# Aufgabe: Sicherheits-Review M4 (unabhängig)

Empfohlener Start: `claude --model fable --effort high`

Neue Sitzung, ohne Kontext aus der Umsetzung. Du hast M4 nicht geschrieben und sollst es nicht
verteidigen, sondern Schwachstellen finden.

## Kontext
Lies `CLAUDE.md`, `.idea/architektur.md` (Berechtigungen, Authentifizierung, Betrieb) und
`.idea/prompts/M4.md` (Auftrag und Bedrohungsmodell). Prüfe dann den Pull Request bzw. Branch `m4-auth`
gegen `main`.

## Prüfe insbesondere
1. **Zugriffskontrolle:** Kann ein Nutzer über irgendeinen Endpunkt, Filter, Paginierung, Download,
   SSE oder Fehlermeldung fremde Dokumente, Schubladen oder deren Existenz erfahren? Gelb/Rot nur für
   den Besitzer?
2. **Authentifizierung:** Passwort-Hashing, Timing-Unterschiede, Brute-Force-Begrenzung, TOTP
   (Wiederverwendung eines Codes, Zeitfenster, Wiederherstellungscodes), Ersteinrichtung des Admins.
3. **Sessions und Tokens:** Cookie-Attribute, CSRF, Widerruf bei Passwortwechsel/Deaktivierung/
   Rollenwechsel, Token-Speicherung, `read`-Tokens wirklich nur lesend.
4. **OIDC:** State, Nonce, PKCE, Prüfung von Issuer, Audience und Signatur, Kontoverknüpfung über
   Subject, offene Weiterleitungen.
5. **Geheimnisse:** in Logs, Fehlermeldungen, OpenAPI, Tests; Verschlüsselung der TOTP-Geheimnisse,
   Umgang mit dem Schlüssel.
6. **Eingaben:** Upload-Grenzen, Pfade, Header, Größen und Typen aller Eingaben.

## Vorgehen
- Belege jeden Befund mit Fundstelle und, wo möglich, einem fehlschlagenden Test.
- Ordne nach Schwere (kritisch, hoch, mittel, gering).
- Ändere keinen Code. Schreibe den Bericht nach `.idea/reviews/M4-security.md` und lege die
  fehlschlagenden Tests auf einem Branch `m4-security-review` ab.
- Am Ende: kurze Zusammenfassung mit den Befunden, die vor dem Merge behoben werden müssen.
