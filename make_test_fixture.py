# -*- coding: utf-8 -*-
"""
분류기 스트레스 테스트용 PBA 도면 생성기
=================================================================
*** 합성 테스트 데이터입니다. 실제 제품도면이 아닙니다. ***

한국성전의 실제 부품도면이 아니며, 어떤 실물 품번도 나타내지 않습니다.
dxf_layer_classifier 의 분류 경로를 의도적으로 깨뜨리기 위해 만든 도면이므로
치수·형상은 공학적 근거가 없습니다. 실제 작도 습관(색상 규칙, 표제란 구조,
블록 패턴) 검증에는 쓸 수 없습니다 - 그건 실물 도면이 있어야 합니다.

[겨냥하는 경로]
  A 라운드 코너 외곽(LINE+ARC 8개)    -> build_components 끝점 결합
  B hole_max 경계(18mm vs 35mm)       -> HOLE / FEATURE 분기
  C fiducial d1 극소 원                -> 홀 오탐
  D 벌룬(문자를 감싼 원)               -> 벌룬 판정 규칙
  E 맨숫자 풍선번호                    -> 버그3 오탐 재현
  F 미정의 선종류                      -> 버그4 수정 확인
  G 미러 하면도(extrusion=(0,0,-1))    -> 미수정 OCS 버그 재현
  H 중첩 블록                          -> explode 깊이
  I 제품이 표제란 영역에 근접          -> 버그2 수정 확인

  python make_test_fixture.py [-o 출력.dxf]
"""
import argparse
import sys

import ezdxf

L = {"layer": "0"}          # 실제 문제 상황 재현: 전부 0번 레이어


def rounded_board(msp, x, y, w, h, r, attribs):
    """라운드 코너 보드 외곽 — LINE 4 + ARC 4 가 끝점으로 이어진다 (경로 A)"""
    msp.add_line((x + r, y), (x + w - r, y), dxfattribs=attribs)
    msp.add_arc((x + w - r, y + r), r, -90, 0, dxfattribs=attribs)
    msp.add_line((x + w, y + r), (x + w, y + h - r), dxfattribs=attribs)
    msp.add_arc((x + w - r, y + h - r), r, 0, 90, dxfattribs=attribs)
    msp.add_line((x + w - r, y + h), (x + r, y + h), dxfattribs=attribs)
    msp.add_arc((x + r, y + h - r), r, 90, 180, dxfattribs=attribs)
    msp.add_line((x, y + h - r), (x, y + r), dxfattribs=attribs)
    msp.add_arc((x + r, y + r), r, 180, 270, dxfattribs=attribs)


