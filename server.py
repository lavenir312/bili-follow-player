# -*- coding: utf-8 -*-
"""BiliPlayer 本地服务：订阅库 + 视频中转 + MPD 生成

设计要点：
- 只有本机可访问（绑定 127.0.0.1），不做任何鉴权
- 视频 CDN 必须带 Referer: https://www.bilibili.com/，否则 403，所以所有流都走 /stream 中转
- dash.js 要 MPD，B 站只给裸地址，所以现场按 SegmentBase 拼一份
"""
import base64
import html as htmlmod
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bili import Bili, BiliError, UA          # noqa: E402
from store import Store                        # noqa: E402
import rank as rankmod                         # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(ROOT, "static")
DATA = os.path.join(ROOT, "data")

# 分类：id, 名字, 一句话说明（前端侧栏和空态用）
CATS = [
    ("news",    "时政·社会", "新闻机构、时事、社会议题"),
    ("biz",     "经济·商业", "财经、投资、公司、职场"),
    ("history", "历史",      "中国史、世界史、军事史"),
    ("ideas",   "思想·人文", "哲学、社科、心理、访谈"),
    ("sci",     "科学·技术", "数理化生医、工程、数码、AI"),
    ("books",   "读书·文学", "书评、有声书、文学、诗词"),
    ("film",    "影像·音乐", "影视解说、纪录片、音乐、动画"),
    ("game",    "游戏",      "单机、策略、手游、桌游"),
    ("study",   "学习·课程", "考试、语言、教程、公开课"),
    ("life",    "生活",      "做饭、手作、摄影、穿搭、出门、日常"),
    ("friend",  "同学·熟人", "原来「学校」分组里的账号"),
    ("unknown", "待定",      "既没投稿也没签名，看不出是什么"),
]
CAT_NAME = {c[0]: c[1] for c in CATS}

# 二级分类：只给内容跨度大的「生活」拆了。侧栏点开大类才出现。
# 顺序就是侧栏显示顺序，也是判据优先级（越靠前越专指）。
SUBCATS = {
    "life": [
        ("food",   "吃·厨房",   "做饭、菜谱、探店、美食节目"),
        ("craft",  "手作·维修", "手工、DIY、修复、积木、居家妙招"),
        ("visual", "摄影·绘画", "拍照、风光、绘画、地图与地理影像"),
        ("style",  "穿戴·身型", "穿搭、美妆、健身"),
        ("travel", "出门·见闻", "旅行、旅居、异国观察"),
        ("daily",  "日常·闲聊", "vlog、生活记录、吐槽"),
    ],
    "film": [
        ("jie",   "影视·解说", "电影剧集的解说、深度解析、影评盘点"),
        ("ani",   "动画·番剧", "新番点评、漫画解说、动画杂谈、音声与虚拟主播"),
        ("music", "音乐·演奏", "演奏、翻唱、乐评、音乐史"),
        ("zip",   "译制·搬运", "字幕组、自译、中配、纪录片搬运"),
        ("meme",  "鬼畜·整活", "鬼畜、搞笑剪辑、颜值合集"),
    ],
}

# 看得出大类、看不出细分的人落在哪一支（和 classify.py 的 FALLBACK_SUB 保持一致）
SUB_FALLBACK = {"life": "daily", "film": "jie"}

# 每类的推荐来源：分区名正则。池子来自 rank.py 抓的各分区排行，
# 排掉的：你已经关注的、已经在库里的、同一个 UP 只留一条
REC_SRC = {
    "":        ["."],                  # 全部关注：推全站，只排掉已关注的人
    "news":    [r"^社会$|^热点$|^资讯$"],  # 精确匹配，否则"预告·资讯"（影视区）会漏进来
    "biz":     [r"财经商业"],
    "history": [r"人文历史"],
    "ideas":   [r"社科|法律|心理"],
    "sci":     [r"科学科普|数码|计算机技术|软件应用|科工机械|野生技能"],
    "books":   [r"人文历史"],          # 再按标题里的书/读/诗 二次筛
    "film":    [r"影视|电影|剧|短片|小剧场|纪录片|音乐|演奏|翻唱|番剧|动画|动漫"],
    "game":    [r"游戏|电竞|桌游|音游"],
    "study":   [r"校园学习|职业职场|公开课"],
    "life":    [r"美食|日常|出行|手工|健身|运动|三农|动物|喵星人|汪星人|穿搭|美妆"],
    "friend":  [],
}
# "读书"这两个字太泛（"送外卖供三姐弟读书"也会命中），只认明确的节目名
BOOKS_HINT = ("书评", "讲书", "荐书", "书单", "书屋", "诗集", "古诗",
              "诗词", "古籍", "译文", "散文", "小说")

# 时政和读书在 B 站没有独立分区（资讯区不上热门榜、读书散在人文历史里），
# 主池常常是空的，只能退一步从全站热门里按标题认内容。
# 词要够硬——"美国""经典"这种一放进去，民谣和吃播就全进来了
REC_TITLE = {
    "news": ("选举", "民调", "议会", "外交", "示威", "罢工", "法案", "关税", "制裁",
             "加沙", "俄乌", "时事", "新闻", "通报", "首相", "内阁", "执政"),
    "books": ("书评", "讲书", "荐书", "书单", "书屋", "诗集", "古诗", "诗词", "古籍", "译文"),
}

# 这类账号上榜多是发广告/通稿，做推荐没意义
REC_DROP = re.compile(r"官方|手游|网游|新闻|日报|晚报|TV$|旗舰|专卖|"
                      r"哔哩哔哩|bilibili|腾讯|网易|米哈游|鹰角")

CONFIG_FILE = os.path.join(ROOT, "config.json")

try:
    with open(CONFIG_FILE, encoding="utf-8") as f:
        CFG = json.load(f)
except FileNotFoundError:
    CFG = {}
except Exception:
    CFG = {}

PORT = int(CFG.get("port", 8791))
store = Store(os.path.join(DATA, "biliplayer.db"))


# ---------------- 设置 ----------------
# 全部落到 data/settings.json。放服务端而不是浏览器的 localStorage，
# 因为换台机器、换个浏览器窗口都得是同一套；这个文件也很小，读写不用计较。
SETTINGS_FILE = os.path.join(DATA, "settings.json")
DEFAULT_SETTINGS = {
    "player": {
        "rate": 1,               # 默认倍速
        "quality": "highest",    # highest | auto | 具体高度如 "720"
        "autoplay_next": True,   # 播完自动下一个
        "next_wait": 5,          # 倒计时秒数
        "skip_intro": 0,         # 开播自动跳过前 N 秒（片头）
        "skip_outro": 0,         # 剩 N 秒时算看完
        "remember_pos": True,    # 记忆播放位置
        "volume": 1,
        "fit": "contain",        # contain 适应 | cover 铺满 | fill 拉伸
        "seek_step": 5,          # ←→ 一次跳几秒
        "seek_step_big": 30,     # Shift+←→
        "wheel_volume": True,    # 滚轮调音量
        "dblclick_full": True,   # 双击全屏
    },
    "danmaku": {
        "on": True,
        "opacity": 0.92,         # 不透明度
        "scale": 1,              # 字号倍率
        "area": 0.76,            # 弹幕占画面高度的比例（挡住画面的上限）
        "duration": 9,           # 一条滚动弹幕横穿屏幕用几秒，越小越快
        "bold": True,
        "stroke": 1,             # 描边粗细倍率，0 = 不要描边
        "scroll": True,          # 显示滚动弹幕
        "top": True,             # 显示顶部固定弹幕
        "bottom": True,          # 显示底部固定弹幕
        "merge": True,           # 同一秒内重复的只留一条
        "block": [],             # 屏蔽词，一行一个，支持简单正则
    },
    "ui": {
        "page_size": 2,          # 「每人 N 条」的默认值
        "days": 7,
    },
}


