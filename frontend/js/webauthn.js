/* Shared WebAuthn helpers (used by login.js and the dashboard "add passkey").
   Manual base64url <-> ArrayBuffer conversion so it works on every browser
   regardless of PublicKeyCredential.toJSON() support. */

function b64urlToBuf(s) {
    const pad = '='.repeat((4 - (s.length % 4)) % 4);
    const b64 = (s + pad).replace(/-/g, '+').replace(/_/g, '/');
    const bin = atob(b64);
    const buf = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) buf[i] = bin.charCodeAt(i);
    return buf.buffer;
}

function bufToB64url(buf) {
    const bytes = new Uint8Array(buf);
    let bin = '';
    for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
    return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

async function postJSON(url, body) {
    const r = await fetch(url, {
        method: 'POST',
        headers: body ? { 'Content-Type': 'application/json' } : {},
        body: body ? JSON.stringify(body) : undefined,
    });
    if (!r.ok) {
        let msg = `Request failed (${r.status})`;
        try { msg = (await r.json()).detail || msg; } catch (e) { /* ignore */ }
        throw new Error(msg);
    }
    return r.json();
}

/* Passkey login → resolves on success, throws with a readable message. */
async function webauthnLogin() {
    const opts = await postJSON('/auth/passkey/login/begin');
    opts.challenge = b64urlToBuf(opts.challenge);
    if (opts.allowCredentials) {
        opts.allowCredentials = opts.allowCredentials.map(c => ({ ...c, id: b64urlToBuf(c.id) }));
    }
    const cred = await navigator.credentials.get({ publicKey: opts });
    const payload = {
        id: cred.id,
        rawId: bufToB64url(cred.rawId),
        type: cred.type,
        authenticatorAttachment: cred.authenticatorAttachment,
        clientExtensionResults: cred.getClientExtensionResults(),
        response: {
            clientDataJSON: bufToB64url(cred.response.clientDataJSON),
            authenticatorData: bufToB64url(cred.response.authenticatorData),
            signature: bufToB64url(cred.response.signature),
            userHandle: cred.response.userHandle ? bufToB64url(cred.response.userHandle) : null,
        },
    };
    return postJSON('/auth/passkey/login/complete', payload);
}

/* Enrol a new passkey (must already be authenticated). */
async function webauthnRegister(label) {
    const opts = await postJSON('/auth/passkey/register/begin');
    opts.challenge = b64urlToBuf(opts.challenge);
    opts.user.id = b64urlToBuf(opts.user.id);
    if (opts.excludeCredentials) {
        opts.excludeCredentials = opts.excludeCredentials.map(c => ({ ...c, id: b64urlToBuf(c.id) }));
    }
    const cred = await navigator.credentials.create({ publicKey: opts });
    const credential = {
        id: cred.id,
        rawId: bufToB64url(cred.rawId),
        type: cred.type,
        authenticatorAttachment: cred.authenticatorAttachment,
        clientExtensionResults: cred.getClientExtensionResults(),
        response: {
            clientDataJSON: bufToB64url(cred.response.clientDataJSON),
            attestationObject: bufToB64url(cred.response.attestationObject),
            transports: cred.response.getTransports ? cred.response.getTransports() : [],
        },
    };
    return postJSON('/auth/passkey/register/complete', { credential, label: label || 'Passkey' });
}
