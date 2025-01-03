from rdgai.languages import convert_language_code

def test_convert_language_code():
    assert convert_language_code("en") == "English"
    assert convert_language_code("fr") == "French"
    assert convert_language_code("de") == "German"
    assert convert_language_code("es") == "Spanish, Castilian"
    assert convert_language_code("English") == "English"