// MORPH Lab 스트리밍 앱 브라우저 테스트 클라이언트.
// 메시지 규칙: docs/WEB_MESSAGES.md (원본은 각 익스텐션 CONTRACT.md)
import { AppStreamer, StreamType } from "@nvidia/omniverse-webrtc-streaming-library";

const params = new URLSearchParams(location.search);
const SERVER = params.get("server") || "127.0.0.1";
const SIGNALING_PORT = Number(params.get("port") || 49100);

const $ = (s) => document.querySelector(s);
const logEl = $("#log");
const state = { connected: false, pushes: {}, last: {}, log: [], results: [] };
window.__morph = state; // 자동화 점검용

function log(kind, text) {
  state.log.push(`${kind} ${text}`);
  const div = document.createElement("div");
  div.className = kind;
  div.textContent = `${new Date().toLocaleTimeString()} ${kind === "out" ? "→" : kind === "in" ? "←" : "·"} ${text}`;
  logEl.prepend(div);
  while (logEl.childElementCount > 300) logEl.lastChild.remove();
}

// --- morph.stream.messaging 전송 계층 ---
const pending = new Map();

// 드래그 중 초당 수십 개씩 나가는 카메라 알림은 로그에 남기지 않는다
const QUIET_OUT = new Set(["morph.camera.control.orbit", "morph.camera.control.pan", "morph.camera.control.zoom"]);
function send(env) {
  if (!(env.id === null && QUIET_OUT.has(env.type))) log("out", JSON.stringify(env));
  AppStreamer.sendMessage({ event_type: "morph.stream.in", payload: { message: JSON.stringify(env) } });
}
function notify(type, payload = {}) {
  send({ type, v: 1, id: null, payload });
}
function request(type, payload = {}, timeoutMs = 5000) {
  const id = crypto.randomUUID();
  return new Promise((resolve) => {
    const t = setTimeout(() => {
      pending.delete(id);
      resolve({ ok: false, error: "timeout", message: `no reply for ${type}` });
    }, timeoutMs);
    pending.set(id, (p) => { clearTimeout(t); resolve(p); });
    send({ type, v: 1, id, payload });
  });
}

function onCustomEvent(msg) {
  const m = typeof msg === "string" ? JSON.parse(msg) : msg;
  if (!m || m.event_type !== "morph.stream.out") return;
  let env;
  try {
    env = JSON.parse(m.payload.message);
  } catch (e) {
    log("sys", `bad envelope: ${e}`);
    return;
  }
  if (env.id && pending.has(env.id)) {
    log("in", `${env.type} ${JSON.stringify(env.payload)}`);
    pending.get(env.id)(env.payload);
    pending.delete(env.id);
    return;
  }
  state.pushes[env.type] = (state.pushes[env.type] || 0) + 1;
  state.last[env.type] = env.payload;
  if (env.type === "morph.viewport.split.state") onSplitState(env.payload);
  if (env.type === "morph.perf.monitor.report") renderPerf();
  if (env.type === "morph.stage.loader.state") onSceneState(env.payload);
  if (env.type === "morph.annotation.overlay.state") onAnnoState(env.payload);
  // 상태·성능 푸시는 자주 오니 로그를 줄인다
  const frequent = env.type === "morph.camera.control.state" || env.type === "morph.perf.monitor.report";
  if (!frequent || state.pushes[env.type] % 30 === 1) {
    log("in", `${env.type} ${JSON.stringify(env.payload)}`);
  }
}

// --- UI ---
document.querySelectorAll("[data-layout]").forEach((b) =>
  b.addEventListener("click", () => request("morph.viewport.split.set_layout", { layout: b.dataset.layout })));
document.querySelectorAll("[data-focus]").forEach((b) =>
  b.addEventListener("click", () => request("morph.viewport.split.focus_view", { view_id: Number(b.dataset.focus) })));
document.querySelectorAll("[data-orbit]").forEach((b) =>
  b.addEventListener("click", () => request("morph.camera.control.orbit", { d_yaw: Number(b.dataset.orbit), d_pitch: 0 })));
document.querySelectorAll("[data-zoom]").forEach((b) =>
  b.addEventListener("click", () => request("morph.camera.control.zoom", { factor: Number(b.dataset.zoom) })));
$("#selftest").addEventListener("click", () => selfTest());

// --- 영상 위 마우스 조작 ---
// 왼쪽 드래그 = orbit, 오른쪽/가운데/Shift+왼쪽 드래그 = pan, 휠 = zoom, 누른 뷰로 포커스.
// 드래그는 id:null(회신 없음)로 보내고 INPUT_HZ로 묶는다 (camera CONTRACT 권장).
const INPUT_HZ = 30;
const ORBIT_DEG_PER_PX = 0.25;
const ZOOM_PER_WHEEL = 1.0015; // deltaY 1당 배율 (아래로 굴리면 멀어짐)
const split = { layout: "1x1", focused: 0, views: [], sideRatio: 0.25 };
const input = $("#input");
const focusBox = $("#focus-box");
const acc = { orbitX: 0, orbitY: 0, panX: 0, panY: 0, zoom: 1, sent: 0 };
let drag = null;

function onSplitState(p) {
  split.layout = p.layout;
  split.focused = p.focused_view_id;
  split.views = p.views || [];
  if (typeof p.side_ratio === "number") split.sideRatio = p.side_ratio;
  drawFocus();
  renderViews();
  renderSide();
}

// --- 1+3 오른쪽 열 폭 (split set_side_ratio). 드래그 중에는 최신 값만 SIDE_HZ로 보낸다 ---
const SIDE_HZ = 10;
const side = { pending: null, timer: 0, editingUntil: 0 };
function renderSide() {
  // 드래그 중(또는 막 놓은 직후)에는 푸시 값으로 슬라이더를 되돌리지 않는다
  if (performance.now() < side.editingUntil) return;
  $("#side-ratio").value = split.sideRatio;
  $("#side-ratio-val").textContent = split.sideRatio.toFixed(2);
}
function flushSide() {
  side.timer = 0;
  if (side.pending === null) return;
  const ratio = side.pending;
  side.pending = null;
  request("morph.viewport.split.set_side_ratio", { ratio }).then((r) => {
    if (!r.ok) log("sys", `set_side_ratio 실패: ${r.error} ${r.message || ""}`);
  });
  side.timer = setTimeout(flushSide, 1000 / SIDE_HZ);
}
$("#side-ratio").addEventListener("input", (e) => {
  side.editingUntil = performance.now() + 1000;
  side.pending = Number(e.target.value);
  $("#side-ratio-val").textContent = side.pending.toFixed(2);
  if (!side.timer) flushSide();
});

