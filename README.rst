================================================================
rdgai
================================================================

.. start-badges

.. image:: https://raw.githubusercontent.com/rbturnbull/rdgai/refs/heads/main/docs/img/rdgai-banner.svg
    :alt: rdgai

|pypi badge| |testing badge| |coverage badge| |docs badge| |black badge|

.. |pypi badge| image:: https://img.shields.io/pypi/v/rdgai?color=blue
    :target: https://pypi.org/project/rdgai/

.. |testing badge| image:: https://github.com/rbturnbull/rdgai/actions/workflows/testing.yml/badge.svg
    :target: https://github.com/rbturnbull/rdgai/actions

.. |docs badge| image:: https://github.com/rbturnbull/rdgai/actions/workflows/docs.yml/badge.svg
    :target: https://rbturnbull.github.io/rdgai
    
.. |black badge| image:: https://img.shields.io/badge/code%20style-black-000000.svg
    :target: https://github.com/psf/black
    
.. |coverage badge| image:: https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/rbturnbull/1cf1aae1e72f85de97c7f79bb41f3d76/raw/coverage-badge.json
    :target: https://rbturnbull.github.io/rdgai/coverage/

.. |doi badge| image:: https://img.shields.io/badge/DOI-10.48550%2FarXiv.2511.13801-blue
   :target: https://doi.org/10.48550/arXiv.2511.13801


Rdgai facilitates the use of LLMs for classifying transitions between variant readings in a Text Encoding Initiative (TEI) XML file containing a critical apparatus. 
It enables users to define classification categories, manually annotate changes, and use an LLM to automate the classification process.
The TEI XML can then be used for phylogenetic analysis of textual traditions using `teiphy <https://github.com/jjmccollum/teiphy>`_.

Background information about the use of classifying variants in this way can be found on the `Why use Rdgai? <https://rbturnbull.github.io/rdgai/docs/why>`_ documentation.

.. end-badges

Documentation is available at `https://rbturnbull.github.io/rdgai <https://rbturnbull.github.io/rdgai>`_.

.. start-quickstart

Installation
==================================

Install using pip:

.. code-block:: bash

    pip install rdgai

Or install directly from the repository:

.. code-block:: bash

    pip install git+https://github.com/rbturnbull/rdgai.git


Usage
==================================

See all the options with the command:

.. code-block:: bash

    rdgai --help


Preparation
==================================

You first need to prepare a `TEI XML <https://teibyexample.org/exist/tutorials/>`_ file with a `critical apparatus <https://tei-c.org/release/doc/tei-p5-doc/en/html/TC.html>`_.

Define categories in the TEI XML header under ``<interpGrp type="transcriptional">``. For example:

.. code-block:: xml

    <interpGrp type="transcriptional">
        <interp xml:id="Addition" corresp="#Omission">An addition of a word or words.</interp>
        <interp xml:id="Omission" corresp="#Addition">An omission of a word or words.</interp>
        <interp xml:id="Substituion">A substitution of a word or words.</interp>
    </interpGrp>

Then annotate pairs of readings in the browser-based GUI (see `Annotation GUI`_ below), where ``RT`` is your
``xml:id`` as the annotator:

.. code-block:: bash

    rdgai annotate apparatus.xml --annotator RT --open

or with Claude through the MCP server (see `MCP server`_ below). Or export classifications to Excel for collaborative editing:

.. code-block:: bash

    rdgai export apparatus.xml reading-pairs.xlsx

Edit in Excel and re-import with:

.. code-block:: bash

    rdgai import-classifications apparatus.xml reading-pairs.xlsx output.xml

More information about preparing the TEI XML file can be found in the `Preparation <https://rbturnbull.github.io/rdgai/docs/preparation>`_ documentation.

Evaluation
==================================

The accuracy of Rdgai depends on the text, the categories and their definitions, and the model used, so it needs to be
evaluated on each document. Draw a sample of units, annotate them, and hold half of them out as test units:

.. code-block:: bash

    rdgai sample apparatus.xml -n 200 --seed 42

Then evaluate the pipeline against your labels on the test units:

