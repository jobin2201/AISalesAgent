# 🚀 AI Sales Agent: Autonomous Outreach & Lead Management

An end-to-end autonomous sales ecosystem designed to ingest leads, score them based on ICP (Ideal Customer Profile) fit, and execute multi-channel outreach. This platform leverages LLMs to handle initial conversations, manage replies, and facilitate human handoff for high-value deal closures.

## 📌 Project Overview

The AI Sales Agent acts as a digital SDR (Sales Development Representative), automating the "top of the funnel." It connects to MongoDB to track lead lifecycles and provides a Streamlit-based dashboard for human oversight.

---
<img width="1583" height="876" alt="1 1" src="https://github.com/user-attachments/assets/54154683-00c3-4311-8022-8e6f7ba97354" />




## 🛠 Architecture & File Structure

### 1. Dashboard & Frontend (`mainapp2.py`)

The primary entry point is a modular Streamlit application organized into functional tabs:

| File | Description |
|------|-------------|
| `lead_overview_tab.py` | Visualizes company data, product-fit narratives, and lead scoring |
| `sender_outreach_tab.py` | Manages the orchestration of outgoing email campaigns |
| `sender_replies_tab.py` | Monitors incoming responses from lead-side senders |
| `customer_reply_tab.py` | Specialized view for direct customer interactions and sentiment analysis |

### 2. Backend Services (`salesagent/app/services/`)

| File | Description |
|------|-------------|
| `email_service.py` | Handles SMTP/API integrations for outreach |
| `db_service.py` | Manages CRUD operations with MongoDB |
| `notification_service.py` | Real-time alerts for new leads or meeting bookings |
| `log_service.py` | Maintains a full audit trail of agent actions |

### 3. Core Logic

- **`app/mainapp2_support/`** — Business logic for lead filtering and data transformation.
- **`lead_gen3/`** — Advanced enrichment and ingestion logic for gathering new prospects.

---

## 🚀 Getting Started

### Prerequisites

