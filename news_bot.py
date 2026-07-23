# -*- coding: utf-8 -*-
"""
NEWS-BOT (Paper) – nachrichten- & social-getriebenes Swing-Trading, long & short
=================================================================================
Realistisches Papiergeld-Modell (kein echtes Depot, keine Broker-Anbindung):

* INTRADAY: Läuft mehrmals täglich WÄHREND der US-Handelszeit (9:30–16:00 ET).
  Bei jedem Lauf werden Schlagzeilen (Yahoo Finance) + StockTwits-Social-Sentiment
  (Bullish/Bearish je Ticker, einflussreiche Accounts doppelt) + 5-Tage-Momentum
  ausgewertet und Long-/Short-Positionen eröffnet/geschlossen. Außerhalb der
  Handelszeit wird NUR bewertet (mark-to-market), nicht gehandelt.
* KURSE: Handel/Bewertung im Handel zum aktuell verfügbaren (~15 Min verzögerten)
  Intraday-Kurs; Momentum auf ABGESCHLOSSENEN Tages-Schlusskursen (kein halbfertiger
  Bar, kein Look-ahead).
* KOSTEN (voll realistisch): Kommission je Trade + Slippage/Spread je Seite +
  tägliche Leihgebühr auf offene Short-Positionen.
* AUDIT: Jeder Lauf schreibt einen Zeitstempel-Snapshot (news_bot_runs.csv);
  jeder Trade Fill-Kurs + Kosten; Entscheidungen mit auslösender Meldung.
* Regeln: KEIN festes Positionslimit – ALLE Assets mit klarem Signal (|Score| ≥ 3)
  werden gehalten, gleichgewichtet (equity ÷ Anzahl), also stets ~100 % investiert
  und OHNE Hebel. Neue Signale trimmen bestehende Positionen entsprechend.
  Haltedauer ≤ 5 Handelstage. Benchmark: Buy & Hold S&P 500. Erwartung laut allen
  Tests: Unterrendite nach Kosten – genau das wird hier ehrlich gemessen. Kein Anlagerat.
"""
import json, os, csv
from datetime import datetime, timezone, timedelta, time as dtime

import universe as U                    # Aktien-Universum (S&P 500 + DAX) + Tages-Cache

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:                       # Fallback: fixe EDT-Verschiebung
    ET = timezone(timedelta(hours=-4))

# --- Handelbares Universum: alle S&P-500- + DAX-40-Titel (gleiche Kriterien) ---
ASSETS = U.FULL_UNIVERSE               # ~540 Titel; SPY dient nur als Benchmark
BENCH  = U.BENCH                       # 'SPY' – Buy & Hold Vergleich, nicht gehandelt
START = 10_000.0

# --- Skalierung auf ein grosses Universum (Details siehe universe.py) ---
# Momentum kommt aus dem Tages-Cache (intraday konstant); die teuren News-/
# StockTwits-Abfragen laufen NUR fuer die staerksten Momentum-Bewegungen.
TOPN = 24                              # so viele Kandidaten bekommen News + Social
REFRESH_BUDGET = 10.0                  # Sek./Lauf fuer inkrementelle Cache-Auffrischung
REFRESH_CHUNK = 20                     # Titel je yfinance-Batch beim Auffrischen
PARALLEL = 20                          # parallele Threads fuer News-/StockTwits-Abrufe

# --- Kostenmodell (voll realistisch) ---
COMMISSION = 0.0005          # 5 bps je Trade
SLIP = 0.0005                # 5 bps Slippage/Spread je Seite (Kauf teurer, Verkauf billiger)
BORROW_ANNUAL = 0.03         # 3 % p.a. Leihgebühr auf Short-Notional
BORROW_DAILY = BORROW_ANNUAL / 252

MAXTAGE = 5                  # max. Haltedauer in Handelstagen
BAND = 0.10                  # Rebalancing-Deadband: nur handeln, wenn Abweichung
                             # > 10 % des Zielgewichts (bremst stündlichen Mikro-Churn)

