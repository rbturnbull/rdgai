from typing import Optional
from pathlib import Path
from dataclasses import dataclass, field
from lxml.etree import _Element as Element
from lxml.etree import _ElementTree as ElementTree
from lxml import etree as ET
from rich.console import Console
import functools
import Levenshtein
import numpy as np

# from .relations import Relation, get_reading_identifier
from .tei import read_tei, find_elements, extract_text, find_parent, find_element, write_tei, make_nc_name, get_language, get_reading_identifier
from .mapper import Mapper

@dataclass
class Reading():
    element: Element
    app:"App"
    n: str  = field(default=None)
    text: str  = field(default=None)
    witnesses: list[str] = field(default_factory=list)

    def __post_init__(self):
        self.n = get_reading_identifier(self.element)
        self.text = extract_text(self.element).strip()
        self.witnesses = self.element.attrib.get("wit", "").split()

    def __str__(self):
        return self.text or 'OMIT'
    
    def witnesses_str(self) -> str:
        return " ".join(self.witnesses)

    def __hash__(self):
        return hash(self.element)


@dataclass
class RelationType():
    element: Element
    name: str
    description: str
    inverse: Optional['RelationType'] = None
    pairs: set['Pair'] = field(default_factory=set)

    def __str__(self):
        return self.name
    
    def __repr__(self) -> str:
        return str(self)

    def __eq__(self, other):
        if isinstance(other, RelationType):
            return (self.name, self.element, self.description) == (other.name, other.element, other.description)
        return False

    def __hash__(self):
        return hash((self.name, self.element, self.description))
    
    def str_with_description(self) -> str:
        result = self.name
        if self.description:
            result += f": {self.description}"
        return result

    def pairs_sorted(self, exclude_rdgai:bool = False) -> list['Pair']:
        pairs = sorted(self.pairs, key=lambda pair: (str(pair.active.app), pair.active.n, pair.passive.n))
        if exclude_rdgai:
            pairs = [pair for pair in pairs if not pair.rdgai_responsible()]
        return pairs

    def get_inverse(self) -> 'RelationType':
        return self.inverse if self.inverse else self
    
    @functools.lru_cache(maxsize=None)
    def representative_examples(self, k:int, random_state:int=42) -> list['Pair']:
        
        def find_representative_examples(pairs_list:list[Pair], k:int, random_state:int=42):
            import kmedoids
            if len(pairs_list) <= k:
                return pairs_list
            distance_matrix = np.zeros((len(pairs_list), len(pairs_list)))
            for index1, pair in enumerate(pairs_list):
                for index2 in range(index1+1, len(pairs_list)):
                    other_pair = pairs_list[index2]
                    active_text_distance = Levenshtein.distance(pair.active.text, other_pair.active.text)
                    passive_text_distance = Levenshtein.distance(pair.passive.text, other_pair.passive.text)
                    distance = active_text_distance + passive_text_distance
                    distance_matrix[index1, index2] = distance
                    distance_matrix[index2, index1] = distance

            result = kmedoids.fasterpam(distance_matrix, k, random_state=random_state, init="build")

            return [pairs_list[index] for index in result.medoids]
    
        pairs_list = self.pairs_sorted(exclude_rdgai=True)
        pairs_with_descriptions = [pair for pair in pairs_list if pair.has_description()]
        representative_pairs = []
        if pairs_with_descriptions:
            representative_pairs = find_representative_examples(pairs_with_descriptions, k, random_state=random_state)
        
        if len(representative_pairs) < k:
            pairs_without_descriptions = [pair for pair in pairs_list if not pair.has_description()]
            additional_pairs = find_representative_examples(pairs_without_descriptions, k-len(representative_pairs), random_state=random_state)
            representative_pairs.extend(additional_pairs)
        
        return representative_pairs


