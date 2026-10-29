'use strict';

const $ = (s) => document.querySelector(s);
const el = (tag, cls, html) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (html !== undefined) n.innerHTML = html;
  return n;
};

const state = {
  user: null,
  cats: [],
  stats: {},
  view: 'home',
  cat: '',
  sub: '',
  star: false,
  stars: 0,
  catName: '全部关注',
  days: 7,
  q: '',
  limit: 2,
  filterMid: null,
  expanded: new Set(),
  page: 1,
  videos: [],
  ups: [],
  upsSort: 'new',
  up: null,
  upMid: 0,
  upFetching: false,
  fill: {},
  recent: [],
  home: null,
  homeRecOffset: 0,
  syncing: false,
  recOffset: 0,
  recs: [],
  search: null,
  searchPage: 1,
};

const PAGE = 90;

/* ---------------- 小工具 ---------------- */
function ago(ts) {
  if (!ts) return '';
  const d = Math.floor(Date.now() / 1000 - ts);
  if (d < 60) return '刚刚';
  if (d < 3600) return Math.floor(d / 60) + ' 分钟前';
  if (d < 86400) return Math.floor(d / 3600) + ' 小时前';
  if (d < 86400 * 30) return Math.floor(d / 86400) + ' 天前';
  const dt = new Date(ts * 1000);
  // 跨年的老视频只写「3月5日」会让人当成今年，把年份带上
  if (d >= 86400 * 365) return `${dt.getFullYear()}年${dt.getMonth() + 1}月`;
  return `${dt.getMonth() + 1}月${dt.getDate()}日`;
}

function ym(ts) {
  if (!ts) return '';
  const d = new Date(ts * 1000);
  return `${d.getFullYear()}.${String(d.getMonth() + 1).padStart(2, '0')}`;
}

function fmtNum(n) {
  if (!n && n !== 0) return '';
  if (n >= 100000000) return (n / 100000000).toFixed(1) + '亿';
  if (n >= 10000) return (n / 10000).toFixed(1) + '万';
  return String(n);
}

function fmtTime(s) {
  if (!isFinite(s) || s < 0) s = 0;
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = Math.floor(s % 60);
  const mm = String(m).padStart(h ? 2 : 1, '0');
  return (h ? h + ':' : '') + mm + ':' + String(sec).padStart(2, '0');
}

function esc(s) {
  return String(s || '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

/** B 站图床对 localhost 的引用不太客气。先按老办法不带 Referer 直连，
    挂了再让本地服务补 Referer 转一次。 */
function imgErr(img) {
  let raw = img.dataset.raw || img.getAttribute('src') || '';
  if (raw.startsWith('//')) raw = 'https:' + raw;
  if (!raw || img.dataset.proxied || raw.startsWith('/api/')) {
    img.style.visibility = 'hidden';
    return;
  }
  img.dataset.proxied = '1';
  img.src = '/api/img?u=' + encodeURIComponent(raw);
}
window.imgErr = imgErr;

function imgProxy(u) {
  if (!u) return '';
  if (u.startsWith('//')) u = 'https:' + u;
  return '/api/img?u=' + encodeURIComponent(u);
}

async function api(url, opt) {
  const r = await fetch(url, opt);
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

const LS = {
  get(k, d) {
    try {
      const v = localStorage.getItem('bp.' + k);
      return v === null ? d : JSON.parse(v);
    } catch (e) { return d; }
  },
  set(k, v) {
    try { localStorage.setItem('bp.' + k, JSON.stringify(v)); } catch (e) {}
  },
};

let toastTimer = null;
function toast(msg) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.remove('hidden');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add('hidden'), 2400);
}

/* ================= 设置 =================
   存在服务端 data/settings.json，换窗口、换机器都是同一套。
   这里先放一份兜底默认值，接口回来之后再覆盖。 */
const SET = {
  player: {
    rate: LS.get('rate', 1), vol: 1, quality: 'highest', autoplay_next: true,
    next_wait: 5, skip_intro: 0, skip_outro: 0, remember_pos: true,
    volume: LS.get('vol', 1), fit: 'contain', seek_step: 5, seek_step_big: 30,
    wheel_volume: true, dblclick_full: true,
  },
  danmaku: {
    on: LS.get('dm', true), opacity: 0.92, scale: 1, area: 0.76, duration: 9,
    bold: true, stroke: 1, scroll: true, top: true, bottom: true,
    merge: true, block: [],
  },
  ui: { page_size: 2, days: 7 },
};

function setGet(path) {
  return path.split('.').reduce((o, k) => (o == null ? o : o[k]), SET);
}

function setPut(path, val) {
  const parts = path.split('.');
  const last = parts.pop();
  const box = parts.reduce((o, k) => (o[k] = o[k] || {}, o[k]), SET);
  box[last] = val;
  scheduleSave();
}

let saveTimer = null;
function scheduleSave() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    fetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(SET),
    }).catch(() => {});
  }, 500);
}

async function loadSettings() {
  try {
    const d = await api('/api/settings');
    for (const k of ['player', 'danmaku', 'ui']) {
      if (d[k]) Object.assign(SET[k], d[k]);
    }
  } catch (e) {}
  // 屏蔽词按行存，正常
  if (!Array.isArray(SET.danmaku.block)) SET.danmaku.block = [];
}

/* ---- 设置面板 ---- */
const SET_SCHEMA = {
  play: [
    { g: '画面' },
    { k: 'player.fit', t: 'seg', lb: '画面缩放',
      d: '上下有黑边可以试「铺满」，但会切掉两边',
      opts: [['contain', '适应'], ['cover', '铺满'], ['fill', '拉伸']] },
    { k: 'player.quality', t: 'seg', lb: '默认清晰度', d: '「最高」= 你这个号能拿到的最高档',
      opts: [['highest', '最高'], ['1080', '1080P'], ['720', '720P'], ['480', '480P'], ['auto', '自动']] },
    { k: 'player.rate', t: 'seg', lb: '默认倍速',
      opts: [[0.75, '0.75×'], [1, '1×'], [1.25, '1.25×'], [1.5, '1.5×'], [2, '2×']] },
    { g: '连播' },
    { k: 'player.autoplay_next', t: 'sw', lb: '播完自动下一个', d: '关掉就停在结束画面，不弹提示条' },
    { k: 'player.next_wait', t: 'rng', lb: '倒数几秒', min: 0, max: 15, step: 1,
      fmt: (v) => (v ? v + ' 秒' : '立刻') },
    { g: '进度' },
    { k: 'player.remember_pos', t: 'sw', lb: '记住看到哪儿', d: '下次打开接着播' },
    { k: 'player.skip_intro', t: 'rng', lb: '自动跳过片头', min: 0, max: 120, step: 5,
      fmt: (v) => (v ? v + ' 秒' : '不跳') },
    { k: 'player.skip_outro', t: 'rng', lb: '片尾提前结束', min: 0, max: 120, step: 5,
      fmt: (v) => (v ? v + ' 秒' : '不跳') },
    { g: '操作' },
    { k: 'player.seek_step', t: 'rng', lb: '快进一步', min: 3, max: 20, step: 1,
      fmt: (v) => v + ' 秒' },
    { k: 'player.wheel_volume', t: 'sw', lb: '滚轮调音量' },
    { k: 'player.dblclick_full', t: 'sw', lb: '双击画面全屏' },
  ],
  dm: [
    { g: '显示' },
    { k: 'danmaku.on', t: 'sw', lb: '显示弹幕' },
    { k: 'danmaku.opacity', t: 'rng', lb: '不透明度', min: 0.2, max: 1, step: 0.02,
      fmt: (v) => Math.round(v * 100) + '%' },
    { k: 'danmaku.scale', t: 'rng', lb: '字号', min: 0.6, max: 1.8, step: 0.1,
      fmt: (v) => Math.round(v * 100) + '%' },
    { k: 'danmaku.area', t: 'rng', lb: '占用画面高度', d: '越小越不挡脸，但同一时刻能显示的条数也少',
      min: 0.25, max: 1, step: 0.05, fmt: (v) => Math.round(v * 100) + '%' },
    { k: 'danmaku.duration', t: 'rng', lb: '滚动速度', d: '一条弹幕横穿屏幕用的秒数',
      min: 4, max: 15, step: 0.5, fmt: (v) => v + ' 秒' },
    { g: '外观' },
    { k: 'danmaku.bold', t: 'sw', lb: '加粗' },
    { k: 'danmaku.stroke', t: 'seg', lb: '描边',
      opts: [[0, '无'], [0.6, '细'], [1, '标准'], [1.7, '粗']] },
    { g: '类型' },
    { k: 'danmaku.scroll', t: 'sw', lb: '滚动弹幕' },
    { k: 'danmaku.top', t: 'sw', lb: '顶部固定' },
    { k: 'danmaku.bottom', t: 'sw', lb: '底部固定' },
    { k: 'danmaku.merge', t: 'sw', lb: '合并重复', d: '同一秒里一模一样的只留一条' },
    { g: '屏蔽' },
    { k: 'danmaku.block', t: 'words', lb: '屏蔽词',
      d: '一行一个，命中整条不显示；支持简单正则' },
  ],
};

const KEYS_HELP = [
  ['空格 / K', '播放 / 暂停'],
  ['← →', '快退 / 快进'],
  ['J / L', '快退 / 快进 10 秒'],
  ['↑ ↓', '音量'],
  ['Shift ← →', '大步快进'],
  ['F', '全屏'],
  ['C', '弹幕开关'],
  ['B', '去 B 站网页'],
  ['P', '画中画'],
  ['S', '截图'],
  ['Tab', '评论 / 推荐'],
  ['Shift , .', '倍速 -/+'],
  ['M', '静音'],
  ['Esc', '关闭播放器'],
  ['?', '打开这个设置面板'],
];

function renderSetBody(tab) {
  const box = $('#set-body');
  box.innerHTML = '';
  if (tab === 'keys') {
    const list = el('div', 'kbd-list');
    KEYS_HELP.forEach(([k, d]) => {
      const r = el('div', 'kbd-row');
      r.innerHTML = `<kbd>${esc(k)}</kbd><span></span>`;
      r.lastChild.textContent = d;
      list.appendChild(r);
    });
    box.appendChild(list);
    const tip = el('div', 'set-hint', '播放器里点一下画面再按，输入框里打字不会触发。');
    tip.style.marginTop = '14px';
    box.appendChild(tip);
    return;
  }
  (SET_SCHEMA[tab] || []).forEach((it) => {
    if (it.g) {
      box.appendChild(el('div', 'set-group', it.g));
      return;
    }
    const row = el('div', 'set-row');
    const lb = el('div', 'lb');
    lb.innerHTML = `<span>${esc(it.lb)}</span>` + (it.d ? `<small>${esc(it.d)}</small>` : '');
    row.appendChild(lb);

    const v = setGet(it.k);
    if (it.t === 'sw') {
      const sw = el('div', 'sw' + (v ? ' on' : ''));
      sw.onclick = () => {
        const nv = !setGet(it.k);
        setPut(it.k, nv);
        sw.classList.toggle('on', nv);
        afterSet(it.k);
      };
      row.appendChild(sw);
    } else if (it.t === 'seg') {
      const seg = el('div', 'seg');
      it.opts.forEach(([val, label]) => {
        const b = el('button', String(v) === String(val) ? 'on' : '', label);
        b.onclick = () => {
          setPut(it.k, val);
          [...seg.children].forEach((x) => x.classList.remove('on'));
          b.classList.add('on');
          afterSet(it.k);
        };
        seg.appendChild(b);
      });
      row.appendChild(seg);
    } else if (it.t === 'rng') {
      const wrap = el('div', 'rng');
      const inp = document.createElement('input');
      inp.type = 'range';
      inp.min = it.min; inp.max = it.max; inp.step = it.step;
      inp.value = v;
      const out = el('span', '', it.fmt(v));
      inp.oninput = () => {
        const nv = Number(inp.value);
        setPut(it.k, nv);
        out.textContent = it.fmt(nv);
        afterSet(it.k);
      };
      wrap.appendChild(inp);
      wrap.appendChild(out);
      row.appendChild(wrap);
    } else if (it.t === 'words') {
      const box2 = el('div');
      box2.style.width = '100%';
      row.style.flexDirection = 'column';
      row.style.alignItems = 'stretch';
      const ta = document.createElement('textarea');
      ta.className = 'set-words';
      ta.placeholder = '例如：剧透\n下次一定\n前方高能';
      ta.value = (v || []).join('\n');
      let wt = null;
      ta.oninput = () => {
        clearTimeout(wt);
        wt = setTimeout(() => {
          setPut(it.k, ta.value.split('\n').map((s) => s.trim()).filter(Boolean));
          afterSet(it.k);
        }, 400);
      };
      box2.appendChild(ta);
      if (it.d) box2.appendChild(el('div', 'set-hint', it.d));
      row.appendChild(box2);
      box.appendChild(row);
      return;
    }
    box.appendChild(row);
  });
}

