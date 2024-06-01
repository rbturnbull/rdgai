import re
from lxml.etree import _ElementTree as ElementTree
from lxml.etree import _Element as Element
from vorlagellm.tei import find_element, find_elements, extract_text
from rich.console import Console

from dataclasses import dataclass, field

error_console = Console(stderr=True, style="bold red")


@dataclass
class RelationCategory():
    name:str
    element:Element
    description:str=""
    instances:list['Relation']=field(default_factory=lambda: [])
    

@dataclass
class Relation():
    active:str
    passive:str
    category:RelationCategory
    location:str
    apparatus:Element
    active_element:Element
    passive_element:Element
    

def get_relation_categories(doc:ElementTree|Element) -> list[RelationCategory]:
    interp_group = find_element(doc, ".//interpGrp[@type='transcriptional']")
    
    relation_categories = []
    if interp_group is None:
        error_console.print("No interpGrp of type='transcriptional' found in TEI file.")
        return relation_categories
    
    for interp in find_elements(interp_group, "./interp"):
        name = interp.attrib.get("{http://www.w3.org/XML/1998/namespace}id", "")
        description = extract_text(interp).strip()
        relation_categories.append(RelationCategory(name=name, element=interp, description=description))

    return relation_categories


def get_relations(doc:ElementTree|Element, relation_categories:list[RelationCategory]) -> list[Relation]:
    relations = []
    relation_category_dict = {c.name: c for c in relation_categories}
    for apparatus in find_elements(doc, ".//app"):
        location = apparatus.attrib.get("{http://www.w3.org/XML/1998/namespace}id", "")

        # Get readings for apparatus
        readings_dict = {}
        for reading in find_elements(apparatus, ".//lem") + find_elements(apparatus, ".//rdg"):
            name = reading.attrib.get("n", "")
            if name:
                readings_dict[name] = reading


        for list_relation in find_elements(apparatus, ".//listRelation[@type='transcriptional']"):
            for relation_element in find_elements(list_relation, ".//relation"):
                # Get Category
                for analytic in relation_element.attrib.get("ana", "").split():
                    if analytic.startswith("#"):
                        analytic = analytic[1:]

                    if analytic not in relation_category_dict:
                        error_console.print(f"Analytic category '{analytic}' not found in relation categories. Skipping relation.")
                        continue
                    category = relation_category_dict[analytic]

                    # Get active reading
                    active_reading_name = relation_element.attrib.get("active", "")
                    if active_reading_name not in readings_dict:
                        error_console.print(f"Active reading '{active_reading_name}' not found in readings. Skipping relation.")
                        continue
                    active_element = readings_dict[active_reading_name]
                    active_text = extract_text(active_element).strip()

                    # Get passive reading
                    passive_reading_name = relation_element.attrib.get("passive", "")
                    if passive_reading_name not in readings_dict:
                        error_console.print(f"Passive reading '{passive_reading_name}' not found in readings. Skipping relation.")
                        continue
                    passive_element = readings_dict[passive_reading_name]
                    passive_text = extract_text(passive_element).strip()

                    
                    relation = Relation(
                        active=active_text, 
                        passive=passive_text, 
                        category=category, 
                        location=location, 
                        apparatus=apparatus, 
                        active_element=active_element, 
                        passive_element=passive_element,
                    )
                    relations.append(relation)
                    category.instances.append(relation)

    return relations
