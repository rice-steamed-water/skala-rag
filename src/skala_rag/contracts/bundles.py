"""Source payload closure for discovery and retrieval, not snapshot policy."""

from typing import Self

from pydantic import model_validator

from .candidates import Candidate
from .common import Contract, Text
from .sources import Chunk, Source


class SourceBundle(Contract):
    sources: dict[Text, Source]

    @model_validator(mode="after")
    def match_source_keys(self) -> Self:
        if any(key != source.source_id for key, source in self.sources.items()):
            raise ValueError("Source map key must match source_id")
        return self


class DiscoveryBundle(SourceBundle):
    candidates: list[Candidate]

    @model_validator(mode="after")
    def close_discovery_sources(self) -> Self:
        for candidate in self.candidates:
            if any(sid not in self.sources for sid in candidate.discovery_source_ids):
                raise ValueError("unresolved discovery Source payload")
        return self


class RetrievalBundle(SourceBundle):
    chunks: list[Chunk]

    @model_validator(mode="after")
    def close_chunk_sources(self) -> Self:
        if any(chunk.source_id not in self.sources for chunk in self.chunks):
            raise ValueError("unresolved Chunk Source payload")
        return self
