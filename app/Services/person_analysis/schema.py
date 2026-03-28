from pydantic import BaseModel, Field
from typing import Optional
import uuid


# ─── Shared ───────────────────────────────────────────────────────────────────

class StartAnalysisRequest(BaseModel):
    user_id: str = Field(..., description="The app user's ID")
    client_description: str = Field(..., description="Description of the client to analyse")
    client_type: Optional[str] = Field(None, description="Optional client category label")
    session_id: Optional[str] = Field(
        default=None,
        description="Omit to create a new session; supply to resume an existing one",
    )

    def resolved_session_id(self) -> str:
               return self.session_id or str(uuid.uuid4())


class StartResponse(BaseModel):
     session_id: str
     user_id: str
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


class EmotionScores(BaseModel):
     clarity: int
     stress: int
     trust: int
     empathy: int


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