from app import apfell, db_objects
from sanic.response import json, raw
from app.database_models.model import Callback, Operator, Task, Command, FileMeta, Operation, Response, LoadedCommands, ATTACKCommand, ATTACKTask, TaskArtifact, ArtifactTemplate, OperatorOperation, Payload
import datetime
from sanic_jwt.decorators import protected, inject_user
from app.api.transform_api import get_transforms_func, get_commandtransforms_func
import json as js
import importlib, sys
from app.api.transforms.utils import TransformOperation, CommandTransformOperation
import shutil, os, glob
from app.api.payloads_api import generate_uuid, write_c2
import app.crypto as crypt
import base64


# This gets all tasks in the database
@apfell.route(apfell.config['API_BASE'] + "/tasks/", methods=['GET'])
@inject_user()
@protected()
async def get_all_tasks(request, user):
    callbacks = Callback.select()
    operators = Operator.select()
    tasks = Task.select()
    full_task_data = await db_objects.prefetch(tasks, callbacks, operators)
    if user['admin']:
        # callbacks_with_operators = await db_objects.prefetch(callbacks, operators)
        return json([c.to_json() for c in full_task_data])
    elif user['current_operation'] != "":
        operation = await db_objects.get(Operation, name=user['current_operation'])
        return json([c.to_json() for c in full_task_data if c.callback.operation == operation])
    else:
        return json({'status': 'error', 'error': 'must be admin to see all tasks or part of a current operation'})


# Get a single response
@apfell.route(apfell.config['API_BASE'] + "/tasks/search", methods=['POST'])
@inject_user()
@protected()
async def search_tasks(request, user):
    try:
        data = request.json
        if 'search' not in data:
            return json({'status': 'error', 'error': 'failed to find search term in request'})
        operation = await db_objects.get(Operation, name=user['current_operation'])
    except Exception as e:
        return json({'status': 'error', 'error': 'Cannot get that response'})
    tasks = await db_objects.execute(Task.select().where((Task.params.contains(data['search'])) | (Task.original_params.contains(data['search']))).join(Callback).where(Callback.operation == operation).order_by(Task.id))
    output = []
    for t in tasks:
        responses = await db_objects.execute(Response.select().where(Response.task == t))
        output.append({**t.to_json(), "responses": [r.to_json() for r in responses]})
    return json({'status': 'success', 'output': output})


@apfell.route(apfell.config['API_BASE'] + "/tasks/callback/<cid:int>", methods=['GET'])
@inject_user()
@protected()
async def get_all_tasks_for_callback(request, cid, user):
    try:
        callback = await db_objects.get(Callback, id=cid)
        operation = await db_objects.get(Operation, id=callback.operation)
    except Exception as e:
        return json({'status': 'error',
                     'error': 'Callback does not exist'})
    if operation.name in user['operations']:
        try:
            cb_task_data = await db_objects.execute(Task.select().where(Task.callback == callback).order_by(Task.id))
            return json([c.to_json() for c in cb_task_data])
        except Exception as e:
            return json({'status': 'error',
                         'error': 'No Tasks',
                         'msg': str(e)})
    else:
        return json({'status': 'error', 'error': 'You must be part of the right operation to see this information'})


@apfell.route(apfell.config['API_BASE'] + "/task_report_by_callback")
@inject_user()
@protected()
async def get_all_tasks_by_callback_in_current_operation(request, user):
    try:
        operation = await db_objects.get(Operation, name=user['current_operation'])
    except Exception as e:
        return json({'status': 'error', 'error': 'Not part of an operation'})
    output = []
    callbacks = await db_objects.execute(Callback.select().where(Callback.operation == operation).order_by(Callback.id))
    for callback in callbacks:
        c = callback.to_json()  # hold this callback, task, and response info to push to our output stack
        c['tasks'] = []
        tasks = await db_objects.execute(Task.select().where(Task.callback == callback).order_by(Task.id))
        for t in tasks:
            t_data = t.to_json()
            t_data['responses'] = []
            responses = await db_objects.execute(Response.select().where(Response.task == t).order_by(Response.id))
            for r in responses:
                t_data['responses'].append(r.to_json())
            c['tasks'].append(t_data)
        output.append(c)
    return json({'status': 'success', 'output': output})


