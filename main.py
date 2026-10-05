import base64, urllib.error, json, os, time, threading, re, urllib.request, urllib.parse
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

UA = {"User-Agent": "Mozilla/5.0"}
INDICES = [("^NSEI", "Nifty 50"), ("^BSESN", "Sensex"), ("^NSEBANK", "Bank Nifty"),
           ("^CNXIT", "Nifty IT"), ("^INDIAVIX", "India VIX"), ("^CNXAUTO", "Nifty Auto"),
           ("^CNXFMCG", "Nifty FMCG"), ("^CNXPHARMA", "Nifty Pharma")]
N50 = """ADANIENT ADANIPORTS APOLLOHOSP ASIANPAINT AXISBANK BAJAJ-AUTO BAJFINANCE BAJAJFINSV BEL BHARTIARTL
CIPLA COALINDIA DRREDDY EICHERMOT ETERNAL GRASIM HCLTECH HDFCBANK HDFCLIFE HEROMOTOCO HINDALCO HINDUNILVR
ICICIBANK INDUSINDBK INFY ITC JIOFIN JSWSTEEL KOTAKBANK LT M&M MARUTI NESTLEIND NTPC ONGC POWERGRID
RELIANCE SBILIFE SBIN SHRIRAMFIN SUNPHARMA TATACONSUM TATAMOTORS TATASTEEL TCS TECHM TITAN TRENT ULTRACEMCO WIPRO""".split()

_cache = {}
_lock = threading.Lock()

def cached(key, ttl, fn):
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    try:
        val = fn()
    except Exception as e:
        with _lock:
            hit = _cache.get(key)
        if hit:
            return hit[1]
        raise
    with _lock:
        _cache[key] = (now, val)
    return val

def get(url, timeout=12):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()

def chart(sym, rng="1d", interval="5m"):
    url = "https://query2.finance.yahoo.com/v8/finance/chart/%s?range=%s&interval=%s" % (urllib.parse.quote(sym), rng, interval)
    d = json.loads(get(url))["chart"]["result"][0]
    m = d["meta"]
    closes = (d["indicators"]["quote"][0].get("close") or [])
    pts = [round(c, 2) for c in closes if c is not None]
    price = m.get("regularMarketPrice")
    prev = m.get("chartPreviousClose") or m.get("previousClose")
    chg = (price - prev) if (price is not None and prev) else None
    return {"symbol": sym, "price": price, "prev": prev, "change": chg,
            "pct": (chg / prev * 100) if chg is not None and prev else None,
            "high": m.get("regularMarketDayHigh"), "low": m.get("regularMarketDayLow"),
            "volume": m.get("regularMarketVolume"), "time": m.get("regularMarketTime"),
            "name": m.get("longName") or m.get("shortName") or sym, "spark": pts[-80:]}


import datetime
class RateLimited(Exception): pass
_block_until = [0]
_chart_block_until = [0]

def yget(url):
    if time.time() < _chart_block_until[0]:
        raise RateLimited()
    try:
        return get(url)
    except urllib.error.HTTPError as e:
        if e.code == 429:
            _chart_block_until[0] = time.time() + 600
            raise RateLimited()
        raise

def chart_full(sym, rng="5d", interval="15m"):
    url = "https://query2.finance.yahoo.com/v8/finance/chart/%s?range=%s&interval=%s" % (urllib.parse.quote(sym), rng, interval)
    d = json.loads(yget(url))["chart"]["result"][0]
    return d

def intraday(sym):
    d = chart_full(sym)
    m = d["meta"]; off = m.get("gmtoffset", 19800)
    ts = d.get("timestamp") or []
    q = d["indicators"]["quote"][0]
    days = {}
    for i, t in enumerate(ts):
        c = q["close"][i]
        if c is None: continue
        day = datetime.datetime.utcfromtimestamp(t + off).date().isoformat()
        days.setdefault(day, []).append((q["open"][i], q["high"][i], q["low"][i], c, q["volume"][i] or 0))
    keys = sorted(days)
    if not keys: raise ValueError("nodata")
    today = days[keys[-1]]; prevd = [days[k] for k in keys[:-1]]
    price = m.get("regularMarketPrice") or today[-1][3]
    prev = prevd[-1][-1][3] if prevd else m.get("chartPreviousClose")
    op = today[0][0] or today[0][3]
    hi = max(x[1] for x in today if x[1] is not None); lo = min(x[2] for x in today if x[2] is not None)
    vol = sum(x[4] for x in today); n = len(today)
    pv = [sum(x[4] for x in dd[:n]) for dd in prevd if len(dd) >= 1]
    vratio = (vol / (sum(pv) / len(pv))) if pv and sum(pv) > 0 else None
    chg = price - prev if prev else None
    orb_hi = today[0][1]; orb_lo = today[0][2]
    tv = sum(((x[1] or x[3]) + (x[2] or x[3]) + x[3]) / 3.0 * x[4] for x in today)
    vwap = (tv / vol) if vol else None
    pdh = max((x[1] for x in prevd[-1] if x[1] is not None), default=None) if prevd else None
    pdl = min((x[2] for x in prevd[-1] if x[2] is not None), default=None) if prevd else None
    return {"orb_hi": orb_hi, "orb_lo": orb_lo, "vwap": vwap, "pdh": pdh, "pdl": pdl, "bars": n, "symbol": sym, "name": m.get("longName") or m.get("shortName") or sym, "price": price, "prev": prev,
            "change": chg, "pct": chg / prev * 100 if prev else None, "open": op, "high": hi, "low": lo, "volume": vol,
            "gap": (op - prev) / prev * 100 if prev else None, "vratio": vratio,
            "range": (hi - lo) / prev * 100 if prev else None,
            "from_high": (price - hi) / hi * 100 if hi else None, "from_low": (price - lo) / lo * 100 if lo else None,
            "time": m.get("regularMarketTime"), "day": keys[-1], "spark": [round(x[3], 2) for x in today][-30:]}

def chart(sym, rng="1d", interval="5m"):
    d = json.loads(yget("https://query2.finance.yahoo.com/v8/finance/chart/%s?range=5d&interval=15m" % urllib.parse.quote(sym)))["chart"]["result"][0]
    m = d["meta"]; off = m.get("gmtoffset", 0)
    days = {}
    for t, c in zip(d.get("timestamp") or [], d["indicators"]["quote"][0].get("close") or []):
        if c is None: continue
        days.setdefault(datetime.datetime.utcfromtimestamp(t + off).date().isoformat(), []).append(round(c, 2))
    keys = sorted(days)
    price = m.get("regularMarketPrice")
    prev = days[keys[-2]][-1] if len(keys) > 1 else (m.get("chartPreviousClose") or m.get("previousClose"))
    pts = days[keys[-1]] if keys else []
    chg = (price - prev) if (price is not None and prev) else None
    return {"symbol": sym, "price": price, "prev": prev, "change": chg,
            "pct": (chg / prev * 100) if chg is not None and prev else None,
            "time": m.get("regularMarketTime"), "name": m.get("longName") or m.get("shortName") or sym, "spark": pts[-80:]}

WORLD = [("^GSPC", "S&P 500 (US)"), ("^IXIC", "Nasdaq (US)"), ("^DJI", "Dow Jones (US)"), ("^FTSE", "FTSE 100 (UK)"),
         ("^GDAXI", "DAX (Germany)"), ("^N225", "Nikkei 225 (Japan)"), ("^HSI", "Hang Seng (HK)"), ("000001.SS", "Shanghai (China)")]
COMMOD = [("GC=F", "Gold"), ("SI=F", "Silver"), ("CL=F", "Crude Oil (WTI)"), ("BZ=F", "Brent Crude"), ("NG=F", "Natural Gas"),
          ("HG=F", "Copper"), ("USDINR=X", "US Dollar in INR"), ("BTC-USD", "Bitcoin (USD)")]

STATE = {"idx": [], "world": [], "commod": [], "stocks": [], "funds": [], "stocks_t": 0, "idx_t": 0, "funds_t": 0, "err": ""}

