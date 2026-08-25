from run_result_interpreter import main


def test_run_result_interpreter_reexports_cli_main() -> None:
    assert callable(main)
    assert main.__module__ == "apps.cli.result_interpreter_main"
