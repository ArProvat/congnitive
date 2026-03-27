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
class RewriteRequest(BaseModel):
     message: str = Field(
          ...,
          min_length=1,
          max_length=4000,
          description="The original message to rewrite",
          examples=["hey just wanted to check if u got my email, lmk asap thx"],
     )
     tone: Tone = Field(
          default=Tone.friendly_professional,
          description="The desired tone for the rewritten message",
     )
     model: str = Field(
          default="gpt-4o",
          description="OpenAI model to use",
          examples=["gpt-4o", "gpt-4o-mini", "gpt-3.5-turbo"],
     )

class RewriteResponse(BaseModel):
     original: str = Field(..., description="The original message")
     rewritten: str = Field(..., description="The professionally rewritten message")
     tone: Tone = Field(..., description="The tone applied")
 