.. code-block:: bash

    rdgai evaluate apparatus.xml --output-dir evaluation --model anthropic:claude-opus-5-5 --rounds 3

This runs the pipeline on a copy of the file with the test units' labels removed (so they cannot leak into the
prompts), then compares the draft and the reviewed predictions with your labels. ``evaluation/report.html`` shows the
accuracy, per-category precision, recall and F1 (categories with fewer than 10 test pairs are marked as indicative
only), the confusion matrix, every test pair (correct or not) and the revisions made in review. The original file is
not modified.

To compare setups without touching the test units, cross-validate on the example units with ``--fold`` (1 to
``--folds``, 5 by default); the folds are saved and reused. The draft model names a second category when it is unsure,
and ``--expert-model`` sends just those pairs to a stronger model (``--expert-mode after``, the default) or lets the
draft model ask it while it works (``--expert-mode tool``). ``--batch`` drafts through Message Batches at half price:

.. code-block:: bash

    rdgai evaluate apparatus.xml --output-dir cv/f1 --fold 1 --model anthropic:claude-haiku-5-5 --expert-model anthropic:claude-opus-5-5

Classification
==================================

The pipeline uses `pydantic-ai <https://ai.pydantic.dev>`_, so any model it supports can be used
(``anthropic:claude-opus-5-5`` by default; ``openai:…``, local models, …). Agents read through the rdgai MCP server
(definitions, units, similar labelled pairs) and give a checked, structured decision for every pair, which is written
with ``resp="#rdgai"``, the agent's confidence as ``@cert`` and a ``<change>`` recording the run and model.
Human labels are never changed. First estimate the cost (no model is called):

.. code-block:: bash

    rdgai estimate apparatus.xml --model anthropic:claude-opus-5-5

Then run the draft pass, which classifies every pair still to classify (pairs covered by subreading rules are labelled
by rule instead). It is resumable: run it again to continue, and calibrate the estimate with ``--limit-units 20`` first:

.. code-block:: bash

    rdgai draft apparatus.xml --model anthropic:claude-opus-5-5

With an Anthropic model, ``--batch`` sends the requests through the Message Batches API at half the price, with results
within hours rather than minutes (``rdgai estimate`` shows both prices). The batch's state is kept in
``apparatus.batch-state.json``, so if the command is interrupted, running it again collects the submitted batch instead
of paying again. For example, Opus labelled all 42,695 unlabelled pairs of the ECM's Mark (5,426 units) in about 84
minutes for $131:

.. code-block:: bash

    rdgai draft apparatus.xml --model anthropic:claude-opus-5-5 --batch

Then review the pipeline's labels one category at a time, applying the revisions (logged to ``apparatus.revisions.jsonl``):

.. code-block:: bash

    rdgai review apparatus.xml --rounds 3

``rdgai run`` does all three (rules, draft, review). Check the results in the annotation GUI, where the pipeline's
labels are shown with their source and can be corrected.

More information about making automated classifications using Rdgai can be found in the `Classification <https://rbturnbull.github.io/rdgai/docs/classification>`_ documentation.

.. end-quickstart


MCP server
==================================

Rdgai can serve a TEI XML file over the `Model Context Protocol <https://modelcontextprotocol.io>`_ so that an AI assistant
(for example Claude) can read the category definitions, browse variation units and record classifications with you:

.. code-block:: bash

    rdgai serve-mcp doc.xml --annotator RT --annotator-name "Your Name"

