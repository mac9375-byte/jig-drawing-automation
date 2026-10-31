#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
JIG 기본도면 배치 엔진 (Layout Engine) v0.1 — 참조 구현
=================================================================
레이어 분류를 마친 제품도면과 설계표준 시트를 받아, 지그 기본도면을
2D DXF 로 생성합니다. 과제 설명의 "배열·피난홀·가이드홀" 배치에 해당합니다.

*** 이것은 뼈대(참조 구현)입니다. ***
  - 배치 규칙은 설계표준 시트에서 옵니다. 시트의 기본값은 더미이므로,
    담당자가 실제 설계표준으로 바꾸기 전까지 결과 형상은 의미가 없습니다.
  - 생성물은 '기본도면'이며 설계자의 최종 수정·승인을 전제로 합니다.
  - 3D 간섭 검증은 없습니다 (CAD 플랫폼 결정 후 담당자가 진행).
  - AutoCAD 등 CAD 프로그램이 설치되어 있지 않아도 동작합니다.
    생성된 DXF 를 CAD 로 열어 수정하십시오.

  python dxf_layer_classifier.py 제품도면.dxf          1) 레이어 정리
  python layout_engine.py 제품도면_layered.dxf 설계표준시트.xlsx --png
"""
from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

from rules_loader import RuleError, check_consistency, load_rules

# 입력(제품도면)에서 읽는 레이어 — dxf_layer_classifier 의 출력과 맞춘다
SRC_OUTLINE = "JIG_01_OUTLINE"
SRC_HOLE = "JIG_03_HOLE"
SRC_REVIEW = "JIG_99_REVIEW"

# 출력(지그도면) 레이어
OUT_LAYERS = {
    "PLATE":   ("JIGOUT_01_PLATE",   7,   "지그 판 외곽"),
    "PRODUCT": ("JIGOUT_02_PRODUCT", 150, "제품 윤곽(참조)"),
    "ESCAPE":  ("JIGOUT_03_ESCAPE",  6,   "피난홀"),
    "GUIDE":   ("JIGOUT_04_GUIDE",   3,   "가이드홀"),
    "WARN":    ("JIGOUT_09_WARNING", 1,   "검증 경고 — 설계자 확인"),
    "NOTE":    ("JIGOUT_10_NOTE",    5,   "주기"),
}


@dataclass
class Product:
    w: float
    h: float
    holes: list = field(default_factory=list)   # [(제품 기준 x, y, 직경)]
    note: list = field(default_factory=list)    # 담당자에게 알릴 사항


@dataclass
class Plan:
    product: Product
    plate_w: float
    plate_h: float
    pitch_x: float
    pitch_y: float
    cells: list = field(default_factory=list)    # [(원점x, 원점y)]
    escapes: list = field(default_factory=list)  # [(x, y, 직경)]
    guides: list = field(default_factory=list)   # [(x, y, 직경)]


def _bbox_of(entity):
    """엔티티의 WCS 경계상자 (xmin, ymin, xmax, ymax). 못 구하면 None."""
    from ezdxf import bbox as ezbbox
    b = ezbbox.extents([entity], fast=False)
    if not b.has_data:
        return None
    return (b.extmin.x, b.extmin.y, b.extmax.x, b.extmax.y)


def _cluster(boxes, pad=1.0):
    """겹치거나 맞닿은 경계상자끼리 묶는다 — 도면의 '뷰' 단위를 찾기 위함."""
    groups = []            # [[합쳐진 bbox, [인덱스...]], ...]
    for i, b in enumerate(boxes):
        hit = [g for g in groups
               if not (g[0][2] + pad < b[0] or b[2] + pad < g[0][0]
                       or g[0][3] + pad < b[1] or b[3] + pad < g[0][1])]
        merged = [min(b[0], *(g[0][0] for g in hit)) if hit else b[0],
                  min(b[1], *(g[0][1] for g in hit)) if hit else b[1],
                  max(b[2], *(g[0][2] for g in hit)) if hit else b[2],
                  max(b[3], *(g[0][3] for g in hit)) if hit else b[3]]
        members = [i] + [i2 for g in hit for i2 in g[1]]
        for g in hit:
            groups.remove(g)
        groups.append([tuple(merged), members])
    return groups


def _hole_wcs(e):
    """홀의 WCS 중심과 직경. CIRCLE 의 center 는 OCS 이므로 반드시 변환한다
    (extrusion 이 뒤집힌 미러 뷰에서 좌표 부호가 바뀐다)."""
    if e.dxftype() == "CIRCLE":
        c = e.ocs().to_wcs(e.dxf.center)
        return c.x, c.y, e.dxf.radius * 2, False
    b = _bbox_of(e)          # 슬롯 등 — 외접 크기로 근사 (extents 는 WCS)
    if b is None:
        return None
    return ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2,
            max(b[2] - b[0], b[3] - b[1]), True)


def read_product(path: Path) -> Product:
    """레이어 분류를 마친 제품도면에서 외곽 크기와 홀 위치를 읽는다.
    뷰가 여러 개면 가장 큰 뷰를 제품으로 보고 나머지는 알린다."""
    import ezdxf

    doc = ezdxf.readfile(str(path))
    msp = doc.modelspace()

    outline = [e for e in msp.query('*[layer=="%s"]' % SRC_OUTLINE)]
    boxes = [(e, _bbox_of(e)) for e in outline]
    boxes = [(e, b) for e, b in boxes if b is not None]
    if not boxes:
        raise SystemExit(
            "[오류] '%s' 레이어에 쓸 수 있는 형상이 없습니다.\n"
            "  먼저 레이어를 정리하십시오:"
            "  python dxf_layer_classifier.py 제품도면.dxf" % SRC_OUTLINE)

    groups = _cluster([b for _, b in boxes])
    groups.sort(key=lambda g: (g[0][2] - g[0][0]) * (g[0][3] - g[0][1]), reverse=True)
    x0, y0, x1, y1 = groups[0][0]

    prod = Product(w=x1 - x0, h=y1 - y0)
    if len(groups) > 1:
        others = ", ".join("%.0fx%.0f" % (g[0][2] - g[0][0], g[0][3] - g[0][1])
                           for g in groups[1:])
        prod.note.append("제품 뷰가 %d개 보입니다. 가장 큰 %.0fx%.0fmm 를 제품으로 "
                         "사용했습니다 (나머지: %s). 상면도·하면도처럼 여러 뷰가 있는 "
                         "도면이면 제품 뷰만 남기고 다시 실행하십시오."
                         % (len(groups), prod.w, prod.h, others))

    for e in msp.query('*[layer=="%s"]' % SRC_HOLE):
        got = _hole_wcs(e)
        if got is None:
            continue
        cx, cy, dia, approx = got
        if not (x0 <= cx <= x1 and y0 <= cy <= y1):
            continue                      # 선택한 뷰 밖의 홀은 제외
        prod.holes.append((cx - x0, cy - y0, dia))
        if approx:
            prod.note.append("%s 홀을 원 %.1fmm 로 근사했습니다 — 설계자 확인 필요"
                             % (e.dxftype(), dia))

    n_review = len(list(msp.query('*[layer=="%s"]' % SRC_REVIEW)))
    if n_review:
        prod.note.append("제품도면에 판정보류(%s) %d개가 남아 있습니다 — "
                         "먼저 정리하지 않으면 홀이 누락될 수 있습니다"
                         % (SRC_REVIEW, n_review))
    return prod


def plan_layout(prod: Product, r: dict) -> Plan:
    """설계표준에 따라 배열·피난홀·가이드홀 위치를 계산한다."""
    pitch_x = r["array_pitch_x"] or (prod.w + r["array_gap"])
    pitch_y = r["array_pitch_y"] or (prod.h + r["array_gap"])
    cols, rows, m = r["array_cols"], r["array_rows"], r["plate_margin"]

    span_x = (cols - 1) * pitch_x + prod.w
    span_y = (rows - 1) * pitch_y + prod.h
    plan = Plan(product=prod, plate_w=span_x + 2 * m, plate_h=span_y + 2 * m,
                pitch_x=pitch_x, pitch_y=pitch_y)

    for j in range(rows):
        for i in range(cols):
            plan.cells.append((m + i * pitch_x, m + j * pitch_y))

    if r["escape_enable"]:
        for ox, oy in plan.cells:
            for hx, hy, hd in prod.holes:
                dia = max(hd + r["escape_dia_offset"], r["escape_min_dia"])
                plan.escapes.append((ox + hx, oy + hy, dia))

    d, e = r["guide_dia"], r["guide_edge_dist"]
    corners = [(e, e), (plan.plate_w - e, plan.plate_h - e)]
    if r["guide_count"] == 4:
        corners += [(plan.plate_w - e, e), (e, plan.plate_h - e)]
    plan.guides = [(x, y, d) for x, y in corners]
    return plan


def validate(plan: Plan, r: dict) -> list:
    """최소 살두께 위반을 찾는다. [(x, y, 사유)] 를 돌려준다."""
    w = r["min_wall"]
    bad = []
    holes = ([(x, y, d, "피난홀") for x, y, d in plan.escapes]
             + [(x, y, d, "가이드홀") for x, y, d in plan.guides])

    for x, y, d, kind in holes:
        edge = min(x - d / 2, y - d / 2,
                   plan.plate_w - x - d / 2, plan.plate_h - y - d / 2)
        if edge < w:
            bad.append((x, y, "%s이 판 모서리에서 %.1fmm — 최소 %gmm" % (kind, edge, w)))

    for a in range(len(holes)):
        xa, ya, da, ka = holes[a]
        for b in range(a + 1, len(holes)):
            xb, yb, db, kb = holes[b]
            gap = math.dist((xa, ya), (xb, yb)) - (da + db) / 2
            if gap < w:
                bad.append(((xa + xb) / 2, (ya + yb) / 2,
                            "%s-%s 간격 %.1fmm — 최소 %gmm" % (ka, kb, gap, w)))
    return bad


def write_dxf(plan: Plan, r: dict, bad: list, out: Path, jig_type: str):
    import ezdxf

    doc = ezdxf.new("R2010", setup=True)
    msp = doc.modelspace()
    for name, color, desc in OUT_LAYERS.values():
        lay = doc.layers.add(name, color=color)
        try:
            lay.description = desc
        except Exception:
            pass

    def L(key):
        return {"layer": OUT_LAYERS[key][0]}

    msp.add_lwpolyline([(0, 0), (plan.plate_w, 0), (plan.plate_w, plan.plate_h),
                        (0, plan.plate_h)], close=True, dxfattribs=L("PLATE"))
    for ox, oy in plan.cells:
        msp.add_lwpolyline([(ox, oy), (ox + plan.product.w, oy),
                            (ox + plan.product.w, oy + plan.product.h),
                            (ox, oy + plan.product.h)],
                           close=True, dxfattribs=L("PRODUCT"))
    for x, y, d in plan.escapes:
        msp.add_circle((x, y), d / 2, dxfattribs=L("ESCAPE"))
    for x, y, d in plan.guides:
        msp.add_circle((x, y), d / 2, dxfattribs=L("GUIDE"))

    for x, y, why in bad:        # 경고를 도면 위에 직접 표시한다
        msp.add_circle((x, y), 4, dxfattribs=L("WARN"))
        msp.add_text(why, height=2.5, dxfattribs=L("WARN")).set_placement((x + 5, y + 5))

    lines = ["※ 자동 생성된 기본도면입니다. 설계자의 수정·승인 후 사용하십시오.",
             "지그 타입: %s    판 %.1f x %.1f x t%g"
             % (jig_type, plan.plate_w, plan.plate_h, r["plate_thickness"]),
             "배열 %dx%d    피치 %.1f x %.1f"
             % (r["array_cols"], r["array_rows"], plan.pitch_x, plan.pitch_y),
             "피난홀 %d개    가이드홀 %d개" % (len(plan.escapes), len(plan.guides))]
    lines += ["[확인] %s" % n for n in plan.product.note]
    if bad:
        lines.append("[경고] 최소 살두께 위반 %d건 — %s 레이어 확인"
                     % (len(bad), OUT_LAYERS["WARN"][0]))

    for i, s in enumerate(lines):
        msp.add_text(s, height=4, dxfattribs=L("NOTE")).set_placement(
            (0, plan.plate_h + 20 + (len(lines) - i) * 7))

    doc.saveas(out)


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="JIG 기본도면 배치 엔진 (참조 구현)")
    ap.add_argument("product", help="레이어 정리를 마친 제품도면 (_layered.dxf)")
    ap.add_argument("rules", help="설계표준 시트 (.xlsx)")
    ap.add_argument("-o", "--out", help="출력 DXF (기본: 원본명_jig.dxf)")
    ap.add_argument("--jig-type", default="미지정", help="예: SMT 수대")
    ap.add_argument("--png", action="store_true", help="미리보기 PNG 생성")
    args = ap.parse_args(argv)

    try:
        rules = load_rules(Path(args.rules))
    except RuleError as ex:
        print("[설계표준 시트에 고칠 곳이 있습니다]")
        for p in ex.problems:
            print("  - %s" % p)
        sys.exit(1)
    for w in check_consistency(rules):
        print("[경고] %s" % w)

    src = Path(args.product)
    out = Path(args.out) if args.out else src.with_name(src.stem + "_jig.dxf")

    prod = read_product(src)
    plan = plan_layout(prod, rules)
    bad = validate(plan, rules)
    write_dxf(plan, rules, bad, out, args.jig_type)

    print("\n제품 %.1f x %.1f mm, 홀 %d개" % (prod.w, prod.h, len(prod.holes)))
    print("지그 판 %.1f x %.1f mm  (배열 %dx%d)"
          % (plan.plate_w, plan.plate_h, rules["array_cols"], rules["array_rows"]))
    print("피난홀 %d개, 가이드홀 %d개" % (len(plan.escapes), len(plan.guides)))
    for n in prod.note:
        print("  [확인] %s" % n)
    if bad:
        print("\n[경고] 최소 살두께 위반 %d건 — 도면의 %s 레이어를 확인하십시오"
              % (len(bad), OUT_LAYERS["WARN"][0]))
        for x, y, why in bad[:5]:
            print("    (%.1f, %.1f) %s" % (x, y, why))
        if len(bad) > 5:
            print("    ... 외 %d건" % (len(bad) - 5))
    print("\n저장: %s" % out)
    print("※ 자동 생성된 기본도면입니다. 설계자의 수정·승인이 필요합니다.")

    if args.png:
        import ezdxf
        from dxf_layer_classifier import save_preview
        png = out.with_suffix(".png")
        if save_preview(ezdxf.readfile(str(out)), png):
            print("미리보기: %s" % png)


if __name__ == "__main__":
    main()
