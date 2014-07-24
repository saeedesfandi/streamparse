"""Base Bolt classes."""
from __future__ import absolute_import, print_function, unicode_literals

from collections import defaultdict
import os
import signal
import sys
import threading
import time

from six import iteritems, reraise, PY3

from .base import Component
from .ipc import (read_handshake, read_tuple, send_message, json, _stdout,
                  Tuple)


class Bolt(Component):
    """The base class for all streamparse bolts.

    For more information on bolts, consult Storm's
    `Concepts documentation <http://storm.incubator.apache.org/documentation/Concepts.html>`_.

    **Example**:

    .. code-block:: python

        from streamparse.bolt import Bolt

        class SentenceSplitterBolt(Bolt):

            AUTO_ANCHOR = True  # perform auto anchoring during emits
            AUTO_ACK = True  # perform auto acking after process()
            AUTO_FAIL = True  # perform auto fail on exceptions

            def process(self, tup):
                sentence = tup.values[0]
                for word in sentence.split(" "):
                    self.emit([word])
    """

    AUTO_ANCHOR = False
    """A ``bool`` indicating whether or not the bolt should automatically
    anchor emits to the incoming tuple ID. Tuple anchoring is how Storm
    provides reliability, you can read more about `tuple anchoring in Storm's
    docs <https://storm.incubator.apache.org/documentation/Guaranteeing-message-processing.html#what-is-storms-reliability-api>`_. Default is ``False``.
    """
    AUTO_ACK = False
    """A ``bool`` indicating whether or not the bolt should automatically
    acknowledge tuples after ``process()`` is called. Default is ``False``.
    """
    AUTO_FAIL = False
    """A ``bool`` indicating whether or not the bolt should automatically fail
    tuples when an exception occurs when the ``process()`` method is called.
    Default is ``False``.
    """

    # Using a list so Bolt and subclasses can have more than one current_tup
    _current_tups = []

    def initialize(self, storm_conf, context):
        """Called immediately after the initial handshake with Storm and before
        the main run loop. A good place to initialize connections to data
        sources.

        :param storm_conf: the Storm configuration for this Bolt.  This is the
                           configuration provided to the topology, merged in
                           with cluster configuration on the worker node.
        :type storm_conf: dict
        :param context: information about the component's place within the
                        topology such as: task IDs, inputs, outputs etc.
        :type context: dict
        """
        pass

    def process(self, tup):
        """Process a single tuple :class:`streamparse.ipc.Tuple` of input

        This should be overridden by subclasses.
        :class:`streamparse.ipc.Tuple` objects contain metadata about which
        component, stream and task it came from. The actual values of the
        tuple can be accessed by calling ``tup.values``.

        :param tup: the tuple to be processed.
        :type tup: streamparse.ipc.Tuple
        """
        raise NotImplementedError()

    def emit(self, tup, stream=None, anchors=None, direct_task=None):
        """Emit a new tuple to a stream.

        :param tup: the Tuple payload to send to Storm, should contain only
                    JSON-serializable data.
        :type tup: list
        :param stream: the ID of the stream to emit this tuple to. Specify
                       ``None`` to emit to default stream.
        :type stream: str
        :param anchors: IDs the tuples (or :class:`streamparse.ipc.Tuple`
                        instances) which the emitted tuples should be anchored
                        to. If ``AUTO_ANCHOR`` is set to ``True`` and
                        you have not specified ``anchors``, ``anchors`` will be
                        set to the incoming/most recent tuple ID(s).
        :type anchors: list
        :param direct_task: the task to send the tuple to.
        :type direct_task: int
        """
        if not isinstance(tup, list):
            raise TypeError('All tuples must be lists, received {!r} instead.'
                            .format(type(tup)))

        msg = {'command': 'emit', 'tuple': tup}

        if anchors is None:
            anchors = self._current_tups if self.AUTO_ANCHOR else []
        msg['anchors'] = [a.id if isinstance(a, Tuple) else a for a in anchors]

        if stream is not None:
            msg['stream'] = stream
        if direct_task is not None:
            msg['task'] = direct_task

        send_message(msg)

    def emit_many(self, tuples, stream=None, anchors=None, direct_task=None):
        """A more efficient way to send many tuples.

        Dumps out all tuples to STDOUT instead of writing one at a time.

        :param tuples: a ``list`` containing ``list`` s of tuple payload data
                       to send to Storm. All tuples should contain only
                       JSON-serializable data.
        :type tuples: list
        :param stream: the ID of the steram to emit these tuples to. Specify
                       ``None`` to emit to default stream.
        :type stream: str
        :param anchors: IDs the tuples (or :class:`streamparse.ipc.Tuple`
                        instances) which the emitted tuples should be anchored
                        to. If ``AUTO_ANCHOR`` is set to ``True`` and
                        you have not specified ``anchors``, ``anchors`` will be
                        set to the incoming/most recent tuple ID(s).
        :type anchors: list
        :param direct_task: indicates the task to send the tuple to.
        :type direct_task: int
        """
        if not isinstance(tuples, list):
            raise TypeError('tuples should be a list of lists, received {!r}'
                            'instead.'.format(type(tuples)))

        msg = {'command': 'emit'}

        if anchors is None:
            anchors = self._current_tups if self.AUTO_ANCHOR else []
        msg['anchors'] = [a.id if isinstance(a, Tuple) else a for a in anchors]

        if stream is not None:
            msg['stream'] = stream
        if direct_task is not None:
            msg['task'] = direct_task

        lines = []
        for tup in tuples:
            msg['tuple'] = tup
            lines.append(json.dumps(msg))
        wrapped_msg = "{}\nend\n".format("\nend\n".join(lines)).encode('utf-8')
        if PY3:
            _stdout.flush()
            _stdout.buffer.write(wrapped_msg)
        else:
            _stdout.write(wrapped_msg)
        _stdout.flush()

    def ack(self, tup):
        """Indicate that processing of a tuple has succeeded.

        :param tup: the tuple to acknowledge.
        :type tup: str or Tuple
        """
        tup_id = tup.id if isinstance(tup, Tuple) else tup
        send_message({'command': 'ack', 'id': tup_id})

    def fail(self, tup):
        """Indicate that processing of a tuple has failed.

        :param tup: the tuple to fail (``id`` if ``str``).
        :type tup: str or Tuple
        """
        tup_id = tup.id if isinstance(tup, Tuple) else tup
        send_message({'command': 'fail', 'id': tup_id})

    def run(self):
        """Main run loop for all bolts.

        Performs initial handshake with Storm and reads tuples handing them off
        to subclasses.  Any exceptions are caught and logged back to Storm
        prior to the Python process exiting.

        Subclasses should **not** override this method.
        """
        storm_conf, context = read_handshake()
        try:
            self.initialize(storm_conf, context)
            while True:
                self._current_tups = [read_tuple()]
                self.process(self._current_tups[0])
                if self.AUTO_ACK:
                    self.ack(self._current_tups[0])
                # reset so that we don't accidentally fail the wrong tuples
                # if a successive call to read_tuple fails
                self._current_tups = []
        except Exception as e:
            if self.AUTO_FAIL and self._current_tups:
                for tup in self._current_tups:
                    self.fail(tup)
            self.raise_exception(e, self._current_tups[0])
            sys.exit(1)


