'use strict';

/* ====================== 小工具 ====================== */

const $ = (id) => document.getElementById(id);
const SVGNS = 'http://www.w3.org/2000/svg';

function el(tag, attrs = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') n.className = v;
    else if (k === 'html') n.innerHTML = v;
    else if (k.startsWith('on')) n.addEventListener(k.slice(2), v);
    else if (v != null) n.setAttribute(k, v);
  }
  for (const c of kids.flat()) if (c != null) n.append(c);
  return n;
}

function svg(tag, attrs = {}, ...kids) {
  const n = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v != null) n.setAttribute(k, v);
  for (const c of kids.flat()) if (c != null) n.append(c);
  return n;
}

// 極小的殘差一律視為 0，避免出現 "-0.00%" 這種讀起來像有問題的顯示
const z = (x) => (Math.abs(x) < 5e-5 ? 0 : x);
const pct = (x, d = 2) => (x == null || !isFinite(x)) ? '—' : (z(x) * 100).toFixed(d) + '%';
const pctS = (x, d = 2) => (x == null || !isFinite(x)) ? '—'
  : (z(x) >= 0 ? '+' : '') + (z(x) * 100).toFixed(d) + '%';
const money = (x, d = 0) => (x == null || !isFinite(x)) ? '—'
  : x.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
const num = (x, d = 2) => (x == null || !isFinite(x)) ? '—' : x.toFixed(d);
const sign = (x) => x > 0 ? 'pos' : (x < 0 ? 'neg' : '');

const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

/* ====================== 圖表 ====================== */

function niceTicks(lo, hi, count = 6) {
  if (!isFinite(lo) || !isFinite(hi) || lo === hi) return [lo];
  const raw = (hi - lo) / count;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const norm = raw / mag;
  const step = (norm >= 5 ? 10 : norm >= 2 ? 5 : norm >= 1 ? 2 : 1) * mag;
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-9; v += step) {
    out.push(Math.abs(v) < step * 1e-9 ? 0 : v);
  }
  return out;
}

/**
 * 通用折線圖。
 * opts: {series, hlines, vlines, markers, xDomain, yDomain, xFmt, yFmt,
 *        xTickVals, height, pad}
 */
function lineChart(opts) {
  const W = 900;
  const H = opts.height || 320;
  const pad = Object.assign({ l: 58, r: 16, t: 14, b: 34 }, opts.pad || {});
  const iw = W - pad.l - pad.r;
  const ih = H - pad.t - pad.b;

  const all = opts.series.flatMap((s) => s.points);
  const xs = all.map((p) => p[0]);
  const ys = all.map((p) => p[1]);
  let [x0, x1] = opts.xDomain || [Math.min(...xs), Math.max(...xs)];
  let [y0, y1] = opts.yDomain || [Math.min(...ys), Math.max(...ys)];
  for (const h of opts.hlines || []) { y0 = Math.min(y0, h.y); y1 = Math.max(y1, h.y); }
  if (y0 === y1) { y0 -= 0.5; y1 += 0.5; }
  const padY = (y1 - y0) * 0.08;
  y0 -= padY; y1 += padY;
  if (x0 === x1) x1 = x0 + 1;

  const X = (v) => pad.l + ((v - x0) / (x1 - x0)) * iw;
  const Y = (v) => pad.t + ih - ((v - y0) / (y1 - y0)) * ih;

  const g = svg('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img' });
  const line = css('--line'), ink2 = css('--ink-2'), ink3 = css('--ink-3');

  // 網格與 Y 軸
  for (const t of niceTicks(y0 + padY, y1 - padY, 6)) {
    g.append(svg('line', { x1: pad.l, x2: W - pad.r, y1: Y(t), y2: Y(t), stroke: line }));
    g.append(svg('text', {
      x: pad.l - 8, y: Y(t) + 4, 'text-anchor': 'end',
      'font-size': 11, fill: ink2, 'font-family': 'ui-monospace, monospace',
    }, (opts.yFmt || num)(t)));
  }

  // X 軸
  const xticks = opts.xTickVals || niceTicks(x0, x1, 7);
  for (const t of xticks) {
    if (t < x0 || t > x1) continue;
    g.append(svg('line', {
      x1: X(t), x2: X(t), y1: pad.t, y2: pad.t + ih, stroke: line, 'stroke-dasharray': '2 4',
    }));
    g.append(svg('text', {
      x: X(t), y: H - 12, 'text-anchor': 'middle',
      'font-size': 11, fill: ink2, 'font-family': 'ui-monospace, monospace',
    }, (opts.xFmt || num)(t)));
  }

  // 零軸
  if (y0 < 0 && y1 > 0) {
    g.append(svg('line', {
      x1: pad.l, x2: W - pad.r, y1: Y(0), y2: Y(0), stroke: ink3, 'stroke-width': 1.4,
    }));
  }

  // 參考線本身畫在資料線下方；標籤則收集起來最後再疊上去，
  // 否則資料線會從字上穿過去而看不清楚。
  const labels = [];

  for (const h of opts.hlines || []) {
    g.append(svg('line', {
      x1: pad.l, x2: W - pad.r, y1: Y(h.y), y2: Y(h.y),
      stroke: h.color || ink3, 'stroke-width': 1.4, 'stroke-dasharray': h.dash || '6 4',
    }));
    if (h.label) {
      const w = h.label.length * 7.6 + 10;
      labels.push(svg('rect', {
        x: W - pad.r - w, y: Y(h.y) - 17, width: w, height: 16,
        fill: css('--panel'), rx: 3,
      }));
      labels.push(svg('text', {
        x: W - pad.r - 5, y: Y(h.y) - 5, 'text-anchor': 'end',
        'font-size': 11, fill: h.color || ink2,
      }, h.label));
    }
  }

  for (const v of opts.vlines || []) {
    g.append(svg('line', {
      x1: X(v.x), x2: X(v.x), y1: pad.t, y2: pad.t + ih,
      stroke: v.color || ink3, 'stroke-width': 1.2, 'stroke-dasharray': v.dash || '5 4',
    }));
    if (v.label) {
      const w = v.label.length * 7.6 + 10;
      labels.push(svg('rect', {
        x: X(v.x) + 2, y: pad.t + 1, width: w, height: 16, fill: css('--panel'), rx: 3,
      }));
      labels.push(svg('text', {
        x: X(v.x) + 6, y: pad.t + 13, 'font-size': 11, fill: v.color || ink2,
      }, v.label));
    }
  }

  // 資料線
  for (const s of opts.series) {
    if (!s.points.length) continue;
    const d = s.points.map((p, i) => `${i ? 'L' : 'M'}${X(p[0]).toFixed(2)},${Y(p[1]).toFixed(2)}`).join('');
    g.append(svg('path', {
      d, fill: 'none', stroke: s.color, 'stroke-width': s.width || 2,
      'stroke-dasharray': s.dash || null, 'stroke-linejoin': 'round',
    }));
  }

  // 事件標記
  for (const m of opts.markers || []) {
    const cx = X(m.x), cy = Y(m.y);
    if (m.shape === 'x') {
      const r = 5;
      g.append(svg('path', {
        d: `M${cx - r},${cy - r}L${cx + r},${cy + r}M${cx + r},${cy - r}L${cx - r},${cy + r}`,
        stroke: m.color, 'stroke-width': 2.4, 'stroke-linecap': 'round',
      }));
    } else {
      g.append(svg('circle', {
        cx, cy, r: 5, fill: 'none', stroke: m.color, 'stroke-width': 2.2,
      }));
    }
    if (m.title) g.append(svg('title', {}, m.title));
  }

  // 參考線標籤疊在資料線之上
  for (const n of labels) g.append(n);

  // 軸線
  g.append(svg('line', { x1: pad.l, x2: pad.l, y1: pad.t, y2: pad.t + ih, stroke: ink3 }));
  g.append(svg('line', {
    x1: pad.l, x2: W - pad.r, y1: pad.t + ih, y2: pad.t + ih, stroke: ink3,
  }));

  attachCrosshair(g, {
    pad, iw, ih, W, X, Y, x0, x1, y0, y1,
    series: opts.series,
    // 座標軸標籤求簡潔，十字線查價則求精確，因此可分別指定格式
    xFmt: opts.hoverXFmt || opts.xFmt || num,
    yFmt: opts.hoverYFmt || opts.yFmt || num,
  });

  return el('div', { class: 'chart' }, g);
}

/** 在資料點陣列中找出最接近 x 的索引（points 依 x 遞增）。 */
function nearestIndex(points, x) {
  let lo = 0, hi = points.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (points[mid][0] <= x) lo = mid; else hi = mid;
  }
  return Math.abs(points[lo][0] - x) <= Math.abs(points[hi][0] - x) ? lo : hi;
}

/**
 * 十字線查價：游標移動時鎖定最接近的 x，於各條線上標點並列出數值。
 */
