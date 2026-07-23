# -*- coding: utf-8 -*-
"""
NEWS-BOT ARENA – A/B-Vergleich mehrerer Strategie-Varianten (reiner Simulator)
=============================================================================
Laeuft in der GitHub-Cloud (KEIN Rechner noetig, gleicher stuendlicher Workflow
wie die Bots). Die TEUREN Signale (News + StockTwits + Momentum) werden EINMAL
pro Lauf geholt und dann durch mehrere Varianten gespielt – jede mit eigenem
Papiergeld-Depot ab 10.000 EUR und eigener Historie. So sieht man direkt, ob die
Upgrades gegen den Ausgangsstand (baseline) etwas bringen.

Varianten (Schalter-Kombinationen, vgl. news_bot.py):
  baseline    – alles aus (= aktueller Bot)
  regime      – nur Regime-Filter
  social      – nur StockTwits-Volumen-Gate
  confidence  – nur |Score|-Gewichtung
  all         – alle drei an

Beruehrt WEDER news_bot.py NOCH das Alpaca-Konto. Reines Mess-Experiment,
kein Anlagerat. Ergebnis: arena_dashboard.html + arena_history.csv.
"""
import os, json, csv
from datetime import datetime

import news_bot as nb        # teure Signal-/Kurs-Bausteine + Kostenmodell wiederverwenden

B = os.path.dirname(os.path.abspath(__file__))
F = {k: os.path.join(B, f'arena_{k}') for k in ['state.json', 'history.csv', 'dashboard.html']}
START = 10_000.0
ASSETS = nb.ASSETS           # gleiches Universum wie der Simulator (S&P 500 + DAX)
BENCH = nb.BENCH

# (Name, Regime-Filter, Social-Volumen-Gate, Confidence-Sizing)
VARIANTS = [
    ('baseline',   False, False, False),
    ('regime',     True,  False, False),
    ('social',     False, True,  False),
    ('confidence', False, False, True),
    ('all',        True,  True,  True),
]


# --------------------------------------------------------------------------- #
#  Reine, guenstige Logik (pro Variant unterschiedlich) – ohne Netzzugriff
# --------------------------------------------------------------------------- #
def score_from_raw(raw, social_gate):
    """Score aus den einmal geholten Rohdaten – Social-Gate variantenabhaengig."""
    soc_eff = raw['soc'] if (not social_gate or raw['n_soc'] >= nb.SOCIAL_MIN_POSTS) else 0
    stimmung = max(-3, min(3, raw['sent'] + soc_eff))
    return stimmung + (2 if raw['mom'] > 1.5 else -2 if raw['mom'] < -1.5 else 0)


def regime_filter(dirs, regime, on):
    if not on or regime == 0:
        return dirs
    keep = -1 if regime < 0 else 1
    return {t: d for t, d in dirs.items() if d == keep}


def weights(dirs, scores, on):
    n = len(dirs)
    if n == 0:
        return {}
    if not on:
        return {t: 1.0 / n for t in dirs}
    tot = sum(abs(scores.get(t, 0)) for t in dirs) or 1.0
    return {t: abs(scores.get(t, 0)) / tot for t in dirs}


def step_variant(stv, scores, mark, close_s, handelstage, regime, cfg, neuer_tag):
    """Ein Handels-/Bewertungsschritt fuer EINE Variant. Mutiert stv (cash/pos).
    cfg = (regime_on, social_on, confidence_on). Rueckgabe: Zahl der Trades."""
    reg_on, _soc_on, conf_on = cfg

    def px(t):
        return (mark.get(t) or close_s.get(t)
                or (stv['pos'].get(t, {}) or {}).get('einstieg', 0.0))

    def equity():
        return stv['cash'] + sum(p['stk'] * px(t) for t, p in stv['pos'].items())

    # Leihgebuehr auf offene Shorts an jedem neuen Handelstag
    if neuer_tag:
        for t, p in stv['pos'].items():
            if p['stk'] < 0:
                stv['cash'] -= abs(p['stk']) * px(t) * nb.BORROW_DAILY

    if scores is None:                     # Markt zu -> nur Bewertung
        return 0

    aged = {t for t, p in stv['pos'].items() if handelstage - p['tag'] >= nb.MAXTAGE}
    dirs = {t: (1 if scores[t] >= 3 else -1)
            for t in scores if abs(scores[t]) >= 3 and t not in aged}
    dirs = regime_filter(dirs, regime, reg_on)
    eq = equity()
    gew = weights(dirs, scores, conf_on)

    trades = 0
    manage = list(dict.fromkeys(list(scores) + list(stv['pos'])))
    for t in sorted(manage, key=lambda x: -abs(scores.get(x, 0))):
        mk = px(t)
        if mk <= 0:
            continue
        nt = eq * gew.get(t, 0.0)
        cur = stv['pos'][t]['stk'] if t in stv['pos'] else 0.0
        tgt = (nt * dirs[t]) / mk if t in dirs else 0.0
        delta = tgt - cur
        if tgt != 0 and abs(delta * mk) < nb.BAND * nt:
            continue
        if abs(delta) < 1e-9:
            continue
        if delta > 0:
            fill = nb.fill_kauf(mk); stv['cash'] -= delta * fill + delta * fill * nb.COMMISSION
        else:
            fill = nb.fill_verkauf(mk); erloes = -delta * fill
            stv['cash'] += erloes - erloes * nb.COMMISSION
        if abs(tgt) < 1e-9:
            stv['pos'].pop(t, None)
        else:
            richtung = 'LONG' if tgt > 0 else 'SHORT'
            if t not in stv['pos'] or stv['pos'][t]['richtung'] != richtung:
                stv['pos'][t] = {'stk': tgt, 'einstieg': fill, 'tag': handelstage, 'richtung': richtung}
            else:
                p = stv['pos'][t]
                if abs(tgt) > abs(cur):
                    p['einstieg'] = (abs(cur) * p['einstieg'] + abs(delta) * fill) / abs(tgt)
                p['stk'] = tgt
        trades += 1
    return trades


