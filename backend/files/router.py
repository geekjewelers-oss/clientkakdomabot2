from fastapi import APIRouter

router = APIRouter(prefix="/files", tags=["files"])


@router.get("/")
def files_stub() -> dict[str, str]:
    return {"status": "stub", "module": "files"}
