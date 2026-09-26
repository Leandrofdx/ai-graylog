const state = {
  token: localStorage.getItem("lab_token") || "",
  staffId: localStorage.getItem("lab_staff") || "",
  name: localStorage.getItem("lab_name") || "",
  storeId: localStorage.getItem("lab_store") || "1001",
  selectedPlan: null,
};

const drawer = document.getElementById("drawer");
const backdrop = document.getElementById("backdrop");
const userChip = document.getElementById("userChip");
const drawerName = document.getElementById("drawerName");
const drawerSub = document.getElementById("drawerSub");
const btnPreSale = document.getElementById("btnPreSale");
const btnCreateCp = document.getElementById("btnCreateCp");
const financedBox = document.getElementById("financedBox");
const planSelect = document.getElementById("planSelect");

function headers(json = true) {
  const h = {
    "X-Request-Id": crypto.randomUUID(),
    "X-Test-Run-Id": window.__TEST_RUN_ID || `ui-${Date.now()}`,
  };
  if (json) h["Content-Type"] = "application/json";
  if (state.staffId) h["X-Staff-Id"] = state.staffId;
  if (state.token) h["Authorization"] = `Bearer ${state.token}`;
  return h;
}

function setSession(data) {
  state.token = data.token || "";
  state.staffId = data.staffId || "";
  state.name = data.name || "";
  state.storeId = data.storeId || "1001";
  localStorage.setItem("lab_token", state.token);
  localStorage.setItem("lab_staff", state.staffId);
  localStorage.setItem("lab_name", state.name);
  localStorage.setItem("lab_store", state.storeId);
  refreshChrome();
}

function clearSession() {
  setSession({});
}

function refreshChrome() {
  const logged = Boolean(state.token);
  userChip.textContent = logged ? `${state.name} · loja ${state.storeId}` : "Faça login";
  drawerName.textContent = logged ? state.name : "Assistente Colombo";
  drawerSub.textContent = logged ? "Vendedor" : "Realize o login";
  document.getElementById("btnLoginToggle").textContent = logged ? "Sair" : "Login";
  btnPreSale.disabled = !logged;
  btnCreateCp.disabled = !logged;
}

function payType() {
  const el = document.querySelector('input[name="payType"]:checked');
  return el ? el.value : "avista";
}

function isFinanced(pt) {
  return pt === "cdc" || pt === "cdci";
}

function syncPayUi() {
  const pt = payType();
  financedBox.classList.toggle("hidden", !isFinanced(pt));
  if (!isFinanced(pt)) {
    state.selectedPlan = null;
    planSelect.innerHTML = '<option value="">Selecione um plano…</option>';
  }
}

function showView(name) {
  document.querySelectorAll(".view").forEach((v) => v.classList.add("hidden"));
  document.getElementById(`view-${name}`).classList.remove("hidden");
  document.querySelectorAll(".nav").forEach((n) => n.classList.toggle("active", n.dataset.view === name));
  closeMenu();
  if (name === "monitor") loadProposals();
  if (name === "cp") loadCp();
}

function openMenu() {
  drawer.classList.add("open");
  backdrop.classList.add("show");
}
function closeMenu() {
  drawer.classList.remove("open");
  backdrop.classList.remove("show");
}

document.getElementById("btnMenu").addEventListener("click", openMenu);
backdrop.addEventListener("click", closeMenu);
document.querySelectorAll(".nav").forEach((btn) =>
  btn.addEventListener("click", () => showView(btn.dataset.view))
);
document.querySelectorAll('input[name="payType"]').forEach((r) => r.addEventListener("change", syncPayUi));

document.getElementById("btnWelcomeLogin").addEventListener("click", () => showView("login"));
document.getElementById("btnLoginToggle").addEventListener("click", () => {
  if (state.token) {
    clearSession();
    showView("home");
  } else {
    showView("login");
  }
});

document.getElementById("btnLogin").addEventListener("click", async () => {
  const staffId = document.getElementById("staffId").value.trim();
  const password = document.getElementById("password").value;
  const res = await fetch("/UserAuthentication/api/Authorize", {
    method: "POST",
    headers: headers(),
    body: JSON.stringify({ staffId, password }),
  });
  const data = await res.json();
  if (!res.ok) {
    alert(data.message || data.error || "Falha no login");
    return;
  }
  setSession(data);
  showView("products");
  searchProducts();
});

