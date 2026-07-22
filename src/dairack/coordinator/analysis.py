"""Deterministic task analysis and semantic-routing gates."""

from __future__ import annotations

import re
import urllib.parse
from pathlib import Path
from typing import Any

from ..messages import (
    TOOL_RESULT_PREFIXES,
    depends_on_conversation_context,
    latest_user_images,
    latest_user_message,
    latest_user_task,
    message_image_paths,
)
from ..network import normalize_http_url
from ..text import truncate
from ..tool_protocol import strip_tool_protocol
from .policy import policy_for
from .tuning import DEFAULT_TUNING

WEB_RESOURCE_PATTERN = re.compile(r"\b(?:web\s*page|website|site|url|link|domain)\b")
WEB_ACCESS_PATTERN = re.compile(
    r"\b(?:open|visit|browse|fetch|read|inspect|check|review|access|view|"
    r"look\s+at|take\s+a\s+look\s+at|go\s+to)\b"
)
WEB_EVALUATION_PATTERN = re.compile(
    r"\b(?:think|thoughts?|opinion|assess|evaluate|legit|safe|trustworthy|credib(?:le|ility)|"
    r"what(?:'s|\s+is)|tell\s+me\s+about)\b"
)
WEB_SEARCH_REQUEST_PATTERN = re.compile(
    r"\b(?:web\s*search|search\s+(?:the\s+)?(?:web|internet|online)|"
    r"look\s+(?:it\s+)?up\s+online|find\s+(?:it\s+)?online)\b"
)
WEB_SCHEME_TARGET_PATTERN = re.compile(r"https?://[^\s<>'\"`]+", re.IGNORECASE)
WEB_BARE_TARGET_PATTERN = re.compile(
    r"(?<![@\w.-])(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z]{2,63}(?::\d{1,5})?(?:/[^\s<>'\"`]*)?",
    re.IGNORECASE,
)
WEB_NON_DOMAIN_SUFFIXES = {
    "c",
    "cc",
    "cpp",
    "css",
    "csv",
    "go",
    "h",
    "hpp",
    "html",
    "ini",
    "java",
    "js",
    "json",
    "jsx",
    "lock",
    "md",
    "py",
    "rb",
    "rs",
    "sh",
    "sql",
    "toml",
    "ts",
    "tsx",
    "txt",
    "xml",
    "yaml",
    "yml",
}
COORDINATOR_FAST_CONVERSATION_PATTERN = re.compile(
    r"^(?:hi|hello|hey|greetings|good\s+(?:morning|afternoon|evening)|"
    r"thanks?(?:\s+you)?(?:\s+for\b.*)?|appreciate\s+it|cheers|bye|goodbye|whats\s+up|how\s+are\s+you|"
    r"okay|ok|cool|great|good|understood|nice\s+work|well\s+done|sounds\s+good|got\s+it|no\s+thanks)[.!?]*$"
)
COORDINATOR_FAST_FACT_PATTERN = re.compile(
    r"^(?:who|when|where)\b|^what\b(?:\s+\w+){0,4}\s+(?:is|are|was|were|does|did)\b|"
    r"^how\s+(?:many|much|old|far|long)\b"
)
COORDINATOR_JUDGMENT_PATTERN = re.compile(
    r"\b(?:best|better|should|recommend|compare|versus|vs\.?|trade-?offs?|audit|review|assess|"
    r"evaluate|analy[sz]e|design|diagnose)\b"
)
SOURCE_CODE_SUFFIXES = frozenset(
    {
        ".c",
        ".cc",
        ".cpp",
        ".cs",
        ".cxx",
        ".dart",
        ".ex",
        ".exs",
        ".fs",
        ".fsx",
        ".go",
        ".h",
        ".hh",
        ".hpp",
        ".hxx",
        ".java",
        ".js",
        ".jsx",
        ".kt",
        ".kts",
        ".lua",
        ".m",
        ".mm",
        ".php",
        ".py",
        ".r",
        ".rb",
        ".rs",
        ".scala",
        ".sh",
        ".sql",
        ".svelte",
        ".swift",
        ".ts",
        ".tsx",
        ".vue",
    }
)
LOCAL_FILE_SUFFIXES = SOURCE_CODE_SUFFIXES | frozenset(
    {
        ".css",
        ".csv",
        ".html",
        ".ini",
        ".json",
        ".lock",
        ".log",
        ".md",
        ".ps1",
        ".toml",
        ".txt",
        ".xml",
        ".yaml",
        ".yml",
    }
)
LOCAL_FILE_SUFFIX_PATTERN = "|".join(
    re.escape(suffix.removeprefix(".")) for suffix in sorted(LOCAL_FILE_SUFFIXES, key=len, reverse=True)
)
WEB_NON_DOMAIN_SUFFIXES.update(suffix.removeprefix(".") for suffix in LOCAL_FILE_SUFFIXES)
SOURCE_EVALUATION_PATTERN = re.compile(
    r"\b(?:audit|review|inspect|analy[sz]e|assess|evaluate|diagnose)\b",
    re.IGNORECASE,
)
DIRECT_RESPONSE_PATTERN = re.compile(
    r"^\s*(?:please\s+)?(?:reply|respond|answer|say)\b"
    r"(?:(?!\b(?:after|once)\s+(?:you\s+)?(?:run|check|inspect|open|search|read)\b).)*$",
    re.IGNORECASE | re.DOTALL,
)
LOCAL_ACTION_REQUEST_PATTERN = re.compile(
    r"(?:^\s*|[.:;!?]\s+|"
    r",\s+(?=(?:please\s+|(?:can|could|would|will)\s+(?:you|u)\s+|"
    r"(?:i(?:'d|\s+would)?\s+like|i\s+need)\s+(?:you|u)\s+to\s+))|"
    r"\b(?:please|then|also|and\s+then)\s+)"
    r"(?:please\s+)?"
    r"(?:(?:can|could|would|will)\s+(?:you|u)\s+|"
    r"(?:i(?:'d|\s+would)?\s+like|i\s+need)\s+(?:you|u)\s+to\s+)?"
    r"(?P<verb>run|execute|build|edit|modify|change|fix|implement|create|make|generate|save|write|apply|delete|remove|"
    r"rename|move|install|configure|deploy|commit|test|read|inspect|open|list|find|search|grep|review|audit|check)\b",
    re.IGNORECASE,
)
LOCAL_ACTION_EXPLANATION_PATTERN = re.compile(
    r"^\s*(?:how|what|why|when|where|explain|describe|tell\s+me\s+how|show\s+me\s+how|should\s+i)\b",
    re.IGNORECASE,
)
LOCAL_RESOURCE_QUERY_PATTERN = re.compile(
    r"^\s*(?:(?:what|which|how\s+many)\s+(?:[a-z0-9_-]+\s+){0,3}"
    r"(?:files?|folders?|directories?|paths?|projects?|repositories|repos?|entries|items)\b"
    r".*\b(?:in|inside|under|within|at)\b|"
    r"where\s+(?:is|are)\b.*\b(?:files?|folders?|directories?|paths?|projects?|repositories|repos?)\b)",
    re.IGNORECASE,
)
LOCAL_TOOL_USE_PATTERN = re.compile(
    r"^\s*(?:please\s+)?use\s+(?:the\s+)?(?:available\s+)?"
    r"(?P<tool>read_file|write_file|edit_file|list_dir|find_paths|grep|hardware_status|search_project|"
    r"index_project|shell|patch|tools?)\b",
    re.IGNORECASE,
)
LOCAL_RESOURCE_PATTERN = re.compile(
    r"\b(?:file|folder|directory|project|repository|repo|codebase|source|tests?|suite|path|diff|logs?|"
    r"script|command|app|application|program|bug|workspace|read_file|list_dir|find_paths|search_project)\b|"
    r"(?:^|[\s`'\"])(?:\.{0,2}[/\\]|[a-z]:[/\\])|"
    rf"\b[\w.-]+\.(?:{LOCAL_FILE_SUFFIX_PATTERN})\b",
    re.IGNORECASE,
)
LOCAL_ROOT_RESOURCE_PATTERN = re.compile(
    r"(?<!square\s)(?<!cube\s)\broot\b(?!\s+(?:cause|canal))",
    re.IGNORECASE,
)
LOCAL_FILE_TARGET_PATTERN = re.compile(
    r"(?P<target>(?:[a-z]:)?(?:\.{0,2}[/\\])?[a-z0-9_.-]+(?:[/\\][a-z0-9_. -]+)*\."
    rf"(?:{LOCAL_FILE_SUFFIX_PATTERN})"
    r"(?![a-z0-9_.-]))",
    re.IGNORECASE,
)
RESOURCE_REQUIRED_ACTIONS = frozenset(
    {
        "build",
        "create",
        "make",
        "generate",
        "save",
        "implement",
        "write",
        "test",
        "read",
        "inspect",
        "open",
        "list",
        "find",
        "search",
        "grep",
        "review",
        "audit",
        "check",
    }
)
FILE_CREATION_OBJECT_PATTERN = re.compile(
    r"\b(?:file|script|document|note|web\s*page|html\s*page|stylesheet)\b",
    re.IGNORECASE,
)


