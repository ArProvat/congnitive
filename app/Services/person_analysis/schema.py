from pydantic import BaseModel, Field
from typing import Any, Optional
import uuid


# ─── Shared ───────────────────────────────────────────────────────────────────

class StartAnalysisRequest(BaseModel):
    person_id: str = Field(..., description="Person ID")
    situation_description: str = Field(..., description="Description of the situation")
    session_id: Optional[str] = Field(
        default=None,
        description="Omit to create a new session; supply to resume an existing one",
    )

    def resolved_session_id(self) -> str:
        return self.session_id or str(uuid.uuid4())


class StartResponse(BaseModel):
    session_id: str
    status: str
    stream_url: str


# ─── Stage 2 — Questions ──────────────────────────────────────────────────────

class QuestionOption(BaseModel):
    id: str
    label: str
    value: str


class Question(BaseModel):
    id: str
    text: str
    category: str
    options: list[QuestionOption]


# ─── Stage 3 — Submit answers + Analysis ─────────────────────────────────────

class AnswerItem(BaseModel):
    """A single answered question as submitted by the frontend."""
    question_id: str = Field(..., description="Matches Question.id, e.g. 'q1'")
    question_text: str = Field(..., description="Full text of the question")
    category: str = Field(..., description="Question category")
    selected_option_id: str = Field(..., description="Matches QuestionOption.id")
    selected_option_label: str = Field(..., description="Human-readable label")
    selected_option_value: str = Field(..., description="Machine-readable snake_case value")


class SubmitAnswersRequest(BaseModel):
    session_id: str = Field(..., description="session_id from Stage 1/2")
    user_id: str
    answers: list[AnswerItem] = Field(..., min_length=1)




class ConflictPoint(BaseModel):
    title: str
    description: str


class Analysis(BaseModel):
    """Structured analysis returned by Stage 3."""
    tones: list[str]
    summary: str
    key_insight: str
    scores: EmotionScores
    conflict_points: list[ConflictPoint]
    risk_level: str                  # low | medium | high
    recommendation: str
    approach_tips: list[str]
    positive_signals: list[str]


# ─── Stage 4 — Follow-up chat ─────────────────────────────────────────────────

class ChatRequest(BaseModel):
    session_id: str = Field(..., description="session_id from Stage 1/2")
    user_id: str
    message: str = Field(..., description="Follow-up question from the app user")


# ─── Session history ──────────────────────────────────────────────────────────

class ClientProfile(BaseModel):
    description: str
    type: Optional[str]


class ChatMessage(BaseModel):
    id: str
    role: str                        # "user" | "assistant"
    content: str
    timestamp: str                   # ISO datetime string
    edited: bool
    original_content: Optional[str]


class SessionSummary(BaseModel):
    """Lightweight record returned in the list endpoint."""
    session_id: str
    user_id: str
    created_at: str
    updated_at: str
    stage: str
    client_profile: ClientProfile
    analysis_preview: Optional[dict] = None   # tones + risk_level + summary only


class SessionDetail(BaseModel):
    """Full session document returned by the get-by-id endpoint."""
    session_id: str
    user_id: str
    created_at: str
    updated_at: str
    stage: str
    client_profile: ClientProfile
    questions: list[dict]
    answers: list[dict]
    analysis: Optional[dict]
    chat_messages: list[ChatMessage]


# ─── Edit requests ────────────────────────────────────────────────────────────

class EditChatMessageRequest(BaseModel):
    """Edit the content of one chat message (user or assistant turn)."""
    new_content: str = Field(..., min_length=1)


class EditAnalysisFieldRequest(BaseModel):
    """
    Edit one field inside the analysis object.

    field_path examples:
      "summary"
      "recommendation"
      "risk_level"
      "scores.clarity"        → integer 0-100
      "approach_tips"         → list[str]  (replaces whole array)
      "conflict_points"       → list[{title, description}]
    """
    field_path: str = Field(
        ...,
        description="Dot-notation path inside the analysis object",
        examples=["summary", "recommendation", "scores.clarity"],
    )
    new_value: Any = Field(..., description="Replacement value — type must match the field")