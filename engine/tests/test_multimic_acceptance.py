from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest


ENGINE_DIR = Path(__file__).resolve().parents[1]
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

import run_multimic_acceptance as acceptance


class MultiMicAcceptanceMatrixTests(unittest.TestCase):
    def test_matrix_covers_every_documented_scenario_and_resolves_tests(self) -> None:
        expected = [f"MM-{index:02d}" for index in range(1, 10)]

        self.assertEqual(list(acceptance.SCENARIO_TESTS), expected)
        self.assertEqual(list(acceptance.SCENARIO_CONDITIONS), expected)
        suite = acceptance._load_suite(expected)
        self.assertEqual(
            suite.countTestCases(),
            sum(len(names) for names in acceptance.SCENARIO_TESTS.values()),
        )

    def test_matrix_metadata_is_strict_json_and_numeric_targets_are_measured(
        self,
    ) -> None:
        payload = {
            "tests": acceptance.SCENARIO_TESTS,
            "conditions": acceptance.SCENARIO_CONDITIONS,
        }

        json.dumps(payload, allow_nan=False)
        self.assertEqual(set(acceptance.MEASUREMENT_BUILDERS), {"MM-01", "MM-02"})


if __name__ == "__main__":
    unittest.main()
