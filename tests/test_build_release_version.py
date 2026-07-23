"""Tests for build_release.ps1 version helpers."""

import subprocess
import tempfile
import unittest
from pathlib import Path


class BuildReleaseVersionTests(unittest.TestCase):
    def test_release_version_helpers_increment_expected_segments(self) -> None:
        project_root = Path(__file__).parents[1]
        script = project_root / "build_release.ps1"
        source = script.read_text(encoding="utf-8")
        helper_script = "\n\n".join(
            [
                _extract_powershell_function(source, "ConvertTo-QSignReleaseVersion"),
                _extract_powershell_function(source, "Get-Next-QSignReleaseVersion"),
                "$a = Get-Next-QSignReleaseVersion -Version '01.001'",
                "$b = Get-Next-QSignReleaseVersion -Version '01.001' -Major $true",
                "$c = ConvertTo-QSignReleaseVersion -Version '01.001.001'",
                'Write-Output "$a|$b|$c"',
            ]
        )

        with tempfile.TemporaryDirectory() as directory:
            helper_path = Path(directory) / "version-test.ps1"
            helper_path.write_text(helper_script, encoding="utf-8")
            result = subprocess.run(
                ["powershell", "-NoProfile", "-File", str(helper_path)],
                cwd=project_root,
                check=True,
                capture_output=True,
                text=True,
            )

        self.assertEqual(result.stdout.strip().splitlines()[-1], "01.002|02.000|01.001")


def _extract_powershell_function(source: str, name: str) -> str:
    start = source.index(f"function {name}")
    brace_start = source.index("{", start)
    depth = 0
    for index in range(brace_start, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise AssertionError(f"Function not closed: {name}")


if __name__ == "__main__":
    unittest.main()