def fetch_many(pairs, workers=3, fn=chart):
    out = []
    def one(p):
        try:
            time.sleep(0.12)
            r = fn(p[0]); r = dict(r); r["name"] = p[1]; return r
        except Exception:
            return None
    with ThreadPoolExecutor(workers) as ex:
        for r in ex.map(one, pairs):
            if r: out.append(r)
    return out

def market_hours():
    n = datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)
    m = n.hour * 60 + n.minute
    return n.weekday() < 5 and 540 <= m <= 945

def refresher():
    last = {"idx": 0, "stk": 0, "fund": 0}
    while True:
        now = time.time()
        try:
            if now - last["idx"] > (120 if market_hours() else 600):
                last["idx"] = now
                for key, lst in (("idx", INDICES), ("world", WORLD), ("commod", COMMOD)):
                    r = fetch_many(lst)
                    if r: STATE[key] = r
                if STATE["idx"]: STATE["idx_t"] = now
            stk_every = 300 if market_hours() else 3600
            if now - last["stk"] > stk_every:
                last["stk"] = now
                r = fetch_many([(s + ".NS", s) for s in N50], 3, intraday)
                for x in r:
                    x["symbol"] = x["symbol"].replace(".NS", "")
                if len(r) >= 25: STATE["stocks"] = r; STATE["stocks_t"] = now
            if now - last["fund"] > 6 * 3600 or not STATE["funds"]:
                last["fund"] = now
                r = load_funds()
                if r: STATE["funds"] = r; STATE["funds_t"] = now
        except Exception as e:
            STATE["err"] = repr(e)
        time.sleep(15)

FUNDS = {"Large Cap": [118632, 120586, 119598, 120465, 120152],
 "Flexi Cap": [122639, 118955, 120662, 120843, 119718],
 "Mid Cap": [118989, 120505, 140228, 147704],
 "Small Cap": [118778, 120828, 125497, 147946, 125354],
 "Index (Nifty 50)": [119063],
 "Tax Saver (ELSS)": [120847, 118473, 135781, 120503, 119723],
 "Balanced / Hybrid": [120377, 118968],
 "Debt / Liquid": [119091, 119800, 120692]}
RISK = {"Large Cap": "Medium risk. Big, stable companies.", "Flexi Cap": "Medium to high risk. Manager picks companies of any size.",
        "Mid Cap": "High risk. Medium-size companies, bigger ups and downs.", "Small Cap": "Very high risk. Small companies, big swings, needs 7+ years.",
        "Index (Nifty 50)": "Medium risk. Simply copies the Nifty 50, low cost.", "Tax Saver (ELSS)": "High risk. Locked for 3 years, saves tax under 80C (old regime).",
        "Balanced / Hybrid": "Lower risk than pure equity. Mix of shares and bonds.", "Debt / Liquid": "Low risk. Parks money, returns near FD rates."}

def nav_return(navs, days, cagr):
    # navs: list of (date, nav) newest first
    latest = navs[0]
    target = latest[0] - datetime.timedelta(days=days)
    for d, v in navs:
        if d <= target:
            r = latest[1] / v
            return (r ** (365.0 / days) - 1) * 100 if cagr else (r - 1) * 100
    return None

def load_funds():
    out = []
    for cat, codes in FUNDS.items():
        for c in codes:
            try:
                d = json.loads(get("https://api.mfapi.in/mf/%d" % c, 20))
                navs = [(datetime.datetime.strptime(x["date"], "%d-%m-%Y").date(), float(x["nav"])) for x in d["data"] if x.get("nav")]
                if len(navs) < 30: continue
                nm = re.sub(r" - Direct Plan.*$| - Direct.*$| Direct Plan.*$", "", d["meta"]["scheme_name"], flags=re.I).strip()
                out.append({"code": c, "name": nm, "house": d["meta"]["fund_house"], "category": cat, "nav": navs[0][1],
                            "nav_date": navs[0][0].isoformat(), "r1": nav_return(navs, 365, False),
                            "r3": nav_return(navs, 1095, True), "r5": nav_return(navs, 1825, True)})
            except Exception:
                pass
            time.sleep(0.1)
    return out

def screeners():
    s = [x for x in STATE["stocks"] if x.get("pct") is not None]
    def top(lst, k=6): return lst[:k]
    return {
      "near_high": top(sorted([x for x in s if x["pct"] > 0.3 and x["from_high"] is not None and x["from_high"] > -0.4], key=lambda x: -x["pct"])),
      "near_low": top(sorted([x for x in s if x["pct"] < -0.3 and x["from_low"] is not None and x["from_low"] < 0.4], key=lambda x: x["pct"])),
      "volume": top(sorted([x for x in s if x["vratio"] and x["vratio"] >= 1.3], key=lambda x: -x["vratio"])),
      "gap_up": top(sorted([x for x in s if x["gap"] is not None and x["gap"] >= 0.8], key=lambda x: -x["gap"])),
      "gap_down": top(sorted([x for x in s if x["gap"] is not None and x["gap"] <= -0.8], key=lambda x: x["gap"])),
      "volatile": top(sorted([x for x in s if x["range"]], key=lambda x: -x["range"])),
    }


def scanner():
    s = [x for x in STATE["stocks"] if x.get("pct") is not None and x.get("price")]
    def hit(x, reason, level=None):
        return {"symbol": x["symbol"], "name": x.get("name"), "price": x["price"], "pct": x["pct"], "reason": reason, "level": level,
                "vratio": x.get("vratio"), "time": x.get("time")}
    R = []
    def add(i, t, d, lst):
        R.append({"id": i, "title": t, "desc": d, "hits": lst[:8]})
    up = [hit(x, "Price %.2f is above the first 15-minute high %.2f" % (x["price"], x["orb_hi"]), x["orb_hi"]) for x in s
          if x.get("orb_hi") and x["bars"] >= 2 and x["price"] > x["orb_hi"]]
    dn = [hit(x, "Price %.2f is below the first 15-minute low %.2f" % (x["price"], x["orb_lo"]), x["orb_lo"]) for x in s
          if x.get("orb_lo") and x["bars"] >= 2 and x["price"] < x["orb_lo"]]
    up.sort(key=lambda h: -h["pct"]); dn.sort(key=lambda h: h["pct"])
    add("orb_up", "Opening range breakout (up)", "Price trades above the high of the first 15-minute candle (9:15-9:30).", up)
    add("orb_dn", "Opening range breakdown (down)", "Price trades below the low of the first 15-minute candle.", dn)
    g = sorted([x for x in s if x.get("gap") is not None and x["gap"] >= 1.0 and x["price"] >= x["open"]], key=lambda x: -x["gap"])
    add("gap_up", "Gap up holding", "Opened 1% or more above the previous close and still trades at or above the open.",
        [hit(x, "Gap %+.1f%% at open, now %+.1f%% on the day" % (x["gap"], x["pct"]), x["open"]) for x in g])
    g = sorted([x for x in s if x.get("gap") is not None and x["gap"] <= -1.0 and x["price"] <= x["open"]], key=lambda x: x["gap"])
    add("gap_dn", "Gap down holding", "Opened 1% or more below the previous close and still trades at or below the open.",
        [hit(x, "Gap %+.1f%% at open, now %+.1f%% on the day" % (x["gap"], x["pct"]), x["open"]) for x in g])
    v = sorted([x for x in s if x.get("vratio") and x["vratio"] >= 1.5], key=lambda x: -x["vratio"])
    add("vol", "Volume surge", "Volume so far is 1.5x or more the average of the previous sessions up to the same time.",
        [hit(x, "Volume is %.1fx the usual level for this time" % x["vratio"]) for x in v])
    w = sorted([x for x in s if x.get("vwap") and x["price"] > x["vwap"] and x["pct"] >= 0.5 and (x.get("vratio") or 0) >= 1.2], key=lambda x: -x["pct"])
    add("vwap_up", "Above VWAP with volume", "Price is above the day's VWAP (average price weighted by volume), up 0.5%+ and volume is 1.2x usual.",
        [hit(x, "Price %.2f vs VWAP %.2f" % (x["price"], x["vwap"]), x["vwap"]) for x in w])
    w = sorted([x for x in s if x.get("vwap") and x["price"] < x["vwap"] and x["pct"] <= -0.5 and (x.get("vratio") or 0) >= 1.2], key=lambda x: x["pct"])
    add("vwap_dn", "Below VWAP with volume", "Price is below the day's VWAP, down 0.5%+ and volume is 1.2x usual.",
        [hit(x, "Price %.2f vs VWAP %.2f" % (x["price"], x["vwap"]), x["vwap"]) for x in w])
    p = sorted([x for x in s if x.get("pdh") and x["price"] > x["pdh"]], key=lambda x: -x["pct"])
    add("pdh", "Above previous day high", "Price trades above yesterday's highest price.",
        [hit(x, "Price %.2f vs previous day high %.2f" % (x["price"], x["pdh"]), x["pdh"]) for x in p])
    p = sorted([x for x in s if x.get("pdl") and x["price"] < x["pdl"]], key=lambda x: x["pct"])
    add("pdl", "Below previous day low", "Price trades below yesterday's lowest price.",
        [hit(x, "Price %.2f vs previous day low %.2f" % (x["price"], x["pdl"]), x["pdl"]) for x in p])
    nh = sorted([x for x in s if x.get("from_high") is not None and x["from_high"] > -0.3 and x["pct"] > 0.5], key=lambda x: -x["pct"])
    add("near_hi", "Near day high", "Trading within 0.3% of today's high and up 0.5%+.",
        [hit(x, "%.2f%% below today's high %.2f" % (-x["from_high"], x["high"]), x["high"]) for x in nh])
    return {"rules": R, "asof": max([x.get("time") or 0 for x in s] or [0]), "fetched": STATE["stocks_t"], "day": (s[0].get("day") if s else None), "universe": len(s), "market_open": market_hours(),
            "delay_note": "Candles come from Yahoo Finance (15-minute bars). Typically 10-15 min delayed; refreshed every 5 min in market hours."}

