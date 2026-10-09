"""Diagnostics for the fundamentals sources (run on GitHub Actions; prints what each source returns)."""
import json, sys, traceback, logging
sys.path.insert(0, "scanner")
logging.basicConfig(level=logging.DEBUG)
import yaml, yfinance as yf
import fundamentals as FU
cfg = yaml.safe_load(open("config.yaml"))["fundamentals"]
out = []
for s in ("AAPL", "CAT"):
    t = yf.Ticker(s)
    try:
        n = t.news
        out.append(f"news {s}: {len(n or [])} items; first keys {list((n or [{}])[0].keys())[:8]}")
        out.append(json.dumps((n or [None])[0], default=str)[:900])
    except Exception:
        out.append(f"news {s} error: " + traceback.format_exc()[-600:])
    try:
        out.append(f"fetch_yahoo {s}: " + json.dumps(FU.fetch_yahoo(s, s, True, cfg), default=str)[:900])
    except Exception:
        out.append("fetch_yahoo error: " + traceback.format_exc()[-600:])
sec = FU.Sec(cfg["sec_user_agent"])
try:
    sec.load_ciks(); out.append(f"ciks: {len(sec.ciks)}")
    for s in ("CAT", "AAPL", "ETN"):
        try:
            g = sec.guidance(s, cfg["guidance_days"])
            out.append(f"guidance {s}: " + json.dumps(g)[:1200])
        except Exception:
            out.append(f"guidance {s} error: " + traceback.format_exc()[-800:])
except Exception:
    out.append("sec error: " + traceback.format_exc()[-800:])
open("diag.txt", "w").write("\n".join(out))
print("\n".join(out))
