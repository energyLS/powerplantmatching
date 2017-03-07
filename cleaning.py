## Copyright 2015-2016 Fabian Hofmann (FIAS), Jonas Hoersch (FIAS)

## This program is free software; you can redistribute it and/or
## modify it under the terms of the GNU General Public License as
## published by the Free Software Foundation; either version 3 of the
## License, or (at your option) any later version.

## This program is distributed in the hope that it will be useful,
## but WITHOUT ANY WARRANTY; without even the implied warranty of
## MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
## GNU General Public License for more details.

## You should have received a copy of the GNU General Public License
## along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""
Functions for vertically cleaning a dataset
"""
from __future__ import absolute_import, print_function

import numpy as np
import pandas as pd
import networkx as nx
import os
import six

from .config import target_columns, target_fueltypes, target_technologies
from .utils import read_csv_if_string
from .duke import duke



def clean_powerplantname(df):
    df = df.copy()
    """
    Cleans the column "Name" of the database by deleting very frequent
    words, numericals and nonalphanumerical characters of the
    column. Returns a reduced dataframe with nonempty Name-column.

    Parameters
    ----------
    df : pandas.Dataframe
        dataframe which should be cleaned

    """
    df.Name.replace(regex=True, value=' ',
                    to_replace=list('-/')+['\(', '\)', '\[', '\]', '[0-9]'],
                    inplace=True)

    common_words = pd.Series(sum(df.Name.str.split(), [])).value_counts()
    cw = list(common_words[common_words >= 20].index)

    pattern = [('(?i)(^|\s)'+x+'($|\s)') for x in cw + \
       ['[a-z]','I','II','III','IV','V','VI','VII','VIII','IX','X','XI','Grupo',
     'parque','eolico','gas','biomasa','COGENERACION'
     ,'gt','unnamed','tratamiento de purines','planta','de','la','station', 'power',
     'storage', 'plant', 'stage', 'pumped', 'project'] ]
    df.loc[:,'Name'] = df.Name.replace(regex=True, to_replace=pattern, value=' ').str.strip().\
                str.replace('\\s\\s+', ' ')
    #do it twice to catch all single letters
    df.loc[:,'Name'] = df.Name.replace(regex=True, to_replace=pattern, value=' ').str.strip().\
                str.replace('\\s\\s+', ' ').str.capitalize()
    df = df[df.Name != ''].sort_values('Name').reset_index(drop=True)
    return df


def gather_fueltype_info(df, search_col=['Name', 'Technology']):
    df = df.copy()
    for i in search_col:
        found = df.loc[:,i].fillna('').str.contains('(?i)lignite|(?i)brown')
        df.loc[found,'Fueltype'] = 'Lignite'
    df.loc[df.Fueltype=='Coal', 'Fueltype'] = 'Hard Coal'
    return df


def gather_technology_info(df, search_col=['Name', 'Fueltype']):
    df = df.copy()
    pattern = '|'.join(('(?i)'+x) for x in target_technologies())
    for i in search_col:
        found = df.loc[:,i].str.findall(pattern).str.join(sep=', ')
        df.loc[:, 'Technology'] = (df.loc[:, 'Technology'].fillna('')
                            .str.cat(found.fillna(''), sep=', ').str.strip())
        df.loc[:,'Technology'] = df.Technology.str.replace('^ , |^,|, $|,$', '').apply(lambda x:
                     ', '.join(list(set(x.split(', ')))) ).str.strip()
        df.Technology.replace('', np.NaN, regex=True, inplace=True)
    return df


def gather_set_info(df, search_col=['Name', 'Fueltype']):
    df = df.copy()
    if 'chp' in df:
        df.loc[df.loc[:,'chp']==(True|1), 'Set'] = 'CHP'
    pattern = '|'.join(['heizkraftwerk', 'hkw', 'chp', 'bhkw', 'cogeneration',
                        'power and heat', 'heat and power'])
    for i in search_col:
        isCHP_b = df.loc[:,i].fillna('').str.contains(pattern, case=False)
        df.loc[isCHP_b, 'Set'] = 'CHP'
    df.loc[df.Set.isnull(),
           'Set' ] = 'PP'
    return df


def clean_technology(df, generalize_hydros=False):
    df = df.copy()
    tech_b = df.Technology.notnull()
    df.loc[tech_b,'Technology'] = df.loc[tech_b,'Technology']\
                                  .replace([' and ',' Power Plant'],[', ', ''],regex=True )
    if generalize_hydros:
        df.loc[(df.Technology.fillna('').str.contains('Pump|pumped', case=False))
               , 'Technology'] = 'Pumped Storage'
        df.loc[(df.Technology.fillna('').str.contains('reservoir|lake', case=False))
               , 'Technology'] = 'Reservoir'
        df.loc[(df.Technology.fillna('').str.contains('run-of-river|weir|water', case=False))
               , 'Technology'] = 'Run-Of-River'
        df.loc[(df.Technology.fillna('').str.contains('dam', case=False))
               , 'Technology'] = 'Reservoir'
    df.loc[df.Technology == 'Gas turbine', 'Technology'] = 'OCGT'
    df.loc[(df.Technology.fillna('').str.contains('combined cycle', case=False))
           , 'Technology'] = 'CCGT'
    df.loc[(df.Technology.fillna('').str.contains('steam turbine|critical thermal', case=False))
           , 'Technology'] = 'Steam Turbine'
    df.loc[(df.Technology.fillna('').str.contains('ocgt|open cycle', case=False))
           , 'Technology'] = 'OCGT'
    df.loc[tech_b,'Technology'] = df.loc[tech_b,
                                         'Technology'].str.title().str.strip().str.split(', ')\
                                    .apply(lambda x: ', '.join(np.unique(x))).str.strip()
    df.Technology[tech_b] = df.Technology[tech_b].str.title().replace(
        ['Ccgt','Ocgt'], ['CCGT', 'OCGT'], regex=True)
    # df.Technology = df.Technology[lambda df : df.notnull()].str.split(', ').apply(
    #         lambda x : ', '.join([i for i in x if i in target_technologies()]))
    df.replace('', np.nan, inplace=True)
    return df


def cliques(df, dataduplicates):
    df = df.copy()
    """
    Locate cliques of units which are determined to belong to the same
    powerplant.  Return the same dataframe with an additional column
    "grouped" which indicates the group that the powerplant is
    belonging to.

    Parameters
    ----------
    df : pandas.Dataframe or string
        dataframe or csv-file which should be analysed
    dataduplicates : pandas.Dataframe or string
        dataframe or name of the csv-linkfile which determines the
        link within one dataset

    """
    df = read_csv_if_string(df)
    G = nx.DiGraph()
    G.add_nodes_from(df.index)
    G.add_edges_from((r.one, r.two) for r in dataduplicates.itertuples())
    H = G.to_undirected(reciprocal=True)
    for i, inds in enumerate(nx.algorithms.clique.find_cliques(H)):
        df.loc[inds, 'grouped'] = i

    return df


def aggregate_units(df, use_saved_aggregation=False, dataset_name=None):
    df = df.copy()
    """
    Vertical cleaning of the database. Cleans the "Name"-column, sums
    up the capacity of powerplant units which are determined to belong
    to the same plant.

    Parameters
    ----------
    df : pandas.Dataframe or string
        dataframe or csv-file to use for the resulting database
    use_saved_aggregation : Boolean (default False):
        Whether to use the automaticly saved aggregation file, which
        is stored in data/aggregation_groups_XX.csv with XX being
        either a custum name for the dataset or the name passed with
        the metadata of the pd.DataFrame. This saves time if you want
        to have aggregated powerplants without running the aggregation
        algorithm again
    dataset_name : str
        costum name for dataset identification, choose your own
        identification in case no metadata is passed to the function


    """
    def prop_for_groups(x):
        """
        Function for grouping duplicates within one dataset. Sums up
        the capacity, takes mean from latitude and longitude, takes
        the most frequent values for the rest of the columns

        """
        results = {'Name': x.Name.value_counts().index[0],
                   'Country': x.Country.value_counts(dropna=False).index[0] ,
#                     if   x.Country.notnull().any(axis=0) else np.NaN,
                   'Fueltype': x.Fueltype.value_counts(dropna=False).index[0],
#                       if x.Fueltype.notnull().any(axis=0) else np.NaN,
                   'Technology': ', '.join(x.Technology.dropna().unique())
                                            if x.Technology.notnull().any(axis=0) else np.NaN,
                   'Set' : ', '.join(x.Set.dropna().unique()),
                   'File': x.File.value_counts(dropna=False).index[0],
                   'Capacity': x['Capacity'].fillna(0.).sum(),
                   'lat': x['lat'].astype(float).mean(),
                   'lon': x['lon'].astype(float).mean(),
                   'YearCommissioned': x['YearCommissioned'].min(),
                   'projectID': list(x.projectID)}
        return pd.Series(results)

    #try to use dataset identifier from df.datasetID
    if dataset_name is None:
        dataset_name = df._metadata[0]

    path_name = _data_out('aggregation_groups_{}.csv'.format(dataset_name))
    if use_saved_aggregation:
        # try:
        # XXX: why  .values?? and NEVER do a catchall except
        df.loc[:, 'grouped'] = pd.read_csv(path_name, header=None,index_col=0 ).values
        # except:
        # print("Non-existing saved links for this dataset, continuing by aggregating again")

    if 'grouped' not in df:
        duplicates = duke(read_csv_if_string(df))
        df = cliques(df, duplicates)
        df.grouped.to_csv(path_name)

    df = df.groupby('grouped').apply(prop_for_groups)
    df.reset_index(drop=True, inplace=True)
    df = df[target_columns()]
    return df

def clean_single(df, aggregate_powerplant_units=True, use_saved_aggregation=False, dataset_name=None):
    df = df.copy()
    """
    Vertical cleaning of the database. Cleans the "Name"-column, sums
    up the capacity of powerplant units which are determined to belong
    to the same plant.

    Parameters
    ----------
    df : pandas.Dataframe or string
        dataframe or csv-file to use for the resulting database

    aggregate_units : Boolean, default True
        Wether or not the power plant units should be aggregated

    use_saved_aggregation : Boolean, default False
        Only sensible if aggregate_units is set to True.
        Whether to use the automatically saved aggregation file, which
        is stored in data/aggregation_groups_XX.csv with XX
        being either a custom name for the dataset or the name passed
        with the metadata of the pd.DataFrame. This saves time if you
        want to have aggregated powerplants without running the
        aggregation algorithm again

    dataset_name : str
        Only sensible if aggregate_units is set to True.  custom name
        for dataset identification, choose your own identification in
        case no metadata is passed to the function
    """
    #df = gather_technology_info(df)
    df = clean_powerplantname(df)

    if aggregate_powerplant_units:
        df = aggregate_units(df, use_saved_aggregation=use_saved_aggregation,
                             dataset_name=dataset_name)
    return clean_technology(df)