@dataclass
class Pair():
    active: Reading
    passive: Reading
    types: set[RelationType] = field(default_factory=set)

    def __post_init__(self):
        for relation_type in self.types:
            relation_type.pairs.add(self)

    def __str__(self):
        return f"{self.active} ➞ {self.passive}"
    
    def print(self, console):
        console.print(f"[bold red]{self.app}[/bold red]: [green]{self.active}[/green] [red]➞[/red] [green]{self.passive}[/green]")

    def reading_transition_str(self) -> str:
        return f"{self.active or 'OMISSION'} → {self.passive or 'OMISSION'}"
    
    def __repr__(self) -> str:
        return str(self)

    @property
    def app(self) -> "App":
        # assert self.active.app == self.passive.app
        return self.active.app
    
    def __hash__(self):
        return hash((self.active, self.passive))
    
    def app_element(self) -> Element:
        return find_parent(self.active.element, "app")
    
    def relation_elements(self) -> list[Element]:
        list_relation = find_element(self.app_element(), ".//listRelation[@type='transcriptional']")
        if list_relation is None:
            return []

        return find_elements(list_relation, f".//relation[@active='{self.active.n}'][@passive='{self.passive.n}']")
    
    def element_for_type(self, type:RelationType) -> Element|None:        
        for relation in self.relation_elements():
            if f"#{type.name}" in relation.attrib.get("ana").split():
                return relation
        return None
    
    def get_inverse(self) -> "Pair":
        found_pair = None
        for pair in self.app.pairs:
            if pair.active == self.passive and pair.passive == self.active:
                found_pair = pair
                break
        assert found_pair is not None, f"No inverse pair found for {self}"
        return found_pair
    
    def add_type_with_inverse(self, type:RelationType, responsible:str|None=None, description:str="", inverse_description:str="") -> Element:
        relation = self.add_type(type, responsible=responsible, description=description)
        inverse = self.get_inverse()
        inverse.add_type(type.get_inverse(), responsible=responsible, description=inverse_description)
        return relation

    def add_type(self, type:RelationType, responsible:str|None=None, description:str="") -> Element:
        self.types.add(type)
        type.pairs.add(self)

        # Check if the relation already exists
        relation = self.element_for_type(type)
        if relation is not None:
            return relation

        list_relation = find_element(self.app_element(), ".//listRelation[@type='transcriptional']")
        if list_relation is None:
            list_relation = ET.SubElement(self.app_element(), "listRelation", attrib={"type":"transcriptional"})
        
        relation = find_element(list_relation, f".//relation[@active='{self.active.n}'][@passive='{self.passive.n}']")
        if relation is not None:
            if type.name not in relation.attrib.get("ana").split():
                relation.attrib["ana"] += f" #{type.name}"
        else:
            relation = ET.SubElement(list_relation, "relation", attrib={"active":self.active.n, "passive":self.passive.n, "ana":f"#{type.name}"})

        if responsible is not None:
            relation.set("resp", responsible)

        self.add_description(description, relation)

        return relation
    
    def remove_description(self):
        for relation in self.relation_elements():
            for desc in find_elements(relation, ".//desc"):
                relation.remove(desc)
                
    def add_description(self, description:str, relation:Element|None=None):
        if relation is None:
            relation_elements = self.relation_elements()

            if len(relation_elements) == 0:
                list_relation = find_element(self.app_element(), ".//listRelation[@type='transcriptional']")
                if list_relation is None:
                    list_relation = ET.SubElement(self.app_element(), "listRelation", attrib={"type":"transcriptional"})
                relation = ET.SubElement(list_relation, "relation", attrib={"active":self.active.n, "passive":self.passive.n})
            else:        
                relation = relation_elements[0]

        description = description.strip()
        if description:
            description_element = find_element(relation, ".//desc")
            if description_element is None:
                description_element = ET.SubElement(relation, "desc")
                
            description_element.text = description

    def remove_type(self, relation_type:RelationType):
        if relation_type in self.types:
            self.types.remove(relation_type)

        if self in relation_type.pairs:    
            relation_type.pairs.remove(self)

        list_relation = find_element(self.app_element(), ".//listRelation[@type='transcriptional']")
        for relation in find_elements(list_relation, f".//relation[@active='{self.active.n}'][@passive='{self.passive.n}']"):
            if f"#{relation_type.name}" in relation.attrib.get("ana").split():
                relation.attrib['ana'] = " ".join([ana for ana in relation.attrib.get("ana").split() if ana != f"#{relation_type.name}"])
            if not relation.attrib.get("ana"):
                relation.getparent().remove(relation)

    def remove_type_with_inverse(self, relation_type:RelationType):
        self.remove_type(relation_type)
        inverse = self.get_inverse()
        inverse.remove_type(relation_type.get_inverse())

    def remove_all_types(self):
        for relation_type in set(self.types):
            self.remove_type_with_inverse(relation_type)

    def rdgai_responsible(self) -> bool:
        for element in self.relation_elements():
            if element.attrib.get('resp', '') == '#rdgai':
                return True
        return False

    def relation_type_names(self) -> set[str]:
        return set(type.name for type in self.types)
    
    def has_description(self) -> bool:
        for relation in self.relation_elements():
            if find_element(relation, ".//desc") is not None:
                return True
        return False

    def get_description(self) -> str:
        description = ""
        for relation_element in self.relation_elements():
            for desc in find_elements(relation_element, ".//desc"):
                description += "\n" + extract_text(desc)
        return description.strip()


