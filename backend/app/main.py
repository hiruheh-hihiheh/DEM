from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.dams import router as dams_router
from app.api.simulations import router as simulations_router
from app.api.system import router as system_router
try:
    from app.api.sites import router as sites_router
except Exception:
    sites_router = None


app = FastAPI(
    title="DEM Twin API",
    version="0.2.0",
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(dams_router)
app.include_router(simulations_router)
app.include_router(system_router)
if sites_router:
    app.include_router(sites_router)


@app.get("/")
def root():
    return {
        "message": "DEM Twin API is running",
        "status": "ok",
    }