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

Then use the graphical user interface (GUI) to classify transitions via buttons or keyboard navigation in a browser-based GUI.

.. code-block:: bash

    rdgai gui apparatus.xml output.xml

Or export classifications to Excel for collaborative editing:

.. code-block:: bash

    rdgai export apparatus.xml reading-pairs.xlsx

Edit in Excel and re-import with:

.. code-block:: bash

    rdgai import-classifications apparatus.xml reading-pairs.xlsx output.xml

More information about preparing the TEI XML file can be found in the `Preparation <https://rbturnbull.github.io/rdgai/docs/preparation>`_ documentation.

Validation
==================================

The accuracy of Rdgai is dependent on the type of text, the categories and their definitions and the LLM used. 
The accuracy needs to be validated on each document used with Rdgai. 
For this purpose, Rdgai comes with a validation tool which assigns a proportion of the manual annotations to be allowed for use in the prompt 
and the remainder are used as ground truth annotations for evaluating the results from Rdgai. 

To run the validation tool, use the following command:

.. code-block:: bash

    rdgai validate apparatus.xml output.xml --report output.html --proportion 0.5 --llm claude-3-5-sonnet-20241022 --examples 20

The HTML report will show the accuracy, precision, recall, F1 scores, confusion matrix, and detailed classifications (correct/incorrect).
The LLM then gives suggestions for clarifying the definitions of the categories and alerts the user to any inconsistencies in the ground truth annotations. 

More information about validating the results of Rdgai for your TEI XML file can be found in the `Validation <https://rbturnbull.github.io/rdgai/docs/validation>`_ documentation.


Classification
==================================

After validating, you can classify the unclassified reading changes using the following command:

.. code-block:: bash

    rdgai classify apparatus.xml output.xml --llm claude-3-5-sonnet-20241022 --examples 20

View the output TEI XML in the Rdgai GUI with:

.. code-block:: bash

    rdgai gui output.xml --inplace

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
- ``draw_sample`` and ``get_sample``: a reproducible random sample of units, split into example and test units, and progress in annotating it.

To draw a sample of 200 units, half of them held out for testing:

.. code-block:: bash

    rdgai sample doc.xml -n 200 --seed 42

The sample is saved to ``doc.sample.yaml`` next to the TEI file (the TEI file is not changed).
Use ``--max-pairs`` to leave out units with very many readings. ``list_units`` with ``sample="sampled"`` and
``status="unclassified"`` lists the sampled units still to annotate.

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
2. two subreadings of the same parent and type (``ao1``–``ao2``): the mapped category;
3. subreading ↔ anything else (``ao``–``b``, ``ao``–``bo``): the label of the parents' pair (``a``–``b``), in the same
   direction, because the categories ignore spelling. Such a pair waits until its parents' pair is labelled; a copy of a
   tentative (pipeline) label is tentative too.

.. code-block:: bash

    rdgai apply-rules doc.xml --rule orthographic=Orthography --inplace      # or --dry-run, or -o out.xml

A subreading's parent is its id without the suffix (``o`` for orthographic, ``f`` for defective; change with
``--suffix orthographic=o``) and any digits after it. Readings whose parent cannot be found, and subreadings of
subreadings, are reported and skipped. Human labels are never changed: those that disagree with a rule are reported
as conflicts. Pipeline labels on rule pairs are replaced, since the rules are more reliable. Running it again changes nothing.

The rules are stored in the file, next to the category definitions, and re-applied to a unit whenever a label in it
changes (from the GUI, MCP or the pipeline), so a label copied under rule 3 always follows its parents' pair:

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

- Each pair is shown with the better-attested reading first; the label is saved for first → second and the
  inverse is set automatically.
- One button per category, always in the same position. ``--group-by-prefix _`` lays them out in rows
  (e.g. Minor_* above Major_*), as does a ``@type`` on the ``<interp>`` elements. Hover over a button for its definition.
- Clicking another category replaces the label; clicking the current one clears it.
- A suggestion (e.g. from Claude via ``suggest_classification``) is shown in bold with its justification, and
  pre-fills the reason. Suggestions are never shown on test units.
- Tick several pairs to label them, or set their certainty, together.
- Keys: ``j``/``k`` next/previous pair, ``1``–``9``, ``0`` (and shift + number) categories, ``h``/``m``/``l``
  certainty, ``x`` tick, ``n`` next unit with unclassified pairs, ``?`` help.
- If a pair was changed elsewhere (e.g. by Claude through MCP) since the page loaded, the save is refused and
  the unit reloads, so no one's label is overwritten silently.


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

