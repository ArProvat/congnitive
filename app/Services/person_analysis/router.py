"""
Client Analysis API — All 4 Stages
====================================

Stage 1+2  POST /api/analyze/stream         Stream diagnostic questions (SSE)
Stage 1    POST /api/analyze/start          Reserve a session_id (optional)
Stage 3    POST /api/analyze/submit-answers Stream structured analysis (SSE)
Stage 4    POST /api/analyze/chat           Stream follow-up chat reply (SSE)

All four stages share the same MongoDB thread via session_id == thread_id.
LangGraph loads and saves the full conversation history automatically.

SSE event reference
───────────────────
Stage 1+2  session_created | question | done | error
Stage 3    thinking | analysis | done | error
Stage 4    start | token | done | error
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi import APIRouter
from .agent import agent_manager
from .schema import StartAnalysisRequest, StartResponse, SubmitAnswersRequest, ChatRequest
from .streaming import stream_questions_sse, stream_analysis_sse, stream_chat_sse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",   # disable nginx / reverse-proxy buffering
    "Connection": "keep-alive",
}

router = APIRouter()




# ─── Stage 1 — Reserve session (optional) ────────────────────────────────────

@router.post(
    "/api/analyze/start",
    response_model=StartResponse,
    summary="Stage 1 — create session",
    tags=["Stage 1+2 Questions"],
)
async def start_analysis(body: StartAnalysisRequest) -> StartResponse:
    """
    Validate input and return a session_id without starting the stream.
    Useful when the frontend needs to store the session_id before
    opening the EventSource connection.
    """
    session_id = body.resolved_session_id()
    return StartResponse(
        session_id=session_id,
        user_id=body.user_id,
        status="ready",
        stream_url="/api/analyze/stream",
    )


# ─── Stage 1+2 — Stream questions ────────────────────────────────────────────

@router.post(
    "/api/analyze/stream",
    summary="Stage 1+2 — input + stream questions",
    tags=["Stage 1+2 Questions"],
    response_description="SSE stream of question events",
)
async def stream_questions(body: StartAnalysisRequest):
    """
    Accept client description, start a LangGraph agent thread in MongoDB,
    and stream diagnostic questions back as Server-Sent Events.

    Each question is emitted as a separate 'question' event the instant the
    model finishes generating it.

    The session_id in the 'done' event is the LangGraph thread_id.
    Store it and pass it to Stage 3 and 4.

    SSE events: session_created → question (×N) → done
    """
    session_id = body.resolved_session_id()
    logger.info("Stage 1+2 | session=%s user=%s", session_id, body.user_id)

    return StreamingResponse(
        stream_questions_sse(
            agent=agent_manager.question_agent,
            user_message=_build_input_message(body),
            session_id=session_id,
            user_id=body.user_id,
        ),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


# ─── Stage 3 — Submit answers + stream analysis ───────────────────────────────

@router.post(
    "/api/analyze/submit-answers",
    summary="Stage 3 — submit answers + stream analysis",
    tags=["Stage 3 Analysis"],
    response_description="SSE stream with structured analysis",
)
async def submit_answers(body: SubmitAnswersRequest):
    """
    Receive the user's selected answers to the diagnostic questions.

    The analysis agent resumes the existing MongoDB thread (via session_id),
    reads the full conversation history (client description + questions),
    appends the answers, and produces a structured JSON analysis.

    SSE events: thinking → analysis → done

    'analysis' event payload:
    {
      "tones": [...],
      "summary": "...",
      "key_insight": "...",
      "scores": { "clarity": 84, "stress": 22, "trust": 61, "empathy": 45 },
      "conflict_points": [{ "title": "...", "description": "..." }],
      "risk_level": "medium",
      "recommendation": "...",
      "approach_tips": [...],
      "positive_signals": [...]
    }
    """
    logger.info(
        "Stage 3 | session=%s user=%s answers=%d",
        body.session_id, body.user_id, len(body.answers),
    )

    return StreamingResponse(
        stream_analysis_sse(
            agent=agent_manager.analysis_agent,
            answers=body.answers,
            session_id=body.session_id,
            user_id=body.user_id,
        ),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


# ─── Stage 4 — Follow-up chat ────────────────────────────────────────────────

@router.post(
    "/api/analyze/chat",
    summary="Stage 4 — follow-up chat",
    tags=["Stage 4 Chat"],
    response_description="SSE stream of chat reply tokens",
)
async def chat(body: ChatRequest):
    """
    Ask follow-up questions about the analysis.

    The chat agent resumes the full thread from MongoDB — the model has
    the complete context: client description, questions, answers, analysis,
    and any prior chat turns.  No history needs to be passed manually.

    SSE events: start → token (×N) → done

    'done' event includes 'full_response' with the assembled reply text.
    """
    logger.info(
        "Stage 4 | session=%s user=%s msg=%.60s",
        body.session_id, body.user_id, body.message,
    )

    return StreamingResponse(
        stream_chat_sse(
            agent=agent_manager.chat_agent,
            message=body.message,
            session_id=body.session_id,
            user_id=body.user_id,
        ),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


# ─── Helper ───────────────────────────────────────────────────────────────────

def _build_input_message(body: StartAnalysisRequest) -> str:
    parts = [f"Client description: {body.client_description}"]
    if body.client_type:
        parts.append(f"Client type / category: {body.client_type}")
    return "\n".join(parts)