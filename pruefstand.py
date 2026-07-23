# -*- coding: utf-8 -*-
"""
STRATEGIE-PRUEFSTAND – Literatur-Strategien gegen Buy & Hold (reiner Simulator)
===============================================================================
Zwei klassische, gut dokumentierte Strategien aus der Forschung laufen als
Papiergeld-Depots (je 10.000 EUR) im selben Cloud-Workflow wie die News-Bots –
mit DEMSELBEN Kostenmodell (Slippage + Kommission aus news_bot.py) und
DERSELBEN Kurs-Infrastruktur (Tages-Cache). Ziel: ehrlich messen, ob bekannte
Faktoren nach Kosten den Markt schlagen. Kein Anlagerat, kein echtes Geld.

  mom12_1    – Querschnitts-Momentum 12-1 (Jegadeesh/Titman 1993):
               monatlich die N staerksten Titel der letzten 12 Monate kaufen,
               den juengsten Monat ueberspringen (Reversal-Effekt). Long-only,
               gleichgewichtet, Rebalancing 1x/Monat.
  reversal5  – Kurzfrist-Reversal (Lehmann 1990 u. a.): woechentlich die N
               groessten 5-Tage-Verlierer kaufen (Ueberreaktions-These).
               Long-only, gleichgewichtet, Rebalancing 1x/Woche.

Beruehrt WEDER die Bots NOCH das Alpaca-Konto. Erwartung laut Literatur:
Brutto-Effekte existieren, sind nach Kosten aber oft weg – die Daten entscheiden.
Ergebnis: pruefstand_dashboard.html + pruefstand_history.csv.
"""
import os, json, csv, time
from datetime import datetime

import news_bot as nb          # Kostenmodell + Marktzeit + Cache-Bausteine
import universe as U

B = os.path.dirname(os.path.abspath(__file__))
F = {k: os.path.join(B, f'pruefstand_{k}')
     for k in ['state.json', 'history.csv', 'dashboard.html', 'mom_cache.json']}
START = 10_000.0
N_POS = 20                     # Positionen je Strategie (gleichgewichtet)
MOM_MIN_TICKER = 250           # erst rebalancen, wenn genug 12-Monats-Daten da sind
MOM_BUDGET_S = 45.0            # Sek./Lauf fuer Monatsdaten-Nachladen (zeitgeboxt)
MOM_CHUNK = 40
STRATS = ['mom12_1', 'reversal5']


# --------------------------------------------------------------------------- #
#  12-1-Momentum: Monatsdaten zeitgeboxt holen und je Ticker cachen
# --------------------------------------------------------------------------- #
def mom12_1_werte(monat):
    """Liefert {ticker: 12-1-Monatsrendite} aus dem Monats-Cache; laedt fehlende
    Ticker zeitgeboxt nach (hoechstens 1x je Ticker und Monat). Ueber mehrere
    Laeufe hinweg fuellt sich der Cache, bis MOM_MIN_TICKER erreicht ist."""
    cache = {}
    if os.path.exists(F['mom_cache.json']):
        try:
            cache = json.load(open(F['mom_cache.json']))
        except Exception:
            cache = {}

    todo = [t for t in nb.ASSETS if cache.get(t, {}).get('m') != monat]
    if todo:
        try:
            import yfinance as yf, pandas as pd
            t0 = time.time()
            for i in range(0, len(todo), MOM_CHUNK):
                if time.time() - t0 > MOM_BUDGET_S:
                    break
                grp = todo[i:i + MOM_CHUNK]
                try:
                    d = yf.download(grp, period='14mo', interval='1mo',
                                    auto_adjust=True, progress=False,
                                    threads=True, timeout=15)['Close']
                except Exception:
                    continue
                if isinstance(d, pd.Series):
                    d = d.to_frame(name=grp[0])
                for t in grp:              # als "diesen Monat versucht" markieren
                    r = None
                    if t in d:
                        s = d[t].dropna()
                        # letzte Zeile = laufender Monat (unfertig) -> weglassen;
                        # 12-1 = Rendite von Monat -13 bis Monat -2
                        if len(s) >= 13:
                            r = float(s.iloc[-2] / s.iloc[-13] - 1)
                    cache[t] = {'m': monat, 'r': r}
        except Exception:
            pass                            # kein Netz -> naechster Lauf holt nach
        try:
            json.dump(cache, open(F['mom_cache.json'], 'w'))
        except Exception:
            pass

    return {t: v['r'] for t, v in cache.items()
            if v.get('m') == monat and v.get('r') is not None}


