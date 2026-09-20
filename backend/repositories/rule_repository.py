from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.models import EligibilityRule
from backend.rules import validate_rule


class RuleRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_rules_for_scheme(self, scheme_id: int) -> list[EligibilityRule]:
        return list(self.session.scalars(select(EligibilityRule).where(
            EligibilityRule.scheme_id == scheme_id).order_by(EligibilityRule.id)))

    def count(self) -> int:
        return self.session.scalar(select(func.count()).select_from(EligibilityRule))

    def create(self, scheme_id: int, rule) -> EligibilityRule:
        definition = validate_rule(rule)
        entity = EligibilityRule(scheme_id=scheme_id, **definition.model_dump())
        self.session.add(entity)
        self.session.flush()
        return entity

    def update(self, rule: EligibilityRule, definition) -> EligibilityRule:
        for key, value in validate_rule(definition).model_dump().items():
            setattr(rule, key, value)
        self.session.flush()
        return rule

    def delete(self, rule: EligibilityRule) -> None:
        self.session.delete(rule)
        self.session.flush()
