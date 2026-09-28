const SESSION_KEY = "assistente_lab_session";
const CART_KEY = "assistente_lab_cart";
const TEST_RUN_KEY = "assistente_lab_test_run";

/** UUID seguro também em HTTP (DuckDNS:8080 não é secure context → sem crypto.randomUUID). */
function newRequestId() {
  try {
    if (globalThis.crypto && typeof globalThis.crypto.randomUUID === "function") {
      return globalThis.crypto.randomUUID();
    }
  } catch (_) {}
  return `req-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

/** Host público da página (EC2/DuckDNS) — nunca deixar 127.0.0.1 nos links de Rastreio. */
function publicHostname() {
  return window.location.hostname || "127.0.0.1";
}

function isLocalHost(h = publicHostname()) {
  return h === "127.0.0.1" || h === "localhost";
}

function jaegerBase() {
  const h = publicHostname();
  return isLocalHost(h) ? "http://127.0.0.1:16686" : `http://${h}:16686`;
}

function graylogBase() {
  const h = publicHostname();
  if (isLocalHost(h)) return "http://127.0.0.1:9000";
  if (h.includes("duckdns.org") || h.endsWith(".nip.io")) return `https://${h}`;
  return `http://${h}:9000`;
}

/** Reescreve 127.0.0.1 → host atual (headers antigos / links estáticos). */
function publicizeUrl(url) {
  if (!url) return url;
  return String(url)
    .replace(/https?:\/\/(127\.0\.0\.1|localhost):16686/g, jaegerBase())
    .replace(/https?:\/\/(127\.0\.0\.1|localhost):9000/g, graylogBase());
}

function rewriteStaticObsLinks() {
  document.querySelectorAll('a[href*="127.0.0.1"], a[href*="localhost"]').forEach((a) => {
    a.href = publicizeUrl(a.getAttribute("href"));
  });
}

function ensureTestRunId() {
  let id = sessionStorage.getItem(TEST_RUN_KEY);
  if (!id) {
    id = `ui-${new Date().toISOString().slice(0, 19).replace(/[-:T]/g, "")}`;
    sessionStorage.setItem(TEST_RUN_KEY, id);
  }
  window.__TEST_RUN_ID = id;
  return id;
}
ensureTestRunId();

function loadSession() {
  try {
    const raw = localStorage.getItem(SESSION_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (parsed?.token) return parsed;
    }
  } catch (_) {}
  const token = localStorage.getItem("lab_token") || "";
  if (!token) return { token: "", staffId: "", name: "", storeId: "1001" };
  return {
    token,
    staffId: localStorage.getItem("lab_staff") || "",
    name: localStorage.getItem("lab_name") || "",
    storeId: localStorage.getItem("lab_store") || "1001",
  };
}

function loadCart() {
  try {
    const raw = localStorage.getItem(CART_KEY);
    if (raw) return JSON.parse(raw) || [];
  } catch (_) {}
  return [];
}

const saved = loadSession();
const state = {
  token: saved.token || "",
  staffId: saved.staffId || "",
  name: saved.name || "",
  storeId: saved.storeId || "1001",
  cart: loadCart(),
  customer: null,
  customerValidated: false,
  selectedPlan: null,
  plans: [],
  limits: null,
  limitType: "CDC",
  act: 1,
  lastSale: null,
  monitorFilter: "all",
  pendingCount: 0,
  activeMod: "sale",
  obs: {
    lastTraceId: "",
    lastRequestId: "",
    lastStep: "",
    lastStatus: "",
    jaegerTrace: "",
    jaegerJourney: "",
    graylogTrace: "",
    graylogJourney: "",
  },
};

const el = {
  userChip: document.getElementById("userChip"),
  storePill: document.getElementById("storePill"),
  btnLoginToggle: document.getElementById("btnLoginToggle"),
  btnPreSale: document.getElementById("btnPreSale"),
  btnCreateCp: document.getElementById("btnCreateCp"),
  financedBox: document.getElementById("financedBox"),
  planList: document.getElementById("planList"),
  plansHint: document.getElementById("plansHint"),
  cartLines: document.getElementById("cartLines"),
  cartTotal: document.getElementById("cartTotal"),
  toastHost: document.getElementById("toastHost"),
  busy: document.getElementById("busy"),
  busyText: document.getElementById("busyText"),
  actRail: document.getElementById("actRail"),
  modNav: document.getElementById("modNav"),
  monitorCount: document.getElementById("monitorCount"),
  btnOpenMonitor: document.getElementById("btnOpenMonitor"),
};

function journeyStep(explicit) {
  if (explicit) return explicit;
  if (state.activeMod && state.activeMod !== "sale") return `mod.${state.activeMod}`;
  return `sale.act${state.act}`;
}

/** Passos estáveis da jornada de venda (funil Graylog). */
const JOURNEY = {
  login: "sale.login",
  validateCustomer: "sale.validate_customer",
  creditLimit: "sale.check_credit",
  searchProduct: "sale.search_product",
  simulatePlan: "sale.simulate_plan",
  listPlans: "sale.list_plans",
  createPresale: "sale.create_presale",
  listProposals: "sale.list_proposals",
  integrateProposal: "sale.integrate_proposal",
  createCp: "sale.create_cp",
};

function headers(json = true, step = "") {
  const h = {
    "X-Request-Id": newRequestId(),
    "X-Test-Run-Id": ensureTestRunId(),
    "X-Journey-Step": journeyStep(step),
  };
  if (json) h["Content-Type"] = "application/json";
  if (state.staffId) h["X-Staff-Id"] = state.staffId;
  if (state.token) h["Authorization"] = `Bearer ${state.token}`;
  return h;
}

