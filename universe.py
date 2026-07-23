# -*- coding: utf-8 -*-
"""
AKTIEN-UNIVERSUM + TAGES-CACHE fuer die News-Bots
=================================================
Erweitert die Bots von 10 Titeln auf S&P 500 + DAX 40, GLEICHE Kriterien
(Schlagzeilen-Sentiment + StockTwits-Social + 5-Tage-Momentum).

WARUM EIN CACHE? Fuer die Momentum-Rangliste brauchen beide Bots die
abgeschlossenen TAGES-Schlusskurse des gesamten Universums (~540 Titel).
Diese aendern sich innerhalb eines Handelstages NICHT. Alle ~540 pro
stuendlichem Lauf neu zu laden dauert ~2 Min und sprengt jedes Zeitlimit.
Daher: die Schlusskurse werden in news_bot_daily_cache.csv gehalten und pro
Lauf nur ZEITGEBOXT inkrementell aufgefrischt (jeder Titel hoechstens einmal
pro Kalendertag). Momentum kommt aus dem Cache; die teuren News-/StockTwits-
Abfragen laufen nur fuer die Top-Kandidaten (staerkste Momentum-Bewegungen).

So bleibt jeder einzelne Lauf unter dem Zeitlimit, waehrend das volle
Universum ueber die Laeufe eines Tages abgedeckt wird.

Symbole in yfinance-Schreibweise (Punkt -> Bindestrich fuer US-Klassen;
.DE = Frankfurt, .PA = Paris fuer DAX-Titel).
"""
import os, csv, json, time
from datetime import datetime


