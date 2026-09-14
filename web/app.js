// 다음걸음 프론트엔드 — 빌드 도구 없는 순수 JS. api.py가 이미 검증된 모든
// 로직을 갖고 있으므로, 이 파일은 화면 그리기 + fetch 호출만 한다.
const API_BASE = "http://localhost:8000";

const STAGE_LABELS = { "준비": "폐업 준비", "진행": "폐업 진행", "후": "폐업 후" };
const EQUIPMENT_CATEGORIES = ["주방/조리기기", "냉장/냉동", "카페/음료기기", "집기/가구", "전자기기", "기타"];
const CONDITION_OPTIONS = ["상", "중", "하"];
const STATUS_OPTIONS = ["보관 중", "판매 중", "예약", "처분 완료"];
const PAYMENT_OPTIONS = ["미입금", "입금 완료"];
const BUSINESS_TYPES = ["카페", "식당/분식", "편의점/소매", "미용/뷰티"];

// ---------- API 클라이언트 ----------

async function apiFetch(path, options = {}) {
  const res = await fetch(API_BASE + path, {
    ...options,
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch (_) {
      // 응답 본문이 JSON이 아니면 statusText 그대로 사용
    }
    throw new Error(detail);
  }
  if (res.status === 204) return null;
  return res.json();
}

// photo-analysis는 multipart/form-data라 apiFetch의 JSON Content-Type과 맞지 않아
// 별도 헬퍼로 분리한다(에러 처리 방식은 apiFetch와 동일하게 맞춤).
async function analyzeEquipmentPhoto(file) {
  const formData = new FormData();
  formData.append("photo", file);
  const res = await fetch(API_BASE + "/equipment/photo-analysis", {
    method: "POST",
    credentials: "include",
    body: formData,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch (_) {
      // 응답 본문이 JSON이 아니면 statusText 그대로 사용
    }
    throw new Error(detail);
  }
  const data = await res.json();
  return data.text;
}

// ---------- 화면 전환 ----------

function showView(id) {
  document.querySelectorAll(".view").forEach((v) => (v.hidden = true));
  document.getElementById(id).hidden = false;
}

function setActiveNav(page) {
  document.querySelectorAll(".nav-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.page === page);
  });
}

// ---------- 로그인 ----------

const loginForm = document.getElementById("login-form");
const loginError = document.getElementById("login-error");

loginForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const name = document.getElementById("login-name").value.trim();
  if (!name) return;
  loginError.hidden = true;
  try {
    const user = await apiFetch("/auth/login", {
      method: "POST",
      body: JSON.stringify({ name }),
    });
    enterApp(user.user_id);
  } catch (err) {
    loginError.textContent = err.message;
    loginError.hidden = false;
  }
});

document.getElementById("logout-btn").addEventListener("click", async () => {
  try {
    await apiFetch("/auth/logout", { method: "POST" });
    location.reload();
  } catch (err) {
    // 서버 세션이 실제로는 안 끊겼을 수 있으니, 성공했을 때만 새로고침한다 —
    // 공용 컴퓨터에서 로그아웃했다고 믿었는데 세션이 살아있으면 안 되기 때문.
    alert(`로그아웃에 실패했습니다: ${err.message}`);
  }
});

function enterApp(userId) {
  document.getElementById("user-name").textContent = userId;
  showView("app-view");
  navigateTo("dashboard");
}

// ---------- 네비게이션 ----------

document.querySelectorAll(".nav-btn").forEach((btn) => {
  btn.addEventListener("click", () => navigateTo(btn.dataset.page));
});

function navigateTo(page) {
  setActiveNav(page);
  if (page === "dashboard") renderDashboard();
  else if (page.startsWith("stage:")) renderStageChat(page.slice("stage:".length));
  else if (page === "policies") renderPolicies();
  else if (page === "equipment") renderEquipment();
  else if (page === "marketplace") renderMarketplace();
}

// ---------- 대시보드 ----------

