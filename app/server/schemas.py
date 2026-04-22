from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, EmailStr, Field

LeadStatus = Literal["new", "qualified", "nurture", "disqualified", "meeting_booked"]


class LeadCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=120)
    email: EmailStr
    company: str = Field(..., min_length=2, max_length=160)
    title: Optional[str] = Field(default=None, max_length=120)
    industry: Optional[str] = Field(default=None, max_length=80)
    company_size: Optional[int] = Field(default=None, ge=1)
    country: Optional[str] = Field(default=None, max_length=80)
    source: str = Field(default="crm")
    intent_signals: List[str] = Field(default_factory=list)
    notes: Optional[str] = Field(default=None, max_length=1000)


class LeadView(BaseModel):
    id: str
    name: str
    email: EmailStr
    company: str
    title: Optional[str] = None
    industry: Optional[str] = None
    company_size: Optional[int] = None
    country: Optional[str] = None
    source: str
    intent_signals: List[str]
    status: LeadStatus
    lead_score: int
    icp_score: int
    bant_score: int
    created_at: datetime
    updated_at: datetime


class EmailPayload(BaseModel):
    subject: str
    body: str


class LeadCreateResponse(BaseModel):
    lead: LeadView
    first_touch_email: EmailPayload
    sequence_steps: List[str]


class SeedResponse(BaseModel):
    inserted: int
    updated: int
    total_in_db: int


class AgentRunResponse(BaseModel):
    lead_email: EmailStr
    decision: str
    detail: str


class ReplyReceived(BaseModel):
    lead_email: EmailStr
    prospect_reply: str


class ReplyHandlerResponse(BaseModel):
    lead_email: EmailStr
    sentiment: str
    objections: List[str]
    next_action: str
    action_detail: str
    agent_response_email: Optional[EmailPayload] = None
