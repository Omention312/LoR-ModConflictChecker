# -*- coding: utf-8 -*-
"""可复现的发布打包脚本。

用法：
    runtime\\python.exe 打包发布.py            # 打包当前版本
    runtime\\python.exe 打包发布.py --no-zip   # 只生成目录，不压缩

产出：
    release\\LoRModConflictChecker-v<版本>\\        发布目录
    release\\LoRModConflictChecker-v<版本>.zip      分发包

同时生成「版本快照记录」：
    <发布目录>\\发布说明.md      人读的说明 + 完整文件清单 + SHA256
    <发布目录>\\版本快照.json    机器可读的版本快照
    <发布目录>\\SHA256SUMS.txt   校验和

安全约定（避免把打包者本机信息带出去）：
  * 排除 报告\\ 下的全部内容（里面有扫描结果与绝对路径）
  * 排除 __pycache__ / *.pyc（.pyc 会记录源码的绝对路径）
  * 排除 release\\ 自身
  * 打包结束后会再扫一遍产物，发现疑似本机路径就报错退出
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP_NAME = "LoRModConflictChecker"

# .git      —— 版本库内部数据，绝不能进发布包
# .github   —— CI 配置，只对仓库有意义
# runtime   —— 注意：runtime\ 不在这里排除！它是发布包的核心内容（内置 Python）。
#              它在 Git 仓库里被 .gitignore 忽略，但打包时要带上。
EXCLUDE_DIRS = {"__pycache__", "release", "报告", ".git", ".github"}
# 打包脚本自己是内部工具（里面的泄露特征清单会自匹配），不放进发布包；
# .gitignore / .gitattributes 只对 Git 仓库有意义，也不进发布包。
# LICENSE 要进 —— 分发的二进制同样需要附带许可证。
EXCLUDE_FILES = {"打包发布.py", ".gitignore", ".gitattributes"}
EXCLUDE_SUFFIX = {".pyc", ".pyo"}
# 这三份是"关于发布本身"的元数据，不能进入自己的文件清单（否则哈希自引用）
META_FILES = {"发布说明.md", "版本快照.json", "SHA256SUMS.txt"}
# 发布包里 报告\ 需要存在但为空，放一个占位说明
REPORT_PLACEHOLDER = "说明.txt"
REPORT_PLACEHOLDER_TEXT = (
    "这个目录用来放工具生成的报告。\n"
    "· mod冲突报告-*.html / .json —— 点「导出 HTML 报告」后生成\n"
    "· 排查会话.json —— 二分排查的进度，自动保存\n"
    "· 症状档案.json —— 接待 / 关卡症状记录，自动保存\n"
    "· 二分排查记录-*.md / 症状档案-*.md —— 手动导出的记录\n"
)

# 打包产物里不该出现的本机特征（用户名、绝对路径等）。
# 注意：CPython 官方二进制里本来就带着上游构建机的路径（C:\Users\Administrator、
# ADMINI~1 等），那是通用名而非打包者信息，必须白名单放过，否则每次都误报。
BENIGN_USERS = ("Public", "Default", "Administrator", "ADMINI~1", "runner",
                "appveyor", "builder", "packer", "vsts", "python")

LEAK_PATTERNS = [
    re.compile(r"[A-Za-z]:\\+Users\\+"
               r"(?!(?:" + "|".join(BENIGN_USERS) + r")\b)"
               r"[^\\\s\"']{2,}", re.IGNORECASE),
    re.compile(r"[A-Za-z]:\\+[^\s\"']*steamapps"),
    re.compile(r"[A-Za-z]:\\+steam\\+steamapps"),
    re.compile(r"dsh[ _]workplace"),
    # Windows 用户名（本机是 "Omention"）出现在 LICENSE 版权行里是**正常的**，
    # 因为公开的 GitHub 句柄就是在它后面接数字。所以只在后面不接字母/数字/
    # 下划线时才判为泄露；"C:\Users\Omention\" 这类路径由第一条规则兜住。
    re.compile(r"O" + r"mention(?![\w])"),
]


def read_version() -> str:
    text = (ROOT / "lorcheck" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    return m.group(1) if m else "0.0.0"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def iter_sources():
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
        for fn in sorted(filenames):
            p = Path(dirpath) / fn
            if p.suffix.lower() in EXCLUDE_SUFFIX:
                continue
            rel = p.relative_to(ROOT)
            if rel.parts and rel.parts[0] in EXCLUDE_DIRS:
                continue
            if fn in EXCLUDE_FILES:
                continue
            yield p, rel


def stage(version: str) -> Path:
    out = ROOT / "release" / f"{APP_NAME}-v{version}"
    if out.exists():
        shutil.rmtree(out)
    (out / "报告").mkdir(parents=True, exist_ok=True)
    (out / "报告" / REPORT_PLACEHOLDER).write_text(
        REPORT_PLACEHOLDER_TEXT, encoding="utf-8")

    count = 0
    for src, rel in iter_sources():
        dst = out / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        count += 1
    print(f"已复制 {count} 个文件到 {out}")
    return out


def inventory(root: Path, exclude_meta: bool = False) -> list:
    items = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for fn in sorted(filenames):
            if exclude_meta and fn in META_FILES:
                continue
            p = Path(dirpath) / fn
            rel = p.relative_to(root).as_posix()
            items.append({"path": rel, "size": p.stat().st_size,
                          "sha256": sha256_file(p)})
    items.sort(key=lambda x: x["path"])
    return items


def leak_check(root: Path) -> list:
    hits = []
    for dirpath, dirnames, filenames in os.walk(root):
        for fn in filenames:
            p = Path(dirpath) / fn
            if p.stat().st_size > 24 * 1024 * 1024:
                continue
            try:
                blob = p.read_bytes()
            except OSError:
                continue
            # 二进制文件（含 NUL 字节）跳过：匹配的是文本特征，扫二进制只会
            # 浪费时间和产生随机误报
            if b"\x00" in blob[:8192]:
                continue
            text = blob.decode("utf-8", errors="ignore")
            for rx in LEAK_PATTERNS:
                m = rx.search(text)
                if m:
                    hits.append((p.relative_to(root).as_posix(), m.group(0)[:60]))
                    break
    return hits


RELEASE_NOTES = """# {app} v{version} · 发布说明

