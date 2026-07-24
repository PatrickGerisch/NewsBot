# -*- coding: utf-8 -*-
"""
PAPER-TRADING-BOT v2 – Dual Momentum, mit Kennzahlen & Live-Dashboard
=====================================================================
Strategie: Monatsende-Check. Halte das stärkere aus S&P 500 / Emerging Markets
(12-Monats-Momentum), wenn dessen Momentum > 0; sonst US-Anleihen (IEF).
Papiergeld. Jeder Lauf aktualisiert bot_history.csv und bot_dashboard.html.
Benchmark: Buy & Hold S&P 500 ab Startdatum. Kein Anlagerat. Kein echtes Geld.
"""
import json, os, csv
from datetime import date

TICKER = {'US': '^GSPC', 'EM': 'EEM', 'BOND': 'IEF'}
NAME = {'US': 'S&P 500', 'EM': 'Emerging Markets', 'BOND': 'US-Anleihen (IEF)'}
STARTKAPITAL = 10_000.0
KOSTEN = 0.001

BASIS = os.path.dirname(os.path.abspath(__file__))
F_STATE = os.path.join(BASIS, 'bot_state.json')
F_HIST = os.path.join(BASIS, 'bot_history.csv')
F_TRADES = os.path.join(BASIS, 'bot_trades.csv')
F_DASH = os.path.join(BASIS, 'bot_dashboard.html')


def lade_kurse():
    import yfinance as yf
    px = yf.download(list(TICKER.values()), period='2y', auto_adjust=True,
                     progress=False)['Close']
    px = px.dropna()
    return px


def signal(px):
    mom = {k: px[t].iloc[-1] / px[t].iloc[-252] - 1 for k, t in TICKER.items()
           if len(px) >= 252}
    if not mom:
        return 'BOND', {}
    best = 'US' if mom['US'] >= mom['EM'] else 'EM'
    return (best if mom[best] > 0 else 'BOND'), mom


def main():
    px = lade_kurse()
    # Datum des letzten tatsaechlichen Handelstags (Kurs-Balken) statt der Wanduhr:
    # so wird jeder Tagespunkt korrekt datiert - auch bei Nachhol-/Morgenlaeufen -,
    # und ein verpasster Handelstag wird beim naechsten Lauf sauber nachgetragen.
    heute = str(px.index[-1].date())
    kurs = {k: float(px[t].iloc[-1]) for k, t in TICKER.items()}

    if os.path.exists(F_STATE):
        with open(F_STATE) as f:
            s = json.load(f)
    else:
        ziel, mom = signal(px)
        anteile = STARTKAPITAL * (1 - KOSTEN) / kurs[ziel]
        s = {'start': heute, 'asset': ziel, 'anteile': anteile,
             'bh_anteile': STARTKAPITAL / kurs['US'], 'letzter_check_monat': heute[:7],
             'n_trades': 1}
        _trade(heute, f'START: Kauf {NAME[ziel]}', kurs[ziel], anteile * kurs[ziel])
        print(f'Bot initialisiert: {NAME[ziel]} gekauft.')

    # --- Monatsende-Logik: Signal nur bei neuem Monat neu bewerten ---
    if heute[:7] != s['letzter_check_monat']:
        ziel, mom = signal(px)
        s['letzter_check_monat'] = heute[:7]
        if ziel != s['asset']:
            wert = s['anteile'] * kurs[s['asset']] * (1 - KOSTEN)
            s['anteile'] = wert * (1 - KOSTEN) / kurs[ziel]
            alt, s['asset'] = s['asset'], ziel
            s['n_trades'] += 2
            _trade(heute, f'WECHSEL {NAME[alt]} -> {NAME[ziel]}', kurs[ziel], wert)
            print(f'>> Umschichtung: {NAME[alt]} -> {NAME[ziel]}')
        else:
            print('Monats-Check: Signal unverändert.')
    else:
        print('Kein Monatswechsel – nur Bewertung.')

    depot = s['anteile'] * kurs[s['asset']]
    bench = s['bh_anteile'] * kurs['US']

    # --- Historie fortschreiben (1 Zeile pro Handelstag, Upsert) ---
    # Upsert: laeuft der Bot mehrfach am selben Handelstag (z. B. stuendlich in der
    # Cloud), wird die Tageszeile aktualisiert statt dupliziert -> der letzte Lauf
    # nach US-Schluss hinterlaesst den Schlusskurs. Einmal-taeglich unveraendert.
    zeilen = []
    if os.path.exists(F_HIST):
        with open(F_HIST) as f:
            zeilen = [r for r in csv.reader(f)][1:]
    neue_zeile = [heute, f'{depot:.2f}', f'{bench:.2f}']
    if zeilen and zeilen[-1][0] == heute:
        zeilen[-1] = neue_zeile
    else:
        zeilen.append(neue_zeile)
    with open(F_HIST, 'w', newline='') as f:
        w = csv.writer(f); w.writerow(['Datum', 'Strategie', 'BuyHold'])
        w.writerows(zeilen)

    kpi = kennzahlen(zeilen, s)
    with open(F_STATE, 'w') as f:
        json.dump(s, f, indent=2)
    dashboard(zeilen, s, kpi)

    print('-' * 56)
    print(f"Position: {NAME[s['asset']]} | Depot {depot:,.2f} € | "
          f"Benchmark {bench:,.2f} € | Diff {depot-bench:+,.2f} €")
    print(f"Kennzahlen: {kpi['rendite']:+.2f}% Rendite, MaxDD {kpi['maxdd']:.2f}%, "
          f"{s['n_trades']} Trades | Dashboard aktualisiert.")


