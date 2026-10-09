"""Fundamentals, news and earnings-release guidance for the shortlisted candidates.

Per candidate (FY1 = current fiscal year not yet reported, FY2 = the one after, FY0 = last reported):
  mcap, pe1, pe2, eps0/eps1/eps2, epsg1/epsg2, revg1/revg2, peg1/peg2,
  epsrev3m  consensus FY1 EPS now vs ~90 days ago (Yahoo EPS trend; own snapshots as fallback)
  revrev3m  consensus FY1 sales now vs ~90 days ago (own weekly snapshots: fills in after ~13 weeks)
  epssurp / revsurp  last reported quarter's surprise when it was reported in the last ~3 months
                     (FMP when FMP_API_KEY is set: EPS and sales; else Yahoo: EPS only)
Per supersector: aggregate PE FY1 and EPS / sales growth over a reference basket (largest names by
market cap in the universe + the candidates), USD reporters only.
Context for the Claude review (not shown raw on the desk): recent Yahoo headlines and, from SEC EDGAR,
the guidance paragraphs of the latest earnings release (8-K item 2.02, exhibit 99).

Everything is best effort: a source failing leaves its fields empty, never stops the scan.
"""
from __future__ import annotations

import datetime as dt
import html
import logging
import os
import pathlib
import re
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import requests

log = logging.getLogger("fund")


def _f(v):
    try:
        v = float(v)
        return v if np.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def _growth(new, old):
    new, old = _f(new), _f(old)
    if new is None or old is None or old == 0:
        return None
    return (new - old) / abs(old)


# ------------------------------------------------------------------ Yahoo
def fetch_yahoo(sym: str, ysym: str, with_extras: bool, cfg: dict) -> dict:
    import yfinance as yf

    t = yf.Ticker(ysym)
    out: dict = {"symbol": sym}
    for attempt in range(2):
        try:
            info = t.get_info() or {}
            break
        except Exception as e:  # noqa: BLE001
            info = {}
            log.debug("info %s: %s", sym, e)
            time.sleep(2 * (attempt + 1))
    out["ccy"] = info.get("financialCurrency") or info.get("currency")
    out["fy_end"] = info.get("lastFiscalYearEnd")
    try:
        ee = t.earnings_estimate
        if ee is not None and "0y" in ee.index:
            out["eps0"] = _f(ee.loc["0y"].get("yearAgoEps"))
            out["eps1"] = _f(ee.loc["0y"].get("avg"))
            out["n_an"] = _f(ee.loc["0y"].get("numberOfAnalysts"))
            if "+1y" in ee.index:
                out["eps2"] = _f(ee.loc["+1y"].get("avg"))
    except Exception as e:  # noqa: BLE001
        log.debug("eps est %s: %s", sym, e)
    try:
        re_ = t.revenue_estimate
        if re_ is not None and "0y" in re_.index:
            r0 = re_.loc["0y"]
            out["rev1"] = _f(r0.get("avg"))
            out["rev0"] = _f(r0.get("yearAgoRevenue"))
            if out["rev0"] is None and _f(r0.get("growth")) is not None and out["rev1"]:
                out["rev0"] = out["rev1"] / (1 + _f(r0.get("growth")))
            if "+1y" in re_.index:
                out["rev2"] = _f(re_.loc["+1y"].get("avg"))
    except Exception as e:  # noqa: BLE001
        log.debug("rev est %s: %s", sym, e)
    if not with_extras:
        return out
    try:
        et = t.eps_trend
        if et is not None and "0y" in et.index:
            out["eps1_90d"] = _f(et.loc["0y"].get("90daysAgo"))
    except Exception as e:  # noqa: BLE001
        log.debug("eps trend %s: %s", sym, e)
    try:
        eh = t.earnings_history
        if eh is not None and len(eh):
            eh = eh.sort_index()
            last = eh.iloc[-1]
            qend = pd.Timestamp(eh.index[-1]).date()
            # quarter end within ~135 days ~ reported within the last 3 months
            if (dt.date.today() - qend).days <= 135:
                sp = _f(last.get("surprisePercent"))
                out["epssurp"] = None if sp is None else sp * 100 if abs(sp) < 5 else sp
                out["surp_q"] = qend.isoformat()
    except Exception as e:  # noqa: BLE001
        log.debug("eps hist %s: %s", sym, e)
    try:
        out["news"] = fetch_news(sym, ysym, cfg)
    except Exception as e:  # noqa: BLE001
        log.debug("news %s: %s", sym, e)
    return out


