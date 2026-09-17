"""Tenant-owned durable run registration, observation and explicit resolution."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote
import re
from ._agent import _id, encode, decode
from .errors import ValidationError

@dataclass(frozen=True)
class AgentRun:
    run_id: str
    name: str
    version: str
    image_digest: str
    status: str
    epoch: int
    input: Any = None
    result: Any = None

    @classmethod
    def from_dict(cls, value):
        return cls(run_id=value['run_id'], name=value['name'], version=value['version'],
                   image_digest=value['image_digest'], status=value['status'], epoch=value['epoch'],
                   input=decode(value['input']) if value.get('input') else None,
                   result=decode(value['result']) if value.get('result') else None)


def _create(run_id, name, version, image_digest, input):
    if not isinstance(image_digest,str) or not re.fullmatch(r'sha256:[a-f0-9]{64}',image_digest):
        raise ValidationError('image_digest must be a pinned sha256 manifest digest')
    return dict(run_id=_id(run_id),name=_id(name),version=_id(version),image_digest=image_digest,encoding='json-v1',input=encode(input))


def _resolve(step_id, expected_revision, decision, reason, evidence_digest, result):
    if type(expected_revision) is not int or expected_revision < 1:
        raise ValidationError('expected_revision must be positive')
    if decision not in ('completed','no_effect','cancelled'):
        raise ValidationError('decision must be completed, no_effect or cancelled')
    if not isinstance(reason,str) or not 1 <= len(reason.encode('utf-8')) <= 1024:
        raise ValidationError('reason must contain 1 through 1024 UTF-8 bytes')
    if not isinstance(evidence_digest,str) or not re.fullmatch(r'sha256:[a-f0-9]{64}',evidence_digest):
        raise ValidationError('evidence_digest must identify the independently verified evidence')
    out=dict(step_id=_id(step_id),expected_revision=expected_revision,decision=decision,reason=reason,evidence_digest=evidence_digest)
    if decision=='completed': out['result']=encode(result)
    return out

class AgentRuns:
    def __init__(self, client, sandbox_id):
        self._client=client
        self._path='/v1/sandboxes/'+quote(sandbox_id,safe='')+'/agent-runs'

    def create(self, *, run_id: str, name: str, version: str, image_digest: str, input: Any) -> AgentRun:
        """Register immutable input and a pinned image before starting the driver."""
        body=_create(run_id,name,version,image_digest,input)
        return AgentRun.from_dict(self._client._request('POST',self._path,json=body))

    def delete(self, run_id: str) -> AgentRun:
        """Erase payloads and permanently fence this run identity."""
        return AgentRun.from_dict(self._client._request('DELETE',self._path+'/'+quote(_id(run_id),safe='').replace('.', '%2E')))

    def get(self, run_id: str) -> AgentRun:
        return AgentRun.from_dict(self._client._request('GET',self._path+'/'+quote(_id(run_id),safe='').replace('.', '%2E')))

    def steps(self, run_id: str, *, after: str='', limit: int=100) -> dict[str, Any]:
        """Read one page of step metadata and its next_after cursor."""
        if type(limit) is not int or not 1<=limit<=100: raise ValidationError('limit must be 1 through 100')
        if after: _id(after)
        return self._client._request('GET',self._path+'/'+quote(_id(run_id),safe='').replace('.', '%2E')+'/steps',params={'after':after,'limit':limit})

    def resolve(self, run_id: str, *, step_id: str, expected_revision: int, decision: str, reason: str, evidence_digest: str, result: Any=None) -> dict[str, Any]:
        """Resolve an unknown effect only after verifying external evidence."""
        body=_resolve(step_id,expected_revision,decision,reason,evidence_digest,result)
        return self._client._request('POST',self._path+'/'+quote(_id(run_id),safe='').replace('.', '%2E')+'/resolve',json=body)

class AsyncAgentRuns:
    def __init__(self, client, sandbox_id):
        self._client=client
        self._path='/v1/sandboxes/'+quote(sandbox_id,safe='')+'/agent-runs'

    async def create(self, *, run_id: str, name: str, version: str, image_digest: str, input: Any) -> AgentRun:
        body=_create(run_id,name,version,image_digest,input)
        return AgentRun.from_dict(await self._client._request('POST',self._path,json=body))

    async def delete(self, run_id: str) -> AgentRun:
        return AgentRun.from_dict(await self._client._request('DELETE',self._path+'/'+quote(_id(run_id),safe='').replace('.', '%2E')))

    async def get(self, run_id: str) -> AgentRun:
        return AgentRun.from_dict(await self._client._request('GET',self._path+'/'+quote(_id(run_id),safe='').replace('.', '%2E')))

    async def steps(self, run_id: str, *, after: str='', limit: int=100) -> dict[str, Any]:
        if type(limit) is not int or not 1<=limit<=100: raise ValidationError('limit must be 1 through 100')
        if after: _id(after)
        return await self._client._request('GET',self._path+'/'+quote(_id(run_id),safe='').replace('.', '%2E')+'/steps',params={'after':after,'limit':limit})

    async def resolve(self, run_id: str, *, step_id: str, expected_revision: int, decision: str, reason: str, evidence_digest: str, result: Any=None) -> dict[str, Any]:
        body=_resolve(step_id,expected_revision,decision,reason,evidence_digest,result)
        return await self._client._request('POST',self._path+'/'+quote(_id(run_id),safe='').replace('.', '%2E')+'/resolve',json=body)

class AgentEvents:
    def __init__(self,client,sandbox_id):
        self._client=client
        self._path='/v1/sandboxes/'+quote(sandbox_id,safe='')+'/agent-events'

    def submit(self,*,source: str,event_id: str,run_id: str,name: str,version: str,image_digest: str,input: Any,command: list[str]) -> dict[str,Any]:
        """Commit one immutable event and driver command before acknowledging it."""
        from ._sandboxes import _exec_payload
        normalized=_exec_payload(command,cwd=None,env=None,timeout_seconds=None,stdin=False,tty=False,rows=None,cols=None)
        body=dict(source=_id(source),event_id=_id(event_id),run=_create(run_id,name,version,image_digest,input),command=normalized['command'])
        return self._client._request('POST',self._path,json=body)

    def get(self,event_id: str,*,source: str) -> dict[str,Any]:
        return self._client._request('GET',self._path+'/'+quote(_id(event_id),safe='').replace('.', '%2E'),params={'source':_id(source)})

class AsyncAgentEvents:
    def __init__(self,client,sandbox_id):
        self._client=client
        self._path='/v1/sandboxes/'+quote(sandbox_id,safe='')+'/agent-events'

    async def submit(self,*,source: str,event_id: str,run_id: str,name: str,version: str,image_digest: str,input: Any,command: list[str]) -> dict[str,Any]:
        from ._sandboxes import _exec_payload
        normalized=_exec_payload(command,cwd=None,env=None,timeout_seconds=None,stdin=False,tty=False,rows=None,cols=None)
        body=dict(source=_id(source),event_id=_id(event_id),run=_create(run_id,name,version,image_digest,input),command=normalized['command'])
        return await self._client._request('POST',self._path,json=body)

    async def get(self,event_id: str,*,source: str) -> dict[str,Any]:
        return await self._client._request('GET',self._path+'/'+quote(_id(event_id),safe='').replace('.', '%2E'),params={'source':_id(source)})
