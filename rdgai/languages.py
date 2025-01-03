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