"""Hämtar senaste stängningskurser från Yahoo Finance och sparar dem i prices.json.

Körs av GitHub Actions varje vardagskväll. Filen innehåller bara kurser,
inga innehav eller belopp.
"""
import json
import math
from datetime import datetime, timezone

import yfinance as yf

# Yahoo-symbol -> namn som i dashboarden
SYMBOLS = {
    # USA (USD)
    "ALVO": "Alvotech",
    "ASTS": "AST SpaceMobile",
    "NVDA": "NVIDIA",
    "PSNY": "Polestar",
    "SPCX": "SpaceX",
    "TSLA": "Tesla",
    "AMD": "AMD",
    # Stockholm (SEK)
    "VOLV-A.ST": "Volvo A",
    "VOLV-B.ST": "Volvo B",
    "VOLCAR-B.ST": "Volvo Cars",
    # Köpenhamn (DKK)
    "NOVO-B.CO": "Novo Nordisk B",
    # Index (för uppskattning av Avanza Zero)
    "^OMX": "OMXS30",
    # Valutor
    "USDSEK=X": "USD/SEK",
    "DKKSEK=X": "DKK/SEK",
}

# Fonder: namn -> Avanzas fond-id (från adressen avanza.se/fonder/om-fonden.html/<id>/...)
FUNDS = {
    "Avanza Zero": 41567,
    "Spiltan Aktiefond Investmentbolag": 325406,
}
AVANZA_FUND_URL = "https://www.avanza.se/_api/fund-guide/guide/{}"

HISTORY_DAYS = 10  # antal handelsdagar som sparas per instrument

# Händelser hämtas bara för aktier (inte index eller valutor)
NO_EVENTS = {"^OMX", "USDSEK=X", "DKKSEK=X"}
EVENT_DAYS_AHEAD = 90   # kommande rapporter/utdelningar så här långt fram
NEWS_DAYS_BACK = 3      # nyheter så här många dagar bakåt
NEWS_PER_STOCK = 3


def num(x):
    """Avrunda till 4 decimaler, None om värdet saknas."""
    if x is None:
        return None
    x = float(x)
    if math.isnan(x):
        return None
    return round(x, 4)


def fetch(symbol):
    t = yf.Ticker(symbol)
    df = t.history(period="1mo", interval="1d", auto_adjust=False)
    df = df.dropna(subset=["Close"])
    if df.empty:
        raise ValueError("ingen kursdata")

    rows = [
        {"datum": idx.date().isoformat(), "stangning": num(r["Close"])}
        for idx, r in df.tail(HISTORY_DAYS).iterrows()
    ]
    last, prev = rows[-1], (rows[-2] if len(rows) > 1 else None)
    change = None
    if prev and prev["stangning"]:
        change = round((last["stangning"] / prev["stangning"] - 1) * 100, 2)

    currency = None
    try:
        currency = t.fast_info.get("currency")
    except Exception:
        pass

    return {
        "valuta": currency,
        "datum": last["datum"],          # handelsdag (börsens lokala datum)
        "stangning": last["stangning"],
        "foregaende_datum": prev["datum"] if prev else None,
        "foregaende_stangning": prev["stangning"] if prev else None,
        "forandring_pct": change,
        "historik": rows,
    }


def fetch_fund(fund_id):
    """Senaste fondkurs (NAV) med datum och dagsförändring från Avanza."""
    import requests

    r = requests.get(
        AVANZA_FUND_URL.format(fund_id),
        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
        timeout=30,
    )
    r.raise_for_status()
    g = r.json()
    if g.get("nav") is None or not g.get("navDate"):
        raise ValueError("ingen fondkurs")
    return {
        "datum": g["navDate"][:10],
        "nav": num(g["nav"]),
        "forandring_pct": None if g.get("developmentOneDay") is None
        else round(float(g["developmentOneDay"]), 2),
    }


def _as_date(v):
    """Gör om datum/tidsstämpel från yfinance till 'YYYY-MM-DD' (eller None)."""
    if v is None:
        return None
    try:
        if isinstance(v, (int, float)):
            return datetime.fromtimestamp(v, timezone.utc).date().isoformat()
        if hasattr(v, "date") and callable(v.date):
            return v.date().isoformat()
        if hasattr(v, "isoformat"):
            return v.isoformat()[:10]
        s = str(v)
        return s[:10] if len(s) >= 10 and s[4] == "-" else None
    except Exception:
        return None


