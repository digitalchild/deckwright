"""Shared fixtures for the deckwright test suite."""

from __future__ import annotations

import importlib
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

from deckwright import pack

REPO_ROOT = Path(__file__).resolve().parents[1]
ASSETS = REPO_ROOT / "examples" / "assets"

sys.path.insert(0, str(REPO_ROOT / "scripts"))


@pytest.fixture
def img() -> str:
    """Absolute path to a local landscape test image."""
    return str((ASSETS / "landscape.jpg").resolve())


@pytest.fixture(scope="session", autouse=True)
def _template_env():
    """Point deckwright at a tmp templates folder for the whole test session.

    The default test pack is the neutral "sample" pack that ships with deckwright.
    """
    tmp = tempfile.mkdtemp(prefix="deckwright-test-templates-")
    empty_config = tempfile.mkdtemp(prefix="deckwright-test-config-")

    old_templates = os.environ.get("DECKWRIGHT_TEMPLATES")
    old_template = os.environ.get("DECKWRIGHT_TEMPLATE")
    old_config_dir = pack.CONFIG_DIR

    os.environ["DECKWRIGHT_TEMPLATES"] = tmp
    os.environ["DECKWRIGHT_TEMPLATE"] = "sample"
    pack.CONFIG_DIR = Path(empty_config)

    yield

    if old_templates is None:
        os.environ.pop("DECKWRIGHT_TEMPLATES", None)
    else:
        os.environ["DECKWRIGHT_TEMPLATES"] = old_templates
    if old_template is None:
        os.environ.pop("DECKWRIGHT_TEMPLATE", None)
    else:
        os.environ["DECKWRIGHT_TEMPLATE"] = old_template
    pack.CONFIG_DIR = old_config_dir
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.rmtree(empty_config, ignore_errors=True)


@pytest.fixture
def template():
    return pack.load("sample")


@pytest.fixture
def output_dir(tmp_path, monkeypatch):
    """Point DECKWRIGHT_OUTPUT_DIR at a tmp dir before deckwright.service/api read it.

    ``service`` reads the env var at import time, so we set the env var first
    and then reload the modules that captured it.
    """
    monkeypatch.setenv("DECKWRIGHT_OUTPUT_DIR", str(tmp_path))

    import deckwright.service as service
    importlib.reload(service)

    import deckwright.api as api
    importlib.reload(api)

    yield tmp_path
