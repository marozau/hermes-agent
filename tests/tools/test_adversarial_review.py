#!/usr/bin/env python3
"""
Tests for adversarial_review() — the parameterized adversarial verification
helper embedded in _COMMON_HELPERS (tools/code_execution_tool.py).

The adversarial_review() function lives inside the _COMMON_HELPERS triple-quoted
string in code_execution_tool.py. It calls delegate_task(tasks=...), which is
a dynamically-generated function available in the execute_code sandbox — not
a real Python module. To test, we extract adversarial_review from the string
and execute it in a namespace with a mock delegate_task injected.

Run with:  python -m pytest tests/tools/test_adversarial_review.py -v
"""

import json
import unittest

from tools.code_execution_tool import _COMMON_HELPERS


def _load_adversarial_review(mock_delegate_task):
    """Execute the _COMMON_HELPERS code to define adversarial_review in a namespace.

    Injects mock_delegate_task so that adversarial_review() can call it.
    Returns the adversarial_review function, or None if not found.
    """
    ns = {
        "json": json,
        "shlex": __import__("shlex"),
        "time": __import__("time"),
        "delegate_task": mock_delegate_task,  # injected — not from a real module
    }
    exec(_COMMON_HELPERS, ns)
    return ns.get("adversarial_review")


class TestAdversarialReview(unittest.TestCase):
    """Unit tests for adversarial_review() logic."""

    def test_all_results_pass(self):
        """When no reviewers find defects, all results pass through."""
        results = [
            {"summary": "Result 0: All clear."},
            {"summary": "Result 1: Everything verified."},
        ]
        task_spec = "Research X and Y with factual accuracy."

        def mock_delegate_task(*, tasks=None, goal=None, **kwargs):
            return [
                {"summary": "RESULT 0: CLEAN\nRESULT 1: CLEAN\nVERDICT: all clean"},
                {"summary": "RESULT 0: CLEAN\nRESULT 1: CLEAN\nVERDICT: no defects"},
            ]

        adversarial_review = _load_adversarial_review(mock_delegate_task)
        self.assertIsNotNone(adversarial_review)
        verdict = adversarial_review(results, task_spec, num_reviewers=2)

        self.assertEqual(len(verdict["passed"]), 2)
        self.assertEqual(len(verdict["defects"]), 0)
        self.assertEqual(len(verdict["all_reviews"]), 2)

    def test_defect_caught_and_excluded(self):
        """A single defective result is excluded from passed."""
        results = [
            {"summary": "Result 0: The sky is green."},  # factual error
            {"summary": "Result 1: The sky is blue."},
        ]
        task_spec = "Report the color of the sky accurately."

        def mock_delegate_task(*, tasks=None, goal=None, **kwargs):
            return [{"summary": "RESULT 0: DEFECTIVE — the sky is not green\nRESULT 1: CLEAN\nVERDICT: result 0 has factual error"}]

        adversarial_review = _load_adversarial_review(mock_delegate_task)
        verdict = adversarial_review(results, task_spec, num_reviewers=1)

        self.assertEqual(verdict["passed"], ["Result 1: The sky is blue."])
        self.assertEqual(len(verdict["defects"]), 1)
        self.assertEqual(verdict["defects"][0]["result_index"], 0)
        self.assertIn("green", verdict["defects"][0]["finding"])

    def test_multiple_reviewers_agree_on_defect(self):
        """Two reviewers both flag the same result — it's excluded once."""
        results = [
            {"summary": "Result 0: Paris is in Germany."},
            {"summary": "Result 1: London is in the UK."},
        ]
        task_spec = "Report capital cities and their countries accurately."

        def mock_delegate_task(*, tasks=None, goal=None, **kwargs):
            return [
                {"summary": "RESULT 0: DEFECTIVE — Paris is not in Germany\nRESULT 1: CLEAN\nVERDICT: result 0 wrong"},
                {"summary": "RESULT 0: DEFECTIVE — capital mismatch\nRESULT 1: CLEAN\nVERDICT: result 0 wrong"},
            ]

        adversarial_review = _load_adversarial_review(mock_delegate_task)
        verdict = adversarial_review(results, task_spec, num_reviewers=2)

        self.assertEqual(verdict["passed"], ["Result 1: London is in the UK."])
        self.assertEqual(len(verdict["defects"]), 2)

    def test_zero_reviewers_passes_all(self):
        """review_agents=0 means all results pass through unreviewed."""
        results = [
            {"summary": "Result 0: potentially wrong."},
            {"summary": "Result 1: also potentially wrong."},
        ]
        task_spec = "Something."

        def mock_delegate_task(*, tasks=None, goal=None, **kwargs):
            return []

        adversarial_review = _load_adversarial_review(mock_delegate_task)
        verdict = adversarial_review(results, task_spec, num_reviewers=0)

        self.assertEqual(len(verdict["passed"]), 2)
        self.assertEqual(len(verdict["defects"]), 0)

    def test_defect_parsing_case_insensitive(self):
        """Defect detection is case-insensitive."""
        results = [{"summary": "Flawed result."}]
        task_spec = "Task."

        def mock_delegate_task(*, tasks=None, goal=None, **kwargs):
            return [{"summary": "RESULT 0: defective — an error was found\nVERDICT: flawed"}]

        adversarial_review = _load_adversarial_review(mock_delegate_task)
        verdict = adversarial_review(results, task_spec, num_reviewers=1)

        self.assertEqual(len(verdict["passed"]), 0)
        self.assertEqual(len(verdict["defects"]), 1)

    def test_all_defective_excludes_everything(self):
        """When all results have defects, passed is empty."""
        results = [
            {"summary": "Wrong A."},
            {"summary": "Wrong B."},
            {"summary": "Wrong C."},
        ]
        task_spec = "Task."

        def mock_delegate_task(*, tasks=None, goal=None, **kwargs):
            return [{"summary": (
                "RESULT 0: DEFECTIVE — wrong\n"
                "RESULT 1: DEFECTIVE — also wrong\n"
                "RESULT 2: DEFECTIVE — still wrong\n"
                "VERDICT: everything is wrong"
            )}]

        adversarial_review = _load_adversarial_review(mock_delegate_task)
        verdict = adversarial_review(results, task_spec, num_reviewers=1)

        self.assertEqual(verdict["passed"], [])
        self.assertEqual(len(verdict["defects"]), 3)

    def test_acceptance_scenario_review_agents_2(self):
        """Acceptance test: review_agents=2 catches a factual error.

        Scenario: Three subagents research programming language creation years.
        One produces a factual error (Python created in 1985 instead of 1991).
        Two adversarial reviewers independently catch it.
        The erroneous result is excluded from the final answer.
        """
        results = [
            {"summary": "Python was created by Guido van Rossum in 1985."},  # ERROR: actually 1991
            {"summary": "JavaScript was created by Brendan Eich in 1995."},  # CORRECT
            {"summary": "Rust was created by Graydon Hoare, first stable release 2015."},  # CORRECT
        ]
        task_spec = (
            "Research the creation years of programming languages Python, "
            "JavaScript, and Rust. All dates must be historically accurate."
        )

        def mock_delegate_task(*, tasks=None, goal=None, **kwargs):
            # Two reviewers, both catch the Python date error
            return [
                {"summary": (
                    "RESULT 0: DEFECTIVE — Python was created in 1991, not 1985. "
                    "Guido van Rossum started work in 1989, first release 1991.\n"
                    "RESULT 1: CLEAN\n"
                    "RESULT 2: CLEAN\n"
                    "VERDICT: result 0 has a date error"
                )},
                {"summary": (
                    "RESULT 0: DEFECTIVE — Python's first public release was 1991 "
                    "(0.9.0). 1985 is before Python existed.\n"
                    "RESULT 1: CLEAN\n"
                    "RESULT 2: CLEAN\n"
                    "VERDICT: result 0 has incorrect creation year"
                )},
            ]

        adversarial_review = _load_adversarial_review(mock_delegate_task)
        verdict = adversarial_review(results, task_spec, num_reviewers=2)

        # The erroneous Python result (index 0) should be excluded
        self.assertEqual(
            verdict["passed"],
            [
                "JavaScript was created by Brendan Eich in 1995.",
                "Rust was created by Graydon Hoare, first stable release 2015.",
            ],
            "The defective result (Python 1985) should be excluded from passed"
        )
        self.assertEqual(len(verdict["defects"]), 2, "Both reviewers should flag the error")
        for d in verdict["defects"]:
            self.assertEqual(d["result_index"], 0, "Defects should reference result index 0")


if __name__ == "__main__":
    unittest.main()
