# Contributing

Thank you for your interest in this project. Questions, bug reports, and pull requests are welcome, in English or Japanese.
(日本語でも英語でも、質問・バグ報告・プルリクエストを歓迎します。)

## Questions and bug reports

Please open an issue on GitHub. For a bug report, include:

- The command you ran, with its options
- The target (`awsim`, `dynamics`, `prism`, `bbsl`) and, for `awsim`, the `container_profile` and Autoware version
- What you expected and what happened, with the relevant part of the log or `<output>.summary.json`
- Python version and OS

Do not include IP addresses, user names, passwords, or other information about your environment that should stay private.

## Development setup

```bash
git clone https://github.com/zlbhjl/AWSIM_launch.git
cd AWSIM_launch
python3 -m pip install -e ".[test]"
python3 -m pytest
```

The unit tests do not need ROS, AWSIM, or a GPU. Tests that need AW-CheckerPy or AW-Runtime-Monitor are skipped when those tools
are absent.

## Pull requests

- Keep each pull request focused on one change, and add or update tests for it.
- Run `python3 -m pytest` before opening the pull request. CI runs the unit tests on Python 3.10, 3.11, and 3.12.
- Follow the structure of the framework: target-specific code goes in `targets/<name>/`, judges in `verifiers/<name>/`, and
  statistical methods in `evaluation/`. The shared layers (`orchestration/`, `runtime/`, `evaluation/`) must not refer to specific
  targets or verifiers. See [docs/architecture.md](docs/architecture.md) and [docs/implementation_rules.md](docs/implementation_rules.md)
  (Japanese only).
- Do not change the results of existing statistical modes (estimates, intervals, stopping decisions) without discussing it in an
  issue first, because past experiments must stay comparable.
- When you change a document that has both Japanese and English versions, update both.

## License

By contributing, you agree that your contributions are licensed under the [Apache License 2.0](LICENSE).
