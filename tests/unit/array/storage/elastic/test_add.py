from docarray import Document, DocumentArray
import numpy as np

import pytest


@pytest.mark.filterwarnings('ignore::UserWarning')
def test_add_ignore_existing_doc_id(start_storage):
    elastic_doc = DocumentArray(
        storage='elasticsearch',
        config={
            'n_dim': 3,
            'columns': [('price', 'int')],
            'distance': 'l2_norm',
            'index_name': 'test_add_ignore_existing_doc_id',
        },
    )

    with elastic_doc:
        elastic_doc.extend(
            [
                Document(id='r0', embedding=[0, 0, 0]),
                Document(id='r1', embedding=[1, 1, 1]),
                Document(id='r2', embedding=[2, 2, 2]),
                Document(id='r3', embedding=[3, 3, 3]),
                Document(id='r4', embedding=[4, 4, 4]),
            ]
        )

    with elastic_doc:
        elastic_doc.extend(
            [
                Document(id='r0', embedding=[0, 0, 0]),
                Document(id='r2', embedding=[2, 2, 2]),
                Document(id='r4', embedding=[4, 4, 4]),
                Document(id='r5', embedding=[2, 2, 2]),
                Document(id='r6', embedding=[4, 4, 4]),
            ]
        )

    indexed_offset_count = elastic_doc._client.count(
        index=elastic_doc._index_name_offset2id
    )['count']

    assert len(elastic_doc) == len(elastic_doc[:, 'embedding'])
    assert len(elastic_doc) == indexed_offset_count
    assert len(elastic_doc[:, 'embedding']) == 7


@pytest.mark.filterwarnings('ignore::UserWarning')
def test_add_skip_wrong_data_type_and_fix_offset(start_storage):
    elastic_doc = DocumentArray(
        storage='elasticsearch',
        config={
            'n_dim': 3,
            'columns': [('price', 'int')],
            'index_name': 'test_add_skip_wrong_data_type_and_fix_offset',
        },
    )

    with elastic_doc:
        elastic_doc.extend(
            [
                Document(id='0', price=1000),
                Document(id='1', price=20000),
                Document(id='2', price=103000),
            ]
        )

    with pytest.raises(IndexError):
        with elastic_doc:
            elastic_doc.extend(
                [
                    Document(id='0', price=10000),
                    Document(id='1', price=20000),
                    Document(id='3', price=30000),
                    Document(id='4', price=100000000000),  # overflow int32
                    Document(id='5', price=2000),
                    Document(id='6', price=100000000000),  # overflow int32
                    Document(id='7', price=30000),
                ]
            )

    expected_ids = ['0', '1', '2', '3', '5', '7']

    assert len(elastic_doc) == 6
    assert len(elastic_doc[:, 'id']) == 6
    assert elastic_doc[:, 'id'] == expected_ids
    assert elastic_doc._offset2ids.ids == expected_ids


@pytest.mark.filterwarnings('ignore::UserWarning')
@pytest.mark.parametrize("assert_customization_propagation", [True, False])
def test_succes_add_bulk_custom_params(
    monkeypatch, start_storage, assert_customization_propagation
):
    bulk_custom_params = {
        'thread_count': 4,
        'chunk_size': 100,
        'max_chunk_bytes': 104857600,
        'queue_size': 4,
    }
    nrof_docs = 100

    def _mock_send_requests(requests, **kwargs):
        # Currently only self._send_requests from extend method which
        # receive customization
        if (
            not requests[0]['_index'].startswith('offset2id__')
            and requests[0]['_op_type'] == 'index'
        ):
            assert kwargs == bulk_custom_params

        return [{'index': {'_id': f'r{i}'}} for i in range(nrof_docs)]

    elastic_doc = DocumentArray(
        storage='elasticsearch',
        config={
            'n_dim': 3,
            'columns': [
                ('is_true', 'bool'),
                ('test_long', 'long'),
                ('test_double', 'double'),
            ],
            'distance': 'l2_norm',
            'index_name': 'test_succes_add_bulk_custom_params',
        },
    )

    if assert_customization_propagation:
        monkeypatch.setattr(elastic_doc, '_send_requests', _mock_send_requests)

    with elastic_doc:
        elastic_doc.extend(
            [
                Document(id=f'r{i}', embedding=np.ones((3,)) * i)
                for i in range(nrof_docs)
            ],
            **bulk_custom_params,
        )