def signal_hits(text: str, terms: tuple[str, ...]) -> list[str]:
    hits: list[str] = []
    for raw_term in terms:
        term = raw_term.strip()
        if not term:
            continue
        if re.fullmatch(r"[a-z0-9]+(?:[ -][a-z0-9]+)*", term):
            pattern = re.escape(term).replace(r"\ ", r"\s+")
            matched = re.search(rf"(?<![a-z0-9_]){pattern}(?![a-z0-9_])", text)
        else:
            matched = term in text
        if matched:
            hits.append(term)
    return hits


def is_direct_response_request(prompt: str) -> bool:
    """Identify explicit response formatting that cannot require external work."""
    return bool(DIRECT_RESPONSE_PATTERN.fullmatch(str(prompt or "").strip()))


def dominant_role(signals: dict[str, float]) -> str:
    thresholds = {
        "coding": (float(signals.get("code") or 0), 0.35, 4),
        "reasoning": (float(signals.get("reasoning") or 0), 0.42, 3),
        "research": (float(signals.get("research") or 0), 0.42, 2),
        "agent": (float(signals.get("agent") or 0), 0.42, 1),
    }
    eligible = [
        (role, value, tie_priority)
        for role, (value, threshold, tie_priority) in thresholds.items()
        if value >= threshold
    ]
    if not eligible:
        return "general"
    return max(eligible, key=lambda item: (item[1], item[2]))[0]