# --------------------------------------------------------------------------- #
#  Portfolio-Schritt: komplettes Rebalancing auf Ziel-Liste (long-only)
# --------------------------------------------------------------------------- #
def rebalance(stv, ziel, mark, close_s):
    """Verkauft alles ausserhalb 'ziel', kauft/gewichtet 'ziel' gleich.
    Kosten wie news_bot: Slippage je Seite + Kommission. Rueckgabe: Trades."""
    def px(t):
        return (mark.get(t) or close_s.get(t)
                or (stv['pos'].get(t, {}) or {}).get('einstieg', 0.0))

    eq = stv['cash'] + sum(p['stk'] * px(t) for t, p in stv['pos'].items())
    ziel = [t for t in ziel if px(t) > 0][:N_POS]
    nt = eq / len(ziel) if ziel else 0.0
    trades = 0

    for t in list(stv['pos']):             # erst verkaufen (setzt Cash frei)
        if t not in ziel:
            mk = px(t)
            stk = stv['pos'][t]['stk']
            fill = nb.fill_verkauf(mk)
            erloes = stk * fill
            stv['cash'] += erloes - erloes * nb.COMMISSION
            stv['pos'].pop(t)
            trades += 1

    for t in ziel:                          # dann Ziele auf Gleichgewicht bringen
        mk = px(t)
        cur = stv['pos'][t]['stk'] if t in stv['pos'] else 0.0
        tgt = nt / mk
        delta = tgt - cur
        if cur > 0 and abs(delta * mk) < nb.BAND * nt:
            continue                        # Deadband: Mini-Anpassungen sparen
        if abs(delta) < 1e-9:
            continue
        if delta > 0:
            fill = nb.fill_kauf(mk)
            stv['cash'] -= delta * fill + delta * fill * nb.COMMISSION
        else:
            fill = nb.fill_verkauf(mk)
            erloes = -delta * fill
            stv['cash'] += erloes - erloes * nb.COMMISSION
        if t in stv['pos']:
            p = stv['pos'][t]
            if tgt > cur:
                p['einstieg'] = (cur * p['einstieg'] + delta * fill) / tgt
            p['stk'] = tgt
        else:
            stv['pos'][t] = {'stk': tgt, 'einstieg': fill}
        trades += 1
    return trades


def equity(stv, mark, close_s):
    def px(t):
        return (mark.get(t) or close_s.get(t)
                or (stv['pos'].get(t, {}) or {}).get('einstieg', 0.0))
    return stv['cash'] + sum(p['stk'] * px(t) for t, p in stv['pos'].items())


# --------------------------------------------------------------------------- #
#  Persistenz + Dashboard (gleiches Muster wie Arena)
# --------------------------------------------------------------------------- #
def _history_update(heute, werte, bench):
    zeilen = []
    if os.path.exists(F['history.csv']):
        with open(F['history.csv']) as f:
            zeilen = [r for r in csv.reader(f)][1:]
    reihe = [heute] + [f'{werte[n]:.2f}' for n in STRATS] + [f'{bench:.2f}']
    if zeilen and zeilen[-1][0] == heute:
        zeilen[-1] = reihe
    else:
        zeilen.append(reihe)
    with open(F['history.csv'], 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['Datum'] + STRATS + ['BuyHold'])
        w.writerows(zeilen)