> 本文件同时是**版本快照记录**：记录了本次发布的确切时间、组件构成、
> 每个文件的 SHA256，便于复现与校验。

## 基本信息

| 项 | 值 |
| --- | --- |
| 版本 | **v{version}** |
| 构建时间 | {built} |
| 目标游戏 | Steam 版《废墟图书馆 / Library of Ruina》 AppID **1256670** |
| 运行环境 | Windows 10 / 11 x64 |
| 是否需装 Python | **不需要**（内置便携 Python {pyver}） |
| 文件总数 | {file_count}（其中内置 Python 运行时 {runtime_count} 个） |
| 解压后大小 | {total_mb:.1f} MB（运行时占 {runtime_mb:.1f} MB） |
| 压缩包 SHA256 | 见解压目录同级的 `{app}-v{version}.zip.sha256` |

## 怎么用（三步）

1. 把压缩包解压到**任意有写入权限的目录**（例如 `D:\\Games\\LoRModChecker`）。
   ⚠ 不要放在 `C:\\Program Files`，那里无权写报告文件。
2. 双击 **`启动检测器.bat`**。
3. 点「**开始扫描**」→ 看「拼点卡死专项」→ 需要时用「二分排查」定位。

命令行模式：双击 `命令行扫描.bat`。

## 这个版本能做什么