``--annotator`` is the ``xml:id`` of the person responsible for the classifications set through the server.
A ``<respStmt>`` with that id is added to the TEI header. Use ``--read-only`` to serve a file without allowing changes.
Readings typed ``defective`` are not classified; orthographic readings are classified like any other reading.
Change this with ``--ignore-type`` (repeatable), e.g. ``--ignore-type defective --ignore-type orthographic``.
Use the same reading types with teiphy's ``-t`` option (e.g. ``teiphy -t defective``) so that the analysis matches what was classified.
Text in parentheses inside a reading (e.g. ECM's ``θεου (cf. 24b)`` or ``om (cf. 27c)``) is treated as an editorial note, not as
reading text; ``om`` on its own marks an omission. Cross-references such as ``cf. 8b`` or ``cf. 3/4-6`` are resolved to unit ids
where possible. Use ``--no-reading-notes`` if parentheses are part of the reading text in your apparatus.

Writes take an advisory lock on ``.doc.xml.lock`` next to the TEI file, and every write reloads the file first if another
process has changed it, so two rdgai processes (e.g. the MCP server and the annotation GUI) never overwrite each other's work.

The file is saved after every change. To add the server to Claude Code:

.. code-block:: bash

    claude mcp add rdgai -- rdgai serve-mcp /path/to/doc.xml --annotator RT

To serve over streamable HTTP instead of stdio (e.g. for a remote connector), use ``--transport http --port 8000``;
the endpoint is ``http://HOST:PORT/mcp``.

The tools are:

- ``get_definitions``: the categories, their inverses, the guidance in the XML comment before the ``<interpGrp>`` (e.g. a decision order) and the direction convention.
- ``list_units``: variation units, filtered by status (``unclassified``, ``partial``, ``classified``) or verse.
- ``get_unit``: a unit's verse context, readings (with any notes) and every pair of substantive readings with its classification
  and provenance. ``pair_order="attestation"`` orients each pair with the better-attested reading first.
- ``set_classification``: sets the category of a pair (replacing any previous one) and the inverse category on the reverse pair.
- ``clear_classification``: removes the classification of a pair and its reverse.
- ``suggest_classification`` and ``get_suggestions``: record and read suggested categories (e.g. from Claude or the pipeline).
  Suggestions are kept in ``<listRelation type="suggestion">``, which teiphy ignores, so they are never classifications.
- ``category_counts`` and ``list_category``: how many pairs have each category, and the pairs in a category with their provenance.
- ``update_category``, ``add_category``, ``remove_category`` and ``update_guidance``: edit the definitions. Renaming a category updates
  its inverse's ``@corresp`` and every classification that uses it; reciprocal categories are added and removed together; a category
  in use can only be removed by reassigning its classifications. These tools are not offered with ``--read-only`` or ``--lock-definitions``.
  ``get_definitions`` reports a hash of the definitions so that results can be tied to the exact definitions used.
- ``set_category_constraints``: declares what a category requires of the computed change (``adds-only``, ``omits-only``,
  ``keeps-word-count``, ``not-only-adds-or-omits``), or ``rules-only`` for a category only the subreading rules assign.
  When the pipeline assigns a category whose constraint the change breaks, it asks the model once to reconsider.
- ``draw_sample`` and ``get_sample``: a reproducible random sample of units, split into example and test units, and progress in annotating it.

To draw a sample of 200 units, half of them held out for testing:

.. code-block:: bash

    rdgai sample doc.xml -n 200 --seed 42

The sample is saved to ``doc.sample.yaml`` next to the TEI file (the TEI file is not changed).
Use ``--max-pairs`` to leave out units with very many readings. ``list_units`` with ``sample="sampled"`` and
``status="unclassified"`` lists the sampled units still to annotate.

Suggestions on the sample's test units are refused (pass ``allow_test_unit`` to override), since they would leak
into the evaluation. With ``--evaluation`` (on ``serve-mcp`` or ``annotate``), no tool or GUI view reveals the labels
or suggestions of test units: they are reported as unlabelled, and their rule labels are
hidden too. Test units cannot be changed (the refusal is the same whether a pair is labelled or not), the definitions
are locked (their hash is reported by ``get_definitions``), and rules cannot be applied or the sample redrawn.
The labels stay in the file for evaluation.

Classifications are stored where teiphy reads them, with their provenance:

.. code-block:: xml

    <app xml:id="Mark.1.1.12-16">
      ...
      <note>
        <listRelation type="transcriptional">
          <relation active="a" passive="b" ana="#Minor_Omission" resp="#RT" cert="high" change="#defs-79e9afa6c1d2">
            <desc type="justification">Omits the article.<date when="2026-11-13T04:01:37Z"/></desc>
          </relation>
          <relation active="b" passive="a" ana="#Minor_Addition" resp="#RT" cert="high" change="#defs-79e9afa6c1d2">
            <desc type="justification">Inverse of a → b: Omits the article.<date when="2026-11-13T04:01:37Z"/></desc>
          </relation>
        </listRelation>
        <listRelation type="suggestion">
          <relation active="a" passive="b" ana="#Minor_Omission" resp="#claude" change="#defs-79e9afa6c1d2">
            <desc type="justification">The article is dropped.<date when="2026-11-13T03:58:12Z"/></desc>
          </relation>
          ...
        </listRelation>
      </note>
    </app>

Each label records when it was saved (the ``<date>``) and the version of the category definitions it was made under:
``@change`` points to a ``<change type="definitions">`` in the ``<revisionDesc>`` holding the definitions' SHA-256 hash.
Classifications made by an automated run have ``resp="#rdgai"`` and also point to a ``<change>`` recording when the run
happened and which model it used.


Subreading rules
==================================

ECM apparatuses type some readings as subreadings of another: e.g. orthographic readings ``ao``, ``ao1``, ``ao2`` are
spellings of reading ``a``. If these readings are kept in the analysis (no ``-t orthographic`` in teiphy), the pairs
they form can be labelled by rule, with no LLM call and no human decision:

1. subreading ↔ its parent (``a``–``ao``): the mapped category, e.g. Orthography;
2. two subreadings of the same parent and type (``ao1``–``ao2``): the mapped category.

A subreading only determines its pair with its own parent (and its siblings). Any other pair with a subreading
(``ao``–``b``, ``ao``–``bo``) is an ordinary pair, annotated by a human or classified by the pipeline like any other.

.. code-block:: bash

    rdgai apply-rules doc.xml --rule orthographic=Orthography --inplace      # or --dry-run, or -o out.xml

A subreading's parent is its id without the suffix (``o`` for orthographic, ``f`` for defective; change with
``--suffix orthographic=o``) and any digits after it. Readings whose parent cannot be found, and subreadings of
subreadings, are reported and skipped. Human labels are never changed: those that disagree with a rule are reported
as conflicts. Pipeline labels on rule pairs are replaced, since the rules are more reliable. Rule labels left on pairs
no rule covers (written by an earlier version's third rule, which copied the parents' label) are removed and counted;
an out-of-date rule description in the file is rewritten. Running it again changes nothing.

