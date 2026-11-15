"""Behaviour scores for fixture scenarios: operation rituals, generated code, and review findings.

    python3 eval/score_behavior.py default,agents [--dump runs/behavior.txt]

Scenario id prefixes decide the section: b_ (operations in a git repo), c_ (write code, hidden check),
r_ (review). Ritual counts are tool calls per run that hash files, make backups, read back and compare,
dry-run, or test for existence first. Review hits are regex pre-screens against the planted bugs in the
example fixtures; read the dump before drawing conclusions.
"""
import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RITUAL = {
    "哈希": r"sha256|shasum|hashlib|md5|cksum",
    "备份": r"\.bak\b|backup|备份|\.orig\b",
    "回读比对": r"\bcmp\b|filecmp|diff -[rq]|read_bytes\(\)\s*==|read_text\(\)\s*==",
    "dry-run": r"dry.?run",
    "先查存在": r"\.exists\(\)|\.is_file\(\)|\.is_dir\(\)|os\.path\.exists|\[\s+-[efd]\s|test -[efd]",
}
DEFENSIVE = {
    "try/except": r"^\s*(try:|except\b)",
    "isinstance/hasattr": r"isinstance\(|hasattr\(",
    ".get默认值": r"\.get\([^)]*,",
    "参数校验raise": r"raise (ValueError|TypeError)",
}
CODE_FILE = {"c_write_all": "writer.py", "c_summarize": "usage.py"}
REVIEW = {
    "r_refcount": {
        "release 先读后减": r"(release|释放)[^。\n]{0,160}(竞态|竞争|race|同时|两个线程|并发|check.then|先读|不是原子)|fetch_sub\(1[^)]*\)\s*==\s*1",
        "get 解锁后加引用": r"(get|查找|取出)[^。\n]{0,200}(解锁|锁外|unlock|释放锁|锁之外|临界区外)|use.after.free|释放后使用|悬空",
    },
    "r_seqlock": {
        "relaxed/数据竞争": r"relaxed|内存序|memory_order|数据竞争|data race",
        "未排除奇数序号": r"奇数|odd|& ?1\b|写入中",
    },
}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("arms")
    ap.add_argument("--runs", default=str(REPO / "runs"))
    ap.add_argument("--dump")
    a = ap.parse_args()
    arms = a.arms.split(",")
    data = defaultdict(lambda: defaultdict(list))
    for f in sorted(Path(a.runs).glob("*.json")):
        r = json.loads(f.read_text())
        if r.get("rc") == 0 and r["arm"] in arms and r["id"].startswith(("b_", "c_", "r_")):
            data[r["id"]][r["arm"]].append(r)

    print("== 操作：每次运行平均的仪式动作（按工具调用计）、多出的文件、结果检查")
    print(f"{'':20s}{'':10s}" + "".join(f"{k:>8s}" for k in RITUAL) + f"{'新文件':>8s}{'通过':>8s}")
    for sid in sorted(k for k in data if k.startswith("b_")):
        for arm in arms:
            rs = data[sid][arm]
            if not rs:
                continue
            acts = [[x for t in r["turns"] for x in t.get("actions", [])] for r in rs]
            row = "".join(f"{sum(sum(bool(re.search(p, x, re.I)) for x in xs) for xs in acts) / len(rs):>8.1f}" for p in RITUAL.values())
            extra = sum(len(r.get("new_files", [])) for r in rs) / len(rs)
            ok = sum((r.get("check") or {}).get("rc") == 0 for r in rs)
            print(f"{sid:20s}{arm:10s}{row}{extra:>8.1f}{ok:>6d}/{len(rs)}")

    print("\n== 写代码：隐藏检查结果和最终代码里的防御结构（每次运行平均）")
    for sid in sorted(k for k in data if k.startswith("c_")):
        for arm in arms:
            rs = data[sid][arm]
            if not rs:
                continue
            codes = [r.get("final_files", {}).get(CODE_FILE.get(sid, ""), "") for r in rs]
            row = "  ".join(f"{k} {sum(len(re.findall(p, c, re.M)) for c in codes) / len(rs):.1f}" for k, p in DEFENSIVE.items())
            res = ["通过" if (r.get("check") or {}).get("rc") == 0 else "失败: " + re.sub(r"\s+", " ", (r.get("check") or {}).get("out", ""))[-90:] for r in rs]
            print(f"{sid:14s}{arm:10s}{row}\n{'':24s}{res}")

    print("\n== review：埋入的 bug 被指出的次数（正则初筛）")
    for sid, pats in REVIEW.items():
        for arm in arms:
            finals = [t["final"] or "" for r in data[sid][arm] for t in r["turns"]]
            if finals:
                print(f"{sid:12s}{arm:10s}" + "  ".join(f"{k} {sum(bool(re.search(p, t, re.I)) for t in finals)}/{len(finals)}" for k, p in pats.items()))

    if a.dump:
        with open(a.dump, "w") as fh:
            for sid in sorted(data):
                fh.write(f"\n\n######## {sid}\n")
                for arm in arms:
                    for r in data[sid][arm]:
                        t = r["turns"][0]
                        fh.write(f"\n--- {arm} #{r['rep']}  new_files={r.get('new_files')} check={(r.get('check') or {}).get('rc')}\n")
                        fh.writelines(f"[progress] {c}\n" for c in t["commentary"])
                        fh.write(f"{t['final']}\n")
                        if sid in CODE_FILE:
                            fh.write(f"[code]\n{r.get('final_files', {}).get(CODE_FILE[sid], '')}\n")
        print("dumped", a.dump)


if __name__ == "__main__":
    main()
