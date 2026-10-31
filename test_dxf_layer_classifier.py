# -*- coding: utf-8 -*-
"""
dxf_layer_classifier 분류 로직 회귀 테스트 (ezdxf 불필요)

  python test_dxf_layer_classifier.py      직접 실행
  pytest test_dxf_layer_classifier.py      pytest 로 실행
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dxf_layer_classifier import Config, DIM_TEXT_RE, Item, LAYERS, classify


# ── 합성 엔티티 생성기 ────────────────────────────────────────
def line(x1, y1, x2, y2, lt="CONTINUOUS", color=7, h=""):
    return Item(dxftype="LINE", handle=h, linetype=lt, color=color,
                bbox=(min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)),
                ends=[(x1, y1), (x2, y2)],
                axis_aligned=(abs(x1 - x2) < 1e-9 or abs(y1 - y2) < 1e-9))


def circle(cx, cy, r, h=""):
    return Item(dxftype="CIRCLE", handle=h, bbox=(cx - r, cy - r, cx + r, cy + r),
                closed=True)


def rect(x1, y1, x2, y2, h=""):
    """4개의 LINE 으로 만든 축정렬 사각형"""
    return [line(x1, y1, x2, y1, h=h + "a"), line(x2, y1, x2, y2, h=h + "b"),
            line(x2, y2, x1, y2, h=h + "c"), line(x1, y2, x1, y1, h=h + "d")]


def text(x, y, s, h=""):
    return Item(dxftype="TEXT", handle=h, bbox=(x, y, x + len(s) * 3, y + 5), text=s)


def cats(items):
    return {it.handle: it.cat for it in items}


def run(items):
    classify(items, Config())
    return cats(items)


# ── 테스트 ────────────────────────────────────────────────────
def test_normal_drawing():
    """A4 틀 + 제품 1뷰 + 홀 2개 + 치수 + 중심선 + 표제란"""
    items = (rect(0, 0, 297, 210, h="F")            # 도면틀
             + rect(100, 100, 180, 160, h="P")      # 제품 외곽 80x60
             + [circle(120, 130, 5, h="H1"),
                circle(160, 130, 5, h="H2"),
                text(250, 10, "KSJ-001", h="T1"),   # 표제란(우하단)
                text(130, 170, "주기: 모따기", h="T2"),
                Item(dxftype="DIMENSION", handle="D1", bbox=(100, 80, 180, 95)),
                line(140, 95, 140, 165, lt="CENTER", h="C1")])
    c = run(items)
    assert c["Fa"] == "FRAME",   f"도면틀이 {c['Fa']}"
    assert c["Pa"] == "OUTLINE", f"제품 외곽이 {c['Pa']}"
    assert c["H1"] == "HOLE" and c["H2"] == "HOLE"
    assert c["T1"] == "FRAME",   "표제란 문자가 FRAME 이 아님"
    assert c["T2"] == "TEXT"
    assert c["D1"] == "DIM"
    assert c["C1"] == "CENTER"


def test_no_frame_product_stays_outline():
    """회귀: 도면틀 없는 1뷰 도면에서 제품 외곽이 FRAME 으로 오분류되면 안 된다"""
    items = (rect(0, 0, 200, 150, h="P")
             + [circle(50, 50, 8, h="H1"),
                circle(150, 50, 8, h="H2"),
                text(80, 100, "ABC", h="T1")])      # 외곽 내부 문자
    c = run(items)
    assert c["Pa"] == "OUTLINE", f"제품 외곽이 {c['Pa']} 로 오분류"
    assert c["H1"] == "HOLE",    f"홀이 {c['H1']} 로 오분류"
    assert c["H2"] == "HOLE"


def test_product_in_title_zone_keeps_holes():
    """회귀: 제품이 우하단(표제란 영역)에 놓여도 홀이 지워지면 안 된다"""
    items = (rect(0, 0, 297, 210, h="F")
             + rect(170, 20, 280, 45, h="P")        # 제품이 우하단 표제란 영역 안에 배치
             + [circle(225, 32, 4, h="H1"),         # 그 안의 홀
                text(10, 200, "NOTE", h="T1"),
                text(250, 5, "KSJ-002", h="T2")])   # 표제란 문자(제품 아래쪽)
    c = run(items)
    assert c["Fa"] == "FRAME"
    assert c["Pa"] == "OUTLINE", f"제품 외곽이 {c['Pa']}"
    assert c["H1"] == "HOLE",    f"제품 홀이 {c['H1']} 로 사라짐"
    assert c["T2"] == "FRAME",   "표제란 문자가 FRAME 이 아님"


def test_title_block_grid_becomes_frame():
    """표제란 격자(축정렬 직선 + 문자)는 FRAME 으로 정리되어야 한다"""
    items = (rect(0, 0, 297, 210, h="F")
             + rect(180, 5, 290, 40, h="TB")        # 표제란 테두리
             + [line(180, 22, 290, 22, h="G1"),     # 격자선
                text(200, 10, "KSJ-003", h="T1"),
                text(200, 28, "SUS304", h="T2")]
             + rect(40, 100, 120, 160, h="P")       # 제품은 좌상단
             + [circle(80, 130, 10, h="H1")])
    c = run(items)
    assert c["TBa"] == "FRAME", f"표제란 테두리가 {c['TBa']}"
    assert c["G1"] == "FRAME",  f"표제란 격자선이 {c['G1']}"
    assert c["T1"] == "FRAME" and c["T2"] == "FRAME"
    assert c["Pa"] == "OUTLINE"
    assert c["H1"] == "HOLE"


def test_every_entity_classified():
    """미분류(cat='') 가 남으면 apply_layers 에서 KeyError 가 난다"""
    cases = [
        rect(0, 0, 100, 100, h="F") + [text(50, 50, "X", h="T1")],
        [circle(0, 0, 5, h="C1")],
        [text(0, 0, "ONLY TEXT", h="T1")],
        rect(0, 0, 10, 10, h="S"),
    ]
    for i, items in enumerate(cases):
        classify(items, Config())
        bad = [it.handle for it in items if it.cat not in LAYERS]
        assert not bad, f"case{i}: 미분류 {bad}"


def test_dim_text_regex():
    """치수값 형태 문자 판정"""
    for s in ["25.4", "%%c6", "R3", "2-%%c4.5 THRU", "10%%p0.1", "45%%d",
              "(30)", "6H7", "M6", "t2.0"]:
        assert DIM_TEXT_RE.match(s), f"치수로 인식 실패: {s}"
    for s in ["A1", "SECTION A-A", "KSJ-1024", "SUS304", "1:1"]:
        assert not DIM_TEXT_RE.match(s), f"치수로 오인: {s}"


def test_balloon_number_follows_balloon():
    """회귀: 벌룬으로 인정한 원 안의 번호가 DIM 으로 남으면 안 된다"""
    items = (rect(0, 0, 200, 150, h="P")
             + [circle(100, 75, 10, h="H1"),            # 제품 내부 홀
                circle(40, 180, 4, h="B1"),             # 외곽 밖 벌룬 원
                text(39, 178, "7", h="BT")])            # 벌룬 안의 번호
    c = run(items)
    assert c["B1"] == "TEXT", f"벌룬 원이 {c['B1']}"
    assert c["BT"] == "TEXT", f"벌룬 번호가 {c['BT']} (원과 따로 분류됨)"


def test_tiny_circle_goes_to_review():
    """fiducial 크기 원이 HOLE 로 가면 지그에 핀·피난홀이 잘못 배치된다"""
    items = (rect(0, 0, 200, 150, h="P")
             + [circle(50, 50, 0.5, h="FID"),           # d1.0 fiducial
                circle(150, 50, 3, h="H1")])            # d6 실제 홀
    c = run(items)
    assert c["FID"] == "REVIEW", f"fiducial 이 {c['FID']}"
    assert c["H1"] == "HOLE",    f"실제 홀이 {c['H1']}"
    # 하한을 낮추면 홀로 인정되어야 한다
    items2 = (rect(0, 0, 200, 150, h="P") + [circle(50, 50, 0.5, h="FID")])
    classify(items2, Config(hole_min=0.5))
    assert cats(items2)["FID"] == "HOLE", "hole_min 조정이 반영되지 않음"


def test_undefined_linetype_survives_audit():
    """회귀: LTYPE 테이블에 없는 선종류가 감사에서 제거되어 중심선·숨은선
    판정이 조용히 무력화되면 안 된다. (ezdxf 필요 - 없으면 건너뜀)"""
    try:
        import ezdxf
    except ImportError:
        print("  SKIP  ezdxf 미설치", end="")
        return
    import tempfile
    from dxf_layer_classifier import extract_items, load_doc

    doc = ezdxf.new("R2010", setup=True)        # setup 은 HIDDEN 을 등록하지 않는다
    msp = doc.modelspace()
    msp.add_line((0, 0), (50, 0), dxfattribs={"layer": "0", "linetype": "HIDDEN"})
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "lt.dxf"
        doc.saveas(p)
        items = extract_items(load_doc(p))
    lts = [it.linetype.upper() for it in items if it.dxftype == "LINE"]
    assert "HIDDEN" in lts, f"선종류가 소실됨: {lts}"


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
