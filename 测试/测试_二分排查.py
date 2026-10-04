"""二分排查状态机的离线验证。

模拟"症状判定器"：只有 culprits 集合被完整禁用时症状才消失。
覆盖单点 / 两两组合 / 三点组合 / 范围外 / 撤销 / 重测 / 存档 / 导出。

用法：runtime\\python.exe 测试\\测试_二分排查.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lorcheck import bisect  # noqa: E402


def run_case(name, candidates, culprits, use_baseline=True,
             max_rounds=400, verbose=False):
    s = bisect.new_session(candidates, scope="test", use_baseline=use_baseline)
    rounds = 0
    while not s.is_done and rounds < max_rounds:
        r = s.pending
        if r is None:
            break
        present = not set(culprits).issubset(set(r.disable))
        bisect.apply_result(s, "persists" if present else "fixed")
        rounds += 1
    ok = s.is_done and set(s.verdict) == set(culprits)
    roles = {}
    for r in s.rounds:
        roles[r.role_cn] = roles.get(r.role_cn, 0) + 1
    print(f"[{'OK ' if ok else 'FAIL'}] {name}: {rounds} 轮 {roles} "
          f"verdict={sorted(s.verdict)}")
    if not ok and verbose:
        print("      note:", s.note)
        for r in s.rounds:
            print(f"      第{r.n:>3}轮 {r.role_cn} 禁用{len(r.disable):>3} "
                  f"结果={r.result:<9} 剩余={r.pool_after}")
    return ok, rounds


def main():
    cands = [f"mod{i:02d}" for i in range(54)]
    results = []
    single = []
    combo = []

    for idx in (0, 1, 26, 27, 52, 53):
        ok, n = run_case(f"单点 mod{idx:02d}", cands, {f"mod{idx:02d}"})
        results.append(ok)
        single.append(n)

    for a, b in ((0, 53), (10, 11), (26, 27), (5, 40)):
        ok, n = run_case(f"两两组合 mod{a:02d}+mod{b:02d}", cands,
                         {f"mod{a:02d}", f"mod{b:02d}"}, verbose=not ok)
        results.append(ok)
        combo.append(n)

    ok, n = run_case("三点组合 mod01+mod20+mod33", cands,
                     {"mod01", "mod20", "mod33"}, verbose=not ok)
    results.append(ok)
    combo.append(n)

    ok, n = run_case("两个候选 / 元凶其中一个", ["a", "b"], {"b"})
    results.append(ok)
    ok, n = run_case("两个候选 / 两个都是元凶", ["a", "b"], {"a", "b"})
    results.append(ok)

    ok, n = run_case("单点（跳过基线）", cands, {"mod33"}, use_baseline=False)
    results.append(ok)

    # 元凶不在范围内 -> 应明确报告"不是这些 mod"
    s = bisect.new_session(cands, scope="test")
    guard = 0
    while not s.is_done and guard < 400:
        bisect.apply_result(s, "persists")
        guard += 1
    out_of_scope = s.is_done and not s.verdict and "不是这些 mod" in s.note
    print(f"[{'OK ' if out_of_scope else 'FAIL'}] 元凶在范围外：phase={s.phase} "
          f"verdict={s.verdict} 轮次={len(s.rounds)}")
    results.append(out_of_scope)

    # 撤销
    s = bisect.new_session(cands)
    while not s.is_done and len(s.rounds) < 3:
        present = "mod07" not in set(s.pending.disable)
        bisect.apply_result(s, "persists" if present else "fixed")
    n_before = len(s.rounds)
    bisect.undo_last(s)
    undo_ok = len(s.rounds) == n_before - 1
    print(f"[{'OK ' if undo_ok else 'FAIL'}] 撤销：轮次 {n_before} -> {len(s.rounds)}，"
          f"待查 {len(s.find_R)}")
    results.append(undo_ok)

    # 重测
    s = bisect.new_session(cands)
    before = list(s.pending.disable)
    bisect.apply_result(s, "unsure")
    same = s.pending.disable == before
    print(f"[{'OK ' if same else 'FAIL'}] 不确定重测：清单不变 = {same}")
    results.append(same)

    # 存档往返
    s = bisect.new_session(cands)
    bisect.apply_result(s, "fixed")
    bisect.apply_result(s, "persists")
    p = ROOT / "_tmp_bisect_session.json"
    bisect.save(s, p)
    s2 = bisect.load(p)
    same2 = (s2 is not None and s2.to_dict() == s.to_dict())
    print(f"[{'OK ' if same2 else 'FAIL'}] 存档往返一致 = {same2}")
    results.append(same2)

    # 导出
    md = bisect.render_markdown(s, {})
    txt = bisect.pending_plain_list(s, {})
    export_ok = ("二分排查记录" in md and "实验记录" in md and "请禁用" in txt)
    print(f"[{'OK ' if export_ok else 'FAIL'}] 导出 Markdown({len(md)}) + "
          f"清单({len(txt)})")
    results.append(export_ok)

    p.unlink(missing_ok=True)
    print()
    print(f"单点最多 {max(single)} 轮；组合最多 {max(combo)} 轮；"
          f"通过 {sum(1 for r in results if r)}/{len(results)}")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
