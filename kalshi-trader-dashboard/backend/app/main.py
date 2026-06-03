"""FastAPI application entry point.

Binds to 127.0.0.1 only (local single-user dashboard).
CORS is open for the Vite dev server on localhost:5173.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routers import chat, control, pnl, portfolio, trades

app = FastAPI(title="Kalshi Trader Dashboard", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(control.router, prefix="/api")
app.include_router(portfolio.router, prefix="/api")
app.include_router(trades.router, prefix="/api")
app.include_router(pnl.router, prefix="/api")
app.include_router(chat.router, prefix="/api")


@app.get("/api/health")
def health():
    return {"status": "ok"}
