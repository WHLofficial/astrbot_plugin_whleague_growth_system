/* 通用 UI 组件：表格、分页、弹层、toast、经验刻度条。 */

export function el(html) {
  const t = document.createElement("template");
  t.innerHTML = html.trim();
  return t.content.firstElementChild;
}

export function fmtXp(v) {
  const n = Number(v) || 0;
  return Number.isInteger(n) ? String(n) : String(Math.round(n * 10) / 10);
}

export function fmtDate(s) {
  return s == null || s === "" ? "—" : String(s);
}

export function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

/* ─── 表格 ─────────────────────────────────────── */

/**
 * 渲染数据表。columns: [{label, num?, render(row)}]；rows 为空显示空态。
 */
export function renderTable(columns, rows, emptyHint = "暂无记录") {
  if (!rows.length) {
    return el(`<div class="empty-state">还没有任何记录<div class="hint">${esc(emptyHint)}</div></div>`);
  }
  const thead = columns
    .map((c, i) => `<th class="${c.num ? "num" : ""}" data-col="${i}">${esc(c.label)}</th>`)
    .join("");
  const tbody = rows
    .map(
      (row, i) =>
        `<tr>${columns
          .map((c) => `<td class="${c.num ? "num" : ""}">${c.render(row, i)}</td>`)
          .join("")}</tr>`
    )
    .join("");
  return el(`<table class="grid"><thead><tr>${thead}</tr></thead><tbody>${tbody}</tbody></table>`);
}

/* ─── 分页 ─────────────────────────────────────── */

/** 渲染分页条；onPage(page) 回调由调用方重取数据。 */
export function renderPager(page, totalPages, onPage) {
  const bar = el(`<div class="pager">
    <button type="button" data-act="prev">‹</button>
    <span>${page} / ${totalPages}</span>
    <button type="button" data-act="next">›</button>
  </div>`);
  const [prev, next] = bar.querySelectorAll("button");
  prev.disabled = page <= 1;
  next.disabled = page >= totalPages;
  prev.addEventListener("click", () => onPage(page - 1));
  next.addEventListener("click", () => onPage(page + 1));
  return bar;
}

/* ─── toast / 确认框 ───────────────────────────── */

export function toast(msg, isErr = false) {
  const zone = document.getElementById("toast-zone");
  const node = el(`<div class="toast${isErr ? " err" : ""}"></div>`);
  node.textContent = msg;
  zone.appendChild(node);
  setTimeout(() => node.remove(), isErr ? 5200 : 2600);
}

/** 破坏性操作确认：确认后 resolve(true)。 */
export function confirmDialog({ title, message, danger = false, confirmText = "确认" }) {
  return new Promise((resolve) => {
    const root = document.getElementById("layer-root");
    const box = el(`<div>
      <div class="drawer-mask"></div>
      <div class="card confirm-box" role="alertdialog" style="position:fixed;left:50%;top:38%;transform:translate(-50%,-50%);z-index:32;margin:0;">
        <h3 style="margin-top:0;font-family:var(--serif)">${esc(title)}</h3>
        <p class="msg">${esc(message)}</p>
        <div style="display:flex;gap:10px;justify-content:flex-end">
          <button type="button" data-act="cancel" class="btn secondary">取消</button>
          <button type="button" data-act="ok" class="btn ${danger ? "danger" : ""}">${esc(confirmText)}</button>
        </div>
      </div>
    </div>`);
    box.querySelector('[data-act="cancel"]').addEventListener("click", () => { box.remove(); resolve(false); });
    box.querySelector('[data-act="ok"]').addEventListener("click", () => { box.remove(); resolve(true); });
    box.querySelector(".drawer-mask").addEventListener("click", () => { box.remove(); resolve(false); });
    root.appendChild(box);
    box.querySelector('[data-act="ok"]').focus();
  });
}

/** 右侧抽屉，返回抽屉元素（调用方填充内容并自行 remove()）。 */
export function openDrawer(titleText) {
  const root = document.getElementById("layer-root");
  const wrap = el(`<div>
    <div class="drawer-mask"></div>
    <aside class="drawer">
      <button type="button" class="close-x" aria-label="关闭">×</button>
      <h3></h3>
      <div class="drawer-body"></div>
    </aside>
  </div>`);
  wrap.querySelector("h3").textContent = titleText;
  const close = () => wrap.remove();
  wrap.querySelector(".close-x").addEventListener("click", close);
  wrap.querySelector(".drawer-mask").addEventListener("click", close);
  root.appendChild(wrap);
  return { body: wrap.querySelector(".drawer-body"), close };
}

/* ─── 经验刻度条（签名元素） ─────────────────────── */

/**
 * xpGauge：轨道 + 草皮绿填充 + 里程碑竖向刻度。
 * @param current 当前经验  @param cap 本级上限  @param thresholds 里程碑阈值数组
 */
export function xpGauge(current, cap, thresholds = []) {
  const pct = cap > 0 ? Math.min(100, (current / cap) * 100) : 0;
  const wrap = el(`<div class="xp-gauge">
    <div class="xp-gauge-track"><div class="xp-gauge-fill"></div></div>
    <div class="xp-gauge-scale"></div>
  </div>`);
  requestAnimationFrame(() => { wrap.querySelector(".xp-gauge-fill").style.width = `${pct}%`; });
  const track = wrap.querySelector(".xp-gauge-track");
  const scale = wrap.querySelector(".xp-gauge-scale");
  for (const t of thresholds) {
    if (cap <= 0 || t >= cap || t <= 0) continue;
    const posPct = (t / cap) * 100;
    const tick = el(`<span class="xp-gauge-tick"></span>`);
    tick.style.left = `calc(${posPct}% - 1px)`;
    if (t % 1 === 0 && Math.abs(t) >= 1000) tick.classList.add("major");
    track.appendChild(tick);
    const lab = el(`<span style="left:${posPct}%"></span>`);
    lab.textContent = fmtXp(t);
    scale.appendChild(lab);
  }
  return wrap;
}

/* ─── 杂项 ─────────────────────────────────────── */

export function statCell(value, label, warn = false) {
  return el(`<div class="stat-cell">
    <div class="stat-num${warn ? " warn" : ""}">${esc(value)}</div>
    <div class="stat-label">${esc(label)}</div>
  </div>`);
}

export function errorNote(msg) {
  const n = el(`<div class="error-note"></div>`);
  n.textContent = msg;
  return n;
}
