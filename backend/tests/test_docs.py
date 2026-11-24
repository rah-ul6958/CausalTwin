"""The README and docs show the numbers in the result files: no hand-typed or stale results."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "update_readme_results.py"


def _load_script() -> ModuleType:
    """Import scripts/update_readme_results.py (scripts are not a package)."""
    spec = importlib.util.spec_from_file_location("update_readme_results", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_generated_result_blocks_are_up_to_date() -> None:
    """Every generated block in README.md and docs/ equals what the result files give today."""
    script = _load_script()
    blocks = script.render_all()
    checked = 0
    for path in script.TARGET_FILES:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        checked += len(script.BLOCK_PATTERN.findall(text))
        assert script.refresh_text(text, blocks) == text, f"{path.name} is stale: run scripts/update_readme_results.py"
    assert checked >= len(blocks), "every results block should appear at least once"


def test_blocks_contain_only_computed_numbers() -> None:
    """Spot checks: the headline success rates and the drift table come from the result files."""
    script = _load_script()
    blocks = script.render_all()
    assert "%" in blocks["headline"] and "predict-then-optimize" in blocks["headline"]
    assert "B3: predict-then-optimize" in blocks["benchmark"] and "B1: naive SHAP" in blocks["benchmark"]
    assert "B2: actionable SHAP" in blocks["straw_man"] and "CausalTwin" in blocks["straw_man"]
    assert blocks["drift"].count("|") > 10 and "Fault-free runs" in blocks["drift"]
    assert "Within budget" in blocks["timings"]
