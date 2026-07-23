# -*- coding: utf-8 -*-
"""
NEWS-BOT · ALPACA PAPER-ANBINDUNG
=================================
Spiegelt die Signal-Logik des Simulators (news_bot.py) auf ein ECHTES
Alpaca-PAPER-Konto: gleiche Nachrichten-/StockTwits-/Momentum-Signale, aber die
Orders gehen als echte Paper-Orders an Alpaca, und Positionen/Depotwert werden
von Alpaca gelesen (= Quelle der Wahrheit). So lässt sich das Verhalten live im
Alpaca-Dashboard beobachten – ohne echtes Geld.

WICHTIG
* Läuft ausschließlich gegen das PAPER-Konto (TradingClient(paper=True)). Es wird
  KEIN echtes Geld bewegt. Für Live-Handel müsste man den Client bewusst umstellen –
  das macht dieses Skript absichtlich nicht.
* Universum: alle S&P-500-Titel (nur US-Aktien – Alpaca handelt keine Frankfurt/
  Ausland-Titel; DAX/MSCI-World laufen daher nur im Simulator). Pro Lauf werden
  per Momentum-Vorfilter die staerksten Bewegungen fuer News/Social ausgewaehlt.
* Sizing wie im Simulator: alle Assets mit |Score| >= 3 gleichgewichtet
  (Depotwert ÷ Anzahl), kein Hebel; Haltedauer <= 5 Handelstage.
* Kein Anlagerat – reines Mess-Experiment.

Setup siehe: ANLEITUNG_Alpaca_Paper.md
"""
import os, json, csv
from datetime import datetime

try:
    import numpy as np
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import MarketOrderRequest
    from alpaca.trading.enums import OrderSide, TimeInForce, PositionSide
except ImportError:
    raise SystemExit("Bitte zuerst installieren:  pip install alpaca-py numpy --break-system-packages")

# Signal-Logik aus dem Simulator wiederverwenden (kein Doppel-Code)
import news_bot as nb

ALPACA_ASSETS = nb.U.US_UNIVERSE                 # US-Universum: alle S&P-500-Titel
TOPN = nb.TOPN                                    # nur staerkste Momentum-Bewegungen scannen
MAXTAGE = nb.MAXTAGE
BAND = nb.BAND
B = os.path.dirname(os.path.abspath(__file__))
F = {k: os.path.join(B, f'news_bot_alpaca_{k}') for k in ['state.json', 'runs.csv', 'dashboard.html']}


# ----------------------------------------------------------------------------- #
#  Schlüssel laden (Umgebungsvariablen ODER lokale Datei alpaca_keys.env)
# ----------------------------------------------------------------------------- #
def load_keys():
    key = os.environ.get('APCA_API_KEY_ID')
    sec = os.environ.get('APCA_API_SECRET_KEY')
    envfile = os.path.join(B, 'alpaca_keys.env')
    if (not key or not sec) and os.path.exists(envfile):
        for line in open(envfile):
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k == 'APCA_API_KEY_ID':
                key = key or v
            elif k == 'APCA_API_SECRET_KEY':
                sec = sec or v
    if not key or not sec:
        raise SystemExit("Keine API-Keys gefunden. Lege 'alpaca_keys.env' an (siehe Anleitung) "
                         "oder setze APCA_API_KEY_ID / APCA_API_SECRET_KEY.")
    return key, sec


# ----------------------------------------------------------------------------- #
#  Reine Planungsfunktion (ohne Netz -> testbar): Ziel-Orderliste bestimmen
# ----------------------------------------------------------------------------- #
def plan_orders(sig, equity, positions, prices, entry_dates, today,
                assets=ALPACA_ASSETS, maxtage=MAXTAGE, band=BAND):
    """positions/prices: {sym: signed_qty}/{sym: kurs}. entry_dates: {sym:'YYYY-MM-DD'}.
    Liefert (orders, ziel_dir, N). orders = Liste {sym,cur,tgt,delta,flip,aged}."""
    aged = {sym for sym in positions if positions[sym] != 0 and sym in entry_dates
            and np.busday_count(entry_dates[sym], today) >= maxtage}
    ziel_dir = {t: (1 if sig[t]['score'] >= 3 else -1)
                for t in assets if abs(sig[t]['score']) >= 3 and t not in aged}
    N = len(ziel_dir)
    tgt_notional = (equity / N) if N else 0.0
    orders = []
    for t in assets:
        price = prices.get(t, 0) or 0
        if price <= 0:
            continue
        cur = positions.get(t, 0.0)
        tgt = int((tgt_notional * ziel_dir[t]) / price) if t in ziel_dir else 0  # ganze Stücke
        delta = tgt - cur
        if tgt != 0 and abs(delta * price) < band * tgt_notional:   # Deadband
            continue
        if abs(delta) < 1e-9:
            continue
        flip = (cur > 0 and tgt < 0) or (cur < 0 and tgt > 0)
        orders.append({'sym': t, 'cur': cur, 'tgt': tgt, 'delta': delta,
                       'flip': flip, 'aged': t in aged})
    return orders, ziel_dir, N