# ------------------------------------------------------------------ news headlines
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}


def fetch_news(sym: str, ysym: str, cfg: dict) -> list[dict]:
    """Recent headlines: Yahoo Finance search (ticker-tagged news), Yahoo RSS as a fallback."""
    cut = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=cfg["news_days"])
    items, seen = [], set()

    def add(ts, title, prov, summary=""):
        if not title or ts is None or ts < cut:
            return
        k = re.sub(r"\W+", "", title.lower())[:60]
        if k in seen:
            return
        seen.add(k)
        items.append({"d": ts.date().isoformat(), "t": title[:180], "s": (summary or "")[:280], "p": prov})
    try:
        r = requests.get("https://query2.finance.yahoo.com/v1/finance/search",
                         params={"q": ysym, "newsCount": 12, "quotesCount": 0}, headers=UA, timeout=15)
        for n in (r.json().get("news") or []) if r.ok else []:
            rel = n.get("relatedTickers") or []
            if rel and ysym not in rel and sym not in rel:
                continue
            add(pd.Timestamp(n["providerPublishTime"], unit="s", tz="UTC") if n.get("providerPublishTime") else None,
                n.get("title"), n.get("publisher"))
    except Exception as e:  # noqa: BLE001
        log.debug("yahoo search news %s: %s", sym, e)
    if len(items) < 3:
        try:
            import xml.etree.ElementTree as ET
            r = requests.get("https://feeds.finance.yahoo.com/rss/2.0/headline",
                             params={"s": ysym, "region": "US", "lang": "en-US"}, headers=UA, timeout=15)
            for it in ET.fromstring(r.content).iter("item") if r.ok else []:
                ts = pd.Timestamp(it.findtext("pubDate")) if it.findtext("pubDate") else None
                ts = ts.tz_convert("UTC") if ts is not None and ts.tzinfo else ts
                add(ts, it.findtext("title"), "Yahoo RSS", html.unescape(it.findtext("description") or ""))
        except Exception as e:  # noqa: BLE001
            log.debug("yahoo rss %s: %s", sym, e)
    items.sort(key=lambda x: x["d"], reverse=True)
    return items[:cfg["news_max"]]


# ------------------------------------------------------------------ FMP (optional, needs FMP_API_KEY)
def fetch_fmp_surprise(sym: str, key: str) -> dict:
    try:
        r = requests.get("https://financialmodelingprep.com/stable/earnings",
                         params={"symbol": sym, "limit": 6, "apikey": key}, timeout=20)
        rows = r.json() if r.ok else []
    except Exception as e:  # noqa: BLE001
        log.debug("fmp %s: %s", sym, e)
        return {}
    today = dt.date.today()
    for row in rows if isinstance(rows, list) else []:
        if row.get("epsActual") is None and row.get("revenueActual") is None:
            continue
        d = dt.date.fromisoformat(row["date"][:10])
        if (today - d).days > 95:
            return {}
        out = {"surp_date": d.isoformat()}
        g = _growth(row.get("epsActual"), row.get("epsEstimated"))
        out["epssurp"] = None if g is None else g * 100
        g = _growth(row.get("revenueActual"), row.get("revenueEstimated"))
        out["revsurp"] = None if g is None else g * 100
        return out
    return {}


# ------------------------------------------------------------------ SEC EDGAR: earnings-release guidance
GUIDE_RX = re.compile(
    r"\b(outlook|guidance|guide[sd]?|expects?|expected|anticipates?|forecasts?|reaffirm\w*|rais\w+|narrow\w*|"
    r"lower\w*|backlog|bookings|book-to-bill|order (?:book|intake|backlog)|orders|full[- ]year|fiscal (?:year )?20\d\d|"
    r"FY ?20?\d\d|EBITDA|operating margin|free cash flow|range of)\b", re.I)
NUM_RX = re.compile(r"(\$\s?\d|\d+(\.\d+)?\s?(%|percent|billion|million|bn|m\b))", re.I)