function recordObs(res, step) {
  const traceId = res.headers.get("X-Trace-Id") || "";
  const requestId = res.headers.get("X-Request-Id") || "";
  const spmService = res.headers.get("X-Obs-Spm-Service") || "assistente-plataforma";
  const domain = res.headers.get("X-Obs-Domain") || "";
  state.obs = {
    lastTraceId: traceId,
    lastRequestId: requestId,
    lastStep: res.headers.get("X-Journey-Step") || step || "",
    lastStatus: String(res.status),
    domain,
    spmService,
    jaegerTrace: publicizeUrl(
      res.headers.get("X-Obs-Jaeger") || (traceId ? `${jaegerBase()}/trace/${traceId}` : "")
    ),
    jaegerJourney: publicizeUrl(
      res.headers.get("X-Obs-Jaeger-Journey") ||
        `${jaegerBase()}/search?service=${encodeURIComponent(spmService)}&tags=${encodeURIComponent(
          JSON.stringify({ "test_run.id": ensureTestRunId() })
        )}`
    ),
    jaegerSpm: publicizeUrl(res.headers.get("X-Obs-Jaeger-Spm") || `${jaegerBase()}/monitor`),
    graylogTrace: publicizeUrl(
      res.headers.get("X-Obs-Graylog-Trace") ||
        (traceId
          ? `${graylogBase()}/search?q=trace_id%3A${traceId}&rangetype=relative&relative=86400`
          : "")
    ),
    graylogJourney: publicizeUrl(
      res.headers.get("X-Obs-Graylog-Journey") ||
        `${graylogBase()}/search?q=test_run_id%3A%22${encodeURIComponent(ensureTestRunId())}%22&rangetype=relative&relative=86400`
    ),
  };
  renderObsDock();
}

function renderObsDock() {
  const dock = document.getElementById("obsDock");
  if (!dock) return;
  const o = state.obs;
  const run = ensureTestRunId();
  document.getElementById("obsTestRun").textContent = run;
  document.getElementById("obsTrace").textContent = o.lastTraceId ? o.lastTraceId.slice(0, 16) + "…" : "—";
  document.getElementById("obsStep").textContent = o.lastStep || "—";
  document.getElementById("obsStatus").textContent = o.lastStatus || "—";
  const jaegerTrace = document.getElementById("obsLinkJaegerTrace");
  const jaegerJourney = document.getElementById("obsLinkJaegerJourney");
  const graylogTrace = document.getElementById("obsLinkGraylogTrace");
  const graylogJourney = document.getElementById("obsLinkGraylogJourney");
  const spm = document.getElementById("obsLinkSpm");
  jaegerTrace.href = o.jaegerTrace || "#";
  jaegerJourney.href = o.jaegerJourney || "#";
  graylogTrace.href = o.graylogTrace || "#";
  graylogJourney.href = o.graylogJourney || "#";
  if (spm) {
    spm.href = o.jaegerSpm || `${jaegerBase()}/monitor`;
    spm.textContent = o.spmService ? `SPM · ${o.spmService.replace("assistente-", "")}` : "SPM desta função";
  }
  jaegerTrace.classList.toggle("disabled", !o.jaegerTrace);
  graylogTrace.classList.toggle("disabled", !o.graylogTrace);
  dock.classList.remove("hidden");
}

async function apiFetch(url, init = {}, step = "") {
  const opts = { ...init };
  const base = headers(Boolean(opts.body), step);
  opts.headers = { ...base, ...(opts.headers || {}) };
  const res = await fetch(url, opts);
  recordObs(res, step);
  return res;
}

function money(v) {
  return Number(v || 0).toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
}

function toast(message, type = "") {
  const n = document.createElement("div");
  n.className = `toast ${type}`.trim();
  n.textContent = message;
  el.toastHost.appendChild(n);
  setTimeout(() => n.remove(), 3200);
}

function setBusy(on, text = "Processando…") {
  el.busy.classList.toggle("hidden", !on);
  el.busyText.textContent = text;
}

function isLoggedIn() {
  return Boolean(state.token && state.staffId);
}

function persistSession() {
  if (!state.token) {
    localStorage.removeItem(SESSION_KEY);
    ["lab_token", "lab_staff", "lab_name", "lab_store"].forEach((k) => localStorage.removeItem(k));
    return;
  }
  const payload = {
    token: state.token,
    staffId: state.staffId,
    name: state.name,
    storeId: state.storeId || "1001",
  };
  localStorage.setItem(SESSION_KEY, JSON.stringify(payload));
  localStorage.setItem("lab_token", payload.token);
  localStorage.setItem("lab_staff", payload.staffId);
  localStorage.setItem("lab_name", payload.name);
  localStorage.setItem("lab_store", payload.storeId);
}

function persistCart() {
  localStorage.setItem(CART_KEY, JSON.stringify(state.cart));
  renderCart();
  refreshChrome();
}

function setSession(data) {
  state.token = data.token || "";
  state.staffId = data.staffId || "";
  state.name = data.name || "";
  state.storeId = data.storeId || "1001";
  persistSession();
  refreshChrome();
}

function clearSession() {
  state.token = "";
  state.staffId = "";
  state.name = "";
  state.storeId = "1001";
  state.customer = null;
  state.customerValidated = false;
  persistSession();
  refreshChrome();
}

function cartQty() {
  return state.cart.reduce((s, i) => s + i.qty, 0);
}

function cartTotal() {
  return state.cart.reduce((s, i) => s + i.price * i.qty, 0);
}

function primaryLine() {
  return state.cart[0] || null;
}

function payType() {
  return document.querySelector('input[name="payType"]:checked')?.value || "avista";
}

