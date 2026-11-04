import importlib.util
import json
from pathlib import Path
import sys

import brotli
import httpx
import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sys.modules.setdefault("pack_site_data", load_script("pack_site_data"))
prepare = load_script("prepare_cloudflare_data")
verify = load_script("verify_cloudflare_data")


def exported_data(tmp_path):
    data = tmp_path / "public/data/latest"
    for directory in [data, data / "commanders", data / "build-styles"]:
        directory.mkdir(parents=True, exist_ok=True)
    files = {"site_manifest.json": {"snapshot_date": "2026-11-03"}, "commanders/index.json": [],
             "build-styles/index.json": {"commanders": [{"commander_slug": "test"}]},
             "build-styles/test.json": {"deck_analysis": {"packages": [], "alternative_models": []}}}
    for name, contents in files.items():
        (data / name).write_text(json.dumps(contents))
    return data, files


def test_separates_large_data_and_preserves_operational_indexes_and_source_checkpoint(tmp_path):
    data, original = exported_data(tmp_path)
    checkpoint = tmp_path / "checkpoint.json"
    checkpoint.write_text(json.dumps(original))
    cloud = tmp_path / "cloudflare"
    prepare.prepare_data(data, cloud, build_id="123-1")
    for name in ["site_manifest.json", "commanders/index.json", "build-styles/index.json"]:
        assert json.loads((data / name).read_text()) == original[name]
    assert not (data / "build-styles/test.json").exists()
    report = json.loads(brotli.decompress((cloud / "data/latest/build-styles/test.json.br").read_bytes()))
    assert report["deck_analysis"] == original["build-styles/test.json"]["deck_analysis"]
    assert json.loads(checkpoint.read_text()) == original
    assert "Access-Control-Allow-Origin: *" in (cloud / "_headers").read_text()
    assert "Content-Encoding: br" not in (cloud / "_headers").read_text()


def test_cloudflare_free_limits_fail_before_any_upload(tmp_path):
    (tmp_path / "large.json.br").write_bytes(b"12345")
    with pytest.raises(ValueError, match="split before publishing"):
        prepare.validate_assets(tmp_path, max_file_bytes=4)
    with pytest.raises(ValueError, match="supports 0 assets"):
        prepare.validate_assets(tmp_path, max_files=0)


def test_large_plain_index_stays_on_pages_and_cloudflare_keeps_complete_encoded_index(tmp_path):
    data, _ = exported_data(tmp_path)
    index = {"commanders": [{"commander_slug": f"commander-{i}", "tags": ["tokens", "counters"]}
                            for i in range(100)]}
    # Exercise the actual per-file decision using a small test limit.
    (data / "build-styles/index.json").write_text(json.dumps(index))
    (data / "commanders/index.json").write_text(json.dumps(index))
    cloud = tmp_path / "cloud"
    prepare.prepare_data(data, cloud, build_id="123-1", max_asset_bytes=2048)
    for name in ["build-styles/index.json", "commanders/index.json"]:
        assert json.loads((data / name).read_text()) == index
        assert not (cloud / "data/latest" / name).exists()
        encoded = cloud / "data/latest" / (name + ".br")
        assert json.loads(brotli.decompress(encoded.read_bytes())) == index
    prepare.validate_assets(cloud, max_file_bytes=2048)

    def serve(request):
        path = cloud / request.url.path.lstrip("/")
        return httpx.Response(200, content=path.read_bytes(), headers={"Access-Control-Allow-Origin": "*"})

    with httpx.Client(transport=httpx.MockTransport(serve)) as client:
        verify.verify_data(cloud, "https://data.example", client=client)


def test_unsafe_staging_and_links_are_rejected_before_moving_data(tmp_path):
    data, _ = exported_data(tmp_path)
    with pytest.raises(ValueError, match="fresh, separate"):
        prepare.prepare_data(data, data / "nested", build_id="123")
    (data / "link.json").symlink_to(data / "site_manifest.json")
    with pytest.raises(ValueError, match="contains a link"):
        prepare.prepare_data(data, tmp_path / "cloud", build_id="123")
    assert (data / "site_manifest.json").is_file()


def test_data_is_pinned_to_worker_version_instead_of_mutable_production_alias():
    url = "https://edhrec-affinity-data.example.workers.dev"
    output = "Current Version ID: abcdef01-2345-6789-abcd-1234567890ab"
    assert verify.version_url(url, output) == "https://abcdef01-edhrec-affinity-data.example.workers.dev"
    with pytest.raises(ValueError, match="unpinned"):
        verify.version_url(url, "No version ID")
    with pytest.raises(ValueError, match="deployment URL"):
        verify.version_url("https://unrelated.example/", output)


@pytest.mark.parametrize("fault", [None, "cors", "wrong-build", "missing"])
def test_public_verification_checks_identity_cors_and_real_report_bytes(tmp_path, fault):
    data, _ = exported_data(tmp_path)
    cloud = tmp_path / "cloud"
    prepare.prepare_data(data, cloud, build_id="123-1")

    def serve(request):
        assert request.headers["origin"] == verify.ORIGIN
        path = cloud / request.url.path.lstrip("/")
        headers = {} if fault == "cors" else {"Access-Control-Allow-Origin": "*"}
        if fault == "missing":
            return httpx.Response(404, headers=headers)
        content = b"wrong" if fault == "wrong-build" else path.read_bytes()
        return httpx.Response(200, content=content, headers=headers)

    with httpx.Client(transport=httpx.MockTransport(serve)) as client:
        if fault:
            with pytest.raises((ValueError, httpx.HTTPStatusError)):
                verify.verify_data(cloud, "https://data.example", client=client)
        else:
            verify.verify_data(cloud, "https://data.example", client=client)


def test_both_publishers_use_one_cloudflare_upload_before_building_ui():
    for name in ["deploy-frontend.yml", "update-data-and-deploy-full.yml"]:
        workflow = (SCRIPTS.parent / ".github/workflows" / name).read_text()
        assert workflow.index("./.github/actions/publish-static-data") < workflow.index("run: npm run build")
        assert "VITE_SITE_DATA_BASE_URL: ${{ steps.static_data.outputs.data-base-url }}" in workflow
