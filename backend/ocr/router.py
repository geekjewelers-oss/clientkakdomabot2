from fastapi import APIRouter

router = APIRouter(prefix="/ocr", tags=["ocr"])


@router.post("/passport")
def ocr_passport() -> dict[str, str]:
    return {"status": "stub", "message": "ocr passport endpoint"}
