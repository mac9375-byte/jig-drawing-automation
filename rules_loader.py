#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
설계표준 시트 로더 (Design Rule Loader) v0.1
=================================================================
설계표준 시트(.xlsx)를 읽어 배치 엔진이 쓸 수 있는 값으로 바꿉니다.
시트를 채우는 사람은 CAD 설계 담당자이므로, 오류 메시지는 반드시
"몇 행의 무엇이 왜 잘못되었는지"를 한국어로 알려줍니다.

이 파일이 설계표준 항목의 **단일 출처**입니다.
항목을 추가·변경하면 make_rule_sheet.py 가 만드는 시트도 함께 바뀝니다.

  python rules_loader.py 설계표준시트.xlsx      시트 검사만 수행
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Rule:
    code: str          # 시트의 항목코드 (영문, 고정)
    name: str          # 담당자가 읽는 항목명
    kind: str          # number | int | bool
    default: object    # 예시 시트에 들어갈 더미값
    unit: str
    desc: str
    lo: float | None = None     # 허용 최솟값(포함)
    hi: float | None = None     # 허용 최댓값(포함)


# ─────────────────────────────────────────────────────────────
# 설계표준 항목 정의 — 과제 설명의 "배열·피난홀·가이드홀" 기준
#   ※ default 는 전부 더미값입니다. 실제 값은 담당자가 채웁니다.
# ─────────────────────────────────────────────────────────────
RULE_SPEC: tuple[Rule, ...] = (
    # ── 지그 판 ──
    Rule("plate_margin", "지그 판 여유", "number", 15.0, "mm",
         "제품 배열 바깥쪽 끝에서 지그 판 모서리까지의 거리", lo=0),
    Rule("plate_thickness", "지그 판 두께", "number", 10.0, "mm",
         "2D 도면에는 그려지지 않고 표제란에만 기록됩니다. 3D 전환 시 사용", lo=0),

    # ── 배열 ──
    Rule("array_cols", "배열 열 수", "int", 2, "개",
         "가로 방향으로 제품을 몇 개 배치할지", lo=1, hi=50),
    Rule("array_rows", "배열 행 수", "int", 2, "개",
         "세로 방향으로 제품을 몇 개 배치할지", lo=1, hi=50),
    Rule("array_gap", "제품 간 간격", "number", 8.0, "mm",
         "이웃한 제품 외곽 사이의 최소 거리. 피치를 자동 계산할 때 씁니다", lo=0),
    Rule("array_pitch_x", "가로 피치", "number", 0.0, "mm",
         "0 이면 (제품 가로폭 + 제품 간 간격)으로 자동 계산합니다", lo=0),
    Rule("array_pitch_y", "세로 피치", "number", 0.0, "mm",
         "0 이면 (제품 세로폭 + 제품 간 간격)으로 자동 계산합니다", lo=0),

    # ── 피난홀 ──
    Rule("escape_enable", "피난홀 사용", "bool", True, "예/아니오",
         "제품 홀 위치에 피난홀을 뚫을지 여부"),
    Rule("escape_dia_offset", "피난홀 직경 여유", "number", 2.0, "mm",
         "제품 홀 직경에 이 값을 더한 것이 피난홀 직경이 됩니다", lo=0),
    Rule("escape_min_dia", "피난홀 최소 직경", "number", 4.0, "mm",
         "계산된 직경이 이 값보다 작으면 이 값으로 올립니다", lo=0),

    # ── 가이드홀 ──
    Rule("guide_dia", "가이드홀 직경", "number", 6.0, "mm",
         "지그를 설비에 정렬할 때 쓰는 기준 홀의 직경", lo=0),
    Rule("guide_edge_dist", "가이드홀 모서리 거리", "number", 10.0, "mm",
         "지그 판 모서리에서 가이드홀 중심까지의 거리", lo=0),
    Rule("guide_count", "가이드홀 개수", "int", 4, "개",
         "2 이면 대각 2개, 4 이면 네 모서리에 배치합니다", lo=2, hi=4),

    # ── 검증 ──
    Rule("min_wall", "최소 살두께", "number", 3.0, "mm",
         "홀과 홀, 홀과 판 모서리 사이에 남아야 하는 최소 두께. "
         "이보다 얇으면 검증에서 경고합니다", lo=0),
)

RULE_BY_CODE = {r.code: r for r in RULE_SPEC}

SHEET_NAME = "설계표준"
HEADERS = ("항목코드", "항목명", "값", "단위", "설명")
TRUE_WORDS = {"예", "y", "yes", "true", "1", "사용", "o", "on"}
FALSE_WORDS = {"아니오", "아니요", "n", "no", "false", "0", "미사용", "x", "off"}


class RuleError(Exception):
    """담당자에게 그대로 보여줄 수 있는 한국어 오류 모음"""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("\n".join(problems))


def _to_bool(raw, where):
    if isinstance(raw, bool):
        return raw
    s = str(raw).strip().lower()
    if s in TRUE_WORDS:
        return True
    if s in FALSE_WORDS:
        return False
    raise ValueError(f"{where} '예' 또는 '아니오' 로 적어주십시오 (현재 값: {raw!r})")


