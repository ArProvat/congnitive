# config/prompt_registry.py

from __future__ import annotations
import threading
import time
import logging
import firebase_admin
from firebase_admin import credentials, remote_config
from app.prompt.prompt import (
    REWRITER_SYSTEM_PROMPT,
    QUESTION_GENERATION_SYSTEM_PROMPT,
    ANALYSIS_SYSTEM_PROMPT,
    CHAT_SYSTEM_PROMPT,
)

logger = logging.getLogger(__name__)


class PromptRegistry:
    """
    Single source of truth for all system prompts.
    Refreshes from Firebase Remote Config in the background.
    """

    _DEFAULTS: dict[str, str] = {
        "REWRITER_SYSTEM_PROMPT": REWRITER_SYSTEM_PROMPT,
        "QUESTION_GENERATION_SYSTEM_PROMPT": QUESTION_GENERATION_SYSTEM_PROMPT,
        "ANALYSIS_SYSTEM_PROMPT": ANALYSIS_SYSTEM_PROMPT,
        "CHAT_SYSTEM_PROMPT": CHAT_SYSTEM_PROMPT,
    }

    def __init__(self):
        self._prompts: dict[str, str] = dict(self._DEFAULTS)
        self._lock = threading.Lock()

    # ── Public getters (typed properties) ────────────────────────────────────

    @property
    def rewriter(self) -> str:
        return self._get("REWRITER_SYSTEM_PROMPT")

    @property
    def question_generation(self) -> str:
        return self._get("QUESTION_GENERATION_SYSTEM_PROMPT")

    @property
    def analysis(self) -> str:
        return self._get("ANALYSIS_SYSTEM_PROMPT")

    @property
    def chat(self) -> str:
        return self._get("CHAT_SYSTEM_PROMPT")

    def _get(self, key: str) -> str:
        with self._lock:
            return self._prompts.get(key, self._DEFAULTS[key])

    # ── Firebase sync ─────────────────────────────────────────────────────────

    def fetch_and_update(self):
          try:
               # ✅ Correct sync API — get_template() not get_server_template()
               template = remote_config.get_template()

               with self._lock:
                    for key in self._DEFAULTS:
                         param = template.parameters.get(key)

                         if param and param.default_value:
                              value = param.default_value.value   # ✅ correct way to read value
                              self._prompts[key] = value if value else self._DEFAULTS[key]
                         else:
                              self._prompts[key] = self._DEFAULTS[key]

               logger.info("PromptRegistry refreshed from Remote Config.")
          except Exception as e:
               logger.error(f"Remote Config fetch failed: {e} — using cached values.")

    def start_polling(self, interval: int = 60):
        def _loop():
            while True:
                self.fetch_and_update()
                time.sleep(interval)

        t = threading.Thread(target=_loop, daemon=True)
        t.start()
        logger.info(f"PromptRegistry polling started every {interval}s.")

    def init(self, service_account_path: str, poll_interval: int = 60):
        if not firebase_admin._apps:
            cred = credentials.Certificate(service_account_path)
            firebase_admin.initialize_app(cred)
        self.fetch_and_update()
        self.start_polling(poll_interval)


# ── Global singleton ──────────────────────────────────────────────────────────
prompt_registry = PromptRegistry()