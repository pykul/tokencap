# Tag protection

Tags matching `v*` are protected. Only authorized users can create, delete, or
force-push these tags.

Authorized to create v* tags:
- Repository owner (pykul org admin)
- GitHub Actions (publish.yml workflow via OIDC)

This protection is enforced via GitHub repository rulesets. To view or modify:
https://github.com/pykul/tokencap/settings/rules

Any attempt to push a v* tag from an unauthorized account will be rejected by
GitHub before the publish workflow fires.

To release a new version, use:

    make release VERSION=X.Y.Z

Never push a v* tag manually without running `make release` first. The Makefile
runs all pre-release checks before tagging.
