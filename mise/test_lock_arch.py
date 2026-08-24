#!/usr/bin/env python3
"""Tests for lock-arch.py, the lock architecture-agreement check."""

import importlib.util
import pathlib
import re
import sys
import tempfile
import unittest

_SPEC = importlib.util.spec_from_file_location(
    "lock_arch", pathlib.Path(__file__).with_name("lock-arch.py")
)
if _SPEC is None or _SPEC.loader is None:  # pragma: no cover - import plumbing
    msg = "cannot load lock-arch.py"
    raise RuntimeError(msg)
lock_arch = importlib.util.module_from_spec(_SPEC)
sys.modules["lock_arch"] = lock_arch
_SPEC.loader.exec_module(lock_arch)


class TestAssetArch(unittest.TestCase):
    """The architecture a release asset's filename claims."""

    def test_real_org_asset_names(self) -> None:
        """Every spelling the org's own locks actually contain."""
        cases = {
            "wasm-pack-v0.15.0-aarch64-apple-darwin.tar.gz": "arm64",
            "wasm-pack-v0.15.0-x86_64-apple-darwin.tar.gz": "x64",
            "committed-v1.1.11-x86_64-apple-darwin.tar.gz": "x64",
            "committed-v1.1.11-aarch64-unknown-linux-musl.tar.gz": "arm64",
            "actionlint_1.7.12_darwin_arm64.tar.gz": "arm64",
            "cargo-deny-0.20.2-aarch64-apple-darwin.tar.gz": "arm64",
        }
        for asset, want in cases.items():
            with self.subTest(asset=asset):
                self.assertEqual(lock_arch.asset_arch(asset), want)

    def test_longest_token_wins(self) -> None:
        """`x86_64` must not be read as the 32-bit `x86` it contains."""
        self.assertEqual(lock_arch.asset_arch("tool-x86_64-linux.tar.gz"), "x64")

    def test_no_architecture_named(self) -> None:
        """A name carrying no architecture is unjudgeable, not a mismatch."""
        self.assertIsNone(lock_arch.asset_arch("shellcheck-v0.11.0.zip"))
        self.assertIsNone(lock_arch.asset_arch("ruby-4.0.6.macos.tar.gz"))


class TestPlatformFamilies(unittest.TestCase):
    """The OS and architecture a mise platform key declares."""

    def test_keys_the_org_locks_use(self) -> None:
        """Including the ABI-suffixed forms, whose suffix is not an arch."""
        cases = {
            "macos-arm64": ("macos", "arm64"),
            "macos-x64": ("macos", "x64"),
            "linux-arm64-musl": ("linux", "arm64"),
            "linux-x64-musl": ("linux", "x64"),
            "windows-x64": ("windows", "x64"),
        }
        for key, want in cases.items():
            with self.subTest(key=key):
                self.assertEqual(lock_arch.platform_families(key), want)

    def test_unrecognised_key_is_not_evidence(self) -> None:
        """An unknown OS or a key with no architecture yields nothing."""
        self.assertIsNone(lock_arch.platform_families("plan9-arm64"))
        self.assertIsNone(lock_arch.platform_families("linux"))


class TestNativeRegex(unittest.TestCase):
    """The expression the caller matches against a real asset list."""

    def test_matches_both_naming_orders(self) -> None:
        """Architecture-first and architecture-last are both real."""
        pattern = lock_arch.native_regex("macos", "arm64")
        self.assertRegex("wasm-pack-v0.15.0-aarch64-apple-darwin.tar.gz", pattern)
        self.assertRegex("actionlint_1.7.12_darwin_arm64.tar.gz", pattern)

    def test_rejects_the_wrong_architecture(self) -> None:
        """The x86_64 darwin asset is what #811 is about; it must not match."""
        pattern = lock_arch.native_regex("macos", "arm64")
        self.assertNotRegex("wasm-pack-v0.15.0-x86_64-apple-darwin.tar.gz", pattern)

    def test_rejects_the_right_arch_on_the_wrong_os(self) -> None:
        """An arm64 LINUX asset does not make macos-arm64 satisfiable."""
        pattern = lock_arch.native_regex("macos", "arm64")
        self.assertNotRegex(
            "committed-v1.1.11-aarch64-unknown-linux-musl.tar.gz", pattern
        )

    def test_is_a_valid_ere(self) -> None:
        """The caller hands this to grep -E; it must compile."""
        for os_family in ("macos", "linux", "windows"):
            for arch in ("x64", "arm64"):
                with self.subTest(os=os_family, arch=arch):
                    re.compile(lock_arch.native_regex(os_family, arch))


