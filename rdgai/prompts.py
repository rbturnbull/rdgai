from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langchain.prompts import ChatPromptTemplate
from .apparatus import App
from .relations import RelationCategory, Reading



def build_template(relation_categories:list[RelationCategory], app:App, readings:list[Reading], language:str, examples:int=3) -> ChatPromptTemplate:
    system_message = f"You are an academic who is an expert in textual criticism in {language}."

    human_message = (
        f"I am analyzing textual variants in a {language} document.\n"
        "I want to classify the types of changes made to readings as the text was copied.\n"
        "If reading 1 were to change to reading 2, what type of change would that be?\n"
        "I also need to classify the change from reading 2 to reading 1.\n"
    )
    
    # Add the categories to the message
    human_message += f"\nHere are the types of {len(relation_categories)} categories for the types of changes in the text:\n"     
    for category in relation_categories:
        human_message += f"{category.str_with_description()}\n"
        for example in category.instances[:examples]:
            human_message += f"\te.g. {example.reading_transition_str()}\n"

    # Add the context to the message
    human_message += f"\nI am variation unit is marked as {app.text_with_signs()} in this text:\n"
    human_message += f"{app.text_in_context()}\n"

    # Add the readings to the message        
    human_message += f"\nHere are the {len(readings)} readings at that variation unit. Remember them so you can analyze them later:\n"
    for reading in readings:
        human_message += f"{reading.id}: {reading.text}\n"

    # Describe output format
    human_message += (
        f"\nYou need to output the classifications of the readings in the following format.\n"
        # "If going reading 1 to reading 2 was the same category of change, you would output the following:\n"
        # "reading_2_id <-> reading_1_id = category : justification\n"

        "If reading 1 were to change to reading 2, you would output the following:\n"
        "reading_1_id → reading_2_id = category : justification\n"
        "You also should output classificaitons from reading 2 to reading 1. It could be the same category or a different one:\n"
        "reading_2_id → reading_1_id = category : justification\n"
        
        "For the justification, give a one sentence explanation of why the change is classified as that category.\n"
        "Separate the result for each pair of readings with a newline and do not add extra new line characters.\n"
    )

    # Add the classified relation examples to the message
    human_message += f"\nGiven the readings above, you can output the following pairs of relations:\n"
    for i, reading1 in enumerate(readings):
        for j in range(i + 1, len(readings)):
            reading2 = readings[j]
            human_message += f"{reading1.id} → {reading2.id}\n"

    human_message += "\nIf the change between one of those pairs of readings does not fit one of the categories, then do not output anything for that pair.\n"
    human_message += f"\nWhen you are finished, output 5 hyphens: '-----'.\n"

    ai_message = "Certainly, classifications for combinations of the readings are:\n"

    template = ChatPromptTemplate.from_messages(messages=[
        SystemMessage(system_message),
        HumanMessage(human_message),
        AIMessage(ai_message),        
    ])
    return template
