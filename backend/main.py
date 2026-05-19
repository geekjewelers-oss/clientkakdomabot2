from fastapi import FastAPI

from backend.apartments.router import router as apartments_router
from backend.audit.router import router as audit_router
from backend.auth.router import router as auth_router
from backend.dashboard.router import router as dashboard_router
from backend.dedup.router import router as dedup_router
from backend.deals.router import router as deals_router
from backend.files.router import router as files_router
from backend.intake.router import router as intake_router
from backend.ocr.router import router as ocr_router
from backend.owners.router import router as owners_router
from backend.payments.router import router as payments_router
from backend.registrations.router import router as registrations_router

app = FastAPI(title="Rental & Registration Backend API")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(auth_router)
app.include_router(intake_router)
app.include_router(ocr_router, prefix="/ocr", tags=["ocr"])
app.include_router(dedup_router)
app.include_router(deals_router)
app.include_router(apartments_router)
app.include_router(owners_router)
app.include_router(registrations_router)
app.include_router(payments_router)
app.include_router(files_router)
app.include_router(dashboard_router)
app.include_router(audit_router)