def task_kind(signals: dict[str, float]) -> str:
    vision = float(signals.get("vision") or 0)
    code = float(signals.get("code") or 0)
    agent = float(signals.get("agent") or 0)
    reasoning = float(signals.get("reasoning") or 0)
    simple = float(signals.get("simple") or 0)
    role = dominant_role(signals)
    if vision and code >= 0.34:
        return "visual coding"
    if vision:
        return "visual analysis"
    if agent >= 0.50 and code >= 0.38:
        return "coding agent"
    if role == "agent" and agent >= 0.58:
        return "system agent"
    if role == "coding":
        return "coding"
    if role == "reasoning" and reasoning >= 0.48:
        return "deep reasoning"
    if role == "research":
        return "research"
    if role == "agent":
        return "system action"
    if role == "reasoning":
        return "reasoning"
    if simple >= 0.55:
        return "quick answer"
    return "general"


def task_role(signals: dict[str, float]) -> str:
    if signals.get("vision", 0.0) >= 0.25:
        return "vision"
    if signals.get("agent", 0.0) >= 0.50 and signals.get("code", 0.0) >= 0.38:
        return "agent"
    return dominant_role(signals)


def extract_public_web_targets(text: str) -> list[str]:
    """Extract canonical public-web candidates without resolving or fetching them."""
    candidates = WEB_SCHEME_TARGET_PATTERN.findall(text)
    masked = WEB_SCHEME_TARGET_PATTERN.sub(" ", text)
    candidates.extend(WEB_BARE_TARGET_PATTERN.findall(masked))
    targets: list[str] = []
    seen: set[str] = set()
    for raw in candidates:
        candidate = raw.rstrip(".,;:!?)]}")
        if not candidate:
            continue
        value = candidate if re.match(r"https?://", candidate, re.IGNORECASE) else f"https://{candidate}"
        try:
            parsed = urllib.parse.urlsplit(value)
            host = str(parsed.hostname or "").lower()
            if not host or host.rsplit(".", 1)[-1] in WEB_NON_DOMAIN_SUFFIXES:
                continue
            normalized = normalize_http_url(value)
        except ValueError:
            continue
        if normalized not in seen:
            seen.add(normalized)
            targets.append(normalized)
    return targets


def public_web_action_contract(messages: list[dict[str, Any]]) -> dict[str, str]:
    """Derive an explicit network capability requirement from natural language."""
    latest = latest_user_message(messages)
    prompt = str(latest.get("content") or "").strip()
    if not prompt:
        return {}
    normalized = re.sub(r"\s+", " ", prompt.lower().replace("’", "'")).strip()
    current_targets = extract_public_web_targets(prompt)
    explicit_search = bool(WEB_SEARCH_REQUEST_PATTERN.search(normalized)) or bool(
        current_targets and re.search(r"\bsearch(?:ed|ing)?\b", normalized)
    )
    asks_for_access = bool(WEB_ACCESS_PATTERN.search(normalized))
    asks_for_evaluation = bool(WEB_EVALUATION_PATTERN.search(normalized))
    references_resource = bool(WEB_RESOURCE_PATTERN.search(normalized))

    if explicit_search:
        return {
            "capability": "public_web",
            "preferred_tool": "web_search",
            "target": current_targets[0] if current_targets else "",
            "reason": "explicit public web search",
        }

    target = current_targets[0] if current_targets else ""
    if not target and references_resource and (asks_for_access or asks_for_evaluation):
        found_latest = False
        for message in reversed(messages):
            if message is latest:
                found_latest = True
                continue
            if not found_latest or str(message.get("role") or "") not in {"user", "assistant"}:
                continue
            prior = str(message.get("content") or "")
            if prior.startswith(TOOL_RESULT_PREFIXES):
                continue
            prior_targets = extract_public_web_targets(strip_tool_protocol(prior))
            if prior_targets:
                target = prior_targets[0]
                break

    if target and (asks_for_access or asks_for_evaluation):
        return {
            "capability": "public_web",
            "preferred_tool": "web_open",
            "target": target,
            "reason": "public website inspection",
        }
    return {}


