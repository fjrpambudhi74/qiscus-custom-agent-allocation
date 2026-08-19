from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
@router.head("/health")
def health() -> dict:
    return {"status": "ok"}