async function searchProducts() {
  const q = document.getElementById("productQuery").value.trim();
  const res = await fetch(`/Products/api/Products/Search?q=${encodeURIComponent(q)}`, { headers: headers(false) });
  const data = await res.json();
  const grid = document.getElementById("productGrid");
  grid.innerHTML = "";
  for (const p of data.products || []) {
    const card = document.createElement("article");
    card.className = "product";
    card.innerHTML = `
      <h3>${p.name}</h3>
      <div class="price">R$ ${Number(p.price).toFixed(2)}</div>
      <div class="stock">Estoque: ${p.stock} · ${p.itemId}</div>
      <button type="button" class="btn-primary">Add carrinho</button>`;
    card.querySelector("button").addEventListener("click", () => {
      document.getElementById("cartItem").value = p.itemId;
      showView("cart");
    });
    grid.appendChild(card);
  }
}
document.getElementById("btnSearch").addEventListener("click", searchProducts);

document.getElementById("btnStock").addEventListener("click", async () => {
  const itemId = document.getElementById("stockItem").value.trim();
  const res = await fetch(`/Stock/api/Stock/Find?itemId=${encodeURIComponent(itemId)}`, { headers: headers(false) });
  const data = await res.json();
  document.getElementById("stockResult").textContent = JSON.stringify(data, null, 2);
});

document.getElementById("btnCustomer").addEventListener("click", async () => {
  const cpf = document.getElementById("cpf").value.trim();
  const res = await fetch(`/Customer/api/Customer/FindByCpfCnpj?cpf=${encodeURIComponent(cpf)}`, {
    headers: headers(false),
  });
  const data = await res.json();
  document.getElementById("customerResult").textContent = JSON.stringify(data, null, 2);
  if (res.ok) {
    document.getElementById("cartCpf").value = data.cpf;
    document.getElementById("cpCpf").value = data.cpf;
  }
});

document.getElementById("btnLimits").addEventListener("click", async () => {
  const cpf = document.getElementById("cartCpf").value.trim();
  const pt = payType().toUpperCase();
  const res = await fetch(
    `/Customer/api/CustomerLimits?cpf=${encodeURIComponent(cpf)}&productType=${encodeURIComponent(pt)}`,
    { headers: headers(false) }
  );
  const data = await res.json();
  document.getElementById("limitsResult").textContent = JSON.stringify(data, null, 2);
});

document.getElementById("btnPlans").addEventListener("click", async () => {
  const pt = payType();
  const storeId = state.storeId || "1001";
  const itemId = document.getElementById("cartItem").value.trim();
  const stockRes = await fetch(`/Stock/api/Stock/Find?itemId=${encodeURIComponent(itemId)}`, {
    headers: headers(false),
  });
  const stock = await stockRes.json();
  const qty = Number(document.getElementById("cartQty").value || 1);
  const amount = stockRes.ok ? Number(stock.price) * qty : 0;

  await fetch(`/PaymentCondition/api/FinancialConditions/Find/${storeId}`, {
    method: "POST",
    headers: headers(),
    body: JSON.stringify({ productType: pt, paymentType: pt }),
  });

  const res = await fetch("/InstallmentSimulator/api/InstallmentSimulator/BatchSimulate", {
    method: "POST",
    headers: headers(),
    body: JSON.stringify({
      productType: pt,
      amount,
      minInstallments: pt === "cdci" ? 20 : 12,
    }),
  });
  const data = await res.json();
  planSelect.innerHTML = '<option value="">Selecione um plano…</option>';
  state.selectedPlan = null;
  for (const p of data.successful || []) {
    const opt = document.createElement("option");
    opt.value = String(p.planId);
    opt.textContent = `${p.name} · ${p.numOfPayment}x R$ ${Number(p.installmentValue).toFixed(2)} · ${p.financeira}`;
    opt.dataset.plan = JSON.stringify(p);
    planSelect.appendChild(opt);
  }
  if (!res.ok) {
    document.getElementById("limitsResult").textContent = JSON.stringify(data, null, 2);
  }
});

planSelect.addEventListener("change", () => {
  const opt = planSelect.selectedOptions[0];
  state.selectedPlan = opt && opt.dataset.plan ? JSON.parse(opt.dataset.plan) : null;
});