async function renderDashboard() {
  const main = document.getElementById("main-content");
  main.innerHTML = `<h2>폐업 진행 상황</h2><p class="loading">불러오는 중...</p>`;
  try {
    const [dash, tasks] = await Promise.all([apiFetch("/dashboard"), apiFetch("/tasks")]);
    main.innerHTML = `
      <h2>폐업 진행 상황</h2>
      <div class="stats">
        <div class="stat"><span>폐업 예정일</span><strong>${dash.profile.planned_close_date || "미정"}</strong></div>
        <div class="stat"><span>진행률</span><strong>${dash.completed}/${dash.total} 완료</strong></div>
      </div>
      <h3>할 일 체크리스트</h3>
      <ul id="task-list"></ul>
    `;
    const list = document.getElementById("task-list");
    tasks.forEach((t) => {
      const li = document.createElement("li");
      const checked = t.status === "사용자 완료";
      li.innerHTML = `
        <input type="checkbox" ${checked ? "checked" : ""} data-task-id="${t.id}">
        <span>[${escapeHtml(STAGE_LABELS[t.stage] || t.stage)}] ${escapeHtml(t.task_title)}</span>
        <span class="due">${t.due_date || "기한 미정"}</span>
      `;
      li.querySelector("input").addEventListener("change", async (e) => {
        const checkbox = e.target;
        const newStatus = checkbox.checked ? "사용자 완료" : "진행 중";
        checkbox.disabled = true;
        try {
          await apiFetch(`/tasks/${t.id}`, { method: "PATCH", body: JSON.stringify({ status: newStatus }) });
          renderDashboard();
        } catch (err) {
          checkbox.checked = !checkbox.checked;
          checkbox.disabled = false;
          alert(`저장에 실패했습니다: ${err.message}`);
        }
      });
      list.appendChild(li);
    });
  } catch (err) {
    main.innerHTML = `<h2>폐업 진행 상황</h2><p class="error">${escapeHtml(err.message)}</p>`;
  }
}

// ---------- 단계별 챗봇 (멀티에이전트) ----------

function renderStageChat(stageKey) {
  const label = STAGE_LABELS[stageKey] || stageKey;
  const main = document.getElementById("main-content");
  main.innerHTML = `
    <h2>${label} 챗봇</h2>
    <p class="loading" style="margin-bottom:8px;">질문 성격에 따라 가이드/정책상담/거래상담 에이전트가 자동으로 답합니다.</p>
    <div id="chat-messages"></div>
    <form id="chat-form">
      <input type="text" id="chat-input" placeholder="${label} 챗봇에게 물어보세요" autocomplete="off">
      <button type="submit">전송</button>
    </form>
  `;

  const messagesEl = document.getElementById("chat-messages");
  const form = document.getElementById("chat-form");
  const input = document.getElementById("chat-input");

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const question = input.value.trim();
    if (!question) return;
    input.value = "";
    input.disabled = true;

    appendMessage(messagesEl, "user", question);
    const pending = appendMessage(messagesEl, "assistant", "생각하는 중...", null, true);

    try {
      const res = await apiFetch("/chat", {
        method: "POST",
        body: JSON.stringify({ stage_key: stageKey, question }),
      });
      let text = res.answer;
      if (res.source_type === "web_search") {
        text = `[미검토 · 실시간 검색 결과]\n\n${text}\n\n이 답변은 등록 자료가 아니라 실시간 검색 결과입니다. 반드시 공식 사이트에서 확인하세요.`;
      }
      pending.querySelector(".bubble").textContent = text;
      const badge = document.createElement("div");
      badge.className = "agent-badge";
      badge.textContent = res.agent_name;
      pending.insertBefore(badge, pending.querySelector(".bubble"));
    } catch (err) {
      pending.querySelector(".bubble").textContent = `오류: ${err.message}`;
    } finally {
      input.disabled = false;
      input.focus();
      messagesEl.scrollTop = messagesEl.scrollHeight;
    }
  });
}

function appendMessage(container, role, text) {
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  const roleLabel = role === "user" ? "나" : "다음걸음";
  div.innerHTML = `<div class="role">${roleLabel}</div><div class="bubble">${escapeHtml(text)}</div>`;
  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
  return div;
}

// ---------- 정책 목록 ----------

