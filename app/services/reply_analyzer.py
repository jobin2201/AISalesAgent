"""
Reply analyzer: understands prospect responses & decides next action.
This is part of Layer 3 (Reasoning Brain).
"""
from typing import Dict, Tuple


def classify_reply_sentiment(reply_text: str) -> str:
    """
    Simple rule-based sentiment: positive / negative / neutral
    Later → use LLM
    """
    positive_signals = {
        "interested",
        "yes",
        "great",
        "love",
        "tell me more",
        "let's talk",
        "calendar",
        "meeting",
        "book",
        "call",
    }
    negative_signals = {
        "no",
        "not interested",
        "not now",
        "remove",
        "unsubscribe",
        "don't",
        "never",
    }

    reply_lower = reply_text.lower()

    if any(sig in reply_lower for sig in negative_signals):
        return "negative"
    if any(sig in reply_lower for sig in positive_signals):
        return "positive"
    return "neutral"


def extract_objections(reply_text: str) -> list:
    """
    Extract common objections from reply.
    Later → use NLP
    """
    objections = []

    reply_lower = reply_text.lower()

    if any(word in reply_lower for word in ["price", "cost", "expensive", "budget"]):
        objections.append("pricing")
    if any(word in reply_lower for word in ["time", "busy", "later", "next month", "next quarter"]):
        objections.append("timing")
    if any(word in reply_lower for word in ["feature", "integration", "api", "technical"]):
        objections.append("features")
    if any(word in reply_lower for word in ["competing", "alternative", "salesforce", "hubspot"]):
        objections.append("competitor")

    return objections


def decide_agent_next_action(reply_text: str, lead_score: int) -> Tuple[str, str]:
    """
    Decide what agent should do next:
    - send_objection_handler: answer their concern
    - send_proof: case study / demo video
    - propose_meeting: if they seem interested
    - qualify_more: ask another question
    - end_sequence: they're not interested
    """
    sentiment = classify_reply_sentiment(reply_text)
    objections = extract_objections(reply_text)

    if sentiment == "negative":
        return "end_sequence", "Prospect not interested. Pause outreach."

    if "pricing" in objections:
        return "send_objection_handler", "Prospect concerned about price. Send pricing/ROI email."

    if "timing" in objections:
        return "send_objection_handler", "Prospect busy now. Schedule follow-up for later."

    if "competitor" in objections:
        return "send_objection_handler", "Prospect comparing. Send battle card."

    if sentiment == "positive":
        return "propose_meeting", "Prospect interested. Offer 3 meeting times."

    return "qualify_more", "Neutral response. Ask more qualifying question."
