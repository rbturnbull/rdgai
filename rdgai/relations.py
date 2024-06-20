import re
from lxml.etree import _ElementTree as ElementTree
from lxml.etree import _Element as Element
import lxml.etree as ET
from rich.console import Console
from dataclasses import dataclass, field

from .tei import find_element, find_elements, extract_text


error_console = Console(stderr=True, style="bold red")


def get_reading_identifier(reading:Element, check:bool=False) -> str:
    identifier = reading.attrib.get("{http://www.w3.org/XML/1998/namespace}id", "")
    if not identifier:
        identifier = reading.attrib.get("n", "")
    
    if check:
        assert identifier, f"Reading {reading} must have a name attribute 'xml:id' or 'n'."
    
    return identifier


def make_readings_dict(apparatus:Element) -> dict[str, Element]:
    readings_dict = dict()
    for reading in find_elements(apparatus, ".//rdg"):
        identifier = get_reading_identifier(reading)
        if not identifier:
            continue
        readings_dict[identifier] = reading
    return readings_dict


@dataclass
class RelationCategory():
    name:str
    element:Element
    description:str=""
    instances:list['Relation']=field(default_factory=lambda: [])
    
    def __str__(self) -> str:
        return self.name

    def __eq__(self, other):
        if isinstance(other, RelationCategory):
            return (self.name, self.element, self.description) == (other.name, other.element, other.description)
        return False

    def __hash__(self):
        return hash((self.name, self.element, self.description))


@dataclass
class Relation():
    active:str
    passive:str
    location:str
    apparatus:Element
    active_element:Element
    passive_element:Element
    relation_element:Element|None=None
    categories:set[RelationCategory]=field(default_factory=lambda: set())

    def add_category(self, category) -> None:
        self.categories.add(category)
        if not self.relation_element:
            self.create_relation_element()
        
        assert self.relation_element is not None
        self.relation_element.set("ana", f"#{category.name}")
        category.instances.append(self)

    def set_responsible(self, responsible:str) -> None:
        if not self.relation_element:
            self.create_relation_element()
        
        assert self.relation_element is not None
        self.relation_element.set("resp", responsible)

    def set_description(self, description:str) -> Element:
        if not self.relation_element:
            self.create_relation_element()
        
        description_element = find_element(self.relation_element, ".//desc")
        if description_element is None:
            description_element = ET.SubElement(self.relation_element, "desc")
            
        description_element.text = description

        return description_element

    def get_list_relation(self) -> Element:
        list_relation = find_element(self.apparatus, ".//listRelation[@type='transcriptional']")
        if not list_relation:
            sibling = find_element(self.apparatus, ".//listRelation")
            if sibling:
                list_relation = ET.Element("listRelation", type="transcriptional")
                self.apparatus.insert(self.apparatus.index(sibling)+1, list_relation)
            else:
                note = ET.SubElement(self.apparatus, "note")
                list_relation = ET.SubElement(note, "listRelation", type="transcriptional")
        return list_relation
    
    @property
    def active_name(self) -> str:
        return get_reading_identifier(self.active_element, check=True)
    
    @property
    def passive_name(self) -> str:
        return get_reading_identifier(self.passive_element, check=True)

    def create_relation_element(self) -> Element:
        if not self.relation_element:
            list_relation = self.get_list_relation()
            self.active_element.attrib["n"]
            self.relation_element = ET.SubElement(list_relation, "relation", active=self.active_name, passive=self.passive_name)
        return self.relation_element
    

def get_relation_categories(doc:ElementTree|Element, categories_to_ignore:list[str]|None=None) -> list[RelationCategory]:
    interp_group = find_element(doc, ".//interpGrp[@type='transcriptional']")
    categories_to_ignore = categories_to_ignore or []
    
    relation_categories = []
    if interp_group is None:
        error_console.print("No interpGrp of type='transcriptional' found in TEI file.")
        return relation_categories
    
    for interp in find_elements(interp_group, "./interp"):
        name = interp.attrib.get("{http://www.w3.org/XML/1998/namespace}id", "")
        if name in categories_to_ignore:
            continue
        description = extract_text(interp).strip()
        relation_categories.append(RelationCategory(name=name, element=interp, description=description))

    return relation_categories