function attachCrosshair(g, o) {
  const { pad, iw, ih, W, X, Y, x0, x1, series, xFmt, yFmt } = o;
  const live = series.filter((s) => s.points && s.points.length > 1);
  if (!live.length) return;

  const ink = css('--ink'), ink2 = css('--ink-2'), ink3 = css('--ink-3');
  const panel = css('--panel'), line = css('--line');

  const layer = svg('g', { style: 'pointer-events:none', visibility: 'hidden' });
  const vline = svg('line', {
    y1: pad.t, y2: pad.t + ih, stroke: ink3, 'stroke-width': 1, 'stroke-dasharray': '3 3',
  });
  const hline = svg('line', {
    x1: pad.l, x2: W - pad.r, stroke: ink3, 'stroke-width': 1, 'stroke-dasharray': '3 3',
  });
  layer.append(vline, hline);

  // 游標高度對應的 Y 軸讀值
  const yTagBg = svg('rect', { fill: ink3, rx: 3, height: 15, width: 52 });
  const yTag = svg('text', { 'font-size': 11, fill: panel, 'text-anchor': 'end' });
  layer.append(yTagBg, yTag);

  const dots = live.map((s) => svg('circle', {
    r: 4.5, fill: s.color, stroke: panel, 'stroke-width': 1.5,
  }));
  dots.forEach((d) => layer.append(d));

  const box = svg('rect', { fill: panel, stroke: line, 'stroke-width': 1, rx: 5, opacity: .97 });
  layer.append(box);
  const header = svg('text', { 'font-size': 11.5, fill: ink2, 'font-weight': 600 });
  layer.append(header);
  const swatches = live.map((s) => svg('rect', { width: 9, height: 3, rx: 1.5, fill: s.color }));
  const rows = live.map(() => svg('text', { 'font-size': 11.5, fill: ink }));
  swatches.forEach((s) => layer.append(s));
  rows.forEach((r) => layer.append(r));

  g.append(layer);

  // 透明感應區疊在最上層；layer 設了 pointer-events:none 所以不會擋住事件
  const hit = svg('rect', {
    x: pad.l, y: pad.t, width: iw, height: ih, fill: 'transparent',
    style: 'cursor:crosshair; touch-action:none',
  });
  g.append(hit);

  const toLocal = (ev) => {
    const m = g.getScreenCTM();
    if (!m) return null;
    const p = g.createSVGPoint();
    p.x = ev.clientX; p.y = ev.clientY;
    return p.matrixTransform(m.inverse());
  };

  function move(ev) {
    const loc = toLocal(ev);
    if (!loc) return;
    const cx = Math.min(Math.max(loc.x, pad.l), pad.l + iw);
    const cy = Math.min(Math.max(loc.y, pad.t), pad.t + ih);
    const xval = x0 + ((cx - pad.l) / iw) * (x1 - x0);

    // 以第一條線的取樣點為準鎖定 x，各線再取同一個 x 的最近點
    const anchor = live[0].points[nearestIndex(live[0].points, xval)][0];
    const px = X(anchor);
    vline.setAttribute('x1', px); vline.setAttribute('x2', px);
    hline.setAttribute('y1', cy); hline.setAttribute('y2', cy);

    yTagBg.setAttribute('x', pad.l - 56);
    yTagBg.setAttribute('y', cy - 7.5);
    yTag.setAttribute('x', pad.l - 8);
    yTag.setAttribute('y', cy + 4);
    yTag.textContent = yFmt(o.y0 + ((pad.t + ih - cy) / ih) * (o.y1 - o.y0));

    const vals = live.map((s) => {
      const i = nearestIndex(s.points, anchor);
      return s.points[i][1];
    });
    live.forEach((s, i) => {
      const d = dots[i];
      d.setAttribute('cx', px);
      d.setAttribute('cy', Y(vals[i]));
    });

    header.textContent = xFmt(anchor);
    live.forEach((s, i) => {
      rows[i].textContent = `${s.name}　${(s.hoverFmt || yFmt)(vals[i])}`;
    });

    // 首次顯示後才量得到文字寬度（SVG 需已在 DOM 中）
    const widths = [header.getComputedTextLength(),
      ...rows.map((r) => r.getComputedTextLength() + 14)];
    const bw = Math.max(...widths) + 22;      // 左右內距
    const bh = 20 + live.length * 16 + 8;

    const right = px + 14 + bw <= W - pad.r;
    const bx = right ? px + 14 : px - 14 - bw;
    const by = Math.min(Math.max(cy - bh / 2, pad.t + 2), pad.t + ih - bh - 2);

    box.setAttribute('x', bx); box.setAttribute('y', by);
    box.setAttribute('width', bw); box.setAttribute('height', bh);
    header.setAttribute('x', bx + 10); header.setAttribute('y', by + 15);
    live.forEach((s, i) => {
      const ty = by + 33 + i * 16;
      swatches[i].setAttribute('x', bx + 10);
      swatches[i].setAttribute('y', ty - 4);
      rows[i].setAttribute('x', bx + 24);
      rows[i].setAttribute('y', ty);
    });

    layer.setAttribute('visibility', 'visible');
  }

  hit.addEventListener('pointermove', move);
  hit.addEventListener('pointerdown', move);
  hit.addEventListener('pointerleave', () => layer.setAttribute('visibility', 'hidden'));
}

/** 直方圖（給定 edges 與 counts）。 */
function histChart(hist, opts = {}) {
  const W = 900, H = opts.height || 260;
  const pad = { l: 58, r: 16, t: 14, b: 34 };
  const iw = W - pad.l - pad.r, ih = H - pad.t - pad.b;
  const { edges, counts } = hist;
  const x0 = edges[0], x1 = edges[edges.length - 1];
  const ymax = Math.max(...counts, 1);
  const X = (v) => pad.l + ((v - x0) / (x1 - x0 || 1)) * iw;
  const Y = (v) => pad.t + ih - (v / ymax) * ih;

  const g = svg('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img' });
  const line = css('--line'), ink2 = css('--ink-2'), ink3 = css('--ink-3');

  for (const t of niceTicks(0, ymax, 5)) {
    g.append(svg('line', { x1: pad.l, x2: W - pad.r, y1: Y(t), y2: Y(t), stroke: line }));
    g.append(svg('text', {
      x: pad.l - 8, y: Y(t) + 4, 'text-anchor': 'end', 'font-size': 11, fill: ink2,
    }, Math.round(t).toLocaleString()));
  }

  for (let i = 0; i < counts.length; i++) {
    const a = X(edges[i]), b = X(edges[i + 1]);
    const mid = (edges[i] + edges[i + 1]) / 2;
    g.append(svg('rect', {
      x: a, y: Y(counts[i]), width: Math.max(b - a - 0.6, 0.6), height: pad.t + ih - Y(counts[i]),
      fill: mid < 0 ? css('--bad') : css('--ok'), opacity: .78,
    }, svg('title', {}, `${pct(mid, 1)}：${counts[i].toLocaleString()} 條`)));
  }

  for (const t of niceTicks(x0, x1, 7)) {
    if (t < x0 || t > x1) continue;
    g.append(svg('text', {
      x: X(t), y: H - 12, 'text-anchor': 'middle', 'font-size': 11, fill: ink2,
    }, pct(t, 0)));
  }
  if (x0 < 0 && x1 > 0) {
    g.append(svg('line', {
      x1: X(0), x2: X(0), y1: pad.t, y2: pad.t + ih, stroke: ink3, 'stroke-width': 1.4,
    }));
  }
  for (const m of opts.marks || []) {
    if (m.x < x0 || m.x > x1) continue;
    g.append(svg('line', {
      x1: X(m.x), x2: X(m.x), y1: pad.t, y2: pad.t + ih,
      stroke: m.color, 'stroke-width': 1.6, 'stroke-dasharray': '5 3',
    }));
    g.append(svg('text', {
      x: X(m.x) + 4, y: pad.t + 12, 'font-size': 11, fill: m.color,
    }, m.label));
  }
  g.append(svg('line', {
    x1: pad.l, x2: W - pad.r, y1: pad.t + ih, y2: pad.t + ih, stroke: ink3,
  }));
  return el('div', { class: 'chart' }, g);
}

function legend(items) {
  return el('div', { class: 'legend' },
    items.map((i) => el('span', {},
      el('i', { class: i.dash ? 'dash' : '', style: `background:${i.color};color:${i.color}` }),
      i.name)));
}