/* 设置改动之后要立刻生效的那几项 */
function afterSet(key) {
  const video = $('#video');
  if (key.startsWith('danmaku.')) {
    dm.on = SET.danmaku.on;
    $('#p-dm').classList.toggle('on', dm.on);
    $('#dm').classList.toggle('off', !dm.on || !dm.ready);
    dmResize();
    dmClear();
    dmReseek(video.currentTime || 0);
    if (dm.on && cur && !dm.items.length) loadDanmaku(cur.cid);
    if (key === 'danmaku.merge' || key === 'danmaku.block') applyDmFilters();
  } else if (key === 'player.fit') {
    applyFit();
  } else if (key === 'player.rate') {
    const r = Number(SET.player.rate) || 1;
    video.playbackRate = r;
    const sel = $('#p-rate');
    if (sel && [...sel.options].some((o) => o.value === String(r))) sel.value = String(r);
    LS.set('rate', r);
  }
}

function applyFit() {
  const v = $('#video');
  v.classList.remove('fit-cover', 'fit-fill');
  if (SET.player.fit === 'cover') v.classList.add('fit-cover');
  else if (SET.player.fit === 'fill') v.classList.add('fit-fill');
}

function openSet(tab) {
  $('#p-set').classList.remove('hidden');
  $('#p-side').classList.add('hidden');
  document.querySelectorAll('.stab').forEach((b) =>
    b.classList.toggle('active', b.dataset.se === (tab || 'play')));
  renderSetBody(tab || 'play');
  wake();
}


/* ---------------- 侧栏分类 ---------------- */
function renderCats() {
  const nav = $('#cats');
  nav.innerHTML = '';

  const totalUps = state.stats.ups || 0;
  const all = el('button', state.cat === '' && !state.star && state.view === 'timeline' ? 'on' : '');
  all.innerHTML = `<span class="dot"></span><span class="nm">全部关注</span><span class="num">${totalUps}</span>`;
  all.onclick = () => selectCat('', '全部关注');
  nav.appendChild(all);

  if (state.stars) {
    const st = el('button', state.star ? 'on' : '');
    st.innerHTML = `<span class="dot"></span><span class="nm">特别关注</span><span class="num">${state.stars}</span>`;
    st.onclick = () => selectCat('', '特别关注', true);
    nav.appendChild(st);
  }

  nav.appendChild(el('div', 'sep'));

  state.cats.forEach((c) => {
    if (!c.count) return;
    const b = el('button', state.cat === c.id && !state.star ? 'on' : '');
    b.innerHTML = `<span class="dot"></span><span class="nm">${esc(c.name)}</span>` +
      `<span class="num">${c.count}</span>`;
    b.title = `${c.desc}｜本周 ${c.active} 人有更新`;
    b.onclick = () => selectCat(c.id, c.name);
    nav.appendChild(b);

    // 选中一个有二级分类的大类（现在只有「生活」），就在下面铺开小标签。
    // 不另做展开箭头：选中即展开，省一次点击，也不占地方。
    if (state.cat === c.id && !state.star && c.subs && c.subs.length) {
      const box = el('div', 'subnav');
      const all = el('button', state.sub ? '' : 'on');
      all.innerHTML = `<span class="nm">全部</span><span class="num">${c.count}</span>`;
      all.onclick = () => selectCat(c.id, c.name);
      box.appendChild(all);
      c.subs.forEach((s) => {
        const sb = el('button', state.sub === s.id ? 'on' : '');
        sb.innerHTML = `<span class="nm">${esc(s.name)}</span><span class="num">${s.count}</span>`;
        sb.title = s.desc;
        sb.onclick = () => selectCat(c.id, `${c.name} · ${s.name}`, false, s.id);
        box.appendChild(sb);
      });
      nav.appendChild(box);
    }
  });
}

function selectCat(id, name, isStar, sub) {
  state.cat = id;
  state.sub = sub || '';
  state.catName = name;
  state.star = !!isStar;
  state.page = 1;
  state.filterMid = null;
  state.expanded.clear();
  state.recOffset = 0;
  if (state.view !== 'timeline') {
    state.view = 'timeline';
    document.querySelectorAll('.tab').forEach((t) =>
      t.classList.toggle('active', t.dataset.view === 'timeline'));
    syncToolbar();
  }
  renderCats();
  loadVideos();
}

function renderStats() {
  const s = state.stats;
  const t = s.last_sync ? ago(s.last_sync) + '更新' : '还没建库';
  $('#stats').innerHTML = `收录 ${s.videos || 0} 条 · ${s.ups || 0} 个 UP<br>${t}`;
}

/* ---------------- 卡片 ---------------- */
const EXT_ICON = '<svg viewBox="0 0 16 16" aria-hidden="true">' +
  '<path d="M6.2 3.4h6.4v6.4" fill="none" stroke="currentColor" stroke-width="1.7" ' +
  'stroke-linecap="round"/><path d="M12.6 3.4L6.8 9.2" fill="none" stroke="currentColor" ' +
  'stroke-width="1.7" stroke-linecap="round"/><path d="M10.6 9.8v2.4c0 .4-.3.7-.7.7H4.4c-.4 0-.7-.3-.7-.7V6.4c0-.4.3-.7.7-.7h2.4" ' +
  'fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>';

function videoCard(v, isRec, queue) {
  const c = el('div', 'c');
  const pct = Math.round((v.progress || 0) * 100);
  const tail = v.dur ? '看到 ' + fmtTime(v.pos || 0) : (v.play ? ago(v.pub_ts) : '');
  c.innerHTML = `
    <div class="thumb">
      <img loading="lazy" src="${esc(v.cover)}" data-raw="${esc(v.cover)}" onerror="imgErr(this)" referrerpolicy="no-referrer">
      ${isRec ? '<span class="tag">未关注</span>' : ''}
      ${v.duration ? `<span class="dur">${esc(v.duration)}</span>` : ''}
      <button class="ext" title="在 B 站网页打开">${EXT_ICON}</button>
      ${pct ? `<div class="prog" style="width:${pct}%"></div>` : ''}
    </div>
    <div class="cap">${esc(v.title)}</div>
    <div class="by">
      <img loading="lazy" src="${esc(v.face)}" data-raw="${esc(v.face)}" onerror="imgErr(this)" referrerpolicy="no-referrer">
      <span class="un">${esc(v.name || '未知 UP')}</span>
      <span class="sp">·</span>
      <span>${v.play ? fmtNum(v.play) + ' 播放' : ago(v.pub_ts)}</span>
      <span class="tm">${tail}</span>
    </div>`;
  c.onclick = () => openPlayer(v, { queue: queue || listForQueue() });
  const ext = c.querySelector('.ext');
  ext.onclick = (e) => {
    e.stopPropagation();
    openInBili(v.bvid);
  };
  return c;
}

/* 当前视图里能被连播的顺序 */
function listForQueue() {
  if (state.view === 'recent') return state.recent;
  if (state.view === 'up') return (state.up && state.up.videos) || [];
  return filtered();
}

/* ---------------- 首页 ---------------- */
/* 一进来先看全局：今天有谁更新、上次没看完的、各分类各来一撮。
   一屏里想看的都在，不用先去侧栏选分类。 */

function showHome() {
  $('#home').classList.remove('hidden');
  $('#list').classList.add('hidden');
  $('#recbar').classList.add('hidden');
  $('#empty').classList.add('hidden');
}

function showList() {
  $('#home').classList.add('hidden');
  $('#list').classList.remove('hidden');
  // 这条只有「按人看」用得上，别的视图先收起来
  const t = $('#ups-tip');
  if (t && state.view !== 'ups') t.classList.add('hidden');
}

function homeSec(title, sub, items, opt) {
  opt = opt || {};
  const sec = el('section', 'h-sec');
  const hd = el('div', 'h-head');
  hd.innerHTML = `<b>${esc(title)}</b><span>${esc(sub)}</span>`;
  if (opt.more) {
    const b = el('button', 'h-more');
    b.textContent = opt.moreText || '看全部';
    b.onclick = opt.more;
    hd.appendChild(b);
  }
  sec.appendChild(hd);
  const row = el('div', 'h-row');
  if (opt.rowId) row.id = opt.rowId;
  (items || []).forEach((v) => row.appendChild(videoCard(v, opt.isRec, opt.queue)));
  sec.appendChild(row);
  return sec;
}

function renderHome() {
  const d = state.home;
  if (!d) return;
  showHome();
  const box = $('#home');
  box.innerHTML = '';

  const t = d.today || {}, w = d.week || {}, st = d.stats || {};
  const hero = el('div', 'h-hero');
  const head = t.videos
    ? `近 24 小时，<b>${t.ups}</b> 个 UP 发了 <b>${t.videos}</b> 条`
    : '最近一天还没有新投稿';
  const bits = [];
  if (st.ups) bits.push(`关注 ${st.ups} 人`);
  if (st.videos) bits.push(`收录 ${st.videos} 条`);
  if (w.ups) bits.push(`本周 ${w.ups} 个 UP 有更新`);
  if (st.last_sync) bits.push(`${ago(st.last_sync)}同步`);
  hero.innerHTML = `<div class="h-hero-t">${head}</div>
    <div class="h-hero-s">${bits.join('<i>·</i>')}</div>`;
  box.appendChild(hero);

  if (!(d.sections || []).length) {
    box.appendChild(el('div', 'h-empty',
      '库里还是空的。点左下角「更新订阅」拉一次，第一次要把关注的人的动态翻一遍，得等几分钟。'));
    return;
  }

  if ((d.continue || []).length) {
    box.appendChild(homeSec('接着看', '上次没看完的，进度都还在', d.continue, {
      queue: d.continue,
    }));
  }

  (d.sections || []).forEach((s) => {
    box.appendChild(homeSec(
      s.name,
      `本周 ${s.active} 人有更新 · 关注 ${s.ups} 人`,
      s.items,
      { queue: s.items, moreText: '看全部', more: () => selectCat(s.cat, s.name) }));
  });

  if ((d.discover || []).length) {
    box.appendChild(homeSec('关注之外', '全站热门里，排掉你关注的人', d.discover, {
      isRec: true, queue: d.discover, rowId: 'h-disc',
      moreText: '换一批', more: shuffleHomeRecs,
    }));
  }

  // 首页渲染完再处理锚点，否则浏览器滚的时候元素还不存在
  if (location.hash) {
    try {
      const t = box.querySelector(location.hash);
      if (t) t.scrollIntoView({ block: 'center' });
    } catch (e) {}
  }
}

async function loadHome(fresh) {
  showHome();
  if (!state.home) {
    $('#home').innerHTML =
      '<div class="h-hero"><div class="h-hero-t" style="color:#c3c8d0">正在整理首页…</div></div>';
  }
  const y = $('#home').scrollTop;
  try {
    const d = await api('/api/home' + (fresh ? '?fresh=1' : ''));
    state.home = d;
    state.homeRecOffset = 0;
    renderHome();
    $('#home').scrollTop = y;
  } catch (e) {
    $('#home').innerHTML = '<div class="h-hero"><div class="h-hero-t">首页没加载出来</div>' +
      `<div class="h-hero-s">${esc(e.message)}</div></div>`;
  }
}

/* 点左上角 logo 回首页 */
function gotoHome() {
  if (state.view === 'home') { loadHome(true); return; }
  state.view = 'home';
  document.querySelectorAll('.tab').forEach((t) =>
    t.classList.toggle('active', t.dataset.view === 'home'));
  syncToolbar();
  loadHome();
}

