"""
SessionRepository — owns every read/write against the `sessions` collection.

Document schema
───────────────
{
  _id:            session_id   (str uuid, same as LangGraph thread_id)
  user_id:        str
  created_at:     datetime (UTC)
  updated_at:     datetime (UTC)
  stage:          "questions" | "analysis" | "chat"

  client_profile: { description: str, type: str | null }

  questions: [                    ← appended one-by-one as Stage 2 streams
    { id, text, category, options: [{id, label, value}] }
  ]

  answers: [                      ← saved atomically when Stage 3 starts
    { question_id, question_text, category,
      selected_option_id, selected_option_label, selected_option_value }
  ]

  analysis: { ... } | null        ← saved after Stage 3 produces the JSON

  chat_messages: [                ← one doc per turn, appended each Stage 4 call
    {
      id:               str (uuid4)
      role:             "user" | "assistant"
      content:          str
      timestamp:        datetime (UTC)
      edited:           bool
      original_content: str | null   (set on first edit, never overwritten again)
    }
  ]
}

Indexes created in setup():
  sessions.user_id        (for listing by user)
  sessions.created_at     (for chronological listing)
"""
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorClient

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


class SessionRepository:
     COLLECTION = "sessions"

     def __init__(self, client: AsyncIOMotorClient, db_name: str) -> None:
          self._col = client[db_name][self.COLLECTION]

     # ── Lifecycle ─────────────────────────────────────────────────────────────

     async def setup(self) -> None:
          """Create indexes (idempotent)."""
          await self._col.create_index("user_id")
          await self._col.create_index("created_at")
          logger.info("SessionRepository indexes ready")

     # ── Stage 1+2 ─────────────────────────────────────────────────────────────

     async def create_session(
          self,
          session_id: str,
          user_id: str,
          client_description: str,
          client_type: str | None,
     ) -> None:
          """Insert a new session document at the start of Stage 1+2."""
          doc = {
               "_id": session_id,
               "user_id": user_id,
               "created_at": _now(),
               "updated_at": _now(),
               "title": None,
               "stage": "questions",
               "client_profile": {
                    "description": client_description,
                    "type": client_type,
               },
               "questions": [],
               "answers": [],
               "analysis": None,
               "chat_messages": [],
          }
          await self._col.insert_one(doc)
          logger.debug("Session created: %s", session_id)

     async def append_question(self, session_id: str, question: dict) -> None:
          """Push one parsed question into the questions array (Stage 2)."""
          await self._col.update_one(
               {"_id": session_id},
               {
                    "$push": {"questions": question},
                    "$set": {"updated_at": _now()},
               },
          )

     async def mark_questions_done(self, session_id: str) -> None:
          await self._col.update_one(
               {"_id": session_id},
               {"$set": {"stage": "questions_done", "updated_at": _now()}},
          )

     # ── Stage 3 ───────────────────────────────────────────────────────────────

     async def save_answers(self, session_id: str, answers: list[dict]) -> None:
          """Replace answers array with the submitted answers (Stage 3 start)."""
          await self._col.update_one(
               {"_id": session_id},
               {"$set": {"answers": answers, "stage": "analysis", "updated_at": _now()}},
          )

     async def save_analysis(self, session_id: str, analysis: dict) -> None:
          """Persist the structured analysis JSON (Stage 3 done)."""
          await self._col.update_one(
               {"_id": session_id},
               {"$set": {"analysis": analysis, "stage": "analysis_done", "updated_at": _now()}},
          )

     # ── Stage 4 ───────────────────────────────────────────────────────────────

     async def append_chat_message(
          self,
          session_id: str,
          role: str,
          content: str,
     ) -> str:
          """
          Append a chat message and return its generated id.
          Call once for the user turn, once for the assistant reply.
          """
          msg_id = _new_id()
          message = {
               "id": msg_id,
               "role": role,
               "content": content,
               "timestamp": _now(),
               "edited": False,
               "original_content": None,
          }
          await self._col.update_one(
               {"_id": session_id},
               {
                    "$push": {"chat_messages": message},
                    "$set": {"stage": "chat", "updated_at": _now()},
               },
          )
          return msg_id

     # ── Read ──────────────────────────────────────────────────────────────────

     async def get_session(self, session_id: str) -> dict | None:
          """Return the full session document, or None if not found."""
          doc = await self._col.find_one({"_id": session_id})
          if doc:
               doc["session_id"] = doc.pop("_id")
          return doc

     async def list_sessions(self, user_id: str) -> list[dict]:
          """
          Return a lightweight summary list for a user, newest first.
          Excludes questions, answers, analysis body, and chat messages
          to keep the list payload small.
          """
          cursor = self._col.find(
               {"user_id": user_id},
               projection={
                    "_id": 1,
                    "user_id": 1,
                    "created_at": 1,
                    "updated_at": 1,
                    "stage": 1,
                    "client_profile": 1,
                    "analysis.tones": 1,
                    "analysis.risk_level": 1,
                    "analysis.summary": 1,
               },
          ).sort("created_at", -1)

          sessions = []
          async for doc in cursor:
               doc["session_id"] = doc.pop("_id")
               sessions.append(doc)
          return sessions

     # ── Edit ──────────────────────────────────────────────────────────────────

     async def edit_chat_message(
          self,
          session_id: str,
          message_id: str,
          new_content: str,
     ) -> bool:
          """
          Edit the content of a single chat message (user or assistant).
          Preserves original_content on first edit only.
          Returns True if a document was modified.
          """
          # First, fetch the current message to capture original_content
          doc = await self._col.find_one(
               {"_id": session_id, "chat_messages.id": message_id},
               {"chat_messages.$": 1},
          )
          if not doc or not doc.get("chat_messages"):
               return False

          current = doc["chat_messages"][0]
          # Only save original_content the very first time it is edited
          original = current.get("original_content") or current["content"]

          result = await self._col.update_one(
               {"_id": session_id, "chat_messages.id": message_id},
               {
                    "$set": {
                         "chat_messages.$.content": new_content,
                         "chat_messages.$.edited": True,
                         "chat_messages.$.original_content": original,
                         "updated_at": _now(),
                    }
               },
          )
          return result.modified_count > 0

     async def edit_analysis_field(
          self,
          session_id: str,
          field_path: str,
          new_value: Any,
     ) -> bool:
          """
          Edit a top-level or nested field inside the analysis object.

          field_path examples:
               "summary"
               "recommendation"
               "risk_level"
               "approach_tips"          (replaces the whole array)
               "scores.clarity"         (replaces a single score)
               "conflict_points"        (replaces the whole array)

          The original value is preserved in analysis._edits as a list
          of { field, old_value, edited_at } records.
          """
          # Fetch current value for audit trail
          doc = await self._col.find_one(
               {"_id": session_id},
               {"analysis": 1},
          )
          if not doc or not doc.get("analysis"):
               return False

          # Navigate to current value for audit
          old_value: Any = doc["analysis"]
          for part in field_path.split("."):
               if isinstance(old_value, dict):
                    old_value = old_value.get(part)
               else:
                    old_value = None
                    break

          audit_entry = {
               "field": field_path,
               "old_value": old_value,
               "edited_at": _now(),
          }

          result = await self._col.update_one(
               {"_id": session_id},
               {
                    "$set": {
                         f"analysis.{field_path}": new_value,
                         "updated_at": _now(),
                    },
                    "$push": {"analysis._edits": audit_entry},
               },
          )
          return result.modified_count > 0