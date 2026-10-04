# -*- coding: utf-8 -*-
"""扫描编排：定位 -> 静态扫描 -> 日志分析 -> 规则判定。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

from . import bfsave, locate, rules, scan_log, scan_mods
from .config import SEV_CN


@dataclass
class ScanResult:
    paths: locate.Paths
    mods: List[scan_mods.ModInfo] = field(default_factory=list)
    log: scan_log.LogReport = field(default_factory=scan_log.LogReport)
    findings: List[rules.Finding] = field(default_factory=list)
    suspects: List[rules.Suspect] = field(default_factory=list)
    log_tail: List[str] = field(default_factory=list)
    elapsed: float = 0.0
    started_at: str = ""
    modsetting: Optional[bfsave.ModSetting] = None
    enabled_source: str = ""       # "ModSetting.save" / "Player.log" / ""
    active_ids: List[str] = field(default_factory=list)

    @property
    def workshop_mods(self) -> List[scan_mods.ModInfo]:
        return [m for m in self.mods if m.source == "workshop"]

    @property
    def local_mods(self) -> List[scan_mods.ModInfo]:
        return [m for m in self.mods if m.source == "local"]

    @property
    def enabled_mods(self) -> List[scan_mods.ModInfo]:
        return [m for m in self.mods if m.enabled is not False]

    @property
    def disabled_mods(self) -> List[scan_mods.ModInfo]:
        return [m for m in self.mods if m.enabled is False]

    def counts(self) -> dict:
        c = {sev: 0 for sev in SEV_CN}
        for f in self.findings:
            c[f.severity] = c.get(f.severity, 0) + 1
        return c


def mark_enabled(mods: List[scan_mods.ModInfo], log: scan_log.LogReport,
                 modsetting: Optional[bfsave.ModSetting]) -> str:
    """判定每个 mod 当前是否启用，返回判定依据。"""
    if modsetting and modsetting.active_ids:
        want = {x.strip().lower() for x in modsetting.active_ids}
        for m in mods:
            if m.package_id:
                m.enabled = m.package_id.strip().lower() in want
        return "ModSetting.save"

    # 降级：用最近一次运行加载过的 mod（来自 Player.log 里的路径）
    loaded = set(log.mod_paths.keys())
    if loaded:
        for m in mods:
            m.enabled = m.uid in loaded
        return "Player.log"

    for m in mods:
        m.enabled = None
    return ""


def run_scan(game_dir: Optional[str] = None,
             workshop_dir: Optional[str] = None,
             log_path: Optional[str] = None,
             progress: Optional[Callable[[str], None]] = None,
             log_tail_lines: int = 80) -> ScanResult:
    def say(msg: str) -> None:
        if progress:
            progress(msg)

    t0 = time.time()
    started = time.strftime("%Y-%m-%d %H:%M:%S")
    say("正在定位 Steam / 游戏 / 日志 …")
    paths = locate.autodetect(game_dir=game_dir, workshop_dir=workshop_dir,
                              log_path=log_path)

    say(f"游戏目录：{paths.game_dir}")
    say(f"创意工坊：{paths.workshop_dir}")

    mods = scan_mods.scan_all(paths.local_mods_dir, paths.workshop_dir,
                              progress=lambda s: say(s))
    say(f"共读取 {len(mods)} 个 mod")

    log_target = paths.player_logs[0] if paths.player_logs else None
    log = scan_log.analyse_log(log_target, progress=say)

    say("正在读取 mod 启用状态 …")
    modsetting = bfsave.load([m.package_id for m in mods if m.package_id])
    source = mark_enabled(mods, log, modsetting)
    on = sum(1 for m in mods if m.enabled is True)
    off = sum(1 for m in mods if m.enabled is False)
    if source:
        say(f"启用状态来源：{source}（启用 {on} / 已禁用 {off}）")

    say("正在应用冲突规则 …")
    findings = rules.build_findings(mods, log)
    suspects = rules.build_suspects(mods, log, findings)

    return ScanResult(paths=paths, mods=mods, log=log, findings=findings,
                      suspects=suspects,
                      log_tail=scan_log.tail_lines(log_target, log_tail_lines),
                      elapsed=time.time() - t0, started_at=started,
                      modsetting=modsetting, enabled_source=source,
                      active_ids=list(modsetting.active_ids) if modsetting else [])