REGION = {"US": "US", "UK": "Europe", "Germany": "Europe", "Japan": "Asia", "HK": "Asia", "China": "Asia"}
def scanner2(kind):
    items = [x for x in (STATE.get("cm") if kind == "commodities" else STATE.get("wi")) or [] if x.get("pct") is not None and x.get("price")]
    def hit(x, reason, level=None):
        return {"symbol": x["name"], "ticker": x["symbol"], "price": x["price"], "pct": x["pct"], "reason": reason, "level": level, "time": x.get("time")}
    R = []
    def add(i, t, d, lst): R.append({"id": i, "title": t, "desc": d, "hits": lst[:8]})
    def fm(v): return "%.2f" % v
    add("orb_up", "Session range breakout (up)", "Price trades above the high of the first 15-minute candle of the latest session.",
        sorted([hit(x, "Price %s is above the first 15-minute high %s" % (fm(x["price"]), fm(x["orb_hi"])), x["orb_hi"]) for x in items if x.get("orb_hi") and x["bars"] >= 2 and x["price"] > x["orb_hi"]], key=lambda h: -h["pct"]))
    add("orb_dn", "Session range breakdown (down)", "Price trades below the low of the first 15-minute candle of the latest session.",
        sorted([hit(x, "Price %s is below the first 15-minute low %s" % (fm(x["price"]), fm(x["orb_lo"])), x["orb_lo"]) for x in items if x.get("orb_lo") and x["bars"] >= 2 and x["price"] < x["orb_lo"]], key=lambda h: h["pct"]))
    add("gap_up", "Gap up holding", "Opened 0.7% or more above the previous close and still trades at or above the open.",
        sorted([hit(x, "Gap %+.1f%% at open, now %+.1f%%" % (x["gap"], x["pct"]), x["open"]) for x in items if x.get("gap") is not None and x["gap"] >= 0.7 and x["price"] >= x["open"]], key=lambda h: -h["pct"]))
    add("gap_dn", "Gap down holding", "Opened 0.7% or more below the previous close and still trades at or below the open.",
        sorted([hit(x, "Gap %+.1f%% at open, now %+.1f%%" % (x["gap"], x["pct"]), x["open"]) for x in items if x.get("gap") is not None and x["gap"] <= -0.7 and x["price"] <= x["open"]], key=lambda h: h["pct"]))
    add("pdh", "Above previous session high", "Price trades above the previous session's highest price.",
        sorted([hit(x, "Price %s vs previous high %s" % (fm(x["price"]), fm(x["pdh"])), x["pdh"]) for x in items if x.get("pdh") and x["price"] > x["pdh"]], key=lambda h: -h["pct"]))
    add("pdl", "Below previous session low", "Price trades below the previous session's lowest price.",
        sorted([hit(x, "Price %s vs previous low %s" % (fm(x["price"]), fm(x["pdl"])), x["pdl"]) for x in items if x.get("pdl") and x["price"] < x["pdl"]], key=lambda h: h["pct"]))
    add("big", "Strong move", "Moved 1.5% or more from the previous close.",
        sorted([hit(x, "%+.1f%% from the previous close" % x["pct"]) for x in items if abs(x["pct"]) >= 1.5], key=lambda h: -abs(h["pct"])))
    add("near_hi", "Near session high", "Within 0.3% of the session high and up 0.5% or more.",
        sorted([hit(x, "%.2f%% below the session high %s" % (-x["from_high"], fm(x["high"])), x["high"]) for x in items if x.get("from_high") is not None and x["from_high"] > -0.3 and x["pct"] >= 0.5], key=lambda h: -h["pct"]))
    add("near_lo", "Near session low", "Within 0.3% of the session low and down 0.5% or more.",
        sorted([hit(x, "%.2f%% above the session low %s" % (x["from_low"], fm(x["low"])), x["low"]) for x in items if x.get("from_low") is not None and x["from_low"] < 0.3 and x["pct"] <= -0.5], key=lambda h: h["pct"]))
    add("vol", "Volume surge", "Volume so far is 1.5x or more the previous sessions' average at the same time (where volume is reported).",
        sorted([hit(x, "Volume is %.1fx the usual level" % x["vratio"]) for x in items if x.get("vratio") and x["vratio"] >= 1.5], key=lambda h: -h["pct"]))
    summ = None
    if kind == "world":
        g = {}
        for x in items:
            for k, v in REGION.items():
                if "(%s)" % k in (x.get("name") or ""): g.setdefault(v, []).append(x["pct"])
        summ = [{"region": k, "avg": sum(v) / len(v), "n": len(v)} for k, v in g.items()]
    ts = [x.get("time") for x in items if x.get("time")]
    return {"rules": R, "summary": summ, "asof": max(ts) if ts else 0, "refreshed": STATE.get("wt"), "count": len(items),
            "delay_note": "Candles come from Yahoo Finance (15-minute bars), typically 10-15 min delayed and refreshed about every 10 min. " + ("Prices are USD futures (COMEX/NYMEX) and spot rates, not MCX rupee prices; MCX prices also depend on USD/INR and local duties." if kind == "commodities" else "Each market has its own trading hours, so closed markets show their last session.")}

def mscore(x):
    if x.get("pct") is None or x["pct"] <= 0: return None
    vr = min(x["vratio"] or 0, 4) / 4
    gap = min(max(x["gap"] or 0, 0), 3) / 3
    nh = 1 - min(max(-(x["from_high"] or 0), 0), 1.0)
    pc = min(x["pct"], 4) / 4
    return round(100 * (0.30 * vr + 0.20 * gap + 0.30 * nh + 0.20 * pc))

def reasons_for(x):
    r = []
    if x.get("vratio") and x["vratio"] >= 1.3: r.append("Volume is %.1fx its usual level for this time" % x["vratio"])
    if x.get("gap") and x["gap"] >= 0.8: r.append("Opened %.1f%% above the previous close" % x["gap"])
    if x.get("from_high") is not None and x["from_high"] > -0.4: r.append("Trading within 0.4% of today's high")
    if x.get("pct") and x["pct"] >= 1: r.append("Up %.1f%% on the day" % x["pct"])
    return r