POS_WORTE = ['rate cut', 'stimulus', 'beats', 'beat estimates', 'surge', 'rally',
             'deal', 'agreement', 'record high', 'upgrade', 'strong growth',
             'cooling inflation', 'soft landing', 'breakthrough', 'optimism']
NEG_WORTE = ['tariff', 'trade war', 'war', 'sanction', 'escalat', 'recession',
             'default', 'crash', 'plunge', 'slump', 'downgrade', 'crisis',
             'shutdown', 'conflict', 'misses', 'layoffs', 'fears', 'selloff']

# --- Social-Media-Signal (StockTwits: finanznah, je Post ein Bullish/Bearish-Tag,
#     Follower-Zahl = grobes Einfluss-Maß). Kostenlos, keine Zugangsschlüssel. ---
ST_SYMBOL = {'BTC-USD': 'BTC.X'}   # abweichende StockTwits-Symbole
ST_UA = {'User-Agent': 'Mozilla/5.0 (compatible; paper-newsbot/1.0)'}
ST_INFLUENCE = 1000                # ab so vielen Followern zählt ein Post doppelt
ST_TAGE = 2                        # nur Posts der letzten N Tage werten

# NYSE-Feiertage 2026 (voller Schließtag) für saubere Handelstag-Erkennung
US_HOLIDAYS = {'2026-01-01', '2026-01-19', '2026-02-16', '2026-04-03', '2026-05-25',
               '2026-06-19', '2026-07-03', '2026-09-07', '2026-11-26', '2026-12-25'}

B = os.path.dirname(os.path.abspath(__file__))
F = {k: os.path.join(B, f'news_bot_{k}') for k in
     ['state.json', 'history.csv', 'trades.csv', 'log.csv', 'runs.csv', 'dashboard.html']}


# ----------------------------------------------------------------------------- #
#  Marktstatus & Kurse
# ----------------------------------------------------------------------------- #
def markt_status(now_et):
    """(offen, nach_close, ist_handelstag) für den US-Aktienmarkt."""
    d = now_et.strftime('%Y-%m-%d')
    handelstag = now_et.weekday() < 5 and d not in US_HOLIDAYS
    t = now_et.timetz().replace(tzinfo=None)
    offen = handelstag and dtime(9, 30) <= t < dtime(16, 0)
    nach_close = handelstag and t >= dtime(16, 0)
    return offen, nach_close, handelstag


def settled_und_momentum(now_et, held, assets=None, topn=None):
    """Frischt den Tages-Cache zeitgeboxt auf und liefert
    (settled_df, close_s, momentum, kandidaten). settled_df/close_s = letzte
    ABGESCHLOSSENen Tages-Schlusskurse je Titel (Momentum/Benchmark-Basis).
    kandidaten = die 'topn' staerksten Momentum-Bewegungen + alle offenen
    Positionen (damit gehaltene Titel immer verwaltet/geschlossen werden).
    'assets' erlaubt ein eingeschraenktes Universum (z. B. nur US fuer Alpaca)."""
    assets = ASSETS if assets is None else assets
    topn = TOPN if topn is None else topn
    heute = now_et.strftime('%Y-%m-%d')
    offen, nach_close, _ = markt_status(now_et)
    pri = [BENCH] + [t for t in held if t in assets]        # Benchmark + Depot zuerst
    df = U.refresh_cache(B, list(assets) + [BENCH], heute,
                         budget_s=REFRESH_BUDGET, chunk=REFRESH_CHUNK, prioritaet=pri)
    settled = df.copy()
    # heutigen, noch nicht abgeschlossenen Bar weglassen (kein halbfertiger Kurs)
    if not nach_close and len(settled) and str(settled.index[-1].date()) == heute:
        settled = settled.iloc[:-1]
    settled = settled.ffill()
    close_s = {t: float(settled[t].dropna().iloc[-1])
               for t in settled.columns if settled[t].notna().any()}

    mom = {}
    for t in assets:
        if t in settled.columns:
            s = settled[t].dropna()
            if len(s) > 6:
                mom[t] = (s.iloc[-1] / s.iloc[-6] - 1) * 100
    ranked = sorted(mom, key=lambda x: -abs(mom[x]))
    kandidaten = ranked[:topn]
    # offene Positionen immer mit einbeziehen (Verwaltung/Exit)
    kandidaten = list(dict.fromkeys(
        kandidaten + [t for t in held if t in assets and t in close_s]))
    return settled, close_s, mom, kandidaten