def runtime_action_contract(messages: list[dict[str, Any]]) -> dict[str, str]:
    """Derive a conservative local-runtime requirement from explicit request grammar."""
    web_contract = public_web_action_contract(messages)
    if web_contract:
        return web_contract
    prompt = latest_user_task(messages).strip()
    if not prompt or is_direct_response_request(prompt):
        return {}
    resource_query = LOCAL_RESOURCE_QUERY_PATTERN.search(prompt)
    if resource_query:
        target_match = LOCAL_FILE_TARGET_PATTERN.search(prompt)
        return {
            "capability": "runtime_action",
            "preferred_tool": "find_paths" if prompt.lstrip().lower().startswith("where") else "list_dir",
            "target": str(target_match.group("target") or "") if target_match else "",
            "reason": "explicit local resource query",
        }
    if LOCAL_ACTION_EXPLANATION_PATTERN.search(prompt):
        return {}
    explicit_tool = LOCAL_TOOL_USE_PATTERN.search(prompt)
    if explicit_tool:
        requested_tool = str(explicit_tool.group("tool") or "auto").lower()
        if requested_tool in {"tool", "tools"}:
            requested_tool = "auto"
        target_match = LOCAL_FILE_TARGET_PATTERN.search(prompt)
        return {
            "capability": "runtime_action",
            "preferred_tool": requested_tool,
            "target": str(target_match.group("target") or "") if target_match else "",
            "reason": "explicit local tool action",
        }
    match = LOCAL_ACTION_REQUEST_PATTERN.search(prompt)
    if not match:
        return {}
    verb = str(match.group("verb") or "").lower()
    resource_context = prompt[: match.start("verb")] + prompt[match.end("verb") :]
    has_local_resource = bool(
        LOCAL_RESOURCE_PATTERN.search(resource_context) or LOCAL_ROOT_RESOURCE_PATTERN.search(resource_context)
    )
    if verb in RESOURCE_REQUIRED_ACTIONS and not has_local_resource:
        return {}
    target_match = LOCAL_FILE_TARGET_PATTERN.search(resource_context)
    target = str(target_match.group("target") or "") if target_match else ""
    creation_verb = verb in {"create", "make", "generate", "save", "write"}
    modifying_existing = bool(re.search(r"\b(?:change|changes|edit|modify|update|append)\b", resource_context, re.I))
    creates_text_file = bool(
        creation_verb
        and not modifying_existing
        and (FILE_CREATION_OBJECT_PATTERN.search(resource_context) or (target and verb != "write"))
    )
    return {
        "capability": "runtime_action",
        "preferred_tool": "write_file" if creates_text_file else "auto",
        "target": target,
        "reason": "explicit local runtime action",
    }


def execution_scope(
    kind: str,
    signals: dict[str, Any],
    action_contract: dict[str, Any] | None = None,
    routing_control: dict[str, Any] | None = None,
) -> str:
    """Convert semantic demand into a capability contract for the executor."""
    if isinstance(action_contract, dict) and action_contract.get("capability"):
        return "agentic"
    if bool(signals.get("direct_response")):
        return "direct-answer"
    if isinstance(routing_control, dict) and routing_control.get("active"):
        action_demand = max(
            (float(signals.get(name) or 0) for name in ("code", "agent", "research")),
            default=0.0,
        )
        if action_demand < 0.34:
            return "direct-answer"
    if kind == "quick answer" and float(signals.get("simple") or 0) >= 0.55:
        return "direct-answer"
    if (
        kind in {"general", "reasoning", "deep reasoning", "coding", "visual analysis"}
        and max(
            float(signals.get("agent") or 0),
            float(signals.get("research") or 0),
        )
        < 0.34
    ):
        return "direct-answer"
    return "agentic"


def is_direct_answer_route(route: dict[str, Any] | None) -> bool:
    """Return whether the route contract excludes tools and delegation."""
    explicit = str((route or {}).get("execution_scope") or "")
    if explicit:
        return explicit == "direct-answer"
    if str((route or {}).get("mode") or "") != "orchestrator":
        return False
    signals = (route or {}).get("signals")
    if not isinstance(signals, dict):
        return False
    return (
        execution_scope(
            str((route or {}).get("task_kind") or "general"),
            signals,
            (route or {}).get("action_contract") if isinstance((route or {}).get("action_contract"), dict) else None,
            (route or {}).get("routing_control") if isinstance((route or {}).get("routing_control"), dict) else None,
        )
        == "direct-answer"
    )


