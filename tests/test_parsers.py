from rdgai.parsers import CategoryParser


def test_parser():
    parser = CategoryParser(["Single_Minor_Word_Change", "Multiple_Word_Changes", "Single_Major_Word_Change", "Orthography", "Transposition"])
    output = 'Certainly, the category for changing from ⸂اما شيطان فليس في⸃ to ⸂اما شيطان فليس⸃ is:  **Single_Minor_Word_Change**\n\nJustification: The deletion of the word "في" (fī) from "فليس" is an example of a single minor word change, as it is a small alteration that does not significantly affect the overall meaning of the sentence. According to the definition, this type of change involves the omission or substitution of a single minor word or part of a word, which aligns with the characteristics of this change.-------\nLet me know if you want further help!'

    category, justification = parser.invoke(output)
    assert category == "Single_Minor_Word_Change"
    assert justification == 'Justification: The deletion of the word "في" (fī) from "فليس" is an example of a single minor word change, as it is a small alteration that does not significantly affect the overall meaning of the sentence. According to the definition, this type of change involves the omission or substitution of a single minor word or part of a word, which aligns with the characteristics of this change.'
    