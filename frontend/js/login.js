/* Login page logic: passkey-first, password as one-time bootstrap, then enrol. */
(function () {
    const $ = id => document.getElementById(id);
    const msg = (text, kind = 'err') => {
        const el = $('message');
        el.textContent = text;
        el.className = 'login-msg ' + kind;
        el.hidden = false;
    };

    async function refresh() {
        let s;
        try {
            s = await fetch('/auth/status').then(r => r.json());
        } catch (e) { msg('Cannot reach server.'); return; }

        if (s.authenticated && s.has_passkey) { location.href = '/'; return; }

        // Authenticated via bootstrap but no passkey yet → enrol.
        if (s.authenticated && !s.has_passkey) { $('enroll').hidden = false; return; }

        if (s.has_passkey) $('passkeyBtn').hidden = false;
        if (s.password_enabled) $('pwForm').hidden = false;
        if (!s.has_passkey && !s.password_enabled) $('notSetup').hidden = false;
    }

    $('passkeyBtn').addEventListener('click', async () => {
        msg('Waiting for passkey…', 'info');
        try { await webauthnLogin(); location.href = '/'; }
        catch (e) { msg(e.message || 'Passkey sign-in failed.'); }
    });

    $('pwForm').addEventListener('submit', async (e) => {
        e.preventDefault();
        msg('Signing in…', 'info');
        try {
            await postJSON('/auth/password', { password: $('pw').value });
            $('pwForm').hidden = true;
            $('enroll').hidden = false;
            msg('Signed in. Now enrol your passkey.', 'info');
        } catch (err) { msg(err.message || 'Sign-in failed.'); }
    });

    $('enrollBtn').addEventListener('click', async () => {
        msg('Follow your device prompt…', 'info');
        try {
            await webauthnRegister(navigator.platform || 'This device');
            location.href = '/';
        } catch (e) { msg(e.message || 'Enrolment failed.'); }
    });

    refresh();
})();
