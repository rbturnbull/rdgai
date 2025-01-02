from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langchain.prompts import ChatPromptTemplate
from .apparatus import Pair, Doc


def select_spaced_elements(lst:list, k:int) -> list:
    n = len(lst)
    if k >= n:
        return lst
    if k == 1:
        return [lst[0]]
    if k == 2:
        return [lst[0], lst[-1]]
    
    # Always include the first and last element
    indices = [0] + [int((n - 1) * i / (k - 1)) for i in range(1, k - 1)] + [n - 1]
    
    return [lst[i] for i in indices]


# def build_template(app:App, examples:int=10) -> ChatPromptTemplate:
#     doc = app.doc
    
#     readings = app.readings

#     system_message = f"You are an academic who is an expert in textual criticism in {language}."

#     human_message = (
#         f"I am analyzing textual variants in a {language} document.\n"
#         "I want to classify the types of changes made to readings as the text was copied.\n"
#         "If reading 1 were to change to reading 2, what type of change would that be?\n"
#     )
    
#     # Add the categories to the message
#     relation_categories = doc.relation_types.values()
#     human_message += f"\nHere are {len(relation_categories)} possible categories for the types of changes in the text:\n"     
#     for category in relation_categories:
#         human_message += f"{category.str_with_description()}\n"
#         instance_strings = sorted(set(instance.reading_transition_str() for instance in category.pairs if not instance.rdgai_responsible()), key=len)
#         for instance_string in select_spaced_elements(instance_strings, examples):
#             human_message += f"\te.g. {instance_string}\n"

#     # Add the context to the message
#     human_message += f"\nI am variation unit is marked as {app.text_with_signs()} in this text:\n"
#     human_message += f"{app.text_in_context()}\n"

#     # Add the readings to the message        
#     human_message += f"\nHere are the {len(readings)} readings at that variation unit. Remember them so you can analyze them later:\n"
#     for reading in readings:
#         reading_text = reading.text or "OMITTED"
#         human_message += f"{reading.n}: {reading_text}\n"

#     # Describe output format
#     human_message += (
#         f"\nYou need to output the classifications of the readings in the following format.\n"
#         # "If going reading 1 to reading 2 was the same category of change, you would output the following:\n"
#         # "reading_2_id <-> reading_1_id = category : justification\n"

#         "If reading 1 were to change to reading 2, you would output the following:\n"
#         "reading_1_id → reading_2_id = category : justification\n"
#         "You also should output classificaitons from reading 2 to reading 1. It could be the same category or a different one:\n"
#         "reading_2_id → reading_1_id = category : justification\n"
        
#         "For the justification, give a one sentence explanation of why the change is classified as that category.\n"
#         "Separate the result for each pair of readings with a newline and do not add extra new line characters.\n"
#     )

#     # Add the classified relation examples to the message
#     human_message += f"\nGiven the readings above, you can output the following pairs of relations:\n"
#     for i, reading1 in enumerate(readings):
#         for j in range(i + 1, len(readings)):
#             reading2 = readings[j]
#             human_message += f"{reading1.n} → {reading2.n}\n"

#     human_message += "\nIf the change between one of those pairs of readings does not fit one of the categories, then do not output anything for that pair.\n"
#     human_message += f"\nWhen you are finished, output 5 hyphens: '-----'.\n"

#     ai_message = "Certainly, classifications for combinations of the readings are:"

#     template = ChatPromptTemplate.from_messages(messages=[
#         SystemMessage(system_message),
#         HumanMessage(human_message),
#         AIMessage(ai_message),        
#     ])
#     return template


def build_system_message(doc:Doc) -> str:
    return f"You are an academic who is an expert in textual criticism in {doc.language}."