def marks_holen(now_et, tickers, close_s):
    """Bewertungs-/Ausfuehrungskurse NUR fuer die gegebenen Titel: im Handel der
    aktuelle (verzoegerte) Intraday-Kurs, sonst der abgeschlossene Schlusskurs.
    Auch Titel ohne Cache-Schlusskurs (z. B. Alt-Positionen ausserhalb des
    Universums) werden per Intraday bewertet, damit sie geschlossen werden koennen."""
    tickers = list(dict.fromkeys(tickers))
    mark = {t: close_s[t] for t in tickers if t in close_s}
    offen, _, _ = markt_status(now_et)
    if offen and tickers:
        try:
            import yfinance as yf, pandas as pd
            intr = yf.download(tickers, period='1d', interval='5m',
                               auto_adjust=True, progress=False, threads=True)['Close']
            if isinstance(intr, pd.Series):
                intr = intr.to_frame(name=tickers[0])
            intr = intr.ffill()
            for t in tickers:
                if t in intr:
                    col = intr[t].dropna()
                    if len(col):
                        mark[t] = float(col.iloc[-1])
        except Exception:
            pass                        # notfalls settled close als mark
    return mark


def news_holen(tickers):
    """Schlagzeilen je Titel – parallel (I/O-gebunden), damit viele Kandidaten
    in wenigen Sekunden abgedeckt sind."""
    import yfinance as yf
    from concurrent.futures import ThreadPoolExecutor

    def one(t):
        try:
            items = yf.Ticker(t).news or []
            return t, [str((i.get('content') or i).get('title', '')) for i in items[:10]]
        except Exception:
            return t, []

    tickers = list(tickers)
    if not tickers:
        return {}
    with ThreadPoolExecutor(max_workers=PARALLEL) as ex:
        return {t: v for t, v in ex.map(one, tickers)}


def social_holen(tickers):
    """StockTwits-Social-Sentiment je Titel – parallel (jede Abfrage ist ein
    unabhaengiger HTTP-Call)."""
    from concurrent.futures import ThreadPoolExecutor
    tickers = list(tickers)
    if not tickers:
        return {}
    with ThreadPoolExecutor(max_workers=PARALLEL) as ex:
        return {t: r for t, r in zip(tickers, ex.map(social_sentiment, tickers))}


# ----------------------------------------------------------------------------- #
#  Signale: Schlagzeilen-Sentiment + StockTwits-Social + Momentum
# ----------------------------------------------------------------------------- #
def sentiment(schlagzeilen):
    s, treffer = 0, ''
    for h in schlagzeilen:
        hl = h.lower()
        p = sum(w in hl for w in POS_WORTE)
        n = sum(w in hl for w in NEG_WORTE)
        s += p - n
        if (p or n) and not treffer:
            treffer = h[:90]
    return max(-3, min(3, s)), treffer


