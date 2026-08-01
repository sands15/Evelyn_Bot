from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = next(
    path
    for path in Path(__file__).resolve().parents
    if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.runtime_source_identity import (  # noqa: E402
    CONTAINER_RUNTIME_ROLES,
    RUNTIME_SOURCE_IDENTITY_SCHEMA,
    runtime_source_identity,
    source_identities_compatible,
)


REVISION_40 = "a" * 40
REVISION_64 = "b" * 64


def container_environment(
    *,
    role: str = "bot_api",
    image_revision: str = REVISION_40,
    expected_revision: str = REVISION_40,
) -> dict[str, str]:
    return {
        "EVELYN_RUNTIME_ROLE": role,
        "EVELYN_IMAGE_SOURCE_REVISION": image_revision,
        "EVELYN_EXPECTED_SOURCE_REVISION": expected_revision,
    }


class RuntimeSourceIdentityTests(unittest.TestCase):
    def test_empty_environment_is_ready_development_identity(self) -> None:
        identity = runtime_source_identity({})

        self.assertEqual(
            identity,
            {
                "schema": RUNTIME_SOURCE_IDENTITY_SCHEMA,
                "role": "development",
                "mode": "development",
                "state": "development",
                "ready": True,
                "aligned": True,
                "verified": False,
                "imageSourceRevision": None,
                "expectedSourceRevision": None,
                "reasonCode": "development_source_identity",
            },
        )

    def test_default_environment_reader_uses_only_contract_values(self) -> None:
        environment = container_environment(role="control_page")
        environment["UNRELATED_SECRET"] = "must-not-be-public"
        with patch.dict(os.environ, environment, clear=True):
            identity = runtime_source_identity()

        self.assertEqual(identity["role"], "control_page")
        self.assertTrue(identity["ready"])
        self.assertNotIn("must-not-be-public", json.dumps(identity))

    def test_each_container_role_requires_exact_aligned_revision(self) -> None:
        for role in sorted(CONTAINER_RUNTIME_ROLES):
            with self.subTest(role=role):
                identity = runtime_source_identity(
                    container_environment(role=role)
                )
                self.assertEqual(identity["role"], role)
                self.assertEqual(identity["mode"], "container")
                self.assertEqual(identity["state"], "aligned")
                self.assertTrue(identity["ready"])
                self.assertTrue(identity["aligned"])
                self.assertTrue(identity["verified"])
                self.assertEqual(
                    identity["reasonCode"],
                    "source_revision_aligned",
                )

    def test_valid_64_character_revision_is_ready(self) -> None:
        identity = runtime_source_identity(
            container_environment(
                role="discord_bot",
                image_revision=REVISION_64,
                expected_revision=REVISION_64,
            )
        )

        self.assertTrue(identity["ready"])
        self.assertEqual(identity["imageSourceRevision"], REVISION_64)

    def test_case_difference_is_an_exact_mismatch(self) -> None:
        identity = runtime_source_identity(
            container_environment(
                image_revision="A" * 40,
                expected_revision="a" * 40,
            )
        )

        self.assertEqual(identity["state"], "mismatch")
        self.assertFalse(identity["ready"])
        self.assertFalse(identity["aligned"])
        self.assertTrue(identity["verified"])
        self.assertEqual(
            identity["reasonCode"],
            "source_revision_mismatch",
        )

    def test_different_valid_revisions_are_mismatch(self) -> None:
        identity = runtime_source_identity(
            container_environment(expected_revision="c" * 40)
        )

        self.assertEqual(identity["state"], "mismatch")
        self.assertFalse(identity["ready"])
        self.assertEqual(identity["imageSourceRevision"], REVISION_40)
        self.assertEqual(identity["expectedSourceRevision"], "c" * 40)

    def test_missing_container_revision_is_unverified(self) -> None:
        cases = (
            {
                "EVELYN_RUNTIME_ROLE": "bot_api",
            },
            {
                "EVELYN_RUNTIME_ROLE": "bot_api",
                "EVELYN_IMAGE_SOURCE_REVISION": REVISION_40,
            },
            {
                "EVELYN_RUNTIME_ROLE": "bot_api",
                "EVELYN_EXPECTED_SOURCE_REVISION": REVISION_40,
            },
        )
        for environment in cases:
            with self.subTest(environment=environment):
                identity = runtime_source_identity(environment)
                self.assertEqual(identity["state"], "unverified")
                self.assertFalse(identity["ready"])
                self.assertFalse(identity["verified"])
                self.assertIsNone(identity["imageSourceRevision"])
                self.assertIsNone(identity["expectedSourceRevision"])
                self.assertEqual(
                    identity["reasonCode"],
                    "source_revision_missing",
                )

    def test_invalid_revision_is_not_copied_to_public_payload(self) -> None:
        invalid_values = (
            "a" * 39,
            "a" * 41,
            "g" * 40,
            f" {REVISION_40}",
            "private-api-token-value",
        )
        for invalid in invalid_values:
            with self.subTest(invalid=invalid):
                identity = runtime_source_identity(
                    container_environment(image_revision=invalid)
                )
                serialized = json.dumps(identity)
                self.assertEqual(identity["state"], "unverified")
                self.assertEqual(
                    identity["reasonCode"],
                    "source_revision_invalid",
                )
                self.assertIsNone(identity["imageSourceRevision"])
                self.assertIsNone(identity["expectedSourceRevision"])
                self.assertNotIn(invalid, identity.values())
                if invalid == "private-api-token-value":
                    self.assertNotIn(invalid, serialized)

    def test_missing_or_invalid_role_is_unverified_and_not_public(self) -> None:
        missing = runtime_source_identity(
            {
                "EVELYN_IMAGE_SOURCE_REVISION": REVISION_40,
                "EVELYN_EXPECTED_SOURCE_REVISION": REVISION_40,
            }
        )
        invalid = runtime_source_identity(
            container_environment(role="secret-role-value")
        )

        self.assertEqual(missing["reasonCode"], "runtime_role_missing")
        self.assertEqual(missing["role"], "unknown")
        self.assertFalse(missing["ready"])
        self.assertEqual(invalid["reasonCode"], "runtime_role_invalid")
        self.assertEqual(invalid["role"], "unknown")
        self.assertNotIn("secret-role-value", json.dumps(invalid))

    def test_compatible_container_identities_may_have_different_roles(self) -> None:
        local = runtime_source_identity(
            container_environment(role="bot_api")
        )
        remote = runtime_source_identity(
            container_environment(role="control_page")
        )

        self.assertTrue(source_identities_compatible(local, remote))
        self.assertTrue(source_identities_compatible(remote, local))

    def test_compatibility_requires_same_exact_revision(self) -> None:
        local = runtime_source_identity(container_environment())
        different = runtime_source_identity(
            container_environment(
                role="discord_bot",
                image_revision="A" * 40,
                expected_revision="A" * 40,
            )
        )

        self.assertFalse(source_identities_compatible(local, different))

    def test_unverified_mismatch_and_tampered_payloads_are_incompatible(self) -> None:
        ready = runtime_source_identity(container_environment())
        unverified = runtime_source_identity(
            {"EVELYN_RUNTIME_ROLE": "control_page"}
        )
        mismatch = runtime_source_identity(
            container_environment(expected_revision="c" * 40)
        )
        tampered = dict(ready)
        tampered["imageSourceRevision"] = "not-a-revision"

        self.assertFalse(source_identities_compatible(ready, unverified))
        self.assertFalse(source_identities_compatible(ready, mismatch))
        self.assertFalse(source_identities_compatible(ready, tampered))
        self.assertFalse(source_identities_compatible({}, ready))
        self.assertFalse(source_identities_compatible(ready, None))  # type: ignore[arg-type]

    def test_development_identity_is_only_compatible_with_development(self) -> None:
        first = runtime_source_identity({})
        second = runtime_source_identity({})
        container = runtime_source_identity(container_environment())

        self.assertTrue(source_identities_compatible(first, second))
        self.assertFalse(source_identities_compatible(first, container))

    def test_compatibility_rejects_forged_ready_flags(self) -> None:
        identity = runtime_source_identity(container_environment())
        for field, value in (
            ("schema", "runtime_source_identity.v0"),
            ("state", "mismatch"),
            ("ready", 1),
            ("aligned", 1),
            ("verified", 1),
            ("reasonCode", "source_revision_mismatch"),
        ):
            with self.subTest(field=field):
                forged = dict(identity)
                forged[field] = value
                self.assertFalse(
                    source_identities_compatible(identity, forged)
                )


if __name__ == "__main__":
    unittest.main()
