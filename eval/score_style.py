"""Style metrics of final replies, or of the documents the runs wrote, per arm, optionally next to a reference corpus.

    python3 eval/score_style.py default,agents
    python3 eval/score_style.py default,agents --baseline claude_turns.jsonl --dump runs/view.txt
    python3 eval/score_style.py default,agents --docs --prefix d_ --baseline claude_docs.json

--baseline takes a JSON list of strings, or JSONL with a "final" field (e.g. analysis/extract_turns.py output
filtered to the model you want to imitate). Densities are per 1,000 characters, rates are % of replies.
--docs scores the .md/.html/.txt files each run created or changed instead of the replies; HTML tags and fenced
code are stripped, and every count is per 1,000 Chinese characters, since one long document hits any pattern.
"""
import argparse
import html
import json
import re
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DENSITY = {
    "讲证据/判断状态": r"依据|证据|实测|验证|确认|核实|判断|结论|说明",
    "书面虚词": r"截至|尚|均|仍|即|此次|该(?!怎|不|是)|相关|对应",
    "口语词": r"还|都|就|得|吧|挺|有点",
    # a clause that opens with a negation: "不切换账号、不自动踢设备", "未当作已发生的有效队数"
    "否定分句": r"(?:^|[，。；：、\n（(])\s*(?:也|都|并|仍|还|就|均|则)?(?:不(?!同|少|多|仅|但|过|然|论|管|久|断)|未|没有?|无需|并非|非)",
}
RATE = {
    "拦截句": r"不能(直接|据此|只|说|视为|当成|当作|认为|算|把|完全|保证|证明|确定|确认|判断)|不代表|不意味|不等于|并不是|并非|准确(地)?说|严格(来)?说|更准确|目前只能|只能说",
    "待验证/尚未": r"尚未|尚无|仍需|仍未|有待|需要.{0,6}(实测|验证|确认)",
    "没做/没改尾巴": r"以[^。\n]{1,12}为准|这次没|没有运行|未运行|没有执行|未修改|没改|未提交|未上传",
    "有表格": r"(?m)^\|.*\|\s*$",
    "整句加粗": r"\*\*[^*\n]{12,}[。；]\*\*|\*\*[^*\n]{12,}\*\*[。；]",
}
# colloquial / written synonym pairs; the share of the colloquial side separates Claude from GPT clearly in Chinese
PAIRS = [
    (r"(?<![使采调作费信引适应通实有没不专备可])用(?!户|例|于|途|量|法|时)", r"使用|采用"),
    (r"所以", r"因此|因而"),
    (r"现在", r"目前|当前"),
    (r"还没|没有.{0,3}(过|完)", r"尚未|尚无|尚不"),
    (r"删掉|去掉", r"删除|移除"),
    (r"加上|加了|加个|加一", r"增加|新增|添加"),
    (r"跑", r"运行|执行"),
    (r"还(?!原|有)", r"仍"),
]


def ending(t):
    s = ([x for x in re.split(r"(?<=[。！？\n])", t.strip()) if len(x.strip()) > 3] or [""])[-1]
    if re.search(r"[？?]\s*$", s):
        return "ask"
    if re.search(r"我(接着|继续|这就|马上|再|会|先|去)|接下来|下一步|然后再", s):
        return "next"
    if re.search(r"所以|因此|总之|总的来说|整体|综上|概括", s):
        return "summary"
    return "other"


def plain(text):
    if re.search(r"<(html|body|p|div|table)\b", text):
        text = re.sub(r"(?s)<(script|style)[^>]*>.*?</\1>", "", text)
        text = html.unescape(re.sub(r"<[^>]+>", "\n", text))
    return re.sub(r"(?s)```.*?```", "", text)


