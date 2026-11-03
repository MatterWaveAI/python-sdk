# Releasing `matterwave-sdk`

Releases are cut from `main` and published to PyPI by
[`.github/workflows/publish-pypi.yml`](.github/workflows/publish-pypi.yml)
using OIDC trusted publishing. Day-to-day work lands on `develop`; `main`
advances only when cutting a release.

## 1. Prepare and verify on `develop`

1. Make sure all local gates pass on `develop`:

   ```bash
   sh scripts/check-ticket-ids.sh
   uv run ruff check
   uv run ruff format --check
   uv run mypy
   uv run pytest
   ```

2. Bump the package version in `src/matterwave_sdk/_version.py`
   (`pyproject.toml` reads `__version__` dynamically from
   `src/matterwave_sdk/_version.py` through `[tool.hatch.version]`).
3. Update [`CHANGELOG.md`](CHANGELOG.md): move the entries under
   `## [Unreleased]` into a new `## [X.Y.Z] - YYYY-MM-DD` section (following
   [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
   [`VERSIONING.md`](VERSIONING.md)).
4. Verify the release helper against the new version and tag:

   ```bash
   python scripts/release.py check-tag vX.Y.Z
   python scripts/release.py notes X.Y.Z
   ```

5. Commit the version bump and changelog update on `develop`, push to
   `origin/develop`, and wait for the `ci` and `docs` workflows to pass.

## 2. Merge `develop` into `main`

Open a pull request from `develop` into `main` (or fast-forward `main` to
`develop` once CI is green):

```bash
git checkout main
git pull --ff-only origin main
git merge --ff-only origin/develop
git push origin main
```

## 3. Tag `vX.Y.Z` on `main`

Tag the release commit on `main` and push the tag to `origin`:

```bash
git checkout main
python scripts/release.py check-tag vX.Y.Z
git tag -a vX.Y.Z -m "vX.Y.Z"
git push origin vX.Y.Z
```

## 4. Verify the PyPI publish workflow

Pushing `vX.Y.Z` triggers `.github/workflows/publish-pypi.yml`, which runs three
jobs:

1. **`build`**:
   - Verifies the commit is an ancestor of `origin/main`
     (`git fetch origin main` and
     `git merge-base --is-ancestor "$GITHUB_SHA" origin/main`) so a tag on an
     unmerged branch can never be published.
   - Verifies the tag matches `src/matterwave_sdk/_version.py` via
     `python scripts/release.py check-tag "$TAG"`.
   - Runs `ruff check`, `ruff format --check`, `mypy`, `pytest`, and
     `uv build`.
   - Exports a CycloneDX 1.5 SBOM (`sbom.cdx.json`) and extracts the version's
     release notes (`notes.md`) from `CHANGELOG.md`.
2. **`publish`**:
   - Uploads the built wheel and sdist in `dist/` to PyPI via OIDC trusted
     publishing in the `pypi` GitHub environment.
3. **`release`**:
   - Creates the GitHub release for `vX.Y.Z` with `notes.md` as the body and
     attaches the wheel, sdist, and `sbom.cdx.json`.

Confirm on the Actions tab that all three jobs succeed, check the new release
on GitHub Releases, and verify the package can be installed from PyPI:

```bash
uv run --isolated --with matterwave-sdk==X.Y.Z python -c "import matterwave_sdk; print(matterwave_sdk.__version__)"
```
