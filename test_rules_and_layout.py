# -*- coding: utf-8 -*-
"""
설계표준 로더 · 배치 엔진 회귀 테스트

  python test_rules_and_layout.py
  pytest test_rules_and_layout.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from layout_engine import Product, plan_layout, read_product, validate
from make_rule_sheet import build as build_sheet
from rules_loader import RULE_SPEC, RuleError, check_consistency, load_rules

DUMMY = {r.code: (r.default if r.kind != "bool" else bool(r.default)) for r in RULE_SPEC}


def _sheet(tmp, **overrides):
    """더미 시트를 만들고 값 일부를 덮어쓴 뒤 경로를 돌려준다"""
    import openpyxl
    p = Path(tmp) / "rules.xlsx"
    build_sheet(p, "테스트")
    if overrides:
        wb = openpyxl.load_workbook(p)
        ws = wb["설계표준"]
        for row in ws.iter_rows(min_row=5):
            code = row[0].value
            if code in overrides:
                row[2].value = overrides[code]
        wb.save(p)
    return p


# ── 설계표준 로더 ────────────────────────────────────────────
def test_generated_sheet_loads_clean():
    """기본 생성 시트는 오류도 경고도 없이 읽혀야 한다"""
    with tempfile.TemporaryDirectory() as tmp:
        rules = load_rules(_sheet(tmp))
        assert len(rules) == len(RULE_SPEC)
        warn = check_consistency(rules)
        assert not warn, f"기본 시트가 경고를 냅니다: {warn}"


def test_bad_values_all_reported_once():
    """오류는 한 번에 모아서, 같은 항목을 두 번 알리지 않아야 한다"""
    with tempfile.TemporaryDirectory() as tmp:
        p = _sheet(tmp, plate_margin="열다섯", guide_count=99, escape_enable="아마도")
        try:
            load_rules(p)
            raise AssertionError("오류가 검출되지 않았습니다")
        except RuleError as ex:
            assert len(ex.problems) == 3, f"문제 개수가 3이 아님: {ex.problems}"
            joined = "\n".join(ex.problems)
            assert "시트에 없습니다" not in joined, "값 오류가 '없음'으로도 중복 보고됨"
            assert "지그 판 여유" in joined and "가이드홀 개수" in joined


def test_missing_item_detected():
    """행을 지우면 없어진 항목을 지목해야 한다"""
    import openpyxl
    with tempfile.TemporaryDirectory() as tmp:
        p = _sheet(tmp)
        wb = openpyxl.load_workbook(p)
        ws = wb["설계표준"]
        for row in ws.iter_rows(min_row=5):
            if row[0].value == "guide_dia":
                ws.delete_rows(row[0].row)
                break
        wb.save(p)
        try:
            load_rules(p)
            raise AssertionError("누락이 검출되지 않았습니다")
        except RuleError as ex:
            assert any("guide_dia" in p_ for p_ in ex.problems), ex.problems


# ── 배치 엔진 ────────────────────────────────────────────────
def test_plate_size_and_array():
    """판 크기 = 배열 전체 + 양쪽 여유, 셀 개수 = 행 x 열"""
    r = dict(DUMMY, array_cols=3, array_rows=2, array_gap=8.0,
             array_pitch_x=0.0, array_pitch_y=0.0, plate_margin=15.0)
    plan = plan_layout(Product(w=70.0, h=30.0), r)
    assert plan.pitch_x == 78.0 and plan.pitch_y == 38.0, "피치 자동계산 오류"
    assert plan.plate_w == (3 - 1) * 78 + 70 + 30, f"판 가로 {plan.plate_w}"
    assert plan.plate_h == (2 - 1) * 38 + 30 + 30, f"판 세로 {plan.plate_h}"
    assert len(plan.cells) == 6


def test_escape_hole_diameter_rules():
    """피난홀 직경 = max(제품홀 + 여유, 최소직경)"""
    r = dict(DUMMY, array_cols=1, array_rows=1, escape_enable=True,
             escape_dia_offset=2.0, escape_min_dia=10.0)
    prod = Product(w=70.0, h=30.0, holes=[(10, 10, 4.0), (30, 10, 20.0)])
    plan = plan_layout(prod, r)
    dias = sorted(d for _, _, d in plan.escapes)
    assert dias == [10.0, 22.0], f"피난홀 직경 {dias}"


def test_escape_disabled():
    r = dict(DUMMY, array_cols=1, array_rows=1, escape_enable=False)
    plan = plan_layout(Product(w=70.0, h=30.0, holes=[(10, 10, 4.0)]), r)
    assert not plan.escapes


def test_guide_count():
    for n, expect in ((2, 2), (4, 4)):
        r = dict(DUMMY, guide_count=n, array_cols=1, array_rows=1)
        plan = plan_layout(Product(w=70.0, h=30.0), r)
        assert len(plan.guides) == expect


def test_min_wall_violation_detected():
    """홀이 너무 붙어 있으면 반드시 잡아야 한다 — 조용히 통과시키면 지그가 깨진다"""
    r = dict(DUMMY, array_cols=1, array_rows=1, min_wall=3.0,
             escape_enable=True, escape_dia_offset=0.0, escape_min_dia=10.0)
    prod = Product(w=70.0, h=30.0, holes=[(20, 15, 10.0), (28, 15, 10.0)])  # 간격 -2mm
    plan = plan_layout(prod, r)
    bad = validate(plan, r)
    assert any("피난홀-피난홀" in why for _, _, why in bad), bad


def test_no_violation_when_spaced():
    r = dict(DUMMY, array_cols=1, array_rows=1, min_wall=3.0, plate_margin=20.0,
             escape_enable=True, escape_dia_offset=0.0, escape_min_dia=6.0,
             guide_edge_dist=10.0, guide_dia=6.0)
    prod = Product(w=70.0, h=30.0, holes=[(15, 15, 6.0), (55, 15, 6.0)])
    bad = validate(plan_layout(prod, r), r)
    assert not bad, f"위반이 없어야 하는데 {bad}"


# ── DXF 읽기 (ezdxf 필요) ────────────────────────────────────
def _write_product_dxf(path, mirrored=False, two_views=False):
    import ezdxf
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    out = {"layer": "JIG_01_OUTLINE"}
    hol = {"layer": "JIG_03_HOLE"}
    msp.add_lwpolyline([(100, 100), (170, 100), (170, 130), (100, 130)],
                       close=True, dxfattribs=out)
    if mirrored:   # extrusion 뒤집힌 홀 — OCS 좌표로 넣는다 (WCS 로는 x=135)
        msp.add_circle((-135, 115), 3, dxfattribs={**hol, "extrusion": (0, 0, -1)})
    else:
        msp.add_circle((135, 115), 3, dxfattribs=hol)
    if two_views:  # 더 작은 두 번째 뷰
        msp.add_lwpolyline([(300, 100), (340, 100), (340, 120), (300, 120)],
                           close=True, dxfattribs=out)
    doc.saveas(path)


def test_mirrored_hole_reads_in_wcs():
    """extrusion 이 뒤집힌 홀의 좌표를 OCS 그대로 쓰면 음수가 나온다"""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "m.dxf"
        _write_product_dxf(p, mirrored=True)
        prod = read_product(p)
        assert len(prod.holes) == 1, f"홀을 못 읽음: {prod.holes}"
        hx, hy, _ = prod.holes[0]
        assert abs(hx - 35.0) < 1e-6 and abs(hy - 15.0) < 1e-6, \
            f"홀 위치가 {hx:.1f},{hy:.1f} — WCS 변환 누락 의심"


def test_multiple_views_picks_largest_and_warns():
    """뷰가 여러 개면 합치지 말고 가장 큰 것을 쓰되 반드시 알려야 한다"""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "v.dxf"
        _write_product_dxf(p, two_views=True)
        prod = read_product(p)
        assert (prod.w, prod.h) == (70.0, 30.0), f"뷰를 합침: {prod.w}x{prod.h}"
        assert any("뷰가" in n for n in prod.note), prod.note


# ── 직접 실행 ────────────────────────────────────────────────
if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
        except AssertionError as ex:
            failed += 1
            print(f"  FAIL  {t.__name__}: {ex}")
    print(f"\n{len(tests) - failed}/{len(tests)} 통과")
    sys.exit(1 if failed else 0)
