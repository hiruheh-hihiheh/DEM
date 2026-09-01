from fastapi import FastAPI

app = FastAPI(
    title="DEM Twin API",
    version="0.1.0",
)


@app.get("/")
def root():
    return {
        "message": "DEM Twin API is running",
        "status": "ok",
    }