

def parse_category_and_justification(llm_output:str) -> tuple[str, str]:
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
    