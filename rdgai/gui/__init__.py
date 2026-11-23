"""
A browser GUI for annotating and reviewing transcriptional-change classifications.

It is a small Starlette app over the same ``AnnotationStore`` as the MCP server, so it can run inside the MCP
server's process (``rdgai serve-mcp --gui-port``) or on its own (``rdgai annotate``). Everything about the
categories comes from the TEI ``<interpGrp type="transcriptional">``; nothing is specific to one corpus.
"""
import re
from pathlib import Path

from pydantic import BaseModel
from starlette.applications import Starlette
from starlette.middleware import Middleware
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
    DefinitionsLockedError,
    ReadOnlyError,
    UnitNotFoundError,
)

from . import views
from .display import display_path, load_display, save_display, initials

STATIC = Path(__file__).parent / "static"
INVERSE_PREFIX = re.compile(r"^Inverse of (\S+) → (\S+):\s*")


def display_justification(text:str) -> str:
    """ Labels and suggestions saved for the reverse direction start 'Inverse of a → b: '; the GUI does not show that. """
    return INVERSE_PREFIX.sub("", text or "")

# Keys for the category buttons, in button order: 1-9, 0, then shift+1 ... shift+0.
KEYS = [str(digit) for digit in range(1, 10)] + ["0"] + [f"shift+{digit}" for digit in [*range(1, 10), 0]]


class Button(BaseModel):
    name: str
    short: str = ""
    description: str
    inverse: str
    group: str
    row: int
    column: int
    key: str | None


