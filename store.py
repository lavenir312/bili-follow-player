# -*- coding: utf-8 -*-
"""本地库：SQLite 存 UP、视频、播放进度、同步状态"""
import json
import os
import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS ups(
  mid INTEGER PRIMARY KEY,
  name TEXT,
  face TEXT,
  groups TEXT DEFAULT '',
  last_ts INTEGER DEFAULT 0,
  seen_ts INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS videos(
  bvid TEXT PRIMARY KEY,
  mid INTEGER,
  pub_ts INTEGER,
  title TEXT,
  cover TEXT,
  duration TEXT,
  play INTEGER DEFAULT 0,
  type_name TEXT,
  seen_ts INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_videos_pub ON videos(pub_ts DESC);
CREATE INDEX IF NOT EXISTS idx_videos_mid ON videos(mid);
CREATE TABLE IF NOT EXISTS progress(
  bvid TEXT PRIMARY KEY,
  pos REAL DEFAULT 0,
  dur REAL DEFAULT 0,
  updated INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
"""


class Store:
    def __init__(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.path = path
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self):
        """老库补列，别动已有的数据"""
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(ups)")}
        if "cat" not in cols:
            self.conn.execute("ALTER TABLE ups ADD COLUMN cat TEXT DEFAULT ''")
        if "star" not in cols:
            self.conn.execute("ALTER TABLE ups ADD COLUMN star INTEGER DEFAULT 0")
        # manual=1 表示这一条是手动改过的，重跑分类时不许覆盖
        if "manual" not in cols:
            self.conn.execute("ALTER TABLE ups ADD COLUMN manual INTEGER DEFAULT 0")
        # 往这个人更早的投稿里翻时，翻到哪一页了。
        # 不记的话每次都得从最新一页重翻，翻到 2020 年要重跑上百次请求。
        if "dyn_offset" not in cols:
            self.conn.execute("ALTER TABLE ups ADD COLUMN dyn_offset TEXT DEFAULT ''")
        if "dyn_done" not in cols:
            self.conn.execute("ALTER TABLE ups ADD COLUMN dyn_done INTEGER DEFAULT 0")
        # 二级分类。目前只有「生活」用上了，别的类留空。
        if "subcat" not in cols:
            self.conn.execute("ALTER TABLE ups ADD COLUMN subcat TEXT DEFAULT ''")
        # 播放记录要把标题封面一起存下来 —— 推荐位的视频不在 videos 表里，
        # 只存 bvid 的话「继续看」里就会是一排空白卡
        pcols = {r[1] for r in self.conn.execute("PRAGMA table_info(progress)")}
        if "title" not in pcols:
            self.conn.execute("ALTER TABLE progress ADD COLUMN title TEXT DEFAULT ''")
        if "cover" not in pcols:
            self.conn.execute("ALTER TABLE progress ADD COLUMN cover TEXT DEFAULT ''")
        if "upname" not in pcols:
            self.conn.execute("ALTER TABLE progress ADD COLUMN upname TEXT DEFAULT ''")

    def _exec(self, sql, args=()):
        with self._lock:
            cur = self.conn.execute(sql, args)
            self.conn.commit()
            return cur

    def _query(self, sql, args=()):
        with self._lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    # ---- UP ----
    def upsert_up(self, mid, name, face, group_id=None):
        row = self._query("SELECT groups FROM ups WHERE mid=?", (mid,))
        groups = row[0]["groups"] if row else ""
        gl = [g for g in groups.split(",") if g]
        if group_id is not None and str(group_id) not in gl:
            gl.append(str(group_id))
        gs = "," + ",".join(gl) + "," if gl else ""
        self._exec(
            "INSERT INTO ups(mid,name,face,groups) VALUES(?,?,?,?) "
            "ON CONFLICT(mid) DO UPDATE SET name=excluded.name, face=excluded.face, groups=excluded.groups",
            (mid, name, face, gs))

    def add_up_group(self, mid, tagid):
        row = self._query("SELECT groups FROM ups WHERE mid=?", (mid,))
        if not row:
            return
        gl = [g for g in row[0]["groups"].split(",") if g]
        if str(tagid) not in gl:
            gl.append(str(tagid))
            self._exec("UPDATE ups SET groups=? WHERE mid=?",
                       ("," + ",".join(gl) + ",", mid))

    def up_count(self):
        return self._query("SELECT COUNT(*) c FROM ups")[0]["c"]

    def known_mids(self):
        return {r["mid"] for r in self._query("SELECT mid FROM ups")}

    def video_bvids(self):
        return {r["bvid"] for r in self._query("SELECT bvid FROM videos")}

    def get_up(self, mid):
        r = self._query("SELECT * FROM ups WHERE mid=?", (mid,))
        return r[0] if r else None

    # ---- 视频 ----
    def save_video(self, v):
        cur = self._exec(
            "INSERT INTO videos(bvid,mid,pub_ts,title,cover,duration,play,type_name,seen_ts) "
            "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(bvid) DO NOTHING",
            (v["bvid"], v["mid"], v["pub_ts"], v["title"], v["cover"],
             v.get("duration", ""), v.get("play", 0), v.get("type_name", ""), int(time.time())))
        if cur.rowcount:
            self._exec("UPDATE ups SET last_ts=MAX(last_ts,?) WHERE mid=?", (v["pub_ts"], v["mid"]))
            return True
        return False

    def has_video(self, bvid):
        return bool(self._query("SELECT 1 FROM videos WHERE bvid=?", (bvid,)))

    def video_count(self):
        return self._query("SELECT COUNT(*) c FROM videos")[0]["c"]

    def list_videos(self, tagid=None, since_ts=None, limit=800, cat=None, star=False,
                    sub=None):
        sql = ("SELECT v.*, u.name, u.face, u.groups, u.cat, u.star, "
               "COALESCE(u.subcat,'') subcat FROM videos v "
               "LEFT JOIN ups u ON u.mid=v.mid WHERE 1=1")
        args = []
        if star:
            sql += " AND u.star=1"
        elif cat is not None and cat != "":
            sql += " AND u.cat=?"
            args.append(cat)
            if sub:
                sql += " AND COALESCE(u.subcat,'')=?"
                args.append(sub)
        elif tagid is not None and tagid != "":
            sql += " AND u.groups LIKE ?"
            args.append(f"%,{tagid},%")
        if since_ts:
            sql += " AND v.pub_ts>=?"
            args.append(since_ts)
        sql += " ORDER BY v.pub_ts DESC LIMIT ?"
        args.append(limit)
        return self._query(sql, args)

    def list_ups(self, tagid=None, cat=None, star=False, sub=None):
        sql = ("SELECT u.*, COUNT(v.bvid) cnt, MAX(v.pub_ts) last_pub, "
               "(SELECT title FROM videos WHERE mid=u.mid ORDER BY pub_ts DESC LIMIT 1) last_title "
               "FROM ups u LEFT JOIN videos v ON v.mid=u.mid WHERE 1=1")
        args = []
        if star:
            sql += " AND u.star=1"
        elif cat is not None and cat != "":
            sql += " AND u.cat=?"
            args.append(cat)
            if sub:
                sql += " AND COALESCE(u.subcat,'')=?"
                args.append(sub)
        elif tagid is not None and tagid != "":
            sql += " AND u.groups LIKE ?"
            args.append(f"%,{tagid},%")
        sql += " GROUP BY u.mid ORDER BY (last_pub IS NULL), last_pub DESC"
        return self._query(sql, args)

    # ---- 分类 ----
    def set_cats(self, mapping):
        """mapping: {mid: 类别} 或 {mid: {"cat":..,"sub":..}}。
        手动改过的人跳过，别把人的判断冲掉。"""
        rows = []
        for m, v in mapping.items():
            if isinstance(v, dict):
                rows.append((v.get("cat") or "", v.get("sub") or "", int(m)))
            else:
                rows.append((v or "", "", int(m)))
        with self._lock:
            self.conn.executemany(
                "UPDATE ups SET cat=?, subcat=? WHERE mid=? AND COALESCE(manual,0)=0",
                rows)
            self.conn.commit()

    def set_up_cat(self, mid, cat, manual=True, sub=""):
        """手动指定某个 UP 的分类（manual=0 时是"恢复自动"）。"""
        return self._exec("UPDATE ups SET cat=?, subcat=?, manual=? WHERE mid=?",
                          (cat or "", sub or "", 1 if manual else 0, int(mid))).rowcount

    def get_up_cat(self, mid):
        r = self._query(
            "SELECT cat, COALESCE(subcat,'') sub, COALESCE(manual,0) manual "
            "FROM ups WHERE mid=?", (int(mid),))
        return r[0] if r else None

    def manual_cats(self):
        return {r["mid"]: r["cat"] for r in self._query(
            "SELECT mid, cat FROM ups WHERE COALESCE(manual,0)=1")}

    def cat_counts(self):
        return {r["cat"] or "": r["c"] for r in self._query(
            "SELECT cat, COUNT(*) c FROM ups GROUP BY cat")}

    def sub_counts(self):
        """{大类: {子类: 人数}}，侧栏展开时用。"""
        out = {}
        for r in self._query(
                "SELECT cat, COALESCE(subcat,'') sub, COUNT(*) c FROM ups "
                "GROUP BY cat, sub"):
            out.setdefault(r["cat"] or "", {})[r["sub"]] = r["c"]
        return out

    def fill_missing_sub(self, fallback=None):
        """有二级分类的大类里，subcat 还是空的补一支。

        早先手动改过分类的人、或者重跑分类时被跳过的那种，subcat 会留空；
        不补的话它只在「全部」里出现，点任何一个子标签都找不到，看着像丢了。
        fallback: {"life": "daily", "film": "jie"}
        """
        n = 0
        for cat, sub in (fallback or {}).items():
            n += self._exec(
                "UPDATE ups SET subcat=? WHERE cat=? AND COALESCE(subcat,'')=''",
                (sub, cat)).rowcount
        return n

    def set_stars(self, mids):
        with self._lock:
            self.conn.execute("UPDATE ups SET star=0")
            self.conn.executemany("UPDATE ups SET star=1 WHERE mid=?", [(int(m),) for m in mids])
            self.conn.commit()

    def max_seen_ts(self):
        r = self._query("SELECT MAX(seen_ts) t FROM videos")
        return r[0]["t"] if r else 0

    def activity(self, since_ts):
        """这段时间里有多少条更新，来自多少个 UP。首页那行抬头要用"""
        r = self._query("SELECT COUNT(*) v, COUNT(DISTINCT mid) u FROM videos WHERE pub_ts>=?",
                        (since_ts,))
        return {"videos": r[0]["v"] or 0, "ups": r[0]["u"] or 0}

    def cat_activity(self, since_ts):
        """每类里，这段时间有更新的 UP 人数"""
        return {r["cat"] or "": r["c"] for r in self._query(
            "SELECT u.cat cat, COUNT(DISTINCT u.mid) c FROM ups u "
            "JOIN videos v ON v.mid=u.mid AND v.pub_ts>=? GROUP BY u.cat", (since_ts,))}

    def videos_by_mid(self, mid, limit=30):
        return self._query(
            "SELECT v.*, u.name, u.face FROM videos v LEFT JOIN ups u ON u.mid=v.mid "
            "WHERE v.mid=? ORDER BY v.pub_ts DESC LIMIT ?", (mid, limit))

    def count_by_mid(self, mid):
        return self._query("SELECT COUNT(*) c FROM videos WHERE mid=?", (mid,))[0]["c"]

    def span_by_mid(self, mid):
        """这个人在库里的时间跨度，用来在页面上写「从 X 年到 Y 年，共 N 条」"""
        r = self._query(
            "SELECT COUNT(*) c, MIN(pub_ts) lo, MAX(pub_ts) hi FROM videos WHERE mid=?", (mid,))
        return r[0] if r else {"c": 0, "lo": 0, "hi": 0}

    def set_dyn_cursor(self, mid, offset, done=0):
        self._exec("UPDATE ups SET dyn_offset=?, dyn_done=? WHERE mid=?",
                   (str(offset or ""), 1 if done else 0, int(mid)))

    def dyn_cursor(self, mid):
        r = self._query("SELECT dyn_offset, dyn_done FROM ups WHERE mid=?", (int(mid),))
        if not r:
            return {"offset": "", "done": 0}
        return {"offset": r[0].get("dyn_offset") or "", "done": r[0].get("dyn_done") or 0}

    def empty_ups(self, limit=100):
        """关注了，但库里一条投稿都没有的人。

        dyn_done=1 的排掉 —— 那种是翻到底确认了确实没投稿，再翻也是白跑。
        """
        return self._query(
            "SELECT u.mid, u.name FROM ups u LEFT JOIN videos v ON v.mid=u.mid "
            "WHERE v.bvid IS NULL AND COALESCE(u.dyn_done,0)=0 "
            "ORDER BY u.mid LIMIT ?", (int(limit),))

    def empty_count(self):
        return self._query(
            "SELECT COUNT(*) c FROM ups u LEFT JOIN videos v ON v.mid=u.mid "
            "WHERE v.bvid IS NULL AND COALESCE(u.dyn_done,0)=0")[0]["c"]

    # ---- 进度 ----
    def set_progress(self, bvid, pos, dur, title=None, cover=None, upname=None):
        self._exec(
            "INSERT INTO progress(bvid,pos,dur,updated,title,cover,upname) VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(bvid) DO UPDATE SET pos=excluded.pos, dur=excluded.dur, "
            "updated=excluded.updated, "
            "title=CASE WHEN excluded.title<>'' THEN excluded.title ELSE progress.title END, "
            "cover=CASE WHEN excluded.cover<>'' THEN excluded.cover ELSE progress.cover END, "
            "upname=CASE WHEN excluded.upname<>'' THEN excluded.upname ELSE progress.upname END",
            (bvid, pos, dur, int(time.time()), title or "", cover or "", upname or ""))

    def recent(self, limit=40, min_pos=5):
        """看过的：优先用库里视频的标题封面，推荐位来的用 progress 里存的"""
        return self._query(
            "SELECT p.bvid, p.pos, p.dur, p.updated, "
            "  COALESCE(NULLIF(v.title,''), p.title) title, "
            "  COALESCE(NULLIF(v.cover,''), p.cover) cover, "
            "  COALESCE(NULLIF(u.name,''), p.upname) name, "
            "  v.duration, v.pub_ts, v.play, v.mid, u.face "
            "FROM progress p "
            "LEFT JOIN videos v ON v.bvid=p.bvid "
            "LEFT JOIN ups u ON u.mid=v.mid "
            "WHERE p.pos>? AND p.dur>0 ORDER BY p.updated DESC LIMIT ?", (min_pos, limit))

    def get_progress(self, bvid):
        r = self._query("SELECT * FROM progress WHERE bvid=?", (bvid,))
        return r[0] if r else None

    def all_progress(self):
        return {r["bvid"]: r for r in self._query("SELECT * FROM progress")}

    # ---- meta ----
    def set_meta(self, k, v):
        if not isinstance(v, str):
            v = json.dumps(v, ensure_ascii=False)
        self._exec("INSERT INTO meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))

    def get_meta(self, k, default=None):
        r = self._query("SELECT v FROM meta WHERE k=?", (k,))
        if not r:
            return default
        try:
            return json.loads(r[0]["v"])
        except Exception:
            return r[0]["v"]
