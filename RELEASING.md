# Releasing tokencap

1. Create a branch: `release/vX.Y.Z`
2. Bump version in `pyproject.toml` to `X.Y.Z`
3. Update `tokencap/__init__.py` `__version__` to match
4. Update `CHANGELOG.md` with the new version and changes
5. Open a PR to main, get it reviewed and merged
6. Create a GitHub release:
   - Tag: `vX.Y.Z`
   - Title: `tokencap vX.Y.Z`
   - Body: paste the CHANGELOG.md entry for this version
7. The publish GitHub Action fires automatically
8. Verify at https://pypi.org/project/tokencap/

Do not manually run `twine upload` after the first release.
All subsequent releases go through the GitHub Action.