function probBars(probs) {
  const colors = {
    '提前出場': css('--s2'),
    '到期還本（未觸及生效）': css('--ok'),
    '到期還本（期末>=執行價）': css('--s3'),
    '到期承接股票': css('--bad'),
  };
  const max = Math.max(...Object.values(probs), 0.01);
  return el('div', { class: 'bars' },
    Object.entries(probs).map(([k, v]) => el('div', { class: 'bar-row' },
      el('div', { class: 'bar-track' },
        el('div', {
          class: 'bar-fill',
          style: `width:${(v / max * 100).toFixed(1)}%;background:${colors[k] || css('--ink-3')};opacity:.35`,
        }),
        el('span', { class: 'bar-label' }, k)),
      el('div', { class: 'bar-val' }, pct(v, 1)))));
}

function kpi(k, v, s, cls) {
  return el('div', { class: 'kpi ' + (cls || '') },
    el('div', { class: 'k' }, k), el('div', { class: 'v' }, v),
    s ? el('div', { class: 's' }, s) : null);
}

/**
 * 表格。第一欄靠左、其餘靠右，表頭與內容採同一套對齊規則
 * （若讓文字儲存格自行靠左，欄位看起來就會與表頭錯位）。
 */
function dataTable(headers, rows) {
  return el('table', { class: 'data' },
    el('thead', {}, el('tr', {}, headers.map((h) => el('th', {}, h)))),
    el('tbody', {}, rows.map((r) => el('tr', {}, r.map((c) =>
      el('td', {}, (c && c.nodeType) ? c : String(c)))))));
}

/* ====================== 表單 ====================== */

const isUpside = () => $('f-product').value === 'upside';

function syncForm() {
  const up = isUpside();
  $('f-participation').disabled = !up;
  const td = $('td-lc');
  if (up && !td.querySelector('input')) {
    td.className = '';
    td.replaceChildren(el('input', { id: 'f-lowercall', value: '103', inputmode: 'decimal' }));
    td.querySelector('input').addEventListener('input', markBlanks);
  } else if (!up && td.querySelector('input')) {
    td.className = 'na';
    td.replaceChildren('NA');
  }
  const kiOff = $('f-kitype').value === 'NA';
  const tdki = $('td-ki');
  if (kiOff && !tdki.classList.contains('na')) {
    tdki.dataset.saved = $('f-ki').value;
    tdki.className = 'na';
    tdki.replaceChildren('NA');
  } else if (!kiOff && tdki.classList.contains('na')) {
    tdki.className = '';
    tdki.replaceChildren(el('input', {
      id: 'f-ki', value: tdki.dataset.saved || '60', inputmode: 'decimal',
    }));
    tdki.querySelector('input').addEventListener('input', markBlanks);
  }
  markBlanks();
}

const BLANKABLE = ['f-coupon', 'f-strike', 'f-autocall', 'f-ki', 'f-rebate', 'f-lowercall'];

const FIELD_LABELS = {
  'f-coupon': '年化配息 (Cpn p.a.)',
  'f-strike': '執行價 (Put Strike)',
  'f-autocall': '提前出場價 (Autocall)',
  'f-ki': '下限價 (KI Level)',
  'f-rebate': '行銷通路費 (Rebate)',
  'f-lowercall': '參與表現價 (Lower Call Strike)',
};

function markBlanks() {
  const blanks = [];
  for (const id of BLANKABLE) {
    const n = $(id);
    if (!n) continue;
    const empty = !n.value.trim();
    n.classList.toggle('blank', empty);
    if (empty) blanks.push(id);
  }

  const s = $('solve-state');
  if (!s) return;
  if (blanks.length === 0) {
    s.className = 'state plain';
    s.innerHTML = '所有欄位皆已填寫 —— 直接以此條件定價，並比對報價是否合理。'
      + '若要讓系統試算年化配息，把 <b>Cpn p.a.</b> 欄位清空即可。';
  } else if (blanks.length === 1) {
    s.className = 'state';
    s.innerHTML = `留白詢價欄位：<b>${FIELD_LABELS[blanks[0]]}</b> —— 送出後由系統試算此欄位。`;
  } else {
    s.className = 'state bad';
    s.innerHTML = '一次只能留白一個欄位詢價，目前留白：<b>'
      + blanks.map((b) => FIELD_LABELS[b]).join('、') + '</b>';
  }
}

function payload() {
  const uds = ['f-ud1', 'f-ud2', 'f-ud3', 'f-ud4']
    .map((i) => $(i).value.trim()).filter(Boolean);
  const v = (id) => { const n = $(id); return n ? n.value.trim() : ''; };
  return {
    underlyings: uds,
    currency: v('f-ccy') || 'USD',
    tenor_months: Number(v('f-tenor') || 12),
    coupon_pa: v('f-coupon'),
    issue_delay_bd: Number(v('f-idelay') || 5),
    coupon_freq: $('f-freq').value,
    strike_pct: v('f-strike'),
    ko_type: $('f-kotype').value,
    autocall_pct: v('f-autocall'),
    ki_type: $('f-kitype').value,
    ki_pct: v('f-ki'),
    upside: isUpside(),
    lower_call_strike_pct: v('f-lowercall'),
    participation: v('f-participation') || '100',
    rebate: v('f-rebate'),
    notional: Number(v('f-notional') || 100000),
    ko_lockout_months: Number(v('f-lockout') || 1),
    lookback_years: Number(v('f-lookback') || 2),
    rate: v('f-rate'),
    paths: Number($('f-paths').value),
    autocall_coupon: $('f-accrual').value,
  };
}

function setForm(o) {
  for (const [id, val] of Object.entries(o)) {
    const n = $(id);
    if (n) n.value = val;
  }
  syncForm();
}

/* ====================== API ====================== */

function showMsg(kind, text, list) {
  $('msg-box').replaceChildren(el('div', { class: 'msg ' + kind },
    el('div', {}, text),
    list && list.length ? el('ul', {}, list.map((s) => el('li', {}, s))) : null));
}
const clearMsg = () => $('msg-box').replaceChildren();

async function call(path, body, btn, statusId, label) {
  const st = $(statusId);
  btn.disabled = true;
  st.replaceChildren(el('span', { class: 'spinner' }), label + ' 計算中…');
  clearMsg();
  const t0 = performance.now();
  try {
    const r = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const j = await r.json();
    if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status));
    st.textContent = `完成（${((performance.now() - t0) / 1000).toFixed(1)} 秒）`;
    return j;
  } catch (e) {
    st.textContent = '';
    showMsg('err', e.message);
    return null;
  } finally {
    btn.disabled = false;
  }
}

/* ====================== 詢價結果 ====================== */

