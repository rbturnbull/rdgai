from rdgai.languages import convert_language_code

def test_convert_language_code():
    assert convert_language_code("eng") == "English"
    assert convert_language_code("fra") == "French"
    assert convert_language_code("deu") == "German"
    assert convert_language_code("spa") == "Spanish"
    assert convert_language_code("English") == "English"