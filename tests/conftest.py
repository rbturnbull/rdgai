from pathlib import Path
import pytest
from rdgai.apparatus import Doc

TEST_DATA_DIR = Path(__file__).parent / 'test-data'


@pytest.fixture
def minimal_doc():
    return Doc(TEST_DATA_DIR/"minimal.xml")


@pytest.fixture
def arb():
    return Doc(TEST_DATA_DIR/"arb.xml")