def _deep_merge(base, patch):
    out = dict(base)
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


_settings_lock = threading.Lock()


def load_settings():
    data = {}
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        pass
    return _deep_merge(DEFAULT_SETTINGS, data)


def save_settings(patch):
    with _settings_lock:
        cur = load_settings()
        new = _deep_merge(cur, patch or {})
        os.makedirs(DATA, exist_ok=True)
        tmp = SETTINGS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(new, f, ensure_ascii=False, indent=2)
        os.replace(tmp, SETTINGS_FILE)
        return new


# 首次运行判断：没有 config.json，或者 sessdata 是空的，就先去配置页
def needs_setup():
    return not (CFG.get("sessdata") or "").strip()


bili_sync = None
bili_play = None
bili_dm = None
DM_DIR = os.path.join(DATA, "dm")
# 取过的播放地址放这儿。playurl 是 B 站限得最死的接口之一，点快一点就
# 要等二十几秒重试，所以同一个视频几分钟内反复打开不要再打一次。
_play_cache = {}
_play_lock = threading.Lock()
PLAY_TTL = 300
_dm_lock = threading.Lock()
# 预览图 / 评论 / 相关 都是"打开播放器顺手取一次"的东西，给个短缓存
_shot_cache = {}
_rep_cache = {}
_rel_cache = {}
_misc_lock = threading.Lock()
sync_state = {"running": False, "phase": "", "done": 0, "total": 0, "new": 0,
              "message": "", "started": 0, "last_finish": 0, "error": ""}
last_ping = [time.time()]
_shutdown = threading.Event()


# ---------------- 同步 ----------------
class Syncer(threading.Thread):
    def __init__(self, pages):
        super().__init__(daemon=True)
        self.pages = pages

    def run(self):
        st = sync_state
        st.update(running=True, phase="分组", done=0, total=0, new=0,
                  message="读取关注分组…", started=time.time(), error="")
        try:
            user = bili_sync.nav()
            store.set_meta("user", user)

            tags = bili_sync.tags()
            groups = [{"tagid": t["tagid"], "name": t["name"], "count": t["count"]} for t in tags]
            store.set_meta("groups", groups)

            total = sum(t["count"] for t in tags) or 1
            st["total"] = total
            done = 0
            for t in tags:
                st["phase"] = f"分组：{t['name']}"
                st["message"] = f"正在读取「{t['name']}」（{t['count']} 人）"
                try:
                    members = bili_sync.tag_members(t["tagid"])
                except BiliError as e:
                    st["message"] = f"「{t['name']}」读取失败：{e}"
                    time.sleep(1)
                    continue
                for m in members:
                    store.upsert_up(m["mid"], m.get("uname", ""), m.get("face", ""), t["tagid"])
                done += len(members)
                st["done"] = min(done, total)

            # 关注了但不在任何分组里的人（关注列表比分组更全时兜底）
            st["phase"] = "关注列表"
            st["message"] = "补齐未分组的关注…"
            try:
                follows = bili_sync.followings(user["mid"])
                st["total"] = max(st["total"], len(follows))
                for m in follows:
                    store.upsert_up(m["mid"], m.get("uname", ""), m.get("face", ""), 0)
                st["done"] = st["total"]
            except BiliError:
                pass

            # 翻动态流
            st["phase"] = "动态流"
            st["done"], st["total"] = 0, self.pages
            offset, page, stop_streak = "", 1, 0
            while page <= self.pages:
                st["message"] = f"拉取第 {page}/{self.pages} 页动态…"
                try:
                    rows, offset, has_more = bili_sync.feed_video(page, offset)
                except BiliError as e:
                    st["message"] = f"第 {page} 页失败：{e}，稍后重试"
                    time.sleep(3)
                    if "风控" in str(e):
                        time.sleep(15)
                    if page > 5:
                        break
                    continue
                if not rows:
                    break
                for r in rows:
                    if store.save_video(r):
                        st["new"] += 1
                        stop_streak = 0
                    else:
                        stop_streak += 1
                        store.upsert_up(r["mid"], r["name"], r["face"], None)
                st["done"] = page
                if stop_streak >= 8 and page > 3:
                    st["message"] = "连续多页都是已入库内容，提前结束"
                    break
                if not offset or not has_more:
                    break
                page += 1

            st["phase"] = "完成"
            st["message"] = f"本次新增 {st['new']} 条，共 {store.video_count()} 条"
            store.set_meta("last_sync", int(time.time()))

            # 第一次用：库里还没有分类文件，顺手把建库流水线跑完（几分钟）
            if not os.path.exists(CURRENT["path"]):
                st["phase"] = "归类"
                st["message"] = "第一次使用，正在建立分类…"
                try:
                    import bootstrap
                    done = bootstrap.build(
                        progress=lambda m: st.update(message=f"正在{m}…"))
                    n = load_cats_file(CURRENT["path"])
                    bad = [f for f, r in done if r != "ok"]
                    st["message"] = (f"分类完成：{n} 条"
                                     + (f"（{len(bad)} 步没跑通：{', '.join(bad)}）" if bad else ""))
                    log(f"首次归类 {n} 条，异常步骤 {bad}")
                except Exception as e:
                    log(f"首次归类失败 {e}")
                    st["message"] = f"归类失败：{e}（可以稍后手动 python bootstrap.py）"
        except Exception as e:
            st["error"] = str(e)
            st["message"] = f"同步失败：{e}"
        finally:
            st["running"] = False
            st["last_finish"] = int(time.time())


def start_sync(pages=None):
    if sync_state["running"]:
        return False
    Syncer(int(pages or CFG.get("sync_pages", 40))).start()
    return True


# ---------------- 补全空白 ----------------
# 关注了几百人，动态流只往回翻 40 页，排在后面的人一条也抓不到，
# 「按人看」里就是一片「还没翻过他的投稿」。这里挨个去他们空间各翻一页，
# 把最近的投稿补进库。一页一次请求，串着来，别并发 —— B 站按 IP 限流。
fill_state = {"running": False, "done": 0, "total": 0, "added": 0,
              "message": "", "error": "", "started": 0}