async function renderPolicies() {
  const main = document.getElementById("main-content");
  main.innerHTML = `<h2>지원정책</h2><p class="loading">불러오는 중...</p>`;
  try {
    const policies = await apiFetch("/policies");
    if (!policies.length) {
      main.innerHTML = `<h2>지원정책</h2><p class="empty">현재 조건에 맞는 등록된 지원사업이 없습니다.</p>`;
      return;
    }
    const cards = policies
      .map(
        (p) => `
      <div class="policy-card">
        <h3>${escapeHtml(p.title)}</h3>
        <div class="meta">${escapeHtml(p.agency || "기관 확인 필요")} · 마감 ${p.application_deadline || "상시/미정"}</div>
        <div class="meta">${escapeHtml(p.label)}</div>
        <div class="score-bar"><span style="width:${p.fit_score}%"></span></div>
        <div class="meta">AI 적합도 ${p.fit_score}/100</div>
      </div>`
      )
      .join("");
    main.innerHTML = `<h2>지원정책</h2>${cards}`;
  } catch (err) {
    main.innerHTML = `<h2>지원정책</h2><p class="error">${escapeHtml(err.message)}</p>`;
  }
}

// ---------- 중고품 관리 ----------

async function renderEquipment() {
  const main = document.getElementById("main-content");
  main.innerHTML = `
    <h2>중고품 관리</h2>
    <details class="equipment-form-wrap">
      <summary>사진으로 물품 인식 (선택, 등록 전 참고용)</summary>
      <p class="meta">사진을 올리고 분석하면 품목·브랜드·상태를 AI가 추정해 알려드려요. 확정된 정보가 아니니 실제 값은 직접 확인한 뒤 아래 '새 물품 등록' 폼에 입력하세요.</p>
      <div class="actions">
        <input type="file" id="eq-photo-input" accept="image/jpeg,image/png">
        <button type="button" id="eq-photo-analyze-btn" class="secondary">AI로 분석하기</button>
      </div>
      <p id="eq-photo-result"></p>
    </details>
    <details class="equipment-form-wrap">
      <summary>새 물품 등록</summary>
      <form id="equipment-form" class="equipment-form">
        <input type="text" id="eq-name" placeholder="물품명" required>
        <select id="eq-category">${EQUIPMENT_CATEGORIES.map((c) => `<option>${c}</option>`).join("")}</select>
        <input type="text" id="eq-model" placeholder="모델 (모르면 비워두세요)">
        <input type="text" id="eq-used-period" placeholder="사용 기간 (예: 2년, 6개월)">
        <select id="eq-condition">${CONDITION_OPTIONS.map((c) => `<option${c === "중" ? " selected" : ""}>${c}</option>`).join("")}</select>
        <input type="text" id="eq-defects" placeholder="하자">
        <select id="eq-ownership"><option>본인 소유</option><option>확인 필요</option></select>
        <input type="text" id="eq-asking-price" placeholder="희망 가격">
        <input type="text" id="eq-pickup-terms" placeholder="수거 조건">
        <button type="submit">등록</button>
      </form>
      <p id="equipment-form-error" class="error" hidden></p>
    </details>
    <div id="equipment-list"><p class="loading">불러오는 중...</p></div>
  `;

  document.getElementById("eq-photo-analyze-btn").addEventListener("click", async () => {
    const fileInput = document.getElementById("eq-photo-input");
    const resultEl = document.getElementById("eq-photo-result");
    const file = fileInput.files[0];
    if (!file) return;
    resultEl.textContent = "분석 중...";
    try {
      const text = await analyzeEquipmentPhoto(file);
      resultEl.innerHTML = `<strong>AI 추정:</strong> ${escapeHtml(text)}<br><em>참고용 설명이며, 실제 값은 아래 등록 폼에 직접 입력해주세요.</em>`;
    } catch (err) {
      resultEl.textContent = `사진 분석에 실패했습니다: ${err.message}`;
    }
  });

  document.getElementById("equipment-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const errorEl = document.getElementById("equipment-form-error");
    errorEl.hidden = true;
    const name = document.getElementById("eq-name").value.trim();
    if (!name) return;
    const submitBtn = e.target.querySelector("button[type=submit]");
    submitBtn.disabled = true;
    try {
      await apiFetch("/equipment", {
        method: "POST",
        body: JSON.stringify({
          name,
          category: document.getElementById("eq-category").value,
          model: document.getElementById("eq-model").value.trim() || null,
          used_period: document.getElementById("eq-used-period").value.trim() || null,
          condition: document.getElementById("eq-condition").value,
          defects: document.getElementById("eq-defects").value.trim() || null,
          ownership_status: document.getElementById("eq-ownership").value,
          asking_price: document.getElementById("eq-asking-price").value.trim() || null,
          pickup_terms: document.getElementById("eq-pickup-terms").value.trim() || null,
        }),
      });
      renderEquipment();
    } catch (err) {
      errorEl.textContent = err.message;
      errorEl.hidden = false;
      submitBtn.disabled = false;
    }
  });

  await loadEquipmentList();
}

