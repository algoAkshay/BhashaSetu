"""Explicit live smoke test; imports/pytest collection never make API calls."""
import argparse
import json
from pathlib import Path
import sys
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.config import ConfigurationError, LLMSettings
from backend.profile_schemas import ProfileExtractionResult, UserProfile
from backend.services.profile_extraction_service import GeminiProfileExtractor, ProfileExtractionError


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("text", help="Transcript to send to Gemini (explicit live API call)")
    parser.add_argument("--current-profile", default="{}", help="Known profile JSON for context")
    parser.add_argument("--expected", help="Exact expected extracted JSON; mismatch exits nonzero")
    parser.add_argument("--eligibility", action="store_true",
                        help="Also evaluate the merged profile using DATABASE_URL and the existing service")
    args = parser.parse_args()
    started = perf_counter()
    try:
        settings = LLMSettings.from_environment()
        profile = UserProfile.model_validate_json(args.current_profile)
        expected = ProfileExtractionResult.model_validate_json(args.expected) if args.expected else None
    except (ConfigurationError, ValueError):
        print("Invalid configuration or profile JSON. Check LLM settings and schema.")
        return 2
    print(f"provider: {settings.provider}\nmodel: {settings.model}\ninput: {args.text}")
    try:
        result = GeminiProfileExtractor(settings).extract_profile(args.text, profile)
    except ProfileExtractionError as error:
        print(f"extraction_failed: {error.reason}")
        print(f"duration_seconds: {perf_counter() - started:.3f}")
        return 1
    print("validated_profile:", result.model_dump_json())
    print(f"duration_seconds: {perf_counter() - started:.3f}")
    if expected is not None and result != expected:
        print("expected_profile_mismatch")
        return 1
    if expected is not None:
        print("expected_profile_matched")
    if args.eligibility:
        from backend.db.session import create_session_factory
        from backend.services.eligibility_service import EligibilityService

        factory = None
        try:
            merged = profile.model_dump() | result.model_dump(exclude_none=True)
            factory = create_session_factory()
            with factory() as session:
                results = EligibilityService(session).evaluate_all_schemes(merged)
            print("eligibility:", json.dumps([
                {"scheme_id": r.scheme_id, "status": r.status, "missing_fields": r.missing_fields}
                for r in results], ensure_ascii=False))
            if not results:
                print("eligibility_failed: no_active_schemes")
                return 1
        except Exception:
            print("eligibility_failed: check_database_configuration_and_migrations")
            return 1
        finally:
            if factory is not None:
                factory.kw["bind"].dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
