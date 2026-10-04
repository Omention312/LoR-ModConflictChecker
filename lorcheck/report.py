# -*- coding: utf-8 -*-
"""生成自包含的 HTML 报告（中文）。"""

from __future__ import annotations

import html
import json
import time
from pathlib import Path
from typing import Iterable, List, Sequence

from . import rules
from .config import CATEGORY_CN, SEV_CN, SEV_FATAL, SEV_HIGH, SEV_INFO, SEV_LOW, SEV_MID
from .engine import ScanResult

CSS = """
:root{--bg:#12141a;--card:#1b1e26;--line:#2b3040;--fg:#e6e8ef;--dim:#9aa1b4;
--fatal:#ff5c6c;--high:#ff9f43;--mid:#f5d76e;--low:#5bc0de;--info:#8e9bb3;--ok:#4fd18b;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.6 "Microsoft YaHei","Segoe UI",system-ui,sans-serif;}
.wrap{max-width:1180px;margin:0 auto;padding:28px 20px 80px}
h1{font-size:24px;margin:0 0 6px}
h2{font-size:18px;margin:34px 0 12px;padding-left:10px;border-left:4px solid #4a5573}
h3{font-size:15px;margin:18px 0 8px}
.sub{color:var(--dim);font-size:13px;margin-bottom:18px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin:10px 0}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:7px 9px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--dim);font-weight:600;background:#20242e}
code,.mono{font-family:Consolas,"Cascadia Mono",monospace;font-size:12.5px}
.kv td:first-child{color:var(--dim);width:190px;white-space:nowrap}
.badge{display:inline-block;padding:1px 8px;border-radius:99px;font-size:12px;font-weight:700;
border:1px solid currentColor;white-space:nowrap}
.s4{color:var(--fatal)}.s3{color:var(--high)}.s2{color:var(--mid)}.s1{color:var(--low)}.s0{color:var(--info)}
.f{border-left:4px solid var(--line)}
.f.s4{border-left-color:var(--fatal)}.f.s3{border-left-color:var(--high)}
.f.s2{border-left-color:var(--mid)}.f.s1{border-left-color:var(--low)}
.f.s0{border-left-color:var(--info)}
.f h3{margin:0 0 6px;display:flex;gap:8px;align-items:baseline;flex-wrap:wrap}
.f .detail{color:#c9cee0;margin:6px 0}
.f .advice{color:var(--ok);margin:6px 0}
ul.ev{margin:6px 0 0 18px;padding:0;color:var(--dim)}
ul.ev li{margin:2px 0;word-break:break-all}
.tag{display:inline-block;background:#262b38;border:1px solid var(--line);border-radius:6px;
padding:0 6px;margin:2px 4px 2px 0;font-size:12px;color:#b9c1d6}
.clash{background:#3a1f27;border-color:#7a3446;color:#ffb3bf}
pre{background:#0e1015;border:1px solid var(--line);border-radius:8px;padding:12px;
overflow:auto;max-height:520px;font-size:12.5px;color:#b9c1d6}
.summary{display:flex;gap:10px;flex-wrap:wrap}
.summary div{flex:1 1 130px;background:var(--card);border:1px solid var(--line);
border-radius:10px;padding:12px}
.summary b{display:block;font-size:26px;line-height:1.2}
.small{font-size:12.5px;color:var(--dim)}
ol.steps{margin:8px 0 0 20px;padding:0}
ol.steps li{margin:5px 0}
.rank{font-weight:700;color:var(--dim);width:34px}
"""

SEV_CLS = {4: "s4", 3: "s3", 2: "s2", 1: "s1", 0: "s0"}


def esc(x) -> str:
    return html.escape(str(x if x is not None else ""))


def _sev_badge(sev: int) -> str:
    return f'<span class="badge {SEV_CLS.get(sev,"s0")}">{SEV_CN.get(sev,"?")}</span>'


def _finding_html(f: rules.Finding) -> str:
    ev = "".join(f"<li>{esc(e)}</li>" for e in f.evidence[:40])
    clash = '<span class="badge clash">拼点相关</span>' if f.clash_relevant else ""
    advice = f'<div class="advice">处置建议：{esc(f.advice)}</div>' if f.advice else ""
    code = f'<span class="small mono">{esc(f.code)}</span>'
    return f"""<div class="card f {SEV_CLS.get(f.severity,'s0')}">
<h3>{_sev_badge(f.severity)} <span>{esc(f.title)}</span> {clash} {code}</h3>
<div class="detail">{esc(f.detail)}</div>
{advice}
<ul class="ev">{ev}</ul></div>"""


