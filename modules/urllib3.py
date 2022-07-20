import os
module_dict={}
module_dict["urllib3"+os.sep+"connectionpool.py"]="""
from __future__ import absolute_import

import errno
import logging
import re
import socket
import sys
import warnings
from socket import error as SocketError
from socket import timeout as SocketTimeout

from .connection import (
    BaseSSLError,
    BrokenPipeError,
    DummyConnection,
    HTTPConnection,
    HTTPException,
    HTTPSConnection,
    VerifiedHTTPSConnection,
    port_by_scheme,
)
from .exceptions import (
    ClosedPoolError,
    EmptyPoolError,
    HeaderParsingError,
    HostChangedError,
    InsecureRequestWarning,
    LocationValueError,
    MaxRetryError,
    NewConnectionError,
    ProtocolError,
    ProxyError,
    ReadTimeoutError,
    SSLError,
    TimeoutError,
)
from .packages import six
from .packages.six.moves import queue
from .request import RequestMethods
from .response import HTTPResponse
from .util.connection import is_connection_dropped
from .util.proxy import connection_requires_http_tunnel
from .util.queue import LifoQueue
from .util.request import set_file_position
from .util.response import assert_header_parsing
from .util.retry import Retry
from .util.ssl_match_hostname import CertificateError
from .util.timeout import Timeout
from .util.url import Url, _encode_target
from .util.url import _normalize_host as normalize_host
from .util.url import get_host, parse_url

xrange = six.moves.xrange

log = logging.getLogger(__name__)

_Default = object()


# Pool objects
class ConnectionPool(object):
    \"\"\"
    Base class for all connection pools, such as
    :class:`.HTTPConnectionPool` and :class:`.HTTPSConnectionPool`.

    .. note::
       ConnectionPool.urlopen() does not normalize or percent-encode target URIs
       which is useful if your target server doesn't support percent-encoded
       target URIs.
    \"\"\"

    scheme = None
    QueueCls = LifoQueue

    def __init__(self, host, port=None):
        if not host:
            raise LocationValueError(\"No host specified.\")

        self.host = _normalize_host(host, scheme=self.scheme)
        self._proxy_host = host.lower()
        self.port = port

    def __str__(self):
        return \"%s(host=%r, port=%r)\" % (type(self).__name__, self.host, self.port)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        # Return False to re-raise any potential exceptions
        return False

    def close(self):
        \"\"\"
        Close all pooled connections and disable the pool.
        \"\"\"
        pass


# This is taken from http://hg.python.org/cpython/file/7aaba721ebc0/Lib/socket.py#l252
_blocking_errnos = {errno.EAGAIN, errno.EWOULDBLOCK}


class HTTPConnectionPool(ConnectionPool, RequestMethods):
    \"\"\"
    Thread-safe connection pool for one host.

    :param host:
        Host used for this HTTP Connection (e.g. \"localhost\"), passed into
        :class:`http.client.HTTPConnection`.

    :param port:
        Port used for this HTTP Connection (None is equivalent to 80), passed
        into :class:`http.client.HTTPConnection`.

    :param strict:
        Causes BadStatusLine to be raised if the status line can't be parsed
        as a valid HTTP/1.0 or 1.1 status line, passed into
        :class:`http.client.HTTPConnection`.

        .. note::
           Only works in Python 2. This parameter is ignored in Python 3.

    :param timeout:
        Socket timeout in seconds for each individual connection. This can
        be a float or integer, which sets the timeout for the HTTP request,
        or an instance of :class:`urllib3.util.Timeout` which gives you more
        fine-grained control over request timeouts. After the constructor has
        been parsed, this is always a `urllib3.util.Timeout` object.

    :param maxsize:
        Number of connections to save that can be reused. More than 1 is useful
        in multithreaded situations. If ``block`` is set to False, more
        connections will be created but they will not be saved once they've
        been used.

    :param block:
        If set to True, no more than ``maxsize`` connections will be used at
        a time. When no free connections are available, the call will block
        until a connection has been released. This is a useful side effect for
        particular multithreaded situations where one does not want to use more
        than maxsize connections per host to prevent flooding.

    :param headers:
        Headers to include with all requests, unless other headers are given
        explicitly.

    :param retries:
        Retry configuration to use by default with requests in this pool.

    :param _proxy:
        Parsed proxy URL, should not be used directly, instead, see
        :class:`urllib3.ProxyManager`

    :param _proxy_headers:
        A dictionary with proxy headers, should not be used directly,
        instead, see :class:`urllib3.ProxyManager`

    :param \\\\**conn_kw:
        Additional parameters are used to create fresh :class:`urllib3.connection.HTTPConnection`,
        :class:`urllib3.connection.HTTPSConnection` instances.
    \"\"\"

    scheme = \"http\"
    ConnectionCls = HTTPConnection
    ResponseCls = HTTPResponse

    def __init__(
        self,
        host,
        port=None,
        strict=False,
        timeout=Timeout.DEFAULT_TIMEOUT,
        maxsize=1,
        block=False,
        headers=None,
        retries=None,
        _proxy=None,
        _proxy_headers=None,
        _proxy_config=None,
        **conn_kw
    ):
        ConnectionPool.__init__(self, host, port)
        RequestMethods.__init__(self, headers)

        self.strict = strict

        if not isinstance(timeout, Timeout):
            timeout = Timeout.from_float(timeout)

        if retries is None:
            retries = Retry.DEFAULT

        self.timeout = timeout
        self.retries = retries

        self.pool = self.QueueCls(maxsize)
        self.block = block

        self.proxy = _proxy
        self.proxy_headers = _proxy_headers or {}
        self.proxy_config = _proxy_config

        # Fill the queue up so that doing get() on it will block properly
        for _ in xrange(maxsize):
            self.pool.put(None)

        # These are mostly for testing and debugging purposes.
        self.num_connections = 0
        self.num_requests = 0
        self.conn_kw = conn_kw

        if self.proxy:
            # Enable Nagle's algorithm for proxies, to avoid packet fragmentation.
            # We cannot know if the user has added default socket options, so we cannot replace the
            # list.
            self.conn_kw.setdefault(\"socket_options\", [])

            self.conn_kw[\"proxy\"] = self.proxy
            self.conn_kw[\"proxy_config\"] = self.proxy_config

    def _new_conn(self):
        \"\"\"
        Return a fresh :class:`HTTPConnection`.
        \"\"\"
        self.num_connections += 1
        log.debug(
            \"Starting new HTTP connection (%d): %s:%s\",
            self.num_connections,
            self.host,
            self.port or \"80\",
        )

        conn = self.ConnectionCls(
            host=self.host,
            port=self.port,
            timeout=self.timeout.connect_timeout,
            strict=self.strict,
            **self.conn_kw
        )
        return conn

    def _get_conn(self, timeout=None):
        \"\"\"
        Get a connection. Will return a pooled connection if one is available.

        If no connections are available and :prop:`.block` is ``False``, then a
        fresh connection is returned.

        :param timeout:
            Seconds to wait before giving up and raising
            :class:`urllib3.exceptions.EmptyPoolError` if the pool is empty and
            :prop:`.block` is ``True``.
        \"\"\"
        conn = None
        try:
            conn = self.pool.get(block=self.block, timeout=timeout)

        except AttributeError:  # self.pool is None
            raise ClosedPoolError(self, \"Pool is closed.\")

        except queue.Empty:
            if self.block:
                raise EmptyPoolError(
                    self,
                    \"Pool reached maximum size and no more connections are allowed.\",
                )
            pass  # Oh well, we'll create a new connection then

        # If this is a persistent connection, check if it got disconnected
        if conn and is_connection_dropped(conn):
            log.debug(\"Resetting dropped connection: %s\", self.host)
            conn.close()
            if getattr(conn, \"auto_open\", 1) == 0:
                # This is a proxied connection that has been mutated by
                # http.client._tunnel() and cannot be reused (since it would
                # attempt to bypass the proxy)
                conn = None

        return conn or self._new_conn()

    def _put_conn(self, conn):
        \"\"\"
        Put a connection back into the pool.

        :param conn:
            Connection object for the current host and port as returned by
            :meth:`._new_conn` or :meth:`._get_conn`.

        If the pool is already full, the connection is closed and discarded
        because we exceeded maxsize. If connections are discarded frequently,
        then maxsize should be increased.

        If the pool is closed, then the connection will be closed and discarded.
        \"\"\"
        try:
            self.pool.put(conn, block=False)
            return  # Everything is dandy, done.
        except AttributeError:
            # self.pool is None.
            pass
        except queue.Full:
            # This should never happen if self.block == True
            log.warning(
                \"Connection pool is full, discarding connection: %s. Connection pool size: %s\",
                self.host,
                self.pool.qsize(),
            )
        # Connection never got put back into the pool, close it.
        if conn:
            conn.close()

    def _validate_conn(self, conn):
        \"\"\"
        Called right before a request is made, after the socket is created.
        \"\"\"
        pass

    def _prepare_proxy(self, conn):
        # Nothing to do for HTTP connections.
        pass

    def _get_timeout(self, timeout):
        \"\"\"Helper that always returns a :class:`urllib3.util.Timeout`\"\"\"
        if timeout is _Default:
            return self.timeout.clone()

        if isinstance(timeout, Timeout):
            return timeout.clone()
        else:
            # User passed us an int/float. This is for backwards compatibility,
            # can be removed later
            return Timeout.from_float(timeout)

    def _raise_timeout(self, err, url, timeout_value):
        \"\"\"Is the error actually a timeout? Will raise a ReadTimeout or pass\"\"\"

        if isinstance(err, SocketTimeout):
            raise ReadTimeoutError(
                self, url, \"Read timed out. (read timeout=%s)\" % timeout_value
            )

        # See the above comment about EAGAIN in Python 3. In Python 2 we have
        # to specifically catch it and throw the timeout error
        if hasattr(err, \"errno\") and err.errno in _blocking_errnos:
            raise ReadTimeoutError(
                self, url, \"Read timed out. (read timeout=%s)\" % timeout_value
            )

        # Catch possible read timeouts thrown as SSL errors. If not the
        # case, rethrow the original. We need to do this because of:
        # http://bugs.python.org/issue10272
        if \"timed out\" in str(err) or \"did not complete (read)\" in str(
            err
        ):  # Python < 2.7.4
            raise ReadTimeoutError(
                self, url, \"Read timed out. (read timeout=%s)\" % timeout_value
            )

    def _make_request(
        self, conn, method, url, timeout=_Default, chunked=False, **httplib_request_kw
    ):
        \"\"\"
        Perform a request on a given urllib connection object taken from our
        pool.

        :param conn:
            a connection from one of our connection pools

        :param timeout:
            Socket timeout in seconds for the request. This can be a
            float or integer, which will set the same timeout value for
            the socket connect and the socket read, or an instance of
            :class:`urllib3.util.Timeout`, which gives you more fine-grained
            control over your timeouts.
        \"\"\"
        self.num_requests += 1

        timeout_obj = self._get_timeout(timeout)
        timeout_obj.start_connect()
        conn.timeout = timeout_obj.connect_timeout

        # Trigger any extra validation we need to do.
        try:
            self._validate_conn(conn)
        except (SocketTimeout, BaseSSLError) as e:
            # Py2 raises this as a BaseSSLError, Py3 raises it as socket timeout.
            self._raise_timeout(err=e, url=url, timeout_value=conn.timeout)
            raise

        # conn.request() calls http.client.*.request, not the method in
        # urllib3.request. It also calls makefile (recv) on the socket.
        try:
            if chunked:
                conn.request_chunked(method, url, **httplib_request_kw)
            else:
                conn.request(method, url, **httplib_request_kw)

        # We are swallowing BrokenPipeError (errno.EPIPE) since the server is
        # legitimately able to close the connection after sending a valid response.
        # With this behaviour, the received response is still readable.
        except BrokenPipeError:
            # Python 3
            pass
        except IOError as e:
            # Python 2 and macOS/Linux
            # EPIPE and ESHUTDOWN are BrokenPipeError on Python 2, and EPROTOTYPE is needed on macOS
            # https://erickt.github.io/blog/2014/11/19/adventures-in-debugging-a-potential-osx-kernel-bug/
            if e.errno not in {
                errno.EPIPE,
                errno.ESHUTDOWN,
                errno.EPROTOTYPE,
            }:
                raise

        # Reset the timeout for the recv() on the socket
        read_timeout = timeout_obj.read_timeout

        # App Engine doesn't have a sock attr
        if getattr(conn, \"sock\", None):
            # In Python 3 socket.py will catch EAGAIN and return None when you
            # try and read into the file pointer created by http.client, which
            # instead raises a BadStatusLine exception. Instead of catching
            # the exception and assuming all BadStatusLine exceptions are read
            # timeouts, check for a zero timeout before making the request.
            if read_timeout == 0:
                raise ReadTimeoutError(
                    self, url, \"Read timed out. (read timeout=%s)\" % read_timeout
                )
            if read_timeout is Timeout.DEFAULT_TIMEOUT:
                conn.sock.settimeout(socket.getdefaulttimeout())
            else:  # None or a value
                conn.sock.settimeout(read_timeout)

        # Receive the response from the server
        try:
            try:
                # Python 2.7, use buffering of HTTP responses
                httplib_response = conn.getresponse(buffering=True)
            except TypeError:
                # Python 3
                try:
                    httplib_response = conn.getresponse()
                except BaseException as e:
                    # Remove the TypeError from the exception chain in
                    # Python 3 (including for exceptions like SystemExit).
                    # Otherwise it looks like a bug in the code.
                    six.raise_from(e, None)
        except (SocketTimeout, BaseSSLError, SocketError) as e:
            self._raise_timeout(err=e, url=url, timeout_value=read_timeout)
            raise

        # AppEngine doesn't have a version attr.
        http_version = getattr(conn, \"_http_vsn_str\", \"HTTP/?\")
        log.debug(
            '%s://%s:%s \"%s %s %s\" %s %s',
            self.scheme,
            self.host,
            self.port,
            method,
            url,
            http_version,
            httplib_response.status,
            httplib_response.length,
        )

        try:
            assert_header_parsing(httplib_response.msg)
        except (HeaderParsingError, TypeError) as hpe:  # Platform-specific: Python 3
            log.warning(
                \"Failed to parse headers (url=%s): %s\",
                self._absolute_url(url),
                hpe,
                exc_info=True,
            )

        return httplib_response

    def _absolute_url(self, path):
        return Url(scheme=self.scheme, host=self.host, port=self.port, path=path).url

    def close(self):
        \"\"\"
        Close all pooled connections and disable the pool.
        \"\"\"
        if self.pool is None:
            return
        # Disable access to the pool
        old_pool, self.pool = self.pool, None

        try:
            while True:
                conn = old_pool.get(block=False)
                if conn:
                    conn.close()

        except queue.Empty:
            pass  # Done.

    def is_same_host(self, url):
        \"\"\"
        Check if the given ``url`` is a member of the same host as this
        connection pool.
        \"\"\"
        if url.startswith(\"/\"):
            return True

        # TODO: Add optional support for socket.gethostbyname checking.
        scheme, host, port = get_host(url)
        if host is not None:
            host = _normalize_host(host, scheme=scheme)

        # Use explicit default port for comparison when none is given
        if self.port and not port:
            port = port_by_scheme.get(scheme)
        elif not self.port and port == port_by_scheme.get(scheme):
            port = None

        return (scheme, host, port) == (self.scheme, self.host, self.port)

    def urlopen(
        self,
        method,
        url,
        body=None,
        headers=None,
        retries=None,
        redirect=True,
        assert_same_host=True,
        timeout=_Default,
        pool_timeout=None,
        release_conn=None,
        chunked=False,
        body_pos=None,
        **response_kw
    ):
        \"\"\"
        Get a connection from the pool and perform an HTTP request. This is the
        lowest level call for making a request, so you'll need to specify all
        the raw details.

        .. note::

           More commonly, it's appropriate to use a convenience method provided
           by :class:`.RequestMethods`, such as :meth:`request`.

        .. note::

           `release_conn` will only behave as expected if
           `preload_content=False` because we want to make
           `preload_content=False` the default behaviour someday soon without
           breaking backwards compatibility.

        :param method:
            HTTP request method (such as GET, POST, PUT, etc.)

        :param url:
            The URL to perform the request on.

        :param body:
            Data to send in the request body, either :class:`str`, :class:`bytes`,
            an iterable of :class:`str`/:class:`bytes`, or a file-like object.

        :param headers:
            Dictionary of custom headers to send, such as User-Agent,
            If-None-Match, etc. If None, pool headers are used. If provided,
            these headers completely replace any pool-specific headers.

        :param retries:
            Configure the number of retries to allow before raising a
            :class:`~urllib3.exceptions.MaxRetryError` exception.

            Pass ``None`` to retry until you receive a response. Pass a
            :class:`~urllib3.util.retry.Retry` object for fine-grained control
            over different types of retries.
            Pass an integer number to retry connection errors that many times,
            but no other types of errors. Pass zero to never retry.

            If ``False``, then retries are disabled and any exception is raised
            immediately. Also, instead of raising a MaxRetryError on redirects,
            the redirect response will be returned.

        :type retries: :class:`~urllib3.util.retry.Retry`, False, or an int.

        :param redirect:
            If True, automatically handle redirects (status codes 301, 302,
            303, 307, 308). Each redirect counts as a retry. Disabling retries
            will disable redirect, too.

        :param assert_same_host:
            If ``True``, will make sure that the host of the pool requests is
            consistent else will raise HostChangedError. When ``False``, you can
            use the pool on an HTTP proxy and request foreign hosts.

        :param timeout:
            If specified, overrides the default timeout for this one
            request. It may be a float (in seconds) or an instance of
            :class:`urllib3.util.Timeout`.

        :param pool_timeout:
            If set and the pool is set to block=True, then this method will
            block for ``pool_timeout`` seconds and raise EmptyPoolError if no
            connection is available within the time period.

        :param release_conn:
            If False, then the urlopen call will not release the connection
            back into the pool once a response is received (but will release if
            you read the entire contents of the response such as when
            `preload_content=True`). This is useful if you're not preloading
            the response's content immediately. You will need to call
            ``r.release_conn()`` on the response ``r`` to return the connection
            back into the pool. If None, it takes the value of
            ``response_kw.get('preload_content', True)``.

        :param chunked:
            If True, urllib3 will send the body using chunked transfer
            encoding. Otherwise, urllib3 will send the body using the standard
            content-length form. Defaults to False.

        :param int body_pos:
            Position to seek to in file-like body in the event of a retry or
            redirect. Typically this won't need to be set because urllib3 will
            auto-populate the value when needed.

        :param \\\\**response_kw:
            Additional parameters are passed to
            :meth:`urllib3.response.HTTPResponse.from_httplib`
        \"\"\"

        parsed_url = parse_url(url)
        destination_scheme = parsed_url.scheme

        if headers is None:
            headers = self.headers

        if not isinstance(retries, Retry):
            retries = Retry.from_int(retries, redirect=redirect, default=self.retries)

        if release_conn is None:
            release_conn = response_kw.get(\"preload_content\", True)

        # Check host
        if assert_same_host and not self.is_same_host(url):
            raise HostChangedError(self, url, retries)

        # Ensure that the URL we're connecting to is properly encoded
        if url.startswith(\"/\"):
            url = six.ensure_str(_encode_target(url))
        else:
            url = six.ensure_str(parsed_url.url)

        conn = None

        # Track whether `conn` needs to be released before
        # returning/raising/recursing. Update this variable if necessary, and
        # leave `release_conn` constant throughout the function. That way, if
        # the function recurses, the original value of `release_conn` will be
        # passed down into the recursive call, and its value will be respected.
        #
        # See issue #651 [1] for details.
        #
        # [1] <https://github.com/urllib3/urllib3/issues/651>
        release_this_conn = release_conn

        http_tunnel_required = connection_requires_http_tunnel(
            self.proxy, self.proxy_config, destination_scheme
        )

        # Merge the proxy headers. Only done when not using HTTP CONNECT. We
        # have to copy the headers dict so we can safely change it without those
        # changes being reflected in anyone else's copy.
        if not http_tunnel_required:
            headers = headers.copy()
            headers.update(self.proxy_headers)

        # Must keep the exception bound to a separate variable or else Python 3
        # complains about UnboundLocalError.
        err = None

        # Keep track of whether we cleanly exited the except block. This
        # ensures we do proper cleanup in finally.
        clean_exit = False

        # Rewind body position, if needed. Record current position
        # for future rewinds in the event of a redirect/retry.
        body_pos = set_file_position(body, body_pos)

        try:
            # Request a connection from the queue.
            timeout_obj = self._get_timeout(timeout)
            conn = self._get_conn(timeout=pool_timeout)

            conn.timeout = timeout_obj.connect_timeout

            is_new_proxy_conn = self.proxy is not None and not getattr(
                conn, \"sock\", None
            )
            if is_new_proxy_conn and http_tunnel_required:
                self._prepare_proxy(conn)

            # Make the request on the httplib connection object.
            httplib_response = self._make_request(
                conn,
                method,
                url,
                timeout=timeout_obj,
                body=body,
                headers=headers,
                chunked=chunked,
            )

            # If we're going to release the connection in ``finally:``, then
            # the response doesn't need to know about the connection. Otherwise
            # it will also try to release it and we'll have a double-release
            # mess.
            response_conn = conn if not release_conn else None

            # Pass method to Response for length checking
            response_kw[\"request_method\"] = method

            # Import httplib's response into our own wrapper object
            response = self.ResponseCls.from_httplib(
                httplib_response,
                pool=self,
                connection=response_conn,
                retries=retries,
                **response_kw
            )

            # Everything went great!
            clean_exit = True

        except EmptyPoolError:
            # Didn't get a connection from the pool, no need to clean up
            clean_exit = True
            release_this_conn = False
            raise

        except (
            TimeoutError,
            HTTPException,
            SocketError,
            ProtocolError,
            BaseSSLError,
            SSLError,
            CertificateError,
        ) as e:
            # Discard the connection for these exceptions. It will be
            # replaced during the next _get_conn() call.
            clean_exit = False

            def _is_ssl_error_message_from_http_proxy(ssl_error):
                # We're trying to detect the message 'WRONG_VERSION_NUMBER' but
                # SSLErrors are kinda all over the place when it comes to the message,
                # so we try to cover our bases here!
                message = \" \".join(re.split(\"[^a-z]\", str(ssl_error).lower()))
                return (
                    \"wrong version number\" in message or \"unknown protocol\" in message
                )

            # Try to detect a common user error with proxies which is to
            # set an HTTP proxy to be HTTPS when it should be 'http://'
            # (ie {'http': 'http://proxy', 'https': 'https://proxy'})
            # Instead we add a nice error message and point to a URL.
            if (
                isinstance(e, BaseSSLError)
                and self.proxy
                and _is_ssl_error_message_from_http_proxy(e)
                and conn.proxy
                and conn.proxy.scheme == \"https\"
            ):
                e = ProxyError(
                    \"Your proxy appears to only use HTTP and not HTTPS, \"
                    \"try changing your proxy URL to be HTTP. See: \"
                    \"https://urllib3.readthedocs.io/en/1.26.x/advanced-usage.html\"
                    \"#https-proxy-error-http-proxy\",
                    SSLError(e),
                )
            elif isinstance(e, (BaseSSLError, CertificateError)):
                e = SSLError(e)
            elif isinstance(e, (SocketError, NewConnectionError)) and self.proxy:
                e = ProxyError(\"Cannot connect to proxy.\", e)
            elif isinstance(e, (SocketError, HTTPException)):
                e = ProtocolError(\"Connection aborted.\", e)

            retries = retries.increment(
                method, url, error=e, _pool=self, _stacktrace=sys.exc_info()[2]
            )
            retries.sleep()

            # Keep track of the error for the retry warning.
            err = e

        finally:
            if not clean_exit:
                # We hit some kind of exception, handled or otherwise. We need
                # to throw the connection away unless explicitly told not to.
                # Close the connection, set the variable to None, and make sure
                # we put the None back in the pool to avoid leaking it.
                conn = conn and conn.close()
                release_this_conn = True

            if release_this_conn:
                # Put the connection back to be reused. If the connection is
                # expired then it will be None, which will get replaced with a
                # fresh connection during _get_conn.
                self._put_conn(conn)

        if not conn:
            # Try again
            log.warning(
                \"Retrying (%r) after connection broken by '%r': %s\", retries, err, url
            )
            return self.urlopen(
                method,
                url,
                body,
                headers,
                retries,
                redirect,
                assert_same_host,
                timeout=timeout,
                pool_timeout=pool_timeout,
                release_conn=release_conn,
                chunked=chunked,
                body_pos=body_pos,
                **response_kw
            )

        # Handle redirect?
        redirect_location = redirect and response.get_redirect_location()
        if redirect_location:
            if response.status == 303:
                method = \"GET\"

            try:
                retries = retries.increment(method, url, response=response, _pool=self)
            except MaxRetryError:
                if retries.raise_on_redirect:
                    response.drain_conn()
                    raise
                return response

            response.drain_conn()
            retries.sleep_for_retry(response)
            log.debug(\"Redirecting %s -> %s\", url, redirect_location)
            return self.urlopen(
                method,
                redirect_location,
                body,
                headers,
                retries=retries,
                redirect=redirect,
                assert_same_host=assert_same_host,
                timeout=timeout,
                pool_timeout=pool_timeout,
                release_conn=release_conn,
                chunked=chunked,
                body_pos=body_pos,
                **response_kw
            )

        # Check if we should retry the HTTP response.
        has_retry_after = bool(response.getheader(\"Retry-After\"))
        if retries.is_retry(method, response.status, has_retry_after):
            try:
                retries = retries.increment(method, url, response=response, _pool=self)
            except MaxRetryError:
                if retries.raise_on_status:
                    response.drain_conn()
                    raise
                return response

            response.drain_conn()
            retries.sleep(response)
            log.debug(\"Retry: %s\", url)
            return self.urlopen(
                method,
                url,
                body,
                headers,
                retries=retries,
                redirect=redirect,
                assert_same_host=assert_same_host,
                timeout=timeout,
                pool_timeout=pool_timeout,
                release_conn=release_conn,
                chunked=chunked,
                body_pos=body_pos,
                **response_kw
            )

        return response


class HTTPSConnectionPool(HTTPConnectionPool):
    \"\"\"
    Same as :class:`.HTTPConnectionPool`, but HTTPS.

    :class:`.HTTPSConnection` uses one of ``assert_fingerprint``,
    ``assert_hostname`` and ``host`` in this order to verify connections.
    If ``assert_hostname`` is False, no verification is done.

    The ``key_file``, ``cert_file``, ``cert_reqs``, ``ca_certs``,
    ``ca_cert_dir``, ``ssl_version``, ``key_password`` are only used if :mod:`ssl`
    is available and are fed into :meth:`urllib3.util.ssl_wrap_socket` to upgrade
    the connection socket into an SSL socket.
    \"\"\"

    scheme = \"https\"
    ConnectionCls = HTTPSConnection

    def __init__(
        self,
        host,
        port=None,
        strict=False,
        timeout=Timeout.DEFAULT_TIMEOUT,
        maxsize=1,
        block=False,
        headers=None,
        retries=None,
        _proxy=None,
        _proxy_headers=None,
        key_file=None,
        cert_file=None,
        cert_reqs=None,
        key_password=None,
        ca_certs=None,
        ssl_version=None,
        assert_hostname=None,
        assert_fingerprint=None,
        ca_cert_dir=None,
        **conn_kw
    ):

        HTTPConnectionPool.__init__(
            self,
            host,
            port,
            strict,
            timeout,
            maxsize,
            block,
            headers,
            retries,
            _proxy,
            _proxy_headers,
            **conn_kw
        )

        self.key_file = key_file
        self.cert_file = cert_file
        self.cert_reqs = cert_reqs
        self.key_password = key_password
        self.ca_certs = ca_certs
        self.ca_cert_dir = ca_cert_dir
        self.ssl_version = ssl_version
        self.assert_hostname = assert_hostname
        self.assert_fingerprint = assert_fingerprint

    def _prepare_conn(self, conn):
        \"\"\"
        Prepare the ``connection`` for :meth:`urllib3.util.ssl_wrap_socket`
        and establish the tunnel if proxy is used.
        \"\"\"

        if isinstance(conn, VerifiedHTTPSConnection):
            conn.set_cert(
                key_file=self.key_file,
                key_password=self.key_password,
                cert_file=self.cert_file,
                cert_reqs=self.cert_reqs,
                ca_certs=self.ca_certs,
                ca_cert_dir=self.ca_cert_dir,
                assert_hostname=self.assert_hostname,
                assert_fingerprint=self.assert_fingerprint,
            )
            conn.ssl_version = self.ssl_version
        return conn

    def _prepare_proxy(self, conn):
        \"\"\"
        Establishes a tunnel connection through HTTP CONNECT.

        Tunnel connection is established early because otherwise httplib would
        improperly set Host: header to proxy's IP:port.
        \"\"\"

        conn.set_tunnel(self._proxy_host, self.port, self.proxy_headers)

        if self.proxy.scheme == \"https\":
            conn.tls_in_tls_required = True

        conn.connect()

    def _new_conn(self):
        \"\"\"
        Return a fresh :class:`http.client.HTTPSConnection`.
        \"\"\"
        self.num_connections += 1
        log.debug(
            \"Starting new HTTPS connection (%d): %s:%s\",
            self.num_connections,
            self.host,
            self.port or \"443\",
        )

        if not self.ConnectionCls or self.ConnectionCls is DummyConnection:
            raise SSLError(
                \"Can't connect to HTTPS URL because the SSL module is not available.\"
            )

        actual_host = self.host
        actual_port = self.port
        if self.proxy is not None:
            actual_host = self.proxy.host
            actual_port = self.proxy.port

        conn = self.ConnectionCls(
            host=actual_host,
            port=actual_port,
            timeout=self.timeout.connect_timeout,
            strict=self.strict,
            cert_file=self.cert_file,
            key_file=self.key_file,
            key_password=self.key_password,
            **self.conn_kw
        )

        return self._prepare_conn(conn)

    def _validate_conn(self, conn):
        \"\"\"
        Called right before a request is made, after the socket is created.
        \"\"\"
        super(HTTPSConnectionPool, self)._validate_conn(conn)

        # Force connect early to allow us to validate the connection.
        if not getattr(conn, \"sock\", None):  # AppEngine might not have  `.sock`
            conn.connect()

        if not conn.is_verified:
            warnings.warn(
                (
                    \"Unverified HTTPS request is being made to host '%s'. \"
                    \"Adding certificate verification is strongly advised. See: \"
                    \"https://urllib3.readthedocs.io/en/1.26.x/advanced-usage.html\"
                    \"#ssl-warnings\" % conn.host
                ),
                InsecureRequestWarning,
            )

        if getattr(conn, \"proxy_is_verified\", None) is False:
            warnings.warn(
                (
                    \"Unverified HTTPS connection done to an HTTPS proxy. \"
                    \"Adding certificate verification is strongly advised. See: \"
                    \"https://urllib3.readthedocs.io/en/1.26.x/advanced-usage.html\"
                    \"#ssl-warnings\"
                ),
                InsecureRequestWarning,
            )


def connection_from_url(url, **kw):
    \"\"\"
    Given a url, return an :class:`.ConnectionPool` instance of its host.

    This is a shortcut for not having to parse out the scheme, host, and port
    of the url before creating an :class:`.ConnectionPool` instance.

    :param url:
        Absolute URL string that must include the scheme. Port is optional.

    :param \\\\**kw:
        Passes additional parameters to the constructor of the appropriate
        :class:`.ConnectionPool`. Useful for specifying things like
        timeout, maxsize, headers, etc.

    Example::

        >>> conn = connection_from_url('http://google.com/')
        >>> r = conn.request('GET', '/')
    \"\"\"
    scheme, host, port = get_host(url)
    port = port or port_by_scheme.get(scheme, 80)
    if scheme == \"https\":
        return HTTPSConnectionPool(host, port=port, **kw)
    else:
        return HTTPConnectionPool(host, port=port, **kw)


def _normalize_host(host, scheme):
    \"\"\"
    Normalize hosts for comparisons and use with sockets.
    \"\"\"

    host = normalize_host(host, scheme)

    # httplib doesn't like it when we include brackets in IPv6 addresses
    # Specifically, if we include brackets but also pass the port then
    # httplib crazily doubles up the square brackets on the Host header.
    # Instead, we need to make sure we never pass ``None`` as the port.
    # However, for backward compatibility reasons we can't actually
    # *assert* that.  See http://bugs.python.org/issue28539
    if host.startswith(\"[\") and host.endswith(\"]\"):
        host = host[1:-1]
    return host

"""
module_dict["urllib3"+os.sep+"_collections.py"]="""
from __future__ import absolute_import

try:
    from collections.abc import Mapping, MutableMapping
except ImportError:
    from collections import Mapping, MutableMapping
try:
    from threading import RLock
except ImportError:  # Platform-specific: No threads available

    class RLock:
        def __enter__(self):
            pass

        def __exit__(self, exc_type, exc_value, traceback):
            pass


from collections import OrderedDict

from .exceptions import InvalidHeader
from .packages import six
from .packages.six import iterkeys, itervalues

__all__ = [\"RecentlyUsedContainer\", \"HTTPHeaderDict\"]


_Null = object()


class RecentlyUsedContainer(MutableMapping):
    \"\"\"
    Provides a thread-safe dict-like container which maintains up to
    ``maxsize`` keys while throwing away the least-recently-used keys beyond
    ``maxsize``.

    :param maxsize:
        Maximum number of recent elements to retain.

    :param dispose_func:
        Every time an item is evicted from the container,
        ``dispose_func(value)`` is called.  Callback which will get called
    \"\"\"

    ContainerCls = OrderedDict

    def __init__(self, maxsize=10, dispose_func=None):
        self._maxsize = maxsize
        self.dispose_func = dispose_func

        self._container = self.ContainerCls()
        self.lock = RLock()

    def __getitem__(self, key):
        # Re-insert the item, moving it to the end of the eviction line.
        with self.lock:
            item = self._container.pop(key)
            self._container[key] = item
            return item

    def __setitem__(self, key, value):
        evicted_value = _Null
        with self.lock:
            # Possibly evict the existing value of 'key'
            evicted_value = self._container.get(key, _Null)
            self._container[key] = value

            # If we didn't evict an existing value, we might have to evict the
            # least recently used item from the beginning of the container.
            if len(self._container) > self._maxsize:
                _key, evicted_value = self._container.popitem(last=False)

        if self.dispose_func and evicted_value is not _Null:
            self.dispose_func(evicted_value)

    def __delitem__(self, key):
        with self.lock:
            value = self._container.pop(key)

        if self.dispose_func:
            self.dispose_func(value)

    def __len__(self):
        with self.lock:
            return len(self._container)

    def __iter__(self):
        raise NotImplementedError(
            \"Iteration over this class is unlikely to be threadsafe.\"
        )

    def clear(self):
        with self.lock:
            # Copy pointers to all values, then wipe the mapping
            values = list(itervalues(self._container))
            self._container.clear()

        if self.dispose_func:
            for value in values:
                self.dispose_func(value)

    def keys(self):
        with self.lock:
            return list(iterkeys(self._container))


class HTTPHeaderDict(MutableMapping):
    \"\"\"
    :param headers:
        An iterable of field-value pairs. Must not contain multiple field names
        when compared case-insensitively.

    :param kwargs:
        Additional field-value pairs to pass in to ``dict.update``.

    A ``dict`` like container for storing HTTP Headers.

    Field names are stored and compared case-insensitively in compliance with
    RFC 7230. Iteration provides the first case-sensitive key seen for each
    case-insensitive pair.

    Using ``__setitem__`` syntax overwrites fields that compare equal
    case-insensitively in order to maintain ``dict``'s api. For fields that
    compare equal, instead create a new ``HTTPHeaderDict`` and use ``.add``
    in a loop.

    If multiple fields that are equal case-insensitively are passed to the
    constructor or ``.update``, the behavior is undefined and some will be
    lost.

    >>> headers = HTTPHeaderDict()
    >>> headers.add('Set-Cookie', 'foo=bar')
    >>> headers.add('set-cookie', 'baz=quxx')
    >>> headers['content-length'] = '7'
    >>> headers['SET-cookie']
    'foo=bar, baz=quxx'
    >>> headers['Content-Length']
    '7'
    \"\"\"

    def __init__(self, headers=None, **kwargs):
        super(HTTPHeaderDict, self).__init__()
        self._container = OrderedDict()
        if headers is not None:
            if isinstance(headers, HTTPHeaderDict):
                self._copy_from(headers)
            else:
                self.extend(headers)
        if kwargs:
            self.extend(kwargs)

    def __setitem__(self, key, val):
        self._container[key.lower()] = [key, val]
        return self._container[key.lower()]

    def __getitem__(self, key):
        val = self._container[key.lower()]
        return \", \".join(val[1:])

    def __delitem__(self, key):
        del self._container[key.lower()]

    def __contains__(self, key):
        return key.lower() in self._container

    def __eq__(self, other):
        if not isinstance(other, Mapping) and not hasattr(other, \"keys\"):
            return False
        if not isinstance(other, type(self)):
            other = type(self)(other)
        return dict((k.lower(), v) for k, v in self.itermerged()) == dict(
            (k.lower(), v) for k, v in other.itermerged()
        )

    def __ne__(self, other):
        return not self.__eq__(other)

    if six.PY2:  # Python 2
        iterkeys = MutableMapping.iterkeys
        itervalues = MutableMapping.itervalues

    __marker = object()

    def __len__(self):
        return len(self._container)

    def __iter__(self):
        # Only provide the originally cased names
        for vals in self._container.values():
            yield vals[0]

    def pop(self, key, default=__marker):
        \"\"\"D.pop(k[,d]) -> v, remove specified key and return the corresponding value.
        If key is not found, d is returned if given, otherwise KeyError is raised.
        \"\"\"
        # Using the MutableMapping function directly fails due to the private marker.
        # Using ordinary dict.pop would expose the internal structures.
        # So let's reinvent the wheel.
        try:
            value = self[key]
        except KeyError:
            if default is self.__marker:
                raise
            return default
        else:
            del self[key]
            return value

    def discard(self, key):
        try:
            del self[key]
        except KeyError:
            pass

    def add(self, key, val):
        \"\"\"Adds a (name, value) pair, doesn't overwrite the value if it already
        exists.

        >>> headers = HTTPHeaderDict(foo='bar')
        >>> headers.add('Foo', 'baz')
        >>> headers['foo']
        'bar, baz'
        \"\"\"
        key_lower = key.lower()
        new_vals = [key, val]
        # Keep the common case aka no item present as fast as possible
        vals = self._container.setdefault(key_lower, new_vals)
        if new_vals is not vals:
            vals.append(val)

    def extend(self, *args, **kwargs):
        \"\"\"Generic import function for any type of header-like object.
        Adapted version of MutableMapping.update in order to insert items
        with self.add instead of self.__setitem__
        \"\"\"
        if len(args) > 1:
            raise TypeError(
                \"extend() takes at most 1 positional \"
                \"arguments ({0} given)\".format(len(args))
            )
        other = args[0] if len(args) >= 1 else ()

        if isinstance(other, HTTPHeaderDict):
            for key, val in other.iteritems():
                self.add(key, val)
        elif isinstance(other, Mapping):
            for key in other:
                self.add(key, other[key])
        elif hasattr(other, \"keys\"):
            for key in other.keys():
                self.add(key, other[key])
        else:
            for key, value in other:
                self.add(key, value)

        for key, value in kwargs.items():
            self.add(key, value)

    def getlist(self, key, default=__marker):
        \"\"\"Returns a list of all the values for the named field. Returns an
        empty list if the key doesn't exist.\"\"\"
        try:
            vals = self._container[key.lower()]
        except KeyError:
            if default is self.__marker:
                return []
            return default
        else:
            return vals[1:]

    # Backwards compatibility for httplib
    getheaders = getlist
    getallmatchingheaders = getlist
    iget = getlist

    # Backwards compatibility for http.cookiejar
    get_all = getlist

    def __repr__(self):
        return \"%s(%s)\" % (type(self).__name__, dict(self.itermerged()))

    def _copy_from(self, other):
        for key in other:
            val = other.getlist(key)
            if isinstance(val, list):
                # Don't need to convert tuples
                val = list(val)
            self._container[key.lower()] = [key] + val

    def copy(self):
        clone = type(self)()
        clone._copy_from(self)
        return clone

    def iteritems(self):
        \"\"\"Iterate over all header lines, including duplicate ones.\"\"\"
        for key in self:
            vals = self._container[key.lower()]
            for val in vals[1:]:
                yield vals[0], val

    def itermerged(self):
        \"\"\"Iterate over all headers, merging duplicate ones together.\"\"\"
        for key in self:
            val = self._container[key.lower()]
            yield val[0], \", \".join(val[1:])

    def items(self):
        return list(self.iteritems())

    @classmethod
    def from_httplib(cls, message):  # Python 2
        \"\"\"Read headers from a Python 2 httplib message object.\"\"\"
        # python2.7 does not expose a proper API for exporting multiheaders
        # efficiently. This function re-reads raw lines from the message
        # object and extracts the multiheaders properly.
        obs_fold_continued_leaders = (\" \", \"\\t\")
        headers = []

        for line in message.headers:
            if line.startswith(obs_fold_continued_leaders):
                if not headers:
                    # We received a header line that starts with OWS as described
                    # in RFC-7230 S3.2.4. This indicates a multiline header, but
                    # there exists no previous header to which we can attach it.
                    raise InvalidHeader(
                        \"Header continuation with no previous header: %s\" % line
                    )
                else:
                    key, value = headers[-1]
                    headers[-1] = (key, value + \" \" + line.strip())
                    continue

            key, value = line.split(\":\", 1)
            headers.append((key, value.strip()))

        return cls(headers)

"""
module_dict["urllib3"+os.sep+"connection.py"]="""
from __future__ import absolute_import

import datetime
import logging
import os
import re
import socket
import warnings
from socket import error as SocketError
from socket import timeout as SocketTimeout

from .packages import six
from .packages.six.moves.http_client import HTTPConnection as _HTTPConnection
from .packages.six.moves.http_client import HTTPException  # noqa: F401
from .util.proxy import create_proxy_ssl_context

try:  # Compiled with SSL?
    import ssl

    BaseSSLError = ssl.SSLError
except (ImportError, AttributeError):  # Platform-specific: No SSL.
    ssl = None

    class BaseSSLError(BaseException):
        pass


try:
    # Python 3: not a no-op, we're adding this to the namespace so it can be imported.
    ConnectionError = ConnectionError
except NameError:
    # Python 2
    class ConnectionError(Exception):
        pass


try:  # Python 3:
    # Not a no-op, we're adding this to the namespace so it can be imported.
    BrokenPipeError = BrokenPipeError
except NameError:  # Python 2:

    class BrokenPipeError(Exception):
        pass


from ._collections import HTTPHeaderDict  # noqa (historical, removed in v2)
from ._version import __version__
from .exceptions import (
    ConnectTimeoutError,
    NewConnectionError,
    SubjectAltNameWarning,
    SystemTimeWarning,
)
from .util import SKIP_HEADER, SKIPPABLE_HEADERS, connection
from .util.ssl_ import (
    assert_fingerprint,
    create_urllib3_context,
    is_ipaddress,
    resolve_cert_reqs,
    resolve_ssl_version,
    ssl_wrap_socket,
)
from .util.ssl_match_hostname import CertificateError, match_hostname

log = logging.getLogger(__name__)

port_by_scheme = {\"http\": 80, \"https\": 443}

# When it comes time to update this value as a part of regular maintenance
# (ie test_recent_date is failing) update it to ~6 months before the current date.
RECENT_DATE = datetime.date(2022, 1, 1)

_CONTAINS_CONTROL_CHAR_RE = re.compile(r\"[^-!#$%&'*+.^_`|~0-9a-zA-Z]\")


class HTTPConnection(_HTTPConnection, object):
    \"\"\"
    Based on :class:`http.client.HTTPConnection` but provides an extra constructor
    backwards-compatibility layer between older and newer Pythons.

    Additional keyword parameters are used to configure attributes of the connection.
    Accepted parameters include:

    - ``strict``: See the documentation on :class:`urllib3.connectionpool.HTTPConnectionPool`
    - ``source_address``: Set the source address for the current connection.
    - ``socket_options``: Set specific options on the underlying socket. If not specified, then
      defaults are loaded from ``HTTPConnection.default_socket_options`` which includes disabling
      Nagle's algorithm (sets TCP_NODELAY to 1) unless the connection is behind a proxy.

      For example, if you wish to enable TCP Keep Alive in addition to the defaults,
      you might pass:

      .. code-block:: python

         HTTPConnection.default_socket_options + [
             (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1),
         ]

      Or you may want to disable the defaults by passing an empty list (e.g., ``[]``).
    \"\"\"

    default_port = port_by_scheme[\"http\"]

    #: Disable Nagle's algorithm by default.
    #: ``[(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)]``
    default_socket_options = [(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)]

    #: Whether this connection verifies the host's certificate.
    is_verified = False

    #: Whether this proxy connection (if used) verifies the proxy host's
    #: certificate.
    proxy_is_verified = None

    def __init__(self, *args, **kw):
        if not six.PY2:
            kw.pop(\"strict\", None)

        # Pre-set source_address.
        self.source_address = kw.get(\"source_address\")

        #: The socket options provided by the user. If no options are
        #: provided, we use the default options.
        self.socket_options = kw.pop(\"socket_options\", self.default_socket_options)

        # Proxy options provided by the user.
        self.proxy = kw.pop(\"proxy\", None)
        self.proxy_config = kw.pop(\"proxy_config\", None)

        _HTTPConnection.__init__(self, *args, **kw)

    @property
    def host(self):
        \"\"\"
        Getter method to remove any trailing dots that indicate the hostname is an FQDN.

        In general, SSL certificates don't include the trailing dot indicating a
        fully-qualified domain name, and thus, they don't validate properly when
        checked against a domain name that includes the dot. In addition, some
        servers may not expect to receive the trailing dot when provided.

        However, the hostname with trailing dot is critical to DNS resolution; doing a
        lookup with the trailing dot will properly only resolve the appropriate FQDN,
        whereas a lookup without a trailing dot will search the system's search domain
        list. Thus, it's important to keep the original host around for use only in
        those cases where it's appropriate (i.e., when doing DNS lookup to establish the
        actual TCP connection across which we're going to send HTTP requests).
        \"\"\"
        return self._dns_host.rstrip(\".\")

    @host.setter
    def host(self, value):
        \"\"\"
        Setter for the `host` property.

        We assume that only urllib3 uses the _dns_host attribute; httplib itself
        only uses `host`, and it seems reasonable that other libraries follow suit.
        \"\"\"
        self._dns_host = value

    def _new_conn(self):
        \"\"\"Establish a socket connection and set nodelay settings on it.

        :return: New socket connection.
        \"\"\"
        extra_kw = {}
        if self.source_address:
            extra_kw[\"source_address\"] = self.source_address

        if self.socket_options:
            extra_kw[\"socket_options\"] = self.socket_options

        try:
            conn = connection.create_connection(
                (self._dns_host, self.port), self.timeout, **extra_kw
            )

        except SocketTimeout:
            raise ConnectTimeoutError(
                self,
                \"Connection to %s timed out. (connect timeout=%s)\"
                % (self.host, self.timeout),
            )

        except SocketError as e:
            raise NewConnectionError(
                self, \"Failed to establish a new connection: %s\" % e
            )

        return conn

    def _is_using_tunnel(self):
        # Google App Engine's httplib does not define _tunnel_host
        return getattr(self, \"_tunnel_host\", None)

    def _prepare_conn(self, conn):
        self.sock = conn
        if self._is_using_tunnel():
            # TODO: Fix tunnel so it doesn't depend on self.sock state.
            self._tunnel()
            # Mark this connection as not reusable
            self.auto_open = 0

    def connect(self):
        conn = self._new_conn()
        self._prepare_conn(conn)

    def putrequest(self, method, url, *args, **kwargs):
        \"\"\" \"\"\"
        # Empty docstring because the indentation of CPython's implementation
        # is broken but we don't want this method in our documentation.
        match = _CONTAINS_CONTROL_CHAR_RE.search(method)
        if match:
            raise ValueError(
                \"Method cannot contain non-token characters %r (found at least %r)\"
                % (method, match.group())
            )

        return _HTTPConnection.putrequest(self, method, url, *args, **kwargs)

    def putheader(self, header, *values):
        \"\"\" \"\"\"
        if not any(isinstance(v, str) and v == SKIP_HEADER for v in values):
            _HTTPConnection.putheader(self, header, *values)
        elif six.ensure_str(header.lower()) not in SKIPPABLE_HEADERS:
            raise ValueError(
                \"urllib3.util.SKIP_HEADER only supports '%s'\"
                % (\"', '\".join(map(str.title, sorted(SKIPPABLE_HEADERS))),)
            )

    def request(self, method, url, body=None, headers=None):
        if headers is None:
            headers = {}
        else:
            # Avoid modifying the headers passed into .request()
            headers = headers.copy()
        if \"user-agent\" not in (six.ensure_str(k.lower()) for k in headers):
            headers[\"User-Agent\"] = _get_default_user_agent()
        super(HTTPConnection, self).request(method, url, body=body, headers=headers)

    def request_chunked(self, method, url, body=None, headers=None):
        \"\"\"
        Alternative to the common request method, which sends the
        body with chunked encoding and not as one block
        \"\"\"
        headers = headers or {}
        header_keys = set([six.ensure_str(k.lower()) for k in headers])
        skip_accept_encoding = \"accept-encoding\" in header_keys
        skip_host = \"host\" in header_keys
        self.putrequest(
            method, url, skip_accept_encoding=skip_accept_encoding, skip_host=skip_host
        )
        if \"user-agent\" not in header_keys:
            self.putheader(\"User-Agent\", _get_default_user_agent())
        for header, value in headers.items():
            self.putheader(header, value)
        if \"transfer-encoding\" not in header_keys:
            self.putheader(\"Transfer-Encoding\", \"chunked\")
        self.endheaders()

        if body is not None:
            stringish_types = six.string_types + (bytes,)
            if isinstance(body, stringish_types):
                body = (body,)
            for chunk in body:
                if not chunk:
                    continue
                if not isinstance(chunk, bytes):
                    chunk = chunk.encode(\"utf8\")
                len_str = hex(len(chunk))[2:]
                to_send = bytearray(len_str.encode())
                to_send += b\"\\r\\n\"
                to_send += chunk
                to_send += b\"\\r\\n\"
                self.send(to_send)

        # After the if clause, to always have a closed body
        self.send(b\"0\\r\\n\\r\\n\")


class HTTPSConnection(HTTPConnection):
    \"\"\"
    Many of the parameters to this constructor are passed to the underlying SSL
    socket by means of :py:func:`urllib3.util.ssl_wrap_socket`.
    \"\"\"

    default_port = port_by_scheme[\"https\"]

    cert_reqs = None
    ca_certs = None
    ca_cert_dir = None
    ca_cert_data = None
    ssl_version = None
    assert_fingerprint = None
    tls_in_tls_required = False

    def __init__(
        self,
        host,
        port=None,
        key_file=None,
        cert_file=None,
        key_password=None,
        strict=None,
        timeout=socket._GLOBAL_DEFAULT_TIMEOUT,
        ssl_context=None,
        server_hostname=None,
        **kw
    ):

        HTTPConnection.__init__(self, host, port, strict=strict, timeout=timeout, **kw)

        self.key_file = key_file
        self.cert_file = cert_file
        self.key_password = key_password
        self.ssl_context = ssl_context
        self.server_hostname = server_hostname

        # Required property for Google AppEngine 1.9.0 which otherwise causes
        # HTTPS requests to go out as HTTP. (See Issue #356)
        self._protocol = \"https\"

    def set_cert(
        self,
        key_file=None,
        cert_file=None,
        cert_reqs=None,
        key_password=None,
        ca_certs=None,
        assert_hostname=None,
        assert_fingerprint=None,
        ca_cert_dir=None,
        ca_cert_data=None,
    ):
        \"\"\"
        This method should only be called once, before the connection is used.
        \"\"\"
        # If cert_reqs is not provided we'll assume CERT_REQUIRED unless we also
        # have an SSLContext object in which case we'll use its verify_mode.
        if cert_reqs is None:
            if self.ssl_context is not None:
                cert_reqs = self.ssl_context.verify_mode
            else:
                cert_reqs = resolve_cert_reqs(None)

        self.key_file = key_file
        self.cert_file = cert_file
        self.cert_reqs = cert_reqs
        self.key_password = key_password
        self.assert_hostname = assert_hostname
        self.assert_fingerprint = assert_fingerprint
        self.ca_certs = ca_certs and os.path.expanduser(ca_certs)
        self.ca_cert_dir = ca_cert_dir and os.path.expanduser(ca_cert_dir)
        self.ca_cert_data = ca_cert_data

    def connect(self):
        # Add certificate verification
        self.sock = conn = self._new_conn()
        hostname = self.host
        tls_in_tls = False

        if self._is_using_tunnel():
            if self.tls_in_tls_required:
                self.sock = conn = self._connect_tls_proxy(hostname, conn)
                tls_in_tls = True

            # Calls self._set_hostport(), so self.host is
            # self._tunnel_host below.
            self._tunnel()
            # Mark this connection as not reusable
            self.auto_open = 0

            # Override the host with the one we're requesting data from.
            hostname = self._tunnel_host

        server_hostname = hostname
        if self.server_hostname is not None:
            server_hostname = self.server_hostname

        is_time_off = datetime.date.today() < RECENT_DATE
        if is_time_off:
            warnings.warn(
                (
                    \"System time is way off (before {0}). This will probably \"
                    \"lead to SSL verification errors\"
                ).format(RECENT_DATE),
                SystemTimeWarning,
            )

        # Wrap socket using verification with the root certs in
        # trusted_root_certs
        default_ssl_context = False
        if self.ssl_context is None:
            default_ssl_context = True
            self.ssl_context = create_urllib3_context(
                ssl_version=resolve_ssl_version(self.ssl_version),
                cert_reqs=resolve_cert_reqs(self.cert_reqs),
            )

        context = self.ssl_context
        context.verify_mode = resolve_cert_reqs(self.cert_reqs)

        # Try to load OS default certs if none are given.
        # Works well on Windows (requires Python3.4+)
        if (
            not self.ca_certs
            and not self.ca_cert_dir
            and not self.ca_cert_data
            and default_ssl_context
            and hasattr(context, \"load_default_certs\")
        ):
            context.load_default_certs()

        self.sock = ssl_wrap_socket(
            sock=conn,
            keyfile=self.key_file,
            certfile=self.cert_file,
            key_password=self.key_password,
            ca_certs=self.ca_certs,
            ca_cert_dir=self.ca_cert_dir,
            ca_cert_data=self.ca_cert_data,
            server_hostname=server_hostname,
            ssl_context=context,
            tls_in_tls=tls_in_tls,
        )

        # If we're using all defaults and the connection
        # is TLSv1 or TLSv1.1 we throw a DeprecationWarning
        # for the host.
        if (
            default_ssl_context
            and self.ssl_version is None
            and hasattr(self.sock, \"version\")
            and self.sock.version() in {\"TLSv1\", \"TLSv1.1\"}
        ):
            warnings.warn(
                \"Negotiating TLSv1/TLSv1.1 by default is deprecated \"
                \"and will be disabled in urllib3 v2.0.0. Connecting to \"
                \"'%s' with '%s' can be enabled by explicitly opting-in \"
                \"with 'ssl_version'\" % (self.host, self.sock.version()),
                DeprecationWarning,
            )

        if self.assert_fingerprint:
            assert_fingerprint(
                self.sock.getpeercert(binary_form=True), self.assert_fingerprint
            )
        elif (
            context.verify_mode != ssl.CERT_NONE
            and not getattr(context, \"check_hostname\", False)
            and self.assert_hostname is not False
        ):
            # While urllib3 attempts to always turn off hostname matching from
            # the TLS library, this cannot always be done. So we check whether
            # the TLS Library still thinks it's matching hostnames.
            cert = self.sock.getpeercert()
            if not cert.get(\"subjectAltName\", ()):
                warnings.warn(
                    (
                        \"Certificate for {0} has no `subjectAltName`, falling back to check for a \"
                        \"`commonName` for now. This feature is being removed by major browsers and \"
                        \"deprecated by RFC 2818. (See https://github.com/urllib3/urllib3/issues/497 \"
                        \"for details.)\".format(hostname)
                    ),
                    SubjectAltNameWarning,
                )
            _match_hostname(cert, self.assert_hostname or server_hostname)

        self.is_verified = (
            context.verify_mode == ssl.CERT_REQUIRED
            or self.assert_fingerprint is not None
        )

    def _connect_tls_proxy(self, hostname, conn):
        \"\"\"
        Establish a TLS connection to the proxy using the provided SSL context.
        \"\"\"
        proxy_config = self.proxy_config
        ssl_context = proxy_config.ssl_context
        if ssl_context:
            # If the user provided a proxy context, we assume CA and client
            # certificates have already been set
            return ssl_wrap_socket(
                sock=conn,
                server_hostname=hostname,
                ssl_context=ssl_context,
            )

        ssl_context = create_proxy_ssl_context(
            self.ssl_version,
            self.cert_reqs,
            self.ca_certs,
            self.ca_cert_dir,
            self.ca_cert_data,
        )

        # If no cert was provided, use only the default options for server
        # certificate validation
        socket = ssl_wrap_socket(
            sock=conn,
            ca_certs=self.ca_certs,
            ca_cert_dir=self.ca_cert_dir,
            ca_cert_data=self.ca_cert_data,
            server_hostname=hostname,
            ssl_context=ssl_context,
        )

        if ssl_context.verify_mode != ssl.CERT_NONE and not getattr(
            ssl_context, \"check_hostname\", False
        ):
            # While urllib3 attempts to always turn off hostname matching from
            # the TLS library, this cannot always be done. So we check whether
            # the TLS Library still thinks it's matching hostnames.
            cert = socket.getpeercert()
            if not cert.get(\"subjectAltName\", ()):
                warnings.warn(
                    (
                        \"Certificate for {0} has no `subjectAltName`, falling back to check for a \"
                        \"`commonName` for now. This feature is being removed by major browsers and \"
                        \"deprecated by RFC 2818. (See https://github.com/urllib3/urllib3/issues/497 \"
                        \"for details.)\".format(hostname)
                    ),
                    SubjectAltNameWarning,
                )
            _match_hostname(cert, hostname)

        self.proxy_is_verified = ssl_context.verify_mode == ssl.CERT_REQUIRED
        return socket


def _match_hostname(cert, asserted_hostname):
    # Our upstream implementation of ssl.match_hostname()
    # only applies this normalization to IP addresses so it doesn't
    # match DNS SANs so we do the same thing!
    stripped_hostname = asserted_hostname.strip(\"u[]\")
    if is_ipaddress(stripped_hostname):
        asserted_hostname = stripped_hostname

    try:
        match_hostname(cert, asserted_hostname)
    except CertificateError as e:
        log.warning(
            \"Certificate did not match expected hostname: %s. Certificate: %s\",
            asserted_hostname,
            cert,
        )
        # Add cert to exception and reraise so client code can inspect
        # the cert when catching the exception, if they want to
        e._peer_cert = cert
        raise


def _get_default_user_agent():
    return \"python-urllib3/%s\" % __version__


class DummyConnection(object):
    \"\"\"Used to detect a failed ConnectionCls import.\"\"\"

    pass


if not ssl:
    HTTPSConnection = DummyConnection  # noqa: F811


VerifiedHTTPSConnection = HTTPSConnection

"""
module_dict["urllib3"+os.sep+"fields.py"]="""
from __future__ import absolute_import

import email.utils
import mimetypes
import re

from .packages import six


def guess_content_type(filename, default=\"application/octet-stream\"):
    \"\"\"
    Guess the \"Content-Type\" of a file.

    :param filename:
        The filename to guess the \"Content-Type\" of using :mod:`mimetypes`.
    :param default:
        If no \"Content-Type\" can be guessed, default to `default`.
    \"\"\"
    if filename:
        return mimetypes.guess_type(filename)[0] or default
    return default


def format_header_param_rfc2231(name, value):
    \"\"\"
    Helper function to format and quote a single header parameter using the
    strategy defined in RFC 2231.

    Particularly useful for header parameters which might contain
    non-ASCII values, like file names. This follows
    `RFC 2388 Section 4.4 <https://tools.ietf.org/html/rfc2388#section-4.4>`_.

    :param name:
        The name of the parameter, a string expected to be ASCII only.
    :param value:
        The value of the parameter, provided as ``bytes`` or `str``.
    :ret:
        An RFC-2231-formatted unicode string.
    \"\"\"
    if isinstance(value, six.binary_type):
        value = value.decode(\"utf-8\")

    if not any(ch in value for ch in '\"\\\\\\r\\n'):
        result = u'%s=\"%s\"' % (name, value)
        try:
            result.encode(\"ascii\")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
        else:
            return result

    if six.PY2:  # Python 2:
        value = value.encode(\"utf-8\")

    # encode_rfc2231 accepts an encoded string and returns an ascii-encoded
    # string in Python 2 but accepts and returns unicode strings in Python 3
    value = email.utils.encode_rfc2231(value, \"utf-8\")
    value = \"%s*=%s\" % (name, value)

    if six.PY2:  # Python 2:
        value = value.decode(\"utf-8\")

    return value


_HTML5_REPLACEMENTS = {
    u\"\\u0022\": u\"%22\",
    # Replace \"\\\" with \"\\\\\".
    u\"\\u005C\": u\"\\u005C\\u005C\",
}

# All control characters from 0x00 to 0x1F *except* 0x1B.
_HTML5_REPLACEMENTS.update(
    {
        six.unichr(cc): u\"%{:02X}\".format(cc)
        for cc in range(0x00, 0x1F + 1)
        if cc not in (0x1B,)
    }
)


def _replace_multiple(value, needles_and_replacements):
    def replacer(match):
        return needles_and_replacements[match.group(0)]

    pattern = re.compile(
        r\"|\".join([re.escape(needle) for needle in needles_and_replacements.keys()])
    )

    result = pattern.sub(replacer, value)

    return result


def format_header_param_html5(name, value):
    \"\"\"
    Helper function to format and quote a single header parameter using the
    HTML5 strategy.

    Particularly useful for header parameters which might contain
    non-ASCII values, like file names. This follows the `HTML5 Working Draft
    Section 4.10.22.7`_ and matches the behavior of curl and modern browsers.

    .. _HTML5 Working Draft Section 4.10.22.7:
        https://w3c.github.io/html/sec-forms.html#multipart-form-data

    :param name:
        The name of the parameter, a string expected to be ASCII only.
    :param value:
        The value of the parameter, provided as ``bytes`` or `str``.
    :ret:
        A unicode string, stripped of troublesome characters.
    \"\"\"
    if isinstance(value, six.binary_type):
        value = value.decode(\"utf-8\")

    value = _replace_multiple(value, _HTML5_REPLACEMENTS)

    return u'%s=\"%s\"' % (name, value)


# For backwards-compatibility.
format_header_param = format_header_param_html5


class RequestField(object):
    \"\"\"
    A data container for request body parameters.

    :param name:
        The name of this request field. Must be unicode.
    :param data:
        The data/value body.
    :param filename:
        An optional filename of the request field. Must be unicode.
    :param headers:
        An optional dict-like object of headers to initially use for the field.
    :param header_formatter:
        An optional callable that is used to encode and format the headers. By
        default, this is :func:`format_header_param_html5`.
    \"\"\"

    def __init__(
        self,
        name,
        data,
        filename=None,
        headers=None,
        header_formatter=format_header_param_html5,
    ):
        self._name = name
        self._filename = filename
        self.data = data
        self.headers = {}
        if headers:
            self.headers = dict(headers)
        self.header_formatter = header_formatter

    @classmethod
    def from_tuples(cls, fieldname, value, header_formatter=format_header_param_html5):
        \"\"\"
        A :class:`~urllib3.fields.RequestField` factory from old-style tuple parameters.

        Supports constructing :class:`~urllib3.fields.RequestField` from
        parameter of key/value strings AND key/filetuple. A filetuple is a
        (filename, data, MIME type) tuple where the MIME type is optional.
        For example::

            'foo': 'bar',
            'fakefile': ('foofile.txt', 'contents of foofile'),
            'realfile': ('barfile.txt', open('realfile').read()),
            'typedfile': ('bazfile.bin', open('bazfile').read(), 'image/jpeg'),
            'nonamefile': 'contents of nonamefile field',

        Field names and filenames must be unicode.
        \"\"\"
        if isinstance(value, tuple):
            if len(value) == 3:
                filename, data, content_type = value
            else:
                filename, data = value
                content_type = guess_content_type(filename)
        else:
            filename = None
            content_type = None
            data = value

        request_param = cls(
            fieldname, data, filename=filename, header_formatter=header_formatter
        )
        request_param.make_multipart(content_type=content_type)

        return request_param

    def _render_part(self, name, value):
        \"\"\"
        Overridable helper function to format a single header parameter. By
        default, this calls ``self.header_formatter``.

        :param name:
            The name of the parameter, a string expected to be ASCII only.
        :param value:
            The value of the parameter, provided as a unicode string.
        \"\"\"

        return self.header_formatter(name, value)

    def _render_parts(self, header_parts):
        \"\"\"
        Helper function to format and quote a single header.

        Useful for single headers that are composed of multiple items. E.g.,
        'Content-Disposition' fields.

        :param header_parts:
            A sequence of (k, v) tuples or a :class:`dict` of (k, v) to format
            as `k1=\"v1\"; k2=\"v2\"; ...`.
        \"\"\"
        parts = []
        iterable = header_parts
        if isinstance(header_parts, dict):
            iterable = header_parts.items()

        for name, value in iterable:
            if value is not None:
                parts.append(self._render_part(name, value))

        return u\"; \".join(parts)

    def render_headers(self):
        \"\"\"
        Renders the headers for this request field.
        \"\"\"
        lines = []

        sort_keys = [\"Content-Disposition\", \"Content-Type\", \"Content-Location\"]
        for sort_key in sort_keys:
            if self.headers.get(sort_key, False):
                lines.append(u\"%s: %s\" % (sort_key, self.headers[sort_key]))

        for header_name, header_value in self.headers.items():
            if header_name not in sort_keys:
                if header_value:
                    lines.append(u\"%s: %s\" % (header_name, header_value))

        lines.append(u\"\\r\\n\")
        return u\"\\r\\n\".join(lines)

    def make_multipart(
        self, content_disposition=None, content_type=None, content_location=None
    ):
        \"\"\"
        Makes this request field into a multipart request field.

        This method overrides \"Content-Disposition\", \"Content-Type\" and
        \"Content-Location\" headers to the request parameter.

        :param content_type:
            The 'Content-Type' of the request body.
        :param content_location:
            The 'Content-Location' of the request body.

        \"\"\"
        self.headers[\"Content-Disposition\"] = content_disposition or u\"form-data\"
        self.headers[\"Content-Disposition\"] += u\"; \".join(
            [
                u\"\",
                self._render_parts(
                    ((u\"name\", self._name), (u\"filename\", self._filename))
                ),
            ]
        )
        self.headers[\"Content-Type\"] = content_type
        self.headers[\"Content-Location\"] = content_location

"""
module_dict["urllib3"+os.sep+"poolmanager.py"]="""
from __future__ import absolute_import

import collections
import functools
import logging

from ._collections import RecentlyUsedContainer
from .connectionpool import HTTPConnectionPool, HTTPSConnectionPool, port_by_scheme
from .exceptions import (
    LocationValueError,
    MaxRetryError,
    ProxySchemeUnknown,
    ProxySchemeUnsupported,
    URLSchemeUnknown,
)
from .packages import six
from .packages.six.moves.urllib.parse import urljoin
from .request import RequestMethods
from .util.proxy import connection_requires_http_tunnel
from .util.retry import Retry
from .util.url import parse_url

__all__ = [\"PoolManager\", \"ProxyManager\", \"proxy_from_url\"]


log = logging.getLogger(__name__)

SSL_KEYWORDS = (
    \"key_file\",
    \"cert_file\",
    \"cert_reqs\",
    \"ca_certs\",
    \"ssl_version\",
    \"ca_cert_dir\",
    \"ssl_context\",
    \"key_password\",
    \"server_hostname\",
)

# All known keyword arguments that could be provided to the pool manager, its
# pools, or the underlying connections. This is used to construct a pool key.
_key_fields = (
    \"key_scheme\",  # str
    \"key_host\",  # str
    \"key_port\",  # int
    \"key_timeout\",  # int or float or Timeout
    \"key_retries\",  # int or Retry
    \"key_strict\",  # bool
    \"key_block\",  # bool
    \"key_source_address\",  # str
    \"key_key_file\",  # str
    \"key_key_password\",  # str
    \"key_cert_file\",  # str
    \"key_cert_reqs\",  # str
    \"key_ca_certs\",  # str
    \"key_ssl_version\",  # str
    \"key_ca_cert_dir\",  # str
    \"key_ssl_context\",  # instance of ssl.SSLContext or urllib3.util.ssl_.SSLContext
    \"key_maxsize\",  # int
    \"key_headers\",  # dict
    \"key__proxy\",  # parsed proxy url
    \"key__proxy_headers\",  # dict
    \"key__proxy_config\",  # class
    \"key_socket_options\",  # list of (level (int), optname (int), value (int or str)) tuples
    \"key__socks_options\",  # dict
    \"key_assert_hostname\",  # bool or string
    \"key_assert_fingerprint\",  # str
    \"key_server_hostname\",  # str
)

#: The namedtuple class used to construct keys for the connection pool.
#: All custom key schemes should include the fields in this key at a minimum.
PoolKey = collections.namedtuple(\"PoolKey\", _key_fields)

_proxy_config_fields = (\"ssl_context\", \"use_forwarding_for_https\")
ProxyConfig = collections.namedtuple(\"ProxyConfig\", _proxy_config_fields)


def _default_key_normalizer(key_class, request_context):
    \"\"\"
    Create a pool key out of a request context dictionary.

    According to RFC 3986, both the scheme and host are case-insensitive.
    Therefore, this function normalizes both before constructing the pool
    key for an HTTPS request. If you wish to change this behaviour, provide
    alternate callables to ``key_fn_by_scheme``.

    :param key_class:
        The class to use when constructing the key. This should be a namedtuple
        with the ``scheme`` and ``host`` keys at a minimum.
    :type  key_class: namedtuple
    :param request_context:
        A dictionary-like object that contain the context for a request.
    :type  request_context: dict

    :return: A namedtuple that can be used as a connection pool key.
    :rtype:  PoolKey
    \"\"\"
    # Since we mutate the dictionary, make a copy first
    context = request_context.copy()
    context[\"scheme\"] = context[\"scheme\"].lower()
    context[\"host\"] = context[\"host\"].lower()

    # These are both dictionaries and need to be transformed into frozensets
    for key in (\"headers\", \"_proxy_headers\", \"_socks_options\"):
        if key in context and context[key] is not None:
            context[key] = frozenset(context[key].items())

    # The socket_options key may be a list and needs to be transformed into a
    # tuple.
    socket_opts = context.get(\"socket_options\")
    if socket_opts is not None:
        context[\"socket_options\"] = tuple(socket_opts)

    # Map the kwargs to the names in the namedtuple - this is necessary since
    # namedtuples can't have fields starting with '_'.
    for key in list(context.keys()):
        context[\"key_\" + key] = context.pop(key)

    # Default to ``None`` for keys missing from the context
    for field in key_class._fields:
        if field not in context:
            context[field] = None

    return key_class(**context)


#: A dictionary that maps a scheme to a callable that creates a pool key.
#: This can be used to alter the way pool keys are constructed, if desired.
#: Each PoolManager makes a copy of this dictionary so they can be configured
#: globally here, or individually on the instance.
key_fn_by_scheme = {
    \"http\": functools.partial(_default_key_normalizer, PoolKey),
    \"https\": functools.partial(_default_key_normalizer, PoolKey),
}

pool_classes_by_scheme = {\"http\": HTTPConnectionPool, \"https\": HTTPSConnectionPool}


class PoolManager(RequestMethods):
    \"\"\"
    Allows for arbitrary requests while transparently keeping track of
    necessary connection pools for you.

    :param num_pools:
        Number of connection pools to cache before discarding the least
        recently used pool.

    :param headers:
        Headers to include with all requests, unless other headers are given
        explicitly.

    :param \\\\**connection_pool_kw:
        Additional parameters are used to create fresh
        :class:`urllib3.connectionpool.ConnectionPool` instances.

    Example::

        >>> manager = PoolManager(num_pools=2)
        >>> r = manager.request('GET', 'http://google.com/')
        >>> r = manager.request('GET', 'http://google.com/mail')
        >>> r = manager.request('GET', 'http://yahoo.com/')
        >>> len(manager.pools)
        2

    \"\"\"

    proxy = None
    proxy_config = None

    def __init__(self, num_pools=10, headers=None, **connection_pool_kw):
        RequestMethods.__init__(self, headers)
        self.connection_pool_kw = connection_pool_kw
        self.pools = RecentlyUsedContainer(num_pools, dispose_func=lambda p: p.close())

        # Locally set the pool classes and keys so other PoolManagers can
        # override them.
        self.pool_classes_by_scheme = pool_classes_by_scheme
        self.key_fn_by_scheme = key_fn_by_scheme.copy()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.clear()
        # Return False to re-raise any potential exceptions
        return False

    def _new_pool(self, scheme, host, port, request_context=None):
        \"\"\"
        Create a new :class:`urllib3.connectionpool.ConnectionPool` based on host, port, scheme, and
        any additional pool keyword arguments.

        If ``request_context`` is provided, it is provided as keyword arguments
        to the pool class used. This method is used to actually create the
        connection pools handed out by :meth:`connection_from_url` and
        companion methods. It is intended to be overridden for customization.
        \"\"\"
        pool_cls = self.pool_classes_by_scheme[scheme]
        if request_context is None:
            request_context = self.connection_pool_kw.copy()

        # Although the context has everything necessary to create the pool,
        # this function has historically only used the scheme, host, and port
        # in the positional args. When an API change is acceptable these can
        # be removed.
        for key in (\"scheme\", \"host\", \"port\"):
            request_context.pop(key, None)

        if scheme == \"http\":
            for kw in SSL_KEYWORDS:
                request_context.pop(kw, None)

        return pool_cls(host, port, **request_context)

    def clear(self):
        \"\"\"
        Empty our store of pools and direct them all to close.

        This will not affect in-flight connections, but they will not be
        re-used after completion.
        \"\"\"
        self.pools.clear()

    def connection_from_host(self, host, port=None, scheme=\"http\", pool_kwargs=None):
        \"\"\"
        Get a :class:`urllib3.connectionpool.ConnectionPool` based on the host, port, and scheme.

        If ``port`` isn't given, it will be derived from the ``scheme`` using
        ``urllib3.connectionpool.port_by_scheme``. If ``pool_kwargs`` is
        provided, it is merged with the instance's ``connection_pool_kw``
        variable and used to create the new connection pool, if one is
        needed.
        \"\"\"

        if not host:
            raise LocationValueError(\"No host specified.\")

        request_context = self._merge_pool_kwargs(pool_kwargs)
        request_context[\"scheme\"] = scheme or \"http\"
        if not port:
            port = port_by_scheme.get(request_context[\"scheme\"].lower(), 80)
        request_context[\"port\"] = port
        request_context[\"host\"] = host

        return self.connection_from_context(request_context)

    def connection_from_context(self, request_context):
        \"\"\"
        Get a :class:`urllib3.connectionpool.ConnectionPool` based on the request context.

        ``request_context`` must at least contain the ``scheme`` key and its
        value must be a key in ``key_fn_by_scheme`` instance variable.
        \"\"\"
        scheme = request_context[\"scheme\"].lower()
        pool_key_constructor = self.key_fn_by_scheme.get(scheme)
        if not pool_key_constructor:
            raise URLSchemeUnknown(scheme)
        pool_key = pool_key_constructor(request_context)

        return self.connection_from_pool_key(pool_key, request_context=request_context)

    def connection_from_pool_key(self, pool_key, request_context=None):
        \"\"\"
        Get a :class:`urllib3.connectionpool.ConnectionPool` based on the provided pool key.

        ``pool_key`` should be a namedtuple that only contains immutable
        objects. At a minimum it must have the ``scheme``, ``host``, and
        ``port`` fields.
        \"\"\"
        with self.pools.lock:
            # If the scheme, host, or port doesn't match existing open
            # connections, open a new ConnectionPool.
            pool = self.pools.get(pool_key)
            if pool:
                return pool

            # Make a fresh ConnectionPool of the desired type
            scheme = request_context[\"scheme\"]
            host = request_context[\"host\"]
            port = request_context[\"port\"]
            pool = self._new_pool(scheme, host, port, request_context=request_context)
            self.pools[pool_key] = pool

        return pool

    def connection_from_url(self, url, pool_kwargs=None):
        \"\"\"
        Similar to :func:`urllib3.connectionpool.connection_from_url`.

        If ``pool_kwargs`` is not provided and a new pool needs to be
        constructed, ``self.connection_pool_kw`` is used to initialize
        the :class:`urllib3.connectionpool.ConnectionPool`. If ``pool_kwargs``
        is provided, it is used instead. Note that if a new pool does not
        need to be created for the request, the provided ``pool_kwargs`` are
        not used.
        \"\"\"
        u = parse_url(url)
        return self.connection_from_host(
            u.host, port=u.port, scheme=u.scheme, pool_kwargs=pool_kwargs
        )

    def _merge_pool_kwargs(self, override):
        \"\"\"
        Merge a dictionary of override values for self.connection_pool_kw.

        This does not modify self.connection_pool_kw and returns a new dict.
        Any keys in the override dictionary with a value of ``None`` are
        removed from the merged dictionary.
        \"\"\"
        base_pool_kwargs = self.connection_pool_kw.copy()
        if override:
            for key, value in override.items():
                if value is None:
                    try:
                        del base_pool_kwargs[key]
                    except KeyError:
                        pass
                else:
                    base_pool_kwargs[key] = value
        return base_pool_kwargs

    def _proxy_requires_url_absolute_form(self, parsed_url):
        \"\"\"
        Indicates if the proxy requires the complete destination URL in the
        request.  Normally this is only needed when not using an HTTP CONNECT
        tunnel.
        \"\"\"
        if self.proxy is None:
            return False

        return not connection_requires_http_tunnel(
            self.proxy, self.proxy_config, parsed_url.scheme
        )

    def _validate_proxy_scheme_url_selection(self, url_scheme):
        \"\"\"
        Validates that were not attempting to do TLS in TLS connections on
        Python2 or with unsupported SSL implementations.
        \"\"\"
        if self.proxy is None or url_scheme != \"https\":
            return

        if self.proxy.scheme != \"https\":
            return

        if six.PY2 and not self.proxy_config.use_forwarding_for_https:
            raise ProxySchemeUnsupported(
                \"Contacting HTTPS destinations through HTTPS proxies \"
                \"'via CONNECT tunnels' is not supported in Python 2\"
            )

    def urlopen(self, method, url, redirect=True, **kw):
        \"\"\"
        Same as :meth:`urllib3.HTTPConnectionPool.urlopen`
        with custom cross-host redirect logic and only sends the request-uri
        portion of the ``url``.

        The given ``url`` parameter must be absolute, such that an appropriate
        :class:`urllib3.connectionpool.ConnectionPool` can be chosen for it.
        \"\"\"
        u = parse_url(url)
        self._validate_proxy_scheme_url_selection(u.scheme)

        conn = self.connection_from_host(u.host, port=u.port, scheme=u.scheme)

        kw[\"assert_same_host\"] = False
        kw[\"redirect\"] = False

        if \"headers\" not in kw:
            kw[\"headers\"] = self.headers.copy()

        if self._proxy_requires_url_absolute_form(u):
            response = conn.urlopen(method, url, **kw)
        else:
            response = conn.urlopen(method, u.request_uri, **kw)

        redirect_location = redirect and response.get_redirect_location()
        if not redirect_location:
            return response

        # Support relative URLs for redirecting.
        redirect_location = urljoin(url, redirect_location)

        # RFC 7231, Section 6.4.4
        if response.status == 303:
            method = \"GET\"

        retries = kw.get(\"retries\")
        if not isinstance(retries, Retry):
            retries = Retry.from_int(retries, redirect=redirect)

        # Strip headers marked as unsafe to forward to the redirected location.
        # Check remove_headers_on_redirect to avoid a potential network call within
        # conn.is_same_host() which may use socket.gethostbyname() in the future.
        if retries.remove_headers_on_redirect and not conn.is_same_host(
            redirect_location
        ):
            headers = list(six.iterkeys(kw[\"headers\"]))
            for header in headers:
                if header.lower() in retries.remove_headers_on_redirect:
                    kw[\"headers\"].pop(header, None)

        try:
            retries = retries.increment(method, url, response=response, _pool=conn)
        except MaxRetryError:
            if retries.raise_on_redirect:
                response.drain_conn()
                raise
            return response

        kw[\"retries\"] = retries
        kw[\"redirect\"] = redirect

        log.info(\"Redirecting %s -> %s\", url, redirect_location)

        response.drain_conn()
        return self.urlopen(method, redirect_location, **kw)


class ProxyManager(PoolManager):
    \"\"\"
    Behaves just like :class:`PoolManager`, but sends all requests through
    the defined proxy, using the CONNECT method for HTTPS URLs.

    :param proxy_url:
        The URL of the proxy to be used.

    :param proxy_headers:
        A dictionary containing headers that will be sent to the proxy. In case
        of HTTP they are being sent with each request, while in the
        HTTPS/CONNECT case they are sent only once. Could be used for proxy
        authentication.

    :param proxy_ssl_context:
        The proxy SSL context is used to establish the TLS connection to the
        proxy when using HTTPS proxies.

    :param use_forwarding_for_https:
        (Defaults to False) If set to True will forward requests to the HTTPS
        proxy to be made on behalf of the client instead of creating a TLS
        tunnel via the CONNECT method. **Enabling this flag means that request
        and response headers and content will be visible from the HTTPS proxy**
        whereas tunneling keeps request and response headers and content
        private.  IP address, target hostname, SNI, and port are always visible
        to an HTTPS proxy even when this flag is disabled.

    Example:
        >>> proxy = urllib3.ProxyManager('http://localhost:3128/')
        >>> r1 = proxy.request('GET', 'http://google.com/')
        >>> r2 = proxy.request('GET', 'http://httpbin.org/')
        >>> len(proxy.pools)
        1
        >>> r3 = proxy.request('GET', 'https://httpbin.org/')
        >>> r4 = proxy.request('GET', 'https://twitter.com/')
        >>> len(proxy.pools)
        3

    \"\"\"

    def __init__(
        self,
        proxy_url,
        num_pools=10,
        headers=None,
        proxy_headers=None,
        proxy_ssl_context=None,
        use_forwarding_for_https=False,
        **connection_pool_kw
    ):

        if isinstance(proxy_url, HTTPConnectionPool):
            proxy_url = \"%s://%s:%i\" % (
                proxy_url.scheme,
                proxy_url.host,
                proxy_url.port,
            )
        proxy = parse_url(proxy_url)

        if proxy.scheme not in (\"http\", \"https\"):
            raise ProxySchemeUnknown(proxy.scheme)

        if not proxy.port:
            port = port_by_scheme.get(proxy.scheme, 80)
            proxy = proxy._replace(port=port)

        self.proxy = proxy
        self.proxy_headers = proxy_headers or {}
        self.proxy_ssl_context = proxy_ssl_context
        self.proxy_config = ProxyConfig(proxy_ssl_context, use_forwarding_for_https)

        connection_pool_kw[\"_proxy\"] = self.proxy
        connection_pool_kw[\"_proxy_headers\"] = self.proxy_headers
        connection_pool_kw[\"_proxy_config\"] = self.proxy_config

        super(ProxyManager, self).__init__(num_pools, headers, **connection_pool_kw)

    def connection_from_host(self, host, port=None, scheme=\"http\", pool_kwargs=None):
        if scheme == \"https\":
            return super(ProxyManager, self).connection_from_host(
                host, port, scheme, pool_kwargs=pool_kwargs
            )

        return super(ProxyManager, self).connection_from_host(
            self.proxy.host, self.proxy.port, self.proxy.scheme, pool_kwargs=pool_kwargs
        )

    def _set_proxy_headers(self, url, headers=None):
        \"\"\"
        Sets headers needed by proxies: specifically, the Accept and Host
        headers. Only sets headers not provided by the user.
        \"\"\"
        headers_ = {\"Accept\": \"*/*\"}

        netloc = parse_url(url).netloc
        if netloc:
            headers_[\"Host\"] = netloc

        if headers:
            headers_.update(headers)
        return headers_

    def urlopen(self, method, url, redirect=True, **kw):
        \"Same as HTTP(S)ConnectionPool.urlopen, ``url`` must be absolute.\"
        u = parse_url(url)
        if not connection_requires_http_tunnel(self.proxy, self.proxy_config, u.scheme):
            # For connections using HTTP CONNECT, httplib sets the necessary
            # headers on the CONNECT to the proxy. If we're not using CONNECT,
            # we'll definitely need to set 'Host' at the very least.
            headers = kw.get(\"headers\", self.headers)
            kw[\"headers\"] = self._set_proxy_headers(url, headers)

        return super(ProxyManager, self).urlopen(method, url, redirect=redirect, **kw)


def proxy_from_url(url, **kw):
    return ProxyManager(proxy_url=url, **kw)

"""
module_dict["urllib3"+os.sep+"request.py"]="""
from __future__ import absolute_import

from .filepost import encode_multipart_formdata
from .packages.six.moves.urllib.parse import urlencode

__all__ = [\"RequestMethods\"]


class RequestMethods(object):
    \"\"\"
    Convenience mixin for classes who implement a :meth:`urlopen` method, such
    as :class:`urllib3.HTTPConnectionPool` and
    :class:`urllib3.PoolManager`.

    Provides behavior for making common types of HTTP request methods and
    decides which type of request field encoding to use.

    Specifically,

    :meth:`.request_encode_url` is for sending requests whose fields are
    encoded in the URL (such as GET, HEAD, DELETE).

    :meth:`.request_encode_body` is for sending requests whose fields are
    encoded in the *body* of the request using multipart or www-form-urlencoded
    (such as for POST, PUT, PATCH).

    :meth:`.request` is for making any kind of request, it will look up the
    appropriate encoding format and use one of the above two methods to make
    the request.

    Initializer parameters:

    :param headers:
        Headers to include with all requests, unless other headers are given
        explicitly.
    \"\"\"

    _encode_url_methods = {\"DELETE\", \"GET\", \"HEAD\", \"OPTIONS\"}

    def __init__(self, headers=None):
        self.headers = headers or {}

    def urlopen(
        self,
        method,
        url,
        body=None,
        headers=None,
        encode_multipart=True,
        multipart_boundary=None,
        **kw
    ):  # Abstract
        raise NotImplementedError(
            \"Classes extending RequestMethods must implement \"
            \"their own ``urlopen`` method.\"
        )

    def request(self, method, url, fields=None, headers=None, **urlopen_kw):
        \"\"\"
        Make a request using :meth:`urlopen` with the appropriate encoding of
        ``fields`` based on the ``method`` used.

        This is a convenience method that requires the least amount of manual
        effort. It can be used in most situations, while still having the
        option to drop down to more specific methods when necessary, such as
        :meth:`request_encode_url`, :meth:`request_encode_body`,
        or even the lowest level :meth:`urlopen`.
        \"\"\"
        method = method.upper()

        urlopen_kw[\"request_url\"] = url

        if method in self._encode_url_methods:
            return self.request_encode_url(
                method, url, fields=fields, headers=headers, **urlopen_kw
            )
        else:
            return self.request_encode_body(
                method, url, fields=fields, headers=headers, **urlopen_kw
            )

    def request_encode_url(self, method, url, fields=None, headers=None, **urlopen_kw):
        \"\"\"
        Make a request using :meth:`urlopen` with the ``fields`` encoded in
        the url. This is useful for request methods like GET, HEAD, DELETE, etc.
        \"\"\"
        if headers is None:
            headers = self.headers

        extra_kw = {\"headers\": headers}
        extra_kw.update(urlopen_kw)

        if fields:
            url += \"?\" + urlencode(fields)

        return self.urlopen(method, url, **extra_kw)

    def request_encode_body(
        self,
        method,
        url,
        fields=None,
        headers=None,
        encode_multipart=True,
        multipart_boundary=None,
        **urlopen_kw
    ):
        \"\"\"
        Make a request using :meth:`urlopen` with the ``fields`` encoded in
        the body. This is useful for request methods like POST, PUT, PATCH, etc.

        When ``encode_multipart=True`` (default), then
        :func:`urllib3.encode_multipart_formdata` is used to encode
        the payload with the appropriate content type. Otherwise
        :func:`urllib.parse.urlencode` is used with the
        'application/x-www-form-urlencoded' content type.

        Multipart encoding must be used when posting files, and it's reasonably
        safe to use it in other times too. However, it may break request
        signing, such as with OAuth.

        Supports an optional ``fields`` parameter of key/value strings AND
        key/filetuple. A filetuple is a (filename, data, MIME type) tuple where
        the MIME type is optional. For example::

            fields = {
                'foo': 'bar',
                'fakefile': ('foofile.txt', 'contents of foofile'),
                'realfile': ('barfile.txt', open('realfile').read()),
                'typedfile': ('bazfile.bin', open('bazfile').read(),
                              'image/jpeg'),
                'nonamefile': 'contents of nonamefile field',
            }

        When uploading a file, providing a filename (the first parameter of the
        tuple) is optional but recommended to best mimic behavior of browsers.

        Note that if ``headers`` are supplied, the 'Content-Type' header will
        be overwritten because it depends on the dynamic random boundary string
        which is used to compose the body of the request. The random boundary
        string can be explicitly set with the ``multipart_boundary`` parameter.
        \"\"\"
        if headers is None:
            headers = self.headers

        extra_kw = {\"headers\": {}}

        if fields:
            if \"body\" in urlopen_kw:
                raise TypeError(
                    \"request got values for both 'fields' and 'body', can only specify one.\"
                )

            if encode_multipart:
                body, content_type = encode_multipart_formdata(
                    fields, boundary=multipart_boundary
                )
            else:
                body, content_type = (
                    urlencode(fields),
                    \"application/x-www-form-urlencoded\",
                )

            extra_kw[\"body\"] = body
            extra_kw[\"headers\"] = {\"Content-Type\": content_type}

        extra_kw[\"headers\"].update(headers)
        extra_kw.update(urlopen_kw)

        return self.urlopen(method, url, **extra_kw)

"""
module_dict["urllib3"+os.sep+"response.py"]="""
from __future__ import absolute_import
# < include 'brotlicffi.py' >

# < include 'brotli.py' >


import io
import logging
import zlib
from contextlib import contextmanager
from socket import error as SocketError
from socket import timeout as SocketTimeout

try:
    try:
        import brotlicffi as brotli
    except ImportError:
        import brotli
except ImportError:
    brotli = None

from ._collections import HTTPHeaderDict
from .connection import BaseSSLError, HTTPException
from .exceptions import (
    BodyNotHttplibCompatible,
    DecodeError,
    HTTPError,
    IncompleteRead,
    InvalidChunkLength,
    InvalidHeader,
    ProtocolError,
    ReadTimeoutError,
    ResponseNotChunked,
    SSLError,
)
from .packages import six
from .util.response import is_fp_closed, is_response_to_head

log = logging.getLogger(__name__)


class DeflateDecoder(object):
    def __init__(self):
        self._first_try = True
        self._data = b\"\"
        self._obj = zlib.decompressobj()

    def __getattr__(self, name):
        return getattr(self._obj, name)

    def decompress(self, data):
        if not data:
            return data

        if not self._first_try:
            return self._obj.decompress(data)

        self._data += data
        try:
            decompressed = self._obj.decompress(data)
            if decompressed:
                self._first_try = False
                self._data = None
            return decompressed
        except zlib.error:
            self._first_try = False
            self._obj = zlib.decompressobj(-zlib.MAX_WBITS)
            try:
                return self.decompress(self._data)
            finally:
                self._data = None


class GzipDecoderState(object):

    FIRST_MEMBER = 0
    OTHER_MEMBERS = 1
    SWALLOW_DATA = 2


class GzipDecoder(object):
    def __init__(self):
        self._obj = zlib.decompressobj(16 + zlib.MAX_WBITS)
        self._state = GzipDecoderState.FIRST_MEMBER

    def __getattr__(self, name):
        return getattr(self._obj, name)

    def decompress(self, data):
        ret = bytearray()
        if self._state == GzipDecoderState.SWALLOW_DATA or not data:
            return bytes(ret)
        while True:
            try:
                ret += self._obj.decompress(data)
            except zlib.error:
                previous_state = self._state
                # Ignore data after the first error
                self._state = GzipDecoderState.SWALLOW_DATA
                if previous_state == GzipDecoderState.OTHER_MEMBERS:
                    # Allow trailing garbage acceptable in other gzip clients
                    return bytes(ret)
                raise
            data = self._obj.unused_data
            if not data:
                return bytes(ret)
            self._state = GzipDecoderState.OTHER_MEMBERS
            self._obj = zlib.decompressobj(16 + zlib.MAX_WBITS)


if brotli is not None:

    class BrotliDecoder(object):
        # Supports both 'brotlipy' and 'Brotli' packages
        # since they share an import name. The top branches
        # are for 'brotlipy' and bottom branches for 'Brotli'
        def __init__(self):
            self._obj = brotli.Decompressor()
            if hasattr(self._obj, \"decompress\"):
                self.decompress = self._obj.decompress
            else:
                self.decompress = self._obj.process

        def flush(self):
            if hasattr(self._obj, \"flush\"):
                return self._obj.flush()
            return b\"\"


class MultiDecoder(object):
    \"\"\"
    From RFC7231:
        If one or more encodings have been applied to a representation, the
        sender that applied the encodings MUST generate a Content-Encoding
        header field that lists the content codings in the order in which
        they were applied.
    \"\"\"

    def __init__(self, modes):
        self._decoders = [_get_decoder(m.strip()) for m in modes.split(\",\")]

    def flush(self):
        return self._decoders[0].flush()

    def decompress(self, data):
        for d in reversed(self._decoders):
            data = d.decompress(data)
        return data


def _get_decoder(mode):
    if \",\" in mode:
        return MultiDecoder(mode)

    if mode == \"gzip\":
        return GzipDecoder()

    if brotli is not None and mode == \"br\":
        return BrotliDecoder()

    return DeflateDecoder()


class HTTPResponse(io.IOBase):
    \"\"\"
    HTTP Response container.

    Backwards-compatible with :class:`http.client.HTTPResponse` but the response ``body`` is
    loaded and decoded on-demand when the ``data`` property is accessed.  This
    class is also compatible with the Python standard library's :mod:`io`
    module, and can hence be treated as a readable object in the context of that
    framework.

    Extra parameters for behaviour not present in :class:`http.client.HTTPResponse`:

    :param preload_content:
        If True, the response's body will be preloaded during construction.

    :param decode_content:
        If True, will attempt to decode the body based on the
        'content-encoding' header.

    :param original_response:
        When this HTTPResponse wrapper is generated from an :class:`http.client.HTTPResponse`
        object, it's convenient to include the original for debug purposes. It's
        otherwise unused.

    :param retries:
        The retries contains the last :class:`~urllib3.util.retry.Retry` that
        was used during the request.

    :param enforce_content_length:
        Enforce content length checking. Body returned by server must match
        value of Content-Length header, if present. Otherwise, raise error.
    \"\"\"

    CONTENT_DECODERS = [\"gzip\", \"deflate\"]
    if brotli is not None:
        CONTENT_DECODERS += [\"br\"]
    REDIRECT_STATUSES = [301, 302, 303, 307, 308]

    def __init__(
        self,
        body=\"\",
        headers=None,
        status=0,
        version=0,
        reason=None,
        strict=0,
        preload_content=True,
        decode_content=True,
        original_response=None,
        pool=None,
        connection=None,
        msg=None,
        retries=None,
        enforce_content_length=False,
        request_method=None,
        request_url=None,
        auto_close=True,
    ):

        if isinstance(headers, HTTPHeaderDict):
            self.headers = headers
        else:
            self.headers = HTTPHeaderDict(headers)
        self.status = status
        self.version = version
        self.reason = reason
        self.strict = strict
        self.decode_content = decode_content
        self.retries = retries
        self.enforce_content_length = enforce_content_length
        self.auto_close = auto_close

        self._decoder = None
        self._body = None
        self._fp = None
        self._original_response = original_response
        self._fp_bytes_read = 0
        self.msg = msg
        self._request_url = request_url

        if body and isinstance(body, (six.string_types, bytes)):
            self._body = body

        self._pool = pool
        self._connection = connection

        if hasattr(body, \"read\"):
            self._fp = body

        # Are we using the chunked-style of transfer encoding?
        self.chunked = False
        self.chunk_left = None
        tr_enc = self.headers.get(\"transfer-encoding\", \"\").lower()
        # Don't incur the penalty of creating a list and then discarding it
        encodings = (enc.strip() for enc in tr_enc.split(\",\"))
        if \"chunked\" in encodings:
            self.chunked = True

        # Determine length of response
        self.length_remaining = self._init_length(request_method)

        # If requested, preload the body.
        if preload_content and not self._body:
            self._body = self.read(decode_content=decode_content)

    def get_redirect_location(self):
        \"\"\"
        Should we redirect and where to?

        :returns: Truthy redirect location string if we got a redirect status
            code and valid location. ``None`` if redirect status and no
            location. ``False`` if not a redirect status code.
        \"\"\"
        if self.status in self.REDIRECT_STATUSES:
            return self.headers.get(\"location\")

        return False

    def release_conn(self):
        if not self._pool or not self._connection:
            return

        self._pool._put_conn(self._connection)
        self._connection = None

    def drain_conn(self):
        \"\"\"
        Read and discard any remaining HTTP response data in the response connection.

        Unread data in the HTTPResponse connection blocks the connection from being released back to the pool.
        \"\"\"
        try:
            self.read()
        except (HTTPError, SocketError, BaseSSLError, HTTPException):
            pass

    @property
    def data(self):
        # For backwards-compat with earlier urllib3 0.4 and earlier.
        if self._body:
            return self._body

        if self._fp:
            return self.read(cache_content=True)

    @property
    def connection(self):
        return self._connection

    def isclosed(self):
        return is_fp_closed(self._fp)

    def tell(self):
        \"\"\"
        Obtain the number of bytes pulled over the wire so far. May differ from
        the amount of content returned by :meth:``urllib3.response.HTTPResponse.read``
        if bytes are encoded on the wire (e.g, compressed).
        \"\"\"
        return self._fp_bytes_read

    def _init_length(self, request_method):
        \"\"\"
        Set initial length value for Response content if available.
        \"\"\"
        length = self.headers.get(\"content-length\")

        if length is not None:
            if self.chunked:
                # This Response will fail with an IncompleteRead if it can't be
                # received as chunked. This method falls back to attempt reading
                # the response before raising an exception.
                log.warning(
                    \"Received response with both Content-Length and \"
                    \"Transfer-Encoding set. This is expressly forbidden \"
                    \"by RFC 7230 sec 3.3.2. Ignoring Content-Length and \"
                    \"attempting to process response as Transfer-Encoding: \"
                    \"chunked.\"
                )
                return None

            try:
                # RFC 7230 section 3.3.2 specifies multiple content lengths can
                # be sent in a single Content-Length header
                # (e.g. Content-Length: 42, 42). This line ensures the values
                # are all valid ints and that as long as the `set` length is 1,
                # all values are the same. Otherwise, the header is invalid.
                lengths = set([int(val) for val in length.split(\",\")])
                if len(lengths) > 1:
                    raise InvalidHeader(
                        \"Content-Length contained multiple \"
                        \"unmatching values (%s)\" % length
                    )
                length = lengths.pop()
            except ValueError:
                length = None
            else:
                if length < 0:
                    length = None

        # Convert status to int for comparison
        # In some cases, httplib returns a status of \"_UNKNOWN\"
        try:
            status = int(self.status)
        except ValueError:
            status = 0

        # Check for responses that shouldn't include a body
        if status in (204, 304) or 100 <= status < 200 or request_method == \"HEAD\":
            length = 0

        return length

    def _init_decoder(self):
        \"\"\"
        Set-up the _decoder attribute if necessary.
        \"\"\"
        # Note: content-encoding value should be case-insensitive, per RFC 7230
        # Section 3.2
        content_encoding = self.headers.get(\"content-encoding\", \"\").lower()
        if self._decoder is None:
            if content_encoding in self.CONTENT_DECODERS:
                self._decoder = _get_decoder(content_encoding)
            elif \",\" in content_encoding:
                encodings = [
                    e.strip()
                    for e in content_encoding.split(\",\")
                    if e.strip() in self.CONTENT_DECODERS
                ]
                if len(encodings):
                    self._decoder = _get_decoder(content_encoding)

    DECODER_ERROR_CLASSES = (IOError, zlib.error)
    if brotli is not None:
        DECODER_ERROR_CLASSES += (brotli.error,)

    def _decode(self, data, decode_content, flush_decoder):
        \"\"\"
        Decode the data passed in and potentially flush the decoder.
        \"\"\"
        if not decode_content:
            return data

        try:
            if self._decoder:
                data = self._decoder.decompress(data)
        except self.DECODER_ERROR_CLASSES as e:
            content_encoding = self.headers.get(\"content-encoding\", \"\").lower()
            raise DecodeError(
                \"Received response with content-encoding: %s, but \"
                \"failed to decode it.\" % content_encoding,
                e,
            )
        if flush_decoder:
            data += self._flush_decoder()

        return data

    def _flush_decoder(self):
        \"\"\"
        Flushes the decoder. Should only be called if the decoder is actually
        being used.
        \"\"\"
        if self._decoder:
            buf = self._decoder.decompress(b\"\")
            return buf + self._decoder.flush()

        return b\"\"

    @contextmanager
    def _error_catcher(self):
        \"\"\"
        Catch low-level python exceptions, instead re-raising urllib3
        variants, so that low-level exceptions are not leaked in the
        high-level api.

        On exit, release the connection back to the pool.
        \"\"\"
        clean_exit = False

        try:
            try:
                yield

            except SocketTimeout:
                # FIXME: Ideally we'd like to include the url in the ReadTimeoutError but
                # there is yet no clean way to get at it from this context.
                raise ReadTimeoutError(self._pool, None, \"Read timed out.\")

            except BaseSSLError as e:
                # FIXME: Is there a better way to differentiate between SSLErrors?
                if \"read operation timed out\" not in str(e):
                    # SSL errors related to framing/MAC get wrapped and reraised here
                    raise SSLError(e)

                raise ReadTimeoutError(self._pool, None, \"Read timed out.\")

            except (HTTPException, SocketError) as e:
                # This includes IncompleteRead.
                raise ProtocolError(\"Connection broken: %r\" % e, e)

            # If no exception is thrown, we should avoid cleaning up
            # unnecessarily.
            clean_exit = True
        finally:
            # If we didn't terminate cleanly, we need to throw away our
            # connection.
            if not clean_exit:
                # The response may not be closed but we're not going to use it
                # anymore so close it now to ensure that the connection is
                # released back to the pool.
                if self._original_response:
                    self._original_response.close()

                # Closing the response may not actually be sufficient to close
                # everything, so if we have a hold of the connection close that
                # too.
                if self._connection:
                    self._connection.close()

            # If we hold the original response but it's closed now, we should
            # return the connection back to the pool.
            if self._original_response and self._original_response.isclosed():
                self.release_conn()

    def read(self, amt=None, decode_content=None, cache_content=False):
        \"\"\"
        Similar to :meth:`http.client.HTTPResponse.read`, but with two additional
        parameters: ``decode_content`` and ``cache_content``.

        :param amt:
            How much of the content to read. If specified, caching is skipped
            because it doesn't make sense to cache partial content as the full
            response.

        :param decode_content:
            If True, will attempt to decode the body based on the
            'content-encoding' header.

        :param cache_content:
            If True, will save the returned data such that the same result is
            returned despite of the state of the underlying file object. This
            is useful if you want the ``.data`` property to continue working
            after having ``.read()`` the file object. (Overridden if ``amt`` is
            set.)
        \"\"\"
        self._init_decoder()
        if decode_content is None:
            decode_content = self.decode_content

        if self._fp is None:
            return

        flush_decoder = False
        fp_closed = getattr(self._fp, \"closed\", False)

        with self._error_catcher():
            if amt is None:
                # cStringIO doesn't like amt=None
                data = self._fp.read() if not fp_closed else b\"\"
                flush_decoder = True
            else:
                cache_content = False
                data = self._fp.read(amt) if not fp_closed else b\"\"
                if (
                    amt != 0 and not data
                ):  # Platform-specific: Buggy versions of Python.
                    # Close the connection when no data is returned
                    #
                    # This is redundant to what httplib/http.client _should_
                    # already do.  However, versions of python released before
                    # December 15, 2012 (http://bugs.python.org/issue16298) do
                    # not properly close the connection in all cases. There is
                    # no harm in redundantly calling close.
                    self._fp.close()
                    flush_decoder = True
                    if self.enforce_content_length and self.length_remaining not in (
                        0,
                        None,
                    ):
                        # This is an edge case that httplib failed to cover due
                        # to concerns of backward compatibility. We're
                        # addressing it here to make sure IncompleteRead is
                        # raised during streaming, so all calls with incorrect
                        # Content-Length are caught.
                        raise IncompleteRead(self._fp_bytes_read, self.length_remaining)

        if data:
            self._fp_bytes_read += len(data)
            if self.length_remaining is not None:
                self.length_remaining -= len(data)

            data = self._decode(data, decode_content, flush_decoder)

            if cache_content:
                self._body = data

        return data

    def stream(self, amt=2 ** 16, decode_content=None):
        \"\"\"
        A generator wrapper for the read() method. A call will block until
        ``amt`` bytes have been read from the connection or until the
        connection is closed.

        :param amt:
            How much of the content to read. The generator will return up to
            much data per iteration, but may return less. This is particularly
            likely when using compressed data. However, the empty string will
            never be returned.

        :param decode_content:
            If True, will attempt to decode the body based on the
            'content-encoding' header.
        \"\"\"
        if self.chunked and self.supports_chunked_reads():
            for line in self.read_chunked(amt, decode_content=decode_content):
                yield line
        else:
            while not is_fp_closed(self._fp):
                data = self.read(amt=amt, decode_content=decode_content)

                if data:
                    yield data

    @classmethod
    def from_httplib(ResponseCls, r, **response_kw):
        \"\"\"
        Given an :class:`http.client.HTTPResponse` instance ``r``, return a
        corresponding :class:`urllib3.response.HTTPResponse` object.

        Remaining parameters are passed to the HTTPResponse constructor, along
        with ``original_response=r``.
        \"\"\"
        headers = r.msg

        if not isinstance(headers, HTTPHeaderDict):
            if six.PY2:
                # Python 2.7
                headers = HTTPHeaderDict.from_httplib(headers)
            else:
                headers = HTTPHeaderDict(headers.items())

        # HTTPResponse objects in Python 3 don't have a .strict attribute
        strict = getattr(r, \"strict\", 0)
        resp = ResponseCls(
            body=r,
            headers=headers,
            status=r.status,
            version=r.version,
            reason=r.reason,
            strict=strict,
            original_response=r,
            **response_kw
        )
        return resp

    # Backwards-compatibility methods for http.client.HTTPResponse
    def getheaders(self):
        return self.headers

    def getheader(self, name, default=None):
        return self.headers.get(name, default)

    # Backwards compatibility for http.cookiejar
    def info(self):
        return self.headers

    # Overrides from io.IOBase
    def close(self):
        if not self.closed:
            self._fp.close()

        if self._connection:
            self._connection.close()

        if not self.auto_close:
            io.IOBase.close(self)

    @property
    def closed(self):
        if not self.auto_close:
            return io.IOBase.closed.__get__(self)
        elif self._fp is None:
            return True
        elif hasattr(self._fp, \"isclosed\"):
            return self._fp.isclosed()
        elif hasattr(self._fp, \"closed\"):
            return self._fp.closed
        else:
            return True

    def fileno(self):
        if self._fp is None:
            raise IOError(\"HTTPResponse has no file to get a fileno from\")
        elif hasattr(self._fp, \"fileno\"):
            return self._fp.fileno()
        else:
            raise IOError(
                \"The file-like object this HTTPResponse is wrapped \"
                \"around has no file descriptor\"
            )

    def flush(self):
        if (
            self._fp is not None
            and hasattr(self._fp, \"flush\")
            and not getattr(self._fp, \"closed\", False)
        ):
            return self._fp.flush()

    def readable(self):
        # This method is required for `io` module compatibility.
        return True

    def readinto(self, b):
        # This method is required for `io` module compatibility.
        temp = self.read(len(b))
        if len(temp) == 0:
            return 0
        else:
            b[: len(temp)] = temp
            return len(temp)

    def supports_chunked_reads(self):
        \"\"\"
        Checks if the underlying file-like object looks like a
        :class:`http.client.HTTPResponse` object. We do this by testing for
        the fp attribute. If it is present we assume it returns raw chunks as
        processed by read_chunked().
        \"\"\"
        return hasattr(self._fp, \"fp\")

    def _update_chunk_length(self):
        # First, we'll figure out length of a chunk and then
        # we'll try to read it from socket.
        if self.chunk_left is not None:
            return
        line = self._fp.fp.readline()
        line = line.split(b\";\", 1)[0]
        try:
            self.chunk_left = int(line, 16)
        except ValueError:
            # Invalid chunked protocol response, abort.
            self.close()
            raise InvalidChunkLength(self, line)

    def _handle_chunk(self, amt):
        returned_chunk = None
        if amt is None:
            chunk = self._fp._safe_read(self.chunk_left)
            returned_chunk = chunk
            self._fp._safe_read(2)  # Toss the CRLF at the end of the chunk.
            self.chunk_left = None
        elif amt < self.chunk_left:
            value = self._fp._safe_read(amt)
            self.chunk_left = self.chunk_left - amt
            returned_chunk = value
        elif amt == self.chunk_left:
            value = self._fp._safe_read(amt)
            self._fp._safe_read(2)  # Toss the CRLF at the end of the chunk.
            self.chunk_left = None
            returned_chunk = value
        else:  # amt > self.chunk_left
            returned_chunk = self._fp._safe_read(self.chunk_left)
            self._fp._safe_read(2)  # Toss the CRLF at the end of the chunk.
            self.chunk_left = None
        return returned_chunk

    def read_chunked(self, amt=None, decode_content=None):
        \"\"\"
        Similar to :meth:`HTTPResponse.read`, but with an additional
        parameter: ``decode_content``.

        :param amt:
            How much of the content to read. If specified, caching is skipped
            because it doesn't make sense to cache partial content as the full
            response.

        :param decode_content:
            If True, will attempt to decode the body based on the
            'content-encoding' header.
        \"\"\"
        self._init_decoder()
        # FIXME: Rewrite this method and make it a class with a better structured logic.
        if not self.chunked:
            raise ResponseNotChunked(
                \"Response is not chunked. \"
                \"Header 'transfer-encoding: chunked' is missing.\"
            )
        if not self.supports_chunked_reads():
            raise BodyNotHttplibCompatible(
                \"Body should be http.client.HTTPResponse like. \"
                \"It should have have an fp attribute which returns raw chunks.\"
            )

        with self._error_catcher():
            # Don't bother reading the body of a HEAD request.
            if self._original_response and is_response_to_head(self._original_response):
                self._original_response.close()
                return

            # If a response is already read and closed
            # then return immediately.
            if self._fp.fp is None:
                return

            while True:
                self._update_chunk_length()
                if self.chunk_left == 0:
                    break
                chunk = self._handle_chunk(amt)
                decoded = self._decode(
                    chunk, decode_content=decode_content, flush_decoder=False
                )
                if decoded:
                    yield decoded

            if decode_content:
                # On CPython and PyPy, we should never need to flush the
                # decoder. However, on Jython we *might* need to, so
                # lets defensively do it anyway.
                decoded = self._flush_decoder()
                if decoded:  # Platform-specific: Jython.
                    yield decoded

            # Chunk content ends with \\r\\n: discard it.
            while True:
                line = self._fp.fp.readline()
                if not line:
                    # Some sites may not end with '\\r\\n'.
                    break
                if line == b\"\\r\\n\":
                    break

            # We read everything; close the \"file\".
            if self._original_response:
                self._original_response.close()

    def geturl(self):
        \"\"\"
        Returns the URL that was the source of this response.
        If the request that generated this response redirected, this method
        will return the final redirect location.
        \"\"\"
        if self.retries is not None and len(self.retries.history):
            return self.retries.history[-1].redirect_location
        else:
            return self._request_url

    def __iter__(self):
        buffer = []
        for chunk in self.stream(decode_content=True):
            if b\"\\n\" in chunk:
                chunk = chunk.split(b\"\\n\")
                yield b\"\".join(buffer) + chunk[0] + b\"\\n\"
                for x in chunk[1:-1]:
                    yield x + b\"\\n\"
                if chunk[-1]:
                    buffer = [chunk[-1]]
                else:
                    buffer = []
            else:
                buffer.append(chunk)
        if buffer:
            yield b\"\".join(buffer)

"""
module_dict["urllib3"+os.sep+"exceptions.py"]="""
from __future__ import absolute_import

from .packages.six.moves.http_client import IncompleteRead as httplib_IncompleteRead

# Base Exceptions


class HTTPError(Exception):
    \"\"\"Base exception used by this module.\"\"\"

    pass


class HTTPWarning(Warning):
    \"\"\"Base warning used by this module.\"\"\"

    pass


class PoolError(HTTPError):
    \"\"\"Base exception for errors caused within a pool.\"\"\"

    def __init__(self, pool, message):
        self.pool = pool
        HTTPError.__init__(self, \"%s: %s\" % (pool, message))

    def __reduce__(self):
        # For pickling purposes.
        return self.__class__, (None, None)


class RequestError(PoolError):
    \"\"\"Base exception for PoolErrors that have associated URLs.\"\"\"

    def __init__(self, pool, url, message):
        self.url = url
        PoolError.__init__(self, pool, message)

    def __reduce__(self):
        # For pickling purposes.
        return self.__class__, (None, self.url, None)


class SSLError(HTTPError):
    \"\"\"Raised when SSL certificate fails in an HTTPS connection.\"\"\"

    pass


class ProxyError(HTTPError):
    \"\"\"Raised when the connection to a proxy fails.\"\"\"

    def __init__(self, message, error, *args):
        super(ProxyError, self).__init__(message, error, *args)
        self.original_error = error


class DecodeError(HTTPError):
    \"\"\"Raised when automatic decoding based on Content-Type fails.\"\"\"

    pass


class ProtocolError(HTTPError):
    \"\"\"Raised when something unexpected happens mid-request/response.\"\"\"

    pass


#: Renamed to ProtocolError but aliased for backwards compatibility.
ConnectionError = ProtocolError


# Leaf Exceptions


class MaxRetryError(RequestError):
    \"\"\"Raised when the maximum number of retries is exceeded.

    :param pool: The connection pool
    :type pool: :class:`~urllib3.connectionpool.HTTPConnectionPool`
    :param string url: The requested Url
    :param exceptions.Exception reason: The underlying error

    \"\"\"

    def __init__(self, pool, url, reason=None):
        self.reason = reason

        message = \"Max retries exceeded with url: %s (Caused by %r)\" % (url, reason)

        RequestError.__init__(self, pool, url, message)


class HostChangedError(RequestError):
    \"\"\"Raised when an existing pool gets a request for a foreign host.\"\"\"

    def __init__(self, pool, url, retries=3):
        message = \"Tried to open a foreign host with url: %s\" % url
        RequestError.__init__(self, pool, url, message)
        self.retries = retries


class TimeoutStateError(HTTPError):
    \"\"\"Raised when passing an invalid state to a timeout\"\"\"

    pass


class TimeoutError(HTTPError):
    \"\"\"Raised when a socket timeout error occurs.

    Catching this error will catch both :exc:`ReadTimeoutErrors
    <ReadTimeoutError>` and :exc:`ConnectTimeoutErrors <ConnectTimeoutError>`.
    \"\"\"

    pass


class ReadTimeoutError(TimeoutError, RequestError):
    \"\"\"Raised when a socket timeout occurs while receiving data from a server\"\"\"

    pass


# This timeout error does not have a URL attached and needs to inherit from the
# base HTTPError
class ConnectTimeoutError(TimeoutError):
    \"\"\"Raised when a socket timeout occurs while connecting to a server\"\"\"

    pass


class NewConnectionError(ConnectTimeoutError, PoolError):
    \"\"\"Raised when we fail to establish a new connection. Usually ECONNREFUSED.\"\"\"

    pass


class EmptyPoolError(PoolError):
    \"\"\"Raised when a pool runs out of connections and no more are allowed.\"\"\"

    pass


class ClosedPoolError(PoolError):
    \"\"\"Raised when a request enters a pool after the pool has been closed.\"\"\"

    pass


class LocationValueError(ValueError, HTTPError):
    \"\"\"Raised when there is something wrong with a given URL input.\"\"\"

    pass


class LocationParseError(LocationValueError):
    \"\"\"Raised when get_host or similar fails to parse the URL input.\"\"\"

    def __init__(self, location):
        message = \"Failed to parse: %s\" % location
        HTTPError.__init__(self, message)

        self.location = location


class URLSchemeUnknown(LocationValueError):
    \"\"\"Raised when a URL input has an unsupported scheme.\"\"\"

    def __init__(self, scheme):
        message = \"Not supported URL scheme %s\" % scheme
        super(URLSchemeUnknown, self).__init__(message)

        self.scheme = scheme


class ResponseError(HTTPError):
    \"\"\"Used as a container for an error reason supplied in a MaxRetryError.\"\"\"

    GENERIC_ERROR = \"too many error responses\"
    SPECIFIC_ERROR = \"too many {status_code} error responses\"


class SecurityWarning(HTTPWarning):
    \"\"\"Warned when performing security reducing actions\"\"\"

    pass


class SubjectAltNameWarning(SecurityWarning):
    \"\"\"Warned when connecting to a host with a certificate missing a SAN.\"\"\"

    pass


class InsecureRequestWarning(SecurityWarning):
    \"\"\"Warned when making an unverified HTTPS request.\"\"\"

    pass


class SystemTimeWarning(SecurityWarning):
    \"\"\"Warned when system time is suspected to be wrong\"\"\"

    pass


class InsecurePlatformWarning(SecurityWarning):
    \"\"\"Warned when certain TLS/SSL configuration is not available on a platform.\"\"\"

    pass


class SNIMissingWarning(HTTPWarning):
    \"\"\"Warned when making a HTTPS request without SNI available.\"\"\"

    pass


class DependencyWarning(HTTPWarning):
    \"\"\"
    Warned when an attempt is made to import a module with missing optional
    dependencies.
    \"\"\"

    pass


class ResponseNotChunked(ProtocolError, ValueError):
    \"\"\"Response needs to be chunked in order to read it as chunks.\"\"\"

    pass


class BodyNotHttplibCompatible(HTTPError):
    \"\"\"
    Body should be :class:`http.client.HTTPResponse` like
    (have an fp attribute which returns raw chunks) for read_chunked().
    \"\"\"

    pass


class IncompleteRead(HTTPError, httplib_IncompleteRead):
    \"\"\"
    Response length doesn't match expected Content-Length

    Subclass of :class:`http.client.IncompleteRead` to allow int value
    for ``partial`` to avoid creating large objects on streamed reads.
    \"\"\"

    def __init__(self, partial, expected):
        super(IncompleteRead, self).__init__(partial, expected)

    def __repr__(self):
        return \"IncompleteRead(%i bytes read, %i more expected)\" % (
            self.partial,
            self.expected,
        )


class InvalidChunkLength(HTTPError, httplib_IncompleteRead):
    \"\"\"Invalid chunk length in a chunked response.\"\"\"

    def __init__(self, response, length):
        super(InvalidChunkLength, self).__init__(
            response.tell(), response.length_remaining
        )
        self.response = response
        self.length = length

    def __repr__(self):
        return \"InvalidChunkLength(got length %r, %i bytes read)\" % (
            self.length,
            self.partial,
        )


class InvalidHeader(HTTPError):
    \"\"\"The header provided was somehow invalid.\"\"\"

    pass


class ProxySchemeUnknown(AssertionError, URLSchemeUnknown):
    \"\"\"ProxyManager does not support the supplied scheme\"\"\"

    # TODO(t-8ch): Stop inheriting from AssertionError in v2.0.

    def __init__(self, scheme):
        # 'localhost' is here because our URL parser parses
        # localhost:8080 -> scheme=localhost, remove if we fix this.
        if scheme == \"localhost\":
            scheme = None
        if scheme is None:
            message = \"Proxy URL had no scheme, should start with http:// or https://\"
        else:
            message = (
                \"Proxy URL had unsupported scheme %s, should use http:// or https://\"
                % scheme
            )
        super(ProxySchemeUnknown, self).__init__(message)


class ProxySchemeUnsupported(ValueError):
    \"\"\"Fetching HTTPS resources through HTTPS proxies is unsupported\"\"\"

    pass


class HeaderParsingError(HTTPError):
    \"\"\"Raised by assert_header_parsing, but we convert it to a log.warning statement.\"\"\"

    def __init__(self, defects, unparsed_data):
        message = \"%s, unparsed data: %r\" % (defects or \"Unknown\", unparsed_data)
        super(HeaderParsingError, self).__init__(message)


class UnrewindableBodyError(HTTPError):
    \"\"\"urllib3 encountered an error when trying to rewind a body\"\"\"

    pass

"""
module_dict["urllib3"+os.sep+"filepost.py"]="""
from __future__ import absolute_import

import binascii
import codecs
import os
from io import BytesIO

from .fields import RequestField
from .packages import six
from .packages.six import b

writer = codecs.lookup(\"utf-8\")[3]


def choose_boundary():
    \"\"\"
    Our embarrassingly-simple replacement for mimetools.choose_boundary.
    \"\"\"
    boundary = binascii.hexlify(os.urandom(16))
    if not six.PY2:
        boundary = boundary.decode(\"ascii\")
    return boundary


def iter_field_objects(fields):
    \"\"\"
    Iterate over fields.

    Supports list of (k, v) tuples and dicts, and lists of
    :class:`~urllib3.fields.RequestField`.

    \"\"\"
    if isinstance(fields, dict):
        i = six.iteritems(fields)
    else:
        i = iter(fields)

    for field in i:
        if isinstance(field, RequestField):
            yield field
        else:
            yield RequestField.from_tuples(*field)


def iter_fields(fields):
    \"\"\"
    .. deprecated:: 1.6

    Iterate over fields.

    The addition of :class:`~urllib3.fields.RequestField` makes this function
    obsolete. Instead, use :func:`iter_field_objects`, which returns
    :class:`~urllib3.fields.RequestField` objects.

    Supports list of (k, v) tuples and dicts.
    \"\"\"
    if isinstance(fields, dict):
        return ((k, v) for k, v in six.iteritems(fields))

    return ((k, v) for k, v in fields)


def encode_multipart_formdata(fields, boundary=None):
    \"\"\"
    Encode a dictionary of ``fields`` using the multipart/form-data MIME format.

    :param fields:
        Dictionary of fields or list of (key, :class:`~urllib3.fields.RequestField`).

    :param boundary:
        If not specified, then a random boundary will be generated using
        :func:`urllib3.filepost.choose_boundary`.
    \"\"\"
    body = BytesIO()
    if boundary is None:
        boundary = choose_boundary()

    for field in iter_field_objects(fields):
        body.write(b(\"--%s\\r\\n\" % (boundary)))

        writer(body).write(field.render_headers())
        data = field.data

        if isinstance(data, int):
            data = str(data)  # Backwards compatibility

        if isinstance(data, six.text_type):
            writer(body).write(data)
        else:
            body.write(data)

        body.write(b\"\\r\\n\")

    body.write(b(\"--%s--\\r\\n\" % (boundary)))

    content_type = str(\"multipart/form-data; boundary=%s\" % boundary)

    return body.getvalue(), content_type

"""
module_dict["urllib3"+os.sep+"__init__.py"]="""
\"\"\"
Python HTTP library with thread-safe connection pooling, file post support, user friendly, and more
\"\"\"
from __future__ import absolute_import

# Set default logging handler to avoid \"No handler found\" warnings.
import logging
import warnings
from logging import NullHandler

from . import exceptions
from ._version import __version__
from .connectionpool import HTTPConnectionPool, HTTPSConnectionPool, connection_from_url
from .filepost import encode_multipart_formdata
from .poolmanager import PoolManager, ProxyManager, proxy_from_url
from .response import HTTPResponse
from .util.request import make_headers
from .util.retry import Retry
from .util.timeout import Timeout
from .util.url import get_host

__author__ = \"Andrey Petrov (andrey.petrov@shazow.net)\"
__license__ = \"MIT\"
__version__ = __version__

__all__ = (
    \"HTTPConnectionPool\",
    \"HTTPSConnectionPool\",
    \"PoolManager\",
    \"ProxyManager\",
    \"HTTPResponse\",
    \"Retry\",
    \"Timeout\",
    \"add_stderr_logger\",
    \"connection_from_url\",
    \"disable_warnings\",
    \"encode_multipart_formdata\",
    \"get_host\",
    \"make_headers\",
    \"proxy_from_url\",
)

logging.getLogger(__name__).addHandler(NullHandler())


def add_stderr_logger(level=logging.DEBUG):
    \"\"\"
    Helper for quickly adding a StreamHandler to the logger. Useful for
    debugging.

    Returns the handler after adding it.
    \"\"\"
    # This method needs to be in this __init__.py to get the __name__ correct
    # even if urllib3 is vendored within another package.
    logger = logging.getLogger(__name__)
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(\"%(asctime)s %(levelname)s %(message)s\"))
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.debug(\"Added a stderr logging handler to logger: %s\", __name__)
    return handler


# ... Clean up.
del NullHandler


# All warning filters *must* be appended unless you're really certain that they
# shouldn't be: otherwise, it's very hard for users to use most Python
# mechanisms to silence them.
# SecurityWarning's always go off by default.
warnings.simplefilter(\"always\", exceptions.SecurityWarning, append=True)
# SubjectAltNameWarning's should go off once per host
warnings.simplefilter(\"default\", exceptions.SubjectAltNameWarning, append=True)
# InsecurePlatformWarning's don't vary between requests, so we keep it default.
warnings.simplefilter(\"default\", exceptions.InsecurePlatformWarning, append=True)
# SNIMissingWarnings should go off only once.
warnings.simplefilter(\"default\", exceptions.SNIMissingWarning, append=True)


def disable_warnings(category=exceptions.HTTPWarning):
    \"\"\"
    Helper for quickly disabling all urllib3 warnings.
    \"\"\"
    warnings.simplefilter(\"ignore\", category)

"""
module_dict["urllib3"+os.sep+"_version.py"]="""
# This file is protected via CODEOWNERS
__version__ = \"1.26.10\"

"""
module_dict["urllib3"+os.sep+"packages"+os.sep+"six.py"]="""
# Copyright (c) 2010-2020 Benjamin Peterson
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the \"Software\"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED \"AS IS\", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

\"\"\"Utilities for writing code that runs on Python 2 and 3\"\"\"

from __future__ import absolute_import
# < include 'StringIO.py' >


import functools
import itertools
import operator
import sys
import types

__author__ = \"Benjamin Peterson <benjamin@python.org>\"
__version__ = \"1.16.0\"


# Useful for very coarse version differentiation.
PY2 = sys.version_info[0] == 2
PY3 = sys.version_info[0] == 3
PY34 = sys.version_info[0:2] >= (3, 4)

if PY3:
    string_types = (str,)
    integer_types = (int,)
    class_types = (type,)
    text_type = str
    binary_type = bytes

    MAXSIZE = sys.maxsize
else:
    string_types = (basestring,)
    integer_types = (int, long)
    class_types = (type, types.ClassType)
    text_type = unicode
    binary_type = str

    if sys.platform.startswith(\"java\"):
        # Jython always uses 32 bits.
        MAXSIZE = int((1 << 31) - 1)
    else:
        # It's possible to have sizeof(long) != sizeof(Py_ssize_t).
        class X(object):
            def __len__(self):
                return 1 << 31

        try:
            len(X())
        except OverflowError:
            # 32-bit
            MAXSIZE = int((1 << 31) - 1)
        else:
            # 64-bit
            MAXSIZE = int((1 << 63) - 1)
        del X

if PY34:
    from importlib.util import spec_from_loader
else:
    spec_from_loader = None


def _add_doc(func, doc):
    \"\"\"Add documentation to a function.\"\"\"
    func.__doc__ = doc


def _import_module(name):
    \"\"\"Import module, returning the module after the last dot.\"\"\"
    __import__(name)
    return sys.modules[name]


class _LazyDescr(object):
    def __init__(self, name):
        self.name = name

    def __get__(self, obj, tp):
        result = self._resolve()
        setattr(obj, self.name, result)  # Invokes __set__.
        try:
            # This is a bit ugly, but it avoids running this again by
            # removing this descriptor.
            delattr(obj.__class__, self.name)
        except AttributeError:
            pass
        return result


class MovedModule(_LazyDescr):
    def __init__(self, name, old, new=None):
        super(MovedModule, self).__init__(name)
        if PY3:
            if new is None:
                new = name
            self.mod = new
        else:
            self.mod = old

    def _resolve(self):
        return _import_module(self.mod)

    def __getattr__(self, attr):
        _module = self._resolve()
        value = getattr(_module, attr)
        setattr(self, attr, value)
        return value


class _LazyModule(types.ModuleType):
    def __init__(self, name):
        super(_LazyModule, self).__init__(name)
        self.__doc__ = self.__class__.__doc__

    def __dir__(self):
        attrs = [\"__doc__\", \"__name__\"]
        attrs += [attr.name for attr in self._moved_attributes]
        return attrs

    # Subclasses should override this
    _moved_attributes = []


class MovedAttribute(_LazyDescr):
    def __init__(self, name, old_mod, new_mod, old_attr=None, new_attr=None):
        super(MovedAttribute, self).__init__(name)
        if PY3:
            if new_mod is None:
                new_mod = name
            self.mod = new_mod
            if new_attr is None:
                if old_attr is None:
                    new_attr = name
                else:
                    new_attr = old_attr
            self.attr = new_attr
        else:
            self.mod = old_mod
            if old_attr is None:
                old_attr = name
            self.attr = old_attr

    def _resolve(self):
        module = _import_module(self.mod)
        return getattr(module, self.attr)


class _SixMetaPathImporter(object):

    \"\"\"
    A meta path importer to import six.moves and its submodules.

    This class implements a PEP302 finder and loader. It should be compatible
    with Python 2.5 and all existing versions of Python3
    \"\"\"

    def __init__(self, six_module_name):
        self.name = six_module_name
        self.known_modules = {}

    def _add_module(self, mod, *fullnames):
        for fullname in fullnames:
            self.known_modules[self.name + \".\" + fullname] = mod

    def _get_module(self, fullname):
        return self.known_modules[self.name + \".\" + fullname]

    def find_module(self, fullname, path=None):
        if fullname in self.known_modules:
            return self
        return None

    def find_spec(self, fullname, path, target=None):
        if fullname in self.known_modules:
            return spec_from_loader(fullname, self)
        return None

    def __get_module(self, fullname):
        try:
            return self.known_modules[fullname]
        except KeyError:
            raise ImportError(\"This loader does not know module \" + fullname)

    def load_module(self, fullname):
        try:
            # in case of a reload
            return sys.modules[fullname]
        except KeyError:
            pass
        mod = self.__get_module(fullname)
        if isinstance(mod, MovedModule):
            mod = mod._resolve()
        else:
            mod.__loader__ = self
        sys.modules[fullname] = mod
        return mod

    def is_package(self, fullname):
        \"\"\"
        Return true, if the named module is a package.

        We need this method to get correct spec objects with
        Python 3.4 (see PEP451)
        \"\"\"
        return hasattr(self.__get_module(fullname), \"__path__\")

    def get_code(self, fullname):
        \"\"\"Return None

        Required, if is_package is implemented\"\"\"
        self.__get_module(fullname)  # eventually raises ImportError
        return None

    get_source = get_code  # same as get_code

    def create_module(self, spec):
        return self.load_module(spec.name)

    def exec_module(self, module):
        pass


_importer = _SixMetaPathImporter(__name__)


class _MovedItems(_LazyModule):

    \"\"\"Lazy loading of moved objects\"\"\"

    __path__ = []  # mark as package


_moved_attributes = [
    MovedAttribute(\"cStringIO\", \"cStringIO\", \"io\", \"StringIO\"),
    MovedAttribute(\"filter\", \"itertools\", \"builtins\", \"ifilter\", \"filter\"),
    MovedAttribute(
        \"filterfalse\", \"itertools\", \"itertools\", \"ifilterfalse\", \"filterfalse\"
    ),
    MovedAttribute(\"input\", \"__builtin__\", \"builtins\", \"raw_input\", \"input\"),
    MovedAttribute(\"intern\", \"__builtin__\", \"sys\"),
    MovedAttribute(\"map\", \"itertools\", \"builtins\", \"imap\", \"map\"),
    MovedAttribute(\"getcwd\", \"os\", \"os\", \"getcwdu\", \"getcwd\"),
    MovedAttribute(\"getcwdb\", \"os\", \"os\", \"getcwd\", \"getcwdb\"),
    MovedAttribute(\"getoutput\", \"commands\", \"subprocess\"),
    MovedAttribute(\"range\", \"__builtin__\", \"builtins\", \"xrange\", \"range\"),
    MovedAttribute(
        \"reload_module\", \"__builtin__\", \"importlib\" if PY34 else \"imp\", \"reload\"
    ),
    MovedAttribute(\"reduce\", \"__builtin__\", \"functools\"),
    MovedAttribute(\"shlex_quote\", \"pipes\", \"shlex\", \"quote\"),
    MovedAttribute(\"StringIO\", \"StringIO\", \"io\"),
    MovedAttribute(\"UserDict\", \"UserDict\", \"collections\"),
    MovedAttribute(\"UserList\", \"UserList\", \"collections\"),
    MovedAttribute(\"UserString\", \"UserString\", \"collections\"),
    MovedAttribute(\"xrange\", \"__builtin__\", \"builtins\", \"xrange\", \"range\"),
    MovedAttribute(\"zip\", \"itertools\", \"builtins\", \"izip\", \"zip\"),
    MovedAttribute(
        \"zip_longest\", \"itertools\", \"itertools\", \"izip_longest\", \"zip_longest\"
    ),
    MovedModule(\"builtins\", \"__builtin__\"),
    MovedModule(\"configparser\", \"ConfigParser\"),
    MovedModule(
        \"collections_abc\",
        \"collections\",
        \"collections.abc\" if sys.version_info >= (3, 3) else \"collections\",
    ),
    MovedModule(\"copyreg\", \"copy_reg\"),
    MovedModule(\"dbm_gnu\", \"gdbm\", \"dbm.gnu\"),
    MovedModule(\"dbm_ndbm\", \"dbm\", \"dbm.ndbm\"),
    MovedModule(
        \"_dummy_thread\",
        \"dummy_thread\",
        \"_dummy_thread\" if sys.version_info < (3, 9) else \"_thread\",
    ),
    MovedModule(\"http_cookiejar\", \"cookielib\", \"http.cookiejar\"),
    MovedModule(\"http_cookies\", \"Cookie\", \"http.cookies\"),
    MovedModule(\"html_entities\", \"htmlentitydefs\", \"html.entities\"),
    MovedModule(\"html_parser\", \"HTMLParser\", \"html.parser\"),
    MovedModule(\"http_client\", \"httplib\", \"http.client\"),
    MovedModule(\"email_mime_base\", \"email.MIMEBase\", \"email.mime.base\"),
    MovedModule(\"email_mime_image\", \"email.MIMEImage\", \"email.mime.image\"),
    MovedModule(\"email_mime_multipart\", \"email.MIMEMultipart\", \"email.mime.multipart\"),
    MovedModule(
        \"email_mime_nonmultipart\", \"email.MIMENonMultipart\", \"email.mime.nonmultipart\"
    ),
    MovedModule(\"email_mime_text\", \"email.MIMEText\", \"email.mime.text\"),
    MovedModule(\"BaseHTTPServer\", \"BaseHTTPServer\", \"http.server\"),
    MovedModule(\"CGIHTTPServer\", \"CGIHTTPServer\", \"http.server\"),
    MovedModule(\"SimpleHTTPServer\", \"SimpleHTTPServer\", \"http.server\"),
    MovedModule(\"cPickle\", \"cPickle\", \"pickle\"),
    MovedModule(\"queue\", \"Queue\"),
    MovedModule(\"reprlib\", \"repr\"),
    MovedModule(\"socketserver\", \"SocketServer\"),
    MovedModule(\"_thread\", \"thread\", \"_thread\"),
    MovedModule(\"tkinter\", \"Tkinter\"),
    MovedModule(\"tkinter_dialog\", \"Dialog\", \"tkinter.dialog\"),
    MovedModule(\"tkinter_filedialog\", \"FileDialog\", \"tkinter.filedialog\"),
    MovedModule(\"tkinter_scrolledtext\", \"ScrolledText\", \"tkinter.scrolledtext\"),
    MovedModule(\"tkinter_simpledialog\", \"SimpleDialog\", \"tkinter.simpledialog\"),
    MovedModule(\"tkinter_tix\", \"Tix\", \"tkinter.tix\"),
    MovedModule(\"tkinter_ttk\", \"ttk\", \"tkinter.ttk\"),
    MovedModule(\"tkinter_constants\", \"Tkconstants\", \"tkinter.constants\"),
    MovedModule(\"tkinter_dnd\", \"Tkdnd\", \"tkinter.dnd\"),
    MovedModule(\"tkinter_colorchooser\", \"tkColorChooser\", \"tkinter.colorchooser\"),
    MovedModule(\"tkinter_commondialog\", \"tkCommonDialog\", \"tkinter.commondialog\"),
    MovedModule(\"tkinter_tkfiledialog\", \"tkFileDialog\", \"tkinter.filedialog\"),
    MovedModule(\"tkinter_font\", \"tkFont\", \"tkinter.font\"),
    MovedModule(\"tkinter_messagebox\", \"tkMessageBox\", \"tkinter.messagebox\"),
    MovedModule(\"tkinter_tksimpledialog\", \"tkSimpleDialog\", \"tkinter.simpledialog\"),
    MovedModule(\"urllib_parse\", __name__ + \".moves.urllib_parse\", \"urllib.parse\"),
    MovedModule(\"urllib_error\", __name__ + \".moves.urllib_error\", \"urllib.error\"),
    MovedModule(\"urllib\", __name__ + \".moves.urllib\", __name__ + \".moves.urllib\"),
    MovedModule(\"urllib_robotparser\", \"robotparser\", \"urllib.robotparser\"),
    MovedModule(\"xmlrpc_client\", \"xmlrpclib\", \"xmlrpc.client\"),
    MovedModule(\"xmlrpc_server\", \"SimpleXMLRPCServer\", \"xmlrpc.server\"),
]
# Add windows specific modules.
if sys.platform == \"win32\":
    _moved_attributes += [
        MovedModule(\"winreg\", \"_winreg\"),
    ]

for attr in _moved_attributes:
    setattr(_MovedItems, attr.name, attr)
    if isinstance(attr, MovedModule):
        _importer._add_module(attr, \"moves.\" + attr.name)
del attr

_MovedItems._moved_attributes = _moved_attributes

moves = _MovedItems(__name__ + \".moves\")
_importer._add_module(moves, \"moves\")


class Module_six_moves_urllib_parse(_LazyModule):

    \"\"\"Lazy loading of moved objects in six.moves.urllib_parse\"\"\"


_urllib_parse_moved_attributes = [
    MovedAttribute(\"ParseResult\", \"urlparse\", \"urllib.parse\"),
    MovedAttribute(\"SplitResult\", \"urlparse\", \"urllib.parse\"),
    MovedAttribute(\"parse_qs\", \"urlparse\", \"urllib.parse\"),
    MovedAttribute(\"parse_qsl\", \"urlparse\", \"urllib.parse\"),
    MovedAttribute(\"urldefrag\", \"urlparse\", \"urllib.parse\"),
    MovedAttribute(\"urljoin\", \"urlparse\", \"urllib.parse\"),
    MovedAttribute(\"urlparse\", \"urlparse\", \"urllib.parse\"),
    MovedAttribute(\"urlsplit\", \"urlparse\", \"urllib.parse\"),
    MovedAttribute(\"urlunparse\", \"urlparse\", \"urllib.parse\"),
    MovedAttribute(\"urlunsplit\", \"urlparse\", \"urllib.parse\"),
    MovedAttribute(\"quote\", \"urllib\", \"urllib.parse\"),
    MovedAttribute(\"quote_plus\", \"urllib\", \"urllib.parse\"),
    MovedAttribute(\"unquote\", \"urllib\", \"urllib.parse\"),
    MovedAttribute(\"unquote_plus\", \"urllib\", \"urllib.parse\"),
    MovedAttribute(
        \"unquote_to_bytes\", \"urllib\", \"urllib.parse\", \"unquote\", \"unquote_to_bytes\"
    ),
    MovedAttribute(\"urlencode\", \"urllib\", \"urllib.parse\"),
    MovedAttribute(\"splitquery\", \"urllib\", \"urllib.parse\"),
    MovedAttribute(\"splittag\", \"urllib\", \"urllib.parse\"),
    MovedAttribute(\"splituser\", \"urllib\", \"urllib.parse\"),
    MovedAttribute(\"splitvalue\", \"urllib\", \"urllib.parse\"),
    MovedAttribute(\"uses_fragment\", \"urlparse\", \"urllib.parse\"),
    MovedAttribute(\"uses_netloc\", \"urlparse\", \"urllib.parse\"),
    MovedAttribute(\"uses_params\", \"urlparse\", \"urllib.parse\"),
    MovedAttribute(\"uses_query\", \"urlparse\", \"urllib.parse\"),
    MovedAttribute(\"uses_relative\", \"urlparse\", \"urllib.parse\"),
]
for attr in _urllib_parse_moved_attributes:
    setattr(Module_six_moves_urllib_parse, attr.name, attr)
del attr

Module_six_moves_urllib_parse._moved_attributes = _urllib_parse_moved_attributes

_importer._add_module(
    Module_six_moves_urllib_parse(__name__ + \".moves.urllib_parse\"),
    \"moves.urllib_parse\",
    \"moves.urllib.parse\",
)


class Module_six_moves_urllib_error(_LazyModule):

    \"\"\"Lazy loading of moved objects in six.moves.urllib_error\"\"\"


_urllib_error_moved_attributes = [
    MovedAttribute(\"URLError\", \"urllib2\", \"urllib.error\"),
    MovedAttribute(\"HTTPError\", \"urllib2\", \"urllib.error\"),
    MovedAttribute(\"ContentTooShortError\", \"urllib\", \"urllib.error\"),
]
for attr in _urllib_error_moved_attributes:
    setattr(Module_six_moves_urllib_error, attr.name, attr)
del attr

Module_six_moves_urllib_error._moved_attributes = _urllib_error_moved_attributes

_importer._add_module(
    Module_six_moves_urllib_error(__name__ + \".moves.urllib.error\"),
    \"moves.urllib_error\",
    \"moves.urllib.error\",
)


class Module_six_moves_urllib_request(_LazyModule):

    \"\"\"Lazy loading of moved objects in six.moves.urllib_request\"\"\"


_urllib_request_moved_attributes = [
    MovedAttribute(\"urlopen\", \"urllib2\", \"urllib.request\"),
    MovedAttribute(\"install_opener\", \"urllib2\", \"urllib.request\"),
    MovedAttribute(\"build_opener\", \"urllib2\", \"urllib.request\"),
    MovedAttribute(\"pathname2url\", \"urllib\", \"urllib.request\"),
    MovedAttribute(\"url2pathname\", \"urllib\", \"urllib.request\"),
    MovedAttribute(\"getproxies\", \"urllib\", \"urllib.request\"),
    MovedAttribute(\"Request\", \"urllib2\", \"urllib.request\"),
    MovedAttribute(\"OpenerDirector\", \"urllib2\", \"urllib.request\"),
    MovedAttribute(\"HTTPDefaultErrorHandler\", \"urllib2\", \"urllib.request\"),
    MovedAttribute(\"HTTPRedirectHandler\", \"urllib2\", \"urllib.request\"),
    MovedAttribute(\"HTTPCookieProcessor\", \"urllib2\", \"urllib.request\"),
    MovedAttribute(\"ProxyHandler\", \"urllib2\", \"urllib.request\"),
    MovedAttribute(\"BaseHandler\", \"urllib2\", \"urllib.request\"),
    MovedAttribute(\"HTTPPasswordMgr\", \"urllib2\", \"urllib.request\"),
    MovedAttribute(\"HTTPPasswordMgrWithDefaultRealm\", \"urllib2\", \"urllib.request\"),
    MovedAttribute(\"AbstractBasicAuthHandler\", \"urllib2\", \"urllib.request\"),
    MovedAttribute(\"HTTPBasicAuthHandler\", \"urllib2\", \"urllib.request\"),
    MovedAttribute(\"ProxyBasicAuthHandler\", \"urllib2\", \"urllib.request\"),
    MovedAttribute(\"AbstractDigestAuthHandler\", \"urllib2\", \"urllib.request\"),
    MovedAttribute(\"HTTPDigestAuthHandler\", \"urllib2\", \"urllib.request\"),
    MovedAttribute(\"ProxyDigestAuthHandler\", \"urllib2\", \"urllib.request\"),
    MovedAttribute(\"HTTPHandler\", \"urllib2\", \"urllib.request\"),
    MovedAttribute(\"HTTPSHandler\", \"urllib2\", \"urllib.request\"),
    MovedAttribute(\"FileHandler\", \"urllib2\", \"urllib.request\"),
    MovedAttribute(\"FTPHandler\", \"urllib2\", \"urllib.request\"),
    MovedAttribute(\"CacheFTPHandler\", \"urllib2\", \"urllib.request\"),
    MovedAttribute(\"UnknownHandler\", \"urllib2\", \"urllib.request\"),
    MovedAttribute(\"HTTPErrorProcessor\", \"urllib2\", \"urllib.request\"),
    MovedAttribute(\"urlretrieve\", \"urllib\", \"urllib.request\"),
    MovedAttribute(\"urlcleanup\", \"urllib\", \"urllib.request\"),
    MovedAttribute(\"URLopener\", \"urllib\", \"urllib.request\"),
    MovedAttribute(\"FancyURLopener\", \"urllib\", \"urllib.request\"),
    MovedAttribute(\"proxy_bypass\", \"urllib\", \"urllib.request\"),
    MovedAttribute(\"parse_http_list\", \"urllib2\", \"urllib.request\"),
    MovedAttribute(\"parse_keqv_list\", \"urllib2\", \"urllib.request\"),
]
for attr in _urllib_request_moved_attributes:
    setattr(Module_six_moves_urllib_request, attr.name, attr)
del attr

Module_six_moves_urllib_request._moved_attributes = _urllib_request_moved_attributes

_importer._add_module(
    Module_six_moves_urllib_request(__name__ + \".moves.urllib.request\"),
    \"moves.urllib_request\",
    \"moves.urllib.request\",
)


class Module_six_moves_urllib_response(_LazyModule):

    \"\"\"Lazy loading of moved objects in six.moves.urllib_response\"\"\"


_urllib_response_moved_attributes = [
    MovedAttribute(\"addbase\", \"urllib\", \"urllib.response\"),
    MovedAttribute(\"addclosehook\", \"urllib\", \"urllib.response\"),
    MovedAttribute(\"addinfo\", \"urllib\", \"urllib.response\"),
    MovedAttribute(\"addinfourl\", \"urllib\", \"urllib.response\"),
]
for attr in _urllib_response_moved_attributes:
    setattr(Module_six_moves_urllib_response, attr.name, attr)
del attr

Module_six_moves_urllib_response._moved_attributes = _urllib_response_moved_attributes

_importer._add_module(
    Module_six_moves_urllib_response(__name__ + \".moves.urllib.response\"),
    \"moves.urllib_response\",
    \"moves.urllib.response\",
)


class Module_six_moves_urllib_robotparser(_LazyModule):

    \"\"\"Lazy loading of moved objects in six.moves.urllib_robotparser\"\"\"


_urllib_robotparser_moved_attributes = [
    MovedAttribute(\"RobotFileParser\", \"robotparser\", \"urllib.robotparser\"),
]
for attr in _urllib_robotparser_moved_attributes:
    setattr(Module_six_moves_urllib_robotparser, attr.name, attr)
del attr

Module_six_moves_urllib_robotparser._moved_attributes = (
    _urllib_robotparser_moved_attributes
)

_importer._add_module(
    Module_six_moves_urllib_robotparser(__name__ + \".moves.urllib.robotparser\"),
    \"moves.urllib_robotparser\",
    \"moves.urllib.robotparser\",
)


class Module_six_moves_urllib(types.ModuleType):

    \"\"\"Create a six.moves.urllib namespace that resembles the Python 3 namespace\"\"\"

    __path__ = []  # mark as package
    parse = _importer._get_module(\"moves.urllib_parse\")
    error = _importer._get_module(\"moves.urllib_error\")
    request = _importer._get_module(\"moves.urllib_request\")
    response = _importer._get_module(\"moves.urllib_response\")
    robotparser = _importer._get_module(\"moves.urllib_robotparser\")

    def __dir__(self):
        return [\"parse\", \"error\", \"request\", \"response\", \"robotparser\"]


_importer._add_module(
    Module_six_moves_urllib(__name__ + \".moves.urllib\"), \"moves.urllib\"
)


def add_move(move):
    \"\"\"Add an item to six.moves.\"\"\"
    setattr(_MovedItems, move.name, move)


def remove_move(name):
    \"\"\"Remove item from six.moves.\"\"\"
    try:
        delattr(_MovedItems, name)
    except AttributeError:
        try:
            del moves.__dict__[name]
        except KeyError:
            raise AttributeError(\"no such move, %r\" % (name,))


if PY3:
    _meth_func = \"__func__\"
    _meth_self = \"__self__\"

    _func_closure = \"__closure__\"
    _func_code = \"__code__\"
    _func_defaults = \"__defaults__\"
    _func_globals = \"__globals__\"
else:
    _meth_func = \"im_func\"
    _meth_self = \"im_self\"

    _func_closure = \"func_closure\"
    _func_code = \"func_code\"
    _func_defaults = \"func_defaults\"
    _func_globals = \"func_globals\"


try:
    advance_iterator = next
except NameError:

    def advance_iterator(it):
        return it.next()


next = advance_iterator


try:
    callable = callable
except NameError:

    def callable(obj):
        return any(\"__call__\" in klass.__dict__ for klass in type(obj).__mro__)


if PY3:

    def get_unbound_function(unbound):
        return unbound

    create_bound_method = types.MethodType

    def create_unbound_method(func, cls):
        return func

    Iterator = object
else:

    def get_unbound_function(unbound):
        return unbound.im_func

    def create_bound_method(func, obj):
        return types.MethodType(func, obj, obj.__class__)

    def create_unbound_method(func, cls):
        return types.MethodType(func, None, cls)

    class Iterator(object):
        def next(self):
            return type(self).__next__(self)

    callable = callable
_add_doc(
    get_unbound_function, \"\"\"Get the function out of a possibly unbound function\"\"\"
)


get_method_function = operator.attrgetter(_meth_func)
get_method_self = operator.attrgetter(_meth_self)
get_function_closure = operator.attrgetter(_func_closure)
get_function_code = operator.attrgetter(_func_code)
get_function_defaults = operator.attrgetter(_func_defaults)
get_function_globals = operator.attrgetter(_func_globals)


if PY3:

    def iterkeys(d, **kw):
        return iter(d.keys(**kw))

    def itervalues(d, **kw):
        return iter(d.values(**kw))

    def iteritems(d, **kw):
        return iter(d.items(**kw))

    def iterlists(d, **kw):
        return iter(d.lists(**kw))

    viewkeys = operator.methodcaller(\"keys\")

    viewvalues = operator.methodcaller(\"values\")

    viewitems = operator.methodcaller(\"items\")
else:

    def iterkeys(d, **kw):
        return d.iterkeys(**kw)

    def itervalues(d, **kw):
        return d.itervalues(**kw)

    def iteritems(d, **kw):
        return d.iteritems(**kw)

    def iterlists(d, **kw):
        return d.iterlists(**kw)

    viewkeys = operator.methodcaller(\"viewkeys\")

    viewvalues = operator.methodcaller(\"viewvalues\")

    viewitems = operator.methodcaller(\"viewitems\")

_add_doc(iterkeys, \"Return an iterator over the keys of a dictionary.\")
_add_doc(itervalues, \"Return an iterator over the values of a dictionary.\")
_add_doc(iteritems, \"Return an iterator over the (key, value) pairs of a dictionary.\")
_add_doc(
    iterlists, \"Return an iterator over the (key, [values]) pairs of a dictionary.\"
)


if PY3:

    def b(s):
        return s.encode(\"latin-1\")

    def u(s):
        return s

    unichr = chr
    import struct

    int2byte = struct.Struct(\">B\").pack
    del struct
    byte2int = operator.itemgetter(0)
    indexbytes = operator.getitem
    iterbytes = iter
    import io

    StringIO = io.StringIO
    BytesIO = io.BytesIO
    del io
    _assertCountEqual = \"assertCountEqual\"
    if sys.version_info[1] <= 1:
        _assertRaisesRegex = \"assertRaisesRegexp\"
        _assertRegex = \"assertRegexpMatches\"
        _assertNotRegex = \"assertNotRegexpMatches\"
    else:
        _assertRaisesRegex = \"assertRaisesRegex\"
        _assertRegex = \"assertRegex\"
        _assertNotRegex = \"assertNotRegex\"
else:

    def b(s):
        return s

    # Workaround for standalone backslash

    def u(s):
        return unicode(s.replace(r\"\\\\\", r\"\\\\\\\\\"), \"unicode_escape\")

    unichr = unichr
    int2byte = chr

    def byte2int(bs):
        return ord(bs[0])

    def indexbytes(buf, i):
        return ord(buf[i])

    iterbytes = functools.partial(itertools.imap, ord)
    import StringIO

    StringIO = BytesIO = StringIO.StringIO
    _assertCountEqual = \"assertItemsEqual\"
    _assertRaisesRegex = \"assertRaisesRegexp\"
    _assertRegex = \"assertRegexpMatches\"
    _assertNotRegex = \"assertNotRegexpMatches\"
_add_doc(b, \"\"\"Byte literal\"\"\")
_add_doc(u, \"\"\"Text literal\"\"\")


def assertCountEqual(self, *args, **kwargs):
    return getattr(self, _assertCountEqual)(*args, **kwargs)


def assertRaisesRegex(self, *args, **kwargs):
    return getattr(self, _assertRaisesRegex)(*args, **kwargs)


def assertRegex(self, *args, **kwargs):
    return getattr(self, _assertRegex)(*args, **kwargs)


def assertNotRegex(self, *args, **kwargs):
    return getattr(self, _assertNotRegex)(*args, **kwargs)


if PY3:
    exec_ = getattr(moves.builtins, \"exec\")

    def reraise(tp, value, tb=None):
        try:
            if value is None:
                value = tp()
            if value.__traceback__ is not tb:
                raise value.with_traceback(tb)
            raise value
        finally:
            value = None
            tb = None

else:

    def exec_(_code_, _globs_=None, _locs_=None):
        \"\"\"Execute code in a namespace.\"\"\"
        if _globs_ is None:
            frame = sys._getframe(1)
            _globs_ = frame.f_globals
            if _locs_ is None:
                _locs_ = frame.f_locals
            del frame
        elif _locs_ is None:
            _locs_ = _globs_
        exec (\"\"\"exec _code_ in _globs_, _locs_\"\"\")

    exec_(
        \"\"\"def reraise(tp, value, tb=None):
    try:
        raise tp, value, tb
    finally:
        tb = None
\"\"\"
    )


if sys.version_info[:2] > (3,):
    exec_(
        \"\"\"def raise_from(value, from_value):
    try:
        raise value from from_value
    finally:
        value = None
\"\"\"
    )
else:

    def raise_from(value, from_value):
        raise value


print_ = getattr(moves.builtins, \"print\", None)
if print_ is None:

    def print_(*args, **kwargs):
        \"\"\"The new-style print function for Python 2.4 and 2.5.\"\"\"
        fp = kwargs.pop(\"file\", sys.stdout)
        if fp is None:
            return

        def write(data):
            if not isinstance(data, basestring):
                data = str(data)
            # If the file has an encoding, encode unicode with it.
            if (
                isinstance(fp, file)
                and isinstance(data, unicode)
                and fp.encoding is not None
            ):
                errors = getattr(fp, \"errors\", None)
                if errors is None:
                    errors = \"strict\"
                data = data.encode(fp.encoding, errors)
            fp.write(data)

        want_unicode = False
        sep = kwargs.pop(\"sep\", None)
        if sep is not None:
            if isinstance(sep, unicode):
                want_unicode = True
            elif not isinstance(sep, str):
                raise TypeError(\"sep must be None or a string\")
        end = kwargs.pop(\"end\", None)
        if end is not None:
            if isinstance(end, unicode):
                want_unicode = True
            elif not isinstance(end, str):
                raise TypeError(\"end must be None or a string\")
        if kwargs:
            raise TypeError(\"invalid keyword arguments to print()\")
        if not want_unicode:
            for arg in args:
                if isinstance(arg, unicode):
                    want_unicode = True
                    break
        if want_unicode:
            newline = unicode(\"\\n\")
            space = unicode(\" \")
        else:
            newline = \"\\n\"
            space = \" \"
        if sep is None:
            sep = space
        if end is None:
            end = newline
        for i, arg in enumerate(args):
            if i:
                write(sep)
            write(arg)
        write(end)


if sys.version_info[:2] < (3, 3):
    _print = print_

    def print_(*args, **kwargs):
        fp = kwargs.get(\"file\", sys.stdout)
        flush = kwargs.pop(\"flush\", False)
        _print(*args, **kwargs)
        if flush and fp is not None:
            fp.flush()


_add_doc(reraise, \"\"\"Reraise an exception.\"\"\")

if sys.version_info[0:2] < (3, 4):
    # This does exactly the same what the :func:`py3:functools.update_wrapper`
    # function does on Python versions after 3.2. It sets the ``__wrapped__``
    # attribute on ``wrapper`` object and it doesn't raise an error if any of
    # the attributes mentioned in ``assigned`` and ``updated`` are missing on
    # ``wrapped`` object.
    def _update_wrapper(
        wrapper,
        wrapped,
        assigned=functools.WRAPPER_ASSIGNMENTS,
        updated=functools.WRAPPER_UPDATES,
    ):
        for attr in assigned:
            try:
                value = getattr(wrapped, attr)
            except AttributeError:
                continue
            else:
                setattr(wrapper, attr, value)
        for attr in updated:
            getattr(wrapper, attr).update(getattr(wrapped, attr, {}))
        wrapper.__wrapped__ = wrapped
        return wrapper

    _update_wrapper.__doc__ = functools.update_wrapper.__doc__

    def wraps(
        wrapped,
        assigned=functools.WRAPPER_ASSIGNMENTS,
        updated=functools.WRAPPER_UPDATES,
    ):
        return functools.partial(
            _update_wrapper, wrapped=wrapped, assigned=assigned, updated=updated
        )

    wraps.__doc__ = functools.wraps.__doc__

else:
    wraps = functools.wraps


def with_metaclass(meta, *bases):
    \"\"\"Create a base class with a metaclass.\"\"\"
    # This requires a bit of explanation: the basic idea is to make a dummy
    # metaclass for one level of class instantiation that replaces itself with
    # the actual metaclass.
    class metaclass(type):
        def __new__(cls, name, this_bases, d):
            if sys.version_info[:2] >= (3, 7):
                # This version introduced PEP 560 that requires a bit
                # of extra care (we mimic what is done by __build_class__).
                resolved_bases = types.resolve_bases(bases)
                if resolved_bases is not bases:
                    d[\"__orig_bases__\"] = bases
            else:
                resolved_bases = bases
            return meta(name, resolved_bases, d)

        @classmethod
        def __prepare__(cls, name, this_bases):
            return meta.__prepare__(name, bases)

    return type.__new__(metaclass, \"temporary_class\", (), {})


def add_metaclass(metaclass):
    \"\"\"Class decorator for creating a class with a metaclass.\"\"\"

    def wrapper(cls):
        orig_vars = cls.__dict__.copy()
        slots = orig_vars.get(\"__slots__\")
        if slots is not None:
            if isinstance(slots, str):
                slots = [slots]
            for slots_var in slots:
                orig_vars.pop(slots_var)
        orig_vars.pop(\"__dict__\", None)
        orig_vars.pop(\"__weakref__\", None)
        if hasattr(cls, \"__qualname__\"):
            orig_vars[\"__qualname__\"] = cls.__qualname__
        return metaclass(cls.__name__, cls.__bases__, orig_vars)

    return wrapper


def ensure_binary(s, encoding=\"utf-8\", errors=\"strict\"):
    \"\"\"Coerce **s** to six.binary_type.

    For Python 2:
      - `unicode` -> encoded to `str`
      - `str` -> `str`

    For Python 3:
      - `str` -> encoded to `bytes`
      - `bytes` -> `bytes`
    \"\"\"
    if isinstance(s, binary_type):
        return s
    if isinstance(s, text_type):
        return s.encode(encoding, errors)
    raise TypeError(\"not expecting type '%s'\" % type(s))


def ensure_str(s, encoding=\"utf-8\", errors=\"strict\"):
    \"\"\"Coerce *s* to `str`.

    For Python 2:
      - `unicode` -> encoded to `str`
      - `str` -> `str`

    For Python 3:
      - `str` -> `str`
      - `bytes` -> decoded to `str`
    \"\"\"
    # Optimization: Fast return for the common case.
    if type(s) is str:
        return s
    if PY2 and isinstance(s, text_type):
        return s.encode(encoding, errors)
    elif PY3 and isinstance(s, binary_type):
        return s.decode(encoding, errors)
    elif not isinstance(s, (text_type, binary_type)):
        raise TypeError(\"not expecting type '%s'\" % type(s))
    return s


def ensure_text(s, encoding=\"utf-8\", errors=\"strict\"):
    \"\"\"Coerce *s* to six.text_type.

    For Python 2:
      - `unicode` -> `unicode`
      - `str` -> `unicode`

    For Python 3:
      - `str` -> `str`
      - `bytes` -> decoded to `str`
    \"\"\"
    if isinstance(s, binary_type):
        return s.decode(encoding, errors)
    elif isinstance(s, text_type):
        return s
    else:
        raise TypeError(\"not expecting type '%s'\" % type(s))


def python_2_unicode_compatible(klass):
    \"\"\"
    A class decorator that defines __unicode__ and __str__ methods under Python 2.
    Under Python 3 it does nothing.

    To support Python 2 and 3 with a single code base, define a __str__ method
    returning text and apply this decorator to the class.
    \"\"\"
    if PY2:
        if \"__str__\" not in klass.__dict__:
            raise ValueError(
                \"@python_2_unicode_compatible cannot be applied \"
                \"to %s because it doesn't define __str__().\" % klass.__name__
            )
        klass.__unicode__ = klass.__str__
        klass.__str__ = lambda self: self.__unicode__().encode(\"utf-8\")
    return klass


# Complete the moves implementation.
# This code is at the end of this module to speed up module loading.
# Turn this module into a package.
__path__ = []  # required for PEP 302 and PEP 451
__package__ = __name__  # see PEP 366 @ReservedAssignment
if globals().get(\"__spec__\") is not None:
    __spec__.submodule_search_locations = []  # PEP 451 @UndefinedVariable
# Remove other six meta path importers, since they cause problems. This can
# happen if six is removed from sys.modules and then reloaded. (Setuptools does
# this for some reason.)
if sys.meta_path:
    for i, importer in enumerate(sys.meta_path):
        # Here's some real nastiness: Another \"instance\" of the six module might
        # be floating around. Therefore, we can't use isinstance() to check for
        # the six meta path importer, since the other six instance will have
        # inserted an importer with different class.
        if (
            type(importer).__name__ == \"_SixMetaPathImporter\"
            and importer.name == __name__
        ):
            del sys.meta_path[i]
            break
    del i, importer
# Finally, add the importer to the meta path import hook.
sys.meta_path.append(_importer)

"""
module_dict["urllib3"+os.sep+"packages"+os.sep+"__init__.py"]="""

"""
module_dict["urllib3"+os.sep+"packages"+os.sep+"backports"+os.sep+"makefile.py"]="""
# -*- coding: utf-8 -*-
\"\"\"
backports.makefile
~~~~~~~~~~~~~~~~~~

Backports the Python 3 ``socket.makefile`` method for use with anything that
wants to create a \"fake\" socket object.
\"\"\"
import io
from socket import SocketIO


def backport_makefile(
    self, mode=\"r\", buffering=None, encoding=None, errors=None, newline=None
):
    \"\"\"
    Backport of ``socket.makefile`` from Python 3.5.
    \"\"\"
    if not set(mode) <= {\"r\", \"w\", \"b\"}:
        raise ValueError(\"invalid mode %r (only r, w, b allowed)\" % (mode,))
    writing = \"w\" in mode
    reading = \"r\" in mode or not writing
    assert reading or writing
    binary = \"b\" in mode
    rawmode = \"\"
    if reading:
        rawmode += \"r\"
    if writing:
        rawmode += \"w\"
    raw = SocketIO(self, rawmode)
    self._makefile_refs += 1
    if buffering is None:
        buffering = -1
    if buffering < 0:
        buffering = io.DEFAULT_BUFFER_SIZE
    if buffering == 0:
        if not binary:
            raise ValueError(\"unbuffered streams must be binary\")
        return raw
    if reading and writing:
        buffer = io.BufferedRWPair(raw, raw, buffering)
    elif reading:
        buffer = io.BufferedReader(raw, buffering)
    else:
        assert writing
        buffer = io.BufferedWriter(raw, buffering)
    if binary:
        return buffer
    text = io.TextIOWrapper(buffer, encoding, errors, newline)
    text.mode = mode
    return text

"""
module_dict["urllib3"+os.sep+"packages"+os.sep+"backports"+os.sep+"__init__.py"]="""

"""
module_dict["urllib3"+os.sep+"util"+os.sep+"ssl_.py"]="""
from __future__ import absolute_import

import hmac
import os
import sys
import warnings
from binascii import hexlify, unhexlify
from hashlib import md5, sha1, sha256

from ..exceptions import (
    InsecurePlatformWarning,
    ProxySchemeUnsupported,
    SNIMissingWarning,
    SSLError,
)
from ..packages import six
from .url import BRACELESS_IPV6_ADDRZ_RE, IPV4_RE

SSLContext = None
SSLTransport = None
HAS_SNI = False
IS_PYOPENSSL = False
IS_SECURETRANSPORT = False
ALPN_PROTOCOLS = [\"http/1.1\"]

# Maps the length of a digest to a possible hash function producing this digest
HASHFUNC_MAP = {32: md5, 40: sha1, 64: sha256}


def _const_compare_digest_backport(a, b):
    \"\"\"
    Compare two digests of equal length in constant time.

    The digests must be of type str/bytes.
    Returns True if the digests match, and False otherwise.
    \"\"\"
    result = abs(len(a) - len(b))
    for left, right in zip(bytearray(a), bytearray(b)):
        result |= left ^ right
    return result == 0


_const_compare_digest = getattr(hmac, \"compare_digest\", _const_compare_digest_backport)

try:  # Test for SSL features
    import ssl
    from ssl import CERT_REQUIRED, wrap_socket
except ImportError:
    pass

try:
    from ssl import HAS_SNI  # Has SNI?
except ImportError:
    pass

try:
    from .ssltransport import SSLTransport
except ImportError:
    pass


try:  # Platform-specific: Python 3.6
    from ssl import PROTOCOL_TLS

    PROTOCOL_SSLv23 = PROTOCOL_TLS
except ImportError:
    try:
        from ssl import PROTOCOL_SSLv23 as PROTOCOL_TLS

        PROTOCOL_SSLv23 = PROTOCOL_TLS
    except ImportError:
        PROTOCOL_SSLv23 = PROTOCOL_TLS = 2

try:
    from ssl import PROTOCOL_TLS_CLIENT
except ImportError:
    PROTOCOL_TLS_CLIENT = PROTOCOL_TLS


try:
    from ssl import OP_NO_COMPRESSION, OP_NO_SSLv2, OP_NO_SSLv3
except ImportError:
    OP_NO_SSLv2, OP_NO_SSLv3 = 0x1000000, 0x2000000
    OP_NO_COMPRESSION = 0x20000


try:  # OP_NO_TICKET was added in Python 3.6
    from ssl import OP_NO_TICKET
except ImportError:
    OP_NO_TICKET = 0x4000


# A secure default.
# Sources for more information on TLS ciphers:
#
# - https://wiki.mozilla.org/Security/Server_Side_TLS
# - https://www.ssllabs.com/projects/best-practices/index.html
# - https://hynek.me/articles/hardening-your-web-servers-ssl-ciphers/
#
# The general intent is:
# - prefer cipher suites that offer perfect forward secrecy (DHE/ECDHE),
# - prefer ECDHE over DHE for better performance,
# - prefer any AES-GCM and ChaCha20 over any AES-CBC for better performance and
#   security,
# - prefer AES-GCM over ChaCha20 because hardware-accelerated AES is common,
# - disable NULL authentication, MD5 MACs, DSS, and other
#   insecure ciphers for security reasons.
# - NOTE: TLS 1.3 cipher suites are managed through a different interface
#   not exposed by CPython (yet!) and are enabled by default if they're available.
DEFAULT_CIPHERS = \":\".join(
    [
        \"ECDHE+AESGCM\",
        \"ECDHE+CHACHA20\",
        \"DHE+AESGCM\",
        \"DHE+CHACHA20\",
        \"ECDH+AESGCM\",
        \"DH+AESGCM\",
        \"ECDH+AES\",
        \"DH+AES\",
        \"RSA+AESGCM\",
        \"RSA+AES\",
        \"!aNULL\",
        \"!eNULL\",
        \"!MD5\",
        \"!DSS\",
    ]
)

try:
    from ssl import SSLContext  # Modern SSL?
except ImportError:

    class SSLContext(object):  # Platform-specific: Python 2
        def __init__(self, protocol_version):
            self.protocol = protocol_version
            # Use default values from a real SSLContext
            self.check_hostname = False
            self.verify_mode = ssl.CERT_NONE
            self.ca_certs = None
            self.options = 0
            self.certfile = None
            self.keyfile = None
            self.ciphers = None

        def load_cert_chain(self, certfile, keyfile):
            self.certfile = certfile
            self.keyfile = keyfile

        def load_verify_locations(self, cafile=None, capath=None, cadata=None):
            self.ca_certs = cafile

            if capath is not None:
                raise SSLError(\"CA directories not supported in older Pythons\")

            if cadata is not None:
                raise SSLError(\"CA data not supported in older Pythons\")

        def set_ciphers(self, cipher_suite):
            self.ciphers = cipher_suite

        def wrap_socket(self, socket, server_hostname=None, server_side=False):
            warnings.warn(
                \"A true SSLContext object is not available. This prevents \"
                \"urllib3 from configuring SSL appropriately and may cause \"
                \"certain SSL connections to fail. You can upgrade to a newer \"
                \"version of Python to solve this. For more information, see \"
                \"https://urllib3.readthedocs.io/en/1.26.x/advanced-usage.html\"
                \"#ssl-warnings\",
                InsecurePlatformWarning,
            )
            kwargs = {
                \"keyfile\": self.keyfile,
                \"certfile\": self.certfile,
                \"ca_certs\": self.ca_certs,
                \"cert_reqs\": self.verify_mode,
                \"ssl_version\": self.protocol,
                \"server_side\": server_side,
            }
            return wrap_socket(socket, ciphers=self.ciphers, **kwargs)


def assert_fingerprint(cert, fingerprint):
    \"\"\"
    Checks if given fingerprint matches the supplied certificate.

    :param cert:
        Certificate as bytes object.
    :param fingerprint:
        Fingerprint as string of hexdigits, can be interspersed by colons.
    \"\"\"

    fingerprint = fingerprint.replace(\":\", \"\").lower()
    digest_length = len(fingerprint)
    hashfunc = HASHFUNC_MAP.get(digest_length)
    if not hashfunc:
        raise SSLError(\"Fingerprint of invalid length: {0}\".format(fingerprint))

    # We need encode() here for py32; works on py2 and p33.
    fingerprint_bytes = unhexlify(fingerprint.encode())

    cert_digest = hashfunc(cert).digest()

    if not _const_compare_digest(cert_digest, fingerprint_bytes):
        raise SSLError(
            'Fingerprints did not match. Expected \"{0}\", got \"{1}\".'.format(
                fingerprint, hexlify(cert_digest)
            )
        )


def resolve_cert_reqs(candidate):
    \"\"\"
    Resolves the argument to a numeric constant, which can be passed to
    the wrap_socket function/method from the ssl module.
    Defaults to :data:`ssl.CERT_REQUIRED`.
    If given a string it is assumed to be the name of the constant in the
    :mod:`ssl` module or its abbreviation.
    (So you can specify `REQUIRED` instead of `CERT_REQUIRED`.
    If it's neither `None` nor a string we assume it is already the numeric
    constant which can directly be passed to wrap_socket.
    \"\"\"
    if candidate is None:
        return CERT_REQUIRED

    if isinstance(candidate, str):
        res = getattr(ssl, candidate, None)
        if res is None:
            res = getattr(ssl, \"CERT_\" + candidate)
        return res

    return candidate


def resolve_ssl_version(candidate):
    \"\"\"
    like resolve_cert_reqs
    \"\"\"
    if candidate is None:
        return PROTOCOL_TLS

    if isinstance(candidate, str):
        res = getattr(ssl, candidate, None)
        if res is None:
            res = getattr(ssl, \"PROTOCOL_\" + candidate)
        return res

    return candidate


def create_urllib3_context(
    ssl_version=None, cert_reqs=None, options=None, ciphers=None
):
    \"\"\"All arguments have the same meaning as ``ssl_wrap_socket``.

    By default, this function does a lot of the same work that
    ``ssl.create_default_context`` does on Python 3.4+. It:

    - Disables SSLv2, SSLv3, and compression
    - Sets a restricted set of server ciphers

    If you wish to enable SSLv3, you can do::

        from urllib3.util import ssl_
        context = ssl_.create_urllib3_context()
        context.options &= ~ssl_.OP_NO_SSLv3

    You can do the same to enable compression (substituting ``COMPRESSION``
    for ``SSLv3`` in the last line above).

    :param ssl_version:
        The desired protocol version to use. This will default to
        PROTOCOL_SSLv23 which will negotiate the highest protocol that both
        the server and your installation of OpenSSL support.
    :param cert_reqs:
        Whether to require the certificate verification. This defaults to
        ``ssl.CERT_REQUIRED``.
    :param options:
        Specific OpenSSL options. These default to ``ssl.OP_NO_SSLv2``,
        ``ssl.OP_NO_SSLv3``, ``ssl.OP_NO_COMPRESSION``, and ``ssl.OP_NO_TICKET``.
    :param ciphers:
        Which cipher suites to allow the server to select.
    :returns:
        Constructed SSLContext object with specified options
    :rtype: SSLContext
    \"\"\"
    # PROTOCOL_TLS is deprecated in Python 3.10
    if not ssl_version or ssl_version == PROTOCOL_TLS:
        ssl_version = PROTOCOL_TLS_CLIENT

    context = SSLContext(ssl_version)

    context.set_ciphers(ciphers or DEFAULT_CIPHERS)

    # Setting the default here, as we may have no ssl module on import
    cert_reqs = ssl.CERT_REQUIRED if cert_reqs is None else cert_reqs

    if options is None:
        options = 0
        # SSLv2 is easily broken and is considered harmful and dangerous
        options |= OP_NO_SSLv2
        # SSLv3 has several problems and is now dangerous
        options |= OP_NO_SSLv3
        # Disable compression to prevent CRIME attacks for OpenSSL 1.0+
        # (issue #309)
        options |= OP_NO_COMPRESSION
        # TLSv1.2 only. Unless set explicitly, do not request tickets.
        # This may save some bandwidth on wire, and although the ticket is encrypted,
        # there is a risk associated with it being on wire,
        # if the server is not rotating its ticketing keys properly.
        options |= OP_NO_TICKET

    context.options |= options

    # Enable post-handshake authentication for TLS 1.3, see GH #1634. PHA is
    # necessary for conditional client cert authentication with TLS 1.3.
    # The attribute is None for OpenSSL <= 1.1.0 or does not exist in older
    # versions of Python.  We only enable on Python 3.7.4+ or if certificate
    # verification is enabled to work around Python issue #37428
    # See: https://bugs.python.org/issue37428
    if (cert_reqs == ssl.CERT_REQUIRED or sys.version_info >= (3, 7, 4)) and getattr(
        context, \"post_handshake_auth\", None
    ) is not None:
        context.post_handshake_auth = True

    def disable_check_hostname():
        if (
            getattr(context, \"check_hostname\", None) is not None
        ):  # Platform-specific: Python 3.2
            # We do our own verification, including fingerprints and alternative
            # hostnames. So disable it here
            context.check_hostname = False

    # The order of the below lines setting verify_mode and check_hostname
    # matter due to safe-guards SSLContext has to prevent an SSLContext with
    # check_hostname=True, verify_mode=NONE/OPTIONAL. This is made even more
    # complex because we don't know whether PROTOCOL_TLS_CLIENT will be used
    # or not so we don't know the initial state of the freshly created SSLContext.
    if cert_reqs == ssl.CERT_REQUIRED:
        context.verify_mode = cert_reqs
        disable_check_hostname()
    else:
        disable_check_hostname()
        context.verify_mode = cert_reqs

    # Enable logging of TLS session keys via defacto standard environment variable
    # 'SSLKEYLOGFILE', if the feature is available (Python 3.8+). Skip empty values.
    if hasattr(context, \"keylog_filename\"):
        sslkeylogfile = os.environ.get(\"SSLKEYLOGFILE\")
        if sslkeylogfile:
            context.keylog_filename = sslkeylogfile

    return context


def ssl_wrap_socket(
    sock,
    keyfile=None,
    certfile=None,
    cert_reqs=None,
    ca_certs=None,
    server_hostname=None,
    ssl_version=None,
    ciphers=None,
    ssl_context=None,
    ca_cert_dir=None,
    key_password=None,
    ca_cert_data=None,
    tls_in_tls=False,
):
    \"\"\"
    All arguments except for server_hostname, ssl_context, and ca_cert_dir have
    the same meaning as they do when using :func:`ssl.wrap_socket`.

    :param server_hostname:
        When SNI is supported, the expected hostname of the certificate
    :param ssl_context:
        A pre-made :class:`SSLContext` object. If none is provided, one will
        be created using :func:`create_urllib3_context`.
    :param ciphers:
        A string of ciphers we wish the client to support.
    :param ca_cert_dir:
        A directory containing CA certificates in multiple separate files, as
        supported by OpenSSL's -CApath flag or the capath argument to
        SSLContext.load_verify_locations().
    :param key_password:
        Optional password if the keyfile is encrypted.
    :param ca_cert_data:
        Optional string containing CA certificates in PEM format suitable for
        passing as the cadata parameter to SSLContext.load_verify_locations()
    :param tls_in_tls:
        Use SSLTransport to wrap the existing socket.
    \"\"\"
    context = ssl_context
    if context is None:
        # Note: This branch of code and all the variables in it are no longer
        # used by urllib3 itself. We should consider deprecating and removing
        # this code.
        context = create_urllib3_context(ssl_version, cert_reqs, ciphers=ciphers)

    if ca_certs or ca_cert_dir or ca_cert_data:
        try:
            context.load_verify_locations(ca_certs, ca_cert_dir, ca_cert_data)
        except (IOError, OSError) as e:
            raise SSLError(e)

    elif ssl_context is None and hasattr(context, \"load_default_certs\"):
        # try to load OS default certs; works well on Windows (require Python3.4+)
        context.load_default_certs()

    # Attempt to detect if we get the goofy behavior of the
    # keyfile being encrypted and OpenSSL asking for the
    # passphrase via the terminal and instead error out.
    if keyfile and key_password is None and _is_key_file_encrypted(keyfile):
        raise SSLError(\"Client private key is encrypted, password is required\")

    if certfile:
        if key_password is None:
            context.load_cert_chain(certfile, keyfile)
        else:
            context.load_cert_chain(certfile, keyfile, key_password)

    try:
        if hasattr(context, \"set_alpn_protocols\"):
            context.set_alpn_protocols(ALPN_PROTOCOLS)
    except NotImplementedError:  # Defensive: in CI, we always have set_alpn_protocols
        pass

    # If we detect server_hostname is an IP address then the SNI
    # extension should not be used according to RFC3546 Section 3.1
    use_sni_hostname = server_hostname and not is_ipaddress(server_hostname)
    # SecureTransport uses server_hostname in certificate verification.
    send_sni = (use_sni_hostname and HAS_SNI) or (
        IS_SECURETRANSPORT and server_hostname
    )
    # Do not warn the user if server_hostname is an invalid SNI hostname.
    if not HAS_SNI and use_sni_hostname:
        warnings.warn(
            \"An HTTPS request has been made, but the SNI (Server Name \"
            \"Indication) extension to TLS is not available on this platform. \"
            \"This may cause the server to present an incorrect TLS \"
            \"certificate, which can cause validation failures. You can upgrade to \"
            \"a newer version of Python to solve this. For more information, see \"
            \"https://urllib3.readthedocs.io/en/1.26.x/advanced-usage.html\"
            \"#ssl-warnings\",
            SNIMissingWarning,
        )

    if send_sni:
        ssl_sock = _ssl_wrap_socket_impl(
            sock, context, tls_in_tls, server_hostname=server_hostname
        )
    else:
        ssl_sock = _ssl_wrap_socket_impl(sock, context, tls_in_tls)
    return ssl_sock


def is_ipaddress(hostname):
    \"\"\"Detects whether the hostname given is an IPv4 or IPv6 address.
    Also detects IPv6 addresses with Zone IDs.

    :param str hostname: Hostname to examine.
    :return: True if the hostname is an IP address, False otherwise.
    \"\"\"
    if not six.PY2 and isinstance(hostname, bytes):
        # IDN A-label bytes are ASCII compatible.
        hostname = hostname.decode(\"ascii\")
    return bool(IPV4_RE.match(hostname) or BRACELESS_IPV6_ADDRZ_RE.match(hostname))


def _is_key_file_encrypted(key_file):
    \"\"\"Detects if a key file is encrypted or not.\"\"\"
    with open(key_file, \"r\") as f:
        for line in f:
            # Look for Proc-Type: 4,ENCRYPTED
            if \"ENCRYPTED\" in line:
                return True

    return False


def _ssl_wrap_socket_impl(sock, ssl_context, tls_in_tls, server_hostname=None):
    if tls_in_tls:
        if not SSLTransport:
            # Import error, ssl is not available.
            raise ProxySchemeUnsupported(
                \"TLS in TLS requires support for the 'ssl' module\"
            )

        SSLTransport._validate_ssl_context_for_tls_in_tls(ssl_context)
        return SSLTransport(sock, ssl_context, server_hostname)

    if server_hostname:
        return ssl_context.wrap_socket(sock, server_hostname=server_hostname)
    else:
        return ssl_context.wrap_socket(sock)

"""
module_dict["urllib3"+os.sep+"util"+os.sep+"wait.py"]="""
import errno
import select
import sys
from functools import partial

try:
    from time import monotonic
except ImportError:
    from time import time as monotonic

__all__ = [\"NoWayToWaitForSocketError\", \"wait_for_read\", \"wait_for_write\"]


class NoWayToWaitForSocketError(Exception):
    pass


# How should we wait on sockets?
#
# There are two types of APIs you can use for waiting on sockets: the fancy
# modern stateful APIs like epoll/kqueue, and the older stateless APIs like
# select/poll. The stateful APIs are more efficient when you have a lots of
# sockets to keep track of, because you can set them up once and then use them
# lots of times. But we only ever want to wait on a single socket at a time
# and don't want to keep track of state, so the stateless APIs are actually
# more efficient. So we want to use select() or poll().
#
# Now, how do we choose between select() and poll()? On traditional Unixes,
# select() has a strange calling convention that makes it slow, or fail
# altogether, for high-numbered file descriptors. The point of poll() is to fix
# that, so on Unixes, we prefer poll().
#
# On Windows, there is no poll() (or at least Python doesn't provide a wrapper
# for it), but that's OK, because on Windows, select() doesn't have this
# strange calling convention; plain select() works fine.
#
# So: on Windows we use select(), and everywhere else we use poll(). We also
# fall back to select() in case poll() is somehow broken or missing.

if sys.version_info >= (3, 5):
    # Modern Python, that retries syscalls by default
    def _retry_on_intr(fn, timeout):
        return fn(timeout)

else:
    # Old and broken Pythons.
    def _retry_on_intr(fn, timeout):
        if timeout is None:
            deadline = float(\"inf\")
        else:
            deadline = monotonic() + timeout

        while True:
            try:
                return fn(timeout)
            # OSError for 3 <= pyver < 3.5, select.error for pyver <= 2.7
            except (OSError, select.error) as e:
                # 'e.args[0]' incantation works for both OSError and select.error
                if e.args[0] != errno.EINTR:
                    raise
                else:
                    timeout = deadline - monotonic()
                    if timeout < 0:
                        timeout = 0
                    if timeout == float(\"inf\"):
                        timeout = None
                    continue


def select_wait_for_socket(sock, read=False, write=False, timeout=None):
    if not read and not write:
        raise RuntimeError(\"must specify at least one of read=True, write=True\")
    rcheck = []
    wcheck = []
    if read:
        rcheck.append(sock)
    if write:
        wcheck.append(sock)
    # When doing a non-blocking connect, most systems signal success by
    # marking the socket writable. Windows, though, signals success by marked
    # it as \"exceptional\". We paper over the difference by checking the write
    # sockets for both conditions. (The stdlib selectors module does the same
    # thing.)
    fn = partial(select.select, rcheck, wcheck, wcheck)
    rready, wready, xready = _retry_on_intr(fn, timeout)
    return bool(rready or wready or xready)


def poll_wait_for_socket(sock, read=False, write=False, timeout=None):
    if not read and not write:
        raise RuntimeError(\"must specify at least one of read=True, write=True\")
    mask = 0
    if read:
        mask |= select.POLLIN
    if write:
        mask |= select.POLLOUT
    poll_obj = select.poll()
    poll_obj.register(sock, mask)

    # For some reason, poll() takes timeout in milliseconds
    def do_poll(t):
        if t is not None:
            t *= 1000
        return poll_obj.poll(t)

    return bool(_retry_on_intr(do_poll, timeout))


def null_wait_for_socket(*args, **kwargs):
    raise NoWayToWaitForSocketError(\"no select-equivalent available\")


def _have_working_poll():
    # Apparently some systems have a select.poll that fails as soon as you try
    # to use it, either due to strange configuration or broken monkeypatching
    # from libraries like eventlet/greenlet.
    try:
        poll_obj = select.poll()
        _retry_on_intr(poll_obj.poll, 0)
    except (AttributeError, OSError):
        return False
    else:
        return True


def wait_for_socket(*args, **kwargs):
    # We delay choosing which implementation to use until the first time we're
    # called. We could do it at import time, but then we might make the wrong
    # decision if someone goes wild with monkeypatching select.poll after
    # we're imported.
    global wait_for_socket
    if _have_working_poll():
        wait_for_socket = poll_wait_for_socket
    elif hasattr(select, \"select\"):
        wait_for_socket = select_wait_for_socket
    else:  # Platform-specific: Appengine.
        wait_for_socket = null_wait_for_socket
    return wait_for_socket(*args, **kwargs)


def wait_for_read(sock, timeout=None):
    \"\"\"Waits for reading to be available on a given socket.
    Returns True if the socket is readable, or False if the timeout expired.
    \"\"\"
    return wait_for_socket(sock, read=True, timeout=timeout)


def wait_for_write(sock, timeout=None):
    \"\"\"Waits for writing to be available on a given socket.
    Returns True if the socket is readable, or False if the timeout expired.
    \"\"\"
    return wait_for_socket(sock, write=True, timeout=timeout)

"""
module_dict["urllib3"+os.sep+"util"+os.sep+"queue.py"]="""
# < include 'Queue.py' >

import collections

from ..packages import six
from ..packages.six.moves import queue

if six.PY2:
    # Queue is imported for side effects on MS Windows. See issue #229.
    import Queue as _unused_module_Queue  # noqa: F401


class LifoQueue(queue.Queue):
    def _init(self, _):
        self.queue = collections.deque()

    def _qsize(self, len=len):
        return len(self.queue)

    def _put(self, item):
        self.queue.append(item)

    def _get(self):
        return self.queue.pop()

"""
module_dict["urllib3"+os.sep+"util"+os.sep+"url.py"]="""
from __future__ import absolute_import
# < include 'idna.py' >


import re
from collections import namedtuple

from ..exceptions import LocationParseError
from ..packages import six

url_attrs = [\"scheme\", \"auth\", \"host\", \"port\", \"path\", \"query\", \"fragment\"]

# We only want to normalize urls with an HTTP(S) scheme.
# urllib3 infers URLs without a scheme (None) to be http.
NORMALIZABLE_SCHEMES = (\"http\", \"https\", None)

# Almost all of these patterns were derived from the
# 'rfc3986' module: https://github.com/python-hyper/rfc3986
PERCENT_RE = re.compile(r\"%[a-fA-F0-9]{2}\")
SCHEME_RE = re.compile(r\"^(?:[a-zA-Z][a-zA-Z0-9+-]*:|/)\")
URI_RE = re.compile(
    r\"^(?:([a-zA-Z][a-zA-Z0-9+.-]*):)?\"
    r\"(?://([^\\\\/?#]*))?\"
    r\"([^?#]*)\"
    r\"(?:\\?([^#]*))?\"
    r\"(?:#(.*))?$\",
    re.UNICODE | re.DOTALL,
)

IPV4_PAT = r\"(?:[0-9]{1,3}\\.){3}[0-9]{1,3}\"
HEX_PAT = \"[0-9A-Fa-f]{1,4}\"
LS32_PAT = \"(?:{hex}:{hex}|{ipv4})\".format(hex=HEX_PAT, ipv4=IPV4_PAT)
_subs = {\"hex\": HEX_PAT, \"ls32\": LS32_PAT}
_variations = [
    #                            6( h16 \":\" ) ls32
    \"(?:%(hex)s:){6}%(ls32)s\",
    #                       \"::\" 5( h16 \":\" ) ls32
    \"::(?:%(hex)s:){5}%(ls32)s\",
    # [               h16 ] \"::\" 4( h16 \":\" ) ls32
    \"(?:%(hex)s)?::(?:%(hex)s:){4}%(ls32)s\",
    # [ *1( h16 \":\" ) h16 ] \"::\" 3( h16 \":\" ) ls32
    \"(?:(?:%(hex)s:)?%(hex)s)?::(?:%(hex)s:){3}%(ls32)s\",
    # [ *2( h16 \":\" ) h16 ] \"::\" 2( h16 \":\" ) ls32
    \"(?:(?:%(hex)s:){0,2}%(hex)s)?::(?:%(hex)s:){2}%(ls32)s\",
    # [ *3( h16 \":\" ) h16 ] \"::\"    h16 \":\"   ls32
    \"(?:(?:%(hex)s:){0,3}%(hex)s)?::%(hex)s:%(ls32)s\",
    # [ *4( h16 \":\" ) h16 ] \"::\"              ls32
    \"(?:(?:%(hex)s:){0,4}%(hex)s)?::%(ls32)s\",
    # [ *5( h16 \":\" ) h16 ] \"::\"              h16
    \"(?:(?:%(hex)s:){0,5}%(hex)s)?::%(hex)s\",
    # [ *6( h16 \":\" ) h16 ] \"::\"
    \"(?:(?:%(hex)s:){0,6}%(hex)s)?::\",
]

UNRESERVED_PAT = r\"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._!\\-~\"
IPV6_PAT = \"(?:\" + \"|\".join([x % _subs for x in _variations]) + \")\"
ZONE_ID_PAT = \"(?:%25|%)(?:[\" + UNRESERVED_PAT + \"]|%[a-fA-F0-9]{2})+\"
IPV6_ADDRZ_PAT = r\"\\[\" + IPV6_PAT + r\"(?:\" + ZONE_ID_PAT + r\")?\\]\"
REG_NAME_PAT = r\"(?:[^\\[\\]%:/?#]|%[a-fA-F0-9]{2})*\"
TARGET_RE = re.compile(r\"^(/[^?#]*)(?:\\?([^#]*))?(?:#.*)?$\")

IPV4_RE = re.compile(\"^\" + IPV4_PAT + \"$\")
IPV6_RE = re.compile(\"^\" + IPV6_PAT + \"$\")
IPV6_ADDRZ_RE = re.compile(\"^\" + IPV6_ADDRZ_PAT + \"$\")
BRACELESS_IPV6_ADDRZ_RE = re.compile(\"^\" + IPV6_ADDRZ_PAT[2:-2] + \"$\")
ZONE_ID_RE = re.compile(\"(\" + ZONE_ID_PAT + r\")\\]$\")

_HOST_PORT_PAT = (\"^(%s|%s|%s)(?::([0-9]{0,5}))?$\") % (
    REG_NAME_PAT,
    IPV4_PAT,
    IPV6_ADDRZ_PAT,
)
_HOST_PORT_RE = re.compile(_HOST_PORT_PAT, re.UNICODE | re.DOTALL)

UNRESERVED_CHARS = set(
    \"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-~\"
)
SUB_DELIM_CHARS = set(\"!$&'()*+,;=\")
USERINFO_CHARS = UNRESERVED_CHARS | SUB_DELIM_CHARS | {\":\"}
PATH_CHARS = USERINFO_CHARS | {\"@\", \"/\"}
QUERY_CHARS = FRAGMENT_CHARS = PATH_CHARS | {\"?\"}


class Url(namedtuple(\"Url\", url_attrs)):
    \"\"\"
    Data structure for representing an HTTP URL. Used as a return value for
    :func:`parse_url`. Both the scheme and host are normalized as they are
    both case-insensitive according to RFC 3986.
    \"\"\"

    __slots__ = ()

    def __new__(
        cls,
        scheme=None,
        auth=None,
        host=None,
        port=None,
        path=None,
        query=None,
        fragment=None,
    ):
        if path and not path.startswith(\"/\"):
            path = \"/\" + path
        if scheme is not None:
            scheme = scheme.lower()
        return super(Url, cls).__new__(
            cls, scheme, auth, host, port, path, query, fragment
        )

    @property
    def hostname(self):
        \"\"\"For backwards-compatibility with urlparse. We're nice like that.\"\"\"
        return self.host

    @property
    def request_uri(self):
        \"\"\"Absolute path including the query string.\"\"\"
        uri = self.path or \"/\"

        if self.query is not None:
            uri += \"?\" + self.query

        return uri

    @property
    def netloc(self):
        \"\"\"Network location including host and port\"\"\"
        if self.port:
            return \"%s:%d\" % (self.host, self.port)
        return self.host

    @property
    def url(self):
        \"\"\"
        Convert self into a url

        This function should more or less round-trip with :func:`.parse_url`. The
        returned url may not be exactly the same as the url inputted to
        :func:`.parse_url`, but it should be equivalent by the RFC (e.g., urls
        with a blank port will have : removed).

        Example: ::

            >>> U = parse_url('http://google.com/mail/')
            >>> U.url
            'http://google.com/mail/'
            >>> Url('http', 'username:password', 'host.com', 80,
            ... '/path', 'query', 'fragment').url
            'http://username:password@host.com:80/path?query#fragment'
        \"\"\"
        scheme, auth, host, port, path, query, fragment = self
        url = u\"\"

        # We use \"is not None\" we want things to happen with empty strings (or 0 port)
        if scheme is not None:
            url += scheme + u\"://\"
        if auth is not None:
            url += auth + u\"@\"
        if host is not None:
            url += host
        if port is not None:
            url += u\":\" + str(port)
        if path is not None:
            url += path
        if query is not None:
            url += u\"?\" + query
        if fragment is not None:
            url += u\"#\" + fragment

        return url

    def __str__(self):
        return self.url


def split_first(s, delims):
    \"\"\"
    .. deprecated:: 1.25

    Given a string and an iterable of delimiters, split on the first found
    delimiter. Return two split parts and the matched delimiter.

    If not found, then the first part is the full input string.

    Example::

        >>> split_first('foo/bar?baz', '?/=')
        ('foo', 'bar?baz', '/')
        >>> split_first('foo/bar?baz', '123')
        ('foo/bar?baz', '', None)

    Scales linearly with number of delims. Not ideal for large number of delims.
    \"\"\"
    min_idx = None
    min_delim = None
    for d in delims:
        idx = s.find(d)
        if idx < 0:
            continue

        if min_idx is None or idx < min_idx:
            min_idx = idx
            min_delim = d

    if min_idx is None or min_idx < 0:
        return s, \"\", None

    return s[:min_idx], s[min_idx + 1 :], min_delim


def _encode_invalid_chars(component, allowed_chars, encoding=\"utf-8\"):
    \"\"\"Percent-encodes a URI component without reapplying
    onto an already percent-encoded component.
    \"\"\"
    if component is None:
        return component

    component = six.ensure_text(component)

    # Normalize existing percent-encoded bytes.
    # Try to see if the component we're encoding is already percent-encoded
    # so we can skip all '%' characters but still encode all others.
    component, percent_encodings = PERCENT_RE.subn(
        lambda match: match.group(0).upper(), component
    )

    uri_bytes = component.encode(\"utf-8\", \"surrogatepass\")
    is_percent_encoded = percent_encodings == uri_bytes.count(b\"%\")
    encoded_component = bytearray()

    for i in range(0, len(uri_bytes)):
        # Will return a single character bytestring on both Python 2 & 3
        byte = uri_bytes[i : i + 1]
        byte_ord = ord(byte)
        if (is_percent_encoded and byte == b\"%\") or (
            byte_ord < 128 and byte.decode() in allowed_chars
        ):
            encoded_component += byte
            continue
        encoded_component.extend(b\"%\" + (hex(byte_ord)[2:].encode().zfill(2).upper()))

    return encoded_component.decode(encoding)


def _remove_path_dot_segments(path):
    # See http://tools.ietf.org/html/rfc3986#section-5.2.4 for pseudo-code
    segments = path.split(\"/\")  # Turn the path into a list of segments
    output = []  # Initialize the variable to use to store output

    for segment in segments:
        # '.' is the current directory, so ignore it, it is superfluous
        if segment == \".\":
            continue
        # Anything other than '..', should be appended to the output
        elif segment != \"..\":
            output.append(segment)
        # In this case segment == '..', if we can, we should pop the last
        # element
        elif output:
            output.pop()

    # If the path starts with '/' and the output is empty or the first string
    # is non-empty
    if path.startswith(\"/\") and (not output or output[0]):
        output.insert(0, \"\")

    # If the path starts with '/.' or '/..' ensure we add one more empty
    # string to add a trailing '/'
    if path.endswith((\"/.\", \"/..\")):
        output.append(\"\")

    return \"/\".join(output)


def _normalize_host(host, scheme):
    if host:
        if isinstance(host, six.binary_type):
            host = six.ensure_str(host)

        if scheme in NORMALIZABLE_SCHEMES:
            is_ipv6 = IPV6_ADDRZ_RE.match(host)
            if is_ipv6:
                # IPv6 hosts of the form 'a::b%zone' are encoded in a URL as
                # such per RFC 6874: 'a::b%25zone'. Unquote the ZoneID
                # separator as necessary to return a valid RFC 4007 scoped IP.
                match = ZONE_ID_RE.search(host)
                if match:
                    start, end = match.span(1)
                    zone_id = host[start:end]

                    if zone_id.startswith(\"%25\") and zone_id != \"%25\":
                        zone_id = zone_id[3:]
                    else:
                        zone_id = zone_id[1:]
                    zone_id = \"%\" + _encode_invalid_chars(zone_id, UNRESERVED_CHARS)
                    return host[:start].lower() + zone_id + host[end:]
                else:
                    return host.lower()
            elif not IPV4_RE.match(host):
                return six.ensure_str(
                    b\".\".join([_idna_encode(label) for label in host.split(\".\")])
                )
    return host


def _idna_encode(name):
    if name and any([ord(x) > 128 for x in name]):
        try:
            import idna
        except ImportError:
            six.raise_from(
                LocationParseError(\"Unable to parse URL without the 'idna' module\"),
                None,
            )
        try:
            return idna.encode(name.lower(), strict=True, std3_rules=True)
        except idna.IDNAError:
            six.raise_from(
                LocationParseError(u\"Name '%s' is not a valid IDNA label\" % name), None
            )
    return name.lower().encode(\"ascii\")


def _encode_target(target):
    \"\"\"Percent-encodes a request target so that there are no invalid characters\"\"\"
    path, query = TARGET_RE.match(target).groups()
    target = _encode_invalid_chars(path, PATH_CHARS)
    query = _encode_invalid_chars(query, QUERY_CHARS)
    if query is not None:
        target += \"?\" + query
    return target


def parse_url(url):
    \"\"\"
    Given a url, return a parsed :class:`.Url` namedtuple. Best-effort is
    performed to parse incomplete urls. Fields not provided will be None.
    This parser is RFC 3986 and RFC 6874 compliant.

    The parser logic and helper functions are based heavily on
    work done in the ``rfc3986`` module.

    :param str url: URL to parse into a :class:`.Url` namedtuple.

    Partly backwards-compatible with :mod:`urlparse`.

    Example::

        >>> parse_url('http://google.com/mail/')
        Url(scheme='http', host='google.com', port=None, path='/mail/', ...)
        >>> parse_url('google.com:80')
        Url(scheme=None, host='google.com', port=80, path=None, ...)
        >>> parse_url('/foo?bar')
        Url(scheme=None, host=None, port=None, path='/foo', query='bar', ...)
    \"\"\"
    if not url:
        # Empty
        return Url()

    source_url = url
    if not SCHEME_RE.search(url):
        url = \"//\" + url

    try:
        scheme, authority, path, query, fragment = URI_RE.match(url).groups()
        normalize_uri = scheme is None or scheme.lower() in NORMALIZABLE_SCHEMES

        if scheme:
            scheme = scheme.lower()

        if authority:
            auth, _, host_port = authority.rpartition(\"@\")
            auth = auth or None
            host, port = _HOST_PORT_RE.match(host_port).groups()
            if auth and normalize_uri:
                auth = _encode_invalid_chars(auth, USERINFO_CHARS)
            if port == \"\":
                port = None
        else:
            auth, host, port = None, None, None

        if port is not None:
            port = int(port)
            if not (0 <= port <= 65535):
                raise LocationParseError(url)

        host = _normalize_host(host, scheme)

        if normalize_uri and path:
            path = _remove_path_dot_segments(path)
            path = _encode_invalid_chars(path, PATH_CHARS)
        if normalize_uri and query:
            query = _encode_invalid_chars(query, QUERY_CHARS)
        if normalize_uri and fragment:
            fragment = _encode_invalid_chars(fragment, FRAGMENT_CHARS)

    except (ValueError, AttributeError):
        return six.raise_from(LocationParseError(source_url), None)

    # For the sake of backwards compatibility we put empty
    # string values for path if there are any defined values
    # beyond the path in the URL.
    # TODO: Remove this when we break backwards compatibility.
    if not path:
        if query is not None or fragment is not None:
            path = \"\"
        else:
            path = None

    # Ensure that each part of the URL is a `str` for
    # backwards compatibility.
    if isinstance(url, six.text_type):
        ensure_func = six.ensure_text
    else:
        ensure_func = six.ensure_str

    def ensure_type(x):
        return x if x is None else ensure_func(x)

    return Url(
        scheme=ensure_type(scheme),
        auth=ensure_type(auth),
        host=ensure_type(host),
        port=port,
        path=ensure_type(path),
        query=ensure_type(query),
        fragment=ensure_type(fragment),
    )


def get_host(url):
    \"\"\"
    Deprecated. Use :func:`parse_url` instead.
    \"\"\"
    p = parse_url(url)
    return p.scheme or \"http\", p.hostname, p.port

"""
module_dict["urllib3"+os.sep+"util"+os.sep+"connection.py"]="""
from __future__ import absolute_import

import socket

from ..contrib import _appengine_environ
from ..exceptions import LocationParseError
from ..packages import six
from .wait import NoWayToWaitForSocketError, wait_for_read


def is_connection_dropped(conn):  # Platform-specific
    \"\"\"
    Returns True if the connection is dropped and should be closed.

    :param conn:
        :class:`http.client.HTTPConnection` object.

    Note: For platforms like AppEngine, this will always return ``False`` to
    let the platform handle connection recycling transparently for us.
    \"\"\"
    sock = getattr(conn, \"sock\", False)
    if sock is False:  # Platform-specific: AppEngine
        return False
    if sock is None:  # Connection already closed (such as by httplib).
        return True
    try:
        # Returns True if readable, which here means it's been dropped
        return wait_for_read(sock, timeout=0.0)
    except NoWayToWaitForSocketError:  # Platform-specific: AppEngine
        return False


# This function is copied from socket.py in the Python 2.7 standard
# library test suite. Added to its signature is only `socket_options`.
# One additional modification is that we avoid binding to IPv6 servers
# discovered in DNS if the system doesn't have IPv6 functionality.
def create_connection(
    address,
    timeout=socket._GLOBAL_DEFAULT_TIMEOUT,
    source_address=None,
    socket_options=None,
):
    \"\"\"Connect to *address* and return the socket object.

    Convenience function.  Connect to *address* (a 2-tuple ``(host,
    port)``) and return the socket object.  Passing the optional
    *timeout* parameter will set the timeout on the socket instance
    before attempting to connect.  If no *timeout* is supplied, the
    global default timeout setting returned by :func:`socket.getdefaulttimeout`
    is used.  If *source_address* is set it must be a tuple of (host, port)
    for the socket to bind as a source address before making the connection.
    An host of '' or port 0 tells the OS to use the default.
    \"\"\"

    host, port = address
    if host.startswith(\"[\"):
        host = host.strip(\"[]\")
    err = None

    # Using the value from allowed_gai_family() in the context of getaddrinfo lets
    # us select whether to work with IPv4 DNS records, IPv6 records, or both.
    # The original create_connection function always returns all records.
    family = allowed_gai_family()

    try:
        host.encode(\"idna\")
    except UnicodeError:
        return six.raise_from(
            LocationParseError(u\"'%s', label empty or too long\" % host), None
        )

    for res in socket.getaddrinfo(host, port, family, socket.SOCK_STREAM):
        af, socktype, proto, canonname, sa = res
        sock = None
        try:
            sock = socket.socket(af, socktype, proto)

            # If provided, set socket level options before connecting.
            _set_socket_options(sock, socket_options)

            if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                sock.settimeout(timeout)
            if source_address:
                sock.bind(source_address)
            sock.connect(sa)
            return sock

        except socket.error as e:
            err = e
            if sock is not None:
                sock.close()
                sock = None

    if err is not None:
        raise err

    raise socket.error(\"getaddrinfo returns an empty list\")


def _set_socket_options(sock, options):
    if options is None:
        return

    for opt in options:
        sock.setsockopt(*opt)


def allowed_gai_family():
    \"\"\"This function is designed to work in the context of
    getaddrinfo, where family=socket.AF_UNSPEC is the default and
    will perform a DNS search for both IPv6 and IPv4 records.\"\"\"

    family = socket.AF_INET
    if HAS_IPV6:
        family = socket.AF_UNSPEC
    return family


def _has_ipv6(host):
    \"\"\"Returns True if the system can bind an IPv6 address.\"\"\"
    sock = None
    has_ipv6 = False

    # App Engine doesn't support IPV6 sockets and actually has a quota on the
    # number of sockets that can be used, so just early out here instead of
    # creating a socket needlessly.
    # See https://github.com/urllib3/urllib3/issues/1446
    if _appengine_environ.is_appengine_sandbox():
        return False

    if socket.has_ipv6:
        # has_ipv6 returns true if cPython was compiled with IPv6 support.
        # It does not tell us if the system has IPv6 support enabled. To
        # determine that we must bind to an IPv6 address.
        # https://github.com/urllib3/urllib3/pull/611
        # https://bugs.python.org/issue658327
        try:
            sock = socket.socket(socket.AF_INET6)
            sock.bind((host, 0))
            has_ipv6 = True
        except Exception:
            pass

    if sock:
        sock.close()
    return has_ipv6


HAS_IPV6 = _has_ipv6(\"::1\")

"""
module_dict["urllib3"+os.sep+"util"+os.sep+"timeout.py"]="""
from __future__ import absolute_import

import time

# The default socket timeout, used by httplib to indicate that no timeout was
# specified by the user
from socket import _GLOBAL_DEFAULT_TIMEOUT

from ..exceptions import TimeoutStateError

# A sentinel value to indicate that no timeout was specified by the user in
# urllib3
_Default = object()


# Use time.monotonic if available.
current_time = getattr(time, \"monotonic\", time.time)


class Timeout(object):
    \"\"\"Timeout configuration.

    Timeouts can be defined as a default for a pool:

    .. code-block:: python

       timeout = Timeout(connect=2.0, read=7.0)
       http = PoolManager(timeout=timeout)
       response = http.request('GET', 'http://example.com/')

    Or per-request (which overrides the default for the pool):

    .. code-block:: python

       response = http.request('GET', 'http://example.com/', timeout=Timeout(10))

    Timeouts can be disabled by setting all the parameters to ``None``:

    .. code-block:: python

       no_timeout = Timeout(connect=None, read=None)
       response = http.request('GET', 'http://example.com/, timeout=no_timeout)


    :param total:
        This combines the connect and read timeouts into one; the read timeout
        will be set to the time leftover from the connect attempt. In the
        event that both a connect timeout and a total are specified, or a read
        timeout and a total are specified, the shorter timeout will be applied.

        Defaults to None.

    :type total: int, float, or None

    :param connect:
        The maximum amount of time (in seconds) to wait for a connection
        attempt to a server to succeed. Omitting the parameter will default the
        connect timeout to the system default, probably `the global default
        timeout in socket.py
        <http://hg.python.org/cpython/file/603b4d593758/Lib/socket.py#l535>`_.
        None will set an infinite timeout for connection attempts.

    :type connect: int, float, or None

    :param read:
        The maximum amount of time (in seconds) to wait between consecutive
        read operations for a response from the server. Omitting the parameter
        will default the read timeout to the system default, probably `the
        global default timeout in socket.py
        <http://hg.python.org/cpython/file/603b4d593758/Lib/socket.py#l535>`_.
        None will set an infinite timeout.

    :type read: int, float, or None

    .. note::

        Many factors can affect the total amount of time for urllib3 to return
        an HTTP response.

        For example, Python's DNS resolver does not obey the timeout specified
        on the socket. Other factors that can affect total request time include
        high CPU load, high swap, the program running at a low priority level,
        or other behaviors.

        In addition, the read and total timeouts only measure the time between
        read operations on the socket connecting the client and the server,
        not the total amount of time for the request to return a complete
        response. For most requests, the timeout is raised because the server
        has not sent the first byte in the specified time. This is not always
        the case; if a server streams one byte every fifteen seconds, a timeout
        of 20 seconds will not trigger, even though the request will take
        several minutes to complete.

        If your goal is to cut off any request after a set amount of wall clock
        time, consider having a second \"watcher\" thread to cut off a slow
        request.
    \"\"\"

    #: A sentinel object representing the default timeout value
    DEFAULT_TIMEOUT = _GLOBAL_DEFAULT_TIMEOUT

    def __init__(self, total=None, connect=_Default, read=_Default):
        self._connect = self._validate_timeout(connect, \"connect\")
        self._read = self._validate_timeout(read, \"read\")
        self.total = self._validate_timeout(total, \"total\")
        self._start_connect = None

    def __repr__(self):
        return \"%s(connect=%r, read=%r, total=%r)\" % (
            type(self).__name__,
            self._connect,
            self._read,
            self.total,
        )

    # __str__ provided for backwards compatibility
    __str__ = __repr__

    @classmethod
    def _validate_timeout(cls, value, name):
        \"\"\"Check that a timeout attribute is valid.

        :param value: The timeout value to validate
        :param name: The name of the timeout attribute to validate. This is
            used to specify in error messages.
        :return: The validated and casted version of the given value.
        :raises ValueError: If it is a numeric value less than or equal to
            zero, or the type is not an integer, float, or None.
        \"\"\"
        if value is _Default:
            return cls.DEFAULT_TIMEOUT

        if value is None or value is cls.DEFAULT_TIMEOUT:
            return value

        if isinstance(value, bool):
            raise ValueError(
                \"Timeout cannot be a boolean value. It must \"
                \"be an int, float or None.\"
            )
        try:
            float(value)
        except (TypeError, ValueError):
            raise ValueError(
                \"Timeout value %s was %s, but it must be an \"
                \"int, float or None.\" % (name, value)
            )

        try:
            if value <= 0:
                raise ValueError(
                    \"Attempted to set %s timeout to %s, but the \"
                    \"timeout cannot be set to a value less \"
                    \"than or equal to 0.\" % (name, value)
                )
        except TypeError:
            # Python 3
            raise ValueError(
                \"Timeout value %s was %s, but it must be an \"
                \"int, float or None.\" % (name, value)
            )

        return value

    @classmethod
    def from_float(cls, timeout):
        \"\"\"Create a new Timeout from a legacy timeout value.

        The timeout value used by httplib.py sets the same timeout on the
        connect(), and recv() socket requests. This creates a :class:`Timeout`
        object that sets the individual timeouts to the ``timeout`` value
        passed to this function.

        :param timeout: The legacy timeout value.
        :type timeout: integer, float, sentinel default object, or None
        :return: Timeout object
        :rtype: :class:`Timeout`
        \"\"\"
        return Timeout(read=timeout, connect=timeout)

    def clone(self):
        \"\"\"Create a copy of the timeout object

        Timeout properties are stored per-pool but each request needs a fresh
        Timeout object to ensure each one has its own start/stop configured.

        :return: a copy of the timeout object
        :rtype: :class:`Timeout`
        \"\"\"
        # We can't use copy.deepcopy because that will also create a new object
        # for _GLOBAL_DEFAULT_TIMEOUT, which socket.py uses as a sentinel to
        # detect the user default.
        return Timeout(connect=self._connect, read=self._read, total=self.total)

    def start_connect(self):
        \"\"\"Start the timeout clock, used during a connect() attempt

        :raises urllib3.exceptions.TimeoutStateError: if you attempt
            to start a timer that has been started already.
        \"\"\"
        if self._start_connect is not None:
            raise TimeoutStateError(\"Timeout timer has already been started.\")
        self._start_connect = current_time()
        return self._start_connect

    def get_connect_duration(self):
        \"\"\"Gets the time elapsed since the call to :meth:`start_connect`.

        :return: Elapsed time in seconds.
        :rtype: float
        :raises urllib3.exceptions.TimeoutStateError: if you attempt
            to get duration for a timer that hasn't been started.
        \"\"\"
        if self._start_connect is None:
            raise TimeoutStateError(
                \"Can't get connect duration for timer that has not started.\"
            )
        return current_time() - self._start_connect

    @property
    def connect_timeout(self):
        \"\"\"Get the value to use when setting a connection timeout.

        This will be a positive float or integer, the value None
        (never timeout), or the default system timeout.

        :return: Connect timeout.
        :rtype: int, float, :attr:`Timeout.DEFAULT_TIMEOUT` or None
        \"\"\"
        if self.total is None:
            return self._connect

        if self._connect is None or self._connect is self.DEFAULT_TIMEOUT:
            return self.total

        return min(self._connect, self.total)

    @property
    def read_timeout(self):
        \"\"\"Get the value for the read timeout.

        This assumes some time has elapsed in the connection timeout and
        computes the read timeout appropriately.

        If self.total is set, the read timeout is dependent on the amount of
        time taken by the connect timeout. If the connection time has not been
        established, a :exc:`~urllib3.exceptions.TimeoutStateError` will be
        raised.

        :return: Value to use for the read timeout.
        :rtype: int, float, :attr:`Timeout.DEFAULT_TIMEOUT` or None
        :raises urllib3.exceptions.TimeoutStateError: If :meth:`start_connect`
            has not yet been called on this object.
        \"\"\"
        if (
            self.total is not None
            and self.total is not self.DEFAULT_TIMEOUT
            and self._read is not None
            and self._read is not self.DEFAULT_TIMEOUT
        ):
            # In case the connect timeout has not yet been established.
            if self._start_connect is None:
                return self._read
            return max(0, min(self.total - self.get_connect_duration(), self._read))
        elif self.total is not None and self.total is not self.DEFAULT_TIMEOUT:
            return max(0, self.total - self.get_connect_duration())
        else:
            return self._read

"""
module_dict["urllib3"+os.sep+"util"+os.sep+"retry.py"]="""
from __future__ import absolute_import

import email
import logging
import re
import time
import warnings
from collections import namedtuple
from itertools import takewhile

from ..exceptions import (
    ConnectTimeoutError,
    InvalidHeader,
    MaxRetryError,
    ProtocolError,
    ProxyError,
    ReadTimeoutError,
    ResponseError,
)
from ..packages import six

log = logging.getLogger(__name__)


# Data structure for representing the metadata of requests that result in a retry.
RequestHistory = namedtuple(
    \"RequestHistory\", [\"method\", \"url\", \"error\", \"status\", \"redirect_location\"]
)


# TODO: In v2 we can remove this sentinel and metaclass with deprecated options.
_Default = object()


class _RetryMeta(type):
    @property
    def DEFAULT_METHOD_WHITELIST(cls):
        warnings.warn(
            \"Using 'Retry.DEFAULT_METHOD_WHITELIST' is deprecated and \"
            \"will be removed in v2.0. Use 'Retry.DEFAULT_ALLOWED_METHODS' instead\",
            DeprecationWarning,
        )
        return cls.DEFAULT_ALLOWED_METHODS

    @DEFAULT_METHOD_WHITELIST.setter
    def DEFAULT_METHOD_WHITELIST(cls, value):
        warnings.warn(
            \"Using 'Retry.DEFAULT_METHOD_WHITELIST' is deprecated and \"
            \"will be removed in v2.0. Use 'Retry.DEFAULT_ALLOWED_METHODS' instead\",
            DeprecationWarning,
        )
        cls.DEFAULT_ALLOWED_METHODS = value

    @property
    def DEFAULT_REDIRECT_HEADERS_BLACKLIST(cls):
        warnings.warn(
            \"Using 'Retry.DEFAULT_REDIRECT_HEADERS_BLACKLIST' is deprecated and \"
            \"will be removed in v2.0. Use 'Retry.DEFAULT_REMOVE_HEADERS_ON_REDIRECT' instead\",
            DeprecationWarning,
        )
        return cls.DEFAULT_REMOVE_HEADERS_ON_REDIRECT

    @DEFAULT_REDIRECT_HEADERS_BLACKLIST.setter
    def DEFAULT_REDIRECT_HEADERS_BLACKLIST(cls, value):
        warnings.warn(
            \"Using 'Retry.DEFAULT_REDIRECT_HEADERS_BLACKLIST' is deprecated and \"
            \"will be removed in v2.0. Use 'Retry.DEFAULT_REMOVE_HEADERS_ON_REDIRECT' instead\",
            DeprecationWarning,
        )
        cls.DEFAULT_REMOVE_HEADERS_ON_REDIRECT = value

    @property
    def BACKOFF_MAX(cls):
        warnings.warn(
            \"Using 'Retry.BACKOFF_MAX' is deprecated and \"
            \"will be removed in v2.0. Use 'Retry.DEFAULT_BACKOFF_MAX' instead\",
            DeprecationWarning,
        )
        return cls.DEFAULT_BACKOFF_MAX

    @BACKOFF_MAX.setter
    def BACKOFF_MAX(cls, value):
        warnings.warn(
            \"Using 'Retry.BACKOFF_MAX' is deprecated and \"
            \"will be removed in v2.0. Use 'Retry.DEFAULT_BACKOFF_MAX' instead\",
            DeprecationWarning,
        )
        cls.DEFAULT_BACKOFF_MAX = value


@six.add_metaclass(_RetryMeta)
class Retry(object):
    \"\"\"Retry configuration.

    Each retry attempt will create a new Retry object with updated values, so
    they can be safely reused.

    Retries can be defined as a default for a pool::

        retries = Retry(connect=5, read=2, redirect=5)
        http = PoolManager(retries=retries)
        response = http.request('GET', 'http://example.com/')

    Or per-request (which overrides the default for the pool)::

        response = http.request('GET', 'http://example.com/', retries=Retry(10))

    Retries can be disabled by passing ``False``::

        response = http.request('GET', 'http://example.com/', retries=False)

    Errors will be wrapped in :class:`~urllib3.exceptions.MaxRetryError` unless
    retries are disabled, in which case the causing exception will be raised.

    :param int total:
        Total number of retries to allow. Takes precedence over other counts.

        Set to ``None`` to remove this constraint and fall back on other
        counts.

        Set to ``0`` to fail on the first retry.

        Set to ``False`` to disable and imply ``raise_on_redirect=False``.

    :param int connect:
        How many connection-related errors to retry on.

        These are errors raised before the request is sent to the remote server,
        which we assume has not triggered the server to process the request.

        Set to ``0`` to fail on the first retry of this type.

    :param int read:
        How many times to retry on read errors.

        These errors are raised after the request was sent to the server, so the
        request may have side-effects.

        Set to ``0`` to fail on the first retry of this type.

    :param int redirect:
        How many redirects to perform. Limit this to avoid infinite redirect
        loops.

        A redirect is a HTTP response with a status code 301, 302, 303, 307 or
        308.

        Set to ``0`` to fail on the first retry of this type.

        Set to ``False`` to disable and imply ``raise_on_redirect=False``.

    :param int status:
        How many times to retry on bad status codes.

        These are retries made on responses, where status code matches
        ``status_forcelist``.

        Set to ``0`` to fail on the first retry of this type.

    :param int other:
        How many times to retry on other errors.

        Other errors are errors that are not connect, read, redirect or status errors.
        These errors might be raised after the request was sent to the server, so the
        request might have side-effects.

        Set to ``0`` to fail on the first retry of this type.

        If ``total`` is not set, it's a good idea to set this to 0 to account
        for unexpected edge cases and avoid infinite retry loops.

    :param iterable allowed_methods:
        Set of uppercased HTTP method verbs that we should retry on.

        By default, we only retry on methods which are considered to be
        idempotent (multiple requests with the same parameters end with the
        same state). See :attr:`Retry.DEFAULT_ALLOWED_METHODS`.

        Set to a ``False`` value to retry on any verb.

        .. warning::

            Previously this parameter was named ``method_whitelist``, that
            usage is deprecated in v1.26.0 and will be removed in v2.0.

    :param iterable status_forcelist:
        A set of integer HTTP status codes that we should force a retry on.
        A retry is initiated if the request method is in ``allowed_methods``
        and the response status code is in ``status_forcelist``.

        By default, this is disabled with ``None``.

    :param float backoff_factor:
        A backoff factor to apply between attempts after the second try
        (most errors are resolved immediately by a second try without a
        delay). urllib3 will sleep for::

            {backoff factor} * (2 ** ({number of total retries} - 1))

        seconds. If the backoff_factor is 0.1, then :func:`.sleep` will sleep
        for [0.0s, 0.2s, 0.4s, ...] between retries. It will never be longer
        than :attr:`Retry.DEFAULT_BACKOFF_MAX`.

        By default, backoff is disabled (set to 0).

    :param bool raise_on_redirect: Whether, if the number of redirects is
        exhausted, to raise a MaxRetryError, or to return a response with a
        response code in the 3xx range.

    :param bool raise_on_status: Similar meaning to ``raise_on_redirect``:
        whether we should raise an exception, or return a response,
        if status falls in ``status_forcelist`` range and retries have
        been exhausted.

    :param tuple history: The history of the request encountered during
        each call to :meth:`~Retry.increment`. The list is in the order
        the requests occurred. Each list item is of class :class:`RequestHistory`.

    :param bool respect_retry_after_header:
        Whether to respect Retry-After header on status codes defined as
        :attr:`Retry.RETRY_AFTER_STATUS_CODES` or not.

    :param iterable remove_headers_on_redirect:
        Sequence of headers to remove from the request when a response
        indicating a redirect is returned before firing off the redirected
        request.
    \"\"\"

    #: Default methods to be used for ``allowed_methods``
    DEFAULT_ALLOWED_METHODS = frozenset(
        [\"HEAD\", \"GET\", \"PUT\", \"DELETE\", \"OPTIONS\", \"TRACE\"]
    )

    #: Default status codes to be used for ``status_forcelist``
    RETRY_AFTER_STATUS_CODES = frozenset([413, 429, 503])

    #: Default headers to be used for ``remove_headers_on_redirect``
    DEFAULT_REMOVE_HEADERS_ON_REDIRECT = frozenset([\"Authorization\"])

    #: Maximum backoff time.
    DEFAULT_BACKOFF_MAX = 120

    def __init__(
        self,
        total=10,
        connect=None,
        read=None,
        redirect=None,
        status=None,
        other=None,
        allowed_methods=_Default,
        status_forcelist=None,
        backoff_factor=0,
        raise_on_redirect=True,
        raise_on_status=True,
        history=None,
        respect_retry_after_header=True,
        remove_headers_on_redirect=_Default,
        # TODO: Deprecated, remove in v2.0
        method_whitelist=_Default,
    ):

        if method_whitelist is not _Default:
            if allowed_methods is not _Default:
                raise ValueError(
                    \"Using both 'allowed_methods' and \"
                    \"'method_whitelist' together is not allowed. \"
                    \"Instead only use 'allowed_methods'\"
                )
            warnings.warn(
                \"Using 'method_whitelist' with Retry is deprecated and \"
                \"will be removed in v2.0. Use 'allowed_methods' instead\",
                DeprecationWarning,
                stacklevel=2,
            )
            allowed_methods = method_whitelist
        if allowed_methods is _Default:
            allowed_methods = self.DEFAULT_ALLOWED_METHODS
        if remove_headers_on_redirect is _Default:
            remove_headers_on_redirect = self.DEFAULT_REMOVE_HEADERS_ON_REDIRECT

        self.total = total
        self.connect = connect
        self.read = read
        self.status = status
        self.other = other

        if redirect is False or total is False:
            redirect = 0
            raise_on_redirect = False

        self.redirect = redirect
        self.status_forcelist = status_forcelist or set()
        self.allowed_methods = allowed_methods
        self.backoff_factor = backoff_factor
        self.raise_on_redirect = raise_on_redirect
        self.raise_on_status = raise_on_status
        self.history = history or tuple()
        self.respect_retry_after_header = respect_retry_after_header
        self.remove_headers_on_redirect = frozenset(
            [h.lower() for h in remove_headers_on_redirect]
        )

    def new(self, **kw):
        params = dict(
            total=self.total,
            connect=self.connect,
            read=self.read,
            redirect=self.redirect,
            status=self.status,
            other=self.other,
            status_forcelist=self.status_forcelist,
            backoff_factor=self.backoff_factor,
            raise_on_redirect=self.raise_on_redirect,
            raise_on_status=self.raise_on_status,
            history=self.history,
            remove_headers_on_redirect=self.remove_headers_on_redirect,
            respect_retry_after_header=self.respect_retry_after_header,
        )

        # TODO: If already given in **kw we use what's given to us
        # If not given we need to figure out what to pass. We decide
        # based on whether our class has the 'method_whitelist' property
        # and if so we pass the deprecated 'method_whitelist' otherwise
        # we use 'allowed_methods'. Remove in v2.0
        if \"method_whitelist\" not in kw and \"allowed_methods\" not in kw:
            if \"method_whitelist\" in self.__dict__:
                warnings.warn(
                    \"Using 'method_whitelist' with Retry is deprecated and \"
                    \"will be removed in v2.0. Use 'allowed_methods' instead\",
                    DeprecationWarning,
                )
                params[\"method_whitelist\"] = self.allowed_methods
            else:
                params[\"allowed_methods\"] = self.allowed_methods

        params.update(kw)
        return type(self)(**params)

    @classmethod
    def from_int(cls, retries, redirect=True, default=None):
        \"\"\"Backwards-compatibility for the old retries format.\"\"\"
        if retries is None:
            retries = default if default is not None else cls.DEFAULT

        if isinstance(retries, Retry):
            return retries

        redirect = bool(redirect) and None
        new_retries = cls(retries, redirect=redirect)
        log.debug(\"Converted retries value: %r -> %r\", retries, new_retries)
        return new_retries

    def get_backoff_time(self):
        \"\"\"Formula for computing the current backoff

        :rtype: float
        \"\"\"
        # We want to consider only the last consecutive errors sequence (Ignore redirects).
        consecutive_errors_len = len(
            list(
                takewhile(lambda x: x.redirect_location is None, reversed(self.history))
            )
        )
        if consecutive_errors_len <= 1:
            return 0

        backoff_value = self.backoff_factor * (2 ** (consecutive_errors_len - 1))
        return min(self.DEFAULT_BACKOFF_MAX, backoff_value)

    def parse_retry_after(self, retry_after):
        # Whitespace: https://tools.ietf.org/html/rfc7230#section-3.2.4
        if re.match(r\"^\\s*[0-9]+\\s*$\", retry_after):
            seconds = int(retry_after)
        else:
            retry_date_tuple = email.utils.parsedate_tz(retry_after)
            if retry_date_tuple is None:
                raise InvalidHeader(\"Invalid Retry-After header: %s\" % retry_after)
            if retry_date_tuple[9] is None:  # Python 2
                # Assume UTC if no timezone was specified
                # On Python2.7, parsedate_tz returns None for a timezone offset
                # instead of 0 if no timezone is given, where mktime_tz treats
                # a None timezone offset as local time.
                retry_date_tuple = retry_date_tuple[:9] + (0,) + retry_date_tuple[10:]

            retry_date = email.utils.mktime_tz(retry_date_tuple)
            seconds = retry_date - time.time()

        if seconds < 0:
            seconds = 0

        return seconds

    def get_retry_after(self, response):
        \"\"\"Get the value of Retry-After in seconds.\"\"\"

        retry_after = response.getheader(\"Retry-After\")

        if retry_after is None:
            return None

        return self.parse_retry_after(retry_after)

    def sleep_for_retry(self, response=None):
        retry_after = self.get_retry_after(response)
        if retry_after:
            time.sleep(retry_after)
            return True

        return False

    def _sleep_backoff(self):
        backoff = self.get_backoff_time()
        if backoff <= 0:
            return
        time.sleep(backoff)

    def sleep(self, response=None):
        \"\"\"Sleep between retry attempts.

        This method will respect a server's ``Retry-After`` response header
        and sleep the duration of the time requested. If that is not present, it
        will use an exponential backoff. By default, the backoff factor is 0 and
        this method will return immediately.
        \"\"\"

        if self.respect_retry_after_header and response:
            slept = self.sleep_for_retry(response)
            if slept:
                return

        self._sleep_backoff()

    def _is_connection_error(self, err):
        \"\"\"Errors when we're fairly sure that the server did not receive the
        request, so it should be safe to retry.
        \"\"\"
        if isinstance(err, ProxyError):
            err = err.original_error
        return isinstance(err, ConnectTimeoutError)

    def _is_read_error(self, err):
        \"\"\"Errors that occur after the request has been started, so we should
        assume that the server began processing it.
        \"\"\"
        return isinstance(err, (ReadTimeoutError, ProtocolError))

    def _is_method_retryable(self, method):
        \"\"\"Checks if a given HTTP method should be retried upon, depending if
        it is included in the allowed_methods
        \"\"\"
        # TODO: For now favor if the Retry implementation sets its own method_whitelist
        # property outside of our constructor to avoid breaking custom implementations.
        if \"method_whitelist\" in self.__dict__:
            warnings.warn(
                \"Using 'method_whitelist' with Retry is deprecated and \"
                \"will be removed in v2.0. Use 'allowed_methods' instead\",
                DeprecationWarning,
            )
            allowed_methods = self.method_whitelist
        else:
            allowed_methods = self.allowed_methods

        if allowed_methods and method.upper() not in allowed_methods:
            return False
        return True

    def is_retry(self, method, status_code, has_retry_after=False):
        \"\"\"Is this method/status code retryable? (Based on allowlists and control
        variables such as the number of total retries to allow, whether to
        respect the Retry-After header, whether this header is present, and
        whether the returned status code is on the list of status codes to
        be retried upon on the presence of the aforementioned header)
        \"\"\"
        if not self._is_method_retryable(method):
            return False

        if self.status_forcelist and status_code in self.status_forcelist:
            return True

        return (
            self.total
            and self.respect_retry_after_header
            and has_retry_after
            and (status_code in self.RETRY_AFTER_STATUS_CODES)
        )

    def is_exhausted(self):
        \"\"\"Are we out of retries?\"\"\"
        retry_counts = (
            self.total,
            self.connect,
            self.read,
            self.redirect,
            self.status,
            self.other,
        )
        retry_counts = list(filter(None, retry_counts))
        if not retry_counts:
            return False

        return min(retry_counts) < 0

    def increment(
        self,
        method=None,
        url=None,
        response=None,
        error=None,
        _pool=None,
        _stacktrace=None,
    ):
        \"\"\"Return a new Retry object with incremented retry counters.

        :param response: A response object, or None, if the server did not
            return a response.
        :type response: :class:`~urllib3.response.HTTPResponse`
        :param Exception error: An error encountered during the request, or
            None if the response was received successfully.

        :return: A new ``Retry`` object.
        \"\"\"
        if self.total is False and error:
            # Disabled, indicate to re-raise the error.
            raise six.reraise(type(error), error, _stacktrace)

        total = self.total
        if total is not None:
            total -= 1

        connect = self.connect
        read = self.read
        redirect = self.redirect
        status_count = self.status
        other = self.other
        cause = \"unknown\"
        status = None
        redirect_location = None

        if error and self._is_connection_error(error):
            # Connect retry?
            if connect is False:
                raise six.reraise(type(error), error, _stacktrace)
            elif connect is not None:
                connect -= 1

        elif error and self._is_read_error(error):
            # Read retry?
            if read is False or not self._is_method_retryable(method):
                raise six.reraise(type(error), error, _stacktrace)
            elif read is not None:
                read -= 1

        elif error:
            # Other retry?
            if other is not None:
                other -= 1

        elif response and response.get_redirect_location():
            # Redirect retry?
            if redirect is not None:
                redirect -= 1
            cause = \"too many redirects\"
            redirect_location = response.get_redirect_location()
            status = response.status

        else:
            # Incrementing because of a server error like a 500 in
            # status_forcelist and the given method is in the allowed_methods
            cause = ResponseError.GENERIC_ERROR
            if response and response.status:
                if status_count is not None:
                    status_count -= 1
                cause = ResponseError.SPECIFIC_ERROR.format(status_code=response.status)
                status = response.status

        history = self.history + (
            RequestHistory(method, url, error, status, redirect_location),
        )

        new_retry = self.new(
            total=total,
            connect=connect,
            read=read,
            redirect=redirect,
            status=status_count,
            other=other,
            history=history,
        )

        if new_retry.is_exhausted():
            raise MaxRetryError(_pool, url, error or ResponseError(cause))

        log.debug(\"Incremented Retry for (url='%s'): %r\", url, new_retry)

        return new_retry

    def __repr__(self):
        return (
            \"{cls.__name__}(total={self.total}, connect={self.connect}, \"
            \"read={self.read}, redirect={self.redirect}, status={self.status})\"
        ).format(cls=type(self), self=self)

    def __getattr__(self, item):
        if item == \"method_whitelist\":
            # TODO: Remove this deprecated alias in v2.0
            warnings.warn(
                \"Using 'method_whitelist' with Retry is deprecated and \"
                \"will be removed in v2.0. Use 'allowed_methods' instead\",
                DeprecationWarning,
            )
            return self.allowed_methods
        try:
            return getattr(super(Retry, self), item)
        except AttributeError:
            return getattr(Retry, item)


# For backwards compatibility (equivalent to pre-v1.9):
Retry.DEFAULT = Retry(3)

"""
module_dict["urllib3"+os.sep+"util"+os.sep+"request.py"]="""
from __future__ import absolute_import
# < include 'brotlicffi.py' >

# < include 'brotli.py' >


from base64 import b64encode

from ..exceptions import UnrewindableBodyError
from ..packages.six import b, integer_types

# Pass as a value within ``headers`` to skip
# emitting some HTTP headers that are added automatically.
# The only headers that are supported are ``Accept-Encoding``,
# ``Host``, and ``User-Agent``.
SKIP_HEADER = \"@@@SKIP_HEADER@@@\"
SKIPPABLE_HEADERS = frozenset([\"accept-encoding\", \"host\", \"user-agent\"])

ACCEPT_ENCODING = \"gzip,deflate\"
try:
    try:
        import brotlicffi as _unused_module_brotli  # noqa: F401
    except ImportError:
        import brotli as _unused_module_brotli  # noqa: F401
except ImportError:
    pass
else:
    ACCEPT_ENCODING += \",br\"

_FAILEDTELL = object()


def make_headers(
    keep_alive=None,
    accept_encoding=None,
    user_agent=None,
    basic_auth=None,
    proxy_basic_auth=None,
    disable_cache=None,
):
    \"\"\"
    Shortcuts for generating request headers.

    :param keep_alive:
        If ``True``, adds 'connection: keep-alive' header.

    :param accept_encoding:
        Can be a boolean, list, or string.
        ``True`` translates to 'gzip,deflate'.
        List will get joined by comma.
        String will be used as provided.

    :param user_agent:
        String representing the user-agent you want, such as
        \"python-urllib3/0.6\"

    :param basic_auth:
        Colon-separated username:password string for 'authorization: basic ...'
        auth header.

    :param proxy_basic_auth:
        Colon-separated username:password string for 'proxy-authorization: basic ...'
        auth header.

    :param disable_cache:
        If ``True``, adds 'cache-control: no-cache' header.

    Example::

        >>> make_headers(keep_alive=True, user_agent=\"Batman/1.0\")
        {'connection': 'keep-alive', 'user-agent': 'Batman/1.0'}
        >>> make_headers(accept_encoding=True)
        {'accept-encoding': 'gzip,deflate'}
    \"\"\"
    headers = {}
    if accept_encoding:
        if isinstance(accept_encoding, str):
            pass
        elif isinstance(accept_encoding, list):
            accept_encoding = \",\".join(accept_encoding)
        else:
            accept_encoding = ACCEPT_ENCODING
        headers[\"accept-encoding\"] = accept_encoding

    if user_agent:
        headers[\"user-agent\"] = user_agent

    if keep_alive:
        headers[\"connection\"] = \"keep-alive\"

    if basic_auth:
        headers[\"authorization\"] = \"Basic \" + b64encode(b(basic_auth)).decode(\"utf-8\")

    if proxy_basic_auth:
        headers[\"proxy-authorization\"] = \"Basic \" + b64encode(
            b(proxy_basic_auth)
        ).decode(\"utf-8\")

    if disable_cache:
        headers[\"cache-control\"] = \"no-cache\"

    return headers


def set_file_position(body, pos):
    \"\"\"
    If a position is provided, move file to that point.
    Otherwise, we'll attempt to record a position for future use.
    \"\"\"
    if pos is not None:
        rewind_body(body, pos)
    elif getattr(body, \"tell\", None) is not None:
        try:
            pos = body.tell()
        except (IOError, OSError):
            # This differentiates from None, allowing us to catch
            # a failed `tell()` later when trying to rewind the body.
            pos = _FAILEDTELL

    return pos


def rewind_body(body, body_pos):
    \"\"\"
    Attempt to rewind body to a certain position.
    Primarily used for request redirects and retries.

    :param body:
        File-like object that supports seek.

    :param int pos:
        Position to seek to in file.
    \"\"\"
    body_seek = getattr(body, \"seek\", None)
    if body_seek is not None and isinstance(body_pos, integer_types):
        try:
            body_seek(body_pos)
        except (IOError, OSError):
            raise UnrewindableBodyError(
                \"An error occurred when rewinding request body for redirect/retry.\"
            )
    elif body_pos is _FAILEDTELL:
        raise UnrewindableBodyError(
            \"Unable to record file position for rewinding \"
            \"request body during a redirect/retry.\"
        )
    else:
        raise ValueError(
            \"body_pos must be of type integer, instead it was %s.\" % type(body_pos)
        )

"""
module_dict["urllib3"+os.sep+"util"+os.sep+"ssl_match_hostname.py"]="""
\"\"\"The match_hostname() function from Python 3.3.3, essential when using SSL.\"\"\"

# Note: This file is under the PSF license as the code comes from the python
# stdlib.   http://docs.python.org/3/license.html

import re
import sys

# ipaddress has been backported to 2.6+ in pypi.  If it is installed on the
# system, use it to handle IPAddress ServerAltnames (this was added in
# python-3.5) otherwise only do DNS matching.  This allows
# util.ssl_match_hostname to continue to be used in Python 2.7.
try:
    import ipaddress
except ImportError:
    ipaddress = None

__version__ = \"3.5.0.1\"


class CertificateError(ValueError):
    pass


def _dnsname_match(dn, hostname, max_wildcards=1):
    \"\"\"Matching according to RFC 6125, section 6.4.3

    http://tools.ietf.org/html/rfc6125#section-6.4.3
    \"\"\"
    pats = []
    if not dn:
        return False

    # Ported from python3-syntax:
    # leftmost, *remainder = dn.split(r'.')
    parts = dn.split(r\".\")
    leftmost = parts[0]
    remainder = parts[1:]

    wildcards = leftmost.count(\"*\")
    if wildcards > max_wildcards:
        # Issue #17980: avoid denials of service by refusing more
        # than one wildcard per fragment.  A survey of established
        # policy among SSL implementations showed it to be a
        # reasonable choice.
        raise CertificateError(
            \"too many wildcards in certificate DNS name: \" + repr(dn)
        )

    # speed up common case w/o wildcards
    if not wildcards:
        return dn.lower() == hostname.lower()

    # RFC 6125, section 6.4.3, subitem 1.
    # The client SHOULD NOT attempt to match a presented identifier in which
    # the wildcard character comprises a label other than the left-most label.
    if leftmost == \"*\":
        # When '*' is a fragment by itself, it matches a non-empty dotless
        # fragment.
        pats.append(\"[^.]+\")
    elif leftmost.startswith(\"xn--\") or hostname.startswith(\"xn--\"):
        # RFC 6125, section 6.4.3, subitem 3.
        # The client SHOULD NOT attempt to match a presented identifier
        # where the wildcard character is embedded within an A-label or
        # U-label of an internationalized domain name.
        pats.append(re.escape(leftmost))
    else:
        # Otherwise, '*' matches any dotless string, e.g. www*
        pats.append(re.escape(leftmost).replace(r\"\\*\", \"[^.]*\"))

    # add the remaining fragments, ignore any wildcards
    for frag in remainder:
        pats.append(re.escape(frag))

    pat = re.compile(r\"\\A\" + r\"\\.\".join(pats) + r\"\\Z\", re.IGNORECASE)
    return pat.match(hostname)


def _to_unicode(obj):
    if isinstance(obj, str) and sys.version_info < (3,):
        # ignored flake8 # F821 to support python 2.7 function
        obj = unicode(obj, encoding=\"ascii\", errors=\"strict\")  # noqa: F821
    return obj


def _ipaddress_match(ipname, host_ip):
    \"\"\"Exact matching of IP addresses.

    RFC 6125 explicitly doesn't define an algorithm for this
    (section 1.7.2 - \"Out of Scope\").
    \"\"\"
    # OpenSSL may add a trailing newline to a subjectAltName's IP address
    # Divergence from upstream: ipaddress can't handle byte str
    ip = ipaddress.ip_address(_to_unicode(ipname).rstrip())
    return ip == host_ip


def match_hostname(cert, hostname):
    \"\"\"Verify that *cert* (in decoded format as returned by
    SSLSocket.getpeercert()) matches the *hostname*.  RFC 2818 and RFC 6125
    rules are followed, but IP addresses are not accepted for *hostname*.

    CertificateError is raised on failure. On success, the function
    returns nothing.
    \"\"\"
    if not cert:
        raise ValueError(
            \"empty or no certificate, match_hostname needs a \"
            \"SSL socket or SSL context with either \"
            \"CERT_OPTIONAL or CERT_REQUIRED\"
        )
    try:
        # Divergence from upstream: ipaddress can't handle byte str
        host_ip = ipaddress.ip_address(_to_unicode(hostname))
    except (UnicodeError, ValueError):
        # ValueError: Not an IP address (common case)
        # UnicodeError: Divergence from upstream: Have to deal with ipaddress not taking
        # byte strings.  addresses should be all ascii, so we consider it not
        # an ipaddress in this case
        host_ip = None
    except AttributeError:
        # Divergence from upstream: Make ipaddress library optional
        if ipaddress is None:
            host_ip = None
        else:  # Defensive
            raise
    dnsnames = []
    san = cert.get(\"subjectAltName\", ())
    for key, value in san:
        if key == \"DNS\":
            if host_ip is None and _dnsname_match(value, hostname):
                return
            dnsnames.append(value)
        elif key == \"IP Address\":
            if host_ip is not None and _ipaddress_match(value, host_ip):
                return
            dnsnames.append(value)
    if not dnsnames:
        # The subject is only checked when there is no dNSName entry
        # in subjectAltName
        for sub in cert.get(\"subject\", ()):
            for key, value in sub:
                # XXX according to RFC 2818, the most specific Common Name
                # must be used.
                if key == \"commonName\":
                    if _dnsname_match(value, hostname):
                        return
                    dnsnames.append(value)
    if len(dnsnames) > 1:
        raise CertificateError(
            \"hostname %r \"
            \"doesn't match either of %s\" % (hostname, \", \".join(map(repr, dnsnames)))
        )
    elif len(dnsnames) == 1:
        raise CertificateError(\"hostname %r doesn't match %r\" % (hostname, dnsnames[0]))
    else:
        raise CertificateError(
            \"no appropriate commonName or subjectAltName fields were found\"
        )

"""
module_dict["urllib3"+os.sep+"util"+os.sep+"response.py"]="""
from __future__ import absolute_import

from email.errors import MultipartInvariantViolationDefect, StartBoundaryNotFoundDefect

from ..exceptions import HeaderParsingError
from ..packages.six.moves import http_client as httplib


def is_fp_closed(obj):
    \"\"\"
    Checks whether a given file-like object is closed.

    :param obj:
        The file-like object to check.
    \"\"\"

    try:
        # Check `isclosed()` first, in case Python3 doesn't set `closed`.
        # GH Issue #928
        return obj.isclosed()
    except AttributeError:
        pass

    try:
        # Check via the official file-like-object way.
        return obj.closed
    except AttributeError:
        pass

    try:
        # Check if the object is a container for another file-like object that
        # gets released on exhaustion (e.g. HTTPResponse).
        return obj.fp is None
    except AttributeError:
        pass

    raise ValueError(\"Unable to determine whether fp is closed.\")


def assert_header_parsing(headers):
    \"\"\"
    Asserts whether all headers have been successfully parsed.
    Extracts encountered errors from the result of parsing headers.

    Only works on Python 3.

    :param http.client.HTTPMessage headers: Headers to verify.

    :raises urllib3.exceptions.HeaderParsingError:
        If parsing errors are found.
    \"\"\"

    # This will fail silently if we pass in the wrong kind of parameter.
    # To make debugging easier add an explicit check.
    if not isinstance(headers, httplib.HTTPMessage):
        raise TypeError(\"expected httplib.Message, got {0}.\".format(type(headers)))

    defects = getattr(headers, \"defects\", None)
    get_payload = getattr(headers, \"get_payload\", None)

    unparsed_data = None
    if get_payload:
        # get_payload is actually email.message.Message.get_payload;
        # we're only interested in the result if it's not a multipart message
        if not headers.is_multipart():
            payload = get_payload()

            if isinstance(payload, (bytes, str)):
                unparsed_data = payload
    if defects:
        # httplib is assuming a response body is available
        # when parsing headers even when httplib only sends
        # header data to parse_headers() This results in
        # defects on multipart responses in particular.
        # See: https://github.com/urllib3/urllib3/issues/800

        # So we ignore the following defects:
        # - StartBoundaryNotFoundDefect:
        #     The claimed start boundary was never found.
        # - MultipartInvariantViolationDefect:
        #     A message claimed to be a multipart but no subparts were found.
        defects = [
            defect
            for defect in defects
            if not isinstance(
                defect, (StartBoundaryNotFoundDefect, MultipartInvariantViolationDefect)
            )
        ]

    if defects or unparsed_data:
        raise HeaderParsingError(defects=defects, unparsed_data=unparsed_data)


def is_response_to_head(response):
    \"\"\"
    Checks whether the request of a response has been a HEAD-request.
    Handles the quirks of AppEngine.

    :param http.client.HTTPResponse response:
        Response to check if the originating request
        used 'HEAD' as a method.
    \"\"\"
    # FIXME: Can we do this somehow without accessing private httplib _method?
    method = response._method
    if isinstance(method, int):  # Platform-specific: Appengine
        return method == 3
    return method.upper() == \"HEAD\"

"""
module_dict["urllib3"+os.sep+"util"+os.sep+"proxy.py"]="""
from .ssl_ import create_urllib3_context, resolve_cert_reqs, resolve_ssl_version


def connection_requires_http_tunnel(
    proxy_url=None, proxy_config=None, destination_scheme=None
):
    \"\"\"
    Returns True if the connection requires an HTTP CONNECT through the proxy.

    :param URL proxy_url:
        URL of the proxy.
    :param ProxyConfig proxy_config:
        Proxy configuration from poolmanager.py
    :param str destination_scheme:
        The scheme of the destination. (i.e https, http, etc)
    \"\"\"
    # If we're not using a proxy, no way to use a tunnel.
    if proxy_url is None:
        return False

    # HTTP destinations never require tunneling, we always forward.
    if destination_scheme == \"http\":
        return False

    # Support for forwarding with HTTPS proxies and HTTPS destinations.
    if (
        proxy_url.scheme == \"https\"
        and proxy_config
        and proxy_config.use_forwarding_for_https
    ):
        return False

    # Otherwise always use a tunnel.
    return True


def create_proxy_ssl_context(
    ssl_version, cert_reqs, ca_certs=None, ca_cert_dir=None, ca_cert_data=None
):
    \"\"\"
    Generates a default proxy ssl context if one hasn't been provided by the
    user.
    \"\"\"
    ssl_context = create_urllib3_context(
        ssl_version=resolve_ssl_version(ssl_version),
        cert_reqs=resolve_cert_reqs(cert_reqs),
    )

    if (
        not ca_certs
        and not ca_cert_dir
        and not ca_cert_data
        and hasattr(ssl_context, \"load_default_certs\")
    ):
        ssl_context.load_default_certs()

    return ssl_context

"""
module_dict["urllib3"+os.sep+"util"+os.sep+"__init__.py"]="""
from __future__ import absolute_import

# For backwards compatibility, provide imports that used to be here.
from .connection import is_connection_dropped
from .request import SKIP_HEADER, SKIPPABLE_HEADERS, make_headers
from .response import is_fp_closed
from .retry import Retry
from .ssl_ import (
    ALPN_PROTOCOLS,
    HAS_SNI,
    IS_PYOPENSSL,
    IS_SECURETRANSPORT,
    PROTOCOL_TLS,
    SSLContext,
    assert_fingerprint,
    resolve_cert_reqs,
    resolve_ssl_version,
    ssl_wrap_socket,
)
from .timeout import Timeout, current_time
from .url import Url, get_host, parse_url, split_first
from .wait import wait_for_read, wait_for_write

__all__ = (
    \"HAS_SNI\",
    \"IS_PYOPENSSL\",
    \"IS_SECURETRANSPORT\",
    \"SSLContext\",
    \"PROTOCOL_TLS\",
    \"ALPN_PROTOCOLS\",
    \"Retry\",
    \"Timeout\",
    \"Url\",
    \"assert_fingerprint\",
    \"current_time\",
    \"is_connection_dropped\",
    \"is_fp_closed\",
    \"get_host\",
    \"parse_url\",
    \"make_headers\",
    \"resolve_cert_reqs\",
    \"resolve_ssl_version\",
    \"split_first\",
    \"ssl_wrap_socket\",
    \"wait_for_read\",
    \"wait_for_write\",
    \"SKIP_HEADER\",
    \"SKIPPABLE_HEADERS\",
)

"""
module_dict["urllib3"+os.sep+"util"+os.sep+"ssltransport.py"]="""
import io
import socket
import ssl

from ..exceptions import ProxySchemeUnsupported
from ..packages import six

SSL_BLOCKSIZE = 16384


class SSLTransport:
    \"\"\"
    The SSLTransport wraps an existing socket and establishes an SSL connection.

    Contrary to Python's implementation of SSLSocket, it allows you to chain
    multiple TLS connections together. It's particularly useful if you need to
    implement TLS within TLS.

    The class supports most of the socket API operations.
    \"\"\"

    @staticmethod
    def _validate_ssl_context_for_tls_in_tls(ssl_context):
        \"\"\"
        Raises a ProxySchemeUnsupported if the provided ssl_context can't be used
        for TLS in TLS.

        The only requirement is that the ssl_context provides the 'wrap_bio'
        methods.
        \"\"\"

        if not hasattr(ssl_context, \"wrap_bio\"):
            if six.PY2:
                raise ProxySchemeUnsupported(
                    \"TLS in TLS requires SSLContext.wrap_bio() which isn't \"
                    \"supported on Python 2\"
                )
            else:
                raise ProxySchemeUnsupported(
                    \"TLS in TLS requires SSLContext.wrap_bio() which isn't \"
                    \"available on non-native SSLContext\"
                )

    def __init__(
        self, socket, ssl_context, server_hostname=None, suppress_ragged_eofs=True
    ):
        \"\"\"
        Create an SSLTransport around socket using the provided ssl_context.
        \"\"\"
        self.incoming = ssl.MemoryBIO()
        self.outgoing = ssl.MemoryBIO()

        self.suppress_ragged_eofs = suppress_ragged_eofs
        self.socket = socket

        self.sslobj = ssl_context.wrap_bio(
            self.incoming, self.outgoing, server_hostname=server_hostname
        )

        # Perform initial handshake.
        self._ssl_io_loop(self.sslobj.do_handshake)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def fileno(self):
        return self.socket.fileno()

    def read(self, len=1024, buffer=None):
        return self._wrap_ssl_read(len, buffer)

    def recv(self, len=1024, flags=0):
        if flags != 0:
            raise ValueError(\"non-zero flags not allowed in calls to recv\")
        return self._wrap_ssl_read(len)

    def recv_into(self, buffer, nbytes=None, flags=0):
        if flags != 0:
            raise ValueError(\"non-zero flags not allowed in calls to recv_into\")
        if buffer and (nbytes is None):
            nbytes = len(buffer)
        elif nbytes is None:
            nbytes = 1024
        return self.read(nbytes, buffer)

    def sendall(self, data, flags=0):
        if flags != 0:
            raise ValueError(\"non-zero flags not allowed in calls to sendall\")
        count = 0
        with memoryview(data) as view, view.cast(\"B\") as byte_view:
            amount = len(byte_view)
            while count < amount:
                v = self.send(byte_view[count:])
                count += v

    def send(self, data, flags=0):
        if flags != 0:
            raise ValueError(\"non-zero flags not allowed in calls to send\")
        response = self._ssl_io_loop(self.sslobj.write, data)
        return response

    def makefile(
        self, mode=\"r\", buffering=None, encoding=None, errors=None, newline=None
    ):
        \"\"\"
        Python's httpclient uses makefile and buffered io when reading HTTP
        messages and we need to support it.

        This is unfortunately a copy and paste of socket.py makefile with small
        changes to point to the socket directly.
        \"\"\"
        if not set(mode) <= {\"r\", \"w\", \"b\"}:
            raise ValueError(\"invalid mode %r (only r, w, b allowed)\" % (mode,))

        writing = \"w\" in mode
        reading = \"r\" in mode or not writing
        assert reading or writing
        binary = \"b\" in mode
        rawmode = \"\"
        if reading:
            rawmode += \"r\"
        if writing:
            rawmode += \"w\"
        raw = socket.SocketIO(self, rawmode)
        self.socket._io_refs += 1
        if buffering is None:
            buffering = -1
        if buffering < 0:
            buffering = io.DEFAULT_BUFFER_SIZE
        if buffering == 0:
            if not binary:
                raise ValueError(\"unbuffered streams must be binary\")
            return raw
        if reading and writing:
            buffer = io.BufferedRWPair(raw, raw, buffering)
        elif reading:
            buffer = io.BufferedReader(raw, buffering)
        else:
            assert writing
            buffer = io.BufferedWriter(raw, buffering)
        if binary:
            return buffer
        text = io.TextIOWrapper(buffer, encoding, errors, newline)
        text.mode = mode
        return text

    def unwrap(self):
        self._ssl_io_loop(self.sslobj.unwrap)

    def close(self):
        self.socket.close()

    def getpeercert(self, binary_form=False):
        return self.sslobj.getpeercert(binary_form)

    def version(self):
        return self.sslobj.version()

    def cipher(self):
        return self.sslobj.cipher()

    def selected_alpn_protocol(self):
        return self.sslobj.selected_alpn_protocol()

    def selected_npn_protocol(self):
        return self.sslobj.selected_npn_protocol()

    def shared_ciphers(self):
        return self.sslobj.shared_ciphers()

    def compression(self):
        return self.sslobj.compression()

    def settimeout(self, value):
        self.socket.settimeout(value)

    def gettimeout(self):
        return self.socket.gettimeout()

    def _decref_socketios(self):
        self.socket._decref_socketios()

    def _wrap_ssl_read(self, len, buffer=None):
        try:
            return self._ssl_io_loop(self.sslobj.read, len, buffer)
        except ssl.SSLError as e:
            if e.errno == ssl.SSL_ERROR_EOF and self.suppress_ragged_eofs:
                return 0  # eof, return 0.
            else:
                raise

    def _ssl_io_loop(self, func, *args):
        \"\"\"Performs an I/O loop between incoming/outgoing and the socket.\"\"\"
        should_loop = True
        ret = None

        while should_loop:
            errno = None
            try:
                ret = func(*args)
            except ssl.SSLError as e:
                if e.errno not in (ssl.SSL_ERROR_WANT_READ, ssl.SSL_ERROR_WANT_WRITE):
                    # WANT_READ, and WANT_WRITE are expected, others are not.
                    raise e
                errno = e.errno

            buf = self.outgoing.read()
            self.socket.sendall(buf)

            if errno is None:
                should_loop = False
            elif errno == ssl.SSL_ERROR_WANT_READ:
                buf = self.socket.recv(SSL_BLOCKSIZE)
                if buf:
                    self.incoming.write(buf)
                else:
                    self.incoming.write_eof()
        return ret

"""
module_dict["urllib3"+os.sep+"contrib"+os.sep+"pyopenssl.py"]="""
\"\"\"
TLS with SNI_-support for Python 2. Follow these instructions if you would
like to verify TLS certificates in Python 2. Note, the default libraries do
*not* do certificate checking; you need to do additional work to validate
certificates yourself.

This needs the following packages installed:

* `pyOpenSSL`_ (tested with 16.0.0)
* `cryptography`_ (minimum 1.3.4, from pyopenssl)
* `idna`_ (minimum 2.0, from cryptography)

However, pyopenssl depends on cryptography, which depends on idna, so while we
use all three directly here we end up having relatively few packages required.

You can install them with the following command:

.. code-block:: bash

    $ python -m pip install pyopenssl cryptography idna

To activate certificate checking, call
:func:`~urllib3.contrib.pyopenssl.inject_into_urllib3` from your Python code
before you begin making HTTP requests. This can be done in a ``sitecustomize``
module, or at any other time before your application begins using ``urllib3``,
like this:

.. code-block:: python

    try:
        import urllib3.contrib.pyopenssl
        urllib3.contrib.pyopenssl.inject_into_urllib3()
    except ImportError:
        pass

Now you can use :mod:`urllib3` as you normally would, and it will support SNI
when the required modules are installed.

Activating this module also has the positive side effect of disabling SSL/TLS
compression in Python 2 (see `CRIME attack`_).

.. _sni: https://en.wikipedia.org/wiki/Server_Name_Indication
.. _crime attack: https://en.wikipedia.org/wiki/CRIME_(security_exploit)
.. _pyopenssl: https://www.pyopenssl.org
.. _cryptography: https://cryptography.io
.. _idna: https://github.com/kjd/idna
\"\"\"
from __future__ import absolute_import
# < include 'OpenSSL.py' >

# < include 'cryptography.py' >

# < include 'idna.py' >


import OpenSSL.SSL
from cryptography import x509
from cryptography.hazmat.backends.openssl import backend as openssl_backend
from cryptography.hazmat.backends.openssl.x509 import _Certificate

try:
    from cryptography.x509 import UnsupportedExtension
except ImportError:
    # UnsupportedExtension is gone in cryptography >= 2.1.0
    class UnsupportedExtension(Exception):
        pass


from io import BytesIO
from socket import error as SocketError
from socket import timeout

try:  # Platform-specific: Python 2
    from socket import _fileobject
except ImportError:  # Platform-specific: Python 3
    _fileobject = None
    from ..packages.backports.makefile import backport_makefile

import logging
import ssl
import sys

from .. import util
from ..packages import six
from ..util.ssl_ import PROTOCOL_TLS_CLIENT

__all__ = [\"inject_into_urllib3\", \"extract_from_urllib3\"]

# SNI always works.
HAS_SNI = True

# Map from urllib3 to PyOpenSSL compatible parameter-values.
_openssl_versions = {
    util.PROTOCOL_TLS: OpenSSL.SSL.SSLv23_METHOD,
    PROTOCOL_TLS_CLIENT: OpenSSL.SSL.SSLv23_METHOD,
    ssl.PROTOCOL_TLSv1: OpenSSL.SSL.TLSv1_METHOD,
}

if hasattr(ssl, \"PROTOCOL_SSLv3\") and hasattr(OpenSSL.SSL, \"SSLv3_METHOD\"):
    _openssl_versions[ssl.PROTOCOL_SSLv3] = OpenSSL.SSL.SSLv3_METHOD

if hasattr(ssl, \"PROTOCOL_TLSv1_1\") and hasattr(OpenSSL.SSL, \"TLSv1_1_METHOD\"):
    _openssl_versions[ssl.PROTOCOL_TLSv1_1] = OpenSSL.SSL.TLSv1_1_METHOD

if hasattr(ssl, \"PROTOCOL_TLSv1_2\") and hasattr(OpenSSL.SSL, \"TLSv1_2_METHOD\"):
    _openssl_versions[ssl.PROTOCOL_TLSv1_2] = OpenSSL.SSL.TLSv1_2_METHOD


_stdlib_to_openssl_verify = {
    ssl.CERT_NONE: OpenSSL.SSL.VERIFY_NONE,
    ssl.CERT_OPTIONAL: OpenSSL.SSL.VERIFY_PEER,
    ssl.CERT_REQUIRED: OpenSSL.SSL.VERIFY_PEER
    + OpenSSL.SSL.VERIFY_FAIL_IF_NO_PEER_CERT,
}
_openssl_to_stdlib_verify = dict((v, k) for k, v in _stdlib_to_openssl_verify.items())

# OpenSSL will only write 16K at a time
SSL_WRITE_BLOCKSIZE = 16384

orig_util_HAS_SNI = util.HAS_SNI
orig_util_SSLContext = util.ssl_.SSLContext


log = logging.getLogger(__name__)


def inject_into_urllib3():
    \"Monkey-patch urllib3 with PyOpenSSL-backed SSL-support.\"

    _validate_dependencies_met()

    util.SSLContext = PyOpenSSLContext
    util.ssl_.SSLContext = PyOpenSSLContext
    util.HAS_SNI = HAS_SNI
    util.ssl_.HAS_SNI = HAS_SNI
    util.IS_PYOPENSSL = True
    util.ssl_.IS_PYOPENSSL = True


def extract_from_urllib3():
    \"Undo monkey-patching by :func:`inject_into_urllib3`.\"

    util.SSLContext = orig_util_SSLContext
    util.ssl_.SSLContext = orig_util_SSLContext
    util.HAS_SNI = orig_util_HAS_SNI
    util.ssl_.HAS_SNI = orig_util_HAS_SNI
    util.IS_PYOPENSSL = False
    util.ssl_.IS_PYOPENSSL = False


def _validate_dependencies_met():
    \"\"\"
    Verifies that PyOpenSSL's package-level dependencies have been met.
    Throws `ImportError` if they are not met.
    \"\"\"
    # Method added in `cryptography==1.1`; not available in older versions
    from cryptography.x509.extensions import Extensions

    if getattr(Extensions, \"get_extension_for_class\", None) is None:
        raise ImportError(
            \"'cryptography' module missing required functionality.  \"
            \"Try upgrading to v1.3.4 or newer.\"
        )

    # pyOpenSSL 0.14 and above use cryptography for OpenSSL bindings. The _x509
    # attribute is only present on those versions.
    from OpenSSL.crypto import X509

    x509 = X509()
    if getattr(x509, \"_x509\", None) is None:
        raise ImportError(
            \"'pyOpenSSL' module missing required functionality. \"
            \"Try upgrading to v0.14 or newer.\"
        )


def _dnsname_to_stdlib(name):
    \"\"\"
    Converts a dNSName SubjectAlternativeName field to the form used by the
    standard library on the given Python version.

    Cryptography produces a dNSName as a unicode string that was idna-decoded
    from ASCII bytes. We need to idna-encode that string to get it back, and
    then on Python 3 we also need to convert to unicode via UTF-8 (the stdlib
    uses PyUnicode_FromStringAndSize on it, which decodes via UTF-8).

    If the name cannot be idna-encoded then we return None signalling that
    the name given should be skipped.
    \"\"\"

    def idna_encode(name):
        \"\"\"
        Borrowed wholesale from the Python Cryptography Project. It turns out
        that we can't just safely call `idna.encode`: it can explode for
        wildcard names. This avoids that problem.
        \"\"\"
        import idna

        try:
            for prefix in [u\"*.\", u\".\"]:
                if name.startswith(prefix):
                    name = name[len(prefix) :]
                    return prefix.encode(\"ascii\") + idna.encode(name)
            return idna.encode(name)
        except idna.core.IDNAError:
            return None

    # Don't send IPv6 addresses through the IDNA encoder.
    if \":\" in name:
        return name

    name = idna_encode(name)
    if name is None:
        return None
    elif sys.version_info >= (3, 0):
        name = name.decode(\"utf-8\")
    return name


def get_subj_alt_name(peer_cert):
    \"\"\"
    Given an PyOpenSSL certificate, provides all the subject alternative names.
    \"\"\"
    # Pass the cert to cryptography, which has much better APIs for this.
    if hasattr(peer_cert, \"to_cryptography\"):
        cert = peer_cert.to_cryptography()
    else:
        # This is technically using private APIs, but should work across all
        # relevant versions before PyOpenSSL got a proper API for this.
        cert = _Certificate(openssl_backend, peer_cert._x509)

    # We want to find the SAN extension. Ask Cryptography to locate it (it's
    # faster than looping in Python)
    try:
        ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound:
        # No such extension, return the empty list.
        return []
    except (
        x509.DuplicateExtension,
        UnsupportedExtension,
        x509.UnsupportedGeneralNameType,
        UnicodeError,
    ) as e:
        # A problem has been found with the quality of the certificate. Assume
        # no SAN field is present.
        log.warning(
            \"A problem was encountered with the certificate that prevented \"
            \"urllib3 from finding the SubjectAlternativeName field. This can \"
            \"affect certificate validation. The error was %s\",
            e,
        )
        return []

    # We want to return dNSName and iPAddress fields. We need to cast the IPs
    # back to strings because the match_hostname function wants them as
    # strings.
    # Sadly the DNS names need to be idna encoded and then, on Python 3, UTF-8
    # decoded. This is pretty frustrating, but that's what the standard library
    # does with certificates, and so we need to attempt to do the same.
    # We also want to skip over names which cannot be idna encoded.
    names = [
        (\"DNS\", name)
        for name in map(_dnsname_to_stdlib, ext.get_values_for_type(x509.DNSName))
        if name is not None
    ]
    names.extend(
        (\"IP Address\", str(name)) for name in ext.get_values_for_type(x509.IPAddress)
    )

    return names


class WrappedSocket(object):
    \"\"\"API-compatibility wrapper for Python OpenSSL's Connection-class.

    Note: _makefile_refs, _drop() and _reuse() are needed for the garbage
    collector of pypy.
    \"\"\"

    def __init__(self, connection, socket, suppress_ragged_eofs=True):
        self.connection = connection
        self.socket = socket
        self.suppress_ragged_eofs = suppress_ragged_eofs
        self._makefile_refs = 0
        self._closed = False

    def fileno(self):
        return self.socket.fileno()

    # Copy-pasted from Python 3.5 source code
    def _decref_socketios(self):
        if self._makefile_refs > 0:
            self._makefile_refs -= 1
        if self._closed:
            self.close()

    def recv(self, *args, **kwargs):
        try:
            data = self.connection.recv(*args, **kwargs)
        except OpenSSL.SSL.SysCallError as e:
            if self.suppress_ragged_eofs and e.args == (-1, \"Unexpected EOF\"):
                return b\"\"
            else:
                raise SocketError(str(e))
        except OpenSSL.SSL.ZeroReturnError:
            if self.connection.get_shutdown() == OpenSSL.SSL.RECEIVED_SHUTDOWN:
                return b\"\"
            else:
                raise
        except OpenSSL.SSL.WantReadError:
            if not util.wait_for_read(self.socket, self.socket.gettimeout()):
                raise timeout(\"The read operation timed out\")
            else:
                return self.recv(*args, **kwargs)

        # TLS 1.3 post-handshake authentication
        except OpenSSL.SSL.Error as e:
            raise ssl.SSLError(\"read error: %r\" % e)
        else:
            return data

    def recv_into(self, *args, **kwargs):
        try:
            return self.connection.recv_into(*args, **kwargs)
        except OpenSSL.SSL.SysCallError as e:
            if self.suppress_ragged_eofs and e.args == (-1, \"Unexpected EOF\"):
                return 0
            else:
                raise SocketError(str(e))
        except OpenSSL.SSL.ZeroReturnError:
            if self.connection.get_shutdown() == OpenSSL.SSL.RECEIVED_SHUTDOWN:
                return 0
            else:
                raise
        except OpenSSL.SSL.WantReadError:
            if not util.wait_for_read(self.socket, self.socket.gettimeout()):
                raise timeout(\"The read operation timed out\")
            else:
                return self.recv_into(*args, **kwargs)

        # TLS 1.3 post-handshake authentication
        except OpenSSL.SSL.Error as e:
            raise ssl.SSLError(\"read error: %r\" % e)

    def settimeout(self, timeout):
        return self.socket.settimeout(timeout)

    def _send_until_done(self, data):
        while True:
            try:
                return self.connection.send(data)
            except OpenSSL.SSL.WantWriteError:
                if not util.wait_for_write(self.socket, self.socket.gettimeout()):
                    raise timeout()
                continue
            except OpenSSL.SSL.SysCallError as e:
                raise SocketError(str(e))

    def sendall(self, data):
        total_sent = 0
        while total_sent < len(data):
            sent = self._send_until_done(
                data[total_sent : total_sent + SSL_WRITE_BLOCKSIZE]
            )
            total_sent += sent

    def shutdown(self):
        # FIXME rethrow compatible exceptions should we ever use this
        self.connection.shutdown()

    def close(self):
        if self._makefile_refs < 1:
            try:
                self._closed = True
                return self.connection.close()
            except OpenSSL.SSL.Error:
                return
        else:
            self._makefile_refs -= 1

    def getpeercert(self, binary_form=False):
        x509 = self.connection.get_peer_certificate()

        if not x509:
            return x509

        if binary_form:
            return OpenSSL.crypto.dump_certificate(OpenSSL.crypto.FILETYPE_ASN1, x509)

        return {
            \"subject\": (((\"commonName\", x509.get_subject().CN),),),
            \"subjectAltName\": get_subj_alt_name(x509),
        }

    def version(self):
        return self.connection.get_protocol_version_name()

    def _reuse(self):
        self._makefile_refs += 1

    def _drop(self):
        if self._makefile_refs < 1:
            self.close()
        else:
            self._makefile_refs -= 1


if _fileobject:  # Platform-specific: Python 2

    def makefile(self, mode, bufsize=-1):
        self._makefile_refs += 1
        return _fileobject(self, mode, bufsize, close=True)

else:  # Platform-specific: Python 3
    makefile = backport_makefile

WrappedSocket.makefile = makefile


class PyOpenSSLContext(object):
    \"\"\"
    I am a wrapper class for the PyOpenSSL ``Context`` object. I am responsible
    for translating the interface of the standard library ``SSLContext`` object
    to calls into PyOpenSSL.
    \"\"\"

    def __init__(self, protocol):
        self.protocol = _openssl_versions[protocol]
        self._ctx = OpenSSL.SSL.Context(self.protocol)
        self._options = 0
        self.check_hostname = False

    @property
    def options(self):
        return self._options

    @options.setter
    def options(self, value):
        self._options = value
        self._ctx.set_options(value)

    @property
    def verify_mode(self):
        return _openssl_to_stdlib_verify[self._ctx.get_verify_mode()]

    @verify_mode.setter
    def verify_mode(self, value):
        self._ctx.set_verify(_stdlib_to_openssl_verify[value], _verify_callback)

    def set_default_verify_paths(self):
        self._ctx.set_default_verify_paths()

    def set_ciphers(self, ciphers):
        if isinstance(ciphers, six.text_type):
            ciphers = ciphers.encode(\"utf-8\")
        self._ctx.set_cipher_list(ciphers)

    def load_verify_locations(self, cafile=None, capath=None, cadata=None):
        if cafile is not None:
            cafile = cafile.encode(\"utf-8\")
        if capath is not None:
            capath = capath.encode(\"utf-8\")
        try:
            self._ctx.load_verify_locations(cafile, capath)
            if cadata is not None:
                self._ctx.load_verify_locations(BytesIO(cadata))
        except OpenSSL.SSL.Error as e:
            raise ssl.SSLError(\"unable to load trusted certificates: %r\" % e)

    def load_cert_chain(self, certfile, keyfile=None, password=None):
        self._ctx.use_certificate_chain_file(certfile)
        if password is not None:
            if not isinstance(password, six.binary_type):
                password = password.encode(\"utf-8\")
            self._ctx.set_passwd_cb(lambda *_: password)
        self._ctx.use_privatekey_file(keyfile or certfile)

    def set_alpn_protocols(self, protocols):
        protocols = [six.ensure_binary(p) for p in protocols]
        return self._ctx.set_alpn_protos(protocols)

    def wrap_socket(
        self,
        sock,
        server_side=False,
        do_handshake_on_connect=True,
        suppress_ragged_eofs=True,
        server_hostname=None,
    ):
        cnx = OpenSSL.SSL.Connection(self._ctx, sock)

        if isinstance(server_hostname, six.text_type):  # Platform-specific: Python 3
            server_hostname = server_hostname.encode(\"utf-8\")

        if server_hostname is not None:
            cnx.set_tlsext_host_name(server_hostname)

        cnx.set_connect_state()

        while True:
            try:
                cnx.do_handshake()
            except OpenSSL.SSL.WantReadError:
                if not util.wait_for_read(sock, sock.gettimeout()):
                    raise timeout(\"select timed out\")
                continue
            except OpenSSL.SSL.Error as e:
                raise ssl.SSLError(\"bad handshake: %r\" % e)
            break

        return WrappedSocket(cnx, sock)


def _verify_callback(cnx, x509, err_no, err_depth, return_code):
    return err_no == 0

"""
module_dict["urllib3"+os.sep+"contrib"+os.sep+"ntlmpool.py"]="""
\"\"\"
NTLM authenticating pool, contributed by erikcederstran

Issue #10, see: http://code.google.com/p/urllib3/issues/detail?id=10
\"\"\"
from __future__ import absolute_import
# < include 'ntlm.py' >


import warnings
from logging import getLogger

from ntlm import ntlm

from .. import HTTPSConnectionPool
from ..packages.six.moves.http_client import HTTPSConnection

warnings.warn(
    \"The 'urllib3.contrib.ntlmpool' module is deprecated and will be removed \"
    \"in urllib3 v2.0 release, urllib3 is not able to support it properly due \"
    \"to reasons listed in issue: https://github.com/urllib3/urllib3/issues/2282. \"
    \"If you are a user of this module please comment in the mentioned issue.\",
    DeprecationWarning,
)

log = getLogger(__name__)


class NTLMConnectionPool(HTTPSConnectionPool):
    \"\"\"
    Implements an NTLM authentication version of an urllib3 connection pool
    \"\"\"

    scheme = \"https\"

    def __init__(self, user, pw, authurl, *args, **kwargs):
        \"\"\"
        authurl is a random URL on the server that is protected by NTLM.
        user is the Windows user, probably in the DOMAIN\\\\username format.
        pw is the password for the user.
        \"\"\"
        super(NTLMConnectionPool, self).__init__(*args, **kwargs)
        self.authurl = authurl
        self.rawuser = user
        user_parts = user.split(\"\\\\\", 1)
        self.domain = user_parts[0].upper()
        self.user = user_parts[1]
        self.pw = pw

    def _new_conn(self):
        # Performs the NTLM handshake that secures the connection. The socket
        # must be kept open while requests are performed.
        self.num_connections += 1
        log.debug(
            \"Starting NTLM HTTPS connection no. %d: https://%s%s\",
            self.num_connections,
            self.host,
            self.authurl,
        )

        headers = {\"Connection\": \"Keep-Alive\"}
        req_header = \"Authorization\"
        resp_header = \"www-authenticate\"

        conn = HTTPSConnection(host=self.host, port=self.port)

        # Send negotiation message
        headers[req_header] = \"NTLM %s\" % ntlm.create_NTLM_NEGOTIATE_MESSAGE(
            self.rawuser
        )
        log.debug(\"Request headers: %s\", headers)
        conn.request(\"GET\", self.authurl, None, headers)
        res = conn.getresponse()
        reshdr = dict(res.getheaders())
        log.debug(\"Response status: %s %s\", res.status, res.reason)
        log.debug(\"Response headers: %s\", reshdr)
        log.debug(\"Response data: %s [...]\", res.read(100))

        # Remove the reference to the socket, so that it can not be closed by
        # the response object (we want to keep the socket open)
        res.fp = None

        # Server should respond with a challenge message
        auth_header_values = reshdr[resp_header].split(\", \")
        auth_header_value = None
        for s in auth_header_values:
            if s[:5] == \"NTLM \":
                auth_header_value = s[5:]
        if auth_header_value is None:
            raise Exception(
                \"Unexpected %s response header: %s\" % (resp_header, reshdr[resp_header])
            )

        # Send authentication message
        ServerChallenge, NegotiateFlags = ntlm.parse_NTLM_CHALLENGE_MESSAGE(
            auth_header_value
        )
        auth_msg = ntlm.create_NTLM_AUTHENTICATE_MESSAGE(
            ServerChallenge, self.user, self.domain, self.pw, NegotiateFlags
        )
        headers[req_header] = \"NTLM %s\" % auth_msg
        log.debug(\"Request headers: %s\", headers)
        conn.request(\"GET\", self.authurl, None, headers)
        res = conn.getresponse()
        log.debug(\"Response status: %s %s\", res.status, res.reason)
        log.debug(\"Response headers: %s\", dict(res.getheaders()))
        log.debug(\"Response data: %s [...]\", res.read()[:100])
        if res.status != 200:
            if res.status == 401:
                raise Exception(\"Server rejected request: wrong username or password\")
            raise Exception(\"Wrong server response: %s %s\" % (res.status, res.reason))

        res.fp = None
        log.debug(\"Connection established\")
        return conn

    def urlopen(
        self,
        method,
        url,
        body=None,
        headers=None,
        retries=3,
        redirect=True,
        assert_same_host=True,
    ):
        if headers is None:
            headers = {}
        headers[\"Connection\"] = \"Keep-Alive\"
        return super(NTLMConnectionPool, self).urlopen(
            method, url, body, headers, retries, redirect, assert_same_host
        )

"""
module_dict["urllib3"+os.sep+"contrib"+os.sep+"securetransport.py"]="""
\"\"\"
SecureTranport support for urllib3 via ctypes.

This makes platform-native TLS available to urllib3 users on macOS without the
use of a compiler. This is an important feature because the Python Package
Index is moving to become a TLSv1.2-or-higher server, and the default OpenSSL
that ships with macOS is not capable of doing TLSv1.2. The only way to resolve
this is to give macOS users an alternative solution to the problem, and that
solution is to use SecureTransport.

We use ctypes here because this solution must not require a compiler. That's
because pip is not allowed to require a compiler either.

This is not intended to be a seriously long-term solution to this problem.
The hope is that PEP 543 will eventually solve this issue for us, at which
point we can retire this contrib module. But in the short term, we need to
solve the impending tire fire that is Python on Mac without this kind of
contrib module. So...here we are.

To use this module, simply import and inject it::

    import urllib3.contrib.securetransport
    urllib3.contrib.securetransport.inject_into_urllib3()

Happy TLSing!

This code is a bastardised version of the code found in Will Bond's oscrypto
library. An enormous debt is owed to him for blazing this trail for us. For
that reason, this code should be considered to be covered both by urllib3's
license and by oscrypto's:

.. code-block::

    Copyright (c) 2015-2016 Will Bond <will@wbond.net>

    Permission is hereby granted, free of charge, to any person obtaining a
    copy of this software and associated documentation files (the \"Software\"),
    to deal in the Software without restriction, including without limitation
    the rights to use, copy, modify, merge, publish, distribute, sublicense,
    and/or sell copies of the Software, and to permit persons to whom the
    Software is furnished to do so, subject to the following conditions:

    The above copyright notice and this permission notice shall be included in
    all copies or substantial portions of the Software.

    THE SOFTWARE IS PROVIDED \"AS IS\", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
    IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
    FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
    AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
    LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
    FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
    DEALINGS IN THE SOFTWARE.
\"\"\"
from __future__ import absolute_import
# < include 'six.py' >


import contextlib
import ctypes
import errno
import os.path
import shutil
import socket
import ssl
import struct
import threading
import weakref

import six

from .. import util
from ..util.ssl_ import PROTOCOL_TLS_CLIENT
from ._securetransport.bindings import CoreFoundation, Security, SecurityConst
from ._securetransport.low_level import (
    _assert_no_error,
    _build_tls_unknown_ca_alert,
    _cert_array_from_pem,
    _create_cfstring_array,
    _load_client_cert_chain,
    _temporary_keychain,
)

try:  # Platform-specific: Python 2
    from socket import _fileobject
except ImportError:  # Platform-specific: Python 3
    _fileobject = None
    from ..packages.backports.makefile import backport_makefile

__all__ = [\"inject_into_urllib3\", \"extract_from_urllib3\"]

# SNI always works
HAS_SNI = True

orig_util_HAS_SNI = util.HAS_SNI
orig_util_SSLContext = util.ssl_.SSLContext

# This dictionary is used by the read callback to obtain a handle to the
# calling wrapped socket. This is a pretty silly approach, but for now it'll
# do. I feel like I should be able to smuggle a handle to the wrapped socket
# directly in the SSLConnectionRef, but for now this approach will work I
# guess.
#
# We need to lock around this structure for inserts, but we don't do it for
# reads/writes in the callbacks. The reasoning here goes as follows:
#
#    1. It is not possible to call into the callbacks before the dictionary is
#       populated, so once in the callback the id must be in the dictionary.
#    2. The callbacks don't mutate the dictionary, they only read from it, and
#       so cannot conflict with any of the insertions.
#
# This is good: if we had to lock in the callbacks we'd drastically slow down
# the performance of this code.
_connection_refs = weakref.WeakValueDictionary()
_connection_ref_lock = threading.Lock()

# Limit writes to 16kB. This is OpenSSL's limit, but we'll cargo-cult it over
# for no better reason than we need *a* limit, and this one is right there.
SSL_WRITE_BLOCKSIZE = 16384

# This is our equivalent of util.ssl_.DEFAULT_CIPHERS, but expanded out to
# individual cipher suites. We need to do this because this is how
# SecureTransport wants them.
CIPHER_SUITES = [
    SecurityConst.TLS_ECDHE_ECDSA_WITH_AES_256_GCM_SHA384,
    SecurityConst.TLS_ECDHE_ECDSA_WITH_AES_128_GCM_SHA256,
    SecurityConst.TLS_ECDHE_RSA_WITH_AES_256_GCM_SHA384,
    SecurityConst.TLS_ECDHE_RSA_WITH_AES_128_GCM_SHA256,
    SecurityConst.TLS_ECDHE_ECDSA_WITH_CHACHA20_POLY1305_SHA256,
    SecurityConst.TLS_ECDHE_RSA_WITH_CHACHA20_POLY1305_SHA256,
    SecurityConst.TLS_DHE_RSA_WITH_AES_256_GCM_SHA384,
    SecurityConst.TLS_DHE_RSA_WITH_AES_128_GCM_SHA256,
    SecurityConst.TLS_ECDHE_ECDSA_WITH_AES_256_CBC_SHA384,
    SecurityConst.TLS_ECDHE_ECDSA_WITH_AES_256_CBC_SHA,
    SecurityConst.TLS_ECDHE_ECDSA_WITH_AES_128_CBC_SHA256,
    SecurityConst.TLS_ECDHE_ECDSA_WITH_AES_128_CBC_SHA,
    SecurityConst.TLS_ECDHE_RSA_WITH_AES_256_CBC_SHA384,
    SecurityConst.TLS_ECDHE_RSA_WITH_AES_256_CBC_SHA,
    SecurityConst.TLS_ECDHE_RSA_WITH_AES_128_CBC_SHA256,
    SecurityConst.TLS_ECDHE_RSA_WITH_AES_128_CBC_SHA,
    SecurityConst.TLS_DHE_RSA_WITH_AES_256_CBC_SHA256,
    SecurityConst.TLS_DHE_RSA_WITH_AES_256_CBC_SHA,
    SecurityConst.TLS_DHE_RSA_WITH_AES_128_CBC_SHA256,
    SecurityConst.TLS_DHE_RSA_WITH_AES_128_CBC_SHA,
    SecurityConst.TLS_AES_256_GCM_SHA384,
    SecurityConst.TLS_AES_128_GCM_SHA256,
    SecurityConst.TLS_RSA_WITH_AES_256_GCM_SHA384,
    SecurityConst.TLS_RSA_WITH_AES_128_GCM_SHA256,
    SecurityConst.TLS_AES_128_CCM_8_SHA256,
    SecurityConst.TLS_AES_128_CCM_SHA256,
    SecurityConst.TLS_RSA_WITH_AES_256_CBC_SHA256,
    SecurityConst.TLS_RSA_WITH_AES_128_CBC_SHA256,
    SecurityConst.TLS_RSA_WITH_AES_256_CBC_SHA,
    SecurityConst.TLS_RSA_WITH_AES_128_CBC_SHA,
]

# Basically this is simple: for PROTOCOL_SSLv23 we turn it into a low of
# TLSv1 and a high of TLSv1.2. For everything else, we pin to that version.
# TLSv1 to 1.2 are supported on macOS 10.8+
_protocol_to_min_max = {
    util.PROTOCOL_TLS: (SecurityConst.kTLSProtocol1, SecurityConst.kTLSProtocol12),
    PROTOCOL_TLS_CLIENT: (SecurityConst.kTLSProtocol1, SecurityConst.kTLSProtocol12),
}

if hasattr(ssl, \"PROTOCOL_SSLv2\"):
    _protocol_to_min_max[ssl.PROTOCOL_SSLv2] = (
        SecurityConst.kSSLProtocol2,
        SecurityConst.kSSLProtocol2,
    )
if hasattr(ssl, \"PROTOCOL_SSLv3\"):
    _protocol_to_min_max[ssl.PROTOCOL_SSLv3] = (
        SecurityConst.kSSLProtocol3,
        SecurityConst.kSSLProtocol3,
    )
if hasattr(ssl, \"PROTOCOL_TLSv1\"):
    _protocol_to_min_max[ssl.PROTOCOL_TLSv1] = (
        SecurityConst.kTLSProtocol1,
        SecurityConst.kTLSProtocol1,
    )
if hasattr(ssl, \"PROTOCOL_TLSv1_1\"):
    _protocol_to_min_max[ssl.PROTOCOL_TLSv1_1] = (
        SecurityConst.kTLSProtocol11,
        SecurityConst.kTLSProtocol11,
    )
if hasattr(ssl, \"PROTOCOL_TLSv1_2\"):
    _protocol_to_min_max[ssl.PROTOCOL_TLSv1_2] = (
        SecurityConst.kTLSProtocol12,
        SecurityConst.kTLSProtocol12,
    )


def inject_into_urllib3():
    \"\"\"
    Monkey-patch urllib3 with SecureTransport-backed SSL-support.
    \"\"\"
    util.SSLContext = SecureTransportContext
    util.ssl_.SSLContext = SecureTransportContext
    util.HAS_SNI = HAS_SNI
    util.ssl_.HAS_SNI = HAS_SNI
    util.IS_SECURETRANSPORT = True
    util.ssl_.IS_SECURETRANSPORT = True


def extract_from_urllib3():
    \"\"\"
    Undo monkey-patching by :func:`inject_into_urllib3`.
    \"\"\"
    util.SSLContext = orig_util_SSLContext
    util.ssl_.SSLContext = orig_util_SSLContext
    util.HAS_SNI = orig_util_HAS_SNI
    util.ssl_.HAS_SNI = orig_util_HAS_SNI
    util.IS_SECURETRANSPORT = False
    util.ssl_.IS_SECURETRANSPORT = False


def _read_callback(connection_id, data_buffer, data_length_pointer):
    \"\"\"
    SecureTransport read callback. This is called by ST to request that data
    be returned from the socket.
    \"\"\"
    wrapped_socket = None
    try:
        wrapped_socket = _connection_refs.get(connection_id)
        if wrapped_socket is None:
            return SecurityConst.errSSLInternal
        base_socket = wrapped_socket.socket

        requested_length = data_length_pointer[0]

        timeout = wrapped_socket.gettimeout()
        error = None
        read_count = 0

        try:
            while read_count < requested_length:
                if timeout is None or timeout >= 0:
                    if not util.wait_for_read(base_socket, timeout):
                        raise socket.error(errno.EAGAIN, \"timed out\")

                remaining = requested_length - read_count
                buffer = (ctypes.c_char * remaining).from_address(
                    data_buffer + read_count
                )
                chunk_size = base_socket.recv_into(buffer, remaining)
                read_count += chunk_size
                if not chunk_size:
                    if not read_count:
                        return SecurityConst.errSSLClosedGraceful
                    break
        except (socket.error) as e:
            error = e.errno

            if error is not None and error != errno.EAGAIN:
                data_length_pointer[0] = read_count
                if error == errno.ECONNRESET or error == errno.EPIPE:
                    return SecurityConst.errSSLClosedAbort
                raise

        data_length_pointer[0] = read_count

        if read_count != requested_length:
            return SecurityConst.errSSLWouldBlock

        return 0
    except Exception as e:
        if wrapped_socket is not None:
            wrapped_socket._exception = e
        return SecurityConst.errSSLInternal


def _write_callback(connection_id, data_buffer, data_length_pointer):
    \"\"\"
    SecureTransport write callback. This is called by ST to request that data
    actually be sent on the network.
    \"\"\"
    wrapped_socket = None
    try:
        wrapped_socket = _connection_refs.get(connection_id)
        if wrapped_socket is None:
            return SecurityConst.errSSLInternal
        base_socket = wrapped_socket.socket

        bytes_to_write = data_length_pointer[0]
        data = ctypes.string_at(data_buffer, bytes_to_write)

        timeout = wrapped_socket.gettimeout()
        error = None
        sent = 0

        try:
            while sent < bytes_to_write:
                if timeout is None or timeout >= 0:
                    if not util.wait_for_write(base_socket, timeout):
                        raise socket.error(errno.EAGAIN, \"timed out\")
                chunk_sent = base_socket.send(data)
                sent += chunk_sent

                # This has some needless copying here, but I'm not sure there's
                # much value in optimising this data path.
                data = data[chunk_sent:]
        except (socket.error) as e:
            error = e.errno

            if error is not None and error != errno.EAGAIN:
                data_length_pointer[0] = sent
                if error == errno.ECONNRESET or error == errno.EPIPE:
                    return SecurityConst.errSSLClosedAbort
                raise

        data_length_pointer[0] = sent

        if sent != bytes_to_write:
            return SecurityConst.errSSLWouldBlock

        return 0
    except Exception as e:
        if wrapped_socket is not None:
            wrapped_socket._exception = e
        return SecurityConst.errSSLInternal


# We need to keep these two objects references alive: if they get GC'd while
# in use then SecureTransport could attempt to call a function that is in freed
# memory. That would be...uh...bad. Yeah, that's the word. Bad.
_read_callback_pointer = Security.SSLReadFunc(_read_callback)
_write_callback_pointer = Security.SSLWriteFunc(_write_callback)


class WrappedSocket(object):
    \"\"\"
    API-compatibility wrapper for Python's OpenSSL wrapped socket object.

    Note: _makefile_refs, _drop(), and _reuse() are needed for the garbage
    collector of PyPy.
    \"\"\"

    def __init__(self, socket):
        self.socket = socket
        self.context = None
        self._makefile_refs = 0
        self._closed = False
        self._exception = None
        self._keychain = None
        self._keychain_dir = None
        self._client_cert_chain = None

        # We save off the previously-configured timeout and then set it to
        # zero. This is done because we use select and friends to handle the
        # timeouts, but if we leave the timeout set on the lower socket then
        # Python will \"kindly\" call select on that socket again for us. Avoid
        # that by forcing the timeout to zero.
        self._timeout = self.socket.gettimeout()
        self.socket.settimeout(0)

    @contextlib.contextmanager
    def _raise_on_error(self):
        \"\"\"
        A context manager that can be used to wrap calls that do I/O from
        SecureTransport. If any of the I/O callbacks hit an exception, this
        context manager will correctly propagate the exception after the fact.
        This avoids silently swallowing those exceptions.

        It also correctly forces the socket closed.
        \"\"\"
        self._exception = None

        # We explicitly don't catch around this yield because in the unlikely
        # event that an exception was hit in the block we don't want to swallow
        # it.
        yield
        if self._exception is not None:
            exception, self._exception = self._exception, None
            self.close()
            raise exception

    def _set_ciphers(self):
        \"\"\"
        Sets up the allowed ciphers. By default this matches the set in
        util.ssl_.DEFAULT_CIPHERS, at least as supported by macOS. This is done
        custom and doesn't allow changing at this time, mostly because parsing
        OpenSSL cipher strings is going to be a freaking nightmare.
        \"\"\"
        ciphers = (Security.SSLCipherSuite * len(CIPHER_SUITES))(*CIPHER_SUITES)
        result = Security.SSLSetEnabledCiphers(
            self.context, ciphers, len(CIPHER_SUITES)
        )
        _assert_no_error(result)

    def _set_alpn_protocols(self, protocols):
        \"\"\"
        Sets up the ALPN protocols on the context.
        \"\"\"
        if not protocols:
            return
        protocols_arr = _create_cfstring_array(protocols)
        try:
            result = Security.SSLSetALPNProtocols(self.context, protocols_arr)
            _assert_no_error(result)
        finally:
            CoreFoundation.CFRelease(protocols_arr)

    def _custom_validate(self, verify, trust_bundle):
        \"\"\"
        Called when we have set custom validation. We do this in two cases:
        first, when cert validation is entirely disabled; and second, when
        using a custom trust DB.
        Raises an SSLError if the connection is not trusted.
        \"\"\"
        # If we disabled cert validation, just say: cool.
        if not verify:
            return

        successes = (
            SecurityConst.kSecTrustResultUnspecified,
            SecurityConst.kSecTrustResultProceed,
        )
        try:
            trust_result = self._evaluate_trust(trust_bundle)
            if trust_result in successes:
                return
            reason = \"error code: %d\" % (trust_result,)
        except Exception as e:
            # Do not trust on error
            reason = \"exception: %r\" % (e,)

        # SecureTransport does not send an alert nor shuts down the connection.
        rec = _build_tls_unknown_ca_alert(self.version())
        self.socket.sendall(rec)
        # close the connection immediately
        # l_onoff = 1, activate linger
        # l_linger = 0, linger for 0 seoncds
        opts = struct.pack(\"ii\", 1, 0)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, opts)
        self.close()
        raise ssl.SSLError(\"certificate verify failed, %s\" % reason)

    def _evaluate_trust(self, trust_bundle):
        # We want data in memory, so load it up.
        if os.path.isfile(trust_bundle):
            with open(trust_bundle, \"rb\") as f:
                trust_bundle = f.read()

        cert_array = None
        trust = Security.SecTrustRef()

        try:
            # Get a CFArray that contains the certs we want.
            cert_array = _cert_array_from_pem(trust_bundle)

            # Ok, now the hard part. We want to get the SecTrustRef that ST has
            # created for this connection, shove our CAs into it, tell ST to
            # ignore everything else it knows, and then ask if it can build a
            # chain. This is a buuuunch of code.
            result = Security.SSLCopyPeerTrust(self.context, ctypes.byref(trust))
            _assert_no_error(result)
            if not trust:
                raise ssl.SSLError(\"Failed to copy trust reference\")

            result = Security.SecTrustSetAnchorCertificates(trust, cert_array)
            _assert_no_error(result)

            result = Security.SecTrustSetAnchorCertificatesOnly(trust, True)
            _assert_no_error(result)

            trust_result = Security.SecTrustResultType()
            result = Security.SecTrustEvaluate(trust, ctypes.byref(trust_result))
            _assert_no_error(result)
        finally:
            if trust:
                CoreFoundation.CFRelease(trust)

            if cert_array is not None:
                CoreFoundation.CFRelease(cert_array)

        return trust_result.value

    def handshake(
        self,
        server_hostname,
        verify,
        trust_bundle,
        min_version,
        max_version,
        client_cert,
        client_key,
        client_key_passphrase,
        alpn_protocols,
    ):
        \"\"\"
        Actually performs the TLS handshake. This is run automatically by
        wrapped socket, and shouldn't be needed in user code.
        \"\"\"
        # First, we do the initial bits of connection setup. We need to create
        # a context, set its I/O funcs, and set the connection reference.
        self.context = Security.SSLCreateContext(
            None, SecurityConst.kSSLClientSide, SecurityConst.kSSLStreamType
        )
        result = Security.SSLSetIOFuncs(
            self.context, _read_callback_pointer, _write_callback_pointer
        )
        _assert_no_error(result)

        # Here we need to compute the handle to use. We do this by taking the
        # id of self modulo 2**31 - 1. If this is already in the dictionary, we
        # just keep incrementing by one until we find a free space.
        with _connection_ref_lock:
            handle = id(self) % 2147483647
            while handle in _connection_refs:
                handle = (handle + 1) % 2147483647
            _connection_refs[handle] = self

        result = Security.SSLSetConnection(self.context, handle)
        _assert_no_error(result)

        # If we have a server hostname, we should set that too.
        if server_hostname:
            if not isinstance(server_hostname, bytes):
                server_hostname = server_hostname.encode(\"utf-8\")

            result = Security.SSLSetPeerDomainName(
                self.context, server_hostname, len(server_hostname)
            )
            _assert_no_error(result)

        # Setup the ciphers.
        self._set_ciphers()

        # Setup the ALPN protocols.
        self._set_alpn_protocols(alpn_protocols)

        # Set the minimum and maximum TLS versions.
        result = Security.SSLSetProtocolVersionMin(self.context, min_version)
        _assert_no_error(result)

        result = Security.SSLSetProtocolVersionMax(self.context, max_version)
        _assert_no_error(result)

        # If there's a trust DB, we need to use it. We do that by telling
        # SecureTransport to break on server auth. We also do that if we don't
        # want to validate the certs at all: we just won't actually do any
        # authing in that case.
        if not verify or trust_bundle is not None:
            result = Security.SSLSetSessionOption(
                self.context, SecurityConst.kSSLSessionOptionBreakOnServerAuth, True
            )
            _assert_no_error(result)

        # If there's a client cert, we need to use it.
        if client_cert:
            self._keychain, self._keychain_dir = _temporary_keychain()
            self._client_cert_chain = _load_client_cert_chain(
                self._keychain, client_cert, client_key
            )
            result = Security.SSLSetCertificate(self.context, self._client_cert_chain)
            _assert_no_error(result)

        while True:
            with self._raise_on_error():
                result = Security.SSLHandshake(self.context)

                if result == SecurityConst.errSSLWouldBlock:
                    raise socket.timeout(\"handshake timed out\")
                elif result == SecurityConst.errSSLServerAuthCompleted:
                    self._custom_validate(verify, trust_bundle)
                    continue
                else:
                    _assert_no_error(result)
                    break

    def fileno(self):
        return self.socket.fileno()

    # Copy-pasted from Python 3.5 source code
    def _decref_socketios(self):
        if self._makefile_refs > 0:
            self._makefile_refs -= 1
        if self._closed:
            self.close()

    def recv(self, bufsiz):
        buffer = ctypes.create_string_buffer(bufsiz)
        bytes_read = self.recv_into(buffer, bufsiz)
        data = buffer[:bytes_read]
        return data

    def recv_into(self, buffer, nbytes=None):
        # Read short on EOF.
        if self._closed:
            return 0

        if nbytes is None:
            nbytes = len(buffer)

        buffer = (ctypes.c_char * nbytes).from_buffer(buffer)
        processed_bytes = ctypes.c_size_t(0)

        with self._raise_on_error():
            result = Security.SSLRead(
                self.context, buffer, nbytes, ctypes.byref(processed_bytes)
            )

        # There are some result codes that we want to treat as \"not always
        # errors\". Specifically, those are errSSLWouldBlock,
        # errSSLClosedGraceful, and errSSLClosedNoNotify.
        if result == SecurityConst.errSSLWouldBlock:
            # If we didn't process any bytes, then this was just a time out.
            # However, we can get errSSLWouldBlock in situations when we *did*
            # read some data, and in those cases we should just read \"short\"
            # and return.
            if processed_bytes.value == 0:
                # Timed out, no data read.
                raise socket.timeout(\"recv timed out\")
        elif result in (
            SecurityConst.errSSLClosedGraceful,
            SecurityConst.errSSLClosedNoNotify,
        ):
            # The remote peer has closed this connection. We should do so as
            # well. Note that we don't actually return here because in
            # principle this could actually be fired along with return data.
            # It's unlikely though.
            self.close()
        else:
            _assert_no_error(result)

        # Ok, we read and probably succeeded. We should return whatever data
        # was actually read.
        return processed_bytes.value

    def settimeout(self, timeout):
        self._timeout = timeout

    def gettimeout(self):
        return self._timeout

    def send(self, data):
        processed_bytes = ctypes.c_size_t(0)

        with self._raise_on_error():
            result = Security.SSLWrite(
                self.context, data, len(data), ctypes.byref(processed_bytes)
            )

        if result == SecurityConst.errSSLWouldBlock and processed_bytes.value == 0:
            # Timed out
            raise socket.timeout(\"send timed out\")
        else:
            _assert_no_error(result)

        # We sent, and probably succeeded. Tell them how much we sent.
        return processed_bytes.value

    def sendall(self, data):
        total_sent = 0
        while total_sent < len(data):
            sent = self.send(data[total_sent : total_sent + SSL_WRITE_BLOCKSIZE])
            total_sent += sent

    def shutdown(self):
        with self._raise_on_error():
            Security.SSLClose(self.context)

    def close(self):
        # TODO: should I do clean shutdown here? Do I have to?
        if self._makefile_refs < 1:
            self._closed = True
            if self.context:
                CoreFoundation.CFRelease(self.context)
                self.context = None
            if self._client_cert_chain:
                CoreFoundation.CFRelease(self._client_cert_chain)
                self._client_cert_chain = None
            if self._keychain:
                Security.SecKeychainDelete(self._keychain)
                CoreFoundation.CFRelease(self._keychain)
                shutil.rmtree(self._keychain_dir)
                self._keychain = self._keychain_dir = None
            return self.socket.close()
        else:
            self._makefile_refs -= 1

    def getpeercert(self, binary_form=False):
        # Urgh, annoying.
        #
        # Here's how we do this:
        #
        # 1. Call SSLCopyPeerTrust to get hold of the trust object for this
        #    connection.
        # 2. Call SecTrustGetCertificateAtIndex for index 0 to get the leaf.
        # 3. To get the CN, call SecCertificateCopyCommonName and process that
        #    string so that it's of the appropriate type.
        # 4. To get the SAN, we need to do something a bit more complex:
        #    a. Call SecCertificateCopyValues to get the data, requesting
        #       kSecOIDSubjectAltName.
        #    b. Mess about with this dictionary to try to get the SANs out.
        #
        # This is gross. Really gross. It's going to be a few hundred LoC extra
        # just to repeat something that SecureTransport can *already do*. So my
        # operating assumption at this time is that what we want to do is
        # instead to just flag to urllib3 that it shouldn't do its own hostname
        # validation when using SecureTransport.
        if not binary_form:
            raise ValueError(\"SecureTransport only supports dumping binary certs\")
        trust = Security.SecTrustRef()
        certdata = None
        der_bytes = None

        try:
            # Grab the trust store.
            result = Security.SSLCopyPeerTrust(self.context, ctypes.byref(trust))
            _assert_no_error(result)
            if not trust:
                # Probably we haven't done the handshake yet. No biggie.
                return None

            cert_count = Security.SecTrustGetCertificateCount(trust)
            if not cert_count:
                # Also a case that might happen if we haven't handshaked.
                # Handshook? Handshaken?
                return None

            leaf = Security.SecTrustGetCertificateAtIndex(trust, 0)
            assert leaf

            # Ok, now we want the DER bytes.
            certdata = Security.SecCertificateCopyData(leaf)
            assert certdata

            data_length = CoreFoundation.CFDataGetLength(certdata)
            data_buffer = CoreFoundation.CFDataGetBytePtr(certdata)
            der_bytes = ctypes.string_at(data_buffer, data_length)
        finally:
            if certdata:
                CoreFoundation.CFRelease(certdata)
            if trust:
                CoreFoundation.CFRelease(trust)

        return der_bytes

    def version(self):
        protocol = Security.SSLProtocol()
        result = Security.SSLGetNegotiatedProtocolVersion(
            self.context, ctypes.byref(protocol)
        )
        _assert_no_error(result)
        if protocol.value == SecurityConst.kTLSProtocol13:
            raise ssl.SSLError(\"SecureTransport does not support TLS 1.3\")
        elif protocol.value == SecurityConst.kTLSProtocol12:
            return \"TLSv1.2\"
        elif protocol.value == SecurityConst.kTLSProtocol11:
            return \"TLSv1.1\"
        elif protocol.value == SecurityConst.kTLSProtocol1:
            return \"TLSv1\"
        elif protocol.value == SecurityConst.kSSLProtocol3:
            return \"SSLv3\"
        elif protocol.value == SecurityConst.kSSLProtocol2:
            return \"SSLv2\"
        else:
            raise ssl.SSLError(\"Unknown TLS version: %r\" % protocol)

    def _reuse(self):
        self._makefile_refs += 1

    def _drop(self):
        if self._makefile_refs < 1:
            self.close()
        else:
            self._makefile_refs -= 1


if _fileobject:  # Platform-specific: Python 2

    def makefile(self, mode, bufsize=-1):
        self._makefile_refs += 1
        return _fileobject(self, mode, bufsize, close=True)

else:  # Platform-specific: Python 3

    def makefile(self, mode=\"r\", buffering=None, *args, **kwargs):
        # We disable buffering with SecureTransport because it conflicts with
        # the buffering that ST does internally (see issue #1153 for more).
        buffering = 0
        return backport_makefile(self, mode, buffering, *args, **kwargs)


WrappedSocket.makefile = makefile


class SecureTransportContext(object):
    \"\"\"
    I am a wrapper class for the SecureTransport library, to translate the
    interface of the standard library ``SSLContext`` object to calls into
    SecureTransport.
    \"\"\"

    def __init__(self, protocol):
        self._min_version, self._max_version = _protocol_to_min_max[protocol]
        self._options = 0
        self._verify = False
        self._trust_bundle = None
        self._client_cert = None
        self._client_key = None
        self._client_key_passphrase = None
        self._alpn_protocols = None

    @property
    def check_hostname(self):
        \"\"\"
        SecureTransport cannot have its hostname checking disabled. For more,
        see the comment on getpeercert() in this file.
        \"\"\"
        return True

    @check_hostname.setter
    def check_hostname(self, value):
        \"\"\"
        SecureTransport cannot have its hostname checking disabled. For more,
        see the comment on getpeercert() in this file.
        \"\"\"
        pass

    @property
    def options(self):
        # TODO: Well, crap.
        #
        # So this is the bit of the code that is the most likely to cause us
        # trouble. Essentially we need to enumerate all of the SSL options that
        # users might want to use and try to see if we can sensibly translate
        # them, or whether we should just ignore them.
        return self._options

    @options.setter
    def options(self, value):
        # TODO: Update in line with above.
        self._options = value

    @property
    def verify_mode(self):
        return ssl.CERT_REQUIRED if self._verify else ssl.CERT_NONE

    @verify_mode.setter
    def verify_mode(self, value):
        self._verify = True if value == ssl.CERT_REQUIRED else False

    def set_default_verify_paths(self):
        # So, this has to do something a bit weird. Specifically, what it does
        # is nothing.
        #
        # This means that, if we had previously had load_verify_locations
        # called, this does not undo that. We need to do that because it turns
        # out that the rest of the urllib3 code will attempt to load the
        # default verify paths if it hasn't been told about any paths, even if
        # the context itself was sometime earlier. We resolve that by just
        # ignoring it.
        pass

    def load_default_certs(self):
        return self.set_default_verify_paths()

    def set_ciphers(self, ciphers):
        # For now, we just require the default cipher string.
        if ciphers != util.ssl_.DEFAULT_CIPHERS:
            raise ValueError(\"SecureTransport doesn't support custom cipher strings\")

    def load_verify_locations(self, cafile=None, capath=None, cadata=None):
        # OK, we only really support cadata and cafile.
        if capath is not None:
            raise ValueError(\"SecureTransport does not support cert directories\")

        # Raise if cafile does not exist.
        if cafile is not None:
            with open(cafile):
                pass

        self._trust_bundle = cafile or cadata

    def load_cert_chain(self, certfile, keyfile=None, password=None):
        self._client_cert = certfile
        self._client_key = keyfile
        self._client_cert_passphrase = password

    def set_alpn_protocols(self, protocols):
        \"\"\"
        Sets the ALPN protocols that will later be set on the context.

        Raises a NotImplementedError if ALPN is not supported.
        \"\"\"
        if not hasattr(Security, \"SSLSetALPNProtocols\"):
            raise NotImplementedError(
                \"SecureTransport supports ALPN only in macOS 10.12+\"
            )
        self._alpn_protocols = [six.ensure_binary(p) for p in protocols]

    def wrap_socket(
        self,
        sock,
        server_side=False,
        do_handshake_on_connect=True,
        suppress_ragged_eofs=True,
        server_hostname=None,
    ):
        # So, what do we do here? Firstly, we assert some properties. This is a
        # stripped down shim, so there is some functionality we don't support.
        # See PEP 543 for the real deal.
        assert not server_side
        assert do_handshake_on_connect
        assert suppress_ragged_eofs

        # Ok, we're good to go. Now we want to create the wrapped socket object
        # and store it in the appropriate place.
        wrapped_socket = WrappedSocket(sock)

        # Now we can handshake
        wrapped_socket.handshake(
            server_hostname,
            self._verify,
            self._trust_bundle,
            self._min_version,
            self._max_version,
            self._client_cert,
            self._client_key,
            self._client_key_passphrase,
            self._alpn_protocols,
        )
        return wrapped_socket

"""
module_dict["urllib3"+os.sep+"contrib"+os.sep+"socks.py"]="""
# -*- coding: utf-8 -*-
\"\"\"
This module contains provisional support for SOCKS proxies from within
urllib3. This module supports SOCKS4, SOCKS4A (an extension of SOCKS4), and
SOCKS5. To enable its functionality, either install PySocks or install this
module with the ``socks`` extra.

The SOCKS implementation supports the full range of urllib3 features. It also
supports the following SOCKS features:

- SOCKS4A (``proxy_url='socks4a://...``)
- SOCKS4 (``proxy_url='socks4://...``)
- SOCKS5 with remote DNS (``proxy_url='socks5h://...``)
- SOCKS5 with local DNS (``proxy_url='socks5://...``)
- Usernames and passwords for the SOCKS proxy

.. note::
   It is recommended to use ``socks5h://`` or ``socks4a://`` schemes in
   your ``proxy_url`` to ensure that DNS resolution is done from the remote
   server instead of client-side when connecting to a domain name.

SOCKS4 supports IPv4 and domain names with the SOCKS4A extension. SOCKS5
supports IPv4, IPv6, and domain names.

When connecting to a SOCKS4 proxy the ``username`` portion of the ``proxy_url``
will be sent as the ``userid`` section of the SOCKS request:

.. code-block:: python

    proxy_url=\"socks4a://<userid>@proxy-host\"

When connecting to a SOCKS5 proxy the ``username`` and ``password`` portion
of the ``proxy_url`` will be sent as the username/password to authenticate
with the proxy:

.. code-block:: python

    proxy_url=\"socks5h://<username>:<password>@proxy-host\"

\"\"\"
from __future__ import absolute_import
# < include 'socks.py' >


try:
    import socks
except ImportError:
    import warnings

    from ..exceptions import DependencyWarning

    warnings.warn(
        (
            \"SOCKS support in urllib3 requires the installation of optional \"
            \"dependencies: specifically, PySocks.  For more information, see \"
            \"https://urllib3.readthedocs.io/en/1.26.x/contrib.html#socks-proxies\"
        ),
        DependencyWarning,
    )
    raise

from socket import error as SocketError
from socket import timeout as SocketTimeout

from ..connection import HTTPConnection, HTTPSConnection
from ..connectionpool import HTTPConnectionPool, HTTPSConnectionPool
from ..exceptions import ConnectTimeoutError, NewConnectionError
from ..poolmanager import PoolManager
from ..util.url import parse_url

try:
    import ssl
except ImportError:
    ssl = None


class SOCKSConnection(HTTPConnection):
    \"\"\"
    A plain-text HTTP connection that connects via a SOCKS proxy.
    \"\"\"

    def __init__(self, *args, **kwargs):
        self._socks_options = kwargs.pop(\"_socks_options\")
        super(SOCKSConnection, self).__init__(*args, **kwargs)

    def _new_conn(self):
        \"\"\"
        Establish a new connection via the SOCKS proxy.
        \"\"\"
        extra_kw = {}
        if self.source_address:
            extra_kw[\"source_address\"] = self.source_address

        if self.socket_options:
            extra_kw[\"socket_options\"] = self.socket_options

        try:
            conn = socks.create_connection(
                (self.host, self.port),
                proxy_type=self._socks_options[\"socks_version\"],
                proxy_addr=self._socks_options[\"proxy_host\"],
                proxy_port=self._socks_options[\"proxy_port\"],
                proxy_username=self._socks_options[\"username\"],
                proxy_password=self._socks_options[\"password\"],
                proxy_rdns=self._socks_options[\"rdns\"],
                timeout=self.timeout,
                **extra_kw
            )

        except SocketTimeout:
            raise ConnectTimeoutError(
                self,
                \"Connection to %s timed out. (connect timeout=%s)\"
                % (self.host, self.timeout),
            )

        except socks.ProxyError as e:
            # This is fragile as hell, but it seems to be the only way to raise
            # useful errors here.
            if e.socket_err:
                error = e.socket_err
                if isinstance(error, SocketTimeout):
                    raise ConnectTimeoutError(
                        self,
                        \"Connection to %s timed out. (connect timeout=%s)\"
                        % (self.host, self.timeout),
                    )
                else:
                    raise NewConnectionError(
                        self, \"Failed to establish a new connection: %s\" % error
                    )
            else:
                raise NewConnectionError(
                    self, \"Failed to establish a new connection: %s\" % e
                )

        except SocketError as e:  # Defensive: PySocks should catch all these.
            raise NewConnectionError(
                self, \"Failed to establish a new connection: %s\" % e
            )

        return conn


# We don't need to duplicate the Verified/Unverified distinction from
# urllib3/connection.py here because the HTTPSConnection will already have been
# correctly set to either the Verified or Unverified form by that module. This
# means the SOCKSHTTPSConnection will automatically be the correct type.
class SOCKSHTTPSConnection(SOCKSConnection, HTTPSConnection):
    pass


class SOCKSHTTPConnectionPool(HTTPConnectionPool):
    ConnectionCls = SOCKSConnection


class SOCKSHTTPSConnectionPool(HTTPSConnectionPool):
    ConnectionCls = SOCKSHTTPSConnection


class SOCKSProxyManager(PoolManager):
    \"\"\"
    A version of the urllib3 ProxyManager that routes connections via the
    defined SOCKS proxy.
    \"\"\"

    pool_classes_by_scheme = {
        \"http\": SOCKSHTTPConnectionPool,
        \"https\": SOCKSHTTPSConnectionPool,
    }

    def __init__(
        self,
        proxy_url,
        username=None,
        password=None,
        num_pools=10,
        headers=None,
        **connection_pool_kw
    ):
        parsed = parse_url(proxy_url)

        if username is None and password is None and parsed.auth is not None:
            split = parsed.auth.split(\":\")
            if len(split) == 2:
                username, password = split
        if parsed.scheme == \"socks5\":
            socks_version = socks.PROXY_TYPE_SOCKS5
            rdns = False
        elif parsed.scheme == \"socks5h\":
            socks_version = socks.PROXY_TYPE_SOCKS5
            rdns = True
        elif parsed.scheme == \"socks4\":
            socks_version = socks.PROXY_TYPE_SOCKS4
            rdns = False
        elif parsed.scheme == \"socks4a\":
            socks_version = socks.PROXY_TYPE_SOCKS4
            rdns = True
        else:
            raise ValueError(\"Unable to determine SOCKS version from %s\" % proxy_url)

        self.proxy_url = proxy_url

        socks_options = {
            \"socks_version\": socks_version,
            \"proxy_host\": parsed.host,
            \"proxy_port\": parsed.port,
            \"username\": username,
            \"password\": password,
            \"rdns\": rdns,
        }
        connection_pool_kw[\"_socks_options\"] = socks_options

        super(SOCKSProxyManager, self).__init__(
            num_pools, headers, **connection_pool_kw
        )

        self.pool_classes_by_scheme = SOCKSProxyManager.pool_classes_by_scheme

"""
module_dict["urllib3"+os.sep+"contrib"+os.sep+"__init__.py"]="""

"""
module_dict["urllib3"+os.sep+"contrib"+os.sep+"appengine.py"]="""
\"\"\"
This module provides a pool manager that uses Google App Engine's
`URLFetch Service <https://cloud.google.com/appengine/docs/python/urlfetch>`_.

Example usage::

    from urllib3 import PoolManager
    from urllib3.contrib.appengine import AppEngineManager, is_appengine_sandbox

    if is_appengine_sandbox():
        # AppEngineManager uses AppEngine's URLFetch API behind the scenes
        http = AppEngineManager()
    else:
        # PoolManager uses a socket-level API behind the scenes
        http = PoolManager()

    r = http.request('GET', 'https://google.com/')

There are `limitations <https://cloud.google.com/appengine/docs/python/\\
urlfetch/#Python_Quotas_and_limits>`_ to the URLFetch service and it may not be
the best choice for your application. There are three options for using
urllib3 on Google App Engine:

1. You can use :class:`AppEngineManager` with URLFetch. URLFetch is
   cost-effective in many circumstances as long as your usage is within the
   limitations.
2. You can use a normal :class:`~urllib3.PoolManager` by enabling sockets.
   Sockets also have `limitations and restrictions
   <https://cloud.google.com/appengine/docs/python/sockets/\\
   #limitations-and-restrictions>`_ and have a lower free quota than URLFetch.
   To use sockets, be sure to specify the following in your ``app.yaml``::

        env_variables:
            GAE_USE_SOCKETS_HTTPLIB : 'true'

3. If you are using `App Engine Flexible
<https://cloud.google.com/appengine/docs/flexible/>`_, you can use the standard
:class:`PoolManager` without any configuration or special environment variables.
\"\"\"

from __future__ import absolute_import
# < include 'google.py' >


import io
import logging
import warnings

from ..exceptions import (
    HTTPError,
    HTTPWarning,
    MaxRetryError,
    ProtocolError,
    SSLError,
    TimeoutError,
)
from ..packages.six.moves.urllib.parse import urljoin
from ..request import RequestMethods
from ..response import HTTPResponse
from ..util.retry import Retry
from ..util.timeout import Timeout
from . import _appengine_environ

try:
    from google.appengine.api import urlfetch
except ImportError:
    urlfetch = None


log = logging.getLogger(__name__)


class AppEnginePlatformWarning(HTTPWarning):
    pass


class AppEnginePlatformError(HTTPError):
    pass


class AppEngineManager(RequestMethods):
    \"\"\"
    Connection manager for Google App Engine sandbox applications.

    This manager uses the URLFetch service directly instead of using the
    emulated httplib, and is subject to URLFetch limitations as described in
    the App Engine documentation `here
    <https://cloud.google.com/appengine/docs/python/urlfetch>`_.

    Notably it will raise an :class:`AppEnginePlatformError` if:
        * URLFetch is not available.
        * If you attempt to use this on App Engine Flexible, as full socket
          support is available.
        * If a request size is more than 10 megabytes.
        * If a response size is more than 32 megabytes.
        * If you use an unsupported request method such as OPTIONS.

    Beyond those cases, it will raise normal urllib3 errors.
    \"\"\"

    def __init__(
        self,
        headers=None,
        retries=None,
        validate_certificate=True,
        urlfetch_retries=True,
    ):
        if not urlfetch:
            raise AppEnginePlatformError(
                \"URLFetch is not available in this environment.\"
            )

        warnings.warn(
            \"urllib3 is using URLFetch on Google App Engine sandbox instead \"
            \"of sockets. To use sockets directly instead of URLFetch see \"
            \"https://urllib3.readthedocs.io/en/1.26.x/reference/urllib3.contrib.html.\",
            AppEnginePlatformWarning,
        )

        RequestMethods.__init__(self, headers)
        self.validate_certificate = validate_certificate
        self.urlfetch_retries = urlfetch_retries

        self.retries = retries or Retry.DEFAULT

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        # Return False to re-raise any potential exceptions
        return False

    def urlopen(
        self,
        method,
        url,
        body=None,
        headers=None,
        retries=None,
        redirect=True,
        timeout=Timeout.DEFAULT_TIMEOUT,
        **response_kw
    ):

        retries = self._get_retries(retries, redirect)

        try:
            follow_redirects = redirect and retries.redirect != 0 and retries.total
            response = urlfetch.fetch(
                url,
                payload=body,
                method=method,
                headers=headers or {},
                allow_truncated=False,
                follow_redirects=self.urlfetch_retries and follow_redirects,
                deadline=self._get_absolute_timeout(timeout),
                validate_certificate=self.validate_certificate,
            )
        except urlfetch.DeadlineExceededError as e:
            raise TimeoutError(self, e)

        except urlfetch.InvalidURLError as e:
            if \"too large\" in str(e):
                raise AppEnginePlatformError(
                    \"URLFetch request too large, URLFetch only \"
                    \"supports requests up to 10mb in size.\",
                    e,
                )
            raise ProtocolError(e)

        except urlfetch.DownloadError as e:
            if \"Too many redirects\" in str(e):
                raise MaxRetryError(self, url, reason=e)
            raise ProtocolError(e)

        except urlfetch.ResponseTooLargeError as e:
            raise AppEnginePlatformError(
                \"URLFetch response too large, URLFetch only supports\"
                \"responses up to 32mb in size.\",
                e,
            )

        except urlfetch.SSLCertificateError as e:
            raise SSLError(e)

        except urlfetch.InvalidMethodError as e:
            raise AppEnginePlatformError(
                \"URLFetch does not support method: %s\" % method, e
            )

        http_response = self._urlfetch_response_to_http_response(
            response, retries=retries, **response_kw
        )

        # Handle redirect?
        redirect_location = redirect and http_response.get_redirect_location()
        if redirect_location:
            # Check for redirect response
            if self.urlfetch_retries and retries.raise_on_redirect:
                raise MaxRetryError(self, url, \"too many redirects\")
            else:
                if http_response.status == 303:
                    method = \"GET\"

                try:
                    retries = retries.increment(
                        method, url, response=http_response, _pool=self
                    )
                except MaxRetryError:
                    if retries.raise_on_redirect:
                        raise MaxRetryError(self, url, \"too many redirects\")
                    return http_response

                retries.sleep_for_retry(http_response)
                log.debug(\"Redirecting %s -> %s\", url, redirect_location)
                redirect_url = urljoin(url, redirect_location)
                return self.urlopen(
                    method,
                    redirect_url,
                    body,
                    headers,
                    retries=retries,
                    redirect=redirect,
                    timeout=timeout,
                    **response_kw
                )

        # Check if we should retry the HTTP response.
        has_retry_after = bool(http_response.getheader(\"Retry-After\"))
        if retries.is_retry(method, http_response.status, has_retry_after):
            retries = retries.increment(method, url, response=http_response, _pool=self)
            log.debug(\"Retry: %s\", url)
            retries.sleep(http_response)
            return self.urlopen(
                method,
                url,
                body=body,
                headers=headers,
                retries=retries,
                redirect=redirect,
                timeout=timeout,
                **response_kw
            )

        return http_response

    def _urlfetch_response_to_http_response(self, urlfetch_resp, **response_kw):

        if is_prod_appengine():
            # Production GAE handles deflate encoding automatically, but does
            # not remove the encoding header.
            content_encoding = urlfetch_resp.headers.get(\"content-encoding\")

            if content_encoding == \"deflate\":
                del urlfetch_resp.headers[\"content-encoding\"]

        transfer_encoding = urlfetch_resp.headers.get(\"transfer-encoding\")
        # We have a full response's content,
        # so let's make sure we don't report ourselves as chunked data.
        if transfer_encoding == \"chunked\":
            encodings = transfer_encoding.split(\",\")
            encodings.remove(\"chunked\")
            urlfetch_resp.headers[\"transfer-encoding\"] = \",\".join(encodings)

        original_response = HTTPResponse(
            # In order for decoding to work, we must present the content as
            # a file-like object.
            body=io.BytesIO(urlfetch_resp.content),
            msg=urlfetch_resp.header_msg,
            headers=urlfetch_resp.headers,
            status=urlfetch_resp.status_code,
            **response_kw
        )

        return HTTPResponse(
            body=io.BytesIO(urlfetch_resp.content),
            headers=urlfetch_resp.headers,
            status=urlfetch_resp.status_code,
            original_response=original_response,
            **response_kw
        )

    def _get_absolute_timeout(self, timeout):
        if timeout is Timeout.DEFAULT_TIMEOUT:
            return None  # Defer to URLFetch's default.
        if isinstance(timeout, Timeout):
            if timeout._read is not None or timeout._connect is not None:
                warnings.warn(
                    \"URLFetch does not support granular timeout settings, \"
                    \"reverting to total or default URLFetch timeout.\",
                    AppEnginePlatformWarning,
                )
            return timeout.total
        return timeout

    def _get_retries(self, retries, redirect):
        if not isinstance(retries, Retry):
            retries = Retry.from_int(retries, redirect=redirect, default=self.retries)

        if retries.connect or retries.read or retries.redirect:
            warnings.warn(
                \"URLFetch only supports total retries and does not \"
                \"recognize connect, read, or redirect retry parameters.\",
                AppEnginePlatformWarning,
            )

        return retries


# Alias methods from _appengine_environ to maintain public API interface.

is_appengine = _appengine_environ.is_appengine
is_appengine_sandbox = _appengine_environ.is_appengine_sandbox
is_local_appengine = _appengine_environ.is_local_appengine
is_prod_appengine = _appengine_environ.is_prod_appengine
is_prod_appengine_mvms = _appengine_environ.is_prod_appengine_mvms

"""
module_dict["urllib3"+os.sep+"contrib"+os.sep+"_appengine_environ.py"]="""
\"\"\"
This module provides means to detect the App Engine environment.
\"\"\"

import os


def is_appengine():
    return is_local_appengine() or is_prod_appengine()


def is_appengine_sandbox():
    \"\"\"Reports if the app is running in the first generation sandbox.

    The second generation runtimes are technically still in a sandbox, but it
    is much less restrictive, so generally you shouldn't need to check for it.
    see https://cloud.google.com/appengine/docs/standard/runtimes
    \"\"\"
    return is_appengine() and os.environ[\"APPENGINE_RUNTIME\"] == \"python27\"


def is_local_appengine():
    return \"APPENGINE_RUNTIME\" in os.environ and os.environ.get(
        \"SERVER_SOFTWARE\", \"\"
    ).startswith(\"Development/\")


def is_prod_appengine():
    return \"APPENGINE_RUNTIME\" in os.environ and os.environ.get(
        \"SERVER_SOFTWARE\", \"\"
    ).startswith(\"Google App Engine/\")


def is_prod_appengine_mvms():
    \"\"\"Deprecated.\"\"\"
    return False

"""
module_dict["urllib3"+os.sep+"contrib"+os.sep+"_securetransport"+os.sep+"bindings.py"]="""
\"\"\"
This module uses ctypes to bind a whole bunch of functions and constants from
SecureTransport. The goal here is to provide the low-level API to
SecureTransport. These are essentially the C-level functions and constants, and
they're pretty gross to work with.

This code is a bastardised version of the code found in Will Bond's oscrypto
library. An enormous debt is owed to him for blazing this trail for us. For
that reason, this code should be considered to be covered both by urllib3's
license and by oscrypto's:

    Copyright (c) 2015-2016 Will Bond <will@wbond.net>

    Permission is hereby granted, free of charge, to any person obtaining a
    copy of this software and associated documentation files (the \"Software\"),
    to deal in the Software without restriction, including without limitation
    the rights to use, copy, modify, merge, publish, distribute, sublicense,
    and/or sell copies of the Software, and to permit persons to whom the
    Software is furnished to do so, subject to the following conditions:

    The above copyright notice and this permission notice shall be included in
    all copies or substantial portions of the Software.

    THE SOFTWARE IS PROVIDED \"AS IS\", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
    IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
    FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
    AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
    LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
    FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
    DEALINGS IN THE SOFTWARE.
\"\"\"
from __future__ import absolute_import

import platform
from ctypes import (
    CDLL,
    CFUNCTYPE,
    POINTER,
    c_bool,
    c_byte,
    c_char_p,
    c_int32,
    c_long,
    c_size_t,
    c_uint32,
    c_ulong,
    c_void_p,
)
from ctypes.util import find_library

from ...packages.six import raise_from

if platform.system() != \"Darwin\":
    raise ImportError(\"Only macOS is supported\")

version = platform.mac_ver()[0]
version_info = tuple(map(int, version.split(\".\")))
if version_info < (10, 8):
    raise OSError(
        \"Only OS X 10.8 and newer are supported, not %s.%s\"
        % (version_info[0], version_info[1])
    )


def load_cdll(name, macos10_16_path):
    \"\"\"Loads a CDLL by name, falling back to known path on 10.16+\"\"\"
    try:
        # Big Sur is technically 11 but we use 10.16 due to the Big Sur
        # beta being labeled as 10.16.
        if version_info >= (10, 16):
            path = macos10_16_path
        else:
            path = find_library(name)
        if not path:
            raise OSError  # Caught and reraised as 'ImportError'
        return CDLL(path, use_errno=True)
    except OSError:
        raise_from(ImportError(\"The library %s failed to load\" % name), None)


Security = load_cdll(
    \"Security\", \"/System/Library/Frameworks/Security.framework/Security\"
)
CoreFoundation = load_cdll(
    \"CoreFoundation\",
    \"/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation\",
)


Boolean = c_bool
CFIndex = c_long
CFStringEncoding = c_uint32
CFData = c_void_p
CFString = c_void_p
CFArray = c_void_p
CFMutableArray = c_void_p
CFDictionary = c_void_p
CFError = c_void_p
CFType = c_void_p
CFTypeID = c_ulong

CFTypeRef = POINTER(CFType)
CFAllocatorRef = c_void_p

OSStatus = c_int32

CFDataRef = POINTER(CFData)
CFStringRef = POINTER(CFString)
CFArrayRef = POINTER(CFArray)
CFMutableArrayRef = POINTER(CFMutableArray)
CFDictionaryRef = POINTER(CFDictionary)
CFArrayCallBacks = c_void_p
CFDictionaryKeyCallBacks = c_void_p
CFDictionaryValueCallBacks = c_void_p

SecCertificateRef = POINTER(c_void_p)
SecExternalFormat = c_uint32
SecExternalItemType = c_uint32
SecIdentityRef = POINTER(c_void_p)
SecItemImportExportFlags = c_uint32
SecItemImportExportKeyParameters = c_void_p
SecKeychainRef = POINTER(c_void_p)
SSLProtocol = c_uint32
SSLCipherSuite = c_uint32
SSLContextRef = POINTER(c_void_p)
SecTrustRef = POINTER(c_void_p)
SSLConnectionRef = c_uint32
SecTrustResultType = c_uint32
SecTrustOptionFlags = c_uint32
SSLProtocolSide = c_uint32
SSLConnectionType = c_uint32
SSLSessionOption = c_uint32


try:
    Security.SecItemImport.argtypes = [
        CFDataRef,
        CFStringRef,
        POINTER(SecExternalFormat),
        POINTER(SecExternalItemType),
        SecItemImportExportFlags,
        POINTER(SecItemImportExportKeyParameters),
        SecKeychainRef,
        POINTER(CFArrayRef),
    ]
    Security.SecItemImport.restype = OSStatus

    Security.SecCertificateGetTypeID.argtypes = []
    Security.SecCertificateGetTypeID.restype = CFTypeID

    Security.SecIdentityGetTypeID.argtypes = []
    Security.SecIdentityGetTypeID.restype = CFTypeID

    Security.SecKeyGetTypeID.argtypes = []
    Security.SecKeyGetTypeID.restype = CFTypeID

    Security.SecCertificateCreateWithData.argtypes = [CFAllocatorRef, CFDataRef]
    Security.SecCertificateCreateWithData.restype = SecCertificateRef

    Security.SecCertificateCopyData.argtypes = [SecCertificateRef]
    Security.SecCertificateCopyData.restype = CFDataRef

    Security.SecCopyErrorMessageString.argtypes = [OSStatus, c_void_p]
    Security.SecCopyErrorMessageString.restype = CFStringRef

    Security.SecIdentityCreateWithCertificate.argtypes = [
        CFTypeRef,
        SecCertificateRef,
        POINTER(SecIdentityRef),
    ]
    Security.SecIdentityCreateWithCertificate.restype = OSStatus

    Security.SecKeychainCreate.argtypes = [
        c_char_p,
        c_uint32,
        c_void_p,
        Boolean,
        c_void_p,
        POINTER(SecKeychainRef),
    ]
    Security.SecKeychainCreate.restype = OSStatus

    Security.SecKeychainDelete.argtypes = [SecKeychainRef]
    Security.SecKeychainDelete.restype = OSStatus

    Security.SecPKCS12Import.argtypes = [
        CFDataRef,
        CFDictionaryRef,
        POINTER(CFArrayRef),
    ]
    Security.SecPKCS12Import.restype = OSStatus

    SSLReadFunc = CFUNCTYPE(OSStatus, SSLConnectionRef, c_void_p, POINTER(c_size_t))
    SSLWriteFunc = CFUNCTYPE(
        OSStatus, SSLConnectionRef, POINTER(c_byte), POINTER(c_size_t)
    )

    Security.SSLSetIOFuncs.argtypes = [SSLContextRef, SSLReadFunc, SSLWriteFunc]
    Security.SSLSetIOFuncs.restype = OSStatus

    Security.SSLSetPeerID.argtypes = [SSLContextRef, c_char_p, c_size_t]
    Security.SSLSetPeerID.restype = OSStatus

    Security.SSLSetCertificate.argtypes = [SSLContextRef, CFArrayRef]
    Security.SSLSetCertificate.restype = OSStatus

    Security.SSLSetCertificateAuthorities.argtypes = [SSLContextRef, CFTypeRef, Boolean]
    Security.SSLSetCertificateAuthorities.restype = OSStatus

    Security.SSLSetConnection.argtypes = [SSLContextRef, SSLConnectionRef]
    Security.SSLSetConnection.restype = OSStatus

    Security.SSLSetPeerDomainName.argtypes = [SSLContextRef, c_char_p, c_size_t]
    Security.SSLSetPeerDomainName.restype = OSStatus

    Security.SSLHandshake.argtypes = [SSLContextRef]
    Security.SSLHandshake.restype = OSStatus

    Security.SSLRead.argtypes = [SSLContextRef, c_char_p, c_size_t, POINTER(c_size_t)]
    Security.SSLRead.restype = OSStatus

    Security.SSLWrite.argtypes = [SSLContextRef, c_char_p, c_size_t, POINTER(c_size_t)]
    Security.SSLWrite.restype = OSStatus

    Security.SSLClose.argtypes = [SSLContextRef]
    Security.SSLClose.restype = OSStatus

    Security.SSLGetNumberSupportedCiphers.argtypes = [SSLContextRef, POINTER(c_size_t)]
    Security.SSLGetNumberSupportedCiphers.restype = OSStatus

    Security.SSLGetSupportedCiphers.argtypes = [
        SSLContextRef,
        POINTER(SSLCipherSuite),
        POINTER(c_size_t),
    ]
    Security.SSLGetSupportedCiphers.restype = OSStatus

    Security.SSLSetEnabledCiphers.argtypes = [
        SSLContextRef,
        POINTER(SSLCipherSuite),
        c_size_t,
    ]
    Security.SSLSetEnabledCiphers.restype = OSStatus

    Security.SSLGetNumberEnabledCiphers.argtype = [SSLContextRef, POINTER(c_size_t)]
    Security.SSLGetNumberEnabledCiphers.restype = OSStatus

    Security.SSLGetEnabledCiphers.argtypes = [
        SSLContextRef,
        POINTER(SSLCipherSuite),
        POINTER(c_size_t),
    ]
    Security.SSLGetEnabledCiphers.restype = OSStatus

    Security.SSLGetNegotiatedCipher.argtypes = [SSLContextRef, POINTER(SSLCipherSuite)]
    Security.SSLGetNegotiatedCipher.restype = OSStatus

    Security.SSLGetNegotiatedProtocolVersion.argtypes = [
        SSLContextRef,
        POINTER(SSLProtocol),
    ]
    Security.SSLGetNegotiatedProtocolVersion.restype = OSStatus

    Security.SSLCopyPeerTrust.argtypes = [SSLContextRef, POINTER(SecTrustRef)]
    Security.SSLCopyPeerTrust.restype = OSStatus

    Security.SecTrustSetAnchorCertificates.argtypes = [SecTrustRef, CFArrayRef]
    Security.SecTrustSetAnchorCertificates.restype = OSStatus

    Security.SecTrustSetAnchorCertificatesOnly.argstypes = [SecTrustRef, Boolean]
    Security.SecTrustSetAnchorCertificatesOnly.restype = OSStatus

    Security.SecTrustEvaluate.argtypes = [SecTrustRef, POINTER(SecTrustResultType)]
    Security.SecTrustEvaluate.restype = OSStatus

    Security.SecTrustGetCertificateCount.argtypes = [SecTrustRef]
    Security.SecTrustGetCertificateCount.restype = CFIndex

    Security.SecTrustGetCertificateAtIndex.argtypes = [SecTrustRef, CFIndex]
    Security.SecTrustGetCertificateAtIndex.restype = SecCertificateRef

    Security.SSLCreateContext.argtypes = [
        CFAllocatorRef,
        SSLProtocolSide,
        SSLConnectionType,
    ]
    Security.SSLCreateContext.restype = SSLContextRef

    Security.SSLSetSessionOption.argtypes = [SSLContextRef, SSLSessionOption, Boolean]
    Security.SSLSetSessionOption.restype = OSStatus

    Security.SSLSetProtocolVersionMin.argtypes = [SSLContextRef, SSLProtocol]
    Security.SSLSetProtocolVersionMin.restype = OSStatus

    Security.SSLSetProtocolVersionMax.argtypes = [SSLContextRef, SSLProtocol]
    Security.SSLSetProtocolVersionMax.restype = OSStatus

    try:
        Security.SSLSetALPNProtocols.argtypes = [SSLContextRef, CFArrayRef]
        Security.SSLSetALPNProtocols.restype = OSStatus
    except AttributeError:
        # Supported only in 10.12+
        pass

    Security.SecCopyErrorMessageString.argtypes = [OSStatus, c_void_p]
    Security.SecCopyErrorMessageString.restype = CFStringRef

    Security.SSLReadFunc = SSLReadFunc
    Security.SSLWriteFunc = SSLWriteFunc
    Security.SSLContextRef = SSLContextRef
    Security.SSLProtocol = SSLProtocol
    Security.SSLCipherSuite = SSLCipherSuite
    Security.SecIdentityRef = SecIdentityRef
    Security.SecKeychainRef = SecKeychainRef
    Security.SecTrustRef = SecTrustRef
    Security.SecTrustResultType = SecTrustResultType
    Security.SecExternalFormat = SecExternalFormat
    Security.OSStatus = OSStatus

    Security.kSecImportExportPassphrase = CFStringRef.in_dll(
        Security, \"kSecImportExportPassphrase\"
    )
    Security.kSecImportItemIdentity = CFStringRef.in_dll(
        Security, \"kSecImportItemIdentity\"
    )

    # CoreFoundation time!
    CoreFoundation.CFRetain.argtypes = [CFTypeRef]
    CoreFoundation.CFRetain.restype = CFTypeRef

    CoreFoundation.CFRelease.argtypes = [CFTypeRef]
    CoreFoundation.CFRelease.restype = None

    CoreFoundation.CFGetTypeID.argtypes = [CFTypeRef]
    CoreFoundation.CFGetTypeID.restype = CFTypeID

    CoreFoundation.CFStringCreateWithCString.argtypes = [
        CFAllocatorRef,
        c_char_p,
        CFStringEncoding,
    ]
    CoreFoundation.CFStringCreateWithCString.restype = CFStringRef

    CoreFoundation.CFStringGetCStringPtr.argtypes = [CFStringRef, CFStringEncoding]
    CoreFoundation.CFStringGetCStringPtr.restype = c_char_p

    CoreFoundation.CFStringGetCString.argtypes = [
        CFStringRef,
        c_char_p,
        CFIndex,
        CFStringEncoding,
    ]
    CoreFoundation.CFStringGetCString.restype = c_bool

    CoreFoundation.CFDataCreate.argtypes = [CFAllocatorRef, c_char_p, CFIndex]
    CoreFoundation.CFDataCreate.restype = CFDataRef

    CoreFoundation.CFDataGetLength.argtypes = [CFDataRef]
    CoreFoundation.CFDataGetLength.restype = CFIndex

    CoreFoundation.CFDataGetBytePtr.argtypes = [CFDataRef]
    CoreFoundation.CFDataGetBytePtr.restype = c_void_p

    CoreFoundation.CFDictionaryCreate.argtypes = [
        CFAllocatorRef,
        POINTER(CFTypeRef),
        POINTER(CFTypeRef),
        CFIndex,
        CFDictionaryKeyCallBacks,
        CFDictionaryValueCallBacks,
    ]
    CoreFoundation.CFDictionaryCreate.restype = CFDictionaryRef

    CoreFoundation.CFDictionaryGetValue.argtypes = [CFDictionaryRef, CFTypeRef]
    CoreFoundation.CFDictionaryGetValue.restype = CFTypeRef

    CoreFoundation.CFArrayCreate.argtypes = [
        CFAllocatorRef,
        POINTER(CFTypeRef),
        CFIndex,
        CFArrayCallBacks,
    ]
    CoreFoundation.CFArrayCreate.restype = CFArrayRef

    CoreFoundation.CFArrayCreateMutable.argtypes = [
        CFAllocatorRef,
        CFIndex,
        CFArrayCallBacks,
    ]
    CoreFoundation.CFArrayCreateMutable.restype = CFMutableArrayRef

    CoreFoundation.CFArrayAppendValue.argtypes = [CFMutableArrayRef, c_void_p]
    CoreFoundation.CFArrayAppendValue.restype = None

    CoreFoundation.CFArrayGetCount.argtypes = [CFArrayRef]
    CoreFoundation.CFArrayGetCount.restype = CFIndex

    CoreFoundation.CFArrayGetValueAtIndex.argtypes = [CFArrayRef, CFIndex]
    CoreFoundation.CFArrayGetValueAtIndex.restype = c_void_p

    CoreFoundation.kCFAllocatorDefault = CFAllocatorRef.in_dll(
        CoreFoundation, \"kCFAllocatorDefault\"
    )
    CoreFoundation.kCFTypeArrayCallBacks = c_void_p.in_dll(
        CoreFoundation, \"kCFTypeArrayCallBacks\"
    )
    CoreFoundation.kCFTypeDictionaryKeyCallBacks = c_void_p.in_dll(
        CoreFoundation, \"kCFTypeDictionaryKeyCallBacks\"
    )
    CoreFoundation.kCFTypeDictionaryValueCallBacks = c_void_p.in_dll(
        CoreFoundation, \"kCFTypeDictionaryValueCallBacks\"
    )

    CoreFoundation.CFTypeRef = CFTypeRef
    CoreFoundation.CFArrayRef = CFArrayRef
    CoreFoundation.CFStringRef = CFStringRef
    CoreFoundation.CFDictionaryRef = CFDictionaryRef

except (AttributeError):
    raise ImportError(\"Error initializing ctypes\")


class CFConst(object):
    \"\"\"
    A class object that acts as essentially a namespace for CoreFoundation
    constants.
    \"\"\"

    kCFStringEncodingUTF8 = CFStringEncoding(0x08000100)


class SecurityConst(object):
    \"\"\"
    A class object that acts as essentially a namespace for Security constants.
    \"\"\"

    kSSLSessionOptionBreakOnServerAuth = 0

    kSSLProtocol2 = 1
    kSSLProtocol3 = 2
    kTLSProtocol1 = 4
    kTLSProtocol11 = 7
    kTLSProtocol12 = 8
    # SecureTransport does not support TLS 1.3 even if there's a constant for it
    kTLSProtocol13 = 10
    kTLSProtocolMaxSupported = 999

    kSSLClientSide = 1
    kSSLStreamType = 0

    kSecFormatPEMSequence = 10

    kSecTrustResultInvalid = 0
    kSecTrustResultProceed = 1
    # This gap is present on purpose: this was kSecTrustResultConfirm, which
    # is deprecated.
    kSecTrustResultDeny = 3
    kSecTrustResultUnspecified = 4
    kSecTrustResultRecoverableTrustFailure = 5
    kSecTrustResultFatalTrustFailure = 6
    kSecTrustResultOtherError = 7

    errSSLProtocol = -9800
    errSSLWouldBlock = -9803
    errSSLClosedGraceful = -9805
    errSSLClosedNoNotify = -9816
    errSSLClosedAbort = -9806

    errSSLXCertChainInvalid = -9807
    errSSLCrypto = -9809
    errSSLInternal = -9810
    errSSLCertExpired = -9814
    errSSLCertNotYetValid = -9815
    errSSLUnknownRootCert = -9812
    errSSLNoRootCert = -9813
    errSSLHostNameMismatch = -9843
    errSSLPeerHandshakeFail = -9824
    errSSLPeerUserCancelled = -9839
    errSSLWeakPeerEphemeralDHKey = -9850
    errSSLServerAuthCompleted = -9841
    errSSLRecordOverflow = -9847

    errSecVerifyFailed = -67808
    errSecNoTrustSettings = -25263
    errSecItemNotFound = -25300
    errSecInvalidTrustSettings = -25262

    # Cipher suites. We only pick the ones our default cipher string allows.
    # Source: https://developer.apple.com/documentation/security/1550981-ssl_cipher_suite_values
    TLS_ECDHE_ECDSA_WITH_AES_256_GCM_SHA384 = 0xC02C
    TLS_ECDHE_RSA_WITH_AES_256_GCM_SHA384 = 0xC030
    TLS_ECDHE_ECDSA_WITH_AES_128_GCM_SHA256 = 0xC02B
    TLS_ECDHE_RSA_WITH_AES_128_GCM_SHA256 = 0xC02F
    TLS_ECDHE_ECDSA_WITH_CHACHA20_POLY1305_SHA256 = 0xCCA9
    TLS_ECDHE_RSA_WITH_CHACHA20_POLY1305_SHA256 = 0xCCA8
    TLS_DHE_RSA_WITH_AES_256_GCM_SHA384 = 0x009F
    TLS_DHE_RSA_WITH_AES_128_GCM_SHA256 = 0x009E
    TLS_ECDHE_ECDSA_WITH_AES_256_CBC_SHA384 = 0xC024
    TLS_ECDHE_RSA_WITH_AES_256_CBC_SHA384 = 0xC028
    TLS_ECDHE_ECDSA_WITH_AES_256_CBC_SHA = 0xC00A
    TLS_ECDHE_RSA_WITH_AES_256_CBC_SHA = 0xC014
    TLS_DHE_RSA_WITH_AES_256_CBC_SHA256 = 0x006B
    TLS_DHE_RSA_WITH_AES_256_CBC_SHA = 0x0039
    TLS_ECDHE_ECDSA_WITH_AES_128_CBC_SHA256 = 0xC023
    TLS_ECDHE_RSA_WITH_AES_128_CBC_SHA256 = 0xC027
    TLS_ECDHE_ECDSA_WITH_AES_128_CBC_SHA = 0xC009
    TLS_ECDHE_RSA_WITH_AES_128_CBC_SHA = 0xC013
    TLS_DHE_RSA_WITH_AES_128_CBC_SHA256 = 0x0067
    TLS_DHE_RSA_WITH_AES_128_CBC_SHA = 0x0033
    TLS_RSA_WITH_AES_256_GCM_SHA384 = 0x009D
    TLS_RSA_WITH_AES_128_GCM_SHA256 = 0x009C
    TLS_RSA_WITH_AES_256_CBC_SHA256 = 0x003D
    TLS_RSA_WITH_AES_128_CBC_SHA256 = 0x003C
    TLS_RSA_WITH_AES_256_CBC_SHA = 0x0035
    TLS_RSA_WITH_AES_128_CBC_SHA = 0x002F
    TLS_AES_128_GCM_SHA256 = 0x1301
    TLS_AES_256_GCM_SHA384 = 0x1302
    TLS_AES_128_CCM_8_SHA256 = 0x1305
    TLS_AES_128_CCM_SHA256 = 0x1304

"""
module_dict["urllib3"+os.sep+"contrib"+os.sep+"_securetransport"+os.sep+"__init__.py"]="""

"""
module_dict["urllib3"+os.sep+"contrib"+os.sep+"_securetransport"+os.sep+"low_level.py"]="""
\"\"\"
Low-level helpers for the SecureTransport bindings.

These are Python functions that are not directly related to the high-level APIs
but are necessary to get them to work. They include a whole bunch of low-level
CoreFoundation messing about and memory management. The concerns in this module
are almost entirely about trying to avoid memory leaks and providing
appropriate and useful assistance to the higher-level code.
\"\"\"
import base64
import ctypes
import itertools
import os
import re
import ssl
import struct
import tempfile

from .bindings import CFConst, CoreFoundation, Security

# This regular expression is used to grab PEM data out of a PEM bundle.
_PEM_CERTS_RE = re.compile(
    b\"-----BEGIN CERTIFICATE-----\\n(.*?)\\n-----END CERTIFICATE-----\", re.DOTALL
)


def _cf_data_from_bytes(bytestring):
    \"\"\"
    Given a bytestring, create a CFData object from it. This CFData object must
    be CFReleased by the caller.
    \"\"\"
    return CoreFoundation.CFDataCreate(
        CoreFoundation.kCFAllocatorDefault, bytestring, len(bytestring)
    )


def _cf_dictionary_from_tuples(tuples):
    \"\"\"
    Given a list of Python tuples, create an associated CFDictionary.
    \"\"\"
    dictionary_size = len(tuples)

    # We need to get the dictionary keys and values out in the same order.
    keys = (t[0] for t in tuples)
    values = (t[1] for t in tuples)
    cf_keys = (CoreFoundation.CFTypeRef * dictionary_size)(*keys)
    cf_values = (CoreFoundation.CFTypeRef * dictionary_size)(*values)

    return CoreFoundation.CFDictionaryCreate(
        CoreFoundation.kCFAllocatorDefault,
        cf_keys,
        cf_values,
        dictionary_size,
        CoreFoundation.kCFTypeDictionaryKeyCallBacks,
        CoreFoundation.kCFTypeDictionaryValueCallBacks,
    )


def _cfstr(py_bstr):
    \"\"\"
    Given a Python binary data, create a CFString.
    The string must be CFReleased by the caller.
    \"\"\"
    c_str = ctypes.c_char_p(py_bstr)
    cf_str = CoreFoundation.CFStringCreateWithCString(
        CoreFoundation.kCFAllocatorDefault,
        c_str,
        CFConst.kCFStringEncodingUTF8,
    )
    return cf_str


def _create_cfstring_array(lst):
    \"\"\"
    Given a list of Python binary data, create an associated CFMutableArray.
    The array must be CFReleased by the caller.

    Raises an ssl.SSLError on failure.
    \"\"\"
    cf_arr = None
    try:
        cf_arr = CoreFoundation.CFArrayCreateMutable(
            CoreFoundation.kCFAllocatorDefault,
            0,
            ctypes.byref(CoreFoundation.kCFTypeArrayCallBacks),
        )
        if not cf_arr:
            raise MemoryError(\"Unable to allocate memory!\")
        for item in lst:
            cf_str = _cfstr(item)
            if not cf_str:
                raise MemoryError(\"Unable to allocate memory!\")
            try:
                CoreFoundation.CFArrayAppendValue(cf_arr, cf_str)
            finally:
                CoreFoundation.CFRelease(cf_str)
    except BaseException as e:
        if cf_arr:
            CoreFoundation.CFRelease(cf_arr)
        raise ssl.SSLError(\"Unable to allocate array: %s\" % (e,))
    return cf_arr


def _cf_string_to_unicode(value):
    \"\"\"
    Creates a Unicode string from a CFString object. Used entirely for error
    reporting.

    Yes, it annoys me quite a lot that this function is this complex.
    \"\"\"
    value_as_void_p = ctypes.cast(value, ctypes.POINTER(ctypes.c_void_p))

    string = CoreFoundation.CFStringGetCStringPtr(
        value_as_void_p, CFConst.kCFStringEncodingUTF8
    )
    if string is None:
        buffer = ctypes.create_string_buffer(1024)
        result = CoreFoundation.CFStringGetCString(
            value_as_void_p, buffer, 1024, CFConst.kCFStringEncodingUTF8
        )
        if not result:
            raise OSError(\"Error copying C string from CFStringRef\")
        string = buffer.value
    if string is not None:
        string = string.decode(\"utf-8\")
    return string


def _assert_no_error(error, exception_class=None):
    \"\"\"
    Checks the return code and throws an exception if there is an error to
    report
    \"\"\"
    if error == 0:
        return

    cf_error_string = Security.SecCopyErrorMessageString(error, None)
    output = _cf_string_to_unicode(cf_error_string)
    CoreFoundation.CFRelease(cf_error_string)

    if output is None or output == u\"\":
        output = u\"OSStatus %s\" % error

    if exception_class is None:
        exception_class = ssl.SSLError

    raise exception_class(output)


def _cert_array_from_pem(pem_bundle):
    \"\"\"
    Given a bundle of certs in PEM format, turns them into a CFArray of certs
    that can be used to validate a cert chain.
    \"\"\"
    # Normalize the PEM bundle's line endings.
    pem_bundle = pem_bundle.replace(b\"\\r\\n\", b\"\\n\")

    der_certs = [
        base64.b64decode(match.group(1)) for match in _PEM_CERTS_RE.finditer(pem_bundle)
    ]
    if not der_certs:
        raise ssl.SSLError(\"No root certificates specified\")

    cert_array = CoreFoundation.CFArrayCreateMutable(
        CoreFoundation.kCFAllocatorDefault,
        0,
        ctypes.byref(CoreFoundation.kCFTypeArrayCallBacks),
    )
    if not cert_array:
        raise ssl.SSLError(\"Unable to allocate memory!\")

    try:
        for der_bytes in der_certs:
            certdata = _cf_data_from_bytes(der_bytes)
            if not certdata:
                raise ssl.SSLError(\"Unable to allocate memory!\")
            cert = Security.SecCertificateCreateWithData(
                CoreFoundation.kCFAllocatorDefault, certdata
            )
            CoreFoundation.CFRelease(certdata)
            if not cert:
                raise ssl.SSLError(\"Unable to build cert object!\")

            CoreFoundation.CFArrayAppendValue(cert_array, cert)
            CoreFoundation.CFRelease(cert)
    except Exception:
        # We need to free the array before the exception bubbles further.
        # We only want to do that if an error occurs: otherwise, the caller
        # should free.
        CoreFoundation.CFRelease(cert_array)
        raise

    return cert_array


def _is_cert(item):
    \"\"\"
    Returns True if a given CFTypeRef is a certificate.
    \"\"\"
    expected = Security.SecCertificateGetTypeID()
    return CoreFoundation.CFGetTypeID(item) == expected


def _is_identity(item):
    \"\"\"
    Returns True if a given CFTypeRef is an identity.
    \"\"\"
    expected = Security.SecIdentityGetTypeID()
    return CoreFoundation.CFGetTypeID(item) == expected


def _temporary_keychain():
    \"\"\"
    This function creates a temporary Mac keychain that we can use to work with
    credentials. This keychain uses a one-time password and a temporary file to
    store the data. We expect to have one keychain per socket. The returned
    SecKeychainRef must be freed by the caller, including calling
    SecKeychainDelete.

    Returns a tuple of the SecKeychainRef and the path to the temporary
    directory that contains it.
    \"\"\"
    # Unfortunately, SecKeychainCreate requires a path to a keychain. This
    # means we cannot use mkstemp to use a generic temporary file. Instead,
    # we're going to create a temporary directory and a filename to use there.
    # This filename will be 8 random bytes expanded into base64. We also need
    # some random bytes to password-protect the keychain we're creating, so we
    # ask for 40 random bytes.
    random_bytes = os.urandom(40)
    filename = base64.b16encode(random_bytes[:8]).decode(\"utf-8\")
    password = base64.b16encode(random_bytes[8:])  # Must be valid UTF-8
    tempdirectory = tempfile.mkdtemp()

    keychain_path = os.path.join(tempdirectory, filename).encode(\"utf-8\")

    # We now want to create the keychain itself.
    keychain = Security.SecKeychainRef()
    status = Security.SecKeychainCreate(
        keychain_path, len(password), password, False, None, ctypes.byref(keychain)
    )
    _assert_no_error(status)

    # Having created the keychain, we want to pass it off to the caller.
    return keychain, tempdirectory


def _load_items_from_file(keychain, path):
    \"\"\"
    Given a single file, loads all the trust objects from it into arrays and
    the keychain.
    Returns a tuple of lists: the first list is a list of identities, the
    second a list of certs.
    \"\"\"
    certificates = []
    identities = []
    result_array = None

    with open(path, \"rb\") as f:
        raw_filedata = f.read()

    try:
        filedata = CoreFoundation.CFDataCreate(
            CoreFoundation.kCFAllocatorDefault, raw_filedata, len(raw_filedata)
        )
        result_array = CoreFoundation.CFArrayRef()
        result = Security.SecItemImport(
            filedata,  # cert data
            None,  # Filename, leaving it out for now
            None,  # What the type of the file is, we don't care
            None,  # what's in the file, we don't care
            0,  # import flags
            None,  # key params, can include passphrase in the future
            keychain,  # The keychain to insert into
            ctypes.byref(result_array),  # Results
        )
        _assert_no_error(result)

        # A CFArray is not very useful to us as an intermediary
        # representation, so we are going to extract the objects we want
        # and then free the array. We don't need to keep hold of keys: the
        # keychain already has them!
        result_count = CoreFoundation.CFArrayGetCount(result_array)
        for index in range(result_count):
            item = CoreFoundation.CFArrayGetValueAtIndex(result_array, index)
            item = ctypes.cast(item, CoreFoundation.CFTypeRef)

            if _is_cert(item):
                CoreFoundation.CFRetain(item)
                certificates.append(item)
            elif _is_identity(item):
                CoreFoundation.CFRetain(item)
                identities.append(item)
    finally:
        if result_array:
            CoreFoundation.CFRelease(result_array)

        CoreFoundation.CFRelease(filedata)

    return (identities, certificates)


def _load_client_cert_chain(keychain, *paths):
    \"\"\"
    Load certificates and maybe keys from a number of files. Has the end goal
    of returning a CFArray containing one SecIdentityRef, and then zero or more
    SecCertificateRef objects, suitable for use as a client certificate trust
    chain.
    \"\"\"
    # Ok, the strategy.
    #
    # This relies on knowing that macOS will not give you a SecIdentityRef
    # unless you have imported a key into a keychain. This is a somewhat
    # artificial limitation of macOS (for example, it doesn't necessarily
    # affect iOS), but there is nothing inside Security.framework that lets you
    # get a SecIdentityRef without having a key in a keychain.
    #
    # So the policy here is we take all the files and iterate them in order.
    # Each one will use SecItemImport to have one or more objects loaded from
    # it. We will also point at a keychain that macOS can use to work with the
    # private key.
    #
    # Once we have all the objects, we'll check what we actually have. If we
    # already have a SecIdentityRef in hand, fab: we'll use that. Otherwise,
    # we'll take the first certificate (which we assume to be our leaf) and
    # ask the keychain to give us a SecIdentityRef with that cert's associated
    # key.
    #
    # We'll then return a CFArray containing the trust chain: one
    # SecIdentityRef and then zero-or-more SecCertificateRef objects. The
    # responsibility for freeing this CFArray will be with the caller. This
    # CFArray must remain alive for the entire connection, so in practice it
    # will be stored with a single SSLSocket, along with the reference to the
    # keychain.
    certificates = []
    identities = []

    # Filter out bad paths.
    paths = (path for path in paths if path)

    try:
        for file_path in paths:
            new_identities, new_certs = _load_items_from_file(keychain, file_path)
            identities.extend(new_identities)
            certificates.extend(new_certs)

        # Ok, we have everything. The question is: do we have an identity? If
        # not, we want to grab one from the first cert we have.
        if not identities:
            new_identity = Security.SecIdentityRef()
            status = Security.SecIdentityCreateWithCertificate(
                keychain, certificates[0], ctypes.byref(new_identity)
            )
            _assert_no_error(status)
            identities.append(new_identity)

            # We now want to release the original certificate, as we no longer
            # need it.
            CoreFoundation.CFRelease(certificates.pop(0))

        # We now need to build a new CFArray that holds the trust chain.
        trust_chain = CoreFoundation.CFArrayCreateMutable(
            CoreFoundation.kCFAllocatorDefault,
            0,
            ctypes.byref(CoreFoundation.kCFTypeArrayCallBacks),
        )
        for item in itertools.chain(identities, certificates):
            # ArrayAppendValue does a CFRetain on the item. That's fine,
            # because the finally block will release our other refs to them.
            CoreFoundation.CFArrayAppendValue(trust_chain, item)

        return trust_chain
    finally:
        for obj in itertools.chain(identities, certificates):
            CoreFoundation.CFRelease(obj)


TLS_PROTOCOL_VERSIONS = {
    \"SSLv2\": (0, 2),
    \"SSLv3\": (3, 0),
    \"TLSv1\": (3, 1),
    \"TLSv1.1\": (3, 2),
    \"TLSv1.2\": (3, 3),
}


def _build_tls_unknown_ca_alert(version):
    \"\"\"
    Builds a TLS alert record for an unknown CA.
    \"\"\"
    ver_maj, ver_min = TLS_PROTOCOL_VERSIONS[version]
    severity_fatal = 0x02
    description_unknown_ca = 0x30
    msg = struct.pack(\">BB\", severity_fatal, description_unknown_ca)
    msg_len = len(msg)
    record_type_alert = 0x15
    record = struct.pack(\">BBBH\", record_type_alert, ver_maj, ver_min, msg_len) + msg
    return record

"""

import os
import types
import zipfile
import sys
import io
import json

class ZipImporter(object):
    def __init__(self, zip_file):
        self.zfile = zip_file
        self._paths = [x.filename for x in self.zfile.filelist]
        
    def _mod_to_paths(self, fullname):
        # get the python module name
        py_filename = fullname.replace(".", os.sep) + ".py"
        # get the filename if it is a package/subpackage
        py_package = fullname.replace(".", os.sep) + os.sep + "__init__.py"
        if py_filename in self._paths:
            return py_filename
        elif py_package in self._paths:
            return py_package
        else:
            return None

    def find_module(self, fullname, path):
        if self._mod_to_paths(fullname) is not None:
            return self
        return None

    def load_module(self, fullname):
        filename = self._mod_to_paths(fullname)
        if not filename in self._paths:
            raise ImportError(fullname)
        new_module = types.ModuleType(fullname)
        sys.modules[fullname]=new_module
        if filename.endswith("__init__.py"):
            new_module.__path__ = [] 
            new_module.__package__ = fullname
        else:
            new_module.__package__ = fullname.rpartition('.')[0]
        exec(self.zfile.open(filename, 'r').read(),new_module.__dict__)
        new_module.__file__ = filename
        new_module.__loader__ = self
        new_module.__spec__=json.__spec__ # To satisfy importlib._common.get_package
        return new_module

module_zip=zipfile.ZipFile(io.BytesIO(),"w")
for key in module_dict:
    module_zip.writestr(key,module_dict[key])

module_importer=ZipImporter(module_zip)
sys.meta_path.insert(0,module_importer)

#from urllib3 import *
import urllib3
globals().update(urllib3.__dict__)
    
if module_importer in sys.meta_path:
    sys.meta_path.remove(module_importer)

#for key in sys.modules.copy():
#    if key=="urllib3" or key.startswith("urllib3."):
#        del sys.modules[key]
