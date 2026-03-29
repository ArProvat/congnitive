from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.Services.person_analysis.router import router as analysis_router
from app.Services.message_rewriter.router import router as rewriter_router
from contextlib import asynccontextmanager
from app.prompt.prompt_register import prompt_registry

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ──────────────────────────────────────────────────────────────
    prompt_registry.init(
        service_account_path="app/config/serviceAccountKey.json",
        poll_interval=60          # re-fetch every 60 seconds
    )
    yield
    # ── Shutdown (cleanup if needed) ─────────────────────────────────────────


app = FastAPI(
     title="Cognitive API",
     version="1.0.0",
     lifespan=lifespan
)

app.add_middleware(
     CORSMiddleware,
     allow_origins=["*"],
     allow_credentials=True,
     allow_methods=["*"],
     allow_headers=["*"],
)

app.include_router(analysis_router)
app.include_router(rewriter_router)

@app.get("/health")
async def health_check():
     return {"status": "ok"}

if __name__ == "__main__":
     import uvicorn
     uvicorn.run("main:app", 
               host="0.0.0.0", 
               port=8800, 
               reload=True
          ) 