def _journal_html(journal) -> str:
    """接待 / 关卡症状档案（没有记录时返回空串）。"""
    if journal is None or not getattr(journal, "observations", None):
        return ""
    from . import journal as jm
    s = jm.summarize(journal)

    def tbl(title, data, key_cn):
        rows = "".join(
            f"<tr><td>{esc(key_cn(k))}</td><td>{v.get('frozen',0)}</td>"
            f"<td>{v.get('ok',0)}</td><td>{v.get('unclear',0)}</td></tr>"
            for k, v in data.items())
        return (f"<h3>{esc(title)}</h3><table>"
                f"<tr><th>名称</th><th>卡死</th><th>正常</th><th>不确定</th></tr>"
                f"{rows}</table>")

    obs_rows = "".join(
        f"<tr><td>{esc(o.ts)}</td><td>{esc(o.key)}</td><td>{esc(o.stage_cn)}</td>"
        f"<td>{esc(o.outcome_cn)}</td>"
        f"<td>{len(o.disabled) if o.disabled else '无'}</td>"
        f"<td>{esc(o.source_cn)}</td><td>{esc(o.note)}</td></tr>"
        for o in journal.observations)

    notes = ""
    if s.stage_hint:
        notes += f'<div class="advice">{esc(s.stage_hint)}</div>'
    if s.reception_specific:
        notes += (f'<div class="advice">卡死只出现在：'
                  f'{esc("、".join(s.frozen_receptions))}；而 '
                  f'{esc("、".join(s.ok_receptions))} 是正常的 —— '
                  f'症状可能与特定接待的内容有关。</div>')
    if s.unstable:
        items = "".join(
            f"<li>{esc(rec)} / {esc(jm.STAGE_CN.get(st, st))} / 禁用 {n} 个："
            f"{esc('、'.join(jm.OUTCOME_CN.get(k, k) for k in kinds))}</li>"
            for rec, st, n, kinds in s.unstable)
        notes += ('<div class="card f s4"><h3>'
                  '<span class="badge s4">不稳定</span>'
                  '<span>同一接待 + 同一步骤 + 同一组禁用 mod 出现了两种结果</span></h3>'
                  '<div class="detail">这种情况下二分排查的结论不可信，'
                  '请对同一组多重测几次。</div>'
                  f'<ul class="ev">{items}</ul></div>')

    return f"""<h2>⑦ 接待 / 关卡症状档案</h2>
<div class="card">
<div class="small">共 {s.total} 条记录：卡死 {s.by_outcome.get('frozen',0)}、
正常 {s.by_outcome.get('ok',0)}、不确定 {s.by_outcome.get('unclear',0)}。
用来区分“启动慢”和“拼点卡死”这两类完全不同的问题。</div>
{notes}
{tbl("按接待 / 关卡", s.by_reception, lambda k: k)}
{tbl("按卡住的阶段", s.by_stage, lambda k: jm.STAGE_CN.get(k, k))}
<h3>全部记录</h3>
<table><tr><th>时间</th><th>接待 / 关卡</th><th>阶段</th><th>结果</th>
<th>当时禁用的 mod</th><th>来源</th><th>备注</th></tr>{obs_rows}</table>
</div>
"""