The rules are stored in the file, next to the category definitions. Clearing a label on a rule pair (e.g. a human
override) restores the rule's label:

.. code-block:: xml

    <interpGrp type="subreading-rules">
      <interp xml:id="rule-orthographic" type="orthographic" ana="#Orthography" n="o">Readings of type 'orthographic' are …</interp>
    </interpGrp>

``@type`` is the reading type, ``@ana`` the category and ``@n`` the id suffix. Rule labels are ordinary relations in
``<listRelation type="transcriptional">`` (so teiphy uses them) with ``resp="#rule-orthographic"``, pointing to the rule.
Pairs covered by a rule are not annotation work: they are left out of the pairs to classify, unit status, sample
progress and the evaluation scores, and are shown in a collapsed section in the GUI, where they can be overridden.
The MCP tool ``apply_rules`` does the same as the command.


Annotation GUI
==================================

A browser interface for annotating and reviewing classifications, generated from the categories in the
``<interpGrp type="transcriptional">`` (nothing is specific to one corpus or category set).
Run it inside the MCP server's process, so that only one program writes the file:

.. code-block:: bash

    rdgai serve-mcp doc.xml --annotator RT --gui-port 8765 --group-by-prefix _

or on its own:

.. code-block:: bash

    rdgai annotate doc.xml --annotator RT --group-by-prefix _ --open

Then open ``http://127.0.0.1:8765/``. If the port is in use, the MCP server still starts (without the GUI).

- Pairs are shown in reading order, the earlier reading first: a → b, a → c, …, b → c, …. The label is saved for
  first → second and the inverse is set automatically. (The pipeline and the MCP tools put the better-attested
  reading first instead, so a suggestion made in the other direction is shown with a "written for" note.)
