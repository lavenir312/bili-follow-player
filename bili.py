# -*- coding: utf-8 -*-
"""B 站接口封装：只用标准库，带限速与风控退避。

踩过的坑（别改回去）：
- x/player/wbi/playurl 对非浏览器指纹一律 412，必须走老接口 x/player/playurl
- x/space/arc/search（UP 投稿列表）对外部请求全封（412 / -799），所以不用它，
  数据源统一走关注动态流 feed/all，靠翻页延展时间范围
"""
import json
import random
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
import http.cookiejar

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


class BiliError(Exception):
    pass


class Bili:
    def __init__(self, sessdata, interval=1.0):
        self.sessdata = sessdata
        self.interval = interval
        self._lock = threading.Lock()
        self._last = 0.0
        self.cj = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cj))
        self._boot()

    # ---------- 底层 ----------
    def _cookie(self, name, value):
        self.cj.set_cookie(http.cookiejar.Cookie(
            0, name, value, None, False, ".bilibili.com", True, False,
            "/", True, False, None, False, None, None, {}))

    def _boot(self):
        self._cookie("SESSDATA", self.sessdata)
        try:
            d = self._raw("https://api.bilibili.com/x/frontend/finger/spi", "https://www.bilibili.com/")
            self._cookie("buvid3", d["data"]["b_3"])
            self._cookie("buvid4", d["data"]["b_4"])
        except Exception:
            pass
        self._cookie("buvid_fp", "%032x" % random.getrandbits(128))
        self._cookie("b_nut", str(int(time.time())))
        self._cookie("_uuid", self._uuid())
        self._cookie("b_lsid", "%08X_%X" % (random.randint(0, 0xFFFFFFFF), int(time.time() * 1000)))

    @staticmethod
    def _uuid():
        t = int(time.time() * 1000) % 100000
        s = "".join("%05d" % random.randint(0, 99999) for _ in range(5))
        return f"{s[:8]}-{s[8:12]}-{s[12:16]}-{s[16:20]}-{s[20:32]}{t}infoc"

    def _throttle(self):
        # 只在这里算好下一个可用时刻，真正 sleep 放到锁外面。
        # 以前是拿着锁睡觉，并发时后一个请求要等前一个睡完才开始排队，
        # 等待时间会一层层叠上去。
        with self._lock:
            now = time.time()
            start = max(now, self._last)
            self._last = start + self.interval + random.uniform(0, 0.2)
        wait = start - now
        if wait > 0:
            time.sleep(wait)

    def _raw(self, url, ref):
        req = urllib.request.Request(url)
        for k, v in {
            "User-Agent": UA,
            "Referer": ref,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }.items():
            req.add_header(k, v)
        with self.opener.open(req, timeout=25) as r:
            return json.loads(r.read().decode("utf-8", "ignore"))

    def _bytes(self, url, ref="https://www.bilibili.com/", retries=2):
        """要原始字节的接口（弹幕 XML 是 deflate 压的，不能当 JSON 读）"""
        last = None
        for attempt in range(retries):
            self._throttle()
            req = urllib.request.Request(url)
            for k, v in {
                "User-Agent": UA,
                "Referer": ref,
                "Accept": "*/*",
                "Accept-Language": "zh-CN,zh;q=0.9",
            }.items():
                req.add_header(k, v)
            try:
                with self.opener.open(req, timeout=25) as r:
                    return r.read()
            except urllib.error.HTTPError as e:
                last = e
                if e.code in (412, 429, 503):
                    time.sleep(2.0 * (attempt + 1))
                    continue
                raise
            except Exception as e:
                last = e
                time.sleep(1.5 * (attempt + 1))
        raise BiliError(f"请求失败: {last}")

    def danmaku_xml(self, cid):
        """老 XML 接口，一次给全片弹幕。压缩是裸 deflate（wbits=-15），
        走 Content-Encoding: deflate 但 urllib 不会替你解压。"""
        raw = self._bytes(f"https://api.bilibili.com/x/v1/dm/list.so?oid={cid}",
                          "https://www.bilibili.com/")
        if raw[:1] in (b"<",):
            return raw.decode("utf-8", "ignore")
        try:
            return zlib.decompress(raw, -15).decode("utf-8", "ignore")
        except Exception:
            try:
                return zlib.decompress(raw).decode("utf-8", "ignore")
            except Exception:
                return raw.decode("utf-8", "ignore")

    def get(self, url, ref="https://www.bilibili.com/", retries=3):
        last = None
        for attempt in range(retries):
            self._throttle()
            try:
                d = self._raw(url, ref)
                code = d.get("code", 0)
                if code in (-352, -412, -799, -509):
                    raise BiliError(f"风控 {code}")
                return d
            except urllib.error.HTTPError as e:
                last = e
                if e.code in (412, 429, 503):
                    time.sleep(2.5 * (attempt + 1))
                    continue
                raise
            except Exception as e:
                last = e
                time.sleep(2.0 * (attempt + 1))
        raise BiliError(f"请求失败: {last}")

    # ---------- 业务 ----------
    def nav(self):
        d = self.get("https://api.bilibili.com/x/web-interface/nav")
        if not d.get("data", {}).get("isLogin"):
            raise BiliError("SESSDATA 无效或已过期")
        return {
            "mid": d["data"]["mid"],
            "uname": d["data"]["uname"],
            "face": d["data"].get("face", ""),
            "vip": d["data"].get("vipStatus", 0),
        }

    def tags(self):
        d = self.get("https://api.bilibili.com/x/relation/tags", "https://space.bilibili.com/")
        return d.get("data") or []

    def tag_members(self, tagid, page_size=50):
        out, pn = [], 1
        while True:
            d = self.get(
                f"https://api.bilibili.com/x/relation/tag?tagid={tagid}&pn={pn}&ps={page_size}",
                "https://space.bilibili.com/")
            if d.get("code") != 0:
                break
            batch = d.get("data") or []
            out.extend(batch)
            if len(batch) < page_size:
                break
            pn += 1
            if pn > 40:
                break
        return out

    def followings(self, mid, page_size=50):
        out, pn = [], 1
        while True:
            d = self.get(
                f"https://api.bilibili.com/x/relation/followings?vmid={mid}&pn={pn}&ps={page_size}&order=desc",
                "https://space.bilibili.com/")
            if d.get("code") != 0:
                break
            batch = (d.get("data") or {}).get("list") or []
            out.extend(batch)
            if len(batch) < page_size:
                break
            pn += 1
            if pn > 40:
                break
        return out

    def feed_video(self, page=1, offset=""):
        url = f"https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/all?type=video&page={page}"
        if offset:
            url += "&offset=" + urllib.parse.quote(offset)
        d = self.get(url, "https://t.bilibili.com/")
        if d.get("code") != 0:
            raise BiliError(f"动态流错误 {d.get('code')}")
        data = d.get("data") or {}
        rows = []
        for it in data.get("items") or []:
            try:
                major = it["modules"]["module_dynamic"]["major"]["archive"]
                author = it["modules"]["module_author"]
                stat = it.get("modules", {}).get("module_stat", {}).get("like", {})
                rows.append({
                    "bvid": major["bvid"],
                    "title": major.get("title", ""),
                    "cover": major.get("cover", ""),
                    "duration": major.get("duration_text", ""),
                    "mid": author.get("mid"),
                    "name": author.get("name", ""),
                    "face": author.get("face", ""),
                    "pub_ts": int(float(author.get("pub_ts") or 0)),
                    "play": stat.get("count", 0),
                    "type_name": major.get("desc", ""),
                })
            except Exception:
                continue
        return rows, (data.get("offset") or ""), bool(data.get("has_more"))

    def view(self, bvid):
        d = self.get(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}")
        if d.get("code") != 0:
            raise BiliError(f"视频信息错误 {d.get('code')}")
        v = d["data"]
        return {
            "bvid": v["bvid"],
            "aid": v.get("aid"),
            "cid": v["cid"],
            "title": v.get("title"),
            "desc": (v.get("desc") or "")[:400],
            "duration": v.get("duration"),
            "pubdate": v.get("pubdate"),
            "owner": v.get("owner", {}),
            "cover": v.get("pic"),
            "pages": [{"cid": p["cid"], "page": p["page"], "part": p.get("part", "")} for p in v.get("pages", [])],
            "stat": v.get("stat", {}),
        }

    def videoshot(self, bvid, cid):
        """进度条悬停预览图。返回一张 10x10 的精灵图和每个格子的时间点。

        不是每片都有，没有的时候 index 是空的，前端自己退化成只显示时间。
        """
        d = self.get(f"https://api.bilibili.com/x/player/videoshot?bvid={bvid}&cid={cid}&index=1",
                     f"https://www.bilibili.com/video/{bvid}")
        if d.get("code") != 0:
            return None
        v = d.get("data") or {}
        img = (v.get("image") or [""])[0]
        if img.startswith("//"):
            img = "https:" + img
        idx = v.get("index") or []
        if not img or not idx:
            return None
        return {
            "img": img,
            "xlen": int(v.get("img_x_len") or 10),
            "ylen": int(v.get("img_y_len") or 10),
            "w": int(v.get("img_x_size") or 160),
            "h": int(v.get("img_y_size") or 90),
            "index": idx[:240],
        }

    def replies(self, aid, pn=1, sort=1):
        """评论。sort 1=按时间 2=按热度。走老接口，不需要 wbi 签名。"""
        url = (f"https://api.bilibili.com/x/v2/reply?type=1&oid={aid}&pn={pn}"
               f"&ps=20&sort={sort}&nohot=0")
        d = self.get(url, "https://www.bilibili.com/")
        if d.get("code") != 0:
            raise BiliError(f"评论接口错误 {d.get('code')}")
        data = d.get("data") or {}
        page = data.get("page") or {}
        rows = []

        def one(r, is_sub=False):
            mem = r.get("member") or {}
            cont = r.get("content") or {}
            return {
                "rpid": r.get("rpid_str") or str(r.get("rpid")),
                "uname": mem.get("uname", ""),
                "face": (mem.get("avatar") or "").replace("http://", "https://"),
                "mid": mem.get("mid", ""),
                "text": cont.get("message", ""),
                "like": r.get("like", 0),
                "rcount": r.get("rcount", 0),
                "ctime": r.get("ctime", 0),
                "sub": is_sub,
            }

        if data.get("top"):
            top = data["top"]
            if isinstance(top, dict) and (top.get("content") or {}).get("message"):
                x = one(top)
                x["top"] = True
                rows.append(x)
        for r in data.get("replies") or []:
            x = one(r)
            kids = []
            for k in (r.get("replies") or [])[:3]:
                kids.append(one(k, True))
            x["kids"] = kids
            rows.append(x)
        return {
            "items": rows,
            "count": page.get("count", 0),
            "page": page.get("num", pn),
            "size": page.get("size", 20),
        }

    def related(self, bvid):
        d = self.get(f"https://api.bilibili.com/x/web-interface/archive/related?bvid={bvid}",
                     f"https://www.bilibili.com/video/{bvid}")
        out = []
        for it in (d.get("data") or [])[:24]:
            out.append({
                "bvid": it.get("bvid"),
                "title": it.get("title", ""),
                "cover": it.get("pic", ""),
                "name": ((it.get("owner") or {}).get("name") or ""),
                "face": ((it.get("owner") or {}).get("face") or ""),
                "mid": ((it.get("owner") or {}).get("mid") or ""),
                "play": ((it.get("stat") or {}).get("view") or 0),
                "duration": it.get("duration"),
                "pub_ts": it.get("pubdate"),
            })
        return [x for x in out if x["bvid"]]

    def online(self, bvid, cid):
        d = self.get(f"https://api.bilibili.com/x/player/online/total?bvid={bvid}&cid={cid}",
                     f"https://www.bilibili.com/video/{bvid}")
        if d.get("code") != 0:
            return None
        return ((d.get("data") or {}).get("total") or "")

    def search(self, keyword, page=1):
        """全站搜索视频。这个接口不用 wbi，能用。"""
        url = ("https://api.bilibili.com/x/web-interface/search/type?search_type=video"
               "&keyword=" + urllib.parse.quote(keyword) + f"&page={page}&order=totalrank")
        d = self.get(url, "https://search.bilibili.com/")
        if d.get("code") != 0:
            raise BiliError(f"搜索失败 {d.get('code')}")
        data = d.get("data") or {}
        out = []
        for it in data.get("result") or []:
            if it.get("type") and it.get("type") != "video":
                continue
            bv = it.get("bvid") or ""
            if not bv:
                continue
            out.append({
                "bvid": bv,
                "title": re.sub(r"<[^>]+>", "", it.get("title") or ""),
                "cover": ("https:" + it["pic"]) if (it.get("pic") or "").startswith("//") else it.get("pic", ""),
                "name": it.get("author", ""),
                "face": "",
                "mid": it.get("mid", ""),
                "play": it.get("play", 0),
                "duration": it.get("duration", ""),
                "pub_ts": it.get("pubdate", 0),
                "desc": re.sub(r"<[^>]+>", "", it.get("description") or "")[:80],
            })
        return {"items": out, "pages": data.get("numPages", 1), "total": data.get("numResults", 0)}

    def playurl(self, bvid, cid, qn=80):
        url = (f"https://api.bilibili.com/x/player/playurl?bvid={bvid}&cid={cid}"
               f"&qn={qn}&fnval=16&fnver=0&fourk=1")
        d = self.get(url, f"https://www.bilibili.com/video/{bvid}")
        if d.get("code") != 0:
            raise BiliError(f"取流失败 {d.get('code')} {d.get('message')}")
        data = d["data"]
        if not data.get("dash"):
            raise BiliError("该视频没有 DASH 流（可能是仅限 APP 或付费内容）")
        return data

    def space_dynamics(self, mid, offset=""):
        """往一个人的更早投稿里翻。

        本来该用 x/space/arc/search，但那个接口现在对非浏览器指纹一律 412
        （带 order 参数更是必挂），wbi 版也一样。空间动态流这条不用签名，
        能把一个人的投稿一直翻到注册那天，所以走它。

        offset 是上一页返回的游标，空串表示从最新一条开始。
        """
        url = ("https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space"
               f"?host_mid={int(mid)}&offset={offset or ''}")
        d = self.get(url)
        if d.get("code") != 0:
            raise BiliError(f"翻不动 {d.get('code')} {d.get('message')}")
        data = d.get("data") or {}
        out = []
        for it in data.get("items") or []:
            if it.get("type") != "DYNAMIC_TYPE_AV":
                continue                     # 图文、转发这些没有视频，跳过
            mods = it.get("modules") or {}
            major = (mods.get("module_dynamic") or {}).get("major") or {}
            arc = major.get("archive") or {}
            bv = arc.get("bvid") or ""
            if not bv:
                continue
            cover = arc.get("cover") or ""
            if cover.startswith("//"):
                cover = "https:" + cover
            cover = cover.replace("http://", "https://")
            stat = arc.get("stat") or {}
            out.append({
                "bvid": bv,
                "title": arc.get("title") or "",
                "cover": cover,
                "pub_ts": int((mods.get("module_author") or {}).get("pub_ts") or 0),
                "duration": arc.get("duration_text") or "",
                "play": play_num(stat.get("play")),
            })
        return {
            "items": out,
            "offset": str(data.get("offset") or ""),
            "has_more": bool(data.get("has_more")),
        }


def play_num(s):
    """B 站把播放量写成「88.2万」，库里的 play 是整数，转一下"""
    s = str(s or "").strip()
    if not s:
        return 0
    try:
        if s.endswith("亿"):
            return int(float(s[:-1]) * 100000000)
        if s.endswith("万"):
            return int(float(s[:-1]) * 10000)
        return int(float(s))
    except ValueError:
        return 0