def momentum():
    out = []
    for x in STATE["stocks"]:
        if x.get("pct") is None or x["pct"] <= 0: continue
        vr = min(x["vratio"] or 0, 4) / 4
        gap = min(max(x["gap"] or 0, 0), 3) / 3
        nh = 1 - min(max(-(x["from_high"] or 0), 0), 1.0) / 1.0
        pc = min(x["pct"], 4) / 4
        score = round(100 * (0.30 * vr + 0.20 * gap + 0.30 * nh + 0.20 * pc))
        y = dict(x); y["score"] = score
        y["reasons"] = []
        if x["vratio"] and x["vratio"] >= 1.3: y["reasons"].append("Volume is %.1fx its usual level for this time" % x["vratio"])
        if x["gap"] and x["gap"] >= 0.8: y["reasons"].append("Opened %.1f%% above yesterday's close" % x["gap"])
        if x["from_high"] is not None and x["from_high"] > -0.4: y["reasons"].append("Trading within 0.4% of today's high")
        if x["pct"] >= 1: y["reasons"].append("Up %.1f%% on the day" % x["pct"])
        out.append(y)
    out.sort(key=lambda z: -z["score"])
    return {"list": out[:15], "asof": STATE["stocks_t"], "day": (STATE["stocks"] or [{}])[0].get("day")}

def movers():
    s = [x for x in STATE["stocks"] if x.get("pct") is not None]
    srt = sorted(s, key=lambda x: x["pct"], reverse=True)
    adv = sum(1 for x in s if x["pct"] > 0)
    return {"gainers": srt[:8], "losers": srt[::-1][:8], "advances": adv, "declines": len(s) - adv, "count": len(s),
            "active": sorted(s, key=lambda x: (x["volume"] or 0) * (x["price"] or 0), reverse=True)[:8], "asof": STATE["stocks_t"],
            "day": s[0]["day"] if s else None}

def funds_view():
    f = STATE["funds"]
    cats = {}
    for x in f:
        cats.setdefault(x["category"], []).append(x)
    out = []
    for c, lst in cats.items():
        lst.sort(key=lambda x: -(x["r3"] if x["r3"] is not None else (x["r1"] or -999)))
        out.append({"category": c, "risk": RISK.get(c, ""), "funds": lst})
    return {"categories": out, "asof": STATE["funds_t"]}

def quote(sym):
    sym = sym.upper().strip()
    if not re.match(r"^[A-Z0-9&\-\.\^=]{1,20}$", sym):
        raise ValueError("bad symbol")
    return cached("q:" + sym, 30, lambda: chart(sym, "1d", "5m"))

def search(q):
    url = "https://query2.finance.yahoo.com/v1/finance/search?quotesCount=10&newsCount=0&q=" + urllib.parse.quote(q)
    d = json.loads(get(url))
    return [{"symbol": x["symbol"], "name": x.get("longname") or x.get("shortname"), "exch": x.get("exchDisp")}
            for x in d.get("quotes", []) if x.get("exchange") in ("NSI", "BSE") and x.get("quoteType") in ("EQUITY", "INDEX", "ETF")]

FEEDS = [("Economic Times", "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"),
         ("Economic Times", "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms"),
         ("Google News", "https://news.google.com/rss/search?q=nifty+OR+sensex+OR+%22stock+market%22+india+when:1d&hl=en-IN&gl=IN&ceid=IN:en")]

def news():
    def go():
        items = []
        for src, url in FEEDS:
            try:
                root = ET.fromstring(get(url))
            except Exception:
                continue
            for it in root.iter("item"):
                t = (it.findtext("title") or "").strip()
                link = (it.findtext("link") or "").strip()
                pub = it.findtext("pubDate")
                s = src
                if src == "Google News":
                    m = re.match(r"^(.*) - ([^-]+)$", t)
                    if m:
                        t, s = m.group(1), m.group(2)
                try:
                    ts = parsedate_to_datetime(pub).timestamp()
                except Exception:
                    ts = 0
                if t and link:
                    items.append({"title": t, "link": link, "source": s, "ts": ts})
        seen, out = set(), []
        for i in sorted(items, key=lambda x: -x["ts"]):
            k = i["title"].lower()[:60]
            if k not in seen:
                seen.add(k)
                out.append(i)
        return out[:40]
    return cached("news", 300, go)

HERE = os.path.dirname(os.path.abspath(__file__))


# ===================== v2 extensions =====================
import http.cookiejar
_cj = http.cookiejar.CookieJar()
_op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_cj))
_crumb = {"v": None, "t": 0}

