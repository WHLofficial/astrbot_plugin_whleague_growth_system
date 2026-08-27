/* 球员页：列表（uid 序）+ 搜索 + 排行切换 + 详情抽屉（等级徽章 + 经验刻度条）。 */

import * as api from "./api.js";
import {
  el, esc, fmtXp, renderTable, renderPager, openDrawer, xpGauge, toast, errorNote,
} from "./ui.js";

let mode = "list";          // list | rank
let sort = "uid";           // uid | xp | career
let q = "";
let page = 1;

export async function render(root, ctx) {
  const head = el(`<div>
    <h2 class="page-title">球员</h2>
    <p class="page-sub">在册球员的成长档案；点击行查看详情</p>
    <div class="toolbar">
      <label class="field" style="flex:0 1 260px"><span>搜索（UID / 名字 / 球队）</span>
        <input type="text" id="pl-q">
      </label>
      <label class="field" style="flex:0 0 auto"><span>视图</span>
        <select id="pl-mode">
          <option value="list">名册</option>
          <option value="rank">排行榜</option>
        </select>
      </label>
      <label class="field" id="pl-sort-wrap" style="flex:0 0 auto"><span>排序</span>
        <select id="pl-sort">
          <option value="uid">按 UID</option>
          <option value="xp">按本期经验</option>
          <option value="career">按生涯经验</option>
        </select>
      </label>
    </div>
    <div id="pl-body"></div>
  </div>`);
  root.appendChild(head);
  const body = head.querySelector("#pl-body");

  async function load() {
    body.innerHTML = `<div class="empty-state">加载中…</div>`;
    try {
      if (mode === "rank") {
        const r = await api.get("rank", { mode: sort === "career" ? "career" : "xp", page });
        drawRank(r, ctx);
      } else {
        const params = { page };
        if (q) params.q = q;
        if (sort !== "uid") params.sort = sort;
        const r = await api.get("players", params);
        drawList(r, ctx);
      }
    } catch (e) {
      body.innerHTML = "";
      body.appendChild(errorNote(e.message));
    }
  }

  function drawList(r, rc) {
    body.innerHTML = "";
    const table = renderTable(
      [
        { label: "UID", render: (row) => esc(row.player_uid) },
        { label: "名字", render: (row) => esc(row.name) },
        { label: "球队", render: (row) => esc(row.team || "—") },
        { label: "等级", num: true, render: (row) => esc(row.level) },
        { label: "本期经验", num: true, render: (row) => esc(fmtXp(row.xp)) },
        { label: "生涯经验", num: true, render: (row) => esc(fmtXp(row.xp_total)) },
      ],
      r.rows,
      "还没有球员。先到「导入」页上传球员名册文件"
    );
    table.addEventListener("click", (ev) => {
      const tr = ev.target.closest("tbody tr");
      if (!tr) return;
      const idx = Array.from(table.tBodies[0].rows).indexOf(tr);
      showDetail(r.rows[idx]);
    });
    body.appendChild(table);
    body.appendChild(renderPager(r.page, r.total_pages, (p) => { page = p; load(); }));
  }

  function drawRank(r, rc) {
    body.innerHTML = "";
    const isCareer = r.mode === "career";
    const key = isCareer ? "xp_total" : "xp";
    const table = renderTable(
      [
        { label: "#", num: true, render: (row) => `<span${(r.page - 1) * (r.page_size || 10) + row._i + 1 <= 3 ? ' class="rank-top"' : ""}>${(r.page - 1) * (r.page_size || 10) + row._i + 1}</span>` },
        { label: "UID", render: (row) => esc(row.player_uid) },
        { label: "名字", render: (row) => esc(row.name) },
        { label: isCareer ? "生涯经验" : "本期经验", num: true, render: (row) => esc(fmtXp(row[key])) },
        { label: "等级", num: true, render: (row) => esc(row.level) },
      ],
      r.rows.map((row, i) => ({ ...row, _i: i })),
      "暂无排行数据"
    );
    table.addEventListener("click", (ev) => {
      const tr = ev.target.closest("tbody tr");
      if (!tr) return;
      const idx = Array.from(table.tBodies[0].rows).indexOf(tr);
      showDetail({ player_uid: r.rows[idx].player_uid });
    });
    body.appendChild(table);
    body.appendChild(renderPager(r.page, r.total_pages, (p) => { page = p; load(); }));
  }

  async function showDetail(seed) {
    const d = openDrawer(`球员 ${seed.player_uid}`);
    try {
      const p = await api.get(`players/${encodeURIComponent(seed.player_uid)}`);
      const pl = p.player;
      d.body.innerHTML = `
        <div style="display:flex;align-items:center;gap:14px;margin-bottom:8px">
          <span class="level-badge">${esc(pl.level)}</span>
          <div>
            <div style="font-size:16px;font-weight:600">${esc(pl.name)}</div>
            <div style="color:var(--muted);font-size:12.5px">UID ${esc(pl.player_uid)} · ${esc(pl.team || "无球队")}</div>
          </div>
        </div>`;
      const g = xpGauge(Number(pl.xp) || 0, Number(pl.next_level_xp) || 0, []);
      d.body.appendChild(g);
      const capLine = el(`<p style="font-family:var(--mono);color:var(--muted);font-size:12px"></p>`);
      capLine.textContent = `本期 ${fmtXp(pl.xp)} / 下一级 ${fmtXp(pl.next_level_xp)} · 生涯 ${fmtXp(pl.xp_total)} · 生涯奖杯 ${fmtXp(p.awards.reduce((s, a) => s + (Number(a.xp) || 0), 0))}`;
      d.body.appendChild(capLine);

      const mkSection = (title, rows, cols) => {
        if (!rows.length) return;
        const sec = el(`<h4 style="margin:18px 0 6px;font-size:13.5px;color:var(--muted)"></h4>`);
        sec.textContent = title;
        d.body.appendChild(sec);
        d.body.appendChild(renderTable(cols, rows));
      };
      mkSection(
        "近期出场",
        (p.appearances || []).slice(0, 20),
        [
          { label: "日期", render: (r2) => esc(r2.match_date) },
          { label: "对手", render: (r2) => esc(r2.opponent) },
          { label: "数据", render: (r2) => esc(r2.stats_text || "") },
          { label: "经验", num: true, render: (r2) => esc(fmtXp(r2.total_xp)) },
        ]
      );
      mkSection(
        "里程碑奖励",
        p.awards || [],
        [
          { label: "期数", num: true, render: (r2) => esc(r2.period_no ?? "生涯") },
          { label: "项目", render: (r2) => esc(r2.stat_key_display || r2.stat_key) },
          { label: "阈值", num: true, render: (r2) => esc(fmtXp(r2.threshold)) },
          { label: "经验", num: true, render: (r2) => `+${esc(fmtXp(r2.xp))}` },
        ]
      );
    } catch (e) {
      d.body.innerHTML = "";
      d.body.appendChild(errorNote(e.message));
    }
  }

  /* 控件事件 */
  const qInput = head.querySelector("#pl-q");
  let debounceTimer = null;
  qInput.addEventListener("input", () => {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => {
      q = qInput.value.trim();
      page = 1;
      if (mode === "rank") { head.querySelector("#pl-mode").value = "list"; }
      mode = "list";
      load();
    }, 320);
  });
  head.querySelector("#pl-mode").addEventListener("change", (ev) => {
    mode = ev.target.value === "rank" ? "rank" : "list";
    page = 1;
    load();
  });
  head.querySelector("#pl-sort").addEventListener("change", (ev) => {
    sort = ev.target.value;
    page = 1;
    if (sort !== "uid") { mode = mode; }
    load();
  });

  await load();
  return () => { clearTimeout(debounceTimer); };
}
