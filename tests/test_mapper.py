from rdgai.mapper import Mapper

class StrObject():
    def __init__(self, string):
        self.string = string
        
    def __str__(self):
        return self.string
    

def test_mapper_basic():
    mapper = Mapper()
    obj1 = "object1"
    obj2 = "object2"

    key1 = mapper.key(obj1)
    key2 = mapper.key(obj2)

    assert key1 == "object1"
    assert key2 == "object2"
    assert mapper.obj(key1) == obj1
    assert mapper.obj(key2) == obj2


def test_mapper_duplicate_key():
    mapper = Mapper()
    obj1 = StrObject("object")
    obj2 = StrObject("object")
    obj3 = StrObject("object")

    key1 = mapper.key(obj1)
    key2 = mapper.key(obj2)
    key3 = mapper.key(obj3)

    assert key1 == "object"
    assert key2 == "object_2"
    assert key3 == "object_3"

    assert mapper.obj(key1) == obj1
    assert mapper.obj(key2) == obj2
    assert mapper.obj(key3) == obj3


def test_mapper_existing_key():
    mapper = Mapper()
    obj1 = StrObject("object")
    obj2 = StrObject("object")

    mapper.key(obj1)
    key2 = mapper.key(obj2)

    assert key2 == "object_2"

def test_mapper_no_object_for_key():
    mapper = Mapper()
    assert mapper.obj("nonexistent") is None


def test_mapper_unique_objects():
    mapper = Mapper()
    obj1 = "object1"
    obj2 = "object2"
    obj3 = "object3"

    key1 = mapper.key(obj1)
    key2 = mapper.key(obj2)
    key3 = mapper.key(obj3)

    assert key1 == "object1"
    assert key2 == "object2"
    assert key3 == "object3"
    assert mapper.obj(key1) == obj1
    assert mapper.obj(key2) == obj2
    assert mapper.obj(key3) == obj3
