"""Contract 02: lexical orientation over existing memories; no state changes."""
import json
from dataclasses import asdict, dataclass
from ..index.fulltext import _tokenize, _extract_text

FIELDS = ('subject', 'primary_context', 'content', 'tags')
DEFAULT_SIGNALS = (*FIELDS, 'relations')


@dataclass(frozen=True)
class OrientationLimits:
    subjects: int = 5
    contexts: int = 10  # total across subjects
    memories_per_context: int = 3
    records: int = 20
    candidates: int = 100
    relationships: int = 24  # examined edges, one hop only
    preview_chars: int = 240
    response_bytes: int = 32768

    def __post_init__(self):
        caps = dict(subjects=20, contexts=40, memories_per_context=10, records=100,
                    candidates=500, relationships=100, preview_chars=1000,
                    response_bytes=131072)
        for key, cap in caps.items():
            value = getattr(self, key)
            if type(value) is not int or not 1 <= value <= cap:
                raise ValueError(f'{key} must be an integer in [1,{cap}]')
        if self.response_bytes < 4096:
            raise ValueError('response_bytes must be at least 4096')


def _size(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode())


def _label(value):
    if value is None:
        return None
    if not isinstance(value, str) or len(value.encode()) > 256:
        raise ValueError('stored label is not a string of at most 256 UTF-8 bytes')
    return value


