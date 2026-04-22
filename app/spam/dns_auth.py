from __future__ import annotations

import os
from typing import Dict


def _domain_from_email(sender_email: str) -> str:
    value = (sender_email or "").strip().lower()
    if "@" not in value:
        return ""
    return value.split("@", 1)[1]


def _query_txt_records(name: str) -> list[str]:
    try:
        import dns.resolver  # type: ignore
    except Exception:
        return []

    try:
        answers = dns.resolver.resolve(name, "TXT")
        out: list[str] = []
        for item in answers:
            # dnspython returns multiple chunks for a TXT value.
            chunks = [part.decode() if isinstance(part, bytes) else str(part) for part in getattr(item, "strings", [])]
            if chunks:
                out.append("".join(chunks))
            else:
                out.append(str(item))
        return out
    except Exception:
        return []


def check_dns_authentication(sender_email: str) -> Dict[str, object]:
    domain = _domain_from_email(sender_email)
    if not domain:
        return {
            "domain": "",
            "ready": False,
            "spf": False,
            "dkim": False,
            "dmarc": False,
            "notes": ["Invalid sender email domain."],
        }

    spf_records = [r.lower() for r in _query_txt_records(domain) if "v=spf1" in r.lower()]
    dmarc_records = [r.lower() for r in _query_txt_records(f"_dmarc.{domain}") if "v=dmarc1" in r.lower()]

    selector1 = _query_txt_records(f"selector1._domainkey.{domain}")
    selector2 = _query_txt_records(f"selector2._domainkey.{domain}")
    dkim_ok = any("k=rsa" in r.lower() or "v=dkim1" in r.lower() for r in [*selector1, *selector2])

    spf_ok = bool(spf_records)
    dmarc_ok = bool(dmarc_records)

    notes: list[str] = []
    if not spf_ok:
        notes.append("Missing SPF TXT record (v=spf1).")
    if not dkim_ok:
        notes.append("Missing DKIM selector TXT records (selector1/selector2).")
    if not dmarc_ok:
        notes.append("Missing DMARC TXT record (_dmarc).")

    return {
        "domain": domain,
        "ready": spf_ok and dkim_ok and dmarc_ok,
        "spf": spf_ok,
        "dkim": dkim_ok,
        "dmarc": dmarc_ok,
        "notes": notes,
        "spf_records": spf_records,
        "dmarc_records": dmarc_records,
    }


def dns_auto_setup_possible() -> Dict[str, object]:
    provider = (os.getenv("DNS_PROVIDER") or "").strip().lower()
    has_dns_token = bool((os.getenv("DNS_API_TOKEN") or "").strip())
    has_tenant = bool((os.getenv("M365_TENANT_ID") or "").strip())
    has_client = bool((os.getenv("M365_CLIENT_ID") or "").strip())
    has_secret = bool((os.getenv("M365_CLIENT_SECRET") or "").strip())

    possible = provider in {"cloudflare", "godaddy", "route53"} and has_dns_token and has_tenant and has_client and has_secret

    return {
        "possible": possible,
        "provider": provider,
        "reason": "" if possible else "DNS/M365 API credentials are not fully configured.",
    }
