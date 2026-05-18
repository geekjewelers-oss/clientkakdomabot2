from fastapi import APIRouter

router = APIRouter(prefix="/intake", tags=["intake"])


@router.post("/cases")
def create_case() -> dict[str, str]:
    return {"status": "stub", "message": "create intake case"}


@router.get("/cases/{id}")
def get_case(id: str) -> dict[str, str]:
    return {"status": "stub", "id": id}


@router.patch("/cases/{id}/step")
def patch_case_step(id: str) -> dict[str, str]:
    return {"status": "stub", "id": id, "message": "step updated"}
