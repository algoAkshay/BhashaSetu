"""Explicit CSV import only: python -m backend.db.seed [--csv PATH]."""
import argparse
import csv
from dataclasses import asdict, dataclass, field
import hashlib
import json
import logging
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from backend.config import ConfigurationError
from backend.db.session import create_session_factory
from backend.normalization import RuleValidationError, normalize_value
from backend.repositories.scheme_repository import SchemeRepository
from backend.schemas import RuleDefinition
from backend.services.scheme_service import SchemeService

logger = logging.getLogger(__name__)
DEFAULT_CSV = Path(__file__).resolve().parents[2] / "database" / "schemes.csv"
COLUMNS = ["scheme_name", "min_age", "max_age", "gender", "max_income"]


@dataclass
class SeedReport:
    processed: int = 0
    created: int = 0
    skipped: int = 0
    rules_created: int = 0
    errors: list[str] = field(default_factory=list)


class SeedError(ValueError):
    def __init__(self, report: SeedReport):
        super().__init__("Seed failed; no changes committed.")
        self.report = report


def parse_row(row: dict):
    if set(row) != set(COLUMNS) or any(value is None for value in row.values()):
        raise ValueError("Incorrect number of CSV columns.")
    name = row["scheme_name"].strip()
    if not name:
        raise ValueError("Scheme name is empty.")
    minimum, maximum, income = [normalize_value(row[key], "integer")
                                for key in ("min_age", "max_age", "max_income")]
    if not 0 <= minimum <= maximum or income < 0:
        raise ValueError("Invalid age range or income threshold.")
    gender = row["gender"].strip()
    if gender not in {"Any", "Male", "Female"}:
        raise ValueError("Unrecognized CSV gender restriction.")
    rules = [
        RuleDefinition(field="age", operator=">=", value=minimum, value_type="integer"),
        RuleDefinition(field="age", operator="<=", value=maximum, value_type="integer"),
        RuleDefinition(field="annual_income", operator="<=", value=income, value_type="integer"),
    ]
    if gender != "Any":
        rules.append(RuleDefinition(field="gender", operator="==", value=gender, value_type="string"))
    source_key = "schemes.csv:" + hashlib.sha256(name.encode("utf-8")).hexdigest()
    return name, source_key, rules


def seed_database(session_factory, path: Path = DEFAULT_CSV) -> SeedReport:
    report = SeedReport()
    parsed = []
    keys = set()
    try:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream, strict=True)
            if reader.fieldnames != COLUMNS:
                report.errors.append("CSV header must match: " + ",".join(COLUMNS))
                raise SeedError(report)
            for row in reader:
                report.processed += 1
                try:
                    entry = parse_row(row)
                    if entry[1] in keys:
                        raise ValueError("Duplicate scheme name in source CSV.")
                    keys.add(entry[1])
                    parsed.append(entry)
                except ValueError as error:
                    report.errors.append(f"Row {reader.line_num}: {error}")
    except (OSError, UnicodeError, csv.Error) as error:
        report.errors.append(f"Cannot read seed CSV ({type(error).__name__}).")
    if report.errors:
        logger.error("seed_validation_failed rows=%s errors=%s", report.processed, len(report.errors))
        raise SeedError(report)
    try:
        with session_factory.begin() as session:
            repository = SchemeRepository(session)
            service = SchemeService(session)
            for name, source_key, rules in parsed:
                if repository.get_by_source_key(source_key) is not None:
                    report.skipped += 1
                    continue
                service.create_with_rules(name=name, source_key=source_key, rules=rules)
                report.created += 1
                report.rules_created += len(rules)
    except (SQLAlchemyError, RuleValidationError):
        report.created = report.rules_created = 0
        report.errors.append("Database import failed; the entire import was rolled back.")
        logger.error("seed_database_failed")
        raise SeedError(report) from None
    logger.info("seed_complete processed=%s created=%s skipped=%s rules_created=%s",
                report.processed, report.created, report.skipped, report.rules_created)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, help="Explicit legacy CSV import; default imports the researched dataset")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    try:
        factory = create_session_factory()
    except ConfigurationError as error:
        print(json.dumps({"error": str(error)}))
        return 1
    try:
        try:
            if args.csv is None:
                from backend.db.import_dataset import DatasetError, import_dataset
                try:
                    print(json.dumps(import_dataset(factory)))
                except (DatasetError, SQLAlchemyError):
                    print(json.dumps({"error": "Curated import failed. Check dataset and migrations; no partial import committed."}))
                    return 1
                return 0
            report = seed_database(factory, args.csv)
        except SeedError as error:
            print(json.dumps(asdict(error.report)))
            return 1
        print(json.dumps(asdict(report)))
        return 0
    finally:
        factory.kw["bind"].dispose()


if __name__ == "__main__":
    raise SystemExit(main())
