"""
Agent factory and lifecycle manager.

Architecture
───────────────────────────────────────────────────────────────────
Three separate compiled LangGraph agents (question / analysis / chat),
each with a different system prompt, all sharing ONE AsyncMongoDBSaver
checkpointer instance and ONE MongoDB database.

When any of the three agents is invoked with the same thread_id
(= session_id), LangGraph loads the full message history for that
thread from MongoDB, appends the new messages, runs the LLM, and
checkpoints the updated state back.  This is how all four stages
share persistent context without any manual history management.

Stage 1 + 2  →  question_agent   (thread written for the first time)
Stage 3      →  analysis_agent   (reads Q1/2 history, appends answers + analysis)
Stage 4      →  chat_agent       (reads full history, appends each Q&A turn)

A SessionRepository is also initialised here and shares the same
MongoDB client so there is only one connection pool for the whole app.
"""
import logging
from langchain.agents import create_agent
from langchain.openai import ChatOpenAI
from langgraph.checkpoint.mongodb import MongoDBSaver
from motor.motor_asyncio import AsyncIOMotorClient

from .config import get_settings
from .db import SessionRepository
from app.prompt.prompt import (
    QUESTION_GENERATION_SYSTEM_PROMPT,
    ANALYSIS_SYSTEM_PROMPT,
    CHAT_SYSTEM_PROMPT,
)

logger = logging.getLogger(__name__)
settings = get_settings()


class AgentManager:
     """
     Initialised once at app startup (FastAPI lifespan).
     All three agents share the same checkpointer → same MongoDB thread.
     The SessionRepository shares the same motor client.
     """

     def __init__(self) -> None:
          self._mongo_client: AsyncIOMotorClient | None = None
          self._checkpointer: MongoDBSaver | None = None
          self._session_repo: SessionRepository | None = None
          self._question_agent = None
          self._analysis_agent = None
          self._chat_agent = None

     # ── Lifecycle ─────────────────────────────────────────────────────────────

     async def initialize(self) -> None:
          logger.info("Connecting to MongoDB: %s", settings.mongodb_uri)
          self._mongo_client = AsyncIOMotorClient(settings.mongodb_uri)

          self._checkpointer = MongoDBSaver(
               client=self._mongo_client,
               db_name=settings.mongodb_db_name,
          )
          # Auto-creates the checkpoints + checkpoint_writes collections
          await self._checkpointer.setup()
          logger.info("MongoDB checkpointer ready  db=%s", settings.mongodb_db_name)

          # SessionRepository reuses the same motor client
          self._session_repo = SessionRepository(
               client=self._mongo_client,
               db_name=settings.mongodb_db_name,
          )
          await self._session_repo.setup()
          logger.info("SessionRepository ready")

          model = ChatOpenAI(
               model="gpt-4o-mini",
               api_key=settings.OPENAI_API_KEY,
               max_tokens=4096,
               temperature=0.5,
               
          )

          self._question_agent = create_react_agent(
               model=model,
               tools=[],
               state_modifier=QUESTION_GENERATION_SYSTEM_PROMPT,
               checkpointer=self._checkpointer,
          )

          self._analysis_agent = create_react_agent(
               model=model,
               tools=[],
               state_modifier=ANALYSIS_SYSTEM_PROMPT,
               checkpointer=self._checkpointer,
          )

          self._chat_agent = create_react_agent(
               model=model,
               tools=[],
               state_modifier=CHAT_SYSTEM_PROMPT,
               checkpointer=self._checkpointer,
          )

          logger.info("All three LangGraph agents ready")

     async def close(self) -> None:
          if self._mongo_client:
               self._mongo_client.close()
               logger.info("MongoDB connection closed")

     # ── Accessors ─────────────────────────────────────────────────────────────

     def _require(self, obj, name: str):
          if obj is None:
               raise RuntimeError(f"AgentManager not initialised — {name} is None")
          return obj

     @property
     def question_agent(self):
          return self._require(self._question_agent, "question_agent")

     @property
     def analysis_agent(self):
          return self._require(self._analysis_agent, "analysis_agent")

     @property
     def chat_agent(self):
          return self._require(self._chat_agent, "chat_agent")

     @property
     def checkpointer(self) -> AsyncMongoDBSaver:
          return self._require(self._checkpointer, "checkpointer")

     @property
     def session_repo(self) -> SessionRepository:
          return self._require(self._session_repo, "session_repo")


# Singleton — imported by main.py
agent_manager = AgentManager()