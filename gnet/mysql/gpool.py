#!/usr/bin/env python
# -*- coding: utf-8 -*-

""" description
mysql db client pool use gevent
兼容 ultramysql, MySQLdb, pymysql
"""

__author__ = 'wangfei'
__date__ = '2015/03/12'

from gevent.event import AsyncResult
from gevent.queue import Queue
import gevent
import logging
import sys

logger = logging.getLogger(__name__)


class Pool(object):
    """连接池,每个连接使用一个gevent队列的连接池
    """

    def __init__(self, n, connection_cls, options={}):
        """options必须有reconnect_delay参数且>0
        : 使用事务时需要指定同一个qid
        """
        assert options.get('reconnect_delay', 0) > 0

        self.conns = []
        self.queues = []
        self.tasks = []

        for _ in xrange(n):
            c = connection_cls(**options)
            self.conns.append(c)
            q = Queue()
            self.queues.append(q)
            g = gevent.spawn(self.loop, c, q)
            self.tasks.append(g)

        assert len(self.conns) == n

    def loop(self, conn, q):
        """
        :param q, 队列格式: (sql, args, op, result)
        op是操作类型, execute, fetchone, fetchall, get_fields
        result: 是gevent的AsyncResult对象, result为空则非阻塞
        """
        while True:
            sql, args, op, async_result = q.peek()
            try:
                rs = getattr(conn, op)(sql, *args)
                if async_result:
                    async_result.set(rs)
            except Exception as e:
                logger.error('[Last query]: %s %s', sql, str(args), exc_info=1)
                if async_result:
                    async_result.set_exception(sys.exc_info()[1])
                else:
                    logger.error(str(e))
            finally:
                q.next()

    def _selectq(self, qid=-1):
        """选择第几个队列, 默认返回长度最小的队列
        """
        if qid >= 0:
            return self.queues[qid]
        minq = min(self.queues, key=lambda q: q.qsize())
        return minq

    def _query(self, sql, args=tuple(), op='execute', qid=-1, block=True):
        deferred = self._execute(sql, args, op, qid, block)
        if deferred:
            return deferred.get()

    def _execute(self, sql, args=tuple(), op='execute', qid=-1, block=True):
        q = self._selectq(qid)
        if block:
            async_result = AsyncResult()
            q.put((sql, args, op, async_result))
            return async_result
        else:
            q.put((sql, args, op, None))

    def map(self, op, sql_args, qid=-1, block=True):
        """并发map
        :param sql_args: [(sql, args), ..], eg. [(('select * from book where author=%s'), ('jack', )), ..]
        :return 返回结果list
        """
        deferreds = [self._execute(s[0], s[1], op=op, qid=qid, block=block) for s in sql_args]
        return [d.get() for d in deferreds]

    def execute(self, sql, args=tuple(), qid=-1, block=True):
        return self._query(sql, args, 'execute', qid, block)

    def fetchone(self, sql, args=tuple(), qid=-1, block=True):
        return self._query(sql, args, 'fetchone', qid, block)

    def fetchall(self, sql, args=tuple(), qid=-1, block=True):
        return self._query(sql, args, 'fetchall', qid, block)

    def get_fields(self, table_name):
        return self._query('select * from %s limit 0' % table_name, tuple(), 'get_fields', qid=-1, block=True)


if __name__ == '__main__':
    sys.modules.pop('threading', None)

    from gevent import monkey

    monkey.patch_all()
    from client import PyMySQLConnection

    logging.basicConfig(level=logging.DEBUG, format='[%(asctime)-15s %(levelname)s:%(module)s] %(message)s')

    test_options = dict(host='localhost', user='root', passwd='112358', db='test', reconnect_delay=5)
    pool = Pool(5, PyMySQLConnection, test_options)

    print pool.map('fetchall', [('select * from book where author = %s', (u'小小', )),
                                ('select * from book where author = %s', (u'大大', ))])
    print pool.fetchall('select * from book where author = %s', (u'小小', ))
    # 像 execute 如果不关心执行结果,可以异步执行
    pool.execute('insert into book set name="abc", author=%s', (u'小小', ), block=False)

    gevent.wait()