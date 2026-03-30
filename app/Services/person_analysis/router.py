"""
Client Analysis API — All 4 Stages + Session History + Edit
=============================================================
 
Analysis routes
───────────────
POST  /api/analyze/start                Reserve a session_id (optional)
POST  /api/analyze/stream               Stage 1+2 — stream questions (SSE)
POST  /api/analyze/submit-answers       Stage 3   — stream analysis   (SSE)
POST  /api/analyze/chat                 Stage 4   — stream chat reply  (SSE)
 
Session history routes
──────────────────────
GET   /api/sessions/user/{user_id}      List all sessions for a user (summaries)
GET   /api/sessions/{session_id}        Full session detail
 
Edit routes
───────────
PATCH /api/sessions/{session_id}/messages/{message_id}   Edit a chat message
PATCH /api/sessions/{session_id}/analysis                Edit an analysis field
"""

import logging
from fastapi.responses import StreamingResponse
from fastapi import APIRouter
from .agent import agent_manager
from .schema import StartAnalysisRequest, StartResponse, SubmitAnswersRequest, ChatRequest, EditChatMessageRequest, EditAnalysisFieldRequest
from .streaming import stream_questions_sse, stream_analysis_sse, stream_chat_sse
from app.DB.mongodb.mongodb import MongoDB
from app.moduls.auth.auth import get_current_user

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
mongodb = MongoDB()



# ─── Stage 1 — Reserve session (optional) ────────────────────────────────────

@router.post(
    "/api/analyze/start",
    response_model=StartResponse,
    summary="Stage 1 — reserve session_id",
    tags=["Analysis"],
)
async def start_analysis(body: StartAnalysisRequest,user:dict=Depends(get_current_user)) -> StartResponse:
    """
    Reserve a session_id without starting the stream.
    Useful when the frontend needs the ID before opening EventSource.
    The session document is NOT created here — that happens when
    /api/analyze/stream is called.
    """
    session_id = body.resolved_session_id()
    return StartResponse(
        session_id=session_id,
        user_id=user['user_id'],
        status="ready",
        stream_url="/api/analyze/stream",
    )
 
 
@router.post(
    "/api/analyze/stream",
    summary="Stage 1+2 — input + stream questions (SSE)",
    tags=["Analysis"],
)
async def stream_questions(body: StartAnalysisRequest,user:dict=Depends(get_current_user)):
    """
    Creates the session document in MongoDB, then streams diagnostic questions.
 
    SSE events:  session_created → question (×N) → done
 
    Store the session_id from session_created — needed for every later call.
    """
    session_id = body.resolved_session_id()
    logger.info("Stage 1+2 | session=%s user=%s", session_id, body.user_id)
 
    return StreamingResponse(
        stream_questions_sse(
            agent=agent_manager.question_agent,
            repo=agent_manager.session_repo,
            user_message=_build_input_message(body),
            session_id=session_id,
            user_id=user['user_id'],
            client_description=body.situation_description,
        ),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )
 
 
@router.post(
    "/api/analyze/submit-answers",
    summary="Stage 3 — submit answers + stream analysis (SSE)",
    tags=["Analysis"],
)
async def submit_answers(body: SubmitAnswersRequest,user:dict=Depends(get_current_user) ):
    """
    Saves the answers, runs the analysis agent, streams the result.
 
    SSE events:  thinking → analysis → done
 
    The analysis event carries the full structured JSON object
    (tones, scores, conflict_points, recommendation, etc.)
    """
    logger.info("Stage 3 | session=%s user=%s answers=%d",
                body.session_id, user['user_id'], len(body.answers))
 
    return StreamingResponse(
        stream_analysis_sse(
            agent=agent_manager.analysis_agent,
            repo=agent_manager.session_repo,
            answers=body.answers,
            session_id=body.session_id,
            user_id=user['user_id'],
        ),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )
 
 
@router.post(
    "/api/analyze/chat",
    summary="Stage 4 — follow-up chat (SSE)",
    tags=["Analysis"],
)
async def chat(body: ChatRequest,user:dict=Depends(get_current_user)):
    """
    Stream a follow-up chat reply.  Both the user message and the assistant
    reply are persisted to the session document with unique IDs.
 
    SSE events:  start → token (×N) → done
 
    The done event carries user_message_id and assistant_message_id
    so the frontend can reference messages for the edit API.
    """
    logger.info("Stage 4 | session=%s user=%s msg=%.60s",
                body.session_id, body.user_id, body.message)
 
    return StreamingResponse(
        stream_chat_sse(
            agent=agent_manager.chat_agent,
            repo=agent_manager.session_repo,
            message=body.message,
            session_id=body.session_id,
            user_id=user['user_id'],
        ),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )
 
 