async function shuffleHomeRecs() {  const next = state.homeRecOffset + 6;
  let d;
  try {
    d = await api(`/api/recs?cat=&n=6&offset=${next}`);
  } catch (e) { toast('换不了，稍后再试'); return; }
  const items = d.items || [];
  if (!items.length) { toast('就这些了，再换还是这批'); return; }
  state.homeRecOffset = next;
  state.home.discover = items;
  const row = $('#h-disc');
  if (!row) return;
  row.innerHTML = '';
  items.forEach((v) => row.appendChild(videoCard(v, true, items)));
}

/* ---------------- 时间线 ---------------- */
function filtered() {
  let list = state.videos;
  if (state.filterMid) list = list.filter((v) => v.mid === state.filterMid);
  if (state.q) {
    const q = state.q.toLowerCase();
    list = list.filter((v) => (v.title || '').toLowerCase().includes(q) ||
      (v.name || '').toLowerCase().includes(q));
  }
  return list;
}

function renderTimeline() {
  showList();
  const list = filtered();
  const box = $('#list');
  box.className = 'grid';
  box.innerHTML = '';
  $('#empty').classList.add('hidden');

  if (!list.length) {
    showEmpty();
    return;
  }

  const total = {};
  list.forEach((v) => { total[v.mid] = (total[v.mid] || 0) + 1; });

  const seen = {};
  const shown = list.slice(0, state.page * PAGE);
  shown.forEach((v) => {
    if (state.expanded.has(v.mid)) { box.appendChild(videoCard(v)); return; }
    seen[v.mid] = (seen[v.mid] || 0) + 1;
    if (seen[v.mid] <= state.limit) {
      box.appendChild(videoCard(v));
      const rest = total[v.mid] - state.limit;
      if (seen[v.mid] === state.limit && rest > 0) {
        const m = el('div', 'more');
        m.innerHTML = `<b>还有 ${rest} 条</b><span>${esc(v.name)}</span>`;
        m.onclick = (e) => { e.stopPropagation(); state.expanded.add(v.mid); renderTimeline(); };
        box.appendChild(m);
      }
    }
  });

  if (shown.length < list.length) {
    const b = el('button', 'loadmore', `加载更多（还有 ${list.length - shown.length} 条）`);
    b.onclick = () => { state.page++; renderTimeline(); };
    box.appendChild(b);
  }
}

function showNeedSetup() {
  const d = document.createElement('div');
  d.style.cssText = 'position:fixed;inset:0;z-index:9999;display:flex;'
    + 'align-items:center;justify-content:center;background:var(--bg);'
    + 'color:var(--text);text-align:center;line-height:2;font-size:14px';
  d.innerHTML = '<div>还没有连接账号，所以这里什么都没有。'
    + '<br><span style="color:var(--sub);font-size:13px">'
    + '第一次用要先填一次 SESSDATA，填完就自动开始读关注。</span><br><br>'
    + '<a href="/setup" style="display:inline-block;padding:9px 20px;border-radius:8px;'
    + 'background:var(--pink);color:#fff;text-decoration:none">去连接账号</a></div>';
  document.body.appendChild(d);
}

function showEmpty() {
  const tips = {
    '': '这个时间范围里没有内容。换个范围，或者点左下角「更新订阅」拉一次新的。',
    friend: '这一组是同学和熟人，平时基本不发投稿。',
  };
  const box = $('#empty');
  box.innerHTML = tips[state.cat] ||
    `「${esc(state.catName)}」这段时间没有更新。往下翻是这一类固定在看的人，或者把顶部时间范围调大。`;
  // 搜了东西但本地没有，就给个去全站搜的出口
  if (state.q) {
    const b = el('button', 'loadmore', `在 B 站全站搜「${state.q}」`);
    b.style.marginTop = '14px';
    b.onclick = () => doSearch(state.q, 1);
    box.appendChild(b);
  }
  box.classList.remove('hidden');
}

/* ---------------- 全站搜索 ---------------- */
async function doSearch(q, page) {
  q = (q || '').trim();
  if (!q) return;
  state.view = 'search';
  state.q = q;
  state.searchPage = page || 1;
  document.querySelectorAll('.tab').forEach((t) => t.classList.remove('active'));
  syncToolbar();
  showList();
  const box = $('#list');
  box.className = 'grid';
  box.innerHTML = '<div class="empty">正在搜…</div>';
  $('#empty').classList.add('hidden');
  try {
    const d = await api(`/api/search?q=${encodeURIComponent(q)}&page=${state.searchPage}`);
    state.search = d;
    renderSearch();
  } catch (e) {
    box.innerHTML = '';
    $('#empty').textContent = '搜索失败：' + e.message;
    $('#empty').classList.remove('hidden');
  }
}

function renderSearch() {
  const d = state.search || {};
  const items = d.items || [];
  const box = $('#list');
  box.className = 'grid';
  box.innerHTML = '';
  showList();

  const head = el('div', 'h-hero');
  head.style.gridColumn = '1 / -1';
  head.innerHTML = `<div class="h-hero-t">全站搜到 <b>${fmtNum(d.total || items.length)}</b> 条「${esc(state.q)}」</div>`
    + `<div class="h-hero-s">这些是你没关注的人，点开就能播<button id="back-follow" `
    + `style="margin-left:14px;color:var(--pink)">回到关注</button></div>`;
  box.appendChild(head);
  box.querySelector('#back-follow').onclick = () => {
    state.view = 'timeline';
    state.search = null;
    document.querySelectorAll('.tab').forEach((t) =>
      t.classList.toggle('active', t.dataset.view === 'timeline'));
    syncToolbar();
    renderCats();
    loadVideos();
  };

  if (!items.length) {
    box.appendChild(el('div', 'empty', '没搜到。换个词试试。'));
    return;
  }
  const queue = items.map((x) => ({
    bvid: x.bvid, title: x.title, cover: x.cover, name: x.name, duration: x.duration,
  }));
  items.forEach((v) => box.appendChild(videoCard(v, false, queue)));

  if ((d.pages || 1) > state.searchPage) {
    const b = el('button', 'loadmore', '换一批');
    b.onclick = () => doSearch(state.q, state.searchPage + 1);
    box.appendChild(b);
  }
}

/* ---------------- 按人看 ---------------- */
function renderUps() {
  showList();
  const box = $('#list');
  box.className = 'grid';
  box.innerHTML = '';
  $('#empty').classList.add('hidden');
  upsFillTip();

  let rows = state.ups.slice();
  if (state.q) {
    const q = state.q.toLowerCase();
    rows = rows.filter((u) => (u.name || '').toLowerCase().includes(q));
  }
  if (!rows.length) { showEmpty(); return; }

  if (state.upsSort === 'old') {
    // 越久没动静的排越前；库里一条都没有的垫底，因为不知道他到底多久没发
    rows.sort((a, b) => {
      const x = a.last_pub || 0;
      const y = b.last_pub || 0;
      if (!x !== !y) return x ? -1 : 1;
      return x - y;
    });
  } else {
    rows.sort((a, b) => (b.last_pub || 0) - (a.last_pub || 0));
  }

  rows.forEach((u) => {
    const card = el('div', 'u');
    let idleTxt = '这段时间没更新', idleCls = 'idle dead';
    if (u.days_idle === 0) { idleTxt = '今天更新'; idleCls = 'idle ok'; }
    else if (u.days_idle !== null && u.days_idle !== undefined) {
      idleTxt = `${u.days_idle} 天前`;
      idleCls = 'idle' + (u.days_idle >= 30 ? ' dead' : u.days_idle >= 10 ? ' warn' : '');
    }
    card.innerHTML = `
      <img loading="lazy" src="${esc(u.face)}" data-raw="${esc(u.face)}" onerror="imgErr(this)" referrerpolicy="no-referrer">
      <span class="un">${esc(u.name || u.mid)}</span>
      <span class="lt">${u.last_title ? esc(u.last_title) : '还没翻过他的投稿'}</span>
      <span class="ucat${u.manual ? ' man' : ''}" title="点一下改分类">${esc(catName(u.cat))}</span>
      <span class="${idleCls}">${idleTxt}${u.cnt ? ` · ${u.cnt} 条` : ''}</span>`;
    const chip = card.querySelector('.ucat');
    chip.onclick = (e) => {
      e.stopPropagation();
      openCatPop(u.mid, u.name, u.cat, u.subcat);
    };
    card.onclick = () => openUpPage(u.mid);
    box.appendChild(card);
  });
}

/* ---------------- 把从没进库的人补一补 ----------------
   关注几百人，同步只往回翻 40 页动态，排后面的人一条也抓不到。这里挨个去
   他们空间各翻一页，把最近的投稿补进来。串行跑，一次一页，别把号跑风控。 */
let fillTimer = null;

function upsFillTip() {
  const box = $('#ups-tip');
  if (!box) return;
  const n = (state.stats || {}).empty_ups || 0;
  const f = state.fill || {};
  if (!n && !f.running) {
    box.classList.add('hidden');
    box.innerHTML = '';
    return;
  }
  box.classList.remove('hidden');
  if (f.running) {
    const pct = f.total ? Math.round((f.done / f.total) * 100) : 0;
    box.innerHTML = `<span>正在补第 ${f.done}/${f.total} 个：${esc(f.message || '')}`
      + `　已补 ${f.added || 0} 条</span><i style="width:${pct}%"></i>`;
    return;
  }
  box.innerHTML = `<span>还有 <b>${n}</b> 个人库里一条都没有 —— 同步只往回翻 40 页动态，`
    + `排在后面的人抓不到他们的投稿。</span><button>补 80 个</button>`;
  box.querySelector('button').onclick = () => startFill(80);
}

async function startFill(limit) {
  try {
    const d = await api('/api/up/fill', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ limit }),
    });
    if (!d.started) { toast('已经在补了，等它跑完'); return; }
    state.fill = d.state;
    renderUps();
    pollFill();
  } catch (e) {
    toast('起不来：' + e.message);
  }
}

function pollFill() {
  clearInterval(fillTimer);
  fillTimer = setInterval(async () => {
    try {
      const s = await api('/api/state');
      state.fill = s.fill || {};
      const f = state.fill;
      if (!f.running) {
        clearInterval(fillTimer);
        toast(f.error ? '停下：' + f.error : `补进来 ${f.added || 0} 条`);
        await loadState();
        if (state.view === 'ups') renderUps();
        return;
      }
      const tip = $('.ups-tip');
      if (tip) {
        const pct = f.total ? Math.round((f.done / f.total) * 100) : 0;
        tip.innerHTML = `<span>正在补第 ${f.done}/${f.total} 个：${esc(f.message || '')}`
          + `　已补 ${f.added || 0} 条</span><i style="width:${pct}%"></i>`;
      }
    } catch (e) {}
  }, 1600);
}

/* ---------------- 一个人的全部投稿 ----------------
   「按人看」点进去就是这里。库里只存了动态流抓到的最近几条，停更的人往往
   一条都没有，所以顶上放一个「往前翻」：顺着他的空间动态一页页往回挖，
   挖到哪记到哪（ups.dyn_offset），下次接着翻。 */
async function openUpPage(mid) {
  state.view = 'up';
  state.upMid = parseInt(mid, 10) || 0;
  state.up = null;
  state.q = '';
  $('#search').value = '';
  document.querySelectorAll('.tab').forEach((t) => t.classList.remove('active'));
  syncToolbar();
  try {
    history.replaceState(null, '', '?view=up&mid=' + state.upMid);
  } catch (e) {}
  await loadUpPage();
}

async function loadUpPage() {
  showList();
  const box = $('#list');
  box.className = '';
  $('#empty').classList.add('hidden');
  box.innerHTML = '<div class="up-wait">正在读他的库…</div>';
  try {
    state.up = await api('/api/up?mid=' + state.upMid);
    renderUpPage();
  } catch (e) {
    box.innerHTML = `<div class="up-wait">读不出来：${esc(e.message)}</div>`;
  }
}

