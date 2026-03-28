from enum import Enum
from pydantic import BaseModel, Field
class Tone(str, Enum):
     formal = "formal"
     friendly_professional = "friendly_professional"
     concise = "concise"
     email = "email"
     executive = "executive"
     
     
TONE_INSTRUCTIONS: dict[Tone, str] = {
     Tone.formal: (
          "Use a strictly formal tone. Appropriate for legal, government, "
          "or executive-level communication."
     ),
     Tone.friendly_professional: (
          "Use a warm but professional tone — collegial and approachable, yet polished."
     ),
     Tone.concise: (
          "Be extremely concise and direct. Strip everything to essentials. No pleasantries."
     ),
     Tone.email: (
          "Format the output as a proper business email with Subject line, "
          "greeting, body paragraphs, and a sign-off."
     ),
     Tone.executive: (
          "Rewrite as a high-level executive summary — precise, authoritative, minimal words."
     ),
}

class RefineRequest(BaseModel):
     message: str
 
class Improvement(BaseModel):
     title: str
     description: str
     
class RefineResponse(BaseModel):
     polished_message: str
     highlighted_phrases: list[str]
     key_improvements: list[Improvement]
     mood: str
     method: str
     goal: str
     original_char_count: int
     polished_char_count: int
     