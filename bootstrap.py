# -*- coding: utf-8 -*-
"""一次性把整个库建起来。

第一次用、或者关注列表大改之后，跑这一个就够，不用记四步的顺序：

    python bootstrap.py

它按顺序做四件事，中间产物都在 data/ 下：
    follow.py     读全量关注画像         -> data/ups_raw.json
    enrich.py     补每个 UP 的最新分区    -> data/up_area.json
    rank.py       抓分区排行和综合热门    -> data/rank.json
    classify.py   按这些信号归类         -> data/cats.json

正常情况用不到手工执行：程序第一次连上账号、同步完动态流之后会自己跑一遍。
某一步失败不拦后面的——缺了分区数据也能出一版粗分类，后面重跑会更好。
"""
import os
import runpy
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))

STEPS = [
    ("follow.py", "读取关注列表"),
    ("enrich.py", "补每个 UP 的最新分区"),
    ("rank.py", "抓分区排行"),
    ("classify.py", "归类"),
]


def _say(msg):
    # pythonw 下 sys.stdout 是 None，直接 print 会抛异常
    try:
        print(msg, flush=True)
    except Exception:
        pass


def build(progress=None, steps=None):
    """跑完整条流水线。返回 [(脚本, 结果)]，不抛异常。"""
    os.makedirs(os.path.join(HERE, "data"), exist_ok=True)
    done = []
    for fn, label in (steps or STEPS):
        path = os.path.join(HERE, fn)
        if not os.path.exists(path):
            done.append((fn, "跳过：文件不存在"))
            continue
        if progress:
            try:
                progress(label)
            except Exception:
                pass
        _say(f"[bootstrap] {label}（{fn}）")
        t = time.time()
        try:
            runpy.run_path(path, run_name="__main__")
            done.append((fn, "ok"))
        except SystemExit as e:
            done.append((fn, "ok" if not e.code else f"退出码 {e.code}"))
        except Exception as e:
            done.append((fn, f"失败：{e}"))
        _say(f"[bootstrap]   用了 {time.time() - t:.0f}s")
    return done


if __name__ == "__main__":
    if not os.path.exists(os.path.join(HERE, "config.json")):
        _say("还没有 config.json，先在程序里连一次账号。")
        sys.exit(1)
    for fn, st in build():
        _say(f"  {fn:12s} {st}")