- One button per category, always in the same position. ``--group-by-prefix _`` lays them out in rows
  (e.g. Minor_* above Major_*), as does a ``@type`` on the ``<interp>`` elements. Hover over a button for its definition.
- Clicking another category replaces the label; clicking the current one clears it.
- A suggestion (e.g. from Claude via ``suggest_classification``) is shown in bold with its justification.
  Suggestions are never shown on test units.
- Tick several pairs to label them, or set their certainty, together.
- Keys: ``j``/``k`` next/previous pair, ``1``–``9``, ``0`` (and shift + number) categories, ``h``/``m``/``l``
  certainty, ``x`` tick, ``n`` next unit with unclassified pairs, ``?`` help.
- If a pair was changed elsewhere (e.g. by Claude through MCP) since the page loaded, the save is refused and
  the unit reloads, so no one's label is overwritten silently. The page also checks every few seconds (and on focus)
  whether the file has changed, and reloads the unit, so suggestions and labels made through MCP appear by themselves.
- Each pair shows a computed description of the change in display order (e.g. "second reading adds: και"), and a
  warning if a saved label contradicts it (e.g. an Omission where the second reading only adds words). Words the
  readings share, or forms of the same word (αυτον/αυτων), that come in a different order are reported as a
  reordering alongside the change (e.g. "reordered and changed: επ αυτον → μετ αυτων").
- The reason box starts empty. "Use reason" copies a suggestion's justification into it. On a pair that already has a
  label it is saved straight away; otherwise it is saved with the category you choose next. Saved unchanged,
  it is recorded as the suggester's wording (``<desc type="justification" resp="#claude">``). A reason written for
  the other direction (b → a) is copied as "Written for b → a: …".
- Two buttons in the rail's "Suggestions" section act on all of a unit's suggestions at once (greyed out when there are none) (after a confirmation, with
  one save). "Save all" labels every unlabelled pair whose suggestions agree, with the suggested
  category, reason and certainty (high if none). "Save reasons" gives each pair you labelled without a reason
  the reason of a suggestion for the same category. Labels and reasons already there are never replaced, pairs
  labelled by rule or by the pipeline are left alone, and the section isn't shown on the sample's test units.
- Units with more than 10 pairs (``--compact-threshold``) are shown as compact rows grouped by the first reading,
  with short buttons in the same order and with the same keys. The triangle at a row's top left (or Enter or ``e``)
  expands it into one box with the reading cards, verse, translation, certainty and reason; "Same suggestion" and
  "Same change", in the rail's "Select pairs like this one" section, select every pair like the current one.
- If two ``serve-mcp`` processes serve the same file (Claude Desktop sometimes starts two), only one gets the GUI port;
  the other logs this, keeps serving MCP, and takes the port over if it is freed (``--gui-retry-seconds``).
  Every write reloads the file under a lock first, so neither process overwrites the other's work.
- Readings are shown as cards, as in the original rdgai GUI, or as a table ("Table"/"Cards" switches; the choice is
  remembered in the browser). Labels made by the pipeline carry the rdgai logo.
- Each pair is shown as two rows, first reading above, an arrow down, then the second reading: a card for the
  reading, and beside it the verse with that reading in place (highlighted), its translation with the words that
  render the reading highlighted, and the witnesses (collapsed).
- Contextual translations: the verse translated with each reading in place, the words that render the reading
  marked ⸂…⸃ (⸆ where an omitted reading would be), e.g. ``of Jesus ⸂Christ⸃, Son of God`` / ``of Jesus⸆, Son of
  God``; translations without marks highlight the words that differ instead. Claude writes them through MCP (``list_untranslated``, then ``set_translations``; e.g. ask
  Claude in Desktop to "translate the sample"). They are kept in a sidecar file, ``<file>.translations.yaml``, never in
  the TEI, and a translation is not shown once its reading's text has changed. The GUI records which unit is open
  (in a hidden ``.<file>.viewing.json`` next to the file), and the MCP tool ``get_current_unit`` returns it, so you can
  ask Claude to "translate the verse I'm on"; the translation appears in the GUI once Claude has saved it. "Translation" or ``t`` shows or hides them.
