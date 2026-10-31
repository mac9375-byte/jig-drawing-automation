#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
JIG 기본도면 만들기 — 담당자용 화면
=================================================================
명령 프롬프트를 쓰지 않고 마우스로 실행하기 위한 간단한 창입니다.
안에서 하는 일은 아래 두 명령과 똑같습니다.

  python dxf_layer_classifier.py 제품도면.dxf --png
  python layout_engine.py 제품도면_layered.dxf 설계표준시트.xlsx --png

서버도 설치도 필요 없습니다. 이 PC 안에서만 동작합니다.

  실행:  지그도면_만들기.bat  을 더블클릭
        (또는)  python gui.py
"""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

HERE = Path(__file__).resolve().parent

NOTICE = ("※ 설계표준 시트의 값이 예시(더미)인 동안에는 "
          "만들어지는 지그 형상에 공학적 의미가 없습니다.\n"
          "   실제 설계표준으로 바꾼 뒤에 사용하십시오.")


def run_py(script: str, *args) -> subprocess.Popen:
    """같은 폴더의 파이썬 스크립트를 UTF-8 로 실행한다.
    -X utf8 을 주지 않으면 Windows 콘솔(cp949)에서 한글이 깨진다."""
    cmd = [sys.executable, "-X", "utf8", "-u", str(HERE / script), *map(str, args)]
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    return subprocess.Popen(cmd, cwd=str(HERE), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace",
                            creationflags=flags)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("JIG 기본도면 만들기")
        self.geometry("820x640")
        self.minsize(720, 560)
        self.q: queue.Queue = queue.Queue()
        self.busy = False
        self.last_out: Path | None = None

        pad = {"padx": 10, "pady": 6}
        top = ttk.Frame(self)
        top.pack(fill="x", **pad)
        top.columnconfigure(1, weight=1)

        self.v_product = tk.StringVar()
        self.v_rules = tk.StringVar()
        self.v_type = tk.StringVar(value="SMT 수대")

        self._row(top, 0, "제품도면 (.dxf)", self.v_product, self.pick_product)
        self._row(top, 1, "설계표준 시트 (.xlsx)", self.v_rules, self.pick_rules)

        ttk.Label(top, text="지그 타입").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Entry(top, textvariable=self.v_type).grid(row=2, column=1, sticky="ew",
                                                      padx=6, pady=4)

        bar = ttk.Frame(self)
        bar.pack(fill="x", **pad)
        self.btn_run = ttk.Button(bar, text="지그 도면 만들기", command=self.run_pipeline)
        self.btn_run.pack(side="left")
        ttk.Button(bar, text="설계표준 시트 새로 만들기",
                   command=self.make_sheet).pack(side="left", padx=6)
        ttk.Button(bar, text="설계표준 시트 검사",
                   command=self.check_sheet).pack(side="left")
        self.btn_open = ttk.Button(bar, text="결과 폴더 열기", command=self.open_folder,
                                   state="disabled")
        self.btn_open.pack(side="right")

        note = ttk.Label(self, text=NOTICE, foreground="#8a5a00", justify="left")
        note.pack(fill="x", padx=10)

        ttk.Label(self, text="진행 내용").pack(anchor="w", padx=10, pady=(8, 0))
        self.log = scrolledtext.ScrolledText(self, height=20, wrap="word")
        self.log.pack(fill="both", expand=True, padx=10, pady=(2, 4))
        self.log.configure(state="disabled")

        self.status = ttk.Label(self, text="준비됨", anchor="w", relief="sunken")
        self.status.pack(fill="x", side="bottom")

        self.after(100, self._drain)
        self.say("제품도면과 설계표준 시트를 고른 뒤 [지그 도면 만들기] 를 누르십시오.")
        self.say("설계표준 시트가 아직 없다면 [설계표준 시트 새로 만들기] 를 먼저 누르십시오.\n")

    def _row(self, parent, r, label, var, cmd):
        ttk.Label(parent, text=label).grid(row=r, column=0, sticky="w", pady=4)
        ttk.Entry(parent, textvariable=var).grid(row=r, column=1, sticky="ew",
                                                 padx=6, pady=4)
        ttk.Button(parent, text="찾아보기", command=cmd).grid(row=r, column=2, pady=4)

    # ── 출력 ────────────────────────────────────────────────
    def say(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _drain(self):
        while True:
            try:
                kind, payload = self.q.get_nowait()
            except queue.Empty:
                break
            if kind == "line":
                self.say(payload)
            elif kind == "done":
                self._finish(payload)
        self.after(100, self._drain)

    # ── 파일 선택 ───────────────────────────────────────────
    def pick_product(self):
        p = filedialog.askopenfilename(title="제품도면 선택",
                                       filetypes=[("도면 파일", "*.dxf *.dwg"),
                                                  ("모든 파일", "*.*")])
        if p:
            self.v_product.set(p)

    def pick_rules(self):
        p = filedialog.askopenfilename(title="설계표준 시트 선택",
                                       filetypes=[("엑셀 파일", "*.xlsx"),
                                                  ("모든 파일", "*.*")])
        if p:
            self.v_rules.set(p)

    def open_folder(self):
        if not self.last_out:
            return
        folder = self.last_out.parent
        if os.name == "nt":
            os.startfile(folder)                      # noqa: S606
        else:
            subprocess.Popen(["xdg-open", str(folder)])

    # ── 작업 실행 ───────────────────────────────────────────
    def _start(self, steps, label):
        """steps: [(설명, 스크립트, 인자들), ...] 를 순서대로 실행"""
        if self.busy:
            return
        self.busy = True
        self.btn_run.configure(state="disabled")
        self.status.configure(text=f"{label} 실행 중…")
        threading.Thread(target=self._worker, args=(steps,), daemon=True).start()

    def _worker(self, steps):
        ok = True
        for desc, script, args in steps:
            self.q.put(("line", f"── {desc} " + "─" * max(0, 46 - len(desc))))
            try:
                proc = run_py(script, *args)
            except Exception as ex:
                self.q.put(("line", f"[오류] 실행하지 못했습니다: {ex}"))
                ok = False
                break
            for line in proc.stdout:
                self.q.put(("line", line.rstrip()))
            if proc.wait() != 0:
                self.q.put(("line", "\n[중단] 위 메시지를 확인하고 고친 뒤 다시 실행하십시오.\n"))
                ok = False
                break
            self.q.put(("line", ""))
        self.q.put(("done", ok))

    def _finish(self, ok):
        self.busy = False
        self.btn_run.configure(state="normal")
        self.status.configure(text="완료" if ok else "중단됨 — 메시지를 확인하십시오")
        if ok and self.last_out and self.last_out.exists():
            self.btn_open.configure(state="normal")
            self.say(f"완료되었습니다: {self.last_out.name}")
            self.say("※ 자동 생성된 기본도면입니다. CAD 로 열어 수정·승인하십시오.")

    # ── 버튼 동작 ───────────────────────────────────────────
    def make_sheet(self):
        p = filedialog.asksaveasfilename(title="설계표준 시트 저장 위치",
                                         defaultextension=".xlsx",
                                         initialfile="설계표준시트.xlsx",
                                         filetypes=[("엑셀 파일", "*.xlsx")])
        if not p:
            return
        if Path(p).exists():
            Path(p).unlink()      # 저장 대화상자가 이미 덮어쓰기를 확인했다
        self.last_out = None
        self.btn_open.configure(state="disabled")
        self._start([("설계표준 시트 생성", "make_rule_sheet.py",
                      ["-o", p, "--jig-type", self.v_type.get() or "미지정"])],
                    "시트 생성")
        self.v_rules.set(p)

    def check_sheet(self):
        if not self.v_rules.get():
            messagebox.showinfo("안내", "설계표준 시트를 먼저 고르십시오.")
            return
        self._start([("설계표준 시트 검사", "rules_loader.py", [self.v_rules.get()])],
                    "시트 검사")

    def run_pipeline(self):
        prod, rules = self.v_product.get().strip(), self.v_rules.get().strip()
        if not prod or not Path(prod).exists():
            messagebox.showinfo("안내", "제품도면을 고르십시오.")
            return
        if not rules or not Path(rules).exists():
            messagebox.showinfo("안내", "설계표준 시트를 고르십시오.")
            return

        src = Path(prod)
        layered = src.with_name(src.stem + "_layered.dxf")
        self.last_out = layered.with_name(layered.stem + "_jig.dxf")
        self.btn_open.configure(state="disabled")
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

        self._start([
            ("1단계 · 제품도면 레이어 정리", "dxf_layer_classifier.py", [str(src), "--png"]),
            ("2단계 · 지그 기본도면 생성", "layout_engine.py",
             [str(layered), rules, "--jig-type", self.v_type.get() or "미지정", "--png"]),
        ], "도면 생성")


if __name__ == "__main__":
    App().mainloop()