SP500 = [
    'MMM', 'AOS', 'ABT', 'ABBV', 'ACN', 'ADBE', 'AMD', 'AES', 'AFL', 'A',
    'APD', 'ABNB', 'AKAM', 'ALB', 'ARE', 'ALGN', 'ALLE', 'LNT', 'ALL', 'GOOGL',
    'GOOG', 'MO', 'AMZN', 'AMCR', 'AEE', 'AEP', 'AXP', 'AIG', 'AMT', 'AWK',
    'AMP', 'AME', 'AMGN', 'APH', 'ADI', 'AON', 'APA', 'APO', 'AAPL', 'AMAT',
    'APP', 'APTV', 'ACGL', 'ADM', 'ARES', 'ANET', 'AJG', 'AIZ', 'T', 'ATO',
    'ADSK', 'ADP', 'AZO', 'AVB', 'AVY', 'AXON', 'BKR', 'BALL', 'BAC', 'BAX',
    'BDX', 'BRK-B', 'BBY', 'TECH', 'BIIB', 'BLK', 'BX', 'XYZ', 'BNY', 'BA',
    'BKNG', 'BSX', 'BMY', 'AVGO', 'BR', 'BRO', 'BF-B', 'BLDR', 'BG', 'BXP',
    'CHRW', 'CDNS', 'CPT', 'COF', 'CAH', 'CCL', 'CARR', 'CVNA', 'CASY', 'CAT',
    'CBOE', 'CBRE', 'CDW', 'COR', 'CNC', 'CNP', 'CF', 'CRL', 'SCHW', 'CHTR',
    'CVX', 'CMG', 'CB', 'CHD', 'CIEN', 'CI', 'CINF', 'CTAS', 'CSCO', 'C',
    'CFG', 'CLX', 'CME', 'CMS', 'KO', 'CTSH', 'COHR', 'COIN', 'CL', 'CMCSA',
    'FIX', 'COP', 'ED', 'STZ', 'CEG', 'COO', 'CPRT', 'GLW', 'CPAY', 'CTVA',
    'CSGP', 'COST', 'CRH', 'CRWD', 'CCI', 'CSX', 'CMI', 'CVS', 'DHR', 'DRI',
    'DDOG', 'DVA', 'DECK', 'DE', 'DELL', 'DAL', 'DVN', 'DXCM', 'FANG', 'DLR',
    'DG', 'DLTR', 'D', 'DPZ', 'DASH', 'DOV', 'DOW', 'DHI', 'DTE', 'DUK',
    'DD', 'ETN', 'EBAY', 'ECHO', 'ECL', 'EIX', 'EW', 'EA', 'ELV', 'EME',
    'EMR', 'ETR', 'EOG', 'EQT', 'EFX', 'EQIX', 'EQR', 'ERIE', 'ESS', 'EL',
    'EG', 'EVRG', 'ES', 'EXC', 'EXE', 'EXPE', 'EXPD', 'EXR', 'XOM', 'FFIV',
    'FDS', 'FICO', 'FAST', 'FRT', 'FDX', 'FDXF', 'FIS', 'FITB', 'FSLR', 'FE',
    'FISV', 'FLEX', 'F', 'FTNT', 'FTV', 'FOXA', 'FOX', 'BEN', 'FCX', 'GRMN',
    'IT', 'GE', 'GEHC', 'GEV', 'GEN', 'GNRC', 'GD', 'GIS', 'GM', 'GPC',
    'GILD', 'GPN', 'GL', 'GDDY', 'GS', 'HAL', 'HIG', 'HAS', 'HCA', 'DOC',
    'HSIC', 'HSY', 'HPE', 'HLT', 'HD', 'HONA', 'HON', 'HRL', 'HST', 'HWM',
    'HPQ', 'HUBB', 'HUM', 'HBAN', 'HII', 'IBM', 'IEX', 'IDXX', 'ITW', 'INCY',
    'IR', 'PODD', 'INTC', 'IBKR', 'ICE', 'IFF', 'IP', 'INTU', 'ISRG', 'IVZ',
    'INVH', 'IQV', 'IRM', 'JBHT', 'JBL', 'JKHY', 'J', 'JNJ', 'JCI', 'JPM',
    'KVUE', 'KDP', 'KEY', 'KEYS', 'KMB', 'KIM', 'KMI', 'KKR', 'KLAC', 'KHC',
    'KR', 'LHX', 'LH', 'LRCX', 'LVS', 'LDOS', 'LEN', 'LII', 'LLY', 'LIN',
    'LYV', 'LMT', 'L', 'LOW', 'LULU', 'LITE', 'LYB', 'MTB', 'MPC', 'MAR',
    'MRSH', 'MLM', 'MRVL', 'MAS', 'MA', 'MKC', 'MCD', 'MCK', 'MDT', 'MRK',
    'META', 'MET', 'MTD', 'MGM', 'MCHP', 'MU', 'MSFT', 'MAA', 'MRNA', 'TAP',
    'MDLZ', 'MPWR', 'MNST', 'MCO', 'MS', 'MOS', 'MSI', 'MSCI', 'NDAQ', 'NTAP',
    'NFLX', 'NEM', 'NWSA', 'NWS', 'NEE', 'NKE', 'NI', 'NDSN', 'NSC', 'NTRS',
    'NOC', 'NCLH', 'NRG', 'NUE', 'NVDA', 'NVR', 'NXPI', 'ORLY', 'OXY', 'ODFL',
    'OMC', 'ON', 'OKE', 'ORCL', 'OTIS', 'PCAR', 'PKG', 'PLTR', 'PANW', 'PSKY',
    'PH', 'PAYX', 'PYPL', 'PNR', 'PEP', 'PFE', 'PCG', 'PM', 'PSX', 'PNW',
    'PNC', 'PPG', 'PPL', 'PFG', 'PG', 'PGR', 'PLD', 'PRU', 'PEG', 'PTC',
    'PSA', 'PHM', 'PWR', 'QCOM', 'DGX', 'Q', 'RL', 'RJF', 'RTX', 'O',
    'REG', 'REGN', 'RF', 'RSG', 'RMD', 'RVTY', 'HOOD', 'ROK', 'ROL', 'ROP',
    'ROST', 'RCL', 'SPGI', 'CRM', 'SNDK', 'SBAC', 'SLB', 'STX', 'SRE', 'NOW',
    'SHW', 'SPG', 'SWKS', 'SJM', 'SW', 'SNA', 'SOLV', 'SO', 'LUV', 'SWK',
    'SBUX', 'STT', 'STLD', 'STE', 'SYK', 'SMCI', 'SYF', 'SNPS', 'SYY', 'TMUS',
    'TROW', 'TTWO', 'TPR', 'TRGP', 'TGT', 'TEL', 'TDY', 'TER', 'TSLA', 'TXN',
    'TPL', 'TXT', 'TMO', 'TJX', 'TKO', 'TTD', 'TSCO', 'TT', 'TDG', 'TRV',
    'TRMB', 'TFC', 'TYL', 'TSN', 'USB', 'UBER', 'UDR', 'ULTA', 'UNP', 'UAL',
    'UPS', 'URI', 'UNH', 'UHS', 'VLO', 'VEEV', 'VTR', 'VLTO', 'VRSN', 'VRSK',
    'VZ', 'VRTX', 'VRT', 'VTRS', 'VICI', 'V', 'VST', 'VMC', 'WRB', 'GWW',
    'WAB', 'WMT', 'DIS', 'WBD', 'WM', 'WAT', 'WEC', 'WFC', 'WELL', 'WST',
    'WDC', 'WY', 'WSM', 'WMB', 'WTW', 'WDAY', 'WYNN', 'XEL', 'XYL', 'YUM',
    'ZBRA', 'ZBH', 'ZTS',
]