function isFinanced(pt) {
  return pt === "cdc" || pt === "cdci";
}

function paymentReady() {
  const pt = payType();
  return !isFinanced(pt) || Boolean(state.selectedPlan);
}

function canReachAct(act) {
  if (act <= 1) return true;
  if (act === 2) return state.customerValidated;
  if (act === 3) return state.customerValidated && state.cart.length > 0;
  if (act === 4) return state.customerValidated && state.cart.length > 0 && paymentReady();
  return false;
}

function refreshChrome() {
  const logged = isLoggedIn();
  el.userChip.textContent = logged ? `${state.name} · ${state.staffId}` : "Faça login";
  el.storePill.textContent = logged ? `Loja ${state.storeId}` : "Loja —";
  el.btnLoginToggle.textContent = logged ? "Sair" : "Login";
  el.btnLoginToggle.classList.toggle("hidden", false);
  el.btnCreateCp.disabled = !logged;
  el.modNav.classList.toggle("hidden", !logged);
  el.btnOpenMonitor.classList.toggle("hidden", !logged);
  if (el.btnPreSale) {
    el.btnPreSale.disabled = !logged || !state.cart.length || !state.customerValidated || !paymentReady();
  }
}

function showView(name) {
  document.querySelectorAll(".view").forEach((v) => v.classList.add("hidden"));
  document.getElementById(`view-${name}`)?.classList.remove("hidden");
}

function setModActive(mod) {
  document.querySelectorAll("#modNav .mod[data-mod]").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.mod === mod);
  });
}

function showModule(mod) {
  if (!isLoggedIn() && mod !== "welcome" && mod !== "login") {
    return showView("login");
  }
  state.activeMod = mod === "sale" ? "sale" : mod;
  setModActive(mod === "sale" ? "sale" : mod);
  el.actRail.classList.toggle("hidden", mod !== "sale");
  if (mod === "sale") {
    showView("sale");
    goAct(state.act, { force: true });
    return;
  }
  showView(mod);
  if (mod === "products") searchModProducts();
  if (mod === "stock") renderStockShortcuts();
  if (mod === "monitor") loadProposals();
  if (mod === "cp") {
    updateCpSim();
    loadCp();
  }
}

function enterStudio() {
  showModule("sale");
  goAct(state.customerValidated ? (state.cart.length ? Math.max(state.act, 2) : 2) : 1, { force: true });
  searchProducts();
  refreshMonitorCount();
}

function goAct(act, { force = false } = {}) {
  const target = Math.min(4, Math.max(1, Number(act) || 1));
  if (!force && target > state.act && !canReachAct(target)) {
    if (!state.customerValidated) toast("Valide o cliente primeiro", "err");
    else if (!state.cart.length) toast("Adicione produtos à venda", "err");
    else if (!paymentReady()) toast("Escolha a parcela do financiamento", "err");
    return;
  }
  state.act = target;

  document.querySelectorAll(".act-panel").forEach((p) => {
    p.classList.toggle("hidden", Number(p.dataset.panel) !== state.act);
  });
  document.querySelectorAll("#actRail .act").forEach((btn) => {
    const a = Number(btn.dataset.act);
    btn.classList.toggle("active", a === state.act);
    btn.classList.toggle("done", a < state.act);
    btn.disabled = !canReachAct(a) && a > state.act;
  });

  document.getElementById("btnToAct2").disabled = !state.customerValidated;
  document.getElementById("btnToAct3").disabled = !state.cart.length;
  document.getElementById("btnToAct4").disabled = !paymentReady();
  if (el.btnPreSale) {
    el.btnPreSale.disabled = !isLoggedIn() || !state.cart.length || !state.customerValidated || !paymentReady();
  }

  if (state.act === 2) searchProducts();
  if (state.act === 3) {
    const financed = isFinanced(payType());
    if (!financed || !el.planList.children.length) syncPayUi();
    else renderPaySummary();
  }
  if (state.act === 4) renderAssSummary();
}

/* Customer */
async function validateCustomer() {
  const cpf = document.getElementById("cartCpf").value.trim();
  if (!cpf) return toast("Informe o CPF", "err");
  setBusy(true, "Consultando cliente…");
  try {
    const res = await apiFetch(
      `/Customer/api/Customer/FindByCpfCnpj?cpf=${encodeURIComponent(cpf)}`,
      { headers: headers(false, JOURNEY.validateCustomer) },
      JOURNEY.validateCustomer
    );
    const data = await res.json();
    if (!res.ok) {
      document.getElementById("customerHero").className = "customer-hero empty";
      document.getElementById("customerHero").innerHTML = `<p>Cliente não encontrado para ${cpf}</p>`;
      state.customerValidated = false;
      toast("Cliente não encontrado", "err");
      return;
    }
    state.customer = data;
    state.customerValidated = true;
    document.getElementById("cpCpf").value = data.cpf;
    document.getElementById("customerHero").className = "customer-hero";
    document.getElementById("customerHero").innerHTML = `
      <strong>${data.name}</strong>
      <span>CPF ${data.cpf} · Conta ${data.accountNum}</span>`;
    document.getElementById("limitStrip").classList.remove("hidden");
    await loadLimitStrip(state.limitType);
    document.getElementById("act1Hint").textContent = "Cliente validado — pode montar a venda";
    toast("Cliente na mesa", "ok");
    goAct(state.act, { force: true });
  } finally {
    setBusy(false);
  }
}

