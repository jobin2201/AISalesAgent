from datetime import datetime, timezone
from typing import Dict, List

from fastapi import FastAPI, HTTPException, Request

from app.data.database import agent_runs_collection, init_db, leads_collection
from app.sample_data import get_seed_leads
from app.server.schemas import (
    AgentRunResponse,
    LeadCreate,
    LeadCreateResponse,
    LeadView,
    ReplyHandlerResponse,
    ReplyReceived,
    SeedResponse,
)
from app.services.ai_service import (
    assess_bant,
    build_sequence_steps,
    choose_next_action,
    generate_email,
    lead_status_from_score,
    score_icp,
)
from app.services.conversation_memory import get_conversation_history, log_inbound
from app.services.email_service import send_email
from app.services.reply_analyzer import classify_reply_sentiment, decide_agent_next_action, extract_objections
from app.spam.dns_auth import check_dns_authentication, dns_auto_setup_possible
from app.spam.feedback import process_sendgrid_events
from app.spam.monitoring import build_deliverability_report

app = FastAPI(title="AI Sales Agent", version="0.1.0")


def _serialize_lead(doc: Dict) -> LeadView:
    fallback_ts = datetime.now(timezone.utc)
    return LeadView(
        id=str(doc["_id"]),
        name=doc["name"],
        email=doc["email"],
        company=doc["company"],
        title=doc.get("title"),
        industry=doc.get("industry"),
        company_size=doc.get("company_size"),
        country=doc.get("country"),
        source=doc.get("source", "crm"),
        intent_signals=doc.get("intent_signals", []),
        status=doc.get("status", "new"),
        lead_score=doc.get("lead_score", 0),
        icp_score=doc.get("icp_score", 0),
        bant_score=doc.get("bant_score", 0),
        created_at=doc.get("created_at", doc.get("updated_at", fallback_ts)),
        updated_at=doc.get("updated_at", doc.get("created_at", fallback_ts)),
    )


def _process_incoming_reply(lead_email: str, prospect_reply: str, subject: str = "Prospect reply") -> ReplyHandlerResponse:
    lead = leads_collection.find_one({"email": lead_email})
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    log_inbound(
        lead_email=lead_email,
        from_email=lead_email,
        subject=subject,
        body=prospect_reply,
    )

    sentiment = classify_reply_sentiment(prospect_reply)
    objections = extract_objections(prospect_reply)
    next_action, action_detail = decide_agent_next_action(prospect_reply, lead.get("lead_score", 0))

    response_email = None
    if next_action == "send_objection_handler":
        if "pricing" in objections:
            response_email = generate_email(lead, lead.get("lead_score", 0), email_type="price_objection")
        elif "timing" in objections:
            response_email = generate_email(lead, lead.get("lead_score", 0), email_type="timing_objection")
        else:
            response_email = generate_email(lead, lead.get("lead_score", 0), email_type="case_study")
    elif next_action == "send_proof":
        response_email = generate_email(lead, lead.get("lead_score", 0), email_type="case_study")
    elif next_action == "propose_meeting":
        response_email = generate_email(lead, lead.get("lead_score", 0), email_type="meeting_proposal")

    if response_email:
        send_email(
            to_email=lead_email,
            subject=response_email["subject"],
            body=response_email["body"],
            lead_email=lead_email,
        )

    if sentiment == "positive":
        new_status = "meeting_booked" if next_action == "propose_meeting" else "qualified"
    elif sentiment == "negative":
        new_status = "disqualified"
    else:
        new_status = lead.get("status", "nurture")

    leads_collection.update_one(
        {"_id": lead["_id"]},
        {"$set": {"status": new_status, "updated_at": datetime.now(timezone.utc)}},
    )

    return ReplyHandlerResponse(
        lead_email=lead_email,
        sentiment=sentiment,
        objections=objections,
        next_action=next_action,
        action_detail=action_detail,
        agent_response_email=response_email,
    )


@app.on_event("startup")
def startup_event() -> None:
    init_db()


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.post("/lead", response_model=LeadCreateResponse)
def create_lead(lead: LeadCreate) -> LeadCreateResponse:
    now = datetime.now(timezone.utc)
    lead_data = lead.model_dump()

    icp_score = score_icp(lead_data)
    bant_score, _ = assess_bant(lead_data)
    lead_score = round((icp_score * 0.6) + (bant_score * 0.4))
    status = lead_status_from_score(lead_score)

    stored_doc = {
        **lead_data,
        "icp_score": icp_score,
        "bant_score": bant_score,
        "lead_score": lead_score,
        "status": status,
        "updated_at": now,
    }

    existing = leads_collection.find_one({"email": lead_data["email"]})
    if existing:
        leads_collection.update_one({"_id": existing["_id"]}, {"$set": stored_doc})
        saved = leads_collection.find_one({"_id": existing["_id"]})
    else:
        stored_doc["created_at"] = now
        insert_result = leads_collection.insert_one(stored_doc)
        saved = leads_collection.find_one({"_id": insert_result.inserted_id})

    if not saved:
        raise HTTPException(status_code=500, detail="Lead save failed")

    email_payload = generate_email(saved, lead_score)
    send_email(
        to_email=saved["email"],
        subject=email_payload["subject"],
        body=email_payload["body"],
        lead_email=saved["email"],
    )

    return LeadCreateResponse(
        lead=_serialize_lead(saved),
        first_touch_email=email_payload,
        sequence_steps=build_sequence_steps(),
    )


@app.get("/leads", response_model=List[LeadView])
def list_leads() -> List[LeadView]:
    docs = leads_collection.find().sort("updated_at", -1)
    return [_serialize_lead(doc) for doc in docs]


