# -*- coding: utf-8 -*-
"""把整个程序打成一个能发给别人的 zip。

    python pack.py

产物在 dist/BiliPlayer-<日期>.zip，解压双击就能用（第一次会让你填 SESSDATA）。

故意不打包的东西：
- config.json  里面有 SESSDATA，等于你的登录凭据，绝对不能带出去
- data/        你自己的订阅库、播放记录、弹幕缓存
- window_profile/  浏览器的临时配置目录，几十兆没用
"""
import os
import time
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(ROOT, "dist")

# 整个目录跳过
SKIP_DIRS = {"data", "window_profile", "__pycache__", ".git", ".idea", "dist"}
# 单个文件跳过
SKIP_FILES = {"config.json", "pack.py", "BiliPlayer.lnk"}
SKIP_EXT = (".pyc", ".pyo", ".log", ".lnk")

# 一定要带上的（缺了就跑不起来），打包前自查
REQUIRED = [
    "server.py", "bili.py", "store.py", "launch.py", "classify.py",
    "bootstrap.py", "follow.py", "enrich.py", "rank.py", "probe.py",
    "static/index.html", "static/app.js", "static/style.css",
    "static/dash.all.min.js", "static/setup.html", "config.example.json",
    # 分发入口，少一个对方就不知道从哪儿点
    "启动BiliPlayer.bat", "BiliPlayer.command", "start.sh",
]


def collect():
    keep = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for fn in sorted(filenames):
            if fn in SKIP_FILES or fn.endswith(SKIP_EXT):
                continue
            full = os.path.join(dirpath, fn)
            keep.append((full, os.path.relpath(full, ROOT).replace("\\", "/")))
    return keep


def main():
    missing = [r for r in REQUIRED if not os.path.isfile(os.path.join(ROOT, r))]
    if missing:
        print("缺少必要文件，先补上再打包：")
        for m in missing:
            print("  -", m)
        return 1

    os.makedirs(DIST, exist_ok=True)
    out = os.path.join(DIST, "BiliPlayer-%s.zip" % time.strftime("%Y%m%d"))
    files = collect()
    total = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for full, rel in files:
            zi = zipfile.ZipInfo.from_file(full, "BiliPlayer/" + rel)
            # 在 Windows 上 chmod 是不生效的，权限位得自己写进 zip，
            # 否则 mac 那边解压出来 start.sh 没有执行权限，双击跑不动
            if rel.endswith((".sh", ".command")):
                zi.external_attr = 0o100755 << 16
            zi.compress_type = zipfile.ZIP_DEFLATED
            with open(full, "rb") as f:
                z.writestr(zi, f.read(), compresslevel=9)
            total += os.path.getsize(full)

    size = os.path.getsize(out)
    print("打包完成：%s" % out)
    print("  %d 个文件，原始 %.1f MB，压缩后 %.1f MB"
          % (len(files), total / 1048576, size / 1048576))
    print("  已排除 config.json（含登录凭据）、data/、window_profile/")
    print()
    print("对方拿到后：解压 → Windows 双击「启动BiliPlayer.bat」，")
    print("             macOS 双击「BiliPlayer.command」→ 在页面上粘贴自己的 SESSDATA")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