async function loadLimitStrip(productType) {
  const cpf = document.getElementById("cartCpf").value.trim();
  const strip = document.getElementById("limitStrip");
  const headline = document.getElementById("limitHeadline");
  const sub = document.getElementById("limitSub");
  state.limitType = productType;
  document.querySelectorAll("#limitTabs .chip").forEach((c) =>
    c.classList.toggle("active", c.dataset.limit === productType)
  );
  const res = await apiFetch(
    `/Customer/api/CustomerLimits?cpf=${encodeURIComponent(cpf)}&productType=${encodeURIComponent(productType)}`,
    { headers: headers(false, JOURNEY.creditLimit) },
    JOURNEY.creditLimit
  );
  const data = await res.json();
  state.limits = data;
  if (!res.ok || !data.isCreditApproved) {
    strip.classList.add("denied");
    headline.textContent = `${productType}: crédito indisponível agora`;
    sub.textContent = data.error || "Tente outro produto ou revise o cadastro.";
    return;
  }
  strip.classList.remove("denied");
  headline.textContent = `Pode financiar até ${money(data.availableLimit)} em ${productType}`;
  sub.textContent = `Já usado ${money(data.usedLimit)}${
    data.totalLimit != null ? ` de ${money(data.totalLimit)}` : ""
  } · ${(data.productTypes || []).join(", ")}`;
}

/* Products / cart */
async function searchProducts() {
  const q = document.getElementById("productQuery").value.trim();
  const res = await apiFetch(
    `/Products/api/Products/Search?q=${encodeURIComponent(q)}`,
    { headers: headers(false, JOURNEY.searchProduct) },
    JOURNEY.searchProduct
  );
  const data = await res.json();
  const products = data.products || [];
  document.getElementById("productCount").textContent = String(products.length);
  const grid = document.getElementById("productGrid");
  grid.innerHTML = "";
  for (const p of products) {
    const card = document.createElement("article");
    card.className = "product";
    card.innerHTML = `
      <div class="sku">${p.itemId}</div>
      <h3>${p.name}</h3>
      <div class="price">${money(p.price)}</div>
      <div class="stock">${p.stock} em estoque</div>
      <div class="actions">
        <button type="button" class="btn-primary">Adicionar</button>
      </div>`;
    card.querySelector("button").onclick = () => addToCart(p);
    grid.appendChild(card);
  }
}

function addToCart(product, qty = 1) {
  if (!isLoggedIn()) {
    toast("Faça login", "err");
    return showView("login");
  }
  const existing = state.cart.find((i) => i.itemId === product.itemId);
  if (existing) existing.qty += qty;
  else state.cart.push({ itemId: product.itemId, name: product.name, price: Number(product.price), qty });
  persistCart();
  toast(`${product.name} na venda`, "ok");
  goAct(state.act, { force: true });
}

function renderCart() {
  const box = el.cartLines;
  if (!state.cart.length) {
    box.className = "cart-lines empty";
    box.textContent = "Nenhum item ainda.";
  } else {
    box.className = "cart-lines";
    box.innerHTML = "";
    for (const line of state.cart) {
      const row = document.createElement("div");
      row.className = "cart-line";
      row.innerHTML = `
        <div class="cart-line-top">
          <div>
            <strong>${line.name}</strong>
            <div class="dim">${line.itemId} · ${money(line.price)}</div>
          </div>
          <span class="line-total">${money(line.price * line.qty)}</span>
        </div>
        <div class="qty">
          <button type="button" data-act="dec">−</button>
          <span>${line.qty}</span>
          <button type="button" data-act="inc">+</button>
          <button type="button" class="btn-ghost" data-act="rm">remover</button>
        </div>`;
      row.querySelector('[data-act="dec"]').onclick = () => {
        line.qty -= 1;
        if (line.qty <= 0) state.cart = state.cart.filter((i) => i.itemId !== line.itemId);
        persistCart();
        goAct(state.act, { force: true });
      };
      row.querySelector('[data-act="inc"]').onclick = () => {
        line.qty += 1;
        persistCart();
      };
      row.querySelector('[data-act="rm"]').onclick = () => {
        state.cart = state.cart.filter((i) => i.itemId !== line.itemId);
        persistCart();
        goAct(state.act, { force: true });
      };
      box.appendChild(row);
    }
  }
  el.cartTotal.textContent = money(cartTotal());
  const line = primaryLine();
  if (line) {
    document.getElementById("cartItem").value = line.itemId;
    document.getElementById("cartQty").value = String(line.qty);
  }
}

async function searchModProducts() {
  const q = document.getElementById("modProductQuery").value.trim();
  const res = await apiFetch(`/Products/api/Products/Search?q=${encodeURIComponent(q)}`, { headers: headers(false) });
  const data = await res.json();
  const grid = document.getElementById("modProductGrid");
  grid.innerHTML = "";
  for (const p of data.products || []) {
    const card = document.createElement("article");
    card.className = "product";
    card.innerHTML = `
      <div class="sku">${p.itemId}</div>
      <h3>${p.name}</h3>
      <div class="price">${money(p.price)}</div>
      <div class="stock">${p.stock} em estoque</div>
      <div class="actions">
        <button type="button" class="btn-primary">Add à venda</button>
      </div>`;
    card.querySelector("button").onclick = () => {
      addToCart(p);
      showModule("sale");
      goAct(2, { force: true });
    };
    grid.appendChild(card);
  }
}

