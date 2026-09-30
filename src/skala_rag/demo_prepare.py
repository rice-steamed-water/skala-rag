"""One-time source/model preparation; never called by the web generation path."""

import hashlib
import json
import re
from pathlib import Path

import httpx
from huggingface_hub import snapshot_download

from skala_rag.agents.m2_local_rag import LocalRAG
from skala_rag.rag.index_validation import prepare_corpus
from skala_rag.rag.local_bge_validation import MODEL, REVISION, run


def prepare_demo(*, root: Path):
    root = root.resolve()
    sources = json.loads(
        (root / "data/manifests/issue49-source-snapshots.json").read_text()
    )
    for source in sources.values():
        path = (root / source["local_path"]).resolve()
        if not path.is_relative_to((root / "data/local").resolve()):
            raise ValueError("SOURCE_PATH_INVALID")
        if not re.fullmatch(
            r"https://arxiv\.org/pdf/\d{4}\.\d{4,5}v\d+", source["url"]
        ):
            raise ValueError("SOURCE_URL_INVALID")
        if path.exists():
            content = path.read_bytes()
        else:
            with httpx.Client(
                timeout=120, follow_redirects=False, trust_env=False
            ) as client:
                with client.stream("GET", source["url"]) as response:
                    response.raise_for_status()
                    blocks = bytearray()
                    for block in response.iter_bytes():
                        blocks.extend(block)
                        if len(blocks) > 30_000_000:
                            raise ValueError("SOURCE_TOO_LARGE")
                    content = bytes(blocks)
        if not content.startswith(b"%PDF-") or (
            "sha256:" + hashlib.sha256(content).hexdigest() != source["content_hash"]
        ):
            raise ValueError("SOURCE_HASH_MISMATCH")
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
    prepare_corpus(root, model_id=MODEL, model_revision=REVISION)
    model_path = root / "data/local/models/bge-m3-5617a9f"
    output = root / "outputs/issue180-local-bge"
    if not output.exists():
        snapshot_download(
            repo_id=MODEL,
            revision=REVISION,
            local_dir=str(model_path),
            token=False,
            allow_patterns=[
                "pytorch_model.bin",
                "config.json",
                "tokenizer.json",
                "tokenizer_config.json",
                "special_tokens_map.json",
                "sentencepiece.bpe.model",
                "modules.json",
                "sentence_bert_config.json",
                "1_Pooling/config.json",
                "README.md",
            ],
        )
        run(
            root,
            model_path,
            output,
            "robot generalization limitations pi0 pi0.5",
            "cpu",
        )
    # Reuse only if source/index/model receipt validation succeeds. Never overwrite
    # a partial/invalid preparation or relabel an old report as a new generation.
    LocalRAG(
        root=root,
        model_path=model_path,
        store_path=output / "index.sqlite",
        receipt_path=output / "validation.json",
    )
    return output
