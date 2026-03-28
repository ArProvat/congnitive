"""
SSE streaming for Stage 2 — question generation.

How it works
------------
1. The LangGraph agent streams tokens from the LLM.
2. Tokens are buffered in memory.
3. Whenever the buffer contains the ---Q--- delimiter, the preceding text is
     extracted, validated as JSON, and immediately emitted as an SSE event.
4. The frontend receives each question the moment the model finishes it —
     no waiting for all 5 questions to complete.

SSE event shape (all events share this envelope):
     data: {"event": "<type>", ...extra fields}

Event types:
     session_created  — fired first; carries session_id for the frontend to store
     question         — one per parsed question; carries the Question object
     done             — final event; carries session_id + question count
     error            — on any exception; carries message + session_id
"""
import json
import logging
from typing import AsyncGenerator
from fastapi import APIRouter
from langchain_core.messages import AIMessageChunk
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from .agent import agent_manager
from .schema import StartAnalysisRequest, StartResponse

logging.basicConfig(
     level=logging.INFO,
     format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)

logger = logging.getLogger(__name__)

DELIMITER = "---Q---"
router = APIRouter()

def _sse(event_type: str, payload: dict) -> str:
     """
     Format a single SSE message.
     The 'data:' prefix and double newline are required by the SSE spec.
     """
     return f"data: {json.dumps({'event': event_type, **payload})}\n\n"


def _parse_question(raw: str) -> dict | None:
     """
     Parse a raw string into a validated question dict.
     Returns None (and logs a warning) on any failure.
     """
     raw = raw.strip()

     # Strip accidental markdown code fences
     if raw.startswith("```"):
          lines = raw.splitlines()
          raw = "\n".join(
               line for line in lines if not line.startswith("```")
          ).strip()

     if not raw:
          return None

     try:
          data = json.loads(raw)
     except json.JSONDecodeError as exc:
          logger.warning("Failed to parse question JSON: %s | raw=%.200s", exc, raw)
          return None

     required_fields = {"id", "text", "category", "options"}
     if not required_fields.issubset(data):
          logger.warning("Question missing fields %s | data=%s", required_fields - data.keys(), data)
          return None

     if not isinstance(data["options"], list) or len(data["options"]) < 2:
          logger.warning("Question has too few options: %s", data)
          return None

     return data


async def stream_questions_sse(
     agent,
     user_message: str,
     session_id: str,
     user_id: str,
) -> AsyncGenerator[str, None]:
     """
     Core SSE generator for Stage 1 + 2.

     Calls the LangGraph agent with a thread_id == session_id so the
     conversation is checkpointed in MongoDB.  Stage 3 / 4 resume the same
     thread by supplying the same session_id.
     """
     # ---------------------------------------------------------------
     # LangGraph checkpoint config.
     # thread_id is the primary key for the conversation in MongoDB.
     # user_id is stored as metadata so Stage 3/4 can verify ownership.
     # ---------------------------------------------------------------
     config = {
          "configurable": {
               "thread_id": session_id,
          },
          "metadata": {
               "user_id": user_id,
               "stage": "question_generation",
          },
     }

     buffer = ""
     question_count = 0

     try:
          # First event: let the frontend know the session_id immediately
          yield _sse("session_created", {"session_id": session_id, "user_id": user_id})

          # stream_mode="messages" yields (chunk, metadata) pairs
          # chunk is an AIMessageChunk when the model is producing tokens
          async for chunk, _metadata in agent.astream(
               {"messages": [{"role": "user", "content": user_message}]},
               config=config,
               stream_mode="messages",
          ):
               if not isinstance(chunk, AIMessageChunk):
                    continue

               token: str = chunk.content
               if not token:
                    continue

               buffer += token

               # Emit every complete question as soon as its delimiter arrives
               while DELIMITER in buffer:
                    before, _, buffer = buffer.partition(DELIMITER)
                    question_data = _parse_question(before)
                    if question_data:
                         question_count += 1
                         logger.debug("Emitting question %d: %s", question_count, question_data["id"])
                         yield _sse("question", {"question": question_data})

          # Flush anything left in the buffer (last question may lack a trailing delimiter)
          remainder = buffer.strip()
          if remainder:
               question_data = _parse_question(remainder)
               if question_data:
                    question_count += 1
                    yield _sse("question", {"question": question_data})

          yield _sse("done", {
               "session_id": session_id,
               "total_questions": question_count,
               "next_stage": "submit_answers",       # hint for the frontend router
          })

     except Exception as exc:
          logger.exception("Error in stream_questions_sse for session=%s", session_id)
          yield _sse("error", {"message": str(exc), "session_id": session_id})

@router.post(
     "/api/analyze/start",
     response_model=StartResponse,
     summary="Stage 1 — create session",
     tags=["analysis"],
)
async def start_analysis(body: StartAnalysisRequest) -> StartResponse:
     """
     Validate the client input and return a session_id.
     
     The returned session_id must be passed to every subsequent stage so that
     the LangGraph checkpointer can resume the same conversation thread.
     """
     session_id = body.resolved_session_id()
     return StartResponse(
               session_id=session_id,
               user_id=body.user_id,
               status="ready",
               stream_url=f"/api/analyze/stream",
     )

# ---------------------------------------------------------------------------
# Stage 1 + 2: stream questions via SSE
# ---------------------------------------------------------------------------

@router.post(
     "/api/analyze/stream",
     summary="Stage 1+2 — input + stream questions",
     tags=["analysis"],
     response_description="Server-Sent Events stream",
)
async def stream_questions(body: StartAnalysisRequest):
     """
     Accept client description, start a LangGraph agent, and stream diagnostic
     questions back as Server-Sent Events.
     
     Each question is emitted as a separate `question` event the instant the
     model finishes generating it — no waiting for all 5 to complete.
     
     The `session_id` in the final `done` event is the LangGraph thread_id
     stored in MongoDB.  Pass it to Stage 3 to continue the same thread.
     """
     session_id = body.resolved_session_id()
     user_message = _build_user_message(body)
     
     logger.info(
          "Starting question stream | session=%s user=%s client_type=%s",
          session_id, body.user_id, body.client_type,
     )
     
     return StreamingResponse(
          stream_questions_sse(
               agent=agent_manager.agent,
               user_message=user_message,
               session_id=session_id,
               user_id=body.user_id,
          ),
          media_type="text/event-stream",
          headers={
               # Prevent any proxy / CDN from buffering the stream
               "Cache-Control": "no-cache",
               "X-Accel-Buffering": "no",
               "Connection": "keep-alive",
          },
     )

def _build_user_message(body: StartAnalysisRequest) -> str:
     parts = [f"Client description: {body.client_description}"]
     if body.client_type:
          parts.append(f"Client type / category: {body.client_type}")
     return "\n".join(parts)
     