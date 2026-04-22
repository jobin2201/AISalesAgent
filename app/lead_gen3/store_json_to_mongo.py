import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from pymongo import ASCENDING, MongoClient


LEADS_COLLECTION_NAME = "new_leads"
EMAILS_COLLECTION_NAME = "new_emails"


def app_root() -> Path:
    return Path(__file__).resolve().parents[2]


def lead_gen_dir() -> Path:
    return Path(__file__).resolve().parent


def load_environment() -> None:
    load_dotenv(app_root() / ".env")


def mongo_database():
    mongo_uri = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
    mongo_db = os.getenv("MONGODB_DB", "ai_sales_agent")
    client = MongoClient(mongo_uri)
    return client, client[mongo_db]


def ensure_indexes(db) -> None:
    db[LEADS_COLLECTION_NAME].create_index([("record_fingerprint", ASCENDING)], unique=True)
    db[LEADS_COLLECTION_NAME].create_index([("product_description", ASCENDING)])
    db[LEADS_COLLECTION_NAME].create_index([("source_file", ASCENDING)])

    db[EMAILS_COLLECTION_NAME].create_index([("record_fingerprint", ASCENDING)], unique=True)
    db[EMAILS_COLLECTION_NAME].create_index([("product_description", ASCENDING)])
    db[EMAILS_COLLECTION_NAME].create_index([("source_file", ASCENDING)])


def latest_matching_file(prefix: str) -> Path:
    # Search recursively so files inside exports/<timestamp>/ are found too.
    matches = sorted(lead_gen_dir().rglob(f"{prefix}_*.json"))
    if not matches:
        raise FileNotFoundError(f"No files found matching {prefix}_*.json in {lead_gen_dir()}")
    return matches[-1]


def load_json_array(file_path: Path) -> list[dict[str, Any]]:
    data = json.loads(file_path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"Expected a JSON array in {file_path}")
    return [item for item in data if isinstance(item, dict)]


def canonical_product_description(product_description: str) -> str:
    """Scan anywhere in the description for a known product name and return it.

    Searches for "digicom" or "digiexpense" (case-insensitive) in any word of
    the description. If found, returns the canonical capitalised name.
    Otherwise returns the raw description unchanged.
    """
    raw = (product_description or "").strip()
    if not raw:
        return ""
    lowered = raw.lower()
    if "digiexpense" in lowered:
        return "DigiExpense"
    if "digicom" in lowered:
        return "DigiCom"
    return raw


def normalize_product_key(product_description: str) -> str:
    return canonical_product_description(product_description).lower()


