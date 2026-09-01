from fastapi import APIRouter, HTTPException

from app.services.dam_service import get_dams


router = APIRouter(
    prefix="/api/dams",
    tags=["Dams"],
)


@router.get("")
def list_dams():
    try:
        return get_dams()
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=500,
            detail=str(exc),
        ) from exc