### 1. 冲突扫描
* 自动定位 Steam 库、游戏本体、创意工坊 Mod 目录、`Player.log`（支持自定义安装位置）
* 解析 `ModSetting.save` 判断每个 Mod **当前是否启用**，只对已启用的 Mod 判定跨 Mod 冲突
* 检查 9 类数据 XML 的重复 ID（书页 / 被动 / 核心书页 / 敌人 / 舞台 / 掉落 / 掉落表 / 卡组 / 能力文本）
* 检查程序集重名（`ErrorLogCleaner`、`KeywordUtil`、`CustomDiceCard`、`1FrameworkLoader` 等框架级单例）
* 检查"多个 Mod 都改动了同一战斗核心类型"（拼点补丁重叠，启发式）
* 解析 `Player.log`：重复 LorId、初始化卡死、慢初始化、缺失资源、重复关键字、找不到的卡组、异常堆栈
* 结果导出 HTML + JSON

### 2. 二分排查
* 交互式开关实验，逐步锁定元凶
* 算法对"多个 Mod 组合才触发"同样正确（纯二分会在这里给出错误答案）
* 实测轮数：单个元凶约 7~8 轮，两个约 14~15 轮，三个约 20 轮
* 支持撤销、中途保存与恢复、导出 Markdown

### 3. 接待症状档案
* 记录"哪个接待、卡在哪一步、结果如何"
* 自动区分「启动慢」与「拼点卡死」；检测症状是否稳定（同条件两种结果会告警）
* 二分排查的结果自动写入

## 目录结构

```
{app}-v{version}\\
├─ 启动检测器.bat        ← 双击这个
├─ 命令行扫描.bat
├─ LoRModChecker.pyw     图形界面主程序
├─ lorcheck_cli.py       命令行入口
├─ lorcheck\\             核心引擎（公开源码）
├─ 测试\\                 自检脚本
├─ runtime\\              内置便携 Python {pyver}
├─ 报告\\                 生成物输出目录（初始为空）
├─ README.md             完整文档
├─ 快速上手.md           三分钟上手
├─ 发布说明.md           本文件
├─ 版本快照.json         机器可读的版本快照
├─ SHA256SUMS.txt        全部载荷文件的校验和
├─ LICENSE               本工具的 MIT 许可证
├─ 第三方组件说明.md      内置 Python 的许可声明
```

## 自检

本包内含三个离线自检脚本，解压后可直接运行：

```
runtime\\python.exe 测试\\测试_二分排查.py     # 二分算法，19 项
runtime\\python.exe 测试\\测试_症状档案.py     # 症状档案，25 项
runtime\\python.exe 测试\\测试_GUI.py          # 图形界面无头驱动，31 项
```

全部通过说明算法与界面在你这台机器上是好的。

## 已知限制

* **只读 `ModSetting.save`**：工具不会替你开关 Mod。它是 .NET `BinaryFormatter`
  的私有格式，写回一旦有字节偏差，游戏可能丢掉整份 Mod 配置，风险远大于收益。
  工具只负责告诉你该关哪些，动手请在游戏内的 Mod 管理界面。
* **框架与加载器不进二分候选**：禁用 `BaseMod` / `1FrameworkPriorityLoader` 等会让
  其它 Mod 根本不加载，实验会失真。
* **"多个 Mod 改动同一游戏类型"是启发式**：DLL 引用了同一个类型 ≠ 一定打了补丁，
  只用来缩小范围。
* **二分排查的结论是实验结论**：如果卡死本身是间歇性的，结果会不准；
  这时「接待症状档案」会给出"不一致"告警，请对同一组重测。
* 卡死后若游戏没有正常退出，`Player.log` 可能缺少最后几秒内容。

## 隐私

* 工具**完全离线**运行，不联网、不上传任何数据。
* 只会读取 Steam 库、游戏目录和 `%USERPROFILE%\\AppData\\LocalLow\\Project Moon\\` 下的日志。
* 唯一的写入位置是本目录下的 `报告\\`。

## 版本快照 · 文件清单