def analyze_task(messages: list[dict[str, Any]], cwd: Path | None = None) -> dict[str, Any]:
    prompt = latest_user_task(messages)
    image_paths = latest_user_images(messages)
    action_contract = runtime_action_contract(messages)
    text = prompt.lower()
    direct_response = is_direct_response_request(prompt)
    file_target_match = LOCAL_FILE_TARGET_PATTERN.search(prompt)
    file_target = str(file_target_match.group("target") or "") if file_target_match else ""
    portable_target = file_target.replace("\\", "/")
    source_suffix = Path(portable_target).suffix.lower() if portable_target else ""
    source_evaluation = bool(source_suffix in SOURCE_CODE_SUFFIXES and SOURCE_EVALUATION_PATTERN.search(prompt))
    code_hits = signal_hits(
        text,
        (
            "code",
            "function",
            "class ",
            "script",
            "bug",
            "stack trace",
            "traceback",
            "compile",
            "typescript",
            "javascript",
            "python",
            "rust",
            "golang",
            "sql",
            "api",
            "test",
            "tests",
            "test suite",
            "pytest",
            "unittest",
            "implementation",
            "source code",
            "refactor",
            "repository",
            "repo",
            "git ",
            "diff",
            "src/",
            ".py",
            ".js",
            ".ts",
            "```",
        ),
    )
    if source_suffix in SOURCE_CODE_SUFFIXES and source_suffix not in code_hits:
        code_hits.append(source_suffix)
    agent_hits = signal_hits(
        text,
        (
            "install",
            "implement",
            "edit",
            "editing",
            "change",
            "fix",
            "build",
            "run ",
            "execute",
            "inspect",
            "search files",
            "open file",
            "read file",
            "list files",
            "what files",
            "which files",
            "folder",
            "directory",
            "deploy",
            "configure",
            "set up",
            "setup",
            "terminal",
            "server",
            "hardware",
            "commit",
            "apply",
            "create file",
            "delete",
            "rename",
        ),
    )
    reasoning_hits = signal_hits(
        text,
        (
            "reason",
            "analyze",
            "architecture",
            "design",
            "tradeoff",
            "tradeoffs",
            "trade-off",
            "trade-offs",
            "pros and cons",
            "why",
            "prove",
            "derive",
            "optimize",
            "optimization",
            "diagnose",
            "root cause",
            "deep",
            "complex",
            "strategy",
            "compare",
            "evaluate",
            "algorithm",
            "math",
            "signal processing",
            "dsp",
            "coherence",
            "audit",
            "review",
            "assess",
        ),
    )
    research_hits = signal_hits(
        text,
        (
            "latest",
            "current",
            "today",
            "news",
            "internet",
            "web",
            "search online",
            "look up",
            "verify",
            "source",
            "citation",
            "price",
            "release",
            "documentation",
            "as of ",
        ),
    )
    risk_hits = signal_hits(
        text,
        (
            "production",
            "security",
            "permission",
            "sudo",
            "database",
            "migration",
            "delete",
            "payment",
            "medical",
            "legal",
            "backup",
            "restore",
            "network",
            "firewall",
            "credential",
            "password",
        ),
    )
    word_count = len(re.findall(r"\b\w+\b", text))
    code = min(1.0, 0.25 + 0.13 * (len(code_hits) - 1)) if code_hits else 0.0
    agent = min(1.0, 0.20 + 0.14 * (len(agent_hits) - 1)) if agent_hits else 0.0
    reasoning = min(1.0, 0.27 + 0.14 * (len(reasoning_hits) - 1)) if reasoning_hits else 0.0
    research = min(1.0, 0.22 + 0.18 * (len(research_hits) - 1)) if research_hits else 0.0
    risk = min(1.0, 0.20 + 0.16 * (len(risk_hits) - 1)) if risk_hits else 0.0
    if source_suffix in SOURCE_CODE_SUFFIXES:
        code = max(code, 0.48)
    if source_evaluation:
        code = max(code, 0.56)
        reasoning = max(reasoning, 0.42)
    if action_contract:
        agent = max(agent, 0.48)
        if action_contract.get("capability") == "public_web":
            research = max(research, 0.72)
    vision = 1.0 if image_paths else 0.0
    in_git_repository = bool(cwd is not None and (cwd / ".git").exists() and (code >= 0.35 or agent >= 0.35))
    if in_git_repository:
        code = min(1.0, code + 0.08)
    simple = 0.92 if word_count <= 24 else 0.72 if word_count <= 55 else 0.38 if word_count <= 120 else 0.12
    task_difficulty = max(code, agent, reasoning, research, risk, vision * 0.35)
    simple *= max(0.20, 1.0 - 0.52 * task_difficulty)
    general = max(0.25, min(1.0, 0.72 + simple * 0.20 - max(code, agent) * 0.18))
    conjunctions = len(re.findall(r"\b(and then|then|also|after that|before|while|across|end to end)\b", text))
    complexity = 0.12
    complexity += min(0.24, word_count / 520.0)
    complexity += reasoning * 0.33 + agent * 0.28 + code * 0.15 + research * 0.18 + risk * 0.18
    complexity += min(0.15, conjunctions * 0.05)
    complexity += min(0.18, vision * (0.10 + 0.03 * len(image_paths)))
    if agent >= 0.55 and code >= 0.45:
        complexity += 0.07
    if reasoning >= 0.60 and risk >= 0.35:
        complexity += 0.07
    if prompt.count("?") > 2:
        complexity += 0.06
    complexity = min(1.0, complexity)

    evidence: list[str] = []
    for label, hits in (
        ("code", code_hits),
        ("actions", agent_hits),
        ("reasoning", reasoning_hits),
        ("research", research_hits),
        ("risk", risk_hits),
    ):
        if hits:
            evidence.append(f"{label}: {', '.join(hits[:3])}")
    if not evidence:
        evidence.append("short conversational request" if simple >= 0.55 else "general language request")
    if action_contract:
        if action_contract.get("capability") == "public_web":
            preferred_tool = str(action_contract.get("preferred_tool") or "web_open")
            target = str(action_contract.get("target") or "")
            evidence.append(f"public web action: {preferred_tool}" + (f" {target}" if target else ""))
        else:
            evidence.append("explicit local runtime action")
    if image_paths:
        evidence.insert(0, f"vision input: {len(image_paths)} image{'s' if len(image_paths) != 1 else ''}")
    if in_git_repository:
        evidence.append("active Git repository")

    signals = {
        "code": round(code, 3),
        "agent": round(agent, 3),
        "reasoning": round(reasoning, 3),
        "general": round(general, 3),
        "research": round(research, 3),
        "vision": round(vision, 3),
        "risk": round(risk, 3),
        "simple": round(simple, 3),
        "direct_response": 1.0 if direct_response else 0.0,
    }
    if direct_response:
        evidence.insert(0, "explicit response-only instruction")
    return {
        "prompt": prompt,
        "task_kind": task_kind(signals),
        "complexity": round(complexity, 3),
        "signals": signals,
        "evidence": evidence[:6],
        "action_contract": action_contract,
    }


