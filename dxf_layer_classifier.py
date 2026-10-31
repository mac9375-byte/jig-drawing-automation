#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
제품도면 레이어 자동 분류기 (DXF Layer Auto-Classifier) v0.2
=================================================================
레이어 구분 없이(모두 0번 레이어 등) 받은 제품 도면을
지그 자동설계에 넣을 수 있도록 표준 레이어로 재분류합니다.

[분류 규칙 - 위에서부터 순서대로 적용]
  1) 엔티티 타입 : 치수/지시선 → DIM, 문자 → TEXT(치수값 형태면 DIM),
                   해칭·채움 → HATCH(작은 삼각형은 화살표로 보고 DIM), 블록 → BLOCK
  2) 선종류      : 일점쇄선 계열 → CENTER, 파선 계열 → HIDDEN
  3) 색상 힌트   : 회사별 작도 습관(예: 빨간색=중심선)을 --color-hint 로 지정
  4) 도면틀      : 전체를 감싸는 축정렬 사각형 + 우하단에 표제란 문자 → FRAME
                   (문자 조건이 없으면 사각 제품 1뷰 도면을 틀로 오인함)
  5) 연결 형상   : 끝점이 이어진 선들을 묶어 형상(컴포넌트)으로 만든 뒤
                   - 다른 형상에 포함되지 않는 큰 형상      → OUTLINE(제품 외곽, 뷰별)
                   - 외곽 안의 작은 폐곡선(≤ hole-max)      → HOLE(홀·슬롯)
                   - 그 중 극소(< hole-min)                 → REVIEW(fiducial·기호)
                   - 외곽 안의 그 밖의 형상                 → FEATURE(내부 형상)
                   - 외곽 밖의 작은 독립 형상               → REVIEW(사람 확인)
  6) 표제란 정리 : 표제란 영역의 축정렬 선·문자 → FRAME
                   단, 제품 뷰(원·호를 품은 외곽) 안의 형상은 보호한다.
                   ⑤ 뒤에 두어야 우하단에 배치된 제품의 홀이 지워지지 않음.
  7) 판단 불가    → REVIEW

[사용법]
  pip install ezdxf matplotlib
  python dxf_layer_classifier.py 제품도면.dxf --png
  python dxf_layer_classifier.py 제품도면.dxf --hole-max 25 --color-hint 1=CENTER,8=HIDDEN
  python dxf_layer_classifier.py 제품도면.dwg          (ODA File Converter 설치 필요)

[출력]
  제품도면_layered.dxf   표준 레이어로 재분류된 도면 (자동설계 입력용)
  제품도면_report.csv    엔티티별 분류 결과와 판정 근거 (엑셀에서 열림)
  제품도면_preview.png   레이어 색상 미리보기 (--png 옵션)

