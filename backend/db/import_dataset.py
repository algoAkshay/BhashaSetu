"""Import supplied researched CSVs only; no scraping or external lookup."""
import argparse
from collections import Counter
import csv
from dataclasses import dataclass, field
from datetime import date
import hashlib
import json
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from backend.config import ConfigurationError
from backend.db.session import create_session_factory
from backend.models import EligibilityRule, Scheme
from backend.normalization import FIELD_TYPES, normalize_value
from backend.schemas import RuleDefinition

DEFAULT_DIRECTORY = Path(__file__).resolve().parents[2] / "database"
MASTER_COLUMNS = set("scheme_name government_level state_or_ut ministry_or_department category short_description "
    "status_confidence official_source_url secondary_source_url last_checked_date age_min age_max gender income_type "
    "income_min income_max state_or_ut_requirement domicile_requirement district_requirement social_category occupation "
    "employment_status student_status education_level farmer_status land_holding_requirement disability_status "
    "disability_percentage_min marital_status widow_status bpl_status rural_urban_requirement minority_requirement "
    "family_conditions other_eligibility_conditions benefit_type benefit_summary benefit_amount application_method "
    "official_application_url confidence_notes".split())
RULE_COLUMNS = {"scheme_name", "field", "operator", "value", "notes"}
CONFIDENCE = {"VERIFIED", "LIKELY_ACTIVE", "NEEDS_REVIEW"}
OPERATORS = {"equals": "==", "not_equals": "!=", "boolean_equals": "==", "in": "IN",
             "not_in": "NOT_IN", "<": "<", "<=": "<=", ">": ">", ">=": ">="}
ALIASES = {"residence_type": "rural_urban"}
# Only unqualified values can be treated as scalar equality/membership. Richer
# source phrases stay visible as manual conditions rather than false negatives.
CATEGORICAL_VALUES = {
    "social_category": {"sc", "st", "obc", "ebc", "dnt", "ews", "bc"},
    "rural_urban": {"rural", "urban"}, "gender": {"male", "female"},
    "education_level": {"class ix", "class x", "class xi", "class xii", "diploma", "undergraduate",
                        "postgraduate", "graduate", "masters", "phd", "post-matric", "professional",
                        "technical diploma", "technical undergraduate degree", "vocational certificate"},
    "occupation": {"farmer", "entrepreneur", "small and marginal farmer", "unorganised worker",
                   "street vendor", "hawker", "retail trader", "shopkeeper", "self-employed person",
                   "micro entrepreneur", "self-employed", "landless agricultural labourer", "traditional artisan"},
    "employment_status": {"unemployed", "underemployed", "self-employed", "unorganised", "apprentice"},
    "marital_status": {"widowed", "married", "unmarried", "divorced"},
}
MASTER_FIELDS = {
    "age_min": ("age", ">="), "age_max": ("age", "<="), "gender": ("gender", "=="),
    "state_or_ut_requirement": ("state_or_ut", None), "social_category": ("social_category", None),
    "occupation": ("occupation", None), "employment_status": ("employment_status", None),
    "student_status": ("student_status", None), "education_level": ("education_level", None),
    "farmer_status": ("farmer_status", None), "disability_status": ("disability_status", None),
    "disability_percentage_min": ("disability_percentage", ">="), "marital_status": ("marital_status", None),
    "widow_status": ("widow_status", None), "bpl_status": ("bpl_status", None),
    "rural_urban_requirement": ("rural_urban", None), "minority_requirement": ("minority_status", None),
}


class DatasetError(ValueError):
    pass


@dataclass
class ParsedScheme:
    master: dict
    metadata: dict
    rules: list[tuple[RuleDefinition, dict]] = field(default_factory=list)
    manual: list[str] = field(default_factory=list)
    reviews: list[dict] = field(default_factory=list)
    unresolved_rules: list[dict] = field(default_factory=list)


def identity(row: dict, group: str) -> tuple[str, str, str]:
    # Central names are resolved only within the Central file. State names need
    # jurisdiction too (identical names occur in different states).
    return (group.upper(), row.get("state_or_ut", "").strip().casefold() if group == "state" else "",
            row["scheme_name"].strip().casefold())