def semantic_context(messages: list[dict[str, Any]]) -> str:
    latest = latest_user_message(messages)
    if not latest:
        return ""
    latest_index = next((index for index in range(len(messages) - 1, -1, -1) if messages[index] is latest), -1)
    if latest_index <= 0:
        return ""
    context: list[str] = []
    for message in messages[max(0, latest_index - 8) : latest_index]:
        role = str(message.get("role") or "").lower()
        content = str(message.get("content") or "").strip()
        if role not in {"user", "assistant"} or not content or content.startswith(TOOL_RESULT_PREFIXES):
            continue
        context.append(f"{role.upper()}: {truncate(strip_tool_protocol(content), 1600)}")
    return truncate("\n\n".join(context), 6000)


def referenced_task_analysis(messages: list[dict[str, Any]], cwd: Path) -> dict[str, Any]:
    latest = latest_user_message(messages)
    if not latest:
        return {}
    found_latest = False
    for message in reversed(messages):
        if message is latest:
            found_latest = True
            continue
        if not found_latest or message.get("role") != "user":
            continue
        content = str(message.get("content") or "").strip()
        if not content or content.startswith(TOOL_RESULT_PREFIXES):
            continue
        prior: dict[str, Any] = {"role": "user", "content": content}
        images = message_image_paths(message)
        if images:
            prior["image_paths"] = images
        return analyze_task([prior], cwd)
    return {}


def semantic_gate(policy: str, analysis: dict[str, Any], score_gap: float, has_context: bool) -> str:
    prompt = str(analysis.get("prompt") or "")
    signals = analysis.get("signals") or {}
    action_contract = analysis.get("action_contract")
    word_count = len(re.findall(r"\b\w+\b", prompt))
    normalized = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s?.]", " ", prompt.lower().replace("'", ""))).strip()
    domain_strength = max(
        (float(signals.get(key) or 0) for key in ("code", "agent", "reasoning", "research", "vision", "risk")),
        default=0.0,
    )
    structural_steps = len(re.findall(r"(?:^|\n)\s*(?:[-*]|\d+[.)])\s+|[;\n]", prompt))
    semantic_mode = policy_for(policy).semantic_mode
    if semantic_mode == "off" or COORDINATOR_FAST_CONVERSATION_PATTERN.fullmatch(normalized):
        return ""
    if (
        isinstance(action_contract, dict)
        and action_contract.get("capability") == "runtime_action"
        and action_contract.get("reason") == "explicit local resource query"
        and not float(signals.get("vision") or 0)
        and not float(signals.get("risk") or 0)
    ):
        return ""
    if has_context and depends_on_conversation_context(prompt):
        return "conversation-dependent follow-up"
    if has_context and word_count <= 4:
        return "short contextual turn"
    if float(signals.get("vision") or 0):
        return "visual capability assessment"
    if semantic_mode == "substantive" and (word_count >= 3 or domain_strength >= 0.34):
        return "quality policy semantic pass"
    if semantic_mode != "ambiguous":
        return ""
    if structural_steps >= 2 and word_count >= 12:
        return "multi-part request"
    if (
        word_count <= 9
        and domain_strength < 0.34
        and COORDINATOR_FAST_FACT_PATTERN.search(normalized)
        and not COORDINATOR_JUDGMENT_PATTERN.search(normalized)
    ):
        return ""
    if word_count <= 2 and domain_strength < 0.34:
        return ""
    if word_count >= 3 or domain_strength >= 0.34 or score_gap <= 0.045:
        return "adaptive semantic pass"
    return ""


