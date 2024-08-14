import inspect
import pkgutil
import sys

# deprecated in python >=3.12
from typing import TypeVar

if sys.version_info >= (3, 9):
    from collections.abc import Callable
else:
    from typing import Callable

T = TypeVar("T")


PACKAGE_NAMES = {_module.name for _module in pkgutil.iter_modules()}


def is_installed(package_name: str) -> bool:
    """Check if the package is installed.

    Parameters
    ----------
    package_name : str
        package name like `sklearn`

    Returns
    -------
    bool
        if installed, True
    """
    return package_name in PACKAGE_NAMES


def dummy_func(x: T, *args, **kwargs) -> T:
    """dummy function

    Parameters
    ----------
    x : T
        Anything

    Returns
    -------
    T
        same as input
    """
    return x


def is_argument(_callable: Callable, arg_name: str) -> bool:
    return arg_name in inspect.signature(_callable).parameters.keys()
