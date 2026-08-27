/* 规则页：数据项 / 里程碑分层展示 + 经验刻度示意。 */

import * as api from "./api.js";
import { el, esc, fmtXp, renderTable, xpGauge, errorNote } from "./ui.js";

export async function render(root, ctx) {
  root.appendChild(el(`<div>
    <h2 class="page-title">成长规则</h2>
    <p class="page-sub">当前生效的计分规则；修改请上传新的规则文件（导入页）</p>
    <div id="ru-body"><div class="empty-state">加载中…</div></div>
  </div>`));
  const body = root.querySelector("#ru-body");

  try {
    const rule = await api.get("rule");
    body.innerHTML = "";

    /* 数据项 */
    const statCard = el(`<div class="card"><h3 style="margin:0 0 10px;font-family:var(--serif);font-size:15px">数据项经验</h3></div>`);
    statCard.appendChild(
      renderTable(
        [
          { label: "键", render: (r) => `<span class="tag">${esc(r.key)}</span>` },
          { label: "名称", render: (r) => esc(r.name) },
          {
            label: "计分",
            num: true,
            render: (r) =>
              r.bands
                ? `分段：${r.bands.map((b) => `${fmtBand(b)}→${fmtXp(b.xp)}`).join("，")}`
                : `每单位 +${fmtXp(r.xp)}`,
          },
        ],
        Object.entries(rule.stats || {}).map(([key, def]) => ({ key, ...def })),
        "规则未定义任何数据项"
      )
    );
    body.appendChild(statCard);

    /* 里程碑 */
    const msCard = el(`<div class="card"><h3 style="margin:0 0 10px;font-family:var(--serif);font-size:15px">里程碑奖励</h3></div>`);
    const milestones = rule.milestones || [];
    if (!milestones.length) {
      msCard.appendChild(el(`<div class="empty-state">规则未配置里程碑<div class="hint">里程碑在统计值达到阈值/步长时一次性或重复发奖</div></div>`));
    } else {
      msCard.appendChild(
        renderTable(
          [
            { label: "范围", render: (r) => esc(rangeName(r.period)) },
            { label: "依据", render: (r) => esc(r.stat ? `${statLabel(rule, r.stat)}` : (r.stat_keys || []).map((k) => statLabel(rule, k)).join(" + ")) },
            { label: "条件", render: (r) => esc(condText(r)) },
            { label: "奖励", num: true, render: (r) => `+${esc(fmtXp(r.xp))}${r.step && r.threshold != null ? " / 每超出一段" : ""}` },
          ],
          milestones,
          ""
        )
      );
    }
    body.appendChild(msCard);

    /* 升级线示意（签名元素） */
    const levelXp = Number(rule.level_xp) || 0;
    if (levelXp > 0) {
      const lv = el(`<div class="card"><h3 style="margin:0 0 4px;font-family:var(--serif);font-size:15px">升级线</h3>
        <p style="color:var(--muted);font-size:12.5px;margin:0">本期经验每累计 ${fmtXp(levelXp)} 升一级（可被配置覆盖）</p></div>`);
      const sampleThresholds = [...new Set(milestones.filter((m) => m.period !== "career" && m.threshold != null).map((m) => m.threshold))].slice(0, 6).sort((a, b) => a - b);
      lv.appendChild(xpGauge(levelXp * 0.72, levelXp, sampleThresholds));
      body.appendChild(lv);
    }

    const meta = el(`<p style="color:var(--muted);font-size:12.5px"></p>`);
    meta.textContent = rule.imported_at ? `规则来源：${rule.label || "?"} · 导入于 ${String(rule.imported_at).slice(0, 10)}` : "";
    if (rule.imported_at) body.appendChild(meta);
  } catch (e) {
    body.innerHTML = "";
    if (/404|尚未/.test(e.message)) {
      body.appendChild(el(`<div class="empty-state">尚未导入成长规则<div class="hint">到「导入」页上传规则文件（支持 .json / .xlsx / .csv）</div></div>`));
    } else {
      body.appendChild(errorNote(e.message));
    }
  }
  return null;
}

function statLabel(rule, key) {
  return rule.stats?.[key]?.name || key;
}

function rangeName(p) {
  return p === "period" ? "单期" : p === "match" ? "单场" : p === "career" ? "生涯" : p;
}

function fmtBand(b) {
  const lo = b.min ?? 0;
  const hi = b.max == null ? "∞" : b.max;
  return `${lo}~${hi}`;
}

function condText(r) {
  if (r.step != null && r.threshold != null) return `首达 ${fmtXp(r.threshold)}，此后每 ${fmtXp(r.step)}`;
  if (r.threshold != null) return `达到 ${fmtXp(r.threshold)}`;
  return r.desc || "—";
}
