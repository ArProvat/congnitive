from app.prompt.prompt import REWRITER_SYSTEM_PROMPT
from openai import AsyncOpenAI
from app.config.settings import settings
from fastapi import HTTPException
from app.Services.messade_rewriter.schema import RefineResponse, Improvement
import json


class RefineMessageService:
     def __init__(self):
          self.client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

     async def refine_message(self, message:str):
          if not message.strip():
               raise HTTPException(status_code=400, detail="Message cannot be empty.")

          try:
               completion = self.client.chat.completions.create(
                    model="gpt-4o-mini",
                    temperature=0.4,
                    response_format={"type": "json_object"},
                    messages=[
                         {"role": "system", "content": REWRITER_SYSTEM_PROMPT},
                         {"role": "user", "content": f"Refine this message:\n\n{message}"},
                    ],
               )
          
               raw = completion.choices[0].message.content
               data = json.loads(raw)
          
               return RefineResponse(
                    polished_message=data["polished_message"],
                    highlighted_phrases=data.get("highlighted_phrases", []),
                    key_improvements=[
                         Improvement(title=i["title"], description=i["description"])
                         for i in data.get("key_improvements", [])
                    ],
                    mood=data.get("mood", ""),
                    method=data.get("method", ""),
                    goal=data.get("goal", ""),
                    original_char_count=len(request.message),
                    polished_char_count=len(data["polished_message"]),
               )
     
          except json.JSONDecodeError:
               raise HTTPException(status_code=502, detail="Model returned invalid JSON.")