@dataclass
class App():
    element: Element
    doc: "Doc"
    readings: list[Reading] = field(default_factory=list)
    pairs: list[Pair] = field(default_factory=list)
    non_redundant_pairs: list[Pair] = field(default_factory=list)

    def __post_init__(self):
        for reading in find_elements(self.element, ".//rdg"):
            self.readings.append(Reading(reading, app=self))

        # Build list of relation elements
        relation_elements = []
        for list_relation in find_elements(self.element, ".//listRelation[@type='transcriptional']"):
            for relation_element in find_elements(list_relation, ".//relation"):
                relation_elements.append(relation_element)
        
        # Build list of relation pairs
        active_visited = set()
        for active in self.readings:
            active_visited.add(active)
            for passive in self.readings:
                if active == passive:
                    continue

                types = set()
                for relation_element in relation_elements:
                    if relation_element.attrib.get("active") == active.n and relation_element.attrib.get("passive") == passive.n:
                        for ana in relation_element.attrib.get("ana", "").split():
                            if ana.startswith("#"):
                                ana = ana[1:]
                            if ana:
                                types.add(ana)

                pair_relation_types = set()
                for type_name in types:
                    relation_type = self.doc.relation_types[type_name] if type_name in self.doc.relation_types else self.doc.add_relation_type(type_name)                    
                    pair_relation_types.add(relation_type)

                pair = Pair(active=active, passive=passive, types=pair_relation_types)
                self.pairs.append(pair)
                if passive not in active_visited:
                    self.non_redundant_pairs.append(pair)

                for relation_type in pair_relation_types:
                    relation_type.pairs.add(pair)

        assert len(self.pairs) == len(self.non_redundant_pairs) * 2

    def get_classified_pairs(self, redundant:bool=True) -> list[Pair]:
        pairs = self.pairs if redundant else self.non_redundant_pairs
        return [pair for pair in pairs if len(pair.types) > 0]

    def get_unclassified_pairs(self, redundant:bool=True) -> list[Pair]:
        pairs = self.pairs if redundant else self.non_redundant_pairs
        return [pair for pair in pairs if len(pair.types) == 0]

    def __hash__(self):
        return hash(self.element)

    def __str__(self):
        name = self.element.attrib.get('{http://www.w3.org/XML/1998/namespace}id', '')
        if not name:
            name = self.element.attrib.get('n', '')

        if not name:
            ab = self.ab()
            for index, app in enumerate(find_elements(ab, ".//app")):
                if app == self.element:
                    name = make_nc_name(f"{self.ab_name()}-{index+1}")
                    self.element.attrib['{http://www.w3.org/XML/1998/namespace}id'] = name
                    break
        return str(name).replace(" ", "_").replace(":", "_")

    def ab(self) -> Element|None:
        return find_parent(self.element, "ab")

    def ab_name(self) -> str:
        ab = self.ab()
        return ab.attrib.get("n", "")
    
    def text_before(self) -> str:
        ab = self.ab()
        if ab is None:
            return ""
        
        items = []
        for child in ab:
            if child == self.element:
                break
            child_text = extract_text(child)
            if child_text:
                items.append(child_text)

        text = " ".join(items)
        return text.strip()
    
    def text_in_context(self, text="") -> str:
        return f"{self.text_before()} {self.text_with_signs(text)} {self.text_after()}".strip()

    def text(self) -> str:
        return extract_text(self.element)

    def text_with_signs(self, text="") -> str:
        text = text or self.text()
        if not text:
            return "⸆"
        return f"⸂{text}⸃"

    def text_after(self) -> str:
        ab = self.ab()
        if ab is None:
            return ""
        
        items = []
        reached_element = False
        for child in ab:
            if reached_element:
                child_text = extract_text(child)
                if child_text:
                    items.append(child_text)
            if child == self.element:
                reached_element = True

        text = " ".join(items)
        return text.strip()


