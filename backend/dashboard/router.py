from fastapi import APIRouter

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/")
def dashboard_stub() -> dict[str, str]:
    return {"status": "stub", "module": "dashboard"}
