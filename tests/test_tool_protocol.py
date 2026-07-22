from __future__ import annotations

import unittest

from dairack.tool_protocol import TOOL_REGISTRY, decode_text_tool_call, strip_tool_protocol


class ToolRegistryTests(unittest.TestCase):
    def test_registry_is_the_source_of_schema_and_validation_rules(self) -> None:
        schemas = {item["function"]["name"]: item["function"] for item in TOOL_REGISTRY.schemas()}

        self.assertEqual(schemas["read_file"]["parameters"]["required"], ["path"])
        self.assertEqual(schemas["index_project"]["parameters"]["required"], [])
        self.assertEqual(schemas["hardware_status"]["parameters"]["required"], [])
        self.assertEqual(schemas["find_paths"]["parameters"]["required"], ["query", "path"])
        self.assertIn("path", schemas["search_project"]["parameters"]["properties"])
        self.assertNotIn("analyze_image", schemas)

        call, error = TOOL_REGISTRY.validate({"name": "index_project"})
        self.assertFalse(error)
        self.assertEqual(call, {"name": "index_project", "reason": ""})

        call, error = TOOL_REGISTRY.validate({"name": "read_file"})
        self.assertIsNone(call)
        self.assertIn("missing path", error)

        call, error = TOOL_REGISTRY.validate(
            {"name": "find_paths", "arguments": {"query": "Lockout", "path": "C:/Users/example"}}
        )
        self.assertFalse(error)
        self.assertEqual(call["name"], "find_paths")

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

        call, error = TOOL_REGISTRY.validate({"name": "run", "arguments": {"command": "pytest -q"}})
        self.assertFalse(error)
        self.assertEqual(call, {"name": "shell", "reason": "", "cmd": "pytest -q"})

        call, error = TOOL_REGISTRY.validate({"name": "read_file", "arguments": {"path": "app.py", "line": 12}})
        self.assertFalse(error)
        self.assertEqual(call, {"name": "read_file", "reason": "", "path": "app.py", "line": "12"})

        call, error = TOOL_REGISTRY.validate(
            {"name": "read_file", "arguments": {"path": "app.py", "start_line": 80, "max_lines": 40}}
        )
        self.assertFalse(error)
        self.assertEqual(
            call,
            {"name": "read_file", "reason": "", "path": "app.py", "start_line": "80", "max_lines": "40"},
        )

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

    def test_decoder_accepts_literal_escaped_whitespace_at_envelope_boundary(self) -> None:
        payload = (
            '<tool>{"name":"read_file","arguments":{"path":"report.md","start_line":175}}'
            r"\n</tool>"
        )

        call, error, recognized = decode_text_tool_call(payload)

        self.assertTrue(recognized)
        self.assertFalse(error)
        self.assertEqual(call["name"], "read_file")
        self.assertEqual(call["path"], "report.md")
        self.assertEqual(call["start_line"], "175")

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

    def test_call_style_near_miss_syntax_decodes_for_known_tools(self) -> None:
        call, error, recognized = decode_text_tool_call(
            r'<search_project(query="lockout project", scope=C:\Users\example\dairack)>'
        )
        self.assertTrue(recognized)
        self.assertFalse(error)
        self.assertEqual(call["name"], "search_project")
        self.assertEqual(call["query"], "lockout project")
        self.assertEqual(call["path"], r"C:\Users\example\dairack")

        call, error, recognized = decode_text_tool_call('<web_open(url="https://example.com/page")>')
        self.assertTrue(recognized)
        self.assertEqual(call["name"], "web_open")
        self.assertEqual(call["url"], "https://example.com/page")

        call, error, recognized = decode_text_tool_call('search_project(query="lockout")')
        self.assertTrue(recognized)
        self.assertEqual(call["query"], "lockout")

        # Unknown names stay prose so ordinary writing about functions is unaffected.
        call, error, recognized = decode_text_tool_call('some_function(argument="value")')
        self.assertIsNone(call)
        self.assertFalse(recognized)

    def test_call_style_body_inside_tool_envelope_is_recovered(self) -> None:
        call, error, recognized = decode_text_tool_call(
            '<tool>read_file(path="report.md", start_line=41, max_lines=20)</tool>'
        )

        self.assertTrue(recognized)
        self.assertFalse(error)
        self.assertEqual(
            call,
            {
                "name": "read_file",
                "reason": "",
                "path": "report.md",
                "start_line": "41",
                "max_lines": "20",
            },
        )

    def test_trailing_call_style_after_prose_is_recognized_as_the_action(self) -> None:
        text = 'Let me inspect that folder for you.\n<list_dir(path="C:\\Users\\example\\project")>'
        call, error, recognized = decode_text_tool_call(text)
        self.assertTrue(recognized)
        self.assertFalse(error)
        self.assertEqual(call["name"], "list_dir")
        self.assertEqual(call["path"], "C:\\Users\\example\\project")

    def test_call_style_recovery_rejects_ambiguous_arguments(self) -> None:
        fixtures = (
            'shell(cmd="echo safe" trailing text)',
            'read_file(path="app.py", ???)',
            'shell(cmd="echo one", cmd="echo two")',
            "hardware_status(garbage)",
            'read_file(path="app.py",)',
        )
        for payload in fixtures:
            with self.subTest(payload=payload):
                call, error, recognized = decode_text_tool_call(payload)
                self.assertTrue(recognized)
                self.assertIsNone(call)
                self.assertTrue(error)

    def test_windows_path_backslashes_do_not_poison_action_json(self) -> None:
        call, error, recognized = decode_text_tool_call(
            r'<tool name="read_file">{"path": "C:\Users\example\notes.txt"}</tool>'
        )
        self.assertTrue(recognized)
        self.assertFalse(error)
        self.assertEqual(call["path"], "C:\\Users\\example\\notes.txt")

        call, error = TOOL_REGISTRY.validate(
            {"name": "read_file", "arguments": r'{"path": "C:\Users\example\notes.txt"}'}
        )
        self.assertFalse(error)
        self.assertEqual(call["path"], "C:\\Users\\example\\notes.txt")

        # JSON accepts these as newline and tab escapes, but neither control character can
        # occur in a Windows path. Recover the model's intended separators.
        call, error, recognized = decode_text_tool_call(r'<tool name="read_file">{"path": "C:\new\test.txt"}</tool>')
        self.assertTrue(recognized)
        self.assertFalse(error)
        self.assertEqual(call["path"], r"C:\new\test.txt")

        # Repair is field-aware: an invalid path must not rewrite valid escapes elsewhere.
        call, error, recognized = decode_text_tool_call(
            r'<tool name="read_file">{"path": "C:\Users\example\notes.txt", "reason": "line\nnext"}</tool>'
        )
        self.assertTrue(recognized)
        self.assertFalse(error)
        self.assertEqual(call["path"], r"C:\Users\example\notes.txt")
        self.assertEqual(call["reason"], "line\nnext")

        # Valid JSON is never rewritten by the tolerant retry.
        call, error, recognized = decode_text_tool_call('<tool name="shell">{"command": "printf \'a\\nb\'"}</tool>')
        self.assertFalse(error)
        self.assertEqual(call["cmd"], "printf 'a\nb'")

    def test_windows_paths_recover_inside_command_fields(self) -> None:
        # Invalid escapes in a non-path field fall back to blanket re-escaping, which only
        # runs on JSON that already failed to parse and so cannot corrupt a valid payload.
        call, error, recognized = decode_text_tool_call(
            r'<tool name="shell">{"cmd": "dir C:\Users\example\Documents"}</tool>'
        )
        self.assertTrue(recognized)
        self.assertFalse(error)
        self.assertEqual(call["cmd"], r"dir C:\Users\example\Documents")

        # Valid-JSON control escapes that continue a drive-anchored path run are restored
        # to the separators the model meant.
        call, error, recognized = decode_text_tool_call(r'<tool name="shell">{"cmd": "dir C:\new\builds"}</tool>')
        self.assertFalse(error)
        self.assertEqual(call["cmd"], r"dir C:\new\builds")

        # Real newlines between shell statements are not path fragments and stay intact.
        call, error, recognized = decode_text_tool_call('<tool name="shell">{"cmd": "echo one\\necho two"}</tool>')
        self.assertFalse(error)
        self.assertEqual(call["cmd"], "echo one\necho two")

    def test_tool_name_envelope_with_attribute_body_decodes(self) -> None:
        # Observed on a Windows client: the tool name used as the envelope tag with
        # attribute-style arguments in the body.
        payload = '<search_project>query="LOCKOUT" filetype=".uproject"</search_project>'
        call, error, recognized = decode_text_tool_call(payload)
        self.assertTrue(recognized)
        self.assertFalse(error)
        self.assertEqual(call["name"], "search_project")
        self.assertEqual(call["query"], "LOCKOUT")
        self.assertEqual(strip_tool_protocol(payload), "")

        narrated = "I'll inspect the project root.\n<list_dir> path=/tmp/dairack-live-project</list_dir>"
        call, error, recognized = decode_text_tool_call(narrated)
        self.assertTrue(recognized)
        self.assertFalse(error)
        self.assertEqual(call["name"], "list_dir")
        self.assertEqual(call["path"], "/tmp/dairack-live-project")
        self.assertEqual(strip_tool_protocol(narrated), "I'll inspect the project root.")

    def test_near_miss_markup_never_reaches_visible_text(self) -> None:
        prose = 'However, there is a hidden folder:\n<list_dir(path="C:\\Users\\example\\.hidden")>'
        self.assertEqual(strip_tool_protocol(prose), "However, there is a hidden folder:")
        self.assertEqual(strip_tool_protocol('<web_open(url="https://example.com")>'), "")
        self.assertEqual(
            strip_tool_protocol("Plain prose about search_project stays."), "Plain prose about search_project stays."
        )


if __name__ == "__main__":
    unittest.main()