def metrics(texts, docs=False):
    n = max(1, len(texts))
    chars = max(1, sum(len(re.findall(r"[\u4e00-\u9fff]", t)) if docs else len(t) for t in texts))
    m = {k + "/1k": sum(len(re.findall(p, t)) for t in texts) / chars * 1000 for k, p in DENSITY.items()}
    if docs:
        m |= {k + "/1k": sum(len(re.findall(p, t)) for t in texts) / chars * 1000 for k, p in RATE.items()}
    else:
        m |= {k + "%": 100 * sum(bool(re.search(p, t)) for t in texts) / n for k, p in RATE.items()}
    a = sum(len(re.findall(p, t)) for t in texts for p, _ in PAIRS)
    b = sum(len(re.findall(q, t)) for t in texts for _, q in PAIRS)
    m["口语词对占比%"] = 100 * a / max(1, a + b)
    m["每条粗体"] = sum(t.count("**") // 2 for t in texts) / n
    m["每条链接"] = sum(len(re.findall(r"\]\(https?://", t)) for t in texts) / n
    m["中位长度"] = sorted(map(len, texts))[len(texts) // 2] if texts else 0
    m["结尾=下一步/提问%"] = 100 * sum(ending(t) in ("next", "ask") for t in texts) / n
    m["结尾=总结%"] = 100 * sum(ending(t) == "summary" for t in texts) / n
    return m


def load_baseline(path):
    text = Path(path).read_text()
    try:
        data = json.loads(text)
        return [x if isinstance(x, str) else x.get("final", "") for x in data]
    except ValueError:
        return [json.loads(l).get("final", "") for l in text.splitlines() if l.strip()]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("arms")
    ap.add_argument("--runs", default=str(REPO / "runs"))
    ap.add_argument("--prefix", default="", help="only scenario ids starting with this")
    ap.add_argument("--baseline")
    ap.add_argument("--docs", action="store_true", help="score the documents the runs wrote instead of the replies")
    ap.add_argument("--dump", help="write all replies (or documents) side by side to this file")
    a = ap.parse_args()
    arms = a.arms.split(",")
    data = defaultdict(lambda: defaultdict(list))
    for f in sorted(Path(a.runs).glob("*.json")):
        r = json.loads(f.read_text())
        if r.get("rc") == 0 and r["arm"] in arms and r["id"].startswith(a.prefix):
            data[r["arm"]][r["id"]].append(r)
    texts = (lambda r: [plain(t) for t in r.get("written", {}).values()]) if a.docs else \
        (lambda r: [t["final"] or "" for t in r["turns"]])
    groups = {"baseline": load_baseline(a.baseline)} if a.baseline else {}
    if a.docs and a.baseline:
        groups["baseline"] = [plain(t) for t in groups["baseline"]]
    groups |= {arm: [t for rs in data[arm].values() for r in rs for t in texts(r)] for arm in arms}
    ms = {g: metrics(v, a.docs) for g, v in groups.items()}
    keys = list(next(iter(ms.values())))
    print(f"{'':20s}" + "".join(f"{g:>12s}" for g in groups))
    print(f"{'文档数' if a.docs else '回复数':20s}" + "".join(f"{len(v):>12d}" for v in groups.values()))
    for k in keys:
        print(f"{k:20s}" + "".join(f"{ms[g][k]:>12.1f}" for g in groups))
    if a.dump:
        with open(a.dump, "w") as fh:
            for sid in sorted({i for arm in arms for i in data[arm]}):
                fh.write(f"\n\n######## {sid}\n")
                for arm in arms:
                    for r in data[arm][sid]:
                        if a.docs:
                            fh.writelines(f"\n--- {arm} #{r['rep']} {rel}\n{t}\n" for rel, t in r.get("written", {}).items())
                            continue
                        for ti, t in enumerate(r["turns"]):
                            fh.write(f"\n--- {arm} #{r['rep']}{' (follow-up)' if ti else ''}\nU: {t['user'][:300]}\n")
                            fh.writelines(f"[progress] {c}\n" for c in t["commentary"])
                            fh.write(f"{t['final']}\n")
        print("dumped", a.dump)


if __name__ == "__main__":
    main()