# ----------------------------------------------------------------------------- #
#  Live gegen Alpaca-Paper
# ----------------------------------------------------------------------------- #
def run():
    key, sec = load_keys()
    trading = TradingClient(key, sec, paper=True)          # << immer PAPER
    now_et = datetime.now(nb.ET)
    heute = now_et.strftime('%Y-%m-%d')
    ts = now_et.strftime('%Y-%m-%d %H:%M ET')

    acct = trading.get_account()
    equity = float(acct.equity)
    poslist = trading.get_all_positions()
    positions = {}
    for p in poslist:
        q = float(p.qty)
        if getattr(p, 'side', None) == PositionSide.SHORT and q > 0:
            q = -q
        positions[p.symbol] = q

    clock = trading.get_clock()
    st = json.load(open(F['state.json'])) if os.path.exists(F['state.json']) else {}
    entry_dates = st.get('entry_dates', {})

    trades = 0
    if not clock.is_open:
        print(f'RUN ts={ts} markt=GESCHLOSSEN trades=0 equity={equity:.2f}')
        print('(US-Markt geschlossen – nur Bewertung, keine Orders. Handel zur US-Handelszeit.)')
    else:
        held = set(positions)
        # Tages-Cache + Momentum-Vorfilter auf dem US-Universum (S&P 500)
        settled, close_s, _mom, cand = nb.settled_und_momentum(
            now_et, held, assets=ALPACA_ASSETS, topn=TOPN)
        # Auch Alt-Positionen ausserhalb des S&P 500 (z. B. ETFs) verwalten/schliessen
        manage = list(dict.fromkeys(list(cand) + list(held)))
        mark = nb.marks_holen(now_et, manage, close_s)
        news = nb.news_holen(cand)
        sig = nb.signale_rechnen(settled, news, cand)
        for t in manage:                 # fehlende Signale (Alt-Positionen) -> neutral -> Exit
            sig.setdefault(t, {'score': 0, 'mom': 0, 'stimmung': 0,
                               'treiber': '', 'soc': 0, 'n_soc': 0})
        prices = {t: mark.get(t, 0) for t in manage}
        orders, ziel_dir, N = plan_orders(sig, equity, positions, prices,
                                          entry_dates, heute, assets=manage)

        for o in orders:
            sym, delta, tgt = o['sym'], o['delta'], o['tgt']
            try:
                # Flip (long<->short) sauber: erst glattstellen, dann neu eröffnen
                if o['flip'] and abs(o['cur']) > 0:
                    trading.close_position(sym)
                    delta = tgt                      # nach Glattstellung volle Zielmenge
                # Shorten nur, wenn der Wert leihbar ist
                if tgt < 0:
                    try:
                        if not trading.get_asset(sym).shortable:
                            print(f'  {sym}: nicht shortbar – übersprungen'); continue
                    except Exception:
                        pass
                if tgt == 0:                         # Position schließen
                    trading.close_position(sym)
                    entry_dates.pop(sym, None)
                    print(f'  EXIT {sym}' + (' (Haltedauer)' if o['aged'] else ''))
                else:
                    side = OrderSide.BUY if delta > 0 else OrderSide.SELL
                    trading.submit_order(MarketOrderRequest(
                        symbol=sym, qty=abs(int(delta)), side=side, time_in_force=TimeInForce.DAY))
                    entry_dates.setdefault(sym, heute)   # Einstiegstag für Haltedauer merken
                    print(f'  {"BUY" if delta>0 else "SELL"} {abs(int(delta))} {sym} -> Ziel {tgt}')
                trades += 1
            except Exception as e:
                print(f'  Order {sym} fehlgeschlagen: {e}')
        # Einstiegstage für nicht mehr gehaltene Ziele aufräumen
        gehalten = {o['sym'] for o in orders if o['tgt'] != 0} | {s for s, q in positions.items() if q}
        entry_dates = {s: d for s, d in entry_dates.items() if s in gehalten}
        print(f'RUN ts={ts} markt=OFFEN trades={trades} equity={equity:.2f}')

    # State + Audit
    st['entry_dates'] = entry_dates
    st['letzter_lauf'] = ts
    json.dump(st, open(F['state.json'], 'w'), indent=2)
    _runlog(ts, 'OFFEN' if clock.is_open else 'GESCHLOSSEN', equity, positions)
    _dashboard(trading, ts, clock.is_open)

    pos_txt = ', '.join(f"{'LONG' if q>0 else 'SHORT'} {s}" for s, q in positions.items() if q) or 'keine'
    print(f'Alpaca-Paper: Depotwert {equity:,.2f} $ | Positionen: {pos_txt}')
    print(f'Konto-Status: {acct.status} | Cash {float(acct.cash):,.2f} $ | Kaufkraft {float(acct.buying_power):,.2f} $')