# ════════════════════════════════════════════════════════════════════════════
# SESSION HISTORY ROUTES
# ════════════════════════════════════════════════════════════════════════════
 
@router.get(
    "/api/sessions/user/{user_id}",
    summary="List all sessions for a user",
    tags=["Session History"],
)
async def list_sessions(user:dict=Depends(get_current_user)):
    """
    Returns a lightweight list of all sessions for the given user,
    sorted newest-first.
 
    Each item includes: session_id, stage, client_profile, created_at,
    updated_at, and a 3-field analysis preview (tones, risk_level, summary).
 
    Full session data (questions, answers, full analysis, chat messages)
    is excluded — use GET /api/sessions/{session_id} for that.
    """
    sessions = await agent_manager.session_repo.list_sessions(user['user_id'])
    return {"user_id": user['user_id'], "total": len(sessions), "sessions": sessions}
 
 
@router.get(
    "/api/sessions/{session_id}",
    summary="Get full session detail",
    tags=["Session History"],
)
async def get_session(session_id: str,user:dict=Depends(get_current_user)):
    """
    Returns the complete session document:
      - client_profile
      - questions (with all options)
      - answers (with selected option details)
      - analysis (full structured JSON)
      - chat_messages (with id, role, content, timestamp, edited flag,
        and original_content if the message was edited)
    """
    doc = await agent_manager.session_repo.get_session(session_id)
    if not doc:
        raise HTTPException(status_code=404, detail=f"Session {session_id!r} not found")
    return doc
 
 
# ════════════════════════════════════════════════════════════════════════════
# EDIT ROUTES
# ════════════════════════════════════════════════════════════════════════════
 
@router.patch(
    "/api/sessions/{session_id}/messages/{message_id}",
    summary="Edit a chat message (user or assistant)",
    tags=["Edit"],
)
async def edit_chat_message(
    session_id: str,
    message_id: str,
    body: EditChatMessageRequest,
    user:dict=Depends(get_current_user)
):
    """
    Update the content of a single chat message.
 
    Works for both user messages and assistant replies.
 
    The original content is preserved in original_content on first edit
    (subsequent edits keep the very first original, not intermediate ones).
    The edited flag is set to true and is never reset.
 
    Typical use: let the app user correct their question or regenerate
    with a different phrasing.
    """
    ok = await agent_manager.session_repo.edit_chat_message(
        session_id, message_id, body.new_content
    )
    if not ok:
        raise HTTPException(
            status_code=404,
            detail=f"Message {message_id!r} not found in session {session_id!r}",
        )
    return {
        "updated": True,
        "session_id": session_id,
        "message_id": message_id,
    }
 
 
@router.patch(
    "/api/sessions/{session_id}/analysis",
    summary="Edit a field in the analysis",
    tags=["Edit"],
)
async def edit_analysis_field(session_id: str, body: EditAnalysisFieldRequest ,user:dict=Depends(get_current_user)):
    """
    Update any field (or nested sub-field) inside the analysis object.
 
    field_path examples and expected new_value types:
      "summary"              → string
      "recommendation"       → string
      "risk_level"           → "low" | "medium" | "high"
      "key_insight"          → string
      "scores.clarity"       → integer 0–100
      "scores.stress"        → integer 0–100
      "approach_tips"        → list of strings  (replaces entire array)
      "positive_signals"     → list of strings
      "conflict_points"      → list of {title, description} objects
 
    Every edit is recorded in analysis._edits as an audit trail:
      [ { field, old_value, edited_at }, ... ]
 
    The frontend can display a "last edited" indicator using this array.
    """
    ok = await agent_manager.session_repo.edit_analysis_field(
        session_id, body.field_path, body.new_value
    )
    if not ok:
        raise HTTPException(
            status_code=404,
            detail=f"Session {session_id!r} not found or has no analysis yet",
        )
    return {
        "updated": True,
        "session_id": session_id,
        "field_path": body.field_path,
    }
 
 
# ─── Helper ───────────────────────────────────────────────────────────────────
 
async def _build_input_message(body: StartAnalysisRequest,user_id:str) -> str:
    parts = [f"Situation description: {body.situation_description}"]    
    person = await mongodb.get_person(body.person_id, user_id)
    if person:
        parts.append(f"Person description: {person}")
    return "\n".join(parts)