def social_sentiment(ticker):
    """StockTwits-Stimmung je Asset: Bullish/Bearish der letzten Posts,
    Posts einflussreicher Accounts (>= ST_INFLUENCE Follower) zählen doppelt.
    Rückgabe: (Score in [-2,2], Anzahl gewerteter Posts, auslösender Post).
    Bei nicht erreichbarer Quelle: (0, 0, '') – der Lauf bricht nicht."""
    import urllib.request
    sym = ST_SYMBOL.get(ticker, ticker)
    url = f'https://api.stocktwits.com/api/2/streams/symbol/{sym}.json'
    try:
        req = urllib.request.Request(url, headers=ST_UA)
        with urllib.request.urlopen(req, timeout=4) as r:
            msgs = json.load(r).get('messages', [])
    except Exception:
        return 0, 0, ''
    grenze = datetime.now(timezone.utc) - timedelta(days=ST_TAGE)
    raw, n, top_foll, top_hl = 0, 0, -1, ''
    for m in msgs:
        try:
            tsm = datetime.fromisoformat(str(m.get('created_at')).replace('Z', '+00:00'))
            if tsm < grenze:
                continue
        except Exception:
            pass
        lab = ((m.get('entities') or {}).get('sentiment') or {}).get('basic')
        if lab not in ('Bullish', 'Bearish'):
            continue
        foll = (m.get('user') or {}).get('followers') or 0
        w = 2 if foll >= ST_INFLUENCE else 1
        raw += w if lab == 'Bullish' else -w
        n += 1
        if foll > top_foll:            # einflussreichster getaggter Post als Beleg
            top_foll = foll
            top_hl = ' '.join(str(m.get('body') or '').split())[:90]
    soc = 2 if raw >= 6 else 1 if raw >= 2 else -2 if raw <= -6 else -1 if raw <= -2 else 0
    return soc, n, top_hl


def signale_rechnen(settled, news, tickers):
    """Score je Titel = gedeckelte (News+Social)-Stimmung + Momentum-Komponente.
    Wird nur fuer die Kandidaten (staerkste Momentum-Bewegungen + Depot) berechnet."""
    soc_all = social_holen(tickers)          # StockTwits parallel vorab holen
    sig = {}
    for t in tickers:
        s = settled[t].dropna() if t in settled.columns else settled.iloc[0:0]
        mom = (s.iloc[-1] / s.iloc[-6] - 1) * 100 if len(s) > 6 else 0
        sent, hl = sentiment(news[t])
        soc, n_soc, soc_hl = soc_all.get(t, (0, 0, ''))
        stimmung = max(-3, min(3, sent + soc))
        score = stimmung + (2 if mom > 1.5 else -2 if mom < -1.5 else 0)
        treiber = f'[StockTwits] {soc_hl}' if (abs(soc) >= abs(sent) and soc_hl) else hl
        sig[t] = {'score': score, 'mom': mom, 'stimmung': stimmung,
                  'treiber': treiber, 'soc': soc, 'n_soc': n_soc}
    return sig


# ----------------------------------------------------------------------------- #
#  Ausführung mit realistischen Kosten
# ----------------------------------------------------------------------------- #
def fill_kauf(mark):    # Kauf/Cover: schlechterer Preis + Slippage
    return mark * (1 + SLIP)


def fill_verkauf(mark):  # Verkauf/Short: schlechterer Preis - Slippage
    return mark * (1 - SLIP)


