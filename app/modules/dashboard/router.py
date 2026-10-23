import secrets
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.core.config import Settings, get_settings
from app.modules.conversation.schemas import utc_now
from app.modules.dashboard.service import DashboardView, dashboard_view

PAGE = (Path(__file__).parent / "page.html").read_text(encoding="utf-8")
NO_STORE = {"Cache-Control": "no-store"}

router = APIRouter(prefix="/dashboard", tags=["dashboard"], include_in_schema=False)
_basic = HTTPBasic(auto_error=False, realm="Contigo")


def require_dashboard_access(
    credentials: Annotated[HTTPBasicCredentials | None, Depends(_basic)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    """Contraseña por autenticación básica del navegador. Sin contraseña configurada: 404."""
    if not settings.dashboard_password:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    if credentials is None or not secrets.compare_digest(
        credentials.password.encode(), settings.dashboard_password.encode()
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": 'Basic realm="Contigo"'},
        )


Access = Annotated[None, Depends(require_dashboard_access)]


@router.get("", response_class=HTMLResponse)
async def dashboard_page(_: Access) -> HTMLResponse:
    return HTMLResponse(PAGE, headers=NO_STORE)


@router.get("/api/conversations", response_model=DashboardView)
async def dashboard_data(_: Access, request: Request, response: Response) -> DashboardView:
    response.headers.update(NO_STORE)
    states = await request.app.state.repository.list_all()
    return dashboard_view(states, utc_now())
