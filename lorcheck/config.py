# -*- coding: utf-8 -*-
"""常量表：数据类别、游戏类型关键词、严重度、程序集白名单。"""

APP_ID = "1256670"
GAME_NAME = "Library Of Ruina"

# ---------------------------------------------------------------------------
# mod 清单文件（创意工坊项 / 本地 Mods 目录都可能出现这几种）
# ---------------------------------------------------------------------------
MANIFEST_NAMES = ("StageModInfo.xml", "ModInfo.Xml", "ModInfo.xml", "stageModInfo.xml")

# ---------------------------------------------------------------------------
# 数据 XML 根节点 -> 逻辑类别。(类别名, 中文名)
# 游戏内部按 "<包ID>:<条目ID>" 组成 LorId 放进字典，
# 同一类别内 LorId 重复 -> ArgumentException: same key has already been added
# ---------------------------------------------------------------------------
ROOT_TO_CATEGORY = {
    "DiceCardXmlRoot": "card",
    "PassiveXmlRoot": "passive",
    "BookXmlRoot": "book",
    "EnemyUnitClassRoot": "enemy",
    "StageXmlRoot": "stage",
    "BookUseXmlRoot": "dropbook",
    "CardDropTableXmlRoot": "droptable",
    "DeckXmlRoot": "deck",
    "BattleCardAbilityDescRoot": "ability_text",
}

CATEGORY_CN = {
    "card": "战斗书页",
    "passive": "被动能力",
    "book": "核心书页",
    "book_enemy": "敌方核心书页",
    "book_librarian": "司书核心书页",
    "enemy": "敌人单位",
    "stage": "接待/舞台",
    "dropbook": "书籍掉落",
    "droptable": "掉落表",
    "deck": "敌方卡组",
    "ability_text": "书页能力文本",
}

# BookXmlRoot 需要通过文件名区分敌我，其它类别不需要
BOOK_FILENAME_HINTS = (
    ("EquipPage_Enemy", "book_enemy"),
    ("EquipPage_Librarian", "book_librarian"),
)

# ---------------------------------------------------------------------------
# 严重度
# ---------------------------------------------------------------------------
SEV_FATAL = 4   # 必然导致崩溃 / 卡死
SEV_HIGH = 3    # 极可能导致崩溃 / 功能失效
SEV_MID = 2     # 会造成错误日志、内容被静默覆盖
SEV_LOW = 1     # 噪声 / 提示
SEV_INFO = 0    # 仅供了解

SEV_CN = {4: "致命", 3: "高", 2: "中", 1: "低", 0: "提示"}
SEV_ORDER = [4, 3, 2, 1, 0]

# ---------------------------------------------------------------------------
# 游戏里与"拼点 / 战斗结算"直接相关的类型。
# 如果两个以上 mod 的 DLL 都引用了同一个类型，它们极可能都给它打了 Harmony 补丁
# ——这是拼点阶段卡死最常见的成因（补丁互相覆盖 / 递归 / 死循环）。
# ---------------------------------------------------------------------------
CLASH_TYPES = (
    "BattleDiceCardModel",
    "DiceCardAbilityBase",
    "DiceCardSelfAbility",
    "BattlePlayingCardDataInUnitModel",
    "BattleUnitModel",
    "UnitDiceCard",
    "DiceCardItemModel",
    "BattleUnitBuf",
    "PassiveAbilityBase",
    "BattleAllyCardDetail",
    "BattleEnemyCardDetail",
    "BattleCardAbilityBase",
    "BattleDiceBehavior",
    "DiceBehaviour",
)

# 纯工具/框架程序集：被多个 mod 重复打包属于常态，不算冲突
FRAMEWORK_ASSEMBLIES = {
    "0harmony", "00harmonypreloader", "mono.cecil", "mono.cecil.mdb", "mono.cecil.pdb",
    "mono.cecil.rocks", "monomod.runtimedetour", "monomod.utils", "monomod.common",
    "naudio", "naudio.core", "naudio.winmm", "newtonsoft.json", "unityengine",
    "unityengine.coremodule", "unityengine.uimodule", "unityengine.uielementsmodule",
    "unityengine.imguimodule", "unityengine.textcoremodule", "unityengine.textrenderingmodule",
    "unityengine.assetbundlemodule", "unityengine.unitywebrequestmodule",
    "unityengine.unitywebrequestassetbundlemodule", "unityengine.audiomodule",
    "unityengine.animationmodule", "assembly-csharp", "assembly-csharp-firstpass",
    "system", "system.core", "system.xml", "system.drawing", "system.windows.forms",
    "mscorlib", "netstandard", "facepunch.steamworks.win64", "ookii.dialogs",
    "spine-unity", "unity.mathematics", "unity.postprocessing.runtime",
    "unity.textmeshpro", "xgamingruntime", "tmpro", "steamworks.net",
    "unity.burst", "unity.collections", "unity.2d.animation.runtime",
    "unity.2d.animation.triangle.runtime", "unity.2d.common.runtime",
}

# 这些程序集被重复打包时几乎一定出问题（框架级单例，版本必须唯一）
RISKY_ASSEMBLIES = {
    "errorlogcleaner", "keywordutil", "autokeywordutil", "customdicecard",
    "synchronizationfix", "10enumextender", "myjsontool", "basejumper",
    "entry", "lorid", "loridextensions", "localfunc", "harmonywrapper",
    # LoR 社区的共享框架：多个版本混用会导致初始化顺序 / 补丁目标错乱
    "1frameworkloader", "1frameworkpriorityinjector", "1frameworkpriorityloader",
    "basemod", "cyaminthe.assortedfixes", "cyaminthe.assortedfixes.integrated",
    "custommaputility", "betterfilters", "lorlocalizationmanager",
    "basejumpercore", "ls_displayutil", "ls_invitationutil", "ls_bookskinutil",
    "basemodframework", "localfunc.lor",
}

# ---------------------------------------------------------------------------
# 拼点 / 战斗关键词：出现在异常堆栈或 mod 名里就提高嫌疑
# ---------------------------------------------------------------------------
CLASH_KEYWORDS = (
    "clash", "dice", "diceroll", "battlecard", "dicecard", "cardability",
    "playingcard", "keywordutil", "autokeyword", "拼点", "骰子", "书页",
)

# ---------------------------------------------------------------------------
# 日志中会用到的正则（compile 在 scan_log 里做，这里只放文本）
# ---------------------------------------------------------------------------
RE_SAME_KEY = r"ArgumentException:\s*An item with the same key has already been added\.\s*Key:\s*LorId\((?P<key>[^)]*)\)"
RE_DUP_ASSEMBLY = r"The same assembly name already exists\.?\s*:?\s*(?P<name>[^\s]+)"
RE_ALREADY_EXISTS = r"ERROR:\s*Aleady Exists\s*:\s*(?P<key>\S+)"
RE_LOAD_PATH = r"load\s*:\s*(?P<path>[A-Za-z]:\\[^\r\n]+?)\s*$"
RE_MISSING_PATH = r'Could not find (?:a part of )?the path "?(?P<path>[^"\r\n]+)"?'
RE_WORKSHOP_PATH = r"[\\/]workshop[\\/]content[\\/]1256670[\\/](?P<id>\d+)"
RE_REPEAT_WAIT = r"Repeat Wait\s*:\s*(?P<n>\d+)"
RE_EXCEPTION_LINE = r"^(?P<type>[A-Za-z_][\w.]*(?:Exception|Error))\s*:\s*(?P<msg>.*)$"
