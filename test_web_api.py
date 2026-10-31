# -*- coding: utf-8 -*-
"""웹 API 회귀 테스트 (서버를 띄우지 않고 TestClient 로 호출)

  python test_web_api.py
  pytest test_web_api.py
"""
import io
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


def _client():
    from fastapi.testclient import TestClient
    import web_app
    return TestClient(web_app.app)


def _samples(tmp):
    """시험용 제품도면과 설계표준 시트를 만든다"""
    from make_rule_sheet import build
    from make_test_fixture import build as build_dxf
    dxf = Path(tmp) / "제품도면_샘플.dxf"
    xlsx = Path(tmp) / "설계표준시트.xlsx"
    build_dxf(str(dxf))
    build(xlsx, "시험")
    return dxf, xlsx


def test_health_and_index():
    c = _client()
    assert c.get("/api/health").json()["ok"] is True
    r = c.get("/")
    assert r.status_code == 200 and "JIG" in r.text


def test_run_full_pipeline():
    """업로드 -> 레이어 정리 -> 배치 -> 결과. CLI 와 같은 값이 나와야 한다"""
    c = _client()
    with tempfile.TemporaryDirectory() as tmp:
        dxf, xlsx = _samples(tmp)
        r = c.post("/api/run", files={
            "product": (dxf.name, dxf.read_bytes(), "application/octet-stream"),
            "rules": (xlsx.name, xlsx.read_bytes(), "application/octet-stream"),
        }, data={"jig_type": "시험", "hole_max": "30", "hole_min": "1.5"})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["ok"] is True, d
        assert (d["product"]["w"], d["product"]["h"]) == (70.0, 30.0), d["product"]
        assert d["jig"]["cells"] == 4 and d["jig"]["guides"] == 4
        assert d["classify"]["review"] > 0, "판정보류가 보고되지 않음"
        assert len(d["files"]) == 5

        # 한글 파일명이 유지되는지 + 실제로 받아지는지
        for name in d["files"].values():
            assert "제품도면_샘플" in name, f"파일명이 깨짐: {name}"
            fr = c.get(f"/api/file/{d['job']}/{name}")
            assert fr.status_code == 200 and len(fr.content) > 0, name


def test_bad_rule_sheet_reports_all_problems():
    """설계표준이 틀리면 배치로 넘어가지 않고 문제를 전부 알려야 한다"""
    import openpyxl
    c = _client()
    with tempfile.TemporaryDirectory() as tmp:
        dxf, xlsx = _samples(tmp)
        wb = openpyxl.load_workbook(xlsx)
        ws = wb["설계표준"]
        for row in ws.iter_rows(min_row=5):
            if row[0].value == "plate_margin":
                row[2].value = "열다섯"
            if row[0].value == "guide_count":
                row[2].value = 99
        wb.save(xlsx)
        r = c.post("/api/run", files={
            "product": (dxf.name, dxf.read_bytes(), "application/octet-stream"),
            "rules": (xlsx.name, xlsx.read_bytes(), "application/octet-stream"),
        }, data={"jig_type": "시험"})
        d = r.json()
        assert d["ok"] is False and d["stage"] == "설계표준", d
        assert len(d["problems"]) == 2, d["problems"]


def test_path_traversal_blocked():
    """작업 번호나 파일명으로 다른 경로를 읽을 수 없어야 한다"""
    c = _client()
    assert c.get("/api/file/..%2F..%2Fweb_app.py/x").status_code in (400, 404)
    assert c.get("/api/file/nonexistent-job/web_app.py").status_code == 404


def test_empty_upload_rejected():
    c = _client()
    r = c.post("/api/run", files={
        "product": ("x.dxf", b"", "application/octet-stream"),
        "rules": ("y.xlsx", b"", "application/octet-stream"),
    }, data={"jig_type": "시험"})
    assert r.status_code == 400, r.status_code


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
        except Exception as ex:
            failed += 1
            print(f"  ERROR {t.__name__}: {type(ex).__name__}: {ex}")
    print(f"\n{len(tests) - failed}/{len(tests)} 통과")
    sys.exit(1 if failed else 0)
