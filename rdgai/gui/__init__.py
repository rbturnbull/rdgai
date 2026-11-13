"""
A browser GUI for annotating and reviewing transcriptional-change classifications.

It is a small Starlette app over the same ``AnnotationStore`` as the MCP server, so it can run inside the MCP
server's process (``rdgai serve-mcp --gui-port``) or on its own (``rdgai annotate``). Everything about the
categories comes from the TEI ``<interpGrp type="transcriptional">``; nothing is specific to one corpus.
"""
from pathlib import Path

from pydantic import BaseModel
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from ..annotation import (
    AnnotationError,
    AnnotationStore,
    Category,
    CERTAINTY_VALUES,
    ConflictError,
    ReadOnlyError,
    UnitNotFoundError,
)

STATIC = Path(__file__).parent / "static"

# Keys for the category buttons, in button order: 1-9, 0, then shift+1 ... shift+0.
KEYS = [str(digit) for digit in range(1, 10)] + ["0"] + [f"shift+{digit}" for digit in [*range(1, 10), 0]]


class Button(BaseModel):
    name: str
    description: str
    inverse: str
    group: str
    row: int
    column: int
    key: str | None


def button_layout(categories:list[Category], group_by_prefix:str|None=None) -> list[Button]:
    """
    Lays out one button per category in a grid with fixed positions.

    Rows come from a grouping hint: the <interp>'s @type/@n (Category.group) if any category has one, otherwise
    the part of the name before ``group_by_prefix`` (e.g. 'Minor' in 'Minor_Omission'). Rows are in order of first
    appearance. When grouping by prefix, columns are aligned by the rest of the name, so that e.g. Minor_Omission
    sits above Major_Omission. Without a hint, all buttons are in one row in document order.
    Keys are assigned in row-major order.
    """
    use_types = any(category.group for category in categories)
    placements = []
    for category in categories:
        if use_types:
            group, suffix = category.group, None
        elif group_by_prefix and group_by_prefix in category.name:
            group, suffix = category.name.split(group_by_prefix, 1)
        else:
            group, suffix = "", None
        placements.append((category, group, suffix))

    rows = list(dict.fromkeys(group for _, group, _ in placements))
    suffixes = list(dict.fromkeys(suffix for _, group, suffix in placements if suffix is not None and group))
    next_column = {group: 0 for group in rows}

    cells = []
    for category, group, suffix in placements:
        if suffix is not None and group:
            column = suffixes.index(suffix)
        else:
            column = next_column[group]
            next_column[group] += 1
        cells.append((rows.index(group), column, category, group))

    cells.sort(key=lambda cell: (cell[0], cell[1]))
    return [
        Button(
            name=category.name,
            description=category.description,
            inverse=category.inverse,
            group=group,
            row=row,
            column=column,
            key=KEYS[index] if index < len(KEYS) else None,
        )
        for index, (row, column, category, group) in enumerate(cells)
    ]


def pair_key(active:str, passive:str) -> str:
    return f"{active}|{passive}"


def parse_pairs(data) -> list[tuple[str, str]]:
    pairs = data.get("pairs") or []
    return [(str(pair[0]), str(pair[1])) for pair in pairs]


def parse_expected(data) -> dict:
    """ {"a|b": category or null} -> {("a", "b"): category or None}; pairs not listed are not checked. """
    expected = {}
    for key, value in (data.get("expected") or {}).items():
        active, _, passive = key.partition("|")
        expected[(active, passive)] = value if value is None else str(value)
    return expected


def error_response(error:AnnotationError) -> JSONResponse:
    if isinstance(error, ConflictError):
        current = {pair_key(*key): value for key, value in error.current.items()}
        return JSONResponse({"error": str(error), "current": current}, status_code=409)
    if isinstance(error, ReadOnlyError):
        return JSONResponse({"error": str(error)}, status_code=403)
    if isinstance(error, UnitNotFoundError):
        return JSONResponse({"error": str(error)}, status_code=404)
    return JSONResponse({"error": str(error)}, status_code=400)


