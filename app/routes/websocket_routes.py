from app import apfell, db_objects
import aiopg
import json as js
import asyncio
from app.database_models.model import Operator, Callback, Task, Response, Payload, PayloadType, C2Profile, PayloadTypeC2Profile, Operation, Credential, Command, FileMeta, CommandParameters, CommandTransform
from sanic_jwt.decorators import protected, inject_user


# --------------- TASKS --------------------------
# notifications for new tasks
@apfell.websocket('/ws/tasks')
@protected()
async def ws_tasks(request, ws):
    try:
        async with aiopg.create_pool(apfell.config['DB_POOL_CONNECT_STRING']) as pool:
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute('LISTEN "newtask";')
                    # before we start getting new things, update with all of the old data
                    callbacks = Callback.select()
                    operators = Operator.select()
                    tasks = Task.select()
                    tasks_with_all_info = await db_objects.prefetch(tasks, callbacks, operators)
                    # callbacks_with_operators = await db_objects.prefetch(callbacks, operators)
                    for task in tasks_with_all_info:
                        await ws.send(js.dumps(task.to_json()))
                    await ws.send("")
                    # now pull off any new tasks we got queued up while processing the old data
                    while True:
                        try:
                            msg = conn.notifies.get_nowait()
                            id = (msg.payload)
                            tsk = await db_objects.get(Task, id=id)
                            await ws.send(js.dumps(tsk.to_json()))
                        except asyncio.QueueEmpty as e:
                            await asyncio.sleep(2)
                            await ws.send("")  # this is our test to see if the client is still there
                            continue
                        except Exception as e:
                            print(e)
                            return
    finally:
        # print("closed /ws/tasks")
        pool.close()


@apfell.websocket('/ws/tasks/current_operation')
@inject_user()
@protected()
async def ws_tasks_current_operation(request, ws, user):
    viewing_callbacks = set()  # this is a list of callback IDs that the operator is viewing, so only update those
    try:
        async with aiopg.create_pool(apfell.config['DB_POOL_CONNECT_STRING']) as pool:
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute('LISTEN "newtask";')
                    await cur.execute('LISTEN "updatedtask";')
                    if user['current_operation'] != "":
                        operation = await db_objects.get(Operation, name=user['current_operation'])
                        while True:
                            try:
                                msg = conn.notifies.get_nowait()
                                id = (msg.payload)
                                tsk = await db_objects.get(Task, id=id)
                                if tsk.callback.operation == operation and tsk.callback.id in viewing_callbacks:
                                    await ws.send(js.dumps(tsk.to_json()))
                            except asyncio.QueueEmpty as e:
                                await asyncio.sleep(0.5)
                                await ws.send("")  # this is our test to see if the client is still there
                            except Exception as e:
                                print(e)
                                return
                            try:
                                msg = await ws.recv()
                                if msg != "":
                                    if msg[0] == "a":
                                        viewing_callbacks.add(int(msg[1:]))
                                    elif msg[0] == "r":
                                        viewing_callbacks.remove(int(msg[1:]))
                            except Exception as e:
                                print(e)


    finally:
        # print("closed /ws/tasks")
        pool.close()


# --------------- RESPONSES ---------------------------
# notifications for task updates
@apfell.websocket('/ws/responses')
@protected()
async def ws_responses(request, ws):
    try:
        async with aiopg.create_pool(apfell.config['DB_POOL_CONNECT_STRING']) as pool:
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute('LISTEN "newresponse";')
                    responses = Response.select()
                    tasks = Task.select()
                    responses_with_tasks = await db_objects.prefetch(responses, tasks)
                    for resp in responses_with_tasks:
                        await ws.send(js.dumps(resp.to_json()))
                    await ws.send("")
                    # now pull off any new responses we got queued up while processing old responses
                    while True:
                        try:
                            msg = conn.notifies.get_nowait()
                            id = (msg.payload)
                            rsp = await db_objects.get(Response, id=id)
                            await ws.send(js.dumps(rsp.to_json()))
                            # print(msg.payload)
                        except asyncio.QueueEmpty as e:
                            await asyncio.sleep(2)
                            await ws.send("") # this is our test to see if the client is still there
                            continue
                        except Exception as e:
                            print(e)
                            return
    finally:
        # print("closed /ws/task_updates")
        pool.close()


