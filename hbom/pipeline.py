import threading
import sys

__all__ = ['Pipeline']


class Pipeline(object):

    __slots__ = ['pipes', 'refs']

    def __init__(self):
        self.pipes = {}
        self.refs = {}

    def attach(self, model, force=False):
        """
        pass in a model object that hasn't been hydrated yet.
        We set up a pipeline callback handler that will read the data from the
        database and populate it into the object.
        the call will be pipelined on hydrate or execute.
        :param force:
        :param model:
        """
        if not force and getattr(model, '_init', False):
            return False

        pipe, refs = self._pipe_refs(model)
        refs.append(model.prepare(pipe))
        return True

    def hydrate(self, models, force=False):
        if not isinstance(models, list):
            models = [models]
        if any([self.attach(model, force=force) for model in models]):
            self.execute()
            return True
        return False

    def execute(self):
        # only need to use threads if we have more than one connection
        if len(self.pipes) > 1:
            threads = []
            # kick off all the threads
            for conn_id, pipe in self.pipes.items():
                t = ExecThread(pipe, self.refs[conn_id])
                t.start()
                threads.append(t)

            # wait for all the threads to finish executing
            for t in threads:
                t.join()

            # did any of them have problems?
            # if so raise the first one you find.
            for t in threads:
                if t.exc_info:
                    raise t.exc_info[0], t.exc_info[1], t.exc_info[2]
        else:
            # only one connection, no threads needed.
            # keep it simple.
            for conn_id, pipe in self.pipes.items():
                for i, result in enumerate(pipe.execute()):
                    self.refs[conn_id][i](result)

    def allocate_callback(self, instance, callback):
        pipe, refs = self._pipe_refs(instance)
        refs.append(callback)
        return pipe

    def allocate_response(self, instance):
        response = PipelineResponse()

        def set_data(data):
            response.data = data

        pipe = self.allocate_callback(instance, set_data)
        return response, pipe

    def _pipe_refs(self, instance):
        conn = instance.db()

        conn_id = id(conn)
        try:
            return self.pipes[conn_id], self.refs[conn_id]
        except KeyError:
            pipe = self.pipes[conn_id] = instance.db_pipeline()
            refs = self.refs[conn_id] = []
            return pipe, refs

    def __getattr__(self, command):
        def fn(*args, **kwargs):
            db_args = [a for a in args]
            if command == 'eval':
                ref = args[2]
                db_args[2] = ref.db_key(ref.primary_key())
            else:
                ref = args[0]
                db_args[0] = ref.db_key(ref.primary_key())
            response, pipe = self.allocate_response(ref)
            getattr(pipe, command)(*db_args, **kwargs)
            return response
        return fn


class PipelineResponse(object):

    __slots__ = ['data']

    def __init__(self, data=None):
        self.data = data

    def __iter__(self):
        yield 'data', self.data


class ExecThread(threading.Thread):
    def __init__(self, pipe, refs):
        threading.Thread.__init__(self)
        self.pipe = pipe
        self.refs = refs
        self.exc_info = None

    def run(self):
        # noinspection PyBroadException
        try:
            for i, result in enumerate(self.pipe.execute()):
                self.refs[i](result)
        except Exception:
            self.exc_info = sys.exc_info()
