"""
SSE streaming helpers for all four stages.

Stage 1+2  stream_questions_sse   — parsed ---Q--- token stream → question events
Stage 3    stream_analysis_sse    — collected JSON → single analysis event
Stage 4    stream_chat_sse        — token-by-token reply stream → token events

All three generators yield SSE-formatted strings that FastAPI's
StreamingResponse emits directly to the client.

SSE envelope (every event uses this shape):
  data: {"event": "<type>", ...extra_fields}\n\n

The session_id == LangGraph thread_id stored in MongoDB, so every
agent call with the same session_id continues the same conversation.
"""
import json
import logging
from typing import AsyncGenerator

from langchain_core.messages import AIMessageChunk

from app.DB.mongodb.mongodb import MongoDB
from .schema import AnswerItem

logger = logging.getLogger(__name__)

Q_DELIMITER = "---Q---"




# ─── Shared helpers ───────────────────────────────────────────────────────────

def _sse(event_type: str, payload: dict) -> str:
     return f"data: {json.dumps({'event': event_type, **payload})}\n\n"


def _make_config(session_id: str, user_id: str, stage: str) -> dict:
     return {
          "configurable": {"thread_id": session_id},
          "metadata": {"user_id": user_id, "stage": stage},
     }


async def _collect_ai_tokens(agent, message: str, config: dict) -> str:
     full = ""
     async for chunk, _ in agent.astream(
          {"messages": [{"role": "user", "content": message}]},
          config=config,
          stream_mode="messages",
     ):
          if isinstance(chunk, AIMessageChunk) and chunk.content:
               full += chunk.content
     return full


# ─── Stage 1+2 — Question streaming ──────────────────────────────────────────

def _parse_question(raw: str) -> dict | None:
     raw = raw.strip()
     if raw.startswith("```"):
          raw = "\n".join(ln for ln in raw.splitlines() if not ln.startswith("```")).strip()
     if not raw:
          return None
     try:
          data = json.loads(raw)
     except json.JSONDecodeError as exc:
          logger.warning("Question JSON parse error: %s | raw=%.200s", exc, raw)
          return None
     if not {"id", "text", "category", "options"}.issubset(data):
          return None
     if not isinstance(data["options"], list) or len(data["options"]) < 2:
          return None
     return data


async def stream_questions_sse(
     agent,
     repo: MongoDB,
     user_message: str,
     session_id: str,
     user_id: str,
     client_description: str,
     client_type: str | None,
) -> AsyncGenerator[str, None]:
     """
     Stage 1+2 SSE generator.
     Creates the session doc, streams questions, saves each one as it arrives.
     """
     config = _make_config(session_id, user_id, "question_generation")
     buffer = ""
     question_count = 0

     try:
          # ── Persist: create session record ────────────────────────────────────
          await repo.create_session(session_id, user_id, client_description, client_type)

          yield _sse("session_created", {"session_id": session_id, "user_id": user_id})

          async for chunk, _ in agent.astream(
               {"messages": [{"role": "user", "content": user_message}]},
               config=config,
               stream_mode="messages",
          ):
               if not isinstance(chunk, AIMessageChunk) or not chunk.content:
                    continue

               buffer += chunk.content

               while Q_DELIMITER in buffer:
                    before, _, buffer = buffer.partition(Q_DELIMITER)
                    q = _parse_question(before)
                    if q:
                         question_count += 1
                         # ── Persist: save question as it arrives ─────────────────
                         await repo.append_question(session_id, q)
                         logger.debug("Q%d saved + emitted: %s", question_count, q["id"])
                         yield _sse("question", {"question": q})

          # Flush remainder (last question may lack trailing delimiter)
          if buffer.strip():
               q = _parse_question(buffer)
               if q:
                    question_count += 1
                    await repo.append_question(session_id, q)
                    yield _sse("question", {"question": q})

          # ── Persist: mark stage done ───────────────────────────────────────────
          await repo.mark_questions_done(session_id)

          yield _sse("done", {
               "session_id": session_id,
               "total_questions": question_count,
               "next_stage": "submit_answers",
          })

     except Exception as exc:
          logger.exception("stream_questions_sse error session=%s", session_id)
          yield _sse("error", {"message": str(exc), "session_id": session_id})


# ─── Stage 3 — Analysis streaming ────────────────────────────────────────────