async function loadEquipmentList() {
  const listEl = document.getElementById("equipment-list");
  try {
    const items = await apiFetch("/equipment");
    if (!items.length) {
      listEl.innerHTML = `<p class="empty">등록된 물품이 없습니다.</p>`;
      return;
    }
    listEl.innerHTML = items.map(renderEquipmentCard).join("");
    items.forEach((item) => wireEquipmentCard(item));
  } catch (err) {
    listEl.innerHTML = `<p class="error">${escapeHtml(err.message)}</p>`;
  }
}

function renderEquipmentCard(item) {
  return `
    <div class="item-card" data-eq-id="${item.id}">
      <h3>${escapeHtml(item.name)}</h3>
      <div class="meta">카테고리: ${escapeHtml(item.category || "미지정")} · 모델: ${escapeHtml(item.model || "확인 필요")} · 상태: ${escapeHtml(item.condition || "확인 필요")} · 하자: ${escapeHtml(item.defects || "확인 필요")}</div>
      <div class="meta">희망 가격: ${escapeHtml(item.asking_price || "확인 필요")} · 수거 조건: ${escapeHtml(item.pickup_terms || "확인 필요")}</div>
      ${item.interest_count > 0 ? `<div class="meta">관심 표시 ${item.interest_count}명</div>` : ""}
      <div class="actions">
        <select class="eq-style" data-id="${item.id}">
          <option value="short">짧은 글</option>
          <option value="blog">블로그 스타일</option>
        </select>
        <button type="button" class="eq-gen-btn secondary" data-id="${item.id}">판매 글 생성 (AI)</button>
      </div>
      <div class="actions">
        <select class="eq-status" data-id="${item.id}">
          ${STATUS_OPTIONS.map((s) => `<option${s === item.status ? " selected" : ""}>${s}</option>`).join("")}
        </select>
        <select class="eq-payment" data-id="${item.id}">
          ${PAYMENT_OPTIONS.map((p) => `<option${p === item.payment_status ? " selected" : ""}>${p}</option>`).join("")}
        </select>
      </div>
      <div class="eq-listing-result"></div>
      ${item.draft ? `<div class="draft-box">${escapeHtml(item.draft)}</div>` : ""}
      ${item.status === "처분 완료" ? renderSaleSection(item) : ""}
    </div>
  `;
}

function renderSaleSection(item) {
  if (item.final_price) {
    return `<p class="meta">실제 판매가 기록됨: ${item.final_price.toLocaleString()}원 (AI 시세 학습에 반영됨)</p>`;
  }
  return `
    <div class="actions">
      <input type="text" class="eq-final-price" data-id="${item.id}" placeholder="실제 판매가 (예: 120만원)">
      <button type="button" class="eq-record-sale-btn secondary" data-id="${item.id}">판매가 기록</button>
    </div>
  `;
}

