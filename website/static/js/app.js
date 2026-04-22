const state = {
  selectedLeadId: null,
  leads: [],
  senderPlan: null,
  notificationMode: "unread",
};
window.AppState = state;

function byId(id) {
  return document.getElementById(id);
}

async function api(url, options = {}) {
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const message = data.error || `Request failed (${res.status})`;
    throw new Error(message);
  }
  return data;
}

function showStatus(elId, text, isError = false) {
  const el = byId(elId);
  if (!el) return;
  el.textContent = text;
  el.className = isError ? "small text-danger" : "small text-muted";
}

function sectionSwitch(targetId) {
  document.querySelectorAll(".app-section").forEach((section) => {
    section.classList.add("d-none");
  });
  byId(targetId).classList.remove("d-none");

  document.querySelectorAll(".nav-section").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.target === targetId);
  });
}

async function loadLeadDetail() {
  if (!state.selectedLeadId) return;
  const data = await api(`/api/leads/${state.selectedLeadId}`);
  if (window.LeadOverviewTab?.renderLeadDetail) {
    window.LeadOverviewTab.renderLeadDetail(data);
  }
}
window.loadLeadDetail = loadLeadDetail;

function renderNotificationsList(rows) {
  const root = byId("notificationsList");
  root.innerHTML = "";
  if (!rows.length) {
    root.innerHTML = `<div class="text-muted">No notifications found.</div>`;
    return;
  }

  rows.forEach((n) => {
    const row = document.createElement("div");
    row.className = "notification-row";
    row.innerHTML = `
      <div class="d-flex justify-content-between align-items-start">
        <div>
          <div class="fw-semibold">${n.lead_name || "Unknown"}</div>
          <div>${n.message || ""}</div>
          <div class="meta">${n.type || "info"} | ${n.lead_email || ""} | ${n.created_at || ""}</div>
        </div>
        <div class="d-flex gap-1">
          <button class="btn btn-sm btn-outline-success" data-read-id="${n._id}">Read</button>
          <button class="btn btn-sm btn-outline-danger" data-del-id="${n._id}">Delete</button>
        </div>
      </div>
    `;
    root.appendChild(row);
  });

  root.querySelectorAll("button[data-read-id]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      await api(`/api/notifications/read/${btn.dataset.readId}`, { method: "POST" });
      await refreshNotifications();
    });
  });

  root.querySelectorAll("button[data-del-id]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      await api(`/api/notifications/${btn.dataset.delId}`, { method: "DELETE" });
      await refreshNotifications();
    });
  });
}

function renderNotificationMenu(rows) {
  const menu = byId("notifMenu");
  menu.innerHTML = `<li><h6 class="dropdown-header">Unread Notifications</h6></li>`;
  if (!rows.length) {
    menu.innerHTML += `<li><span class="dropdown-item-text text-muted">No unread notifications</span></li>`;
    return;
  }
  rows.slice(0, 8).forEach((n) => {
    menu.innerHTML += `<li><span class="dropdown-item-text"><strong>${n.lead_name || "Unknown"}</strong><br><small>${n.message || ""}</small></span></li>`;
  });
}

async function refreshNotifications() {
  const mode = state.notificationMode || "unread";
  const data = await api(`/api/notifications?mode=${mode}&limit=100`);
  byId("notifBadge").textContent = data.unread_count || 0;
  renderNotificationsList(data.rows || []);

  const unreadData = await api(`/api/notifications?mode=unread&limit=20`);
  renderNotificationMenu(unreadData.rows || []);
}