# We don't put @protected or @inject_user here since the callback needs to be able to call this function
@apfell.route(apfell.config['API_BASE'] + "/tasks/callback/<cid:int>/nextTask", methods=['GET'])
async def get_next_task(request, cid):
    # gets the next task by time for the callback to do
    try:
        callback = await db_objects.get(Callback, id=cid)
    except Exception as e:
        print("Callback did not exist, returning blank message")
        return json({}, status=404)
    try:
        callback.last_checkin = datetime.datetime.utcnow()
        callback.active = True  # always set this to true regardless of what it was before because it's clearly active
        await db_objects.update(callback)  # update the last checkin time
        operation = await db_objects.get(Operation, name=callback.operation.name)
        if not operation.complete:
            tasks = await db_objects.get(Task.select().join(Callback).where(
                (Task.callback == callback) & (Task.status == "submitted")).order_by(Task.timestamp).limit(1))
        else:
            # operation is complete, just return blank
            return json({}, status=404)
    except Exception as e:
        print(e)
        return json({'command': 'none'})  # return empty if there are no tasks that meet the criteria
    tasks.status = "processing"
    await db_objects.update(tasks)
    if callback.encryption_type != "" and callback.encryption_type is not None:
        # encrypt the message before returning it
        string_message = js.dumps({"command": tasks.command.cmd, "params": tasks.params, "id": tasks.id})
        if callback.encryption_type == "AES256":
            raw_encrypted = await crypt.encrypt_AES256(data=string_message.encode(),
                                                       key=base64.b64decode(callback.encryption_key))
            return raw(base64.b64encode(raw_encrypted), status=200)
    else:
        return json({"command": tasks.command.cmd, "params": tasks.params, "id": tasks.id})


# create a new task to a specific callback
@apfell.route(apfell.config['API_BASE'] + "/tasks/callback/<cid:int>", methods=['POST'])
@inject_user()
@protected()
async def add_task_to_callback(request, cid, user):
    # some commands can optionally upload files or indicate files for use
    # if they are uploaded here, process them first and substitute the values with corresponding file_id numbers
    if user['current_operation'] == "":
        return json({'status': 'error', 'error': 'Must be part of a current operation first'})
    try:
        operator = await db_objects.get(Operator, username=user['username'])
    except Exception as e:
        return json({'status': 'error', 'error': "failed to get the current user's info from the database"})
    try:
        operation = await db_objects.get(Operation, name=user['current_operation'])
    except Exception as e:
        return json({'status': 'error', 'error': "failed to get the current operation"})
    if request.form:
        data = js.loads(request.form.get('json'))
    else:
        data = request.json
    file_updates_with_task = []  # if we create new files throughout this process, be sure to tag them with the right task at the end
    if request.files:
        # this means we got files as part of our task, so handle those first
        params = js.loads(data['params'])
        for k in params:
            if params[k] == 'FILEUPLOAD':
                # this means we need to handle a file upload scenario and replace this value with a file_id
                code = request.files['file' + k][0].body
                path = "./app/files/{}/{}".format(user['current_operation'], request.files['file' + k][0].name)
                os.makedirs("./app/files/{}".format(user['current_operation']), exist_ok=True)
                code_file = open(path, "wb")
                code_file.write(code)
                code_file.close()
                new_file_meta = await db_objects.create(FileMeta, total_chunks=1, chunks_received=1, complete=True,
                                                  path=path, operation=operation, operator=operator)
                params[k] = new_file_meta.id
                file_updates_with_task.append(new_file_meta)
        data['params'] = js.dumps(params)
    data['operator'] = user['username']
    data['file_updates_with_task'] = file_updates_with_task
    return json(await add_task_to_callback_func(data, cid, user))