function renderQuote(d) {
  const out = $('quote-out');
  const t = d.terms, p = d.pricing, f = d.forecast;
  const parts = [];

  if (t.platform_warnings.length) {
    showMsg('warn', '解出的條款超出詢價平台可受理範圍：', t.platform_warnings);
  }

  /* --- 詢價結果 --- */
  if (d.solve) {
    parts.push(el('section', { class: 'panel' },
      el('h2', {}, '詢價結果（留白欄位求解）'),
      el('div', { class: 'kpis' },
        kpi('留白欄位', d.solve.label),
        kpi('求得數值', pct(d.solve.value, 4), null, 'hero'),
        kpi('對應理論價值', pct(d.solve.pv, 4), `目標 ${pct(d.solve.target)}`),
        kpi('求解方式', d.solve.iterations ? `二分搜尋 ${d.solve.iterations} 次`
          : '封閉解', `${d.solve.n_paths.toLocaleString()} 條路徑`))));
  }

  /* --- 定價 --- */
  const gapCls = p.value_gap == null ? '' : (p.value_gap < -0.005 ? 'bad' : 'good');
  parts.push(el('section', { class: 'panel' },
    el('h2', {}, '風險中性定價'),
    el('div', { class: 'kpis' },
      kpi('公允年化配息率', pct(p.fair_coupon_pa), p.rebate ? `已扣 ${pct(p.rebate)} 通路費` : null, 'hero'),
      kpi('券商報價配息率', pct(p.quoted_coupon_pa),
        p.quoted_coupon_pa == null ? null
          : `缺口 ${pctS(p.quoted_coupon_pa - p.fair_coupon_pa)}`),
      kpi('理論價值', pct(p.pv_at_quote), '面額 100%', gapCls),
      kpi('承接股票機率', pct(p.prob_delivery), null, p.prob_delivery > 0.2 ? 'bad' : ''),
      kpi('觸及生效機率', t.ki_type === 'NA' ? '—' : pct(p.prob_ki), t.ki_type === 'NA' ? '無 KI 條件' : null),
      kpi('預期存續期間', num(p.expected_life_years, 2) + ' 年', `契約 ${num(t.tenor_months / 12, 2)} 年`)),
    p.implied_total_fee == null ? null : el('div', {},
      el('h3', {}, '價值差距分解'),
      dataTable(['項目', '面額比例', '說明'], [
        ['報價隱含總抽成', el('span', { class: 'neg' }, pct(p.implied_total_fee)), '100% − 理論價值'],
        ['報價單載明通路費', pct(p.rebate), 'Rebate 欄位'],
        ['發行商保留', pct(p.issuer_margin), '避險成本 + 利潤'],
      ])),
    el('h3', {}, '情境機率（風險中性測度）'),
    probBars(p.scenario_probs),
    el('p', { class: 'note' },
      `蒙地卡羅 ${p.n_paths.toLocaleString()} 條路徑。`
      + `未扣通路費的毛公允配息率為 ${pct(p.fair_coupon_gross)}。`)));

  /* --- 條款價位 --- */
  parts.push(el('section', { class: 'panel' },
    el('h2', {}, '契約日程與價位'),
    el('div', { class: 'grid2' },
      el('div', {},
        el('h3', {}, '關鍵日期'),
        dataTable(['項目', '日期'], [
          ['交易日 / 期初定價', d.schedule.trade_date],
          ['發行日', d.schedule.issue_date],
          ['期末評價日', d.schedule.final_valuation],
          ['KO 觀察期間', d.schedule.ko_days
            ? `${d.schedule.ko_start} ~ ${d.schedule.ko_end}（${d.schedule.ko_days} 日）` : '無'],
          ['KI 觀察期間', d.schedule.ki_days
            ? `${d.schedule.ki_start} ~ ${d.schedule.ki_end}（${d.schedule.ki_days} 日）` : '無'],
          ['配息', `${t.n_coupons} 期，每期 ${money(t.coupon_per_period)} ${t.currency}`],
        ])),
      el('div', {},
        el('h3', {}, '各標的價位'),
        dataTable(
          ['標的', '期初價', '提前出場價', t.lower_call_strike_pct ? '參與表現價' : null,
            '執行價', '下限價'].filter(Boolean),
          d.levels.map((L) => [L.symbol, num(L.initial), num(L.ko),
            t.lower_call_strike_pct ? num(L.lower_call) : null,
            num(L.strike), L.ki == null ? '—' : num(L.ki)].filter((x) => x !== null)))))));

  /* --- 市場參數 --- */
  const m = d.market;
  parts.push(el('section', { class: 'panel' },
    el('h2', {}, '市場參數校準'),
    el('div', { class: 'grid2' },
      el('div', {},
        el('h3', {}, '波動度與股息'),
        dataTable(['標的', '現價', '年化波動', '股息殖利率'],
          m.symbols.map((s, i) => [s, num(m.spot[i]), pct(m.vol[i]), pct(m.div_yield[i])]))),
      el('div', {},
        el('h3', {}, '相關係數矩陣'),
        dataTable(['', ...m.symbols],
          m.symbols.map((s, i) => [s, ...m.corr[i].map((c) => num(c))])))),
    el('p', { class: 'note' },
      `無風險利率 ${pct(m.rate)}（^IRX 13 週美國國庫券）。`
      + '波動度與相關性以還原股息收盤價估算；障礙判定則使用未還原股息的實際收盤價。')));

  /* --- 到期損益曲線 --- */
  const pf = d.payoff;
  const xf = (v) => (v * 100).toFixed(0) + '%';
  const vlines = [
    { x: pf.strike_pct, label: '執行價', color: css('--warn') },
    { x: pf.autocall_pct, label: '提前出場價', color: css('--s2') },
  ];
  if (pf.has_ki) vlines.push({ x: pf.ki_pct, label: '下限價', color: css('--bad') });
  if (pf.lower_call_pct) vlines.push({ x: pf.lower_call_pct, label: '參與表現價', color: css('--s4') });

  const series = [
    { name: '直接持有最差標的', color: css('--ink-3'), points: pf.x.map((x, i) => [x, pf.buy_hold[i]]), dash: '5 4' },
    { name: '已觸及生效', color: css('--bad'), points: pf.x.map((x, i) => [x, pf.ki_hit[i]]), width: 2.4 },
  ];
  if (pf.has_ki) {
    series.push({ name: '未觸及生效', color: css('--ok'), points: pf.x.map((x, i) => [x, pf.no_ki[i]]), width: 2.4 });
  }

  parts.push(el('section', { class: 'panel' },
    el('h2', {}, '到期損益曲線'),
    lineChart({
      series, vlines, xFmt: xf, yFmt: (v) => (v * 100).toFixed(0) + '%', height: 340,
      hoverXFmt: (v) => '最差標的期末表現 ' + pct(v, 1),
      hoverYFmt: (v) => pctS(v, 2),
      hlines: [{ y: pf.coupon_total, label: `配息上限 ${pct(pf.coupon_total)}`, color: css('--ink-3') }],
    }),
    legend([...series.map((s) => ({ name: s.name, color: s.color, dash: !!s.dash })),
      ...vlines.map((v) => ({ name: v.label, color: v.color, dash: true }))]),
    el('p', { class: 'note' },
      '將游標移到圖上可用十字線查價。'
      + '橫軸為期末「表現最差標的」相對期初的表現，縱軸為總報酬（含配息）。'
      + (pf.has_ki
        ? '綠線為期間未跌破下限價的情形（無論期末多低都全額還本）；紅線為曾跌破下限價的情形。兩線之間的落差就是下限保護的價值，也是所謂懸崖式風險。'
        : '本商品無下限保護，期末低於執行價即承接股票。'))));

  /* --- 損益分布 --- */
  parts.push(el('section', { class: 'panel' },
    el('h2', {}, '損益分布預測（真實機率測度）'),
    el('div', { class: 'kpis' },
      kpi('平均報酬', pctS(f.summary['平均報酬']), null, sign(f.summary['平均報酬'])),
      kpi('中位數報酬', pctS(f.summary['中位數報酬'])),
      kpi('勝率', pct(f.summary['勝率'], 1), '報酬 > 0 的比例'),
      kpi('5% VaR', pctS(f.summary['5% VaR']), null, sign(f.summary['5% VaR'])),
      kpi('1% VaR', pctS(f.percentiles.P1), null, sign(f.percentiles.P1)),
      kpi('最差情形', pctS(f.summary['最差']), null, 'bad')),
    el('h3', {}, '報酬分布'),
    histChart(f.histogram, {
      marks: [{ x: f.summary['平均報酬'], label: '平均', color: css('--s2') }],
    }),
    el('p', { class: 'note' },
      '紅色為虧損區間、綠色為獲利區間。典型的 FCN 呈現「高勝率、負偏態」：'
      + '大量路徑集中在配息上限附近，少數路徑因承接股票而大幅虧損。'),
    el('h3', {}, '情境機率'),
    probBars(f.scenario_probs),
    el('h3', {}, '報酬分位數'),
    dataTable(['分位數', '報酬率'],
      Object.entries(f.percentiles).map(([k, v]) =>
        [k, el('span', { class: sign(v) }, pctS(v))])),
    el('p', { class: 'note' },
      `對照：同期直接持有最差標的的平均報酬為 `
      + `${pctS(f.summary['對照:直接持有最差標的平均報酬'])}。`)));

  out.replaceChildren(...parts);
}

/* ====================== 回測結果 ====================== */

