from decimal import Decimal
from types import SimpleNamespace
import unittest

from backend.normalization import InputValidationError, RuleValidationError
from backend.rules import evaluate_rule, evaluate_scheme
from backend.schemas import RuleDefinition


def rule(field="age", operator=">=", value=18, value_type="integer"):
    return dict(field=field, operator=operator, value=value, value_type=value_type)


class RuleTests(unittest.TestCase):
    def assert_operator(self, op, threshold, passing, failing):
        definition = rule(operator=op, value=threshold)
        self.assertTrue(evaluate_rule(definition, {"age": passing}).passed)
        self.assertFalse(evaluate_rule(definition, {"age": failing}).passed)

    def test_equal(self):
        self.assert_operator("==", 18, 18, 19)

    def test_not_equal(self):
        self.assert_operator("!=", 18, 19, 18)

    def test_greater_than(self):
        self.assert_operator(">", 18, 19, 18)

    def test_greater_or_equal(self):
        self.assert_operator(">=", 18, 18, 17)

    def test_less_than(self):
        self.assert_operator("<", 18, 17, 18)

    def test_less_or_equal(self):
        self.assert_operator("<=", 18, 18, 19)

    def test_in(self):
        self.assert_operator("IN", [18, 21], 21, 19)

    def test_not_in(self):
        self.assert_operator("NOT_IN", [18, 21], 19, 21)

    def test_membership_normalizes_strings(self):
        definition = rule("state", "IN", ["Uttar Pradesh", "Bihar"], "string")
        self.assertTrue(evaluate_rule(definition, {"state": "  UTTAR PRADESH "}).passed)

    def test_numeric_strings_are_explicitly_supported(self):
        self.assertTrue(evaluate_rule(rule(value="18"), {"age": " 18 "}).passed)
        definition = rule("annual_income", "<=", "200000.25", "decimal")
        check = evaluate_rule(definition, {"annual_income": "200000.25"})
        self.assertTrue(check.passed)
        self.assertEqual(check.actual, Decimal("200000.25"))

    def test_malformed_numeric_user_values(self):
        for field, value in [("age", "18years"), ("age", 18.5), ("age", True),
                             ("age", "18.0"), ("annual_income", "1,00,000"),
                             ("annual_income", "NaN"), ("annual_income", float("inf")),
                             ("annual_income", True), ("annual_income", -1), ("age", -1)]:
            with self.subTest(field=field, value=value), self.assertRaises(InputValidationError):
                evaluate_rule(rule(), {field: value})

    def test_small_decimal_threshold_survives_json_round_trip(self):
        definition = RuleDefinition(**rule("annual_income", "<=", Decimal("1E-20"), "decimal"))
        self.assertEqual(definition.value, "0.00000000000000000001")
        persisted = definition.model_dump(mode="json")
        self.assertTrue(evaluate_rule(persisted, {"annual_income": "0.00000000000000000001"}).passed)
        self.assertFalse(evaluate_rule(persisted, {"annual_income": "0.00000000000000000002"}).passed)

    def test_invalid_rule_definitions_fail_even_with_missing_user_field(self):
        for definition in [rule(operator="EXEC"), rule(value=None), rule(value="oops"),
                           rule(field="unknown"), rule(value_type="string"),
                           rule(operator="IN", value=18), rule(operator="IN", value=[]),
                           rule(operator="IN", value=[18, None]), rule(value=True),
                           rule(value=-1), rule("state", ">", "Bihar", "string")]:
            with self.subTest(rule=definition), self.assertRaises(RuleValidationError):
                evaluate_rule(definition, {})

    def test_boolean_values_are_strict(self):
        definition = rule("is_disabled", "==", True, "boolean")
        self.assertTrue(evaluate_rule(definition, {"is_disabled": True}).passed)
        self.assertFalse(evaluate_rule(definition, {"is_disabled": False}).passed)
        for invalid in ["true", "false", 1, 0]:
            with self.subTest(value=invalid), self.assertRaises(InputValidationError):
                evaluate_rule(definition, {"is_disabled": invalid})

    def test_null_and_blank_are_missing(self):
        for value in [None, "", "   "]:
            with self.subTest(value=value):
                self.assertIsNone(evaluate_rule(rule(), {"age": value}).passed)

    def test_unknown_attribute_rejected(self):
        with self.assertRaises(InputValidationError):
            evaluate_rule(rule(), {"income": 200000})


class SchemeEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.scheme = SimpleNamespace(id=1, name="Example", is_active=True)
        self.rules = [rule(value=18), rule(operator="<=", value=60),
                      rule("annual_income", "<=", 200000, "integer")]

    def evaluate(self, attributes, rules=None):
        return evaluate_scheme(self.scheme, self.rules if rules is None else rules, attributes)

    def test_age_minimum_and_income_maximum_inclusive(self):
        result = self.evaluate({"age": 18, "annual_income": 200000})
        self.assertEqual(result.status, "ELIGIBLE")
        self.assertEqual(result.missing_fields, [])
        self.assertTrue(all(check.passed for check in result.checks))

    def test_age_maximum_inclusive(self):
        self.assertEqual(self.evaluate({"age": 60, "annual_income": 200000}).status, "ELIGIBLE")

    def test_one_rule_fails(self):
        result = self.evaluate({"age": 61, "annual_income": 200000})
        self.assertEqual(result.status, "NOT_ELIGIBLE")
        self.assertEqual([check.passed for check in result.checks], [True, False, True])

    def test_multiple_rules_fail(self):
        result = self.evaluate({"age": 61, "annual_income": 200001})
        self.assertEqual(result.status, "NOT_ELIGIBLE")
        self.assertEqual(sum(check.passed is False for check in result.checks), 2)

    def test_one_missing_field(self):
        result = self.evaluate({"age": 60})
        self.assertEqual(result.status, "NEED_MORE_INFORMATION")
        self.assertEqual(result.missing_fields, ["annual_income"])
        self.assertIsNone(result.checks[-1].actual)
        self.assertIsNone(result.checks[-1].passed)

    def test_multiple_missing_fields_deduplicated_no_defaults(self):
        attributes = {}
        result = self.evaluate(attributes)
        self.assertEqual(result.status, "NEED_MORE_INFORMATION")
        self.assertEqual(result.missing_fields, ["age", "annual_income"])
        self.assertEqual(attributes, {})
        self.assertTrue(all(check.actual is None for check in result.checks))

    def test_known_failure_takes_precedence_over_missing(self):
        result = self.evaluate({"age": 61})
        self.assertEqual(result.status, "NOT_ELIGIBLE")
        self.assertEqual(result.missing_fields, ["annual_income"])

    def test_no_rules_does_not_grant_eligibility(self):
        result = self.evaluate({}, rules=[])
        self.assertEqual(result.status, "NEED_MORE_INFORMATION")
        self.assertEqual(result.reason, "no_rules")

    def test_inactive_scheme_is_excluded(self):
        self.scheme.is_active = False
        result = self.evaluate({"age": 40, "annual_income": 100000})
        self.assertEqual(result.status, "NOT_ELIGIBLE")
        self.assertEqual(result.reason, "inactive_scheme")
