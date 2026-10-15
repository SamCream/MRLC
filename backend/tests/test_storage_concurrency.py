from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from app.storage import Store


def test_shared_store_materializes_reads_before_releasing_connection_lock(tmp_path):
    store=Store(str(tmp_path/"concurrency.sqlite3"))
    try:
        with store.tx():
            store.put("user",{
                "id":"owner","username":"owner","displayName":"Owner",
                "role":"administrator","disabled":False,"allProjects":True,
                "extraPermissions":[],"deniedPermissions":[],
            })
        errors=[]

        def reader(index:int):
            try:
                for _ in range(250):
                    user=store.obj("user","owner")
                    assert user is not None
                    assert user["username"]=="owner"
                    rows=store.execute(
                        "SELECT body FROM objects WHERE kind='user' AND id='owner'"
                    ).fetchall()
                    assert len(rows)==1
                    assert rows[0]["body"] is not None
                    list(store.execute("SELECT id,type,payload FROM ui_events ORDER BY seq DESC LIMIT 20"))
            except Exception as exc:
                errors.append((index,exc))

        def writer(index:int):
            try:
                for n in range(150):
                    with store.tx():
                        store.audit("stress",f"writer {index} #{n}")
                        store.emit("stress.changed",None,{"writer":index,"n":n})
            except Exception as exc:
                errors.append((index,exc))

        with ThreadPoolExecutor(max_workers=12) as pool:
            futures=[pool.submit(reader,i) for i in range(8)]
            futures.extend(pool.submit(writer,100+i) for i in range(4))
            for future in futures:
                future.result()

        assert not errors,errors
        assert store.obj("user","owner")["username"]=="owner"
    finally:
        store.close()