class Filler(threading.Thread):
    def __init__(self, limit=80):
        super().__init__(daemon=True)
        self.limit = limit

    def run(self):
        st = fill_state
        rows = store.empty_ups(self.limit)
        st.update(running=True, done=0, total=len(rows), added=0,
                  message="", error="", started=time.time())
        for i, u in enumerate(rows):
            mid = u["mid"]
            st["done"] = i
            st["message"] = u.get("name") or str(mid)
            try:
                r = bili_play.space_dynamics(mid, "")
                for v in r["items"]:
                    v["mid"] = mid
                    if store.save_video(v):
                        st["added"] += 1
                store.set_dyn_cursor(mid, r["offset"], 0 if r["has_more"] else 1)
            except BiliError as e:
                st["error"] = str(e)
                break                      # 被风控了就别硬撑着继续撞
            except Exception:
                continue                   # 单个人失败不影响后面的
        st["done"] = st["total"]
        st["running"] = False
        st["message"] = f"补进来 {st['added']} 条"


def start_fill(limit=80):
    if fill_state["running"] or sync_state["running"] or bili_play is None:
        return False
    Filler(max(1, min(int(limit), 400))).start()
    return True


# ---------------- 分类与推荐 ----------------
CURRENT = {"path": os.path.join(DATA, "cats.json")}


def load_cats_file(path):
    """把 classify.py 的产物导进库"""
    try:
        with open(path, encoding="utf-8") as f:
            m = json.load(f)
    except Exception:
        return 0
    if not m:
        return 0
    store.set_cats({k: {"cat": v["cat"], "sub": v.get("sub") or ""}
                    for k, v in m.items() if v.get("cat")})
    store.fill_missing_sub(SUB_FALLBACK)
    try:
        with open(os.path.join(DATA, "stars.json"), encoding="utf-8") as f:
            store.set_stars(json.load(f))
    except Exception:
        pass
    store.set_meta("cats_applied", int(time.time()))
    return len(m)


def up_info(mid):
    """这个 UP 现在归在哪一类，给播放器那行小字用。"""
    try:
        mid = int(mid or 0)
    except Exception:
        mid = 0
    if not mid:
        return {"mid": 0, "cat": "", "manual": False, "name": "", "known": False}
    row = store.get_up_cat(mid) or {}
    up = store.get_up(mid) or {}
    return {"mid": mid, "cat": row.get("cat") or "",
            "sub": row.get("sub") or "",
            "manual": bool(row.get("manual")), "name": up.get("name") or "",
            # 相关推荐里点进来的 UP 可能压根不在关注列表里，那就没什么可分的
            "known": bool(up)}


def apply_up_cat(mid, cat, sub=""):
    """手动改分类。cat 传空串 = 恢复自动（回到 cats.json 算出来的那一类）。

    sub 只对生活这类带二级分类的生效；不传就沿用 cats.json 里算好的那一支。
    """
    mid = int(mid)
    if not cat:
        auto, autosub = "", ""
        try:
            with open(CURRENT["path"], encoding="utf-8") as f:
                v = json.load(f).get(str(mid)) or {}
                auto, autosub = v.get("cat") or "", v.get("sub") or ""
        except Exception:
            pass
        store.set_up_cat(mid, auto, manual=False, sub=autosub)
        return auto
    if cat not in CAT_NAME:
        raise ValueError("没有这个分类")
    valid = [s[0] for s in SUBCATS.get(cat, [])]
    if not sub and valid:
        # 换到一个有子分类的类但没指定分支：先落到第一支，之后能再改
        sub = valid[0]
    if sub and sub not in valid:
        raise ValueError("这个分类下没有这个分支")
    store.set_up_cat(mid, cat, manual=True, sub=sub)
    return cat