def create_app(store:AnnotationStore, group_by_prefix:str|None=None) -> Starlette:
    """ Builds the GUI app over a store (which may be shared with an MCP server in the same process). """

    def queue_filters(request:Request) -> dict:
        params = request.query_params
        default_sample = "sampled" if store.load_sample() else "all"
        return dict(
            sample=params.get("sample") or default_sample,
            status=params.get("status") or "all",
            verse=params.get("verse") or "",
        )

    def queue_apps(filters:dict):
        return store.filter_units(status=filters["status"], verse=filters["verse"], sample=filters["sample"])

    def pair_payload(app, pair, role:str|None) -> dict:
        info = store.pair_info(pair)
        suggestions = [] if role == "test" else store.suggestions_for(app, pair)
        if info.provenance is not None:
            reason = info.provenance.justification
        elif suggestions:
            reason = max(suggestions, key=lambda suggestion: suggestion.when).justification
        else:
            reason = ""
        rule = None
        if info.rule is not None:
            waiting = False
            if info.rule == 3:
                parent = store.get_pair(app, *info.rule_parent)
                waiting = parent.relation_element() is None and parent.get_inverse().relation_element() is None
            rule = dict(rule=info.rule, parent=info.rule_parent, waiting=waiting)
        return dict(
            first=pair.active.n,
            second=pair.passive.n,
            first_text=info.active_text,
            second_text=info.passive_text,
            category=info.category,
            inverse_category=info.inverse_category,
            provenance=info.provenance.model_dump() if info.provenance else None,
            source=info.source,
            rule=rule,
            suggestions=[suggestion.model_dump() for suggestion in suggestions],
            reason=reason,
        )

    def progress(app) -> dict:
        pairs = store.work_pairs(app)
        return dict(
            pairs_done=sum(store.is_classified(pair) for pair in pairs),
            pairs_total=len(pairs),
            rule_pairs=len(store.rule_pairs(app)),
        )

    def next_unclassified(apps:list, current_id:str) -> str|None:
        ids = [str(app) for app in apps]
        start = ids.index(current_id) + 1 if current_id in ids else 0
        for offset in range(len(apps)):
            app = apps[(start + offset) % len(apps)]
            if str(app) != current_id and store.status(app) in ("unclassified", "partial"):
                return str(app)
        return None

    async def index(request:Request):
        return FileResponse(STATIC / "index.html")

    def config(request:Request):
        with store.lock:
            store.refresh()
            definitions = store.definitions()
            buttons = button_layout(definitions.categories, group_by_prefix=group_by_prefix)
            return JSONResponse(dict(
                file=store.path.name,
                language=definitions.language,
                guidance=definitions.guidance,
                direction_convention=definitions.direction_convention,
                definitions_hash=definitions.definitions_hash,
                buttons=[button.model_dump() for button in buttons],
                certainty=list(CERTAINTY_VALUES),
                annotator=store.annotator,
                read_only=store.read_only,
                has_sample=store.load_sample() is not None,
            ))

    def queue(request:Request):
        try:
            with store.lock:
                store.refresh()
                filters = queue_filters(request)
                apps = queue_apps(filters)
                return JSONResponse(dict(
                    filters=filters,
                    total=len(apps),
                    units=[store.unit_summary(app).model_dump() for app in apps],
                ))
        except AnnotationError as error:
            return error_response(error)

    def unit(request:Request):
        try:
            with store.lock:
                store.refresh()
                app = store.get_app(request.path_params["unit_id"])
                unit_id = str(app)
                filters = queue_filters(request)
                apps = queue_apps(filters)
                ids = [str(queued) for queued in apps]
                role = store.sample_role(unit_id)
                if unit_id in ids:
                    index = ids.index(unit_id)
                    position = dict(index=index, total=len(ids))
                    prev_id = ids[index - 1] if index > 0 else None
                    next_id = ids[index + 1] if index + 1 < len(ids) else None
                else:
                    position, prev_id, next_id = None, None, (ids[0] if ids else None)
                detail = store.get_unit(unit_id)
                ordered = store.ordered_pairs(app)
                return JSONResponse(dict(
                    id=unit_id,
                    verse=detail.verse,
                    sample_role=role,
                    status=detail.status,
                    position=position,
                    prev=prev_id,
                    next=next_id,
                    next_unclassified=next_unclassified(apps, unit_id),
                    progress=progress(app),
                    context=dict(before=app.text_before(), lemma=app.text(), after=app.text_after()),
                    readings=[reading.model_dump() for reading in detail.readings],
                    lacunose_witnesses=detail.lacunose_witnesses,
                    pairs=[pair_payload(app, pair, role) for pair in ordered if store.coverage_for(pair) is None],
                    rule_pairs=[pair_payload(app, pair, role) for pair in ordered if store.coverage_for(pair) is not None],
                    filters=filters,
                ))
        except AnnotationError as error:
            return error_response(error)

    def write_response(unit_id:str, pairs:list[tuple[str, str]]) -> JSONResponse:
        with store.lock:
            app = store.get_app(unit_id)
            role = store.sample_role(str(app))
            payload = [pair_payload(app, store.get_pair(app, active, passive), role) for active, passive in pairs]
            return JSONResponse(dict(pairs=payload, progress=progress(app), status=store.status(app)))

    async def label(request:Request):
        return await run_in_threadpool(label_sync, await request.json())

    def label_sync(data:dict):
        try:
            unit_id = data.get("unit_id", "")
            pairs = parse_pairs(data)
            expected = parse_expected(data)
            category = data.get("category")
            if category is None:
                store.clear_classifications(unit_id, pairs, expected=expected)
            else:
                store.set_classifications(
                    unit_id, pairs, category,
                    justification=data.get("justification") or "",
                    cert=data.get("cert") or "high",
                    expected=expected,
                )
            return write_response(unit_id, pairs)
        except AnnotationError as error:
            return error_response(error)

    async def certainty(request:Request):
        return await run_in_threadpool(certainty_sync, await request.json())

    def certainty_sync(data:dict):
        try:
            unit_id = data.get("unit_id", "")
            pairs = parse_pairs(data)
            store.set_certainty(unit_id, pairs, cert=data.get("cert") or "")
            return write_response(unit_id, pairs)
        except AnnotationError as error:
            return error_response(error)

    routes = [
        Route("/", index),
        Route("/api/config", config),
        Route("/api/queue", queue),
        Route("/api/unit/{unit_id:path}", unit),
        Route("/api/label", label, methods=["POST"]),
        Route("/api/certainty", certainty, methods=["POST"]),
        Mount("/static", StaticFiles(directory=STATIC), name="static"),
    ]
    return Starlette(routes=routes)
