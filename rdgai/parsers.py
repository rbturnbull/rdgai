from dataclasses import dataclass
from langchain_core.runnables import Runnable

@dataclass
class CategoryParser(Runnable):
    relation_type_names: list[str]

    def invoke(self, llm_output:str, *args, **kwargs) -> tuple[str, str]:
        """
        Parses the output of a language model to category and justification.
        """
        llm_output = llm_output.strip()
        if "-----" in llm_output:
            llm_output = llm_output[:llm_output.find("-----")]

        category = ""
        justification = ""
        minimim_index = len(llm_output)
        for relation_type in self.relation_type_names:
            if relation_type in llm_output:
                index = llm_output.find(relation_type)
                if index < minimim_index:
                    minimim_index = index
                    category = relation_type
                    justification = llm_output[index + 1:]
                    justification_index = justification.find("\n")
                    justification = justification[justification_index + 1:].strip()
                    
        return category, justification

