from sqlalchemy.orm import Session

from backend.repositories.rule_repository import RuleRepository
from backend.repositories.scheme_repository import SchemeRepository


class SchemeService:
    def __init__(self, session: Session):
        self.session = session
        self.schemes = SchemeRepository(session)

    def get_active_schemes(self):
        return self.schemes.get_active_schemes()

    def get_by_id(self, scheme_id: int):
        return self.schemes.get_by_id(scheme_id)

    def catalogue_counts(self) -> tuple[int, int]:
        # Access both tables even when the catalogue has no active schemes.
        return len(self.get_active_schemes()), RuleRepository(self.session).count()

    def create_with_rules(self, *, name: str, rules: list, **metadata):
        # Caller owns the outer transaction. A failed rule rolls back the whole aggregate.
        with self.session.begin_nested():
            scheme = self.schemes.create(name=name, **metadata)
            repository = RuleRepository(self.session)
            for rule in rules:
                repository.create(scheme.id, rule)
        return scheme
