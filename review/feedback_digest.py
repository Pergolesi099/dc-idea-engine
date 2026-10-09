#!/usr/bin/env python3
"""Turn Dean's queued desk feedback (written notes + chart mark-ups) into material Claude can digest.

Usage: python review/feedback_digest.py <raw_dir> <images_dir> <work_dir>
  raw_dir/feedback/*.json   ArtifactData list of collection `feedback` (saved with out_dir)
  images_dir                chart images by asset id (<chart_id>.webp|png, from Artifact read path=<chart_id>)
                            or by name (<SYM>_<side>.png, from the scan's charts/ folder)
Writes:
  work_dir/feedback/<id>.png   the chart with Dean's marks drawn on it (numbered as on the desk)
  work_dir/feedback.md         every queued item: scope, what he wrote, each mark's note, image path
Only items with status "queued" are included. Mark coordinates are fractions (0-1) of the image.
"""
import json
import pathlib
import sys

from PIL import Image, ImageDraw

raw, imgs, work = (pathlib.Path(a) for a in sys.argv[1:4])
out = work / "feedback"
out.mkdir(parents=True, exist_ok=True)
COL = (196, 30, 120)


def find_image(d):
    for name in filter(None, [d.get("chart_id"), f"{str(d.get('symbol', '')).replace('.', '-')}_{d.get('side')}"]):
        for ext in (".webp", ".png", ".jpg"):
            p = imgs / f"{name}{ext}"
            if p.exists():
                return p
    return None


def draw(img_path, marks, dest):
    im = Image.open(img_path).convert("RGB")
    W, H = im.size
    dr = ImageDraw.Draw(im)
    lw = max(3, W // 400)
    for i, m in enumerate(marks, 1):
        x1, y1 = m.get("x1", 0) * W, m.get("y1", 0) * H
        x2, y2 = m.get("x2", m.get("x1", 0)) * W, m.get("y2", m.get("y1", 0)) * H
        t = m.get("t")
        if t == "box":
            dr.rectangle([min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)], outline=COL, width=lw)
        elif t in ("line", "arrow"):
            dr.line([x1, y1, x2, y2], fill=COL, width=lw)
            if t == "arrow":
                dr.ellipse([x2 - 2 * lw, y2 - 2 * lw, x2 + 2 * lw, y2 + 2 * lw], fill=COL)
        r = 4 * lw
        dr.ellipse([x1 - r, y1 - r, x1 + r, y1 + r], fill=COL)
        dr.text((x1 - r / 2, y1 - r * 0.8), str(i), fill=(255, 255, 255))
    im.save(dest)


items = []
for f in sorted((raw / "feedback").glob("*.json")):
    d = json.loads(f.read_text())
    d = d.get("data", d)
    if d.get("status") != "queued":
        continue
    d["_id"] = f.stem
    items.append(d)
items.sort(key=lambda d: d.get("created_at") or "")

lines = [f"# Dean's queued feedback ({len(items)} items)", ""]
for d in items:
    scope = d.get("scope", "general")
    where = {"chart": f"{d.get('symbol')} {d.get('side')}", "pair": f"{(d.get('pair') or {}).get('long')} / {(d.get('pair') or {}).get('short')}"}.get(scope, "general")
    lines += [f"## {d['_id']} · {scope} · {where} · run {d.get('run_id') or '–'} · engine v{d.get('engine') or '?'} · {str(d.get('created_at', ''))[:16]}",
              "", f"> {d.get('text', '').strip()}", ""]
    marks = d.get("marks") or []
    for i, m in enumerate(marks, 1):
        lines.append(f"- mark {i} ({m.get('t')} at x={m.get('x1', 0):.2f}, y={m.get('y1', 0):.2f}): {m.get('txt') or '(no note)'}")
    if marks or scope == "chart":
        p = find_image(d)
        if p and marks:
            dest = out / f"{d['_id']}.png"
            draw(p, marks, dest)
            lines.append(f"- image: {dest}")
        elif p:
            lines.append(f"- image: {p}")
        else:
            lines.append("- image: not found (fetch the chart asset by chart_id first)")
    lines.append("")
(work / "feedback.md").write_text("\n".join(lines))
print(f"{len(items)} queued items -> {work / 'feedback.md'}")
