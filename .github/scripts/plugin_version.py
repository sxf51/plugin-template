"""Single source of truth for this plugin's released version.

The host resolves plugin updates by Git tag: a release is a tag named ``v`` plus
the version in ``plugin.yaml``, and "is there an update?" is answered by
comparing that tag's version against the installed one.  Nothing reconciles the
two afterwards, so a tag that disagrees with the manifest is a release that
misreports itself forever.

This script is what the release workflow reads, and it refuses to answer when
the repository's own metadata is inconsistent:

    python .github/scripts/plugin_version.py          # print version and tag
    python .github/scripts/plugin_version.py --check  # validate only, no output

Run it locally before bumping a version - the same check gates the release.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
# PEP 440, narrowed to what a plugin release realistically needs: three numeric
# parts plus an optional prerelease suffix. A version the host cannot order
# (dates, build metadata, a bare "latest") would silently never look newer.
VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?$")


def read_manifest_version() -> str:
    """Read the version the host will show and compare against release tags."""
    manifest_path = next((ROOT / name for name in ("plugin.yaml", "plugin.yml") if (ROOT / name).is_file()), None)
    if manifest_path is None:
        raise SystemExit("plugin.yaml is missing from the repository root")
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    version = str(manifest.get("version") or "").strip()
    if not version:
        raise SystemExit(f"{manifest_path.name} does not declare a version")
    if not VERSION_PATTERN.fullmatch(version):
        raise SystemExit(f"{manifest_path.name} version '{version}' must look like 1.2.3, 1.2.3rc1, or 1.2.3b2")
    return version


def read_project_version() -> str | None:
    """Read pyproject's version, which exists only for the author's own tooling."""
    pyproject = ROOT / "pyproject.toml"
    if not pyproject.is_file():
        return None
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    version = data.get("project", {}).get("version")
    return str(version).strip() if version else None


def resolve() -> tuple[str, str]:
    """Return the validated (version, tag) pair, or exit explaining what is wrong."""
    version = read_manifest_version()
    project_version = read_project_version()
    if project_version is not None and project_version != version:
        raise SystemExit(
            f"pyproject.toml version '{project_version}' does not match plugin.yaml version '{version}'. "
            "plugin.yaml is what the host reads; bump both together."
        )
    return version, f"v{version}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="validate the version without printing it")
    arguments = parser.parse_args()
    version, tag = resolve()
    if arguments.check:
        print(f"version {version} is consistent and releasable as {tag}", file=sys.stderr)
        return
    print(f"version={version}")
    print(f"tag={tag}")
    # GitHub Actions reads step outputs from this file rather than from stdout.
    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with Path(output_path).open("a", encoding="utf-8") as handle:
            handle.write(f"version={version}\ntag={tag}\n")


if __name__ == "__main__":
    main()