# notifications for task updates
@apfell.websocket('/ws/responses/current_operation')
@inject_user()
@protected()
async def ws_responses_current_operation(request, ws, user):
    viewing_callbacks = set()  # this is a list of callback IDs that the operator is viewing, so only update those
    try:
        async with aiopg.create_pool(apfell.config['DB_POOL_CONNECT_STRING']) as pool:
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute('LISTEN "newresponse";')
                    if user['current_operation'] != "":
                        operation = await db_objects.get(Operation, name=user['current_operation'])
                        while True:
                            try:
                                msg = conn.notifies.get_nowait()
                                id = (msg.payload)
                                rsp = await db_objects.get(Response, id=id)
                                if rsp.task.callback.operation == operation and rsp.task.callback.id in viewing_callbacks:
                                    await ws.send(js.dumps(rsp.to_json()))
                                # print(msg.payload)
                            except asyncio.QueueEmpty as e:
                                await asyncio.sleep(0.5)
                                await ws.send("") # this is our test to see if the client is still there
                            except Exception as e:
                                print(e)
                                return
                            try:
                                msg = await ws.recv()
                                if msg != "":
                                    if msg[0] == "a":
                                        viewing_callbacks.add(int(msg[1:]))
                                    elif msg[0] == "r":
                                        viewing_callbacks.remove(int(msg[1:]))
                            except Exception as e:
                                print(e)
    finally:
        # print("closed /ws/task_updates")
        pool.close()


# --------------------- CALLBACKS ------------------
@apfell.websocket('/ws/callbacks/current_operation')
@inject_user()
@protected()
async def ws_callbacks_current_operation(request, ws, user):
    try:
        async with aiopg.create_pool(apfell.config['DB_POOL_CONNECT_STRING']) as pool:
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute('LISTEN "newcallback";')
                    if user['current_operation'] != "":
                        # before we start getting new things, update with all of the old data
                        operation = await db_objects.get(Operation, name=user['current_operation'])
                        callbacks = Callback.select().where(Callback.operation == operation).order_by(Callback.id)
                        operators = Operator.select()
                        callbacks_with_operators = await db_objects.prefetch(callbacks, operators)
                        for cb in callbacks_with_operators:
                            await ws.send(js.dumps(cb.to_json()))
                        await ws.send("")
                        # now pull off any new callbacks we got queued up while processing the old data
                        while True:
                            # msg = await conn.notifies.get()
                            try:
                                msg = conn.notifies.get_nowait()
                                id = (msg.payload)
                                cb = await db_objects.get(Callback, id=id, operation=operation)
                                await ws.send(js.dumps(cb.to_json()))
                            except asyncio.QueueEmpty as e:
                                await asyncio.sleep(0.5)
                                await ws.send("") # this is our test to see if the client is still there
                                continue
                            except Exception as e:
                                print(e)
                                return
    finally:
        pool.close()


# notifications for updated callbacks
@apfell.websocket('/ws/updatedcallbacks')
@inject_user()
@protected()
async def ws_updated_callbacks(request, ws, user):
    try:
        async with aiopg.create_pool(apfell.config['DB_POOL_CONNECT_STRING']) as pool:
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute('LISTEN "updatedcallback";')
                    # just want updates, not anything else
                    while True:
                        # msg = await conn.notifies.get()
                        try:
                            msg = conn.notifies.get_nowait()
                            # print("got an update for a callback")
                            id = (msg.payload)
                            cb = await db_objects.get(Callback, id=id)
                            await ws.send(js.dumps(cb.to_json()))
                        except asyncio.QueueEmpty as e:
                            await asyncio.sleep(2)
                            await ws.send("") # this is our test to see if the client is still there
                            continue
                        except Exception as e:
                            print(e)
                            return
    finally:
        pool.close()


