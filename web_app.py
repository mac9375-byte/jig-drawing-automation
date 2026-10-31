#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
JIG 설계 자동화 — 웹 서버
=================================================================
브라우저에서 제품도면을 올리면 지그 기본도면을 만들어 돌려줍니다.
처리 로직은 기존 CLI 엔진(dxf_layer_classifier, layout_engine)을
그대로 호출하므로 결과는 CLI 와 동일합니다.

  실행:  웹서버_실행.bat 더블클릭
        (또는)  python web_app.py
        브라우저에서 http://127.0.0.1:8000

[보안]
  기본값은 127.0.0.1 — 이 PC 안에서만 접속됩니다. 도면이 밖으로 나가지
  않습니다. 사내 다른 PC 에서 접속하게 하려면 --host 0.0.0.0 을 주되,
  그 순간부터 사내망 누구나 접속 가능해지므로 배포.md 를 먼저 읽으십시오.

[보존 정책]
  업로드·생성 파일은 작업 폴더에 남습니다. 오래된 작업은 자동 삭제됩니다
  (--keep-hours, 기본 24시간).
"""
from __future__ import annotations

import argparse
import datetime as dt
import shutil
import sys
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

import dxf_layer_classifier as clf
import layout_engine as eng
from rules_loader import RuleError, check_consistency, load_rules

HERE = Path(__file__).resolve().parent
STATIC = HERE / "web_static"
JOBS = HERE / "작업폴더"
KEEP_HOURS = 24.0
MAX_MB = 50

app = FastAPI(title="JIG 설계 자동화", docs_url="/api/docs")


# ─────────────────────────────────────────────────────────────
# 작업 폴더
# ─────────────────────────────────────────────────────────────
def new_job() -> Path:
    JOBS.mkdir(exist_ok=True)
    sweep_old()
    d = JOBS / f"{dt.datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}"
    d.mkdir()
    return d


def sweep_old():
    """오래된 작업 폴더를 지운다 — 고객 도면이 무한정 쌓이지 않도록."""
    cutoff = time.time() - KEEP_HOURS * 3600
    for d in JOBS.glob("*"):
        try:
            if d.is_dir() and d.stat().st_mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


def job_dir(job_id: str) -> Path:
    """경로 조작을 막고 작업 폴더를 돌려준다."""
    if not job_id or "/" in job_id or "\\" in job_id or ".." in job_id:
        raise HTTPException(400, "잘못된 작업 번호입니다")
    d = JOBS / job_id
    if not d.is_dir():
        raise HTTPException(404, "작업을 찾을 수 없습니다. 만료되었을 수 있습니다")
    return d


async def save_upload(up: UploadFile, dest: Path) -> Path:
    data = await up.read()
    if len(data) > MAX_MB * 1024 * 1024:
        raise HTTPException(413, f"파일이 너무 큽니다 (최대 {MAX_MB}MB)")
    if not data:
        raise HTTPException(400, f"빈 파일입니다: {up.filename}")
    dest.write_bytes(data)
    return dest


# ─────────────────────────────────────────────────────────────
# 처리 — CLI 엔진을 그대로 호출한다
# ─────────────────────────────────────────────────────────────
def run_classifier(src: Path, cfg: clf.Config) -> tuple[Path, dict, list]:
    """레이어 정리. (출력경로, 분류집계, 알림) 을 돌려준다."""
    notes = []
    doc = clf.load_doc(src)
    if cfg.explode:
        n = clf.explode_inserts(doc.modelspace())
        if n:
            notes.append(f"블록 {n}개를 분해했습니다")
    items = clf.extract_items(doc)
    if not items:
        raise HTTPException(400, "모델 공간에 형상이 없습니다. "
                                 "배치(Layout) 공간 도면인지 확인하십시오")
    clf.classify(items, cfg)
    clf.apply_layers(doc, items, cfg)

    out = src.with_name(src.stem + "_layered.dxf")
    doc.saveas(out)
    clf.write_report(items, out.with_name(src.stem + "_report.csv"))
    clf.save_preview(doc, out.with_name(src.stem + "_preview.png"))

    counts = {}
    for it in items:
        name, _, desc = clf.LAYERS[it.cat]
        counts[desc] = counts.get(desc, 0) + 1
    if not any(it.cat == "FRAME" for it in items):
        notes.append("도면틀을 찾지 못했습니다 — 틀 선이 제품 외곽으로 "
                     "분류되지 않았는지 보고서를 확인하십시오")
    return out, counts, notes


@app.post("/api/run")
async def api_run(
    product: UploadFile,
    rules: UploadFile,
    jig_type: str = Form("미지정"),
    hole_max: float = Form(30.0),
    hole_min: float = Form(1.5),
    color_hint: str = Form(""),
):
    d = new_job()
    p_src = await save_upload(product, d / Path(product.filename or "제품도면.dxf").name)
    p_rules = await save_upload(rules, d / Path(rules.filename or "설계표준시트.xlsx").name)

    # 1) 설계표준 — 틀리면 여기서 멈추고 전부 모아 알린다
    try:
        rule_values = load_rules(p_rules)
    except RuleError as ex:
        return {"ok": False, "stage": "설계표준", "problems": ex.problems}
    warnings = check_consistency(rule_values)

    # 2) 레이어 정리
    try:
        cfg = clf.Config(hole_max=hole_max, hole_min=hole_min,
                         color_hints=clf.parse_hints(color_hint) if color_hint else {})
    except SystemExit as ex:
        raise HTTPException(400, str(ex)) from None
    try:
        layered, counts, notes = run_classifier(p_src, cfg)
    except HTTPException:
        raise
    except Exception as ex:
        return {"ok": False, "stage": "레이어 정리", "problems": [f"{type(ex).__name__}: {ex}"]}

    review = counts.get("판정보류 - 담당자 확인", 0)

    # 3) 지그 배치
    try:
        prod = eng.read_product(layered)
        plan = eng.plan_layout(prod, rule_values)
        bad = eng.validate(plan, rule_values)
        out = layered.with_name(layered.stem + "_jig.dxf")
        eng.write_dxf(plan, rule_values, bad, out, jig_type)
        clf.save_preview(__import__("ezdxf").readfile(str(out)),
                         out.with_suffix(".png"))
    except SystemExit as ex:
        return {"ok": False, "stage": "지그 배치", "problems": [str(ex)]}
    except Exception as ex:
        return {"ok": False, "stage": "지그 배치",
                "problems": [f"{type(ex).__name__}: {ex}"]}

    return {
        "ok": True,
        "job": d.name,
        "jig_type": jig_type,
        "rule_warnings": warnings,
        "classify": {"counts": counts, "review": review, "notes": notes},
        "product": {"w": round(prod.w, 1), "h": round(prod.h, 1),
                    "holes": len(prod.holes), "notes": prod.note},
        "jig": {"plate_w": round(plan.plate_w, 1), "plate_h": round(plan.plate_h, 1),
                "pitch_x": round(plan.pitch_x, 1), "pitch_y": round(plan.pitch_y, 1),
                "cells": len(plan.cells), "escapes": len(plan.escapes),
                "guides": len(plan.guides)},
        "violations": [{"x": round(x, 1), "y": round(y, 1), "why": w} for x, y, w in bad],
        "files": {
            "지그 기본도면 (DXF)": out.name,
            "레이어 정리 도면 (DXF)": layered.name,
            "분류 보고서 (CSV)": p_src.stem + "_report.csv",
            "지그 미리보기 (PNG)": out.with_suffix(".png").name,
            "레이어 미리보기 (PNG)": p_src.stem + "_preview.png",
        },
    }


@app.post("/api/rule-sheet")
async def api_rule_sheet(jig_type: str = Form("지그 타입을 적으십시오")):
    """빈 설계표준 시트를 만들어 내려준다 (값은 전부 더미)."""
    from make_rule_sheet import build
    d = new_job()
    p = d / "설계표준시트.xlsx"
    build(p, jig_type)
    return {"ok": True, "job": d.name, "file": p.name}


@app.get("/api/file/{job_id}/{name}")
def api_file(job_id: str, name: str, download: bool = False):
    d = job_dir(job_id)
    f = d / Path(name).name           # 경로 조작 차단
    if not f.is_file():
        raise HTTPException(404, "파일을 찾을 수 없습니다")
    return FileResponse(f, filename=f.name if download else None)


@app.get("/api/health")
def api_health():
    return {"ok": True, "jobs": len(list(JOBS.glob("*"))) if JOBS.exists() else 0}


@app.get("/", response_class=HTMLResponse)
def index():
    return (STATIC / "index.html").read_text(encoding="utf-8")


app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


def main(argv=None):
    global KEEP_HOURS
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="JIG 설계 자동화 웹 서버")
    ap.add_argument("--host", default="127.0.0.1",
                    help="기본 127.0.0.1 (이 PC 전용). 사내 공개는 0.0.0.0")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--keep-hours", type=float, default=KEEP_HOURS,
                    help="작업 폴더 보존 시간")
    args = ap.parse_args(argv)
    KEEP_HOURS = args.keep_hours

    if args.host not in ("127.0.0.1", "localhost"):
        print("[경고] 이 PC 밖에서도 접속할 수 있게 엽니다.")
        print("       사내망의 누구나 업로드한 도면을 볼 수 있습니다.")
        print("       배포.md 의 '사내 공개 시 확인사항' 을 먼저 읽으십시오.\n")

    print(f"주소: http://{'127.0.0.1' if args.host == '0.0.0.0' else args.host}:{args.port}")
    print("종료하려면 이 창에서 Ctrl+C 를 누르십시오.\n")
    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
