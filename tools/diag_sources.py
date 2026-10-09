"""Diagnostics for the fundamentals sources (run on GitHub Actions; prints what each source returns)."""
import json, sys, traceback
import requests
out = []
UAS = ["Sample Company Name AdminContact@example.com","dc-idea-engine research bot pergolesi099@users.noreply.github.com",
       "DC Idea Engine pergolesi099@users.noreply.github.com",
       "Pergolesi099 Research pergolesi099@users.noreply.github.com",
       "Mozilla/5.0 (compatible; dc-idea-engine; +https://github.com/Pergolesi099) pergolesi099@users.noreply.github.com"]
for ua in UAS:
    for url in ("https://www.sec.gov/files/company_tickers.json", "https://data.sec.gov/submissions/CIK0000018230.json"):
        try:
            r = requests.get(url, headers={"User-Agent": ua, "Accept-Encoding": "gzip, deflate"}, timeout=20)
            out.append(f"SEC {r.status_code} {url[-35:]} UA={ua[:40]} body={r.text[:120]!r}")
        except Exception as e:
            out.append(f"SEC err {e}")
for url in ("https://efts.sec.gov/LATEST/search-index?q=%22guidance%22&forms=8-K",
            "https://feeds.finance.yahoo.com/rss/2.0/headline?s=CAT&region=US&lang=en-US",
            "https://news.google.com/rss/search?q=Caterpillar+CAT+stock+when:21d&hl=en-US&gl=US&ceid=US:en",
            "https://query2.finance.yahoo.com/v1/finance/search?q=CAT&newsCount=8&quotesCount=0"):
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        out.append(f"NEWS {r.status_code} {url[:60]} len={len(r.text)} head={r.text[:400]!r}")
    except Exception as e:
        out.append(f"NEWS err {url[:50]} {e}")
open("diag.txt", "w").write("\n".join(out))
print("\n".join(out))
