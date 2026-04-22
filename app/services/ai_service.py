from typing import Dict, List, Tuple


HIGH_VALUE_KEYWORDS = {
	"pricing_page",
	"demo_request",
	"comparison_page",
	"contact_sales",
	"trial_signup",
}


def score_icp(lead: Dict) -> int:
	score = 0

	industry = (lead.get("industry") or "").lower()
	title = (lead.get("title") or "").lower()
	company_size = lead.get("company_size") or 0
	signals = {s.lower() for s in lead.get("intent_signals", [])}

	if industry in {"saas", "software", "fintech", "ecommerce", "healthtech"}:
		score += 30
	if any(k in title for k in ["vp", "head", "director", "manager", "founder"]):
		score += 20
	if 20 <= company_size <= 1000:
		score += 25
	if signals.intersection(HIGH_VALUE_KEYWORDS):
		score += 25

	return min(score, 100)


def assess_bant(lead: Dict) -> Tuple[int, Dict[str, bool]]:
	title = (lead.get("title") or "").lower()
	signals = {s.lower() for s in lead.get("intent_signals", [])}
	notes = (lead.get("notes") or "").lower()

	authority = any(k in title for k in ["vp", "head", "director", "founder", "owner"])
	need = bool(signals.intersection(HIGH_VALUE_KEYWORDS))
	timeline = any(k in notes for k in ["this month", "asap", "urgent", "q1", "q2", "q3", "q4"])
	budget = any(k in notes for k in ["budget", "approved", "procurement"])

	bant_map = {
		"budget": budget,
		"authority": authority,
		"need": need,
		"timeline": timeline,
	}
	bant_score = int(sum(bant_map.values()) * 25)
	return bant_score, bant_map


def lead_status_from_score(lead_score: int) -> str:
	if lead_score >= 75:
		return "qualified"
	if lead_score >= 45:
		return "nurture"
	return "new"


def generate_email(lead: Dict, lead_score: int, email_type: str = "first_touch") -> Dict[str, str]:
	"""Generate different types of personalized emails based on context."""
	name = lead.get("name", "there")
	company = lead.get("company", "your team")
	title = lead.get("title") or "your role"
	industry = (lead.get("industry") or "business").title()

	if email_type == "first_touch":
		if lead_score >= 75:
			cta = "Would you be open to a 20-minute discovery call this week?"
		elif lead_score >= 45:
			cta = "Would a short overview and use-case walkthrough be useful for your team?"
		else:
			cta = "If priorities shift, I can share a concise playbook for teams like yours."

		subject = f"{company}: quick idea to improve sales efficiency"
		body = (
			f"Hi {name},\n\n"
			f"I noticed {company} is growing in {industry}, and teams with {title} responsibilities "
			"often struggle with lead follow-up speed and consistency.\n\n"
			"Our AI Sales Agent helps qualify leads faster, personalize outreach, and reduce manual SDR work.\n\n"
			f"{cta}\n\n"
			"Best,\nAI Sales Team"
		)

	elif email_type == "price_objection":
		subject = f"RE: {company} — ROI breakdown"
		body = (
			f"Hi {name},\n\n"
			"Great question on pricing. Here's the ROI math:\\n\\n"
			"• Time saved per SDR: 2-3 hours/day\\n"
			"• That's 10-15 qualified leads/week extra\\n"
			"• At 10% close rate = 4-6 new deals/month\\n\\n"
			"For teams like yours, it often pays for itself in 4-6 weeks.\\n\\n"
			"Worth a quick ROI calc personalized for StartupX?\\n\\n"
			"Best,\\nAI Sales Team"
		)

	elif email_type == "timing_objection":
		subject = f"RE: {company} — let's reconnect in June"
		body = (
			f"Hi {name},\n\n"
			"No pressure — I know Q2 is hectic.\\n\\n"
			"How about we reconnect early June when things calm down? "
			"You'll have bandwidth to actually see the time-savings firsthand.\\n\\n"
			"If that doesn't work, I can reach out in July.\\n\\n"
			"Best,\\nAI Sales Team"
		)

	elif email_type == "case_study":
		subject = f"RE: {company} — case study inside"
		body = (
			f"Hi {name},\n\n"
			"I thought you'd find this valuable — NorthStar Tech (350-person SaaS) "
			"went from 2-hour email response to 15 minutes, and booked 8 extra meetings/month.\\n\\n"
			"They started exactly where you are now.\\n\\n"
			"Attached: full case study + metrics.\\n\\n"
			"Worth 20 minutes to see if we can replicate that for you?\\n\\n"
			"Best,\\nAI Sales Team"
		)

	elif email_type == "meeting_proposal":
		subject = f"RE: {company} — let's meet"
		body = (
			f"Hi {name},\n\n"
			"Perfect! I'd love to show you a custom walkthrough.\\n\\n"
			"Here are 3 times that work for me:\\n"
			"• Monday 2 PM IST\\n"
			"• Wednesday 10 AM IST\\n"
			"• Thursday 3 PM IST\\n\\n"
			"Which works best for you? I'll send a Zoom link + agenda.\\n\\n"
			"Best,\\nAI Sales Team"
		)

	else:
		subject = f"RE: {company}"
		body = f"Hi {name},\n\nThanks for getting back to me.\n\nBest,\nAI Sales Team"

	return {"subject": subject, "body": body}


def build_sequence_steps() -> List[str]:
	return [
		"Day 0: Personalized intro email",
		"Day 3: Follow-up with relevant use case",
		"Day 7: Meeting prompt with 2-3 time options",
	]


def choose_next_action(lead: Dict) -> Tuple[str, str]:
	score = lead.get("lead_score", 0)
	if score >= 75:
		return "propose_meeting", "High intent detected, ask for calendar slot preference."
	if score >= 45:
		return "send_case_study", "Medium intent detected, nurture with proof points."
	return "ask_qualifying_question", "Low context; ask one qualification question."
