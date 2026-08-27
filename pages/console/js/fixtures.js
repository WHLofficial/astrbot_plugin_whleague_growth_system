/* 赛程页：主场营收插件联动 —— 按轮次查看赛程，逐场录入/编辑双方球员数据。 */

import * as api from "./api.js";
import {
  el, esc, fmtXp, renderTable, toast, errorNote,
} from "./ui.js";

function todayStr() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export async function render(root, ctx) {
  root.appendChild(el(`<div>
    <h2 class="page-title">赛程</h2>
    <p class="page-sub">与主场营收插件联动：按轮次浏览真实对阵，点开任意一场即可为双方球员录入成长数据</p>
  </div>`));

  const banner = el(`<div class="card" id="fx-state"></div>`);
  const board = el(`<div class="card" id="fx-board"><div class="empty-state">加载中…</div></div>`);
  root.appendChild(banner);
  root.appendChild(board);

  let statDefs = [];
  try {
    const rule = await api.get("rule");
    statDefs = Object.entries((rule && rule.stats) || {});
  } catch { /* 无规则时仅浏览赛程 */ }

  let roundsData = null;
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
      // 默认选中最早还有未打比赛的轮次，否则第一轮
      const pending = roundsData.rounds.find((r) => r.played < r.total);
      currentRound = String(pending ? pending.round_no : roundsData.rounds[0].round_no);
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
    const zone = el(`<div class="round-chips" role="tablist"></div>`);
    for (const r of roundsData.rounds) {
      const no = String(r.round_no);
      const chip = el(`<button type="button" class="round-chip${no === currentRound ? " active" : ""}" data-round="${esc(no)}">
        <b>第 ${esc(no)} 轮</b><small>${r.played}/${r.total}</small>
      </button>`);
      chip.addEventListener("click", async () => {
        currentRound = no;
        for (const c of zone.querySelectorAll(".round-chip")) {
          c.classList.toggle("active", c.dataset.round === no);
        }
        await loadFixtures();
      });
      zone.appendChild(chip);
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
      const data = await api.get("fixtures", { round: currentRound });
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
    const wrap = el(`<div class="scroll-x"></div>`);
    wrap.appendChild(renderTable(
      [
        { label: "主队", render: (r) => `<b>${esc(r.home_team)}</b>` },
        {
          label: "比分",
          num: true,
          render: (r) => `<b>${esc(r.score || "—")}</b>`,
        },
        { label: "客队", render: (r) => `<b>${esc(r.away_team)}</b>` },
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
      b.addEventListener("click", () => openFixture(b.dataset.key));
    }
    zone.appendChild(wrap);
  }

  /* ─── 单场详情抽屉 ─────────────────────────── */

  async function openFixture(key) {
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
    const d = openDrawer(`第 ${fx.round_no} 轮 · ${fx.home_team} vs ${fx.away_team}`);
    drawBody(d.body);

    /* 服务端花名册：home/away 为按队名自动匹配的球员库玩家 */
    const rosters = detail.rosters || { home: [], away: [], unmatched: [] };
    /* 已录出场按 player_uid 聚合：{stats:{k:v}, period_no, total_xp} */
    const apps = {};
    for (const row of detail.appearances) {
      const p = (apps[row.player_uid] ||= {
        stats: {},
        period_no: row.period_no,
        total_xp: Number(row.total_xp) || 0,
      });
      if (row.stat_key !== null && row.stat_key !== undefined) {
        p.stats[row.stat_key] = Number(row.value) || 0;
      }
    }
    /* 未匹配球员被手动指定的球队视角：{uid: "home"|"away"} */
    const assigned = {};

    function dateField() {
      const wrap = el(`<label class="field fx-date"><span>比赛日期（首次保存生效）</span><input type="date"></label>`);
      wrap.querySelector("input").value = todayStr();
      return wrap;
    }

    function drawBody(body) {
      body.innerHTML = "";
      body.appendChild(el(`<div class="fx-headline">
        <span class="badge-period">${esc(fx.competition || "联赛")} · 第 ${esc(fx.season_number)} 赛季 第 ${esc(fx.window_seq)} 窗口</span>
        ${fx.result ? `<span class="tag ok">已完赛</span>` : `<span class="tag">未打</span>`}
        ${fx.weather ? `<span class="hint">${esc(fx.weather)}</span>` : ""}
      </div>`));

      const dateInput = dateField();
      body.appendChild(dateInput);

      const zoneHome = el(`<div></div>`);
      const zoneAway = el(`<div></div>`);
      const zoneBench = el(`<div></div>`);
      body.appendChild(zoneHome);
      body.appendChild(zoneAway);
      body.appendChild(zoneBench);
      redraw();

      function sidePlayers(side) {
        const base = rosters[side].map((p) => ({ ...p }));
        for (const [uid, asSide] of Object.entries(assigned)) {
          if (asSide !== side) continue;
          const p = rosters.unmatched.find((x) => x.player_uid === uid);
          if (p && !base.some((x) => x.player_uid === uid)) base.push({ ...p });
        }
        return base;
      }

      function section(side, titleText, zone) {
        zone.innerHTML = "";
        const cardEl = el(`<div style="margin-bottom:18px"></div>`);
        cardEl.appendChild(el(`<h4 style="margin:0 0 8px;font-family:var(--serif);font-size:14px">${esc(titleText)}</h4>`));
        const tableZone = el(`<div class="scroll-x"></div>`);
        const players = sidePlayers(side);
        if (!players.length) {
          tableZone.appendChild(el(`<p class="hint" style="margin:4px 0">无匹配球员（球员库中无人绑定该队名，可在下方未匹配名单中指定）</p>`));
        } else {
          const thead = statDefs.map(([key, def]) => `<th class="num">${esc(def.name || key)}</th>`).join("");
          const t = el(`<table class="grid"><thead><tr>
            <th>球员</th>${thead}<th class="num">本期经验</th><th></th>
          </tr></thead><tbody></tbody></table>`);
          for (const p of players) t.querySelector("tbody").appendChild(playerRow(side, p));
          tableZone.appendChild(t);
        }
        cardEl.appendChild(tableZone);
        zone.appendChild(cardEl);
      }

      function playerRow(side, p) {
        const tr = document.createElement("tr");
        const appP = apps[p.player_uid];
        const locked = Boolean(appP && Number(appP.period_no) !== Number(detail.current_period_no));

        const tdName = document.createElement("td");
        tdName.innerHTML = `<b>${esc(p.name)}</b><br><small class="hint">${esc(p.player_uid)}</small>`;
        tr.appendChild(tdName);

        const inputs = [];
        for (const [key, def] of statDefs) {
          const td = document.createElement("td");
          td.className = "num";
          const inp = document.createElement("input");
          inp.type = "number";
          inp.step = "any";
          inp.min = "0";
          inp.title = def.name || key;
          inp.style.width = "76px";
          if (appP && appP.stats[key] != null) inp.value = String(appP.stats[key]);
          if (locked) inp.disabled = true;
          td.appendChild(inp);
          tr.appendChild(td);
          inputs.push([key, inp]);
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
          for (const [key, inp] of inputs) {
            const v = inp.value.trim();
            if (v !== "" && Number(v) !== 0) stats[key] = v;
          }
          if (!Object.keys(stats).length) {
            toast(`${p.name} 的数据项均为空或 0`, true);
            return;
          }
          btn.disabled = true;
          try {
            const r = await api.post("fixtures/appearance", {
              rev_fixture_key: key,
              rev_side: side,
              player_uid: p.player_uid,
              stats,
              match_date: dateInput.querySelector("input").value || todayStr(),
            });
            toast(`${r.name} 已保存：+${fmtXp(r.total_xp)} 经验`);
            ctx.refreshBadges();
            await reopen();
          } catch (e) {
            toast(e.message, true);
            btn.disabled = false;
          }
        });
        tdOp.appendChild(btn);
        tr.appendChild(tdOp);
        return tr;
      }

      function bench() {
        zoneBench.innerHTML = "";
        const list = rosters.unmatched.filter((p) => !assigned[p.player_uid]);
        if (!list.length) return;
        zoneBench.appendChild(el(`<h4 style="margin:14px 0 8px;font-family:var(--serif);font-size:14px">未匹配名单<span class="hint">（球员所属队伍与双方队名都不一致，手动指定视角后即可录入）</span></h4>`));
        for (const p of list) {
          const hasApp = Boolean(apps[p.player_uid]);
          const rowEl = el(`<div class="bench-row">
            <b></b><small class="hint"></small><span style="flex:1"></span>
            <button type="button" class="btn secondary" data-side="home">记入主队</button>
            <button type="button" class="btn secondary" data-side="away">记入客队</button>
          </div>`);
          rowEl.querySelector("b").textContent = p.name;
          rowEl.querySelector("small").textContent =
            `${p.player_uid}${p.team ? " · 所属: " + p.team : ""}${hasApp ? " · 已有记录" : ""}`;
          for (const b of rowEl.querySelectorAll("button[data-side]")) {
            b.addEventListener("click", () => {
              assigned[p.player_uid] = b.dataset.side;
              redraw();
            });
          }
          zoneBench.appendChild(rowEl);
        }
      }

      function redraw() {
        section("home", `主队 ${fx.home_team}`, zoneHome);
        section("away", `客队 ${fx.away_team}`, zoneAway);
        bench();
      }

      /** 保存成功后重拉详情并原地重绘本抽屉。 */
      async function reopen() {
        try {
          detail = await api.get(`fixtures/detail/${encodeURIComponent(key)}`);
        } catch (e) {
          toast(e.message, true);
          return;
        }
        // 重算聚合（assigned 保留用户手动指定）
        for (const k of Object.keys(apps)) delete apps[k];
        for (const row of detail.appearances) {
          const p = (apps[row.player_uid] ||= {
            stats: {},
            period_no: row.period_no,
            total_xp: Number(row.total_xp) || 0,
          });
          if (row.stat_key !== null && row.stat_key !== undefined) {
            p.stats[row.stat_key] = Number(row.value) || 0;
          }
        }
        drawBody(d.body);
        await refreshRounds();
      }
    }
  }
}