// --- 뷰별 카메라 (split list_cameras / set_view_camera) · 프리셋 (camera save/list/apply/delete_preset) ---
// 카메라·프리셋 목록은 푸시가 없어 필요할 때 다시 요청한다 (각 CONTRACT)
const lists = { cameras: [], presets: [] };
const esc = (t) => String(t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

async function refreshCameras() {
  const r = await request("morph.viewport.split.list_cameras");
  if (r.ok) lists.cameras = r.cameras || [];
  renderViews();
  return r;
}

function renderViews() {
  const el = $("#view-rows");
  // 사용자가 드롭다운을 연(포커스한) 채로 state 푸시가 오면 다시 그리지 않는다 (닫히지 않게). blur 때 다시 그린다
  if (el.contains(document.activeElement) && document.activeElement.tagName === "SELECT") { el.dataset.stale = "1"; return; }
  delete el.dataset.stale;
  if (!split.views.length) { el.className = "muted"; el.textContent = "split 상태 대기 중"; return; }
  el.className = "";
  el.innerHTML = split.views.map((v) => {
    const paths = lists.cameras.map((c) => c.path);
    // 목록에 없는 현재 카메라(목록 갱신 전)도 선택지로 보이게 한다
    const opts = (paths.includes(v.camera_path) ? [] : [{ path: v.camera_path, name: v.camera_path }]).concat(lists.cameras);
    return `<div class="row${v.view_id === split.focused ? " focused" : ""}"><span>view ${v.view_id}${v.view_id === split.focused ? " ●" : ""}</span>` +
      `<select data-view="${v.view_id}">${opts.map((c) => `<option value="${esc(c.path)}"${c.path === v.camera_path ? " selected" : ""}>${esc(c.builtin ? `${c.name} (기본)` : c.path)}</option>`).join("")}</select></div>`;
  }).join("");
}

$("#view-rows").addEventListener("change", async (e) => {
  const sel = e.target.closest("select[data-view]");
  if (!sel) return;
  const r = await request("morph.viewport.split.set_view_camera", { view_id: Number(sel.dataset.view), camera_path: sel.value });
  if (!r.ok) { log("sys", `set_view_camera 실패: ${r.error} ${r.message || ""}`); renderViews(); }
});
$("#cam-refresh").addEventListener("click", () => refreshCameras());
$("#view-rows").addEventListener("focusout", () => setTimeout(() => { if ($("#view-rows").dataset.stale) renderViews(); }, 0));

async function refreshPresets() {
  const r = await request("morph.camera.control.list_presets");
  if (r.ok) lists.presets = r.presets || [];
  const el = $("#preset-rows");
  if (!lists.presets.length) { el.className = "muted"; el.textContent = "없음"; return r; }
  el.className = "";
  el.innerHTML = lists.presets.map((p) =>
    `<div class="row"><span title="eye ${esc(JSON.stringify(p.eye))}">${esc(p.name)}</span>` +
    `<button data-apply="${esc(p.name)}">적용</button><button data-delete="${esc(p.name)}">삭제</button></div>`).join("");
  return r;
}

$("#preset-save").addEventListener("click", async () => {
  const name = $("#preset-name").value.trim();
  if (!name) return;
  const r = await request("morph.camera.control.save_preset", { name });
  if (!r.ok) log("sys", `save_preset 실패: ${r.error} ${r.message || ""}`);
  refreshPresets();
});
$("#preset-rows").addEventListener("click", async (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  if (b.dataset.apply) await request("morph.camera.control.apply_preset", { name: b.dataset.apply, duration_s: 0.5 });
  if (b.dataset.delete) { await request("morph.camera.control.delete_preset", { name: b.dataset.delete }); refreshPresets(); }
});

// --- 장면 목록·열기 (stage.loader list_scenes / open_scene / reload / new_scene, state 푸시) ---
// 열기 요청은 열기가 끝난 뒤 회신이 오므로 시간 제한을 길게 둔다 (stage.loader CONTRACT)
const OPEN_TIMEOUT_MS = 60000;
const scene = { list: [], current: null, name: null, loading: false };
function onSceneState(p) {
  scene.current = p.scene_id;
  scene.name = p.name;
  scene.loading = !!p.loading;
  renderScenes();
}
function renderScenes() {
  $("#scene-cur").textContent = scene.loading ? "여는 중…" : (scene.current || scene.name || "(없음)");
  const el = $("#scene-rows");
  if (!scene.list.length) { el.className = "muted"; el.textContent = "목록 없음"; return; }
  el.className = "";
  el.innerHTML = scene.list.map((s) =>
    `<div class="row${s.id === scene.current ? " focused" : ""}"><span title="${esc(s.root)} · ${s.size_bytes} B">${esc(s.id)}${s.id === scene.current ? " ●" : ""}</span>` +
    `<button data-open="${esc(s.id)}"${scene.loading ? " disabled" : ""}>열기</button></div>`).join("");
}
async function refreshScenes(refresh = false) {
  const r = await request("morph.stage.loader.list_scenes", refresh ? { refresh: true } : {});
  if (r.ok) { scene.list = r.scenes || []; scene.current = r.current_scene_id; }
  renderScenes();
  return r;
}
async function openScene(id) {
  const r = await request("morph.stage.loader.open_scene", { id }, OPEN_TIMEOUT_MS);
  if (!r.ok) log("sys", `open_scene 실패: ${r.error} ${r.message || ""}`);
  else refreshCameras(); // 새 장면의 카메라로 드롭다운 갱신
  return r;
}
$("#scene-rows").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-open]");
  if (b) openScene(b.dataset.open);
});
$("#scene-refresh").addEventListener("click", () => refreshScenes(true));
$("#scene-reload").addEventListener("click", async () => {
  const r = await request("morph.stage.loader.reload", {}, OPEN_TIMEOUT_MS);
  if (!r.ok) log("sys", `reload 실패: ${r.error} ${r.message || ""}`); else refreshCameras();
});
$("#scene-new").addEventListener("click", async () => {
  const r = await request("morph.stage.loader.new_scene", {}, OPEN_TIMEOUT_MS);
  if (!r.ok) log("sys", `new_scene 실패: ${r.error} ${r.message || ""}`); else refreshCameras();
});

// --- 주석 (annotation.overlay list/add/update/remove/set_visible/save/reload, state 푸시) ---
// 주석은 prim이 아니라 월드 좌표 데이터다. Kit 뷰포트 안에 그려지므로 웹은 목록·표시만 다룬다
const anno = { list: [], visible: true, file: null, showDesc: true, textScale: 1, selected: null, picking: false, pickMode: "point", face: null, colorTouched: false, editing: null };
const KIND_KO = { label: "라벨", inlet: "입구", outlet: "출구", plane: "단면" };
function onAnnoState(p) {
  anno.list = p.annotations || [];
  anno.visible = !!p.visible;
  anno.file = p.file;
  if (typeof p.show_description === "boolean") anno.showDesc = p.show_description;
  if (typeof p.text_scale === "number") anno.textScale = p.text_scale;
  if ("face" in p) { anno.face = p.face; renderFace(); }
  if ("selected_id" in p && p.selected_id !== anno.selected) {
    anno.selected = p.selected_id;
    loadForm(anno.list.find((x) => x.id === anno.selected) || null);
  } else if (anno.editing && !anno.dirty) {
    const cur = anno.list.find((x) => x.id === anno.editing);
    if (cur && JSON.stringify(cur) !== JSON.stringify(anno.orig)) loadForm(cur);
  }
  renderAnnos();
}
function renderAnnos() {
  $("#anno-visible").checked = anno.visible;
  $("#anno-desc").checked = anno.showDesc !== false;
  if (document.activeElement !== $("#anno-scale") && typeof anno.textScale === "number") $("#anno-scale").value = anno.textScale;
  $("#anno-file").textContent = anno.file || "(파일 장면 아님)";
  const el = $("#anno-rows");
  if (!anno.list.length) { el.className = "muted"; el.textContent = "없음"; return; }
  el.className = "";
  el.innerHTML = anno.list.map((a) =>
    `<div class="row${a.id === anno.selected ? " sel" : ""}"><span>${esc(KIND_KO[a.kind] || a.kind)}</span><input type="checkbox" data-anno-vis="${esc(a.id)}"${a.visible ? " checked" : ""} style="flex:none" />` +
    `<span data-anno-sel="${esc(a.id)}" title="${esc(a.description || "")}" style="flex:1">${esc(a.label || a.id)}</span><button data-anno-del="${esc(a.id)}">삭제</button></div>`).join("");
}
async function refreshAnnos() {
  const r = await request("morph.annotation.overlay.list");
  if (r.ok) onAnnoState(r);
  return r;
}
const annoReq = async (type, payload) => {
  const r = await request(type, payload);
  if (!r.ok) log("sys", `${type} 실패: ${r.error} ${r.message || ""}`);
  return r;
};
$("#anno-visible").addEventListener("change", (e) => annoReq("morph.annotation.overlay.set_visible", { visible: e.target.checked }));
$("#anno-rows").addEventListener("change", (e) => {
  const cb = e.target.closest("input[data-anno-vis]");
  if (cb) annoReq("morph.annotation.overlay.set_visible", { id: cb.dataset.annoVis, visible: cb.checked });
});
$("#anno-rows").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-anno-del]");
  if (b) { annoReq("morph.annotation.overlay.remove", { id: b.dataset.annoDel }); return; }
  const sel = e.target.closest("[data-anno-sel]");
  if (sel) selectAnno(sel.dataset.annoSel);
});
$("#anno-desc").addEventListener("change", (e) => annoReq("morph.annotation.overlay.set_options", { show_description: e.target.checked }));
$("#anno-scale").addEventListener("change", (e) => annoReq("morph.annotation.overlay.set_options", { text_scale: Number(e.target.value) }));
$("#anno-reload").addEventListener("click", () => annoReq("morph.annotation.overlay.reload", {}));
$("#anno-save").addEventListener("click", () => annoReq("morph.annotation.overlay.save", {}));
$("#anno-add").addEventListener("click", async () => {
  let a;
  try { a = JSON.parse($("#anno-json").value); } catch (e) { log("sys", `주석 JSON 오류: ${e}`); return; }
  const r = await annoReq("morph.annotation.overlay.add", { annotation: a });
  if (r.ok) $("#anno-json").value = "";
});

