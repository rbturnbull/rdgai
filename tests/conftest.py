from pathlib import Path
import pytest
from rdgai.apparatus import Doc

TEST_DATA_DIR = Path(__file__).parent / 'test-data'


def make_fixture(path):
    name = path.stem.replace("-", "_").replace(".", "_")
    print(name)
    @pytest.fixture(name=name)
    def fixture_function():
        return Doc(path)
    
    globals()[name] = fixture_function
    return fixture_function


for path in TEST_DATA_DIR.glob("*.xml"):
    make_fixture(path)
    
