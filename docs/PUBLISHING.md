# Publishing

How this package gets to PyPI and conda-forge.

## PyPI (automated via trusted publishing)

Releases publish themselves — no API tokens stored anywhere. One-time setup,
already done:

1. On [pypi.org](https://pypi.org) → account → **Publishing** → add a
   **pending trusted publisher**:
   - PyPI project name: `mobile-money-analyzer`
   - Owner: `Ferinmtk`, repository: `mobile-money-analyser`
   - Workflow: `release.yml`
   - Environment: `pypi`
2. Repo **Settings → Environments → New environment** named `pypi`.

For each release:

```bash
# bump version in pyproject.toml and recipe/recipe.yaml, commit, then:
git tag v0.3.0
git push origin v0.3.0
gh release create v0.3.0 --generate-notes
```

The `Release to PyPI` workflow builds the sdist + wheel, runs `twine check`,
and publishes via OIDC.

## conda-forge

conda-forge builds from the **PyPI sdist**, so always release to PyPI first.

`recipe/recipe.yaml` is in the **v1 recipe format** ([CEP 13]/[CEP 14]). As of
August 2026 v1 is the preferred submission format on staged-recipes; the older
v0 `meta.yaml` format is deprecated and reviewed more slowly.

### First-time submission

1. Get the sdist hash for the released version:
   ```bash
   curl -sL https://pypi.org/pypi/mobile-money-analyzer/json \
     | python -c "import json,sys; print([u['digests']['sha256'] for u in json.load(sys.stdin)['urls'] if u['packagetype']=='sdist'][0])"
   ```
2. Fork <https://github.com/conda-forge/staged-recipes>, branch off `main`.
3. Copy `recipe/recipe.yaml` to
   `staged-recipes/recipes/mobile-money-analyzer/recipe.yaml`, with the
   `sha256` above.
4. Open a PR against staged-recipes. CI builds the recipe; a reviewer merges
   it, which creates the `mobile-money-analyzer-feedstock` repo with the
   listed maintainers.

### After the feedstock exists

Version bumps are mostly automatic: the `regro-cf-autotick-bot` opens a PR on
the feedstock whenever a new sdist lands on PyPI. Review the diff (especially
if dependencies changed) and merge.

### Validating the recipe locally

The recipe can be checked against the official schema without installing
conda:

```bash
pip install jsonschema pyyaml
curl -sO https://raw.githubusercontent.com/prefix-dev/recipe-format/main/schema.json
python -c "
import json, yaml, jsonschema
jsonschema.validate(yaml.safe_load(open('recipe/recipe.yaml')), json.load(open('schema.json')))
print('valid')"
```

To do a real build, install [rattler-build] (or [pixi]) and run
`rattler-build build --recipe recipe/recipe.yaml`.

Checks reviewers commonly ask for are already handled in the recipe:
`noarch: python`, `python_min` in host/run/test, `pip_check`, a CLI smoke
test, declared entry points, and `license_file`.

[CEP 13]: https://github.com/conda/ceps/blob/main/cep-0013.md
[CEP 14]: https://github.com/conda/ceps/blob/main/cep-0014.md
[rattler-build]: https://rattler-build.prefix.dev
[pixi]: https://pixi.sh