def source_key(key: tuple) -> str:
    return "curated:" + hashlib.sha256(json.dumps(key, ensure_ascii=False).encode("utf-8")).hexdigest()


def read_rows(path: Path, required: set, issues: list) -> list[tuple[int, dict]]:
    try:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream, strict=True)
            if not reader.fieldnames or required - set(reader.fieldnames) or len(reader.fieldnames) != len(set(reader.fieldnames)):
                raise DatasetError(f"{path.name}: missing or duplicate required columns")
            rows = []
            for row in reader:
                if None in row or any(value is None for value in row.values()):
                    issues.append({"file": path.name, "row": reader.line_num, "reason": "malformed_columns"})
                    continue
                rows.append((reader.line_num, {key: value.strip() for key, value in row.items()}))
            return rows
    except (OSError, UnicodeError, csv.Error):
        raise DatasetError(f"{path.name}: cannot read valid UTF-8 CSV") from None


def normalize_category(raw: str) -> str:
    # Deterministic display taxonomy only; never affects eligibility.
    value = raw.casefold()
    priorities = [
        ("Scholarship", ("scholarship", "fellowship")), ("Disability", ("disability",)),
        ("Labour", ("labour", "tea workers")), ("Pension", ("pension", "senior citizens", "widows")),
        ("Food Security", ("food security", "pds")), ("Housing", ("housing", "land rights")),
        ("Health", ("health", "sanitation", "leprosy", "substance abuse")),
        ("Skill Development", ("skill development", "training")), ("MSME", ("msme",)),
        ("Entrepreneurship", ("entrepreneur", "self-employment", "micro-enterprise")),
        ("Education", ("students", "education", "coaching", "competitive exams")),
        ("Agriculture", ("agriculture", "farmer", "fisheries", "horticulture", "animal husbandry", "livestock")),
        ("Tribal Welfare", ("tribal", "st welfare")), ("Minority Welfare", ("minority",)),
        ("Women & Child", ("women", "children", "girls", "maternity", "nutrition", "marriage")),
        ("Employment", ("employment", "livelihood")), ("Financial Assistance", ("financial assistance", "insurance")),
    ]
    return next((category for category, terms in priorities if any(term in value for term in terms)), "Social Welfare")


def parse_rule(row: dict, master: dict) -> RuleDefinition:
    source_field = row["field"]
    name = ALIASES.get(source_field, source_field)
    if name == "annual_income" and master.get("income_type"):
        if master["income_type"] in {"FAMILY", "FAMILY_ANNUAL"}:
            name = "family_annual_income"
        else:
            raise ValueError("ambiguous_income_scope")
    if name not in FIELD_TYPES or name in {"state", "is_disabled"}:
        raise ValueError("specialized_field_requires_review")
    if row.get("rule_group", "ALL") != "ALL":
        raise ValueError("unsupported_rule_group")
    operator = OPERATORS.get(row["operator"])
    if not operator:
        raise ValueError("unsupported_operator")
    kind = FIELD_TYPES[name]
    expected_source_type = row.get("value_type")
    if expected_source_type and expected_source_type != ("number" if kind in {"decimal", "integer"} else kind):
        raise ValueError("inconsistent_source_type")
    if row["operator"] == "boolean_equals" and kind != "boolean":
        raise ValueError("invalid_boolean_operator")
    values = row["value"].split("|") if operator in {"IN", "NOT_IN"} else [row["value"]]
    normalized = []
    for value in values:
        value = value.strip()
        if kind == "boolean":
            if value.casefold() not in {"true", "false"}:
                raise ValueError("non_boolean_condition")
            value = value.casefold() == "true"
        elif kind == "string" and name in CATEGORICAL_VALUES:
            if value.casefold() not in CATEGORICAL_VALUES[name]:
                raise ValueError("qualified_category_requires_review")
        normalized.append(normalize_value(value, kind))
    value = normalized if operator in {"IN", "NOT_IN"} else normalized[0]
    return RuleDefinition(field=name, operator=operator, value=value, value_type=kind)


