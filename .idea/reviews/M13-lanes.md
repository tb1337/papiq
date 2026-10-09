# Dokumentlauf M13: zehn Originale mit Sprachmodell

Datum: 2026-10-09. Entscheidung 6 (`prompts/M13.md`): zehn Originale aus der Paperless-Testinstanz
(Auszug der Produktion), quer über Dokumenttypen und Kontakte, in eine Wegwerf-Umgebung
(Datenbank `papiq_trial`, Dateisystem-Speicher, Index `papiq-trial-documents`, ein Worker) normal
per API hochgeladen (Kanal `web`, ohne Metadaten). Die Paperless-Werte sind das Soll. Vorher wurden
die Stammdaten aus Paperless angelegt (86 Kontakte, 16 Dokumenttypen, 10 Tags, 14 Felder, alle
global), wie es nach einer Übernahme der Fall wäre. Keine Titel und keine Personennamen in diesem
Bericht; die Auswahl steht als Paperless-IDs.

## Verlauf und Modell

- **Ollama (`https://ollama.tbsch.me`, `qwen3:8b-ctx8k`, CPU):** Der erste Abschnitt (fünf
  Dokumente, zwei Worker) endete komplett rot: um 12:19 Uhr antwortete der Dienst für alle offenen
  Anfragen (auch Embeddings) mit **502 Bad Gateway**, zwei parallele Klassifizierungen liefen in
  das Zeitlimit von 600 s. Wiederholung mit einem Worker: zwei weitere 502-Ausfälle (12:40, 13:09,
  jeweils länger als die drei Versuche mit 30/60 s Abstand), dazwischen normale Antworten
  (Klassifizierung 219 s und 349 s, Felder 390 s je Dokument). Ursache nicht geklärt
  (Neustart von Ollama auf dem NUC?); ein Dokument wurde so gelb, der Rest blieb hängen.
- **Groq (`openai/gpt-oss-120b`, ab 13:21 Uhr, Tobis Entscheidung):** Antworten in 2 bis 4 s je
  Anfrage. Zwei Dinge waren nötig: `PAPIQ_LLM_RESPONSE_FORMAT=json_schema` (mit `json_object`
  hielt das Modell das Schema nicht ein, Feld `contact_evidence` statt des Aufbaus der Antwort)
  und ein kleineres Prompt-Budget (`PAPIQ_LLM_INPUT_BUDGET=6000`, `PAPIQ_STEP_RETRY_DELAY=70`),
  weil die Gratis-Stufe **8000 Token pro Minute** je Modell erlaubt: 16 Antworten `429`, von den
  Wiederholungen aufgefangen; ein Dokument wurde dadurch einmal rot und beim Wiederholen gelb.
  Alle drei verfügbaren Modelle (gpt-oss 120b/20b, qwen3.8-27b) haben dieses Limit. Im Mittel
  etwa zwei Minuten je Dokument, fast nur Wartezeit auf das Limit.
- Embeddings liefen weiter über Ollama (Abschnitte je Dokument, ohne Befund).

## Lanes

| Lane | Anzahl | Gründe |
| --- | --- | --- |
| grün | 1 | Kontoauszug: Kontakt und Typ erkannt, Datum geprüft |
| gelb | 9 | 7× Kontakt: kein bestehender Kontakt passt zum vorgeschlagenen Namen (neuer Kontakt vorgeschlagen) oder kein Kontakt erkannt; 1× Kontakt kommt nicht im Text vor; 1× Dokumenttyp: neuer Typ vorgeschlagen; 1× Datum nicht erkannt; 2× Datumsfeld gleich Dokumentdatum (nur Vorschlag); 1× Betrag in deutscher Schreibweise abgelehnt (`2.111,68`) |
| rot | 0 | (vorübergehend 5 durch Ollama-Ausfall, 1 durch das Groq-Limit; nach Wiederholung gelb) |

## Trefferquote je Feld gegen Paperless