DAX = [
    'ADS.DE', 'AIR.PA', 'ALV.DE', 'BAS.DE', 'BAYN.DE', 'BEI.DE', 'BMW.DE', 'BNR.DE', 'CBK.DE', 'CON.DE',
    'DTG.DE', 'DBK.DE', 'DB1.DE', 'DHL.DE', 'DTE.DE', 'EOAN.DE', 'FRE.DE', 'FME.DE', 'G1A.DE', 'HNR1.DE',
    'HEI.DE', 'HEN3.DE', 'IFX.DE', 'MBG.DE', 'MRK.DE', 'MTX.DE', 'MUV2.DE', 'PAH3.DE', 'QIA.DE', 'RHM.DE',
    'RWE.DE', 'SAP.DE', 'G24.DE', 'SIE.DE', 'ENR.DE', 'SHL.DE', 'SY1.DE', 'VOW3.DE', 'VNA.DE', 'ZAL.DE',
]

BENCH = 'SPY'                       # Benchmark Buy & Hold (nur Bewertung)

US_UNIVERSE   = sorted(set(SP500))                 # Alpaca handelt NUR US-Titel
FULL_UNIVERSE = sorted(set(SP500 + DAX))           # Simulator: US + DAX


# --------------------------------------------------------------------------- #
#  Tages-Cache der abgeschlossenen Schlusskurse (intraday konstant)
# --------------------------------------------------------------------------- #
def cache_paths(base):
    return (os.path.join(base, 'news_bot_daily_cache.csv'),
            os.path.join(base, 'news_bot_daily_cache_meta.json'))


def load_cache(csv_path):
    """Liefert DataFrame (Index=Datum, Spalten=Ticker) oder leeres DataFrame."""
    import pandas as pd
    if os.path.exists(csv_path):
        try:
            return pd.read_csv(csv_path, index_col=0, parse_dates=True)
        except Exception:
            pass
    return pd.DataFrame()


def _save_cache(df, csv_path):
    tmp = csv_path + '.tmp'
    df.sort_index().to_csv(tmp)
    os.replace(tmp, csv_path)


def refresh_cache(base, tickers, today, budget_s=18.0, chunk=40, prioritaet=()):
    """Frischt den Tages-Cache ZEITGEBOXT auf. Jeder Ticker wird pro Kalendertag
    hoechstens einmal geladen (Vermerk in *_meta.json). 'prioritaet' (z. B.
    Benchmark + offene Positionen) wird zuerst aufgefrischt. Gibt das DataFrame
    zurueck. Bei Netz-/Datenfehlern: still weiter, naechster Lauf holt nach."""
    import pandas as pd
    try:
        import yfinance as yf
    except Exception:
        return load_cache(cache_paths(base)[0])

    csv_path, meta_path = cache_paths(base)
    df = load_cache(csv_path)
    meta = {}
    if os.path.exists(meta_path):
        try:
            meta = json.load(open(meta_path))
        except Exception:
            meta = {}

    # heute noch nicht aufgefrischte Titel, Prioritaet nach vorne
    todo = [t for t in tickers if meta.get(t) != today]
    pri  = [t for t in prioritaet if t in todo]
    todo = pri + [t for t in todo if t not in set(pri)]

    t0 = time.time()
    neue = []
    for i in range(0, len(todo), chunk):
        if time.time() - t0 > budget_s:
            break
        grp = todo[i:i + chunk]
        try:
            d = yf.download(grp, period='1mo', interval='1d', auto_adjust=True,
                            progress=False, threads=True)['Close']
        except Exception:
            continue
        if isinstance(d, pd.Series):
            d = d.to_frame(name=grp[0])
        d = d.dropna(axis=1, how='all')
        if not d.empty:
            neue.append(d)
        for t in grp:                       # als "heute versucht" markieren
            meta[t] = today

    if neue:
        # neue Werte ueberschreiben alte; fehlende bleiben erhalten (combine_first)
        merged = pd.concat(neue, axis=1)
        merged = merged.loc[:, ~merged.columns.duplicated()]
        df = merged.combine_first(df) if not df.empty else merged
        df = df.sort_index()
        _save_cache(df, csv_path)
        try:
            json.dump(meta, open(meta_path, 'w'))
        except Exception:
            pass
    return df
