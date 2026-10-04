# -*- coding: utf-8 -*-
"""静态扫描：mod 清单、数据 XML 的 ID 索引、程序集与拼点相关类型。"""

from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from .config import (BOOK_FILENAME_HINTS, CATEGORY_CN, CLASH_TYPES,
                     FRAMEWORK_ASSEMBLIES, MANIFEST_NAMES, RISKY_ASSEMBLIES,
                     ROOT_TO_CATEGORY)

RE_ROOT_TAG = re.compile(rb"<([A-Za-z_][A-Za-z0-9_.]*)")
RE_MANIFEST_ID = re.compile(r"<ID>(.*?)</ID>", re.S)
RE_MANIFEST_TITLE = re.compile(r"<Title>(.*?)</Title>", re.S)
RE_MANIFEST_DESC = re.compile(r"<Description>(.*?)</Description>", re.S)
RE_MANIFEST_VER = re.compile(r"<Version>(.*?)</Version>", re.S)
RE_MANIFEST_TAG = re.compile(r"<Tag>(.*?)</Tag>", re.S)

# 数据 XML 的判定上限：超过这个大小仍然解析，但跳过资源型大文件
MAX_XML_BYTES = 32 * 1024 * 1024
MAX_DLL_BYTES = 48 * 1024 * 1024


@dataclass
class DataEntry:
    category: str
    entry_id: str
    name: str
    file: str          # 相对 mod 根目录


@dataclass
class ModInfo:
    uid: str                      # 稳定标识：工坊 ID 或本地目录名
    source: str                   # "workshop" / "local"
    folder: str
    path: Path
    package_id: str = ""
    title: str = ""
    description: str = ""
    version: str = ""
    tag: str = ""
    manifest: str = ""
    manifest_root: str = ""
    is_framework: bool = False
    is_sample: bool = False
    # None = 未知（读不到 ModSetting.save / 日志），True/False = 当前是否启用
    enabled: Optional[bool] = None
    assemblies: Dict[str, str] = field(default_factory=dict)   # 小写名 -> 相对路径
    clash_types: Dict[str, str] = field(default_factory=dict)  # 类型名 -> 相对 dll 路径
    entries: List[DataEntry] = field(default_factory=list)
    xml_by_category: Dict[str, List[str]] = field(default_factory=dict)
    file_count: int = 0
    total_bytes: int = 0
    issues: List[str] = field(default_factory=list)

    @property
    def display(self) -> str:
        t = self.title or self.package_id or self.folder
        return f"{t}（{self.folder}）" if self.title and self.title != self.folder else t

    def ids_for(self, category: str) -> Dict[str, List[Tuple[str, str]]]:
        """category -> {entry_id: [(name, file), ...]}"""
        out: Dict[str, List[Tuple[str, str]]] = {}
        for e in self.entries:
            if e.category == category:
                out.setdefault(e.entry_id, []).append((e.name, e.file))
        return out


def _read_head(path: Path, n: int = 4096) -> bytes:
    try:
        with open(path, "rb") as f:
            return f.read(n)
    except OSError:
        return b""


def _root_tag(path: Path) -> str:
    head = _read_head(path)
    if not head:
        return ""
    m = RE_ROOT_TAG.search(head)
    return m.group(1).decode("ascii", "replace") if m else ""


def _parse_manifest(path: Path) -> dict:
    """解析 StageModInfo.xml / ModInfo.Xml，返回 Workshop 字段。"""
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return {}
    # 人脸/发型类 ModInfo.Xml 没有 Workshop 段，直接跳过
    if "<Workshop>" not in text and "<FaceInfo>" in text:
        return {"__face__": True}

    def grab(rx):
        m = rx.search(text)
        if not m:
            return ""
        v = m.group(1).strip()
        v = re.sub(r"<[^>]+>", "", v)
        return v

    return {
        "package_id": grab(RE_MANIFEST_ID),
        "title": grab(RE_MANIFEST_TITLE),
        "description": grab(RE_MANIFEST_DESC),
        "version": grab(RE_MANIFEST_VER),
        "tag": grab(RE_MANIFEST_TAG),
    }


def _extract_entries(path: Path) -> List[Tuple[str, str, str]]:
    """返回 [(entry_id, name, )]，取根节点下带 ID 属性的直接子元素。"""
    out: List[Tuple[str, str, str]] = []
    try:
        tree = ET.parse(str(path))
    except (ET.ParseError, OSError, ValueError):
        return out
    root = tree.getroot()
    if root is None:
        return out
    for child in root:
        if not isinstance(child.tag, str):
            continue
        eid = child.get("ID")
        if eid is None:
            continue
        name = child.get("Name")
        if not name:
            name = (child.findtext("Name") or "").strip()
        if not name:
            name = str(child.tag)
        out.append((str(eid).strip(), name.strip()))
    return out


def _category_for(root_tag: str, filename: str) -> Optional[str]:
    cat = ROOT_TO_CATEGORY.get(root_tag)
    if cat is None:
        return None
    if cat == "book":
        for hint, real in BOOK_FILENAME_HINTS:
            if hint.lower() in filename.lower():
                return real
        return "book"
    return cat


