#!/usr/bin/env python
# -*- coding: utf-8 -*-

""" description
mysql db client pool use threading
兼容 ultramysql, MySQLdb, pymysql
"""

__author__ = 'wangfei'
__date__ = '2015/03/12'

from threading import Thread, Condition, Lock
from Queue import Queue
import logging
import sys

logger = logging.getLogger(__name__)


class AsyncResult(object):
    """threading模块提供了Event,但是没提供future/promise模式的异步 AsyncResult
    AsyncResult类似阻塞channel
    Queue类似非阻塞channel
    """

    def __init__(self):
        self.cond = Condition(Lock())
        self.value = None
        self.exception = None

    def set(self, value=None):
        self.cond.acquire()
        self.value = value
        try:
            self.cond.notify_all()
        finally:
            self.cond.release()

    def set_exception(self, exception):
        self.cond.acquire()
        self.exception = exception
        try:
            self.cond.notify_all()
        finally:
            self.cond.release()

    def get(self, block=True, timeout=None):
        self.cond.acquire()
        try:
            if block:
                self.cond.wait(timeout)
        finally:
            self.cond.release()
            if self.exception is not None:
                raise self.exception
            return self.value


def test_AsyncResult():
    from threading import Thread
    import time

    event = AsyncResult()

    def foo(event):
        time.sleep(5)
        event.set('ooxx')
        # event.set_exception(Exception('ooxx'))

    t = Thread(target=foo, args=(event,))
    t.start()
    print event.get()
    print 'over..'


class Pool(object):
    """连接池,每个连接使用一个队列的连接池
    """

    def __init__(self, options, n, adapter='ultramysql'):
        """options必须有reconnect_delay参数且>0
        : 使用事务时需要指定同一个qid
        """
        assert options.get('reconnect_delay', 0) > 0

        self.conns = []
        self.queues = []
        self.tasks = []

        if adapter == 'ultramysql':
            from client import UMySQLConnection as Connection
        elif adapter == 'MySQLdb':
            from client import MySQLdbConnection as Connection
        elif adapter == 'pymysql':
            from client import PyMySQLConnection as Connection
        else:
            raise Exception('mysql client adapter not found')

        for _ in xrange(n):
            c = Connection(**options)
            self.conns.append(c)
            q = Queue()
            self.queues.append(q)
            t = Thread(target=self.loop, args=(c, q))
            t.setDaemon(True)  # 主线程退出子线程退出
            t.start()
            self.tasks.append(t)

        assert len(self.conns) == n

    def loop(self, conn, q):
        """
        :param q, 队列格式: (sql, args, op, result)
        op是操作类型, execute, fetchone, fetchall, get_fields
        result: 是gevent的AsyncResult对象, result为空则非阻塞
        """
        while True:
            sql, args, op, async_result = q.get()
            try:
                rs = getattr(conn, op)(sql, *args)
                if async_result:
                    async_result.set(rs)
            except Exception as e:
                logger.error('[Last query]: %s %s', sql, str(args), exc_info=1)
                if async_result:
                    async_result.set_exception(Exception(sys.exc_info()[1]))
                else:
                    logger.error(str(e))
            finally:
                pass

    def join(self):
        map(lambda t: t.join(), self.tasks)

    def _selectq(self, qid=-1):
        """选择第几个队列, 默认返回长度最小的队列
        """
        if qid >= 0:
            return self.queues[qid]
        minq = min(self.queues, key=lambda q: q.qsize())
        return minq

    def _query(self, sql, args=tuple(), op='execute', qid=-1, block=True):
        q = self._selectq(qid)
        if block:
            async_result = AsyncResult()
            q.put((sql, args, op, async_result))
            return async_result.get()
        else:
            q.put((sql, args, op, None))

    def execute(self, sql, args=tuple(), qid=-1, block=True):
        return self._query(sql, args, 'execute', qid, block)

    def fetchone(self, sql, args=tuple(), qid=-1, block=True):
        return self._query(sql, args, 'fetchone', qid, block)

    def fetchall(self, sql, args=tuple(), qid=-1, block=True):
        return self._query(sql, args, 'fetchall', qid, block)

    def get_fields(self, table_name):
        return self._query('select * from %s limit 0' % table_name, tuple(), 'get_fields', qid=-1, block=True)


if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG, format='[%(asctime)-15s %(levelname)s:%(module)s] %(message)s')

    test_options = dict(host='localhost', user='root', passwd='112358', db='test', reconnect_delay=5)
    pool = Pool(test_options, 20, adapter='pymysql')

    print pool.fetchall('select * from book where author = %s', (u'小小', ))

    # 像 execute 如果不关心执行结果,可以异步执行
    pool.execute('insert into book set name="abc", author=%s', (u'小小', ), block=False)

    pool.join()
