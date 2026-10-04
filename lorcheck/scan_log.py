# -*- coding: utf-8 -*-
"""解析 Unity Player.log，把运行时异常归因到具体 mod。"""

from __future__ import annotations

import re
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .config import (RE_ALREADY_EXISTS, RE_DUP_ASSEMBLY, RE_EXCEPTION_LINE,
                     RE_LOAD_PATH, RE_MISSING_PATH, RE_REPEAT_WAIT,
                     RE_SAME_KEY, RE_WORKSHOP_PATH)

RE_LOCAL_MOD_PATH = re.compile(
    r"[\\/]LibraryOfRuina_Data[\\/]Mods[\\/](?P<name>[^\\/\r\n]+)")
RE_ANY_PATH = re.compile(r"[A-Za-z]:\\[^\s\"']+")
RE_CALL_MOD_BEFORE = re.compile(
    r"LoA\s*::\s*Call Mod Before\s*:\s*(?P<type>[^/]+?)\s*//\s*(?P<path>.+?)\s*$")
RE_BATTLE_TAIL = ("MoveIconTarget", "activeVoice", "이펙트 발생", "LoA :: 이펙트",
                  "deck not found", "activeVoice2", "Battle Unit", "Clash")

# LoA 每帧轮询一次初始化器；绝大多数初始化器下一帧就返回（Repeat Wait : 1）。
# 只有累计轮询次数达到下面的阈值，才算"卡死不返回"。
MIN_STALL_WAIT = 3
# 初始化器最终返回了、但耗时超过这个秒数 => "慢到看起来像卡死"
MIN_SLOW_SECONDS = 10.0

MAX_LOG_BYTES = 64 * 1024 * 1024


@dataclass
class LogException:
    type: str
    message: str
    line_no: int
    stack: List[str] = field(default_factory=list)
    mod_paths: List[str] = field(default_factory=list)
    folder_ids: List[str] = field(default_factory=list)
    is_crash_upload: bool = False


@dataclass
class LogReport:
    path: Optional[Path] = None
    exists: bool = False
    size: int = 0
    mtime: float = 0.0
    total_lines: int = 0
    parse_truncated: bool = False

    same_key: List[dict] = field(default_factory=list)          # 重复 LorId
    already_exists: Dict[str, int] = field(default_factory=dict)  # 关键字重复注册
    dup_assemblies: Dict[str, int] = field(default_factory=dict)  # 重复程序集名
    dup_assembly_mods: Dict[str, List[str]] = field(default_factory=dict)
    missing_paths: List[str] = field(default_factory=list)
    exceptions: List[LogException] = field(default_factory=list)
    mod_paths: Dict[str, List[str]] = field(default_factory=dict)  # folder -> dll/json paths
    deck_not_found: List[str] = field(default_factory=list)
    repeat_wait_max: int = 0
    initializer_stall: bool = False
    progress_stall: Optional[str] = None
    crash_report_marker: bool = False
    tail_repeat: Tuple[str, int] = ("", 0)
    tail_is_battle: bool = False
    tail_battle_markers: int = 0
    load_complete: bool = False
    markers: List[Tuple[int, str]] = field(default_factory=list)

    # 卡住时正在等待的 mod 初始化器（LoA DataLoader）；记录每一次，最后生效的是
    # 崩溃前那一次
    stall_owner_type: str = ""
    stall_owner_dll: str = ""
    stall_owner_line: int = 0
    stall_owner_folders: List[str] = field(default_factory=list)
    stall_seconds: float = 0.0
    stall_progress: float = 0.0
    stall_owner_wait: int = 0
    # 初始化很慢但最终返回了（不是死锁）
    slow_init_seconds: float = 0.0
    slow_owner_type: str = ""
    slow_owner_dll: str = ""
    slow_owner_line: int = 0
    stall_events: List[Tuple[int, str, str]] = field(default_factory=list)
    # (type, dll, line) -> {"wait": 最大轮询次数, "duration": 最长等待秒数}
    stall_candidates: Dict[Tuple[str, str, int], Dict[str, float]] = field(
        default_factory=dict)

    @property
    def has_fatal(self) -> bool:
        return bool(self.same_key) or self.initializer_stall or self.tail_repeat[1] >= 8

def _folder_of(path: str) -> List[str]:
    if not isinstance(path, str):
        return []
    out: List[str] = []
    m = re.search(RE_WORKSHOP_PATH, path)
    if m:
        out.append(m.group("id"))
    m2 = RE_LOCAL_MOD_PATH.search(path)
    if m2:
        out.append(m2.group("name"))
    return out


def _note_stall(rep: "LogReport", last_call_mod: Tuple[str, str, int],
                wait: int = 0, duration: float = 0.0) -> None:
    """登记"DataLoader 正在等哪个 mod 初始化器"以及等了多久。

    LoA 每帧轮询一次，绝大多数初始化器只会等到 Repeat Wait : 1 就返回（正常）。
    真正卡死的那个会累积到很大的轮询次数，所以按轮询次数排序取最严重的一次。
    """
    if not last_call_mod[0]:
        return
    c = rep.stall_candidates.setdefault(last_call_mod, {"wait": 0, "duration": 0.0})
    c["wait"] = max(c["wait"], float(wait))
    c["duration"] = max(c["duration"], float(duration))


