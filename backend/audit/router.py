from fastapi import APIRouter

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("/")
def audit_stub() -> dict[str, str]:
    return {"status": "stub", "module": "audit"}