# notifications for updated callbacks
@apfell.websocket('/ws/updatedcallbacks/current_operation')
@inject_user()
@protected()
async def ws_callbacks_updated_current_operation(request, ws, user):
    try:
        async with aiopg.create_pool(apfell.config['DB_POOL_CONNECT_STRING']) as pool:
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute('LISTEN "updatedcallback";')
                    if user['current_operation'] != "":
                        # just want updates, not anything else
                        operation = await db_objects.get(Operation, name=user['current_operation'])
                        while True:
                            # msg = await conn.notifies.get()
                            try:
                                msg = conn.notifies.get_nowait()
                                # print("got an update for a callback")
                                id = (msg.payload)
                                cb = await db_objects.get(Callback, id=id, operation=operation)
                                await ws.send(js.dumps(cb.to_json()))
                            except asyncio.QueueEmpty as e:
                                await asyncio.sleep(0.5)
                                await ws.send("") # this is our test to see if the client is still there
                                continue
                            except Exception as e:
                                print(e)
                                return
    finally:
        pool.close()


# --------------- PAYLOADS -----------------------
# notifications for new payloads
@apfell.websocket('/ws/payloads')
@protected()
async def ws_payloads(request, ws):
    try:
        async with aiopg.create_pool(apfell.config['DB_POOL_CONNECT_STRING']) as pool:
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute('LISTEN "newpayload";')
                    # BEFORE WE START GETTING NEW THINGS, UPDATE WITH ALL OF THE OLD DATA
                    payloads = await db_objects.execute(Payload.select().order_by(Payload.id))
                    for p in payloads:
                        await ws.send(js.dumps(p.to_json()))
                    await ws.send("")
                    # now pull off any new payloads we got queued up while processing old data
                    while True:
                        try:
                            msg = conn.notifies.get_nowait()
                            id = (msg.payload)
                            p = await db_objects.get(Payload, id=id)
                            await ws.send(js.dumps(p.to_json()))
                        except asyncio.QueueEmpty as e:
                            await asyncio.sleep(2)
                            await ws.send("")  # this is our test to see if the client is still there
                            continue
                        except Exception as e:
                            print(e)
                            return
    finally:
        pool.close()


# notifications for new payloads
@apfell.websocket('/ws/payloads/current_operation')
@inject_user()
@protected()
async def ws_payloads_current_operation(request, ws, user):
    try:
        async with aiopg.create_pool(apfell.config['DB_POOL_CONNECT_STRING']) as pool:
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute('LISTEN "newpayload";')
                    # BEFORE WE START GETTING NEW THINGS, UPDATE WITH ALL OF THE OLD DATA
                    if user['current_operation'] != "":
                        operation = await db_objects.get(Operation, name=user['current_operation'])
                        payloads = await db_objects.execute(Payload.select().where(Payload.operation == operation).order_by(Payload.id))
                        for p in payloads:
                            await ws.send(js.dumps(p.to_json()))
                        await ws.send("")
                        # now pull off any new payloads we got queued up while processing old data
                        while True:
                            try:
                                msg = conn.notifies.get_nowait()
                                id = (msg.payload)
                                p = await db_objects.get(Payload, id=id)
                                if p.operation == operation:
                                    await ws.send(js.dumps(p.to_json()))
                            except asyncio.QueueEmpty as e:
                                await asyncio.sleep(1)
                                await ws.send("")  # this is our test to see if the client is still there
                                continue
                            except Exception as e:
                                print(e)
                                return
    finally:
        pool.close()


