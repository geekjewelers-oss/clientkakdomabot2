from fastapi import APIRouter

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/phone/start")
def start_phone_auth() -> dict[str, str]:
    return {"status": "stub", "message": "phone start endpoint"}


@router.post("/phone/verify")
def verify_phone_auth() -> dict[str, str]:
    return {"status": "stub", "message": "phone verify endpoint"}
