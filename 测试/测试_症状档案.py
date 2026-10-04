"""接待 / 关卡症状档案的离线验证。

覆盖：增删清、按接待/阶段汇总、阶段判断、不一致检测、接待特异性、
存档往返、Markdown 导出、以及与二分排查的联动统计。

用法：runtime\\python.exe 测试\\测试_症状档案.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lorcheck import bisect, journal  # noqa: E402

ok = []


def check(name, cond, extra=""):
    ok.append(bool(cond))
    print(f"[{'OK ' if cond else 'FAIL'}] {name} {extra}")


def obs(rec, stage, outcome, disabled=(), note=""):
    return journal.Observation(reception=rec, stage=stage, outcome=outcome,
                               disabled=list(disabled), note=note)


# --- 基本增删 -------------------------------------------------------------
j = journal.Journal()
check("初始为空", j.observations == [] and journal.summarize(j).total == 0)
j.add(obs("接待A", "clash", "frozen"))
j.add(obs("接待B", "clash", "ok"))
check("添加两条", len(j.observations) == 2)
check("时间戳自动填", bool(j.observations[0].ts))
check("接待列表", j.receptions() == ["接待A", "接待B"])
j.remove(0)
check("删除一条", len(j.observations) == 1 and j.observations[0].key == "接待B")
j.clear()
check("清空", j.observations == [])

# --- 阶段判断 -------------------------------------------------------------
j = journal.Journal()
j.add(obs("A", "startup", "frozen"))
s = journal.summarize(j)
check("只启动卡死 -> 提示慢初始化",
      "启动" in s.stage_hint and "拼点" not in s.stage_hint.split("——")[0])

j = journal.Journal()
j.add(obs("A", "clash", "frozen"))
s = journal.summarize(j)
check("只拼点卡死 -> 提示拼点冲突",
      "拼点" in s.stage_hint and "二分排查最有效" in s.stage_hint)

j = journal.Journal()
j.add(obs("A", "startup", "frozen"))
j.add(obs("A", "clash", "frozen"))
s = journal.summarize(j)
check("两类都有 -> 提示是两个不同问题", "两个不同的问题" in s.stage_hint)

# --- 汇总统计 -------------------------------------------------------------
j = journal.Journal()
j.add(obs("接待A", "clash", "frozen", ["m1", "m2"]))
j.add(obs("接待A", "clash", "frozen", ["m1", "m3"]))
j.add(obs("接待A", "clash", "ok", ["m1", "m2", "m3"]))
j.add(obs("接待B", "clash", "ok", ["m1"]))
s = journal.summarize(j)
check("按接待汇总", s.by_reception["接待A"] == {"frozen": 2, "ok": 1, "unclear": 0},
      str(s.by_reception.get("接待A")))
check("总数与结果分布", s.total == 4 and s.by_outcome.get("frozen") == 2
      and s.by_outcome.get("ok") == 2)
check("接待特异性", s.reception_specific,
      f"frozen={s.frozen_receptions} ok={s.ok_receptions}")

# --- 不一致检测 -----------------------------------------------------------
j = journal.Journal()
j.add(obs("接待A", "clash", "frozen", ["m1", "m2"]))
j.add(obs("接待A", "clash", "ok", ["m1", "m2"]))     # 同签名，结果不同
s = journal.summarize(j)
check("检测到不一致", len(s.unstable) == 1, str(s.unstable))

j2 = journal.Journal()
j2.add(obs("接待A", "clash", "frozen", ["m1"]))
j2.add(obs("接待A", "clash", "ok", ["m1", "m2"]))    # 禁用集合不同 -> 正常
check("禁用集合不同不算不一致", not journal.summarize(j2).unstable)

# --- 存档往返 -------------------------------------------------------------
j = journal.Journal()
j.add(obs("接待A", "clash", "frozen", ["m1"]))
j.add(obs("接待B", "startup", "ok", [], note="读了 30 秒"))
p = ROOT / "_tmp_journal.json"
j.save(p)
j2 = journal.Journal.load(p)
check("存档往返一致", j2.to_dict() == j.to_dict())
check("备注与禁用集合保留",
      j2.observations[1].note == "读了 30 秒" and j2.observations[0].disabled == ["m1"])

# --- Markdown 导出 --------------------------------------------------------
md = journal.render_markdown(j, {})
check("Markdown 含关键小节",
      "症状档案" in md and "按接待" in md and "按阶段" in md and "全部记录" in md,
      f"{len(md)} 字符")
txt = journal.render_text_summary(j, {})
check("界面汇总文本", "共 2 条记录" in txt)

# --- 与二分排查联动 -------------------------------------------------------
cands = [f"mod{i:02d}" for i in range(8)]
s = bisect.new_session(cands, symptom_reception="都市恶疾", symptom_stage="clash")
check("会话带固定测试条件",
      s.symptom_reception == "都市恶疾" and s.symptom_stage == "clash")
check("轮次继承测试条件",
      s.pending.reception == "都市恶疾" and s.pending.stage == "clash")
check("测试条件文案", "都市恶疾" in bisect.symptom_text(s)
      and "开始拼点" in bisect.symptom_text(s))

jl = journal.Journal()
culprit = "mod03"
rounds = 0
while not s.is_done and rounds < 40:
    r = s.pending
    outcome = "ok" if culprit in set(r.disable) else "frozen"
    jl.add(journal.Observation(reception=s.symptom_reception, stage=s.symptom_stage,
                               outcome=outcome, disabled=list(r.disable),
                               round_n=r.n, source="bisect"))
    bisect.apply_result(s, "fixed" if outcome == "ok" else "persists")
    rounds += 1
check("二分结论正确", s.verdict == [culprit], str(s.verdict))
sm = journal.summarize(jl)
check("联动：全部记录归到同一接待",
      list(sm.by_reception) == ["都市恶疾"], str(list(sm.by_reception)))
check("联动：卡死/正常次数之和等于轮数",
      sm.by_reception["都市恶疾"]["frozen"] + sm.by_reception["都市恶疾"]["ok"] == rounds,
      f"{sm.by_reception['都市恶疾']} vs {rounds} 轮")
bmd = bisect.render_markdown(s, {})
check("排查记录含测试条件", "固定测试条件" in bmd and "都市恶疾" in bmd)

p.unlink(missing_ok=True)
print()
print(f"通过 {sum(ok)}/{len(ok)}")
raise SystemExit(0 if all(ok) else 1)
