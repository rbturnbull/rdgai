==================================
Classification
==================================

After evaluating, classify the reading pairs that are still unclassified. First estimate the cost (no model is called):

.. code-block:: bash

    rdgai estimate apparatus.xml --model anthropic:claude-opus-5-5

The draft pass classifies every pair still to classify, unit by unit. Agents read through the Rdgai MCP server (the
definitions, the unit, similar labelled pairs) and give a checked, structured decision for every pair, which is written
to the file with ``resp="#rdgai"``, the agent's confidence as ``@cert`` and a ``<change>`` recording the run and model.
Human labels are never changed, and the run can be stopped and continued at any time:

.. code-block:: bash

    rdgai draft apparatus.xml --model anthropic:claude-opus-5-5 --limit-units 20   # calibrate the estimate first
    rdgai draft apparatus.xml --model anthropic:claude-opus-5-5

Then review the pipeline's labels one category at a time; the revisions are applied and logged to
``apparatus.revisions.jsonl``:

.. code-block:: bash

    rdgai review apparatus.xml --rounds 3

``rdgai run apparatus.xml`` does all of this (subreading rules, draft and review). Check the results in the
annotation GUI, where the pipeline's labels are shown with their source and can be corrected:

.. code-block:: bash

    rdgai annotate apparatus.xml --annotator RT
