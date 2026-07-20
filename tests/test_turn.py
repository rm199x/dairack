from __future__ import annotations

import unittest

from dairack.turn import (
    CompletionOutcome,
    ResponseFacts,
    ReviewOutcome,
    RouteFacts,
    TurnAction,
    TurnState,
    completion_arbiter_outcome,
    finalizing,
    next_action,
    review_outcome,
    synthesis_exhausted,
)


def _decide(
    state: TurnState,
    is_finalizing: bool = False,
    completion_checked: bool = False,
    **facts: object,
) -> TurnAction:
    response = ResponseFacts(
        has_call=bool(facts.get("has_call")),
        parse_error=str(facts.get("parse_error") or ""),
        incomplete_reason=str(facts.get("incomplete_reason") or ""),
        response_blank=bool(facts.get("response_blank")),
    )
    route = RouteFacts(
        has_reviewer=bool(facts.get("has_reviewer")),
        action_requirement=str(facts.get("action_requirement") or ""),
        contract_capability=bool(facts.get("contract_capability")),
    )
    return next_action(state, response, route, is_finalizing, completion_checked)


class FinalizingTests(unittest.TestCase):
    def test_finalizing_tracks_budget_and_forced_synthesis(self) -> None:
        self.assertFalse(finalizing(TurnState(action_limit=12, action_steps=0), force_synthesis=False))
        self.assertTrue(finalizing(TurnState(action_limit=12, action_steps=12), force_synthesis=False))
        self.assertTrue(finalizing(TurnState(action_limit=12, action_steps=0), force_synthesis=True))

    def test_synthesis_budget_is_bounded(self) -> None:
        self.assertFalse(synthesis_exhausted(TurnState(action_limit=12, synthesis_attempts=2)))
        self.assertTrue(synthesis_exhausted(TurnState(action_limit=12, synthesis_attempts=3)))


class LadderOrderTests(unittest.TestCase):
    def test_contract_repair_is_first_but_only_on_the_opening_step(self) -> None:
        state = TurnState(action_limit=12)
        self.assertEqual(_decide(state, action_requirement="use web_open"), TurnAction.REPAIR_CONTRACT)
        state.contract_repair_attempted = True
        self.assertEqual(_decide(state, action_requirement="use web_open"), TurnAction.DELIVER)
        # Not on later steps, even before the flag is set.
        self.assertEqual(
            _decide(TurnState(action_limit=12, action_steps=1), action_requirement="use web_open"),
            TurnAction.DELIVER,
        )

    def test_completion_retry_fires_once_then_stops_when_not_finalizing(self) -> None:
        state = TurnState(action_limit=12)
        self.assertEqual(_decide(state, incomplete_reason="unclosed fence"), TurnAction.RETRY_COMPLETION)
        state.completion_repair_attempted = True
        self.assertEqual(_decide(state, incomplete_reason="unclosed fence"), TurnAction.STOP_INCOMPLETE)

    def test_completion_arbiter_only_after_a_real_action_under_a_contract(self) -> None:
        after_action = TurnState(action_limit=12, action_steps=1)
        self.assertEqual(_decide(after_action, contract_capability=True), TurnAction.CHECK_COMPLETION)
        # No capability contract: an ordinary answer is delivered.
        self.assertEqual(_decide(after_action, contract_capability=False), TurnAction.DELIVER)
        # No prior action: nothing to verify.
        self.assertEqual(
            _decide(TurnState(action_limit=12, action_steps=0), contract_capability=True),
            TurnAction.DELIVER,
        )
        # Once the arbiter has run for this response, the ladder advances to delivery.
        self.assertEqual(
            _decide(after_action, contract_capability=True, completion_checked=True),
            TurnAction.DELIVER,
        )
        self.assertEqual(
            _decide(after_action, contract_capability=True, completion_checked=True, has_reviewer=True),
            TurnAction.REVIEW,
        )

    def test_parse_error_repairs_once_then_blocks(self) -> None:
        state = TurnState(action_limit=12)
        self.assertEqual(_decide(state, parse_error="invalid JSON"), TurnAction.REPAIR_PARSE)
        state.parse_repair_attempted = True
        self.assertEqual(_decide(state, parse_error="invalid JSON"), TurnAction.BLOCK_PARSE)

    def test_review_runs_up_to_the_round_limit_then_delivers(self) -> None:
        self.assertEqual(_decide(TurnState(action_limit=12), has_reviewer=True), TurnAction.REVIEW)
        self.assertEqual(
            _decide(TurnState(action_limit=12, review_rounds=2), has_reviewer=True),
            TurnAction.DELIVER,
        )
        self.assertEqual(_decide(TurnState(action_limit=12), has_reviewer=False), TurnAction.DELIVER)

    def test_a_valid_call_executes(self) -> None:
        self.assertEqual(_decide(TurnState(action_limit=12), has_call=True), TurnAction.EXECUTE_CALL)


class FinalizingLadderTests(unittest.TestCase):
    def test_finalizing_delivers_a_clean_answer(self) -> None:
        self.assertEqual(
            _decide(TurnState(action_limit=12, action_steps=12), is_finalizing=True), TurnAction.FINALIZE_DELIVER
        )

    def test_finalizing_retries_then_fails_on_an_invalid_final(self) -> None:
        state = TurnState(action_limit=12, action_steps=12, synthesis_attempts=1)
        self.assertEqual(_decide(state, is_finalizing=True, has_call=True), TurnAction.SYNTHESIZE_RETRY)
        exhausted = TurnState(action_limit=12, action_steps=12, synthesis_attempts=2)
        self.assertEqual(_decide(exhausted, is_finalizing=True, has_call=True), TurnAction.FINALIZE_FAIL)
        self.assertEqual(_decide(exhausted, is_finalizing=True, response_blank=True), TurnAction.FINALIZE_FAIL)

    def test_finalizing_takes_precedence_over_review_and_execution(self) -> None:
        # Even with a reviewer configured, a finalizing clean answer is delivered, not reviewed.
        state = TurnState(action_limit=12, action_steps=12)
        self.assertEqual(_decide(state, is_finalizing=True, has_reviewer=True), TurnAction.FINALIZE_DELIVER)


class SubOutcomeTests(unittest.TestCase):
    def test_completion_arbiter_repairs_then_stops(self) -> None:
        self.assertEqual(
            completion_arbiter_outcome(TurnState(action_limit=12), enforce=False), CompletionOutcome.ACCEPT
        )
        self.assertEqual(completion_arbiter_outcome(TurnState(action_limit=12), enforce=True), CompletionOutcome.REPAIR)
        spent = TurnState(action_limit=12, action_completion_repairs=2)
        self.assertEqual(completion_arbiter_outcome(spent, enforce=True), CompletionOutcome.STOP)

    def test_review_outcome_revises_then_marks_unresolved(self) -> None:
        first = TurnState(action_limit=12, review_rounds=1)
        self.assertEqual(review_outcome(first, "revise", has_feedback=True), ReviewOutcome.REVISE)
        second = TurnState(action_limit=12, review_rounds=2)
        self.assertEqual(review_outcome(second, "revise", has_feedback=True), ReviewOutcome.UNRESOLVED)
        # A pass, or a revise without usable feedback, accepts the answer.
        self.assertEqual(review_outcome(first, "pass", has_feedback=False), ReviewOutcome.ACCEPT)
        self.assertEqual(review_outcome(first, "revise", has_feedback=False), ReviewOutcome.ACCEPT)


if __name__ == "__main__":
    unittest.main()