# --------------------------------------------------------------------------- #
#  Persistenz
# --------------------------------------------------------------------------- #
def _history_update(heute, werte, bench):
    zeilen = []
    if os.path.exists(F['history.csv']):
        with open(F['history.csv']) as f:
            zeilen = [r for r in csv.reader(f)][1:]
    reihe = [heute] + [f'{werte[n]:.2f}' for n, *_ in VARIANTS] + [f'{bench:.2f}']
    if zeilen and zeilen[-1][0] == heute:
        zeilen[-1] = reihe
    else:
        zeilen.append(reihe)
    with open(F['history.csv'], 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['Datum'] + [n for n, *_ in VARIANTS] + ['BuyHold'])
        w.writerows(zeilen)


def _dashboard(ts, marktstatus, werte, bench):
    zeilen = []
    if os.path.exists(F['history.csv']):
        with open(F['history.csv']) as f:
            zeilen = [r for r in csv.reader(f)][1:]
    if not zeilen:
        zeilen = [[ts[:10]] + [f'{START:.2f}'] * len(VARIANTS) + [f'{START:.2f}']]
    labels = json.dumps([z[0] for z in zeilen])
    farben = ['#111827', '#2563eb', '#c27803', '#0e9f6e', '#e02424']
    datasets = []
    for i, (name, *_) in enumerate(VARIANTS):
        datasets.append("{label:'%s',data:%s,borderColor:'%s',borderWidth:2,pointRadius:0,tension:.2}"
                        % (name, json.dumps([float(z[i + 1]) for z in zeilen]), farben[i % len(farben)]))
    datasets.append("{label:'Buy & Hold',data:%s,borderColor:'#9ca3af',borderWidth:1,borderDash:[5,4],pointRadius:0}"
                    % json.dumps([float(z[-1]) for z in zeilen]))

    rows = ''
    ranking = sorted(VARIANTS, key=lambda v: -werte[v[0]])
    for name, *_ in ranking:
        r = (werte[name] / START - 1) * 100
        rb = (bench / START - 1) * 100
        farbe = '#0e9f6e' if r >= rb else '#e02424'
        rows += (f"<tr><td><b>{name}</b></td><td style='text-align:right'>{werte[name]:,.2f} €</td>"
                 f"<td style='text-align:right;color:{farbe}'>{r:+.2f} %</td>"
                 f"<td style='text-align:right'>{r - rb:+.2f} %-Pkt.</td></tr>")

    html = f"""<!DOCTYPE html><html lang="de"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>News-Bot Arena – A/B-Vergleich</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>body{{font-family:'Segoe UI',sans-serif;background:#f9fafb;color:#111827;margin:0;padding:24px}}
.wrap{{max-width:900px;margin:0 auto}}h1{{font-size:1.35rem;margin-bottom:2px}}
.sub{{color:#6b7280;font-size:.9rem;margin-bottom:16px}}
.card{{background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:18px;margin-bottom:16px}}
table{{width:100%;border-collapse:collapse;font-size:.85rem}}td,th{{padding:7px 4px;border-top:1px solid #f0f0f0;text-align:left}}
.warn{{font-size:.75rem;color:#6b7280;margin-top:14px}}</style></head><body><div class="wrap">
<h1>🏁 News-Bot Arena – A/B-Vergleich der Varianten</h1>
<div class="sub">Stand: {ts} · Markt {marktstatus} · gleiche Signale, verschiedene Schalter · je Start 10.000 € · reines Papiergeld</div>
<div class="card"><canvas id="c" height="120"></canvas></div>
<div class="card"><b>Rangliste (Depotwert)</b><table>
<tr><th>Variante</th><th style="text-align:right">Depot</th><th style="text-align:right">Rendite</th><th style="text-align:right">vs. Buy&amp;Hold</th></tr>
{rows}</table></div>
<div class="warn">baseline = aktueller Bot (alle Schalter aus). regime/social/confidence = je ein Upgrade an, all = alle an.
Signale (Schlagzeilen + StockTwits + Momentum) werden pro Lauf einmal geholt und geteilt; nur die Portfolio-Bildung
unterscheidet sich. Kein Anlagerat.</div>
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
    ts = now_et.strftime('%Y-%m-%d %H:%M ET')
    marktstatus = ('OFFEN' if offen else 'NACH-SCHLUSS' if nach_close
                   else 'VORBÖRSLICH' if handelstag else 'GESCHLOSSEN')

    st = json.load(open(F['state.json'])) if os.path.exists(F['state.json']) else None
    all_held = set()
    if st:
        for v in st['variants'].values():
            all_held |= set(v['pos'])

    settled, close_s, _mom, kandidaten = nb.settled_und_momentum(now_et, all_held)
    if 'SPY' not in close_s:
        print('-' * 60)
        print(f'RUN ts={ts} markt={marktstatus} trades=0 (Cache waermt auf – Benchmark fehlt)')
        return

    if st is None:                                   # Erstinitialisierung
        st = {'start': heute, 'bh_anteile': START / close_s['SPY'],
              'variants': {n: {'cash': START, 'pos': {}, 'handelstage': 0, 'letzter_tag': None}
                           for n, *_ in VARIANTS}}
        print(f'Arena initialisiert: {len(VARIANTS)} Varianten je {START:,.0f} € (Universum S&P 500 + DAX).')

    manage = list(dict.fromkeys(list(kandidaten) + list(all_held)))
    mark = nb.marks_holen(now_et, set(manage) | {BENCH}, close_s)

    # --- Teure Signale EINMAL holen; Rohdaten je Titel merken ---
    raw = None
    if offen:
        news = nb.news_holen(manage)
        soc_all = nb.social_holen(manage)
        raw = {}
        for t in manage:
            s = settled[t].dropna() if t in settled.columns else settled.iloc[0:0]
            mom = (s.iloc[-1] / s.iloc[-6] - 1) * 100 if len(s) > 6 else 0.0
            sent, _hl = nb.sentiment(news.get(t, []))
            soc, n_soc, _shl = soc_all.get(t, (0, 0, ''))
            raw[t] = {'sent': sent, 'soc': soc, 'n_soc': n_soc, 'mom': mom}

    regime, _ratio = nb.market_regime(settled)

    total_trades, werte = 0, {}
    for name, reg_on, soc_on, conf_on in VARIANTS:
        v = st['variants'][name]
        v.setdefault('letzter_tag', None)
        v.setdefault('handelstage', 0)
        neuer_tag = handelstag and v['letzter_tag'] != heute
        if neuer_tag:
            v['handelstage'] += 1
            v['letzter_tag'] = heute
        scores = None
        if offen and raw is not None:
            scores = {t: score_from_raw(raw[t], soc_on) for t in manage}
        total_trades += step_variant(v, scores, mark, close_s, v['handelstage'],
                                     regime, (reg_on, soc_on, conf_on), neuer_tag)
        werte[name] = v['cash'] + sum(p['stk'] * (mark.get(t) or close_s.get(t)
                                      or p.get('einstieg', 0.0)) for t, p in v['pos'].items())

    bench = st['bh_anteile'] * (mark.get(BENCH) or close_s.get(BENCH, 0.0))
    _history_update(heute, werte, bench)
    json.dump(st, open(F['state.json'], 'w'), indent=2)
    _dashboard(ts, marktstatus, werte, bench)

    print('-' * 60)
    print(f'RUN ts={ts} markt={marktstatus} trades={total_trades}')
    for name, *_ in sorted(VARIANTS, key=lambda v: -werte[v[0]]):
        print(f'  {name:11s} {werte[name]:>11,.2f} € ({(werte[name]/START-1)*100:+.2f} %)')
    print(f'  {"BuyHold":11s} {bench:>11,.2f} € ({(bench/START-1)*100:+.2f} %)')
    if not offen:
        print(f'(Markt {marktstatus} – nur Bewertung, kein Handel.)')


if __name__ == '__main__':
    main()