def _dashboard(ts, marktstatus, werte, bench, info):
    zeilen = []
    if os.path.exists(F['history.csv']):
        with open(F['history.csv']) as f:
            zeilen = [r for r in csv.reader(f)][1:]
    if not zeilen:
        zeilen = [[ts[:10]] + [f'{START:.2f}'] * len(STRATS) + [f'{START:.2f}']]
    labels = json.dumps([z[0] for z in zeilen])
    farben = ['#2563eb', '#c27803']
    datasets = []
    for i, name in enumerate(STRATS):
        datasets.append("{label:'%s',data:%s,borderColor:'%s',borderWidth:2,pointRadius:0,tension:.2}"
                        % (name, json.dumps([float(z[i + 1]) for z in zeilen]), farben[i]))
    datasets.append("{label:'Buy & Hold',data:%s,borderColor:'#9ca3af',borderWidth:1,borderDash:[5,4],pointRadius:0}"
                    % json.dumps([float(z[-1]) for z in zeilen]))

    rows = ''
    for name in sorted(STRATS, key=lambda n: -werte[n]):
        r = (werte[name] / START - 1) * 100
        rb = (bench / START - 1) * 100
        farbe = '#0e9f6e' if r >= rb else '#e02424'
        rows += (f"<tr><td><b>{name}</b></td><td style='text-align:right'>{werte[name]:,.2f} €</td>"
                 f"<td style='text-align:right;color:{farbe}'>{r:+.2f} %</td>"
                 f"<td style='text-align:right'>{r - rb:+.2f} %-Pkt.</td></tr>")

    html = f"""<!DOCTYPE html><html lang="de"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Strategie-Prüfstand</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>body{{font-family:'Segoe UI',sans-serif;background:#f9fafb;color:#111827;margin:0;padding:24px}}
.wrap{{max-width:900px;margin:0 auto}}h1{{font-size:1.35rem;margin-bottom:2px}}
.sub{{color:#6b7280;font-size:.9rem;margin-bottom:16px}}
.card{{background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:18px;margin-bottom:16px}}
table{{width:100%;border-collapse:collapse;font-size:.85rem}}td,th{{padding:7px 4px;border-top:1px solid #f0f0f0;text-align:left}}
.warn{{font-size:.75rem;color:#6b7280;margin-top:14px}}</style></head><body><div class="wrap">
<h1>🧪 Strategie-Prüfstand – Literatur-Faktoren vs. Buy &amp; Hold</h1>
<div class="sub">Stand: {ts} · Markt {marktstatus} · {info} · je Start 10.000 € · reines Papiergeld</div>
<div class="card"><canvas id="c" height="120"></canvas></div>
<div class="card"><b>Rangliste (Depotwert)</b><table>
<tr><th>Strategie</th><th style="text-align:right">Depot</th><th style="text-align:right">Rendite</th><th style="text-align:right">vs. Buy&amp;Hold</th></tr>
{rows}</table></div>
<div class="warn">mom12_1 = Querschnitts-Momentum 12-1 (Jegadeesh/Titman), monatliches Rebalancing, Top-{N_POS} long, gleichgewichtet.
reversal5 = Kurzfrist-Reversal, wöchentlich die {N_POS} größten 5-Tage-Verlierer long. Kosten wie News-Bot
(Slippage {nb.SLIP*1e4:.0f} bps + Kommission {nb.COMMISSION*1e4:.0f} bps je Trade). Erwartung laut Forschung: Brutto-Effekte
existieren, nach Kosten oft nicht – genau das wird hier gemessen. Kein Anlagerat.</div>
</div><script>new Chart(document.getElementById('c'),{{type:'line',
data:{{labels:{labels},datasets:[{','.join(datasets)}]}},
options:{{plugins:{{legend:{{position:'bottom'}}}},scales:{{y:{{ticks:{{callback:v=>v.toLocaleString('de-DE')+' €'}}}}}}}}}});</script>
</body></html>"""
    with open(F['dashboard.html'], 'w') as f:
        f.write(html)


