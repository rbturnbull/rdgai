

class Mapper():
    def __init__(self):
        self.key_to_object = {}
        self.object_to_key = {}
    
    def key(self, object):
        if object in self.object_to_key:
            return self.object_to_key[object]
        
        key = str(object)
        # check if key is already in use and if it is, then make unique by adding number
        if key in self.key_to_object:
            i = 2
            while f"{key}_{i}" in self.key_to_object:
                i += 1
            key = f"{key}_{i}"

        self.key_to_object[key] = object
        self.object_to_key[object] = key
        return key

    def obj(self, key):
        return self.key_to_object.get(key)