def kennzahlen(zeilen, s):
    werte = [float(z[1]) for z in zeilen]
    bench = [float(z[2]) for z in zeilen]
    r = (werte[-1] / STARTKAPITAL - 1) * 100
    rb = (bench[-1] / STARTKAPITAL - 1) * 100
    peak, dd = 0, 0
    for w in werte:
        peak = max(peak, w); dd = min(dd, w / peak - 1)
    return dict(rendite=r, bench=rb, diff=r - rb, maxdd=dd * 100, tage=len(werte))


def _trade(datum, aktion, kurs, wert):
    neu = not os.path.exists(F_TRADES)
    with open(F_TRADES, 'a', newline='') as f:
        w = csv.writer(f)
        if neu: w.writerow(['Datum', 'Aktion', 'Kurs', 'Depotwert'])
        w.writerow([datum, aktion, f'{kurs:.2f}', f'{wert:.2f}'])


def dashboard(zeilen, s, k):
    labels = json.dumps([z[0] for z in zeilen])
    d1 = json.dumps([float(z[1]) for z in zeilen])
    d2 = json.dumps([float(z[2]) for z in zeilen])
    trades = ''
    if os.path.exists(F_TRADES):
        with open(F_TRADES) as f:
            rows = [r for r in csv.reader(f)][1:][-8:]
        trades = ''.join(f'<tr><td>{r[0]}</td><td>{r[1]}</td><td style="text-align:right">{r[3]} €</td></tr>' for r in reversed(rows))
    farbe = '#0e9f6e' if k['diff'] >= 0 else '#e02424'
    html = f"""<!DOCTYPE html><html lang="de"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Paper-Bot Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>body{{font-family:'Segoe UI',sans-serif;background:#f9fafb;color:#111827;margin:0;padding:24px}}
.wrap{{max-width:860px;margin:0 auto}}h1{{font-size:1.4rem}}.sub{{color:#6b7280;font-size:.9rem;margin-bottom:18px}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:20px}}
.kpi{{background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:14px}}
.kpi b{{display:block;font-size:1.35rem}}.kpi span{{color:#6b7280;font-size:.78rem}}
.card{{background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:18px;margin-bottom:16px}}
table{{width:100%;border-collapse:collapse;font-size:.85rem}}td{{padding:6px 4px;border-top:1px solid #f0f0f0}}
.warn{{font-size:.75rem;color:#6b7280;margin-top:14px}}</style></head><body><div class="wrap">
<h1>📊 Paper-Trading-Bot – Dual Momentum</h1>
<div class="sub">Stand: {zeilen[-1][0]} · Papiergeld, kein echtes Depot · Start: {s['start']}</div>
<div class="kpis">
<div class="kpi"><b>{float(zeilen[-1][1]):,.0f} €</b><span>Depotwert (Start 10.000)</span></div>
<div class="kpi"><b>{k['rendite']:+.2f} %</b><span>Rendite Strategie</span></div>
<div class="kpi"><b>{k['bench']:+.2f} %</b><span>Buy &amp; Hold S&amp;P 500</span></div>
<div class="kpi" style="border-color:{farbe}"><b style="color:{farbe}">{k['diff']:+.2f} %-Pkt.</b><span>Über-/Unterrendite</span></div>
<div class="kpi"><b>{k['maxdd']:.2f} %</b><span>Max. Drawdown</span></div>
<div class="kpi"><b>{NAME[s['asset']]}</b><span>Aktuelle Position · {s['n_trades']} Trades</span></div>
</div>
<div class="card"><canvas id="c" height="110"></canvas></div>
<div class="card"><b>Letzte Trades</b><table>{trades or '<tr><td>–</td></tr>'}</table></div>
<div class="warn">Regelwerk: Monatsende-Check, 12-Monats-Momentum S&amp;P 500 vs. Emerging Markets, bei negativem Momentum Wechsel in Anleihen. 0,1 % Kosten je Umschichtung. Diese Simulation dient dem ehrlichen Strategietest – Ergebnisse der Vergangenheit garantieren nichts. Kein Anlagerat.</div></div>
<script>new Chart(document.getElementById('c'),{{type:'line',data:{{labels:{labels},datasets:[
{{label:'Strategie',data:{d1},borderColor:'#1a56db',borderWidth:2,pointRadius:0,tension:.2}},
{{label:'Buy & Hold S&P 500',data:{d2},borderColor:'#9ca3af',borderWidth:2,pointRadius:0,tension:.2}}]}},
options:{{plugins:{{legend:{{position:'bottom'}}}},scales:{{y:{{ticks:{{callback:v=>v.toLocaleString('de-DE')+' €'}}}}}}}}}});</script>
</body></html>"""
    with open(F_DASH, 'w') as f:
        f.write(html)


if __name__ == '__main__':
    main()