function wireEquipmentCard(item) {
  const card = document.querySelector(`.item-card[data-eq-id="${item.id}"]`);
  if (!card) return;

  card.querySelector(".eq-status").addEventListener("change", async (e) => {
    const select = e.target;
    const previous = item.status;
    select.disabled = true;
    try {
      await apiFetch(`/equipment/${item.id}`, { method: "PATCH", body: JSON.stringify({ status: select.value }) });
      loadEquipmentList();
    } catch (err) {
      select.value = previous;
      select.disabled = false;
      alert(`저장에 실패했습니다: ${err.message}`);
    }
  });
  card.querySelector(".eq-payment").addEventListener("change", async (e) => {
    const select = e.target;
    const previous = item.payment_status;
    select.disabled = true;
    try {
      await apiFetch(`/equipment/${item.id}`, { method: "PATCH", body: JSON.stringify({ payment_status: select.value }) });
      loadEquipmentList();
    } catch (err) {
      select.value = previous;
      select.disabled = false;
      alert(`저장에 실패했습니다: ${err.message}`);
    }
  });
  const genBtn = card.querySelector(".eq-gen-btn");
  genBtn.addEventListener("click", async () => {
    const style = card.querySelector(".eq-style").value;
    const resultEl = card.querySelector(".eq-listing-result");
    genBtn.disabled = true;
    resultEl.textContent = "생성 중...";
    try {
      const res = await apiFetch(`/equipment/${item.id}/listing`, {
        method: "POST",
        body: JSON.stringify({ style }),
      });
      let priceInfo = "";
      if (res.predicted_price != null) {
        const range = res.price_range || [];
        const rangeText = range.length === 2 ? ` (유사 사례 ${range[0].toLocaleString()}~${range[1].toLocaleString()}원)` : "";
        priceInfo = `<p class="meta">AI 추천가: ${res.predicted_price.toLocaleString()}원${rangeText}</p>`;
      }
      const tipText = res.channel_tip ? `<p class="meta">${escapeHtml(res.channel_tip)}</p>` : "";
      resultEl.innerHTML = `${priceInfo}<div class="draft-box">${escapeHtml(res.draft)}</div>${tipText}`;
    } catch (err) {
      resultEl.innerHTML = `<p class="error">${escapeHtml(err.message)}</p>`;
    } finally {
      genBtn.disabled = false;
    }
  });

  const recordBtn = card.querySelector(".eq-record-sale-btn");
  if (recordBtn) {
    recordBtn.addEventListener("click", async () => {
      const priceText = card.querySelector(".eq-final-price").value.trim();
      if (!priceText) return;
      recordBtn.disabled = true;
      try {
        await apiFetch(`/equipment/${item.id}/sale`, {
          method: "POST",
          body: JSON.stringify({ price_text: priceText }),
        });
        loadEquipmentList();
      } catch (err) {
        alert(err.message);
        recordBtn.disabled = false;
      }
    });
  }
}

// ---------- 설비 매칭 (마켓플레이스) ----------

async function renderMarketplace() {
  const main = document.getElementById("main-content");
  main.innerHTML = `
    <h2>설비 매칭</h2>
    <p class="loading" style="margin-bottom:8px;">폐업을 준비 중인 사장님들이 '판매 중'으로 등록한 설비를 예비 창업자가 카테고리·지역으로 찾아볼 수 있는 화면입니다.</p>
    <div class="filter-bar">
      <select id="mp-business"><option value="">업종 선택 안 함</option>${BUSINESS_TYPES.map((b) => `<option>${b}</option>`).join("")}</select>
      <select id="mp-category"><option value="">전체 카테고리</option></select>
      <select id="mp-region"><option value="">전체 지역</option></select>
    </div>
    <div id="marketplace-list"><p class="loading">불러오는 중...</p></div>
  `;

  let allListings;
  try {
    allListings = await apiFetch("/marketplace");
  } catch (err) {
    document.getElementById("marketplace-list").innerHTML = `<p class="error">${escapeHtml(err.message)}</p>`;
    return;
  }

  const categories = [...new Set(allListings.map((l) => l.category).filter(Boolean))].sort();
  const regions = [...new Set(allListings.map((l) => l.region).filter(Boolean))].sort();
  const categorySelect = document.getElementById("mp-category");
  categorySelect.innerHTML =
    `<option value="">전체 카테고리</option>` + categories.map((c) => `<option>${escapeHtml(c)}</option>`).join("");
  const regionSelect = document.getElementById("mp-region");
  regionSelect.innerHTML =
    `<option value="">전체 지역</option>` + regions.map((r) => `<option>${escapeHtml(r)}</option>`).join("");

  [document.getElementById("mp-business"), categorySelect, regionSelect].forEach((el) =>
    el.addEventListener("change", loadMarketplaceList)
  );

  // 필터를 아직 아무것도 안 고른 첫 렌더는 방금 받아온 allListings 그대로가
  // 곧 "필터 없음" 결과와 같으므로, 같은 데이터를 다시 요청하지 않고 바로 그린다.
  renderMarketplaceItems(allListings, false);
}