class BatchingBolt(Bolt):
    """A bolt which batches tuples for processing.

    Batching tuples is unexpectedly complex to do correctly. The main problem
    is that all bolts are single-threaded. The difficult comes when the
    topology is shutting down because Storm stops feeding the bolt tuples. If
    the bolt is blocked waiting on stdin, then it can't process any waiting
    tuples, or even ack ones that were asynchronously written to a data store.

    This bolt helps with that grouping tuples based on a time interval and then
    processing them on a worker thread.

    To use this class, you must implement ``process_batch``. ``group_key`` can
    be optionally implemented so that tuples are grouped before
    ``process_batch`` is even called.

    **Example**:

    .. code-block:: python

        from streamparse.bolt import BatchingBolt

        class WordCounterBolt(BatchingBolt):

            SECS_BETWEEN_BATCHES = 5
            AUTO_ACK = True
            AUTO_ANCHOR = True
            AUTO_FAIL = True

            def group_key(self, tup):
                word = tup.values[0]
                return word  # collect batches of words

            def process_batch(self, key, tups):
                # emit the count of words we had per 5s batch
                self.emit([key, len(tups)])

    """

    AUTO_ANCHOR = False
    """A ``bool`` indicating whether or not the bolt should automatically
    anchor emits to the incoming tuple ID. Tuple anchoring is how Storm
    provides reliability, you can read more about `tuple anchoring in Storm's
    docs <https://storm.incubator.apache.org/documentation/Guaranteeing-message-processing.html#what-is-storms-reliability-api>`_. Default is ``False``.
    """
    AUTO_ACK = False
    """A ``bool`` indicating whether or not the bolt should automatically
    acknowledge tuples after ``process_batch()`` is called. Default is
    ``False``.
    """
    AUTO_FAIL = False
    """A ``bool`` indicating whether or not the bolt should automatically fail
    tuples when an exception occurs when the ``process_batch()`` method is
    called. Default is ``False``.
    """
    SECS_BETWEEN_BATCHES = 2
    """The time (in seconds) between calls to ``process_batch()``. Note that if
    there are no tuples in any batch, the BatchingBolt will continue to sleep.
    Note: Can be fractional to specify greater precision (e.g. 2.5).
    """

    def __init__(self):
        super(BatchingBolt, self).__init__()
        self.exc_info = None
        signal.signal(signal.SIGINT, self._handle_worker_exception)

        self._batches = defaultdict(list)
        self._should_stop = threading.Event()
        self._batcher = threading.Thread(target=self._batch_entry)
        self._batch_lock = threading.Lock()
        self._batcher.daemon = True
        self._batcher.start()

    def group_key(self, tup):
        """Return the group key used to group tuples within a batch.

        By default, returns None, which put all tuples in a single
        batch, effectively just time-based batching. Override this create
        multiple batches based on a key.

        :param tup: the tuple used to extract a group key
        :type tup: Tuple
        :returns: Any ``hashable`` value.
        """
        return None

    def process_batch(self, key, tups):
        """Process a batch of tuples. Should be overridden by subclasses.

        :param key: the group key for the list of batches.
        :type key: hashable
        :param tups: a `list` of :class:`streamparse.ipc.Tuple` s for the group.
        :type tups: list
        """
        raise NotImplementedError()

    def run(self):
        """Modified and simplified run loop which runs in the main thread since
        we only need to add tuples to the proper batch for later processing
        in the _batcher thread.
        """
        storm_conf, context = read_handshake()
        self.initialize(storm_conf, context)
        while True:
            tup = read_tuple()
            group_key = self.group_key(tup)
            with self._batch_lock:
                self._batches[group_key].append(tup)

    def _batch_entry(self):
        """Entry point for the batcher thread."""
        try:
            while True:
                time.sleep(self.SECS_BETWEEN_BATCHES)
                with self._batch_lock:
                    if not self._batches:
                        # No tuples to save
                        continue
                    for key, batch in iteritems(self._batches):
                        self._current_tups = batch
                        self.process_batch(key, batch)
                        if self.AUTO_ACK:
                            for tup in batch:
                                self.ack(tup)
                    self._batches = defaultdict(list)
        except Exception as e:
            self.raise_exception(e, self._current_tups)
            if self.AUTO_FAIL and self._current_tups:
                for tup in self._current_tups:
                    self.fail(tup)
            self.exc_info = sys.exc_info()
            os.kill(os.getpid(), signal.SIGINT)  # interrupt stdin waiting

    def _handle_worker_exception(self, signum, frame):
        """Handle an exception raised in the worker thread.

        Exceptions in the _batcher thread will send a SIGINT to the main
        thread which we catch here, and then raise in the main thread.
        """
        reraise(*self.exc_info)


class BasicBolt(Bolt):
    """Legacy support for BasicBolt which simply sets all ``AUTO_*``
    instance vars to ``True``.

    Deprecated.
    """

    AUTO_ACK = True
    AUTO_ANCHOR = True
    AUTO_FAIL = True


class BasicBatchingBolt(Bolt):
    """Legacy support for BasicBatchingBolt which simply sets all ``AUTO_*``
    instance vars to ``True``.

    Deprecated.
    """

    AUTO_ACK = True
    AUTO_ANCHOR = True
    AUTO_FAIL = True
