import argparse
from pathlib import Path

import requests


def main() -> int:
    parser = argparse.ArgumentParser(description="Create or update an OmniVoice clone profile")
    parser.add_argument("profile_id", help="profile id, e.g. sohee")
    parser.add_argument("ref_audio", help="path to reference wav file")
    parser.add_argument("--ref-text", default="", help="reference transcript text")
    parser.add_argument("--server", default="http://127.0.0.1:8880", help="OmniVoice server base URL")
    parser.add_argument("--overwrite", action="store_true", help="overwrite existing profile")
    args = parser.parse_args()

    audio_path = Path(args.ref_audio)
    if not audio_path.exists():
        raise SystemExit(f"ref audio not found: {audio_path}")

    with audio_path.open("rb") as f:
        response = requests.post(
            f"{args.server.rstrip('/')}/v1/voices/profiles",
            data={
                "profile_id": args.profile_id,
                "ref_text": args.ref_text,
                "overwrite": str(args.overwrite).lower(),
            },
            files={"ref_audio": (audio_path.name, f, "audio/wav")},
            timeout=300,
        )

    print(response.status_code)
    print(response.text)
    response.raise_for_status()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