class Sec:
    def __init__(self, ua: str):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": ua, "Accept-Encoding": "gzip, deflate"})
        self.ciks: dict[str, int] = {}
        self.last = 0.0

    def get(self, url: str):
        wait = 0.12 - (time.time() - self.last)     # SEC fair-access: <= 10 requests/second
        if wait > 0:
            time.sleep(wait)
        self.last = time.time()
        r = self.s.get(url, timeout=25)
        r.raise_for_status()
        return r

    def load_ciks(self):
        j = self.get("https://www.sec.gov/files/company_tickers.json").json()
        self.ciks = {v["ticker"].upper(): int(v["cik_str"]) for v in j.values()}

    def guidance(self, sym: str, days: int) -> dict | None:
        cik = self.ciks.get(sym.upper()) or self.ciks.get(sym.upper().replace(".", "-"))
        if not cik:
            return None
        sub = self.get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json").json()
        rec = sub.get("filings", {}).get("recent", {})
        cut = dt.date.today() - dt.timedelta(days=days)
        for form, items, date, acc, doc in zip(rec.get("form", []), rec.get("items", []), rec.get("filingDate", []),
                                               rec.get("accessionNumber", []), rec.get("primaryDocument", [])):
            if dt.date.fromisoformat(date) < cut:
                break
            if form not in ("8-K", "6-K") or (form == "8-K" and "2.02" not in (items or "")):
                continue
            base = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}"
            idx = self.get(f"{base}/index.json").json()
            names = [it["name"] for it in idx.get("directory", {}).get("item", [])]
            ex = [n for n in names if re.search(r"ex[-_]?99|exhibit99|dex99", n, re.I) and n.lower().endswith((".htm", ".html", ".txt"))]
            if not ex and form == "6-K":
                ex = [doc] if doc else []
            if not ex:
                continue
            text = _html_text(self.get(f"{base}/{sorted(ex)[0]}").text)
            exc = extract_guidance(text)
            if exc:
                return {"date": date, "form": form, "url": f"{base}/{sorted(ex)[0]}", "excerpt": exc}
        return None


def _html_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h\d)>", "\n", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    raw = html.unescape(raw).replace("\xa0", " ")
    return "\n".join(re.sub(r"[ \t]+", " ", ln).strip() for ln in raw.splitlines() if ln.strip())


def extract_guidance(text: str, cap: int = 1800) -> str:
    """Forward-looking sentences with numbers (guidance, backlog, orders); skips the safe-harbor boilerplate."""
    text = re.split(r"(?i)\n\s*(forward[- ]looking statements|safe harbor|cautionary (note|statement))", text)[0]
    sents = re.split(r"(?<=[.;])\s+(?=[A-Z(•])|\n", text)
    keep, seen, n = [], set(), 0
    for s in sents:
        s = s.strip(" •-")
        if not (40 <= len(s) <= 450) or not GUIDE_RX.search(s) or not NUM_RX.search(s):
            continue
        if re.search(r"(?i)\b(compared (to|with) (the )?(prior|same)|for the (three|six|nine) months ended)\b", s) \
                and not re.search(r"(?i)outlook|guidance|expect|backlog|bookings|order", s):
            continue     # historical results, not guidance
        k = s[:80].lower()
        if k in seen:
            continue
        seen.add(k)
        keep.append(s)
        n += len(s)
        if n >= cap:
            break
    return " | ".join(keep)[:cap]


# ------------------------------------------------------------------ revisions from our own weekly snapshots
def snapshot_revisions(root: pathlib.Path, today: dt.date, cur: pd.DataFrame) -> pd.DataFrame:
    """For each symbol, FY1 EPS and sales now vs the snapshot closest to 91 days ago (80-105 days), same FY."""
    snaps = []
    for p in sorted((root / "history" / "fundamentals").glob("*.csv.gz")):
        d = dt.date.fromisoformat(p.name[:10])
        if 80 <= (today - d).days <= 105:
            snaps.append((abs((today - d).days - 91), p))
    if not snaps or cur.empty:
        return pd.DataFrame({"epsrev3m_snap": np.nan, "revrev3m": np.nan}, index=cur.index)
    old = pd.read_csv(sorted(snaps)[0][1]).drop_duplicates("symbol").set_index("symbol")
    j = cur.join(old[["fy_end", "eps1", "rev1"]], rsuffix="_old", how="left")
    fy = lambda x: pd.to_numeric(x, errors="coerce").round(0)   # epoch seconds; NaN-safe
    same = (fy(j.fy_end) == fy(j.fy_end_old)).to_numpy()
    out = pd.DataFrame(index=cur.index)
    out["epsrev3m_snap"] = np.where(same, (j.eps1 - j.eps1_old) / j.eps1_old.abs() * 100, np.nan)
    out["revrev3m"] = np.where(same, (j.rev1 / j.rev1_old - 1) * 100, np.nan)
    return out


