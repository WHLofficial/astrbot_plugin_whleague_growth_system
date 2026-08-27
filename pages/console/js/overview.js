/* 总览页：统计卡 + 当前成长期 + 近期比赛。 */

import * as api from "./api.js";
import { el, esc, fmtXp, statCell, renderTable, errorNote } from "./ui.js";

export async function render(root, ctx) {
  root.appendChild(el(`<div>
    <h2 class="page-title">总览</h2>
    <p class="page-sub">联赛成长体系一页速览</p>
    <div id="ov-body"><div class="empty-state">加载中…</div></div>
  </div>`));
  const body = root.querySelector("#ov-body");

  try {
    const ov = await api.get("stats/overview");
    body.innerHTML = "";

    const stats = el(`<div class="stat-grid"></div>`);
    stats.appendChild(statCell(String(ov.player_count), "在册球员"));
    stats.appendChild(statCell(String(ov.match_count), "累计比赛"));
    stats.appendChild(statCell(fmtXp(ov.current_xp), "当前期经验池"));
    stats.appendChild(statCell(fmtXp(ov.career_xp), "生涯经验总和"));
    if (ov.pending_imports > 0) {
      const cell = statCell(String(ov.pending_imports), "待确认导入", true);
      cell.style.cursor = "pointer";
      cell.addEventListener("click", () => ctx.go("imports"));
      stats.appendChild(cell);
    }
    body.appendChild(stats);

    const period = el(`<div class="card"></div>`);
    if (ov.period) {
      period.innerHTML = `<div style="display:flex;align-items:baseline;gap:12px;flex-wrap:wrap">
        <span style="font-family:var(--serif);font-size:17px;font-weight:700"></span>
        <span class="badge-period"></span>
        <span style="color:var(--muted);font-size:12.5px" id="ov-period-meta"></span>
      </div>`;
      period.querySelector("span[style*=serif]").textContent = `第 ${ov.period.period_no} 期`;
      period.querySelector(".badge-period").textContent = ov.period.name;
      const meta = period.querySelector("#ov-period-meta");
      meta.textContent = `始于 ${fmtDateSafe(ov.period.started_at)} · 球员 ${ov.player_count} 人`;
      const goPeriods = el(`<button type="button" class="btn secondary" style="margin-top:10px">查看成长期与结算</button>`);
      goPeriods.addEventListener("click", () => ctx.go("periods"));
      period.appendChild(goPeriods);
    } else {
      period.innerHTML = `<div class="empty-state">还没有开启任何成长期<div class="hint">先在「导入」页上传规则文件，再于「成长期」页开期</div></div>`;
    }
    body.appendChild(period);

    const recentCard = el(`<div class="card"><h3 style="margin:0 0 10px;font-family:var(--serif);font-size:15px">近期比赛</h3></div>`);
    recentCard.appendChild(
      renderTable(
        [
          { label: "日期", render: (r) => esc(r.match_date) },
          { label: "对手", render: (r) => esc(r.opponent) },
          { label: "出场", num: true, render: (r) => esc(r.player_count) },
          { label: "发出经验", num: true, render: (r) => esc(fmtXp(r.xp_total)) },
        ],
        ov.recent_matches || [],
        "还没有比赛记录，去「比赛」页录入第一场"
      )
    );
    body.appendChild(recentCard);
  } catch (e) {
    body.innerHTML = "";
    body.appendChild(errorNote(e.message));
  }
  return null;
}

function fmtDateSafe(s) {
  return s ? String(s).slice(0, 10) : "—";
}
