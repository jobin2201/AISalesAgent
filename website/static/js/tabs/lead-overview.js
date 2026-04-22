window.LeadOverviewTab = (() => {
  function esc(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/\"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function toTimeValue(value) {
    if (!value) return 0;
    const ts = Date.parse(String(value));
    return Number.isNaN(ts) ? 0 : ts;
  }

  function formatDateTime(value) {
    const ts = toTimeValue(value);
    if (!ts) return "-";
    try {
      return new Date(ts).toLocaleString();
    } catch (_err) {
      return String(value || "-");
    }
  }

  function parseMeetingTimestamp(lead) {
    const candidates = [
      lead?.meeting_start,
      lead?.meeting_start_time,
      lead?.meeting_datetime,
      lead?.meeting_time,
      lead?.meeting_at,
      lead?.meeting_scheduled_for,
      lead?.meeting_date,
    ];
    for (const item of candidates) {
      const ts = toTimeValue(item);
      if (ts) return ts;
    }
    return 0;
  }

  function resolveMeetingUi(lead) {
    const rawState = String(lead?.meeting_state || "Not scheduled").trim();
    const normalized = rawState.toLowerCase();
    const meetingTs = parseMeetingTimestamp(lead);
    const now = Date.now();
    const wasBooked = normalized.includes("booked") || normalized.includes("confirmed") || normalized.includes("scheduled");

    if (meetingTs && meetingTs < now) {
      return {
        label: "Previously booked",
        chipClass: "lead-chip-meeting-past",
        panelClass: "meeting-state-past",
        detail: formatDateTime(meetingTs),
      };
    }

    if (meetingTs && meetingTs >= now) {
      return {
        label: "Booked",
        chipClass: "lead-chip-meeting-booked",
        panelClass: "meeting-state-booked",
        detail: "Upcoming",
      };
    }

    if (wasBooked && !meetingTs) {
      return {
        label: "Not booked",
        chipClass: "lead-chip-meeting",
        panelClass: "meeting-state-default",
        detail: "No Google Calendar event",
      };
    }

    return {
      label: "Not booked",
      chipClass: "lead-chip-meeting",
      panelClass: "meeting-state-default",
      detail: "No Google Calendar event",
    };
  }

  function buildPersonalizedThreads(data) {
    const recipients = (data.recipients || [])
      .map((r) => String(r || "").trim().toLowerCase())
      .filter(Boolean);
    const recipientSet = new Set(recipients);

    const history = Array.isArray(data.sender_contact_history) ? data.sender_contact_history : [];
    const senderMessages = history
      .map((row) => {
        const customer = String(
          row.to || row.recipient || row.participant_email || row.lead_email || ""
        ).trim().toLowerCase();
        const threadId = String(row.thread_id || row.gmail_thread_id || "").trim();
        return {
          type: "sender",
          customer,
          threadId,
          subject: String(row.subject || "").trim(),
          body: String(row.body || row.message || "").trim(),
          when: String(row.created_at || row.time || row.sent_at || "").trim(),
        };
      })
      .filter((row) => {
        if (!row.customer) return false;
        if (!recipientSet.size) return true;
        return recipientSet.has(row.customer);
      });

    const replies = (Array.isArray(data.sender_conversations) ? data.sender_conversations : [])
      .map((row) => {
        const customer = String(row.from_email || row.from || "").trim().toLowerCase();
        return {
          type: "customer",
          customer,
          threadId: String(row.thread_id || "").trim(),
          subject: String(row.subject || "").trim(),
          body: String(row.body || row.snippet || "").trim(),
          when: String(row.received_at || row.created_at || "").trim(),
        };
      })
      .filter((row) => {
        if (!row.customer || !row.threadId) return false;
        if (!recipientSet.size) return true;
        return recipientSet.has(row.customer);
      });

    const merged = [...senderMessages, ...replies];
    const grouped = new Map();

    merged.forEach((msg) => {
      const fallbackThread = msg.subject ? `subject:${msg.subject.toLowerCase()}` : "thread:unknown";
      const threadKey = msg.threadId || fallbackThread;
      const key = `${msg.customer}|${threadKey}`;
      if (!grouped.has(key)) {
        grouped.set(key, {
          customer: msg.customer,
          threadId: msg.threadId,
          messages: [],
        });
      }
      grouped.get(key).messages.push(msg);
    });

    const groups = Array.from(grouped.values());
    groups.forEach((g) => {
      g.messages.sort((a, b) => toTimeValue(b.when) - toTimeValue(a.when));
      g.latestTs = g.messages.length ? toTimeValue(g.messages[0].when) : 0;
    });
    groups.sort((a, b) => b.latestTs - a.latestTs);

    return groups;
  }

  function renderThreadGroups(groups, lead) {
    if (!groups.length) {
      return `<div class="text-muted">No personalized sender conversations found for this lead.</div>`;
    }

    return groups
      .map((group, index) => {
        const panelId = `threadPanel_${index}`;
        const threadLabel = group.threadId || "N/A";
        const latest = group.messages[0] || {};
        const companyName = String(lead?.company_name || "").trim() || "Unknown Company";
        const latestSubject = latest.subject || "(no subject)";
        const latestDate = latest.when || "";
        const rows = group.messages
          .map((msg) => {
            const whoClass = msg.type === "customer" ? "msg-customer" : "msg-sender";
            const whoLabel = msg.type === "customer" ? "Customer" : "Sender";
            const preview = msg.body || msg.subject || "(no content)";
            return `
              <div class="thread-msg ${whoClass}">
                <div class="thread-msg-head">
                  <span class="thread-role">${whoLabel}</span>
                  <span class="thread-time">${esc(msg.when || "")}</span>
                </div>
                <div class="thread-subject">${esc(msg.subject || "(no subject)")}</div>
                <div class="thread-body">${esc(preview)}</div>
              </div>
            `;
          })
          .join("");

        return `
          <div class="thread-group-card mb-3">
            <button class="thread-toggle-btn" type="button" data-thread-target="${panelId}" aria-expanded="false">
              <span class="thread-toggle-arrow" aria-hidden="true">▾</span>
              <span class="thread-toggle-main">
                <span class="thread-customer">${esc(group.customer)}</span>
                <span class="thread-company">${esc(companyName)}</span>
              </span>
              <span class="thread-toggle-meta">
                <span class="thread-subject-inline">${esc(latestSubject)}</span>
                <span class="thread-date-inline">${esc(latestDate)}</span>
                <span class="thread-id">Thread: ${esc(threadLabel)}</span>
              </span>
            </button>
            <div id="${panelId}" class="thread-panel" hidden>
              <div class="thread-msg-list">${rows}</div>
            </div>
          </div>
        `;
      })
      .join("");
  }

  function bindThreadToggles() {
    document.querySelectorAll(".thread-toggle-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const panelId = btn.getAttribute("data-thread-target");
        const panel = panelId ? document.getElementById(panelId) : null;
        if (!panel) return;
        const expanded = btn.getAttribute("aria-expanded") === "true";
        btn.setAttribute("aria-expanded", expanded ? "false" : "true");
        panel.hidden = expanded;
      });
    });
  }

  function renderLeadDetail(data) {
    const lead = data.lead || {};
    const recipients = (data.recipients || []).map((r) => `<span class="sender-contact-pill">${r}</span>`).join(" ");
    const contacted = (data.sender_contacted_emails || []).map((r) => `<span class="sender-contact-pill">${r}</span>`).join(" ");
    const linkedin = (data.linkedin_urls || []).map((url) => `<a class="lead-link-item" href="${url}" target="_blank" rel="noreferrer">LinkedIn ↗</a>`).join(" ");
    const threadGroups = buildPersonalizedThreads(data);
    const threadGroupsHtml = renderThreadGroups(threadGroups, lead);

    const meetingUi = resolveMeetingUi(lead);
    const sourceUrl = lead.source_url || lead.website || "";
    const sourceLink = sourceUrl ? `<a class="lead-link-item" href="${sourceUrl}" target="_blank" rel="noreferrer">Source ↗</a>` : "";

    document.getElementById("leadContent").innerHTML = `
      <div class="col-12 lead-overview-shell">
        <div class="row g-3">
          <div class="col-xl-4">
            <div class="lead-profile-card p-3 h-100">
              <div class="lead-profile-top mb-3">
                <div class="lead-avatar-wrap">
                  <img class="avatar" src="${data.profile_photo_url || ""}" alt="profile" />
                </div>
                <div class="lead-identity">
                  <h5 class="mb-1">${lead.company_name || "Lead"}</h5>
                  <div class="text-muted small">${lead.industry || "Unknown industry"}</div>
                </div>
              </div>

              <div class="lead-kpi-grid mb-3">
                <span class="lead-stat-chip lead-chip-priority">Priority: ${lead.priority || "-"}</span>
                <span class="lead-stat-chip lead-chip-score">Score: ${lead.total_score || 0}</span>
                <span class="lead-stat-chip ${meetingUi.chipClass}">Meeting: ${meetingUi.label}</span>
              </div>

              <div class="meeting-state-box ${meetingUi.panelClass} mb-3">
                <div class="meeting-state-label">Meeting Status</div>
                <div class="meeting-state-value">${meetingUi.label}</div>
                <div class="meeting-state-detail">${meetingUi.detail}</div>
              </div>

              <div class="lead-mini-grid mb-3">
                <div class="lead-mini-item">
                  <div class="mini-label">Recipients</div>
                  <div class="mini-value">${(data.recipients || []).length || 0}</div>
                </div>
                <div class="lead-mini-item">
                  <div class="mini-label">Contacted</div>
                  <div class="mini-value">${(data.sender_contacted_emails || []).length || 0}</div>
                </div>
              </div>

              <div class="lead-section-title">Recommended Action</div>
              <p class="lead-body-text mb-0">${lead.recommended_action || "-"}</p>
            </div>
          </div>

          <div class="col-xl-8">
            <div class="lead-about-card p-3 mb-3">
              <div class="lead-section-title">Company Snapshot</div>
              <p class="lead-body-text mb-2">${lead.description || "No description available."}</p>
              <div class="lead-section-title">Product</div>
              <p class="lead-body-text mb-2">${lead.product_description || "-"}</p>
              <div class="lead-section-title">Lead Source</div>
              <div>${sourceLink || "<span class='text-muted'>No source URL</span>"}</div>
            </div>

            <div class="lead-linkedin-card p-3 mb-3">
              <div class="lead-section-title">LinkedIn</div>
              <div>${linkedin || "<span class='text-muted'>No LinkedIn URL found.</span>"}</div>
            </div>

            <div class="lead-contacts-card p-3 mb-3">
              <div class="lead-section-title">Contacts Sender Has Contacted</div>
              <div>${contacted || "<span class='text-muted'>No contacted recipients yet.</span>"}</div>
              <hr>
              <div class="lead-section-title">Current Recipients</div>
              <div>${recipients || "<span class='text-muted'>No recipients found.</span>"}</div>
            </div>

            <div class="lead-conversations-card p-3">
              <div class="lead-section-title">Sender Conversations</div>
              <div class="thread-groups-wrap">${threadGroupsHtml}</div>
            </div>
          </div>
        </div>
      </div>
    `;

    bindThreadToggles();
  }

  return { renderLeadDetail };
})();