# ------------------------------------------------------------------ sector aggregates
def sector_aggregates(df: pd.DataFrame) -> dict:
    """Aggregate (market-cap weighted) sector figures; USD reporters with estimates only."""
    out = {}
    d = df[(df.ccy.fillna("USD") == "USD") & df.price.gt(0) & df.mcap.gt(0)].copy()
    d["shares"] = d.mcap / d.price
    # one-offs (turnarounds, near-zero bases) swamp an aggregate: keep names with EPS growth in [-80%, +300%]
    for a, b in (("eps0", "eps1"), ("eps1", "eps2")):
        g = (d[b] - d[a]) / d[a].abs()
        bad = g.notna() & ((g < -0.8) | (g > 3.0))
        d.loc[bad, b if a == "eps0" else "eps2"] = np.nan
    for ss, g in d.groupby("supersector"):
        e0, e1, e2 = (g.shares * g.eps0), (g.shares * g.eps1), (g.shares * g.eps2)
        ok1 = g.eps1.notna()
        ok01 = g.eps0.notna() & g.eps1.notna()
        ok12 = g.eps1.notna() & g.eps2.notna()
        r01 = g.rev0.notna() & g.rev1.notna()
        r12 = g.rev1.notna() & g.rev2.notna()

        def ratio(a, b, m):
            sb = b[m].sum()
            return None if not m.any() or sb == 0 else float(a[m].sum() / sb)
        pe = ratio(g.mcap, e1, ok1 & (e1 > 0))
        g1 = ratio(e1, e0, ok01 & (e0 > 0))
        g2 = ratio(e2, e1, ok12 & (e1 > 0))
        rg1 = ratio(g.rev1, g.rev0, r01)
        rg2 = ratio(g.rev2, g.rev1, r12)
        out[ss] = {"pe1": None if pe is None else round(pe, 1),
                   "epsg1": None if g1 is None else round((g1 - 1) * 100, 1),
                   "epsg2": None if g2 is None else round((g2 - 1) * 100, 1),
                   "revg1": None if rg1 is None else round((rg1 - 1) * 100, 1),
                   "revg2": None if rg2 is None else round((rg2 - 1) * 100, 1),
                   "n": int(ok1.sum())}
    return out