def _ofetch(url, timeout=15):
    if time.time() < _block_until[0]:
        raise RateLimited()
    try:
        with _op.open(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code == 429:
            _block_until[0] = time.time() + 600
            raise RateLimited()
        raise

def ycrumb():
    if _crumb["v"] and time.time() - _crumb["t"] < 3000:
        return _crumb["v"]
    try: _ofetch("https://fc.yahoo.com")
    except RateLimited: raise
    except Exception: pass
    c = _ofetch("https://query2.finance.yahoo.com/v1/test/getcrumb").decode()
    if "<" in c or " " in c or len(c) > 30: raise RateLimited()
    _crumb["v"], _crumb["t"] = c, time.time()
    return c

def yjson(url):
    c = ycrumb()
    return json.loads(_ofetch(url + ("&" if "?" in url else "?") + "crumb=" + urllib.parse.quote(c)))

import http.cookiejar
_nj = {"op": None, "t": 0}
def nget(url, timeout=20):
    if not _nj["op"] or time.time() - _nj["t"] > 600:
        cj = http.cookiejar.CookieJar()
        op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
        h = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36", "Accept-Language": "en-US,en;q=0.9"}
        try: op.open(urllib.request.Request("https://www.nseindia.com/", headers=h), timeout=15).read()
        except Exception: pass
        _nj["op"], _nj["t"], _nj["h"] = op, time.time(), h
    h = dict(_nj["h"]); h["Referer"] = "https://www.nseindia.com/"; h["Accept"] = "application/json"
    return _nj["op"].open(urllib.request.Request(url, headers=h), timeout=timeout).read()

def nse(path, ttl=60):
    def go():
        return json.loads(get("https://www.nseindia.com/api/" + path, 20))
    return cached("nse:" + path, ttl, go)

def num(v):
    try: return float(str(v).replace(",", ""))
    except Exception: return None

def r_nse_indices(q):
    d = nse("allIndices", 60)
    keep = ["index", "last", "variation", "percentChange", "open", "high", "low", "previousClose", "yearHigh", "yearLow", "pe", "pb", "dy", "advances", "declines", "perChange30d", "perChange365d", "key"]
    return {"data": [{k: x.get(k) for k in keep} for x in d["data"]], "advances": d.get("advances"), "declines": d.get("declines"), "unchanged": d.get("unchanged"), "ts": d.get("timestamp")}

def r_movers(q):
    grp = q.get("g", ["NIFTY"])[0]
    if grp not in ("NIFTY", "BANKNIFTY", "NIFTYNEXT50", "FOSec", "allSec", "SecGtr20"): grp = "NIFTY"
    g = nse("live-analysis-variations?index=gainers", 60)
    l = nse("live-analysis-variations?index=loosers", 60)
    def row(x): return {"symbol": x["symbol"], "price": x["ltp"], "pct": x["perChange"], "change": x["net_price"], "volume": x["trade_quantity"], "value": x["turnover"], "open": x["open_price"], "high": x["high_price"], "low": x["low_price"], "prev": x["prev_price"]}
    return {"group": grp, "gainers": [row(x) for x in g[grp]["data"][:20]], "losers": [row(x) for x in l[grp]["data"][:20]], "ts": g[grp].get("timestamp")}

def r_52w(q):
    out = {}
    for k in ("high", "low"):
        d = nse("live-analysis-52week?index=" + k, 300)
        rows = d.get("dataLtpGreater20") or []
        out[k] = [{"symbol": x["symbol"], "name": x.get("comapnyName"), "ltp": x.get("ltp"), "level": x.get("new52WHL"), "prev": x.get("prev52WHL"), "prevDate": x.get("prevHLDate"), "pct": x.get("pChange") if x.get("pChange") is not None else None} for x in rows[:40]]
        out["ts"] = d.get("timestamp")
    return out

def r_active(q):
    by = q.get("by", ["value"])[0]
    if by not in ("value", "volume"): by = "value"
    d = nse("live-analysis-most-active-securities?index=" + by, 120)
    return {"data": [{"symbol": x["symbol"], "price": x.get("lastPrice"), "pct": x.get("pChange"), "volume": x.get("totalTradedVolume"), "value": x.get("totalTradedValue"), "yearHigh": x.get("yearHigh"), "yearLow": x.get("yearLow")} for x in d["data"][:25]]}

FIIHIST = {}
def r_fiidii(q):
    d = nse("fiidiiTradeReact", 600)
    for x in d:
        FIIHIST.setdefault(x["date"], {})[x["category"]] = {"buy": num(x["buyValue"]), "sell": num(x["sellValue"]), "net": num(x["netValue"])}
    return {"latest": d, "history": FIIHIST}

def r_ipo(q):
    cur = nse("ipo-current-issue", 900)
    up = nse("upcoming-issues", 900)
    return {"current": cur, "upcoming": up}

def r_results(q):
    d = nse("corporate-board-meetings?index=equities", 1800)
    rows = [x for x in d if "result" in (x.get("bm_purpose") or "").lower()]
    def key(x):
        try: return datetime.datetime.strptime(x["bm_date"], "%d-%b-%Y")
        except Exception: return datetime.datetime(2100, 1, 1)
    rows.sort(key=key)
    return {"data": [{"symbol": x["bm_symbol"], "date": x["bm_date"], "purpose": x.get("bm_purpose"), "desc": x.get("bm_desc"), "name": x.get("sm_name") or x.get("bm_symbol")} for x in rows[:120]]}

def r_events(q):
    ca = nse("corporates-corporateActions?index=equities", 1800)
    out = [{"symbol": x.get("symbol"), "name": x.get("comp"), "subject": x.get("subject"), "ex": x.get("exDate"), "record": x.get("recDate")} for x in ca[:150]]
    return {"actions": out}

def r_announce(q):
    d = nse("corporate-announcements?index=equities", 300)
    return {"data": [{"symbol": x.get("symbol"), "name": x.get("sm_name"), "desc": x.get("desc"), "text": (x.get("attchmntText") or "")[:240], "time": x.get("an_dt"), "link": x.get("attchmntFile")} for x in d[:40]]}

def r_deals(q):
    d = nse("block-deal", 600)
    return {"ts": d.get("timestamp"), "data": [{"symbol": x["symbol"], "price": x.get("lastPrice"), "qty": x.get("totalTradedVolume"), "value": x.get("totalTradedValue"), "session": x.get("session")} for x in d.get("data", [])[:30]]}

def r_fno(q):
    d = nse("live-analysis-oi-spurts-underlyings", 120)
    return {"oi": [{"symbol": x["symbol"], "oi": x["latestOI"], "prevOI": x["prevOI"], "chg": x["changeInOI"], "pct": (x["changeInOI"] / x["prevOI"] * 100) if x.get("prevOI") else None, "volume": x["volume"], "price": x["underlyingValue"]} for x in d["data"][:30]]}

def r_options(q):
    sym = q.get("symbol", ["NIFTY"])[0].upper()
    if sym not in ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"): sym = "NIFTY"
    info = nse("option-chain-contract-info?symbol=" + sym, 600)
    exps = info.get("expiryDates", [])
    exp = q.get("expiry", [exps[0] if exps else ""])[0]
    d = nse("option-chain-v3?type=Indices&symbol=%s&expiry=%s" % (sym, urllib.parse.quote(exp)), 90)
    rec = d.get("records", {})
    spot = rec.get("underlyingValue")
    rows = {}
    for x in rec.get("data", []):
        k = x.get("strikePrice")
        e = rows.setdefault(k, {"strike": k})
        for side in ("CE", "PE"):
            if x.get(side):
                o = x[side]; e[side] = {"oi": o.get("openInterest"), "chg": o.get("changeinOpenInterest"), "ltp": o.get("lastPrice"), "iv": o.get("impliedVolatility"), "vol": o.get("totalTradedVolume")}
    strikes = sorted(rows)
    ce = sum((rows[k].get("CE") or {}).get("oi") or 0 for k in strikes)
    pe = sum((rows[k].get("PE") or {}).get("oi") or 0 for k in strikes)
    def pain(k):
        t = 0
        for j in strikes:
            c = (rows[j].get("CE") or {}).get("oi") or 0
            p = (rows[j].get("PE") or {}).get("oi") or 0
            t += c * max(k - j, 0) + p * max(j - k, 0)
        return t
    mp = min(strikes, key=pain) if strikes else None
    atm = min(strikes, key=lambda k: abs(k - spot)) if strikes and spot else None
    i = strikes.index(atm) if atm in strikes else 0
    win = strikes[max(0, i - 10): i + 11]
    res = max(strikes, key=lambda k: (rows[k].get("CE") or {}).get("oi") or 0) if strikes else None
    sup = max(strikes, key=lambda k: (rows[k].get("PE") or {}).get("oi") or 0) if strikes else None
    return {"symbol": sym, "expiry": exp, "expiries": exps[:8], "spot": spot, "pcr": (pe / ce) if ce else None, "ceOI": ce, "peOI": pe, "maxPain": mp, "atm": atm, "resistance": res, "support": sup, "rows": [rows[k] for k in win], "ts": rec.get("timestamp")}

def r_chart(q):
    s = q.get("s", ["^NSEI"])[0]
    r = q.get("r", ["1d"])[0]
    cfg = {"1d": ("1d", "5m"), "5d": ("5d", "15m"), "1m": ("1mo", "1d"), "6m": ("6mo", "1d"), "1y": ("1y", "1d"), "5y": ("5y", "1wk"), "max": ("max", "1mo")}
    rng, iv = cfg.get(r, cfg["1d"])
    if not re.match(r"^[A-Za-z0-9&\-\.\^=]{1,20}$", s): raise ValueError
    if not s.startswith("^") and "." not in s and "=" not in s and "-" not in s: s += ".NS"
    def go():
        try:
            return _ychart(s, r, rng, iv)
        except Exception as ye:
            if s.startswith("^") or not s.endswith(".NS"): raise
            try: return nse_chart(s[:-3], r)
            except Exception as ne: raise RuntimeError("yahoo %r; nse %r" % (ye, ne))
    return cached("ch:%s:%s" % (s, r), 90 if r in ("1d", "5d") else 900, go)

def _ychart(s, r, rng, iv):
    def history(period, interval):
        raw = yget("https://query2.finance.yahoo.com/v8/finance/chart/%s?range=%s&interval=%s" % (urllib.parse.quote(s), period, interval))
        results = json.loads(raw).get("chart", {}).get("result") or []
        if not results: raise RuntimeError("Yahoo history unavailable")
        d = results[0]
        quotes = d.get("indicators", {}).get("quote") or [{}]
        q0 = quotes[0]; ts = d.get("timestamp") or []
        closes = q0.get("close") or []; volumes = q0.get("volume") or []
        pts = [[t, round(c, 2), (volumes[i] or 0) if i < len(volumes) else 0]
               for i, (t, c) in enumerate(zip(ts, closes)) if t is not None and c is not None]
        pts.sort(key=lambda p: p[0])
        if len(pts) < 2: raise RuntimeError("Yahoo returned no usable history")
        return d.get("meta") or {}, pts
    latest_session = False
    try:
        m, pts = history(rng, iv)
    except Exception:
        if r != "1d": raise
        # On weekends and exchange holidays Yahoo can return metadata only for 1d.
        # Use real observations from the most recent trading session, never made-up points.
        m, all_pts = history("5d", "15m")
        offset = m.get("gmtoffset", 0)
        session = lambda t: datetime.datetime.fromtimestamp(t + offset, datetime.timezone.utc).date()
        last_day = session(all_pts[-1][0])
        pts = [p for p in all_pts if session(p[0]) == last_day]
        earlier = [p for p in all_pts if session(p[0]) < last_day]
        m = dict(m)
        m["chartPreviousClose"] = earlier[-1][1] if earlier else m.get("previousClose")
        if len(pts) < 2: raise RuntimeError("Latest session has insufficient observations")
        latest_session = True
    return {"symbol": s, "range": r, "points": pts, "prev": m.get("chartPreviousClose"),
            "currency": m.get("currency"), "hi52": m.get("fiftyTwoWeekHigh"),
            "lo52": m.get("fiftyTwoWeekLow"), "source": "Yahoo Finance", "latestSession": latest_session}

def nse_chart(sym, r):
    import datetime
    if r == "1d":
        d = json.loads(get("https://www.nseindia.com/api/chart-databyindex?index=%sEQN" % urllib.parse.quote(sym), 20))
        pts = [[int(t / 1000) - 19800, p, 0] for t, p in d.get("grapthData", []) if p]
        if not pts: raise RuntimeError("nse empty 1d")
        return {"symbol": sym + ".NS", "range": r, "points": pts, "prev": d.get("closePrice") or None, "currency": "INR", "source": "NSE"}
    days = {"5d": 9, "1m": 31, "6m": 183, "1y": 366, "5y": 1826, "max": 1826}.get(r, 31)
    end = datetime.date.today(); out = []
    cur_end = end
    left = days
    while left > 0:
        span = min(left, 360); st = cur_end - datetime.timedelta(days=span)
        u = "https://www.nseindia.com/api/historical/cm/equity?symbol=%s&series=%%5B%%22EQ%%22%%5D&from=%s&to=%s" % (urllib.parse.quote(sym), st.strftime("%d-%m-%Y"), cur_end.strftime("%d-%m-%Y"))
        out = json.loads(get(u, 20)).get("data", []) + out
        cur_end = st - datetime.timedelta(days=1); left -= span + 1
    pts = []
    for x in out:
        try: t = int(datetime.datetime.strptime(x["CH_TIMESTAMP"], "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc).timestamp()) + 36000
        except Exception: continue
        if x.get("CH_CLOSING_PRICE"): pts.append([t, x["CH_CLOSING_PRICE"], x.get("CH_TOT_TRADED_QTY") or 0])
    pts.sort()
    if r == "5d": pts = pts[-5:]
    if not pts: raise RuntimeError("nse empty hist")
    return {"symbol": sym + ".NS", "range": r, "points": pts, "prev": None, "currency": "INR", "source": "NSE"}

def _raw(d, k):
    v = (d or {}).get(k)
    return v.get("raw") if isinstance(v, dict) else v

def ts_series(ysym, types):
    url = "https://query2.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/%s?type=%s&merge=false&period1=1400000000&period2=%d" % (ysym, ",".join(types), int(time.time()) + 86400)
    d = yjson(url)["timeseries"]["result"]
    out = {}
    for r in d:
        t = r["meta"]["type"][0]
        out[t] = [(x["asOfDate"], x["reportedValue"]["raw"], x.get("currencyCode") or x["reportedValue"].get("currencyCode")) for x in r.get(t, []) if x and x.get("reportedValue")]
    return out

def r_stock(q):
    sym = q.get("s", ["TCS"])[0].upper()
    if not re.match(r"^[A-Z0-9&\-\.]{1,20}$", sym): raise ValueError
    ysym = sym if "." in sym else sym + ".NS"
    def go():
        mods = "summaryDetail,defaultKeyStatistics,financialData,assetProfile,recommendationTrend,majorHoldersBreakdown,price"
        d = yjson("https://query2.finance.yahoo.com/v10/finance/quoteSummary/%s?modules=%s" % (ysym, mods))["quoteSummary"]["result"][0]
        sd, ks, fd, ap, pr = d.get("summaryDetail", {}), d.get("defaultKeyStatistics", {}), d.get("financialData", {}), d.get("assetProfile", {}), d.get("price", {})
        mh = d.get("majorHoldersBreakdown", {})
        rt = (d.get("recommendationTrend", {}).get("trend") or [{}])[0]
        out = {"symbol": sym, "ysym": ysym, "name": pr.get("longName") or pr.get("shortName"), "exchange": pr.get("exchangeName"), "currency": pr.get("currency"),
               "price": _raw(pr, "regularMarketPrice"), "change": _raw(pr, "regularMarketChange"), "pct": (_raw(pr, "regularMarketChangePercent") or 0) * 100,
               "open": _raw(sd, "open"), "prev": _raw(sd, "previousClose"), "dayHigh": _raw(sd, "dayHigh"), "dayLow": _raw(sd, "dayLow"),
               "hi52": _raw(sd, "fiftyTwoWeekHigh"), "lo52": _raw(sd, "fiftyTwoWeekLow"), "volume": _raw(sd, "volume"), "avgVolume": _raw(sd, "averageVolume"),
               "marketCap": _raw(sd, "marketCap"), "pe": _raw(sd, "trailingPE"), "fpe": _raw(sd, "forwardPE"), "pb": _raw(ks, "priceToBook"), "bookValue": _raw(ks, "bookValue"),
               "divYield": (_raw(sd, "dividendYield") or 0), "divRate": _raw(sd, "dividendRate"), "beta": _raw(sd, "beta"), "eps": _raw(ks, "trailingEps"),
               "roe": _raw(fd, "returnOnEquity"), "roa": _raw(fd, "returnOnAssets"), "de": _raw(fd, "debtToEquity"), "currentRatio": _raw(fd, "currentRatio"),
               "grossMargin": _raw(fd, "grossMargins"), "opMargin": _raw(fd, "operatingMargins"), "netMargin": _raw(fd, "profitMargins"),
               "revGrowth": _raw(fd, "revenueGrowth"), "earnGrowth": _raw(fd, "earningsGrowth"), "revenue": _raw(fd, "totalRevenue"), "ebitda": _raw(fd, "ebitda"),
               "debt": _raw(fd, "totalDebt"), "cash": _raw(fd, "totalCash"), "fcf": _raw(fd, "freeCashflow"), "ev": _raw(ks, "enterpriseValue"),
               "targetMean": _raw(fd, "targetMeanPrice"), "targetHigh": _raw(fd, "targetHighPrice"), "targetLow": _raw(fd, "targetLowPrice"), "reco": fd.get("recommendationKey"), "analysts": _raw(fd, "numberOfAnalystOpinions"),
               "insiders": _raw(mh, "insidersPercentHeld"), "institutions": _raw(mh, "institutionsPercentHeld"),
               "sector": ap.get("sector"), "industry": ap.get("industry"), "about": ap.get("longBusinessSummary"), "website": ap.get("website"), "employees": ap.get("fullTimeEmployees"), "city": ap.get("city"),
               "trend": {"strongBuy": rt.get("strongBuy"), "buy": rt.get("buy"), "hold": rt.get("hold"), "sell": rt.get("sell"), "strongSell": rt.get("strongSell")}}
        return out
    def go2():
        try: return go()
        except Exception as ye:
            try: return nse_stock(sym)
            except Exception as ne: raise RuntimeError("yahoo %r; nse %r" % (ye, ne))
    return cached("stock:" + ysym, 120, go2)

def nse_stock(sym):
    raw = get("https://query2.finance.yahoo.com/v8/finance/chart/%s.NS?range=1d&interval=5m" % urllib.parse.quote(sym), 15)
    m = json.loads(raw)["chart"]["result"][0]["meta"]
    px, pv = m.get("regularMarketPrice"), m.get("previousClose") or m.get("chartPreviousClose")
    if px is None: raise RuntimeError("no meta price")
    out = {"symbol": sym, "ysym": sym + ".NS", "name": m.get("longName") or m.get("shortName") or sym, "exchange": "NSE", "currency": "INR", "price": px,
           "change": round(px - pv, 2) if pv else None, "pct": round((px - pv) / pv * 100, 2) if pv else None, "prev": pv,
           "dayHigh": m.get("regularMarketDayHigh"), "dayLow": m.get("regularMarketDayLow"), "hi52": m.get("fiftyTwoWeekHigh"), "lo52": m.get("fiftyTwoWeekLow"),
           "volume": m.get("regularMarketVolume"), "partial": True, "source": "Yahoo summary"}
    try:
        h = get("https://www.google.com/finance/quote/%s:NSE" % urllib.parse.quote(sym), 15).decode("utf8", "replace")
        kv = dict(re.findall(r'class="SwQK7">([^<]*)</div><div class="dO6ijd">([^<]*)</div>', h))
        pe = num(kv.get("P/E ratio"))
        if pe: out["pe"] = pe
        o = num((kv.get("Open") or "").replace("\u20b9", "")); e = num((kv.get("EPS") or "").replace("\u20b9", ""))
        if o: out["open"] = o
        if e: out["eps"] = e
    except Exception: pass
    return out

def r_fin(q):
    sym = q.get("s", ["TCS"])[0].upper()
    if not re.match(r"^[A-Z0-9&\-\.]{1,20}$", sym): raise ValueError
    ysym = sym if "." in sym else sym + ".NS"
    def go():
        ann = ts_series(ysym, ["annualTotalRevenue", "annualNetIncome", "annualEBITDA", "annualBasicEPS", "annualTotalAssets", "annualTotalDebt", "annualStockholdersEquity", "annualOperatingCashFlow", "annualFreeCashFlow"])
        qtr = ts_series(ysym, ["quarterlyTotalRevenue", "quarterlyNetIncome", "quarterlyBasicEPS", "quarterlyEBITDA"])
        return {"annual": ann, "quarterly": qtr}
    return cached("fin:" + ysym, 3600, go)

def r_peers(q):
    sym = q.get("s", ["TCS"])[0].upper()
    ysym = sym if "." in sym else sym + ".NS"
    def go():
        d = json.loads(_ofetch("https://query2.finance.yahoo.com/v6/finance/recommendationsbysymbol/" + urllib.parse.quote(ysym)))
        syms = [x["symbol"] for x in d["finance"]["result"][0]["recommendedSymbols"] if x["symbol"].endswith((".NS", ".BO"))][:6]
        out = []
        for s in syms:
            try:
                c = chart(s); c["symbol"] = s.replace(".NS", ""); out.append(c)
            except Exception: pass
        return out
    return {"peers": cached("peers:" + ysym, 900, go)}

def r_stocknews(q):
    name = q.get("n", [""])[0][:60] or q.get("s", [""])[0]
    url = "https://news.google.com/rss/search?q=%s&hl=en-IN&gl=IN&ceid=IN:en" % urllib.parse.quote(name + " share stock when:30d")
    def go():
        root = ET.fromstring(get(url)); out = []
        for it in root.iter("item"):
            t = (it.findtext("title") or "").strip(); m = re.match(r"^(.*) - ([^-]+)$", t)
            try: ts = parsedate_to_datetime(it.findtext("pubDate")).timestamp()
            except Exception: ts = 0
            out.append({"title": m.group(1) if m else t, "source": m.group(2) if m else "News", "link": it.findtext("link"), "ts": ts})
        return out[:12]
    return {"data": cached("sn:" + name, 600, go)}

WSTOCKS = {"US": ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "JPM"], "UK": ["SHEL.L", "AZN.L", "HSBA.L", "ULVR.L", "BP.L", "GSK.L", "RIO.L", "BATS.L"],
           "Germany": ["SAP.DE", "SIE.DE", "ALV.DE", "DTE.DE", "AIR.DE", "MBG.DE", "BMW.DE", "BAS.DE"], "Japan": ["7203.T", "6758.T", "9984.T", "8306.T", "6861.T", "7974.T", "9983.T", "8035.T"],
           "Hong Kong": ["0700.HK", "9988.HK", "1299.HK", "0941.HK", "3690.HK", "1810.HK", "0005.HK", "0388.HK"], "China": ["600519.SS", "601318.SS", "600036.SS", "601398.SS", "000858.SZ", "300750.SZ", "002594.SZ", "601857.SS"]}
STATE["wstocks"] = {}; STATE["wt"] = 0; STATE["cm"] = []; STATE["cmt"] = 0

def refresh_world_picks():
    out = {}
    for mk, syms in WSTOCKS.items():
        res = fetch_many([(s, s) for s in syms], 3, intraday)
        out[mk] = res
    STATE["wstocks"] = out
    cm = fetch_many([(s, n) for s, n in COMMOD], 3, intraday)
    STATE["cm"] = cm
    STATE["wi"] = fetch_many(WORLD, 3, intraday)
    STATE["wt"] = time.time()

def scored(lst, k=8):
    out = []
    for x in lst:
        sc = mscore(x)
        y = dict(x); y["score"] = sc if sc is not None else 0; y["reasons"] = reasons_for(x); out.append(y)
    out.sort(key=lambda z: -z["score"])
    return out[:k]

def r_picks(q):
    res = {"India": scored(STATE["stocks"], 10)}
    for mk, lst in STATE["wstocks"].items():
        res[mk] = scored(lst, 6)
    com = []
    for x in STATE["cm"]:
        sc = mscore(x); y = dict(x)
        pct = x.get("pct") or 0
        y["score"] = sc if sc is not None else 0
        y["state"] = "Strong upward momentum" if y["score"] >= 55 else "Mild upward momentum" if y["score"] >= 30 else "Weak or sideways" if pct > -0.5 else "Downward pressure" if pct > -1.5 else "Strong downward pressure"
        y["reasons"] = reasons_for(x); com.append(y)
    return {"markets": res, "commodities": com, "asof": STATE["wt"]}

ROUTES = {"/api/nse/indices": r_nse_indices, "/api/movers2": r_movers, "/api/52w": r_52w, "/api/active": r_active, "/api/fiidii": r_fiidii, "/api/ipo": r_ipo,
          "/api/results": r_results, "/api/events": r_events, "/api/announce": r_announce, "/api/deals": r_deals, "/api/fno": r_fno, "/api/options": r_options,
          "/api/chart": r_chart, "/api/stock": r_stock, "/api/fin": r_fin, "/api/peers": r_peers, "/api/stocknews": r_stocknews, "/api/picks": r_picks}

def _world_loop():
    while True:
        try:
            if time.time() - STATE["wt"] > 900 and time.time() > _block_until[0]:
                refresh_world_picks()
        except Exception as e:
            STATE["err"] = repr(e)
        time.sleep(30)

ICON192 = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAMAAAADACAIAAADdvvtQAAAE4UlEQVR4nO3dS07cQBRG4UuUWbKHRAyDxBqYsxTWw1KY9xqQeoqyiCwgg5ZKlo2N7b8e91adbxShjttSHcrlB913P379MeCsb613ALERECQEBAkBQUJAkBAQJAQECQFBQkCQEBAkBAQJAUFCQJAQECQEBAkBQUJAkBAQJAQECQFBQkCQEBAkBAQJAUFCQJAQECQEBAkBQUJAkBAQJAQECQFBQkCQEBAkBAQJAUFCQJAQECQEBAkBQUJAkBAQJN9b7wAyuH97nf3k4/mlzlvf8V0ZoS3TmaqQEQFFtZ3OVNGMWAOFtL+eoy8+ioDiORFEuYYICBICCub0XFJoEiIgSAgoEnEWKTEJERAkBAQJAUFCQJAQUBhFLyifRkAxZKmnxE0xAgrA59xzQ0De5aqn0D15AoKEgFxzPv0YAXnmvx7jmWi33J52zRCQR3o91R6qJyB3TtdTLZop1kC+xKrHCMiVcPUYAXWgYT1GQH6cm37a1mME5ITnu13bCKi9iEufhIAaC12PEVBb0esxAmqog3qMgMJxVY8RUCtBT9qXCKiBuCftSwRUWx9Ln4SAquqsHiOgmvqrxwiomi7rMQJyznk9RkB1dHPSvkRAxfV00r5EQGX1uvRJCKig7usxAipnhHqMgAoZpB4joBLGqccIyI+I9RgBZdfxJZ9PEVBOfV/y+VQ/fxv/7+81/fvxeqn/Oz3U0ifp4QvnpulM1cxozHqsg0PYWj1m9v7wVOeYMmw9Fn0G2qgnebxerORQjVyPhZ6B/Hzt4wl91GNxA7p/e31/eNrzyvSyEg2NdtK+FDIg5Vv7MmbkbVZrIl5A+rBlGfjBlz5JsIBy/dK3+uq/zuqxWGdhy2Hbswy6nYWtOTGi1DMVZgYqtOA4ulnqmYkR0Nqwbc8ue16wsfGMeq3HQhzCvhzgtQPZnnqmvhxmTtqXvAe0f8ymGR1NJ9kYbA5en3IdUKsLLcshp541ftdADS/Tzd6aejY4Daj5mKUdaL4nznk8hGUZs7b3GQapxxzOQLl+4xsO4Tj1mLcZKPvxQpmHzp3WDVWPuQqo3Grj6JZPX1garR7zcwgrulY9NK4b99d2PoE0FBczULUzndMXtafW5qEBpx/zMAPVPE8uN8Zj1mPNA6p/lWXjPx59RlbfmQ60DKjVNbqP55eMQz5yPdYwoOZXeLNsZ/B6rFVAzevJsjXqsSYBOaknbTNtdufVwtPPinSpdkCu6lE2zvRzUzUgn/VM32LnM7LUk9QLyHM96Y0+nl82Gvr5+6Hm/oRQKSD/9Uzf8fF6ubWSpJ9Qz0yNWxmB6sFRxT+hbPYJLDwX0ZmCM9DGh/fwXEQ3Sq2Btj/6afuuE/UEUiSgPR8cttYQ9cTS/nGOKeoJJ39Ae6afm9kkRD0ReZmBqCcoFwFRT1ztA6Ke0BoHRD3R5Q9odhcpyyvhVvtDGEIrEtCeqYXppw+lZqDtPqinG8Uf55hdVySdzrj402bExSIaEgKChIAgISBICAgSAoKEgCAhIEgICBICgoSAICEgSAgIEgKChIAgISBICAgSAoKEgCAhIEgICBICgoSAICEgSAgIEgKChIAgISBICAgSAoKEgCAhIEgICBICgoSAICEgSAgIEgKChIAgISBICAgSAoKEgCAhIEgICBICgoSAICEgSAgIkv++46Dz9bQy7AAAAABJRU5ErkJggg==")
ICON512 = base64.b64decode("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")
MANIFEST = '{"name":"Nexora Markets","short_name":"Nexora","description":"Free market dashboard for India and the world","start_url":"/?src=pwa","scope":"/","display":"standalone","orientation":"portrait","background_color":"#0b1f2a","theme_color":"#0b1f2a","icons":[{"src":"/icon-192.png","sizes":"192x192","type":"image/png","purpose":"any maskable"},{"src":"/icon-512.png","sizes":"512x512","type":"image/png","purpose":"any maskable"}]}\n'
SW = "const C='nexora-v1';\nself.addEventListener('install',e=>{e.waitUntil(caches.open(C).then(c=>c.addAll(['/','/icon-192.png'])));self.skipWaiting()});\nself.addEventListener('activate',e=>{e.waitUntil(caches.keys().then(k=>Promise.all(k.filter(x=>x!==C).map(x=>caches.delete(x)))));self.clients.claim()});\nself.addEventListener('fetch',e=>{const u=new URL(e.request.url);if(e.request.method!=='GET'||u.origin!==location.origin)return;\nif(u.pathname.startsWith('/api/')){e.respondWith(fetch(e.request).then(r=>{const c=r.clone();caches.open(C).then(x=>x.put(e.request,c));return r}).catch(()=>caches.match(e.request)));return}\ne.respondWith(fetch(e.request).then(r=>{const c=r.clone();caches.open(C).then(x=>x.put(e.request,c));return r}).catch(()=>caches.match(e.request).then(m=>m||caches.match('/'))))});\n"
STATIC = {'/manifest.webmanifest': (MANIFEST.encode(), 'application/manifest+json'), '/sw.js': (SW.encode(), 'application/javascript'),
  '/icon-192.png': (ICON192, 'image/png'), '/icon-512.png': (ICON512, 'image/png'), '/apple-touch-icon.png': (ICON192, 'image/png')}

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def send(self, code, body, ctype="application/json", cache="no-store"):
        if isinstance(body, str): body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8" if ctype.startswith("text") or "json" in ctype else ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        if self.command != "HEAD": self.wfile.write(body)
    do_HEAD = lambda self: self.do_GET()
    def do_GET(self):
        u = urllib.parse.urlparse(self.path); p = u.path; q = urllib.parse.parse_qs(u.query)
        try:
            if p in ("/", "/index.html"):
                return self.send(200, open(os.path.join(HERE, "index.html"), "rb").read(), "text/html", "public, max-age=60")
            if p == "/healthz": return self.send(200, "ok", "text/plain")
            if p == "/robots.txt": return self.send(200, "User-agent: *\nAllow: /\nSitemap: https://nexora-markets-web.onrender.com/sitemap.xml\n", "text/plain")
            if p == "/api/indices":
                nw = time.time(); ist_m = ((datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)).hour * 60 + (datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)).minute)
                live = market_hours() and ist_m >= 570
                ind = []
                for x in STATE["idx"]:
                    x = dict(x); x["stale"] = bool(live and x.get("time") and nw - x["time"] > 1800); ind.append(x)
                stk = [{"name": {"RELIANCE": "Reliance", "TCS": "TCS", "INFY": "Infosys", "HDFCBANK": "HDFC Bank", "ICICIBANK": "ICICI Bank"}[s["symbol"]], "symbol": s["symbol"], "price": s.get("price"), "change": s.get("change"), "pct": s.get("pct"), "time": s.get("time"),
                        "stale": bool(live and s.get("time") and nw - s["time"] > 1800)} for s in STATE["stocks"] if s["symbol"] in ("RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK")]
                return self.send(200, json.dumps({"india": ind, "world": STATE["world"], "commod": STATE["commod"], "stocks": stk, "asof": STATE["idx_t"]}))
            if p == "/sitemap.xml":
                base = "https://nexora-markets-web.onrender.com"
                return self.send(200, '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + "".join("<url><loc>%s/%s</loc></url>\n" % (base, h) for h in ("", "#/markets", "#/screens", "#/world", "#/commodities", "#/funds", "#/news")) + "</urlset>\n", "application/xml", "public, max-age=3600")
            if p == "/api/screens": return self.send(200, json.dumps({"s": screeners(), "asof": STATE["stocks_t"], "day": (STATE["stocks"] or [{}])[0].get("day")}))
            if p == "/api/scanner": return self.send(200, json.dumps(scanner()))
            if p == "/api/scanner2": return self.send(200, json.dumps(scanner2(q.get("g", ["world"])[0])))
            if p == "/api/momentum": return self.send(200, json.dumps(momentum()))
            if p in STATIC:
                body, ct = STATIC[p]
                return self.send(200, body, ct, "public, max-age=3600" if p != "/sw.js" else "no-cache")
            if p == "/api/funds": return self.send(200, json.dumps(funds_view()))
            if p == "/api/movers": return self.send(200, json.dumps(movers()))
            if p == "/api/news": return self.send(200, json.dumps(news()))
            if p == "/api/search": return self.send(200, json.dumps(search(q.get("q", [""])[0])))
            if p == "/api/quote":
                syms = [s for s in q.get("s", [""])[0].split(",") if s][:25]
                with ThreadPoolExecutor(8) as ex:
                    r = list(ex.map(lambda s: (lambda x: x)(_try(quote, s)), syms))
                return self.send(200, json.dumps([x for x in r if x]))
            if p in ROUTES:
                return self.send(200, json.dumps(ROUTES[p](q)))
            if p == "/api/status":
                return self.send(200, json.dumps({"ok": True, "now": time.time(), "err": STATE.get("err"), "yblocked": time.time() < _block_until[0]}))
            return self.send(404, "not found", "text/plain")
        except Exception as e:
            return self.send(502, json.dumps({"error": "upstream unavailable", "why": repr(e)[:160]}))

def _try(f, *a):
    try: return f(*a)
    except Exception: return None

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    threading.Thread(target=refresher, daemon=True).start()
    threading.Thread(target=_world_loop, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", port), H).serve_forever()
