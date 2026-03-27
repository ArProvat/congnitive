from fastapi import APIRouter, HTTPException
from app.Services.messade_rewriter.code import RefineMessageService
from app.Services.messade_rewriter.schema import RefineRequest, RefineResponse


router = APIRouter()
service = RefineMessageService()

@router.post("/refine", response_model=RefineResponse)
async def refine_message(message: str):
     try:
          return await service.refine_message(message)
     except HTTPException as e:
          raise e
     except Exception as e:
          raise HTTPException(status_code=500, detail=str(e))