def get_relation_categories_dict(doc:ElementTree|Element, categories_to_ignore:list[str]|None=None) -> dict[str,RelationCategory]:
    relation_categories = get_relation_categories(doc, categories_to_ignore)
    return {c.name: c for c in relation_categories}


def get_categories(relation_element:Element, relation_category_dict:dict[str,RelationCategory]) -> set[RelationCategory]:
    categories = set()
    for analytic in relation_element.attrib.get("ana", "").split():
        if analytic.startswith("#"):
            analytic = analytic[1:]

        if analytic not in relation_category_dict:
            error_console.print(f"Analytic category '{analytic}' not found in relation categories. Skipping relation.")
            continue
        categories.add(relation_category_dict[analytic])
    return categories


def get_classified_relations(doc:ElementTree|Element, relation_categories:list[RelationCategory]) -> list[Relation]:
    relations = []
    relation_category_dict = {c.name: c for c in relation_categories}
    for apparatus in find_elements(doc, ".//app"):
        location = apparatus.attrib.get("{http://www.w3.org/XML/1998/namespace}id", "")

        # Get readings for apparatus
        readings_dict = make_readings_dict(apparatus)

        for list_relation in find_elements(apparatus, ".//listRelation[@type='transcriptional']"):
            for relation_element in find_elements(list_relation, ".//relation"):
                # Get Categories
                categories = get_categories(relation_element, relation_category_dict)

                # Get active reading
                for active_reading_name in relation_element.attrib.get("active", "").split():
                    if active_reading_name not in readings_dict and active_reading_name.startswith("#"):
                        active_reading_name = active_reading_name[1:]

                    if active_reading_name not in readings_dict:
                        error_console.print(f"Active reading '{active_reading_name}' not found in readings. Skipping relation.")
                        continue
                    active_element = readings_dict[active_reading_name]
                    active_text = extract_text(active_element).strip()

                    # Get passive reading
                    for passive_reading_name in relation_element.attrib.get("passive", "").split():
                        if passive_reading_name not in readings_dict and passive_reading_name.startswith("#"):
                            passive_reading_name = passive_reading_name[1:]

                        if passive_reading_name not in readings_dict:
                            error_console.print(f"Passive reading '{passive_reading_name}' not found in readings. Skipping relation.")
                            continue
                        passive_element = readings_dict[passive_reading_name]
                        passive_text = extract_text(passive_element).strip()

                        
                        relation = Relation(
                            active=active_text, 
                            passive=passive_text, 
                            categories=categories, 
                            location=location, 
                            apparatus=apparatus, 
                            active_element=active_element, 
                            passive_element=passive_element,
                        )
                        relations.append(relation)
                        for category in categories:
                            category.instances.append(relation)

    return relations


def get_unclassified_relations(doc:ElementTree|Element) -> list[Relation]:
    relations = []
    for apparatus in find_elements(doc, ".//app"):
        location = apparatus.attrib.get("{http://www.w3.org/XML/1998/namespace}id", "")

        list_relation = find_element(apparatus, ".//listRelation[@type='transcriptional']")

        # Get readings for apparatus
        readings_dict = {}
        for reading in find_elements(apparatus, ".//rdg"):
            identifier = get_reading_identifier(reading)
            if not identifier:
                continue

            readings_dict[identifier] = reading

        for active_id in readings_dict:
            for passive_id in readings_dict:
                if active_id == passive_id:
                    continue

                # Check if relation already exists for this pair
                if find_element(list_relation, f".//relation[@active='{active_id}' and @passive='{passive_id}']"):
                    continue

                active_element = readings_dict[active_id]
                active_text = extract_text(active_element).strip()
                passive_element = readings_dict[passive_id]
                passive_text = extract_text(passive_element).strip()

                relation = Relation(
                    active=active_text, 
                    passive=passive_text, 
                    location=location, 
                    apparatus=apparatus, 
                    active_element=active_element, 
                    passive_element=passive_element,
                )
                relations.append(relation)

    return relations