function renderUpPage() {
  const d = state.up || {};
  const up = d.up || {};
  const list = d.videos || [];
  const box = $('#list');
  box.className = '';
  box.innerHTML = '';

  const last = d.last_ts ? ago(d.last_ts) : '';
  const first = d.first_ts ? ym(d.first_ts) + ' 起' : '';

  const hd = el('div', 'up-hd');
  hd.innerHTML = `
    <button class="up-back">← 按人看</button>
    <img class="up-ava" src="${esc(up.face || '')}" data-raw="${esc(up.face || '')}" onerror="imgErr(this)" referrerpolicy="no-referrer">
    <div class="up-id">
      <div class="up-nm">${esc(up.name || d.mid || '')}</div>
      <div class="up-line">
        <span class="ucat${up.manual ? ' man' : ''}" title="点一下改分类">${esc(
          up.subcat ? `${catName(up.cat)} · ${subLabel(up.cat, up.subcat)}` : catName(up.cat))}</span>
        <span>库里 ${d.count || 0} 条${first ? '，' + first : ''}</span>
        ${last ? `<span>最后更新 ${last}</span>` : '<span>库里还没有他的投稿</span>'}
      </div>
    </div>
    <button class="up-more">往前翻更早的投稿</button>`;
  box.appendChild(hd);

  const back = hd.querySelector('.up-back');
  back.onclick = () => {
    state.view = 'ups';
    document.querySelectorAll('.tab').forEach((t) =>
      t.classList.toggle('active', t.dataset.view === 'ups'));
    syncToolbar();
    try { history.replaceState(null, '', '?view=ups'); } catch (e) {}
    loadUps();
  };
  hd.querySelector('.ucat').onclick = () => openCatPop(state.upMid, up.name, up.cat, up.subcat);

  const more = hd.querySelector('.up-more');
  if (d.done) {
    more.textContent = '已经翻到最早一条了';
    more.disabled = true;
  } else {
    more.onclick = () => upFetchMore();
  }

  if (!list.length) {
    const e = el('div', 'up-wait');
    e.innerHTML = '这个人还没进过库。<br>点右上角「往前翻更早的投稿」，'
      + '顺着他的空间动态往回挖，一次大概能翻出五六十条。';
    box.appendChild(e);
    return;
  }

  const grid = el('div', 'grid');
  list.forEach((v) => grid.appendChild(videoCard(v, false, list)));
  box.appendChild(grid);
}

async function upFetchMore() {
  if (state.upFetching) return;
  const btn = $('.up-more');
  if (!btn) return;
  state.upFetching = true;
  const old = btn.textContent;
  btn.disabled = true;
  btn.textContent = '正在往前翻…';
  try {
    const d = await api('/api/up/fetch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mid: state.upMid, pages: 5 }),
    });
    if (d.error) toast('翻不动：' + d.error);
    else if (d.added) toast(`又翻出 ${d.added} 条`);
    else if (d.done) toast('到头了，最早的就是这些');
    else toast('这几页都是已经在库里的');
    await loadUpPage();
    if (d.done) {
      const b = $('.up-more');
      if (b) { b.textContent = '已经翻到最早一条了'; b.disabled = true; }
    }
  } catch (e) {
    toast('翻不动：' + e.message);
    btn.textContent = old;
  } finally {
    state.upFetching = false;
  }
}

/* ---------------- 改分类 ----------------
   自动分错了就地改：播放器顶上点分类标签，或者在「按人看」里点卡片上的标签。
   改过的记在 ups.manual=1，之后重跑 classify 不会把人的判断冲掉。 */
function catName(id) {
  if (!id) return '未分类';
  const c = (state.cats || []).find((x) => x.id === id);
  return c ? c.name : '未分类';
}

let catPopMid = 0;

function openCatPop(mid, name, cat, sub) {
  mid = parseInt(mid, 10) || 0;
  if (!mid) return;
  catPopMid = mid;
  const curSub = sub || '';
  const where = cat
    ? `　现在在「${subName(cat, curSub) || catName(cat)}」`
    : '　现在没有分类';
  $('#cat-who').textContent = '把这个 UP 放到哪一类';
  $('#cat-hint').textContent = (name || '未知 UP') + where;
  const g = $('#cat-grid');
  g.innerHTML = '';
  (state.cats || []).forEach((c) => {
    const b = el('button', c.id === cat ? 'on' : '');
    b.textContent = c.name;
    b.title = c.desc;
    b.onclick = () => submitCat(c.id);
    g.appendChild(b);

    // 「生活」这种大类下面带分支的，紧跟着铺一行细选。
    // 不点细分就只改大类，程序会自己挑一支放进去。
    if (c.subs && c.subs.length) {
      const wrap = el('div', 'cat-subs');
      wrap.appendChild(el('span', 'lb')).textContent = '细分';
      c.subs.forEach((s) => {
        const sb = el('button', cat === c.id && curSub === s.id ? 'on' : '');
        sb.textContent = s.name;
        sb.title = s.desc;
        sb.onclick = () => submitCat(c.id, s.id);
        wrap.appendChild(sb);
      });
      g.appendChild(wrap);
    }
  });
  $('#cat-pop').classList.remove('hidden');
}

function subLabel(cat, sub) {
  const c = (state.cats || []).find((x) => x.id === cat);
  const s = c && (c.subs || []).find((x) => x.id === sub);
  return s ? s.name : '';
}

function subName(cat, sub) {
  const nm = subLabel(cat, sub);
  return nm ? `${catName(cat)} · ${nm}` : '';
}

function closeCatPop() {
  $('#cat-pop').classList.add('hidden');
  catPopMid = 0;
}

async function submitCat(cat, sub) {
  const mid = catPopMid;
  if (!mid) return;
  try {
    const d = await api('/api/set_cat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mid, cat: cat || '', sub: sub || '' }),
    });
    if (d.error) throw new Error(d.error);
    closeCatPop();
    toast(cat ? `已移到「${subName(cat, sub) || catName(cat)}」` : '已恢复自动分类');
    if (cur && cur.mid === mid) {
      cur.upCat = (d.up && d.up.cat) || cat;
      cur.upSub = (d.up && d.up.sub) || '';
      renderPlayerUp();
    }
    await loadState();
    if (state.view === 'ups') await loadUps();
    else if (state.view === 'timeline') await loadVideos();
    else if (state.view === 'home') await loadHome(true);
  } catch (e) {
    toast('没能改：' + e.message);
  }
}

function renderPlayerUp() {
  const box = $('#p-sub');
  if (!cur || !cur.mid) { box.classList.add('hidden'); return; }
  box.classList.remove('hidden');
  $('#p-upname').textContent = cur.name || '未知 UP';
  const chip = $('#p-catchip');
  // 相关推荐点进来的 UP 不在关注列表里，没什么可分的
  if (cur.upKnown === false) { chip.classList.add('hidden'); return; }
  chip.classList.remove('hidden');
  const lab = cur.upSub ? subLabel(cur.upCat, cur.upSub) : '';
  const full = lab ? `${catName(cur.upCat)}·${lab}` : catName(cur.upCat);
  chip.textContent = '分类：' + full + ' ▾';
  chip.onclick = () => openCatPop(cur.mid, cur.name, cur.upCat, cur.upSub);
}

/* ---------------- 看过 ---------------- */
function renderRecent() {
  showList();
  const box = $('#list');
  box.className = 'grid';
  box.innerHTML = '';
  $('#empty').classList.add('hidden');

  let rows = state.recent;
  if (state.q) {
    const q = state.q.toLowerCase();
    rows = rows.filter((v) => (v.title || '').toLowerCase().includes(q) ||
      (v.name || '').toLowerCase().includes(q));
  }
  if (!rows.length) {
    $('#empty').innerHTML = '还没有播放记录。看过一半的视频会按时间排在这里，接着看就行。';
    $('#empty').classList.remove('hidden');
    return;
  }
  rows.forEach((v) => {
    const c = videoCard(v);
    // 播完的给个标记
    if (v.progress > 0.92) c.querySelector('.thumb').insertAdjacentHTML(
      'beforeend', '<span class="tag" style="left:auto;right:5px;top:5px">已看完</span>');
    box.appendChild(c);
  });
}

/* ---------------- 分类推荐 ---------------- */
async function loadRecs(reset) {
  const bar = $('#recbar');
  if (state.view !== 'timeline' || state.star || state.cat === 'friend') {
    bar.classList.add('hidden');
    return;
  }
  if (reset) state.recOffset = 0;
  let d;
  try {
    d = await api(`/api/recs?cat=${encodeURIComponent(state.cat)}&n=4&offset=${state.recOffset}`);
  } catch (e) {
    bar.classList.add('hidden');
    return;
  }
  const items = d.items || [];
  if (!items.length) { bar.classList.add('hidden'); return; }
  state.recs = items;
  bar.classList.remove('hidden');
  const all = !state.cat;
  $('#rec-sub').textContent = all
    ? '全站热门里，排掉你已关注的人'
    : `来自${state.catName}分区排行，排掉你已关注的人`;
  $('.rec-title').textContent = all
    ? '关注之外，B 站今天在火什么'
    : `这个分类里，你没关注的人在发什么`;

  const box = $('#recs');
  box.innerHTML = '';
  items.forEach((v) => {
    box.appendChild(videoCard({
      bvid: v.bvid,
      title: v.title,
      cover: v.cover,
      duration: v.duration,
      name: v.name,
      face: v.face,
      play: v.play,
      pub_ts: v.pubdate,
    }, true));
  });
}

/* ---------------- 数据加载 ---------------- */
async function loadState() {
  const s = await api('/api/state');
  state.user = s.user;
  state.cats = s.cats || [];
  state.stats = s.stats || {};
  state.stars = s.stars || 0;
  state.fill = s.fill || {};
  if (s.per_up_limit) state.limit = s.per_up_limit;
  $('#who').textContent = s.user ? s.user.uname : '未登录';
  $('#btn-limit').textContent = `每人 ${state.limit} 条`;
  renderCats();
  renderStats();
  return s;
}

async function loadVideos() {
  const p = new URLSearchParams();
  if (state.cat) p.set('cat', state.cat);
  if (state.sub) p.set('sub', state.sub);
  if (state.star) p.set('star', '1');
  if (state.days) p.set('days', state.days);
  const d = await api('/api/videos?' + p.toString());
  state.videos = d.items || [];
  state.page = 1;
  if (state.view === 'timeline') renderTimeline();
  else if (state.view === 'ups') await loadUps();
  if (state.view === 'timeline') loadRecs(true);
}

async function loadUps() {
  const p = new URLSearchParams();
  if (state.cat) p.set('cat', state.cat);
  if (state.sub) p.set('sub', state.sub);
  if (state.star) p.set('star', '1');
  const d = await api('/api/ups?' + p.toString());
  state.ups = d.items || [];
  renderUps();
}

async function loadRecent() {
  try {
    const d = await api('/api/recent?n=60');
    state.recent = d.items || [];
  } catch (e) {
    state.recent = [];
  }
  renderRecent();
}

/* ---------------- 同步 ---------------- */
let syncTimer = null;

async function pollSync() {
  const s = await api('/api/sync');
  const bar = $('#synctip');
  if (s.running) {
    bar.classList.remove('hidden');
    const pct = s.total ? Math.min(99, Math.round((s.done / s.total) * 100)) : 8;
    bar.querySelector('i').style.width = pct + '%';
    $('#sync-text').textContent = `${s.phase} · ${s.message}`;
    state.syncing = true;
    $('#btn-sync').textContent = '正在更新…';
    syncTimer = setTimeout(pollSync, 1200);
  } else if (state.syncing) {
    state.syncing = false;
    $('#btn-sync').textContent = '更新订阅';
    bar.querySelector('i').style.width = '100%';
    $('#sync-text').textContent = s.message || '完成';
    if (s.error) $('#sync-text').textContent = '失败：' + s.error;
    setTimeout(() => bar.classList.add('hidden'), 2400);
    await loadState();
    if (state.view === 'home') await loadHome(true);
    else if (state.view === 'up') await loadUpPage();
    else await loadVideos();
  }
}

