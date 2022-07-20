import os
module_dict={}
module_dict["certifi"+os.sep+"core.py"]="""
\"\"\"
certifi.py
~~~~~~~~~~

This module returns the installation location of cacert.pem or its contents.
\"\"\"
import os
import types
from typing import Union

try:
    from importlib.resources import path as get_path, read_text

    _CACERT_CTX = None
    _CACERT_PATH = None

    def where() -> str:
        # This is slightly terrible, but we want to delay extracting the file
        # in cases where we're inside of a zipimport situation until someone
        # actually calls where(), but we don't want to re-extract the file
        # on every call of where(), so we'll do it once then store it in a
        # global variable.
        global _CACERT_CTX
        global _CACERT_PATH
        if _CACERT_PATH is None:
            # This is slightly janky, the importlib.resources API wants you to
            # manage the cleanup of this file, so it doesn't actually return a
            # path, it returns a context manager that will give you the path
            # when you enter it and will do any cleanup when you leave it. In
            # the common case of not needing a temporary file, it will just
            # return the file system location and the __exit__() is a no-op.
            #
            # We also have to hold onto the actual context manager, because
            # it will do the cleanup whenever it gets garbage collected, so
            # we will also store that at the global level as well.
            _CACERT_CTX = get_path(\"certifi\", \"cacert.pem\")
            _CACERT_PATH = str(_CACERT_CTX.__enter__())

        return _CACERT_PATH


except ImportError:
    Package = Union[types.ModuleType, str]
    Resource = Union[str, \"os.PathLike\"]

    # This fallback will work for Python versions prior to 3.7 that lack the
    # importlib.resources module but relies on the existing `where` function
    # so won't address issues with environments like PyOxidizer that don't set
    # __file__ on modules.
    def read_text(
        package: Package,
        resource: Resource,
        encoding: str = 'utf-8',
        errors: str = 'strict'
    ) -> str:
        with open(where(), encoding=encoding) as data:
            return data.read()

    # If we don't have importlib.resources, then we will just do the old logic
    # of assuming we're on the filesystem and munge the path directly.
    def where() -> str:
        f = os.path.dirname(__file__)

        return os.path.join(f, \"cacert.pem\")


def contents() -> str:
    return read_text(\"certifi\", \"cacert.pem\", encoding=\"ascii\")

"""
module_dict["certifi"+os.sep+"__init__.py"]="""
from .core import contents, where

__all__ = [\"contents\", \"where\"]
__version__ = \"2022.06.15\"

"""
module_dict["certifi"+os.sep+"__main__.py"]="""
import argparse

from certifi import contents, where

parser = argparse.ArgumentParser()
parser.add_argument(\"-c\", \"--contents\", action=\"store_true\")
args = parser.parse_args()

if args.contents:
    print(contents())
else:
    print(where())

"""

import os
import types
import zipfile
import sys
import io
import json

class ZipImporter(object):
    def __init__(self, zip_file):
        self.zfile = zip_file
        self._paths = [x.filename for x in self.zfile.filelist]
        
    def _mod_to_paths(self, fullname):
        # get the python module name
        py_filename = fullname.replace(".", os.sep) + ".py"
        # get the filename if it is a package/subpackage
        py_package = fullname.replace(".", os.sep) + os.sep + "__init__.py"
        if py_filename in self._paths:
            return py_filename
        elif py_package in self._paths:
            return py_package
        else:
            return None

    def find_module(self, fullname, path):
        if self._mod_to_paths(fullname) is not None:
            return self
        return None

    def load_module(self, fullname):
        filename = self._mod_to_paths(fullname)
        if not filename in self._paths:
            raise ImportError(fullname)
        new_module = types.ModuleType(fullname)
        sys.modules[fullname]=new_module
        if filename.endswith("__init__.py"):
            new_module.__path__ = [] 
            new_module.__package__ = fullname
        else:
            new_module.__package__ = fullname.rpartition('.')[0]
        exec(self.zfile.open(filename, 'r').read(),new_module.__dict__)
        new_module.__file__ = filename
        new_module.__loader__ = self
        new_module.__spec__=json.__spec__ # To satisfy importlib._common.get_package
        return new_module

module_zip=zipfile.ZipFile(io.BytesIO(),"w")
for key in module_dict:
    module_zip.writestr(key,module_dict[key])

module_importer=ZipImporter(module_zip)
sys.meta_path.insert(0,module_importer)

#from certifi import *
import certifi
globals().update(certifi.__dict__)
    
if module_importer in sys.meta_path:
    sys.meta_path.remove(module_importer)

#for key in sys.modules.copy():
#    if key=="certifi" or key.startswith("certifi."):
#        del sys.modules[key]