def build(path):
    doc = ezdxf.new("R2010", setup=True)
    msp = doc.modelspace()

    # ── 도면틀 A3 + 표제란 + 개정란 ──────────────────────────
    for a, b in [((0, 0), (420, 0)), ((420, 0), (420, 297)),
                 ((420, 297), (0, 297)), ((0, 297), (0, 0))]:
        msp.add_line(a, b, dxfattribs=L)
    for a, b in [((280, 0), (280, 45)), ((280, 45), (420, 45)),
                 ((280, 15), (420, 15)), ((280, 30), (420, 30)),
                 ((350, 0), (350, 45))]:
        msp.add_line(a, b, dxfattribs=L)
    msp.add_text("SYNTHETIC TEST FIXTURE", height=4, dxfattribs=L).set_placement((288, 36))
    msp.add_text("NOT A REAL PART DRAWING", height=4, dxfattribs=L).set_placement((288, 21))
    msp.add_text("샘플 기판 (가상)", height=5, dxfattribs=L).set_placement((288, 5))
    msp.add_text("1:1", height=4, dxfattribs=L).set_placement((360, 5))
    msp.add_text("※ 합성 데이터 — 실제 제품도면 아님", height=6,
                 dxfattribs=L).set_placement((20, 270))

    # ── 상면도: 70x30 보드, R3 (경로 A) ──────────────────────
    bx, by, bw, bh = 60, 170, 70, 30
    rounded_board(msp, bx, by, bw, bh, 3, L)

    # 가이드홀 d4 · 마운팅홀 d2.5 · 피난홀 d6
    for cx, cy, d in [(8, 15, 4), (62, 15, 4),          # 가이드홀
                      (20, 5, 2.5), (50, 25, 2.5),      # 마운팅홀
                      (35, 15, 6)]:                      # 피난홀
        msp.add_circle((bx + cx, by + cy), d / 2, dxfattribs=L)

    # 경로 B: hole_max(30) 경계 — 18mm 커넥터 절개부 vs 35mm 대형 절개부
    msp.add_lwpolyline([(bx + 25, by + 21), (bx + 43, by + 21),
                        (bx + 43, by + 27), (bx + 25, by + 27)],
                       close=True, dxfattribs=L)                    # 18mm -> HOLE 기대
    msp.add_lwpolyline([(bx + 15, by + 8), (bx + 50, by + 8),
                        (bx + 50, by + 13), (bx + 15, by + 13)],
                       close=True, dxfattribs=L)                    # 35mm -> FEATURE 기대

    # 경로 C: fiducial d1
    msp.add_circle((bx + 5, by + 5), 0.5, dxfattribs=L)
    msp.add_circle((bx + 65, by + 25), 0.5, dxfattribs=L)

    # V-cut / 라우터 경로 (일점쇄선) + 중심선
    msp.add_line((bx - 10, by + 15), (bx + bw + 10, by + 15),
                 dxfattribs={**L, "linetype": "CENTER"})
    msp.add_line((bx + 35, by - 10), (bx + 35, by + bh + 10),
                 dxfattribs={**L, "linetype": "CENTER"})

    # 경로 F: LTYPE 테이블에 없는 선종류 (감사가 조용히 지우는지)
    msp.add_line((bx + 10, by + 18), (bx + 60, by + 18),
                 dxfattribs={**L, "linetype": "HIDDEN"})

    # ── 경로 G: 미러 하면도 (extrusion=(0,0,-1), 열린 폴리라인) ──
    # OCS 의 X 가 WCS 와 반대 방향이므로 OCS 좌표는 음수로 준다.
    ocs = {**L, "extrusion": (0, 0, -1)}
    msp.add_lwpolyline([(-310, 170), (-240, 170), (-240, 200)],
                       close=False, dxfattribs=ocs)
    msp.add_lwpolyline([(-240, 200), (-310, 200), (-310, 170)],
                       close=False, dxfattribs=ocs)
    msp.add_circle((-275, 185), 3, dxfattribs=ocs)
    msp.add_text("BOTTOM (MIRRORED)", height=4, dxfattribs=L).set_placement((245, 205))

    # ── 경로 I: 표제란 영역에 근접한 소형 뷰 (버그2 확인) ──────
    rounded_board(msp, 190, 55, 50, 22, 2, L)
    msp.add_circle((205, 66), 2, dxfattribs=L)
    msp.add_circle((228, 66), 2, dxfattribs=L)

    # ── 치수 · 지시선 · 풍선번호 · 해칭 ──────────────────────
    msp.add_linear_dim(base=(bx, by - 25), p1=(bx, by), p2=(bx + bw, by),
                       dxfattribs=L).render()
    msp.add_text("4-%%c4 THRU", height=4, dxfattribs=L).set_placement((bx + bw + 8, by + 24))
    msp.add_text("%%c6 ESCAPE", height=4, dxfattribs=L).set_placement((bx + bw + 8, by + 14))

    # 경로 D: 벌룬 — 문자를 감싼 원
    msp.add_circle((bx + 12, by + 42), 4, dxfattribs=L)
    msp.add_text("1", height=4, dxfattribs=L).set_placement((bx + 11, by + 40))
    # 경로 E: 맨숫자 풍선번호 (원 없음) — DIM 오탐 재현
    msp.add_text("2", height=4, dxfattribs=L).set_placement((bx + 30, by + 42))

    hatch = msp.add_hatch(color=2, dxfattribs=L)
    hatch.paths.add_polyline_path([(bx + 15, by + 8), (bx + 50, by + 8),
                                   (bx + 50, by + 13), (bx + 15, by + 13)])

    # ── 경로 H: 중첩 블록 ────────────────────────────────────
    inner = doc.blocks.new(name="FIDUCIAL_SYM")
    inner.add_circle((0, 0), 1)
    inner.add_line((-2, 0), (2, 0))
    outer = doc.blocks.new(name="MARK_GROUP")
    outer.add_blockref("FIDUCIAL_SYM", (0, 0))
    outer.add_line((0, 2), (0, 6))
    msp.add_blockref("MARK_GROUP", (bx + 50, by + 40), dxfattribs=L)

    doc.saveas(path)
    return len(list(msp))


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="분류기 스트레스 테스트용 PBA 도면 생성")
    ap.add_argument("-o", "--out", default="fixture_pba.dxf", help="출력 DXF 경로")
    args = ap.parse_args(argv)
    n = build(args.out)
    print(f"생성: {args.out}  (엔티티 {n}개, 전부 레이어 0)")
    print("*** 합성 테스트 데이터 — 실제 제품도면이 아닙니다 ***")


if __name__ == "__main__":
    main()