async def add_task_to_callback_func(data, cid, user):
    try:
        # first see if the operator and callback exists
        op = await db_objects.get(Operator, username=user['username'])
        cb = await db_objects.get(Callback, id=cid)
        operation = await db_objects.get(Operation, name=user['current_operation'])
        original_params = None
        task = None
        # now check the task and add it if it's valid and valid for this callback's payload type
        try:
            cmd = await db_objects.get(Command, cmd=data['command'], payload_type=cb.registered_payload.payload_type)
        except Exception as e:
            # it's not registered, so check the default tasks/clear
            if data['command'] == "tasks":
                # this means we're just listing out the not-completed tasks, so nothing actually goes to the agent
                task = await db_objects.create(Task, callback=cb, operator=op, params=data['command'],
                                               status="processed", original_params=data['command'])
                raw_rsp = await get_all_not_completed_tasks_for_callback_func(cb.id, user)
                if raw_rsp['status'] == 'success':
                    rsp = ""
                    for t in raw_rsp['tasks']:
                        rsp += "\nOperator: " + t['operator'] + "\nTask " + str(t['id']) + ": " + t['command'] + " " + \
                               t['params'] + "\nStatus: " + t['status']
                    await db_objects.create(Response, task=task, response=rsp)
                    return {'status': 'success', **task.to_json(), 'command': 'tasks'}
                else:
                    return {'status': 'error', 'error': 'failed to get tasks', 'cmd': data['command'],
                            'params': data['params']}
            elif data['command'] == "clear":
                # this means we're going to be clearing out some tasks depending on our access levels
                task = await db_objects.create(Task, callback=cb, operator=op, params="clear " + data['params'],
                                               status="processed", original_params="clear " + data['params'])
                raw_rsp = await clear_tasks_for_callback_func({"task": data['params']}, cb.id, user)
                if raw_rsp['status'] == 'success':
                    rsp = "Removed the following:"
                    for t in raw_rsp['tasks_removed']:
                        rsp += "\nOperator: " + t['operator'] + "\nTask " + str(t['id']) + ": " + t['command'] + " " + t['params']
                    await db_objects.create(Response, task=task, response=rsp)
                    return {'status': 'success', **task.to_json()}
                else:
                    return {'status': 'error', 'error': raw_rsp['error'], 'cmd': data['command'],
                            'params': data['params']}
            # it's not tasks/clear, so return an error
            return {'status': 'error', 'error': data['command'] + ' is not a registered command', 'cmd': data['command'],
                    'params': data['params']}
        file_meta = ""
        # some tasks require a bit more processing, so we'll handle that here so it's easier for the implant
        if cmd.cmd == "upload":
            upload_config = js.loads(data['params'])
            # we need to get the file into the database before we can signal for the callback to pull it down
            try:
                # see if we actually submitted "file_id /remote/path/here"
                if 'file_id' in upload_config and upload_config['file_id'] > 0:
                    f = await db_objects.get(FileMeta, id=upload_config['file_id'])
                    # we don't want to lose our tracking on this file, so we'll create a new database entry
                    file_meta = await db_objects.create(FileMeta, total_chunks=f.total_chunks, chunks_received=f.chunks_received,
                                                        complete=f.complete, path=f.path, operation=f.operation, operator=op)
                    data['file_updates_with_task'].append(file_meta)
                elif 'file' in upload_config:
                    # we just made the file for this instance, so just use it as the file_meta
                    # in this case it's already added to data['file_updates_with_task']
                    file_meta = await db_objects.get(FileMeta, id=upload_config['file'])
                # now normalize the data for the agent since it doesn't care if it was an old or new file_id to upload
                data['params'] = js.dumps({'remote_path': upload_config['remote_path'], 'file_id': file_meta.id})
            except Exception as e:
                print(e)
                return {'status': 'error', 'error': 'failed to get file info from the database: ' + str(e), 'cmd': data['command'], 'params': data['params']}

        elif cmd.cmd == "download":
            if '"' in data['params']:
                data['params'] = data['params'][1:-1]  # remove "" around the string at this point if they are there
        elif cmd.cmd == "screencapture":
            data['params'] = datetime.datetime.utcnow().strftime('%Y-%m-%d-%H:%M:%S') + ".png"
        elif cmd.cmd == "load":
            try:
                status = await perform_load_transforms(data, cb, operation, op)
                if status['status'] == 'error':
                    return {**status, 'cmd': data['command'], 'params': data['params']}
                # now create a corresponding file_meta
                file_meta = await db_objects.create(FileMeta, total_chunks=1, chunks_received=1, complete=True,
                                                    path=status['path'], operation=cb.operation)
                data['file_updates_with_task'].append(file_meta)
                data['params'] = js.dumps({"cmds": data['params'], "file_id": file_meta.id})

            except Exception as e:
                print(e)
                return {'status': 'error', 'error': 'failed to open and encode new function', 'cmd': data['command'], 'params': data['params']}
        # now actually run through all of the command transforms
        original_params = data['params']
        step_output = {}  # keep track of output at each stage
        step_output["0 - initial params"] = data['params']
        cmd_transforms = await get_commandtransforms_func(cmd.id, operation.name)
        if cmd_transforms['status'] == 'success':
            # reload our transforms right before use if we are actually going to do some
            if len(cmd_transforms['transforms']) > 0:
                try:
                    import app.api.transforms.utils
                    importlib.reload(sys.modules['app.api.transforms.utils'])
                except Exception as e:
                    print(e)
                from app.api.transforms.utils import CommandTransformOperation
                commandTransforms = CommandTransformOperation()
            for t in cmd_transforms['transforms']:
                if data['transform_status'][str(t['order'])]:  # if this is set to active, do it
                    try:
                        data['params'] = await getattr(commandTransforms, t['name'])(data['params'], t['parameter'])
                        step_output[str(t['order']) + " - " + t['name']] = data['params']
                    except Exception as e:
                        print(e)
                        return {'status': 'error', 'error': 'failed to apply transform {}, with message: {}'.format(
                            t['name'], str(e)), 'cmd': data['command'], 'params': original_params}
        else:
            return {'status': 'error', 'error': 'failed to get command transforms with message: {}'.format(
                            str(cmd_transforms['error'])), 'cmd': data['command'], 'params': original_params}
        if "test_command" in data and data['test_command']:
            # we just wanted to test out how things would end up looking, but don't actually create a Task for this
            # remove all of the fileMeta objects we created in prep for this since it's not a real issuing
            for update_file in data['file_updates_with_task']:
                await db_objects.delete(update_file)
                # we only want to delete the file from disk if there are no other db objects pointing to it
                # so we need to check other FileMeta.paths and Payload.locations
                file_count = await db_objects.count(FileMeta.select().where( (FileMeta.path == update_file.path) & (FileMeta.deleted == False)))
                file_count += await db_objects.count(Payload.select().where( (Payload.location == update_file.path) & (Payload.deleted == False)))
                try:
                    if file_count == 0:
                        os.remove(update_file.path)
                except Exception as e:
                    pass
            try:
                await db_objects.delete(file_meta)
                file_count = await db_objects.count(FileMeta.select().where( (FileMeta.path == file_meta.path) & (FileMeta.deleted == False)))
                file_count += await db_objects.count(Payload.select().where( (Payload.location == file_meta.path) & (Payload.deleted == False)))
                if file_count == 0:
                    os.remove(file_meta.path)
            except Exception as e:
                pass
            return {'status': 'success', 'cmd': data['command'], 'params': original_params, 'test_output': step_output}
        if original_params is None:
            original_params = data['params']
        if task is None:
            task = await db_objects.create(Task, callback=cb, operator=op, command=cmd, params=data['params'], original_params=original_params)
        await add_command_attack_to_task(task, cmd)
        for update_file in data['file_updates_with_task']:
            # now we can associate the task with the filemeta object
            update_file.task = task
            await db_objects.update(update_file)
        status = {'status': 'success'}
        task_json = task.to_json()
        task_json['task_status'] = task_json['status']  # we don't want the two status keys to conflict
        task_json.pop('status')
        return {**status, **task_json}
    except Exception as e:
        print("failed to get something in add_task_to_callback_func " + str(e))
        return {'status': 'error', 'error': 'Failed to create task: ' + str(e), 'cmd': data['command'], 'params': data['params']}


