"""
Agent factory and lifecycle manager.

Uses create_react_agent (LangGraph stable API) with AsyncMongoDBSaver so that
the conversation thread (session_id) is persisted across the 4 stages.

Stage 3 and 4 resume the same thread by passing the same session_id as thread_id.
"""
import logging
from langchain_openai import ChatOpenAI
from langchain.agents import create_agent
from langgraph.checkpoint.mongodb import MongoDBSaver
from motor.motor_asyncio import AsyncIOMotorClient

from app.config.settings import get_settings
from app.prompt.prompt import QUESTION_GENERATION_SYSTEM_PROMPT

logger = logging.getLogger(__name__)
settings = get_settings()


class AgentManager:
     """
     Manages the MongoDB connection, checkpointer, and agent lifecycle.
     Initialised once at app startup via FastAPI lifespan.
     """

     def __init__(self) -> None:
          self._mongo_client: AsyncIOMotorClient | None = None
          self._checkpointer: MongoDBSaver | None = None
          self._agent = None

     async def initialize(self) -> None:
          logger.info("Connecting to MongoDB: %s", settings.MONGODB_URI)
          self._mongo_client = AsyncIOMotorClient(settings.MONGODB_URI)

          # AsyncMongoDBSaver uses the motor async client directly
          self._checkpointer = MongoDBSaver(
               client=self._mongo_client,
               db_name=settings.MONGODB_DB_NAME,
          )
          # Creates required indexes / collections if they don't exist
          await self._checkpointer.setup()
          logger.info("MongoDB checkpointer ready (db=%s)", settings.MONGODB_DB_NAME)

          model = ChatOpenAI(
               model="gpt-4o-mini",
               api_key=settings.OPENAI_API_KEY,
               max_tokens=2048,
          )

          # state_modifier injects the system prompt into every LLM call
          self._question_agent = create_agent(
               model=model,
               tools=[],                                       # no tools for question gen
               system_prompt=QUESTION_GENERATION_SYSTEM_PROMPT,
               checkpointer=self._checkpointer,
          )
          self._analysis_agent = create_agent(
               model=model,
               tools=[],
               system_prompt=ANALYSIS_SYSTEM_PROMPT,
               checkpointer=self._checkpointer,
          )
     
          self._chat_agent = create_agent(
               model=model,
               tools=[],
               system_prompt=CHAT_SYSTEM_PROMPT,
               checkpointer=self._checkpointer,
          )
     
          logger.info("All three LangGraph agents ready")
     

     async def close(self) -> None:
          if self._mongo_client:
               self._mongo_client.close()
               logger.info("MongoDB connection closed")

     @property
     def agent(self):
          if self._agent is None:
               raise RuntimeError("AgentManager.initialize() has not been called")
          return self._agent

     @property
     def checkpointer(self) -> MongoDBSaver:
          if self._checkpointer is None:
               raise RuntimeError("AgentManager.initialize() has not been called")
          return self._checkpointer


# Singleton — imported by main.py and route handlers
agent_manager = AgentManager()