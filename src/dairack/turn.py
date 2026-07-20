"""Frontend-agnostic turn decision core.

The agent turn is a state machine: a model response is generated, classified,
and one bounded recovery or delivery action is chosen. That decision ladder was
historically re-implemented inline in every interface, which is how the paths
drifted. This module owns the ladder as pure functions over explicit facts, so
each interface becomes a thin driver that performs I/O and defers every branch
here. No function in this module performs I/O, calls a model, or imports a UI.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class TurnAction(str, Enum):
    REPAIR_CONTRACT = "repair_contract"
    RETRY_COMPLETION = "retry_completion"
    STOP_INCOMPLETE = "stop_incomplete"
    CHECK_COMPLETION = "check_completion"
    SYNTHESIZE_RETRY = "synthesize_retry"
    FINALIZE_DELIVER = "finalize_deliver"
    FINALIZE_FAIL = "finalize_fail"
    REPAIR_PARSE = "repair_parse"
    BLOCK_PARSE = "block_parse"
    REVIEW = "review"
    DELIVER = "deliver"
    EXECUTE_CALL = "execute_call"


@dataclass
class TurnState:
    """Mutable per-turn counters and one-shot repair flags.

    A driver mutates this as it performs the actions the ladder returns; the
    decision functions only read it.
    """

    action_limit: int
    action_steps: int = 0
    synthesis_attempts: int = 0
    review_rounds: int = 0
    contract_repair_attempted: bool = False
    completion_repair_attempted: bool = False
    parse_repair_attempted: bool = False
    action_completion_repairs: int = 0

    def base_finalizing(self) -> bool:
        return self.action_steps >= self.action_limit


@dataclass(frozen=True)
class ResponseFacts:
    """What one generated response is, independent of how it was produced."""

    has_call: bool
    parse_error: str = ""
    incomplete_reason: str = ""
    response_blank: bool = False


@dataclass(frozen=True)
class RouteFacts:
    """Route properties the ladder branches on."""

    has_reviewer: bool = False
    action_requirement: str = ""
    contract_capability: bool = False


REVIEW_ROUND_LIMIT = 2
SYNTHESIS_ATTEMPT_LIMIT = 2
ACTION_COMPLETION_REPAIR_LIMIT = 2


def finalizing(state: TurnState, force_synthesis: bool) -> bool:
    """Whether this iteration must produce a final answer rather than an action."""
    return state.base_finalizing() or force_synthesis


def synthesis_exhausted(state: TurnState) -> bool:
    """Whether the synthesis budget is spent and the turn must stop."""
    return state.synthesis_attempts > SYNTHESIS_ATTEMPT_LIMIT


def next_action(
    state: TurnState,
    facts: ResponseFacts,
    route: RouteFacts,
    is_finalizing: bool,
    completion_checked: bool = False,
) -> TurnAction:
    """Return the single next action for a classified response.

    This is the canonical ladder. Its order is load-bearing and mirrors the
    behavior every interface historically implemented by hand. `completion_checked`
    is set by a driver after it has run the action-completion arbiter for this
    response, so the ladder advances past it to delivery instead of looping.
    """
    productive = not facts.has_call and not facts.parse_error

    if (
        productive
        and not is_finalizing
        and state.action_steps == 0
        and route.action_requirement
        and not state.contract_repair_attempted
    ):
        return TurnAction.REPAIR_CONTRACT

    if facts.incomplete_reason and not state.completion_repair_attempted:
        return TurnAction.RETRY_COMPLETION

    if facts.incomplete_reason and not is_finalizing:
        return TurnAction.STOP_INCOMPLETE

    if (
        productive
        and not facts.incomplete_reason
        and not is_finalizing
        and state.action_steps > 0
        and route.contract_capability
        and not completion_checked
    ):
        return TurnAction.CHECK_COMPLETION

    if is_finalizing:
        invalid_final = bool(facts.has_call or facts.parse_error or facts.incomplete_reason or facts.response_blank)
        if invalid_final and state.synthesis_attempts < SYNTHESIS_ATTEMPT_LIMIT:
            return TurnAction.SYNTHESIZE_RETRY
        return TurnAction.FINALIZE_FAIL if invalid_final else TurnAction.FINALIZE_DELIVER

    if facts.parse_error:
        return TurnAction.REPAIR_PARSE if not state.parse_repair_attempted else TurnAction.BLOCK_PARSE

    if not facts.has_call:
        if route.has_reviewer and state.review_rounds < REVIEW_ROUND_LIMIT:
            return TurnAction.REVIEW
        return TurnAction.DELIVER

    return TurnAction.EXECUTE_CALL


class CompletionOutcome(str, Enum):
    REPAIR = "repair"
    STOP = "stop"
    ACCEPT = "accept"


def completion_arbiter_outcome(state: TurnState, enforce: bool) -> CompletionOutcome:
    """Decide what a completed action-completion check implies.

    `enforce` is true when the arbiter is confident the action did not complete.
    """
    if not enforce:
        return CompletionOutcome.ACCEPT
    if state.action_completion_repairs < ACTION_COMPLETION_REPAIR_LIMIT:
        return CompletionOutcome.REPAIR
    return CompletionOutcome.STOP


class ReviewOutcome(str, Enum):
    REVISE = "revise"
    UNRESOLVED = "unresolved"
    ACCEPT = "accept"


def review_outcome(state: TurnState, verdict: str, has_feedback: bool) -> ReviewOutcome:
    """Decide what a completed review verdict implies for the turn.

    `state.review_rounds` is the number of reviews already performed, including
    the one that produced this verdict.
    """
    if verdict == "revise" and has_feedback:
        if state.review_rounds >= REVIEW_ROUND_LIMIT:
            return ReviewOutcome.UNRESOLVED
        return ReviewOutcome.REVISE
    return ReviewOutcome.ACCEPT