async def perform_load_transforms(data, cb, operation, op):
    # in the end this returns a dict of status and either a final file path or an error message
    load_transforms = await get_transforms_func(cb.registered_payload.payload_type.ptype, "load")
    if load_transforms['status'] == "success":
        # if we need to do something like compile or put code in a specific format
        #   we should have a temp working directory for whatever needs to be done, similar to payload creation
        uuid = await generate_uuid()
        working_path = "./app/payloads/operations/{}/{}".format(operation.name, uuid)
        # copy the payload type's files there
        shutil.copytree("./app/payloads/{}/payload/".format(cb.registered_payload.payload_type.ptype), working_path)
        # now that we copied files here, do the same replacement we do for creating a payload
        for base_file in glob.iglob(working_path + "/*", recursive=False):
            base = open(base_file, 'r')
            # write to the new file, then copy it over when we're done
            custom = open(working_path + "/" + uuid, 'w')  # make sure our temp file won't exist
            for line in base:
                if 'C2PROFILE_NAME_HERE' in line:
                    # optional directive to insert the name of the c2 profile
                    replaced_line = line.replace("C2PROFILE_NAME_HERE", cb.registered_payload.c2_profile.name)
                    custom.write(replaced_line)
                elif 'UUID_HERE' in line:
                    replaced_line = line.replace("UUID_HERE", uuid)
                    custom.write(replaced_line)
                else:
                    custom.write(line)
            base.close()
            custom.close()
            os.remove(base_file)
            os.rename(working_path + "/" + uuid, base_file)
        # also copy over and handle the c2 profile files just in case they have header files or anything needed
        for file in glob.glob(r'./app/c2_profiles/{}/{}/{}/*'.format(cb.registered_payload.operation.name,
                                                                     cb.registered_payload.c2_profile.name,
                                                                     cb.registered_payload.payload_type.ptype)):
            # once we copy a file over, try to replace some c2 params in it
            try:
                base_c2 = open(file, 'r')
                base_c2_new = open(working_path + "/{}".format(file.split("/")[-1]), 'w')
            except Exception as e:
                shutil.rmtree(working_path)
                return {'status': 'error', 'error': 'failed to open c2 code'}
            await write_c2(base_c2_new, base_c2, cb.registered_payload)
            base_c2.close()
            base_c2_new.close()
        transform_output = []
        # always start with a list of paths for all of the things we want to load
        # check if somebody submitted {'cmds':'shell,load, etc', 'file_id': 4} instead of list of commands
        try:
            replaced_params = data['params'].replace("'", '"')
            funcs = js.loads(replaced_params)['cmds']
        except Exception as e:
            funcs = data['params']
        data['params'] = funcs
        for p in data['params'].split(","):
            # register this command as one that we're going to have loaded into the callback
            try:
                command = await db_objects.get(Command, payload_type=cb.registered_payload.payload_type,
                                               cmd=p)
                try:
                    loaded_command = await db_objects.get(LoadedCommands, callback=cb, command=command)
                    loaded_command.version = command.version
                    await db_objects.update(loaded_command)
                except Exception as e:
                    # we couldn't find it, so we need to create it since this is a new command, not an update
                    loaded_command = await db_objects.create(LoadedCommands, callback=cb, command=command,
                                                            version=command.version, operator=op)
            except Exception as e:
                print(e)
            transform_output.append(
                "./app/payloads/{}/commands/{}".format(cb.registered_payload.payload_type.ptype, p.strip()))
        # if we actually have transforms to do, then reload the utils to make sure we're using the latest
        if len(load_transforms['transforms']) > 0:
            try:
                import app.api.transforms.utils
                importlib.reload(sys.modules['app.api.transforms.utils'])
            except Exception as e:
                print(e)
            from app.api.transforms.utils import TransformOperation
            transforms = TransformOperation(working_dir=working_path + "/")

        for t in load_transforms['transforms']:
            try:
                transform_output = await getattr(transforms, t['name'])(cb.registered_payload,
                                                                        transform_output, t['parameter'])
            except Exception as e:
                print(e)
                shutil.rmtree(working_path)
                return {'status': 'error', 'error': 'failed to apply transform {}, with message: {}'.format(
                    t['name'], str(e)), 'cmd': data['command'], 'params': data['params']}
        # at the end, we need to make sure our final file path is not located in our current working dir
        # if the user selected a file outside of it, that's fine, same with if they did something that got it there
        # if not, handle it for them here
        if working_path in transform_output:
            new_path = "./app/payloads/operations/{}/load-{}".format(operation.name, datetime.datetime.utcnow())
            shutil.copy(transform_output, new_path)
            transform_output = new_path
        # now that the file is in a good place, remove the working area
        shutil.rmtree(working_path)
        return {'status': 'success', 'path': transform_output}
    else:
        return {'status': 'error', 'error': 'failed to get transforms for this payload type', 'cmd': data['command'],
                'params': data['params']}


