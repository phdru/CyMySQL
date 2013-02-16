# -*- coding: utf-8 -*-
import struct
import re
import sys

from cymysql.err import Warning, Error, InterfaceError, DataError, \
             DatabaseError, OperationalError, IntegrityError, InternalError, \
            NotSupportedError, ProgrammingError

insert_values = re.compile(r'\svalues\s*(\(.+\))', re.IGNORECASE)

PYTHON3 = sys.version_info[0] > 2

class Cursor(object):
    '''
    This is the object you use to interact with the database.
    '''
    def __init__(self, connection):
        '''
        Do not create an instance of a Cursor yourself. Call
        connections.Connection.cursor().
        '''
        from weakref import proxy
        self.connection = proxy(connection)
        self.description = None
        self.rownumber = 0
        self.rowcount = -1
        self.arraysize = 1
        self._executed = None
        self.messages = []
        self.errorhandler = connection.errorhandler
        self._has_next = None
        self._rows = ()

    def __del__(self):
        '''
        When this gets GC'd close it.
        '''
        self.close()

    def close(self):
        '''
        Closing a cursor just exhausts all remaining data.
        '''
        if not self.connection:
            return
        try:
            while self.nextset():
                pass
        except:
            pass

        self.connection = None

    def _get_db(self):
        if not self.connection:
            self.errorhandler(self, ProgrammingError, "cursor closed")
        return self.connection

    def _check_executed(self):
        if not self._executed:
            self.errorhandler(self, ProgrammingError, "execute() first")

    def setinputsizes(self, *args):
        """Does nothing, required by DB API."""

    def setoutputsizes(self, *args):
        """Does nothing, required by DB API."""

    def nextset(self):
        ''' Get the next query set '''
        if self._executed:
            self.fetchall()
        del self.messages[:]

        if not self._has_next:
            return None
        connection = self._get_db()
        connection.next_result()
        self._do_get_result()
        return True

    def execute(self, query, args=None):
        ''' Execute a query '''
        from sys import exc_info

        conn = self._get_db()
        charset = conn.charset
        del self.messages[:]

        # TODO: make sure that conn.escape is correct

        if PYTHON3 and (not isinstance(query, str)):
            query = query.decode(charset)
        if (not PYTHON3) and isinstance(query, unicode):
            query = query.encode(charset)

        if args is not None:
            if isinstance(args, tuple) or isinstance(args, list):
                escaped_args = tuple(conn.escape(arg) for arg in args)
            elif isinstance(args, dict):
                escaped_args = dict((key, conn.escape(val)) for (key, val) in args.items())
            else:
                #If it's not a dictionary let's try escaping it anyways.
                #Worst case it will throw a Value error
                escaped_args = conn.escape(args)

            query = query % escaped_args

        result = 0
        try:
            result = self._query(query)
        except:
            exc, value, tb = exc_info()
            del tb
            self.messages.append((exc,value))
            self.errorhandler(self, exc, value)

        self._executed = query
        return result

    def executemany(self, query, args):
        ''' Run several data against one query '''
        del self.messages[:]
        #conn = self._get_db()
        if not args:
            return
        #charset = conn.charset
        #if isinstance(query, unicode):
        #    query = query.encode(charset)

        self.rowcount = sum([ self.execute(query, arg) for arg in args ])
        return self.rowcount


    def callproc(self, procname, args=()):
        """Execute stored procedure procname with args

        procname -- string, name of procedure to execute on server

        args -- Sequence of parameters to use with procedure

        Returns the original args.

        Compatibility warning: PEP-249 specifies that any modified
        parameters must be returned. This is currently impossible
        as they are only available by storing them in a server
        variable and then retrieved by a query. Since stored
        procedures return zero or more result sets, there is no
        reliable way to get at OUT or INOUT parameters via callproc.
        The server variables are named @_procname_n, where procname
        is the parameter above and n is the position of the parameter
        (from zero). Once all result sets generated by the procedure
        have been fetched, you can issue a SELECT @_procname_0, ...
        query using .execute() to get any OUT or INOUT values.

        Compatibility warning: The act of calling a stored procedure
        itself creates an empty result set. This appears after any
        result sets generated by the procedure. This is non-standard
        behavior with respect to the DB-API. Be sure to use nextset()
        to advance through all result sets; otherwise you may get
        disconnected.
        """
        conn = self._get_db()
        for index, arg in enumerate(args):
            q = "SET @_%s_%d=%s" % (procname, index, conn.escape(arg))
            if isinstance(q, unicode):
                q = q.encode(conn.charset)
            self._query(q)
            self.nextset()

        q = "CALL %s(%s)" % (procname,
                             ','.join(['@_%s_%d' % (procname, i)
                                       for i in range(len(args))]))
        if isinstance(q, unicode):
            q = q.encode(conn.charset)
        self._query(q)
        self._executed = q

        return args

    def fetchone(self):
        ''' Fetch the next row '''
        self._check_executed()
        if self._rows is None or self.rownumber >= len(self._rows):
            return None
        result = self._rows[self.rownumber]
        self.rownumber += 1
        return result

    def fetchmany(self, size=None):
        ''' Fetch several rows '''
        self._check_executed()
        end = self.rownumber + (size or self.arraysize)
        result = self._rows[self.rownumber:end]
        if self._rows is None:
            return None
        self.rownumber = min(end, len(self._rows))
        return result

    def fetchall(self):
        ''' Fetch all the rows '''
        self._check_executed()
        if self._rows is None:
            return None
        if self.rownumber:
            result = self._rows[self.rownumber:]
        else:
            result = self._rows
        self.rownumber = len(self._rows)
        return result

    def scroll(self, value, mode='relative'):
        self._check_executed()
        if mode == 'relative':
            r = self.rownumber + value
        elif mode == 'absolute':
            r = value
        else:
            self.errorhandler(self, ProgrammingError,
                    "unknown scroll mode %s" % mode)

        if r < 0 or r >= len(self._rows):
            self.errorhandler(self, IndexError, "out of range")
        self.rownumber = r

    def _query(self, q):
        conn = self._get_db()
        self._last_executed = q
        conn.query(q)
        self._do_get_result()
        return self.rowcount

    def _do_get_result(self):
        conn = self._get_db()
        self.rowcount = conn._result.affected_rows

        self.rownumber = 0
        self.description = conn._result.description
        self.lastrowid = conn._result.insert_id
        self._rows = conn._result.rows
        self._has_next = conn._result.has_next

    def __iter__(self):
        return iter(self.fetchone, None)

    Warning = Warning
    Error = Error
    InterfaceError = InterfaceError
    DatabaseError = DatabaseError
    DataError = DataError
    OperationalError = OperationalError
    IntegrityError = IntegrityError
    InternalError = InternalError
    ProgrammingError = ProgrammingError
    NotSupportedError = NotSupportedError

class DictCursor(Cursor):
    """A cursor which returns results as a dictionary"""

    def execute(self, query, args=None):
        result = super(DictCursor, self).execute(query, args)
        if self.description:
            self._fields = [ field[0] for field in self.description ]
        return result

    def fetchone(self):
        ''' Fetch the next row '''
        self._check_executed()
        if self._rows is None or self.rownumber >= len(self._rows):
            return None
        result = dict(zip(self._fields, self._rows[self.rownumber]))
        self.rownumber += 1
        return result

    def fetchmany(self, size=None):
        ''' Fetch several rows '''
        self._check_executed()
        if self._rows is None:
            return None
        end = self.rownumber + (size or self.arraysize)
        result = [ dict(zip(self._fields, r)) for r in self._rows[self.rownumber:end] ]
        self.rownumber = min(end, len(self._rows))
        return tuple(result)

    def fetchall(self):
        ''' Fetch all the rows '''
        self._check_executed()
        if self._rows is None:
            return None
        if self.rownumber:
            result = [ dict(zip(self._fields, r)) for r in self._rows[self.rownumber:] ]
        else:
            result = [ dict(zip(self._fields, r)) for r in self._rows ]
        self.rownumber = len(self._rows)
        return tuple(result)

