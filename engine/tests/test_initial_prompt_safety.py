from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace
import unittest


ENGINE_DIR = Path(__file__).resolve().parents[1]
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

import transcribe_npu as engine


class FakeTokenizer:
    def __init__(self, token_count: int) -> None:
        self._token_count = token_count

    def encode(self, _text: str):
        return SimpleNamespace(
            input_ids=SimpleNamespace(shape=[1, self._token_count]),
        )


class FakePipe:
    def __init__(self, token_count: int = 22, max_length: int = 448) -> None:
        self.config = SimpleNamespace(max_length=max_length)
        self.tokenizer = FakeTokenizer(token_count)

    def get_generation_config(self):
        return self.config

    def get_tokenizer(self):
        return self.tokenizer


class InitialPromptSafetyTests(unittest.TestCase):
    def test_empty_initial_prompt_has_no_warning(self) -> None:
        plan = engine.prepare_initial_prompt(FakePipe(), "   ")

        self.assertFalse(plan.requested)
        self.assertIsNone(plan.warning)

    def test_short_initial_prompt_is_token_validated_and_disabled(self) -> None:
        plan = engine.prepare_initial_prompt(FakePipe(token_count=22), "회의 용어")

        self.assertTrue(plan.requested)
        self.assertEqual(plan.token_count, 22)
        self.assertEqual(plan.maximum_token_count, 384)
        self.assertIn("안전하게 제외", plan.warning)

    def test_overlong_initial_prompt_is_rejected_before_generation(self) -> None:
        plan = engine.prepare_initial_prompt(FakePipe(token_count=385), "회의 용어")

        self.assertEqual(plan.token_count, 385)
        self.assertEqual(plan.maximum_token_count, 384)
        self.assertIn("허용 길이를 초과", plan.warning)

    def test_generation_config_does_not_assign_initial_prompt(self) -> None:
        pipe = FakePipe()

        config = engine.configure_generation(pipe, "<|ko|>", 1, "")

        self.assertIs(config, pipe.config)
        self.assertFalse(hasattr(config, "initial_prompt"))


if __name__ == "__main__":
    unittest.main()
