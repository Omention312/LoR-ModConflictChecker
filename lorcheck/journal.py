# -*- coding: utf-8 -*-
"""接待 / 关卡症状档案。

解决的问题
----------
"卡死"其实有好几种完全不同的东西：

* **启动 / 读盘**阶段长时间不动 —— 通常是某个 mod 初始化慢（例如 30 秒），
  最终能进游戏，和 mod 冲突导致的死锁是两回事；
* **进入接待**时卡住 —— 多半是 mod 的数据注册 / 资源缺失；
* **开始拼点**时卡死 —— 拼点结算链路被多个 mod 同时改写，最典型。

如果不把"在哪个接待、卡在哪一步"记下来，二分排查的每一轮结果就不可比：
这一轮测接待 A、下一轮测接待 B，收敛出来的结论没有意义。

本模块提供一个轻量的记录表：每条记录 = 一次"在某个接待里做到某一步"的观察。
二分排查每轮的结果会自动写进来，也可以在界面上手工补记。

同时提供汇总与**一致性检查**：同一个接待、同一步骤、同样的禁用集合却出现
两种结果，说明症状本身不稳定 —— 这种情况下二分排查的结论不可信，必须重测。
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

VERSION = 1

STAGES: List[Tuple[str, str]] = [
    ("startup", "启动 / 读盘"),
    ("enter", "进入接待"),
    ("clash", "开始拼点"),
    ("mid", "战斗中途"),
    ("other", "其他"),
]
STAGE_CN = dict(STAGES)

OUTCOMES: List[Tuple[str, str]] = [
    ("frozen", "卡死"),
    ("ok", "正常"),
    ("unclear", "不确定"),
]
OUTCOME_CN = dict(OUTCOMES)

SOURCE_CN = {"manual": "手工", "bisect": "二分排查"}


@dataclass
class Observation:
    ts: str = ""
    reception: str = ""
    stage: str = "clash"
    outcome: str = "frozen"
    disabled: List[str] = field(default_factory=list)
    note: str = ""
    source: str = "manual"      # manual | bisect
    round_n: int = 0

    @property
    def stage_cn(self) -> str:
        return STAGE_CN.get(self.stage, self.stage)

    @property
    def outcome_cn(self) -> str:
        return OUTCOME_CN.get(self.outcome, self.outcome)

    @property
    def source_cn(self) -> str:
        return SOURCE_CN.get(self.source, self.source)

    @property
    def key(self) -> str:
        return self.reception.strip() or "(未填)"

    @property
    def signature(self) -> Tuple[str, str, Tuple[str, ...]]:
        """用于一致性检查：同一接待 + 同一步骤 + 同一组禁用 mod。"""
        return (self.key, self.stage, tuple(sorted(self.disabled)))


@dataclass
class Summary:
    total: int = 0
    by_reception: Dict[str, Dict[str, int]] = field(default_factory=dict)
    by_stage: Dict[str, Dict[str, int]] = field(default_factory=dict)
    by_outcome: Dict[str, int] = field(default_factory=dict)
    unstable: List[Tuple[str, str, int, List[str]]] = field(default_factory=list)
    reception_specific: bool = False
    frozen_receptions: List[str] = field(default_factory=list)
    ok_receptions: List[str] = field(default_factory=list)
    stage_hint: str = ""


def _blank() -> Dict[str, int]:
    return {o: 0 for o, _ in OUTCOMES}


def summarize(j: "Journal") -> Summary:
    s = Summary()
    s.total = len(j.observations)
    sig_map: Dict[Tuple[str, str, Tuple[str, ...]], List[str]] = {}
    for o in j.observations:
        rec = s.by_reception.setdefault(o.key, _blank())
        rec[o.outcome] = rec.get(o.outcome, 0) + 1
        st = s.by_stage.setdefault(o.stage, _blank())
        st[o.outcome] = st.get(o.outcome, 0) + 1
        s.by_outcome[o.outcome] = s.by_outcome.get(o.outcome, 0) + 1
        sig_map.setdefault(o.signature, []).append(o.outcome)

    for (rec, stage, dis), outcomes in sig_map.items():
        kinds = {x for x in outcomes}
        if len(kinds) > 1:
            s.unstable.append((rec, stage, len(dis), sorted(kinds)))

    s.frozen_receptions = sorted(r for r, c in s.by_reception.items() if c["frozen"])
    s.ok_receptions = sorted(r for r, c in s.by_reception.items() if c["ok"])
    s.reception_specific = bool(s.frozen_receptions and s.ok_receptions
                                and set(s.frozen_receptions) != set(s.ok_receptions))

    # 阶段提示：帮用户区分"慢初始化"和"拼点冲突"
    startup = s.by_stage.get("startup", {}).get("frozen", 0)
    clash = s.by_stage.get("clash", {}).get("frozen", 0)
    if startup and clash:
        s.stage_hint = ("启动阶段和拼点阶段都出现过卡死 —— 这通常是**两个不同的问题**："
                        "启动卡多半是 mod 初始化慢，拼点卡才是拼点链路冲突。"
                        "请把两类记录分开，二分排查时固定测试同一个接待、同一个阶段。")
    elif startup and not clash:
        s.stage_hint = ("卡死只出现在启动 / 读盘阶段 —— 更像某个 mod 初始化慢或数据量过大，"
                        "而不是拼点冲突。可对照报告里的 `LOG_SLOW_INIT` 条目。")
    elif clash and not startup:
        s.stage_hint = "卡死只出现在拼点阶段 —— 典型的拼点结算链路冲突，二分排查最有效。"
    elif s.total:
        s.stage_hint = "目前没有卡死记录，或阶段信息不足。"
    else:
        s.stage_hint = "还没有任何记录。"
    return s


@dataclass
class Journal:
    observations: List[Observation] = field(default_factory=list)

    # -- 基本操作 ----------------------------------------------------------
    def add(self, obs: Observation) -> Observation:
        if not obs.ts:
            obs.ts = time.strftime("%Y-%m-%d %H:%M:%S")
        self.observations.append(obs)
        return obs

    def remove(self, index: int) -> bool:
        if 0 <= index < len(self.observations):
            del self.observations[index]
            return True
        return False

    def clear(self) -> None:
        self.observations = []

    def receptions(self) -> List[str]:
        out: List[str] = []
        for o in self.observations:
            k = o.key
            if k != "(未填)" and k not in out:
                out.append(k)
        return out

    # -- 序列化 ------------------------------------------------------------
    def to_dict(self) -> dict:
        return {"version": VERSION,
                "observations": [asdict(o) for o in self.observations]}

    @classmethod
    def from_dict(cls, d: dict) -> "Journal":
        j = cls()
        for o in d.get("observations", []):
            if not isinstance(o, dict):
                continue
            j.observations.append(Observation(
                ts=o.get("ts", ""), reception=o.get("reception", ""),
                stage=o.get("stage", "clash"), outcome=o.get("outcome", "frozen"),
                disabled=list(o.get("disabled", []) or []),
                note=o.get("note", ""), source=o.get("source", "manual"),
                round_n=int(o.get("round_n", 0) or 0)))
        return j

    def save(self, path: Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
                        encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: Path) -> "Journal":
        path = Path(path)
        if not path.is_file():
            return cls()
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            return cls.from_dict(d) if isinstance(d, dict) else cls()
        except Exception:
            return cls()


# ---------------------------------------------------------------------------
# 展示
# ---------------------------------------------------------------------------
def _mod_names(mods_by_uid: Dict[str, object], uids: Sequence[str],
               limit: int = 8) -> str:
    names = []
    for u in uids[:limit]:
        m = mods_by_uid.get(u)
        names.append((getattr(m, "title", "") or getattr(m, "package_id", "") or u)
                     if m else u)
    if len(uids) > limit:
        names.append(f"…共 {len(uids)} 个")
    return "、".join(names) if names else "（全部启用）"


def render_markdown(j: "Journal", mods_by_uid: Dict[str, object]) -> str:
    s = summarize(j)
    L: List[str] = []
    L.append("# 接待 / 关卡症状档案")
    L.append("")
    L.append(f"- 记录条数：{s.total}")
    L.append(f"- 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}")
    L.append("")

    L.append("## 汇总")
    L.append("")
    L.append(f"- 卡死：{s.by_outcome.get('frozen', 0)} 次；"
             f"正常：{s.by_outcome.get('ok', 0)} 次；"
             f"不确定：{s.by_outcome.get('unclear', 0)} 次")
    L.append("")
    L.append("### 按接待")
    L.append("")
    L.append("| 接待 / 关卡 | 卡死 | 正常 | 不确定 |")
    L.append("| --- | --- | --- | --- |")
    for rec, c in s.by_reception.items():
        L.append(f"| {rec} | {c.get('frozen',0)} | {c.get('ok',0)} | "
                 f"{c.get('unclear',0)} |")
    L.append("")
    L.append("### 按阶段")
    L.append("")
    L.append("| 阶段 | 卡死 | 正常 | 不确定 |")
    L.append("| --- | --- | --- | --- |")
    for st, c in s.by_stage.items():
        L.append(f"| {STAGE_CN.get(st, st)} | {c.get('frozen',0)} | {c.get('ok',0)} | "
                 f"{c.get('unclear',0)} |")
    L.append("")
    if s.stage_hint:
        L.append(f"> {s.stage_hint}")
        L.append("")
    if s.reception_specific:
        L.append(f"> 卡死只出现在这些接待：{'、'.join(s.frozen_receptions)}；"
                 f"而这些接待是正常的：{'、'.join(s.ok_receptions)}。")
        L.append("> 说明症状可能与**特定接待的内容**有关，而不是全局性冲突。")
        L.append("")
    if s.unstable:
        L.append("### ⚠ 不一致记录（症状不稳定）")
        L.append("")
        L.append("同一个接待、同一步骤、同样的禁用集合却得到不同结果。"
                 "这种情况下二分排查的结论不可信，请对同一组多重测几次。")
        L.append("")
        for rec, stage, n, kinds in s.unstable:
            L.append(f"- {rec} / {STAGE_CN.get(stage, stage)} / 禁用 {n} 个："
                     f"出现过 {'、'.join(OUTCOME_CN.get(k, k) for k in kinds)}")
        L.append("")

    L.append("## 全部记录")
    L.append("")
    L.append("| 时间 | 接待 / 关卡 | 阶段 | 结果 | 当时禁用的 mod | 来源 | 备注 |")
    L.append("| --- | --- | --- | --- | --- | --- | --- |")
    for o in j.observations:
        dis = (f"{len(o.disabled)} 个" if o.disabled else "无（全部启用）")
        L.append(f"| {o.ts} | {o.key} | {o.stage_cn} | {o.outcome_cn} | {dis} | "
                 f"{o.source_cn} | {o.note} |")
    L.append("")
    return "\n".join(L)


def render_text_summary(j: "Journal", mods_by_uid: Dict[str, object]) -> str:
    """给界面下方汇总区用。"""
    s = summarize(j)
    L: List[str] = []
    L.append(f"共 {s.total} 条记录：卡死 {s.by_outcome.get('frozen',0)}、"
             f"正常 {s.by_outcome.get('ok',0)}、"
             f"不确定 {s.by_outcome.get('unclear',0)}")
    L.append("")
    if s.total:
        L.append("按接待：")
        for rec, c in s.by_reception.items():
            L.append(f"  {rec}：卡死 {c.get('frozen',0)} / 正常 {c.get('ok',0)}"
                     f" / 不确定 {c.get('unclear',0)}")
        L.append("")
        L.append("按阶段：")
        for st, c in s.by_stage.items():
            L.append(f"  {STAGE_CN.get(st, st)}：卡死 {c.get('frozen',0)} / "
                     f"正常 {c.get('ok',0)} / 不确定 {c.get('unclear',0)}")
        L.append("")
    if s.stage_hint:
        L.append("判断：" + s.stage_hint)
        L.append("")
    if s.reception_specific:
        L.append("注意：卡死只出现在 " + "、".join(s.frozen_receptions) +
                 "；而 " + "、".join(s.ok_receptions) + " 是正常的 —— "
                 "症状可能与特定接待的内容有关。")
        L.append("")
    if s.unstable:
        L.append("⚠ 不一致记录（同一接待 + 同一步骤 + 同一组禁用 mod 出现两种结果）：")
        for rec, stage, n, kinds in s.unstable:
            L.append(f"  {rec} / {STAGE_CN.get(stage, stage)} / 禁用 {n} 个："
                     + "、".join(OUTCOME_CN.get(k, k) for k in kinds))
        L.append("  这种情况下二分排查的结论不可信，请重测。")
    if not j.observations:
        L.append("还没有记录。每做一次测试就在上面记一条；"
                 "二分排查的结果会自动写入这里。")
    return "\n".join(L)
