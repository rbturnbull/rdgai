"""
An MCP server for classifying transitions between variant readings in a TEI critical apparatus.

Run it with ``rdgai serve-mcp FILE`` (stdio) or ``rdgai serve-mcp FILE --transport http``.
"""
from contextlib import contextmanager
from typing import Annotated, Literal

from pydantic import Field
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations

from .annotation import (
    AnnotationError,
    AnnotationStore,
    CategoryCounts,
    CategoryPairList,
    Definitions,
    UnitList,
    UnitDetail,
    UnitStatus,
    WriteResult,
)

INSTRUCTIONS = """\
This server gives access to a TEI critical apparatus whose variant readings are being classified into
transcriptional change categories.

Workflow:
1. Call get_definitions first and follow its guidance (including any decision order) and direction convention.
2. Use list_units to find units (e.g. status='unclassified', or by verse such as 'Mark.1.2').
3. Use get_unit to read a unit: its verse context, its readings and every pair of substantive readings.
   The only context is the verse given.
4. Use set_classification to record a category for a pair. Its inverse is set automatically, so classify
   each unordered pair once. A new classification replaces the old one.

When working with a human annotator, propose categories and only call set_classification for the
labels the annotator has agreed to, with the certainty they choose.
"""


@contextmanager
def tool_errors():
    """ Reports expected errors (unknown unit, reading or category, read-only, ...) to the client. """
    try:
        yield
    except AnnotationError as error:
        raise ToolError(str(error)) from error


def build_server(store:AnnotationStore) -> MCPServer:
    server = MCPServer(name="rdgai", instructions=INSTRUCTIONS)
    read_only = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
    write = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False)

    @server.tool(annotations=read_only)
    def get_definitions() -> Definitions:
        """
        Returns the transcriptional change categories with their definitions and inverses, the guidance
        given with them (e.g. the decision order) and the direction convention for active/passive readings.
        """
        with tool_errors():
            return store.definitions()

    @server.tool(annotations=read_only)
    def list_units(
        status: Annotated[UnitStatus, Field(description="Filter by classification status of the unit's pairs.")] = "all",
        verse: Annotated[str, Field(description="Only units in this verse or chapter, e.g. 'Mark.1.2' or 'Mark.1'.")] = "",
        offset: Annotated[int, Field(ge=0)] = 0,
        limit: Annotated[int, Field(ge=1, le=500)] = 50,
    ) -> UnitList:
        """
        Lists variation units with their number of readings, pairs to classify and pairs classified.
        Units with no substantive pairs have status 'nothing_to_classify'.
        """
        with tool_errors():
            return store.list_units(status=status, verse=verse, offset=offset, limit=limit)

    @server.tool(annotations=read_only)
    def get_unit(
        unit_id: Annotated[str, Field(description="The variation unit's id, e.g. 'Mark.1.1.12-16'.")],
    ) -> UnitDetail:
        """
        Returns a variation unit: the verse with the unit marked ⸂…⸃, the readings (OMIT is an omission),
        lacunose witnesses, and each unordered pair of substantive readings with its current category
        and provenance (who classified it, certainty and justification).
        """
        with tool_errors():
            return store.get_unit(unit_id)

    @server.tool(annotations=read_only)
    def category_counts() -> CategoryCounts:
        """
        Returns how many pairs have each category (counting each direction separately), split into
        human (certain) and pipeline (tentative) classifications, and how many pairs are classified overall.
        """
        with tool_errors():
            return store.category_counts()

    @server.tool(annotations=read_only)
    def list_category(
        category: Annotated[str, Field(description="The category name.")],
        source: Annotated[Literal["all", "human", "pipeline"], Field(description="Only pairs classified by a human or by the pipeline.")] = "all",
        offset: Annotated[int, Field(ge=0)] = 0,
        limit: Annotated[int, Field(ge=1, le=500)] = 50,
    ) -> CategoryPairList:
        """
        Lists the pairs classified with a category (in the direction active → passive), with their verse
        context and provenance. Useful for checking consistency and finding precedents.
        """
        with tool_errors():
            return store.list_category(category, source=source, offset=offset, limit=limit)

    if not store.read_only:
        @server.tool(annotations=write)
        def set_classification(
            unit_id: Annotated[str, Field(description="The variation unit's id.")],
            active: Annotated[str, Field(description="Id of the active (first) reading.")],
            passive: Annotated[str, Field(description="Id of the passive (second) reading.")],
            category: Annotated[str, Field(description="Category for the change from the active to the passive reading.")],
            justification: Annotated[str, Field(description="A short reason for the decision.")] = "",
            cert: Annotated[Literal["high", "medium", "low"], Field(description="The annotator's certainty.")] = "high",
        ) -> WriteResult:
            """
            Sets the category for the change from the active reading to the passive reading, replacing any
            existing category. The inverse category is set on the reverse pair, and the file is saved.
            Recorded as the human annotator's decision.
            """
            with tool_errors():
                return store.set_classification(unit_id, active, passive, category, justification=justification, cert=cert)

        @server.tool(annotations=write)
        def clear_classification(
            unit_id: Annotated[str, Field(description="The variation unit's id.")],
            active: Annotated[str, Field(description="Id of the active (first) reading.")],
            passive: Annotated[str, Field(description="Id of the passive (second) reading.")],
        ) -> WriteResult:
            """ Removes the classification of a pair and of its reverse pair, and saves the file. """
            with tool_errors():
                return store.clear_classification(unit_id, active, passive)

    return server