# --------------- C2PROFILES -----------------------
# notifications for new c2profiles
@apfell.websocket('/ws/c2profiles')
@inject_user()
@protected()
async def ws_c2profiles(request, ws, user):
    try:
        async with aiopg.create_pool(apfell.config['DB_POOL_CONNECT_STRING']) as pool:
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute('LISTEN "newc2profile";')
                    # BEFORE WE START GETTING NEW THINGS, UPDATE WITH ALL OF THE OLD DATA
                    profiles = await db_objects.execute(C2Profile.select().order_by(C2Profile.id))
                    for p in profiles:
                        await ws.send(js.dumps(p.to_json()))
                    await ws.send("")
                    # now pull off any new payloads we got queued up while processing old data
                    while True:
                        try:
                            msg = conn.notifies.get_nowait()
                            id = (msg.payload)
                            p = await db_objects.get(C2Profile, id=id)
                            await ws.send(js.dumps(p.to_json()))
                        except asyncio.QueueEmpty as e:
                            await asyncio.sleep(2)
                            await ws.send("")  # this is our test to see if the client is still there
                            continue
                        except Exception as e:
                            print(e)
                            return
    finally:
        pool.close()


# notifications for new c2profiles
@apfell.websocket('/ws/c2profiles/current_operation')
@inject_user()
@protected()
async def ws_c2profile_current_operation(request, ws, user):
    try:
        async with aiopg.create_pool(apfell.config['DB_POOL_CONNECT_STRING']) as pool:
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute('LISTEN "newc2profile";')
                    # BEFORE WE START GETTING NEW THINGS, UPDATE WITH ALL OF THE OLD DATA
                    if user['current_operation'] != "":
                        operation = await db_objects.get(Operation, name=user['current_operation'])
                        profiles = await db_objects.execute(C2Profile.select().where(C2Profile.operation == operation).order_by(C2Profile.id))
                        for p in profiles:
                            await ws.send(js.dumps(p.to_json()))
                        await ws.send("")
                        # now pull off any new payloads we got queued up while processing old data
                        while True:
                            try:
                                msg = conn.notifies.get_nowait()
                                id = (msg.payload)
                                p = await db_objects.get(C2Profile, id=id)
                                if p.operation == operation:
                                    await ws.send(js.dumps(p.to_json()))
                            except asyncio.QueueEmpty as e:
                                await asyncio.sleep(1)
                                await ws.send("")  # this is our test to see if the client is still there
                                continue
                            except Exception as e:
                                print(e)
                                return
    finally:
        pool.close()


@apfell.websocket('/ws/payloadtypec2profile')
@protected()
async def ws_payloadtypec2profile(request, ws):
    try:
        async with aiopg.create_pool(apfell.config['DB_POOL_CONNECT_STRING']) as pool:
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute('LISTEN "newpayloadtypec2profile";')
                    # BEFORE WE START GETTING NEW THINGS, UPDATE WITH ALL OF THE OLD DATA
                    profiles = await db_objects.execute(PayloadTypeC2Profile.select())
                    for p in profiles:
                        await ws.send(js.dumps(p.to_json()))
                    await ws.send("")
                    # now pull off any new payloads we got queued up while processing old data
                    while True:
                        try:
                            msg = conn.notifies.get_nowait()
                            id = (msg.payload)
                            p = await db_objects.get(PayloadTypeC2Profile, id=id)
                            await ws.send(js.dumps(p.to_json()))
                        except asyncio.QueueEmpty as e:
                            await asyncio.sleep(2)
                            await ws.send("")  # this is our test to see if the client is still there
                            continue
                        except Exception as e:
                            print(e)
                            return
    finally:
        pool.close()


