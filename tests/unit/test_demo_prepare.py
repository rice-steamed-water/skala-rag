import json

import pytest


def test_preparation_rejects_escaping_manifest_before_fetch(tmp_path):
    from skala_rag.demo_prepare import prepare_demo

    manifest = tmp_path / "data/manifests/issue49-source-snapshots.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "test": {
                    "local_path": "../escape.pdf",
                    "url": "https://arxiv.org/pdf/2410.24164v4",
                }
            }
        )
    )
    with pytest.raises(ValueError, match="SOURCE_PATH_INVALID"):
        prepare_demo(root=tmp_path)
