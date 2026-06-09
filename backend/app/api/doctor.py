from fastapi import APIRouter, Depends

from app.auth.dependencies import get_current_admin
from app.db.models import User
from app.doctor.checks import run_doctor


router = APIRouter(prefix="/api/doctor", tags=["doctor"])


@router.get("")
async def doctor(_admin: User = Depends(get_current_admin)):
    return await run_doctor()