async function startSync() {
  if (state.syncing) return;
  state.syncing = true;
  $('#btn-sync').textContent = '正在更新…';
  await api('/api/sync', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
  pollSync();
}

/* ================= 弹幕 ================= */
/* 自己画。B 站的老 XML 接口一次给全片，格式是
   <d p="时间,模式,字号,颜色,时间戳,池,uid,行号">文字</d>，后端已经解析成
   [t, mode, color, text] 数组，这里只管按播放时间把它们送过屏幕。 */

const dm = {
  items: [],
  idx: 0,
  active: [],
  tracks: [],
  tops: [],
  bottoms: [],
  on: true,
  canvas: null,
  ctx: null,
  w: 0, h: 0, vw: 0, vh: 0, vx: 0, vy: 0,
  areaH: 0,
  fs: 30,
  lineH: 40,
  lastT: 0,
  ready: false,
};

let dmRaw = [];

/** 按设置里的类型开关、屏蔽词、合并去重，把原始列表过一遍。 */
function applyDmFilters() {
  const cfg = SET.danmaku;
  const words = [];
  for (const w of cfg.block || []) {
    const s = String(w || '').trim();
    if (!s) continue;
    try {
      words.push(new RegExp(s, 'i'));
    } catch (e) {
      words.push(new RegExp(s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'i'));
    }
  }
  const seen = new Set();
  const out = [];
  for (const it of dmRaw) {
    const [t, mode, , text] = it;
    if (mode === 5 && !cfg.top) continue;
    if (mode === 4 && !cfg.bottom) continue;
    if (mode !== 4 && mode !== 5 && !cfg.scroll) continue;
    let hit = false;
    for (const re of words) if (re.test(text)) { hit = true; break; }
    if (hit) continue;
    if (cfg.merge) {
      const k = Math.floor(t) + '|' + text;
      if (seen.has(k)) continue;
      seen.add(k);
    }
    out.push(it);
  }
  dm.items = out;
  const v = $('#video');
  dmReseek(v ? v.currentTime || 0 : 0);
}

function dmInit() {
  dm.canvas = $('#dm');
  dm.ctx = dm.canvas.getContext('2d');
  dm.on = SET.danmaku.on !== false;
  $('#dm').classList.toggle('off', !dm.on);
  $('#p-dm').classList.toggle('on', dm.on);
  window.addEventListener('resize', dmResize);
  requestAnimationFrame(dmLoop);
}

function dmResize() {
  if (!dm.canvas) return;
  // 画布直接铺在"视频画面"那一块上，不铺满整个播放器：
  // 这样弹幕天生就落在画面里，不用 ctx.clip() 去裁——
  // clip 在这个规模下会莫名其妙把之后的绘制全吃掉。
  const layer = $('#player');
  const lr = layer.getBoundingClientRect();
  if (!lr.width || !lr.height) return;
  const v = $('#video');
  const vw = v.videoWidth || 16, vh = v.videoHeight || 9;
  const sc = Math.min(lr.width / vw, lr.height / vh);
  const dw = Math.max(1, Math.round(vw * sc));
  const dh = Math.max(1, Math.round(vh * sc));
  const dx = Math.round((lr.width - dw) / 2);
  const dy = Math.round((lr.height - dh) / 2);
  const cv = dm.canvas;
  cv.style.left = dx + 'px';
  cv.style.top = dy + 'px';
  cv.style.width = dw + 'px';
  cv.style.height = dh + 'px';

  const dpr = Math.min(2, window.devicePixelRatio || 1);
  cv.width = Math.round(dw * dpr);
  cv.height = Math.round(dh * dpr);
  dm.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  dm.w = dw;
  dm.h = dh;
  dm.vw = dw;
  dm.vh = dh;
  dm.vx = 0;
  dm.vy = 0;
  dm.lastVW = v.videoWidth || 0;

  // 字号跟着画面走：1080 高的片子大约 38px，和 B 站差不多
  const fscale = Math.min(1.8, Math.max(0.5, Number(SET.danmaku.scale) || 1));
  dm.fs = Math.max(11, Math.round(dm.vh * 0.035 * fscale));
  dm.lineH = Math.round(dm.fs * 1.42);
  const area = Math.min(1, Math.max(0.2, Number(SET.danmaku.area) || 0.76));
  dm.areaH = dm.vh * area;

  // 轨道数变了，原来排的位置就都不对了，重排
  const n = dmTrackCount();
  if (!dm.tracks.length || dm.tracks.length !== n) {
    dm.tracks = new Array(n).fill(0);
    dmClearKeepItems();
  }
}

/** 只清屏和轨道，不动播放进度对应的游标 */
function dmClearKeepItems() {
  dm.active = [];
  dm.tracks = new Array(dmTrackCount()).fill(0);
  dm.tops = [0, 0, 0];
  dm.bottoms = [0, 0, 0];
  if (dm.ctx) dm.ctx.clearRect(0, 0, dm.w, dm.h);
}

function dmTrackCount() {
  return Math.max(2, Math.floor(dm.areaH / Math.max(1, dm.lineH)));
}

function dmClear() {
  dm.active = [];
  dm.tracks = new Array(dmTrackCount()).fill(0);
  dm.tops = [0, 0, 0];
  dm.bottoms = [0, 0, 0];
  if (dm.ctx) dm.ctx.clearRect(0, 0, dm.w, dm.h);
}

function dmFreeSlot(arr, t) {
  for (let i = 0; i < arr.length; i++) if (arr[i] <= t) return i;
  return -1;
}

function dmReseek(t) {
  dmClear();
  // 二分找第一条时间 >= t 的弹幕
  let lo = 0, hi = dm.items.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (dm.items[mid][0] < t) lo = mid + 1; else hi = mid;
  }
  dm.idx = lo;
  dm.lastT = t;
}

function dmLaunch(t) {
  const ctx = dm.ctx;
  const life = Math.min(15, Math.max(4, Number(SET.danmaku.duration) || 9));
  while (dm.idx < dm.items.length && dm.items[dm.idx][0] <= t) {
    const [dt, mode, color, text] = dm.items[dm.idx++];
    // 掉帧时别一口气补一屏老弹幕
    if (t - dt > 1.2) continue;
    const fs = dm.fs;
    ctx.font = `${SET.danmaku.bold ? 600 : 400} ${fs}px "Microsoft YaHei",-apple-system,sans-serif`;
    const tw = ctx.measureText(text).width;
    const rgb = '#' + color.toString(16).padStart(6, '0');

    if (mode === 1 || mode === 2 || mode === 3) {
      const spd = dm.vw / life;
      // 轨道上一条必须已经整条进屏幕，才轮得到这一条
      let track = -1;
      for (let i = 0; i < dm.tracks.length; i++) {
        if (dm.tracks[i] <= dt) { track = i; break; }
      }
      if (track < 0) continue;            // 满了就丢，不硬挤
      dm.tracks[track] = dt + (tw + dm.fs) / spd + 0.12;
      dm.active.push({ t0: dt, text, rgb, tw, spd, track, fs, mode: 0 });
    } else if (mode === 5) {
      const slot = dmFreeSlot(dm.tops, dt);
      if (slot < 0) continue;
      dm.tops[slot] = dt + 5;
      dm.active.push({ t0: dt, text, rgb, tw, fs, mode: 1, slot });
    } else if (mode === 4) {
      const slot = dmFreeSlot(dm.bottoms, dt);
      if (slot < 0) continue;
      dm.bottoms[slot] = dt + 5;
      dm.active.push({ t0: dt, text, rgb, tw, fs, mode: 2, slot });
    }
  }
}

function dmDraw() {
  const ctx = dm.ctx;
  if (!ctx) return;
  ctx.clearRect(0, 0, dm.w, dm.h);
  if (!dm.active.length) return;
  const cfg = SET.danmaku;
  const t = dm.lastT;
  // 视频是 contain 的，两侧/上下会有黑边。弹幕只在画面里跑，
  // 不然会飘到黑边上，看着像错位。
  ctx.save();
  ctx.globalAlpha = Math.min(1, Math.max(0.15, Number(cfg.opacity) || 0.92));
  ctx.textBaseline = 'alphabetic';
  ctx.lineJoin = 'round';
  const weight = cfg.bold ? 600 : 400;
  const strokeW = Math.max(0, Math.min(2, Number(cfg.stroke))) * 0.14;

  const keep = [];
  try {
  for (const d of dm.active) {
    const age = t - d.t0;
    let x, y;
    if (d.mode === 0) {
      x = dm.vx + dm.vw - age * d.spd;
      if (x + d.tw < dm.vx) continue;               // 已经出画
      y = dm.vy + d.track * dm.lineH + dm.lineH * 0.78;
    } else {
      if (age > 5) continue;
      x = dm.vx + (dm.vw - d.tw) / 2;
      y = d.mode === 1
        ? dm.vy + (d.slot + 1) * dm.lineH
        : dm.vy + dm.vh - d.slot * dm.lineH - dm.fs * 0.4;
    }
    ctx.font = `${weight} ${d.fs}px "Microsoft YaHei",-apple-system,sans-serif`;
    if (strokeW > 0) {
      ctx.strokeStyle = 'rgba(0,0,0,.72)';
      ctx.lineWidth = Math.max(1.2, d.fs * strokeW);
      ctx.strokeText(d.text, x, y);
    } else {
      // 没描边的时候给一点投影，不然浅色弹幕在白画面上看不清
      ctx.shadowColor = 'rgba(0,0,0,.6)';
      ctx.shadowBlur = Math.max(2, d.fs * 0.14);
    }
    ctx.fillStyle = d.rgb;
    ctx.fillText(d.text, x, y);
    ctx.shadowBlur = 0;
    keep.push(d);
  }
  } catch (e) { /* 一条坏弹幕不该让整屏卡住 */ }
  ctx.restore();
  dm.active = keep;
}

function dmLoop() {
  requestAnimationFrame(dmLoop);
  if (!dm.canvas || $('#player').classList.contains('hidden')) return;
  const video = $('#video');
  // dash.js 是走 MSE 挂流的，loadedmetadata 的时机靠不住，
  // 每帧对一下视频尺寸，变了就重算画面区域
  if (video.videoWidth && video.videoWidth !== dm.lastVW) dmResize();
  if (!dm.on || !dm.ready) { return; }
  if (!dm.w || !dm.h) dmResize();

  const t = video.currentTime;
  // 拖进度或时间跳变：重新定位游标，但**不能就此 return**——
  // 否则这一帧不会发射弹幕，跳变频繁时（比如加了快放）画面里会一条都没有。
  if (t < dm.lastT - 0.4 || Math.abs(t - dm.lastT) > 1.5) {
    dmReseek(t);
  }
  dm.lastT = t;
  if (video.paused) { dmDraw(); return; }
  dmLaunch(t);
  dmDraw();
}

async function loadDanmaku(cid) {
  dmRaw = [];
  dm.items = [];
  dm.idx = 0;
  dm.ready = false;
  dmClear();
  dm.lastT = 0;
  $('#dm').classList.toggle('off', !dm.on);
  if (!dm.on) {
    dm.ready = true;
    renderDmList();
    return;
  }
  try {
    const d = await api('/api/danmaku?cid=' + cid);
    if (!cur || String(cur.cid) !== String(cid)) return;
    dmRaw = d.items || [];
    dm.ready = true;
    applyDmFilters();
    dmReseek($('#video').currentTime || 0);
    renderDmList();
    if (d.n) toast(`弹幕 ${d.n} 条`);
  } catch (e) {
    dm.ready = true;
    renderDmList();
  }
}

/* ================= 右侧面板：弹幕列表 / 评论 / 相关 ================= */
let sideTab = 'dm';
let cmPage = 1;
let cmAid = 0;
let cmLoading = false;
let cmHasMore = true;

function openSide(tab) {
  sideTab = tab || sideTab || 'dm';
  $('#p-set').classList.add('hidden');
  $('#p-side').classList.remove('hidden');
  document.querySelectorAll('.st').forEach((b) =>
    b.classList.toggle('active', b.dataset.st === sideTab));
  const map = { dm: '#side-dm', cm: '#side-cm', rel: '#side-rel' };
  Object.entries(map).forEach(([k, sel]) => $(sel).classList.toggle('hidden', k !== sideTab));
  if (sideTab === 'dm') renderDmList();
  else if (sideTab === 'cm') loadReplies(true);
  else loadRelated();
  wake();
}

function closeSide() { $('#p-side').classList.add('hidden'); }

function renderDmList() {
  const box = $('#side-dm');
  if (!box) return;
  if (!$('#side-dm').classList.contains('hidden') === false && sideTab !== 'dm') {
    // 没显示就不用渲染，省得白算
  }
  box.innerHTML = '';
  if (!dm.items.length) {
    box.appendChild(el('div', 'dm-empty',
      dm.on ? '这个视频还没有弹幕，或者还没加载完' : '弹幕已关闭。想看得先在设置里打开'));
    return;
  }
  const frag = document.createDocumentFragment();
  dm.items.slice(0, 600).forEach(([t, , , text]) => {
    const r = el('div', 'dmrow');
    const tm = el('time', '', fmtTime(t));
    const tx = el('span');
    tx.textContent = text;
    r.appendChild(tm);
    r.appendChild(tx);
    r.onclick = () => {
      const v = $('#video');
      if (!v.duration) return;
      v.currentTime = Math.min(v.duration - 0.2, Math.max(0, t));
      dmReseek(v.currentTime);
      wake();
    };
    frag.appendChild(r);
  });
  box.appendChild(frag);
}