// --- 주석 편집 폼: 목록에서 고르면(select) 폼에 채우고, 숫자로 고친 뒤 적용(add/update).
// "뷰에서 위치 찍기"를 켜면 영상 위 다음 왼쪽 클릭을 pick으로 보내 표면 위치·법선을 받는다 (prim에 묶지 않음)
const VEC = { position: ["p0", "p1", "p2"], direction: ["d0", "d1", "d2"], up: ["u0", "u1", "u2"] };
const aeF = (f) => document.querySelector(`#anno-edit [data-f="${f}"]`);
const hex = (c) => "#" + c.map((x) => Math.round(Math.max(0, Math.min(1, x)) * 255).toString(16).padStart(2, "0")).join("");
const unhex = (h) => [1, 3, 5].map((i) => Math.round((parseInt(h.slice(i, i + 2), 16) / 255) * 1000) / 1000);
const r3 = (x) => Math.round(x * 1000) / 1000;
function setVec(name, v) { VEC[name].forEach((f, i) => { aeF(f).value = v && v[i] !== undefined ? r3(v[i]) : ""; }); }
function getVec(name) {
  const vals = VEC[name].map((f) => aeF(f).value.trim());
  if (vals.every((x) => x === "")) return null;
  if (vals.some((x) => x === "")) throw new Error(`${name}: 세 칸을 모두 채우거나 모두 비우세요`);
  return vals.map(Number);
}
function aeMsg(text, err = false) { const m = $("#ae-msg"); m.textContent = text; m.className = err ? "err" : "muted"; }
function layoutForm() {
  const k = $("#ae-kind").value, plane = k === "plane";
  document.querySelector('#anno-edit [data-ae="direction"]').style.display = k === "label" ? "none" : "";
  document.querySelector('#anno-edit [data-ae="size"]').style.display = k === "label" ? "none" : "";
  document.querySelector('#anno-edit [data-ae="up"]').style.display = plane ? "" : "none";
  aeF("s1").style.display = plane ? "" : "none";
  $("#ae-pos-name").textContent = plane ? "중심" : "위치";
  $("#ae-dir-name").textContent = plane ? "법선" : "흐름";
}
function loadForm(a) {
  anno.editing = a ? a.id : null;
  anno.orig = a ? JSON.parse(JSON.stringify(a)) : null;
  anno.dirty = false;
  if (a) $("#ae-kind").value = a.kind;
  $("#ae-kind").disabled = !!a;
  $("#ae-id").value = a ? a.id : "";
  $("#ae-id").disabled = !!a;
  $("#ae-label").value = a?.label || "";
  $("#ae-desc").value = a?.description || "";
  if (a?.color) $("#ae-color").value = hex(a.color);
  anno.colorTouched = false;
  const plane = a?.kind === "plane";
  setVec("position", a ? (plane ? a.origin : a.position) : null);
  setVec("direction", a ? (plane ? a.normal : a.direction) : null);
  setVec("up", plane ? a.up : null);
  const size = a?.size;
  aeF("s0").value = Array.isArray(size) ? size[0] : (size ?? "");
  aeF("s1").value = Array.isArray(size) ? size[1] : "";
  layoutForm();
  aeMsg(a ? `편집 중: ${a.id}` : "새 주석");
}
function formFields() {
  const k = $("#ae-kind").value, plane = k === "plane";
  const f = { label: $("#ae-label").value, description: $("#ae-desc").value };
  if (anno.colorTouched) f.color = unhex($("#ae-color").value);
  const pos = getVec("position"), dir = getVec("direction");
  if (plane) { f.origin = pos; f.normal = dir; } else { f.position = pos; if (k !== "label") f.direction = dir; }
  const s0 = aeF("s0").value.trim(), s1 = aeF("s1").value.trim();
  if (plane) f.size = s0 === "" && s1 === "" ? null : [Number(s0 || s1), Number(s1 || s0)];
  else if (k !== "label") f.size = s0 === "" ? null : Number(s0);
  if (plane) f.up = getVec("up");
  return f;
}
async function selectAnno(id) {
  const r = await annoReq("morph.annotation.overlay.select", { id });
  if (r.ok) {
    anno.selected = r.selected_id;
    loadForm(anno.list.find((x) => x.id === anno.selected) || null);
    renderAnnos();
  }
  return r;
}
const sameShown = (formVal, origVal) => {
  if (formVal === null || formVal === undefined) return origVal === null || origVal === undefined;
  if (Array.isArray(formVal)) return Array.isArray(origVal) && formVal.length === origVal.length && formVal.every((x, i) => typeof origVal[i] === "number" && r3(x) === r3(origVal[i]));
  if (typeof formVal === "number") return typeof origVal === "number" && r3(formVal) === r3(origVal);
  return formVal === origVal;
};
function changedFields(f, orig) {
  const out = {};
  for (const [k, v] of Object.entries(f)) {
    if (k === "color") { out.color = v; continue; } // 색은 건드렸을 때만 f에 들어 있다
    const o = orig?.[k] ?? ((k === "label" || k === "description") ? "" : undefined);
    if (!sameShown(v, o)) out[k] = v;
  }
  return out;
}
async function applyForm() {
  let f;
  try { f = formFields(); } catch (e) { aeMsg(String(e.message || e), true); return { ok: false, error: "form", message: String(e) }; }
  let r;
  if (anno.editing) {
    const fields = changedFields(f, anno.orig);
    if (!Object.keys(fields).length) { aeMsg("바뀐 값이 없습니다"); return { ok: true, unchanged: true }; }
    r = await request("morph.annotation.overlay.update", { id: anno.editing, fields });
    if (r.ok && r.annotation) loadForm(r.annotation);
  } else {
    const a = { kind: $("#ae-kind").value, ...f };
    for (const k of Object.keys(a)) if (a[k] === null) delete a[k];
    const id = $("#ae-id").value.trim();
    if (id) a.id = id;
    r = await request("morph.annotation.overlay.add", { annotation: a });
    if (r.ok) {
      for (let i = 0; i < 20 && !anno.list.some((x) => x.id === r.id); i++) await sleep(50); // state 푸시로 목록 반영 대기
      await selectAnno(r.id);
    }
  }
  aeMsg(r.ok ? `적용됨: ${r.id || r.annotation?.id}` : `${r.error}: ${r.message || ""}`, !r.ok);
  return r;
}
function setPicking(on, mode = "point") {
  anno.picking = on;
  anno.pickMode = mode;
  $("#ae-pick").classList.toggle("on", on && mode === "point");
  $("#ae-face").classList.toggle("on", on && mode === "face");
  input.classList.toggle("picking", on);
  if (on) aeMsg(mode === "face" ? "영상에서 면을 클릭하세요 (Esc 취소)" : "영상에서 모델 표면을 클릭하세요 (Esc 취소)");
}
// --- 면 선택 (pick_face): 클릭한 점이 놓인 평면 영역 전체의 중심·법선·면적. Kit 뷰에 외곽선·중심 좌표가 그려진다
const KIND_FACE = { planar: "평면", cylinder_side: "원통 옆면", sphere: "구면", polygon: "다각형", point: "점" };
const v3 = (v) => (v ? `(${v.map(r3).join(", ")})` : "-");
function renderFace() {
  const f = anno.face;
  $("#ae-face-info").textContent = f
    ? `${KIND_FACE[f.kind] || f.kind} · 중심 ${v3(f.center)} · 법선 ${v3(f.normal)} · 면적 ${r3(f.area)}${f.size ? ` · 크기 ${f.size.map(r3).join("×")}` : ""}`
    : "없음";
  $("#ae-face-info").className = f ? "" : "muted";
}
async function pickFaceAt(v, x, y) {
  setPicking(false);
  aeMsg("면 찾는 중…");
  const r = await request("morph.annotation.overlay.pick_face", { viewport_name: v.viewport_name, x, y });
  if (!r.ok) { aeMsg(`${r.error}: ${r.message || ""}`, true); return r; }
  anno.face = r.hit ? r.face : null;
  renderFace();
  aeMsg(r.hit ? "면을 선택했습니다 — [면 중심을 폼에]로 주석에 쓸 수 있습니다" : "빈 곳입니다 (모델 표면을 클릭)", !r.hit);
  return r;
}
function useFace() {
  const f = anno.face;
  if (!f) { aeMsg("선택된 면이 없습니다", true); return false; }
  const k = $("#ae-kind").value;
  setVec("position", f.center);
  if (k === "inlet" || k === "outlet") setVec("direction", f.normal.map((n) => (k === "inlet" ? -n : n)));
  if (k === "plane") {
    setVec("direction", f.normal);
    setVec("up", null);
    if (f.size) { if (f.size[0] > 0) aeF("s0").value = r3(f.size[0] * 1.2); if (f.size[1] > 0) aeF("s1").value = r3(f.size[1] * 1.2); } // 성분별: 0이면 기존 값 유지 (CONTRACT)
  }
  anno.dirty = true;
  aeMsg(`면 중심 ${v3(f.center)}을 폼에 넣었습니다 — 적용을 누르세요`);
  return true;
}
async function pickAt(v, x, y) {
  setPicking(false);
  aeMsg("찍는 중…");
  const r = await request("morph.annotation.overlay.pick", { viewport_name: v.viewport_name, x, y });
  if (!r.ok) { aeMsg(`${r.error}: ${r.message || ""}`, true); return r; }
  if (!r.hit) { aeMsg("빈 곳입니다 (모델 표면을 클릭)", true); return r; }
  const k = $("#ae-kind").value;
  setVec("position", r.position);
  if (r.normal && (k === "inlet" || k === "outlet")) setVec("direction", r.normal.map((n) => (k === "inlet" ? -n : n)));
  aeMsg(`찍음 ${r.position.map(r3).join(", ")}${r.normal ? " · 법선 " + r.normal.map(r3).join(", ") : ""} — 적용을 누르세요`);
  return r;
}
$("#ae-kind").addEventListener("change", layoutForm);
$("#anno-edit").addEventListener("input", () => { anno.dirty = true; });
$("#ae-color").addEventListener("input", () => { anno.colorTouched = true; });
$("#ae-new").addEventListener("click", () => { if (anno.selected) selectAnno(null); loadForm(null); });
$("#ae-apply").addEventListener("click", () => applyForm());
$("#ae-del").addEventListener("click", async () => {
  if (!anno.editing) return;
  const r = await annoReq("morph.annotation.overlay.remove", { id: anno.editing });
  if (r.ok) { anno.selected = null; loadForm(null); }
});
$("#ae-pick").addEventListener("click", () => setPicking(!(anno.picking && anno.pickMode === "point"), "point"));
$("#ae-face").addEventListener("click", () => setPicking(!(anno.picking && anno.pickMode === "face"), "face"));
$("#ae-face-use").addEventListener("click", () => useFace());
$("#ae-face-clear").addEventListener("click", async () => { const r = await annoReq("morph.annotation.overlay.clear_face", {}); if (r.ok) { anno.face = null; renderFace(); } });
window.addEventListener("keydown", (e) => { if (e.key === "Escape" && anno.picking) { setPicking(false); aeMsg(""); } });
layoutForm();

