from pathlib import Path
from langchain.schema.output_parser import StrOutputParser
from langchain_core.language_models.llms import LLM
import llmloader
from rich.console import Console
from rich.progress import track

from .prompts import build_template_pair
from .parsers import read_output_pair
from .apparatus import Doc, Pair


DEFAULT_MODEL_ID = "gpt-4o"


def classify(
    doc:Doc,
    output:Path,
    pairs:list[Pair]|None=None,
    verbose:bool=False,
    api_key:str="",
    llm:str=DEFAULT_MODEL_ID,
    prompt_only:bool=False,
    examples:int=10,
    console:Console|None=None,
):
    """
    Classifies relations in TEI documents.
    """
    assert isinstance(doc, Doc), f"Expected Doc, got {type(doc)}"

    console = console or Console()
    llm = llmloader.load(model=llm, api_key=api_key)
    
    pairs = pairs or doc.get_unclassified_pairs()
    for pair in track(pairs):
        classify_pair(doc, pair, llm, output, verbose=verbose, prompt_only=prompt_only, examples=examples, console=console)
        
        
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
    print(f"Analyzing pair at {pair}")

    template = build_template_pair(pair, examples=examples)
    if verbose or prompt_only:
        template.pretty_print()
        if prompt_only:
            return

    chain = template | llm.bind(stop=["----"]) | StrOutputParser() | read_output_pair

    console.print(f"Saving output to: {output}")
    doc.write(output)

    print("Classifying reading relations ✨")
    category, description = chain.invoke({})

    relation_type = doc.relation_types.get(category, None)
    if relation_type is None:
        return
    
    pair.print(console)
    pair.add_type(relation_type, responsible="#rdgai", description=description)

    doc.write(output)
    
    