function cmNode(it) {
  const n = el('div', 'cm');
  const h = el('div', 'cm-h');
  if (it.face) {
    const im = document.createElement('img');
    im.src = it.face;
    im.dataset.raw = it.face;
    im.loading = 'lazy';
    im.referrerPolicy = 'no-referrer';
    im.onerror = () => imgErr(im);
    h.appendChild(im);
  }
  const b = el('b');
  b.textContent = it.uname || '匿名';
  h.appendChild(b);
  if (it.top) h.appendChild(el('em', '', '置顶'));
  const time = el('span', '', ago(it.ctime));
  h.appendChild(time);
  n.appendChild(h);
  const t = el('div', 'cm-t');
  t.textContent = it.text || '';
  n.appendChild(t);
  const f = el('div', 'cm-f');
  f.innerHTML = `<i>👍 ${fmtNum(it.like || 0)}</i>` + (it.rcount ? `<i>回复 ${it.rcount}</i>` : '');
  n.appendChild(f);
  (it.kids || []).forEach((k) => {
    const kid = el('div', 'cm-kid');
    const kh = el('div', 'cm-h');
    const kb = el('b');
    kb.textContent = k.uname || '';
    kh.appendChild(kb);
    kid.appendChild(kh);
    const kt = el('div', 'cm-t');
    kt.textContent = k.text || '';
    kid.appendChild(kt);
    n.appendChild(kid);
  });
  return n;
}

async function loadReplies(reset) {
  if (!cur || cmLoading) return;
  if (reset) {
    cmPage = 1;
    cmHasMore = true;
    $('#side-cm').innerHTML = '';
  }
  if (!cmHasMore) return;
  cmLoading = true;
  const box = $('#side-cm');
  const more = el('button', 'cm-more', '…');
  box.appendChild(more);
  try {
    const q = `/api/replies?bvid=${encodeURIComponent(cur.bvid)}`
      + (cmAid ? `&aid=${cmAid}` : '') + `&pn=${cmPage}`;
    const d = await api(q);
    more.remove();
    if (d.error) throw new Error(d.error);
    if (cmPage === 1) {
      const head = el('div', 'set-group');
      head.style.margin = '10px 15px 4px';
      head.textContent = `共 ${fmtNum(d.count || 0)} 条评论`;
      if (!d.items || !d.items.length) {
        box.appendChild(el('div', 'cm-empty', '这个视频没有评论，或者被关掉了'));
        cmHasMore = false;
        cmLoading = false;
        return;
      }
      box.appendChild(head);
    }
    (d.items || []).forEach((it) => box.appendChild(cmNode(it)));
    cmHasMore = (d.items || []).length >= 18;
    cmPage += 1;
    if (cmHasMore) {
      const b2 = el('button', 'cm-more', '再看 20 条');
      b2.onclick = () => { b2.remove(); loadReplies(false); };
      box.appendChild(b2);
    }
  } catch (e) {
    try { more.remove(); } catch (e2) {}
    box.appendChild(el('div', 'cm-empty', '评论没拉下来：' + e.message));
    cmHasMore = false;
  }
  cmLoading = false;
}

async function loadRelated() {
  if (!cur) return;
  const box = $('#side-rel');
  if (box.dataset.bvid === cur.bvid && box.childElementCount) return;
  box.dataset.bvid = cur.bvid;
  box.innerHTML = '<div class="cm-empty">加载中…</div>';
  try {
    const d = await api('/api/related?bvid=' + encodeURIComponent(cur.bvid));
    box.innerHTML = '';
    const items = d.items || [];
    if (!items.length) {
      box.appendChild(el('div', 'cm-empty', '没有相关推荐'));
      return;
    }
    const wrap = el('div', 'rel-grid');
    items.forEach((v) => {
      const r = el('div', 'rel');
      const th = el('div', 'th');
      const im = document.createElement('img');
      im.src = v.cover ? v.cover.replace(/^http:/, 'https:') : '';
      im.dataset.raw = im.src;
      im.loading = 'lazy';
      im.referrerPolicy = 'no-referrer';
      im.onerror = () => imgErr(im);
      th.appendChild(im);
      const tx = el('div', 'tx');
      const b = el('b');
      b.textContent = v.title;
      const s = el('small');
      s.textContent = v.name + (v.play ? ' · ' + fmtNum(v.play) + ' 播放' : '');
      tx.appendChild(b);
      tx.appendChild(s);
      r.appendChild(th);
      r.appendChild(tx);
      r.onclick = () => {
        const q = items.map((x) => ({
          bvid: x.bvid, title: x.title, cover: x.cover, name: x.name, play: x.play,
        }));
        openPlayer(v, { queue: q });
      };
      wrap.appendChild(r);
    });
    box.appendChild(wrap);
  } catch (e) {
    box.innerHTML = '';
    box.appendChild(el('div', 'cm-empty', '相关推荐没拉下来'));
  }
}

/* ================= 进度条悬停预览 ================= */
let shots = null;

async function loadShots(bvid, cid) {
  shots = null;
  const im = $('#p-tip-img');
  im.classList.add('none');
  im.style.backgroundImage = '';
  try {
    const d = await api(`/api/shots?bvid=${encodeURIComponent(bvid)}&cid=${cid}`);
    if (!d.ok) return;
    if (!cur || String(cur.bvid) !== String(bvid)) return;
    shots = d;
    const dw = 168;
    const dh = Math.round(dw * d.h / d.w);
    im.style.width = dw + 'px';
    im.style.height = dh + 'px';
    im.style.backgroundSize = `${d.xlen * dw}px ${d.ylen * dh}px`;
    im.dataset.dw = dw;
    im.dataset.dh = dh;
    d.proxy = imgProxy(d.img);
    const probe = new Image();
    probe.onload = () => { if (shots === d) im.classList.remove('none'); };
    probe.src = d.proxy;
  } catch (e) {}
}

function showTip(clientX) {
  const bar = $('#p-progress');
  const ctrl = $('#p-ctrl');
  const v = $('#video');
  if (!bar || !v.duration) return;
  const r = bar.getBoundingClientRect();
  const cr = ctrl.getBoundingClientRect();
  const ratio = Math.min(1, Math.max(0, (clientX - r.left) / r.width));
  const t = ratio * v.duration;
  const tip = $('#p-tip');
  tip.classList.remove('hidden');
  const half = 92;
  const x = Math.max(half, Math.min(cr.width - half, clientX - cr.left));
  tip.style.left = x + 'px';
  $('#p-tip-time').textContent = fmtTime(t);

  const im = $('#p-tip-img');
  if (!shots) { im.classList.add('none'); return; }
  const idx = shots.index;
  let best = 0, bd = Infinity;
  for (let i = 0; i < idx.length; i++) {
    const d = Math.abs(idx[i] - t);
    if (d < bd) { bd = d; best = i; }
  }
  const dw = Number(im.dataset.dw) || 168;
  const dh = Number(im.dataset.dh) || 94;
  const c = best % shots.xlen;
  const r2 = Math.floor(best / shots.xlen);
  im.style.backgroundImage = `url("${shots.proxy || shots.img}")`;
  im.style.backgroundPosition = `${-c * dw}px ${-r2 * dh}px`;
  im.classList.remove('none');
}

function hideTip() {
  $('#p-tip').classList.add('hidden');
}

/* ================= 播放器 ================= */
let dp = null;
let cur = null;          // {bvid, title, cover, name, face, cid}
let curPages = [];
let saveTick = null;
let idleTimer = null;
let playQueue = [];      // 连播顺序
let queueIdx = -1;
let nextTimer = null;

function biliUrl(bvid, p) {
  return 'https://www.bilibili.com/video/' + bvid + (p > 1 ? '?p=' + p : '');
}

async function openInBili(bvid, p) {
  const url = biliUrl(bvid, p);
  try {
    const r = await fetch('/api/open?url=' + encodeURIComponent(url));
    const d = await r.json();
    if (!d.ok) throw new Error();
    toast('已在浏览器打开');
  } catch (e) {
    // 兜底：复制链接，省得用户自己拼
    try {
      await navigator.clipboard.writeText(url);
      toast('打开失败，链接已复制');
    } catch (e2) {
      toast('打开失败：' + url);
    }
  }
}

function openPlayer(v, opt) {
  opt = opt || {};
  if (!opt.keepQueue) {
    const q = opt.queue || [];
    playQueue = q.map((x) => ({
      bvid: x.bvid, title: x.title, cover: x.cover,
      name: x.name, face: x.face, duration: x.duration,
    }));
    queueIdx = playQueue.findIndex((x) => x.bvid === v.bvid);
  }

  cur = {
    bvid: v.bvid, title: v.title || '', cover: v.cover || '',
    name: v.name || '', face: v.face || '', cid: opt.cid || 0,
    mid: v.mid || 0, upCat: '', upSub: '', upKnown: !!v.mid,
  };

  const layer = $('#player');
  layer.classList.remove('hidden');
  hideNext();
  closeSide();
  $('#p-set').classList.add('hidden');
  dmClear();
  dm.items = [];
  dmRaw = [];
  dm.ready = false;
  shots = null;
  cmPage = 1;
  cmHasMore = true;
  cmAid = 0;
  cmLoading = false;
  $('#side-cm').innerHTML = '';
  $('#side-rel').innerHTML = '';
  $('#side-rel').dataset.bvid = '';
  $('#side-dm').innerHTML = '';
  $('#p-title').textContent = cur.title;
  $('#p-upname').textContent = cur.name || '';
  renderPlayerUp();
  $('#video').__outro = false;
  $('#p-loading').classList.remove('hidden');
  $('#p-quality').innerHTML = '<option>…</option>';
  $('#p-part').classList.add('hidden');
  applyFit();
  dmResize();
  wake();

  const qs = '/api/play?bvid=' + encodeURIComponent(cur.bvid) +
    (cur.cid ? '&cid=' + cur.cid : '');

  fetch(qs)
    .then((r) => r.json())
    .then((d) => {
      if (d.error) throw new Error(d.error);
      const view = d.view || {};
      cur.cid = d.cid;
      cur.title = view.title || cur.title;
      cur.cover = view.cover || cur.cover;
      // 从 ?play= 或推荐位直接进来时没有 UP 信息，用接口返回的补上
      if (!cur.name && view.owner) {
        cur.name = view.owner.name || '';
        cur.face = view.owner.face || '';
      }
      curPages = view.pages || [];
      $('#p-title').textContent = cur.title;
      // 这个 UP 归在哪一类 —— 播放器顶上那个小标签，点开就能改
      if (d.up && d.up.mid) {
        cur.mid = d.up.mid;
        cur.upCat = d.up.cat || '';
        cur.upKnown = d.up.known !== false;
        if (!cur.name) cur.name = d.up.name || '';
      }
      renderPlayerUp();

      // 分P
      const partSel = $('#p-part');
      if (curPages.length > 1) {
        partSel.innerHTML = curPages.map((p) =>
          `<option value="${p.cid}">P${p.page} ${esc(p.part || '')}</option>`).join('');
        partSel.value = String(cur.cid);
        partSel.classList.remove('hidden');
      } else {
        partSel.classList.add('hidden');
      }

      const sel = $('#p-quality');
      sel.innerHTML = '';
      (d.quality || []).forEach((q) => {
        const o = el('option'); o.value = q.id; o.textContent = q.label; sel.appendChild(o);
      });
      if (sel.options.length) sel.selectedIndex = 0;
      cmAid = d.aid || 0;

      if (dp) { try { dp.reset(); } catch (e) {} }
      dp = dashjs.MediaPlayer().create();
      dp.updateSettings({
        streaming: { abr: { initialBitrate: { video: 1200 } }, buffer: { fastSwitchEnabled: true } },
        debug: { logLevel: dashjs.Debug.LOG_LEVEL_NONE },
      });
      const video = $('#video');
      dp.initialize(video, d.mpd, true);
      dp.on(dashjs.MediaPlayer.events.STREAM_INITIALIZED, () => {
        $('#p-loading').classList.add('hidden');

        // 记忆位置。跳片头也在这里做，两个都设的时候取更靠后的那个
        let start = 0;
        if (SET.player.remember_pos && d.progress && d.progress.pos > 5
            && d.progress.pos < (video.duration - 10)) {
          start = d.progress.pos;
        }
        const intro = Number(SET.player.skip_intro) || 0;
        if (intro > 0 && start < intro && video.duration > intro + 20) {
          start = intro;
        }
        if (start > 0) {
          video.currentTime = start;
          if (intro > 0 && start === intro) toast(`跳过片头 ${intro} 秒`);
          else toast('接着上次 ' + fmtTime(start));
        }

        // 清晰度。auto 就交给 dash.js 自己切，别插手
        try {
          const reps = dp.getRepresentationsByType('video') || [];
          const pref = String(SET.player.quality || 'highest');
          if (reps.length && pref !== 'auto') {
            const sorted = reps.slice().sort((a, b) => (b.height || 0) - (a.height || 0));
            let best = sorted[0];
            if (pref !== 'highest') {
              const want = parseInt(pref, 10);
              const hit = sorted.find((r) => (r.height || 0) <= want);
              if (hit) best = hit;
              else best = sorted[sorted.length - 1];
            }
            dp.updateSettings({ streaming: { abr: { autoSwitchBitrate: { video: false } } } });
            dp.setRepresentationForTypeById('video', best.id);
            sel.value = best.id;
          }
        } catch (e) {}

        // 倍速。dash.js 挂流之后会重置 playbackRate，所以放在这儿
        const r = Number(SET.player.rate) || 1;
        video.playbackRate = r;
        if (r !== 1) $('#p-rate').value = String(r);

        // 必须等 dash.js 装好流之后再播。之前是在 initialize 之后立刻调，
        // 被它自己的加载请求打断（AbortError），之后就没人再调了 —— 画面停在第一帧。
        safePlay();
        loadShots(cur.bvid, cur.cid);
      });
      dp.on(dashjs.MediaPlayer.events.STREAM_TEARDOWN_COMPLETE, () => {});
      dp.on(dashjs.MediaPlayer.events.ERROR, (e) => {
        console.error(e);
        $('#p-title').textContent = '播放出错：' + ((e.error && e.error.message) || '未知错误');
      });
      loadDanmaku(cur.cid);
      if (sideTab === 'cm' && !$('#p-side').classList.contains('hidden')) loadReplies(true);
      if (sideTab === 'rel' && !$('#p-side').classList.contains('hidden')) loadRelated();
    })
    .catch((e) => {
      $('#p-loading').classList.add('hidden');
      $('#p-title').textContent = '打不开：' + e.message;
    });

  clearInterval(saveTick);
  saveTick = setInterval(saveProgress, 5000);
}