// rect가 없을 때(split 1.2 전, 전환 중) 쓰는 배치: "RxC" = R행 C열 균등(view_id 행 우선), "1+3" = 왼쪽 큰 뷰 + 오른쪽 세로 3개(폭 side_ratio).
// 모르는 레이아웃이면 null (판정에서 뺀다)
function fallbackRect(viewId) {
  const sr = split.sideRatio;
  if (split.layout === "1+3") return viewId === 0 ? { x: 0, y: 0, w: 1 - sr, h: 1 } : { x: 1 - sr, y: (viewId - 1) / 3, w: sr, h: 1 / 3 };
  if (!/^\d+x\d+$/.test(split.layout || "1x1")) return null;
  const [r, c] = (split.layout || "1x1").split("x").map(Number);
  const i = viewId % (r * c);
  return { x: (i % c) / c, y: Math.floor(i / c) / r, w: 1 / c, h: 1 / r };
}
const rectOf = (v) => v.rect || fallbackRect(v.view_id);

// 화면 좌표 → 스트림 프레임 정규화 좌표 (video object-fit: contain 여백 제외)
function videoBox() {
  const v = $("#remote-video");
  const b = v.getBoundingClientRect();
  const vw = v.videoWidth || 1920, vh = v.videoHeight || 1080;
  const s = Math.min(b.width / vw, b.height / vh);
  const w = vw * s, h = vh * s;
  return { left: b.left + (b.width - w) / 2, top: b.top + (b.height - h) / 2, w, h, scale: s, vw };
}
function hitView(clientX, clientY) {
  const box = videoBox();
  const x = (clientX - box.left) / box.w, y = (clientY - box.top) / box.h;
  if (x < 0 || y < 0 || x > 1 || y > 1) return null;
  return split.views.find((v) => {
    const r = rectOf(v);
    return r && x >= r.x && x < r.x + r.w && y >= r.y && y < r.y + r.h;
  }) ?? null;
}
function drawFocus() {
  const v = split.views.find((x) => x.view_id === split.focused);
  const r = v && rectOf(v);
  if (!r || split.views.length < 2) return void (focusBox.style.display = "none");
  const box = videoBox(), st = $("#stage").getBoundingClientRect();
  Object.assign(focusBox.style, {
    display: "block",
    left: `${box.left - st.left + r.x * box.w}px`, top: `${box.top - st.top + r.y * box.h}px`,
    width: `${r.w * box.w}px`, height: `${r.h * box.h}px`,
  });
}
window.addEventListener("resize", drawFocus);

// pan: 화면 픽셀 → 월드 단위. target 거리의 화면 폭 2·d·tan(fov/2) / 뷰 폭(스트림 px)
function worldPerPixel() {
  const cam = state.last["morph.camera.control.state"];
  const v = split.views.find((x) => x.view_id === split.focused);
  if (!cam) return 0;
  const d = Math.hypot(...cam.eye.map((e, i) => e - cam.target[i]));
  const viewPx = ((v && rectOf(v)?.w) || 1) * (videoBox().vw || 1920);
  return (2 * d * Math.tan((cam.fov * Math.PI) / 360)) / viewPx;
}

