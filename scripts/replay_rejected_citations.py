"""离线重放：把某次真跑被拦回的原稿重新过一遍引用核对。

只读 --run 指向的作业目录（冻结输入 + 各次调用的 validation.json），
不联网、不调模型、不写回任何产物。
用法：
  .venv/Scripts/python.exe scripts/replay_rejected_citations.py --run "<daily 作业目录>"
"""
import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from review_agent.grounding import ROLE_SOURCES, _unfence, build_catalog, cite, validate_section  # noqa: E402


def main(run: Path) -> int:
    date = json.loads((run / "get_sentiment_data.json").read_text(encoding="utf-8"))["target_date"]
    values = {path.stem: json.loads(path.read_text(encoding="utf-8"))["value"]
              for path in run.glob("get_*.json")}
    catalog = build_catalog(values, date)
    lines = {record["line"] for record in catalog if record.get("line")}
    print(f"目标日 {date}，整包目录 {len(catalog)} 条")

    refused = 0
    for path in sorted(run.glob("*/validation.json")):
        row = json.loads(path.read_text(encoding="utf-8"))
        raw, reason = row.get("rejected_response") or "", row.get("reason") or ""
        print(f"\n== {path.parent}  attempt={row.get('attempt')}  正文 {len(raw)} 字")
        print("   当时被拒原因：" + reason.replace("\n", " ")[:110])
        if not raw.strip():
            print("   （空正文：这轮模型没交回内容，与引用核对无关）")
            continue
        try:
            obj = json.loads(_unfence(raw))
        except ValueError:
            print("   去掉收尾围栏仍解析不动 —— 真的结构无效")
            refused += 1
            continue
        printed = [eid for finding in obj.get("findings", []) for eid in finding["citations"]
                   if eid[3:].isdigit() and int(eid[3:]) in lines]
        if printed:
            print("   模型抄的其实是目录里的 line（材料行号）：" + "、".join(printed))
        outcomes = []
        for key in ROLE_SOURCES:
            records = cite([e for e in catalog if e["input"] in ROLE_SOURCES[key]])
            try:
                result = validate_section(obj, records)
            except Exception as exc:
                outcomes.append(str(exc))
            else:
                resolved = {r["id"]: r for r in records}
                print(f"   按新契约在 {key} 段通过（本段 {len(records)} 条）：")
                for finding, kept in zip(obj["findings"], result["findings"]):
                    print(f"     段{finding['citations']}")
                    for real in kept["citations"]:
                        row = resolved[real]
                        print(f"        -> {real[:12]}… [{row['input']}] "
                              f"{str(row.get('text') or row.get('value') or '')[:40]}")
                break
        else:
            kinds = {}
            for text in outcomes:
                key = ("引用" if "不在本段可用目录内" in text else
                       "自由生成数字" if "自由生成数字" in text else "其它")
                kinds.setdefault(key, []).append(text[:60])
            print("   五段都仍拦回：" + "、".join(kinds))
            for key, samples in kinds.items():
                print(f"     [{key}] {samples[0]}")
            refused += 1
    print(f"\n仍拦回 {refused} 份")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True)
    sys.exit(main(Path(parser.parse_args().run)))
