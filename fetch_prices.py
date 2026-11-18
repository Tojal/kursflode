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
    "VOLCAR-B.ST": "Volvo Car B",
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

    with open("prices.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    if not out["kurser"]:
        raise SystemExit("Inga kurser kunde hämtas")


if __name__ == "__main__":
    main()
