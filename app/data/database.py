import os

from dotenv import load_dotenv
from pymongo import ASCENDING, MongoClient

load_dotenv()

MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
MONGODB_DB = os.getenv("MONGODB_DB", "ai_sales_agent")

client = MongoClient(MONGODB_URI)
db = client[MONGODB_DB]

leads_collection = db["leads"]
messages_collection = db["messages"]
agent_runs_collection = db["agent_runs"]
spam_events_collection = db["spam_events"]
spam_suppression_collection = db["spam_suppression"]
spam_metrics_collection = db["spam_metrics"]
spam_warmup_collection = db["spam_warmup"]


def init_db() -> None:
    """Create indexes used by the basic and advanced agent flows."""
    leads_collection.create_index([("email", ASCENDING)], unique=True)
    leads_collection.create_index([("status", ASCENDING)])
    leads_collection.create_index([("lead_score", ASCENDING)])
    leads_collection.create_index([("updated_at", ASCENDING)])

    messages_collection.create_index([("lead_email", ASCENDING)])
    messages_collection.create_index([("created_at", ASCENDING)])

    agent_runs_collection.create_index([("lead_email", ASCENDING)])
    agent_runs_collection.create_index([("created_at", ASCENDING)])

    spam_events_collection.create_index([("created_at", ASCENDING)])
    spam_events_collection.create_index([("event_type", ASCENDING)])
    spam_events_collection.create_index([("email", ASCENDING)])
    spam_events_collection.create_index([("domain", ASCENDING)])

    spam_suppression_collection.create_index([("email", ASCENDING)], unique=True)
    spam_suppression_collection.create_index([("domain", ASCENDING)])

    spam_metrics_collection.create_index([("window", ASCENDING)], unique=True)
    spam_metrics_collection.create_index([("updated_at", ASCENDING)])

    spam_warmup_collection.create_index([("domain", ASCENDING)], unique=True)
    spam_warmup_collection.create_index([("updated_at", ASCENDING)])