# ------------------------------------------------------------------ main entry
def collect(cands: list[str], meta: pd.DataFrame, price: pd.Series, run_id: str, root: pathlib.Path,
            cfg: dict, ysym=lambda s: s, fetch=None, fmp=None, sec_fn=None) -> tuple[dict, dict, dict]:
    """Returns ({symbol: fund dict}, {supersector: aggregates}, {symbol: context dict})."""
    fc = cfg.get("fundamentals") or {}
    if not fc.get("enabled", True):
        return {}, {}, {}
    fetch = fetch or fetch_yahoo
    basket = (meta.assign(price=price.reindex(meta.index)).dropna(subset=["price"])
              .sort_values("mcap", ascending=False).groupby("supersector").head(fc.get("basket_per_sector", 20)))
    syms = list(dict.fromkeys(list(cands) + list(basket.index)))
    cset = set(cands)
    t0 = time.time()

    def one(s):
        try:
            return fetch(s, ysym(s), s in cset, fc)
        except Exception as e:  # noqa: BLE001
            log.debug("fund %s: %s", s, e)
            return {"symbol": s}
    with ThreadPoolExecutor(fc.get("workers", 6)) as ex:
        rows = list(ex.map(one, syms))
    df = pd.DataFrame(rows).set_index("symbol")
    for c in ("ccy", "fy_end", "eps0", "eps1", "eps2", "rev0", "rev1", "rev2", "eps1_90d", "epssurp", "surp_q", "n_an"):
        if c not in df:
            df[c] = np.nan
    df = df.join(meta[["supersector", "mcap"]], how="left")
    df["price"] = price.reindex(df.index)
    got = int(df.eps1.notna().sum())
    log.info("Fundamentals: estimates for %d/%d names (%d candidates) in %.0fs", got, len(df), len(cset), time.time() - t0)

    # snapshot for future revisions, then revisions vs ~3 months ago
    snap_dir = root / "history" / "fundamentals"
    snap_dir.mkdir(parents=True, exist_ok=True)
    today = dt.date.fromisoformat(run_id[:10])
    df[["fy_end", "eps1", "eps2", "rev1", "rev2"]].reset_index().to_csv(
        snap_dir / f"{run_id}.csv.gz", index=False, float_format="%.10g")
    rev = snapshot_revisions(root, today, df[["fy_end", "eps1", "rev1"]])

    sectors = sector_aggregates(df.reset_index())

    key = os.environ.get("FMP_API_KEY")
    fmp = fmp or (fetch_fmp_surprise if key else None)
    surprises = {}
    if fmp:
        with ThreadPoolExecutor(4) as ex:
            for s, r in zip(cands, ex.map(lambda s: fmp(s, key), cands)):
                surprises[s] = r

    context = {}
    if fc.get("guidance", True):
        try:
            ua = os.environ.get("SEC_USER_AGENT")       # "Name email": SEC requires a real contact
            if not sec_fn and not ua:
                raise RuntimeError("SEC_USER_AGENT secret not set; skipping earnings-release guidance")
            sec = sec_fn or Sec(ua)
            if hasattr(sec, "load_ciks"):
                sec.load_ciks()
            for s in cands:
                try:
                    g = sec.guidance(s, fc.get("guidance_days", 120))
                except Exception as e:  # noqa: BLE001
                    log.debug("sec %s: %s", s, e)
                    g = None
                if g:
                    context.setdefault(s, {})["guidance"] = g
            log.info("Guidance excerpts: %d/%d candidates", sum("guidance" in v for v in context.values()), len(cands))
        except Exception as e:  # noqa: BLE001
            log.warning("SEC guidance unavailable: %s", e)

    fund = {}
    for s in cands:
        if s not in df.index:
            continue
        r = df.loc[s]
        px, usd = _f(r.price), (r.ccy if isinstance(r.ccy, str) else "USD") == "USD"
        eps0, eps1, eps2 = _f(r.eps0), _f(r.eps1), _f(r.eps2)
        pe1 = px / eps1 if usd and px and eps1 and eps1 > 0 else None
        pe2 = px / eps2 if usd and px and eps2 and eps2 > 0 else None
        g1, g2 = _growth(eps1, eps0), _growth(eps2, eps1)
        rg1, rg2 = _growth(r.rev1, r.rev0), _growth(r.rev2, r.rev1)
        epsrev = _growth(eps1, r.eps1_90d)
        if epsrev is None and s in rev.index:
            epsrev = _f(rev.loc[s, "epsrev3m_snap"])
            epsrev = None if epsrev is None else epsrev / 100
        sp = surprises.get(s) or {}
        x = {
            "mcap": _f(r.mcap), "pe1": pe1, "pe2": pe2, "eps0": eps0, "eps1": eps1, "eps2": eps2,
            "epsg1": None if g1 is None else g1 * 100, "epsg2": None if g2 is None else g2 * 100,
            "revg1": None if rg1 is None else rg1 * 100, "revg2": None if rg2 is None else rg2 * 100,
            "peg1": pe1 / (g1 * 100) if pe1 and g1 and g1 > 0 else None,
            "peg2": pe2 / (g2 * 100) if pe2 and g2 and g2 > 0 else None,
            "epsrev3m": None if epsrev is None else epsrev * 100,
            "revrev3m": _f(rev.loc[s, "revrev3m"]) if s in rev.index and "revrev3m" in rev else None,
            "epssurp": sp.get("epssurp", _f(r.epssurp)), "revsurp": sp.get("revsurp"),
            "surp_date": sp.get("surp_date") or (r.surp_q if isinstance(r.surp_q, str) else None),
            "n_an": _f(r.n_an), "ccy": None if usd else r.ccy,
        }
        fund[s] = {k: (round(v, 2) if isinstance(v, float) else v) for k, v in x.items()}
        if isinstance(r.get("news"), list) and r.get("news"):
            context.setdefault(s, {})["news"] = r["news"]
    return fund, sectors, context