def _finalize_stalls(rep: "LogReport") -> None:
    # 只有"轮询了很多次都没返回"才算真正卡死；单纯的 Duration 很长说明它最终
    # 还是返回了，属于"慢初始化"，要分开报。
    rep.initializer_stall = rep.repeat_wait_max >= MIN_STALL_WAIT
    if not rep.stall_candidates:
        return
    items = list(rep.stall_candidates.items())
    (wt, wdll, wline), winfo = max(
        items, key=lambda kv: (kv[1]["wait"], kv[1]["duration"]))
    (dt, ddll, dline), dinfo = max(
        items, key=lambda kv: (kv[1]["duration"], kv[1]["wait"]))
    rep.stall_events = [(k[2], k[0], k[1]) for k, v in sorted(
        items, key=lambda kv: -kv[1]["wait"])]

    if rep.initializer_stall:
        rep.stall_owner_type = wt
        rep.stall_owner_dll = wdll
        rep.stall_owner_line = wline
        rep.stall_owner_folders = _folder_of(wdll)
        rep.stall_owner_wait = int(winfo["wait"])
        if winfo["duration"]:
            rep.stall_seconds = winfo["duration"]
        if rep.stall_progress:
            rep.progress_stall = (
                f"LoA DataLoader 卡在 {rep.stall_progress:.4f}% 长达 "
                f"{rep.stall_seconds:.0f} 秒，等待 {rep.stall_owner_type} 返回"
                f"（轮询 {rep.stall_owner_wait} 次）")
        return

    # 没有死锁；但初始化超过阈值就单独记录
    if dinfo["duration"] >= MIN_SLOW_SECONDS:
        rep.slow_owner_type = dt
        rep.slow_owner_dll = ddll
        rep.slow_owner_line = dline
        rep.slow_init_seconds = dinfo["duration"]
        rep.progress_stall = (
            f"加载进度长时间停在 {rep.stall_progress:.4f}%，"
            f"等待 {dt} 返回，耗时 {rep.slow_init_seconds:.0f} 秒（最终完成）")
    rep.stall_events = [(k[2], k[0], k[1]) for k, v in sorted(
        rep.stall_candidates.items(), key=lambda kv: -kv[1]["wait"])]


