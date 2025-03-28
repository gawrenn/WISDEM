"""
Provides a library for sub configurations within ORBIT. By default, the library
is located at `../ORBIT/library/`, but it can also be stored outside of the
repo by passing in the `library_path` variable to `ProjectManager` or any
individual phases.

ORBIT expects a library outside of the repo to have the following structure:
```
<library folder name>
├── defaults         <- Top-level default data
├── project
│   ├── config       <- Configuration dictionary repository
│   ├── port         <- Port specific data setttings
│   ├── plant        <- Wind farm specific data setttings
│   ├── site         <- Project site data settings
│   ├── development  <- Project development cost settings
├── cables           <- Cable data files: array cables, export cables
├── substructures    <- Substructure data files: monopiles, jackets, etc.
├── turbines         <- Turbine data files
├── vessels          <- Vessel data files
│   ├── defaults     <- Default data related to vessel tasks
├── weather          <- Weather profiles
├── results
```
"""

__author__ = "Rob Hammond"
__copyright__ = "Copyright 2020, National Renewable Energy Laboratory"
__maintainer__ = "Rob Hammond"
__email__ = "rob.hammond@nrel.gov"


import os
import re
import csv
import warnings
from pathlib import Path

import yaml
import pandas as pd
from yaml import Dumper

from wisdem.orbit.core.exceptions import LibraryItemNotFoundError

ROOT = Path(__file__).parents[2]
default_library = ROOT / "library"


# Need a custom loader to read in scientific notation correctly
class CustomSafeLoader(yaml.SafeLoader):
    """Custom loader that enables tuple sequences in YAML files."""

    def construct_python_tuple(self, node):
        """Constructs the tuple."""
        return tuple(self.construct_sequence(node))


CustomSafeLoader.add_constructor(
    "tag:yaml.org,2002:python/tuple",
    CustomSafeLoader.construct_python_tuple,
)

loader = CustomSafeLoader
loader.add_implicit_resolver(
    "tag:yaml.org,2002:float",
    re.compile(
        """^(?:
     [-+]?(?:[0-9][0-9_]*)\\.[0-9_]*(?:[eE][-+]?[0-9]+)?
    |[-+]?(?:[0-9][0-9_]*)(?:[eE][-+]?[0-9]+)
    |\\.[0-9_]+(?:[eE][-+][0-9]+)?
    |[-+]?[0-9][0-9_]*(?::[0-5]?[0-9])+\\.[0-9_]*
    |[-+]?\\.(?:inf|Inf|INF)
    |\\.(?:nan|NaN|NAN))$""",
        re.X,
    ),
    list("-+0123456789."),
)


def clean_warning(message, category, filename, lineno, file=None, line=""):
    """Formats the standard warning output."""
    return f"{category.__name__}: {filename}:{lineno}\n{message}"


warnings.formatwarning = clean_warning


def initialize_library(library_path):
    """
    Creates an environment variable named "DATA_LIBRARY", defaulting to
    `../ORBIT/library/` if not defined.

    Parameters
    ----------
    library_path : str | None
        Absolute path to the project library.
    """

    if "DATA_LIBRARY" in os.environ:
        return

    if library_path is None:
        library_path = default_library

    library_path = Path(library_path).resolve()
    if not library_path.is_dir():
        raise ValueError("Invalid library path.")

    os.environ["DATA_LIBRARY"] = str(library_path)
    # print(f"ORBIT library intialized at '{library_path}'")


def extract_library_data(config, additional_keys=None):
    """
    Extracts the configuration data from the specified library.

    Parameters
    ----------
    config : dict
        Configuration dictionary.
    additional_keys : list | None
        Additional keys that contain data that needs to be extracted from
        within `config`, by default [].

    Returns
    -------
    config : dict
        Configuration dictionary.
    """

    if additional_keys is None:
        additional_keys = []

    if os.environ.get("DATA_LIBRARY", None) is None:
        return config

    for key, val in config.items():
        if isinstance(val, dict) and any(el in key for el in additional_keys):
            config[key] = extract_library_data(val)
        elif not isinstance(val, str):
            continue

        try:
            config[key] = extract_library_specs(key, val)
        except KeyError:
            continue

    return config


