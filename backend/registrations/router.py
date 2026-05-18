from fastapi import APIRouter

router = APIRouter(prefix="/registrations", tags=["registrations"])


@router.get("/")
def registrations_stub() -> dict[str, str]:
    return {"status": "stub", "module": "registrations"}