def render_html(result: ScanResult, journal=None) -> str:
    p = result.paths
    counts = result.counts()

    # --- 环境 ---
    env_rows = [
        ("Steam 根目录", p.steam_root),
        ("Steam 库", " ; ".join(str(x) for x in p.libraries)),
        ("游戏目录", p.game_dir),
        ("创意工坊 mod 目录", p.workshop_dir),
        ("本地 Mods 目录", p.local_mods_dir),
        ("BaseMod 目录", p.basemod_dir),
        ("Player.log", str(result.log.path) if result.log.path else "未找到"),
        ("日志大小 / 行数",
         f"{result.log.size/1048576:.2f} MB / {result.log.total_lines} 行"
         if result.log.exists else "—"),
    ]
    env_html = "".join(
        f"<tr><td>{esc(k)}</td><td class='mono'>{esc(v)}</td></tr>" for k, v in env_rows)

    # --- 摘要 ---
    summary = "".join(
        f'<div><b class="{SEV_CLS[s]}">{counts.get(s,0)}</b>'
        f'<span class="small">{SEV_CN[s]}问题</span></div>'
        for s in (SEV_FATAL, SEV_HIGH, SEV_MID, SEV_LOW, SEV_INFO))

    # --- 拼点专项 ---
    clash_findings = [f for f in result.findings if f.clash_relevant]
    top = result.suspects[:8]
    suspect_rows = "".join(
        f"<tr><td class='rank'>{i+1}</td><td>{esc(s.mod.display)}<br>"
        f"<span class='small mono'>{esc(s.mod.path)}</span></td>"
        f"<td><b>{s.score}</b></td>"
        f"<td>{''.join(f'<div class=small>· {esc(r)}</div>' for r in s.reasons[:6])}</td></tr>"
        for i, s in enumerate(top))

    steps = """<ol class="steps">
<li>先备份 <code>LibraryOfRuina_Data\\Mods</code> 与创意工坊订阅列表。</li>
<li>按上面的嫌疑排行，<b>一次只禁用 1～2 个</b>，每次禁用后进入同一個接待、制造拼点，验证是否仍卡死。</li>
<li>优先处理“致命”和“高”级别、且与拼点/骰子相关的条目（红色与橙色卡片）。</li>
<li>如果禁用单个 mod 无效，用二分法：先禁用一半 mod，再逐次缩小范围。</li>
<li>若某类问题只在特定 mod 组合下出现，把它记录进本报告底部的“复现记录”。</li>
<li>改完 mod 后重启游戏（部分框架需要进入主菜单后重启一次才生效），并重新运行本检测器对比结果。</li>
</ol>"""

    # --- 全部冲突 ---
    findings_html = "".join(_finding_html(f) for f in result.findings) \
        or '<div class="card">未发现明显冲突。</div>'

    # --- Mod 清单 ---
    mod_rows = "".join(
        f"<tr><td>{esc(m.title or '(无标题)')}</td>"
        f"<td class='mono'>{esc(m.package_id)}</td>"
        f"<td>{'工坊' if m.source=='workshop' else '本地'}</td>"
        f"<td>{'启用' if m.enabled is True else ('已禁用' if m.enabled is False else '未知')}</td>"
        f"<td class='mono'>{esc(m.folder)}</td>"
        f"<td>{len(m.entries)}</td><td>{len(m.assemblies)}</td>"
        f"<td>{len(m.clash_types)}</td>"
        f"<td class='small'>{esc('；'.join(m.issues))}</td></tr>"
        for m in sorted(result.mods, key=lambda m: (m.enabled is not True,
                                                    m.source, m.folder)))

    # --- 日志摘要 ---
    log = result.log
    log_rows = [
        ("重复 LorId 异常", len(log.same_key)),
        ("卡住的 mod 初始化器",
         f"{log.stall_owner_type}（{log.stall_owner_dll}）" if log.stall_owner_type
         else "—"),
        ("卡住时长", f"{log.stall_seconds:.0f} 秒" if log.stall_seconds else "—"),
        ("重复程序集名报错", sum(log.dup_assemblies.values())),
        ("重复关键字报错", len(log.already_exists)),
        ("捕获异常数", len(log.exceptions)),
        ("缺失资源路径", len(log.missing_paths)),
        ("找不到的卡组", "、".join(log.deck_not_found[:5]) or "—"),
        ("初始化卡住", "是" if log.initializer_stall else "否"),
        ("日志尾部连续重复行", f"{log.tail_repeat[1]} 次：{log.tail_repeat[0][:80]}"),
        ("卡死发生在战斗中", "是" if (log.tail_is_battle or log.tail_battle_markers >= 3) else "否"),
        ("有崩溃上报标记", "是" if log.crash_report_marker else "否"),
    ]
    log_html = "".join(f"<tr><td>{esc(k)}</td><td>{esc(v)}</td></tr>" for k, v in log_rows)
    tail_html = esc("\n".join(result.log_tail[-80:]))

    journal_html = _journal_html(journal)
    # 没有症状档案时不留空段，编号保持连续
    rec_no = "⑧" if journal_html else "⑦"

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>废墟图书馆 Mod 冲突检测报告</title><style>{CSS}</style></head><body>
<div class="wrap">
<h1>废墟图书馆 Mod 冲突检测报告</h1>
<div class="sub">生成时间 {esc(result.started_at)} ｜ 耗时 {result.elapsed:.1f}s ｜
共 {len(result.mods)} 个 mod（工坊 {len(result.workshop_mods)} / 本地 {len(result.local_mods)}）</div>

<h2>① 环境信息</h2>
<div class="card"><table class="kv">{env_html}</table></div>

<h2>② 结论摘要</h2>
<div class="summary">{summary}</div>

