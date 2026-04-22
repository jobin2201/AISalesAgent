window.SenderTab = (() => {
  const state = {
    detail: null,
    conversations: [],
    selectedIndex: 0,
    guidedPlan: null,
    draftDefaults: {
      subject: "",
      body: "",
      plan: "{}",
    },
  };

  function byId(id) {
    return document.getElementById(id);
  }

  function mode() {
    return byId("senderModeManual")?.checked ? "manual" : "guided";
  }

  function getSelectedLead() {
    const leadId = window.AppState?.selectedLeadId;
    const leads = Array.isArray(window.AppState?.leads) ? window.AppState.leads : [];
    return leads.find((x) => x.id === leadId) || null;
  }

  function safeReplySubject(subject) {
    const s = String(subject || "").trim();
    if (!s) return "Re: Quick follow-up";
    if (/^re:/i.test(s)) return s;
    return `Re: ${s}`;
  }

  function senderCompanyName(productDescription) {
    const value = String(productDescription || "").trim().toLowerCase();
    if (value.includes("digicom")) return "DigiCom Private Ltd";
    if (value.includes("digiexpense") || value.includes("digidelight") || value.includes("digi delight")) {
      return "Digi Delight Solution Private Ltd";
    }
    return "Digi Delight Solution Private Ltd";
  }

  function ensureSenderSignature(body, productDescription) {
    const company = senderCompanyName(productDescription);
    const raw = String(body || "").replace(/\r\n/g, "\n").trimEnd();
    const normalized = raw.replace(/\n*Best,\s*\nAI Sales Team\s*$/i, "").trimEnd();
    const lower = normalized.toLowerCase();
    const signatureBlock = `balaji,\n${company}`.toLowerCase();
    const signatureInline = `balaji, ${company}`.toLowerCase();

    if (lower.endsWith(signatureBlock) || lower.endsWith(signatureInline)) {
      return normalized;
    }

    return normalized ? `${normalized}\n\nBalaji,\n${company}` : `Balaji,\n${company}`;
  }

  function formatTs(value) {
    const ts = Date.parse(String(value || ""));
    if (Number.isNaN(ts)) return String(value || "");
    return new Date(ts).toLocaleString("en-IN", {
      year: "numeric",
      month: "short",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: true,
      timeZone: "Asia/Kolkata",
    }) + " IST";
  }

  function renderMeta() {
    const lead = state.detail?.lead || getSelectedLead() || {};
    byId("senderMetaLead").textContent = lead.company_name || "Unknown";
    byId("senderMetaSender").textContent = state.detail?.lead?.sender_loop || "Configured sender mailbox";
    byId("senderMetaCustomer").textContent = state.detail?.lead?.customer_loop || "Configured customer mailbox";
  }

  function renderConversationSelect() {
    const sel = byId("senderConversationSelect");
    sel.innerHTML = "";
    if (!state.conversations.length) {
      const opt = document.createElement("option");
      opt.value = "";
      opt.textContent = "No customer replies found";
      sel.appendChild(opt);
      return;
    }

    state.conversations.forEach((item, idx) => {
      const opt = document.createElement("option");
      const when = item.received_at ? ` | ${formatTs(item.received_at)}` : "";
      opt.value = String(idx);
      opt.textContent = `${item.from_email || "unknown"} | ${item.subject || "(no subject)"}${when}`;
      sel.appendChild(opt);
    });
    sel.value = String(state.selectedIndex);
  }

  function findLastSenderDraft(participant) {
    const logs = Array.isArray(state.detail?.sender_contact_history) ? state.detail.sender_contact_history : [];
    const p = String(participant || "").trim().toLowerCase();
    return logs.find((log) => String(log.to || "").trim().toLowerCase() === p) || null;
  }

  function renderSelectedConversation() {
    const lead = state.detail?.lead || {};
    const conv = state.conversations[state.selectedIndex] || null;
    const participant = conv?.from_email || "";
    const threadId = conv?.thread_id || "";

    byId("senderParticipant").value = participant;
    byId("senderThreadId").value = threadId;

    byId("senderSummaryParticipant").textContent = participant || "-";
    byId("senderSummaryMeetingState").textContent = lead.meeting_state || "-";
    byId("senderSummaryMeetingContact").textContent = lead.meeting_contact_email || "-";
    byId("senderSummaryThreadId").textContent = `Thread ID: ${threadId || "-"}`;

    byId("senderCtxFrom").textContent = participant || "Unknown";
    byId("senderCtxSubject").textContent = conv?.subject || "Quick follow-up";
    byId("senderCustomerText").value = conv?.body || "";

    const lastDraft = findLastSenderDraft(participant);
    byId("senderPrevSubject").textContent = lastDraft?.subject || "-";
    byId("senderPreviousDraft").value = lastDraft?.body || "No previous sender draft is logged yet for this participant.";

    const participants = Array.isArray(state.detail?.participants) ? state.detail.participants : [];
    byId("senderTrackedParticipants").textContent = `Tracked participants: ${participants.length ? participants.join(", ") : "None found"}`;
  }

  function setDraftFields(subject, body, plan) {
    byId("senderReplySubject").value = subject || "";
    byId("senderReplyBody").value = body || "";
    byId("senderPlanJson").value = JSON.stringify(plan || {}, null, 2);

    state.draftDefaults.subject = subject || "";
    state.draftDefaults.body = body || "";
    state.draftDefaults.plan = JSON.stringify(plan || {}, null, 2);
  }

  function resetSuggestion() {
    byId("senderReplySubject").value = state.draftDefaults.subject || "";
    byId("senderReplyBody").value = state.draftDefaults.body || "";
    byId("senderPlanJson").value = state.draftDefaults.plan || "{}";
    window.AppActions.showStatus("senderActionStatus", "Sender suggestion reset.");
  }

  function setModeUi() {
    const guided = mode() === "guided";
    byId("senderPlanJson").disabled = !guided;
    byId("btnGenerateSenderPlan").disabled = !guided;
    byId("senderPlanHint").textContent = guided
      ? "Generate suggestion to view detected intent/action."
      : "Manual mode: subject/body are editable directly.";
  }

  async function loadData() {
    const lead = getSelectedLead();
    if (!lead?.id) {
      window.AppActions.showStatus("senderActionStatus", "Select a lead first from Dashboard.", true);
      return;
    }

    const detail = await window.AppActions.api(`/api/leads/${lead.id}`);
    state.detail = detail;
    state.conversations = (Array.isArray(detail.sender_conversations) ? detail.sender_conversations : []).filter((x) => !x.error);
    state.selectedIndex = 0;

    renderMeta();
    renderConversationSelect();

    if (!state.conversations.length) {
      byId("senderActionStatus").textContent = "No customer replies were found yet for this selected lead.";
      return;
    }

    renderSelectedConversation();
    await generateSuggestion(true);
  }

  async function generateSuggestion(silent = false) {
    const conv = state.conversations[state.selectedIndex];
    if (!conv) {
      throw new Error("No customer conversation selected.");
    }

    const participant = byId("senderParticipant").value.trim();
    const inboundBody = byId("senderCustomerText").value || "";

    if (mode() === "manual") {
      const lead = state.detail?.lead || getSelectedLead() || {};
      const manualPlan = {
        kind: "manual",
        action: "manual",
        subject: safeReplySubject(conv.subject),
        body: ensureSenderSignature("Hi,\n\n", lead.product_description || ""),
      };
      setDraftFields(manualPlan.subject, manualPlan.body, manualPlan);
      if (!silent) window.AppActions.showStatus("senderActionStatus", "Manual baseline loaded.");
      return;
    }

    const data = await window.AppActions.api("/api/sender/plan", {
      method: "POST",
      body: JSON.stringify({
        lead_id: window.AppState.selectedLeadId,
        participant_email: participant,
        customer_text: inboundBody,
      }),
    });

    const plan = data.plan || {};
    state.guidedPlan = plan;
    const lead = state.detail?.lead || getSelectedLead() || {};
    const subject = String(plan.subject || safeReplySubject(conv.subject));
    const body = ensureSenderSignature(String(plan.body || ""), lead.product_description || "");
    setDraftFields(subject, body, plan);

    const hint = plan.kind === "scheduling"
      ? `Scheduling action detected: ${plan.action || "unknown"}`
      : `NLP intent detected: ${plan.intent || "unknown"}`;
    byId("senderPlanHint").textContent = hint;

    if (!silent) window.AppActions.showStatus("senderActionStatus", "Sender plan generated.");
  }

  async function draftInSenderMailbox() {
    const participant = byId("senderParticipant").value.trim();
    const threadId = byId("senderThreadId").value.trim();
    const subject = byId("senderReplySubject").value.trim();
    const body = byId("senderReplyBody").value.trim();
    const rawPlan = byId("senderPlanJson").value.trim();

    if (!participant) throw new Error("Participant email is required.");
    if (!threadId) throw new Error("Thread id is required.");
    if (!subject) throw new Error("Reply subject is required.");
    if (!body) throw new Error("Reply body is required.");

    let plan = {};
    try {
      plan = rawPlan ? JSON.parse(rawPlan) : {};
    } catch (_err) {
      throw new Error("Plan JSON is invalid.");
    }

    if (mode() === "manual") {
      plan = {
        ...(plan || {}),
        kind: "manual",
        action: "manual",
      };
    }

    plan.subject = subject;
    const lead = state.detail?.lead || getSelectedLead() || {};
    plan.body = ensureSenderSignature(body, lead.product_description || "");

    const data = await window.AppActions.api("/api/sender/apply-plan", {
      method: "POST",
      body: JSON.stringify({
        lead_id: window.AppState.selectedLeadId,
        participant_email: participant,
        thread_id: threadId,
        plan,
      }),
    });

    window.AppActions.showStatus("senderActionStatus", `Sender Gmail draft created. Subject: ${data.subject}`);
    await window.AppActions.refreshNotifications();
    await window.AppActions.loadLogs();
  }

  function init() {
    const generate = document.getElementById("btnGenerateSenderPlan");
    const apply = document.getElementById("btnApplySenderPlan");
    const refresh = document.getElementById("btnSenderRefreshInbox");
    const reset = document.getElementById("btnSenderResetSuggestion");
    const select = document.getElementById("senderConversationSelect");
    const modeGuided = document.getElementById("senderModeGuided");
    const modeManual = document.getElementById("senderModeManual");
    const openButtons = document.querySelectorAll(".nav-section[data-target='senderSection']");

    openButtons.forEach((btn) => {
      btn.addEventListener("click", () => {
        loadData().catch((err) => window.AppActions.showStatus("senderActionStatus", err.message, true));
      });
    });

    if (select) {
      select.addEventListener("change", async () => {
        state.selectedIndex = Number(select.value || 0);
        renderSelectedConversation();
        await generateSuggestion(true);
      });
    }

    if (refresh) {
      refresh.addEventListener("click", () => {
        loadData().catch((err) => window.AppActions.showStatus("senderActionStatus", err.message, true));
      });
    }

    if (reset) {
      reset.addEventListener("click", resetSuggestion);
    }

    [modeGuided, modeManual].forEach((el) => {
      if (!el) return;
      el.addEventListener("change", async () => {
        setModeUi();
        await generateSuggestion(true);
      });
    });

    if (generate) {
      generate.addEventListener("click", async () => {
        try {
          await generateSuggestion(false);
        } catch (err) {
          window.AppActions.showStatus("senderActionStatus", err.message, true);
        }
      });
    }
    if (apply) {
      apply.addEventListener("click", async () => {
        try {
          await draftInSenderMailbox();
        } catch (err) {
          window.AppActions.showStatus("senderActionStatus", err.message, true);
        }
      });
    }

    setModeUi();
  }

  return { init };
})();