document.getElementById("btnPreSale").addEventListener("click", async () => {
  const pt = payType();
  const body = {
    staffId: state.staffId,
    cpf: document.getElementById("cartCpf").value.trim(),
    itemId: document.getElementById("cartItem").value.trim(),
    qty: Number(document.getElementById("cartQty").value || 1),
    paymentType: pt,
  };
  if (isFinanced(pt)) {
    if (!state.selectedPlan) {
      alert("Selecione um plano CDC/CDCI (Ver mais planos).");
      return;
    }
    body.installments = state.selectedPlan.numOfPayment;
    body.tenderTypeId = state.selectedPlan.tenderTypeId;
    body.numOfPayment = state.selectedPlan.numOfPayment;
  }
  const res = await fetch("/SalesOrder/api/CreatePreSales", {
    method: "POST",
    headers: headers(),
    body: JSON.stringify(body),
  });
  const data = await res.json();
  document.getElementById("saleResult").textContent = JSON.stringify(data, null, 2);
  if (res.ok && data.proposalId) {
    // opcional: ir ao monitor
  }
});

async function loadProposals() {
  const res = await fetch("/MultiFinancial/api/Proposals", { headers: headers(false) });
  const data = await res.json();
  const tbody = document.querySelector("#proposalsTable tbody");
  tbody.innerHTML = "";
  for (const p of data.proposals || []) {
    const tr = document.createElement("tr");
    const canIntegrate = p.status === "pending_integration";
    tr.innerHTML = `
      <td>${p.proposalId}</td>
      <td>${p.saleId || "—"}</td>
      <td>${p.customerName}<br/><small>${p.cpf}</small></td>
      <td><span class="badge ${String(p.productType).toLowerCase()}">${p.productType}</span></td>
      <td>${p.installments}x</td>
      <td>R$ ${Number(p.amount).toFixed(2)}</td>
      <td>${p.status}</td>
      <td></td>`;
    const td = tr.lastElementChild;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn-mini";
    btn.textContent = "Integrar financeira";
    btn.disabled = !canIntegrate;
    btn.addEventListener("click", async () => {
      const r = await fetch("/MultiFinancial/api/IntegrateProposal", {
        method: "POST",
        headers: headers(),
        body: JSON.stringify({ proposalId: p.proposalId }),
      });
      const d = await r.json();
      alert(d.statusLabel || d.error || "ok");
      loadProposals();
    });
    td.appendChild(btn);
    tbody.appendChild(tr);
  }
}
document.getElementById("btnRefreshProposals").addEventListener("click", loadProposals);

async function loadCp() {
  const res = await fetch("/PersonalCredit/api/Proposals", { headers: headers(false) });
  const data = await res.json();
  const tbody = document.querySelector("#cpTable tbody");
  tbody.innerHTML = "";
  for (const p of data.proposals || []) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${p.proposalId}</td>
      <td>${p.customerName}<br/><small>${p.cpf}</small></td>
      <td>R$ ${Number(p.amount).toFixed(2)}</td>
      <td>${p.installments}x</td>
      <td>${p.status}</td>
      <td>${p.financeira}</td>`;
    tbody.appendChild(tr);
  }
}
document.getElementById("btnRefreshCp").addEventListener("click", loadCp);

document.getElementById("btnCreateCp").addEventListener("click", async () => {
  const body = {
    staffId: state.staffId,
    cpf: document.getElementById("cpCpf").value.trim(),
    amount: Number(document.getElementById("cpAmount").value),
    installments: Number(document.getElementById("cpInstallments").value || 12),
  };
  const res = await fetch("/PersonalCredit/api/Create", {
    method: "POST",
    headers: headers(),
    body: JSON.stringify(body),
  });
  const data = await res.json();
  document.getElementById("cpCreateResult").textContent = JSON.stringify(data, null, 2);
  if (res.ok) loadCp();
});

const chatBox = document.getElementById("chatBox");
function addChat(direction, text) {
  const b = document.createElement("div");
  b.className = `bubble ${direction}`;
  b.textContent = text;
  chatBox.appendChild(b);
  chatBox.scrollTop = chatBox.scrollHeight;
}
document.getElementById("chatForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = document.getElementById("chatInput");
  const message = input.value.trim();
  if (!message) return;
  addChat("outgoing", message);
  input.value = "";
  const res = await fetch("/chat/api/chat", {
    method: "POST",
    headers: headers(),
    body: JSON.stringify({ message }),
  });
  const data = await res.json();
  addChat("incoming", data.message || data.error || "Sem resposta");
});

refreshChrome();
syncPayUi();
showView("home");
