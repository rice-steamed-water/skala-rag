from pathlib import Path

from skala_rag.live_exploration import run

if __name__ == "__main__":
    run(
        root=Path.cwd(),
        artifacts_root=Path(__file__).parent,
        approval_reference=(
            "2026-09-30 user chat: 20min/50 requests/30 LLM/USD3 approved"
        ),
    )