async def add_command_attack_to_task(task, command):
    try:
        attack_mappings = await db_objects.execute(ATTACKCommand.select().where(ATTACKCommand.command == command))
        for attack in attack_mappings:
            await db_objects.get_or_create(ATTACKTask, task=task, attack=attack.attack)
        # now do the artifact adjustments as well
        artifacts = await db_objects.execute(ArtifactTemplate.select().where(ArtifactTemplate.command == command))
        for artifact in artifacts:
            temp_string = artifact.artifact_string
            if artifact.command_parameter is not None and artifact.command_parameter != 'null':
                # we need to swap out temp_string's replace_string with task's param's command_parameter.name value
                parameter_dict = js.loads(task.params)
                temp_string = temp_string.replace(artifact.replace_string, str(parameter_dict[artifact.command_parameter.name]))
            else:
                # we need to swap out temp_string's replace_string with task's params value
                if artifact.replace_string != "":
                    temp_string = temp_string.replace(artifact.replace_string, str(task.params))
            await db_objects.create(TaskArtifact, task=task, artifact_template=artifact, artifact_instance=temp_string)

    except Exception as e:
        print(e)
        raise e


@apfell.route(apfell.config['API_BASE'] + "/tasks/callback/<cid:int>/notcompleted", methods=['GET'])
@inject_user()
@protected()
async def get_all_not_completed_tasks_for_callback(request, cid, user):
    return json(await get_all_not_completed_tasks_for_callback_func(cid, user))