function saveProgress() {
  const video = $('#video');
  if (!cur || !video.duration || !isFinite(video.duration)) return;
  fetch('/api/progress', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      bvid: cur.bvid, pos: video.currentTime, dur: video.duration,
      title: cur.title, cover: cur.cover, name: cur.name,
    }),
  }).catch(() => {});
}

function closePlayer() {
  saveProgress();
  clearInterval(saveTick);
  hideNext();
  closeSide();
  $('#p-set').classList.add('hidden');
  hideTip();
  const video = $('#video');
  video.pause();
  if (dp) { try { dp.reset(); } catch (e) {} dp = null; }
  dmClear();
  dm.items = [];
  dmRaw = [];
  dm.ready = false;
  shots = null;
  $('#player').classList.add('hidden');
  cur = null;
  if (state.view === 'home') loadHome(true);
  else if (state.view === 'recent') loadRecent();
  else if (state.view === 'up') loadUpPage();
  else if (state.view === 'ups') loadUps();
  else loadVideos();
}

function wake() {
  const layer = $('#player');
  layer.classList.remove('idle');
  clearTimeout(idleTimer);
  idleTimer = setTimeout(() => {
    const panelOpen = !$('#p-side').classList.contains('hidden')
      || !$('#p-set').classList.contains('hidden');
    if (!$('#video').paused && !panelOpen) layer.classList.add('idle');
  }, 2600);
}

/** 统一的起播入口。
 *
 * 除了等 dash.js 装好，还得兜住自动播放策略：带声音被拦就静音先起播，
 * 起来之后再恢复音量，否则用户看到的就是一直黑屏转圈。
 */
function safePlay() {
  const video = $('#video');
  const p = video.play();
  if (!p || !p.catch) return;
  p.catch(() => {
    if (video.muted) return;
    video.muted = true;
    $('#p-mute').textContent = '🔇';
    video.play().then(() => {
      video.muted = false;
      $('#p-mute').textContent = '🔊';
    }).catch(() => {});
  });
}

/* ---- 连播 ---- */
function hideNext() {
  clearInterval(nextTimer);
  nextTimer = null;
  $('#p-next').classList.add('hidden');
}

function showNext(v) {
  if (!v) return;
  const wait = Math.max(0, Math.min(15, Number(SET.player.next_wait) || 0));
  if (wait === 0) { playAt(queueIdx + 1); return; }
  $('#p-next-title').textContent = v.title || v.bvid;
  $('#p-next').classList.remove('hidden');
  let left = wait;
  const go = $('#p-next-go');
  go.textContent = `马上播 (${left})`;
  clearInterval(nextTimer);
  nextTimer = setInterval(() => {
    left -= 1;
    if (left <= 0) {
      clearInterval(nextTimer);
      nextTimer = null;
      playAt(queueIdx + 1);
      return;
    }
    go.textContent = `马上播 (${left})`;
  }, 1000);
}

function playAt(i) {
  if (i < 0 || i >= playQueue.length) return;
  queueIdx = i;
  hideNext();
  openPlayer(playQueue[i], { keepQueue: true });
}

function step(dir) {
  const i = queueIdx + dir;
  if (i < 0 || i >= playQueue.length) {
    toast(dir > 0 ? '已经是最后一个了' : '这是第一个');
    return;
  }
  playAt(i);
}

