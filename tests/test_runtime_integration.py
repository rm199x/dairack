from __future__ import annotations

import asyncio
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch

os.environ.setdefault("DAIRACK_HOME", tempfile.mkdtemp(prefix="dairack-tests-"))

from test_models import hardware  # noqa: E402

from dairack import runtime as CORE  # noqa: E402
from dairack.bootstrap import InitializationResult  # noqa: E402
from dairack.catalog import recommendation_set  # noqa: E402
from dairack.models import ModelRegistry  # noqa: E402
from dairack.paths import AppPaths  # noqa: E402
from dairack.ui import textual_app as ui  # noqa: E402

TEST_ROOT = Path(os.environ["DAIRACK_HOME"])
CORE.PATHS = AppPaths(
    TEST_ROOT / "config",
    TEST_ROOT / "data",
    TEST_ROOT / "cache",
    TEST_ROOT / "state",
)
CORE.PATHS.ensure()
CORE.PATHS.hardware_file.write_text(json.dumps(hardware().to_dict(), sort_keys=True), encoding="utf-8")
MODELS = [
    CORE.ModelInfo(
        "qwen3.5:9b",
        6_600_000_000,
        capabilities=("completion", "vision", "tools", "thinking"),
    ),
    CORE.ModelInfo("qwen3-coder:30b", 18_000_000_000, capabilities=("completion", "tools")),
    CORE.ModelInfo(
        "qwen3.6:27b",
        17_000_000_000,
        capabilities=("completion", "vision", "tools", "thinking"),
    ),
    CORE.ModelInfo(
        "devstral-small-2:latest",
        15_000_000_000,
        capabilities=("completion", "vision", "tools"),
    ),
]


def semantic_json(**overrides: Any) -> str:
    payload: dict[str, Any] = {
        "intent": "general",
        "code": 0.0,
        "agent": 0.0,
        "reasoning": 0.2,
        "general": 0.8,
        "research": 0.0,
        "vision": 0.0,
        "risk": 0.0,
        "complexity": 0.2,
        "needs_plan": False,
        "needs_review": False,
        "requires_action": False,
        "confidence": 0.9,
        "compute_preference": "auto",
        "control_target": "none",
        "preference_strength": 0.0,
        "control_confidence": 0.9,
        "applies_to_previous": False,
        "resolved_task": "",
        "reason": "general request",
    }
    payload.update(overrides)
    return json.dumps(payload, separators=(",", ":"))


SEMANTIC_JSON = semantic_json(
    intent="coding",
    code=1,
    agent=1,
    reasoning=0.84,
    general=0.2,
    research=0.1,
    risk=0.81,
    complexity=0.94,
    needs_plan=True,
    needs_review=True,
    requires_action=True,
    confidence=0.94,
    reason="multi-stage code change with operational risk",
)
AMBIGUOUS_TASK = (
    "implement code in the repository; then verify current documentation online; "
    "then run tests and diagnose root cause; then design a robust API."
)


class FakeProvider:
    stream_phase = "idle"
    current_model = ""
    current_stats: dict[str, Any] = {}
    last_stats: dict[str, Any] = {}

    def __init__(
        self,
        resident: tuple[str, ...] = ("qwen3.5:9b",),
        response: str = SEMANTIC_JSON,
        native_call: dict[str, Any] | None = None,
    ) -> None:
        self.resident = list(resident)
        self.response = response
        self.native_call = native_call
        self.calls: list[dict[str, Any]] = []

    def list_models(self) -> list[Any]:
        return MODELS

    def running_models(self) -> list[str]:
        return self.resident

    def supports(self, model: str, capability: str) -> bool:
        features = next(item.capabilities for item in MODELS if item.name == model)
        return capability in features

    def chat_stream(self, model: str, messages: list[dict[str, str]], **kwargs: Any) -> Any:
        self.calls.append({"model": model, "messages": messages, "kwargs": kwargs})
        sink = kwargs.get("tool_call_sink")
        if self.native_call and callable(sink):
            sink(self.native_call)
        if self.response:
            yield self.response


class FakeTransferProvider:
    def pull(self, _model: str):
        yield {"status": "pulling manifest"}
        yield {"status": "downloading", "digest": "layer", "completed": 50, "total": 100}
        yield {"status": "downloading", "digest": "layer", "completed": 100, "total": 100}
        yield {"status": "success"}


class SynchronizedMessageTests(unittest.TestCase):
    def test_deepcopy_returns_an_independent_plain_list(self) -> None:
        lock = threading.RLock()
        messages = CORE.SynchronizedMessages(
            [{"role": "system", "content": "base", "metadata": {"stable": True}}],
            lock,
        )

        snapshot = deepcopy(messages)
        messages[0]["metadata"]["stable"] = False
        messages.append({"role": "user", "content": "new"})

        self.assertIs(type(snapshot), list)
        self.assertEqual(snapshot, [{"role": "system", "content": "base", "metadata": {"stable": True}}])

    def test_concurrent_structural_mutation_produces_consistent_snapshots(self) -> None:
        lock = threading.RLock()
        messages = CORE.SynchronizedMessages([{"role": "system", "content": "base"}], lock)
        started = threading.Event()
        stop = threading.Event()

        def mutate() -> None:
            started.set()
            index = 0
            while not stop.is_set():
                messages.append({"role": "user", "content": str(index)})
                if len(messages) > 48:
                    messages.pop(1)
                if index % 64 == 0:
                    time.sleep(0)
                index += 1

        worker = threading.Thread(target=mutate)
        worker.start()
        try:
            self.assertTrue(started.wait(1.0))
            for _ in range(100):
                snapshot = deepcopy(messages)
                self.assertEqual(snapshot[0], {"role": "system", "content": "base"})
                self.assertLessEqual(len(snapshot), 49)
                self.assertTrue(
                    all(message.get("role") in {"system", "user"} and "content" in message for message in snapshot)
                )
        finally:
            stop.set()
            worker.join(timeout=1.0)
        self.assertFalse(worker.is_alive())


class SignalFeedbackTests(unittest.TestCase):
    def test_welcome_wordmark_has_one_canonical_identity_and_fixed_geometry(self) -> None:
        self.assertEqual(ui.WELCOME_WORDMARK, "DAIRACK")
        self.assertEqual(len(ui.WELCOME_GLYPHS), len(ui.WELCOME_WORDMARK))
        self.assertTrue(all(ui.Text(row).cell_len == 3 for glyph in ui.WELCOME_GLYPHS for row in glyph))

    def test_signal_clock_is_bounded_and_reduced_motion_is_static(self) -> None:
        self.assertEqual(ui.MOTION_FRAME_SECONDS, 0.05)
        self.assertEqual(ui.WAIT_FRAME_SECONDS, 0.1)
        self.assertLess(ui.PHASE_SETTLE_SECONDS, 0.25)
        self.assertLess(ui.FOCUS_SETTLE_SECONDS, 0.20)
        self.assertLessEqual(ui.COMPLETION_SWEEP_SECONDS, 0.40)
        self.assertLessEqual(ui.WELCOME_SETTLE_SECONDS, 0.90)
        self.assertEqual(ui.signal_pulse(0.0, True), ui.signal_pulse(999.0, True))
        self.assertEqual(ui.signal_pulse(0.0, False)[0], ui.signal_pulse(0.8, False)[0])
        self.assertNotEqual(ui.signal_pulse(0.0, False)[1], ui.signal_pulse(0.8, False)[1])

    def test_transition_and_progress_easing_are_bounded(self) -> None:
        values = [ui.transition_progress(index / 100, 1.0) for index in range(101)]
        self.assertEqual(values[0], 0.0)
        self.assertEqual(values[-1], 1.0)
        self.assertLessEqual(max(values), 1.0)
        self.assertTrue(all(left <= right for left, right in zip(values, values[1:], strict=False)))

        progress = 0.0
        for _ in range(20):
            progress = ui.eased_progress(progress, 0.63, 0.05)
            self.assertLessEqual(progress, 0.63)
        self.assertGreater(progress, 0.62)
        self.assertEqual(ui.eased_progress(progress, 0.40, 0.05), 0.40)

    def test_color_mix_uses_linear_light(self) -> None:
        self.assertEqual(ui.mix_color("#000000", "#ffffff", 0.5), "#bcbcbc")

    def test_signal_track_keeps_geometry_fixed(self) -> None:
        first = ui.Text()
        second = ui.Text()
        ui.append_signal_track(first, 9, 4.1, wrap=False)
        ui.append_signal_track(second, 9, 4.6, wrap=False)

        self.assertEqual(first.plain, "─" * 9)
        self.assertEqual(second.plain, first.plain)
        self.assertEqual(first.cell_len, 9)
        self.assertNotEqual(first.spans, second.spans)
        self.assertNotIn(" on ", repr(first.spans))


def coordinator_config(policy: str = "adaptive", semantic: bool = True) -> dict[str, Any]:
    return {
        **CORE.DEFAULT_CONFIG,
        "model": "qwen3-coder:30b",
        "model_mode": "orchestrator",
        "orchestrator_policy": policy,
        "orchestrator_semantic_routing": semantic,
    }


