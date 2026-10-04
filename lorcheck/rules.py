# -*- coding: utf-8 -*-
"""冲突规则引擎：把静态扫描 + 日志证据转成可执行的结论。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from . import scan_mods as sm
from .config import (CATEGORY_CN, CLASH_KEYWORDS, FRAMEWORK_ASSEMBLIES,
                     RISKY_ASSEMBLIES, SEV_CN, SEV_FATAL, SEV_HIGH, SEV_INFO,
                     SEV_LOW, SEV_MID)
from .scan_log import MIN_SLOW_SECONDS, LogException, LogReport

RE_NS_TOKEN = re.compile(r"^at\s+([A-Za-z_][A-Za-z0-9_]*)")

# 会被游戏按 LorId 建字典、重复即抛异常的核心数据类别
CORE_CATEGORIES = ("card", "passive", "book", "book_enemy", "book_librarian",
                   "enemy", "stage", "deck", "dropbook", "droptable")

# mod 里绝不该出现的"游戏本体程序集"——出现说明作者把 Managed 目录整个打包了
GAME_LIB_NAMES = {
    "assembly-csharp", "assembly-csharp-firstpass", "mscorlib", "unityengine",
    "spine-unity", "facepunch.steamworks.win64", "xgamingruntime", "ookii.dialogs",
    "unity.mathematics", "unity.textmeshpro", "unity.postprocessing.runtime",
    "unity.burst", "unity.collections", "system", "system.core", "system.xml",
}

EXC_SEVERITY = (
    ("MissingMethodException", SEV_HIGH),
    ("MissingFieldException", SEV_HIGH),
    ("TypeLoadException", SEV_HIGH),
    ("FileNotFoundException", SEV_HIGH),
    ("DirectoryNotFoundException", SEV_HIGH),
    ("IOException", SEV_HIGH),
    ("ArgumentException", SEV_HIGH),
    ("NullReferenceException", SEV_HIGH),
    ("IndexOutOfRangeException", SEV_MID),
    ("InvalidOperationException", SEV_MID),
    ("KeyNotFoundException", SEV_MID),
)


@dataclass
class Finding:
    code: str
    severity: int
    title: str
    detail: str
    evidence: List[str] = field(default_factory=list)
    mods: List[str] = field(default_factory=list)      # mod uid
    clash_relevant: bool = False
    advice: str = ""

    @property
    def sev_cn(self) -> str:
        return SEV_CN.get(self.severity, "?")


@dataclass
class Suspect:
    mod: sm.ModInfo
    score: int
    reasons: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 归因工具
# ---------------------------------------------------------------------------
def _assembly_stems(mods: Sequence[sm.ModInfo]) -> Dict[str, List[sm.ModInfo]]:
    idx: Dict[str, List[sm.ModInfo]] = {}
    for m in mods:
        for low in m.assemblies:
            idx.setdefault(low, []).append(m)
    return idx


def _exception_tokens(e: LogException) -> List[str]:
    toks: List[str] = []
    for line in e.stack:
        s = line.strip()
        m = RE_NS_TOKEN.match(s)
        if m:
            t = m.group(1)
            if t not in toks and t.lower() not in ("unityengine", "system", "lor_input"):
                toks.append(t)
            continue
        if ":" in s and "(" in s:
            head = s.split(":", 1)[0].strip()
            if re.fullmatch(r"[A-Za-z_][\w.]*", head):
                root = head.split(".")[0]
                if root not in toks and root.lower() not in ("unityengine", "system"):
                    toks.append(root)
    return toks


def attribute_exception(mods: Sequence[sm.ModInfo], e: LogException,
                        asm_index: Optional[Dict[str, List[sm.ModInfo]]] = None
                        ) -> List[sm.ModInfo]:
    """把异常归因到 mod：先看堆栈里的文件路径，再看命名空间/程序集名。"""
    by_uid = {m.uid: m for m in mods}
    hit: List[sm.ModInfo] = []
    for fid in e.folder_ids:
        m = by_uid.get(fid)
        if m and m not in hit:
            hit.append(m)
    if hit:
        return hit
    asm_index = asm_index if asm_index is not None else _assembly_stems(mods)
    for tok in _exception_tokens(e):
        for m in asm_index.get(tok.lower(), []):
            if m not in hit:
                hit.append(m)
    return hit


def _mods_with_package(mods: Sequence[sm.ModInfo], pkg: str) -> List[sm.ModInfo]:
    p = pkg.strip().lower()
    if not p:
        return []
    out = [m for m in mods if m.package_id.strip().lower() == p]
    if out:
        return out
    return [m for m in mods if p in m.package_id.lower() or p in m.folder.lower()]


# ---------------------------------------------------------------------------
# 规则
# ---------------------------------------------------------------------------
def build_findings(mods: Sequence[sm.ModInfo], log: LogReport) -> List[Finding]:
    findings: List[Finding] = []
    asm_index = _assembly_stems(mods)
    by_uid = {m.uid: m for m in mods}
    live = [m for m in mods if not m.is_sample]
    # 跨 mod 冲突只看"当前启用"的 mod：已禁用/已退订的 mod 之间重复没有意义。
    # 读不到启用状态时（enabled 全为 None）退回全量。
    known_state = any(m.enabled is not None for m in live)
    active = [m for m in live if m.enabled is not False] if known_state else live

    # --- 1. 同一 mod 内同类目 ID 重复 -------------------------------------
    for m in active:
        for cat in sorted(set(m.xml_by_category)):
            if cat not in CORE_CATEGORIES:
                continue
            ids = m.ids_for(cat)
            dups = {k: v for k, v in ids.items() if len(v) > 1}
            if not dups:
                continue
            ev = [f"{CATEGORY_CN.get(cat, cat)} 条目 ID={k} 出现 {len(v)} 次："
                  f"{'; '.join(f'{n} ({f})' for n, f in v[:3])}"
                  for k, v in list(dups.items())[:5]]
            findings.append(Finding(
                code="DUP_ID_IN_MOD", severity=SEV_HIGH,
                title=f"{m.display} 内部有 {len(dups)} 组重复 ID（{CATEGORY_CN.get(cat, cat)}）",
                detail=("同一个 mod 在同一数据类别里出现重复条目 ID。游戏把 “包ID:条目ID” "
                        "组成 LorId 放进字典，重复注册会直接抛 "
                        "ArgumentException: An item with the same key has already been added，"
                        "该 mod 的初始化随之中断——这正是本次日志里出现的异常类型。"),
                evidence=ev, mods=[m.uid],
                clash_relevant=cat in ("card", "passive", "enemy", "deck"),
                advice="确认该 mod 是否最新版本；若你手工合并过多个 mod 的数据文件，"
                       "需要重新编号。可先禁用该 mod 验证。",
            ))

    # --- 2. 包 ID 冲突 / 重复安装 ----------------------------------------
    pkg_map: Dict[str, List[sm.ModInfo]] = {}
    for m in active:
        if m.package_id:
            pkg_map.setdefault(m.package_id.lower(), []).append(m)
    for pkg, group in pkg_map.items():
        if len(group) < 2:
            continue
        srcs = {g.source for g in group}
        code = "DUP_MOD_INSTALL" if (len(srcs) > 1 or len(group) > 2) else "DUP_PACKAGE_ID"
        findings.append(Finding(
            code=code, severity=SEV_FATAL,
            title=f"包 ID “{group[0].package_id}” 被 {len(group)} 个 mod 同时占用",
            detail=("多个 mod 使用同一个包 ID，它们的数据会生成完全相同的 LorId，"
                    "互相覆盖并可能在战斗结算时抛异常。"),
            evidence=[f"[{'工坊' if g.source == 'workshop' else '本地'}] "
                      f"{g.folder} — {g.title or '(无标题)'}" for g in group],
            mods=[g.uid for g in group], clash_relevant=True,
            advice="只保留一个，其余在创意工坊取消订阅或移出 Mods 目录。",
        ))

    # --- 3. 打包了游戏本体程序集 ------------------------------------------
    for m in active:
        game_libs = sorted(a for a in m.assemblies
                           if a in GAME_LIB_NAMES or a.startswith("unityengine."))
        if len(game_libs) < 5:
            continue
        findings.append(Finding(
            code="BUNDLED_GAME_LIBS", severity=SEV_MID,
            title=f"{m.display} 打包了 {len(game_libs)} 个游戏本体程序集",
            detail=("这个 mod 把游戏自身的 Managed 目录 DLL 一起打包了。"
                    "按程序集名加载时同名副本会被丢弃，多余副本既占空间，"
                    "也可能让某些类型解析到错误版本。"),
            evidence=[", ".join(game_libs[:25])], mods=[m.uid],
            advice="一般可以保留；若该作者同时提供“无本体库”的精简版，优先用精简版。",
        ))

    # --- 4. 重复程序集（框架级单例 / mod 自带功能程序集）-------------------
    static_dups = sm.assembly_duplicates(active)
    log_lower = {n.lower(): c for n, c in log.dup_assemblies.items()}
    log_mods_lower: Dict[str, List[str]] = {}
    for n, ids in log.dup_assembly_mods.items():
        log_mods_lower.setdefault(n.lower(), []).extend(ids)

    framework_dups: List[Tuple[str, int]] = []
    candidates = dict(static_dups)
    for low in log_lower:
        candidates.setdefault(low, [])
    for low in sorted(candidates):
        kind = sm.classify_assembly(low)
        providers = list(static_dups.get(low, []))
        for u in log_mods_lower.get(low, []):
            m = by_uid.get(u)
            if m and m not in providers:
                providers.append(m)
        if kind == "framework" or (low in GAME_LIB_NAMES or low.startswith("unityengine.")):
            framework_dups.append((low, len(providers)))
            continue
        if not providers:
            continue
        if len(providers) < 2 and not log_lower.get(low):
            continue
        sev = SEV_HIGH if (kind == "risky" or len(providers) <= 4) else SEV_MID
        kind_cn = {"risky": "框架级单例程序集（版本必须唯一，重复基本必冲突）",
                   "content": "mod 自带的功能程序集"}.get(kind, "程序集")
        ev = [f"[{'工坊' if m.source == 'workshop' else '本地'}] {m.folder} — "
              f"{m.title or m.package_id or '(无标题)'}" for m in providers[:20]]
        if log_lower.get(low):
            ev.insert(0, f"Player.log 中 “The same assembly name already exists” "
                         f"出现 {log_lower[low]} 次")
        findings.append(Finding(
            code="DUP_ASSEMBLY", severity=sev,
            title=f"程序集重复：[{low}] 被 {len(providers)} 个 mod 打包",
            detail=(f"{kind_cn}。Mono 按程序集名加载，同名副本只有第一份生效，"
                    "于是某个 mod 实际跑的是别人的版本，行为不可预测；"
                    "这类问题在拼点等复杂结算里最容易表现为卡死或效果错乱。"),
            evidence=ev, mods=[m.uid for m in providers],
            clash_relevant=kind == "risky" or any(k in low for k in ("dice", "card", "harmony")),
            advice="保留版本最新/最完整的那一份；其余 mod 在作者修复前不要同时启用。",
        ))

    if framework_dups:
        top = sorted(framework_dups, key=lambda kv: -kv[1])[:8]
        findings.append(Finding(
            code="DUP_SHARED_LIB", severity=SEV_INFO,
            title="通用库被大量 mod 重复打包（正常现象）",
            detail=("0Harmony / Mono.Cecil / MonoMod / NAudio 一类通用库几乎每个 mod 都会自带，"
                    "游戏对此有容错，通常不会单独导致崩溃，仅作背景信息。"),
            evidence=[f"{n} ×{c} 个 mod" for n, c in top],
            advice="",
        ))

    # --- 5. 日志：重复 LorId（已确认的崩溃点）-----------------------------
    for item in log.same_key:
        pkg = item["package_id"]
        owners = _mods_with_package(live, pkg)
        ev = [f"Player.log 第 {item['line_no']} 行：ArgumentException: An item with the "
              f"same key has already been added. Key: LorId({item['key']})"]
        if item.get("crash_upload"):
            ev.append("该异常出现在 “Uploading Crash Report” 之前 —— 属于已确认的崩溃点")
        detail = ("游戏注册数据时发现同一个 LorId 已存在，属于日志中确认的真实崩溃/中断。")
        if not owners:
            for e in log.exceptions:
                if abs(e.line_no - item["line_no"]) <= 120:
                    for m in attribute_exception(live, e, asm_index):
                        if m not in owners:
                            owners.append(m)
                    if owners:
                        ev.append(f"由异常堆栈归因：第 {e.line_no} 行 {e.type}")
                        break
        if owners:
            detail += " 归属 mod：" + "、".join(m.display for m in owners) + "。"
        else:
            detail += f" 未能定位到包 ID “{pkg}” 对应的 mod，请检查日志上下文。"
        findings.append(Finding(
            code="LOG_DUP_LORID", severity=SEV_FATAL,
            title=f"日志确认的重复 LorId：LorId({item['key']})",
            detail=detail, evidence=ev, mods=[m.uid for m in owners],
            clash_relevant=True,
            advice="先禁用上面归属的 mod（或取消订阅）再进接待测试。")
        )
        # 该包 ID 对应的 mod 若没有静态可见的内部重复 ID，说明是运行期重复注册
        if owners:
            m0 = owners[0]
            if not any(f.code == "DUP_ID_IN_MOD" and m0.uid in f.mods for f in findings):
                findings.append(Finding(
                    code="RUNTIME_DUP_LORID", severity=SEV_HIGH,
                    title=f"{m0.display} 在运行期重复注册了同一个 LorId",
                    detail=("静态数据里没有发现重复 ID，说明是该 mod 的代码在初始化时"
                            "把同一份数据注册了两遍（常见于多语言文件缺失后回退重读、"
                            "或 mod 与本体数据重复加载）。"),
                    evidence=[f"涉及 LorId({item['key']})；异常位置见堆栈中的 "
                              f"{item.get('package_id')} 命名空间方法"],
                    mods=[m0.uid], clash_relevant=True,
                    advice="更新该 mod 到最新版；若无效只能禁用或改用其他替代 mod。"))

    # --- 5b. 日志：mod 初始化器卡死不返回（LoA DataLoader）----------------
    if log.initializer_stall and (log.stall_owner_type or log.stall_owner_dll):
        owners = [by_uid[u] for u in log.stall_owner_folders if u in by_uid]
        if not owners and log.stall_owner_dll:
            owners = list(asm_index.get(Path(log.stall_owner_dll).stem.lower(), []))
        ev = [f"第 {log.stall_owner_line} 行：LoA :: Call Mod Before :"
              f"{log.stall_owner_type}  ← 此后 DataLoader 一直在等它返回",
              f"初始化器所属程序集：{log.stall_owner_dll}"]
        if log.stall_seconds:
            ev.append(f"游戏在这个初始化器上卡了 {log.stall_seconds:.0f} 秒，"
                      "加载进度停在同一个百分比上不再变化")
        if log.repeat_wait_max:
            ev.append(f"“Call Initializer Not Completed, Repeat Wait” 累计 "
                      f"{log.repeat_wait_max} 次")
        findings.append(Finding(
            code="LOG_INIT_STALL", severity=SEV_FATAL,
            title=f"mod 初始化器卡死不返回：{log.stall_owner_type}",
            detail=("LoA 的 DataLoader 在等待这个 mod 的初始化方法返回，游戏进度停在同一处"
                    "不动，随后才抛出异常。这是“卡死”最直接、最可操作的证据。"),
            evidence=ev, mods=[m.uid for m in owners], clash_relevant=True,
            advice="更新该 mod；若无效，先整体禁用该 mod 再进接待验证是否恢复。"))

    # --- 5c. 日志：mod 初始化很慢（最终返回了，但看起来像卡死）-------------
    if log.slow_owner_type and log.slow_init_seconds >= MIN_SLOW_SECONDS:
        owners = list(asm_index.get(Path(log.slow_owner_dll).stem.lower(), []))
        ev = [f"第 {log.slow_owner_line} 行：LoA :: Call Mod Before :"
              f"{log.slow_owner_type}",
              f"初始化器所属程序集：{log.slow_owner_dll}",
              f"耗时约 {log.slow_init_seconds:.0f} 秒后**最终完成**（不是死锁）"]
        if log.progress_stall:
            ev.append(log.progress_stall)
        findings.append(Finding(
            code="LOG_SLOW_INIT", severity=SEV_MID,
            title=f"mod 初始化很慢：{log.slow_owner_type}（{log.slow_init_seconds:.0f} 秒）",
            detail=("这个 mod 的初始化拖了十几秒以上，加载进度会长时间停在同一个百分比，"
                    "观感上非常像卡死。它最终返回了，所以不是本次死锁的元凶，"
                    "但如果你的\"卡死\"发生在读盘阶段，它就是原因。"),
            evidence=ev, mods=[m.uid for m in owners], clash_relevant=False,
            advice="可先禁用它看加载是否变快；若启动时间对你无所谓可以保留。"))

    # --- 6. 日志：关键字重复注册 ------------------------------------------    if log.already_exists:
        keys = sorted(log.already_exists)
        findings.append(Finding(
            code="LOG_KEYWORD_DUP", severity=SEV_MID,
            title=f"{len(keys)} 个战斗关键字被重复注册（AutoKeywordUtil/KeywordUtil 冲突）",
            detail=("多个 mod 注册了同名自定义关键字，后注册的会被丢弃，对应书页效果"
                    "静默失效或产生未定义行为；若关键字参与拼点结算，也可能直接卡死。"),
            evidence=[f"{k}（出现 {log.already_exists[k]} 次）" for k in keys[:60]],
            clash_relevant=True,
            advice="找出同时提供这些关键字的 mod，只保留一个。"))

    # --- 7. 拼点相关类型交叉引用（启发式：补丁重叠）------------------------
    type_to_mods: Dict[str, List[sm.ModInfo]] = {}
    for m in active:
        for t in m.clash_types:
            type_to_mods.setdefault(t, []).append(m)
    rare, common = [], []
    for t, ms in type_to_mods.items():
        (rare if len(ms) <= 10 else common).append((t, ms))
    for t, ms in sorted(rare, key=lambda kv: -len(kv[1])):
        findings.append(Finding(
            code="CLASH_PATCH_OVERLAP",
            severity=SEV_HIGH if len(ms) <= 5 else SEV_MID,
            title=f"{len(ms)} 个 mod 都改动了游戏类型 {t}（拼点相关）",
            detail=("这些 mod 的 DLL 都引用了同一个拼点/战斗核心类型，极可能都给同一个方法"
                    "打了 Harmony 补丁。补丁互相覆盖或形成递归，是“开始拼点即卡死”"
                    "最典型的成因。（本项为启发式推断，用于缩小排查范围）"),
            evidence=[f"{m.folder} — {m.title or m.package_id}" for m in ms[:20]],
            mods=[m.uid for m in ms], clash_relevant=True,
            advice="把这些 mod 两两分开测试（二分法），优先保留功能性修复 mod。"))
    if common:
        top = sorted(common, key=lambda kv: -len(kv[1]))[:6]
        findings.append(Finding(
            code="CLASH_PATCH_COMMON", severity=SEV_INFO,
            title="拼点相关类型被大量 mod 引用（概况）",
            detail="以下类型被很多 mod 引用，属于普遍现象，仅作背景参考。",
            evidence=[f"{t}：{len(ms)} 个 mod" for t, ms in top],
            clash_relevant=True))

    # --- 8. 缺失资源 ------------------------------------------------------
    if log.missing_paths:
        findings.append(Finding(
            code="MISSING_RESOURCE", severity=SEV_HIGH,
            title=f"日志报告 {len(log.missing_paths)} 个资源文件缺失",
            detail="mod 引用的贴图/音频不存在。轻则贴图空白，重则在战斗中抛异常中断结算。",
            evidence=log.missing_paths[:15],
            mods=sorted({f for p in log.missing_paths
                         for f in re.findall(r"1256670[\\/]([^\\/]+)", p)}),
            advice="重新订阅该 mod，或验证游戏文件完整性（Steam → 属性 → 已安装文件）。"))

    # --- 8b. 找不到卡组 ---------------------------------------------------
    if log.deck_not_found:
        findings.append(Finding(
            code="LOG_DECK_NOT_FOUND", severity=SEV_HIGH,
            title=f"战斗中出现 {len(log.deck_not_found)} 个找不到的卡组（deck not found）",
            detail=("某个单位引用了不存在的卡组 ID。战斗开始或分配书页时如果拿到空卡组，"
                    "结算流程可能在循环里空转——与“开始拼点即卡死”的表现吻合。"),
            evidence=[f"deck not found : {d}" for d in log.deck_not_found[:10]],
            clash_relevant=True,
            advice="确认该卡组 ID 由哪个 mod 提供（通常是当前接待的敌人配置），"
                   "更新或禁用对应 mod。"))

    # --- 9. 其它异常 ------------------------------------------------------
    seen_e: Dict[str, Finding] = {}
    for e in log.exceptions:
        if "ArgumentException" in e.type and "same key" in e.message:
            continue
        key = f"{e.type}|{e.message[:120]}"
        owners = attribute_exception(live, e, asm_index)
        sev = SEV_MID
        for name, v in EXC_SEVERITY:
            if name in e.type:
                sev = v
                break
        f = seen_e.get(key)
        if f is None:
            blob = e.type + " " + e.message
            f = Finding(
                code="LOG_EXCEPTION", severity=sev,
                title=f"{e.type}: {e.message[:110]}",
                detail="Player.log 中捕获到的异常。",
                evidence=[f"第 {e.line_no} 行"] + e.stack[:6],
                mods=[m.uid for m in owners],
                clash_relevant=bool(re.search(r"(?i)clash|dice|battle|card", blob)),
                advice="若该异常与拼点同时发生，它就是首要嫌疑。")
            if owners:
                f.evidence.append("归因 mod：" + "、".join(m.display for m in owners))
            seen_e[key] = f
            findings.append(f)
        else:
            f.evidence.append(f"第 {e.line_no} 行（同类异常再次出现）")

    # --- 10. 卡死特征 -----------------------------------------------------
    if log.initializer_stall or log.tail_repeat[1] >= 8:
        ev: List[str] = []
        if log.repeat_wait_max:
            ev.append(f"日志出现 “Call Initializer Not Completed, Repeat Wait : "
                      f"{log.repeat_wait_max}” —— mod 初始化卡住不返回")
        if log.progress_stall:
            ev.append(log.progress_stall)
        if log.tail_repeat[1] >= 8:
            ev.append(f"日志结尾连续重复同一行 {log.tail_repeat[1]} 次："
                      f"{log.tail_repeat[0][:120]!r} —— 典型的卡死循环特征")
        if log.tail_is_battle:
            ev.append("而日志最后停留的位置是战斗特效/语音，"
                      "说明卡死发生在战斗（拼点）过程中，而不是菜单里")
        elif log.tail_battle_markers >= 3:
            ev.append(f"日志结尾有 {log.tail_battle_markers} 行都是战斗特效/语音标记，"
                      "说明卡死发生在战斗（拼点）过程中")
        if log.crash_report_marker:
            ev.append("日志中有 “Uploading Crash Report” 标记")
        findings.append(Finding(
            code="LOG_HANG", severity=SEV_FATAL,
            title="日志中存在卡死/挂起特征",
            detail=("游戏在原地重复等待，说明某个 mod 的初始化或战斗循环没有返回，"
                    "与你描述的“开始拼点时卡死”一致。"),
            evidence=ev, clash_relevant=True,
            advice="结合上面的嫌疑排行做二分排查；也可把日志最后 20 行反馈给 mod 作者。"))

    # --- 11. 缺清单 / 空 mod ----------------------------------------------
    for m in live:
        if not m.manifest:
            findings.append(Finding(
                code="NO_MANIFEST", severity=SEV_LOW,
                title=f"{m.folder} 没有 mod 清单文件",
                detail="该目录不含 StageModInfo.xml / ModInfo.Xml，游戏可能不会加载它；"
                       "若它是某个框架的设置目录则可忽略。",
                evidence=[str(m.path)], mods=[m.uid],
                advice="如果是误放的内容目录，可以删除。"))

    # --- 12. 框架顺序提示 -------------------------------------------------
    fw = [m for m in active if m.is_framework]
    if fw:
        findings.append(Finding(
            code="FRAMEWORK_ORDER", severity=SEV_INFO,
            title=f"检测到 {len(fw)} 个框架/加载器",
            detail="LoA / 1FrameworkLoader 一类框架要求优先加载；顺序不对时需要在进入主菜单后重启一次游戏。",
            evidence=[f"{m.folder} — {m.title or m.package_id}" for m in fw],
            mods=[m.uid for m in fw],
            advice="把加载器 mod 排在最前面，改完顺序后重启游戏。"))

    # --- 13. 已禁用 mod 说明 ----------------------------------------------
    off = [m for m in live if m.enabled is False]
    if off:
        findings.append(Finding(
            code="DISABLED_MODS", severity=SEV_INFO,
            title=f"{len(off)} 个已安装 mod 当前处于禁用状态（未参与冲突判定）",
            detail=("这些 mod 已安装但未启用，它们之间的重复与冲突不会影响游戏，"
                    "因此已从跨 mod 冲突判定中排除。"),
            evidence=[f"{m.title or m.package_id}（{m.folder}）" for m in off[:25]],
            mods=[m.uid for m in off],
            advice="如果以后重新启用它们，请重新运行本检测器。"))

    findings.sort(key=lambda f: (-f.severity, f.code, f.title))
    return findings


# ---------------------------------------------------------------------------
# 嫌疑度评分
# ---------------------------------------------------------------------------
REASON_CAP = 7


def build_suspects(mods: Sequence[sm.ModInfo], log: LogReport,
                   findings: Sequence[Finding]) -> List[Suspect]:
    live = [m for m in mods if not m.is_sample]
    known_state = any(m.enabled is not None for m in live)
    pool = [m for m in live if m.enabled is not False] if known_state else live
    score: Dict[str, int] = {m.uid: 0 for m in pool}
    reasons: Dict[str, List[str]] = {m.uid: [] for m in pool}
    by_uid = {m.uid: m for m in pool}

    def bump(m: sm.ModInfo, pts: int, why: str):
        score[m.uid] = score.get(m.uid, 0) + pts
        r = reasons.setdefault(m.uid, [])
        if why not in r and len(r) < REASON_CAP:
            r.append(why)

    asm_count: Dict[str, int] = {}
    for f in findings:
        for uid in f.mods:
            m = by_uid.get(uid)
            if not m:
                continue
            if f.code == "LOG_DUP_LORID":
                bump(m, 60, "日志确认的重复 LorId（已确认的崩溃点）")
            elif f.code == "RUNTIME_DUP_LORID":
                bump(m, 30, "运行期重复注册 LorId")
            elif f.code == "LOG_HANG":
                bump(m, 8, "日志出现卡死特征")
            elif f.code == "LOG_EXCEPTION":
                bump(m, 22 if f.clash_relevant else 13, f"日志异常：{f.title[:70]}")
            elif f.code in ("DUP_MOD_INSTALL", "DUP_PACKAGE_ID"):
                bump(m, 45, "包 ID 冲突")
            elif f.code == "DUP_ASSEMBLY":
                n = asm_count.get(uid, 0)
                asm_count[uid] = n + 1
                pts = 16 if n == 0 else (5 if n < 3 else 2)
                bump(m, pts, f"重复程序集：{f.title.split('：', 1)[-1][:60]}")
            elif f.code == "CLASH_PATCH_OVERLAP":
                bump(m, 20 if f.severity >= SEV_HIGH else 10,
                     f"拼点类型重叠：{f.title[:60]}")
            elif f.code == "DUP_ID_IN_MOD":
                bump(m, 15, f"内部重复 ID：{f.title[:70]}")
            elif f.code == "BUNDLED_GAME_LIBS":
                bump(m, 5, "打包了游戏本体程序集")
            elif f.code == "MISSING_RESOURCE":
                bump(m, 16, "缺失资源文件")

    for m in pool:
        if m.is_framework:
            bump(m, -60, "框架/加载器本身不是冲突源")
        n_clash = len(m.clash_types)
        if n_clash >= 10:
            bump(m, 18, f"改动了 {n_clash} 个拼点/战斗核心类型")
        elif n_clash >= 6:
            bump(m, 9, f"改动了 {n_clash} 个战斗类型")
        blob = f"{m.title} {m.package_id} {m.folder}".lower()
        if any(k in blob for k in CLASH_KEYWORDS):
            bump(m, 12, "名称/描述与拼点、骰子直接相关")
        risky = sorted(a for a in m.assemblies if a in RISKY_ASSEMBLIES)
        if risky:
            bump(m, 7, "打包了框架级单例程序集：" + ", ".join(risky[:4]))
        if m.file_count > 2000:
            bump(m, 6, f"体量很大（{m.file_count} 个文件），改动面广")

    out = [Suspect(mod=by_uid[u], score=score[u], reasons=reasons.get(u, []))
           for u in score]
    out.sort(key=lambda s: (-s.score, s.mod.folder))
    return out


def summarize(findings: Sequence[Finding]) -> Dict[int, int]:
    out: Dict[int, int] = {}
    for f in findings:
        out[f.severity] = out.get(f.severity, 0) + 1
    return out
