from fastapi import APIRouter, Depends

from app.auth.dependencies import get_current_user
from app.db.models import User
from app.tools.catalog import list_tools

router = APIRouter(prefix="/api/tools", tags=["tools"])


@router.get("")
def tools(_user: User = Depends(get_current_user)):
    return {"tools": list_tools()}
