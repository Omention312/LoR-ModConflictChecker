# -*- coding: utf-8 -*-
"""废墟图书馆 Mod 冲突检测器 · 图形界面

双击 `启动检测器.bat` 或本文件即可运行。纯标准库（tkinter），无需安装依赖。
命令行模式见 lorcheck_cli.py。
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path
from tkinter import (BOTH, END, LEFT, RIGHT, VERTICAL, BooleanVar, Listbox,
                     StringVar, Text, Tk, Toplevel)
from tkinter import filedialog, messagebox, ttk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lorcheck import __version__, bisect, journal, locate, report as report_mod
from lorcheck.config import SEV_CN
from lorcheck.engine import ScanResult, run_scan

APP_TITLE = f"废墟图书馆 Mod 冲突检测器 v{__version__}"
BASE_DIR = Path(__file__).resolve().parent
REPORT_DIR = BASE_DIR / "报告"

FONT = ("Microsoft YaHei UI", 10)
FONT_MONO = ("Consolas", 10)
BG = "#161a22"
FG = "#e6e8ef"
SEV_COLOR = {4: "#ff5c6c", 3: "#ff9f43", 2: "#f5d76e", 1: "#5bc0de", 0: "#8e9bb3"}
SEV_TAG = {4: "fatal", 3: "high", 2: "mid", 1: "low", 0: "info"}


class App:
    def __init__(self, root: Tk):
        self.root = root
        self.result: ScanResult | None = None
        self.report_path: Path | None = None
        self.scanning = False
        # 工作线程只往队列里放消息，所有 tkinter 调用都留在主线程，
        # 避免跨线程操作 Tcl 解释器导致的 RuntimeError。
        self.msg_queue: "queue.Queue[tuple]" = queue.Queue()

        root.title(APP_TITLE)
        root.geometry("1120x760")
        root.minsize(940, 620)

        try:
            ttk.Style().theme_use("clam")
        except Exception:
            pass
        self._style()

        self.var_game = StringVar()
        self.var_workshop = StringVar()
        self.var_log = StringVar()
        self.var_status = StringVar(value="就绪。点击“开始扫描”自动检测 Steam / 游戏 / 日志。")
        # 二分排查
        self.var_scope = StringVar(value="enabled")
        self.var_baseline = BooleanVar(value=True)
        self.var_bs_status = StringVar(value="还没有开始排查。先扫描，再点“开始新排查”。")
        self.var_reception = StringVar(value="")
        self.var_stage = StringVar(value="开始拼点")
        self.bs_session: bisect.Session | None = None
        self.bs_path = REPORT_DIR / "排查会话.json"
        self.bs_custom: list[str] = []
        # 接待 / 关卡症状档案
        self.journal = journal.Journal()
        self.journal_path = REPORT_DIR / "症状档案.json"
        self.var_obs_reception = StringVar(value="")
        self.var_obs_stage = StringVar(value="开始拼点")
        self.var_obs_outcome = StringVar(value="卡死")
        self.var_obs_note = StringVar(value="")
        self.var_j_status = StringVar(value="还没有记录。")

        self._build_paths()
        self._build_toolbar()
        self._build_tabs()

        self.root.after(120, self.autofill)
        self.root.after(150, self._pump)
        self.root.after(300, self._bs_autoload)
        self.root.after(320, self._j_autoload)

    # ------------------------------------------------------------------ 样式
    def _style(self):
        st = ttk.Style()
        st.configure(".", font=FONT)
        st.configure("TFrame", background=BG)
        st.configure("TLabel", background=BG, foreground=FG)
        st.configure("TLabelframe", background=BG, foreground=FG)
        st.configure("TLabelframe.Label", background=BG, foreground="#9aa1b4")
        st.configure("TButton", padding=(10, 5))
        st.configure("TCheckbutton", background=BG, foreground=FG)
        st.configure("TNotebook", background=BG, borderwidth=0)
        st.configure("TNotebook.Tab", padding=(14, 7))
        st.configure("Treeview", background="#1b1e26", fieldbackground="#1b1e26",
                     foreground=FG, rowheight=24, borderwidth=0)
        st.configure("Treeview.Heading", font=("Microsoft YaHei UI", 10, "bold"))
        st.configure("Treeview", font=FONT)
        self.root.configure(bg=BG)

    # ------------------------------------------------------------ 路径输入区
    def _build_paths(self):
        f = ttk.Frame(self.root, padding=(12, 12, 12, 4))
        f.pack(fill="x")
        f.columnconfigure(1, weight=1)

        rows = [("游戏安装目录", self.var_game, self._pick_game),
                ("创意工坊 Mod 目录", self.var_workshop, self._pick_workshop),
                ("Player.log", self.var_log, self._pick_log)]
        for i, (label, var, cmd) in enumerate(rows):
            ttk.Label(f, text=label, width=17).grid(row=i, column=0, sticky="w", pady=3)
            ttk.Entry(f, textvariable=var).grid(row=i, column=1, sticky="ew", pady=3, padx=6)
            ttk.Button(f, text="浏览…", command=cmd, width=9).grid(row=i, column=2, pady=3)

    # ---------------------------------------------------------------- 工具栏
    def _build_toolbar(self):
        f = ttk.Frame(self.root, padding=(12, 4))
        f.pack(fill="x")

        self.btn_scan = ttk.Button(f, text="开始扫描", command=self.start_scan)
        self.btn_scan.pack(side=LEFT)
        self.btn_report = ttk.Button(f, text="导出 HTML 报告", command=self.export_report,
                                     state="disabled")
        self.btn_report.pack(side=LEFT, padx=6)
        self.btn_open = ttk.Button(f, text="打开报告目录", command=self.open_report_dir,
                                   state="disabled")
        self.btn_open.pack(side=LEFT, padx=6)
        ttk.Button(f, text="重新自动定位", command=self.autofill).pack(side=LEFT, padx=6)

        self.bar = ttk.Progressbar(f, mode="indeterminate", length=180)
        self.bar.pack(side=RIGHT)

        s = ttk.Label(self.root, textvariable=self.var_status, padding=(14, 2, 14, 6),
                      foreground="#9aa1b4")
        s.pack(fill="x")

    # ---------------------------------------------------------------- 标签页
    def _build_tabs(self):
        nb = ttk.Notebook(self.root)
        nb.pack(fill=BOTH, expand=True, padx=12, pady=(0, 12))
        self.nb = nb

        # 概览
        self.txt_overview = self._text_tab(nb, "概览")
        # 冲突清单
        tab = ttk.Frame(nb, padding=6)
        nb.add(tab, text="冲突清单")
        det = ttk.Label(tab, text="选中上面任意一行查看证据与处置建议：")
        self.txt_finding = Text(tab, height=12, font=FONT_MONO, bg="#0e1015", fg="#c9cee0",
                                relief="flat", wrap="word")
        self.txt_finding.configure(state="disabled")
        # 先占位底部，再让表格吃掉剩余空间——否则表格会把详情面板挤出可视区
        det.pack(side="bottom", anchor="w", pady=(8, 2))
        self.txt_finding.pack(side="bottom", fill="x")
        cols = ("sev", "code", "title", "n")
        self.tree = ttk.Treeview(tab, columns=cols, show="headings", selectmode="browse",
                                 height=12)
        for c, t, w in (("sev", "级别", 70), ("code", "类型", 170),
                        ("title", "标题", 640), ("n", "涉及mod", 90)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="w", stretch=(c == "title"))
        self.tree.pack(side="top", fill=BOTH, expand=True)
        for sev, tag in SEV_TAG.items():
            self.tree.tag_configure(tag, foreground=SEV_COLOR[sev])
        self.tree.bind("<<TreeviewSelect>>", self._on_finding_select)

        # 拼点专项
        self.txt_clash = self._text_tab(nb, "拼点卡死专项")
        # 二分排查
        self._build_bisect_tab(nb)
        # 接待症状档案
        self._build_journal_tab(nb)
        # Mod 清单
        tab2 = ttk.Frame(nb, padding=6)
        nb.add(tab2, text="Mod 清单")
        cols2 = ("name", "pkg", "src", "st", "folder", "ent", "asm", "clash", "note")
        self.tree_mods = ttk.Treeview(tab2, columns=cols2, show="headings")
        for c, t, w in (("name", "名称", 230), ("pkg", "包 ID", 170), ("src", "来源", 55),
                        ("st", "状态", 60), ("folder", "目录", 100),
                        ("ent", "数据条目", 70),
                        ("asm", "程序集", 60), ("clash", "拼点类型", 70),
                        ("note", "备注", 220)):
            self.tree_mods.heading(c, text=t)
            self.tree_mods.column(c, width=w, anchor="w", stretch=(c in ("name", "note")))
        vs = ttk.Scrollbar(tab2, orient=VERTICAL, command=self.tree_mods.yview)
        self.tree_mods.configure(yscrollcommand=vs.set)
        self.tree_mods.pack(side=LEFT, fill=BOTH, expand=True)
        vs.pack(side=RIGHT, fill="y")
        # 日志
        self.txt_log = self._text_tab(nb, "日志分析")

    def _text_tab(self, nb, title: str) -> Text:
        tab = ttk.Frame(nb, padding=6)
        nb.add(tab, text=title)
        t = Text(tab, font=FONT_MONO, bg="#0e1015", fg="#c9cee0", relief="flat",
                 wrap="word", padx=10, pady=8)
        vs = ttk.Scrollbar(tab, orient=VERTICAL, command=t.yview)
        t.configure(yscrollcommand=vs.set)
        t.pack(side=LEFT, fill=BOTH, expand=True)
        vs.pack(side=RIGHT, fill="y")
        t.configure(state="disabled")
        return t

    # ------------------------------------------------------------ 文本小工具
    @staticmethod
    def _set_text(widget: Text, content: str):
        widget.configure(state="normal")
        widget.delete("1.0", END)
        widget.insert("1.0", content)
        widget.configure(state="disabled")

    def _insert_finding(self, f) -> None:
        lines = [f"【{f.sev_cn}】{f.title}", "", f"规则代码：{f.code}",
                 f"拼点相关：{'是' if f.clash_relevant else '否'}", "",
                 "说明：", f.detail, ""]
        if f.advice:
            lines += ["处置建议：", f.advice, ""]
        lines.append("证据：")
        lines += [f"  · {e}" for e in f.evidence]
        if f.mods and self.result:
            by = {m.uid: m for m in self.result.mods}
            lines += ["", "涉及的 Mod："]
            for uid in f.mods:
                m = by.get(uid)
                if m:
                    lines.append(f"  · {m.display}  [{m.source}]  {m.path}")
        self._set_text(self.txt_finding, "\n".join(lines))

    # ---------------------------------------------------------------- 定位
    def autofill(self):
        try:
            p = locate.autodetect()
        except Exception:
            return
        self.var_game.set(str(p.game_dir) if p.game_dir else "")
        self.var_workshop.set(str(p.workshop_dir) if p.workshop_dir else "")
        self.var_log.set(str(p.player_logs[0]) if p.player_logs else "")
        missing = []
        if not p.game_dir:
            missing.append("游戏目录")
        if not p.workshop_dir:
            missing.append("创意工坊目录")
        if not p.player_logs:
            missing.append("Player.log")
        if missing:
            self.var_status.set("未能自动定位：" + "、".join(missing) + "，请手动选择。")
        else:
            self.var_status.set("已自动定位完成，点击“开始扫描”。")

    def _pick_game(self):
        d = filedialog.askdirectory(title="选择 Library Of Ruina 安装目录（含 LibraryOfRuina.exe）")
        if d:
            self.var_game.set(d)

    def _pick_workshop(self):
        d = filedialog.askdirectory(title="选择创意工坊目录（steamapps\\workshop\\content\\1256670）")
        if d:
            self.var_workshop.set(d)

    def _pick_log(self):
        f = filedialog.askopenfilename(title="选择 Player.log",
                                       filetypes=[("日志文件", "*.log"), ("所有文件", "*.*")])
        if f:
            self.var_log.set(f)

    # ---------------------------------------------------------------- 扫描
    def start_scan(self):
        if self.scanning:
            return
        self.scanning = True
        self.btn_scan.configure(state="disabled")
        self.btn_report.configure(state="disabled")
        self.btn_open.configure(state="disabled")
        self.bar.start(12)
        self.var_status.set("正在扫描 …")

        game = self.var_game.get().strip() or None
        workshop = self.var_workshop.get().strip() or None
        log = self.var_log.get().strip() or None

        def worker():
            try:
                res = run_scan(game_dir=game, workshop_dir=workshop, log_path=log,
                               progress=lambda m: self.msg_queue.put(("progress", m)))
                self.msg_queue.put(("done", res, None))
            except Exception:
                self.msg_queue.put(("done", None, traceback.format_exc()))

        threading.Thread(target=worker, daemon=True).start()

    def _pump(self):
        """主线程定时器：消费工作线程的消息（唯一接触 UI 的地方）。"""
        try:
            while True:
                kind, *payload = self.msg_queue.get_nowait()
                if kind == "progress":
                    self.var_status.set(payload[0])
                elif kind == "done":
                    self._scan_done(payload[0], payload[1])
        except queue.Empty:
            pass
        self.root.after(120, self._pump)

    def _scan_done(self, res: ScanResult | None, err: str | None):
        self.scanning = False
        self.bar.stop()
        self.btn_scan.configure(state="normal")
        if err:
            self.var_status.set("扫描失败。")
            messagebox.showerror("扫描失败", err[-2000:])
            return
        self.result = res
        self._render(res)
        self._bs_render()
        self._bs_refresh_scope()
        self.btn_report.configure(state="normal")
        self.btn_open.configure(state="normal")
        self.var_status.set(
            f"扫描完成，用时 {res.elapsed:.1f}s：致命 {res.counts().get(4,0)}、"
            f"高 {res.counts().get(3,0)}、中 {res.counts().get(2,0)}。"
            f"建议先看“拼点卡死专项”。")

    # ---------------------------------------------------------------- 渲染
    def _render(self, res: ScanResult):
        c = res.counts()
        lines = [
            "=" * 78,
            "  环境信息",
            "=" * 78,
            f"  Steam 根目录      : {res.paths.steam_root}",
            f"  Steam 库          : {', '.join(str(x) for x in res.paths.libraries)}",
            f"  游戏目录          : {res.paths.game_dir}",
            f"  创意工坊目录      : {res.paths.workshop_dir}",
            f"  本地 Mods 目录    : {res.paths.local_mods_dir}",
            f"  Player.log        : {res.log.path}",
            f"  日志规模          : {res.log.size/1048576:.2f} MB / {res.log.total_lines} 行",
            "",
            "=" * 78,
            "  结论摘要",
            "=" * 78,
        ]
        for sev in (4, 3, 2, 1, 0):
            if c.get(sev):
                lines.append(f"  {SEV_CN[sev]:<3} : {c[sev]} 条")
        lines += [
            "",
            f"  mod 总数 {len(res.mods)}（工坊 {len(res.workshop_mods)} / 本地 {len(res.local_mods)}）",
            f"  日志确认的重复 LorId 异常 : {len(res.log.same_key)} 处",
            f"  重复程序集报错            : {sum(res.log.dup_assemblies.values())} 次",
            f"  重复关键字报错            : {len(res.log.already_exists)} 个",
            f"  卡死的初始化器            : {res.log.stall_owner_type or '—'}",
            f"  卡死发生在战斗中          : "
            f"{'是' if (res.log.tail_is_battle or res.log.tail_battle_markers >= 3) else '否'}",
            "",
            "=" * 78,
            "  怀疑度排行（Top 15）",
            "=" * 78,
        ]
        for i, s in enumerate(res.suspects[:15], 1):
            lines.append(f"  {i:>2}. [{s.score:>4}] {s.mod.display}")
            for r in s.reasons[:4]:
                lines.append(f"          · {r}")
        lines += ["", "=" * 78,
                  "  拼点相关冲突项（红色/橙色优先处理）",
                  "=" * 78]
        for f in res.findings:
            if f.clash_relevant:
                lines.append(f"  [{f.sev_cn}] {f.title}")
        self._set_text(self.txt_overview, "\n".join(lines))

        # 冲突清单
        self.tree.delete(*self.tree.get_children())
        for f in res.findings:
            self.tree.insert("", END, values=(f.sev_cn, f.code, f.title, len(f.mods)),
                             tags=(SEV_TAG.get(f.severity, "info"),))
        if res.findings:
            first = self.tree.get_children()[0]
            self.tree.selection_set(first)
            self.tree.focus(first)

        # 拼点专项
        cl = ["接待拼点卡死 · 专项排查", "=" * 78, ""]
        cl.append("第一步：按怀疑度从高到低，一次只禁用 1～2 个 mod，")
        cl.append("        进入同一个接待并制造拼点，看是否仍然卡死。")
        cl.append("第二步：若单个无效，用二分法（先禁用一半）缩小范围。")
        cl.append("第三步：优先怀疑下面这些条目。")
        cl.append("")
        cl.append("-" * 78)
        cl.append("怀疑度排行")
        cl.append("-" * 78)
        for i, s in enumerate(res.suspects[:20], 1):
            cl.append(f"{i:>2}. [{s.score:>4}] {s.mod.display}")
            cl.append(f"      {s.mod.path}")
            for r in s.reasons:
                cl.append(f"      · {r}")
            cl.append("")
        cl.append("-" * 78)
        cl.append("与拼点直接相关的冲突项")
        cl.append("-" * 78)
        for f in res.findings:
            if f.clash_relevant:
                cl.append(f"[{f.sev_cn}] {f.code}  {f.title}")
                cl.append(f"    {f.detail}")
                for e in f.evidence[:6]:
                    cl.append(f"      · {e}")
                if f.advice:
                    cl.append(f"    → {f.advice}")
                cl.append("")
        cl.append("-" * 78)
        cl.append("日志中的卡死证据")
        cl.append("-" * 78)
        if res.log.stall_owner_type:
            cl.append(f"卡住的初始化器    : {res.log.stall_owner_type}")
            cl.append(f"  所属程序集      : {res.log.stall_owner_dll}")
            cl.append(f"  日志行号        : {res.log.stall_owner_line}")
            if res.log.stall_seconds:
                cl.append(f"  卡住时长        : 约 {res.log.stall_seconds:.0f} 秒"
                          "（加载进度停在同一百分比）")
        cl.append(f"初始化卡住        : {'是' if res.log.initializer_stall else '否'}"
                  f"（Repeat Wait 最大 {res.log.repeat_wait_max}）")
        if res.log.progress_stall:
            cl.append(f"进度停住          : {res.log.progress_stall}")
        cl.append(f"日志尾部重复行    : {res.log.tail_repeat[1]} 次  "
                  f"{res.log.tail_repeat[0][:100]}")
        cl.append(f"卡死在战斗中      : "
                  f"{'是' if (res.log.tail_is_battle or res.log.tail_battle_markers >= 3) else '否'}"
                  f"（战斗特效/语音标记 {res.log.tail_battle_markers} 行）")
        if res.log.deck_not_found:
            cl.append(f"找不到的卡组      : {', '.join(res.log.deck_not_found[:5])}")
        cl.append(f"崩溃上报标记      : {'有' if res.log.crash_report_marker else '无'}")
        self._set_text(self.txt_clash, "\n".join(cl))

        # Mod 清单
        self.tree_mods.delete(*self.tree_mods.get_children())
        for m in sorted(res.mods, key=lambda x: (x.enabled is not True, x.source, x.folder)):
            self.tree_mods.insert("", END, values=(
                m.title or "(无标题)", m.package_id,
                "工坊" if m.source == "workshop" else "本地",
                "启用" if m.enabled is True else ("已禁用" if m.enabled is False else "未知"),
                m.folder,
                len(m.entries), len(m.assemblies), len(m.clash_types),
                "；".join(m.issues)))

        # 日志
        lg = ["=" * 78, "  日志分析", "=" * 78,
              f"  文件            : {res.log.path}",
              f"  大小 / 行数     : {res.log.size/1048576:.2f} MB / {res.log.total_lines} 行",
              f"  重复 LorId      : {len(res.log.same_key)} 处",
              f"  卡住的初始化器  : {res.log.stall_owner_type or '—'}"
              + (f"（卡住 {res.log.stall_seconds:.0f} 秒）" if res.log.stall_seconds else ""),
              f"  重复程序集报错  : {sum(res.log.dup_assemblies.values())} 次",
              f"  重复关键字      : {len(res.log.already_exists)} 个",
              f"  缺失资源        : {len(res.log.missing_paths)} 个",
              f"  找不到的卡组    : {', '.join(res.log.deck_not_found) or '—'}",
              f"  捕获异常        : {len(res.log.exceptions)} 个",
              f"  初始化卡住      : {'是' if res.log.initializer_stall else '否'}",
              f"  尾部重复行      : {res.log.tail_repeat[1]} 次",
              f"  卡死在战斗中    : "
              f"{'是' if (res.log.tail_is_battle or res.log.tail_battle_markers >= 3) else '否'}",
              ""]
        if res.log.same_key:
            lg += ["-" * 78, "重复 LorId 明细", "-" * 78]
            for it in res.log.same_key:
                lg.append(f"  第 {it['line_no']} 行  LorId({it['key']})"
                          f"{'  ← 崩溃上报前' if it.get('crash_upload') else ''}")
            lg.append("")
        if res.log.dup_assemblies:
            lg += ["-" * 78, "重复程序集名（按次数）", "-" * 78]
            for n, cnt in sorted(res.log.dup_assemblies.items(),
                                 key=lambda kv: -kv[1])[:25]:
                lg.append(f"  {cnt:>3} × {n}")
            lg.append("")
        if res.log.already_exists:
            lg += ["-" * 78, "重复注册的关键字", "-" * 78]
            for n in sorted(res.log.already_exists):
                lg.append(f"  {n}")
            lg.append("")
        if res.log.exceptions:
            lg += ["-" * 78, "异常列表", "-" * 78]
            for e in res.log.exceptions[:20]:
                lg.append(f"  第 {e.line_no} 行  {e.type}: {e.message[:160]}")
                for s in e.stack[:4]:
                    lg.append(f"        {s[:150]}")
                lg.append("")
        lg += ["-" * 78, "日志尾部（最后 60 行）", "-" * 78]
        lg += list(res.log_tail[-60:])
        self._set_text(self.txt_log, "\n".join(lg))
        self.nb.select(0)

    # ------------------------------------------------------------ 二分排查
    INFRA_IDS = {"basemod", "1frameworkpriorityloader", "1frameworkloader",
                 "extendedloader", "harmonypreloader", "frameworkloader",
                 "1frameworkpriorityinjector", "0harmonypreloader"}

    def _build_bisect_tab(self, nb):
        tab = ttk.Frame(nb, padding=6)
        nb.add(tab, text="二分排查")

        top = ttk.Frame(tab)
        top.pack(fill="x")
        ttk.Label(top, text="排查范围：").pack(side=LEFT)
        for val, text in (("enabled", "全部已启用"), ("clash", "仅拼点相关"),
                          ("custom", "自定义勾选")):
            ttk.Radiobutton(top, text=text, value=val, variable=self.var_scope,
                            command=self._bs_refresh_scope).pack(side=LEFT, padx=2)
        ttk.Button(top, text="选择…", width=7,
                   command=self._bs_pick_custom).pack(side=LEFT, padx=4)
        ttk.Checkbutton(top, text="先做基线测试",
                        variable=self.var_baseline).pack(side=LEFT, padx=10)

        # 固定测试条件：每轮都必须在同一个接待、同一步骤复现
        cond = ttk.Frame(tab)
        cond.pack(fill="x", pady=(4, 0))
        ttk.Label(cond, text="固定测试条件：在").pack(side=LEFT)
        self.cmb_reception = ttk.Combobox(cond, textvariable=self.var_reception,
                                          width=22, font=FONT)
        self.cmb_reception.pack(side=LEFT, padx=4)
        ttk.Label(cond, text="接待里，做到").pack(side=LEFT)
        self.cmb_stage = ttk.Combobox(
            cond, textvariable=self.var_stage, width=12, font=FONT, state="readonly",
            values=[label for _, label in journal.STAGES])
        self.cmb_stage.pack(side=LEFT, padx=4)
        ttk.Label(cond, text="这一步").pack(side=LEFT)
        ttk.Label(cond, text="（每轮都按这个条件复现，结果才可比）",
                  foreground="#9aa1b4").pack(side=LEFT, padx=8)

        bar = ttk.Frame(tab)
        bar.pack(fill="x", pady=(6, 2))
        ttk.Button(bar, text="开始新排查", command=self._bs_start).pack(side=LEFT)
        self.bs_btn_undo = ttk.Button(bar, text="撤销上一步", command=self._bs_undo,
                                      state="disabled")
        self.bs_btn_undo.pack(side=LEFT, padx=4)
        ttk.Button(bar, text="重置", command=self._bs_reset).pack(side=LEFT, padx=4)
        ttk.Button(bar, text="复制本轮清单",
                   command=self._bs_copy).pack(side=LEFT, padx=4)
        ttk.Button(bar, text="导出排查记录",
                   command=self._bs_export).pack(side=LEFT, padx=4)

        ttk.Label(tab, textvariable=self.var_bs_status, foreground="#9aa1b4",
                  padding=(2, 4), wraplength=1060, justify="left").pack(fill="x")

        self.txt_bs = Text(tab, height=15, font=FONT_MONO, bg="#0e1015", fg="#c9cee0",
                           relief="flat", wrap="word", padx=10, pady=8)
        self.txt_bs.pack(fill=BOTH, expand=True)
        self.txt_bs.configure(state="disabled")

        res = ttk.Frame(tab)
        res.pack(fill="x", pady=(6, 2))
        self.bs_res_btns = []
        for text, val in (("仍然卡死", "persists"), ("已恢复正常", "fixed"),
                          ("不确定，重测", "unsure")):
            b = ttk.Button(res, text=text, command=lambda v=val: self._bs_apply(v))
            b.pack(side=LEFT, padx=(0, 8))
            self.bs_res_btns.append(b)

        hist = ttk.Frame(tab)
        hist.pack(fill=BOTH, expand=False, pady=(6, 0))
        ttk.Label(hist, text="实验记录：").pack(anchor="w")
        cols = ("n", "role", "cnt", "result", "rest", "time")
        self.tree_bs = ttk.Treeview(hist, columns=cols, show="headings", height=6)
        for c, t, w in (("n", "轮次", 50), ("role", "阶段", 70), ("cnt", "禁用数", 70),
                        ("result", "结果", 100), ("rest", "剩余待查", 80),
                        ("time", "时间", 160)):
            self.tree_bs.heading(c, text=t)
            self.tree_bs.column(c, width=w, anchor="w", stretch=(c == "time"))
        self.tree_bs.pack(fill=BOTH, expand=True)
        self._bs_set_buttons(False)

    # -- 候选集合 ----------------------------------------------------------
    def _bs_enabled_mods(self):
        if not self.result:
            return []
        out = []
        for m in self.result.mods:
            if m.enabled is not True:
                continue
            if m.is_framework:
                continue
            if (m.package_id or "").strip().lower() in self.INFRA_IDS:
                continue
            out.append(m)
        score = {s.mod.uid: s.score for s in self.result.suspects}
        out.sort(key=lambda m: (-score.get(m.uid, 0), m.folder))
        return out

    def _bs_candidates(self):
        pool = self._bs_enabled_mods()
        scope = self.var_scope.get()
        if scope == "clash":
            pool = [m for m in pool if m.clash_types]
        elif scope == "custom":
            keep = set(self.bs_custom)
            pool = [m for m in pool if m.uid in keep]
        return pool

    def _bs_refresh_scope(self):
        if not self.result:
            return
        pool = self._bs_candidates()
        total = len(self._bs_enabled_mods())
        clash = len([m for m in self._bs_enabled_mods() if m.clash_types])
        self.var_bs_status.set(
            f"当前范围候选 {len(pool)} 个 mod（可选：全部已启用 {total} / "
            f"仅拼点相关 {clash}）。已排除框架与加载器，"
            f"禁用它们会让其它 mod 不加载、实验失真。")

    def _bs_pick_custom(self):
        if not self.result:
            messagebox.showinfo("需要先扫描", "请先点击“开始扫描”。")
            return
        pool = self._bs_enabled_mods()
        if not pool:
            messagebox.showwarning("没有候选", "没有可用于排查的已启用 mod。")
            return
        win = Toplevel(self.root)
        win.title("选择要纳入排查的 mod")
        win.geometry("620x560")
        win.transient(self.root)
        ttk.Label(win, text="按住 Ctrl / Shift 可多选：", padding=8).pack(anchor="w")
        frame = ttk.Frame(win, padding=(8, 0, 8, 8))
        frame.pack(fill=BOTH, expand=True)
        lb = Listbox(frame, selectmode="extended", font=FONT, bg="#1b1e26",
                     fg=FG, selectbackground="#3a4256", relief="flat")
        for m in pool:
            lb.insert(END, f"{m.title or m.package_id}｜{m.package_id}｜{m.folder}")
        vs = ttk.Scrollbar(frame, orient=VERTICAL, command=lb.yview)
        lb.configure(yscrollcommand=vs.set)
        lb.pack(side=LEFT, fill=BOTH, expand=True)
        vs.pack(side=RIGHT, fill="y")
        pre = {m.uid for m in pool}
        pre = {u for u in self.bs_custom if u in pre} or {m.uid for m in pool}
        for i, m in enumerate(pool):
            if m.uid in pre:
                lb.selection_set(i)

        def ok():
            sel = [pool[i].uid for i in lb.curselection()]
            self.bs_custom = sel
            self.var_scope.set("custom")
            win.destroy()
            self._bs_refresh_scope()

        btns = ttk.Frame(win, padding=8)
        btns.pack(fill="x")
        ttk.Button(btns, text="确定", command=ok).pack(side=RIGHT)
        ttk.Button(btns, text="取消", command=win.destroy).pack(side=RIGHT, padx=6)

    # -- 会话控制 ----------------------------------------------------------
    def _bs_mods_by_uid(self) -> dict:
        return {m.uid: m for m in self.result.mods} if self.result else {}

    def _bs_set_buttons(self, enabled: bool):
        state = "normal" if enabled else "disabled"
        for b in self.bs_res_btns:
            b.configure(state=state)

    def _bs_autoload(self):
        s = bisect.load(self.bs_path)
        if s is not None:
            self.bs_session = s
            self.var_reception.set(s.symptom_reception)
            self.var_stage.set(journal.STAGE_CN.get(s.symptom_stage, "开始拼点"))
            self._bs_render()
            self._j_render()
            self.var_bs_status.set(
                f"已恢复上次的排查会话（{bisect.progress_text(s)}）。"
                "如需重新开始，点“重置”。")

    def _bs_save(self):
        if self.bs_session is not None:
            try:
                bisect.save(self.bs_session, self.bs_path)
            except OSError:
                pass

    def _bs_start(self):
        if not self.result:
            messagebox.showinfo("需要先扫描", "请先点击“开始扫描”，再开始二分排查。")
            return
        pool = self._bs_candidates()
        if len(pool) < 2:
            messagebox.showwarning(
                "候选太少",
                f"当前范围只有 {len(pool)} 个 mod，无法二分排查。\n"
                "请把范围换成“全部已启用”或自定义勾选更多 mod。")
            return
        stage_key = self._stage_key(self.var_stage.get())
        reception = self.var_reception.get().strip()
        cond_txt = (f"在「{reception}」接待里，做到「{self.var_stage.get()}」这一步"
                    if reception else
                    f"任意接待，做到「{self.var_stage.get()}」这一步")
        rounds = max(1, (len(pool) - 1).bit_length()) + 1
        msg = (f"将对 {len(pool)} 个 mod 开始排查。\n\n"
               f"固定测试条件：{cond_txt}\n"
               "每一轮都必须按这个条件复现，结果才可比。\n\n"
               "每一轮工具会给出\"本轮要禁用的 mod\"清单，"
               "请你在游戏内的 Mod 管理界面照做，"
               "然后按上面的条件复现，回到本页点击结果。\n\n"
               f"若问题是单个 mod 引起，约需 {rounds + 1} 轮；\n"
               "若是两个 mod 组合才触发，约需其 2 倍。\n\n"
               "确认开始？")
        if not messagebox.askyesno("开始二分排查", msg):
            return
        self.bs_session = bisect.new_session(
            [m.uid for m in pool], scope=self.var_scope.get(),
            use_baseline=bool(self.var_baseline.get()),
            symptom_reception=reception, symptom_stage=stage_key)
        self._bs_save()
        self._bs_render()
        self._j_render()

    def _bs_apply(self, result: str):
        if self.bs_session is None:
            return
        if self.bs_session.is_done:
            return
        # 会话的固定测试条件可能被改过，记录下来一并生效
        self.bs_session.symptom_reception = self.var_reception.get().strip()
        self.bs_session.symptom_stage = self._stage_key(self.var_stage.get())
        pending = self.bs_session.pending
        if pending is not None:
            pending.reception = self.bs_session.symptom_reception
            pending.stage = self.bs_session.symptom_stage
            disabled = list(pending.disable)
            rn = pending.n
        else:
            disabled, rn = [], 0

        bisect.apply_result(self.bs_session, result)
        self._bs_save()

        # 自动写进「接待症状档案」
        if result != "unsure":
            self._j_add(journal.Observation(
                reception=self.bs_session.symptom_reception,
                stage=self.bs_session.symptom_stage,
                outcome="frozen" if result == "persists" else "ok",
                disabled=disabled, round_n=rn, source="bisect",
                note=f"二分排查第 {rn} 轮"), save=True)

        self._bs_render()
        s = self.bs_session
        if s.is_done:
            if s.verdict:
                messagebox.showinfo("排查完成", "已定位到元凶，详见本页结论。")
            else:
                messagebox.showwarning("排查结束", s.note or "未能定位到 mod。")

    def _bs_undo(self):
        if self.bs_session is None:
            return
        if bisect.undo_last(self.bs_session):
            self._bs_save()
            self._bs_render()

    def _bs_reset(self):
        if self.bs_session is None:
            return
        if not messagebox.askyesno("重置排查", "将清空所有实验记录并重新开始，确定吗？"):
            return
        bisect.reset(self.bs_session)
        self._bs_save()
        self._bs_render()

    def _bs_copy(self):
        if self.bs_session is None or self.bs_session.pending is None:
            return
        txt = bisect.pending_plain_list(self.bs_session, self._bs_mods_by_uid())
        self.root.clipboard_clear()
        self.root.clipboard_append(txt)
        self.var_bs_status.set("本轮禁用清单已复制到剪贴板。")

    def _bs_export(self):
        if self.bs_session is None:
            messagebox.showinfo("没有排查记录", "请先开始一次排查。")
            return
        md = bisect.render_markdown(self.bs_session, self._bs_mods_by_uid())
        stamp = time.strftime("%Y%m%d-%H%M%S")
        out = REPORT_DIR / f"二分排查记录-{stamp}.md"
        try:
            REPORT_DIR.mkdir(parents=True, exist_ok=True)
            out.write_text(md, encoding="utf-8")
        except OSError:
            messagebox.showerror("导出失败", "无法写入报告目录。")
            return
        self.var_bs_status.set(f"排查记录已导出：{out}")
        if messagebox.askyesno("已导出", f"{out}\n\n是否立即打开？"):
            self._open(out)

    # -- 渲染 --------------------------------------------------------------
    def _bs_render(self):
        s = self.bs_session
        self.bs_btn_undo.configure(state="normal" if (s and s.rounds) else "disabled")
        if s is None:
            self._set_text(self.txt_bs,
                           "还没有排查会话。\n\n"
                           "1. 先点上面的“开始扫描”；\n"
                           "2. 选择排查范围（建议“全部已启用”）；\n"
                           "3. 点“开始新排查”。")
            self._bs_set_buttons(False)
            return

        by = self._bs_mods_by_uid()
        total = len(s.all_candidates)
        L = []
        L.append("=" * 78)
        L.append("  二分排查（每轮只改这一组，其余 mod 全部保持启用）")
        L.append("=" * 78)
        L.append(f"  初始候选        : {total} 个 mod")
        L.append(f"  已找到的元凶    : "
                 + ("、".join(bisect.describe(by, s.found)) if s.found else "（暂无）"))
        L.append(f"  当前待查        : {len(s.find_R)} 个")
        L.append(f"  已执行测试      : {s.tested_rounds} 轮")
        L.append(f"  状态            : {bisect.progress_text(s)}")
        L.append(f"  固定测试条件    : {bisect.symptom_text(s)}")
        L.append("")
        try:
            _sum = journal.summarize(self.journal)
            if _sum.unstable:
                L.append("  ⚠ 症状档案里存在不一致记录（同一接待 + 同一步骤 + 同一组禁用 mod "
                         "出现过两种结果），")
                L.append("    说明症状不稳定，二分排查的结论不可信 —— 建议先对同一组多重测几次。")
                L.append("    详见「接待症状档案」标签页。")
                L.append("")
        except Exception:
            pass

        if s.is_done:
            L.append("-" * 78)
            L.append("  结论")
            L.append("-" * 78)
            if s.verdict:
                L.append("  导致问题的 mod（已通过开关实验确认，去掉任何一个问题都会回来）：")
                L.append("")
                for x in bisect.describe(by, s.verdict):
                    L.append(f"    ★ {x}")
                L.append("")
                L.append("  把它们保持禁用，或等作者修复后再启用。")
            else:
                L.append("  未能定位到具体 mod。")
            if s.note:
                L.append("")
                L.append(f"  {s.note}")
            self._set_text(self.txt_bs, "\n".join(L))
            self._bs_set_buttons(False)
            self._bs_fill_history(s)
            return

        p = s.pending
        if p is not None:
            L.append("-" * 78)
            L.append(f"  本轮：第 {p.n} 轮 · {p.role_cn} —— {p.label}")
            L.append("-" * 78)
            L.append(f"  【请禁用以下 {len(p.disable)} 个 mod】")
            L.append("")
            for x in bisect.describe(by, p.disable):
                L.append(f"    ☐ {x}")
            L.append("")
            L.append("  【其余所有 mod 请保持启用】")
            L.append("")
            L.append("-" * 78)
            L.append("  操作步骤")
            L.append("-" * 78)
            L.append("    1. 打开游戏内的 Mod 管理界面（BaseMod 的 Mod 开关列表）")
            L.append("    2. 按上面的清单取消勾选这些 mod")
            L.append("    3. 进入同一个接待，制造拼点，看是否卡死")
            L.append("    4. 回到本页，点下面三个按钮中的一个")
            L.append("")
            if p.action == "baseline":
                L.append("  ⚠ 基线测试要禁用全部候选 mod。它的作用是先确认"
                         "\"问题确实由 mod 引起\"；")
                L.append("    如果全禁用后依然卡死，说明不是 mod 冲突，"
                         "工具会直接告诉你，省下后面的轮次。")
            elif p.action == "verify":
                L.append("  ⚠ 这一轮要确认：只禁用这些已找到的 mod，"
                         "其余全部恢复启用。")
            L.append("")
        if s.note:
            L.append(f"  提示：{s.note}")
        self._set_text(self.txt_bs, "\n".join(L))
        self._bs_set_buttons(True)
        self._bs_fill_history(s)

    def _bs_fill_history(self, s):
        self.tree_bs.delete(*self.tree_bs.get_children())
        for r in s.rounds:
            self.tree_bs.insert("", END, values=(
                r.n, r.role_cn, len(r.disable), r.result_cn, r.pool_after, r.ts))

    # ------------------------------------------------------- 接待症状档案
    def _build_journal_tab(self, nb):
        tab = ttk.Frame(nb, padding=6)
        nb.add(tab, text="接待症状档案")

        row = ttk.Frame(tab)
        row.pack(fill="x")
        ttk.Label(row, text="接待 / 关卡：").pack(side=LEFT)
        self.cmb_obs_rec = ttk.Combobox(row, textvariable=self.var_obs_reception,
                                        width=18, font=FONT)
        self.cmb_obs_rec.pack(side=LEFT, padx=4)
        ttk.Label(row, text="卡在哪一步：").pack(side=LEFT, padx=(10, 0))
        ttk.Combobox(row, textvariable=self.var_obs_stage, width=12, font=FONT,
                     state="readonly",
                     values=[l for _, l in journal.STAGES]).pack(side=LEFT, padx=4)
        ttk.Label(row, text="结果：").pack(side=LEFT, padx=(10, 0))
        ttk.Combobox(row, textvariable=self.var_obs_outcome, width=8, font=FONT,
                     state="readonly",
                     values=[l for _, l in journal.OUTCOMES]).pack(side=LEFT, padx=4)
        ttk.Label(row, text="备注：").pack(side=LEFT, padx=(10, 0))
        ttk.Entry(row, textvariable=self.var_obs_note, width=24).pack(side=LEFT, padx=4)
        ttk.Button(row, text="记录", command=self._j_add_manual).pack(side=LEFT, padx=6)

        bar = ttk.Frame(tab)
        bar.pack(fill="x", pady=(6, 2))
        ttk.Button(bar, text="删除选中", command=self._j_delete).pack(side=LEFT)
        ttk.Button(bar, text="清空全部", command=self._j_clear).pack(side=LEFT, padx=4)
        ttk.Button(bar, text="导出 Markdown",
                   command=self._j_export).pack(side=LEFT, padx=4)
        ttk.Label(bar, textvariable=self.var_j_status,
                  foreground="#9aa1b4").pack(side=LEFT, padx=10)

        cols = ("ts", "rec", "stage", "outcome", "cnt", "src", "note")
        self.tree_j = ttk.Treeview(tab, columns=cols, show="headings", height=9)
        for c, t, w in (("ts", "时间", 150), ("rec", "接待 / 关卡", 170),
                        ("stage", "阶段", 100), ("outcome", "结果", 70),
                        ("cnt", "当时禁用的 mod", 110), ("src", "来源", 70),
                        ("note", "备注", 220)):
            self.tree_j.heading(c, text=t)
            self.tree_j.column(c, width=w, anchor="w", stretch=(c == "note"))
        self.tree_j.pack(fill=BOTH, expand=False)

        ttk.Label(tab, text="汇总与判断：").pack(anchor="w", pady=(8, 2))
        self.txt_j = Text(tab, height=12, font=FONT_MONO, bg="#0e1015", fg="#c9cee0",
                          relief="flat", wrap="word", padx=10, pady=8)
        self.txt_j.pack(fill=BOTH, expand=True)
        self.txt_j.configure(state="disabled")

    @staticmethod
    def _stage_key(label_or_key: str) -> str:
        for key, label in journal.STAGES:
            if label_or_key == label:
                return key
        return label_or_key or "clash"

    @staticmethod
    def _outcome_key(label_or_key: str) -> str:
        for key, label in journal.OUTCOMES:
            if label_or_key == label:
                return key
        return label_or_key or "frozen"

    def _j_autoload(self):
        try:
            self.journal = journal.Journal.load(self.journal_path)
        except Exception:
            self.journal = journal.Journal()
        self._j_render()

    def _j_add(self, obs, save: bool = False):
        self.journal.add(obs)
        if save:
            self._j_save()
            self._j_render()

    def _j_save(self):
        try:
            self.journal.save(self.journal_path)
        except OSError:
            pass

    def _j_add_manual(self):
        rec = self.var_obs_reception.get().strip()
        if not rec:
            messagebox.showinfo("需要填写接待",
                                "请先填写接待 / 关卡名称，例如接待名或关卡名。")
            return
        self.journal.add(journal.Observation(
            reception=rec,
            stage=self._stage_key(self.var_obs_stage.get()),
            outcome=self._outcome_key(self.var_obs_outcome.get()),
            note=self.var_obs_note.get().strip(), source="manual"))
        self.var_obs_note.set("")
        self._j_save()
        self._j_render()

    def _j_delete(self):
        sel = self.tree_j.selection()
        if not sel:
            return
        for i in sorted((self.tree_j.index(s) for s in sel), reverse=True):
            self.journal.remove(i)
        self._j_save()
        self._j_render()

    def _j_clear(self):
        if not self.journal.observations:
            return
        if messagebox.askyesno("清空档案", "将删除全部记录，确定吗？"):
            self.journal.clear()
            self._j_save()
            self._j_render()

    def _j_export(self):
        md = journal.render_markdown(self.journal, self._bs_mods_by_uid())
        stamp = time.strftime("%Y%m%d-%H%M%S")
        out = REPORT_DIR / f"症状档案-{stamp}.md"
        try:
            REPORT_DIR.mkdir(parents=True, exist_ok=True)
            out.write_text(md, encoding="utf-8")
        except OSError:
            messagebox.showerror("导出失败", "无法写入报告目录。")
            return
        self.var_j_status.set(f"已导出：{out.name}")
        if messagebox.askyesno("已导出", f"{out}\n\n是否立即打开？"):
            self._open(out)

    def _j_render(self):
        j = self.journal
        self.tree_j.delete(*self.tree_j.get_children())
        for o in j.observations:
            self.tree_j.insert("", END, values=(
                o.ts, o.key, o.stage_cn, o.outcome_cn,
                f"{len(o.disabled)} 个" if o.disabled else "无",
                o.source_cn, o.note))
        self._set_text(self.txt_j, journal.render_text_summary(j, self._bs_mods_by_uid()))
        recs = j.receptions()
        if self.bs_session and self.bs_session.symptom_reception:
            r = self.bs_session.symptom_reception
            if r not in recs:
                recs.append(r)
        try:
            self.cmb_obs_rec.configure(values=recs)
            self.cmb_reception.configure(values=recs)
        except Exception:
            pass
        self.var_j_status.set(f"共 {len(j.observations)} 条记录")

    def _on_finding_select(self, _event=None):
        if not self.result:
            return
        sel = self.tree.selection()
        if not sel:
            return
        idx = self.tree.index(sel[0])
        if 0 <= idx < len(self.result.findings):
            self._insert_finding(self.result.findings[idx])

    # ---------------------------------------------------------------- 报告
    def export_report(self):
        if not self.result:
            return
        try:
            path = report_mod.write_report(self.result, REPORT_DIR,
                                           journal=self.journal)
        except Exception:
            messagebox.showerror("导出失败", traceback.format_exc()[-2000:])
            return
        self.report_path = path
        self.var_status.set(f"报告已生成：{path}")
        if messagebox.askyesno("报告已生成", f"{path}\n\n是否立即用浏览器打开？"):
            self._open(path)

    def open_report_dir(self):
        target = self.report_path.parent if self.report_path else REPORT_DIR
        target.mkdir(parents=True, exist_ok=True)
        self._open(target)

    @staticmethod
    def _open(path: Path):
        try:
            os.startfile(str(path))  # type: ignore[attr-defined]
        except Exception:
            subprocess.Popen(["explorer", str(path)])


def main() -> int:
    root = Tk()
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
