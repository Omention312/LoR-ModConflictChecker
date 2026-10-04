# -*- coding: utf-8 -*-
"""自动定位 Steam / 游戏本体 / 创意工坊 / 日志。"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

try:                     # Windows 专有；其它平台让模块仍可被导入
    import winreg
except ImportError:      # pragma: no cover - 非 Windows
    winreg = None        # type: ignore[assignment]

from .config import APP_ID

RE_VDF_PATH = re.compile(r'"path"\s*"([^"]+)"')
RE_VDF_KV = re.compile(r'"([^"]+)"\s*"([^"]*)"')


# ---------------------------------------------------------------------------
# Steam
# ---------------------------------------------------------------------------
def find_steam_root() -> Optional[Path]:
    """从注册表 + 常见安装位置找 Steam 根目录。

    非 Windows 平台没有注册表，直接跳过那一步，只用环境变量和常见目录探测
    —— 这样 ``lorcheck`` 在 CI 的 Linux 任务里也能正常导入。
    """
    candidates: List[Path] = []

    if winreg is not None:
        for hive, key in (
            (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam"),
        ):
            try:
                with winreg.OpenKey(hive, key) as k:
                    for value_name in ("SteamPath", "InstallPath"):
                        try:
                            v, _ = winreg.QueryValueEx(k, value_name)
                        except OSError:
                            continue
                        if v:
                            candidates.append(Path(str(v)))
            except OSError:
                continue

    env = os.environ.get("STEAM_PATH") or os.environ.get("STEAM_DIR")
    if env:
        candidates.append(Path(env))

    for drive in "CDEFGHIJ":
        for sub in (r"Steam", r"steam", r"Program Files (x86)\Steam",
                    r"Program Files\Steam", r"Games\Steam", r"SteamLibrary"):
            candidates.append(Path(f"{drive}:\\{sub}"))

    for c in candidates:
        try:
            if c.is_dir() and (c / "steamapps").is_dir():
                return c
        except OSError:
            continue
    return None


def find_steam_libraries(steam_root: Optional[Path]) -> List[Path]:
    """解析 libraryfolders.vdf，返回所有 Steam 库根目录。"""
    libs: List[Path] = []

    def add(p: Path) -> None:
        try:
            if p.is_dir() and p not in libs:
                libs.append(p)
        except OSError:
            pass

    if steam_root:
        add(steam_root)
        vdf = steam_root / "steamapps" / "libraryfolders.vdf"
        if vdf.is_file():
            try:
                text = vdf.read_text(encoding="utf-8", errors="replace")
                for m in RE_VDF_PATH.finditer(text):
                    add(Path(m.group(1).replace("\\\\", "\\")))
            except OSError:
                pass

    # 兜底：扫各盘常见库目录里的 libraryfolders.vdf / appmanifest
    if not libs:
        for drive in "CDEFGHIJ":
            for sub in (r"SteamLibrary", r"Steam", r"steam", r"Games\SteamLibrary"):
                p = Path(f"{drive}:\\{sub}")
                if (p / "steamapps").is_dir():
                    add(p)
    return libs


def find_game_dir(libs: List[Path], app_id: str = APP_ID) -> Optional[Path]:
    """按 appmanifest 找游戏安装目录。"""
    for lib in libs:
        acf = lib / "steamapps" / f"appmanifest_{app_id}.acf"
        if not acf.is_file():
            continue
        try:
            text = acf.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        m = re.search(r'"installdir"\s*"([^"]+)"', text)
        if not m:
            continue
        game = lib / "steamapps" / "common" / m.group(1)
        if game.is_dir():
            return game
    return None


def find_workshop_dir(libs: List[Path], app_id: str = APP_ID) -> Optional[Path]:
    for lib in libs:
        d = lib / "steamapps" / "workshop" / "content" / app_id
        if d.is_dir():
            return d
    return None


def read_appworkshop(libs: List[Path], app_id: str = APP_ID) -> dict:
    """读取 appworkshop_<appid>.acf，拿到已下载的工坊项 ID -> timeupdated。"""
    result: dict = {}
    for lib in libs:
        acf = lib / "steamapps" / "workshop" / f"appworkshop_{app_id}.acf"
        if not acf.is_file():
            continue
        try:
            text = acf.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # "WorkshopItemsInstalled" { "2115481672" { "size" "..." "timeupdated" "..." } }
        blocks = re.findall(
            r'"(\d{6,})"\s*\{(.*?)\n\t*\}', text, re.S)
        for wid, body in blocks:
            kv = dict(RE_VDF_KV.findall(body))
            result.setdefault(wid, kv)
    return result


# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------
def find_player_logs() -> List[Path]:
    found: List[Path] = []
    local = os.environ.get("USERPROFILE")
    roots = []
    if local:
        roots.append(Path(local) / "AppData" / "LocalLow")
    for root in roots:
        for sub in ("Project Moon/LibraryOfRuina", "ProjectMoon/LibraryOfRuina",
                    "Project Moon/Library Of Ruina", "ProjectMoon/Library Of Ruina"):
            d = root / sub
            for name in ("Player.log", "Player-prev.log"):
                p = d / name
                if p.is_file():
                    found.append(p)
    # 退而求其次：全盘搜 LocalLow 下的 LibraryOfRuina
    if not found and roots:
        for root in roots:
            if not root.is_dir():
                continue
            try:
                for d in root.iterdir():
                    if not d.is_dir():
                        continue
                    for sub in d.iterdir():
                        if sub.is_dir() and "ruina" in sub.name.lower():
                            for name in ("Player.log", "Player-prev.log"):
                                p = sub / name
                                if p.is_file():
                                    found.append(p)
            except OSError:
                pass
    # 去重并保持顺序
    out: List[Path] = []
    for p in found:
        if p not in out:
            out.append(p)
    return out


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------
@dataclass
class Paths:
    steam_root: Optional[Path] = None
    libraries: List[Path] = field(default_factory=list)
    game_dir: Optional[Path] = None
    workshop_dir: Optional[Path] = None
    local_mods_dir: Optional[Path] = None
    basemod_dir: Optional[Path] = None
    player_logs: List[Path] = field(default_factory=list)
    workshop_meta: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "steam_root": str(self.steam_root) if self.steam_root else "",
            "libraries": [str(p) for p in self.libraries],
            "game_dir": str(self.game_dir) if self.game_dir else "",
            "workshop_dir": str(self.workshop_dir) if self.workshop_dir else "",
            "local_mods_dir": str(self.local_mods_dir) if self.local_mods_dir else "",
            "basemod_dir": str(self.basemod_dir) if self.basemod_dir else "",
            "player_logs": [str(p) for p in self.player_logs],
        }


def autodetect(game_dir: Optional[str] = None,
               workshop_dir: Optional[str] = None,
               log_path: Optional[str] = None) -> Paths:
    """自动定位；传入的参数优先（用于 GUI 手动指定）。"""
    p = Paths()

    p.steam_root = find_steam_root()
    p.libraries = find_steam_libraries(p.steam_root)
    p.workshop_meta = read_appworkshop(p.libraries)

    if game_dir:
        g = Path(game_dir)
        if g.is_dir():
            p.game_dir = g
    if p.game_dir is None:
        p.game_dir = find_game_dir(p.libraries)

    # 游戏目录本身也是一个"库"（用于找工坊目录）
    libs = list(p.libraries)
    if p.game_dir is not None:
        # <lib>\steamapps\common\<game> -> <lib>
        try:
            cand = p.game_dir.parents[1]
            if cand not in libs:
                libs.insert(0, cand)
        except IndexError:
            pass

    if workshop_dir:
        w = Path(workshop_dir)
        if w.is_dir():
            p.workshop_dir = w
    if p.workshop_dir is None:
        p.workshop_dir = find_workshop_dir(libs)

    if p.game_dir is not None:
        data = p.game_dir / "LibraryOfRuina_Data"
        p.local_mods_dir = data / "Mods"
        bm = data / "Managed" / "BaseMod"
        if bm.is_dir():
            p.basemod_dir = bm

    p.player_logs = find_player_logs()
    if log_path:
        lp = Path(log_path)
        if lp.is_file():
            p.player_logs = [lp] + [q for q in p.player_logs if q != lp]

    return p