function renderBacktest(d) {
  const s = d.summary;
  const parts = [];

  parts.push(el('section', { class: 'panel' },
    el('h2', {}, '回測總覽'),
    el('div', { class: 'kpis' },
      kpi('樣本數', s['樣本數'].toLocaleString(), '個進場日'),
      kpi('平均報酬', pctS(s['平均報酬']), null, sign(s['平均報酬'])),
      kpi('中位數報酬', pctS(s['中位數報酬'])),
      kpi('勝率', pct(s['勝率'], 1)),
      kpi('最差報酬', pctS(s['最差報酬']), null, 'bad'),
      kpi('5% VaR', pctS(s['5% VaR']), null, sign(s['5% VaR'])),
      kpi('承接股票比例', pct(s['承接股票比例'], 1), null, s['承接股票比例'] > 0.1 ? 'bad' : ''),
      kpi('提前出場比例', pct(s['提前出場比例'], 1)),
      kpi('平均持有天數', Math.round(s['平均持有天數']) + ' 天'),
      kpi('勝過直接持有比例', pct(s['勝過直接持有比例'], 1),
        `直接持有平均 ${pctS(s['對照:直接持有最差標的平均報酬'])}`)),
    el('p', { class: 'note' },
      `使用配息率 ${pct(d.terms.coupon_pa)}。`
      + '每個進場日都持有到提前出場或到期，只計入資料足以走完整份契約的進場日。')));

  parts.push(el('section', { class: 'panel' },
    el('h2', {}, '各情境明細'),
    dataTable(['情境', '次數', '占比', '平均報酬', '最差', '最佳'],
      d.breakdown.map((b) => [b.scenario, b.count, pct(b.share, 1),
        el('span', { class: sign(b.mean) }, pctS(b.mean)),
        el('span', { class: sign(b.min) }, pctS(b.min)),
        el('span', { class: sign(b.max) }, pctS(b.max))])),
    el('h3', {}, '報酬分布'),
    histChart(d.histogram)));

  // 逐進場日報酬時間序列
  const rows = d.rows;
  const idx = rows.map((_, i) => i);
  parts.push(el('section', { class: 'panel' },
    el('h2', {}, '逐進場日結果'),
    lineChart({
      height: 300,
      series: [
        {
          name: 'FCN 報酬', color: css('--s2'),
          points: idx.map((i) => [i, rows[i].return]), width: 1.6,
        },
        {
          name: '直接持有最差標的', color: css('--ink-3'), dash: '4 4',
          points: idx.map((i) => [i, rows[i].buy_hold]), width: 1.4,
        },
      ],
      xTickVals: idx.filter((i) => i % Math.max(1, Math.floor(rows.length / 8)) === 0),
      xFmt: (i) => (rows[Math.round(i)] || {}).trade_date || '',
      yFmt: (v) => (v * 100).toFixed(0) + '%',
      hoverXFmt: (i) => {
        const r = rows[Math.round(i)] || {};
        return `${r.trade_date}　${r.scenario || ''}`;
      },
      hoverYFmt: (v) => pctS(v, 2),
    }),
    legend([
      { name: 'FCN 報酬', color: css('--s2') },
      { name: '直接持有最差標的', color: css('--ink-3'), dash: true },
    ]),
    el('h3', {}, `明細（${rows.length} 筆）`),
    el('div', { class: 'scroll' },
      dataTable(['交易日', '出場日', '情境', '持有天數', '報酬率', '直接持有', '最差標的', '曾觸KI', '承接'],
        rows.map((r) => [r.trade_date, r.exit_date, r.scenario, r.days,
          el('span', { class: sign(r.return) }, pctS(r.return)),
          el('span', { class: sign(r.buy_hold) }, pctS(r.buy_hold)),
          r.worst, r.ki ? '是' : '—', r.delivered || '—'])))));

  $('backtest-out').replaceChildren(...parts);
}

/* ====================== 路徑檢視 ====================== */

function renderPath(d) {
  const o = d.outcome, L = d.levels, t = d.terms;
  const dates = d.dates;
  const syms = Object.keys(d.series);
  const colors = [css('--s3'), css('--s2'), css('--s1'), css('--s4')];

  const series = syms.map((s, i) => ({
    name: s, color: colors[i % colors.length],
    points: d.series[s].map((v, j) => [j, v]), width: 1.8,
    // 查價時同時給出相對期初的表現與當日實際收盤價
    hoverFmt: (v) => `${pct(v, 2)}　${num(v * o.initial_prices[s])}`,
  }));

  const hlines = [
    { y: L.ko, label: `提前出場價 ${pct(L.ko, 0)}`, color: css('--s2') },
    { y: L.strike, label: `執行價 ${pct(L.strike, 0)}`, color: css('--warn') },
  ];
  if (L.ki != null) hlines.push({ y: L.ki, label: `下限價 ${pct(L.ki, 0)}`, color: css('--bad') });
  if (L.lower_call != null) {
    hlines.push({ y: L.lower_call, label: `參與表現價 ${pct(L.lower_call, 0)}`, color: css('--s4') });
  }

  const markers = [];
  for (const [s, dt] of Object.entries(d.events.memory)) {
    if (!dt) continue;
    const j = dates.indexOf(dt);
    if (j >= 0) markers.push({ x: j, y: d.series[s][j], color: css('--s2'), shape: 'o', title: `${s} 記憶事件 ${dt}` });
  }
  for (const [s, dt] of Object.entries(d.events.ki)) {
    if (!dt) continue;
    const j = dates.indexOf(dt);
    if (j >= 0) markers.push({ x: j, y: d.series[s][j], color: css('--bad'), shape: 'x', title: `${s} 觸及生效 ${dt}` });
  }

  const step = Math.max(1, Math.floor(dates.length / 8));
  const parts = [];

  parts.push(el('section', { class: 'panel' },
    el('h2', {}, `價格走勢與事件（${d.schedule.trade_date} 進場）`),
    lineChart({
      series, hlines, markers, height: 380,
      xTickVals: dates.map((_, i) => i).filter((i) => i % step === 0),
      xFmt: (i) => (dates[Math.round(i)] || '').slice(2),
      yFmt: (v) => (v * 100).toFixed(0) + '%',
      hoverXFmt: (i) => dates[Math.round(i)] || '',
      hoverYFmt: (v) => pct(v, 2),
    }),
    legend([
      ...series.map((s) => ({ name: s.name, color: s.color })),
      ...hlines.map((h) => ({ name: h.label, color: h.color, dash: true })),
      { name: '○ 記憶事件', color: css('--s2') },
      { name: '✕ 觸及生效事件', color: css('--bad') },
    ]),
    el('p', { class: 'note' },
      '縱軸為各標的相對期初價的表現（期初 = 100%）。將游標移到圖上可用十字線查價。')));

  const isDel = o.is_delivery;
  parts.push(el('section', { class: 'panel' },
    el('h2', {}, '給付結果'),
    el('div', { class: 'kpis' },
      kpi('情境', o.scenario, null, isDel ? 'bad' : 'good'),
      kpi('出場日', o.exit_date, `持有 ${o.days_held} 天`),
      kpi('總報酬率', pctS(o.return_pct), `年化 ${pctS(o.annualized_pct)}`,
        o.return_pct >= 0 ? 'good' : 'bad'),
      kpi('總價值', money(o.total_value) + ' ' + t.currency,
        `損益 ${o.pnl >= 0 ? '+' : ''}${money(o.pnl)}`),
      kpi('對照：直接持有 ' + o.worst_symbol, pctS(o.buy_hold_return), null,
        sign(o.buy_hold_return)),
      kpi('相對優劣', pctS(o.return_pct - o.buy_hold_return),
        o.return_pct > o.buy_hold_return ? 'FCN 勝出' : 'FCN 落後',
        o.return_pct > o.buy_hold_return ? 'good' : 'bad')),
    el('h3', {}, '現金流'),
    dataTable(['項目', '金額 ' + t.currency], [
      ['配息合計', money(o.coupon_total)],
      ...(isDel ? [
        ['承接標的', o.delivered_symbol],
        ['承接價（執行價）', num(o.delivery_price)],
        ['承接股數', money(o.shares)],
        ['股票市值（期末）', money(o.stock_value)],
        ['零股現金找補', money(o.residual_cash, 2)],
      ] : [
        ['本金返還', money(o.principal_returned)],
        ...(o.upside_payment ? [['參與漲幅', money(o.upside_payment)]] : []),
      ]),
      ['總價值', money(o.total_value)],
    ]),
    el('h3', {}, '期末表現'),
    dataTable(['標的', '期初價', '期末價', '表現', '記憶事件', '觸及生效'],
      syms.map((s) => [s, num(o.initial_prices[s]), num(o.final_prices[s]),
        el('span', { class: sign(o.performances[s] - 1) }, pctS(o.performances[s] - 1)),
        d.events.memory[s] || '—', d.events.ki[s] || '—'])),
    o.warnings.length ? el('div', { class: 'msg warn', style: 'margin-top:14px' },
      el('div', {}, '條款提醒'),
      el('ul', {}, o.warnings.map((w) => el('li', {}, w)))) : null));

  $('path-out').replaceChildren(...parts);
}

/* ====================== 事件綁定 ====================== */

function showTab(name) {
  document.querySelectorAll('nav button').forEach(
    (x) => x.classList.toggle('on', x.dataset.tab === name));
  document.querySelectorAll('.tab').forEach(
    (t) => t.classList.toggle('on', t.id === 'tab-' + name));
  // 教學頁不需要報價條件表，藏起來避免干擾
  const learning = name === 'learn';
  $('terms-panel').style.display = learning ? 'none' : '';
  $('msg-box').style.display = learning ? 'none' : '';
  window.scrollTo({ top: 0 });
}

document.querySelectorAll('nav button').forEach((b) => {
  b.addEventListener('click', () => showTab(b.dataset.tab));
});

$('f-product').addEventListener('change', syncForm);
$('f-kitype').addEventListener('change', syncForm);
BLANKABLE.forEach((id) => { const n = $(id); if (n) n.addEventListener('input', markBlanks); });

function renderTickers(d) {
  const box = $('ticker-state');
  const cards = d.tickers.map((t) => t.ok
    ? el('div', { class: 'tk' },
      el('div', {}, el('span', { class: 'sym' }, t.symbol),
        ' ', t.name || '', ' ', el('span', { class: 'meta' }, `(${t.input})`)),
      el('div', { class: 'meta' },
        `${t.currency} ${num(t.last)} @ ${t.last_date}｜${t.exchange}`
        + `｜資料自 ${t.first_date}（${t.n_days.toLocaleString()} 個交易日）`))
    : el('div', { class: 'tk bad' },
      el('div', {}, el('span', { class: 'sym' }, t.input), ' 查無此標的'),
      el('div', { class: 'meta' }, t.error)));

  box.replaceChildren(
    el('div', { class: 'tickers' }, cards),
    ...(d.warnings || []).map((w) => el('div', { class: 'state bad' }, w)));
}

