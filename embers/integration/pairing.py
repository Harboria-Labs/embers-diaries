"""Pairing V1 IO only. Rust selects; existing FUR derives W."""
from ..core.domain import call
from ..cognitive.usefulness import derive
from .usefulness_service import service


def select(db, namespace, direct_ids, context, state=None, query_id=None):
    if not direct_ids:
        return None, None
    with db._writer.lock:
        state = state if state is not None else service(db, namespace)._load()
        provenance = call('pair_select', dict(direct_ids=direct_ids, context=context,
            edges=db._graph_index.get_edges(direct_ids[0], direction='outgoing'),
            states=derive(state['experiences'], state['policy']), neutral=state['policy']['w0']))
        if provenance is None:
            return None, None
        # Availability is an admission constraint after selection, never a rerank.
        rec = db._reader.get(provenance['target'], track_access=False)
        if rec is None or rec.namespace != namespace or rec.record_type not in db._DURABLE_MEMORY_TYPES or not rec.retrieval_candidate:
            return None, None
        provenance['query_request_id'] = query_id
        return rec, provenance


def link(db, actor, *, source, target, relation, primary_context):
    """Authorized creation of an explicitly contextual stored relationship."""
    call('fur_target', {'kind':'pair','memory_ids':[source,target],'relation':relation})
    call('context', primary_context)
    with db._writer.lock:
        a = db._reader.get(source, track_access=False)
        b = db._reader.get(target, track_access=False)
        if a is None or b is None:
            raise ValueError('relationship endpoint unavailable')
        db.require_namespace_access(a.namespace, actor, 'write')
        db.require_namespace_access(b.namespace, actor, 'write')
        if a.namespace != b.namespace:
            raise ValueError('pair endpoints must share a namespace')
        # Exact link retries reuse the stored identity and do not add duplicate edges.
        for edge in db._graph_index.get_edges(source, direction='outgoing'):
            if edge['target']==target and edge['edge_type']==relation and 'primary_context' in edge.get('metadata',{}) and edge['metadata']['primary_context']==primary_context:
                return {'source':source, **edge}
        db.link(source, target, relation, primary_context=primary_context)
        return {'source':source, **db._graph_index.get_edges(source, direction='outgoing')[-1]}
