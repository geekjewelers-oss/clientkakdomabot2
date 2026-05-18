from fastapi import APIRouter

router = APIRouter(prefix="/owners", tags=["owners"])


@router.get("/")
def owners_stub() -> dict[str, str]:
    return {"status": "stub", "module": "owners"}