$('btn-check').addEventListener('click', async (e) => {
  const uds = ['f-ud1', 'f-ud2', 'f-ud3', 'f-ud4']
    .map((i) => $(i).value.trim()).filter(Boolean);
  const d = await call('/api/tickers', { underlyings: uds }, e.target, 'status-quote', '查驗');
  if (d) renderTickers(d);
});

$('btn-clear-coupon').addEventListener('click', () => {
  $('f-coupon').value = '';
  markBlanks();
  $('f-coupon').focus();
});

$('btn-quote').addEventListener('click', async (e) => {
  const d = await call('/api/quote', payload(), e.target, 'status-quote', '詢價');
  if (d) renderQuote(d);
});

$('btn-backtest').addEventListener('click', async (e) => {
  const body = Object.assign(payload(), {
    years: Number($('f-bt-years').value || 5),
    step: Number($('f-bt-step').value || 5),
  });
  const d = await call('/api/backtest', body, e.target, 'status-backtest', '回測');
  if (d) renderBacktest(d);
});

$('btn-path').addEventListener('click', async (e) => {
  const body = Object.assign(payload(), { trade_date: $('f-path-date').value });
  const d = await call('/api/path', body, e.target, 'status-path', '路徑');
  if (d) renderPath(d);
});

$('btn-preset-fcn').addEventListener('click', () => {
  $('f-product').value = 'fcn';
  $('f-kitype').value = 'AKI';
  syncForm();
  setForm({
    'f-ud1': 'NVDA UW', 'f-ud2': 'TSM UN', 'f-ud3': 'AMD UW', 'f-ud4': '',
    'f-tenor': '12', 'f-coupon': '', 'f-strike': '80', 'f-kotype': 'Daily Memory',
    'f-autocall': '100', 'f-ki': '75', 'f-rebate': '3',
  });
});

$('btn-preset-upside').addEventListener('click', () => {
  $('f-product').value = 'upside';
  $('f-kitype').value = 'NA';
  syncForm();
  setForm({
    'f-ud1': 'TSM UN', 'f-ud2': 'AMD UW', 'f-ud3': 'NVDA UW', 'f-ud4': '',
    'f-tenor': '12', 'f-coupon': '9', 'f-strike': '85', 'f-kotype': 'Monthly',
    'f-autocall': '100', 'f-lowercall': '103', 'f-rebate': '3', 'f-participation': '100',
  });
});

syncForm();


/* ====================== 教學頁互動試算 ====================== */

const NOTIONAL = 100_000;
const QUICK_PICKS = ['NVDA UW', 'TSM UN', 'AAPL UW', 'TSLA UW', 'MSFT UW', 'AMD UW'];

// 行情尚未載入前先用 100 元的示意價，讓頁面一進來就能操作
let learnStock = { symbol: '示意標的', name: '', price: 100, live: false };

function setStockInfo(html, bad) {
  const n = $('s-info');
  n.className = 'stockinfo' + (bad ? ' bad' : '');
  n.innerHTML = html;
}

async function loadLearnStock(ticker) {
  $('s-ticker').value = ticker;
  document.querySelectorAll('#quick-picks button').forEach(
    (b) => b.classList.toggle('on', b.dataset.t === ticker));
  setStockInfo('查詢中…');
  try {
    const r = await fetch('/api/tickers', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ underlyings: [ticker] }),
    });
    const j = await r.json();
    const t = (j.tickers || [])[0];
    if (!r.ok || !t || !t.ok) throw new Error((t && t.error) || j.error || '查無此標的');
    learnStock = { symbol: t.symbol, name: t.name, price: t.last, live: true };
    setStockInfo(
      `<b>${t.symbol}</b> ${t.name || ''}<br>`
      + `現價 <span class="px">${num(t.last)}</span> ${t.currency}`
      + `　<span style="color:var(--ink-3)">${t.last_date} 收盤</span>`);
  } catch (e) {
    learnStock = { symbol: '示意標的', name: '', price: 100, live: false };
    setStockInfo(`${e.message}　—　暫以每股 100 元的示意價試算`, true);
  }
  learnSim();
}

function learnSim() {
  const cpn = +$('s-cpn').value / 100;
  const strike = +$('s-strike').value / 100;
  const ki = +$('s-ki').value / 100;
  const final = +$('s-final').value / 100;
  const breached = $('s-breach').checked;
  const spot = learnStock.price;

  $('l-cpn').textContent = pct(cpn, 1);
  $('l-strike').textContent = pct(strike, 0);
  $('l-ki').textContent = pct(ki, 0);
  $('l-buffer').textContent = pct(1 - strike, 0);
  $('l-coupon-amt').textContent = money(NOTIONAL * cpn);
  $('l-strike-px').textContent = num(spot * strike);
  $('l-ki-px').textContent = num(spot * ki);
  $('l-final').textContent = num(spot * final);
  $('l-final-pct').textContent = pct(final, 0);
  $('l-table-sym').textContent = learnStock.live ? `（${learnStock.symbol}）` : '';

  const r = learnOutcome(cpn, strike, final, breached, spot);
  const hold = final - 1;

  $('v-scenario').textContent = r.scenario;
  $('v-scenario').className = 'verdict' + (r.deliver ? ' risk' : '');

  $('v-fcn').textContent = pctS(r.ret);
  $('v-fcn').className = 'big ' + (r.ret >= 0 ? 'pos' : 'neg');
  $('v-fcn-amt').textContent = `${money(NOTIONAL)} → ${money(r.value)}`;
  $('v-fcn-detail').textContent = r.deliver
    ? `承接 ${learnStock.symbol} ${num(r.shares, 1)} 股 @ ${num(spot * strike)}`
      + `　市值 ${money(r.stockValue)}　＋配息 ${money(r.coupon)}`
    : `本金 ${money(NOTIONAL)}　＋配息 ${money(r.coupon)}`;

  $('v-hold').textContent = pctS(hold);
  $('v-hold').className = 'big ' + (hold >= 0 ? 'pos' : 'neg');
  $('v-hold-amt').textContent = `${money(NOTIONAL)} → ${money(NOTIONAL * final)}`;
  $('v-hold-detail').textContent =
    `${num(NOTIONAL / spot, 1)} 股 @ ${num(spot)}　→ 每股 ${num(spot * final)}`;

  const gap = r.ret - hold;
  $('v-delta').innerHTML = gap >= 0
    ? `這個情境下 <b class="pos">FCN 勝出 ${pct(gap)}</b>　（多賺 ${money(gap * NOTIONAL)}）`
    : `這個情境下 <b class="neg">FCN 落後 ${pct(-gap)}</b>　`
      + `（少賺 ${money(-gap * NOTIONAL)}，這是放棄上檔的代價）`;

  // 提前出場價通常訂在期初的 100%：真實商品在股價回到原點時多半早就出場了
  $('v-ko-note').textContent = final >= 1
    ? '註：本試算只看到期情境。真實商品的提前出場價多半訂在期初的 100%，'
      + '股價漲回原點時通常早就提前出場，報酬同樣封頂在已累積的配息。'
    : '';

  drawLearnChart(cpn, strike, ki, final, breached, spot);
  drawLearnTable(cpn, strike, final, breached, spot);
}

/** 到期給付：規則與 fcn/engine.py 一致（此處聚焦到期，不含提前出場）。 */
function learnOutcome(cpn, strike, final, breached, spot) {
  const coupon = NOTIONAL * cpn;
  const deliver = breached && final < strike;
  if (deliver) {
    const stockValue = NOTIONAL * (final / strike);
    return {
      deliver: true, coupon, stockValue, value: stockValue + coupon,
      ret: (stockValue + coupon) / NOTIONAL - 1,
      shares: NOTIONAL / (spot * strike),
      scenario: 'D　到期承接股票',
    };
  }
  return {
    deliver: false, coupon, stockValue: 0, value: NOTIONAL + coupon, ret: cpn, shares: 0,
    scenario: breached
      ? 'C　到期還本（曾破下限價，期末站回執行價之上）'
      : 'B　到期還本',
  };
}

const TABLE_ROWS = [0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.2, 1.4];

