window.CustomerTab = (() => {
  const state = {
    context: null,
    flowMap: {},
    suggestedIntent: "need_clarity",
    lastGenerated: { subject: "", body: "" },
    parsedSlots: [],
    baseGuidedSubject: "",
    baseGuidedBody: "",
  };

  function byId(id) {
    return document.getElementById(id);
  }

  function selectedMode() {
    return byId("customerModeManual")?.checked ? "manual" : "guided";
  }

  function parseSlotTable(slotText) {
    const out = [];
    String(slotText || "")
      .split(/\r?\n/)
      .forEach((raw) => {
        const line = raw.trim();
        if (!line || !line.includes("|")) return;
        if (line.toLowerCase().startsWith("date") || line.startsWith("---")) return;
        const parts = line.split("|").map((x) => x.trim());
        if (parts.length < 3) return;
        const dateLabel = parts[0];
        const dayLabel = parts[1];
        const windows = parts[2];
        if (!windows || windows.toLowerCase() === "busy") return;
        windows
          .split(",")
          .map((x) => x.trim())
          .filter(Boolean)
          .forEach((window) => {
            out.push(`${dayLabel}, ${dateLabel} ${window} IST`);
          });
      });
    return out;
  }

  function toMinutes(label) {
    const m = String(label || "").match(/^(\d{1,2}):(\d{2})\s*([AP]M)$/i);
    if (!m) return null;
    let hour = Number(m[1]);
    const min = Number(m[2]);
    const ap = m[3].toUpperCase();
    if (ap === "PM" && hour !== 12) hour += 12;
    if (ap === "AM" && hour === 12) hour = 0;
    return hour * 60 + min;
  }

  function toLabel(minutes) {
    let h = Math.floor(minutes / 60) % 24;
    const m = minutes % 60;
    const ap = h >= 12 ? "PM" : "AM";
    h = h % 12;
    if (h === 0) h = 12;
    return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")} ${ap}`;
  }

  function buildStartOptions(slotLabel, duration) {
    const match = String(slotLabel || "").match(/^(.*)\s(\d{1,2}:\d{2}\s*[AP]M)-(\d{1,2}:\d{2}\s*[AP]M)\s+IST$/i);
    if (!match) return [];
    const start = toMinutes(match[2].toUpperCase());
    const endRaw = toMinutes(match[3].toUpperCase());
    if (start == null || endRaw == null) return [];
    let end = endRaw;
    if (end <= start) end += 24 * 60;
    const opts = [];
    for (let cur = start; cur + duration <= end; cur += 15) {
      opts.push(toLabel(cur));
    }
    return opts;
  }

  function applySchedulingToDraft() {
    const intent = byId("customerIntent")?.value;
    if (intent !== "accept" || selectedMode() !== "guided") {
      byId("customerDraftSubject").value = state.baseGuidedSubject || byId("customerDraftSubject").value;
      byId("customerDraftBody").value = state.baseGuidedBody || byId("customerDraftBody").value;
      return;
    }

    const slot = byId("customerSlotSelect")?.value || "";
    const duration = Number(byId("customerDurationSelect")?.value || "30");
    const startLabel = byId("customerSlotStartSelect")?.value || "";
    if (!slot || !startLabel) return;

    const slotMatch = String(slot).match(/^(.*)\s(\d{1,2}:\d{2}\s*[AP]M)-(\d{1,2}:\d{2}\s*[AP]M)\s+IST$/i);
    if (!slotMatch) return;
    const slotLabel = slotMatch[1];
    const startMin = toMinutes(startLabel.toUpperCase());
    if (startMin == null) return;
    const endMin = startMin + duration;
    const preciseSlot = `${slotLabel} ${toLabel(startMin)}-${toLabel(endMin)} IST`;

    const customerAddr = state.context?.customer_email || "customer";
    const subjectSlotLabel = preciseSlot.replace(" IST", "");
    let body = String(state.baseGuidedBody || byId("customerDraftBody").value || "");
    let subject = `Re: Meeting slot request @ ${subjectSlotLabel} (IST) (${customerAddr})`;

    if (/Preferred slot:\s*.*/i.test(body)) {
      body = body.replace(/Preferred slot:\s*.*/i, `Preferred slot: ${preciseSlot}`);
    }
    if (/Preferred duration:\s*.*/i.test(body)) {
      body = body.replace(/Preferred duration:\s*.*/i, `Preferred duration: ${duration} minutes`);
    } else if (body.includes("If that slot is still available, please send the calendar invite.")) {
      body = body.replace(
        "\n\nIf that slot is still available, please send the calendar invite.",
        `\nPreferred duration: ${duration} minutes\n\nIf that slot is still available, please send the calendar invite.`
      );
    }

    byId("customerDraftSubject").value = subject;
    byId("customerDraftBody").value = body;
  }

  async function updateSchedulingUi() {
    const guided = selectedMode() === "guided";
    const intent = byId("customerIntent")?.value;
    const show = guided && intent === "accept";
    byId("customerSchedulingBox")?.classList.toggle("d-none", !show);
    byId("customerCalendarDetails")?.classList.toggle("d-none", !show);
    if (!show) return;

    const subject = byId("customerSenderSubject").value || "";
    const body = byId("customerSenderBody").value || "";
    const intentCheck = await window.AppActions.api("/api/customer/scheduling-intent", {
      method: "POST",
      body: JSON.stringify({ sender_subject: subject, sender_body: body }),
    });
    if (!intentCheck?.has_scheduling_intent) {
      byId("customerSchedulingBox")?.classList.add("d-none");
      byId("customerCalendarDetails")?.classList.add("d-none");
      return;
    }

    const slotResp = await window.AppActions.api("/api/calendar/slots?lookahead_days=7");
    byId("customerCalendarText").textContent = slotResp.slot_text || "";
    state.parsedSlots = parseSlotTable(slotResp.slot_text || "");

    const slotSel = byId("customerSlotSelect");
    slotSel.innerHTML = "";
    if (!state.parsedSlots.length) {
      const opt = document.createElement("option");
      opt.value = "";
      opt.textContent = "No available slots";
      slotSel.appendChild(opt);
      return;
    }
    state.parsedSlots.forEach((slot) => {
      const opt = document.createElement("option");
      opt.value = slot;
      opt.textContent = slot;
      slotSel.appendChild(opt);
    });

    refreshStartOptions();
    applySchedulingToDraft();
  }

  function refreshStartOptions() {
    const slot = byId("customerSlotSelect")?.value || "";
    const duration = Number(byId("customerDurationSelect")?.value || "30");
    const starts = buildStartOptions(slot, duration);
    const startSel = byId("customerSlotStartSelect");
    startSel.innerHTML = "";
    if (!starts.length) {
      const opt = document.createElement("option");
      opt.value = "";
      opt.textContent = "No valid start time";
      startSel.appendChild(opt);
      return;
    }
    starts.forEach((start) => {
      const opt = document.createElement("option");
      opt.value = start;
      opt.textContent = start;
      startSel.appendChild(opt);
    });
  }

  function setFlowDescription() {
    const intent = byId("customerIntent")?.value || "";
    const flow = state.flowMap[intent];
    byId("customerFlowDescription").textContent = flow?.description || "Pick a path to generate a tailored draft.";
  }

  function getSelectedLead() {
    const leadId = window.AppState?.selectedLeadId;
    const leads = Array.isArray(window.AppState?.leads) ? window.AppState.leads : [];
    return leads.find((x) => x.id === leadId) || null;
  }

  async function loadContext() {
    try {
      const lead = getSelectedLead();
      const data = await window.AppActions.api("/api/customer/context");
      const ctx = data?.context || {};
      const latest = ctx.latest_message || {};

      state.context = ctx;
      byId("customerMetaSender").textContent = ctx.sender_email || "Not configured";
      byId("customerMetaCustomer").textContent = ctx.customer_email || "Not configured";
      byId("customerMetaLead").textContent = lead?.company_name || "Unknown";

      byId("customerCtxFrom").textContent = ctx.sender_email || "Unknown";
      byId("customerCtxTo").textContent = ctx.customer_email || "Unknown";
      byId("customerCtxSubject").textContent = latest.subject || "Quick follow-up";
      byId("customerCtxThread").textContent = latest.thread_id || "-";

      byId("customerSenderSubject").value = latest.subject || "Quick follow-up";
      byId("customerSenderBody").value = latest.body || "";
      byId("customerThreadId").value = latest.thread_id || "";
      byId("customerSenderAddr").value = ctx.sender_email || "";

      await generateTemplate(true);
    } catch (err) {
      window.AppActions.showStatus("customerActionStatus", `Context not ready: ${err.message}`, true);
    }
  }

  async function generateTemplate(silent = false) {
    const intent = byId("customerIntent").value;
    const senderSubject = byId("customerSenderSubject").value || "Quick follow-up";
    const senderBody = byId("customerSenderBody").value || "";

    const data = await window.AppActions.api("/api/customer/template", {
      method: "POST",
      body: JSON.stringify({
        intent,
        sender_subject: senderSubject,
        sender_body: senderBody,
      }),
    });

    state.flowMap = {};
    (data.choices || []).forEach((item) => {
      state.flowMap[item.id] = item;
    });

    if (!byId("customerIntent").dataset.flowLoaded && data.default_choice) {
      byId("customerIntent").value = data.default_choice;
      byId("customerIntent").dataset.flowLoaded = "1";
    }

    if (selectedMode() === "guided") {
      byId("customerDraftSubject").value = data.subject || "";
      byId("customerDraftBody").value = data.body || "";
      state.lastGenerated = { subject: data.subject || "", body: data.body || "" };
      state.baseGuidedSubject = data.subject || "";
      state.baseGuidedBody = data.body || "";
    }

    setFlowDescription();
    await updateSchedulingUi();
    applySchedulingToDraft();
    if (!silent) {
      window.AppActions.showStatus("customerActionStatus", "Template generated.");
    }
  }

  function resetDraft() {
    byId("customerDraftSubject").value = state.lastGenerated.subject || "";
    byId("customerDraftBody").value = state.lastGenerated.body || "";
    window.AppActions.showStatus("customerActionStatus", "Draft reset to latest generated template.");
  }

  async function draftReply() {
    const lead = getSelectedLead();
    const subject = byId("customerDraftSubject").value.trim();
    const body = byId("customerDraftBody").value.trim();
    const threadId = byId("customerThreadId").value.trim();
    const senderAddr = byId("customerSenderAddr").value.trim();
    if (!subject || !body || !threadId) {
      throw new Error("Reply subject, reply body and thread id are required.");
    }

    const payload = {
      product_description: lead?.product_description || "",
      company_name: lead?.company_name || "",
      subject,
      body,
      thread_id: threadId,
      sender_addr: senderAddr,
      customer_addr: state.context?.customer_email || "",
    };

    const data = await window.AppActions.api("/api/customer/draft", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    window.AppActions.showStatus("customerActionStatus", `Customer draft created (${data.result.delivery_mode}).`);
  }

  function setModeUi() {
    const guided = selectedMode() === "guided";
    byId("customerIntent").disabled = !guided;
    byId("btnGenerateCustomerTemplate").disabled = !guided;
    byId("customerFlowDescription").classList.toggle("customer-flow-muted", !guided);
  }

  function init() {
    const generate = document.getElementById("btnGenerateCustomerTemplate");
    const draft = document.getElementById("btnDraftCustomerReply");
    const reset = document.getElementById("btnCustomerResetDraft");
    const intent = document.getElementById("customerIntent");
    const modeGuided = document.getElementById("customerModeGuided");
    const modeManual = document.getElementById("customerModeManual");
    const openButtons = document.querySelectorAll(".nav-section[data-target='customerSection']");

    if (generate) {
      generate.addEventListener("click", async () => {
        try {
          await generateTemplate(false);
        } catch (err) {
          window.AppActions.showStatus("customerActionStatus", err.message, true);
        }
      });
    }

    if (draft) {
      draft.addEventListener("click", async () => {
        try {
          await draftReply();
        } catch (err) {
          window.AppActions.showStatus("customerActionStatus", err.message, true);
        }
      });
    }

    if (reset) {
      reset.addEventListener("click", resetDraft);
    }

    if (intent) {
      intent.addEventListener("change", async () => {
        try {
          setFlowDescription();
          if (selectedMode() === "guided") {
            await generateTemplate(true);
          }
          applySchedulingToDraft();
        } catch (err) {
          window.AppActions.showStatus("customerActionStatus", err.message, true);
        }
      });
    }

    [modeGuided, modeManual].forEach((el) => {
      if (!el) return;
      el.addEventListener("change", async () => {
        try {
          setModeUi();
          if (selectedMode() === "guided") {
            await generateTemplate(true);
          }
          await updateSchedulingUi();
          applySchedulingToDraft();
        } catch (err) {
          window.AppActions.showStatus("customerActionStatus", err.message, true);
        }
      });
    });

    byId("customerSlotSelect")?.addEventListener("change", () => {
      refreshStartOptions();
      applySchedulingToDraft();
    });
    byId("customerDurationSelect")?.addEventListener("change", () => {
      refreshStartOptions();
      applySchedulingToDraft();
    });
    byId("customerSlotStartSelect")?.addEventListener("change", () => {
      applySchedulingToDraft();
    });

    openButtons.forEach((btn) => {
      btn.addEventListener("click", () => {
        loadContext().catch((err) => window.AppActions.showStatus("customerActionStatus", err.message, true));
      });
    });

    setModeUi();
    loadContext().catch(() => undefined);
  }

  return { init };
})();