async def get_all_not_completed_tasks_for_callback_func(cid, user):
    try:
        callback = await db_objects.get(Callback, id=cid)
        operation = await db_objects.get(Operation, id=callback.operation)
    except Exception as e:
        print(e)
        return {'status': 'error', 'error': 'failed to get callback or operation'}
    if operation.name in user['operations']:
        # Get all tasks that have a status of submitted or processing
        tasks = await db_objects.execute(Task.select().join(Callback).where(
            (Task.callback == callback) & (Task.status != "processed")).order_by(Task.timestamp))
        return {'status': 'success', 'tasks': [x.to_json() for x in tasks]}
    else:
        return {'status': 'error', 'error': 'You must be part of the operation to view this information'}


@apfell.route(apfell.config['API_BASE'] + "/tasks/callback/<cid:int>/clear", methods=['POST'])
@inject_user()
@protected()
async def clear_tasks_for_callback(request, cid, user):
    return json(await clear_tasks_for_callback_func(request.json, cid, user))


async def clear_tasks_for_callback_func(data, cid, user):
    try:
        callback = await db_objects.get(Callback, id=cid)
        operation = await db_objects.get(Operation, id=callback.operation)
    except Exception as e:
        print(e)
        return {'status': 'error', 'error': 'failed to get callback or operation'}
    tasks_removed = []
    if "all" == data['task']:
        tasks = await db_objects.execute(Task.select().join(Callback).where(
            (Task.callback == callback) & (Task.status == "submitted")).order_by(Task.timestamp))
    elif len(data['task']) > 0:
        #  if the user specifies a task, make sure that it's not being processed
        tasks = await db_objects.execute(Task.select().where( (Task.id == data['task']) & (Task.status == "submitted")))
    else:
        # if you don't actually specify a task, remove the the last task that was entered
        tasks = await db_objects.execute(Task.select().where(
            (Task.status == "submitted") & (Task.callback == callback)
        ).order_by(-Task.timestamp).limit(1))
    for t in tasks:
        if operation.name in user['operations']:
            try:
                t_removed = t.to_json()
                # don't actually delete it, just mark it as completed with a response of "CLEARED TASK"
                t.status = "processed"
                await db_objects.update(t)
                # we need to adjust all of the things associated with this task now since it didn't actually happen
                # find/remove ATTACKTask, TaskArtifact, FileMeta
                attack_tasks = await db_objects.execute(ATTACKTask.select().where(ATTACKTask.task == t))
                for at in attack_tasks:
                    await db_objects.delete(at, recursive=True)
                task_artifacts = await db_objects.execute(TaskArtifact.select().where(TaskArtifact.task == t))
                for ta in task_artifacts:
                    await db_objects.delete(ta, recursive=True)
                file_metas = await db_objects.execute(FileMeta.select().where(FileMeta.task == t))
                for fm in file_metas:
                    os.remove(fm.path)
                    await db_objects.delete(fm, recursive=True)
                # now create the response so it's easy to track what happened with it
                response = "CLEARED TASK by " + user['username']
                await db_objects.create(Response, task=t, response=response)
                tasks_removed.append(t_removed)
            except Exception as e:
                print(e)
                return {'status': 'error', 'error': 'failed to delete task: ' + t.command.cmd}
    return {'status': 'success', 'tasks_removed': tasks_removed}


