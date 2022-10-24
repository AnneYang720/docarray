import functools
import random
from time import perf_counter

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
from docarray import Document, DocumentArray
from rich.console import Console
from rich.table import Table


def timer(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        start = perf_counter()
        res = func(*args, **kwargs)
        return (perf_counter() - start, res)

    return wrapper


@timer
def create(da, docs):
    da.extend(docs)


@timer
def read(da, ids):
    da[ids]


@timer
def update(da, docs):
    da[[d.id for d in docs]] = docs


@timer
def delete(da, ids):
    del da[ids]


@timer
def find_by_condition(da, query):
    da.find(query)


@timer
def find_by_vector(da, query, limit, **kwargs):
    return da.find(query, limit=limit, **kwargs)


def get_docs(train):
    return [
        Document(
            embedding=x,
            tags={'i': int(i)},
        )
        for i, x in enumerate(train)
    ]


def fmt(value, unit):
    return '{:.3f} {}'.format(value, unit)


def get_configuration_storage_backends(argparse, D, random=True):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--default-hnsw',
        help='Whether to use default HNSW configurations',
        action='store_true',
    )

    parser.add_argument(
        '--exclude-backends',
        help='list of comma separated backends to exclude from the benchmarks',
        type=str,
        default='',
    )

    args = parser.parse_args()

    storage_backends = {
        'memory': {
            'storage_config': None,
        },
        'sqlite': {
            'storage_config': None,
        },
        'annlite': {
            'storage_config': {
                'n_dim': D,
                'columns': {'i': 'int'},
            },
        },
        'qdrant': {
            'storage_config': {
                'n_dim': D,
                'port': '41233',
                'scroll_batch_size': 8,
            },
        },
        'weaviate': {
            'storage_config': {
                'n_dim': D,
                'port': '41234',
                'columns': {'i': 'int'},
            },
        },
        'elasticsearch': {
            'storage_config': {
                'n_dim': D,
                'hosts': 'http://localhost:41235',
                'columns': {'i': 'int'},
                'es_config': {'timeout': 1000},
            },
        },
        'redis': {
            'storage_config': {
                'n_dim': D,
                'port': '41236',
                'columns': {'i': 'int'},
            },
        },
    }

    if random:
        if not args.default_hnsw:
            storage_backends['annlite']['storage_config'].update(
                {
                    'ef_construction': 100,
                    'ef_search': 100,
                    'max_connection': 16,
                }
            )
            storage_backends['qdrant']['storage_config'].update(
                {
                    'ef_construct': 100,
                    'm': 16,
                }
            )
            storage_backends['weaviate']['storage_config'].update(
                {
                    'ef': 100,
                    'ef_construction': 100,
                    'max_connections': 16,
                }
            )
            storage_backends['elasticsearch']['storage_config'].update(
                {
                    'ef_construction': 100,
                    'm': 16,
                }
            )
            storage_backends['redis']['storage_config'].update(
                {
                    'ef_construction': 100,
                    'm': 16,
                    'ef_runtime': 100,
                }
            )

        storage_backends = [
            (storage, configs['storage_config'])
            for storage, configs in storage_backends.items()
            if storage not in (args.exclude_backends or '').split(',')
        ]
    else:
        storage_backends['annlite']['storage_config']['metric'] = 'euclidean'
        storage_backends['annlite']['hnsw_config'] = {
            'max_connection': [16, 32],
            'ef_construction': [128, 256],
            'ef_search': [64, 128, 256],
        }

        storage_backends['qdrant']['storage_config']['distance'] = 'euclidean'
        storage_backends['qdrant']['hnsw_config'] = {
            'm': [12, 16, 32],
            'ef_construct': [32, 64, 128],
        }

        storage_backends['weaviate']['storage_config']['distance'] = 'l2-squared'
        storage_backends['weaviate']['hnsw_config'] = {
            'max_connections': [16, 32],
            'ef_construction': [128, 256],
            'ef': [64, 128, 256],
        }

        storage_backends['elasticsearch']['storage_config']['distance'] = 'l2_norm'
        storage_backends['elasticsearch']['hnsw_config'] = {
            'm': [8, 12, 16],
            'ef_construction': [16, 32, 64, 128],
        }

        storage_backends['redis']['storage_config']['distance'] = 'L2'
        storage_backends['redis']['hnsw_config'] = {
            'm': [16, 32],
            'ef_construction': [128, 256],
            'ef_runtime': [64, 128, 256],
        }

        for storage in args.exclude_backends.split(','):
            storage_backends.pop(storage, None)

    return storage_backends


