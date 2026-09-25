"""Opt-in live Candidate 04 harness; never uses the production store by default."""
import argparse
import json
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--store', default='./ember-experimental-store')
    parser.add_argument('--credentials', default='./ember-experimental-credentials.json')
    parser.add_argument('--port', type=int, default=9200)
    parser.add_argument('--encoding', required=True, help='tiktoken encoding used by your test model')
    parser.add_argument('--max-bytes', type=int, default=268435456)
    args = parser.parse_args()
    import tiktoken
    import uvicorn
    from . import api
    from .db import EmberDB
    from .identity.registry import AgentRegistry
    from .integration.candidate_recall import CandidateRecall
    from .config import load_config
    config = load_config(env={}, overrides={'storage.path': args.store,
        'storage.max_total_bytes': args.max_bytes, 'api.host': '127.0.0.1', 'api.port': args.port})
    config.require_runtime_supported()
    db = EmberDB.connect(args.store, max_total_bytes=args.max_bytes, runtime_config=config)
    from .integration.server_memory import prepare_memory_services
    prepare_memory_services(db, args.store, encoding_name=args.encoding,
                            credentials_path=args.credentials)
    registry = AgentRegistry(db)
    credentials = Path(args.credentials).resolve()
    api._config, api._db, api._registry = config, db, registry
    print(f'Experimental server: http://127.0.0.1:{args.port}/mcp')
    print(f'Credentials saved at {credentials}; do not post the token publicly.')
    print('Context: live-test; namespace: memories; model time is explicit, not wall-clock seconds.')
    uvicorn.run(api.app, host='127.0.0.1', port=args.port)


if __name__ == '__main__':
    main()
