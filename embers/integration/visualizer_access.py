"""Agent-issued, expiring read-only viewer capabilities; no agent credentials shared."""
import base64
import hashlib
import secrets
import time
from ..core.record import EmberRecord
from ..core.types import RecordType

PREFIX = 'visualizer-access-'


def normalize(code):
    if not isinstance(code, str) or len(code)>100:
        raise PermissionError('Invalid or expired viewing code')
    value=code.upper().replace('-','').replace(' ','')
    if len(value)==25 and value.startswith('EMBER'): value=value[5:]
    if len(value)!=20 or any(c not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567' for c in value):
        raise PermissionError('Invalid or expired viewing code')
    return value


def grant_id(code):
    return PREFIX+hashlib.sha256(normalize(code).encode()).hexdigest()


def issue(db, namespace, actor, *, ttl_seconds=900, session_id=None):
    db.require_namespace_access(namespace,actor,'read')
    if type(ttl_seconds) is not int or not 60<=ttl_seconds<=3600:
        raise ValueError('ttl_seconds must be 60..3600')
    if session_id:
        session=db.get_session(session_id)
        if session is None or session.agent_id!=actor or session.status.value!='active':
            raise PermissionError('Active caller-owned session required')
    secret=base64.b32encode(secrets.token_bytes(12)).decode().rstrip('=')
    code='EMBER-'+'-'.join(secret[i:i+5] for i in range(0,20,5))
    rid=grant_id(code);now=time.time()
    data={'kind':'visualizer-access.v1','actor':actor,'namespace':namespace,'session_id':session_id,
          'created_at':now,'expires_at':now+ttl_seconds,'scope':'visualizer:read'}
    db._writer.write(EmberRecord(id=rid,namespace=namespace,record_type=RecordType.RAW,data=data,
        written_by=actor,agent_id=actor,retrieval_candidate=False,training_candidate=False))
    return {'code':code,'grant_id':rid,'namespace':namespace,'expires_at':data['expires_at'],
            'scope':'visualizer:read','viewer_path':'/visualizer#view='+code,
            'note':'Temporary read-only access to this namespace. Share only with the intended viewer.'}


def verify(db, code, namespace=None):
    rid=grant_id(code)
    record=db._store.read(rid)
    if (record is None or record.record_type!=RecordType.RAW or not record.verify_integrity()
        or record.data.get('kind')!='visualizer-access.v1' or record.data.get('scope')!='visualizer:read'
        or record.data['expires_at']<=time.time() or db._store.read(rid+'-revoked') is not None):
        raise PermissionError('Invalid or expired viewing code')
    data=record.data
    if record.namespace!=data['namespace'] or (namespace is not None and namespace!=data['namespace']):
        raise PermissionError('Viewing code does not authorize this namespace')
    db.require_namespace_access(data['namespace'],data['actor'],'read')
    if data.get('session_id'):
        session=db.get_session(data['session_id'])
        if session is None or session.agent_id!=data['actor'] or session.status.value!='active':
            raise PermissionError('Viewing session has ended')
    return data


def revoke(db, rid, actor):
    record=db._store.read(rid) if isinstance(rid,str) and rid.startswith(PREFIX) else None
    if record is None or record.data.get('kind')!='visualizer-access.v1' or record.data.get('actor')!=actor:
        raise PermissionError('Only the issuing agent can revoke this view')
    with db._writer.lock:
        if db._store.read(rid+'-revoked') is None:
            db._writer.write(EmberRecord(id=rid+'-revoked',namespace=record.namespace,record_type=RecordType.RAW,
                data={'kind':'visualizer-access-revocation.v1','grant_id':rid,'revoked_at':time.time()},
                written_by=actor,agent_id=actor,retrieval_candidate=False,training_candidate=False))
    return {'grant_id':rid,'revoked':True}
