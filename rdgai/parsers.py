from dataclasses import dataclass

@dataclass
class Result:
    reading_1_id: str
    reading_2_id: str
    category: str
    justification: str


def read_output(llm_output:str) -> list[Result]:
    """
    Parses the output of a language model to extract readings, categories, and justifications.

    The input string should contain lines formatted as "reading1 → reading2 = category: justification".
    The function will split each line based on these delimiters and create a list of Result objects.

    Args:
        llm_output (str): The output string from a language model, containing multiple lines.

    Returns:
        List[Result]: A list of Result objects, each containing reading_1_id, reading_2_id, category, and justification.

    Examples:
        >>> llm_output = "r1 → r2 = cat1: justification1\\nr3 → r4 = cat2: justification2"
        >>> read_output(llm_output)
        [Result(reading_1_id='r1', reading_2_id='r2', category='cat1', justification='justification1'),
         Result(reading_1_id='r3', reading_2_id='r4', category='cat2', justification='justification2')]
    """
    results = []
    for line in llm_output.split("\n"):
        line = line.strip()
        if not line:
            continue

        # find "→" to split the readings
        index = line.find("→")
        if index == -1:
            continue

        reading1 = line[:index].strip()
        line = line[index + 1:].strip()


        index = line.find("=")
        if index == -1:
            continue

        reading2 = line[:index].strip()
        category = line[index + 1:].strip()

        index = category.find(":")
        if index == -1:
            justification = ""
        else:
            justification = category[index + 1:].strip()
            category = category[:index].strip()

        results.append( Result(reading1.strip(), reading2.strip(), category.strip(), justification.strip()) )
    return results


def read_output_pair(llm_output:str) -> list[Result]:
    """
    Parses the output of a language model to category and justification.
    """
    llm_output = llm_output.strip()
    if "\n" not in llm_output:
        category = llm_output
        justification = ""
    else:
        index = llm_output.find("\n")
        category = llm_output[:index].strip()
        justification = llm_output[index + 1:].strip()
    return category, justification
    