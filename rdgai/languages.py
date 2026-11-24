from functools import cache
from pathlib import Path

@cache
def read_language_codes_yaml_cached() -> dict[str, str]:
    path = Path(__file__).parent / "data/language-subtag-registry.yaml"

    assert path.exists(), f"File not found: {path}"

    import yaml    
    with open(path, encoding='utf8') as f:
        result = yaml.safe_load(f)
    return result


def convert_language_code(code:str) -> str:
    codes = read_language_codes_yaml_cached()
    if code in codes:
        return codes[code]
    
    return code

# ---------------------------------------------------------------------------
# language-specific spelling rules (used only when the file's language matches)
# ---------------------------------------------------------------------------

def greek_trivially_respelled(first:str, second:str) -> bool:
    """ Greek: one word with a movable ν / ς or an elided final vowel (ειπεν / ειπε, αλλα / αλλ). """
    short, long = sorted((first, second), key=len)
    return len(long) == len(short) + 1 and long.startswith(short) and long[-1] in "νσαεοι"


SPELLING_RULES = {
    "grc": greek_trivially_respelled,   # Ancient Greek
    "el": greek_trivially_respelled,    # Greek
}


def spelling_rule(language:str):
    """ The trivial-respelling rule for a language code (e.g. 'grc', 'grc-Grek'), or None. """
    code = (language or "").split("-")[0].lower()
    return SPELLING_RULES.get(code)


def trivially_respelled(first:str, second:str, language:str="") -> bool:
    """ The same word after normalization, or a trivial respelling under the rules for the file's language (if any). """
    from .search import normalize
    first, second = normalize(first), normalize(second)
    if first == second:
        return True
    rule = spelling_rule(language)
    return bool(rule and rule(first, second))
