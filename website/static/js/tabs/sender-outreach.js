window.SenderOutreachTab = (() => {
  const state = {
    detail: null,
    selectedTemplateId: "",
    defaults: {
      subject: "",
      body: "",
      manualRecipient: "",
    },
  };

  function byId(id) {
    return document.getElementById(id);
  }

  function getSelectedLead() {
    const leadId = window.AppState?.selectedLeadId;
    const leads = Array.isArray(window.AppState?.leads) ? window.AppState.leads : [];
    return leads.find((x) => x.id === leadId) || null;
  }

  function senderCompanyName(productDescription) {
    const value = String(productDescription || "").trim().toLowerCase();
    if (value.includes("digicom")) return "DigiCom Private Ltd";
    if (value.includes("digiexpense") || value.includes("digidelight") || value.includes("digi delight")) {
      return "Digi Delight Solution Private Ltd";
    }
    return "Digi Delight Solution Private Ltd";
  }

  function ensureSignature(body, productDescription) {
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

  function selectedRecipient() {
    return (byId("outreachManualRecipient").value || byId("outreachKnownRecipient").value || "").trim();
  }

  function mapTemplateLabel(doc) {
    const contact = doc.contact_name || "No contact";
    const email = doc.contact_email || "No email";
    const subject = doc.email_subject || "No subject";
    return `${contact} | ${email} | ${subject}`;
  }

  function renderTemplateOptions(relatedEmails) {
    const sel = byId("outreachTemplateSelect");
    sel.innerHTML = "";
    if (!relatedEmails.length) {
      const opt = document.createElement("option");
      opt.value = "";
      opt.textContent = "No stored templates";
      sel.appendChild(opt);
      return;
    }

    relatedEmails.forEach((doc, index) => {
      const opt = document.createElement("option");
      opt.value = String(doc._id || doc.id || index);
      opt.textContent = mapTemplateLabel(doc);
      sel.appendChild(opt);
    });
    state.selectedTemplateId = sel.value;
  }

  function renderRecipients(recipients) {
    const sel = byId("outreachKnownRecipient");
    sel.innerHTML = "";
    const list = recipients.length ? recipients : [""];
    list.forEach((email) => {
      const opt = document.createElement("option");
      opt.value = email;
      opt.textContent = email || "No known recipients";
      sel.appendChild(opt);
    });
  }

  function fillFromSelectedTemplate() {
    const detail = state.detail || {};
    const lead = detail.lead || {};
    const related = Array.isArray(detail.related_emails) ? detail.related_emails : [];
    const picked = related.find((doc, index) => String(doc._id || doc.id || index) === byId("outreachTemplateSelect").value) || null;

    const defaultSubject = (picked?.email_subject || lead.draft_email_subject || "").trim();
    const defaultBody = ensureSignature((picked?.email_body || lead.draft_email_body || "").trim(), lead.product_description || "");

    byId("outreachSubject").value = defaultSubject;
    byId("outreachBody").value = defaultBody;
    state.defaults.subject = defaultSubject;
    state.defaults.body = defaultBody;
  }

  function renderMeta() {
    const lead = state.detail?.lead || getSelectedLead() || {};
    byId("outreachMetaLead").textContent = lead.company_name || "Unknown";
    byId("outreachMetaProduct").textContent = lead.product_description || "-";
    byId("outreachMetaWebsite").textContent = lead.website || "-";
  }

  async function loadData() {
    const lead = getSelectedLead();
    if (!lead?.id) {
      window.AppActions.showStatus("outreachActionStatus", "Select a lead first from Dashboard.", true);
      return;
    }

    const detail = await window.AppActions.api(`/api/leads/${lead.id}`);
    state.detail = detail;

    const related = Array.isArray(detail.related_emails) ? detail.related_emails : [];
    const recipients = Array.isArray(detail.recipients) ? detail.recipients : [];

    renderMeta();
    renderTemplateOptions(related);
    renderRecipients(recipients);
    byId("outreachTemplateCount").textContent = `${related.length} template${related.length === 1 ? "" : "s"}`;

    const defaultManual = recipients.length ? "" : (lead.contact_email || "");
    byId("outreachManualRecipient").value = defaultManual;
    state.defaults.manualRecipient = defaultManual;

    fillFromSelectedTemplate();
    window.AppActions.showStatus("outreachActionStatus", "Outreach workspace ready.");
  }

  function resetForm() {
    byId("outreachSubject").value = state.defaults.subject || "";
    byId("outreachBody").value = state.defaults.body || "";
    byId("outreachManualRecipient").value = state.defaults.manualRecipient || "";
    window.AppActions.showStatus("outreachActionStatus", "Outreach draft reset.");
  }

  async function sendOutreach() {
    const lead = getSelectedLead();
    const recipient = selectedRecipient();
    const subject = (byId("outreachSubject").value || "").trim();
    const body = ensureSignature((byId("outreachBody").value || "").trim(), lead?.product_description || "");

    if (!recipient) {
      throw new Error("Recipient email is required.");
    }
    if (!subject) {
      throw new Error("Subject is required.");
    }
    if (!body) {
      throw new Error("Body is required.");
    }

    if (!lead?.id) {
      throw new Error("Select a lead first from Dashboard.");
    }

    const data = await window.AppActions.api("/api/sender/outreach", {
      method: "POST",
      body: JSON.stringify({
        lead_id: lead.id,
        to_email: recipient,
        subject,
        body,
      }),
    });

    byId("outreachBody").value = body;
    const result = data.result || {};
    if (result.delivery_mode === "gmail_draft" && result.status === "draft") {
      window.AppActions.showStatus("outreachActionStatus", `New thread draft created for ${recipient}.`);
    } else if (result.delivery_mode === "sendgrid" && result.status === "sent") {
      window.AppActions.showStatus("outreachActionStatus", `New thread email sent to ${recipient}.`);
    } else {
      window.AppActions.showStatus("outreachActionStatus", `Outreach created for ${recipient}.`);
    }
    await window.AppActions.loadLogs();
    await window.AppActions.refreshNotifications();
  }

  function init() {
    const navButtons = document.querySelectorAll(".nav-section[data-target='senderOutreachSection']");
    navButtons.forEach((btn) => {
      btn.addEventListener("click", () => {
        loadData().catch((err) => window.AppActions.showStatus("outreachActionStatus", err.message, true));
      });
    });

    byId("outreachTemplateSelect")?.addEventListener("change", () => {
      fillFromSelectedTemplate();
    });

    byId("outreachKnownRecipient")?.addEventListener("change", () => {
      byId("outreachManualRecipient").value = "";
    });

    byId("btnOutreachReset")?.addEventListener("click", resetForm);

    byId("btnOutreachSend")?.addEventListener("click", () => {
      sendOutreach().catch((err) => window.AppActions.showStatus("outreachActionStatus", err.message, true));
    });
  }

  return { init, loadData };
})();
