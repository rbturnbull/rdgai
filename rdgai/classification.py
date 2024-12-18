from pathlib import Path
from langchain.schema.output_parser import StrOutputParser
import llmloader
from rich.console import Console

from .prompts import build_template
from .parsers import read_output
from .apparatus import Doc


DEFAULT_MODEL_ID = "gpt-4o"


def classify(
    doc:Doc,
    output:Path,
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
    
    for app in doc.apps:
        print(f"Analyzing apparatus at {app}")
        unclassified_pairs = app.get_unclassified_pairs()
        if not unclassified_pairs:
            continue

        template = build_template(app, examples=examples)
        if verbose or prompt_only:
            template.pretty_print()
            if prompt_only:
                return

        chain = template | llm.bind(stop=["----"]) | StrOutputParser() | read_output

        console.print(f"Saving output to: {output}")
        doc.write(output)

        print("Classifying reading relations ✨")
        results = chain.invoke({})

        for result in results:
            for pair in unclassified_pairs:
                relation_type = doc.relation_types.get(result.category, None)
                if relation_type is None:
                    continue
                
                description = result.justification
                if pair.active.n == result.reading_2_id and pair.passive.n == result.reading_1_id:
                    # if the pair is in the inverse order, swap the relation type
                    relation_type = relation_type.inverse if relation_type.inverse else relation_type
                    description = f"c.f. {pair.active} ➞ {pair.passive}"
                elif pair.active.n != result.reading_1_id or pair.passive.n != result.reading_2_id:
                    # if the pair doesn't match the result, skip
                    continue

                pair.print(console)
                pair.add_type(relation_type, responsible="#rdgai", description=description)

                doc.write(output)
        
        
    