"""Optional analytics dependencies: skipped by default, required on demand.

The stdlib suite skips SciPy and DuckDB paths. A job that installs
plugins/p/requirements-eval.txt sets RETRO_EVAL_REQUIRE_OPTIONAL=1, which turns
every such skip into a failure, so those paths can never pass by not running.
"""

import importlib.util
import os
import unittest

REQUIRE_OPTIONAL = os.environ.get("RETRO_EVAL_REQUIRE_OPTIONAL") == "1"


def requires(module_name):
    if importlib.util.find_spec(module_name) is not None:
        return lambda test: test
    if REQUIRE_OPTIONAL:
        def decorate(test):
            def missing(self):
                self.fail("%s is required when RETRO_EVAL_REQUIRE_OPTIONAL=1"
                          % module_name)
            missing.__name__ = test.__name__
            return missing
        return decorate
    return unittest.skip("optional %s not installed" % module_name)
