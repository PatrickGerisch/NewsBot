# News-Bot in der GitHub-Cloud (Paper) — Einrichtung

Dieser Ordner ist ein fertiges Paket, das deine beiden Bots **stündlich in der GitHub-Cloud** laufen lässt — unabhängig davon, ob dein Surface an ist oder nicht. GitHub führt den Zeitplan aus, die Orders gehen weiterhin nur an dein **Alpaca-PAPER-Konto** (kein echtes Geld).

Einmal einrichten (ca. 15 Min), danach läuft es von allein. Vom Handy schaust du nur noch aufs Depot.

---

## Was drin ist

- `news_bot.py`, `news_bot_alpaca.py`, `universe.py` — deine Skripte (unverändert)
- deine aktuellen State-/Cache-Dateien — damit die Cloud **nahtlos** dort weitermacht, wo du stehst
- `.github/workflows/news-bot.yml` — der Zeitplan (stündlich zur US-Handelszeit)
- `requirements.txt`, `.gitignore` — Technik-Kram, nichts zu tun

**Wichtig:** Die API-Schlüssel sind **nicht** im Paket. Die kommen in Schritt 3 als GitHub-Secrets rein — sicher und nicht im Code sichtbar.

---

## Voraussetzungen

1. Ein **GitHub-Konto** (kostenlos): https://github.com/signup
2. Deine **Alpaca-Paper-Schlüssel**. Die stehen bereits in `alpaca_keys.env` in deinem Projektordner (Zeilen `APCA_API_KEY_ID=...` und `APCA_API_SECRET_KEY=...`). Alternativ neu erzeugen: app.alpaca.markets → oben auf **Paper** umstellen → *Home* → *API Keys* → *Generate*.

---

## Schritt 1 — Privates Repo anlegen

Auf GitHub oben rechts **+** → **New repository**.
- Name z. B. `news-bot`
- **Private** auswählen (wichtig — dein Depot soll nicht öffentlich sein)
- **kein** README/gitignore ankreuzen (haben wir schon)
- **Create repository**

Die nächste Seite zeigt eine URL wie `https://github.com/DEIN-NAME/news-bot.git` — die brauchst du gleich.

## Schritt 2 — Dateien hochladen

**Variante A — GitHub Desktop (einfachste, GUI):**
1. https://desktop.github.com installieren, mit GitHub-Konto anmelden.
2. *File → Add local repository* → diesen Ordner `news-bot-cloud` wählen. (Fragt es nach „create a repository", zustimmen.)
3. Unten *Summary* eintragen, **Commit** klicken, dann oben **Publish/Push** zum Repo aus Schritt 1.

**Variante B — Kommandozeile (Git muss installiert sein):**
```bash
cd "C:\Users\patrick\...\Bachelorarbeit\news-bot-cloud"
git init
git add .
git commit -m "News-Bot initial"
git branch -M main
git remote add origin https://github.com/DEIN-NAME/news-bot.git
git push -u origin main
```
Beim ersten Push öffnet sich ein GitHub-Login im Browser — bestätigen.

> Der Ordner `.github/workflows/` mit der `news-bot.yml` muss mit hochgeladen werden (ist er, beide Varianten nehmen ihn automatisch mit).

## Schritt 3 — API-Schlüssel als Secrets hinterlegen

Im Repo: **Settings** → links **Secrets and variables** → **Actions** → **New repository secret**. Zwei Stück anlegen:

| Name | Wert |
|---|---|
| `ALPACA_API_KEY_ID` | dein Key-ID (aus `alpaca_keys.env`) |
| `ALPACA_API_SECRET_KEY` | dein Secret-Key |

Name **exakt** so schreiben. Secrets sind danach nicht mehr lesbar — nur überschreibbar. Das ist normal.

## Schritt 4 — Ersten Lauf starten & prüfen

1. Reiter **Actions** → ggf. „I understand… enable workflows" bestätigen.
2. Links **News-Bot (Paper)** → rechts **Run workflow** → **Run workflow**.
3. Nach ~1–2 Min den Lauf öffnen. Ganz unten unter **Summary** siehst du die `RUN …`-Zeilen beider Bots. Grüner Haken = lief durch.
4. Gegencheck: app.alpaca.markets (Modus **Paper**) zeigt dasselbe Depot.

Ab jetzt läuft der Bot **automatisch stündlich** Mo–Fr zur US-Handelszeit. Nichts weiter zu tun.

## Schritt 5 — Lokalen Cowork-Task pausieren (WICHTIG)

Damit nicht **zwei** Läufer gleichzeitig Orders auf dasselbe Alpaca-Paper-Konto schicken, den bisherigen stündlichen Cowork-Task **pausieren/löschen**, sobald GitHub läuft. Sonst kämpfen beide um dieselben Positionen. Sag mir Bescheid, dann deaktiviere ich ihn — oder du machst es in den geplanten Aufgaben selbst.

---

## Vom Handy beobachten

- **Alpaca-App** oder app.alpaca.markets (Modus **Paper**) → Live-Depot, egal wo der Bot läuft.
- **GitHub-App** oder github.com → Repo → **Actions** → jeder Lauf mit Zusammenfassung; „Run workflow" startet auch manuell einen Lauf.

## Gut zu wissen

- **Kein echtes Geld.** `paper=True` ist fest verdrahtet; die Skripte handeln nur Papiergeld.
- **Kosten:** Bei einem privaten Repo sind 2000 Action-Minuten/Monat gratis — dieser Bot braucht davon nur ~200–400. Reicht locker. (Öffentlich wäre unbegrenzt, aber dann wären deine Positionen sichtbar — bleib bei privat.)
- **Zeitplan ändern:** in `.github/workflows/news-bot.yml` die `cron`-Zeile anpassen (UTC!). Häufiger: z. B. `*/30 13-21 * * 1-5` = alle 30 Min.
- **Pausieren:** Actions-Tab → Workflow → `•••` → *Disable workflow*.
- **Verzögerung:** GitHubs Zeitplan kann sich in Stoßzeiten um ein paar Minuten verschieben oder mal einen Lauf auslassen — für einen stündlichen Paper-Bot unkritisch.
- **State:** Nach jedem Lauf committet der Workflow die aktualisierten State-/Cache-Dateien zurück ins Repo — so bleibt der Verlauf über Läufe hinweg erhalten.

Kein Anlagerat — reines Mess-Experiment mit Papiergeld.
