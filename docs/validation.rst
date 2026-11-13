==================================
Evaluation
==================================

The accuracy of Rdgai depends on the type of text, the categories and their definitions, and the model used.
It needs to be evaluated on each document used with Rdgai.

First draw a reproducible random sample of variation units and annotate them. Half of them (by default) are held out
as test units; the rest are example units, which the pipeline may use as precedents:

.. code-block:: bash

    rdgai sample apparatus.xml -n 200 --seed 42

The sample is saved to ``apparatus.sample.yaml`` next to the TEI file. Then run the evaluation:

.. code-block:: bash

    rdgai evaluate apparatus.xml --output-dir evaluation --model anthropic:claude-opus-5-5 --rounds 3

The pipeline runs on a copy of the file from which the test units' labels and suggestions have been removed, so they
cannot reach the model. It drafts the test units, then reviews its labels one category at a time for the given number
of rounds, and both the draft and the reviewed predictions are compared with your labels. The original file is not
modified.

Choice of model
-----------------------------------

Models are given as `pydantic-ai <https://ai.pydantic.dev>`_ model names, e.g. ``anthropic:claude-opus-5-5`` (the
default), ``anthropic:claude-sonnet-5-5``, ``openai:gpt-5``, or a local model. Set the provider's API key in the usual
environment variable (e.g. ``ANTHROPIC_API_KEY``). Use ``rdgai estimate`` to see what a run would cost first.

HTML report
-----------------------------------

``evaluation/report.html`` shows the accuracy and macro precision, recall and F1 for the draft and after review,
per-category scores (categories with fewer than 10 test pairs are marked as indicative only), the confusion matrix,
every test pair with the gold label, the draft and the reviewed prediction and its justification, and the revisions
made in review. It also records the model, the hash of the category definitions and the runs, so results can be
reproduced. ``evaluation/report.json`` has the same results as data, and ``evaluation/revisions.jsonl`` logs every
revision with its reason and evidence.
