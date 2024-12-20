from pathlib import Path
from langchain.schema.output_parser import StrOutputParser
from langchain_core.language_models.llms import LLM
import llmloader
from rich.console import Console
from rich.progress import track

from .prompts import build_template
from .parsers import CategoryParser
from .apparatus import Doc, Pair


DEFAULT_MODEL_ID = "gpt-4o"


def classify_pair(
    doc:Doc,
    pair:Pair,
    llm:LLM,
    output:Path,
    verbose:bool=False,
    prompt_only:bool=False,
    examples:int=10,
    console:Console|None=None,
):
    """
    Classifies relations for a pair of readings.
    """
    assert isinstance(doc, Doc), f"Expected Doc, got {type(doc)}"

    console = console or Console()

    template = build_template(pair, examples=examples)
    if verbose or prompt_only:
        template.pretty_print()
        if prompt_only:
            return

    chain = template | llm.bind(stop=["----"]) | StrOutputParser() | CategoryParser(doc.relation_types.keys())

    doc.write(output)

    category, description = chain.invoke({})

    console.print()
    pair.print(console)
    console.print(category, style="green bold")
    console.print(description, style="grey46")

    relation_type = doc.relation_types.get(category, None)
    if relation_type is None:
        return
    
    inverse_description = f"c.f. {pair.active} ➞ {pair.passive}"
    pair.add_type_with_inverse(relation_type, responsible="#rdgai", description=description, inverse_description=inverse_description)

    doc.write(output)
    
    
def classify(
    doc:Doc,
    output:Path,
    pairs:list[Pair]|None=None,
    verbose:bool=False,
    api_key:str="",
    llm:str=DEFAULT_MODEL_ID,
    temperature:float=0.1,
    prompt_only:bool=False,
    examples:int=10,
    console:Console|None=None,
):
    """
    Classifies relations in TEI documents.
    """
    assert isinstance(doc, Doc), f"Expected Doc, got {type(doc)}"

    console = console or Console()
    llm = llmloader.load(model=llm, api_key=api_key, temperature=temperature)
    
    pairs = pairs or doc.get_unclassified_pairs(redundant=False)
    for pair in track(pairs):
        classify_pair(doc, pair, llm, output, verbose=verbose, prompt_only=prompt_only, examples=examples, console=console)
        
        
