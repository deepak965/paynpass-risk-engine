from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(
    title="PayNPass Risk Intelligence Engine",
    version="1.0.0"
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def root():
    return {"message": "PayNPass Risk Engine Running"}

@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "service": "paynpass-risk-engine",
        "version": "1.0.0"
    }