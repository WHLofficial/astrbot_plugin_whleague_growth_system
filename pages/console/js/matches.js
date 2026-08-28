/* 比赛页：单场录入（数据项行按规则动态生成）+ 近期比赛表。 */

import * as api from "./api.js";
import { el, esc, fmtXp, renderTable, toast, errorNote } from "./ui.js";

export async function render(root, ctx) {
  root.appendChild(el(`<div>
    <h2 class="page-title">比赛</h2>
    <p class="page-sub">录入一场比赛：选择球员、填入数据项，系统按规则即时结算经验</p>
  </div>`));

  /* 表单卡片 */
  const formCard = el(`<div class="card">
    <h3 style="margin:0 0 10px;font-family:var(--serif);font-size:15px">单场录入</h3>
    <div id="mc-form"></div>
  </div>`);
  root.appendChild(formCard);
  const formZone = formCard.querySelector("#mc-form");

  const listCard = el(`<div class="card"><h3 style="margin:0 0 10px;font-family:var(--serif);font-size:15px">近期比赛</h3></div>`);
  root.appendChild(listCard);
  let reloadRecent = null;

  try {
    /* 下拉需要全量球员：首页与规则并行取，其余页顺序补齐（防御性上限 50 页） */
    const [rule, firstPage] = await Promise.all([
      api.get("rule"),
      api.get("players", { page: 1 }),
    ]);
    const players = [...(firstPage.rows || [])];
    for (
      let p = 2;
      p <= Math.min(Number(firstPage.total_pages) || 1, 50) && players.length < 2000;
      p += 1
    ) {
      const pageData = await api.get("players", { page: p });
      players.push(...(pageData.rows || []));
    }
    drawForm(rule, players);
  } catch (e) {
    formZone.innerHTML = "";
    formZone.appendChild(errorNote(e.message));
  }
  await loadRecent();

  function drawForm(rule, players) {
    const statDefs = Object.entries(rule.stats || {});
    const form = el(`<form id="mc-record">
      <div class="form-row">
        <label class="field" style="flex:1 1 200px"><span>球员</span>
          <select id="mc-uid"></select>
        </label>
        <label class="field" style="flex:0 0 160px"><span>比赛日期</span>
          <input type="date" id="mc-date" required>
        </label>
        <label class="field" style="flex:1 1 160px"><span>对手（可留空）</span>
          <input type="text" id="mc-opp">
        </label>
      </div>
      <p style="font-size:12.5px;color:var(--muted);margin:6px 0">数据项（留空或 0 视为未上场该项）</p>
      <div class="form-row" id="mc-stats"></div>
      <div style="margin-top:12px">
        <button type="submit" class="btn">录入这场比赛</button>
      </div>
      <div id="mc-result"></div>
    </form>`);

    const uidSel = form.querySelector("#mc-uid");
    for (const p of players) {
      const opt = document.createElement("option");
      opt.value = p.player_uid;
      opt.textContent = `${p.player_uid} ${p.name}`;
      uidSel.appendChild(opt);
    }

    const statsZone = form.querySelector("#mc-stats");
    for (const [key, def] of statDefs) {
      const label = el(`<label class="field"><span></span><input type="number" step="any" min="0" data-key="${esc(key)}"></label>`);
      label.querySelector("span").textContent = def.name || key;
      statsZone.appendChild(label);
    }

    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const resultZone = form.querySelector("#mc-result");
      const stats = {};
      for (const inp of statsZone.querySelectorAll("input[data-key]")) {
        const v = inp.value.trim();
        if (v !== "" && Number(v) !== 0) stats[inp.dataset.key] = v;
      }
      try {
        const r = await api.post("matches/record", {
          player_uid: uidSel.value,
          match_date: form.querySelector("#mc-date").value,
          opponent: form.querySelector("#mc-opp").value.trim(),
          stats,
        });
        toast(`已录入：${r.name} +${fmtXp(r.total_xp)} 经验`);
        showResult(r);
        if (reloadRecent) reloadRecent();
        ctx.refreshBadges();
      } catch (e) {
        toast(e.message, true);
      }
    });

    function showResult(r) {
      const zone = form.querySelector("#mc-result");
      zone.innerHTML = "";
      const card = el(`<div style="margin-top:14px;border-top:1px dashed var(--line);padding-top:10px;font-size:13.5px"></div>`);
      card.innerHTML = `<b>${esc(r.name)}</b> ${esc(r.match_date)} 对阵 ${esc(r.opponent || "—")}：
        数据经验 <b class="num">${esc(fmtXp(r.stat_xp))}</b>
        · 里程碑 <b class="num">+${esc(fmtXp(r.bonus_xp))}</b>
        · 合计 <b class="num" style="color:var(--pitch)">+${esc(fmtXp(r.total_xp))}</b>`;
      if ((r.awarded || []).length) {
        const ul = el(`<ul class="milestone-list"></ul>`);
        for (const a of r.awarded) {
          ul.appendChild(el(`<li><span>${esc(a.label || a.stat_key_display || a.key || "")}</span><span class="xp">+${esc(fmtXp(a.xp))}</span></li>`));
        }
        card.appendChild(ul);
      }
      zone.appendChild(card);
    }

    formZone.innerHTML = "";
    formZone.appendChild(form);
  }

  async function loadRecent() {
    try {
      const ov = await api.get("stats/overview");
      listCard.innerHTML = "<h3 style=\"margin:0 0 10px;font-family:var(--serif);font-size:15px\">近期比赛</h3>";
      listCard.appendChild(
        renderTable(
          [
            { label: "日期", render: (r) => esc(r.match_date) },
            { label: "对手", render: (r) => esc(r.opponent) },
            { label: "出场人数", num: true, render: (r) => esc(r.player_count) },
            { label: "发出经验", num: true, render: (r) => esc(fmtXp(r.xp_total)) },
          ],
          ov.recent_matches || [],
          "还没有比赛记录"
        )
      );
      reloadRecent = loadRecent;
    } catch (e) {
      listCard.appendChild(errorNote(e.message));
    }
  }

  return null;
}
