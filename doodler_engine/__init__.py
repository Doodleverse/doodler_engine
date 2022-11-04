from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("doodler_engine")
except PackageNotFoundError:
    # package is not installed
    pass

del version, PackageNotFoundError
