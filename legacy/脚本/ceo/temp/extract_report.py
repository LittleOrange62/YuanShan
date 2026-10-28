#!/usr/bin/env python3
"""
extract_report.py — 从 daily_score_update.py 的完整输出中提取纯报告（去日志行）
用法：python3 extract_report.py <input.txt> <output.txt>
"""
import sys
from pathlib import Path

def extract(input_path, output_path):
    text = Path(input_path).read_text(encoding="utf-8")
    lines = text.splitlines()
    started = False
    report = []
    for line in lines:
        stripped = line.strip()
        if not started:
            if "📊 远山评分卡" in line or "📊" in stripped[:3]:
                started = True
        if started and not stripped.startswith("["):
            report.append(stripped)
    Path(output_path).write_text("\n".join(report), encoding="utf-8")
    return len(report)

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("用法: python3 extract_report.py <input.txt> <output.txt>")
        sys.exit(1)
    n = extract(sys.argv[1], sys.argv[2])
    print(f"提取了 {n} 行报告")