def build_preamble(doc:Doc, examples:int=10) -> str:
    relation_categories = doc.relation_types.values()
    human_message = (
        f"I am analyzing textual variants in a document written in {doc.language}.\n"
        f"There are {len(relation_categories)} possible categories for the types of changes in the text that a scribe could make to change one variant reading to another.\n"
        "Here are the categories with their descriptions. Remember them because you will use this information later:\n"
    )
    for category in relation_categories:
        human_message += f"{category.str_with_description()}\n"

    human_message += "\n"
    human_message += f"Here are examples of these categories The word 'OMIT' indicates the absence of text in this reading. If there is a justification describing the choice of category then it is added in square brackets []:\n"
    for category in relation_categories:
        representative_category_exampes = category.representative_examples(examples)
        if len(representative_category_exampes) == 0:
            continue
        human_message += f"{category}:\n"        
        for pair in representative_category_exampes:
            human_message += f"\te.g. {pair.reading_transition_str()}"
            if pair.has_description():
                human_message += f" [{pair.get_description()}]"
            human_message += "\n"

    human_message += "\n"
    human_message += "I will give you a two variant readings. On the first line of your response, provide the correct category name for changing from the first reading to the second reading.\n"
    human_message += "Do not provide any other text than the name of the category on the first line.\n"
    human_message += "Then on a second line, give a justification of your decision according the definitions of the categories provided and similar examples.\n"
    human_message += f"When you are finished, output 5 hyphens: '-----'.\n"

    return human_message


def build_template(pair:Pair, examples:int=10) -> ChatPromptTemplate:
    app = pair.app
    doc = app.doc

    system_message = build_system_message(doc)
    human_message = build_preamble(doc, examples)    

    human_message += f"\nThe variation unit you need to classify is marked as {app.text_with_signs(pair.active.text)} in this text:\n"
    human_message += f"{app.text_in_context(pair.active.text)}\n"

    active_reading_text = app.text_with_signs(str(pair.active))
    passive_reading_text = app.text_with_signs(str(pair.passive))
    human_message += f"\nWhat category would best describe a change from {active_reading_text} to {passive_reading_text}?\n"

    relation_categories = doc.relation_types.values()
    relation_categories_list = ", ".join(str(category) for category in relation_categories)
    human_message += f"Respond with one of these categories: {relation_categories_list}\n"
    human_message += f"On the second line, provide a justification for your decision."

    ai_message = f"Certainly, the category for changing from {active_reading_text} to {passive_reading_text} is:"

    template = ChatPromptTemplate.from_messages(messages=[
        SystemMessage(system_message),
        HumanMessage(human_message),
        AIMessage(ai_message),        
    ])
    return template


def build_review_prompt(
    doc:Doc,
    correct_items:list["EvalItem"],
    incorrect_items:list["EvalItem"],
    examples:int=10,
):
    prompt_preamble = build_preamble(doc, examples=examples)

    system_message = f"You are an expert prompt engineer with expertise in {doc.language}.\n"

    human_message = "Please review the prompt based on results compared with the ground truth.\n"
    human_message += f"Here is the prompt:\n```\n{prompt_preamble}```\n"
    human_message += "----\nHere are the correctly classified items:\n"
    for item in correct_items:
        human_message += f" - { item.app_id }: {item.reading_transition_str} in `{item.text_in_context}` was correctly classified as {', '.join(item.predicted)} with this as the justification: {item.description}"
        if item.ground_truth_description:
            human_message += f" The justification for the ground truth {', '.join(item.ground_truth)} is: {item.ground_truth_description}"
        human_message += "\n"

    human_message += "----\nHere are the incorrectly classified items:\n"
    for item in incorrect_items:
        human_message += f" - { item.app_id }: {item.reading_transition_str} in `{item.text_in_context}` was incorrectly classified as {', '.join(item.predicted)} when it should have been {', '.join(item.ground_truth)}. This was the justification given: {item.description}"
        if item.ground_truth_description:
            human_message += f" The justification for the ground truth {', '.join(item.ground_truth)} is: {item.ground_truth_description}"
        human_message += "\n"

    human_message += "----\nPlease provide feedback on the prompt. Are the text be editing to improve the accuracy of the results? Are there examples in the test set where the ground truth label is incorrect? If so, then list all the problematic cases with their locations.\n"

    template = ChatPromptTemplate.from_messages(messages=[
        SystemMessage(system_message),
        HumanMessage(human_message),
    ])

    return template