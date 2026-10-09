"""Diagnostics for the fundamentals sources (run on GitHub Actions; prints what each source returns)."""
import json, sys, traceback
sys.path.insert(0, "scanner")
import yaml
import fundamentals as FU
cfg = yaml.safe_load(open("config.yaml"))["fundamentals"]
out = []
for s in ("CAT", "AAPL", "IDT", "BRK.B"):
    try:
        n = FU.fetch_news(s, s.replace(".", "-"), cfg)
        out.append(f"news {s}: {len(n)} " + json.dumps(n[:3])[:500])
    except Exception:
        out.append(f"news {s} error " + traceback.format_exc()[-400:])
sec = FU.Sec("Sample Company Name AdminContact@example.com")   # one-off parser test only
try:
    sec.load_ciks()
    for s in ("CAT", "ETN", "AAPL", "IDT"):
        try:
            g = sec.guidance(s, 120)
            out.append(f"guidance {s}: " + json.dumps(g)[:1400])
        except Exception:
            out.append(f"guidance {s} error " + traceback.format_exc()[-500:])
except Exception:
    out.append("sec error " + traceback.format_exc()[-500:])
open("diag.txt", "w").write("\n".join(out))
print("\n".join(out))
