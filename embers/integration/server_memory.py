"""Prepare the existing Candidate 04 live-test service in the normal server.

This is startup wiring, not new context-resolution or learning policy.
"""
import json
import logging
import os
from pathlib import Path


def _prepare_memory_services(db, store_path, *, encoding_name=None, credentials_path=None):
    import tiktoken
    from ..identity.registry import AgentRegistry
    from .candidate_recall import CandidateRecall

    root = Path(store_path).resolve()
    credentials = (Path(credentials_path).resolve() if credentials_path else
                   root.parent / (root.name + '-candidate-credentials.json'))
    if credentials.is_relative_to(root):
        raise ValueError('candidate credentials must be outside the managed store')
    encoding_name = encoding_name or os.environ.get('EMBER_TOKEN_ENCODING', 'cl100k_base')
    encoding = tiktoken.get_encoding(encoding_name)
    registry = AgentRegistry(db)
    # The store lock serializes bootstrap across cooperating server processes.
    with db._writer.lock:
        if credentials.exists():
            auth = json.loads(credentials.read_text())
            registry.authenticate(auth['agent_id'], auth['token'])
        else:
            agent, token = registry.register('candidate-feedback-agent')
            auth = {'agent_id': agent.agent_id, 'token': token}
            fd = os.open(credentials, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as stream:
                json.dump(auth, stream)
                stream.flush()
                os.fsync(stream.fileno())
        journal = db.relevance_journal(namespace='memories', context_id='live-test',
            context={'task': 'live-test'}, authorized_resolvers=frozenset({auth['agent_id']}),
            memory_rate=.25, pair_rate=.25)
        service = CandidateRecall(journal, tokenizer_id=f'tiktoken:{encoding_name}',
            token_counter=lambda text: len(encoding.encode(text, disallowed_special=())))
    logging.getLogger('embers').warning(
        'Candidate 04 ready on this server: namespace=memories context=live-test; '
        'private agent credentials: %s; encoding=%s', credentials, encoding_name)
    return service


def prepare_memory_services(db, store_path, **options):
    from ..core.errors import StorageLimitError
    try:
        return _prepare_memory_services(db, store_path, **options)
    except (StorageLimitError, OSError) as error:
        if not isinstance(error, StorageLimitError) and 'storage.max_total_bytes' not in str(error):
            raise
        logging.getLogger('embers').error(
            'Candidate 04 setup blocked by storage limit; ordinary server remains available: %s', error)
        return None
