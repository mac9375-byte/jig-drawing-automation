"use strict";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

// ── 빈 설계표준 시트 내려받기 ──────────────────────────────
$("btn-sheet").addEventListener("click", async () => {
  const fd = new FormData();
  fd.append("jig_type", $("jigtype").value || "지그 타입을 적으십시오");
  try {
    const r = await fetch("/api/rule-sheet", { method: "POST", body: fd });
    const j = await r.json();
    if (!j.ok) throw new Error("시트를 만들지 못했습니다");
    location.href = `/api/file/${j.job}/${encodeURIComponent(j.file)}?download=true`;
  } catch (e) {
    alert("시트를 만들지 못했습니다: " + e.message);
  }
});

// ── 실행 ──────────────────────────────────────────────────
$("btn-run").addEventListener("click", async () => {
  const product = $("product").files[0];
  const rules = $("rules").files[0];
  if (!product) return alert("제품도면을 고르십시오.");
  if (!rules) return alert("설계표준 시트를 고르십시오.");

  const fd = new FormData();
  fd.append("product", product);
  fd.append("rules", rules);
  fd.append("jig_type", $("jigtype").value || "미지정");
  fd.append("hole_max", $("holemax").value);
  fd.append("hole_min", $("holemin").value);
  fd.append("color_hint", $("colorhint").value);

  busy(true);
  try {
    const r = await fetch("/api/run", { method: "POST", body: fd });
    if (!r.ok) {
      let detail = `서버 오류 (${r.status})`;
      try { detail = (await r.json()).detail || detail; } catch (_) {}
      throw new Error(detail);
    }
    render(await r.json());
  } catch (e) {
    render({ ok: false, stage: "실행", problems: [e.message] });
  } finally {
    busy(false);
  }
});

function busy(on) {
  $("btn-run").disabled = on;
  $("spinner").hidden = !on;
  $("btn-run").textContent = on ? "만드는 중…" : "지그 도면 만들기";
}

// ── 결과 표시 ─────────────────────────────────────────────
function render(d) {
  const card = $("result-card");
  const out = $("result");
  card.hidden = false;
  card.scrollIntoView({ behavior: "smooth", block: "start" });

  if (!d.ok) {
    out.innerHTML = `
      <div class="box bad">
        <h3>${esc(d.stage)} 단계에서 멈췄습니다</h3>
        <ul>${(d.problems || []).map((p) => `<li>${esc(p)}</li>`).join("")}</ul>
      </div>
      <p class="sub">위 내용을 고친 뒤 다시 실행하십시오.</p>`;
    return;
  }

  const f = (name) => `/api/file/${d.job}/${encodeURIComponent(name)}`;
  const parts = [];

  parts.push(`
    <div class="stats">
      <div class="stat"><b>${d.product.w} × ${d.product.h}</b><span>제품 크기 (mm)</span></div>
      <div class="stat"><b>${d.jig.plate_w} × ${d.jig.plate_h}</b><span>지그 판 (mm)</span></div>
      <div class="stat"><b>${d.jig.cells}</b><span>배열 수</span></div>
      <div class="stat"><b>${d.jig.escapes}</b><span>피난홀</span></div>
      <div class="stat"><b>${d.jig.guides}</b><span>가이드홀</span></div>
      ${d.classify.review ? `<div class="stat bad"><b>${d.classify.review}</b><span>판정보류</span></div>` : ""}
    </div>`);

  // 반드시 사람이 봐야 하는 것부터 위에 둔다
  if (d.violations.length) {
    parts.push(`
      <div class="box bad">
        <h3>최소 살두께 위반 ${d.violations.length}건 — 설계자 확인 필요</h3>
        <ul>${d.violations.slice(0, 10).map((v) =>
          `<li>(${v.x}, ${v.y}) ${esc(v.why)}</li>`).join("")}
          ${d.violations.length > 10 ? `<li>… 외 ${d.violations.length - 10}건</li>` : ""}</ul>
        <p class="sub" style="color:inherit">도면의 <code>JIGOUT_09_WARNING</code> 레이어에 표시되어 있습니다.</p>
      </div>`);
  }

  // 판정보류는 아래 product.notes 에서 배치 엔진이 이미 알려주므로 여기서 또 쓰지 않는다
  const checks = [].concat(
    d.classify.notes || [], d.product.notes || [], d.rule_warnings || []);
  if (checks.length) {
    parts.push(`
      <div class="box warn">
        <h3>확인하십시오</h3>
        <ul>${checks.map((c) => `<li>${esc(c)}</li>`).join("")}</ul>
      </div>`);
  }

  parts.push(`
    <div class="box">
      <h3>미리보기</h3>
      <div class="previews">
        <figure>
          <figcaption>지그 기본도면</figcaption>
          <img src="${f(d.files["지그 미리보기 (PNG)"])}" alt="지그 기본도면 미리보기">
        </figure>
        <figure>
          <figcaption>제품도면 레이어 분류</figcaption>
          <img src="${f(d.files["레이어 미리보기 (PNG)"])}" alt="레이어 분류 미리보기">
        </figure>
      </div>
    </div>`);

  const rows = Object.entries(d.classify.counts)
    .map(([k, v]) => `<tr><th>${esc(k)}</th><td>${v}개</td></tr>`).join("");
  parts.push(`
    <div class="box">
      <h3>레이어 분류 결과</h3>
      <table>${rows}</table>
    </div>`);

  parts.push(`
    <div class="box">
      <h3>내려받기</h3>
      <div class="files">
        ${Object.entries(d.files).map(([label, name]) =>
          `<a href="${f(name)}?download=true" download>${esc(label)}</a>`).join("")}
      </div>
      <p class="sub">※ 자동 생성된 기본도면입니다. CAD 로 열어 수정·승인하십시오.</p>
    </div>`);

  out.innerHTML = parts.join("");
}
