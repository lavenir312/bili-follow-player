# -*- coding: utf-8 -*-
"""抓各分区排行榜。

用途有两个：
1. 建 tid -> 分区名 对照表。视频详情接口的 tname 被 B 站改成了空串，只剩 tid 可用，
   而排行榜里 tid/tname 是一一对应的，反查即可。
2. 给每个分类准备"延伸推荐"的候选池：这些是你没关注的人在发的热门。

结果存 data/rank.json，缓存 6 小时。
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from bili import Bili

OUT = os.path.join(HERE, "data", "rank.json")

# 一级分区 rid；0 是全站
RIDS = [0, 1, 3, 4, 5, 11, 13, 23, 36, 119, 129, 138, 155, 160, 167,
        181, 188, 202, 211, 217, 223, 234, 249, 251]


def fetch(b, force=False, max_age=6 * 3600):
    if not force and os.path.exists(OUT):
        try:
            old = json.load(open(OUT, encoding="utf-8"))
            if time.time() - old.get("ts", 0) < max_age:
                return old
        except Exception:
            pass

    items, tidmap = [], {}

    def add(lst, src):
        for it in lst:
            tid = it.get("tid")
            tn = it.get("tname") or ""
            if tid and tn:
                tidmap[str(tid)] = tn
            owner = it.get("owner") or {}
            items.append({
                "bvid": it.get("bvid"),
                "title": it.get("title", ""),
                "cover": it.get("pic", ""),
                "duration": it.get("duration"),
                "tid": tid,
                "tname": tn,
                "mid": owner.get("mid"),
                "name": owner.get("name", ""),
                "face": owner.get("face", ""),
                "play": (it.get("stat") or {}).get("view", 0),
                "pubdate": it.get("pubdate", 0),
                "src": src,
            })

    for rid in RIDS:
        try:
            d = b.get(f"https://api.bilibili.com/x/web-interface/ranking/v2?rid={rid}&type=all",
                      "https://www.bilibili.com/v/popular/rank/all")
            add((d.get("data") or {}).get("list") or [], f"rank{rid}")
        except Exception:
            continue

    # 综合热门能翻页，比排行榜量大得多，推荐池主要靠它
    for pn in range(1, 9):
        try:
            d = b.get(f"https://api.bilibili.com/x/web-interface/popular?ps=50&pn={pn}",
                      "https://www.bilibili.com/v/popular/all")
            lst = (d.get("data") or {}).get("list") or []
        except Exception:
            break
        if not lst:
            break
        add(lst, f"pop{pn}")
        if (d.get("data") or {}).get("no_more"):
            break

    # 去重（同一条可能既上排行又上热门）
    seen, uniq = set(), []
    for it in items:
        if not it["bvid"] or it["bvid"] in seen:
            continue
        seen.add(it["bvid"])
        uniq.append(it)

    data = {"ts": int(time.time()), "tidmap": tidmap, "items": uniq}
    json.dump(data, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"抓 {len(uniq)} 条，分区名 {len(tidmap)} 个")
    return data


def main():
    CFG = json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))
    b = Bili(CFG["sessdata"], interval=0.8)
    d = fetch(b, force=True)
    from collections import Counter
    c = Counter(it["tname"] for it in d["items"])
    print("分区覆盖：", dict(c.most_common(40)))
    print("tid 对照：", sorted(d["tidmap"].items(), key=lambda kv: int(kv[0]))[:40])


if __name__ == "__main__":
    main()