async function loadModCustomer() {
  const cpf = document.getElementById("modCpf").value.trim();
  if (!cpf) return toast("Informe o CPF", "err");
  setBusy(true, "Consultando cliente…");
  try {
    const res = await apiFetch(`/Customer/api/Customer/FindByCpfCnpj?cpf=${encodeURIComponent(cpf)}`, {
      headers: headers(false),
    });
    const data = await res.json();
    const card = document.getElementById("modCustomerCard");
    if (!res.ok) {
      card.innerHTML = `<strong>Não encontrado</strong><div>${cpf}</div>`;
      document.getElementById("btnModCustomerToSale").disabled = true;
      return toast("Cliente não encontrado", "err");
    }
    state._modCustomer = data;
    card.innerHTML = `<strong>${data.name}</strong><div>CPF ${data.cpf}</div><div>Conta ${data.accountNum}</div>`;
    document.getElementById("btnModCustomerToSale").disabled = false;
    await loadModLimits(state.limitType || "CDC");
  } finally {
    setBusy(false);
  }
}

async function loadModLimits(productType) {
  const cpf = document.getElementById("modCpf").value.trim();
  document.querySelectorAll("#modLimitTabs .chip").forEach((c) =>
    c.classList.toggle("active", c.dataset.limit === productType)
  );
  const res = await apiFetch(
    `/Customer/api/CustomerLimits?cpf=${encodeURIComponent(cpf)}&productType=${encodeURIComponent(productType)}`,
    { headers: headers(false) }
  );
  const data = await res.json();
  const card = document.getElementById("modLimitsCard");
  if (!res.ok || !data.isCreditApproved) {
    card.innerHTML = `<strong>${productType}: indisponível</strong><div>${data.error || "Sem crédito"}</div>`;
    return;
  }
  card.innerHTML = `<strong>Disponível ${money(data.availableLimit)}</strong>
    <div>Usado ${money(data.usedLimit)}${
      data.totalLimit != null ? ` · teto ${money(data.totalLimit)}` : ""
    } · ${(data.productTypes || []).join(", ")}</div>`;
  document.getElementById("btnResetLimit").disabled = false;
}

/* Payment */
function financeiraLabel(pt) {
  if (pt === "cdc") return "Crediare";
  if (pt === "cdci") return "Financeira 25";
  return "—";
}

function renderPlans(plans) {
  state.plans = plans || [];
  el.planList.innerHTML = "";
  state.selectedPlan = null;
  if (!state.plans.length) {
    el.plansHint.textContent = "Nenhuma parcela disponível para este valor";
    return;
  }
  el.plansHint.textContent = "Escolha a parcela que cabe";
  for (const p of state.plans) {
    const card = document.createElement("button");
    card.type = "button";
    card.className = "plan-card";
    card.innerHTML = `
      <strong>${p.numOfPayment}x de ${money(p.installmentValue)}</strong>
      <div class="band-meta">${p.name.replace(/^CDC |^CDCI /, "")} · total ${money(p.totalValue)} · ${
      p.financeira === "Financeira 12" ? "Crediare" : p.financeira
    }</div>`;
    card.onclick = () => {
      state.selectedPlan = p;
      el.planList.querySelectorAll(".plan-card").forEach((c) => c.classList.remove("selected"));
      card.classList.add("selected");
      renderPaySummary();
      document.getElementById("btnToAct4").disabled = !paymentReady();
      if (el.btnPreSale) {
        el.btnPreSale.disabled = !isLoggedIn() || !state.cart.length || !state.customerValidated || !paymentReady();
      }
    };
    el.planList.appendChild(card);
  }
  const first = el.planList.querySelector(".plan-card");
  if (first) {
    first.classList.add("selected");
    state.selectedPlan = state.plans[0];
    renderPaySummary();
    document.getElementById("btnToAct4").disabled = !paymentReady();
  }
}

function renderPaySummary() {
  const box = document.getElementById("paySummary");
  const pt = payType();
  const labels = { avista: "À vista", cartao: "Cartão", pix: "PIX", cdc: "CDC · Crediare", cdci: "CDCI · Financeira 25" };
  let extra = "";
  if (isFinanced(pt) && state.selectedPlan) {
    extra = `<div>Parcela escolhida: <strong>${state.selectedPlan.numOfPayment}x de ${money(
      state.selectedPlan.installmentValue
    )}</strong></div>`;
  }
  box.innerHTML = `<div>Condição: <strong>${labels[pt]}</strong></div>
    <div>Total da venda: <strong>${money(cartTotal())}</strong></div>${extra}`;
}

async function syncPayUi() {
  const pt = payType();
  el.financedBox.classList.toggle("hidden", !isFinanced(pt));
  if (!isFinanced(pt)) {
    state.selectedPlan = null;
    el.planList.innerHTML = "";
    renderPaySummary();
    goAct(state.act, { force: true });
    return;
  }
  el.plansHint.textContent = "Buscando parcelas…";
  const storeId = state.storeId || "1001";
  const amount = cartTotal() || 100;
  await apiFetch(
    `/PaymentCondition/api/FinancialConditions/Find/${storeId}`,
    {
      method: "POST",
      headers: headers(true, JOURNEY.listPlans),
      body: JSON.stringify({ productType: pt }),
    },
    JOURNEY.listPlans
  );
  const limRes = await apiFetch(
    `/Customer/api/CustomerLimits?cpf=${encodeURIComponent(
      document.getElementById("cartCpf").value.trim()
    )}&productType=${encodeURIComponent(pt.toUpperCase())}`,
    { headers: headers(false, JOURNEY.creditLimit) },
    JOURNEY.creditLimit
  );
  const lim = await limRes.json();
  const mini = document.getElementById("cartLimitsMini");
  if (limRes.ok && lim.isCreditApproved) {
    mini.textContent = `Crédito ok · disponível ${money(lim.availableLimit)} em ${financeiraLabel(pt)}`;
  } else {
    mini.textContent = "Atenção: limite pode bloquear o fechamento desta condição.";
  }
  const res = await apiFetch(
    "/InstallmentSimulator/api/InstallmentSimulator/BatchSimulate",
    {
      method: "POST",
      headers: headers(true, JOURNEY.simulatePlan),
      body: JSON.stringify({
        productType: pt,
        amount,
        minInstallments: pt === "cdci" ? 20 : 12,
      }),
    },
    JOURNEY.simulatePlan
  );
  const data = await res.json();
  if (!res.ok) {
    el.plansHint.textContent = data.message || data.error || "Falha nos planos";
    renderPlans([]);
    return;
  }
  renderPlans(data.successful || []);
}

