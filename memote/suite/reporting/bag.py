# -*- coding: utf-8 -*-

# Copyright 2017 Novo Nordisk Foundation Center for Biosustainability,
# Technical University of Denmark.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Utilities that handle a `dask.bag`."""

from __future__ import absolute_import

from builtins import dict

import io
import logging

try:
    import simplejson as json
except ImportError:
    import json
from os.path import exists

import pandas as pd
import dask.bag as db
from colorama import Fore

LOGGER = logging.getLogger(__name__)


class ResultBagWrapper(object):
    """Report-specific wrapper around a `dask.bag`."""

    def __init__(self, files, **kwargs):
        """
        Load (JSON) documents into memory managed by a `dask.bag`.

        The order of the `files` argument determines the order of rows in data
        frames returned by other methods.

        Parameters
        ----------
        files : iterable
            A list of filenames that should contain valid JSON.

        """
        super(ResultBagWrapper, self).__init__(**kwargs)
        # load all into memory and avoid strange dask JSON object expectations
        objects = list()
        for filename in files:
            if not exists(filename):
                LOGGER.warning(
                    Fore.YELLOW +
                    "Expected file %s is missing."
                    + Fore.RESET, filename)  # noqa: W503
                continue
            with io.open(filename) as file_h:
                objects.append(json.load(file_h))
        self._bag = db.from_sequence(objects, npartitions=1)
        self._index = None

    def build_index(self):
        """Build a data index either from timestamps and commit hashes."""
        LOGGER.debug("Building index...")
        expected = pd.DataFrame({
            "timestamp": pd.Series(dtype="datetime64[ns]"),
            "commit_hash": pd.Series(dtype="str")
        })
        df = self._bag.pluck("meta", dict()).to_dataframe(expected).compute()
        df.set_index(
            "commit_hash", drop=True, inplace=True, verify_integrity=True)
        trunc = 5
        res = df.index.str[:trunc]
        while len(res.unique()) < len(df):
            trunc += 1
            res = df.index.str[:trunc]
        df["commit_hash"] = res.copy()
        df.sort_values("timestamp", inplace=True, kind="mergesort")
        self._index = df
        LOGGER.debug("%s", str(df))

    def _assert_index_presence(self):
        """Ensure that the index was built."""
        if self._index is None:
            raise ValueError(
                "No index present. Please call method `build_index` first.")

    def get_model_ids(self):
        """Get unique model IDs. Should typically be of length one."""
        return self._bag.pluck("report").pluck("test_basic").\
            pluck("model_id").distinct().compute()

    def get_basic_dataframe(self):
        """Create basic information data frame."""
        LOGGER.debug("Collecting basic information from bag.")
        self._assert_index_presence()
        columns = ("commit", "num_genes", "num_reactions", "num_metabolites",
                   "num_metabolites_no_formula")
        data = pd.DataFrame(list(self._bag.map(_get_basics)), columns=columns)
        data.set_index("commit", inplace=True)
        return self._index.join(data)

    def get_biomass_dataframe(self):
        """Create biomass information data frame."""
        LOGGER.debug("Collecting biomass information from bag.")
        self._assert_index_presence()
        columns = ("commit", "reaction", "biomass_sum",
                   "biomass_default_flux", "num_default_blocked_precursors",
                   "num_open_blocked_precursors")
        data = pd.DataFrame(self._bag.map(_get_biomass).fold(
            list.__iadd__, initial=list()).compute(), columns=columns)
        data.set_index("commit", inplace=True)
        return self._index.join(data)


def _get_basics(elem):
    """Collect results from `test_basic`."""
    tmp = elem["report"]["test_basic"]
    return (elem["meta"]["commit_hash"],
            tmp["num_genes"],
            tmp["num_reactions"],
            tmp["num_metabolites"],
            len(tmp["metabolites_no_formula"]))


def _get_biomass(elem):
    """Collect results from `test_biomass`."""
    tmp = elem["report"]["test_biomass"]
    commit = elem["meta"]["commit_hash"]
    res = [
        (commit, rxn, tmp["biomass_sum"][i], tmp["biomass_default_flux"][i],
         len(tmp["default_blocked_precursors"][i]),
         len(tmp["open_blocked_precursors"][i]))
        for i, rxn in enumerate(tmp["biomass_reactions"])
    ]
    return res