from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.dams import router as dams_router
from app.api.simulations import router as simulations_router


app = FastAPI(
    title="DEM Twin API",
    version="0.2.0",
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(dams_router)
app.include_router(simulations_router)


@app.get("/")
def root():
    return {
        "message": "DEM Twin API is running",
        "status": "ok",
    }