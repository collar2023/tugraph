"""
scripts/auto_onboard.py — 数据体检 (v1 极简版, 2026-06-20)
========================================================

边界声明 (重要):
  本工具只做"列画像体检", 回答一个问题:
    "这张表的数据质量够不够让我写 seed_*.py?"

  本工具不做的事 (本体的宪法权归架构师, 不归本工具):
    - 不建议 schema / label / 边类型
    - 不生成 db.createVertexLabelByJson 调用片段
    - 不直连 TuGraph, 不写库
    - 不替架构师决策

  看完报告后, 架构师 / 客户按 docs/ontology_seeding_guide.md §2-3
  手写 seed_*.py, 这是第 0 步不可逾越的底线.

用法:
  python3 scripts/auto_onboard.py data/客户合同.csv
  python3 scripts/auto_onboard.py data/客户合同.csv --nrows 1000
"""
from __future__ import annotations
import os
import re
import json
import html
import argparse
import datetime
from pathlib import Path
from typing import Any

import pandas as pd

# ---------- 颜色 ----------
try:
    from colorama import init as _ci, Fore, Style
    _ci()
    GREEN = Fore.GREEN; RED = Fore.RED; YELLOW = Fore.YELLOW; CYAN = Fore.CYAN; RESET = Style.RESET_ALL; BOLD = Style.BRIGHT
except ImportError:
    GREEN = RED = YELLOW = CYAN = RESET = BOLD = ""

# ============================================================
# 1. 读取
# ============================================================
def load_dataframe(path: Path, nrows: int | None = None) -> pd.DataFrame:
    """CSV / XLSX / XLS, 自动编码探测"""
    suffix = path.suffix.lower()
    if suffix == ".csv":
        for enc in ("utf-8", "gbk", "utf-8-sig", "latin-1"):
            try:
                return pd.read_csv(path, nrows=nrows, encoding=enc)
            except (UnicodeDecodeError, UnicodeError):
                continue
        raise ValueError(f"无法解码 {path}, 已尝试 utf-8/gbk/utf-8-sig/latin-1")
    elif suffix in (".xlsx", ".xls"):
        return pd.read_excel(path, nrows=nrows)
    else:
        raise ValueError(f"不支持的文件格式: {suffix} (需 .csv / .xlsx / .xls)")


# ============================================================
# 2. 列画像 (类型 / 主键特征 / 脏数据)
# ============================================================
def infer_type(series: pd.Series) -> str:
    """按值推断, 不用 pandas dtype (电话/编号都被 dtype 识别成 object)"""
    s = series.dropna()
    if s.empty:
        return "STRING"
    sample = s.head(100)
    int_re = re.compile(r"^-?\d+$")
    if all(isinstance(v, (int, float)) or (isinstance(v, str) and int_re.match((v or "").strip())) for v in sample):
        return "INT32"
    float_re = re.compile(r"^-?\d+(\.\d+)?$")
    if all(isinstance(v, (int, float)) or (isinstance(v, str) and float_re.match((v or "").strip())) for v in sample):
        return "DOUBLE"
    date_pat = re.compile(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}")
    if all(isinstance(v, str) and date_pat.match(v.strip()) for v in sample.head(5)):
        return "DATETIME"
    bool_set = {"true", "false", "0", "1", "yes", "no", "是", "否", "y", "n"}
    if all(isinstance(v, str) and v.strip().lower() in bool_set for v in sample):
        return "BOOL"
    return "STRING"


def pk_features(series: pd.Series) -> dict:
    """主键特征: 唯一性 + 非空率, 只列事实, 不下结论"""
    n = len(series)
    if n == 0:
        return {"unique_pct": 0, "non_null_pct": 0}
    n_null = series.isna().sum()
    n_unique = series.nunique(dropna=True)
    return {
        "unique_pct": round(n_unique / max(1, n - n_null), 3),
        "non_null_pct": round((n - n_null) / n, 3),
    }