async function loadMarketplaceList() {
  const listEl = document.getElementById("marketplace-list");
  listEl.innerHTML = `<p class="loading">불러오는 중...</p>`;
  const business = document.getElementById("mp-business").value;
  const category = document.getElementById("mp-category").value;
  const region = document.getElementById("mp-region").value;
  const params = new URLSearchParams();
  if (business) params.set("business_type", business);
  if (category) params.set("category", category);
  if (region) params.set("region", region);

  try {
    const items = await apiFetch(`/marketplace?${params.toString()}`);
    renderMarketplaceItems(items, !!business);
  } catch (err) {
    listEl.innerHTML = `<p class="error">${escapeHtml(err.message)}</p>`;
  }
}

function renderMarketplaceItems(items, showMatch) {
  const listEl = document.getElementById("marketplace-list");
  if (!items.length) {
    listEl.innerHTML = `<p class="empty">조건에 맞는 판매 중인 설비가 없습니다.</p>`;
    return;
  }
  listEl.innerHTML = items.map((item) => renderMarketplaceCard(item, showMatch)).join("");
  items.forEach((item) => wireMarketplaceCard(item));
}

function renderMarketplaceCard(item, showMatch) {
  let priceDiff = "";
  if (item.price_diff_pct != null && item.price_diff_pct !== 0) {
    const cheap = item.price_diff_pct > 0;
    priceDiff = `<div class="price-diff ${cheap ? "cheap" : "pricey"}">AI 추정 시세 대비 약 ${Math.abs(item.price_diff_pct)}% ${cheap ? "저렴" : "비쌈"}</div>`;
  }
  const matchBlock =
    showMatch && item.match_score != null
      ? `<div class="score-bar"><span style="width:${item.match_score}%"></span></div><div class="meta">추천도 ${item.match_score}/100</div>`
      : "";
  return `
    <div class="item-card">
      <h3>${escapeHtml(item.name)}</h3>
      <div class="meta">카테고리: ${escapeHtml(item.category || "확인 필요")} · 지역: ${escapeHtml(item.region || "확인 필요")} · 상태: ${escapeHtml(item.condition || "확인 필요")}</div>
      <div class="meta">희망 가격: ${escapeHtml(item.asking_price || "확인 필요")} · 수거 조건: ${escapeHtml(item.pickup_terms || "확인 필요")}</div>
      ${matchBlock}
      ${priceDiff}
      ${item.draft ? `<div class="draft-box">${escapeHtml(item.draft)}</div>` : ""}
      <div class="actions">
        <button type="button" class="mp-interest-btn secondary" data-id="${item.id}">관심 표시</button>
      </div>
    </div>
  `;
}

function wireMarketplaceCard(item) {
  const btn = document.querySelector(`.mp-interest-btn[data-id="${item.id}"]`);
  if (!btn) return;
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    try {
      await apiFetch(`/marketplace/${item.id}/interest`, { method: "POST" });
      btn.textContent = "관심 표시 완료";
    } catch (err) {
      alert(err.message);
      btn.disabled = false;
    }
  });
}

// ---------- 유틸 ----------

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str ?? "";
  return div.innerHTML;
}

// ---------- 시작: 이미 로그인된 세션인지 확인 ----------

(async function init() {
  try {
    const me = await apiFetch("/auth/me");
    enterApp(me.user_id);
  } catch (_) {
    showView("login-view");
  }
})();