# --------------------------------------------------------------------------- #
#  Hauptlauf
# --------------------------------------------------------------------------- #
def main():
    now_et = datetime.now(nb.ET)
    offen, nach_close, handelstag = nb.markt_status(now_et)
    heute = now_et.strftime('%Y-%m-%d')
    monat = now_et.strftime('%Y-%m')
    woche = now_et.strftime('%G-W%V')          # ISO-Woche fuer das Reversal-Rebal
    ts = now_et.strftime('%Y-%m-%d %H:%M ET')
    marktstatus = ('OFFEN' if offen else 'NACH-SCHLUSS' if nach_close
                   else 'VORBÖRSLICH' if handelstag else 'GESCHLOSSEN')

    st = json.load(open(F['state.json'])) if os.path.exists(F['state.json']) else None
    held = set()
    if st:
        for v in st['strats'].values():
            held |= set(v['pos'])

    settled, close_s, mom5, _kand = nb.settled_und_momentum(now_et, held)
    if 'SPY' not in close_s:
        print('-' * 60)
        print(f'RUN ts={ts} markt={marktstatus} trades=0 (Cache waermt auf – Benchmark fehlt)')
        return

    if st is None:
        st = {'start': heute, 'bh_anteile': START / close_s['SPY'],
              'strats': {n: {'cash': START, 'pos': {}, 'letzter_rebal': None}
                         for n in STRATS}}
        print(f'Pruefstand initialisiert: {len(STRATS)} Strategien je {START:,.0f} € '
              f'(Universum S&P 500 + DAX, Kostenmodell wie News-Bot).')

    mark = nb.marks_holen(now_et, held | {nb.BENCH}, close_s)
    trades, info = 0, []

    if offen:
        # --- mom12_1: 1x pro Monat, sobald genuegend Monatsdaten im Cache ---
        v = st['strats']['mom12_1']
        if v['letzter_rebal'] != monat:
            r12 = mom12_1_werte(monat)
            if len(r12) >= MOM_MIN_TICKER:
                ziel = sorted(r12, key=lambda t: -r12[t])
                ziel = [t for t in ziel if t in close_s][:N_POS]
                mark.update(nb.marks_holen(now_et, set(ziel) | set(v['pos']), close_s))
                trades += rebalance(v, ziel, mark, close_s)
                v['letzter_rebal'] = monat
                info.append(f'mom12_1 rebalanct ({len(ziel)} Titel)')
            else:
                info.append(f'mom12_1 wartet auf Monatsdaten ({len(r12)}/{MOM_MIN_TICKER})')

        # --- reversal5: 1x pro Woche, groesste 5-Tage-Verlierer ---
        v = st['strats']['reversal5']
        if v['letzter_rebal'] != woche:
            verlierer = sorted((t for t in mom5 if t in close_s and mom5[t] < 0),
                               key=lambda t: mom5[t])
            ziel = verlierer[:N_POS]
            if ziel:
                mark.update(nb.marks_holen(now_et, set(ziel) | set(v['pos']), close_s))
                trades += rebalance(v, ziel, mark, close_s)
                v['letzter_rebal'] = woche
                info.append(f'reversal5 rebalanct ({len(ziel)} Titel)')

    werte = {n: equity(st['strats'][n], mark, close_s) for n in STRATS}
    bench = st['bh_anteile'] * (mark.get(nb.BENCH) or close_s.get(nb.BENCH, 0.0))
    _history_update(heute, werte, bench)
    json.dump(st, open(F['state.json'], 'w'), indent=2)
    _dashboard(ts, marktstatus, werte, bench,
               ' · '.join(info) if info else 'kein Rebalancing faellig')

    print('-' * 60)
    print(f'RUN ts={ts} markt={marktstatus} trades={trades} {" | ".join(info)}')
    for n in sorted(STRATS, key=lambda x: -werte[x]):
        print(f'  {n:11s} {werte[n]:>11,.2f} € ({(werte[n]/START-1)*100:+.2f} %)')
    print(f'  {"BuyHold":11s} {bench:>11,.2f} € ({(bench/START-1)*100:+.2f} %)')
    if not offen:
        print(f'(Markt {marktstatus} – nur Bewertung, kein Handel.)')


if __name__ == '__main__':
    main()
