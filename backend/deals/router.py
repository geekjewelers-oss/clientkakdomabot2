from fastapi import APIRouter

router = APIRouter(prefix="/deals", tags=["deals"])


@router.get("/")
def deals_stub() -> dict[str, str]:
    return {"status": "stub", "module": "deals"}
