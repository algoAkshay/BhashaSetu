from backend.repositories.rule_repository import RuleRepository
from backend.repositories.scheme_repository import SchemeRepository


class AdminNotFound(ValueError):
    pass


class AdminService:
    """Caller owns commit/rollback, just like the existing scheme service."""
    def __init__(self, session):
        self.session = session
        self.schemes = SchemeRepository(session)
        self.rules = RuleRepository(session)

    def scheme(self, scheme_id):
        entity = self.schemes.get_by_id(scheme_id)
        if entity is None:
            raise AdminNotFound("Scheme not found.")
        return entity

    def update_scheme(self, scheme_id, payload):
        return self.schemes.update(self.scheme(scheme_id), **payload.model_dump(exclude_unset=True))

    def rule(self, scheme_id, rule_id):
        scheme = self.scheme(scheme_id)
        entity = next((rule for rule in scheme.rules if rule.id == rule_id), None)
        if entity is None:
            raise AdminNotFound("Rule not found for this scheme.")
        return entity

    def save_rule(self, scheme_id, payload, rule_id=None):
        if rule_id is None:
            self.scheme(scheme_id)
            rule = self.rules.create(scheme_id, payload.definition)
        else:
            rule = self.rules.update(self.rule(scheme_id, rule_id), payload.definition)
        metadata = dict(rule.source_metadata or {})
        if "confidence" in payload.model_fields_set:
            key = "rule_confidence" if "rule_confidence" in metadata else "confidence"
            metadata[key] = payload.confidence
        if "notes" in payload.model_fields_set:
            metadata["notes"] = payload.notes
        rule.source_metadata = metadata
        self.session.flush()
        return rule

    def delete_rule(self, scheme_id, rule_id):
        self.rules.delete(self.rule(scheme_id, rule_id))