@dataclass
class Doc():
    path: Path
    tree: ElementTree = field(default=None)
    apps: list[App] = field(default_factory=list)
    relation_types: dict[str,RelationType] = field(default_factory=dict)
    
    def __post_init__(self):
        self.tree = read_tei(self.path)
        self.relation_types = self.get_relation_types()

        for app_element in find_elements(self.tree, ".//app"):
            app = App(app_element, doc=self)
            self.apps.append(app)

    def get_interpgrp(self) -> Element:
        text = find_element(self.tree, "text") 
        interp_group = find_element(text, ".//interpGrp[@type='transcriptional']") 
        if interp_group is None: 
            interp_group = ET.Element("interpGrp", attrib={"type":"transcriptional"}) 
            text.insert(0, interp_group) 

        return interp_group

    def add_relation_type(self, name:str, description:str="") -> RelationType:
        if name in self.relation_types:
            assert self.relation_types[name].description == description, f"RelationType {name} already exists with a different description."
            return self.relation_types[name]
    
        interp_group = self.get_interpgrp()
        interp = find_element(interp_group, f".//interp[@xml:id='{name}']")
        if interp is None:
            interp = ET.Element("interp", attrib={"{http://www.w3.org/XML/1998/namespace}id":name})
            interp_group.append(interp)

        relation_type = RelationType(name=name, element=interp, description="")
        self.relation_types[name] = relation_type
        return relation_type

    def __str__(self):
        return str(self.path)

    def __repr__(self) -> str:
        return str(self)

    def write(self, output:str|Path):
        write_tei(self.tree, output)

    @property
    def language(self):
        return get_language(self.tree)
    
    def get_relation_types(self, categories_to_ignore:list[str]|None=None) -> list[RelationType]:
        interp_group = self.get_interpgrp()
        categories_to_ignore = categories_to_ignore or []
        
        relation_types = dict()
        assert interp_group is not None, "No interpGrp of type='transcriptional' found in TEI file."
        
        for interp in find_elements(interp_group, "./interp"):
            name = interp.attrib.get("{http://www.w3.org/XML/1998/namespace}id", "")
            if name in categories_to_ignore: continue

            description = extract_text(interp).strip()
            relation_types[name] = RelationType(name=name, element=interp, description=description)

        # get corresponding relations
        for category in relation_types.values():
            inverse_name = category.element.attrib.get("corresp", "")
            if inverse_name.startswith("#"):
                inverse_name = inverse_name[1:]

            if inverse_name in relation_types:
                inverse = relation_types[inverse_name]
                category.inverse = inverse
                if inverse.inverse is None:
                    inverse.inverse = category
                else:
                    assert inverse.inverse == category, f"Inverse category {inverse} already has an inverse {inverse.inverse}."

        return relation_types

    def get_classified_pairs(self, redundant:bool=True) -> list[Pair]:
        pairs = []
        for app in self.apps:
            pairs.extend(app.get_classified_pairs(redundant=redundant))

        return pairs

    def get_unclassified_pairs(self, redundant:bool=True) -> list[Pair]:
        pairs = []
        for app in self.apps:
            pairs.extend(app.get_unclassified_pairs(redundant=redundant))

        return pairs

    def print_classified_pairs(self, console:Console|None=None) -> None:
        console = console or Console()
        for relation_type in self.relation_types.values():
            console.rule(str(relation_type))
            console.print(relation_type.description, style="grey46")
            for pair in relation_type.pairs_sorted():
                pair.print(console)

            console.print("")

    def render_html(self, output:Path|None=None, all_apps:bool=False) -> str:
        from flask import Flask, request, render_template
        
        mapper = Mapper()
        app = Flask(__name__)

        with app.app_context():
            html = render_template('server.html', doc=self, mapper=mapper, all_apps=all_apps)
        
        if output:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(html)
        
        return html

    def flask_app(self, output:Path, all_apps:bool=False):
        from flask import Flask, request, render_template

        self.write(output)
        mapper = Mapper()
        
        app = Flask(__name__)

        @app.route("/")
        def root():
            return render_template('server.html', doc=self, mapper=mapper, all_apps=all_apps)

        @app.route("/api/relation-type", methods=['POST'])
        def api_relation_type():
            data = request.get_json()
            
            relation_type = mapper.obj(data['relation_type'])
            assert isinstance(relation_type, RelationType), f"Expected RelationType, got {type(relation_type)}"
            
            pair = mapper.obj(data['pair'])
            assert isinstance(pair, Pair), f"Expected Pair, got {type(pair)}"

            try:
                if data['operation'] == 'remove':
                    print('remove', relation_type)
                    pair.remove_type_with_inverse(relation_type)
                elif data['operation'] == 'add':
                    print('add', relation_type)
                    pair.add_type_with_inverse(relation_type)
                else:
                    raise ValueError(f"Unknown operation {data['operation']}")
                
                print('write', output)
                self.write(output)
                return "Success", 200           
            except Exception as e:  
                print(str(e))
                return str(e), 400

            return "Failed", 400
        
        @app.route("/api/desc", methods=['POST'])
        def desc():
            data = request.get_json()
                        
            pair = mapper.obj(data['pair'])
            assert isinstance(pair, Pair), f"Expected Pair, got {type(pair)}"

            try:
                if data['operation'] == 'remove':
                    pair.remove_description()
                elif data['operation'] == 'add':
                    pair.add_description(data['description'])   
                else:
                    raise ValueError(f"Unknown operation {data['operation']}")

                print('write', output)
                self.write(output)
                return "Success", 200           
            except Exception as e:  
                print(str(e))
                return str(e), 400

            return "Failed", 400

        return app
        # app.run(debug=True, use_reloader=True)

    def clean(self, output:Path|None=None):
        """ Cleans a TEI XML file for common errors. """

        # find all listRelation elements
        list_relations = find_elements(self.tree, ".//listRelation")
        for list_relation in list_relations:
            relations_so_far = set()
            for relation in find_elements(list_relation, ".//relation"):
                # make sure that relation elements have a # at the start of the ana attribute
                if not relation.attrib['ana'].startswith("#"):
                    relation.attrib['ana'] = f"#{relation.attrib['ana']}"
                
                relations_so_far.add( (relation.attrib['active'], relation.attrib['passive']) )
            
            # consolidate duplicate relations
            for active, passive in relations_so_far:
                relations = find_elements(list_relation, f".//relation[@active='{active}'][@passive='{passive}']")
                if len(relations) > 1:
                    analytic_set = set()
                    for relation in relations:
                        analytic_set.update(relation.attrib['ana'].split())

                    for relation in relations[1:]:
                        list_relation.remove(relation)

                    relations[0].attrib['ana'] = " ".join(sorted(analytic_set))
        
        if output:
            output = Path(output)
            output.parent.mkdir(parents=True, exist_ok=True)
            print("Writing to", output)
            self.write(output)     