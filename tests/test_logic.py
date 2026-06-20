import unittest

from backend.logic import (
    extract_fields,
    find_eligible_schemes,
    get_session_state,
    reset_session,
    apply_defaults_if_needed,
)


class LogicTests(unittest.TestCase):
    def setUp(self):
        reset_session("test-a")
        reset_session("test-b")

    def test_extracts_decimal_lakh_income_without_using_age(self):
        extract_fields("मैं 32 साल का पुरुष हूँ और मेरी सालाना आय 1.5 लाख है", "test-a")

        self.assertEqual(
            get_session_state("test-a"),
            {
                "age": 32,
                "gender": "Male",
                "income": 150000,
                "attempts": 1,
                "finalized": False,
            },
        )

    def test_extracts_one_digit_age(self):
        extract_fields("मेरी बेटी 5 साल की लड़की है और आय 100000 है", "test-a")

        state = get_session_state("test-a")
        self.assertEqual(state["age"], 5)
        self.assertEqual(state["gender"], "Female")
        self.assertEqual(state["income"], 100000)

    def test_hoon_does_not_force_male_gender(self):
        extract_fields("मैं 32 साल की महिला hoon और आय 150000 है", "test-a")

        self.assertEqual(get_session_state("test-a")["gender"], "Female")

    def test_csv_scheme_lookup(self):
        extract_fields("मैं 32 साल का पुरुष हूँ और मेरी आय 150000 है", "test-a")
        extract_fields("हाँ", "test-a")
        apply_defaults_if_needed("test-a")

        schemes = find_eligible_schemes("test-a")

        self.assertIn("प्रधानमंत्री आवास योजना", schemes)
        self.assertIn("आयुष्मान भारत योजना", schemes)

    def test_sessions_are_independent(self):
        extract_fields("मैं 32 साल का पुरुष हूँ और आय 150000 है", "test-a")
        extract_fields("मैं 5 साल की लड़की हूँ और आय 100000 है", "test-b")

        self.assertEqual(get_session_state("test-a")["age"], 32)
        self.assertEqual(get_session_state("test-b")["age"], 5)

    def test_defaults_apply_after_second_attempt(self):
        extract_fields("मुझे योजना चाहिए", "test-a")
        extract_fields("पता नहीं", "test-a")
        apply_defaults_if_needed("test-a")

        self.assertEqual(get_session_state("test-a")["age"], 30)
        self.assertEqual(get_session_state("test-a")["gender"], "Male")
        self.assertEqual(get_session_state("test-a")["income"], 100000)
        self.assertTrue(get_session_state("test-a")["finalized"])


if __name__ == "__main__":
    unittest.main()
