=====================================
Classification
=====================================

Assuming that you have `prepared your TEI XML file <preparation>`_ and `validated the results <validation>`_, you can now classify the transitions in the variation units.

To do that, run the following command:

.. code-block:: bash

    rdgai classify apparatus.xml output.xml

You can modify the TEI XML file in place with the ``--inplace`` flag:

.. code-block:: bash

    rdgai classify apparatus.xml --inplace

Use the same LLM that you used for validation with the ``--llm`` flag. You can also set the number of examples per category with the ``--examples`` flag. e.g.

.. code-block:: bash

    rdgai classify apparatus.xml output.xml --llm claude-3-5-sonnet-20241022 --examples 20

The classifications and justifications will be added to the TEI XML file with "#rdgai" as the responsible party.

You can view the output TEI XML in the Rdgai GUI by running:

.. code-block:: bash

    rdgai gui output.xml --inplace

This will launch a Flask server that you can visit in your browser. The Rdgai classifications will show the Rdgai logo.