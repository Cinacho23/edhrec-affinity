import importlib.util
import json
from pathlib import Path

import brotli
import pytest
from edhrec_affinity.site_payload import project_build_report


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/pack_site_data.py"
spec = importlib.util.spec_from_file_location("pack_site_data", SCRIPT)
packer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packer)


def build_site(tmp_path):
    root = tmp_path / "dist"
    data = root / "data/latest"
    data.mkdir(parents=True)
    (data / "site_manifest.json").write_text('{"build_style_export":{"index_file":"build-styles/index.json"}}')
    (data / "build-styles").mkdir()
    (data / "build-styles/index.json").write_text('{"commanders":[{"commander_slug":"commander"}]}')
    cards = [{"name": f"Card {i}", "average_inclusion": 0.3333333333333333} for i in range(100)]
    deck = {"cards": cards, "primer": "[[Éowyn, Shieldmaiden]]", "power_level": 6.71}
    report = {"deck_analysis": {"packages": [{"generated_deck": deck}],
              "alternative_models": [{"components": i, "packages": [{"generated_deck": deck}]} for i in range(1, 28)]}}
    raw = json.dumps(report, ensure_ascii=False).encode()
    (data / "build-styles/commander.json").write_bytes(raw)
    (root / "index.html").write_text("<html>App</html>")
    return root, data, raw


def test_pack_preserves_every_byte_model_card_and_primer_and_keeps_operational_indexes(tmp_path):
    root, data, raw = build_site(tmp_path)
    original = tmp_path / "checkpoint.json"
    original.write_bytes(raw)
    result = packer.pack_site(root)
    expected = project_build_report(json.loads(raw))
    restored = json.loads(brotli.decompress((data / "build-styles/commander.json.br").read_bytes()))
    assert restored == expected
    assert restored["deck_analysis"] == json.loads(raw)["deck_analysis"]
    assert not (data / "build-styles/commander.json").exists()
    assert original.read_bytes() == raw
    for name in ["site_manifest.json", "build-styles/index.json"]:
        assert brotli.decompress((data / (name + ".br")).read_bytes()) == (data / name).read_bytes()
    assert result["bytes"] < result["before_bytes"]
    assert packer.pack_site(root)["bytes"] == result["bytes"]  # Safe to repeat a completed packaging step.


def test_size_guard_checks_actual_stored_files_and_archive_allowance(tmp_path):
    root, _, _ = build_site(tmp_path)
    with pytest.raises(ValueError, match="exceeds the safe"):
        packer.pack_site(root, max_bytes=100)


def test_refuses_linked_payload_before_modifying_any_data(tmp_path):
    root, data, raw = build_site(tmp_path)
    (root / "linked.json").symlink_to(data / "build-styles/commander.json")
    with pytest.raises(ValueError, match="contains a link"):
        packer.pack_site(root)
    assert (data / "build-styles/commander.json").read_bytes() == raw


@pytest.mark.parametrize("workflow", ["deploy-frontend.yml", "update-data-and-deploy-full.yml"])
def test_both_publication_workflows_enable_decoder_and_verify_size_before_upload(workflow):
    text = (SCRIPT.parents[1] / ".github/workflows" / workflow).read_text()
    assert "VITE_SITE_DATA_ENCODING: br" in text
    assert text.index("npm run build") < text.index("scripts/pack_site_data.py") < text.index("actions/upload-pages-artifact@")
