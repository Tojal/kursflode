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


def main():
    out = {
        "hamtad": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "kalla": "Yahoo Finance via yfinance",
        "kurser": {},
        "fel": {},
    }
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