/** 一次列出各種到期股價的結果，避免只看單一情境而誤判。 */
function drawLearnTable(cpn, strike, final, breached, spot) {
  const rows = TABLE_ROWS.map((x) => {
    const safe = learnOutcome(cpn, strike, x, false, spot).ret;
    const hit = learnOutcome(cpn, strike, x, true, spot).ret;
    const hold = x - 1;
    return [
      num(spot * x),
      pct(x, 0),
      el('span', { class: sign(safe) }, pctS(safe)),
      el('span', { class: sign(hit) }, pctS(hit)),
      el('span', { class: sign(hold) }, pctS(hold)),
      el('span', { class: sign((breached ? hit : safe) - hold) },
        pctS((breached ? hit : safe) - hold)),
    ];
  });

  const t = dataTable(
    ['到期股價', '相對現在', 'FCN（未破下限價）', 'FCN（曾破下限價）',
      '直接買股票', '目前設定下的差距'],
    rows,
  );
  // 標出目前滑桿所在那一列。容差是為了讓正中間的值（例如 130% 落在
  // 120% 與 140% 之間）穩定取較低那列，而不是由浮點誤差決定。
  let near = TABLE_ROWS[0];
  for (const x of TABLE_ROWS) {
    if (Math.abs(x - final) + 1e-9 < Math.abs(near - final)) near = x;
  }
  t.querySelectorAll('tbody tr').forEach((tr, i) => {
    if (TABLE_ROWS[i] === near) tr.className = 'here';
  });
  $('v-table').replaceChildren(t);
}

function drawLearnChart(cpn, strike, ki, final, breached, spot) {
  const xs = [];
  for (let x = 0.2; x <= 1.601; x += 0.01) xs.push(x);
  const fcnAt = (x) => (breached && x < strike ? x / strike - 1 : 0) + cpn;

  const vlines = [
    { x: strike, label: `執行價 ${num(spot * strike)}`, color: css('--warn') },
    { x: ki, label: `下限價 ${num(spot * ki)}`, color: css('--bad') },
  ];
  const series = [
    {
      name: '直接買股票', color: css('--ink-3'), dash: '5 4',
      points: xs.map((x) => [x, x - 1]),
    },
    {
      name: 'FCN', color: css('--accent'), width: 2.6,
      points: xs.map((x) => [x, fcnAt(x)]),
    },
  ];

  const box = $('v-chart');
  box.replaceChildren(lineChart({
    series, vlines, height: 250,
    xFmt: (v) => num(spot * v, spot >= 100 ? 0 : 1),
    yFmt: (v) => (v * 100).toFixed(0) + '%',
    hoverXFmt: (v) => `到期股價 ${num(spot * v)}　(${pct(v, 1)})`,
    hoverYFmt: (v) => pctS(v, 2),
    markers: [
      { x: final, y: fcnAt(final), color: css('--accent'), shape: 'o', title: '目前設定' },
      { x: final, y: final - 1, color: css('--ink-3'), shape: 'o', title: '直接持股' },
    ],
  }).firstChild);
  box.append(legend([
    { name: 'FCN', color: css('--accent') },
    { name: '直接買股票', color: css('--ink-3'), dash: true },
    { name: '執行價', color: css('--warn'), dash: true },
    { name: '下限價', color: css('--bad'), dash: true },
  ]));
}

/* ====================== 分享圖（下載 PNG） ====================== */

// 匯出的圖不跟隨深色模式：同一組條件分享出去必須每次都長一樣，
// 所以這裡用固定的亮色調色盤，而不是讀 CSS 變數。
// 字型堆疊一律用單引號，序列化成 XML 屬性時才不會被跳脫成 &quot;。
const CARD = {
  w: 1000, h: 1180, pad: 48,
  bg: '#ffffff', panel: '#f5f6f8', line: '#dde1e7',
  ink: '#1b1f26', ink2: '#5a6472', ink3: '#8b95a3',
  accent: '#c8102e', pos: '#17825a', neg: '#c0392b', warn: '#b7791f',
  posSoft: '#e6f5ee', negSoft: '#fdedeb',
  font: "'Noto Sans TC','PingFang TC','Microsoft JhengHei','Hiragino Sans GB',"
    + "system-ui,-apple-system,'Helvetica Neue',Arial,sans-serif",
  mono: "ui-monospace,SFMono-Regular,Menlo,Consolas,monospace",
};

/** 分享圖的文字：一律用 <text>，不用 foreignObject（canvas 不保證能光柵化）。 */
function ct(x, y, s, o = {}) {
  return svg('text', {
    x, y, fill: o.fill || CARD.ink,
    'font-size': o.size || 16,
    'font-weight': o.weight || 400,
    'text-anchor': o.anchor || 'start',
    'font-family': o.mono ? CARD.mono : CARD.font,
  }, s);
}

const crect = (x, y, w, h, o = {}) => svg('rect', {
  x, y, width: w, height: h, rx: o.rx == null ? 10 : o.rx,
  fill: o.fill || 'none', stroke: o.stroke, 'stroke-width': o.sw,
});

/** 分享圖上的損益圖：與 drawLearnChart 同一條公式，只是自帶配色與尺寸。 */
function cardChart(g, x, y, w, h, o) {
  const { cpn, strike, ki, final, breached, spot } = o;
  const L = x + 70, R = x + w - 16, T = y + 18, B = y + h - 36;
  const xlo = 0.2, xhi = 1.6;
  const f = (v) => (breached && v < strike ? v / strike - 1 : 0) + cpn;

  const xs = [];
  for (let v = xlo; v <= xhi + 1e-9; v += 0.01) xs.push(v);
  const ys = xs.flatMap((v) => [f(v), v - 1]);
  let ylo = Math.min(...ys), yhi = Math.max(...ys);
  const padY = (yhi - ylo) * 0.08;
  ylo -= padY; yhi += padY;

  const X = (v) => L + ((v - xlo) / (xhi - xlo)) * (R - L);
  const Y = (v) => B - ((v - ylo) / (yhi - ylo)) * (B - T);

  g.append(crect(x, y, w, h, { fill: CARD.panel }));
  for (const t of niceTicks(ylo + padY, yhi - padY, 6)) {
    g.append(svg('line', { x1: L, x2: R, y1: Y(t), y2: Y(t), stroke: CARD.line }));
    g.append(ct(L - 10, Y(t) + 5, (t * 100).toFixed(0) + '%',
      { size: 14, fill: CARD.ink2, anchor: 'end', mono: true }));
  }
  // X 軸刻度寫死每 20%：niceTicks 在 0.2~1.6 上只會給三格，價位讀不出來
  for (let t = xlo; t <= xhi + 1e-9; t += 0.2) {
    g.append(svg('line', {
      x1: X(t), x2: X(t), y1: T, y2: B, stroke: CARD.line, 'stroke-dasharray': '2 4',
    }));
    g.append(ct(X(t), B + 26, num(spot * t, spot >= 100 ? 0 : 1),
      { size: 14, fill: CARD.ink2, anchor: 'middle', mono: true }));
  }

  const path = (fn, attrs) => g.append(svg('path', Object.assign({
    d: xs.map((v, i) => `${i ? 'L' : 'M'}${X(v).toFixed(1)} ${Y(fn(v)).toFixed(1)}`).join(' '),
    fill: 'none', 'stroke-linejoin': 'round',
  }, attrs)));
  path((v) => v - 1, { stroke: CARD.ink3, 'stroke-width': 2, 'stroke-dasharray': '6 5' });
  path(f, { stroke: CARD.accent, 'stroke-width': 3.5 });

  // 兩條界線的標籤錯開高度：執行價與下限價設得很近時才不會疊在一起
  [{ v: strike, c: CARD.warn, t: '執行價', dy: 18 },
    { v: ki, c: CARD.neg, t: '下限價', dy: 40 }].forEach((b) => {
    g.append(svg('line', {
      x1: X(b.v), x2: X(b.v), y1: T, y2: B,
      stroke: b.c, 'stroke-width': 1.5, 'stroke-dasharray': '5 4',
    }));
    g.append(ct(X(b.v) + 7, T + b.dy, `${b.t} ${num(spot * b.v)}`,
      { size: 14, weight: 600, fill: b.c }));
  });
  // 目前設定的兩個點；白色外框是為了壓在格線上仍看得出來
  for (const m of [{ y: f(final), c: CARD.accent }, { y: final - 1, c: CARD.ink3 }]) {
    g.append(svg('circle', {
      cx: X(final), cy: Y(m.y), r: 7, fill: m.c, stroke: '#ffffff', 'stroke-width': 2.5,
    }));
  }
}

