================================================================
rdgai
================================================================

.. start-badges

.. image:: https://raw.githubusercontent.com/rbturnbull/rdgai/refs/heads/main/docs/img/rdgai-banner.svg
    :alt: rdgai

|pypi badge| |testing badge| |coverage badge| |docs badge| |black badge|

.. |pypi badge| image:: https://img.shields.io/pypi/v/rdgai
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

.. end-badges

Documentation is available at `https://rbturnbull.github.io/rdgai`_.

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

Categories are defined in the TEI XML header under the `<interpGrp type="transcriptional">` element. Categories can be:
- **Reciprocal:** A transition from A to B has an inverse category for B to A.
- **Symmetrical:** A transition between A and B belongs to the same category regardless of direction.

Users can classify transitions via the Rdgai GUI by clicking buttons corresponding to each category for each variation unit.

The variation units can be exported to Excel for collaborative editing and then imported back into TEI XML.

More information about preparing the TEI XML file can be found in the `documentation <https://rbturnbull.github.io/rdgai/docs/preparation>`_.

Validation
==================================

The accuracy of Rdgai is dependent on the type of text, the categories and their definitions and the LLM used. 
The accuracy needs to be validated on each document used with Rdgai. 
For this purpose, Rdgai comes with a validation tool which assigns a proportion of the manual annotations to be allowed for use in the prompt 
and the remainder are used as ground truth annotations for evaluating the results from Rdgai. 
It creates an HTML report  showing the accuracy, macro precision, recall and F1 scores, a confusion matrix and a complete list of all the correct and incorrect classifications, 
showing the ground truth classification, the predicted category from Rdgai and the justification given. 
The report includes the base prompt including the representative example for each category. 
A textual version of the report is given to the LLM and it is asked to review the prompt with the category definitions and examples, 
based on the correct and incorrect results. The LLM then gives suggestions for clarifying the definitions of the categories 
and alerts the user to any inconsistencies in the ground truth annotations.


More information about validating the results of Rdgai for your TEI XML file can be found in the `validation documentation <https://rbturnbull.github.io/rdgai/docs/validation>`_.


Classification
==================================


Usage of the GUI for classifying transitions can be found in the `classification documentation <https://rbturnbull.github.io/rdgai/docs/classification>`_.



.. end-quickstart


Credits
==================================

.. start-credits

Robert Turnbull
For more information contact: <robert.turnbull@unimelb.edu.au>

Citation details to come.

.. end-credits

