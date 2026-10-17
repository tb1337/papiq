# Papiq – Umsetzungsplan

Stand: 06.10.2026 · Grundlage: [architektur.md](architektur.md)

Papiq wird API-first in 14 Meilensteinen (M0–M13) gebaut. Ab M3 lassen sich Dokumente per API einliefern; ab M11 ist Papiq über die Web-UI vollständig nutzbar; M13 schließt mit Release 0.1 und den ersten 100 Dokumenten ab.

## Abhängigkeiten

```mermaid
flowchart LR
    M0[M0 Fundament<br/>Devcontainer, CI] --> M1[M1 Kern]
    M1 --> M2[M2 Persistenz]
    M2 --> M3[M3 Ingest]
    M2 --> M4[M4 Auth + API]
    M3 --> M5[M5 KI + Lanes]
    M3 --> M6[M6 Suche]
    M4 --> M6
    M5 --> M7[M7 Regeln]
    M3 --> M8[M8 Webhooks + MCP]
    M4 --> M8
    M3 --> M9[M9 Container]
    M4 --> M10[M10 UI-Fundament]
    M7 --> M11[M11 UI-Funktionen]
    M8 --> M11
    M10 --> M11
    M7 --> M12[M12 Migration]
    M9 --> M13[M13 Release 0.1]
    M11 --> M13
    M12 --> M13
```

## Übersicht

| Kürzel | Meilenstein | Modell | Effort |
| --- | --- | --- | --- |
| M0 | Fundament: Devcontainer, CI, Konfiguration | Sonnet 5.5 | high |
| M1 | Domänenkern und Ports | Opus 5.5 | high |
| M2 | Persistenz (SQLite, Postgres), Jobs, Outbox | Opus 5.5 | high |
| M3 | Ingest-Pipeline: Speicher, OCR, Parsing, Worker, SSE | Opus 5.5 | high |
| M4 | Authentifizierung, Rechte, REST-API | Opus 5.5 | high (+ Review Fable 5.1 · high) |
| M5 | KI-Klassifizierung, Konfidenz, Lanes, Posteingang | Opus 5.5 | high |
| M6 | Suche mit Meilisearch | Sonnet 5.5 | high |
| M7 | Regel-Engine | Opus 5.5 | high |
| M8 | Webhooks und MCP-Server | Sonnet 5.5 | high |
| M9 | Papiq-Image mit s6-overlay | Sonnet 5.5 | high |
| M10 | Web-UI-Fundament und Design | Opus 5.5 | high |
| M11 | Web-UI-Funktionen | Sonnet 5.5 | medium (Regel-Baukasten: Opus 5.5 · high) |
| M12 | Migration aus Paperless-ngx | Sonnet 5.5 | high |
| M13 | Härtung und Release 0.1 | Fable 5.1 | high |
| M14 | Ingest-Graph: Knoten, Zustand je Knoten, Ereignisse, Sichtbarkeit in der UI (`ingest-v2.md`) | Opus 5.5 | high |
| M15 | Verstehen: Kennungen, Klassifikator, Nachbarn, Urteil, Titelmuster | Opus 5.5 | high |
| M16 | Lesen: Seitenbilder, VLM-Lesung, Bilder im Modellaufruf | Sonnet 5.5 | high |
| M17 | Grounding in der Prüfansicht (vorgezogen mit der neuen Dokumentansicht) | Sonnet 5.5 | medium |
| M18 | Eskalation und Kalibrierung | Opus 5.5 | high |
| 0.3.0 | Tags mit Arten, Gruppen, Themen, Spaces, Icons und Logos (fünf Pakete, siehe dort) | je Paket | high |

**Faustregel für die Modellwahl**

