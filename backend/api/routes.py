from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from backend.schemas import EligibilityRequest, EligibilityResult, SchemeRead
from backend.services.eligibility_service import EligibilityService
from backend.services.scheme_service import SchemeService

router = APIRouter(prefix="/api")


def database_session(request: Request):
    with request.app.state.session_factory() as session:
        yield session


@router.get("/schemes", response_model=list[SchemeRead])
def list_schemes(session: Session = Depends(database_session)):
    return SchemeService(session).get_active_schemes()


@router.get("/schemes/{scheme_id}", response_model=SchemeRead)
def get_scheme(scheme_id: int, session: Session = Depends(database_session)):
    scheme = SchemeService(session).get_by_id(scheme_id)
    if scheme is None:
        raise HTTPException(status_code=404, detail="Scheme not found.")
    return scheme


@router.post("/eligibility", response_model=list[EligibilityResult])
def evaluate_eligibility(payload: EligibilityRequest, session: Session = Depends(database_session)):
    return EligibilityService(session).evaluate_all_schemes(payload.attributes)