# ---------------- OPERATORS --------------------------
# notifications for new operators
@apfell.websocket('/ws/operators')
@protected()
async def ws_operators(request, ws):
    try:
        async with aiopg.create_pool(apfell.config['DB_POOL_CONNECT_STRING']) as pool:
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute('LISTEN "newoperator";')
                    # BEFORE WE START GETTING NEW THINGS, UPDATE WITH ALL OF THE OLD DATA
                    operators = await db_objects.execute(Operator.select())
                    for o in operators:
                        await ws.send(js.dumps(o.to_json()))
                    await ws.send("")
                    # now pull off any new payloads we got queued up while processing old data
                    while True:
                        try:
                            msg = conn.notifies.get_nowait()
                            id = (msg.payload)
                            p = await db_objects.get(Operator, id=id)
                            await ws.send(js.dumps(p.to_json()))
                        except asyncio.QueueEmpty as e:
                            await asyncio.sleep(2)
                            await ws.send("")  # this is our test to see if the client is still there
                            continue
                        except Exception as e:
                            print(e)
                            return
    finally:
        pool.close()


# notifications for updated operators
@apfell.websocket('/ws/updatedoperators')
@protected()
async def ws_updated_operators(request, ws):
    try:
        async with aiopg.create_pool(apfell.config['DB_POOL_CONNECT_STRING']) as pool:
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute('LISTEN "updatedoperator";')
                    # just want updates, not anything else
                    while True:
                        # msg = await conn.notifies.get()
                        try:
                            msg = conn.notifies.get_nowait()
                            # print("got an update for a callback")
                            id = (msg.payload)
                            cb = await db_objects.get(Operator, id=id)
                            await ws.send(js.dumps(cb.to_json()))
                        except asyncio.QueueEmpty as e:
                            await asyncio.sleep(2)
                            await ws.send("") # this is our test to see if the client is still there
                            continue
                        except Exception as e:
                            print(e)
                            return
    finally:
        pool.close()


# ---------------- PAYLOADTYPES --------------------------
# notifications for new payloadtypes
@apfell.websocket('/ws/payloadtypes')
@protected()
async def ws_payloadtypes(request, ws):
    try:
        async with aiopg.create_pool(apfell.config['DB_POOL_CONNECT_STRING']) as pool:
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute('LISTEN "newpayloadtype";')
                    # BEFORE WE START GETTING NEW THINGS, UPDATE WITH ALL OF THE OLD DATA
                    payloadtypes = await db_objects.execute(PayloadType.select().order_by(PayloadType.id))
                    for p in payloadtypes:
                        await ws.send(js.dumps(p.to_json()))
                    await ws.send("")
                    # now pull off any new payloads we got queued up while processing old data
                    while True:
                        try:
                            msg = conn.notifies.get_nowait()
                            id = (msg.payload)
                            p = await db_objects.get(PayloadType, id=id)
                            await ws.send(js.dumps(p.to_json()))
                        except asyncio.QueueEmpty as e:
                            await asyncio.sleep(2)
                            await ws.send("")  # this is our test to see if the client is still there
                            continue
                        except Exception as e:
                            print(e)
                            return
    finally:
        pool.close()


# ---------------- COMMANDS --------------------------
# notifications for new commands
@apfell.websocket('/ws/commands')
@protected()
async def ws_commands(request, ws):
    try:
        async with aiopg.create_pool(apfell.config['DB_POOL_CONNECT_STRING']) as pool:
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute('LISTEN "newcommand";')
                    # BEFORE WE START GETTING NEW THINGS, UPDATE WITH ALL OF THE OLD DATA
                    commands = await db_objects.execute(Command.select())
                    for c in commands:
                        await ws.send(js.dumps(c.to_json()))
                    await ws.send("")
                    # now pull off any new payloads we got queued up while processing old data
                    while True:
                        try:
                            msg = conn.notifies.get_nowait()
                            id = (msg.payload)
                            p = await db_objects.get(Command, id=id)
                            await ws.send(js.dumps(p.to_json()))
                        except asyncio.QueueEmpty as e:
                            await asyncio.sleep(2)
                            await ws.send("")  # this is our test to see if the client is still there
                            continue
                        except Exception as e:
                            print(e)
                            return
    finally:
        pool.close()