/** 依目前滑桿狀態組出一張分享用 SVG（獨立於畫面上的版面）。 */
function buildShareSvg() {
  const cpn = +$('s-cpn').value / 100;
  const strike = +$('s-strike').value / 100;
  const ki = +$('s-ki').value / 100;
  const final = +$('s-final').value / 100;
  const breached = $('s-breach').checked;
  const spot = learnStock.price;

  const r = learnOutcome(cpn, strike, final, breached, spot);
  const hold = final - 1;
  const gap = r.ret - hold;
  const sym = learnStock.live ? learnStock.symbol : '示意標的';

  const W = CARD.w, P = CARD.pad, CW = W - P * 2;
  const g = svg('svg', { width: W, height: CARD.h, viewBox: `0 0 ${W} ${CARD.h}` });
  g.append(crect(0, 0, W, CARD.h, { fill: CARD.bg, rx: 0 }));

  // 抬頭
  g.append(ct(P, 62, 'FCN 情境試算', { size: 34, weight: 700 }));
  g.append(ct(W - P, 62, new Date().toLocaleDateString('en-CA'),
    { size: 15, fill: CARD.ink3, anchor: 'end', mono: true }));
  g.append(ct(P, 92, '固定配息 ＋ 到期可能承接股票的結構型商品', { size: 16, fill: CARD.ink2 }));
  g.append(svg('line', { x1: P, x2: W - P, y1: 116, y2: 116, stroke: CARD.line }));

  // 標的
  g.append(ct(P, 154, sym, { size: 26, weight: 700 }));
  g.append(ct(P, 182, learnStock.live
    ? `${learnStock.name || ''}　現價 ${num(spot)}`
    : '尚未載入行情，以每股 100 元的示意價試算', { size: 16, fill: CARD.ink2 }));

  // 條件
  const bw = (CW - 42) / 4;
  [['年化配息', pct(cpn, 1), `每年 ${money(NOTIONAL * cpn)}`],
    ['執行價', pct(strike, 0), num(spot * strike)],
    ['下限價', pct(ki, 0), num(spot * ki)],
    ['到期股價', pct(final, 0), num(spot * final)],
  ].forEach(([lbl, val, sub], i) => {
    const bx = P + i * (bw + 14);
    g.append(crect(bx, 204, bw, 100, { fill: CARD.panel }));
    g.append(ct(bx + 16, 234, lbl, { size: 15, fill: CARD.ink2 }));
    g.append(ct(bx + 16, 268, val, { size: 26, weight: 700, mono: true }));
    g.append(ct(bx + 16, 292, sub, { size: 15, fill: CARD.ink3, mono: true }));
  });

  // 情境
  g.append(crect(P, 324, CW, 54, { fill: r.deliver ? CARD.negSoft : CARD.posSoft }));
  g.append(ct(W / 2, 359,
    `${breached ? '期間曾跌破下限價' : '期間未跌破下限價'}　→　${r.scenario}`,
    { size: 21, weight: 600, anchor: 'middle', fill: r.deliver ? CARD.neg : CARD.pos }));

  // 兩側對照
  const pw = (CW - 24) / 2;
  for (const p of [
    {
      x: P, lbl: '投資 FCN', ret: r.ret, bar: CARD.accent,
      amt: `${money(NOTIONAL)} → ${money(r.value)}`,
      lines: r.deliver
        ? [`承接 ${sym} ${num(r.shares, 1)} 股 @ ${num(spot * strike)}`,
          `市值 ${money(r.stockValue)}　＋配息 ${money(r.coupon)}`]
        : [`本金 ${money(NOTIONAL)}　＋配息 ${money(r.coupon)}`],
    },
    {
      x: P + pw + 24, lbl: '直接買股票', ret: hold, bar: CARD.ink3,
      amt: `${money(NOTIONAL)} → ${money(NOTIONAL * final)}`,
      lines: [`${num(NOTIONAL / spot, 1)} 股 @ ${num(spot)}`, `→ 每股 ${num(spot * final)}`],
    },
  ]) {
    g.append(crect(p.x, 400, pw, 228, { fill: CARD.panel }));
    g.append(crect(p.x, 400, 5, 228, { fill: p.bar, rx: 2 }));
    g.append(ct(p.x + 26, 438, p.lbl, { size: 17, weight: 600, fill: CARD.ink2 }));
    g.append(ct(p.x + 26, 504, pctS(p.ret), {
      size: 50, weight: 700, mono: true, fill: p.ret >= 0 ? CARD.pos : CARD.neg,
    }));
    g.append(ct(p.x + 26, 546, p.amt, { size: 20, weight: 600, mono: true }));
    p.lines.forEach((s, i) => g.append(
      ct(p.x + 26, 584 + i * 26, s, { size: 16, fill: CARD.ink2 })));
  }

  g.append(ct(W / 2, 666, gap >= 0
    ? `這個情境下 FCN 勝出 ${pct(gap)}（多賺 ${money(gap * NOTIONAL)}）`
    : `這個情境下 FCN 落後 ${pct(-gap)}（少賺 ${money(-gap * NOTIONAL)}，這是放棄上檔的代價）`,
    { size: 18, weight: 600, anchor: 'middle', fill: gap >= 0 ? CARD.pos : CARD.neg }));

  cardChart(g, P, 690, CW, 340, { cpn, strike, ki, final, breached, spot });

  let lx = P;
  for (const [name, color, dash] of [
    ['FCN', CARD.accent, false], ['直接買股票', CARD.ink3, true],
    ['執行價', CARD.warn, true], ['下限價', CARD.neg, true],
  ]) {
    g.append(svg('line', {
      x1: lx, x2: lx + 24, y1: 1050, y2: 1050,
      stroke: color, 'stroke-width': 3, 'stroke-dasharray': dash ? '5 4' : null,
    }));
    g.append(ct(lx + 32, 1055, name, { size: 15, fill: CARD.ink2 }));
    lx += 32 + name.length * 15 + 28;
  }

  // 分享出去的圖會脫離網頁脈絡，風險揭露必須跟著圖走
  g.append(svg('line', { x1: P, x2: W - P, y1: 1082, y2: 1082, stroke: CARD.line }));
  [
    '本圖為到期情境試算，不含提前出場、匯率、稅負與費用，也未反映發行機構信用風險。',
    'FCN 不保本、報酬封頂於配息；跌破下限價且期末低於執行價時，將以執行價承接最差表現標的。',
    '僅供教育用途，不構成投資建議，亦非任何商品之報價。',
  ].forEach((s, i) => g.append(ct(P, 1110 + i * 24, s, { size: 14, fill: CARD.ink3 })));

  return g;
}

function shareFileName() {
  const s = (learnStock.live ? learnStock.symbol : 'DEMO').replace(/[^\w.-]/g, '');
  return `FCN_${s}_${new Date().toLocaleDateString('en-CA').replace(/-/g, '')}.png`;
}

async function downloadSharePng() {
  const btn = $('btn-png');
  const label = btn.textContent;
  btn.disabled = true;
  btn.textContent = '產生中…';
  try {
    const src = new XMLSerializer().serializeToString(buildShareSvg());
    const img = new Image();
    await new Promise((ok, fail) => {
      img.onload = ok;
      img.onerror = () => fail(new Error('SVG 無法載入'));
      img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(src);
    });

    const scale = 2;            // 2 倍解析度，貼進簡報或社群才不會糊
    const cv = el('canvas');
    cv.width = CARD.w * scale;
    cv.height = CARD.h * scale;
    const ctx = cv.getContext('2d');
    ctx.fillStyle = CARD.bg;    // PNG 有透明通道，先鋪白底
    ctx.fillRect(0, 0, cv.width, cv.height);
    ctx.drawImage(img, 0, 0, cv.width, cv.height);

    const blob = await new Promise((ok) => cv.toBlob(ok, 'image/png'));
    if (!blob) throw new Error('canvas 轉檔失敗');
    const href = URL.createObjectURL(blob);
    const a = el('a', { href, download: shareFileName() });
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(href), 30_000);
  } catch (e) {
    btn.textContent = '產生失敗，請再試一次';
    setTimeout(() => { btn.textContent = label; btn.disabled = false; }, 2500);
    return;
  }
  btn.textContent = label;
  btn.disabled = false;
}

$('btn-png').addEventListener('click', downloadSharePng);

['s-cpn', 's-final'].forEach((id) => $(id).addEventListener('input', learnSim));
$('s-breach').addEventListener('change', learnSim);
$('btn-goto-quote').addEventListener('click', () => showTab('quote'));

// 下限價不可高於執行價（條款要求），拉動時互相夾住
function clampKi() {
  if (+$('s-ki').value >= +$('s-strike').value) $('s-ki').value = +$('s-strike').value - 1;
  learnSim();
}
$('s-strike').addEventListener('input', clampKi);
$('s-ki').addEventListener('input', clampKi);

$('quick-picks').replaceChildren(...QUICK_PICKS.map((t) => el('button', {
  'data-t': t, onclick: () => loadLearnStock(t),
}, t.split(' ')[0])));
$('btn-load-stock').addEventListener('click', () => loadLearnStock($('s-ticker').value.trim()));
$('s-ticker').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') loadLearnStock($('s-ticker').value.trim());
});

learnSim();
showTab('learn');
loadLearnStock('NVDA UW');     // 先以示意價渲染，行情回來再更新