class TestReleaseSource(unittest.TestCase):
    """Recovering owner/repo and tag from a download URL."""

    def test_release_download_url(self) -> None:
        """The shape every aqua github_release asset URL takes."""
        url = (
            "https://github.com/wasm-bindgen/wasm-pack/releases/download/"
            "v0.15.0/wasm-pack-v0.15.0-aarch64-apple-darwin.tar.gz"
        )
        self.assertEqual(
            lock_arch.release_source(url), ("wasm-bindgen/wasm-pack", "v0.15.0")
        )

    def test_non_release_url_has_no_asset_list(self) -> None:
        """A tool served from elsewhere cannot be checked this way."""
        self.assertIsNone(
            lock_arch.release_source("https://nodejs.org/dist/v24.0.0/node.tar.gz")
        )


class TestCandidates(unittest.TestCase):
    """End to end over a lock file, which is what the task runs."""

    @staticmethod
    def _lock(body: str) -> pathlib.Path:
        """Write a lock file into a temporary directory.

        Returns:
            The path written.

        """
        tmp = tempfile.mkdtemp()
        path = pathlib.Path(tmp) / "mise.lock"
        path.write_text(body, encoding="utf-8")
        return path

    def test_reports_the_811_defect(self) -> None:
        """The real wasm-pack entry, verbatim from a consumer's lock."""
        path = self._lock(
            '[[tools."aqua:rustwasm/wasm-pack"]]\n'
            'version = "0.15.0"\n\n'
            '[tools."aqua:rustwasm/wasm-pack"."platforms.macos-arm64"]\n'
            'checksum = "sha256:d3f1a4a3"\n'
            'url = "https://github.com/wasm-bindgen/wasm-pack/releases/'
            'download/v0.15.0/wasm-pack-v0.15.0-x86_64-apple-darwin.tar.gz"\n'
        )
        found = lock_arch.candidates(path)
        self.assertEqual(len(found), 1)
        fields = found[0].split("\t")
        self.assertEqual(fields[1], "aqua:rustwasm/wasm-pack")
        self.assertEqual(fields[2], "macos-arm64")
        self.assertEqual(fields[3], "macos/arm64")
        self.assertEqual(fields[5], "wasm-bindgen/wasm-pack")
        self.assertEqual(fields[6], "v0.15.0")

    def test_agreeing_entry_is_silent(self) -> None:
        """The corrected entry must produce nothing at all."""
        path = self._lock(
            '[[tools."aqua:rustwasm/wasm-pack"]]\n'
            'version = "0.15.0"\n\n'
            '[tools."aqua:rustwasm/wasm-pack"."platforms.macos-arm64"]\n'
            'url = "https://github.com/wasm-bindgen/wasm-pack/releases/'
            'download/v0.15.0/wasm-pack-v0.15.0-aarch64-apple-darwin.tar.gz"\n'
        )
        self.assertEqual(lock_arch.candidates(path), [])

    def test_unjudgeable_asset_is_not_reported(self) -> None:
        """No architecture in the name means no disagreement to claim."""
        path = self._lock(
            "[[tools.shellcheck]]\n"
            'version = "0.11.0"\n\n'
            '[tools.shellcheck."platforms.windows-x64"]\n'
            'url = "https://github.com/koalaman/shellcheck/releases/'
            'download/v0.11.0/shellcheck-v0.11.0.zip"\n'
        )
        self.assertEqual(lock_arch.candidates(path), [])

    def test_empty_lock_is_clean(self) -> None:
        """A lock with no tools is not an error."""
        self.assertEqual(lock_arch.candidates(self._lock("[tools]\n")), [])


if __name__ == "__main__":
    unittest.main()
