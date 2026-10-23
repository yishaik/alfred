import importlib.util
from pathlib import Path
from unittest.mock import patch


def test_manual_sender_import_neither_reads_credentials_nor_sends():
    spec = importlib.util.spec_from_file_location("manual_sender", Path(__file__).parents[1] / "send_test.py")
    module = importlib.util.module_from_spec(spec)
    with patch("pathlib.Path.read_text", side_effect=AssertionError("credential read")), patch("urllib.request.urlopen", side_effect=AssertionError("network")):
        spec.loader.exec_module(module)
    assert callable(module.main)
