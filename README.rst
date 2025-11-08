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


Credits
==================================

.. start-credits

Robert Turnbull
For more information contact: <robert.turnbull@unimelb.edu.au>

The article about Rdgai will be published in the near future. For now, please cite the repository and some of the following articles:

- Robert Turnbull, "Transmission History" Pages 156–204 in *Codex Sinaiticus Arabicus and Its Family: A Bayesian Approach*. Vol. 66. New Testament Tools, Studies and Documents. Brill, 2025. `https://doi.org/10.1163/9789004704619_007 <https://doi.org/10.1163/9789004704619_007>`_
- Joey McCollum and Robert Turnbull. "teiphy: A Python Package for Converting TEI XML Collations to NEXUS and Other Formats." *Journal of Open Source Software* 7, no. 80 (2022): 4879. `https://doi.org/10.21105/joss.04879 <https://doi.org/10.21105/joss.04879>`_
- Joey McCollum and Robert Turnbull. "Using Bayesian Phylogenetics to Infer Manuscript Transmission History." *Digital Scholarship in the Humanities* 39, no. 1 (2024): 258–79. `https://doi.org/10.1093/llc/fqad089 <https://doi.org/10.1093/llc/fqad089>`_

.. end-credits

