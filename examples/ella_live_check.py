"""Isolated real-server ELLA V1 acceptance check.

Uses a temporary store and localhost uvicorn. It validates REST/MCP parity,
threshold confirmation, idempotency, restart persistence, promotion separation,
truth/usefulness firewalls, conflict overlay, and live SSE correction delivery.
No deployment or persistent user data is touched.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time

import httpx


def free_port() -> int:
    sock=socket.socket()
    sock.bind(("127.0.0.1",0))
    port=sock.getsockname()[1]
    sock.close()
    return port


def main() -> None:
    proof={}
    with tempfile.TemporaryDirectory(prefix="ember-ella-live-") as tmp:
        root=Path(tmp)/"store"
        log_path=Path(tmp)/"server.log"
        port=free_port()
        url=f"http://127.0.0.1:{port}"
        process=None

        def start():
            nonlocal process
            log=open(log_path,"a",encoding="utf-8")
            process=subprocess.Popen(
                [sys.executable,"-m","uvicorn","embers.api:app","--host","127.0.0.1",
                 "--port",str(port),"--timeout-graceful-shutdown","1"],
                env={**os.environ,"EMBER_STORE":str(root)},
                stdout=log,stderr=subprocess.STDOUT,
                cwd=Path(__file__).resolve().parents[1],
            )
            for _ in range(150):
                try:
                    if httpx.get(url+"/health",timeout=1,trust_env=False).status_code==200:
                        return log
                except httpx.HTTPError:
                    pass
                time.sleep(.1)
            log.flush()
            raise RuntimeError("server startup failed: "+log_path.read_text(encoding="utf-8")[-5000:])

        def stop(log):
            nonlocal process
            if process is not None and process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
            log.close()

        log=start()
        client=httpx.Client(base_url=url,timeout=10,trust_env=False)
        try:
            auth=json.loads((Path(tmp)/"store-candidate-credentials.json").read_text(encoding="utf-8"))
            headers={"X-Ember-Agent-Id":auth["agent_id"],"X-Ember-Token":auth["token"]}

            def post(path,body,headers_override=None,expected=200):
                r=client.post(path,json=body,headers=headers_override or headers)
                assert r.status_code==expected,(path,r.status_code,r.text)
                return r.json() if r.content else None

            def get(path,params=None,headers_override=None,expected=200):
                r=client.get(path,params=params,headers=headers_override or headers)
                assert r.status_code==expected,(path,r.status_code,r.text)
                return r.json() if r.content else None

            namespace="ella-live"
            rid=post("/v1/memory/write",{"namespace":namespace,"content":{"content":"Fictional ELLA acceptance claim"}})["id"]
            initial=get(f"/v1/epistemic/state/{namespace}",{"memory_id":rid})
            version=initial["target_memory_version"]
            assert initial["base_epistemic_verdict"]=="PROVISIONAL"
            assert initial["score"]==0
            proof["initial_provisional"]=True

            # Snapshot FUR-owned learning state before epistemic writes.
            fur_before=get(f"/v1/visualizer/{namespace}")
            fur_keys=("source_journal_revision","states","experiences","reports")
            fur_before={k:fur_before[k] for k in fur_keys}

            e1=post(f"/v1/memory/{rid}/evidence",{"source":"fixture-one","reference":"ella-live-one",
                "origin":"artifact:ella-live-one","origin_confidence":"AGENT_DECLARED"})["evidence_id"]
            e2=post(f"/v1/memory/{rid}/evidence",{"source":"fixture-two","reference":"ella-live-two",
                "origin":"artifact:ella-live-two","origin_confidence":"AGENT_DECLARED"})["evidence_id"]

            def epistemic(action,payload,request_id,revision,headers_override=None,expected=200):
                return post(f"/v1/epistemic/feedback/{namespace}",
                    {"action":action,"payload":payload,"request_id":request_id,"expected_revision":revision},
                    headers_override,expected)

            base={"target_memory_id":rid,"target_memory_version":version}
            one=epistemic("report",{**base,"evidence_id":e1,"polarity":"SUPPORTS","strength":"STRONG",
                "assessment_note":"first independent observable unit"},"report-one",0)
            assert one["base_epistemic_verdict"]=="PROVISIONAL" and not one["confirmation_required"]
            first_assessment=one["units"][0]["assessment_ids"][0]

            two_payload={**base,"evidence_id":e2,"polarity":"SUPPORTS","strength":"STRONG",
                "assessment_note":"second independent observable unit"}
            two=epistemic("report",two_payload,"report-two",1)
            assert two["base_epistemic_verdict"]=="PROVISIONAL" and two["confirmation_required"]
            pending=two["confirmation_assessment_ids"][0]

            # Exact retry is idempotent.
            retry=epistemic("report",two_payload,"report-two",1)
            assert retry==two
            proof["idempotent_retry"]=True

            self_confirm={**base,"evidence_id":e2,"polarity":"SUPPORTS","strength":"STRONG",
                "assessment_note":"self confirmation must fail","confirmation_of":pending}
            epistemic("confirm",self_confirm,"self-confirm",2,expected=403)

            second=post("/v1/agents/register",{"name":"ella-live-confirming-assessor"})
            second_headers={"X-Ember-Agent-Id":second["agent_id"],"X-Ember-Token":second["token"]}
            confirmed=epistemic("confirm",{**base,"evidence_id":e2,"polarity":"SUPPORTS","strength":"STRONG",
                "assessment_note":"distinct threshold confirmation","confirmation_of":pending},
                "confirm-two",2,second_headers)
            assert confirmed["base_epistemic_verdict"]=="VERIFIED"
            verified_score=confirmed["score"]
            proof["threshold_confirmation_verified"]=True

            # Full ELLA metadata never alters FUR state.
            fur_after=get(f"/v1/visualizer/{namespace}")
            fur_after={k:fur_after[k] for k in fur_keys}
            assert fur_after==fur_before,(fur_before,fur_after)
            proof["usefulness_firewall"]=True

            # MCP state must agree with REST canonical state.
            rpc={"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"ember_epistemic_state",
                "arguments":{"namespace":namespace,"memory_id":rid,"agent_id":auth["agent_id"],"token":auth["token"]}}}
            mcp=client.post("/mcp",json=rpc,headers={"Content-Type":"application/json"})
            assert mcp.status_code==200,mcp.text
            mcp_result=mcp.json()["result"]
            assert not mcp_result["isError"],mcp_result
            mcp_state=json.loads(mcp_result["content"][0]["text"])
            rest_state=get(f"/v1/epistemic/state/{namespace}",{"memory_id":rid})
            for key in ("base_epistemic_verdict","public_epistemic_state","score","support_mass",
                        "opposition_mass","accepted_unit_count","epistemic_revision"):
                assert mcp_state[key]==rest_state[key],key
            proof["rest_mcp_parity"]=True

            # Conflict overlay may change the public projection, never ELLA score.
            other=post("/v1/memory/write",{"namespace":namespace,"content":{"content":"Fictional contradictory claim"}})["id"]
            post("/v1/conflicts/map",{"memory_a":rid,"memory_b":other,"note":"ELLA live overlay check"})
            disputed=get(f"/v1/epistemic/state/{namespace}",{"memory_id":rid})
            assert disputed["score"]==verified_score
            assert disputed["base_epistemic_verdict"]=="VERIFIED"
            assert disputed["public_epistemic_state"]=="DISPUTED"
            proof["conflict_overlay_score_firewall"]=True

            # Proposal confidence is admission confidence only.
            proposal=post("/v1/memory/propose",{"namespace":namespace,"claim":{"content":"High-confidence admitted claim"},
                "reason":"ELLA promotion separation check","confidence":0.99})["proposal_id"]
            promoted=post("/v1/memory/promote",{"proposal_id":proposal})
            assert promoted["status"]=="provisional",promoted
            proof["promotion_not_verification"]=True

            # Open a real HTTP SSE stream, then revise an accepted assessment.
            state=get(f"/v1/epistemic/state/{namespace}",{"memory_id":rid})
            accepted_ids=[a for unit in state["units"] if unit.get("status")=="accepted" for a in unit.get("assessment_ids",[])]
            assert first_assessment in accepted_ids
            revise_id=first_assessment
            sse_result={}
            ready=threading.Event()

            def listen():
                try:
                    with httpx.Client(base_url=url,timeout=httpx.Timeout(10,read=10),trust_env=False) as sse_client:
                        with sse_client.stream("GET",f"/v1/visualizer-stream/{namespace}",headers=headers) as response:
                            response.raise_for_status()
                            event=None
                            data=[]
                            for line in response.iter_lines():
                                if line.startswith("event: "):
                                    event=line[7:]
                                elif line.startswith("data: "):
                                    data.append(line[6:])
                                elif line=="" and event:
                                    payload=json.loads("\n".join(data)) if data else {}
                                    if event=="ready":
                                        ready.set()
                                    if event=="patch":
                                        for item in payload.get("events",[]):
                                            if item.get("event_type")=="epistemic_revise":
                                                sse_result["event"]=item
                                                return
                                    event=None;data=[]
                except Exception as exc:
                    sse_result["error"]=repr(exc)
                    ready.set()

            thread=threading.Thread(target=listen,daemon=True)
            thread.start()
            assert ready.wait(5),sse_result
            assert "error" not in sse_result,sse_result

            current=get(f"/v1/epistemic/state/{namespace}",{"memory_id":rid})
            revised=epistemic("revise",{**base,"evidence_id":e1,"assessment_id":revise_id,
                "polarity":"SUPPORTS","strength":"MEDIUM","assessment_note":"corrected live assessment"},
                "revise-live",current["epistemic_revision"])
            thread.join(5)
            assert "error" not in sse_result,sse_result
            assert "event" in sse_result,sse_result
            assert sse_result["event"]["epistemic"]["score"]==revised["score"]
            proof["live_sse_correction"]=True

            # Observer/read surfaces must not create ELLA revisions.
            before_revision=get(f"/v1/epistemic/state/{namespace}",{"memory_id":rid})["epistemic_revision"]
            get(f"/v1/visualizer/{namespace}")
            get(f"/v1/epistemic/state/{namespace}",{"memory_id":rid})
            after_revision=get(f"/v1/epistemic/state/{namespace}",{"memory_id":rid})["epistemic_revision"]
            assert after_revision==before_revision
            proof["read_only_observation"]=True

            # Restart same store and prove journal replay/persistence.
            client.close()
            stop(log)
            log=start()
            client=httpx.Client(base_url=url,timeout=10,trust_env=False)
            persisted=get(f"/v1/epistemic/state/{namespace}",{"memory_id":rid})
            assert persisted["epistemic_revision"]==before_revision
            assert persisted["score"]==revised["score"]
            proof["restart_persistence"]=True

            proof["result"]="PASS"
            print(json.dumps(proof,sort_keys=True))
        finally:
            try:
                client.close()
            except Exception:
                pass
            if process is not None and process.poll() is None:
                stop(log)


if __name__=="__main__":
    main()
