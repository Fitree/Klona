import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCENARIO = ROOT / "e2e_test" / "e2e_scenario1.py"
MENTAL_MODEL = ROOT / "e2e_test" / "test_vault" / "MENTAL_MODEL.md"


def load_scenario_module():
    spec = importlib.util.spec_from_file_location("sandbox_e2e_scenario1", SCENARIO)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MentalModelCaptureVerificationTests(unittest.TestCase):
    def setUp(self):
        self.scenario = load_scenario_module()
        self.mental_model = MENTAL_MODEL.read_text(encoding="utf-8")

    def write_capture(self, temp_path, records):
        capture_file = temp_path / "capture.jsonl"
        capture_file.write_text(
            "".join(json.dumps(record) + "\n" for record in records),
            encoding="utf-8",
        )
        self.scenario.CAPTURE_FILE = capture_file

    def chat_record(self, messages, extra=None):
        body = {"messages": messages}
        if extra:
            body.update(extra)
        return {"path": "/v1/chat/completions", "body": json.dumps(body)}

    def test_valid_user_message_contains_only_exact_mental_model_block(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            user_content = f"<Mental_model>\n{self.mental_model}\n</Mental_model>"
            self.write_capture(
                temp_path,
                [self.chat_record([{"role": "user", "content": user_content}])],
            )

            self.scenario.check_mental_model_injection_at_user_message()

    def test_valid_user_message_allows_only_leading_wrapper_newline_before_file_content(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            user_content = f"<Mental_model>\n{self.mental_model}</Mental_model>"
            self.write_capture(
                temp_path,
                [self.chat_record([{"role": "user", "content": user_content}])],
            )

            self.scenario.check_mental_model_injection_at_user_message()

    def test_marker_outside_user_message_does_not_satisfy_injection_check(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            user_content = "User message without mental model"
            self.write_capture(
                temp_path,
                [
                    self.chat_record(
                        [
                            {"role": "system", "content": f"<Mental_model>\n{self.mental_model}\n</Mental_model>"},
                            {"role": "user", "content": user_content},
                        ],
                        extra={"metadata": "KLONA_E2E_MENTAL_MODEL_LOADED_7f4e2d1a9c6b4380b5e21f0d3a8c9e62"},
                    )
                ],
            )

            with self.assertRaises(SystemExit):
                self.scenario.check_mental_model_injection_at_user_message()

    def test_mental_model_block_must_match_file_content_exactly(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            user_content = "<Mental_model>\n# Wrong mental model\n</Mental_model>"
            self.write_capture(
                temp_path,
                [
                    self.chat_record(
                        [
                            {"role": "system", "content": self.mental_model},
                            {"role": "user", "content": user_content},
                        ]
                    )
                ],
            )

            with self.assertRaises(SystemExit):
                self.scenario.check_mental_model_injection_at_user_message()

    def test_scenario_uses_direct_stripped_mental_model_comparison(self):
        content = SCENARIO.read_text(encoding="utf-8")

        self.assertNotIn("_mental_model_matches", content)
        self.assertIn('inner.strip("\\n") == expected_mental_model.strip("\\n")', content)


if __name__ == "__main__":
    unittest.main()
