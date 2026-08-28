/* 赛程页：主场营收插件联动 —— 按轮次查看赛程，逐场录入/编辑双方球员数据。 */

import * as api from "./api.js";
import {
  el, esc, fmtXp, renderTable, toast, errorNote,
} from "./ui.js";

export async function render(root, ctx) {
  root.appendChild(el(`<div>
    <h2 class="page-title">赛程</h2>
    <p class="page-sub">与主场营收插件联动：按轮次浏览真实对阵，点开任意一场即可为双方球员录入成长数据</p>
  </div>`));

  const banner = el(`<div class="card" id="fx-state"></div>`);
  const board = el(`<div class="card" id="fx-board"><div class="empty-state">加载中…</div></div>`);
  /* 录入面板独立于 board：refreshRounds→drawChips 会整体重建 board，面板不能被误清 */
  const editorZone = el(`<div id="fx-editor"></div>`);
  root.appendChild(banner);
  root.appendChild(board);
  root.appendChild(editorZone);

  let statDefs = [];
  try {
    const rule = await api.get("rule");
    statDefs = Object.entries((rule && rule.stats) || {});
  } catch { /* 无规则时仅浏览赛程 */ }

  let roundsData = null;
  let currentComp = null;
  let currentRound = null;

  await loadRounds();

  /* ─── 轮次总览 ─────────────────────────────── */

  async function loadRounds() {
    try {
      roundsData = await api.get("fixtures/rounds");
    } catch (e) {
      banner.innerHTML = "";
      banner.appendChild(errorNote(e.message));
      board.innerHTML = "";
      return;
    }
    drawBanner();
    if (!roundsData.available) {
      board.innerHTML = "";
      board.appendChild(el(`<div class="empty-state">
        未检测到主场营收插件数据库
        <div class="hint">请确认已安装 astrbot_plugin_whleague_revenue_system（v2.6.0+）并至少导入过一次赛程</div>
      </div>`));
      return;
    }
    if (!(roundsData.rounds || []).length) {
      board.innerHTML = "";
      board.appendChild(el(`<div class="empty-state">主场库暂无赛程<div class="hint">先让管理员执行 /主场赛程导入 导入赛程文件</div></div>`));
      return;
    }
    drawChips();
    if (currentRound === null) {
      // 默认选中最早还有未打比赛的（赛事,轮次），否则第一个小节
      const rounds = roundsData.rounds || [];
      const pending = rounds.find((r) => r.played < r.total);
      const first = pending || rounds[0];
      currentComp = first.competition || "联赛";
      currentRound = String(first.round_no);
    }
    await loadFixtures();
  }

  /** 保存后静默刷新轮次计数（不重置选中轮次）。 */
  async function refreshRounds() {
    try {
      roundsData = await api.get("fixtures/rounds");
      if (roundsData.available && (roundsData.rounds || []).length) {
        drawBanner();
        drawChips();
        await loadFixtures();
      }
    } catch { /* 静默失败 */ }
  }

  function drawBanner() {
    banner.innerHTML = "";
    const s = roundsData.state;
    if (!s || !s.season_number) {
      banner.appendChild(el(`<p class="page-sub" style="margin:0">主场插件未提供当前赛季信息</p>`));
      return;
    }
    const name = s.season_name ? `「${esc(s.season_name)}」` : "";
    banner.className = "card fx-banner";
    banner.innerHTML = `
      <span class="badge-period">第 ${esc(s.season_number)} 赛季${name} · 第 ${esc(s.window_seq)} 窗口</span>
      <span class="hint">共 ${roundsData.rounds.length} 轮 · 赛程只读自主场插件，比分与天气请用 /主场赛果录入 维护</span>`;
  }

  function drawChips() {
    board.innerHTML = "";
    const rounds = roundsData.rounds || [];
    const comps = [...new Set(rounds.map((r) => r.competition || "联赛"))];
    const multi = comps.length > 1 || comps[0] !== "联赛";
    const zone = el(`<div class="round-chips" role="tablist"></div>`);
    for (const comp of comps) {
      if (multi) {
        zone.appendChild(el(`<div class="fx-comp">${esc(comp)}</div>`));
      }
      for (const r of rounds.filter((x) => (x.competition || "联赛") === comp)) {
        const no = String(r.round_no);
        const active = no === currentRound && (r.competition || "联赛") === (currentComp || "联赛");
        const chip = el(`<button type="button" class="round-chip${active ? " active" : ""}" data-round="${esc(no)}" data-comp="${esc(comp)}">
          <b>第 ${esc(no)} 轮</b><small>${r.played}/${r.total}</small>
        </button>`);
        chip.addEventListener("click", async () => {
          currentComp = comp;
          currentRound = no;
          for (const c of zone.querySelectorAll(".round-chip")) {
            c.classList.toggle(
              "active",
              c.dataset.round === no && c.dataset.comp === comp
            );
          }
          editorZone.innerHTML = "";
          await loadFixtures();
        });
        zone.appendChild(chip);
      }
    }
    board.appendChild(zone);
    board.appendChild(el(`<div id="fx-list"></div>`));
  }

  /* ─── 某轮对阵列表 ─────────────────────────── */

  async function loadFixtures() {
    const zone = board.querySelector("#fx-list");
    if (!zone) return;
    zone.innerHTML = "";
    let rows;
    try {
      const data = await api.get("fixtures", {
        round: currentRound,
        competition: currentComp || "",
      });
      rows = (data && data.fixtures) || [];
    } catch (e) {
      zone.appendChild(errorNote(e.message));
      return;
    }
    if (!rows.length) {
      zone.appendChild(el(`<div class="empty-state">该轮暂无对阵</div>`));
      return;
    }
    const recorded = roundsData.recorded || {};
    const wrap = el(`<div></div>`);
    wrap.appendChild(renderTable(
      [
        {
          label: "对阵",
          render: (r) =>
            `<b>${esc(r.home_team)}</b> <span class="fx-score">${esc(r.score || "vs")}</span> <b>${esc(r.away_team)}</b>`,
        },
        {
          label: "状态",
          render: (r) =>
            r.result === "C"
              ? `<span class="tag">取消</span>`
              : r.result
                ? `<span class="tag ok">已完赛</span>`
                : `<span class="tag">未打</span>`,
        },
        { label: "天气", render: (r) => esc(r.weather || "—") },
        {
          label: "已录成长数据",
          num: true,
          render: (r) => {
            const c = recorded[r.fixture_key];
            return c ? `${c.player_count} 人 · ${fmtXp(c.xp_total)}` : "0 人";
          },
        },
        {
          label: "",
          render: (r) => `<button type="button" class="btn secondary" data-key="${esc(r.fixture_key)}">${(recorded[r.fixture_key] || {}).player_count ? "查看 / 编辑" : "录数据"}</button>`,
        },
      ],
      rows
    ));
    for (const b of wrap.querySelectorAll("button[data-key]")) {
      b.addEventListener("click", () => openEditor(b.dataset.key));
    }
    zone.appendChild(wrap);
  }

  /* ─── 单场录入面板（内嵌于赛程列表下方）────── */

  async function openEditor(key) {
    let detail;
    try {
      detail = await api.get(`fixtures/detail/${encodeURIComponent(key)}`);
    } catch (e) {
      toast(e.message, true);
      return;
    }
    if (!statDefs.length) {
      toast("尚未导入成长规则，无法录入数据；请先在「导入」页上传规则文件。", true);
      return;
    }
    const fx = detail.fixture;
    /* 服务端花名册：home/away 为按队名自动匹配的球员库玩家 */
    let rosters = detail.rosters || { home: [], away: [], unmatched: [] };
    /* 已录出场：服务端以 player_uid 为键聚合 {stats:{k:v}, period_no, total_xp} */
    let apps = detail.appearances || {};
    let activeSide = "home";

    /* 「出场」计数项（键 appearance 或名称含「出场」）用复选框表达：打过=1，没打=0 */
    const capStat = statDefs.find(
      ([k, def]) => k === "appearance" || String(def.name || "").includes("出场")
    );
    const numStats = statDefs.filter(([k]) => !capStat || k !== capStat[0]);

    const zone = editorZone;
    zone.innerHTML = "";
    const card = el(`<div class="card fx-editor">
      <div class="fx-headline">
        <b>第 ${esc(fx.round_no)} 轮 · ${esc(fx.home_team)} vs ${esc(fx.away_team)}</b>
        <span class="badge-period">${esc(fx.competition || "联赛")} · 第 ${esc(fx.season_number)} 赛季 第 ${esc(fx.window_seq)} 窗口</span>
        ${fx.result === "C" ? `<span class="tag">取消</span>` : fx.result ? `<span class="tag ok">已完赛</span>` : `<span class="tag">未打</span>`}
        ${fx.weather ? `<span class="hint">${esc(fx.weather)}</span>` : ""}
        <span style="flex:1"></span>
        <button type="button" class="btn secondary" data-act="close">收起</button>
      </div>
      <div class="fx-side-toggle" role="tablist"></div>
      <div class="fx-roster-wrap"></div>
    </div>`);
    card.querySelector('[data-act="close"]').addEventListener("click", () => {
      zone.innerHTML = "";
    });
    zone.appendChild(card);

    function sidePlayers(side) {
      return rosters[side].map((p) => ({ ...p }));
    }

    function drawTable() {
      const wrapEl = card.querySelector(".fx-roster-wrap");
      wrapEl.innerHTML = "";
      const players = sidePlayers(activeSide);
      if (!players.length) {
        wrapEl.appendChild(el(`<p class="hint" style="margin:10px">该侧无匹配球员（球员库中无人所属队伍与该队队名一致；此类球员如需录入，请管理员用 /成长上报 按 UID 直录）</p>`));
        return;
      }
      const capHead = capStat ? `<th class="num">出场</th>` : "";
      const numHead = numStats
        .map(([k, def]) => `<th class="num">${esc(def.name || k)}</th>`)
        .join("");
      const t = el(`<table class="grid"><thead><tr>
        <th>球员</th>${capHead}${numHead}<th class="num">本期经验</th><th></th>
      </tr></thead><tbody></tbody></table>`);
      for (const p of players) t.querySelector("tbody").appendChild(playerRow(p));
      wrapEl.appendChild(t);
    }

    function playerRow(p) {
      const tr = document.createElement("tr");
      const appP = apps[p.player_uid];
      const locked = Boolean(appP && Number(appP.period_no) !== Number(detail.current_period_no));

      const tdName = document.createElement("td");
      tdName.innerHTML = `<b>${esc(p.name)}</b><br><small class="hint">${esc(p.player_uid)}</small>`;
      tr.appendChild(tdName);

      let capInp = null;
      if (capStat) {
        const td = document.createElement("td");
        td.className = "num";
        capInp = document.createElement("input");
        capInp.type = "checkbox";
        capInp.checked = Boolean(appP && Number(appP.stats[capStat[0]]) > 0);
        if (locked) capInp.disabled = true;
        td.appendChild(capInp);
        tr.appendChild(td);
      }

      const inputs = [];
      for (const [k, def] of numStats) {
        const td = document.createElement("td");
        td.className = "num";
        const inp = document.createElement("input");
        inp.type = "number";
        inp.step = "any";
        inp.min = "0";
        inp.title = def.name || k;
        if (appP && appP.stats[k] != null) inp.value = String(appP.stats[k]);
        if (locked) inp.disabled = true;
        td.appendChild(inp);
        tr.appendChild(td);
        inputs.push([k, inp]);
      }

      const tdXp = document.createElement("td");
      tdXp.className = "num";
      tdXp.textContent = appP ? fmtXp(appP.total_xp) : "—";
      if (locked) {
        const tag = el(`<small class="hint">第${appP.period_no}期锁定</small>`);
        tdXp.appendChild(document.createElement("br"));
        tdXp.appendChild(tag);
      }
      tr.appendChild(tdXp);

      const tdOp = document.createElement("td");
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn secondary";
      btn.textContent = appP ? "更新" : "保存";
      if (locked) {
        btn.disabled = true;
        btn.title = `该数据属于已结束的成长期第 ${appP.period_no} 期`;
      }
      btn.addEventListener("click", async () => {
        const stats = {};
        for (const [k, inp] of inputs) {
          const v = inp.value.trim();
          if (v !== "" && Number(v) !== 0) stats[k] = v;
        }
        if (capStat && capInp.checked) stats[capStat[0]] = "1";
        if (!Object.keys(stats).length) {
          toast(`${p.name} 的数据项均为空或 0（勾选「出场」可只记出场）`, true);
          return;
        }
        btn.disabled = true;
        try {
          const r = await api.post("fixtures/appearance", {
            rev_fixture_key: key,
            rev_side: activeSide,
            player_uid: p.player_uid,
            stats,
          });
          toast(`${r.name} 已保存：+${fmtXp(r.total_xp)} 经验`);
          ctx.refreshBadges();
          await reopenEditor();
        } catch (e) {
          toast(e.message, true);
          btn.disabled = false;
        }
      });
      tdOp.appendChild(btn);
      tr.appendChild(tdOp);
      return tr;
    }

    function redraw() {
      const toggle = card.querySelector(".fx-side-toggle");
      toggle.innerHTML = "";
      for (const side of ["home", "away"]) {
        const team = side === "home" ? fx.home_team : fx.away_team;
        const chip = el(`<button type="button" class="round-chip${side === activeSide ? " active" : ""}" data-side="${side}">
          <b>${side === "home" ? "主队" : "客队"} ${esc(team)}</b><small>${sidePlayers(side).length} 人</small>
        </button>`);
        chip.addEventListener("click", () => {
          activeSide = side;
          redraw();
        });
        toggle.appendChild(chip);
      }
      drawTable();
    }

    /** 保存成功后重拉详情并原地重绘。 */
    async function reopenEditor() {
      try {
        detail = await api.get(`fixtures/detail/${encodeURIComponent(key)}`);
      } catch (e) {
        toast(e.message, true);
        return;
      }
      rosters = detail.rosters || { home: [], away: [], unmatched: [] };
      apps = detail.appearances || {};
      redraw();
      await refreshRounds();
    }

    redraw();
    zone.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}
