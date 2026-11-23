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

On the ECM's Gospel of Mark (5-fold cross-validation over 200 annotated units, 1,311 pairs, two runs per model), the
models alone labelled 92.1–94.1% (Haiku 5.5), 92.6–94.0% (Sonnet 5.5) and 95.3–96.6% (Opus 5.5) of the pairs as the
annotator did. Runs of the same model differed by 1–2 points, so compare setups over more than one run.

Cross-validation
-----------------------------------

The test units are for the final check. To compare models, definitions or settings without using them, cross-validate
on the example units: ``--fold`` evaluates one fold (1 to ``--folds``, 5 by default) with the other folds as precedents,
and the test units are hidden entirely. The folds are drawn with ``--seed`` and saved (by default to
``<output-dir>/../folds.yaml``), so every run uses the same split:

.. code-block:: bash

    for fold in 1 2 3 4 5; do
        rdgai evaluate apparatus.xml --output-dir cv/f$fold --fold $fold --model anthropic:claude-opus-5-5 --rounds 0
    done

``--fold-pool all`` folds over every sample unit (stratified by size) instead of the example units only.

Escalating unsure pairs
-----------------------------------

When the draft model is unsure of a pair, it names a second category. ``--expert-model`` gives those pairs to a stronger
model, which chooses between the two options with both definitions and similar labelled pairs in front of it
(``--expert-mode after``, the default). With ``--expert-mode tool``, the draft model can ask the expert itself while it
works (at most two questions per request). The report shows how many pairs were flagged, how often the second category
was right, and what the expert fixed and broke.

.. code-block:: bash

    rdgai evaluate apparatus.xml --output-dir cv/f1 --fold 1 --model anthropic:claude-haiku-5-5 --expert-model anthropic:claude-opus-5-5

``--batch`` drafts through Anthropic's Message Batches at half the price (see :doc:`classification`).

HTML report
-----------------------------------

``evaluation/report.html`` shows the accuracy and macro precision, recall and F1 for the draft and after review,
per-category scores (categories with fewer than 10 test pairs are marked as indicative only), the confusion matrix,
every test pair with the gold label, the draft and the reviewed prediction and its justification, and the revisions
made in review. It also records the model, the hash of the category definitions and the runs, so results can be
reproduced. ``evaluation/report.json`` has the same results as data, and ``evaluation/revisions.jsonl`` logs every
revision with its reason and evidence.