def main():
    now_et = datetime.now(ET)
    offen, nach_close, handelstag = markt_status(now_et)
    heute = now_et.strftime('%Y-%m-%d')
    ts = now_et.strftime('%Y-%m-%d %H:%M ET')
    marktstatus = 'OFFEN' if offen else ('NACH-SCHLUSS' if nach_close else
                                         ('VORBÖRSLICH' if handelstag else 'GESCHLOSSEN'))

    # --- State zuerst laden: offene Positionen priorisieren die Cache-Auffrischung ---
    s = None
    if os.path.exists(F['state.json']):
        with open(F['state.json']) as f:
            s = json.load(f)
    held = set(s['pos']) if s else set()

    settled, close_s, mom, kandidaten = settled_und_momentum(now_et, held)

    if 'SPY' not in close_s:               # Cache-Aufwaermphase: Benchmark fehlt noch
        print('-' * 60)
        print(f'RUN ts={ts} markt={marktstatus} trades=0 equity=0.00 bench=0.00')
        print('Cache waermt noch auf (Benchmark-Kurs fehlt) – Lauf uebersprungen, '
              'naechster Lauf holt nach.')
        return

    if s is None:                          # Erstinitialisierung
        s = {'start': heute, 'cash': START, 'pos': {},
             'bh_anteile': START / close_s['SPY'], 'handelstage': 0}
        print('News-Bot initialisiert (10.000 EUR Papiergeld, Universum S&P 500 + DAX).')
    s.setdefault('letzter_tag', None)      # letzter gezaehlter Handelstag
    s.setdefault('handelstage', 0)

    # Bewertungs-/Ausfuehrungskurse NUR fuer Kandidaten + Depot + Benchmark holen
    mark = marks_holen(now_et, set(kandidaten) | held | {BENCH}, close_s)

    def _px(t):                            # robuster Kurszugriff mit Fallback
        return (mark.get(t) or close_s.get(t)
                or (s['pos'].get(t, {}) or {}).get('einstieg', 0.0))

    def _equity():
        return s['cash'] + sum(p['stk'] * _px(t) for t, p in s['pos'].items())

    trades_run = 0

    # --- Neuer Handelstag: Zähler + Leihgebühr auf offene Shorts ---
    neuer_tag = handelstag and s['letzter_tag'] != heute
    if neuer_tag:
        s['handelstage'] += 1
        s['letzter_tag'] = heute
        for t, p in s['pos'].items():
            if p['stk'] < 0:
                geb = abs(p['stk']) * _px(t) * BORROW_DAILY
                s['cash'] -= geb
                _trade(heute, f'LEIHGEBÜHR {t}', _px(t), s['cash'], ts, '', f'-{geb:.2f}')

    # --- Handeln nur bei offenem Markt ---
    if offen:
        news = news_holen(kandidaten)
        sig = signale_rechnen(settled, news, kandidaten)

        # Auch Alt-Positionen ausserhalb des Universums (z. B. ETFs aus der
        # 10-Titel-Version) verwalten/schliessen: neutrales Signal -> Exit.
        manage = list(dict.fromkeys(list(kandidaten) + list(s['pos'])))
        for t in manage:
            sig.setdefault(t, {'score': 0, 'mom': 0, 'stimmung': 0,
                               'treiber': '', 'soc': 0, 'n_soc': 0})

        logf = _log_oeffnen()
        lw = csv.writer(logf)

        # --- Ziel-Portfolio: ALLE klaren Signale gleichgewichtet, kein Hebel ---
        # Haltedauer erreicht -> raus und diesen Lauf nicht neu eröffnen
        aged = {t for t, p in s['pos'].items() if s['handelstage'] - p['tag'] >= MAXTAGE}
        ziel_dir = {t: (1 if sig[t]['score'] >= 3 else -1)
                    for t in manage if abs(sig[t]['score']) >= 3 and t not in aged}
        N = len(ziel_dir)
        equity = _equity()
        ziel_notional = (equity / N) if N else 0.0     # Gleichgewicht -> Summe = 100 %

        for t in sorted(manage, key=lambda x: -abs(sig[x]['score'])):
            mk = _px(t)
            if mk <= 0:                                # ohne Kurs nicht handelbar
                continue
            cur_stk = s['pos'][t]['stk'] if t in s['pos'] else 0.0
            tgt_stk = (ziel_notional * ziel_dir[t]) / mk if t in ziel_dir else 0.0
            delta = tgt_stk - cur_stk
            # Deadband: kleine Abweichungen nicht handeln (Schließen aber immer)
            if tgt_stk != 0 and abs(delta * mk) < BAND * ziel_notional:
                continue
            if abs(delta) < 1e-9:
                continue
            if delta > 0:                              # kaufen (Long auf / Short covern)
                fill = fill_kauf(mk); kosten = delta * fill * COMMISSION
                s['cash'] -= delta * fill + kosten
            else:                                      # verkaufen (Long ab / Short auf)
                fill = fill_verkauf(mk); erloes = -delta * fill
                kosten = erloes * COMMISSION; s['cash'] += erloes - kosten

            if abs(tgt_stk) < 1e-9:                     # Position schließen
                akt = f"EXIT {s['pos'][t]['richtung']}" + (' (Haltedauer)' if t in aged else '')
                s['pos'].pop(t, None)
            else:
                richtung = 'LONG' if tgt_stk > 0 else 'SHORT'
                if t not in s['pos'] or s['pos'][t]['richtung'] != richtung:
                    s['pos'][t] = {'stk': tgt_stk, 'einstieg': fill, 'tag': s['handelstage'],
                                   'richtung': richtung, 'einstieg_ts': ts}
                    akt = richtung
                else:
                    p = s['pos'][t]
                    if abs(tgt_stk) > abs(cur_stk):     # aufgestockt -> Einstieg mischen
                        p['einstieg'] = (abs(cur_stk) * p['einstieg'] + abs(delta) * fill) / abs(tgt_stk)
                        akt = f"AUFSTOCKEN {richtung}"
                    else:                               # reduziert -> Einstieg bleibt
                        akt = f"REDUZIEREN {richtung}"
                    p['stk'] = tgt_stk
            _trade(heute, f"{akt} {t}", mk, s['cash'], ts, f'{fill:.2f}', f'{kosten:.2f}')
            lw.writerow([heute, t, f"{sig[t]['mom']:.1f}", sig[t]['stimmung'], sig[t]['score'],
                         akt, sig[t]['treiber'], sig[t]['soc'], sig[t]['n_soc']])
            trades_run += 1
        logf.close()

    # --- Bewertung (immer), Historie (1 Zeile/Tag, auf letzten Stand aktualisiert) ---
    equity = _equity()
    bench = s['bh_anteile'] * _px('SPY')
    _historie_update(heute, equity, bench)
    _run_snapshot(ts, marktstatus, _px('SPY'), s['cash'], equity, bench, s, mark)

    with open(F['state.json'], 'w') as f:
        json.dump(s, f, indent=2)
    dashboard(s, mark, ts, marktstatus)

    offen_txt = ', '.join(f"{p['richtung']} {t}" for t, p in s['pos'].items()) or 'keine'
    print('-' * 60)
    print(f'RUN ts={ts} markt={marktstatus} trades={trades_run} '
          f'equity={equity:.2f} bench={bench:.2f}')
    print(f'News-Bot: Depot {equity:,.2f} € | Benchmark {bench:,.2f} € | '
          f'Diff {equity - bench:+,.2f} € | Positionen: {offen_txt}')
    if not offen:
        print(f'(Markt {marktstatus} – nur Bewertung, kein Handel. Trades laufen zur US-Handelszeit.)')