def _build_answers_message(answers: list[AnswerItem]) -> str:
     category_labels = {
          "communication_style": "Communication Style",
          "decision_making":     "Decision Making",
          "emotional_state":     "Emotional State",
          "expectations":        "Expectations",
          "past_experience":     "Past Experience",
     }
     lines = [
          "The app user has completed the diagnostic questionnaire.",
          "Here are their answers:\n",
     ]
     for ans in answers:
          cat = category_labels.get(ans.category, ans.category.replace("_", " ").title())
          lines.append(
               f"  [{cat}]  {ans.question_text}\n"
               f"   → Selected: \"{ans.selected_option_label}\" ({ans.selected_option_value})\n"
          )
     lines.append(
          "\nBased on the client description, the questions above, and these "
          "answers, produce the full structured analysis now."
     )
     return "\n".join(lines)


def _parse_analysis(raw: str) -> dict | None:
     raw = raw.strip()
     if raw.startswith("```"):
          raw = "\n".join(ln for ln in raw.splitlines() if not ln.startswith("```")).strip()
     start = raw.find("{")
     end   = raw.rfind("}") + 1
     if start == -1 or end == 0:
          logger.warning("No JSON object in analysis response")
          return None
     try:
          data = json.loads(raw[start:end])
     except json.JSONDecodeError as exc:
          logger.warning("Analysis JSON parse error: %s | raw=%.400s", exc, raw)
          return None
     required = {
          "tones", "summary", "key_insight", "scores",
          "conflict_points", "risk_level", "recommendation",
          "approach_tips", "positive_signals",
     }
     missing = required - data.keys()
     if missing:
          logger.warning("Analysis missing fields: %s", missing)
     return data


async def stream_analysis_sse(
     agent,
     repo: MongoDB,
     answers: list[AnswerItem],
     session_id: str,
     user_id: str,
) -> AsyncGenerator[str, None]:
     """
     Stage 3 SSE generator.
     Saves answers immediately, then saves analysis once parsed.
     """
     config = _make_config(session_id, user_id, "analysis")
     answers_message = _build_answers_message(answers)

     try:
          yield _sse("thinking", {
               "session_id": session_id,
               "message": "Analysing client profile…",
          })

          # ── Persist: save submitted answers ───────────────────────────────────
          await repo.save_answers(
               session_id,
               [a.model_dump() for a in answers],
          )

          raw_response = await _collect_ai_tokens(agent, answers_message, config)
          logger.debug("Analysis raw response (%.200s…)", raw_response)

          analysis = _parse_analysis(raw_response)
          if not analysis:
               yield _sse("error", {
                    "session_id": session_id,
                    "message": "Failed to parse structured analysis from model response.",
                    "raw": raw_response[:500],
               })
               return

          # ── Persist: save structured analysis ─────────────────────────────────
          await repo.save_analysis(session_id, analysis)

          yield _sse("analysis", {"session_id": session_id, "analysis": analysis})
          yield _sse("done", {"session_id": session_id, "next_stage": "follow_up_chat"})

     except Exception as exc:
          logger.exception("stream_analysis_sse error session=%s", session_id)
          yield _sse("error", {"message": str(exc), "session_id": session_id})


# ─── Stage 4 — Follow-up chat streaming ──────────────────────────────────────

async def stream_chat_sse(
     agent,
     repo: MongoDB,
     message: str,
     session_id: str,
     user_id: str,
) -> AsyncGenerator[str, None]:
     """
     Stage 4 SSE generator.
     Saves user message before streaming, saves assistant reply after done.
     Both messages get unique IDs returned in the 'done' event.
     """
     config = _make_config(session_id, user_id, "follow_up_chat")
     full_response = ""

     try:
          # ── Persist: save user message ────────────────────────────────────────
          user_msg_id = await repo.append_chat_message(session_id, "user", message)

          yield _sse("start", {"session_id": session_id, "user_message_id": user_msg_id})

          async for chunk, _ in agent.astream(
               {"messages": [{"role": "user", "content": message}]},
               config=config,
               stream_mode="messages",
          ):
               if not isinstance(chunk, AIMessageChunk) or not chunk.content:
                    continue
               token: str = chunk.content
               full_response += token
               yield _sse("token", {"content": token})

          # ── Persist: save assistant reply ─────────────────────────────────────
          assistant_msg_id = await repo.append_chat_message(
               session_id, "assistant", full_response
          )

          yield _sse("done", {
               "session_id": session_id,
               "user_message_id": user_msg_id,
               "assistant_message_id": assistant_msg_id,
               "full_response": full_response,
          })

     except Exception as exc:
          logger.exception("stream_chat_sse error session=%s", session_id)
          yield _sse("error", {"message": str(exc), "session_id": session_id})