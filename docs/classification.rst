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

Batch mode
-----------------------------------

With an Anthropic model, ``--batch`` sends the draft pass through Anthropic's Message Batches API at half the price;
results come within hours rather than minutes, and ``rdgai estimate`` shows both prices:

.. code-block:: bash

    rdgai draft apparatus.xml --model anthropic:claude-opus-5-5 --batch

Each answer is checked as in the interactive pass; an answer that fails a check goes into a further, smaller batch with
the problem explained. The submitted batches and the usage so far are kept in ``apparatus.batch-state.json`` (or
``--state-file``), so if the command is interrupted, running it again collects the batch already paid for instead of
sending it again. ``--poll-interval`` sets how often the batch is checked (60 seconds by default).

For example, Opus 5.5 labelled all 42,695 unlabelled pairs of the ECM's Gospel of Mark (5,426 units) in about 84
minutes: 6,214 requests in three batches, 65.3M input tokens (45.5M of them cached) and 8.7M output tokens, for $130.70.

Category constraints
-----------------------------------

A category can declare what it requires of the change between two readings, with the MCP tool
``set_category_constraints``: ``adds-only``, ``omits-only``, ``keeps-word-count`` or ``not-only-adds-or-omits``. When the
pipeline assigns a category whose constraint the computed change breaks, it asks the model once to reconsider (the model
may keep its answer, since the check cannot tell every difference of spelling from one of form). ``rules-only`` marks a
category that only the subreading rules assign, so the pipeline never offers it. Without declared constraints nothing is
checked.

Review
-----------------------------------

Then review the pipeline's labels one category at a time; the revisions are applied and logged to
``apparatus.revisions.jsonl``:

.. code-block:: bash

    rdgai review apparatus.xml --rounds 3

``rdgai run apparatus.xml`` does all of this (subreading rules, draft and review). Check the results in the
annotation GUI, where the pipeline's labels are shown with their source and can be corrected:

.. code-block:: bash

    rdgai annotate apparatus.xml --annotator RT
