import os
module_dict={}
module_dict["requests"+os.sep+"auth.py"]="""
\"\"\"
requests.auth
~~~~~~~~~~~~~

This module contains the authentication handlers for Requests.
\"\"\"

import hashlib
import os
import re
import threading
import time
import warnings
from base64 import b64encode

from ._internal_utils import to_native_string
from .compat import basestring, str, urlparse
from .cookies import extract_cookies_to_jar
from .utils import parse_dict_header

CONTENT_TYPE_FORM_URLENCODED = \"application/x-www-form-urlencoded\"
CONTENT_TYPE_MULTI_PART = \"multipart/form-data\"


def _basic_auth_str(username, password):
    \"\"\"Returns a Basic Auth string.\"\"\"

    # \"I want us to put a big-ol' comment on top of it that
    # says that this behaviour is dumb but we need to preserve
    # it because people are relying on it.\"
    #    - Lukasa
    #
    # These are here solely to maintain backwards compatibility
    # for things like ints. This will be removed in 3.0.0.
    if not isinstance(username, basestring):
        warnings.warn(
            \"Non-string usernames will no longer be supported in Requests \"
            \"3.0.0. Please convert the object you've passed in ({!r}) to \"
            \"a string or bytes object in the near future to avoid \"
            \"problems.\".format(username),
            category=DeprecationWarning,
        )
        username = str(username)

    if not isinstance(password, basestring):
        warnings.warn(
            \"Non-string passwords will no longer be supported in Requests \"
            \"3.0.0. Please convert the object you've passed in ({!r}) to \"
            \"a string or bytes object in the near future to avoid \"
            \"problems.\".format(type(password)),
            category=DeprecationWarning,
        )
        password = str(password)
    # -- End Removal --

    if isinstance(username, str):
        username = username.encode(\"latin1\")

    if isinstance(password, str):
        password = password.encode(\"latin1\")

    authstr = \"Basic \" + to_native_string(
        b64encode(b\":\".join((username, password))).strip()
    )

    return authstr


class AuthBase:
    \"\"\"Base class that all auth implementations derive from\"\"\"

    def __call__(self, r):
        raise NotImplementedError(\"Auth hooks must be callable.\")


class HTTPBasicAuth(AuthBase):
    \"\"\"Attaches HTTP Basic Authentication to the given Request object.\"\"\"

    def __init__(self, username, password):
        self.username = username
        self.password = password

    def __eq__(self, other):
        return all(
            [
                self.username == getattr(other, \"username\", None),
                self.password == getattr(other, \"password\", None),
            ]
        )

    def __ne__(self, other):
        return not self == other

    def __call__(self, r):
        r.headers[\"Authorization\"] = _basic_auth_str(self.username, self.password)
        return r


class HTTPProxyAuth(HTTPBasicAuth):
    \"\"\"Attaches HTTP Proxy Authentication to a given Request object.\"\"\"

    def __call__(self, r):
        r.headers[\"Proxy-Authorization\"] = _basic_auth_str(self.username, self.password)
        return r


class HTTPDigestAuth(AuthBase):
    \"\"\"Attaches HTTP Digest Authentication to the given Request object.\"\"\"

    def __init__(self, username, password):
        self.username = username
        self.password = password
        # Keep state in per-thread local storage
        self._thread_local = threading.local()

    def init_per_thread_state(self):
        # Ensure state is initialized just once per-thread
        if not hasattr(self._thread_local, \"init\"):
            self._thread_local.init = True
            self._thread_local.last_nonce = \"\"
            self._thread_local.nonce_count = 0
            self._thread_local.chal = {}
            self._thread_local.pos = None
            self._thread_local.num_401_calls = None

    def build_digest_header(self, method, url):
        \"\"\"
        :rtype: str
        \"\"\"

        realm = self._thread_local.chal[\"realm\"]
        nonce = self._thread_local.chal[\"nonce\"]
        qop = self._thread_local.chal.get(\"qop\")
        algorithm = self._thread_local.chal.get(\"algorithm\")
        opaque = self._thread_local.chal.get(\"opaque\")
        hash_utf8 = None

        if algorithm is None:
            _algorithm = \"MD5\"
        else:
            _algorithm = algorithm.upper()
        # lambdas assume digest modules are imported at the top level
        if _algorithm == \"MD5\" or _algorithm == \"MD5-SESS\":

            def md5_utf8(x):
                if isinstance(x, str):
                    x = x.encode(\"utf-8\")
                return hashlib.md5(x).hexdigest()

            hash_utf8 = md5_utf8
        elif _algorithm == \"SHA\":

            def sha_utf8(x):
                if isinstance(x, str):
                    x = x.encode(\"utf-8\")
                return hashlib.sha1(x).hexdigest()

            hash_utf8 = sha_utf8
        elif _algorithm == \"SHA-256\":

            def sha256_utf8(x):
                if isinstance(x, str):
                    x = x.encode(\"utf-8\")
                return hashlib.sha256(x).hexdigest()

            hash_utf8 = sha256_utf8
        elif _algorithm == \"SHA-512\":

            def sha512_utf8(x):
                if isinstance(x, str):
                    x = x.encode(\"utf-8\")
                return hashlib.sha512(x).hexdigest()

            hash_utf8 = sha512_utf8

        KD = lambda s, d: hash_utf8(f\"{s}:{d}\")  # noqa:E731

        if hash_utf8 is None:
            return None

        # XXX not implemented yet
        entdig = None
        p_parsed = urlparse(url)
        #: path is request-uri defined in RFC 2616 which should not be empty
        path = p_parsed.path or \"/\"
        if p_parsed.query:
            path += f\"?{p_parsed.query}\"

        A1 = f\"{self.username}:{realm}:{self.password}\"
        A2 = f\"{method}:{path}\"

        HA1 = hash_utf8(A1)
        HA2 = hash_utf8(A2)

        if nonce == self._thread_local.last_nonce:
            self._thread_local.nonce_count += 1
        else:
            self._thread_local.nonce_count = 1
        ncvalue = f\"{self._thread_local.nonce_count:08x}\"
        s = str(self._thread_local.nonce_count).encode(\"utf-8\")
        s += nonce.encode(\"utf-8\")
        s += time.ctime().encode(\"utf-8\")
        s += os.urandom(8)

        cnonce = hashlib.sha1(s).hexdigest()[:16]
        if _algorithm == \"MD5-SESS\":
            HA1 = hash_utf8(f\"{HA1}:{nonce}:{cnonce}\")

        if not qop:
            respdig = KD(HA1, f\"{nonce}:{HA2}\")
        elif qop == \"auth\" or \"auth\" in qop.split(\",\"):
            noncebit = f\"{nonce}:{ncvalue}:{cnonce}:auth:{HA2}\"
            respdig = KD(HA1, noncebit)
        else:
            # XXX handle auth-int.
            return None

        self._thread_local.last_nonce = nonce

        # XXX should the partial digests be encoded too?
        base = (
            f'username=\"{self.username}\", realm=\"{realm}\", nonce=\"{nonce}\", '
            f'uri=\"{path}\", response=\"{respdig}\"'
        )
        if opaque:
            base += f', opaque=\"{opaque}\"'
        if algorithm:
            base += f', algorithm=\"{algorithm}\"'
        if entdig:
            base += f', digest=\"{entdig}\"'
        if qop:
            base += f', qop=\"auth\", nc={ncvalue}, cnonce=\"{cnonce}\"'

        return f\"Digest {base}\"

    def handle_redirect(self, r, **kwargs):
        \"\"\"Reset num_401_calls counter on redirects.\"\"\"
        if r.is_redirect:
            self._thread_local.num_401_calls = 1

    def handle_401(self, r, **kwargs):
        \"\"\"
        Takes the given response and tries digest-auth, if needed.

        :rtype: requests.Response
        \"\"\"

        # If response is not 4xx, do not auth
        # See https://github.com/psf/requests/issues/3772
        if not 400 <= r.status_code < 500:
            self._thread_local.num_401_calls = 1
            return r

        if self._thread_local.pos is not None:
            # Rewind the file position indicator of the body to where
            # it was to resend the request.
            r.request.body.seek(self._thread_local.pos)
        s_auth = r.headers.get(\"www-authenticate\", \"\")

        if \"digest\" in s_auth.lower() and self._thread_local.num_401_calls < 2:

            self._thread_local.num_401_calls += 1
            pat = re.compile(r\"digest \", flags=re.IGNORECASE)
            self._thread_local.chal = parse_dict_header(pat.sub(\"\", s_auth, count=1))

            # Consume content and release the original connection
            # to allow our new request to reuse the same one.
            r.content
            r.close()
            prep = r.request.copy()
            extract_cookies_to_jar(prep._cookies, r.request, r.raw)
            prep.prepare_cookies(prep._cookies)

            prep.headers[\"Authorization\"] = self.build_digest_header(
                prep.method, prep.url
            )
            _r = r.connection.send(prep, **kwargs)
            _r.history.append(r)
            _r.request = prep

            return _r

        self._thread_local.num_401_calls = 1
        return r

    def __call__(self, r):
        # Initialize per-thread state, if needed
        self.init_per_thread_state()
        # If we have a saved nonce, skip the 401
        if self._thread_local.last_nonce:
            r.headers[\"Authorization\"] = self.build_digest_header(r.method, r.url)
        try:
            self._thread_local.pos = r.body.tell()
        except AttributeError:
            # In the case of HTTPDigestAuth being reused and the body of
            # the previous request was a file-like object, pos has the
            # file position of the previous body. Ensure it's set to
            # None.
            self._thread_local.pos = None
        r.register_hook(\"response\", self.handle_401)
        r.register_hook(\"response\", self.handle_redirect)
        self._thread_local.num_401_calls = 1

        return r

    def __eq__(self, other):
        return all(
            [
                self.username == getattr(other, \"username\", None),
                self.password == getattr(other, \"password\", None),
            ]
        )

    def __ne__(self, other):
        return not self == other

"""
module_dict["requests"+os.sep+"sessions.py"]="""
\"\"\"
requests.sessions
~~~~~~~~~~~~~~~~~

This module provides a Session object to manage and persist settings across
requests (cookies, auth, proxies).
\"\"\"
import os
import sys
import time
from collections import OrderedDict
from datetime import timedelta

from ._internal_utils import to_native_string
from .adapters import HTTPAdapter
from .auth import _basic_auth_str
from .compat import Mapping, cookielib, urljoin, urlparse
from .cookies import (
    RequestsCookieJar,
    cookiejar_from_dict,
    extract_cookies_to_jar,
    merge_cookies,
)
from .exceptions import (
    ChunkedEncodingError,
    ContentDecodingError,
    InvalidSchema,
    TooManyRedirects,
)
from .hooks import default_hooks, dispatch_hook

# formerly defined here, reexposed here for backward compatibility
from .models import (  # noqa: F401
    DEFAULT_REDIRECT_LIMIT,
    REDIRECT_STATI,
    PreparedRequest,
    Request,
)
from .status_codes import codes
from .structures import CaseInsensitiveDict
from .utils import (  # noqa: F401
    DEFAULT_PORTS,
    default_headers,
    get_auth_from_url,
    get_environ_proxies,
    get_netrc_auth,
    requote_uri,
    resolve_proxies,
    rewind_body,
    should_bypass_proxies,
    to_key_val_list,
)

# Preferred clock, based on which one is more accurate on a given system.
if sys.platform == \"win32\":
    preferred_clock = time.perf_counter
else:
    preferred_clock = time.time


def merge_setting(request_setting, session_setting, dict_class=OrderedDict):
    \"\"\"Determines appropriate setting for a given request, taking into account
    the explicit setting on that request, and the setting in the session. If a
    setting is a dictionary, they will be merged together using `dict_class`
    \"\"\"

    if session_setting is None:
        return request_setting

    if request_setting is None:
        return session_setting

    # Bypass if not a dictionary (e.g. verify)
    if not (
        isinstance(session_setting, Mapping) and isinstance(request_setting, Mapping)
    ):
        return request_setting

    merged_setting = dict_class(to_key_val_list(session_setting))
    merged_setting.update(to_key_val_list(request_setting))

    # Remove keys that are set to None. Extract keys first to avoid altering
    # the dictionary during iteration.
    none_keys = [k for (k, v) in merged_setting.items() if v is None]
    for key in none_keys:
        del merged_setting[key]

    return merged_setting


def merge_hooks(request_hooks, session_hooks, dict_class=OrderedDict):
    \"\"\"Properly merges both requests and session hooks.

    This is necessary because when request_hooks == {'response': []}, the
    merge breaks Session hooks entirely.
    \"\"\"
    if session_hooks is None or session_hooks.get(\"response\") == []:
        return request_hooks

    if request_hooks is None or request_hooks.get(\"response\") == []:
        return session_hooks

    return merge_setting(request_hooks, session_hooks, dict_class)


class SessionRedirectMixin:
    def get_redirect_target(self, resp):
        \"\"\"Receives a Response. Returns a redirect URI or ``None``\"\"\"
        # Due to the nature of how requests processes redirects this method will
        # be called at least once upon the original response and at least twice
        # on each subsequent redirect response (if any).
        # If a custom mixin is used to handle this logic, it may be advantageous
        # to cache the redirect location onto the response object as a private
        # attribute.
        if resp.is_redirect:
            location = resp.headers[\"location\"]
            # Currently the underlying http module on py3 decode headers
            # in latin1, but empirical evidence suggests that latin1 is very
            # rarely used with non-ASCII characters in HTTP headers.
            # It is more likely to get UTF8 header rather than latin1.
            # This causes incorrect handling of UTF8 encoded location headers.
            # To solve this, we re-encode the location in latin1.
            location = location.encode(\"latin1\")
            return to_native_string(location, \"utf8\")
        return None

    def should_strip_auth(self, old_url, new_url):
        \"\"\"Decide whether Authorization header should be removed when redirecting\"\"\"
        old_parsed = urlparse(old_url)
        new_parsed = urlparse(new_url)
        if old_parsed.hostname != new_parsed.hostname:
            return True
        # Special case: allow http -> https redirect when using the standard
        # ports. This isn't specified by RFC 7235, but is kept to avoid
        # breaking backwards compatibility with older versions of requests
        # that allowed any redirects on the same host.
        if (
            old_parsed.scheme == \"http\"
            and old_parsed.port in (80, None)
            and new_parsed.scheme == \"https\"
            and new_parsed.port in (443, None)
        ):
            return False

        # Handle default port usage corresponding to scheme.
        changed_port = old_parsed.port != new_parsed.port
        changed_scheme = old_parsed.scheme != new_parsed.scheme
        default_port = (DEFAULT_PORTS.get(old_parsed.scheme, None), None)
        if (
            not changed_scheme
            and old_parsed.port in default_port
            and new_parsed.port in default_port
        ):
            return False

        # Standard case: root URI must match
        return changed_port or changed_scheme

    def resolve_redirects(
        self,
        resp,
        req,
        stream=False,
        timeout=None,
        verify=True,
        cert=None,
        proxies=None,
        yield_requests=False,
        **adapter_kwargs,
    ):
        \"\"\"Receives a Response. Returns a generator of Responses or Requests.\"\"\"

        hist = []  # keep track of history

        url = self.get_redirect_target(resp)
        previous_fragment = urlparse(req.url).fragment
        while url:
            prepared_request = req.copy()

            # Update history and keep track of redirects.
            # resp.history must ignore the original request in this loop
            hist.append(resp)
            resp.history = hist[1:]

            try:
                resp.content  # Consume socket so it can be released
            except (ChunkedEncodingError, ContentDecodingError, RuntimeError):
                resp.raw.read(decode_content=False)

            if len(resp.history) >= self.max_redirects:
                raise TooManyRedirects(
                    f\"Exceeded {self.max_redirects} redirects.\", response=resp
                )

            # Release the connection back into the pool.
            resp.close()

            # Handle redirection without scheme (see: RFC 1808 Section 4)
            if url.startswith(\"//\"):
                parsed_rurl = urlparse(resp.url)
                url = \":\".join([to_native_string(parsed_rurl.scheme), url])

            # Normalize url case and attach previous fragment if needed (RFC 7231 7.1.2)
            parsed = urlparse(url)
            if parsed.fragment == \"\" and previous_fragment:
                parsed = parsed._replace(fragment=previous_fragment)
            elif parsed.fragment:
                previous_fragment = parsed.fragment
            url = parsed.geturl()

            # Facilitate relative 'location' headers, as allowed by RFC 7231.
            # (e.g. '/path/to/resource' instead of 'http://domain.tld/path/to/resource')
            # Compliant with RFC3986, we percent encode the url.
            if not parsed.netloc:
                url = urljoin(resp.url, requote_uri(url))
            else:
                url = requote_uri(url)

            prepared_request.url = to_native_string(url)

            self.rebuild_method(prepared_request, resp)

            # https://github.com/psf/requests/issues/1084
            if resp.status_code not in (
                codes.temporary_redirect,
                codes.permanent_redirect,
            ):
                # https://github.com/psf/requests/issues/3490
                purged_headers = (\"Content-Length\", \"Content-Type\", \"Transfer-Encoding\")
                for header in purged_headers:
                    prepared_request.headers.pop(header, None)
                prepared_request.body = None

            headers = prepared_request.headers
            headers.pop(\"Cookie\", None)

            # Extract any cookies sent on the response to the cookiejar
            # in the new request. Because we've mutated our copied prepared
            # request, use the old one that we haven't yet touched.
            extract_cookies_to_jar(prepared_request._cookies, req, resp.raw)
            merge_cookies(prepared_request._cookies, self.cookies)
            prepared_request.prepare_cookies(prepared_request._cookies)

            # Rebuild auth and proxy information.
            proxies = self.rebuild_proxies(prepared_request, proxies)
            self.rebuild_auth(prepared_request, resp)

            # A failed tell() sets `_body_position` to `object()`. This non-None
            # value ensures `rewindable` will be True, allowing us to raise an
            # UnrewindableBodyError, instead of hanging the connection.
            rewindable = prepared_request._body_position is not None and (
                \"Content-Length\" in headers or \"Transfer-Encoding\" in headers
            )

            # Attempt to rewind consumed file-like object.
            if rewindable:
                rewind_body(prepared_request)

            # Override the original request.
            req = prepared_request

            if yield_requests:
                yield req
            else:

                resp = self.send(
                    req,
                    stream=stream,
                    timeout=timeout,
                    verify=verify,
                    cert=cert,
                    proxies=proxies,
                    allow_redirects=False,
                    **adapter_kwargs,
                )

                extract_cookies_to_jar(self.cookies, prepared_request, resp.raw)

                # extract redirect url, if any, for the next loop
                url = self.get_redirect_target(resp)
                yield resp

    def rebuild_auth(self, prepared_request, response):
        \"\"\"When being redirected we may want to strip authentication from the
        request to avoid leaking credentials. This method intelligently removes
        and reapplies authentication where possible to avoid credential loss.
        \"\"\"
        headers = prepared_request.headers
        url = prepared_request.url

        if \"Authorization\" in headers and self.should_strip_auth(
            response.request.url, url
        ):
            # If we get redirected to a new host, we should strip out any
            # authentication headers.
            del headers[\"Authorization\"]

        # .netrc might have more auth for us on our new host.
        new_auth = get_netrc_auth(url) if self.trust_env else None
        if new_auth is not None:
            prepared_request.prepare_auth(new_auth)

    def rebuild_proxies(self, prepared_request, proxies):
        \"\"\"This method re-evaluates the proxy configuration by considering the
        environment variables. If we are redirected to a URL covered by
        NO_PROXY, we strip the proxy configuration. Otherwise, we set missing
        proxy keys for this URL (in case they were stripped by a previous
        redirect).

        This method also replaces the Proxy-Authorization header where
        necessary.

        :rtype: dict
        \"\"\"
        headers = prepared_request.headers
        scheme = urlparse(prepared_request.url).scheme
        new_proxies = resolve_proxies(prepared_request, proxies, self.trust_env)

        if \"Proxy-Authorization\" in headers:
            del headers[\"Proxy-Authorization\"]

        try:
            username, password = get_auth_from_url(new_proxies[scheme])
        except KeyError:
            username, password = None, None

        if username and password:
            headers[\"Proxy-Authorization\"] = _basic_auth_str(username, password)

        return new_proxies

    def rebuild_method(self, prepared_request, response):
        \"\"\"When being redirected we may want to change the method of the request
        based on certain specs or browser behavior.
        \"\"\"
        method = prepared_request.method

        # https://tools.ietf.org/html/rfc7231#section-6.4.4
        if response.status_code == codes.see_other and method != \"HEAD\":
            method = \"GET\"

        # Do what the browsers do, despite standards...
        # First, turn 302s into GETs.
        if response.status_code == codes.found and method != \"HEAD\":
            method = \"GET\"

        # Second, if a POST is responded to with a 301, turn it into a GET.
        # This bizarre behaviour is explained in Issue 1704.
        if response.status_code == codes.moved and method == \"POST\":
            method = \"GET\"

        prepared_request.method = method


class Session(SessionRedirectMixin):
    \"\"\"A Requests session.

    Provides cookie persistence, connection-pooling, and configuration.

    Basic Usage::

      >>> import requests
      >>> s = requests.Session()
      >>> s.get('https://httpbin.org/get')
      <Response [200]>

    Or as a context manager::

      >>> with requests.Session() as s:
      ...     s.get('https://httpbin.org/get')
      <Response [200]>
    \"\"\"

    __attrs__ = [
        \"headers\",
        \"cookies\",
        \"auth\",
        \"proxies\",
        \"hooks\",
        \"params\",
        \"verify\",
        \"cert\",
        \"adapters\",
        \"stream\",
        \"trust_env\",
        \"max_redirects\",
    ]

    def __init__(self):

        #: A case-insensitive dictionary of headers to be sent on each
        #: :class:`Request <Request>` sent from this
        #: :class:`Session <Session>`.
        self.headers = default_headers()

        #: Default Authentication tuple or object to attach to
        #: :class:`Request <Request>`.
        self.auth = None

        #: Dictionary mapping protocol or protocol and host to the URL of the proxy
        #: (e.g. {'http': 'foo.bar:3128', 'http://host.name': 'foo.bar:4012'}) to
        #: be used on each :class:`Request <Request>`.
        self.proxies = {}

        #: Event-handling hooks.
        self.hooks = default_hooks()

        #: Dictionary of querystring data to attach to each
        #: :class:`Request <Request>`. The dictionary values may be lists for
        #: representing multivalued query parameters.
        self.params = {}

        #: Stream response content default.
        self.stream = False

        #: SSL Verification default.
        #: Defaults to `True`, requiring requests to verify the TLS certificate at the
        #: remote end.
        #: If verify is set to `False`, requests will accept any TLS certificate
        #: presented by the server, and will ignore hostname mismatches and/or
        #: expired certificates, which will make your application vulnerable to
        #: man-in-the-middle (MitM) attacks.
        #: Only set this to `False` for testing.
        self.verify = True

        #: SSL client certificate default, if String, path to ssl client
        #: cert file (.pem). If Tuple, ('cert', 'key') pair.
        self.cert = None

        #: Maximum number of redirects allowed. If the request exceeds this
        #: limit, a :class:`TooManyRedirects` exception is raised.
        #: This defaults to requests.models.DEFAULT_REDIRECT_LIMIT, which is
        #: 30.
        self.max_redirects = DEFAULT_REDIRECT_LIMIT

        #: Trust environment settings for proxy configuration, default
        #: authentication and similar.
        self.trust_env = True

        #: A CookieJar containing all currently outstanding cookies set on this
        #: session. By default it is a
        #: :class:`RequestsCookieJar <requests.cookies.RequestsCookieJar>`, but
        #: may be any other ``cookielib.CookieJar`` compatible object.
        self.cookies = cookiejar_from_dict({})

        # Default connection adapters.
        self.adapters = OrderedDict()
        self.mount(\"https://\", HTTPAdapter())
        self.mount(\"http://\", HTTPAdapter())

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def prepare_request(self, request):
        \"\"\"Constructs a :class:`PreparedRequest <PreparedRequest>` for
        transmission and returns it. The :class:`PreparedRequest` has settings
        merged from the :class:`Request <Request>` instance and those of the
        :class:`Session`.

        :param request: :class:`Request` instance to prepare with this
            session's settings.
        :rtype: requests.PreparedRequest
        \"\"\"
        cookies = request.cookies or {}

        # Bootstrap CookieJar.
        if not isinstance(cookies, cookielib.CookieJar):
            cookies = cookiejar_from_dict(cookies)

        # Merge with session cookies
        merged_cookies = merge_cookies(
            merge_cookies(RequestsCookieJar(), self.cookies), cookies
        )

        # Set environment's basic authentication if not explicitly set.
        auth = request.auth
        if self.trust_env and not auth and not self.auth:
            auth = get_netrc_auth(request.url)

        p = PreparedRequest()
        p.prepare(
            method=request.method.upper(),
            url=request.url,
            files=request.files,
            data=request.data,
            json=request.json,
            headers=merge_setting(
                request.headers, self.headers, dict_class=CaseInsensitiveDict
            ),
            params=merge_setting(request.params, self.params),
            auth=merge_setting(auth, self.auth),
            cookies=merged_cookies,
            hooks=merge_hooks(request.hooks, self.hooks),
        )
        return p

    def request(
        self,
        method,
        url,
        params=None,
        data=None,
        headers=None,
        cookies=None,
        files=None,
        auth=None,
        timeout=None,
        allow_redirects=True,
        proxies=None,
        hooks=None,
        stream=None,
        verify=None,
        cert=None,
        json=None,
    ):
        \"\"\"Constructs a :class:`Request <Request>`, prepares it and sends it.
        Returns :class:`Response <Response>` object.

        :param method: method for the new :class:`Request` object.
        :param url: URL for the new :class:`Request` object.
        :param params: (optional) Dictionary or bytes to be sent in the query
            string for the :class:`Request`.
        :param data: (optional) Dictionary, list of tuples, bytes, or file-like
            object to send in the body of the :class:`Request`.
        :param json: (optional) json to send in the body of the
            :class:`Request`.
        :param headers: (optional) Dictionary of HTTP Headers to send with the
            :class:`Request`.
        :param cookies: (optional) Dict or CookieJar object to send with the
            :class:`Request`.
        :param files: (optional) Dictionary of ``'filename': file-like-objects``
            for multipart encoding upload.
        :param auth: (optional) Auth tuple or callable to enable
            Basic/Digest/Custom HTTP Auth.
        :param timeout: (optional) How long to wait for the server to send
            data before giving up, as a float, or a :ref:`(connect timeout,
            read timeout) <timeouts>` tuple.
        :type timeout: float or tuple
        :param allow_redirects: (optional) Set to True by default.
        :type allow_redirects: bool
        :param proxies: (optional) Dictionary mapping protocol or protocol and
            hostname to the URL of the proxy.
        :param stream: (optional) whether to immediately download the response
            content. Defaults to ``False``.
        :param verify: (optional) Either a boolean, in which case it controls whether we verify
            the server's TLS certificate, or a string, in which case it must be a path
            to a CA bundle to use. Defaults to ``True``. When set to
            ``False``, requests will accept any TLS certificate presented by
            the server, and will ignore hostname mismatches and/or expired
            certificates, which will make your application vulnerable to
            man-in-the-middle (MitM) attacks. Setting verify to ``False``
            may be useful during local development or testing.
        :param cert: (optional) if String, path to ssl client cert file (.pem).
            If Tuple, ('cert', 'key') pair.
        :rtype: requests.Response
        \"\"\"
        # Create the Request.
        req = Request(
            method=method.upper(),
            url=url,
            headers=headers,
            files=files,
            data=data or {},
            json=json,
            params=params or {},
            auth=auth,
            cookies=cookies,
            hooks=hooks,
        )
        prep = self.prepare_request(req)

        proxies = proxies or {}

        settings = self.merge_environment_settings(
            prep.url, proxies, stream, verify, cert
        )

        # Send the request.
        send_kwargs = {
            \"timeout\": timeout,
            \"allow_redirects\": allow_redirects,
        }
        send_kwargs.update(settings)
        resp = self.send(prep, **send_kwargs)

        return resp

    def get(self, url, **kwargs):
        r\"\"\"Sends a GET request. Returns :class:`Response` object.

        :param url: URL for the new :class:`Request` object.
        :param \\*\\*kwargs: Optional arguments that ``request`` takes.
        :rtype: requests.Response
        \"\"\"

        kwargs.setdefault(\"allow_redirects\", True)
        return self.request(\"GET\", url, **kwargs)

    def options(self, url, **kwargs):
        r\"\"\"Sends a OPTIONS request. Returns :class:`Response` object.

        :param url: URL for the new :class:`Request` object.
        :param \\*\\*kwargs: Optional arguments that ``request`` takes.
        :rtype: requests.Response
        \"\"\"

        kwargs.setdefault(\"allow_redirects\", True)
        return self.request(\"OPTIONS\", url, **kwargs)

    def head(self, url, **kwargs):
        r\"\"\"Sends a HEAD request. Returns :class:`Response` object.

        :param url: URL for the new :class:`Request` object.
        :param \\*\\*kwargs: Optional arguments that ``request`` takes.
        :rtype: requests.Response
        \"\"\"

        kwargs.setdefault(\"allow_redirects\", False)
        return self.request(\"HEAD\", url, **kwargs)

    def post(self, url, data=None, json=None, **kwargs):
        r\"\"\"Sends a POST request. Returns :class:`Response` object.

        :param url: URL for the new :class:`Request` object.
        :param data: (optional) Dictionary, list of tuples, bytes, or file-like
            object to send in the body of the :class:`Request`.
        :param json: (optional) json to send in the body of the :class:`Request`.
        :param \\*\\*kwargs: Optional arguments that ``request`` takes.
        :rtype: requests.Response
        \"\"\"

        return self.request(\"POST\", url, data=data, json=json, **kwargs)

    def put(self, url, data=None, **kwargs):
        r\"\"\"Sends a PUT request. Returns :class:`Response` object.

        :param url: URL for the new :class:`Request` object.
        :param data: (optional) Dictionary, list of tuples, bytes, or file-like
            object to send in the body of the :class:`Request`.
        :param \\*\\*kwargs: Optional arguments that ``request`` takes.
        :rtype: requests.Response
        \"\"\"

        return self.request(\"PUT\", url, data=data, **kwargs)

    def patch(self, url, data=None, **kwargs):
        r\"\"\"Sends a PATCH request. Returns :class:`Response` object.

        :param url: URL for the new :class:`Request` object.
        :param data: (optional) Dictionary, list of tuples, bytes, or file-like
            object to send in the body of the :class:`Request`.
        :param \\*\\*kwargs: Optional arguments that ``request`` takes.
        :rtype: requests.Response
        \"\"\"

        return self.request(\"PATCH\", url, data=data, **kwargs)

    def delete(self, url, **kwargs):
        r\"\"\"Sends a DELETE request. Returns :class:`Response` object.

        :param url: URL for the new :class:`Request` object.
        :param \\*\\*kwargs: Optional arguments that ``request`` takes.
        :rtype: requests.Response
        \"\"\"

        return self.request(\"DELETE\", url, **kwargs)

    def send(self, request, **kwargs):
        \"\"\"Send a given PreparedRequest.

        :rtype: requests.Response
        \"\"\"
        # Set defaults that the hooks can utilize to ensure they always have
        # the correct parameters to reproduce the previous request.
        kwargs.setdefault(\"stream\", self.stream)
        kwargs.setdefault(\"verify\", self.verify)
        kwargs.setdefault(\"cert\", self.cert)
        if \"proxies\" not in kwargs:
            kwargs[\"proxies\"] = resolve_proxies(request, self.proxies, self.trust_env)

        # It's possible that users might accidentally send a Request object.
        # Guard against that specific failure case.
        if isinstance(request, Request):
            raise ValueError(\"You can only send PreparedRequests.\")

        # Set up variables needed for resolve_redirects and dispatching of hooks
        allow_redirects = kwargs.pop(\"allow_redirects\", True)
        stream = kwargs.get(\"stream\")
        hooks = request.hooks

        # Get the appropriate adapter to use
        adapter = self.get_adapter(url=request.url)

        # Start time (approximately) of the request
        start = preferred_clock()

        # Send the request
        r = adapter.send(request, **kwargs)

        # Total elapsed time of the request (approximately)
        elapsed = preferred_clock() - start
        r.elapsed = timedelta(seconds=elapsed)

        # Response manipulation hooks
        r = dispatch_hook(\"response\", hooks, r, **kwargs)

        # Persist cookies
        if r.history:

            # If the hooks create history then we want those cookies too
            for resp in r.history:
                extract_cookies_to_jar(self.cookies, resp.request, resp.raw)

        extract_cookies_to_jar(self.cookies, request, r.raw)

        # Resolve redirects if allowed.
        if allow_redirects:
            # Redirect resolving generator.
            gen = self.resolve_redirects(r, request, **kwargs)
            history = [resp for resp in gen]
        else:
            history = []

        # Shuffle things around if there's history.
        if history:
            # Insert the first (original) request at the start
            history.insert(0, r)
            # Get the last request made
            r = history.pop()
            r.history = history

        # If redirects aren't being followed, store the response on the Request for Response.next().
        if not allow_redirects:
            try:
                r._next = next(
                    self.resolve_redirects(r, request, yield_requests=True, **kwargs)
                )
            except StopIteration:
                pass

        if not stream:
            r.content

        return r

    def merge_environment_settings(self, url, proxies, stream, verify, cert):
        \"\"\"
        Check the environment and merge it with some settings.

        :rtype: dict
        \"\"\"
        # Gather clues from the surrounding environment.
        if self.trust_env:
            # Set environment's proxies.
            no_proxy = proxies.get(\"no_proxy\") if proxies is not None else None
            env_proxies = get_environ_proxies(url, no_proxy=no_proxy)
            for (k, v) in env_proxies.items():
                proxies.setdefault(k, v)

            # Look for requests environment configuration
            # and be compatible with cURL.
            if verify is True or verify is None:
                verify = (
                    os.environ.get(\"REQUESTS_CA_BUNDLE\")
                    or os.environ.get(\"CURL_CA_BUNDLE\")
                    or verify
                )

        # Merge all the kwargs.
        proxies = merge_setting(proxies, self.proxies)
        stream = merge_setting(stream, self.stream)
        verify = merge_setting(verify, self.verify)
        cert = merge_setting(cert, self.cert)

        return {\"proxies\": proxies, \"stream\": stream, \"verify\": verify, \"cert\": cert}

    def get_adapter(self, url):
        \"\"\"
        Returns the appropriate connection adapter for the given URL.

        :rtype: requests.adapters.BaseAdapter
        \"\"\"
        for (prefix, adapter) in self.adapters.items():

            if url.lower().startswith(prefix.lower()):
                return adapter

        # Nothing matches :-/
        raise InvalidSchema(f\"No connection adapters were found for {url!r}\")

    def close(self):
        \"\"\"Closes all adapters and as such the session\"\"\"
        for v in self.adapters.values():
            v.close()

    def mount(self, prefix, adapter):
        \"\"\"Registers a connection adapter to a prefix.

        Adapters are sorted in descending order by prefix length.
        \"\"\"
        self.adapters[prefix] = adapter
        keys_to_move = [k for k in self.adapters if len(k) < len(prefix)]

        for key in keys_to_move:
            self.adapters[key] = self.adapters.pop(key)

    def __getstate__(self):
        state = {attr: getattr(self, attr, None) for attr in self.__attrs__}
        return state

    def __setstate__(self, state):
        for attr, value in state.items():
            setattr(self, attr, value)


def session():
    \"\"\"
    Returns a :class:`Session` for context-management.

    .. deprecated:: 1.0.0

        This method has been deprecated since version 1.0.0 and is only kept for
        backwards compatibility. New code should use :class:`~requests.sessions.Session`
        to create a session. This may be removed at a future date.

    :rtype: Session
    \"\"\"
    return Session()

"""
module_dict["requests"+os.sep+"hooks.py"]="""
\"\"\"
requests.hooks
~~~~~~~~~~~~~~

This module provides the capabilities for the Requests hooks system.

Available hooks:

``response``:
    The response generated from a Request.
\"\"\"
HOOKS = [\"response\"]


def default_hooks():
    return {event: [] for event in HOOKS}


# TODO: response is the only one


def dispatch_hook(key, hooks, hook_data, **kwargs):
    \"\"\"Dispatches a hook dictionary on a given piece of data.\"\"\"
    hooks = hooks or {}
    hooks = hooks.get(key)
    if hooks:
        if hasattr(hooks, \"__call__\"):
            hooks = [hooks]
        for hook in hooks:
            _hook_data = hook(hook_data, **kwargs)
            if _hook_data is not None:
                hook_data = _hook_data
    return hook_data

"""
module_dict["requests"+os.sep+"packages.py"]="""
# < include 'chardet.py' >

# < include 'charset_normalizer.py' >

import sys

try:
    import chardet
except ImportError:
    import warnings

    import charset_normalizer as chardet

    warnings.filterwarnings(\"ignore\", \"Trying to detect\", module=\"charset_normalizer\")

# This code exists for backwards compatibility reasons.
# I don't like it either. Just look the other way. :)

for package in (\"urllib3\", \"idna\"):
    locals()[package] = __import__(package)
    # This traversal is apparently necessary such that the identities are
    # preserved (requests.packages.urllib3.* is urllib3.*)
    for mod in list(sys.modules):
        if mod == package or mod.startswith(f\"{package}.\"):
            sys.modules[f\"requests.packages.{mod}\"] = sys.modules[mod]

target = chardet.__name__
for mod in list(sys.modules):
    if mod == target or mod.startswith(f\"{target}.\"):
        target = target.replace(target, \"chardet\")
        sys.modules[f\"requests.packages.{target}\"] = sys.modules[mod]
# Kinda cool, though, right?

"""
module_dict["requests"+os.sep+"adapters.py"]="""
# < include 'urllib3.py' >

\"\"\"
requests.adapters
~~~~~~~~~~~~~~~~~

This module contains the transport adapters that Requests uses to define
and maintain connections.
\"\"\"

import os.path
import socket  # noqa: F401

from urllib3.exceptions import ClosedPoolError, ConnectTimeoutError
from urllib3.exceptions import HTTPError as _HTTPError
from urllib3.exceptions import InvalidHeader as _InvalidHeader
from urllib3.exceptions import (
    LocationValueError,
    MaxRetryError,
    NewConnectionError,
    ProtocolError,
)
from urllib3.exceptions import ProxyError as _ProxyError
from urllib3.exceptions import ReadTimeoutError, ResponseError
from urllib3.exceptions import SSLError as _SSLError
from urllib3.poolmanager import PoolManager, proxy_from_url
from urllib3.response import HTTPResponse
from urllib3.util import Timeout as TimeoutSauce
from urllib3.util import parse_url
from urllib3.util.retry import Retry

from .auth import _basic_auth_str
from .compat import basestring, urlparse
from .cookies import extract_cookies_to_jar
from .exceptions import (
    ConnectionError,
    ConnectTimeout,
    InvalidHeader,
    InvalidProxyURL,
    InvalidSchema,
    InvalidURL,
    ProxyError,
    ReadTimeout,
    RetryError,
    SSLError,
)
from .models import Response
from .structures import CaseInsensitiveDict
from .utils import (
    DEFAULT_CA_BUNDLE_PATH,
    extract_zipped_paths,
    get_auth_from_url,
    get_encoding_from_headers,
    prepend_scheme_if_needed,
    select_proxy,
    urldefragauth,
)

try:
    from urllib3.contrib.socks import SOCKSProxyManager
except ImportError:

    def SOCKSProxyManager(*args, **kwargs):
        raise InvalidSchema(\"Missing dependencies for SOCKS support.\")


DEFAULT_POOLBLOCK = False
DEFAULT_POOLSIZE = 10
DEFAULT_RETRIES = 0
DEFAULT_POOL_TIMEOUT = None


class BaseAdapter:
    \"\"\"The Base Transport Adapter\"\"\"

    def __init__(self):
        super().__init__()

    def send(
        self, request, stream=False, timeout=None, verify=True, cert=None, proxies=None
    ):
        \"\"\"Sends PreparedRequest object. Returns Response object.

        :param request: The :class:`PreparedRequest <PreparedRequest>` being sent.
        :param stream: (optional) Whether to stream the request content.
        :param timeout: (optional) How long to wait for the server to send
            data before giving up, as a float, or a :ref:`(connect timeout,
            read timeout) <timeouts>` tuple.
        :type timeout: float or tuple
        :param verify: (optional) Either a boolean, in which case it controls whether we verify
            the server's TLS certificate, or a string, in which case it must be a path
            to a CA bundle to use
        :param cert: (optional) Any user-provided SSL certificate to be trusted.
        :param proxies: (optional) The proxies dictionary to apply to the request.
        \"\"\"
        raise NotImplementedError

    def close(self):
        \"\"\"Cleans up adapter specific items.\"\"\"
        raise NotImplementedError


class HTTPAdapter(BaseAdapter):
    \"\"\"The built-in HTTP Adapter for urllib3.

    Provides a general-case interface for Requests sessions to contact HTTP and
    HTTPS urls by implementing the Transport Adapter interface. This class will
    usually be created by the :class:`Session <Session>` class under the
    covers.

    :param pool_connections: The number of urllib3 connection pools to cache.
    :param pool_maxsize: The maximum number of connections to save in the pool.
    :param max_retries: The maximum number of retries each connection
        should attempt. Note, this applies only to failed DNS lookups, socket
        connections and connection timeouts, never to requests where data has
        made it to the server. By default, Requests does not retry failed
        connections. If you need granular control over the conditions under
        which we retry a request, import urllib3's ``Retry`` class and pass
        that instead.
    :param pool_block: Whether the connection pool should block for connections.

    Usage::

      >>> import requests
      >>> s = requests.Session()
      >>> a = requests.adapters.HTTPAdapter(max_retries=3)
      >>> s.mount('http://', a)
    \"\"\"

    __attrs__ = [
        \"max_retries\",
        \"config\",
        \"_pool_connections\",
        \"_pool_maxsize\",
        \"_pool_block\",
    ]

    def __init__(
        self,
        pool_connections=DEFAULT_POOLSIZE,
        pool_maxsize=DEFAULT_POOLSIZE,
        max_retries=DEFAULT_RETRIES,
        pool_block=DEFAULT_POOLBLOCK,
    ):
        if max_retries == DEFAULT_RETRIES:
            self.max_retries = Retry(0, read=False)
        else:
            self.max_retries = Retry.from_int(max_retries)
        self.config = {}
        self.proxy_manager = {}

        super().__init__()

        self._pool_connections = pool_connections
        self._pool_maxsize = pool_maxsize
        self._pool_block = pool_block

        self.init_poolmanager(pool_connections, pool_maxsize, block=pool_block)

    def __getstate__(self):
        return {attr: getattr(self, attr, None) for attr in self.__attrs__}

    def __setstate__(self, state):
        # Can't handle by adding 'proxy_manager' to self.__attrs__ because
        # self.poolmanager uses a lambda function, which isn't pickleable.
        self.proxy_manager = {}
        self.config = {}

        for attr, value in state.items():
            setattr(self, attr, value)

        self.init_poolmanager(
            self._pool_connections, self._pool_maxsize, block=self._pool_block
        )

    def init_poolmanager(
        self, connections, maxsize, block=DEFAULT_POOLBLOCK, **pool_kwargs
    ):
        \"\"\"Initializes a urllib3 PoolManager.

        This method should not be called from user code, and is only
        exposed for use when subclassing the
        :class:`HTTPAdapter <requests.adapters.HTTPAdapter>`.

        :param connections: The number of urllib3 connection pools to cache.
        :param maxsize: The maximum number of connections to save in the pool.
        :param block: Block when no free connections are available.
        :param pool_kwargs: Extra keyword arguments used to initialize the Pool Manager.
        \"\"\"
        # save these values for pickling
        self._pool_connections = connections
        self._pool_maxsize = maxsize
        self._pool_block = block

        self.poolmanager = PoolManager(
            num_pools=connections,
            maxsize=maxsize,
            block=block,
            strict=True,
            **pool_kwargs,
        )

    def proxy_manager_for(self, proxy, **proxy_kwargs):
        \"\"\"Return urllib3 ProxyManager for the given proxy.

        This method should not be called from user code, and is only
        exposed for use when subclassing the
        :class:`HTTPAdapter <requests.adapters.HTTPAdapter>`.

        :param proxy: The proxy to return a urllib3 ProxyManager for.
        :param proxy_kwargs: Extra keyword arguments used to configure the Proxy Manager.
        :returns: ProxyManager
        :rtype: urllib3.ProxyManager
        \"\"\"
        if proxy in self.proxy_manager:
            manager = self.proxy_manager[proxy]
        elif proxy.lower().startswith(\"socks\"):
            username, password = get_auth_from_url(proxy)
            manager = self.proxy_manager[proxy] = SOCKSProxyManager(
                proxy,
                username=username,
                password=password,
                num_pools=self._pool_connections,
                maxsize=self._pool_maxsize,
                block=self._pool_block,
                **proxy_kwargs,
            )
        else:
            proxy_headers = self.proxy_headers(proxy)
            manager = self.proxy_manager[proxy] = proxy_from_url(
                proxy,
                proxy_headers=proxy_headers,
                num_pools=self._pool_connections,
                maxsize=self._pool_maxsize,
                block=self._pool_block,
                **proxy_kwargs,
            )

        return manager

    def cert_verify(self, conn, url, verify, cert):
        \"\"\"Verify a SSL certificate. This method should not be called from user
        code, and is only exposed for use when subclassing the
        :class:`HTTPAdapter <requests.adapters.HTTPAdapter>`.

        :param conn: The urllib3 connection object associated with the cert.
        :param url: The requested URL.
        :param verify: Either a boolean, in which case it controls whether we verify
            the server's TLS certificate, or a string, in which case it must be a path
            to a CA bundle to use
        :param cert: The SSL certificate to verify.
        \"\"\"
        if url.lower().startswith(\"https\") and verify:

            cert_loc = None

            # Allow self-specified cert location.
            if verify is not True:
                cert_loc = verify

            if not cert_loc:
                cert_loc = extract_zipped_paths(DEFAULT_CA_BUNDLE_PATH)

            if not cert_loc or not os.path.exists(cert_loc):
                raise OSError(
                    f\"Could not find a suitable TLS CA certificate bundle, \"
                    f\"invalid path: {cert_loc}\"
                )

            conn.cert_reqs = \"CERT_REQUIRED\"

            if not os.path.isdir(cert_loc):
                conn.ca_certs = cert_loc
            else:
                conn.ca_cert_dir = cert_loc
        else:
            conn.cert_reqs = \"CERT_NONE\"
            conn.ca_certs = None
            conn.ca_cert_dir = None

        if cert:
            if not isinstance(cert, basestring):
                conn.cert_file = cert[0]
                conn.key_file = cert[1]
            else:
                conn.cert_file = cert
                conn.key_file = None
            if conn.cert_file and not os.path.exists(conn.cert_file):
                raise OSError(
                    f\"Could not find the TLS certificate file, \"
                    f\"invalid path: {conn.cert_file}\"
                )
            if conn.key_file and not os.path.exists(conn.key_file):
                raise OSError(
                    f\"Could not find the TLS key file, invalid path: {conn.key_file}\"
                )

    def build_response(self, req, resp):
        \"\"\"Builds a :class:`Response <requests.Response>` object from a urllib3
        response. This should not be called from user code, and is only exposed
        for use when subclassing the
        :class:`HTTPAdapter <requests.adapters.HTTPAdapter>`

        :param req: The :class:`PreparedRequest <PreparedRequest>` used to generate the response.
        :param resp: The urllib3 response object.
        :rtype: requests.Response
        \"\"\"
        response = Response()

        # Fallback to None if there's no status_code, for whatever reason.
        response.status_code = getattr(resp, \"status\", None)

        # Make headers case-insensitive.
        response.headers = CaseInsensitiveDict(getattr(resp, \"headers\", {}))

        # Set encoding.
        response.encoding = get_encoding_from_headers(response.headers)
        response.raw = resp
        response.reason = response.raw.reason

        if isinstance(req.url, bytes):
            response.url = req.url.decode(\"utf-8\")
        else:
            response.url = req.url

        # Add new cookies from the server.
        extract_cookies_to_jar(response.cookies, req, resp)

        # Give the Response some context.
        response.request = req
        response.connection = self

        return response

    def get_connection(self, url, proxies=None):
        \"\"\"Returns a urllib3 connection for the given URL. This should not be
        called from user code, and is only exposed for use when subclassing the
        :class:`HTTPAdapter <requests.adapters.HTTPAdapter>`.

        :param url: The URL to connect to.
        :param proxies: (optional) A Requests-style dictionary of proxies used on this request.
        :rtype: urllib3.ConnectionPool
        \"\"\"
        proxy = select_proxy(url, proxies)

        if proxy:
            proxy = prepend_scheme_if_needed(proxy, \"http\")
            proxy_url = parse_url(proxy)
            if not proxy_url.host:
                raise InvalidProxyURL(
                    \"Please check proxy URL. It is malformed \"
                    \"and could be missing the host.\"
                )
            proxy_manager = self.proxy_manager_for(proxy)
            conn = proxy_manager.connection_from_url(url)
        else:
            # Only scheme should be lower case
            parsed = urlparse(url)
            url = parsed.geturl()
            conn = self.poolmanager.connection_from_url(url)

        return conn

    def close(self):
        \"\"\"Disposes of any internal state.

        Currently, this closes the PoolManager and any active ProxyManager,
        which closes any pooled connections.
        \"\"\"
        self.poolmanager.clear()
        for proxy in self.proxy_manager.values():
            proxy.clear()

    def request_url(self, request, proxies):
        \"\"\"Obtain the url to use when making the final request.

        If the message is being sent through a HTTP proxy, the full URL has to
        be used. Otherwise, we should only use the path portion of the URL.

        This should not be called from user code, and is only exposed for use
        when subclassing the
        :class:`HTTPAdapter <requests.adapters.HTTPAdapter>`.

        :param request: The :class:`PreparedRequest <PreparedRequest>` being sent.
        :param proxies: A dictionary of schemes or schemes and hosts to proxy URLs.
        :rtype: str
        \"\"\"
        proxy = select_proxy(request.url, proxies)
        scheme = urlparse(request.url).scheme

        is_proxied_http_request = proxy and scheme != \"https\"
        using_socks_proxy = False
        if proxy:
            proxy_scheme = urlparse(proxy).scheme.lower()
            using_socks_proxy = proxy_scheme.startswith(\"socks\")

        url = request.path_url
        if is_proxied_http_request and not using_socks_proxy:
            url = urldefragauth(request.url)

        return url

    def add_headers(self, request, **kwargs):
        \"\"\"Add any headers needed by the connection. As of v2.0 this does
        nothing by default, but is left for overriding by users that subclass
        the :class:`HTTPAdapter <requests.adapters.HTTPAdapter>`.

        This should not be called from user code, and is only exposed for use
        when subclassing the
        :class:`HTTPAdapter <requests.adapters.HTTPAdapter>`.

        :param request: The :class:`PreparedRequest <PreparedRequest>` to add headers to.
        :param kwargs: The keyword arguments from the call to send().
        \"\"\"
        pass

    def proxy_headers(self, proxy):
        \"\"\"Returns a dictionary of the headers to add to any request sent
        through a proxy. This works with urllib3 magic to ensure that they are
        correctly sent to the proxy, rather than in a tunnelled request if
        CONNECT is being used.

        This should not be called from user code, and is only exposed for use
        when subclassing the
        :class:`HTTPAdapter <requests.adapters.HTTPAdapter>`.

        :param proxy: The url of the proxy being used for this request.
        :rtype: dict
        \"\"\"
        headers = {}
        username, password = get_auth_from_url(proxy)

        if username:
            headers[\"Proxy-Authorization\"] = _basic_auth_str(username, password)

        return headers

    def send(
        self, request, stream=False, timeout=None, verify=True, cert=None, proxies=None
    ):
        \"\"\"Sends PreparedRequest object. Returns Response object.

        :param request: The :class:`PreparedRequest <PreparedRequest>` being sent.
        :param stream: (optional) Whether to stream the request content.
        :param timeout: (optional) How long to wait for the server to send
            data before giving up, as a float, or a :ref:`(connect timeout,
            read timeout) <timeouts>` tuple.
        :type timeout: float or tuple or urllib3 Timeout object
        :param verify: (optional) Either a boolean, in which case it controls whether
            we verify the server's TLS certificate, or a string, in which case it
            must be a path to a CA bundle to use
        :param cert: (optional) Any user-provided SSL certificate to be trusted.
        :param proxies: (optional) The proxies dictionary to apply to the request.
        :rtype: requests.Response
        \"\"\"

        try:
            conn = self.get_connection(request.url, proxies)
        except LocationValueError as e:
            raise InvalidURL(e, request=request)

        self.cert_verify(conn, request.url, verify, cert)
        url = self.request_url(request, proxies)
        self.add_headers(
            request,
            stream=stream,
            timeout=timeout,
            verify=verify,
            cert=cert,
            proxies=proxies,
        )

        chunked = not (request.body is None or \"Content-Length\" in request.headers)

        if isinstance(timeout, tuple):
            try:
                connect, read = timeout
                timeout = TimeoutSauce(connect=connect, read=read)
            except ValueError:
                raise ValueError(
                    f\"Invalid timeout {timeout}. Pass a (connect, read) timeout tuple, \"
                    f\"or a single float to set both timeouts to the same value.\"
                )
        elif isinstance(timeout, TimeoutSauce):
            pass
        else:
            timeout = TimeoutSauce(connect=timeout, read=timeout)

        try:
            if not chunked:
                resp = conn.urlopen(
                    method=request.method,
                    url=url,
                    body=request.body,
                    headers=request.headers,
                    redirect=False,
                    assert_same_host=False,
                    preload_content=False,
                    decode_content=False,
                    retries=self.max_retries,
                    timeout=timeout,
                )

            # Send the request.
            else:
                if hasattr(conn, \"proxy_pool\"):
                    conn = conn.proxy_pool

                low_conn = conn._get_conn(timeout=DEFAULT_POOL_TIMEOUT)

                try:
                    skip_host = \"Host\" in request.headers
                    low_conn.putrequest(
                        request.method,
                        url,
                        skip_accept_encoding=True,
                        skip_host=skip_host,
                    )

                    for header, value in request.headers.items():
                        low_conn.putheader(header, value)

                    low_conn.endheaders()

                    for i in request.body:
                        low_conn.send(hex(len(i))[2:].encode(\"utf-8\"))
                        low_conn.send(b\"\\r\\n\")
                        low_conn.send(i)
                        low_conn.send(b\"\\r\\n\")
                    low_conn.send(b\"0\\r\\n\\r\\n\")

                    # Receive the response from the server
                    r = low_conn.getresponse()

                    resp = HTTPResponse.from_httplib(
                        r,
                        pool=conn,
                        connection=low_conn,
                        preload_content=False,
                        decode_content=False,
                    )
                except Exception:
                    # If we hit any problems here, clean up the connection.
                    # Then, raise so that we can handle the actual exception.
                    low_conn.close()
                    raise

        except (ProtocolError, OSError) as err:
            raise ConnectionError(err, request=request)

        except MaxRetryError as e:
            if isinstance(e.reason, ConnectTimeoutError):
                # TODO: Remove this in 3.0.0: see #2811
                if not isinstance(e.reason, NewConnectionError):
                    raise ConnectTimeout(e, request=request)

            if isinstance(e.reason, ResponseError):
                raise RetryError(e, request=request)

            if isinstance(e.reason, _ProxyError):
                raise ProxyError(e, request=request)

            if isinstance(e.reason, _SSLError):
                # This branch is for urllib3 v1.22 and later.
                raise SSLError(e, request=request)

            raise ConnectionError(e, request=request)

        except ClosedPoolError as e:
            raise ConnectionError(e, request=request)

        except _ProxyError as e:
            raise ProxyError(e)

        except (_SSLError, _HTTPError) as e:
            if isinstance(e, _SSLError):
                # This branch is for urllib3 versions earlier than v1.22
                raise SSLError(e, request=request)
            elif isinstance(e, ReadTimeoutError):
                raise ReadTimeout(e, request=request)
            elif isinstance(e, _InvalidHeader):
                raise InvalidHeader(e, request=request)
            else:
                raise

        return self.build_response(request, resp)

"""
module_dict["requests"+os.sep+"models.py"]="""
# < include 'urllib3.py' >

# < include 'idna.py' >

\"\"\"
requests.models
~~~~~~~~~~~~~~~

This module contains the primary objects that power Requests.
\"\"\"

import datetime

# Import encoding now, to avoid implicit import later.
# Implicit import within threads may cause LookupError when standard library is in a ZIP,
# such as in Embedded Python. See https://github.com/psf/requests/issues/3578.
import encodings.idna  # noqa: F401
from io import UnsupportedOperation

from urllib3.exceptions import (
    DecodeError,
    LocationParseError,
    ProtocolError,
    ReadTimeoutError,
    SSLError,
)
from urllib3.fields import RequestField
from urllib3.filepost import encode_multipart_formdata
from urllib3.util import parse_url

from ._internal_utils import to_native_string, unicode_is_ascii
from .auth import HTTPBasicAuth
from .compat import (
    Callable,
    JSONDecodeError,
    Mapping,
    basestring,
    builtin_str,
    chardet,
    cookielib,
)
from .compat import json as complexjson
from .compat import urlencode, urlsplit, urlunparse
from .cookies import _copy_cookie_jar, cookiejar_from_dict, get_cookie_header
from .exceptions import (
    ChunkedEncodingError,
    ConnectionError,
    ContentDecodingError,
    HTTPError,
    InvalidJSONError,
    InvalidURL,
)
from .exceptions import JSONDecodeError as RequestsJSONDecodeError
from .exceptions import MissingSchema
from .exceptions import SSLError as RequestsSSLError
from .exceptions import StreamConsumedError
from .hooks import default_hooks
from .status_codes import codes
from .structures import CaseInsensitiveDict
from .utils import (
    check_header_validity,
    get_auth_from_url,
    guess_filename,
    guess_json_utf,
    iter_slices,
    parse_header_links,
    requote_uri,
    stream_decode_response_unicode,
    super_len,
    to_key_val_list,
)

#: The set of HTTP status codes that indicate an automatically
#: processable redirect.
REDIRECT_STATI = (
    codes.moved,  # 301
    codes.found,  # 302
    codes.other,  # 303
    codes.temporary_redirect,  # 307
    codes.permanent_redirect,  # 308
)

DEFAULT_REDIRECT_LIMIT = 30
CONTENT_CHUNK_SIZE = 10 * 1024
ITER_CHUNK_SIZE = 512


class RequestEncodingMixin:
    @property
    def path_url(self):
        \"\"\"Build the path URL to use.\"\"\"

        url = []

        p = urlsplit(self.url)

        path = p.path
        if not path:
            path = \"/\"

        url.append(path)

        query = p.query
        if query:
            url.append(\"?\")
            url.append(query)

        return \"\".join(url)

    @staticmethod
    def _encode_params(data):
        \"\"\"Encode parameters in a piece of data.

        Will successfully encode parameters when passed as a dict or a list of
        2-tuples. Order is retained if data is a list of 2-tuples but arbitrary
        if parameters are supplied as a dict.
        \"\"\"

        if isinstance(data, (str, bytes)):
            return data
        elif hasattr(data, \"read\"):
            return data
        elif hasattr(data, \"__iter__\"):
            result = []
            for k, vs in to_key_val_list(data):
                if isinstance(vs, basestring) or not hasattr(vs, \"__iter__\"):
                    vs = [vs]
                for v in vs:
                    if v is not None:
                        result.append(
                            (
                                k.encode(\"utf-8\") if isinstance(k, str) else k,
                                v.encode(\"utf-8\") if isinstance(v, str) else v,
                            )
                        )
            return urlencode(result, doseq=True)
        else:
            return data

    @staticmethod
    def _encode_files(files, data):
        \"\"\"Build the body for a multipart/form-data request.

        Will successfully encode files when passed as a dict or a list of
        tuples. Order is retained if data is a list of tuples but arbitrary
        if parameters are supplied as a dict.
        The tuples may be 2-tuples (filename, fileobj), 3-tuples (filename, fileobj, contentype)
        or 4-tuples (filename, fileobj, contentype, custom_headers).
        \"\"\"
        if not files:
            raise ValueError(\"Files must be provided.\")
        elif isinstance(data, basestring):
            raise ValueError(\"Data must not be a string.\")

        new_fields = []
        fields = to_key_val_list(data or {})
        files = to_key_val_list(files or {})

        for field, val in fields:
            if isinstance(val, basestring) or not hasattr(val, \"__iter__\"):
                val = [val]
            for v in val:
                if v is not None:
                    # Don't call str() on bytestrings: in Py3 it all goes wrong.
                    if not isinstance(v, bytes):
                        v = str(v)

                    new_fields.append(
                        (
                            field.decode(\"utf-8\")
                            if isinstance(field, bytes)
                            else field,
                            v.encode(\"utf-8\") if isinstance(v, str) else v,
                        )
                    )

        for (k, v) in files:
            # support for explicit filename
            ft = None
            fh = None
            if isinstance(v, (tuple, list)):
                if len(v) == 2:
                    fn, fp = v
                elif len(v) == 3:
                    fn, fp, ft = v
                else:
                    fn, fp, ft, fh = v
            else:
                fn = guess_filename(v) or k
                fp = v

            if isinstance(fp, (str, bytes, bytearray)):
                fdata = fp
            elif hasattr(fp, \"read\"):
                fdata = fp.read()
            elif fp is None:
                continue
            else:
                fdata = fp

            rf = RequestField(name=k, data=fdata, filename=fn, headers=fh)
            rf.make_multipart(content_type=ft)
            new_fields.append(rf)

        body, content_type = encode_multipart_formdata(new_fields)

        return body, content_type


class RequestHooksMixin:
    def register_hook(self, event, hook):
        \"\"\"Properly register a hook.\"\"\"

        if event not in self.hooks:
            raise ValueError(f'Unsupported event specified, with event name \"{event}\"')

        if isinstance(hook, Callable):
            self.hooks[event].append(hook)
        elif hasattr(hook, \"__iter__\"):
            self.hooks[event].extend(h for h in hook if isinstance(h, Callable))

    def deregister_hook(self, event, hook):
        \"\"\"Deregister a previously registered hook.
        Returns True if the hook existed, False if not.
        \"\"\"

        try:
            self.hooks[event].remove(hook)
            return True
        except ValueError:
            return False


class Request(RequestHooksMixin):
    \"\"\"A user-created :class:`Request <Request>` object.

    Used to prepare a :class:`PreparedRequest <PreparedRequest>`, which is sent to the server.

    :param method: HTTP method to use.
    :param url: URL to send.
    :param headers: dictionary of headers to send.
    :param files: dictionary of {filename: fileobject} files to multipart upload.
    :param data: the body to attach to the request. If a dictionary or
        list of tuples ``[(key, value)]`` is provided, form-encoding will
        take place.
    :param json: json for the body to attach to the request (if files or data is not specified).
    :param params: URL parameters to append to the URL. If a dictionary or
        list of tuples ``[(key, value)]`` is provided, form-encoding will
        take place.
    :param auth: Auth handler or (user, pass) tuple.
    :param cookies: dictionary or CookieJar of cookies to attach to this request.
    :param hooks: dictionary of callback hooks, for internal usage.

    Usage::

      >>> import requests
      >>> req = requests.Request('GET', 'https://httpbin.org/get')
      >>> req.prepare()
      <PreparedRequest [GET]>
    \"\"\"

    def __init__(
        self,
        method=None,
        url=None,
        headers=None,
        files=None,
        data=None,
        params=None,
        auth=None,
        cookies=None,
        hooks=None,
        json=None,
    ):

        # Default empty dicts for dict params.
        data = [] if data is None else data
        files = [] if files is None else files
        headers = {} if headers is None else headers
        params = {} if params is None else params
        hooks = {} if hooks is None else hooks

        self.hooks = default_hooks()
        for (k, v) in list(hooks.items()):
            self.register_hook(event=k, hook=v)

        self.method = method
        self.url = url
        self.headers = headers
        self.files = files
        self.data = data
        self.json = json
        self.params = params
        self.auth = auth
        self.cookies = cookies

    def __repr__(self):
        return f\"<Request [{self.method}]>\"

    def prepare(self):
        \"\"\"Constructs a :class:`PreparedRequest <PreparedRequest>` for transmission and returns it.\"\"\"
        p = PreparedRequest()
        p.prepare(
            method=self.method,
            url=self.url,
            headers=self.headers,
            files=self.files,
            data=self.data,
            json=self.json,
            params=self.params,
            auth=self.auth,
            cookies=self.cookies,
            hooks=self.hooks,
        )
        return p


class PreparedRequest(RequestEncodingMixin, RequestHooksMixin):
    \"\"\"The fully mutable :class:`PreparedRequest <PreparedRequest>` object,
    containing the exact bytes that will be sent to the server.

    Instances are generated from a :class:`Request <Request>` object, and
    should not be instantiated manually; doing so may produce undesirable
    effects.

    Usage::

      >>> import requests
      >>> req = requests.Request('GET', 'https://httpbin.org/get')
      >>> r = req.prepare()
      >>> r
      <PreparedRequest [GET]>

      >>> s = requests.Session()
      >>> s.send(r)
      <Response [200]>
    \"\"\"

    def __init__(self):
        #: HTTP verb to send to the server.
        self.method = None
        #: HTTP URL to send the request to.
        self.url = None
        #: dictionary of HTTP headers.
        self.headers = None
        # The `CookieJar` used to create the Cookie header will be stored here
        # after prepare_cookies is called
        self._cookies = None
        #: request body to send to the server.
        self.body = None
        #: dictionary of callback hooks, for internal usage.
        self.hooks = default_hooks()
        #: integer denoting starting position of a readable file-like body.
        self._body_position = None

    def prepare(
        self,
        method=None,
        url=None,
        headers=None,
        files=None,
        data=None,
        params=None,
        auth=None,
        cookies=None,
        hooks=None,
        json=None,
    ):
        \"\"\"Prepares the entire request with the given parameters.\"\"\"

        self.prepare_method(method)
        self.prepare_url(url, params)
        self.prepare_headers(headers)
        self.prepare_cookies(cookies)
        self.prepare_body(data, files, json)
        self.prepare_auth(auth, url)

        # Note that prepare_auth must be last to enable authentication schemes
        # such as OAuth to work on a fully prepared request.

        # This MUST go after prepare_auth. Authenticators could add a hook
        self.prepare_hooks(hooks)

    def __repr__(self):
        return f\"<PreparedRequest [{self.method}]>\"

    def copy(self):
        p = PreparedRequest()
        p.method = self.method
        p.url = self.url
        p.headers = self.headers.copy() if self.headers is not None else None
        p._cookies = _copy_cookie_jar(self._cookies)
        p.body = self.body
        p.hooks = self.hooks
        p._body_position = self._body_position
        return p

    def prepare_method(self, method):
        \"\"\"Prepares the given HTTP method.\"\"\"
        self.method = method
        if self.method is not None:
            self.method = to_native_string(self.method.upper())

    @staticmethod
    def _get_idna_encoded_host(host):
        import idna

        try:
            host = idna.encode(host, uts46=True).decode(\"utf-8\")
        except idna.IDNAError:
            raise UnicodeError
        return host

    def prepare_url(self, url, params):
        \"\"\"Prepares the given HTTP URL.\"\"\"
        #: Accept objects that have string representations.
        #: We're unable to blindly call unicode/str functions
        #: as this will include the bytestring indicator (b'')
        #: on python 3.x.
        #: https://github.com/psf/requests/pull/2238
        if isinstance(url, bytes):
            url = url.decode(\"utf8\")
        else:
            url = str(url)

        # Remove leading whitespaces from url
        url = url.lstrip()

        # Don't do any URL preparation for non-HTTP schemes like `mailto`,
        # `data` etc to work around exceptions from `url_parse`, which
        # handles RFC 3986 only.
        if \":\" in url and not url.lower().startswith(\"http\"):
            self.url = url
            return

        # Support for unicode domain names and paths.
        try:
            scheme, auth, host, port, path, query, fragment = parse_url(url)
        except LocationParseError as e:
            raise InvalidURL(*e.args)

        if not scheme:
            raise MissingSchema(
                f\"Invalid URL {url!r}: No scheme supplied. \"
                f\"Perhaps you meant http://{url}?\"
            )

        if not host:
            raise InvalidURL(f\"Invalid URL {url!r}: No host supplied\")

        # In general, we want to try IDNA encoding the hostname if the string contains
        # non-ASCII characters. This allows users to automatically get the correct IDNA
        # behaviour. For strings containing only ASCII characters, we need to also verify
        # it doesn't start with a wildcard (*), before allowing the unencoded hostname.
        if not unicode_is_ascii(host):
            try:
                host = self._get_idna_encoded_host(host)
            except UnicodeError:
                raise InvalidURL(\"URL has an invalid label.\")
        elif host.startswith((\"*\", \".\")):
            raise InvalidURL(\"URL has an invalid label.\")

        # Carefully reconstruct the network location
        netloc = auth or \"\"
        if netloc:
            netloc += \"@\"
        netloc += host
        if port:
            netloc += f\":{port}\"

        # Bare domains aren't valid URLs.
        if not path:
            path = \"/\"

        if isinstance(params, (str, bytes)):
            params = to_native_string(params)

        enc_params = self._encode_params(params)
        if enc_params:
            if query:
                query = f\"{query}&{enc_params}\"
            else:
                query = enc_params

        url = requote_uri(urlunparse([scheme, netloc, path, None, query, fragment]))
        self.url = url

    def prepare_headers(self, headers):
        \"\"\"Prepares the given HTTP headers.\"\"\"

        self.headers = CaseInsensitiveDict()
        if headers:
            for header in headers.items():
                # Raise exception on invalid header value.
                check_header_validity(header)
                name, value = header
                self.headers[to_native_string(name)] = value

    def prepare_body(self, data, files, json=None):
        \"\"\"Prepares the given HTTP body data.\"\"\"

        # Check if file, fo, generator, iterator.
        # If not, run through normal process.

        # Nottin' on you.
        body = None
        content_type = None

        if not data and json is not None:
            # urllib3 requires a bytes-like body. Python 2's json.dumps
            # provides this natively, but Python 3 gives a Unicode string.
            content_type = \"application/json\"

            try:
                body = complexjson.dumps(json, allow_nan=False)
            except ValueError as ve:
                raise InvalidJSONError(ve, request=self)

            if not isinstance(body, bytes):
                body = body.encode(\"utf-8\")

        is_stream = all(
            [
                hasattr(data, \"__iter__\"),
                not isinstance(data, (basestring, list, tuple, Mapping)),
            ]
        )

        if is_stream:
            try:
                length = super_len(data)
            except (TypeError, AttributeError, UnsupportedOperation):
                length = None

            body = data

            if getattr(body, \"tell\", None) is not None:
                # Record the current file position before reading.
                # This will allow us to rewind a file in the event
                # of a redirect.
                try:
                    self._body_position = body.tell()
                except OSError:
                    # This differentiates from None, allowing us to catch
                    # a failed `tell()` later when trying to rewind the body
                    self._body_position = object()

            if files:
                raise NotImplementedError(
                    \"Streamed bodies and files are mutually exclusive.\"
                )

            if length:
                self.headers[\"Content-Length\"] = builtin_str(length)
            else:
                self.headers[\"Transfer-Encoding\"] = \"chunked\"
        else:
            # Multi-part file uploads.
            if files:
                (body, content_type) = self._encode_files(files, data)
            else:
                if data:
                    body = self._encode_params(data)
                    if isinstance(data, basestring) or hasattr(data, \"read\"):
                        content_type = None
                    else:
                        content_type = \"application/x-www-form-urlencoded\"

            self.prepare_content_length(body)

            # Add content-type if it wasn't explicitly provided.
            if content_type and (\"content-type\" not in self.headers):
                self.headers[\"Content-Type\"] = content_type

        self.body = body

    def prepare_content_length(self, body):
        \"\"\"Prepare Content-Length header based on request method and body\"\"\"
        if body is not None:
            length = super_len(body)
            if length:
                # If length exists, set it. Otherwise, we fallback
                # to Transfer-Encoding: chunked.
                self.headers[\"Content-Length\"] = builtin_str(length)
        elif (
            self.method not in (\"GET\", \"HEAD\")
            and self.headers.get(\"Content-Length\") is None
        ):
            # Set Content-Length to 0 for methods that can have a body
            # but don't provide one. (i.e. not GET or HEAD)
            self.headers[\"Content-Length\"] = \"0\"

    def prepare_auth(self, auth, url=\"\"):
        \"\"\"Prepares the given HTTP auth data.\"\"\"

        # If no Auth is explicitly provided, extract it from the URL first.
        if auth is None:
            url_auth = get_auth_from_url(self.url)
            auth = url_auth if any(url_auth) else None

        if auth:
            if isinstance(auth, tuple) and len(auth) == 2:
                # special-case basic HTTP auth
                auth = HTTPBasicAuth(*auth)

            # Allow auth to make its changes.
            r = auth(self)

            # Update self to reflect the auth changes.
            self.__dict__.update(r.__dict__)

            # Recompute Content-Length
            self.prepare_content_length(self.body)

    def prepare_cookies(self, cookies):
        \"\"\"Prepares the given HTTP cookie data.

        This function eventually generates a ``Cookie`` header from the
        given cookies using cookielib. Due to cookielib's design, the header
        will not be regenerated if it already exists, meaning this function
        can only be called once for the life of the
        :class:`PreparedRequest <PreparedRequest>` object. Any subsequent calls
        to ``prepare_cookies`` will have no actual effect, unless the \"Cookie\"
        header is removed beforehand.
        \"\"\"
        if isinstance(cookies, cookielib.CookieJar):
            self._cookies = cookies
        else:
            self._cookies = cookiejar_from_dict(cookies)

        cookie_header = get_cookie_header(self._cookies, self)
        if cookie_header is not None:
            self.headers[\"Cookie\"] = cookie_header

    def prepare_hooks(self, hooks):
        \"\"\"Prepares the given hooks.\"\"\"
        # hooks can be passed as None to the prepare method and to this
        # method. To prevent iterating over None, simply use an empty list
        # if hooks is False-y
        hooks = hooks or []
        for event in hooks:
            self.register_hook(event, hooks[event])


class Response:
    \"\"\"The :class:`Response <Response>` object, which contains a
    server's response to an HTTP request.
    \"\"\"

    __attrs__ = [
        \"_content\",
        \"status_code\",
        \"headers\",
        \"url\",
        \"history\",
        \"encoding\",
        \"reason\",
        \"cookies\",
        \"elapsed\",
        \"request\",
    ]

    def __init__(self):
        self._content = False
        self._content_consumed = False
        self._next = None

        #: Integer Code of responded HTTP Status, e.g. 404 or 200.
        self.status_code = None

        #: Case-insensitive Dictionary of Response Headers.
        #: For example, ``headers['content-encoding']`` will return the
        #: value of a ``'Content-Encoding'`` response header.
        self.headers = CaseInsensitiveDict()

        #: File-like object representation of response (for advanced usage).
        #: Use of ``raw`` requires that ``stream=True`` be set on the request.
        #: This requirement does not apply for use internally to Requests.
        self.raw = None

        #: Final URL location of Response.
        self.url = None

        #: Encoding to decode with when accessing r.text.
        self.encoding = None

        #: A list of :class:`Response <Response>` objects from
        #: the history of the Request. Any redirect responses will end
        #: up here. The list is sorted from the oldest to the most recent request.
        self.history = []

        #: Textual reason of responded HTTP Status, e.g. \"Not Found\" or \"OK\".
        self.reason = None

        #: A CookieJar of Cookies the server sent back.
        self.cookies = cookiejar_from_dict({})

        #: The amount of time elapsed between sending the request
        #: and the arrival of the response (as a timedelta).
        #: This property specifically measures the time taken between sending
        #: the first byte of the request and finishing parsing the headers. It
        #: is therefore unaffected by consuming the response content or the
        #: value of the ``stream`` keyword argument.
        self.elapsed = datetime.timedelta(0)

        #: The :class:`PreparedRequest <PreparedRequest>` object to which this
        #: is a response.
        self.request = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def __getstate__(self):
        # Consume everything; accessing the content attribute makes
        # sure the content has been fully read.
        if not self._content_consumed:
            self.content

        return {attr: getattr(self, attr, None) for attr in self.__attrs__}

    def __setstate__(self, state):
        for name, value in state.items():
            setattr(self, name, value)

        # pickled objects do not have .raw
        setattr(self, \"_content_consumed\", True)
        setattr(self, \"raw\", None)

    def __repr__(self):
        return f\"<Response [{self.status_code}]>\"

    def __bool__(self):
        \"\"\"Returns True if :attr:`status_code` is less than 400.

        This attribute checks if the status code of the response is between
        400 and 600 to see if there was a client error or a server error. If
        the status code, is between 200 and 400, this will return True. This
        is **not** a check to see if the response code is ``200 OK``.
        \"\"\"
        return self.ok

    def __nonzero__(self):
        \"\"\"Returns True if :attr:`status_code` is less than 400.

        This attribute checks if the status code of the response is between
        400 and 600 to see if there was a client error or a server error. If
        the status code, is between 200 and 400, this will return True. This
        is **not** a check to see if the response code is ``200 OK``.
        \"\"\"
        return self.ok

    def __iter__(self):
        \"\"\"Allows you to use a response as an iterator.\"\"\"
        return self.iter_content(128)

    @property
    def ok(self):
        \"\"\"Returns True if :attr:`status_code` is less than 400, False if not.

        This attribute checks if the status code of the response is between
        400 and 600 to see if there was a client error or a server error. If
        the status code is between 200 and 400, this will return True. This
        is **not** a check to see if the response code is ``200 OK``.
        \"\"\"
        try:
            self.raise_for_status()
        except HTTPError:
            return False
        return True

    @property
    def is_redirect(self):
        \"\"\"True if this Response is a well-formed HTTP redirect that could have
        been processed automatically (by :meth:`Session.resolve_redirects`).
        \"\"\"
        return \"location\" in self.headers and self.status_code in REDIRECT_STATI

    @property
    def is_permanent_redirect(self):
        \"\"\"True if this Response one of the permanent versions of redirect.\"\"\"
        return \"location\" in self.headers and self.status_code in (
            codes.moved_permanently,
            codes.permanent_redirect,
        )

    @property
    def next(self):
        \"\"\"Returns a PreparedRequest for the next request in a redirect chain, if there is one.\"\"\"
        return self._next

    @property
    def apparent_encoding(self):
        \"\"\"The apparent encoding, provided by the charset_normalizer or chardet libraries.\"\"\"
        return chardet.detect(self.content)[\"encoding\"]

    def iter_content(self, chunk_size=1, decode_unicode=False):
        \"\"\"Iterates over the response data.  When stream=True is set on the
        request, this avoids reading the content at once into memory for
        large responses.  The chunk size is the number of bytes it should
        read into memory.  This is not necessarily the length of each item
        returned as decoding can take place.

        chunk_size must be of type int or None. A value of None will
        function differently depending on the value of `stream`.
        stream=True will read data as it arrives in whatever size the
        chunks are received. If stream=False, data is returned as
        a single chunk.

        If decode_unicode is True, content will be decoded using the best
        available encoding based on the response.
        \"\"\"

        def generate():
            # Special case for urllib3.
            if hasattr(self.raw, \"stream\"):
                try:
                    yield from self.raw.stream(chunk_size, decode_content=True)
                except ProtocolError as e:
                    raise ChunkedEncodingError(e)
                except DecodeError as e:
                    raise ContentDecodingError(e)
                except ReadTimeoutError as e:
                    raise ConnectionError(e)
                except SSLError as e:
                    raise RequestsSSLError(e)
            else:
                # Standard file-like object.
                while True:
                    chunk = self.raw.read(chunk_size)
                    if not chunk:
                        break
                    yield chunk

            self._content_consumed = True

        if self._content_consumed and isinstance(self._content, bool):
            raise StreamConsumedError()
        elif chunk_size is not None and not isinstance(chunk_size, int):
            raise TypeError(
                f\"chunk_size must be an int, it is instead a {type(chunk_size)}.\"
            )
        # simulate reading small chunks of the content
        reused_chunks = iter_slices(self._content, chunk_size)

        stream_chunks = generate()

        chunks = reused_chunks if self._content_consumed else stream_chunks

        if decode_unicode:
            chunks = stream_decode_response_unicode(chunks, self)

        return chunks

    def iter_lines(
        self, chunk_size=ITER_CHUNK_SIZE, decode_unicode=False, delimiter=None
    ):
        \"\"\"Iterates over the response data, one line at a time.  When
        stream=True is set on the request, this avoids reading the
        content at once into memory for large responses.

        .. note:: This method is not reentrant safe.
        \"\"\"

        pending = None

        for chunk in self.iter_content(
            chunk_size=chunk_size, decode_unicode=decode_unicode
        ):

            if pending is not None:
                chunk = pending + chunk

            if delimiter:
                lines = chunk.split(delimiter)
            else:
                lines = chunk.splitlines()

            if lines and lines[-1] and chunk and lines[-1][-1] == chunk[-1]:
                pending = lines.pop()
            else:
                pending = None

            yield from lines

        if pending is not None:
            yield pending

    @property
    def content(self):
        \"\"\"Content of the response, in bytes.\"\"\"

        if self._content is False:
            # Read the contents.
            if self._content_consumed:
                raise RuntimeError(\"The content for this response was already consumed\")

            if self.status_code == 0 or self.raw is None:
                self._content = None
            else:
                self._content = b\"\".join(self.iter_content(CONTENT_CHUNK_SIZE)) or b\"\"

        self._content_consumed = True
        # don't need to release the connection; that's been handled by urllib3
        # since we exhausted the data.
        return self._content

    @property
    def text(self):
        \"\"\"Content of the response, in unicode.

        If Response.encoding is None, encoding will be guessed using
        ``charset_normalizer`` or ``chardet``.

        The encoding of the response content is determined based solely on HTTP
        headers, following RFC 2616 to the letter. If you can take advantage of
        non-HTTP knowledge to make a better guess at the encoding, you should
        set ``r.encoding`` appropriately before accessing this property.
        \"\"\"

        # Try charset from content-type
        content = None
        encoding = self.encoding

        if not self.content:
            return \"\"

        # Fallback to auto-detected encoding.
        if self.encoding is None:
            encoding = self.apparent_encoding

        # Decode unicode from given encoding.
        try:
            content = str(self.content, encoding, errors=\"replace\")
        except (LookupError, TypeError):
            # A LookupError is raised if the encoding was not found which could
            # indicate a misspelling or similar mistake.
            #
            # A TypeError can be raised if encoding is None
            #
            # So we try blindly encoding.
            content = str(self.content, errors=\"replace\")

        return content

    def json(self, **kwargs):
        r\"\"\"Returns the json-encoded content of a response, if any.

        :param \\*\\*kwargs: Optional arguments that ``json.loads`` takes.
        :raises requests.exceptions.JSONDecodeError: If the response body does not
            contain valid json.
        \"\"\"

        if not self.encoding and self.content and len(self.content) > 3:
            # No encoding set. JSON RFC 4627 section 3 states we should expect
            # UTF-8, -16 or -32. Detect which one to use; If the detection or
            # decoding fails, fall back to `self.text` (using charset_normalizer to make
            # a best guess).
            encoding = guess_json_utf(self.content)
            if encoding is not None:
                try:
                    return complexjson.loads(self.content.decode(encoding), **kwargs)
                except UnicodeDecodeError:
                    # Wrong UTF codec detected; usually because it's not UTF-8
                    # but some other 8-bit codec.  This is an RFC violation,
                    # and the server didn't bother to tell us what codec *was*
                    # used.
                    pass
                except JSONDecodeError as e:
                    raise RequestsJSONDecodeError(e.msg, e.doc, e.pos)

        try:
            return complexjson.loads(self.text, **kwargs)
        except JSONDecodeError as e:
            # Catch JSON-related errors and raise as requests.JSONDecodeError
            # This aliases json.JSONDecodeError and simplejson.JSONDecodeError
            raise RequestsJSONDecodeError(e.msg, e.doc, e.pos)

    @property
    def links(self):
        \"\"\"Returns the parsed header links of the response, if any.\"\"\"

        header = self.headers.get(\"link\")

        resolved_links = {}

        if header:
            links = parse_header_links(header)

            for link in links:
                key = link.get(\"rel\") or link.get(\"url\")
                resolved_links[key] = link

        return resolved_links

    def raise_for_status(self):
        \"\"\"Raises :class:`HTTPError`, if one occurred.\"\"\"

        http_error_msg = \"\"
        if isinstance(self.reason, bytes):
            # We attempt to decode utf-8 first because some servers
            # choose to localize their reason strings. If the string
            # isn't utf-8, we fall back to iso-8859-1 for all other
            # encodings. (See PR #3538)
            try:
                reason = self.reason.decode(\"utf-8\")
            except UnicodeDecodeError:
                reason = self.reason.decode(\"iso-8859-1\")
        else:
            reason = self.reason

        if 400 <= self.status_code < 500:
            http_error_msg = (
                f\"{self.status_code} Client Error: {reason} for url: {self.url}\"
            )

        elif 500 <= self.status_code < 600:
            http_error_msg = (
                f\"{self.status_code} Server Error: {reason} for url: {self.url}\"
            )

        if http_error_msg:
            raise HTTPError(http_error_msg, response=self)

    def close(self):
        \"\"\"Releases the connection back to the pool. Once this method has been
        called the underlying ``raw`` object must not be accessed again.

        *Note: Should not normally need to be called explicitly.*
        \"\"\"
        if not self._content_consumed:
            self.raw.close()

        release_conn = getattr(self.raw, \"release_conn\", None)
        if release_conn is not None:
            release_conn()

"""
module_dict["requests"+os.sep+"api.py"]="""
\"\"\"
requests.api
~~~~~~~~~~~~

This module implements the Requests API.

:copyright: (c) 2012 by Kenneth Reitz.
:license: Apache2, see LICENSE for more details.
\"\"\"

from . import sessions


def request(method, url, **kwargs):
    \"\"\"Constructs and sends a :class:`Request <Request>`.

    :param method: method for the new :class:`Request` object: ``GET``, ``OPTIONS``, ``HEAD``, ``POST``, ``PUT``, ``PATCH``, or ``DELETE``.
    :param url: URL for the new :class:`Request` object.
    :param params: (optional) Dictionary, list of tuples or bytes to send
        in the query string for the :class:`Request`.
    :param data: (optional) Dictionary, list of tuples, bytes, or file-like
        object to send in the body of the :class:`Request`.
    :param json: (optional) A JSON serializable Python object to send in the body of the :class:`Request`.
    :param headers: (optional) Dictionary of HTTP Headers to send with the :class:`Request`.
    :param cookies: (optional) Dict or CookieJar object to send with the :class:`Request`.
    :param files: (optional) Dictionary of ``'name': file-like-objects`` (or ``{'name': file-tuple}``) for multipart encoding upload.
        ``file-tuple`` can be a 2-tuple ``('filename', fileobj)``, 3-tuple ``('filename', fileobj, 'content_type')``
        or a 4-tuple ``('filename', fileobj, 'content_type', custom_headers)``, where ``'content-type'`` is a string
        defining the content type of the given file and ``custom_headers`` a dict-like object containing additional headers
        to add for the file.
    :param auth: (optional) Auth tuple to enable Basic/Digest/Custom HTTP Auth.
    :param timeout: (optional) How many seconds to wait for the server to send data
        before giving up, as a float, or a :ref:`(connect timeout, read
        timeout) <timeouts>` tuple.
    :type timeout: float or tuple
    :param allow_redirects: (optional) Boolean. Enable/disable GET/OPTIONS/POST/PUT/PATCH/DELETE/HEAD redirection. Defaults to ``True``.
    :type allow_redirects: bool
    :param proxies: (optional) Dictionary mapping protocol to the URL of the proxy.
    :param verify: (optional) Either a boolean, in which case it controls whether we verify
            the server's TLS certificate, or a string, in which case it must be a path
            to a CA bundle to use. Defaults to ``True``.
    :param stream: (optional) if ``False``, the response content will be immediately downloaded.
    :param cert: (optional) if String, path to ssl client cert file (.pem). If Tuple, ('cert', 'key') pair.
    :return: :class:`Response <Response>` object
    :rtype: requests.Response

    Usage::

      >>> import requests
      >>> req = requests.request('GET', 'https://httpbin.org/get')
      >>> req
      <Response [200]>
    \"\"\"

    # By using the 'with' statement we are sure the session is closed, thus we
    # avoid leaving sockets open which can trigger a ResourceWarning in some
    # cases, and look like a memory leak in others.
    with sessions.Session() as session:
        return session.request(method=method, url=url, **kwargs)


def get(url, params=None, **kwargs):
    r\"\"\"Sends a GET request.

    :param url: URL for the new :class:`Request` object.
    :param params: (optional) Dictionary, list of tuples or bytes to send
        in the query string for the :class:`Request`.
    :param \\*\\*kwargs: Optional arguments that ``request`` takes.
    :return: :class:`Response <Response>` object
    :rtype: requests.Response
    \"\"\"

    return request(\"get\", url, params=params, **kwargs)


def options(url, **kwargs):
    r\"\"\"Sends an OPTIONS request.

    :param url: URL for the new :class:`Request` object.
    :param \\*\\*kwargs: Optional arguments that ``request`` takes.
    :return: :class:`Response <Response>` object
    :rtype: requests.Response
    \"\"\"

    return request(\"options\", url, **kwargs)


def head(url, **kwargs):
    r\"\"\"Sends a HEAD request.

    :param url: URL for the new :class:`Request` object.
    :param \\*\\*kwargs: Optional arguments that ``request`` takes. If
        `allow_redirects` is not provided, it will be set to `False` (as
        opposed to the default :meth:`request` behavior).
    :return: :class:`Response <Response>` object
    :rtype: requests.Response
    \"\"\"

    kwargs.setdefault(\"allow_redirects\", False)
    return request(\"head\", url, **kwargs)


def post(url, data=None, json=None, **kwargs):
    r\"\"\"Sends a POST request.

    :param url: URL for the new :class:`Request` object.
    :param data: (optional) Dictionary, list of tuples, bytes, or file-like
        object to send in the body of the :class:`Request`.
    :param json: (optional) json data to send in the body of the :class:`Request`.
    :param \\*\\*kwargs: Optional arguments that ``request`` takes.
    :return: :class:`Response <Response>` object
    :rtype: requests.Response
    \"\"\"

    return request(\"post\", url, data=data, json=json, **kwargs)


def put(url, data=None, **kwargs):
    r\"\"\"Sends a PUT request.

    :param url: URL for the new :class:`Request` object.
    :param data: (optional) Dictionary, list of tuples, bytes, or file-like
        object to send in the body of the :class:`Request`.
    :param json: (optional) json data to send in the body of the :class:`Request`.
    :param \\*\\*kwargs: Optional arguments that ``request`` takes.
    :return: :class:`Response <Response>` object
    :rtype: requests.Response
    \"\"\"

    return request(\"put\", url, data=data, **kwargs)


def patch(url, data=None, **kwargs):
    r\"\"\"Sends a PATCH request.

    :param url: URL for the new :class:`Request` object.
    :param data: (optional) Dictionary, list of tuples, bytes, or file-like
        object to send in the body of the :class:`Request`.
    :param json: (optional) json data to send in the body of the :class:`Request`.
    :param \\*\\*kwargs: Optional arguments that ``request`` takes.
    :return: :class:`Response <Response>` object
    :rtype: requests.Response
    \"\"\"

    return request(\"patch\", url, data=data, **kwargs)


def delete(url, **kwargs):
    r\"\"\"Sends a DELETE request.

    :param url: URL for the new :class:`Request` object.
    :param \\*\\*kwargs: Optional arguments that ``request`` takes.
    :return: :class:`Response <Response>` object
    :rtype: requests.Response
    \"\"\"

    return request(\"delete\", url, **kwargs)

"""
module_dict["requests"+os.sep+"certs.py"]="""
#!/usr/bin/env python

# < include 'certifi.py' >

\"\"\"
requests.certs
~~~~~~~~~~~~~~

This module returns the preferred default CA certificate bundle. There is
only one — the one from the certifi package.

If you are packaging Requests, e.g., for a Linux distribution or a managed
environment, you can change the definition of where() to return a separately
packaged CA bundle.
\"\"\"
from certifi import where

if __name__ == \"__main__\":
    print(where())

"""
module_dict["requests"+os.sep+"__version__.py"]="""
# .-. .-. .-. . . .-. .-. .-. .-.
# |(  |-  |.| | | |-  `-.  |  `-.
# ' ' `-' `-`.`-' `-' `-'  '  `-'

__title__ = \"requests\"
__description__ = \"Python HTTP for Humans.\"
__url__ = \"https://requests.readthedocs.io\"
__version__ = \"2.28.1\"
__build__ = 0x022801
__author__ = \"Kenneth Reitz\"
__author_email__ = \"me@kennethreitz.org\"
__license__ = \"Apache 2.0\"
__copyright__ = \"Copyright 2022 Kenneth Reitz\"
__cake__ = \"\\u2728 \\U0001f370 \\u2728\"

"""
module_dict["requests"+os.sep+"status_codes.py"]="""
r\"\"\"
The ``codes`` object defines a mapping from common names for HTTP statuses
to their numerical codes, accessible either as attributes or as dictionary
items.

Example::

    >>> import requests
    >>> requests.codes['temporary_redirect']
    307
    >>> requests.codes.teapot
    418
    >>> requests.codes['\\o/']
    200

Some codes have multiple names, and both upper- and lower-case versions of
the names are allowed. For example, ``codes.ok``, ``codes.OK``, and
``codes.okay`` all correspond to the HTTP status code 200.
\"\"\"

from .structures import LookupDict

_codes = {
    # Informational.
    100: (\"continue\",),
    101: (\"switching_protocols\",),
    102: (\"processing\",),
    103: (\"checkpoint\",),
    122: (\"uri_too_long\", \"request_uri_too_long\"),
    200: (\"ok\", \"okay\", \"all_ok\", \"all_okay\", \"all_good\", \"\\\\o/\", \"✓\"),
    201: (\"created\",),
    202: (\"accepted\",),
    203: (\"non_authoritative_info\", \"non_authoritative_information\"),
    204: (\"no_content\",),
    205: (\"reset_content\", \"reset\"),
    206: (\"partial_content\", \"partial\"),
    207: (\"multi_status\", \"multiple_status\", \"multi_stati\", \"multiple_stati\"),
    208: (\"already_reported\",),
    226: (\"im_used\",),
    # Redirection.
    300: (\"multiple_choices\",),
    301: (\"moved_permanently\", \"moved\", \"\\\\o-\"),
    302: (\"found\",),
    303: (\"see_other\", \"other\"),
    304: (\"not_modified\",),
    305: (\"use_proxy\",),
    306: (\"switch_proxy\",),
    307: (\"temporary_redirect\", \"temporary_moved\", \"temporary\"),
    308: (
        \"permanent_redirect\",
        \"resume_incomplete\",
        \"resume\",
    ),  # \"resume\" and \"resume_incomplete\" to be removed in 3.0
    # Client Error.
    400: (\"bad_request\", \"bad\"),
    401: (\"unauthorized\",),
    402: (\"payment_required\", \"payment\"),
    403: (\"forbidden\",),
    404: (\"not_found\", \"-o-\"),
    405: (\"method_not_allowed\", \"not_allowed\"),
    406: (\"not_acceptable\",),
    407: (\"proxy_authentication_required\", \"proxy_auth\", \"proxy_authentication\"),
    408: (\"request_timeout\", \"timeout\"),
    409: (\"conflict\",),
    410: (\"gone\",),
    411: (\"length_required\",),
    412: (\"precondition_failed\", \"precondition\"),
    413: (\"request_entity_too_large\",),
    414: (\"request_uri_too_large\",),
    415: (\"unsupported_media_type\", \"unsupported_media\", \"media_type\"),
    416: (
        \"requested_range_not_satisfiable\",
        \"requested_range\",
        \"range_not_satisfiable\",
    ),
    417: (\"expectation_failed\",),
    418: (\"im_a_teapot\", \"teapot\", \"i_am_a_teapot\"),
    421: (\"misdirected_request\",),
    422: (\"unprocessable_entity\", \"unprocessable\"),
    423: (\"locked\",),
    424: (\"failed_dependency\", \"dependency\"),
    425: (\"unordered_collection\", \"unordered\"),
    426: (\"upgrade_required\", \"upgrade\"),
    428: (\"precondition_required\", \"precondition\"),
    429: (\"too_many_requests\", \"too_many\"),
    431: (\"header_fields_too_large\", \"fields_too_large\"),
    444: (\"no_response\", \"none\"),
    449: (\"retry_with\", \"retry\"),
    450: (\"blocked_by_windows_parental_controls\", \"parental_controls\"),
    451: (\"unavailable_for_legal_reasons\", \"legal_reasons\"),
    499: (\"client_closed_request\",),
    # Server Error.
    500: (\"internal_server_error\", \"server_error\", \"/o\\\\\", \"✗\"),
    501: (\"not_implemented\",),
    502: (\"bad_gateway\",),
    503: (\"service_unavailable\", \"unavailable\"),
    504: (\"gateway_timeout\",),
    505: (\"http_version_not_supported\", \"http_version\"),
    506: (\"variant_also_negotiates\",),
    507: (\"insufficient_storage\",),
    509: (\"bandwidth_limit_exceeded\", \"bandwidth\"),
    510: (\"not_extended\",),
    511: (\"network_authentication_required\", \"network_auth\", \"network_authentication\"),
}

codes = LookupDict(name=\"status_codes\")


def _init():
    for code, titles in _codes.items():
        for title in titles:
            setattr(codes, title, code)
            if not title.startswith((\"\\\\\", \"/\")):
                setattr(codes, title.upper(), code)

    def doc(code):
        names = \", \".join(f\"``{n}``\" for n in _codes[code])
        return \"* %d: %s\" % (code, names)

    global __doc__
    __doc__ = (
        __doc__ + \"\\n\" + \"\\n\".join(doc(code) for code in sorted(_codes))
        if __doc__ is not None
        else None
    )


_init()

"""
module_dict["requests"+os.sep+"exceptions.py"]="""
# < include 'urllib3.py' >

\"\"\"
requests.exceptions
~~~~~~~~~~~~~~~~~~~

This module contains the set of Requests' exceptions.
\"\"\"
from urllib3.exceptions import HTTPError as BaseHTTPError

from .compat import JSONDecodeError as CompatJSONDecodeError


class RequestException(IOError):
    \"\"\"There was an ambiguous exception that occurred while handling your
    request.
    \"\"\"

    def __init__(self, *args, **kwargs):
        \"\"\"Initialize RequestException with `request` and `response` objects.\"\"\"
        response = kwargs.pop(\"response\", None)
        self.response = response
        self.request = kwargs.pop(\"request\", None)
        if response is not None and not self.request and hasattr(response, \"request\"):
            self.request = self.response.request
        super().__init__(*args, **kwargs)


class InvalidJSONError(RequestException):
    \"\"\"A JSON error occurred.\"\"\"


class JSONDecodeError(InvalidJSONError, CompatJSONDecodeError):
    \"\"\"Couldn't decode the text into json\"\"\"

    def __init__(self, *args, **kwargs):
        \"\"\"
        Construct the JSONDecodeError instance first with all
        args. Then use it's args to construct the IOError so that
        the json specific args aren't used as IOError specific args
        and the error message from JSONDecodeError is preserved.
        \"\"\"
        CompatJSONDecodeError.__init__(self, *args)
        InvalidJSONError.__init__(self, *self.args, **kwargs)


class HTTPError(RequestException):
    \"\"\"An HTTP error occurred.\"\"\"


class ConnectionError(RequestException):
    \"\"\"A Connection error occurred.\"\"\"


class ProxyError(ConnectionError):
    \"\"\"A proxy error occurred.\"\"\"


class SSLError(ConnectionError):
    \"\"\"An SSL error occurred.\"\"\"


class Timeout(RequestException):
    \"\"\"The request timed out.

    Catching this error will catch both
    :exc:`~requests.exceptions.ConnectTimeout` and
    :exc:`~requests.exceptions.ReadTimeout` errors.
    \"\"\"


class ConnectTimeout(ConnectionError, Timeout):
    \"\"\"The request timed out while trying to connect to the remote server.

    Requests that produced this error are safe to retry.
    \"\"\"


class ReadTimeout(Timeout):
    \"\"\"The server did not send any data in the allotted amount of time.\"\"\"


class URLRequired(RequestException):
    \"\"\"A valid URL is required to make a request.\"\"\"


class TooManyRedirects(RequestException):
    \"\"\"Too many redirects.\"\"\"


class MissingSchema(RequestException, ValueError):
    \"\"\"The URL scheme (e.g. http or https) is missing.\"\"\"


class InvalidSchema(RequestException, ValueError):
    \"\"\"The URL scheme provided is either invalid or unsupported.\"\"\"


class InvalidURL(RequestException, ValueError):
    \"\"\"The URL provided was somehow invalid.\"\"\"


class InvalidHeader(RequestException, ValueError):
    \"\"\"The header value provided was somehow invalid.\"\"\"


class InvalidProxyURL(InvalidURL):
    \"\"\"The proxy URL provided is invalid.\"\"\"


class ChunkedEncodingError(RequestException):
    \"\"\"The server declared chunked encoding but sent an invalid chunk.\"\"\"


class ContentDecodingError(RequestException, BaseHTTPError):
    \"\"\"Failed to decode response content.\"\"\"


class StreamConsumedError(RequestException, TypeError):
    \"\"\"The content for this response was already consumed.\"\"\"


class RetryError(RequestException):
    \"\"\"Custom retries logic failed\"\"\"


class UnrewindableBodyError(RequestException):
    \"\"\"Requests encountered an error when trying to rewind a body.\"\"\"


# Warnings


class RequestsWarning(Warning):
    \"\"\"Base warning for Requests.\"\"\"


class FileModeWarning(RequestsWarning, DeprecationWarning):
    \"\"\"A file was opened in text mode, but Requests determined its binary length.\"\"\"


class RequestsDependencyWarning(RequestsWarning):
    \"\"\"An imported dependency doesn't match the expected version range.\"\"\"

"""
module_dict["requests"+os.sep+"__init__.py"]="""
#   __
#  /__)  _  _     _   _ _/   _
# / (   (- (/ (/ (- _)  /  _)
#          /

# < include 'urllib3.py' >

# < include 'charset_normalizer.py' >

# < include 'chardet.py' >

# < include 'cryptography.py' >

\"\"\"
Requests HTTP Library
~~~~~~~~~~~~~~~~~~~~~

Requests is an HTTP library, written in Python, for human beings.
Basic GET usage:

   >>> import requests
   >>> r = requests.get('https://www.python.org')
   >>> r.status_code
   200
   >>> b'Python is a programming language' in r.content
   True

... or POST:

   >>> payload = dict(key1='value1', key2='value2')
   >>> r = requests.post('https://httpbin.org/post', data=payload)
   >>> print(r.text)
   {
     ...
     \"form\": {
       \"key1\": \"value1\",
       \"key2\": \"value2\"
     },
     ...
   }

The other HTTP methods are supported - see `requests.api`. Full documentation
is at <https://requests.readthedocs.io>.

:copyright: (c) 2017 by Kenneth Reitz.
:license: Apache 2.0, see LICENSE for more details.
\"\"\"

import warnings

import urllib3

from .exceptions import RequestsDependencyWarning

try:
    from charset_normalizer import __version__ as charset_normalizer_version
except ImportError:
    charset_normalizer_version = None

try:
    from chardet import __version__ as chardet_version
except ImportError:
    chardet_version = None


def check_compatibility(urllib3_version, chardet_version, charset_normalizer_version):
    urllib3_version = urllib3_version.split(\".\")
    assert urllib3_version != [\"dev\"]  # Verify urllib3 isn't installed from git.

    # Sometimes, urllib3 only reports its version as 16.1.
    if len(urllib3_version) == 2:
        urllib3_version.append(\"0\")

    # Check urllib3 for compatibility.
    major, minor, patch = urllib3_version  # noqa: F811
    major, minor, patch = int(major), int(minor), int(patch)
    # urllib3 >= 1.21.1, <= 1.26
    assert major == 1
    assert minor >= 21
    assert minor <= 26

    # Check charset_normalizer for compatibility.
    if chardet_version:
        major, minor, patch = chardet_version.split(\".\")[:3]
        major, minor, patch = int(major), int(minor), int(patch)
        # chardet_version >= 3.0.2, < 6.0.0
        assert (3, 0, 2) <= (major, minor, patch) < (6, 0, 0)
    elif charset_normalizer_version:
        major, minor, patch = charset_normalizer_version.split(\".\")[:3]
        major, minor, patch = int(major), int(minor), int(patch)
        # charset_normalizer >= 2.0.0 < 3.0.0
        assert (2, 0, 0) <= (major, minor, patch) < (3, 0, 0)
    else:
        raise Exception(\"You need either charset_normalizer or chardet installed\")


def _check_cryptography(cryptography_version):
    # cryptography < 1.3.4
    try:
        cryptography_version = list(map(int, cryptography_version.split(\".\")))
    except ValueError:
        return

    if cryptography_version < [1, 3, 4]:
        warning = \"Old version of cryptography ({}) may cause slowdown.\".format(
            cryptography_version
        )
        warnings.warn(warning, RequestsDependencyWarning)


# Check imported dependencies for compatibility.
try:
    check_compatibility(
        urllib3.__version__, chardet_version, charset_normalizer_version
    )
except (AssertionError, ValueError):
    warnings.warn(
        \"urllib3 ({}) or chardet ({})/charset_normalizer ({}) doesn't match a supported \"
        \"version!\".format(
            urllib3.__version__, chardet_version, charset_normalizer_version
        ),
        RequestsDependencyWarning,
    )

# Attempt to enable urllib3's fallback for SNI support
# if the standard library doesn't support SNI or the
# 'ssl' library isn't available.
try:
    try:
        import ssl
    except ImportError:
        ssl = None

    if not getattr(ssl, \"HAS_SNI\", False):
        from urllib3.contrib import pyopenssl

        pyopenssl.inject_into_urllib3()

        # Check cryptography version
        from cryptography import __version__ as cryptography_version

        _check_cryptography(cryptography_version)
except ImportError:
    pass

# urllib3's DependencyWarnings should be silenced.
from urllib3.exceptions import DependencyWarning

warnings.simplefilter(\"ignore\", DependencyWarning)

# Set default logging handler to avoid \"No handler found\" warnings.
import logging
from logging import NullHandler

from . import packages, utils
from .__version__ import (
    __author__,
    __author_email__,
    __build__,
    __cake__,
    __copyright__,
    __description__,
    __license__,
    __title__,
    __url__,
    __version__,
)
from .api import delete, get, head, options, patch, post, put, request
from .exceptions import (
    ConnectionError,
    ConnectTimeout,
    FileModeWarning,
    HTTPError,
    JSONDecodeError,
    ReadTimeout,
    RequestException,
    Timeout,
    TooManyRedirects,
    URLRequired,
)
from .models import PreparedRequest, Request, Response
from .sessions import Session, session
from .status_codes import codes

logging.getLogger(__name__).addHandler(NullHandler())

# FileModeWarnings go off per the default.
warnings.simplefilter(\"default\", FileModeWarning, append=True)

"""
module_dict["requests"+os.sep+"_internal_utils.py"]="""
\"\"\"
requests._internal_utils
~~~~~~~~~~~~~~

Provides utility functions that are consumed internally by Requests
which depend on extremely few external helpers (such as compat)
\"\"\"
import re

from .compat import builtin_str

_VALID_HEADER_NAME_RE_BYTE = re.compile(rb\"^[^:\\s][^:\\r\\n]*$\")
_VALID_HEADER_NAME_RE_STR = re.compile(r\"^[^:\\s][^:\\r\\n]*$\")
_VALID_HEADER_VALUE_RE_BYTE = re.compile(rb\"^\\S[^\\r\\n]*$|^$\")
_VALID_HEADER_VALUE_RE_STR = re.compile(r\"^\\S[^\\r\\n]*$|^$\")

HEADER_VALIDATORS = {
    bytes: (_VALID_HEADER_NAME_RE_BYTE, _VALID_HEADER_VALUE_RE_BYTE),
    str: (_VALID_HEADER_NAME_RE_STR, _VALID_HEADER_VALUE_RE_STR),
}


def to_native_string(string, encoding=\"ascii\"):
    \"\"\"Given a string object, regardless of type, returns a representation of
    that string in the native string type, encoding and decoding where
    necessary. This assumes ASCII unless told otherwise.
    \"\"\"
    if isinstance(string, builtin_str):
        out = string
    else:
        out = string.decode(encoding)

    return out


def unicode_is_ascii(u_string):
    \"\"\"Determine if unicode string only contains ASCII characters.

    :param str u_string: unicode string to check. Must be unicode
        and not Python 2 `str`.
    :rtype: bool
    \"\"\"
    assert isinstance(u_string, str)
    try:
        u_string.encode(\"ascii\")
        return True
    except UnicodeEncodeError:
        return False

"""
module_dict["requests"+os.sep+"utils.py"]="""
# < include 'urllib3.py' >

\"\"\"
requests.utils
~~~~~~~~~~~~~~

This module provides utility functions that are used within Requests
that are also useful for external consumption.
\"\"\"

import codecs
import contextlib
import io
import os
import re
import socket
import struct
import sys
import tempfile
import warnings
import zipfile
from collections import OrderedDict

from urllib3.util import make_headers, parse_url

from . import certs
from .__version__ import __version__

# to_native_string is unused here, but imported here for backwards compatibility
from ._internal_utils import HEADER_VALIDATORS, to_native_string  # noqa: F401
from .compat import (
    Mapping,
    basestring,
    bytes,
    getproxies,
    getproxies_environment,
    integer_types,
)
from .compat import parse_http_list as _parse_list_header
from .compat import (
    proxy_bypass,
    proxy_bypass_environment,
    quote,
    str,
    unquote,
    urlparse,
    urlunparse,
)
from .cookies import cookiejar_from_dict
from .exceptions import (
    FileModeWarning,
    InvalidHeader,
    InvalidURL,
    UnrewindableBodyError,
)
from .structures import CaseInsensitiveDict

NETRC_FILES = (\".netrc\", \"_netrc\")

DEFAULT_CA_BUNDLE_PATH = certs.where()

DEFAULT_PORTS = {\"http\": 80, \"https\": 443}

# Ensure that ', ' is used to preserve previous delimiter behavior.
DEFAULT_ACCEPT_ENCODING = \", \".join(
    re.split(r\",\\s*\", make_headers(accept_encoding=True)[\"accept-encoding\"])
)


if sys.platform == \"win32\":
    # provide a proxy_bypass version on Windows without DNS lookups

    def proxy_bypass_registry(host):
        try:
            import winreg
        except ImportError:
            return False

        try:
            internetSettings = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r\"Software\\Microsoft\\Windows\\CurrentVersion\\Internet Settings\",
            )
            # ProxyEnable could be REG_SZ or REG_DWORD, normalizing it
            proxyEnable = int(winreg.QueryValueEx(internetSettings, \"ProxyEnable\")[0])
            # ProxyOverride is almost always a string
            proxyOverride = winreg.QueryValueEx(internetSettings, \"ProxyOverride\")[0]
        except (OSError, ValueError):
            return False
        if not proxyEnable or not proxyOverride:
            return False

        # make a check value list from the registry entry: replace the
        # '<local>' string by the localhost entry and the corresponding
        # canonical entry.
        proxyOverride = proxyOverride.split(\";\")
        # now check if we match one of the registry values.
        for test in proxyOverride:
            if test == \"<local>\":
                if \".\" not in host:
                    return True
            test = test.replace(\".\", r\"\\.\")  # mask dots
            test = test.replace(\"*\", r\".*\")  # change glob sequence
            test = test.replace(\"?\", r\".\")  # change glob char
            if re.match(test, host, re.I):
                return True
        return False

    def proxy_bypass(host):  # noqa
        \"\"\"Return True, if the host should be bypassed.

        Checks proxy settings gathered from the environment, if specified,
        or the registry.
        \"\"\"
        if getproxies_environment():
            return proxy_bypass_environment(host)
        else:
            return proxy_bypass_registry(host)


def dict_to_sequence(d):
    \"\"\"Returns an internal sequence dictionary update.\"\"\"

    if hasattr(d, \"items\"):
        d = d.items()

    return d


def super_len(o):
    total_length = None
    current_position = 0

    if hasattr(o, \"__len__\"):
        total_length = len(o)

    elif hasattr(o, \"len\"):
        total_length = o.len

    elif hasattr(o, \"fileno\"):
        try:
            fileno = o.fileno()
        except (io.UnsupportedOperation, AttributeError):
            # AttributeError is a surprising exception, seeing as how we've just checked
            # that `hasattr(o, 'fileno')`.  It happens for objects obtained via
            # `Tarfile.extractfile()`, per issue 5229.
            pass
        else:
            total_length = os.fstat(fileno).st_size

            # Having used fstat to determine the file length, we need to
            # confirm that this file was opened up in binary mode.
            if \"b\" not in o.mode:
                warnings.warn(
                    (
                        \"Requests has determined the content-length for this \"
                        \"request using the binary size of the file: however, the \"
                        \"file has been opened in text mode (i.e. without the 'b' \"
                        \"flag in the mode). This may lead to an incorrect \"
                        \"content-length. In Requests 3.0, support will be removed \"
                        \"for files in text mode.\"
                    ),
                    FileModeWarning,
                )

    if hasattr(o, \"tell\"):
        try:
            current_position = o.tell()
        except OSError:
            # This can happen in some weird situations, such as when the file
            # is actually a special file descriptor like stdin. In this
            # instance, we don't know what the length is, so set it to zero and
            # let requests chunk it instead.
            if total_length is not None:
                current_position = total_length
        else:
            if hasattr(o, \"seek\") and total_length is None:
                # StringIO and BytesIO have seek but no usable fileno
                try:
                    # seek to end of file
                    o.seek(0, 2)
                    total_length = o.tell()

                    # seek back to current position to support
                    # partially read file-like objects
                    o.seek(current_position or 0)
                except OSError:
                    total_length = 0

    if total_length is None:
        total_length = 0

    return max(0, total_length - current_position)


def get_netrc_auth(url, raise_errors=False):
    \"\"\"Returns the Requests tuple auth for a given url from netrc.\"\"\"

    netrc_file = os.environ.get(\"NETRC\")
    if netrc_file is not None:
        netrc_locations = (netrc_file,)
    else:
        netrc_locations = (f\"~/{f}\" for f in NETRC_FILES)

    try:
        from netrc import NetrcParseError, netrc

        netrc_path = None

        for f in netrc_locations:
            try:
                loc = os.path.expanduser(f)
            except KeyError:
                # os.path.expanduser can fail when $HOME is undefined and
                # getpwuid fails. See https://bugs.python.org/issue20164 &
                # https://github.com/psf/requests/issues/1846
                return

            if os.path.exists(loc):
                netrc_path = loc
                break

        # Abort early if there isn't one.
        if netrc_path is None:
            return

        ri = urlparse(url)

        # Strip port numbers from netloc. This weird `if...encode`` dance is
        # used for Python 3.2, which doesn't support unicode literals.
        splitstr = b\":\"
        if isinstance(url, str):
            splitstr = splitstr.decode(\"ascii\")
        host = ri.netloc.split(splitstr)[0]

        try:
            _netrc = netrc(netrc_path).authenticators(host)
            if _netrc:
                # Return with login / password
                login_i = 0 if _netrc[0] else 1
                return (_netrc[login_i], _netrc[2])
        except (NetrcParseError, OSError):
            # If there was a parsing error or a permissions issue reading the file,
            # we'll just skip netrc auth unless explicitly asked to raise errors.
            if raise_errors:
                raise

    # App Engine hackiness.
    except (ImportError, AttributeError):
        pass


def guess_filename(obj):
    \"\"\"Tries to guess the filename of the given object.\"\"\"
    name = getattr(obj, \"name\", None)
    if name and isinstance(name, basestring) and name[0] != \"<\" and name[-1] != \">\":
        return os.path.basename(name)


def extract_zipped_paths(path):
    \"\"\"Replace nonexistent paths that look like they refer to a member of a zip
    archive with the location of an extracted copy of the target, or else
    just return the provided path unchanged.
    \"\"\"
    if os.path.exists(path):
        # this is already a valid path, no need to do anything further
        return path

    # find the first valid part of the provided path and treat that as a zip archive
    # assume the rest of the path is the name of a member in the archive
    archive, member = os.path.split(path)
    while archive and not os.path.exists(archive):
        archive, prefix = os.path.split(archive)
        if not prefix:
            # If we don't check for an empty prefix after the split (in other words, archive remains unchanged after the split),
            # we _can_ end up in an infinite loop on a rare corner case affecting a small number of users
            break
        member = \"/\".join([prefix, member])

    if not zipfile.is_zipfile(archive):
        return path

    zip_file = zipfile.ZipFile(archive)
    if member not in zip_file.namelist():
        return path

    # we have a valid zip archive and a valid member of that archive
    tmp = tempfile.gettempdir()
    extracted_path = os.path.join(tmp, member.split(\"/\")[-1])
    if not os.path.exists(extracted_path):
        # use read + write to avoid the creating nested folders, we only want the file, avoids mkdir racing condition
        with atomic_open(extracted_path) as file_handler:
            file_handler.write(zip_file.read(member))
    return extracted_path


@contextlib.contextmanager
def atomic_open(filename):
    \"\"\"Write a file to the disk in an atomic fashion\"\"\"
    tmp_descriptor, tmp_name = tempfile.mkstemp(dir=os.path.dirname(filename))
    try:
        with os.fdopen(tmp_descriptor, \"wb\") as tmp_handler:
            yield tmp_handler
        os.replace(tmp_name, filename)
    except BaseException:
        os.remove(tmp_name)
        raise


def from_key_val_list(value):
    \"\"\"Take an object and test to see if it can be represented as a
    dictionary. Unless it can not be represented as such, return an
    OrderedDict, e.g.,

    ::

        >>> from_key_val_list([('key', 'val')])
        OrderedDict([('key', 'val')])
        >>> from_key_val_list('string')
        Traceback (most recent call last):
        ...
        ValueError: cannot encode objects that are not 2-tuples
        >>> from_key_val_list({'key': 'val'})
        OrderedDict([('key', 'val')])

    :rtype: OrderedDict
    \"\"\"
    if value is None:
        return None

    if isinstance(value, (str, bytes, bool, int)):
        raise ValueError(\"cannot encode objects that are not 2-tuples\")

    return OrderedDict(value)


def to_key_val_list(value):
    \"\"\"Take an object and test to see if it can be represented as a
    dictionary. If it can be, return a list of tuples, e.g.,

    ::

        >>> to_key_val_list([('key', 'val')])
        [('key', 'val')]
        >>> to_key_val_list({'key': 'val'})
        [('key', 'val')]
        >>> to_key_val_list('string')
        Traceback (most recent call last):
        ...
        ValueError: cannot encode objects that are not 2-tuples

    :rtype: list
    \"\"\"
    if value is None:
        return None

    if isinstance(value, (str, bytes, bool, int)):
        raise ValueError(\"cannot encode objects that are not 2-tuples\")

    if isinstance(value, Mapping):
        value = value.items()

    return list(value)


# From mitsuhiko/werkzeug (used with permission).
def parse_list_header(value):
    \"\"\"Parse lists as described by RFC 2068 Section 2.

    In particular, parse comma-separated lists where the elements of
    the list may include quoted-strings.  A quoted-string could
    contain a comma.  A non-quoted string could have quotes in the
    middle.  Quotes are removed automatically after parsing.

    It basically works like :func:`parse_set_header` just that items
    may appear multiple times and case sensitivity is preserved.

    The return value is a standard :class:`list`:

    >>> parse_list_header('token, \"quoted value\"')
    ['token', 'quoted value']

    To create a header from the :class:`list` again, use the
    :func:`dump_header` function.

    :param value: a string with a list header.
    :return: :class:`list`
    :rtype: list
    \"\"\"
    result = []
    for item in _parse_list_header(value):
        if item[:1] == item[-1:] == '\"':
            item = unquote_header_value(item[1:-1])
        result.append(item)
    return result


# From mitsuhiko/werkzeug (used with permission).
def parse_dict_header(value):
    \"\"\"Parse lists of key, value pairs as described by RFC 2068 Section 2 and
    convert them into a python dict:

    >>> d = parse_dict_header('foo=\"is a fish\", bar=\"as well\"')
    >>> type(d) is dict
    True
    >>> sorted(d.items())
    [('bar', 'as well'), ('foo', 'is a fish')]

    If there is no value for a key it will be `None`:

    >>> parse_dict_header('key_without_value')
    {'key_without_value': None}

    To create a header from the :class:`dict` again, use the
    :func:`dump_header` function.

    :param value: a string with a dict header.
    :return: :class:`dict`
    :rtype: dict
    \"\"\"
    result = {}
    for item in _parse_list_header(value):
        if \"=\" not in item:
            result[item] = None
            continue
        name, value = item.split(\"=\", 1)
        if value[:1] == value[-1:] == '\"':
            value = unquote_header_value(value[1:-1])
        result[name] = value
    return result


# From mitsuhiko/werkzeug (used with permission).
def unquote_header_value(value, is_filename=False):
    r\"\"\"Unquotes a header value.  (Reversal of :func:`quote_header_value`).
    This does not use the real unquoting but what browsers are actually
    using for quoting.

    :param value: the header value to unquote.
    :rtype: str
    \"\"\"
    if value and value[0] == value[-1] == '\"':
        # this is not the real unquoting, but fixing this so that the
        # RFC is met will result in bugs with internet explorer and
        # probably some other browsers as well.  IE for example is
        # uploading files with \"C:\\foo\\bar.txt\" as filename
        value = value[1:-1]

        # if this is a filename and the starting characters look like
        # a UNC path, then just return the value without quotes.  Using the
        # replace sequence below on a UNC path has the effect of turning
        # the leading double slash into a single slash and then
        # _fix_ie_filename() doesn't work correctly.  See #458.
        if not is_filename or value[:2] != \"\\\\\\\\\":
            return value.replace(\"\\\\\\\\\", \"\\\\\").replace('\\\\\"', '\"')
    return value


def dict_from_cookiejar(cj):
    \"\"\"Returns a key/value dictionary from a CookieJar.

    :param cj: CookieJar object to extract cookies from.
    :rtype: dict
    \"\"\"

    cookie_dict = {}

    for cookie in cj:
        cookie_dict[cookie.name] = cookie.value

    return cookie_dict


def add_dict_to_cookiejar(cj, cookie_dict):
    \"\"\"Returns a CookieJar from a key/value dictionary.

    :param cj: CookieJar to insert cookies into.
    :param cookie_dict: Dict of key/values to insert into CookieJar.
    :rtype: CookieJar
    \"\"\"

    return cookiejar_from_dict(cookie_dict, cj)


def get_encodings_from_content(content):
    \"\"\"Returns encodings from given content string.

    :param content: bytestring to extract encodings from.
    \"\"\"
    warnings.warn(
        (
            \"In requests 3.0, get_encodings_from_content will be removed. For \"
            \"more information, please see the discussion on issue #2266. (This\"
            \" warning should only appear once.)\"
        ),
        DeprecationWarning,
    )

    charset_re = re.compile(r'<meta.*?charset=[\"\\']*(.+?)[\"\\'>]', flags=re.I)
    pragma_re = re.compile(r'<meta.*?content=[\"\\']*;?charset=(.+?)[\"\\'>]', flags=re.I)
    xml_re = re.compile(r'^<\\?xml.*?encoding=[\"\\']*(.+?)[\"\\'>]')

    return (
        charset_re.findall(content)
        + pragma_re.findall(content)
        + xml_re.findall(content)
    )


def _parse_content_type_header(header):
    \"\"\"Returns content type and parameters from given header

    :param header: string
    :return: tuple containing content type and dictionary of
         parameters
    \"\"\"

    tokens = header.split(\";\")
    content_type, params = tokens[0].strip(), tokens[1:]
    params_dict = {}
    items_to_strip = \"\\\"' \"

    for param in params:
        param = param.strip()
        if param:
            key, value = param, True
            index_of_equals = param.find(\"=\")
            if index_of_equals != -1:
                key = param[:index_of_equals].strip(items_to_strip)
                value = param[index_of_equals + 1 :].strip(items_to_strip)
            params_dict[key.lower()] = value
    return content_type, params_dict


def get_encoding_from_headers(headers):
    \"\"\"Returns encodings from given HTTP Header Dict.

    :param headers: dictionary to extract encoding from.
    :rtype: str
    \"\"\"

    content_type = headers.get(\"content-type\")

    if not content_type:
        return None

    content_type, params = _parse_content_type_header(content_type)

    if \"charset\" in params:
        return params[\"charset\"].strip(\"'\\\"\")

    if \"text\" in content_type:
        return \"ISO-8859-1\"

    if \"application/json\" in content_type:
        # Assume UTF-8 based on RFC 4627: https://www.ietf.org/rfc/rfc4627.txt since the charset was unset
        return \"utf-8\"


def stream_decode_response_unicode(iterator, r):
    \"\"\"Stream decodes an iterator.\"\"\"

    if r.encoding is None:
        yield from iterator
        return

    decoder = codecs.getincrementaldecoder(r.encoding)(errors=\"replace\")
    for chunk in iterator:
        rv = decoder.decode(chunk)
        if rv:
            yield rv
    rv = decoder.decode(b\"\", final=True)
    if rv:
        yield rv


def iter_slices(string, slice_length):
    \"\"\"Iterate over slices of a string.\"\"\"
    pos = 0
    if slice_length is None or slice_length <= 0:
        slice_length = len(string)
    while pos < len(string):
        yield string[pos : pos + slice_length]
        pos += slice_length


def get_unicode_from_response(r):
    \"\"\"Returns the requested content back in unicode.

    :param r: Response object to get unicode content from.

    Tried:

    1. charset from content-type
    2. fall back and replace all unicode characters

    :rtype: str
    \"\"\"
    warnings.warn(
        (
            \"In requests 3.0, get_unicode_from_response will be removed. For \"
            \"more information, please see the discussion on issue #2266. (This\"
            \" warning should only appear once.)\"
        ),
        DeprecationWarning,
    )

    tried_encodings = []

    # Try charset from content-type
    encoding = get_encoding_from_headers(r.headers)

    if encoding:
        try:
            return str(r.content, encoding)
        except UnicodeError:
            tried_encodings.append(encoding)

    # Fall back:
    try:
        return str(r.content, encoding, errors=\"replace\")
    except TypeError:
        return r.content


# The unreserved URI characters (RFC 3986)
UNRESERVED_SET = frozenset(
    \"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz\" + \"0123456789-._~\"
)


def unquote_unreserved(uri):
    \"\"\"Un-escape any percent-escape sequences in a URI that are unreserved
    characters. This leaves all reserved, illegal and non-ASCII bytes encoded.

    :rtype: str
    \"\"\"
    parts = uri.split(\"%\")
    for i in range(1, len(parts)):
        h = parts[i][0:2]
        if len(h) == 2 and h.isalnum():
            try:
                c = chr(int(h, 16))
            except ValueError:
                raise InvalidURL(f\"Invalid percent-escape sequence: '{h}'\")

            if c in UNRESERVED_SET:
                parts[i] = c + parts[i][2:]
            else:
                parts[i] = f\"%{parts[i]}\"
        else:
            parts[i] = f\"%{parts[i]}\"
    return \"\".join(parts)


def requote_uri(uri):
    \"\"\"Re-quote the given URI.

    This function passes the given URI through an unquote/quote cycle to
    ensure that it is fully and consistently quoted.

    :rtype: str
    \"\"\"
    safe_with_percent = \"!#$%&'()*+,/:;=?@[]~\"
    safe_without_percent = \"!#$&'()*+,/:;=?@[]~\"
    try:
        # Unquote only the unreserved characters
        # Then quote only illegal characters (do not quote reserved,
        # unreserved, or '%')
        return quote(unquote_unreserved(uri), safe=safe_with_percent)
    except InvalidURL:
        # We couldn't unquote the given URI, so let's try quoting it, but
        # there may be unquoted '%'s in the URI. We need to make sure they're
        # properly quoted so they do not cause issues elsewhere.
        return quote(uri, safe=safe_without_percent)


def address_in_network(ip, net):
    \"\"\"This function allows you to check if an IP belongs to a network subnet

    Example: returns True if ip = 192.168.1.1 and net = 192.168.1.0/24
             returns False if ip = 192.168.1.1 and net = 192.168.100.0/24

    :rtype: bool
    \"\"\"
    ipaddr = struct.unpack(\"=L\", socket.inet_aton(ip))[0]
    netaddr, bits = net.split(\"/\")
    netmask = struct.unpack(\"=L\", socket.inet_aton(dotted_netmask(int(bits))))[0]
    network = struct.unpack(\"=L\", socket.inet_aton(netaddr))[0] & netmask
    return (ipaddr & netmask) == (network & netmask)


def dotted_netmask(mask):
    \"\"\"Converts mask from /xx format to xxx.xxx.xxx.xxx

    Example: if mask is 24 function returns 255.255.255.0

    :rtype: str
    \"\"\"
    bits = 0xFFFFFFFF ^ (1 << 32 - mask) - 1
    return socket.inet_ntoa(struct.pack(\">I\", bits))


def is_ipv4_address(string_ip):
    \"\"\"
    :rtype: bool
    \"\"\"
    try:
        socket.inet_aton(string_ip)
    except OSError:
        return False
    return True


def is_valid_cidr(string_network):
    \"\"\"
    Very simple check of the cidr format in no_proxy variable.

    :rtype: bool
    \"\"\"
    if string_network.count(\"/\") == 1:
        try:
            mask = int(string_network.split(\"/\")[1])
        except ValueError:
            return False

        if mask < 1 or mask > 32:
            return False

        try:
            socket.inet_aton(string_network.split(\"/\")[0])
        except OSError:
            return False
    else:
        return False
    return True


@contextlib.contextmanager
def set_environ(env_name, value):
    \"\"\"Set the environment variable 'env_name' to 'value'

    Save previous value, yield, and then restore the previous value stored in
    the environment variable 'env_name'.

    If 'value' is None, do nothing\"\"\"
    value_changed = value is not None
    if value_changed:
        old_value = os.environ.get(env_name)
        os.environ[env_name] = value
    try:
        yield
    finally:
        if value_changed:
            if old_value is None:
                del os.environ[env_name]
            else:
                os.environ[env_name] = old_value


def should_bypass_proxies(url, no_proxy):
    \"\"\"
    Returns whether we should bypass proxies or not.

    :rtype: bool
    \"\"\"
    # Prioritize lowercase environment variables over uppercase
    # to keep a consistent behaviour with other http projects (curl, wget).
    def get_proxy(key):
        return os.environ.get(key) or os.environ.get(key.upper())

    # First check whether no_proxy is defined. If it is, check that the URL
    # we're getting isn't in the no_proxy list.
    no_proxy_arg = no_proxy
    if no_proxy is None:
        no_proxy = get_proxy(\"no_proxy\")
    parsed = urlparse(url)

    if parsed.hostname is None:
        # URLs don't always have hostnames, e.g. file:/// urls.
        return True

    if no_proxy:
        # We need to check whether we match here. We need to see if we match
        # the end of the hostname, both with and without the port.
        no_proxy = (host for host in no_proxy.replace(\" \", \"\").split(\",\") if host)

        if is_ipv4_address(parsed.hostname):
            for proxy_ip in no_proxy:
                if is_valid_cidr(proxy_ip):
                    if address_in_network(parsed.hostname, proxy_ip):
                        return True
                elif parsed.hostname == proxy_ip:
                    # If no_proxy ip was defined in plain IP notation instead of cidr notation &
                    # matches the IP of the index
                    return True
        else:
            host_with_port = parsed.hostname
            if parsed.port:
                host_with_port += f\":{parsed.port}\"

            for host in no_proxy:
                if parsed.hostname.endswith(host) or host_with_port.endswith(host):
                    # The URL does match something in no_proxy, so we don't want
                    # to apply the proxies on this URL.
                    return True

    with set_environ(\"no_proxy\", no_proxy_arg):
        # parsed.hostname can be `None` in cases such as a file URI.
        try:
            bypass = proxy_bypass(parsed.hostname)
        except (TypeError, socket.gaierror):
            bypass = False

    if bypass:
        return True

    return False


def get_environ_proxies(url, no_proxy=None):
    \"\"\"
    Return a dict of environment proxies.

    :rtype: dict
    \"\"\"
    if should_bypass_proxies(url, no_proxy=no_proxy):
        return {}
    else:
        return getproxies()


def select_proxy(url, proxies):
    \"\"\"Select a proxy for the url, if applicable.

    :param url: The url being for the request
    :param proxies: A dictionary of schemes or schemes and hosts to proxy URLs
    \"\"\"
    proxies = proxies or {}
    urlparts = urlparse(url)
    if urlparts.hostname is None:
        return proxies.get(urlparts.scheme, proxies.get(\"all\"))

    proxy_keys = [
        urlparts.scheme + \"://\" + urlparts.hostname,
        urlparts.scheme,
        \"all://\" + urlparts.hostname,
        \"all\",
    ]
    proxy = None
    for proxy_key in proxy_keys:
        if proxy_key in proxies:
            proxy = proxies[proxy_key]
            break

    return proxy


def resolve_proxies(request, proxies, trust_env=True):
    \"\"\"This method takes proxy information from a request and configuration
    input to resolve a mapping of target proxies. This will consider settings
    such a NO_PROXY to strip proxy configurations.

    :param request: Request or PreparedRequest
    :param proxies: A dictionary of schemes or schemes and hosts to proxy URLs
    :param trust_env: Boolean declaring whether to trust environment configs

    :rtype: dict
    \"\"\"
    proxies = proxies if proxies is not None else {}
    url = request.url
    scheme = urlparse(url).scheme
    no_proxy = proxies.get(\"no_proxy\")
    new_proxies = proxies.copy()

    if trust_env and not should_bypass_proxies(url, no_proxy=no_proxy):
        environ_proxies = get_environ_proxies(url, no_proxy=no_proxy)

        proxy = environ_proxies.get(scheme, environ_proxies.get(\"all\"))

        if proxy:
            new_proxies.setdefault(scheme, proxy)
    return new_proxies


def default_user_agent(name=\"python-requests\"):
    \"\"\"
    Return a string representing the default user agent.

    :rtype: str
    \"\"\"
    return f\"{name}/{__version__}\"


def default_headers():
    \"\"\"
    :rtype: requests.structures.CaseInsensitiveDict
    \"\"\"
    return CaseInsensitiveDict(
        {
            \"User-Agent\": default_user_agent(),
            \"Accept-Encoding\": DEFAULT_ACCEPT_ENCODING,
            \"Accept\": \"*/*\",
            \"Connection\": \"keep-alive\",
        }
    )


def parse_header_links(value):
    \"\"\"Return a list of parsed link headers proxies.

    i.e. Link: <http:/.../front.jpeg>; rel=front; type=\"image/jpeg\",<http://.../back.jpeg>; rel=back;type=\"image/jpeg\"

    :rtype: list
    \"\"\"

    links = []

    replace_chars = \" '\\\"\"

    value = value.strip(replace_chars)
    if not value:
        return links

    for val in re.split(\", *<\", value):
        try:
            url, params = val.split(\";\", 1)
        except ValueError:
            url, params = val, \"\"

        link = {\"url\": url.strip(\"<> '\\\"\")}

        for param in params.split(\";\"):
            try:
                key, value = param.split(\"=\")
            except ValueError:
                break

            link[key.strip(replace_chars)] = value.strip(replace_chars)

        links.append(link)

    return links


# Null bytes; no need to recreate these on each call to guess_json_utf
_null = \"\\x00\".encode(\"ascii\")  # encoding to ASCII for Python 3
_null2 = _null * 2
_null3 = _null * 3


def guess_json_utf(data):
    \"\"\"
    :rtype: str
    \"\"\"
    # JSON always starts with two ASCII characters, so detection is as
    # easy as counting the nulls and from their location and count
    # determine the encoding. Also detect a BOM, if present.
    sample = data[:4]
    if sample in (codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE):
        return \"utf-32\"  # BOM included
    if sample[:3] == codecs.BOM_UTF8:
        return \"utf-8-sig\"  # BOM included, MS style (discouraged)
    if sample[:2] in (codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE):
        return \"utf-16\"  # BOM included
    nullcount = sample.count(_null)
    if nullcount == 0:
        return \"utf-8\"
    if nullcount == 2:
        if sample[::2] == _null2:  # 1st and 3rd are null
            return \"utf-16-be\"
        if sample[1::2] == _null2:  # 2nd and 4th are null
            return \"utf-16-le\"
        # Did not detect 2 valid UTF-16 ascii-range characters
    if nullcount == 3:
        if sample[:3] == _null3:
            return \"utf-32-be\"
        if sample[1:] == _null3:
            return \"utf-32-le\"
        # Did not detect a valid UTF-32 ascii-range character
    return None


def prepend_scheme_if_needed(url, new_scheme):
    \"\"\"Given a URL that may or may not have a scheme, prepend the given scheme.
    Does not replace a present scheme with the one provided as an argument.

    :rtype: str
    \"\"\"
    parsed = parse_url(url)
    scheme, auth, host, port, path, query, fragment = parsed

    # A defect in urlparse determines that there isn't a netloc present in some
    # urls. We previously assumed parsing was overly cautious, and swapped the
    # netloc and path. Due to a lack of tests on the original defect, this is
    # maintained with parse_url for backwards compatibility.
    netloc = parsed.netloc
    if not netloc:
        netloc, path = path, netloc

    if auth:
        # parse_url doesn't provide the netloc with auth
        # so we'll add it ourselves.
        netloc = \"@\".join([auth, netloc])
    if scheme is None:
        scheme = new_scheme
    if path is None:
        path = \"\"

    return urlunparse((scheme, netloc, path, \"\", query, fragment))


def get_auth_from_url(url):
    \"\"\"Given a url with authentication components, extract them into a tuple of
    username,password.

    :rtype: (str,str)
    \"\"\"
    parsed = urlparse(url)

    try:
        auth = (unquote(parsed.username), unquote(parsed.password))
    except (AttributeError, TypeError):
        auth = (\"\", \"\")

    return auth


def check_header_validity(header):
    \"\"\"Verifies that header parts don't contain leading whitespace
    reserved characters, or return characters.

    :param header: tuple, in the format (name, value).
    \"\"\"
    name, value = header

    for part in header:
        if type(part) not in HEADER_VALIDATORS:
            raise InvalidHeader(
                f\"Header part ({part!r}) from {{{name!r}: {value!r}}} must be \"
                f\"of type str or bytes, not {type(part)}\"
            )

    _validate_header_part(name, \"name\", HEADER_VALIDATORS[type(name)][0])
    _validate_header_part(value, \"value\", HEADER_VALIDATORS[type(value)][1])


def _validate_header_part(header_part, header_kind, validator):
    if not validator.match(header_part):
        raise InvalidHeader(
            f\"Invalid leading whitespace, reserved character(s), or return\"
            f\"character(s) in header {header_kind}: {header_part!r}\"
        )


def urldefragauth(url):
    \"\"\"
    Given a url remove the fragment and the authentication part.

    :rtype: str
    \"\"\"
    scheme, netloc, path, params, query, fragment = urlparse(url)

    # see func:`prepend_scheme_if_needed`
    if not netloc:
        netloc, path = path, netloc

    netloc = netloc.rsplit(\"@\", 1)[-1]

    return urlunparse((scheme, netloc, path, params, query, \"\"))


def rewind_body(prepared_request):
    \"\"\"Move file pointer back to its recorded starting position
    so it can be read again on redirect.
    \"\"\"
    body_seek = getattr(prepared_request.body, \"seek\", None)
    if body_seek is not None and isinstance(
        prepared_request._body_position, integer_types
    ):
        try:
            body_seek(prepared_request._body_position)
        except OSError:
            raise UnrewindableBodyError(
                \"An error occurred when rewinding request body for redirect.\"
            )
    else:
        raise UnrewindableBodyError(\"Unable to rewind request body for redirect.\")

"""
module_dict["requests"+os.sep+"structures.py"]="""
\"\"\"
requests.structures
~~~~~~~~~~~~~~~~~~~

Data structures that power Requests.
\"\"\"

from collections import OrderedDict

from .compat import Mapping, MutableMapping


class CaseInsensitiveDict(MutableMapping):
    \"\"\"A case-insensitive ``dict``-like object.

    Implements all methods and operations of
    ``MutableMapping`` as well as dict's ``copy``. Also
    provides ``lower_items``.

    All keys are expected to be strings. The structure remembers the
    case of the last key to be set, and ``iter(instance)``,
    ``keys()``, ``items()``, ``iterkeys()``, and ``iteritems()``
    will contain case-sensitive keys. However, querying and contains
    testing is case insensitive::

        cid = CaseInsensitiveDict()
        cid['Accept'] = 'application/json'
        cid['aCCEPT'] == 'application/json'  # True
        list(cid) == ['Accept']  # True

    For example, ``headers['content-encoding']`` will return the
    value of a ``'Content-Encoding'`` response header, regardless
    of how the header name was originally stored.

    If the constructor, ``.update``, or equality comparison
    operations are given keys that have equal ``.lower()``s, the
    behavior is undefined.
    \"\"\"

    def __init__(self, data=None, **kwargs):
        self._store = OrderedDict()
        if data is None:
            data = {}
        self.update(data, **kwargs)

    def __setitem__(self, key, value):
        # Use the lowercased key for lookups, but store the actual
        # key alongside the value.
        self._store[key.lower()] = (key, value)

    def __getitem__(self, key):
        return self._store[key.lower()][1]

    def __delitem__(self, key):
        del self._store[key.lower()]

    def __iter__(self):
        return (casedkey for casedkey, mappedvalue in self._store.values())

    def __len__(self):
        return len(self._store)

    def lower_items(self):
        \"\"\"Like iteritems(), but with all lowercase keys.\"\"\"
        return ((lowerkey, keyval[1]) for (lowerkey, keyval) in self._store.items())

    def __eq__(self, other):
        if isinstance(other, Mapping):
            other = CaseInsensitiveDict(other)
        else:
            return NotImplemented
        # Compare insensitively
        return dict(self.lower_items()) == dict(other.lower_items())

    # Copy is required
    def copy(self):
        return CaseInsensitiveDict(self._store.values())

    def __repr__(self):
        return str(dict(self.items()))


class LookupDict(dict):
    \"\"\"Dictionary lookup object.\"\"\"

    def __init__(self, name=None):
        self.name = name
        super().__init__()

    def __repr__(self):
        return f\"<lookup '{self.name}'>\"

    def __getitem__(self, key):
        # We allow fall-through here, so values default to None

        return self.__dict__.get(key, None)

    def get(self, key, default=None):
        return self.__dict__.get(key, default)

"""
module_dict["requests"+os.sep+"cookies.py"]="""
# < include 'dummy_threading.py' >

\"\"\"
requests.cookies
~~~~~~~~~~~~~~~~

Compatibility code to be able to use `cookielib.CookieJar` with requests.

requests.utils imports from here, so be careful with imports.
\"\"\"

import calendar
import copy
import time

from ._internal_utils import to_native_string
from .compat import Morsel, MutableMapping, cookielib, urlparse, urlunparse

try:
    import threading
except ImportError:
    import dummy_threading as threading


class MockRequest:
    \"\"\"Wraps a `requests.Request` to mimic a `urllib2.Request`.

    The code in `cookielib.CookieJar` expects this interface in order to correctly
    manage cookie policies, i.e., determine whether a cookie can be set, given the
    domains of the request and the cookie.

    The original request object is read-only. The client is responsible for collecting
    the new headers via `get_new_headers()` and interpreting them appropriately. You
    probably want `get_cookie_header`, defined below.
    \"\"\"

    def __init__(self, request):
        self._r = request
        self._new_headers = {}
        self.type = urlparse(self._r.url).scheme

    def get_type(self):
        return self.type

    def get_host(self):
        return urlparse(self._r.url).netloc

    def get_origin_req_host(self):
        return self.get_host()

    def get_full_url(self):
        # Only return the response's URL if the user hadn't set the Host
        # header
        if not self._r.headers.get(\"Host\"):
            return self._r.url
        # If they did set it, retrieve it and reconstruct the expected domain
        host = to_native_string(self._r.headers[\"Host\"], encoding=\"utf-8\")
        parsed = urlparse(self._r.url)
        # Reconstruct the URL as we expect it
        return urlunparse(
            [
                parsed.scheme,
                host,
                parsed.path,
                parsed.params,
                parsed.query,
                parsed.fragment,
            ]
        )

    def is_unverifiable(self):
        return True

    def has_header(self, name):
        return name in self._r.headers or name in self._new_headers

    def get_header(self, name, default=None):
        return self._r.headers.get(name, self._new_headers.get(name, default))

    def add_header(self, key, val):
        \"\"\"cookielib has no legitimate use for this method; add it back if you find one.\"\"\"
        raise NotImplementedError(
            \"Cookie headers should be added with add_unredirected_header()\"
        )

    def add_unredirected_header(self, name, value):
        self._new_headers[name] = value

    def get_new_headers(self):
        return self._new_headers

    @property
    def unverifiable(self):
        return self.is_unverifiable()

    @property
    def origin_req_host(self):
        return self.get_origin_req_host()

    @property
    def host(self):
        return self.get_host()


class MockResponse:
    \"\"\"Wraps a `httplib.HTTPMessage` to mimic a `urllib.addinfourl`.

    ...what? Basically, expose the parsed HTTP headers from the server response
    the way `cookielib` expects to see them.
    \"\"\"

    def __init__(self, headers):
        \"\"\"Make a MockResponse for `cookielib` to read.

        :param headers: a httplib.HTTPMessage or analogous carrying the headers
        \"\"\"
        self._headers = headers

    def info(self):
        return self._headers

    def getheaders(self, name):
        self._headers.getheaders(name)


def extract_cookies_to_jar(jar, request, response):
    \"\"\"Extract the cookies from the response into a CookieJar.

    :param jar: cookielib.CookieJar (not necessarily a RequestsCookieJar)
    :param request: our own requests.Request object
    :param response: urllib3.HTTPResponse object
    \"\"\"
    if not (hasattr(response, \"_original_response\") and response._original_response):
        return
    # the _original_response field is the wrapped httplib.HTTPResponse object,
    req = MockRequest(request)
    # pull out the HTTPMessage with the headers and put it in the mock:
    res = MockResponse(response._original_response.msg)
    jar.extract_cookies(res, req)


def get_cookie_header(jar, request):
    \"\"\"
    Produce an appropriate Cookie header string to be sent with `request`, or None.

    :rtype: str
    \"\"\"
    r = MockRequest(request)
    jar.add_cookie_header(r)
    return r.get_new_headers().get(\"Cookie\")


def remove_cookie_by_name(cookiejar, name, domain=None, path=None):
    \"\"\"Unsets a cookie by name, by default over all domains and paths.

    Wraps CookieJar.clear(), is O(n).
    \"\"\"
    clearables = []
    for cookie in cookiejar:
        if cookie.name != name:
            continue
        if domain is not None and domain != cookie.domain:
            continue
        if path is not None and path != cookie.path:
            continue
        clearables.append((cookie.domain, cookie.path, cookie.name))

    for domain, path, name in clearables:
        cookiejar.clear(domain, path, name)


class CookieConflictError(RuntimeError):
    \"\"\"There are two cookies that meet the criteria specified in the cookie jar.
    Use .get and .set and include domain and path args in order to be more specific.
    \"\"\"


class RequestsCookieJar(cookielib.CookieJar, MutableMapping):
    \"\"\"Compatibility class; is a cookielib.CookieJar, but exposes a dict
    interface.

    This is the CookieJar we create by default for requests and sessions that
    don't specify one, since some clients may expect response.cookies and
    session.cookies to support dict operations.

    Requests does not use the dict interface internally; it's just for
    compatibility with external client code. All requests code should work
    out of the box with externally provided instances of ``CookieJar``, e.g.
    ``LWPCookieJar`` and ``FileCookieJar``.

    Unlike a regular CookieJar, this class is pickleable.

    .. warning:: dictionary operations that are normally O(1) may be O(n).
    \"\"\"

    def get(self, name, default=None, domain=None, path=None):
        \"\"\"Dict-like get() that also supports optional domain and path args in
        order to resolve naming collisions from using one cookie jar over
        multiple domains.

        .. warning:: operation is O(n), not O(1).
        \"\"\"
        try:
            return self._find_no_duplicates(name, domain, path)
        except KeyError:
            return default

    def set(self, name, value, **kwargs):
        \"\"\"Dict-like set() that also supports optional domain and path args in
        order to resolve naming collisions from using one cookie jar over
        multiple domains.
        \"\"\"
        # support client code that unsets cookies by assignment of a None value:
        if value is None:
            remove_cookie_by_name(
                self, name, domain=kwargs.get(\"domain\"), path=kwargs.get(\"path\")
            )
            return

        if isinstance(value, Morsel):
            c = morsel_to_cookie(value)
        else:
            c = create_cookie(name, value, **kwargs)
        self.set_cookie(c)
        return c

    def iterkeys(self):
        \"\"\"Dict-like iterkeys() that returns an iterator of names of cookies
        from the jar.

        .. seealso:: itervalues() and iteritems().
        \"\"\"
        for cookie in iter(self):
            yield cookie.name

    def keys(self):
        \"\"\"Dict-like keys() that returns a list of names of cookies from the
        jar.

        .. seealso:: values() and items().
        \"\"\"
        return list(self.iterkeys())

    def itervalues(self):
        \"\"\"Dict-like itervalues() that returns an iterator of values of cookies
        from the jar.

        .. seealso:: iterkeys() and iteritems().
        \"\"\"
        for cookie in iter(self):
            yield cookie.value

    def values(self):
        \"\"\"Dict-like values() that returns a list of values of cookies from the
        jar.

        .. seealso:: keys() and items().
        \"\"\"
        return list(self.itervalues())

    def iteritems(self):
        \"\"\"Dict-like iteritems() that returns an iterator of name-value tuples
        from the jar.

        .. seealso:: iterkeys() and itervalues().
        \"\"\"
        for cookie in iter(self):
            yield cookie.name, cookie.value

    def items(self):
        \"\"\"Dict-like items() that returns a list of name-value tuples from the
        jar. Allows client-code to call ``dict(RequestsCookieJar)`` and get a
        vanilla python dict of key value pairs.

        .. seealso:: keys() and values().
        \"\"\"
        return list(self.iteritems())

    def list_domains(self):
        \"\"\"Utility method to list all the domains in the jar.\"\"\"
        domains = []
        for cookie in iter(self):
            if cookie.domain not in domains:
                domains.append(cookie.domain)
        return domains

    def list_paths(self):
        \"\"\"Utility method to list all the paths in the jar.\"\"\"
        paths = []
        for cookie in iter(self):
            if cookie.path not in paths:
                paths.append(cookie.path)
        return paths

    def multiple_domains(self):
        \"\"\"Returns True if there are multiple domains in the jar.
        Returns False otherwise.

        :rtype: bool
        \"\"\"
        domains = []
        for cookie in iter(self):
            if cookie.domain is not None and cookie.domain in domains:
                return True
            domains.append(cookie.domain)
        return False  # there is only one domain in jar

    def get_dict(self, domain=None, path=None):
        \"\"\"Takes as an argument an optional domain and path and returns a plain
        old Python dict of name-value pairs of cookies that meet the
        requirements.

        :rtype: dict
        \"\"\"
        dictionary = {}
        for cookie in iter(self):
            if (domain is None or cookie.domain == domain) and (
                path is None or cookie.path == path
            ):
                dictionary[cookie.name] = cookie.value
        return dictionary

    def __contains__(self, name):
        try:
            return super().__contains__(name)
        except CookieConflictError:
            return True

    def __getitem__(self, name):
        \"\"\"Dict-like __getitem__() for compatibility with client code. Throws
        exception if there are more than one cookie with name. In that case,
        use the more explicit get() method instead.

        .. warning:: operation is O(n), not O(1).
        \"\"\"
        return self._find_no_duplicates(name)

    def __setitem__(self, name, value):
        \"\"\"Dict-like __setitem__ for compatibility with client code. Throws
        exception if there is already a cookie of that name in the jar. In that
        case, use the more explicit set() method instead.
        \"\"\"
        self.set(name, value)

    def __delitem__(self, name):
        \"\"\"Deletes a cookie given a name. Wraps ``cookielib.CookieJar``'s
        ``remove_cookie_by_name()``.
        \"\"\"
        remove_cookie_by_name(self, name)

    def set_cookie(self, cookie, *args, **kwargs):
        if (
            hasattr(cookie.value, \"startswith\")
            and cookie.value.startswith('\"')
            and cookie.value.endswith('\"')
        ):
            cookie.value = cookie.value.replace('\\\\\"', \"\")
        return super().set_cookie(cookie, *args, **kwargs)

    def update(self, other):
        \"\"\"Updates this jar with cookies from another CookieJar or dict-like\"\"\"
        if isinstance(other, cookielib.CookieJar):
            for cookie in other:
                self.set_cookie(copy.copy(cookie))
        else:
            super().update(other)

    def _find(self, name, domain=None, path=None):
        \"\"\"Requests uses this method internally to get cookie values.

        If there are conflicting cookies, _find arbitrarily chooses one.
        See _find_no_duplicates if you want an exception thrown if there are
        conflicting cookies.

        :param name: a string containing name of cookie
        :param domain: (optional) string containing domain of cookie
        :param path: (optional) string containing path of cookie
        :return: cookie.value
        \"\"\"
        for cookie in iter(self):
            if cookie.name == name:
                if domain is None or cookie.domain == domain:
                    if path is None or cookie.path == path:
                        return cookie.value

        raise KeyError(f\"name={name!r}, domain={domain!r}, path={path!r}\")

    def _find_no_duplicates(self, name, domain=None, path=None):
        \"\"\"Both ``__get_item__`` and ``get`` call this function: it's never
        used elsewhere in Requests.

        :param name: a string containing name of cookie
        :param domain: (optional) string containing domain of cookie
        :param path: (optional) string containing path of cookie
        :raises KeyError: if cookie is not found
        :raises CookieConflictError: if there are multiple cookies
            that match name and optionally domain and path
        :return: cookie.value
        \"\"\"
        toReturn = None
        for cookie in iter(self):
            if cookie.name == name:
                if domain is None or cookie.domain == domain:
                    if path is None or cookie.path == path:
                        if toReturn is not None:
                            # if there are multiple cookies that meet passed in criteria
                            raise CookieConflictError(
                                f\"There are multiple cookies with name, {name!r}\"
                            )
                        # we will eventually return this as long as no cookie conflict
                        toReturn = cookie.value

        if toReturn:
            return toReturn
        raise KeyError(f\"name={name!r}, domain={domain!r}, path={path!r}\")

    def __getstate__(self):
        \"\"\"Unlike a normal CookieJar, this class is pickleable.\"\"\"
        state = self.__dict__.copy()
        # remove the unpickleable RLock object
        state.pop(\"_cookies_lock\")
        return state

    def __setstate__(self, state):
        \"\"\"Unlike a normal CookieJar, this class is pickleable.\"\"\"
        self.__dict__.update(state)
        if \"_cookies_lock\" not in self.__dict__:
            self._cookies_lock = threading.RLock()

    def copy(self):
        \"\"\"Return a copy of this RequestsCookieJar.\"\"\"
        new_cj = RequestsCookieJar()
        new_cj.set_policy(self.get_policy())
        new_cj.update(self)
        return new_cj

    def get_policy(self):
        \"\"\"Return the CookiePolicy instance used.\"\"\"
        return self._policy


def _copy_cookie_jar(jar):
    if jar is None:
        return None

    if hasattr(jar, \"copy\"):
        # We're dealing with an instance of RequestsCookieJar
        return jar.copy()
    # We're dealing with a generic CookieJar instance
    new_jar = copy.copy(jar)
    new_jar.clear()
    for cookie in jar:
        new_jar.set_cookie(copy.copy(cookie))
    return new_jar


def create_cookie(name, value, **kwargs):
    \"\"\"Make a cookie from underspecified parameters.

    By default, the pair of `name` and `value` will be set for the domain ''
    and sent on every request (this is sometimes called a \"supercookie\").
    \"\"\"
    result = {
        \"version\": 0,
        \"name\": name,
        \"value\": value,
        \"port\": None,
        \"domain\": \"\",
        \"path\": \"/\",
        \"secure\": False,
        \"expires\": None,
        \"discard\": True,
        \"comment\": None,
        \"comment_url\": None,
        \"rest\": {\"HttpOnly\": None},
        \"rfc2109\": False,
    }

    badargs = set(kwargs) - set(result)
    if badargs:
        raise TypeError(
            f\"create_cookie() got unexpected keyword arguments: {list(badargs)}\"
        )

    result.update(kwargs)
    result[\"port_specified\"] = bool(result[\"port\"])
    result[\"domain_specified\"] = bool(result[\"domain\"])
    result[\"domain_initial_dot\"] = result[\"domain\"].startswith(\".\")
    result[\"path_specified\"] = bool(result[\"path\"])

    return cookielib.Cookie(**result)


def morsel_to_cookie(morsel):
    \"\"\"Convert a Morsel object into a Cookie containing the one k/v pair.\"\"\"

    expires = None
    if morsel[\"max-age\"]:
        try:
            expires = int(time.time() + int(morsel[\"max-age\"]))
        except ValueError:
            raise TypeError(f\"max-age: {morsel['max-age']} must be integer\")
    elif morsel[\"expires\"]:
        time_template = \"%a, %d-%b-%Y %H:%M:%S GMT\"
        expires = calendar.timegm(time.strptime(morsel[\"expires\"], time_template))
    return create_cookie(
        comment=morsel[\"comment\"],
        comment_url=bool(morsel[\"comment\"]),
        discard=False,
        domain=morsel[\"domain\"],
        expires=expires,
        name=morsel.key,
        path=morsel[\"path\"],
        port=None,
        rest={\"HttpOnly\": morsel[\"httponly\"]},
        rfc2109=False,
        secure=bool(morsel[\"secure\"]),
        value=morsel.value,
        version=morsel[\"version\"] or 0,
    )


def cookiejar_from_dict(cookie_dict, cookiejar=None, overwrite=True):
    \"\"\"Returns a CookieJar from a key/value dictionary.

    :param cookie_dict: Dict of key/values to insert into CookieJar.
    :param cookiejar: (optional) A cookiejar to add the cookies to.
    :param overwrite: (optional) If False, will not replace cookies
        already in the jar with new ones.
    :rtype: CookieJar
    \"\"\"
    if cookiejar is None:
        cookiejar = RequestsCookieJar()

    if cookie_dict is not None:
        names_from_jar = [cookie.name for cookie in cookiejar]
        for name in cookie_dict:
            if overwrite or (name not in names_from_jar):
                cookiejar.set_cookie(create_cookie(name, cookie_dict[name]))

    return cookiejar


def merge_cookies(cookiejar, cookies):
    \"\"\"Add cookies to cookiejar and returns a merged CookieJar.

    :param cookiejar: CookieJar object to add the cookies to.
    :param cookies: Dictionary or CookieJar object to be added.
    :rtype: CookieJar
    \"\"\"
    if not isinstance(cookiejar, cookielib.CookieJar):
        raise ValueError(\"You can only merge into CookieJar\")

    if isinstance(cookies, dict):
        cookiejar = cookiejar_from_dict(cookies, cookiejar=cookiejar, overwrite=False)
    elif isinstance(cookies, cookielib.CookieJar):
        try:
            cookiejar.update(cookies)
        except AttributeError:
            for cookie_in_jar in cookies:
                cookiejar.set_cookie(cookie_in_jar)

    return cookiejar

"""
module_dict["requests"+os.sep+"help.py"]="""
# < include 'idna.py' >

# < include 'urllib3.py' >

# < include 'charset_normalizer.py' >

# < include 'chardet.py' >

# < include 'cryptography.py' >

# < include 'OpenSSL.py' >

\"\"\"Module containing bug report helper(s).\"\"\"

import json
import platform
import ssl
import sys

import idna
import urllib3

from . import __version__ as requests_version

try:
    import charset_normalizer
except ImportError:
    charset_normalizer = None

try:
    import chardet
except ImportError:
    chardet = None

try:
    from urllib3.contrib import pyopenssl
except ImportError:
    pyopenssl = None
    OpenSSL = None
    cryptography = None
else:
    import cryptography
    import OpenSSL


def _implementation():
    \"\"\"Return a dict with the Python implementation and version.

    Provide both the name and the version of the Python implementation
    currently running. For example, on CPython 3.10.3 it will return
    {'name': 'CPython', 'version': '3.10.3'}.

    This function works best on CPython and PyPy: in particular, it probably
    doesn't work for Jython or IronPython. Future investigation should be done
    to work out the correct shape of the code for those platforms.
    \"\"\"
    implementation = platform.python_implementation()

    if implementation == \"CPython\":
        implementation_version = platform.python_version()
    elif implementation == \"PyPy\":
        implementation_version = \"{}.{}.{}\".format(
            sys.pypy_version_info.major,
            sys.pypy_version_info.minor,
            sys.pypy_version_info.micro,
        )
        if sys.pypy_version_info.releaselevel != \"final\":
            implementation_version = \"\".join(
                [implementation_version, sys.pypy_version_info.releaselevel]
            )
    elif implementation == \"Jython\":
        implementation_version = platform.python_version()  # Complete Guess
    elif implementation == \"IronPython\":
        implementation_version = platform.python_version()  # Complete Guess
    else:
        implementation_version = \"Unknown\"

    return {\"name\": implementation, \"version\": implementation_version}


def info():
    \"\"\"Generate information for a bug report.\"\"\"
    try:
        platform_info = {
            \"system\": platform.system(),
            \"release\": platform.release(),
        }
    except OSError:
        platform_info = {
            \"system\": \"Unknown\",
            \"release\": \"Unknown\",
        }

    implementation_info = _implementation()
    urllib3_info = {\"version\": urllib3.__version__}
    charset_normalizer_info = {\"version\": None}
    chardet_info = {\"version\": None}
    if charset_normalizer:
        charset_normalizer_info = {\"version\": charset_normalizer.__version__}
    if chardet:
        chardet_info = {\"version\": chardet.__version__}

    pyopenssl_info = {
        \"version\": None,
        \"openssl_version\": \"\",
    }
    if OpenSSL:
        pyopenssl_info = {
            \"version\": OpenSSL.__version__,
            \"openssl_version\": f\"{OpenSSL.SSL.OPENSSL_VERSION_NUMBER:x}\",
        }
    cryptography_info = {
        \"version\": getattr(cryptography, \"__version__\", \"\"),
    }
    idna_info = {
        \"version\": getattr(idna, \"__version__\", \"\"),
    }

    system_ssl = ssl.OPENSSL_VERSION_NUMBER
    system_ssl_info = {\"version\": f\"{system_ssl:x}\" if system_ssl is not None else \"\"}

    return {
        \"platform\": platform_info,
        \"implementation\": implementation_info,
        \"system_ssl\": system_ssl_info,
        \"using_pyopenssl\": pyopenssl is not None,
        \"using_charset_normalizer\": chardet is None,
        \"pyOpenSSL\": pyopenssl_info,
        \"urllib3\": urllib3_info,
        \"chardet\": chardet_info,
        \"charset_normalizer\": charset_normalizer_info,
        \"cryptography\": cryptography_info,
        \"idna\": idna_info,
        \"requests\": {
            \"version\": requests_version,
        },
    }


def main():
    \"\"\"Pretty-print the bug information as JSON.\"\"\"
    print(json.dumps(info(), sort_keys=True, indent=2))


if __name__ == \"__main__\":
    main()

"""
module_dict["requests"+os.sep+"compat.py"]="""
# < include 'chardet.py' >

# < include 'simplejson.py' >

# < include 'charset_normalizer.py' >

\"\"\"
requests.compat
~~~~~~~~~~~~~~~

This module previously handled import compatibility issues
between Python 2 and Python 3. It remains for backwards
compatibility until the next major version.
\"\"\"

try:
    import chardet
except ImportError:
    import charset_normalizer as chardet

import sys

# -------
# Pythons
# -------

# Syntax sugar.
_ver = sys.version_info

#: Python 2.x?
is_py2 = _ver[0] == 2

#: Python 3.x?
is_py3 = _ver[0] == 3

# json/simplejson module import resolution
has_simplejson = False
try:
    import simplejson as json

    has_simplejson = True
except ImportError:
    import json

if has_simplejson:
    from simplejson import JSONDecodeError
else:
    from json import JSONDecodeError

# Keep OrderedDict for backwards compatibility.
from collections import OrderedDict
from collections.abc import Callable, Mapping, MutableMapping
from http import cookiejar as cookielib
from http.cookies import Morsel
from io import StringIO

# --------------
# Legacy Imports
# --------------
from urllib.parse import (
    quote,
    quote_plus,
    unquote,
    unquote_plus,
    urldefrag,
    urlencode,
    urljoin,
    urlparse,
    urlsplit,
    urlunparse,
)
from urllib.request import (
    getproxies,
    getproxies_environment,
    parse_http_list,
    proxy_bypass,
    proxy_bypass_environment,
)

builtin_str = str
str = str
bytes = bytes
basestring = (str, bytes)
numeric_types = (int, float)
integer_types = (int,)

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

#from requests import *
import requests
globals().update(requests.__dict__)
    
if module_importer in sys.meta_path:
    sys.meta_path.remove(module_importer)

#for key in sys.modules.copy():
#    if key=="requests" or key.startswith("requests."):
#        del sys.modules[key]