def _manual_master_conditions(entry: ParsedScheme):
    master = entry.master
    for key in ("other_eligibility_conditions", "family_conditions", "domicile_requirement",
                "district_requirement", "land_holding_requirement"):
        if master[key]:
            entry.manual.append(master[key] if key == "other_eligibility_conditions" else f"{key}: {master[key]}")
    for key, (name, operator) in MASTER_FIELDS.items():
        value = master[key]
        if not value or value.upper() == "ANY":
            continue
        if not any(rule.field == name and (operator is None or rule.operator == operator)
                   and raw["value"].casefold() == value.casefold() for rule, raw in entry.rules):
            entry.manual.append(f"{key}: {value}")
    for key, operator in (("income_min", ">="), ("income_max", "<=")):
        if master[key] and not any("income" in rule.field and rule.operator == operator
                                  and raw["value"] == master[key] for rule, raw in entry.rules):
            entry.manual.append(f"{key} ({master['income_type'] or 'unspecified scope'}): {master[key]}")
    if master["status_confidence"] != "VERIFIED":
        entry.manual.append(f"Source status requires confirmation: {master['status_confidence']}. {master['confidence_notes']}")
    entry.manual = list(dict.fromkeys(entry.manual))


def load_dataset(directory: Path = DEFAULT_DIRECTORY):
    directory = Path(directory)
    issues = []
    report = {"source_schemes": {}, "source_rules": {}, "duplicate_schemes": 0, "duplicate_rules": 0,
              "review_rows": 0, "rules_preserved_for_review": [], "issues": issues,
              "raw_fields": {}, "raw_operators": {}, "raw_categories": {}}
    entries = {}
    for group in ("central", "state"):
        master_path = directory / f"{group}_schemes_master.csv"
        rows = read_rows(master_path, MASTER_COLUMNS, issues)
        report["source_schemes"][group] = len(rows)
        report["raw_categories"][group] = dict(Counter(row["category"] for _, row in rows))
        for line, row in rows:
            try:
                if not row["scheme_name"] or row["government_level"] != group.upper() or not row["state_or_ut"]:
                    raise ValueError("invalid_scheme_identity")
                if row["status_confidence"] not in CONFIDENCE:
                    raise ValueError("invalid_confidence")
                checked = date.fromisoformat(row["last_checked_date"]) if row["last_checked_date"] else None
                key = identity(row, group)
                if key in entries:
                    report["duplicate_schemes"] += 1
                    if entries[key].master != row:
                        entries[key].manual.append("Conflicting duplicate source metadata requires review.")
                        issues.append({"file": master_path.name, "row": line, "reason": "conflicting_duplicate_scheme"})
                    continue
                metadata = {name: row[name] or None for name in (
                    "government_level", "ministry_or_department", "status_confidence", "secondary_source_url",
                    "benefit_type", "benefit_summary", "benefit_amount", "application_method", "official_application_url",
                    "confidence_notes", "other_eligibility_conditions")}
                metadata.update(name=row["scheme_name"], description=row["short_description"] or None,
                                state=row["state_or_ut"], official_url=row["official_source_url"] or None,
                                category=normalize_category(row["category"]), raw_category=row["category"] or None,
                                last_checked_date=checked, is_active=True)
                entries[key] = ParsedScheme(row, metadata)
            except ValueError:
                issues.append({"file": master_path.name, "row": line, "reason": "malformed_master_row"})
        rule_path = directory / f"{group}_eligibility_rules.csv"
        required = RULE_COLUMNS | ({"state_or_ut", "rule_confidence"} if group == "state" else
                                  {"rule_id", "value_type", "rule_group", "source_basis", "confidence"})
        rows = read_rows(rule_path, required, issues)
        report["source_rules"][group] = len(rows)
        report["raw_fields"][group] = dict(Counter(row["field"] for _, row in rows))
        report["raw_operators"][group] = dict(Counter(row["operator"] for _, row in rows))
        if any(item["file"] == rule_path.name for item in issues):
            # A broken column layout may hide a mandatory rule/identity. Keep the
            # valid data usable, but never present that file as fully evaluated.
            for key, entry in entries.items():
                if key[0] == group.upper():
                    entry.manual.append(f"Malformed rows in {rule_path.name}; rule completeness requires review.")
        seen_rules = set()
        for line, row in rows:
            entry = entries.get(identity(row, group))
            if entry is None:
                issues.append({"file": rule_path.name, "row": line, "reason": "missing_scheme_reference"})
                continue
            try:
                rule = parse_rule(row, entry.master)
                fingerprint = (identity(row, group), json.dumps(rule.model_dump(), sort_keys=True))
                if fingerprint in seen_rules:
                    report["duplicate_rules"] += 1
                    continue
                seen_rules.add(fingerprint)
                entry.rules.append((rule, row))
                confidence = row.get("confidence", row.get("rule_confidence"))
                if confidence not in {"HIGH", "VERIFIED"}:
                    entry.manual.append(f"Rule source confidence requires review: {row['field']} ({confidence}). {row['notes']}")
            except (ValueError, ValidationError) as error:
                reason = str(error) if type(error) is ValueError and len(str(error)) < 70 else "invalid_rule_value"
                item = {"file": rule_path.name, "row": line, "scheme": row["scheme_name"], "field": row["field"],
                        "reason": reason, "source": row}
                report["rules_preserved_for_review"].append(item)
                entry.unresolved_rules.append(item)
                entry.manual.append(f"Unresolved rule: {row['field']} {row['operator']} {row['value']}. {row['notes']}")
        review_path = directory / f"{group}_needs_review.csv"
        for line, row in read_rows(review_path, MASTER_COLUMNS, issues):
            report["review_rows"] += 1
            entry = entries.get(identity(row, group))
            if entry is None:
                issues.append({"file": review_path.name, "row": line, "reason": "missing_review_reference"})
            else:
                entry.reviews.append(row)
                entry.manual.append(f"Needs-review source: {row['confidence_notes'] or row['status_confidence']}")
    if not entries:
        raise DatasetError("No valid schemes; refusing to replace the catalogue.")
    for entry in entries.values():
        _manual_master_conditions(entry)
    report.update(unique_schemes=len(entries), central=sum(key[0] == "CENTRAL" for key in entries),
                  state=sum(key[0] == "STATE" for key in entries),
                  structured_rules=sum(len(entry.rules) for entry in entries.values()),
                  schemes_with_manual_conditions=sum(bool(entry.manual) for entry in entries.values()),
                  categories=dict(Counter(e.metadata["category"] for e in entries.values())),
                  status_confidence=dict(Counter(e.master["status_confidence"] for e in entries.values())),
                  supported_fields=sorted({r.field for e in entries.values() for r, _ in e.rules}))
    return entries, report


