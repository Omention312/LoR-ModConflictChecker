# -*- coding: utf-8 -*-
"""从 BaseMod 的 ``ModSetting.save`` 里读取玩家的 mod 启用顺序。

`ModSetting.save` 是 .NET ``BinaryFormatter`` 序列化的对象图（``Sd.Di`` /
``Sd.Kv``），格式私有、无官方文档。完整复刻 BinaryFormatter 读取器既复杂又
脆弱，因此这里只做一件事：

    扫描 ``BinaryObjectString`` (记录号 0x06) 记录，按字节顺序取出所有字符串，
    再交给调用方用"已扫描到的 mod 包 ID"做交叉校验。

优点：
  * 只依赖 `0x06 + ObjectId + 7bit 长度 + UTF-8` 这一个稳定特征；
  * 交叉校验之后，只有确实对应某个已安装 mod 的包 ID 才会被采纳，
    因此即使扫出噪声也不会污染结果；
  * 匹配数量太少就返回 ``None``，调用方自动降级到 Player.log。

**本模块只读**，不提供任何写回函数：BinaryFormatter 写回一旦字节级偏差，
游戏可能直接丢掉整份 mod 配置，风险远大于收益。工具只负责告诉用户
"请在游戏内的 Mod 管理界面取消勾选这些 mod"。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set

REC_OBJECT_STRING = 0x06
MAX_STRING = 200
MAX_OBJECT_ID = 1 << 24     # ObjectId 只会是很小的整数
RE_PLAUSIBLE = re.compile(r"^[\w.\-+ ]{1,120}$", re.UNICODE)


def default_path() -> Optional[Path]:
    up = os.environ.get("USERPROFILE")
    if not up:
        return None
    p = (Path(up) / "AppData" / "LocalLow" / "Project Moon" /
         "LibraryOfRuina" / "ModSetting.save")
    return p if p.is_file() else None


def extract_strings(data: bytes) -> List[str]:
    """按出现顺序取出疑似 BinaryObjectString 的内容。"""
    out: List[str] = []
    i = 0
    n = len(data)
    while i < n - 6:
        if data[i] != REC_OBJECT_STRING:
            i += 1
            continue
        oid = int.from_bytes(data[i + 1:i + 5], "little")
        if oid <= 0 or oid > MAX_OBJECT_ID:
            i += 1
            continue
        j = i + 5
        length = 0
        shift = 0
        terminated = False
        for _ in range(5):
            if j >= n:
                break
            b = data[j]
            j += 1
            length |= (b & 0x7F) << shift
            shift += 7
            if not (b & 0x80):
                terminated = True
                break
        if not terminated or not (0 < length <= MAX_STRING) or j + length > n:
            i += 1
            continue
        try:
            s = data[j:j + length].decode("utf-8")
        except UnicodeDecodeError:
            i += 1
            continue
        if not RE_PLAUSIBLE.match(s):
            i += 1
            continue
        out.append(s)
        i = j + length
    return out


@dataclass
class ModSetting:
    path: Optional[Path] = None
    active_ids: List[str] = field(default_factory=list)   # 已交叉校验的包 ID（保序）
    all_strings: List[str] = field(default_factory=list)
    unknown_ids: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.active_ids)


def load(known_package_ids: Sequence[str],
         path: Optional[Path] = None,
         min_matches: int = 3) -> Optional[ModSetting]:
    """读取并交叉校验。

    ``known_package_ids``：扫描阶段拿到的所有 mod 包 ID。
    只有出现在其中的字符串才会被当成"已启用的 mod"。
    """
    path = Path(path) if path else default_path()
    if not path or not path.is_file():
        return None
    try:
        data = path.read_bytes()
    except OSError:
        return None

    strings = extract_strings(data)
    if not strings:
        return None

    known: Dict[str, str] = {}
    for pid in known_package_ids:
        if pid:
            known.setdefault(pid.strip().lower(), pid)

    ms = ModSetting(path=path, all_strings=strings)
    seen: Set[str] = set()
    for s in strings:
        key = s.strip().lower()
        real = known.get(key)
        if real and key not in seen:
            seen.add(key)
            ms.active_ids.append(real)
            continue
        # 记录"看起来像包 ID 但没匹配上"的，便于诊断
        if ("." in s or "_" in s) and 3 <= len(s) <= 80 \
                and not s.lower().startswith(("system", "unity", "assembly")):
            if s not in ms.unknown_ids:
                ms.unknown_ids.append(s)

    if len(ms.active_ids) < min_matches:
        return None
    return ms
