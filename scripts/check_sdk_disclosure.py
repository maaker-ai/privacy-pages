#!/usr/bin/env python3
"""隐私政策第三方 SDK 公示 vs 全国 SDK 管理服务平台登记信息，逐字段比对。

为什么要逐字比对：应用商店（OPPO 等）的 SDK 合规检测要求隐私政策里公示的
「SDK 名称、开发者、SDK 隐私政策链接」与全国 SDK 管理服务平台（sdk.caict.ac.cn）
登记信息**完全一致**——半角/全角括号、名称里的空格、链接里的 ?spm= 与 # 锚点都算。
肉眼核对必漏，所以用本脚本守。

真相源：<app目录>/sdk-registry.json
  sdks[]               每项 package / name / developer / privacy_url
  completeness_missing 商店「完整性检测」点名的包名（这些行另外要求五列都非空）

检查对象：<app目录>/index.html 中标题含「第三方 SDK 清单」的 h2 之后的第一张表，
按表头定位列：SDK 名称 / 提供方 / 用途 / 收集的个人信息 / 隐私政策。
「SDK 名称」单元格必须只写登记名（按单元格全文精确匹配）。

完整性：registry 里列出的每个 SDK 所在行，五列都非空（隐私政策列还必须有链接）。

用法：
    python3 scripts/check_sdk_disclosure.py youcai
任一 FAIL 退出码 1。
"""

from __future__ import annotations

import difflib
import json
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECTION_KEYWORD = "第三方 SDK 清单"
COL_NAME, COL_DEV, COL_USE, COL_INFO, COL_LINK = "SDK 名称", "提供方", "用途", "收集的个人信息", "隐私政策"
COLUMNS = [COL_NAME, COL_DEV, COL_USE, COL_INFO, COL_LINK]


class _SdkTableParser(HTMLParser):
    """抓第 5 节标题之后第一张表：每行每格的文字与 <a href> 列表。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_h2 = False
        self.h2_text = ""
        self.armed = False  # 已经过了 SDK 清单标题，等第一张表
        self.done = False
        self.in_table = False
        self.rows: list[list[dict]] = []
        self.cell: dict | None = None

    def handle_starttag(self, tag, attrs):
        if self.done:
            return
        if tag == "h2":
            self.in_h2, self.h2_text = True, ""
        elif tag == "table" and self.armed:
            self.in_table = True
        elif self.in_table and tag == "tr":
            self.rows.append([])
        elif self.in_table and tag in ("td", "th"):
            self.cell = {"tag": tag, "text": "", "hrefs": []}
        elif self.cell is not None and tag == "a":
            self.cell["hrefs"].append(dict(attrs).get("href") or "")

    def handle_endtag(self, tag):
        if self.done:
            return
        if tag == "h2" and self.in_h2:
            self.in_h2 = False
            if SECTION_KEYWORD in self.h2_text:
                self.armed = True
        elif tag in ("td", "th") and self.cell is not None:
            self.rows[-1].append(self.cell)
            self.cell = None
        elif tag == "table" and self.in_table:
            self.in_table, self.done = False, True

    def handle_data(self, data):
        if self.in_h2:
            self.h2_text += data
        if self.cell is not None:
            self.cell["text"] += data


def parse_sdk_table(html_text: str) -> list[dict]:
    p = _SdkTableParser()
    p.feed(html_text)
    if not p.rows:
        raise SystemExit(f"找不到「{SECTION_KEYWORD}」标题之后的表格")
    header = [c["text"].strip() for c in p.rows[0]]
    missing = [c for c in COLUMNS if c not in header]
    if missing:
        raise SystemExit(f"SDK 表缺少列：{missing}；实际表头 {header}")
    idx = {c: header.index(c) for c in COLUMNS}
    out = []
    for r in p.rows[1:]:
        row = {}
        for c in COLUMNS:
            cell = r[idx[c]] if idx[c] < len(r) else {"text": "", "hrefs": []}
            row[c] = cell["text"].strip()
            if c == COL_LINK:
                row["href"] = cell["hrefs"][0] if cell["hrefs"] else ""
        out.append(row)
    return out


def check(app_dir: Path) -> int:
    registry = json.loads((app_dir / "sdk-registry.json").read_text(encoding="utf-8"))
    rows = parse_sdk_table((app_dir / "index.html").read_text(encoding="utf-8"))
    by_name = {r[COL_NAME]: r for r in rows}
    fails = 0

    def report(ok: bool, label: str, detail: str = "") -> None:
        nonlocal fails
        fails += 0 if ok else 1
        print(f"{'PASS' if ok else 'FAIL'}  {label}{('  ' + detail) if detail and not ok else ''}")

    print(f"== 合规公示 SDK 信息检测（{app_dir.name}/index.html vs sdk-registry.json）==")
    for sdk in registry["sdks"]:
        tag = f"[{sdk['package']}]"
        row = by_name.get(sdk["name"])
        guess = None
        if row is None:
            close = difflib.get_close_matches(sdk["name"], list(by_name), n=1, cutoff=0.4)
            guess = by_name[close[0]] if close else None
        actual = row or guess or {}
        report(row is not None, f"{tag} SDK名称",
               f"{actual.get(COL_NAME, '（无此行）')!r} → {sdk['name']!r}")
        report(actual.get(COL_DEV) == sdk["developer"], f"{tag} 开发者",
               f"{actual.get(COL_DEV, '（无此行）')!r} → {sdk['developer']!r}")
        report(actual.get("href") == sdk["privacy_url"], f"{tag} 隐私政策链接",
               f"{actual.get('href', '（无此行）')!r} → {sdk['privacy_url']!r}")

    print("== 合规公示 SDK 完整性检测（名称/开发者/收集信息/用途/隐私政策链接 五项非空）==")
    for sdk in registry["sdks"]:
        flag = "（商店点名）" if sdk["package"] in registry.get("completeness_missing", []) else ""
        row = by_name.get(sdk["name"])
        if row is None:
            report(False, f"[{sdk['package']}]{flag} 完整性", f"表中没有名称为 {sdk['name']!r} 的行")
            continue
        empty = [c for c in COLUMNS if not row[c]] + ([] if row["href"] else ["隐私政策链接"])
        report(not empty, f"[{sdk['package']}]{flag} 完整性", f"空列：{empty}")

    print(f"== {'全部 PASS' if fails == 0 else f'{fails} 项 FAIL'} ==")
    return 1 if fails else 0


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    app_dir = Path(sys.argv[1])
    if not app_dir.is_absolute():
        app_dir = ROOT / app_dir
    return check(app_dir)


if __name__ == "__main__":
    sys.exit(main())