def orient(db, *, clues, namespace, hints=None, limits=None, signals=None):
    if not isinstance(clues, str) or not clues.strip() or len(clues.encode()) > 2048:
        raise ValueError('clues must be nonempty text of at most 2048 UTF-8 bytes')
    if not isinstance(namespace, str) or not namespace:
        raise ValueError('namespace is required')
    hints = {} if hints is None else hints
    if not isinstance(hints, dict) or set(hints) - {'subject', 'primary_context'}:
        raise ValueError('hints accepts subject and primary_context only')
    for value in hints.values():
        _label(value)
    if limits is not None and not isinstance(limits, dict):
        raise ValueError('limits must be an object')
    try:
        budget = OrientationLimits(**(limits or {}))
    except TypeError as error:
        raise ValueError('unknown limit') from error
    enabled = list(DEFAULT_SIGNALS) if signals is None else signals
    if (not isinstance(enabled, list) or any(not isinstance(s, str) for s in enabled)
            or len(enabled) != len(set(enabled)) or set(enabled) - set(DEFAULT_SIGNALS)
            or not set(enabled).intersection(FIELDS)):
        raise ValueError('signals must be distinct supported names with at least one lexical field')
    words = set(_tokenize(clues))
    hint_words = {key: set(_tokenize(value or '')) for key, value in hints.items()}
    query = ' '.join(sorted(words | set().union(*hint_words.values())))
    # Reuse the persisted/rebuilt full-text index. Selection is bounded, although
    # the existing BM25 implementation can inspect many postings internally.
    with db._fulltext_index._lock:
        hits = db._fulltext_index.search(query, namespace, budget.candidates + 1)
    shortlisted = hits[:budget.candidates]
    rows, ranks, records = {}, {}, {}
    skipped = 0

    def load(rid):
        nonlocal skipped
        record = db._reader.get(rid, track_access=False)
        if (record is None or record.namespace != namespace or not record.retrieval_candidate
                or record.record_type not in db._DURABLE_MEMORY_TYPES):
            return None
        data = record.data if isinstance(record.data, dict) else {'content': record.data}
        try:
            subject, context = _label(data.get('subject')), _label(data.get('primary_context'))
            if len(rid.encode()) > 256:
                raise ValueError('oversize identity')
        except ValueError:
            skipped += 1
            return None
        content = _extract_text(data.get('content', data.get('text', data.get('summary', ''))))
        values = dict(subject=subject or '', primary_context=context or '',
                      content=content, tags=' '.join(record.tags))
        matched = {}
        for field in FIELDS:
            if field not in enabled:
                continue
            tokens = set(_tokenize(values[field]))
            match, hint = sorted(words & tokens), sorted(hint_words.get(field, set()) & tokens)
            if match or hint:
                matched[field] = {'clue_terms': match, 'hint_terms': hint}
        unique = set(t for value in matched.values() for t in value['clue_terms'])
        hint_count = sum(len(value['hint_terms']) for value in matched.values())
        rank = (-len(unique), -hint_count, -len(matched), rid)
        row = dict(id=rid, subject=subject, primary_context=context,
                   preview=content[:budget.preview_chars], preview_truncated=len(content) > budget.preview_chars,
                   signals=matched, relationships=[],
                   provenance={'written_by': str(record.written_by)[:128],
                               'created_at': record.created_at.isoformat(),
                               'version': record.version},
                   evidence={'assessment': 'not_performed', 'attached_evidence': 'not_scanned',
                             'provenance_reference_count': len(record.derived_from),
                             'annotation_count': len(record.annotations)},
                   ranking={'unique_clue_terms': len(unique), 'hint_terms': hint_count,
                            'matching_fields': len(matched)})
        return record, row, rank

    for rid, _score in shortlisted:
        item = load(rid)
        if item and item[1]['signals']:
            records[rid], rows[rid], ranks[rid] = item
    seeds = sorted(rows, key=lambda rid: ranks[rid])
    edges_examined = 0
    if 'relations' in enabled:
        for source in seeds:
            if edges_examined >= budget.relationships:
                break
            # Existing native graph, outgoing only. No recursive expansion and
            # no inference of reverse/semantic relationships.
            edges = db._graph_index.get_edges(source, direction='outgoing')
            for edge in edges:
                if edges_examined >= budget.relationships:
                    break
                edges_examined += 1
                target = edge['target']
                if target not in rows:
                    if len(rows) >= budget.candidates:
                        continue
                    item = load(target)
                    if item is None:
                        continue
                    records[target], rows[target], ranks[target] = item
                relation = {'from_id': source, 'to_id': target,
                            'type': str(edge.get('edge_type', ''))[:128],
                            'label': str(edge.get('label', ''))[:128]}
                rows[target]['relationships'].append(relation)
    ordered = sorted(rows, key=lambda rid: ranks[rid])
    output = {'method': 'orientation-lexical-v1', 'clues': clues, 'hints': hints,
              'namespace': namespace, 'enabled_signals': enabled, 'bounds': asdict(budget),
              'coverage': 'bounded BM25 shortlist, not exhaustive',
              'candidate_ids': ordered, 'territory': [], 'returned_ids': [],
              'diagnostics': {'shortlist_count': len(shortlisted),
                  'shortlist_capped': len(hits) > budget.candidates,
                  'edges_examined': edges_examined, 'skipped_oversize_labels': skipped,
                  'candidate_ids_omitted': 0, 'byte_budget_trimmed': False},
              'decision': 'agent_selects_context', 'response_bytes': 0}
    subjects, contexts = {}, {}
    for rid in ordered:
        row = rows[rid]
        subject, context = row['subject'], row['primary_context']
        key = (subject, context)
        if subject not in subjects and len(subjects) >= budget.subjects:
            continue
        if key not in contexts and len(contexts) >= budget.contexts:
            continue
        if len(output['returned_ids']) >= budget.records:
            break
        if key in contexts and len(contexts[key]['memories']) >= budget.memories_per_context:
            continue
        if subject not in subjects:
            subjects[subject] = {'subject': subject, 'contexts': []}
            output['territory'].append(subjects[subject])
        if key not in contexts:
            contexts[key] = {'primary_context': context, 'memories': []}
            subjects[subject]['contexts'].append(contexts[key])
        contexts[key]['memories'].append(row)
        output['returned_ids'].append(rid)

    # Count the complete JSON payload, including observations and echoed inputs.
    def measure():
        for _ in range(8):
            size = _size(output)
            if size == output['response_bytes']:
                return size
            output['response_bytes'] = size
        return _size(output)

    while measure() > budget.response_bytes:
        output['diagnostics']['byte_budget_trimmed'] = True
        if output['candidate_ids']:
            output['candidate_ids'].pop()
            output['diagnostics']['candidate_ids_omitted'] += 1
        elif output['territory']:
            subject = output['territory'][-1]
            context = subject['contexts'][-1]
            row = context['memories'].pop()
            output['returned_ids'].remove(row['id'])
            if not context['memories']:
                subject['contexts'].pop()
            if not subject['contexts']:
                output['territory'].pop()
        else:
            raise ValueError('echoed request exceeds response budget')
    return output