def short_labels(names:list[str]) -> dict[str, str]:
    """
    Short, distinct labels for category buttons in compact rows: the first letters of each part of the name
    (e.g. Minor_Omission -> 'Min·Omi', Orthography -> 'Orth'), lengthened where two would be the same.
    """
    def parts(name:str) -> list[str]:
        return [part for part in re.split(r"[_\-\s.]+", name) if part] or [name]

    def make(name:str, length:int) -> str:
        name_parts = parts(name)
        if len(name_parts) == 1:
            return name_parts[0][:length]
        return "·".join(part[:length] for part in name_parts)

    lengths = {name: (4 if len(parts(name)) == 1 else 3) for name in names}
    longest = max((len(name) for name in names), default=0)
    while True:
        labels = {name: make(name, lengths[name]) for name in names}
        seen = {}
        for name, label in labels.items():
            seen.setdefault(label, []).append(name)
        clashes = [group for group in seen.values() if len(group) > 1]
        if not clashes or all(lengths[name] > longest for group in clashes for name in group):
            return labels
        for group in clashes:
            for name in group:
                lengths[name] += 1


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
    shorts = short_labels([category.name for category in categories])
    return [
        Button(
            name=category.name,
            short=shorts[category.name],
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
    if isinstance(error, (ReadOnlyError, DefinitionsLockedError)):
        return JSONResponse({"error": str(error)}, status_code=403)
    if isinstance(error, UnitNotFoundError):
        return JSONResponse({"error": str(error)}, status_code=404)
    return JSONResponse({"error": str(error)}, status_code=400)


class CacheControl:
    """
    The API is never cached (another process, e.g. Claude through MCP, may change the file at any time);
    the page and its scripts are revalidated on every load.
    """
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        value = b"no-store" if scope["path"].startswith("/api/") else b"no-cache"

        async def send_with_header(message):
            if message["type"] == "http.response.start":
                headers = [(name, item) for name, item in message.get("headers", []) if name.lower() != b"cache-control"]
                message = {**message, "headers": headers + [(b"cache-control", value)]}
            await send(message)

        await self.app(scope, receive, send_with_header)


def file_version(*paths:Path) -> str:
    """ A token that changes whenever any of the files changes (rdgai replaces files atomically, so the inode changes). """
    parts = []
    for path in paths:
        try:
            stat = path.stat()
            parts.append(f"{stat.st_ino}-{stat.st_mtime_ns}-{stat.st_size}")
        except FileNotFoundError:
            parts.append("none")
    return "|".join(parts)


def create_app(store:AnnotationStore, group_by_prefix:str|None=None, compact_threshold:int=10) -> Starlette:
    """ Builds the GUI app over a store (which may be shared with an MCP server in the same process). """

    def queue_filters(request:Request) -> dict:
        params = request.query_params
        default_sample = "sampled" if store.load_sample() else "all"
        return dict(
            sample=params.get("sample") or default_sample,
            status=params.get("status") or "all",
            verse=params.get("verse") or "",
            category=params.get("category") or "",
            cert=params.get("cert") or "",
            source=params.get("source") or "",
            disagreement="1" if (params.get("disagreement") or "").lower() in views.TRUE_VALUES else "",
        )

    def queue_apps(filters:dict):
        return views.filter_queue(store, filters)

    def pair_payload(app, pair, role:str|None) -> dict:
        info = store.pair_info(pair)
        suggestions = []
        for suggestion in ([] if role == "test" else store.suggestions_for(app, pair)):
            match = INVERSE_PREFIX.match(suggestion.justification or "")
            payload = suggestion.model_dump()
            payload["justification"] = display_justification(suggestion.justification)
            # text written for the other direction would read as contradicting the category shown: say which direction
            payload["written_for"] = [match[1], match[2]] if match else None
            suggestions.append(payload)
        if info.provenance is not None:
            info.provenance.justification = display_justification(info.provenance.justification)
        # the reason box holds the annotator's own reason only: it starts empty (suggestions are shown separately)
        reason = info.provenance.justification if info.provenance is not None else ""
        rule = dict(rule=info.rule) if info.rule is not None else None
        translations = store.translations_for(app)
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
            change=info.change.model_dump() if info.change else None,
            warning=info.warning,
            suggestions=suggestions,
            reason=reason,
            first_translation=translations.get(pair.active.n),
            second_translation=translations.get(pair.passive.n),
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

    def current_version() -> str:
        return file_version(store.path, store.sample_path, store.translations_path)

    def version(request:Request):
        return JSONResponse(dict(version=current_version()))

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
                classifier_instructions=definitions.classifier_instructions,
                constraints=definitions.constraints,
                direction_convention=definitions.direction_convention,
                definitions_hash=definitions.definitions_hash,
                buttons=[button.model_dump() for button in buttons],
                certainty=list(CERTAINTY_VALUES),
                annotator=store.annotator,
                annotator_name=store.annotator_full_name(),
                profile=profile(),
                title=load_display(display_path(store.path)).get("title") or None,
                read_only=store.read_only,
                definitions_editable=not store.read_only and not store.lock_definitions,
                evaluation=store.evaluation,
                compact_threshold=compact_threshold,
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

    def sample_progress(request:Request):
        """ Labelled pairs and complete units per role of the sample (example, test) and in the whole file, for the sidebar's bars. """
        with store.lock:
            store.refresh()
            if store.load_sample() is None:
                return JSONResponse(dict(sample=False, file=store.file_progress()))
            result = dict(sample=True, file=store.file_progress())
            for role in ("example", "test"):
                counts = dict(pairs_done=0, pairs_total=0, units_done=0, units_total=0)
                for app in store.filter_units(sample=role):
                    pairs = store.work_pairs(app)
                    counts["pairs_done"] += sum(store.is_classified(pair) for pair in pairs)
                    counts["pairs_total"] += len(pairs)
                    counts["units_done"] += store.status(app) in ("classified", "nothing_to_classify")
                    counts["units_total"] += 1
                result[role] = counts
            return JSONResponse(result)

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
                ordered = store.reading_ordered_pairs(app)
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
                    translations=detail.translations,
                    pairs=[pair_payload(app, pair, role) for pair in ordered if store.coverage_for(pair) is None],
                    rule_pairs=[pair_payload(app, pair, role) for pair in ordered if store.coverage_for(pair) is not None],
                    filters=filters,
                    version=current_version(),
                ))
        except AnnotationError as error:
            return error_response(error)

    def write_payload(unit_id:str, pairs:list[tuple[str, str]]) -> dict:
        with store.lock:
            app = store.get_app(unit_id)
            role = store.sample_role(str(app))
            payload = [pair_payload(app, store.get_pair(app, active, passive), role) for active, passive in pairs]
            return dict(pairs=payload, progress=progress(app), status=store.status(app))

    def write_response(unit_id:str, pairs:list[tuple[str, str]]) -> JSONResponse:
        return JSONResponse(write_payload(unit_id, pairs))

    async def save_suggestions(request:Request):
        data = await request.json()

        def run():
            try:
                unit_id = data.get("unit_id", "")
                pairs = parse_pairs(data) if data.get("pairs") else []
                result = store.save_suggestions(unit_id, pairs, data.get("kind", ""))
                return JSONResponse(dict(
                    write_payload(unit_id, result.written),
                    written=result.written,
                    skipped=[item.model_dump() for item in result.skipped],
                ))
            except AnnotationError as error:
                return error_response(error)

        return await run_in_threadpool(run)

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
                    justification_source=data.get("justification_source") or None,
                )
            return write_response(unit_id, pairs)
        except AnnotationError as error:
            return error_response(error)

    def profile() -> dict:
        if not store.annotator:
            return dict(id=None, name=None, short_name=None, editable=False)
        name = store.annotator_full_name()
        saved = (load_display(display_path(store.path)).get("annotators") or {}).get(store.annotator) or {}
        return dict(
            id=store.annotator,
            name=name,
            short_name=saved.get("short_name") or initials(name) or store.annotator,
            editable=not store.read_only,
        )

    async def edit_profile(request:Request):
        data = await request.json()

        def run():
            try:
                if store.read_only:
                    raise ReadOnlyError("This server is read-only.")
                if ("name" in data or "short_name" in data) and not store.annotator:
                    raise AnnotationError("No annotator is set on this server (start it with --annotator, e.g. --annotator RT).")
                if (data.get("name") or "").strip() and data["name"].strip() != store.annotator_full_name():
                    store.set_annotator_name(data["name"])
                path = display_path(store.path)
                display = load_display(path)
                if "title" in data:
                    title = (data.get("title") or "").strip()
                    if title:
                        display["title"] = title
                    else:
                        display.pop("title", None)
                if "short_name" in data:
                    annotators = display.setdefault("annotators", {})
                    short_name = (data.get("short_name") or "").strip()
                    if short_name:
                        annotators.setdefault(store.annotator, {})["short_name"] = short_name
                    else:
                        annotators.pop(store.annotator, None)
                    if not annotators:
                        display.pop("annotators")
                save_display(display, path)
                return JSONResponse(dict(profile=profile(), title=display.get("title")))
            except AnnotationError as error:
                return error_response(error)
        return await run_in_threadpool(run)

    async def viewing(request:Request):
        data = await request.json()

        def run():
            try:
                store.set_viewing(data.get("unit_id", ""))
                return JSONResponse(dict(unit_id=store.viewing().unit_id))
            except AnnotationError as error:
                return error_response(error)
        return await run_in_threadpool(run)

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

    def categories(request:Request):
        with store.lock:
            return JSONResponse(store.category_counts().model_dump())

    def category(request:Request):
        try:
            with store.lock:
                store.refresh()
                params = request.query_params
                pairs = views.category_pairs(store, request.path_params["name"], source=params.get("source") or "all")
                offset = max(int(params.get("offset") or 0), 0)
                limit = max(min(int(params.get("limit") or 100), 500), 1)
                roles = store.sample_roles()
                rows = []
                for pair in pairs[offset:offset + limit]:
                    app = pair.app
                    role = roles.get(str(app))
                    rows.append(dict(
                        pair_payload(app, pair, role),
                        unit_id=str(app),
                        sample_role=role,
                        context=dict(before=app.text_before(), lemma=app.text(), after=app.text_after()),
                    ))
                return JSONResponse(dict(
                    category=store.get_relation_type(request.path_params["name"]).name,
                    total=len(pairs), offset=offset, limit=limit, pairs=rows,
                    version=current_version(),
                ))
        except AnnotationError as error:
            return error_response(error)
        except ValueError:
            return JSONResponse({"error": "offset and limit must be numbers."}, status_code=400)

    def agreement(request:Request):
        try:
            with store.lock:
                store.refresh()
                params = request.query_params
                return JSONResponse(views.agreement(
                    store, scope=params.get("scope") or None, source=params.get("source") or None,
                    display_justification=display_justification,
                ))
        except AnnotationError as error:
            return error_response(error)

    def definitions_write(method):
        async def endpoint(request:Request):
            data = await request.json()

            def run():
                try:
                    return JSONResponse(method(data).model_dump())
                except AnnotationError as error:
                    return error_response(error)
            return await run_in_threadpool(run)
        return endpoint

    update_category = definitions_write(lambda data: store.update_category(
        data.get("name", ""), description=data.get("description"), new_name=data.get("new_name") or None))
    update_guidance = definitions_write(lambda data: store.update_guidance(data.get("guidance", "")))
    update_classifier = definitions_write(lambda data: store.update_classifier_instructions(data.get("instructions", "")))
    add_category = definitions_write(lambda data: store.add_category(
        data.get("name", ""), data.get("description", ""),
        inverse_name=data.get("inverse_name") or None, inverse_description=data.get("inverse_description") or ""))
    remove_category = definitions_write(lambda data: store.remove_category(data.get("name", ""), reassign_to=data.get("reassign_to") or None))

    routes = [
        Route("/", index),
        Route("/api/config", config),
        Route("/api/queue", queue),
        Route("/api/sample-progress", sample_progress),
        Route("/api/version", version),
        Route("/api/unit/{unit_id:path}", unit),
        Route("/api/label", label, methods=["POST"]),
        Route("/api/certainty", certainty, methods=["POST"]),
        Route("/api/save-suggestions", save_suggestions, methods=["POST"]),
        Route("/api/viewing", viewing, methods=["POST"]),
        Route("/api/profile", edit_profile, methods=["POST"]),
        Route("/api/categories", categories),
        Route("/api/category/{name}", category),
        Route("/api/agreement", agreement),
        Route("/api/definitions/category", update_category, methods=["POST"]),
        Route("/api/definitions/guidance", update_guidance, methods=["POST"]),
        Route("/api/definitions/classifier", update_classifier, methods=["POST"]),
        Route("/api/definitions/add", add_category, methods=["POST"]),
        Route("/api/definitions/remove", remove_category, methods=["POST"]),
        Mount("/static", StaticFiles(directory=STATIC), name="static"),
    ]
    return Starlette(routes=routes, middleware=[Middleware(CacheControl)])