def make_fingerprint(namespace: str, payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(f"{namespace}:{raw}".encode("utf-8")).hexdigest()


def prepare_lead_docs(records: list[dict[str, Any]], product_description: str, source_file: Path) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    imported_at = datetime.now(timezone.utc)
    canonical_product = canonical_product_description(product_description)
    product_key = normalize_product_key(product_description)

    for item in records:
        doc = {
            "product_description": canonical_product,
            "product_key": product_key,
            "company_name": item.get("company_name", ""),
            "website": item.get("website", ""),
            "industry": item.get("industry", ""),
            "description": item.get("description", ""),
            "signals_found": item.get("signals_found", []),
            "decision_makers_found": item.get("decision_makers_found", []),
            "source_url": item.get("source_url", ""),
            "emails_found": item.get("emails_found", []),
            "linkedin_urls": item.get("linkedin_urls", []),
            "job_titles_found": item.get("job_titles_found", []),
            "exec_contacts": item.get("exec_contacts", []),
            "draft_email_subject": item.get("draft_email_subject", ""),
            "draft_email_body": item.get("draft_email_body", ""),
            "signal_strength": item.get("signal_strength", 0),
            "industry_fit": item.get("industry_fit", 0),
            "size_fit": item.get("size_fit", 0),
            "engagement_score": item.get("engagement_score", 0),
            "total_score": item.get("total_score", 0),
            "priority": item.get("priority", ""),
            "recommended_action": item.get("recommended_action", ""),
            "scraped_at": item.get("scraped_at", ""),
            "source_file": source_file.name,
            "imported_at": imported_at,
        }
        doc["record_fingerprint"] = make_fingerprint(
            "lead",
            {
                "product_description": canonical_product,
                "source_file": source_file.name,
                "company_name": doc["company_name"],
                "website": doc["website"],
                "draft_email_subject": doc["draft_email_subject"],
            },
        )
        prepared.append(doc)

    return prepared


def prepare_email_docs(records: list[dict[str, Any]], product_description: str, source_file: Path) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    imported_at = datetime.now(timezone.utc)
    canonical_product = canonical_product_description(product_description)
    product_key = normalize_product_key(product_description)

    for item in records:
        doc = {
            "product_description": canonical_product,
            "product_key": product_key,
            "company": item.get("company", ""),
            "industry": item.get("industry", ""),
            "lead_score": item.get("lead_score", 0),
            "priority": item.get("priority", ""),
            "website": item.get("website", ""),
            "contact_name": item.get("contact_name", ""),
            "contact_title": item.get("contact_title", ""),
            "contact_email": item.get("contact_email", ""),
            "email_subject": item.get("email_subject", ""),
            "email_body": item.get("email_body", ""),
            "signals": item.get("signals", ""),
            "recommended_action": item.get("recommended_action", ""),
            "source_file": source_file.name,
            "imported_at": imported_at,
        }
        doc["record_fingerprint"] = make_fingerprint(
            "email",
            {
                "product_description": canonical_product,
                "source_file": source_file.name,
                "company": doc["company"],
                "contact_email": doc["contact_email"],
                "email_subject": doc["email_subject"],
            },
        )
        prepared.append(doc)

    return prepared


def upsert_many(collection, documents: list[dict[str, Any]]) -> tuple[int, int]:
    inserted = 0
    updated = 0
    for document in documents:
        result = collection.update_one(
            {"record_fingerprint": document["record_fingerprint"]},
            {"$set": document},
            upsert=True,
        )
        if result.upserted_id is not None:
            inserted += 1
        elif result.modified_count > 0:
            updated += 1
    return inserted, updated


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Store lead_gen3 exported leads/emails JSON files into new MongoDB collections."
    )
    parser.add_argument(
        "--product-description",
        required=True,
        help="Product description label to store with every lead/email record, e.g. DigiExpense or DigiCom.",
    )
    parser.add_argument(
        "--lead-file",
        default="latest",
        help="Path to a leads_*.json file, or 'latest' to use the newest one in lead_gen3.",
    )
    parser.add_argument(
        "--email-file",
        default="latest",
        help="Path to an emails_*.json file, or 'latest' to use the newest one in lead_gen3.",
    )
    return parser.parse_args()


def resolve_input_file(value: str, prefix: str) -> Path:
    if value.strip().lower() == "latest":
        return latest_matching_file(prefix)
    return Path(value).resolve()


def import_json_exports(product_description: str, lead_file: str | Path = "latest", email_file: str | Path = "latest") -> dict[str, Any]:
    load_environment()

    lead_path = resolve_input_file(str(lead_file), "leads")
    email_path = resolve_input_file(str(email_file), "emails")

    lead_records = load_json_array(lead_path)
    email_records = load_json_array(email_path)

    lead_docs = prepare_lead_docs(lead_records, product_description, lead_path)
    email_docs = prepare_email_docs(email_records, product_description, email_path)

    client, db = mongo_database()
    try:
        ensure_indexes(db)
        leads_inserted, leads_updated = upsert_many(db[LEADS_COLLECTION_NAME], lead_docs)
        emails_inserted, emails_updated = upsert_many(db[EMAILS_COLLECTION_NAME], email_docs)
    finally:
        client.close()

    return {
        "mongo_uri": os.getenv("MONGODB_URI", "mongodb://localhost:27017"),
        "mongo_db": os.getenv("MONGODB_DB", "ai_sales_agent"),
        "lead_file": lead_path,
        "email_file": email_path,
        "lead_input_count": len(lead_docs),
        "email_input_count": len(email_docs),
        "leads_inserted": leads_inserted,
        "leads_updated": leads_updated,
        "emails_inserted": emails_inserted,
        "emails_updated": emails_updated,
    }


def main() -> None:
    args = parse_args()
    result = import_json_exports(
        product_description=args.product_description,
        lead_file=args.lead_file,
        email_file=args.email_file,
    )

    print(f"MongoDB: {result['mongo_uri']} / {result['mongo_db']}")
    print(f"Leads file: {Path(result['lead_file']).name}")
    print(f"Emails file: {Path(result['email_file']).name}")
    print(
        f"Collection '{LEADS_COLLECTION_NAME}': inserted={result['leads_inserted']}, "
        f"updated={result['leads_updated']}, total_input={result['lead_input_count']}"
    )
    print(
        f"Collection '{EMAILS_COLLECTION_NAME}': inserted={result['emails_inserted']}, "
        f"updated={result['emails_updated']}, total_input={result['email_input_count']}"
    )


if __name__ == "__main__":
    main()