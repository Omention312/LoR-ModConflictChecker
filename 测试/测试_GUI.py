"""GUI「二分排查」标签页的无头功能测试（不弹窗、不截图、不打开外部程序）。

用法：runtime\\python.exe 测试\\测试_GUI.py
"""
import faulthandler
import importlib.util
import sys
import time
from pathlib import Path
from tkinter import Tk

faulthandler.dump_traceback_later(120, exit=True)

APP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_DIR))

spec = importlib.util.spec_from_file_location("lor_gui", APP_DIR / "LoRModChecker.pyw")
gui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gui)

# 屏蔽所有弹窗
gui.messagebox.askyesno = lambda *a, **k: True
gui.messagebox.showinfo = lambda *a, **k: None
gui.messagebox.showwarning = lambda *a, **k: None
gui.messagebox.showerror = lambda *a, **k: None

ok = []


def check(name, cond, extra=""):
    ok.append(bool(cond))
    print(f"[{'OK ' if cond else 'FAIL'}] {name} {extra}", flush=True)


print("== 创建窗口 ==", flush=True)
root = Tk()
app = gui.App(root)
app._open = lambda p: None          # 不要真的打开外部程序
root.update()
app.autofill()
root.update()

print("== 开始扫描 ==", flush=True)
app.start_scan()
t0 = time.time()
while app.result is None and time.time() - t0 < 120:
    root.update()
    time.sleep(0.05)
root.update()
check("扫描完成", app.result is not None,
      f"mods={len(app.result.mods)} "
      f"enabled={sum(1 for m in app.result.mods if m.enabled is True)}")

sess_path = app.bs_path
sess_path.unlink(missing_ok=True)

pool = app._bs_candidates()
check("候选池非空", len(pool) >= 10, f"pool={len(pool)}")
check("候选已排除框架", all(not m.is_framework for m in pool))
check("候选都是已启用", all(m.enabled is True for m in pool))
print(f"        全部候选 {len(pool)}，其中拼点相关 "
      f"{len([m for m in pool if m.clash_types])}")

app.var_scope.set("enabled")
app.var_reception.set("测试接待甲")
app.var_stage.set("开始拼点")
app.bs_session = None
app._bs_start()
s = app.bs_session
check("会话已创建", s is not None and len(s.all_candidates) == len(pool))
check("会话文件已保存", sess_path.exists())
check("会话带固定测试条件",
      s.symptom_reception == "测试接待甲" and s.symptom_stage == "clash",
      f"{s.symptom_reception}/{s.symptom_stage}")
check("首轮继承测试条件",
      s.pending.reception == "测试接待甲" and s.pending.stage == "clash")

culprit = pool[5].uid
rounds = 0
while s is not None and not s.is_done and rounds < 60:
    r = s.pending
    if r is None:
        break
    app._bs_apply("fixed" if culprit in set(r.disable) else "persists")
    rounds += 1
root.update()
check("定位到正确元凶", s.is_done and s.verdict == [culprit],
      f"{rounds} 轮 -> {s.verdict}")
check("轮数与预估相符", rounds <= 10, f"{rounds} 轮")

txt = app.txt_bs.get("1.0", "end")
check("结论已渲染", "导致问题的 mod" in txt and "★" in txt)
rows = app.tree_bs.get_children()
check("历史表行数匹配", len(rows) == len(s.rounds), f"{len(rows)} vs {len(s.rounds)}")
check("结果按钮已禁用", str(app.bs_res_btns[0]["state"]) == "disabled")
check("撤销按钮可用", str(app.bs_btn_undo["state"]) == "normal")

before = len(s.rounds)
app._bs_undo()
root.update()
check("撤销生效", len(s.rounds) == before - 1 and not s.is_done,
      f"{before} -> {len(s.rounds)}, phase={s.phase}")

app._bs_copy()
clip = root.clipboard_get()
check("剪贴板清单", "请禁用" in clip and "保持启用" in clip, f"{len(clip)} 字符")

app._bs_export()
mds = sorted(gui.REPORT_DIR.glob("二分排查记录-*.md"))
check("导出 Markdown", bool(mds), mds[-1].name if mds else "")
if mds:
    content = mds[-1].read_text(encoding="utf-8")
    check("导出内容完整", "实验记录" in content and "| 轮次 |" in content)
    mds[-1].unlink()

app.bs_session = None
app._bs_autoload()
check("会话可恢复",
      app.bs_session is not None and len(app.bs_session.rounds) == len(s.rounds))

app._bs_reset()
root.update()
check("重置生效", app.bs_session is not None and not app.bs_session.rounds)

# ---------------------------------------------------------------- 症状档案
bisect_obs = [o for o in app.journal.observations if o.source == "bisect"]
check("二分结果自动写入档案", len(bisect_obs) >= 1,
      f"{len(bisect_obs)} 条 bisect 记录")
check("自动记录带固定测试条件",
      all(o.reception == app.bs_session.symptom_reception for o in bisect_obs))

app.var_obs_reception.set("测试接待甲")
app.var_obs_stage.set("启动 / 读盘")
app.var_obs_outcome.set("卡死")
app.var_obs_note.set("读了 30 秒才进去")
app._j_add_manual()
root.update()
check("手工记录已添加", len(app.journal.observations) == len(bisect_obs) + 1)
check("手工记录字段正确",
      app.journal.observations[-1].stage == "startup"
      and app.journal.observations[-1].outcome == "frozen"
      and app.journal.observations[-1].note == "读了 30 秒才进去")
check("档案已存盘", app.journal_path.exists())
check("档案表格行数一致",
      len(app.tree_j.get_children()) == len(app.journal.observations))

# 人为制造一条不一致记录（同接待 + 同阶段 + 同禁用集合，结果不同）
app.var_obs_reception.set("测试接待甲")
app.var_obs_stage.set("启动 / 读盘")
app.var_obs_outcome.set("卡死")
app.var_obs_note.set("")
app._j_add_manual()
app.var_obs_outcome.set("正常")
app._j_add_manual()
root.update()
check("档案检测到不一致", "不一致" in app.txt_j.get("1.0", "end"))

app._j_export()
jmds = sorted(gui.REPORT_DIR.glob("症状档案-*.md"))
check("档案导出 Markdown", bool(jmds), jmds[-1].name if jmds else "")
if jmds:
    jc = jmds[-1].read_text(encoding="utf-8")
    check("档案导出含不一致小节", "不一致记录" in jc and "按接待" in jc)
    jmds[-1].unlink()

rp = gui.report_mod.write_report(app.result, gui.REPORT_DIR, journal=app.journal)
html = rp.read_text(encoding="utf-8")
check("HTML 报告含症状档案",
      "接待 / 关卡症状档案" in html and "不稳定" in html, f"{len(html)} 字符")
for f in gui.REPORT_DIR.glob(f"{rp.stem}.*"):
    f.unlink()

app._j_clear()
root.update()
check("档案清空", len(app.journal.observations) == 0)

sess_path.unlink(missing_ok=True)
app.journal_path.unlink(missing_ok=True)
root.destroy()

print()
print(f"通过 {sum(ok)}/{len(ok)}")
raise SystemExit(0 if all(ok) else 1)
