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
    RulesReport,
    DefinitionsChange,
    SampleFilter,
    SampleInfo,
    SuggestionList,
    SuggestionResult,
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

Pairs formed by typed subreadings (e.g. orthographic readings 'ao', 'ao1' of reading 'a') are labelled
automatically by subreading rules (source 'rule'; see apply_rules) and are not counted as work: do not
classify them unless the annotator wants to override one.

Do not suggest classifications for the sample's test units (they are refused unless the annotator explicitly asks):
suggestions there would leak into the evaluation.

Annotating the sample: get_sample shows progress; list_units(sample='sampled', status='unclassified')
gives the next units in document order. Work through one unit at a time.

When working with a human annotator, propose a category with a brief reason for each pair of the unit,
and only call set_classification for the labels the annotator has agreed to, with the certainty they
choose (high, medium or low). Record the annotator's reasoning as the justification.
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
        sample: Annotated[SampleFilter, Field(description="Only units in the sample ('sampled'), in its example or test set, or not in the sample.")] = "all",
        offset: Annotated[int, Field(ge=0)] = 0,
        limit: Annotated[int, Field(ge=1, le=500)] = 50,
    ) -> UnitList:
        """
        Lists variation units with their number of readings, pairs to classify and pairs classified.
        Units with no substantive pairs have status 'nothing_to_classify'.
        """
        with tool_errors():
            return store.list_units(status=status, verse=verse, sample=sample, offset=offset, limit=limit)

    @server.tool(annotations=read_only)
    def get_unit(
        unit_id: Annotated[str, Field(description="The variation unit's id, e.g. 'Mark.1.1.12-16'.")],
        pair_order: Annotated[Literal["document", "attestation"], Field(
            description="'document': pairs in document order of their readings. 'attestation': each pair oriented "
                        "with the better-attested reading first, as the annotation GUI shows them."
        )] = "document",
    ) -> UnitDetail:
        """
        Returns a variation unit: the verse with the unit marked ⸂…⸃, the readings (OMIT is an omission;
        editorial notes found in a reading's text are listed separately and are not part of the reading),
        lacunose witnesses, and each unordered pair of substantive readings with its current category
        and provenance (who classified it, when, certainty, justification and definitions version).
        """
        with tool_errors():
            return store.get_unit(unit_id, pair_order=pair_order)

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

    @server.tool(annotations=read_only)
    def get_sample() -> SampleInfo:
        """
        Returns the random sample of units (if one has been drawn): its seed and settings, each sampled
        unit with its role (example or test) and status, and progress overall and by role.
        To annotate the sample in order, use list_units with sample='sampled' and status='unclassified'.
        """
        with tool_errors():
            return store.sample_info()

    @server.tool(annotations=read_only)
    def get_suggestions(
        unit_id: Annotated[str | None, Field(description="Only suggestions for this unit.")] = None,
        active: Annotated[str | None, Field(description="With passive: only suggestions for this pair, in this direction.")] = None,
        passive: Annotated[str | None, Field(description="With active: only suggestions for this pair, in this direction.")] = None,
        source: Annotated[str | None, Field(description="Only suggestions from this source, e.g. 'claude' or 'rdgai:<model>'.")] = None,
    ) -> SuggestionList:
        """
        Returns suggested classifications (kept separately from labels; they are not classifications).
        Without a pair, each suggestion is given once, for the pair in document order.
        """
        with tool_errors():
            return SuggestionList(suggestions=store.get_suggestions(unit_id, active, passive, source))

    if not store.read_only and not store.lock_definitions:
        @server.tool(annotations=write)
        def update_category(
            name: Annotated[str, Field(description="The category to change.")],
            description: Annotated[str | None, Field(description="The new definition text (omit to keep it).")] = None,
            new_name: Annotated[str | None, Field(description="A new name (omit to keep it). Classifications using the category are updated.")] = None,
        ) -> DefinitionsChange:
            """
            Changes a category's definition and/or renames it. Renaming also updates its inverse's link and
            every classification that uses it. Only make changes the annotator has asked for.
            """
            with tool_errors():
                return store.update_category(name, description=description, new_name=new_name)

        @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False))
        def add_category(
            name: Annotated[str, Field(description="Name of the new category (a valid xml:id, e.g. 'Harmonization').")],
            description: Annotated[str, Field(description="The definition of the new category.")],
            inverse_name: Annotated[str | None, Field(description="For a reciprocal pair (like Addition/Omission), the name of the new inverse category.")] = None,
            inverse_description: Annotated[str, Field(description="The definition of the inverse category.")] = "",
        ) -> DefinitionsChange:
            """
            Adds a category. Without inverse_name it is its own inverse (like Transposition); with inverse_name
            a reciprocal pair is added. Only make changes the annotator has asked for.
            """
            with tool_errors():
                return store.add_category(name, description, inverse_name=inverse_name, inverse_description=inverse_description)

        @server.tool(annotations=write)
        def remove_category(
            name: Annotated[str, Field(description="The category to remove (its inverse is removed too).")],
            reassign_to: Annotated[str | None, Field(description="Category to move existing classifications to; required if the category is in use.")] = None,
        ) -> DefinitionsChange:
            """
            Removes a category and its inverse. If pairs are classified with them, reassign_to must give a
            category with a matching inverse structure; their labels move to it with their provenance kept.
            Only make changes the annotator has asked for.
            """
            with tool_errors():
                return store.remove_category(name, reassign_to=reassign_to)

        @server.tool(annotations=write)
        def update_guidance(
            guidance: Annotated[str, Field(description="The full new guidance text (e.g. the decision order). Replaces the old guidance.")],
        ) -> DefinitionsChange:
            """
            Replaces the guidance kept in the XML comment before the category definitions (e.g. the decision
            order). Read the current guidance with get_definitions first. Only make changes the annotator has asked for.
            """
            with tool_errors():
                return store.update_guidance(guidance)

    if not store.read_only and not store.evaluation:
        # not in evaluation mode: a new sample would change the test units, and rules would change their labels
        @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False))
        def draw_sample(
            size: Annotated[int, Field(ge=1, description="Number of units to sample.")],
            seed: Annotated[int, Field(description="Random seed; the same seed and settings give the same sample.")],
            test_fraction: Annotated[float, Field(ge=0.0, le=1.0, description="Fraction of the sampled units held out as test units.")] = 0.5,
            max_pairs: Annotated[int | None, Field(ge=1, description="Exclude units with more pairs than this from the sample.")] = None,
            overwrite: Annotated[bool, Field(description="Replace an existing sample. Only do this if the user explicitly asks.")] = False,
        ) -> SampleInfo:
            """
            Draws a reproducible random sample of variation units (those with at least one pair to classify)
            and splits it into example and test units. The sample is saved in a sidecar YAML file.
            Refuses to replace an existing sample unless overwrite is true.
            """
            with tool_errors():
                return store.draw_sample(size=size, seed=seed, test_fraction=test_fraction, max_pairs=max_pairs, overwrite=overwrite)

        @server.tool(annotations=write)
        def apply_rules(
            rules: Annotated[dict[str, str] | None, Field(description="Reading type -> category, e.g. {'orthographic': 'Orthography'}. Replaces the rules stored in the file. Omit to use the stored rules.")] = None,
            suffixes: Annotated[dict[str, str] | None, Field(description="Reading type -> id suffix, e.g. {'orthographic': 'o'} ('ao2' is a subreading of 'a'). Defaults: orthographic 'o', defective 'f'.")] = None,
            dry_run: Annotated[bool, Field(description="Report what would change without saving.")] = False,
        ) -> RulesReport:
            """
            Labels the pairs formed by typed subreadings (e.g. orthographic readings 'ao', 'ao1' of reading 'a'):
            subreading-parent and sibling subreadings get the mapped category; any other pair with a subreading copies
            the label of its parents' pair (waiting if that is unlabelled). Human labels are never changed; those that
            disagree are reported as conflicts. The rules are re-applied automatically whenever a label in a unit changes.
            """
            from .rules import build_rules
            with tool_errors():
                try:
                    rule_list = build_rules(store.rules, rules, suffixes)
                except ValueError as error:
                    raise AnnotationError(str(error))
                return store.apply_rules(rule_list, dry_run=dry_run)

    if not store.read_only:
        @server.tool(annotations=write)
        def suggest_classification(
            unit_id: Annotated[str, Field(description="The variation unit's id.")],
            active: Annotated[str, Field(description="Id of the active (first) reading.")],
            passive: Annotated[str, Field(description="Id of the passive (second) reading.")],
            category: Annotated[str, Field(description="Suggested category for the change from the active to the passive reading.")],
            justification: Annotated[str, Field(description="A short reason for the suggestion.")] = "",
            source: Annotated[str, Field(description="Who is suggesting: 'claude', or e.g. 'rdgai:<model>'.")] = "claude",
            allow_test_unit: Annotated[bool, Field(description="Suggestions on the sample's test units are refused, because they would leak into the evaluation. Set only if the annotator explicitly asks.")] = False,
        ) -> SuggestionResult:
            """
            Records a suggested category for a pair without classifying it. Suggestions are shown to the
            annotator in the GUI (in bold, with the justification) and are stored separately from labels,
            so they never affect the phylogenetic analysis. A new suggestion from the same source replaces
            the previous one for the pair. Use this to propose labels for the annotator to review.
            """
            with tool_errors():
                return store.suggest_classification(
                    unit_id, active, passive, category, justification=justification, source=source, allow_test_unit=allow_test_unit,
                )

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