| Feld | Treffer | von | Bemerkung |
| --- | --- | --- | --- |
| Dokumenttyp | 8 | 10 | ein „Dokument“ in Paperless wurde als „Kontoauszug“ erkannt (passender als das Soll); einmal neuer Typ „Sammelbestätigung“ statt „Spendennachweis“ |
| Kontakt | 2 | 10 | Vorschläge sind meist richtig, aber nicht der Paperless-Name: „ING-DiBa AG“ statt „ING“, „Scalable Capital GmbH“ statt des Kurznamens; bei Gehalts- und Zeitnachweisen nennt das Modell den Empfänger statt den Absender; der Abgleich ist buchstabengenau mit Ähnlichkeitsschwelle 0,75 |
| Dokumentdatum | 5 | 10 | drei Abweichungen um einen Tag (Paperless „erstellt“ gegen das Datum im Dokument), zwei nicht erkannt |
| Tags | 1 | 3 | nur drei Dokumente haben Tags in Paperless |
| Felder | 0 | 16 | nicht aussagekräftig: die Testinstanz trägt Testwerte („Das ist ein Text“, Zufallstext, Verweise auf andere Dokumente), die kein Modell aus dem Dokument lesen kann; siehe Befund unten |
| Titel | 0 | 10 | Papiqs Titel kommt vom Modell, Paperless-Titel sind von Hand; kein Maß |

## Laufzeit je Schritt (Sekunden, alle Läufe)

| Schritt | Median | Max | Läufe |
| --- | --- | --- | --- |
| OCR | 1,6 | 7,5 | 10 |
| Parsen (Docling) | 22,8 | 48,7 | 10 |
| Klassifizieren | 3,1 (Groq) | 600 (Ollama, Zeitlimit) | 16 |
| Felder | 2,2 (Groq) | 390 (Ollama) | 14 |

Ollama auf der CPU: 219 bis 390 s je Modellanfrage. Groq: 2 bis 4 s, Durchsatz durch 8000
Token/min begrenzt.

## Befunde

1. **Globale Felder werden aus jedem Dokument extrahiert.** Alle 14 Felder sind global (so
   legt die Migration Paperless-Custom-Fields an, Paperless kennt keine Zuordnung zum Typ). Das
   Modell füllte deshalb „Langer Text“, „Text“, „Währung“, „Zahl“, „LA_Brutto“ auch bei
   Rechnungen und Kontoauszügen mit irgendeinem Wert aus dem Text, geprüft und „ok“, weil der
   Wert im Text vorkommt. Für den Betrieb: Felder nach der Übernahme Dokumenttypen zuordnen,
   sonst entstehen bei jedem Upload Werte in allen Feldern. Vorschlag für die Übergabe: die
   Migration könnte Custom Fields, die in Paperless nur bei einem Typ vorkommen, diesem Typ
   zuordnen (Nach 0.1).
2. **Beträge in deutscher Schreibweise.** `2.111,68` wird als Betrag abgelehnt (gelb mit Grund).
   Das Modell gibt den Wert, wie er im Text steht. Vorschlag: `1.234,56` und `1,234.56` beim
   Prüfen eines Betrags normalisieren, wenn eindeutig (Nach 0.1).
3. **Kontaktabgleich.** Der Vorschlag ist meist der volle Firmenname aus dem Briefkopf, in
   Paperless steht ein Kurzname. Eine Übernahme mit Kurznamen führt so zu vielen Gelb mit
   „neuer Kontakt“; hilfreich wären Aliasse je Kontakt (Nach 0.1) oder Kurznamen schon in
   Paperless. Bei Gehalts- und Zeitnachweisen wählt das Modell den Empfänger als Kontakt; die
   Prüfung lässt das zu, wenn der Name im Text steht (Restrisiko wie in M5 beschrieben).
4. **Dokumentdatum ±1 Tag.** Paperless „erstellt“ weicht dreimal um einen Tag vom Datum im
   Dokument ab (vermutlich Zeitzone beim Import in Paperless). Keine Änderung nötig.
5. **Zwei Modellanfragen je Dokument** mit zusammen 4000 bis 8000 Token: bei Gratis-Stufen mit
   Token-Limits je Minute reicht ein Worker; `PAPIQ_LLM_INPUT_BUDGET` ist die Stellschraube.
6. **Ollama über den Reverse Proxy** ist für Massenläufe unzuverlässig (drei 502 in einer
   Stunde). Für die Übernahme des Archivs bleibt das Modell aus (übernommene Metadaten), die
   Embeddings entstehen danach in einem `reindex`.

## Umgebung

Trial-Einstellungen: `/tmp/m13/trial/trial.env` im Devcontainer (Kopie von `live.env` mit
eigener Datenbank, Dateisystem-Speicher unter `/tmp/m13/trial/objects`, Index
`papiq-trial-documents`, API auf Port 8001), Skript `/tmp/m13/trial/evaluate.py` (`seed`,
`upload`, `report`), Originale und Paperless-Metadaten unter `/tmp/m13/trial/docs`. `/tmp` ist
mit dem nächsten Neustart des Containers weg; die Datenbank `papiq_trial` bleibt, damit Tobi
weitere Dokumente dort testen kann (Entscheidung 6).
