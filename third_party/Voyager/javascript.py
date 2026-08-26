class _MissingJavascriptModuleError(RuntimeError):
    pass


def require(module_name):  # pragma: no cover
    raise _MissingJavascriptModuleError(
        "The optional 'javascript' dependency is unavailable in this environment. "
        "Tests that import Voyager action modules should provide a stubbed implementation."
    )