def merge_semantic_assessment(
    analysis: dict[str, Any],
    assessment: dict[str, Any],
    tuning: Any = DEFAULT_TUNING,
    referenced_analysis: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Blend semantic evidence without weakening deterministic capability gates."""
    if not assessment:
        return analysis
    original_analysis = analysis
    deterministic_signals = original_analysis.get("signals")
    deterministic_signals = dict(deterministic_signals) if isinstance(deterministic_signals, dict) else {}
    existing_control = analysis.get("routing_control")
    existing_control = dict(existing_control) if isinstance(existing_control, dict) else {}
    semantic_control = assessment.get("routing_control")
    semantic_control = dict(semantic_control) if isinstance(semantic_control, dict) else {}
    # A conservative deterministic parse exists only for explicit compute
    # directives. Do not let classifier variance erase that user instruction.
    control = existing_control if existing_control.get("active") else semantic_control
    control_authority_signals: dict[str, Any] = {}
    if control.get("active") and control.get("applies_to_previous") and control.get("resolved_task"):
        resolved = analyze_task([{"role": "user", "content": str(control["resolved_task"])}])
        resolved["prompt"] = str(analysis.get("prompt") or "")
        resolved["resolved_task"] = str(control["resolved_task"])
        current_contract = analysis.get("action_contract")
        referenced_contract = (referenced_analysis or {}).get("action_contract")
        resolved["action_contract"] = (
            current_contract
            if isinstance(current_contract, dict) and current_contract.get("capability")
            else referenced_contract
            if isinstance(referenced_contract, dict) and referenced_contract.get("capability")
            else {}
        )
        resolved_signals = dict(resolved.get("signals") or {})
        observed_vision = max(
            float((analysis.get("signals") or {}).get("vision") or 0),
            float(((referenced_analysis or {}).get("signals") or {}).get("vision") or 0),
        )
        resolved_signals["vision"] = round(observed_vision, 3)
        resolved["signals"] = resolved_signals
        resolved["task_kind"] = task_kind(resolved_signals)
        resolved["evidence"] = ["resolved per-turn routing instruction", *list(resolved.get("evidence") or [])][:6]
        authority = referenced_analysis if referenced_analysis else resolved
        control_authority_signals = dict(authority.get("signals") or {})
        analysis = resolved
    merged = dict(analysis)
    signals = dict(analysis.get("signals") or {})
    prompt = str(original_analysis.get("prompt") or "")
    word_count = len(re.findall(r"\b\w+\b", prompt))
    deterministic_strength = max(
        (float(signals.get(key) or 0) for key in ("code", "agent", "reasoning", "research", "risk")),
        default=0.0,
    )
    confidence = max(0.0, min(1.0, float(assessment.get("confidence", 0.65))))
    base_weight = 0.55 if word_count >= 8 or deterministic_strength >= 0.34 else 0.42
    semantic_weight = min(0.76, base_weight * (0.65 + confidence * 0.35) * float(tuning.semantic_evidence_scale))
    if assessment.get("intent") == "conversation":
        semantic_weight = min(semantic_weight, 0.32)
    if assessment.get("trigger") == "conversation-dependent follow-up":
        semantic_weight = max(semantic_weight, 0.64 * (0.85 + confidence * 0.15))
    deterministic_risk = float(signals.get("risk") or 0)
    referenced_signals = (referenced_analysis or {}).get("signals")
    for key in ("code", "agent", "reasoning", "general", "research", "risk"):
        semantic_value = float(assessment.get(key) or 0)
        existing = float(signals.get(key) or 0)
        referenced_value = float(referenced_signals.get(key) or 0) if isinstance(referenced_signals, dict) else 0.0
        if key in {"code", "agent", "research"}:
            grounded = max(existing, referenced_value)
            promotion_margin = 0.24 if grounded >= 0.34 else 0.20
            promotion_margin += max(0.0, confidence - 0.60) * 0.55
            semantic_value = min(semantic_value, max(0.18, grounded + promotion_margin))
        if control.get("active") and key in {"agent", "research"}:
            authority_value = float(control_authority_signals.get(key) or 0)
            semantic_value = min(semantic_value, max(0.18, existing + 0.12, authority_value + 0.12))
        if assessment.get("intent") in {"conversation", "general"} and key != "general":
            corroborated_margin = 0.12 if existing >= 0.34 else 0.0
            semantic_value = min(
                semantic_value,
                max(0.18, existing + corroborated_margin, referenced_value + 0.12),
            )
        blended = existing * (1.0 - semantic_weight) + semantic_value * semantic_weight
        evidence_floor = existing * (0.72 - confidence * 0.30)
        signals[key] = round(max(evidence_floor, blended), 3)
    signals["risk"] = round(max(deterministic_risk, float(signals.get("risk") or 0)), 3)
    action_contract = analysis.get("action_contract")
    if (
        not (isinstance(action_contract, dict) and action_contract.get("capability"))
        and bool(assessment.get("requires_action"))
        and confidence >= 0.60
        and not bool(signals.get("direct_response"))
        and not (
            float(deterministic_signals.get("vision") or 0) > 0
            and max(
                float(deterministic_signals.get("agent") or 0),
                float(deterministic_signals.get("research") or 0),
            )
            < 0.18
        )
    ):
        action_contract = {
            "capability": "runtime_action",
            "preferred_tool": "auto",
            "target": "",
            "reason": "semantic runtime action requirement",
        }
        merged["action_contract"] = action_contract
        signals["agent"] = round(max(0.48, float(signals.get("agent") or 0)), 3)
    if signals.get("direct_response"):
        # Response-format instructions such as "reply exactly ..." cannot
        # acquire tool authority from a mistaken semantic assessment.
        signals["agent"] = round(min(0.18, float(signals.get("agent") or 0)), 3)
    if isinstance(action_contract, dict) and action_contract.get("capability") == "public_web":
        signals["research"] = round(max(0.72, float(signals.get("research") or 0)), 3)
        signals["agent"] = round(max(0.48, float(signals.get("agent") or 0)), 3)
    if isinstance(referenced_signals, dict):
        for key in ("code", "agent", "reasoning", "research", "risk"):
            inherited = float(referenced_signals.get(key) or 0) * 0.88
            signals[key] = round(max(float(signals.get(key) or 0), inherited), 3)
    signals["vision"] = round(float(signals.get("vision") or 0), 3)
    specialized = max(
        signals.get("code", 0.0),
        signals.get("agent", 0.0),
        signals.get("reasoning", 0.0),
        signals.get("research", 0.0),
        signals.get("risk", 0.0),
        signals.get("vision", 0.0) * 0.35,
    )
    signals["simple"] = round(min(float(signals.get("simple") or 0), max(0.05, 1.0 - specialized * 0.82)), 3)
    merged["signals"] = signals
    existing_complexity = float(analysis.get("complexity") or 0)
    semantic_complexity = float(assessment.get("complexity") or 0)
    complexity_weight = 0.55 if word_count >= 8 or deterministic_strength >= 0.34 else 0.35
    if referenced_analysis and assessment.get("trigger") == "conversation-dependent follow-up":
        complexity_weight = max(complexity_weight, semantic_weight)
    merged["complexity"] = round(
        max(
            existing_complexity,
            existing_complexity * (1.0 - complexity_weight) + semantic_complexity * complexity_weight,
        ),
        3,
    )
    if referenced_analysis:
        merged["complexity"] = round(
            max(float(merged["complexity"]), float(referenced_analysis.get("complexity") or 0) * 0.90),
            3,
        )
    if (
        assessment.get("intent") == "system_action"
        and signals.get("agent", 0.0) >= 0.42
        and signals.get("risk", 0.0) >= 0.30
    ):
        operational_floor = min(
            1.0,
            semantic_complexity * 0.80 + signals["risk"] * float(tuning.operational_risk_weight),
        )
        merged["complexity"] = round(max(float(merged["complexity"]), operational_floor), 3)
    merged["task_kind"] = task_kind(signals)
    evidence = list(analysis.get("evidence") or [])
    evidence.insert(0, f"semantic assessor {assessment.get('model')}: {assessment.get('reason')}")
    if referenced_analysis:
        evidence.insert(1, f"resolved prior task: {referenced_analysis.get('task_kind') or 'general'}")
    merged["evidence"] = evidence[:6]
    merged_assessment = dict(assessment)
    merged_assessment["weight"] = semantic_weight
    merged["semantic_assessment"] = merged_assessment
    if control:
        merged["routing_control"] = control
    return merged
