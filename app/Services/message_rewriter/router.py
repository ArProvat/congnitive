from fastapi import APIRouter, HTTPException
from app.Services.message_rewriter.code import RefineMessageService
from app.Services.message_rewriter.schema import RefineRequest, RefineResponse
from app.config.settings import get_registry
from app.prompt.prompt_register import PromptRegistry


router = APIRouter()
service = RefineMessageService()

@router.post("/refine",
     response_model=RefineResponse,
     summary="Refine a message",
     tags=["Message Refiner"],
)
async def refine_message(
     message: str,
     registry: PromptRegistry = Depends(get_registry),
):
     try:
          return await service.refine_message(message, registry)
     except HTTPException as e:
          raise e
     except Exception as e:
          raise HTTPException(status_code=500, detail=str(e))
