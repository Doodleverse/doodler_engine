# doodler_engine

A set of common Doodleverse/Doodler tools and utilities

A pip-installable repository

Yeah.


## Development

### Install

Run the following commands from this directory:

```
conda create -n doodler_engine_env -c conda-forge --override-channels python=3.8
conda activate doodler_engine_env
conda install --file requirements.txt -c conda-forge --override-channels
pip install -e .
```

Note that:

- it is advised to use conda specially to install `pydensecrf` which is otherwise difficult to build
- `conda install --file requirements.txt` works as the dependencies all have the same name on pip and conda. If that were not the case, an alternative is to maintain an `environment.yml` file for `conda install`, and make sure the dependencies listed in this file are kept in sync with those in `requirements.txt` or `pyproject.toml`.