def analyse_log(path: Optional[Path], tail_lines: int = 400,
                progress=None) -> LogReport:
    rep = LogReport()
    if path is None or not Path(path).is_file():
        return rep
    path = Path(path)
    rep.path = path
    rep.exists = True
    try:
        st = path.stat()
        rep.size = st.st_size
        rep.mtime = st.st_mtime
    except OSError:
        pass

    if progress:
        progress(f"读取日志 {path.name}（{rep.size/1048576:.1f} MB）")

    try:
        raw = path.read_bytes()
    except OSError:
        return rep
    if len(raw) > MAX_LOG_BYTES:
        raw = raw[-MAX_LOG_BYTES:]
        rep.parse_truncated = True
    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()
    rep.total_lines = len(lines)

    pending_exc: Optional[LogException] = None
    last_load_path: str = ""
    last_call_mod: Tuple[str, str, int] = ("", "", 0)
    crash_upload = False

    def flush_exc():
        nonlocal pending_exc
        if pending_exc is not None:
            rep.exceptions.append(pending_exc)
            pending_exc = None

    for i, line in enumerate(lines, 1):
        s = line.strip()
        if not s:
            continue

        if "Uploading Crash Report" in s:
            crash_upload = True
            rep.crash_report_marker = True
            continue

        # ---- 重复 LorId ----
        m = re.search(RE_SAME_KEY, s)
        if m:
            key = m.group("key")
            pkg, _, eid = key.partition(":")
            rep.same_key.append({
                "key": key, "package_id": pkg, "entry_id": eid,
                "line_no": i, "crash_upload": crash_upload,
            })
            continue

        # ---- 重复程序集名 ----
        m = re.search(RE_DUP_ASSEMBLY, s)
        if m:
            name = m.group("name").strip().rstrip(".")
            if name and not name.startswith("("):
                rep.dup_assemblies[name] = rep.dup_assemblies.get(name, 0) + 1
                if last_load_path:
                    for fid in _folder_of(last_load_path):
                        bucket = rep.dup_assembly_mods.setdefault(name, [])
                        if fid not in bucket:
                            bucket.append(fid)
            continue

        # ---- 关键字重复注册 ----
        m = re.search(RE_ALREADY_EXISTS, s)
        if m:
            k = m.group("key").strip()
            rep.already_exists[k] = rep.already_exists.get(k, 0) + 1
            continue

        # ---- 程序集加载路径 ----
        m = re.search(RE_LOAD_PATH, s)
        if m:
            last_load_path = m.group("path").strip()
            for fid in _folder_of(last_load_path):
                rep.mod_paths.setdefault(fid, [])
                if last_load_path not in rep.mod_paths[fid]:
                    rep.mod_paths[fid].append(last_load_path)
            continue

        # ---- 缺失文件 ----
        m = re.search(RE_MISSING_PATH, s)
        if m:
            p = m.group("path").strip()
            if p and p not in rep.missing_paths:
                rep.missing_paths.append(p)

        # ---- 卡死特征 ----
        m = re.search(RE_REPEAT_WAIT, s)
        if m:
            try:
                n = int(m.group("n"))
                if n > rep.repeat_wait_max:
                    rep.repeat_wait_max = n
                _note_stall(rep, last_call_mod, wait=n)
            except ValueError:
                pass
        if "Call Initializer Not Completed" in s:
            _note_stall(rep, last_call_mod)
        if "Maybe CallInitializer Complete Wait" in s:
            dur = 0.0
            msec = re.search(r"Duration\s*:\s*([\d.]+)s", s)
            if msec:
                try:
                    dur = float(msec.group(1))
                    rep.stall_seconds = max(rep.stall_seconds, dur)
                except ValueError:
                    pass
            _note_stall(rep, last_call_mod, duration=dur)
            mpct = re.search(r"Current Progress\s*:\s*([\d.]+)%", s)
            if mpct:
                try:
                    rep.stall_progress = max(rep.stall_progress, float(mpct.group(1)))
                except ValueError:
                    pass
        if "Load Complete" in s:
            rep.load_complete = True
        if "deck not found" in s:
            v = s.split(":", 1)[-1].strip()
            if v and v not in rep.deck_not_found:
                rep.deck_not_found.append(v)

        # ---- 记录 LoA 正在调用的 mod 初始化器（卡住时的归属线索）----
        m = RE_CALL_MOD_BEFORE.search(s)
        if m:
            last_call_mod = (m.group("type").strip(), m.group("path").strip(), i)

        # ---- 异常块 ----
        m = re.match(RE_EXCEPTION_LINE, s)
        if m and "Exception" in m.group("type"):
            flush_exc()
            msg = m.group("msg")[:400]
            exc = LogException(type=m.group("type"), message=msg, line_no=i,
                               is_crash_upload=crash_upload)
            # 异常消息里通常直接带着出错的资源路径，用它做归因
            for p in RE_ANY_PATH.findall(msg):
                if p not in exc.mod_paths:
                    exc.mod_paths.append(p)
                for fid in _folder_of(p):
                    if fid not in exc.folder_ids:
                        exc.folder_ids.append(fid)
            pending_exc = exc
            continue
        if pending_exc is not None:
            if s.startswith("at ") or s.startswith("(") or " in " in s[:80]:
                if len(pending_exc.stack) < 60:
                    pending_exc.stack.append(s)
                for p in RE_ANY_PATH.findall(s):
                    for fid in _folder_of(p):
                        if fid not in pending_exc.folder_ids:
                            pending_exc.folder_ids.append(fid)
                    if p not in pending_exc.mod_paths:
                        pending_exc.mod_paths.append(p)
                continue
            if s.startswith("(Filename:") or s.startswith("UnityEngine."):
                continue
            flush_exc()
    flush_exc()
    _finalize_stalls(rep)

    # ---- 尾部重复行（卡死循环特征）----
    # 注意：Unity 在每个 Debug.Log 后面都会跟一行 "(Filename: ...)"，
    # 必须剔除，否则永远检测不出连续重复。
    tail = [l.strip() for l in lines[-tail_lines:]
            if l.strip() and not l.lstrip().startswith("(Filename:")]
    if tail:
        best_line, best_run = "", 0
        run_line, run = tail[0], 1
        for prev, cur in zip(tail, tail[1:]):
            if cur == prev:
                run += 1
            else:
                if run > best_run:
                    best_run, best_line = run, run_line
                run_line, run = cur, 1
        if run > best_run:
            best_run, best_line = run, run_line
        rep.tail_repeat = (best_line, best_run)

    recent = tail[-60:]
    rep.tail_battle_markers = sum(1 for l in recent
                                  if any(m in l for m in RE_BATTLE_TAIL))
    rep.tail_is_battle = any(m in rep.tail_repeat[0] for m in RE_BATTLE_TAIL)

    # 只保留有信息量的异常（过滤 Unity 引擎自身噪声）
    noisy = ("UnityEngine.DebugLogHandler", "UnityEngine.Logger")
    cleaned: List[LogException] = []
    for e in rep.exceptions:
        blob = e.type + " " + e.message
        if any(n in blob for n in noisy) and not e.stack:
            continue
        cleaned.append(e)
    rep.exceptions = cleaned
    return rep


def tail_lines(path: Optional[Path], n: int = 80) -> List[str]:
    if path is None or not Path(path).is_file():
        return []
    try:
        raw = Path(path).read_bytes()
    except OSError:
        return []
    text = raw.decode("utf-8", errors="replace")
    return text.splitlines()[-n:]
