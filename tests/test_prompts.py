from langchain.schema.output_parser import StrOutputParser
from langchain.prompts import ChatPromptTemplate
from rdgai.instances import IndexInstance
from rdgai.prompts import build_template


def test_build_template():
    index_name = "Sample Index"
    index_instances = [
        IndexInstance(path=None, line_number=0, index_name="", text="Sample Entry 1", context="Context for entry 1"),
        IndexInstance(path=None, line_number=0, index_name="", text="Sample Entry 2", context="Context for entry 2"),
    ]
    description = "This is a sample index description."

    template = build_template(index_name=index_name, index_instances=index_instances, description=description)
    assert template.input_variables == ["text"]
    assert len(template.messages) > 5

    response = template.invoke(dict(text="Example text section")).to_string()
    assert "System: You are an editor for an academic publisher" in response
    assert "Start and end your responses with 10 hyphens like this: \'----------\'." in response

