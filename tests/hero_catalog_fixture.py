"""Compile the production engine-independent catalogue in policy tests."""
import os
from pathlib import Path

def catalogue_source(root, target):
    source = (root / 'alliance_game/hero_catalog.cpp').read_text()
    source += '\n#include <boost/json/src.hpp>\n'
    target.write_text(source)
    candidates = [Path(os.environ.get('BOOST_ROOT', '/opt/boost')) / 'include', Path('/usr/include')]
    for include in candidates:
        if (include / 'boost/json.hpp').is_file():
            return ['-I' + str(include)]
    raise RuntimeError('The pinned verification Boost headers are required.')
