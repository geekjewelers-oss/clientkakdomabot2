from fastapi import APIRouter

router = APIRouter(prefix="/payments", tags=["payments"])


@router.get("/")
def payments_stub() -> dict[str, str]:
    return {"status": "stub", "module": "payments"}
