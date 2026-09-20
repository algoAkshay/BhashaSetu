from pathlib import Path
import secrets
from typing import Literal, get_args

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from backend.admin_schemas import AdminRuleInput, Confidence, SchemePage, SchemePatch
from backend.api.routes import database_session
from backend.normalization import FIELD_TYPES
from backend.schemas import Operator, RuleRead, SchemeRead
from backend.services.admin_auth import COOKIE, SESSION_SECONDS, create_session, require_admin, settings
from backend.services.admin_service import AdminNotFound, AdminService

pages = APIRouter(prefix="/admin", include_in_schema=False)
UI = Path(__file__).resolve().parents[1] / "admin_ui"
PRIVATE = {"Cache-Control": "no-store", "X-Frame-Options": "DENY", "Referrer-Policy": "same-origin",
           "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"}


def private_response(response: Response):
    response.headers.update(PRIVATE)


router = APIRouter(prefix="/api/admin", dependencies=[Depends(require_admin), Depends(private_response)], tags=["admin"])


@pages.get("/login")
def login_page():
    return FileResponse(UI / "login.html", headers=PRIVATE)


@pages.post("/login")
async def login(request: Request):
    password, key, secure = settings()
    # A custom header + JSON prevents cross-site form login; no CORS is enabled.
    if request.headers.get("X-Admin-Login") != "1":
        raise HTTPException(403, "Invalid login request.")
    try:
        payload = await request.json()
        supplied = payload.get("password") if isinstance(payload, dict) else None
    except ValueError:
        supplied = None
    if not isinstance(supplied, str) or not secrets.compare_digest(supplied.encode(), password.encode()):
        # Never echo the submitted password in a Pydantic validation response.
        raise HTTPException(401, "Incorrect admin password.")
    response = JSONResponse({"ok": True}, headers=PRIVATE)
    response.set_cookie(COOKIE, create_session(key), max_age=SESSION_SECONDS,
                        httponly=True, samesite="strict", secure=secure, path="/")
    return response


@pages.get("")
@pages.get("/")
def panel(request: Request):
    try:
        require_admin(request)
    except HTTPException as error:
        if error.status_code == 401:
            return RedirectResponse("/admin/login", status_code=303, headers=PRIVATE)
        raise
    return FileResponse(UI / "panel.html", headers=PRIVATE)


@router.get("/session")
def session_info(response: Response, csrf: str = Depends(require_admin)):
    response.headers.update(PRIVATE)
    return {"csrf": csrf, "fields": FIELD_TYPES, "operators": get_args(Operator)}


@router.post("/logout")
def logout():
    response = JSONResponse({"ok": True}, headers=PRIVATE)
    response.delete_cookie(COOKIE, path="/", httponly=True, samesite="strict", secure=settings()[2])
    return response


def service(session: Session = Depends(database_session)):
    try:
        yield AdminService(session)
    except AdminNotFound as error:
        raise HTTPException(404, str(error)) from None


@router.get("/filters")
def filters(admin: AdminService = Depends(service)):
    return admin.schemes.admin_filter_options()


@router.get("/schemes", response_model=SchemePage)
def schemes(search: str = Query("", max_length=200),
            government_level: Literal["CENTRAL", "STATE"] | None = None,
            state_or_ut: str | None = Query(None, max_length=100),
            status_confidence: Confidence | None = None,
            category: str | None = Query(None, max_length=100), is_active: bool | None = None,
            page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100),
            admin: AdminService = Depends(service)):
    return admin.schemes.admin_page(search=search.strip(), government_level=government_level,
        state_or_ut=state_or_ut, status_confidence=status_confidence, category=category,
        is_active=is_active, page=page, page_size=page_size)


@router.get("/schemes/{scheme_id}", response_model=SchemeRead)
def scheme(scheme_id: int, admin: AdminService = Depends(service)):
    return admin.scheme(scheme_id)


@router.patch("/schemes/{scheme_id}", response_model=SchemeRead)
def update_scheme(scheme_id: int, payload: SchemePatch, admin: AdminService = Depends(service)):
    admin.update_scheme(scheme_id, payload)
    admin.session.commit()
    return admin.scheme(scheme_id)


@router.post("/schemes/{scheme_id}/rules", response_model=RuleRead, status_code=201)
def add_rule(scheme_id: int, payload: AdminRuleInput, admin: AdminService = Depends(service)):
    rule = admin.save_rule(scheme_id, payload)
    admin.session.commit()
    return rule


@router.put("/schemes/{scheme_id}/rules/{rule_id}", response_model=RuleRead)
def update_rule(scheme_id: int, rule_id: int, payload: AdminRuleInput, admin: AdminService = Depends(service)):
    rule = admin.save_rule(scheme_id, payload, rule_id)
    admin.session.commit()
    return rule


@router.delete("/schemes/{scheme_id}/rules/{rule_id}", status_code=204)
def delete_rule(scheme_id: int, rule_id: int, admin: AdminService = Depends(service)):
    admin.delete_rule(scheme_id, rule_id)
    admin.session.commit()
    return Response(status_code=204)
