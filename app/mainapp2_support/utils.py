def format_list(value) -> str:
    if not value:
        return ""
    if isinstance(value, list):
        return ", ".join(str(item) for item in value if str(item).strip())
    return str(value)


def collect_recipients(lead: dict, email_records: list[dict]) -> list[str]:
    candidates: list[str] = []
    for record in email_records:
        email = (record.get("contact_email") or "").strip()
        if email and email not in candidates:
            candidates.append(email)
    for email in lead.get("emails_found", []) or []:
        if email and email not in candidates:
            candidates.append(email)
    for contact in lead.get("exec_contacts", []) or []:
        email = (contact.get("email") or "").strip()
        if email and email not in candidates:
            candidates.append(email)
    return candidates