def route(
    provider: FakeProvider,
    prompt: str,
    config: dict[str, Any] | None = None,
    history: list[dict[str, str]] | None = None,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    messages = list(history or []) + [{"role": "user", "content": prompt}]
    CORE.reset_semantic_assessment_cache()
    return CORE.select_orchestrator_route(
        provider,
        config or coordinator_config(),
        messages,
        Path("/tmp"),
        cancel_event,
    )


class CoordinatorRoutingTests(unittest.TestCase):
    def test_embedding_only_models_never_enter_chat_routing_or_delegation(self) -> None:
        class MixedProvider(FakeProvider):
            def list_models(self) -> list[Any]:
                return [
                    CORE.ModelDescriptor(name="embed:latest", size=200_000_000, capabilities=("embedding",)),
                    *MODELS,
                ]

        provider = MixedProvider(())
        config = coordinator_config(semantic=False)
        decision = route(provider, "Explain this architecture", config)

        with self.assertRaisesRegex(ValueError, "does not support chat completion"):
            CORE.validate_direct_model_choice(provider, "embed:latest")
        self.assertEqual(CORE.validate_direct_model_choice(provider, "model-not-yet-listed"), "model-not-yet-listed")
        with patch("builtins.input", return_value=""):
            self.assertNotEqual(CORE.select_model(provider, "embed:latest"), "embed:latest")
        self.assertNotEqual(decision["executor"], "embed:latest")
        self.assertNotIn("embed:latest", {item["model"] for item in decision["candidates"]})
        specialist = CORE.select_coordinator_specialist(
            provider,
            config,
            {"name": "consult_specialist", "specialty": "general", "task": "Check the explanation"},
            decision,
        )
        self.assertNotEqual(specialist["specialist"], "embed:latest")

    def test_direct_answer_scope_cannot_expose_tools_or_delegate(self) -> None:
        config = coordinator_config(semantic=False)
        for prompt in ("Hello", "Tell me a joke"):
            with self.subTest(prompt=prompt):
                provider = FakeProvider(())
                decision = route(provider, prompt, config)
                self.assertEqual(decision["execution_scope"], "direct-answer")
                self.assertEqual(decision["executor"], "qwen3.5:9b")
                self.assertEqual(CORE.native_tools_for(provider, decision["executor"], True, decision), [])
                self.assertEqual(CORE.coordinator_delegation_limit(config, decision), 0)
                admitted, _ = CORE.coordinator_admits_delegation(
                    {
                        "specialty": "general",
                        "policy": "adaptive",
                        "specialist": "qwen3.6:27b",
                        "capability_gain": 0.5,
                    },
                    {"name": "consult_specialist", "task": "Answer the user"},
                    decision,
                )
                self.assertFalse(admitted)

    def test_public_website_request_gets_an_agentic_open_contract(self) -> None:
        provider = FakeProvider(())
        decision = route(
            provider,
            "what u think of playlockout.com",
            coordinator_config(semantic=False),
        )

        self.assertEqual(decision["execution_scope"], "agentic")
        self.assertEqual(decision["task_kind"], "research")
        self.assertEqual(decision["action_contract"]["preferred_tool"], "web_open")
        self.assertEqual(decision["action_contract"]["target"], "https://playlockout.com/")
        tool_names = {
            tool["function"]["name"] for tool in CORE.native_tools_for(provider, decision["executor"], True, decision)
        }
        self.assertIn("web_open", tool_names)
        self.assertIn("https://playlockout.com/", CORE.coordinator_executor_directive(decision, coordinator_config()))

    def test_semantic_runtime_action_requires_a_real_tool_call(self) -> None:
        provider = FakeProvider(
            (),
            response=semantic_json(
                intent="system_action",
                agent=0.92,
                general=0.2,
                complexity=0.28,
                confidence=0.96,
                requires_action=True,
                reason="must search client files",
            ),
        )
        decision = route(provider, "Can you find the Unreal project named Lockout?")

        self.assertEqual(decision["action_contract"]["capability"], "runtime_action")
        self.assertEqual(decision["execution_scope"], "agentic")
        directive = CORE.coordinator_executor_directive(decision, coordinator_config())
        self.assertIn("real function", CORE.system_prompt(Path("/tmp"), True, coordinator_config()))
        self.assertIn("supplied function tool", directive)
        self.assertIn("Do not print commands", directive)

    def test_explanatory_request_does_not_gain_an_action_contract(self) -> None:
        provider = FakeProvider(
            (),
            response=semantic_json(
                intent="general",
                general=0.9,
                complexity=0.12,
                confidence=0.95,
                requires_action=False,
                reason="general instructions only",
            ),
        )
        decision = route(provider, "Explain how I can find an Unreal project on Windows")

        self.assertFalse(decision.get("action_contract"))

    def test_website_follow_up_resolves_the_prior_public_target(self) -> None:
        decision = route(
            FakeProvider(()),
            "can u check the website",
            coordinator_config(semantic=False),
            history=[
                {"role": "user", "content": "what u think of playlockout.com"},
                {"role": "assistant", "content": "I would need current website content."},
            ],
        )

        self.assertTrue(CORE.depends_on_conversation_context("can u check the website"))
        self.assertEqual(decision["execution_scope"], "agentic")
        self.assertEqual(decision["action_contract"]["preferred_tool"], "web_open")
        self.assertEqual(decision["action_contract"]["target"], "https://playlockout.com/")

    def test_semantic_assessment_cannot_erase_a_public_web_contract(self) -> None:
        assessment = semantic_json(
            intent="conversation",
            code=0,
            agent=0,
            reasoning=0,
            general=1,
            complexity=0.05,
            confidence=1,
            control_confidence=1,
            reason="misclassified as casual conversation",
        )
        decision = route(FakeProvider((), response=assessment), "can you open example.com")

        self.assertEqual(decision["execution_scope"], "agentic")
        self.assertGreaterEqual(decision["signals"]["research"], 0.72)
        self.assertGreaterEqual(decision["signals"]["agent"], 0.48)
        self.assertEqual(decision["action_contract"]["target"], "https://example.com/")

    def test_code_filename_and_website_creation_do_not_invent_network_work(self) -> None:
        for prompt in ("Review runtime.py", "Build a website from the local project"):
            with self.subTest(prompt=prompt):
                decision = route(FakeProvider(()), prompt, coordinator_config(semantic=False))
                self.assertFalse(decision.get("action_contract"))

    def test_final_request_fit_reserves_tools_and_keeps_latest_image(self) -> None:
        config = {**coordinator_config(semantic=False), "num_ctx": 8192, "context_budget_ratio": 0.82}
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": CORE.system_prompt(Path("/tmp"), True)},
            {"role": "user", "content": "old request"},
            {"role": "assistant", "content": "old evidence " * 1800},
            {"role": "user", "content": "another old request"},
            {"role": "assistant", "content": "more old evidence " * 1600},
            {
                "role": "user",
                "content": "What is shown here?",
                "image_paths": ["/tmp/reference.png"],
            },
        ]
        tools = CORE.agent_tool_schemas()
        fitted = CORE.fit_request_context_messages(messages, config, tools)
        tool_tokens = CORE.estimate_tokens(json.dumps(tools, sort_keys=True))
        headroom = max(256, int(config["num_ctx"] * 0.04))

        self.assertEqual(fitted[-1]["image_paths"], ["/tmp/reference.png"])
        self.assertLess(len(fitted), len(messages))
        self.assertLessEqual(
            sum(CORE.estimate_message_tokens(message) for message in fitted) + tool_tokens + headroom,
            CORE.context_budget(config),
        )

    def test_native_requests_drop_the_prose_tool_catalog_and_fit_default_context(self) -> None:
        config = {**coordinator_config(semantic=False), "num_ctx": 4096, "context_budget_ratio": 0.82}
        messages = [
            {"role": "system", "content": CORE.system_prompt(Path("/tmp"), True, {"model_mode": "direct"})},
            {"role": "user", "content": "What folders are in root?"},
        ]

        fitted, tools = CORE.fit_agent_request_context_messages(messages, config, CORE.agent_tool_schemas())

        # Native tools stay active at the default context size; the schemas are
        # authoritative there, so the duplicate prose catalog is dropped.
        self.assertTrue(tools)
        self.assertIn(CORE.NATIVE_TOOL_DIRECTIVE, fitted[0]["content"])
        self.assertNotIn("Available tools:", fitted[0]["content"])
        self.assertIn("Compatibility fallback only:", fitted[0]["content"])

        compat, no_tools = CORE.fit_agent_request_context_messages(messages, config, None)
        self.assertEqual(no_tools, [])
        self.assertIn("Available tools:", compat[0]["content"])

    def test_final_fitter_can_shrink_macro_memory_without_dropping_active_directives(self) -> None:
        config = {**coordinator_config(semantic=False), "num_ctx": 4096, "context_budget_ratio": 0.82}
        messages = CORE.canonicalize_messages(
            [
                {"role": "system", "content": CORE.system_prompt(Path("/tmp"), True, config)},
                {
                    "role": "system",
                    "content": "Persistent summary of earlier conversation:\n" + "grounded detail " * 1200,
                },
                {
                    "role": "system",
                    "content": "Retrieved local project memory.\n" + "stale retrieval " * 900,
                },
                {"role": "user", "content": "CURRENT_FILE_ANALYSIS_TASK"},
            ],
            ["ACTIVE_COORDINATOR_DIRECTIVE"],
        )

        fitted = CORE.fit_request_context_messages(messages, config)
        rendered = "\n".join(str(message.get("content") or "") for message in fitted)

        self.assertIn("CURRENT_FILE_ANALYSIS_TASK", rendered)
        self.assertIn("ACTIVE_COORDINATOR_DIRECTIVE", rendered)
        self.assertIn("Persistent summary of earlier conversation", rendered)
        self.assertNotIn("stale retrieval", rendered)
        self.assertLessEqual(
            sum(CORE.estimate_message_tokens(message) for message in fitted) + max(256, int(config["num_ctx"] * 0.04)),
            CORE.context_budget(config),
        )

    def test_response_reserve_binds_only_when_generation_room_is_inadequate(self) -> None:
        healthy = {"num_ctx": 4096, "context_budget_ratio": 0.82}
        self.assertEqual(CORE.response_token_reserve(healthy), 0)
        self.assertEqual(CORE.response_token_reserve({"num_ctx": 16384, "context_budget_ratio": 0.82}), 0)

        # A raised ratio, thinking mode, or final synthesis must win room back for generation.
        self.assertGreater(CORE.response_token_reserve({"num_ctx": 4096, "context_budget_ratio": 0.95}), 0)
        self.assertGreater(CORE.response_token_reserve({**healthy, "think": True}), 0)
        self.assertGreater(CORE.response_token_reserve(healthy, finalizing=True), 0)

        # The enforced reserve is visible as real answer room in context accounting.
        reserved = CORE.response_token_reserve({"num_ctx": 4096, "context_budget_ratio": 0.95})
        budget = CORE.context_budget({"num_ctx": 4096, "context_budget_ratio": 0.95})
        self.assertGreaterEqual((4096 - budget) + reserved, 320)

    def test_executor_response_allowance_caps_generation_at_the_residual(self) -> None:
        config = {"num_ctx": 4096, "context_budget_ratio": 0.82}
        small = [{"role": "system", "content": "s"}, {"role": "user", "content": "hi"}]
        allowance = CORE.executor_response_allowance(small, None, config)
        prompt = sum(CORE.estimate_message_tokens(message) for message in small)
        headroom, _ = CORE.context_request_headroom(config)
        self.assertEqual(allowance, 4096 - prompt - headroom)

        packed = [{"role": "user", "content": "x" * 40_000}]
        self.assertEqual(CORE.executor_response_allowance(packed, None, config), 256)

    def test_context_posture_directive_matches_window_tiers(self) -> None:
        tight = {**coordinator_config(semantic=False), "num_ctx": 4096, "context_budget_ratio": 0.82}
        roomy = {**tight, "num_ctx": 32768}
        standard = {**tight, "num_ctx": 8192}
        messages = [
            {"role": "system", "content": CORE.system_prompt(Path("/tmp"), True, {"model_mode": "direct"})},
            {"role": "user", "content": "Audit runtime.py"},
        ]

        fitted, tools = CORE.fit_agent_request_context_messages(messages, tight, CORE.agent_tool_schemas())
        self.assertTrue(tools)
        self.assertIn("Context posture: TIGHT", fitted[0]["content"])

        fitted, _ = CORE.fit_agent_request_context_messages(messages, roomy, CORE.agent_tool_schemas())
        self.assertIn("Context posture: ROOMY", fitted[0]["content"])

        fitted, _ = CORE.fit_agent_request_context_messages(messages, standard, CORE.agent_tool_schemas())
        self.assertNotIn("Context posture:", fitted[0]["content"])

    def test_executor_continuity_keeps_resident_incumbent_within_margin(self) -> None:
        descriptor = CORE.ModelDescriptor(name="qwen3.5:9b", size=6_600_000_000)
        incumbent = {
            "model": "qwen3.5:9b",
            "score": 0.700,
            "resident": True,
            "descriptor": descriptor,
            "capabilities": {"vision": 0.0},
        }
        challenger = {
            "model": "big:32b",
            "score": 0.715,
            "resident": False,
            "descriptor": descriptor,
            "capabilities": {"vision": 0.0},
        }
        previous = {"executor": "qwen3.5:9b"}
        signals = {"vision": 0.0}

        kept = CORE._executor_continuity([challenger, incumbent], previous, "adaptive", signals)
        self.assertIsNotNone(kept)
        self.assertEqual(kept["executor"], "qwen3.5:9b")
        self.assertEqual(kept["over"], "big:32b")

        # A decisive lead, a resident challenger, an unloaded incumbent, a vision
        # requirement the incumbent cannot serve, or no incumbent all disable it.
        decisive = {**challenger, "score": 0.850}
        self.assertIsNone(CORE._executor_continuity([decisive, incumbent], previous, "adaptive", signals))
        warm = {**challenger, "resident": True}
        self.assertIsNone(CORE._executor_continuity([warm, incumbent], previous, "adaptive", signals))
        cold_incumbent = {**incumbent, "resident": False}
        self.assertIsNone(CORE._executor_continuity([challenger, cold_incumbent], previous, "adaptive", signals))
        self.assertIsNone(CORE._executor_continuity([challenger, incumbent], previous, "adaptive", {"vision": 0.8}))
        self.assertIsNone(CORE._executor_continuity([challenger, incumbent], None, "adaptive", signals))

        # A moderate lead stays sticky under adaptive but switches under quality's tighter margin.
        moderate = {**challenger, "score": 0.725}
        self.assertIsNotNone(CORE._executor_continuity([moderate, incumbent], previous, "adaptive", signals))
        self.assertIsNone(CORE._executor_continuity([moderate, incumbent], previous, "quality", signals))

    def test_final_request_fit_fails_locally_when_latest_turn_cannot_fit(self) -> None:
        config = {**coordinator_config(semantic=False), "num_ctx": 1024, "context_budget_ratio": 0.82}
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "x" * 10_000},
        ]

        with self.assertRaisesRegex(ValueError, "cannot fit safely"):
            CORE.fit_request_context_messages(messages, config)

    def test_agent_request_sheds_native_schema_before_rejecting_a_valid_image_turn(self) -> None:
        config = {**coordinator_config(semantic=False), "num_ctx": 4096, "context_budget_ratio": 0.82}
        messages = [
            {"role": "system", "content": CORE.system_prompt(Path("/tmp"), True)},
            {"role": "user", "content": "What is shown here?", "image_paths": ["/tmp/reference.png"]},
        ]

        fitted, request_tools = CORE.fit_agent_request_context_messages(messages, config, CORE.agent_tool_schemas())

        self.assertEqual(request_tools, [])
        self.assertEqual(fitted[-1]["image_paths"], ["/tmp/reference.png"])
        self.assertNotIn(CORE.NATIVE_TOOL_DIRECTIVE, fitted[0]["content"])

    def test_schema_shedding_converts_native_tool_history_to_compatibility_messages(self) -> None:
        config = {**coordinator_config(semantic=False), "num_ctx": 4096, "context_budget_ratio": 0.82}
        native_call = {
            "type": "function",
            "function": {"name": "list_dir", "arguments": {"path": "."}},
        }
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": CORE.system_prompt(Path("/tmp"), True)},
            {"role": "user", "content": "Inspect this project."},
            {"role": "assistant", "content": "", "tool_calls": [native_call]},
            {
                "role": "tool",
                "tool_name": "list_dir",
                "content": "Structured tool result:\ntool: list_dir\nexit_code: 0\noutput:\n" + "file.py\n" * 350,
            },
        ]

        fitted, request_tools = CORE.fit_agent_request_context_messages(
            messages,
            config,
            CORE.agent_tool_schemas(),
        )

        self.assertEqual(request_tools, [])
        self.assertFalse(any(message.get("role") == "tool" for message in fitted))
        self.assertFalse(any(message.get("tool_calls") for message in fitted))
        self.assertEqual(fitted[-1]["role"], "user")
        self.assertIn("Structured tool result:", fitted[-1]["content"])

    def test_active_context_keeps_native_tool_exchange_atomic(self) -> None:
        config = {**coordinator_config(semantic=False), "num_ctx": 2048, "context_budget_ratio": 0.82}
        native_call = {
            "type": "function",
            "function": {"name": "read_file", "arguments": {"path": "src/app.py"}},
        }
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "old request"},
            {"role": "assistant", "content": "old context " * 2000},
            {"role": "user", "content": "CURRENT_TASK_SENTINEL"},
            {"role": "assistant", "content": "", "tool_calls": [native_call]},
            {"role": "tool", "tool_name": "read_file", "content": "Structured tool result:\noutput:\nvalue = 1"},
        ]

        fitted = CORE.active_context_messages(messages, "", config)

        self.assertEqual([message["role"] for message in fitted[-2:]], ["assistant", "tool"])
        self.assertEqual(fitted[-2]["tool_calls"], [native_call])
        self.assertTrue(any(message.get("content") == "CURRENT_TASK_SENTINEL" for message in fitted))
        self.assertFalse(any(str(message.get("content") or "").startswith("old context") for message in fitted))

    def test_runtime_failure_is_context_but_not_a_user_task(self) -> None:
        failure = CORE.runtime_failure_message(
            "Ollama returned HTTP 400: request (8227 tokens) exceeds the available context size (8192 tokens)"
        )
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "What is shown here?", "image_paths": ["/tmp/reference.png"]},
            failure,
            {"role": "user", "content": "what happened"},
        ]

        self.assertIn("context limit exceeded", failure["content"])
        self.assertEqual(CORE.latest_user_task(messages), "what happened")
        self.assertIn(failure, CORE.active_context_messages(messages, "", coordinator_config()))

    def test_context_report_uses_the_routed_executors_runtime_profile(self) -> None:
        config = {**coordinator_config(), "num_ctx": 6144}
        chat = {"last_route": {"executor": "devstral-small-2:latest"}, "summary_upto": 1}
        runtime = {**config, "num_ctx": 8192}
        messages = [{"role": "system", "content": "test"}, {"role": "user", "content": "hello"}]
        with patch.object(CORE, "runtime_config_for_model", return_value=runtime):
            report = CORE.context_report(messages, "", config, chat)
        self.assertIn("request window: 8192 tokens / devstral-small-2:latest", report)
        self.assertIn(f"/{CORE.context_budget(runtime)} est tokens", report)
        self.assertIn("macro memory:", report)
        self.assertIn("live working set:", report)
        self.assertIn("answer reserve:", report)

    def test_context_and_tool_result_budgets_scale_with_the_executor_window(self) -> None:
        messages = [{"role": "system", "content": "system"}, {"role": "user", "content": "inspect this"}]
        chat = {"summary": "", "summary_upto": 1}
        small = {"num_ctx": 4096, "context_budget_ratio": 0.82, "agent": False}
        large = {"num_ctx": 32768, "context_budget_ratio": 0.82, "agent": False}

        small_state = CORE.context_state(messages, "", small, chat)
        large_state = CORE.context_state(messages, "", large, chat)

        self.assertGreater(large_state["budget"], small_state["budget"])
        self.assertGreater(large_state["answer_reserve"], small_state["answer_reserve"])
        self.assertGreater(
            CORE.tool_result_char_budget(messages, chat, large),
            CORE.tool_result_char_budget(messages, chat, small),
        )

    def test_omitted_current_turn_tool_results_leave_a_compact_evidence_ledger(self) -> None:
        config = {"num_ctx": 2048, "context_budget_ratio": 0.82, "agent": False}
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "CURRENT_TASK"},
        ]
        for index in range(5):
            call = {
                "type": "function",
                "function": {"name": "read_file", "arguments": {"path": f"file-{index}.py"}},
            }
            messages.extend(
                [
                    {"role": "assistant", "content": "", "tool_calls": [call]},
                    {
                        "role": "tool",
                        "tool_name": "read_file",
                        "content": (
                            "Structured tool result:\n"
                            "tool: read_file\n"
                            f"summary: read file-{index}.py\n"
                            "exit_code: 0\n"
                            "output:\n"
                            f"EVIDENCE_{index} " + "x" * 1400
                        ),
                    },
                ]
            )

        active = CORE.active_context_messages(messages, "", config)
        rendered = "\n".join(str(message.get("content") or "") for message in active)

        self.assertIn("CURRENT_TASK", rendered)
        self.assertIn("Compressed evidence from omitted active tool exchanges", rendered)
        self.assertIn("EVIDENCE_", rendered)
        self.assertEqual([message["role"] for message in active[-2:]], ["assistant", "tool"])

    def test_read_file_window_is_clamped_to_the_context_result_budget(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "large.py"
            source.write_text("\n".join(f"line {index}" for index in range(1, 401)), encoding="utf-8")

            code, output = CORE.execute_tool_call(
                {"name": "read_file", "path": str(source), "start_line": "101", "max_lines": "200"},
                root,
                output_limit=1800,
            )

        self.assertEqual(code, 0)
        self.assertIn("lines 101-120 of 400", output)
        self.assertIn("continue with start_line=121, max_lines=20", output)
        self.assertNotIn("  121  line 121", output)

    def test_large_read_after_compaction_is_rebounded_for_a_valid_continuation(self) -> None:
        config = {
            **coordinator_config(semantic=False),
            "model_mode": "direct",
            "model": "qwen3.5:9b",
            "num_ctx": 4096,
            "context_budget_ratio": 0.82,
            "agent": True,
        }
        call = {
            "name": "read_file",
            "path": "large.cpp",
            "start_line": "1",
            "max_lines": "260",
        }
        native_call = {
            "type": "function",
            "function": {
                "name": "read_file",
                "arguments": {"path": "large.cpp", "start_line": "1", "max_lines": "260"},
            },
        }
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": CORE.system_prompt(Path("/tmp"), True, config)},
            {"role": "user", "content": "old request"},
            {"role": "assistant", "content": "old answer"},
            {"role": "user", "content": "Read the file in parts and analyze it."},
            {"role": "assistant", "content": "", "tool_calls": [native_call]},
        ]
        chat = {
            "cwd": "/tmp",
            "summary": "# Grounded conversation memory\n" + "Earlier grounded detail. " * 500,
            "summary_upto": 3,
            "summary_format": CORE.GROUNDED_MEMORY_FORMAT,
        }
        raw_result = (
            "FILE large.cpp\nlines 1-260 of 1200\n"
            + "\n".join(f"{line:5}  line evidence {line} " + "x" * 80 for line in range(1, 261))
            + "\ncontinue with start_line=261, max_lines=260"
        )

        fitted_result = CORE.fit_tool_result_for_context(
            messages,
            chat,
            config,
            call,
            0,
            raw_result,
            Path("/tmp"),
        )
        history = [*messages, CORE.tool_history_message(call, 0, fitted_result)]
        active = CORE.request_context_messages(history, chat, config, Path("/tmp"), include_retrieval=False)
        fitted, _tools = CORE.fit_agent_request_context_messages(active, config, CORE.agent_tool_schemas())
        rendered = "\n".join(str(message.get("content") or "") for message in fitted)

        self.assertLess(len(fitted_result), len(raw_result))
        self.assertTrue(fitted_result.endswith("continue with start_line=261, max_lines=260"))
        self.assertIn("Read the file in parts and analyze it.", rendered)
        self.assertIn("Persistent summary of earlier conversation", rendered)
        self.assertIn("continue with start_line=261, max_lines=260", rendered)

    def test_project_retrieval_keeps_the_real_user_prompt_after_tool_results(self) -> None:
        messages = [
            {"role": "user", "content": "Audit the permission implementation"},
            {
                "role": "user",
                "content": "Structured tool result:\nsummary: read_file permissions.py\nexit_code: 0",
            },
        ]
        self.assertEqual(CORE.latest_user_prompt(messages), "Audit the permission implementation")

    def test_indexed_child_becomes_persistent_chat_project_scope(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            project = home / "dairack-project"
            project.mkdir()
            (project / "engine.py").write_text(
                "ACTIVE_PROJECT_SCOPE_SENTINEL = 'locked'\n",
                encoding="utf-8",
            )
            config = coordinator_config()
            with (
                patch.object(CORE, "INDEX_DB_PATH", home / "project-index.sqlite3"),
                patch.object(CORE, "CHAT_DIR", home / "chats"),
            ):
                code, index_output = CORE.build_project_index(project)
                self.assertEqual(code, 0)
                missing_code, _ = CORE.search_project_index(home, "ACTIVE_PROJECT_SCOPE_SENTINEL")
                self.assertNotEqual(missing_code, 0)

                chat = CORE.new_chat_state(home, config)
                CORE.remember_indexed_project(
                    chat,
                    home,
                    {"name": "index_project", "path": str(project)},
                    code,
                )
                search_code, output = CORE.execute_tool_call(
                    {"name": "search_project", "query": "ACTIVE_PROJECT_SCOPE_SENTINEL"},
                    home,
                    CORE.project_scope_for_chat(chat, home),
                )
                self.assertEqual(search_code, 0)
                self.assertIn("engine.py", output)

                messages = [
                    {"role": "system", "content": "test"},
                    CORE.tool_history_message(
                        {"name": "index_project", "path": str(project)},
                        code,
                        index_output,
                    ),
                ]
                saved = CORE.save_chat_session(chat, home, config, messages, [])
                saved_session = CORE.load_chat_file(saved)
                restored, _, _, _ = CORE.chat_runtime_state(saved_session, home, config)
                self.assertEqual(restored["project_root"], str(project.resolve()))
                saved_session.pop("project_root")
                migrated, _, _, _ = CORE.chat_runtime_state(saved_session, home, config)
                self.assertEqual(migrated["project_root"], "")

    def test_hybrid_retrieval_finds_semantic_matches_beyond_lexical(self) -> None:
        class EmbedProvider(FakeProvider):
            def __init__(self) -> None:
                super().__init__(())
                self.embed_calls: list[list[str]] = []

            def list_models(self) -> list[Any]:
                return [
                    CORE.ModelDescriptor(name="nomic-embed", size=200_000_000, capabilities=("embedding",)),
                    CORE.ModelDescriptor(name="qwen3.5:9b", size=6_600_000_000, capabilities=("completion",)),
                ]

            def embed(self, model: str, texts: list[str]) -> list[list[float]]:
                self.embed_calls.append(list(texts))

                def vector(text: str) -> list[float]:
                    lowered = text.lower()
                    if "authentication" in lowered or "login" in lowered:
                        return [1.0, 0.0]
                    return [0.0, 1.0]

                return [vector(text) for text in texts]

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            root.mkdir()
            (root / ".git").mkdir()
            (root / "auth_notes.md").write_text("Authentication tokens rotate hourly for safety.\n", encoding="ascii")
            (root / "colors.py").write_text("PALETTE = ['red', 'green']\n", encoding="ascii")
            with patch.object(CORE, "INDEX_DB_PATH", Path(directory) / "index.sqlite3"):
                provider = EmbedProvider()
                code, output = CORE.index_project_with_vectors(provider, root, enable_embeddings=False)
                self.assertEqual(code, 0, output)
                self.assertIn("semantic vectors: disabled", output)
                self.assertEqual(provider.embed_calls, [])

                code, output = CORE.index_project_with_vectors(provider, root)
                self.assertEqual(code, 0, output)
                self.assertIn("semantic vectors: 2 embedded", output)

                # Pure lexical search misses a query with no shared words; the
                # fused search recovers the semantic neighbor.
                lexical_code, _ = CORE.search_project_index(root, "login policy")
                self.assertNotEqual(lexical_code, 0)
                hybrid_code, hybrid_output = CORE.hybrid_project_search(provider, root, "login policy")
                self.assertEqual(hybrid_code, 0, hybrid_output)
                self.assertIn("auth_notes.md", hybrid_output)

                # A provider without an embedding-capable model degrades to exactly
                # the lexical behavior instead of failing.
                fallback_code, _ = CORE.hybrid_project_search(FakeProvider(()), root, "login policy")
                self.assertNotEqual(fallback_code, 0)

                # Re-indexing reuses fresh vectors instead of re-embedding.
                code, output = CORE.index_project_with_vectors(provider, root)
                self.assertEqual(code, 0, output)
                self.assertIn("0 embedded, 2 current", output)

    def test_hybrid_retrieval_refills_candidates_after_stale_vector_hits(self) -> None:
        class EmbedProvider(FakeProvider):
            def list_models(self) -> list[Any]:
                return [CORE.ModelDescriptor(name="embed", size=100_000_000, capabilities=("embedding",))]

            def embed(self, model: str, texts: list[str]) -> list[list[float]]:
                vectors: list[list[float]] = []
                for text in texts:
                    lowered = text.lower()
                    if "stale_top" in lowered or "login policy" in lowered:
                        vectors.append([1.0, 0.0])
                    elif "authorization guidance" in lowered:
                        vectors.append([0.8, 0.2])
                    else:
                        vectors.append([0.0, 1.0])
                return vectors

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            root.mkdir()
            stale_paths = []
            for index in range(13):
                path = root / f"stale_{index:02d}.txt"
                path.write_text("STALE_TOP\n", encoding="ascii")
                stale_paths.append(path)
            (root / "related.txt").write_text("Authorization guidance for protected sessions.\n", encoding="ascii")
            with patch.object(CORE, "INDEX_DB_PATH", Path(directory) / "index.sqlite3"):
                code, output = CORE.index_project_with_vectors(EmbedProvider(), root)
                self.assertEqual(code, 0, output)
                for path in stale_paths:
                    path.write_text("STALE_TOP changed after indexing\n", encoding="ascii")

                code, output = CORE.hybrid_project_search(EmbedProvider(), root, "login policy", limit=1)

            self.assertEqual(code, 0, output)
            self.assertIn("related.txt", output)
            self.assertNotIn("stale_", output)

    def test_stale_index_content_is_not_returned_as_current_project_memory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.py"
            source.write_text("STALE_INDEX_SENTINEL = 1\n", encoding="ascii")
            with patch.object(CORE, "INDEX_DB_PATH", root / "index.sqlite3"):
                code, output = CORE.build_project_index(root)
                self.assertEqual(code, 0, output)
                source.write_text("CURRENT_IMPLEMENTATION = True\n", encoding="ascii")
                code, output = CORE.search_project_index(root, "STALE_INDEX_SENTINEL")

            self.assertNotEqual(code, 0)
            self.assertIn("stale", output)

    def test_corrupt_project_index_is_quarantined_and_rebuilt_privately(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            index = root / "index.sqlite3"
            index.write_bytes(b"not a sqlite database")
            (root / "source.py").write_text("VALUE = 1\n", encoding="ascii")
            with patch.object(CORE, "INDEX_DB_PATH", index):
                code, output = CORE.build_project_index(root)

            self.assertEqual(code, 0, output)
            self.assertTrue(index.exists())
            self.assertEqual(len(list(root.glob("index.sqlite3.corrupt-*"))), 1)
            if os.name == "posix":
                self.assertEqual(index.stat().st_mode & 0o777, 0o600)

    def test_partial_index_corruption_is_recovered_after_query_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            index = root / "index.sqlite3"
            index.write_bytes(b"damaged page")
            connection = SimpleNamespace(
                execute=Mock(side_effect=CORE.sqlite3.DatabaseError("database disk image is malformed")),
                close=Mock(),
            )
            with (
                patch.object(CORE, "INDEX_DB_PATH", index),
                patch.object(CORE, "indexed_project_for_cwd", return_value=root),
                patch.object(CORE, "open_index_db", return_value=connection),
            ):
                code, output = CORE.search_project_index(root, "needle")

            self.assertEqual(code, 1)
            self.assertIn("has been reset", output)
            connection.close.assert_called_once()
            self.assertTrue(index.exists())
            self.assertEqual(len(list(root.glob("index.sqlite3.corrupt-*"))), 1)

            with patch.object(CORE, "INDEX_DB_PATH", index):
                repaired = CORE.open_index_db()
                try:
                    self.assertEqual(repaired.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                finally:
                    repaired.close()

    def test_index_connection_closes_when_indexing_raises(self) -> None:
        connection = SimpleNamespace(close=Mock())
        with (
            patch.object(CORE, "open_index_db", return_value=connection),
            patch.object(CORE, "_build_project_index_connected", side_effect=RuntimeError("fault")),
            self.assertRaisesRegex(RuntimeError, "fault"),
        ):
            CORE.build_project_index(Path.cwd())
        connection.close.assert_called_once()

    def test_indexing_retries_once_after_partial_database_corruption(self) -> None:
        first = SimpleNamespace(close=Mock())
        second = SimpleNamespace(close=Mock())
        with (
            patch.object(CORE, "open_index_db", side_effect=[first, second]) as open_index,
            patch.object(
                CORE,
                "_build_project_index_connected",
                side_effect=[CORE.sqlite3.DatabaseError("database disk image is malformed"), (0, "rebuilt")],
            ) as build,
            patch.object(CORE, "quarantine_corrupt_index") as quarantine,
        ):
            code, output = CORE.build_project_index(Path.cwd())

        self.assertEqual((code, output), (0, "rebuilt"))
        self.assertEqual(open_index.call_count, 2)
        self.assertEqual(build.call_count, 2)
        quarantine.assert_called_once()
        first.close.assert_called_once()
        second.close.assert_called_once()

    def test_resumed_chat_runtime_enums_are_validated_before_config_use(self) -> None:
        config = coordinator_config()
        session = {
            "model_mode": "untrusted-mode",
            "orchestrator_policy": "untrusted-policy",
            "messages": [],
        }

        chat, _cwd, _messages, _blocks = CORE.chat_runtime_state(session, Path.cwd(), config)

        self.assertEqual(chat["model_mode"], config["model_mode"])
        self.assertEqual(chat["orchestrator_policy"], config["orchestrator_policy"])

    def test_resumed_chat_discards_unverified_project_authority(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cwd = Path(directory)
            session = {"project_root": "/", "messages": []}

            chat, _cwd, _messages, _blocks = CORE.chat_runtime_state(session, cwd, coordinator_config())

        self.assertEqual(chat["project_root"], "")

    def test_runtime_config_loader_fails_closed_without_saving_defaults(self) -> None:
        with (
            patch.object(CORE, "load_app_config", side_effect=ValueError("corrupt JSON")),
            patch.object(CORE, "save_app_config") as save,
            self.assertRaisesRegex(SystemExit, "refusing to overwrite unreadable configuration"),
        ):
            CORE.load_config()

        save.assert_not_called()

    def test_signal_matching_uses_word_boundaries(self) -> None:
        for prompt in (
            "What is the capital of France?",
            "Who won the latest contest?",
            "Explain biological classification.",
        ):
            with self.subTest(prompt=prompt):
                analysis = CORE.analyze_orchestrator_task([{"role": "user", "content": prompt}])
                self.assertEqual(analysis["signals"]["code"], 0.0)
                self.assertFalse(any(item.startswith("code:") for item in analysis["evidence"]))
                self.assertEqual(
                    route(FakeProvider(()), prompt, coordinator_config(semantic=False))["executor"],
                    "qwen3.5:9b",
                )

    def test_trivial_routes_are_protected_from_learned_heavy_model_bias(self) -> None:
        learned_state = {
            "records": {
                "qwen3-coder:30b|general": {"positive": 96, "negative": 0},
                "qwen3.5:9b|general": {"positive": 0, "negative": 96},
            }
        }
        with patch.object(CORE, "load_calibration_state", return_value=learned_state):
            decision = route(FakeProvider(()), "Hello", coordinator_config(semantic=False))

        self.assertEqual(decision["executor"], "qwen3.5:9b")
        candidates = {item["model"]: item for item in decision["candidates"]}
        self.assertTrue(candidates["qwen3-coder:30b"]["learning_guarded"])
        self.assertEqual(candidates["qwen3-coder:30b"]["learned_adjustment"], 0.0)

    def test_task_kind_learning_changes_only_the_matching_route(self) -> None:
        learned_state = {
            "records": {
                "qwen3-coder:30b|agent": {"positive": 48, "negative": 48},
                "qwen3-coder:30b|agent|coding agent": {"positive": 96, "negative": 0},
            }
        }
        config = coordinator_config(semantic=False)
        with patch.object(CORE, "load_calibration_state", return_value=learned_state):
            matching = route(
                FakeProvider(()),
                "Inspect this repository, edit the Python implementation, run tests, and fix the bug.",
                config,
            )
            sibling = route(FakeProvider(()), "Write a Python function to parse JSON.", config)

        self.assertEqual(matching["task_kind"], "coding agent")
        self.assertEqual(matching["executor"], "qwen3-coder:30b")
        matching_candidate = next(
            candidate for candidate in matching["candidates"] if candidate["model"] == "qwen3-coder:30b"
        )
        self.assertEqual(matching_candidate["learning_role_evidence"], 96.0)
        self.assertEqual(matching_candidate["learning_kind_evidence"], 96.0)
        self.assertGreater(matching_candidate["learning_kind_weight"], 0.9)

        self.assertEqual(sibling["task_kind"], "coding")
        self.assertEqual(sibling["executor"], "qwen3.5:9b")
        sibling_candidate = next(
            candidate for candidate in sibling["candidates"] if candidate["model"] == "qwen3-coder:30b"
        )
        self.assertEqual(sibling_candidate["learned_adjustment"], 0.0)
        self.assertEqual(sibling_candidate["learning_kind_evidence"], 0.0)

    def test_primary_command_surface_uses_canonical_product_vocabulary(self) -> None:
        self.assertIn("/library", CORE.SLASH_COMMANDS)
        self.assertIn("/library", CORE.PRIMARY_HELP_TEXT)
        self.assertNotIn("/orchestrator", CORE.PRIMARY_HELP_TEXT)
        self.assertIn("/orchestrator", CORE.help_text(["all"]))
        self.assertIn("Did you mean /library", CORE.unknown_command_display("libary"))

    def test_semantic_assessment_cannot_invent_images_or_expensive_stages(self) -> None:
        provider = FakeProvider(
            response=semantic_json(
                code=0.1,
                agent=0.2,
                reasoning=0.3,
                general=0.4,
                research=0.5,
                vision=0.6,
                risk=0.7,
                complexity=0.8,
                needs_plan=True,
                needs_review=True,
                confidence=0.42,
                control_confidence=0.42,
                reason="request is ambiguous",
            )
        )
        decision = route(
            provider,
            "can't u do it?",
            history=[
                {"role": "user", "content": "What files are in my home directory?"},
                {"role": "assistant", "content": "I can list them for you."},
            ],
        )

        self.assertEqual(decision["signals"]["vision"], 0.0)
        self.assertEqual(decision["executor"], "qwen3.5:9b")
        self.assertEqual(decision["strategy"], "single")
        self.assertFalse(decision["planner"])
        self.assertFalse(decision["reviewer"])

    def test_semantic_assessment_cannot_downgrade_observed_risk(self) -> None:
        analysis = CORE.analyze_orchestrator_task(
            [{"role": "user", "content": "delete the production database and remove all backups permanently"}]
        )
        deterministic_risk = analysis["signals"]["risk"]
        assessment = {
            "intent": "general",
            "code": 0.0,
            "agent": 0.0,
            "reasoning": 0.0,
            "general": 1.0,
            "research": 0.0,
            "risk": 0.0,
            "confidence": 1.0,
        }

        merged = CORE._merge_semantic_assessment(analysis, assessment)

        self.assertGreaterEqual(merged["signals"]["risk"], deterministic_risk)

    def test_semantic_merge_reclassifies_the_task(self) -> None:
        decision = route(
            FakeProvider(),
            "go ahead",
            history=[
                {"role": "user", "content": "Refactor and test the Python repository."},
                {"role": "assistant", "content": "Ready after confirmation."},
            ],
        )

        self.assertIn(decision["task_kind"], {"coding", "coding agent"})
        self.assertGreaterEqual(decision["complexity"], 0.50)

    def test_semantic_general_label_cannot_suppress_corroborated_research(self) -> None:
        decision = route(
            FakeProvider(
                response=semantic_json(
                    code=0.1,
                    agent=0.2,
                    reasoning=0.3,
                    general=0.85,
                    research=0.9,
                    complexity=0.4,
                    confidence=0.75,
                    control_confidence=0.75,
                    needs_review=True,
                    reason="software release notes",
                )
            ),
            "What is the latest stable release of Textual and what changed in it?",
        )

        self.assertEqual(decision["preference_role"], "research")

    def test_research_effort_contributes_to_semantic_task_complexity(self) -> None:
        cases = (
            (
                "Install the package, configure its service, start it, and verify that it is healthy.",
                semantic_json(
                    intent="system_action",
                    code=0.1,
                    agent=0.85,
                    general=0.3,
                    risk=0.4,
                    complexity=0.6,
                    confidence=0.95,
                    control_confidence=0.95,
                    needs_plan=True,
                    requires_action=True,
                    reason="multi-step service setup",
                ),
                0.45,
            ),
            (
                "Find the current Ollama tool-calling documentation and verify the request schema from primary sources.",
                semantic_json(
                    intent="research",
                    code=0.1,
                    agent=0.2,
                    reasoning=0.3,
                    general=0.4,
                    research=0.95,
                    risk=0.1,
                    complexity=0.6,
                    confidence=0.8,
                    control_confidence=0.8,
                    needs_review=True,
                    reason="verify primary documentation",
                ),
                0.40,
            ),
        )
        for prompt, response, minimum in cases:
            with self.subTest(prompt=prompt):
                decision = route(FakeProvider(response=response), prompt)
                self.assertGreaterEqual(decision["complexity"], minimum)

    def test_tool_parser_accepts_supported_wire_formats_and_reports_failures(self) -> None:
        fixtures = (
            (
                '<tool name="shell" cmd="/bin/ls -la / | head -30" reason="list root directory contents"></tool>',
                {"name": "shell", "cmd": "/bin/ls -la / | head -30", "reason": "list root directory contents"},
            ),
            (
                '<tool name="shell" cmd="find . -type f 2>/dev/null | head"></tool>',
                {"name": "shell", "cmd": "find . -type f 2>/dev/null | head", "reason": ""},
            ),
            (
                '<tool>{"function":{"name":"web_search","arguments":{"query":"Ollama tools"}}}</tool>',
                {"name": "web_search", "reason": "", "query": "Ollama tools"},
            ),
            (
                'index_project{"path":"/home/example","reason":"build project memory index"}',
                {"name": "index_project", "reason": "build project memory index", "path": "/home/example"},
            ),
            (
                "index_project",
                {"name": "index_project", "reason": ""},
            ),
        )
        for payload, expected in fixtures:
            with self.subTest(payload=payload):
                call, error = CORE.parse_tool_request(payload)
                self.assertEqual(call, expected)
                self.assertFalse(error)

        call, error = CORE.parse_tool_request('<tool name="shell"></tool>')
        self.assertIsNone(call)
        self.assertIn("missing cmd", error)
        call, error = CORE.parse_tool_request('<tool>{"name":"list_dir"}</tool><tool>{"name":"list_dir"}</tool>')
        self.assertIsNone(call)
        self.assertIn("multiple action requests", error)
        call, error = CORE.parse_tool_request('index_project{"path":"/home/example"} trailing prose')
        self.assertIsNone(call)
        self.assertIn("invalid JSON", error)
        call, error = CORE.parse_tool_request("read_file")
        self.assertIsNone(call)
        self.assertIn("missing path", error)

    def test_native_tool_calls_round_trip_as_tool_history(self) -> None:
        raw = {
            "id": "call_123",
            "type": "function",
            "function": {"index": 0, "name": "list_dir", "arguments": {"path": "/home/example"}},
        }
        call, error = CORE.resolve_tool_request("", [raw])

        self.assertFalse(error)
        self.assertEqual(call["name"], "list_dir")
        self.assertEqual(call["_protocol"], "native")
        result = CORE.tool_history_message(call, 0, "file.txt")
        self.assertEqual(result["role"], "tool")
        self.assertEqual(result["tool_name"], "list_dir")
        sanitized = CORE.sanitize_messages(
            [
                {"role": "system", "content": "old"},
                {"role": "assistant", "content": "", "tool_calls": [raw]},
                result,
            ],
            Path("/tmp"),
            True,
        )
        self.assertEqual(sanitized[1]["tool_calls"], [raw])
        self.assertEqual(sanitized[2], result)

    def test_route_history_updates_a_turn_instead_of_duplicating_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.object(CORE, "CHAT_DIR", Path(directory)):
            config = coordinator_config(semantic=False)
            chat = CORE.new_chat_state(Path("/tmp"), config, "routing audit")
            messages = [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "Hello"},
            ]
            route_one = route(FakeProvider(()), "Hello", config)
            chat["last_route"] = route_one
            CORE.save_chat_session(chat, Path("/tmp"), config, messages, [])
            route_one["passes"] = 1
            CORE.save_chat_session(chat, Path("/tmp"), config, messages, [])
            route_two = route(FakeProvider(()), "Explain Fourier transforms", config)
            route_two["created_at"] = route_two["created_at"] + "-next"
            chat["last_route"] = route_two
            path = CORE.save_chat_session(chat, Path("/tmp"), config, messages, [])
            saved = CORE.load_chat_file(path)

        self.assertEqual(len(saved["route_history"]), 2)
        self.assertEqual(saved["route_history"][0]["passes"], 1)
        self.assertIn("qwen3.5:9b", CORE.format_route_history(saved))

    def test_semantic_assessment_caches_identical_turns_in_process(self) -> None:
        CORE.reset_semantic_assessment_cache()
        first_provider = FakeProvider()
        first_provider.host = "https://compute-a.example.test"
        config = coordinator_config()
        messages = [{"role": "user", "content": AMBIGUOUS_TASK}]
        first = CORE.select_orchestrator_route(first_provider, config, messages, Path("/tmp"))
        self.assertEqual(len(first_provider.calls), 1)

        # The identical turn re-routes without a second classifier inference.
        second_provider = FakeProvider()
        second_provider.host = first_provider.host
        second = CORE.select_orchestrator_route(second_provider, config, messages, Path("/tmp"))
        self.assertEqual(second_provider.calls, [])
        self.assertEqual(second["executor"], first["executor"])
        self.assertEqual(second["strategy"], first["strategy"])

        # Caller-side mutation of a returned assessment cannot poison the cache.
        second["semantic_assessment"]["code"] = 99.0
        third_provider = FakeProvider()
        third_provider.host = first_provider.host
        third = CORE.select_orchestrator_route(third_provider, config, messages, Path("/tmp"))
        self.assertLessEqual(float(third["semantic_assessment"]["code"]), 1.0)

        # A different inference endpoint gets its own verdict; model names alone
        # do not make independently hosted model builds equivalent.
        other_provider = FakeProvider()
        other_provider.host = "https://compute-b.example.test"
        CORE.select_orchestrator_route(other_provider, config, messages, Path("/tmp"))
        self.assertEqual(len(other_provider.calls), 1)
        CORE.reset_semantic_assessment_cache()

    def test_semantic_arbitration_and_stage_costs(self) -> None:
        provider = FakeProvider()
        decision = route(provider, AMBIGUOUS_TASK)

        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(provider.calls[0]["model"], "qwen3.5:9b")
        self.assertFalse(provider.calls[0]["kwargs"]["think"])
        self.assertEqual(provider.calls[0]["kwargs"]["extra_options"]["temperature"], 0)
        self.assertEqual(decision["executor"], "qwen3-coder:30b")
        self.assertEqual(decision["planner"], "qwen3.5:9b")
        self.assertEqual(decision["reviewer"], "qwen3.5:9b")
        self.assertEqual(decision["strategy"], "plan-review")
        self.assertTrue(decision["semantic_assessment"]["trigger"])

    def test_quality_uses_strongest_stages(self) -> None:
        decision = route(FakeProvider(), AMBIGUOUS_TASK, coordinator_config("quality"))

        self.assertEqual(decision["executor"], "qwen3-coder:30b")
        self.assertEqual(decision["planner"], "qwen3.6:27b")
        self.assertEqual(decision["reviewer"], "qwen3.6:27b")
        self.assertEqual(decision["semantic_assessment"]["trigger"], "quality policy semantic pass")

    def test_conversation_context_resolves_short_follow_up(self) -> None:
        provider = FakeProvider()
        decision = route(
            provider,
            "go ahead",
            history=[
                {"role": "user", "content": "Please inspect and refactor the Python repository safely."},
                {"role": "assistant", "content": "I can do that after you confirm."},
            ],
        )

        self.assertEqual(decision["semantic_assessment"]["trigger"], "conversation-dependent follow-up")
        classifier_prompt = provider.calls[0]["messages"][-1]["content"]
        self.assertIn("Please inspect and refactor", classifier_prompt)
        self.assertIn("Current request:\ngo ahead", classifier_prompt)

    def test_greeting_does_not_inherit_prior_chat_complexity(self) -> None:
        history = [
            {"role": "user", "content": "Research current deployment security and refactor the repository."},
            {"role": "assistant", "content": "I completed the deep review."},
        ]
        for prompt in ("Hello", "Greetings", "What's up?", "Thanks for the help"):
            with self.subTest(prompt=prompt):
                provider = FakeProvider()
                decision = route(provider, prompt, history=history)
                self.assertEqual(decision["executor"], "qwen3.5:9b")
                self.assertEqual(decision["task_kind"], "quick answer")
                self.assertFalse(decision["semantic_assessment"])
                self.assertFalse(provider.calls)

    def test_explicit_capacity_request_resolves_and_reroutes_the_prior_task(self) -> None:
        resolved_task = (
            "Generate distinctive single-word names for a local LLM coordinator with an underground "
            "1980s analog character."
        )
        provider = FakeProvider(
            response=semantic_json(
                intent="reasoning",
                reasoning=0.72,
                general=0.86,
                complexity=0.46,
                confidence=0.97,
                compute_preference="higher_capacity",
                control_target="compute",
                preference_strength=0.96,
                control_confidence=0.98,
                applies_to_previous=True,
                resolved_task=resolved_task,
                reason="explicit executor-capacity request for the prior naming task",
            )
        )
        decision = route(
            provider,
            "Try using a heavier model",
            history=[
                {"role": "user", "content": resolved_task},
                {"role": "assistant", "content": "Here are several initial names."},
            ],
        )

        control = decision["routing_control"]
        self.assertEqual(decision["executor"], "qwen3.6:27b")
        self.assertEqual(decision["execution_scope"], "direct-answer")
        self.assertTrue(control["active"])
        self.assertTrue(control["honored"])
        self.assertTrue(control["changed_executor"])
        self.assertEqual(control["baseline_executor"], "qwen3.5:9b")
        self.assertEqual(control["selected_executor"], "qwen3.6:27b")
        self.assertEqual(control["resolved_task"], resolved_task)
        self.assertEqual(CORE.native_tools_for(provider, decision["executor"], True, decision), [])
        directive = CORE.coordinator_executor_directive(decision, coordinator_config())
        self.assertIn(resolved_task, directive)
        self.assertIn("Do not inspect, list, load, or discuss models", directive)

    def test_quality_request_can_raise_answer_depth_without_becoming_a_tool_task(self) -> None:
        provider = FakeProvider(
            response=semantic_json(
                intent="reasoning",
                reasoning=0.82,
                general=0.78,
                complexity=0.58,
                confidence=0.96,
                compute_preference="quality",
                control_target="compute",
                preference_strength=0.92,
                control_confidence=0.97,
                applies_to_previous=True,
                resolved_task="Develop a more original and technically grounded name for the local LLM framework.",
                reason="request for deeper treatment of the prior naming task",
            )
        )
        decision = route(
            provider,
            "Can you think a tad deeper?",
            history=[
                {"role": "user", "content": "Find a distinctive name for this local LLM framework."},
                {"role": "assistant", "content": "Relay, Phosphor, and Spooler."},
            ],
        )

        self.assertEqual(decision["executor"], "qwen3.6:27b")
        self.assertEqual(decision["execution_scope"], "direct-answer")
        self.assertEqual(decision["routing_control"]["preference"], "quality")
        self.assertFalse(CORE.native_tools_for(provider, decision["executor"], True, decision))

    def test_model_discussion_does_not_change_the_executor(self) -> None:
        prompt = "When would the coordinator use a heavier model?"
        history = [
            {"role": "user", "content": "Explain how automatic routing works."},
            {"role": "assistant", "content": "It ranks suitable installed models for each turn."},
        ]
        baseline = route(FakeProvider(()), prompt, coordinator_config(semantic=False), history)
        provider = FakeProvider(
            response=semantic_json(
                intent="general",
                reasoning=0.45,
                general=0.92,
                complexity=0.28,
                confidence=0.96,
                compute_preference="auto",
                control_confidence=0.98,
                reason="discussion about routing rather than an execution instruction",
            )
        )
        decision = route(provider, prompt, history=history)

        self.assertEqual(decision["executor"], baseline["executor"])
        self.assertFalse(decision["routing_control"])
        self.assertFalse(provider.calls)

    def test_semantically_assessed_model_comparison_remains_automatic(self) -> None:
        prompt = "Compare when a heavier model improves naming quality with when its extra latency is not worthwhile."
        provider = FakeProvider(
            response=semantic_json(
                intent="reasoning",
                reasoning=0.88,
                general=0.72,
                complexity=0.54,
                confidence=0.97,
                compute_preference="auto",
                control_confidence=0.98,
                reason="model tradeoff question rather than an execution instruction",
            )
        )
        decision = route(provider, prompt)

        self.assertEqual(len(provider.calls), 1)
        self.assertFalse(decision["routing_control"]["active"])
        self.assertEqual(decision["routing_control"]["preference"], "auto")
        self.assertNotIn("changed_executor", decision["routing_control"])

    def test_semantic_specialization_requires_grounding_in_the_actual_task(self) -> None:
        provider = FakeProvider(
            response=semantic_json(
                intent="coding",
                code=1,
                agent=0.85,
                reasoning=0.72,
                general=0.45,
                research=0.3,
                complexity=0.8,
                confidence=0.92,
                compute_preference="quality",
                control_target="content",
                preference_strength=0.75,
                control_confidence=0.95,
                applies_to_previous=True,
                resolved_task="Describe the funk arrangement's rhythm section.",
                needs_plan=True,
                reason="incorrectly inferred software work from creative instructions",
            )
        )
        decision = route(
            provider,
            "Give the bassline a heavier character and make the pocket feel deeper.",
            history=[
                {"role": "user", "content": "Help me describe this funk arrangement."},
                {"role": "assistant", "content": "The rhythm section is sparse and dry."},
            ],
        )

        self.assertEqual(decision["executor"], "qwen3.5:9b")
        self.assertLess(decision["signals"]["code"], 0.35)
        self.assertLess(decision["signals"]["agent"], 0.34)
        self.assertFalse(decision["routing_control"]["active"])

    def test_semantic_specialization_can_promote_partial_code_evidence(self) -> None:
        provider = FakeProvider(
            response=semantic_json(
                intent="coding",
                code=1,
                reasoning=0.45,
                general=0.5,
                complexity=0.42,
                confidence=0.96,
                reason="explicit software implementation request",
            )
        )
        decision = route(provider, "Write a SQL query joining users and orders.")

        self.assertGreaterEqual(decision["signals"]["code"], 0.35)
        self.assertEqual(decision["preference_role"], "coding")

    def test_uncertain_or_contextless_capacity_control_is_ignored(self) -> None:
        resolved_task = "Generate a better framework name."
        cases = (
            (
                0.60,
                True,
                resolved_task,
                [{"role": "user", "content": resolved_task}],
                "confidence below threshold",
            ),
            (0.98, True, resolved_task, [], "no prior task available"),
        )
        for confidence, applies, task, history, status in cases:
            with self.subTest(status=status):
                provider = FakeProvider(
                    response=semantic_json(
                        compute_preference="higher_capacity",
                        control_target="compute",
                        preference_strength=0.95,
                        control_confidence=confidence,
                        applies_to_previous=applies,
                        resolved_task=task,
                        confidence=0.96,
                        reason="possible capacity instruction",
                    )
                )
                decision = route(provider, "Use a heavier model", history=history)

                self.assertEqual(decision["executor"], "qwen3.5:9b")
                self.assertFalse(decision["routing_control"]["active"])
                self.assertEqual(decision["routing_control"]["status"], status)

    def test_capacity_preference_retains_the_best_fit_when_nothing_larger_qualifies(self) -> None:
        provider = FakeProvider(
            response=semantic_json(
                intent="reasoning",
                reasoning=0.98,
                general=0.7,
                complexity=0.88,
                confidence=0.98,
                compute_preference="higher_capacity",
                control_target="compute",
                preference_strength=0.95,
                control_confidence=0.98,
                applies_to_previous=True,
                resolved_task="Compare two fault-tolerant distributed designs and justify their tradeoffs.",
                reason="explicit capacity request for a demanding reasoning task",
            )
        )
        decision = route(
            provider,
            "Use something heavier for that",
            history=[
                {
                    "role": "user",
                    "content": "Compare two fault-tolerant distributed designs and justify their tradeoffs.",
                },
                {"role": "assistant", "content": "I can compare them."},
            ],
        )

        self.assertEqual(decision["executor"], "qwen3.6:27b")
        self.assertFalse(decision["routing_control"]["honored"])
        self.assertFalse(decision["routing_control"]["changed_executor"])
        self.assertEqual(decision["routing_control"]["status"], "no suitable higher-capacity fit")

    def test_capacity_control_cannot_bypass_the_observed_image_capability_gate(self) -> None:
        provider = FakeProvider(
            response=semantic_json(
                intent="visual",
                vision=1,
                reasoning=0.72,
                general=0.72,
                complexity=0.52,
                confidence=0.98,
                compute_preference="higher_capacity",
                control_target="compute",
                preference_strength=0.9,
                control_confidence=0.98,
                applies_to_previous=True,
                resolved_task="Inspect the attached diagnostic image carefully.",
                reason="capacity request for the prior visual task",
            )
        )
        messages = [
            {
                "role": "user",
                "content": "Inspect this diagnostic image.",
                "image_paths": ["/tmp/diagnostic.png"],
            },
            {"role": "assistant", "content": "I can inspect it."},
            {"role": "user", "content": "Use a heavier model for that"},
        ]
        decision = CORE.select_orchestrator_route(
            provider,
            coordinator_config(),
            messages,
            Path("/tmp"),
        )

        self.assertGreater(decision["signals"]["vision"], 0)
        self.assertTrue(provider.supports(decision["executor"], "vision"))
        self.assertTrue(all(provider.supports(item["model"], "vision") for item in decision["candidates"]))

    def test_capacity_preference_is_per_turn_and_never_sticky(self) -> None:
        provider = FakeProvider(
            response=semantic_json(
                compute_preference="higher_capacity",
                control_target="compute",
                preference_strength=0.95,
                control_confidence=0.98,
                applies_to_previous=True,
                resolved_task="Generate a more distinctive product name.",
                confidence=0.98,
                reason="explicit capacity request",
            )
        )
        first = route(
            provider,
            "Try a heavier model",
            history=[{"role": "user", "content": "Generate a more distinctive product name."}],
        )
        calls_after_first = len(provider.calls)
        second = route(
            provider,
            "Thanks",
            history=[
                {"role": "user", "content": "Generate a more distinctive product name."},
                {"role": "assistant", "content": "Here are stronger candidates."},
            ],
        )

        self.assertEqual(first["executor"], "qwen3.6:27b")
        self.assertEqual(second["executor"], "qwen3.5:9b")
        self.assertFalse(second["routing_control"])
        self.assertEqual(len(provider.calls), calls_after_first)

    def test_fast_paths_do_not_call_a_model(self) -> None:
        simple = FakeProvider()
        self.assertFalse(route(simple, "What is an FFT?")["semantic_assessment"])
        self.assertFalse(simple.calls)

        efficient = FakeProvider()
        self.assertFalse(route(efficient, AMBIGUOUS_TASK, coordinator_config("efficient"))["semantic_assessment"])
        self.assertFalse(efficient.calls)

        no_resident = FakeProvider(())
        self.assertTrue(route(no_resident, AMBIGUOUS_TASK)["semantic_assessment"])
        self.assertEqual(len(no_resident.calls), 1)

        disabled = FakeProvider()
        self.assertFalse(route(disabled, AMBIGUOUS_TASK, coordinator_config(semantic=False))["semantic_assessment"])
        self.assertFalse(disabled.calls)

        stopped = threading.Event()
        stopped.set()
        cancelled = FakeProvider()
        self.assertFalse(route(cancelled, AMBIGUOUS_TASK, cancel_event=stopped)["semantic_assessment"])
        self.assertFalse(cancelled.calls)

    def test_partial_semantic_assessment_is_ignored(self) -> None:
        provider = FakeProvider(response='{"intent":"coding","code":1}')
        decision = route(provider, "Explain a robust architecture for this service.")

        self.assertEqual(len(provider.calls), 1)
        self.assertFalse(decision["semantic_assessment"])

    def test_semantic_reasoning_promotes_the_strong_reasoner(self) -> None:
        provider = FakeProvider(
            response=semantic_json(
                intent="reasoning",
                code=0.05,
                agent=0.02,
                reasoning=0.96,
                general=0.75,
                research=0.05,
                risk=0.12,
                complexity=0.78,
                confidence=0.96,
                control_confidence=0.96,
                reason="comparative technical reasoning",
            )
        )
        decision = route(
            provider,
            "Compare FFT and wavelet methods for denoising a noisy ECG and justify the tradeoffs.",
        )

        self.assertEqual(decision["executor"], "qwen3.6:27b")
        self.assertEqual(decision["task_kind"], "deep reasoning")

    def test_mixed_task_role_uses_dominant_evidence_instead_of_fixed_priority(self) -> None:
        provider = FakeProvider(
            response=semantic_json(
                intent="coding",
                code=1,
                agent=0.2,
                reasoning=0.75,
                general=0.3,
                research=0.4,
                risk=0.6,
                complexity=0.85,
                confidence=0.9,
                control_confidence=0.9,
                needs_plan=True,
                reason="code-level architecture planning",
            )
        )
        decision = route(
            provider,
            "DESIGN A ROBUST QUEUE ARCHITECTURE AND EXPLAIN THE CONSISTENCY, LATENCY, AND RECOVERY TRADEOFFS",
        )

        self.assertGreater(decision["signals"]["reasoning"], decision["signals"]["code"])
        self.assertEqual(decision["preference_role"], "reasoning")
        self.assertEqual(decision["task_kind"], "deep reasoning")

    def test_visual_quality_scales_with_policy(self) -> None:
        response = semantic_json(
            intent="visual",
            code=0.02,
            agent=0.02,
            reasoning=0.18,
            general=0.72,
            research=0.02,
            vision=1,
            risk=0.02,
            complexity=0.22,
            confidence=0.95,
            control_confidence=0.95,
            reason="simple image description",
        )
        messages = [{"role": "user", "content": "What is shown here?", "image_paths": ["/tmp/reference.png"]}]
        adaptive = CORE.select_orchestrator_route(
            FakeProvider(response=response), coordinator_config("adaptive"), messages, Path("/tmp")
        )
        quality = CORE.select_orchestrator_route(
            FakeProvider(response=response), coordinator_config("quality"), messages, Path("/tmp")
        )

        self.assertEqual(adaptive["executor"], "qwen3.5:9b")
        self.assertEqual(quality["executor"], "qwen3.6:27b")
        for participant in (quality["executor"], quality["planner"], quality["reviewer"]):
            if participant:
                self.assertTrue(FakeProvider().supports(participant, "vision"), participant)

    def test_role_preferences_are_soft_and_reported_in_routes(self) -> None:
        config = coordinator_config(semantic=False)
        config["coordinator_role_preferences"] = {"coding": "devstral-small-2:latest"}
        decision = route(FakeProvider(()), "Implement and test a Python API change.", config)

        self.assertEqual(decision["preference_role"], "coding")
        self.assertEqual(decision["preferred_model"], "devstral-small-2:latest")
        preferred = next(item for item in decision["candidates"] if item["model"] == "devstral-small-2:latest")
        self.assertTrue(preferred["preferred"])

        config["coordinator_role_preferences"] = {"coding": "removed:model"}
        fallback = route(FakeProvider(()), "Implement and test a Python API change.", config)
        self.assertEqual(fallback["preferred_model"], "")

    def test_structured_specialist_demand(self) -> None:
        provider = FakeProvider()
        config = coordinator_config()
        parent = {"executor": "qwen3-coder:30b", "policy": "adaptive"}

        routine = CORE.select_coordinator_specialist(
            provider,
            config,
            {"specialty": "vision", "task": "Describe the input.", "quality": "routine", "risk": "low"},
            parent,
        )
        critical = CORE.select_coordinator_specialist(
            provider,
            config,
            {"specialty": "vision", "task": "Assess the input.", "quality": "high", "risk": "high"},
            parent,
        )

        self.assertEqual(routine["specialist"], "qwen3.5:9b")
        self.assertEqual(critical["specialist"], "qwen3.6:27b")
        self.assertLess(routine["quality_demand"], critical["quality_demand"])

    def test_image_path_overrides_stale_nonvision_specialist_decision(self) -> None:
        provider = FakeProvider(response="visual evidence")
        config = coordinator_config(semantic=False)
        parent = {"executor": "qwen3-coder:30b", "policy": "adaptive", "delegations": []}
        stale = {
            "specialty": "code_review",
            "specialist": "qwen3-coder:30b",
            "policy": "adaptive",
            "quality_demand": 0.4,
            "capability_gain": 0.2,
            "independent": False,
        }
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "screen.png"
            image.write_bytes(b"image")
            call = {
                "name": "consult_specialist",
                "specialty": "code_review",
                "path": str(image),
                "task": "Review the screenshot",
                "quality": "routine",
                "risk": "low",
            }
            self.assertEqual(CORE.coordinator_specialty(call), "vision")
            code, _output, record = CORE.execute_coordinator_delegation(
                provider,
                config,
                Path(directory),
                call,
                parent,
                [{"role": "user", "content": "What is shown?", "image_paths": [str(image)]}],
                decision=stale,
            )

        self.assertEqual(code, 0)
        self.assertEqual(record["specialty"], "vision")
        self.assertNotEqual(record["specialist"], "qwen3-coder:30b")
        self.assertTrue(provider.supports(record["specialist"], "vision"))

    def test_visual_executor_ranking_uses_provider_modality_support(self) -> None:
        provider = FakeProvider()
        messages = [{"role": "user", "content": "Inspect this", "image_paths": ["/tmp/reference.png"]}]

        def inflated_capabilities(model: Any) -> dict[str, float]:
            values = CORE.capabilities_for(model, CORE.PATHS)
            if getattr(model, "name", str(model)) == "qwen3-coder:30b":
                values["vision"] = 1.0
            return values

        with patch.object(CORE, "model_capabilities", side_effect=inflated_capabilities):
            decision = CORE.select_orchestrator_route(
                provider,
                coordinator_config(semantic=False),
                messages,
                Path("/tmp"),
            )
        self.assertTrue(provider.supports(decision["executor"], "vision"))

    def test_configuration_and_coordinator_blocks_round_trip(self) -> None:
        config = coordinator_config()
        self.assertEqual(CORE.configure_orchestrator(config, ["semantic", "off"]), "coordinator semantic: off")
        self.assertFalse(config["orchestrator_semantic_routing"])
        self.assertEqual(CORE.configure_orchestrator(config, ["semantic", "on"]), "coordinator semantic: on")
        blocks = CORE.sanitize_blocks(
            [
                {"role": "coordinator", "text": "FLOW  coder > vision"},
                {"role": "action", "text": "WEB SEARCH  COMPLETE"},
            ]
        )
        self.assertEqual(
            blocks,
            [
                {"role": "coordinator", "text": "FLOW  coder > vision"},
                {"role": "action", "text": "WEB SEARCH  COMPLETE"},
            ],
        )
        legacy = CORE.sanitize_blocks(
            [
                {"role": "asusai", "text": "Before the rename"},
                {"role": "dairack", "text": "During migration"},
            ]
        )
        self.assertEqual(
            legacy,
            [
                {"role": "assistant", "text": "Before the rename"},
                {"role": "assistant", "text": "During migration"},
            ],
        )

    def test_action_presentation_is_typed_for_every_runtime_tool(self) -> None:
        calls = (
            {"name": "shell", "cmd": "uname -a"},
            {"name": "patch", "patch": "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+new\n"},
            {"name": "read_file", "path": "src/main.py", "line": "12"},
            {"name": "list_dir", "path": "src"},
            {"name": "grep", "query": "permission policy", "path": "src"},
            {"name": "search_project", "query": "permission policy"},
            {"name": "index_project", "path": "."},
            {"name": "consult_specialist", "task": "Review this boundary"},
            {"name": "analyze_image", "path": "diagram.png"},
            {"name": "web_search", "query": "Ollama release notes"},
            {"name": "web_open", "url": "https://example.com/docs"},
        )
        for call in calls:
            with self.subTest(tool=call["name"]):
                presentation = CORE.tool_presentation(call)
                request = CORE.tool_request_display(call)
                result = CORE.tool_result_display(call, 0, "evidence", "read-auto", 1.25)
                self.assertTrue(presentation["display_name"])
                self.assertTrue(CORE.tool_activity_label(call))
                self.assertTrue(request.startswith("ACTION REQUEST\n"))
                self.assertIn(str(presentation["display_name"]).upper(), request)
                self.assertTrue("COMPLETE" in result or "APPLIED" in result)
                self.assertIn("ACCESS  READ-AUTO", result)
                self.assertIn("RESULT\nevidence", result)

        grep_call = {"name": "grep", "query": "password|token", "path": "src"}
        grep_request = CORE.tool_request_display(grep_call)
        grep_result = CORE.tool_result_display(grep_call, 0, "match", "approved", 0.1)
        self.assertIn("ROOT  src", grep_request)
        self.assertIn("QUERY  password|token", grep_request)
        self.assertIn("ROOT  src", grep_result)
        self.assertIn("QUERY  password|token", grep_result)

    def test_cancellable_process_runner_stops_the_process_tree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cancelled = threading.Event()
            timer = threading.Timer(0.2, cancelled.set)
            started = time.monotonic()
            timer.start()
            try:
                code, output = CORE.run_argv(
                    [sys.executable, "-c", "import time; time.sleep(10)"],
                    Path(directory),
                    timeout=5,
                    cancel_event=cancelled,
                )
            finally:
                timer.join(timeout=1)

        self.assertEqual(code, 130)
        self.assertIn("interrupted", output)
        self.assertLess(time.monotonic() - started, 3)

    def test_network_actions_respect_preflight_cancellation(self) -> None:
        cancelled = threading.Event()
        cancelled.set()
        code, output = CORE.internet_search("latest Ollama release", cancel_event=cancelled)
        self.assertEqual(code, 130)
        self.assertIn("interrupted", output)

    def test_untrusted_web_text_cannot_emit_terminal_controls(self) -> None:
        unsafe = "title\x1b[2J\rreplacement\x07\u202e"
        cleaned = CORE.sanitize_terminal_text(unsafe)

        self.assertNotIn("\x1b", cleaned)
        self.assertNotIn("\r", cleaned)
        self.assertNotIn("\x07", cleaned)
        self.assertNotIn("\u202e", cleaned)
        self.assertIn("title", cleaned)
        self.assertIn("replacement", cleaned)

    def test_startup_chat_reference_is_explicit_by_default(self) -> None:
        args = SimpleNamespace(chat=None, resume=None, new_chat=False)
        config = {**coordinator_config(), "last_chat": "saved-chat", "startup_chat": "new"}
        self.assertEqual(CORE.startup_chat_reference(args, config), "")

        config["startup_chat"] = "resume-last"
        self.assertEqual(CORE.startup_chat_reference(args, config), "saved-chat")
        args.resume = "chosen-chat"
        self.assertEqual(CORE.startup_chat_reference(args, config), "chosen-chat")
        args.new_chat = True
        self.assertEqual(CORE.startup_chat_reference(args, config), "")

    def test_image_discovery_falls_back_without_ripgrep(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "reference.png"
            image.write_bytes(b"not-empty")
            canonical_image = image.resolve()
            ignored = root / "node_modules" / "ignored.png"
            ignored.parent.mkdir()
            ignored.write_bytes(b"not-empty")

            with patch.object(CORE.shutil, "which", return_value=None):
                discovered = CORE.discover_image_files(root)

        self.assertEqual(discovered, [canonical_image])

    def test_visual_prompt_is_restricted_to_vision_models(self) -> None:
        provider = FakeProvider()
        decision = CORE.select_orchestrator_route(
            provider,
            coordinator_config(semantic=False),
            [{"role": "user", "content": "What is shown here?", "image_paths": ["/tmp/reference.png"]}],
            Path("/tmp"),
        )

        selected = next(model for model in MODELS if model.name == decision["executor"])
        self.assertIn("vision", selected.capabilities)

    def test_windows_file_url_conversion_preserves_drive_and_unc_paths(self) -> None:
        drive_path = CORE.file_url_path("file:///C:/Users/Ryan/image.png", windows=True).replace("\\", "/")
        unc_path = CORE.file_url_path("file://server/share/image.png", windows=True).replace("\\", "/")
        self.assertEqual(drive_path, "C:/Users/Ryan/image.png")
        self.assertEqual(
            unc_path,
            "//server/share/image.png",
        )
        self.assertEqual(
            CORE.file_url_path("file:///home/user/literal%2520name.png", windows=False),
            "/home/user/literal%20name.png",
        )

    def test_native_tool_capability_errors_are_not_silently_downgraded(self) -> None:
        class BrokenProvider:
            def supports(self, _model: str, _capability: str) -> bool:
                raise OSError("transport unavailable")

        with self.assertRaisesRegex(RuntimeError, "could not verify"):
            CORE.native_tools_for(BrokenProvider(), "model", True, {"execution_scope": "agentic"})

    def test_non_streaming_one_shot_prints_returned_response(self) -> None:
        class OneShotProvider:
            def chat(self, *_args: Any, **_kwargs: Any) -> str:
                return "VISIBLE_RESPONSE"

        config = {**coordinator_config(), "model_mode": "direct"}
        args = SimpleNamespace(host=None, model=None, no_stream=True, max_tokens=32)
        output = io.StringIO()
        with patch.object(CORE, "provider_from_config", return_value=OneShotProvider()), redirect_stdout(output):
            code = CORE.one_shot(args, config, "test")
        self.assertEqual(code, 0)
        self.assertIn("VISIBLE_RESPONSE", output.getvalue())

    def test_agent_prompt_preserves_read_only_audit_scope(self) -> None:
        prompt = CORE.system_prompt(Path("/tmp"), agent=True)
        directive = CORE.coordinator_executor_directive(
            {"mode": "orchestrator", "policy": "adaptive", "delegations": []},
            coordinator_config(),
        )

        self.assertIn("audit requests are read-only", prompt)
        self.assertIn("inspection, explanation, review, and audit remain read-only", directive)

    def test_action_completion_arbiter_rejects_a_future_promise(self) -> None:
        provider = FakeProvider(
            response=json.dumps(
                {
                    "complete": False,
                    "needs_action": True,
                    "confidence": 0.98,
                    "reason": "candidate only promises another search",
                }
            )
        )
        route_state = {
            "executor": "qwen3.5:9b",
            "action_contract": {"capability": "runtime_action", "preferred_tool": "auto"},
            "semantic_assessment": {"model": "qwen3.5:9b"},
        }
        messages = [
            {"role": "user", "content": "Find the project named Lockout"},
            {
                "role": "tool",
                "tool_name": "list_dir",
                "content": "Structured tool result:\ntool: list_dir\nexit_code: 0\noutput:\nwrong directory",
            },
        ]

        result = CORE.assess_action_completion(
            provider,
            coordinator_config(),
            route_state,
            messages,
            "Please wait while I check another location.",
        )

        self.assertFalse(result["complete"])
        self.assertTrue(result["needs_action"])
        self.assertIn("exactly one", CORE.action_completion_directive(result))

    def test_executed_action_requires_followthrough_without_an_explicit_contract(self) -> None:
        route_state = {"mode": "orchestrator", "action_contract": {}}

        self.assertFalse(CORE.action_completion_required(route_state, 0, True))
        self.assertTrue(CORE.action_completion_required(route_state, 1, True))
        self.assertFalse(CORE.action_completion_required(route_state, 1, False))
        self.assertFalse(CORE.action_completion_required({"mode": "direct"}, 1, True))

    def test_find_paths_locates_a_named_unreal_project_without_shell_syntax(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "Projects" / "Lockout"
            project.mkdir(parents=True)
            marker = project / "Lockout.uproject"
            marker.write_text("{}", encoding="ascii")

            code, output = CORE.execute_tool_call(
                {"name": "find_paths", "query": "Lockout", "path": str(root)},
                root,
            )

        self.assertEqual(code, 0)
        self.assertIn(marker.name, output)
        self.assertIn(project.name, output)
        self.assertIn("project", output)

    def test_structural_completion_detection_is_bounded_and_general(self) -> None:
        self.assertIn(
            "token limit", CORE.response_incomplete_reason("A complete-looking sentence.", {"done_reason": "length"})
        )
        self.assertIn("code fence", CORE.response_incomplete_reason("```python\nprint('unfinished')"))
        self.assertIn("parenthesis", CORE.response_incomplete_reason("The relevant call is shown in ("))
        self.assertEqual(CORE.response_incomplete_reason("A concise complete response."), "")
        # Ordinary trailing punctuation is a normal way for complete answers to end and
        # must not force a silent full regeneration.
        self.assertEqual(CORE.response_incomplete_reason("Here are the steps:"), "")
        self.assertEqual(CORE.response_incomplete_reason("I was checking,"), "")
        self.assertEqual(CORE.response_incomplete_reason("first; second;"), "")

    def test_compaction_rebuilds_grounded_memory_without_calling_a_model(self) -> None:
        class NoCompactionModel:
            def chat_stream(self, *_args: Any, **_kwargs: Any) -> Any:
                raise AssertionError("grounded compaction must not call a model")

        native_read = {
            "type": "function",
            "function": {"name": "read_file", "arguments": {"path": "SECURITY.md"}},
        }
        native_patch = {
            "type": "function",
            "function": {"name": "patch", "arguments": {"patch": "*** invalid"}},
        }
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "Audit the code and report findings only."},
            {"role": "assistant", "content": "I will inspect the repository."},
            {"role": "assistant", "content": "", "tool_calls": [native_read]},
            {
                "role": "tool",
                "tool_name": "read_file",
                "content": "Structured tool result:\nsummary: read_file SECURITY.md\nexit_code: 0\noutput:\n1 security line",
            },
            {"role": "assistant", "content": "", "tool_calls": [native_patch]},
            {"role": "tool", "tool_name": "patch", "content": "Action was not executed: by user"},
            {"role": "user", "content": "What were you trying to do?"},
            {"role": "assistant", "content": "I was trying to explain,"},
        ]
        chat = {
            "cwd": "/tmp",
            "project_root": "/tmp/project",
            "summary": "Invented claim about src/dairack/security.py",
            "summary_upto": 7,
            "summary_format": "",
        }
        config = {**coordinator_config(), "auto_compact_keep_recent": 4}

        should, reason = CORE.should_auto_compact(messages, chat, config)
        changed, detail = CORE.compact_chat_memory(
            NoCompactionModel(),
            "unused",
            messages,
            chat,
            config,
            keep_recent=4,
        )

        self.assertTrue(should)
        self.assertIn("upgrading", reason)
        self.assertTrue(changed)
        self.assertIn("rebuilt grounded memory", detail)
        self.assertEqual(chat["summary_format"], CORE.GROUNDED_MEMORY_FORMAT)
        self.assertIn("User [1]: Audit the code", chat["summary"])
        self.assertIn("Action result [4]: read_file SECURITY.md -> exit 0", chat["summary"])
        self.assertIn("Denied action [6]: patch", chat["summary"])
        self.assertNotIn("src/dairack/security.py", chat["summary"])

    def test_compacted_context_replaces_covered_messages_with_grounded_memory(self) -> None:
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "OLD_USER_SENTINEL"},
            {"role": "assistant", "content": "OLD_ASSISTANT_SENTINEL"},
            {"role": "user", "content": "current request"},
        ]
        chat = {
            "cwd": "/tmp",
            "summary": "Grounded memory of the earlier exchange.",
            "summary_upto": 3,
            "summary_format": CORE.GROUNDED_MEMORY_FORMAT,
        }

        active = CORE.request_context_messages(
            messages,
            chat,
            coordinator_config(semantic=False),
            Path("/tmp"),
            include_retrieval=False,
        )
        rendered = "\n".join(str(message.get("content") or "") for message in active)

        self.assertIn("Grounded memory of the earlier exchange", rendered)
        self.assertIn("current request", rendered)
        self.assertNotIn("OLD_USER_SENTINEL", rendered)
        self.assertNotIn("OLD_ASSISTANT_SENTINEL", rendered)

    def test_compaction_reduces_a_token_heavy_tail_until_the_next_request_has_headroom(self) -> None:
        config = {
            **coordinator_config(semantic=False),
            "num_ctx": 4096,
            "context_budget_ratio": 0.82,
            "auto_compact_keep_recent": 16,
        }
        messages: list[dict[str, Any]] = [{"role": "system", "content": CORE.system_prompt(Path("/tmp"), True)}]
        for index in range(12):
            messages.extend(
                [
                    {"role": "user", "content": f"task {index}"},
                    {"role": "assistant", "content": (f"result {index} " * 350).strip()},
                ]
            )
        messages.append({"role": "user", "content": "review the current implementation"})
        chat = {
            "cwd": "/tmp",
            "summary": "",
            "summary_upto": 1,
            "summary_format": CORE.GROUNDED_MEMORY_FORMAT,
        }

        changed, detail = CORE.compact_chat_memory(None, "unused", messages, chat, config)
        source = CORE.summarized_context_source(messages, chat)
        active = CORE.active_context_messages(
            source,
            chat["summary"],
            config,
            summary_required=True,
        )
        fitted, _tools = CORE.fit_agent_request_context_messages(active, config, CORE.agent_tool_schemas())

        self.assertTrue(changed)
        self.assertIn("request", detail)
        self.assertEqual(chat["summary_upto"], len(messages) - 1)
        self.assertEqual(source[-1]["content"], "review the current implementation")
        self.assertLess(sum(CORE.estimate_message_tokens(message) for message in active), CORE.context_budget(config))
        self.assertTrue(fitted)
        should, reason = CORE.should_auto_compact(messages, chat, config)
        self.assertFalse(should, reason)

    def test_grounded_memory_retains_action_evidence_and_avoids_duplicate_summary(self) -> None:
        config = {**coordinator_config(), "num_ctx": 4096}
        small = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        self.assertEqual(CORE.active_context_messages(small, "summary", config), small)

        messages: list[dict[str, Any]] = [{"role": "system", "content": "system"}]
        messages.extend({"role": "user", "content": f"User detail {index} " + "x" * 1000} for index in range(12))
        messages.append(
            {
                "role": "user",
                "content": "Structured tool result:\nsummary: read_file critical.py\nexit_code: 0\noutput:\ncritical evidence",
            }
        )
        messages.extend(
            {"role": "assistant", "content": f"Assistant detail {index} " + "y" * 1000} for index in range(12)
        )

        summary = CORE.grounded_memory_summary(messages, len(messages), config, {"cwd": "/tmp"})

        self.assertIn("Action result [13]: read_file critical.py", summary)


class LoopResilienceTests(unittest.TestCase):
    def test_read_batch_rejects_provider_calls_when_agent_is_off_or_finalizing(self) -> None:
        tui = object.__new__(CORE.DairackTui)
        tui.config = {"agent": False, "permission_mode": "read-auto"}
        native_calls = [
            {"function": {"name": "read_file", "arguments": {"path": "a.py"}}},
            {"function": {"name": "list_dir", "arguments": {"path": "."}}},
        ]

        with patch.object(CORE, "read_only_batch") as read_batch:
            self.assertFalse(tui.maybe_run_read_batch(native_calls, ""))
            read_batch.assert_not_called()

            tui.config["agent"] = True
            self.assertFalse(tui.maybe_run_read_batch(native_calls, "", finalizing=True))
            read_batch.assert_not_called()

    def test_fallback_keeps_input_while_an_action_awaits_approval(self) -> None:
        tui = object.__new__(CORE.DairackTui)
        tui.input = SimpleNamespace(text="follow-up question")
        tui.pending_images = []
        tui.busy = False
        tui.pending_tool = {"name": "read_file", "path": "a.py"}
        tui._queued_prompts = []
        tui.append_system = Mock()

        tui.submit()

        self.assertEqual(tui.input.text, "follow-up question")
        tui.append_system.assert_called_once_with("Approve or reject the pending action first with /allow or /deny.")

        tui.append_system.reset_mock()
        tui.handle_command = Mock()
        tui.input.text = "/help"
        tui.submit()
        self.assertEqual(tui.input.text, "/help")
        tui.handle_command.assert_not_called()

        tui.input.text = "/deny"
        tui.submit()
        self.assertEqual(tui.input.text, "")
        tui.handle_command.assert_called_once_with("/deny")

    def test_fallback_denial_releases_the_held_prompt_queue(self) -> None:
        tui = object.__new__(CORE.DairackTui)
        tui.pending_tool = {"name": "read_file", "path": "a.py"}
        tui.messages = []
        tui.append_action = Mock()
        tui.save_current_chat = Mock()
        tui.flush_queued_prompt = Mock()
        tui.input = object()
        tui.app = SimpleNamespace(
            layout=SimpleNamespace(focus=Mock()),
            invalidate=Mock(),
        )

        tui.deny_pending_tool()

        self.assertIsNone(tui.pending_tool)
        tui.flush_queued_prompt.assert_called_once_with(False)

    def test_truncate_middle_retains_head_and_tail(self) -> None:
        text = "start-" + "x" * 50000 + "-end"
        bounded = CORE.truncate_middle(text, 2000)
        self.assertLess(len(bounded), 2200)
        self.assertTrue(bounded.startswith("start-"))
        self.assertTrue(bounded.endswith("-end"))
        self.assertIn("omitted from the middle", bounded)
        self.assertEqual(CORE.truncate_middle("short", 100), "short")

    def test_action_loop_guard_blocks_the_third_identical_read(self) -> None:
        guard = CORE.ActionLoopGuard()
        call = {"name": "read_file", "path": "src/app.py", "reason": ""}
        self.assertEqual(guard.refusal(call), "")
        self.assertEqual(guard.record(call, "content"), "")
        self.assertEqual(guard.refusal(call), "")
        self.assertIn("unchanged", guard.record(call, "content"))
        self.assertIn("already ran twice", guard.refusal(call))
        self.assertFalse(guard.force_synthesis)
        self.assertTrue(guard.refusal(call))
        self.assertTrue(guard.force_synthesis)

    def test_action_loop_guard_resets_after_state_changing_actions(self) -> None:
        guard = CORE.ActionLoopGuard()
        read = {"name": "read_file", "path": "src/app.py", "reason": ""}
        guard.record(read, "one")
        guard.record(read, "one")
        guard.record({"name": "shell", "command": "pytest", "reason": ""}, "output")
        self.assertEqual(guard.refusal(read), "")
        self.assertEqual(guard.record(read, "two"), "")
        # Different parameters are always a fresh action.
        other = {"name": "read_file", "path": "src/other.py", "reason": ""}
        self.assertEqual(guard.refusal(other), "")

    def test_action_loop_guard_allows_changed_read_results(self) -> None:
        guard = CORE.ActionLoopGuard()
        call = {"name": "web_search", "query": "current release", "reason": ""}
        guard.record(call, "first result")
        guard.record(call, "updated result")
        self.assertEqual(guard.refusal(call), "")
        self.assertIn("unchanged", guard.record(call, "updated result"))
        self.assertTrue(guard.refusal(call))

    def test_action_loop_guard_resets_when_project_index_changes(self) -> None:
        guard = CORE.ActionLoopGuard()
        search = {"name": "search_project", "query": "router", "reason": ""}
        guard.record(search, "old index result")
        guard.record(search, "old index result")
        self.assertTrue(guard.refusal(search))
        guard.record({"name": "index_project", "path": ".", "reason": ""}, "index refreshed")
        self.assertEqual(guard.refusal(search), "")
        self.assertFalse(guard.force_synthesis)

    def test_transient_stream_errors_are_distinguished_from_request_errors(self) -> None:
        self.assertTrue(CORE.transient_stream_error(CORE.OllamaError("Ollama stream stalled or disconnected")))
        self.assertTrue(CORE.transient_stream_error(CORE.OllamaError("could not reach Ollama at host: refused")))
        self.assertTrue(CORE.transient_stream_error(CORE.OllamaError("Ollama returned HTTP 502: Bad Gateway")))
        self.assertTrue(CORE.transient_stream_error(TimeoutError()))
        self.assertFalse(CORE.transient_stream_error(CORE.OllamaError("Ollama returned HTTP 404: no such model")))
        self.assertFalse(CORE.transient_stream_error(ValueError("unrelated")))

    def test_plain_agent_retries_transport_and_repairs_one_malformed_action(self) -> None:
        class RecoveringProvider(FakeProvider):
            def __init__(self) -> None:
                super().__init__(response="")
                self.responses = [
                    '<tool name="read_file">{"path":"app.py"</tool>',
                    'read_file{"path":"app.py"}',
                    "The file was read successfully.",
                ]
                self.attempts = 0

            def chat(self, model: str, messages: list[dict[str, str]], **kwargs: Any) -> str:
                self.calls.append({"model": model, "messages": messages, "kwargs": kwargs})
                self.attempts += 1
                if self.attempts == 1:
                    raise CORE.OllamaError("Ollama returned HTTP 502: Bad Gateway")
                self.last_stats = {"done_reason": "stop"}
                return self.responses.pop(0)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text("PLAIN_FALLBACK = True\n", encoding="utf-8")
            config = coordinator_config(semantic=False)
            config.update(
                {
                    "model_mode": "direct",
                    "model": "qwen3.5:9b",
                    "agent": True,
                    "auto_compact": False,
                    "permission_mode": "read-auto",
                }
            )
            messages = [
                {"role": "system", "content": "test"},
                {"role": "user", "content": "Read app.py"},
            ]
            chat: dict[str, Any] = {"summary": "", "project_root": "", "last_route": {}}
            provider = RecoveringProvider()
            output = io.StringIO()

            with redirect_stdout(output):
                CORE.chat_turn(provider, "qwen3.5:9b", messages, config, root, chat)

        self.assertEqual(provider.attempts, 4)
        self.assertIn("The file was read successfully.", output.getvalue())
        self.assertNotIn("invalid JSON", output.getvalue())
        self.assertTrue(
            any(
                message.get("role") == "user"
                and "Return exactly one corrected tool request" in str(message.get("content") or "")
                for message in messages
            )
        )

    def test_plain_agent_review_revises_once_then_delivers(self) -> None:
        class ReviewingProvider(FakeProvider):
            def __init__(self) -> None:
                super().__init__(response="")
                self.answers = ["First draft answer.", "Corrected final answer."]
                self.reviews = ["VERDICT: REVISE\nFEEDBACK: cite the source file."]

            def chat(self, model: str, messages: list[dict[str, str]], **kwargs: Any) -> str:
                self.calls.append({"model": model, "messages": messages, "kwargs": kwargs})
                self.last_stats = {"done_reason": "stop"}
                return self.answers.pop(0)

            def chat_stream(self, model: str, messages: list[dict[str, str]], **kwargs: Any) -> Any:
                # The independent reviewer collects its verdict through chat_stream.
                self.last_stats = {"done_reason": "stop"}
                yield self.reviews.pop(0) if self.reviews else "VERDICT: PASS"

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = coordinator_config(semantic=False)
            config.update({"model_mode": "direct", "model": "qwen3.5:9b", "agent": True, "auto_compact": False})
            route = {"reviewer": "qwen3.5:9b", "executor": "qwen3.5:9b", "policy": "quality", "task_kind": "coding"}
            chat: dict[str, Any] = {"summary": "", "project_root": "", "last_route": route}
            messages = [{"role": "system", "content": "test"}, {"role": "user", "content": "Explain the router."}]
            provider = ReviewingProvider()
            output = io.StringIO()
            with redirect_stdout(output), patch.object(CORE, "select_orchestrator_route", return_value=route):
                CORE.chat_turn(provider, "qwen3.5:9b", messages, config, root, chat)

        text = output.getvalue()
        self.assertIn("review requested corrections; revising", text)
        self.assertIn("Corrected final answer.", text)
        assistant_messages = [m for m in messages if m.get("role") == "assistant"]
        self.assertEqual(assistant_messages[-1]["content"], "Corrected final answer.")
        # The revised answer is re-reviewed exactly once and, here, accepted.
        self.assertEqual(chat["last_route"]["review"]["verdict"], "pass")
        self.assertEqual(chat["last_route"]["review"]["round"], 2)

    def test_plain_agent_executes_a_tool_then_delivers(self) -> None:
        class ToolThenAnswerProvider(FakeProvider):
            def __init__(self) -> None:
                super().__init__(response="")
                self.responses = ['read_file{"path":"app.py"}', "app.py defines the entry point."]

            def chat(self, model: str, messages: list[dict[str, str]], **kwargs: Any) -> str:
                self.calls.append({"model": model, "messages": messages, "kwargs": kwargs})
                self.last_stats = {"done_reason": "stop"}
                return self.responses.pop(0)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text("def main():\n    return 0\n", encoding="utf-8")
            config = coordinator_config(semantic=False)
            config.update(
                {
                    "model_mode": "direct",
                    "model": "qwen3.5:9b",
                    "agent": True,
                    "auto_compact": False,
                    "permission_mode": "read-auto",
                }
            )
            chat: dict[str, Any] = {"summary": "", "project_root": "", "last_route": {}}
            messages = [{"role": "system", "content": "test"}, {"role": "user", "content": "Read app.py"}]
            provider = ToolThenAnswerProvider()
            output = io.StringIO()
            with redirect_stdout(output):
                CORE.chat_turn(provider, "qwen3.5:9b", messages, config, root, chat)

        text = output.getvalue()
        self.assertIn("def main()", text)
        self.assertIn("app.py defines the entry point.", text)
        tool_results = [m for m in messages if str(m.get("content") or "").startswith("Structured tool result:")]
        self.assertEqual(len(tool_results), 1)


class TextualInteractionTests(unittest.IsolatedAsyncioTestCase):
    async def test_inference_failure_is_available_to_the_follow_up_turn(self) -> None:
        class FailingThenExplainingProvider(FakeProvider):
            def __init__(self) -> None:
                super().__init__(response="")

            def chat_stream(self, model: str, messages: list[dict[str, str]], **kwargs: Any) -> Any:
                self.calls.append({"model": model, "messages": messages, "kwargs": kwargs})
                if len(self.calls) == 1:
                    yield "I can see"
                    raise RuntimeError(
                        "Ollama returned HTTP 400: request (8227 tokens) exceeds the available context size "
                        "(8192 tokens)"
                    )
                yield "The previous image request exceeded the model context and did not complete."

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "reference.png"
            image.write_bytes(b"image")
            CORE.CONFIG_PATH = root / "config.json"
            CORE.HISTORY_PATH = root / "history"
            CORE.CHAT_DIR = root / "chats"
            CORE.INDEX_DB_PATH = root / "index.sqlite3"
            CORE.CHECKPOINT_DIR = root / "checkpoints"
            CORE.APP_DATA_DIR = root
            config = coordinator_config(semantic=False)
            config.update(
                {
                    "model_mode": "direct",
                    "model": "qwen3.5:9b",
                    "agent": True,
                    "auto_compact": False,
                    # This test asserts runtime-event retention across turns, not
                    # window-eviction behavior; keep the window comfortably large.
                    "num_ctx": 16384,
                }
            )
            provider = FailingThenExplainingProvider()
            app = ui.build_textual_app(CORE, CORE.DairackTui, provider, "test", config, root)

            async with app.run_test(size=(80, 26)) as pilot:
                app._pending_images = [image]
                app.query_one("#composer", ui.Composer).load_text("What is shown here?")
                await pilot.press("enter")
                for _ in range(120):
                    await pilot.pause(0.025)
                    if not app.busy and len(provider.calls) == 1:
                        break

                runtime_events = [
                    message
                    for message in app.messages
                    if str(message.get("content") or "").startswith("Runtime event:")
                ]
                self.assertEqual(len(runtime_events), 1)
                self.assertIn("context limit exceeded", runtime_events[0]["content"])

                app.query_one("#composer", ui.Composer).load_text("what happened")
                await pilot.press("enter")
                for _ in range(120):
                    await pilot.pause(0.025)
                    if not app.busy and len(provider.calls) == 2:
                        break

                second_context = "\n".join(
                    str(message.get("content") or "") for message in provider.calls[1]["messages"]
                )
                self.assertIn("Runtime event:", second_context)
                self.assertIn("context limit exceeded", second_context)
                self.assertIn("previous image request exceeded", app.render_transcript_text())

    async def test_direct_answer_rejects_rogue_delegation_without_ui_noise(self) -> None:
        class RogueGreetingProvider(FakeProvider):
            def __init__(self) -> None:
                super().__init__(response="")

            def chat_stream(self, model: str, messages: list[dict[str, str]], **kwargs: Any) -> Any:
                self.calls.append({"model": model, "messages": messages, "kwargs": kwargs})
                if len(self.calls) == 1:
                    sink = kwargs.get("tool_call_sink")
                    if callable(sink):
                        sink(
                            {
                                "type": "function",
                                "function": {
                                    "name": "consult_specialist",
                                    "arguments": {
                                        "specialty": "general",
                                        "quality": "routine",
                                        "risk": "low",
                                        "task": "Handle the greeting without delegation.",
                                    },
                                },
                            }
                        )
                    return
                yield "Hello. What can I help you with?"

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            CORE.CONFIG_PATH = root / "config.json"
            CORE.HISTORY_PATH = root / "history"
            CORE.CHAT_DIR = root / "chats"
            CORE.INDEX_DB_PATH = root / "index.sqlite3"
            CORE.CHECKPOINT_DIR = root / "checkpoints"
            CORE.APP_DATA_DIR = root
            config = coordinator_config(semantic=False)
            config.update({"agent": True, "auto_compact": False})
            provider = RogueGreetingProvider()
            app = ui.build_textual_app(CORE, CORE.DairackTui, provider, "test", config, root)

            async with app.run_test(size=(80, 26)) as pilot:
                app.query_one("#composer", ui.Composer).load_text("Hello")
                await pilot.press("enter")
                for _ in range(160):
                    await pilot.pause(0.025)
                    if not app.busy and len(provider.calls) == 2:
                        break

                self.assertFalse(app.busy)
                self.assertEqual([call["model"] for call in provider.calls], ["qwen3.5:9b", "qwen3.5:9b"])
                self.assertTrue(all(call["kwargs"]["tools"] is None for call in provider.calls))
                transcript = app.render_transcript_text()
                self.assertIn("Hello. What can I help you with?", transcript)
                self.assertNotIn("ACTION REQUEST", transcript)
                self.assertNotIn("DELEGATION", transcript)
                self.assertFalse(any(block["role"] in {"action", "coordinator"} for block in app.blocks))

    async def test_website_request_recovers_false_refusal_and_opens_the_page(self) -> None:
        class WebsiteProvider(FakeProvider):
            def __init__(self) -> None:
                super().__init__(response="")

            def chat_stream(self, model: str, messages: list[dict[str, str]], **kwargs: Any) -> Any:
                self.calls.append({"model": model, "messages": messages, "kwargs": kwargs})
                self.last_stats = {"done_reason": "stop"}
                if len(self.calls) == 1:
                    yield "I cannot access websites in this session."
                    return
                if len(self.calls) == 2:
                    sink = kwargs.get("tool_call_sink")
                    if callable(sink):
                        sink(
                            {
                                "type": "function",
                                "function": {
                                    "name": "web_open",
                                    "arguments": {"url": "https://playlockout.com/"},
                                },
                            }
                        )
                    return
                yield "LOCKOUT is presented as ReaperCorp's broadcast-combat game, with venues, weapons, and news."

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            CORE.CONFIG_PATH = root / "config.json"
            CORE.HISTORY_PATH = root / "history"
            CORE.CHAT_DIR = root / "chats"
            CORE.INDEX_DB_PATH = root / "index.sqlite3"
            CORE.CHECKPOINT_DIR = root / "checkpoints"
            CORE.APP_DATA_DIR = root
            config = coordinator_config(semantic=False)
            config.update({"agent": True, "auto_compact": False})
            provider = WebsiteProvider()
            app = ui.build_textual_app(CORE, CORE.DairackTui, provider, "test", config, root)

            with patch.object(
                CORE,
                "web_open_url",
                return_value=(
                    0,
                    "URL: https://playlockout.com/\nTitle: LOCKOUT | Official ReaperCorp Broadcast Site",
                ),
            ) as web_open:
                async with app.run_test(size=(80, 26)) as pilot:
                    app.query_one("#composer", ui.Composer).load_text("what u think of playlockout.com")
                    await pilot.press("enter")
                    for _ in range(200):
                        await pilot.pause(0.025)
                        if app.pending_tool is not None:
                            break

                    self.assertEqual(app.pending_tool["name"], "web_open")
                    self.assertIsInstance(app.screen, ui.ApprovalScreen)
                    await pilot.press("a")
                    for _ in range(200):
                        await pilot.pause(0.025)
                        if not app.busy and len(provider.calls) == 4:
                            break

                    self.assertFalse(app.busy)
                    self.assertEqual(len(provider.calls), 4)
                    web_open.assert_called_once_with(
                        "https://playlockout.com/",
                        cancel_event=app.cancel_event,
                    )
                    transcript = app.render_transcript_text()
                    self.assertNotIn("cannot access websites", transcript)
                    self.assertIn("WEB PAGE  COMPLETE", transcript)
                    self.assertIn("Official ReaperCorp Broadcast Site", transcript)
                    self.assertIn("LOCKOUT is presented", transcript)

    async def test_primary_ui_rejects_a_future_promise_after_an_action(self) -> None:
        class SequencedProvider(FakeProvider):
            def __init__(self) -> None:
                super().__init__(response="")
                self.responses = [
                    'read_file{"path":"app.py"}',
                    "I will inspect the companion file next.",
                    'read_file{"path":"other.py"}',
                    "Both files were inspected and contain the expected markers.",
                ]

            def chat_stream(self, model: str, messages: list[dict[str, str]], **kwargs: Any) -> Any:
                self.calls.append({"model": model, "messages": messages, "kwargs": kwargs})
                self.last_stats = {"done_reason": "stop"}
                yield self.responses.pop(0)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text("APP_MARKER = True\n", encoding="utf-8")
            (root / "other.py").write_text("OTHER_MARKER = True\n", encoding="utf-8")
            CORE.CONFIG_PATH = root / "config.json"
            CORE.HISTORY_PATH = root / "history"
            CORE.CHAT_DIR = root / "chats"
            CORE.INDEX_DB_PATH = root / "index.sqlite3"
            CORE.CHECKPOINT_DIR = root / "checkpoints"
            CORE.APP_DATA_DIR = root
            config = coordinator_config(semantic=False)
            config.update({"agent": True, "auto_compact": False, "permission_mode": "read-auto"})
            provider = SequencedProvider()
            app = ui.build_textual_app(CORE, CORE.DairackTui, provider, "test", config, root)
            original_select = CORE.select_orchestrator_route

            def select_without_contract(*args: Any, **kwargs: Any) -> dict[str, Any]:
                selected = original_select(*args, **kwargs)
                selected.update(
                    {
                        "action_contract": {},
                        "execution_scope": "agentic",
                        "planner": "",
                        "reviewer": "",
                    }
                )
                return selected

            assessments = [
                {
                    "complete": False,
                    "needs_action": True,
                    "confidence": 0.98,
                    "reason": "candidate only promises the second read",
                },
                {
                    "complete": True,
                    "needs_action": False,
                    "confidence": 0.99,
                    "reason": "both reads are reflected in the answer",
                },
            ]
            with (
                patch.object(CORE, "select_orchestrator_route", side_effect=select_without_contract),
                patch.object(CORE, "assess_action_completion", side_effect=assessments) as assess,
            ):
                async with app.run_test(size=(80, 26)) as pilot:
                    app.query_one("#composer", ui.Composer).load_text("Inspect app.py and other.py")
                    await pilot.press("enter")
                    for _ in range(300):
                        await pilot.pause(0.025)
                        if not app.busy and len(provider.calls) == 4:
                            break

                    self.assertFalse(app.busy)
                    self.assertEqual(len(provider.calls), 4)
                    self.assertEqual(assess.call_count, 2)
                    transcript = app.render_transcript_text()
                    self.assertNotIn("I will inspect", transcript)
                    self.assertIn("Both files were inspected", transcript)
                    self.assertTrue(app.chat["last_route"]["action_completion"]["complete"])

    async def test_transcript_entry_accepts_stream_updates_before_mount(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            CORE.CONFIG_PATH = root / "config.json"
            CORE.HISTORY_PATH = root / "history"
            CORE.CHAT_DIR = root / "chats"
            CORE.INDEX_DB_PATH = root / "index.sqlite3"
            CORE.CHECKPOINT_DIR = root / "checkpoints"
            CORE.APP_DATA_DIR = root
            app = ui.build_textual_app(
                CORE,
                CORE.DairackTui,
                FakeProvider(()),
                "test",
                coordinator_config(),
                root,
            )

            async with app.run_test(size=(80, 26)) as pilot:
                entry = ui.TranscriptEntry("assistant", "", 999)
                await entry.set_text("First streamed chunk arrived before mount.")
                await app.query_one("#transcript", ui.VerticalScroll).mount(entry)
                await pilot.pause(0.05)

                self.assertEqual(entry.source_text, "First streamed chunk arrived before mount.")
                self.assertEqual(
                    entry.query_one(ui.Markdown).source,
                    "First streamed chunk arrived before mount.",
                )

    async def test_transcript_rebuild_serializes_queued_append_and_update(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            CORE.CONFIG_PATH = root / "config.json"
            CORE.HISTORY_PATH = root / "history"
            CORE.CHAT_DIR = root / "chats"
            CORE.INDEX_DB_PATH = root / "index.sqlite3"
            CORE.CHECKPOINT_DIR = root / "checkpoints"
            CORE.APP_DATA_DIR = root
            app = ui.build_textual_app(
                CORE,
                CORE.DairackTui,
                FakeProvider(()),
                "test",
                coordinator_config(),
                root,
            )

            async with app.run_test(size=(80, 26)) as pilot:
                with app.lock:
                    app.blocks = [
                        {"role": "you", "text": "Hello"},
                        {"role": "assistant", "text": "First response"},
                    ]
                await asyncio.gather(
                    app._rebuild_transcript_main(),
                    app._append_entry_main("system", "stale callback", 1, "error"),
                )
                await pilot.pause(0.05)

                entries = app.query("#entry-1")
                self.assertEqual(len(entries), 1)
                entry = entries.first(ui.TranscriptEntry)
                self.assertEqual(entry.role, "assistant")
                self.assertEqual(entry.source_text, "First response")

                with app.lock:
                    app.blocks[1]["text"] = "Canonical streamed response"
                await asyncio.gather(
                    app._rebuild_transcript_main(),
                    app._update_entry_main(1, "stale streamed response"),
                )
                await pilot.pause(0.05)

                entries = app.query("#entry-1")
                self.assertEqual(len(entries), 1)
                entry = entries.first(ui.TranscriptEntry)
                self.assertEqual(entry.source_text, "Canonical streamed response")
                self.assertEqual(entry.query_one(ui.Markdown).source, "Canonical streamed response")

    async def test_structurally_incomplete_response_is_replaced_once(self) -> None:
        class SequencedProvider(FakeProvider):
            def __init__(self) -> None:
                super().__init__(response="")
                self.responses = [
                    "```python\nprint('unfinished')",
                    "I was checking the project and found no blocking issue.",
                ]

            def chat_stream(self, model: str, messages: list[dict[str, str]], **kwargs: Any) -> Any:
                self.calls.append({"model": model, "messages": messages, "kwargs": kwargs})
                self.last_stats = {"done_reason": "stop"}
                yield self.responses.pop(0)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            CORE.CONFIG_PATH = root / "config.json"
            CORE.HISTORY_PATH = root / "history"
            CORE.CHAT_DIR = root / "chats"
            CORE.INDEX_DB_PATH = root / "index.sqlite3"
            CORE.CHECKPOINT_DIR = root / "checkpoints"
            CORE.APP_DATA_DIR = root
            config = coordinator_config()
            config.update(
                {
                    "model_mode": "direct",
                    "model": "qwen3.5:9b",
                    "auto_compact": False,
                }
            )
            provider = SequencedProvider()
            app = ui.build_textual_app(CORE, CORE.DairackTui, provider, "test", config, root)

            async with app.run_test(size=(80, 26)) as pilot:
                app.query_one("#composer", ui.Composer).load_text("Check the project")
                await pilot.press("enter")
                for _ in range(200):
                    await pilot.pause(0.025)
                    if not app.busy and len(provider.calls) == 2:
                        break

                self.assertFalse(app.busy)
                self.assertEqual(len(provider.calls), 2)
                self.assertIn("structurally incomplete", provider.calls[1]["messages"][0]["content"])
                assistant_messages = [message for message in app.messages if message.get("role") == "assistant"]
                self.assertEqual(
                    [message["content"] for message in assistant_messages],
                    ["I was checking the project and found no blocking issue."],
                )
                retry = app.chat["last_route"]["completion_retry"]
                self.assertTrue(retry["attempted"])
                self.assertTrue(retry["recovered"])
                transcript = app.render_transcript_text()
                self.assertIn("found no blocking issue", transcript)
                self.assertNotIn("unfinished", transcript)

    async def test_coordinator_recovers_once_with_the_next_ranked_executor_after_blank_retries(self) -> None:
        class RecoveryProvider(FakeProvider):
            def chat_stream(self, model: str, messages: list[dict[str, str]], **kwargs: Any) -> Any:
                self.calls.append({"model": model, "messages": messages, "kwargs": kwargs})
                self.last_stats = {"done_reason": "stop"}
                if model == "qwen3.5:9b":
                    yield "Recovered answer from the alternate executor."
                if False:
                    yield ""

        route = {
            "mode": "orchestrator",
            "policy": "adaptive",
            "task_kind": "code inspection",
            "executor": "devstral-small-2:latest",
            "planner": "",
            "reviewer": "",
            "strategy": "single",
            "execution_scope": "conversation",
            "action_contract": {},
            "candidates": [
                {"model": "devstral-small-2:latest", "score": 0.9},
                {"model": "qwen3.5:9b", "score": 0.8},
            ],
            "signals": {},
            "evidence": [],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            CORE.CONFIG_PATH = root / "config.json"
            CORE.HISTORY_PATH = root / "history"
            CORE.CHAT_DIR = root / "chats"
            CORE.INDEX_DB_PATH = root / "index.sqlite3"
            CORE.CHECKPOINT_DIR = root / "checkpoints"
            CORE.APP_DATA_DIR = root
            config = coordinator_config(semantic=False)
            config.update({"agent": True, "auto_compact": False})
            provider = RecoveryProvider(response="")
            app = ui.build_textual_app(CORE, CORE.DairackTui, provider, "test", config, root)

            with patch.object(CORE, "select_orchestrator_route", return_value=deepcopy(route)):
                async with app.run_test(size=(80, 26)) as pilot:
                    app.query_one("#composer", ui.Composer).load_text("Inspect the implementation")
                    await pilot.press("enter")
                    for _ in range(240):
                        await pilot.pause(0.025)
                        if not app.busy and len(provider.calls) == 3:
                            break

                    self.assertFalse(app.busy)
                    self.assertEqual(
                        [call["model"] for call in provider.calls],
                        ["devstral-small-2:latest", "devstral-small-2:latest", "qwen3.5:9b"],
                    )
                    self.assertIn("Recovered answer", app.render_transcript_text())
                    self.assertNotIn("No response was returned", app.render_transcript_text())
                    recovery = app.chat["last_route"]["executor_recoveries"]
                    self.assertEqual(recovery[0]["from"], "devstral-small-2:latest")
                    self.assertEqual(recovery[0]["to"], "qwen3.5:9b")

    async def test_repeated_malformed_tool_output_recovers_on_an_alternate_executor(self) -> None:
        class ProtocolRecoveryProvider(FakeProvider):
            def chat_stream(self, model: str, messages: list[dict[str, str]], **kwargs: Any) -> Any:
                self.calls.append({"model": model, "messages": messages, "kwargs": kwargs})
                self.last_stats = {"done_reason": "stop"}
                if model == "devstral-small-2:latest":
                    raise CORE.OllamaError(
                        'Ollama returned HTTP 500: {"error":"XML syntax error on line 4: unexpected EOF"}'
                    )
                yield "Recovered after malformed tool output."

        route = {
            "mode": "orchestrator",
            "policy": "adaptive",
            "task_kind": "code inspection",
            "executor": "devstral-small-2:latest",
            "planner": "",
            "reviewer": "",
            "strategy": "single",
            "execution_scope": "agentic",
            "action_contract": {},
            "candidates": [
                {"model": "devstral-small-2:latest", "score": 0.9},
                {"model": "qwen3.5:9b", "score": 0.8},
            ],
            "signals": {},
            "evidence": [],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            CORE.CONFIG_PATH = root / "config.json"
            CORE.HISTORY_PATH = root / "history"
            CORE.CHAT_DIR = root / "chats"
            CORE.INDEX_DB_PATH = root / "index.sqlite3"
            CORE.CHECKPOINT_DIR = root / "checkpoints"
            CORE.APP_DATA_DIR = root
            config = coordinator_config(semantic=False)
            config.update({"agent": True, "auto_compact": False})
            provider = ProtocolRecoveryProvider(response="")
            app = ui.build_textual_app(CORE, CORE.DairackTui, provider, "test", config, root)

            with patch.object(CORE, "select_orchestrator_route", return_value=deepcopy(route)):
                async with app.run_test(size=(80, 26)) as pilot:
                    app.query_one("#composer", ui.Composer).load_text("Inspect the implementation")
                    await pilot.press("enter")
                    for _ in range(240):
                        await pilot.pause(0.025)
                        if not app.busy and len(provider.calls) == 3:
                            break

                    self.assertFalse(app.busy)
                    self.assertEqual(
                        [call["model"] for call in provider.calls],
                        ["devstral-small-2:latest", "devstral-small-2:latest", "qwen3.5:9b"],
                    )
                    transcript = app.render_transcript_text()
                    self.assertIn("Recovered after malformed tool output", transcript)
                    self.assertNotIn("XML syntax error", transcript)

    async def test_thinking_stream_is_shown_dim_when_think_is_enabled(self) -> None:
        class ThinkingProvider(FakeProvider):
            def __init__(self) -> None:
                super().__init__(response="")

            def chat_stream(self, model: str, messages: list[dict[str, str]], **kwargs: Any) -> Any:
                self.calls.append({"model": model, "messages": messages, "kwargs": kwargs})
                self.last_stats = {"done_reason": "stop"}
                sink = kwargs.get("thinking_sink")
                if callable(sink):
                    sink("Consider the edge cases first.")
                yield "The answer is settled."

        for think, expected in ((True, True), (False, False)):
            with self.subTest(think=think), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / ".git").mkdir()
                CORE.CONFIG_PATH = root / "config.json"
                CORE.HISTORY_PATH = root / "history"
                CORE.CHAT_DIR = root / "chats"
                CORE.INDEX_DB_PATH = root / "index.sqlite3"
                CORE.CHECKPOINT_DIR = root / "checkpoints"
                CORE.APP_DATA_DIR = root
                config = coordinator_config(semantic=False)
                config.update(
                    {
                        "model_mode": "direct",
                        "model": "qwen3.5:9b",
                        "agent": True,
                        "auto_compact": False,
                        "think": think,
                    }
                )
                provider = ThinkingProvider()
                app = ui.build_textual_app(CORE, CORE.DairackTui, provider, "test", config, root)

                async with app.run_test(size=(80, 26)) as pilot:
                    app.query_one("#composer", ui.Composer).load_text("Weigh the options")
                    await pilot.press("enter")
                    for _ in range(160):
                        await pilot.pause(0.025)
                        if not app.busy and provider.calls:
                            break

                    transcript = app.render_transcript_text()
                    self.assertIn("The answer is settled.", transcript)
                    self.assertEqual("Consider the edge cases first." in transcript, expected)
                    sink = provider.calls[0]["kwargs"].get("thinking_sink")
                    self.assertEqual(callable(sink), expected)
                    # Thinking is transcript-only; provider history must never carry it.
                    self.assertFalse(
                        any("Consider the edge cases" in str(m.get("content") or "") for m in app.messages)
                    )

    async def test_long_action_results_collapse_but_stay_in_transcript_text(self) -> None:
        class ReadThenAnswerProvider(FakeProvider):
            def __init__(self) -> None:
                super().__init__(response="")
                self.round = 0

            def chat_stream(self, model: str, messages: list[dict[str, str]], **kwargs: Any) -> Any:
                self.calls.append({"model": model, "messages": messages, "kwargs": kwargs})
                self.last_stats = {"done_reason": "stop"}
                self.round += 1
                sink = kwargs.get("tool_call_sink")
                if self.round == 1 and callable(sink):
                    sink({"function": {"name": "read_file", "arguments": {"path": "big.py"}}})
                    return
                yield "Reviewed the module."

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".git").mkdir()
            body = "\n".join(f"VALUE_{index} = {index}" for index in range(1, 41))
            (root / "big.py").write_text(body + "\n", encoding="ascii")
            CORE.CONFIG_PATH = root / "config.json"
            CORE.HISTORY_PATH = root / "history"
            CORE.CHAT_DIR = root / "chats"
            CORE.INDEX_DB_PATH = root / "index.sqlite3"
            CORE.CHECKPOINT_DIR = root / "checkpoints"
            CORE.APP_DATA_DIR = root
            config = coordinator_config(semantic=False)
            config.update(
                {
                    "model_mode": "direct",
                    "model": "qwen3.5:9b",
                    "agent": True,
                    "auto_compact": False,
                    "permission_mode": "read-auto",
                }
            )
            provider = ReadThenAnswerProvider()
            app = ui.build_textual_app(CORE, CORE.DairackTui, provider, "test", config, root)

            async with app.run_test(size=(80, 26)) as pilot:
                app.query_one("#composer", ui.Composer).load_text("Read big.py")
                await pilot.press("enter")
                for _ in range(200):
                    await pilot.pause(0.025)
                    if not app.busy and provider.round >= 2:
                        break

                collapsibles = list(app.query(ui.Collapsible))
                self.assertTrue(collapsibles)
                self.assertTrue(all(widget.collapsed for widget in collapsibles))
                # Full result fidelity is preserved for copy/export and assertions,
                # including the windowed read's continuation hint deep in the fold.
                transcript = app.render_transcript_text()
                self.assertIn("VALUE_25 = 25", transcript)
                self.assertIn("continue with start_line=", transcript)

    async def test_at_token_suggests_project_paths_in_the_composer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".git").mkdir()
            (root / "auth_notes.md").write_text("tokens\n", encoding="ascii")
            (root / "src").mkdir()
            (root / "src" / "colors.py").write_text("PALETTE = []\n", encoding="ascii")
            (root / ".venv").mkdir()
            (root / ".venv" / "hidden.py").write_text("HIDDEN = True\n", encoding="ascii")
            CORE.CONFIG_PATH = root / "config.json"
            CORE.HISTORY_PATH = root / "history"
            CORE.CHAT_DIR = root / "chats"
            CORE.INDEX_DB_PATH = root / "index.sqlite3"
            CORE.CHECKPOINT_DIR = root / "checkpoints"
            CORE.APP_DATA_DIR = root
            config = coordinator_config(semantic=False)
            config.update({"model_mode": "direct", "model": "qwen3.5:9b", "agent": True, "auto_compact": False})
            app = ui.build_textual_app(CORE, CORE.DairackTui, FakeProvider(()), "test", config, root)

            async with app.run_test(size=(80, 26)) as pilot:
                composer = app.query_one("#composer", ui.Composer)
                composer.load_text("read @auth")
                await pilot.pause(0.05)
                self.assertEqual(composer.suggestion, "_notes.md")

                composer.load_text("open @src/col")
                await pilot.pause(0.05)
                self.assertEqual(composer.suggestion, "ors.py")
                candidates = app._project_path_candidates()
                self.assertIn("src/colors.py", candidates)
                self.assertFalse(any("\\" in path for path in candidates))
                self.assertNotIn(".venv/hidden.py", candidates)

                composer.load_text("/mo")
                await pilot.pause(0.05)
                self.assertTrue(composer.suggestion.startswith("de"))

    async def test_prompt_typed_while_busy_queues_and_sends_when_the_turn_ends(self) -> None:
        release = threading.Event()

        class GatedProvider(FakeProvider):
            def __init__(self) -> None:
                super().__init__(response="")
                self.round = 0

            def chat_stream(self, model: str, messages: list[dict[str, str]], **kwargs: Any) -> Any:
                self.calls.append({"model": model, "messages": messages, "kwargs": kwargs})
                self.last_stats = {"done_reason": "stop"}
                self.round += 1
                if self.round == 1:
                    release.wait(timeout=5)
                    yield "First answer."
                    return
                yield "Second answer."

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".git").mkdir()
            CORE.CONFIG_PATH = root / "config.json"
            CORE.HISTORY_PATH = root / "history"
            CORE.CHAT_DIR = root / "chats"
            CORE.INDEX_DB_PATH = root / "index.sqlite3"
            CORE.CHECKPOINT_DIR = root / "checkpoints"
            CORE.APP_DATA_DIR = root
            config = coordinator_config(semantic=False)
            config.update({"model_mode": "direct", "model": "qwen3.5:9b", "agent": True, "auto_compact": False})
            provider = GatedProvider()
            app = ui.build_textual_app(CORE, CORE.DairackTui, provider, "test", config, root)

            async with app.run_test(size=(80, 26)) as pilot:
                app.query_one("#composer", ui.Composer).load_text("first question")
                await pilot.press("enter")
                for _ in range(120):
                    await pilot.pause(0.025)
                    if app.busy and provider.round == 1:
                        break
                self.assertTrue(app.busy)

                app.query_one("#composer", ui.Composer).load_text("second question")
                await pilot.press("enter")
                self.assertEqual(app.query_one("#composer", ui.Composer).text, "")

                release.set()
                for _ in range(200):
                    await pilot.pause(0.025)
                    if not app.busy and provider.round == 2:
                        break

                self.assertFalse(app.busy)
                self.assertEqual(provider.round, 2)
                second_request = provider.calls[1]["messages"]
                self.assertTrue(any("second question" in str(m.get("content") or "") for m in second_request))
                transcript = app.render_transcript_text()
                self.assertIn("First answer.", transcript)
                self.assertIn("Second answer.", transcript)

    async def test_interrupt_returns_queued_input_to_the_composer(self) -> None:
        release = threading.Event()

        class SlowProvider(FakeProvider):
            def __init__(self) -> None:
                super().__init__(response="")
                self.round = 0

            def chat_stream(self, model: str, messages: list[dict[str, str]], **kwargs: Any) -> Any:
                self.calls.append({"model": model, "messages": messages, "kwargs": kwargs})
                self.last_stats = {"done_reason": "stop"}
                self.round += 1
                yield "Working"
                release.wait(timeout=5)
                yield " on it."

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".git").mkdir()
            CORE.CONFIG_PATH = root / "config.json"
            CORE.HISTORY_PATH = root / "history"
            CORE.CHAT_DIR = root / "chats"
            CORE.INDEX_DB_PATH = root / "index.sqlite3"
            CORE.CHECKPOINT_DIR = root / "checkpoints"
            CORE.APP_DATA_DIR = root
            config = coordinator_config(semantic=False)
            config.update({"model_mode": "direct", "model": "qwen3.5:9b", "agent": True, "auto_compact": False})
            provider = SlowProvider()
            app = ui.build_textual_app(CORE, CORE.DairackTui, provider, "test", config, root)

            async with app.run_test(size=(80, 26)) as pilot:
                app.query_one("#composer", ui.Composer).load_text("first question")
                await pilot.press("enter")
                for _ in range(120):
                    await pilot.pause(0.025)
                    if app.busy and provider.round == 1:
                        break
                self.assertTrue(app.busy)

                app.query_one("#composer", ui.Composer).load_text("second question")
                await pilot.press("enter")
                await pilot.press("escape")
                release.set()
                for _ in range(200):
                    await pilot.pause(0.025)
                    if not app.busy and "second question" in app.query_one("#composer", ui.Composer).text:
                        break

                self.assertFalse(app.busy)
                # The interrupted turn did not consume the queued prompt; it returned to the composer.
                self.assertEqual(provider.round, 1)
                self.assertIn("second question", app.query_one("#composer", ui.Composer).text)

    async def test_pending_approval_holds_then_denial_releases_the_prompt_queue(self) -> None:
        release = threading.Event()

        class ApprovalProvider(FakeProvider):
            def __init__(self) -> None:
                super().__init__(response="")
                self.round = 0

            def chat_stream(self, model: str, messages: list[dict[str, str]], **kwargs: Any) -> Any:
                self.calls.append({"model": model, "messages": messages, "kwargs": kwargs})
                self.last_stats = {"done_reason": "stop"}
                self.round += 1
                if self.round == 1:
                    release.wait(timeout=5)
                    sink = kwargs.get("tool_call_sink")
                    if callable(sink):
                        sink({"function": {"name": "list_dir", "arguments": {"path": "/"}}})
                    return
                yield "Second answer."

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".git").mkdir()
            CORE.CONFIG_PATH = root / "config.json"
            CORE.HISTORY_PATH = root / "history"
            CORE.CHAT_DIR = root / "chats"
            CORE.INDEX_DB_PATH = root / "index.sqlite3"
            CORE.CHECKPOINT_DIR = root / "checkpoints"
            CORE.APP_DATA_DIR = root
            config = coordinator_config(semantic=False)
            config.update({"model_mode": "direct", "model": "qwen3.5:9b", "agent": True, "auto_compact": False})
            provider = ApprovalProvider()
            app = ui.build_textual_app(CORE, CORE.DairackTui, provider, "test", config, root)

            async with app.run_test(size=(80, 26)) as pilot:
                app.query_one("#composer", ui.Composer).load_text("inspect root")
                await pilot.press("enter")
                for _ in range(120):
                    await pilot.pause(0.025)
                    if app.busy and provider.round == 1:
                        break

                app.query_one("#composer", ui.Composer).load_text("second question")
                await pilot.press("enter")
                release.set()
                for _ in range(160):
                    await pilot.pause(0.025)
                    if isinstance(app.screen, ui.ApprovalScreen):
                        break

                self.assertIsInstance(app.screen, ui.ApprovalScreen)
                self.assertEqual(provider.round, 1)
                self.assertEqual(app._queued_prompts, ["second question"])
                await pilot.pause(0.1)
                self.assertEqual(provider.round, 1)

                await pilot.press("escape")
                for _ in range(160):
                    await pilot.pause(0.025)
                    if not app.busy and provider.round == 2:
                        break

                self.assertEqual(provider.round, 2)
                self.assertEqual(app._queued_prompts, [])
                self.assertTrue(
                    any(
                        "second question" in str(message.get("content") or "")
                        for message in provider.calls[1]["messages"]
                    )
                )

    async def test_approved_action_finishes_its_turn_before_releasing_the_prompt_queue(self) -> None:
        release = threading.Event()

        class ApprovalProvider(FakeProvider):
            def __init__(self) -> None:
                super().__init__(response="")
                self.round = 0

            def chat_stream(self, model: str, messages: list[dict[str, str]], **kwargs: Any) -> Any:
                self.calls.append({"model": model, "messages": messages, "kwargs": kwargs})
                self.last_stats = {"done_reason": "stop"}
                self.round += 1
                if self.round == 1:
                    release.wait(timeout=5)
                    sink = kwargs.get("tool_call_sink")
                    if callable(sink):
                        sink({"function": {"name": "list_dir", "arguments": {"path": "."}}})
                    return
                yield "Inspection complete." if self.round == 2 else "Second answer."

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".git").mkdir()
            CORE.CONFIG_PATH = root / "config.json"
            CORE.HISTORY_PATH = root / "history"
            CORE.CHAT_DIR = root / "chats"
            CORE.INDEX_DB_PATH = root / "index.sqlite3"
            CORE.CHECKPOINT_DIR = root / "checkpoints"
            CORE.APP_DATA_DIR = root
            config = coordinator_config(semantic=False)
            config.update({"model_mode": "direct", "model": "qwen3.5:9b", "agent": True, "auto_compact": False})
            provider = ApprovalProvider()
            app = ui.build_textual_app(CORE, CORE.DairackTui, provider, "test", config, root)

            async with app.run_test(size=(80, 26)) as pilot:
                app.query_one("#composer", ui.Composer).load_text("inspect project")
                await pilot.press("enter")
                for _ in range(120):
                    await pilot.pause(0.025)
                    if app.busy and provider.round == 1:
                        break
                app.query_one("#composer", ui.Composer).load_text("second question")
                await pilot.press("enter")
                release.set()
                for _ in range(160):
                    await pilot.pause(0.025)
                    if isinstance(app.screen, ui.ApprovalScreen):
                        break

                self.assertEqual(provider.round, 1)
                await pilot.press("a")
                for _ in range(240):
                    await pilot.pause(0.025)
                    if not app.busy and provider.round == 3:
                        break

                self.assertEqual(provider.round, 3)
                self.assertFalse(
                    any(
                        "second question" in str(message.get("content") or "")
                        for message in provider.calls[1]["messages"]
                    )
                )
                self.assertTrue(
                    any(
                        "second question" in str(message.get("content") or "")
                        for message in provider.calls[2]["messages"]
                    )
                )

    async def test_read_auto_batches_multiple_reads_in_one_response(self) -> None:
        class BatchThenAnswerProvider(FakeProvider):
            def __init__(self) -> None:
                super().__init__(response="")
                self.round = 0

            def chat_stream(self, model: str, messages: list[dict[str, str]], **kwargs: Any) -> Any:
                self.calls.append({"model": model, "messages": messages, "kwargs": kwargs})
                self.last_stats = {"done_reason": "stop"}
                self.round += 1
                sink = kwargs.get("tool_call_sink")
                if self.round == 1 and callable(sink):
                    sink({"function": {"name": "read_file", "arguments": {"path": "a.py"}}})
                    sink({"function": {"name": "read_file", "arguments": {"path": "b.py"}}})
                    return
                yield "Both files define stubs."

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".git").mkdir()
            (root / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
            (root / "b.py").write_text("def b():\n    return 2\n", encoding="utf-8")
            CORE.CONFIG_PATH = root / "config.json"
            CORE.HISTORY_PATH = root / "history"
            CORE.CHAT_DIR = root / "chats"
            CORE.INDEX_DB_PATH = root / "index.sqlite3"
            CORE.CHECKPOINT_DIR = root / "checkpoints"
            CORE.APP_DATA_DIR = root
            config = coordinator_config(semantic=False)
            config.update(
                {
                    "model_mode": "direct",
                    "model": "qwen3.5:9b",
                    "agent": True,
                    "auto_compact": False,
                    "permission_mode": "read-auto",
                }
            )
            provider = BatchThenAnswerProvider()
            app = ui.build_textual_app(CORE, CORE.DairackTui, provider, "test", config, root)

            async with app.run_test(size=(80, 26)) as pilot:
                app.query_one("#composer", ui.Composer).load_text("Read a.py and b.py")
                await pilot.press("enter")
                for _ in range(200):
                    await pilot.pause(0.025)
                    if not app.busy and provider.round == 2:
                        break

                self.assertFalse(app.busy)
                # Both reads ran from a single response, then one synthesis generation followed.
                self.assertEqual(provider.round, 2)
                tool_results = [
                    m for m in app.messages if str(m.get("content") or "").startswith("Structured tool result:")
                ]
                self.assertEqual(len(tool_results), 2)
                self.assertTrue(any("def a()" in str(m.get("content") or "") for m in tool_results))
                self.assertTrue(any("def b()" in str(m.get("content") or "") for m in tool_results))
                self.assertIn("Both files define stubs.", app.render_transcript_text())

    async def test_transient_stream_error_is_retried_once_without_a_visible_failure(self) -> None:
        class FlakyProvider(FakeProvider):
            def __init__(self) -> None:
                super().__init__(response="")
                self.attempts = 0

            def chat_stream(self, model: str, messages: list[dict[str, str]], **kwargs: Any) -> Any:
                self.calls.append({"model": model, "messages": messages, "kwargs": kwargs})
                self.attempts += 1
                self.last_stats = {"done_reason": "stop"}
                if self.attempts == 1:
                    yield "partial half-ans"
                    raise CORE.OllamaError("Ollama stream stalled or disconnected")
                yield "The full recovered answer."

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            CORE.CONFIG_PATH = root / "config.json"
            CORE.HISTORY_PATH = root / "history"
            CORE.CHAT_DIR = root / "chats"
            CORE.INDEX_DB_PATH = root / "index.sqlite3"
            CORE.CHECKPOINT_DIR = root / "checkpoints"
            CORE.APP_DATA_DIR = root
            config = coordinator_config()
            config.update(
                {
                    "model_mode": "direct",
                    "model": "qwen3.5:9b",
                    "auto_compact": False,
                }
            )
            provider = FlakyProvider()
            app = ui.build_textual_app(CORE, CORE.DairackTui, provider, "test", config, root)

            async with app.run_test(size=(80, 26)) as pilot:
                app.query_one("#composer", ui.Composer).load_text("Summarize the project")
                await pilot.press("enter")
                for _ in range(200):
                    await pilot.pause(0.025)
                    if not app.busy and provider.attempts == 2:
                        break

                self.assertFalse(app.busy)
                self.assertEqual(provider.attempts, 2)
                assistant_messages = [message for message in app.messages if message.get("role") == "assistant"]
                self.assertEqual(
                    [message["content"] for message in assistant_messages],
                    ["The full recovered answer."],
                )
                transcript = app.render_transcript_text()
                self.assertIn("The full recovered answer.", transcript)
                self.assertNotIn("half-ans", transcript)
                self.assertNotIn("stalled", transcript)

    async def test_bare_tool_request_approval_executes_and_resumes(self) -> None:
        class SequencedProvider(FakeProvider):
            def __init__(self, responses: list[str]) -> None:
                super().__init__(response="")
                self.responses = responses

            def chat_stream(self, model: str, messages: list[dict[str, str]], **kwargs: Any) -> Any:
                self.calls.append({"model": model, "messages": messages, "kwargs": kwargs})
                yield self.responses.pop(0)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text("def main():\n    return 0\n", encoding="utf-8")
            CORE.CONFIG_PATH = root / "config.json"
            CORE.HISTORY_PATH = root / "history"
            CORE.CHAT_DIR = root / "chats"
            CORE.INDEX_DB_PATH = root / "index.sqlite3"
            CORE.CHECKPOINT_DIR = root / "checkpoints"
            CORE.APP_DATA_DIR = root
            config = coordinator_config()
            config["model_mode"] = "direct"
            config["model"] = "qwen3.5:9b"
            config["max_agent_steps"] = 1
            provider = SequencedProvider(["index_project", "I inspected the project and found its Python entry point."])
            app = ui.build_textual_app(CORE, CORE.DairackTui, provider, "test", config, root)

            async with app.run_test(size=(80, 26)) as pilot:
                app.query_one("#composer", ui.Composer).load_text("Inspect your own codebase and understand it")
                await pilot.press("enter")
                for _ in range(120):
                    await pilot.pause(0.025)
                    if isinstance(app.screen, ui.ApprovalScreen):
                        break

                self.assertIsInstance(app.screen, ui.ApprovalScreen)
                self.assertEqual(app.pending_tool["name"], "index_project")
                self.assertEqual(app.pending_tool["_protocol"], "compat")
                self.assertEqual(
                    app.blocks[-1]["text"],
                    "ACTION REQUEST\nPROJECT INDEX  /  LOCAL\nPATH  .",
                )
                self.assertNotIn("\nindex_project\n", app.render_transcript_text())

                self.assertTrue(app.screen.query_one("#deny", ui.Button).has_focus)
                await pilot.press("a")
                for _ in range(200):
                    await pilot.pause(0.025)
                    if not app.busy and len(provider.calls) == 2:
                        break

                self.assertFalse(app.busy)
                self.assertEqual(len(provider.calls), 2)
                self.assertIsNone(provider.calls[1]["kwargs"]["tools"])
                self.assertIn("action budget is exhausted", provider.calls[1]["messages"][0]["content"])
                self.assertTrue(CORE.INDEX_DB_PATH.exists())
                self.assertEqual(app.chat["project_root"], str(root.resolve()))
                self.assertEqual(app.chat["last_route"]["tool_steps"], 1)
                self.assertEqual(app.messages[-1]["role"], "assistant")
                self.assertIn("I inspected the project", app.messages[-1]["content"])
                self.assertIn("I inspected the project", app.render_transcript_text())
                self.assertTrue(
                    any(
                        message.get("role") == "user"
                        and str(message.get("content") or "").startswith("Structured tool result:")
                        for message in app.messages
                    )
                )

    async def test_agent_search_uses_the_project_it_just_indexed(self) -> None:
        class SequencedProvider(FakeProvider):
            def __init__(self) -> None:
                super().__init__(response="")
                self.responses = [
                    'index_project{"path":"project"}',
                    'search_project{"query":"SCOPE_SENTINEL"}',
                    'read_file{"path":"implementation.py"}',
                    "The indexed project contains the expected implementation marker.",
                ]

            def chat_stream(self, model: str, messages: list[dict[str, str]], **kwargs: Any) -> Any:
                self.calls.append({"model": model, "messages": messages, "kwargs": kwargs})
                yield self.responses.pop(0)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            (project / "implementation.py").write_text("SCOPE_SENTINEL = True\n", encoding="utf-8")
            CORE.CONFIG_PATH = root / "config.json"
            CORE.HISTORY_PATH = root / "history"
            CORE.CHAT_DIR = root / "chats"
            CORE.INDEX_DB_PATH = root / "index.sqlite3"
            CORE.CHECKPOINT_DIR = root / "checkpoints"
            CORE.APP_DATA_DIR = root
            config = coordinator_config()
            config.update(
                {
                    "model_mode": "direct",
                    "model": "qwen3.5:9b",
                    "permission_mode": "read-auto",
                    "max_agent_steps": 4,
                }
            )
            provider = SequencedProvider()
            app = ui.build_textual_app(CORE, CORE.DairackTui, provider, "test", config, root)

            async with app.run_test(size=(80, 26)) as pilot:
                app.query_one("#composer", ui.Composer).load_text("Inspect the child project")
                await pilot.press("enter")
                for _ in range(300):
                    await pilot.pause(0.025)
                    if isinstance(app.screen, ui.ApprovalScreen):
                        await pilot.press("a")
                    if not app.busy and len(provider.calls) == 4:
                        break

                self.assertFalse(app.busy)
                self.assertEqual(len(provider.calls), 4)
                self.assertEqual(app.chat["project_root"], str(project.resolve()))
                self.assertEqual(app.chat["last_route"]["tool_steps"], 3)
                results = "\n".join(str(message.get("content") or "") for message in app.messages)
                self.assertIn("implementation.py", results)
                self.assertNotIn("No project index found", results)

    async def test_action_limit_blocks_more_tools_and_retries_final_synthesis(self) -> None:
        class SequencedProvider(FakeProvider):
            def __init__(self) -> None:
                super().__init__(response="")
                self.responses = [
                    'list_dir{"path":"."}',
                    'list_dir{"path":"."}',
                    "I completed the available inspection and retained its result.",
                ]

            def chat_stream(self, model: str, messages: list[dict[str, str]], **kwargs: Any) -> Any:
                self.calls.append({"model": model, "messages": messages, "kwargs": kwargs})
                yield self.responses.pop(0)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "only-once.txt").write_text("evidence\n", encoding="utf-8")
            CORE.CONFIG_PATH = root / "config.json"
            CORE.HISTORY_PATH = root / "history"
            CORE.CHAT_DIR = root / "chats"
            CORE.INDEX_DB_PATH = root / "index.sqlite3"
            CORE.CHECKPOINT_DIR = root / "checkpoints"
            CORE.APP_DATA_DIR = root
            config = coordinator_config()
            config.update(
                {
                    "model_mode": "direct",
                    "model": "qwen3.5:9b",
                    "permission_mode": "read-auto",
                    "max_agent_steps": 1,
                }
            )
            provider = SequencedProvider()
            app = ui.build_textual_app(CORE, CORE.DairackTui, provider, "test", config, root)

            async with app.run_test(size=(80, 26)) as pilot:
                app.query_one("#composer", ui.Composer).load_text("Inspect once and summarize")
                await pilot.press("enter")
                for _ in range(300):
                    await pilot.pause(0.025)
                    if not app.busy and len(provider.calls) == 3:
                        break

                self.assertFalse(app.busy)
                self.assertEqual(len(provider.calls), 3)
                self.assertEqual(app.chat["last_route"]["tool_steps"], 1)
                self.assertIsNone(provider.calls[1]["kwargs"]["tools"])
                self.assertIsNone(provider.calls[2]["kwargs"]["tools"])
                executed = [
                    message
                    for message in app.messages
                    if str(message.get("content") or "").startswith("Structured tool result:")
                ]
                self.assertEqual(len(executed), 1)
                self.assertIn("I completed the available inspection", app.messages[-1]["content"])

    async def test_native_tool_request_opens_real_approval_modal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            CORE.CONFIG_PATH = root / "config.json"
            CORE.HISTORY_PATH = root / "history"
            CORE.CHAT_DIR = root / "chats"
            CORE.INDEX_DB_PATH = root / "index.sqlite3"
            CORE.CHECKPOINT_DIR = root / "checkpoints"
            CORE.APP_DATA_DIR = root
            config = coordinator_config()
            config["model_mode"] = "direct"
            config["model"] = "qwen3.5:9b"
            native_call = {
                "type": "function",
                "function": {
                    "name": "list_dir",
                    "arguments": {"path": "/", "reason": "list root folders"},
                },
            }
            provider = FakeProvider(response="", native_call=native_call)
            app = ui.build_textual_app(CORE, CORE.DairackTui, provider, "test", config, root)

            async with app.run_test(size=(80, 26)) as pilot:
                app.query_one("#composer", ui.Composer).load_text("What folders are in root?")
                await pilot.press("enter")
                for _ in range(120):
                    await pilot.pause(0.025)
                    if isinstance(app.screen, ui.ApprovalScreen):
                        break

                self.assertIsInstance(app.screen, ui.ApprovalScreen)
                self.assertEqual(app.pending_tool["name"], "list_dir")
                self.assertEqual(app.pending_tool["_protocol"], "native")
                self.assertEqual(app.messages[-1]["tool_calls"], [native_call])
                request_messages = provider.calls[0]["messages"]
                self.assertEqual(request_messages[0]["role"], "system")
                self.assertNotIn("system", [message["role"] for message in request_messages[1:]])
                self.assertIn(CORE.NATIVE_TOOL_DIRECTIVE, request_messages[0]["content"])
                self.assertIn("Dairack implementation path:", request_messages[0]["content"])
                self.assertIn("ACTION REQUEST", app.blocks[-1]["text"])
                await pilot.press("escape")
                await pilot.pause(0.05)
                self.assertEqual(app.messages[-1]["role"], "tool")
                self.assertIn("not executed", app.messages[-1]["content"])

    async def test_deny_mode_blocks_model_actions_without_opening_approval(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            CORE.CONFIG_PATH = root / "config.json"
            CORE.HISTORY_PATH = root / "history"
            CORE.CHAT_DIR = root / "chats"
            CORE.INDEX_DB_PATH = root / "index.sqlite3"
            CORE.CHECKPOINT_DIR = root / "checkpoints"
            CORE.APP_DATA_DIR = root
            config = coordinator_config()
            config.update({"model_mode": "direct", "model": "qwen3.5:9b", "permission_mode": "deny"})
            provider = FakeProvider(response='shell{"cmd":"touch denied.txt"}')
            app = ui.build_textual_app(CORE, CORE.DairackTui, provider, "test", config, root)

            with patch.object(CORE, "execute_tool_call") as execute:
                async with app.run_test(size=(80, 26)) as pilot:
                    app.query_one("#composer", ui.Composer).load_text("Create a file")
                    await pilot.press("enter")
                    for _ in range(120):
                        await pilot.pause(0.025)
                        if not app.busy:
                            break

                    self.assertFalse(app.busy)
                    self.assertNotIsInstance(app.screen, ui.ApprovalScreen)
                    self.assertIn("permissions policy", app.messages[-1]["content"])
            execute.assert_not_called()
            self.assertFalse((root / "denied.txt").exists())

    async def test_read_auto_routes_out_of_scope_reads_to_approval(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            root.mkdir()
            outside = Path(directory) / "secret.txt"
            outside.write_text("secret\n", encoding="ascii")
            CORE.CONFIG_PATH = Path(directory) / "config.json"
            CORE.HISTORY_PATH = Path(directory) / "history"
            CORE.CHAT_DIR = Path(directory) / "chats"
            CORE.INDEX_DB_PATH = Path(directory) / "index.sqlite3"
            CORE.CHECKPOINT_DIR = Path(directory) / "checkpoints"
            CORE.APP_DATA_DIR = Path(directory)
            config = coordinator_config()
            config.update({"model_mode": "direct", "model": "qwen3.5:9b", "permission_mode": "read-auto"})
            provider = FakeProvider(response=f'read_file{{"path":{json.dumps(str(outside))}}}')
            app = ui.build_textual_app(CORE, CORE.DairackTui, provider, "test", config, root)

            async with app.run_test(size=(80, 26)) as pilot:
                app.query_one("#composer", ui.Composer).load_text("Read the other file")
                await pilot.press("enter")
                for _ in range(120):
                    await pilot.pause(0.025)
                    if isinstance(app.screen, ui.ApprovalScreen):
                        break

                self.assertIsInstance(app.screen, ui.ApprovalScreen)
                self.assertEqual(app.pending_tool["name"], "read_file")
                await pilot.press("escape")

    async def test_attribute_tool_request_opens_real_approval_modal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            CORE.CONFIG_PATH = root / "config.json"
            CORE.HISTORY_PATH = root / "history"
            CORE.CHAT_DIR = root / "chats"
            CORE.INDEX_DB_PATH = root / "index.sqlite3"
            CORE.CHECKPOINT_DIR = root / "checkpoints"
            CORE.APP_DATA_DIR = root
            config = coordinator_config()
            config["model_mode"] = "direct"
            config["model"] = "qwen3.5:9b"
            provider = FakeProvider(
                response=(
                    '<tool name="shell" cmd="/bin/ls -la / | head -30" reason="list root directory contents"></tool>'
                )
            )
            app = ui.build_textual_app(CORE, CORE.DairackTui, provider, "test", config, root)

            async with app.run_test(size=(80, 26)) as pilot:
                composer = app.query_one("#composer", ui.Composer)
                composer.load_text("What folders are in root?")
                await pilot.press("enter")
                for _ in range(120):
                    await pilot.pause(0.025)
                    if isinstance(app.screen, ui.ApprovalScreen):
                        break

                self.assertIsInstance(app.screen, ui.ApprovalScreen)
                self.assertEqual(app.pending_tool["cmd"], "/bin/ls -la / | head -30")
                self.assertIn("ACTION REQUEST", app.blocks[-1]["text"])
                self.assertFalse(any("Preparing action request" in block["text"] for block in app.blocks))
                self.assertIn(
                    "/bin/ls -la / | head -30",
                    str(app.screen.query_one(".approval-code", ui.Static).render()),
                )
                await pilot.press("escape")
                await pilot.pause(0.05)
                self.assertIsNone(app.pending_tool)
                self.assertTrue(any("COMMAND  DENIED" in block["text"] for block in app.blocks))

    async def test_direct_actions_and_permission_choices_share_the_typed_ux(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "app.py"
            source.write_text("print('ready')\n", encoding="utf-8")
            CORE.CONFIG_PATH = root / "config.json"
            CORE.HISTORY_PATH = root / "history"
            CORE.CHAT_DIR = root / "chats"
            CORE.INDEX_DB_PATH = root / "index.sqlite3"
            CORE.CHECKPOINT_DIR = root / "checkpoints"
            CORE.APP_DATA_DIR = root
            config = coordinator_config()
            app = ui.build_textual_app(CORE, CORE.DairackTui, FakeProvider(()), "test", config, root)

            with patch.object(
                CORE,
                "internet_search",
                return_value=(0, "1. Ollama docs\n   https://ollama.com/docs"),
            ):
                async with app.run_test(size=(80, 26)) as pilot:
                    composer = app.query_one("#composer", ui.Composer)
                    composer.load_text("/web Ollama tool calling")
                    await pilot.press("enter")
                    for _ in range(80):
                        await pilot.pause(0.025)
                        if not app.busy and any(block.get("role") == "action" for block in app.blocks):
                            break

                    action = next(block for block in reversed(app.blocks) if block.get("role") == "action")
                    self.assertIn("WEB SEARCH  COMPLETE", action["text"])
                    self.assertIn("QUERY  Ollama tool calling", action["text"])
                    self.assertIn("ACCESS  USER", action["text"])
                    self.assertIn("https://ollama.com/docs", action["text"])
                    self.assertIn("tool: web_search", app.messages[-1]["content"])
                    self.assertIn("https://ollama.com/docs", app.messages[-1]["content"])

                    web_call = {"name": "web_search", "query": "current release", "reason": "verify recency"}
                    app.pending_tool = web_call
                    app._show_approval_main(web_call)
                    await pilot.pause(0.05)
                    self.assertIsInstance(app.screen, ui.ApprovalScreen)
                    self.assertEqual(len(app.screen.query("#read-auto")), 0)
                    self.assertIn("WEB SEARCH", str(app.screen.query_one(".dialog-title", ui.Static).render()))
                    self.assertTrue(app.screen.query_one("#deny", ui.Button).has_focus)
                    self.assertIn("ENTER CHOOSE", str(app.screen.query_one(".dialog-keys", ui.Static).render()))
                    await pilot.press("escape")
                    await pilot.pause(0.05)

                    read_call = {"name": "read_file", "path": str(source), "reason": "inspect source"}
                    app.pending_tool = read_call
                    app._show_approval_main(read_call)
                    await pilot.pause(0.05)
                    self.assertIsInstance(app.screen, ui.ApprovalScreen)
                    self.assertEqual(len(app.screen.query("#read-auto")), 1)
                    self.assertTrue(app.screen.query_one("#approve", ui.Button).has_focus)
                    self.assertEqual(
                        [button.id for button in app.screen.query(ui.Button)],
                        ["deny", "approve", "read-auto"],
                    )
                    self.assertEqual(
                        str(app.screen.query_one("#read-auto", ui.Button).label),
                        "AUTO-ALLOW PROJECT READS",
                    )
                    policy_text = str(app.screen.query_one(".approval-policy", ui.Static).render())
                    self.assertIn("persists for future chats", policy_text)
                    await pilot.press("escape")
                    await pilot.pause(0.05)

                    grep_call = {
                        "name": "grep",
                        "query": "password|token",
                        "path": "src",
                        "reason": "inspect credential handling",
                    }
                    app.pending_tool = grep_call
                    app._show_approval_main(grep_call)
                    await pilot.pause(0.05)
                    approval = app.screen
                    self.assertIsInstance(approval, ui.ApprovalScreen)
                    preview = str(approval.query_one(".approval-code", ui.Static).render())
                    self.assertIn("ROOT  src", preview)
                    self.assertIn("QUERY  password|token", preview)
                    self.assertEqual(str(approval.query_one("#approve", ui.Button).label), "ALLOW SEARCH")
                    await pilot.press("escape")
                    await pilot.pause(0.05)

                    app.cancel_event.clear()
                    app.begin_tool_action(
                        {"name": "patch", "patch": "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-old\n+new\n"}
                    )
                    await pilot.press("escape")
                    await pilot.pause(0.05)
                    self.assertFalse(app.cancel_event.is_set())
                    self.assertIn("finishing atomically", app._notice)
                    app.finish_tool_action()
                    app.set_busy(False)

                    with patch.object(CORE, "execute_tool_call", side_effect=RuntimeError("executor fault")):
                        app.run_tool_call({"name": "list_dir", "path": "."}, approved_by="read-auto")
                    await pilot.pause(0.05)
                    failed = next(block for block in reversed(app.blocks) if block.get("role") == "action")
                    self.assertIn("DIRECTORY  FAILED", failed["text"])
                    self.assertIn("action failed: executor fault", failed["text"])
                    self.assertIn("exit_code: 1", app.messages[-1]["content"])
                    self.assertIsNone(app._active_tool_call)
                    app.set_busy(False)

    async def test_welcome_session_archive_and_image_attachment_are_native(self) -> None:
        async def wait_for_selector(app: Any, pilot: Any) -> ui.SelectorScreen:
            for _ in range(80):
                await pilot.pause(0.025)
                if isinstance(app.screen, ui.SelectorScreen):
                    return app.screen
            self.fail(f"selector did not open; current screen is {type(app.screen).__name__}")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "reference.png"
            image.write_bytes(b"not-empty")
            canonical_image = image.resolve()
            CORE.CONFIG_PATH = root / "config.json"
            CORE.HISTORY_PATH = root / "history"
            CORE.CHAT_DIR = root / "chats"
            CORE.INDEX_DB_PATH = root / "index.sqlite3"
            CORE.CHECKPOINT_DIR = root / "checkpoints"
            CORE.APP_DATA_DIR = root
            config = coordinator_config()
            chat = CORE.new_chat_state(root, config)
            chat["_transient"] = True
            app = ui.build_textual_app(
                CORE,
                CORE.DairackTui,
                FakeProvider(()),
                "test",
                config,
                root,
                chat=chat,
            )

            async with app.run_test(size=(72, 24)) as pilot:
                await pilot.pause(0.2)
                welcome = app.query_one("#empty-state", ui.Static)
                first_frame = repr(welcome.render())
                self.assertIn("L O C A L   I N T E L L I G E N C E", first_frame)
                compact_welcome = app._welcome_content(44).plain
                self.assertIn("D A I R A C K", compact_welcome)
                self.assertNotIn("A S U S A I", compact_welcome)
                self.assertLessEqual(welcome.region.right, 72)
                self.assertLessEqual(welcome.region.bottom, 24)
                with patch.object(app, "_context_values", wraps=app._context_values) as context_values:
                    await pilot.pause(0.25)
                context_values.assert_not_called()
                self.assertNotEqual(first_frame, repr(welcome.render()))
                await pilot.pause(0.5)
                self.assertIn("NEW SESSION", str(welcome.render()))
                self.assertNotIn("COORDINATOR / CALIBRATED", str(welcome.render()))
                app._welcome_started = time.monotonic() - ui.WELCOME_SETTLE_SECONDS - 0.1
                app.refresh_chrome(force=True)
                settled_frame = repr(welcome.render())
                await pilot.pause(0.2)
                self.assertEqual(settled_frame, repr(welcome.render()))
                self.assertNotIn("COORDINATOR / ADAPTIVE", settled_frame)

                composer = app.query_one("#composer", ui.Composer)
                composer.load_text("Hello")
                with patch.object(app, "start_generation", side_effect=app.invalidate) as generation:
                    await pilot.press("enter")
                    await pilot.pause(0.2)
                generation.assert_called_once_with()
                self.assertEqual(app.messages[-1], {"role": "user", "content": "Hello"})
                self.assertEqual(len(app.query(ui.TranscriptEntry)), 1)
                self.assertFalse(list(app.query("#empty-state")))
                self.assertFalse(any("No nodes match" in block["text"] for block in app.blocks))

                await pilot.press("f3")
                archive = await wait_for_selector(app, pilot)
                archive_options = archive.query_one("#selector-options", ui.OptionList)
                self.assertEqual(archive_options.get_option_at_index(0).id, "__new__")
                self.assertEqual(archive_options.get_option_at_index(1).id, "__startup__")
                self.assertTrue(archive_options.get_option_at_index(2).disabled)
                session_prompt = archive_options.get_option_at_index(3).prompt
                self.assertNotIn(str(app.chat["id"]), session_prompt.plain)
                await pilot.press("escape")

                await pilot.press("f4")
                picker = await wait_for_selector(app, pilot)
                picker_options = picker.query_one("#selector-options", ui.OptionList)
                self.assertEqual(picker_options.get_option_at_index(0).id, "__path__")
                self.assertEqual(picker_options.get_option_at_index(1).id, str(canonical_image))
                await pilot.press("down", "enter")
                await pilot.pause(0.1)
                self.assertEqual(app._pending_images, [canonical_image])
                self.assertIn("reference.png", str(app.query_one("#attachment-bar", ui.Static).render()))

            self.assertEqual(len(list(CORE.CHAT_DIR.glob("*.json"))), 1)

    async def test_surface_state_is_explicit_transient_and_coalesced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            CORE.CONFIG_PATH = root / "config.json"
            CORE.HISTORY_PATH = root / "history"
            CORE.CHAT_DIR = root / "chats"
            CORE.INDEX_DB_PATH = root / "index.sqlite3"
            CORE.CHECKPOINT_DIR = root / "checkpoints"
            CORE.APP_DATA_DIR = root
            config = coordinator_config()
            provider = FakeProvider(())
            app = ui.build_textual_app(CORE, CORE.DairackTui, provider, "test", config, root)

            async with app.run_test(size=(120, 36)) as pilot:
                app.handle_command("/help")
                await pilot.pause(0.05)
                self.assertEqual(app.blocks[-1].get("kind"), "reference")
                self.assertIn("/help all", app.blocks[-1]["text"])
                self.assertNotIn("/orchestrator", app.blocks[-1]["text"])

                app.handle_command("/libary")
                await pilot.pause(0.05)
                self.assertEqual(app.blocks[-1].get("severity"), "error")
                self.assertIn("Did you mean /library", app.blocks[-1]["text"])
                error_entry = app.query_one(f"#entry-{len(app.blocks) - 1}", ui.TranscriptEntry)
                self.assertEqual(error_entry.role_label, "ERROR")

                app._last_route = {"executor": "qwen3.5:9b"}
                self.assertEqual(
                    app._display_model(width=120),
                    "COORDINATOR / ADAPTIVE > QWEN3.5:9B",
                )
                self.assertEqual(app._display_model(width=44), "ADAPT > QWEN3.5:9B")
                composer_meta = app._composer_meta_content(120).plain
                self.assertIn("ENTER SEND", composer_meta)
                self.assertIn("CTRL+P COMMANDS", composer_meta)
                self.assertNotIn("COORD", composer_meta)
                self.assertFalse(app._activity_visible())
                self.assertEqual(app.query_one("#activity").styles.display, "none")

                composer = app.query_one("#composer", ui.Composer)
                composer.load_text("/co")
                command_hints = app._composer_meta_content(120).plain
                self.assertIn("/coordinator", command_hints)
                self.assertIn("/compact", command_hints)
                self.assertIn("/compute", command_hints)
                self.assertNotIn("/orchestrator", command_hints)
                composer.load_text("")

                provider.current_model = "qwen3.5:9b"
                app.set_busy(True, "executing / qwen3.5:9b")
                app.refresh_chrome(force=True)
                self.assertTrue(app._activity_visible())
                self.assertEqual(app.query_one("#activity").styles.display, "block")
                app.request_interrupt()
                activity = app._activity_content(120).plain
                self.assertIn("INTERRUPTING / QWEN3.5:9B", activity)
                self.assertIn("WAIT", activity)
                self.assertNotIn("ESC STOP", activity)
                self.assertNotIn("─", activity)
                app.set_busy(False)
                app.cancel_event.clear()

                provider.stream_phase = "responding"
                provider.current_model = "qwen3.5:9b"
                app.set_busy(True, "executing / qwen3.5:9b")
                app._stream_chars = 80
                streaming = app._activity_content(120).plain
                self.assertIn("OUTPUT / QWEN3.5:9B", streaming)
                self.assertNotIn("─", streaming)
                app.set_busy(False)
                provider.stream_phase = ""

                provider.last_stats = {"eval_count": 22, "tokens_per_second": 62.9}
                app.set_busy(True, "responding")
                app.set_busy(False)
                completion = app._activity_content(120).plain
                self.assertIn("LAST 22 tok", completion)
                self.assertIn("─", completion)
                app._last_turn_stats_until = time.monotonic() - 0.01
                self.assertNotIn("LAST 22 tok", app._activity_content(120).plain)

                composer = app.query_one("#composer", ui.Composer)
                app._completion_sweep_started = 0.0
                app._focus_from = 0.0
                app._focus_to = 0.82
                app._focus_transition_started = time.monotonic() - ui.FOCUS_SETTLE_SECONDS * 0.5
                app._composer_was_focused = True
                app._update_signal_surfaces(time.monotonic())
                transitioning_rail = composer.styles.border_left
                app._focus_transition_started = time.monotonic() - ui.FOCUS_SETTLE_SECONDS
                app._update_signal_surfaces(time.monotonic())
                self.assertNotEqual(transitioning_rail, composer.styles.border_left)

                app._welcome_started = time.monotonic() - ui.WELCOME_SETTLE_SECONDS - 0.1
                app._focus_transition_started = time.monotonic() - ui.FOCUS_SETTLE_SECONDS - 0.1
                app._completion_sweep_started = time.monotonic() - ui.COMPLETION_SWEEP_SECONDS - 0.1
                app._last_turn_stats = {}
                app._notice = ""
                app.refresh_chrome(force=True)
                await pilot.pause(0.15)
                self.assertIsNone(app._feedback_timer)

                app._reduced_motion = True
                now = time.monotonic()
                app._focus_from = 0.0
                app._focus_to = 0.82
                app._focus_transition_started = now
                app._phase_transition_started = now
                app._completion_sweep_started = now
                self.assertIsNone(app._next_feedback_delay(now))

                app.append_assistant_start()
                await pilot.pause(0.05)
                app._last_stream_render = time.monotonic()
                with patch.object(app, "_dispatch") as dispatch:
                    for index in range(10):
                        app.replace_last_assistant_text(f"chunk {index}")
                    dispatch.assert_not_called()
                    app._last_stream_render = time.monotonic() - ui.STREAM_RENDER_INTERVAL - 0.01
                    app.replace_last_assistant_text("rendered chunk")
                    dispatch.assert_called_once()

                composer.load_text("界" * 120)
                app._resize_composer()
                await pilot.pause(0.05)
                self.assertGreaterEqual(composer.region.height, 3)

                self.assertFalse(any(binding.key == "q" for binding in ui.SelectorScreen.BINDINGS))

    async def test_chrome_geometry_is_locked_across_terminal_sizes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            CORE.CONFIG_PATH = root / "config.json"
            CORE.HISTORY_PATH = root / "history"
            CORE.CHAT_DIR = root / "chats"
            CORE.INDEX_DB_PATH = root / "index.sqlite3"
            CORE.CHECKPOINT_DIR = root / "checkpoints"
            CORE.APP_DATA_DIR = root

            for size in ((32, 16), (36, 18), (44, 20), (58, 20), (80, 24), (92, 24), (120, 36), (160, 48)):
                with self.subTest(size=size):
                    config = coordinator_config()
                    chat = CORE.new_chat_state(root, config)
                    chat["_transient"] = True
                    app = ui.build_textual_app(
                        CORE,
                        CORE.DairackTui,
                        FakeProvider(()),
                        "test",
                        config,
                        root,
                        chat=chat,
                    )
                    async with app.run_test(size=size) as pilot:
                        await pilot.pause(0.05)
                        app._last_route = {"executor": "qwen3.6:27b"}
                        app.set_busy(True, "processing context / qwen3.6:27b")
                        app.query_one("#composer", ui.Composer).load_text("draft")
                        app.refresh_chrome(force=True)
                        await pilot.pause(0.02)

                        content_width = size[0] - 2
                        self.assertLessEqual(app._topbar_content(size[0]).cell_len, content_width)
                        self.assertLessEqual(app._metabar_content(size[0]).cell_len, content_width)
                        self.assertLessEqual(app._activity_content(size[0]).cell_len, content_width)
                        self.assertLessEqual(app._composer_meta_content(size[0]).cell_len, content_width)
                        self.assertLessEqual(app._keybar_content(size[0]).cell_len, content_width)

                        topbar = app.query_one("#topbar")
                        transcript = app.query_one("#transcript")
                        activity = app.query_one("#activity")
                        composer_shell = app.query_one("#composer-shell")
                        self.assertLessEqual(topbar.region.bottom, transcript.region.y)
                        self.assertLessEqual(transcript.region.bottom, activity.region.y)
                        self.assertLessEqual(activity.region.bottom, composer_shell.region.y)
                        self.assertLessEqual(composer_shell.region.bottom, size[1])

                        if size == (32, 16):
                            selector = ui.SelectorScreen(
                                "MODEL LIBRARY / LOCAL",
                                "Choose an action.",
                                [("install", ui.Text("INSTALL MODEL")), ("back", ui.Text("BACK"))],
                            )
                            app.push_screen(selector)
                            await pilot.pause(0.05)
                            selector_dialog = selector.query_one("#selector-dialog")
                            selector_keys = str(selector.query_one(".dialog-keys", ui.Static).render())
                            self.assertLessEqual(len(selector_keys), selector_dialog.region.width - 2)
                            await pilot.press("escape")

                            approval = ui.ApprovalScreen(
                                CORE,
                                {
                                    "name": "shell",
                                    "cmd": "sudo systemctl restart ollama",
                                    "reason": "Apply the reviewed service configuration.",
                                },
                                True,
                            )
                            app.push_screen(approval)
                            await pilot.pause(0.05)
                            approval_dialog = approval.query_one("#approval-dialog")
                            self.assertIn(
                                "sudo systemctl restart ollama",
                                str(approval.query_one(".approval-code", ui.Static).render()),
                            )
                            for button in approval.query(ui.Button):
                                self.assertLessEqual(button.region.right, approval_dialog.region.right)
                            await pilot.press("escape")

                        if size == (36, 18):
                            transfer = ui.ModelTransferScreen(FakeTransferProvider(), ["example:latest"])
                            app.push_screen(transfer)
                            for _ in range(40):
                                await pilot.pause(0.025)
                                if transfer.complete:
                                    break
                            self.assertTrue(transfer.complete)
                            dialog = transfer.query_one("#transfer-dialog")
                            self.assertLessEqual(dialog.region.right, size[0])
                            self.assertLessEqual(dialog.region.bottom, size[1])
                            self.assertIn(
                                "EXISTING LAYERS REUSED",
                                str(transfer.query_one(".transfer-plan", ui.Static).render()),
                            )
                            self.assertIn("100.0%", str(transfer.query_one("#transfer-meter", ui.Static).render()))
                            await pilot.press("enter")

            self.assertFalse(CORE.CHAT_DIR.exists())

    async def test_desktop_and_mobile_interactions(self) -> None:
        async def wait_for_selector(app: Any, pilot: Any) -> None:
            for _ in range(40):
                await pilot.pause(0.025)
                if isinstance(app.screen, ui.SelectorScreen):
                    return
            self.fail(f"selector did not open; current screen is {type(app.screen).__name__}")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            CORE.CONFIG_PATH = root / "config.json"
            CORE.HISTORY_PATH = root / "history"
            CORE.CHAT_DIR = root / "chats"
            CORE.INDEX_DB_PATH = root / "index.sqlite3"
            CORE.CHECKPOINT_DIR = root / "checkpoints"
            CORE.APP_DATA_DIR = root

            for size in ((120, 36), (44, 20)):
                config = coordinator_config()
                chat = CORE.new_chat_state(Path("/tmp"), config, "UI regression")
                blocks = [
                    {"role": "you", "text": "Coordinate this implementation."},
                    {
                        "role": "coordinator",
                        "text": (
                            "DELEGATION  01 / CODE REVIEW\n"
                            "FLOW  qwen3-coder:30b > qwen3.5:9b\n"
                            "STATE  COMPLETE / 2.4s\n"
                            "FIT  quality 76% / confidence 84%\n"
                            "TASK  inspect correctness\n"
                            "EVIDENCE\nNo blocking defect found."
                        ),
                    },
                    {
                        "role": "action",
                        "text": (
                            "WEB PAGE  COMPLETE  /  EXIT 0  /  1.8s\n"
                            "URL  https://example.com/a/long/reference/path\n"
                            "ACCESS  APPROVED ONCE\n"
                            "RESULT\nReference loaded successfully."
                        ),
                    },
                    {"role": "assistant", "text": "The implementation is ready."},
                ]
                app = ui.build_textual_app(
                    CORE,
                    CORE.DairackTui,
                    FakeProvider(()),
                    "test",
                    config,
                    Path("/tmp"),
                    chat=chat,
                    blocks=blocks,
                )
                async with app.run_test(size=size) as pilot:
                    await pilot.pause(0.1)
                    composer = app.query_one("#composer", ui.Composer)
                    self.assertTrue(composer.has_focus)
                    self.assertEqual(len(app.query(ui.TranscriptEntry)), 4)
                    shell = app.query_one("#composer-shell")
                    self.assertLessEqual(shell.region.bottom, size[1])

                    await pilot.press("f2")
                    await wait_for_selector(app, pilot)
                    dialog = app.screen.query_one("#selector-dialog")
                    self.assertLessEqual(dialog.region.right, size[0])
                    self.assertLessEqual(dialog.region.bottom, size[1])
                    self.assertEqual(app.screen.query_one("#selector-options", ui.OptionList).option_count, 5)

                    await pilot.press("enter")
                    for _ in range(40):
                        await pilot.pause(0.025)
                        if isinstance(app.screen, ui.SelectorScreen):
                            title = str(app.screen.query_one(".dialog-title", ui.Static).render())
                            if "COORDINATOR / OPERATING POLICY" in title:
                                break
                    self.assertIn("COORDINATOR / OPERATING POLICY", title)
                    await pilot.press("escape")
                    await pilot.pause(0.05)
                    self.assertNotIsInstance(app.screen, ui.SelectorScreen)
                    self.assertTrue(composer.has_focus)

                    app.push_screen(
                        ui.ApprovalScreen(
                            CORE,
                            {
                                "name": "shell",
                                "cmd": "sudo systemctl restart ollama",
                                "reason": "Apply the reviewed Ollama service configuration.",
                            },
                            True,
                        )
                    )
                    await pilot.pause(0.05)
                    approval = app.screen
                    self.assertIsInstance(approval, ui.ApprovalScreen)
                    approval_dialog = approval.query_one("#approval-dialog")
                    self.assertLessEqual(approval_dialog.region.right, size[0])
                    self.assertLessEqual(approval_dialog.region.bottom, size[1])
                    self.assertIn(
                        "sudo systemctl restart ollama",
                        str(approval.query_one(".approval-code", ui.Static).render()),
                    )
                    await pilot.press("escape")
                    await pilot.pause(0.05)
                    self.assertNotIsInstance(app.screen, ui.ApprovalScreen)

                    app.set_busy(True, "semantic arbitration")
                    await pilot.press("escape")
                    await pilot.pause(0.025)
                    self.assertTrue(app.cancel_event.is_set())
                    app.set_busy(False)

    async def test_model_library_coordinator_settings_and_transfer_are_native_modals(self) -> None:
        async def wait_for(app: Any, pilot: Any, screen_type: type[Any]) -> Any:
            for _ in range(80):
                await pilot.pause(0.025)
                if isinstance(app.screen, screen_type):
                    return app.screen
            self.fail(
                f"{screen_type.__name__} did not open; current screen is {type(app.screen).__name__}; "
                f"busy={app.busy}, library={app.model_library_active}; "
                f"last block: {app.blocks[-1:] if app.blocks else 'none'}"
            )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            CORE.CONFIG_PATH = root / "config.json"
            CORE.HISTORY_PATH = root / "history"
            CORE.CHAT_DIR = root / "chats"
            CORE.INDEX_DB_PATH = root / "index.sqlite3"
            CORE.CHECKPOINT_DIR = root / "checkpoints"
            CORE.APP_DATA_DIR = root
            config = coordinator_config()
            registry = ModelRegistry.discover(MODELS, hardware())
            initialized = InitializationResult(hardware(), registry, dict(config), "test", ui.PATHS)
            recommendations = recommendation_set(initialized.hardware, registry)
            app = ui.build_textual_app(
                CORE,
                CORE.DairackTui,
                FakeProvider(()),
                "test",
                config,
                Path("/tmp"),
            )

            with (
                patch.object(ui, "initialize", return_value=initialized),
                patch.object(ui, "recommendation_set", return_value=recommendations),
            ):
                async with app.run_test(size=(110, 34)) as pilot:
                    await pilot.press("f6")
                    library = await wait_for(app, pilot, ui.SelectorScreen)
                    self.assertIn("MODEL LIBRARY", str(library.query_one(".dialog-title", ui.Static).render()))
                    library_options = library.query_one("#selector-options", ui.OptionList)
                    self.assertEqual(library_options.option_count, 9)
                    self.assertTrue(library_options.get_option_at_index(0).disabled)
                    self.assertEqual(
                        [library_options.get_option_at_index(index).id for index in range(1, 4)],
                        ["custom", "sets", "update-all"],
                    )
                    library_filter = library.query_one("#selector-filter", ui.Input)
                    library_filter.value = "qwen"
                    await pilot.pause(0.05)
                    self.assertEqual(library_options.option_count, 4)
                    self.assertEqual(
                        [library_options.get_option_at_index(index).id for index in range(4)],
                        [
                            "__heading__:installed",
                            "model|qwen3.5:9b",
                            "model|qwen3-coder:30b",
                            "model|qwen3.6:27b",
                        ],
                    )
                    library_filter.value = "qwen3.5"
                    await pilot.pause(0.05)
                    self.assertEqual(library_options.option_count, 2)
                    self.assertEqual(library_options.get_option_at_index(1).id, "model|qwen3.5:9b")
                    await pilot.press("escape")
                    await pilot.pause(0.05)
                    self.assertNotIsInstance(app.screen, ui.SelectorScreen)

                    app.open_coordinator_settings()
                    settings = await wait_for(app, pilot, ui.SelectorScreen)
                    self.assertIn("COORDINATOR / CONTROL", str(settings.query_one(".dialog-title", ui.Static).render()))
                    await pilot.press("escape")
                    await pilot.pause(0.05)
                    self.assertNotIsInstance(app.screen, ui.SelectorScreen)

                    app.push_screen(ui.TextInputScreen("INPUT", "Esc must close this modal."))
                    await wait_for(app, pilot, ui.TextInputScreen)
                    await pilot.press("escape")
                    await pilot.pause(0.05)
                    self.assertNotIsInstance(app.screen, ui.TextInputScreen)

                    transfer = ui.ModelTransferScreen(FakeTransferProvider(), ["example:latest"])
                    app.push_screen(transfer)
                    await wait_for(app, pilot, ui.ModelTransferScreen)
                    for _ in range(80):
                        await pilot.pause(0.025)
                        if transfer.complete:
                            break
                    self.assertTrue(transfer.complete)
                    self.assertTrue(transfer.succeeded)
                    self.assertIn("100.0%", str(transfer.query_one("#transfer-meter", ui.Static).render()))
                    await pilot.press("enter")
                    await pilot.pause(0.05)
                    self.assertNotIsInstance(app.screen, ui.ModelTransferScreen)

                    app._available_update = ui.UpdateInfo(
                        "0.1.0",
                        "0.2.0",
                        "https://updates.example.test/dairack.json",
                        "https://updates.example.test/notes/0.2.0",
                    )
                    app.refresh_chrome(force=True)
                    self.assertIn("UPDATE 0.2.0", str(app.query_one("#topbar", ui.Static).render()))
                    app.handle_command("/update")
                    update_screen = await wait_for(app, pilot, ui.SelectorScreen)
                    self.assertIn("DAIRACK UPDATE", str(update_screen.query_one(".dialog-title", ui.Static).render()))
                    await pilot.press("escape")
                    await pilot.pause(0.05)
                    self.assertNotIsInstance(app.screen, ui.SelectorScreen)

    async def test_compute_connection_is_a_native_runtime_mode(self) -> None:
        async def wait_for_selector(app: Any, pilot: Any) -> ui.SelectorScreen:
            for _ in range(80):
                await pilot.pause(0.025)
                if isinstance(app.screen, ui.SelectorScreen):
                    return app.screen
            self.fail(f"compute selector did not open; current screen is {type(app.screen).__name__}")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            CORE.CONFIG_PATH = root / "config.json"
            CORE.HISTORY_PATH = root / "history"
            CORE.CHAT_DIR = root / "chats"
            CORE.INDEX_DB_PATH = root / "index.sqlite3"
            CORE.CHECKPOINT_DIR = root / "checkpoints"
            CORE.APP_DATA_DIR = root
            config = coordinator_config()
            config.update(
                {
                    "compute_mode": "remote",
                    "compute_name": "Studio Server",
                    "compute_transport": "bridge",
                    "compute_hardware_verified": True,
                    "ollama_host": "https://studio.example.test",
                    "remote_ollama_host": "https://studio.example.test",
                }
            )
            initial_provider = FakeProvider(())
            app = ui.build_textual_app(CORE, CORE.DairackTui, initial_provider, "test", config, root)

            async with app.run_test(size=(110, 34)) as pilot:
                self.assertIn("COMPUTE STUDIO SERVER / CONNECTED", app._topbar_content(110).plain)
                app.handle_command("/compute")
                center = await wait_for_selector(app, pilot)
                self.assertIn("COMPUTE / CONNECTION", str(center.query_one(".dialog-title", ui.Static).render()))
                options = center.query_one("#selector-options", ui.OptionList)
                option_ids = [options.get_option_at_index(index).id for index in range(options.option_count)]
                self.assertEqual(option_ids, ["refresh", "connect", "local", "token"])
                await pilot.press("escape")
                await pilot.pause(0.05)

                password = ui.TextInputScreen("COMPUTE / ACCESS TOKEN", "Private token", password=True)
                app.push_screen(password)
                await pilot.pause(0.05)
                self.assertTrue(password.query_one("#modal-input", ui.Input).password)
                await pilot.press("escape")
                await pilot.pause(0.05)

                connected_config = dict(config)
                connected_config["compute_name"] = "New Server"
                connected_config["ollama_host"] = "https://new.example.test"
                connected_config["remote_ollama_host"] = "https://new.example.test"
                registry = ModelRegistry.discover(
                    MODELS,
                    hardware(),
                    compute_endpoint="https://new.example.test",
                    hardware_verified=True,
                )
                initialized = InitializationResult(hardware(), registry, connected_config, "remote-version", ui.PATHS)
                replacement = FakeProvider(())
                probe = SimpleNamespace()
                with (
                    patch.object(ui, "probe_compute", return_value=probe),
                    patch.object(ui, "initialize", return_value=initialized),
                    patch.object(ui, "provider_for_config", return_value=replacement),
                    patch.object(ui, "compute_token", return_value=""),
                    patch.object(ui, "stored_compute_token", return_value=""),
                ):
                    app._begin_compute_connection("https://new.example.test")
                    for _ in range(100):
                        await pilot.pause(0.025)
                        if not app.busy and app.provider is replacement:
                            break
                self.assertIs(app.provider, replacement)
                self.assertEqual(app.config["compute_name"], "New Server")
                self.assertEqual(app.version, "remote-version")
                await wait_for_selector(app, pilot)


if __name__ == "__main__":
    unittest.main()
