window.DashboardTab = (() => {
  function metricCard(label, value, note, idx) {
    return `
      <div class="col-md-4">
        <div class="metric-card" style="animation-delay:${idx * 80}ms">
          <div class="metric-label">${label}</div>
          <div class="metric-value" data-count-to="${value}">0</div>
          <div class="metric-note">${note || ""}</div>
        </div>
      </div>
    `;
  }

  function animateCounters() {
    document.querySelectorAll(".metric-value[data-count-to]").forEach((el) => {
      const target = Number(el.getAttribute("data-count-to") || "0");
      const duration = 450;
      const start = performance.now();

      function tick(now) {
        const pct = Math.min(1, (now - start) / duration);
        el.textContent = Math.round(target * pct);
        if (pct < 1) requestAnimationFrame(tick);
      }
      requestAnimationFrame(tick);
    });
  }

  function renderMetrics(metrics) {
    const root = byId("metricCards");
    root.innerHTML = [
      metricCard("Leads", metrics.leads ?? 0, "Matching current filters", 0),
      metricCard("Products", metrics.products ?? 0, "Available in dataset", 1),
      metricCard("Email Contacts", metrics.email_contacts ?? 0, "Reachable contacts", 2),
    ].join("");
    animateCounters();
  }

  function priorityChip(priority) {
    const p = String(priority || "").toUpperCase();
    const cls = p === "HIGH" ? "dashboard-priority-high" : p === "MEDIUM" ? "dashboard-priority-medium" : "dashboard-priority-low";
    return `<span class="dashboard-priority-chip ${cls}">${p || "-"}</span>`;
  }

  function renderInsight(row) {
    const panel = byId("dashboardInsightPanel");
    if (!row) {
      panel.textContent = "Select a lead from the table to preview quick insight and open full Lead Overview.";
      return;
    }
    panel.textContent =
      `Company: ${row.company_name || "-"}\n` +
      `Product: ${row.product_description || "-"}\n` +
      `Industry: ${row.industry || "-"}\n` +
      `Priority: ${row.priority || "-"}\n` +
      `Score: ${row.total_score || 0}\n\n` +
      `Use Open to jump into the full Lead Overview with recipients, parsed details, and conversation preview.`;
  }

  function renderLeads(rows) {
    const tbody = byId("leadsTable").querySelector("tbody");
    tbody.innerHTML = "";
    byId("leadCountBadge").textContent = `${rows.length} leads`;

    rows.forEach((row) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${row.company_name || ""}</td>
        <td>${row.product_description || ""}</td>
        <td>${row.industry || ""}</td>
        <td>${priorityChip(row.priority)}</td>
        <td><strong>${row.total_score || 0}</strong></td>
        <td><button class="btn btn-sm btn-outline-primary" data-lead-id="${row.id}">Open</button></td>
      `;

      tr.addEventListener("mouseenter", () => renderInsight(row));
      tr.addEventListener("focusin", () => renderInsight(row));
      tbody.appendChild(tr);
    });

    tbody.querySelectorAll("button[data-lead-id]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        window.AppState.selectedLeadId = btn.dataset.leadId;
        await loadLeadDetail();
        sectionSwitch("leadSection");
      });
    });
  }

  async function loadBootstrap() {
    const data = await api("/api/bootstrap");
    const sel = byId("filterProduct");
    sel.innerHTML = `<option value="">All Products</option>`;
    (data.products || []).forEach((p) => {
      const option = document.createElement("option");
      option.value = p;
      option.textContent = p;
      sel.appendChild(option);
    });
    byId("sidebarStatus").textContent = `Sender: ${data.sender_loop || "Not configured"} | Customer: ${data.customer_loop || "Not configured"}`;
    byId("notifBadge").textContent = data.unread_notifications || 0;
  }

  async function loadLeads() {
    const priorities = byId("filterPriority").value || "";
    const params = new URLSearchParams({
      product: byId("filterProduct").value,
      priorities,
      min_score: byId("filterMinScore").value || "0",
      q: byId("filterQuery").value || "",
    });
    const data = await api(`/api/leads?${params.toString()}`);
    window.AppState.leads = data.rows || [];
    renderMetrics(data.metrics || {});
    renderLeads(window.AppState.leads);
  }

  function bindEvents() {
    byId("btnLoadLeads").addEventListener("click", async () => {
      try {
        await loadLeads();
      } catch (err) {
        alert(err.message);
      }
    });

    byId("btnDashboardReset").addEventListener("click", async () => {
      byId("filterProduct").value = "";
      byId("filterMinScore").value = "0";
      byId("filterQuery").value = "";
      byId("filterPriority").value = "HIGH";
      await loadLeads();
    });

    byId("btnQuickHighScore").addEventListener("click", async () => {
      byId("filterMinScore").value = "75";
      await loadLeads();
    });

    byId("btnQuickAllPriority").addEventListener("click", async () => {
      byId("filterPriority").value = "";
      await loadLeads();
    });

    byId("filterProduct").addEventListener("change", async () => {
      await loadLeads();
    });

    byId("filterPriority").addEventListener("change", async () => {
      await loadLeads();
    });

    byId("filterMinScore").addEventListener("change", async () => {
      await loadLeads();
    });

    let typingTimer = null;
    byId("filterQuery").addEventListener("input", () => {
      if (typingTimer) clearTimeout(typingTimer);
      typingTimer = setTimeout(() => {
        loadLeads().catch((err) => alert(err.message));
      }, 280);
    });
  }

  async function init() {
    bindEvents();
    await loadBootstrap();
    await loadLeads();
    renderInsight(null);
  }

  return {
    init,
    loadBootstrap,
    loadLeads,
  };
})();