async function loadLogs() {
  const data = await api("/api/message-logs?limit=50");
  const tbody = byId("logsTable").querySelector("tbody");
  tbody.innerHTML = "";
  (data.rows || []).forEach((row) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${row.created_at || row.time || ""}</td>
      <td>${row.company_name || ""}</td>
      <td>${row.to || ""}</td>
      <td>${row.subject || ""}</td>
      <td>${row.status || ""}</td>
      <td>${row.delivery_mode || ""}</td>
    `;
    tbody.appendChild(tr);
  });
}

async function generateSenderPlan() {
  if (!state.selectedLeadId) throw new Error("Select a lead first from Dashboard.");
  const payload = {
    lead_id: state.selectedLeadId,
    participant_email: byId("senderParticipant").value.trim(),
    customer_text: byId("senderCustomerText").value.trim(),
  };
  const data = await api("/api/sender/plan", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  state.senderPlan = data.plan;
  byId("senderPlanJson").value = JSON.stringify(data.plan, null, 2);
  showStatus("senderActionStatus", "Sender plan generated.");
}

async function applySenderPlan() {
  if (!state.selectedLeadId) throw new Error("Select a lead first from Dashboard.");
  const raw = byId("senderPlanJson").value.trim();
  const plan = raw ? JSON.parse(raw) : null;
  if (!plan) throw new Error("Plan JSON is required.");

  const payload = {
    lead_id: state.selectedLeadId,
    participant_email: byId("senderParticipant").value.trim(),
    thread_id: byId("senderThreadId").value.trim(),
    plan,
  };
  const data = await api("/api/sender/apply-plan", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  showStatus("senderActionStatus", `Draft created. Subject: ${data.subject}`);
  await refreshNotifications();
  await loadLogs();
}

async function generateCustomerTemplate() {
  const payload = {
    intent: byId("customerIntent").value,
    sender_subject: byId("customerSenderSubject").value,
    sender_body: byId("customerSenderBody").value,
  };
  const data = await api("/api/customer/template", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  byId("customerDraftSubject").value = data.subject || "";
  byId("customerDraftBody").value = data.body || "";
  showStatus("customerActionStatus", "Customer template generated.");
}

async function draftCustomerReply() {
  const payload = {
    product_description: "",
    company_name: "",
    subject: byId("customerDraftSubject").value,
    body: byId("customerDraftBody").value,
    thread_id: byId("customerThreadId").value,
    sender_addr: byId("customerSenderAddr").value,
  };
  const data = await api("/api/customer/draft", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  showStatus("customerActionStatus", `Customer draft created (${data.result.delivery_mode}).`);
  await refreshNotifications();
  await loadLogs();
}

async function runTwoWayCycle() {
  const data = await api("/api/cycles/two-way", {
    method: "POST",
    body: JSON.stringify({ max_messages_per_side: 5 }),
  });
  byId("sidebarStatus").textContent = `Cycle complete: sender=${data.sender_inbound_processed}, customer=${data.customer_inbound_processed}`;
  await refreshNotifications();
  await loadLogs();
}

async function createTestNotification() {
  await api("/api/notifications/test", { method: "POST" });
  await refreshNotifications();
}

function bindEvents() {
  document.querySelectorAll(".nav-section").forEach((btn) => {
    btn.addEventListener("click", () => sectionSwitch(btn.dataset.target));
  });

  byId("btnRefreshAll").addEventListener("click", async () => {
    try {
      const dashboardBootstrap = window.DashboardTab?.loadBootstrap ? window.DashboardTab.loadBootstrap() : Promise.resolve();
      const dashboardLeads = window.DashboardTab?.loadLeads ? window.DashboardTab.loadLeads() : Promise.resolve();
      await Promise.all([dashboardBootstrap, dashboardLeads, refreshNotifications(), loadLogs()]);
    } catch (err) {
      alert(err.message);
    }
  });

  byId("btnCreateTestNotification").addEventListener("click", async () => {
    await createTestNotification();
  });

  byId("btnRunCycle").addEventListener("click", async () => {
    try {
      await runTwoWayCycle();
    } catch (err) {
      alert(err.message);
    }
  });

}

async function init() {
  window.AppActions = {
    api,
    showStatus,
    refreshNotifications,
    loadLogs,
    loadLeadDetail,
    generateSenderPlan,
    applySenderPlan,
    generateCustomerTemplate,
    draftCustomerReply,
    runTwoWayCycle,
    createTestNotification,
  };

  bindEvents();
  window.SenderOutreachTab?.init?.();
  window.SenderTab?.init?.();
  window.CustomerTab?.init?.();
  window.NotificationsTab?.init?.();
  window.LogsTab?.init?.();

  if (window.DashboardTab?.init) {
    await window.DashboardTab.init();
  } else {
    await loadBootstrap();
    await loadLeads();
  }
  await refreshNotifications();
  await loadLogs();
}

init().catch((err) => {
  alert(err.message);
});
