# -*- coding: utf-8 -*-
"""给「什么信号都没有」的 UP 补一页投稿。

分类脚本有四路信号：投稿分区 / 名字自述 / 标题关键词 / 签名。四条全空的人
会被判成兜底类。这类人通常是几个月没投稿的老关注，enrich.py 抓不到分区
（它只看最近一条），签名又是空的或只有商务联系方式。

但他们的空间动态还在，翻一页就能拿到最近 15 条投稿的标题——这既是分类依据，
本身也是该进库的视频。补完之后重跑 classify.py，就不必靠猜了。

用法：
    python probe.py            # 只处理没视频的
    python probe.py --all      # 没视频的 + 只有 1-2 条的
"""
import json
import os
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bili as bili_mod
import store as store_mod


def main():
    cfg = json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))
    db = store_mod.Store(os.path.join(HERE, "data", "biliplayer.db"))
    b = bili_mod.Bili(cfg["sessdata"], interval=CFG_GAP)

    all_mode = "--all" in sys.argv
    # empty_ups 自带「没翻到底」的过滤，翻到底确认没投稿的不会再排队
    rows = db.empty_ups(999 if all_mode else 200)
    todo = [(r["mid"], r["name"], 0) for r in rows]

    print(f"待补 {len(todo)} 人" + ("（含视频很少的）" if all_mode else "（只看一条视频都没有的）"))
    ok = fail = added = 0
    for i, (mid, name, n) in enumerate(todo, 1):
        try:
            c = db.dyn_cursor(mid)
            r = b.space_dynamics(mid, c.get("offset") or "")
            got = 0
            for it in r["items"]:
                if db.save_video({
                    "bvid": it["bvid"], "mid": mid, "title": it["title"],
                    "cover": it["cover"], "pub_ts": it["pub_ts"],
                    "duration": it["duration"], "play": it["play"],
                    "name": name,
                }):
                    got += 1
            db.set_dyn_cursor(mid, r["offset"], 0 if r["has_more"] else 1)
            added += got
            ok += 1
            flag = "" if got else "（这个号真没投稿）"
            print(f"  {i:>3}/{len(todo)} {name[:16]:<18} +{got} 条 {flag}")
        except Exception as e:
            fail += 1
            print(f"  {i:>3}/{len(todo)} {name[:16]:<18} 跳过：{type(e).__name__} {str(e)[:50]}")
    print(f"\n完成 {ok} 人，失败 {fail} 人，新入库 {added} 条")


CFG_GAP = 1.2

if __name__ == "__main__":
    main()