<h2>③ 接待拼点卡死 · 专项排查</h2>
<div class="card">
<div class="small">按“日志证据 + 拼点相关代码改动 + 危险程序集重复”综合评分排序。
分数越高越可疑，但<b>不是结论</b>，请配合下面的处置步骤验证。</div>
<h3>怀疑度排行</h3>
<table><tr><th>#</th><th>Mod</th><th>分数</th><th>命中原因</th></tr>{suspect_rows}</table>
</div>
<div class="card"><h3>处置步骤</h3>{steps}</div>
<h3>与拼点直接相关的冲突项（{len(clash_findings)} 条）</h3>
{''.join(_finding_html(f) for f in clash_findings) or '<div class="card">无</div>'}

<h2>④ 全部冲突明细（{len(result.findings)} 条）</h2>
{findings_html}

<h2>⑤ Mod 清单（{len(result.mods)}）</h2>
<div class="card"><table>
<tr><th>名称</th><th>包 ID</th><th>来源</th><th>状态</th><th>目录</th><th>数据条目</th>
<th>程序集</th><th>拼点类型</th><th>备注</th></tr>{mod_rows}</table></div>

<h2>⑥ 日志分析</h2>
<div class="card"><table class="kv">{log_html}</table></div>
<h3>日志尾部（最后 80 行）</h3><pre>{tail_html}</pre>

{journal_html}
<h2>{rec_no} 复现记录（手工填写）</h2>
<div class="card">
<table><tr><th>禁用的 mod</th><th>是否仍卡死</th><th>备注</th></tr>
<tr><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>
<tr><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>
<tr><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>
<tr><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>
</table></div>
<div class="sub">由「废墟图书馆 Mod 冲突检测器」生成 · AppID 1256670</div>
</div></body></html>"""


def write_report(result: ScanResult, out_dir: Path, journal=None) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = out_dir / f"mod冲突报告-{stamp}.html"
    out.write_text(render_html(result, journal=journal), encoding="utf-8")
    # 同时导出机器可读的 JSON
    try:
        (out_dir / f"mod冲突报告-{stamp}.json").write_text(
            json.dumps(result_to_dict(result), ensure_ascii=False, indent=2),
            encoding="utf-8")
    except OSError:
        pass
    return out


def result_to_dict(result: ScanResult) -> dict:
    return {
        "generated_at": result.started_at,
        "elapsed_seconds": round(result.elapsed, 2),
        "paths": result.paths.as_dict(),
        "mod_count": len(result.mods),
        "findings": [{
            "code": f.code, "severity": f.severity, "severity_cn": f.sev_cn,
            "title": f.title, "detail": f.detail, "advice": f.advice,
            "clash_relevant": f.clash_relevant, "evidence": f.evidence,
            "mods": f.mods,
        } for f in result.findings],
        "suspects": [{
            "folder": s.mod.folder, "package_id": s.mod.package_id,
            "title": s.mod.title, "source": s.mod.source,
            "score": s.score, "reasons": s.reasons,
        } for s in result.suspects],
        "mods": [{
            "folder": m.folder, "source": m.source, "package_id": m.package_id,
            "title": m.title, "version": m.version, "manifest": m.manifest,
            "is_framework": m.is_framework, "file_count": m.file_count,
            "assemblies": sorted(m.assemblies), "clash_types": sorted(m.clash_types),
            "data_categories": {k: len(v) for k, v in m.xml_by_category.items()},
            "entry_count": len(m.entries), "issues": m.issues, "path": str(m.path),
        } for m in result.mods],
        "log": {
            "path": str(result.log.path) if result.log.path else "",
            "size": result.log.size, "lines": result.log.total_lines,
            "same_key": result.log.same_key,
            "dup_assemblies": result.log.dup_assemblies,
            "already_exists": result.log.already_exists,
            "missing_paths": result.log.missing_paths,
            "exceptions": [{
                "type": e.type, "message": e.message, "line_no": e.line_no,
                "folder_ids": e.folder_ids, "stack": e.stack[:12],
            } for e in result.log.exceptions],
            "initializer_stall": result.log.initializer_stall,
            "stall_owner_type": result.log.stall_owner_type,
            "stall_owner_dll": result.log.stall_owner_dll,
            "stall_owner_line": result.log.stall_owner_line,
            "stall_seconds": result.log.stall_seconds,
            "deck_not_found": result.log.deck_not_found,
            "tail_is_battle": result.log.tail_is_battle,
            "tail_battle_markers": result.log.tail_battle_markers,
            "repeat_wait_max": result.log.repeat_wait_max,
            "tail_repeat": {"line": result.log.tail_repeat[0],
                            "count": result.log.tail_repeat[1]},
        },
    }