function renderAssSummary() {
  const pt = payType();
  const labels = { avista: "À vista", cartao: "Cartão", pix: "PIX", cdc: "CDC · Crediare", cdci: "CDCI · Financeira 25" };
  const cust = state.customer ? `${state.customer.name} (${state.customer.cpf})` : document.getElementById("cartCpf").value;
  const items = state.cart.map((i) => `${i.qty}× ${i.name}`).join(", ");
  let plan = "—";
  if (isFinanced(pt) && state.selectedPlan) {
    plan = `${state.selectedPlan.numOfPayment}x de ${money(state.selectedPlan.installmentValue)}`;
  }
  document.getElementById("assSummary").innerHTML = `
    <div>Cliente: <strong>${cust}</strong></div>
    <div>Itens: <strong>${items || "—"}</strong></div>
    <div>Pagamento: <strong>${labels[pt]}</strong></div>
    <div>Parcelas: <strong>${plan}</strong></div>
    <div>Total: <strong>${money(cartTotal())}</strong></div>`;
}

async function createPreSale() {
  if (!isLoggedIn()) return toast("Faça login", "err");
  if (!state.cart.length) return toast("Carrinho vazio", "err");
  const pt = payType();
  const line = primaryLine();
  const feedback = document.getElementById("saleResult");
  feedback.className = "sale-feedback";
  feedback.textContent = "";
  if (isFinanced(pt) && !state.selectedPlan) return toast("Escolha a parcela", "err");

  setBusy(true, "Gerando pré-venda…");
  try {
    const body = {
      staffId: state.staffId,
      cpf: document.getElementById("cartCpf").value.trim(),
      itemId: line.itemId,
      qty: line.qty,
      paymentType: pt,
    };
    if (isFinanced(pt)) {
      body.installments = state.selectedPlan.numOfPayment;
      body.tenderTypeId = state.selectedPlan.tenderTypeId;
    }
    const res = await apiFetch(
      "/SalesOrder/api/CreatePreSales",
      {
        method: "POST",
        headers: headers(true, JOURNEY.createPresale),
        body: JSON.stringify(body),
      },
      JOURNEY.createPresale
    );
    const data = await res.json();
    if (!res.ok) {
      feedback.className = "sale-feedback err";
      feedback.textContent = data.message || data.error || "Falha na pré-venda";
      toast(feedback.textContent, "err");
      return;
    }
    state.lastSale = data;
    feedback.className = "sale-feedback ok";
    feedback.textContent = `${data.saleId} criada`;
    toast("Pré-venda pronta. Pode chamar a integração.", "ok");
    openSuccessSheet(data);
    refreshMonitorCount();
  } finally {
    setBusy(false);
  }
}

/* Sheets */
function openSheet(id) {
  closeSheets();
  document.getElementById("sheetBackdrop").classList.remove("hidden");
  document.getElementById(id).classList.remove("hidden");
}

function closeSheets() {
  document.getElementById("sheetBackdrop").classList.add("hidden");
  document.querySelectorAll(".sheet").forEach((s) => s.classList.add("hidden"));
}

function openSuccessSheet(data) {
  document.getElementById("successTitle").textContent = "Pré-venda pronta";
  document.getElementById("successLead").textContent = `${data.saleId} · ${money(data.amount)} · ${String(
    data.paymentType
  ).toUpperCase()}${data.proposalId ? ` · ${data.proposalId}` : ""}`;
  const integrate = document.getElementById("btnIntegrateFromSuccess");
  integrate.classList.toggle("hidden", !data.proposalId);
  integrate.onclick = async () => {
    if (!data.proposalId) return;
    setBusy(true, "Enviando à financeira…");
    try {
      await new Promise((r) => setTimeout(r, 500));
      const r = await apiFetch(
        "/MultiFinancial/api/IntegrateProposal",
        {
          method: "POST",
          headers: headers(true, JOURNEY.integrateProposal),
          body: JSON.stringify({ proposalId: data.proposalId }),
        },
        JOURNEY.integrateProposal
      );
      const d = await r.json();
      if (!r.ok) toast(d.error || "Falha", "err");
      else {
        toast(d.statusLabel || "Integrada na financeira!", "ok");
        closeSheets();
        showModule("monitor");
      }
    } finally {
      setBusy(false);
    }
  };
  openSheet("sheetSuccess");
}

function proposalCardHtml(p, kind = "cdc") {
  const statusClass = p.status === "integrated" ? "integrated" : "pending";
  const statusLabel =
    p.status === "integrated" ? "Integrada" : p.status === "pending_integration" ? "Aguardando financeira" : p.status;
  return `
    <header>
      <div>
        <strong>${p.proposalId}</strong>
        <div class="dim">${p.customerName || ""} · ${p.cpf}</div>
      </div>
      <div>
        <span class="badge ${kind}">${p.productType || kind.toUpperCase()}</span>
        <span class="badge ${statusClass}">${statusLabel}</span>
      </div>
    </header>
    <div class="proposal-meta">
      <div><span>ASS</span><strong>${p.saleId || "—"}</strong></div>
      <div><span>Parcelas</span><strong>${p.installments}x</strong></div>
      <div><span>Valor</span><strong>${money(p.amount)}</strong></div>
      <div><span>Financeira</span><strong>${
        p.financeira === "Financeira 12" ? "Crediare" : p.financeira || "—"
      }</strong></div>
    </div>`;
}