@apfell.route(apfell.config['API_BASE'] + "/tasks/<tid:int>", methods=['GET'])
@inject_user()
@protected()
async def get_one_task_and_responses(request, tid, user):
    try:
        task = await db_objects.get(Task, id=tid)
        if task.callback.operation.name in user['operations']:
            responses = await db_objects.execute(Response.select().where(Response.task == task))
            return json({'status': "success", "callback": task.callback.to_json(), "task": task.to_json(), "responses": [r.to_json() for r in responses]})
        else:
            return json({'status': 'error', 'error': 'you don\'t have access to that task'})
    except Exception as e:
        print(e)
        return json({'status': 'error', 'error': 'failed to find that task'})


@apfell.route(apfell.config['API_BASE'] + "/tasks/comments/<tid:int>", methods=['POST'])
@inject_user()
@protected()
async def add_comment_to_task(request, tid, user):
    try:
        task = await db_objects.get(Task, id=tid)
        data = request.json
        operator = await db_objects.get(Operator, username=user['username'])
        if task.callback.operation.name in user['operations']:
            if 'comment' in data:
                task.comment = data['comment']
                task.comment_operator = operator
                await db_objects.update(task)
                return json({'status': "success", "task": task.to_json()})
            else:
                return json({'status': 'error', 'error': 'must supply a "comment" to add'})
        else:
            return json({'status': 'error', 'error': 'you don\'t have access to that task'})
    except Exception as e:
        print(e)
        return json({'status': 'error', 'error': 'failed to find that task'})


