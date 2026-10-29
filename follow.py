# -*- coding: utf-8 -*-
"""拉全量关注用户的画像（名字/签名/星标），存 data/ups_raw.json。

classify.py 靠这份数据分类。关注列表有变动（新关注、取关）时重跑一次：
    python follow.py && python classify.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from bili import Bili

CFG = json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))
OUT = os.path.join(HERE, "data", "ups_raw.json")


def main():
    b = Bili(CFG["sessdata"], interval=0.9)
    u = b.nav()
    print("账号:", u["uname"], "mid:", u["mid"])

    rows, pn = [], 1
    while True:
        d = b.get(f"https://api.bilibili.com/x/relation/followings?vmid={u['mid']}"
                  f"&pn={pn}&ps=50&order=desc", "https://space.bilibili.com/")
        if d.get("code") != 0:
            print("第", pn, "页失败", d.get("code"), d.get("message"))
            break
        batch = (d.get("data") or {}).get("list") or []
        rows.extend(batch)
        print(f"  第 {pn} 页 +{len(batch)} 累计 {len(rows)}")
        if len(batch) < 50 or pn > 40:
            break
        pn += 1

    out = [{
        "mid": r["mid"], "name": r.get("uname", ""), "sign": r.get("sign", ""),
        "face": r.get("face", ""), "mtime": r.get("mtime", 0),
        "tag": r.get("tag") or [], "special": r.get("special", 0),
        "vip": (r.get("vip") or {}).get("vipStatus", 0),
    } for r in rows]

    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("总计", len(out), "->", OUT)


if __name__ == "__main__":
    main()
