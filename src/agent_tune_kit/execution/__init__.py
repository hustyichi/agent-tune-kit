from .runtime import launch


def check_runner(**kwargs):
    return launch("eval", check=True, **kwargs)


def run_evaluation(**kwargs):
    return launch("eval", **kwargs)


def run_rule(**kwargs):
    return launch("rule", **kwargs)
