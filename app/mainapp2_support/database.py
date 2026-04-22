import os

import streamlit as st
from pymongo import ASCENDING, DESCENDING, MongoClient


LEADS_COLLECTION_NAME = "new_leads"
EMAILS_COLLECTION_NAME = "new_emails"
MESSAGE_LOG_COLLECTION_NAME = "new_outreach_messages"
NOTIFICATIONS_COLLECTION_NAME = "notifications"


@st.cache_resource
def mongo_client() -> MongoClient:
    return MongoClient(os.getenv("MONGODB_URI", "mongodb://localhost:27017"))


def database():
    return mongo_client()[os.getenv("MONGODB_DB", "ai_sales_agent")]


def init_db2() -> None:
    db = database()
    db[LEADS_COLLECTION_NAME].create_index([("product_description", ASCENDING)])
    db[LEADS_COLLECTION_NAME].create_index([("total_score", DESCENDING)])
    db[LEADS_COLLECTION_NAME].create_index([("company_name", ASCENDING)])

    db[EMAILS_COLLECTION_NAME].create_index([("product_description", ASCENDING)])
    db[EMAILS_COLLECTION_NAME].create_index([("company", ASCENDING)])
    db[EMAILS_COLLECTION_NAME].create_index([("contact_email", ASCENDING)])

    db[MESSAGE_LOG_COLLECTION_NAME].create_index([("created_at", DESCENDING)])
    db[MESSAGE_LOG_COLLECTION_NAME].create_index([("product_description", ASCENDING)])
    db[MESSAGE_LOG_COLLECTION_NAME].create_index([("company_name", ASCENDING)])

    db[NOTIFICATIONS_COLLECTION_NAME].create_index([("created_at", DESCENDING)])
    db[NOTIFICATIONS_COLLECTION_NAME].create_index([("status", ASCENDING)])
    db[NOTIFICATIONS_COLLECTION_NAME].create_index([("type", ASCENDING)])