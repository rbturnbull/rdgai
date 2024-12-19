from rdgai.evaluation import evaluate_docs


def test_evaluate_docs(no_interpgrp):
    evaluate_docs(no_interpgrp, no_interpgrp)
