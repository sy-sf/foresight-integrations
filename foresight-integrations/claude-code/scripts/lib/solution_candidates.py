"""Helpers for progressive solution candidate loading."""

from __future__ import annotations

from dataclasses import asdict, dataclass

SOLUTION_CANDIDATES_STATE = "solution_candidates.json"
OPENED_SOLUTIONS_STATE = "opened_solutions.json"


@dataclass(slots=True)
class SolutionCandidateRoute:
    """Routing data for a solution candidate shown to the agent by title."""

    title: str
    solution_id: str
    source_bank_id: str
    organization_slug: str
    relevance: float
    layer: str


def normalize_solution_title(value: object) -> str:
    """Normalize titles to the single-line handle shown in the prompt."""
    if value is None:
        return ""
    return " ".join(str(value).split())


def _coerce_relevance(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def route_from_recall_solution(solution: object) -> SolutionCandidateRoute | None:
    """Build title-based routing data from one recall API solution result."""
    if not isinstance(solution, dict):
        return None

    title = normalize_solution_title(solution.get("title"))
    solution_id = normalize_solution_title(solution.get("id"))
    if not title or not solution_id:
        return None

    metadata = solution.get("metadata") if isinstance(solution.get("metadata"), dict) else {}
    organization_slug = normalize_solution_title(solution.get("organization_slug") or metadata.get("organization_slug"))

    return SolutionCandidateRoute(
        title=title,
        solution_id=solution_id,
        source_bank_id=normalize_solution_title(solution.get("source_bank_id")),
        organization_slug=organization_slug,
        relevance=_coerce_relevance(solution.get("relevance")),
        layer=normalize_solution_title(solution.get("layer")),
    )


def build_solution_candidate_routes(solutions: list[object]) -> list[SolutionCandidateRoute]:
    """Build loadable routes for all valid solution candidates."""
    routes = []
    for solution in solutions:
        route = route_from_recall_solution(solution)
        if route is not None:
            routes.append(route)
    return routes


def serialize_solution_candidate_state(
    routes: list[SolutionCandidateRoute],
    *,
    bank_id: str,
    saved_at: str,
    session_id: str = "",
) -> dict[str, object]:
    """Serialize current candidate routes for the MCP solution opener."""
    return {
        "bank_id": bank_id,
        "session_id": session_id,
        "saved_at": saved_at,
        "candidates": [asdict(route) for route in routes],
    }


def session_id_from_candidate_state(state: object) -> str:
    """Read the session ID associated with the current candidate set."""
    if not isinstance(state, dict):
        return ""
    return normalize_solution_title(state.get("session_id"))


def parse_solution_candidate_state(state: object) -> list[SolutionCandidateRoute]:
    """Parse candidate routes from state, ignoring malformed entries."""
    if not isinstance(state, dict):
        return []
    candidates = state.get("candidates")
    if not isinstance(candidates, list):
        return []

    routes = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        title = normalize_solution_title(candidate.get("title"))
        solution_id = normalize_solution_title(candidate.get("solution_id"))
        if not title or not solution_id:
            continue
        routes.append(
            SolutionCandidateRoute(
                title=title,
                solution_id=solution_id,
                source_bank_id=normalize_solution_title(candidate.get("source_bank_id")),
                organization_slug=normalize_solution_title(candidate.get("organization_slug")),
                relevance=_coerce_relevance(candidate.get("relevance")),
                layer=normalize_solution_title(candidate.get("layer")),
            )
        )
    return routes


def select_solution_route_by_title(
    routes: list[SolutionCandidateRoute],
    title: str,
) -> SolutionCandidateRoute | None:
    """Select the current candidate matching a title, preferring highest relevance."""
    normalized_title = normalize_solution_title(title)
    if not normalized_title:
        return None
    matches = [route for route in routes if route.title == normalized_title]
    if not matches:
        return None
    return max(matches, key=lambda route: route.relevance)


def opened_solution_record(
    route: SolutionCandidateRoute,
    *,
    session_id: str,
    opened_at: str,
) -> dict[str, object]:
    """Build retain metadata for a solution that was actually opened."""
    return {
        "session_id": session_id,
        "title": route.title,
        "solution_id": route.solution_id,
        "source_bank_id": route.source_bank_id,
        "organization_slug": route.organization_slug,
        "relevance": route.relevance,
        "layer": route.layer or "user",
        "opened_at": opened_at,
    }


def append_opened_solution_state(state: object, record: dict[str, object]) -> dict[str, object]:
    """Append one opened solution record under its session, de-duping the same route."""
    session_id = normalize_solution_title(record.get("session_id"))
    if not session_id:
        return state if isinstance(state, dict) else {"sessions": {}}

    current = state if isinstance(state, dict) else {}
    sessions = current.get("sessions") if isinstance(current.get("sessions"), dict) else {}
    session_records = sessions.get(session_id) if isinstance(sessions.get(session_id), list) else []
    key = (
        normalize_solution_title(record.get("source_bank_id")),
        normalize_solution_title(record.get("solution_id")),
        normalize_solution_title(record.get("layer")),
    )
    deduped = []
    for item in session_records:
        if not isinstance(item, dict):
            continue
        item_key = (
            normalize_solution_title(item.get("source_bank_id")),
            normalize_solution_title(item.get("solution_id")),
            normalize_solution_title(item.get("layer")),
        )
        if item_key != key:
            deduped.append(item)
    deduped.append(record)
    sessions[session_id] = deduped[-20:]
    return {"sessions": sessions}


def opened_solutions_for_session(state: object, session_id: str) -> list[dict[str, object]]:
    """Return opened solution records for one Claude session."""
    if not isinstance(state, dict):
        return []
    sessions = state.get("sessions")
    if not isinstance(sessions, dict):
        return []
    records = sessions.get(normalize_solution_title(session_id))
    if not isinstance(records, list):
        return []
    return [record for record in records if isinstance(record, dict)]


def clear_opened_solution_session(state: object, session_id: str) -> dict[str, object]:
    """Remove one session from opened solution state after retain succeeds."""
    if not isinstance(state, dict):
        return {"sessions": {}}
    sessions = state.get("sessions") if isinstance(state.get("sessions"), dict) else {}
    sessions.pop(normalize_solution_title(session_id), None)
    return {"sessions": sessions}