- **Opus 5.5** für alles, was Struktur festlegt oder schwer zu korrigieren ist: Kern, Datenmodell, Nebenläufigkeit, Sicherheit, Regel-Logik, Design-System.
- **Sonnet 5.5** für klar umrissene Integrationen nach festem Muster: Adapter, Container, Webhooks, Bildschirme nach Vorlage.
- **Fable 5.1** für unabhängige Prüfungen und lange, verifikationslastige Abschnitte (Sicherheits-Review, Release-Härtung).
- **Effort:** `medium` bei klarem Umfang, `high`, wenn Verifikation zählt (Default bei den meisten Modellen; Opus 5.5 startet bei `medium`). `xhigh`/`max` nur gezielt für einzelne harte Probleme, z. B. per `ultrathink` im Prompt. ([Quelle](https://code.claude.com/docs/en/model-config))
- Jeder Meilenstein beginnt mit einem Plan (Plan-Modus) und endet mit einem Review durch einen separaten Agenten, der die Umsetzung nicht geschrieben hat.

## Meilensteine

### M0 – Fundament: Devcontainer, CI, Konfiguration

**Ziel:** Entwickeln und Testen vollständig im Devcontainer; alle Abhängigkeiten starten mit.

- Devcontainer wie im Abschnitt [Devcontainer](#devcontainer) beschrieben.
- Mehrstufiges `Dockerfile` mit Ziel `dev` (Devcontainer) und `runtime` (M9), damit Entwicklung und Betrieb dieselben Systempakete nutzen.
- Konfiguration: `PAPIQ_`-Variablen mit Pydantic Settings, `_FILE`-Variablen für Docker Secrets, Abbruch bei ungültiger Konfiguration.
- Composition Root als Gerüst; strukturiertes Logging.
- CI: Lint, Typen, Import-Verträge, Tests; Testmatrix SQLite/Postgres vorbereitet.

**Fertig, wenn:** „Reopen in Container" startet Postgres, Garage und Meilisearch; `uv run pytest` läuft im Container grün; CI ist grün.

**Modell:** Sonnet 5.5 · high – viel bekanntes Muster, aber die Garage-Initialisierung und Netzwerk-Details brauchen Verifikation.

### M1 – Domänenkern und Ports

**Ziel:** Fachlogik ohne jede Technik, vollständig testbar.

- Entitäten: Nutzer, Rolle, Dokument, Kontakt, Dokumenttyp, Tag, Felddefinition und -wert, Schublade, Freigabe, Lane, Verarbeitungsschritt.
- Rechteprüfung (Besitzer, Schublade, Freigabe lesen/schreiben).
- Zustandsautomat der Pipeline und Lane-Berechnung (schlechtestes Schrittergebnis).
- Ports mit echten async Signaturen; In-Memory-Adapter für jeden Port; Vertragstest-Suiten.

**Vorher klären:** Felddatentypen.

**Fertig, wenn:** Kern-Tests decken Rechte, Zustandsautomat und Lanes ab; Import-Verträge grün.

**Modell:** Opus 5.5 · high – legt die Struktur fest, auf der alles aufbaut.

### M2 – Persistenz, Jobs, Outbox

**Ziel:** Repository-, Job-Queue- und Event-Bus-Adapter auf SQLite und Postgres.

- SQLAlchemy 2 async (aiosqlite, asyncpg), Alembic-Migrationen für beide Datenbanken.
- Job-Tabelle mit Retries, Sperren und Idempotenz.
- Outbox-Tabelle, Verteilung im Prozess; Polling (SQLite) und optional `LISTEN/NOTIFY` (Postgres).

**Fertig, wenn:** Vertragstests aus M1 laufen gegen SQLite und Postgres grün.

**Modell:** Opus 5.5 · high – die Verträge aus M1 verlangen u. a. lückenlose Ereigniszustellung trotz abweichender Commit-Reihenfolge und sicheres paralleles Claimen; das auf zwei Datenbanken braucht Verifikation.

### M3 – Ingest-Pipeline

**Ziel:** Ein Dokument per API einliefern und bis „Parsen" verarbeiten.

- Objektspeicher-Adapter: Dateisystem und S3 (Garage); SHA-256 als Schlüssel, Dublettenprüfung.
- `POST /documents` mit `202 Accepted`; Worker-Dienst; Schritte Empfangen → OCR → Parsen mit Retries.
- OCRmyPDF- und Docling-Adapter, rechenlastig in eigenen Prozessen.
- Verarbeitungsprotokoll je Schritt; „Schritt wiederholen", „ab Schritt X neu verarbeiten".
- Server-Sent Events für Fortschritt.

**Übernommen aus M2** (bewusst offen gelassen, in M3 entscheiden bzw. umsetzen):

- Outbox-Verteilung: Der Worker ruft `EventBus.dispatch` per Polling auf. Postgres `LISTEN/NOTIFY` ist nicht umgesetzt; nur ergänzen, wenn die Latenz des Pollings stört.
- Aufräumen: Erledigte und aufgegebene Jobs sowie Outbox-Einträge, die alle Abonnenten erhalten haben, wachsen unbegrenzt. Aufräum-Job vorsehen.
- Lebenszyklus: Der Container schließt die Datenbank-Engine (`Database.dispose`) noch nicht; mit dem Start und Stopp von Worker und API regeln.
- Fehlgeschlagene Zustellungen werden unbegrenzt wiederholt (`event_retries` zählt die Versuche); Obergrenze oder wachsender Abstand bei Bedarf.
- Mehrere Dispatcher mit demselben Abonnentennamen stellen ggf. doppelt zu (mindestens einmal ist erlaubt).
- SQLite hat einen Schreiber: Ein Task darf keine zweite schreibende Unit of Work öffnen, solange seine erste offen ist (wartet sonst bis zum Busy-Timeout).

**Fertig, wenn:** Ein PDF und ein Foto laufen durch, Archiv-PDF und Docling-Ausgabe liegen im Speicher, ein abgebrochener Schritt lässt sich einzeln wiederholen.

**Modell:** Opus 5.5 · high – Idempotenz, Prozesse und Fehlerpfade.

### M4 – Authentifizierung, Rechte, REST-API

**Ziel:** Mehrbenutzerbetrieb und vollständige API für Stammdaten und Schubladen.

- Nativer Login (Argon2id), TOTP optional, Session-Cookie, persönliche API-Tokens mit Stufen.
- OIDC über Authlib, Verknüpfung mit lokalem Konto.
- Endpunkte: Nutzer (Admin), Stammdaten (Admin), Schubladen und Freigaben, Dokumente; OpenAPI-Spezifikation sauber gepflegt.

**Fertig, wenn:** Rechte-Tests zeigen, dass kein Nutzer fremde Dokumente ohne Freigabe sieht; OpenAPI ist vollständig.

**Modell:** Opus 5.5 · high; danach Sicherheits-Review mit Fable 5.1 · high – sicherheitskritisch, unabhängige Prüfung lohnt.

**Review und Behebung:** Das unabhängige Sicherheits-Review (`reviews/M4-security.md`) fand keine kritischen, einen hohen, vier mittlere und sieben geringe Befunde. Behoben vor dem Merge: Quellen-Drossel sperrt nur noch falsche Anmeldungen, `PAPIQ_FORWARDED_ALLOW_IPS` ist mit `Secure`-Cookies Pflicht (M4-01); Fehlversuche werden atomar vor der Prüfung gezählt (M4-02); Admin-Endpunkte für Anmeldedaten nur per Session, kein Selbstbezug (M4-03); Request-Bodies begrenzt, `PAPIQ_REQUEST_MAX_SIZE` (M4-04); dazu M4-06 bis M4-11. M4-05 (Token-Widerruf bei Passwortwechsel als Default) bewusst nicht geändert, M4-12 akzeptiert. Auftrag: `prompts/M4-fixes.md`.

### M5 – KI-Klassifizierung, Konfidenz, Lanes

**Ziel:** Dokumente werden klassifiziert und landen in Grün, Gelb oder Rot.

- LLM- und Embedding-Adapter (OpenAI-kompatibel), strukturierte Ausgabe mit festem Schema.
- Klassifizierung (Kontakt, Typ, Tags), Feldextraktion je Dokumenttyp.
- Konfidenz aus prüfbaren Fakten (Abgleich, Liste, Wert im Text); neue Gegebenheiten → Gelb.
- Posteingang (API): gelbe und rote Dokumente nur für den Besitzer.
- Kleiner Bewertungssatz aus Beispieldokumenten, um Modelle vergleichen zu können.

**Vorher klären:** LLM und Embedding-Modell, Konfidenz-Schwellen, Anzahl Retries.

**Fertig, wenn:** Der Bewertungssatz läuft reproduzierbar; Lanes stimmen mit den Erwartungen überein.

**Modell:** Opus 5.5 · high – Prompt- und Schema-Design, Bewertung.

**Review und Behebung:** Das Review durch einen separaten Agenten fand keine Blocker. Behoben vor dem Merge: Ein Kontakt wird nur grün, wenn er selbst im Text steht (ähnliche Schreibweise → Vorschlag); Belege nur als ganze Wörter, nur ISO-4217-Codes als Währung, Ja/Nein- und Auswahlwerte brauchen eine Belegstelle von mindestens 4 Zeichen; Tags aus der Klassifizierung werden ergänzt statt ersetzt; beim Bestätigen gelten Werte, die das Dokument schon hat, als entschieden (Vorschläge füllen nur leere Felder); die Bewertung zählt „grün mit falschem Wert" und bricht dann mit Status 1 ab. Als Restrisiko akzeptiert (Tobi, 07.10.2026): siehe „Übernommen aus M5" unter M6.

### M6 – Suche

**Ziel:** Hybride Suche, gefiltert nach Rechten.

- Meilisearch-Adapter, Index-Aktualisierung per Ereignis, vollständiger Neuaufbau per Job.
- Jede Suche gefiltert auf sichtbare Schubladen.

**Übernommen aus M5** (offen bzw. für spätere Meilensteine festgehalten):

- Embedding-Port und OpenAI-kompatibler Adapter existieren (`PAPIQ_EMBEDDING_*`), werden aber noch nicht genutzt; die hybride Suche baut darauf auf.
- Echter Bewertungslauf (07.10.2026, Ergebnis in `architektur.md`): `qwen3:8b` mit 8192 Kontext, nur 5 der 28 Dokumente (Tobis Wahl). Offen: der volle Lauf (auf dem NUC etwa 1,5 bis 2,5 Stunden). Auf dem NUC liegt dafür die Modellvariante `qwen3:8b-ctx8k` (`num_ctx` 8192); für den Betrieb `OLLAMA_CONTEXT_LENGTH=8192` auf dem NUC setzen.
- Laufzeit 3 bis 10 Minuten pro Dokument auf der CPU des NUC: für die Migration (M12) mit vielen Dokumenten einplanen (Klassifizierung abschaltbar oder nachgelagert?).
- `bge-m3` ist auf dem NUC geladen, aber ungeprüft; die Wahl in M6 bestätigen.
- Restrisiko (akzeptiert): Eine Anweisung im Dokumenttext kann einen falschen, aber vorhandenen Kontakt grün machen, wenn der Text diesen Kontakt nennt. Tags aus der Klassifizierung werden ohne Textprüfung gesetzt. Für M7: Regeln dürfen Kontakt und Tags, die das Modell gesetzt hat, nicht allein vertrauen.
- Für M7: `inbox.field_checks(log)` liefert die Vorschläge und Prüfungen des letzten Modelllaufs je Feld; Bestätigungen stehen mit `model_version="person"` im Verarbeitungsprotokoll. Ohne Regeln setzt `confirm` nach dem Bestätigen bei `apply_rules` fort.
- Bestätigen ab `extract_fields` ersetzt die Felder, die der Besitzer eingetragen hat (dokumentiert, bewusst so).
- Feldprüfungen werden als bestanden protokolliert, auch wenn die Änderung danach an fehlenden Stammdaten scheitert (der Schritt wird dann unsicher).
- Fehlender Test: Klassifizierung auf Postgres im Integrationstest (bisher nur Speicher-Adapter und Rundlauf des Status `review`).

**Fertig, wenn:** Rechte-Tests für die Suche grün; Neuaufbau aus Datenbank und Speicher funktioniert.

**Modell:** Sonnet 5.5 · high – bekannte Integration, der Rechtefilter braucht Sorgfalt.

**Umsetzung und Review:** Index-Port `SearchIndex` mit Speicher- und Meilisearch-Adapter (httpx2, kein SDK), Vektoren über den Embedding-Port (`userProvided`), Abschnitte statt eines Vektors je Dokument, `GET /documents/search`, `POST /search/reindex`, Befehl `reindex`, `evaluate-search` mit 48 Anfragen. Das Review durch einen separaten Agenten fand keine Rechte-Lücke. Behoben vor dem Merge: Embedding-Ausfall hält ein Dokument nicht mehr aus dem Index; Umbenennungen während eines Neuaufbaus gehen nicht verloren; Abgleich bei jedem Start des Workers; Vektoren falscher Länge fallen auf Wörter zurück; Paging mit Bedeutung; ein Index ohne Einstellungen wird neu eingerichtet; `/health` meldet eine gestörte Suche als `degraded` (Tobi, 07.10.2026). Embedding-Modell: `snowflake-arctic-embed2` (Tobi, 07.10.2026, Bewertung in `architektur.md` unter „Suche“). Der Test der Klassifizierung auf SQL-Persistenz (Übergabe aus M5) liegt in `tests/integration/test_classification_evaluation.py`.

### M7 – Regel-Engine

**Ziel:** Globale und Nutzer-Regeln wie spezifiziert.

- Bedingungsbaum, Operatoren, Aktionen, Priorität, Konflikte → Gelb (auch Regel gegen LLM).
- Versionierung, Protokoll, Probelauf bei Dokumentänderungen.
- Rückwirkendes Anwenden: Ermittlung der betroffenen Dokumente und Ausführung als Job (Auswahl in der UI folgt in M11).

**Fertig, wenn:** Tests decken Konflikte, Schleifenschutz und Rechtegrenzen globaler Regeln ab.

**Übernommen aus M6** (offen bzw. für spätere Meilensteine festgehalten):

- Jede Dokumentänderung (auch durch Regeln oder rückwirkendes Anwenden) löst über das Ereignis `document.updated`/`lane_changed`/`filed` einen Index-Job aus; Regeln müssen nichts am Index tun. Ändert eine Regel viele Dokumente, entstehen viele Index-Jobs und Embeddings: auf dem NUC etwa 0,6 s je Abschnitt.
- Für M8 (MCP-Tool `search`): `SearchService.search(actor, text, filter, offset=, limit=, semantic_ratio=)` prüft Rechte selbst; der MCP-Server ruft ihn mit dem Besitzer des Tokens auf. Eine Seite kann weniger Treffer als `limit` enthalten.
- Die hybride Suche hat keine Schwelle: Meilisearch liefert auch bei Unsinn die nächsten Nachbarn. Eine Schwelle (`rankingScoreThreshold`) wäre eine eigene Entscheidung, am besten mit dem Bewertungssatz erweitert um Anfragen ohne Treffer.
- Der Neuaufbau belegt eine Worker-Schleife; seine Leihe (`PAPIQ_SEARCH_REBUILD_TIMEOUT`) wird nicht verlängert. Der Befehl `reindex` und ein laufender Neuaufbau-Job dürfen nicht gleichzeitig laufen (nur in der Dokumentation, nicht erzwungen).
- Ein Modellwechsel oder eine andere Vektorlänge braucht einen Neuaufbau; der Adapter bricht ab, wenn ein Index mit anderem Embedder Dokumente enthält.
- `PAPIQ_SEARCH_LOCALES` prüft nur das Format, nicht ob Meilisearch die Sprache kennt.
- Nicht in dieser Umgebung geprüft (läuft in der CI): der Rundlauf `test_search_end_to_end` mit Postgres und S3 und die Postgres-Variante von `test_classification_evaluation`.
- Für `evaluate-search` ist `PAPIQ_EMBEDDING_DIMENSIONS` irgendein Wert, weil die Konfiguration zuerst geprüft wird; Präfixe gelten für alle Modelle eines Laufs.

**Modell:** Opus 5.5 · high – viele Randfälle, Wechselwirkung mit Rechten.

**Review und Behebung:** Das Review durch einen separaten Agenten fand keine Blocker. Behoben vor dem Merge: Rückwirkend gilt Ablegen in eine geteilte Schublade auf unbestätigte Modellwerte als Konflikt (nur mit Annahme); eine beim Upload oder durch Verschieben gewählte Schublade und früher von der Person gesetzte Tags und Felder ändert keine Regel mehr (Protokoll `person:drawer`); Bestätigen beantwortet nur die gesehenen erzwungenen Prüfungen; Bestätigen verlangt eine Schublade, wenn die Ablage sonst wieder scheitert; ein Bearbeiter, dem eine Änderung die Sicht nimmt, erhält nur `id` und `access: null`; Regex-Suchen teilen sich ein Zeitbudget (2 s je Lauf, 20 s je Vorschau-Seite); rückwirkende Anwendungen enden bei einem unerwarteten Fehler; SQL lädt nur die aktuelle Regelversion.

### M8 – Webhooks und MCP-Server

**Ziel:** Externe Systeme informieren und Claude anbinden.

- Webhook-Abonnements pro Nutzer, schlanke Ereignisse, HMAC-SHA256, Retries, Zustellprotokoll.
- MCP-Server über HTTP mit API-Token: `search`, `get_document`, `get_text`, `update_metadata`, `list_tags`.

**Fertig, wenn:** Ein Test-Empfänger erhält signierte Ereignisse; ein lokaler MCP-Client kann suchen und Metadaten ändern.

**Übernommen aus M6 und M7** (offen bzw. für spätere Meilensteine festgehalten):

- Aus M6: Für das MCP-Tool `search` prüft `SearchService.search(actor, text, filter, offset=, limit=, semantic_ratio=)` die Rechte selbst; der MCP-Server ruft ihn mit dem Besitzer des Tokens auf. Eine Seite kann weniger Treffer als `limit` enthalten. Die übrigen Punkte aus „Übernommen aus M6" unter M7 (Schwelle der hybriden Suche, Neuaufbau, Modellwechsel, Locales, Postgres-Läufe in der CI) gelten weiter.
- MCP-Tool `update_metadata`: über `DocumentService.change_metadata(actor, id, changes)` gehen, nicht über `update_metadata`; nur so laufen die Änderungs-Regeln (Flanke), und das Ergebnis enthält den Regelbericht (`rules`, nur für den Besitzer) und `access` (None, wenn die Regeln das Dokument aus der Sicht des Aufrufers abgelegt haben: dann nichts weiter zurückgeben, wie REST).
- Webhooks: `document.filed` kommt auch, wenn eine Regel in `apply_rules` oder `confirm(drawer_id)` das Dokument verschiebt, solange es noch keine Lane hat (früher als die endgültige Ablage). Für Abonnenten zählt erst `document.filed` bei grüner Lane; ggf. im Ereignis die Lane mitgeben oder `move_to` vor der Ablage kein `filed` melden lassen.
- Webhooks: Regeländerungen und rückwirkende Anwendungen erzeugen keine eigenen Ereignisse; Fortschritt nur per `GET /rule-applications/{id}`.
- Offen aus M7: Kein In-Memory-Adapter für `PatternMatcher` (der Speicher-Container nutzt den `regex`-Adapter, der ohne Dienst läuft). `RuleApplication` schreibt `documents`/`skipped` je Dokument neu (quadratisch, bei `PAPIQ_RULES_APPLY_MAX_DOCUMENTS` nahe 100 000 relevant). Kleines Zeitfenster: Text und Muster werden vor der Transaktion für die dann gesehenen Regeln vorbereitet; eine dazwischen aktivierte Textregel wird ohne Text ausgewertet. Keine Obergrenze für Regeln je Nutzer (das Zeitbudget begrenzt die Kosten).
- Nicht in dieser Umgebung geprüft (läuft in der CI): Regel-Vertragstests und `test_rules_end_to_end` auf Postgres.

**Modell:** Sonnet 5.5 · high.

**Umsetzung und Review:** Webhooks (`/webhooks`, Zustellprotokoll, `POST /webhooks/{id}/test`): Abonnent `webhooks.fanout` und Job `webhooks.deliver` im Worker, Rechte beim Einreihen und vor jedem Versuch (sonst `dropped`), `document.deleted` an die Leser zum Zeitpunkt des Löschens (`DocumentDeleted.readers`, auch für SSE), Signatur nach Standard Webhooks (`webhook-id`, `-timestamp`, `-signature`, zwei Signaturen während der Übergangszeit nach dem Erneuern), Geheimnis AES-GCM-verschlüsselt, Wiederholung 10 Versuche ab 30 s verdoppelnd bis 1 h, Abschalten nach 20 aufgegebenen in Folge, Aufräumen des Protokolls über `PAPIQ_RETENTION`. MCP unter `/api/v1/mcp` mit dem offiziellen SDK (`mcp==2.3.0`), fünf Tools, Namen statt IDs, Bearer-Token mit Stufen. Admins haben alle Rechte an allen Webhooks: lesen, ändern, löschen, testen, Geheimnis erneuern (Tobi, 08.10.2026); ein von ihnen erneuertes Geheimnis sehen sie einmal. Das Review durch einen separaten Agenten fand keine Rechte-Lücke bei Ereignissen und MCP. Behoben vor dem Merge: Der HTTP-Client protokollierte die volle Ziel-URL (kann ein Zugangsdatum enthalten) – die Logger `httpx`, `httpx2` und `httpcore` zeigen nur noch Warnungen; `dropped` nennt für gelöschte und nicht mehr sichtbare Dokumente denselben Grund; Geheimnisse stehen nicht mehr in `repr`.

### M9 – Papiq-Image mit s6-overlay

**Ziel:** Ein betriebsfertiges Image.

- `runtime`-Ziel im Dockerfile: Debian slim, OCRmyPDF-Abhängigkeiten, Docling mit PyTorch (CPU).
- s6-Dienste: `init-papiq`, `init-migrations`, `svc-api`, `svc-worker`; `PAPIQ_ROLE`, `PUID`/`PGID`, Healthchecks.
- Beispiel-Compose für SQLite + Dateisystem und für Postgres + Garage.
- CI baut Images für amd64 und arm64.

**Vorher klären:** Backup-Strategie.

**Übernommen aus M8:**

- Der Worker braucht jetzt `PAPIQ_SECRET_KEY` (Webhook-Geheimnisse); die Einstellungen verlangen ihn für jede Rolle, auch für `check` und `migrate`. Das Image muss ihn (oder `PAPIQ_SECRET_KEY_FILE`) allen s6-Diensten geben.
- Neue Variablen: `PAPIQ_WEBHOOKS_PER_USER`, `PAPIQ_WEBHOOK_SECRET_GRACE`, `PAPIQ_WEBHOOK_TIMEOUT`, `PAPIQ_WEBHOOK_MAX_ATTEMPTS`, `PAPIQ_WEBHOOK_RETRY_DELAY`, `PAPIQ_WEBHOOK_DISABLE_AFTER`, `PAPIQ_WEBHOOK_CONCURRENCY`, `PAPIQ_MCP_ENABLED`, `PAPIQ_MCP_TEXT_MAX`; in die Beispiel-Compose-Dateien und die Doku des Images aufnehmen.
- MCP liegt im API-Prozess unter `/api/v1/mcp` (Streamable HTTP, zustandslos). Ein Reverse Proxy darf es nicht puffern oder auf eine Antwortzeit begrenzen; Anfragen sind einfache `POST`s.
- Die Worker-Rolle stellt Webhooks zu und braucht Netzzugang zu den Zielen (auch im eigenen Netz, das ist entschieden). Weiterleitungen werden nie verfolgt, Proxy-Umgebungsvariablen nie ausgewertet.
- Ein toter Empfänger belegt eine Zustellschleife bis zum Zeitlimit je Versuch (`PAPIQ_WEBHOOK_CONCURRENCY`); die Pipeline bleibt unberührt.
- Offen (M8): `POST /webhooks/{id}/test` ohne Ratenbegrenzung (ein Token mit Schreibstufe kann interne Adressen anstoßen, akzeptiert); `Webhook.previous_secret` bleibt nach Ablauf der Übergangszeit verschlüsselt in der Datenbank bis zum nächsten Erneuern; Admins sehen in Zustellprotokollen Dokument-IDs und die Ziel-URL fremder Webhooks (entschieden, so gewollt).
- Nicht in dieser Umgebung geprüft (läuft in der CI): die Vertragstests für Webhooks auf Postgres, die Migration `v0006` auf Postgres, ein echter MCP-Client (Claude Code, Inspector) gegen einen laufenden API-Prozess.

**Fertig, wenn:** Beide Beispiel-Stacks starten, migrieren und verarbeiten ein Dokument.

**Modell:** Sonnet 5.5 · high.

**Umsetzung und Review:** Stufen `deps` (gesperrte Abhängigkeiten ohne Dev-Gruppe), `app`, `s6` (s6-overlay 3.2.3.2, SHA-256 je Architektur) und `runtime`; `docling-models` baut auf `deps` (ein `uv sync` für `dev` und `runtime`). Dienste `init-papiq`, `init-migrations`, `svc-api`, `svc-worker` unter `deploy/image/rootfs`; die Rolle (`PAPIQ_ROLE`) prüft jedes Dienstskript (`s6-svc -Od .`, s6-rc ist statisch); der Worker wartet in `svc-worker` mit dem neuen, schreibfreien Befehl `check-schema --wait 600` auf das Schema (nicht in `init-migrations`: ein Oneshot ließe sich mit `docker stop` nicht unterbrechen; Review-Befund). Papiq-Prozesse laufen über `papiq-run` als `PUID`:`PGID` (Default 1000, `0` abgelehnt, kein passwd-Eintrag); `/data` ist das Volume (Pfade per Image-`ENV`, `settings.py` unverändert); `init-papiq` lehnt relative Pfade, `/` und Systemordner ab. Herunterfahren: Worker 30 s, `S6_SERVICES_GRACETIME` 40 s, `S6_KILL_GRACETIME` 1 s (s6 wartet sie immer ganz ab), Compose `stop_grace_period` 60 s. Healthcheck: API über `/api/v1/health`, Worker über `s6-svstat` (mindestens 10 s oben) und eine Marke, dass das Warten auf das Schema vorbei ist. Hilfsbefehl `papiq` (`docker exec … papiq reindex`). Beispiel-Stacks `deploy/compose.sqlite.yml` und `compose.postgres.yml` mit Secrets als Dateien (`create-secrets.sh`), `deploy/test-image.sh` (31 Prüfungen, auch in der CI; seit M13 über 40), `deploy/README.md` mit Betrieb, Reverse Proxy, Webhooks, MCP, Backup und Wiederherstellung. Größe laut `docker image inspect` in der CI: 3,19 GB (amd64), 3,02 GB (arm64); OrbStack auf dem Mac zeigt 4,57 GB.

Echtläufe auf Tobis Mac (Ollama am NUC, `qwen3:8b-ctx8k` und `snowflake-arctic-embed2`): beide Stacks, je zwei PDFs (gerenderte Stromrechnung und Handwerkerrechnung aus dem Bewertungssatz) bis Lane Gelb in 66 bis 96 s (Kontakt und Typ unbekannt, Stammdaten leer), Suche semantisch; MCP gegen den laufenden Container mit dem MCP-SDK-Client (alle fünf Tools, `update_metadata` eingeschlossen) und mit Claude Code (`claude -p` mit `--mcp-config`); Backup und Wiederherstellung mit beiden Stacks durchgespielt (Dokument, Original, Archiv und Suche nach `reindex` wieder da). CI-Zeiten des Image-Jobs (Pull Request, je Architektur nativ): kalt 4:05 bis 4:17 (Bau 2:27 bis 2:28, Test 75 bis 83 s); mit GHA-Layer-Cache nicht schneller (Wiederherstellen rund 1 min, Schreiben aus `main` weitere 2 min), deshalb ohne Cache und nur im Pull Request. Pull Requests ohne Image-Pfad bleiben unberührt (Job `changes` 4 bis 5 s, `backend` 1:00, `integration` 2:47). Das Review durch einen separaten Agenten fand keine Lücke bei Geheimnissen (alle Beispielgeheimnisse in Logs und `docker inspect` gesucht) und Rechten (nur `s6-supervise` läuft als root). Behoben vor dem Merge: das Warten des Workers (siehe oben), Pfadprüfung in `init-papiq`, Angaben der Backup-Doku (Originale unveränderlich, Ableitungen nicht; Wiederherstellung ersetzt Objekte; `alpine` gepinnt), Hinweis, Geheimnisse nur als `_FILE` zu übergeben.

### M10 – Web-UI-Fundament

**Ziel:** Cleanes, modernes Grundgerüst, auf dem alle Bildschirme aufbauen.

- SvelteKit (statisch), Tailwind, shadcn-svelte; TypeScript-Client aus OpenAPI generiert.
- Gestaltungsrichtung, Farben, Typografie, Dark Mode; Login inkl. TOTP; Navigation.

**Fertig, wenn:** Login funktioniert, Design-System steht, Uvicorn liefert die gebaute UI aus.

**Modell:** Opus 5.5 · high – Gestaltungsentscheidungen prägen die ganze UI.

**Übernommen aus M9:**

- Die UI kommt ins Image: eine Node-Stufe im `Dockerfile` baut die statischen Dateien, die Stufe `runtime` kopiert sie; `.dockerignore` lässt `web/` durch (heute nur `backend/pyproject.toml`, `uv.lock`, `README.md`, `src/`, `deploy/image/`). Wie Uvicorn sie ausliefert (Pfad, Einstellung `PAPIQ_…`, Vorrang der API-Pfade unter `/api/v1`), entscheidet M10; `deploy/README.md` und der Reverse-Proxy-Abschnitt (Cache-Header, Pfade) nachziehen.
- CI: Der Job `image` löst bei `web/` heute nicht aus (Pfade in `changes`: `Dockerfile`, `.dockerignore`, `deploy/` ohne Markdown, `backend/pyproject.toml`, `backend/uv.lock`, `ci.yml`). Sobald die UI im Image steckt, `web/` (ohne Tests und Markdown) und die Lock-Datei der UI aufnehmen und `deploy/test-image.sh` um einen Abruf der Startseite erweitern.
- Das Betriebsbild für die UI-Entwicklung bleibt der Devcontainer; das Image wird nur gebaut, nicht für Vite genutzt.
- Offen aus M8 (unverändert weitergetragen): `POST /webhooks/{id}/test` ohne Ratenbegrenzung, `Webhook.previous_secret` bleibt nach der Übergangszeit verschlüsselt liegen, Admins sehen Dokument-IDs und Ziel-URL fremder Webhooks (entschieden); dazu die Punkte aus „Übernommen aus M6 und M7" (Schwelle der hybriden Suche, Neuaufbau-Leihe, Modellwechsel, `PAPIQ_SEARCH_LOCALES`, `PatternMatcher` ohne In-Memory-Adapter, quadratisches Schreiben in `RuleApplication`, Zeitfenster bei Textregeln, keine Obergrenze für Regeln je Nutzer).

**Umsetzung und Review:** SvelteKit 3.0.1 als Single-Page-App (`adapter-static`, Fallback `index.html`, kein SSR) unter `/ui`, Svelte 5, Vite 8, TypeScript 6, Tailwind 4, shadcn-svelte (bits-ui), Paraglide JS 2 (Englisch als Basis, Deutsch; Wahl im Browser gespeichert, Formate nach Browser-Region, sonst de-DE/en-US), Schriften Geist und Geist Mono lokal, alles exakt gepinnt (`pnpm-lock.yaml`, pnpm 12.9.1, Node 24.21.0). Gestaltung Richtung B „Papier" (Tobi, 08.10.2026; Artifact mit drei Richtungen): Petrol `#0d6b6b` als einziger Akzent, Papierweiß, runde Formen, Kopfnavigation mit Pillen, Lanes „Erledigt/Prüfen/Eingreifen/Läuft" immer mit Symbol und Wort. Client: `openapi-typescript` aus dem eingecheckten `web/openapi.json` (Befehl `python -m papiq.composition.openapi`, ein Backend-Test vergleicht) und `openapi-fetch` mit eigenem `fetch` (Cookie der Seite, `X-CSRF-Token` nur bei Änderungen, ein Wiederholversuch bei veraltetem Token, 401 außerhalb der Anmeldung → Login mit `next`). Anmeldung mit Passwort, TOTP, Wiederherstellungscode und OIDC-Knopf (nur wenn eingerichtet); `next` nur auf Seiten unter `/ui/`. Auslieferung: `PAPIQ_UI_DIR` (im Image `/opt/papiq/ui`; gesetzt ohne `index.html` ist ein Konfigurationsfehler), `adapters/inbound/rest/ui.py` nach allen API-Routen, `/` → 302 `/ui/`, `/ui` → 308; Dateien des Builds, sonst `index.html` für Seitenpfade ohne Endung, 404-Problem für fehlende Dateien, alles unter `_app/`, versteckte Namen, Pfade außerhalb; nur GET/HEAD. `_app/immutable` ein Jahr `immutable`, alles andere `no-cache` mit ETag/304. CSP per Meta-Tag aus SvelteKit (Hash-Modus), als Header `frame-ancestors 'none'`, `nosniff`, `Referrer-Policy: same-origin`. Image: Stufe `web` auf der Bauplattform, `runtime` ohne Node. CI: Job `web` (ESLint, Prettier, svelte-check, Vitest, Client-Abgleich, Build), nur bei `web/`; `image` auch bei `web/` ohne Tests. Tests: 172 Vitest (u. a. kein fester Text im Markup, gleiche Schlüssel und Platzhalter in beiden Sprachen, `next`-Prüfung), 30 Backend-Tests der Auslieferung, Playwright-Rauchtest nur lokal (gelaufen: Anmeldung mit TOTP, Navigation, Neuladen, Abmelden, Rücksprung nach Sitzungsende). Das Review durch einen separaten Agenten fand keinen offenen Weg aus `next` heraus und keinen Pfadausbruch. Behoben vor dem Merge: die CSRF-Wiederholung lief nach einer Anmeldung in einem anderen Tab als die andere Person (jetzt nur bei gleicher Nutzer-ID, sonst neu laden); API-Änderungen ohne UI-Bezug lösten den Image-Job aus (`openapi.json`, `schema.ts` ausgenommen); Tastaturfokus zu blass (jetzt 2-px-Umriss in Petrol mit Abstand, 6:1); Fehler beim Laden einer Seite zeigten Englisch „Internal Error“ ohne das Detail der API (`hooks.client.ts`; dabei gefunden: `openapi-fetch` liest den Fehlerkörper selbst, `apiError` nimmt ihn jetzt entgegen); doppelte Schrägstriche umgingen die Regeln für `_app/`; 405 hatte den Titel „405“; Fokus und `aria-describedby` auf der Anmeldeseite; CSRF-Kopf nur an die eigene Seite.

### M11 – Web-UI-Funktionen

**Ziel:** Papiq vollständig über die Oberfläche nutzbar.

- Dokumentliste und -detail mit PDF.js, Upload, Fortschritt per SSE.
- Posteingang mit Lanes, Bestätigen und Korrigieren.
- Schubladen und Freigaben, Stammdaten (Admin), Webhooks, API-Tokens, Einstellungen.
- Regel-Baukasten mit Probelauf und Auswahldialog für rückwirkendes Anwenden.

**Übernommen aus M10:**

- Gerüst: `web/README.md`. Neue Seiten liegen unter `src/routes/(app)/…` (Layout prüft die Sitzung), Bereiche in `src/lib/sections.ts`; die Platzhalterseiten (`Placeholder.svelte`) werden ersetzt. Bausteine aus shadcn-svelte per CLI (`pnpm dlx shadcn-svelte@1.7.0 add …`, `components.json`), danach Texte über Paraglide; die Bausteinseite `/ui/dev/components` (nur `pnpm dev`) zeigt den Stand.
- Importe heißen `#lib/…` mit Dateiendung (SvelteKit 3 hat `$lib` entfernt; `imports` in `package.json`), `$app/env` statt `$app/environment`.
- Jeder sichtbare Text in `messages/{en,de}.json`; die Tests schlagen bei festem Text im Markup (auch `aria-label`, `title`, `placeholder`, `alt`) und bei fehlenden Schlüsseln an. Fehlertexte der API (Problem-Details) bleiben, wie die API sie schreibt.
- API-Aufrufe nur über `api` aus `#lib/api/client.ts`; Fehler mit `describeError`/`reportError` (`#lib/errors.ts`), 429 nennt die Wartezeit. Nach einer API-Änderung `web/openapi.json` neu schreiben und `pnpm gen:api`.
- Formate über `formatDate`, `formatNumber`, `formatMoney` (`#lib/i18n.ts`); Lanes über `LaneBadge`.
- CSP: `img-src` erlaubt `data:` und `blob:` (PDF-Vorschau), `connect-src 'self'` (SSE unter `/api/v1/events` passt). PDF.js braucht vermutlich einen Worker (`worker-src`) und darf kein CDN nutzen; die Direktiven stehen in `web/vite.config.ts`.
- Neue Abhängigkeiten nur nach Absprache und exakt gepinnt; pnpm 12 lehnt Pakete jünger als einen Tag ab (`minimumReleaseAge`).
- Nicht gebaut: Superforms, Zod, TanStack (E6); bei Formularen mit vielen Feldern neu entscheiden.
- Beobachtet: Bricht die OIDC-Anmeldung beim Anbieter ab, zeigt `/api/v1/auth/oidc/callback` ein Problem-JSON statt einer Seite der UI.
- Seitenpfade dürfen im letzten Abschnitt keinen Punkt enthalten (`/ui/users/john.doe`): die Auslieferung hält ihn für eine Datei und antwortet 404. IDs (UUID) sind unkritisch; Namen nicht als Pfadparameter.
- CSP: `style-src 'unsafe-inline'` ist weiter als nötig; laut Review erzeugt das Bündel keine `<style>`-Elemente, `style-src 'self'; style-src-attr 'unsafe-inline'` könnte reichen (im Browser prüfen, auch mit PDF.js).
- Playwright läuft nur lokal (`pnpm e2e`, `web/README.md`), die CI installiert keine Browser.

**Fertig, wenn:** Alle Abläufe aus dem Architektur-Dokument sind in der UI erreichbar.

**Modell:** Sonnet 5.5 · medium für Bildschirme nach Vorlage; Regel-Baukasten mit Opus 5.5 · high.

**Umsetzung und Review (M11a, ohne Regelbereich):** Tobis Entscheidungen (08.10.2026): Regeln komplett M11b (der Probelauf für Dokumente gehört zu M11a); Formulare ohne Superforms, Zod, TanStack; QR-Code für TOTP mit `uqr` 0.1.3 als SVG; OIDC-Fehler leitet auf `/ui/login?error=<code>` (Codes `denied`, `failed`, `conflict`, `provider`; bei verknüpfendem Aufruf mit Sitzung auf `/ui/settings?error=<code>`); Dokumentliste ohne Sortierung, „Mehr laden“; Suche ohne Gewichtungsregler; keine neue Design-Runde. Neue Abhängigkeiten: `pdfjs-dist` 6.4.299 (Legacy-Build, weil der normale `Map.getOrInsertComputed` braucht), `uqr` 0.1.3. Umgesetzt: Dokumentliste mit Filtern und Ereignisstrom (eine `EventSource`, Neuladen nach Wiederverbindung), Upload (3 parallel, nur Status „lädt hoch“, dann SSE), Detail mit schlankem PDF-Viewer, Bearbeiten mit Probelauf vor dem Speichern, Verarbeitungsprotokoll, Wiederholen, Neuverarbeiten, Verschieben, Löschen; Posteingang mit Zähler und Bestätigen/Korrigieren; Suche; Schubladen mit Freigaben; Stammdaten (Kontakte, Dokumenttypen, Schlagwörter, Felder); Benutzerverwaltung; Webhooks mit Zustellprotokoll; Einstellungen (Passwort, TOTP mit QR und Wiederherstellungscodes, OIDC verknüpfen, andere Sitzungen beenden, API-Tokens). Einzige API-Änderung: der OIDC-Callback (mit Test, `openapi.json` und Client neu erzeugt). CSP: `style-src 'self'` plus `style-src-attr 'unsafe-inline'` und `worker-src 'self'`, im Browser mit PDF.js ohne Verstöße; `wasm-unsafe-eval` war nicht nötig. Tests: 259 Vitest, 1973 Backend-Unit-Tests, Playwright lokal (`areas.spec.ts` öffnet alle Bereiche ohne Konsolenfehler und legt Kontakt, Schublade, Webhook und Token an und wieder weg). Das Review durch einen separaten Agenten fand und behoben vor dem Merge: `loading` blieb nach `refresh()` während `more()` für immer gesetzt (Test ergänzt); Webhook-Test und Geheimnis erneuern sowie das Verarbeitungsprotokoll wurden auch gezeigt, wo die API „nur Eigentümer“ sagt; das Formular wurde bei jedem Ereignis zurückgesetzt und löschte Eingaben (jetzt nur, wenn nichts geändert ist); Admins bekamen für Upload und Prüfen Schubladen angeboten, in die sie nicht schreiben dürfen; späte Antworten eines früheren Dokuments konnten das aktuelle überschreiben; Uploads blieben nach Abmelden stehen; Neustart des Ereignisstroms nach Abmeldung; zwei Layouts des PDF-Viewers konnten sich überholen; Namensabruf der Webhook-Seite ohne Fehlerbehandlung.

**Übergaben an M11b:**

- Die Seite `/ui/rules` ist weiter ein Platzhalter (`Placeholder.svelte`, Eintrag in `src/lib/sections.ts`). Wiederverwendbar: `PagedList` (`paging.svelte.ts`), `NativeSelect`, `ConfirmDialog`, `NameDialog`, `FieldValueInput`, `ReviewFieldInput`, `loadLookup` (Kontakte, Typen, Schlagwörter, Felder, Schubladen, Benutzer; `masterdata.svelte.ts`), `permissions.ts`, `events.svelte.ts` (`generation` nach Wiederverbindung), `fieldErrors` (422-Details je Feld) und der Probelauf-Dialog des `DocumentEditor` als Vorlage für die Vorschau von Regeln.
- Texte: `web/messages/{en,de}.json`, Hilfsskript ist nicht eingecheckt; Schlüssel in beiden Sprachen, sonst schlägt der Test an.
- Rechte zeigt die UI nur als Komfort, die API entscheidet. Laut Endpunktbeschreibung sind „Test senden“ und „Geheimnis erneuern“ eines Webhooks sowie das Verarbeitungsprotokoll eines Dokuments nur für den Eigentümer, auch für Admins; `GET /drawers` liefert Admins nur eigene und freigegebene Schubladen. Das passt nicht zu „Admins haben alle Rechte und sehen alles“: bei Bedarf in der API ändern und die UI nachziehen (`permissions.ts`, `webhooks/+page.svelte`).
- Nur mit abgefangenen API-Antworten geprüft (lokal gibt es weder Meilisearch noch ein Modell): die gelbe Prüfansicht mit Vorschlägen, Suchtreffer, die rote Spur. Beim ersten Lauf mit echten Diensten ansehen.
- Offen: `img-src` erlaubt weiter `blob:`; wahrscheinlich überflüssig, weil kein Code Blob-URLs erzeugt (mit einem PDF mit JBIG2/JPX prüfen, dann streichen). Kein Byte-Fortschritt beim Upload (nur Status). Für Währung gibt es keinen Feldtyp, die API speichert sie je Wert.
- Unverändert weitergetragen: die Punkte aus „Übernommen aus M10“ (offen aus M8, M6 und M7, M9).

**Umsetzung und Review (M11b):** Tobis Entscheidungen (08.10.2026): E1 Admins haben alle Rechte und sehen alles; E2 Probelauf vor dem Einschalten (Regel wird ausgeschaltet gespeichert); E3 Konflikte nie vorausgewählt, eigenes Häkchen (`accept_conflicts`); E4 Versionen nur ansehen; E5 kein JSON-Editor; F1a SSE für Admins zu allen Dokumenten, Webhooks nur Reichweite des Besitzers; F2a Schublade einer Nutzer-Regel gegen deren Besitzer; F3a Listen zeigen die Reichweite, Admins schalten mit `all_users` um; F4a Admins legen in jede Schublade ab, auch beim Bestätigen; F5a neue Regel per POST, dann PATCH `enabled=false`. Umgesetzt: Rechte in `permissions.py` (`can_control_document`, Reichweite für Listen und Webhooks), Ports `DocumentRepository.query` und `Visibility.everything` mit Vertragstests, `all_users` an Dokumentliste, Posteingang und Suche, Admins sehen und verwalten alle Schubladen, Regeln und Webhooks; `architektur.md` angepasst. Fehler beim Speichern einer Regel nennen den Ort. UI: Umschalter „Alle Nutzer“, Besitzer an fremden Dokumenten, fremde Schubladen; Regelliste, Baukasten (verschachtelte Gruppen bis Tiefe 5, alle Felder, Operatoren und Aktionen, Platzhalter, Prüfung vor dem Speichern, API-Fehler am Block), Ansicht, Versionen, Probelauf mit Auswahl, Fortschritt und Ergebnis. Keine neue Abhängigkeit, CSP unverändert. Tests: 300 Vitest (u. a. Hin- und Rückweg jeder Definition), 2049 Backend-Unit-Tests, Playwright lokal grün (`rules.spec.ts`: bauen, Probelauf, anwenden, ändern, Version 1 lesen, unverändert speichern ohne neue Version, abgelehnter Schalter, löschen; `areas.spec.ts`). Das Review durch einen separaten Agenten fand keine Lücke bei Rechten, Suche, SSE und Webhooks; behoben vor dem Merge: Bestätigen durch Admins prüfte gegen den Besitzer (gegen F4a; jetzt jede Schublade, die Ablage akzeptiert die Wahl des Admins); Regelseiten luden Regel und Probelauf doppelt und konnten Eingaben verwerfen; ein abgelehnter Schalter blieb umgelegt; die Prüfung im Browser lehnte Datums- und Zahlschreibweisen, lange Texte und doppelte Listenwerte ab, die die API annimmt; Fehlerzuordnung bei 404 für Dokumenttypen, bei Pydantic-Unions und bei „; “ im Text; `?all=1` galt auch für Nicht-Admins; späte Antworten der Regelliste; stilles Scheitern beim Wiedereinschalten.

### M12 – Migration aus Paperless-ngx

**Ziel:** Bestehendes Paperless-Archiv übernehmen, nur über die Papiq-API.

- CLI liest die Paperless-REST-API, schreibt über die Papiq-API.
- Abbildung von Korrespondenten, Typen, Tags, Custom Fields, Besitzern; Speicherpfade und Berechtigungen auf Schubladen.
- Probelauf mit Bericht, dann Übernahme; wiederaufnehmbar.

**Vorher klären:** Abbildung von Speicherpfaden und Berechtigungen.

**Fertig, wenn:** Eine Paperless-Testinstanz ist vollständig übernommen, der Bericht zeigt keine Verluste.

**Übernommen aus M11:**

- Admin-Rechte (M11b, `architektur.md`): Admins lesen und schreiben jedes Dokument, legen in jede Schublade ab und verwalten jede Schublade, jeden Webhook und jede Regel. Listen, Posteingang und Suche zeigen weiter nur die eigene Reichweite; `all_users=true` (nur Admins) zeigt alle. Für die Migration: ein Admin-Token kann in jede Schublade hochladen, aber `POST /documents` kennt keinen Besitzer, Besitzer wird der Hochladende. Besitzer aus Paperless brauchen also ein Token je Besitzer oder eine API-Erweiterung (in M12 klären).
- Regeln eines Nutzers prüfen die Schublade einer Aktion gegen den Besitzer der Regel, nicht gegen den Aufrufer (F2a).
- Bestätigt ein Admin in eine Schublade, in die der Besitzer nicht schreiben darf, steht seine Wahl im Protokoll (`drawer_id` im Eintrag `person`, ebenso in `person:drawer` bei Upload und Verschieben); die Ablage akzeptiert sie, solange der Admin aktiv ist. Ältere Protokolleinträge nennen keine Schublade.
- Fehler beim Speichern einer Regel nennen den Ort (`conditions.all[1].any[0]: …`, `actions[2]: …`); Fehler der Anfrageprüfung kommen weiter in Pydantics Form (`conditions.AllGroup.all.0.ConditionSchema.op: …`, je versuchter Art ein Fehler; die UI behält nur die passende).
- Offen: Gründe für Konflikte und Hinweise im Probelauf und bei Übersprungenen kommen von der API englisch und mit rohen IDs (etwa eines Kontakts); die UI zeigt sie, wie sie kommen. Die Regelliste ist nicht live (kein Ereignis für Regeln). Zwischen Anlegen und Ausschalten einer neuen Regel liegt ein kurzes Fenster, in dem sie eingeschaltet ist (F5a, entschieden). Die Suche über alle Nutzer (`Visibility.everything`) ist nur in der CI gegen Meilisearch geprüft. Der Baustein `checkbox` des Kits nutzt noch `data-checked`-Varianten (bits-ui setzt `data-state`), wird aber nirgends verwendet.
- Unverändert weitergetragen:
  - Aus M11a: `img-src blob:` wahrscheinlich überflüssig (mit JBIG2/JPX-PDF prüfen); kein Byte-Fortschritt beim Upload; gelbe Prüfansicht, Suchtreffer und rote Spur nur mit abgefangenen Antworten geprüft.
  - Aus M8: `POST /webhooks/{id}/test` ohne Ratenbegrenzung, `Webhook.previous_secret` bleibt nach der Übergangszeit verschlüsselt liegen.
  - Aus M6 und M7: Schwelle der hybriden Suche, Neuaufbau-Leihe, Modellwechsel, `PAPIQ_SEARCH_LOCALES`, `PatternMatcher` ohne In-Memory-Adapter, quadratisches Schreiben in `RuleApplication`, Zeitfenster bei Textregeln, keine Obergrenze für Regeln je Nutzer.
  - Aus M9 für M13: siehe Abschnitt M13 im Umsetzungsplan.

**Umsetzung und Review (M12):** Tobis Entscheidungen (08.10.2026, Prompt `M12.md`) und die Antworten auf die Fragen im Plan: `owner_id` an `POST /drawers`, `POST /users` mit Admin-Token (nur Rolle „Nutzer“, ohne Passwort), `sha256` in den Dokumentdetails, Ablauftests gegen die echte API im Backend. Umgesetzt: `POST /documents` mit `owner` und `metadata` (nur Admins, `metadata` nur mit `channel=migration`; die Metadaten stehen im Protokolleintrag `receive`, eine Hülle um Klassifizieren und Felder (`core/services/imports.py`) wendet sie ohne LLM an, kein Schema und kein Port geändert); CLI `migration/` (`papiq_migration`, nur `httpx2`, SQLite-Zustand) mit `plan`, `run`, `verify`, Berichten als Markdown und JSON; CI-Job `migration`. Tests: 79 für die CLI gegen Attrappen beider APIs, 17 gegen die echte API im Backend (Abbruch und Wiederaufnahme, zweiter Lauf ohne Doppeltes, Rechte je Erweiterung), 2067 Backend-Unit-Tests, 300 Vitest. **Echtlauf gegen die Testinstanz** (Paperless 3.3.0, 1547 Dokumente, Papiq im Devcontainer mit Postgres, Dateisystem-Objektspeicher, OCR und Docling auf CPU, Worker 4, kein LLM): Probelauf 1,4 s; 50 Dokumente 626 s; Abbruch per Strg-C (Exit 130) und Wiederaufnahme bis 100 Dokumente ohne Doppeltes; Gesamtlauf 4 h 27 min für 1311 Uploads (etwa 12 s je Dokument, Pipeline je Dokument von Upload bis fertig: Median 36 s, 90. Perzentil 111 s); `verify` 1,4 s, mit `--rehash` 17 s. Ergebnis: 1525 Dokumente übernommen (1518 grün, 3 gelb wegen PDF/A, 4 rot), `verify` ohne Abweichung bei diesen; 1 Dokument ausgelassen (E-Mail `.eml`, Papiq nimmt nur PDF, JPEG, PNG, TIFF); **21 Dokumente nicht übernommen, weil die Testinstanz die Datei nicht liefert** (HTTP 404 für Original und Archiv, `original_size` null; die Titel enthalten Umlaute, vermutlich ein Dateinamenproblem der Testinstanz). Fehler, die der Echtlauf in Papiq fand: Upload mit Dateiname jenseits von Latin-1 (Gedankenstrich, Euro) antwortete 500 (behoben, Test); fünf Dokumente rot, weil Docling bei vier parallelen Workern auf 8 GB vom Betriebssystem beendet wurde (Exit -9; einzeln neu verarbeitet, grün). Das Review durch einen separaten Agenten fand keine Lücke bei Rechten und Schlüsseln, aber Randfälle, die vor dem Merge behoben wurden: inaktive Paperless-Nutzer werden erst nach den Dokumenten deaktiviert (sonst 404 für Besitzer und Freigaben); eine Freigabe an den ausführenden Admin (der Besitzer ist) brach den Lauf ab; ein Fehler bei Nutzern, Stammdaten oder Schubladen beendete den Lauf ohne Bericht; ein verlorenes Ergebnis nach dem Anlegen eines Nutzers gab 409; ein Upload nach Neustart wurde erneut gesendet statt seine Pipeline abzuwarten; Dubletten bekamen keine Lane; zu große Werte ließen das Dokument scheitern; Papierkorb, Mailkonten, weitere Dateiversionen, „hinzugefügt“ und Verlauf fehlten im Bericht; `verify` prüfte die Standardschublade nicht.

**Modell:** Sonnet 5.5 · high.

### M13 – Härtung und Release 0.1

**Ziel:** Papiq mit den ersten 100 Dokumenten produktiv nutzen.

- Ende-zu-Ende-Lauf mit 100 Dokumenten; Auswertung der Lanes.
- Sicherheits- und Architektur-Review des Gesamtsystems.
- Dokumentation: Installation, Konfiguration (alle `PAPIQ_`-Variablen), Backup.

**Vorher klären:** Verfügbarkeit des Namens.

**Übernommen aus M9:**

- Veröffentlichen des Images (Entscheidung 3 in M9): Tag, Registry, Mehrarchitektur-Manifest (amd64 und arm64 werden in der CI auf getrennten Runnern gebaut), danach die Beispiel-Compose-Dateien auf das veröffentlichte Image umstellen (heute `build:` mit `papiq:local`). Der Image-Job nutzt keinen Layer-Cache (gemessen, siehe oben); für einen Release-Build ist das nur dann zu ändern, wenn die Bauzeit stört.
- Backup: M9 hat nur Doku (`deploy/README.md`). Ein `backup`-Befehl wäre Sache von M13; der Punkt „Backup-Strategie" in `architektur.md` (Offene Punkte) ist noch nicht abgehakt (Tobis Entscheidung).
- Vollständige Variablenliste und Installationsanleitung: `deploy/README.md` nennt nur die Betriebsvariablen, `backend/README.md` die Liste.
- Beobachtet: Uvicorn schreibt jeden Healthcheck-Aufruf (alle 30 s) als INFO in die Logs; den Pfad `/api/v1/health` aus dem Zugriffslog nehmen. Der Worker-Healthcheck erkennt keinen hängenden Worker, nur einen beendeten oder neu startenden (Herzschlag-Datei wäre ein Eingriff in den Worker).
- Nicht geprüft: ein Update von Image zu Image mit getrennten API- und Worker-Containern.

**Übernommen aus M12:**

- Migration von Tobis Archiv (M13): `migration/README.md` beschreibt Aufruf und Berichte. Ablauf wie in M12: erst `plan` (liest nur), dann `run`, dann `verify`; Papiq-Token eines Admins mit Stufe `read_write` (aus der UI unter Einstellungen, oder per Sitzung `POST /auth/tokens`), Paperless-Schlüssel nur aus Umgebung oder Datei. Der Admin muss in Papiq denselben Benutzernamen haben wie der Besitzer der Dokumente in Paperless (Zuordnung über den Benutzernamen), sonst legt die CLI einen zweiten Nutzer an und die Dokumente gehören diesem. Fehlende Nutzer entstehen ohne Passwort und Rolle „Nutzer“; Admin-Rechte und Anmeldung (OIDC oder Passwort) vergibt Tobi. Die CLI liegt nicht im Image; ob sie ins Release kommt, entscheidet M13.
- Dauer: etwa 12 s je Dokument (OCR und Docling auf der CPU, Worker 4, 8 GB RAM, kein LLM); 1500 Dokumente brauchen rund 4,5 Stunden. Mit 8 Workern auf 8 GB wurde es langsamer (Auslagerung), vier Docling-Prozesse wurden vom Betriebssystem beendet. Für 8 GB `PAPIQ_WORKER_CONCURRENCY` höchstens 4, besser 3. Ein hängender oder abgestürzter Lauf lässt sich fortsetzen (`run` erneut), auch nach einem Neustart von API und Worker.
- Fehlerbilder aus dem Echtlauf, die Papiq betreffen: (1) OCR schlägt bei einzelnen PDFs fehl (`ocrmypdf` Exit 1: „color space“ bei einem Dokument; „invalidating the signature“ bei zwei signierten PDFs): rot, die Metadaten aus Paperless werden erst nach einem erfolgreichen Wiederholen angewendet. Zu klären: Rückfall im OCR-Schritt (z. B. ohne PDF/A, Signatur verwerfen) und ob übernommene Metadaten schon vor OCR/Parsen gelten sollen. (2) Drei Dokumente gelb („das Archiv konnte nicht als PDF/A erzeugt werden“). (3) Ein Dokument rot, weil Docling keinen Text findet (leere Seiten). (4) Docling wird bei zu viel Parallelität beendet (Exit -9; die Wiederholung einzeln gelingt): Speicherbedarf je Worker dokumentieren oder begrenzen. (5) Beträge liefert die API in E-Schreibweise (`5.335E+7` für 53350000); die UI formatiert sie, die API sollte feste Schreibweise ausgeben (`field_json` in `schemas.py`).
- Offen aus dem Echtlauf: 21 Dokumente der Testinstanz liefern ihre Datei nicht (404 für Original und Archiv bei `original_size` null); an Tobis Archiv prüfen, ob das auch dort vorkommt (Titel mit Umlauten). Die CLI meldet sie als „failed“ mit dem Grund und holt sie bei einem weiteren `run` nach, sobald Paperless die Datei liefert. Der Embedding-Dienst (Ollama) lief nicht: die Suche arbeitet mit Wörtern; Embeddings entstehen beim ersten Neuaufbau (`reindex`) oder bei der nächsten Dokumentänderung (Fenster höchstens 40 Minuten, ab 23 Uhr CET Ruhe, danach `keep_alive=0`).
- Verhalten der Migration: Dokumente ohne zusätzliche Rechte landen in der Standardschublade ihres Besitzers (Regeln können sie dort wieder ablegen, `verify` meldet das dann als Abweichung der Schublade). Eine Neuverarbeitung ab „Klassifizieren“ wendet Titel, Kontakt, Typ, Datum und Tags aus Paperless erneut an (nicht das Modell) und überschreibt spätere Änderungen von Hand in diesen Feldern. `verify` vergleicht den SHA-256 in Papiq mit der Prüfsumme, die Paperless 3.x je Version führt (`versions[].checksum`); `--rehash` lädt die Originale neu. Ein Papiq-Neuaufbau ohne den Zustand der CLI zu löschen führt zu fälschlich „fertigen“ Dokumenten (README).
- Weitergetragen (nicht Teil von M12, unverändert): die Punkte aus M11b, M11a, M8, M6 und M7 unter „Übernommen aus M11“ in M12.

**Fertig, wenn:** Review-Befunde sind behoben, Release 0.1 ist getaggt.

**Modell:** Fable 5.1 · high – lange, verifikationslastige Prüfung über das ganze System.

**Umsetzung und Review (M13):** Tobis Entscheidungen (09.10.2026, `prompts/M13.md` und Plan-Freigabe): Testinstanz als Quelle, Admin `test`, OCR-Rückfall auch bei Exit 2 mit Signaturhinweis, `OcrResult.note`, Variablen-Doku in `backend/README.md`, Devinstanz-Doku in `.devcontainer/README.md`, Stoppen per VS-Code-Befehl, Bucket-Kopie mit `rclone`, Übernahme ohne Embeddings mit anschließendem `reindex`, rotes Dokument ohne Text als eigener Thread nach 0.1 (siehe unten), Webhook-Test 10 je Nutzer und Minute, Versionstest, alte PRs rückwirkend gelabelt; signierte PDFs grün mit Hinweis, Docling-Textschicht als Rückfall gelb (09.10., 07:06); die vier Einzeiler des Reviews mitgenommen (07:54).

Vier Phasen, je ein Pull Request, jeder vor der nächsten Phase gemergt:

- **Phase A (PR #28):** OCRmyPDF läuft bei signierten PDFs ein zweites Mal mit `--invalidate-digital-signatures` (Archiv PDF/A, Original unverändert, grün mit Hinweis im Protokoll) und bei Exit 1 (Farbraum) ein zweites Mal als einfaches PDF (gelb mit Grund); bei Exit 10 steht Ghostscripts Grund im Protokoll (`-v1`): Overprint-Modus und CID-0-Schriften der Quelldateien, nicht behebbar. Findet Doclings Layout-Modell keinen Text, obwohl OCR welchen fand (die Gehaltsabrechnung aus M12), wird die Textschicht des Archivs als Markdown genommen und der Schritt gelb. Beträge und Zahlen in fester Schreibweise (`format(…, "f")`), Healthcheck aus dem Zugriffslog, Webhook-Test gedrosselt (429 mit `Retry-After`), `previous_secret` nach der Übergangszeit gelöscht, Test für rote Dokumente mit übernommenen Metadaten. Speicher gemessen: Docling 1,1 GB (eine Seite) bis 2,0 GB (zehn Seiten) je Prozess, OCRmyPDF 150 MB; Tabelle `PAPIQ_WORKER_CONCURRENCY` je RAM in `backend/README.md` (8 GB: 3), Default bleibt 2.
- **Phase B (PR #29):** fünf Review-Agenten parallel (`.idea/reviews/M13-review.md`): 1 hoch, 5 mittel, 40 niedrig. Behoben: Admin-Token konnte Konten zum Admin machen und Admins deaktivieren (Rollenwechsel und Admin-Zustand nur mit Sitzung, `bootstrap_admin` prüft aktive Admins); `init-papiq` nahm `/data/../etc` an (`.`/`..`, `/tmp`, `/var` abgelehnt, Prüfung in `test-image.sh`); UI: Ereignisstrom und Uploads überlebten keine Navigation, `generation` nach vom Server geschlossenem Strom, späte Antworten in Prüfansicht, Protokoll und Such-Trefferzahl. Zwei Architektur-Befunde (MCP nutzt REST-Schemas; Regelmuster `re` gegen `regex`) nach Prüfung niedrig. Doku-Korrekturen in `architektur.md` (Kontakt/Typ optional, Worker indexiert, Ports-Tabelle vollständig, Name abgehakt), READMEs.
- **Phase C (PR #30):** `.vscode/tasks.json` (Starten, Stoppen über `workbench.action.tasks.terminate`, API, Worker, UI dev, Schema migrieren, UI bauen, Suche neu aufbauen, Migration plan/run/verify), `.devcontainer/live.env.example`, `live-setup.sh` (Schlüssel, Datenbank `papiq_live`), `garage-init` mit `PAPIQ_S3_EXTRA_BUCKETS=papiq-live`, Index `papiq-live-documents`; Tests bleiben getrennt (`papiq_test_*`, Präfix `tests/`, `papiq-test-*`). `.devcontainer/README.md` (Einrichten, Tasks, Update nach `git pull`, Ollama-Regeln, Speicher, Migration, Backup mit `pg_dump` über `docker exec` und `rclone/rclone:1.75.1`). `backend/README.md`: eine Zeile je Variable mit Rolle (API, Worker, beide), Test gegen `Settings.model_fields`. Version 0.1.0 in Backend, CLI und UI mit Test. Release Drafter `v7.10.0` und `micnncim/action-label-syncer@v1.3.0` mit expliziten `permissions` (der Standard-Token des Repos darf nur lesen), Label-Satz aus pypaperless plus `dev-deps`, Abschnitt „Labels“ in `CLAUDE.md`, neues `README.md`. `img-src data: blob:` gestrichen (im Browser mit JPX-PDF und TOTP-QR geprüft). Die vier Einzeiler: `no-store`/`nosniff` auf allen API-Antworten außer Health und API-Beschreibung, OIDC-Flow-Cookie nach abgelehntem Login, Webhook-Test mit unlesbarem Geheimnis als `gave_up`, Token-Name 100 Zeichen. Die Tasks wurden in einem frisch gebauten Dev-Container neben dem laufenden durchgespielt (Einrichten, Migrieren, UI bauen, Start, Stopp, Neustart, Reindex, `check-schema`); ein Update mit neuer Alembic-Revision gab es in M13 nicht zu testen. `PAPIQ_MIGRATION_*` lösen keine Warnung „unbekannte Variable“ mehr aus. CI je Pull Request: changes 0:06, backend 0:50, migration 0:15, web 1:07–1:44, integration 2:16–2:50, devcontainer 2:49–3:04, image 4:05–5:14 (nur bei Image-Pfaden).
- **Phase D (PR #31):** Devinstanz allein nach `.devcontainer/README.md` aufgesetzt (zwei Lücken dabei behoben: `garage-init` für den neuen Bucket in einem bestehenden Container, Host-Pfade beim Backup). **Dokumentlauf** (`.idea/reviews/M13-lanes.md`): zehn Originale, 1 grün, 9 gelb, 0 rot; Dokumenttyp 8 von 10, Datum 5 von 10, Kontakt 2 von 10 (Vorschläge meist richtig, aber voller Firmenname statt Kurzname; bei Gehalts- und Zeitnachweisen der Empfänger); Felder in der Testinstanz nicht messbar; Ollama über `ollama.tbsch.me` fiel dreimal mit 502 aus, der Lauf wurde auf Tobis Wunsch mit Groq (`openai/gpt-oss-120b`, `json_schema`, 8000 Token/min, `PAPIQ_LLM_INPUT_BUDGET=6000`) beendet: 2 bis 4 s je Anfrage. **Übernahme** der Testinstanz (1547 Dokumente, Paperless 3.3.0) in die Devinstanz mit `plan`, `run` (vier Worker, ohne Embeddings, 13:32 bis 18:57 Uhr mit einer Unterbrechung, etwa 12 s je Dokument) und `verify` (2 s): 1525 übernommen, 1519 grün, 5 gelb (3× kein PDF/A, 1× Farbraum als einfaches PDF, 1× Text aus der OCR-Schicht), 1 rot (Docling Exit -9 bei vier parallelen Uploads und drei Workern; einzeln wiederholt grün); 1 übersprungen (.eml); 21 nicht übernommen, weil die Testinstanz die Datei nicht liefert (HTTP 404, dieselben wie in M12), `verify` nennt genau diese 21 als Abweichung „not migrated (failed)“. Ein Index, der mit Embeddings angelegt war und dann ohne Embeddings beschrieben wurde, lehnt jedes Dokument ab (`vector_embedding_error`, Embedder `userProvided`): für die Übernahme ohne Embeddings den Index vorher löschen oder gar nicht erst mit Embeddings anlegen; der `reindex` danach richtet ihn neu ein. **Backup und Wiederherstellung** nach der Doku durchgespielt (pg_dump, rclone, Bucket geleert und zurückgespielt, `reindex`): `pg_dump` 2,2 MB, Bucket 7630 Objekte / 2,1 GB per `rclone sync` (etwa eine Minute), Datenbank per `dropdb`/`createdb`/`pg_restore` ersetzt, Bucket geleert und aus der Kopie zurückgespielt (wieder 7630 Objekte, gleiche Größe); danach Start und Neuaufbau des Index. Der `reindex` mit Embeddings über `ollama.tbsch.me` lief mit rund 10 s je Dokument (250 Dokumente in 40 Minuten) statt der in M6 gemessenen 0,7 s und wurde nach Tobis 40-Minuten-Regel abgebrochen; die Devinstanz läuft bis zur Klärung ohne Embeddings (Wortsuche), der Index wird vom Abgleich beim Start neu gefüllt. Ein Wechsel zwischen „mit“ und „ohne“ Embeddings verlangt das Löschen des Index (Embedder `userProvided`), sonst `vector_embedding_error`. **UI mit echten Diensten** angesehen: gelbe Prüfansicht mit Vorschlag, Zitat, Konfidenz und Datumswarnung; Suchtreffer mit Hervorhebung und Trefferzahl; rote Spur mit Hinweis, Wiederholen und Protokoll (pdf.js meldet „Invalid PDF structure“ englisch, Befund 5.6). Beobachtung: alle 14 Felder sind nach der Übernahme global und erscheinen an jedem Dokument.

**Release-Entwurf:** Release Drafter 7 legt ohne eine veröffentlichte Vorgängerversion einen Entwurf ohne Änderungsliste an („No changes“, Warnhinweis im Entwurf) und löst die Version als `v0.0.1` auf; die Labels wirken erst ab der zweiten Version. Für 0.1.0 wird der Entwurf deshalb einmal von Hand gesetzt (Tag `v0.1.0`, Notizen aus den gelabelten Pull Requests #1–#31); danach arbeitet der Drafter normal. Die Konfiguration aus pypaperless nutzt die veralteten `version-resolver`-Schlüssel (Warnung im Lauf; funktioniert in 7.10.0).

**Nach 0.1 (Vorschläge; was kommt, entscheidet Tobi):**

- Aus dem Review (`M13-review.md`, alle niedrig): 1-02 OIDC-Verknüpfung nur mit Passwort; 1-03 `channel=migration` nur für Admins; 1-05 Sperrliste oder knappere Rückmeldung für den Webhook-Test; 1-06 Obergrenze für Ereignisströme je Nutzer; 1-08 Kommentar und README zur Konto-Drossel; 2-01 Warnung ohne Meilisearch-Schlüssel und toleranter Abgleich; 2-02 `https` für OIDC-Discovery-Endpunkte; 2-03 LLM-Zeitlimit je Anfrage; 3.4 Setuid-Programme aus dem Image; 3.5 `VOLUME /data`; 3.6 Lücken in `test-image.sh`; 3.7/3.8 Actions und Basis-Images per SHA/Digest, `curl | bash` in der Dev-Stufe; 3.10 `.lock` 0666; A-01 Vertrag für Inbound-Adapter untereinander; A-02 Musterprüfung über den `PatternMatcher`-Port; A-03 Vertrag Outbound→Outbound; A-04 In-Memory-`PatternMatcher`; A-05 Indizes für Listenfilter und Aufräumabfragen; A-06 Test der Fehlerzuordnung; A-09 Meta-Test der Vertragstests; 5.4 Upload-Abbruch, Größenprüfung, Duplikat-Link, `document.deleted`; 5.5 Pluralformen; 5.6 Textkleinigkeiten; 5.9 Zugänglichkeit; 5.10 Rechte-Komfort (Webhook-Knöpfe, Schublade für andere, deaktivierte Nutzer in Freigaben); 5.11 Posteingangs-Reiter und `all_users` in der URL; 5.12 PDF-Viewer bei nachgelieferter Archivdatei; 5.13 `minimumReleaseAge`, `pnpm audit`.
- Rotes Dokument ohne Text (Entscheidung 10): ein Dokument ohne Textschicht geht rot; der Besitzer kann es heute per Bestätigen von Hand ablegen (`Document.confirm` nimmt Rot). Vorschlag für einen eigenen Thread: Hinweis in der UI („kein Text erkannt, von Hand ablegen“), Bildvorschau statt leerem Viewer, ggf. Handschrift-OCR als späterer Adapter.
- Idee: Docling als eigener Dienst statt im Image (Tobi, 09.10.2026). Heute festgelegt in `architektur.md` („ein Papiq-Image“, „Docling ist im Image enthalten“); eine Begründung gegen einen eigenen Container ist nicht dokumentiert. Dafür: Image ohne PyTorch und Modelle deutlich kleiner, Worker leicht, Speicher von Docling getrennt begrenzbar (Exit -9 aus M12/M13 träfe nicht den Worker), Modelle einmal statt je Dokument geladen (Zeitgewinn nicht gemessen), Docling auf anderem Rechner möglich (z. B. NUC). Dagegen: zweiter Dienst, Abbruch bei Zeitlimit und Freigabe des Speichers nicht mehr durch den Prozess je Dokument (`docling/parser.py`), Netzwerk und Versionsabstimmung. Umsetzung ohne Kernänderung: neuer HTTP-Adapter hinter `DocumentParser` (z. B. gegen `docling-serve`), Eintrag in `PARSERS` (`composition/container.py`), Dienst in den Compose-Stacks; verlangt eine Änderung von `architektur.md`.
- Weitergetragen aus M6–M11b (unverändert, keins vom Review als Risiko eingestuft): Schwelle der hybriden Suche, Neuaufbau-Leihe, Modellwechsel braucht Neuaufbau, `PAPIQ_SEARCH_LOCALES` nur formal geprüft, quadratisches Schreiben in `RuleApplication`, Zeitfenster bei Textregeln, keine Obergrenze für Regeln je Nutzer, englische Konfliktgründe mit rohen IDs, Regelliste nicht live, Fenster beim Anlegen einer Regel, Suche über alle Nutzer nur in der CI geprüft, Baustein `checkbox`, kein Byte-Fortschritt beim Upload, Worker-Healthcheck erkennt keinen hängenden Worker, Update von Image zu Image mit getrennten Containern nicht geprüft.
- Veröffentlichen des Images (Registry, Mehrarchitektur-Manifest, Beispiel-Compose auf das veröffentlichte Image), Migrations-CLI im Release (heute nur im Repo), Release-Drafter-Konfiguration auf die neuen `semver-increment`-Schlüssel umstellen, `backup`-Befehl (Entscheidung 7: nur Doku), ein Update-Test mit neuer Alembic-Revision über die Tasks.
- Idee: DocLang als Strukturformat (Tobi, 09.10.2026). DocLang ist kein Parser oder Dienst, sondern eine offene Spezifikation für ein KI-taugliches Dokumentformat (eingeschränktes XML mit Struktur und Seitenlayout, Beispiel `*.dclg.xml`), seit 06/2026 Arbeitsgruppe bei LF AI & Data (IBM, NVIDIA, Red Hat, ABBYY, HumanSignal), baut auf Docling auf; Docling soll es ausgeben können. Spezifikation noch in Arbeit. Bezug zu Papiq: `DocumentParser` legt neben dem Markdown das verlustfreie Docling-JSON ab (`docling/convert.py`, `export_to_dict()`); intern liest es niemand, Klassifizierung und Suche nutzen das Markdown. DocLang könnte dieses Strukturformat ersetzen oder ergänzen, damit Fremdwerkzeuge es lesen. Kein Ersatz für Docling und ohne Einfluss auf die Idee „Docling als eigener Dienst“. Erst sinnvoll, wenn die Spezifikation stabil ist. Quellen: https://doclang.ai/, https://lfaidata.foundation/press-release/2026/06/09/lf-ai-data-foundation-launches-doclang-specification-working-group-to-advance-an-open-standard-for-ai-native-documents/
- Idee: Live-Verarbeitungsstand beim Upload (Tobi, 10.10.2026): Fortschrittsbalken und Anzeige „wartet auf Verarbeitung“. Heute vorhanden: SSE `GET /events` (`document.received`, `document.node_completed`, `document.lane_changed`, `document.filed`), vom UI abonniert (`web/src/lib/events.svelte.ts`); `GET /documents/{id}` mit `processing.status`, `current_node`, `outcomes`; sieben feste Schritte. Das Upload-Fenster (`web/src/lib/upload.svelte.ts`) zeigt nur „wartet“ (Browser-Warteschlange), „lädt hoch“, „wird verarbeitet“ und dann die Lane; Schritt-Events nutzt es nicht. Teil 1, nur UI: Balken nach Schritten (n/7) aus `step_completed` und Dokumentzustand; Upload-Fortschritt in Bytes per `XMLHttpRequest` (`fetch` meldet keinen Upload-Fortschritt), keine neue Abhängigkeit. Teil 2, Backend: „wartet“ und „läuft“ sind heute beide `status=processing` mit `current_node`; ob der Job in der Warteschlange liegt oder ein Worker ihn hält, steht nur in der Job-Queue. Nötig wären ein Feld am Dokument (z. B. `waiting`/`running`, ggf. Position in der Warteschlange) und ein Event „Schritt gestartet“; das ändert den Port `JobQueue` (Abfrage nach Dokument) samt Vertragstests, also nur nach Absprache. Grenzen: kein Fortschritt innerhalb eines Schritts (z. B. OCR-Seiten), der Balken springt; SSE ohne Wiederholung und für eine API-Instanz (nach Reconnect lädt das UI den Stand neu, `recheck()`). WebSocket oder Polling sind nicht nötig.

**Unscharfe Zuordnung (nach 0.1, Tobi 10.10.2026):** Plan und Entscheidungen in `/mnt/project-files/plan/unscharfe-zuordnung-plan.md`. Aliase je Kontakt (Tabelle `contact_aliases`, Revision 0008), automatisch gelernt beim Bestätigen in der Prüfansicht (`learned_alias` im Protokoll), Beschreibung je Dokumenttyp, Vorauswahl von 20 Kontakten im Prompt (`classify-2`, `contacts_listed` im Protokoll). Offen: Aliase für Tags (vorerst nein), Paperless-Match-Regeln bei der Migration (vorerst nein), ähnliche bestätigte Dokumente über die Suche als zweites Signal, Wirkung im Echtlauf messen (M13: Kontakt 2 von 10 richtig). Aus dem Review offen (niedrig): `PATCH /contacts` hat keine Versionsprüfung; speichert ein Admin den Dialog, während ein Alias gelernt wird, geht der gelernte verloren.

### M14 – Ingest-Graph

Zielbild und Entscheidungen: `ingest-v2.md` (Tobi, 12.10.2026). Die sieben Schritte werden Knoten eines Graphen, ohne neue Logik; das Verhalten bleibt, der Bewertungssatz muss dieselben Lanes liefern.

- Kern: `Node` mit Kanten statt `Step` in fester Reihenfolge; `Processing` mit Zustand je Knoten (wartet, läuft, ok, unsicher, fehlgeschlagen, Dauer); „ab Knoten X neu“ setzt X und die Nachfolger zurück; Lanes unverändert; Protokoll je Knoten.
- Jobs: ein Job je Knoten, Knoten ohne gegenseitige Abhängigkeit parallel; `pages` und `archive` laufen nebeneinander, `read` wartet auf beide. Leihe, Wiederholung und Idempotenz wie heute.
- Ereignisse `document.node_started` und `document.node_completed`; `GET /documents/{id}` mit dem Zustand je Knoten; die Idee „Live-Verarbeitungsstand beim Upload“ geht hier auf.
- Web-UI: Graph mit Zustand je Knoten in der Dokumentansicht, Text sofort nach `read`, Hochladefenster nach Knoten. Jedes Zwischenergebnis sichtbar, sobald es vorliegt (Tobi, 12.10.2026).
- Migration des Zustands bestehender Dokumente (Schritt → Knoten) per Alembic-Revision.

**Fertig, wenn:** Bewertungssatz und Echtlauf liefern dieselben Lanes wie 0.1; die UI zeigt den Graphen live; Neuverarbeitung ab jedem Knoten funktioniert.

**Modell:** Opus 5.5 · high.

**Umsetzung (Branch `feature/smart-pipeline-0.2.0`, Tobis Entscheidungen vom 12.10.2026):** Die Schritte heißen in Code, API, Ereignissen, Datenbank und UI-Schlüsseln `Node`/`node` (Revision 0009 benennt Spalten, Job-Art, Nutzlast-Schlüssel und Ereignistyp um); die Texte für Nutzer sagen weiter „Schritt“. Graph `GRAPH` in `core/domain/pipeline.py`: `receive` → `ocr` und `pages` nebeneinander, `ocr` → `parse` → `classify` → `extract_fields` → `apply_rules` → `file`; `file` wartet auch auf `pages`. `Processing` hält je Knoten `status` (`waiting`, `running`, `done`, `skipped`), `outcome`, Start und Ende; `current_node` und `outcomes` entfallen (Revision 0010 übernimmt die Zeiten aus dem Protokoll, `pages` ist für Bestandsdokumente übersprungen; Spalten werden in place gelöscht, nie durch Neuanlegen der Tabelle, weil SQLite bei Fremdschlüsseln sonst Protokoll, Tags und Felder mitlöscht). Scheitert ein Knoten, enden laufende Zweige, nichts Neues startet; `retry` wiederholt alle gescheiterten Knoten, Neuverarbeitung ab X setzt X und seine Nachfolger zurück, Bestätigen übernimmt, was nicht erneut läuft, und lässt nie gelaufene, nicht benötigte Knoten (die Seiten nach gescheiterter OCR) nachlaufen. Der Worker markiert den Knoten beim Beanspruchen als laufend (eigene Transaktion, Ereignis `document.node_started`) und reiht nach einem Ergebnis alle neu fälligen Knoten ein; zwei Knoten eines Dokuments gleichzeitig fängt die optimistische Sperre mit drei Schreibversuchen. `pages`: alle Seiten als WebP 1200 px breit (Bilder behalten kleinere Größen, jede TIFF-Seite ist eine Seite) plus die 400-px-Vorschau, die vorher die OCR aus dem Archiv zog; Port `PageRenderer` statt `PreviewRenderer`, PDFium für PDF, Pillow für Bilder; `page_count` am Dokument (Revision 0011). Bestandsdokumente: Befehl `render-pages` reiht je Dokument einen Nachhol-Job ein (`pipeline.catch_up`), der den übersprungenen Knoten außerhalb der Verarbeitung ausführt und weder Status noch Lane ändert (kein erneutes `document.filed`, kein Rot für Seitenbilder). API: `processing` ist `{status, run, nodes[]}`, neu `GET /pipeline`, `GET /documents/{id}/pages/{n}`, `GET /documents/{id}/text` (wie das MCP-Tool `get_text`), Ereignis `document.node_started`. Web-UI: Graph als Zeilen je Ebene mit Zustand und Dauer (laufende Uhr), Text sobald `parse` fertig ist, Hochladefenster mit Fortschritt „Schritt n von N“ und laufendem Knoten, Protokoll nach Knoten gruppiert; die Neuverarbeitung folgt dem Graphen aus `GET /pipeline`. Der Migrationsclient liest `nodes`. Nicht geprüft in dieser Sitzung (keine Dienste im Container): Integrationstests auf Postgres, Garage und Docling; der Bewertungssatz lief nicht. Das Review durch einen separaten Agenten (12.10.2026) fand keine Rechte-Lücke an `/pipeline`, `/pages/{n}` und `/text` und keine Verletzung der Architekturregeln; behoben vor dem PR: `processing.nodes` kam auf Postgres in JSONB-Schlüsselordnung statt Graphordnung; späte Antworten der UI konnten einen neueren Zustand überschreiben; ein gescheiterter Nachhol-Lauf der Seiten blockierte jede Neuverarbeitung (der Knoten bleibt jetzt übersprungen); Bestätigen bei noch laufendem Knoten hätte dessen Ergebnis über die Entscheidung geschrieben (jetzt 409); ein Knoten, dessen Versuch eine Ausnahme warf, erschien in der Wartezeit als laufend (neues Ereignis `document.node_deferred`); `deploy/test-image.sh` las noch `outcomes`; die TOTP-Zeitschritte und der Anmelde-Assistent waren mit umbenannt; umbenannte Variablen `PAPIQ_STEP_*` werden mit dem neuen Namen abgewiesen statt stillschweigend ignoriert; der Downgrade von 0010 verlor laufende Knoten. Offen gelassen: `catch_up()` lädt alle Dokumente in den Speicher (bei größerem Bestand eine streamende Abfrage).

### M15 – Verstehen

- Kennungen je Kontakt (IBAN, USt-IdNr, Kundennummer, E-Mail-Domain) als Stammdaten mit API und UI; Signal `facts` mit Namen, Aliasen und Kennungen.
- Port `Classifier` mit In-Memory-Adapter und Vertragstest; Adapter auf scikit-learn (neue Abhängigkeit, Entscheidung 2); Job `train` nach N Bestätigungen, Modell versioniert im Objektspeicher; Trainingsbeispiele sind von Personen gesetzte oder bestätigte Metadaten, die Übernahme eingeschlossen (Entscheidung 3).
- Signal `neighbours` über ähnliche Dokumente im Suchindex (`SearchIndex` um die Vektorsuche erweitert, Meilisearch `/similar`).
- Signal `llm`: ein Aufruf mit Kontakt samt Rolle, Typ, Tags, Datum, Titel, Sprache, Zitaten; Felder des Typs gleich mit, wenn der Typ sicher ist.
- `judge` mit der Grün-Regel aus `ingest-v2.md`; Kandidaten mit Quellen und Begründung in Protokoll und Prüfansicht.
- Felder nur, wenn der Typ sie führt (Entscheidung 5); Normalisierung deutscher Zahlen und Daten; Titelmuster je Dokumenttyp (Entscheidung 7); keine Schublade aus dem Klassifikator (Entscheidung 6).
- Bericht: Lanes je Feld, Trefferquote gegen Paperless, Rückkopplungsmaß (automatisch grün, später korrigiert).

**Fertig, wenn:** Der Echtlauf mit den zehn Originalen aus M13 kommt mit trainiertem Klassifikator über neun von zehn Gelb hinaus, ohne ein falsches Grün; der Bewertungssatz läuft mit und ohne Modell.

**Modell:** Opus 5.5 · high.

**Umsetzung (Branch `feature/understanding-0.2.0`, Tobis Entscheidungen vom 13.10.2026):** Lesarten 3, 4 und 7 bestätigt; Lesart 5 ersetzt: Felder sind je Zuordnung zu einem Typ Pflicht oder optional, globale mit eigenem Schalter, nach der Übernahme alle optional (Revision 0013); Pflicht muss gefunden und geprüft sein, ein optionales Feld darf leer bleiben, ein genannter Wert muss die Prüfung bestehen. Tags machen nie gelb. Kennungen von Hand und beim Training gewonnen (in mindestens zwei bestätigten Dokumenten, alle eines Kontakts); eine verworfene bleibt gesperrt, eine gelernte kann von Hand übernommen werden (Revision 0012). Ein Klassifikator für alle Nutzer. Die Knoten heißen `judge` und `fields`, davor `signal_llm`, `signal_classifier`, `signal_neighbours`, `signal_facts` (Revision 0016 benennt in Zustand, Protokoll, Jobs und Outbox um und gibt Bestandsdokumenten übersprungene, laufenden wartende Signale); die Dateien der Modellanfragen heißen `signal_llm.json` und `fields.json`, die alten `classify.json` und `extract_fields.json` gelten beim Lesen weiter. `confirmed_at` am Dokument (Revision 0014, nachgefüllt aus dem Protokoll) macht ein Dokument zum Trainingsbeispiel: Bestätigen, Änderung von Kontakt, Typ, Tags, Datum oder Feldern durch eine Person, die Übernahme; ändert die Verarbeitung Kontakt, Typ oder Tags, fällt es weg. Klassifikator: TF-IDF (Wörter und Wortpaare, `sublinear_tf`, `min_df` 2, `max_df` 0,5) und logistische Regression mit `class_weight=balanced`, Ablage als `npz` ohne Pickle, Vorhersage mit numpy (ein Paritätstest gegen `predict_proba`); Tabelle `classifier_models` (Revision 0015), Prüfjob alle zehn Minuten, `train`, `GET /classifier`, `POST /classifier/train`, Verwaltungsseite. Nachbarn: `SearchIndex.similar` (Meilisearch `/search` mit `vector` und `semanticRatio` 1, gefiltert auf `confirmed` und die Sichtbarkeit des Besitzers), jeder Treffer in der Datenbank nachgeprüft; nach dem Update nimmt der Abgleich beim Start des Workers die Dokumente neu auf (`v0014` erhöht die Version der bestätigten) und behält die Vektoren unveränderten Texts, ohne `reindex`. Ein Modellaufruf je Dokument (Prompt `propose-1`); der Felder-Knoten fragt nur nach Feldern außerhalb dessen, wonach das Modell gefragt hat (nach einer Typkorrektur, Prompt `extract-2`). Beim Bauen entschieden: Ein Signal, das einen anderen Wert nennt, widerspricht immer, auch einem Wert, den es nicht kennt (sonst bliebe der Lohnnachweis grün, dessen Angestellten das Modell für den Absender hält); „abdecken“ entscheidet nur, ob das Modell allein reicht, und die Nachbarn decken nur Werte ab, die ein bestätigtes Dokument trägt (`DocumentRepository.confirmed_labels`), nicht schon, sobald einer gefunden wurde. Ohne Vorschlag des Modells ist das Urteil unsicher statt rot. Titel: Muster des Typs, sonst der des Modells, solange der Titel der Dateiname oder der der Maschine ist; Bestätigen rendert das Muster mit den entschiedenen Werten neu; eine Regel ersetzt den Titel der Maschine ohne Gelb. Prüfansicht mit gereihten Kandidaten und Quellen-Chips (Klick übernimmt), Quellen grüner Werte hinter einem Info-Symbol, Zusammenfassung je Signal im Protokoll, Pflichtfelder markiert. Rückkopplungsmaß: `corrected` im Protokolleintrag jeder Korrektur, auch beim Bestätigen im Posteingang. Bewertungssatz: Trainingskorpus mit 55 Briefen von elf Kontakten im Stil ihrer Fälle, zwei Läufe (kalt und trainiert) mit einer simulierten Person, Bericht nebeneinander; neue Fälle 29–33 (Lohnnachweis mit Angestellter, Kontoauszug mit fremden Kontakten, Absender nur über Kennungen, `2.111,68`, Unsinn im optionalen Feld). Mit Fake-Antworten: kein falsches Grün in beiden Läufen; trainiert werden neue Kontakte und Typen, die der kleine Korpus nicht hat, gelb, weil Nachbarn oder Klassifikator einen anderen Wert nennen (die Nachbarn haben noch keine Mindestähnlichkeit; das gehört zur Kalibrierung in M18). Das Review durch einen separaten Agenten (13.10.2026) fand keine Rechte-Lücke an `/classifier`, an den Kennungen und an den Kandidaten in `/review` und keine Verletzung der Architekturregeln; behoben vor dem PR: Klassen eines Modells, das vor dem Löschen eines Kontakts oder Typs trainiert wurde (zwei zusammengeführt), ließen das Urteil scheitern (rot) oder widersprachen dem richtigen Wert; die Nachbarn deckten Werte aus den bestätigten Dokumenten aller Besitzer ab statt aus denen, die der Besitzer lesen darf; eine Neuverarbeitung ab `judge` las die alten Fakten, Vorhersagen und Nachbarn (jetzt laufen die Signale ohne Modellaufruf mit, `CURRENT_SIGNALS`), sodass ein neuer Alias oder eine neue Kennung nicht wirkte; ein Versionskonflikt beim Speichern des Trainings galt als verlorener Job und blockierte ihn für die Leihe, zwei Trainings zugleich konnten dieselbe Nummer nehmen (jetzt unter Sperre, mit Wiederholung); ohne Sprachmodell war der Felder-Knoten auch bei nur optionalen Feldern unsicher; die Signale übernommener Dokumente enthielten sich nicht; ein Absender, der zwei Kontakten gleich ähnlich ist, machte mit dem Klassifikator einen davon grün; der Titel des Modells kam mit Zeilenumbrüchen; das Zitat hinter einem Quellen-Chip war nur per Maus erreichbar; zwei API-Beschreibungen nannten noch `extract_fields`. Offen gelassen: Bestätigte Dokumente ohne Text zählen für die Schwellen des Trainings mit, bei sehr kleinem Bestand scheitert der Trainingsjob dann alle zehn Minuten mit „zu wenige“; Klassifikator und Nachbarn schreiben je Lauf ihre bekannten Klassen ins Protokoll (bei Hunderten Kontakten einige zehn KB je Dokument). Echtlauf (14.10.2026, `reviews/M15-lanes.md`, Kopie der Devinstanz mit 1516 bestätigten Dokumenten, Groq `qwen/qwen3.8-27b`): mit Klassifikator ohne Nachbarn 3 grün, 7 gelb; mit Klassifikator und Nachbarn 1 grün, 9 gelb (M13: 1 grün, 9 gelb); in keinem Durchgang ein falsches Grün (die vier grünen Datumsabweichungen sind Ausstellungsdaten gegen Paperless' Zeitraumsende oder Zuwendungstag). Der Kontakt ist zehnmal richtig (M13: zweimal). Die Nachbarn widersprechen dreimal dem Modell (einmal falsch: gleichförmige Formulare eines anderen Absenders, zweimal Paperless' uneinheitliche Typen), daher ist das Fertig-Kriterium nur ohne Nachbarn erfüllt; Tobi (14.10.2026): die Nachbarn bleiben, wie sie sind (Kalibrierung in M18); Beträge ohne jede Währung im Text gelten in `PAPIQ_DEFAULT_CURRENCY` (EUR, umgesetzt, die Lohnabrechnung wird damit grün: mit Nachbarn 2 grün, 8 gelb); Felder „nur von Hand“ als Idee in Issue #52. Die Lernregel der Kennungen bleibt (eine Anteilsregel hätte bei den zehn nur richtige Belege gestrichen). Beim Lauf gefunden und behoben: die UI-Komponenten fragten Attribute ab, die bits-ui nicht setzt (Reiter der Stammdaten, Zustände), das Suchfeld verdeckte den dritten Admin-Eintrag, gelernte Kennungen und Protokollkarten auf schmalen Schirmen. Offen: der Worker bedient die Indexierung vor der Pipeline, nach einem großen Abgleich warten neue Uploads.

### Zwischenschritt: Layout-Text statt Docling

**Anlass:** Im Test des M15-Stands las das Modell auf einer Lohnabrechnung das Grundgehalt statt des Gesamtbruttos, und sein Zitat zum Netto stand nicht im Text. Ursache war Docling: Es nahm die Seite für eine zweispaltige Tabelle, legte mehrere Zeilen in eine Zelle und ließ die letzte Zeile als Fußzeile weg. Tobis Frage (14.10.2026), ob es Docling überhaupt braucht, führte zu einem Vergleich.

**Vergleich (`ocr_test`, lokal, nicht im Repository):** Sechs Varianten an vier echten Dokumenten (Lohnabrechnung, Versicherungsrechnung, Arztrechnung, Zeitnachweis) und fünf synthetischen mit bekanntem Text (Brief mit Seitenleiste, derselbe gescannt, Rechnung gescannt, schlechter Scan eines Kontoauszugs, Handyfoto eines Kassenbons).

| | Wörter (synthetisch, Mittel) | Werte (Beträge, Daten, IBAN, USt-IdNr) | Betrag bei seiner Beschriftung | Dauer je Dokument |
| --- | --- | --- | --- | --- |
| Textebene von OCRmyPDF | 62 % | 53 von 81 | 12 von 27 | 1 bis 6 s (OCR) |
| Docling (bisher) | 48 % | 45 von 81 | 8 von 27 | 6 bis 72 s |
| Docling mit eigener OCR | 46 % | 41 von 81 | 7 von 27 | 7 bis 71 s |
| MarkItDown | 62 % | 53 von 81 | 7 von 27 | 1 bis 2 s |
| Layout-Text (PDFium) | 62 % | 53 von 81 | 12 von 27 | unter 0,1 s |
| VLM (`anthropic.claude-sonnet-5-5` über Open WebUI) | 99 % | 81 von 81 | 26 von 27 | 4 bis 37 s, 2.400 Eingabe-Token je Seite |

Die Lücke der textbasierten Varianten kommt fast ganz aus dem schlechten Scan (Tesseract las 17 % der Wörter, `55,08 S` wurde `55,088`) und dem Foto (Tesseracts Seitenanalyse fand gar keinen Text); auf lesbaren Seiten las der Layout-Text alles. Docling ließ Kopf- und Fußzeilen weg: In einer Stichprobe von 250 Dokumenten der Devinstanz standen bei 89 Kennungen (USt-IdNr, IBAN, Mail-Domain) nur dort. Seine gepolsterten Tabellen füllten das Eingabebudget (die Lohnabrechnung: 11.445 Zeichen, ohne Leerzeichen 3.510; im Prompt fehlten 5.718). MarkItDown verklebte die Wörter eines Arztbriefs, dessen PDF keine Leerzeichen setzt. Das VLM las auch Text in Logos, schrieb aber auf der echten Lohnabrechnung einmal falsch, ließ eine Angabe aus und kommentierte in seiner Ausgabe.

**Entscheidungen (Tobi, 14.10.2026):** Docling ganz entfernen; der Text wird als Klartext gespeichert und in Festbreite gezeigt; `GET /documents/{id}/structure` liefert eine eigene Struktur (Seiten, Zeilen, Wörter mit Boxen); der Bestand wird von Hand neu ausgelesen (`reparse`). Das VLM als Leser schwer lesbarer Seiten ist Issue #53.

**Umsetzung (Branch `feature/layout-parser`, von `feature/understanding-0.2.0`):** Parser `adapters/outbound/pdfium/layout.py` auf den Zeichen und Zeilen von `chars.py` (aus dem Festbreiten-Modul gelöst): Wortgrenzen an den Lücken der Zeichenboxen, Spalten auf dem Raster der üblichen Zeichenbreite, Festbreiten-Blöcke mit ihrem eigenen Raster, eine Seite, deren Text überwiegend quer läuft, in dieser Richtung gelesen (`reading_turn`; hilft auch, wenn OCRmyPDF eine Seite wegen zu geringer Sicherheit nicht dreht), schräger Text am Rand in seiner Richtung. Docling, PyTorch, die Modelle, die Image-Stufe `docling-models`, die CI-Schritte und der Marker `docling` entfallen, ebenso `PAPIQ_PARSE_TIMEOUT` und `PAPIQ_DOCLING_MODELS_PATH`; das Image schrumpft von 4,6 auf 1,4 GB. Der Text heißt `content.txt` (`text/plain`); Leser nehmen `content.md` eines alten Dokuments, bis es neu ausgelesen ist. Ohne Docling fällt kein Parser mehr zurück: `ParseResult.note` und der unsichere Ausgang des Parse-Knotens entfallen (leere Seiten machen nichts gelb). Das Eingabebudget zählt einen Lauf von Leerzeichen einmal. `reparse` (Befehl und `POST /pipeline/reparse`) reiht je Dokument einen Job ein, der den Parse-Knoten neben der Verarbeitung ausführt und protokolliert (`input.reparse`). Der Inhalt-Tab zeigt den Text seitenweise in Festbreite; die Markdown-Ansicht und `marked` entfallen.

**Beobachtungen:** Ein seitlich liegender Scan wurde von OCRmyPDF nicht gedreht (Sicherheit der Ausrichtung 11,4 unter der Schwelle 14); bei 90° und 180° las Tesseract dann nichts Brauchbares, bei 270° liest der Layout-Text die Seite richtig. `test_api_end_to_end` (bisher nur mit dem Marker `docling`) scheiterte zweimal unter Volllast an seiner Frist von 10 s für die SSE-Ereignisse und lief danach dreimal grün. `deploy/test-image.sh` läuft im Devcontainer nicht (docker-outside-of-docker bindet die Temp-Dateien nicht ein); ein Rauchtest des Images über die API lief: OCR ok, Parsen ok mit `layout 1`.

**Uneinige Nachbarn (Tobi, 14.10.2026):** In der neuen, leeren Devinstanz wurde die erste Lohnabrechnung eines neuen Arbeitgebers gelb: Modell und Name im Text nannten ihn, die fünf ähnlichsten bestätigten Dokumente waren Abrechnungen früherer Arbeitgeber (drei des einen, zwei des anderen), und drei von fünf galten als Widerspruch. Neu: Bekommt unter den Nachbarn ein zweiter Kontakt ebenfalls zwei Stimmen, nennen sie keinen Kontakt. Ein Kontoauszug, auf dem das Modell einen Zahlungsempfänger als Absender nimmt, bleibt gelb (dort sind sich die Nachbarn einig). Die angezeigte Konfidenz von 89 % war die Obergrenze unsicherer Felder (`_UNSURE`), keine Messung.

**Offen:** Die Messung in der Kopie der Devinstanz (Bestand neu auslesen, die zehn Originale und eine Stichprobe ab den Signalen neu verarbeiten, Lanes, Felder und Zitatfehler gegen den Docling-Stand), danach die Entscheidung, den Branch zurück nach `feature/understanding-0.2.0` zu mergen.

### Zwischenschritt: Felder mit Beschreibung

**Anlass (Tobi, 14.10.2026):** Dokumente werden oft gelb, „weil die Namen der Felder im Text nicht gefunden werden“; die Feldnamen (`LA_Brutto`, `LA_Netto`) vergeben wir intern. Gemessen: Gelb machte fast immer das Zitat, nicht der Name. 70 von 74 Feldprüfungen an 37 Lohnabrechnungen scheiterten daran, dass das Modell Beschriftung und Wert aus verschiedenen Stellen zusammensetzte oder Beträge umschrieb. In 59 der 70 Fälle war der Wert trotzdem richtig. Eine gelockerte Prüfung hätte 10 der 11 falschen Werte grün gemacht.

**Entscheidungen (Tobi, 14.10.2026):**
- Beschreibung als Freitext am Feld (bis 300 Zeichen, in beiden Prompts, in Feldverwaltung und Formular).
- Prompt schärfen und messen, die Prüfung bleibt streng.
- Nach dem falschen Grün der Messung zusätzlich:
  - A: Das Zitat muss den Wert zeigen.
  - B: Ein Betrag zählt im Zitat nur als Betrag.
  - D: Werte auf schwach gelesenen Seiten sind nur Vorschläge; die Schwelle steht in `PAPIQ_POOR_READING_THRESHOLD`.
  - C (die Beschriftung beim Wert) ist Issue #55.
- Eine fremde OCR-Schicht liest Papiq selbst neu (`--redo-ocr`).
- Das Modell der Devinstanz ist `anthropic.claude-sonnet-5-5`, mit Testläufen von höchstens 15 Dokumenten.

**Umsetzung (Branch `feature/field-hints-0.2.0`, von `release/0.2.0`):**
- **Feld und Schema:** `FieldDefinition.description` (Revision 0017), REST `description` an Feldern.
- **Prompts** `propose-2` und `extract-3`: Beschreibung hinter Name und Optionen, Zitat „wie es dasteht“, bei getrennter Beschriftung die Zeile des Werts.
- **Weboberfläche:** Textfeld in der Feldverwaltung, Hinweis unter dem Feld im Formular.
- **Bewertungssatz:** Felder mit Beschreibung, Fall 35 (Grundgehalt und Gesamtbrutto).
- **Prüfungen A und B:** in `_check_field` und `_check_date` (auch für das Dokumentdatum), dazu `amounts_in`.
- **D, Lesung je Seite:** Sie kommt aus dem hOCR, das OCRmyPDF mit `--keep-temporary-files` in einem eigenen Arbeitsordner behält (`OcrResult.readings`, Protokoll `readings`). Unsicher ist ein Wort unter 60 % Sicherheit; weniger als 10 Wörter sagen nichts.
- **D, Wirkung:** Felder-Knoten und Urteil machen einen grünen Wert auf einer schwach gelesenen Seite zum Vorschlag (die Seite des Zitats, ohne Fundstelle jede gelesene).
- **Fremde OCR:** Ein PDF mit unsichtbarem Text bekommt `--redo-ocr` statt `--skip-text` und `--deskew`; das Protokoll vermerkt `read_again`.
- **Sonnet 5.5:**
  - `PAPIQ_LLM_TEMPERATURE=none` lässt die Temperatur weg.
  - Die Schemas schreiben „eins aus der Liste oder null“ als `anyOf` und haben keine Grenzen für Listen.
- **Protokoll-Ansicht:** Sie zeigt die Lesung je Seite.

**Messung (`reviews/field-hints.md`, 15 Dokumente):**
- alter Stand: 0 grün;
- neuer Prompt mit A und B: 10 grün;
- dazu eigene OCR und D: 10 grün, die fünf schwach gelesenen gelb;
- in keinem Lauf ein echtes falsches Grün. Grüne Abweichungen gab es nur bei den Bestätigungen, die Tobi prüft.

**Beobachtungen:**
- Die Originale der Lohnabrechnungen tragen die OCR von ecoDMS, die bisher ungeprüft blieb.
- Groqs Tageskontingent reichte für 45 Dokumente, und es teilt sich den Schlüssel mit der Devinstanz.
- `-k` druckt den Ort der Dateien mitten in die Ausgabe vor den Fehler; der Adapter entfernt die Zeilen.

**Offen:**
- Tobis eigene Beschreibungen für LA_Brutto und LA_Netto.
- Die Prüfung der fraglichen Bestätigungen.
- Der Bestand liest erst nach „ab OCR neu verarbeiten“ neu.
- Die Kalibrierung der Schwelle (M18).

### Zwischenschritt: Titelmuster als Regel, Liquid als Template-Sprache

**Anlass (Tobi, 14.10.2026):** Ein Titelmuster soll kein eigener Code sein, sondern die Abkürzung für eine vorgefertigte Regel: sichtbar unter Regeln, schreibgeschützt, mit dem Dokumenttyp als verlinktem Eigentümer. Regeln (und Muster) bekommen eine echte Template-Sprache mit allen Dokumentdaten und Feldern (`field.la_brutto`).

**Entscheidungen (Tobi, 14.10.2026):**
- Liquid (`python-liquid` 2.3, neue Abhängigkeit) statt Jinja2: Regeln schreiben auch Nutzer, Liquid ist dafür gebaut (nur übergebene Daten, Grenzen), die Sandbox von Jinja2 hatte 2024/2025 zwei Ausbrüche und kennt keine Schleifengrenze.
- Feldschlüssel aus dem Namen; Umbenennen schreibt Templates um.
- Eine Nutzerregel, die den Titel setzt, hat Vorrang vor der Typ-Regel.
- Templates in „Titel setzen“, „Feld setzen“ für Textfelder und im Grund von „Prüfung erzwingen“; Bedingungen bleiben ein Baukasten.
- Locale und Währung der Templates nur aus Einstellungen (`PAPIQ_TEMPLATE_LOCALE`, `PAPIQ_DEFAULT_CURRENCY`), keine Vorgabe im Adapter.

**Umsetzung (Branch `feature/templates-0.2.0`, von `release/0.2.0`):**
- **Port und Adapter:** Port `TemplateRenderer` (`check`: genutzte Pfade, `render`: Text oder `MissingValueError`), Adapter `adapters/outbound/liquid`.
  - Ohne `include`/`render`; Grenzen 1.000 Schleifendurchläufe und 10.000 Zeichen.
  - `FalsyStrictUndefined`: Ein fehlender Wert lässt sich prüfen, wirft aber bei der Ausgabe.
  - Zahlen als Gleitkommazahl, die ihren genauen Dezimaltext schreibt (Liquids Rechenfilter kennen kein `Decimal`); Beträge als Objekt mit `amount` und `currency`; Daten ohne Zeitzonenverschiebung; Babel-Filter.
  - Wie `regex` auch im Speicher-Container (Ausnahme in `architektur.md`).
- **Domäne:** `FieldDefinition.key` (`template_key`) mit Eindeutigkeit.
- **Engine:** Der Regel-Planer rendert mit dem Renderer, nachdem Typ, Kontakt, Ablage, Felder und Tags aufgelöst sind; die Namen von Tags, Schublade und Besitzer kommen über die Situation dazu. Fehlende Werte erzeugen eine Notiz `skipped`; eine erzwungene Prüfung hält mit dem rohen Grund.
- **Speichern:** Prüfung von Syntax und Werten, Ablehnung alter Platzhalter mit Hinweis.
- **Felder:** Umbenennen schreibt Regeln und Muster um; Löschen und Einengen sind gesperrt, solange ein Template das Feld nutzt.
- **Revisionen:**
  - 0018 übersetzt gespeicherte Regeln so, dass sie tun, was sie taten (`| default: ''`, `{% raw %}`).
  - 0019 übersetzt die Typmuster und legt je Muster eine Regel an. Die Zuordnung steht in einer eigenen Tabelle `title_pattern_rules`, weil SQLite eine Spalte mit Fremdschlüssel an `rules` nicht ohne Kopie der Tabelle wieder entfernen kann; die Regelversionen hängen an `rules`.
- **Typ-Regel:** global, Bedingung „Dokumenttyp ist X“, Aktion „Titel setzen“, Priorität 0, Eingang und Änderung; schreibgeschützt (409), weicht anderen Titeln (`yielded`).
- **Felder-Knoten und Bestätigen:** Der Felder-Knoten gibt nur den Titel des Modells; Bestätigen rendert kein Muster mehr (die Regeln laufen nach dem Bestätigen ohnehin).
- **Vorschau und Weboberfläche:**
  - `POST /templates/preview`.
  - Ein Template-Feld in Festbreite mit Werten und Feldschlüsseln zum Einfügen und der Vorschau am neuesten Dokument des Typs, im Typ-Dialog und im Regel-Editor.
  - Abzeichen „Titelmuster von …“ mit Link zum Typ (`?type=` öffnet den Dialog).
  - Der Feldschlüssel in der Feldverwaltung.

**Prüfung an echten Daten:**
- Eine Kopie von `papiq_live` (3 Typen mit `{document_date:YYYY/MM}`, keine Regeln) lief auf 0019: drei Muster in Liquid, drei Typ-Regeln.
- Im Browser (hell, dunkel, Handybreite):
  - Liste und Detail schreibgeschützt mit Link.
  - Der Typ-Dialog zeigt die Vorschau am neuesten Dokument, mit deutschem Monatsnamen und Betrag.
  - Die alte Schreibweise wird mit Hinweis abgelehnt.
- Die Kopie ist gelöscht.

**Beobachtungen:**
- Ein Muster in alter Schreibweise ist gültiges Liquid und erscheint wörtlich im Titel. Eine Ablehnung beim Speichern gab es kurz; Tobi (15.10.2026) will sie nicht: Dann steht eben `{document_date}` da.
- Fehlermeldungen des Servers bleiben englisch wie alle Meldungen der API.

**Offen:**
- Bestehende Dokumente tragen ihren alten Titel, bis die Regel angewendet wird („auf bestehende Dokumente anwenden“) oder sie neu verarbeitet werden.

### Zwischenschritt: Template-Editor, Stammdaten-Dialoge überall, Trainingsende als Ereignis

**Anlass (Tobi, 15.10.2026):** Der Test der Titelmuster ergab neun Punkte: die alte Schreibweise nicht mehr prüfen, kleinere Einfüge-Chips, den langen Hinweis entfernen; Stammdaten-Dialoge überall an Ort und Stelle, auch aus der Regel, mit einem Ausstieg zur Regel; die Klassifikator-Seite nach einem Training ohne Neuladen aktualisieren; ein Template-Editor von rechts mit Referenzdokument und Syntaxfarben für alle Template-Stellen. Punkt 6 (Autor „unbekannt“ bei Version 1) entfiel: Das Verhalten passt.

**Entscheidungen (Tobi, 15.10.2026):**
- Die alte Schreibweise wird nicht geprüft; rot ist nur ein ungültiges Template.
- Template-Editor mit CodeMirror 6 (neue Abhängigkeit) und eigener Liquid-Färbung.
- Das Ende eines Trainings ist ein Ereignis, kein Abfragen.
- CodeMirrors Styles im Shadow Root statt einer gelockerten CSP.

**Umsetzung (Branch `feature/template-editor-0.2.0`, von `release/0.2.0`):**
- **Alte Platzhalter:** `old_placeholder` und seine Prüfungen entfallen; `{document_date}` ist Text. Issue #58: eine Template-Dokumentation aus den Editoren verlinken.
- **Stammdaten-Dialoge:**
  - Ein Store (`masterdata-dialog.svelte.ts`) und ein Host im App-Layout öffnen Kontakte, Typen, Tags und Felder von jeder Seite.
  - Der Felder-Dialog ist aus der Feldverwaltung gelöst (`FieldDialog.svelte`).
  - Listen laden nach dem Speichern neu (`changed`).
  - Das Abzeichen „Titelmuster von …“ öffnet den Typ für Admins an Ort und Stelle. Der Typ-Dialog verlinkt die Regel, außer er kam aus ihr.
- **Trainingsende:** Domänenereignis `classifier.trained` (Nummer, Beispiele) in derselben Unit of Work wie das Modell. Der Ereignisstrom gibt es an aktive Admins (nicht an einen Strom für ein Dokument), Webhooks wählen weiter nur Dokumentereignisse. Die Klassifikator-Seite lädt bei dem Ereignis und nach einer Unterbrechung des Stroms neu.
- **Template-Editor:**
  - `lib/templates/liquid.ts` färbt, findet die Stelle für Vervollständigungen und fügt Werte und Filter passend ein (reine Funktionen, auch für die statische Ansicht).
  - `lib/templates/codemirror.ts` ist der Editor (Dekorationen statt Grammatik, Vervollständigung, Undo), nachgeladen; ein Chunk von etwa 97 KB gzip. Zwei junge transitive Pakete (`@codemirror/language` 6.13.1, `@lezer/lr` 1.4.11, acht Tage alt) sind per `pnpm-workspace.yaml` auf ältere Releases gesetzt; das Dockerfile kopiert die Datei mit.
  - `TemplateInput` (klein) und `TemplateEditor` (Sheet, `popover`- und `command`-Wrapper aus shadcn-svelte für das Referenzdokument) ersetzen das Template-Feld im Typ-Dialog, in „Titel setzen“, im Wert eines Textfelds und im Grund von „Prüfung erzwingen“.
  - `TemplatePreviewOut.values`: die Werte des Dokuments, wie ein Template sie schreibt, für die Liste im Editor.
  - Die Regelansicht färbt Templates statisch (`TemplateText`).
  - Tests ersetzen den Editor durch ein Textfeld (`tests/setup.ts`); die Grammatik, die Vervollständigung und der echte Editor in jsdom sind eigens getestet.

**Prüfung an echten Daten:**
- Eine Kopie von `papiq_live` lief mit der API auf Port 8001, ohne Worker; Browser hell, dunkel und in Handybreite, erst am Dev-Server, dann am Build. Geprüft:
  - Typ-Dialog aus den Stammdaten mit Link zur Regel, aus dem Abzeichen ohne.
  - Färbung, Vervollständigung, Fehler nur bei ungültigem Template.
  - Editor mit Referenzdokument, Werten, Suche, Einfügen, Filter-Chips, Übernehmen und Abbrechen.
  - Regelansicht eingefärbt.
- Das Trainingsende ohne echtes Training: Die Kopie teilt den S3-Bucket mit `papiq_live`, ein Training hätte dort ein Modell geschrieben und das älteste gelöscht. Stattdessen standen eine Modellzeile und das Ereignis in der Outbox der Kopie; die Seite zeigte das neue Modell ohne Neuladen, mit Toast.
- Die Kopie ist gelöscht.

**Beobachtungen:**
- Der Host der Stammdaten-Dialoge las in seinem Effekt den Eintrag, den er gerade gesetzt hatte, wenn eine Liste ihn ganz übergab: Endlosschleife, die Seite fiel auf die Fehlerseite. Die Tests hatten nur mit IDs geöffnet; jetzt lädt er in lokale Variablen, ein Test öffnet mit dem ganzen Eintrag.
- Im Build sperrte die CSP CodeMirrors `<style>`-Element; der Dev-Server sendet keine CSP. Im Shadow Root nahm die Fokusfalle von bits-ui den Fokus im Editor für entwichen (`Node.contains` sieht nicht in Shadow Roots) und holte ihn zurück; ein Listener am Fenster hält `focusin` aus dem Editor von ihr fern.
- Vorschläge im Typ-Dialog wurden am Rand des Dialogs abgeschnitten; sie öffnen jetzt innerhalb des Dialogs, nach oben, wo unten kein Platz ist.

**Offen:**
- Die Template-Dokumentation (#58).

### Zwischenschritt: Bezugszeitraum als Feld-Datentyp „Zeitraum“

**Anlass (Tobi, 15.10.2026):** Viele Dokumente beziehen sich auf einen Zeitraum, der nicht ihr Datum ist: Ein Zeitnachweis vom Oktober zeigt den September, eine Lohnabrechnung ihren Monat, eine Rechnung einen Leistungszeitraum. Solche Zeitfenster sollen standardmäßig erkannt werden.

**Entscheidungen (Tobi, 15.10.2026):**
- Bezeichnung „Bezugszeitraum“.
- Kein eingebautes Feld am Dokument, sondern ein Feld-Datentyp „Zeitraum“: pro Typ zuordenbar, Pflicht oder optional, die Beschreibung als Hinweis an das Modell. Die Bedeutung trägt der Typ, nicht der Name.
- Die Feldregeln gelten unverändert (ein durchgefallener Wert macht gelb).
- Ohne Jahr nur ein Vorschlag mit dem Jahr des Dokumentdatums.
- Regel-Operatoren: ist, überschneidet, beginnt nach, endet vor, vorhanden, fehlt.
- Filter in Liste und Suche später.

**Umsetzung (Branch `feature/period-0.2.0`, von `release/0.2.0`):**
- **Ein Codec für Feldwerte** (Tobis Hinweis): Die JSON-Form eines Feldwerts wurde zweimal geschrieben und zweimal gelesen (Domäne und API), die Kopien waren auseinandergelaufen (`5.335E+7` gegen `53350000`). `field_to_json` und `field_from_json` stehen jetzt bei den Feldtypen und dienen API, MCP, Regeln, Übernahme und Protokoll; `values.py` entfällt.
- **Domäne:** `Period(start, end)` in einem eigenen Modul (`core/domain/periods.py`), die Art aus den Enden (`PeriodKind`), JSON `{start, end}`.
- **SQL:** Revision 0020, `document_fields.value_date_end`. Der Downgrade macht Zeiträume zu Daten (ihrem Anfang). Wo Unbekanntes bisher als Datum durchrutschte (`case _`), steht das Datum jetzt ausdrücklich.
- **Lesen:** `evidence.py` liest Zeiträume mit ihrer Stelle im Text. Von einem Wert gilt die Lesung, die in keiner anderen liegt (die Tage einer Spanne, das Jahr eines Monats zählen nicht); zwei sind keine. Ein Text zeigt einen Zeitraum, wie er dasteht, oder mit erstem und letztem Tag.
- **Prompts:** `extract-4` fragt nach `{start, end}`, `propose-2` nimmt den Zeitraum wie geschrieben; das Antwortschema ist Claude-verträglich.
- **Prüfung:** wie ein Datum; ohne Jahr oder als Tag gleich dem Dokumentdatum ein Vorschlag; schwach gelesene Seiten wie bei allen wörtlichen Werten.
- **Regeln:** Operator `overlaps`; `gt` und `lt` vergleichen den Anfang bzw. das Ende mit einem Datum (`condition_value`). Templates: `.start`, `.end`; ausgegeben als „September 2026“, „KW 38 2026“, „Q3 2026“. Suchindex: der Zeitraum in beiden Schreibweisen und als Wort.
- **Web:** Typ im Felddialog; Eingabe Von/Bis und ein Monatswähler (`PeriodInput`), auch in Regeln; Anzeige `formatPeriod` (in UTC). Die Stellen, die „Objekt = Betrag“ annahmen, prüfen ausdrücklich `isMoney`/`isPeriod`; `sameValue` vergleicht unabhängig von der Reihenfolge der Schlüssel.

**Prüfung:**
- Evaluation: Feld „Bezugszeitraum“ für Lohnabrechnung und Zeitnachweis (neuer Typ), die vier Lohnabrechnungen mit ihrem Monat, zwei neue Fälle (Zeitnachweis „Abrechnungsmonat: September 2026“, Wochenbericht „KW 38/2026“). Feste Antworten: kein falsches Grün, keine Verstöße.
- Sonnet 5.5, kalt, zehn Dokumente: Bezugszeitraum 5 von 5 richtig und ohne Person übernommen. Dabei zeigte sich eine Lücke im Set: Zwei Lohnabrechnungen zeigen ein Gesamtbrutto, das ihre Erwartung nicht kannte (das Feld kam später); nachgetragen, danach richtig.
- Trainiert kommen die Zeitnachweise gelb: Die Lohnabrechnungen desselben Arbeitgebers im Korpus nennen einen anderen Typ, ein Widerspruch (`lanes`).

**Offen:**
- Filter nach Bezugszeitraum (und Dokumentdatum) in Liste und Suche.
- In der Telefonrechnung des Sets liest Sonnet „Fällig am“ anders als erwartet; der Text nennt nur eine Abbuchung „frühestens am 12.04.2026“.

### Zwischenschritt: VLM als Texterkennung auf Wunsch (Issue #53)

**Anlass:** Schlechte Scans und Fotos liest Tesseract kaum oder gar nicht (synthetischer Kontoauszug: 17 % der Wörter, das Foto eines Kassenbons: nichts); dort las im Vergleich in `ocr_test` nur ein VLM. Auf der Devinstanz (80 Dokumente, 15.10.2026) hat Tesseract 12 Seiten selbst gelesen, 5 davon schwach (ab 8 % unsicherer Wörter), keine ohne Text.

**Entscheidungen (Tobi, 15.10.2026):**
- Ein Dokument an ein VLM zu senden, ist immer eine Entscheidung eines Menschen: ein Knopf „Mit VLM lesen“, der immer da ist, wenn ein VLM eingerichtet ist; keine Automatik.
- Eine neue Schwelle gibt es nicht: Empfohlen ist der Knopf, wo die Texterkennung das Dokument schon rot (kein Text) oder gelb (eine schwach gelesene Seite) macht.
- Immer das ganze Dokument, keine Seitenauswahl.
- Liest das VLM, zählt sein Text als OCR und gehört in die Textebene des Archiv-PDFs.
- Eigene Einstellungen `PAPIQ_VLM_*`, nicht die des Sprachmodells: Seitenbilder gehen nur dorthin, wo es ausdrücklich eingetragen ist.
- Das Archiv ist der Speicher der Lesung: „Neu verarbeiten ab Parse“ und `reparse` behalten sie. „Neu verarbeiten ab OCR“ fragt bei einem Dokument, das zuletzt das VLM las, ob wieder das VLM liest, vorausgewählt (jede Sendung bleibt eine Entscheidung; Tobi, 15.10.2026, nach dem ersten Stand, der dann wortlos mit Tesseract las). Woran man es erkennt: das Protokoll der OCR und die Metadaten des Archivs (`/Creator`, `xmp:CreatorTool`: „Papiq, vision language model … (read-1)“).
- Kein Werkzeug im MCP-Server.

**Versuch vorab (Sonnet 5.5 über Open WebUI, die fünf synthetischen Seiten aus `ocr_test`, zehn Aufrufe):** Die Zeilen mit ihrer Box in Tausendsteln des Bildes liefen auf dem dichten Kontoauszug ab der Mitte nach unten weg und lagen auf dem Foto bis zu 17 Zeilen daneben. In Pixeln des gesendeten Bildes (höchstens 1,15 Megapixel, damit der Dienst nicht noch einmal skaliert) saß jede Zeile: Überdeckung mit der wahren Lage im Median 0,79 bis 0,91, auf dem Foto 35 von 35 und auf dem Kontoauszug 80 von 80 Zeilen mit mindestens 0,5; Wörter 99 bis 100 %. Je Seite etwa 2.100 Eingabe- und 1.100 bis 2.400 Ausgabe-Tokens, 10 bis 15 s.

**Umsetzung (Branch `feature/vlm-reader-0.2.0`, von `release/0.2.0`):**
- **OCR-Plugin:** OCRmyPDF 17 nimmt eigene Texterkennungen als Plugin. Mit `vision` läuft es mit `--force-ocr --deskew` und `ocrmypdf/plugin.py`: Drehung und Schräglage bestimmt weiter Tesseract, jedes vorbereitete Seitenbild geht ans VLM (Prompt `read-1`, JSON-Schema, nach einer unpassenden Antwort einmal neu), die Wörter einer Zeile teilen sich ihre Box nach Länge, OCRmyPDF schreibt daraus die Textebene. Die Einstellungen gehen in der Umgebung in OCRmyPDFs Prozess, je Seite bleibt ein Protokoll in einem eigenen Ordner. `[unreadable]` zählt als unsicheres Wort.
- **Port:** `Ocr.vision` nennt das Modell, `make_archive(vision=True)` liest mit ihm, `OcrResult.vision` hält die Aufrufe. Fake und Vertragstest; der echte Adapter gegen einen Ersatz-Endpunkt.
- **Einstellungen:** `PAPIQ_VLM_BASE_URL`, `_MODEL`, `_API_KEY(_FILE)`, `_TIMEOUT` (das ganze Dokument, Vorgabe 30 Minuten; eine Seite höchstens 5 Minuten). Die Leasedauer der Jobs wächst damit; beim Start warnt Papiq vor einem VLM außerhalb des lokalen Netzes.
- **Pipeline:** `POST /documents/{id}/vision` (Besitzer und Admins) verarbeitet ab OCR neu; der Lauf merkt sich die Bitte (`processing.vision`, Revision 0021), ein Wiederholen fragt erneut. Der OCR-Knoten protokolliert Modell, Endpunkt, Prompt und je Aufruf die Tokens (im Token-Tooltip „N Aufrufe“ statt Versuche) und legt die Aufrufe wie die des Sprachmodells ab (`exchanges/ocr.json`, das Bild als Platzhalter); ohne eingerichtetes VLM scheitert er. Ein VLM, das nicht lesen kann, ist ein vorübergehender Fehler (Wiederholung, dann rot).
- **Empfehlung:** `GET /documents/{id}/reading` mit den Seiten und `advice` (`no_text`, `poor_page`); keine mehr, sobald das VLM gelesen hat. `GET /pipeline` nennt Modell und Endpunkt.
- **Web:** „Mit VLM lesen“ im Aktionsmenü, bei Empfehlung markiert und als Hinweis unter dem Kopf; ein Dialog nennt Endpunkt und Modell, bevor etwas gesendet wird. Während des Lesens sagt der Kopf es, der Inhalt trägt „vom VLM gelesen“, das Protokoll öffnet den Prompt der OCR. Neu verarbeiten ab OCR fragt nach einer VLM-Lesung „Wieder mit dem VLM lesen, wie beim letzten Mal“ (vorausgewählt, mit Endpunkt und Modell; der Knopf heißt dann „Mit VLM neu verarbeiten“ und ruft `POST /documents/{id}/vision`); abgewählt oder ohne VLM warnt er, dass die Lesung verloren geht.

**Prüfung:**
- Neue Beispiele, nur mit Pillow erzeugt: ein Scan mit 100 dpi, Schatten, Rauschen und starkem JPEG sowie ein Kassenbon, schräg auf einem Tisch fotografiert. Beide gut lesbar, Tesseract findet auf beiden keinen Text.
- Integrationstest mit echtem OCRmyPDF, Ersatz-VLM und Ersatz-Sprachmodell: ohne Bitte rot mit `no_text`, nichts gesendet; mit „Mit VLM lesen“ grün mit Kontakt, Typ, Datum und Pflichtfeld.
- Im Browser gegen den Build auf einer leeren Instanz mit Ersatz-VLM: Hinweis, Menü, Dialog, Lesung, Inhalt, Prompt, Warnung; hell, dunkel, Handybreite, ohne Konsolenfehler und CSP-Verstöße.

**Offen:**
- Ein echter Lauf an Scans und Fotos der Devinstanz (Seiten gehen dabei hinaus, nur mit Tobis Zustimmung).
- Ein lokales Vision-Modell auf dem NUC statt eines entfernten; der Adapter passt für jeden OpenAI-kompatiblen Endpunkt, der Bilder nimmt und Pixel-Boxen liefert.
- Fotos vorher entzerren (Perspektive), damit Tesseract sie liest; bräuchte OpenCV.

### Zwischenschritt: Fakten und Codes am Dokument

**Anlass (Tobi, 15.10.2026):** Papiq liest „Fakten“ auf dem Dokument (Namen und Kennungen von Kontakten) und ordnet sie Kontakten zu; zu sehen waren sie nur am Kontakt. Tobi wollte sie am Dokument sehen, mit der Fundstelle, dazu eine Erkennung von Bar- und QR-Codes, und Kennungen per Klick an den Kontakt heften.

**Entscheidungen (Tobi, 15.10.2026):**
- Ort: Inhalt › Text (Variante B des Entwurfs). Die Textbox wird schmaler, rechts „Fakten“, darunter „Codes“. Unter Details nur ein Chip am Kontaktfeld, der zum Inhalt führt; die Spalte unter Details ist schon voll. Variante A (ein Umschalter „Angaben“ / „Fakten und Codes“ in dieser Spalte) verworfen.
- Erkennung mit `zxing-cpp` (neue Abhängigkeit). Eine Python-Lösung ohne Systempakete und mit Data Matrix gibt es nicht: `pyzbar` braucht `libzbar0` und liest kein Data Matrix, `pylibdmtx` nur Data Matrix, OpenCV ist groß, `pyzxing` braucht Java.
- Die Codes liest das Fakten-Signal; kein neuer Knoten, keine Änderung am Graphen.
- Anheften vorerst nur Admins.
- Mit dabei: die Warnung beim eigenen Konto und „wie ‹Betragsfeld›“ am GiroCode.
- Nachtrag: Die Personalnummer ist eine eigene Kennung (`personnel_number`): hinter ihrer Beschriftung, 3 bis 30 Zeichen, führende Nullen fallen weg. Der Name des Kontakts unter „Belegen“ öffnet für Admins seinen Dialog an Ort und Stelle. Codes mit gleichem Format und Inhalt (derselbe Link auf jeder Seite eines Briefs) stehen einmal, mit einem Chip je Seite wie die Fundstellen der Felder; `codes.json` hält weiter jeden Abdruck.
- Mit dem Plan freigegeben („Okay, baue das.“): Links in QR-Codes nur als Text mit Kopierknopf, nicht anklickbar (der Inhalt ist fremd); eine Kennung aus einem Code zählt im Urteil wie eine im Text; nach dem Anheften keine neue Bewertung des Dokuments.

**Umsetzung (Branch `feature/facts-codes-0.2.0`, von `release/0.2.0`):**
- **Codes lesen:** Port `CodeReader` (`read_codes(pdf) → CodeReading`), Adapter `adapters/outbound/zxing`: PDFium rendert jede Seite des Archivs in Graustufen mit 300 dpi (Kante höchstens 5000 px, höchstens 50 Seiten, unter der Sperre von PDFium), zxing-cpp liest alle Formate. Ungültige und leere Funde fallen weg, gleiche je Seite zählen einmal; die Box steht in Bruchteilen der Seite. Fake und Vertragstest. Domäne `codes.py`: der GiroCode (EPC069-12) mit Empfänger, IBAN, BIC, Betrag und Zweck; Kennungen im Inhalt findet dieselbe Erkennung wie im Text, dazu die IBAN des GiroCodes. Das Beispiel `codes.pdf` (GiroCode, Code 128, Data Matrix) erzeugt `make_samples.py` mit zxing-cpp.
- **Fakten:** `signal_facts` liest die Codes einmal und legt sie als `codes.json` ab (mit der Version des Lesers); die OCR löscht die Datei mit einem neuen Archiv, eine neue Bewertung nimmt sie. Scheitert das Lesen, warnt das Signal und läuft ohne Codes. Revision 0022: `document_identifiers` (Dokument, Art, Wert), gefüllt vom Signal; daraus zählt `contacts_of` die Kontakte bestätigter Dokumente. Die Fakten stehen einmal in `domain/facts.py`, für Signal und Ansicht, mit Spannen im Text: Namen über Satzzeichen und Umlaut-Schreibweisen hinweg, Kennungen über Trennzeichen.
- **Bestand:** `read-codes` (Befehl, VS-Code-Task „Papiq: Codes nachholen“) reiht für jedes Dokument mit gelesenem Text, das nicht in Verarbeitung ist, einen Job `pipeline.facts` ein: Codes lesen und `document_identifiers` füllen, ohne Protokoll und ohne neue Bewertung. Übernommene Dokumente bekommen ihre Codes nur so, weil sich das Signal bei ihnen enthält.
- **API:** `GET /documents/{id}/facts` (Leserecht, 409 ohne Text): Namen und Kennungen mit dem Kontakt, dem sie heute gehören, und ihren Spannen im Text (Zeichen wie bei `/text`); die Codes einmal je Format und Inhalt, mit ihren Seiten und Boxen (eine je Abdruck), GiroCode, Kennungen und den Betragsfeldern gleichen Werts; `other_contacts` nur für Admins. `POST /contacts/{id}/identifiers` (Admins) heftet genau eine Kennung von Hand an; `PATCH` ersetzt die ganze Liste ohne Versionsprüfung.
- **Web:** `FactsPanel` neben dem Text, auf schmalen Schirmen darunter: „Belegen ‹Kontakt›“, „Andere Kontakte“, „Keinem Kontakt zugeordnet“. Überfahren markiert die Fundstelle im Text, ein Klick hält die Markierung und scrollt dorthin; ein Code markiert, was von seinem Inhalt auch im Text steht. Je Code ein Ausschnitt des Seitenbilds (`CodeThumb`, per CSS), der GiroCode als Überweisung mit „wie ‹Feld›“, anderer Inhalt roh mit Kopierknopf; je Seite ein Chip wie an den Feldern, der Details auf dieser Seite öffnet, alle Fundstellen des Codes markiert. Anheften an den Kontakt des Dokuments (vorausgewählt) oder einen anderen, die Warnung schon an der Zeile. Der Chip unter dem Kontaktfeld zählt Belege, Kennungen ohne Kontakt und Codes.

**Prüfung:**
- Unit- und Vertragstests; der zxing-Adapter am Beispiel `codes.pdf` (alle drei Codes, etwa 0,1 s je Seite). Die übrigen Beispiele (Scans, schlechter Scan, Foto) ergeben keine falschen Funde.
- Evaluation mit festen Antworten: gleich wie auf `release/0.2.0` (73 Fälle, keine falschen Grünen, keine Verstöße), auch mit der Personalnummer.
- Im Browser gegen den Build auf einer leeren Instanz mit einer synthetischen Rechnung (GiroCode, Code 128, Data Matrix): Chip, Markieren, Anheften, Code auf der Seite; hell, dunkel, Handybreite, ohne Konsolenfehler.

**Offen:**
- Tesseract liest in IBANs gern „O“ statt „0“ (die synthetische Rechnung: „DEO2 …“), die Kennung fehlt dann. Die Erkennung könnte in IBANs „O“ als „0“ lesen, wenn die Prüfziffer dann stimmt.
- Fundstellen von Namen und Kennungen auch auf der Seite (Box im Archiv), nicht nur im Text.
- Anheften auch für Besitzer.
- Nach dem Merge auf der Devinstanz: Revision 0022 und `read-codes`.

### Zwischenschritt: Dokumentansicht – ganze Höhe, Seiten nebeneinander, Metadaten

**Anlass (Tobi, 16.10.2026):** Die Dokumentansicht scrollte als Ganzes, Inhalt und Details sahen verschieden aus, die Ansicht der Seiten zeigte eine Seite je Reihe und zoomte nur mit Knöpfen; der Text hatte noch die JSON-Ansicht aus der Docling-Zeit, und anders als in Paperless-ngx fehlten die Metadaten der Dateien.

**Entscheidungen (Tobi, 16.10.2026):**
- Ab Desktop-Breite passt die Ansicht ins Fenster, gescrollt wird nur in ihren Karten; am Handy scrollt die Seite.
- Inhalt in den Karten und Spalten von Details, der Text ohne inneren Rahmen.
- Die JSON-Ansicht fällt weg; `/structure` und `content.json` bleiben im Backend.
- Seiten nebeneinander, die Aufteilung folgt dem Zoom; „1 Seite“, „2 Seiten“ (Vorgabe), „Übersicht“ (vier je Reihe). Herauszoomen hält bei der Übersicht; eine Aufteilung, die der Zoom erreicht, ist markiert (nach einem klickbaren Muster). Zoom mit Strg/⌘ und Mausrad, Pinch und zwei Fingern.
- Ein Tab „Metadaten“ wie in Paperless-ngx; `pikepdf` wird direkte Abhängigkeit.

**Umsetzung (Branch `feature/document-view-0.2.0`, von `release/0.2.0`):**
- **Layout:** Die App-Hülle füllt ab `lg` mit einer `data-wide`-Seite das Fenster; die Tabs Details, Inhalt, Metadaten und Protokoll füllen den Rest, ihre Karten scrollen in sich (im Protokoll Knoten und Laufkasten getrennt). Unsichtbare Texte für Screenreader sind absolut positioniert; die Scrollbereiche sind ihr Bezugsrahmen, sonst ragten sie hinaus und das Fenster scrollte.
- **Inhalt:** links eine Karte mit Leiste (Zeichen, „vom VLM gelesen“, Kopieren, Herunterladen) und dem Text, rechts die Fakten und Codes auf der Innenfläche; der JSON-Baum und sein Abruf sind entfernt.
- **Seiten:** `PageViewer` für PDF und Seitenbilder: die Seiten in umbrechenden Reihen, 100 % ist die gedruckte Größe. Reine Funktionen in `lib/viewer/zoom.ts` (Aufteilungen, Grenzen, Anker unter dem Zeiger, Reihe der Seitenanzeige „Seite 1–2 von 3“) und `lib/viewer/gestures.ts` (Mausrad mit Strg/⌘, Safaris Gesten, zwei Finger). Während der Zoom läuft, werden die Seiten nur skaliert; ruht er, zeichnet PDF.js die sichtbaren neu, daneben und dann auf einmal kopiert, damit keine Seite leer steht. Doppelklick vergrößert eine Seite und zurück.
- **Metadaten:** Port `MetadataReader`, Adapter `adapters/outbound/pikepdf` (PDF-Infos, XMP, PDF/A; Bilder mit Pillow, EXIF ohne die Angaben zur Speicherung der Pixel), Fake und Vertragstest; `GET /documents/{id}/files` (Leserecht) mit Größe und SHA-256 von Original und Archiv. Der Pfad heißt `files`, nicht `metadata`, weil „Metadaten“ in Papiq sonst Titel, Kontakt, Typ und Felder sind.

**Prüfung:**
- Vitest für Zoom und Gesten, Komponententests für Inhalt und Metadaten; Backend: Adapter an einem erzeugten PDF/A mit XMP, verschlüsselt, an einem JPEG mit EXIF und am TIFF; Endpunkt mit Leserecht.
- Browser gegen den Build mit synthetischen Dokumenten: am Desktop scrollt das Fenster in keinem Tab (1400×900 und 1100×760), am Handy die Seite ohne seitlichen Überstand; Strg + Mausrad hält bei der Übersicht, beim Hineinzoomen wird „2 Seiten“ markiert; Doppelklick, Zwei-Finger-Pinch, Sprung zu Codes; hell, dunkel, ohne Konsolenfehler.

**Offen:**
- Die Daten der PDF-Infos stehen roh da (`D:2026…`), wie die Datei sie hält.

### Zwischenschritt: UI-Verbesserungen – eine Ansicht für Dokumente und Suche, Zahlen an den Stammdaten, Zeiträume in ISO

**Anlass (Tobi, 16.10.2026):** Zehn Punkte aus dem Test der Oberfläche: Die Stammdaten zeigten nicht, wie viele Dokumente sie haben; Felder ließen sich nur vom Feld aus Typen zuordnen, mit einer langen Liste von Schaltern; das Titelmuster hatte keinen Hinweis; der Fortschritt war eine Textzeile; der Zeitraum hatte nur ein Monatsfeld; ein Zitat mit vielen Fundstellen brach Buchstabe für Buchstabe um („S. 9 S. 9“); die Hinweisbox für das VLM war kaum zu sehen; Dokumente und Suche waren zwei Seiten mit denselben Filtern, ohne Sortierung; Dokumente gab es nur als Liste.

**Entscheidungen (Tobi, 16.10.2026):**
- **Bereich „Alle Nutzer“:** eine Sitzungseinstellung im Benutzermenü, nur für Admins, im Browser gemerkt. Sie gilt für Dokumente, Suche, Posteingang mit seiner Zahl, Regeln und die Zahlen an den Stammdaten. `all=1` verschwindet aus den URLs; die API behält `all_users` und prüft wie bisher.
- **Zahlen an den Stammdaten:** auf Wunsch über `?document_counts=true` an den vier Listen. Es zählen die Dokumente im Bereich, bei Feldern nur die mit Wert. Die Zahl ist ein Link zur gefilterten Ansicht.
- **Zuordnung Feld ↔ Typ:** eine Liste mit Suche zum Hinzufügen, in beiden Dialogen. Der Typ-Dialog speichert über einen eigenen Endpunkt; globale Felder stehen dort nur zur Info.
- **Titelmuster:** ein kurzer Hinweis.
- **Fortschritt:** Abschnitte je Knoten, im Kopf und klein in Raster, Liste und Posteingang.
- **Zeitraum nach ISO 8601, erweitert um `JJJJ-Qn`:** im Eingabefeld, in den Anzeigen von Zeiträumen und in Templates. Die deutsche Schreibweise ist nicht genormt („KW 38“ zählt nur die ISO-Woche). „Die ISO reicht erstmal nur für das Zeitraum Feld und die Templates“: Einfache Datumsangaben bleiben.
- **Fundstellen:** ein Chip je Seite, das Zitat in eigener Zeile, ab sieben Seiten „+N“.
- **Hinweisboxen:** Varianten der vorhandenen Alert-Komponente mit den gebräuchlichen Namen und Farben: `note` (blau), `tip` (grün, Glühbirne), `warning` (gelb), `caution` (rot). Die VLM-Empfehlung wird `tip`.
- **Dokumente und Suche:** eine Ansicht unter `/documents`, `/search` entfällt ganz, ohne Umleitung (0.2.0 ist noch nicht veröffentlicht). Sortierung über `?sort=`, je für Liste und Suche im Browser gemerkt: Suche nach Relevanz, Liste nach Dokumentdatum absteigend, Dokumente ohne Datum am Ende.
- **Darstellung:** Raster oder Liste, Raster als Vorgabe, gemerkt.

**Umsetzung (Branch `feature/ui-improvements-0.2.0`, von `release/0.2.0`):**
- **Bereich:** `lib/scope.svelte.ts` hält die Wahl (`localStorage`, abgesichert) und gilt nur, solange der Nutzer Admin ist; der Schalter steht im Benutzermenü, ein Abzeichen am Avatar zeigt ihn an. `AllUsersToggle` und `all` in den Filtern sind entfernt.
- **Sortierung:** `DocumentOrder` (Schlüssel und Richtung) im Port; die Liste blättert weiter mit der ID des letzten Dokuments, der Service liest dessen Wert des Schlüssels nach (fehlt das Dokument, 422). Revision 0023: `title_key` (NFKD ohne Akzente, casefold) mit den Indizes für Datum und Titel; SQLite verträgt dafür kein Batch-Umschreiben der Tabelle, es löschte abhängige Zeilen. In Meilisearch steht `sort` vor den übrigen Ranking-Regeln; sortierbar sind Dokumentdatum, Eingang und Titelschlüssel.
- **Feldfilter:** `field_id` an Liste und Suche (Dokumente mit Wert für das Feld); der Index hält die IDs der Felder mit Wert. Einträge des älteren Aufbaus (Layout 1) schreibt der Abgleich neu, mit ihren Vektoren.
- **Eine Ansicht:** `routes/(app)/documents` mit eigenem Suchfeld (× beendet die Suche), Filtern, Sortierung und dem Umschalter Raster/Liste (`lib/listing.ts`); `DocumentCard` zeigt die erste Seite. Route und Menüpunkt der Suche sind entfernt, das Suchfeld im Kopf führt nach `/documents?q=`.
- **Zahlen:** `DocumentRepository.count_by(CountBy, user)` zählt in einer gruppierten Abfrage; `document_count` an Kontakten, Typen, Tags und Feldern, wenn `document_counts=true`. `DocumentCount` verlinkt auf `/documents?contact=…` (ebenso `type`, `tag`, `field`).
- **Zuordnung:** `MasterDataService.assign_fields` und `PUT /document-types/{id}/fields` (Felder mit Pflicht, in einer Transaktion; ein globales Feld oder eines mit Werten außerhalb, das ein Muster nutzt, wird abgelehnt). `AssignmentList` in Feld- und Typ-Dialog.
- **Hinweisboxen:** Tokens `--note` hell und dunkel; die Alert-Varianten setzen ihr Icon selbst.
- **Fortschritt:** `ProcessingProgress` mit `role="progressbar"`, Tooltip je Abschnitt; im Kopf ein Link ins Protokoll.
- **Fundstellen:** `byPage` fasst die Boxen je Seite zusammen; `FieldShell` zeigt höchstens sechs Chips und „+N“.
- **Zeitraum:** `Period.notation` im Kern schreibt ISO, der Liquid-Renderer und der Suchindex nutzen sie (der Index zusätzlich zu den deutschen Formen). Im Web liest und schreibt `lib/periods.ts`; `PeriodInput` hat statt des Monatsfelds das Feld „Zeitraum“, das Getipptes bis zum Verlassen stehen lässt und meldet, was es nicht lesen kann.

**Prüfung:**
- Vertragstests für Sortierung, Blättern, Zählen und Feldfilter auf Speicher, SQLite und Postgres; Suchindex-Verträge für Feldfilter und Sortierung (Meilisearch, auch hybrid); REST für Sortierung, Zahlen und Typ-Zuordnung; Renderer und Index für Zeiträume.
- Vitest für Bereich, Sortierung und Darstellung, Zuordnungsliste, Fortschritt, Chips und das Lesen und Schreiben von Zeiträumen.
- Im Browser gegen den Build auf einer leeren Instanz mit synthetischen Dokumenten: Bereichsschalter, Suche in der Ansicht, Sortierung mit Zurück-Knopf, Raster und Liste, Zahlen-Links, beide Dialoge, Fortschritt, Hinweisbox, Zeitraum-Eingabe; hell, dunkel, Handybreite, ohne Konsolenfehler.
- Der lokale Smoke-Test `areas.spec.ts` öffnet die Suche jetzt unter `/documents?q=` und wartet nach der Anmeldung auf den Posteingang, die Startseite; grün gegen die Prüfinstanz.

**Offen:**
- Nach dem Merge auf der Devinstanz: Revision 0023, Neustart; der Abgleich schreibt die Index-Einträge neu (Layout 2), ohne Embeddings.
- Neue Titel aus Titelmustern mit Zeitraum sehen anders aus als ältere (`2026-W38` statt „KW 38 2026“).
- Filter nach Zeitraum in Liste und Suche.
- Der lokale Playwright-Test `rules.spec.ts` passt schon vor diesem Schritt nicht mehr zum Regel-Baukasten (Beschriftungen der Bedingungen, Start im Posteingang).

### Release 0.3.0 – Tags mit Arten, Gruppen, Themen, Spaces

**Anlass (Tobi, 16.10.2026):** Tags waren nur Namen, flach, und jeder Tag war ein Ziel der Klassifizierung. Es fehlten eine Struktur um die Kontakte (Gruppen darüber, Themen darunter), eine Hierarchie, die Trennung zwischen Merkmalen, die Papiq ermittelt, und solchen, die nur ein Mensch festlegt, und ein Aussehen (Icons, Farben, Logos). Beispiel: Eine Versicherungsgruppe ist zugleich Arbeitgeberin und Versicherer für Haftpflicht, Krankenversicherung und Lebensversicherung; ihre Dokumente gehören je in eines dieser Themen, und Fakten wie Personal- oder Versicherungsscheinnummer zeigen, in welches.

**Entscheidungen (Tobi, 16.10.2026, in sechs Runden; Ergebnis in `architektur.md`: Datenmodell, „Tags, Gruppen und Themen“, Urteil, Felder, Kontakte, Suche, Regel-Engine, Schnittstellen):**
- **Klassifizierungsmerkmal oder Menschenmerkmal:** ein Schalter `auto`/`manual` an Tags und Feldern; Regeln dürfen manuelle Merkmale setzen. Issue #52 kommt mit: Ein Pflichtfeld „nur von Hand“ hält das Dokument gelb, bis eine Person es setzt.
- **Ein Tag-Modell mit Arten** `document`, `contact` (Kontaktgruppe), `document_type` (Typgruppe), `topic` (Thema). Kontakte und Typen in beliebig vielen Gruppen; Gruppen sind statisch (immer `manual`) und werden vom Dokument über Kontakt und Typ geerbt. Personen-Tags nein (Personen sollen Nutzer werden); Status-Tags sind manuelle Dokument-Tags.
- **Hierarchie:** gespeichert wird nur der gesetzte Tag, Vorfahren sind abgeleitet; Filter und Regelbedingungen treffen Nachfahren mit; keine Zyklen, höchstens fünf Ebenen; Namen eindeutig je Art; Elterntags dürfen gesetzt werden, Kind und Elternteil zugleich wird zum Kind.
- **Thema** (englisch `topic`, verworfen: „Schublade“, das mit dem Ablage-Möbel kollidiert, und „Sachgebiet“): globaler Katalog, am Kontakt freigeschaltet (Variante B; verworfen A: ein Thema je Kontakt, ohne Sicht über Kontakte hinweg). Höchstens ein Thema je Dokument, eines, das sein Kontakt führt. Eine Kennung gehört einem Kontakt und zeigt optional auf eines seiner Themen; das Training gibt einer gelernten Kennung ein Thema nur, wenn alle ihre Dokumente es tragen. Freischalten im Kontaktdialog oder, als Admin, durch die Wahl am Dokument. Urteil: gesetzt nur bei grünem Kontakt und Kennung plus Signal oder zwei Signalen; sonst Vorschlag, nie gelb, ein Widerspruch lässt es leer.
- **Anzeige:** je Tag „am Dokument zeigen“, Vorgabe nach Art; am Dokument das Blatt, der Pfad im Tooltip (Variante a).
- **Aussehen:** Icons aus Lucide (bleibt; eine Auswahl von 170, die übrigen nachgeladen aus dem eigenen Build; Vorschau „Papiq Icon-Auswahl“, Artifact, privat), zwölf Hauptfarben plus eigene Farbe für Tags, Logos für Kontakte per Upload und, auf Wunsch (`PAPIQ_LOGO_FETCH`), von der eigenen Website des Kontakts. Logo-Dienste Dritter nein.
- **Space statt Schublade** in Code, API, Datenbank, Templates und Oberfläche, auf Deutsch und Englisch „Space“ (verworfen: Aktenschrank/Cabinet, Vault). Der Bruch der API ist gewollt; die Version bleibt 0.3.0, der PR trägt `refactor` und nennt den Bruch im Titel. Der Umbau der Rechte kommt später (Tobi steuert ihn ein).
- **Migrationsclient:** nur, was die API erzwingt; ASN, Tag-Hierarchie und Farben aus Paperless in einem späteren Release.
- **Schnitt:** fünf Pakete, je eine Branch von `release/0.3.0` und ein PR mit eigenem Label, in dieser Reihenfolge (3 vor 4, weil Themen das Tag-Modell brauchen). Jede Branch beginnt mit einem Plan im Plan-Modus und endet mit einem Review durch einen separaten Agenten.

| # | Branch | Inhalt | Label | Revision | Modell |
| --- | --- | --- | --- | --- | --- |
| 1 | `refactor/spaces-0.3.0` | Schublade wird Space | `refactor` | 0024 | Sonnet 5.5 · high (Revision im Review) |
| 2 | `feature/manual-fields-0.3.0` | Felder „nur von Hand“ (#52) | `enhancement` | 0025 | Sonnet 5.5 · medium |
| 3 | `feature/nested-tags-0.3.0` | Tag-Modell mit Arten, Hierarchie, Gruppen, auto/manuell, Anzeige | `new-feature` | 0026 | Opus 5.5 · high |
| 4 | `feature/topics-0.3.0` | Themen an Kontakt, Dokument und Kennung, im Urteil | `new-feature` | 0027 | Opus 5.5 · high |
| 5 | `feature/master-data-looks-0.3.0` | Icons, Farben, Logos | `new-feature` | 0028 | Sonnet 5.5 · high (Schutz vor SSRF im Review mit Fable 5.1 · high) |
| 6 | `feature/tags-menu-0.3.0` | Ein Tags-Reiter, Anlegen aus den Auswahlen, Stammdaten am Handy (nachgereicht, Tobi 17.10.2026) | `enhancement` | – | Opus 5.5 |
| 7 | `feature/refinements-0.3.0` | Nachbesserungen aus Tobis Antworten auf die offenen Punkte (nachgereicht, Tobi 17.10.2026) | `enhancement` | – | Opus 5.5 |

#### Paket 1 – Space statt Schublade (`refactor/spaces-0.3.0`)

PR mit Label `refactor`, der Bruch im Titel, etwa „Rename drawers to spaces (breaking: /api/v1/spaces, space_id, {{ space }})“. Kein Verhalten ändert sich; der Name `space` ersetzt `drawer` überall, auch in gespeicherten Daten. Bestand vor dem Umbau: 197 Dateien nennen „drawer“ oder „Schublade“ (Backend, Web, Migrationsclient, Doku).

**Umsetzung:**
- **Kern:** `domain/drawers.py` wird `domain/spaces.py` (`Space`, `SpaceId`, `ShareLevel` bleibt, `DEFAULT_SPACE_NAME` behält den Wert „Default“); `Document.space_id` und `move_to`; `DocumentFiled.space_id` (der Ereignistyp `document.filed` bleibt); `permissions.py` (`space_access`, `reach_space_access`, `can_file_into`, `can_manage_space`); Regeln `SetSpace` mit `type: "set_space"`, `References.spaces`, im Regelwerk `SPACE = "space"`, `SpaceTarget`, `RulePlan.space`; Pipeline `PERSON_SPACE = "person:space"`, `CHOSEN_SPACE = "space_id"`; Template-Variable `space` statt `drawer`; Suche `IndexDocument.space_id`, `Visibility.spaces`; Port `SpaceRepository`, `DocumentFilter.space`, `exists(space=)`; `SpaceService`; alle Meldungen („no write access to space“ …).
- **Revision 0024 (`v0024_spaces`):** Tabellen `drawers` → `spaces`, `drawer_shares` → `space_shares`, Spalten `documents.drawer_id` und `space_shares.drawer_id` → `space_id`; Schlüssel und Indizes nach der Namenskonvention (`pk_spaces`, `uq_spaces_default_owner_id`, `ix_documents_space_id` …). Postgres benennt in place um (`ALTER TABLE … RENAME`, `RENAME CONSTRAINT`, `ALTER INDEX … RENAME`). Unter SQLite fiele das durch den Abgleich-Test (er vergleicht den Wortlaut von `CREATE TABLE` samt Schlüsselnamen, ein Umbenennen setzt den Namen in Anführungszeichen und behält die alten Schlüssel): Dort werden `spaces` und `space_shares` neu angelegt und befüllt und `documents` über eine temporäre Kopie neu aufgebaut, ohne Umbenennen, mit ausgeschalteten Fremdschlüsseln (`migrate`), sodass abhängige Zeilen bleiben (geändert beim Plan der Branch, Tobi: „Baue es um wie Du brauchst“). Dieselbe Revision schreibt die gespeicherten Daten um:
  - `rule_versions.actions`: `{"type": "set_drawer", "drawer_id"}` → `{"type": "set_space", "space_id"}`;
  - Templates in Regeln (Titel, Textfelder, Gründe) und `document_types.title_pattern`: die Variable `drawer` → `space`, nur in Ausdrücken (`{{ … }}`, `{% … %}`) und als ganzes Wort, über den Lexer von python-liquid, nie im übrigen Text;
  - `outbox.payload` von `document.filed`: `drawer_id` → `space_id`;
  - `processing_log`: `model_version` `person:drawer` → `person:space`, der Schlüssel `drawer_id` in Ein- und Ausgabe, das Feld `drawer` in geänderten Feldern, Prüfungen und offenen Feldern → `space`; der Plan der Branch prüft, ob es auch in `documents.processing_nodes` steht.
  - Die Rückrichtung stellt alles wieder her.
- **API:** `/api/v1/spaces` (Tag `spaces`) mit denselben Routen; `SpaceCreate`, `SpaceOut`; `space_id` in `DocumentDetails`, `MoveRequest`, `VisibilityOut` (`space_owner_id`), `ConfirmRequest`, im Upload-Feld und als Filter von Liste und Suche; Regelaktion `set_space`; MCP `DocumentOut.space_id`. Die Web-UI erkennt den Fehler „kein Schreibrecht“ heute am Text (`web/src/lib/rules/errors.ts:74`, `/drawer/`): Das Problem-Detail bekommt einen festen Code, die UI prüft ihn statt des Texts.
- **Suche:** Feld `space_id` im Index, `LAYOUT = 3`; der Abgleich schreibt die Einträge neu und behält die Vektoren.
- **Web:** Route `/spaces`, Menüpunkt, `SpaceShares.svelte`, `lib/permissions.ts`, `lib/masterdata.svelte.ts`, `lib/filters.ts` (`?space=`), Upload, Regel-Baukasten (`set_space`), Prüf- und Template-Werte; 22 Schlüssel je Sprache umbenannt (`nav_spaces`, `space_new` …), drei weitere Texte angepasst; deutsch „Space“, „Spaces“, „Standard-Space“, „Mit mir geteilt“.
- **Migrationsclient:** `/spaces`, `space_id`, Berichtsart `space`; Tests und Fake.
- **Doku:** `README.md` (Glossar), `backend/README.md` (Knoten, Endpunkte, `set_space`, Template-Variablen, `person:space`), `web/README.md`, `migration/README.md`, Bewertungsfälle 27 und 28.
- **Tests:** umbenannt mit (`test_users_and_spaces.py`, Builder `space()`, `default_space()`, `in_space`); Unit-of-Work-, Suchindex-, Regel- und Ereignis-Verträge.

**Prüfung:**
- Alle Prüfungen in `backend/`, `web/` und `migration/`; `openapi.json` und `schema.ts` neu erzeugt.
- Revision 0024 hin und zurück auf SQLite und Postgres mit Daten: Regel mit `set_drawer` und `{{ drawer }}` im Titel und Grund, Titelmuster mit `{{ drawer }}`, ein Wort „drawer“ im Text des Templates (bleibt), ein offenes Ereignis `document.filed`, Protokoll mit `person:drawer`.
- Nach dem Umbau nennt nur noch Geschichte „drawer“: frühere Revisionen, diese Revision, `.idea/` (Suche als Test im Review).
- Browser gegen den Build: Spaces anlegen, teilen, umbenennen, löschen; Hochladen in einen Space; Verschieben; Regel mit „Space setzen“ und Fehler „kein Schreibrecht“; hell, dunkel, Handybreite, ohne Konsolenfehler.

**Nach dem Merge:** Devinstanz: Revision 0024, Neustart (der Abgleich schreibt den Index neu, ohne Embeddings).

**Umsetzung (Branch `refactor/spaces-0.3.0`, 16.10.2026, autonom über Nacht auf Tobis Wunsch):**
- Commits: gemeinsame `downgrade()` für Tests (Fremdschlüssel aus wie bei `migrate`, sonst nähme die Rückrichtung eines Neuaufbaus abhängige Zeilen mit); der Umbau selbst in einem Commit (Backend, Revision 0024, API, Web, Migrationsclient), weil Feldnamen wie `space` auch Werte der API sind (offene Felder, Prüfungen) und ein grüner Zwischenstand Übersetzungscode zum Wegwerfen gebraucht hätte; `SpaceWriteDeniedError` mit `space_id` im Problem-Detail statt eines Fehlercodes (wie `existing_document_id`; die Web-UI liest das Feld statt des Texts); README; Layout 3 des Suchindex (im Review gefunden: ohne die Erhöhung hätte der Abgleich die alten Einträge mit `drawer_id` behalten und geteilte Dokumente wären aus der Suche anderer gefallen).
- Templates: Die Variable wird über die Analyse von python-liquid umbenannt (Fundstellen), Text, Zeichenketten, `{% raw %}` und `field.drawer` bleiben; ein Template, das `space` schon anders nutzt, hält die Revision an. Die Bewertungsfälle 27 und 28 schleusen nun `space` ein.
- Migrationsclient: alte Zustandsdateien werden beim Öffnen umgeschrieben (`kind`, Schlüssel `drawer` in den Details).
- Prüfung: alle Prüfungen grün (3007 Backend-Tests mit Postgres, 532 Vitest, Build, Migrationsclient 80); Revision 0024 hin und zurück mit Daten auf SQLite und Postgres, Schema nach der Rückrichtung gleich dem von 0023; Browser gegen den Build auf einer leeren Prüfinstanz (Port 8001): Spaces anlegen, löschen, Filter `?space=`, Dokument im Space, hell, dunkel, Handybreite, Englisch, keine Konsolenfehler außer der erwarteten 401 vor der Anmeldung. Review durch einen separaten Agenten: Layout behoben, „des Spaces“.
- Offen für Tobi: Alte Clients, die `drawer_id` beim Upload senden, landen still im Standard-Space (`?drawer_id=` filtert nicht mehr); ein 422 für eine Version wäre möglich. Der Review schlug `breaking-change` als Label vor; nach Tobis Entscheidung bleibt `refactor` mit dem Bruch im Titel.

#### Paket 2 – Felder „nur von Hand“ (`feature/manual-fields-0.3.0`, Issue #52)

PR mit Label `enhancement`, schließt #52.

**Umsetzung:**
- **Kern:** `Assignment` (`auto`, `manual`) als gemeinsamer Begriff für Felder und später Tags (`domain/master_data.py`); `FieldDefinition.assignment`, Vorgabe `auto`.
- **Revision 0025:** `field_definitions.assignment` (Text, nicht leer, Vorgabe `auto`).
- **`signal_llm`:** fragt nur Felder mit `auto` an (`nodes.py`, bevor die Felder je Typ gruppiert werden); der Prompt bleibt `propose-2`, nur die Liste ändert sich.
- **`fields`:** fragt manuelle Felder nie an (auch nicht mit `extract-4` nach einer Korrektur des Typs) und lässt ihren Wert, wie er ist. Ein optionales manuelles Feld ist immer in Ordnung. Ein Pflichtfeld ohne Wert ist eine unsichere Prüfung mit dem Grund „is set by hand“, ohne Vorschlag: Das Dokument wird gelb; die Person setzt den Wert in der Prüfansicht (`decide` nimmt ihn wie jeden anderen). Hat das Dokument schon einen Wert (von einer Person, einer Regel oder der Übernahme), ist die Prüfung in Ordnung.
- **Regeln:** setzen manuelle Felder wie bisher.
- **API:** `assignment` in `FieldCreate`, `FieldPatch`, `FieldOut`.
- **Web:** Schalter „Nur von Hand“ im Feld-Dialog mit Hinweis („Papiq fragt das Modell nie nach diesem Feld“), ein Abzeichen in der Feldliste; in der Prüfansicht der Grund statt eines Zitats.
- **Doku:** `backend/README.md` (Felder), Bewertungsfall mit einem manuellen Pflichtfeld.

**Prüfung:**
- Unit: Signal und Felder-Knoten ohne manuelle Felder im Prompt (die abgelegte Anfrage enthält sie nicht), Pflicht ohne Wert gelb, mit Wert grün, optional immer grün; Vertragstest der Feldspalte auf Speicher, SQLite und Postgres; Revision hin und zurück.
- Evaluation mit festen Antworten: wie auf `release/0.3.0`, dazu der neue Fall (gelb mit Grund, das Modell nie gefragt).
- Browser: Feld anlegen und umschalten, Dokument mit manuellem Pflichtfeld in der Prüfansicht setzen und bestätigen.

**Nach dem Merge:** Devinstanz: Revision 0025.

**Umsetzung (Branch `feature/manual-fields-0.3.0`, 16.10.2026, autonom):**
- `Assignment` (`auto`, `manual`) in `domain/master_data.py`, `FieldDefinition.assignment` und `is_read`, Revision 0025, `assignment` an `FieldCreate`/`FieldPatch`/`FieldOut`; Schalter „Nur von Hand“ im Feld-Dialog, Hinweis in der Feldliste.
- `signal_llm` und `fields` fragen manuelle Felder nie an (auch nicht mit `extract-4` nach einer Typkorrektur) und lassen ihren Wert, wie er ist.
- Abweichung vom Plan (im Review gefunden): Ob ein Pflichtfeld „nur von Hand“ einen Wert hat, prüft `apply_rules` nach den Regeln, nicht der Felder-Knoten; sonst blieb ein Dokument gelb, dessen Wert eine Regel im selben Lauf setzte. Bestätigen ohne Wert ist für ein solches Feld keine Entscheidung (422 `open_fields`). Bei einer Typkorrektur behalten Formular und Bewertung den von Hand eingetragenen Wert (er wird nie neu gelesen). Die Prüfkarte sagt „Diesen Wert trägst Du von Hand ein“ statt „Keine Fundstelle im Text“.
- Bewertung: Typ „Quittung“ mit dem Pflichtfeld „Ablagenummer“ (nur von Hand), Fall 38; der Bericht wertet solche Felder nicht als Leistung des Modells. Die Bewertung führt den Regelknoten nicht aus, Fall 38 ist kalt daher grün (das Gelb prüfen Pipeline-Tests), trainiert gelb wegen des Typs.
- Prüfung: 3014 Backend-Tests mit Postgres, 534 Vitest, Build; Bewertung mit festen Antworten ohne falsches Grün und ohne Verstöße; Browser gegen den Build (Feld anlegen, Dialog, Prüfkarte mit Eingabe, hell und dunkel). Review durch einen separaten Agenten, die Funde oben behoben.
- Offen: Die ASN aus Paperless als Feld „nur von Hand“ kommt mit dem Migrationsclient in einem späteren Release.

#### Paket 3 – Tags mit Arten, Hierarchie und Gruppen (`feature/nested-tags-0.3.0`)

PR mit Label `new-feature`. Die Art `topic` kommt erst mit Paket 4.

**Umsetzung:**
- **Kern:**
  - `TagScope` (`document`, `contact`, `document_type`); `Tag.scope`, `Tag.parent_id`, `Tag.assignment` (Gruppen immer `manual`), `Tag.shown` (Vorgabe: Gruppen aus, Dokument-Tags an).
  - `domain/tag_tree.py`, rein: `TagTree` mit `ancestors`, `descendants`, `path`, `check_parent` (gleiche Art, kein Zyklus, mit dem Teilbaum höchstens fünf Ebenen) und `normalise` (streicht Vorfahren anderer gesetzter Tags).
  - `Contact.group_ids` (Art `contact`), `DocumentType.group_ids` (Art `document_type`); am Dokument nur Tags der Art `document`, beim Setzen normalisiert.
  - `MasterDataService`: Anlegen und Ändern mit Art, Elterntag, Ermittlung und Anzeige; die Art eines Tags ist fest. Löschen nur ohne Kinder und ohne Gebrauch (Dokumente, Kontakte, Typen, Regeln). Neuer Elterntag, geänderte Gruppen an Kontakt oder Typ und Umbenennung reihen `search.refresh` für die betroffenen Dokumente ein.
  - Filter: Der Kern erweitert jeden gewünschten Tag um seine Nachfahren und gibt dem Port Mengen („eines davon“, je Menge UND); ebenso für Kontakt- und Typgruppen über Kontakt und Typ des Dokuments. So braucht weder SQLite noch Postgres eine rekursive Abfrage; die Bäume sind klein. Zahlen an Elterntags zählen jedes Dokument einmal, das den Tag oder einen Nachfahren trägt.
- **Revision 0026:** `tags.scope` (Vorgabe `document`), `tags.parent_id` (Fremdschlüssel auf `tags`, Index), `tags.assignment` (Vorgabe `auto`), `tags.shown` (Vorgabe wahr); eindeutig `(scope, name_key)` statt `name_key`; Tabellen `contact_groups` (Kontakt mit CASCADE, Tag) und `document_type_groups` (Typ mit CASCADE, Tag). Risiko: `uq_tags_name_key` steht unter SQLite im `CREATE TABLE`; sie zu ändern verlangt ein Neuanlegen der Tabelle, das mit eingeschalteten Fremdschlüsseln abhängige Zeilen verlieren kann. Der Plan der Branch wählt zwischen dem Verfahren von SQLite (Fremdschlüssel aus, neu anlegen, `foreign_key_check`) und einem Schlüssel, der die Art enthält; ein Test belegt, dass `document_tags` vollständig bleibt.
- **Klassifizierung:** Ziele sind nur Tags der Art `document` mit `auto`: `signal_llm` listet nur sie (der Prompt bleibt `propose-2`), der Klassifikator trainiert nur sie, die Nachbarn stimmen nur über sie ab, das Urteil schlägt keine manuellen vor und normalisiert, was es hinzufügt. Behoben nebenbei: Vorschläge des Urteils für Tags sind Namen, `inbox._suggested` liest sie als IDs (`judge_tags` liefert künftig IDs).
- **Bestätigung (Vorschlag, nicht besprochen):** Eine Änderung nur an manuellen Tags bestätigt das Dokument nicht; ein Status-Tag sagt nichts über Kontakt und Typ. Bleibt es bei der heutigen Regel, bestätigt jede Änderung an Tags.
- **Regeln:** Bedingungen auf Tags treffen Nachfahren mit; neue Felder `contact_group` und `document_type_group` (enthält, eines von, vorhanden, fehlt) über die Gruppen von Kontakt und Typ samt Vorfahren, unter Misstrauen „unbekannt“, wenn Kontakt oder Typ vom Modell und unbestätigt sind. „Tags hinzufügen/entfernen“ nur mit Tags der Art `document`, manuelle eingeschlossen.
- **Templates:** `contact_groups`, `document_type_groups` (die zugeordneten Namen, sortiert); `tags` bleiben die gesetzten.
- **Suche:** Der Eintrag hält Tags mit Vorfahren (IDs und Namen) und die Gruppen von Kontakt und Typ mit Vorfahren; der Filter in Meilisearch braucht daher keine Erweiterung. `LAYOUT = 4`.
- **API:** `TagOut` (Art, Elterntag, Pfad, Ermittlung, Anzeige, Zahl), `TagCreate` (Art mit Vorgabe `document`), `TagPatch`; `GET /tags?scope=`; `group_ids` an Kontakten und Typen; Filter `contact_group_id` und `document_type_group_id` an Liste und Suche (wiederholbar wie `tag_id`). MCP: `list_tags` mit Art und Pfad, `search` und `update_metadata` mit Dokument-Tags.
- **Web:**
  - Stammdaten: der Reiter Tags wird drei Reiter (Dokument-Tags, Kontaktgruppen, Typgruppen) mit Baum (ein- und ausklappen, Zahl je Zeile); Dialog mit Elterntag (gleiche Art, ohne eigene Nachfahren), „Ermittlung: automatisch / nur von Hand“ (nicht bei Gruppen) und „am Dokument zeigen“.
  - Kontakt- und Typ-Dialog: Gruppen auswählen.
  - `TagsInput`: Auswahl mit Pfaden, die Suche trifft auch Elternnamen; Chips zeigen das Blatt, den Pfad im Tooltip und beim Tippen.
  - Chips auch in Liste, Raster und Kopf des Dokuments (heute zeigt nur das Formular Tags); eingeblendete Gruppen bei Kontakt und Typ.
  - Filter für Gruppen; Regel-Baukasten mit den neuen Feldern; Template-Werte.
- **Migrationsclient:** gleicht Namen nur mit `GET /tags?scope=document` ab (sonst träfe er eine gleichnamige Gruppe).
- **Doku:** `backend/README.md` (Tags, Filter, Regeln, Templates).

**Prüfung:**
- Unit: `TagTree` (Zyklus, Tiefe mit Teilbaum, fremde Art, Normalisieren), Löschen und Neuindizieren im Service, Urteil ohne manuelle Tags, Regelwerk mit Nachfahren, Gruppen und Misstrauen.
- Verträge auf Speicher, SQLite und Postgres: Filter mit Mengen, Gruppen, Zahlen an Elterntags, Eindeutigkeit je Art; Suchindex mit Vorfahren. Revision hin und zurück mit Daten.
- Evaluation mit festen Antworten: unverändert gegenüber Paket 2.
- Vitest: Baum, Auswahl mit Pfaden, Chips. Browser gegen den Build: Bäume anlegen und umhängen, Gruppen an Kontakt und Typ, Filter über einen Elterntag und eine Gruppe, Chips in Liste, Raster und Kopf; hell, dunkel, Handybreite.

**Nach dem Merge:** Devinstanz: Revision 0026, Neustart (Index Layout 4).

**Umsetzung (Branch `feature/nested-tags-0.3.0`, 16./17.10.2026, autonom über Nacht):**
- **Kern:** `TagScope` (`document`, `contact`, `document_type`; `is_group` und die Vorgaben je Art, sodass `topic` als weiterer Wert hinzukommt), `Tag.scope`, `parent_id`, `assignment`, `shown`, `Tag.is_target`; `domain/tag_tree.py` mit `TagTree` (Vorfahren, Nachfahren, Pfad, `check_parent`, `normalise`, gegen Zyklen in gespeicherten Daten gesichert); `Contact.group_ids`, `DocumentType.group_ids` (höchstens 100). Dokument-Tags prüft und normalisiert eine Stelle (`_access.checked_changes`: Änderung durch eine Person, Urteil, Prüfansicht, Übernahme); die Regeln normalisieren ihr Ergebnis selbst. Auch die Gruppen eines Kontakts oder Typs werden normalisiert (wie am Dokument; Auslegung von „Kind und Elternteil zugleich wird zum Kind“).
- **Revision 0026:** unter SQLite `tags` über eine temporäre Kopie neu aufgebaut wie `documents` in 0024 (Fremdschlüssel aus, Zahl der Zeilen geprüft), unter Postgres in place; eindeutig `uq_tags_scope_name_key`; `contact_groups` und `document_type_groups` mit Index auf `tag_id`. Test mit Daten auf SQLite und Postgres: `document_tags` vollständig, gleichnamige Kontaktgruppe erlaubt, doppelter Name je Art abgewiesen, nach der Rückrichtung das Schema von 0025. Die Rückrichtung löscht Gruppen und Verschachtelung; Regeln mit Gruppenbedingungen bleiben, wie sie sind (wie 0020 mit Zeiträumen).
- **Service:** Anlegen und Ändern mit Art, Elterntag, Ermittlung und Anzeige; Löschen nur ohne Kinder und ohne Gebrauch durch Dokumente, Kontakte, Typen und Regeln (409 mit den Namen der Regeln). Für Tags heißt das: Regeln werden nicht mehr abgeschaltet, das Löschen wird abgelehnt (wie entschieden; Kontakte, Typen, Felder und Spaces schalten Regeln weiter ab, Doku in `rules/references.py`). `search.refresh` nur, wenn sich Name, Elterntag oder Gruppen wirklich ändern; der Job nimmt die Dokumente mit Nachfahren und die der Kontakte und Typen in der Gruppe oder darunter.
- **Filter und Zahlen:** `DocumentFilter.tags`, `contact_groups`, `document_type_groups` als Mengen („eines davon“, je Menge UND); der Kern erweitert um die Nachfahren (`services/_tags.py`); SQL mit `IN`-Unterabfragen, Gruppen über `contact_groups`/`document_type_groups`; Meilisearch `tag_ids IN [...]`; die Suche prüft Gruppen gegen die Datenbank nach. Zahlen an Elterntags über den neuen Port `count_tagged` (eine Abfrage mit `count(DISTINCT CASE …)`, je 200 Tags), an Gruppen aus den Zahlen je Kontakt und Typ.
- **Suche:** Layout 4 mit Tags samt Vorfahren und `contact_group_ids`/`contact_groups`, `document_type_group_ids`/`document_type_groups` (filterbar, durchsuchbar). Der Kopf des ersten Abschnitts nennt weiter nur die gesetzten Tags: Ein Umhängen kostet so keine Embeddings, und der Abgleich auf Layout 4 bettet nichts neu ein (Entscheidung beim Bau, die zurückhaltendere).
- **Klassifizierung:** nur automatische Dokument-Tags in der Liste des Modells (Prompt bleibt `propose-2`), im Training, in den Stimmen der Nachbarn (auch im Protokoll) und im Urteil; das Urteil normalisiert, was es setzt, und schlägt IDs vor (behoben, die Prüfansicht las sie schon als IDs); die neuen Namen des Modells stehen nur noch in `proposed`. Die Bestätigung bleibt, wie sie ist.
- **Regeln:** Tag-Bedingungen treffen Nachfahren; `contact_group` und `document_type_group` (enthält, eines von, vorhanden, fehlt) über die Gruppen mit Vorfahren, unter Misstrauen unbekannt, solange Kontakt oder Typ vom Modell und unbestätigt sind (`Taxonomy` in den Fakten). Beim Speichern wird die Art jedes genannten Tags geprüft (422); „Tags hinzufügen/entfernen“ nur mit Dokument-Tags, ein Elterntag eines bleibenden oder hinzukommenden Tags wird nicht gesetzt (Notiz `skipped`). Templates: `contact_groups`, `document_type_groups`.
- **API und MCP:** `TagOut` (mit Pfad), `TagCreate`, `TagPatch`, `GET /tags?scope=` in der Reihenfolge der Bäume, `group_ids` an Kontakten und Typen, Filter `contact_group_id` und `document_type_group_id`; MCP `list_tags` mit Art und Pfad (optional eine Art), `search` und `update_metadata` nennen nur Dokument-Tags.
- **Web:** drei Reiter mit Baum (`TagTreeList`: ein- und ausklappen, Zahl je Zeile als Link, am Handy unter dem Namen); Tag-Dialog mit Elterntag (gleiche Art, ohne sich und seine Nachfahren), Ermittlung (nicht bei Gruppen) und Anzeige; Gruppen im Kontakt- und Typ-Dialog; Filter `?contact_group=`, `?type_group=`; `TagsInput` als Suche über den Pfad (Elterntags eines gesetzten Tags bietet sie nicht an); Chips (`TagChip`, `TagChips`, `GroupChips`) in Liste, Raster, Kopf und unter Kontakt und Typ, der Pfad als Tooltip und im Kopf und Formular beim Tippen. Gezeigt wird je Tag der nächste eingeblendete auf dem Weg nach oben: Ein ausgeblendeter Tag zeigt seinen eingeblendeten Elterntag (Auslegung von „nur Tags mit `shown`“). Regel-Baukasten mit den neuen Feldern und Tags mit Pfad, Template-Werte.
- **Migrationsclient:** `GET /tags?scope=document`; der Fake kennt Arten, ein Test mit gleichnamiger Kontaktgruppe.
- **Doku:** `backend/README.md` (Abschnitt Tags, Signale und Urteil, Regeln, Templates, Suche, Endpunkte, MCP), `migration/README.md`.
- **Prüfung:** alle Prüfungen grün: 3060 Backend-Tests mit Postgres, Garage und Meilisearch, 552 Vitest, Build, Migrationsclient 81; Bewertung mit festen Antworten unverändert (75 Fälle, kein falsches Grün, keine Verstöße, Bericht bis auf die Uhrzeit gleich). Browser gegen den Build auf einer leeren Prüfinstanz (8001): Bäume in den drei Reitern, Tag anlegen und umhängen, ein- und ausklappen, Gruppen an Kontakt und Typ, Filter über einen Elterntag und eine Gruppe (auch über die Filterleiste), Chips in Liste, Raster und Kopf mit dem Pfad als Tooltip und beim Tippen, Tag-Eingabe mit Suche über den Elternnamen, Regel mit Gruppenbedingung; Deutsch und Englisch, hell und dunkel, Handybreite; keine Konsolenfehler außer der 401 vor der Anmeldung, keine CSP-Verstöße. Dabei gefunden und behoben: Zahl und Einzug der Baumzeilen am Handy, Breite der Gruppenfilter, Name des Knopfs „Tag hinzufügen“, Gruppenbedingungen im Plural („Kontaktgruppen enthalten …“).
- **Nach dem Review (separater Agent):** (1) Zwei gleichzeitige Umhängungen (A unter B, B unter A) bestanden je die Prüfung und speicherten einen Zyklus; Anlegen und Ändern eines Tags nehmen nun die Sperre der Unit of Work (`lock("tags")`: unter SQLite die Schreibsperre, unter Postgres ein Advisory Lock), bevor sie den Baum lesen; Test auf Speicher, SQLite und Postgres (ohne die Sperre schlägt er unter Postgres fehl). Tags, zu denen kein oberster Tag führt (so beschädigte Daten), zeigen `TagTree.of_scope` und der Baum der Web-UI am Ende wie oberste, damit man sie reparieren kann. (2) `verify` des Migrationsclients vergleicht Tags samt Vorfahren (Papiq behält von Kind und Elternteil das Kind). (3) Eigene Texte für Gruppen („Keine (oberste Ebene)“, Löschhinweise), der englische Hinweis zur Ermittlung umformuliert, 404 für eine unbekannte Gruppe beim Anlegen von Kontakt und Typ dokumentiert, das Tag-Feld behält seine `id`, auch ohne Knopf. (4) Gruppen stehen in Raster und Liste als umrandete Chips neben ihrem Kontakt und Typ, die Tags in eigener Zeile; der Kopf des Dokuments zeigt nur die Tags (die Gruppen stehen im Formular unter Kontakt und Typ). Prüfung danach: 3063 Backend-Tests, 556 Vitest, Build, Migrationsclient 82; Browser wie oben erneut (Deutsch, Englisch, hell, dunkel, Handybreite), ohne Konsolenfehler und CSP-Verstöße.
- **Offen für Tobi:** (1) Bestätigung: Soll eine Änderung nur an manuellen Tags das Dokument nicht bestätigen? Heute bestätigt jede Änderung an Tags (unverändert gelassen). (2) Klassifikator und Nachbarn lernen und stimmen über die gesetzten Tags, nicht über deren Vorfahren. (3) Ein Umhängen normalisiert vorhandene Dokumente nicht nach (eines kann danach Kind und Elternteil tragen, bis jemand es ändert). (4) Die Kopfleiste der App läuft am Handy um 8 px über, schon auf `release/0.3.0` (AppShell nicht angefasst). (5) Die Rückrichtung von 0026 lässt Regeln mit Gruppenbedingungen stehen.

#### Paket 4 – Themen (`feature/topics-0.3.0`)

PR mit Label `new-feature`.

**Umsetzung:**
- **Kern:**
  - `TagScope.TOPIC`.
  - `Contact.topic_ids` (die freigeschalteten Themen); `Identifier.topic_id`, die Regel „Thema der Kennung ∈ Themen des Kontakts“ im Kontakt.
  - Freischaltung entfernen: abgelehnt, solange Dokumente des Kontakts das Thema tragen (Service); Kennungen verlieren das Thema.
  - `Document.topic_id`: nur ein Thema, das der Kontakt führt (Service). Wechselt der Kontakt, bleibt es, wenn der neue es führt, sonst fällt es weg. Wählt ein Admin ein Thema, das der Kontakt nicht führt, wird es in derselben Transaktion freigeschaltet; andere bekommen 422.
  - `topic_id` gehört zu `CONFIRMING` und `CLASSIFIER_LABELS`.
- **Kennungen:** `mine_identifiers` bekommt je bestätigtem Dokument Kontakt, Thema und Kennungen; eine gelernte Kennung bekommt das Thema, wenn alle ihre Dokumente es tragen, sonst keines. `facts_in` liefert zur Kennung das Thema, `FactsView` die Paare aus Kontakt und Thema.
- **Revision 0027:** `contact_topics` (Kontakt mit CASCADE, Tag), `contact_identifiers.topic_id` (leer erlaubt), `documents.topic_id` (Fremdschlüssel, Index).
- **Signale und Urteil:**
  - `signal_llm` mit Prompt `propose-3`: zu den vorausgewählten Kontakten ihre Themen, die Antwort nennt ein Thema aus dieser Liste.
  - Klassifikator: ein Modell über Themen wie beim Typ (mindestens zwei Dokumente je Klasse); das Urteil wertet nur die Themen des entschiedenen Kontakts.
  - Nachbarn: Stimmen für das Thema; `KnownLabels` mit Themen.
  - Fakten: eine Kennung mit Thema ist harter Beweis für das Paar.
  - `judge_topic` nach `judge_contact`: gesetzt, wenn der Kontakt grün ist und (Kennung des Paares und ein Signal) oder zwei Signale das Thema nennen; sonst Vorschlag; verschiedene Themen genannt → leer mit allen Kandidaten und Quellen; nie unsicher. `MetadataResult` setzt es und räumt ein Thema ab, das der neue Kontakt nicht führt.
- **Regeln:** Feld `topic` (ist, eines von, vorhanden, fehlt; mit Nachfahren), Aktion „Thema setzen“ in Nutzer-Regeln wie Kontakt und Typ (führt der Kontakt das Thema nicht, wird es nicht gesetzt und vermerkt); zwei Regeln mit verschiedenen Themen sind ein Konflikt wie bei jedem Einzelfeld; unter Misstrauen „unbekannt“, wenn das Modell das Thema gesetzt hat.
- **Templates:** `topic`.
- **Suche:** Thema mit Vorfahren (ID und Name) im Eintrag und im Kopf des ersten Abschnitts; Filter `topic_id`; `LAYOUT = 5`.
- **API:** `GET /tags?scope=topic`; `topic_ids` an Kontakten; `topic_id` an `IdentifierIn`/`IdentifierOut` und an `POST /contacts/{id}/identifiers`; `topic_id` in `DocumentPatch`, `DocumentDetails`, als Filter von Liste und Suche, in der Prüfansicht (Kandidaten) und in `decide`; MCP `get_document` und `update_metadata` mit Thema (beim Namen).
- **Web:**
  - Stammdaten: Reiter „Themen“ mit Baum.
  - Kontaktdialog: Themen freischalten; im Kennungs-Editor je Zeile das Thema.
  - Fakten: Anheften mit Thema, vorbelegt mit dem Thema des Dokuments.
  - Dokument: Feld „Thema“ mit den Themen des Kontakts (Admins: alle, mit Hinweis „wird am Kontakt freigeschaltet“), Karte in der Prüfansicht mit Kandidaten, eckiger Chip in Liste, Raster und Kopf.
  - Filter, Regel-Baukasten, Template-Werte.
- **Evaluation:** Trainingskorpus mit Themen an zwei Kontakten; neue Fälle: Lohnabrechnung mit Personalnummer → „Arbeit“ grün; Brief desselben Kontakts mit Versicherungsscheinnummer → „Krankenversicherung“; zwei Kennungen verschiedener Themen → leer mit Vorschlägen; gelber Kontakt → Thema nur Vorschlag.
- **Doku:** `backend/README.md` (Themen, Kennungen, Urteil, Prompt `propose-3`).

**Prüfung:**
- Unit: Regeln am Kontakt (Kennung mit fremdem Thema, Freischaltung entfernen), Themenwechsel beim Kontaktwechsel, Freischalten durch Admins, Lernregel, `judge_topic` in allen Fällen oben, Regelwerk.
- Verträge auf Speicher, SQLite und Postgres; Revision hin und zurück mit Daten.
- Evaluation mit festen Antworten: keine falschen Grünen, die neuen Fälle wie erwartet, die übrigen unverändert.
- Browser: Thema freischalten, Kennung mit Thema, Anheften, Dokument mit Themenwahl und Kontaktwechsel, Prüfkarte, Filter.

**Nach dem Merge:** Devinstanz: Revision 0027, Neustart (Index Layout 5). Themen und Kennungen pflegt Tobi; das Training lernt Themen der Kennungen mit dem nächsten Lauf.

**Umsetzung (Branch `feature/topics-0.3.0`, 17.10.2026, autonom):**
- **Kern:** `TagScope.TOPIC` (Vorgaben: automatisch, angezeigt); `Contact.topic_ids` (höchstens 100; ein Thema und eines darunter dürfen beide freigeschaltet sein), `set_topics` nimmt den Kennungen ein entferntes Thema; `Identifier.topic_id` mit der Regel „Thema der Kennung ∈ Themen des Kontakts“ im Kontakt (`_check_topics`); `Document.topic_id` in `CONFIRMING` und `CLASSIFIER_LABELS`. „Nur ein Thema, das der Kontakt führt“ prüft eine Stelle, `_access.checked_changes` (Änderung durch eine Person, Urteil, Prüfansicht): Ein Kontaktwechsel ohne Thema räumt ein nicht geführtes ab, ein Admin schaltet ein gewähltes in derselben Unit of Work frei, andere bekommen 422, ein Thema ohne Kontakt ist 422. Abschalten am Kontakt 409, solange Dokumente des Kontakts es tragen; Löschen nur ohne Kinder und ungenutzt (Dokumente, Freischaltungen, Kennungen, Regeln; 409).
- **Kennungen:** `mine_identifiers` bekommt je bestätigtem Dokument Kontakt, Thema und Kennungen; eine gelernte Kennung bekommt das Thema nur, wenn alle ihre Dokumente es tragen. Eine gelernte Kennung behält ihr Thema nur, solange der Kontakt es führt.
- **Revision 0027:** `contact_topics` (Kontakt mit CASCADE, Index auf `tag_id`), `contact_identifiers.topic_id` und `documents.topic_id` mit Fremdschlüssel auf `tags`, `documents` mit Index. Unter Postgres in place; unter SQLite `documents` und `contact_identifiers` ohne Umbenennen neu gebaut wie in 0024 und 0026 (temporäre Kopie, Fremdschlüssel aus mit Schutzabfrage, Zahl der Zeilen geprüft). Die Rückrichtung entfernt Spalten, `contact_topics` und die Themen; Regeln mit Themen bleiben (wie 0026). Test mit Daten auf SQLite und Postgres, hin und zurück.
- **Signale und Urteil:** Prompt `propose-3`: an jedem der höchstens 20 vorausgewählten Kontakte seine automatischen Themen, im Schema das Pflichtfeld `topic` (eines davon oder `null`). Klassifikator mit dem Ziel Thema (mindestens zwei Dokumente je Klasse); Modelle ohne Themen laden weiter. Nachbarn stimmen über das Thema ab, `KnownLabels.topics`; die Fakten liefern das Thema einer Kennung (`FactsView.topics` je Kontakt). `judge_topic` wie entschieden, immer OK; unter einem nur vorgeschlagenen Kontakt nur Vorschläge. Ein Thema „nur von Hand“ schlägt die Klassifizierung nie vor (wie bei Dokument-Tags).
- **Regeln und Templates:** Bedingung `topic` (ist, eines von, vorhanden, fehlt; mit Nachfahren; unter Misstrauen unbekannt), Aktion `set_topic` nur in Nutzer-Regeln (in globalen 422), mit Notiz übersprungen, wenn der Kontakt nach den Regeln es nicht führt; verschiedene Themen zweier Regeln sind ein Konflikt. Setzt eine Regel einen Kontakt, der das Thema nicht führt, räumt sie es ab (als Wirkung dieser Regel im Bericht). Templates: `topic`.
- **Suche:** Layout 5 mit `topic_ids` (filterbar) und `topics` (durchsuchbar) samt Vorfahren, im Kopf des ersten Abschnitts der Pfad des Themas. Der Abgleich auf Layout 5 bettet nichts neu ein (noch hat kein Dokument ein Thema); Umbenennen und Umhängen eines Themas aktualisieren seine Dokumente und betten deren ersten Abschnitt neu ein.
- **API und MCP:** wie geplant (Prüfansicht und Bestätigen eingeschlossen); dazu `topic_id` an den Kennungen in `GET /documents/{id}/facts` (zum Vorbelegen beim Anheften). MCP `get_document` und `update_metadata` mit dem Thema beim Namen, `list_tags` nennt die Themen.
- **Web:** Reiter „Themen“ mit Baum; Kontaktdialog mit Themenauswahl (Pfad als Name) und je Kennung dem Thema in eigener Zeile (nur Themen des Kontakts); Anheften in den Fakten mit Thema, vorbelegt; Formularfeld „Thema“ (Nutzer: Themen des Kontakts; Admins: alle, mit Hinweis auf die Freischaltung); die Karte in der Prüfansicht ist eine ruhige Hinweiskarte mit Kandidaten (das Thema ist nie unsicher); eckiger Chip (`rounded-md`) vor den Tags in Liste, Raster und Kopf; Filter, Regel-Baukasten, Template-Werte; Texte auf Deutsch und Englisch.
- **Evaluation:** `master_data.json` mit Themen (Arbeit; Versicherung › Hausrat-, Haftpflicht-, Krankenversicherung), `contact_topics` und Kennungen mit Thema (Hanse: Personalnummer → Arbeit; Gesundkasse: Versicherungsschein → Krankenversicherung); der Trainingskorpus trägt Themen (Hanse, Gesundkasse) und zwei neue Lohnabrechnungen der Hanse (57 Dokumente); Fälle 39–42 neu, 13 und 14 erwarten ihr Thema. Im Bericht zählt ein Thema als angenommen nur, wenn es gesetzt wurde.
- **Doku:** `backend/README.md` (Abschnitt Themen, Signale und Urteil, Regeln, Templates, Suche, Endpunkte, MCP).
- **Abweichungen:** (1) Widersprechen sich Signale oder Kennungen, bleibt das Thema leer mit allen Kandidaten, aber ohne einzelnen Vorschlag (`suggestion`), damit keiner bevorzugt wird. (2) Das Formular schickt bei jedem Kontaktwechsel das gewählte Thema mit; sonst ginge die Wahl eines Admins verloren, wenn sie dem gespeicherten Thema gleicht, das der neue Kontakt nicht führt (im Browser gefunden). (3) Der Filter `topic_id` ist einzeln wie `contact_id`, nicht wiederholbar wie `tag_id`; MCP `search` hat keinen Themenfilter (Themennamen sind durchsuchbar). (4) Für die Evaluation setzt der Korpus das Thema über eine Änderung durch eine Person, damit die Freischaltregel gilt.
- **Prüfung:** alle grün: 3113 Backend-Tests mit Postgres, Garage und Meilisearch, ruff, mypy, lint-imports; 565 Vitest, Lint, Format, Check, Build; Migrationsclient 82 (unverändert). Bewertung mit festen Antworten: 83 Fälle (kalt 41, trainiert 42), kein falsches Grün, kein Grün mit falschem Wert, keine Änderung ohne bestandene Prüfung, alle Spuren wie erwartet; Thema richtig 40 von 41 (kalt) und 41 von 42 (trainiert); die übrigen Fälle in ihren Spuren unverändert. Browser gegen den Build auf einer leeren Prüfinstanz (8001), 18 Prüfungen: Thema im Dialog anlegen, Baum im Reiter, Themen am Kontakt freischalten, Kennung mit Thema, kein Thema ohne Kontakt, Thema bleibt bzw. fällt beim Kontaktwechsel, Freischalten durch die Wahl eines Admins, Anheften mit vorbelegtem Thema, Prüfkarte bei einer Nutzerin (nur die Themen des Kontakts, Kandidat übernehmen, Bestätigen), Filter (auch über ein Elternthema), Chips in Liste, Raster und Kopf, Regel-Baukasten mit Bedingung und Aktion zum Thema; Deutsch und Englisch, hell und dunkel, Handybreite; keine Konsolenfehler außer der 401 vor der Anmeldung, keine CSP-Verstöße. Dabei gefunden und behoben: Abweichung (2), die Kennungszeile im Kontaktdialog war zu eng (Thema jetzt in eigener Zeile), „ohne Fundstelle“ am Thema entfällt (ein Thema hat nie ein Zitat).
- **Nach dem Review (separater Agent):** (1) Ein Thema, das ein Kontaktwechsel abräumte, galt als Entscheidung der Person: Änderungsregeln und die Regeln nach dem Bestätigen wurden „overruled“, und es zählte als Korrektur. Der Eintrag der Person kommt nun aus der Änderung, wie sie gegeben wurde (`changed`; ein so abgeräumtes Thema steht auch nicht in `corrected`). Das Formular schickt das Thema bei einem Kontaktwechsel nur als Wahl der Person: ein anderes Thema, das gespeicherte geleert, wo der neue Kontakt es führt, oder das gespeicherte erneut gewählt, wo er es nicht führt (die Wahl eines Admins schaltet es frei); ein vom Kontakt abgeräumtes nie. (2) Setzte eine Regel einen Kontakt, der das von der Person gewählte Thema nicht führt, fiel das Thema weg; jetzt wird der Kontakt der Regel überstimmt (Notiz `overruled`). (3) Zwei Regeln mit verschiedenen Themen ließen das Thema offen, die Web-UI konnte es aber nicht bestätigen; jetzt eine gelbe Prüfkarte (ohne „Keine Fundstelle“), Startwert das Thema des Dokuments oder der Vorschlag, Teil der Bestätigung. (4) Abschalten eines Themas am Kontakt und Setzen am Dokument liefen ohne Sperre und konnten beide gelingen; beide nehmen jetzt eine Sperre je Kontakt (`contact_topics_lock`), Test auf Speicher, SQLite und Postgres (ohne die Sperre schlägt er auf Speicher und Postgres fehl). (5) Nach einer Freischaltung durch die Wahl eines Admins lädt die Dokumentseite die Stammdaten neu, der Hinweis verschwindet. Prüfung danach: 3119 Backend-Tests, 569 Vitest, Lint, Format, Check, Build, Migrationsclient unverändert; Bewertung mit festen Antworten unverändert (Bericht bis auf die Uhrzeit gleich); Browser erneut mit 23 Prüfungen, neu: ein abgeräumtes Thema wird nicht geschickt, der Hinweis verschwindet nach dem Freischalten, ein von zwei Regeln offen gelassenes Thema wird in der Prüfansicht entschieden; keine Konsolenfehler außer der 401, keine CSP-Verstöße. Ein voller Lauf scheiterte einmal an `test_documents_go_through_the_api[postgres+s3]`; allein und im nächsten vollen Lauf grün (nicht weiter verfolgt).
- **Offen für Tobi:** (1) Klassifikator und Nachbarn lernen das gesetzte Thema, nicht seine Vorfahren (wie bei Tags). (2) Soll der Filter `topic_id` wiederholbar sein und MCP `search` einen Themenfilter bekommen? (3) Die Rückrichtung von 0027 lässt Regeln mit Themen stehen. (4) Umbenennen oder Umhängen eines Themas kostet Embeddings für den ersten Abschnitt seiner Dokumente, weil der Pfad im Kopf steht (so geplant; Tags nennen dort nur sich selbst). (5) Die Begründungen der Prüfkarte kommen wie bei den anderen Feldern englisch aus dem Backend; ein Regelkonflikt nennt die Werte als IDs. (6) Regeln, die ein Thema oder einen Kontakt setzen, nehmen die Sperre je Kontakt nicht (sie lesen die Themen des Kontakts in derselben Transaktion; seltener Wettlauf, offen).

#### Paket 5 – Icons, Farben und Logos (`feature/master-data-looks-0.3.0`)

PR mit Label `new-feature`.

**Umsetzung:**
- **Kern:** `Tag.icon` und `DocumentType.icon` (Name eines Lucide-Icons, Kleinbuchstaben, Ziffern und Bindestriche, höchstens 64 Zeichen, optional); `Tag.color`: einer der zwölf Namen (`gray`, `red`, `orange`, `amber`, `lime`, `green`, `teal`, `sky`, `blue`, `violet`, `fuchsia`, `pink`) oder `#rrggbb`. `Contact.logo`: Herkunft (hochgeladen, abgerufen), Domain, Zeitpunkt; die Datei liegt unter `contacts/{id}/logo.webp` (Schlüssel in `services/objects.py`).
- **Revision 0028:** `tags.icon`, `tags.color`, `document_types.icon`, die Angaben zum Logo an `contacts`.
- **Bilder:** Port `ImageNormaliser` (PNG, JPEG, WebP, ICO lesen; Grenze gegen Bilder, die beim Entpacken riesig werden; auf 128 px als WebP mit Transparenz), Adapter mit Pillow, Fake und Vertragstest. SVG wird abgelehnt.
- **Abruf:** Port `LogoFetcher` (`fetch(domain)`), Adapter mit `httpx2`, Fake und Vertragstest:
  - Schutz vor SSRF, den es in Papiq noch nicht gibt: Der Name wird aufgelöst, jede Adresse muss öffentlich sein (`is_global`; abgewiesen werden auch 100.64.0.0/10, ULA und auf IPv6 abgebildete private IPv4), verbunden wird mit der geprüften Adresse und dem Namen für SNI und Host (gegen DNS-Rebinding).
  - Nur HTTPS, höchstens drei Weiterleitungen, jede neu geprüft; höchstens 1 MB, 10 s; Inhaltstyp geprüft; ohne Cookies, `trust_env=False`.
  - Gelesen werden `apple-touch-icon`, `<link rel="icon">` mit Größen, das Web-App-Manifest und `/favicon.ico`, mit `html.parser` der Standardbibliothek; gewählt wird das größte Rasterbild ab 64 px.
- **Ablauf:** Einstellung `PAPIQ_LOGO_FETCH` (Vorgabe aus). Job `contacts.logo`, eingereiht, wenn ein Kontakt ohne hochgeladenes Logo eine Domain als Kennung bekommt (von Hand, gelernt, angeheftet); dazu der Knopf (`POST /contacts/{id}/logo/fetch`, 202) und der Befehl `fetch-logos` mit VS-Code-Task. Ein gescheiterter Abruf wird mit der Domain vermerkt und erst nach neuer Domain oder auf Knopfdruck wiederholt. Ein hochgeladenes Logo wird nie überschrieben.
- **API:** `PUT /contacts/{id}/logo` (Upload, Admins, 1 MB, 415 bei anderem Typ), `DELETE /contacts/{id}/logo`, `GET /contacts/{id}/logo` (angemeldete Nutzer; die Stammdaten sind global) mit `Cache-Control: private, max-age=86400` und der Version in der URL, als Ausnahme vom `no-store` der API, damit Listen die Logos nicht neu laden; `ContactOut.logo`; die API meldet, ob der Abruf eingeschaltet ist. `icon` und `color` an Tags und Typen.
- **Web:**
  - `IconPicker`: die 170 Icons der Auswahl nach Bereichen, die Suche über alle; die Liste der Namen kommt aus `import.meta.glob`, geladen wird ein Icon erst, wenn es zu sehen ist (eigene Dateien mit Hash im Build). Bereiche und Suchwörter in beiden Sprachen.
  - `ColorPicker`: zwölf Felder, „Mehr Farben“ mit Farbwähler und Hex-Feld.
  - `TagChip` (Icon, Farbe; eine eigene Farbe mit Hintergrund und Schrift gemischt, wie in der Vorschau), `TypeIcon`, `ContactAvatar` (Logo oder Initialen) in Liste, Raster, Kopf, Formular, Prüfansicht und Stammdaten.
  - Kontaktdialog: Logo hochladen, entfernen, abrufen (nur wenn eingeschaltet), mit Herkunft.
  - Die zwölf Farben als Tokens in `app.css`, hell und dunkel, Text mit WCAG AA.
- **Doku:** `backend/README.md` (Logos, `PAPIQ_LOGO_FETCH`, Endpunkte), `deploy/README.md` (ausgehende Anfragen), `.devcontainer/live.env.example`.

**Prüfung:**
- Unit: Schutz vor SSRF mit festem Resolver (privat, Loopback, Link-Local, CGNAT, ULA, abgebildete IPv4, Weiterleitung auf eine private Adresse, zu groß, zu langsam), Wahl des Icons aus HTML und Manifest, Normalisieren (riesiges Bild, ICO, Transparenz), Auslöser des Jobs, Vorrang des Uploads.
- REST: Größe, Typen, SVG abgelehnt, Header des Logos; Vertrag der Spalten.
- Vitest: Auswahl, Farben, Kontrast eigener Farben, Avatar.
- Browser gegen den Build: Icons und Farben an Tags und Typen, eigene Farbe hell und dunkel, Logo hochladen und entfernen, Initialen; Größe des Builds vorher und nachher im PR. Kein echter Abruf in Tests; den ersten auf der Devinstanz nur auf Tobis Zuruf.

**Nach dem Merge:** Devinstanz: Revision 0028, `PAPIQ_LOGO_FETCH=true` in `live.env`, `fetch-logos` auf Zuruf.

**Umsetzung (Branch `feature/master-data-looks-0.3.0`, 17.10.2026, autonom):**
- **Kern:** `domain/appearance.py`: `checked_icon` (Kleinbuchstaben und Ziffern in Wörtern mit Bindestrich, höchstens 64 Zeichen; nur die Form), `PaletteColor` (zwölf Namen) und `checked_color` (ein Name oder `#rrggbb`, kleingeschrieben gespeichert), `ContactLogo` (Herkunft `uploaded`/`fetched`, Domain eines geholten, Zeitpunkt = Version) und `FailedLogoFetch` (Domain, Zeitpunkt). `Tag.icon`, `Tag.color`, `DocumentType.icon`, `Contact.logo`, `Contact.logo_failure`, `Contact.domains` (die zählenden Domains, von Hand eingetragene zuerst). Schlüssel `objects.logo_key` → `contacts/{id}/logo.webp`.
- **Revision 0028:** `tags.icon`, `tags.color`, `document_types.icon`, an `contacts` `logo_source`, `logo_domain`, `logo_stored_at`, `logo_failed_domain`, `logo_failed_at`; alle leer erlaubt, ohne Schlüssel, daher auf beiden Datenbanken in place (kein Neubau unter SQLite). Test mit Daten auf SQLite und Postgres hin und zurück, danach das Schema von 0027.
- **Bilder:** Port `ImageNormaliser`, Adapter `adapters/outbound/pillow` (nur die Decoder PNG, JPEG, WebP, ICO; Art am Inhalt erkannt, SVG erreicht keinen Decoder; höchstens 4096 × 4096 Pixel vor dem Dekodieren geprüft, bei ICO jedes Bild nach seinem eigenen Kopf; JPEG verkleinert dekodiert und nach EXIF aufgerichtet; WebP bis 128 px mit Transparenz), Fake und Vertragstest.
- **Abruf:** Port `LogoFetcher` (`fetch(domain)`), Adapter `adapters/outbound/logos` mit httpx2, Fake und Vertragstest. Schutz vor SSRF (`addresses.is_public`): jede Adresse des Namens muss öffentlich sein (`is_global` und nicht privat, Loopback, Link-Local, Multicast, reserviert, 100.64.0.0/10, fc00::/7, Site-Local, mit Zone, IPv4-kompatibel, 6to4, Teredo; auf IPv6 abgebildete und NAT64-Adressen zählen als ihre IPv4-Adresse); verbunden wird mit der geprüften Adresse, Name als `Host` und für TLS (`sni_hostname`, Zertifikat), jede Weiterleitung neu geprüft (höchstens drei); nur HTTPS auf Port 443, ohne Zugangsdaten in der Adresse; je Anfrage höchstens 1 MB und 10 s, je Abruf 30 s; nur die erfragten Inhaltstypen; `Accept-Encoding: identity`, gzip wird innerhalb der Grenze entpackt, andere Kodierungen abgelehnt; je Anfrage ein eigener Client (keine Cookies), `trust_env=False`. Gelesen werden die Seite der Domain (sonst `www.`), `apple-touch-icon` (ohne Größe 180 px), `<link rel="icon">` mit `sizes`, das Manifest und `/favicon.ico` (`html.parser`); versucht werden höchstens vier Icons, nach angegebener Größe, ab 128 px ist Schluss, sonst das größte ab 64 px (Größe aus dem Bildkopf). Tests gegen eine Attrappe des Webs (`MockTransport`) mit festem Resolver; keine Anfrage verlässt den Prozess.
- **Ablauf:** `LogoService`: Hochladen (Admins; PNG, JPEG oder WebP bis 1 MiB, ICO nur von Websites), Entfernen, Lesen; Datei und Kontakt ändern sich unter einer Sperre je Kontakt (`logo_lock`), auch beim Löschen des Kontakts (das Logo geht mit). Job `contacts.logo` (mit `PAPIQ_LOGO_FETCH`): eingereiht, wenn ein Kontakt ohne Logo eine neue Domain bekommt (von Hand, angeheftet, gelernt: `replace_learned_identifiers`), nur für diese Domain; auf Knopfdruck (`POST /contacts/{id}/logo/fetch`) alle Domains nacheinander (höchstens drei), auch eine gescheiterte, ersetzt ein geholtes Logo; `fetch-logos` (VS-Code-Task „Papiq: Logos holen“) für alle Kontakte ohne Logo, ohne gescheiterte Domains. Gescheitert: vermerkt mit der ersten versuchten Domain. Das Netz wird außerhalb jeder Transaktion gefragt; ein hochgeladenes Logo wird nie ersetzt, auch wenn es während eines Abrufs kommt.
- **API:** `icon`/`color` an `TagCreate`/`TagPatch`/`TagOut` und an den Typen (`null` entfernt); `ContactOut.logo` (Herkunft, Domain, Zeitpunkt, `url` mit `?v=`) und `logo_failure`; `PUT`/`DELETE`/`GET /contacts/{id}/logo` (413, 415, 422); `GET` mit `Cache-Control: private, max-age=86400` als Ausnahme von `NoStore` (nur erfolgreiche Antworten dieses Pfads), das Hochladen ausgenommen von `PAPIQ_REQUEST_MAX_SIZE` (eigene Grenze); `POST /contacts/{id}/logo/fetch` (202; 503 ohne Abruf, 409 mit hochgeladenem Logo, 422 ohne Domain); `GET /pipeline` meldet `logo_fetch` (die Stelle, an der die Web-UI schon das VLM erfährt).
- **Web:** Palette als Tokens `--tag-<name>`/`--tag-<name>-foreground` in `app.css` (Werte der freigegebenen Vorschau, jedes Paar ≥ 6,2:1 hell, ≥ 8,5:1 dunkel); eigene Farbe mit `color-mix` wie in der Vorschau (`.chip-own`). `LucideIcon` (Icon aus dem eigenen Build per `import.meta.glob`, je Icon eine Datei, geladen, wenn es sichtbar wird; Ersatz für unbekannte Namen), `IconPicker` (170 Icons in elf Bereichen, Suche über alle 1866 nach Namen und für die Auswahl nach deutschen und englischen Wörtern), `ColorPicker` (zwölf Felder, „Mehr Farben“ mit Farbwähler und Hex-Feld), `TagChip` mit Icon und Farbe (Thema eckig, Gruppen umrandet), `TypeIcon`, `ContactAvatar` (Logo auf weißem Grund, sonst Initialen ohne Rechtsformen) in Liste, Raster, Kopf und Formular des Dokuments, bei den Kandidaten der Prüfansicht und in den Stammdaten (Bäume als Chips); Kontaktdialog mit Logo (hochladen, entfernen, holen nur mit Abruf, Domain und ohne hochgeladenes Logo) und Herkunft.
- **Doku:** `backend/README.md` (Abschnitt Icons, Farben und Logos, Endpunkte, Ausnahme vom `no-store`, Job), `deploy/README.md` (ausgehende Anfragen nur mit `PAPIQ_LOGO_FETCH`), `.devcontainer/live.env.example`.
- **Abweichungen und Auslegungen:** (1) Eine eigene Farbe mischt den Grund wie in der Vorschau (22 % mit der Karte), die Schrift aber nicht fest zu 70 %: Für Weiß im hellen und Schwarz im dunklen Schema ergäbe das 2,1:1 bzw. 1,3:1. `lib/colors.ts` rechnet je Schema den größten Anteil von 70 % abwärts (in 5-%-Schritten, Oklab wie `color-mix`), der 4,6:1 hält, und gibt ihn als `--chip-light`/`--chip-dark` mit; ein Test prüft die Palette aus `app.css` und 216 eigene Farben, der Browser maß an den Chips mindestens 4,85:1. Eigene Farben tragen einen feinen Ring, damit Weiß auf Weiß als Chip erkennbar bleibt. (2) Die Suchwörter der 170 Icons stehen in zwei Datendateien je Sprache (`lib/icons/words.de.ts`, `words.en.ts`), die elf Bereiche als Meldungen; 340 Meldungen wären unhandlich gewesen. (3) Ob der Abruf eingeschaltet ist, meldet `GET /pipeline` (`logo_fetch`), wo die Web-UI das VLM erfährt. (4) Der Kopf des Dokuments zeigt jetzt Kontakt (Logo oder Initialen) und Typ (Icon) über den Tags, ohne Gruppen; die Bäume der Stammdaten zeigen Tags als Chips. (5) Ein Logo steht auf weißem Grund, auch dunkel (Logos sind für hellen Grund gezeichnet). (6) Beim Bau festgelegt: nur Port 443, je Abruf 30 s und höchstens vier Icons, `www.` als zweiter Versuch, ab 128 px kein weiteres Icon; ein ICO wird nur von Websites genommen, hochgeladen 415; ein gescheiterter Abruf wird mit der ersten versuchten Domain vermerkt; der Knopf ohne Abruf antwortet 503 wie der Klassifikator. (7) Vitest liest `app.css` roh (`test.css.include` in `vite.config.ts`), sonst lieferte `?raw` dort nichts.
- **Größe des Builds:** vorher 6 918 400 Bytes in 365 Dateien (JavaScript zusammen gzip 722 978 Bytes), nachher 8 054 956 Bytes in 2 214 Dateien (gzip 919 820 Bytes): 1 866 Icons zu je etwa 0,5 KB als eigene Dateien, geladen nur beim Zeigen; die Liste aller Icons (232 KB, gzip 50 KB) lädt mit jeder Seite, die Chips zeigt.
- **Prüfung:** alle grün: 3321 Backend-Tests mit Postgres, Garage und Meilisearch, ruff, mypy, lint-imports; 611 Vitest, Lint, Format, Check, Build; Migrationsclient 82 (unverändert). Bewertung mit festen Antworten wie nach Paket 4 (Bericht bis auf eine Zeitspalte gleich, kein falsches Grün). Schutz vor SSRF: 112 Tests gegen eine Attrappe des Webs mit festem Resolver (privat, Loopback, Link-Local, CGNAT, ULA, abgebildete IPv4, NAT64, eine von zwei Adressen privat, DNS-Rebinding, Weiterleitung auf private Adressen und fremde Ports, mehr als drei Weiterleitungen, zu groß angekündigt und unangekündigt, gzip-Bombe, zu langsam, falsche Inhaltstypen, Cookies, Proxy aus der Umgebung), keine Anfrage nach außen. Browser gegen den Build auf einer leeren Prüfinstanz (8001), 26 Prüfungen: Icons und Farben an Tags und Typen über die Dialoge (Suche „Auto“, Palette, „Mehr Farben“ mit Hex), eigene Farben hell und dunkel mit dem gemessenen Kontrast der Chips, Logo hochladen und entfernen im Kontaktdialog, Initialen, Logo mit eigenem Cache-Header, Logo und Typ-Icon in Liste, Raster, Kopf und Formular; Deutsch und Englisch, hell und dunkel, Handybreite ohne Überlauf; keine Konsolenfehler außer der 401 vor der Anmeldung, keine CSP-Verstöße. Dabei gefunden und behoben: „Kein Icon“ in Monospace, Weiß auf Weiß ohne Kontur.
- **Review des Schutzes vor SSRF (separater Agent, nur lesend, ohne Netz):** keine Umgehung gefunden (Prüfung bis in httpx2/httpcore2: `Host` bleibt, `sni_hostname` wird `server_hostname` mit Zertifikatsprüfung, keine Proxys, kein erneutes Auflösen, keine Wiederholungen, kein HTTP/2). Behoben: Seiten und Manifeste werden als UTF-8 gelesen, gleich welchen Zeichensatz der Server nennt (punycode brauchte für 1 MB 66 s auf der Ereignisschleife, idna warf); ein ICO wird beim Abruf nur über sein Verzeichnis gemessen und im Thread (Pillow dekodiert beim Öffnen das größte Bild); der Normalisierer prüft jedes Bild eines ICO so, wie Pillow es liest (ein vorangestellter IHDR von 1 × 1 verbarg 9000 × 9000), höchstens 64 Bilder, ohne globale Warnungsfilter; gzip endet mit seinem Strom, auch die gesendeten Bytes zählen gegen die Grenze; Größen nur aus ASCII-Ziffern, ein tief verschachteltes Manifest gilt als leer, alles Übrige wird zum gescheiterten Abruf (wird vermerkt); 192.88.99.0/24 gilt nicht als öffentlich. Hinweise der Review ohne Änderung: je Anfrage ein neuer TLS-Kontext, nur die erste geprüfte Adresse wird versucht, ein abgebrochenes `getaddrinfo` belegt seinen Thread weiter, ein progressives JPEG mit sehr vielen Scans kann den Thread des Normalisierers eine Weile binden.
- **Nach dem Review (unabhängig, keine Umgehung des Schutzes vor SSRF):** (1) Ein progressives JPEG unter 1 MB mit Zehntausenden Scans band den Normalisierer minutenlang, länger als die Lease von drei Minuten; mehr als 64 Scan-Marken werden vor dem Dekodieren abgewiesen (64 dauern bei 4096 × 4096 eine halbe Sekunde), die Lease von `contacts.logo` ist zehn Minuten. (2) EXIF mit der Auflösung als Text ließ `exif_transpose` einen `TypeError` werfen (Hochladen 500, Abruf brach ab, der Fehlschlag wurde nicht vermerkt); nicht anwendbares EXIF lässt das Bild, wie es ist, jeder andere Fehler von Pillow ist ein `ImageError`, ein unerwarteter Fehler eines Adapters ein gescheiterter Abruf dieser Domain. (3) Ein Platz für den Fehlschlag ließ `fetch-logos` zwei scheiternde Domains abwechselnd versuchen; der Kontakt hält nun alle gescheiterten Domains (`logo_failed_domains` in 0028), jeder Abruf fügt die versuchten hinzu, wiederholt wird nur auf Knopfdruck. (4) Die Datei wurde vor der Zeile des Kontakts geschrieben; nun die Zeile zuerst (eine Änderung des Kontakts in der Zwischenzeit scheitert unter SQL an der Version, bevor die Datei ersetzt ist), dann die Datei, bei einem Konflikt bis zu dreimal neu. Prüfung danach: 3328 Backend-Tests mit Postgres, Garage und Meilisearch, ruff, mypy, lint-imports; 612 Vitest, Lint, Format, Check, Build; Browser erneut mit 26 Prüfungen, ohne Konsolenfehler und CSP-Verstöße.
- **Offen für Tobi:** (1) Die angepasste Mischung eigener Farben (Abweichung 1) statt fester 70 %. (2) Kontakt und Typ im Kopf des Dokuments (Abweichung 4). (3) Ein Kontakt mit geholtem Logo holt bei einer neuen Domain nicht neu (nur auf Knopfdruck); `fetch-logos` versucht gescheiterte Domains nicht erneut (eine Option dafür wäre möglich). (4) Wird `PAPIQ_LOGO_FETCH` wieder ausgeschaltet, bleiben eingereihte Jobs `contacts.logo` liegen, bis er wieder an ist. (5) Die Liste aller Icons kostet 50 KB gzip auf Seiten mit Chips; mit `build.modulePreload.resolveDependencies` ließe sie sich verkleinern. (6) Die Pfeiltasten wechseln die Farbe, nicht aber das Icon (170 Tab-Stopps in der Auswahl).

#### Paket 6 – Ein Tags-Menü, Anlegen ohne Umweg, Stammdaten am Handy (`feature/tags-menu-0.3.0`)

**Anlass (Tobi, 17.10.2026):** Nach den Paketen 3 bis 5 hatten die Stammdaten sieben Reiter, die am Handy in zwei Reihen umbrachen; je Zeile ließen „0 Dokumente“ und zwei Knöpfe etwa sechs Zeichen für den Namen, Felder zeigten Schlüssel, Typ und Beschreibung dreifach abgeschnitten, die Dialoge listeten alle Gruppen und Themen als Häkchenlisten, „Speichern“ stand weit unten; die Kopfleiste lief um 8 px über. Wer beim Kontakt eine neue Gruppe brauchte, musste den Dialog verlassen.

**Entscheidungen (Tobi, 17.10.2026, nach Entwürfen; Ergebnis in `architektur.md` › Schnittstellen › Web-UI):** vier Reiter (Kontakte · Dokumenttypen · Tags · Felder); der Reiter Tags als der eine Ort für alle Arten (Variante A: Suche, Filter nach Art in der URL, je Art ein Abschnitt mit einzeiligem Kopf ohne Zahlen, eingeklappt gemerkt, Gebrauch als Link); die Zahlen an Gruppen und Themen aus der API; „Neuer Tag“ fragt zuerst nach der Art; Anlegen aus jeder Auswahl für Admins („„…“ anlegen“, „Mit Details anlegen …“); am Handy ganze Zeilen als Knopf, Editoren über den ganzen Bildschirm mit fester Leiste, die Auswahl als Blatt von unten; Löschen im Editor.

**Umsetzung (Branch `feature/tags-menu-0.3.0`, 17.10.2026, autonom):**
- **API:** `TagOut.contact_count` (Kontaktgruppen und Themen) und `document_type_count` (Typgruppen) mit `document_counts=true`, für jeden, der fragt (Stammdaten sind global). Jedes Mitglied zählt einmal an seinem Tag und an allen darüber, wie ein Filter auf den Tag es findet: `TagTree.holders` (rein), `MasterDataService.tag_members` liest Tags, Kontakte und Typen in einer Unit of Work. Keine Revision. Tests: Baum (Einheit), Service auf Speicher, SQL-Suite auf SQLite und Postgres, REST.
- **Kopfleiste:** schmalere Abstände unter `sm`; bei 390 px kein Überlauf mehr (im Browser gemessen, auch das Rechteck jedes Elements der Kopfleiste).
- **Stammdaten:** vier Reiter in `?tab=` (ein unbekannter ist der erste; die alten Werte ohne Umleitung), je Reiter eine Suche im Browser (`lib/master-search.ts`: Kontakte auch nach Aliasen und Kennungen, eine IBAN mit oder ohne Leerzeichen; Felder auch nach Schlüssel) und ein Knopf zum Anlegen, am Handy das runde „+“ neben dem Titel und die Reiter gleich breit („Typen“). Zeilen über `MasterRow`: am Desktop Gebrauch als Link und Stift, am Handy die ganze Zeile als Knopf mit der Zahl und einem Pfeil, ohne weitere Knöpfe; Namen bis zwei Zeilen. Kontakte: Gruppen als Chips, Kennungen, Themen, ein Alias (am Handy nur ohne Gruppen); Typen: Beschreibung auf zwei Zeilen („Noch keine Beschreibung“), Gruppen, „Titelmuster“ (das Muster als Tooltip), „n Felder · n Pflicht“; Felder: Datentyp, „von Hand“, „Rechnung · Pflicht“ bzw. „Alle Typen · optional“, Beschreibung einzeilig, der Schlüssel nur am Desktop und im Feld-Editor. `DocumentCount` heißt `UsageCount` und zählt auch Kontakte und Typen.
- **Reiter Tags (`TagsPanel`, `lib/tag-kinds.ts`):** Filter-Chips „Alle · Dokument-Tags · Kontaktgruppen · Typgruppen · Themen“ in `?kind=`, je Art ein Abschnitt (Einklappen, Icon, Name, am Desktop die Beschreibung, rundes „+“ mit der Art vorbelegt), eingeklappte Abschnitte in `localStorage` (abgesichert). Eine Suche trifft auch Elternnamen, zeigt die Treffer mit den Tags darüber, nur Abschnitte mit Treffern und öffnet sie (ohne das Gemerkte zu ändern). Baumzeilen: Pfeil nur bei Kindern, je Ebene eine Stufe weiter (`--depth`), Chips ohne Pfeil fluchten mit denen mit Pfeil; am Desktop „n darunter“ und bei Typgruppen die Namen ihrer Typen (aus dem Entwurf). Gebrauch als Link: Dokumente; Kontaktgruppe → `?tab=contacts&group=`, Typgruppe → `?tab=document-types&group=`, Thema → Kontakte (`&topic=`) · Dokumente; die Kontakt- und Typliste filtern danach samt der Tags darunter und zeigen den Filter als Chip zum Entfernen.
- **Editoren (`EditorShell`):** am Desktop Dialoge mit stehendem Kopf und Fuß (Löschen links, Abbrechen und Speichern rechts), dazwischen scrollt der Inhalt; ein Fehler der API steht unter dem Kopf. Am Handy über den ganzen Bildschirm mit fester Leiste („Abbrechen“ · Art des Eintrags, etwa „Kontakt“ · „Sichern“), Löschen am Ende. Löschen fragt nach (`ConfirmDialog`), ein 409 bleibt als Meldung, der Editor offen; die Listen haben keine Löschknöpfe mehr (alle vier Arten, auch Felder). Der Feld-Editor zeigt `field.<key>`.
- **Neuer Tag:** ohne Art (Knopf des Reiters bei „Alle“) zuerst vier Karten mit Satz und Beispiel, dann Name, Elterntag (nur dieser Art), Icon, Farbe, Ermittlung (nicht bei Gruppen), „Am Dokument zeigen“ (Vorgabe je Art) und eine Vorschau mit ihrer Form („eckig: ein Thema“). Mit Art (Abschnitt, Filter, Auswahl) ohne Karten.
- **Auswahl (`TagPicker`, `TagPickerDialog`):** Gruppen eines Kontakts oder Typs und Themen eines Kontakts als Chips mit „+ Gruppe“/„+ Thema“; die Auswahl ist ein Dialog über dem Editor, am Handy ein Blatt von unten (Suche über die Pfade, alle Tags der Art mit Häkchen und Einrückung, bei keinem Treffer „Kein Treffer.“ und alle, „Fertig“). Kennungen am Handy als Zeilen (Art, Wert in Mono, Themen-Chip, „gelernt“/„verworfen“), die sich an Ort und Stelle öffnen; Aliase danach, kompakt.
- **Anlegen ohne Umweg (`lib/tag-create.ts`):** nur Admins; heißt kein Tag der Art wie die Suche (Groß- und Kleinschreibung und Leerzeichen am Rand egal), bieten Auswahl, Tag-Eingabe am Dokument und Themenfeld „„…“ anlegen“ (`POST /tags` mit Art, oberste Ebene, sofort gewählt) und „Mit Details anlegen …“: `masterDataDialogs.createTag` öffnet einen zweiten Tag-Dialog über dem aktuellen (Art gesetzt, Name vorbelegt), der neue Tag wird gewählt; danach laden Listen und Lookup neu (`saved`). Die Felder des Stammdaten-Dialogs haben eigene IDs (`$props.id()`), weil zwei übereinander stehen können. Das Themenfeld am Dokument ist dafür eine Suche im Popover statt eines Auswahlfelds (Chip, „Kein Thema“, Pfade); ein dort angelegtes Thema schaltet das Speichern am Kontakt frei wie jede Wahl eines Admins. Ein Admin kann so auch das erste Thema eines Kontakts anlegen (das Feld ist für ihn mit Kontakt nie gesperrt).
- **Handy:** Breite unter `sm` über `lib/phone.svelte.ts` (`MediaQuery`, in Tests überschreibbar), wo sich die Struktur unterscheidet; Ziele mindestens 44 px (Filter-Chips, Pfeile und „+“ der Abschnitte und Bäume, „+ Gruppe“, Filter-Chip), der kleine Entfernen-Knopf der Chips nimmt Tipps über seinen Rand hinaus.
- **Doku:** `architektur.md` (ein Tags-Reiter, Anlegen ohne Umweg, Stammdaten am Handy), `web/README.md` (Abschnitt Master data), `backend/README.md` (Zahlen an Gruppen und Themen). Der Smoke-Test `areas.spec.ts` löscht seinen Kontakt nun im Editor (grün gegen die Prüfinstanz).
- **Abweichungen und Auslegungen:** (1) Die Filter-Chips tragen keine Zahlen (der Entwurf für den Desktop zeigte die Zahl der Tags je Art; „keine Zahlen im Kopf“ auch hier, wie im Handy-Entwurf). (2) Hinweise nur, wo ein Tag vom Üblichen seiner Art abweicht: „nur von Hand“, „am Dokument ausgeblendet“, bei Gruppen (Vorgabe: ausgeblendet) „am Dokument gezeigt“; sonst trug im Browser jede Gruppe den Hinweis. (3) Der Gebrauch von Kontaktgruppen, Typgruppen und Themen verlinkt die gefilterte Kontakt- bzw. Typliste (neuer Filter dort). (4) Die Suche gibt es auch am Desktop. (5) „… unter „Versicherung““ aus dem Entwurf fehlt: Welcher Elterntag gemeint ist, fiel nicht sauber heraus. (6) Am Handy zeigt der Abschnittskopf kein Icon und die Zeile als Zahl den letzten Gebrauch (bei Themen die Dokumente), wie im Entwurf. (7) Der Logo-Block im Kontakt-Editor bleibt wie in Paket 5 (der Entwurf setzte das Logo neben den Namen). (8) Sprach- und Farbknopf bleiben in der Kopfleiste (der Entwurf ließ sie weg); gegen den Überlauf genügten die Abstände. (9) Am Desktop ist die Auswahl ein Dialog über dem Editor, kein Popover.
- **Prüfung:** Backend 3333 Tests mit Postgres, Garage und Meilisearch, ruff, ruff format, mypy, lint-imports grün; Web 655 Vitest in 70 Dateien, Lint, Format, Check, Build (in ein eigenes Verzeichnis, `web/build` der laufenden Devinstanz blieb unberührt); Migrationsclient 82 (unverändert). Browser gegen den Build auf einer leeren Prüfinstanz (8001) mit synthetischen Stammdaten und drei Dokumenten, 116 Prüfungen ohne Fehlschlag: alle vier Reiter bei 1400 × 900 und 390 × 844, Deutsch und Englisch, hell und dunkel, ohne Überlauf (auch die Kopfleiste); Filter in der URL und nach dem Neuladen, Einklappen nach dem Neuladen gemerkt, Suche über den Elternnamen; Kontaktgruppe im Kontakt-Editor über „„…“ anlegen“ und „Mit Details anlegen …“ (Desktop und Handy, gespeichert); Thema im Themenfeld des Dokuments als Admin angelegt, gespeichert und am Kontakt freigeschaltet (beide Breiten); Editor am Handy über den ganzen Bildschirm, geändert und mit „Sichern“ gespeichert; Löschen aus dem Editor (Tag, Feld, Kontakt am Handy, Thema am Desktop), ein benutzter Tag abgelehnt; „Neuer Tag“ mit Karten und die Auswahl als Blatt in Englisch und dunkel. Konsole: außer der 401 vor der Anmeldung nur die 409 der absichtlich abgelehnten Löschung; keine CSP-Verstöße. Dabei gefunden und behoben: Hinweis „ausgeblendet“ an jeder Gruppe (Abweichung 2), graue Kreise hinter aufgeklappten Pfeilen, zu lange Zeile unter Kontakten am Handy, doppelte IDs zweier Dialoge übereinander.
- **Nach dem Review (unabhängig, nichts Schweres):** (1) „Fertig“ ließ die Suche der Auswahl gefüllt (bits-ui meldet nur eigene Schließvorgänge); sie wird dort und bei jedem Öffnen geleert. (2) Ein Filter auf eine Art, deren Abschnitt der Browser eingeklappt hält, zeigte einen leeren Abschnitt; mit Filter (wie mit Suche) ist er offen, ohne Pfeil, das Gemerkte bleibt. (3) Ein unbekanntes `?group=`/`?topic=` (gelöscht, vertippt, andere Art) leerte die Kontakt- oder Typliste ohne Chip; es wird ignoriert und aus der URL genommen. (4) Ein Doppelklick auf „„…“ anlegen“ in der Tag-Eingabe und im Themenfeld schickte zwei `POST /tags` (das zweite 409); beide warten nun auf das erste. (5) Die Kopfleiste lief bei 360 px noch um 1 px über; unter 380 px sind Rand, Abstände und der Sprachknopf etwas schmaler (im Browser bei 360, 375 und 390 gemessen). (6) Die vergrößerte Trefferfläche des ✕ an Chips lag über dem Ende des Namens; sie reicht nur noch nach rechts, oben und unten. (7) Barrierefreiheit: `aria-controls` nur bei offenem Abschnitt (ebenso am Icon-Knopf), `aria-expanded` an „+ Gruppe“/„+ Thema“, die Chips heißen nach dem sichtbaren Label (`aria-labelledby`); am Handy 44 px für den Namen eines Chips mit Pfad, die Farbfelder (36 px und 4 jenseits des Rahmens, Abstand 8, ohne Überlappung) und die Knöpfe für Icon und „Mehr Farben“ (im Browser mit `elementFromPoint` gemessen). Neue Vitest: Suche nach „Fertig“ leer, ein offener gefilterter Abschnitt, unbekannter Filter, ein Anlegen bei Doppelklick (Tag-Eingabe, Themenfeld), kein Anlegen für Nutzer im Themenfeld, Escape schließt nur den oberen Dialog. Zwei Zwischenstände (5a7e8d8, 94d3fc5) fielen durch Lint, je der nächste Commit behebt es. Prüfung danach: 663 Vitest in 71 Dateien, Lint, Format, Check, Build (eigenes Verzeichnis); Browser auf frischer Prüfinstanz erneut 116 Prüfungen ohne Fehlschlag, Konsole wie zuvor (401, die absichtliche 409), keine CSP-Verstöße; Backend unverändert.
- **Offen für Tobi:** (1) Zahlen in den Filter-Chips? (2) „am Dokument gezeigt“ als Hinweis bei Gruppen statt „ausgeblendet“ bei allen? (3) „… unter …“ beim Anlegen, und unter welchem Elterntag? (4) Logo neben dem Namen im Kontakt-Editor am Handy, Sprach- und Farbknopf in der Kopfleiste am Handy? (5) Meldungen der API (etwa „'Gesundheit' is in use“) kommen weiter englisch.

#### Paket 7 – Nachbesserungen aus Tobis Antworten (`feature/refinements-0.3.0`)

**Anlass (Tobi, 17.10.2026):** Antworten auf die offenen Punkte der Pakete 3 bis 6.

**Entscheidungen (Tobi, 17.10.2026; Ergebnis in `architektur.md`: „Tags, Gruppen und Themen“, Kontakte › Logo, Training und Rückkopplung, Suche, Regel-Engine, Schnittstellen):** (1) Eine Änderung nur an manuellen Dokument-Tags bestätigt nicht. (2) Bekommt ein Tag oder eine Gruppe einen anderen Elterntag, behalten Dokumente bzw. Kontakte und Typen nur den Nachfahren, in einem Job über die betroffenen Zeilen, ohne Bestätigung und Lane zu ändern, mit neuen Sucheinträgen. (3) `topic_id` wiederholbar („eines davon“), im Web als Chips, MCP `search` mit Themen beim Namen. (4) Gründe von Regelkonflikten nennen Namen statt IDs. (5) Ein abgerufenes Logo wird für eine neue Domain neu geholt, ein hochgeladenes nie ersetzt. (6) Ein Job `contacts.logo` bei ausgeschaltetem Abruf endet sofort mit Vermerk. (7) Die Liste aller Icons lädt nur, wo sie gebraucht wird. (8) Pfeiltasten in der Icon-Auswahl. (9) Am Handy das Logo neben dem Namen im Kontakt-Editor, Sprache und Darstellung im Kontomenü.

**Umsetzung (Branch `feature/refinements-0.3.0`, 17.10.2026, autonom):**
- **Bestätigung (1):** `documents.confirms` (rein): bestätigend ist eine Änderung an `CONFIRMING`, außer sie fügt nur manuelle Tags hinzu oder entfernt sie; `change_metadata` liest dafür die Ermittlung der hinzugekommenen und entfernten Tags (REST `PATCH`, MCP `update_metadata`, Probelauf). Bestätigen im Posteingang und die Übernahme bleiben, wie sie sind.
- **Normalisieren nach dem Umhängen (2):** Ein neuer Elterntag (nicht die oberste Ebene) eines Dokument-Tags oder einer Gruppe reiht `tags.normalise` mit dem Tag ein, in derselben Unit of Work wie das Umhängen; Themen brauchen keinen (ein Dokument trägt eines, ein Kontakt darf ein Thema und eines darunter führen). Der Job läuft im Worker nach den Regelanwendungen in Schritten (`master_data.normalise_tagged`): Jeder Schritt hält die Sperre des Tag-Baums, liest den Baum, wie er dann ist, nimmt den Tag mit seinen Nachfahren und normalisiert bei Dokument-Tags die nächsten 200 Dokumente, die einen davon tragen (über `DocumentFilter`, nach ID geblättert), jedes eine eigene Änderung (`document.updated`, die den Sucheintrag erneuert); bei Gruppen die Kontakte bzw. Typen (deren Gruppen samt Vorfahren, die der Index hält, bleiben gleich). Der Schritt schließt seinen Job und reiht den nächsten (`after`) im selben Commit ein. Lane und `confirmed_at` bleiben; eine Zeile, die sich inzwischen änderte, scheitert an der Version, und der Schritt läuft erneut (fünf Versuche ab einer Minute, verdoppelt). Ohne Schlüssel gegen Doppel: jedes Umhängen hat seinen Job, ein zweiter Lauf ändert nichts.
- **Themenfilter (3):** `topic_id` in `GET /documents` und `/documents/search` wiederholbar (höchstens 50), mehrere „eines davon“, jedes mit den Themen darunter (der Kern konnte Mengen schon). MCP `search` mit `topics` (Namen, eines davon) und dem Thema je Treffer. Web: `Filters.topics` (`?topic=` wiederholt), statt des Auswahlfelds „Alle Themen“ ein „Thema hinzufügen“ wie bei Tags, gewählte Themen als eckige Chips zum Entfernen.
- **Gründe mit Namen (4):** Das Regelwerk nennt in Gründen und Notizen Kontakte, Typen, Themen, Spaces und Tags beim Namen und Feldwerte, wie sie geschrieben werden (`_Planner._shown`, `_value_text`: Text und Auswahl in Anführungszeichen, `12.50 EUR`, `2026-Q3`, Datum ISO, yes/no); die Gründe des Knotens `apply_rules` nennen ein Feld beim Namen statt `field:<id>` (`RulePlan.labels`). Die IDs bleiben in Werten, Vorschlägen und Wirkungen. Geprüft wurden alle Gründe von Regelwerk und Regelknoten; nur etwas, das es nicht mehr gibt („… no longer exists“), steht weiter mit seiner ID.
- **Logo für eine neue Domain (5):** `queue_logo_fetch` reiht auch für einen Kontakt mit abgerufenem Logo ein, für die erste neue Domain, die nicht scheiterte und von der das Logo nicht stammt (von Hand, angeheftet, gelernt); der Job ersetzt ein abgerufenes Logo durch ein gefundenes, findet er keines, bleibt das alte und die Domain wird vermerkt. `fetch-logos` fragt weiter nur für Kontakte ohne Logo; ein hochgeladenes wird nie ersetzt.
- **Logo-Jobs bei ausgeschaltetem Abruf (6):** `LogoService.run_next_job` nimmt den Job auch ohne Abruf und gibt ihn sofort auf (`failed`, Vermerk „not fetched: fetching logos is off (PAPIQ_LOGO_FETCH)“), ohne Warten und Wiederholung; der Knopf antwortet weiter 503.
- **Icon-Liste nur, wo nötig (7):** Chips und Typ-Icons laden die 170 Icons der Auswahl über eine kleine eigene Liste (`lib/icons/curated-modules.ts`, ein `import.meta.glob` über die wörtliche Liste, weil Vite sie so verlangt; ein Test hält sie gleich `curated.ts`), `lib/icons/load.ts` lädt für ein anderes Icon die Liste aller nach. Der offene Teil der Icon-Auswahl (`IconPickerPanel`: Bereiche, Suche, Suchwörter, Liste aller) lädt erst beim Öffnen. Größen des Builds (eigenes Verzeichnis, vorab geladenes JavaScript je Seite aus dem Manifest von Vite): vorher lud jede Seite der App die Liste aller (232 748 Bytes, gzip 46 916), `/documents` vorab 899 430 Bytes (gzip 280 989), Dokumentansicht 1 544 018 (477 800), Posteingang 875 295 (272 895), Stammdaten 896 639 (277 519); nachher keine Seite: `/documents` 680 097 (gzip 235 015), Dokumentansicht 1 324 834 (431 922), Posteingang 656 090 (226 894), Stammdaten 677 430 (231 528). Die kleine Liste 27 640 Bytes (gzip 7 613, mit den Vorlade-Listen), der offene Teil der Auswahl 20 847 (9 184), die Liste aller 225 248 (42 801). Build insgesamt 8 106 860 Bytes in 2 215 Dateien → 8 133 917 in 2 218.
- **Pfeiltasten in der Icon-Auswahl (8):** Die Kacheln sind ein Tab-Stopp (Roving Tabindex: das gewählte Icon, sonst das erste, danach das zuletzt fokussierte); die Pfeile bewegen den Fokus, links und rechts in Lesereihenfolge, hoch und runter in die Reihe darüber bzw. darunter zur nächstgelegenen Kachel, auch von einem Bereich in den nächsten (`lib/grid-keys.ts`, rein, die Reihen aus den Boxen der Kacheln, weil das Raster umbricht), Pos1 und Ende an Anfang und Ende der Reihe, mit Strg/⌘ aller Kacheln; Enter und Leertaste wählen (Knöpfe).
- **Handy (9):** Im Kontakt-Editor unter `sm` das Logo (56 px) neben dem Namensfeld, darunter kleine Pillen ohne Icons („Logo hochladen“, „Von der Website“, „Logo entfernen“; je 44 px Trefferfläche über ein Pseudo-Element) und die Herkunft, wie im Entwurf; ein neuer Kontakt zeigt die Initialen des getippten Namens. Am Handy stehen Sprache und Erscheinungsbild als Gruppen im Kontomenü (`LanguageChoices`, `ThemeChoices`, die auch die Menüs der Kopfleiste nutzen); am Desktop und auf der Anmeldeseite bleibt alles, wie es war.
- **Doku:** `architektur.md` (Training und Rückkopplung, Hierarchie, Kontakte › Logo, Suche › Filter, Regel-Engine › Konflikte, MCP, Stammdaten am Handy), `backend/README.md` (Klassifikator, Tags, Themen, Regeln, Logos, Worker, MCP, Endpunkte), `web/README.md` (Filter, Icons, Kopfleiste, Kontakt-Editor), `deploy/README.md` (ausgeschalteter Abruf).
- **Abweichungen und Auslegungen:** (1) Räumt das Normalisieren beim Setzen eines manuellen Tags einen automatischen Elterntag ab, berührt die Änderung einen automatischen Tag und bestätigt. (2) Das Normalisieren meldet je Dokument `document.updated` (auch an Webhooks und SSE), weil sich das Dokument ändert; das Protokoll nennt es nicht. (3) MCP-Treffer nennen jetzt auch ihr Thema. (4) Ein Thema heißt in Gründen bei seinem Namen, nicht beim Pfad. (5) Vermerkt wird bei (6) als gescheiterter Job: die Warteschlange kennt Vermerke nur dort. (6) Am Handy fehlt der Hinweis zu Dateitypen unter dem Logo (wie im Entwurf); die Reihenfolge der Knöpfe ist jetzt überall Hochladen · Holen · Entfernen (am Desktop vorher Hochladen · Entfernen · Holen). (7) Pos1/Ende wirken in der Reihe wie im Raster-Muster der APG, aller Kacheln mit Strg/⌘; an den Enden geht es nicht weiter.
- **Prüfung:** alle grün: Backend 3345 Tests (vorher 3333) mit Postgres, Garage und Meilisearch, ruff, ruff format, mypy, lint-imports, jeder Backend-Commit für sich; Web 676 Vitest in 73 Dateien (vorher 663 in 71), Lint, Format, Check, Build (in ein eigenes Verzeichnis, `web/build` der Devinstanz blieb unberührt); Migrationsclient 82 (unverändert). Bewertung mit festen Antworten: 83 Fälle, kein falsches Grün, keine Verstöße, Bericht bis auf die Zeitspalte gleich dem nach Paket 5. Neue Tests: `confirms` und der Service (manuelle Tags allein bestätigen nicht), Normalisieren auf Speicher und in der SQL-Suite auf SQLite und Postgres (Schritte zu 2 Dokumenten, Lane und Bestätigung bleiben, Gruppen von Kontakt und Typ, ein zwischen zwei Schritten zurückgehängter Tag bleibt unberührt) und im Worker, Themenfilter in Service, REST und MCP, Gründe mit Namen im Regelwerk, Logo neu für neue Domain (von Hand, angeheftet, gelernt; Upload bleibt) und sofortiges Ende ohne Abruf, Vitest für Filter-Chips, kleine Liste und Laden, `grid-keys`, Tastatur in der Auswahl, Kontakt-Editor und Kontomenü am Handy. Browser gegen den Build auf einer frischen Prüfinstanz (8001; API mit `PAPIQ_LOGO_FETCH=true`, Worker ohne, so fragt nichts das Netz): über die API 7 Prüfungen (Konfliktgrund mit Namen, manueller Tag bestätigt nicht, automatischer schon, Umhängen im Worker normalisiert, zwei Themen, Elternthema, Abruf angenommen und vom Worker sofort mit Vermerk beendet), im Browser 77 Prüfungen ohne Fehlschlag: zwei Themen über die Filterleiste (Desktop Deutsch hell, Handy Englisch dunkel; URL, Chips, Treffer, kein Überlauf), ein Icon außerhalb der Auswahl am Chip gezeichnet, Konfliktgrund mit Namen in der Dokumentansicht (Deutsch hell, Englisch dunkel), keine Liste aller Icons auf einer Dokumentseite mit Icons der Auswahl, Icon-Auswahl per Tastatur (ein Tab-Stopp, Tab auf das gewählte, Pfeil runter gleiche Spalte, Pos1/Ende, Enter wählt, der Fokus kehrt zum Knopf zurück; nach 1,5 s keine sichtbare Kachel ohne Icon, Desktop Englisch dunkel und Handy Deutsch hell), bei 360, 375 und 390 px in Deutsch hell und Englisch dunkel Kopfleiste ohne Überlauf und ohne Sprach- und Farbknopf, Sprache und Erscheinungsbild im Kontomenü innerhalb des Bildschirms, Logo neben dem Namen und Pillen darunter in einer Reihe mit 44 px Trefferfläche, Logo hochladen und entfernen am Handy, kein Überlauf; Desktop mit Sprach- und Farbknopf in der Kopfleiste. Keine Konsolenfehler außer der 401 vor der Anmeldung, keine CSP-Verstöße. Dabei gefunden und behoben: die beiden Knöpfe unter dem Logo brachen am Handy in zwei Zeilen um (jetzt kleine Pillen ohne Icons, kurzer Text), der lange Hinweis machte den Block hoch.
- **Nach dem Review (separater Agent, nur lesend; nichts Schweres):** (1) Der Job las den Baum einmal und nutzte ihn für alle Seiten ohne Sperre: Wurde der Tag währenddessen zurückgehängt, strichen die übrigen Seiten den früheren Elterntag trotzdem. Jetzt ist jeder Schritt (200 Dokumente) ein eigener Job, der die Sperre des Baums hält und ihn neu liest, und den nächsten im selben Commit einreiht; so deckt eine Lease auch nur einen Schritt (Test auf Speicher: zurückgehängt zwischen zwei Schritten). (2) Ein Test prüfte die Bestätigung nur scheinbar (das Dokument war nie bestätigt); jetzt mit einer bestätigenden Änderung vorab. (3) Docstrings, die nur Kontakte ohne Logo nannten, nennen auch solche mit abgerufenem. (4) Der kurze Knopf „Von der Website“ heißt für Screenreader „Von der Website holen“; nach der Wahl eines Icons kehrt der Fokus zum Knopf der Auswahl zurück (vorher fiel er auf die Seite); lädt der offene Teil der Auswahl nicht, sagt sie das (`icon_load_failed`) statt leer zu bleiben; der Test prüft, dass Tab die Kacheln verlässt. Ohne Änderung: die Bestätigung beim Abräumen eines automatischen Elterntags (Auslegung 1), die Reihenfolge der Knöpfe am Desktop (Auslegung 6). Prüfung danach: Backend 3345 Tests, Web 676 Vitest, Lint, Format, Check, Build; Browser auf frischer Prüfinstanz erneut (7 + 77 Prüfungen, keine Konsolenfehler außer der 401, keine CSP-Verstöße).
- **Entschieden (Tobi, 17.10.2026), alles bleibt, wie gebaut:** (1) Webhooks und SSE hören das Normalisieren als `document.updated`. (2) Ein Thema heißt in Gründen bei seinem Namen. (3) Am Handy kein Hinweis zu Dateitypen unter dem Logo. (4) Pos1/Ende in der Reihe, mit Strg/⌘ über alle Kacheln.

#### Abschluss des Release

- PRs je Paket gegen `release/0.3.0` (Tobi, 17.10.2026: „für jedes Paket einen PR, dann repariere die Notes nachträglich“): #84 (Spaces, `refactor`), #85 (Felder von Hand, `enhancement`), #86 (Tags, `new-feature`), #87 (Themen, `new-feature`), #88 (Icons, Farben, Logos, `new-feature`), #89 (Tags-Menü, Handy, `enhancement`), #90 (Nachbesserungen, `enhancement`); CI grün, per Fast-Forward der Reihe nach gemergt. Dazu „After the update to 0.3“ in `backend/README.md` und ein Satz in `deploy/README.md` (Tobi: ja).
- `release/0.3.0` → `main` als PR mit `skip-changelog` (die Pakete tragen die Release Notes), Merge-Commit; Tobi veröffentlicht 0.3.0.
- Offen für spätere Releases: Umbau der Rechte; Migrationsclient mit ASN, Tag-Hierarchie und Farben aus Paperless; Personen als Nutzer, die ein Dokument betrifft.

### M16 – Lesen

- `PageRenderer` statt `PreviewRenderer`: alle Seiten als WebP-Derivate; Endpunkt `GET /documents/{id}/pages/{n}`.
- `DocumentParser` mit Betriebsarten `ocr`, `vlm`, `auto` (`PAPIQ_READ_MODE`); Docling-VLM-Pipeline lokal (`granite-docling-258M`) und entfernt über einen OpenAI-kompatiblen Endpunkt; Qualitätsmaß je Lesung im Protokoll.
- Hinweis (14.10.2026): Mit dem Layout-Text statt Docling entfällt die Docling-VLM-Pipeline; das VLM als Leser schwer lesbarer Seiten über Open WebUI ist Issue #53.
- Hinweis (15.10.2026): Das VLM liest auf Wunsch einer Person als Texterkennung (Zwischenschritt oben); eine Betriebsart `auto`, die selbst zum VLM greift, entfällt (Tobi: immer eine Entscheidung eines Menschen).
- `LanguageModel` mit Bildern in der Anfrage (`PAPIQ_LLM_VISION_MODEL`, `PAPIQ_LLM_PAGES`).
- Messungen: granite-docling auf dem NUC je Seite; Vision-Modelle über OpenWebUI (Claude, Groq); Bewertungssatz um Seitenbilder erweitert.

**Fertig, wenn:** Ein Scan ohne Textschicht und ein Formular aus dem Bewertungssatz werden in `vlm` gelesen; `auto` wählt die VLM-Lesung nur bei schwacher Tesseract-Lesung; die Laufzeiten stehen im Bericht.

**Modell:** Sonnet 5.5 · high.

### M17 – Grounding

- Zitate der Signale und Felder auf Seite und Box abgebildet (Docling-Provenienz); Prüfansicht zeigt die Seite mit Hervorhebung je Wert.
- Seitenansicht aus den WebP-Derivaten, PDF.js bleibt für das Archiv.

**Fertig, wenn:** In der Prüfansicht führt jeder Wert mit Zitat zur Stelle auf der Seite.

**Modell:** Sonnet 5.5 · medium.

**Vorgezogen mit der neuen Dokumentansicht (Tobi, 13.10.2026; Branch `feature/document-view-0.2.0`, ein PR):** Entwurf als Design-Fläche „Papiq Dokumentansicht“ (Artifact, privat). Entscheidungen:

- Fundstellen aus der Textebene des Archiv-PDFs mit PDFium statt aus der Docling-Provenienz: Docling kennt nur Boxen je Textblock, PDFium die Zeichen des Zitats. Neuer Port `TextLocator` mit In-Memory-Adapter und Vertragstest; Klassifizierung und Felder legen je Zitat Seite und Boxen im Ergebnis ab (`/review`, Protokoll), Bestandsdokumente erst nach Neuverarbeitung. Zu jedem Zitat die Angabe, ob es den Wert selbst zeigt; die UI unterscheidet Zitat, Abgeleitet und Kein Beleg.
- Seitenansicht: PDF.js auf dem Archiv mit Overlay; bis das Archiv da ist, die WebP-Seitenbilder ohne Markierung. Grund: OCRmyPDF dreht und entzerrt Seiten (`--rotate-pages`, `--deskew`), die WebP-Bilder entstehen aus dem Original, Boxen des Archivs passen auf sie nicht.
- Eine Dokumentansicht für Posteingang und Dokumente (`/documents/{id}`, `/inbox/{id}` leitet weiter) mit den Tabs Details, Inhalt, Protokoll. Details: Seiten links, rechts Grunddaten, Globale Felder und „Felder für ‹Typ›“ als getrennte Gruppen; zu prüfende Felder als große Karten; Überfahren markiert die Fundstelle, ein Klick auf die Quellenangabe springt hin. Bestätigen im Posteingang mit „danach das nächste Dokument öffnen“. In den Stammdaten bleibt „Felder“ mit „Gilt für“, kein neuer Oberbegriff.
- Inhalt: Text (Markdown gerendert mit `marked`, nur der Lexer, Ausgabe ohne `{@html}`), Markdown-Quelle, Docling-JSON über neu `GET /documents/{id}/structure`.
- Protokoll: Graph und Protokoll in einer Liste je Schritt, Uhrzeit vorne; Info-Icon mit allen Token-Angaben des Anbieters (`StructuredAnswer` übernimmt das ganze `usage`); Fenster „Prompt“ mit System, Nachricht und Antwort, Tokens hinter der Zeichenzahl (Eingabe-Tokens je Teil geschätzt). Die Prompts liegen je Knoten und Lauf im Objektspeicher (`documents/{id}/exchanges/<knoten>.json`, alle Läufe), neu `GET /documents/{id}/exchanges/{node}`.
- Menü: Posteingang, Dokumente, Schubladen, Regeln, Webhooks | Stammdaten, Nutzer; Suche als Feld oben rechts (`/`), Startseite Posteingang.
- Behoben: Der PDF-Viewer zeichnete nur die erste Seite (der Observer nutzte den Callback der ersten Seite für alle).

**Umsetzung und Review (13.10.2026, Branch `feature/document-view-0.2.0`):** Backend: `StructuredAnswer.usage` mit dem ganzen Bericht des Anbieters (je Versuch in `output.tokens`); Prompts je Knoten und Lauf unter `documents/{id}/exchanges/<knoten>.json` (in `derivative_keys`, Fehler beim Ablegen lassen den Knoten nie scheitern), `GET /documents/{id}/exchanges/{node}` (Besitzer und Admins), `GET /documents/{id}/structure` (Leserecht, 409 vor dem Auslesen); Port `TextLocator` mit PDFium-Adapter (Abgleich erst mit angeglichenem Leerraum, Trennstrichen am Zeilenende und Groß- und Kleinschreibung, dann nur auf Buchstaben und Ziffern; höchstens fünf Stellen je Zitat; Wortboxen der OCR-Textebene zu einer Box je Zeile zusammengefasst; Seitendreh beachtet; unlesbare Seiten übersprungen), Fake und Vertragstest; `FieldCheck.locations` und `quote_shows_value` (gleiche Prüfung wie gegen den Text, nur gegen das Zitat; Ja/Nein zeigt nie den Wert). Web-UI: Menü und Suchfeld (`/`), Startseite Posteingang, eine Dokumentansicht mit Tabs, Prüfkarten mit den drei Belegfällen, Markieren und Springen, Bestätigen mit „danach das nächste Dokument“ (auch im Posteingang aller Nutzer), Inhalt mit `marked` 18.0.14 (neue Abhängigkeit, nur der Lexer), Protokoll mit Token-Tooltip und Prompt-Fenster, Token `--surface`. Tests: 2260 Backend-Unit-Tests, 352 Vitest; Integrationstests ohne Docling 228 grün. Im Browser geprüft (Chromium über Playwright gegen eine eigene API auf Port 8001 mit den Entwicklungsdaten und einem Ersatz-Modell): ein gescannter Zweiseiter durch Texterkennung, Docling und PDFium, Markieren, Springen auf Seite 2, abgeleitetes Zitat gestrichelt, Inhalt, Protokoll, Prompt, Bestätigen mit Wechsel zum nächsten Dokument, Dark Mode, Handybreite; keine Konsolenfehler, keine CSP-Verstöße. Das Review durch einen separaten Agenten fand und behoben vor dem PR: Bestätigen fehlte bei gelben oder roten Dokumenten mit Status `completed` (Ablage wartet auf die Schublade), ein fehlgeschlagenes Dokument ohne offene Felder ließ sich nicht mehr ändern; nach einer Größenänderung zeichnete der Viewer keine Seiten mehr; das Protokoll lud bei jedem Ereignis neu; doppelte Token-Schlüssel bei zwei Versuchen; nach einem korrigierten Typ gingen Vorschläge als Werte des Besitzers mit; Markierungen konnten auf dem Original statt dem Archiv liegen; Admins mit „Alle Nutzer“ landeten nach dem Bestätigen bei einem fremden Dokument; das Ablegen der Prompts und das Suchen der Stellen konnten den Knoten scheitern lassen.

Nach Tobis Review (13.10.2026): Tabellen der Textansicht in voller Breite; Markdown ohne HTML-Entities (Entscheidung B und C): Docling exportiert mit `escape_html=False`; Textprüfung (`normalise`, damit auch Namensabgleich und Textbedingungen der Regeln) und Fundstellen lösen Entities älterer Texte auf, ohne Neuverarbeitung. Ein `|` in der Zelle einer von Docling erkannten Tabelle bleibt Doclings `&#124;` (die Leser des Texts lösen es auf); das Ersetzen im Markdown-Text (`\|`) ist verworfen.

Text in fester Zeichenbreite (Tobi, 13.10.2026, Variante 2): Ausdrucke aus Fachprogrammen (Zeitnachweis, Liste) richten Spalten mit Leerzeichen aus und zeichnen Tabellen aus `|` und `-`; Docling las sie als Absätze und Tabellen mit verschobenen Zellen. Jetzt setzt der Docling-Kindprozess solche Stellen aus den Zeichen der Textebene neu zusammen (PDFium, `adapters/outbound/pdfium/fixed_pitch.py`): mindestens drei aufeinanderfolgende Zeilen, deren Zeichen alle in Schriften fester Breite mit gleichem Vorschub stehen, werden ein Block; jedes Zeichen kommt in seine Spalte, ein weiter Zeilenabstand wird eine Leerzeile. Feste Breite ist gemessen (gleicher Vorschub über mindestens fünf verschiedene Buchstaben), sonst entscheidet das FixedPitch-Flag der Schrift; ein Testdokument hatte Courier ohne Flag. Im DoclingDocument ersetzt je Block ein Code-Element die Elemente, die ganz im Block liegen (`adapters/outbound/docling/fixed_pitch.py`); das Markdown hat dort einen Codeblock, JSON und Markdown bleiben gleich. Ragt ein Element nur teilweise hinein (eine Tabelle mit Kopf in anderer Schrift), bleibt Doclings Lesung der Stelle ganz erhalten. Nicht betroffen: Scans (die OCR-Textebene ist proportional gesetzt), gedrehte Seiten, Tabellen in Proportionalschrift. In der Textansicht ist Code wörtlich (keine Entities aufgelöst). Neues Beispiel `tests/samples/printout.pdf`; geprüft an Tobis Zeitnachweis (beide Seiten spaltengenau) und an den 20 Dokumenten des M13-Probelaufs (zwei weitere Treffer, einer davon bleibt bei Doclings guter Tabelle).

Behoben auf diesem Branch (Tests aus M14, auf `release/0.2.0` ebenso rot): `TestPostgresMigrations::test_attributes_become_fields_and_back` las die Knotenzustände in JSONB-Schlüsselordnung statt Graphordnung; `test_api_end_to_end.py` erwartete die Läufe ohne den Knoten `pages`, der neben der Texterkennung läuft; `test_search_end_to_end.py` änderte ein Dokument, während noch Knoten liefen (gelegentlich 409). Alle 240 Integrationstests grün, mit Docling, auf SQLite und Postgres. Bestandsdokumente bekommen Stellen, Prompts und den ganzen Token-Bericht erst mit einer Neuverarbeitung (kein Nachhol-Befehl). Die Eingabe-Tokens je Teil des Prompts sind nach Zeichen geschätzt. Überfahren scrollt nicht zu einer Stelle außerhalb der Sicht (nur der Klick auf die Seite). Gründe der Prüfungen kommen weiter englisch aus der API.

### M18 – Eskalation und Kalibrierung

Aus dem M15-Echtlauf (`reviews/M15-lanes.md`): wie stark ähnliche Dokumente widersprechen dürfen. Gleichförmige Formulare (Lohnsteuerbescheinigungen verschiedener Arbeitgeber) stimmen einstimmig für einen anderen Absender; eine Mindestähnlichkeit oder ein geringeres Gewicht der Nachbarn gegen Namen und Kennungen im Text ist am Rückkopplungsmaß zu kalibrieren (Tobi, 14.10.2026).

- Bei Widerspruch zwischen Signalen ein zweiter Aufruf mit den Kandidaten an ein stärkeres Modell (konfigurierbar, optional).
- Schwellen des Klassifikators und des Namensabgleichs am Rückkopplungsmaß kalibriert; Bericht über mehrere Läufe.

**Fertig, wenn:** Das Rückkopplungsmaß je Feld liegt im Echtlauf unter der festgelegten Grenze, und der Anteil Gelb sinkt gegenüber M15 messbar.

**Modell:** Opus 5.5 · high.

## Devcontainer

Entwickelt und getestet wird im Devcontainer; er startet alle Abhängigkeiten per Docker Compose mit.

**Dienste** (`.devcontainer/compose.yml`)

| Dienst | Zweck |
| --- | --- |
| `dev` | Arbeitsumgebung: Ziel `dev` aus dem Papiq-Dockerfile, also dieselben Systempakete wie im Betrieb (Tesseract mit Deutsch und Englisch, Ghostscript u. a.); dazu Python 3.13 mit uv, Node.js mit pnpm |
| `postgres` | Datenbank für Postgres-Tests |
| `garage` | S3-Speicher; ein Init-Schritt legt Layout, Schlüssel und Bucket an |
| `meilisearch` | Suche |

**Weitere Punkte**

- Funktion „Docker outside of Docker", damit sich aus dem Devcontainer das Papiq-Image bauen und starten lässt (M9).
- Weitergeleitete Ports: API, Vite-Entwicklungsserver, Meilisearch, Garage.
- Nach dem Erstellen: `uv sync` und `pnpm install` automatisch.
- Tests: Kern-Tests mit In-Memory-Adaptern; Vertragstests gegen SQLite, Postgres, Dateisystem, Garage und Meilisearch aus dem Compose-Netz.
- LLM: kein Modell im Devcontainer. Tests nutzen einen Fake-Adapter; für echte Läufe zeigt `PAPIQ_LLM_BASE_URL` auf ein vorhandenes Ollama (z. B. auf dem Host über `host.docker.internal`). Docker auf macOS hat keinen GPU-Zugriff, ein Ollama im Container wäre dort langsam.
- Alle Images müssen amd64 und arm64 unterstützen.
- Versionen der Abhängigkeiten werden gepinnt.
