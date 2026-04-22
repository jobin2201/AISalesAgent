"""
Streamlit Dashboard for AI Lead Generation Engine
Run: streamlit run dashboard.py
"""

import streamlit as st
import json
import pandas as pd
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from app import GroqClient, CriteriaAgent, ScraperAgent, ScoringAgent, export_leads
from store_json_to_mongo import import_json_exports

# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="AI Lead Engine",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ── CSS ────────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&display=swap');
html, body, [class*="css"] { font-family: 'Space Grotesk', sans-serif; }
.stApp { background: #0a0e1a; color: #e2e8f0; }

.section-label {
    font-size: 0.70rem; font-weight: 700; letter-spacing: 1.5px;
    text-transform: uppercase; color: #475569; margin: 16px 0 4px;
}
.metric-card {
    background: linear-gradient(135deg, #1a2035 0%, #0d1525 100%);
    border: 1px solid #2a3550; border-radius: 12px; padding: 20px; text-align: center;
}
.metric-value { font-size: 2.4rem; font-weight: 700; color: #60a5fa; }
.metric-label { font-size: 0.75rem; color: #94a3b8; text-transform: uppercase; letter-spacing: 1px; }
.lead-card {
    background: #111827; border: 1px solid #1e2d45;
    border-radius: 12px; padding: 16px; margin-bottom: 10px;
    border-left: 4px solid #334155;
}
.lead-card.high   { border-left-color: #ef4444; }
.lead-card.medium { border-left-color: #f59e0b; }
.lead-card.low    { border-left-color: #22c55e; }
.badge { display:inline-block; padding:3px 10px; border-radius:20px; font-size:0.72rem; font-weight:700; }
.badge-high   { background:#7f1d1d; color:#fca5a5; }
.badge-medium { background:#78350f; color:#fcd34d; }
.badge-low    { background:#14532d; color:#86efac; }
.chip        { display:inline-block; background:#1e3a5f; color:#93c5fd; padding:2px 8px; border-radius:4px; font-size:0.70rem; margin:2px; }
.chip-green  { background:#14532d; color:#86efac; }
</style>
""", unsafe_allow_html=True)

# ── Sidebar ────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## ⚙️ Groq Config")
    groq_key   = st.text_input("API Key",   value=os.getenv("GROQ_API_KEY",  ""), type="password")
    groq_model = st.text_input("Model",     value=os.getenv("GROQ_MODEL",    "llama3-70b-8192"))
    os.environ["GROQ_API_KEY"]  = groq_key
    os.environ["GROQ_MODEL"]    = groq_model
    st.markdown("---")
    st.markdown("### 🔍 Filter Results")
    min_score       = st.slider("Min Score", 0, 100, 40)
    priority_filter = st.multiselect("Priority", ["HIGH", "MEDIUM", "LOW"], default=["HIGH", "MEDIUM", "LOW"])

# ── Header ─────────────────────────────────────────────────────────────────────
st.markdown("# 🎯 AI Lead Generation Engine")
st.markdown("*Describe your product + target → get scored, enriched B2B leads automatically*")
st.markdown("---")

# ══════════════════════════════════════════════════════════════════════════════
# INPUT FORM
# ══════════════════════════════════════════════════════════════════════════════
with st.expander("📋 Lead Generation Brief", expanded=True):

    # ── Product description ────────────────────────────────────────────────
    st.markdown('<div class="section-label">🏷️ Product / Service Description <span style="color:#ef4444">*</span></div>', unsafe_allow_html=True)
    product_desc = st.text_area(
        label="pd", label_visibility="collapsed",
        placeholder="Describe your product — what it does, who it helps, what problems it solves, key features...",
        height=120, key="product_desc"
    )

    # ── Use case + ICP ────────────────────────────────────────────────────
    ca, cb = st.columns(2)
    with ca:
        st.markdown('<div class="section-label">🎯 Primary Use Case / Pain Solved</div>', unsafe_allow_html=True)
        use_case = st.text_input(
            label="uc", label_visibility="collapsed",
            placeholder="e.g. Automate manual expense reimbursements for field teams",
            key="use_case"
        )
    with cb:
        st.markdown('<div class="section-label">👤 Ideal Customer Profile (ICP)</div>', unsafe_allow_html=True)
        ideal_customer = st.text_input(
            label="icp", label_visibility="collapsed",
            placeholder="e.g. Mid-size pharma/FMCG with 50+ field reps, manual processes",
            key="ideal_customer"
        )

    # ── Industries + Geography ────────────────────────────────────────────
    cc, cd = st.columns(2)
    with cc:
        st.markdown('<div class="section-label">🏭 Industry Focus <span style="color:#475569">(leave blank = AI decides)</span></div>', unsafe_allow_html=True)
        industry_focus = st.multiselect(
            label="if", label_visibility="collapsed",
            options=[
                "Pharma / Life Sciences", "FMCG / Consumer Goods", "Insurance",
                "Manufacturing", "Logistics / Supply Chain", "Retail",
                "IT / SaaS", "BFSI / Finance", "Healthcare", "Education",
                "Real Estate", "Telecom", "Energy / Utilities", "Auto / EV",
                "Agri / Food", "Media & Entertainment"
            ],
            placeholder="Pick industries...",
            key="industry_focus"
        )
    with cd:
        st.markdown('<div class="section-label">🌍 Target Geography <span style="color:#475569">(leave blank = AI decides)</span></div>', unsafe_allow_html=True)
        target_geography = st.multiselect(
            label="tg", label_visibility="collapsed",
            options=["India", "Southeast Asia", "Middle East", "USA", "UK",
                     "Europe", "Africa", "APAC", "LATAM", "Global"],
            placeholder="Pick regions...",
            key="target_geography"
        )

    # ── Company size + Decision makers ────────────────────────────────────
    ce, cf = st.columns(2)
    with ce:
        st.markdown('<div class="section-label">🏢 Target Company Size</div>', unsafe_allow_html=True)
        size_options = ["Any", "1–10", "11–50", "51–200", "201–500", "501–1000", "1000–5000", "5000+"]
        company_size = st.select_slider(
            label="cs", label_visibility="collapsed",
            options=size_options,
            value=("11–50", "501–1000"),
            key="company_size"
        )
    with cf:
        st.markdown('<div class="section-label">👔 Key Decision Maker Roles</div>', unsafe_allow_html=True)
        target_roles = st.multiselect(
            label="tr", label_visibility="collapsed",
            options=[
                "CEO / Founder", "CFO / Finance Head", "COO / Operations Head",
                "HR Head / CHRO", "CTO / IT Head", "VP Sales / Sales Head",
                "Procurement Head", "Admin / Office Head", "Business Owner / MD"
            ],
            placeholder="Who approves this purchase?",
            key="target_roles"
        )

    # ── Competitors + Exclude ─────────────────────────────────────────────
    cg, ch = st.columns(2)
    with cg:
        st.markdown('<div class="section-label">⚔️ Known Competitors <span style="color:#475569">(AI will target their customers)</span></div>', unsafe_allow_html=True)
        competitors = st.text_input(
            label="comp", label_visibility="collapsed",
            placeholder="e.g. Concur, Expensify, Zoho Expense, SAP Concur",
            key="competitors"
        )
    with ch:
        st.markdown('<div class="section-label">🚫 Exclude Keywords / Sectors</div>', unsafe_allow_html=True)
        exclude_keywords = st.text_input(
            label="excl", label_visibility="collapsed",
            placeholder="e.g. startups, non-profit, government, solo founders",
            key="exclude_keywords"
        )

    # ── Buying signals hint + search settings ─────────────────────────────
    ci, cj = st.columns(2)
    with ci:
        st.markdown('<div class="section-label">📡 Custom Buying Signals to Watch</div>', unsafe_allow_html=True)
        custom_signals = st.text_input(
            label="csig", label_visibility="collapsed",
            placeholder="e.g. hiring sales reps, expanding to new cities, series B funding",
            key="custom_signals"
        )
    with cj:
        st.markdown('<div class="section-label">💰 Typical Deal Size / Budget Range</div>', unsafe_allow_html=True)
        deal_size = st.selectbox(
            label="ds", label_visibility="collapsed",
            options=["Not specified", "< ₹1L / month", "₹1L–5L / month",
                     "₹5L–20L / month", "₹20L+ / month",
                     "< $1K / month", "$1K–5K / month", "$5K–20K / month", "$20K+ / month"],
            key="deal_size"
        )

    # ── Sender info for email drafting ────────────────────────────────────
    st.markdown('<div class="section-label">✉️ Your Details (for Email Drafts)</div>', unsafe_allow_html=True)
    cs1, cs2 = st.columns(2)
    with cs1:
        sender_name = st.text_input(
            label="sn", label_visibility="collapsed",
            placeholder="Your name — e.g. Rahul Sharma",
            key="sender_name"
        )
    with cs2:
        sender_company = st.text_input(
            label="sc", label_visibility="collapsed",
            placeholder="Your company name — e.g. DigiExpense",
            key="sender_company"
        )

    # ── Search tuning ─────────────────────────────────────────────────────
    st.markdown('<div class="section-label">⚙️ Search Settings</div>', unsafe_allow_html=True)
    ck, cl, cm = st.columns(3)
    with ck:
        max_queries = st.number_input("Max Search Queries", min_value=3, max_value=15, value=6, key="max_queries")
    with cl:
        results_per_query = st.number_input("Results per Query", min_value=3, max_value=10, value=5, key="rpq")
    with cm:
        min_score_threshold = st.number_input("Min Lead Score Filter", min_value=0, max_value=100, value=40, key="mst")

# ── Completeness indicator + Run button ───────────────────────────────────────
filled = sum([
    bool(product_desc.strip()), bool(use_case), bool(ideal_customer),
    bool(industry_focus), bool(target_geography), bool(target_roles),
    bool(competitors), bool(exclude_keywords), bool(custom_signals)
])
quality = "🟢 Great brief" if filled >= 5 else "🟡 Good" if filled >= 3 else "🔴 Minimal — add more context for better leads"
st.caption(f"{quality} &nbsp;·&nbsp; {filled}/9 fields filled")

col_btn, col_hint = st.columns([1, 5])
with col_btn:
    run_btn = st.button("🚀 Find Leads", type="primary", use_container_width=True)
with col_hint:
    st.caption("AI will analyze your brief, generate search queries, scrape company pages, and score every lead.")

# ══════════════════════════════════════════════════════════════════════════════
# RUN PIPELINE
# ══════════════════════════════════════════════════════════════════════════════
if run_btn and product_desc.strip():

    extra_context = {}
    if use_case:           extra_context["use_case"]          = use_case.strip()
    if ideal_customer:     extra_context["ideal_customer"]    = ideal_customer.strip()
    if industry_focus:     extra_context["industry_focus"]    = ", ".join(industry_focus)
    if target_geography:   extra_context["target_geography"]  = ", ".join(target_geography)
    if company_size != ("Any", "Any"):
        extra_context["company_size"] = f"{company_size[0]} to {company_size[1]} employees"
    if target_roles:       extra_context["target_roles"]      = ", ".join(target_roles)
    if competitors:        extra_context["competitors"]       = competitors.strip()
    if exclude_keywords:   extra_context["exclude_keywords"]  = exclude_keywords.strip()
    if custom_signals:     extra_context["custom_signals"]    = custom_signals.strip()
    if deal_size != "Not specified":
        extra_context["deal_size"] = deal_size
    extra_context["max_queries"]       = max_queries
    extra_context["results_per_query"] = results_per_query

    progress_bar = st.progress(0)
    status = st.empty()

    with st.spinner(""):
        status.markdown("🧠 **Layer 1:** Analyzing brief & generating search criteria...")
        progress_bar.progress(8)
        llm = GroqClient()
        criteria = CriteriaAgent(llm).generate(product_desc, extra_context=extra_context)
        progress_bar.progress(25)

        status.markdown("🔍 **Layer 2:** Scraping web for matching companies...")
        raw_results = ScraperAgent().run(criteria)
        progress_bar.progress(65)

        status.markdown("🎯 **Layer 3:** Scoring & enriching leads with AI...")
        leads = ScoringAgent(llm).run(raw_results, criteria, product_desc)
        progress_bar.progress(70)

        status.markdown("🔎 **Layer 4:** Finding executive contacts...")
        from app import ExecContactFinderAgent
        leads = ExecContactFinderAgent(ScraperAgent()).run(leads)
        progress_bar.progress(85)

        status.markdown("✉️ **Layer 5:** Drafting personalised outreach emails...")
        from app import EmailDraftAgent
        _sname = sender_name.strip() or "Sales Team"
        _scomp = sender_company.strip() or "Our Company"
        leads = EmailDraftAgent(llm).run(leads, product_desc, _sname, _scomp)
        progress_bar.progress(95)

        leads = [l for l in leads if l.total_score >= min_score_threshold]
        json_path, csv_path, emails_path = export_leads(leads)
        mongo_result = import_json_exports(
            product_description=product_desc.strip(),
            lead_file=json_path,
            email_file=emails_path,
        )
        progress_bar.progress(100)
        status.markdown(
            f"✅ **Done!** Found **{len(leads)}** qualified leads. "
            f"Mongo saved leads={mongo_result['leads_inserted'] + mongo_result['leads_updated']}, "
            f"emails={mongo_result['emails_inserted'] + mongo_result['emails_updated']}."
        )

    st.session_state.update({
        "leads": leads, "criteria": criteria,
        "csv_path": csv_path, "json_path": json_path,
        "emails_path": emails_path,
        "extra": extra_context,
        "mongo_result": mongo_result,
    })

elif run_btn:
    st.warning("Please enter a product description.")

# ══════════════════════════════════════════════════════════════════════════════
# RESULTS
# ══════════════════════════════════════════════════════════════════════════════
if "leads" in st.session_state and st.session_state["leads"]:
    leads    = st.session_state["leads"]
    criteria = st.session_state["criteria"]
    extra    = st.session_state.get("extra", {})

    filtered = [l for l in leads if l.total_score >= min_score and l.priority in priority_filter]

    st.markdown("---")

    # Metrics
    high_c = len([l for l in leads if l.priority == "HIGH"])
    med_c  = len([l for l in leads if l.priority == "MEDIUM"])
    avg_s  = round(sum(l.total_score for l in leads) / len(leads), 1) if leads else 0

    c1, c2, c3, c4 = st.columns(4)
    for col, val, label, color in [
        (c1, len(leads), "Total Leads", "#60a5fa"),
        (c2, high_c, "🔴 High Priority", "#ef4444"),
        (c3, med_c,  "🟡 Medium Priority", "#f59e0b"),
        (c4, avg_s,  "Avg Score /100", "#60a5fa"),
    ]:
        with col:
            st.markdown(f'<div class="metric-card"><div class="metric-value" style="color:{color}">{val}</div><div class="metric-label">{label}</div></div>', unsafe_allow_html=True)

    st.markdown("")

    # AI Criteria
    with st.expander("🧠 AI-Generated Search Criteria", expanded=False):
        xa, xb = st.columns(2)
        with xa:
            st.markdown("**Industries**")
            for i in criteria.target_industries: st.markdown(f"- {i}")
            st.markdown(f"\n**Company Size:** {criteria.company_size_range}")
            st.markdown("\n**Decision Makers**")
            for d in criteria.decision_makers: st.markdown(f"- {d}")
        with xb:
            st.markdown("**Buying Signals**")
            for s in criteria.buying_signals: st.markdown(f"- ✅ {s}")
            st.markdown("**Pain Points**")
            for p in criteria.pain_points: st.markdown(f"- 🎯 {p}")
            st.markdown("**Queries Run**")
            for q in criteria.search_queries: st.markdown(f"- 🔍 `{q}`")

    # Brief summary
    if extra:
        with st.expander("📋 Brief You Provided", expanded=False):
            disp = {k: v for k, v in extra.items() if k not in ("max_queries", "results_per_query")}
            cols = st.columns(3)
            for i, (k, v) in enumerate(disp.items()):
                with cols[i % 3]:
                    st.markdown(f"**{k.replace('_',' ').title()}**")
                    st.code(str(v), language=None)

    # Lead display
    st.markdown(f"### 📋 Leads — {len(filtered)} shown")
    tab1, tab2, tab3 = st.tabs(["🃏 Cards", "📊 Table", "✉️ Email Drafts"])

    with tab1:
        if not filtered:
            st.info("No leads match current filters. Lower the minimum score in the sidebar.")
        for lead in filtered:
            pc = lead.priority.lower()
            sc = "#ef4444" if lead.total_score >= 75 else "#f59e0b" if lead.total_score >= 55 else "#22c55e"
            sigs = "".join(f'<span class="chip">{s[:40]}</span>' for s in list(set(lead.signals_found))[:5]) \
                   or '<span style="color:#334155">No signals detected</span>'
            jobs = "".join(f'<span class="chip chip-green">{j[:30]}</span>' for j in lead.job_titles_found[:3])
            li   = f'<a href="{lead.linkedin_urls[0]}" target="_blank" style="color:#818cf8;font-size:0.82rem">LinkedIn ↗</a>' \
                   if lead.linkedin_urls else ""

            def minibar(v, c):
                return f'<div style="display:inline-flex;align-items:center;gap:4px;margin-right:8px"><span style="font-size:0.65rem;color:#475569;width:60px">{c}</span><div style="width:50px;background:#1e2d45;border-radius:2px;height:4px"><div style="width:{int(v*10)}%;background:{"#ef4444" if c=="Signal" else "#f59e0b" if c=="Industry" else "#22c55e" if c=="Size" else "#818cf8"};height:4px;border-radius:2px"></div></div><span style="font-size:0.65rem;color:#64748b">{v:.0f}</span></div>'

            st.markdown(f"""
            <div class="lead-card {pc}">
              <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:6px">
                <div>
                  <span style="font-size:1.05rem;font-weight:700">{lead.company_name}</span>
                  <span style="color:#334155;margin-left:8px;font-size:0.80rem">{lead.industry}</span>
                </div>
                <div style="text-align:right">
                  <span class="badge badge-{pc}">{lead.priority}</span>
                  <div style="font-size:1.5rem;font-weight:700;color:{sc};line-height:1.1">{lead.total_score}<span style="font-size:0.72rem;color:#334155">/100</span></div>
                </div>
              </div>
              <div style="color:#94a3b8;font-size:0.83rem;margin:6px 0">{lead.description}</div>
              <div style="margin:6px 0">{sigs}</div>
              {f'<div style="margin:4px 0">{jobs}</div>' if jobs else ''}
              <div style="margin-top:10px;display:flex;flex-wrap:wrap;gap:16px;font-size:0.80rem;color:#475569">
                <div>{minibar(lead.signal_strength,"Signal")}{minibar(lead.industry_fit,"Industry")}{minibar(lead.size_fit,"Size")}{minibar(lead.engagement_score,"Engage")}</div>
                <div>
                  👔 {", ".join(lead.decision_makers_found[:2]) or "—"} &nbsp;
                  📧 {lead.emails_found[0] if lead.emails_found else "—"} &nbsp;
                  {li}
                </div>
                <div>🌐 <a href="{lead.website}" target="_blank" style="color:#60a5fa">{lead.website[:50]}</a></div>
              </div>
              <div style="margin-top:8px;background:#0a0e1a;border-radius:6px;padding:8px 12px;font-size:0.81rem;color:#a5b4fc">
                ⚡ {lead.recommended_action}
              </div>
            </div>""", unsafe_allow_html=True)

    with tab2:
        df = pd.DataFrame([{
            "Company":         l.company_name,
            "Industry":        l.industry,
            "Score":           l.total_score,
            "Priority":        l.priority,
            "Signal Str.":     l.signal_strength,
            "Industry Fit":    l.industry_fit,
            "Size Fit":        l.size_fit,
            "Engagement":      l.engagement_score,
            "Signals":         "; ".join(list(set(l.signals_found))[:4]),
            "Decision Makers": ", ".join(l.decision_makers_found[:2]),
            "Email":           l.emails_found[0] if l.emails_found else "",
            "LinkedIn":        l.linkedin_urls[0] if l.linkedin_urls else "",
            "Jobs":            ", ".join(l.job_titles_found[:2]),
            "Action":          l.recommended_action,
            "URL":             l.website,
        } for l in filtered])
        st.dataframe(df.sort_values("Score", ascending=False), use_container_width=True, height=500)

    with tab3:
        has_emails = any(l.draft_email_subject for l in filtered)
        if not has_emails:
            st.info("No emails drafted yet — make sure your Groq API key is valid and run again.")
        else:
            # Filter leads that have exec contacts or draft
            email_leads = [l for l in filtered if l.draft_email_subject]
            st.caption(f"{len(email_leads)} emails drafted · addresses to publicly available exec contacts")

            for lead in email_leads:
                contacts = lead.exec_contacts or [{"name": "", "title": "Decision Maker", "email": "—"}]
                for contact in contacts:
                    cname  = contact.get("name", "") or "Decision Maker"
                    ctitle = contact.get("title", "") or ""
                    cemail = contact.get("email", "") or "—"

                    pc = lead.priority.lower()
                    sc = "#ef4444" if lead.total_score >= 75 else "#f59e0b" if lead.total_score >= 55 else "#22c55e"

                    with st.container():
                        st.markdown(f"""
                        <div class="lead-card {pc}" style="margin-bottom:16px">
                          <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:6px">
                            <div>
                              <span style="font-weight:700;font-size:1rem">{lead.company_name}</span>
                              <span style="color:#334155;font-size:0.80rem;margin-left:8px">{lead.industry}</span>
                            </div>
                            <div style="text-align:right">
                              <span class="badge badge-{pc}">{lead.priority}</span>
                              <span style="font-size:1.1rem;font-weight:700;color:{sc};margin-left:8px">{lead.total_score}/100</span>
                            </div>
                          </div>
                          <div style="margin-top:8px;font-size:0.82rem;color:#64748b">
                            <b>To:</b> {cname} · {ctitle} · <span style="color:#60a5fa">{cemail}</span>
                          </div>
                          <div style="margin-top:6px;background:#0a0e1a;border-radius:6px;padding:10px 14px">
                            <div style="font-size:0.78rem;color:#475569;text-transform:uppercase;letter-spacing:1px;margin-bottom:4px">Subject</div>
                            <div style="font-size:0.92rem;font-weight:600;color:#e2e8f0">{lead.draft_email_subject}</div>
                          </div>
                          <div style="margin-top:6px;background:#0a0e1a;border-radius:6px;padding:10px 14px">
                            <div style="font-size:0.78rem;color:#475569;text-transform:uppercase;letter-spacing:1px;margin-bottom:6px">Body</div>
                            <div style="font-size:0.84rem;color:#94a3b8;white-space:pre-line;line-height:1.6">{lead.draft_email_body}</div>
                          </div>
                        </div>
                        """, unsafe_allow_html=True)

                    # Copy button per email
                    copy_text = f"To: {cemail}\nSubject: {lead.draft_email_subject}\n\n{lead.draft_email_body}"
                    st.download_button(
                        f"📋 Copy email for {lead.company_name}",
                        data=copy_text,
                        file_name=f"email_{lead.company_name.replace(' ','_')}.txt",
                        mime="text/plain",
                        key=f"copy_{lead.company_name}_{cemail}"
                    )
                    st.markdown("")

    # Downloads
    st.markdown("---")
    d1, d2, d3 = st.columns(3)
    with d1:
        p = st.session_state.get("csv_path")
        if p and os.path.exists(p):
            with open(p, "rb") as f:
                st.download_button("📥 Leads CSV", f, "leads.csv", "text/csv", use_container_width=True)
    with d2:
        p = st.session_state.get("json_path")
        if p and os.path.exists(p):
            with open(p, "rb") as f:
                st.download_button("📥 Leads JSON", f, "leads.json", "application/json", use_container_width=True)
    with d3:
        p = st.session_state.get("emails_path")
        if p and os.path.exists(p):
            with open(p, "rb") as f:
                st.download_button("📥 Emails JSON", f, "emails.json", "application/json", use_container_width=True)

else:
    if "leads" not in st.session_state:
        st.markdown("""
        <div style="text-align:center;padding:60px 20px;color:#334155">
            <div style="font-size:3.5rem">🎯</div>
            <h3 style="color:#475569">Fill in the brief above and click Find Leads</h3>
            <p style="color:#334155;max-width:500px;margin:8px auto">
                The more context you provide — target industry, geography, company size,
                decision makers, competitors — the more precise your leads will be.
            </p>
        </div>""", unsafe_allow_html=True)