def dirty_scan(series: pd.Series) -> dict:
    """脏数据扫描: 全角 / 控制字符 / 极端长度 / 前后空白 / 常量列"""
    issues = []
    s = series.dropna().astype(str)
    n = len(s)
    if n == 0:
        return {"issues": ["空列"], "score": 0}

    half = sum(1 for v in s if re.search(r"[\uFF01-\uFF5E]", v))
    if 0 < half < n:
        issues.append(f"{half}/{n} 行含全角字符")

    ctrl = sum(1 for v in s if re.search(r"[\x00-\x1f\x7f]", v))
    if ctrl:
        issues.append(f"{ctrl} 行含控制字符")

    lens = s.str.len()
    if lens.max() > 255:
        issues.append(f"最长 {lens.max()} 字符 (max_length 建议 ≥ 500)")

    ws = sum(1 for v in s if v != v.strip())
    if ws:
        issues.append(f"{ws} 行含前后空白")

    if series.nunique(dropna=True) == 1:
        issues.append("全列相同 (常量列, 建议删除)")

    score = max(0, 100 - len(issues) * 20)
    return {"issues": issues if issues else ["无"], "score": score}


# ============================================================
# 3. HTML 报告
# ============================================================
def render_html(path: Path, df: pd.DataFrame, profile: dict) -> str:
    n_rows, n_cols = df.shape
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    avg_dirty = sum(p["dirty_score"] for p in profile.values()) / max(1, len(profile))
    overall_color = "green" if avg_dirty >= 80 else ("orange" if avg_dirty >= 50 else "red")
    overall_advice = (
        "数据质量良好, 可以进入本体建模 (docs/ontology_seeding_guide.md §2-3)"
        if avg_dirty >= 80 else
        "数据质量中等, 建议先清洗脏数据列再进入本体建模"
        if avg_dirty >= 50 else
        "数据质量较差, 强烈建议先清洗再继续"
    )

    col_rows = ""
    for c in df.columns:
        p = profile[c]
        unique_color = "green" if p["unique_pct"] >= 0.99 else ("orange" if p["unique_pct"] >= 0.8 else "gray")
        dirty_color = "green" if p["dirty_score"] >= 80 else ("orange" if p["dirty_score"] >= 50 else "red")
        issues_str = html.escape(", ".join(p["dirty_issues"]))
        col_rows += f"""
        <tr>
          <td><code>{html.escape(str(c))}</code></td>
          <td>{p['type']}</td>
          <td>{p['non_null_pct']:.0%}</td>
          <td><span class="badge" style="background:{unique_color}">{p['unique_pct']:.0%}</span></td>
          <td><span class="badge" style="background:{dirty_color}">{p['dirty_score']}</span> {issues_str}</td>
        </tr>"""

    sample_html = df.head(5).to_html(index=False, classes="sample", border=0, escape=True)

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>数据体检 — {html.escape(path.name)}</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; max-width: 1000px; margin: 20px auto; padding: 0 20px; background: #f8fafc; }}
h1 {{ color: #1e293b; border-bottom: 3px solid #64748b; padding-bottom: 10px; }}
h2 {{ color: #334155; margin-top: 32px; border-left: 4px solid #64748b; padding-left: 12px; }}
.summary {{ background: white; padding: 20px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
.summary-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-top: 12px; }}
.summary-cell {{ background: #f1f5f9; padding: 12px; border-radius: 6px; text-align: center; }}
.summary-cell .num {{ font-size: 28px; font-weight: bold; color: #1e293b; }}
.summary-cell .lbl {{ font-size: 12px; color: #64748b; margin-top: 4px; }}
.badge {{ display: inline-block; padding: 2px 8px; border-radius: 10px; color: white; font-size: 12px; font-weight: bold; }}
.badge.big {{ font-size: 18px; padding: 4px 12px; }}
table {{ width: 100%; border-collapse: collapse; margin-top: 10px; background: white; }}
th, td {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid #e2e8f0; font-size: 14px; }}
th {{ background: #f1f5f9; color: #334155; font-weight: 600; }}
code {{ background: #f1f5f9; padding: 1px 6px; border-radius: 3px; font-size: 13px; color: #be185d; }}
table.sample {{ font-size: 12px; }}
table.sample th {{ background: #fef3c7; }}
.footer {{ margin-top: 40px; padding: 16px; background: #f1f5f9; border-radius: 6px; font-size: 13px; color: #475569; border-left: 4px solid #64748b; }}
.footer b {{ color: #1e293b; }}
</style>
</head>
<body>
<h1>🔍 数据体检报告</h1>
<p><b>文件:</b> <code>{html.escape(str(path))}</code> &nbsp;|&nbsp; <b>生成:</b> {ts}</p>

<div class="summary">
<h2>📈 概览</h2>
<div class="summary-grid">
  <div class="summary-cell"><div class="num">{n_rows}</div><div class="lbl">行数</div></div>
  <div class="summary-cell"><div class="num">{n_cols}</div><div class="lbl">列数</div></div>
  <div class="summary-cell"><div class="num"><span class="badge big" style="background:{overall_color}">{avg_dirty:.0f}</span></div><div class="lbl">数据健康度</div></div>
</div>
<p style="margin-top:16px;padding:12px;background:#f1f5f9;border-radius:6px">
  <b>建议:</b> {overall_advice}
</p>
</div>

<h2>🔎 样本前 5 行</h2>
{sample_html}

<h2>🧬 列画像 (类型 / 唯一率 / 脏数据)</h2>
<table>
<tr><th>列名</th><th>类型</th><th>非空率</th><th>唯一率</th><th>脏数据</th></tr>
{col_rows}
</table>

<div class="footer">
<p><b>📖 本工具的边界</b> (重要, 必读):</p>
<ul>
  <li>本工具只做<strong>列画像体检</strong>, 列出类型/唯一率/脏数据事实.</li>
  <li>本工具<strong>不建议 schema / label / 边类型</strong> — 这是本体宪法权, 归架构师.</li>
  <li>本工具<strong>不连接 TuGraph</strong>, 不写库, 不替您决策.</li>
  <li>看完报告后, 请按 <code>docs/ontology_seeding_guide.md</code> §2-3 手写 <code>seed_*.py</code>.</li>
</ul>
</div>
</body>
</html>"""


# ============================================================
# 主流程
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="数据体检 (列画像, 不做候选建议)")
    parser.add_argument("file", help="输入文件 (CSV/XLSX/XLS)")
    parser.add_argument("--nrows", type=int, default=None, help="只读前 N 行 (大文件加速)")
    parser.add_argument("--report", default="logs/auto_onboard_<ts>.html", help="HTML 报告路径 (含 <ts>)")
    args = parser.parse_args()

    print(f"{BOLD}╔══════════════════════════════════════════════════════╗")
    print(f"║  auto_onboard 极简版  ·  {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  ║")
    print(f"╚══════════════════════════════════════════════════════╝{RESET}")
    print(f"  {CYAN}边界: 只做列画像, 不建议 schema, 不连 TuGraph, 不写库{RESET}")

    path = Path(args.file).resolve()
    if not path.exists():
        print(f"{RED}✗ 文件不存在: {path}{RESET}")
        sys.exit(1)
    print(f"  {CYAN}输入: {path}{RESET}")

    print(f"\n{BOLD}── 读取 ──{RESET}")
    try:
        df = load_dataframe(path, nrows=args.nrows)
    except Exception as e:
        print(f"{RED}✗ 读取失败: {e}{RESET}")
        sys.exit(1)
    print(f"  {GREEN}✓ {df.shape[0]} 行 × {df.shape[1]} 列{RESET}")

    print(f"\n{BOLD}── 列画像 ──{RESET}")
    profile = {}
    for c in df.columns:
        col_type = infer_type(df[c])
        pk = pk_features(df[c])
        dirty = dirty_scan(df[c])
        profile[c] = {**pk, "type": col_type, "dirty_score": dirty["score"], "dirty_issues": dirty["issues"]}
        # 主键特征只列事实, 不下结论 (避免越权)
        flag = ""
        if pk["non_null_pct"] >= 0.99 and pk["unique_pct"] >= 0.99:
            flag = f" {YELLOW}(可能主键, 由架构师判断){RESET}"
        elif pk["non_null_pct"] < 0.5:
            flag = f" {RED}(空值过半, 慎用){RESET}"
        issues_brief = "无" if dirty["issues"] == ["无"] else "; ".join(dirty["issues"])[:60]
        print(f"  {c:<28} {col_type:<10} 非空={pk['non_null_pct']:.0%} 唯一={pk['unique_pct']:.0%} 脏={dirty['score']:<3}  {issues_brief}{flag}")

    avg_dirty = sum(p["dirty_score"] for p in profile.values()) / max(1, len(profile))
    print(f"\n  整体健康度: {avg_dirty:.0f}/100")

    print(f"\n{BOLD}── 生成 HTML 报告 ──{RESET}")
    html_path = Path(args.report.replace("<ts>", datetime.datetime.now().strftime("%Y%m%d_%H%M%S")))
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(render_html(path, df, profile), encoding="utf-8")
    print(f"  {GREEN}✓ {html_path}{RESET}")
    print(f"\n  {CYAN}下一步: 打开 HTML 看体检结果, 然后按 ontology_seeding_guide.md §2-3 手写 seed_*.py{RESET}\n")


if __name__ == "__main__":
    main()