# ----------------------------------------------------------------------------- #
#  Persistenz-Helfer
# ----------------------------------------------------------------------------- #
def _log_oeffnen():
    """Entscheidungs-Log öffnen; Alt-Header (7 Spalten) einmalig auf 9 heben."""
    ALT = ['Datum', 'Asset', 'Mom5%', 'Sentiment', 'Score', 'Aktion', 'Schlagzeile']
    NEU = ['Datum', 'Asset', 'Mom5%', 'Stimmung', 'Score', 'Aktion', 'Schlagzeile',
           'StockTwits', 'ST-Posts']
    if os.path.exists(F['log.csv']):
        with open(F['log.csv']) as f:
            rows = list(csv.reader(f))
        if rows and rows[0][:7] == ALT:
            rows[0] = NEU
            with open(F['log.csv'], 'w', newline='') as f:
                csv.writer(f).writerows(rows)
    neu = not os.path.exists(F['log.csv'])
    f = open(F['log.csv'], 'a', newline='')
    if neu:
        csv.writer(f).writerow(NEU)
    return f


def _trade(datum, aktion, kurs, wert, ts='', fill='', kosten=''):
    neu = not os.path.exists(F['trades.csv'])
    with open(F['trades.csv'], 'a', newline='') as f:
        w = csv.writer(f)
        if neu:
            w.writerow(['Datum', 'Aktion', 'Kurs', 'Depot/Cash', 'Zeitstempel', 'Fill', 'Kosten'])
        w.writerow([datum, aktion, f'{kurs:.2f}', f'{wert:.2f}', ts, fill, kosten])


def _historie_update(heute, equity, bench):
    zeilen = []
    if os.path.exists(F['history.csv']):
        with open(F['history.csv']) as f:
            zeilen = [r for r in csv.reader(f)][1:]
    if zeilen and zeilen[-1][0] == heute:           # heutigen Tag aktualisieren
        zeilen[-1] = [heute, f'{equity:.2f}', f'{bench:.2f}']
    else:
        zeilen.append([heute, f'{equity:.2f}', f'{bench:.2f}'])
    with open(F['history.csv'], 'w', newline='') as f:
        w = csv.writer(f); w.writerow(['Datum', 'Strategie', 'BuyHold']); w.writerows(zeilen)


