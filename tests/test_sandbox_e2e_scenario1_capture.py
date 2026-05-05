import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCENARIO = ROOT / "e2e_test" / "e2e_scenario1.py"
KLONA_MEMORY_MENTAL_MODEL = ROOT / "e2e_test" / "test_vault" / "KLONA_MEMORY_MENTAL_MODEL.md"


def load_scenario_module():
    spec = importlib.util.spec_from_file_location("sandbox_e2e_scenario1", SCENARIO)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class KlonaMemoryMentalModelCaptureVerificationTests(unittest.TestCase):
    def setUp(self):
        self.scenario = load_scenario_module()
        self.klona_memory_mental_model = KLONA_MEMORY_MENTAL_MODEL.read_text(encoding="utf-8")

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

    def test_valid_user_message_contains_only_exact_klona_memory_mental_model_block(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            user_content = f"<Klona_memory_mental_model>\n{self.klona_memory_mental_model}</Klona_memory_mental_model>\nHello from test"
            self.write_capture(
                temp_path,
                [self.chat_record([{"role": "user", "content": user_content}])],
            )

            self.scenario.check_klona_memory_mental_model_injection_at_user_message("Hello from test")

    def test_scans_until_user_message_containing_requested_text(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            wrong_content = f"<Klona_memory_mental_model>\n# Wrong Klona memory mental model\n</Klona_memory_mental_model>\nOther message"
            matching_content = f"<Klona_memory_mental_model>\n{self.klona_memory_mental_model}</Klona_memory_mental_model>\nRequested message"
            self.write_capture(
                temp_path,
                [
                    self.chat_record([{"role": "user", "content": wrong_content}]),
                    self.chat_record([{"role": "user", "content": matching_content}]),
                ],
            )

            self.scenario.check_klona_memory_mental_model_injection_at_user_message("Requested message")

    def test_scans_all_requested_messages_until_one_has_klona_memory_mental_model(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            first_match_without_model = "Requested duplicate message"
            second_match_with_model = f"<Klona_memory_mental_model>\n{self.klona_memory_mental_model}</Klona_memory_mental_model>\nRequested duplicate message"
            self.write_capture(
                temp_path,
                [
                    self.chat_record([{"role": "user", "content": first_match_without_model}]),
                    self.chat_record([{"role": "user", "content": second_match_with_model}]),
                ],
            )

            self.scenario.check_klona_memory_mental_model_injection_at_user_message("Requested duplicate message")

    def test_marker_outside_user_message_does_not_satisfy_klona_memory_mental_model_injection_check(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            user_content = "User message without Klona memory mental model"
            self.write_capture(
                temp_path,
                [
                    self.chat_record(
                        [
                            {"role": "system", "content": f"<Klona_memory_mental_model>\n{self.klona_memory_mental_model}\n</Klona_memory_mental_model>"},
                            {"role": "user", "content": user_content},
                        ],
                        extra={"metadata": "KLONA_E2E_KLONA_MEMORY_MENTAL_MODEL_LOADED_7f4e2d1a9c6b4380b5e21f0d3a8c9e62"},
                    )
                ],
            )

            with self.assertRaises(SystemExit):
                self.scenario.check_klona_memory_mental_model_injection_at_user_message("User message without Klona memory mental model")

    def test_klona_memory_mental_model_block_must_match_file_content_exactly(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            user_content = "<Klona_memory_mental_model>\n# Wrong Klona memory mental model\n</Klona_memory_mental_model>"
            self.write_capture(
                temp_path,
                [
                    self.chat_record(
                        [
                            {"role": "system", "content": self.klona_memory_mental_model},
                            {"role": "user", "content": user_content},
                        ]
                    )
                ],
            )

            with self.assertRaises(SystemExit):
                self.scenario.check_klona_memory_mental_model_injection_at_user_message("Wrong Klona memory mental model")

    def test_scenario_uses_exact_klona_memory_mental_model_block_comparison(self):
        content = SCENARIO.read_text(encoding="utf-8")

        self.assertNotIn("_klona_memory_mental_model_matches", content)
        self.assertIn('expected_block = f"<Klona_memory_mental_model>\\n{expected_klona_memory_mental_model}</Klona_memory_mental_model>"', content)
        self.assertIn("if expected_block in content:", content)


if __name__ == "__main__":
    unittest.main()