/* ---- 截图 / 画中画 ---- */
function screenshot() {
  const video = $('#video');
  if (!video.videoWidth) { toast('还没有画面'); return; }
  const c = document.createElement('canvas');
  c.width = video.videoWidth;
  c.height = video.videoHeight;
  c.getContext('2d').drawImage(video, 0, 0, c.width, c.height);
  c.toBlob((blob) => {
    const a = document.createElement('a');
    const name = (cur && cur.title ? cur.title : '截图').replace(/[\\/:*?"<>|\s]+/g, '_').slice(0, 40);
    a.href = URL.createObjectURL(blob);
    a.download = `${name}_${fmtTime(video.currentTime).replace(/:/g, '-')}.png`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
    toast('截图已存到下载文件夹');
  }, 'image/png');
}

async function togglePip() {
  const video = $('#video');
  try {
    if (document.pictureInPictureElement) await document.exitPictureInPicture();
    else await video.requestPictureInPicture();
  } catch (e) {
    toast('这个窗口不支持画中画，可以按 F 全屏');
  }
}

/* ---- 绑定 ---- */
function togglePlay() {
  const video = $('#video');
  video.paused ? safePlay() : video.pause();
}

function handleEnded() {
  const nx = playQueue[queueIdx + 1];
  if (!SET.player.autoplay_next || !nx) return;
  showNext(nx);
}

function setRate(r) {
  const video = $('#video');
  const RATES = [0.5, 0.75, 1, 1.25, 1.5, 1.75, 2, 2.5, 3, 3.5, 4];
  r = Math.min(4, Math.max(0.5, Number(r) || 1));
  video.playbackRate = r;
  SET.player.rate = RATES.includes(r) ? r : SET.player.rate;
  scheduleSave();
  const sel = $('#p-rate');
  if (sel && [...sel.options].some((o) => Number(o.value) === r)) sel.value = String(r);
  if ('preservesPitch' in video) video.preservesPitch = true;
  if ('webkitPreservesPitch' in video) video.webkitPreservesPitch = true;
  toast('倍速 ' + r + '×');
}

function bindPlayer() {
  const video = $('#video');

  $('#p-close').onclick = closePlayer;
  $('#p-toggle').onclick = togglePlay;
  video.onclick = togglePlay;
  video.ondblclick = (e) => {
    if (!SET.player.dblclick_full) return;
    e.preventDefault();
    $('#p-full').click();
  };
  video.onplay = () => { $('#p-toggle').textContent = '❚❚'; };
  video.onpause = () => { $('#p-toggle').textContent = '▶'; };

  video.addEventListener('loadedmetadata', dmResize);

  $('#p-prev').onclick = () => step(-1);
  $('#p-nextbtn').onclick = () => step(1);

  $('#p-ext').onclick = () => {
    if (!cur) return;
    const p = curPages.find((x) => String(x.cid) === String(cur.cid));
    openInBili(cur.bvid, p ? p.page : 1);
  };

  $('#p-next-go').onclick = () => { playAt(queueIdx + 1); };
  $('#p-next-cancel').onclick = hideNext;

  /* 右侧面板 */
  $('#p-side-btn').onclick = () => {
    if ($('#p-side').classList.contains('hidden')) openSide('cm');
    else closeSide();
  };
  $('#p-side-close').onclick = closeSide;
  document.querySelectorAll('.st').forEach((b) => {
    b.onclick = () => { sideTab = b.dataset.st; openSide(sideTab); };
  });

  /* 设置面板 */
  $('#p-set-btn').onclick = () => {
    if ($('#p-set').classList.contains('hidden')) openSet('play');
    else $('#p-set').classList.add('hidden');
  };
  $('#set-close').onclick = () => $('#p-set').classList.add('hidden');
  document.querySelectorAll('.stab').forEach((b) => {
    b.onclick = () => {
      document.querySelectorAll('.stab').forEach((x) => x.classList.remove('active'));
      b.classList.add('active');
      renderSetBody(b.dataset.se);
    };
  });

  video.onended = handleEnded;

  video.ontimeupdate = () => {
    if (!video.duration) return;
    const pct = (video.currentTime / video.duration) * 100;
    $('#p-played').style.width = pct + '%';
    $('#p-knob').style.left = pct + '%';
    $('#p-time').textContent = `${fmtTime(video.currentTime)} / ${fmtTime(video.duration)}`;
    const b = video.buffered;
    if (b.length) {
      const end = b.end(b.length - 1);
      $('#p-buf').style.width = Math.min(100, (end / video.duration) * 100) + '%';
    }
    // 片尾提前结束
    const so = Number(SET.player.skip_outro) || 0;
    if (so > 0) {
      if (video.duration - video.currentTime <= so && !video.__outro) {
        video.__outro = true;
        video.pause();
        if (!playQueue[queueIdx + 1] || !SET.player.autoplay_next) {
          toast(`片尾 ${so} 秒已跳过`);
        }
        handleEnded();
      }
    } else {
      video.__outro = false;
    }
  };

  let dragging = false;
  const seekTo = (e) => {
    const r = $('#p-progress').getBoundingClientRect();
    const ratio = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
    if (video.duration) {
      video.currentTime = ratio * video.duration;
      dmReseek(video.currentTime);
    }
  };
  const bar = $('#p-progress');
  bar.onmousedown = (e) => { dragging = true; hideTip(); seekTo(e); };
  bar.addEventListener('mousemove', (e) => { if (!dragging) showTip(e.clientX); });
  bar.addEventListener('mouseleave', hideTip);
  window.addEventListener('mousemove', (e) => { if (dragging) seekTo(e); });
  window.addEventListener('mouseup', () => { dragging = false; });

  // 滚轮调音量
  $('#player').addEventListener('wheel', (e) => {
    if (!SET.player.wheel_volume) return;
    if (e.target.closest('#p-side, #p-set')) return;
    e.preventDefault();
    const step = e.deltaY < 0 ? 0.05 : -0.05;
    video.volume = Math.min(1, Math.max(0, video.volume + step));
    if (video.volume > 0 && video.muted) {
      video.muted = false;
      $('#p-mute').textContent = '🔊';
    }
    $('#p-vol').value = video.volume;
    wake();
  }, { passive: false });

  // 分P
  $('#p-part').onchange = (e) => {
    if (!cur) return;
    saveProgress();
    openPlayer(cur, { keepQueue: true, cid: e.target.value });
  };

  // 倍速：下拉选，一次到位，不用一路点过去
  const RATES = [0.5, 0.75, 1, 1.25, 1.5, 1.75, 2, 2.5, 3, 3.5, 4];
  const rateSel = $('#p-rate');
  rateSel.innerHTML = '';
  RATES.forEach((r) => {
    const o = document.createElement('option');
    o.value = String(r);
    o.textContent = String(r) + '×';
    rateSel.appendChild(o);
  });
  const savedRate = Number(SET.player.rate) || 1;
  rateSel.value = String(RATES.includes(savedRate) ? savedRate : 1);
  video.playbackRate = Number(rateSel.value);
  rateSel.onchange = () => {
    video.playbackRate = Number(rateSel.value);
    SET.player.rate = Number(rateSel.value);
    scheduleSave();
    LS.set('rate', Number(rateSel.value));
    // 提速时保持音调，不然 2× 以上就是花栗鼠
    if ('preservesPitch' in video) video.preservesPitch = true;
    if ('webkitPreservesPitch' in video) video.webkitPreservesPitch = true;
  };
  if ('preservesPitch' in video) video.preservesPitch = true;

  // 音量记忆
  const vol = Number(SET.player.volume);
  video.volume = isFinite(vol) ? Math.min(1, Math.max(0, vol)) : 1;
  $('#p-vol').value = video.volume;
  video.addEventListener('volumechange', () => {
    SET.player.volume = video.volume;
    scheduleSave();
    LS.set('vol', video.volume);
  });

  $('#p-quality').onchange = (e) => {
    if (!dp) return;
    dp.updateSettings({ streaming: { abr: { autoSwitchBitrate: { video: false } } } });
    dp.setRepresentationForTypeById('video', e.target.value);
    // 换档偶尔会把播放按下暂停，补一手
    setTimeout(() => { if (video.paused) safePlay(); }, 500);
  };

  $('#p-mute').onclick = () => {
    video.muted = !video.muted;
    $('#p-mute').textContent = video.muted ? '🔇' : '🔊';
  };
  $('#p-vol').oninput = (e) => {
    video.volume = parseFloat(e.target.value);
    video.muted = video.volume === 0;
    $('#p-mute').textContent = video.muted ? '🔇' : '🔊';
  };

  $('#p-dm').onclick = () => {
    dm.on = !dm.on;
    SET.danmaku.on = dm.on;
    scheduleSave();
    LS.set('dm', dm.on);
    $('#p-dm').classList.toggle('on', dm.on);
    $('#dm').classList.toggle('off', !dm.on || !dm.ready);
    if (dm.on && cur && !dm.items.length) loadDanmaku(cur.cid);
    else if (!dm.on) dmClear();
    if (sideTab === 'dm') renderDmList();
  };

  $('#p-pip').onclick = togglePip;
  $('#p-shot').onclick = screenshot;

  $('#p-full').onclick = () => {
    if (document.fullscreenElement) document.exitFullscreen();
    else $('#player').requestFullscreen().catch(() => {});
  };

  $('#player').addEventListener('mousemove', wake);

  window.addEventListener('keydown', (e) => {
    if ($('#player').classList.contains('hidden')) {
      if (e.key === '/' && document.activeElement.tagName !== 'INPUT') {
        e.preventDefault(); $('#search').focus();
      }
      return;
    }
    if (e.target && /^(INPUT|SELECT|TEXTAREA)$/.test(e.target.tagName)) return;
    const k = e.key;
    const stepS = Number(SET.player.seek_step) || 5;
    const stepB = Number(SET.player.seek_step_big) || 30;
    if (k === 'Escape') {
      if (!$('#cat-pop').classList.contains('hidden')) return closeCatPop();
      if (!$('#p-set').classList.contains('hidden')) return $('#p-set').classList.add('hidden');
      if (!$('#p-side').classList.contains('hidden')) return closeSide();
      return closePlayer();
    }
    if (k === '?' || (e.shiftKey && k === '/')) {
      e.preventDefault();
      if ($('#p-set').classList.contains('hidden')) openSet('keys');
      else $('#p-set').classList.add('hidden');
      return;
    }
    if (k === 'Tab') {
      e.preventDefault();
      if ($('#p-side').classList.contains('hidden')) openSide('cm');
      else closeSide();
      return;
    }
    if (k === ' ' || e.code === 'Space' || k === 'k' || k === 'K') {
      e.preventDefault();
      togglePlay();
    }
    else if (k === 'ArrowRight') { video.currentTime += e.shiftKey ? stepB : stepS; dmReseek(video.currentTime); wake(); }
    else if (k === 'ArrowLeft') { video.currentTime -= e.shiftKey ? stepB : stepS; dmReseek(video.currentTime); wake(); }
    else if (k === 'l' || k === 'L') { video.currentTime += 10; dmReseek(video.currentTime); wake(); }
    else if (k === 'j' || k === 'J') { video.currentTime -= 10; dmReseek(video.currentTime); wake(); }
    else if (k === 'ArrowUp') { video.volume = Math.min(1, video.volume + 0.05); $('#p-vol').value = video.volume; wake(); }
    else if (k === 'ArrowDown') { video.volume = Math.max(0, video.volume - 0.05); $('#p-vol').value = video.volume; wake(); }
    else if (k === 'f' || k === 'F') $('#p-full').click();
    else if (k === 'c' || k === 'C') $('#p-dm').click();
    else if (k === 'p' || k === 'P') togglePip();
    else if (k === 's' || k === 'S') screenshot();
    else if (k === 'b' || k === 'B') $('#p-ext').click();
    else if (k === 'm' || k === 'M') $('#p-mute').click();
    else if (e.shiftKey && (k === ',' || k === '<')) setRate(video.playbackRate - 0.25);
    else if (e.shiftKey && (k === '.' || k === '>')) setRate(video.playbackRate + 0.25);
    wake();
  });
}

/* ---------------- 事件 ---------------- */
/* 首页用不上「时间范围」「每人几条」，这两个控件跟着视图走 */
function syncToolbar() {
  const onList = state.view === 'timeline';
  $('#days').classList.toggle('hidden', !onList);
  $('#btn-limit').classList.toggle('hidden', !onList);
  const sortBox = $('#ups-sort');
  sortBox.classList.toggle('hidden', state.view !== 'ups');
  sortBox.querySelectorAll('button').forEach((b) =>
    b.classList.toggle('on', b.dataset.sort === state.upsSort));
  // 这条提示只属于「按人看」，换视图就收起来，别挂在首页顶上
  if (state.view !== 'ups') {
    const t = $('#ups-tip');
    if (t) t.classList.add('hidden');
  }
  if (state.view !== 'timeline') $('#recbar').classList.add('hidden');
}

function bindUI() {
  // 改分类弹层：点空白处、取消、恢复自动
  $('#cat-cancel').onclick = closeCatPop;
  $('#cat-auto').onclick = () => submitCat('');
  $('#cat-pop').onclick = (e) => { if (e.target.id === 'cat-pop') closeCatPop(); };

  document.querySelectorAll('#ups-sort button').forEach((b) => {
    b.onclick = () => {
      state.upsSort = b.dataset.sort;
      LS.set('upsSort', state.upsSort);
      syncToolbar();
      renderUps();
    };
  });

  document.querySelectorAll('.tab').forEach((t) => {
    t.onclick = () => {
      if (state.view === t.dataset.view) return;
      document.querySelectorAll('.tab').forEach((x) => x.classList.remove('active'));
      t.classList.add('active');
      state.view = t.dataset.view;
      state.page = 1;
      state.filterMid = null;
      syncToolbar();
      if (state.view === 'home') loadHome();
      else if (state.view === 'timeline') { renderTimeline(); loadRecs(false); }
      else if (state.view === 'ups') { $('#recbar').classList.add('hidden'); loadUps(); }
      else { $('#recbar').classList.add('hidden'); loadRecent(); }
    };
  });

  $('#days').onchange = (e) => { state.days = parseInt(e.target.value, 10); loadVideos(); };

  let ti = null;
  $('#search').oninput = (e) => {
    clearTimeout(ti);
    ti = setTimeout(() => {
      state.q = e.target.value.trim();
      if (state.view === 'home') {
        if (!state.q) return;
        // 首页是概览，搜不了东西，直接落到「全部关注」里搜
        state.view = 'timeline';
        document.querySelectorAll('.tab').forEach((x) =>
          x.classList.toggle('active', x.dataset.view === 'timeline'));
        syncToolbar();
        loadVideos();
        return;
      }
      if (state.view === 'search') {
        if (state.q) doSearch(state.q, 1);
        return;
      }
      if (state.view === 'timeline') renderTimeline();
      else if (state.view === 'ups') renderUps();
      else if (state.view === 'up') return;   // 单个人的页里搜的是全站，不在这儿过滤
      else renderRecent();
    }, 180);
  };

  $('#btn-limit').onclick = () => {
    const seq = [1, 2, 3, 5, 99];
    const i = seq.indexOf(state.limit);
    state.limit = seq[(i + 1) % seq.length];
    $('#btn-limit').textContent = state.limit === 99 ? '不限条数' : `每人 ${state.limit} 条`;
    state.expanded.clear();
    renderTimeline();
  };

  $('#rec-shuffle').onclick = () => {
    state.recOffset += 4;
    loadRecs(false);
  };

  $('.brand').onclick = gotoHome;

  $('#btn-sync').onclick = startSync;
  $('#btn-quit').onclick = () => {
    fetch('/api/shutdown', { method: 'POST' }).catch(() => {});
    setTimeout(() => window.close(), 300);
  };

  setInterval(() => { fetch('/api/state', { cache: 'no-store' }).catch(() => {}); }, 45000);
}

/* ---------------- 启动 ---------------- */
(async function init() {
  await loadSettings();
  applyFit();
  bindPlayer();
  bindUI();
  dmInit();

  const params = new URLSearchParams(location.search);
  state.upsSort = LS.get('upsSort', 'new');
  const qsort = params.get('sort');
  if (qsort === 'old' || qsort === 'new') state.upsSort = qsort;
  const qv = params.get('view');
  const qmid = parseInt(params.get('mid') || '0', 10) || 0;
  if (qv === 'up' && qmid) { state.view = 'up'; state.upMid = qmid; }
  else if (qv === 'timeline' || qv === 'ups' || qv === 'recent') state.view = qv;
  document.querySelectorAll('.tab').forEach((t) =>
    t.classList.toggle('active', t.dataset.view === state.view));
  syncToolbar();
  const s = await loadState();
  // 没有账号就直说，别让人对着一屏空卡片猜（?force=1 进来会走到这里）
  if (s && s.need_setup) { showNeedSetup(); return; }
  // ?cat=history 这样可以直接落到某个分类，方便存书签
  const qc = params.get('cat');
  if (qc) {
    const c = (s.cats || []).find((x) => x.id === qc);
    const qs = params.get('sub');
    const sb = c && (c.subs || []).find((x) => x.id === qs);
    state.cat = qc;
    state.sub = sb ? qs : '';
    state.catName = sb ? `${c.name} · ${sb.name}` : (c ? c.name : qc);
    state.view = 'timeline';
    document.querySelectorAll('.tab').forEach((t) =>
      t.classList.toggle('active', t.dataset.view === 'timeline'));
    syncToolbar();
    renderCats();
  }
  if (state.view === 'up') await loadUpPage();  else if (state.view === 'home') await loadHome();
  else if (state.view === 'recent') await loadRecent();
  else await loadVideos();
  // ?q=关键词 直接搜全站，方便存书签
  const qq = params.get('q');
  if (qq) await doSearch(qq, 1);


  const playBvid = params.get('play');
  if (playBvid) {
    openPlayer({ bvid: playBvid, title: '' });
    // ?panel=set|cm|rel 可以直接带着面板打开，方便存书签
    const panel = params.get('panel');
    if (panel) {
      setTimeout(() => {
        if (panel === 'set') openSet(params.get('tab') || 'play');
        else openSide(panel);
      }, 900);
    }
    // ?tip=0.4 让进度条预览停在 40% 的位置，方便截图核对
    const tipAt = params.get('tip');
    if (tipAt) {
      setTimeout(() => {
        const b = $('#p-progress').getBoundingClientRect();
        showTip(b.left + b.width * parseFloat(tipAt));
      }, 2600);
    }
  }
  if (s.sync && s.sync.running) { state.syncing = true; pollSync(); }
})();
