import logging

from sqlalchemy.orm import Session

from backend.normalization import RuleValidationError, normalize_attributes
from backend.repositories.scheme_repository import SchemeRepository
from backend.rules import evaluate_scheme
from backend.schemas import EligibilityResult

logger = logging.getLogger(__name__)


class EligibilityService:
    def __init__(self, session: Session):
        self.schemes = SchemeRepository(session)

    def evaluate_all_schemes(self, user_data: dict) -> list[EligibilityResult]:
        attributes = normalize_attributes(user_data)
        results = []
        for scheme in self.schemes.get_active_schemes():
            try:
                results.append(evaluate_scheme(scheme, scheme.rules, attributes))
            except RuleValidationError:
                logger.error("eligibility_invalid_rule scheme_id=%s", scheme.id)
                raise
        return results
