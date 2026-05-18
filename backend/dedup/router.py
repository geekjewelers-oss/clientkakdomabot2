from fastapi import APIRouter

router = APIRouter(prefix="/dedup", tags=["dedup"])


@router.get("/")
def dedup_stub() -> dict[str, str]:
    return {"status": "stub", "module": "dedup"}
