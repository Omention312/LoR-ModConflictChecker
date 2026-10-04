# -*- coding: utf-8 -*-
"""二分排查（bisect）引擎 —— 把"哪个 mod 导致卡死"变成一串可执行的开关实验。

算法：包含式查找 + 逐个确认
---------------------------
核心判定只有一条，而且对**任意数量**的元凶都成立：

    禁用集合 D 之后症状消失  ⟺  全部元凶都包含在 D 里

记元凶集合为 K（可能不止一个），于是：
* 禁用 D 后**消失**  ⇒  K ⊆ D
* 禁用 D 后**仍在**  ⇒  K ⊄ D（至少有一个元凶在 D 之外）

只用到这两条，不需要假定"只有一个元凶"，因此结果是精确的。

流程
----
1. ``find``：在候选集合 R 里找**一个**元凶（不知道是哪个，只求找到一个）。
   把 R 对半切成 X / Y，测试"禁用 anchor ∪ X"：
     * 消失  ⇒ K∩R ⊆ X ⇒ 元凶在 X 里，R := X
     * 仍在  ⇒ K∩R ⊄ X ⇒ 元凶也在 Y 里，anchor ∪= X，R := Y
   每一步 R 减半，``log2`` 轮后 R 只剩 1 个 —— 那就是一个确定的元凶。
2. ``verify``：禁用已找到的全部元凶。
     * 消失  ⇒ 已找到的集合 ⊆ K 且 ⊇ K ⇒ **恰好等于 K**，结束。
     * 仍在  ⇒ 还有元凶没找到，回到 ``find`` 继续（anchor 从已找到的集合开始）。

为什么不用纯二分
----------------
纯二分在"仍在"时推断"元凶在另一半"，这只在**只有一个元凶**时成立；一旦问题需要两个
mod 同时存在，这个推断会把真正的元凶丢掉，最后给出错误答案。上面的写法把"仍在"只
解读为"元凶没全在这一半"，因此对组合冲突同样正确。

为什么不用 ddmin
----------------
ddmin 同样正确，但实测在 54 个候选、2 个元凶时需要 30~80 轮，人工点不完。本算法在
同样场景下约 14 轮，单点约 7 轮。

每轮的约定
----------
**除"本轮禁用清单"以外的所有 mod 都保持启用。**

会话可保存为 JSON，关掉工具再回来能接着做。
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

PHASE_CN = {
    "baseline": "基线测试",
    "find": "查找元凶",
    "verify": "确认元凶",
    "done": "已结束",
}

RESULT_CN = {
    "": "待测",
    "fixed": "已恢复正常",
    "persists": "仍然卡死",
    "unsure": "不确定",
}

ROLE_CN = {
    "baseline": "基线",
    "find": "查找",
    "verify": "确认",
}

SESSION_VERSION = 3


def _split_half(seq: Sequence[str]) -> tuple:
    """把 seq 尽量均匀地切成两半。"""
    n = len(seq)
    half = (n + 1) // 2
    return list(seq[:half]), list(seq[half:])


@dataclass
class Round:
    n: int = 0
    phase: str = "find"
    action: str = "find"          # baseline | find | verify
    disable: List[str] = field(default_factory=list)   # 本轮要禁用的 uid
    tested: List[str] = field(default_factory=list)    # 本轮试探的 X
    label: str = ""
    reception: str = ""           # 本轮固定的测试接待
    stage: str = "clash"          # 本轮固定的症状阶段
    result: str = ""
    ts: str = ""
    pool_before: int = 0
    pool_after: int = 0
    snapshot: dict = field(default_factory=dict)

    @property
    def result_cn(self) -> str:
        return RESULT_CN.get(self.result, self.result)

    @property
    def role_cn(self) -> str:
        return ROLE_CN.get(self.action, self.action)


@dataclass
class Session:
    created: str = ""
    updated: str = ""
    scope: str = "enabled"
    use_baseline: bool = True
    symptom_reception: str = ""     # 固定测试的接待 / 关卡
    symptom_stage: str = "clash"    # 固定观察的症状阶段
    phase: str = "baseline"        # baseline | find | verify | done
    all_candidates: List[str] = field(default_factory=list)
    found: List[str] = field(default_factory=list)     # 已确认的元凶
    find_R: List[str] = field(default_factory=list)    # 当前查找区域
    find_S: List[str] = field(default_factory=list)    # 查找时固定禁用的锚点
    verdict: List[str] = field(default_factory=list)
    note: str = ""
    rounds: List[Round] = field(default_factory=list)
    pending: Optional[Round] = None

    # -- 序列化 ------------------------------------------------------------
    def to_dict(self) -> dict:
        d = asdict(self)
        d["version"] = SESSION_VERSION
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Session":
        s = cls()
        s.created = d.get("created", "")
        s.updated = d.get("updated", "")
        s.scope = d.get("scope", "enabled")
        s.use_baseline = bool(d.get("use_baseline", True))
        s.symptom_reception = d.get("symptom_reception", "")
        s.symptom_stage = d.get("symptom_stage", "clash")
        s.phase = d.get("phase", "find")
        s.all_candidates = list(d.get("all_candidates", []))
        s.found = list(d.get("found", []))
        s.find_R = list(d.get("find_R", []))
        s.find_S = list(d.get("find_S", []))
        s.verdict = list(d.get("verdict", []))
        s.note = d.get("note", "")
        for r in d.get("rounds", []):
            s.rounds.append(Round(
                n=r.get("n", 0), phase=r.get("phase", "find"),
                action=r.get("action", "find"),
                disable=list(r.get("disable", [])), tested=list(r.get("tested", [])),
                label=r.get("label", ""), result=r.get("result", ""),
                ts=r.get("ts", ""), pool_before=r.get("pool_before", 0),
                pool_after=r.get("pool_after", 0),
                reception=r.get("reception", ""), stage=r.get("stage", "clash"),
                snapshot=r.get("snapshot", {}) or {}))
        p = d.get("pending")
        if p:
            s.pending = Round(
                n=p.get("n", 0), phase=p.get("phase", "find"),
                action=p.get("action", "find"),
                disable=list(p.get("disable", [])),
                tested=list(p.get("tested", [])),
                label=p.get("label", ""),
                result=p.get("result", ""),
                ts=p.get("ts", ""),
                pool_before=p.get("pool_before", 0),
                pool_after=p.get("pool_after", 0),
                reception=p.get("reception", ""), stage=p.get("stage", "clash"),
                snapshot=p.get("snapshot", {}) or {})
        return s

    # -- 便捷属性 ----------------------------------------------------------
    @property
    def is_done(self) -> bool:
        return self.phase == "done"

    @property
    def suspect_count(self) -> int:
        return len(self.find_R)

    @property
    def tested_rounds(self) -> int:
        return sum(1 for r in self.rounds if r.result and r.result != "unsure")

    def estimate_remaining(self) -> int:
        if self.is_done:
            return 0
        if self.phase == "baseline":
            return 1 + max(1, (len(self.all_candidates) - 1).bit_length()) + 1
        if not self.find_R:
            return 1
        n = len(self.find_R)
        per = max(1, (n - 1).bit_length()) if n > 1 else 0
        return per + 1          # 本轮查找 + 一次确认


# ---------------------------------------------------------------------------
# 会话
# ---------------------------------------------------------------------------
def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def new_session(candidates: Sequence[str], scope: str = "enabled",
                use_baseline: bool = True, symptom_reception: str = "",
                symptom_stage: str = "clash") -> Session:
    s = Session(created=_now(), updated=_now(), scope=scope,
                use_baseline=use_baseline,
                symptom_reception=symptom_reception,
                symptom_stage=symptom_stage,
                all_candidates=list(candidates),
                find_R=list(candidates), find_S=[])
    s.phase = "baseline" if use_baseline else "find"
    s.pending = plan(s)
    return s


def _snapshot(s: Session) -> dict:
    return {"phase": s.phase, "found": list(s.found), "find_R": list(s.find_R),
            "find_S": list(s.find_S), "verdict": list(s.verdict), "note": s.note}


def _restore(s: Session, snap: dict) -> None:
    s.phase = snap.get("phase", "find")
    s.found = list(snap.get("found", []))
    s.find_R = list(snap.get("find_R", []))
    s.find_S = list(snap.get("find_S", []))
    s.verdict = list(snap.get("verdict", []))
    s.note = snap.get("note", "")


def _finish(s: Session, verdict: Sequence[str], note: str) -> None:
    s.phase = "done"
    s.verdict = list(verdict)
    s.note = note
    s.pending = None


# ---------------------------------------------------------------------------
# 计划下一轮
# ---------------------------------------------------------------------------
def plan(s: Session) -> Optional[Round]:
    """给出下一轮实验；自动带上会话固定的"测试接待 / 症状阶段"。"""
    r = _plan_inner(s)
    if r is not None:
        r.reception = s.symptom_reception
        r.stage = s.symptom_stage
    return r


def _plan_inner(s: Session) -> Optional[Round]:
    if s.phase == "done":
        return None
    n_round = len(s.rounds) + 1

    if s.phase == "baseline":
        return Round(n=n_round, phase="baseline", action="baseline",
                     disable=list(s.all_candidates),
                     tested=list(s.all_candidates),
                     label="基线测试：禁用**全部**候选 mod，确认问题确实由 mod 引起",
                     pool_before=len(s.find_R))

    if s.phase == "verify":
        return Round(n=n_round, phase="verify", action="verify",
                     disable=list(s.found), tested=list(s.found),
                     label=f"确认：只禁用这 {len(s.found)} 个已找到的 mod，其余全部启用",
                     pool_before=len(s.find_R))

    # find
    R = list(s.find_R) or list(s.all_candidates)
    if len(R) <= 1:
        # 只剩一个候选：直接判定，无需测试（由 apply 处理）
        return None
    X, Y = _split_half(R)
    return Round(n=n_round, phase="find", action="find",
                 disable=list(s.find_S) + X, tested=X,
                 label=(f"查找元凶：在 {len(R)} 个候选里禁用前 {len(X)} 个"
                       + (f"（另外固定禁用 {len(s.find_S)} 个）" if s.find_S else "")),
                 pool_before=len(R))


# ---------------------------------------------------------------------------
# 应用结果
# ---------------------------------------------------------------------------
def apply_result(s: Session, result: str) -> None:
    if s.pending is None:
        s.pending = plan(s)
        if s.pending is None:
            _conclude_no_test(s)
            return
    r = s.pending
    r.result = result
    r.ts = _now()
    r.snapshot = _snapshot(s)

    if result == "unsure":
        s.rounds.append(r)
        s.pending = Round(n=r.n + 1, phase=r.phase, action=r.action,
                          disable=list(r.disable), tested=list(r.tested),
                          label="重测：上一轮结果不确定，请再测一次同一组",
                          reception=r.reception, stage=r.stage,
                          pool_before=r.pool_before)
        s.updated = _now()
        return

    fixed = (result == "fixed")
    action = r.action

    # ---- 基线 ----
    if action == "baseline":
        r.pool_after = 0 if fixed else len(s.all_candidates)
        s.rounds.append(r)
        if not fixed:
            _finish(s, [], "禁用全部候选 mod 后问题依然存在 —— 说明卡死不是这些 mod "
                           "造成的。请扩大排查范围（把全部已启用 mod 纳入），"
                           "或先验证游戏本体文件完整性。")
        else:
            s.phase = "find"
            s.find_R = list(s.all_candidates)
            s.find_S = []
            s.found = []
            s.note = "基线确认：问题确实由这些 mod 引起，开始查找元凶。"
            s.pending = plan(s)
            if s.pending is None:
                _conclude_no_test(s)
        s.updated = _now()
        return

    # ---- 确认 ----
    if action == "verify":
        s.rounds.append(r)
        if fixed:
            n = len(s.found)
            _finish(s, s.found,
                    f"已确认：以上 {n} 个 mod 就是元凶。"
                    "去掉其中任何一个，问题都会回来（该集合是最小的）。")
        else:
            rest = [c for c in s.all_candidates if c not in set(s.found)]
            if not rest:
                _finish(s, [], "已确认所有候选 mod 都禁用后问题仍在 —— "
                               "结论矛盾，说明元凶不在当前排查范围内。")
            else:
                s.note = (f"已找到 {len(s.found)} 个元凶，但问题仍在 —— "
                          "还有别的 mod 参与，继续查找下一个。")
                s.phase = "find"
                s.find_R = rest
                s.find_S = list(s.found)
                s.pending = plan(s)
                if s.pending is None:
                    _conclude_no_test(s)
        s.updated = _now()
        return

    # ---- 查找 ----
    s.rounds.append(r)
    X = list(r.tested)
    if fixed:
        s.find_R = X
    else:
        s.find_S = list(s.find_S) + [x for x in X if x not in set(s.find_S)]
        s.find_R = [x for x in s.find_R if x not in set(X)]
    r.pool_after = len(s.find_R)

    if len(s.find_R) <= 1:
        if s.find_R:
            s.found = list(s.found) + [s.find_R[0]]
        s.phase = "verify"
        s.pending = plan(s)
    else:
        s.pending = plan(s)
    s.updated = _now()


def _conclude_no_test(s: Session) -> None:
    """当 plan 返回 None 但不是 done 时的收尾（例如候选只剩一个）。"""
    if s.phase == "find":
        if s.find_R:
            s.found = list(s.found) + [s.find_R[0]]
        s.phase = "verify"
        s.pending = plan(s)
        if s.pending is not None:
            return
    if s.phase == "verify":
        _finish(s, s.found, "已确认：定位完成。")
    elif s.phase != "done":
        _finish(s, [], "候选集合为空，无法继续排查。")


def undo_last(s: Session) -> bool:
    if not s.rounds:
        return False
    last = s.rounds.pop()
    if last.snapshot:
        _restore(s, last.snapshot)
        s.pending = plan(s)
        if s.pending is None and not s.is_done:
            _conclude_no_test(s)
    else:
        reset(s)
    s.updated = _now()
    return True


def reset(s: Session) -> None:
    s.found = []
    s.find_R = list(s.all_candidates)
    s.find_S = []
    s.verdict = []
    s.note = ""
    s.rounds = []
    s.phase = "baseline" if s.use_baseline else "find"
    s.pending = plan(s)
    s.updated = _now()


# ---------------------------------------------------------------------------
# 持久化
# ---------------------------------------------------------------------------
def save(s: Session, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    s.updated = _now()
    path.write_text(json.dumps(s.to_dict(), ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return path


def load(path: Path) -> Optional[Session]:
    path = Path(path)
    if not path.is_file():
        return None
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return Session.from_dict(d) if isinstance(d, dict) else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 展示
# ---------------------------------------------------------------------------
def describe(mods_by_uid: Dict[str, object], uids: Sequence[str]) -> List[str]:
    out: List[str] = []
    for u in uids:
        m = mods_by_uid.get(u)
        if m is None:
            out.append(u)
            continue
        pkg = getattr(m, "package_id", "") or ""
        title = getattr(m, "title", "") or ""
        src = "工坊" if getattr(m, "source", "") == "workshop" else "本地"
        folder = getattr(m, "folder", "") or u
        out.append(f"{title or pkg}｜包 ID {pkg or '—'}｜{src} {folder}")
    return out


def progress_text(s: Session) -> str:
    if s.is_done:
        if s.verdict:
            return f"已结束：定位到 {len(s.verdict)} 个 mod"
        return "已结束：未定位到 mod"
    return (f"{PHASE_CN.get(s.phase, s.phase)}｜已找到 {len(s.found)} 个｜"
            f"待查 {len(s.find_R)} 个｜预估还需 {s.estimate_remaining()} 轮")


def symptom_text(s: Session) -> str:
    """把固定的"测试接待 + 症状阶段"说成人话。"""
    from .journal import STAGE_CN
    stage = STAGE_CN.get(s.symptom_stage, s.symptom_stage or "开始拼点")
    if s.symptom_reception.strip():
        return f"在「{s.symptom_reception.strip()}」这个接待里，做到「{stage}」这一步"
    return f"任意接待，做到「{stage}」这一步"


def render_markdown(s: Session, mods_by_uid: Dict[str, object]) -> str:
    L: List[str] = []
    L.append("# 二分排查记录")
    L.append("")
    L.append(f"- 会话创建：{s.created}")
    L.append(f"- 最后更新：{s.updated}")
    L.append(f"- 初始候选：{len(s.all_candidates)} 个 mod")
    L.append(f"- 当前阶段：{PHASE_CN.get(s.phase, s.phase)}")
    L.append(f"- 已执行测试：{s.tested_rounds} 轮")
    L.append(f"- **固定测试条件**：{symptom_text(s)}")
    L.append("")
    L.append("## 结论")
    L.append("")
    if s.verdict:
        L.append("**导致问题的 mod（已通过开关实验确认）：**")
        L.append("")
        for x in describe(mods_by_uid, s.verdict):
            L.append(f"- {x}")
        L.append("")
        L.append("请保持这些 mod 处于禁用状态，或等作者修复后再启用。")
    elif s.is_done:
        L.append("未能定位到具体 mod。")
    else:
        L.append(f"尚未定位完成。已确认 {len(s.found)} 个元凶，"
                 f"仍待查 {len(s.find_R)} 个：")
        L.append("")
        for x in describe(mods_by_uid, s.find_R[:40]):
            L.append(f"- {x}")
    if s.note:
        L.append("")
        L.append(f"> {s.note}")
    L.append("")
    L.append("## 实验记录")
    L.append("")
    L.append("| 轮次 | 阶段 | 禁用 mod 数 | 测试接待 | 症状阶段 | 结果 | 剩余待查 | 时间 |")
    L.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    from .journal import STAGE_CN
    for r in s.rounds:
        L.append(f"| {r.n} | {r.role_cn} | {len(r.disable)} | {r.reception or '—'} | "
                 f"{STAGE_CN.get(r.stage, r.stage)} | {r.result_cn} | "
                 f"{r.pool_after} | {r.ts} |")
    L.append("")
    L.append("## 每轮禁用清单")
    L.append("")
    for r in s.rounds:
        L.append(f"### 第 {r.n} 轮 · {r.role_cn}（{r.result_cn}）")
        L.append("")
        L.append(r.label)
        L.append("")
        if r.reception:
            L.append(f"测试条件：在「{r.reception}」接待里做到"
                     f"「{STAGE_CN.get(r.stage, r.stage)}」这一步。")
            L.append("")
        for x in describe(mods_by_uid, r.disable):
            L.append(f"- {x}")
        L.append("")
    if s.pending is not None:
        L.append(f"## 待执行的下一轮（第 {s.pending.n} 轮 · {s.pending.role_cn}）")
        L.append("")
        L.append(s.pending.label)
        L.append("")
        for x in describe(mods_by_uid, s.pending.disable):
            L.append(f"- {x}")
        L.append("")
    return "\n".join(L)


def pending_plain_list(s: Session, mods_by_uid: Dict[str, object]) -> str:
    if s.pending is None:
        return ""
    out = [f"第 {s.pending.n} 轮 · {s.pending.role_cn}", s.pending.label, "",
           f"测试条件：{symptom_text(s)}", "",
           "【请禁用以下 mod】"]
    for u in s.pending.disable:
        m = mods_by_uid.get(u)
        pkg = getattr(m, "package_id", "") if m else u
        title = ((getattr(m, "title", "") if m else "") or pkg)
        out.append(f"  - {title}    [包 ID: {pkg}]")
    out += ["", "【其余所有 mod 请保持启用】", "",
            f"改完后，{symptom_text(s)}，回工具点击结果。"]
    return "\n".join(out)