input.addEventListener("contextmenu", (e) => e.preventDefault());
input.addEventListener("pointerdown", (e) => {
  e.preventDefault();
  const v = hitView(e.clientX, e.clientY);
  if (anno.picking && e.button === 0) { // 주석 위치 찍기: 카메라 조작 대신 pick (뷰 안 0..1 좌표)
    if (v?.viewport_name) {
      const box = videoBox(), r = rectOf(v);
      const u = (e.clientX - box.left) / box.w, w = (e.clientY - box.top) / box.h;
      const px = Math.min(1, Math.max(0, (u - r.x) / r.w)), py = Math.min(1, Math.max(0, (w - r.y) / r.h));
      (anno.pickMode === "face" ? pickFaceAt : pickAt)(v, px, py);
    }
    return;
  }
  try { input.setPointerCapture(e.pointerId); } catch {} // 합성 이벤트(self test)는 캡처할 포인터가 없다
  if (v && v.view_id !== split.focused) {
    split.focused = v.view_id; // 푸시 전에 바로 반영 (드래그가 새 뷰 기준으로 시작)
    drawFocus();
    notify("morph.viewport.split.focus_view", { view_id: v.view_id });
  }
  const mode = e.button === 0 && !e.shiftKey ? "orbit" : "pan";
  drag = { id: e.pointerId, mode, x: e.clientX, y: e.clientY };
  input.classList.add("drag");
});
input.addEventListener("pointermove", (e) => {
  if (!drag || e.pointerId !== drag.id) return;
  const s = videoBox().scale || 1; // 화면 px → 스트림 px
  const dx = (e.clientX - drag.x) / s, dy = (e.clientY - drag.y) / s;
  drag.x = e.clientX;
  drag.y = e.clientY;
  if (drag.mode === "orbit") { acc.orbitX += dx; acc.orbitY += dy; }
  else { acc.panX += dx; acc.panY += dy; }
});
const endDrag = (e) => {
  if (!drag || e.pointerId !== drag.id) return;
  drag = null;
  input.classList.remove("drag");
};
input.addEventListener("pointerup", endDrag);
input.addEventListener("pointercancel", endDrag);
input.addEventListener("wheel", (e) => {
  e.preventDefault();
  const v = hitView(e.clientX, e.clientY);
  if (v && v.view_id !== split.focused) {
    split.focused = v.view_id;
    drawFocus();
    notify("morph.viewport.split.focus_view", { view_id: v.view_id });
  }
  const dy = e.deltaMode === 1 ? e.deltaY * 40 : e.deltaY; // 줄 단위 → px
  acc.zoom *= Math.pow(ZOOM_PER_WHEEL, dy);
}, { passive: false });

function flushInput() {
  // 장면이 마우스를 따라오게: 오른쪽으로 끌면 카메라는 왼쪽(d_yaw<0), 아래로 끌면 카메라가 위로(d_pitch>0)
  if (acc.orbitX || acc.orbitY) {
    notify("morph.camera.control.orbit", { d_yaw: -acc.orbitX * ORBIT_DEG_PER_PX, d_pitch: acc.orbitY * ORBIT_DEG_PER_PX });
    acc.sent++;
  }
  if (acc.panX || acc.panY) {
    const k = worldPerPixel();
    if (k > 0) {
      notify("morph.camera.control.pan", { dx: -acc.panX * k, dy: acc.panY * k });
      acc.sent++;
    }
  }
  if (acc.zoom !== 1) {
    notify("morph.camera.control.zoom", { factor: acc.zoom });
    acc.sent++;
  }
  acc.orbitX = acc.orbitY = acc.panX = acc.panY = 0;
  acc.zoom = 1;
}
setInterval(flushInput, 1000 / INPUT_HZ);

// --- 성능 표시 ---
// Kit: morph.perf.monitor.report (webReport=true일 때 주기마다). 브라우저: 받은 영상 fps, 요청 왕복 시간.
const perf = { videoFps: 0, rttMs: null, inputPerSec: 0 };
state.perf = perf;
let lastFrames = 0, lastSent = 0;
setInterval(async () => {
  const frames = $("#remote-video").getVideoPlaybackQuality?.().totalVideoFrames ?? 0; // 디코딩된 프레임 수
  perf.videoFps = frames - lastFrames;
  lastFrames = frames;
  perf.inputPerSec = acc.sent - lastSent;
  lastSent = acc.sent;
  if (state.connected && state.hello) {
    const t0 = performance.now();
    const r = await request("morph.viewport.split.get_state", {}, 3000);
    if (r.ok) { perf.rttMs = performance.now() - t0; onSplitState(r); }
  }
  renderPerf();
}, 1000);

const fmt = (x, d = 1) => (typeof x === "number" && isFinite(x) ? x.toFixed(d) : "-");
function renderPerf() {
  const rep = state.last["morph.perf.monitor.report"];
  const rows = [];
  if (rep) {
    const f = rep.frame_ms || {};
    rows.push(`<span class="k">Kit frame</span> avg ${fmt(f.avg)} · p95 <span class="${f.p95 > 33 ? "warn" : ""}">${fmt(f.p95)}</span> · max ${fmt(f.max)} ms (${fmt(f.avg ? 1000 / f.avg : NaN, 0)} fps)`);
    const top = Object.entries(rep.metrics || {})
      .filter(([, s]) => s.kind === "scope")
      .sort((a, b) => b[1].avg - a[1].avg)
      .slice(0, 4);
    for (const [name, s] of top) rows.push(`<span class="k">${name.replace(/^morph\./, "")}</span> ${fmt(s.avg, 2)} / p95 ${fmt(s.p95, 2)} ms`);
    // 시스템 지표 (perf 1.1, 이름은 perf CONTRACT "시스템 지표"). 없는 항목은 줄에서 뺀다
    const m = rep.metrics || {};
    const val = (k) => m[`morph.perf.monitor.${k}`]?.value;
    const mem = [];
    if (val("sys.mem_working_set_mb") !== undefined) mem.push(`프로세스 ${fmt(val("sys.mem_working_set_mb") / 1024, 2)} GB (전용 ${fmt(val("sys.mem_private_mb") / 1024, 2)})`);
    if (val("gpu.mem_used_mb") !== undefined) mem.push(`GPU ${fmt(val("gpu.mem_used_mb"), 0)} / ${fmt(val("gpu.mem_budget_mb"), 0)} MB`);
    if (mem.length) rows.push(`<span class="k">메모리</span> ${mem.join(" · ")}`);
  } else {
    rows.push(`<span class="k">Kit</span> 리포트 없음 (스트리밍 앱 webReport 설정 확인)`);
  }
  rows.push(`<span class="k">브라우저</span> 영상 ${perf.videoFps} fps · 왕복 ${fmt(perf.rttMs)} ms · 입력 ${perf.inputPerSec}/s`);
  $("#perf").innerHTML = rows.join("<br>");
}

