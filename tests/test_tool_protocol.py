from __future__ import annotations

import unittest

from dairack.tool_protocol import TOOL_REGISTRY, decode_text_tool_call, strip_tool_protocol


class ToolRegistryTests(unittest.TestCase):
    def test_registry_is_the_source_of_schema_and_validation_rules(self) -> None:
        schemas = {item["function"]["name"]: item["function"] for item in TOOL_REGISTRY.schemas()}

        self.assertEqual(schemas["read_file"]["parameters"]["required"], ["path"])
        self.assertEqual(schemas["index_project"]["parameters"]["required"], [])
        self.assertIn("path", schemas["search_project"]["parameters"]["properties"])
        self.assertNotIn("analyze_image", schemas)

        call, error = TOOL_REGISTRY.validate({"name": "index_project"})
        self.assertFalse(error)
        self.assertEqual(call, {"name": "index_project", "reason": ""})

        call, error = TOOL_REGISTRY.validate({"name": "read_file"})
        self.assertIsNone(call)
        self.assertIn("missing path", error)

    def test_registry_owns_complete_action_presentation_metadata(self) -> None:
        allowed_risks = {"read", "write", "system", "local", "network", "coordinator"}
        for name in TOOL_REGISTRY.names:
            with self.subTest(name=name):
                presentation = TOOL_REGISTRY.presentation(name)
                self.assertEqual(presentation["name"], name)
                self.assertTrue(presentation["display_name"])
                self.assertTrue(presentation["activity"])
                self.assertIn(presentation["risk"], allowed_risks)
                self.assertIsInstance(presentation["interruptible"], bool)

        self.assertEqual(TOOL_REGISTRY.presentation("bash")["name"], "shell")
        self.assertEqual(TOOL_REGISTRY.presentation("unknown")["risk"], "system")

    def test_registry_normalizes_aliases_nested_arguments_and_types(self) -> None:
        call, error = TOOL_REGISTRY.validate(
            {"function": {"name": "bash", "arguments": '{"command":"pwd","reason":"inspect"}'}}
        )
        self.assertFalse(error)
        self.assertEqual(call, {"name": "shell", "reason": "inspect", "cmd": "pwd"})

        call, error = TOOL_REGISTRY.validate({"name": "read_file", "arguments": {"path": "app.py", "line": 12}})
        self.assertFalse(error)
        self.assertEqual(call, {"name": "read_file", "reason": "", "path": "app.py", "line": "12"})

    def test_registry_rejects_invalid_arguments_and_closed_enums(self) -> None:
        call, error = TOOL_REGISTRY.validate({"name": "read_file", "arguments": "not json"})
        self.assertIsNone(call)
        self.assertIn("invalid JSON", error)

        call, error = TOOL_REGISTRY.validate({"name": "consult_specialist", "task": "review", "quality": "maximum"})
        self.assertIsNone(call)
        self.assertIn("must be one of", error)

    def test_decoder_normalizes_protocol_families_through_the_registry(self) -> None:
        fixtures = (
            '<tool_call>{"name":"list_dir","path":"."}</tool_call>',
            '{"function":{"name":"list_dir","arguments":{"path":"."}}}',
            '[TOOL_CALLS]list_dir[ARGS]{"path":"."}</s>',
            'list_dir{"path":"."}',
        )
        for payload in fixtures:
            with self.subTest(payload=payload):
                call, error, recognized = decode_text_tool_call(payload)
                self.assertTrue(recognized)
                self.assertFalse(error)
                self.assertEqual(call, {"name": "list_dir", "reason": "", "path": "."})
                self.assertEqual(strip_tool_protocol(payload), "")

    def test_decoder_distinguishes_prose_from_malformed_action_protocol(self) -> None:
        call, error, recognized = decode_text_tool_call("This is an ordinary answer.")
        self.assertIsNone(call)
        self.assertFalse(error)
        self.assertFalse(recognized)

        call, error, recognized = decode_text_tool_call("[TOOL_CALLS] malformed")
        self.assertIsNone(call)
        self.assertTrue(recognized)
        self.assertIn("unrecognized", error)

    def test_unterminated_tool_markup_is_stripped_in_linear_time(self) -> None:
        payload = "Visible answer.\n<tool_call>" + " \n" * 8000
        self.assertEqual(strip_tool_protocol(payload), "Visible answer.")

    def test_deeply_nested_json_fails_closed(self) -> None:
        payload = "<tool>" + "[" * 10000 + "]" * 10000 + "</tool>"
        call, error, recognized = decode_text_tool_call(payload)
        self.assertIsNone(call)
        self.assertTrue(recognized)
        self.assertIn("invalid JSON", error)

        nested_arguments = '{"a":' * 10000 + "1" + "}" * 10000
        call, error = TOOL_REGISTRY.validate({"name": "shell", "arguments": nested_arguments})
        self.assertIsNone(call)
        self.assertIn("invalid JSON", error)

    def test_complete_tool_envelopes_are_removed_without_suffix_rescans(self) -> None:
        payload = "Visible\n" + "<tool>a</tool>" * 20000
        self.assertEqual(strip_tool_protocol(payload), "Visible")


if __name__ == "__main__":
    unittest.main()