def _run_snapshot(ts, marktstatus, spy, cash, equity, bench, s, mark):
    """Vollständiger Audit-Eintrag je Lauf."""
    pos = ' / '.join(f"{p['richtung']} {t}@{mark.get(t, p.get('einstieg', 0.0)):.2f}"
                     for t, p in s['pos'].items()) or '-'
    neu = not os.path.exists(F['runs.csv'])
    with open(F['runs.csv'], 'a', newline='') as f:
        w = csv.writer(f)
        if neu:
            w.writerow(['Zeitstempel', 'Marktstatus', 'SPY_mark', 'Cash',
                        'Depotwert', 'Benchmark', 'Positionen'])
        w.writerow([ts, marktstatus, f'{spy:.2f}', f'{cash:.2f}',
                    f'{equity:.2f}', f'{bench:.2f}', pos])


# ----------------------------------------------------------------------------- #
#  Dashboard
# ----------------------------------------------------------------------------- #
def dashboard(s, mark, ts, marktstatus):
    zeilen = []
    if os.path.exists(F['history.csv']):
        with open(F['history.csv']) as f:
            zeilen = [r for r in csv.reader(f)][1:]
    if not zeilen:
        zeilen = [[ts[:10], f'{START:.2f}', f'{START:.2f}']]
    labels = json.dumps([z[0] for z in zeilen])
    d1 = json.dumps([float(z[1]) for z in zeilen])
    d2 = json.dumps([float(z[2]) for z in zeilen])
    eq, bh = float(zeilen[-1][1]), float(zeilen[-1][2])
    r, rb = (eq / START - 1) * 100, (bh / START - 1) * 100
    farbe = '#0e9f6e' if r >= rb else '#e02424'

    posrows = ''.join(
        f"<tr><td>{p['richtung']}</td><td>{t}</td><td style='text-align:right'>{p['einstieg']:.2f}</td>"
        f"<td style='text-align:right'>{mark.get(t, p['einstieg']):.2f}</td>"
        f"<td style='text-align:right'>{(mark.get(t, p['einstieg'])/p['einstieg']-1)*100*(1 if p['stk']>0 else -1):+.2f} %</td></tr>"
        for t, p in s['pos'].items()) or '<tr><td colspan=5>keine offenen Positionen</td></tr>'

    logrows = ''
    if os.path.exists(F['log.csv']):
        with open(F['log.csv']) as f:
            rows = [x for x in csv.reader(f)][1:][-10:]
        logrows = ''.join(
            f'<tr><td>{x[0]}</td><td>{x[5]} {x[1]}</td><td>Score {x[4]}'
            + (f' · <span style="color:#c27803">StockTwits {x[7]}</span>'
               if len(x) > 7 and x[7] not in ('', '0') else '')
            + f'</td><td style="color:#6b7280">{x[6][:70]}</td></tr>' for x in reversed(rows))

    runrows = ''
    if os.path.exists(F['runs.csv']):
        with open(F['runs.csv']) as f:
            rr = [x for x in csv.reader(f)][1:][-8:]
        runrows = ''.join(
            f'<tr><td>{x[0]}</td><td>{x[1]}</td><td style="text-align:right">{float(x[4]):,.0f} €</td>'
            f'<td style="text-align:right">{float(x[5]):,.0f} €</td></tr>' for x in reversed(rr))

    html = f"""<!DOCTYPE html><html lang="de"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>News-Bot Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>body{{font-family:'Segoe UI',sans-serif;background:#f9fafb;color:#111827;margin:0;padding:24px}}
.wrap{{max-width:900px;margin:0 auto}}h1{{font-size:1.4rem;margin-bottom:2px}}.sub{{color:#6b7280;font-size:.9rem;margin-bottom:16px}}
.badge{{display:inline-block;padding:2px 8px;border-radius:6px;font-size:.72rem;font-weight:600;
background:{'#def7ec' if marktstatus=='OFFEN' else '#f3f4f6'};color:{'#0e9f6e' if marktstatus=='OFFEN' else '#6b7280'}}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:20px}}
.kpi{{background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:14px}}
.kpi b{{display:block;font-size:1.3rem}}.kpi span{{color:#6b7280;font-size:.78rem}}
.card{{background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:18px;margin-bottom:16px}}
table{{width:100%;border-collapse:collapse;font-size:.83rem}}td,th{{padding:6px 4px;border-top:1px solid #f0f0f0;text-align:left}}
.warn{{font-size:.75rem;color:#6b7280;margin-top:14px}}</style></head><body><div class="wrap">
<h1>📰 News-Bot – nachrichten- &amp; social-getriebenes Swing-Trading (Paper)</h1>
<div class="sub">Stand: {ts} · <span class="badge">Markt {marktstatus}</span> · long &amp; short · alle klaren Signale gleichgewichtet (kein Hebel) · Haltedauer ≤ 5 Tage · Start: {s.get('start','?')}</div>
<div class="kpis">
<div class="kpi"><b>{eq:,.0f} €</b><span>Depotwert (Start 10.000)</span></div>
<div class="kpi"><b>{r:+.2f} %</b><span>Rendite News-Bot</span></div>
<div class="kpi"><b>{rb:+.2f} %</b><span>Buy &amp; Hold S&amp;P 500</span></div>
<div class="kpi" style="border-color:{farbe}"><b style="color:{farbe}">{r-rb:+.2f} %-Pkt.</b><span>Über-/Unterrendite</span></div>
</div>
<div class="card"><canvas id="c" height="110"></canvas></div>
<div class="card"><b>Offene Positionen</b><table><tr><th>Richtung</th><th>Asset</th><th>Einstieg (Fill)</th><th>Aktuell</th><th>P&amp;L</th></tr>{posrows}</table></div>
<div class="card"><b>Letzte Entscheidungen (mit auslösender Meldung)</b><table>{logrows or '<tr><td>–</td></tr>'}</table></div>
<div class="card"><b>Letzte Läufe (Audit-Trail)</b><table><tr><th>Zeitpunkt</th><th>Markt</th><th style="text-align:right">Depot</th><th style="text-align:right">Benchmark</th></tr>{runrows or '<tr><td>–</td></tr>'}</table></div>
<div class="warn">Signal = Schlagzeilen-Sentiment (transparente Wortliste) + StockTwits-Social-Sentiment (Bullish/Bearish je Ticker, einflussreiche Accounts doppelt) + 5-Tage-Momentum auf abgeschlossenen Schlusskursen. Ausführung zum aktuellen (~15 Min verzögerten) Intraday-Kurs mit Slippage {SLIP*1e4:.0f} bps + Kommission {COMMISSION*1e4:.0f} bps je Trade; Shorts zahlen {BORROW_ANNUAL*100:.0f} % p.a. Leihgebühr. Reines Papiergeld – misst ehrlich, ob nachrichten-/social-getriebenes Trading nach Kosten eine Überrendite liefert. Prognose laut allen bisherigen Tests: nein – die Daten entscheiden. Kein Anlagerat.</div>
</div><script>new Chart(document.getElementById('c'),{{type:'line',data:{{labels:{labels},datasets:[
{{label:'News-Bot',data:{d1},borderColor:'#c27803',borderWidth:2,pointRadius:0,tension:.2}},
{{label:'Buy & Hold S&P 500',data:{d2},borderColor:'#9ca3af',borderWidth:2,pointRadius:0,tension:.2}}]}},
options:{{plugins:{{legend:{{position:'bottom'}}}},scales:{{y:{{ticks:{{callback:v=>v.toLocaleString('de-DE')+' €'}}}}}}}}}});</script>
</body></html>"""
    with open(F['dashboard.html'], 'w') as f:
        f.write(html)


if __name__ == '__main__':
    main()
