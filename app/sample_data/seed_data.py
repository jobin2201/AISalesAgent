from datetime import datetime, timezone
from typing import Dict, List

from app.data.database import init_db, leads_collection
from app.services.ai_service import assess_bant, lead_status_from_score, score_icp


def get_seed_leads() -> List[Dict]:
    return [
        {
            "name": "Alice",
            "email": "alice@test.com",
            "company": "StartupX",
            "title": "Head of Sales",
            "industry": "SaaS",
            "company_size": 120,
            "country": "India",
            "source": "crm",
            "intent_signals": ["pricing_page", "demo_request"],
            "notes": "Need automation this month. Budget approved.",
        },
        {
            "name": "Rahul Mehta",
            "email": "rahul@northstartech.com",
            "company": "NorthStar Tech",
            "title": "VP Revenue",
            "industry": "Software",
            "company_size": 350,
            "country": "India",
            "source": "website",
            "intent_signals": ["comparison_page", "contact_sales"],
            "notes": "Reviewing alternatives in Q2.",
        },
        {
            "name": "Meera Jain",
            "email": "meera@retailflux.io",
            "company": "RetailFlux",
            "title": "Founder",
            "industry": "Ecommerce",
            "company_size": 45,
            "country": "UAE",
            "source": "webinar",
            "intent_signals": ["pricing_page"],
            "notes": "Looking to improve follow-up conversion urgently.",
        },
        {
            "name": "Daniel Park",
            "email": "daniel@medinova.ai",
            "company": "MediNova",
            "title": "Sales Manager",
            "industry": "HealthTech",
            "company_size": 220,
            "country": "Singapore",
            "source": "crm",
            "intent_signals": ["docs_page"],
            "notes": "Exploring in next quarter.",
        },
        {
            "name": "Priya S",
            "email": "priya@finbridge.co",
            "company": "FinBridge",
            "title": "Director GTM",
            "industry": "FinTech",
            "company_size": 500,
            "country": "India",
            "source": "referral",
            "intent_signals": ["pricing_page", "trial_signup"],
            "notes": "Need rollout ASAP.",
        },
    ]


def seed() -> None:
    init_db()
    now = datetime.now(timezone.utc)
    inserted = 0
    updated = 0

    for item in get_seed_leads():
        icp_score = score_icp(item)
        bant_score, _ = assess_bant(item)
        lead_score = round((icp_score * 0.6) + (bant_score * 0.4))
        payload = {
            **item,
            "icp_score": icp_score,
            "bant_score": bant_score,
            "lead_score": lead_score,
            "status": lead_status_from_score(lead_score),
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
    print({"inserted": inserted, "updated": updated, "total": total})


if __name__ == "__main__":
    seed()