本次发布共 **{file_count}** 个载荷文件（{total_mb:.1f} MB），其中内置 Python 运行时
**{runtime_count}** 个（{runtime_mb:.1f} MB）。下面列出**工具自身的全部文件**；
运行时的逐文件校验和请看 `SHA256SUMS.txt`，机器可读全量清单看 `版本快照.json`。

> `发布说明.md`、`版本快照.json`、`SHA256SUMS.txt` 三份元数据不列入清单，
> 否则会出现哈希自引用。

| 文件 | 大小 (KB) | SHA256（前 32 位） |
| --- | --- | --- |
{file_table}

---

校验和：`SHA256SUMS.txt`（逐文件，含运行时）｜机器可读快照：`版本快照.json`｜
压缩包校验和：解压目录同级的 `{app}-v{version}.zip.sha256`。

重新打包（在开发目录）：`runtime\\python.exe 打包发布.py`
"""

QUICK_START = """# 快速上手（三分钟）

## 1. 解压
把整个文件夹解压到**有写入权限**的目录，例如 D:\\Games\\LoRModChecker。
不要放 C:\\Program Files（无法写报告）。

## 2. 双击 `启动检测器.bat`

会弹出图形界面，三行路径会自动填好（游戏目录 / 创意工坊 / Player.log）。
如果是空的，点「浏览…」自己选。

## 3. 点「开始扫描」
大约 1 秒完成。然后：

* **「拼点卡死专项」** —— 先看这里，按怀疑度排行逐个禁用验证
* **「冲突清单」** —— 全部问题，点一行看证据和处置建议
* **「导出 HTML 报告」** —— 保存完整报告

## 4. 分不清是哪个 Mod？用「二分排查」
每轮工具会给出"本轮要禁用的 Mod"清单，你去游戏内的 Mod 管理界面照做，
进接待制造拼点，回工具点结果（仍然卡死 / 已恢复正常 / 不确定）。
工具会自动把范围减半，直到锁定元凶。

> 铁律：**除了清单里列出的，其余 Mod 都保持启用。**

## 5. 顺手记一笔「接待症状档案」
记录"哪个接待、卡在哪一步"。工具会自动判断：
只有启动阶段卡 → 多半是某个 Mod 加载慢，不是拼点冲突；
只有拼点卡 → 典型的拼点链路冲突。

## 出问题怎么办
* 双击没反应 → 看 README.md 的「环境要求」
* 扫描结果为空 → 在界面上手动指定三行路径
* 想用命令行 → 双击 `命令行扫描.bat`
"""

THIRD_PARTY = """# 第三方组件说明

本发布包内含一个精简过的 **CPython {pyver}** 运行时（`runtime\\` 目录），
用于让没有安装 Python 的 Windows 用户直接运行本工具。

* 组件：Python 编程语言解释器（含 tkinter / Tcl-Tk）
* 版本：{pyver}
* 版权：Copyright © 2001-2024 Python Software Foundation. All rights reserved.
* 许可：PSF License Agreement（Python 软件基金会许可协议）
* 许可全文：`runtime\\LICENSE.txt`

Python 的 PSF 许可允许再分发，条件是保留版权声明与许可文本 —— `runtime\\LICENSE.txt`
即为此保留的许可全文。

本工具自身的代码（`lorcheck\\`、`LoRModChecker.pyw`、`lorcheck_cli.py`、`测试\\`）
未使用任何第三方 Python 库，只依赖 Python 标准库。