@apfell.route(apfell.config['API_BASE'] + "/tasks/comments/<tid:int>", methods=['DELETE'])
@inject_user()
@protected()
async def remove_task_comment(request, tid, user):
    try:
        task = await db_objects.get(Task, id=tid)
        operator = await db_objects.get(Operator, username=user['username'])
        if task.callback.operation.name in user['operations']:
            task.comment = ""
            task.comment_operator = operator
            await db_objects.update(task)
            return json({'status': "success", "task": task.to_json()})
        else:
            return json({'status': 'error', 'error': 'you don\'t have access to that task'})
    except Exception as e:
        print(e)
        return json({'status': 'error', 'error': 'failed to find that task'})


@apfell.route(apfell.config['API_BASE'] + "/tasks/comments/by_operator", methods=['GET'])
@inject_user()
@protected()
async def get_comments_by_operator_in_current_operation(request, user):
    try:
        operation = await db_objects.get(Operation, name=user['current_operation'])
        operator_operation = await db_objects.execute(OperatorOperation.select().where(OperatorOperation.operation == operation))
    except Exception as e:
        return json({'status': 'error', 'error': 'failed to find operator or operation: ' + str(e)})
    operators_list = []
    for mapping in operator_operation:
        operator = mapping.operator
        tasks = await db_objects.execute(Task.select().where( (Task.comment_operator == operator) & (Task.comment != "")).join(Callback).where(Callback.operation == operation).order_by(Task.id))
        callbacks = {}
        for t in tasks:
            responses = await db_objects.execute(Response.select().where(Response.task == t))
            if t.callback.id not in callbacks:
                callbacks[t.callback.id] = t.callback.to_json()
                callbacks[t.callback.id]['tasks'] = []
            callbacks[t.callback.id]['tasks'].append({**t.to_json(), "responses": [r.to_json() for r in responses]})
        if len(callbacks.keys()) > 0:
            operators_list.append({**operator.to_json(), 'callbacks': list(callbacks.values())})
    return json({'status': 'success', 'operators': operators_list})


@apfell.route(apfell.config['API_BASE'] + "/tasks/comments/by_callback", methods=['GET'])
@inject_user()
@protected()
async def get_comments_by_callback_in_current_operation(request, user):
    try:
        operator = await db_objects.get(Operator, username=user['username'])
        operation = await db_objects.get(Operation, name=user['current_operation'])
    except Exception as e:
        return json({'status': 'error', 'error': 'failed to find operator or operation: ' + str(e)})
    tasks = await db_objects.execute(Task.select().where(Task.comment != "").join(Callback).where(Callback.operation == operation).order_by(Task.id))
    callbacks = {}
    for t in tasks:
        responses = await db_objects.execute(Response.select().where(Response.task == t))
        if t.callback.id not in callbacks:
            callbacks[t.callback.id] = t.callback.to_json()
            callbacks[t.callback.id]['tasks'] = []
        callbacks[t.callback.id]['tasks'].append({**t.to_json(), "responses": [r.to_json() for r in responses]})
    return json({'status': 'success', 'callbacks': list(callbacks.values())})


@apfell.route(apfell.config['API_BASE'] + "/tasks/comments/search", methods=['POST'])
@inject_user()
@protected()
async def search_comments_by_callback_in_current_operation(request, user):
    try:
        operator = await db_objects.get(Operator, username=user['username'])
        operation = await db_objects.get(Operation, name=user['current_operation'])
        data = request.json
        if 'search' not in data:
            return json({'status': 'error', 'error': 'search is required'})
    except Exception as e:
        return json({'status': 'error', 'error': 'failed to find operator or operation: ' + str(e)})
    tasks = await db_objects.execute(Task.select().where(Task.comment.contains(data['search'])).join(Callback).where(Callback.operation == operation).order_by(Task.id))
    callbacks = {}
    for t in tasks:
        responses = await db_objects.execute(Response.select().where(Response.task == t))
        if t.callback.id not in callbacks:
            callbacks[t.callback.id] = t.callback.to_json()
            callbacks[t.callback.id]['tasks'] = []
        callbacks[t.callback.id]['tasks'].append({**t.to_json(), "responses": [r.to_json() for r in responses]})
    return json({'status': 'success', 'callbacks': list(callbacks.values())})