def _rank_data():
    try:
        with open(os.path.join(DATA, "rank.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"items": [], "tidmap": {}}


def recs_for(cat, n=6, offset=0):
    """这一分类下，你没关注的人在发什么。

    拿排行榜里对应分区的条目，排掉已关注/已入库、官方营销号，同一个 UP 只留一条。
    """
    srcs = REC_SRC.get(cat) or []
    if not srcs:
        return []
    pats = [re.compile(s) for s in srcs]
    rd = _rank_data()
    known_mid = store.known_mids()
    known_bvid = store.video_bvids()
    now = time.time()

    def pick(extra=None):
        out, seen = [], set()
        for it in rd.get("items") or []:
            tn = it.get("tname") or ""
            if not any(p.search(tn) for p in pats):
                continue
            if it.get("mid") in known_mid or it.get("bvid") in known_bvid:
                continue
            if it.get("mid") in seen:
                continue
            if REC_DROP.search(it.get("name") or ""):
                continue
            if extra and not extra(it):
                continue
            if it.get("pubdate") and now - it["pubdate"] > 120 * 86400:
                continue
            seen.add(it.get("mid"))
            out.append(it)
        out.sort(key=lambda x: -(x.get("pubdate") or 0))
        return out

    if cat == "books":
        rows = pick(lambda it: any(h in (it.get("title") or "") for h in BOOKS_HINT))
    else:
        rows = pick()

    # 主池空的时候，按标题在全站热门里捞
    if len(rows) < 3 and cat in REC_TITLE:
        hints = REC_TITLE[cat]
        have = {x.get("bvid") for x in rows}
        for it in rd.get("items") or []:
            if it.get("bvid") in have or it.get("mid") in known_mid:
                continue
            if REC_DROP.search(it.get("name") or ""):
                continue
            if not any(h in (it.get("title") or "") for h in hints):
                continue
            rows.append(it)
            have.add(it.get("bvid"))
            if len(rows) >= 4:
                break

    # 同一个分区（比如"数码"）在热门里占绝对多数时，直接按时间排会被它刷屏，
    # 所以先每个二级分区各取一条，保证推荐是多样的一撮，而不是一水儿手机评测
    def balance(rows_, n_):
        group = {}
        for it in rows_:
            group.setdefault(it.get("tname") or "", []).append(it)
        for v in group.values():
            v.sort(key=lambda x: -(x.get("pubdate") or 0))
        first = [v[0] for v in group.values()]
        first.sort(key=lambda x: -(x.get("pubdate") or 0))
        if len(first) >= n_:
            return first
        rest = [x for v in group.values() for x in v[1:]]
        rest.sort(key=lambda x: -(x.get("pubdate") or 0))
        return first + rest[:n_ - len(first)]

    rows = balance(rows, max(n + offset, 4))
    rows = rows[offset:offset + n]
    return [{
        "bvid": it["bvid"], "title": it.get("title", ""), "cover": it.get("cover", ""),
        "duration": _sec_to_text(it.get("duration") or 0),
        "name": it.get("name", ""), "face": it.get("face", ""),
        "play": it.get("play", 0), "tname": it.get("tname", ""),
        "pubdate": it.get("pubdate", 0), "mid": it.get("mid"),
    } for it in rows]


def _sec_to_text(sec):
    try:
        sec = int(sec)
    except Exception:
        return ""
    if sec <= 0:
        return ""
    h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


# ---------------- 首页 ----------------
# 首页一次要拼：继续看 + 每个分类最新几条 + 关注之外。分类十来个，
# 每个一次查询，本地库够快；但要防着切来切去反复算，缓存半分钟。
_home_cache = {"ts": 0, "data": None}
_home_lock = threading.Lock()
HOME_TTL = 30


def home_payload(fresh=False):
    now = int(time.time())
    with _home_lock:
        if not fresh and _home_cache["data"] and now - _home_cache["ts"] < HOME_TTL:
            return _home_cache["data"]

    prog = store.all_progress()

    # 接着看：看过一点、又没看完的
    cont = []
    for r in store.recent(limit=40):
        dur = r.get("dur") or 0
        if not dur:
            continue
        p = r["pos"] / dur
        if p <= 0.02 or p >= 0.95:
            continue
        r["progress"] = min(1.0, p)
        r["pub_ts"] = r.get("pub_ts") or r.get("updated")
        r["name"] = r.get("name") or ""
        r["face"] = r.get("face") or ""
        cont.append(r)
        if len(cont) >= 8:
            break

    counts = store.cat_counts()
    act = store.cat_activity(now - 7 * 86400)
    sections = []
    for cid, name, desc in CATS:
        items = store.list_videos(cat=cid, limit=6)
        if not items:
            continue
        for it in items:
            p = prog.get(it["bvid"])
            it["progress"] = min(1.0, p["pos"] / p["dur"]) if p and p.get("dur") else 0
        sections.append({"cat": cid, "name": name, "desc": desc,
                         "ups": counts.get(cid, 0), "active": act.get(cid, 0),
                         "items": items})

    try:
        discover = recs_for("", 6, 0)
    except Exception:
        discover = []

    data = {
        "today": store.activity(now - 86400),
        "week": store.activity(now - 7 * 86400),
        "continue": cont,
        "sections": sections,
        "discover": discover,
        "stats": {"ups": store.up_count(), "videos": store.video_count(),
                  "last_sync": store.get_meta("last_sync") or store.max_seen_ts()},
    }
    with _home_lock:
        _home_cache.update(ts=now, data=data)
    return data


# ---------------- 弹幕 ----------------
_DM_RE = re.compile(r'<d p="([^"]*)"[^>]*>(.*?)</d>', re.S)
_DM_TAG = re.compile(r'<[^>]+>')


def parse_danmaku(xml):
    """p="时间,模式,字号,颜色,..."；只留滚动(1/2/3)、顶部(5)、底部(4)。
    高级弹幕(7)和代码弹幕(8)要跑脚本，直接丢掉。"""
    out = []
    for m in _DM_RE.finditer(xml):
        p = m.group(1).split(",")
        if len(p) < 4:
            continue
        try:
            t = float(p[0])
            mode = int(p[1])
            color = int(p[3])
        except ValueError:
            continue
        if mode not in (1, 2, 3, 4, 5):
            continue
        txt = htmlmod.unescape(_DM_TAG.sub("", m.group(2))).strip()
        if not txt:
            continue
        out.append([round(t, 2), mode, color & 0xFFFFFF, txt[:120]])
    out.sort(key=lambda x: x[0])
    return out


def danmaku_for(cid):
    """磁盘缓存 3 天。一个视频动辄几千条，别每次都回源。"""
    os.makedirs(DM_DIR, exist_ok=True)
    p = os.path.join(DM_DIR, f"{cid}.json")
    try:
        if os.path.exists(p) and time.time() - os.path.getmtime(p) < 3 * 86400:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    with _dm_lock:
        # 别让两个请求同时回源同一个 cid
        try:
            if os.path.exists(p) and time.time() - os.path.getmtime(p) < 3 * 86400:
                with open(p, encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        items = parse_danmaku(bili_dm.danmaku_xml(cid))
        try:
            with open(p, "w", encoding="utf-8") as f:
                json.dump(items, f, ensure_ascii=False, separators=(",", ":"))
        except Exception:
            pass
        return items


# ---------------- 进度条预览 / 评论 / 相关 ----------------
def shots_for(bvid, cid):
    """进度条悬停用的缩略图。一张 10x10 精灵图 + 每个格子的时间点。"""
    key = f"{bvid}:{cid}"
    with _misc_lock:
        hit = _shot_cache.get(key)
        if hit and time.time() - hit["ts"] < 6 * 3600:
            return hit["data"]
    try:
        data = bili_dm.videoshot(bvid, cid)
    except Exception:
        data = None
    with _misc_lock:
        _shot_cache[key] = {"ts": time.time(), "data": data}
        if len(_shot_cache) > 80:
            for k in list(_shot_cache)[:40]:
                _shot_cache.pop(k, None)
    return data


def replies_for(bvid, aid, pn=1, sort=1):
    key = f"{bvid}:{pn}:{sort}"
    with _misc_lock:
        hit = _rep_cache.get(key)
        if hit and time.time() - hit["ts"] < 300:
            return hit["data"]
    if not aid:
        aid = bili_play.view(bvid).get("aid")
    data = bili_dm.replies(aid, pn, sort)
    with _misc_lock:
        _rep_cache[key] = {"ts": time.time(), "data": data}
        if len(_rep_cache) > 60:
            for k in list(_rep_cache)[:30]:
                _rep_cache.pop(k, None)
    return data


def related_for(bvid):
    with _misc_lock:
        hit = _rel_cache.get(bvid)
        if hit and time.time() - hit["ts"] < 1800:
            return hit["data"]
    data = bili_play.related(bvid)
    with _misc_lock:
        _rel_cache[bvid] = {"ts": time.time(), "data": data}
        if len(_rel_cache) > 60:
            for k in list(_rel_cache)[:30]:
                _rel_cache.pop(k, None)
    return data


def init_bili(sessdata):
    """装/换账号后重建三个客户端（同步慢、播放快、弹幕极快）。"""
    global bili_sync, bili_play, bili_dm, CFG
    bili_sync = Bili(sessdata, interval=float(CFG.get("request_interval", 1.0)))
    bili_play = Bili(sessdata, interval=0.7)
    bili_dm = Bili(sessdata, interval=0.15)


# ---------------- 交给系统浏览器 ----------------
_EXT_OK = re.compile(r"^https://[\w.-]*bilibili\.com/")
# 只代理 B 站自己的图床，别把这个端口变成任意 URL 的跳板
_IMG_OK = re.compile(r"^https://[\w.-]*(hdslb\.com|bilibili\.com)/")

def open_external(url):
    """用默认浏览器打开 B 站网页版。只放行 bilibili.com，
    免得这个本地端口变成任人使唤的启动器。"""
    if not _EXT_OK.match(url or ""):
        return False
    try:
        os.startfile(url)          # Windows：走 ShellExecute，等同双击链接
        return True
    except Exception:
        try:
            import webbrowser
            return webbrowser.open(url)
        except Exception:
            return False


# ---------------- MPD ----------------
def _pick(d, *names):
    for n in names:
        if n in d and d[n]:
            return d[n]
    return None


def _seg_base(rep):
    sb = _pick(rep, "SegmentBase", "segment_base", "segmentBase") or {}
    init = _pick(sb, "Initialization", "initialization", "init") or {}
    if isinstance(init, dict):
        init_range = _pick(init, "range") or _pick(rep, "initRange", "init_range") or ""
    else:
        init_range = init  # B 站这里直接给 "0-917" 这种 range 字符串
    index = _pick(sb, "indexRange", "index_range", "indexRangeExact") or ""
    return init_range, index


def build_mpd(data, duration, qn_pref=""):
    dash = data.get("dash") or data
    host = f"http://127.0.0.1:{PORT}"
    vids, auds = [], []
    for v in dash.get("video") or []:
        codecs = (v.get("codecs") or "").lower()
        if not codecs.startswith("avc1"):
            continue  # HEVC 在部分设备上播不了，只保留 H.264
        init, index = _seg_base(v)
        url = v.get("baseUrl") or v.get("base_url")
        if not url or not init or not index:
            continue
        enc = base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")
        vids.append({
            "id": str(v.get("id")), "bandwidth": int(v.get("bandwidth") or 1000000),
            "width": v.get("width"), "height": v.get("height"),
            "frameRate": v.get("frameRate") or v.get("frame_rate") or "30",
            "codecs": v.get("codecs"), "sar": v.get("sar") or "1:1",
            "base": f"{host}/stream?u={enc}", "init": init, "index": index,
        })
    for a in dash.get("audio") or []:
        init, index = _seg_base(a)
        url = a.get("baseUrl") or a.get("base_url")
        if not url or not init or not index:
            continue
        enc = base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")
        auds.append({
            "id": str(a.get("id")), "bandwidth": int(a.get("bandwidth") or 128000),
            "codecs": a.get("codecs") or "mp4a.40.2",
            "base": f"{host}/stream?u={enc}", "init": init, "index": index,
        })
    if not vids or not auds:
        raise BiliError("没有可用的 DASH 轨道")
    vids.sort(key=lambda x: x["height"] or 0)
    auds.sort(key=lambda x: x["bandwidth"], reverse=True)

    maxw = max(v["width"] for v in vids)
    maxh = max(v["height"] for v in vids)
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" '
        'profiles="urn:mpeg:dash:profile:isoff-on-demand:2011" type="static" '
        f'mediaPresentationDuration="PT{int(duration)}S" minBufferTime="PT1.5S">',
        f'<Period duration="PT{int(duration)}S">',
        f'<AdaptationSet contentType="video" mimeType="video/mp4" segmentAlignment="true" '
        f'startWithSAP="1" maxWidth="{maxw}" maxHeight="{maxh}" par="16:9">',
    ]
    for v in vids:
        lines.append(
            f'<Representation id="{v["id"]}" bandwidth="{v["bandwidth"]}" '
            f'codecs="{v["codecs"]}" width="{v["width"]}" height="{v["height"]}" '
            f'frameRate="{v["frameRate"]}" sar="{v["sar"]}">'
            f'<BaseURL>{v["base"]}</BaseURL>'
            f'<SegmentBase indexRange="{v["index"]}">'
            f'<Initialization range="{v["init"]}"/></SegmentBase></Representation>')
    lines.append('</AdaptationSet>')
    lines.append('<AdaptationSet contentType="audio" mimeType="audio/mp4" segmentAlignment="true" lang="und">')
    for a in auds:
        lines.append(
            f'<Representation id="{a["id"]}" bandwidth="{a["bandwidth"]}" codecs="{a["codecs"]}">'
            f'<BaseURL>{a["base"]}</BaseURL>'
            f'<SegmentBase indexRange="{a["index"]}">'
            f'<Initialization range="{a["init"]}"/></SegmentBase></Representation>')
    lines.append('</AdaptationSet></Period></MPD>')
    return "\n".join(lines)


# ---------------- HTTP ----------------
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "BiliPlayer/1.0"

    def log_message(self, fmt, *args):
        pass

    # ---- helpers ----
    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _json_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    # ---- 路由 ----
    def do_GET(self):
        last_ping[0] = time.time()
        u = urllib.parse.urlparse(self.path)
        path, qs = u.path, urllib.parse.parse_qs(u.query)
        try:
            if path == "/" or path == "/index.html":
                if needs_setup() and (qs.get("force") or [""])[0] != "1":
                    return self._file("setup.html", "text/html; charset=utf-8")
                return self._file("index.html", "text/html; charset=utf-8")
            if path == "/setup":
                return self._file("setup.html", "text/html; charset=utf-8")
            if path.startswith("/static/"):
                return self._file(path[8:], None)
            # 还没连账号时，除了配置页自己要用的几个接口，一律给个说得清的错误，
            # 别让前端对着 None 报 "'NoneType' object has no attribute ..."
            if (path.startswith("/api/") and bili_play is None
                    and path not in ("/api/settings", "/api/setup", "/api/state")):
                return self._send(200, {"error": "还没有连接账号，先填一次 SESSDATA",
                                        "need_setup": True, "items": [], "total": 0,
                                        "list": [], "replies": [], "continue": []})
            if path == "/api/state":
                return self._state()
            if path == "/api/home":
                try:
                    fresh = (qs.get("fresh") or [""])[0] == "1"
                    return self._send(200, home_payload(fresh))
                except Exception as e:
                    return self._send(200, {"error": str(e), "continue": [],
                                            "sections": [], "discover": [],
                                            "today": {}, "week": {}, "stats": {}})
            if path == "/api/videos":
                return self._videos(qs)
            if path == "/api/ups":
                return self._ups(qs)
            if path == "/api/recs":
                return self._recs(qs)
            if path == "/api/up":
                return self._up_detail(qs)
            if path == "/api/up/fill":
                return self._send(200, fill_state)
            if path == "/api/play":
                return self._play(qs)
            if path == "/api/danmaku":
                return self._danmaku(qs)
            if path == "/api/shots":
                return self._shots(qs)
            if path == "/api/replies":
                return self._replies(qs)
            if path == "/api/related":
                return self._related(qs)
            if path == "/api/search":
                return self._search(qs)
            if path == "/api/settings":
                return self._send(200, load_settings())
            if path == "/api/setup":
                return self._send(200, {"needed": needs_setup(),
                                        "configured": bool(CFG.get("sessdata")),
                                        "user": store.get_meta("user")})
            if path == "/api/recent":
                return self._recent(qs)
            if path == "/api/open":
                return self._open(qs)
            if path == "/api/mpd":
                return self._mpd(qs)
            if path == "/api/progress":
                return self._send(200, {"ok": True, "items": store.all_progress()})
            if path == "/api/sync":
                return self._send(200, dict(sync_state, video_count=store.video_count(),
                                            up_count=store.up_count()))
            if path == "/stream":
                return self._stream(qs)
            if path == "/api/img":
                return self._img(qs)
            if path == "/favicon.ico":
                self.send_response(204)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            return self._send(404, {"error": "not found"})
        except BrokenPipeError:
            pass
        except Exception as e:
            try:
                self._send(500, {"error": str(e)})
            except Exception:
                pass

    def do_POST(self):
        last_ping[0] = time.time()
        u = urllib.parse.urlparse(self.path)
        body = self._json_body()
        try:
            if u.path == "/api/settings":
                return self._send(200, {"ok": True, "settings": save_settings(body)})
            if u.path == "/api/setup":
                return self._setup(body)
            if u.path == "/api/sync":
                ok = start_sync(body.get("pages"))
                return self._send(200, {"started": ok, "state": sync_state})
            if u.path == "/api/progress":
                store.set_progress(body.get("bvid"), float(body.get("pos") or 0),
                                   float(body.get("dur") or 0),
                                   body.get("title"), body.get("cover"),
                                   body.get("name"))
                return self._send(200, {"ok": True})
            if u.path == "/api/exclude":
                ex = store.get_meta("exclude_ups", []) or []
                mid = body.get("mid")
                if mid in ex:
                    ex.remove(mid)
                    on = False
                else:
                    ex.append(mid)
                    on = True
                store.set_meta("exclude_ups", ex)
                return self._send(200, {"ok": True, "excluded": on, "list": ex})
            if u.path == "/api/up/fill":
                ok = start_fill(body.get("limit") or 80)
                return self._send(200, {"started": ok, "state": fill_state})
            if u.path == "/api/up/fetch":
                return self._up_fetch(body)
            if u.path == "/api/set_cat":
                mid = body.get("mid")
                if not mid:
                    return self._send(400, {"ok": False, "error": "缺少 mid"})
                try:
                    cat = apply_up_cat(mid, body.get("cat") or "",
                                       body.get("sub") or "")
                except ValueError as e:
                    return self._send(400, {"ok": False, "error": str(e)})
                return self._send(200, {"ok": True, "up": up_info(mid),
                                        "counts": store.cat_counts(),
                                        "subs": store.sub_counts().get(cat, {})})
            if u.path == "/api/reclassify":
                n = load_cats_file(CURRENT["path"])
                return self._send(200, {"ok": True, "applied": n})
            if u.path == "/api/recs/refresh":
                def _bg():
                    try:
                        rankmod.fetch(bili_play, force=True)
                    except Exception:
                        pass
                threading.Thread(target=_bg, daemon=True).start()
                return self._send(200, {"ok": True, "started": True})
            if u.path == "/api/shutdown":
                threading.Thread(target=lambda: (time.sleep(0.3), _shutdown.set()), daemon=True).start()
                return self._send(200, {"ok": True})
            return self._send(404, {"error": "not found"})
        except BrokenPipeError:
            pass
        except Exception as e:
            try:
                self._send(500, {"error": str(e)})
            except Exception:
                pass

    # ---- 静态 ----
    def _file(self, name, ctype):
        p = os.path.join(STATIC, name)
        if not os.path.isfile(p):
            return self._send(404, {"error": "no file"})
        if ctype is None:
            ext = os.path.splitext(p)[1].lower()
            ctype = {".js": "application/javascript; charset=utf-8",
                     ".css": "text/css; charset=utf-8",
                     ".html": "text/html; charset=utf-8",
                     ".svg": "image/svg+xml",
                     ".png": "image/png"}.get(ext, "application/octet-stream")
        with open(p, "rb") as f:
            data = f.read()
        # 本地服务，改完必须立刻生效，别让浏览器缓存住
        self._send(200, data, ctype, {"Cache-Control": "no-store"})

    # ---- API ----
    def _state(self):
        user = store.get_meta("user")
        groups = store.get_meta("groups") or []
        since7 = int(time.time()) - 7 * 86400
        counts = store.cat_counts()
        subs = store.sub_counts()
        active = store.cat_activity(since7)
        cats = []
        for cid, name, desc in CATS:
            # 有二级分类的大类，把每个小标签的人数一起带上，侧栏展开就能画
            sublist = []
            for sid, snm, sdesc in SUBCATS.get(cid, []):
                n = subs.get(cid, {}).get(sid, 0)
                if n:
                    sublist.append({"id": sid, "name": snm, "desc": sdesc, "count": n})
            cats.append({"id": cid, "name": name, "desc": desc,
                         "count": counts.get(cid, 0), "active": active.get(cid, 0),
                         "subs": sublist})
        stars = store._query("SELECT COUNT(*) c FROM ups WHERE star=1")[0]["c"]
        return self._send(200, {
            "user": user,
            "groups": groups,
            "cats": cats,
            "stars": stars,
            "need_setup": needs_setup(),
            "stats": {"ups": store.up_count(), "videos": store.video_count(),
                      "last_sync": store.get_meta("last_sync") or store.max_seen_ts(),
                      "unclassified": counts.get("", 0),
                      "empty_ups": store.empty_count()},
            "sync": sync_state,
            "fill": fill_state,
            "exclude": store.get_meta("exclude_ups", []),
            "per_up_limit": CFG.get("per_up_limit", 2),
        })

    def _recs(self, qs):
        cat = (qs.get("cat") or [""])[0]
        n = int((qs.get("n") or ["6"])[0])
        off = int((qs.get("offset") or ["0"])[0])
        try:
            return self._send(200, {"items": recs_for(cat, n, off)})
        except Exception as e:
            return self._send(200, {"items": [], "error": str(e)})

    def _videos(self, qs):
        tagid = (qs.get("group") or [""])[0]
        cat = (qs.get("cat") or [""])[0]
        star = (qs.get("star") or [""])[0] == "1"
        q = (qs.get("q") or [""])[0].strip().lower()
        days = int((qs.get("days") or ["0"])[0] or 0)
        since = int(time.time()) - days * 86400 if days else None
        sub = (qs.get("sub") or [""])[0]
        rows = store.list_videos(tagid=tagid or None, since_ts=since, limit=1500,
                                 cat=cat or None, star=star, sub=sub or None)
        if q:
            rows = [r for r in rows if q in (r["title"] or "").lower() or q in (r["name"] or "").lower()]
        prog = store.all_progress()
        ex = set(store.get_meta("exclude_ups", []) or [])
        for r in rows:
            p = prog.get(r["bvid"])
            r["progress"] = min(1.0, (p["pos"] / p["dur"])) if p and p.get("dur") else 0
            r["excluded"] = r["mid"] in ex
        return self._send(200, {"items": rows, "total": len(rows)})

    def _ups(self, qs):
        tagid = (qs.get("group") or [""])[0]
        cat = (qs.get("cat") or [""])[0]
        star = (qs.get("star") or [""])[0] == "1"
        sub = (qs.get("sub") or [""])[0]
        rows = store.list_ups(tagid=tagid or None, cat=cat or None, star=star,
                              sub=sub or None)
        now = int(time.time())
        for r in rows:
            lp = r.get("last_pub") or 0
            r["days_idle"] = int((now - lp) / 86400) if lp else None
        return self._send(200, {"items": rows})

    def _up_detail(self, qs):
        mid = int((qs.get("mid") or ["0"])[0])
        up = store.get_up(mid) or {}
        span = store.span_by_mid(mid)
        cur = store.dyn_cursor(mid)
        hi = span.get("hi") or 0
        now = int(time.time())
        return self._send(200, {
            "up": up,
            "videos": store.videos_by_mid(mid, 600),
            "count": span.get("c") or 0,
            "first_ts": span.get("lo") or 0,
            "last_ts": hi,
            "days_idle": int((now - hi) / 86400) if hi else None,
            "done": bool(cur["done"]),
        })

    def _up_fetch(self, body):
        """往一个人更早的投稿里翻几页。

        每个人的游标存在 ups.dyn_offset 上，所以连着点几次是「接着上次往前」，
        不会每次都从最新一页重来。
        """
        if bili_play is None:
            return self._send(200, {"ok": False, "error": "还没有连接账号"})
        mid = int(body.get("mid") or 0)
        if not mid:
            return self._send(400, {"ok": False, "error": "缺少 mid"})
        try:
            pages = int(body.get("pages") or 5)
        except (TypeError, ValueError):
            pages = 5
        pages = max(1, min(pages, 20))

        if not store.get_up(mid):
            store.upsert_up(mid, body.get("name") or str(mid), body.get("face") or "")

        cur = store.dyn_cursor(mid)
        if cur["done"]:
            return self._send(200, {"ok": True, "added": 0, "fetched": 0,
                                    "pages": 0, "done": True})

        offset = cur["offset"]
        added = 0
        fetched = 0
        done = False
        for i in range(pages):
            try:
                r = bili_play.space_dynamics(mid, offset)
            except BiliError as e:
                store.set_dyn_cursor(mid, offset, 0)
                span = store.span_by_mid(mid)
                return self._send(200, {"ok": False, "error": str(e), "added": added,
                                        "fetched": fetched, "pages": i, "done": False,
                                        "count": span.get("c") or 0})
            for v in r["items"]:
                v["mid"] = mid
                if store.save_video(v):
                    added += 1
                fetched += 1
            offset = r["offset"]
            if not r["has_more"] or not offset:
                done = True
                break

        store.set_dyn_cursor(mid, offset, 1 if done else 0)
        span = store.span_by_mid(mid)
        return self._send(200, {"ok": True, "added": added, "fetched": fetched,
                                "pages": pages if not done else "末",
                                "done": done, "count": span.get("c") or 0,
                                "first_ts": span.get("lo") or 0})

    def _play(self, qs):
        bvid = (qs.get("bvid") or [""])[0]
        cid = (qs.get("cid") or [""])[0]
        use_cid = int(cid) if cid else 0
        key = f"{bvid}:{use_cid}"

        with _play_lock:
            hit = _play_cache.get(key)
            if hit and time.time() - hit["ts"] < PLAY_TTL:
                cached = hit
            else:
                cached = None

        if cached:
            v, dash, use_cid = cached["view"], cached["dash"], cached["cid"]
        else:
            v = bili_play.view(bvid)
            use_cid = use_cid or v["cid"]
            data = bili_play.playurl(bvid, use_cid, int(CFG.get("qn", 112)))
            dash = data.get("dash") or data
            with _play_lock:
                _play_cache[key] = {"ts": time.time(), "view": v, "dash": dash,
                                    "cid": use_cid}
                _play_cache[f"{bvid}:{use_cid}"] = _play_cache[key]
                if len(_play_cache) > 60:      # 别一直攒着
                    for k in list(_play_cache)[:30]:
                        _play_cache.pop(k, None)

        quality = []
        for x in dash.get("video") or []:
            if (x.get("codecs") or "").lower().startswith("avc1"):
                quality.append({"id": x["id"], "label": f'{x["height"]}P',
                                "width": x["width"], "height": x["height"]})
        seen, uniq = set(), []
        for x in sorted(quality, key=lambda y: y["height"], reverse=True):
            if x["height"] not in seen:
                seen.add(x["height"])
                uniq.append(x)
        return self._send(200, {
            "view": v,
            "aid": v.get("aid"),
            "cid": use_cid,
            "quality": uniq,
            "mpd": f"/api/mpd?bvid={bvid}&cid={use_cid}",
            "progress": store.get_progress(bvid),
            "up": up_info((v.get("owner") or {}).get("mid")),
        })

    def _danmaku(self, qs):
        cid = (qs.get("cid") or [""])[0]
        if not cid:
            return self._send(400, {"error": "no cid", "items": []})
        try:
            items = danmaku_for(cid)
        except Exception as e:
            return self._send(200, {"items": [], "n": 0, "error": str(e)})
        return self._send(200, {"items": items, "n": len(items)})

    def _shots(self, qs):
        bvid = (qs.get("bvid") or [""])[0]
        cid = (qs.get("cid") or [""])[0]
        if not bvid or not cid:
            return self._send(200, {"ok": False})
        try:
            data = shots_for(bvid, cid)
        except Exception as e:
            return self._send(200, {"ok": False, "error": str(e)})
        if not data:
            return self._send(200, {"ok": False})
        return self._send(200, dict(data, ok=True))

    def _replies(self, qs):
        bvid = (qs.get("bvid") or [""])[0]
        aid = (qs.get("aid") or [""])[0]
        pn = int((qs.get("pn") or ["1"])[0] or 1)
        sort = int((qs.get("sort") or ["1"])[0] or 1)
        if not bvid:
            return self._send(400, {"error": "no bvid", "items": []})
        try:
            return self._send(200, replies_for(bvid, aid, pn, sort))
        except Exception as e:
            return self._send(200, {"items": [], "count": 0, "error": str(e)})

    def _related(self, qs):
        bvid = (qs.get("bvid") or [""])[0]
        if not bvid:
            return self._send(400, {"error": "no bvid", "items": []})
        try:
            return self._send(200, {"items": related_for(bvid)})
        except Exception as e:
            return self._send(200, {"items": [], "error": str(e)})

    def _search(self, qs):
        q = ((qs.get("q") or [""])[0] or "").strip()
        pn = int((qs.get("page") or ["1"])[0] or 1)
        if not q:
            return self._send(200, {"items": [], "total": 0})
        try:
            return self._send(200, bili_play.search(q, pn))
        except Exception as e:
            return self._send(200, {"items": [], "total": 0, "error": str(e)})

    def _setup(self, body):
        """首次配置：收 SESSDATA，验一次登录，写进 config.json。"""
        global CFG
        sess = (body.get("sessdata") or "").strip()
        if not sess:
            return self._send(400, {"ok": False, "error": "SESSDATA 不能为空"})
        # 有人会把整串 "SESSDATA=xxx; " 复制进来，剁掉
        if "=" in sess:
            m = re.search(r"SESSDATA=([^;\s]+)", sess)
            if m:
                sess = m.group(1)
        sess = sess.strip().strip(";")
        try:
            probe = Bili(sess, interval=0.3)
            user = probe.nav()
        except Exception as e:
            return self._send(200, {"ok": False, "error": f"验证失败：{e}"})

        patch = {"sessdata": sess}
        for k in ("sync_pages", "qn"):
            if body.get(k) not in (None, ""):
                try:
                    patch[k] = int(body[k])
                except Exception:
                    pass
        CFG.update(patch)
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(CFG, f, ensure_ascii=False, indent=2)
        except Exception as e:
            return self._send(500, {"ok": False, "error": f"写 config.json 失败：{e}"})

        init_bili(sess)
        store.set_meta("user", user)
        log(f"配置完成：{user.get('uname')}（{user.get('mid')}）")

        def _after():
            time.sleep(0.3)
            try:
                rankmod.fetch(bili_play)
            except Exception:
                pass
            start_sync()
        threading.Thread(target=_after, daemon=True).start()
        return self._send(200, {"ok": True, "user": user})

    def _recent(self, qs):
        n = int((qs.get("n") or ["40"])[0])
        rows = store.recent(limit=n)
        for r in rows:
            r["progress"] = min(1.0, (r["pos"] / r["dur"])) if r.get("dur") else 0
            r["pub_ts"] = r.get("pub_ts") or r.get("updated")
            r["name"] = r.get("name") or ""
            r["face"] = r.get("face") or ""
        return self._send(200, {"items": rows})

    def _open(self, qs):
        url = (qs.get("url") or [""])[0]
        ok = open_external(url)
        return self._send(200 if ok else 400, {"ok": ok, "url": url})

    def _mpd(self, qs):
        bvid = (qs.get("bvid") or [""])[0]
        cid = (qs.get("cid") or [""])[0]
        cache_key = f"mpd:{bvid}:{cid}"
        cached = store.get_meta(cache_key)
        if cached and cached.get("ts", 0) > time.time() - 3600:
            return self._send(200, cached["xml"], "application/dash+xml; charset=utf-8")
        # 刚刚 /api/play 已经取过一次了，别为了同一个视频再打一遍 playurl
        with _play_lock:
            hit = _play_cache.get(f"{bvid}:{cid}")
        if hit and time.time() - hit["ts"] < PLAY_TTL:
            dash = hit["dash"]
            dur = dash.get("duration") or 0
        else:
            data = bili_play.playurl(bvid, int(cid), int(CFG.get("qn", 112)))
            dash = data.get("dash") or data
            dur = dash.get("duration") or data.get("duration") or 0
            if not dur:
                dur = bili_play.view(bvid).get("duration") or 0
        if not dash.get("duration"):
            dur = dur or bili_play.view(bvid).get("duration") or 0
        else:
            dur = dash.get("duration") or dur
        xml = build_mpd(dash, dur)
        store.set_meta(cache_key, {"xml": xml, "ts": int(time.time())})
        return self._send(200, xml, "application/dash+xml; charset=utf-8")

    # ---- 图片中转 ----
    _img_cache = {}

    def _img(self, qs):
        """B 站图床查 Referer，页面在 127.0.0.1 上直接引用会 403，
        头像和预览图就是这么挂的。这里补上 Referer 再转一次。"""
        u = (qs.get("u") or [""])[0]
        if u.startswith("//"):
            u = "https:" + u
        if not _IMG_OK.match(u):
            return self._send(400, {"error": "only bilibili images"})
        now = time.time()
        with _misc_lock:
            hit = Handler._img_cache.get(u)
            if hit and now - hit[0] < 6 * 3600:
                data, ct = hit[1], hit[2]
                kind = "hit"
            else:
                data = None
        if data is None:
            kind = "miss"
            try:
                req = urllib.request.Request(u, headers={
                    "User-Agent": UA,
                    "Referer": "https://www.bilibili.com/",
                    "Accept": "image/avif,image/webp,image/*,*/*;q=0.8",
                })
                opener = urllib.request.build_opener()
                with opener.open(req, timeout=20) as r:
                    data = r.read()
                    ct = r.headers.get("Content-Type") or "image/jpeg"
            except Exception:
                return self._send(404, {"error": "image fetch failed"})
            with _misc_lock:
                if len(Handler._img_cache) > 300:
                    for k in list(Handler._img_cache)[:150]:
                        Handler._img_cache.pop(k, None)
                Handler._img_cache[u] = (now, data, ct)
        self.send_response(200)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(data)))
        # 图床地址基本不变，让浏览器自己存着，别每次都回来问
        self.send_header("Cache-Control", "public, max-age=604800")
        self.send_header("X-Img-Cache", kind)
        self.end_headers()
        try:
            self.wfile.write(data)
        except Exception:
            pass

    # ---- 视频中转 ----
    def _stream(self, qs):
        enc = (qs.get("u") or [""])[0]
        if not enc:
            return self._send(400, {"error": "no url"})
        enc += "=" * (-len(enc) % 4)
        try:
            url = base64.urlsafe_b64decode(enc).decode()
        except Exception:
            return self._send(400, {"error": "bad url"})
        headers = {"User-Agent": UA, "Referer": "https://www.bilibili.com/",
                   "Origin": "https://www.bilibili.com", "Accept": "*/*"}
        rng = self.headers.get("Range")
        if rng:
            headers["Range"] = rng
        req = urllib.request.Request(url, headers=headers)
        opener = urllib.request.build_opener()  # 不带 cookie，别把 SESSDATA 发给 CDN
        try:
            resp = opener.open(req, timeout=40)
        except urllib.error.HTTPError as e:
            return self._send(e.code, {"error": f"upstream {e.code}"})
        except Exception as e:
            return self._send(502, {"error": str(e)})
        try:
            code = resp.getcode() or 200
            self.send_response(206 if code == 206 else 200)
            for h in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges"):
                v = resp.headers.get(h)
                if v:
                    self.send_header(h, v)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            try:
                resp.close()
            except Exception:
                pass


