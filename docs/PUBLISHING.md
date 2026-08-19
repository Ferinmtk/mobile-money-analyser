# Publishing

How this package gets to PyPI and conda-forge.

## PyPI (automated via trusted publishing)

Releases publish themselves — no API tokens anywhere. One-time setup:

1. On [pypi.org](https://pypi.org) → your account → **Publishing** → add a
   **pending trusted publisher**:
   - PyPI project name: `mobile-money-analyzer`
   - Owner: `Ferinmtk`, repository: `mobile-money-analyser`
   - Workflow: `release.yml`
   - Environment: `pypi`
2. In this repo: **Settings → Environments → New environment** named `pypi`.

Then for every release:

```bash
# bump version in pyproject.toml, commit, then:
git tag v0.2.0
git push origin v0.2.0
gh release create v0.2.0 --generate-notes
```

The `Release to PyPI` workflow builds the sdist + wheel, runs `twine check`,
and publishes via OIDC.

## conda-forge (first-time submission)

conda-forge builds from the **PyPI sdist**, so do the PyPI release first.

1. Get the sdist hash:
   ```bash
   pip download --no-deps --no-binary :all: mobile-money-analyzer==0.2.0 -d /tmp/sdist
   sha256sum /tmp/sdist/*.tar.gz
   ```
2. Fork <https://github.com/conda-forge/staged-recipes>, branch off `main`.
3. Copy `recipe/meta.yaml` from this repo into
   `staged-recipes/recipes/mobile-money-analyzer/meta.yaml` and fill in the
   `sha256`.
4. Open a PR against staged-recipes. CI builds the recipe on Linux, macOS,
   and Windows; a reviewer merges it, which creates the
   `mobile-money-analyzer-feedstock` repo with you as maintainer.
5. After the feedstock exists, new versions are mostly automatic: the
   regro-cf-autotick-bot opens a version-bump PR on the feedstock whenever a
   new sdist lands on PyPI — review and merge it.

Sanity checks reviewers commonly ask for are already in the recipe:
`noarch: python`, `python_min` pinning, `pip check` + CLI smoke test,
declared `entry_points`, `license_file`.