# notifications for new commands
@apfell.websocket('/ws/all_command_info')
@protected()
async def ws_commands(request, ws):
    try:
        async with aiopg.create_pool(apfell.config['DB_POOL_CONNECT_STRING']) as pool:
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute('LISTEN "newcommandparameters";')
                    await cur.execute('LISTEN "updatedcommandparameters";')
                    await cur.execute('LISTEN "deletedcommandparameters";')
                    await cur.execute('LISTEN "newcommandtransform";')
                    await cur.execute('LISTEN "updatedcommandtransform";')
                    await cur.execute('LISTEN "deletedcommandtransform";')
                    await cur.execute('LISTEN "newcommand";')
                    await cur.execute('LISTEN "updatedcommand";')
                    await cur.execute('LISTEN "deletedcommand";')
                    # now pull off any new payloads we got queued up while processing old data
                    while True:
                        try:
                            msg = conn.notifies.get_nowait()
                            id = msg.payload
                            msg_dict = {}
                            if "parameters" in msg.channel and "deleted" not in msg.channel:
                                p = await db_objects.get(CommandParameters, id=id)
                            elif "transform" in msg.channel and "deleted" not in msg.channel:
                                p = await db_objects.get(CommandTransform, id=id)
                            elif "deleted" not in msg.channel:
                                p = await db_objects.get(Command, id=id)
                            if msg.channel == "deletedcommand":
                                # this is a special case
                                await ws.send(js.dumps({**js.loads(id), "notify": msg.channel}))
                                continue
                            elif "deleted" in msg.channel:
                                # print(msg)
                                p = await db_objects.get(Command, id=js.loads(id)['command_id'])
                                msg_dict = {**js.loads(id)}
                            await ws.send(js.dumps({**p.to_json(), **msg_dict, "notify": msg.channel}))
                        except asyncio.QueueEmpty as e:
                            await asyncio.sleep(1)
                            await ws.send("")  # this is our test to see if the client is still there
                            continue
                        except Exception as e:
                            print(e)
                            return
    finally:
        pool.close()


# ------------- FILEMETA ---------------------------
# notifications for new screenshots
@apfell.websocket('/ws/screenshots')
@inject_user()
@protected()
async def ws_screenshots(request, ws, user):
    try:
        async with aiopg.create_pool(apfell.config['DB_POOL_CONNECT_STRING']) as pool:
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute('LISTEN "newfilemeta";')
                    # BEFORE WE START GETTING NEW THINGS, UPDATE WITH ALL OF THE OLD DATA
                    operation = await db_objects.get(Operation, name=user['current_operation'])
                    files = await db_objects.execute(FileMeta.select().where(FileMeta.operation == operation).order_by(FileMeta.id))
                    for f in files:
                        if "{}/downloads/".format(user['current_operation']) in f.path and "/screenshots/" in f.path:
                            if f.task:
                                await ws.send(js.dumps({**f.to_json(), 'callback_id': f.task.callback.id, 'operator': f.task.operator.username}))
                            else:
                                await ws.send(js.dumps({**f.to_json(), 'callback_id': 0,
                                                        'operator': "null"}))
                    await ws.send("")
                    # now pull off any new payloads we got queued up while processing old data
                    while True:
                        try:
                            msg = conn.notifies.get_nowait()
                            id = (msg.payload)
                            f = await db_objects.get(FileMeta, id=id)
                            if "{}/downloads/".format(user['current_operation']) in f.path and "/screenshots" in f.path:
                                if f.task:
                                    callback_id = f.task.callback.id
                                    await ws.send(js.dumps({**f.to_json(), 'callback_id': callback_id, 'operator': f.task.operator.username}))
                                else:
                                    await ws.send(js.dumps({**f.to_json(), 'callback_id': 0,
                                                            'operator': "null"}))
                        except asyncio.QueueEmpty as e:
                            await asyncio.sleep(2)
                            await ws.send("")  # this is our test to see if the client is still there
                            continue
                        except Exception as e:
                            print(e)
                            return
    finally:
        pool.close()