def scan_mod_dir(path: Path, source: str, uid: Optional[str] = None,
                 progress: Optional[Callable[[str], None]] = None) -> ModInfo:
    mod = ModInfo(uid=uid or path.name, source=source, folder=path.name, path=path)

    # --- 清单 ---
    for name in MANIFEST_NAMES:
        f = path / name
        if f.is_file():
            mod.manifest = name
            info = _parse_manifest(f)
            if info.get("__face__"):
                mod.issues.append("这是人脸/发型 ModInfo.Xml，不是标准 mod 清单")
                break
            mod.manifest_root = "NormalInvitation"
            mod.package_id = info.get("package_id", "")
            mod.title = info.get("title", "")
            mod.description = info.get("description", "")
            mod.version = info.get("version", "")
            mod.tag = info.get("tag", "")
            break

    # --- 遍历文件 ---
    xml_paths: List[Tuple[Path, str]] = []
    dll_paths: List[Path] = []
    for dirpath, dirnames, filenames in os.walk(path):
        # 跳过明显的无关目录，加速
        dirnames[:] = [d for d in dirnames if d.lower() not in
                       ("__pycache__", "samplecode")]
        for fn in filenames:
            fp = Path(dirpath) / fn
            try:
                st = fp.stat()
            except OSError:
                continue
            mod.file_count += 1
            mod.total_bytes += st.st_size
            low = fn.lower()
            if low.endswith(".dll"):
                dll_paths.append(fp)
            elif low.endswith(".xml"):
                if st.st_size <= MAX_XML_BYTES:
                    xml_paths.append((fp, ""))
                else:
                    xml_paths.append((fp, "too_large"))

    # --- XML 分类与 ID 提取 ---
    for fp, flag in xml_paths:
        rel = os.path.relpath(fp, path)
        if flag == "too_large":
            continue
        rt = _root_tag(fp)
        cat = _category_for(rt, fp.name)
        if cat is None:
            continue
        mod.xml_by_category.setdefault(cat, []).append(rel)
        entries = _extract_entries(fp)
        if not entries:
            continue
        for eid, name in entries:
            mod.entries.append(DataEntry(category=cat, entry_id=eid,
                                         name=name, file=rel))

    # --- 程序集 ---
    clash_pat = [(t, t.encode("ascii")) for t in CLASH_TYPES]
    for fp in dll_paths:
        rel = os.path.relpath(fp, path)
        low = fp.stem.lower()
        mod.assemblies[low] = rel
        if "1frameworkassemblies" in rel.lower() or low in (
                "1frameworkloader", "1frameworkpriorityinjector",
                "1frameworkpriorityloader"):
            mod.is_framework = True
        try:
            size = fp.stat().st_size
        except OSError:
            continue
        if size > MAX_DLL_BYTES:
            continue
        try:
            blob = fp.read_bytes()
        except OSError:
            continue
        for tname, pat in clash_pat:
            if tname in mod.clash_types:
                continue
            if pat in blob:
                mod.clash_types[tname] = rel

    if not mod.manifest:
        mod.issues.append("未找到 mod 清单（StageModInfo.xml / ModInfo.Xml）")
    if mod.folder.startswith("ModSample") or mod.package_id == "projmoon.sample":
        mod.is_sample = True
        mod.is_framework = False
    if mod.package_id and ("framework" in mod.package_id.lower()
                           or mod.package_id.startswith("1Framework")):
        mod.is_framework = True

    if progress:
        progress(f"已扫描 {mod.folder}")
    return mod


def scan_all(local_mods_dir: Optional[Path], workshop_dir: Optional[Path],
             progress: Optional[Callable[[str], None]] = None) -> List[ModInfo]:
    mods: List[ModInfo] = []
    if workshop_dir and workshop_dir.is_dir():
        try:
            items = sorted([d for d in workshop_dir.iterdir() if d.is_dir()],
                           key=lambda p: p.name)
        except OSError:
            items = []
        for d in items:
            mods.append(scan_mod_dir(d, "workshop", uid=d.name, progress=progress))

    if local_mods_dir and local_mods_dir.is_dir():
        try:
            items = sorted([d for d in local_mods_dir.iterdir() if d.is_dir()],
                           key=lambda p: p.name)
        except OSError:
            items = []
        for d in items:
            mods.append(scan_mod_dir(d, "local", uid=d.name, progress=progress))
    return mods


def assembly_duplicates(mods: Iterable[ModInfo]) -> Dict[str, List[ModInfo]]:
    """程序集名 -> 提供该程序集的 mod 列表（仅 >1 的）。"""
    index: Dict[str, List[ModInfo]] = {}
    for m in mods:
        for low in m.assemblies:
            index.setdefault(low, []).append(m)
    return {k: v for k, v in index.items() if len(v) > 1}


def classify_assembly(low_name: str) -> str:
    if low_name in RISKY_ASSEMBLIES:
        return "risky"
    if low_name in FRAMEWORK_ASSEMBLIES:
        return "framework"
    return "content"
