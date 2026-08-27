/* 成长期页：当前期状态、历史期列表与结算表、推进（危险操作二次确认）。 */

import * as api from "./api.js";
import {
  el, esc, fmtXp, renderTable, openDrawer, confirmDialog, toast, errorNote, statCell,
} from "./ui.js";

export async function render(root, ctx) {
  root.appendChild(el(`<div>
    <h2 class="page-title">成长期</h2>
    <p class="page-sub">联赛以「期」为单位结算成长；推进时本期经验清零并落成快照</p>
    <div id="pd-body"><div class="empty-state">加载中…</div></div>
  </div>`));
  const body = root.querySelector("#pd-body");

  async function load() {
    body.innerHTML = `<div class="empty-state">加载中…</div>`;
    try {
      const st = await api.get("periods");
      draw(st);
    } catch (e) {
      body.innerHTML = "";
      body.appendChild(errorNote(e.message));
    }
  }

  function draw(st) {
    body.innerHTML = "";
    const grid = el(`<div class="stat-grid"></div>`);
    if (st.current) {
      grid.appendChild(statCell(`第 ${st.current.period_no} 期`, "当前成长期"));
      grid.appendChild(statCell(esc(st.current.name), "期名"));
      grid.appendChild(statCell(String(st.player_count ?? "—"), "参与球员"));
      grid.appendChild(statCell(fmtXp(st.current_xp), "本期经验池"));
    }
    body.appendChild(grid);

    const actionCard = el(`<div class="card"></div>`);
    if (!st.current) {
      actionCard.innerHTML = `<div class="empty-state">尚未开启任何成长期<div class="hint">先在「导入」页上传规则文件，再输入期名开第一期</div></div>`;
    } else {
      actionCard.innerHTML = `
        <h3 style="margin:0 0 10px;font-family:var(--serif);font-size:15px">推进到下一期</h3>
        <div class="form-row">
          <label class="field"><span>新期名</span><input type="text" id="adv-name" placeholder="如：2026夏季联赛"></label>
          <label class="field" style="flex:0 0 auto;flex-direction:row;display:flex;align-items:center;gap:6px">
            <input type="checkbox" id="adv-carry" checked style="width:auto">
            <span style="margin:0">等级结转（本期经验折算等级）</span>
          </label>
          <button type="button" class="btn danger" id="adv-go">推进成长期</button>
        </div>
        <p style="color:var(--muted);font-size:12.5px;margin:10px 0 0">推进后会关闭第 ${st.current.period_no} 期并为全体球员生成快照；该操作不可撤销。</p>`;
      actionCard.querySelector("#adv-go").addEventListener("click", async () => {
        const name = actionCard.querySelector("#adv-name").value.trim();
        const carry = actionCard.querySelector("#adv-carry").checked;
        if (!name) { toast("请填写新期名", true); return; }
        const ok = await confirmDialog({
          title: "确认推进成长期？",
          message: `将关闭「第 ${st.current.period_no} 期 ${st.current.name}」，开启「${name}」。\n等级结转：${carry ? "开启" : "关闭"}\n此操作不可撤销。`,
          danger: true,
          confirmText: "确认推进",
        });
        if (!ok) return;
        try {
          const r = await api.post("periods/advance", { name, carryover: carry });
          toast(`已开启第 ${r.opened_no} 期「${r.opened_name}」`);
          ctx.refreshBadges();
          load();
        } catch (e) {
          toast(e.message, true);
        }
      });
    }
    body.appendChild(actionCard);

    /* 历史期 */
    const listCard = el(`<div class="card"><h3 style="margin:0 0 10px;font-family:var(--serif);font-size:15px">历期汇总</h3></div>`);
    const rows = (st.periods || []).map((p) => ({
      ...p,
      _sum: (st.summaries || []).find((s) => s.period_no === p.period_no) || {},
    }));
    const table = renderTable(
      [
        { label: "期数", num: true, render: (r) => `第 ${r.period_no} 期` },
        { label: "期名", render: (r) => esc(r.name) + (r.is_current ? ' <span class="tag ok">进行中</span>' : "") },
        { label: "起止", render: (r) => esc((r.started_at || "?").slice(0, 10)) + " ~ " + (r.ended_at ? esc(r.ended_at.slice(0, 10)) : "—") },
        { label: "升级人数", num: true, render: (r) => esc(r._sum.upgraded_count ?? "—") },
        { label: "发出经验", num: true, render: (r) => esc(r._sum.xp_total != null ? fmtXp(r._sum.xp_total) : "—") },
        { label: "", render: (r) => `<button type="button" class="btn secondary" data-no="${r.period_no}" style="min-height:28px;padding:2px 12px;font-size:12.5px">结算表</button>` },
      ],
      rows,
      "暂无历史成长期"
    );
    table.addEventListener("click", (ev) => {
      const btn = ev.target.closest("button[data-no]");
      if (btn) showResult(btn.dataset.no);
    });
    listCard.appendChild(table);
    body.appendChild(listCard);
  }

  async function showResult(no) {
    const d = openDrawer(`第 ${no} 期结算表`);
    try {
      const r = await api.get(`periods/${encodeURIComponent(no)}/result`);
      d.body.innerHTML = `<p style="color:var(--muted);font-size:13px;margin-top:0"></p>`;
      d.body.querySelector("p").textContent =
        `${r.period.name} · 升级 ${r.rows.filter((x) => x.upgraded).length} 人`;
      d.body.appendChild(
        renderTable(
          [
            { label: "#", num: true, render: (_r, i) => String(i + 1) },
            { label: "UID", render: (row) => esc(row.player_uid) },
            { label: "名字", render: (row) => esc(row.player_name || row.name || "") },
            { label: "期末等级", num: true, render: (row) => esc(row.level_end) },
            { label: "+级", num: true, render: (row) => `+${esc(row.level_gained)}` },
            { label: "本期经验", num: true, render: (row) => esc(fmtXp(row.xp_period)) },
            { label: "结转", num: true, render: (row) => esc(fmtXp(row.xp_carryover)) },
          ],
          r.rows,
          "本期没有快照数据"
        )
      );
      const dl = el(`<button type="button" class="btn secondary" style="margin-top:12px">下载结算文件</button>`);
      dl.addEventListener("click", () => api.downloadExport(no));
      d.body.appendChild(dl);
    } catch (e) {
      d.body.innerHTML = "";
      d.body.appendChild(errorNote(e.message));
    }
  }

  await load();
  return load;
}
