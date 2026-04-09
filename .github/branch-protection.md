# Branch protection follow-up

After the new security workflow has been stable (low false-positive/noise), configure branch protection for `main` and require these status checks:

- `pip-audit`
- `bandit`

These are provided by `.github/workflows/security.yml`.