# notifications for updated screenshots
@apfell.websocket('/ws/updated_screenshots')
@inject_user()
@protected()
async def ws_updated_screenshots(request, ws, user):
    try:
        async with aiopg.create_pool(apfell.config['DB_POOL_CONNECT_STRING']) as pool:
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute('LISTEN "updatedfilemeta";')
                    # BEFORE WE START GETTING NEW THINGS, UPDATE WITH ALL OF THE OLD DATA
                    operation = await db_objects.get(Operation, name=user['current_operation'])
                    while True:
                        try:
                            msg = conn.notifies.get_nowait()
                            id = (msg.payload)
                            f = await db_objects.get(FileMeta, id=id)
                            if "{}/downloads/".format(user['current_operation']) in f.path and "/screenshots" in f.path:
                                if f.task:
                                    callback_id = f.task.callback.id
                                    await ws.send(js.dumps({**f.to_json(), 'callback_id': callback_id, 'operator': f.task.operator.username}))
                                else:
                                    await ws.send(js.dumps({**f.to_json(), 'callback_id': 0,
                                                            'operator': "null"}))
                        except asyncio.QueueEmpty as e:
                            await asyncio.sleep(2)
                            await ws.send("")  # this is our test to see if the client is still there
                            continue
                        except Exception as e:
                            print(e)
                            return
    finally:
        pool.close()


# notifications for new files in the current operation
@apfell.websocket('/ws/files/current_operation')
@inject_user()
@protected()
async def ws_files_current_operation(request, ws, user):
    try:
        async with aiopg.create_pool(apfell.config['DB_POOL_CONNECT_STRING']) as pool:
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute('LISTEN "newfilemeta";')
                    # BEFORE WE START GETTING NEW THINGS, UPDATE WITH ALL OF THE OLD DATA
                    operation = await db_objects.get(Operation, name=user['current_operation'])
                    files = await db_objects.execute(FileMeta.select().where(
                        (FileMeta.operation == operation) & (FileMeta.deleted == False)).order_by(FileMeta.id))
                    for f in files:
                        if "/screenshots/" not in f.path:
                            if "/{}/downloads/".format(user['current_operation']) not in f.path:
                                # this means it's an upload, so supply additional information as well
                                # two kinds of uploads: via task or manual
                                if f.task is not None:  # this is an upload via agent tasking
                                    await ws.send(js.dumps(
                                        {**f.to_json(), 'host': f.task.callback.host, "upload": f.task.params}))
                                else:  # this is a manual upload
                                    await ws.send(js.dumps({**f.to_json(), 'host': 'MANUAL FILE UPLOAD',
                                                            "upload": "{\"remote_path\": \"Apfell\", \"file_id\": " + str(f.id) + "}", "task": "null"}))
                            else:
                                await ws.send(js.dumps({**f.to_json(), 'host': f.task.callback.host, 'params': f.task.params}))
                    await ws.send("")
                    # now pull off any new payloads we got queued up while processing old data
                    while True:
                        try:
                            msg = conn.notifies.get_nowait()
                            id = (msg.payload)
                            f = await db_objects.get(FileMeta, id=id, operation=operation, deleted=False)
                            if "/screenshots" not in f.path:
                                try:
                                    if "/{}/downloads/".format(user['current_operation']) not in f.path:
                                        # this means it's an upload, so supply additional information as well
                                        # could be upload via task or manual
                                        if f.task is not None:  # this is an upload via gent tasking
                                            await ws.send(js.dumps(
                                                {**f.to_json(), 'host': f.task.callback.host, "upload": f.task.params}))
                                        else: # this is a manual upload
                                            await ws.send(js.dumps({**f.to_json(), 'host': 'MANUAL FILE UPLOAD',
                                                                    "upload": "{\"remote_path\": \"Apfell\", \"file_id\": " + str(f.id) + "}", "task": "null"}))
                                    else:
                                        await ws.send(js.dumps({**f.to_json(), 'host': f.task.callback.host,
                                                                'params': f.task.params}))
                                except Exception as e:
                                    pass  # we got a file that's just not part of our current operation, so move on
                        except asyncio.QueueEmpty as e:
                            await asyncio.sleep(1)
                            await ws.send("")  # this is our test to see if the client is still there
                            continue
                        except Exception as e:
                            print(e)
                            continue
    finally:
        pool.close()


