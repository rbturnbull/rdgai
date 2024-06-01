from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langchain.prompts import ChatPromptTemplate

from .instances import IndexInstance, sort_index_instances_entries



def build_template(index_name:str="", index_instances:list[IndexInstance]|None=None, description:str="") -> ChatPromptTemplate:
    messages = [
        SystemMessage("You are an editor for an academic publisher that seeks to support work of rigorous research outputs that are easy for the academic community to use."),
        HumanMessage(f"I am creating an index for an academic work that is formatted in LaTeX. "),
    ]
    if index_name:
        messages.append(HumanMessage(f"The index is for the '{index_name}' index."))

    if description:
        messages.append(HumanMessage(f"The index is described as follows: '{description}'"))

    if index_instances:

        messages.append(HumanMessage(
            f"The index thus far contains the following {len(index_instances)} entries. "
            "The text of the entry is given first, followed by the context and the entry is placed at the [^] symbol.\n")
        )
        index_instances = sort_index_instances_entries(index_instances)
        current_entry = ""
        for instance in index_instances:
            current_entry = instance.entry
            messages.append(HumanMessage(f"{instance.entry}: {instance.context}\n"))
            
    messages += [
        HumanMessage(
            f"Now I will give you a text section of the work. Please provide a list of places to add to the index. "
            "Ignore any text that is already properly indexed unless you think there needs to be an additional entry.\n"
            f"Each entry should be formatted on a new line like this\n:"
            "entry::text_before[^]text_after\n"
            "Here 'entry' is the text of the index entry, 'text_before' is about 30 characters immediately before the place to put the index, "
            "and 'text_after' is about 20 characters immediately after. The string '[^]' should be in the location where to place the index.\n"
            "For example a response could look like this:\n"
            "Text-Types!Caesarean::manuscripts of the varied manuscripts described as `Caesarean'.[^]\\footcite\n"
            "This would add an index entry for 'Text-Types!Caesarean' with the text before the index being \"manuscripts of the varied manuscripts described as `Caesarean.'[^]\\footcite'.\n"
            "Once you have finished responses for all entries, print 10 hyphens like this: '----------'.\n"
            "Here is the text section:\n"
        ),
        ("human","{text}"),
        AIMessage("Certainly, here are the responses for the index entries in the required format:\n----------\n"),
    ]

    template = ChatPromptTemplate.from_messages(messages=messages)
    return template