@app.post("/seed/basic", response_model=SeedResponse)
def seed_basic_data() -> SeedResponse:
    now = datetime.now(timezone.utc)
    inserted = 0
    updated = 0

    for item in get_seed_leads():
        icp_score = score_icp(item)
        bant_score, _ = assess_bant(item)
        lead_score = round((icp_score * 0.6) + (bant_score * 0.4))
        status = lead_status_from_score(lead_score)

        payload = {
            **item,
            "icp_score": icp_score,
            "bant_score": bant_score,
            "lead_score": lead_score,
            "status": status,
            "updated_at": now,
        }

        existing = leads_collection.find_one({"email": item["email"]})
        if existing:
            leads_collection.update_one({"_id": existing["_id"]}, {"$set": payload})
            updated += 1
        else:
            payload["created_at"] = now
            leads_collection.insert_one(payload)
            inserted += 1

    total = leads_collection.count_documents({})
    return SeedResponse(inserted=inserted, updated=updated, total_in_db=total)


@app.post("/agent/run/{lead_email}", response_model=AgentRunResponse)
def run_agent(lead_email: str) -> AgentRunResponse:
    lead = leads_collection.find_one({"email": lead_email})
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    decision, detail = choose_next_action(lead)
    agent_runs_collection.insert_one(
        {
            "lead_email": lead_email,
            "decision": decision,
            "detail": detail,
            "created_at": datetime.now(timezone.utc),
        }
    )
    return AgentRunResponse(lead_email=lead_email, decision=decision, detail=detail)


@app.post("/reply", response_model=ReplyHandlerResponse)
def handle_prospect_reply(payload: ReplyReceived) -> ReplyHandlerResponse:
    return _process_incoming_reply(
        lead_email=payload.lead_email,
        prospect_reply=payload.prospect_reply,
        subject="Prospect reply",
    )


@app.post("/webhook/sendgrid/inbound")
async def handle_sendgrid_inbound(request: Request) -> Dict[str, str]:
    from_email = ""
    to_email = ""
    subject = "Prospect reply"
    text = ""
    html = ""

    if request.headers.get("content-type", "").startswith("application/json"):
        payload = await request.json()
        from_email = payload.get("from") or payload.get("from_email") or from_email
        to_email = payload.get("to") or payload.get("to_email") or to_email
        subject = payload.get("subject") or subject
        text = payload.get("text") or payload.get("body") or text
    else:
        try:
            form = await request.form()
            from_email = form.get("from", "")
            to_email = form.get("to", "")
            subject = form.get("subject", subject)
            text = form.get("text", "")
            html = form.get("html", "")
        except Exception:
            pass

    lead_email = (from_email or "").strip().lower()
    if "<" in lead_email and ">" in lead_email:
        lead_email = lead_email.split("<")[-1].split(">")[0].strip().lower()

    body = (text or html or "").strip()
    if not lead_email:
        raise HTTPException(status_code=400, detail="Missing from_email in webhook payload")
    if not body:
        raise HTTPException(status_code=400, detail="Missing message body in webhook payload")

    _process_incoming_reply(lead_email=lead_email, prospect_reply=body, subject=subject)
    return {
        "status": "processed",
        "lead_email": lead_email,
        "target_mailbox": to_email,
    }


@app.post("/webhook/sendgrid/events")
async def handle_sendgrid_events(request: Request) -> Dict[str, int]:
    payload = await request.json()
    if isinstance(payload, dict):
        events = [payload]
    elif isinstance(payload, list):
        events = payload
    else:
        raise HTTPException(status_code=400, detail="Invalid SendGrid events payload")

    result = process_sendgrid_events(events)
    return {
        "processed": int(result.get("inserted", 0)),
        "suppressed": int(result.get("suppressed", 0)),
    }


@app.get("/deliverability/report")
def deliverability_report(hours: int = 24) -> Dict[str, object]:
    return build_deliverability_report(hours=hours)


@app.get("/deliverability/dns-auth")
def deliverability_dns_auth(sender_email: str) -> Dict[str, object]:
    return check_dns_authentication(sender_email)


@app.get("/deliverability/dns-auto-setup-capability")
def deliverability_dns_auto_setup_capability() -> Dict[str, object]:
    return dns_auto_setup_possible()


@app.get("/advanced/blueprint")
def advanced_blueprint() -> Dict[str, List[str]]:
    return {
        "phase_1_basic": [
            "CRM trigger (manual API call)",
            "ICP+BANT lead scoring",
            "3-step email sequence",
            "MongoDB lead + message persistence",
            "Basic agent action decision",
        ],
        "phase_2": [
            "Sentiment-based response adaptation",
            "Objection handling library",
            "Rep briefing generation",
            "Approval queue",
        ],
        "phase_3": [
            "Vector memory for account history",
            "3rd-party enrichment fusion",
            "Pattern learning from conversion outcomes",
        ],
        "phase_4": [
            "RLHF feedback loop",
            "Multi-provider LLM routing",
            "WhatsApp/SMS channel",
            "Advanced observability dashboard",
        ],
    }


@app.get("/conversation/{lead_email}")
def get_conversation(lead_email: str) -> Dict:
    history = get_conversation_history(lead_email)
    for msg in history:
        if "_id" in msg:
            msg["_id"] = str(msg["_id"])
        if "created_at" in msg:
            msg["created_at"] = msg["created_at"].isoformat()
    return {
        "lead_email": lead_email,
        "message_count": len(history),
        "messages": history,
    }