def _to_number(raw, rule, where):
    if isinstance(raw, bool) or raw is None or str(raw).strip() == "":
        raise ValueError(f"{where} 값이 비어 있습니다")
    try:
        v = float(str(raw).strip())
    except ValueError:
        raise ValueError(f"{where} 숫자여야 하는데 '{raw}' 라고 적혀 있습니다") from None
    if rule.kind == "int":
        if v != int(v):
            raise ValueError(f"{where} 정수여야 합니다 (현재 값: {raw})")
        v = int(v)
    if rule.lo is not None and v < rule.lo:
        raise ValueError(f"{where} {rule.lo:g}{rule.unit} 이상이어야 합니다 (현재 값: {v:g})")
    if rule.hi is not None and v > rule.hi:
        raise ValueError(f"{where} {rule.hi:g}{rule.unit} 이하여야 합니다 (현재 값: {v:g})")
    return v


def load_rules(path: Path) -> dict:
    """설계표준 시트를 읽어 {항목코드: 값} 으로 돌려준다.
    잘못된 곳이 있으면 전부 모아 RuleError 로 올린다 (하나씩 고치게 하지 않는다)."""
    import openpyxl

    if not path.exists():
        raise RuleError([f"설계표준 시트를 찾을 수 없습니다: {path}"])
    try:
        wb = openpyxl.load_workbook(path, data_only=True)
    except Exception as ex:
        raise RuleError([f"엑셀 파일을 열 수 없습니다: {ex}"]) from None

    ws = wb[SHEET_NAME] if SHEET_NAME in wb.sheetnames else wb.worksheets[0]

    # 헤더 행 찾기 — 담당자가 위에 제목 행을 추가해도 견디게 한다
    head_row = None
    for row in ws.iter_rows(min_row=1, max_row=20):
        values = [str(c.value).strip() if c.value is not None else "" for c in row]
        if "항목코드" in values and "값" in values:
            head_row = row[0].row
            col_code = values.index("항목코드") + 1
            col_value = values.index("값") + 1
            break
    if head_row is None:
        raise RuleError([
            f"'{ws.title}' 시트에서 머리글을 찾지 못했습니다.",
            "  '항목코드' 와 '값' 이라는 머리글이 같은 행에 있어야 합니다.",
            "  시트를 새로 받으려면: python make_rule_sheet.py",
        ])

    found, problems = {}, []
    seen = set()        # 값이 틀려도 '행은 있었다' — 없다고 또 알리지 않기 위해
    for row in ws.iter_rows(min_row=head_row + 1):
        code = row[col_code - 1].value
        if code is None or not str(code).strip():
            continue
        code = str(code).strip()
        n = row[0].row
        if code not in RULE_BY_CODE:
            problems.append(f"{n}행: 알 수 없는 항목코드 '{code}' 입니다. "
                            f"이 행은 무시됩니다 — 오타가 아닌지 확인하십시오")
            continue
        if code in seen:
            problems.append(f"{n}행: 항목코드 '{code}' 가 중복됩니다")
            continue
        seen.add(code)
        rule = RULE_BY_CODE[code]
        raw = row[col_value - 1].value
        where = f"{n}행 '{rule.name}' —"
        try:
            found[code] = (_to_bool(raw, where) if rule.kind == "bool"
                           else _to_number(raw, rule, where))
        except ValueError as ex:
            problems.append(str(ex))

    for rule in RULE_SPEC:
        if rule.code not in seen:
            problems.append(f"'{rule.name}'({rule.code}) 항목이 시트에 없습니다 — "
                            f"행을 지웠다면 되살리십시오")

    if problems:
        raise RuleError(problems)
    return found


def check_consistency(rules: dict) -> list[str]:
    """값 하나하나는 맞지만 조합이 이상한 경우를 잡는다 (경고, 중단하지 않음)"""
    warn = []
    if rules["escape_enable"] and rules["escape_min_dia"] <= rules["min_wall"]:
        warn.append("피난홀 최소 직경이 최소 살두께보다 작거나 같습니다 — "
                    "홀 주변이 너무 얇아질 수 있습니다")
    for axis, pitch in (("가로", "array_pitch_x"), ("세로", "array_pitch_y")):
        if rules[pitch] and rules[pitch] < rules["array_gap"]:
            warn.append(f"{axis} 피치({rules[pitch]:g}mm)가 제품 간 간격"
                        f"({rules['array_gap']:g}mm)보다 작습니다 — 제품이 겹칩니다")
    if rules["guide_edge_dist"] < rules["guide_dia"] / 2 + rules["min_wall"]:
        warn.append("가이드홀이 판 모서리에 너무 가깝습니다 — "
                    "모서리 거리를 (가이드홀 반경 + 최소 살두께) 이상으로 하십시오")
    return warn


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="설계표준 시트 검사")
    ap.add_argument("sheet", help="설계표준 시트 (.xlsx)")
    args = ap.parse_args(argv)

    try:
        rules = load_rules(Path(args.sheet))
    except RuleError as ex:
        print("[설계표준 시트에 고칠 곳이 있습니다]")
        for p in ex.problems:
            print(f"  - {p}")
        sys.exit(1)

    print(f"설계표준 {len(rules)}개 항목을 모두 읽었습니다.")
    for w in check_consistency(rules):
        print(f"  [경고] {w}")
    print("\n[읽은 값]")
    for rule in RULE_SPEC:
        v = rules[rule.code]
        shown = ("예" if v else "아니오") if rule.kind == "bool" else f"{v:g}"
        print(f"  {rule.name:<18}{shown:>8} {rule.unit}")


if __name__ == "__main__":
    main()
