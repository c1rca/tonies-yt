// Send the browser to the sign-in page when the server says the session has ended
// (restart, "Lock" on another device, or a password change elsewhere).
(() => {
  const nativeFetch = window.fetch.bind(window);
  let redirecting = false;
  window.fetch = async (...args) => {
    const response = await nativeFetch(...args);
    if (response.status === 401 && !redirecting) {
      redirecting = true;
      window.location.href = '/login?locked=1';
    }
    return response;
  };
})();