storage_backend_filters = {
    'memory': {'tags__i': {'$eq': 0}},
    'sqlite': {'tags__i': {'$eq': 0}},
    'annlite': {'i': {'$eq': 0}},
    'qdrant': {'tags__i': {'$eq': 0}},
    'weaviate': {'path': 'i', 'operator': 'Equal', 'valueInt': 0},
    'elasticsearch': {'match': {'i': 0}},
    'redis': {'i': {'$eq': 0}},
}


def recall(predicted, relevant, eval_at):
    if eval_at == 0:
        return 0.0
    predicted_at_k = predicted[:eval_at]
    n_predicted_and_relevant = len(
        set(predicted_at_k[:, 'tags__i']).intersection(set(relevant))
    )
    return n_predicted_and_relevant / len(relevant)


def run_benchmark(
    docs,
    test,
    ground_truth,
    n_index,
    n_vector_queries,
    n_query,
    storage_backends,
    K,
):
    table = Table(
        title=f'DocArray Random Benchmarking n_index={n_index} n_query={n_query} D={test[0].shape[0]} K={K}'
    )
    benchmark_df = pd.DataFrame(
        {
            'Storage Backend': [],
            'Indexing time (C)': [],
            'Query (R)': [],
            'Update (U)': [],
            'Delete (D)': [],
            'Find by vector': [],
            f'Recall at k={K} for vector search': [],
            'Find by condition': [],
        }
    )

    for col in benchmark_df.columns:
        table.add_column(col)

    console = Console()

    docs_to_delete = random.sample(docs, n_query)
    docs_to_update = random.sample(docs, n_query)
    vector_queries = [x for x in test]

    find_by_vector_time_all = []
    create_time_all = []

    for backend, config in storage_backends:
        try:
            console.print('\nBackend:', backend.title())
            console.print('Backend config', str(config))

            if not config:
                da = DocumentArray(storage=backend)
            else:
                da = DocumentArray(storage=backend, config=config)

            console.print(f'\tindexing {n_index} docs ...')
            create_time, _ = create(da, docs)

            # for n_q in n_query:
            console.print(f'\treading {n_query} docs ...')
            read_time, _ = read(
                da,
                random.sample([d.id for d in docs], n_query),
            )

            console.print(f'\tupdating {n_query} docs ...')
            update_time, _ = update(da, docs_to_update)

            console.print(f'\tdeleting {n_query} docs ...')
            delete_time, _ = delete(da, [d.id for d in docs_to_delete])

            console.print(
                f'\tfinding {n_query} docs by vector averaged {n_vector_queries} times ...'
            )

            if backend == 'sqlite':
                find_by_vector_time, result = find_by_vector(
                    da, vector_queries[0], limit=K
                )
                recall_at_k = recall(result, ground_truth[0], K)
            else:
                recall_at_k_values = []
                find_by_vector_times = []
                for i, query in enumerate(vector_queries):
                    find_by_vector_time, results = find_by_vector(da, query, limit=K)
                    find_by_vector_times.append(find_by_vector_time)
                    if backend == 'memory':
                        ground_truth.append(results[:, 'tags__i'])
                        recall_at_k_values.append(1)
                    else:
                        recall_at_k_values.append(recall(results, ground_truth[i], K))

                recall_at_k = np.mean(recall_at_k_values)
                find_by_vector_time = np.mean(find_by_vector_times)

            console.print(f'\tfinding {n_query} docs by condition ...')
            find_by_condition_time, _ = find_by_condition(
                da, storage_backend_filters[backend]
            )

            table.add_row(
                backend.title(),
                fmt(create_time, 's'),
                fmt(read_time * 1000, 'ms'),
                fmt(update_time * 1000, 'ms'),
                fmt(delete_time * 1000, 'ms'),
                fmt(find_by_vector_time, 's'),
                '{:.3f}'.format(recall_at_k),
                fmt(find_by_condition_time, 's'),
            )
            benchmark_df.loc[len(benchmark_df.index)] = [
                backend.title(),
                create_time,
                read_time,
                update_time,
                delete_time,
                find_by_vector_time,
                recall_at_k,
                find_by_condition_time,
            ]

            find_by_vector_time_all.append(find_by_vector_time)
            create_time_all.append(create_time)

            da.clear()
            del da
        except Exception as e:
            console.print(f'Storage Backend {backend} failed: {e}')

    console.print(table)
    return find_by_vector_time_all, create_time_all, benchmark_df


