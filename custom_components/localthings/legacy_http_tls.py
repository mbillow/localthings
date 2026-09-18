"""TLS for the 8888 bridge (issue #168).

This firmware speaks TLS 1.0 and nothing newer, and a modern OpenSSL will
not negotiate with it at all without `DEFAULT@SECLEVEL=0`. Both contexts
are built here so that constraint lives in one place: the client context
the transport uses, and the server context the token bootstrap's listener
needs, since the appliance calls back over HTTPS.

`ssl` can only load a certificate chain from the filesystem, and both the
leaf and its key are held on the config entry as PEM text. They are written
to a private temporary file, loaded, and unlinked before this returns, so
the key is on disk for the duration of one call and is never left behind.
"""

from __future__ import annotations

import contextlib
import os
import ssl
import tempfile
from collections.abc import Iterator


@contextlib.contextmanager
def _pem_files(cert_pem: str, key_pem: str) -> Iterator[tuple[str, str]]:
    paths = []
    try:
        for content in (cert_pem, key_pem):
            handle, path = tempfile.mkstemp(suffix=".pem")
            paths.append(path)
            with os.fdopen(handle, "w") as file:
                file.write(content)
            os.chmod(path, 0o600)
        yield paths[0], paths[1]
    finally:
        for path in paths:
            with contextlib.suppress(OSError):
                os.unlink(path)


def _relaxed(context: ssl.SSLContext, cert_pem: str, key_pem: str) -> ssl.SSLContext:
    context.minimum_version = ssl.TLSVersion.TLSv1
    context.set_ciphers("DEFAULT@SECLEVEL=0")
    with _pem_files(cert_pem, key_pem) as (cert_path, key_path):
        context.load_cert_chain(cert_path, key_path)
    return context


def client_context(cert_pem: str, key_pem: str) -> ssl.SSLContext:
    """For talking to the appliance.

    The appliance's own certificate is neither verified nor verifiable --
    it is issued for a name it does not answer to, by a chain nothing here
    trusts. The authentication that matters on this link runs the other
    way: the appliance checks the client certificate, and refuses the
    request outright without one (`400 No required SSL certificate was
    sent`, from nginx, before the application ever sees it).
    """
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return _relaxed(context, cert_pem, key_pem)


def server_context(cert_pem: str, key_pem: str) -> ssl.SSLContext:
    """For the token callback listener.

    The appliance connects back over HTTPS, so the listener needs a server
    certificate; the same leaf works, since the appliance does not verify
    it either.
    """
    return _relaxed(ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER), cert_pem, key_pem)
