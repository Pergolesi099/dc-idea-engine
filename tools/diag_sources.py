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
open("diag.txt", "w").write("\n".join(out))
print("\n".join(out))