async function refreshMonitorCount() {
  try {
    const res = await apiFetch(
      "/MultiFinancial/api/Proposals",
      { headers: headers(false, JOURNEY.listProposals) },
      JOURNEY.listProposals
    );
    state.pendingCount = pending;
    el.monitorCount.textContent = String(pending);
  } catch (_) {}
}

async function loadProposals() {
  const empty = document.getElementById("proposalsEmpty");
  const grid = document.getElementById("proposalsGrid");
  grid.innerHTML = "";
  const res = await apiFetch(
    "/MultiFinancial/api/Proposals",
    { headers: headers(false, JOURNEY.listProposals) },
    JOURNEY.listProposals
  );
  const data = await res.json();
  let list = data.proposals || [];
  if (state.monitorFilter !== "all") list = list.filter((p) => p.status === state.monitorFilter);
  empty.classList.toggle("hidden", list.length > 0);
  for (const p of list) {
    const card = document.createElement("article");
    card.className = "proposal-card";
    card.innerHTML = proposalCardHtml(p, String(p.productType || "cdc").toLowerCase());
    const actions = document.createElement("div");
    actions.className = "proposal-actions";
    const can = p.status === "pending_integration";
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = can ? "btn-primary" : "btn-secondary";
    btn.textContent = can ? "Integrar financeira" : "Já integrada";
    btn.disabled = !can;
    btn.onclick = async () => {
      setBusy(true, "Enviando à financeira…");
      try {
        await new Promise((r) => setTimeout(r, 500));
        const r = await apiFetch(
          "/MultiFinancial/api/IntegrateProposal",
          {
            method: "POST",
            headers: headers(true, JOURNEY.integrateProposal),
            body: JSON.stringify({ proposalId: p.proposalId }),
          },
          JOURNEY.integrateProposal
        );
        const d = await r.json();
        if (!r.ok) toast(d.error || "Falha", "err");
        else {
          toast(d.statusLabel || "Integrada!", "ok");
          loadProposals();
          refreshMonitorCount();
        }
      } finally {
        setBusy(false);
      }
    };
    actions.appendChild(btn);
    card.appendChild(actions);
    grid.appendChild(card);
  }
  refreshMonitorCount();
}

function updateCpSim() {
  const amount = Number(document.getElementById("cpAmount").value || 0);
  const n = Number(document.getElementById("cpInstallments").value || 12);
  const parcela = n ? (amount * 1.025) / n : 0;
  document.getElementById("cpSim").textContent = `Parcela estimada: ${money(parcela)}`;
}

async function loadCp() {
  const grid = document.getElementById("cpGrid");
  grid.innerHTML = "";
  const res = await apiFetch("/PersonalCredit/api/Proposals", { headers: headers(false) });
  const data = await res.json();
  const list = data.proposals || [];
  if (!list.length) {
    grid.innerHTML = `<div class="empty-state">Nenhuma proposta CP.</div>`;
    return;
  }
  for (const p of list) {
    const card = document.createElement("article");
    card.className = "proposal-card";
    card.innerHTML = proposalCardHtml({ ...p, saleId: "—" }, "cp");
    grid.appendChild(card);
  }
}

async function consultStock() {
  const itemId = document.getElementById("stockItem").value.trim();
  const card = document.getElementById("stockCard");
  const res = await apiFetch(`/Stock/api/Stock/Find?itemId=${encodeURIComponent(itemId)}`, { headers: headers(false) });
  const data = await res.json();
  if (!res.ok) {
    card.innerHTML = `<strong>Não encontrado</strong>`;
    return;
  }
  card.innerHTML = `<strong>${data.name}</strong><div>${data.itemId}</div><div>${money(data.price)}</div><div>${
    data.physicalStock
  } em estoque</div>`;
  card.dataset.item = JSON.stringify({
    itemId: data.itemId,
    name: data.name,
    price: data.price,
  });
}

async function renderStockShortcuts() {
  const box = document.getElementById("stockShortcuts");
  const res = await apiFetch("/Products/api/Products/Search?q=", { headers: headers(false) });
  const data = await res.json();
  box.innerHTML = "";
  for (const p of (data.products || []).slice(0, 8)) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "chip";
    b.textContent = p.itemId;
    b.onclick = () => {
      document.getElementById("stockItem").value = p.itemId;
      consultStock();
    };
    box.appendChild(b);
  }
}

/* Events */
document.getElementById("btnWelcomeLogin").onclick = () => showView("login");
document.getElementById("btnLogin").onclick = async () => {
  setBusy(true, "Autenticando…");
  try {
    const res = await apiFetch(
      "/UserAuthentication/api/Authorize",
      {
        method: "POST",
        headers: headers(true, JOURNEY.login),
        body: JSON.stringify({
          staffId: document.getElementById("staffId").value.trim(),
          password: document.getElementById("password").value,
        }),
      },
      JOURNEY.login
    );
    const data = await res.json();
    if (!res.ok) return toast(data.message || "Falha no login", "err");
    setSession(data);
    toast(`Bem-vindo(a), ${data.name.split(" ")[0]}`, "ok");
    enterStudio();
  } finally {
    setBusy(false);
  }
};

el.btnLoginToggle.onclick = () => {
  if (isLoggedIn()) {
    clearSession();
    el.actRail.classList.add("hidden");
    el.modNav.classList.add("hidden");
    el.btnOpenMonitor.classList.add("hidden");
    showView("welcome");
    toast("Sessão encerrada");
  } else showView("login");
};