def save_benchmark_df(benchmark_df, n):
    benchmark_df.to_csv(f'benchmark-seconds-{n}.csv')

    benchmark_df['Indexing time (C)'] = benchmark_df['Indexing time (C)'].apply(
        lambda value: 1_000_000 / value
    )
    benchmark_df['Query (R)'] = benchmark_df['Query (R)'].apply(lambda value: 1 / value)
    benchmark_df['Update (U)'] = benchmark_df['Update (U)'].apply(
        lambda value: 1 / value
    )
    benchmark_df['Delete (D)'] = benchmark_df['Delete (D)'].apply(
        lambda value: 1 / value
    )
    benchmark_df['Find by vector'] = benchmark_df['Find by vector'].apply(
        lambda value: 1 / value
    )
    benchmark_df['Find by condition'] = benchmark_df['Find by condition'].apply(
        lambda value: 1 / value
    )

    benchmark_df.to_csv(f'benchmark-qps-{n}.csv')


def plot_results(
    find_by_vector_values, storage_backends, create_values, plot_legend=True
):
    find_df = pd.DataFrame(find_by_vector_values)
    find_df.index = [backend for backend, _ in storage_backends]
    find_df = find_df.drop(['sqlite'], errors='ignore')
    print('\n\nQuery times')
    print(find_df)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(17, 5))

    find_df.plot(
        kind="bar",
        ax=ax1,
        fontsize=16,
        color=sns.color_palette('muted')[1:4],
        # title='Find by vector per backend and dataset size',
        # ylabel='seconds',
        rot=0,
        legend=plot_legend,
    )
    ax1.set_ylabel('seconds', fontsize=18)
    ax1.set_title('Find by vector per backend', fontsize=18)

    threshold = 0.3
    ax1.hlines(y=threshold, xmin=-20, xmax=20, linewidth=2, color='r', linestyle='--')

    create_df = pd.DataFrame(create_values)
    create_df.index = [backend for backend, _ in storage_backends]

    create_df = create_df.drop(['memory'])
    print('\n\nIndexing times')
    print(create_df)
    create_df.plot(
        kind="bar",
        ax=ax2,
        fontsize=16,
        color=sns.color_palette('muted')[1:4],
        # title='Indexing per backend and dataset size',
        # ylabel='seconds',
        rot=0,
        legend=plot_legend,
    )

    ax2.set_ylabel('seconds', fontsize=18)
    ax2.set_title('Indexing per backend', fontsize=18)

    plt.tight_layout()
    ax1.legend(fontsize=15)
    ax2.legend(fontsize=15)
    plt.savefig('benchmark.svg')


