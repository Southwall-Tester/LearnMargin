# Contributing

Use a short-lived branch and a pull request linked to a concrete issue. Keep changes focused; changes to learning behavior must update the skill and application together.

Before requesting review:

1. Run Python checks/tests and frontend tests/build as documented in the README.
2. For parser changes, add a small generated fixture that demonstrates the behavior. Never commit private course material.
3. For PDF changes, render and inspect real output. Verify A4 dimensions, long-content pagination, mathematics, answer separation and link destinations.
4. For API changes, exercise both protocol adapters through a local mock transport. Live model checks must be opt-in and accurately reported.
5. Keep uploads, API credentials, temporary artifacts and generated test history out of Git.

CI must pass before merging. Record what was actually verified, any optional dependencies not exercised, and material limitations. Page counts, automated tests and plausible generated explanations do not by themselves establish learning outcomes.

Only a reviewed, intentional release should create a version tag. Dependency updates should regenerate lockfiles and pass the same checks.
