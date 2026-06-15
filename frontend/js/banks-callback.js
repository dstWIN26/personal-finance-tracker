/* Bank consent callback: read code+state from the URL and complete the link
   via a same-origin POST (which carries the SameSite=Strict session cookie). */
(function () {
    const params = new URLSearchParams(location.search);
    const code = params.get('code');
    const state = params.get('state');
    const bankError = params.get('error');
    const msg = document.getElementById('msg');
    const back = document.getElementById('back');

    function showBack() { if (back) back.style.display = ''; }

    if (bankError || !code || !state) {
        msg.textContent = bankError
            ? 'The bank reported: ' + bankError
            : 'Missing authorisation details — please start linking again.';
        showBack();
        return;
    }

    fetch('/settings/banks/complete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code: code, state: state }),
    })
        .then(async (r) => {
            if (r.ok) {
                msg.textContent = 'Linked! Returning to settings…';
                setTimeout(() => { location.href = '/?tab=settings'; }, 900);
            } else {
                const d = await r.json().catch(() => ({}));
                msg.textContent = d.detail || 'Could not finish linking the bank.';
                showBack();
            }
        })
        .catch(() => {
            msg.textContent = 'Network error while finishing the link.';
            showBack();
        });
})();