def fetch_events(symbol, name, today):
    """Kommande rapport/utdelning och senaste nyheter för en aktie."""
    t = yf.Ticker(symbol)
    rows = []
    horizon = today.toordinal() + EVENT_DAYS_AHEAD

    def upcoming(d):
        if not d:
            return False
        try:
            o = datetime.fromisoformat(d).date().toordinal()
        except ValueError:
            return False
        return today.toordinal() <= o <= horizon

    cal = {}
    try:
        cal = t.calendar or {}
        if hasattr(cal, "to_dict"):  # äldre yfinance gav en tabell
            cal = {k: list(v.values()) for k, v in cal.to_dict().items()}
    except Exception:
        cal = {}

    earnings = cal.get("Earnings Date") or []
    if not isinstance(earnings, (list, tuple)):
        earnings = [earnings]
    dates = sorted({d for d in (_as_date(e) for e in earnings) if upcoming(d)})
    if dates:
        rubrik = "Rapport"
        if len(dates) > 1:
            rubrik += f" (väntas {dates[0]} – {dates[-1]})"
        rows.append({"instrument": name, "symbol": symbol, "typ": "Rapport",
                     "datum": dates[0], "rubrik": rubrik, "lank": None, "kalla": "Yahoo Finance"})

    exdiv = _as_date(cal.get("Ex-Dividend Date"))
    if upcoming(exdiv):
        rows.append({"instrument": name, "symbol": symbol, "typ": "Utdelning",
                     "datum": exdiv, "rubrik": "X-dag för utdelning (köp senast dagen innan)",
                     "lank": None, "kalla": "Yahoo Finance"})

    news = []
    try:
        news = t.news or []
    except Exception:
        news = []
    cutoff = today.toordinal() - NEWS_DAYS_BACK
    seen = set()
    for item in news:
        c = item.get("content", item) if isinstance(item, dict) else {}
        title = c.get("title")
        when = _as_date(c.get("pubDate") or c.get("displayTime") or c.get("providerPublishTime"))
        url = ((c.get("canonicalUrl") or {}).get("url") or (c.get("clickThroughUrl") or {}).get("url")
               or c.get("link"))
        src = (c.get("provider") or {}).get("displayName") or c.get("publisher")
        if not title or not when or title in seen:
            continue
        try:
            if datetime.fromisoformat(when).date().toordinal() < cutoff:
                continue
        except ValueError:
            continue
        if url and not str(url).startswith(("https://", "http://")):
            url = None
        seen.add(title)
        rows.append({"instrument": name, "symbol": symbol, "typ": "Nyhet", "datum": when,
                     "rubrik": title, "lank": url, "kalla": src})
        if len([r for r in rows if r["typ"] == "Nyhet"]) >= NEWS_PER_STOCK:
            break
    return rows


def load_previous():
    try:
        with open("prices.json", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def main():
    prev_file = load_previous()
    out = {
        "hamtad": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "kalla": "Yahoo Finance via yfinance; fonder från Avanza",
        "kurser": {},
        "fonder": {},
        "fel": {},
    }

    # Fonder: spara varje dag som hämtas så att historiken växer över tid
    for name, fund_id in FUNDS.items():
        old_hist = (prev_file.get("fonder", {}).get(name, {}) or {}).get("historik", [])
        try:
            q = fetch_fund(fund_id)
            hist = {h["datum"]: h for h in old_hist}
            hist[q["datum"]] = {"datum": q["datum"], "nav": q["nav"],
                                "forandring_pct": q["forandring_pct"]}
            rows = sorted(hist.values(), key=lambda h: h["datum"])[-HISTORY_DAYS * 2:]
            out["fonder"][name] = {**q, "avanza_id": fund_id, "historik": rows}
            print(f"OK   {name[:12]:12} {q['datum']} {q['nav']} ({q['forandring_pct']} %)")
        except Exception as e:
            if old_hist:  # behåll tidigare historik om hämtningen misslyckas
                out["fonder"][name] = {**old_hist[-1], "avanza_id": fund_id,
                                       "historik": old_hist, "inaktuell": True}
            out["fel"][name] = str(e)
            print(f"FEL  {name[:12]:12} {e}")
    for symbol, name in SYMBOLS.items():
        try:
            q = fetch(symbol)
            q["namn"] = name
            out["kurser"][symbol] = q
            print(f"OK   {symbol:12} {q['datum']} {q['stangning']} ({q['forandring_pct']} %)")
        except Exception as e:  # ett fel stoppar inte de andra
            out["fel"][symbol] = str(e)
            print(f"FEL  {symbol:12} {e}")

    # Händelser: kommande rapporter/utdelningar och senaste nyheter per aktie
    today = datetime.now(timezone.utc).date()
    events = []
    for symbol, name in SYMBOLS.items():
        if symbol in NO_EVENTS:
            continue
        try:
            events.extend(fetch_events(symbol, name, today))
        except Exception as e:
            out["fel"]["händelser " + symbol] = str(e)
            print(f"FEL  händelser {symbol}: {e}")
    events.sort(key=lambda r: (r["typ"] == "Nyhet", r["datum"]))
    out["handelser"] = events
    print(f"Händelser: {len(events)} st")

    with open("prices.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    if not out["kurser"]:
        raise SystemExit("Inga kurser kunde hämtas")


if __name__ == "__main__":
    main()