- Python 3.10+
- MongoDB Instance
- [LocalXpose](https://localxpose.io/) CLI (for public tunneling)
- Environment variables configured in a `.env` file (API keys for LLMs, DB URI, etc.)

### Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/jobin2201/SalesAgent.git
   cd SalesAgent
   ```

2. Set up a virtual environment:
   ```bash
   python -m venv venv
   .\venv\Scripts\activate
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements/requirements-full.txt
   ```

### Running the Application

To run the full stack with public access, open **two terminals**:

**Terminal 1 — Streamlit Server**
```powershell
cd salesagent
streamlit run mainapp2.py
```
> Local URL: http://127.0.0.1:8501

**Terminal 2 — LocalXpose Tunnel**
```powershell
powershell -ExecutionPolicy Bypass -File "F:/WORK/sales_ai_agent/salesagent/tools/start_localxpose_tunnel.ps1"
```
> This script automates the LocalXpose agent login and opens a public tunnel (e.g., `https://xyz.ap.loclx.io`) to your local port 8501.

---

## 🖥 Using the Dashboard

Once the app is running, open [http://127.0.0.1:8501](http://127.0.0.1:8501) in your browser.

### Step 1 — Sidebar: Authorize accounts
<img width="1915" height="870" alt="1" src="https://github.com/user-attachments/assets/73ec6082-6cc3-409b-9bf8-b811b8f44317" />
<br>

Before using any tab, complete authorization in the **sidebar**:

| Button | Purpose |
|--------|---------|
| **Authorize Gmail Draft Access** | Required if `EMAIL_SEND_MODE=gmail_draft` — enables draft creation in sender mailbox |
| **Authorize Sender Inbox + Calendar** | Grants access to sender mailbox threads and Google Calendar for scheduling |
| **Authorize Customer Reply Mailbox** | Grants access to customer-side mailbox for reading and drafting replies |

> Both reply-account buttons must be authorized for the full threaded reply workflow to function correctly.

### Step 2 — Sidebar: Select filters
<img width="1911" height="830" alt="3" src="https://github.com/user-attachments/assets/eaef4dc0-77d5-40df-9a4f-5ef9b88de000" />
<br>

1. **Product Description** — choose the product to filter leads against *(currently 2 products: `DigiCom`, `DigiExpense`)*
2. **Priority** — filter leads by `HIGH`, `MEDIUM`, or `LOW`
3. **Minimum Score** — set a lead quality threshold
4. **Company Search** *(optional)* — search by company name
5. **Choose Lead** — pick a lead from the filtered list; summary, recipients, and templates load automatically

**SMS Reminders Subsystem**
A new SMS reminder workflow is now active via `sms_reminders.py`:

| Feature | Detail |
|---------|--------|
| Send SMS on meeting booked | Toggle in sidebar |
| Follow-up reminders | Toggle in sidebar |
| Reminder phone input | Recipient number for SMS |
| Twilio diagnostics | Readiness check panel |
| Test SMS button | Fires a test message to verify Twilio config |
| Runtime counters | Tracks sent, failed, and pending reminders |

---

### Step 3 — Tabs in detail

#### 📊 Lead Overview
Displays full company details, ICP fit summary, intent signals, and lead context. Use this tab to assess a lead before outreach.

---

#### 📤 Sender Outreach — Email Workspace
<img width="1575" height="803" alt="4" src="https://github.com/user-attachments/assets/12953021-1b59-4720-9a73-380fe1bb9ab5" />
<br>
This tab handles the first outbound message and any proactive follow-ups from sender to a lead contact.

| Field / Control | What it does |
|-----------------|-------------|
| **Choose stored email template** | Loads a pre-imported template for the selected lead/company. Populates contact context, default subject, and body draft. |
| **Known recipient** | Dropdown of discovered contacts for this lead. Safe and fast — avoids typing errors. |
| **Or type recipient email** | Manual override. Takes precedence over the Known recipient dropdown. Use for new stakeholders not yet in known contacts. |
| **Subject** | Final email subject line. Keep the template default or customise for relevance. |
| **Body** | Main outreach content. Sender signature is automatically normalised to your configured company signature. |

**On clicking Send / Create Draft:**

Guardrails (`pre_send_check`) run first, then behaviour depends on mode:

| Mode | Behaviour |
|------|-----------|
| `gmail_draft` | Creates a Gmail draft — you review and send manually from Gmail |
| `send` | Sends immediately via SendGrid |
| `draft` | Saves to internal review queue — not delivered to any inbox |

> This tab must run first. It starts the thread in the recipient's inbox. Without this outbound message, Customer Reply and Sender Reply won't have real thread context to continue.

---

#### 💬 Customer Reply — Customer Reply Workspace
<img width="1629" height="866" alt="5" src="https://github.com/user-attachments/assets/26e68bc9-eac3-47c9-8b8d-720cd30d0763" />
<br>
This tab simulates or manages the customer-side response to the latest sender message.

**Header context blocks** at the top confirm: sender mailbox, customer mailbox, and active lead. Verify these before drafting.

| Field / Control | What it does |
|-----------------|-------------|
| **Sender Email Snapshot** | Shows the latest sender email found in the customer inbox — From, To, Subject, and full body. This is the message the customer is responding to. |
| **Reply style** | `Guided flow` offers structured response patterns. `Manual reply` gives full freeform control. |
| **Choose reply path** | Selects the intent goal, e.g. *Accept a proposed slot*, *Ask for more clarity*. A description explains what that path achieves. |
| **Pick preferred slot** | Pulls available slot options from sender calendar context. |
| **Preferred duration** | Sets the requested meeting length. |
| **Pick start time inside selected slot** | Allows precise sub-selection within an available window. |

> When slot/duration fields are changed, the subject and body auto-update to include the selected slot and duration — ensuring clear booking intent in the outgoing message.

**On clicking Draft in Customer Gmail:** creates a threaded draft in the customer mailbox addressed to sender. You send it manually from customer Gmail to trigger the next sender-side cycle.

---

#### 📥 Sender Replies — Sender Reply Workspace
This tab handles sender responses to inbound customer messages already received in the sender inbox.

**Header context blocks** confirm: sender mailbox, customer mailbox, and active lead.

**Step 1 — Pick a customer thread:**

| Field / Control | What it does |
|-----------------|-------------|
| **Tracked participants** | Lists all possible contacts tied to this lead. |
| **Choose customer conversation** | Shows latest inbound messages by participant and subject. Selecting one sets the active `thread_id` for reply routing. |
| **Thread Summary** | Displays: Participant email, Meeting State, Current Meeting Contact, and Thread ID — confirming the correct conversation context. |

**Step 2 — Review conversation context:**

| Tab | What it shows |
|-----|---------------|
| **Latest customer message** | Newest inbound content from customer — read before composing. |
| **Last sent sender draft** | Previous sender response — keeps tone and continuity consistent. |

**Step 3 — Compose next sender reply:**

| Field / Control | What it does |
|-----------------|-------------|
| **Reply style** | `NLP-guided reply` auto-suggests a response based on message intent. `Manual reply` gives full control. |
| **Scheduling action detected** | Shows `book_slot` when the parser detects a bookable scheduling intent in the customer reply. |
| **Reply subject / body** | Editable even in guided mode — always review before drafting. |
| **Current sender calendar availability** | Live slot panel — verify open slots before finalising the draft. |
<img width="1490" height="382" alt="image" src="https://github.com/user-attachments/assets/6d6e27cd-590e-43fd-81fc-859d5c85105c" />
<br>


**On clicking Draft in Sender Gmail:** the system applies the sender plan — if a scheduling action exists, it may create/update/cancel a calendar event first, then creates a threaded draft in the sender Gmail inbox. You send it manually from Gmail.

---

#### 🔔 Notifications & Message Log 
The message log sits at the bottom of the dashboard and provides operational visibility across all tabs.

**Notifications**

Shows workflow-level alerts: meeting-booked events, reply triggers, and escalations. Displays *All caught up* when no active items are pending.

**New App Message Log**

Auditable history of every draft created or email sent during the session.

| Column | Description |
|--------|-------------|
| `time` | Timestamp of the action |
| `company` | Lead company the action relates to |
| `to` | Recipient email address |
| `subject` | Email subject line |
| `status` | Outcome — success, failed, etc. |
| `delivery_mode` | Which mode ran: `gmail_draft`, `send`, or `draft` |
| `error` | Error detail if the action failed |

Use this log to verify whether a draft was created vs actually sent, confirm which mode executed, and diagnose any failures.

---

### Step 4 — Standard end-to-end workflow

```
Sender Outreach  →  send draft from sender Gmail
        ↓
Customer Reply   →  draft reply from customer Gmail  →  send manually
        ↓
Sender Replies   →  draft response from sender Gmail  →  send manually
        ↓
        🔁 Repeat until meeting booked or thread closed
```

**Key behaviours to note:**
- All replies use the same **Gmail thread ID** — drafts are created inside the existing conversation, never as a new unrelated email.
- Reply tabs are **draft-first** — you manually send from Gmail in most modes.
- Recipient addresses are resolved from the selected conversation participant, not entered manually.

---
## ⚡ How Email & Calendar Booking Works (Internals)
<img width="1548" height="683" alt="6" src="https://github.com/user-attachments/assets/3a756cf4-7b46-4313-bcf3-e9d2abb4bc9e" />
<br>

### Email flow

#### 1. Sender Outreach — first touch from sender

Triggered by clicking **Send / Create Draft** in the Sender Outreach tab.

**Call chain:**
```
sender_outreach_tab.py
  └─ send_new_lead_message()          # app/mainapp2_support/messaging.py
       ├─ pre_send_check()            # guardrails run before every send
       └─ mode decides behaviour:
            gmail_draft → _create_gmail_draft()    # Gmail API — draft only, send manually
            send        → _send_via_sendgrid()      # SendGrid — sends immediately
            draft       → internal review queue     # not delivered to any inbox
```

#### 2. Customer Reply — customer responds to sender email

Triggered by clicking **Draft in Customer Gmail** in the Customer Reply tab.

**Call chain:**
```
customer_reply_tab.py
  └─ draft_customer_mailbox_reply()  # app/mainapp2_support/customer_flow.py
       └─ create_threaded_draft()    # app/conversation_flow/google_tools.py
                                     # uses Gmail thread_id → reply stays in same thread
```

> Result: a draft is created in the customer mailbox inside the existing thread. You send it manually from customer Gmail.

#### 3. Sender Reply — sender responds to customer

Triggered by clicking **Draft in Sender Gmail** in the Sender Replies tab.

**Call chain:**
```
sender_replies_tab.py
  └─ apply_sender_plan()             # app/mainapp2_support/sender_flow.py
       └─ draft_sender_mailbox_reply()
            └─ create_threaded_draft()  # app/conversation_flow/google_tools.py
                                        # uses Gmail thread_id → reply stays in same thread
```

> Result: a draft is created in the sender mailbox inside the existing thread. You send it manually from sender Gmail. If scheduling intent is detected, calendar booking logic runs before the draft is created.

> Steps 2 and 3 repeat in a loop until the meeting is booked, the lead is disqualified, or the thread is closed.

---

### Calendar booking flow

> **Important:** Meeting booking happens in the **Sender Replies** tab only — not in Sender Outreach.

When the app detects scheduling intent in a customer reply, the following sequence runs on clicking **Draft in Sender Gmail**:

```
1. Customer reply is analysed into a structured plan
   └─ sender_reply_plan()               # app/mainapp2_support/sender_flow.py

2. Scheduling intent detected → plan action set to:
   offer_slots  /  book_slot  /  cancel

3. apply_sender_plan() executes the action
   └─ app/mainapp2_support/sender_flow.py

4. For book_slot:
   ├─ Existing event found → update_calendar_invite()
   └─ No event yet        → create_calendar_invite()
        └─ Google Calendar v3 API  (app/conversation_flow/calendar_tools.py)
             sendUpdates=all  →  invite sent automatically by Google to all attendees

5. Lead record updated in MongoDB with:
   meeting_state, meeting_event_id, meeting_event_link, meeting_start, meeting_end

6. Sender confirmation email drafted in same Gmail thread (draft-first, manual send)
```

**Why authorization matters for booking:**
The **Authorize Sender Inbox + Calendar** button must be clicked because booking requires both Gmail scopes (mailbox access) and Calendar scopes (event create/update). If Calendar scopes are missing, booking is blocked and the app shows a re-authorize prompt.

| Scope group | Required for |
|-------------|-------------|
| `gmail.modify`, `gmail.compose` | Reading threads, creating drafts |
| `calendar.readonly` + write | Fetching available slots, creating/updating calendar invites |

---
