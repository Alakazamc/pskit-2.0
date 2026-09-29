from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_admin
from app.db.models import User
from app.db.session import get_db
from app.doctor.checks import run_doctor


router = APIRouter(prefix="/api/doctor", tags=["doctor"])


@router.get("")
async def doctor(
    _admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    return await run_doctor(db)
