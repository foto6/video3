from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .gemini_visual_critic import (
    GeminiNativeVideoCriticAdapter,
    critique_candidate_with_gemini_native_video,
)
from .visual_critic import parse_visual_critic_input


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Optional live Gemini native-video visual critic smoke test. "
            "Requires explicit GEMINI_API_KEY."
        )
    )
    parser.add_argument(
        "--video",
        required=True,
        help="Path to the finished MP4 whose SHA-256 matches the critic input.",
    )
    parser.add_argument(
        "--critic-input",
        required=True,
        help="Path to a growth.visual_critic.r15.v1 input JSON file.",
    )
    args = parser.parse_args()

    if not os.environ.get("GEMINI_API_KEY"):
        parser.error(
            "GEMINI_API_KEY is required; live Gemini smoke is disabled by default"
        )

    input_path = Path(args.critic_input)
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    critic_input = parse_visual_critic_input(payload)
    adapter = GeminiNativeVideoCriticAdapter.from_environment(
        video_path=Path(args.video)
    )
    if adapter is None:
        parser.error("Gemini provider is disabled")
    report = critique_candidate_with_gemini_native_video(
        critic_input,
        adapter=adapter,
    )
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
