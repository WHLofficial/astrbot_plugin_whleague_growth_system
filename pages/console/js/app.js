/* 入口：bridge 就绪 → 主题 → hash 路由 → 导航渲染。 */

import * as api from "./api.js";
import { toast, esc } from "./ui.js";

const TABS = [
  { id: "overview", label: "总览", loader: () => import("./overview.js") },
  { id: "players", label: "球员", loader: () => import("./players.js") },
  { id: "matches", label: "比赛", loader: () => import("./matches.js") },
  { id: "fixtures", label: "赛程", loader: () => import("./fixtures.js") },
  { id: "imports", label: "导入", loader: () => import("./imports.js") },
  { id: "periods", label: "成长期", loader: () => import("./periods.js") },
  { id: "rules", label: "规则", loader: () => import("./rules.js") },
  { id: "settings", label: "配置", loader: () => import("./settings.js") },
];

const contentEl = document.getElementById("content");
const navEl = document.getElementById("sidenav");
let currentId = null;
let activeUnload = null;

/* ─── 主题 ─────────────────────────────────────── */

function applyTheme(isDark) {
  document.documentElement.dataset.theme = isDark ? "dark" : "light";
  const btn = document.getElementById("theme-toggle");
  btn.textContent = isDark ? "☀ 日间" : "☾ 夜间";
}
applyTheme(api.context()?.isDark);

document.getElementById("theme-toggle").addEventListener("click", () => {
  const dark = document.documentElement.dataset.theme !== "dark";
  applyTheme(dark);
});

/* ─── 顶栏徽记 ─────────────────────────────────── */

async function refreshBadges() {
  try {
    const [ov, pending] = await Promise.all([
      api.get("stats/overview"),
      api.get("imports/pending"),
    ]);
    const badge = document.getElementById("period-badge");
    if (ov.period) {
      badge.textContent = `第 ${ov.period.period_no} 期 · ${ov.period.name}`;
      badge.hidden = false;
    } else {
      badge.textContent = "尚未开期";
      badge.hidden = false;
    }
    const n = (pending.pending || []).filter((r) => r.status === "pending").length;
    const pb = document.getElementById("pending-badge");
    if (n > 0) {
      pb.textContent = `${n} 条待确认导入`;
      pb.hidden = false;
      pb.onclick = () => location.hash = "#/imports";
    } else {
      pb.hidden = true;
    }
  } catch {
    /* 徽记失败不打扰主流程 */
  }
}

/* ─── 路由 ─────────────────────────────────────── */

function currentRoute() {
  const h = location.hash.replace(/^#\/?/, "");
  return TABS.some((t) => t.id === h) ? h : "overview";
}

async function render() {
  const id = currentRoute();
  if (id === currentId) return;
  currentId = id;

  if (typeof activeUnload === "function") {
    try { activeUnload(); } catch { /* 忽略卸载错误 */ }
    activeUnload = null;
  }

  for (const a of navEl.querySelectorAll("a")) {
    a.classList.toggle("active", a.dataset.tab === id);
  }
  for (const t of TABS) {
    if (t.id !== id) continue;
    contentEl.innerHTML = "";
    try {
      const mod = await t.loader();
      activeUnload = await mod.render(contentEl, {
        refreshBadges,
        go: (tabId) => { location.hash = `#/${tabId}`; },
      }) || null;
    } catch (e) {
      contentEl.innerHTML = "";
      const errEl = document.createElement("div");
      errEl.className = "boot-error";
      errEl.innerHTML = `<p>该页面加载失败：${esc(e.message)}</p><p>可尝试刷新页面；若持续失败，请检查插件日志</p>`;
      contentEl.appendChild(errEl);
      toast(e.message, true);
    }
    break;
  }
}

function buildNav() {
  for (const t of TABS) {
    const a = document.createElement("a");
    a.href = `#/${t.id}`;
    a.dataset.tab = t.id;
    a.textContent = t.label;
    navEl.appendChild(a);
  }
}

async function boot() {
  try {
    await api.initBridge();
  } catch (e) {
    contentEl.innerHTML = `<div class="boot-error"><p>${esc(e.message)}</p></div>`;
    return;
  }
  applyTheme(api.context()?.isDark);
  buildNav();
  window.addEventListener("hashchange", render);
  await render();
  refreshBadges();
}

boot();