def watchdog():
    time.sleep(20)
    while not _shutdown.is_set():
        if time.time() - last_ping[0] > 300:
            _shutdown.set()
            break
        time.sleep(10)


def log(msg):
    """pythonw 下 sys.stdout 是 None，print 会抛异常把进程带走，所以写文件为主。"""
    try:
        with open(os.path.join(DATA, "run.log"), "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + str(msg) + "\n")
    except Exception:
        pass
    try:
        print(msg)
    except Exception:
        pass


def claim_single_instance():
    """同一时间只留一个服务进程。

    Windows 的 SO_REUSEADDR 允许两个进程绑同一端口，第二个不会报错，
    而是和第一个抢连接 —— 表现就是"改了代码有时生效有时不生效"。
    互斥名带上端口：同一个人在别的目录用别的端口跑第二份，不该被挡住。
    """
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        k32.CreateMutexW(None, False, "Local\\BiliPlayerServer_%d" % PORT)
        return k32.GetLastError() != 183        # ERROR_ALREADY_EXISTS
    except Exception:
        return True


def main():
    global bili_sync, bili_play, bili_dm
    if not claim_single_instance():
        log("已有实例在跑，这次退出")
        return
    if CFG.get("sessdata"):
        bili_sync = Bili(CFG["sessdata"], interval=float(CFG.get("request_interval", 1.0)))
        bili_play = Bili(CFG["sessdata"], interval=0.7)
        bili_dm = Bili(CFG["sessdata"], interval=0.15)
    else:
        log("还没有配置 SESSDATA，先走配置页")
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    srv.daemon_threads = True
    log(f"BiliPlayer 已启动: http://127.0.0.1:{PORT}")
    threading.Thread(target=watchdog, daemon=True).start()

    if needs_setup():
        # 配置页上什么都不该预热，等填完 SESSDATA 重启再说
        srv.timeout = 1
        while not _shutdown.is_set():
            srv.handle_request()
        try:
            srv.server_close()
        except Exception:
            pass
        return

    # 导入分类与星标（classify.py 跑过就有 cats.json）
    if os.path.exists(CURRENT["path"]):
        n = load_cats_file(CURRENT["path"])
        log(f"导入分类 {n} 条")

    # 排行榜缓存过期就在后台重抓，不挡首页
    def _warm():
        try:
            time.sleep(2)
            rankmod.fetch(bili_play)
        except Exception as e:
            log(f"排行榜预热失败 {e}")
    threading.Thread(target=_warm, daemon=True).start()

    # 首次启动自动建库
    if store.video_count() == 0:
        threading.Timer(1.0, lambda: start_sync()).start()

    srv.timeout = 1
    while not _shutdown.is_set():
        srv.handle_request()
    try:
        srv.server_close()
    except Exception:
        pass


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        log("崩溃: " + repr(e))
        log(traceback.format_exc())