# notifications for new files in the current operation
@apfell.websocket('/ws/updated_files/current_operation')
@inject_user()
@protected()
async def ws_updated_files(request, ws, user):
    try:
        async with aiopg.create_pool(apfell.config['DB_POOL_CONNECT_STRING']) as pool:
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute('LISTEN "updatedfilemeta";')
                    # BEFORE WE START GETTING NEW THINGS, UPDATE WITH ALL OF THE OLD DATA
                    operation = await db_objects.get(Operation, name=user['current_operation'])
                    # now pull off any new payloads we got queued up while processing old data
                    while True:
                        try:
                            msg = conn.notifies.get_nowait()
                            id = (msg.payload)
                            f = await db_objects.get(FileMeta, id=id, operation=operation, deleted=False)
                            if "/screenshots" not in f.path:
                                try:
                                    if "/{}/downloads/".format(user['current_operation']) not in f.path:
                                        # this means it's an upload, so supply additional information as well
                                        if f.task is not None:  # this is an upload agent tasking
                                            await ws.send(js.dumps(
                                                {**f.to_json(), 'host': f.task.callback.host, "upload": f.task.params}))
                                        else:
                                            await ws.send(js.dumps({**f.to_json(), 'host': 'MANUAL FILE UPLOAD',
                                                                    "upload": "{\"remote_path\": \"Apfell\", \"file_id\": " + str(f.id) + "}", "task": "null"}))
                                    else:
                                        await ws.send(js.dumps({**f.to_json(), 'host': f.task.callback.host, 'params': f.task.params}))
                                except Exception as e:
                                    pass  # got an update for a file not in this operation
                        except asyncio.QueueEmpty as e:
                            await asyncio.sleep(1)
                            await ws.send("")  # this is our test to see if the client is still there
                            continue
                        except Exception as e:
                            print(e)
                            continue
    finally:
        pool.close()


# ------------- CREDENTIAL ---------------------------
# notifications for new credentials
@apfell.websocket('/ws/credentials/current_operation')
@inject_user()
@protected()
async def ws_credentials_current_operation(request, ws, user):
    try:
        async with aiopg.create_pool(apfell.config['DB_POOL_CONNECT_STRING']) as pool:
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute('LISTEN "newcredential";')
                    # BEFORE WE START GETTING NEW THINGS, UPDATE WITH ALL OF THE OLD DATA
                    operation = await db_objects.get(Operation, name=user['current_operation'])
                    creds = await db_objects.execute(Credential.select().where(Credential.operation == operation))
                    for c in creds:
                        await ws.send(js.dumps({**c.to_json()}))
                    await ws.send("")
                    # now pull off any new payloads we got queued up while processing old data
                    while True:
                        try:
                            msg = conn.notifies.get_nowait()
                            id = (msg.payload)
                            try:
                                c = await db_objects.get(Credential, id=id, operation=operation)
                                await ws.send(js.dumps({**c.to_json()}))
                            except Exception as e:
                                pass  # we got a file that's just not part of our current operation, so move on
                        except asyncio.QueueEmpty as e:
                            await asyncio.sleep(2)
                            await ws.send("")  # this is our test to see if the client is still there
                            continue
                        except Exception as e:
                            print(e)
                            continue
    finally:
        pool.close()