def extract_library_specs(key, filename, file_type="yaml"):
    """
    Base method that extracts a file from the configured data library. If the
    file is not found in the configured library, the default library located
    within the repository will also be searched.

    Parameters
    ----------
    key : str
        Configuration key used to map to specific subpath in library.
    filename : str
        Name of the file to be extracted.
    file_type : str
        Should be one of "yaml" or "csv".

    Returns
    -------
    dict
        Dictionary of specifications for `filename`.

    Raises
    ------
    LibraryItemNotFoundError
        An error is raised when the file cannot be found in the library or the
        default library.
    """

    filename = f"{filename}.{file_type}"
    path = PATH_LIBRARY[key]
    filepath = Path(os.environ["DATA_LIBRARY"]) / path / filename

    if filepath.is_file():
        return _extract_file(filepath)

    if Path(os.environ["DATA_LIBRARY"]) != default_library:
        filepath = default_library / path / filename
        if filepath.is_file():
            return _extract_file(filepath)

    raise LibraryItemNotFoundError(path, filename)


def _extract_file(filepath):
    """
    Extracts file from valid filepath. Currently only supports "yaml" or "csv".

    Parameters
    ----------
    filepath : pathlib.Path
        Valid filepath of library item.
    """

    ftype = filepath.suffix
    if ftype in (".yaml", ".yml"):
        with filepath.open() as f:
            fyaml = yaml.load(f, Loader=loader)
        return fyaml

    elif ftype == ".csv":
        df = pd.read_csv(filepath, index_col=False)

        # Drop empty rows and columns
        df = df.dropna(how="all").dropna(how="all", axis=1)

        # Enforce strictly lowercase and "_" separated column names
        df.columns = [el.replace(" ", "_").lower() for el in df.columns]
        return df

    raise TypeError(f"File type {ftype} not supported for extraction.")


def _get_yes_no_response(filename):
    """Elicits a y/n response from the user to overwrite a file.

    Returns
    -------
    bool
        Indicator to overwrite `filename`.
    """

    response = input(f"{filename} already exists, overwrite [y/n]?").lower()
    if response not in ("y", "n"):
        print("Bad input! Must be one of [y/n]")
        _get_yes_no_response(filename)
    return True if response == "y" else False


def export_library_specs(key, filename, data, file_ext="yaml"):
    """
    Base method that export a file to the data library.

    Parameters
    ----------
    key : str
        Configuration key used to map to a specific subpath in library.
    filename : str
        Name to be given to the file without the extension.
    data : yaml-ready or List[list]
        Data to be saved to YAML (any Python type) or csv-ready.
    """

    filename = f"{filename}.{file_ext}"
    path = PATH_LIBRARY[key]
    data_path = Path(os.environ["DATA_LIBRARY"]) / path / filename
    if data_path.is_file() and not _get_yes_no_response(data_path):
        print("Cancelling save!")
        return
    if file_ext == "yaml":
        with data_path.open("w") as f:
            yaml.dump(data, f, Dumper=Dumper, default_flow_style=False)
    elif file_ext == "csv":
        with data_path.open("w") as f:
            writer = csv.writer(f)
            writer.writerows(data)
    print("Save complete!")


PATH_LIBRARY = {
    # default data
    "defaults": "defaults",
    # vessels
    "array_cable_install_vessel": "vessels",
    "array_cable_bury_vessel": "vessels",
    "array_cable_trench_vessel": "vessels",
    "export_cable_install_vessel": "vessels",
    "export_cable_bury_vessel": "vessels",
    "export_cable_trench_vessel": "vessels",
    "oss_install_vessel": "vessels",
    "spi_vessel": "vessels",
    "trench_dig_vessel": "vessels",
    "feeder": "vessels",
    "mooring_install_vessel": "vessels",
    "wtiv": "vessels",
    "towing_vessel": "vessels",
    "support_vessel": "vessels",
    "ahts_vessel": "vessels",
    # cables
    "cables": "cables",
    "array_system": "cables",
    "array_system_design": "cables",
    "export_system": "cables",
    "export_system_design": "cables",
    # project details
    "config": str(Path("project") / "config"),
    "plant": str(Path("project") / "plant"),
    "port": str(Path("project") / "ports"),
    "project_development": str(Path("project") / "development"),
    "site": str(Path("project") / "site"),
    # substructures
    "monopile": "substructures",
    "monopile_design": "substructures",
    "scour_protection": "substructures",
    "scour_design": "substructures",
    "transition_piece": "substructures",
    "offshore_substation_substructure": "substructures",
    "offshore_substation_topside": "substructures",
    # turbine
    "turbine": "turbines",
}