document.querySelectorAll("#modNav .mod[data-mod]").forEach((btn) =>
  btn.addEventListener("click", () => showModule(btn.dataset.mod))
);

document.getElementById("btnValidateCustomer").onclick = validateCustomer;
document.querySelectorAll("#limitTabs .chip").forEach((c) =>
  c.addEventListener("click", () => loadLimitStrip(c.dataset.limit))
);
document.getElementById("btnToAct2").onclick = () => goAct(2);
document.getElementById("btnToAct3").onclick = () => goAct(3);
document.getElementById("btnToAct4").onclick = () => goAct(4);
document.getElementById("btnBackAct1").onclick = () => goAct(1, { force: true });
document.getElementById("btnBackAct2").onclick = () => goAct(2, { force: true });
document.getElementById("btnBackAct3").onclick = () => goAct(3, { force: true });
document.querySelectorAll("#actRail .act").forEach((btn) =>
  btn.addEventListener("click", () => goAct(btn.dataset.act))
);

document.getElementById("btnSearch").onclick = searchProducts;
document.getElementById("productQuery").addEventListener("keydown", (e) => {
  if (e.key === "Enter") searchProducts();
});
document.getElementById("btnClearCart").onclick = () => {
  state.cart = [];
  persistCart();
  goAct(state.act, { force: true });
};

document.querySelectorAll('input[name="payType"]').forEach((r) => r.addEventListener("change", () => syncPayUi()));
document.getElementById("btnPlans").onclick = () => syncPayUi();
document.getElementById("btnPreSale").onclick = createPreSale;

document.getElementById("sheetBackdrop").onclick = closeSheets;
document.getElementById("btnCloseSuccess").onclick = closeSheets;
document.getElementById("btnSuccessToMonitor").onclick = () => {
  closeSheets();
  showModule("monitor");
};
document.getElementById("btnOpenMonitor").onclick = () => showModule("monitor");
document.getElementById("btnRefreshProposals").onclick = loadProposals;
document.getElementById("monitorFilter").onchange = (e) => {
  state.monitorFilter = e.target.value;
  loadProposals();
};

document.getElementById("btnModSearch").onclick = searchModProducts;
document.getElementById("modProductQuery").addEventListener("keydown", (e) => {
  if (e.key === "Enter") searchModProducts();
});
document.getElementById("btnStock").onclick = consultStock;
document.getElementById("btnStockToCart").onclick = () => {
  const raw = document.getElementById("stockCard").dataset.item;
  if (!raw) return toast("Consulte um item", "err");
  addToCart(JSON.parse(raw));
  showModule("sale");
  goAct(2, { force: true });
};
document.getElementById("btnModCustomer").onclick = loadModCustomer;
document.getElementById("btnResetLimit").onclick = async () => {
  await resetCreditLimit(document.getElementById("modCpf").value.trim());
};
document.getElementById("btnResetLimitSale").onclick = async () => {
  await resetCreditLimit(document.getElementById("cartCpf").value.trim());
};

async function resetCreditLimit(cpf) {
  cpf = (cpf || "").trim();
  if (!cpf) return toast("Informe o CPF", "err");
  setBusy(true, "Zerando limite usado…");
  try {
    const res = await apiFetch("/Customer/api/ResetCreditLimit", {
      method: "POST",
      headers: headers(),
      body: JSON.stringify({ cpf }),
    });
    const data = await res.json();
    if (!res.ok) return toast(data.message || data.error || "Falha ao zerar", "err");
    toast(`Limite liberado · disponível ${money(data.availableLimit)}`, "ok");
    if (document.getElementById("modCpf").value.trim() === cpf) {
      await loadModLimits(state.limitType || "CDC");
    }
    if (document.getElementById("cartCpf").value.trim() === cpf) {
      await loadLimitStrip(state.limitType || "CDC");
    }
  } finally {
    setBusy(false);
  }
}
document.querySelectorAll("#modLimitTabs .chip").forEach((c) =>
  c.addEventListener("click", () => loadModLimits(c.dataset.limit))
);
document.getElementById("btnModCustomerToSale").onclick = () => {
  const c = state._modCustomer;
  if (!c) return;
  document.getElementById("cartCpf").value = c.cpf;
  showModule("sale");
  validateCustomer();
};

document.getElementById("cpAmount").oninput = updateCpSim;
document.getElementById("cpInstallments").onchange = updateCpSim;
document.getElementById("btnCreateCp").onclick = async () => {
  setBusy(true, "Criando CP…");
  try {
    const res = await apiFetch(
      "/PersonalCredit/api/Create",
      {
        method: "POST",
        headers: headers(true, JOURNEY.createCp),
        body: JSON.stringify({
          staffId: state.staffId,
          cpf: document.getElementById("cpCpf").value.trim(),
          amount: Number(document.getElementById("cpAmount").value),
          installments: Number(document.getElementById("cpInstallments").value || 12),
        }),
      },
      JOURNEY.createCp
    );
    const data = await res.json();
    const box = document.getElementById("cpCreateResult");
    if (!res.ok) {
      box.className = "sale-feedback err";
      box.textContent = data.message || data.error || "Falha CP";
      return toast(box.textContent, "err");
    }
    box.className = "sale-feedback ok";
    box.textContent = `${data.proposalId} · ${money(data.amount)}`;
    toast("Proposta CP criada", "ok");
    loadCp();
  } finally {
    setBusy(false);
  }
};

/* boot */
rewriteStaticObsLinks();
refreshChrome();
renderCart();
if (isLoggedIn()) enterStudio();
else showView("welcome");