def _runlog(ts, markt, equity, positions):
    pos = ' / '.join(f"{'LONG' if q>0 else 'SHORT'} {s}:{q:g}" for s, q in positions.items() if q) or '-'
    neu = not os.path.exists(F['runs.csv'])
    with open(F['runs.csv'], 'a', newline='') as f:
        w = csv.writer(f)
        if neu:
            w.writerow(['Zeitstempel', 'Markt', 'Depotwert', 'Positionen'])
        w.writerow([ts, markt, f'{equity:.2f}', pos])


def _dashboard(trading, ts, offen):
    try:
        acct = trading.get_account()
        poslist = trading.get_all_positions()
    except Exception:
        return
    eq = float(acct.equity)
    rows = ''.join(
        f"<tr><td>{p.side.value.upper() if hasattr(p.side,'value') else p.side}</td><td>{p.symbol}</td>"
        f"<td style='text-align:right'>{float(p.qty):g}</td>"
        f"<td style='text-align:right'>{float(p.avg_entry_price):.2f}</td>"
        f"<td style='text-align:right'>{float(p.current_price or 0):.2f}</td>"
        f"<td style='text-align:right'>{float(p.unrealized_plpc or 0)*100:+.2f} %</td></tr>"
        for p in poslist) or '<tr><td colspan=6>keine offenen Positionen</td></tr>'
    runrows = ''
    if os.path.exists(F['runs.csv']):
        with open(F['runs.csv']) as f:
            rr = [x for x in csv.reader(f)][1:][-10:]
        runrows = ''.join(f'<tr><td>{x[0]}</td><td>{x[1]}</td>'
                          f'<td style="text-align:right">{float(x[2]):,.0f} $</td></tr>' for x in reversed(rr))
    html = f"""<!DOCTYPE html><html lang="de"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>News-Bot · Alpaca Paper</title>
<style>body{{font-family:'Segoe UI',sans-serif;background:#f9fafb;color:#111827;margin:0;padding:24px}}
.wrap{{max-width:880px;margin:0 auto}}h1{{font-size:1.35rem;margin-bottom:2px}}.sub{{color:#6b7280;font-size:.9rem;margin-bottom:16px}}
.kpi{{display:inline-block;background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:14px 18px;margin:0 10px 14px 0}}
.kpi b{{display:block;font-size:1.3rem}}.kpi span{{color:#6b7280;font-size:.78rem}}
.card{{background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:18px;margin-bottom:16px}}
table{{width:100%;border-collapse:collapse;font-size:.83rem}}td,th{{padding:6px 4px;border-top:1px solid #f0f0f0;text-align:left}}
.warn{{font-size:.75rem;color:#6b7280;margin-top:14px}}.badge{{display:inline-block;padding:2px 8px;border-radius:6px;font-size:.72rem;font-weight:600;background:#eef2ff;color:#4338ca}}</style></head>
<body><div class="wrap"><h1>🅰️ News-Bot · Alpaca <span class="badge">PAPER</span></h1>
<div class="sub">Stand: {ts} · Markt {'OFFEN' if offen else 'GESCHLOSSEN'} · Live-Depot von Alpaca · kein echtes Geld</div>
<div class="kpi"><b>{eq:,.0f} $</b><span>Depotwert (Alpaca Paper)</span></div>
<div class="kpi"><b>{float(acct.cash):,.0f} $</b><span>Cash</span></div>
<div class="kpi"><b>{len(poslist)}</b><span>offene Positionen</span></div>
<div class="card"><b>Offene Positionen (live von Alpaca)</b><table>
<tr><th>Seite</th><th>Symbol</th><th>Stück</th><th>Einstieg</th><th>Aktuell</th><th>P&amp;L</th></tr>{rows}</table></div>
<div class="card"><b>Letzte Läufe</b><table><tr><th>Zeitpunkt</th><th>Markt</th><th style="text-align:right">Depotwert</th></tr>{runrows or '<tr><td>–</td></tr>'}</table></div>
<div class="warn">Signal-Logik identisch zum Simulator (news_bot.py): Schlagzeilen + StockTwits-Social + Momentum, alle klaren Signale gleichgewichtet, kein Hebel. Orders gehen als echte PAPER-Orders an Alpaca; Depot/Positionen kommen live von Alpaca. Kein echtes Geld, kein Anlagerat.</div>
</div></body></html>"""
    with open(F['dashboard.html'], 'w') as f:
        f.write(html)


if __name__ == '__main__':
    run()