function check(ok, what) {
  state.results.push({ ok: !!ok, what });
  const div = document.createElement("div");
  div.className = ok ? "pass" : "fail";
  div.textContent = `${ok ? "PASS" : "FAIL"} ${what}`;
  $("#results").append(div);
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function selfTest() {
  state.results = [];
  $("#results").textContent = "";
  const video = $("#remote-video");
  // 새로고침 직후에는 Kit이 이전 클라이언트를 정리하는 동안 연결이 늦다 (클라이언트는 하나만 받음)
  for (let i = 0; i < 60 && !(state.connected && video.videoWidth > 0); i++) await sleep(500);
  check(video.videoWidth > 0, `video frames (${video.videoWidth}x${video.videoHeight})`);

  const hello = await request("morph.stream.messaging.hello", { client_id: "web-test" });
  state.hello = state.hello || hello.ok;
  let r0, st0;
  check(hello.ok && hello.protocol === 2, `hello ${JSON.stringify(hello)}`);

  // --- 장면 (stage.loader): 샘플 열기 → 이후 항목은 이 장면 위에서 돈다 ---
  const DEMO = "samples/morph_demo.usda";
  r0 = await refreshScenes(true);
  check(r0.ok && scene.list.some((s) => s.id === DEMO) && scene.list.every((s) => !("path" in s)), `list_scenes ${JSON.stringify(scene.list.map((s) => s.id).slice(0, 6))}`);
  const statePushes0 = state.pushes["morph.stage.loader.state"] || 0;
  const btn = document.querySelector(`#scene-rows button[data-open="${DEMO}"]`);
  const opened = btn ? new Promise((res) => { const t0 = Date.now(); (async () => { while (Date.now() - t0 < OPEN_TIMEOUT_MS && !((state.pushes["morph.stage.loader.state"] || 0) >= statePushes0 + 2 && scene.current === DEMO && !scene.loading)) await sleep(100); res(scene.current === DEMO && !scene.loading); })(); }) : Promise.resolve(false);
  btn?.click();
  check(await opened, `open_scene button -> current ${scene.current}`);
  check((state.pushes["morph.stage.loader.state"] || 0) >= statePushes0 + 2, `stage.loader.state pushes (${statePushes0} -> ${state.pushes["morph.stage.loader.state"] || 0})`);
  r0 = await request("morph.stage.loader.open_scene", { id: "samples/no_such.usda" });
  check(!r0.ok && r0.error === "not_found", `open_scene unknown -> ${r0.error}`);
  r0 = await request("morph.stage.loader.reload", {}, OPEN_TIMEOUT_MS);
  check(r0.ok && r0.scene_id === DEMO, `reload ${JSON.stringify(r0)}`);

  let r = await request("morph.viewport.split.set_layout", { layout: "2x2" });
  check(r.ok, `set_layout 2x2 ${JSON.stringify(r)}`);
  await sleep(2500);
  const st = await request("morph.viewport.split.get_state");
  check(st.ok && st.layout === "2x2" && st.views?.length === 4, `get_state layout=${st.layout} views=${st.views?.length}`);

  r = await request("morph.viewport.split.focus_view", { view_id: 1 });
  check(r.ok, `focus_view 1 ${JSON.stringify(r)}`);
  r = await request("morph.viewport.split.focus_view", { view_id: 99 });
  check(!r.ok && r.error === "not_found", `focus_view 99 -> ${r.error}`);

  // orbit은 원근 카메라 뷰(view 0, Persp)에서 확인한다. 2x2 기본 view 1은 Top(정수직 시점)이라
  // yaw만으로는 eye가 바뀌지 않아 state 푸시가 없는 게 정상이다.
  r = await request("morph.viewport.split.focus_view", { view_id: 0 });
  check(r.ok, `focus_view 0 ${JSON.stringify(r)}`);
  await sleep(500);
  const before = state.pushes["morph.camera.control.state"] || 0;
  r = await request("morph.camera.control.orbit", { d_yaw: 20, d_pitch: 5 });
  check(r.ok, `orbit ${JSON.stringify(r)}`);
  await sleep(800);
  const after = state.pushes["morph.camera.control.state"] || 0;
  check(after > before, `camera.state pushes after orbit (${before} -> ${after})`);

  r = await request("morph.camera.control.apply_preset", { name: "no-such" });
  check(!r.ok && r.error === "not_found", `apply_preset unknown -> ${r.error}`);
  r = await request("morph.camera.control.zoom", { factor: -1 });
  check(!r.ok && (r.error === "invalid_argument" || r.error === "invalid_payload"), `zoom -1 -> ${r.error}`);

  r = await request("morph.perf.monitor.set_enabled", { enabled: true });
  check(r.ok && r.enabled === true, `perf set_enabled ${JSON.stringify(r)}`);

  r = await request("morph.camera.orbit", { d_yaw: 1, d_pitch: 0 }); // 1.0 이전 이름
  check(!r.ok && r.error === "unknown_type", `old type name -> ${r.error}`);

  check(!!state.pushes["morph.viewport.split.state"], `viewport.split.state pushes (${state.pushes["morph.viewport.split.state"] || 0})`);

  // --- 마우스 조작 (입력 레이어에 합성 이벤트) ---
  const st2 = await request("morph.viewport.split.get_state");
  if (st2.ok) onSplitState(st2);
  check(st2.views?.every((v) => v.rect && v.rect.w > 0), `view rect ${JSON.stringify(st2.views?.map((v) => v.rect))}`);
  const center = (id) => {
    const box = videoBox(), r = rectOf(split.views.find((v) => v.view_id === id));
    return [box.left + (r.x + r.w / 2) * box.w, box.top + (r.y + r.h / 2) * box.h];
  };
  // 실제 클릭처럼 그 좌표의 맨 위 요소에 보낸다 (입력 레이어가 가려지면 실패하도록)
  const pe = (type, x, y, extra = {}) =>
    (document.elementFromPoint(x, y) || input).dispatchEvent(new PointerEvent(type, { clientX: x, clientY: y, pointerId: 7, bubbles: true, button: 0, buttons: 1, ...extra }));
  let [cx, cy] = center(3);
  pe("pointerdown", cx, cy);
  pe("pointerup", cx, cy);
  await sleep(800);
  const s3 = await request("morph.viewport.split.get_state");
  check(s3.focused_view_id === 3, `click view 3 -> focused ${s3.focused_view_id}`);

  [cx, cy] = center(0);
  pe("pointerdown", cx, cy);
  await sleep(600);
  const eye0 = state.last["morph.camera.control.state"]?.eye;
  for (let i = 1; i <= 10; i++) { pe("pointermove", cx + i * 12, cy + i * 3); await sleep(16); }
  pe("pointerup", cx + 120, cy + 30);
  await sleep(800);
  const eye1 = state.last["morph.camera.control.state"]?.eye;
  check(eye0 && eye1 && JSON.stringify(eye0) !== JSON.stringify(eye1), `drag orbit eye ${JSON.stringify(eye0)} -> ${JSON.stringify(eye1)}`);

  const dist = () => { const c = state.last["morph.camera.control.state"]; return c ? Math.hypot(...c.eye.map((e, i) => e - c.target[i])) : NaN; };
  const d0 = dist();
  (document.elementFromPoint(cx, cy) || input).dispatchEvent(new WheelEvent("wheel", { clientX: cx, clientY: cy, deltaY: -300, bubbles: true, cancelable: true }));
  await sleep(800);
  const d1 = dist();
  check(d1 < d0, `wheel zoom in distance ${fmt(d0)} -> ${fmt(d1)}`);

  const tgt0 = state.last["morph.camera.control.state"]?.target;
  pe("pointerdown", cx, cy, { button: 2, buttons: 2 });
  for (let i = 1; i <= 10; i++) { pe("pointermove", cx + i * 10, cy, { button: 2, buttons: 2 }); await sleep(16); }
  pe("pointerup", cx + 100, cy, { button: 2 });
  await sleep(800);
  const tgt1 = state.last["morph.camera.control.state"]?.target;
  check(tgt0 && tgt1 && JSON.stringify(tgt0) !== JSON.stringify(tgt1), `right-drag pan target ${JSON.stringify(tgt0)} -> ${JSON.stringify(tgt1)}`);

  // --- 뷰별 카메라 선택 (split list_cameras + set_view_camera, 드롭다운으로) ---
  r = await refreshCameras();
  const camPaths = (r.cameras || []).map((c) => c.path);
  check(r.ok && camPaths.includes("/OmniverseKit_Persp") && (r.cameras || []).every((c) => typeof c.builtin === "boolean"),
    `list_cameras ${camPaths.length} ${JSON.stringify(camPaths.slice(0, 6))}`);
  check(["CamFront", "CamTop", "CamSide"].every((n) => camPaths.includes(`/World/Cameras/${n}`)), `demo scene cameras listed`);
  const sel = document.querySelector('select[data-view="3"]');
  const before3 = split.views.find((v) => v.view_id === 3)?.camera_path;
  const used = split.views.map((v) => v.camera_path);
  const pick = camPaths.find((p) => !used.includes(p)) || camPaths.find((p) => p !== before3);
  if (sel && pick) { sel.value = pick; sel.dispatchEvent(new Event("change", { bubbles: true })); }
  for (let i = 0; i < 20 && split.views.find((v) => v.view_id === 3)?.camera_path !== pick; i++) await sleep(100);
  check(pick && split.views.find((v) => v.view_id === 3)?.camera_path === pick, `dropdown view 3 camera ${before3} -> ${pick}`);
  if (before3) await request("morph.viewport.split.set_view_camera", { view_id: 3, camera_path: before3 });

  // --- 프리셋 저장·목록·적용·삭제 (포커스 view 0) ---
  $("#preset-name").value = "selftest";
  $("#preset-save").click();
  for (let i = 0; i < 20 && !lists.presets.some((p) => p.name === "selftest"); i++) await sleep(100);
  const saved = lists.presets.find((p) => p.name === "selftest");
  check(saved && saved.eye?.length === 3 && saved.target?.length === 3, `save_preset + list_presets ${JSON.stringify(saved)}`);
  await request("morph.camera.control.orbit", { d_yaw: 30, d_pitch: 0 });
  await sleep(500);
  r = await request("morph.camera.control.apply_preset", { name: "selftest" });
  await sleep(600);
  const eyeBack = state.last["morph.camera.control.state"]?.eye;
  check(r.ok && saved && eyeBack && eyeBack.every((x, i) => Math.abs(x - saved.eye[i]) < 1e-2), `apply_preset restores eye ${JSON.stringify(eyeBack)}`);
  document.querySelector('#preset-rows button[data-delete="selftest"]')?.click();
  for (let i = 0; i < 20 && lists.presets.some((p) => p.name === "selftest"); i++) await sleep(100);
  r = await request("morph.camera.control.delete_preset", { name: "selftest" });
  check(!lists.presets.some((p) => p.name === "selftest") && !r.ok && r.error === "not_found", `delete_preset (again -> ${r.error})`);

  // --- 1+3 오른쪽 열 폭 (슬라이더 → set_side_ratio, state.side_ratio·rect 반영) ---
  r = await request("morph.viewport.split.set_layout", { layout: "1+3" });
  await sleep(2500);
  const ratio0 = split.sideRatio;
  const slider = $("#side-ratio");
  slider.value = "0.4";
  slider.dispatchEvent(new Event("input", { bubbles: true }));
  const w1 = () => split.views.find((v) => v.view_id === 1)?.rect?.w ?? 0;
  for (let i = 0; i < 30 && !(Math.abs(split.sideRatio - 0.4) < 1e-6 && w1() > 0.3); i++) await sleep(100);
  check(r.ok && Math.abs(split.sideRatio - 0.4) < 1e-6 && w1() > 0.3, `slider side_ratio ${ratio0} -> ${split.sideRatio}, view 1 rect.w ${fmt(w1())}`);
  r = await request("morph.viewport.split.set_side_ratio", { ratio: 0.9 });
  check(r.ok && r.side_ratio === 0.5, `set_side_ratio 0.9 -> clamped ${r.side_ratio}`);
  r = await request("morph.viewport.split.set_side_ratio", { ratio: "wide" });
  check(!r.ok && (r.error === "invalid_argument" || r.error === "invalid_payload"), `set_side_ratio "wide" -> ${r.error}`);
  await request("morph.viewport.split.set_side_ratio", { ratio: ratio0 });

  // --- 성능 리포트 ---
  for (let i = 0; i < 6 && !state.pushes["morph.perf.monitor.report"]; i++) await sleep(500);
  const rp = state.last["morph.perf.monitor.report"];
  check(rp && rp.frame_ms?.count > 0, `perf report frame_ms ${JSON.stringify(rp?.frame_ms)}`);
  check(rp?.metrics?.["morph.perf.monitor.sys.mem_working_set_mb"]?.value > 0, `perf sys memory ${rp?.metrics?.["morph.perf.monitor.sys.mem_working_set_mb"]?.value}`);
  check(perf.videoFps > 0, `browser video fps ${perf.videoFps}`);

  // --- 주석 (배관 샘플 + 사이드카 JSON 자동 읽기) — 마지막에 돈다 (장면을 바꾸므로) ---
  const PIPE = "samples/pipe_manifold.usda";
  r = await openScene(PIPE);
  for (let i = 0; i < 30 && !anno.list.length; i++) await sleep(100);
  const kinds = (k) => anno.list.filter((a) => a.kind === k).length;
  check(r.ok && kinds("inlet") === 1 && kinds("outlet") === 2 && kinds("plane") === 2 && kinds("label") >= 2,
    `annotations auto-loaded ${anno.file} ${JSON.stringify(Object.fromEntries(["inlet", "outlet", "plane", "label"].map((k) => [k, kinds(k)])))}`);
  const first = anno.list.find((a) => a.kind === "inlet");
  const cb = first && document.querySelector(`#anno-rows input[data-anno-vis="${first.id}"]`);
  if (cb) { cb.checked = false; cb.dispatchEvent(new Event("change", { bubbles: true })); }
  for (let i = 0; i < 20 && anno.list.find((a) => a.id === first?.id)?.visible !== false; i++) await sleep(100);
  check(first && anno.list.find((a) => a.id === first.id)?.visible === false, `anno checkbox hides ${first?.id}`);
  const n0 = anno.list.length;
  $("#anno-json").value = JSON.stringify({ id: "web_test", kind: "plane", label: "Web plane", origin: [0, 0, 1], normal: [0, 1, 0] });
  $("#anno-add").click();
  for (let i = 0; i < 20 && anno.list.length !== n0 + 1; i++) await sleep(100);
  check(anno.list.some((a) => a.id === "web_test" && a.kind === "plane"), `anno add via JSON -> ${anno.list.length}`);
  r = await request("morph.annotation.overlay.add", { annotation: { kind: "inlet", position: [0, 0, 0], direction: [0, 0, 0] } });
  check(!r.ok && r.error === "invalid_argument", `anno add zero direction -> ${r.error}`);
  r = await request("morph.annotation.overlay.update", { id: "web_test", fields: { label: "Web plane 2" } });
  check(r.ok && r.annotation?.label === "Web plane 2", `anno update ${JSON.stringify(r.annotation?.label)}`);
  document.querySelector('#anno-rows button[data-anno-del="web_test"]')?.click();
  for (let i = 0; i < 20 && anno.list.some((a) => a.id === "web_test"); i++) await sleep(100);
  r = await request("morph.annotation.overlay.remove", { id: "web_test" });
  check(!anno.list.some((a) => a.id === "web_test") && !r.ok && r.error === "not_found", `anno delete button (again -> ${r.error})`);
  // 편집 폼: 위치 찍기(합성 클릭) → 새 라벨 적용 → 선택 강조 → 숫자 보정 → 삭제
  st0 = await request("morph.viewport.split.get_state");
  if (st0.ok) onSplitState(st0);
  const pv = split.views.find((v) => v.view_id === 0) || split.views[0];
  $("#ae-new").click();
  $("#ae-kind").value = "label"; $("#ae-kind").dispatchEvent(new Event("change"));
  $("#ae-pick").click();
  const pr = rectOf(pv), pbox = videoBox();
  const px = pbox.left + (pr.x + pr.w / 2) * pbox.w, py = pbox.top + (pr.y + pr.h / 2) * pbox.h;
  (document.elementFromPoint(px, py) || input).dispatchEvent(new PointerEvent("pointerdown", { clientX: px, clientY: py, pointerId: 9, bubbles: true, button: 0, buttons: 1 }));
  input.dispatchEvent(new PointerEvent("pointerup", { clientX: px, clientY: py, pointerId: 9, bubbles: true, button: 0 }));
  for (let i = 0; i < 40 && aeF("p0").value === ""; i++) await sleep(100);
  const picked = getVec("position");
  check(picked && picked.every(Number.isFinite) && !anno.picking, `pick on view 0 center -> ${JSON.stringify(picked)} (${$("#ae-msg").textContent})`);
  $("#ae-id").value = "web_pick"; $("#ae-label").value = "Picked";
  r = await applyForm();
  check(r.ok && anno.selected === "web_pick" && document.querySelector('#anno-rows .row.sel [data-anno-sel="web_pick"]'), `apply new label + selected ${anno.selected}`);
  aeF("p2").value = String(Number(aeF("p2").value) + 0.25);
  r = await applyForm();
  const zOf = () => anno.list.find((a) => a.id === "web_pick")?.position?.[2];
  for (let i = 0; i < 20 && picked && Math.abs(zOf() - (picked[2] + 0.25)) >= 2e-3; i++) await sleep(100); // state 푸시로 목록 갱신 대기
  const moved = anno.list.find((a) => a.id === "web_pick");
  check(r.ok && picked && moved && Math.abs(moved.position[2] - (picked[2] + 0.25)) < 2e-3, `numeric nudge z ${picked?.[2]} -> ${moved?.position?.[2]}`);
  $("#ae-del").click();
  for (let i = 0; i < 20 && anno.list.some((a) => a.id === "web_pick"); i++) await sleep(100);
  check(!anno.list.some((a) => a.id === "web_pick") && anno.selected === null, `form delete clears selection (${anno.selected})`);
  // 면 선택: 뷰 중앙 면 → 정보 표시(state face) → 단면 폼에 면 중심·법선 → 해제
  $("#ae-new").click();
  $("#ae-face").click();
  (document.elementFromPoint(px, py) || input).dispatchEvent(new PointerEvent("pointerdown", { clientX: px, clientY: py, pointerId: 9, bubbles: true, button: 0, buttons: 1 }));
  input.dispatchEvent(new PointerEvent("pointerup", { clientX: px, clientY: py, pointerId: 9, bubbles: true, button: 0 }));
  for (let i = 0; i < 40 && !anno.face; i++) await sleep(100);
  const fc = anno.face;
  check(fc && fc.center?.length === 3 && fc.normal?.length === 3 && typeof fc.area === "number" && fc.kind && fc.kind !== "point" && fc.outline?.length > 0 && !anno.picking,
    `pick_face view 0 center -> ${fc?.kind} center ${JSON.stringify(fc?.center)} area ${fc?.area} outline ${fc?.outline?.length}`);
  $("#ae-kind").value = "plane"; $("#ae-kind").dispatchEvent(new Event("change"));
  $("#ae-face-use").click();
  const fo = getVec("position"), fn = getVec("direction");
  check(fc && fo && fn && fo.every((x, i) => Math.abs(x - fc.center[i]) < 2e-3) && fn.every((x, i) => Math.abs(x - fc.normal[i]) < 2e-3), `face center -> plane form origin ${JSON.stringify(fo)} normal ${JSON.stringify(fn)}`);
  $("#ae-face-clear").click();
  for (let i = 0; i < 20 && anno.face; i++) await sleep(100);
  check(anno.face === null && $("#ae-face-info").textContent === "없음", `clear_face`);
  loadForm(null);
  const descCb = $("#anno-desc");
  descCb.checked = false; descCb.dispatchEvent(new Event("change", { bubbles: true }));
  for (let i = 0; i < 20 && anno.showDesc !== false; i++) await sleep(100);
  r = await request("morph.annotation.overlay.set_options", { text_scale: 9 });
  check(anno.showDesc === false && !r.ok && r.error === "invalid_argument", `anno options: description off, text_scale 9 -> ${r.error}`);
  r = await request("morph.annotation.overlay.set_options", { show_description: true, text_scale: 1 });
  check(r.ok && r.show_description === true && r.text_scale === 1, `anno options restore ${JSON.stringify(r)}`);
  r = await request("morph.annotation.overlay.reload", {});
  for (let i = 0; i < 20 && anno.list.find((a) => a.id === first?.id)?.visible !== true; i++) await sleep(100);
  check(r.ok && r.count === n0 && anno.list.find((a) => a.id === first?.id)?.visible === true, `anno reload restores file (${r.count})`);
  const passed = state.results.filter((x) => x.ok).length;
  log("sys", `self test ${passed}/${state.results.length} passed`);
  state.done = true;
  return state.results;
}
window.__morphSelfTest = selfTest;

// 받은 영상 프레임과 self test 결과를 개발 서버(_out/)에 저장 (vite.config.js의 /__save)
async function saveArtifacts() {
  const v = $("#remote-video");
  const c = document.createElement("canvas");
  c.width = v.videoWidth;
  c.height = v.videoHeight;
  c.getContext("2d").drawImage(v, 0, 0);
  const blob = await new Promise((r) => c.toBlob(r, "image/png"));
  await fetch("/__save?name=frame.png", { method: "POST", body: blob });
  await fetch("/__save?name=selftest.json", { method: "POST", body: JSON.stringify(state.results, null, 2) });
  return `${c.width}x${c.height}`;
}
window.__morphSave = saveArtifacts;

// --- 연결 ---
const status = $("#status");
AppStreamer.connect({
  streamSource: StreamType.DIRECT,
  streamConfig: {
    videoElementId: "remote-video",
    audioElementId: "remote-audio",
    authenticate: false,
    maxReconnects: 5,
    signalingServer: SERVER,
    signalingPort: SIGNALING_PORT,
    mediaServer: SERVER,
    nativeTouchEvents: true,
    width: 1920,
    height: 1080,
    fps: 60,
    onStart: (e) => {
      log("sys", `onStart ${JSON.stringify(e)}`);
      if (e.status === "success") {
        state.connected = true;
        status.textContent = `연결됨 ${SERVER}:${SIGNALING_PORT}`;
        // 푸시(split.state, camera.state, perf.report)는 hello 뒤부터 온다
        // 연결 직후에는 메시징 채널이 아직 준비되지 않아 첫 hello가 묻힐 수 있어 회신이 올 때까지 다시 보낸다
        (async () => {
          for (let i = 0; i < 10 && !state.hello; i++) {
            const r = await request("morph.stream.messaging.hello", { client_id: "web-test" }, 2000);
            state.hello = state.hello || r.ok;
          }
          if (state.hello) { refreshCameras(); refreshPresets(); refreshScenes(); refreshAnnos(); }
        })();
      } else {
        status.textContent = `시작 실패: ${e.info || e.status}`;
      }
    },
    onUpdate: (e) => log("sys", `onUpdate ${JSON.stringify(e)}`),
    onCustomEvent,
    onStop: (e) => log("sys", `onStop ${JSON.stringify(e)}`),
    onTerminate: (e) => { state.connected = false; status.textContent = "연결 끊김"; log("sys", `onTerminate ${JSON.stringify(e)}`); },
  },
}).catch((e) => {
  status.textContent = `연결 실패: ${e?.info || e}`;
  log("sys", `connect failed ${JSON.stringify(e)}`);
});

window.addEventListener("beforeunload", () => {
  try { notify("morph.stream.messaging.bye", { client_id: "web-test" }); } catch {}
});
