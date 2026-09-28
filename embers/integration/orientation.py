"""Contract 02 IO/tokenizer adapter. Rust owns eligibility, ranking and bounds."""
from dataclasses import dataclass, asdict
from ..core.domain import call
from ..index.fulltext import _tokenize, _extract_text

FIELDS = ('subject', 'primary_context', 'content', 'tags')
DEFAULT_SIGNALS = (*FIELDS, 'relations')

@dataclass(frozen=True)
class OrientationLimits:
    subjects: int = 5
    contexts: int = 10
    memories_per_context: int = 3
    records: int = 20
    candidates: int = 100
    relationships: int = 24
    preview_chars: int = 240
    response_bytes: int = 32768

    def __post_init__(self):
        call('orientation', {'prepare':True,'clues':'validation','namespace':'validation','limits':asdict(self)})


def orient(db, *, clues, namespace, hints=None, limits=None, signals=None):
    args = dict(clues=clues, namespace=namespace, hints=hints, limits=limits, signals=signals)
    prepared = call('orientation', dict(args, prepare=True))
    budget, hints = prepared['bounds'], prepared['hints']
    words = set(_tokenize(clues))
    hint_words = {k:set(_tokenize(v or '')) for k,v in hints.items()}
    query = ' '.join(sorted(words | set().union(*hint_words.values())))
    with db._fulltext_index._lock:
        hits = db._fulltext_index.search(query, namespace, budget['candidates']+1)
    records = {}
    def load(rid):
        if rid in records: return
        record = db._reader.get(rid, track_access=False)
        if record is None: return
        data = record.data if isinstance(record.data,dict) else {'content':record.data}
        content = _extract_text(data.get('content',data.get('text',data.get('summary',''))))
        values = dict(subject=data.get('subject'), primary_context=data.get('primary_context'), content=content, tags=' '.join(record.tags))
        records[rid] = dict(id=rid, namespace=record.namespace, record_type=record.record_type.value,
            retrieval_candidate=record.retrieval_candidate, current=True,
            subject=values['subject'], primary_context=values['primary_context'], content=content,
            tokens={k:_tokenize(v) if isinstance(v,str) else [] for k,v in values.items()},
            provenance={'written_by':str(record.written_by)[:128],'created_at':record.created_at.isoformat(),'version':record.version},
            evidence={'assessment':'not_performed','attached_evidence':'not_scanned','provenance_reference_count':len(record.derived_from),'annotation_count':len(record.annotations)})
    ids = [rid for rid,_ in hits[:budget['candidates']]]
    for rid in ids: load(rid)
    inputs = dict(args, records=records, shortlisted=ids, shortlist_capped=len(hits)>budget['candidates'], words=sorted(words), hint_words={k:sorted(v) for k,v in hint_words.items()}, edges={})
    planning = call('orientation', dict(inputs, plan=True))
    remaining = budget['relationships']
    if 'relations' in prepared['signals']:
        for rid in planning['seeds']:
            edges = db._graph_index.get_edges(rid,direction='outgoing')[:remaining]
            inputs['edges'][rid] = edges
            remaining -= len(edges)
            for edge in edges: load(edge['target'])
            if not remaining: break
    return call('orientation', inputs)