- All controls are in the left sidebar, grouped under headings: View (Annotate, By category, Agreement), Units to
  annotate (sample, progress, verse) and Narrow down (the filters below), Display (translations, reading overview) and
  Reference (definitions, keyboard). "Reset" returns to the whole sample.
- The header shows the unit and its place in the queue, then the file (or a title) and the annotator. Click your name to
  change your full name (recorded in the TEI header, ``<respStmt xml:id="RT"><name>``), the short name shown in the
  circle, and a title shown instead of the file name; the last two are kept in ``<file>.display.yaml``. The id
  labels are saved under (e.g. ``#RT``) is set when the server starts (``--annotator``).
- When the "Units" filter selects the sample (or only its example or test units), the left sidebar shows a progress
  bar per role, e.g. "Example pairs (181/576) 31.4%" (labelled pairs out of all the pairs to label, one decimal
  place) with "Example units 12/100 (12.0%)" (complete units) under it. Below them, always, the same for the whole
  file: "All pairs (628/44006)" and "All units 113/5626".
- A narrow rail at the right edge holds the unit navigation: Prev and Next, and "Next to do" (``n``: the next unit
  with pairs to label), which turns into the filled red button once every pair in this unit is labelled. Below them
  are the unit's progress ("3 / 6 pairs · 1 by rule") and its pairs (``a → b``), with ✓ on the labelled ones and the
  current pair highlighted; click one to go to that pair. In narrow windows the navigation and progress sit in a
  strip above the pairs instead.
- The units can also be filtered by category, certainty, source (human, pipeline or rule labels, or suggestions) and
  "Disagreement" (a label differs from a suggestion for the same pair).

Besides Annotate, the GUI has views for developing rdgai:

- **By category**: every pair with a category, with its verse and reasons, to check consistency; click another
  category to re-label a pair in place. A category that is its own inverse lists each pair once.
- **Agreement**: human labels compared with one source's suggestions, pair by pair in display direction: overall and
  per-category agreement, a confusion matrix and the disagreements, each linking to its unit. Only example units are
  compared unless test units are chosen explicitly.
- **Definitions** (panel): the guidance and categories with their version; unless the server is read-only or
  started with ``--lock-definitions``, they can be edited, renamed, added and removed (removing a category in use
  needs a category to move its labels to).


Credits
==================================

.. start-credits

Robert Turnbull

For more information contact: <robert.turnbull@unimelb.edu.au>

The article about Rdgai will be published in the near future. For now, please cite the preprint:

    Turnbull, Robert. “Rdgai: Classifying Transcriptional Changes Using Large Language Models with a Test Case from an Arabic Gospel Tradition,” 2025. DOI: 10.48550/arXiv.2511.13801.

.. code-block:: bibtex

    @article{turnbull2025rdgaiclassifyingtranscriptionalchanges,
        title = {{Rdgai: Classifying transcriptional changes using Large Language Models with a test case from an Arabic Gospel tradition}},
        author = {Turnbull, Robert},
        year = {2025},
        eprint = {2511.13801},
        archiveprefix = {arXiv},
        doi = {10.48550/arXiv.2511.13801}
    }


You might like to cite some of the following articles:

- Robert Turnbull, "Transmission History" Pages 156–204 in *Codex Sinaiticus Arabicus and Its Family: A Bayesian Approach*. Vol. 66. New Testament Tools, Studies and Documents. Brill, 2025. `https://doi.org/10.1163/9789004704619_007 <https://doi.org/10.1163/9789004704619_007>`_
- Joey McCollum and Robert Turnbull. "teiphy: A Python Package for Converting TEI XML Collations to NEXUS and Other Formats." *Journal of Open Source Software* 7, no. 80 (2022): 4879. `https://doi.org/10.21105/joss.04879 <https://doi.org/10.21105/joss.04879>`_
- Joey McCollum and Robert Turnbull. "Using Bayesian Phylogenetics to Infer Manuscript Transmission History." *Digital Scholarship in the Humanities* 39, no. 1 (2024): 258–79. `https://doi.org/10.1093/llc/fqad089 <https://doi.org/10.1093/llc/fqad089>`_

.. end-credits

