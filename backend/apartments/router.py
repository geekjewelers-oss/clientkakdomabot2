from fastapi import APIRouter

router = APIRouter(prefix="/apartments", tags=["apartments"])


@router.get("/")
def apartments_stub() -> dict[str, str]:
    return {"status": "stub", "module": "apartments"}
