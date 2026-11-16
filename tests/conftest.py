import os

# Typer forces coloured "terminal" output when GITHUB_ACTIONS, FORCE_COLOR or PY_COLORS is set, so on CI the --help
# text the tests check is full of escape codes that split option names apart. It reads this when it is first imported,
# so it is set here, before any test imports rdgai (and with it Typer). The program itself is unaffected.
os.environ["_TYPER_FORCE_DISABLE_TERMINAL"] = "1"
# and Rich's own console colours its output when FORCE_COLOR is set (some CI setups set it): plain text for the tests
for variable in ("FORCE_COLOR", "PY_COLORS"):
    os.environ.pop(variable, None)

from pathlib import Path  # noqa: E402
import pytest  # noqa: E402
from rdgai.apparatus import Doc  # noqa: E402

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
    
