#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
설계표준 시트 생성기
=================================================================
담당자가 채울 빈 설계표준 시트(.xlsx)를 만듭니다.
항목 정의는 rules_loader.RULE_SPEC 하나만 보고 만들어지므로,
항목을 바꾸면 시트도 같이 바뀝니다.

  python make_rule_sheet.py                      설계표준시트.xlsx 생성
  python make_rule_sheet.py -o SMT수대.xlsx      지그 타입별로 따로 만들 때

*** 들어가는 값은 전부 더미입니다. 실제 값은 설계 담당자가 채워야 합니다. ***
"""
import argparse
import sys
from pathlib import Path

from rules_loader import HEADERS, RULE_SPEC, SHEET_NAME

NOTE = ("※ '값' 칸만 고치십시오. 항목코드는 프로그램이 찾는 이름이므로 바꾸면 안 됩니다.\n"
        "※ 지금 들어 있는 값은 전부 예시(더미)입니다. 실제 설계표준으로 바꿔주십시오.\n"
        "※ 다 채운 뒤 검사:  python rules_loader.py 이파일.xlsx")


def build(path: Path, jig_type: str):
    import openpyxl
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = SHEET_NAME

    thin = Side(style="thin", color="BBBBBB")
    box = Border(left=thin, right=thin, top=thin, bottom=thin)
    head_fill = PatternFill("solid", fgColor="D9E2F3")
    code_fill = PatternFill("solid", fgColor="F2F2F2")   # 고치지 말 것
    edit_fill = PatternFill("solid", fgColor="FFF2CC")   # 여기를 고치세요

    # 제목 + 안내
    ws["A1"] = f"JIG 설계표준 시트 — {jig_type}"
    ws["A1"].font = Font(size=14, bold=True)
    ws.merge_cells("A1:E1")
    ws["A2"] = NOTE
    ws["A2"].alignment = Alignment(wrap_text=True, vertical="top")
    ws.merge_cells("A2:E2")
    ws.row_dimensions[2].height = 52

    # 머리글 (4행) — rules_loader 가 '항목코드'/'값' 으로 찾는다
    for col, title in enumerate(HEADERS, start=1):
        c = ws.cell(row=4, column=col, value=title)
        c.font = Font(bold=True)
        c.fill = head_fill
        c.border = box
        c.alignment = Alignment(horizontal="center", vertical="center")

    for i, rule in enumerate(RULE_SPEC):
        r = 5 + i
        value = ("예" if rule.default else "아니오") if rule.kind == "bool" else rule.default
        for col, v in enumerate((rule.code, rule.name, value, rule.unit, rule.desc), start=1):
            c = ws.cell(row=r, column=col, value=v)
            c.border = box
            c.alignment = Alignment(vertical="center",
                                    wrap_text=(col == 5),
                                    horizontal="center" if col in (3, 4) else "left")
        ws.cell(row=r, column=1).fill = code_fill
        ws.cell(row=r, column=3).fill = edit_fill
        ws.row_dimensions[r].height = 30

    for col, width in zip("ABCDE", (22, 22, 12, 10, 62)):
        ws.column_dimensions[col].width = width
    ws.freeze_panes = "A5"

    wb.save(path)
    return len(RULE_SPEC)


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="설계표준 시트 생성")
    ap.add_argument("-o", "--out", default="설계표준시트.xlsx", help="출력 경로")
    ap.add_argument("--jig-type", default="지그 타입을 적으십시오",
                    help="예: SMT 수대 / TAPE JIG / 라우터 JIG")
    args = ap.parse_args(argv)

    out = Path(args.out)
    if out.exists():
        sys.exit(f"[중단] {out} 이 이미 있습니다. 담당자가 채운 내용을 덮어쓰지 않도록 "
                 "다른 이름으로 지정하거나 기존 파일을 옮기십시오.")
    n = build(out, args.jig_type)
    print(f"생성: {out}  (설계표준 {n}개 항목)")
    print("*** 값은 전부 예시입니다. 설계 담당자가 실제 값으로 바꿔야 합니다. ***")


if __name__ == "__main__":
    main()
