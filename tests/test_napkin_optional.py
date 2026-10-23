import subprocess
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from tgbridge import napkin_store


def test_missing_napkin_is_reported_as_unavailable():
    with patch.object(napkin_store, "_base_cmd", return_value=None):
        assert not napkin_store.available()
        with pytest.raises(napkin_store.NapkinError, match="not found"):
            napkin_store.search("unused", "query")


def test_napkin_transport_parses_json_and_keeps_vault_scope(tmp_path):
    with patch.object(napkin_store, "_base_cmd", return_value=["napkin"]), patch.object(napkin_store.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout='{"results":[{"file":"one.md"}]}', stderr="")) as run:
        assert napkin_store.search(str(tmp_path), "query") == [{"file":"one.md"}]
        assert run.call_args.kwargs["cwd"] == str(tmp_path)
        assert "--vault" in run.call_args.args[0]


@pytest.mark.parametrize("result", [SimpleNamespace(returncode=1, stdout="", stderr="failed"), SimpleNamespace(returncode=0, stdout="bad JSON", stderr="")])
def test_napkin_errors_are_not_silently_successful(result):
    with patch.object(napkin_store, "_base_cmd", return_value=["napkin"]), patch.object(napkin_store.subprocess, "run", return_value=result):
        with pytest.raises(napkin_store.NapkinError):
            napkin_store.search("unused", "query")
