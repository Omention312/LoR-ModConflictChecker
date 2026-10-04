# -*- coding: utf-8 -*-
"""命令行入口：lorcheck_cli.py [--game DIR] [--workshop DIR] [--log FILE]
                          [--out DIR] [--top N] [--json]"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lorcheck import report as report_mod
from lorcheck.config import SEV_CN
from lorcheck.engine import run_scan

DEFAULT_OUT = Path(__file__).resolve().parent / "报告"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="废墟图书馆 (Library of Ruina) Mod 冲突检测器 · 命令行模式")
    ap.add_argument("--game", help="游戏安装目录（含 LibraryOfRuina.exe）")
    ap.add_argument("--workshop", help="创意工坊 mod 目录 (steamapps\\workshop\\content\\1256670)")
    ap.add_argument("--log", dest="log_path", help="Player.log 路径")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="报告输出目录")
    ap.add_argument("--top", type=int, default=8, help="显示前 N 个嫌疑 mod")
    ap.add_argument("--json", action="store_true", help="把结果 JSON 打到标准输出")
    ap.add_argument("--quiet", action="store_true", help="不打印进度")
    args = ap.parse_args(argv)

    def progress(msg: str) -> None:
        if not args.quiet:
            print("  " + msg, flush=True)

    print("=== 废墟图书馆 Mod 冲突检测器 ===")
    result = run_scan(game_dir=args.game, workshop_dir=args.workshop,
                      log_path=args.log_path,
                      progress=None if args.quiet else progress)

    p = result.paths
    print()
    print(f"游戏目录     : {p.game_dir}")
    print(f"创意工坊目录 : {p.workshop_dir}")
    print(f"本地 Mods    : {p.local_mods_dir}")
    print(f"Player.log   : {result.log.path}")
    print(f"mod 数量     : {len(result.mods)}"
          f"（工坊 {len(result.workshop_mods)} / 本地 {len(result.local_mods)}）")

    counts = result.counts()
    print()
    print("--- 结论摘要 ---")
    for sev in sorted(counts, reverse=True):
        if counts[sev]:
            print(f"  {SEV_CN[sev]:<3} : {counts[sev]}")

    print()
    print(f"--- 拼点卡死嫌疑排行（前 {args.top}）---")
    for i, s in enumerate(result.suspects[:args.top], 1):
        print(f"  {i}. [{s.score:>3}] {s.mod.display}")
        for r in s.reasons[:4]:
            print(f"        · {r}")

    print()
    print("--- 冲突明细 ---")
    for f in result.findings:
        mark = "★" if f.clash_relevant else " "
        print(f"  {mark} [{f.sev_cn}] {f.title}")
        if f.advice:
            print(f"      → {f.advice}")

    out = report_mod.write_report(result, Path(args.out))
    print()
    print(f"报告已生成: {out}")

    if args.json:
        print(json.dumps(report_mod.result_to_dict(result),
                         ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
