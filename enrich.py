# -*- coding: utf-8 -*-
"""给每个有投稿的 UP 补一个"真实分区名"。

动态流里那个 desc 字段其实是视频简介，不是分区，分类时不能用。
真正的分区（tname，如"人文历史""科学科普""财经商业"）只有视频详情接口
x/web-interface/view 会给。它是全库最可靠的分类信号，一个 UP 取最近一条就够。
"""
import json
import os
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from bili import Bili

CFG = json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))
DB = os.path.join(HERE, "data", "biliplayer.db")
OUT = os.path.join(HERE, "data", "up_area.json")


def main():
    conn = sqlite3.connect(DB)
    rows = conn.execute(
        "SELECT mid, bvid FROM videos WHERE bvid IN "
        "(SELECT bvid FROM videos v2 WHERE v2.mid=videos.mid ORDER BY pub_ts DESC LIMIT 1) "
        "GROUP BY mid").fetchall()
    # 上面那条太绕，直接用窗口函数思路：每个 mid 取 pub_ts 最大的 bvid
    rows = conn.execute("""
        SELECT v.mid, v.bvid FROM videos v
        JOIN (SELECT mid, MAX(pub_ts) mx FROM videos GROUP BY mid) t
          ON t.mid = v.mid AND t.mx = v.pub_ts
        GROUP BY v.mid
    """).fetchall()
    conn.close()
    print(f"需要补分区的 UP：{len(rows)} 个")

    old = {}
    if os.path.exists(OUT):
        old = json.load(open(OUT, encoding="utf-8"))

    b = Bili(CFG["sessdata"], interval=0.85)
    out = dict(old)
    done = 0
    for i, (mid, bvid) in enumerate(rows):
        if str(mid) in out:
            continue
        try:
            d = b.get(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}")
            data = d.get("data") or {}
            out[str(mid)] = {
                # 必须用旧的 tid：新版 tname/tname_v2 都是空串，只剩 tid 可用，
                # 而排行榜里的 tid->tname 对照表用的正是这套旧编号（tid_v2 是另一套，对不上）
                "tname": data.get("tname_v2") or data.get("tname", ""),
                "tid": data.get("tid", 0),
                "bvid": bvid}
            done += 1
        except Exception as e:
            out[str(mid)] = {"tname": "", "tid": 0, "bvid": bvid, "err": str(e)[:60]}
        if (i + 1) % 20 == 0:
            print(f"  {i + 1}/{len(rows)} 已补 {done}")
            json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    from collections import Counter
    c = Counter(v.get("tname", "") for v in out.values())
    print("分区分布：", dict(c.most_common(25)))
    print("已写", OUT)


if __name__ == "__main__":
    main()