단위는 mm 기준입니다. 결과의 JIG_99_REVIEW 레이어만 담당자가 확인하면 됩니다.
"""
from __future__ import annotations

import argparse
import csv
import math
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

# ─────────────────────────────────────────────────────────────
# 1. 설정 — 자동설계 툴이 기대하는 레이어명으로 바꿔서 쓰십시오
# ─────────────────────────────────────────────────────────────
LAYERS = {
    # 분류키 : (레이어명,          ACI색상, 설명)
    "OUTLINE": ("JIG_01_OUTLINE", 7,   "제품 외곽"),
    "FEATURE": ("JIG_02_FEATURE", 150, "내부 형상"),
    "HOLE":    ("JIG_03_HOLE",    6,   "홀·슬롯"),
    "CENTER":  ("JIG_04_CENTER",  1,   "중심선"),
    "HIDDEN":  ("JIG_05_HIDDEN",  8,   "숨은선"),
    "DIM":     ("JIG_06_DIM",     3,   "치수·지시선"),
    "TEXT":    ("JIG_07_TEXT",    5,   "문자·주기"),
    "HATCH":   ("JIG_08_HATCH",   9,   "해칭·채움"),
    "BLOCK":   ("JIG_09_BLOCK",   200, "블록(부품·심볼)"),
    "FRAME":   ("JIG_10_FRAME",   252, "도면틀·표제란"),
    "REVIEW":  ("JIG_99_REVIEW",  30,  "판정보류 - 담당자 확인"),
}


@dataclass
class Config:
    tol: float = 0.05                 # 끝점 연결 허용오차(mm)
    hole_max: float = 30.0            # 이 크기 이하 폐곡선을 홀·슬롯으로 판정(mm)
    hole_min: float = 1.5             # 이 크기 미만은 fiducial·기호로 보고 REVIEW(mm)
    outline_min_ratio: float = 0.02   # 가장 큰 뷰 대비 이 면적비 이상이면 외곽 후보
    border_contain_ratio: float = 0.95  # 도면틀: 전체 엔티티 중 이 비율 이상을 감싸야 함
    inner_border_ratio: float = 0.80  # 안쪽 테두리: 바깥 테두리 대비 면적비
    title_zone_w: float = 0.45        # 표제란 영역(테두리 폭 대비 우측 비율)
    title_zone_h: float = 0.22        # 표제란 영역(테두리 높이 대비 하단 비율)
    arrow_max: float = 5.0            # 이 크기 이하 SOLID는 치수 화살표로 판정(mm)
    explode: bool = True              # 블록(INSERT)을 분해한 뒤 분류
    bylayer: bool = True              # 색상을 BYLAYER로 정리
    center_keywords: tuple = ("CENTER", "DASHDOT", "CHAIN", "PHANTOM", "DIVIDE",
                              "ISO04", "ISO05", "ISO08", "ISO09", "ISO10", "ISO12")
    hidden_keywords: tuple = ("HIDDEN", "DASHED", "DASH", "ISO02", "ISO03")
    color_hints: dict = field(default_factory=dict)   # 예: {1: "CENTER", 8: "HIDDEN"}


DIM_TYPES = {"DIMENSION", "ARC_DIMENSION", "LARGE_RADIAL_DIMENSION",
             "LEADER", "MULTILEADER", "MLEADER", "TOLERANCE"}
TEXT_TYPES = {"TEXT", "MTEXT", "ATTRIB", "ATTDEF"}
FILL_TYPES = {"HATCH", "SOLID", "TRACE", "WIPEOUT", "MPOLYGON"}
CURVE_TYPES = {"LINE", "ARC", "CIRCLE", "ELLIPSE", "LWPOLYLINE", "POLYLINE", "SPLINE"}

# 치수값처럼 생긴 문자 (분해된 치수): 25.4, Ø6, %%c6, R3, 2-Ø4.5 THRU, 10±0.1, 45°, (30), 6H7 …
DIM_TEXT_RE = re.compile(
    r"^\(?\s*(\d+\s*[-xX×]\s*)?"
    r"(%%[cC]|[ØøΦφ⌀]|SR|R|M|C|t|□)?\s*"
    r"\d+(\.\d+)?\s*(°|%%[dD])?\s*"
    r"([a-zA-Z]{1,2}\d{1,2})?\s*"
    r"((±|%%[pP])\s*\d+(\.\d+)?|[+\-]\s*\d+(\.\d+)?(\s*/?\s*[+\-]?\s*\d+(\.\d+)?)?)?\s*"
    r"\)?\s*(TYP\.?|THRU|EA)?\s*$"
)


# ─────────────────────────────────────────────────────────────
# 2. 분류 로직 (ezdxf와 무관한 순수 로직 — 단독 테스트 가능)
# ─────────────────────────────────────────────────────────────
@dataclass(eq=False)
class Item:
    dxftype: str
    handle: str = ""
    linetype: str = ""
    color: int = 256
    bbox: tuple | None = None          # (xmin, ymin, xmax, ymax)
    ends: list = field(default_factory=list)   # 열린 곡선의 양 끝점 [(x,y),(x,y)]
    closed: bool = False               # 원·닫힌 폴리라인
    axis_aligned: bool = False         # 수평·수직 직선으로만 구성
    text: str = ""
    cat: str = ""                      # 분류 결과
    reason: str = ""                   # 판정 근거
    ref: object = None                 # 원본 ezdxf 엔티티


@dataclass(eq=False)
class Comp:
    items: list
    bbox: tuple
    closed: bool
    axis: bool

    @property
    def area(self) -> float:
        return bb_area(self.bbox)

    @property
    def size(self) -> float:
        return max(self.bbox[2] - self.bbox[0], self.bbox[3] - self.bbox[1])


def bb_area(b):
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def bb_center(b):
    return ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0)


def bb_union(bs):
    return (min(b[0] for b in bs), min(b[1] for b in bs),
            max(b[2] for b in bs), max(b[3] for b in bs))


def bb_contains(outer, inner, tol=0.0):
    return (outer[0] - tol <= inner[0] and outer[1] - tol <= inner[1]
            and inner[2] <= outer[2] + tol and inner[3] <= outer[3] + tol)


def pt_in_bb(p, b, tol=0.0):
    return b[0] - tol <= p[0] <= b[2] + tol and b[1] - tol <= p[1] <= b[3] + tol


def title_zone(b, cfg: Config):
    """bbox b 의 우하단 표제란 영역"""
    w, h = b[2] - b[0], b[3] - b[1]
    return (b[2] - cfg.title_zone_w * w, b[1], b[2], b[1] + cfg.title_zone_h * h)


def is_axis(p, q, eps=1e-6):
    return abs(p[0] - q[0]) <= eps or abs(p[1] - q[1]) <= eps


def build_components(curves, tol):
    """끝점이 허용오차 안에서 만나는 곡선들을 하나의 형상으로 묶는다."""
    nodes, grid = [], defaultdict(list)

    def node_id(p):
        cx, cy = math.floor(p[0] / tol), math.floor(p[1] / tol)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for nid in grid.get((cx + dx, cy + dy), ()):
                    q = nodes[nid]
                    if abs(q[0] - p[0]) <= tol and abs(q[1] - p[1]) <= tol:
                        return nid
        nodes.append(p)
        grid[(cx, cy)].append(len(nodes) - 1)
        return len(nodes) - 1

    parent = list(range(len(curves)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    owner, degree, item_nodes = {}, Counter(), []
    for i, it in enumerate(curves):
        ids = []
        if not it.closed and len(it.ends) == 2:
            for p in it.ends:
                nid = node_id(p)
                ids.append(nid)
                degree[nid] += 1
                if nid in owner:
                    parent[find(i)] = find(owner[nid])
                else:
                    owner[nid] = i
        item_nodes.append(ids)

    groups = defaultdict(list)
    for i in range(len(curves)):
        groups[find(i)].append(i)

    comps, orphans = [], []
    for members in groups.values():
        its = [curves[i] for i in members]
        bbs = [it.bbox for it in its if it.bbox]
        if not bbs:
            orphans.extend(its)
            continue
        nids = {n for i in members for n in item_nodes[i]}
        if len(its) == 1 and its[0].closed:
            closed = True
        else:
            closed = bool(nids) and all(degree[n] >= 2 and degree[n] % 2 == 0 for n in nids)
        comps.append(Comp(its, bb_union(bbs), closed, all(it.axis_aligned for it in its)))
    return comps, orphans


def classify(items, cfg: Config):
    """items 각각에 cat(분류)과 reason(판정 근거)을 채운다."""

    def mark(it, cat, why):
        it.cat, it.reason = cat, why

    # ① 엔티티 타입 · ② 선종류 · ③ 색상 힌트
    curves = []
    for it in items:
        t = it.dxftype
        if t in DIM_TYPES:
            mark(it, "DIM", "치수·지시선 엔티티")
        elif t in TEXT_TYPES:
            if DIM_TEXT_RE.match(it.text.strip()):
                mark(it, "DIM", f"치수값 형태 문자 '{it.text.strip()}'")
            else:
                mark(it, "TEXT", "문자 엔티티")
        elif t in FILL_TYPES:
            if (t in ("SOLID", "TRACE") and it.bbox
                    and math.dist(it.bbox[:2], it.bbox[2:]) <= cfg.arrow_max):
                mark(it, "DIM", "작은 채움 삼각형(치수 화살표 추정)")
            else:
                mark(it, "HATCH", "해칭·채움 엔티티")
        elif t == "INSERT":
            mark(it, "BLOCK", "분해하지 않은 블록")
        elif t in CURVE_TYPES:
            lt = (it.linetype or "").upper()
            hint = cfg.color_hints.get(it.color)
            if any(k in lt for k in cfg.center_keywords):
                mark(it, "CENTER", f"선종류 {lt}")
            elif any(k in lt for k in cfg.hidden_keywords):
                mark(it, "HIDDEN", f"선종류 {lt}")
            elif hint in LAYERS:
                mark(it, hint, f"색상 힌트 ACI {it.color}")
            elif not it.bbox:
                mark(it, "REVIEW", "형상 범위 계산 불가")
            else:
                curves.append(it)
        else:
            mark(it, "REVIEW", f"분류 규칙 없는 엔티티 {t}")

    comps, orphans = build_components(curves, cfg.tol)
    for it in orphans:
        mark(it, "REVIEW", "형상 범위 계산 불가")

    # ④ 도면틀(테두리) 탐지
    frames = []
    text_items = [it for it in items if it.dxftype in TEXT_TYPES and it.bbox]
    for c in sorted(comps, key=lambda c: c.area, reverse=True)[:4]:
        if not c.axis or c.area <= 0:
            continue
        if frames and c.area < cfg.inner_border_ratio * frames[0].area:
            break
        own = set(c.items).union(*(f.items for f in frames))   # 자신·이미 찾은 테두리 제외
        pts = [bb_center(it.bbox) for it in items if it.bbox and it not in own]
        if not pts:
            continue
        ratio = sum(pt_in_bb(p, c.bbox) for p in pts) / len(pts)
        # 문자가 "안에 있는지"가 아니라 "우하단 표제란에 있는지"를 본다.
        # 사각 제품을 1뷰로 그린 도면(틀 없음)을 도면틀로 오인하지 않기 위한 조건.
        has_title = any(pt_in_bb(bb_center(t.bbox), title_zone(c.bbox, cfg))
                        for t in text_items)
        if ratio >= cfg.border_contain_ratio and has_title:
            frames.append(c)
            for it in c.items:
                mark(it, "FRAME", f"도면 테두리(전체의 {ratio:.0%} 포함)")

    rest = [c for c in comps if c not in frames]

    # ⑤ 제품 외곽 · 홀 · 내부 형상
    host_of, outlines = {}, []
    if rest:
        max_area = max(c.area for c in rest)
        big = sorted((c for c in rest if c.area > 0 and c.area >= cfg.outline_min_ratio * max_area),
                     key=lambda c: c.area)
        big_set = set(big)
        for c in rest:
            host = next((o for o in big if o is not c and o.area > c.area
                         and bb_contains(o.bbox, c.bbox, cfg.tol)), None)
            host_of[c] = host
            if c in big_set and host is None:
                cat, why = "OUTLINE", f"독립된 큰 형상(뷰 외곽, {c.size:.1f}mm)"
                outlines.append(c)
            elif host is not None:
                if c.closed and c.size < cfg.hole_min:
                    # fiducial·기호를 홀로 넘기면 지그에 핀·피난홀이 잘못 배치된다.
                    cat, why = "REVIEW", (f"외곽 내부 극소 폐곡선 {c.size:.1f}mm"
                                          f" < {cfg.hole_min:g} (fiducial·기호 추정)")
                elif c.closed and c.size <= cfg.hole_max:
                    cat, why = "HOLE", f"외곽 내부 폐곡선 {c.size:.1f}mm ≤ {cfg.hole_max:g}"
                else:
                    cat, why = "FEATURE", "외곽 내부 형상"
            elif c.closed and len(c.items) == 1 and c.items[0].dxftype == "CIRCLE" and (
                    balloon := [t for t in text_items
                                if pt_in_bb(bb_center(t.bbox), c.bbox)]):
                cat, why = "TEXT", "문자를 감싼 원(벌룬)"
                for t in balloon:       # 번호도 같이 TEXT 로 — 규칙 ① 의 치수 오판을 덮는다
                    mark(t, "TEXT", "벌룬 안의 번호")
            else:
                cat, why = "REVIEW", "외곽 밖 독립 형상(분해된 치수선·기호 추정)"
            for it in c.items:
                mark(it, cat, why)

    # ⑥ 표제란 영역 정리 — ⑤ 뒤에 두어 제품 뷰에 속한 홀·형상을 지우지 않는다.
    #    표제란 격자는 모두 축정렬 직선이므로, 원·호를 품은 외곽만 제품 뷰로 본다.
    if frames:
        zone = title_zone(frames[-1].bbox, cfg)     # 안쪽 테두리 기준
        products = [o for o in outlines
                    if any(not c.axis and host_of.get(c) is o for c in rest)]

        def in_product(b):
            return any(bb_contains(o.bbox, b, cfg.tol) for o in products)

        for c in rest:
            if c.axis and bb_contains(zone, c.bbox, cfg.tol) and not in_product(c.bbox):
                for it in c.items:
                    mark(it, "FRAME", "표제란 영역 내 선")
        for it in items:
            if it.cat in ("TEXT", "DIM", "BLOCK", "HATCH") and it.bbox \
                    and bb_contains(zone, it.bbox, cfg.tol) and not in_product(it.bbox):
                mark(it, "FRAME", "표제란 영역 내 문자·블록")
    return items


# ─────────────────────────────────────────────────────────────
# 3. DXF 입출력 (ezdxf)
# ─────────────────────────────────────────────────────────────
def load_doc(path: Path):
    if path.suffix.lower() == ".dwg":
        from ezdxf.addons import odafc
        try:
            return odafc.readfile(str(path))
        except Exception as ex:
            sys.exit(f"[오류] DWG 변환 실패: {ex}\n"
                     " → ODA File Converter를 설치하거나, CAD에서 DXF로 저장한 뒤 실행하십시오.")
    import ezdxf
    from ezdxf import recover
    doc, auditor = recover.readfile(str(path))
    if auditor.has_errors:
        print(f"[알림] 도면 오류 {len(auditor.errors)}건을 자동 복구했습니다.")
    # Auditor 는 LTYPE 테이블에 없는 선종류를 조용히 지운다. 선종류는 중심선·
    # 숨은선 판정의 1차 근거이므로, 구조 오류가 없으면 감사 없이 다시 읽어 살린다.
    dropped = sum("undefined linetype" in f.message for f in auditor.fixes)
    if dropped and not auditor.has_errors:
        try:
            doc = ezdxf.readfile(str(path))
            print(f"[알림] 정의되지 않은 선종류 {dropped}건 — 선종류 판정을 살리기 위해"
                  " 감사 없이 다시 읽었습니다.")
            return doc
        except Exception as ex:
            print(f"[경고] 선종류 보존 재읽기 실패: {ex}")
    if dropped:
        print(f"[경고] 정의되지 않은 선종류 {dropped}건이 제거되었습니다."
              " 중심선·숨은선이 내부 형상(FEATURE)으로 분류될 수 있습니다.\n"
              "  --color-hint 로 색상 기준을 주거나, CAD 에서 선종류를 정의해"
              " 다시 내보내십시오.")
    return doc


def explode_inserts(msp, max_depth=4):
    count = 0
    for _ in range(max_depth):
        inserts = list(msp.query("INSERT"))
        if not inserts:
            break
        before = count
        for ins in inserts:
            try:
                ins.explode()
                count += 1
            except Exception:
                pass            # 비균등 축척 등 분해 불가 블록은 BLOCK으로 남김
        if count == before:
            break
    return count


def _layer(doc, name):
    try:
        return doc.layers.get(name)
    except Exception:
        return None


def _effective(e, doc):
    lt = e.dxf.get("linetype", "BYLAYER") or "BYLAYER"
    color = e.dxf.get("color", 256)
    lay = _layer(doc, e.dxf.get("layer", "0"))
    if lt.upper() == "BYLAYER":
        lt = lay.dxf.get("linetype", "CONTINUOUS") if lay else "CONTINUOUS"
    if color == 256 and lay is not None:
        color = abs(lay.dxf.get("color", 7))
    return lt, color


def _bbox(e):
    from ezdxf import bbox as ezbbox
    try:
        ext = ezbbox.extents([e], fast=True)
        if ext.has_data:
            return (ext.extmin.x, ext.extmin.y, ext.extmax.x, ext.extmax.y)
    except Exception:
        pass
    return None


def _xy(v):
    return (float(v[0]), float(v[1]))


def _geometry(e):
    """(ends, closed, axis_aligned) 반환"""
    t = e.dxftype()
    if t == "LINE":
        s, d = _xy(e.dxf.start), _xy(e.dxf.end)
        return [s, d], False, is_axis(s, d)
    if t == "ARC":
        return [_xy(e.start_point), _xy(e.end_point)], False, False
    if t == "CIRCLE":
        return [], True, False
    if t == "ELLIPSE":
        span = (e.dxf.end_param - e.dxf.start_param) % math.tau
        if span < 1e-9 or abs(span - math.tau) < 1e-9:
            return [], True, False
        return [_xy(e.start_point), _xy(e.end_point)], False, False
    if t == "LWPOLYLINE":
        pts = e.get_points("xyb")
        if len(pts) < 2:
            return [], False, False
        segs = list(zip(pts, pts[1:])) + ([(pts[-1], pts[0])] if e.closed else [])
        axis = all(abs(p[2]) < 1e-9 and is_axis(p, q) for p, q in segs)
        if e.closed:
            return [], True, axis
        return [_xy(pts[0]), _xy(pts[-1])], False, axis
    if t == "POLYLINE":
        if e.is_polygon_mesh or e.is_poly_face_mesh:
            return [], False, False
        vs = list(e.vertices)
        if len(vs) < 2:
            return [], False, False
        pts = [_xy(v.dxf.location) for v in vs]
        bulge = any(abs(v.dxf.get("bulge", 0)) > 1e-9 for v in vs)
        segs = list(zip(pts, pts[1:])) + ([(pts[-1], pts[0])] if e.is_closed else [])
        axis = not bulge and all(is_axis(p, q) for p, q in segs)
        if e.is_closed:
            return [], True, axis
        return [pts[0], pts[-1]], False, axis
    if t == "SPLINE":
        pts = list(e.fit_points) or list(e.control_points)
        if e.closed:
            return [], True, False
        if len(pts) < 2:
            return [], False, False
        return [_xy(pts[0]), _xy(pts[-1])], False, False
    return [], False, False


def extract_items(doc):
    items = []
    for e in doc.modelspace():
        t = e.dxftype()
        lt, color = _effective(e, doc)
        it = Item(dxftype=t, handle=e.dxf.get("handle", ""), linetype=lt,
                  color=color, bbox=_bbox(e), ref=e)
        if t in CURVE_TYPES:
            try:
                it.ends, it.closed, it.axis_aligned = _geometry(e)
            except Exception:
                it.ends, it.closed = [], False
        elif t == "MTEXT":
            it.text = e.plain_text()
        elif t in TEXT_TYPES:
            it.text = e.dxf.get("text", "")
        items.append(it)
    return items


def apply_layers(doc, items, cfg: Config):
    for name, color, desc in LAYERS.values():
        if name in doc.layers:
            lay = doc.layers.get(name)
            lay.dxf.color = color
        else:
            try:
                lay = doc.layers.add(name, color=color)
            except AttributeError:          # 구버전 ezdxf
                lay = doc.layers.new(name, dxfattribs={"color": color})
        try:
            lay.description = desc
        except Exception:
            pass
    for it in items:
        e = it.ref
        e.dxf.layer = LAYERS[it.cat][0]
        if cfg.bylayer:
            e.dxf.color = 256
            if e.dxf.hasattr("true_color"):
                e.dxf.discard("true_color")


def write_report(items, csv_path: Path):
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["handle", "엔티티", "분류", "레이어", "판정근거", "원래색상(ACI)", "선종류", "문자"])
        for it in items:
            w.writerow([it.handle, it.dxftype, LAYERS[it.cat][2], LAYERS[it.cat][0],
                        it.reason, it.color, it.linetype, it.text[:40]])


CJK_FONTS = ("malgun.ttf", "gulim.ttc", "batang.ttc",       # Windows
             "NanumGothic.ttf", "NotoSansCJK-Regular.ttc",  # Linux
             "AppleGothic.ttf")                             # macOS


def _use_cjk_font(doc):
    """미리보기는 담당자가 눈으로 보는 것이므로 한글이 깨지면 안 된다.
    STYLE 기본 폰트 'txt' 는 arial.ttf 로 매핑되어 한글 글리프가 없다.
    doc.saveas() 뒤에 호출되므로 저장된 DXF 에는 영향이 없다."""
    try:
        from ezdxf.fonts import fonts
    except Exception:
        return None
    for ttf in CJK_FONTS:
        try:
            face = fonts.get_font_face(ttf)
        except Exception:
            continue
        # 모르는 이름에는 대체 폰트를 돌려주므로 실제로 찾았는지 확인한다
        if not face or face.filename.lower() != ttf.lower():
            continue
        for style in doc.styles:
            style.dxf.font = ttf
        return face.family
    return None


def save_preview(doc, png_path: Path):
    try:
        if not _use_cjk_font(doc):
            print("[알림] 한글 폰트를 찾지 못해 미리보기의 한글이 깨질 수 있습니다.")
        from ezdxf.addons.drawing import matplotlib as dm
        dm.qsave(doc.modelspace(), str(png_path), bg="#FFFFFF", dpi=200)
        return True
    except Exception as ex:
        print(f"[알림] 미리보기 생성 실패({ex}) — matplotlib 설치 여부를 확인하십시오.")
        return False


def print_summary(items, cfg: Config):
    total = len(items)
    cnt = Counter(it.cat for it in items)
    print(f"\n엔티티 {total}개 분류 결과")
    print("-" * 46)
    for key, (name, _, desc) in LAYERS.items():
        if cnt[key]:
            print(f"  {name:<16} {desc:<14} {cnt[key]:>6}개")
    print("-" * 46)
    auto = 1 - cnt["REVIEW"] / total if total else 0
    print(f"  자동 분류율 {auto:.1%}  (확인 필요 {cnt['REVIEW']}개)")
    if not cnt["FRAME"]:
        print("\n[경고] 도면틀을 찾지 못했습니다. 표제란이 우하단에 없거나 배치(Layout)"
              " 공간에 있는 도면일 수 있습니다.\n"
              "  틀 선이 JIG_01_OUTLINE 으로 들어갔는지 보고서에서 확인하십시오.")
    colors = Counter(it.color for it in items if it.dxftype in CURVE_TYPES)
    if colors:
        top = ", ".join(f"ACI {c}: {n}개" for c, n in colors.most_common(6))
        print(f"\n[참고] 선 색상 분포 — {top}")
        print("  색상으로 선 용도를 구분하는 도면이면 --color-hint 1=CENTER 처럼 지정하십시오.")


def parse_hints(text):
    hints = {}
    for part in filter(None, (text or "").split(",")):
        aci, cat = part.split("=")
        cat = cat.strip().upper()
        if cat not in LAYERS:
            sys.exit(f"[오류] --color-hint 분류명 '{cat}' 은 {list(LAYERS)} 중 하나여야 합니다.")
        hints[int(aci)] = cat
    return hints


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="제품도면 레이어 자동 분류기 (지그 자동설계 전처리)")
    ap.add_argument("input", help="입력 도면 (.dxf 또는 .dwg)")
    ap.add_argument("-o", "--out", help="출력 DXF 경로 (기본: 원본명_layered.dxf)")
    ap.add_argument("--hole-max", type=float, default=Config.hole_max, help="홀 판정 최대 크기 mm")
    ap.add_argument("--hole-min", type=float, default=Config.hole_min,
                    help="이 크기 미만 폐곡선은 fiducial·기호로 보고 REVIEW mm")
    ap.add_argument("--tol", type=float, default=Config.tol, help="끝점 연결 허용오차 mm")
    ap.add_argument("--outline-ratio", type=float, default=Config.outline_min_ratio,
                    help="외곽 판정 최소 면적비")
    ap.add_argument("--color-hint", default="", help="색상→분류 지정 예) 1=CENTER,8=HIDDEN")
    ap.add_argument("--keep-blocks", action="store_true", help="블록을 분해하지 않음")
    ap.add_argument("--keep-color", action="store_true", help="원래 색상 유지(BYLAYER 정리 안 함)")
    ap.add_argument("--png", action="store_true", help="레이어 색상 미리보기 PNG 생성")
    args = ap.parse_args(argv)

    if args.hole_min > args.hole_max:
        sys.exit(f"[오류] --hole-min({args.hole_min:g}) 이 --hole-max({args.hole_max:g}) 보다 큽니다.")
    cfg = Config(tol=args.tol, hole_max=args.hole_max, hole_min=args.hole_min,
                 outline_min_ratio=args.outline_ratio,
                 explode=not args.keep_blocks, bylayer=not args.keep_color,
                 color_hints=parse_hints(args.color_hint))

    src = Path(args.input)
    if not src.exists():
        sys.exit(f"[오류] 파일이 없습니다: {src}")
    out = Path(args.out) if args.out else src.with_name(src.stem + "_layered.dxf")

    doc = load_doc(src)
    msp = doc.modelspace()
    if cfg.explode:
        n = explode_inserts(msp)
        if n:
            print(f"[알림] 블록 {n}개를 분해했습니다.")

    items = extract_items(doc)
    if not items:
        sys.exit("[오류] 모델 공간에 엔티티가 없습니다. 배치(Layout) 공간 도면인지 확인하십시오.")
    classify(items, cfg)
    apply_layers(doc, items, cfg)
    doc.saveas(out)
    csv_path = out.with_name(src.stem + "_report.csv")
    write_report(items, csv_path)

    print_summary(items, cfg)
    print(f"\n저장: {out}\n보고서: {csv_path}")
    if args.png:
        png = out.with_name(src.stem + "_preview.png")
        if save_preview(doc, png):
            print(f"미리보기: {png}")


if __name__ == "__main__":
    main()