def run_benchmark_sift(
    test,
    docs,
    ground_truth,
    n_index,
    n_vector_queries,
    n_query,
    storage,
    storage_config,
    hnsw_config,
    K,
):
    table = Table(title=f'DocArray Sift1M Benchmarking storage={storage}')
    benchmark_df = pd.DataFrame(
        {
            'Storage Backend': [],
            'M': [],
            'EF_CONSTRUCTION': [],
            'EF_RUNTIME': [],
            'Indexing time (C)': [],
            'Query (R)': [],
            'Update (U)': [],
            'Delete (D)': [],
            'Find by vector': [],
            f'Recall at k={K} for vector search': [],
            'Find by condition': [],
        }
    )

    for col in benchmark_df.columns:
        table.add_column(col)

    console = Console()

    docs_to_delete = random.sample(docs, n_query)
    docs_to_update = random.sample(docs, n_query)
    vector_queries = [x for x in test]

    for config in hnsw_config:
        try:
            console.print('\nBackend:', storage.title())
            console.print('Backend hnsw config', str(config))

            if not storage_config:
                da = DocumentArray(storage=storage)
            else:
                config.update(storage_config)
                da = DocumentArray(storage=storage, config=config)

            console.print(f'\tindexing {n_index} docs ...')
            create_time, _ = create(da, docs)

            # for n_q in n_query:
            console.print(f'\treading {n_query} docs ...')
            read_time, _ = read(
                da,
                random.sample([d.id for d in docs], n_query),
            )

            console.print(
                f'\tfinding {n_query} docs by vector averaged {n_vector_queries} times ...'
            )

            if storage == 'memory' or storage == 'sqlite':
                find_by_vector_time, result = find_by_vector(
                    da, vector_queries[0], limit=K, metric='euclidean'
                )
                recall_at_k = recall(result, ground_truth[0], K)
            else:
                recall_at_k_values = []
                find_by_vector_times = []
                for i, query in enumerate(vector_queries):
                    find_by_vector_time, results = find_by_vector(da, query, limit=K)
                    find_by_vector_times.append(find_by_vector_time)
                    recall_at_k_values.append(recall(results, ground_truth[i], K))

                recall_at_k = np.mean(recall_at_k_values)
                find_by_vector_time = np.mean(find_by_vector_times)

            console.print(f'\tfinding {n_query} docs by condition ...')
            find_by_condition_time, _ = find_by_condition(
                da, storage_backend_filters[storage]
            )

            console.print(f'\tupdating {n_query} docs ...')
            update_time, _ = update(da, docs_to_update)

            console.print(f'\tdeleting {n_query} docs ...')
            delete_time, _ = delete(da, [d.id for d in docs_to_delete])

            table.add_row(
                storage.title(),
                str(config.get(get_param(storage, 'M'), None)),
                str(config.get(get_param(storage, 'EF_CONSTRUCTION'), None)),
                str(config.get(get_param(storage, 'EF_RUNTIME'), None)),
                fmt(create_time, 's'),
                fmt(read_time * 1000, 'ms'),
                fmt(update_time * 1000, 'ms'),
                fmt(delete_time * 1000, 'ms'),
                fmt(find_by_vector_time * 1000, 'ms'),
                '{:.3f}'.format(recall_at_k),
                fmt(find_by_condition_time, 's'),
            )
            benchmark_df.loc[len(benchmark_df.index)] = [
                storage.title(),
                config.get(get_param(storage, 'M'), None),
                config.get(get_param(storage, 'EF_CONSTRUCTION'), None),
                config.get(get_param(storage, 'EF_RUNTIME'), None),
                create_time,
                read_time,
                update_time,
                delete_time,
                find_by_vector_time,
                recall_at_k,
                find_by_condition_time,
            ]

            # print and store benchmark time
            console.print(table)
            benchmark_df.to_csv(f'benchmark-seconds-{storage}.csv')

            da.clear()
            del da
        except Exception as e:
            console.print(f'Storage Backend {storage} failed: {e}')

    console.print(table)
    return benchmark_df


param_dict = {
    'annlite': {
        'M': 'max_connection',
        'EF_CONSTRUCTION': 'ef_construction',
        'EF_RUNTIME': 'ef_search',
    },
    'qdrant': {'M': 'm', 'EF_CONSTRUCTION': 'ef_construct'},
    'weaviate': {
        'M': 'max_connections',
        'EF_CONSTRUCTION': 'ef_construction',
        'EF_RUNTIME': 'ef',
    },
    'elasticsearch': {'M': 'm', 'EF_CONSTRUCTION': 'ef_construction'},
    'redis': {
        'M': 'm',
        'EF_CONSTRUCTION': 'ef_construction',
        'EF_RUNTIME': 'ef_runtime',
    },
}


def get_param(storage, param):
    if storage == 'memory' or storage == 'sqlite':
        return param

    return param_dict[storage].get(param, param)


def plot_results_sift(storages):
    fig, ax = plt.subplots()
    storages = list(storages)
    if 'memory' in storages:
        storages.remove('memory')
    if 'sqlite' in storages:
        storages.remove('sqlite')

    for storage in storages:
        df = pd.read_csv(f'benchmark-qps-{storage}.csv')
        df.rename(columns={'Recall at k=10 for vector search': 'Recall'}, inplace=True)
        df.sort_values(by=['Recall'], inplace=True)
        df.plot(
            style='.-',
            ax=ax,
            x='Recall',
            y='Find by vector',
            ylabel='Queries per second (1/s)',
            label=storage,
        )

    ax.set_title('Recall/Queries per second (1/s)', fontsize=18)

    plt.savefig('benchmark.png')