def import_dataset(factory, directory: Path = DEFAULT_DIRECTORY, *, overwrite_existing=False) -> dict:
    entries, report = load_dataset(directory)
    report.update(created=0, updated=0, unchanged=0, legacy_archived=0, rules_created=0,
                  skipped_updates=0, conflicts=[])
    with factory.begin() as session:
        report["schemes_before"] = session.scalar(select(func.count()).select_from(Scheme))
        # Archive prototypes on first migration only. Later activation may be an admin edit.
        has_curated = session.scalar(select(Scheme.id).where(Scheme.source_key.startswith("curated:")).limit(1)) is not None
        legacy = session.scalars(select(Scheme).where(Scheme.source_key.startswith("schemes.csv:"))).all()
        for scheme in legacy:
            if scheme.is_active:
                if has_curated and not overwrite_existing:
                    report["skipped_updates"] += 1
                    report["conflicts"].append({"scheme_id": scheme.id, "source_key": scheme.source_key,
                                                "reason": "active_legacy_record_preserved"})
                    continue
                scheme.is_active = False
                report["legacy_archived"] += 1
        for key, entry in entries.items():
            source = source_key(key)
            scheme = session.scalar(select(Scheme).where(Scheme.source_key == source))
            metadata = {**entry.metadata, "manual_conditions": entry.manual,
                        "source_metadata": {"master": entry.master, "reviews": entry.reviews,
                                            "unresolved_rules": entry.unresolved_rules}}
            if scheme is not None:
                stored_rules = list(session.scalars(select(EligibilityRule).where(EligibilityRule.scheme_id == scheme.id)))
                def rule_record(rule):
                    return {name: getattr(rule, name) for name in ("field", "operator", "value", "value_type", "source_metadata")}
                desired_rules = [{**definition.model_dump(), "source_metadata": raw} for definition, raw in entry.rules]
                canonical = lambda rows: sorted(json.dumps(row, sort_keys=True) for row in rows)
                changed_fields = sorted(name for name, value in metadata.items() if getattr(scheme, name) != value)
                rules_differ = canonical(map(rule_record, stored_rules)) != canonical(desired_rules)
                if (changed_fields or rules_differ) and not overwrite_existing:
                    report["skipped_updates"] += 1
                    report["conflicts"].append({"scheme_id": scheme.id, "source_key": source,
                        "reason": "stored_record_differs_from_source", "fields": changed_fields,
                        "rules_differ": rules_differ})
                    continue
            if scheme is None:
                scheme = Scheme(source_key=source, **metadata)
                session.add(scheme)
                session.flush()
                report["created"] += 1
            elif any(getattr(scheme, name) != value for name, value in metadata.items()):
                for name, value in metadata.items():
                    setattr(scheme, name, value)
                report["updated"] += 1
            else:
                report["unchanged"] += 1
            current = list(session.scalars(select(EligibilityRule).where(EligibilityRule.scheme_id == scheme.id)))
            def signature(rule):
                return json.dumps({key: getattr(rule, key) for key in ("field", "operator", "value", "value_type")}, sort_keys=True)
            by_signature = {signature(rule): rule for rule in current}
            wanted = set()
            for definition, raw in entry.rules:
                fingerprint = json.dumps(definition.model_dump(), sort_keys=True)
                wanted.add(fingerprint)
                if fingerprint in by_signature:
                    by_signature[fingerprint].source_metadata = raw
                else:
                    session.add(EligibilityRule(scheme_id=scheme.id, **definition.model_dump(), source_metadata=raw))
                    report["rules_created"] += 1
            for rule in current:
                if signature(rule) not in wanted or by_signature[signature(rule)].id != rule.id:
                    session.delete(rule)
        session.flush()
        report["schemes_after"] = session.scalar(select(func.count()).select_from(Scheme))
        report["active_schemes_after"] = session.scalar(select(func.count()).select_from(Scheme).where(Scheme.is_active.is_(True)))
        report["all_stored_rules"] = session.scalar(select(func.count()).select_from(EligibilityRule))
        report["active_rules_after"] = session.scalar(select(func.count()).select_from(EligibilityRule).join(Scheme).where(Scheme.is_active.is_(True)))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=DEFAULT_DIRECTORY)
    parser.add_argument("--audit-only", action="store_true")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--overwrite-existing", action="store_true",
                        help="Explicitly replace conflicting stored metadata/rules, including admin edits; review and back up first")
    args = parser.parse_args()
    factory = None
    try:
        if args.audit_only:
            _, report = load_dataset(args.directory)
        else:
            factory = create_session_factory()
            report = import_dataset(factory, args.directory, overwrite_existing=args.overwrite_existing)
        output = json.dumps(report, ensure_ascii=False, indent=2)
        if args.report:
            args.report.write_text(output + "\n", encoding="utf-8")
        print(output)
        return 0
    except (DatasetError, ConfigurationError, SQLAlchemyError):
        print("Import failed. Check dataset headers, database configuration and migrations. No partial database import committed.")
        return 1
    finally:
        if factory is not None:
            factory.kw["bind"].dispose()


if __name__ == "__main__":
    raise SystemExit(main())
