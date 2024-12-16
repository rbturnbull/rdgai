import re
from lxml.etree import _ElementTree as ElementTree
from lxml.etree import _Element as Element
from typing import Optional
import lxml.etree as ET
from rich.console import Console
from dataclasses import dataclass, field

from .tei import find_element, find_elements, extract_text


error_console = Console(stderr=True, style="bold red")


def get_reading_identifier(reading:Element, check:bool=False, create_if_necessary:bool=True) -> str:
    identifier = reading.attrib.get("{http://www.w3.org/XML/1998/namespace}id", "")
    if not identifier:
        identifier = reading.attrib.get("n", "")

    if not identifier and create_if_necessary:
        app = reading.getparent()
        identifier = 1
        while find_element(app, f".//rdg[@n='{identifier}']") is not None:
            identifier += 1
        identifier = str(identifier)
        reading.attrib["n"] = identifier
    
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
class Reading:
    id: str
    text: str


def make_readings_list(apparatus:Element) -> list[Reading]:
    readings = []
    for reading in find_elements(apparatus, ".//rdg"):
        identifier = get_reading_identifier(reading)
        text = extract_text(reading).strip()
        readings.append(Reading(id=identifier, text=text))
    return readings


@dataclass
class RelationCategory():
    name:str
    element:Element
    description:str=""
    instances:list['Relation']=field(default_factory=lambda: [])
    inverse:Optional['RelationCategory']=None
    
    def __str__(self) -> str:
        return self.name
    
    def __repr__(self) -> str:
        return str(self)
    
    def str_with_description(self) -> str:
        result = self.name
        if self.description:
            result += f": {self.description}"
        return result

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

    def rdgai_resposible(self) -> bool:
        if self.relation_element is None:
            return False
        return self.relation_element.attrib.get("resp", "") == "#rdgai"

    def __str__(self) -> str:
        return f"{self.location}: {self.reading_transition_str()} [{', '.join(str(c) for c in self.categories)}]"
    
    def reading_transition_str(self) -> str:
        return f"{self.active or 'OMISSION'} → {self.passive or 'OMISSION'}"

    def __repr__(self) -> str:
        return str(self)

    def add_category(self, category) -> None:
        self.categories.add(category)
        if self.relation_element is None:
            self.create_relation_element()
        
        assert self.relation_element is not None
        self.relation_element.set("ana", f"#{category.name}")
        category.instances.append(self)

    def set_responsible(self, responsible:str) -> None:
        if self.relation_element is None:
            self.create_relation_element()
        
        assert self.relation_element is not None
        self.relation_element.set("resp", responsible)

    def set_description(self, description:str) -> Element:
        if self.relation_element is None:
            self.create_relation_element()
        
        description_element = find_element(self.relation_element, ".//desc")
        if description_element is None:
            description_element = ET.SubElement(self.relation_element, "desc")
            
        description_element.text = description

        return description_element

    def get_list_relation(self) -> Element:
        list_relation = find_element(self.apparatus, ".//listRelation[@type='transcriptional']")
        if list_relation is None:
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
        category = RelationCategory(name=name, element=interp, description=description)
        relation_categories.append(category)

    # get corresponding relations
    relation_categories_dict = {c.name: c for c in relation_categories}
    for category in relation_categories:
        inverse_name = category.element.attrib.get("corresp", "")
        if inverse_name.startswith("#"):
            inverse_name = inverse_name[1:]

        if inverse_name in relation_categories_dict:
            inverse = relation_categories_dict[inverse_name]
            category.inverse = inverse
            if inverse.inverse is None:
                inverse.inverse = category
            else:
                assert inverse.inverse == category, f"Inverse category {inverse} already has an inverse {inverse.inverse}."

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


def get_classified_relations(doc:ElementTree|Element, relation_categories:list[RelationCategory]|None=None) -> list[Relation]:
    relations = []
    if not relation_categories:
        relation_categories = get_relation_categories(doc)
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
                            relation_element=relation_element,
                            active_element=active_element, 
                            passive_element=passive_element,
                        )
                        relations.append(relation)
                        for category in categories:
                            category.instances.append(relation)

    return relations


def get_apparatus_unclassified_relations(apparatus:Element) -> list[Relation]:
    relations = []
    location = apparatus.attrib.get("{http://www.w3.org/XML/1998/namespace}id", "")

    list_relation = find_element(apparatus, ".//listRelation[@type='transcriptional']")
    if list_relation is None:
        note = ET.SubElement(apparatus, "note")
        list_relation = ET.SubElement(note, "listRelation", type="transcriptional")

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
            xpath = f".//relation[@active='{active_id}'][@passive='{passive_id}']"
            relation_element = find_element(list_relation, xpath)
            if relation_element is not None:
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


def get_unclassified_relations(doc:ElementTree|Element) -> list[Relation]:
    relations = []
    for apparatus in find_elements(doc, ".//app"):
        relations += get_apparatus_unclassified_relations(apparatus)
        
    return relations