本工具与 Project Moon / 《废墟图书馆》官方无任何关联，仅供玩家排查 Mod 冲突使用。
"""


def build_docs(out: Path, version: str, inv: list) -> dict:
    total = sum(x["size"] for x in inv)
    app_files = [x for x in inv if not x["path"].startswith("runtime/")]
    rt_files = [x for x in inv if x["path"].startswith("runtime/")]
    pyver = "3.12"
    try:
        v = (out / "runtime" / "Lib" / "sysconfig.py").read_text(
            encoding="utf-8", errors="ignore")
        m = re.search(r'"py_version_short"\s*:\s*"([\d.]+)"', v)
        if m:
            pyver = m.group(1)
    except OSError:
        pass

    rows = []
    for x in app_files:
        rows.append(f"| `{x['path']}` | {x['size']/1024:.1f} | `{x['sha256'][:32]}…` |")

    ctx = {
        "app": APP_NAME, "version": version,
        "built": time.strftime("%Y-%m-%d %H:%M:%S"),
        "file_count": len(inv), "total_mb": total / 1048576,
        "app_file_count": len(app_files),
        "runtime_count": len(rt_files),
        "runtime_mb": sum(x["size"] for x in rt_files) / 1048576,
        "file_table": "\n".join(rows), "pyver": pyver,
    }

    (out / "发布说明.md").write_text(RELEASE_NOTES.format(**ctx), encoding="utf-8")
    (out / "快速上手.md").write_text(QUICK_START, encoding="utf-8")
    (out / "第三方组件说明.md").write_text(THIRD_PARTY.format(**ctx), encoding="utf-8")

    # 机器可读快照
    snapshot = {
        "app": APP_NAME, "version": version,
        "built_at": ctx["built"],
        "target_game": {"name": "Library Of Ruina", "steam_appid": "1256670"},
        "python_runtime": pyver,
        "file_count": len(inv),
        "total_bytes": total,
        "files": inv,
    }
    (out / "版本快照.json").write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")

    # 校验和（排除校验和文件自身，否则自引用）
    lines = [f"{x['sha256']}  {x['path']}" for x in inv]
    (out / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return ctx


def make_zip(out: Path) -> Path:
    zip_path = out.parent / f"{out.name}.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for dirpath, dirnames, filenames in os.walk(out):
            dirnames.sort()
            for fn in sorted(filenames):
                p = Path(dirpath) / fn
                z.write(p, Path(out.name) / p.relative_to(out))
        # 保证空目录也在包里
        for d in ("报告",):
            z.writestr(f"{out.name}/{d}/", "")
    return zip_path


def main() -> int:
    ap = argparse.ArgumentParser(description="打包 LoR Mod 冲突检测器发布版")
    ap.add_argument("--no-zip", action="store_true", help="只生成目录，不压缩")
    args = ap.parse_args()

    version = read_version()
    print(f"=== 打包 {APP_NAME} v{version} ===")

    out = stage(version)
    payload = inventory(out, exclude_meta=True)
    total = sum(x["size"] for x in payload)
    print(f"载荷清单：{len(payload)} 个文件，{total/1048576:.1f} MB")

    ctx = build_docs(out, version, payload)
    print(f"已生成 发布说明.md / 快速上手.md / 第三方组件说明.md / "
          f"版本快照.json / SHA256SUMS.txt")

    print("正在做本机信息泄露检查 …")
    hits = leak_check(out)
    if hits:
        print("!! 发现疑似本机信息，已中止打包：")
        for rel, what in hits[:20]:
            print(f"   {rel}  <- {what}")
        return 2
    print("   未发现本机路径 / 用户名")

    zip_path = None
    if not args.no_zip:
        print("正在压缩 …")
        zip_path = make_zip(out)

    all_files = inventory(out)
    print()
    print(f"发布目录   : {out}")
    print(f"文件总数   : {len(all_files)}（载荷 {len(payload)} + 元数据 "
          f"{len(all_files)-len(payload)}）")
    print(f"解压后大小 : {sum(x['size'] for x in all_files)/1048576:.1f} MB")
    if zip_path:
        digest = sha256_file(zip_path)
        (zip_path.parent / f"{zip_path.name}.sha256").write_text(
            f"{digest}  {zip_path.name}\n", encoding="utf-8")
        print(f"压缩包     : {zip_path}")
        print(f"压缩包大小 : {zip_path.stat().st_size/1048576:.1f} MB")
        print(f"压缩包 SHA256: {digest}")
    print("完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
